"""Single-owner SQLite with pre-migration backup and fail-closed schema checks."""
from __future__ import annotations

import os
import sqlite3
import stat
import uuid
from contextlib import closing
from pathlib import Path
from typing import Callable, BinaryIO

from .migrations import SCHEMA_VERSION, migrate, version


def reject_links(path: Path) -> None:
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PermissionError("ACCESS_DENIED")


class LocalStore:
    def __init__(self, directory: Path, secure: Callable[[Path], None]):
        self.directory = directory
        self.secure = secure
        self.connection: sqlite3.Connection | None = None
        self._lock: BinaryIO | None = None

    def open(self) -> None:
        reject_links(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.secure(self.directory)
        lock_path = self.directory / "writer.lock"
        reject_links(lock_path)
        self._lock = lock_path.open("a+b")
        try:
            self.secure(lock_path)
            if os.name == "nt":
                import msvcrt
                self._lock.seek(0)
                # Locking beyond EOF is supported on Windows.
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            path = self.directory / "workspace.sqlite3"
            for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
                reject_links(candidate)
            existed = path.exists()
            connection = sqlite3.connect(path, timeout=2, isolation_level=None)
            self.connection = connection
            self.secure(path)
            current = version(connection)  # Do not set WAL or migrate unknown schemas.
            if existed and current < SCHEMA_VERSION:
                backup = self.directory / f"pre-v{SCHEMA_VERSION}-{uuid.uuid4()}.sqlite3"
                pending = Path(str(backup) + ".pending")
                with closing(sqlite3.connect(pending)) as target:
                    self.secure(pending)
                    connection.backup(target)
                    if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                        raise sqlite3.DatabaseError("BACKUP_INVALID")
                with pending.open("r+b") as stream:
                    os.fsync(stream.fileno())
                os.replace(pending, backup)
                self.secure(backup)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=2000")
            connection.execute("PRAGMA journal_mode=WAL")
            migrate(connection)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self._lock is not None:
            self._lock.close()
            self._lock = None
