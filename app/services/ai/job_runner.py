"""작업 실행기: 처리권 획득 → handler 실행(+heartbeat) → 확정. 종류별 handler는 등록해서 쓴다.

작업은 API 프로세스의 스레드 풀에서 실행한다(submit, AD-33). 사용자 API key는 요청 헤더로 받아 그 실행의
JobContext에만 두고 DB·로그·큐에 남기지 않는다. 일시 오류의 자동 재시도도 같은 실행이 key를 든 채 예약한다.
handler(입력 준비, 설명·리뷰 생성)는 input_prepare·generation_service가 register_handler로 등록한다.
"""
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.ai_settings import get_ai_settings
from app.services.ai import job_service
from app.services.ai.lease import CancelRequested, Lease, LeaseLost

logger = logging.getLogger('gitviz.ai.jobs')


class RetryableError(Exception):
    """일시 오류. 한도가 남았으면 backoff 후 재시도한다."""

    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code = error_code


class JobFailed(Exception):
    """재시도해도 소용없는 실패."""

    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code = error_code


@dataclass
class HandlerResult:
    """work는 결과 저장처럼 상태 확정과 같은 transaction에서 실행할 함수다.

    storage_retryable=False이면 work가 실패해도 handler를 다시 실행하지 않는다. 이미 비용이 든 외부 호출을
    자동으로 되풀이하지 않기 위해서다(사용자가 명시적으로 다시 시작한다).
    """
    work: Callable[[Session], None] | None = None
    storage_retryable: bool = True


class JobContext:
    def __init__(self, lease: Lease, session_factory, api_key: str | None = None):
        self.lease = lease
        self.session_factory = session_factory
        self.api_key = api_key           # 이 실행에서만 쓰는 사용자 key(외부 모델). 저장·기록하지 않는다
        self.lost = False
        self.cancel_requested = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def heartbeat(self) -> job_service.HeartbeatResult:
        with self.session_factory() as db:
            result = job_service.heartbeat(db, self.lease)
        self.lost = not result.alive
        self.cancel_requested = self.cancel_requested or result.cancel_requested
        return result

    def raise_if_stopped(self) -> None:
        if self.lost:
            raise LeaseLost('처리권을 잃은 작업입니다.')

    def start_heartbeat(self) -> None:
        interval = max(get_ai_settings().lease_seconds / 3, 0.5)

        def loop():
            while not self._stop.wait(interval):
                try:
                    if not self.heartbeat().alive:
                        return
                except Exception:      # DB 일시 장애는 다음 주기에 다시 시도한다
                    logger.warning('heartbeat failed job=%s', self.lease.job_id)
        self._thread = threading.Thread(target=loop, name=f'ai-heartbeat-{self.lease.job_id}', daemon=True)
        self._thread.start()

    def stop_heartbeat(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


Handler = Callable[[JobContext], 'HandlerResult | None']
HANDLERS: dict[str, Handler] = {}


def register_handler(kind: str, handler: Handler) -> None:
    HANDLERS[kind] = handler


def load_handlers() -> None:
    """실제 handler를 등록한다(입력 준비·설명·리뷰 생성). 테스트가 먼저 등록한 handler는 덮어쓰지 않는다."""
    if {'prepare', 'summary', 'review'} <= HANDLERS.keys():
        return
    from app.services.ai import generation_service, input_prepare  # noqa: F401  (import 시 등록)


def run_job(job_id: UUID, *, session_factory=None, api_key: str | None = None) -> str:
    """한 작업을 실행한다. 결과: skipped / succeeded / failed / retry / cancelled / lost."""
    if session_factory is None:
        from app.core.database import SessionLocal as session_factory
    with session_factory() as db:
        lease = job_service.claim_job(db, job_id)
    if lease is None:
        return 'skipped'
    ctx = JobContext(lease, session_factory, api_key)
    ctx.start_heartbeat()
    try:
        handler = HANDLERS.get(lease.kind)
        if handler is None:
            return _fail(session_factory, lease, 'NO_HANDLER', retryable=False)
        try:
            result = handler(ctx)
        except RetryableError as exc:
            return _fail(session_factory, lease, exc.error_code, retryable=True)
        except JobFailed as exc:
            return _fail(session_factory, lease, exc.error_code, retryable=False)
        except LeaseLost:
            return 'lost'
        except CancelRequested:
            return _fail(session_factory, lease, 'CANCELLED', retryable=False)   # 취소 접수 상태면 cancelled로 확정
        except Exception as exc:
            logger.error('job handler crashed job=%s error=%s', job_id, type(exc).__name__)
            return _fail(session_factory, lease, 'INTERNAL_ERROR', retryable=False)
        try:
            with session_factory() as db:
                final = job_service.finish_job(db, lease, 'succeeded', work=getattr(result, 'work', None))
        except LeaseLost:
            return 'lost'
        except Exception as exc:
            logger.error('job finalize failed job=%s error=%s', job_id, type(exc).__name__)
            return _fail(session_factory, lease, 'RESULT_STORAGE_FAILED',
                         retryable=getattr(result, 'storage_retryable', True))
        return final
    finally:
        ctx.stop_heartbeat()


def _fail(session_factory, lease: Lease, error_code: str, *, retryable: bool) -> str:
    try:
        with session_factory() as db:
            final = job_service.fail_job(db, lease, error_code, retryable=retryable)
    except LeaseLost:
        return 'lost'
    return {'retry_wait': 'retry'}.get(final, final)


# ── API 프로세스 실행(AD-33) ─────────────────────────────────────────────

MAX_CONCURRENT_JOBS = 2
_POOL = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS, thread_name_prefix='ai-job')
_TIMERS: set[threading.Timer] = set()
_TIMERS_LOCK = threading.Lock()


def submit(job_id: UUID, api_key: str | None = None, *, session_factory=None) -> None:
    """접수된 작업을 이 프로세스의 스레드에서 실행한다. 실패하면(풀 종료 등) 작업을 INTERRUPTED로 닫는다."""
    load_handlers()
    try:
        _POOL.submit(_run_and_reschedule, job_id, api_key, session_factory)
    except RuntimeError:
        logger.error('ai job submit failed job=%s', job_id)
        _mark_interrupted(job_id, session_factory)


def _run_and_reschedule(job_id: UUID, api_key: str | None, session_factory) -> None:
    try:
        outcome = run_job(job_id, session_factory=session_factory, api_key=api_key)
    except Exception as exc:          # claim 단계의 DB 장애 등. 처리권이 있었다면 만료 정리가 닫는다
        logger.error('ai job run failed job=%s error=%s', job_id, type(exc).__name__)
        return
    if outcome == 'retry':
        _schedule_retry(job_id, api_key, session_factory)


def _schedule_retry(job_id: UUID, api_key: str | None, session_factory) -> None:
    """재시도 대기 시각에 같은 key로 다시 실행한다. 기다리는 동안 풀 스레드를 붙잡지 않는다."""
    if session_factory is None:
        from app.core.database import SessionLocal as session_factory
    from sqlalchemy import text
    with session_factory() as db:
        due = db.execute(text("SELECT next_attempt_at FROM ai_jobs WHERE id = :id AND status = 'retry_wait'"),
                         {'id': job_id}).scalar()
    if due is None:
        return
    delay = max((due - datetime.now(timezone.utc)).total_seconds(), 0.0)

    def fire():
        with _TIMERS_LOCK:
            _TIMERS.discard(timer)
        try:
            with session_factory() as db:
                if not job_service.requeue_due(db, job_id):
                    return               # 그사이 취소·삭제됐다
        except Exception:
            logger.warning('ai job requeue failed job=%s', job_id)
            return
        submit(job_id, api_key, session_factory=session_factory)

    timer = threading.Timer(delay, fire)
    timer.daemon = True
    with _TIMERS_LOCK:
        _TIMERS.add(timer)
    timer.start()


def _mark_interrupted(job_id: UUID, session_factory) -> None:
    if session_factory is None:
        from app.core.database import SessionLocal as session_factory
    from sqlalchemy import text
    try:
        with session_factory() as db:
            db.execute(text("""UPDATE ai_jobs SET status = 'failed', error_code = 'INTERRUPTED', finished_at = now(),
                               updated_at = now() WHERE id = :id AND status IN ('queued', 'retry_wait')"""),
                       {'id': job_id})
            db.commit()
    except Exception:
        logger.warning('ai job mark interrupted failed job=%s', job_id)


# ── 유지관리(이전 dispatcher의 주기 작업) ─────────────────────────────────

MAINTENANCE_INTERVAL = 60.0
_MAINTENANCE_STARTED = threading.Event()


def maintenance_once(session_factory=None) -> dict:
    """실행자를 잃은 작업 정리, 기한이 지난 입력·결과 정리. 여러 프로세스가 동시에 불러도 안전하다."""
    if session_factory is None:
        from app.core.database import SessionLocal as session_factory
    from app.services.ai import input_store, result_store
    with session_factory() as db:
        stats = job_service.run_maintenance(db)
    with session_factory() as db:
        stats['inputs'] = input_store.run_input_maintenance(db)
    with session_factory() as db:
        stats['results_purged'] = result_store.purge_expired_results(db)
    return stats


def start_maintenance_loop(interval: float | None = None) -> None:
    """API 프로세스 시작 때 한 번 부른다. 데몬 스레드라 프로세스 종료를 막지 않는다.
    AI_MAINTENANCE_INTERVAL_SECONDS=0이면 시작하지 않는다(테스트가 정리 시점을 직접 정할 때)."""
    if interval is None:
        try:
            interval = float(os.getenv('AI_MAINTENANCE_INTERVAL_SECONDS', str(MAINTENANCE_INTERVAL)))
        except ValueError:
            interval = MAINTENANCE_INTERVAL
    if interval <= 0 or _MAINTENANCE_STARTED.is_set():
        return
    _MAINTENANCE_STARTED.set()

    def loop():
        while True:
            try:
                maintenance_once()
            except Exception as exc:          # DB 일시 장애는 다음 주기에 다시 시도한다
                logger.warning('ai maintenance failed error=%s', type(exc).__name__)
            threading.Event().wait(interval)
    threading.Thread(target=loop, name='ai-maintenance', daemon=True).start()
