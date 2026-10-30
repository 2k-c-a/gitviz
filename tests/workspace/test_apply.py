from __future__ import annotations

import difflib
import hashlib
import json
import time
import unittest
import uuid
from pathlib import Path
from typing import Any

from app.workspace.apply import Application
from app.workspace.contracts import validate
from app.workspace.proposals import Proposals
from app.workspace.refactor_models import validate_refactor
from app.workspace.review import Reviews
from app.workspace.review_transport import Reply
from app.workspace.scoped_files import ScopeError
from tests.workspace.support import WorkspaceFixture


def uid() -> str:
    return str(uuid.uuid4())


def refactor_output(user: str) -> dict[str, Any]:
    content = json.loads(user)
    changes: list[dict[str, Any]] = []
    for file in content['files']:
        source = file['source']
        target = source.replace(' = 1', ' = 9', 1).replace(' = 2', ' = 8', 1).replace(' = 9', ' = 7', 1)
        if target == source:
            target = source + '\n# small fixture refactor\n'
        patch = '\n'.join(difflib.unified_diff(source.lstrip('\ufeff').splitlines(), target.lstrip('\ufeff').splitlines(),
            fromfile='a/' + file['path'], tofile='b/' + file['path'], lineterm='')) + '\n'
        changes.append({k: file[k] for k in ('fileId', 'facet', 'path', 'preimageHash')} | {'patch': patch})
    return {'schemaVersion': 'workspace-refactor-v1', 'purpose': content['purpose'], 'expectedEffects': ['Predicted readability only.'],
        'risks': ['Not executed.'], 'validationSuggestions': ['Run a separately approved unit plan.'], 'changes': changes, 'limitations': []}


class RefactorValidatorTests(unittest.TestCase):
    def test_target_hash_is_calculated_and_forged_preimage_rejected(self) -> None:
        source = b'x = 1\r\nprint(x)\r\n'
        file = {'fileId': uid(), 'facet': 'selected', 'path': 'a.py', 'source': source.decode(), 'preimageHash': hashlib.sha256(source).hexdigest()}
        raw = refactor_output(json.dumps({'purpose': 'fixture', 'files': [file]}))
        checked = validate_refactor(json.dumps(raw).encode(), [file])
        self.assertEqual(checked['changes'][0]['targetHash'], hashlib.sha256(b'x = 7\r\nprint(x)\r\n').hexdigest())
        raw['changes'][0]['preimageHash'] = '0' * 64
        with self.assertRaises(ScopeError):
            validate_refactor(json.dumps(raw).encode(), [file])


class ApplyFixture(WorkspaceFixture):
    def setUp(self) -> None:
        super().setUp()
        self.calls = 0
        def transport(profile: dict[str, Any], system: str, user: str, cancelled: Any) -> Reply:
            self.calls += 1
            return Reply(json.dumps(refactor_output(user)).encode())
        self.reviews = Reviews(self.snapshots, transport)
        self.proposals = Proposals(self.reviews)
        self.application = Application(self.proposals)

    def tearDown(self) -> None:
        self.reviews.jobs.close()
        super().tearDown()

    def generated(self, *, facet: str = 'selected', path: str = 'app.py', purpose: str = 'small fixture') -> tuple[dict[str, Any], dict[str, Any]]:
        snapshot = self.capture([{'facet': facet, 'path': path}], facet == 'untracked')
        preview = self.reviews.preview(self.project_id, {'snapshotId': snapshot['snapshotId'], 'kind': 'refactor',
            'purpose': purpose, 'profileId': 'ollama-local-v1', 'generation': 1})
        return preview, self.finish_preview(preview)

    def finish_preview(self, preview: dict[str, Any]) -> dict[str, Any]:
        consent = {k: preview[k] for k in ('previewId', 'previewHash', 'manifestHash', 'maxEstimatedCost')}
        run = self.reviews.start(self.project_id, {**consent, 'acknowledged': True, 'duplicateRiskAcknowledged': False}, uid())
        for _ in range(200):
            result = self.reviews.jobs.get(self.project_id, run['runId'])
            if result['status'] not in ('queued', 'running'):
                self.assertEqual(result['status'], 'succeeded')
                return result
            time.sleep(.005)
        self.fail('fixture timeout')

    def pending(self) -> dict[str, Any]:
        _, run = self.generated(purpose='small fixture ' + uid())
        return self.proposals.create(self.project_id, {'refactorRunId': run['runId']}, uid())

    def approved(self, proposal: dict[str, Any]) -> dict[str, Any]:
        token = 'a' * 64
        result = self.proposals.approve(self.project_id, {k: proposal[k] for k in ('proposalId', 'patchHash', 'preconditionsHash')} |
            {'expectedVersion': proposal['version'], 'capability': token}, uid())
        return {'proposalId': proposal['proposalId'], 'approvalId': result['approvalId'], 'capability': token}



class ApplyTests(ApplyFixture):
    def test_two_file_publication_uses_the_same_locked_conditions(self) -> None:
        (self.repo / 'app.ts').write_bytes(b'const value = 2;\n')
        snapshot = self.capture([{'facet': 'selected', 'path': 'app.py'}, {'facet': 'selected', 'path': 'app.ts'}])
        preview = self.reviews.preview(self.project_id, {'snapshotId': snapshot['snapshotId'], 'kind': 'refactor',
            'purpose': 'two file fixture', 'profileId': 'ollama-local-v1', 'generation': 1})
        run = self.finish_preview(preview)
        proposal = self.proposals.create(self.project_id, {'refactorRunId': run['runId']}, uid())
        payload = self.approved(proposal)
        self.assertEqual(self.application.apply(self.project_id, payload, uid())['status'], 'applied')

    def test_no_unapproved_write_native_capability_binding_idempotency_and_index_preservation(self) -> None:
        before = self.hashes()
        proposal = self.pending()
        validate('response', {'protocolVersion': 1, 'requestId': uid(), 'ok': True, 'result': proposal})
        self.assertEqual(before, self.hashes())
        with self.assertRaises(ScopeError):
            self.application.apply(self.project_id, {'proposalId': proposal['proposalId'], 'approvalId': uid(), 'capability': '0' * 64}, uid())
        self.assertEqual(before, self.hashes())
        payload = self.approved(proposal)
        with self.assertRaises(ScopeError):
            self.application.apply(self.project_id, {**payload, 'capability': 'b' * 64}, uid())
        key = uid()
        result = self.application.apply(self.project_id, payload, key)
        self.assertEqual(result['status'], 'applied')
        self.assertEqual((self.repo / 'app.py').read_bytes(), b'x = 7\r\nprint(x)\r\n')
        after = self.hashes()
        self.assertEqual(before[str(Path('.git') / 'index')], after[str(Path('.git') / 'index')])
        self.assertEqual(self.application.apply(self.project_id, payload, key)['status'], 'applied')
        self.assertEqual(after, self.hashes())
        self.assertEqual(self.calls, 1)

    def test_external_edit_and_staged_worktree_mismatch_are_not_overwritten(self) -> None:
        proposal = self.pending()
        payload = self.approved(proposal)
        (self.repo / 'app.py').write_bytes(b'external editor\r\n')
        with self.assertRaises(ScopeError):
            self.application.apply(self.project_id, payload, uid())
        self.assertEqual((self.repo / 'app.py').read_bytes(), b'external editor\r\n')
        (self.repo / 'app.py').write_bytes(b'x = 2\r\n')
        self.git('add', 'app.py')
        (self.repo / 'app.py').write_bytes(b'x = 3\r\n')
        _, run = self.generated(facet='staged')
        with self.assertRaises(ScopeError):
            self.proposals.create(self.project_id, {'refactorRunId': run['runId']}, uid())
        self.assertEqual((self.repo / 'app.py').read_bytes(), b'x = 3\r\n')

    def test_reject_and_revision_revoke_old_approval_and_preserve_lineage(self) -> None:
        proposal = self.pending()
        payload = self.approved(proposal)
        current = self.proposals.get(self.project_id, proposal['proposalId'])
        preview = self.proposals.request_revision(self.project_id, {'proposalId': proposal['proposalId'],
            'expectedVersion': current['version'], 'instruction': 'Keep the same small scope.'}, uid())
        self.assertEqual(self.proposals.get(self.project_id, proposal['proposalId'])['status'], 'superseded')
        with self.assertRaises(ScopeError):
            self.application.apply(self.project_id, payload, uid())
        run = self.finish_preview(preview)
        child = self.proposals.create(self.project_id, {'refactorRunId': run['runId']}, uid())
        self.assertNotEqual(child['proposalId'], proposal['proposalId'])
        self.assertEqual(child['previousProposalId'], proposal['proposalId'])
        rejected = self.proposals.reject(self.project_id, {'proposalId': child['proposalId'], 'expectedVersion': child['version']}, uid())
        self.assertEqual(rejected['status'], 'rejected')
        self.assertEqual((self.repo / 'app.py').read_bytes(), b'x = 1\r\nprint(x)\r\n')

    def test_interrupted_publish_requires_explicit_recovery_and_preserves_third_party_creator(self) -> None:
        proposal = self.pending()
        payload = self.approved(proposal)
        def interrupt(phase: str) -> None:
            if phase == 'original_moved_before_record':
                raise RuntimeError('fixture interruption')
        with self.assertRaises(ScopeError):
            self.application.apply(self.project_id, payload, uid(), interrupt)
        self.assertFalse((self.repo / 'app.py').exists())
        self.assertEqual(self.proposals.get(self.project_id, proposal['proposalId'])['status'], 'recovery_required')
        (self.repo / 'app.py').write_bytes(b'third party created this')
        with self.assertRaises(ScopeError):
            self.application.recovery_plan(self.project_id, proposal['proposalId'])
        self.assertEqual((self.repo / 'app.py').read_bytes(), b'third party created this')

    def test_gap_recovery_and_post_publish_recovery_restore_only_application_targets(self) -> None:
        for phase in ['original_moved_before_record', 'target_published_before_record']:
            with self.subTest(phase=phase):
                proposal = self.pending()
                payload = self.approved(proposal)
                def interrupt(at: str) -> None:
                    if at == phase:
                        raise RuntimeError('fixture interruption')
                with self.assertRaises(ScopeError):
                    self.application.apply(self.project_id, payload, uid(), interrupt)
                plan = self.application.recovery_plan(self.project_id, proposal['proposalId'])
                approval = self.application.approve_recovery(self.project_id, {'proposalId': proposal['proposalId'], 'recoveryHash': plan['recoveryHash'], 'capability': 'c' * 64}, uid())
                result = self.application.recover(self.project_id, {'proposalId': proposal['proposalId'], 'approvalId': approval['approvalId'], 'recoveryHash': plan['recoveryHash'], 'capability': 'c' * 64}, uid())
                self.assertEqual(result['status'], 'restored')
                self.assertEqual((self.repo / 'app.py').read_bytes(), b'x = 1\r\nprint(x)\r\n')
