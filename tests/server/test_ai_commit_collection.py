"""Commit capture and real Git diff checks, without DB or network."""
import os
import sys
import tempfile
import unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_ai_pr_collection import FakeClient, REPO, A, B, C, diffc, Fixture
from app.collectors.github import ai_commit_collector as cc
from app.collectors.ai_errors import PRChanged
from app.collectors.github.ai_pr_collector import InvalidCursor
from app.schemas.ai import PrepareInputRequest
from pydantic import ValidationError


def detail(parents=None):
    return {'sha': B, 'parents': [{'sha': p} for p in (parents if parents is not None else [A])],
            'commit': {'message': 'Title\nDetails'},
            'files': [{'filename': 'main.py', 'status': 'modified', 'additions': 1, 'deletions': 1}]}


class Capture(unittest.TestCase):
    def client(self, data):
        return FakeClient({f'/repos/acme/widgets/commits/{B}': data})

    def test_first_parent_merge_and_root(self):
        for parents, base in (([A], A), ([A, C], A), ([], cc.EMPTY_TREE_SHA)):
            row = cc.capture_commit(self.client(detail(parents)), REPO, B, base, B)
            self.assertEqual((row.source_type, row.pr_number, row.merge_base_sha), ('commit', None, base))
            self.assertEqual(row.revision.source_type, 'commit')

    def test_pins_and_response_sha_are_checked(self):
        with self.assertRaises(PRChanged):
            cc.capture_commit(self.client(detail()), REPO, B, C, B)
        with self.assertRaises(PRChanged):
            cc.list_commit_files(self.client({**detail(), 'sha': C}), REPO, B)

    def test_file_cursor_is_bound_to_commit_and_repository(self):
        client = self.client((detail(), {'Link': '<next>; rel="next"'}))
        page = cc.list_commit_files(client, REPO, B)
        self.assertIsNotNone(page.next_cursor)
        with self.assertRaises(InvalidCursor):
            cc.list_commit_files(client, REPO, C, page.next_cursor)

    def test_commit_request_requires_exactly_one_source_and_matching_head(self):
        body = dict(repository_id=14, commit_sha=B, expected_base_sha=A, expected_head_sha=B, selected_paths=['main.py'])
        PrepareInputRequest(**body)
        for patch in ({'pr_number': 7}, {'commit_sha': None}, {'expected_head_sha': C}):
            with self.assertRaises(ValidationError):
                PrepareInputRequest(**{**body, **patch})


class GitDiff(unittest.TestCase):
    def test_root_and_parent_diffs_and_full_inventory(self):
        root = tempfile.mkdtemp(prefix='gitviz-commit-test-')
        try:
            fx = Fixture(root)
            for base, head in ((cc.EMPTY_TREE_SHA, fx.c0), (fx.c0, fx.f1)):
                with diffc.revision_workspace(fx.path.as_uri(), base, head, protocols='file') as work:
                    collected = diffc.collect_selected_code(work, base, head, ['main.py'], protocols='file')
                    self.assertEqual([f.manifest.path for f in collected.files], ['main.py'])
                    self.assertGreater(len(collected.changed_files), 1)
                    self.assertEqual(collected.files[0].manifest.change_type.value, 'added' if base == cc.EMPTY_TREE_SHA else 'modified')
        finally:
            diffc._remove_tree(root)


if __name__ == '__main__':
    unittest.main(verbosity=2)
