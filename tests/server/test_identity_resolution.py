"""격리 PostgreSQL에서 기여자 신원 정리 단계 사이의 연결을 검증한다.

커밋 API 단계가 계정을 못 찾은 기여자도 같은 실행의 Search 단계가 시도해야 한다.
GitHub API는 가짜 client로 대신한다.
GITVIZ_TEST_DATABASE_URL은 이름이 gitviz_test로 시작하는 전용 DB여야 한다.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
URL = os.environ.get('GITVIZ_TEST_DATABASE_URL')
if not URL:
    raise RuntimeError('GITVIZ_TEST_DATABASE_URL에 격리 PostgreSQL을 지정하세요.')
from sqlalchemy.engine import make_url
if not (make_url(URL).database or '').startswith('gitviz_test'):
    raise RuntimeError('DB 이름은 gitviz_test로 시작해야 합니다.')
os.environ['DATABASE_URL'] = URL
os.environ['DATABASE_URL_ASYNC'] = URL.replace('postgresql://', 'postgresql+asyncpg://', 1)

from alembic import command
from alembic.config import Config
from app.core.database import SessionLocal
from app.models import Repository
from app.models.commit import Commit
from app.models.contributor import Contributor
from app.collectors.identity.resolver import run_identity_resolution


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class FakeGitHub:
    """커밋 SHA → 연결된 login, 이메일 → 검색 결과 login. 없으면 못 찾은 것으로 응답한다."""
    BASE_URL = 'https://api.github.test'

    def __init__(self, commit_logins, search_logins):
        self.commit_logins = commit_logins
        self.search_logins = search_logins
        self.commit_calls = []
        self.search_calls = []

    def get(self, url, params=None):
        if url.endswith('/search/users'):
            email = params['q'].split(' ')[0]
            self.search_calls.append(email)
            login = self.search_logins.get(email)
            return FakeResponse({'total_count': 1, 'items': [{'login': login}]} if login
                                else {'total_count': 0, 'items': []})
        sha = url.rsplit('/', 1)[-1]
        self.commit_calls.append(sha)
        login = self.commit_logins.get(sha)
        return FakeResponse({'sha': sha, 'author': {'login': login} if login else None})


class IdentityResolution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        command.upgrade(Config(str(ROOT / 'alembic.ini')), 'head')

    def setUp(self):
        self.db = SessionLocal()
        self.repo = Repository(url=f'https://github.com/test/{uuid4().hex}', name='repo',
                               github_id=None, is_private=False)
        self.db.add(self.repo); self.db.commit()
        self.repo.github_id = self.repo.id; self.db.commit()

    def tearDown(self):
        self.db.close()

    def contributor(self, email, sha):
        person = Contributor(repository_id=self.repo.id, primary_email=email,
                             display_name=email.split('@')[0])
        self.db.add(person); self.db.commit()
        self.db.add(Commit(repository_id=self.repo.id, contributor_id=person.id, sha=sha,
                           author_email=email))
        self.db.commit()
        return person

    def resolve(self, client):
        with patch('app.collectors.identity.resolver.time.sleep'):
            return run_identity_resolution(self.db, self.repo.id, client=client, stage2_limit=None,
                                           owner='test', repo='repo')

    def test_commit_api_miss_is_searched_in_same_run(self):
        linked = self.contributor('alice@example.com', 'a' * 40)
        unlinked = self.contributor('bob@corp.example', 'b' * 40)
        client = FakeGitHub(commit_logins={'a' * 40: 'Alice'},
                            search_logins={'bob@corp.example': 'Bob'})

        result = self.resolve(client)

        self.assertEqual(client.search_calls, ['bob@corp.example'])
        self.assertEqual(result['commit_login'], {'checked': 2, 'resolved': 1, 'merged': 0})
        self.assertEqual(result['stage2'], {'searched': 1, 'resolved': 1, 'merged': 0})
        self.db.refresh(linked); self.db.refresh(unlinked)
        self.assertEqual((linked.github_login, linked.resolution_status), ('alice', 'auto'))
        self.assertEqual((unlinked.github_login, unlinked.resolution_status), ('bob', 'auto'))

    def test_found_login_merges_into_existing_contributor(self):
        known = self.contributor('dave@example.com', 'd' * 40)
        known.github_login = 'dave'; known.resolution_status = 'auto'; self.db.commit()
        known_id, other_id = known.id, self.contributor('dave@laptop.local', 'e' * 40).id
        client = FakeGitHub(commit_logins={'e' * 40: 'Dave'}, search_logins={})

        result = self.resolve(client)

        self.assertEqual(result['commit_login'], {'checked': 1, 'resolved': 1, 'merged': 1})
        self.db.expire_all()
        self.assertIsNone(self.db.get(Contributor, other_id))
        authors = self.db.query(Commit.contributor_id).filter(Commit.repository_id == self.repo.id).all()
        self.assertEqual({row[0] for row in authors}, {known_id})

    def test_failed_lookups_wait_for_cooldown_on_rerun(self):
        unknown = self.contributor('carol@corp.example', 'c' * 40)
        first = FakeGitHub(commit_logins={}, search_logins={})
        self.resolve(first)
        self.assertEqual((first.commit_calls, first.search_calls), (['c' * 40], ['carol@corp.example']))

        again = FakeGitHub(commit_logins={}, search_logins={})
        result = self.resolve(again)

        self.assertEqual((again.commit_calls, again.search_calls), ([], []))
        self.assertEqual(result['stage2']['searched'], 0)
        self.db.refresh(unknown)
        self.assertEqual(unknown.resolution_status, 'unresolved')
        self.assertIsNotNone(unknown.identity_search_attempted_at)


if __name__ == '__main__':
    unittest.main()
