"""작업 실행기: 처리권 획득 → handler 실행(+heartbeat) → 확정. 종류별 handler는 등록해서 쓴다.

S4에서는 모델 없이 fake handler로 생성·배송·완료·취소·복구를 검증한다. 실제 handler(입력 준비, 설명·리뷰
생성)는 이후 단계에서 register_handler로 등록한다.
"""
import importlib
import logging
import os
import threading
from dataclasses import dataclass
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
    """work는 결과 저장처럼 상태 확정과 같은 transaction에서 실행할 함수다."""
    work: Callable[[Session], None] | None = None


class JobContext:
    def __init__(self, lease: Lease, session_factory):
        self.lease = lease
        self.session_factory = session_factory
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


def load_handler_modules() -> None:
    """AI_HANDLER_MODULES(쉼표 구분 import 경로)의 모듈을 불러와 handler를 등록하게 한다."""
    for name in filter(None, (m.strip() for m in os.getenv('AI_HANDLER_MODULES', '').split(','))):
        importlib.import_module(name)


def run_job(job_id: UUID, *, session_factory=None) -> str:
    """한 작업을 실행한다. 결과: skipped / succeeded / failed / retry / cancelled / lost."""
    if session_factory is None:
        from app.core.database import SessionLocal as session_factory
    with session_factory() as db:
        lease = job_service.claim_job(db, job_id)
    if lease is None:
        return 'skipped'
    ctx = JobContext(lease, session_factory)
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
            return _fail(session_factory, lease, 'RESULT_STORAGE_FAILED', retryable=True)
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
