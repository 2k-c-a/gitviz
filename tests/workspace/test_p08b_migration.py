from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.workspace import remote_schema
from app.workspace.store import LocalStore
from tests.workspace.support import WorkspaceFixture


class P08bMigrationTests(WorkspaceFixture):
    def test_real_v7_inbox_and_ciphertext_survive_rollback_backup_and_upgrade(self) -> None:
        binary = Path(__file__).resolve().parents[2] / '.validation/p08b/preserved-schema7-agent/gitviz-workspace-agent.exe'
        if not binary.is_file() and os.environ.get('GITVIZ_CI_GOLDEN_SCHEMA') == '1':
            self.skipTest('CI: archived DDL covered separately; historical binary execution remains a host acceptance gate')
        self.assertTrue(binary.is_file(), 'Preserved schema7 agent is required')
        historic = self.root / 'actual-v7'
        def request(name: str, payload: dict[str, object]) -> dict[str, object]:
            return {'protocolVersion': 1, 'requestId': '00000000-0000-4000-8000-000000000001', 'operation': 'workspace.system.' + name, 'payload': payload}
        messages = [request('handshake', {'appVersion': '0.8.0', 'protocolVersion': 1, 'localSchemaRange': {'min': 7, 'max': 7}}),
            {'type': 'bootstrap', 'protocolVersion': 1, 'keyVersion': 1, 'keyBase64': base64.b64encode(b'P' * 32).decode()},
            request('health', {}), request('shutdown', {})]
        result = subprocess.run([str(binary), '--data-dir', str(historic), '--parent-pid', str(os.getpid())],
            input=b''.join(json.dumps(message).encode() + b'\n' for message in messages), capture_output=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout.splitlines()[1])['result']['state'], 'ready')
        self.capture([{'facet': 'selected', 'path': 'app.py'}])
        from app.workspace import events
        self.db.execute('BEGIN IMMEDIATE')
        events.record(self.db, project=self.project_id, source_type='validation', source_id='00000000-0000-4000-8000-000000000002',
                      revision=1, status='failed', summary={'exitCode': 1})
        self.db.execute('UPDATE notification SET count=3,read_at=123.0,version=4')
        self.db.commit()
        with closing(sqlite3.connect(historic / 'workspace.sqlite3')) as old:
            tables = [r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name<>'schema_meta'")]
            for table in tables:
                rows = self.db.execute('SELECT * FROM ' + table).fetchall()
                if rows:
                    old.executemany('INSERT INTO ' + table + ' VALUES(' + ','.join('?' for _ in rows[0]) + ')', rows)
            old.commit()
            before = {table: old.execute('SELECT * FROM ' + table).fetchall() for table in tables}
            self.assertEqual(old.execute('PRAGMA foreign_key_check').fetchall(), [])
        create = remote_schema.create
        def interrupted(db: sqlite3.Connection) -> None:
            create(db)
            raise sqlite3.OperationalError('schema8 fixture interruption')
        with patch('app.workspace.remote_schema.create', side_effect=interrupted), self.assertRaises(sqlite3.OperationalError):
            LocalStore(historic, lambda _: None).open()
        with closing(sqlite3.connect(historic / 'workspace.sqlite3')) as old:
            self.assertEqual(old.execute('SELECT version FROM schema_meta').fetchone()[0], 7)
            self.assertIsNone(old.execute("SELECT name FROM sqlite_master WHERE name='remote_connection'").fetchone())
            self.assertEqual(old.execute('SELECT * FROM notification').fetchall(), before['notification'])
        store = LocalStore(historic, lambda _: None)
        store.open()
        try:
            assert store.connection is not None
            db = store.connection
            self.assertEqual(db.execute('SELECT version FROM schema_meta').fetchone()[0], 8)
            for table in tables:
                self.assertEqual(db.execute('SELECT * FROM ' + table).fetchall(), before[table], table)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            with self.assertRaisesRegex(sqlite3.DatabaseError, 'IMMUTABLE_EVENT'):
                db.execute("UPDATE workspace_event SET severity='info'")
            db.rollback()
        finally:
            store.close()
        backups = list(historic.glob('pre-v8-*.sqlite3'))
        self.assertTrue(backups)
        for backup in backups:
            with closing(sqlite3.connect(backup)) as saved:
                self.assertEqual(saved.execute('SELECT version FROM schema_meta').fetchone()[0], 7)
                self.assertEqual(saved.execute('SELECT * FROM notification').fetchall(), before['notification'])
                self.assertEqual(saved.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
