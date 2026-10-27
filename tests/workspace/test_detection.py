from __future__ import annotations

import json
import uuid

from app.workspace.detection import Detection
from app.workspace.scoped_files import ScopeError
from tests.workspace.support import WorkspaceFixture


class DetectionTests(WorkspaceFixture):
    def setUp(self) -> None:
        super().setUp()
        self.tool = self.root / 'fixed-runtime.exe'
        self.tool.write_bytes(b'fixture executable, never executed')
        self.detector = Detection(self.snapshots, lambda _kind, _root: (str(self.tool), []))

    def detect(self, cwd: str = '.', manager: str | None = None):
        return self.detector.detect(self.project_id, {'cwdRelative': cwd, 'packageManager': manager})

    def test_scripts_hooks_plan_preconditions_replay_and_no_execution(self) -> None:
        marker = self.repo / 'EXECUTED'
        (self.repo / 'package.json').write_text(json.dumps({'scripts': {'pretest': f'echo side-effect > {marker}', 'test': 'node test.js', 'posttest': 'node post.js', 'lint': 'eslint .'}}))
        (self.repo / 'package-lock.json').write_text('{}')
        (self.repo / 'setup.py').write_text('raise RuntimeError("must not import")\n')
        before = self.hashes()
        detected = self.detect()
        self.assertEqual(len(detected['candidates']), 2)
        candidate = detected['candidates'][0]
        self.assertIn('pretest', candidate['scripts'])
        snapshot = self.capture([{'facet': 'selected', 'path': 'app.py'}])
        payload = {'snapshotId': snapshot['snapshotId'], 'candidateId': candidate['candidateId']}
        key = str(uuid.uuid4())
        plan = self.detector.plan(self.project_id, payload, key)
        self.assertFalse(plan['executionAllowed'])
        self.assertEqual(plan['argv'], ['run', 'test'])
        self.assertEqual(plan, self.detector.plan(self.project_id, payload, key))
        self.assertNotIn('side-effect', self.db.execute("SELECT result FROM local_operation WHERE operation='validation.plan'").fetchone()[0])
        self.assertIn('not-sandboxed', plan['risk'])
        self.assertEqual(before, self.hashes())
        self.assertFalse(marker.exists())
        (self.repo / 'package.json').write_text('{"scripts":{"test":"different"}}')
        with self.assertRaisesRegex(ScopeError, 'CAPTURE_CHANGED'):
            self.detector.plan(self.project_id, payload, str(uuid.uuid4()))

    def test_lock_conflicts_explicit_choice_monorepo_and_missing_runtime(self) -> None:
        sub = self.repo / 'sub'
        sub.mkdir()
        (sub / 'package.json').write_text('{"scripts":{"test":"exit 1"}}')
        (sub / 'yarn.lock').write_text('lock')
        (sub / 'pnpm-lock.yaml').write_text('lock')
        self.assertEqual(self.detect('sub')['candidates'], [])
        chosen = self.detect('sub', 'pnpm')
        self.assertEqual(chosen['candidates'][0]['packageManager'], 'pnpm')
        missing = Detection(self.snapshots, lambda _kind, _root: None).detect(self.project_id, {'cwdRelative': 'sub', 'packageManager': 'pnpm'})
        self.assertFalse(missing['candidates'][0]['available'])
        with self.assertRaisesRegex(ScopeError, 'PATH_OUTSIDE_SCOPE'):
            self.detect('../')

    def test_python_config_and_credentials_are_not_executed_or_exposed(self) -> None:
        (self.repo / 'pyproject.toml').write_text('[tool.pytest.ini_options]\ntestpaths=["tests"]\n[tool.ruff]\nline-length=100\n')
        found = self.detect()
        self.assertEqual({c['name'] for c in found['candidates']}, {'pytest', 'ruff'})
        (self.repo / '.npmrc').write_text('SECRET_CREDENTIAL_CANARY')
        blocked = self.detect()
        self.assertEqual(blocked['candidates'], [])
        self.assertNotIn('SECRET_CREDENTIAL_CANARY', json.dumps(blocked))
