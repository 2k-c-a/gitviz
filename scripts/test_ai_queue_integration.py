"""S4 큐 통합 테스트: 실제 PostgreSQL + Redis + 별도 Celery worker 프로세스.

GITVIZ_TEST_DATABASE_URL(gitviz_test…)과 GITVIZ_TEST_REDIS_URL(테스트 전용 Redis, 시작 시 해당 DB를 비운다)이
필요하다. eager 실행이 아니라 outbox → Redis → worker 경로를 그대로 통과한다.
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402

URL = support.require_test_database()
REDIS_URL = os.environ.get('GITVIZ_TEST_REDIS_URL')
if not REDIS_URL:
    raise RuntimeError('GITVIZ_TEST_REDIS_URL에 테스트 전용 Redis를 지정하세요(해당 DB를 비웁니다).')
sys.path.insert(0, str(support.ROOT))
os.environ.update(REDIS_URL=REDIS_URL, AI_LEASE_SECONDS='3', AI_RETRY_DELAY_SECONDS='1', AI_MAX_ATTEMPTS='3',
                  AI_QUEUE_DEADLINE_SECONDS='600')

import redis  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402

import test_ai_job_lifecycle as lc  # noqa: E402
from app.services.ai import idempotency, job_service  # noqa: E402
from app.tasks import ai_dispatch  # noqa: E402

broker = redis.Redis.from_url(REDIS_URL)


def setUpModule():
    command.upgrade(Config(str(support.ROOT / 'alembic.ini')), 'head')


def wait_for(condition, timeout=60.0, interval=0.25, message='조건이 충족되지 않았다'):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(message)


class QueueCase(lc.Case):
    def setUp(self):
        super().setUp()
        broker.flushdb()
        self.workers, self.logs = [], []
        self.stop = threading.Event()
        self.dispatcher = None

    def tearDown(self):
        self.stop.set()
        if self.dispatcher is not None:
            self.dispatcher.join(10)
        for proc in self.workers:
            self.kill(proc)
        for log in self.logs:
            log.close()
        broker.flushdb()
        super().tearDown()

    # ── 프로세스 ──
    def start_worker(self):
        log = tempfile.TemporaryFile()
        env = {**os.environ, 'AI_HANDLER_MODULES': 'scripts.ai_test_handlers', 'PYTHONIOENCODING': 'utf-8'}
        proc = subprocess.Popen(
            [sys.executable, '-m', 'celery', '-A', 'app.core.celery_app.celery', 'worker', '--pool=solo',
             '-Q', 'ai_prepare,ai_generate', '--loglevel=WARNING', '-n', f'itest-{len(self.workers)}@%h',
             '--without-gossip', '--without-mingle', '--without-heartbeat'],
            cwd=str(support.ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
        self.workers.append(proc)
        self.logs.append(log)
        return proc

    @staticmethod
    def kill(proc):
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=15)

    def start_dispatcher(self):
        self.dispatcher = threading.Thread(
            target=ai_dispatch.run_forever, args=(self.stop,),
            kwargs={'dispatch_interval': 0.3, 'maintenance_interval': 1.0}, daemon=True)
        self.dispatcher.start()

    def worker_output(self):
        out = []
        for log in self.logs:
            log.seek(0)
            out.append(log.read().decode('utf-8', 'replace')[-1500:])
        return '\n'.join(out)

    # ── 데이터 ──
    def submit(self, world, mode, *, kind='review', fingerprint=None):
        owner, repo, _, inp = world
        return job_service.create_job(
            self.db, owner_id=owner, repository_id=repo, input_id=inp, kind=kind,
            fingerprint=fingerprint or lc.H('1'), route=lc.ROUTE, idempotency_key=f'k-{mode}-{fingerprint}-{time.time_ns()}',
            body_hash=idempotency.request_hash({'mode': mode, 'f': fingerprint}), request_options={'mode': mode},
            provider_profile=None if kind == 'prepare' else 'p', prompt_version=None if kind == 'prepare' else 'v',
            schema_version=None if kind == 'prepare' else 'v').job

    def status(self, job_id):
        self.db.rollback()
        return self.scalar('SELECT status FROM ai_jobs WHERE id = :j', j=job_id)

    def wait_status(self, job_id, wanted, timeout=60):
        wanted = (wanted,) if isinstance(wanted, str) else wanted
        try:
            return wait_for(lambda: self.status(job_id) in wanted and self.status(job_id), timeout)
        except AssertionError:
            self.db.rollback()
            rows = self.db.execute(text('SELECT dispatch_generation, event_type, published_at, retry_count, available_at '
                                        'FROM task_outbox WHERE job_id = :j ORDER BY 1'), {'j': job_id}).all()
            job = self.db.execute(text('SELECT status, attempt_count, next_attempt_at, error_code, kind FROM ai_jobs '
                                       'WHERE id = :j'), {'j': job_id}).one()
            raise AssertionError(f'{job_id} job={tuple(job)} wanted={wanted} outbox={[tuple(r) for r in rows]}\n'
                                 f'{self.worker_output()}')

    def audit(self, job_id, action):
        self.db.rollback()
        return self.scalar('SELECT count(*) FROM audit_events WHERE object_id = :j AND action = :a',
                           j=str(job_id), a=action)


class EndToEnd(QueueCase):
    def test_outbox_to_worker_runs_each_job_once_and_retries_transient_errors(self):
        generate_world = self.make_world()
        prepare_world = self.make_world(input_status='building')
        ok = self.submit(generate_world, 'ok', fingerprint=lc.H('1'))
        retry = self.submit(generate_world, 'retry_once', fingerprint=lc.H('2'))
        prepare = self.submit(prepare_world, 'ok', kind='prepare', fingerprint=lc.H('3'))
        self.start_dispatcher()
        self.start_worker()
        for job in (ok, retry, prepare):
            self.wait_status(job.id, 'succeeded')
        self.assertEqual(self.audit(ok.id, 'executed'), 1)
        self.assertEqual(self.audit(prepare.id, 'executed'), 1)
        self.assertEqual(self.audit(retry.id, 'executed'), 2)                          # 일시 오류 뒤 한 번 더
        attempts = self.db.execute(text("SELECT attempt_no, status, error_code FROM ai_attempts WHERE job_id = :j ORDER BY 1"),
                                   {'j': retry.id}).all()
        self.assertEqual([tuple(a) for a in attempts], [(1, 'failed', 'TEMPORARY'), (2, 'succeeded', None)])
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_attempts WHERE job_id = :j', j=ok.id), 1)
        self.db.rollback()
        pending = self.scalar('SELECT count(*) FROM task_outbox WHERE published_at IS NULL')
        self.assertEqual(pending, 0)

    def test_events_are_routed_to_their_own_queues(self):
        generate = self.submit(self.make_world(), 'ok', fingerprint=lc.H('1'))
        prepare = self.submit(self.make_world(input_status='building'), 'ok', kind='prepare', fingerprint=lc.H('2'))
        stats = ai_dispatch.dispatch_once()
        self.assertEqual(stats['published'], 2)
        self.assertEqual((broker.llen('ai_prepare'), broker.llen('ai_generate')), (1, 1))
        self.assertEqual(broker.llen('celery'), 0)                                    # 기존 분석 큐와 분리

    def test_broker_outage_keeps_the_outbox_row_and_recovers(self):
        job = self.submit(self.make_world(), 'ok')
        original = ai_dispatch.celery.conf.broker_url
        ai_dispatch.celery.conf.broker_url = 'redis://127.0.0.1:1/0'                   # 닿지 않는 broker
        try:
            stats = ai_dispatch.dispatch_once()
        finally:
            ai_dispatch.celery.conf.broker_url = original
        self.assertEqual((stats['published'], stats['failed']), (0, 1))
        self.db.rollback()
        row = self.db.execute(text('SELECT retry_count, published_at FROM task_outbox')).one()
        self.assertEqual((row.retry_count, row.published_at), (1, None))
        self.assertEqual(self.status(job.id), 'queued')
        self.db.execute(text("UPDATE task_outbox SET available_at = now()"))          # backoff 경과로 간주
        self.db.commit()
        self.start_dispatcher()
        self.start_worker()
        self.wait_status(job.id, 'succeeded')
        self.assertEqual(self.audit(job.id, 'executed'), 1)

    def test_duplicate_messages_execute_the_job_once(self):
        job = self.submit(self.make_world(), 'ok')
        for _ in range(3):                                                             # 같은 메시지를 중복 배송
            ai_dispatch.celery.send_task('ai.generate_job', args=[str(job.id)], queue='ai_generate')
        ai_dispatch.dispatch_once()                                                    # outbox의 정상 발행까지 4번째
        self.start_worker()
        self.wait_status(job.id, 'succeeded')
        wait_for(lambda: broker.llen('ai_generate') == 0, 20, message='큐가 비지 않았다')
        time.sleep(1.5)
        self.assertEqual(self.audit(job.id, 'executed'), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_attempts WHERE job_id = :j', j=job.id), 1)


class WorkerFailure(QueueCase):
    def test_killed_worker_is_recovered_and_the_job_finishes_on_another_worker(self):
        job = self.submit(self.make_world(), 'sleep_first')
        self.start_dispatcher()
        first = self.start_worker()
        wait_for(lambda: self.audit(job.id, 'sleeping') or None, 60, message=self.worker_output())
        self.kill(first)                                                               # 처리 중에 worker가 사라짐
        self.wait_status(job.id, ('retry_wait', 'queued'), timeout=30)                 # lease 만료 후 maintenance가 복구
        self.assertEqual(self.scalar("SELECT status FROM ai_attempts WHERE job_id = :j AND attempt_no = 1", j=job.id), 'failed')
        self.start_worker()
        self.wait_status(job.id, 'succeeded')
        attempts = self.db.execute(text("SELECT attempt_no, status, error_code FROM ai_attempts WHERE job_id = :j ORDER BY 1"),
                                   {'j': job.id}).all()
        self.assertEqual([tuple(a) for a in attempts], [(1, 'failed', 'LEASE_EXPIRED'), (2, 'succeeded', None)])

    def test_worker_that_died_during_a_provider_call_is_not_rerun(self):
        job = self.submit(self.make_world(), 'reserve_sleep')
        self.start_dispatcher()
        first = self.start_worker()
        wait_for(lambda: self.audit(job.id, 'sleeping') or None, 60, message=self.worker_output())
        self.kill(first)
        self.wait_status(job.id, 'failed', timeout=30)
        self.db.rollback()
        self.assertEqual(self.scalar('SELECT error_code FROM ai_jobs WHERE id = :j', j=job.id), 'OUTCOME_UNCERTAIN')
        self.assertEqual(self.scalar("SELECT status FROM ai_attempts WHERE job_id = :j", j=job.id), 'uncertain')
        self.assertEqual(self.scalar("SELECT count(*) FROM usage_reservations WHERE status = 'uncertain'"), 2)
        self.start_worker()
        time.sleep(3)
        self.assertEqual(self.audit(job.id, 'executed'), 1)                            # 새 호출로 이어가지 않았다
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_attempts WHERE job_id = :j', j=job.id), 1)


class Cancel(QueueCase):
    def test_cancel_during_execution_is_never_overwritten_by_success(self):
        world = self.make_world()
        job = self.submit(world, 'wait_cancel')
        self.start_dispatcher()
        self.start_worker()
        wait_for(lambda: self.audit(job.id, 'waiting') or None, 60, message=self.worker_output())
        self.assertEqual(job_service.request_cancel(self.db, job.id, world[0]), 'cancel_requested')
        self.wait_status(job.id, 'cancelled', timeout=30)
        self.db.rollback()
        self.assertEqual(self.scalar("SELECT status FROM ai_attempts WHERE job_id = :j", j=job.id), 'cancelled')
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_results'), 0)
        time.sleep(1)
        self.assertEqual(self.status(job.id), 'cancelled')


if __name__ == '__main__':
    unittest.main(verbosity=2)
