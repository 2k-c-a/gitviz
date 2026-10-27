from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any
from unittest.mock import patch

from app.workspace.comparisons import Comparisons
from app.workspace.contracts import validate
from app.workspace.detection import Detection
from app.workspace.scoped_files import ScopeError
from app.workspace.validation import Validation
from tests.workspace.support import WorkspaceFixture


def uid() -> str:
    return str(uuid.uuid4())


class ObservedValidation(Validation):
    def __init__(self, detection: Detection):
        self.observed: list[dict[str, Any]] = []
        super().__init__(detection)

    def conditions(self, project: str, envelope: dict[str, Any], *, grant: dict[str, str] | None = None,
                   snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        result = super().conditions(project, envelope, grant=grant, snapshot=snapshot)
        self.observed.append(result)
        return result


class ValidationTests(WorkspaceFixture):
    def setUp(self) -> None:
        super().setUp()
        (self.repo / 'pyproject.toml').write_text('[tool.unittest]\n', encoding='utf-8')
        (self.repo / 'test_fixture.py').write_text('import unittest,app\nclass Check(unittest.TestCase):\n def test_value(self): self.assertEqual(app.x,1)\n', encoding='utf-8')
        self.detection = Detection(self.snapshots, lambda _kind, _root: (str(getattr(sys, '_base_executable', sys.executable)), []))
        self.validation = ObservedValidation(self.detection)
        self.comparisons = Comparisons(self.validation)

    def tearDown(self) -> None:
        self.validation.close()
        for _ in range(200):
            self.validation.drain()
            if self.validation.active is None:
                break
            time.sleep(.01)
        super().tearDown()

    def plan(self) -> dict[str, Any]:
        snapshot = self.capture([{'facet': 'selected', 'path': 'app.py'}])
        detected = self.detection.detect(self.project_id, {'cwdRelative': '.', 'packageManager': None})
        return self.detection.plan(self.project_id, {'snapshotId': snapshot['snapshotId'], 'candidateId': detected['candidates'][0]['candidateId']}, uid())

    def accepted(self, plan: dict[str, Any]) -> dict[str, Any]:
        prepared = self.validation.prepare(self.project_id, {'planId': plan['planId']})
        approval = self.validation.approve(self.project_id, {'planId': plan['planId'], 'confirmationHash': prepared['confirmationHash'],
            'capability': 'b' * 64, 'trusted': True}, uid())
        return {'planId': plan['planId'], 'approvalId': approval['validationApprovalId'], 'capability': 'b' * 64}

    def finish(self, run: dict[str, Any]) -> dict[str, Any]:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            result = self.validation.get(self.project_id, {'runId': run['runId'], 'offset': 0, 'limit': 10})
            if result['status'] != 'running':
                validate('response', {'protocolVersion': 1, 'requestId': uid(), 'ok': True, 'result': result})
                return result
            time.sleep(.01)
        self.fail('fixture runner deadline')

    def test_no_trust_or_capability_no_run_and_consumption_replay(self) -> None:
        plan = self.plan()
        with self.assertRaisesRegex(ScopeError, 'APPROVAL_REQUIRED'):
            self.validation.start(self.project_id, {'planId': plan['planId'], 'approvalId': uid(), 'capability': 'b' * 64}, uid())
        self.assertEqual(self.db.execute('SELECT count(*) FROM validation_run').fetchone()[0], 0)
        payload = self.accepted(plan)
        key = uid()
        try:
            run = self.validation.start(self.project_id, payload, key)
        except ScopeError:
            left, right = self.validation.observed[-2:]
            delta = {field: (left[field] is None, right[field] is None) for field in left if left[field] != right[field]}
            self.fail('condition classification: ' + str(delta))
        self.assertEqual(self.finish(run)['status'], 'succeeded')
        replay = self.validation.start(self.project_id, payload, key)
        self.assertEqual(run['runId'], replay['runId'])
        with self.assertRaisesRegex(ScopeError, 'APPROVAL_REQUIRED'):
            self.validation.start(self.project_id, payload, uid())
        self.assertEqual(self.db.execute('SELECT count(*) FROM validation_run').fetchone()[0], 1)
        blob = self.db.execute('SELECT payload FROM validation_run').fetchone()[0]
        self.assertNotIn(b'Ran 1 test', blob)
        self.assertNotIn('b' * 64, json.dumps(self.db.execute('SELECT result FROM local_operation').fetchall()))

    def test_config_source_runtime_environment_and_foreign_project_are_rechecked(self) -> None:
        plan = self.plan()
        payload = self.accepted(plan)
        (self.repo / 'app.py').write_text('x = 9\n', encoding='utf-8')
        with self.assertRaisesRegex(ScopeError, 'PRECONDITION_FAILED'):
            self.validation.start(self.project_id, payload, uid())
        (self.repo / 'app.py').write_bytes(b'x = 1\r\nprint(x)\r\n')
        (self.repo / 'pyproject.toml').write_text('[tool.unittest]\nchanged=true\n', encoding='utf-8')
        with self.assertRaisesRegex(ScopeError, 'PRECONDITION_FAILED'):
            self.validation.start(self.project_id, payload, uid())
        with self.assertRaisesRegex(ScopeError, 'RESOURCE_NOT_FOUND'):
            self.validation.get(uid(), {'runId': uid(), 'offset': 0, 'limit': 10})
        (self.repo / 'pyproject.toml').write_text('[tool.unittest]\n', encoding='utf-8')
        with patch.dict(os.environ, {'TEMP': str(self.root / 'changed-environment')}), self.assertRaisesRegex(ScopeError, 'COMMAND_NOT_ALLOWED'):
            self.validation.start(self.project_id, payload, uid())
        executable = self.root / 'controlled-runtime.exe'
        executable.write_bytes(b'MZ fixture never executed')
        detection = Detection(self.snapshots, lambda _kind, _root: (str(executable), []))
        candidate = detection.detect(self.project_id, {'cwdRelative': '.', 'packageManager': None})['candidates'][0]
        controlled = detection.plan(self.project_id, {'snapshotId': plan['snapshotId'], 'candidateId': candidate['candidateId']}, uid())
        self.validation = ObservedValidation(detection)
        approved = self.accepted(controlled)
        executable.write_bytes(b'MZ changed fixture never executed')
        with self.assertRaisesRegex(ScopeError, 'PRECONDITION_FAILED'):
            self.validation.start(self.project_id, approved, uid())
        self.assertEqual(self.db.execute('SELECT count(*) FROM validation_run').fetchone()[0], 0)

    def test_real_baseline_then_changed_result_and_missing_baseline(self) -> None:
        before = self.plan()
        baseline = self.finish(self.validation.start(self.project_id, self.accepted(before), uid()))
        self.assertEqual(baseline['status'], 'succeeded')
        (self.repo / 'app.py').write_text('x = 3\nprint(x)\n', encoding='utf-8')
        after = self.plan()
        result = self.finish(self.validation.start(self.project_id, self.accepted(after), uid()))
        self.assertEqual(result['status'], 'failed')
        payload = {'beforeSnapshotId': before['snapshotId'], 'afterSnapshotId': after['snapshotId'], 'proposalId': None,
            'beforeRunId': baseline['runId'], 'afterRunId': result['runId']}
        comparison = self.comparisons.create(self.project_id, payload, uid())
        validate('response', {'protocolVersion': 1, 'requestId': uid(), 'ok': True, 'result': comparison})
        self.assertEqual(comparison['changedFiles'], 1)
        self.assertEqual(comparison['validationStatus'], 'comparable')
        self.assertIsNone(comparison['testTransitions'])
        self.assertIsNone(comparison['qualityImprovement'])
        missing = self.comparisons.create(self.project_id, {**payload, 'beforeRunId': None}, uid())
        self.assertEqual(missing['validationStatus'], 'not_comparable')
        self.assertIn('baseline-not-executed', missing['reasons'])
        with self.assertRaisesRegex(ScopeError, 'PRECONDITION_FAILED'):
            self.comparisons.create(self.project_id, {**payload, 'beforeRunId': result['runId']}, uid())

    def test_restart_revokes_approval_and_marks_intent_interrupted_without_execution(self) -> None:
        plan = self.plan()
        payload = self.accepted(plan)
        run = uid()
        self.db.execute('INSERT INTO validation_run VALUES(?,?,?,?,?)', (run, self.project_id, plan['planId'], 'running',
            self.validation.encrypt(self.project_id, run, {'fixture': 'not-started'})))
        Validation(self.detection)
        self.assertEqual(self.db.execute('SELECT status FROM validation_run WHERE id=?', (run,)).fetchone()[0], 'interrupted')
        with self.assertRaisesRegex(ScopeError, 'APPROVAL_REQUIRED'):
            self.validation.start(self.project_id, payload, uid())

    def test_completed_output_is_durable_when_snapshot_expires_before_drain(self) -> None:
        plan = self.plan()
        run = self.validation.start(self.project_id, self.accepted(plan), uid())
        deadline = time.monotonic() + 30
        while self.validation.completed.empty() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.validation.completed.empty())
        with patch.object(self.snapshots, 'get', side_effect=ScopeError('INPUT_EXPIRED')):
            result = self.validation.get(self.project_id, {'runId': run['runId'], 'offset': 0, 'limit': 10})
        validate('response', {'protocolVersion': 1, 'requestId': uid(), 'ok': True, 'result': result})
        self.assertEqual(result['status'], 'succeeded')
        self.assertTrue(result['output'])
        self.assertIsNone(result['inputChanged'])
        self.assertIn('diagnostic-input-unavailable', result['limitations'])

    def test_editor_location_requires_snapshot_project_hash_and_valid_line(self) -> None:
        self.assertEqual(self.projects.get(self.project_id)['settings']['editorId']['value'], 'vscode')
        updated = self.projects.set_editor(self.project_id, {'editorId': 'cursor', 'expectedVersion': 1}, uid())
        validate('response', {'protocolVersion': 1, 'requestId': uid(), 'ok': True, 'result': updated})
        self.assertEqual(updated['settings']['editorId'], {'value': 'cursor', 'version': 2})
        with self.assertRaisesRegex(ScopeError, 'CONFLICT'):
            self.projects.set_editor(self.project_id, {'editorId': 'vscode', 'expectedVersion': 1}, uid())
        plan = self.plan()
        snapshot = self.snapshots.get(self.project_id, plan['snapshotId'])
        payload = {'snapshotId': snapshot['snapshotId'], 'fileId': snapshot['files'][0]['fileId'], 'line': 1, 'column': 1}
        location = self.validation.locate(self.project_id, payload)
        self.assertEqual(location['sourceHash'], snapshot['files'][0]['afterHash'])
        with self.assertRaisesRegex(ScopeError, 'INVALID_REQUEST'):
            self.validation.locate(self.project_id, {**payload, 'line': 999})
        (self.repo / 'app.py').write_text('changed\n', encoding='utf-8')
        with self.assertRaisesRegex(ScopeError, 'PRECONDITION_FAILED'):
            self.validation.locate(self.project_id, payload)
        (self.repo / 'app.py').write_text('# 😀x\n', encoding='utf-8')
        snapshot = self.capture([{'facet': 'selected', 'path': 'app.py'}])
        payload = {'snapshotId': snapshot['snapshotId'], 'fileId': snapshot['files'][0]['fileId'], 'line': 1, 'column': 5}
        self.assertEqual(self.validation.locate(self.project_id, payload)['column'], 5)
        with self.assertRaisesRegex(ScopeError, 'INVALID_REQUEST'):
            self.validation.locate(self.project_id, {**payload, 'column': 4})

    def test_actual_npm_pre_test_post_hooks_match_reviewed_plan(self) -> None:
        (self.repo / 'package.json').write_text(json.dumps({'scripts': {
            'pretest': 'node hook.cjs pre', 'test': 'node hook.cjs test', 'posttest': 'node hook.cjs post'}}), encoding='utf-8')
        (self.repo / 'hook.cjs').write_text('process.stdout.write("P07_PHASE_"+process.argv[2]+"\\n");\n', encoding='utf-8')
        detection = Detection(self.snapshots)
        candidates = detection.detect(self.project_id, {'cwdRelative': '.', 'packageManager': None})
        candidate = next(item for item in candidates['candidates'] if item['packageManager'] == 'npm')
        self.assertTrue(candidate['available'], 'Installed fixed Node/npm is required for this Windows acceptance')
        snapshot = self.capture([{'facet': 'selected', 'path': 'hook.cjs'}])
        plan = detection.plan(self.project_id, {'snapshotId': snapshot['snapshotId'], 'candidateId': candidate['candidateId']}, uid())
        self.assertEqual(set(plan['scripts']), {'pretest', 'test', 'posttest'})
        self.validation = ObservedValidation(detection)
        result = self.finish(self.validation.start(self.project_id, self.accepted(plan), uid()))
        self.assertEqual(result['status'], 'succeeded', ''.join(item['text'] for item in result['output'] if item['stream'] == 'stderr')[:2000])
        output = ''.join(item['text'] for item in result['output'] if item['stream'] == 'stdout')
        self.assertLess(output.index('P07_PHASE_pre'), output.index('P07_PHASE_test'))
        self.assertLess(output.index('P07_PHASE_test'), output.index('P07_PHASE_post'))
