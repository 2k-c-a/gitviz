"""Bounded local Git reads with repository hooks, filters and includes disabled."""
from __future__ import annotations

import os
import json
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

from .scoped_files import ScopeError, bounded_text, pinned


def installed_git() -> Path:
    candidates = [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/cmd/git.exe",
                  Path(os.environ.get("LOCALAPPDATA", "C:/missing")) / "Programs/Git/cmd/git.exe"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ScopeError("GIT_NOT_FOUND")


class LocalGit:
    def __init__(self, executable: Path | None = None):
        self.executable = executable or installed_git()
        if not self.executable.is_absolute():
            raise ScopeError("GIT_NOT_FOUND")

    def _configuration(self, grant: dict[str, str]) -> list[str]:
        result = ["-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "-c", "core.attributesFile=" + os.devnull]
        for directory in {grant["gitDir"], grant["commonDir"]}:
            root = Path(directory)
            for name in ("config", "config.worktree"):
                if not (root / name).exists():
                    continue
                value = bounded_text(root / name)
                if re.search(r"(?im)^\s*\[\s*include", value):
                    raise ScopeError("UNSUPPORTED_REPOSITORY")
                if re.search(r"(?im)^\s*(?:promisor|partialclone)\s*=", value):
                    raise ScopeError("UNSUPPORTED_REPOSITORY")
                for filter_name in re.findall(r'(?im)^\s*\[filter\s+"([A-Za-z0-9_.-]+)"\s*\]', value):
                    result += ["-c", f"filter.{filter_name}.clean=", "-c", f"filter.{filter_name}.process=",
                               "-c", f"filter.{filter_name}.required=false"]
                # Reject unrecognized/escaped filter names instead of executing a filter.
                if len(re.findall(r"(?im)^\s*\[filter\b", value)) != len(re.findall(r'(?im)^\s*\[filter\s+"([A-Za-z0-9_.-]+)"\s*\]', value)):
                    raise ScopeError("UNSUPPORTED_REPOSITORY")
            alternate = root / "objects/info/alternates"
            if alternate.exists() and bounded_text(alternate).strip():
                raise ScopeError("METADATA_GRANT_REQUIRED")
        return result

    def head(self, grant: dict[str, str]) -> str:
        """Caller owns metadata pins for write preconditions; never changes Git state."""
        value = self._run(grant, ['rev-parse', '--verify', 'HEAD']).decode('ascii').strip()
        if not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', value):
            raise ScopeError('PRECONDITION_FAILED')
        return value

    def _run(self, grant: dict[str, str], arguments: list[str]) -> bytes:
        environment = {name: os.environ[name] for name in ("SystemRoot", "WINDIR", "TEMP", "TMP") if name in os.environ}
        environment.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                            "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
                            "GIT_ATTR_NOSYSTEM": "1", "GIT_PAGER": "", "LC_ALL": "C"})
        environment.update({"GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": "", "GIT_PROTOCOL_FROM_USER": "0"})
        command = [str(self.executable), "--no-optional-locks", *self._configuration(grant),
                   "--git-dir=" + grant["gitDir"], "--work-tree=" + grant["root"], *arguments]
        try:
            process = subprocess.Popen(command, cwd=grant["root"], env=environment, shell=False,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=0x08000000 if os.name == "nt" else 0)
        except FileNotFoundError:
            raise ScopeError("GIT_NOT_FOUND") from None
        output = bytearray()
        assert process.stdout is not None and process.stderr is not None
        exceeded = threading.Event()
        overflow: dict[str, int | str] = {}

        def drain(stream: Any, keep: bool, limit: int) -> None:
            size = 0
            while chunk := stream.read(4096):
                size += len(chunk)
                if size > limit:
                    if keep:
                        overflow.update(operation='git-' + arguments[0] if arguments[0] in ('status', 'diff', 'log') else 'git-read', limitBytes=limit, observedBytes=size)
                    exceeded.set()
                    process.kill()
                    break
                if keep:
                    output.extend(chunk)

        threads = [threading.Thread(target=drain, args=(process.stdout, True, 262144), daemon=True),
                   threading.Thread(target=drain, args=(process.stderr, False, 65536), daemon=True)]
        for thread in threads:
            thread.start()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise ScopeError("TIMED_OUT") from None
        finally:
            for thread in threads:
                thread.join(timeout=1)
            process.stdout.close()
            process.stderr.close()
        if exceeded.is_set():
            if overflow:
                raise ScopeError("CHANGESET_TOO_LARGE", details=overflow)
            raise ScopeError("OUTPUT_LIMIT")
        if process.returncode:
            raise ScopeError("GIT_UNAVAILABLE")
        return bytes(output)

    def status(self, grant: dict[str, str], recent_limit: int = 20) -> dict[str, Any]:
        if type(recent_limit) is not int or not 1 <= recent_limit <= 100:
            raise ScopeError("INVALID_REQUEST")
        with pinned(grant):
            raw = self._run(grant, ["status", "--porcelain=v2", "-z", "--branch", "--untracked-files=all", "--ignore-submodules=all"])
            try:
                records = iter(raw.decode("utf-8", errors="strict").split("\0"))
                result: dict[str, Any] = {"branch": None, "head": None, "detached": False, "unborn": False,
                                          "staged": [], "unstaged": [], "untracked": [], "conflicts": [], "recentCommits": [],
                                          "limitations": ["submodule-status-not-expanded", "reparse-points-rejected", "config-includes-rejected", "repository-filters-disabled"]}
                for record in records:
                    if record.startswith("# branch.oid "):
                        oid = record[13:]
                        result["unborn"] = oid == "(initial)"
                        result["head"] = None if result["unborn"] else oid
                    elif record.startswith("# branch.head "):
                        branch = record[14:]
                        result["detached"] = branch == "(detached)"
                        result["branch"] = None if result["detached"] else branch
                    elif record.startswith(("1 ", "2 ", "u ")):
                        kind = record[0]
                        fields = record.split(" ", {"1": 8, "2": 9, "u": 10}[kind])
                        path, xy = fields[-1], fields[1]
                        item = {"path": path, "status": xy}
                        if kind == "2":
                            item["oldPath"] = next(records)
                        if kind == "u":
                            result["conflicts"].append(item)
                        else:
                            if xy[0] != ".":
                                result["staged"].append(item)
                            if xy[1] != ".":
                                result["unstaged"].append(item)
                    elif record.startswith("? "):
                        result["untracked"].append({"path": record[2:], "status": "??"})
                if not result["unborn"]:
                    log = self._run(grant, ["log", "--no-show-signature", "--no-notes", f"-n{recent_limit}",
                                            "--format=%H%x00%an%x00%aI%x00%s", "-z", "HEAD", "--"])
                    fields = log.decode("utf-8", errors="strict").split("\0")
                    for offset in range(0, len(fields) - 1, 4):
                        result["recentCommits"].append(dict(zip(("oid", "author", "authoredAt", "subject"), fields[offset:offset + 4], strict=True)))
                result["objectFormat"] = self._run(grant, ["rev-parse", "--show-object-format"]).decode("ascii").strip()
                if any((Path(grant[key]) / "info/sparse-checkout").exists() for key in ("gitDir", "commonDir")):
                    result["limitations"].append("sparse-checkout-metadata-present")
                counts = [len(result[key]) for key in ('staged', 'unstaged', 'untracked', 'conflicts')]
                if max(counts) > 2000:
                    raise ScopeError('CHANGESET_TOO_LARGE', details={'operation': 'status', 'limitItems': 2000, 'observedItems': max(counts)})
                size = len(json.dumps(result, ensure_ascii=False).encode())
                if size > 200000:
                    raise ScopeError('CHANGESET_TOO_LARGE', details={'operation': 'status', 'limitBytes': 200000, 'observedBytes': size})
                return result
            except (UnicodeError, StopIteration, IndexError):
                raise ScopeError("UNSUPPORTED_FILENAME") from None

    def inventory(self, grant: dict[str, str], state: dict[str, Any]) -> list[dict[str, str]]:
        if state['conflicts']:
            raise ScopeError('CONFLICT')
        baseline = state['head'] or self._run(grant, ['hash-object', '-t', 'tree', '--stdin']).decode('ascii').strip()
        result: list[dict[str, str]] = []
        for facet in ('staged', 'unstaged'):
            args = ['diff', '--raw', '-z', '--no-abbrev', '--find-renames', '--no-ext-diff', '--no-textconv', '--ignore-submodules=all']
            if facet == 'staged':
                args += ['--cached', baseline]
            raw = self._run(grant, [*args, '--'])
            try:
                fields = raw.decode('utf-8', 'strict').split('\0')
                records = iter(fields)
                for header in records:
                    if not header:
                        continue
                    path = next(records)
                    old_mode, new_mode, old_oid, new_oid, status = header.lstrip(':').split(' ')
                    old_path = path if status.startswith('R') else None
                    if old_path is not None:
                        path = next(records)
                    item = {'facet': facet, 'path': path, 'oldMode': old_mode, 'newMode': new_mode,
                            'oldOid': old_oid, 'newOid': new_oid, 'status': status}
                    if old_path is not None:
                        item['oldPath'] = old_path
                    result.append(item)
            except (ValueError, UnicodeError, StopIteration):
                raise ScopeError('UNSUPPORTED_FILENAME') from None
        result += [{'facet': 'untracked', 'path': item['path'], 'status': '??', 'oldMode': '000000',
                    'newMode': '100644', 'oldOid': '', 'newOid': ''} for item in state['untracked']]
        return sorted(result, key=lambda item: (item['facet'], item['path']))

    def blob(self, grant: dict[str, str], oid: str, object_format: str) -> bytes | None:
        if not oid or set(oid) == {'0'}:
            return None
        if not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', oid):
            raise ScopeError('INVALID_REQUEST')
        size = int(self._run(grant, ['cat-file', '-s', oid]).decode('ascii').strip())
        if size > 200000:
            raise ScopeError('INPUT_TOO_LARGE')
        value = self._run(grant, ['cat-file', 'blob', oid])
        import hashlib
        digest = hashlib.new(object_format, f'blob {len(value)}\0'.encode() + value).hexdigest()
        if digest != oid:
            raise ScopeError('CAPTURE_CHANGED')
        return value
