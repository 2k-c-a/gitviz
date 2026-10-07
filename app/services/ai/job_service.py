"""AI 작업의 생성·처리권·상태 전이. 모델을 호출하지 않는다.

상태: queued → running → succeeded | failed | cancelled
      running → retry_wait → queued (일시 오류), queued/running/retry_wait → cancel_requested → cancelled
모든 확정은 `WHERE id AND lease_token AND status` 조건과 행 잠금으로 처리권 소유자만 할 수 있다.
함수는 호출자의 Session을 쓰고 상태를 바꾸는 함수는 스스로 commit한다(LeaseLost 등은 rollback 후 예외).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.ai_settings import get_ai_settings
from app.models.ai_input import AIInput
from app.models.ai_job import AIJob
from app.schemas.ai import ErrorCode
from app.services.ai import idempotency, outbox, usage_service
from app.services.ai.access import AIError, ensure_owner
from app.services.ai.lease import CancelRequested, Lease, LeaseLost

if TYPE_CHECKING:
    from app.services.ai.consent import PinnedConfig

__all__ = ['Lease', 'LeaseLost', 'CreateResult', 'HeartbeatResult', 'create_job', 'claim_job', 'heartbeat',
           'request_cancel', 'finish_job', 'fail_job', 'recover_expired', 'fail_queue_deadline',
           'run_maintenance', 'retry_delay', 'TERMINAL_STATUSES', 'IN_PROGRESS_STATUSES']

TERMINAL_STATUSES = ('succeeded', 'failed', 'cancelled')
IN_PROGRESS_STATUSES = ('queued', 'running', 'retry_wait', 'cancel_requested')
RETRY_DELAY_MAX = 600


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def retry_delay(attempt_count: int) -> int:
    return min(get_ai_settings().retry_delay_seconds * 2 ** max(attempt_count - 1, 0), RETRY_DELAY_MAX)


@dataclass
class CreateResult:
    job: AIJob
    created: bool
    reused: str | None = None        # 'idempotency' | 'fingerprint'


@dataclass
class HeartbeatResult:
    alive: bool
    cancel_requested: bool = False


def _has_unresolved_reservation(db: Session, job_id: UUID) -> bool:
    """정산·해제되지 않은(reserved) 또는 결과가 불명확한(uncertain) 비용 예약이 이 작업에 있는가."""
    return db.execute(text("""SELECT 1 FROM usage_reservations WHERE job_id = :j
                              AND status IN ('reserved', 'uncertain') LIMIT 1"""), {'j': job_id}).first() is not None


def _call_state(db: Session, job_id: UUID, attempt_no: int) -> str:
    """이 attempt에서 제공자 호출이 일어났는가: 'none'(호출 전) / 'uncertain'(결과 모름) / 'settled'(처리·정산됨).

    정산이 끝난 예약도 호출이 처리됐다는 기록이다. 미정산 예약만 보면 정산 직후·결과 저장 전에 중단된 작업을
    '호출 전'으로 오판해 과금 호출을 되풀이한다.
    """
    statuses = set(db.execute(text("""SELECT status FROM usage_reservations WHERE job_id = :j AND attempt_no = :n"""),
                              {'j': job_id, 'n': attempt_no}).scalars())
    if statuses & {'reserved', 'uncertain'}:
        return 'uncertain'
    return 'settled' if 'settled' in statuses else 'none'


def _close_open_reservations(db: Session, lease: Lease, *, succeeded: bool) -> bool:
    """확정 시점에 남은 reserved 예약을 정리한다. 정리한 것이 불명(uncertain)이면 True.

    성공이면 실제 비용을 모르므로 예약액으로 추정 정산하고, 그 외에는 호출이 일어났는지 알 수 없으므로
    uncertain으로 남겨 예산을 계속 점유한다.
    """
    amount = db.execute(text("""SELECT max(reserved_amount) FROM usage_reservations
                                WHERE job_id = :j AND attempt_no = :n AND status = 'reserved'"""),
                        {'j': lease.job_id, 'n': lease.attempt_no}).scalar()
    if amount is None:
        return False
    if succeeded:
        usage_service.settle_attempt(db, lease.job_id, lease.attempt_no, amount)
        return False
    usage_service.mark_uncertain(db, lease.job_id, lease.attempt_no)
    return True


def _advisory_lock(db: Session, key: str) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {'k': key})


# ── 생성 ────────────────────────────────────────────────────────────────

def create_job(db: Session, *, owner_id: UUID, repository_id: int, input_id: UUID | None, kind: str, fingerprint: str,
               route: str, idempotency_key: str, body_hash: str, request_options: dict | None = None,
               pinned: 'PinnedConfig | None' = None, force_regenerate: bool = False, input_factory: Callable[[Session], UUID] | None = None,
               now: datetime | None = None) -> CreateResult:
    """job·Idempotency-Key·outbox를 한 transaction으로 저장한다.

    input_factory를 주면(입력 준비 작업) 재사용 여부를 확인한 뒤에만 호출되어 새 입력을 만들고, 입력·작업·
    key·outbox가 모두 같은 transaction에 저장된다. 이때 input_id는 None이다.

    생성 작업(summary/review)은 동의로 확정한 `pinned`(제공자·모델·profile·사용자 key 버전)가 필수이며 작업에 그대로
    기록된다. 이후 key를 교체해도 이 작업은 고정된 key 버전과 모델로 실행된다.

    재사용 규칙은 두 가지로 구분한다.
    - Idempotency-Key: 같은 key·같은 요청이면 처음 만든 job을 그대로, 다른 요청이면 IDEMPOTENCY_CONFLICT.
    - fingerprint: 새 key라도 같은 (owner, kind, fingerprint)의 최신 generation이 진행 중이고 같은 key 버전에 고정돼 있으면
      그것을, 성공했고 force_regenerate가 아니면 그 결과를 재사용한다(성공 결과는 key와 무관). 실패·취소·강제 재생성은 새 generation이다.
    """
    try:
        return _create_job(db, owner_id=owner_id, repository_id=repository_id, input_id=input_id, kind=kind,
                           fingerprint=fingerprint, route=route, idempotency_key=idempotency_key,
                           body_hash=body_hash, request_options=request_options, pinned=pinned,
                           force_regenerate=force_regenerate, input_factory=input_factory, now=_now(now))
    except Exception:
        db.rollback()        # advisory lock과 이 transaction의 key·job 기록을 함께 되돌린다
        raise


def _create_job(db: Session, *, owner_id, repository_id, input_id, kind, fingerprint, route, idempotency_key,
                body_hash, request_options, pinned, force_regenerate, input_factory, now) -> CreateResult:
    settings = get_ai_settings()
    _advisory_lock(db, f'ai-create:{owner_id}')       # 같은 owner의 생성은 직렬화(재사용·한도·채번 경쟁 방지)
    entry = idempotency.begin(db, owner_id, route, idempotency_key, body_hash, now)
    if entry.resource_id is not None:
        existing = db.execute(select(AIJob).where(AIJob.id == entry.resource_id)
                              .execution_options(populate_existing=True)).scalar_one_or_none()
        if existing is not None and existing.deleted_at is None and existing.owner_user_id == owner_id:
            db.commit()
            return CreateResult(existing, False, 'idempotency')
        # key가 가리키던 작업이 삭제됐다. TTL 동안 key는 그 작업에 묶여 있으므로 새 작업을 만들지 않는다.
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '이 요청으로 만들어진 작업은 삭제됐습니다. 새 Idempotency-Key로 다시 요청해 주세요.',
                      details={'reason': 'JOB_DELETED'})

    if input_factory is None:
        _check_input(db, owner_id, repository_id, input_id, kind, now)
    if kind != 'prepare':
        _check_pinned(db, owner_id, repository_id, input_id, kind, pinned)
    latest = db.execute(select(AIJob).where(
        AIJob.owner_user_id == owner_id, AIJob.kind == kind, AIJob.fingerprint == fingerprint)
        .order_by(AIJob.generation.desc()).limit(1).with_for_update()
        .execution_options(populate_existing=True)).scalar_one_or_none()      # 다른 경로가 바꾼 상태를 반영
    if latest is not None and latest.deleted_at is None:
        # 끝난 결과는 어느 key로 만들었든 재사용할 수 있다. 진행 중 작업은 동의한 key 버전과 같을 때만 재사용한다:
        # 다른 버전에 고정된 작업을 돌려주면 사용자가 동의한 key와 다른 key로 비용·전송이 일어난다.
        # 다르면 새 generation을 만들고, 기존 작업의 고정 설정은 건드리지 않는다.
        in_progress_ok = latest.status in IN_PROGRESS_STATUSES and (
            kind == 'prepare' or latest.credential_version_id == pinned.credential_version_id)
        reusable = in_progress_ok or (latest.status == 'succeeded' and not force_regenerate)
        if reusable and kind == 'prepare' and not _prepared_input_usable(db, latest.input_id, now):
            reusable = False          # 만료됐거나 더는 실행에 쓸 수 없는 입력은 새로 준비한다
        if reusable:
            idempotency.complete(db, entry, latest.id)
            db.commit()
            return CreateResult(latest, False, 'fingerprint')

    if kind != 'prepare':
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        used = db.execute(text("""SELECT count(*) FROM ai_jobs WHERE owner_user_id = :o AND kind <> 'prepare'
                                  AND created_at >= :d"""), {'o': owner_id, 'd': day_start}).scalar()
        if used >= settings.daily_job_limit:
            raise AIError(ErrorCode.USER_LIMIT, '일일 AI 요청 한도에 도달했습니다.', retryable=False)

    if input_factory is not None:
        input_id = input_factory(db)
    job = AIJob(owner_user_id=owner_id, repository_id=repository_id, input_id=input_id, kind=kind,
                created_at=now, updated_at=now, fingerprint=fingerprint, generation=(latest.generation + 1) if latest is not None else 0,
                status='queued', request_options=request_options or {}, **_pinned_columns(kind, pinned))
    db.add(job)
    db.flush()
    outbox.enqueue(db, job.id, kind)
    idempotency.complete(db, entry, job.id)
    db.commit()
    return CreateResult(job, True)


def _pinned_columns(kind: str, pinned) -> dict:
    if pinned is None:
        return {}
    return {'consent_manifest_hash': pinned.manifest_hash, 'provider_profile': pinned.profile.id,
            'prompt_version': pinned.profile.prompt_version(kind), 'schema_version': pinned.profile.schema_version(kind),
            'provider': pinned.provider, 'model': pinned.profile.model, 'profile_fingerprint': pinned.profile_fingerprint,
            'credential_version_id': pinned.credential_version_id}


def _check_pinned(db: Session, owner_id, repository_id, input_id, kind: str, pinned) -> None:
    """생성 작업의 고정 설정을 확인한다. key 버전 행을 FOR SHARE로 잠가 교체(retire)와 직렬화한다:
    교체가 먼저 끝났으면 거부(새 확인 필요), 작업 생성이 먼저면 그 작업이 옛 key를 쓰는 동안 암호문이 남는다."""
    if pinned is None:
        raise AIError(ErrorCode.CREDENTIAL_REQUIRED, '제공자·모델·API key를 확인하고 동의해야 합니다.')
    if pinned.input_id != input_id or pinned.repository_id != repository_id:
        raise AIError(ErrorCode.PREVIEW_CHANGED, '동의한 입력과 다른 입력입니다.')
    if not pinned.profile.requires_api_key:
        if pinned.credential_version_id is not None:
            raise AIError(ErrorCode.PREVIEW_CHANGED, '로컬 실행에 API key를 사용할 수 없습니다.')
        return
    status = db.execute(text("""SELECT status FROM ai_credential_versions WHERE id = :v AND owner_user_id = :o
                                FOR SHARE"""), {'v': pinned.credential_version_id, 'o': owner_id}).scalar()
    if status != 'active':
        raise AIError(ErrorCode.PREVIEW_CHANGED, 'API key가 바뀌었거나 삭제됐습니다. 다시 확인해 주세요.')


def _prepared_input_usable(db: Session, input_id: UUID, now: datetime) -> bool:
    row = db.execute(text("""SELECT status, valid_until, expires_at FROM ai_inputs WHERE id = :i"""),
                     {'i': input_id}).one_or_none()
    if row is None or row.expires_at <= now:
        return False
    if row.status == 'building':
        return True
    return row.status == 'ready' and row.valid_until is not None and row.valid_until > now


def _check_input(db: Session, owner_id: UUID, repository_id: int, input_id: UUID, kind: str, now: datetime) -> None:
    row = db.execute(select(AIInput).where(AIInput.id == input_id, AIInput.owner_user_id == owner_id,
                                           AIInput.repository_id == repository_id)).scalar_one_or_none()
    if row is None:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '입력을 찾을 수 없습니다.')
    if kind == 'prepare':
        if row.status != 'building':
            raise AIError(ErrorCode.INVALID_STATE, '이미 확정된 입력입니다.')
        return
    if row.status == 'expired' or (row.valid_until is not None and row.valid_until <= now):
        raise AIError(ErrorCode.INPUT_EXPIRED, '입력의 실행 유효기간이 지났습니다.')
    if row.status != 'ready':
        raise AIError(ErrorCode.INVALID_STATE, '준비가 끝난 입력이 아닙니다.')


# ── 처리권 ──────────────────────────────────────────────────────────────

def claim_job(db: Session, job_id: UUID, *, now: datetime | None = None) -> Lease | None:
    """queued 작업의 처리권을 한 처리자에게만 준다. 중복 메시지·경쟁은 None을 받는다."""
    now = _now(now)
    settings = get_ai_settings()
    token = uuid4()
    row = db.execute(text("""
        UPDATE ai_jobs SET status = 'running', lease_token = :token, lease_until = :until, heartbeat_at = :now,
            started_at = COALESCE(started_at, :now), attempt_count = attempt_count + 1, updated_at = :now
        WHERE id = :id AND status = 'queued' AND deleted_at IS NULL AND cancel_requested_at IS NULL
          AND (next_attempt_at IS NULL OR next_attempt_at <= :now) AND attempt_count < :max
          AND NOT EXISTS (SELECT 1 FROM usage_reservations r WHERE r.job_id = ai_jobs.id
                          AND r.status IN ('reserved', 'uncertain'))
        RETURNING owner_user_id, repository_id, kind, attempt_count"""),
        {'token': token, 'until': now + timedelta(seconds=settings.lease_seconds), 'now': now, 'id': job_id,
         'max': settings.max_attempts}).one_or_none()
    if row is None:
        db.execute(text("""
            UPDATE ai_jobs SET status = 'failed', error_code = 'ATTEMPTS_EXHAUSTED', finished_at = :now,
                updated_at = :now
            WHERE id = :id AND status = 'queued' AND deleted_at IS NULL AND cancel_requested_at IS NULL
              AND attempt_count >= :max"""), {'now': now, 'id': job_id, 'max': settings.max_attempts})
        db.execute(text("""
            UPDATE ai_jobs SET status = 'failed', error_code = 'OUTCOME_UNCERTAIN', finished_at = :now, updated_at = :now
            WHERE id = :id AND status = 'queued' AND deleted_at IS NULL AND cancel_requested_at IS NULL
              AND EXISTS (SELECT 1 FROM usage_reservations r WHERE r.job_id = ai_jobs.id
                          AND r.status IN ('reserved', 'uncertain'))"""), {'now': now, 'id': job_id})
        db.commit()
        return None
    db.execute(text("""INSERT INTO ai_attempts (job_id, attempt_no, lease_token, status, started_at)
                       VALUES (:job, :n, :token, 'started', :now)"""),
               {'job': job_id, 'n': row.attempt_count, 'token': token, 'now': now})
    db.commit()
    return Lease(job_id=job_id, token=token, attempt_no=row.attempt_count, owner_user_id=row.owner_user_id,
                 repository_id=row.repository_id, kind=row.kind)


def heartbeat(db: Session, lease: Lease, *, now: datetime | None = None) -> HeartbeatResult:
    now = _now(now)
    row = db.execute(text("""
        UPDATE ai_jobs SET lease_until = :until, heartbeat_at = :now, updated_at = :now
        WHERE id = :id AND lease_token = :token AND status IN ('running', 'cancel_requested')
        RETURNING status"""),
        {'until': now + timedelta(seconds=get_ai_settings().lease_seconds), 'now': now, 'id': lease.job_id,
         'token': lease.token}).one_or_none()
    db.commit()
    if row is None:
        return HeartbeatResult(alive=False)
    return HeartbeatResult(alive=True, cancel_requested=row.status == 'cancel_requested')


def request_cancel(db: Session, job_id: UUID, owner_id: UUID, *, now: datetime | None = None) -> str:
    """취소를 접수한다. 대기 중이면 즉시 cancelled, 실행 중이면 cancel_requested(처리자가 확정)."""
    now = _now(now)
    row = db.execute(text("SELECT owner_user_id, status, deleted_at FROM ai_jobs WHERE id = :id FOR UPDATE"),
                     {'id': job_id}).one_or_none()
    if row is None or row.deleted_at is not None:
        db.rollback()
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '작업을 찾을 수 없습니다.')
    try:
        ensure_owner(row.owner_user_id, owner_id)
    except AIError:
        db.rollback()
        raise
    status = row.status
    if status in ('queued', 'retry_wait'):
        db.execute(text("""UPDATE ai_jobs SET status = 'cancelled', cancel_requested_at = :now, finished_at = :now,
                           next_attempt_at = NULL, lease_token = NULL, lease_until = NULL, updated_at = :now
                           WHERE id = :id"""), {'now': now, 'id': job_id})
        status = 'cancelled'
    elif status == 'running':
        db.execute(text("""UPDATE ai_jobs SET status = 'cancel_requested', cancel_requested_at = :now,
                           updated_at = :now WHERE id = :id"""), {'now': now, 'id': job_id})
        status = 'cancel_requested'
    db.commit()
    return status


def _locked(db: Session, lease: Lease):
    row = db.execute(text("""SELECT status, attempt_count, kind FROM ai_jobs
                             WHERE id = :id AND lease_token = :token FOR UPDATE"""),
                     {'id': lease.job_id, 'token': lease.token}).one_or_none()
    if row is None or row.status not in ('running', 'cancel_requested'):
        db.rollback()
        raise LeaseLost('처리권을 잃은 작업입니다.')
    return row


def _close_attempt(db: Session, lease: Lease, status: str, error_code: str | None, now: datetime) -> None:
    db.execute(text("""UPDATE ai_attempts SET status = :s, error_code = :e, finished_at = :now
                       WHERE job_id = :j AND attempt_no = :n AND status = 'started'"""),
               {'s': status, 'e': error_code, 'now': now, 'j': lease.job_id, 'n': lease.attempt_no})


def _terminal(db: Session, lease: Lease, status: str, error_code: str | None, now: datetime) -> None:
    db.execute(text("""UPDATE ai_jobs SET status = :s, error_code = :e, finished_at = :now, lease_token = NULL,
                       lease_until = NULL, next_attempt_at = NULL, updated_at = :now WHERE id = :id"""),
               {'s': status, 'e': error_code, 'now': now, 'id': lease.job_id})


def finish_job(db: Session, lease: Lease, outcome: str = 'succeeded', *, error_code: str | None = None,
               work: Callable[[Session], None] | None = None, now: datetime | None = None) -> str:
    """처리권 소유자가 종료 상태를 확정한다. 확정된 최종 status를 돌려준다.

    - work(결과 저장 등)는 같은 transaction에서 처리권을 확인한 뒤 실행되어 결과와 상태가 함께 커밋된다.
    - 취소가 접수된 작업은 성공으로 확정하지 않는다(work도 실행하지 않는다).
    - 처리권이 없으면 LeaseLost. work가 실패하면 rollback되어 작업은 running으로 남는다.
    """
    if outcome not in ('succeeded', 'failed'):
        raise ValueError('outcome은 succeeded 또는 failed여야 합니다.')
    if outcome == 'failed' and not error_code:
        raise ValueError('failed에는 error_code가 필요합니다.')
    now = _now(now)
    row = _locked(db, lease)
    if row.status == 'cancel_requested':
        final, attempt_status, error_code = 'cancelled', 'cancelled', None
        _close_open_reservations(db, lease, succeeded=False)
    else:
        final, attempt_status = outcome, outcome
        if outcome == 'succeeded' and work is not None:
            try:
                work(db)
            except Exception:
                db.rollback()
                raise
        _close_open_reservations(db, lease, succeeded=outcome == 'succeeded')
    _close_attempt(db, lease, attempt_status, error_code, now)
    _terminal(db, lease, final, error_code if final == 'failed' else None, now)
    db.commit()
    return final


def fail_job(db: Session, lease: Lease, error_code: str, *, retryable: bool = False,
             now: datetime | None = None) -> str:
    """실패를 확정한다. 재시도 가능하고 한도가 남았으면 retry_wait(+outbox 재발행 예약), 아니면 failed."""
    now = _now(now)
    row = _locked(db, lease)
    _close_open_reservations(db, lease, succeeded=False)       # 남은 예약이 있으면 호출 결과가 불명확하다
    if row.status == 'cancel_requested':
        _close_attempt(db, lease, 'cancelled', None, now)
        _terminal(db, lease, 'cancelled', None, now)
        final = 'cancelled'
    elif _has_unresolved_reservation(db, lease.job_id):
        # 호출 결과를 알 수 없으면 자동으로 새 호출을 시작하지 않는다(사용자가 명시적으로 재시도).
        _close_attempt(db, lease, 'failed', 'OUTCOME_UNCERTAIN', now)
        _terminal(db, lease, 'failed', 'OUTCOME_UNCERTAIN', now)
        final = 'failed'
    elif retryable and row.attempt_count < get_ai_settings().max_attempts             and _call_state(db, lease.job_id, lease.attempt_no) == 'none':        # 이미 처리된 호출은 자동으로 되풀이하지 않는다
        due = now + timedelta(seconds=retry_delay(row.attempt_count))
        _close_attempt(db, lease, 'failed', error_code, now)
        db.execute(text("""UPDATE ai_jobs SET status = 'retry_wait', error_code = :e, next_attempt_at = :due,
                           lease_token = NULL, lease_until = NULL, updated_at = :now WHERE id = :id"""),
                   {'e': error_code, 'due': due, 'now': now, 'id': lease.job_id})
        outbox.enqueue(db, lease.job_id, row.kind, available_at=due)
        final = 'retry_wait'
    else:
        _close_attempt(db, lease, 'failed', error_code, now)
        _terminal(db, lease, 'failed', error_code, now)
        final = 'failed'
    db.commit()
    return final


# ── 복구·유지관리 ───────────────────────────────────────────────────────

def recover_expired(db: Session, *, now: datetime | None = None, limit: int = 100) -> dict:
    """처리권이 만료된 작업을 정리한다(처리자 중단 복구).

    - AI 호출을 예약해 둔 attempt는 결과가 불명확하므로 새 호출로 이어가지 않는다(uncertain → failed).
    - 호출 전이었다면 attempt를 실패로 닫고 한도가 남았으면 재시도 대기, 아니면 실패.
    - 취소가 접수된 작업은 cancelled.
    """
    now = _now(now)
    max_attempts = get_ai_settings().max_attempts
    stats = {'retried': 0, 'uncertain': 0, 'exhausted': 0, 'cancelled': 0, 'result_lost': 0}
    rows = db.execute(text("""
        SELECT id, status, attempt_count, kind FROM ai_jobs
        WHERE status IN ('running', 'cancel_requested') AND lease_until < :now
        ORDER BY lease_until LIMIT :limit FOR UPDATE SKIP LOCKED"""), {'now': now, 'limit': limit}).all()
    for row in rows:
        lease = Lease(job_id=row.id, token=uuid4(), attempt_no=row.attempt_count, owner_user_id=None,
                      repository_id=0, kind=row.kind)
        state = _call_state(db, row.id, row.attempt_count)
        called = state == 'uncertain' or _has_unresolved_reservation(db, row.id)
        if called:
            usage_service.mark_uncertain(db, row.id, row.attempt_count)
        if row.status == 'cancel_requested':
            _close_attempt(db, lease, 'cancelled', None, now)
            _terminal(db, lease, 'cancelled', None, now)
            stats['cancelled'] += 1
        elif called:
            _terminal(db, lease, 'failed', 'OUTCOME_UNCERTAIN', now)
            stats['uncertain'] += 1
        elif state == 'settled':
            # 호출은 처리·정산됐지만 결과를 저장하지 못했다. 같은 작업으로 다시 호출하지 않는다(새 generation은 사용자가 요청).
            _close_attempt(db, lease, 'failed', 'RESULT_LOST', now)
            _terminal(db, lease, 'failed', 'RESULT_LOST', now)
            stats['result_lost'] += 1
        else:
            _close_attempt(db, lease, 'failed', 'LEASE_EXPIRED', now)
            if row.attempt_count < max_attempts:
                db.execute(text("""UPDATE ai_jobs SET status = 'retry_wait', error_code = 'LEASE_EXPIRED',
                                   next_attempt_at = :now, lease_token = NULL, lease_until = NULL, updated_at = :now
                                   WHERE id = :id"""), {'now': now, 'id': row.id})
                outbox.enqueue(db, row.id, row.kind, available_at=now)
                stats['retried'] += 1
            else:
                _terminal(db, lease, 'failed', 'ATTEMPTS_EXHAUSTED', now)
                stats['exhausted'] += 1
    db.commit()
    return stats


def fail_queue_deadline(db: Session, *, now: datetime | None = None) -> int:
    """대기열에서 기한을 넘긴 작업을 실패로 닫는다(발행이 계속 실패하는 경우 등)."""
    now = _now(now)
    cutoff = now - timedelta(seconds=get_ai_settings().queue_deadline_seconds)
    result = db.execute(text("""
        UPDATE ai_jobs SET status = 'failed', error_code = 'QUEUE_TIMEOUT', finished_at = :now, next_attempt_at = NULL,
            updated_at = :now
        WHERE status IN ('queued', 'retry_wait') AND deleted_at IS NULL
          AND COALESCE(next_attempt_at, created_at) < :cutoff"""), {'now': now, 'cutoff': cutoff})
    db.commit()
    return result.rowcount


def run_maintenance(db: Session, *, now: datetime | None = None) -> dict:
    now = _now(now)
    stats = recover_expired(db, now=now)
    stats['queue_timeout'] = fail_queue_deadline(db, now=now)
    stats['idempotency_purged'] = idempotency.purge_expired(db, now)
    db.commit()
    return stats
