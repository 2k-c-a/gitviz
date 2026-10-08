"""Idempotency-Key 처리. 같은 key·같은 요청은 같은 자원을, 같은 key·다른 요청은 409를 돌려준다.

작업 fingerprint 재사용과는 다른 규칙이다(job_service.create_job 참고). 이 모듈은 owner·route·key
행만 다루며, 호출자가 같은 transaction에서 advisory lock을 잡고 사용한다.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.ai_job import RequestIdempotency
from app.schemas.ai import ErrorCode, canonical_hash
from app.services.ai.access import AIError

KEY_TTL = timedelta(hours=24)
_KEY_RE = re.compile(r'^[\x21-\x7e]{1,255}$')    # 공백 없는 출력 가능 ASCII


def validate_key(key) -> str:
    if not isinstance(key, str) or not _KEY_RE.match(key):
        raise AIError(ErrorCode.INVALID_STATE, 'Idempotency-Key가 올바르지 않습니다.')
    return key


def request_hash(body: dict) -> str:
    return canonical_hash(body)


@dataclass
class IdempotencyEntry:
    row: RequestIdempotency
    resource_id: UUID | None     # 이전에 같은 요청으로 만든 자원. 새 요청이면 None


def begin(db: Session, owner_id: UUID, route: str, key: str, body_hash: str,
          now: datetime | None = None) -> IdempotencyEntry:
    """key를 확보한다. 같은 요청의 재전송이면 resource_id가 채워져 돌아온다."""
    now = now or datetime.now(timezone.utc)
    validate_key(key)
    row = db.execute(select(RequestIdempotency).where(
        RequestIdempotency.owner_user_id == owner_id, RequestIdempotency.route == route,
        RequestIdempotency.key == key).with_for_update()).scalar_one_or_none()
    if row is not None and row.expires_at <= now:
        db.execute(delete(RequestIdempotency).where(RequestIdempotency.id == row.id))
        db.flush()
        row = None
    if row is None:
        row = RequestIdempotency(owner_user_id=owner_id, route=route, key=key, request_hash=body_hash,
                                 status='in_progress', expires_at=now + KEY_TTL)
        db.add(row)
        db.flush()
        return IdempotencyEntry(row, None)
    if row.request_hash != body_hash:
        raise AIError(ErrorCode.IDEMPOTENCY_CONFLICT, '같은 Idempotency-Key로 다른 요청을 보낼 수 없습니다.')
    return IdempotencyEntry(row, row.resource_id)


def complete(db: Session, entry: IdempotencyEntry, resource_id: UUID) -> None:
    entry.row.resource_id = resource_id
    entry.row.status = 'completed'
    db.flush()


def purge_expired(db: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    result = db.execute(delete(RequestIdempotency).where(RequestIdempotency.expires_at <= now))
    return result.rowcount
