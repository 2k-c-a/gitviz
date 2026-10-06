"""큐 통합 테스트용 fake handler. AI_HANDLER_MODULES=scripts.ai_test_handlers로 worker에 불러온다.

작업의 request_options.mode로 동작을 고른다. 모델·네트워크는 쓰지 않는다.
  ok            즉시 성공
  retry_once    첫 시도는 일시 오류, 두 번째 시도는 성공
  sleep_first   첫 시도는 'started' 기록 후 오래 멈춘다(worker 중단 시험), 이후 시도는 성공
  reserve_sleep 호출 예산을 예약한 뒤 오래 멈춘다(불명 호출 시험)
  wait_cancel   취소가 접수될 때까지 기다렸다가 성공을 시도한다
"""
import time
from decimal import Decimal

from sqlalchemy import text

from app.services.ai import job_runner, usage_service


def _mode(ctx):
    with ctx.session_factory() as db:
        options = db.execute(text('SELECT request_options FROM ai_jobs WHERE id = :j'), {'j': ctx.lease.job_id}).scalar()
    return options.get('mode', 'ok')


def _audit(ctx, action):
    with ctx.session_factory() as db:
        db.execute(text("""INSERT INTO audit_events(action, object_type, object_id, request_id)
                           VALUES(:a, 'job', :j, :n)"""),
                   {'a': action, 'j': str(ctx.lease.job_id), 'n': f'attempt-{ctx.lease.attempt_no}'})
        db.commit()


def handle(ctx):
    mode = _mode(ctx)
    _audit(ctx, 'executed')
    if mode == 'retry_once' and ctx.lease.attempt_no == 1:
        raise job_runner.RetryableError('TEMPORARY')
    if mode == 'sleep_first' and ctx.lease.attempt_no == 1:
        _audit(ctx, 'sleeping')
        time.sleep(120)
    if mode == 'reserve_sleep':
        with ctx.session_factory() as db:
            usage_service.reserve_attempt(db, ctx.lease, Decimal('1'))
            db.commit()
        _audit(ctx, 'sleeping')
        time.sleep(120)
    if mode == 'wait_cancel':
        _audit(ctx, 'waiting')
        deadline = time.monotonic() + 30
        while not ctx.cancel_requested and time.monotonic() < deadline:
            time.sleep(0.2)
    return None


job_runner.register_handler('review', handle)
job_runner.register_handler('summary', handle)
job_runner.register_handler('prepare', handle)
