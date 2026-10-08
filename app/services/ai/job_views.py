"""작업·결과 조회와 삭제(읽기 쪽). 새 실행 로직은 없다: 상태 전이는 job_service, 생성 실행은 generation_service가 한다.

모든 조회는 작성자 본인의 자료만 돌려주고(남의 자료는 존재를 숨긴다) 삭제된 작업은 보이지 않는다. 저장소 접근 권한은
API 계층이 현재 권한을 다시 확인한다. GET은 작업을 만들거나 코드를 가져오지 않는다.
"""
import base64
import binascii
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.ai_settings import get_ai_settings
from app.schemas.ai import (Coverage, ErrorCode, Finding, FindingView, FeedbackView, JobKind, JobStage, JobStatus,
                            JobView, ResultView, UsageView)
from app.services.ai.access import AIError, ensure_owner
from app.services.ai.usage_service import period_start

TERMINAL = ('succeeded', 'failed', 'cancelled')
MAX_PAGE = 50
DEFAULT_PAGE = 20

_JOB_SQL = """
SELECT j.id, j.owner_user_id, j.repository_id, j.input_id, j.kind, j.status, j.generation, j.provider, j.model,
       j.error_code, j.cancel_requested_at, j.next_attempt_at, j.created_at, j.finished_at, j.attempt_count,
       i.coverage AS input_coverage, r.id AS result_id, r.coverage AS result_coverage,
       coalesce((SELECT array_agg(DISTINCT u.status) FROM usage_reservations u
                 WHERE u.job_id = j.id AND u.attempt_no = j.attempt_count), '{}') AS attempt_reservations,
       EXISTS (SELECT 1 FROM usage_reservations u WHERE u.job_id = j.id AND u.status = 'uncertain') AS uncertain
FROM ai_jobs j
JOIN ai_inputs i ON i.id = j.input_id
LEFT JOIN ai_results r ON r.job_id = j.id
WHERE j.deleted_at IS NULL"""


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _stage(row) -> JobStage | None:
    if row.status in TERMINAL:
        return None
    if row.status in ('queued', 'retry_wait'):
        return JobStage.queued
    if row.kind == 'prepare':
        return JobStage.input_preparation
    reservations = set(row.attempt_reservations or ())
    if 'reserved' in reservations:
        return JobStage.provider_call
    if 'settled' in reservations:
        return JobStage.output_validation      # 호출은 끝났고 검증·저장 중
    return JobStage.input_preparation          # 호출 전: 입력 확인·준비


def _view(row, now: datetime) -> JobView:
    retry_after = None
    if row.status == 'retry_wait' and row.next_attempt_at is not None:
        retry_after = max(0, int((row.next_attempt_at - now).total_seconds()))
    coverage = row.result_coverage if row.result_coverage is not None else row.input_coverage
    return JobView(
        job_id=row.id, kind=JobKind(row.kind), status=JobStatus(row.status), stage=_stage(row),
        coverage=Coverage(**coverage), result_id=row.result_id, retry_after_seconds=retry_after,
        cancel_requested=row.cancel_requested_at is not None and row.status not in TERMINAL,
        error_code=row.error_code, repository_id=row.repository_id, input_id=row.input_id,
        generation=row.generation, provider=row.provider, model=row.model, outcome_uncertain=bool(row.uncertain),
        created_at=row.created_at, finished_at=row.finished_at)


def load_job_row(db: Session, job_id: UUID, owner_id: UUID):
    row = db.execute(text(_JOB_SQL + " AND j.id = :j"), {'j': job_id}).one_or_none()
    if row is None:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '작업을 찾을 수 없습니다.')
    ensure_owner(row.owner_user_id, owner_id)
    return row


def get_job(db: Session, job_id: UUID, owner_id: UUID, *, now: datetime | None = None) -> JobView:
    row = load_job_row(db, job_id, owner_id)
    return _view(row, _now(now))


def encode_cursor(*parts) -> str:
    return base64.urlsafe_b64encode(json.dumps(parts, separators=(',', ':')).encode()).decode().rstrip('=')


def decode_cursor(cursor: str, arity: int) -> list:
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
    except (ValueError, binascii.Error):
        data = None
    if not isinstance(data, list) or len(data) != arity:
        raise AIError(ErrorCode.INVALID_SELECTION, 'cursor가 올바르지 않습니다.')
    return data


def list_jobs(db: Session, owner_id: UUID, repository_id: int, *, kind: str | None = None, status: str | None = None,
              input_id: UUID | None = None, limit: int = DEFAULT_PAGE, cursor: str | None = None,
              now: datetime | None = None) -> tuple[list[JobView], str | None]:
    """최신순(created_at, id) keyset 목록. 한 저장소의 본인 작업만. 같은 시각이어도 id로 순서가 고정돼 중복·누락이 없다."""
    now = _now(now)
    limit = max(1, min(limit, MAX_PAGE))
    sql = _JOB_SQL + " AND j.owner_user_id = :o AND j.repository_id = :r"
    params: dict = {'o': owner_id, 'r': repository_id, 'n': limit + 1}
    if kind is not None:
        sql += " AND j.kind = :k"
        params['k'] = kind
    if status is not None:
        sql += " AND j.status = :s"
        params['s'] = status
    if input_id is not None:
        sql += " AND j.input_id = :i"
        params['i'] = input_id
    if cursor is not None:
        stamp, last_id = decode_cursor(cursor, 2)
        try:
            params['t'], params['c'] = datetime.fromisoformat(stamp), UUID(last_id)
        except (TypeError, ValueError):
            raise AIError(ErrorCode.INVALID_SELECTION, 'cursor가 올바르지 않습니다.') from None
        sql += " AND (j.created_at, j.id) < (:t, :c)"
    rows = db.execute(text(sql + " ORDER BY j.created_at DESC, j.id DESC LIMIT :n"), params).all()
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(rows[-1].created_at.isoformat(), str(rows[-1].id)) if more else None
    return [_view(r, now) for r in rows], next_cursor


def job_deleted_error() -> AIError:
    return AIError(ErrorCode.RESOURCE_NOT_FOUND, '이 요청으로 만들어진 작업은 삭제됐습니다. 새 Idempotency-Key로 다시 요청해 주세요.',
                   details={'reason': 'JOB_DELETED'})


def existing_for_key(db: Session, owner_id: UUID, route: str, key: str, body_hash: str, now: datetime) -> UUID | None:
    """같은 Idempotency-Key의 재전송이면 처음 만든 작업 id. 다른 요청이면 IDEMPOTENCY_CONFLICT."""
    row = db.execute(text("""SELECT request_hash, resource_id FROM request_idempotency
                             WHERE owner_user_id = :o AND route = :r AND key = :k AND expires_at > :now"""),
                     {'o': owner_id, 'r': route, 'k': key, 'now': now}).one_or_none()
    if row is None:
        return None
    if row.request_hash != body_hash:
        raise AIError(ErrorCode.IDEMPOTENCY_CONFLICT, '같은 Idempotency-Key로 다른 요청을 보낼 수 없습니다.')
    if row.resource_id is None:
        return None
    alive = db.execute(text("SELECT deleted_at IS NULL FROM ai_jobs WHERE id = :j AND owner_user_id = :o"),
                       {'j': row.resource_id, 'o': owner_id}).scalar()
    if not alive:
        raise job_deleted_error()                  # 삭제된 작업에 묶인 key: 같은 요청으로 새 작업(재과금)을 만들지 않는다
    return row.resource_id


def delete_job(db: Session, job_id: UUID, owner_id: UUID, *, request_id: str = '-', now: datetime | None = None) -> None:
    """끝난 작업과 그 결과·지적·피드백을 삭제한다. 진행 중이면 먼저 취소해야 한다. 비용 기록(예약)은 남는다."""
    now = _now(now)
    row = db.execute(text("SELECT owner_user_id, status, deleted_at, repository_id, kind FROM ai_jobs WHERE id = :j FOR UPDATE"),
                     {'j': job_id}).one_or_none()
    if row is None or row.deleted_at is not None:
        db.rollback()
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '작업을 찾을 수 없습니다.')
    try:
        ensure_owner(row.owner_user_id, owner_id)
    except AIError:
        db.rollback()
        raise
    if row.status not in TERMINAL:
        db.rollback()
        raise AIError(ErrorCode.INVALID_STATE, '진행 중인 작업은 취소한 뒤 삭제할 수 있습니다.')
    db.execute(text("DELETE FROM ai_results WHERE job_id = :j"), {'j': job_id})      # 지적·피드백은 cascade
    db.execute(text("UPDATE ai_jobs SET deleted_at = :now, updated_at = :now WHERE id = :j"), {'now': now, 'j': job_id})
    db.execute(text("""INSERT INTO audit_events (actor_user_id, repository_id, action, object_type, object_id, request_id, details)
                       VALUES (:a, :r, 'job.deleted', 'job', :j, :q, CAST(:d AS jsonb))"""),
               {'a': owner_id, 'r': row.repository_id, 'j': str(job_id), 'q': request_id,
                'd': json.dumps({'kind': row.kind})})
    db.commit()


# ── 결과·지적 ───────────────────────────────────────────────────────────

def load_result_row(db: Session, result_id: UUID, owner_id: UUID, now: datetime):
    row = db.execute(text("""
        SELECT r.*, j.kind, j.input_id, j.provider, j.model,
               (SELECT count(*) FROM ai_findings f WHERE f.result_id = r.id) AS finding_count
        FROM ai_results r JOIN ai_jobs j ON j.id = r.job_id WHERE r.id = :r AND j.deleted_at IS NULL"""),
                     {'r': result_id}).one_or_none()
    if row is None:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '결과를 찾을 수 없습니다.')
    ensure_owner(row.owner_user_id, owner_id)
    if row.expires_at <= now:
        raise AIError(ErrorCode.RESULT_EXPIRED, '결과의 보관 기간이 지났습니다.')
    return row


def get_result(db: Session, result_id: UUID, owner_id: UUID, *, now: datetime | None = None) -> ResultView:
    now = _now(now)
    row = load_result_row(db, result_id, owner_id, now)
    return ResultView(
        result_id=row.id, job_id=row.job_id, kind=row.kind, repository_id=row.repository_id, input_id=row.input_id,
        payload=row.payload, payload_hash=row.payload_hash.strip(), coverage=Coverage(**row.coverage),
        structure_validation=row.structure_validation, human_validation=row.human_validation, provider=row.provider,
        model=row.model, finding_count=row.finding_count, created_at=row.created_at, expires_at=row.expires_at)


def _finding_view(row) -> FindingView:
    detail = dict(row.detail)
    quote = detail.pop('quote', None)
    for extra in ('file_id', 'model_limitations'):
        detail.pop(extra, None)
    feedback = None
    if row.fb_version is not None:
        feedback = FeedbackView(verdict=row.fb_verdict, note=row.fb_note, version=row.fb_version,
                                updated_at=row.fb_updated_at)
        detail['human_validation'] = row.fb_verdict          # 원본 결과는 그대로, 내 검토 상태만 덧입힌다
    return FindingView(finding_id=row.id, result_id=row.result_id, source_file_id=row.source_file_id,
                       finding=Finding(**detail), quote=quote, feedback=feedback)


def list_findings(db: Session, result_id: UUID, owner_id: UUID, *, limit: int = DEFAULT_PAGE, cursor: str | None = None,
                  now: datetime | None = None) -> tuple[list[FindingView], str | None]:
    """ordinal 순 keyset 목록. 결과 작성자의 피드백만 붙는다."""
    now = _now(now)
    load_result_row(db, result_id, owner_id, now)
    limit = max(1, min(limit, MAX_PAGE))
    after = 0
    if cursor is not None:
        (after,) = decode_cursor(cursor, 1)
        if not isinstance(after, int) or isinstance(after, bool) or after < 0:
            raise AIError(ErrorCode.INVALID_SELECTION, 'cursor가 올바르지 않습니다.')
    rows = db.execute(text("""
        SELECT f.id, f.result_id, f.ordinal, f.detail, f.source_file_id, b.verdict AS fb_verdict, b.note AS fb_note,
               b.version AS fb_version, b.updated_at AS fb_updated_at
        FROM ai_findings f LEFT JOIN finding_feedback b ON b.finding_id = f.id AND b.author_user_id = :o
        WHERE f.result_id = :r AND f.ordinal > :a ORDER BY f.ordinal LIMIT :n"""),
                      {'o': owner_id, 'r': result_id, 'a': after, 'n': limit + 1}).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return [_finding_view(r) for r in rows], (encode_cursor(rows[-1].ordinal) if more else None)


# ── 사용량 ──────────────────────────────────────────────────────────────

def usage_summary(db: Session, owner_id: UUID, *, now: datetime | None = None) -> UsageView:
    now = _now(now)
    settings = get_ai_settings()
    period: date = period_start(now)
    budget = db.execute(text("""SELECT amount_limit, reserved_amount, spent_amount FROM usage_budgets
                                WHERE scope = 'user' AND owner_user_id = :o AND period_start = :p AND currency = :c"""),
                        {'o': owner_id, 'p': period, 'c': settings.budget_currency}).one_or_none()
    limit = Decimal(budget.amount_limit) if budget else settings.user_monthly_budget
    reserved = Decimal(budget.reserved_amount) if budget else Decimal(0)
    spent = Decimal(budget.spent_amount) if budget else Decimal(0)
    uncertain = db.execute(text("""SELECT count(*) FROM usage_reservations u JOIN usage_budgets b ON b.id = u.budget_id
                                   WHERE u.status = 'uncertain' AND b.scope = 'user' AND b.owner_user_id = :o"""),
                           {'o': owner_id}).scalar()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    used = db.execute(text("""SELECT count(*) FROM ai_jobs WHERE owner_user_id = :o AND kind <> 'prepare'
                              AND created_at >= :d"""), {'o': owner_id, 'd': day_start}).scalar()
    fmt = lambda value: format(value, 'f')
    return UsageView(period_start=period, currency=settings.budget_currency, user_limit=fmt(limit),
                     user_spent=fmt(spent), user_reserved=fmt(reserved),
                     user_remaining=fmt(max(limit - reserved - spent, Decimal(0))),
                     server_budget_enabled=settings.server_monthly_budget > 0, uncertain_reservations=uncertain,
                     daily_jobs_used=used, daily_job_limit=settings.daily_job_limit)
