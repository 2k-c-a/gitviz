from __future__ import annotations

import copy
from contextlib import closing
import json
import sqlite3
import threading
import time
import unittest
import uuid
from typing import Any
from unittest.mock import patch

from app.workspace.contracts import validate, encode
from app.workspace.dispatcher import Dispatcher
from app.workspace.review import Reviews
from app.workspace.review_models import validate_output
from app.workspace.review_transport import Reply, TransportFailure, pc_ollama, request_body
from app.workspace.review_schema import TABLES
from app.workspace.scoped_files import ScopeError
from tests.workspace.support import WorkspaceFixture


def uid() -> str:
    return str(uuid.uuid4())


def output(file: dict[str, Any] | None = None) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    if file:
        line = next(line for line in file['lines'] if line.get('right_line'))
        findings.append({'category': 'testing', 'priority': 'P2', 'severity': 'unknown',
            'severityReason': 'The fixture does not include tests.', 'condition': 'If this branch is reached.',
            'impact': 'Behavior requires human review.', 'evidence': [{'fileId': file['fileId'], 'facet': file['facet'],
                'path': file['path'], 'side': 'RIGHT', 'startLine': line['right_line'], 'endLine': line['right_line'], 'quote': line['text']}],
            'suggestion': 'Add a focused test.', 'verification': 'not_executed', 'limitations': ['Model inference only.']})
    return {'schemaVersion': 'workspace-review-v1', 'findings': findings, 'limitations': []}


class PureReviewTests(unittest.TestCase):
    def test_wire_payload_pins_context_schema_and_messages_without_tools(self) -> None:
        profile = {'endpoint': 'http://127.0.0.1:11434', 'provider': 'ollama', 'timeoutSeconds': 90,
            'model': 'qwen2.5-coder:7b', 'maxOutputTokens': 4000, 'contextWindow': 32768}
        with patch('app.workspace.review_transport.http.client.HTTPConnection') as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 200
            response.read.return_value = json.dumps({'model': profile['model'], 'done': True, 'done_reason': 'stop',
                'message': {'content': json.dumps(output())}, 'prompt_eval_count': 12, 'eval_count': 5}).encode()
            reply = pc_ollama(profile, 'system bytes', 'user bytes', threading.Event())
            sent = connection.return_value.request.call_args.args[2]
            self.assertEqual(sent, request_body(profile, 'system bytes', 'user bytes'))
            wire = json.loads(sent)
            self.assertEqual(wire['options'], {'num_ctx': 32768, 'num_predict': 4000})
            self.assertEqual(wire['format']['additionalProperties'], False)
            self.assertNotIn('tools', wire)
            self.assertEqual(reply.usage, {'inputTokens': 12, 'outputTokens': 5})

    def test_connection_time_is_subtracted_from_total_provider_deadline(self) -> None:
        profile = {'endpoint': 'http://127.0.0.1:11434', 'provider': 'ollama', 'timeoutSeconds': 90,
            'model': 'qwen2.5-coder:7b', 'maxOutputTokens': 4000, 'contextWindow': 32768}
        with patch('app.workspace.review_transport.http.client.HTTPConnection') as connection, \
                patch('app.workspace.review_transport.time.monotonic', side_effect=[100, 125]), \
                patch('app.workspace.review_transport.threading.Timer') as timer:
            connection.return_value.getresponse.return_value.status = 302
            with self.assertRaises(TransportFailure):
                pc_ollama(profile, 'system', 'user', threading.Event())
            self.assertEqual(timer.call_args.args[0], 65)
            connection.return_value.sock.settimeout.assert_called_once_with(65)

    def test_output_rejects_forged_evidence_severity_extra_keys_and_false_verification(self) -> None:
        file: dict[str, Any] = {'fileId': uid(), 'path': 'a.py', 'facet': 'staged',
            'lines': [{'kind': 'context', 'left_line': 1, 'right_line': 1, 'text': 'value = 42'}]}
        valid = output(file)
        self.assertEqual(validate_output(json.dumps(valid).encode(), [file])['findings'][0]['humanValidation'], 'unverified')
        for field, value in [('severity', 'P0'), ('verification', 'executed'), ('humanValidation', 'valid')]:
            bad = copy.deepcopy(valid)
            bad['findings'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ScopeError):
                validate_output(json.dumps(bad).encode(), [file])
        for field, value in [('fileId', uid()), ('facet', 'unstaged'), ('path', '../secret'), ('side', 'LEFT'), ('startLine', 2), ('quote', 'not sent')]:
            bad = copy.deepcopy(valid)
            bad['findings'][0]['evidence'][0][field] = value
            # LEFT line 1 is genuinely sent
            # Use a side absent from a RIGHT-only input.
            sent = [{**file, 'lines': [{**file['lines'][0], 'left_line': None}]}] if field == 'side' else [file]
            with self.subTest(field=field), self.assertRaises(ScopeError):
                validate_output(json.dumps(bad).encode(), sent)
        for raw in [b'{"schemaVersion":"workspace-review-v1","findings":[],"findings":[],"limitations":[]}', b'{' , b'x' * 65537]:
            with self.assertRaises(ScopeError):
                validate_output(raw, [file])

    def test_transport_never_redirects_proxies_or_accepts_arbitrary_destination(self) -> None:
        with patch('app.workspace.review_transport.http.client.HTTPConnection') as connection:
            with self.assertRaises(TransportFailure) as error:
                pc_ollama({'endpoint': 'http://evil.invalid', 'provider': 'ollama'}, '', '', threading.Event())
            self.assertFalse(error.exception.uncertain)
            connection.assert_not_called()
            fake = connection.return_value
            fake.getresponse.return_value.status = 302
            with self.assertRaises(TransportFailure) as error:
                pc_ollama({'endpoint': 'http://127.0.0.1:11434', 'provider': 'ollama', 'timeoutSeconds': 90,
                    'model': 'qwen2.5-coder:7b', 'maxOutputTokens': 4000, 'contextWindow': 32768}, 'system', 'user', threading.Event())
            self.assertTrue(error.exception.uncertain)
            connection.assert_called_once_with('127.0.0.1', 11434, timeout=90)
            self.assertEqual(fake.request.call_count, 1)


class ReviewTests(WorkspaceFixture):
    def setUp(self) -> None:
        super().setUp()
        self.snapshot = self.capture([{'facet': 'selected', 'path': 'app.py'}])
        self.calls: list[tuple[str, str]] = []
        self.release = threading.Event()
        self.entered = threading.Event()
        self.mode = 'success'

        def transport(profile: dict[str, Any], system: str, user: str, cancelled: threading.Event) -> Reply:
            self.calls.append((system, user))
            self.entered.set()
            if self.mode == 'blocked':
                self.release.wait(5)
            if self.mode == 'uncertain':
                raise TransportFailure('PROVIDER_TIMEOUT', True)
            if self.mode == 'rejected':
                raise TransportFailure('PROVIDER_REJECTED', False)
            files = json.loads(user)['files']
            value = output(files[0])
            if self.mode == 'invalid':
                value['findings'][0]['evidence'][0]['quote'] = 'forged source'
            return Reply(json.dumps(value).encode(), {'inputTokens': 100, 'outputTokens': 20})

        self.service = Reviews(self.snapshots, transport)

    def tearDown(self) -> None:
        self.release.set()
        # All bounded fixture transports finish before the SQLite fixture is removed.
        deadline = time.monotonic() + 6
        while self.service.jobs.active and time.monotonic() < deadline:
            self.service.jobs.drain()
            time.sleep(.005)
        self.service.jobs.close()
        super().tearDown()

    def preview(self, generation: int = 1) -> dict[str, Any]:
        return self.service.preview(self.project_id, {'snapshotId': self.snapshot['snapshotId'],
            'kind': 'review', 'purpose': 'Review this selected source.', 'profileId': 'ollama-local-v1', 'generation': generation})

    @staticmethod
    def consent(value: dict[str, Any], risk: bool = False) -> dict[str, Any]:
        return {k: value[k] for k in ('previewId', 'previewHash', 'manifestHash', 'maxEstimatedCost')} | {'acknowledged': True, 'duplicateRiskAcknowledged': risk}

    def finished(self, run: str) -> dict[str, Any]:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            value = self.service.jobs.get(self.project_id, run)
            if value['status'] not in ('queued', 'running', 'cancel_requested'):
                return value
            time.sleep(.005)
        self.fail('fixture transport did not finish')

    def test_exact_preview_consent_bytes_encryption_schema_and_source_preservation(self) -> None:
        before = self.hashes()
        value = self.preview()
        validate('response', {'protocolVersion': 1, 'requestId': uid(), 'ok': True, 'result': value})
        run = self.service.start(self.project_id, self.consent(value), uid())
        final = self.finished(run['runId'])
        self.assertEqual(final['status'], 'succeeded')
        self.assertEqual(self.calls, [(value['system'], value['user'])])
        self.assertEqual(self.hashes(), before)
        validate('response', {'protocolVersion': 1, 'requestId': uid(), 'ok': True, 'result': final})
        self.assertEqual(final['findings'][0]['humanValidation'], 'unverified')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM local_usage_ledger').fetchone()[0], 1)
        ledger = '\n'.join(row[0] for row in self.db.execute('SELECT result FROM local_operation'))
        self.assertNotIn('Review this selected source.', ledger)
        self.assertNotIn('x = 1', ledger)
        encrypted = self.db.execute('SELECT payload FROM transfer_consent').fetchone()[0]
        self.assertNotIn(b'x = 1', encrypted)

    def test_no_consent_hash_mismatch_expiry_and_cloud_fail_before_transport(self) -> None:
        value = self.preview()
        for field, changed in [('acknowledged', False), ('previewHash', 'a' * 64), ('manifestHash', 'b' * 64), ('maxEstimatedCost', '1')]:
            with self.subTest(field=field), self.assertRaises(ScopeError):
                self.service.start(self.project_id, {**self.consent(value), field: changed}, uid())
        with self.assertRaises(ScopeError):
            self.service.start(uid(), self.consent(value), uid())
        self.service.previews[value['previewId']] = (time.time() - 1, value)
        with self.assertRaises(ScopeError):
            self.service.start(self.project_id, self.consent(value), uid())
        for profile in ['gpt-5.4-v1', 'claude-opus-5-5-v1', 'https://evil.invalid']:
            with self.assertRaises(ScopeError):
                self.service.preview(self.project_id, {'snapshotId': self.snapshot['snapshotId'], 'kind': 'review', 'purpose': 'review', 'profileId': profile, 'generation': 1})
        self.assertEqual(self.calls, [])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM review_run').fetchone()[0], 0)

    def test_cloud_rejection_encodes_as_error_and_next_request_remains_usable(self) -> None:
        dispatcher = Dispatcher(self.root / 'unused-rpc-directory', lambda _: None)
        dispatcher.negotiated = True
        dispatcher.state = 'ready'
        dispatcher.projects = self.projects
        dispatcher.reviews = self.service
        request = {'protocolVersion': 1, 'requestId': uid(), 'projectId': self.project_id,
            'operation': 'workspace.reviews.preview', 'payload': {'snapshotId': self.snapshot['snapshotId'],
                'kind': 'review', 'purpose': 'review', 'profileId': 'gpt-5.4-v1', 'generation': 1}}
        rejected = dispatcher.dispatch(request)
        self.assertEqual(json.loads(encode(rejected))['error']['code'], 'CLOUD_MAPPING_REQUIRED')
        following = dispatcher.dispatch({**request, 'requestId': uid(), 'operation': 'workspace.reviews.list',
            'payload': {'limit': 1, 'offset': 0}})
        self.assertEqual(json.loads(encode(following))['result'], {'projectId': self.project_id, 'runs': []})
        self.assertEqual(self.calls, [])

    def test_same_operation_and_generation_never_resend_and_payload_conflicts(self) -> None:
        self.mode = 'blocked'
        value = self.preview()
        key = uid()
        consent = self.consent(value)
        first = self.service.start(self.project_id, consent, key)
        self.assertTrue(self.entered.wait(2))
        self.assertEqual(self.service.start(self.project_id, consent, key)['runId'], first['runId'])
        self.assertEqual(self.service.start(self.project_id, consent, uid())['runId'], first['runId'])
        with self.assertRaises(ScopeError):
            self.service.start(self.project_id, {**consent, 'duplicateRiskAcknowledged': True}, key)
        self.assertEqual(len(self.calls), 1)
        self.release.set()
        self.finished(first['runId'])

    def test_uncertain_keeps_reservation_and_requires_explicit_new_generation(self) -> None:
        self.mode = 'uncertain'
        value = self.preview()
        consent = self.consent(value)
        key = uid()
        run = self.service.start(self.project_id, consent, key)
        final = self.finished(run['runId'])
        self.assertEqual(final['status'], 'uncertain')
        self.assertEqual(self.db.execute('SELECT outcome_uncertain,settled_at FROM local_usage_ledger').fetchone(), (1, None))
        self.service = Reviews(self.snapshots, self.service.jobs.transport)
        self.assertEqual(self.service.jobs.get(self.project_id, run['runId'])['status'], 'uncertain')
        self.assertEqual(self.service.start(self.project_id, consent, key)['runId'], run['runId'])
        self.assertEqual(len(self.calls), 1)
        second = self.preview(2)
        with self.assertRaises(ScopeError):
            self.service.start(self.project_id, self.consent(second), uid())
        self.mode = 'success'
        next_run = self.service.start(self.project_id, self.consent(second, True), uid())
        self.assertEqual(self.finished(next_run['runId'])['status'], 'succeeded')
        self.assertEqual(len(self.calls), 2)

    def test_cancel_discards_complete_result_and_does_not_claim_unsent(self) -> None:
        self.mode = 'blocked'
        value = self.preview()
        run = self.service.start(self.project_id, self.consent(value), uid())
        self.assertTrue(self.entered.wait(2))
        pending = self.service.jobs.cancel(self.project_id, {'runId': run['runId'], 'expectedVersion': run['version']}, uid())
        self.assertEqual(pending['status'], 'cancel_requested')
        self.release.set()
        final = self.finished(run['runId'])
        self.assertEqual(final['status'], 'cancelled')
        self.assertEqual(final['findings'], [])
        self.assertEqual(len(self.calls), 1)

    def test_invalid_result_is_failed_not_partially_published(self) -> None:
        self.mode = 'invalid'
        value = self.preview()
        run = self.service.start(self.project_id, self.consent(value), uid())
        final = self.finished(run['runId'])
        self.assertEqual(final['status'], 'failed')
        self.assertEqual(final['errorCode'], 'OUTPUT_INVALID')
        self.assertEqual(final['findings'], [])

    def test_feedback_cas_encrypted_note_keeps_model_result_immutable(self) -> None:
        value = self.preview()
        run = self.service.start(self.project_id, self.consent(value), uid())
        final = self.finished(run['runId'])
        finding = final['findings'][0]
        original = self.db.execute('SELECT result FROM review_run WHERE id=?', (run['runId'],)).fetchone()[0]
        payload = {'findingId': finding['findingId'], 'expectedVersion': 1, 'verdict': 'false_positive', 'note': 'Human-only private note.'}
        key = uid()
        edited = self.service.feedback(self.project_id, payload, key)
        self.assertEqual(edited['findings'][0]['feedback']['version'], 2)
        self.assertEqual(edited['findings'][0]['humanValidation'], 'unverified')
        self.assertEqual(self.service.feedback(self.project_id, payload, key)['findings'][0]['feedback']['version'], 2)
        with self.assertRaises(ScopeError):
            self.service.feedback(self.project_id, payload, uid())
        with self.assertRaises(ScopeError):
            self.service.feedback(uid(), payload, uid())
        self.assertEqual(self.db.execute('SELECT result FROM review_run WHERE id=?', (run['runId'],)).fetchone()[0], original)
        self.assertNotIn(b'Human-only', self.db.execute('SELECT note FROM review_feedback').fetchone()[0])
        self.assertNotIn('Human-only', str(self.db.execute('SELECT result FROM local_operation').fetchall()))

    def test_unselected_source_never_enters_preview_and_preview_copy_cannot_mutate_consent(self) -> None:
        (self.repo / 'unselected.py').write_text('DO_NOT_SEND_THIS = 123', encoding='utf-8')
        value = self.preview()
        self.assertNotIn('DO_NOT_SEND_THIS', value['user'])
        value['user'] = 'forged renderer text'
        run = self.service.start(self.project_id, self.consent(value), uid())
        self.finished(run['runId'])
        self.assertNotIn('forged renderer text', self.calls[0][1])
        with self.assertRaises(ScopeError):
            self.service.jobs.get(uid(), run['runId'])
        with self.assertRaises(ScopeError):
            self.projects.unregister(self.project_id, 1, uid())

    def test_restart_without_persisted_response_is_uncertain_never_replayed(self) -> None:
        value = self.preview()
        with patch('app.workspace.jobs.threading.Thread.start', side_effect=RuntimeError('fixture pre-send crash')):
            with self.assertRaises(ScopeError):
                self.service.start(self.project_id, self.consent(value), uid())
        # Durable intent may precede the actual call; do not assume it was unsent after a crash.
        run = self.db.execute('SELECT id,status FROM review_run').fetchone()
        self.assertEqual(run[1], 'uncertain')
        self.service = Reviews(self.snapshots, self.service.jobs.transport)
        self.assertEqual(self.service.jobs.get(self.project_id, run[0])['status'], 'uncertain')
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.db.execute('SELECT settled_at FROM local_usage_ledger').fetchone()[0])

    def test_acceptance_failure_rolls_back_consent_job_reservation_and_operation(self) -> None:
        value = self.preview()
        key = uid()
        self.db.execute("CREATE TRIGGER fail_reservation BEFORE INSERT ON local_usage_ledger BEGIN SELECT RAISE(ABORT,'fixture write failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.start(self.project_id, self.consent(value), key)
        for table in ['transfer_consent', 'review_run', 'review_attempt', 'local_usage_ledger']:
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM local_operation WHERE operation='reviews.start'").fetchone()[0], 0)
        self.assertEqual(self.calls, [])
        self.db.execute('DROP TRIGGER fail_reservation')
        run = self.service.start(self.project_id, self.consent(value), key)
        self.assertEqual(self.finished(run['runId'])['status'], 'succeeded')
        self.assertEqual(len(self.calls), 1)

    def test_result_write_failure_keeps_response_for_retry_without_provider_resend(self) -> None:
        self.mode = 'blocked'
        value = self.preview()
        run = self.service.start(self.project_id, self.consent(value), uid())
        self.assertTrue(self.entered.wait(2))
        self.db.execute("CREATE TRIGGER fail_result BEFORE INSERT ON review_finding BEGIN SELECT RAISE(ABORT,'fixture write failure'); END")
        self.release.set()
        deadline = time.monotonic() + 3
        while self.service.jobs.completed.empty() and time.monotonic() < deadline:
            time.sleep(.005)
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.jobs.drain()
        self.assertEqual(self.db.execute('SELECT status,result FROM review_run').fetchone(), ('running', None))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM review_finding').fetchone()[0], 0)
        self.assertIsNone(self.db.execute('SELECT settled_at FROM local_usage_ledger').fetchone()[0])
        self.assertIsNotNone(self.service.jobs.pending_completion)
        self.db.execute('DROP TRIGGER fail_result')
        self.assertEqual(self.finished(run['runId'])['status'], 'succeeded')
        self.assertEqual(len(self.calls), 1)


class ReviewMigrationTests(WorkspaceFixture):
    def test_v3_snapshot_ciphertext_survives_failed_then_successful_v4_upgrade(self) -> None:
        from app.workspace.store import LocalStore
        from app.workspace.snapshots import Snapshots
        from app.workspace.projects import Projects
        captured = self.capture([{'facet': 'selected', 'path': 'app.py'}])
        original = self.db.execute('SELECT payload FROM change_file').fetchone()[0]
        # Historical v3 fixture: precisely the seven v3 tables, constraints and old encrypted payload.
        for name in ['remote_analysis', 'remote_connection', 'notification', 'workspace_event', 'comparison', 'validation_run', 'validation_approval', 'impact_analysis', 'apply_journal', 'approval', 'proposal_event', 'proposal', 'review_feedback', 'review_finding', 'local_usage_ledger', 'review_attempt', 'review_run', 'transfer_consent']:
            self.db.execute('DROP TABLE ' + name)
        self.db.execute('DROP INDEX uq_command_plan_project_id')
        self.db.execute('ALTER TABLE project_setting RENAME TO current_settings')
        self.db.execute("CREATE TABLE project_setting(project_id TEXT NOT NULL REFERENCES local_project(id) ON DELETE CASCADE,key TEXT NOT NULL CHECK(key='recentLimit'),value TEXT NOT NULL CHECK(json_valid(value)),version INTEGER NOT NULL CHECK(version>=1),PRIMARY KEY(project_id,key))")
        self.db.execute("INSERT INTO project_setting SELECT * FROM current_settings WHERE key='recentLimit'")
        self.db.execute('DROP TABLE current_settings')
        self.db.execute('ALTER TABLE schema_meta RENAME TO old_meta')
        self.db.execute('CREATE TABLE schema_meta(version INTEGER PRIMARY KEY CHECK(version=3),applied_at TEXT NOT NULL,app_version TEXT NOT NULL)')
        self.db.execute("INSERT INTO schema_meta VALUES(3,'fixture','0.4.0')")
        self.db.execute('DROP TABLE old_meta')
        self.db.commit()
        self.store.close()
        def interrupted(connection: sqlite3.Connection) -> None:
            connection.execute('CREATE TABLE partial_review_fixture(id TEXT)')
            raise sqlite3.OperationalError('fixture migration interruption')
        candidate = LocalStore(self.root / 'data', lambda _: None)
        with patch('app.workspace.review_schema.create', side_effect=interrupted):
            with self.assertRaises(sqlite3.OperationalError):
                candidate.open()
        with closing(sqlite3.connect(self.root / 'data/workspace.sqlite3')) as check:
            self.assertEqual(check.execute('SELECT version FROM schema_meta').fetchone()[0], 3)
            self.assertIsNone(check.execute("SELECT name FROM sqlite_master WHERE name='partial_review_fixture'").fetchone())
            self.assertEqual(check.execute('SELECT payload FROM change_file').fetchone()[0], original)
        self.store = LocalStore(self.root / 'data', lambda _: None)
        self.store.open()
        assert self.store.connection is not None
        current = self.store.connection
        self.assertEqual(current.execute('SELECT version FROM schema_meta').fetchone()[0], 8)
        self.assertTrue(TABLES <= {r[0] for r in current.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        self.assertEqual(current.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(current.execute('SELECT payload FROM change_file').fetchone()[0], original)
        fixed = Snapshots(Projects(current), self.cipher).preview(self.project_id,
            {'snapshotId': captured['snapshotId'], 'fileId': captured['files'][0]['fileId'], 'side': 'RIGHT', 'startLine': 1, 'endLine': 5})
        self.assertIn('x = 1', [line['text'] for line in fixed['lines']])
        backups = list((self.root / 'data').glob('pre-v8-*.sqlite3'))
        self.assertEqual(len(backups), 2)
        for backup in backups:
            with closing(sqlite3.connect(backup)) as saved:
                self.assertEqual(saved.execute('SELECT version FROM schema_meta').fetchone()[0], 3)
                self.assertEqual(saved.execute('SELECT payload FROM change_file').fetchone()[0], original)
                self.assertEqual(saved.execute('PRAGMA integrity_check').fetchone()[0], 'ok')


if __name__ == '__main__':
    unittest.main()
