"""S1 AI 권한·설정·CSRF·암호화 테스트. PostgreSQL 없이 실행된다(SQLite·mock 사용)."""
import datetime
import logging
import os
import ssl
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# 연결하지 않는 placeholder URL(엔진은 지연 연결). 실제 .env의 DB를 건드리지 않는다.
os.environ['DATABASE_URL'] = 'postgresql://gitviz_test:dbsecretpw@localhost:5432/gitviz_test'
os.environ['DATABASE_URL_ASYNC'] = ''
os.environ.setdefault('SESSION_SECRET_KEY', 'ai-security-test-secret')

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from app.api.ai.dependencies import ai_error_response, require_ai_user, require_csrf  # noqa: E402
from app.core import ai_settings  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.schemas.ai import ErrorCode  # noqa: E402
from app.services.ai import access  # noqa: E402
from app.services.ai.access import AIAction, AIError, authorize_ai, ensure_owner  # noqa: E402
from app.services.ai.crypto import InputCryptoError, decrypt_input, encrypt_input  # noqa: E402

KEY1, KEY2 = 'a1' * 32, 'b2' * 32
ORIGIN = 'http://localhost:8000'
LOCAL_DB = 'postgresql://gitviz_test:dbsecretpw@localhost:5432/gitviz_test'
REMOTE_DB = 'postgresql://user.proj:dbsecretpw@pooler.example.com:5432/postgres'
REMOTE_DB_ASYNC = 'postgresql+asyncpg://user.proj:dbsecretpw@pooler.example.com:6543/postgres'


def ai_env(**over):
    env = {'AI_ENABLED': '1', 'AI_ALLOW_EXTERNAL_TRANSFER': '1', 'AI_INPUT_ENCRYPTION_KEYS': f'v1:{KEY1}',
           'AI_INPUT_KEY_VERSION': 'v1', 'AI_ALLOWED_ORIGINS': ORIGIN, 'SESSION_COOKIE_SECURE': '0',
           'DATABASE_URL': LOCAL_DB, 'DATABASE_URL_ASYNC': LOCAL_DB.replace('postgresql://', 'postgresql+asyncpg://'),
           'DATABASE_SSL_CA': '', 'AI_POLICY_VERSIONS': 'code-transfer-v1',
           'AI_CREDENTIAL_ENCRYPTION_KEYS': f'c1:{KEY2}', 'AI_CREDENTIAL_KEY_VERSION': 'c1',
           'AI_PROVIDER_PROFILE': 'claude-opus-5-5-v1', 'AI_ALLOWED_PROFILES': ''}
    env.update(over)
    return patch.dict(os.environ, env)


def make_ca_file(directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'test-ca')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    path = Path(directory) / 'ca.pem'
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(path)


class SettingsValidation(unittest.TestCase):
    def problems(self, **over):
        with ai_env(**over):
            return ai_settings.validate_ai_settings()

    def test_disabled_needs_nothing(self):
        self.assertEqual(self.problems(AI_ENABLED='0', AI_INPUT_ENCRYPTION_KEYS='', DATABASE_URL=''), [])

    def test_valid_local_config(self):
        self.assertEqual(self.problems(), [])

    def test_key_problems(self):
        self.assertTrue(self.problems(AI_INPUT_ENCRYPTION_KEYS=''))
        self.assertTrue(self.problems(AI_INPUT_ENCRYPTION_KEYS='v1:abc'))        # 짧은 키
        self.assertTrue(self.problems(AI_INPUT_ENCRYPTION_KEYS='v1:' + 'zz' * 32))  # hex 아님
        self.assertTrue(self.problems(AI_INPUT_KEY_VERSION='v2'))                # 없는 버전

    def test_remote_origin_requires_secure_cookie(self):
        self.assertTrue(self.problems(AI_ALLOWED_ORIGINS='https://gitviz.example.com'))
        self.assertEqual(self.problems(AI_ALLOWED_ORIGINS='https://gitviz.example.com', SESSION_COOKIE_SECURE='1'), [])

    def test_sync_and_async_must_be_same_database(self):
        self.assertTrue(self.problems(DATABASE_URL=LOCAL_DB,
                                      DATABASE_URL_ASYNC='postgresql+asyncpg://gitviz_test:x@localhost:5432/other'))
        self.assertTrue(self.problems(DATABASE_URL=LOCAL_DB,
                                      DATABASE_URL_ASYNC='postgresql+asyncpg://gitviz_test:x@otherhost:5432/gitviz_test'))
        # 풀러 포트 차이는 같은 DB로 본다.
        self.assertTrue(ai_settings.same_physical_database(REMOTE_DB, REMOTE_DB_ASYNC))

    def test_remote_database_requires_ca(self):
        self.assertTrue(self.problems(DATABASE_URL=REMOTE_DB, DATABASE_URL_ASYNC=REMOTE_DB_ASYNC))
        self.assertTrue(self.problems(DATABASE_URL=REMOTE_DB, DATABASE_URL_ASYNC=REMOTE_DB_ASYNC,
                                      DATABASE_SSL_CA=str(ROOT / 'no-such-ca.pem')))
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.problems(DATABASE_URL=REMOTE_DB, DATABASE_URL_ASYNC=REMOTE_DB_ASYNC,
                                           DATABASE_SSL_CA=make_ca_file(d)), [])

    def test_messages_and_logs_do_not_leak_secrets(self):
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        logging.getLogger().addHandler(handler)
        try:
            with tempfile.TemporaryDirectory() as d:
                ca = str(Path(d) / 'missing-secret-path.pem')
                out = self.problems(DATABASE_URL=REMOTE_DB, DATABASE_URL_ASYNC=REMOTE_DB_ASYNC + 'x',
                                    DATABASE_SSL_CA=ca, AI_INPUT_ENCRYPTION_KEYS=f'v9:{KEY2}')
                with ai_env(DATABASE_URL=REMOTE_DB, DATABASE_SSL_CA=ca, AI_INPUT_KEY_VERSION='v0'):
                    with self.assertRaises(RuntimeError) as ctx:
                        ai_settings.require_valid_ai_settings()
        finally:
            logging.getLogger().removeHandler(handler)
        text = ' '.join(out) + str(ctx.exception) + ' '.join(r.getMessage() for r in records)
        for secret in ('dbsecretpw', KEY1, KEY2, 'missing-secret-path'):
            self.assertNotIn(secret, text)

    def test_settings_repr_hides_keys(self):
        with ai_env():
            self.assertNotIn(KEY1, repr(ai_settings.get_ai_settings()))


class RemoteDatabaseCA(unittest.TestCase):
    def test_async_context(self):
        from app.database import _async_ssl_context
        with tempfile.TemporaryDirectory() as d:
            ca = make_ca_file(d)
            with ai_env(DATABASE_SSL_CA=ca):
                ctx = _async_ssl_context(REMOTE_DB_ASYNC)
                self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(ctx.check_hostname)
                self.assertFalse(_async_ssl_context(LOCAL_DB))  # 로컬은 TLS 없음
            with ai_env(DATABASE_SSL_CA=str(Path(d) / 'missing.pem')):
                with self.assertRaises(OSError):
                    _async_ssl_context(REMOTE_DB_ASYNC)           # 잘못된 CA는 연결 전에 실패
            bad = Path(d) / 'bad.pem'
            bad.write_text('not a certificate')
            with ai_env(DATABASE_SSL_CA=str(bad)):
                with self.assertRaises(ssl.SSLError):
                    _async_ssl_context(REMOTE_DB_ASYNC)
            with ai_env(DATABASE_SSL_CA=''):                     # System trust; verification stays mandatory.
                ctx = _async_ssl_context(REMOTE_DB_ASYNC)
                self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(ctx.check_hostname)

    def test_sync_connect_args(self):
        from app.core.database import _sync_connect_args
        with ai_env(DATABASE_SSL_CA='/etc/ca.pem'):
            self.assertEqual(_sync_connect_args(REMOTE_DB), {'sslmode': 'verify-full', 'sslrootcert': '/etc/ca.pem'})
            self.assertEqual(_sync_connect_args(LOCAL_DB), {})
        with ai_env(DATABASE_SSL_CA=''):
            self.assertEqual(_sync_connect_args(REMOTE_DB), {})


class InputEncryption(unittest.TestCase):
    def test_roundtrip_and_randomized(self):
        with ai_env():
            a, b = encrypt_input(b'secret code', 'v1'), encrypt_input(b'secret code', 'v1')
            self.assertNotEqual(a, b)
            self.assertNotIn(b'secret code', a)
            self.assertEqual(decrypt_input(a, 'v1'), b'secret code')
            self.assertEqual(decrypt_input(encrypt_input(b'', 'v1'), 'v1'), b'')

    def test_tamper_truncation_and_aad(self):
        with ai_env():
            payload = encrypt_input(b'code', 'v1', aad=b'input-1')
            self.assertEqual(decrypt_input(payload, 'v1', aad=b'input-1'), b'code')
            flipped = bytearray(payload)
            flipped[-1] ^= 1
            for bad in (bytes(flipped), payload[:-1], payload[:12], b''):
                with self.subTest(len=len(bad)), self.assertRaises(InputCryptoError):
                    decrypt_input(bad, 'v1', aad=b'input-1')
            with self.assertRaises(InputCryptoError):
                decrypt_input(payload, 'v1', aad=b'input-2')   # 다른 행으로 옮긴 암호문

    def test_wrong_and_unknown_key(self):
        with ai_env():
            payload = encrypt_input(b'code', 'v1')
        with ai_env(AI_INPUT_ENCRYPTION_KEYS=f'v1:{KEY2}'):
            with self.assertRaises(InputCryptoError):
                decrypt_input(payload, 'v1')                    # 다른 키
        with ai_env():
            with self.assertRaises(InputCryptoError):
                decrypt_input(payload, 'v9')
            with self.assertRaises(InputCryptoError):
                encrypt_input(b'x', 'v9')

    def test_key_rotation_keeps_old_data_readable(self):
        with ai_env():
            old = encrypt_input(b'old', 'v1')
        with ai_env(AI_INPUT_ENCRYPTION_KEYS=f'v1:{KEY1},v2:{KEY2}', AI_INPUT_KEY_VERSION='v2'):
            self.assertEqual(decrypt_input(old, 'v1'), b'old')
            self.assertEqual(decrypt_input(encrypt_input(b'new', 'v2'), 'v2'), b'new')

    def test_errors_do_not_contain_plaintext_or_key(self):
        with ai_env():
            payload = bytearray(encrypt_input(b'plain-secret', 'v1'))
            payload[-1] ^= 1
            with self.assertRaises(InputCryptoError) as ctx:
                decrypt_input(bytes(payload), 'v1')
        self.assertNotIn('plain-secret', str(ctx.exception))
        self.assertNotIn(KEY1, str(ctx.exception))


USER, OTHER = uuid.uuid4(), uuid.uuid4()


class Authorization(unittest.TestCase):
    def authorize(self, action=AIAction.generate, side_effect=None, policy='code-transfer-v1', user=USER,
                  private=False, **env):
        with ai_env(**env), patch.object(access, 'verify_repository_access',
                                         side_effect=side_effect or (lambda *a, **k: ('repo', {'id': 1, 'private': private}))) as m:
            try:
                result = authorize_ai(None, user, 7, action, policy)
            except AIError as exc:
                return exc, m
            return result, m

    def code(self, *a, **k):
        result, _ = self.authorize(*a, **k)
        self.assertIsInstance(result, AIError)
        return result.code

    def test_allowed(self):
        result, m = self.authorize()
        self.assertEqual(result, ('repo', {'id': 1, 'private': False}))
        self.assertEqual(m.call_args.args[1:3], (7, USER))  # 현재 사용자 기준으로 GitHub 권한 확인

    def test_no_user_and_feature_off(self):
        self.assertEqual(self.code(user=None), ErrorCode.AUTH_REQUIRED)
        self.assertEqual(self.code(AI_ENABLED='0'), ErrorCode.RESOURCE_NOT_FOUND)

    def test_github_permission_lost_or_check_failure_never_allows(self):
        cases = {401: ErrorCode.REAUTH_REQUIRED, 404: ErrorCode.RESOURCE_NOT_FOUND,
                 429: ErrorCode.UPSTREAM_RATE_LIMIT, 502: ErrorCode.ACCESS_CHECK_UNAVAILABLE,
                 409: ErrorCode.INVALID_STATE}
        for status, expected in cases.items():
            with self.subTest(status=status):
                self.assertEqual(self.code(side_effect=HTTPException(status, 'x')), expected)
        self.assertEqual(self.code(side_effect=RuntimeError('boom')), ErrorCode.ACCESS_CHECK_UNAVAILABLE)
        exc, _ = self.authorize(side_effect=RuntimeError('token-in-message'))
        self.assertNotIn('token-in-message', exc.message)
        self.assertTrue(exc.retryable)

    def test_external_transfer_policy(self):
        self.assertEqual(self.code(AI_ALLOW_EXTERNAL_TRANSFER='0'), ErrorCode.TRANSFER_NOT_ALLOWED)
        self.assertEqual(self.code(policy='unknown-v9'), ErrorCode.TRANSFER_NOT_ALLOWED)
        # 입력 준비·조회는 외부 전송 허용 없이도 가능
        for action in (AIAction.prepare, AIAction.read, AIAction.delete):
            result, _ = self.authorize(action=action, AI_ALLOW_EXTERNAL_TRANSFER='0', policy='anything')
            self.assertEqual(result, ('repo', {'id': 1, 'private': False}))

    def test_private_repository_is_never_sent_externally(self):
        # 외부 전송을 켜도 비공개(또는 공개 여부를 모르는) 저장소는 generate 불가
        for private in (True, None):
            with self.subTest(private=private):
                self.assertEqual(self.code(private=private), ErrorCode.TRANSFER_NOT_ALLOWED)
        # 비공개 저장소도 입력 준비·조회·삭제는 가능(외부 전송 없음)
        for action in (AIAction.prepare, AIAction.read, AIAction.delete):
            result, _ = self.authorize(action=action, private=True)
            self.assertEqual(result, ('repo', {'id': 1, 'private': True}))

    def test_other_author_is_hidden_as_not_found(self):
        ensure_owner(USER, USER)
        for owner in (OTHER, None):
            with self.assertRaises(AIError) as ctx:
                ensure_owner(owner, USER)
            self.assertEqual(ctx.exception.code, ErrorCode.RESOURCE_NOT_FOUND)
            self.assertEqual(ctx.exception.status_code, 404)


def build_app():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key='test', same_site='lax')
    app.add_exception_handler(AIError, ai_error_response)
    app.include_router(auth_router.router)

    @app.post('/test/login')
    async def login(request: Request):
        request.session['user_id'] = str(USER)
        return {}

    @app.get('/test/me')
    async def me(request: Request):
        return {'user': str(require_ai_user(request))}

    @app.post('/test/act')
    async def act(request: Request):
        require_ai_user(request)
        require_csrf(request)
        return {'ok': True}

    @app.delete('/test/act')
    async def act_delete(request: Request):
        require_csrf(request)
        return {'ok': True}

    return app


class CsrfAndSession(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(build_app())

    def tearDown(self):
        self.client.close()

    def login_token(self):
        self.client.post('/test/login')
        return self.client.get('/api/auth/csrf').json()['token']

    def test_no_session_is_401_with_envelope(self):
        with ai_env():
            r = self.client.get('/test/me')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()['error']['code'], 'AUTH_REQUIRED')
        self.assertEqual(r.headers['cache-control'], 'private, no-store')
        self.assertTrue(r.json()['error']['request_id'])

    def test_feature_off_hides_endpoints_and_keeps_auth_csrf_endpoint(self):
        with ai_env(AI_ENABLED='0'):
            self.client.post('/test/login')
            self.assertEqual(self.client.get('/test/me').status_code, 404)
            self.assertEqual(self.client.get('/api/auth/csrf').status_code, 200)

    def test_csrf_token_endpoint(self):
        with ai_env():
            t1 = self.login_token()
            self.assertGreaterEqual(len(t1), 32)
            self.assertEqual(self.client.get('/api/auth/csrf').json()['token'], t1)  # 세션 동안 고정
            with TestClient(build_app()) as other:
                self.assertNotEqual(other.get('/api/auth/csrf').json()['token'], t1)  # 세션 바인딩

    def test_mutation_requires_token_and_origin(self):
        with ai_env():
            token = self.login_token()
            good = {'Origin': ORIGIN, 'X-CSRF-Token': token}
            self.assertEqual(self.client.post('/test/act', headers=good).status_code, 200)
            self.assertEqual(self.client.delete('/test/act', headers=good).status_code, 200)
            bad_headers = [
                {'Origin': ORIGIN},                                       # 토큰 누락
                {'Origin': ORIGIN, 'X-CSRF-Token': token + 'x'},          # 토큰 오류
                {'Origin': ORIGIN, 'X-CSRF-Token': ''},
                {'X-CSRF-Token': token},                                  # Origin 누락
                {'Origin': 'https://evil.example.com', 'X-CSRF-Token': token},
                {'Origin': 'null', 'X-CSRF-Token': token},
            ]
            for headers in bad_headers:
                for method in (self.client.post, self.client.delete):
                    with self.subTest(headers=headers):
                        r = method('/test/act', headers=headers)
                        self.assertEqual(r.status_code, 403)
                        self.assertEqual(r.json()['error']['code'], 'CSRF_FAILED')

    def test_token_without_issued_session_token_rejected(self):
        with ai_env():
            self.client.post('/test/login')   # 토큰 발급 전
            r = self.client.post('/test/act', headers={'Origin': ORIGIN, 'X-CSRF-Token': ''})
            self.assertEqual(r.status_code, 403)

    def test_token_from_another_session_rejected(self):
        with ai_env():
            token = self.login_token()
            with TestClient(build_app()) as attacker:
                attacker.post('/test/login')
                attacker.get('/api/auth/csrf')
                r = attacker.post('/test/act', headers={'Origin': ORIGIN, 'X-CSRF-Token': token})
                self.assertEqual(r.status_code, 403)

    def test_get_needs_no_csrf(self):
        with ai_env():
            self.client.post('/test/login')
            self.assertEqual(self.client.get('/test/me').status_code, 200)

    def test_oauth_state_is_not_accepted_as_csrf_token(self):
        with ai_env():
            self.client.post('/test/login')
            self.client.get('/api/auth/github', follow_redirects=False)  # oauth_state 저장
            r = self.client.post('/test/act', headers={'Origin': ORIGIN, 'X-CSRF-Token': 'anything'})
            self.assertEqual(r.status_code, 403)


class AppStartup(unittest.TestCase):
    def run_import(self, **env):
        base = {**os.environ, 'SESSION_SECRET_KEY': 'startup-test-secret', 'DATABASE_URL': LOCAL_DB,
                'AI_INPUT_ENCRYPTION_KEYS': '', 'DATABASE_URL_ASYNC': '', 'AI_ENABLED': '0',
                'PYTHONIOENCODING': 'utf-8'}
        base.update(env)
        return subprocess.run([sys.executable, '-c', 'import app.main'], cwd=ROOT, env=base,
                              capture_output=True, text=True, encoding='utf-8')

    def test_feature_off_keeps_existing_app_running(self):
        r = self.run_import(AI_ENABLED='0')
        self.assertEqual(r.returncode, 0, r.stderr[-500:])

    def test_enabled_with_insecure_config_refuses_to_start_without_leaking(self):
        r = self.run_import(AI_ENABLED='1', DATABASE_URL=REMOTE_DB, SESSION_SECRET_KEY='startup-test-secret')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('AI 설정이 올바르지 않습니다', r.stderr)
        for secret in ('dbsecretpw', 'startup-test-secret'):
            self.assertNotIn(secret, r.stderr)

    def test_enabled_with_valid_config_starts(self):
        r = self.run_import(AI_ENABLED='1', DATABASE_URL=LOCAL_DB, AI_INPUT_ENCRYPTION_KEYS=f'v1:{KEY1}',
                            AI_INPUT_KEY_VERSION='v1', FRONTEND_URL=ORIGIN, AI_ALLOWED_ORIGINS='',
                            AI_CREDENTIAL_ENCRYPTION_KEYS=f'c1:{KEY2}', AI_CREDENTIAL_KEY_VERSION='c1')
        self.assertEqual(r.returncode, 0, r.stderr[-500:])


if __name__ == '__main__':
    unittest.main(verbosity=2)
