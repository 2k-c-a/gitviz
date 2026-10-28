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
            backup, = root.glob("pre-v7-*.sqlite3")
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
                self.assertEqual(store.connection.execute("SELECT version FROM schema_meta").fetchall(), [(7,)])
                backup, = root.glob("pre-v7-*.sqlite3")
                with closing(sqlite3.connect(backup)) as connection:
                    self.assertEqual(connection.execute("SELECT * FROM schema_meta").fetchall(), before)
                    self.assertEqual(connection.execute("SELECT sql FROM sqlite_master WHERE name='schema_meta'").fetchone(), before_schema)
                    self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            finally:
                store.close()
