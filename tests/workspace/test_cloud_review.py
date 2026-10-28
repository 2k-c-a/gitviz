from __future__ import annotations

import base64
import json
import time
import uuid
import unittest
from decimal import Decimal
from typing import Any

from app.workspace.contracts import validate
from app.workspace.remote_connections import RemoteConnections
from app.workspace.review import Reviews
from app.workspace.scoped_files import ScopeError
from tests.workspace.support import WorkspaceFixture
from tests.workspace.test_review import output


class CloudReviewTests(WorkspaceFixture):
    def setUp(self) -> None:
        super().setUp()
        self.git('remote', 'add', 'origin', 'https://github.com/o/r')
        self.remote = RemoteConnections(self.projects)
        inspected = self.remote.inspect(self.project_id)
        self.remote.connect(self.project_id, {'phase': 'connect', 'expectedVersion': inspected['projectVersion'],
            'accountId': '11', 'serverOrigin': 'https://fixture.invalid', 'repositoryId': 2, 'githubId': 123,
            'canonicalUrl': 'https://github.com/o/r', 'remoteName': 'origin', 'configHash': inspected['configHash']}, str(uuid.uuid4()))
        self.reviews = Reviews(self.snapshots)
        self.snapshot = self.capture([{'facet': 'selected', 'path': 'app.py'}])
        self.reference = str(uuid.uuid4())

    def tearDown(self) -> None:
        self.reviews.jobs.close()
        super().tearDown()

    def authorize(self) -> dict[str, Any]:
        connection = self.remote.inspect(self.project_id)['connection']
        proof = {name: connection[name] for name in ('connectionId', 'accountId', 'serverOrigin', 'repositoryId', 'githubId', 'canonicalUrl', 'configHash')}
        proof.update(visibility='public', expiresAt=time.time() + 30)
        payload = {'phase': 'authorize', 'profileId': 'gpt-5.4-v1', 'credentialRef': self.reference, 'credentialVersion': 1, 'proof': proof}
        validate('request', {'protocolVersion': 1, 'requestId': str(uuid.uuid4()), 'projectId': self.project_id,
                            'operation': 'workspace.reviews.cloudContext', 'payload': payload})
        self.reviews.cloud.authorize(self.project_id, payload)
        return payload

    def preview(self, generation: int = 1) -> dict[str, Any]:
        result = self.reviews.preview(self.project_id, {'snapshotId': self.snapshot['snapshotId'], 'kind': 'review',
            'purpose': 'Review selected input', 'profileId': 'gpt-5.4-v1', 'generation': generation, 'credentialRef': self.reference})
        validate('response', {'protocolVersion': 1, 'requestId': str(uuid.uuid4()), 'ok': True, 'result': result})
        return result

    def test_private_unknown_unmapped_expired_and_config_drift_are_blocked(self) -> None:
        with self.assertRaises(ScopeError):
            self.preview()
        payload = self.authorize()
        for altered in ({'visibility': 'private'}, {'accountId': 'other'}, {'githubId': 124}, {'expiresAt': time.time() - 1}):
            with self.subTest(altered=altered), self.assertRaises(ScopeError):
                self.reviews.cloud.authorize(self.project_id, payload | {'proof': dict(payload['proof']) | altered})
        self.git('remote', 'set-url', 'origin', 'https://github.com/other/repo')
        with self.assertRaises(ScopeError):
            self.preview()

    def test_agent_contract_rejects_api_keys_and_unapproved_proof_fields(self) -> None:
        payload = self.authorize()
        with self.assertRaises(Exception):
            validate('request', {'protocolVersion': 1, 'requestId': str(uuid.uuid4()), 'projectId': self.project_id,
                'operation': 'workspace.reviews.cloudContext', 'payload': payload | {'apiKey': 'must-never-cross-stdio'}})

    def test_fake_main_exchange_validates_output_and_settles_real_cost_without_key(self) -> None:
        self.authorize()
        preview = self.preview()
        self.assertGreater(Decimal(str(preview['maxEstimatedCost'])), 0)
        payload = {name: preview[name] for name in ('previewId', 'previewHash', 'manifestHash', 'maxEstimatedCost')}
        payload.update(acknowledged=True, duplicateRiskAcknowledged=False)
        key = str(uuid.uuid4())
        run = self.reviews.start(self.project_id, payload, key)['runId']
        taken: dict[str, object] = {}
        for _ in range(100):
            taken = self.reviews.cloud_exchange(self.project_id, {'phase': 'take', 'runId': run})
            if taken['ready']:
                break
            time.sleep(.01)
        validate('response', {'protocolVersion': 1, 'requestId': str(uuid.uuid4()), 'ok': True, 'result': taken})
        self.assertTrue(taken['ready'])
        with self.assertRaises(ScopeError):
            self.reviews.cloud_exchange(self.project_id, {'phase': 'take', 'runId': run})
        wire = json.loads(base64.b64decode(str(taken['bodyBase64'])))
        sent = json.loads(wire['input'])['files'][0]
        response = {'model': 'gpt-5.4-2026-03-05', 'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(output(sent))}]}],
            'usage': {'input_tokens': 100, 'output_tokens': 50}}
        self.reviews.cloud_exchange(self.project_id, {'phase': 'complete', 'runId': run, 'httpStatus': 200,
            'rawBase64': base64.b64encode(json.dumps(response).encode()).decode(), 'errorCode': None, 'uncertain': False})
        result: dict[str, Any] = {}
        for _ in range(100):
            result = self.reviews.jobs.get(self.project_id, run)
            if result['status'] != 'running':
                break
            time.sleep(.01)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(self.reviews.start(self.project_id, payload, key)['runId'], run)
        actual, settled = self.db.execute('SELECT actual_cost,settled_at FROM local_usage_ledger').fetchone()
        self.assertEqual(Decimal(actual), Decimal('0.001000'))
        self.assertIsNotNone(settled)
        self.assertNotIn('apiKey', json.dumps(self.reviews.cloud.values))
        old_reference = self.reference
        self.reference = str(uuid.uuid4())
        self.authorize()
        second = self.preview()
        self.assertEqual(second['profileFingerprint'], preview['profileFingerprint'])
        second_payload = {name: second[name] for name in ('previewId', 'previewHash', 'manifestHash', 'maxEstimatedCost')}
        second_payload.update(acknowledged=True, duplicateRiskAcknowledged=False)
        repeated = self.reviews.start(self.project_id, second_payload, str(uuid.uuid4()))
        self.assertEqual((repeated['runId'], repeated['credentialRef']), (run, old_reference))
        self.assertEqual(self.db.execute('SELECT count(*) FROM local_usage_ledger').fetchone()[0], 1)

    def test_uncertain_reservation_is_not_released_or_bypassed_by_new_credential_reference(self) -> None:
        self.authorize()
        preview = self.preview()
        payload = {name: preview[name] for name in ('previewId', 'previewHash', 'manifestHash', 'maxEstimatedCost')}
        payload.update(acknowledged=True, duplicateRiskAcknowledged=False)
        run = self.reviews.start(self.project_id, payload, str(uuid.uuid4()))['runId']
        for _ in range(100):
            if self.reviews.cloud_exchange(self.project_id, {'phase': 'take', 'runId': run})['ready']:
                break
            time.sleep(.01)
        self.reviews.cloud_exchange(self.project_id, {'phase': 'complete', 'runId': run, 'httpStatus': 0,
            'rawBase64': '', 'errorCode': 'PROVIDER_ERROR', 'uncertain': True})
        result: dict[str, Any] = {}
        for _ in range(100):
            result = self.reviews.jobs.get(self.project_id, run)
            if result['status'] != 'running':
                break
            time.sleep(.01)
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(self.db.execute('SELECT actual_cost,settled_at,outcome_uncertain FROM local_usage_ledger').fetchone(), (None, None, 1))
        self.db.execute("UPDATE local_usage_ledger SET reserved_cost='20'")
        self.reference = str(uuid.uuid4())
        self.authorize()
        second = self.preview(2)
        second_payload = {name: second[name] for name in ('previewId', 'previewHash', 'manifestHash', 'maxEstimatedCost')}
        second_payload.update(acknowledged=True, duplicateRiskAcknowledged=True)
        with self.assertRaises(ScopeError) as caught:
            self.reviews.start(self.project_id, second_payload, str(uuid.uuid4()))
        self.assertEqual(caught.exception.code, 'BUDGET_EXCEEDED')
        self.assertEqual(self.db.execute('SELECT count(*) FROM local_usage_ledger').fetchone()[0], 1)


class CloudCodecTests(unittest.TestCase):
    def test_registered_provider_wire_output_and_tool_rejection_without_network(self) -> None:
        from app.workspace.adapters import ai_core
        from app.workspace.cloud_transport import body, parse
        from app.workspace.review_transport import TransportFailure
        for profile_id in ('claude-opus-5-5-v1', 'claude-sonnet-5-5-v1', 'gpt-5.4-v1', 'gemini-3.5-flash-lite-v1'):
            with self.subTest(profile=profile_id):
                base = ai_core.profile(profile_id, {})
                profile = {'id': base.id, 'provider': base.provider, 'model': base.model, 'maxOutputTokens': base.max_output_tokens,
                           'effort': base.effort, 'schemaVersion': 'workspace-review-v1'}
                wire = json.loads(body(profile, 'fixed system', 'fixed user'))
                self.assertNotIn('tools', wire)
                text = json.dumps({'schemaVersion': 'workspace-review-v1', 'findings': [], 'limitations': []})
                if base.provider == 'anthropic':
                    response = {'model': base.model, 'type': 'message', 'role': 'assistant', 'stop_reason': 'end_turn',
                        'content': [{'type': 'thinking', 'thinking': 'not exposed'}, {'type': 'text', 'text': text}], 'usage': {'input_tokens': 10, 'output_tokens': 5}}
                    invalid = response | {'content': [{'type': 'tool_use', 'name': 'must-not-execute'}]}
                elif base.provider == 'openai':
                    response = {'model': base.model, 'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}], 'usage': {'input_tokens': 10, 'output_tokens': 5}}
                    invalid = response | {'output': [{'type': 'function_call', 'name': 'must-not-execute'}]}
                else:
                    response = {'modelVersion': base.model, 'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': text}]}}], 'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 5}}
                    invalid = response | {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'functionCall': {'name': 'must-not-execute'}}]}}]}
                reply = parse(profile, json.dumps(response).encode())
                self.assertEqual(json.loads(reply.raw)['findings'], [])
                self.assertEqual(reply.usage, {'inputTokens': 10, 'outputTokens': 5})
                with self.assertRaises(TransportFailure):
                    parse(profile, json.dumps(invalid).encode())
