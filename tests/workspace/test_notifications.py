"""T09 local inbox: dedupe, multi-project filters, read CAS, forged targets, cursors, restart and atomic emission."""
from __future__ import annotations

import base64
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.workspace import events
from app.workspace.contracts import validate
from app.workspace.detection import Detection
from app.workspace.notifications import Notifications
from app.workspace.projects import Projects
from app.workspace.review import Reviews
from app.workspace.review_transport import Reply, TransportFailure
from app.workspace.scoped_files import ScopeError
from app.workspace.store import LocalStore
from app.workspace.validation import Validation
from tests.workspace.support import WorkspaceFixture
from tests.workspace.test_apply import ApplyFixture
from tests.workspace.test_review import output

ALL = {'projectIds': None, 'types': None, 'severities': None, 'read': 'all'}


def uid() -> str:
    return str(uuid.uuid4())


def ok(result: dict[str, Any]) -> None:
    validate('response', {'protocolVersion': 1, 'requestId': uid(), 'ok': True, 'result': result})


class InboxStoreTests(unittest.TestCase):
    """Inbox semantics over the real versioned store; targets are synthetic IDs (unavailable by design)."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name) / 'data'
        self.open()
        self.first, self.second = uid(), uid()
        for project, name in ((self.first, '첫 프로젝트'), (self.second, 'second')):
            self.db.execute('INSERT INTO local_project VALUES(?,?,?,?,?,?,?,1)', (project, f'C:/{project}', 'identity', 'g', 'c', '{}', name))

    def open(self) -> None:
        self.store = LocalStore(self.directory, lambda _: None)
        self.store.open()
        assert self.store.connection is not None
        self.db = self.store.connection
        self.inbox = Notifications(Projects(self.db))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def emit(self, project: str, source: str, status: str, *, source_id: str | None = None, revision: int = 1,
             summary: dict[str, Any] | None = None, cause: str | None = None) -> str:
        resource = source_id or uid()
        self.db.execute('BEGIN IMMEDIATE')
        events.record(self.db, project=project, source_type=source, source_id=resource, revision=revision, status=status,
                      summary=summary, cause=cause)
        self.db.commit()
        return resource

    def page(self, filters: dict[str, Any] | None = None, cursor: str | None = None, limit: int = 30) -> dict[str, Any]:
        result = self.inbox.list({'filters': filters or ALL, 'cursor': cursor, 'limit': limit})
        ok(result)
        return result

    def mark(self, project: str, item: dict[str, Any], version: int, read: bool, key: str | None = None) -> dict[str, Any]:
        return self.inbox.mark_read(project, {'notificationId': item['notificationId'], 'expectedVersion': version, 'read': read}, key or uid())

    def test_redelivery_is_one_notification_with_count_and_new_revision_is_separate(self) -> None:
        run = self.emit(self.first, 'validation', 'failed', summary={'exitCode': 1})
        self.emit(self.first, 'validation', 'failed', source_id=run, summary={'exitCode': 1})
        items = self.page()['items']
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]['count'], items[0]['version'], items[0]['causeCode']), (2, 2, 'command-nonzero-exit'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM workspace_event').fetchone()[0], 1)
        self.emit(self.first, 'review', 'uncertain', source_id=run, revision=2)
        self.emit(self.first, 'review', 'uncertain', source_id=run, revision=3)
        self.assertEqual(len(self.page()['items']), 3)

    def test_non_terminal_statuses_and_raw_causes_are_not_stored(self) -> None:
        self.db.execute('BEGIN IMMEDIATE')
        self.assertFalse(events.record(self.db, project=self.first, source_type='validation', source_id=uid(), revision=1, status='running'))
        self.assertFalse(events.record(self.db, project=self.first, source_type='proposal', source_id=uid(), revision=2, status='approved'))
        events.record(self.db, project=self.first, source_type='review', source_id=uid(), revision=1, status='failed',
                      cause='C:\\Users\\secret path: provider said <b>')
        self.db.commit()
        (item,) = self.page()['items']
        self.assertEqual(item['causeCode'], 'FAILED')

    def test_ai_inference_and_deterministic_failure_keep_separate_severity_and_cause(self) -> None:
        self.emit(self.first, 'review', 'succeeded', summary=events.review_summary('review', {'findings': [{'severity': 'low'}, {'severity': 'high'}]}))
        self.emit(self.first, 'review', 'succeeded', summary=events.review_summary('review', {'findings': []}))
        self.emit(self.first, 'review', 'succeeded', summary=events.review_summary('refactor', {'findings': [{'severity': 'high'}]}))
        self.emit(self.first, 'validation', 'failed', summary={'exitCode': 3})
        by_cause = {item['causeCode']: item for item in self.page()['items']}
        self.assertEqual(by_cause['ai-findings']['severity'], 'warning')
        self.assertEqual(by_cause['ai-findings']['summary'], {'kind': 'review', 'findings': 2, 'highestFindingSeverity': 'high'})
        self.assertEqual(by_cause['ai-no-findings']['severity'], 'info')
        self.assertEqual(by_cause['ai-refactor-ready']['severity'], 'info')
        self.assertEqual((by_cause['command-nonzero-exit']['severity'], by_cause['command-nonzero-exit']['summary']), ('error', {'exitCode': 3}))

    def test_multi_project_type_severity_and_read_filters(self) -> None:
        self.emit(self.first, 'validation', 'failed')
        self.emit(self.second, 'validation', 'succeeded')
        self.emit(self.second, 'proposal', 'recovery_required', revision=4)
        self.assertEqual(len(self.page()['items']), 3)
        self.assertEqual({i['projectId'] for i in self.page({**ALL, 'projectIds': [self.second]})['items']}, {self.second})
        self.assertEqual([i['type'] for i in self.page({**ALL, 'types': ['proposal.recovery_required']})['items']], ['proposal.recovery_required'])
        self.assertEqual({i['severity'] for i in self.page({**ALL, 'severities': ['error']})['items']}, {'error'})
        target = self.page({**ALL, 'projectIds': [self.first]})['items'][0]
        self.mark(self.first, target, 1, True)
        self.assertEqual(len(self.page({**ALL, 'read': 'unread'})['items']), 2)
        self.assertEqual([i['notificationId'] for i in self.page({**ALL, 'read': 'read'})['items']], [target['notificationId']])
        self.assertEqual(self.page()['unreadCount'], 2)

    def test_read_cas_idempotency_and_other_project_forgery(self) -> None:
        self.emit(self.first, 'validation', 'failed')
        (item,) = self.page()['items']
        with self.assertRaisesRegex(ScopeError, 'RESOURCE_NOT_FOUND'):
            self.mark(self.second, item, 1, True)
        self.assertIsNone(self.page()['items'][0]['readAt'])
        key = uid()
        marked = self.mark(self.first, item, 1, True, key)
        ok(marked)
        self.assertEqual(marked['version'], 2)
        self.assertEqual(self.mark(self.first, item, 1, True, key)['version'], 2)  # Same key and body replays.
        with self.assertRaisesRegex(ScopeError, 'CONFLICT'):
            self.mark(self.first, item, 1, False, key)  # Same key, different body.
        with self.assertRaisesRegex(ScopeError, 'CONFLICT'):
            self.mark(self.first, item, 1, False)  # Stale version.
        self.assertIsNone(self.mark(self.first, item, 2, False)['readAt'])

    def test_cursor_is_stable_bound_to_filters_and_rejects_tampering(self) -> None:
        for _ in range(5):
            self.emit(self.first, 'validation', 'succeeded')
        seen: list[str] = []
        cursor = None
        while True:
            page = self.page(cursor=cursor, limit=2)
            seen += [item['notificationId'] for item in page['items']]
            cursor = page['nextCursor']
            if cursor is None:
                break
            self.emit(self.second, 'validation', 'failed')  # Newer items never shift an existing cursor.
        self.assertEqual((len(seen), len(set(seen))), (5, 5))
        first = self.page(limit=2)['nextCursor']
        assert first is not None
        with self.assertRaisesRegex(ScopeError, 'INVALID_REQUEST'):
            self.page({**ALL, 'read': 'unread'}, cursor=first)
        forged = base64.urlsafe_b64encode(json.dumps({'c': 1.0, 'i': 'x', 'f': 'y'}).encode()).decode().rstrip('=')
        for bad in (forged, 'AAAA', first[:-2]):
            with self.subTest(bad=bad), self.assertRaisesRegex(ScopeError, 'INVALID_REQUEST'):
                self.page(cursor=bad)

    def test_missing_target_is_unavailable_and_project_removal_is_blocked(self) -> None:
        self.emit(self.first, 'review', 'failed')
        self.assertEqual(self.page()['items'][0]['targetState'], 'unavailable')
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('DELETE FROM local_project WHERE id=?', (self.first,))

    def test_restart_preserves_events_read_state_and_sequence(self) -> None:
        self.emit(self.first, 'validation', 'failed')
        (item,) = self.page()['items']
        self.mark(self.first, item, 1, True)
        self.store.close()
        self.open()
        (again,) = self.page()['items']
        self.assertEqual((again['notificationId'], again['version']), (item['notificationId'], 2))
        self.assertIsNotNone(again['readAt'])
        self.emit(self.second, 'validation', 'succeeded')
        self.assertEqual([e['sequence'] for e in events.read(self.db, {'afterSequence': 0, 'limit': 10, 'projectIds': None})['events']], [1, 2])

    def test_event_read_sequence_project_filter_reset_and_bounds(self) -> None:
        for project in (self.first, self.second, self.first):
            self.emit(project, 'validation', 'succeeded')
        batch = events.read(self.db, {'afterSequence': 0, 'limit': 2, 'projectIds': None})
        ok(batch)
        self.assertEqual(([e['sequence'] for e in batch['events']], batch['hasMore'], batch['lastSequence']), ([1, 2], True, 3))
        filtered = events.read(self.db, {'afterSequence': 0, 'limit': 10, 'projectIds': [self.first]})
        self.assertEqual([e['sequence'] for e in filtered['events']], [1, 3])
        self.assertTrue(events.read(self.db, {'afterSequence': 9, 'limit': 10, 'projectIds': None})['reset'])
        self.assertEqual(set(batch['events'][0]), {'sequence', 'eventId', 'projectId', 'type', 'resourceId', 'sourceRevision'})

    def test_recording_requires_transaction_rolls_back_with_it_and_events_are_immutable(self) -> None:
        with self.assertRaises(sqlite3.ProgrammingError):
            events.record(self.db, project=self.first, source_type='validation', source_id=uid(), revision=1, status='failed')
        self.db.execute('BEGIN IMMEDIATE')
        events.record(self.db, project=self.first, source_type='validation', source_id=uid(), revision=1, status='failed')
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM workspace_event').fetchone()[0], 0)
        self.emit(self.first, 'validation', 'failed')
        with self.assertRaisesRegex(sqlite3.DatabaseError, 'IMMUTABLE_EVENT'):
            self.db.execute("UPDATE workspace_event SET severity='info'")

    def test_contract_shapes_for_requests(self) -> None:
        project = uid()
        validate('request', {'protocolVersion': 1, 'requestId': uid(), 'operation': 'workspace.notifications.list',
                             'payload': {'filters': ALL, 'cursor': None, 'limit': 30}})
        validate('request', {'protocolVersion': 1, 'requestId': uid(), 'operation': 'workspace.events.read',
                             'payload': {'afterSequence': 0, 'limit': 100, 'projectIds': [project]}})
        validate('request', {'protocolVersion': 1, 'requestId': uid(), 'operation': 'workspace.notifications.markRead', 'projectId': project,
                             'idempotencyKey': uid(), 'payload': {'notificationId': uid(), 'expectedVersion': 1, 'read': True}})
        rejected: list[dict[str, Any]] = [{**ALL, 'projectIds': []}, {**ALL, 'types': ['review.started']}, {**ALL, 'read': 'maybe'}, {**ALL, 'extra': 1}]
        for bad in rejected:
            with self.subTest(bad=bad), self.assertRaises(Exception):
                validate('request', {'protocolVersion': 1, 'requestId': uid(), 'operation': 'workspace.notifications.list',
                                     'payload': {'filters': bad, 'cursor': None, 'limit': 30}})
        with self.assertRaises(Exception):  # markRead must name the owning project.
            validate('request', {'protocolVersion': 1, 'requestId': uid(), 'operation': 'workspace.notifications.markRead',
                                 'idempotencyKey': uid(), 'payload': {'notificationId': uid(), 'expectedVersion': 1, 'read': True}})


class HandshakeContractTests(unittest.TestCase):
    def test_handshake_advertises_inbox_operations_within_the_response_schema(self) -> None:
        from app.workspace.dispatcher import Dispatcher
        with tempfile.TemporaryDirectory() as tmp:
            dispatcher = Dispatcher(Path(tmp) / 'data', lambda _: None)
            answer = dispatcher.dispatch({'protocolVersion': 1, 'requestId': uid(), 'operation': 'workspace.system.handshake',
                'payload': {'appVersion': '0.8.0', 'protocolVersion': 1, 'localSchemaRange': {'min': 8, 'max': 8}}})
            self.assertTrue(answer['ok'], answer.get('error'))
            self.assertEqual(answer['result']['localSchemaRange'], {'min': 8, 'max': 8})
            self.assertTrue({'workspace.notifications.list', 'workspace.notifications.markRead', 'workspace.events.read'} <= set(answer['result']['capabilities']))


class ReviewEventTests(WorkspaceFixture):
    def setUp(self) -> None:
        super().setUp()
        self.snapshot = self.capture([{'facet': 'selected', 'path': 'app.py'}])
        self.mode = 'success'
        self.release = threading.Event()

        def transport(profile: dict[str, Any], system: str, user: str, cancelled: threading.Event) -> Reply:
            if self.mode == 'blocked':
                self.release.wait(5)
            if self.mode == 'uncertain':
                raise TransportFailure('PROVIDER_TIMEOUT', True)
            return Reply(json.dumps(output(json.loads(user)['files'][0])).encode(), {'inputTokens': 1, 'outputTokens': 1})
        self.service = Reviews(self.snapshots, transport)
        self.inbox = Notifications(self.projects)

    def tearDown(self) -> None:
        self.release.set()
        deadline = time.monotonic() + 6
        while self.service.jobs.active and time.monotonic() < deadline:
            self.service.jobs.drain()
            time.sleep(.005)
        self.service.jobs.close()
        super().tearDown()

    def start(self) -> str:
        value = self.service.preview(self.project_id, {'snapshotId': self.snapshot['snapshotId'], 'kind': 'review',
            'purpose': 'Review this selected source.', 'profileId': 'ollama-local-v1', 'generation': 1})
        consent = {k: value[k] for k in ('previewId', 'previewHash', 'manifestHash', 'maxEstimatedCost')}
        return self.service.start(self.project_id, consent | {'acknowledged': True, 'duplicateRiskAcknowledged': False}, uid())['runId']

    def finished(self, run: str) -> dict[str, Any]:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            value = self.service.jobs.get(self.project_id, run)
            if value['status'] not in ('queued', 'running', 'cancel_requested'):
                return value
            time.sleep(.005)
        self.fail('fixture transport did not finish')

    def items(self) -> list[dict[str, Any]]:
        page = self.inbox.list({'filters': ALL, 'cursor': None, 'limit': 30})
        ok(page)
        return page['items']

    def test_completed_review_creates_one_available_ai_inference_notification_without_source(self) -> None:
        run = self.start()
        self.assertEqual(self.finished(run)['status'], 'succeeded')
        self.service.jobs.get(self.project_id, run)  # A later drain must not duplicate.
        (item,) = self.items()
        self.assertEqual((item['type'], item['severity'], item['causeCode'], item['targetState']),
                         ('review.succeeded', 'warning', 'ai-findings', 'available'))
        self.assertEqual(item['target'], {'kind': 'review', 'projectId': self.project_id, 'resourceId': run})
        stored = '\n'.join(str(row) for row in self.db.execute('SELECT * FROM workspace_event'))
        for secret in ('x = 1', 'Review this selected source.', str(self.repo), 'app.py'):
            self.assertNotIn(secret, stored)

    def test_result_write_failure_emits_nothing_until_the_retry_commits(self) -> None:
        self.mode = 'blocked'
        run = self.start()
        self.db.execute("CREATE TRIGGER fail_result BEFORE INSERT ON review_finding BEGIN SELECT RAISE(ABORT,'fixture write failure'); END")
        self.release.set()
        deadline = time.monotonic() + 3
        while self.service.jobs.completed.empty() and time.monotonic() < deadline:
            time.sleep(.005)
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.jobs.drain()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM workspace_event').fetchone()[0], 0)
        self.db.execute('DROP TRIGGER fail_result')
        self.assertEqual(self.finished(run)['status'], 'succeeded')
        self.assertEqual([i['type'] for i in self.items()], ['review.succeeded'])

    def test_restart_uncertain_is_reported_once(self) -> None:
        with patch('app.workspace.jobs.threading.Thread.start', side_effect=RuntimeError('fixture pre-send crash')):
            with self.assertRaises(ScopeError):
                self.start()
        Reviews(self.snapshots, self.service.jobs.transport)  # A second recovery must not duplicate.
        (item,) = self.items()
        self.assertEqual((item['type'], item['severity'], item['count']), ('review.uncertain', 'warning', 1))


class ValidationEventTests(WorkspaceFixture):
    def setUp(self) -> None:
        super().setUp()
        (self.repo / 'pyproject.toml').write_text('[tool.unittest]\n', encoding='utf-8')
        (self.repo / 'test_fixture.py').write_text('import unittest,app\nclass Check(unittest.TestCase):\n def test_value(self): self.assertEqual(app.x,1)\n', encoding='utf-8')
        (self.repo / 'app.py').write_text('x = 3\nprint(x)\n', encoding='utf-8')  # The fixture test fails deterministically.
        self.detection = Detection(self.snapshots, lambda _kind, _root: (str(getattr(sys, '_base_executable', sys.executable)), []))
        self.validation = Validation(self.detection)
        self.inbox = Notifications(self.projects)

    def tearDown(self) -> None:
        self.validation.close()
        for _ in range(200):
            self.validation.drain()
            if self.validation.active is None:
                break
            time.sleep(.01)
        super().tearDown()

    def items(self) -> list[dict[str, Any]]:
        return self.inbox.list({'filters': ALL, 'cursor': None, 'limit': 30})['items']

    def test_deterministic_failure_and_restart_interruption_are_inbox_events(self) -> None:
        snapshot = self.capture([{'facet': 'selected', 'path': 'app.py'}])
        detected = self.detection.detect(self.project_id, {'cwdRelative': '.', 'packageManager': None})
        plan = self.detection.plan(self.project_id, {'snapshotId': snapshot['snapshotId'], 'candidateId': detected['candidates'][0]['candidateId']}, uid())
        prepared = self.validation.prepare(self.project_id, {'planId': plan['planId']})
        approval = self.validation.approve(self.project_id, {'planId': plan['planId'], 'confirmationHash': prepared['confirmationHash'],
            'capability': 'c' * 64, 'trusted': True}, uid())
        self.assertEqual(self.items(), [])  # Approval is not an inbox event.
        run = self.validation.start(self.project_id, {'planId': plan['planId'], 'approvalId': approval['validationApprovalId'], 'capability': 'c' * 64}, uid())
        deadline = time.monotonic() + 45
        while self.validation.get(self.project_id, {'runId': run['runId'], 'offset': 0, 'limit': 1})['status'] == 'running':
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        (item,) = self.items()
        self.assertEqual((item['type'], item['severity'], item['causeCode'], item['targetState']),
                         ('validation.failed', 'error', 'command-nonzero-exit', 'available'))
        self.assertNotIn(item['summary']['exitCode'], (0, None))
        orphan = uid()
        self.db.execute('INSERT INTO validation_run VALUES(?,?,?,?,?)', (orphan, self.project_id, plan['planId'], 'running',
            self.validation.encrypt(self.project_id, orphan, {'fixture': 'not-started'})))
        Validation(self.detection)
        Validation(self.detection)
        interrupted = [i for i in self.items() if i['type'] == 'validation.interrupted']
        self.assertEqual([(i['target']['resourceId'], i['count']) for i in interrupted], [(orphan, 1)])


class ProposalEventTests(ApplyFixture):
    def test_apply_and_interrupted_publish_report_outcomes_not_approvals(self) -> None:
        inbox = Notifications(self.projects)
        def listed(types: list[str] | None = None) -> list[dict[str, Any]]:
            return inbox.list({'filters': {**ALL, 'types': types}, 'cursor': None, 'limit': 30})['items']
        proposal = self.pending()
        self.assertEqual([(i['type'], i['causeCode']) for i in listed()], [('review.succeeded', 'ai-refactor-ready')])
        payload = self.approved(proposal)
        self.assertEqual(len(listed()), 1)  # Approval is not an inbox event.
        self.assertEqual(self.application.apply(self.project_id, payload, uid())['status'], 'applied')
        applied = listed(['proposal.applied'])
        self.assertEqual([(i['target']['resourceId'], i['targetState']) for i in applied], [(proposal['proposalId'], 'available')])
        self.db.execute("UPDATE proposal SET status='applying' WHERE id=?", (proposal['proposalId'],))
        type(self.proposals)(self.reviews)  # Restart: applying -> recovery_required.
        recovery = listed(['proposal.recovery_required'])
        self.assertEqual([(i['severity'], i['count']) for i in recovery], [('error', 1)])


if __name__ == '__main__':
    unittest.main()
