"""사용자 API key(BYOK) HTTP API 테스트: /api/ai/v1/providers, /credentials. 제공자 호출은 대체한다."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402

support.require_test_database()
sys.path.insert(0, str(support.ROOT))

import ai_credential_support  # noqa: E402,F401
import ai_input_support as S  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from app.api.ai import credentials as credentials_api  # noqa: E402
from app.api.ai.dependencies import ai_error_response, ai_validation_error_response  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.services.ai import credentials  # noqa: E402
from app.services.ai.access import AIError  # noqa: E402
from app.services.ai.providers.base import ProviderError, ProviderFailure  # noqa: E402

ORIGIN = 'http://localhost:8000'
KEY_A = 'sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAA1111'
KEY_B = 'sk-ant-api03-BBBBBBBBBBBBBBBBBBBBBBBBBBBB2222'
OPUS, SONNET = 'claude-opus-5-5-v1', 'claude-sonnet-5-5-v1'
URL = '/api/ai/v1/credentials/anthropic'


def setUpModule():
    S.migrate()


def build_app():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key='api-test', same_site='lax')
    app.add_exception_handler(AIError, ai_error_response)
    app.add_exception_handler(RequestValidationError, ai_validation_error_response)
    app.include_router(auth_router.router)
    app.include_router(credentials_api.router)

    @app.post('/test/login/{user_id}')
    async def login(user_id: str, request: Request):
        request.session['user_id'] = user_id
        return {}
    return app


class Api(S.InputCase):
    def setUp(self):
        super().setUp()
        self.verified = []
        self.fail_with = None

        def verifier(profile, key):
            self.verified.append((profile.id, key))
            if self.fail_with:
                raise ProviderError(ProviderFailure(self.fail_with, retryable=False, outcome_uncertain=False))
        self._patch = patch.object(credentials, 'default_verifier', verifier)
        self._patch.start()
        self._env = S.ai_env(AI_ALLOW_EXTERNAL_TRANSFER=1)
        self._env.__enter__()
        self.client = TestClient(build_app())
        self.owner = self.make_user()
        self.login(self.owner)

    def tearDown(self):
        self._env.__exit__(None, None, None)
        self._patch.stop()
        self.client.close()
        super().tearDown()

    def login(self, user):
        self.client.post(f'/test/login/{user}')
        self.token = self.client.get('/api/auth/csrf').json()['token']

    def headers(self, **extra):
        return {'Origin': ORIGIN, 'X-CSRF-Token': self.token, **extra}

    def put(self, key=KEY_A, **over):
        return self.client.put(URL, json={'api_key': key, **over}, headers=self.headers())

    def error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()['error']['code'], code)
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        self.assertNotIn('sk-ant', response.text)
        return response.json()['error']


class Endpoints(Api):
    def test_register_list_replace_delete_never_return_the_key(self):
        r = self.put(profile_id=SONNET)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers['cache-control'], 'private, no-store')
        body = r.json()
        self.assertEqual((body['changed'], body['credential']['version'], body['credential']['key_hint'],
                          body['credential']['preferred_profile_id']), (True, 1, 'sk-ant-…1111', SONNET))
        self.assertNotIn('AAAA', r.text)
        self.assertNotIn('api_key', r.text)
        listed = self.client.get('/api/ai/v1/credentials')
        self.assertEqual([c['provider'] for c in listed.json()['credentials']], ['anthropic'])
        self.assertNotIn('AAAA', listed.text)
        self.assertEqual(self.put().json()['changed'], False)
        replaced = self.put(KEY_B).json()
        self.assertEqual((replaced['changed'], replaced['previous_version'], replaced['credential']['key_hint']),
                         (True, 1, 'sk-ant-…2222'))
        deleted = self.client.delete(URL, headers=self.headers())
        self.assertEqual((deleted.status_code, deleted.json()), (200, {'provider': 'anthropic', 'status': 'deleted', 'jobs_cancelled': 0}))
        self.assertEqual(self.client.get('/api/ai/v1/credentials').json()['credentials'], [])
        self.error(self.client.delete(URL, headers=self.headers()), 404, 'RESOURCE_NOT_FOUND')

    def test_each_user_sees_only_their_own_key(self):
        self.put(KEY_A)
        other = self.make_user()
        self.login(other)
        self.assertEqual(self.client.get('/api/ai/v1/credentials').json()['credentials'], [])
        self.error(self.client.delete(URL, headers=self.headers()), 404, 'RESOURCE_NOT_FOUND')
        self.put(KEY_B)
        self.assertEqual(self.client.get('/api/ai/v1/credentials').json()['credentials'][0]['key_hint'], 'sk-ant-…2222')
        self.login(self.owner)
        self.assertEqual(self.client.get('/api/ai/v1/credentials').json()['credentials'][0]['key_hint'], 'sk-ant-…1111')

    def test_providers_lists_models_prices_and_allow_list(self):
        r = self.client.get('/api/ai/v1/providers')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body['external_transfer_allowed'])
        models = {m['profile_id']: m for m in body['providers'][0]['models']}
        self.assertEqual(set(models), {OPUS, SONNET})
        self.assertTrue(models[OPUS]['allowed'])
        with S.ai_env(AI_ALLOWED_PROFILES=SONNET):
            listed = self.client.get('/api/ai/v1/providers').json()['providers'][0]['models']
        self.assertEqual({m['profile_id']: m['allowed'] for m in listed}, {OPUS: False, SONNET: True})


class Rejections(Api):
    def test_login_feature_flag_and_csrf(self):
        anon = TestClient(build_app())
        for method, path in (('get', '/api/ai/v1/providers'), ('get', '/api/ai/v1/credentials'),
                             ('put', URL), ('delete', URL)):
            self.error(getattr(anon, method)(path), 401, 'AUTH_REQUIRED')
        with S.ai_env(AI_ENABLED=0):
            self.error(self.put(), 404, 'RESOURCE_NOT_FOUND')
            self.error(self.client.get('/api/ai/v1/providers'), 404, 'RESOURCE_NOT_FOUND')
        self.error(self.client.put(URL, json={'api_key': KEY_A}), 403, 'CSRF_FAILED')
        self.error(self.client.put(URL, json={'api_key': KEY_A}, headers=self.headers(Origin='https://evil.example')),
                   403, 'CSRF_FAILED')
        self.error(self.client.delete(URL), 403, 'CSRF_FAILED')
        self.assertEqual(self.verified, [])

    def test_validation_errors_never_echo_the_key(self):
        for body in ({'api_key': 'short'}, {'api_key': KEY_A, 'extra': 1}, {'api_key': 'x' * 500}, {}):
            r = self.client.put(URL, json=body, headers=self.headers())
            self.assertEqual(r.status_code, 422, r.text)
            self.assertNotIn(KEY_A, r.text)
            self.assertNotIn('x' * 50, r.text)
        self.error(self.put('sk-ant-api03-has space inside123456'), 422, 'CREDENTIAL_INVALID')
        self.error(self.client.put('/api/ai/v1/credentials/BAD PROVIDER', json={'api_key': KEY_A}, headers=self.headers()),
                   422, 'INVALID_SELECTION')
        self.error(self.client.put('/api/ai/v1/credentials/unsupported', json={'api_key': KEY_A}, headers=self.headers()),
                   422, 'INVALID_SELECTION')
        self.error(self.put(profile_id='gpt-5-v1'), 422, 'INVALID_SELECTION')
        r = self.client.put(URL, content='{"api_key": "' + 'a' * 5000 + '"}',
                            headers={**self.headers(), 'Content-Type': 'application/json'})
        self.assertIn(r.status_code, (413, 422))
        self.assertEqual(self.client.get('/api/ai/v1/credentials').json()['credentials'], [])

    def test_transfer_disabled_blocks_registration_but_not_deletion(self):
        self.put()
        with S.ai_env(AI_ALLOW_EXTERNAL_TRANSFER=0):
            self.error(self.put(KEY_B), 403, 'TRANSFER_NOT_ALLOWED')
            self.assertEqual(self.client.delete(URL, headers=self.headers()).status_code, 200)

    def test_provider_failures_map_to_http_errors(self):
        for kind, status, code in (('auth_failed', 422, 'CREDENTIAL_INVALID'), ('billing_error', 422, 'CREDENTIAL_INVALID'),
                                   ('rate_limited', 429, 'UPSTREAM_RATE_LIMIT'), ('server_error', 503, 'UPSTREAM_UNAVAILABLE')):
            with self.subTest(kind=kind):
                self.fail_with = kind
                self.error(self.put(), status, code)
        self.assertEqual(self.client.get('/api/ai/v1/credentials').json()['credentials'], [])
        self.fail_with = None
        self.assertEqual(self.put().status_code, 200)

    def test_verify_false_skips_the_provider_call(self):
        r = self.put(verify=False)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()['credential']['validated_at'])
        self.assertEqual(self.verified, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
