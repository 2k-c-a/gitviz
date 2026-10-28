from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from unittest.mock import patch

from app.workspace.detection import Detection
from app.workspace.scoped_files import ScopeError
from tests.workspace.support import WorkspaceFixture


class ChangesetLimitTests(WorkspaceFixture):
    def test_detect_does_not_walk_unrelated_project_files_or_call_git(self):
        (self.repo / 'pyproject.toml').write_text('[tool.unittest]\n')
        def missing_tool(_kind: str, _root: Path) -> None:
            return None
        detector = Detection(self.snapshots, missing_tool)
        with patch('app.workspace.scoped_files.os.walk', side_effect=AssertionError('no full inventory')):
            with patch.object(self.projects.git, '_run', side_effect=AssertionError('no git')):
                result = detector.detect(self.project_id, {'cwdRelative': '.', 'packageManager': None})
        self.assertEqual(result['candidates'][0]['name'], 'unittest')

    def test_git_stdout_limit_is_typed_and_path_free(self):
        grant = self.projects.grant(self.project_id)
        # Real Git emits a commit message beyond its unchanged 256 KiB stdout cap.
        process = subprocess.run([str(self.executable), '-C', str(self.repo), 'commit', '--allow-empty', '-F', '-'],
            input=b'x' * 270000, capture_output=True, check=True)
        self.assertEqual(process.returncode, 0)
        with self.assertRaises(ScopeError) as caught:
            getattr(self.projects.git, '_run')(grant, ['log', '-1', '--format=%B'])
        self.assertEqual(caught.exception.code, 'CHANGESET_TOO_LARGE')
        self.assertEqual(caught.exception.details['limitBytes'], 262144)
        observed = caught.exception.details['observedBytes']
        assert isinstance(observed, int)
        self.assertGreater(observed, 262144)
        self.assertNotIn(str(self.repo), str(caught.exception.details))

    def test_status_array_limit_is_typed_before_rpc_serialization(self):
        original = getattr(self.projects.git, '_run')
        def run(grant: dict[str, str], args: list[str]) -> bytes:
            if args[0] == 'status':
                return b''.join(f'? u{i}\0'.encode() for i in range(2001))
            return original(grant, args)
        with patch.object(self.projects.git, '_run', side_effect=run):
            with self.assertRaises(ScopeError) as caught:
                self.projects.status(self.project_id, 1)
        self.assertEqual(caught.exception.code, 'CHANGESET_TOO_LARGE')
        self.assertEqual(caught.exception.details['limitItems'], 2000)

    def test_scope_pin_limit_is_typed_and_capture_cannot_hide_it(self):
        # Distinct names with a mocked pin exercise the unchanged item cap cheaply.
        rows: list[tuple[str, list[str], list[str]]] = [(str(self.repo), [], [f'f{i}' for i in range(20001)])]
        with patch('app.workspace.scoped_files._pin'):
            with patch('app.workspace.scoped_files.os.walk', return_value=iter(rows)):
                with self.assertRaises(ScopeError) as caught:
                    self.snapshots.capture(self.project_id, {'items': [{'facet': 'selected', 'path': 'app.py'}],
                        'includeUntracked': False}, str(uuid.uuid4()))
        self.assertEqual(caught.exception.code, 'CHANGESET_TOO_LARGE')
        self.assertEqual(caught.exception.details['observedItems'], 20001)
