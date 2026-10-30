from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any

from app.workspace.crypto import PayloadCipher
from app.workspace.git_service import LocalGit
from app.workspace.projects import Projects
from app.workspace.snapshots import Snapshots
from app.workspace.store import LocalStore


class WorkspaceFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / '한글 project'
        self.repo.mkdir()
        self.executable = Path(shutil.which('git') or 'missing').resolve()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'P04 fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        (self.repo / 'app.py').write_bytes(b'x = 1\r\nprint(x)\r\n')
        self.git('add', 'app.py')
        self.git('commit', '-m', 'fixture')
        self.store = LocalStore(self.root / 'data', lambda _: None)
        self.store.open()
        assert self.store.connection is not None
        self.db = self.store.connection
        self.projects = Projects(self.db, LocalGit(self.executable))
        ticket = self.projects.inspect(str(self.repo), [], False)['selectionId']
        self.project_id: str = self.projects.register(ticket, str(uuid.uuid4()))['id']
        self.cipher = PayloadCipher(b'P' * 32, 1)
        self.snapshots = Snapshots(self.projects, self.cipher)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def git(self, *args: str) -> bytes:
        return subprocess.run([str(self.executable), '-C', str(self.repo), *args], check=True, capture_output=True,
            env={**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}).stdout

    def hashes(self) -> dict[str, str]:
        return {str(path.relative_to(self.repo)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.repo.rglob('*') if path.is_file()}

    def capture(self, items: list[dict[str, str]], include: bool = False) -> dict[str, Any]:
        return self.snapshots.capture(self.project_id, {'items': items, 'includeUntracked': include}, str(uuid.uuid4()))
