"""Explicit repository grants. Reparse points are fail-closed in P02."""
from __future__ import annotations

import ctypes
import os
import stat
import re
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Generator


class ScopeError(Exception):
    def __init__(self, code: str, requested_path: str | None = None):
        super().__init__(code)
        self.code = code
        self.requested_path = requested_path


def identity(path: Path) -> str:
    info = path.stat(follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ScopeError("PATH_OUTSIDE_SCOPE")
    return f"{info.st_dev}:{info.st_ino}"


def canonical(path: Path) -> Path:
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
        raise ScopeError("PATH_OUTSIDE_SCOPE")
    for parent in (*reversed(path.parents), path):
        identity(parent)
    result = path.resolve(strict=True)
    if not result.is_dir():
        raise ScopeError("NOT_A_REPOSITORY")
    return result


def bounded_text(path: Path, limit: int = 65536) -> str:
    identity(path)
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ScopeError("OUTPUT_LIMIT")
    try:
        return value.decode("utf-8-sig", errors="strict")
    except UnicodeError:
        raise ScopeError("UNSUPPORTED_FILENAME") from None


def relative_path(value: str) -> str:
    parts = value.split('/')
    if (not value or len(value) > 2048 or '\\' in value or ':' in value
            or any(ord(char) < 32 for char in value)
            or any(part in ('', '.', '..') or part.endswith((' ', '.'))
                   or re.fullmatch(r'(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part) for part in parts)
            or any(part.casefold().startswith('.gitviz-workspace-') for part in parts)
            or '.git' in {part.casefold() for part in parts}):
        raise ScopeError('PATH_OUTSIDE_SCOPE')
    return value


def read_scoped(grant: dict[str, str], value: str, limit: int = 200000) -> bytes | None:
    relative_path(value)
    root = Path(grant['root'])
    path = root.joinpath(*value.split('/'))
    for candidate in (*reversed(path.parents), path):
        if candidate == root or root in candidate.parents:
            try:
                identity(candidate)
            except FileNotFoundError:
                return None
    with ExitStack() as stack:
        try:
            _pin(path, stack)
            before = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode):
                raise ScopeError('PATH_OUTSIDE_SCOPE')
            with path.open('rb') as stream:
                content = stream.read(limit + 1)
            after = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            raise ScopeError('CAPTURE_CHANGED') from None
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ScopeError('CAPTURE_CHANGED')
    if len(content) > limit:
        raise ScopeError('INPUT_TOO_LARGE')
    return content


def discover(selected: Path, metadata: set[str] | None = None, expanded: bool = False) -> dict[str, str]:
    selected = canonical(selected)
    root = selected
    while not (root / ".git").exists():
        if root.parent == root:
            raise ScopeError("NOT_A_REPOSITORY")
        root = root.parent
    root = canonical(root)
    if root != selected and not expanded:
        raise ScopeError("ROOT_GRANT_REQUIRED", str(root))
    marker = root / ".git"
    identity(marker)
    if marker.is_dir():
        git_dir = marker
    else:
        text = bounded_text(marker, 4096).strip()
        if not text.startswith("gitdir: "):
            raise ScopeError("NOT_A_REPOSITORY")
        git_dir = root / text[8:]
    git_dir = canonical(git_dir.absolute())
    approved = metadata or set()
    if not git_dir.is_relative_to(root) and str(git_dir) not in approved:
        raise ScopeError("METADATA_GRANT_REQUIRED", str(git_dir))
    common = git_dir
    if (git_dir / "commondir").exists():
        common = canonical((git_dir / bounded_text(git_dir / "commondir", 4096).strip()).absolute())
    if not common.is_relative_to(root) and str(common) not in approved:
        raise ScopeError("METADATA_GRANT_REQUIRED", str(common))
    return {"root": str(root), "rootIdentity": identity(root), "gitDir": str(git_dir),
            "gitIdentity": identity(git_dir), "commonDir": str(common), "commonIdentity": identity(common),
            "selected": str(selected)}


def verify(grant: dict[str, str]) -> None:
    for key, identity_key in (("root", "rootIdentity"), ("gitDir", "gitIdentity"), ("commonDir", "commonIdentity")):
        path = canonical(Path(grant[key]))
        if identity(path) != grant[identity_key]:
            raise ScopeError("PROJECT_MOVED")
    current = discover(Path(grant["root"]), {grant["gitDir"], grant["commonDir"]}, True)
    if any(current[key] != grant[key] for key in ("root", "gitDir", "commonDir", "rootIdentity", "gitIdentity", "commonIdentity")):
        raise ScopeError("PROJECT_MOVED")


def _pin(path: Path, stack: ExitStack) -> None:
    identity(path)
    if os.name != "nt":
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    # No FILE_SHARE_DELETE: ancestors/files cannot be replaced while Git reads them.
    handle = kernel.CreateFileW(str(path), 0, 3, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ScopeError("ACCESS_DENIED")
    stack.callback(kernel.CloseHandle, handle)
    identity(path)


def pin_path(path: Path, stack: ExitStack) -> None:
    """Keep a verified ancestor stable for the approved write protocol."""
    _pin(path, stack)


@contextmanager
def pinned(grant: dict[str, str]) -> Generator[None, None, None]:
    with ExitStack() as stack:
        count = 0
        visited: set[str] = set()
        for key in ("root", "gitDir", "commonDir"):
            root = Path(grant[key])
            for parent in (*reversed(root.parents), root):
                name = os.path.normcase(str(parent))
                if name not in visited:
                    _pin(parent, stack)
                    visited.add(name)
            for directory, children, files in os.walk(root, followlinks=False):
                for name in (*children, *files):
                    path = Path(directory) / name
                    normalized = os.path.normcase(str(path))
                    if normalized in visited:
                        continue
                    count += 1
                    if count > 20000:
                        raise ScopeError("OUTPUT_LIMIT")
                    _pin(path, stack)
                    visited.add(normalized)
        verify(grant)
        yield
        verify(grant)
