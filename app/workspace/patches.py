"""Small exact unified text edits. Never invokes Git/apply, a shell or a fuzzy patcher."""
from __future__ import annotations

import re
import codecs
import io
import tokenize
from pathlib import PurePosixPath

from .adapters import ai_core
from .scoped_files import ScopeError, relative_path

MAX_FILES = 3
MAX_CHANGED_LINES = 120
RESERVED = '.gitviz-workspace-'


def validate_encoding(path: str, content: bytes) -> None:
    if path.endswith('.py'):
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(content).readline)
            if codecs.lookup(encoding).name not in ('utf-8', 'utf-8-sig'):
                raise ScopeError('PATCH_ENCODING_UNSUPPORTED')
        except (SyntaxError, LookupError):
            raise ScopeError('PATCH_ENCODING_UNSUPPORTED') from None


def writable_path(path: str) -> str:
    relative_path(path)
    parts = PurePosixPath(path).parts
    name = parts[-1].casefold()
    if (any(p.casefold().startswith(RESERVED) for p in parts)
            or any(p.casefold() in ('.github', '.vscode', '.idea') for p in parts)
            or name in ('package.json', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'pyproject.toml',
                        'setup.py', 'setup.cfg', 'tox.ini', 'makefile', 'dockerfile', 'docker-compose.yml')
            or name.startswith(('.env', 'requirements')) or name.endswith(('.cmd', '.bat', '.ps1'))):
        raise ScopeError('PATCH_SCOPE_UNSUPPORTED')
    return path


def apply_patch_bytes(path: str, original: bytes, patch: str) -> bytes:
    writable_path(path)
    validate_encoding(path, original)
    if len(patch.encode()) > 24000 or '\r' in patch or '\0' in patch:
        raise ScopeError('PATCH_INVALID')
    try:
        text = original.decode('utf-8')
    except UnicodeError:
        raise ScopeError('PATCH_INVALID') from None
    bom = text.startswith('\ufeff')
    if bom:
        text = text[1:]
    crlf = '\r\n' in text
    normalized = text.replace('\r\n', '\n')
    if '\r' in normalized or (crlf and text.count('\n') != text.count('\r\n')):
        raise ScopeError('PATCH_ENCODING_UNSUPPORTED')
    final_newline = normalized.endswith('\n')
    source = normalized.split('\n')
    if final_newline:
        source.pop()
    if normalized == '':
        source = []
    lines = patch.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    if lines[:2] != ['--- a/' + path, '+++ b/' + path]:
        raise ScopeError('PATCH_INVALID')
    output: list[str] = []
    at, cursor, changed, hunks = 2, 0, 0, 0
    while at < len(lines):
        match = re.fullmatch(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: [^\r\n]*)?', lines[at])
        if match is None:
            raise ScopeError('PATCH_INVALID')
        old_start, old_count = int(match[1]), int(match[2] or '1')
        new_start, new_count = int(match[3]), int(match[4] or '1')
        position = old_start - 1 if old_count else old_start
        if position < cursor or position > len(source):
            raise ScopeError('PATCH_INVALID')
        output.extend(source[cursor:position])
        if (new_start - 1 if new_count else new_start) != len(output):
            raise ScopeError('PATCH_INVALID')
        cursor = position
        old_seen = new_seen = 0
        at += 1
        hunks += 1
        while at < len(lines) and not lines[at].startswith('@@ '):
            value = lines[at]
            if not value or value[0] not in ' +-':
                raise ScopeError('PATCH_INVALID')
            kind, content = value[0], value[1:]
            if kind in ' -':
                if cursor >= len(source) or source[cursor] != content:
                    raise ScopeError('PRECONDITION_FAILED')
                cursor += 1
                old_seen += 1
            if kind in ' +':
                output.append(content)
                new_seen += 1
            if kind in '+-':
                changed += 1
            at += 1
        if old_seen != old_count or new_seen != new_count:
            raise ScopeError('PATCH_INVALID')
    if not hunks or not changed or changed > MAX_CHANGED_LINES:
        raise ScopeError('PATCH_TOO_LARGE')
    if len(source) > 30 and changed > max(12, len(source) // 2):
        raise ScopeError('PATCH_TOO_LARGE')
    output.extend(source[cursor:])
    rendered = '\n'.join(output) + ('\n' if output and final_newline else '')
    if crlf:
        rendered = rendered.replace('\n', '\r\n')
    target = (('\ufeff' if bom else '') + rendered).encode()
    validate_encoding(path, target)
    if target == original or not output or ai_core.exclusion(path, original, target):
        raise ScopeError('PATCH_INVALID')
    return target
