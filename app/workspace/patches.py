"""Small exact unified text edits. Never invokes Git/apply, a shell or a fuzzy patcher."""
from __future__ import annotations

import re
import codecs
import difflib
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


def changed_line_count(before: list[str], after: list[str]) -> int:
    """Removed plus added lines of the minimal line diff between two texts."""
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    return sum((i2 - i1) + (j2 - j1) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != 'equal')


def _lines(content: bytes) -> list[str]:
    text = content.decode('utf-8')
    text = text[1:] if text.startswith('\ufeff') else text
    normalized = text.replace('\r\n', '\n')
    lines = normalized.split('\n')
    return lines[:-1] if normalized.endswith('\n') else lines


def minimal_patch(path: str, original: bytes, target: bytes) -> str:
    """Exact LF unified diff (3 context lines) from original to target, for display and for re-applying."""
    hunks = difflib.unified_diff(_lines(original), _lines(target), fromfile='a/' + path, tofile='b/' + path, lineterm='', n=3)
    return '\n'.join(hunks) + '\n'


def locate(source: list[str], old: list[str], cursor: int, stated: int) -> int:
    """Start index of `old` in `source` at or after `cursor`: the stated line if it matches, else the single nearest match."""
    if not old:
        if not cursor <= stated <= len(source):
            raise ScopeError('PATCH_INVALID')
        return stated
    if cursor <= stated and source[stated:stated + len(old)] == old:
        return stated
    found = [index for index in range(cursor, len(source) - len(old) + 1) if source[index:index + len(old)] == old]
    if not found:
        raise ScopeError('PRECONDITION_FAILED')
    best = min(abs(index - stated) for index in found)
    nearest = [index for index in found if abs(index - stated) == best]
    if len(nearest) != 1:
        raise ScopeError('PRECONDITION_FAILED')  # Two equally close places: refuse rather than guess.
    return nearest[0]


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
        at += 1
        hunks += 1
        body: list[tuple[str, str]] = []
        while at < len(lines) and not lines[at].startswith('@@ '):
            value = lines[at]
            if not value or value[0] not in ' +-':
                raise ScopeError('PATCH_INVALID')
            body.append((value[0], value[1:]))
            at += 1
        old = [content for kind, content in body if kind in ' -']
        # Models count lines in text that carries no line numbers, so the @@ numbers are a hint only (as with
        # `git apply`, an offset is allowed but no fuzz): the context and removed lines must occur exactly, once
        # nearest to the hint, after the previous hunk. Header counts are recomputed from the hunk body.
        stated = old_start - 1 if old_count else old_start
        position = locate(source, old, cursor, stated)
        output.extend(source[cursor:position])
        cursor = position
        for kind, content in body:
            if kind in ' -':
                cursor += 1
            if kind in ' +':
                output.append(content)
    if not hunks:
        raise ScopeError('PATCH_INVALID')
    output.extend(source[cursor:])
    # Size limits apply to what really changes: a model that deletes and re-adds identical lines (a whole-file
    # rewrite) is measured by the lines that differ, not by the length of its patch text.
    changed = changed_line_count(source, output)
    if not changed:
        raise ScopeError('PATCH_NO_CHANGE')  # e.g. the issue was already fixed by an earlier apply.
    if changed > MAX_CHANGED_LINES or (len(source) > 30 and changed > max(12, len(source) // 2)):
        raise ScopeError('PATCH_TOO_LARGE')
    rendered = '\n'.join(output) + ('\n' if output and final_newline else '')
    if crlf:
        rendered = rendered.replace('\n', '\r\n')
    target = (('\ufeff' if bom else '') + rendered).encode()
    validate_encoding(path, target)
    if target == original:
        raise ScopeError('PATCH_NO_CHANGE')
    if not output or ai_core.exclusion(path, original, target):
        raise ScopeError('PATCH_INVALID')
    return target
