from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.workspace import validation_schema
from app.workspace.projects import Projects
from app.workspace.proposals import Proposals
from app.workspace.review import Reviews
from app.workspace.snapshots import Snapshots
from app.workspace.store import LocalStore
from tests.workspace.test_apply import ApplyFixture, uid


class P07MigrationTests(ApplyFixture):
    def test_actual_p06_v5_proposals_preserved_after_failure_and_upgrade(self) -> None:
        binary = Path(__file__).resolve().parents[2] / 'desktop/out/GitVizWorkspaceP06Verify-win32-x64/resources/agent/gitviz-workspace-agent.exe'
        self.assertTrue(binary.is_file(), 'Preserved P06 agent is required for exact schema5 acceptance')
        historic = self.root / 'actual-v5'
        def request(name: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {'protocolVersion': 1, 'requestId': uid(), 'operation': 'workspace.system.' + name, 'payload': payload}
        messages = [request('handshake', {'appVersion': '0.6.0', 'protocolVersion': 1, 'localSchemaRange': {'min': 5, 'max': 5}}),
            {'type': 'bootstrap', 'protocolVersion': 1, 'keyVersion': 1, 'keyBase64': base64.b64encode(b'P' * 32).decode()},
            request('health', {}), request('shutdown', {})]
        result = subprocess.run([str(binary), '--data-dir', str(historic), '--parent-pid', str(os.getpid())],
            input=b''.join(json.dumps(message).encode() + b'\n' for message in messages), capture_output=True, timeout=20, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout.splitlines()[1])['result']['state'], 'ready')
        proposal = self.pending()
        self.approved(proposal)
        with closing(sqlite3.connect(historic / 'workspace.sqlite3')) as old:
            tables = [row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name<>'schema_meta'")]
            for table in tables:
                rows = self.db.execute('SELECT * FROM ' + table + (" WHERE key='recentLimit'" if table == 'project_setting' else '')).fetchall()
                if rows:
                    old.executemany('INSERT INTO ' + table + ' VALUES(' + ','.join('?' for _ in rows[0]) + ')', rows)
            old.commit()
            before = old.execute('SELECT payload FROM proposal').fetchone()[0]
            self.assertEqual(old.execute('PRAGMA foreign_key_check').fetchall(), [])
        create = validation_schema.create
        def interrupted(db: sqlite3.Connection) -> None:
            create(db)
            raise sqlite3.OperationalError('fixture schema6 interrupted')
        with patch('app.workspace.validation_schema.create', side_effect=interrupted), self.assertRaises(sqlite3.OperationalError):
            LocalStore(historic, lambda _: None).open()
        with closing(sqlite3.connect(historic / 'workspace.sqlite3')) as old:
            self.assertEqual(old.execute('SELECT version FROM schema_meta').fetchone()[0], 5)
            self.assertEqual(old.execute('SELECT payload FROM proposal').fetchone()[0], before)
            self.assertIsNone(old.execute("SELECT name FROM sqlite_master WHERE name='validation_run'").fetchone())
        upgraded = LocalStore(historic, lambda _: None)
        upgraded.open()
        try:
            assert upgraded.connection is not None
            self.assertEqual(upgraded.connection.execute('SELECT version FROM schema_meta').fetchone()[0], 7)
            self.assertEqual(upgraded.connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(upgraded.connection.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(upgraded.connection.execute('SELECT payload FROM proposal').fetchone()[0], before)
            reviews = Reviews(Snapshots(Projects(upgraded.connection), self.cipher))
            restored = Proposals(reviews)
            self.assertEqual(restored.get(self.project_id, proposal['proposalId'])['patchHash'], proposal['patchHash'])
            self.assertIsNone(upgraded.connection.execute('SELECT 1 FROM approval WHERE consumed_at IS NULL AND revoked_at IS NULL').fetchone())
            reviews.jobs.close()
        finally:
            upgraded.close()
        backups = list(historic.glob('pre-v7-*.sqlite3'))
        self.assertTrue(backups)
        for backup in backups:
            with closing(sqlite3.connect(backup)) as saved:
                self.assertEqual(saved.execute('SELECT version FROM schema_meta').fetchone()[0], 5)
                self.assertEqual(saved.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
