"""운영 도구(ai_ops.py)와 기한 삭제(결과 보관 기간) 테스트. 실제 PostgreSQL을 쓴다."""
import contextlib
import io
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402

support.require_test_database()
sys.path.insert(0, str(support.ROOT))

import ai_credential_support as C  # noqa: E402
import ai_input_support as S  # noqa: E402
sys.path.insert(0, str(support.ROOT / "tools" / "ops"))
import ai_ops  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.schemas.ai import ErrorCode, canonical_hash  # noqa: E402
from app.services.ai import job_service, job_views, result_store, usage_service  # noqa: E402
from app.services.ai.access import AIError  # noqa: E402


def setUpModule():
    S.migrate()


def now():
    return datetime.now(timezone.utc)


class Ops(S.InputCase):
    def setUp(self):
        super().setUp()
        self._env2 = S.ai_env(AI_ALLOW_EXTERNAL_TRANSFER=1)
        self._env2.start()

    def tearDown(self):
        self._env2.stop()
        super().tearDown()

    def world(self, pr=7):
        owner, repo = self.make_user(), self.make_repo()
        _, _, input_id, _ = self.prepared(owner, repo, now=now(), pr_number=pr)
        pinned = C.pinned_for(self.db, owner, repo, input_id, manifest_hash=self.input_row(input_id)['manifest_hash'].strip())
        job = job_service.create_job(self.db, owner_id=owner, repository_id=repo, input_id=input_id, kind='review',
                                     fingerprint=canonical_hash(str(uuid4())), route='r', idempotency_key=uuid4().hex,
                                     body_hash=canonical_hash(str(uuid4())), pinned=pinned).job
        return owner, repo, input_id, job

    def uncertain_job(self):
        owner, repo, input_id, job = self.world(pr=8)
        lease = job_service.claim_job(self.db, job.id)
        usage_service.reserve_attempt(self.db, lease, Decimal('0.5'))
        self.db.commit()
        usage_service.mark_uncertain(self.db, job.id, lease.attempt_no)
        self.db.commit()
        job_service.fail_job(self.db, lease, 'X', retryable=False)
        return owner, job

    def reservation_ids(self):
        self.db.rollback()
        return [r['id'] for r in self.db.execute(text("SELECT id FROM usage_reservations WHERE status = 'uncertain'")).mappings()]

    def test_status_reports_queue_budget_and_retention_without_secrets(self):
        _, _, input_id, job = self.world()
        self.uncertain_job()
        self.bypass = lambda sql, **p: self._bypass(sql, **p)
        self._bypass("UPDATE ai_inputs SET expires_at = now() - interval '1 minute' WHERE id = :i", i=input_id)
        st = ai_ops.status(self.db)
        self.assertEqual(st['jobs']['review/queued'], 1)
        self.assertEqual(st['jobs']['review/failed'], 1)
        self.assertEqual(st['uncertain_reservations'], 1)
        self.assertEqual(Decimal(st['reserved_amount']) > 0, True)
        self.assertEqual(st['inputs_expired_with_payload'], 1)
        self.assertGreaterEqual(st['outbox_unpublished'], 1)
        self.assertEqual(st['credentials_active'], 2)
        text_out = json.dumps(st)
        self.assertNotIn(C.TEST_KEY, text_out)

    def _bypass(self, sql, **params):
        with S.Session() as db:
            db.execute(text('SET LOCAL session_replication_role = replica'))
            db.execute(text(sql), params)
            db.commit()

    def test_uncertain_calls_can_be_listed_and_resolved_by_the_operator(self):
        owner, job = self.uncertain_job()
        rows = ai_ops.list_uncertain(self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['job_id'], rows[0]['amount'], rows[0]['job'].split('/')[:2]), (str(job.id), '0.500000', ['review', 'failed']))
        rid = self.reservation_ids()[0]
        self.assertTrue(ai_ops.resolve_uncertain(self.db, rid, Decimal('0.2')))                  # 실제 비용을 확인해 정산
        self.assertFalse(ai_ops.resolve_uncertain(self.db, rid, None))                           # 두 번 정리해도 안전
        self.assertEqual(ai_ops.list_uncertain(self.db), [])
        budget = self.db.execute(text("SELECT reserved_amount, spent_amount FROM usage_budgets WHERE scope = 'user'")).one()
        self.assertEqual((budget.reserved_amount, budget.spent_amount), (0, Decimal('0.2')))
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_events WHERE action = 'usage.uncertain_resolved'"), 1)

    def test_release_frees_the_budget(self):
        self.uncertain_job()
        for rid in self.reservation_ids():
            ai_ops.resolve_uncertain(self.db, rid, None)
        budget = self.db.execute(text("SELECT max(reserved_amount) AS r, max(spent_amount) AS s FROM usage_budgets")).one()
        self.assertEqual((budget.r, budget.s), (0, 0))

    def test_operator_can_cancel_waiting_and_running_jobs(self):
        _, _, _, queued = self.world()
        self.assertEqual(ai_ops.cancel_job(self.db, queued.id), 'cancelled')
        _, _, _, running = self.world(pr=9)
        job_service.claim_job(self.db, running.id)
        self.assertEqual(ai_ops.cancel_job(self.db, running.id), 'cancel_requested')
        with self.assertRaises(LookupError):
            ai_ops.cancel_job(self.db, uuid4())
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_events WHERE action = 'job.cancelled_by_operator'"), 2)

    def test_expired_results_are_purged_and_their_jobs_disappear_but_costs_stay(self):
        owner, repo, input_id, job = self.world()
        lease = job_service.claim_job(self.db, job.id)
        usage_service.reserve_attempt(self.db, lease, Decimal('1'))
        self.db.commit()
        job_service.finish_job(self.db, lease, 'succeeded')
        self.db.execute(text("""INSERT INTO ai_results (job_id, owner_user_id, repository_id, payload, payload_hash, coverage, structure_validation, expires_at)
                                VALUES (:j, :o, :r, '{}'::jsonb, repeat('a', 64), '{}'::jsonb, 'passed', :e)"""),
                        {'j': job.id, 'o': owner, 'r': repo, 'e': now() - timedelta(days=1)})
        self.db.commit()
        keep_owner, keep_repo, keep_input, keep_job = self.world(pr=11)
        self.assertEqual(result_store.purge_expired_results(self.db), 1)
        self.assertEqual(result_store.purge_expired_results(self.db), 0)                          # 멱등
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_results'), 0)
        with self.assertRaises(AIError) as ctx:
            job_views.get_job(self.db, job.id, owner)                                              # 삭제된 작업처럼 보이지 않는다
        self.assertEqual(ctx.exception.code, ErrorCode.RESOURCE_NOT_FOUND)
        self.assertEqual(job_views.get_job(self.db, keep_job.id, keep_owner).status.value, 'queued')   # 다른 작업은 그대로
        self.assertGreaterEqual(self.scalar('SELECT count(*) FROM usage_reservations'), 1)        # 비용 기록은 남는다
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_events WHERE action = 'result.expired_purged'"), 1)

    def test_check_flags_unsafe_or_missing_configuration(self):
        env = {'REDIS_URL': 'redis://x'}
        with patch('redis.Redis.from_url') as redis_from_url:
            redis_from_url.return_value.ping.return_value = True
            ok = {name: (passed, detail) for name, passed, detail in ai_ops.check(redis_url='redis://x', env=env)}
        self.assertTrue(ok['AI 설정'][0], ok['AI 설정'])
        self.assertTrue(ok['DB migration'][0], ok['DB migration'])
        self.assertTrue(ok['Redis'][0] and ok['제공자 주소 우회 없음'][0])
        with patch.dict(os.environ, {'AI_TEST_PROVIDER_BASE_URL': 'http://127.0.0.1:1'}):
            self.assertFalse({n: p for n, p, _ in ai_ops.check(redis_url=None, env=env)}['제공자 주소 우회 없음'])   # 운영에서는 비워야 한다
        stray = {n: d for n, _, d in ai_ops.check(redis_url=None, env={'ANTHROPIC_BASE_URL': 'http://evil', 'ANTHROPIC_API_KEY': 'sk-x'})}
        self.assertIn('ANTHROPIC_BASE_URL', stray['남은 제공자 환경변수'])
        self.assertNotIn('sk-x', json.dumps(stray))                                                 # 값은 출력하지 않는다
        self.assertFalse({n: p for n, p, _ in ai_ops.check(redis_url=None, env={})}['Redis'])
        with patch.dict(os.environ, {'AI_ENABLED': '0'}):
            self.assertFalse({n: p for n, p, _ in ai_ops.check(redis_url=None, env=env)}['AI 설정'])

    def test_cli_commands(self):
        self.uncertain_job()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ai_ops.main(['status']), 0)
            self.assertEqual(ai_ops.main(['uncertain']), 0)
        self.assertIn('uncertain_reservations', out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ai_ops.main(['resolve', 'not-a-uuid']), 2)
            self.assertEqual(ai_ops.main(['resolve', str(uuid4()), '--settle', 'abc']), 2)
            self.assertEqual(ai_ops.main(['cancel', str(uuid4())]), 2)
            self.assertEqual(ai_ops.main(['resolve', str(uuid4())]), 0)                             # 없는 예약: 정리할 것이 없다
            self.assertEqual(ai_ops.main(['cancel', 'not-a-uuid']), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
