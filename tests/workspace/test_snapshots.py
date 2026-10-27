from __future__ import annotations

import base64
import json
import sqlite3
import uuid
from contextlib import closing
from typing import Any
from unittest.mock import patch

from app.workspace.scoped_files import ScopeError
from app.workspace.snapshots import Snapshots
from app.workspace.store import LocalStore
from app.workspace.projects import Projects
from tests.workspace.support import WorkspaceFixture


class SnapshotTests(WorkspaceFixture):
    def test_v2_backup_interrupted_upgrade_preserves_projects_and_recovers(self) -> None:
        directory = self.root / 'historic-v2'
        directory.mkdir()
        path = directory / 'workspace.sqlite3'
        grant = self.projects.grant(self.project_id)
        with closing(sqlite3.connect(path)) as old:
            old.executescript('''
CREATE TABLE schema_meta(version INTEGER PRIMARY KEY CHECK(version=2),applied_at TEXT NOT NULL,app_version TEXT NOT NULL);
INSERT INTO schema_meta VALUES(2,'fixture','0.2.0');
CREATE TABLE local_project(id TEXT PRIMARY KEY,canonical_root TEXT NOT NULL UNIQUE,root_identity TEXT NOT NULL,git_dir TEXT NOT NULL,common_dir TEXT NOT NULL,metadata_grant TEXT NOT NULL CHECK(json_valid(metadata_grant)),display_name TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>=1));
CREATE TABLE project_setting(project_id TEXT NOT NULL REFERENCES local_project(id) ON DELETE CASCADE,key TEXT NOT NULL CHECK(key='recentLimit'),value TEXT NOT NULL CHECK(json_valid(value)),version INTEGER NOT NULL CHECK(version>=1),PRIMARY KEY(project_id,key));
CREATE TABLE local_operation(project_scope TEXT NOT NULL,operation TEXT NOT NULL,idempotency_key TEXT NOT NULL,body_hash TEXT NOT NULL,result TEXT NOT NULL CHECK(json_valid(result)),PRIMARY KEY(project_scope,operation,idempotency_key));
''')
            old.execute('INSERT INTO local_project VALUES(?,?,?,?,?,?,?,?)', (self.project_id, str(self.repo).casefold(), grant['rootIdentity'], grant['gitDir'], grant['commonDir'], json.dumps(grant), 'preserve-me', 1))
            old.execute('INSERT INTO project_setting VALUES(?,?,?,?)', (self.project_id, 'recentLimit', '17', 2))
            old.commit()
        connect = sqlite3.connect
        class Interrupted(sqlite3.Connection):
            def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
                if sql.startswith('CREATE TABLE change_file'):
                    raise sqlite3.OperationalError('fixture interrupted migration')
                return super().execute(sql, parameters)
        def failing(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
            return connect(database, *args, **{**kwargs, 'factory': Interrupted})
        with patch('app.workspace.store.sqlite3.connect', side_effect=failing):
            with self.assertRaises(sqlite3.OperationalError):
                LocalStore(directory, lambda _: None).open()
        with closing(connect(path)) as old:
            self.assertEqual(old.execute('SELECT version FROM schema_meta').fetchone()[0], 2)
            self.assertIsNone(old.execute("SELECT name FROM sqlite_master WHERE name='change_snapshot'").fetchone())
            self.assertEqual(old.execute('SELECT value FROM project_setting').fetchone()[0], '17')
        backup = next(directory.glob('pre-v6-*.sqlite3'))
        with closing(connect(backup)) as saved:
            self.assertEqual(saved.execute('SELECT version FROM schema_meta').fetchone()[0], 2)
            self.assertEqual(saved.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        upgraded = LocalStore(directory, lambda _: None)
        upgraded.open()
        try:
            assert upgraded.connection is not None
            self.assertEqual(upgraded.connection.execute('SELECT version FROM schema_meta').fetchone()[0], 6)
            self.assertEqual(Projects(upgraded.connection).get(self.project_id)['settings']['recentLimit']['value'], 17)
        finally:
            upgraded.close()

    def test_facets_fixed_bytes_replay_restart_and_ciphertext(self) -> None:
        (self.repo / 'app.py').write_bytes(b'x = 2\r\nprint(x)\r\n')
        self.git('add', 'app.py')
        (self.repo / 'app.py').write_bytes(b'x = 3\r\nprint(x)\r\n')
        before = self.hashes()
        payload = {'items': [{'facet': 'staged', 'path': 'app.py'}, {'facet': 'unstaged', 'path': 'app.py'}], 'includeUntracked': False}
        key = str(uuid.uuid4())
        result = self.snapshots.capture(self.project_id, payload, key)
        self.assertEqual(result['coverage']['read'], 2)
        files = result['files']
        self.assertNotEqual(files[0]['fileId'], files[1]['fileId'])
        self.assertEqual(files[0]['afterHash'], files[1]['beforeHash'])
        self.assertEqual(result, self.snapshots.capture(self.project_id, payload, key))
        self.assertEqual(result['inputHash'], self.snapshots.capture(self.project_id, payload, str(uuid.uuid4()))['inputHash'])
        self.assertEqual(before, self.hashes())
        file = files[0]
        preview = {'snapshotId': result['snapshotId'], 'fileId': file['fileId'], 'side': 'RIGHT', 'startLine': 1, 'endLine': 20}
        shown = self.snapshots.preview(self.project_id, preview)
        self.assertIn('x = 2', [line['text'] for line in shown['lines']])
        row = self.db.execute('SELECT payload FROM change_file WHERE id=?', (file['fileId'],)).fetchone()
        self.assertNotIn(b'print(x)', row[0])
        decoded = json.loads(self.cipher.decrypt(row[0], self.snapshots.aad(self.project_id, result['snapshotId'], file['fileId'])))
        self.assertEqual(base64.b64decode(decoded['after']), b'x = 2\r\nprint(x)\r\n')
        (self.repo / 'app.py').write_bytes(b'live = 900\r\n')
        self.store.close()
        self.store = LocalStore(self.root / 'data', lambda _: None)
        self.store.open()
        assert self.store.connection is not None
        self.snapshots = Snapshots(Projects(self.store.connection), self.cipher)
        self.assertEqual(shown, self.snapshots.preview(self.project_id, preview))

    def test_complete_preimage_secret_generated_binary_and_unselected_are_not_persisted(self) -> None:
        (self.repo / 'secret.py').write_text('password = "abcdefghijklmnop-secret-value"\n', encoding='utf8')
        self.git('add', 'secret.py')
        self.git('commit', '-m', 'secret fixture')
        (self.repo / 'secret.py').write_text('password = None\n', encoding='utf8')
        (self.repo / 'other.py').write_text('UNSELECTED_CANARY=1\n', encoding='utf8')
        (self.repo / 'bundle.min.js').write_text('generated-canary', encoding='utf8')
        (self.repo / 'binary.py').write_bytes(b'x\0y')
        (self.repo / '.env').write_text('EXCLUDED_NAME_CANARY=1', encoding='utf8')
        result = self.capture([{'facet': 'unstaged', 'path': 'secret.py'}, *[{'facet': 'untracked', 'path': name} for name in ('bundle.min.js', 'binary.py', '.env')]], True)
        self.assertEqual({f['exclusionReason'] for f in result['files']}, {'secret_suspected', 'generated_file', 'binary'})
        self.assertEqual(self.db.execute('SELECT count(*) FROM change_file WHERE payload IS NOT NULL').fetchone()[0], 0)
        self.assertNotIn('other.py', json.dumps(result))
        for file in result['files']:
            with self.assertRaisesRegex(ScopeError, 'ACCESS_DENIED'):
                self.snapshots.preview(self.project_id, {'snapshotId': result['snapshotId'], 'fileId': file['fileId'], 'side': 'RIGHT', 'startLine': 1, 'endLine': 10})

    def test_mid_capture_file_index_inventory_and_head_changes_roll_back(self) -> None:
        (self.repo / 'app.py').write_bytes(b'x = 2\r\n')
        payload = {'items': [{'facet': 'unstaged', 'path': 'app.py'}], 'includeUntracked': False}
        changes = [lambda: (self.repo / 'app.py').write_bytes(b'x = 3\r\n'),
                   lambda: self.git('add', 'app.py'),
                   lambda: (self.repo / 'other.py').write_text('new inventory\n'),
                   lambda: self.git('commit', '--allow-empty', '-m', 'head changed')]
        for index, mutate in enumerate(changes):
            (self.repo / 'app.py').write_bytes(f'x = {10 + index}\r\n'.encode())
            called: list[bool] = []
            def checkpoint() -> None:
                called.append(True)
                mutate()
            with self.assertRaisesRegex(ScopeError, 'CAPTURE_CHANGED'):
                self.snapshots.capture(self.project_id, payload, str(uuid.uuid4()), checkpoint)
            self.assertEqual(called, [True])
            self.assertEqual(self.db.execute('SELECT count(*) FROM change_snapshot').fetchone()[0], 0)

    def test_cross_project_tamper_expiry_and_immutable_rows(self) -> None:
        result = self.capture([{'facet': 'selected', 'path': 'app.py'}])
        file = result['files'][0]
        options = {'snapshotId': result['snapshotId'], 'fileId': file['fileId'], 'side': 'RIGHT', 'startLine': 1, 'endLine': 10}
        with self.assertRaisesRegex(ScopeError, 'RESOURCE_NOT_FOUND'):
            self.snapshots.preview(str(uuid.uuid4()), options)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('UPDATE change_snapshot SET input_hash=?', ('f' * 64,))
        row = self.db.execute('SELECT payload FROM change_file WHERE id=?', (file['fileId'],)).fetchone()
        with self.assertRaises(ValueError):
            self.cipher.decrypt(row[0], self.snapshots.aad(self.project_id, result['snapshotId'], str(uuid.uuid4())))
        with patch.object(self.snapshots, 'get', side_effect=ScopeError('INPUT_EXPIRED')):
            with self.assertRaisesRegex(ScopeError, 'INPUT_EXPIRED'):
                self.snapshots.preview(self.project_id, options)
        for value in ('../outside.py', '.git/config', 'C:/outside.py', 'a\\b.py', 'nul.py'):
            with self.assertRaisesRegex(ScopeError, 'PATH_OUTSIDE_SCOPE'):
                self.capture([{'facet': 'selected', 'path': value}])

    def test_unborn_rename_delete_and_lf_line_numbering(self) -> None:
        self.git('mv', 'app.py', 'renamed.py')
        renamed = self.capture([{'facet': 'staged', 'path': 'renamed.py'}])
        self.assertEqual(renamed['files'][0]['oldPath'], 'app.py')
        (self.repo / 'renamed.py').unlink()
        deleted = self.capture([{'facet': 'unstaged', 'path': 'renamed.py'}])
        self.assertIsNone(deleted['files'][0]['afterHash'])
        self.assertIsNotNone(deleted['files'][0]['beforeHash'])
        self.git('symbolic-ref', 'HEAD', 'refs/heads/unborn-fixture')
        (self.repo / 'new.py').write_bytes(b'x = 1\n')
        self.git('add', 'new.py')
        unborn = self.capture([{'facet': 'staged', 'path': 'new.py'}])
        self.assertIsNone(unborn['head'])
