"""AI 작업 큐 발행(dispatcher)과 실행 task.

- dispatcher: outbox의 미발행 행을 Celery로 보낸다. `python -m app.tasks.ai_dispatch`로 상시 실행하며
  주기적으로 유지관리(만료 처리권 복구, 대기 기한, 만료 key 정리)도 수행한다.
- task: ai.prepare_job / ai.generate_job. 같은 메시지가 두 번 와도 claim_job이 한 번만 처리하게 한다.
"""
import logging
import signal
import threading
import time
from uuid import UUID

from app.core.celery_app import celery
from app.core.database import SessionLocal
from app.services.ai import input_store, job_runner, job_service, outbox

logger = logging.getLogger('gitviz.ai.dispatch')

QUEUE_BY_EVENT = {outbox.EVENT_PREPARE: 'ai_prepare', outbox.EVENT_GENERATE: 'ai_generate'}
TASK_BY_EVENT = {outbox.EVENT_PREPARE: 'ai.prepare_job', outbox.EVENT_GENERATE: 'ai.generate_job'}
DISPATCH_INTERVAL = 2.0
MAINTENANCE_INTERVAL = 30.0

from app.tasks import ai_prepare  # noqa: E402,F401  (운영 prepare handler 등록)

# 환경변수로 지정한 handler 모듈은 운영 handler 뒤에 불러와 그 위에 덮어쓸 수 있다(테스트용 fake 등).
job_runner.load_handler_modules()


@celery.task(name='ai.prepare_job', acks_late=True, reject_on_worker_lost=True)
def prepare_job(job_id: str) -> str:
    return job_runner.run_job(UUID(job_id))


@celery.task(name='ai.generate_job', acks_late=True, reject_on_worker_lost=True)
def generate_job(job_id: str) -> str:
    return job_runner.run_job(UUID(job_id))


def publish_event(event: outbox.OutboxEvent) -> None:
    """Redis 발행이 실패하면 예외를 올려 outbox가 재시도하게 한다."""
    # 발행 연결만 짧은 timeout을 쓴다(실패는 outbox가 재시도). 기존 분석 task의 설정은 건드리지 않는다.
    with celery.connection_for_write(transport_options={'socket_connect_timeout': 3, 'socket_timeout': 10}) as conn:
        celery.send_task(TASK_BY_EVENT[event.event_type], args=[str(event.job_id)],
                         queue=QUEUE_BY_EVENT[event.event_type], retry=False, connection=conn)


def dispatch_once(session_factory=SessionLocal, publish=publish_event, *, limit: int = 50) -> dict:
    with session_factory() as db:
        return outbox.dispatch_pending(db, publish, limit=limit)


def maintenance_once(session_factory=SessionLocal) -> dict:
    with session_factory() as db:
        stats = job_service.run_maintenance(db)
    with session_factory() as db:
        stats['inputs'] = input_store.run_input_maintenance(db)
    return stats


def run_forever(stop: threading.Event | None = None, *, session_factory=SessionLocal,
                dispatch_interval: float = DISPATCH_INTERVAL, maintenance_interval: float = MAINTENANCE_INTERVAL):
    stop = stop or threading.Event()
    last_maintenance = 0.0
    while not stop.is_set():
        try:
            dispatch_once(session_factory)
            if time.monotonic() - last_maintenance >= maintenance_interval:
                maintenance_once(session_factory)
                last_maintenance = time.monotonic()
        except Exception as exc:       # DB·큐 일시 장애로 dispatcher가 죽지 않게 한다
            logger.error('dispatcher iteration failed error=%s', type(exc).__name__)
        stop.wait(dispatch_interval)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    run_forever(stop)


if __name__ == '__main__':
    main()
