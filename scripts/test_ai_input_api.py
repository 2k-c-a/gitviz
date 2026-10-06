"""S5 입력 준비 HTTP API 테스트: /api/ai/v1/inputs. 실제 PostgreSQL·local Git fixture, GitHub API는 대체한다."""
import os
import sys
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402

support.require_test_database()
sys.path.insert(0, str(support.ROOT))

import ai_input_support as S  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from app.api.ai import inputs as inputs_api  # noqa: E402
from app.api.ai.dependencies import ai_error_response, ai_validation_error_response  # noqa: E402
from app.collectors.ai_errors import (MergeBaseUnavailable, PRChanged, RateLimited, ResourceNotFound,  # noqa: E402
                                      UpstreamUnavailable)
from app.routers import auth as auth_router  # noqa: E402
from app.schemas.ai import ErrorCode  # noqa: E402
from app.services.ai import job_runner  # noqa: E402
from app.services.ai.access import AIAction, AIError  # noqa: E402

ORIGIN = 'http://localhost:8000'


def setUpModule():
    S.migrate()


def build_app():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key='api-test', same_site='lax')
    app.add_exception_handler(AIError, ai_error_response)
    app.add_exception_handler(RequestValidationError, ai_validation_error_response)
    app.include_router(auth_router.router)
    app.include_router(inputs_api.router)
    state = {}

    @app.post('/test/login/{user_id}')
    async def login(user_id: str, request: Request):
        request.session['user_id'] = user_id
        return {}

    @app.get('/other')
    async def other(n: int):
        return {'n': n}
    return app


class ApiCase(S.InputCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(build_app())
        self.owner, self.repo = self.make_user(), self.make_repo()
        self.calls = {'authorize': [], 'capture': []}
        self.capture_error = None
        self.authorize_error = None

        def authorize(db, user_id, repo_id, action, policy_version):
            self.calls['authorize'].append((action, repo_id))
            if self.authorize_error is not None:
                raise self.authorize_error
            row = db.execute(text('SELECT id, url, github_id FROM repositories WHERE id = :i'), {'i': repo_id}).one()
            return SimpleNamespace(id=row.id, url=row.url, github_id=row.github_id), {'private': False}

        def capture(client, ref, pr_number, base, head):
            self.calls['capture'].append((ref.full_name, pr_number, base, head))
            if self.capture_error is not None:
                raise self.capture_error
            return self.captured(ref.repository_id, pr_number=pr_number, **self.meta)
        self.meta = {}
        self._patches = [patch.object(inputs_api, 'authorize_ai', side_effect=authorize),
                         patch.object(inputs_api, 'capture_revision', side_effect=capture),
                         patch.object(inputs_api, 'user_github_token', return_value=None),
                         patch.object(inputs_api, 'GitHubClient')]
        for p in self._patches:
            p.start()
        self.login(self.owner)

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self.client.close()
        super().tearDown()

    # ── 요청 helper ──
    def login(self, user):
        self.client.post(f'/test/login/{user}')
        self.token = self.client.get('/api/auth/csrf').json()['token']

    def headers(self, **extra):
        return {'Origin': ORIGIN, 'X-CSRF-Token': self.token, **extra}

    def body(self, paths=None, **over):
        data = {'repository_id': self.repo, 'pr_number': 7, 'expected_base_sha': self.fx.m1,
                'expected_head_sha': self.fx.f1, 'selected_paths': paths if paths is not None else S.PR_PATHS}
        data.update(over)
        return data

    def post(self, body=None, key='key-1', **kw):
        headers = self.headers(**({'Idempotency-Key': key} if key else {}))
        headers.update(kw.pop('headers', {}))
        return self.client.post('/api/ai/v1/inputs', json=body if body is not None else self.body(), headers=headers)

    def error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        error = response.json()['error']
        self.assertEqual(error['code'], code)
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        self.assertTrue(error['request_id'])
        return error

    def counts(self):
        return {t: self.scalar(f'SELECT count(*) FROM {t}') for t in
                ('pr_revisions', 'ai_inputs', 'ai_jobs', 'ai_input_files', 'task_outbox', 'request_idempotency',
                 'ai_attempts', 'usage_reservations')}

    def prepare_via_worker(self, job_id):
        return self.run_prepare(UUID(job_id))


class Access(ApiCase):
    def test_login_feature_flag_and_csrf(self):
        anon = TestClient(build_app())
        for method, path in (('post', '/api/ai/v1/inputs'), ('get', f'/api/ai/v1/inputs/{uuid4()}'),
                             ('delete', f'/api/ai/v1/inputs/{uuid4()}'),
                             ('get', f'/api/ai/v1/inputs/{uuid4()}/files/{uuid4()}')):
            self.error(getattr(anon, method)(path), 401, 'AUTH_REQUIRED')
        with S.ai_env(AI_ENABLED=0):
            self.error(self.post(), 404, 'RESOURCE_NOT_FOUND')
            self.error(self.client.get(f'/api/ai/v1/inputs/{uuid4()}'), 404, 'RESOURCE_NOT_FOUND')
        self.error(self.client.post('/api/ai/v1/inputs', json=self.body(), headers={'Idempotency-Key': 'k'}), 403, 'CSRF_FAILED')
        self.error(self.client.post('/api/ai/v1/inputs', json=self.body(),
                                    headers={**self.headers(Origin='https://evil.example'), 'Idempotency-Key': 'k'}), 403, 'CSRF_FAILED')
        self.error(self.client.delete(f'/api/ai/v1/inputs/{uuid4()}'), 403, 'CSRF_FAILED')
        self.assertEqual(self.counts()['ai_jobs'], 0)

    def test_request_validation_errors_use_the_error_envelope(self):
        error = self.error(self.post(self.body(paths=[])), 422, 'INVALID_SELECTION')
        self.assertIn('selected_paths', error['details']['fields'])
        for bad in (self.body(expected_head_sha='xyz'), self.body(pr_number=0), self.body(owner_user_id='x'),
                    self.body(paths=['/etc/passwd']), self.body(paths=['a', 'a'])):
            self.error(self.post(bad), 422, 'INVALID_SELECTION')
        self.assertNotIn('xyz', self.post(self.body(expected_head_sha='xyz')).text)       # 입력값을 되돌려 주지 않는다
        self.error(self.post(key=None), 422, 'INVALID_STATE')
        self.error(self.post(key='has space'), 422, 'INVALID_STATE')
        self.assertEqual(self.client.get('/other', params={'n': 'x'}).json()['detail'][0]['loc'], ['query', 'n'])   # 기존 API는 그대로
        self.assertEqual(self.counts()['ai_jobs'], 0)

    def test_size_limits(self):
        with S.ai_env(AI_MAX_FILES=2):
            self.error(self.post(self.body(paths=['a.py', 'b.py', 'c.py'])), 413, 'INPUT_TOO_LARGE')
        self.error(self.post(headers={'Content-Length': '999999'}), 413, 'INPUT_TOO_LARGE')
        self.assertEqual(self.calls['capture'], [])

    def test_github_access_failures_map_to_envelope_without_creating_anything(self):
        self.authorize_error = AIError(ErrorCode.RESOURCE_NOT_FOUND, 'x')
        self.error(self.post(), 404, 'RESOURCE_NOT_FOUND')
        self.authorize_error = AIError(ErrorCode.ACCESS_CHECK_UNAVAILABLE, 'x', retryable=True)
        self.assertTrue(self.error(self.post(), 503, 'ACCESS_CHECK_UNAVAILABLE')['retryable'])
        self.assertEqual(self.calls['capture'], [])
        self.assertEqual(self.counts()['ai_inputs'], 0)


class Create(ApiCase):
    def test_post_accepts_and_stores_everything_in_one_step_without_ai(self):
        response = self.post(headers={'X-Request-ID': 'req-abc'})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        data = response.json()
        self.assertEqual((data['status'], data['files'], data['manifest_hash'], data['reviewable']), ('building', [], None, False))
        self.assertEqual((data['pr_number'], data['base_sha'], data['head_sha'], data['merge_base_sha']),
                         (7, self.fx.m1, self.fx.f1, self.fx.c0))
        self.assertEqual(data['prepare_job']['status'], 'queued')
        self.assertEqual(data['policy_version'], 'code-transfer-v1')
        self.assertEqual(self.calls['capture'], [('acme/widgets', 7, self.fx.m1, self.fx.f1)])
        self.assertEqual(self.calls['authorize'], [(AIAction.prepare, self.repo)])         # 외부 전송 권한이 아니라 준비 권한
        self.assertEqual(self.counts(), {'pr_revisions': 1, 'ai_inputs': 1, 'ai_jobs': 1, 'ai_input_files': 0,
                                         'task_outbox': 1, 'request_idempotency': 1, 'ai_attempts': 0,
                                         'usage_reservations': 0})
        self.assertEqual(self.scalar('SELECT event_type FROM task_outbox'), 'prepare')
        job = self.job_row(UUID(data['prepare_job']['job_id']))
        self.assertEqual((job['kind'], job['owner_user_id']), ('prepare', self.owner))     # owner는 서버가 세션에서 정한다
        self.assertEqual(job['request_options']['selected_paths'], sorted(S.PR_PATHS))
        audit = self.db.execute(text("SELECT action, request_id, details FROM audit_events")).one()
        self.assertEqual((audit.action, audit.request_id, audit.details['selected_files']), ('input.prepare_requested', 'req-abc', 4))

    def test_private_repository_can_be_prepared_without_any_transfer_permission(self):
        with S.ai_env(AI_ALLOW_EXTERNAL_TRANSFER=0):
            private_repo = self.make_repo(private=True)
            self.assertEqual(self.post(self.body(repository_id=private_repo)).status_code, 202)

    def test_same_key_returns_the_original_input_even_if_the_pr_moved(self):
        first = self.post(key='dup')
        self.capture_error = PRChanged('moved')
        again = self.post(key='dup', body=self.body(paths=list(reversed(S.PR_PATHS))))     # 선택 순서만 다른 같은 요청
        self.assertEqual((again.status_code, again.json()['input_id']), (202, first.json()['input_id']))
        self.assertEqual(len(self.calls['capture']), 1)                                    # 재전송은 GitHub를 다시 부르지 않는다
        self.assertEqual(self.counts()['ai_inputs'], 1)
        self.error(self.post(key='dup', body=self.body(paths=['main.py'])), 409, 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.counts()['ai_jobs'], 1)

    def test_same_selection_with_a_new_key_reuses_the_running_preparation(self):
        first = self.post(key='k1').json()
        second = self.post(key='k2').json()
        self.assertEqual(second['input_id'], first['input_id'])
        self.assertEqual(self.counts()['ai_jobs'], 1)
        self.assertEqual(self.counts()['request_idempotency'], 2)

    def test_capture_failures_are_reported_and_nothing_is_created(self):
        cases = ((PRChanged('x'), 409, 'PR_CHANGED', True), (MergeBaseUnavailable('x'), 422, 'INVALID_STATE', False),
                 (ResourceNotFound('x'), 404, 'RESOURCE_NOT_FOUND', False),
                 (UpstreamUnavailable('x'), 503, 'UPSTREAM_UNAVAILABLE', True))
        for exc, status, code, retryable in cases:
            self.capture_error = exc
            with self.subTest(code=code):
                self.assertEqual(self.error(self.post(key=f'k-{code}'), status, code)['retryable'], retryable)
        self.capture_error = RateLimited('x', retry_after=900)
        response = self.post(key='k-rate')
        self.error(response, 429, 'UPSTREAM_RATE_LIMIT')
        self.assertEqual(response.headers['retry-after'], '900')
        self.assertEqual(self.counts()['ai_inputs'], 0)
        self.assertEqual(self.counts()['request_idempotency'], 0)                           # 실패한 요청의 key는 소모하지 않는다
        self.capture_error = None
        self.assertEqual(self.post(key='k-rate').status_code, 202)


class ReadAndDelete(ApiCase):
    def prepared_input(self, **kw):
        data = self.post(**kw).json()
        self.assertEqual(self.prepare_via_worker(data['prepare_job']['job_id']), 'succeeded')
        return data['input_id']

    def get(self, path, **kw):
        return self.client.get(f'/api/ai/v1/inputs/{path}', **kw)

    def test_preview_after_preparation_is_the_transfer_candidate_with_consent_hash(self):
        self.meta = {'changed_files': 4}
        input_id = self.prepared_input()
        response = self.get(input_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        data = response.json()
        self.assertEqual((data['status'], data['execution_valid'], data['raw_available'], data['reviewable']),
                         ('ready', True, True, True))
        self.assertEqual([f['path'] for f in data['files']], sorted(S.PR_PATHS))
        self.assertEqual(data['manifest_hash'], self.input_row(UUID(input_id))['manifest_hash'].strip())
        self.assertEqual((data['pr_title'], data['pr_body']), ('Add feature', 'Adds the feature'))
        estimate = data['token_estimate']
        self.assertGreater(estimate['input_tokens'], 200)
        self.assertEqual((estimate['profile'], estimate['limit'], estimate['within_limit'], estimate['currency']),
                         ('claude-opus-5-5-v1', 24000, True, 'USD'))
        self.assertGreater(float(estimate['estimated_max_cost']), 0)
        self.assertEqual(data['provider_profile'], 'claude-opus-5-5-v1')
        coverage = data['coverage']
        self.assertEqual((coverage['selected_files'], coverage['pr_total_files'], coverage['inventory_coverage'],
                          coverage['selected_content_coverage'], coverage['analysis_coverage']),
                         (4, 4, 'complete', 'complete', 'none'))
        self.assertIsNotNone(data['valid_until'])
        self.assertIsNotNone(data['expires_at'])
        self.assertEqual(data['prepare_job']['status'], 'succeeded')
        # 미리보기에 코드 원문이 없다(코드는 files 엔드포인트로만)
        self.assertNotIn('line two', response.text)

    def test_file_endpoint_returns_stored_diff_with_side_and_range(self):
        input_id = self.prepared_input()
        files = {f['path']: f['file_id'] for f in self.get(input_id).json()['files']}
        full = self.get(f'{input_id}/files/{files["src/b.py"]}')
        self.assertEqual(full.status_code, 200)
        self.assertEqual(full.headers['cache-control'], 'private, no-store')
        kinds = {l['kind'] for l in full.json()['lines']}
        self.assertEqual(kinds, {'context', 'added', 'removed'})
        self.assertIn('line two', full.text)                       # 실제 보관 자료
        part = self.get(f'{input_id}/files/{files["src/b.py"]}', params={'side': 'RIGHT', 'start_line': 2, 'end_line': 3})
        self.assertEqual([l['right_line'] for l in part.json()['lines']], [2, 3])
        self.error(self.get(f'{input_id}/files/{files["src/b.py"]}', params={'start_line': 1, 'end_line': 2}), 422, 'INVALID_SELECTION')
        self.error(self.get(f'{input_id}/files/{files["src/old_mod.py"]}', params={'side': 'RIGHT'}), 422, 'INVALID_SELECTION')
        self.error(self.get(f'{input_id}/files/{uuid4()}'), 404, 'RESOURCE_NOT_FOUND')
        self.error(self.get(f'{input_id}/files/not-a-uuid'), 422, 'INVALID_SELECTION')

    def test_gets_never_start_work_or_change_anything(self):
        input_id = self.prepared_input()
        file_id = self.get(input_id).json()['files'][0]['file_id']
        before = self.counts()
        with patch('app.collectors.git.ai_diff_collector.collect_selected_code') as collect, \
                patch.object(inputs_api, 'capture_revision') as capture:
            for _ in range(3):
                self.get(input_id)
                self.get(f'{input_id}/files/{file_id}')
            self.assertEqual((collect.call_count, capture.call_count), (0, 0))
        after = self.counts()
        self.assertEqual({k: v for k, v in after.items() if k != 'audit_events'}, before)

    def test_only_the_author_can_see_or_delete_and_access_is_rechecked(self):
        input_id = self.prepared_input()
        file_id = self.get(input_id).json()['files'][0]['file_id']
        stranger = self.make_user()
        self.login(stranger)
        for response in (self.get(input_id), self.get(f'{input_id}/files/{file_id}'),
                         self.client.delete(f'/api/ai/v1/inputs/{input_id}', headers=self.headers())):
            self.error(response, 404, 'RESOURCE_NOT_FOUND')
        self.assertEqual(self.input_row(UUID(input_id))['status'], 'ready')                # 삭제되지 않았다
        self.login(self.owner)
        self.assertEqual(self.get(input_id).status_code, 200)
        self.authorize_error = AIError(ErrorCode.RESOURCE_NOT_FOUND, 'x')                  # GitHub 권한 상실
        self.error(self.get(input_id), 404, 'RESOURCE_NOT_FOUND')
        self.error(self.get(f'{input_id}/files/{file_id}'), 404, 'RESOURCE_NOT_FOUND')
        self.authorize_error = AIError(ErrorCode.ACCESS_CHECK_UNAVAILABLE, 'x', retryable=True)
        self.error(self.get(input_id), 503, 'ACCESS_CHECK_UNAVAILABLE')                    # 확인 장애를 허용으로 추정하지 않는다
        self.assertEqual(self.client.delete(f'/api/ai/v1/inputs/{input_id}', headers=self.headers()).status_code, 202)

    def test_delete_removes_raw_code_cancels_pending_work_and_blocks_reads(self):
        input_id = self.prepared_input()
        file_id = self.get(input_id).json()['files'][0]['file_id']
        response = self.client.delete(f'/api/ai/v1/inputs/{input_id}', headers=self.headers(**{'X-Request-ID': 'del-1'}))
        self.assertEqual((response.status_code, response.json()), (202, {'input_id': input_id, 'status': 'expired'}))
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        data = self.get(input_id).json()
        self.assertEqual((data['status'], data['raw_available'], data['execution_valid']), ('expired', False, False))
        self.assertEqual(len(data['files']), 4)                                           # manifest 기록은 남는다
        self.error(self.get(f'{input_id}/files/{file_id}'), 410, 'INPUT_EXPIRED')
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_input_files WHERE encrypted_payload IS NOT NULL'), 0)
        self.assertEqual(self.client.delete(f'/api/ai/v1/inputs/{input_id}', headers=self.headers()).status_code, 202)   # 멱등
        self.assertEqual(self.scalar("SELECT request_id FROM audit_events WHERE action = 'input.deleted' ORDER BY created_at LIMIT 1"), 'del-1')

    def test_delete_during_preparation_cancels_it(self):
        data = self.post().json()
        response = self.client.delete(f'/api/ai/v1/inputs/{data["input_id"]}', headers=self.headers())
        self.assertEqual(response.json()['status'], 'failed')
        self.assertEqual(self.job_row(UUID(data['prepare_job']['job_id']))['status'], 'cancelled')
        self.assertEqual(self.prepare_via_worker(data['prepare_job']['job_id']), 'skipped')
        self.assertEqual(self.get(data['input_id']).json()['status'], 'failed')

    def test_failed_preparation_is_visible_with_its_error_code(self):
        data = self.post().json()
        outcome = self.run_prepare(UUID(data['prepare_job']['job_id']), verify=PRChanged('moved'))
        self.assertEqual(outcome, 'failed')
        shown = self.get(data['input_id']).json()
        self.assertEqual((shown['status'], shown['prepare_job']['status'], shown['prepare_job']['error_code']),
                         ('failed', 'failed', 'PR_CHANGED'))
        self.assertEqual(shown['files'], [])
        self.assertFalse(shown['reviewable'])

    def test_expired_input_is_gone_after_retention(self):
        input_id = self.prepared_input()
        file_id = self.get(input_id).json()['files'][0]['file_id']
        with self.db.begin():
            self.db.execute(text('SET LOCAL session_replication_role = replica'))     # 시간 경과를 재현(보관 기한 이동)
            self.db.execute(text("UPDATE ai_inputs SET expires_at = now() - interval '1 minute' WHERE id = :i"), {'i': input_id})
        data = self.get(input_id).json()
        self.assertEqual((data['status'], data['execution_valid']), ('expired', False))
        self.error(self.get(f'{input_id}/files/{file_id}'), 410, 'INPUT_EXPIRED')


if __name__ == '__main__':
    unittest.main(verbosity=2)
