"""Portable DDL fixture coverage. This does not claim to execute a historical binary."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.workspace import events, remote_schema
from app.workspace.store import LocalStore
from tests.workspace.support import WorkspaceFixture


class GoldenMigrationTests(WorkspaceFixture):
    def test_archived_v4_v5_v7_ddl_preserves_ciphertext_and_rolls_back(self) -> None:
        self.capture([{'facet': 'selected', 'path': 'app.py'}])
        self.db.execute('BEGIN IMMEDIATE')
        events.record(self.db, project=self.project_id, source_type='validation', source_id='00000000-0000-4000-8000-000000000002', revision=1, status='failed')
        self.db.execute('UPDATE notification SET count=2,read_at=123.0,version=3')
        self.db.commit()
        for version in (4, 5, 7):
            with self.subTest(schema=version):
                data = json.loads((Path(__file__).parent / f'fixtures/schema-v{version}.json').read_text())
                directory = self.root / f'golden-v{version}'
                directory.mkdir()
                with closing(sqlite3.connect(directory / 'workspace.sqlite3')) as old:
                    for statement in data['ddl']:
                        old.execute(statement)
                    old.execute('INSERT INTO schema_meta VALUES(?,?,?)', data['schemaMeta'])
                    tables = [r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name<>'schema_meta'")]
                    for table in tables:
                        rows = self.db.execute('SELECT * FROM ' + table + (" WHERE key='recentLimit'" if table == 'project_setting' and version < 6 else '')).fetchall()
                        if rows:
                            old.executemany('INSERT INTO ' + table + ' VALUES(' + ','.join('?' for _ in rows[0]) + ')', rows)
                    old.commit()
                    before = {table: old.execute('SELECT * FROM ' + table).fetchall() for table in tables}
                    self.assertEqual(old.execute('PRAGMA foreign_key_check').fetchall(), [])
                create = remote_schema.create
                def interrupted(db: sqlite3.Connection) -> None:
                    create(db)
                    raise sqlite3.OperationalError('golden schema8 interruption')
                with patch('app.workspace.remote_schema.create', side_effect=interrupted), self.assertRaises(sqlite3.OperationalError):
                    LocalStore(directory, lambda _: None).open()
                with closing(sqlite3.connect(directory / 'workspace.sqlite3')) as old:
                    self.assertEqual(old.execute('SELECT version FROM schema_meta').fetchone()[0], version)
                    self.assertEqual(old.execute('SELECT payload FROM change_file').fetchall(), self.db.execute('SELECT payload FROM change_file').fetchall())
                store = LocalStore(directory, lambda _: None)
                store.open()
                try:
                    assert store.connection is not None
                    self.assertEqual(store.connection.execute('SELECT payload FROM change_file').fetchall(), self.db.execute('SELECT payload FROM change_file').fetchall())
                    if version == 7:
                        self.assertEqual(store.connection.execute('SELECT * FROM notification').fetchall(), before['notification'])
                    self.assertEqual(store.connection.execute('PRAGMA foreign_key_check').fetchall(), [])
                    self.assertEqual(store.connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                finally:
                    store.close()
                self.assertTrue(list(directory.glob('pre-v8-*.sqlite3')))
