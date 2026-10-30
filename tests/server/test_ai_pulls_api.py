"""S9 PR·파일 선택 조회 API(/api/ai/v1/pulls)와 기본 모델 변경(PATCH /credentials) 테스트. GitHub와 DB 접근 확인은 대역이다."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402

support.require_test_database()
sys.path.insert(0, str(support.ROOT))

import ai_credential_support as C  # noqa: E402
import ai_input_support as S  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from app.api.ai import credentials as credentials_api  # noqa: E402
from app.api.ai import pulls as pulls_api  # noqa: E402
from app.api.ai import commits as commits_api
from app.api.ai.dependencies import ai_error_response, ai_validation_error_response  # noqa: E402
from app.collectors.ai_errors import RateLimited, ResourceNotFound, UpstreamUnavailable  # noqa: E402
from app.collectors.github.ai_pr_collector import PRFilesPage, PRPage  # noqa: E402
from app.api import auth as auth_router  # noqa: E402
from app.schemas.ai import ErrorCode, InventoryCoverage  # noqa: E402
from app.services.ai.access import AIError  # noqa: E402

ORIGIN = 'http://localhost:8000'
SHA_A, SHA_B = 'a' * 40, 'b' * 40


def setUpModule():
    S.migrate()


def build_app():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key='api-test', same_site='lax')
    app.add_exception_handler(AIError, ai_error_response)
    app.add_exception_handler(RequestValidationError, ai_validation_error_response)
    for module in (auth_router, pulls_api, commits_api, credentials_api):
        app.include_router(module.router)

    @app.post('/test/login/{user_id}')
    async def login(user_id: str, request: Request):
        request.session['user_id'] = user_id
        return {}
    return app


def pr(number=7, **over):
    data = {'number': number, 'github_pr_id': 9000 + number, 'title': f'PR {number}', 'state': 'open', 'draft': False,
            'author_login': 'dev', 'base_ref': 'main', 'base_sha': SHA_A, 'head_ref': 'feat', 'head_sha': SHA_B,
            'head_repo_full_name': 'acme/widgets', 'head_repo_deleted': False, 'is_fork': False,
            'updated_at': '2026-10-01T00:00:00Z'}
    data.update(over)
    return data


class Case(S.InputCase):
    def setUp(self):
        super().setUp()
        self._env2 = S.ai_env(AI_ALLOW_EXTERNAL_TRANSFER=1)
        self._env2.start()
        self.calls, self.error = [], None
        self.denied = None

        def authorize(db, user_id, repo_id, action, policy):
            self.calls.append(('authorize', action.value, repo_id))
            if self.denied:
                raise self.denied
            from types import SimpleNamespace
            row = db.execute(S.text('SELECT id, url, github_id FROM repositories WHERE id = :i'), {'i': repo_id}).one()
            return SimpleNamespace(id=row.id, url=row.url, github_id=row.github_id), {'private': False}

        def prs(client, ref, state, cursor, limit):
            self.calls.append(('prs', ref.full_name, state, cursor, limit))
            if self.error:
                raise self.error
            return PRPage(items=[pr(7), pr(8, title='<img src=x onerror=alert(1)>', is_fork=True)],
                          next_cursor='next-1' if cursor is None else None)

        def files(client, ref, number, cursor, limit):
            self.calls.append(('files', number, cursor, limit))
            if self.error:
                raise self.error
            return PRFilesPage(files=[{'path': 'a.py', 'old_path': None, 'change_type': 'modified', 'additions': 3,
                                       'deletions': 1, 'blob_sha': SHA_A, 'has_patch': True},
                                      {'path': 'b.bin', 'old_path': 'old.bin', 'change_type': 'renamed', 'additions': 0,
                                       'deletions': 0, 'blob_sha': None, 'has_patch': False}],
                               next_cursor=None, base_sha=SHA_A, head_sha=SHA_B, pr_total_files=2,
                               inventory_coverage=InventoryCoverage.complete)
        self._patches = [patch.object(pulls_api, 'authorize_ai', side_effect=authorize),
                         patch.object(commits_api, 'authorize_ai', side_effect=authorize),
                         patch.object(pulls_api, 'list_ai_prs', side_effect=prs),
                         patch.object(pulls_api, 'list_pr_files', side_effect=files),
                         patch.object(pulls_api, '_token', return_value=None),
                         patch.object(pulls_api, 'GitHubClient')]
        for p in self._patches:
            p.start()
        self.client = TestClient(build_app())
        self.owner, self.repo = self.make_user(), self.make_repo()
        self.login(self.owner)

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._env2.stop()
        self.client.close()
        super().tearDown()

    def login(self, user):
        self.client.post(f'/test/login/{user}')
        self.token = self.client.get('/api/auth/csrf').json()['token']

    def headers(self):
        return {'Origin': ORIGIN, 'X-CSRF-Token': self.token}

    def err(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()['error']['code'], code)
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        return response


class Pulls(Case):
    def test_commit_listing_files_validation_and_access(self):
        from test_ai_pr_collection import FakeClient
        from test_ai_commit_collection import detail
        client = FakeClient({'/repos/acme/widgets/commits': ([{'sha': SHA_B,
            'commit': {'message': 'Fix', 'committer': {'date': '2026-01-01T00:00:00Z'}}}], {}),
            f'/repos/acme/widgets/commits/{SHA_B}': detail()})
        with patch.object(commits_api, '_client', return_value=client):
            response = self.client.get('/api/ai/v1/commits', params={'repository_id': self.repo})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers['cache-control'], 'private, no-store')
            self.assertEqual(response.json()['items'][0]['sha'], SHA_B)
            response = self.client.get(f'/api/ai/v1/commits/{SHA_B}/files', params={'repository_id': self.repo})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['base_sha'], SHA_A)
            self.err(self.client.get('/api/ai/v1/commits/bad/files', params={'repository_id': self.repo}), 422, 'INVALID_SELECTION')
            self.denied = AIError(ErrorCode.RESOURCE_NOT_FOUND, 'missing')
            self.err(self.client.get('/api/ai/v1/commits', params={'repository_id': self.repo}), 404, 'RESOURCE_NOT_FOUND')
        anon = TestClient(build_app())
        self.addCleanup(anon.close)
        self.err(anon.get('/api/ai/v1/commits', params={'repository_id': self.repo}), 401, 'AUTH_REQUIRED')

    def test_lists_pull_requests_and_files_without_storing_anything(self):
        before = self.scalar('SELECT count(*) FROM ai_jobs') + self.scalar('SELECT count(*) FROM ai_inputs')
        r = self.client.get('/api/ai/v1/pulls', params={'repository_id': self.repo, 'state': 'merged', 'limit': 10})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers['cache-control'], 'private, no-store')
        body = r.json()
        self.assertEqual([i['number'] for i in body['items']], [7, 8])
        self.assertEqual((body['items'][1]['title'], body['items'][1]['is_fork'], body['next_cursor']),
                         ('<img src=x onerror=alert(1)>', True, 'next-1'))          # 제목은 가공하지 않는다(화면이 텍스트로만 쓴다)
        self.assertIn(('prs', 'acme/widgets', 'merged', None, 10), self.calls)
        self.assertIn(('authorize', 'prepare', self.repo), self.calls)                   # 외부 AI 전송이 아니라 조회 권한
        files = self.client.get('/api/ai/v1/pulls/7/files', params={'repository_id': self.repo}).json()
        self.assertEqual((files['base_sha'], files['head_sha'], files['pr_total_files'], files['inventory_coverage']),
                         (SHA_A, SHA_B, 2, 'complete'))
        self.assertEqual([f['change_type'] for f in files['files']], ['modified', 'renamed'])
        after = self.scalar('SELECT count(*) FROM ai_jobs') + self.scalar('SELECT count(*) FROM ai_inputs')
        self.assertEqual(before, after)

    def test_login_flag_validation_and_access(self):
        anon = TestClient(build_app())
        self.err(anon.get('/api/ai/v1/pulls', params={'repository_id': 1}), 401, 'AUTH_REQUIRED')
        self.err(anon.get('/api/ai/v1/pulls/1/files', params={'repository_id': 1}), 401, 'AUTH_REQUIRED')
        with S.ai_env(AI_ENABLED=0):
            self.err(self.client.get('/api/ai/v1/pulls', params={'repository_id': self.repo}), 404, 'RESOURCE_NOT_FOUND')
        for params in ({}, {'repository_id': 0}, {'repository_id': self.repo, 'state': 'all'},
                       {'repository_id': self.repo, 'limit': 500}):
            self.err(self.client.get('/api/ai/v1/pulls', params=params), 422, 'INVALID_SELECTION')
        self.denied = AIError(ErrorCode.RESOURCE_NOT_FOUND, 'no access')
        self.err(self.client.get('/api/ai/v1/pulls', params={'repository_id': self.repo}), 404, 'RESOURCE_NOT_FOUND')
        self.err(self.client.get('/api/ai/v1/pulls/7/files', params={'repository_id': self.repo}), 404, 'RESOURCE_NOT_FOUND')

    def test_github_failures_map_to_the_error_envelope(self):
        for exc, status, code in ((ResourceNotFound('missing'), 404, 'RESOURCE_NOT_FOUND'),
                                  (RateLimited('limited', retry_after=30), 429, 'UPSTREAM_RATE_LIMIT'),
                                  (UpstreamUnavailable('down'), 503, 'UPSTREAM_UNAVAILABLE')):
            with self.subTest(code=code):
                self.error = exc
                r = self.err(self.client.get('/api/ai/v1/pulls', params={'repository_id': self.repo}), status, code)
                if status == 429:
                    self.assertEqual(r.headers['retry-after'], '30')


class PreferredProfile(Case):
    def test_patch_changes_only_the_default_model(self):
        self.err(self.client.patch('/api/ai/v1/credentials/anthropic', json={'profile_id': C.SONNET}, headers=self.headers()),
                 404, 'RESOURCE_NOT_FOUND')                                              # key가 아직 없다
        C.ensure_credential(self.db, self.owner, key=C.TEST_KEY, profile_id=C.OPUS)
        before = self.client.get('/api/ai/v1/credentials').json()['credentials'][0]
        r = self.client.patch('/api/ai/v1/credentials/anthropic', json={'profile_id': C.SONNET}, headers=self.headers())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers['cache-control'], 'private, no-store')
        body = r.json()
        self.assertEqual((body['preferred_profile_id'], body['version'], body['key_hint']),
                         (C.SONNET, before['version'], before['key_hint']))             # key 버전·힌트는 그대로
        self.assertNotIn(C.TEST_KEY, r.text)
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_credential_versions'), 1)
        for bad in ('gpt-5-v1', 'nope'):
            self.err(self.client.patch('/api/ai/v1/credentials/anthropic', json={'profile_id': bad}, headers=self.headers()),
                     422, 'INVALID_SELECTION')
        with S.ai_env(AI_ALLOWED_PROFILES=C.OPUS, AI_ALLOW_EXTERNAL_TRANSFER=1):
            self.err(self.client.patch('/api/ai/v1/credentials/anthropic', json={'profile_id': C.SONNET}, headers=self.headers()),
                     422, 'INVALID_SELECTION')
        self.err(self.client.patch('/api/ai/v1/credentials/anthropic', json={'profile_id': C.SONNET, 'x': 1}, headers=self.headers()),
                 422, 'INVALID_SELECTION')

    def test_patch_needs_csrf_and_is_per_user(self):
        C.ensure_credential(self.db, self.owner, key=C.TEST_KEY, profile_id=C.OPUS)
        self.err(self.client.patch('/api/ai/v1/credentials/anthropic', json={'profile_id': C.SONNET}), 403, 'CSRF_FAILED')
        other = self.make_user()
        self.login(other)
        self.err(self.client.patch('/api/ai/v1/credentials/anthropic', json={'profile_id': C.SONNET}, headers=self.headers()),
                 404, 'RESOURCE_NOT_FOUND')                                              # 남의 key는 바꿀 수 없다
        self.login(self.owner)
        self.assertEqual(self.client.get('/api/ai/v1/credentials').json()['credentials'][0]['preferred_profile_id'], C.OPUS)


if __name__ == '__main__':
    unittest.main(verbosity=2)
