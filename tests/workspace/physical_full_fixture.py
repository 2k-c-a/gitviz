"""Explicit-only acceptance fixture for a disposable, bounded P01 NTFS volume."""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from app.workspace.dispatcher import Dispatcher
from app.workspace.store import LocalStore
from app.workspace.windows import secure_path
from app.workspace.migrations import SCHEMA_VERSION


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capacity(root: Path) -> tuple[int, int]:
    free, total, available = (ctypes.c_ulonglong() for _ in range(3))
    if not ctypes.windll.kernel32.GetDiskFreeSpaceExW(str(root), ctypes.byref(available),
                                                    ctypes.byref(total), ctypes.byref(free)):
        raise ctypes.WinError()
    return available.value, total.value


def run(root: Path) -> dict[str, Any]:
    assert os.name == "nt" and not ctypes.windll.shell32.IsUserAnAdmin()
    assert root == Path(root.anchor) and root.drive.upper() not in ("C:", "D:")
    label, filesystem = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(64)
    assert ctypes.windll.kernel32.GetVolumeInformationW(str(root), label, 64, None, None, None, filesystem, 64)
    assert label.value == "P01FULL" and filesystem.value == "NTFS"
    assert 16 * 1024**2 <= capacity(root)[1] <= 64 * 1024**2
    assert (root / "P01-DISPOSABLE.tag").read_text() == "GitViz-P01-physical-full-v1"
    legacy, active, bootstrap = (root / name for name in ("legacy", "active", "bootstrap"))
    for directory in (legacy, active, bootstrap):
        directory.mkdir()
    source = legacy / "workspace.sqlite3"
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("CREATE TABLE fixture(value BLOB)")
        connection.execute("INSERT INTO fixture VALUES(zeroblob(131072))")
        connection.execute("DROP TABLE fixture")
        connection.commit()
    preserved = legacy / "preexisting-backup.sqlite3"
    with closing(sqlite3.connect(source)) as connection, closing(sqlite3.connect(preserved)) as target:
        connection.backup(target)
    before, backup_before = digest(source), digest(preserved)
    writer = LocalStore(active, secure_path)
    writer.open()
    assert writer.connection is not None
    baseline = writer.connection.execute("SELECT * FROM schema_meta").fetchall()
    seeded = LocalStore(bootstrap, secure_path)
    seeded.open()
    seeded.close()
    filler = root / "P01-filler.bin"
    fill_errors: list[dict[str, int | None]] = []
    with filler.open("xb", buffering=0) as stream:
        for size in (1024**2, 4096):
            block = b"F" * size
            while True:
                try:
                    if stream.write(block) != size:
                        break
                except OSError as error:
                    fill_errors.append({"errno": error.errno, "winerror": error.winerror})
                    break
        os.fsync(stream.fileno())
    full_free = capacity(root)[0]
    assert full_free == 0, full_free
    assert any(item["winerror"] == 112 or item["errno"] == 28 for item in fill_errors)
    errors = {}
    failed = LocalStore(legacy, secure_path)
    try:
        failed.open()
        raise AssertionError("backup/migration unexpectedly succeeded on full volume")
    except (OSError, sqlite3.Error) as error:
        errors["backup"] = {"class": type(error).__name__, "sqliteCode": getattr(error, "sqlite_errorcode", None),
                            "winerror": getattr(error, "winerror", None)}
    finally:
        failed.close()
    assert digest(source) == before and digest(preserved) == backup_before
    try:
        writer.connection.execute("BEGIN IMMEDIATE")
        writer.connection.execute("UPDATE schema_meta SET app_version=?", ("X" * 1024**2,))
        writer.connection.commit()
        raise AssertionError("WAL write unexpectedly succeeded on full volume")
    except sqlite3.Error as error:
        errors["wal"] = {"class": type(error).__name__, "sqliteCode": error.sqlite_errorcode}
        assert error.sqlite_errorcode == sqlite3.SQLITE_FULL
        if writer.connection.in_transaction:
            writer.connection.rollback()
    assert writer.connection.execute("SELECT * FROM schema_meta").fetchall() == baseline
    writer.close()
    message = {"type": "bootstrap", "protocolVersion": 1, "keyVersion": 1,
               "keyBase64": base64.b64encode(b"P" * 32).decode("ascii")}
    dispatcher = Dispatcher(bootstrap, secure_path)
    dispatcher.negotiated = True
    dispatcher.bootstrap(message)
    assert dispatcher.state == "unavailable" and dispatcher.error_code == "STORAGE_UNAVAILABLE"
    dispatcher.close()
    # Remove only this known fixture filler on the validated disposable volume.
    filler.unlink()
    assert capacity(root)[0] > 1024**2
    for path in (source, preserved, active / "workspace.sqlite3", bootstrap / "workspace.sqlite3"):
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert digest(source) == before and digest(preserved) == backup_before
    reopened = LocalStore(legacy, secure_path)
    reopened.open()
    reopened.close()
    backups = list(legacy.glob(f"pre-v{SCHEMA_VERSION}-*.sqlite3"))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    recovered = Dispatcher(bootstrap, secure_path)
    recovered.negotiated = True
    recovered.bootstrap(message)
    assert recovered.state == "ready"
    recovered.close()
    return {"status": "passed", "standardUser": True, "filesystem": filesystem.value,
            "volumeBytes": capacity(root)[1], "fullFreeBytes": full_free, "fillErrors": fill_errors,
            "storageErrors": errors, "checks": ["physical-ENOSPC", "backup-source-preserved",
            "existing-backup-preserved", "WAL-rollback", "bootstrap-fail-closed", "integrity-after-full",
            "migration-after-space-release", "bootstrap-after-space-release"]}


if __name__ == "__main__":
    report = Path(sys.argv[2])
    assert report.parent == Path(r"C:\P01\results") and report.name == "physical-full.json"
    try:
        result = run(Path(sys.argv[1]))
    except Exception as error:
        result = {"status": "failed", "class": type(error).__name__, "message": str(error),
                  "line": error.__traceback__.tb_lineno if error.__traceback__ else 0}
    report.write_text(json.dumps(result), encoding="utf-8")
    raise SystemExit(0 if result["status"] == "passed" else 1)
