"""Repository search paging: the page reaches GitHub, stays inside its 1,000-result window and is echoed back.

No network or database: GitHub, the session user and the registered-repository query are fixtures.
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

from app.api import repositories  # noqa: E402


class SearchPagingTests(unittest.TestCase):
    def search(self, **query):
        calls = []

        def fake_get(url, params, headers, timeout):
            calls.append(params)
            return SimpleNamespace(status_code=200, ok=True, json=lambda: {'total_count': 4321, 'items': []})

        registered = SimpleNamespace(all=lambda: [])
        with (patch.object(repositories.requests, 'get', fake_get),
              patch.object(repositories, 'user_github_token', return_value=None),
              patch.object(repositories, '_get_session_user_id', return_value=None),
              patch.object(repositories, 'visible_repository_query', return_value=registered)):
            result = repositories.search_github_repositories(SimpleNamespace(), db=None, **query)
        return calls[0], result

    def test_page_is_forwarded_and_echoed(self):
        params, result = self.search(q='django', per_page=20, page=3)
        self.assertEqual((params['page'], params['per_page']), (3, 20))
        self.assertEqual((result['page'], result['per_page'], result['total_count']), (3, 20, 4321))

    def test_default_is_first_page(self):
        params, result = self.search(q='django')
        self.assertEqual((params['page'], result['page']), (1, 1))

    def test_page_is_clamped_to_githubs_result_window(self):
        self.assertEqual(self.search(q='django', per_page=20, page=51)[0]['page'], 50)
        self.assertEqual(self.search(q='django', per_page=100, page=99)[0]['page'], 10)
        self.assertEqual(self.search(q='django', per_page=20, page=0)[0]['page'], 1)
        self.assertEqual(self.search(q='django', per_page=20, page=-4)[0]['page'], 1)


if __name__ == '__main__':
    unittest.main()
