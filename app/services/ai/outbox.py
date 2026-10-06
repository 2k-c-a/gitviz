"""작업 접수 outbox. job과 같은 transaction에 기록하고 dispatcher가 큐로 발행한다.

발행은 at-least-once다. commit 후 Redis 발행이 실패하면 행이 남아 재발행되고, 같은 메시지가 두 번
도착해도 job_service.claim_job이 처리권을 한 번만 내준다.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

EVENT_PREPARE = 'prepare'
EVENT_GENERATE = 'generate'
RETRY_BACKOFF_BASE = 2
RETRY_BACKOFF_MAX = 300


@dataclass(frozen=True)
class OutboxEvent:
    id: UUID
    job_id: UUID
    event_type: str
    dispatch_generation: int


def event_type_for(kind: str) -> str:
    return EVENT_PREPARE if kind == 'prepare' else EVENT_GENERATE


def enqueue(db: Session, job_id: UUID, kind: str, *, available_at: datetime | None = None) -> bool:
    """다음 dispatch_generation으로 행을 추가한다(호출자의 transaction 안). 새로 추가했으면 True."""
    event = event_type_for(kind)
    result = db.execute(text("""
        INSERT INTO task_outbox (job_id, dispatch_generation, event_type, available_at)
        SELECT :job, COALESCE(MAX(dispatch_generation) + 1, 0), :event, COALESCE(:available, now())
        FROM task_outbox WHERE job_id = :job
        ON CONFLICT (job_id, dispatch_generation, event_type) DO NOTHING"""),
        {'job': job_id, 'event': event, 'available': available_at})
    return result.rowcount == 1


def dispatch_pending(db: Session, publish: Callable[[OutboxEvent], None], *, limit: int = 50,
                     now: datetime | None = None) -> dict:
    """도래한 미발행 행을 발행한다. 여러 dispatcher가 동시에 돌아도 SKIP LOCKED로 겹치지 않는다.

    - 이미 종료·삭제된 job의 행은 발행하지 않고 처리 완료로 표시한다.
    - retry_wait 작업은 대기 시간이 지났으면 queued로 되돌리고 **먼저 commit한 뒤** 발행한다. 그렇지 않으면
      worker가 아직 retry_wait로 보이는 작업의 메시지를 받아 버려 영영 처리되지 않는다.
    - 발행 실패는 retry_count를 올리고 backoff 후 다시 시도한다(행은 남는다).
    """
    now = now or datetime.now(timezone.utc)
    db.execute(text("""
        UPDATE ai_jobs SET status = 'queued', updated_at = :now
        WHERE status = 'retry_wait' AND deleted_at IS NULL AND next_attempt_at <= :now
          AND EXISTS (SELECT 1 FROM task_outbox o WHERE o.job_id = ai_jobs.id AND o.published_at IS NULL
                      AND o.available_at <= :now)"""), {'now': now})
    db.commit()
    rows = db.execute(text("""
        SELECT o.id, o.job_id, o.event_type, o.dispatch_generation, o.retry_count
        FROM task_outbox o WHERE o.published_at IS NULL AND o.available_at <= :now
        ORDER BY o.available_at, o.id LIMIT :limit FOR UPDATE SKIP LOCKED"""),
        {'now': now, 'limit': limit}).all()
    stats = {'published': 0, 'skipped': 0, 'failed': 0}
    for row in rows:
        job = db.execute(text("SELECT status, deleted_at FROM ai_jobs WHERE id = :id"), {'id': row.job_id}).one_or_none()
        if job is None or job.deleted_at is not None or job.status not in ('queued', 'retry_wait'):
            db.execute(text("UPDATE task_outbox SET published_at = :now WHERE id = :id"), {'now': now, 'id': row.id})
            stats['skipped'] += 1
            continue
        if job.status == 'retry_wait':
            continue       # 아직 재시도 시각 전이다. 행을 남겨 두고 다음 주기에 다시 본다.
        try:
            publish(OutboxEvent(row.id, row.job_id, row.event_type, row.dispatch_generation))
        except Exception:
            delay = min(RETRY_BACKOFF_BASE ** (row.retry_count + 1), RETRY_BACKOFF_MAX)
            db.execute(text("""UPDATE task_outbox SET retry_count = retry_count + 1,
                               available_at = :next WHERE id = :id"""),
                       {'next': now + timedelta(seconds=delay), 'id': row.id})
            stats['failed'] += 1
            continue
        db.execute(text("UPDATE task_outbox SET published_at = :now WHERE id = :id"), {'now': now, 'id': row.id})
        stats['published'] += 1
    db.commit()
    return stats
