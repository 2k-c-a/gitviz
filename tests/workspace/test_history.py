from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from app.workspace.contracts import validate
from tests.workspace.support import WorkspaceFixture


class ChangeHistoryTests(WorkspaceFixture):
    """Local change analysis: server rules (window, live tree, large-commit exclusion), read only."""

    def commit(self, message: str, files: Mapping[str, bytes | None], days_ago: float, author: str = 'Dev One') -> None:
        for path, content in files.items():
            target = self.repo / path
            if content is None:
                self.git('rm', '-q', path)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            self.git('add', path)
        when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime('%Y-%m-%dT%H:%M:%S+00:00')
        subprocess.run([str(self.executable), '-C', str(self.repo), '-c', f'user.name={author}', 'commit', '-q', '-m', message],
                       check=True, capture_output=True,
                       env={**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                            'GIT_AUTHOR_DATE': when, 'GIT_COMMITTER_DATE': when})

    def setUp(self) -> None:
        super().setUp()
        # Committer dates must run forward along history, as in a real repository: git's date-limited walk stops early otherwise.
        when = (datetime.now(timezone.utc) - timedelta(days=300)).strftime('%Y-%m-%dT%H:%M:%S+00:00')
        subprocess.run([str(self.executable), '-C', str(self.repo), 'commit', '-q', '--amend', '--no-edit'], check=True, capture_output=True,
                       env={**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                            'GIT_AUTHOR_DATE': when, 'GIT_COMMITTER_DATE': when})
        self.commit('feat: old work', {'a.py': b'0\n', 'old.py': b'0\n'}, days_ago=200)
        for index in range(3):
            self.commit(f'feat: step {index}', {'a.py': f'step {index}\n'.encode(), 'b.py': f'step {index}\n'.encode()}, days_ago=30 - index)
        self.commit('fix: bug in a', {'a.py': b'fixed\n'}, days_ago=10, author='Dev Two')
        self.commit('chore: temp', {'gone.py': b'1\n', 'a.py': b'again\n'}, days_ago=9)
        self.commit('chore: remove temp', {'gone.py': None}, days_ago=8)
        self.commit('feat: image', {'img.bin': b'\x00\x01\x02'}, days_ago=7)
        bulk = {f'gen/f{index}.txt': b'x\n' for index in range(16)}
        self.commit('chore: generate', bulk, days_ago=6)
        self.commit('chore: regenerate', {path: b'y\n' for path in bulk}, days_ago=5)

    def test_frequent_files_pairs_and_timeline_follow_server_rules(self) -> None:
        before = self.hashes()
        value = self.projects.history(self.project_id)
        self.assertEqual(self.hashes(), before)

        files = {item['path']: item for item in value['files']}
        self.assertEqual(files['a.py']['changeCount'], 5)        # 3 steps + fix + temp; the 200-day-old commit is outside
        self.assertEqual(files['a.py']['bugfixCount'], 1)
        self.assertEqual(files['a.py']['authorCount'], 2)
        self.assertNotIn('gone.py', files)                      # not in the HEAD tree
        self.assertNotIn('old.py', files)                       # only changed before the window
        self.assertIsNone(files['img.bin']['churn'])            # binary numstat
        self.assertIsNone(files['img.bin']['priority'])
        self.assertTrue(files['a.py']['lowSupport'] is False and files['b.py']['lowSupport'] is True)
        self.assertEqual(value['files'][0]['path'], 'a.py')

        self.assertEqual(value['treeFileCount'], 21)            # app.py a.py b.py old.py img.bin + 16 generated
        self.assertNotIn('app.py', files)                       # its only commit is 300 days old
        self.assertEqual(value['largeChangeThreshold'], 15)
        self.assertEqual(value['excludedLargeCommits'], 2)
        pairs = {(item['fileA'], item['fileB']): item for item in value['pairs']}
        self.assertEqual(set(pairs), {('a.py', 'b.py')})        # generated files only co-change in large commits
        self.assertEqual((pairs[('a.py', 'b.py')]['coChangeCount'], pairs[('a.py', 'b.py')]['score']), (3, 1.0))

        self.assertEqual(sum(week['commits'] for week in value['timeline']), value['commitCount'])
        self.assertEqual(value['commitCount'], 9)
        self.assertEqual(sum(week['bugfix'] for week in value['timeline']), 1)
        self.assertGreaterEqual(len(value['timeline']), 26)
        self.assertFalse(value['truncated'])

    def test_result_matches_the_shared_response_schema(self) -> None:
        validate('response', {'protocolVersion': 1, 'requestId': str(uuid.uuid4()), 'ok': True,
                              'result': self.projects.history(self.project_id)})
        validate('request', {'protocolVersion': 1, 'requestId': str(uuid.uuid4()), 'operation': 'workspace.history.analyze',
                             'projectId': self.project_id, 'payload': {}})

    def test_repository_without_commits_gives_an_empty_analysis(self) -> None:
        empty = self.root / 'empty'
        empty.mkdir()
        subprocess.run([str(self.executable), 'init', '-q', '-b', 'main', str(empty)], check=True, capture_output=True,
                       env={**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull})
        project = self.projects.register(self.projects.inspect(str(empty), [], False)['selectionId'], str(uuid.uuid4()))['id']
        value = self.projects.history(project)
        self.assertEqual((value['commitCount'], value['files'], value['pairs']), (0, [], []))
