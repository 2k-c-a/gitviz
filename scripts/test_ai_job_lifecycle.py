"""S4 모델 없는 AI 작업 처리 테스트: 생성·멱등성·outbox·처리권·취소·복구·예산.

GITVIZ_TEST_DATABASE_URL(gitviz_test로 시작하는 PostgreSQL)이 필요하다. 시간은 now 인자로 제어한다.
"""
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402

URL = support.require_test_database()
sys.path.insert(0, str(support.ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import ai_credential_support as credential_support  # noqa: E402
from app.schemas.ai import ErrorCode  # noqa: E402
from app.services.ai import idempotency, job_runner, job_service, outbox, usage_service  # noqa: E402
from app.services.ai.access import AIError  # noqa: E402
from app.services.ai.lease import CancelRequested, LeaseLost  # noqa: E402

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
H = lambda c: c * 64  # noqa: E731
ROUTE = 'POST /api/ai/v1/jobs'
engine = create_engine(URL, pool_size=30, max_overflow=0)
Session = sessionmaker(engine, expire_on_commit=False)
TRUNCATE = ('usage_reservations, usage_budgets, audit_events, request_idempotency, task_outbox, ai_attempts, '
            'finding_feedback, ai_findings, ai_results, ai_input_files, ai_jobs, ai_inputs, pr_revisions')


def setUpModule():
    command.upgrade(Config(str(support.ROOT / 'alembic.ini')), 'head')


def env(**values):
    return patch.dict(os.environ, {k: str(v) for k, v in values.items()})


class Case(unittest.TestCase):
    def setUp(self):
        self.db = Session()
        self.users, self.repos = [], []
        # 이 파일의 예산 테스트는 서버 공유 예산이 켜진 구성(사용자 + 서버 두 예산)을 전제로 한다. 기본값은 꺼짐이다.
        self._budget_env = patch.dict(os.environ, {'AI_SERVER_MONTHLY_BUDGET': '100'})
        self._budget_env.start()

    def tearDown(self):
        self._budget_env.stop()
        self.db.rollback()
        self.db.close()
        with Session() as db:
            db.execute(text(f'TRUNCATE {TRUNCATE} CASCADE'))
            for uid in self.users:
                db.execute(text('DELETE FROM users WHERE id = :i'), {'i': uid})
            for rid in self.repos:
                db.execute(text('DELETE FROM repositories WHERE id = :i'), {'i': rid})
            db.commit()

    # ── 데이터 준비 ──
    def make_world(self, input_status='ready', valid_until=None):
        owner = uuid4()
        self.db.execute(text("INSERT INTO users(id, github_id, github_login) VALUES(:i, :g, 'u')"),
                        {'i': owner, 'g': uuid4().int % 10**12})
        repo = self.db.execute(text("INSERT INTO repositories(url, name) VALUES(:u, 'r') RETURNING id"),
                               {'u': 'https://github.com/t/' + uuid4().hex[:8]}).scalar()
        self.users.append(owner)
        self.repos.append(repo)
        rev = self.db.execute(text("""INSERT INTO pr_revisions(owner_user_id, repository_id, pr_number, github_pr_id,
            base_sha, head_sha, merge_base_sha, revision_fingerprint, state_at_capture)
            VALUES(:o, :r, 1, 1, :s, :s, :s, :f, 'open') RETURNING id"""),
                               {'o': owner, 'r': repo, 's': 'a' * 40, 'f': uuid4().hex * 2}).scalar()
        inp = self.new_input(owner, repo, rev, input_status, valid_until)
        self.db.commit()
        return owner, repo, rev, inp

    def new_input(self, owner, repo, rev, status='ready', valid_until=None):
        ready = status == 'ready'
        return self.db.execute(text("""INSERT INTO ai_inputs(owner_user_id, repository_id, revision_id, status,
            coverage, expires_at, policy_version, manifest, manifest_hash, input_hash, finalized_at, valid_until)
            VALUES(:o, :r, :v, :s, '{}', '2099-01-01', 'code-transfer-v1',
                   CASE WHEN :ready THEN '{}'::jsonb END, CASE WHEN :ready THEN :h END, CASE WHEN :ready THEN :h END,
                   CASE WHEN :ready THEN now() END, CASE WHEN :ready THEN :vu END) RETURNING id"""),
                               {'o': owner, 'r': repo, 'v': rev, 's': status, 'ready': ready, 'h': H('a'),
                                'vu': valid_until or datetime(2099, 1, 1, tzinfo=timezone.utc)}).scalar()

    def pinned(self, world, **kw):
        owner, repo, _, inp = world
        return credential_support.pinned_for(self.db, owner, repo, inp, **kw)

    def create(self, world, *, key=None, kind='review', fingerprint=None, body=None, **kw):
        owner, repo, _, inp = world
        return job_service.create_job(
            self.db, owner_id=owner, repository_id=repo, input_id=inp, kind=kind, fingerprint=fingerprint or H('1'),
            route=ROUTE, idempotency_key=key if key is not None else uuid4().hex,
            body_hash=idempotency.request_hash(body or {'input': str(inp), 'kind': kind}),
            pinned=None if kind == 'prepare' else kw.pop('pinned', None) or self.pinned(world),
            now=kw.pop('now', T0), **kw)

    def job_row(self, job_id):
        return self.db.execute(text("SELECT * FROM ai_jobs WHERE id = :i"), {'i': job_id}).mappings().one()

    def scalar(self, sql, **params):
        return self.db.execute(text(sql), params).scalar()

    def code_of(self, fn, *args, **kw):
        with self.assertRaises(AIError) as ctx:
            fn(*args, **kw)
        return ctx.exception.code

    def claimed(self, world=None, **kw):
        world = world or self.make_world()
        job = self.create(world, **kw).job
        return world, job, job_service.claim_job(self.db, job.id, now=T0)


class Creation(Case):
    def test_job_key_and_outbox_are_saved_together(self):
        world = self.make_world()
        result = self.create(world, key='k1')
        self.assertTrue(result.created)
        job = self.job_row(result.job.id)
        self.assertEqual((job['status'], job['generation'], job['attempt_count']), ('queued', 0, 0))
        rows = self.db.execute(text("SELECT event_type, dispatch_generation, published_at FROM task_outbox WHERE job_id = :j"),
                               {'j': job['id']}).all()
        self.assertEqual([tuple(r) for r in rows], [('generate', 0, None)])
        idem = self.db.execute(text("SELECT resource_id, status FROM request_idempotency WHERE key = 'k1'")).one()
        self.assertEqual((idem.resource_id, idem.status), (job['id'], 'completed'))
        prepare_world = self.make_world(input_status='building')
        prepared = self.create(prepare_world, kind='prepare', fingerprint=H('2'))
        self.assertEqual(self.scalar("SELECT event_type FROM task_outbox WHERE job_id = :j", j=prepared.job.id), 'prepare')

    def test_same_key_same_request_returns_same_job_and_different_request_conflicts(self):
        world = self.make_world()
        first = self.create(world, key='same')
        again = self.create(world, key='same')
        self.assertEqual((again.created, again.reused, again.job.id), (False, 'idempotency', first.job.id))
        self.assertEqual(self.scalar('SELECT count(*) FROM task_outbox'), 1)
        code = self.code_of(self.create, world, key='same', body={'input': 'other'}, fingerprint=H('9'))
        self.assertEqual(code, ErrorCode.IDEMPOTENCY_CONFLICT)
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_jobs'), 1)

    def test_key_validation_and_expiry(self):
        world = self.make_world()
        for bad in ('', 'has space', 'x' * 256, '한글'):
            with self.subTest(key=bad):
                self.assertEqual(self.code_of(self.create, world, key=bad), ErrorCode.INVALID_STATE)
        first = self.create(world, key='old', now=T0)
        later = self.create(world, key='old', body={'input': 'changed'}, now=T0 + timedelta(hours=25))
        self.assertEqual(later.job.id, first.job.id)      # 만료된 key는 새 요청이고, 같은 fingerprint의 진행 중 job을 재사용
        self.assertEqual(later.reused, 'fingerprint')
        self.assertEqual(self.scalar('SELECT count(*) FROM request_idempotency'), 1)

    def test_fingerprint_reuse_and_generations(self):
        world = self.make_world()
        first = self.create(world).job
        # 진행 중: 새 key라도 같은 job
        self.assertEqual((self.create(world).job.id, self.create(world).reused), (first.id, 'fingerprint'))
        lease = job_service.claim_job(self.db, first.id, now=T0)
        job_service.finish_job(self.db, lease, 'succeeded', now=T0)
        # 성공: 재사용. 강제 재생성: 새 generation
        self.assertEqual(self.create(world).job.id, first.id)
        regenerated = self.create(world, force_regenerate=True)
        self.assertTrue(regenerated.created)
        self.assertEqual(regenerated.job.generation, 1)
        # 진행 중인 최신 generation이 있으면 강제 재생성도 그것을 재사용
        self.assertEqual(self.create(world, force_regenerate=True).job.id, regenerated.job.id)
        # 실패·취소된 최신은 새 generation
        lease = job_service.claim_job(self.db, regenerated.job.id, now=T0)
        job_service.fail_job(self.db, lease, 'BOOM', retryable=False, now=T0)
        second = self.create(world)
        self.assertEqual(second.job.generation, 2)
        job_service.request_cancel(self.db, second.job.id, world[0], now=T0)
        self.assertEqual(self.create(world).job.generation, 3)
        # kind가 다르면 별개
        self.assertTrue(self.create(world, kind='summary').created)

    def test_same_key_concurrent_requests_create_one_job(self):
        world = self.make_world()
        pinned = self.pinned(world)
        results, errors = [], []

        def run():
            with Session() as db:
                try:
                    results.append(job_service.create_job(
                        db, owner_id=world[0], repository_id=world[1], input_id=world[3], kind='review',
                        fingerprint=H('1'), route=ROUTE, idempotency_key='race',
                        body_hash=idempotency.request_hash({'a': 1}), pinned=pinned, now=T0))
                except Exception as exc:     # pragma: no cover
                    errors.append(exc)
        threads = [threading.Thread(target=run) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(len({r.job.id for r in results}), 1)
        self.assertEqual(sum(r.created for r in results), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_jobs'), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM task_outbox'), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM request_idempotency'), 1)

    def test_same_fingerprint_different_keys_concurrent_requests_create_one_job(self):
        world = self.make_world()
        pinned = self.pinned(world)
        results = []

        def run(i):
            with Session() as db:
                results.append(job_service.create_job(
                    db, owner_id=world[0], repository_id=world[1], input_id=world[3], kind='review',
                    fingerprint=H('1'), route=ROUTE, idempotency_key=f'key-{i}',
                    body_hash=idempotency.request_hash({'i': i}), pinned=pinned, now=T0))
        threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sum(r.created for r in results), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_jobs'), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM request_idempotency'), 8)

    def test_daily_limit_counts_generation_jobs_and_cannot_be_bypassed_concurrently(self):
        with env(AI_DAILY_JOB_LIMIT=2):
            world = self.make_world()
            self.create(world, fingerprint=H('1'))
            self.create(world, fingerprint=H('2'))
            self.assertEqual(self.code_of(self.create, world, fingerprint=H('3')), ErrorCode.USER_LIMIT)
            self.assertEqual(self.create(world, fingerprint=H('1')).reused, 'fingerprint')   # 재사용은 한도와 무관
            prepare_world = self.make_world(input_status='building')
            self.assertTrue(self.create(prepare_world, kind='prepare', fingerprint=H('4')).created)
            self.assertEqual(self.scalar('SELECT count(*) FROM request_idempotency'), 4)   # 거부된 요청의 key는 남지 않는다
            # 다른 날은 새로 센다
            self.assertTrue(self.create(world, fingerprint=H('5'), now=T0 + timedelta(days=1)).created)

        with env(AI_DAILY_JOB_LIMIT=2):
            racer = self.make_world()
            racer_pinned = self.pinned(racer)
            outcomes = []

            def run(i):
                with Session() as db:
                    try:
                        job_service.create_job(
                            db, owner_id=racer[0], repository_id=racer[1], input_id=racer[3], kind='review',
                            fingerprint=H(format(i, 'x')), route=ROUTE, idempotency_key=f'r{i}',
                            body_hash=idempotency.request_hash({'i': i}), pinned=racer_pinned, now=T0)
                        outcomes.append('ok')
                    except AIError as exc:
                        outcomes.append(exc.code)
            threads = [threading.Thread(target=run, args=(i,)) for i in range(6)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            self.assertEqual(outcomes.count('ok'), 2, outcomes)
            self.assertEqual(outcomes.count(ErrorCode.USER_LIMIT), 4)

    def test_input_validation_and_failed_creation_does_not_consume_the_key(self):
        world = self.make_world()
        other = self.make_world()
        owner, repo, _, inp = world
        self.assertEqual(self.code_of(job_service.create_job, self.db, owner_id=other[0], repository_id=repo,
                                      input_id=inp, kind='review', fingerprint=H('1'), route=ROUTE,
                                      idempotency_key='x', body_hash=H('a'), now=T0), ErrorCode.RESOURCE_NOT_FOUND)
        expired = self.make_world(valid_until=T0 - timedelta(seconds=1))
        self.assertEqual(self.code_of(self.create, expired, key='e'), ErrorCode.INPUT_EXPIRED)
        building = self.make_world(input_status='building')
        self.assertEqual(self.code_of(self.create, building, key='b'), ErrorCode.INVALID_STATE)
        self.assertEqual(self.code_of(self.create, world, kind='prepare', key='p', fingerprint=H('7')),
                         ErrorCode.INVALID_STATE)
        self.assertEqual(self.scalar('SELECT count(*) FROM request_idempotency'), 0)
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_jobs'), 0)
        self.assertEqual(self.scalar('SELECT count(*) FROM task_outbox'), 0)
        self.assertTrue(self.create(building, key='b', kind='prepare', fingerprint=H('8')).created)


class Leases(Case):
    def test_claim_gives_one_lease_and_records_attempt(self):
        world, job, lease = self.claimed()
        self.assertEqual((lease.attempt_no, lease.owner_user_id, lease.kind), (1, world[0], 'review'))
        row = self.job_row(job.id)
        self.assertEqual((row['status'], row['attempt_count'], row['lease_token']), ('running', 1, lease.token))
        self.assertEqual(row['lease_until'], T0 + timedelta(seconds=120))
        attempt = self.db.execute(text("SELECT attempt_no, status, lease_token FROM ai_attempts WHERE job_id = :j"),
                                  {'j': job.id}).one()
        self.assertEqual(tuple(attempt), (1, 'started', lease.token))
        self.assertIsNone(job_service.claim_job(self.db, job.id, now=T0))      # 중복 메시지

    def test_concurrent_claims_give_exactly_one_lease(self):
        world = self.make_world()
        job = self.create(world).job
        leases = []

        def run():
            with Session() as db:
                leases.append(job_service.claim_job(db, job.id, now=T0))
        threads = [threading.Thread(target=run) for _ in range(10)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len([l for l in leases if l is not None]), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_attempts'), 1)
        self.assertEqual(self.scalar('SELECT attempt_count FROM ai_jobs WHERE id = :j', j=job.id), 1)

    def test_claim_conditions(self):
        world = self.make_world()
        job = self.create(world).job
        self.db.execute(text("UPDATE ai_jobs SET next_attempt_at = :t WHERE id = :j"), {'t': T0 + timedelta(minutes=5), 'j': job.id})
        self.db.commit()
        self.assertIsNone(job_service.claim_job(self.db, job.id, now=T0))                   # 아직 때가 아님
        self.assertIsNotNone(job_service.claim_job(self.db, job.id, now=T0 + timedelta(minutes=5)))
        queued = self.create(self.make_world(), fingerprint=H('3')).job
        self.db.execute(text("UPDATE ai_jobs SET deleted_at = now() WHERE id = :j"), {'j': queued.id})
        self.db.commit()
        self.assertIsNone(job_service.claim_job(self.db, queued.id, now=T0))                 # 삭제됨
        self.assertIsNone(job_service.claim_job(self.db, uuid4(), now=T0))                   # 없음

    def test_heartbeat_extends_only_for_the_lease_holder(self):
        world, job, lease = self.claimed()
        result = job_service.heartbeat(self.db, lease, now=T0 + timedelta(seconds=60))
        self.assertEqual((result.alive, result.cancel_requested), (True, False))
        self.assertEqual(self.job_row(job.id)['lease_until'], T0 + timedelta(seconds=180))
        stranger = type(lease)(**{**lease.__dict__, 'token': uuid4()})
        self.assertFalse(job_service.heartbeat(self.db, stranger, now=T0).alive)
        job_service.finish_job(self.db, lease, 'succeeded', now=T0)
        self.assertFalse(job_service.heartbeat(self.db, lease, now=T0).alive)

    def test_finish_runs_work_in_the_same_transaction(self):
        world, job, lease = self.claimed()

        def failing(db):
            db.execute(text("INSERT INTO audit_events(action, object_type, request_id) VALUES('x', 'job', 'r')"))
            raise RuntimeError('storage down')
        with self.assertRaises(RuntimeError):
            job_service.finish_job(self.db, lease, 'succeeded', work=failing, now=T0)
        self.assertEqual(self.job_row(job.id)['status'], 'running')                      # 상태는 확정되지 않았다
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_events WHERE action = 'x'"), 0)   # 결과 저장도 되돌려졌다

        def storing(db):
            db.execute(text("INSERT INTO audit_events(action, object_type, request_id) VALUES('stored', 'job', 'r')"))
        self.assertEqual(job_service.finish_job(self.db, lease, 'succeeded', work=storing, now=T0), 'succeeded')
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_events WHERE action = 'stored'"), 1)
        row = self.job_row(job.id)
        self.assertEqual((row['status'], row['lease_token'], row['finished_at']), ('succeeded', None, T0))
        self.assertEqual(self.scalar("SELECT status FROM ai_attempts WHERE job_id = :j", j=job.id), 'succeeded')
        with self.assertRaises(LeaseLost):                                                 # 두 번 확정 불가
            job_service.finish_job(self.db, lease, 'succeeded', now=T0)

    def test_failed_outcome_requires_code_and_is_recorded(self):
        world, job, lease = self.claimed()
        with self.assertRaises(ValueError):
            job_service.finish_job(self.db, lease, 'failed')
        self.assertEqual(job_service.finish_job(self.db, lease, 'failed', error_code='PROVIDER_REJECTED', now=T0), 'failed')
        self.assertEqual(self.job_row(job.id)['error_code'], 'PROVIDER_REJECTED')

    def test_retryable_failure_waits_then_requeues_through_the_outbox(self):
        world, job, lease = self.claimed()
        self.assertEqual(job_service.fail_job(self.db, lease, 'UPSTREAM', retryable=True, now=T0), 'retry_wait')
        row = self.job_row(job.id)
        self.assertEqual((row['status'], row['next_attempt_at'], row['lease_token']), ('retry_wait', T0 + timedelta(seconds=30), None))
        self.assertEqual(self.scalar("SELECT status FROM ai_attempts WHERE job_id = :j AND attempt_no = 1", j=job.id), 'failed')
        events = self.db.execute(text("SELECT dispatch_generation, available_at FROM task_outbox WHERE job_id = :j ORDER BY 1"),
                                 {'j': job.id}).all()
        self.assertEqual([(e[0], e[1]) for e in events], [(0, events[0][1]), (1, T0 + timedelta(seconds=30))])
        published = []
        outbox.dispatch_pending(self.db, published.append, now=T0 + timedelta(seconds=10))      # 아직 도래 전
        self.assertEqual(self.job_row(job.id)['status'], 'retry_wait')
        outbox.dispatch_pending(self.db, published.append, now=T0 + timedelta(seconds=31))
        self.assertEqual(self.job_row(job.id)['status'], 'queued')
        self.assertTrue(any(e.dispatch_generation == 1 for e in published))
        lease2 = job_service.claim_job(self.db, job.id, now=T0 + timedelta(seconds=31))
        self.assertEqual(lease2.attempt_no, 2)
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_attempts WHERE job_id = :j', j=job.id), 2)

    def test_retry_limit(self):
        with env(AI_MAX_ATTEMPTS=2):
            world, job, lease = self.claimed()
            self.assertEqual(job_service.fail_job(self.db, lease, 'E', retryable=True, now=T0), 'retry_wait')
            self.db.execute(text("UPDATE ai_jobs SET status = 'queued' WHERE id = :j"), {'j': job.id})
            self.db.commit()
            lease2 = job_service.claim_job(self.db, job.id, now=T0 + timedelta(hours=1))
            self.assertEqual(lease2.attempt_no, 2)
            self.assertEqual(job_service.fail_job(self.db, lease2, 'E', retryable=True, now=T0), 'failed')   # 한도 도달
            self.assertEqual(self.job_row(job.id)['error_code'], 'E')
            # 이미 한도를 넘긴 queued 작업은 claim 시 닫힌다
            other = self.create(self.make_world(), fingerprint=H('5')).job
            self.db.execute(text("UPDATE ai_jobs SET attempt_count = 2 WHERE id = :j"), {'j': other.id})
            self.db.commit()
            self.assertIsNone(job_service.claim_job(self.db, other.id, now=T0))
            self.assertEqual(self.job_row(other.id)['error_code'], 'ATTEMPTS_EXHAUSTED')


class Cancellation(Case):
    def test_cancel_queued_job_immediately(self):
        world = self.make_world()
        job = self.create(world).job
        self.assertEqual(job_service.request_cancel(self.db, job.id, world[0], now=T0), 'cancelled')
        row = self.job_row(job.id)
        self.assertEqual((row['status'], row['finished_at']), ('cancelled', T0))
        self.assertIsNone(job_service.claim_job(self.db, job.id, now=T0))
        published = []
        outbox.dispatch_pending(self.db, published.append, now=T0)
        self.assertEqual(published, [])                                         # 취소된 작업은 발행하지 않는다
        self.assertIsNotNone(self.scalar('SELECT published_at FROM task_outbox'))
        self.assertEqual(job_service.request_cancel(self.db, job.id, world[0], now=T0), 'cancelled')   # 멱등

    def test_cancel_running_job_is_confirmed_by_lease_holder_and_success_is_not_recorded(self):
        world, job, lease = self.claimed()
        self.assertEqual(job_service.request_cancel(self.db, job.id, world[0], now=T0), 'cancel_requested')
        self.assertTrue(job_service.heartbeat(self.db, lease, now=T0).cancel_requested)
        saved = []
        final = job_service.finish_job(self.db, lease, 'succeeded', work=lambda db: saved.append(1), now=T0)
        self.assertEqual((final, saved), ('cancelled', []))                      # 취소 뒤 성공으로 덮어쓰지 않는다
        self.assertEqual(self.scalar("SELECT status FROM ai_attempts WHERE job_id = :j", j=job.id), 'cancelled')

    def test_cancel_ownership_and_terminal_state(self):
        world, job, lease = self.claimed()
        stranger = self.make_world()
        self.assertEqual(self.code_of(job_service.request_cancel, self.db, job.id, stranger[0]), ErrorCode.RESOURCE_NOT_FOUND)
        self.assertEqual(self.code_of(job_service.request_cancel, self.db, uuid4(), world[0]), ErrorCode.RESOURCE_NOT_FOUND)
        job_service.finish_job(self.db, lease, 'succeeded', now=T0)
        self.assertEqual(job_service.request_cancel(self.db, job.id, world[0], now=T0), 'succeeded')   # 종료는 그대로

    def test_cancelled_retry_wait_job_is_not_requeued(self):
        world, job, lease = self.claimed()
        job_service.fail_job(self.db, lease, 'E', retryable=True, now=T0)
        self.assertEqual(job_service.request_cancel(self.db, job.id, world[0], now=T0), 'cancelled')
        published = []
        outbox.dispatch_pending(self.db, published.append, now=T0 + timedelta(hours=1))
        self.assertEqual(published, [])
        self.assertEqual(self.job_row(job.id)['status'], 'cancelled')

    def test_cancel_and_finish_race_is_consistent(self):
        world = self.make_world()
        for i in range(25):
            job = self.create(world, fingerprint=format(i + 1, '064x'), key=f'race-{i}').job
            lease = job_service.claim_job(self.db, job.id, now=T0)
            barrier = threading.Barrier(2)
            seen = {}

            def cancel():
                with Session() as db:
                    barrier.wait()
                    seen['cancel'] = job_service.request_cancel(db, job.id, world[0], now=T0)

            def finish():
                with Session() as db:
                    barrier.wait()
                    seen['finish'] = job_service.finish_job(db, lease, 'succeeded', now=T0)
            threads = [threading.Thread(target=cancel), threading.Thread(target=finish)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            final = self.job_row(job.id)['status']
            self.assertEqual(final, seen['finish'])
            # 취소가 접수된 뒤라면 반드시 cancelled, 성공이 먼저 확정됐다면 취소는 그 결과를 돌려준다.
            self.assertEqual(seen['cancel'], 'cancel_requested' if final == 'cancelled' else 'succeeded', (i, seen))
            self.assertIn(final, ('succeeded', 'cancelled'))


class Recovery(Case):
    def expire(self, job_id):
        return T0 + timedelta(seconds=job_service.get_ai_settings().lease_seconds + 1)

    def test_expired_lease_before_provider_call_is_retried_and_stale_worker_is_fenced(self):
        world, job, lease = self.claimed()
        self.assertEqual(job_service.recover_expired(self.db, now=T0 + timedelta(seconds=60))['retried'], 0)   # 유효
        stats = job_service.recover_expired(self.db, now=self.expire(job.id))
        self.assertEqual(stats['retried'], 1)
        row = self.job_row(job.id)
        self.assertEqual((row['status'], row['error_code'], row['lease_token']), ('retry_wait', 'LEASE_EXPIRED', None))
        self.assertEqual(self.scalar("SELECT status FROM ai_attempts WHERE job_id = :j", j=job.id), 'failed')
        self.assertEqual(self.scalar('SELECT count(*) FROM task_outbox WHERE job_id = :j', j=job.id), 2)
        # 오래된 처리자는 더 이상 확정할 수 없다
        with self.assertRaises(LeaseLost):
            job_service.finish_job(self.db, lease, 'succeeded', now=self.expire(job.id))
        with self.assertRaises(LeaseLost):
            job_service.fail_job(self.db, lease, 'X', now=self.expire(job.id))
        self.assertFalse(job_service.heartbeat(self.db, lease, now=self.expire(job.id)).alive)
        # 새 처리자가 이어받는다
        outbox.dispatch_pending(self.db, lambda e: None, now=self.expire(job.id))
        lease2 = job_service.claim_job(self.db, job.id, now=self.expire(job.id))
        self.assertEqual(lease2.attempt_no, 2)
        with self.assertRaises(LeaseLost):
            job_service.finish_job(self.db, lease, 'succeeded', now=self.expire(job.id))     # 여전히 옛 처리자는 불가
        self.assertEqual(job_service.finish_job(self.db, lease2, 'succeeded', now=self.expire(job.id)), 'succeeded')

    def test_recovery_exhausts_attempts(self):
        with env(AI_MAX_ATTEMPTS=1):
            world, job, lease = self.claimed()
            self.assertEqual(job_service.recover_expired(self.db, now=self.expire(job.id))['exhausted'], 1)
            self.assertEqual(self.job_row(job.id)['error_code'], 'ATTEMPTS_EXHAUSTED')

    def test_attempt_that_reserved_a_provider_call_is_never_resumed_with_a_new_call(self):
        world, job, lease = self.claimed()
        usage_service.reserve_attempt(self.db, lease, Decimal('1.5'), now=T0)
        self.db.commit()
        stats = job_service.recover_expired(self.db, now=self.expire(job.id))
        self.assertEqual((stats['uncertain'], stats['retried']), (1, 0))
        row = self.job_row(job.id)
        self.assertEqual((row['status'], row['error_code']), ('failed', 'OUTCOME_UNCERTAIN'))
        attempt = self.db.execute(text("SELECT status, outcome_uncertain FROM ai_attempts WHERE job_id = :j"), {'j': job.id}).one()
        self.assertEqual(tuple(attempt), ('uncertain', True))
        self.assertEqual(set(self.db.execute(text('SELECT status FROM usage_reservations')).scalars()), {'uncertain'})
        budgets = self.db.execute(text('SELECT reserved_amount, spent_amount FROM usage_budgets')).all()
        self.assertTrue(all(b[0] == Decimal('1.5') and b[1] == 0 for b in budgets))         # 예약을 해제하지 않는다
        outbox.dispatch_pending(self.db, lambda e: self.fail('재호출 금지'), now=self.expire(job.id))
        self.assertIsNone(job_service.claim_job(self.db, job.id, now=self.expire(job.id)))

    def test_cancel_requested_job_with_dead_worker_is_cancelled(self):
        world, job, lease = self.claimed()
        job_service.request_cancel(self.db, job.id, world[0], now=T0)
        self.assertEqual(job_service.recover_expired(self.db, now=self.expire(job.id))['cancelled'], 1)
        self.assertEqual(self.job_row(job.id)['status'], 'cancelled')

    def test_queue_deadline(self):
        world = self.make_world()
        old = self.create(world, fingerprint=H('1'), now=T0).job
        fresh = self.create(world, fingerprint=H('2'), now=T0 + timedelta(minutes=29)).job
        self.assertEqual(job_service.fail_queue_deadline(self.db, now=T0 + timedelta(minutes=31)), 1)
        self.assertEqual(self.job_row(old.id)['error_code'], 'QUEUE_TIMEOUT')
        self.assertEqual(self.job_row(fresh.id)['status'], 'queued')

    def test_maintenance_purges_expired_keys(self):
        world = self.make_world()
        self.create(world, key='k', now=T0)
        stats = job_service.run_maintenance(self.db, now=T0 + timedelta(hours=25))
        self.assertEqual(stats['idempotency_purged'], 1)


class Outbox(Case):
    def test_publish_failure_keeps_the_row_and_retries_with_backoff(self):
        world = self.make_world()
        job = self.create(world).job

        def broken(event):
            raise ConnectionError('redis down')
        stats = outbox.dispatch_pending(self.db, broken, now=T0)
        self.assertEqual(stats, {'published': 0, 'skipped': 0, 'failed': 1})
        row = self.db.execute(text('SELECT retry_count, published_at, available_at FROM task_outbox')).one()
        self.assertEqual((row.retry_count, row.published_at), (1, None))
        self.assertGreater(row.available_at, T0)
        self.assertEqual(self.job_row(job.id)['status'], 'queued')                 # job은 그대로 대기
        published = []
        self.assertEqual(outbox.dispatch_pending(self.db, published.append, now=T0 + timedelta(seconds=1))['published'], 0)
        stats = outbox.dispatch_pending(self.db, published.append, now=T0 + timedelta(minutes=10))
        self.assertEqual((stats['published'], len(published), published[0].job_id), (1, 1, job.id))
        self.assertEqual(published[0].event_type, 'generate')
        self.assertEqual(outbox.dispatch_pending(self.db, published.append, now=T0 + timedelta(hours=1))['published'], 0)

    def test_retry_requeue_is_committed_before_the_message_is_published(self):
        """worker가 메시지를 받는 시점에 작업이 이미 queued여야 한다(아니면 claim이 건너뛰어 작업이 유실된다)."""
        world, job, lease = self.claimed()
        job_service.fail_job(self.db, lease, 'E', retryable=True, now=T0)
        seen = []

        def publish(event):
            with Session() as other:                       # worker와 같은 시점의 다른 connection
                seen.append(other.execute(text('SELECT status FROM ai_jobs WHERE id = :j'), {'j': event.job_id}).scalar())
                claimed = job_service.claim_job(other, event.job_id, now=T0 + timedelta(seconds=31))
                seen.append(claimed is not None)
        outbox.dispatch_pending(self.db, publish, now=T0 + timedelta(seconds=31))
        self.assertEqual(seen[-2:], ['queued', True])

    def test_failure_of_one_event_does_not_block_others(self):
        jobs = [self.create(self.make_world(), fingerprint=H(str(i))).job for i in range(3)]
        seen = []

        def flaky(event):
            if event.job_id == jobs[1].id:
                raise ConnectionError()
            seen.append(event.job_id)
        stats = outbox.dispatch_pending(self.db, flaky, now=T0 + timedelta(seconds=1))
        self.assertEqual((stats['published'], stats['failed']), (2, 1))

    def test_concurrent_dispatchers_never_publish_the_same_event_twice(self):
        for i in range(12):
            self.create(self.make_world(), fingerprint=H(format(i, 'x')))
        counts, lock = {}, threading.Lock()

        def publish(event):
            with lock:
                counts[event.id] = counts.get(event.id, 0) + 1

        def run():
            with Session() as db:
                outbox.dispatch_pending(db, publish, limit=5, now=T0 + timedelta(seconds=1))
        threads = [threading.Thread(target=run) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(counts), 12)
        self.assertEqual(set(counts.values()), {1})

    def test_duplicate_delivery_runs_the_job_once(self):
        world = self.make_world()
        job = self.create(world).job
        runs = []
        job_runner.register_handler('review', lambda ctx: runs.append(ctx.lease.attempt_no))
        try:
            results = []
            threads = [threading.Thread(target=lambda: results.append(job_runner.run_job(job.id, session_factory=Session)))
                       for _ in range(5)]
            [t.start() for t in threads]
            [t.join() for t in threads]
        finally:
            job_runner.HANDLERS.pop('review', None)
        self.assertEqual(runs, [1])
        self.assertEqual(sorted(results), ['skipped'] * 4 + ['succeeded'])


class Runner(Case):
    def run_with(self, handler, **kw):
        world = self.make_world()
        job = self.create(world, **kw).job
        job_runner.register_handler('review', handler)
        try:
            outcome = job_runner.run_job(job.id, session_factory=Session)
        finally:
            job_runner.HANDLERS.pop('review', None)
        return world, job, outcome

    def test_outcomes(self):
        world, job, outcome = self.run_with(lambda ctx: None)
        self.assertEqual((outcome, self.job_row(job.id)['status']), ('succeeded', 'succeeded'))
        _, job, outcome = self.run_with(lambda ctx: (_ for _ in ()).throw(job_runner.JobFailed('BAD_INPUT')))
        self.assertEqual((outcome, self.job_row(job.id)['error_code']), ('failed', 'BAD_INPUT'))
        _, job, outcome = self.run_with(lambda ctx: (_ for _ in ()).throw(job_runner.RetryableError('TEMP')))
        self.assertEqual((outcome, self.job_row(job.id)['status']), ('retry', 'retry_wait'))
        _, job, outcome = self.run_with(lambda ctx: 1 / 0)
        self.assertEqual((outcome, self.job_row(job.id)['error_code']), ('failed', 'INTERNAL_ERROR'))

    def test_missing_handler_fails_without_retry(self):
        world = self.make_world()
        job = self.create(world).job
        self.assertEqual(job_runner.run_job(job.id, session_factory=Session), 'failed')
        self.assertEqual(self.job_row(job.id)['error_code'], 'NO_HANDLER')

    def test_result_storage_failure_is_retried_not_lost(self):
        def handler(ctx):
            return job_runner.HandlerResult(work=lambda db: 1 / 0)
        _, job, outcome = self.run_with(handler)
        self.assertEqual((outcome, self.job_row(job.id)['error_code']), ('retry', 'RESULT_STORAGE_FAILED'))

    def test_heartbeat_thread_keeps_a_long_job_alive_and_reports_cancel(self):
        observed = {}

        def handler(ctx):
            import time
            job_service.request_cancel(Session(), ctx.lease.job_id, ctx.lease.owner_user_id)
            time.sleep(1.8)
            observed['cancel'] = ctx.cancel_requested
        with env(AI_LEASE_SECONDS=1):
            _, job, outcome = self.run_with(handler)
        self.assertTrue(observed['cancel'])                       # heartbeat가 취소 접수를 알렸다
        self.assertEqual((outcome, self.job_row(job.id)['status']), ('cancelled', 'cancelled'))

    def test_lease_loss_during_handler_is_reported(self):
        def handler(ctx):
            with Session() as db:      # 다른 프로세스가 복구해 간 상황
                db.execute(text("UPDATE ai_jobs SET lease_until = :t WHERE id = :j"), {'t': T0, 'j': ctx.lease.job_id})
                db.commit()
                job_service.recover_expired(db, now=T0 + timedelta(seconds=1))
        _, job, outcome = self.run_with(handler)
        self.assertEqual(outcome, 'lost')
        self.assertEqual(self.job_row(job.id)['status'], 'retry_wait')


class Budget(Case):
    def budgets(self):
        rows = self.db.execute(text("SELECT scope, reserved_amount, spent_amount FROM usage_budgets ORDER BY scope")).all()
        return {r.scope: (r.reserved_amount, r.spent_amount) for r in rows}

    def test_reserve_settle_release_and_uncertain(self):
        with env(AI_USER_MONTHLY_BUDGET=10, AI_SERVER_MONTHLY_BUDGET=100):
            world, job, lease = self.claimed()
            ids = usage_service.reserve_attempt(self.db, lease, Decimal('2'), now=T0)
            self.db.commit()
            self.assertEqual(len(ids), 2)                                          # 사용자 + 서버
            self.assertEqual(self.budgets(), {'server': (2, 0), 'user': (2, 0)})
            self.assertEqual(self.scalar('SELECT estimated_cost FROM ai_attempts'), Decimal('2'))
            self.assertEqual(usage_service.settle_attempt(self.db, job.id, 1, Decimal('1.25'), input_tokens=100,
                                                          output_tokens=20, provider_reported=True), 2)
            self.db.commit()
            self.assertEqual(self.budgets(), {'server': (0, Decimal('1.25')), 'user': (0, Decimal('1.25'))})
            attempt = self.db.execute(text('SELECT provider_reported_cost, input_tokens, output_tokens FROM ai_attempts')).one()
            self.assertEqual(tuple(attempt), (Decimal('1.25'), 100, 20))
            self.assertEqual(usage_service.settle_attempt(self.db, job.id, 1, Decimal('5')), 0)   # 이미 정산됨
            self.assertEqual(self.budgets()['user'][1], Decimal('1.25'))
            self.assertEqual(self.code_of(usage_service.reserve_attempt, self.db, lease, Decimal('1')), ErrorCode.INVALID_STATE)

    def requeue_after_settled_attempt(self, job, now=T0):
        """정산된 attempt의 작업을 다시 queued로 돌린다. fail_job은 이미 처리된 호출을 재시도하지 않으므로(S7 복구 규칙)
        예산 계산을 보려는 이 테스트는 상태를 직접 되돌린다."""
        self.db.execute(text("UPDATE ai_attempts SET status = 'failed', finished_at = :n WHERE job_id = :j AND status = 'started'"),
                        {'n': now, 'j': job.id})
        self.db.execute(text("""UPDATE ai_jobs SET status = 'queued', lease_token = NULL, lease_until = NULL
                                WHERE id = :j"""), {'j': job.id})
        self.db.commit()

    def test_each_attempt_is_tracked_separately_across_retries(self):
        with env(AI_USER_MONTHLY_BUDGET=10, AI_SERVER_MONTHLY_BUDGET=100):
            world, job, lease = self.claimed()
            usage_service.reserve_attempt(self.db, lease, Decimal('1'), now=T0)
            usage_service.settle_attempt(self.db, job.id, 1, Decimal('0.4'))
            self.db.commit()
            self.requeue_after_settled_attempt(job)                                           # 출력 검증 실패 → 재생성
            lease2 = job_service.claim_job(self.db, job.id, now=T0 + timedelta(hours=1))
            usage_service.reserve_attempt(self.db, lease2, Decimal('1'), now=T0)
            usage_service.settle_attempt(self.db, job.id, 2, Decimal('0.6'))
            self.db.commit()
            self.assertEqual(self.budgets()['user'], (0, Decimal('1.0')))                   # 실패한 호출 비용도 남는다
            self.assertEqual(self.scalar('SELECT count(*) FROM usage_reservations'), 4)

    def test_release_and_uncertain_resolution(self):
        with env(AI_USER_MONTHLY_BUDGET=10, AI_SERVER_MONTHLY_BUDGET=100):
            world, job, lease = self.claimed()
            usage_service.reserve_attempt(self.db, lease, Decimal('3'), now=T0)
            self.assertEqual(usage_service.release_attempt(self.db, job.id, 1), 2)
            self.db.commit()
            self.assertEqual(self.budgets(), {'server': (0, 0), 'user': (0, 0)})
            self.assertEqual(usage_service.release_attempt(self.db, job.id, 1), 0)           # 두 번 해제해도 안전

            world2, job2, lease2 = self.claimed(self.make_world(), fingerprint=H('2'))
            usage_service.reserve_attempt(self.db, lease2, Decimal('4'), now=T0)
            self.assertEqual(usage_service.mark_uncertain(self.db, job2.id, 1), 2)
            self.db.commit()
            self.assertEqual(self.budgets()['user'], (4, 0))                                  # 불명 호출은 예산을 점유
            ids = list(self.db.execute(text("SELECT id FROM usage_reservations WHERE status = 'uncertain' ORDER BY 1")).scalars())
            self.assertTrue(usage_service.resolve_uncertain(self.db, ids[0], Decimal('2.5')))
            self.assertTrue(usage_service.resolve_uncertain(self.db, ids[1]))
            self.assertFalse(usage_service.resolve_uncertain(self.db, ids[1]))
            self.db.commit()
            totals = self.db.execute(text('SELECT sum(reserved_amount), sum(spent_amount) FROM usage_budgets')).one()
            self.assertEqual((totals[0], totals[1]), (0, Decimal('2.5')))

    def test_budget_limits_are_atomic_across_user_and_server(self):
        with env(AI_USER_MONTHLY_BUDGET=5, AI_SERVER_MONTHLY_BUDGET=3):
            world, job, lease = self.claimed()
            self.assertEqual(self.code_of(usage_service.reserve_attempt, self.db, lease, Decimal('4'), now=T0), ErrorCode.BUDGET_LIMIT)
            self.db.rollback()
            self.assertEqual(self.scalar('SELECT count(*) FROM usage_reservations'), 0)       # 부분 예약이 남지 않는다
            self.assertEqual(self.budgets().get('user', (0, 0))[0], 0)
            usage_service.reserve_attempt(self.db, lease, Decimal('3'), now=T0)               # 서버 한도 정확히 소진
            self.db.commit()
            world2, job2, lease2 = self.claimed(self.make_world(), fingerprint=H('2'))
            self.assertEqual(self.code_of(usage_service.reserve_attempt, self.db, lease2, Decimal('0.01'), now=T0),
                             ErrorCode.BUDGET_LIMIT)
            with self.assertRaises(ValueError):
                usage_service.reserve_attempt(self.db, lease2, Decimal('-1'))

    def test_spent_amount_counts_against_the_limit(self):
        with env(AI_USER_MONTHLY_BUDGET=5, AI_SERVER_MONTHLY_BUDGET=100):
            world, job, lease = self.claimed()
            usage_service.reserve_attempt(self.db, lease, Decimal('4'), now=T0)
            usage_service.settle_attempt(self.db, job.id, 1, Decimal('6'))                    # 예약보다 많이 나온 실제 비용
            self.db.commit()
            self.assertEqual(self.budgets()['user'], (0, Decimal('6')))
            self.requeue_after_settled_attempt(job)
            lease2 = job_service.claim_job(self.db, job.id, now=T0 + timedelta(hours=1))
            self.assertEqual(self.code_of(usage_service.reserve_attempt, self.db, lease2, Decimal('0.01'), now=T0),
                             ErrorCode.BUDGET_LIMIT)

    def test_concurrent_reservations_cannot_exceed_the_limit(self):
        with env(AI_USER_MONTHLY_BUDGET=5, AI_SERVER_MONTHLY_BUDGET=100):
            owner_world = self.make_world()
            leases = []
            for i in range(6):
                job = self.create(owner_world, fingerprint=H(format(i + 1, 'x'))).job
                leases.append(job_service.claim_job(self.db, job.id, now=T0))
            outcomes = []

            def run(lease):
                with Session() as db:
                    try:
                        usage_service.reserve_attempt(db, lease, Decimal('2'), now=T0)
                        db.commit()
                        outcomes.append('ok')
                    except AIError as exc:
                        db.rollback()
                        outcomes.append(exc.code)
            threads = [threading.Thread(target=run, args=(l,)) for l in leases]
            [t.start() for t in threads]
            [t.join() for t in threads]
            self.assertEqual(outcomes.count('ok'), 2, outcomes)
            self.assertEqual(outcomes.count(ErrorCode.BUDGET_LIMIT), 4)
            self.assertEqual(self.budgets()['user'][0], Decimal('4'))
            self.assertEqual(self.budgets()['server'][0], Decimal('4'))

    def test_reservation_requires_the_current_lease(self):
        world, job, lease = self.claimed()
        stranger = type(lease)(**{**lease.__dict__, 'token': uuid4()})
        with self.assertRaises(LeaseLost):
            usage_service.reserve_attempt(self.db, stranger, Decimal('1'))
        self.db.rollback()
        job_service.finish_job(self.db, lease, 'succeeded', now=T0)
        with self.assertRaises(LeaseLost):
            usage_service.reserve_attempt(self.db, lease, Decimal('1'))
        self.db.rollback()
        self.assertEqual(self.scalar('SELECT count(*) FROM usage_reservations'), 0)

    def test_anonymized_budget_survives_user_deletion_and_period_rolls_over(self):
        with env(AI_USER_MONTHLY_BUDGET=10, AI_SERVER_MONTHLY_BUDGET=100):
            world, job, lease = self.claimed()
            usage_service.reserve_attempt(self.db, lease, Decimal('1'), now=T0)
            usage_service.settle_attempt(self.db, job.id, 1, Decimal('1'))
            self.db.commit()
            self.db.execute(text('DELETE FROM users WHERE id = :i'), {'i': world[0]})
            self.db.commit()
            self.assertEqual(self.scalar("SELECT count(*) FROM usage_budgets WHERE scope = 'user' AND owner_user_id IS NULL"), 1)
            later = T0 + timedelta(days=40)
            world2 = self.make_world()
            job2 = self.create(world2, fingerprint=H('2'), now=later).job
            lease2 = job_service.claim_job(self.db, job2.id, now=later)
            usage_service.reserve_attempt(self.db, lease2, Decimal('1'), now=later)   # 다음 달은 새 예산
            self.db.commit()
            self.assertEqual(self.scalar("SELECT count(DISTINCT period_start) FROM usage_budgets WHERE scope = 'server'"), 2)


class ReservationSafety(Case):
    """비용 예약은 유효한 처리권에서만, 미정산 예약이 있으면 자동 재호출 없이."""

    def reservations(self):
        return [tuple(r) for r in self.db.execute(text(
            'SELECT attempt_no, status FROM usage_reservations ORDER BY attempt_no, status')).all()]

    def test_expired_or_cancelled_lease_cannot_reserve(self):
        world, job, lease = self.claimed()
        late = T0 + timedelta(seconds=121)                                   # lease_until(T0+120s) 이후, 아직 복구 전
        with self.assertRaises(LeaseLost):
            usage_service.reserve_attempt(self.db, lease, Decimal('1'), now=late)
        self.db.rollback()
        self.assertEqual(self.scalar('SELECT count(*) FROM usage_reservations'), 0)
        job_service.request_cancel(self.db, job.id, world[0], now=T0)
        with self.assertRaises(CancelRequested):
            usage_service.reserve_attempt(self.db, lease, Decimal('1'), now=T0)
        self.db.rollback()
        self.assertEqual(self.scalar('SELECT count(*) FROM usage_reservations'), 0)

    def test_recovery_in_between_check_and_reserve_cannot_leave_a_reservation(self):
        world, job, lease = self.claimed()
        recovering = Session()
        recovering.execute(text("""UPDATE ai_jobs SET status = 'retry_wait', lease_token = NULL, lease_until = NULL,
                                   next_attempt_at = :t WHERE id = :j"""), {'t': T0, 'j': job.id})   # 미커밋
        outcome = {}

        def reserve():
            with Session() as db:
                try:
                    usage_service.reserve_attempt(db, lease, Decimal('1'), now=T0)
                    db.commit()
                    outcome['result'] = 'reserved'
                except LeaseLost:
                    db.rollback()
                    outcome['result'] = 'lost'
        thread = threading.Thread(target=reserve)
        thread.start()
        thread.join(0.7)
        self.assertTrue(thread.is_alive())                                    # 작업 행 잠금을 기다린다
        recovering.commit()
        recovering.close()
        thread.join(10)
        self.assertEqual(outcome['result'], 'lost')
        self.assertEqual(self.scalar('SELECT count(*) FROM usage_reservations'), 0)

    def test_recovery_skips_a_job_that_is_reserving_and_then_treats_it_as_uncertain(self):
        world, job, lease = self.claimed()
        reserving = Session()
        usage_service.reserve_attempt(reserving, lease, Decimal('1'), now=T0)   # 미커밋 잠금 보유
        late = T0 + timedelta(seconds=121)
        self.assertEqual(job_service.recover_expired(self.db, now=late)['retried'], 0)   # 잠긴 행은 건드리지 않는다
        reserving.commit()
        reserving.close()
        stats = job_service.recover_expired(self.db, now=late)
        self.assertEqual((stats['uncertain'], stats['retried']), (1, 0))
        self.assertEqual(self.job_row(job.id)['error_code'], 'OUTCOME_UNCERTAIN')

    def test_retryable_failure_with_open_reservation_is_uncertain_and_not_retried(self):
        world, job, lease = self.claimed()
        usage_service.reserve_attempt(self.db, lease, Decimal('1'), now=T0)
        self.db.commit()
        self.assertEqual(job_service.fail_job(self.db, lease, 'TIMEOUT', retryable=True, now=T0), 'failed')
        row = self.job_row(job.id)
        self.assertEqual((row['status'], row['error_code']), ('failed', 'OUTCOME_UNCERTAIN'))
        self.assertEqual(self.reservations(), [(1, 'uncertain'), (1, 'uncertain')])
        self.assertEqual(self.scalar('SELECT status FROM ai_attempts WHERE job_id = :j', j=job.id), 'uncertain')
        self.assertEqual(self.scalar("SELECT max(reserved_amount) FROM usage_budgets"), Decimal('1'))   # 예산 계속 점유
        self.assertEqual(self.scalar('SELECT count(*) FROM task_outbox WHERE job_id = :j', j=job.id), 1)  # 재시도 발행 없음
        self.assertIsNone(job_service.claim_job(self.db, job.id, now=T0 + timedelta(hours=1)))
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_attempts WHERE job_id = :j', j=job.id), 1)

    def test_already_uncertain_reservation_also_blocks_retry(self):
        world, job, lease = self.claimed()
        usage_service.reserve_attempt(self.db, lease, Decimal('1'), now=T0)
        usage_service.mark_uncertain(self.db, job.id, 1)
        self.db.commit()
        self.assertEqual(job_service.fail_job(self.db, lease, 'TIMEOUT', retryable=True, now=T0), 'failed')
        self.assertEqual(self.job_row(job.id)['error_code'], 'OUTCOME_UNCERTAIN')

    def test_released_reservation_allows_retry_but_a_settled_call_never_does(self):
        resolvers = ((lambda j: usage_service.release_attempt(self.db, j, 1), 'retry_wait'),     # 호출이 처리되지 않았다
                     (lambda j: usage_service.settle_attempt(self.db, j, 1, Decimal('0.5')), 'failed'))   # 이미 처리·정산됨
        for n, (resolve, expected) in enumerate(resolvers):
            world, job, lease = self.claimed(self.make_world(), fingerprint=H(str(n + 1)))
            usage_service.reserve_attempt(self.db, lease, Decimal('1'), now=T0)
            resolve(job.id)
            self.db.commit()
            self.assertEqual(job_service.fail_job(self.db, lease, 'TEMP', retryable=True, now=T0), expected)

    def test_finish_cleans_up_open_reservations(self):
        world, job, lease = self.claimed()
        usage_service.reserve_attempt(self.db, lease, Decimal('1.5'), now=T0)
        self.db.commit()
        job_service.finish_job(self.db, lease, 'succeeded', now=T0)               # 비용을 모르면 예약액으로 추정 정산
        self.assertEqual(self.reservations(), [(1, 'settled'), (1, 'settled')])
        self.assertEqual(self.scalar("SELECT max(spent_amount) FROM usage_budgets"), Decimal('1.5'))
        self.assertEqual(self.scalar("SELECT max(reserved_amount) FROM usage_budgets"), 0)

        world2, job2, lease2 = self.claimed(self.make_world(), fingerprint=H('2'))
        usage_service.reserve_attempt(self.db, lease2, Decimal('1'), now=T0)
        self.db.commit()
        job_service.finish_job(self.db, lease2, 'failed', error_code='BAD', now=T0)   # 호출 여부를 모르면 불명
        self.assertIn((1, 'uncertain'), self.reservations())

        world3, job3, lease3 = self.claimed(self.make_world(), fingerprint=H('3'))
        usage_service.reserve_attempt(self.db, lease3, Decimal('1'), now=T0)
        self.db.commit()
        job_service.request_cancel(self.db, job3.id, world3[0], now=T0)
        self.assertEqual(job_service.finish_job(self.db, lease3, 'succeeded', now=T0), 'cancelled')
        self.assertEqual(self.scalar("SELECT count(*) FROM usage_reservations WHERE status = 'reserved'"), 0)

    def test_claim_refuses_a_job_with_unresolved_reservations(self):
        world, job, lease = self.claimed()
        usage_service.reserve_attempt(self.db, lease, Decimal('1'), now=T0)
        self.db.execute(text("UPDATE ai_jobs SET status = 'queued', lease_token = NULL, lease_until = NULL WHERE id = :j"),
                        {'j': job.id})                    # 어떤 경로로든 queued로 되돌려진 상황
        self.db.commit()
        self.assertIsNone(job_service.claim_job(self.db, job.id, now=T0 + timedelta(minutes=5)))
        row = self.job_row(job.id)
        self.assertEqual((row['status'], row['error_code']), ('failed', 'OUTCOME_UNCERTAIN'))
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_attempts WHERE job_id = :j', j=job.id), 1)

    def test_runner_does_not_retry_after_a_reserved_call_times_out(self):
        world = self.make_world()
        job = self.create(world).job

        def handler(ctx):
            with Session() as db:
                usage_service.reserve_attempt(db, ctx.lease, Decimal('1'))
                db.commit()
            raise job_runner.RetryableError('TIMEOUT')
        job_runner.register_handler('review', handler)
        try:
            outcome = job_runner.run_job(job.id, session_factory=Session)
        finally:
            job_runner.HANDLERS.pop('review', None)
        self.assertEqual(outcome, 'failed')
        self.assertEqual(self.job_row(job.id)['error_code'], 'OUTCOME_UNCERTAIN')
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_attempts WHERE job_id = :j', j=job.id), 1)

    def test_runner_stops_when_cancel_arrives_before_the_next_reservation(self):
        world = self.make_world()
        job = self.create(world).job

        def handler(ctx):
            with Session() as db:
                job_service.request_cancel(db, ctx.lease.job_id, ctx.lease.owner_user_id)
            with Session() as db:
                usage_service.reserve_attempt(db, ctx.lease, Decimal('1'))      # CancelRequested
        job_runner.register_handler('review', handler)
        try:
            outcome = job_runner.run_job(job.id, session_factory=Session)
        finally:
            job_runner.HANDLERS.pop('review', None)
        self.assertEqual((outcome, self.job_row(job.id)['status']), ('cancelled', 'cancelled'))
        self.assertEqual(self.scalar('SELECT count(*) FROM usage_reservations'), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
