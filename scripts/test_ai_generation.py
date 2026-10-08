"""S7 설명·리뷰 생성 실행 테스트. 실제 PostgreSQL·실제 job runner·실제 provider adapter를 쓰고, 제공자 HTTP만
httpx2.MockTransport로 대체한다(외부 전송 없음). 저장소 접근 확인(authorize_ai)은 호출 순서를 기록하는 대역으로 바꾼다.
"""
import json
import logging
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402

support.require_test_database()
sys.path.insert(0, str(support.ROOT))

import ai_credential_support as C  # noqa: E402
import ai_input_support as S  # noqa: E402
import anthropic  # noqa: E402
import httpx2  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.schemas.ai import ErrorCode, canonical_hash  # noqa: E402
from app.services.ai import credentials, generation_service, input_store, job_runner, job_service, token_budget  # noqa: E402
from app.services.ai.access import AIError  # noqa: E402
from app.services.ai.providers import registry  # noqa: E402
from app.tasks import ai_generate  # noqa: E402,F401  (summary·review handler 등록)

PROFILE = registry.get_profile(C.OPUS)
USER_KEY = 'sk-ant-api03-GENERATIONKEY0123456789abcdef'
CODE_MARKER = 'b = 20'
USAGE = {'input_tokens': 1200, 'output_tokens': 300}


def setUpModule():
    S.migrate()


def message(text_, usage=USAGE, stop='end_turn'):
    body = {'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': PROFILE.model,
            'content': [{'type': 'text', 'text': text_}], 'stop_reason': stop, 'stop_sequence': None}
    if usage is not None:
        body['usage'] = usage
    return body


class Gen(S.InputCase):
    def setUp(self):
        super().setUp()
        self._env2 = S.ai_env(AI_ALLOW_EXTERNAL_TRANSFER=1, AI_USER_MONTHLY_BUDGET=20, AI_SERVER_MONTHLY_BUDGET=0)
        self._env2.start()
        self.events, self.http, self.api_keys = [], [], []
        self.fail_authorize = {}          # n번째 authorize 호출에서 낼 오류
        self.on_authorize = {}            # n번째 authorize 호출 때 실행할 함수
        self.on_http = {}                 # 경로 끝 부분 → 호출 때 실행할 함수
        self.responder = None
        self.output = None
        self.count_tokens = 1500
        self.logs = []
        real = anthropic.Anthropic

        def factory(**kw):
            self.api_keys.append(kw.get('api_key'))
            return real(**{**kw, 'http_client': anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(self.handle))})
        self._patches = [patch('app.services.ai.generation_service.authorize_ai', side_effect=self.authorize),
                         patch('app.services.ai.providers.anthropic_provider.anthropic.Anthropic', side_effect=factory)]
        for p in self._patches:
            p.start()
        handler = logging.Handler()
        handler.emit = self.logs.append
        self._log_handler = handler
        logging.getLogger().addHandler(handler)
        self._old_level = logging.getLogger().level
        logging.getLogger().setLevel(logging.DEBUG)

    def tearDown(self):
        logging.getLogger().removeHandler(self._log_handler)
        logging.getLogger().setLevel(self._old_level)
        for p in reversed(self._patches):
            p.stop()
        self._env2.stop()
        super().tearDown()

    # ── 대역 ──
    def authorize(self, db, user_id, repo_id, action, policy):
        n = sum(1 for e in self.events if e[0] == 'authorize') + 1
        self.events.append(('authorize', action.value))
        if n in self.on_authorize:
            self.on_authorize[n]()
        if n in self.fail_authorize:
            raise self.fail_authorize[n]
        return SimpleNamespace(), {'private': False}

    def handle(self, request):
        path = request.url.path
        self.events.append(('http', path.rsplit('/', 1)[-1]))
        self.http.append((path, request.headers.get('x-api-key'), request.content))
        for suffix, hook in self.on_http.items():
            if path.endswith(suffix):
                hook()
        if path.endswith('count_tokens'):
            return httpx2.Response(200, json={'input_tokens': self.count_tokens})
        if self.responder is not None:
            return self.responder(request)
        return httpx2.Response(200, json=message(json.dumps(self.output)), headers={'request-id': 'req_abc'})

    # ── 데이터 ──
    def world(self, kind='review', *, profile_id=C.OPUS, key=USER_KEY, pr_number=7):
        owner, repo = self.make_user(), self.make_repo()
        return self.world_for(owner, repo, kind, profile_id=profile_id, key=key, pr_number=pr_number)

    def world_for(self, owner, repo, kind='review', *, profile_id=C.OPUS, key=USER_KEY, pr_number=7, input_id=None,
                  force=False, manifest_hash=None):
        if input_id is None:
            _, _, input_id, _ = self.prepared(owner, repo, now=datetime.now(timezone.utc), pr_number=pr_number)
        C.ensure_credential(self.db, owner, key=key, profile_id=profile_id)
        manifest_hash = manifest_hash or self.input_row(input_id)['manifest_hash'].strip()
        pinned = C.pinned_for(self.db, owner, repo, input_id, profile_id=profile_id, manifest_hash=manifest_hash)
        job = job_service.create_job(
            self.db, owner_id=owner, repository_id=repo, input_id=input_id, kind=kind,
            fingerprint=canonical_hash([kind, str(uuid4())]), route='POST /api/ai/v1/jobs', idempotency_key=uuid4().hex,
            body_hash=canonical_hash(str(uuid4())), pinned=pinned, force_regenerate=force).job
        self.output = self.output or self.valid_output(kind, job.id)
        return SimpleNamespace(owner=owner, repo=repo, input_id=input_id, job=job, kind=kind)

    def handle_of(self, job_id, path='main.py'):
        stub = SimpleNamespace(session_factory=S.Session, lease=SimpleNamespace(job_id=job_id))
        info = generation_service._load_job(stub)
        _, _, sent, _ = generation_service._prepare(stub, info)
        return next(f.handle for f in sent.files if f.path == path)

    def valid_output(self, kind, job_id):
        handle = self.handle_of(job_id)
        if kind == 'review':
            return {'schema_version': 'review-output-v1', 'findings': [self.finding(handle)]}
        return {'schema_version': 'summary-output-v1', 'overview': 'main.py changes the value of b',
                'author_intent': 'adds the feature', 'unknowns': ['callers are not shown'], 'evidence_ids': [handle],
                'file_changes': [{'path': 'main.py', 'summary': 'b becomes 20', 'evidence_ids': [handle]}]}

    @staticmethod
    def finding(handle, **over):
        base = {'title': 'magic number', 'category': 'maintainability', 'priority': 1, 'condition': 'when b is read',
                'impact': 'unclear meaning', 'evidence_ids': [handle], 'verification': 'search for users of b',
                'suggestion': 'name the constant', 'limitations': [],
                'location': {'path': 'main.py', 'side': 'RIGHT', 'start_line': 2, 'end_line': 2, 'quote': CODE_MARKER}}
        base.update(over)
        return base

    def run_job(self, world):
        self.db.rollback()
        return job_runner.run_job(world.job.id, session_factory=S.Session)

    def rows(self, sql, **params):
        self.db.rollback()
        return self.db.execute(text(sql), params).mappings().all()

    def job_row(self, job_id):
        return self.rows('SELECT * FROM ai_jobs WHERE id = :i', i=job_id)[0]

    def budget(self):
        rows = self.rows('SELECT scope, reserved_amount, spent_amount FROM usage_budgets')
        return {r['scope']: (r['reserved_amount'], r['spent_amount']) for r in rows}

    def reservations(self):
        return [r['status'] for r in self.rows('SELECT status FROM usage_reservations ORDER BY attempt_no, id')]

    def messages_calls(self):
        return [h for h in self.http if h[0].endswith('/messages')]

    def bypass(self, sql, **params):
        """finalize된 입력은 trigger가 막으므로 '시간이 지난 상황'은 trigger를 우회해 만든다(테스트 DB 전용)."""
        with S.Session() as db:
            db.execute(text('SET LOCAL session_replication_role = replica'))
            db.execute(text(sql), params)
            db.commit()

    def assertNoResult(self):
        self.assertEqual(self.rows('SELECT count(*) AS n FROM ai_results')[0]['n'], 0)
        self.assertEqual(self.rows('SELECT count(*) AS n FROM ai_findings')[0]['n'], 0)

    def expected_cost(self, usage=USAGE):
        return token_budget.cost_of(PROFILE, usage['input_tokens'], usage['output_tokens'])


class HappyPath(Gen):
    def test_expanded_output_is_prompted_validated_and_stored(self):
        w = self.world('review', profile_id='claude-opus-5-5-v3')
        self.output['schema_version'] = 'review-output-v2'
        self.output['findings'][0]['condition'] = '가' * 6000
        self.assertEqual(self.run_job(w), 'succeeded')
        row = self.rows('SELECT payload FROM ai_results WHERE job_id=:i', i=w.job.id)[0]
        self.assertEqual(len(row['payload']['findings'][0]['condition']), 6000)
        self.assertEqual(row['payload']['schema_version'], 'review-output-v2')
        import json
        request = json.loads(self.messages_calls()[0][2])
        self.assertIn('condition: at most 6000 characters', request['system'])
        self.assertEqual(request['output_config']['format']['schema']['properties']['schema_version']['enum'], ['review-output-v2'])

    def test_review_runs_the_pinned_flow_and_stores_an_immutable_result(self):
        w = self.world('review')
        self.assertEqual(self.run_job(w), 'succeeded')
        job = self.job_row(w.job.id)
        self.assertEqual((job['status'], job['error_code'], job['attempt_count']), ('succeeded', None, 1))
        result = self.rows('SELECT * FROM ai_results WHERE job_id = :j', j=w.job.id)
        self.assertEqual(len(result), 1)
        result = result[0]
        self.assertEqual((result['structure_validation'], result['human_validation']), ('passed', 'unverified'))
        self.assertEqual(result['payload']['kind'], 'review')
        self.assertEqual(result['payload_hash'].strip(), canonical_hash(result['payload']))
        self.assertEqual(result['coverage'], {**self.input_row(w.input_id)['coverage'], 'analysis_coverage': 'complete'})
        self.assertGreater(result['expires_at'], datetime.now(timezone.utc) + timedelta(days=29))
        findings = self.rows('SELECT * FROM ai_findings WHERE result_id = :r', r=result['id'])
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual((f['ordinal'], f['priority'], f['side'], f['start_line'], f['end_line'], f['title']),
                         (1, 1, 'RIGHT', 2, 2, 'magic number'))
        self.assertEqual(len(f['fingerprint'].strip()), 64)
        stored_file = self.rows("SELECT id FROM ai_input_files WHERE input_id = :i AND path = 'main.py'", i=w.input_id)[0]
        self.assertEqual(f['source_file_id'], stored_file['id'])
        self.assertEqual(self.rows('SELECT count(*) AS n FROM finding_feedback')[0]['n'], 0)
        with self.assertRaises(Exception):                                                  # 결과는 바꿀 수 없다
            self.db.execute(text("UPDATE ai_results SET payload_hash = repeat('0', 64) WHERE id = :i"), {'i': result['id']})
        self.db.rollback()

    def test_calls_use_the_users_key_in_order_without_tools_and_with_the_pinned_model(self):
        w = self.world('review')
        self.assertEqual(self.run_job(w), 'succeeded')
        self.assertEqual([e for e in self.events if e[0] == 'http'], [('http', 'count_tokens'), ('http', 'messages')])
        self.assertEqual(set(self.api_keys), {USER_KEY})                                    # 사용자 key만, 서버 공용 key 없음
        self.assertTrue(all(h[1] == USER_KEY for h in self.http))
        body = json.loads(self.messages_calls()[0][2])
        self.assertEqual(body['model'], 'claude-opus-5-5')
        self.assertEqual(set(body) & {'tools', 'tool_choice', 'fallbacks', 'betas', 'temperature', 'top_p', 'top_k'}, set())
        self.assertIn('output_config', body)
        self.assertIn(CODE_MARKER, body['messages'][0]['content'])                         # 고정한 코드가 실제로 전송됐다
        counted = json.loads(self.http[0][2])
        self.assertEqual(counted['output_config'], body['output_config'])                  # 실제 출력 schema 포함

    def test_summary_runs_independently_and_review_prompt_never_contains_the_summary(self):
        owner, repo = self.make_user(), self.make_repo()
        summary = self.world_for(owner, repo, 'summary')
        self.assertEqual(self.run_job(summary), 'succeeded')
        stored = self.rows('SELECT payload FROM ai_results')[0]['payload']
        self.assertEqual((stored['kind'], stored['file_changes'][0]['path']), ('summary', 'main.py'))
        self.assertEqual(self.rows('SELECT count(*) AS n FROM ai_findings')[0]['n'], 0)
        self.output = None
        review = self.world_for(owner, repo, 'review', input_id=summary.input_id)
        self.http.clear()
        self.assertEqual(self.run_job(review), 'succeeded')
        review_body = self.messages_calls()[0][2].decode()
        self.assertNotIn('main.py changes the value of b', review_body)                      # summary 문장을 코드 사실로 넣지 않는다
        self.assertEqual(self.rows('SELECT count(*) AS n FROM ai_results')[0]['n'], 2)

    def test_usage_is_reserved_before_the_call_and_settled_to_the_reported_cost(self):
        w = self.world('review')
        seen = {}
        self.on_http['/messages'] = lambda: seen.update(budget=self.budget(), reservations=self.reservations())
        self.assertEqual(self.run_job(w), 'succeeded')
        reserved = token_budget.max_cost(PROFILE, 1500)
        self.assertEqual(seen['reservations'], ['reserved'])                                  # 호출 시점에는 예약돼 있다
        self.assertEqual(seen['budget']['user'], (reserved, 0))
        self.assertEqual(self.reservations(), ['settled'])
        self.assertEqual(self.budget()['user'], (0, self.expected_cost()))
        self.assertNotIn('server', self.budget())                                             # 서버 공유 예산은 꺼져 있다
        attempt = self.rows('SELECT * FROM ai_attempts WHERE job_id = :j', j=w.job.id)[0]
        self.assertEqual((attempt['status'], attempt['provider_request_id'], attempt['input_tokens'],
                          attempt['output_tokens'], attempt['outcome_uncertain']), ('succeeded', 'req_abc', 1200, 300, False))
        self.assertEqual(attempt['estimated_cost'], self.expected_cost())

    def test_missing_usage_is_settled_at_the_reserved_amount(self):
        w = self.world('review')
        self.responder = lambda r: httpx2.Response(200, json=message(json.dumps(self.output), usage=None))
        self.assertEqual(self.run_job(w), 'succeeded')
        self.assertEqual(self.budget()['user'], (0, token_budget.max_cost(PROFILE, 1500)))

    def test_no_database_transaction_is_open_during_provider_calls(self):
        w = self.world('review')
        open_tx = []

        def check():
            with S.Session() as db:
                open_tx.append(db.execute(text("""SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()
                                AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'""")).scalar())
        self.on_http['/count_tokens'] = check
        self.on_http['/messages'] = check
        self.db.rollback()
        self.assertEqual(self.run_job(w), 'succeeded')
        self.assertEqual(open_tx, [0, 0])

    def test_key_code_and_model_output_never_reach_logs_or_the_database(self):
        w = self.world('review')
        self.assertEqual(self.run_job(w), 'succeeded')
        logs = ' '.join(r.getMessage() + repr(r.args) for r in self.logs)
        self.assertNotIn(USER_KEY, logs)
        self.assertNotIn('GENERATIONKEY', logs)
        self.assertNotIn(CODE_MARKER, logs)
        self.assertNotIn('magic number', logs)
        stored = ' '.join(str(self.db.execute(text(sql)).scalar() or '') for sql in (
            'SELECT string_agg(request_options::text, $$ $$) FROM ai_jobs', 'SELECT string_agg(details::text, $$ $$) FROM audit_events',
            'SELECT string_agg(coalesce(error_code, $$$$), $$ $$) FROM ai_attempts'))
        self.assertNotIn(USER_KEY, stored)


class Revalidation(Gen):
    def test_access_and_policy_are_rechecked_before_count_tokens_before_generation_and_before_saving(self):
        w = self.world('review')
        self.assertEqual(self.run_job(w), 'succeeded')
        self.assertEqual(self.events, [('authorize', 'generate'), ('authorize', 'generate'), ('http', 'count_tokens'),
                                       ('authorize', 'generate'), ('http', 'messages'), ('authorize', 'read')])

    def test_access_lost_before_count_tokens_sends_nothing(self):
        w = self.world('review')
        self.fail_authorize[2] = AIError(ErrorCode.RESOURCE_NOT_FOUND, 'gone')              # 두 번째 확인(count_tokens 직전)
        self.assertEqual(self.run_job(w), 'failed')
        self.assertEqual(self.http, [])
        self.assertEqual(self.job_row(w.job.id)['error_code'], 'ACCESS_REVOKED')
        self.assertEqual((self.reservations(), self.budget()), ([], {}))
        self.assertNoResult()

    def test_access_lost_between_count_tokens_and_generation_sends_no_code_to_the_model(self):
        w = self.world('review')
        self.fail_authorize[3] = AIError(ErrorCode.TRANSFER_NOT_ALLOWED, 'private now')
        self.assertEqual(self.run_job(w), 'failed')
        self.assertEqual([h[0].rsplit('/', 1)[-1] for h in self.http], ['count_tokens'])
        self.assertEqual(self.job_row(w.job.id)['error_code'], 'TRANSFER_NOT_ALLOWED')
        self.assertEqual((self.reservations(), self.budget()), ([], {}))                    # 예약도 하지 않았다

    def test_external_transfer_disabled_or_policy_withdrawn_blocks_the_job(self):
        w = self.world('review')
        self.fail_authorize[1] = AIError(ErrorCode.TRANSFER_NOT_ALLOWED, 'off')
        self.assertEqual(self.run_job(w), 'failed')
        self.assertEqual((self.http, self.job_row(w.job.id)['error_code']), ([], 'TRANSFER_NOT_ALLOWED'))

    def test_access_check_outage_before_any_call_is_retried_later(self):
        w = self.world('review')
        self.fail_authorize[1] = AIError(ErrorCode.ACCESS_CHECK_UNAVAILABLE, 'down', retryable=True)
        self.assertEqual(self.run_job(w), 'retry')
        self.assertEqual((self.http, self.job_row(w.job.id)['status']), ([], 'retry_wait'))
        self.fail_authorize[2] = AIError(ErrorCode.REAUTH_REQUIRED, 'login')

    def test_key_deleted_while_access_is_being_checked_cancels_before_any_transfer(self):
        w = self.world('review')

        def delete_key():
            with S.Session() as db:
                credentials.delete_credential(db, w.owner, 'anthropic')
        self.on_authorize[2] = delete_key                                                  # count_tokens 직전 확인 중에 삭제
        self.assertEqual(self.run_job(w), 'cancelled')
        self.assertEqual(self.http, [])
        self.assertEqual(self.job_row(w.job.id)['status'], 'cancelled')
        self.assertNoResult()
        self.assertEqual(self.rows('SELECT count(*) AS n FROM ai_credential_versions WHERE encrypted_key IS NOT NULL')[0]['n'], 0)

    def test_key_deleted_after_count_tokens_prevents_the_generation_call(self):
        w = self.world('review')

        def delete_key():
            with S.Session() as db:
                credentials.delete_credential(db, w.owner, 'anthropic')
        self.on_http['/count_tokens'] = delete_key
        self.assertEqual(self.run_job(w), 'cancelled')
        self.assertEqual([h[0].rsplit('/', 1)[-1] for h in self.http], ['count_tokens'])
        self.assertEqual((self.reservations(), self.budget()), ([], {}))
        self.assertNoResult()

    def test_purged_key_without_a_cancel_is_still_refused(self):
        """삭제가 key 파기를 먼저 커밋하고 취소는 뒤에 하는 사이: 파기만으로도 호출하지 않는다."""
        w = self.world('review')

        def purge():
            with S.Session() as db:
                db.execute(text("""UPDATE ai_credential_versions SET status = 'deleted', encrypted_key = NULL,
                                   key_version = NULL, secret_purged_at = now(), retired_at = now()"""))
                db.commit()
        self.on_http['/count_tokens'] = purge
        self.assertEqual(self.run_job(w), 'failed')
        self.assertEqual(self.job_row(w.job.id)['error_code'], 'CREDENTIAL_UNAVAILABLE')
        self.assertEqual([h[0].rsplit('/', 1)[-1] for h in self.http], ['count_tokens'])
        self.assertEqual(self.reservations(), [])

    def test_deleted_key_before_start_fails_without_calling_the_provider(self):
        w = self.world('review')
        with S.Session() as db:
            db.execute(text("""UPDATE ai_credential_versions SET status = 'deleted', encrypted_key = NULL,
                               key_version = NULL, secret_purged_at = now(), retired_at = now()"""))
            db.commit()
        self.assertEqual(self.run_job(w), 'failed')
        self.assertEqual((self.http, self.job_row(w.job.id)['error_code']), ([], 'CREDENTIAL_UNAVAILABLE'))

    def test_cancel_requested_before_count_tokens_sends_nothing(self):
        w = self.world('review')

        def cancel():
            with S.Session() as db:
                job_service.request_cancel(db, w.job.id, w.owner)
        self.on_authorize[2] = cancel
        self.assertEqual(self.run_job(w), 'cancelled')
        self.assertEqual(self.http, [])

    def test_consent_scope_expiry_and_model_allow_list_are_rechecked(self):
        w = self.world('review')
        self.assertEqual(self.run_job(self.world_for(w.owner, w.repo, 'summary', input_id=w.input_id,
                                                      manifest_hash='e' * 64)), 'failed')
        self.assertEqual(self.http, [])
        self.assertEqual(self.rows("SELECT error_code FROM ai_jobs WHERE kind = 'summary'")[0]['error_code'], 'PREVIEW_CHANGED')
        self.bypass("UPDATE ai_inputs SET valid_until = now() - interval '1 minute' WHERE id = :i", i=w.input_id)
        self.assertEqual(self.run_job(w), 'failed')
        self.assertEqual((self.http, self.job_row(w.job.id)['error_code']), ([], 'INPUT_EXPIRED'))
        w2 = self.world('review', pr_number=8)
        with S.ai_env(AI_ALLOWED_PROFILES=C.SONNET):
            self.assertEqual(self.run_job(w2), 'failed')
        self.assertEqual((self.http, self.job_row(w2.job.id)['error_code']), ([], 'PROFILE_NOT_ALLOWED'))

    def test_profile_changed_after_pinning_is_refused(self):
        import dataclasses
        w = self.world('review')
        changed = dataclasses.replace(PROFILE, output_price_per_mtok=PROFILE.output_price_per_mtok + 1)
        with patch.dict(registry.PROFILES, {C.OPUS: changed}):
            self.assertEqual(self.run_job(w), 'failed')
        self.assertEqual((self.http, self.job_row(w.job.id)['error_code']), ([], 'PROFILE_CHANGED'))

    def test_unreadable_input_is_never_sent(self):
        w = self.world('review')
        self.bypass("UPDATE ai_input_files SET content_hash = repeat('0', 64) WHERE input_id = :i AND path = 'main.py'",
                    i=w.input_id)
        self.assertEqual(self.run_job(w), 'failed')
        self.assertEqual((self.http, self.job_row(w.job.id)['error_code']), ([], 'INPUT_INTEGRITY'))

    def test_count_tokens_over_the_limit_is_rejected_before_any_reservation(self):
        w = self.world('review')
        self.count_tokens = PROFILE.max_input_tokens + 1
        self.assertEqual(self.run_job(w), 'failed')
        self.assertEqual(self.job_row(w.job.id)['error_code'], 'INPUT_TOO_LARGE')
        self.assertEqual(([h[0].rsplit('/', 1)[-1] for h in self.http], self.reservations()), (['count_tokens'], []))


class Failures(Gen):
    def test_fabricated_location_is_rejected_whole_and_the_call_is_still_billed(self):
        w = self.world('review')
        handle = self.handle_of(w.job.id)
        self.output = {'schema_version': 'review-output-v1', 'findings': [
            self.finding(handle), self.finding(handle, title='fake', location={
                'path': 'main.py', 'side': 'RIGHT', 'start_line': 400, 'end_line': 400, 'quote': 'nothing like this'})]}
        self.assertEqual(self.run_job(w), 'failed')
        job = self.job_row(w.job.id)
        self.assertEqual((job['error_code'], job['attempt_count']), ('OUTPUT_VALIDATION_FAILED', 1))
        self.assertNoResult()                                                               # 부분 채택 없음
        self.assertEqual(self.budget()['user'], (0, self.expected_cost()))                  # 호출했으므로 비용은 기록한다
        self.assertEqual(len(self.messages_calls()), 1)                                    # 자동으로 다시 호출하지 않는다

    def test_invalid_json_truncation_and_refusal_fail_without_a_result(self):
        for text_, stop, code in (('not json at all', 'end_turn', 'OUTPUT_VALIDATION_FAILED'),
                                  ('{"schema_version"', 'max_tokens', 'OUTPUT_TRUNCATED')):
            with self.subTest(code=code):
                w = self.world('review')
                self.responder = lambda r, t=text_, s=stop: httpx2.Response(200, json=message(t, stop=s))
                self.assertEqual(self.run_job(w), 'failed')
                self.assertEqual(self.job_row(w.job.id)['error_code'], code)
        self.assertNoResult()

    def test_retryable_provider_error_releases_the_reservation_and_waits(self):
        w = self.world('review')
        self.responder = lambda r: httpx2.Response(429, json={'type': 'error', 'error': {'type': 'rate_limit_error', 'message': 'x'}},
                                                   headers={'retry-after': '9', 'request-id': 'req_1'})
        self.assertEqual(self.run_job(w), 'retry')
        job = self.job_row(w.job.id)
        self.assertEqual((job['status'], job['error_code']), ('retry_wait', 'PROVIDER_RATE_LIMITED'))
        self.assertEqual((self.reservations(), self.budget()['user']), (['released'], (0, 0)))   # 호출이 처리되지 않았으므로 비용 없음
        self.assertNoResult()

    def test_permanent_provider_error_fails_and_releases(self):
        w = self.world('review')
        self.responder = lambda r: httpx2.Response(401, json={'type': 'error', 'error': {'type': 'authentication_error',
                                                                                         'message': USER_KEY}})
        self.assertEqual(self.run_job(w), 'failed')
        self.assertEqual(self.job_row(w.job.id)['error_code'], 'PROVIDER_AUTH')
        self.assertEqual((self.reservations(), self.budget()['user']), (['released'], (0, 0)))
        self.assertNotIn(USER_KEY, ' '.join(r.getMessage() for r in self.logs))

    def test_uncertain_outcome_keeps_the_reservation_and_never_retries_automatically(self):
        w = self.world('review')

        def lost(request):
            raise httpx2.ReadTimeout('slow')
        self.responder = lost
        self.assertEqual(self.run_job(w), 'failed')
        job = self.job_row(w.job.id)
        self.assertEqual((job['status'], job['error_code'], job['attempt_count']), ('failed', 'OUTCOME_UNCERTAIN', 1))
        self.assertEqual(self.reservations(), ['uncertain'])
        self.assertEqual(self.budget()['user'][0], token_budget.max_cost(PROFILE, 1500))     # 예산을 계속 점유한다
        attempt = self.rows('SELECT status, outcome_uncertain FROM ai_attempts WHERE job_id = :j', j=w.job.id)[0]
        self.assertEqual((attempt['status'], attempt['outcome_uncertain']), ('uncertain', True))
        self.assertEqual(self.run_job(w), 'skipped')                                         # 같은 작업은 다시 실행되지 않는다
        self.assertEqual(len(self.messages_calls()), 1)

    def test_budget_limit_stops_before_the_call(self):
        w = self.world('review')
        with S.ai_env(AI_USER_MONTHLY_BUDGET='0.01'):
            self.assertEqual(self.run_job(w), 'failed')
        self.assertEqual(self.job_row(w.job.id)['error_code'], 'BUDGET_LIMIT')
        self.assertEqual(([h[0].rsplit('/', 1)[-1] for h in self.http], self.reservations()), (['count_tokens'], []))

    def test_shared_server_budget_applies_only_when_enabled(self):
        w = self.world('review')
        with S.ai_env(AI_SERVER_MONTHLY_BUDGET='0.01'):
            self.assertEqual(self.run_job(w), 'failed')
        self.assertEqual(self.job_row(w.job.id)['error_code'], 'BUDGET_LIMIT')
        w2 = self.world('review', pr_number=9)
        self.assertEqual(self.run_job(w2), 'succeeded')                                      # 꺼져 있으면(0) 서버 예산과 무관

    def test_regeneration_after_a_failed_attempt_reserves_again_and_keeps_costs_separate(self):
        w = self.world('review')
        self.responder = lambda r: httpx2.Response(401, json={'type': 'error', 'error': {'type': 'authentication_error', 'message': 'x'}})
        self.assertEqual(self.run_job(w), 'failed')
        self.responder = None
        job2 = job_service.create_job(
            self.db, owner_id=w.owner, repository_id=w.repo, input_id=w.input_id, kind='review',
            fingerprint=w.job.fingerprint, route='POST /api/ai/v1/jobs', idempotency_key=uuid4().hex,
            body_hash=canonical_hash('again'), pinned=C.pinned_for(
                self.db, w.owner, w.repo, w.input_id, manifest_hash=self.input_row(w.input_id)['manifest_hash'].strip())).job
        self.assertEqual(job2.generation, w.job.generation + 1)                              # 실패한 작업은 재사용하지 않는다
        w.job = job2
        self.assertEqual(self.run_job(w), 'succeeded')
        self.assertEqual(sorted(self.reservations()), ['released', 'settled'])
        self.assertEqual(self.budget()['user'], (0, self.expected_cost()))

    def test_storage_failure_after_a_paid_call_is_not_retried_automatically(self):
        w = self.world('review')
        with patch('app.services.ai.result_store.save_validated_result', side_effect=RuntimeError('db down')):
            self.assertEqual(self.run_job(w), 'failed')
        job = self.job_row(w.job.id)
        self.assertEqual((job['status'], job['error_code']), ('failed', 'RESULT_STORAGE_FAILED'))
        self.assertEqual(len(self.messages_calls()), 1)                                      # 또 호출하지 않는다
        self.assertEqual(self.budget()['user'], (0, self.expected_cost()))
        self.assertNoResult()

    def test_result_and_status_are_atomic(self):
        w = self.world('review')
        with patch('app.services.ai.result_store.finding_fingerprint', side_effect=RuntimeError('boom')):
            self.assertEqual(self.run_job(w), 'failed')
        self.assertNoResult()                                                                # result만 남는 일이 없다
        self.assertEqual(self.job_row(w.job.id)['status'], 'failed')


class Interruptions(Gen):
    def test_cancel_during_the_call_never_publishes_the_result_but_records_the_cost(self):
        w = self.world('review')

        def cancel():
            with S.Session() as db:
                job_service.request_cancel(db, w.job.id, w.owner)
        self.on_http['/messages'] = cancel
        self.assertEqual(self.run_job(w), 'cancelled')
        self.assertEqual(self.job_row(w.job.id)['status'], 'cancelled')
        self.assertNoResult()
        self.assertEqual((self.reservations(), self.budget()['user']), (['settled'], (0, self.expected_cost())))

    def test_access_lost_during_the_call_never_publishes_the_result(self):
        w = self.world('review')
        self.fail_authorize[4] = AIError(ErrorCode.RESOURCE_NOT_FOUND, 'gone')              # 저장 직전의 접근 확인
        self.assertEqual(self.run_job(w), 'failed')
        self.assertEqual(self.job_row(w.job.id)['error_code'], 'ACCESS_REVOKED')
        self.assertNoResult()
        self.assertEqual(self.budget()['user'], (0, self.expected_cost()))

    def test_lease_lost_during_the_call_is_uncertain_and_publishes_nothing(self):
        w = self.world('review')

        def expire():
            with S.Session() as db:
                db.execute(text("UPDATE ai_jobs SET lease_until = now() - interval '1 minute' WHERE id = :j"), {'j': w.job.id})
                db.commit()
            with S.Session() as db:
                job_service.recover_expired(db)
        self.on_http['/messages'] = expire
        self.assertEqual(self.run_job(w), 'lost')
        job = self.job_row(w.job.id)
        self.assertEqual((job['status'], job['error_code']), ('failed', 'OUTCOME_UNCERTAIN'))
        self.assertEqual(self.reservations(), ['uncertain'])
        self.assertNoResult()


class WorkerCrash(BaseException):
    """처리자 프로세스가 갑자기 사라지는 상황(except Exception으로 잡히지 않는다)."""


class Recovery(Gen):
    def expire_and_recover(self, w):
        with S.Session() as db:
            db.execute(text("UPDATE ai_jobs SET lease_until = now() - interval '1 minute' WHERE id = :j"), {'j': w.job.id})
            db.commit()
        with S.Session() as db:
            return job_service.recover_expired(db)

    def test_crash_after_settlement_before_saving_is_not_called_again(self):
        w = self.world('review')
        with patch('app.services.ai.generation_service._record_request_id', side_effect=WorkerCrash()):
            with self.assertRaises(WorkerCrash):                                             # 정산 직후 worker 중단
                self.run_job(w)
        self.assertEqual(self.reservations(), ['settled'])
        stats = self.expire_and_recover(w)
        self.assertEqual((stats['result_lost'], stats['retried']), (1, 0))
        job = self.job_row(w.job.id)
        self.assertEqual((job['status'], job['error_code']), ('failed', 'RESULT_LOST'))
        self.assertEqual(self.run_job(w), 'skipped')                                          # 다시 실행되지 않는다
        self.assertEqual(len(self.messages_calls()), 1)                                       # 과금 호출은 1번뿐
        self.assertNoResult()
        self.assertEqual(self.budget()['user'], (0, self.expected_cost()))                    # 비용은 한 번만 기록
        self.assertEqual(self.rows('SELECT status FROM ai_attempts WHERE job_id = :j', j=w.job.id)[0]['status'], 'failed')

    def test_crash_before_the_call_is_still_retried_and_after_the_call_never_by_fail_job(self):
        w = self.world('review')
        with patch('app.services.ai.generation_service._reserve', side_effect=WorkerCrash()):
            with self.assertRaises(WorkerCrash):                                             # 예약 전 중단: 호출 없음
                self.run_job(w)
        self.assertEqual(self.expire_and_recover(w)['retried'], 1)
        self.assertEqual(self.job_row(w.job.id)['status'], 'retry_wait')
        self.assertEqual(self.http[-1][0].rsplit('/', 1)[-1], 'count_tokens')                # 생성 호출은 없었다
        self.assertEqual(self.messages_calls(), [])

    def test_fail_job_never_requeues_an_attempt_whose_call_was_settled(self):
        from decimal import Decimal
        from app.services.ai import usage_service
        w = self.world('review')
        with S.Session() as db:
            lease = job_service.claim_job(db, w.job.id)
        with S.Session() as db:
            usage_service.reserve_attempt(db, lease, Decimal('0.5'))
            db.commit()
        with S.Session() as db:
            usage_service.settle_attempt(db, lease.job_id, lease.attempt_no, Decimal('0.3'))
            db.commit()
        with S.Session() as db:
            self.assertEqual(job_service.fail_job(db, lease, 'RESULT_STORAGE_FAILED', retryable=True), 'failed')
        self.assertEqual(self.job_row(w.job.id)['error_code'], 'RESULT_STORAGE_FAILED')


class CoverageRecording(Gen):
    def test_result_records_the_actually_analyzed_scope_and_keeps_the_input_coverage(self):
        w = self.world('review')
        before = self.input_row(w.input_id)['coverage']
        self.assertEqual(before['analysis_coverage'], 'none')                                # 준비 단계는 분석 전이다
        self.assertEqual(self.run_job(w), 'succeeded')
        after = self.rows('SELECT coverage FROM ai_results')[0]['coverage']
        self.assertEqual((after['analysis_coverage'], after['selected_content_coverage'], after['inventory_coverage']),
                         ('complete', 'complete', 'complete'))
        self.assertEqual(self.input_row(w.input_id)['coverage'], before)                     # 입력의 coverage는 바뀌지 않는다
        for key in ('selected_files', 'pr_total_files', 'limitations'):
            self.assertEqual(after[key], before[key])

    def test_partial_when_some_selected_files_could_not_be_sent(self):
        owner, repo = self.make_user(), self.make_repo()
        _, _, input_id, _ = self.prepared(owner, repo, ['main.py', '.env'], now=datetime.now(timezone.utc))
        w = self.world_for(owner, repo, 'review', input_id=input_id)
        self.assertEqual(self.run_job(w), 'succeeded')
        after = self.rows('SELECT coverage FROM ai_results')[0]['coverage']
        self.assertEqual((after['selected_content_coverage'], after['analysis_coverage']), ('partial', 'partial'))
        self.assertEqual(after['limitations'], self.input_row(input_id)['coverage']['limitations'])   # 제외 사유는 그대로 보존

    def test_result_coverage_rules(self):
        from app.services.ai import result_store
        base = {'inventory_coverage': 'complete', 'selected_content_coverage': 'complete', 'analysis_coverage': 'none',
                'selected_files': 3, 'pr_total_files': 5, 'limitations': ['not_selected']}
        cases = [(3, base, 'complete'), (2, base, 'partial'), (0, base, 'none'),
                 (3, {**base, 'selected_content_coverage': 'partial'}, 'partial'),
                 (2, {**base, 'selected_content_coverage': 'none'}, 'none')]
        for sent, coverage, expected in cases:
            with self.subTest(sent=sent, selected=coverage['selected_content_coverage']):
                result = result_store.result_coverage(coverage, sent)
                self.assertEqual(result['analysis_coverage'], expected)
                self.assertEqual(result['limitations'], ['not_selected'])                    # 선택 밖 파일은 limitation으로 남는다
        self.assertEqual(base['analysis_coverage'], 'none')                                  # 입력 dict는 건드리지 않는다


if __name__ == '__main__':
    unittest.main(verbosity=2)
