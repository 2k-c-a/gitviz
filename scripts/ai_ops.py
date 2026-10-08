"""AI 기능 운영 도구: 설정 점검·상태 확인·작업 취소·복구·기한 삭제. 사용법과 절차는 docs/AI_OPERATIONS.md.

    python scripts/ai_ops.py check                     설정·DB(migration)·Redis 점검(문제가 있으면 종료 코드 1)
    python scripts/ai_ops.py status                    작업·대기열·예산·보관 현황
    python scripts/ai_ops.py uncertain                 결과를 알 수 없는 호출(예산을 점유 중)
    python scripts/ai_ops.py resolve <예약id> [--settle 금액]   불명 호출 정리: 금액을 주면 정산, 없으면 해제
    python scripts/ai_ops.py cancel <작업id>           작업 취소(대기 중이면 즉시, 실행 중이면 취소 접수)
    python scripts/ai_ops.py maintenance               복구·기한 삭제를 지금 한 번 실행(dispatcher가 주기적으로 같은 일을 한다)

DATABASE_URL 등 운영 환경변수가 있는 셸에서 실행한다. 사용자 API key·원문 코드는 출력하지 않는다.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402


def check(*, redis_url: str | None = None, env=None) -> list[tuple[str, bool, str]]:
    """(항목, 통과, 설명) 목록. 하나라도 False면 운영을 시작하면 안 된다."""
    from app.core.ai_settings import get_ai_settings, validate_ai_settings
    env = os.environ if env is None else env
    out = []
    settings = get_ai_settings()
    problems = validate_ai_settings(settings)
    out.append(('AI 설정', not problems and settings.enabled, '; '.join(problems) if problems else ('켜짐' if settings.enabled else 'AI_ENABLED=1이 아닙니다')))
    out.append(('외부 전송 허용', True, '허용됨(사용자가 동의해야 전송)' if settings.allow_external_transfer else '꺼짐(입력 준비·조회만 가능)'))
    out.append(('제공자 주소 우회 없음', not settings.test_provider_base_url, '운영에서는 AI_TEST_PROVIDER_BASE_URL을 비워야 합니다' if settings.test_provider_base_url else '공식 주소만 사용'))
    stray = [k for k in ('ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY') if env.get(k)]
    out.append(('남은 제공자 환경변수', True, f'{", ".join(stray)}가 설정돼 있지만 AI 기능은 무시합니다(사용자 key만 사용). 정리를 권장합니다' if stray else '없음'))
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        from app.core.database import SessionLocal
        head = ScriptDirectory.from_config(Config(str(ROOT / 'alembic.ini'))).get_current_head()
        with SessionLocal() as db:
            current = db.execute(text('SELECT version_num FROM alembic_version')).scalar()
        out.append(('DB migration', current == head, f'현재 {current} / 최신 {head}'))
    except Exception as exc:
        out.append(('DB migration', False, f'확인하지 못했습니다({type(exc).__name__})'))
    url = redis_url or env.get('REDIS_URL')
    if url:
        try:
            import redis
            redis.Redis.from_url(url, socket_connect_timeout=3).ping()
            out.append(('Redis', True, '연결됨'))
        except Exception as exc:
            out.append(('Redis', False, f'연결하지 못했습니다({type(exc).__name__})'))
    else:
        out.append(('Redis', False, 'REDIS_URL이 없습니다'))
    return out


def status(db, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    one = lambda sql, **p: db.execute(text(sql), p).scalar()  # noqa: E731
    jobs = {f'{r.kind}/{r.status}': r.n for r in db.execute(text(
        "SELECT kind, status, count(*) AS n FROM ai_jobs WHERE deleted_at IS NULL GROUP BY kind, status ORDER BY kind, status"))}
    oldest = one("SELECT min(created_at) FROM ai_jobs WHERE status IN ('queued','retry_wait') AND deleted_at IS NULL")
    return {
        'jobs': jobs,
        'oldest_waiting_seconds': int((now - oldest).total_seconds()) if oldest else None,
        'running_with_expired_lease': one("SELECT count(*) FROM ai_jobs WHERE status IN ('running','cancel_requested') AND lease_until < :n", n=now),
        'outbox_unpublished': one('SELECT count(*) FROM task_outbox WHERE published_at IS NULL'),
        'outbox_failing': one('SELECT count(*) FROM task_outbox WHERE published_at IS NULL AND retry_count > 0'),
        'uncertain_reservations': one("SELECT count(DISTINCT (job_id, attempt_no)) FROM usage_reservations WHERE status = 'uncertain'"),
        'reserved_amount': str(one("SELECT coalesce(sum(reserved_amount), 0) FROM usage_budgets WHERE period_start = date_trunc('month', :n)::date", n=now)),
        'spent_amount': str(one("SELECT coalesce(sum(spent_amount), 0) FROM usage_budgets WHERE period_start = date_trunc('month', :n)::date AND scope = 'user'", n=now)),
        'inputs_expired_with_payload': one("""SELECT count(DISTINCT i.id) FROM ai_inputs i JOIN ai_input_files f ON f.input_id = i.id
                                              WHERE f.encrypted_payload IS NOT NULL AND i.expires_at <= :n""", n=now),
        'results_expired': one('SELECT count(*) FROM ai_results WHERE expires_at <= :n', n=now),
        'credentials_active': one("SELECT count(*) FROM ai_credential_versions WHERE status = 'active'"),
        'retired_credentials_with_secret': one("SELECT count(*) FROM ai_credential_versions WHERE status = 'retired' AND encrypted_key IS NOT NULL"),
    }


def list_uncertain(db) -> list[dict]:
    rows = db.execute(text("""
        SELECT u.id, u.job_id, u.attempt_no, u.reserved_amount, b.scope, u.created_at, j.kind, j.status AS job_status, j.error_code
        FROM usage_reservations u JOIN usage_budgets b ON b.id = u.budget_id LEFT JOIN ai_jobs j ON j.id = u.job_id
        WHERE u.status = 'uncertain' ORDER BY u.created_at"""))
    return [{'reservation_id': str(r.id), 'job_id': str(r.job_id) if r.job_id else None, 'attempt': r.attempt_no, 'scope': r.scope,
             'amount': str(r.reserved_amount), 'job': f'{r.kind}/{r.job_status}/{r.error_code}', 'since': r.created_at.isoformat()} for r in rows]


def resolve_uncertain(db, reservation_id: UUID, settle: Decimal | None = None) -> bool:
    from app.services.ai import usage_service
    done = usage_service.resolve_uncertain(db, reservation_id, settle)
    if not done:
        db.rollback()
        return False
    db.execute(text("""INSERT INTO audit_events (action, object_type, object_id, request_id, details)
                       VALUES ('usage.uncertain_resolved', 'reservation', :r, 'ops', CAST(:d AS jsonb))"""),
               {'r': str(reservation_id), 'd': json.dumps({'settled': str(settle) if settle is not None else None})})
    db.commit()
    return done


def cancel_job(db, job_id: UUID) -> str:
    from app.services.ai import job_service
    owner = db.execute(text('SELECT owner_user_id FROM ai_jobs WHERE id = :j AND deleted_at IS NULL'), {'j': job_id}).scalar()
    if owner is None:
        raise LookupError('작업을 찾을 수 없습니다.')
    status_after = job_service.request_cancel(db, job_id, owner)
    db.execute(text("""INSERT INTO audit_events (action, object_type, object_id, request_id, details)
                       VALUES ('job.cancelled_by_operator', 'job', :j, 'ops', '{}'::jsonb)"""), {'j': str(job_id)})
    db.commit()
    return status_after


def run_maintenance() -> dict:
    from app.tasks import ai_dispatch
    return ai_dispatch.maintenance_once()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('check')
    sub.add_parser('status')
    sub.add_parser('uncertain')
    r = sub.add_parser('resolve')
    r.add_argument('reservation_id')
    r.add_argument('--settle')
    c = sub.add_parser('cancel')
    c.add_argument('job_id')
    sub.add_parser('maintenance')
    args = ap.parse_args(argv)
    # AI 설정 검사보다 먼저 프로젝트 .env를 읽는다. 셸에 지정한 값은 유지한다.
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    if args.cmd == 'check':
        results = check()
        for name, ok, detail in results:
            print(f'[{"OK" if ok else "문제"}] {name}: {detail}')
        return 0 if all(ok for _, ok, _ in results) else 1
    if args.cmd == 'maintenance':
        print(json.dumps(run_maintenance(), ensure_ascii=False, indent=2))
        return 0
    from app.core.database import SessionLocal
    with SessionLocal() as db:
        if args.cmd == 'status':
            print(json.dumps(status(db), ensure_ascii=False, indent=2))
        elif args.cmd == 'uncertain':
            print(json.dumps(list_uncertain(db), ensure_ascii=False, indent=2))
        elif args.cmd == 'resolve':
            try:
                settle = Decimal(args.settle) if args.settle is not None else None
                rid = UUID(args.reservation_id)
            except (InvalidOperation, ValueError):
                print('예약 id 또는 금액이 올바르지 않습니다.')
                return 2
            print('정리했습니다.' if resolve_uncertain(db, rid, settle) else '불명 상태의 예약이 아닙니다.')
        elif args.cmd == 'cancel':
            try:
                print(cancel_job(db, UUID(args.job_id)))
            except (LookupError, ValueError) as exc:
                print(exc)
                return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
