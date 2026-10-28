"""P08b HTTP identity contract. No database connection or external provider/GitHub call."""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['DATABASE_URL'] = 'postgresql://gitviz_test:unused@127.0.0.1:1/gitviz_test'
os.environ['DATABASE_URL_ASYNC'] = 'postgresql+asyncpg://gitviz_test:unused@127.0.0.1:1/gitviz_test'
os.environ['REDIS_URL'] = 'redis://127.0.0.1:1/0'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from app.api.desktop import repositories

ACCOUNT = UUID('11111111-1111-4111-8111-111111111111')


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.add_middleware(SessionMiddleware, secret_key='fixture-only-no-real-auth')
        self.app.include_router(repositories.router)
        self.app.dependency_overrides[repositories.enabled] = lambda: None
        self.database = object()
        self.app.dependency_overrides[repositories.get_db] = lambda: self.database
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.user = patch.object(repositories, '_get_session_user_id', return_value=ACCOUNT).start()
        self.access = patch.object(repositories, 'verify_repository_access', return_value=(
            SimpleNamespace(id=2, github_id=900), {'id': 900, 'canonical_url': 'https://github.com/OWNER/repo.git', 'private': False})).start()
        self.addCleanup(patch.stopall)

    def test_verified_identity_is_account_bound_and_no_store(self):
        result = self.client.get('/api/desktop/v1/repositories/2/identity')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {'accountId': str(ACCOUNT), 'repositoryId': 2, 'githubId': 900,
                                         'canonicalUrl': 'https://github.com/owner/repo', 'visibility': 'public'})
        self.access.assert_called_once_with(self.database, 2, ACCOUNT)
        self.assertEqual(result.headers['cache-control'], 'private, no-store')
        self.assertEqual(result.headers['referrer-policy'], 'no-referrer')

    def test_anonymous_never_checks_repository(self):
        self.user.return_value = None
        self.assertEqual(self.client.get('/api/desktop/v1/repositories/2/identity').status_code, 401)
        self.access.assert_not_called()

    def test_existing_permission_errors_are_preserved(self):
        for code in (401, 404, 409, 429, 502):
            self.access.side_effect = HTTPException(code, 'fixture')
            self.assertEqual(self.client.get('/api/desktop/v1/repositories/2/identity').status_code, code)

    def test_identity_drift_and_malformed_canonical_url_fail_closed(self):
        for metadata in ({'id': 901, 'canonical_url': 'https://github.com/owner/repo', 'private': False},
                         {'id': True, 'canonical_url': 'https://github.com/owner/repo', 'private': False},
                         {'id': 900, 'canonical_url': 'https://evil.invalid/owner/repo', 'private': False}):
            self.access.return_value = (SimpleNamespace(id=2, github_id=900), metadata)
            self.assertIn(self.client.get('/api/desktop/v1/repositories/2/identity').status_code, (409, 502))

    def test_visibility_requires_a_verified_boolean_and_preserves_private(self):
        for private in (False, True, None, 0, 'false'):
            self.access.return_value = (SimpleNamespace(id=2, github_id=900),
                {'id': 900, 'canonical_url': 'https://github.com/owner/repo', 'private': private})
            result = self.client.get('/api/desktop/v1/repositories/2/identity')
            self.assertEqual(result.status_code, 200 if type(private) is bool else 502)
            if type(private) is bool:
                self.assertEqual(result.json()['visibility'], 'private' if private else 'public')

    def test_disabled_feature_is_404_and_does_not_check_repository(self):
        def disabled():
            raise HTTPException(404)
        self.app.dependency_overrides[repositories.enabled] = disabled
        self.assertEqual(self.client.get('/api/desktop/v1/repositories/2/identity').status_code, 404)
        self.access.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
