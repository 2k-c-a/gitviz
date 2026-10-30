from __future__ import annotations

import sqlite3
import uuid
import tempfile
import base64
import unittest
from pathlib import Path
from unittest.mock import patch

from app.workspace import events
from app.workspace.contracts import validate
from app.workspace.notifications import Notifications
from app.workspace.remote_connections import RemoteConnections, github_url
from app.workspace.scoped_files import ScopeError
from tests.workspace.support import WorkspaceFixture
from app.workspace.dispatcher import Dispatcher


class RemoteConnectionTests(WorkspaceFixture):
    def setUp(self) -> None:
        super().setUp()
        self.git('remote', 'add', 'origin', 'git@github.com:Owner/Repo.git')
        self.service = RemoteConnections(self.projects)

    def connect(self, account: str = '11') -> str:
        inspected = self.service.inspect(self.project_id)
        payload = {'phase': 'connect', 'expectedVersion': inspected['projectVersion'], 'accountId': account,
                   'serverOrigin': 'https://staging.example.invalid', 'repositoryId': 2, 'githubId': 123,
                   'canonicalUrl': 'https://github.com/owner/repo', 'remoteName': 'origin', 'configHash': inspected['configHash']}
        result = self.service.connect(self.project_id, payload, str(uuid.uuid4()))
        return result['connectionId']

    def observe(self, connection: str, status: str, job: int = 3, account: str = '11') -> str:
        result = self.service.observe(self.project_id, {'connectionId': connection, 'accountId': account,
            'serverOrigin': 'https://staging.example.invalid', 'jobId': job, 'status': status,
            'snapshotId': 4 if status == 'done' else None}, str(uuid.uuid4()))
        return result['analysisId']

    def test_known_job_transition_is_atomic_and_terminal_baseline_is_silent(self) -> None:
        connection = self.connect()
        self.observe(connection, 'done', 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM notification').fetchone()[0], 0)
        analysis = self.observe(connection, 'queued')
        self.assertEqual(self.observe(connection, 'running'), analysis)
        self.observe(connection, 'done')
        self.observe(connection, 'done')
        result = Notifications(self.projects).list({'filters': {'projectIds': None, 'types': None, 'severities': None, 'read': 'all'}, 'limit': 30, 'cursor': None})
        validate('response', {'protocolVersion': 1, 'requestId': str(uuid.uuid4()), 'ok': True, 'result': result})
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['type'], 'analysis.succeeded')
        self.assertEqual(result['items'][0]['count'], 1)
        with self.assertRaises(ScopeError):
            self.observe(connection, 'failed')

    def test_account_origin_remote_change_and_old_connection_fail_closed(self) -> None:
        connection = self.connect()
        analysis = self.observe(connection, 'running')
        payload = {'analysisId': analysis, 'accountId': '11', 'serverOrigin': 'https://staging.example.invalid'}
        self.assertEqual(self.service.get(self.project_id, payload)['jobId'], 3)
        for altered in ({'accountId': '12'}, {'serverOrigin': 'https://other.invalid'}):
            with self.assertRaises(ScopeError):
                self.service.get(self.project_id, payload | altered)
        self.git('remote', 'set-url', 'origin', 'https://github.com/other/repo')
        with self.assertRaises(ScopeError):
            self.service.get(self.project_id, payload)
        self.git('remote', 'set-url', 'origin', 'https://github.com/owner/repo')
        self.connect('12')
        with self.assertRaises(ScopeError):
            self.observe(connection, 'done')

    def test_failed_event_insert_rolls_back_job_and_notification(self) -> None:
        connection = self.connect()
        self.observe(connection, 'running')
        with patch.object(events, 'record', side_effect=sqlite3.IntegrityError('fixture')):
            with self.assertRaises(sqlite3.IntegrityError):
                self.observe(connection, 'done')
        self.assertEqual(self.db.execute('SELECT status FROM remote_analysis').fetchone()[0], 'running')
        self.assertEqual(self.db.execute('SELECT count(*) FROM notification').fetchone()[0], 0)

    def test_remote_urls_never_accept_credentials_or_non_github_hosts(self) -> None:
        self.assertEqual(github_url('ssh://git@github.com/Owner/Repo.git'), 'https://github.com/owner/repo')
        for value in ('https://token@github.com/o/r', 'https://github.com.evil/o/r', 'file:///tmp/r',
                      'https://github.com/o/..', 'https://github.com/o/r?token=secret'):
            self.assertIsNone(github_url(value))

    def test_secondary_public_fetch_url_cannot_override_a_different_primary(self) -> None:
        self.git('remote', 'set-url', 'origin', 'https://github.com/private/primary')
        self.git('config', '--add', 'remote.origin.url', 'https://github.com/owner/repo')
        self.assertEqual(self.service.inspect(self.project_id)['remotes'], [])
        self.assertEqual(github_url('https://GitHub.com/Owner/Repo.git'), 'https://github.com/owner/repo')


class GitOptionalFoundationTests(unittest.TestCase):
    def test_store_and_health_start_without_git_installed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch('app.workspace.git_service.installed_git', side_effect=ScopeError('GIT_NOT_FOUND')) as lookup:
            dispatcher = Dispatcher(Path(tmp) / 'data', lambda _: None)
            try:
                hello = dispatcher.dispatch({'protocolVersion': 1, 'requestId': str(uuid.uuid4()), 'operation': 'workspace.system.handshake',
                    'payload': {'appVersion': '0.8.0', 'protocolVersion': 1, 'localSchemaRange': {'min': 8, 'max': 8}}})
                self.assertTrue(hello['ok'])
                dispatcher.bootstrap({'type': 'bootstrap', 'protocolVersion': 1, 'keyVersion': 1, 'keyBase64': base64.b64encode(b'P' * 32).decode()})
                self.assertEqual(dispatcher.state, 'ready')
                lookup.assert_not_called()
            finally:
                dispatcher.close()
