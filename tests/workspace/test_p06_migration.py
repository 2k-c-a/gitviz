from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.workspace.projects import Projects
from app.workspace.review import Reviews
from app.workspace.review_transport import Reply
from app.workspace.snapshots import Snapshots
from app.workspace.store import LocalStore
from tests.workspace.support import WorkspaceFixture
from tests.workspace.test_review import output, uid


class P06MigrationTests(WorkspaceFixture):
    def test_actual_p05_v4_schema_and_review_ciphertext_survive_failed_then_successful_upgrade(self) -> None:
        binary = Path(__file__).resolve().parents[2] / 'desktop/out/GitVizWorkspaceP05Verify-win32-x64/resources/agent/gitviz-workspace-agent.exe'
        if not binary.is_file():
            self.skipTest('Preserved actual P05 frozen agent required for historical v4 fixture')
        historic = self.root / 'actual-v4'
        def request(name: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {'protocolVersion': 1, 'requestId': uid(), 'operation': 'workspace.system.' + name, 'payload': payload}
        messages = [request('handshake', {'appVersion': '0.5.0', 'protocolVersion': 1, 'localSchemaRange': {'min': 4, 'max': 4}}),
            {'type': 'bootstrap', 'protocolVersion': 1, 'keyVersion': 1, 'keyBase64': base64.b64encode(b'P' * 32).decode()},
            request('health', {}), request('shutdown', {})]
        result = subprocess.run([str(binary), '--data-dir', str(historic), '--parent-pid', str(os.getpid())],
            input=b''.join(json.dumps(message).encode() + b'\n' for message in messages), capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(replies[1]['result']['state'], 'ready')
        calls: list[int] = []
        def provider(profile: dict[str, Any], system: str, user: str, cancelled: Any) -> Reply:
            calls.append(1)
            return Reply(json.dumps(output(json.loads(user)['files'][0])).encode())
        reviews = Reviews(self.snapshots, provider)
        snapshot = self.capture([{'facet': 'selected', 'path': 'app.py'}])
        preview = reviews.preview(self.project_id, {'snapshotId': snapshot['snapshotId'], 'kind': 'review', 'purpose': 'v4 preservation fixture', 'profileId': 'ollama-local-v1', 'generation': 1})
        consent = {k: preview[k] for k in ('previewId', 'previewHash', 'manifestHash', 'maxEstimatedCost')}
        run = reviews.start(self.project_id, {**consent, 'acknowledged': True, 'duplicateRiskAcknowledged': False}, uid())
        final = run
        for _ in range(200):
            final = reviews.jobs.get(self.project_id, run['runId'])
            if final['status'] == 'succeeded':
                break
            time.sleep(.005)
        self.assertEqual(final['status'], 'succeeded')
        finding = final['findings'][0]
        reviews.feedback(self.project_id, {'findingId': finding['findingId'], 'expectedVersion': 1, 'verdict': 'uncertain', 'note': 'preserve encrypted feedback'}, uid())
        reviews.jobs.close()
        tables = ['local_project', 'project_setting', 'local_operation', 'change_snapshot', 'change_file', 'command_plan',
            'transfer_consent', 'review_run', 'review_attempt', 'local_usage_ledger', 'review_finding', 'review_feedback']
        with closing(sqlite3.connect(historic / 'workspace.sqlite3')) as old:
            old.execute('PRAGMA foreign_keys=ON')
            for table in tables:
                rows = self.db.execute('SELECT * FROM ' + table + (" WHERE key='recentLimit'" if table == 'project_setting' else '')).fetchall()
                if rows:
                    old.executemany('INSERT INTO ' + table + ' VALUES(' + ','.join('?' for _ in rows[0]) + ')', rows)
            old.commit()
            self.assertEqual(old.execute('SELECT version FROM schema_meta').fetchone()[0], 4)
            self.assertIn("CHECK(kind='review')", old.execute("SELECT sql FROM sqlite_master WHERE name='review_run'").fetchone()[0])
            before = old.execute('SELECT result FROM review_run').fetchone()[0]
        connect = sqlite3.connect
        class Interrupted(sqlite3.Connection):
            def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
                if sql == 'DROP TABLE review_run':
                    raise sqlite3.OperationalError('fixture interrupted parent rebuild')
                return super().execute(sql, parameters)
        def interrupted(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
            return connect(database, *args, **{**kwargs, 'factory': Interrupted})
        with patch('app.workspace.store.sqlite3.connect', side_effect=interrupted):
            with self.assertRaises(sqlite3.OperationalError):
                LocalStore(historic, lambda _: None).open()
        with closing(connect(historic / 'workspace.sqlite3')) as old:
            self.assertEqual(old.execute('SELECT version FROM schema_meta').fetchone()[0], 4)
            self.assertEqual(old.execute('SELECT result FROM review_run').fetchone()[0], before)
            self.assertIsNone(old.execute("SELECT name FROM sqlite_master WHERE name='review_run_v5'").fetchone())
        upgraded = LocalStore(historic, lambda _: None)
        upgraded.open()
        try:
            assert upgraded.connection is not None
            self.assertEqual(upgraded.connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(upgraded.connection.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(upgraded.connection.execute('SELECT result FROM review_run').fetchone()[0], before)
            current = Reviews(Snapshots(Projects(upgraded.connection), self.cipher), provider)
            restored = current.jobs.get(self.project_id, run['runId'])
            self.assertEqual(restored['findings'][0]['findingId'], finding['findingId'])
            self.assertEqual(restored['findings'][0]['feedback']['note'], 'preserve encrypted feedback')
            self.assertEqual(len(calls), 1)
            current.jobs.close()
        finally:
            upgraded.close()
        for backup in historic.glob('pre-v7-*.sqlite3'):
            with closing(connect(backup)) as saved:
                self.assertEqual(saved.execute('SELECT version FROM schema_meta').fetchone()[0], 4)
                self.assertEqual(saved.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
