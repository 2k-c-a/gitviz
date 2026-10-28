from __future__ import annotations

import hashlib
import sqlite3
import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from typing import Any

from app.workspace.migrations import migrate
from app.workspace.store import LocalStore


def no_acl(_: Path) -> None:
    pass


class RecoveryTests(unittest.TestCase):
    def test_real_sqlite_full_rolls_back_and_preserves_backup(self):
        """Real SQLite capacity exhaustion, not a mocked 'disk full' exception."""
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / "workspace.sqlite3"
            sqlite3.connect(path).close()
            connect = sqlite3.connect

            def limited_connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
                connection = connect(database, *args, **kwargs)
                if Path(database) == path:
                    connection.execute("PRAGMA max_page_count=1")
                return connection

            with patch("app.workspace.store.sqlite3.connect", side_effect=limited_connect):
                with self.assertRaises(sqlite3.OperationalError) as raised:
                    LocalStore(directory, no_acl).open()
            self.assertEqual(raised.exception.sqlite_errorcode, sqlite3.SQLITE_FULL)
            with closing(connect(path)) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone(), ("ok",))
                self.assertEqual(connection.execute("SELECT name FROM sqlite_master").fetchall(), [])
            backups = list(directory.glob("pre-v8-*.sqlite3"))
            self.assertEqual(len(backups), 1)
            with closing(connect(backups[0])) as backup:
                self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            recovered = LocalStore(directory, no_acl)
            recovered.open()
            recovered.close()

    @unittest.skipUnless(os.name == "nt", "Windows process termination fixture")
    def test_kill_each_durable_store_boundary_and_reopen(self):
        phases = ("root", "writer_locked", "database_open", "backup_open", "backup_copying",
                  "backup_complete", "backup_published", "wal_enabled", "transaction_open",
                  "schema_created", "metadata_inserted", "committed")
        code = r'''
import sys,os,threading
from pathlib import Path
sys.path.insert(0,sys.argv[3])
import sqlite3,msvcrt
from app.workspace.store import LocalStore
phase=sys.argv[1];root=Path(sys.argv[2])
def checkpoint(name):
    if name==phase:
        print(name,flush=True)
        threading.Event().wait(20)
        raise RuntimeError('fixture was not terminated')
class Connection(sqlite3.Connection):
    def execute(self,sql,parameters=()):
        result=super().execute(sql,parameters)
        for prefix,name in [('PRAGMA journal_mode=WAL','wal_enabled'),('BEGIN IMMEDIATE','transaction_open'),('CREATE TABLE schema_meta','schema_created'),('INSERT INTO schema_meta','metadata_inserted')]:
            if sql.startswith(prefix):checkpoint(name)
        return result
    def commit(self):
        result=super().commit();checkpoint('committed');return result
    def backup(self,target,**kwargs):
        def progress(status,remaining,total):
            if remaining>0:checkpoint('backup_copying')
        result=super().backup(target,pages=1,progress=progress)
        checkpoint('backup_complete');return result
connect=sqlite3.connect
def wrapped_connect(*args,**kwargs):
    kwargs['factory']=Connection
    return connect(*args,**kwargs)
sqlite3.connect=wrapped_connect
locking=msvcrt.locking
def wrapped_locking(*args):
    result=locking(*args);checkpoint('writer_locked');return result
msvcrt.locking=wrapped_locking
def secure(path):
    if path==root:checkpoint('root')
    elif path.name=='workspace.sqlite3':checkpoint('database_open')
    elif path.name.endswith('.pending'):checkpoint('backup_open')
    elif path.name.startswith('pre-v8-'):checkpoint('backup_published')
store=LocalStore(root,secure);store.open();store.close()
raise RuntimeError('checkpoint not reached')
'''
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                path = directory / "workspace.sqlite3"
                with closing(sqlite3.connect(path)) as connection:
                    connection.execute("CREATE TABLE fixture(payload BLOB)")
                    connection.execute("INSERT INTO fixture VALUES(zeroblob(131072))")
                    connection.execute("DROP TABLE fixture")
                    connection.commit()
                before = hashlib.sha256(path.read_bytes()).digest()
                child = subprocess.Popen([getattr(sys, "_base_executable", sys.executable), "-B", "-c", code,
                                          phase, str(directory), str(Path(sys.prefix) / "Lib/site-packages")],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                lines: queue.Queue[bytes] = queue.Queue()
                assert child.stdout
                output = child.stdout
                threading.Thread(target=lambda: lines.put(output.readline()), daemon=True).start()
                try:
                    self.assertEqual(lines.get(timeout=10).decode().strip(), phase)
                    child.kill()
                    child.communicate(timeout=5)
                    self.assertIsNotNone(child.returncode)
                    if phase in phases[:7]:
                        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)
                    with closing(sqlite3.connect(path)) as connection:
                        self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone(), ("ok",))
                        schema = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
                        self.assertEqual(schema, [('apply_journal',), ('approval',), ('change_file',), ('change_snapshot',), ('command_plan',), ('comparison',), ('impact_analysis',), ('local_operation',), ('local_project',), ('local_usage_ledger',), ('notification',), ('project_setting',), ('proposal',), ('proposal_event',), ('remote_analysis',), ('remote_connection',), ('review_attempt',), ('review_feedback',), ('review_finding',), ('review_run',), ('schema_meta',), ('transfer_consent',), ('validation_approval',), ('validation_run',), ('workspace_event',)] if phase == "committed" else [])
                    for backup in directory.glob("pre-v8-*.sqlite3"):
                        with closing(sqlite3.connect(backup)) as connection:
                            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone(), ("ok",))
                    reopened = LocalStore(directory, no_acl)
                    reopened.open()
                    reopened.close()
                finally:
                    if child.poll() is None:
                        child.kill()
                    child.communicate(timeout=5)

    def test_schema_lock_and_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "data"
            first = LocalStore(directory, no_acl)
            first.open()
            try:
                self.assertIsNotNone(first.connection)
                assert first.connection
                self.assertEqual(first.connection.execute("PRAGMA foreign_keys").fetchone(), (1,))
                self.assertEqual(first.connection.execute("PRAGMA journal_mode").fetchone(), ("wal",))
                self.assertEqual(first.connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall(), [('apply_journal',), ('approval',), ('change_file',), ('change_snapshot',), ('command_plan',), ('comparison',), ('impact_analysis',), ('local_operation',), ('local_project',), ('local_usage_ledger',), ('notification',), ('project_setting',), ('proposal',), ('proposal_event',), ('remote_analysis',), ('remote_connection',), ('review_attempt',), ('review_feedback',), ('review_finding',), ('review_run',), ('schema_meta',), ('transfer_consent',), ('validation_approval',), ('validation_run',), ('workspace_event',)])
                with self.assertRaises(OSError):
                    LocalStore(directory, no_acl).open()
            finally:
                first.close()
            again = LocalStore(directory, no_acl)
            again.open()
            again.close()

    def test_future_schema_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / "workspace.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TABLE schema_meta(version INTEGER)")
                connection.execute("INSERT INTO schema_meta VALUES(9)")
                connection.commit()
            before = hashlib.sha256(path.read_bytes()).digest()
            with self.assertRaisesRegex(ValueError, "UNSUPPORTED_SCHEMA"):
                LocalStore(directory, no_acl).open()
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_failed_migration_preserves_backup_and_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / "workspace.sqlite3"
            sqlite3.connect(path).close()
            with patch("app.workspace.store.migrate", side_effect=sqlite3.OperationalError("full")):
                with self.assertRaises(sqlite3.OperationalError):
                    LocalStore(directory, no_acl).open()
            backups = list(directory.glob("pre-v8-*.sqlite3"))
            self.assertEqual(len(backups), 1)
            with closing(sqlite3.connect(backups[0])) as backup:
                self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            with closing(sqlite3.connect(path)) as source:
                self.assertEqual(source.execute("SELECT name FROM sqlite_master").fetchall(), [])

    def test_transaction_rolls_back(self):
        class FailingConnection(sqlite3.Connection):
            def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
                if sql.startswith("INSERT INTO schema_meta"):
                    raise sqlite3.OperationalError("fixture: disk full")
                return super().execute(sql, parameters)
        with closing(sqlite3.connect(":memory:", isolation_level=None, factory=FailingConnection)) as connection:
            with self.assertRaises(sqlite3.OperationalError):
                migrate(connection)
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master").fetchall(), [])
