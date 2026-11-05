from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.workspace.git_service import LocalGit
from app.workspace.projects import Projects
from app.workspace.scoped_files import ScopeError
from app.workspace.store import LocalStore
from tests.workspace.support import WorkspaceFixture


class ProjectTests(unittest.TestCase):
    def test_project_settings_and_cursor_remain_isolated_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = Path(shutil.which("git") or "missing").resolve()
            environment = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
            store = LocalStore(root / "data", lambda _: None)
            store.open()
            ids: list[str] = []
            try:
                assert store.connection is not None
                projects = Projects(store.connection, LocalGit(executable))
                for name in ("one", "two"):
                    repo = root / name
                    repo.mkdir()
                    subprocess.run([str(executable), "init", "-b", "main", str(repo)], check=True, env=environment, capture_output=True)
                    ticket = projects.inspect(str(repo), [], False)["selectionId"]
                    ids.append(projects.register(ticket, str(uuid.uuid4()))["id"])
                projects.update(ids[0], {"recentLimit": 5, "expectedVersion": 1}, str(uuid.uuid4()))
                self.assertEqual(projects.get(ids[1])["settings"]["recentLimit"], {"value": 20, "version": 1})
                first = projects.list(None, 1)
                second = projects.list(first["nextCursor"], 1)
                self.assertEqual({first["items"][0]["id"], second["items"][0]["id"]}, set(ids))
                self.assertIsNone(second["nextCursor"])
                with self.assertRaisesRegex(ScopeError, "RESOURCE_NOT_FOUND"):
                    projects.get(str(uuid.uuid4()))
            finally:
                store.close()
            again = LocalStore(root / "data", lambda _: None)
            again.open()
            try:
                assert again.connection is not None
                projects = Projects(again.connection, LocalGit(executable))
                self.assertEqual(projects.get(ids[0])["settings"]["recentLimit"]["value"], 5)
                self.assertEqual(projects.get(ids[1])["settings"]["recentLimit"]["value"], 20)
            finally:
                again.close()

    def test_v1_migration_failure_after_first_new_table_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "workspace.sqlite3"
            connect = sqlite3.connect
            with closing(connect(path)) as connection:
                connection.execute("CREATE TABLE schema_meta(version INTEGER PRIMARY KEY CHECK(version=1),applied_at TEXT NOT NULL,app_version TEXT NOT NULL)")
                connection.execute("INSERT INTO schema_meta VALUES(1,'fixture','0.1.0')")
                connection.commit()

            class Interrupted(sqlite3.Connection):
                def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
                    if sql.startswith("CREATE TABLE project_setting"):
                        raise RuntimeError("fixture interrupted after local_project creation")
                    return super().execute(sql, parameters)

            def broken_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
                return connect(*args, **kwargs, factory=Interrupted)

            with patch("app.workspace.store.sqlite3.connect", side_effect=broken_connect):
                with self.assertRaisesRegex(RuntimeError, "fixture interrupted"):
                    LocalStore(root, lambda _: None).open()
            with closing(connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("schema_meta",)])
                self.assertEqual(connection.execute("SELECT * FROM schema_meta").fetchall(), [(1, "fixture", "0.1.0")])
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            backup, = root.glob("pre-v8-*.sqlite3")
            with closing(connect(backup)) as connection:
                self.assertEqual(connection.execute("SELECT * FROM schema_meta").fetchall(), [(1, "fixture", "0.1.0")])
            reopened = LocalStore(root, lambda _: None)
            reopened.open()
            reopened.close()

    def test_registration_settings_replay_and_unregister_preserve_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "project"
            repo.mkdir()
            executable = Path(shutil.which("git") or "missing").resolve()
            env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
            subprocess.run([str(executable), "init", "-b", "main", str(repo)], check=True, capture_output=True, env=env)
            store = LocalStore(root / "data", lambda _: None)
            store.open()
            try:
                assert store.connection is not None
                projects = Projects(store.connection, LocalGit(executable))
                ticket = projects.inspect(str(repo), [], False)["selectionId"]
                key = str(uuid.uuid4())
                before = {str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest() for path in repo.rglob("*") if path.is_file()}
                item = projects.register(ticket, key)
                project_id = item["id"]
                self.assertEqual(projects.register(ticket, key), item)
                other_ticket = projects.inspect(str(repo), [], False)["selectionId"]
                self.assertEqual(projects.register(other_ticket, str(uuid.uuid4()))["id"], project_id)
                restarted = Projects(store.connection, LocalGit(executable))
                self.assertEqual(restarted.register(ticket, key), item)
                settings_key = str(uuid.uuid4())
                changed = projects.update(project_id, {"recentLimit": 7, "expectedVersion": 1}, settings_key)
                self.assertEqual(changed["settings"]["recentLimit"], {"value": 7, "version": 2})
                self.assertEqual(projects.update(project_id, {"recentLimit": 7, "expectedVersion": 1}, settings_key), changed)
                with self.assertRaisesRegex(ScopeError, "CONFLICT"):
                    projects.update(project_id, {"recentLimit": 8, "expectedVersion": 1}, settings_key)
                with self.assertRaisesRegex(ScopeError, "CONFLICT"):
                    projects.update(project_id, {"recentLimit": 8, "expectedVersion": 1}, str(uuid.uuid4()))
                self.assertEqual(projects.list(None, 1)["items"][0]["id"], project_id)
                moved = root / "moved-project"
                repo.rename(moved)
                self.assertEqual(projects.get(project_id)["accessState"], "unavailable")
                self.assertEqual(projects.inspect(str(moved), [], False)["confirmation"]["kind"], "PROJECT_RECONNECT_REQUIRED")
                reconnect_ticket = projects.inspect(str(moved), [], False, True)["selectionId"]
                reconnected = projects.register(reconnect_ticket, str(uuid.uuid4()))
                self.assertEqual(reconnected["id"], project_id)
                self.assertEqual(reconnected["version"], 2)
                repo = moved
                delete_key = str(uuid.uuid4())
                projects.unregister(project_id, 2, delete_key)
                self.assertTrue(projects.unregister(project_id, 2, delete_key)["unregistered"])
                self.assertEqual(projects.list(None, 1)["items"], [])
                self.assertEqual(store.connection.execute("SELECT * FROM project_setting").fetchall(), [])
                self.assertEqual(before, {str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest() for path in repo.rglob("*") if path.is_file()})
            finally:
                store.close()

    def test_v1_to_v2_backup_and_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "workspace.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TABLE schema_meta(version INTEGER PRIMARY KEY CHECK(version=1),applied_at TEXT NOT NULL,app_version TEXT NOT NULL)")
                connection.execute("INSERT INTO schema_meta VALUES(1,'2026-10-24','0.1.0')")
                connection.commit()
            with closing(sqlite3.connect(path)) as connection:
                before = connection.execute("SELECT * FROM schema_meta").fetchall()
                before_schema = connection.execute("SELECT sql FROM sqlite_master WHERE name='schema_meta'").fetchone()
            store = LocalStore(root, lambda _: None)
            store.open()
            try:
                assert store.connection is not None
                self.assertEqual(store.connection.execute("SELECT version FROM schema_meta").fetchall(), [(8,)])
                backup, = root.glob("pre-v8-*.sqlite3")
                with closing(sqlite3.connect(backup)) as connection:
                    self.assertEqual(connection.execute("SELECT * FROM schema_meta").fetchall(), before)
                    self.assertEqual(connection.execute("SELECT sql FROM sqlite_master WHERE name='schema_meta'").fetchone(), before_schema)
                    self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            finally:
                store.close()


class FileTreeTests(WorkspaceFixture):
    """File picker: one directory per call, ignored and metadata entries hidden, nothing read or written."""

    def setUp(self) -> None:
        super().setUp()
        (self.repo / 'src' / 'sub').mkdir(parents=True)
        (self.repo / 'src' / 'b.py').write_bytes(b'b = 2\n')
        (self.repo / 'src' / 'sub' / 'c.py').write_bytes(b'c = 3\n')
        (self.repo / 'src' / 'debug.log').write_bytes(b'log')
        (self.repo / 'build').mkdir()
        (self.repo / 'build' / 'out.txt').write_bytes(b'generated')
        (self.repo / '.env').write_bytes(b'SECRET=1\n')
        (self.repo / '.gitignore').write_bytes(b'build/\n*.log\n.env\n')

    def test_root_lists_one_level_folders_first_and_hides_ignored_and_git(self) -> None:
        before = self.hashes()
        value = self.projects.tree(self.project_id, '')
        self.assertEqual(self.hashes(), before)
        self.assertEqual([(item['name'], item['kind']) for item in value['entries']],
                         [('src', 'directory'), ('.gitignore', 'file'), ('app.py', 'file')])
        self.assertEqual(value['ignoredCount'], 2)  # build/ and .env
        self.assertEqual(value['skippedCount'], 1)  # .git
        self.assertFalse(value['truncated'])
        self.assertEqual(value['entries'][2]['size'], len(b'x = 1\r\nprint(x)\r\n'))

    def test_subdirectory_uses_root_relative_paths(self) -> None:
        value = self.projects.tree(self.project_id, 'src')
        self.assertEqual([item['path'] for item in value['entries']], ['src/sub', 'src/b.py'])
        self.assertEqual(value['ignoredCount'], 1)  # src/debug.log
        self.assertEqual([item['path'] for item in self.projects.tree(self.project_id, 'src/sub')['entries']], ['src/sub/c.py'])

    def test_rejects_paths_outside_scope_and_missing_folders(self) -> None:
        for directory in ('..', '.git', 'src/../..', 'C:/Windows', 'src\\sub'):
            with self.subTest(directory=directory), self.assertRaises(ScopeError) as caught:
                self.projects.tree(self.project_id, directory)
            self.assertEqual(caught.exception.code, 'PATH_OUTSIDE_SCOPE')
        for directory in ('missing', 'app.py'):
            with self.subTest(directory=directory), self.assertRaises(ScopeError) as caught:
                self.projects.tree(self.project_id, directory)
            self.assertEqual(caught.exception.code, 'RESOURCE_NOT_FOUND')

    def test_nested_repository_is_listed_but_not_expandable(self) -> None:
        (self.repo / 'vendor' / '.git').mkdir(parents=True)
        entry = next(item for item in self.projects.tree(self.project_id, '')['entries'] if item['name'] == 'vendor')
        self.assertEqual(entry['kind'], 'repository')

    def test_locate_returns_current_file_hash_and_rejects_outside_paths(self) -> None:
        value = self.projects.locate(self.project_id, {'path': 'src/b.py', 'line': 3, 'column': 2})
        self.assertEqual(Path(value['absolutePath']).resolve(), (self.repo / 'src' / 'b.py').resolve())
        self.assertEqual(value['sourceHash'], hashlib.sha256(b'b = 2\n').hexdigest())
        self.assertEqual((value['line'], value['column']), (3, 2))
        for path in ('../outside.txt', '.git/config'):
            with self.subTest(path=path), self.assertRaises(ScopeError) as caught:
                self.projects.locate(self.project_id, {'path': path, 'line': 1, 'column': 1})
            self.assertEqual(caught.exception.code, 'PATH_OUTSIDE_SCOPE')
        with self.assertRaises(ScopeError) as caught:
            self.projects.locate(self.project_id, {'path': 'src/missing.py', 'line': 1, 'column': 1})
        self.assertEqual(caught.exception.code, 'RESOURCE_NOT_FOUND')

    def test_results_match_the_shared_response_schema(self) -> None:
        from app.workspace.contracts import validate
        envelope = {'protocolVersion': 1, 'requestId': str(uuid.uuid4()), 'ok': True}
        validate('response', {**envelope, 'result': self.projects.tree(self.project_id, '')})
        validate('response', {**envelope, 'result': self.projects.tree(self.project_id, 'src')})
        validate('response', {**envelope, 'result': self.projects.locate(self.project_id, {'path': 'app.py', 'line': 1, 'column': 1})})

    def test_unchanged_file_from_tree_is_captured_whole_as_selected(self) -> None:
        snapshot = self.capture([{'facet': 'selected', 'path': 'src/sub/c.py'}])
        self.assertEqual([(item['facet'], item['path'], item['state']) for item in snapshot['files']],
                         [('selected', 'src/sub/c.py', 'read')])
        self.assertEqual(snapshot['files'][0]['beforeHash'], snapshot['files'][0]['afterHash'])
