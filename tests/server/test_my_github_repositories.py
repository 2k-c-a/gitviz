"""'내 GitHub 저장소' 목록: 로그인 사용자의 토큰으로 GET /user/repos(github.com/repos와 같은 범위)를 부른다.

No network or database: GitHub, the session user, the token and the registered-repository query are fixtures.
"""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ.setdefault('DATABASE_URL', 'postgresql://gitviz_test:unused@localhost/gitviz_test')
os.environ.setdefault('DATABASE_URL_ASYNC', '')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import HTTPException  # noqa: E402
from requests.exceptions import ConnectionError as RequestsConnectionError  # noqa: E402

from app.api import repositories  # noqa: E402

ITEMS = [
    {'name': 'app', 'owner': {'login': 'me'}, 'html_url': 'https://github.com/me/app', 'private': True,
     'language': 'Python', 'stargazers_count': 2, 'forks_count': 1, 'open_issues_count': 0, 'pushed_at': '2026-11-01T00:00:00Z'},
    {'name': 'lib', 'owner': {'login': 'org'}, 'html_url': 'https://github.com/Org/Lib', 'fork': True, 'archived': True},
]


class MyGithubRepositoriesTests(unittest.TestCase):
    def call(self, *, user='user-1', token='personal', status=200, body=None, link='', registered=(), **query):
        calls = []

        def fake_get(url, params, headers, timeout):
            calls.append((url, params, headers))
            return SimpleNamespace(status_code=status, ok=200 <= status < 300, headers={'Link': link},
                                   json=lambda: ITEMS if body is None else body)

        rows = SimpleNamespace(all=lambda: [SimpleNamespace(url=url) for url in registered])
        with (patch.object(repositories.requests, 'get', fake_get),
              patch.object(repositories, 'user_github_token', return_value=token),
              patch.object(repositories, '_get_session_user_id', return_value=user),
              patch.object(repositories, 'visible_repository_query', return_value=rows)):
            result = repositories.my_github_repositories(SimpleNamespace(), db=None, **query)
        return calls, result

    def test_lists_repositories_the_user_can_access_with_the_personal_token(self):
        calls, result = self.call(registered=['https://github.com/org/lib'])
        url, params, headers = calls[0]
        self.assertEqual(url, 'https://api.github.com/user/repos')
        self.assertEqual(params['affiliation'], 'owner,collaborator,organization_member')
        self.assertEqual((params['sort'], params['direction'], params['page'], params['per_page']), ('pushed', 'desc', 1, 30))
        self.assertEqual(headers['Authorization'], 'Bearer personal')
        first, second = result['results']
        self.assertEqual((first['owner'], first['repo_name'], first['private'], first['is_registered']), ('me', 'app', True, False))
        self.assertEqual((second['fork'], second['archived'], second['is_registered']), (True, True, True))
        self.assertIsNotNone(first['scores'])
        self.assertFalse(result['has_next'])

    def test_next_page_comes_from_the_link_header(self):
        _, result = self.call(page=2, per_page=10, link='<https://api.github.com/user/repos?page=3>; rel="next"')
        self.assertEqual((result['page'], result['per_page'], result['has_next']), (2, 10, True))

    def test_paging_is_clamped(self):
        self.assertEqual(self.call(page=0, per_page=500)[0][0][1]['page'], 1)
        self.assertEqual(self.call(page=0, per_page=500)[0][0][1]['per_page'], 100)
        calls, result = self.call(page=999, link='rel="next"')
        self.assertEqual(calls[0][1]['page'], 100)
        self.assertFalse(result['has_next'])

    def test_requires_login_and_a_personal_token(self):
        for kwargs in ({'user': None}, {'token': None}):
            with self.assertRaises(HTTPException) as raised:
                calls, _ = self.call(**kwargs)
            self.assertEqual(raised.exception.status_code, 401)

    def test_github_failures_map_to_short_errors(self):
        for status, expected in ((401, 401), (403, 429), (429, 429), (500, 502)):
            with self.assertRaises(HTTPException) as raised:
                self.call(status=status)
            self.assertEqual(raised.exception.status_code, expected, status)
        with self.assertRaises(HTTPException) as raised:
            self.call(body={'message': 'unexpected'})
        self.assertEqual(raised.exception.status_code, 502)

    def test_connection_failure_is_502_without_retry(self):
        attempts = []

        def failing(*args, **kwargs):
            attempts.append(1)
            raise RequestsConnectionError('down')

        with (patch.object(repositories.requests, 'get', failing),
              patch.object(repositories, 'user_github_token', return_value='personal'),
              patch.object(repositories, '_get_session_user_id', return_value='user-1')):
            with self.assertRaises(HTTPException) as raised:
                repositories.my_github_repositories(SimpleNamespace(), db=None)
        self.assertEqual((raised.exception.status_code, len(attempts)), (502, 1))


if __name__ == '__main__':
    unittest.main()
