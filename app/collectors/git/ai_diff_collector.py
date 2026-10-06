"""PR 선택 파일의 고정 diff 수집. DB·AI·GitHub API를 호출하지 않는다.

비교 기준은 merge-base..head 두 commit의 tree 차이다(GitHub의 PR diff와 같다).
저장소 전체를 받지 않고, 두 commit만 얕게(--depth=1, blob 지연) 가져와 선택한 파일의 blob만 읽는다.
작업 공간은 bare 저장소라 checkout·hook·symlink 해석이 없고, 임의 fork URL·submodule은 따라가지 않는다.
"""
import contextlib
import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.collectors.ai_errors import InputTooLarge, InvalidSelection, MergeBaseUnavailable, UpstreamUnavailable
from app.collectors.git.clone_manager import git_environment
from app.schemas.ai import (ChangeType, ContentCoverage, ContentState, Coverage, Hunk, InventoryCoverage,
                            Limitation, ManifestFile)
from app.services.ai import content_policy

CONTEXT_LINES = 25
ZERO_OID = re.compile(r'^0+$')
GIT_TIMEOUT = 120
_HUNK_RE = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
_STATUS_TYPES = {'A': ChangeType.added, 'D': ChangeType.deleted, 'M': ChangeType.modified,
                 'T': ChangeType.modified, 'R': ChangeType.renamed}


@dataclass(frozen=True)
class ChangedFile:
    path: str
    old_path: str | None
    change_type: ChangeType
    old_oid: str | None
    new_oid: str | None
    old_mode: str
    new_mode: str


@dataclass
class CollectedFile:
    manifest: ManifestFile
    payload: bytes | None          # 보낼 diff(맥락 포함) UTF-8. read일 때만 있다.
    line_map: dict
    side_availability: str         # LEFT / RIGHT / BOTH
    byte_count: int


@dataclass
class CollectedInput:
    merge_base_sha: str
    head_sha: str
    files: list[CollectedFile]
    coverage: Coverage
    policy_version: str = content_policy.POLICY_VERSION
    total_bytes: int = 0
    exclusions: dict[str, str] = field(default_factory=dict)   # path -> Limitation 값


# ── Git 실행 ────────────────────────────────────────────────────────────

def _git(repo_path, *args, env=None, check=True, timeout=GIT_TIMEOUT) -> bytes:
    # --literal-pathspecs: 'a[1].py'나 '*.py' 같은 경로를 glob·pathspec 문법이 아닌 글자 그대로 취급한다.
    cmd = ['git', '--literal-pathspecs', '-c', 'core.quotepath=off', '-c', 'diff.external=', '-c',
           'core.fsmonitor=false', '--git-dir', str(repo_path), *args]
    try:
        proc = subprocess.run(cmd, env=env or git_environment(), capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise UpstreamUnavailable('Git 작업 시간이 초과됐습니다.') from None
    if check and proc.returncode != 0:
        raise UpstreamUnavailable('Git 작업에 실패했습니다.')
    return proc.stdout


def fetch_revision_objects(workdir, remote, merge_base_sha, head_sha, *, token=None, protocols='https'):
    """merge-base와 head commit을 workdir(bare)로 가져온다. remote는 호출자가 검증한 URL이다."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    env = git_environment(token)
    env['GIT_ALLOW_PROTOCOL'] = protocols
    subprocess.run(['git', 'init', '--bare', '-q', str(workdir)], env=env, check=True, capture_output=True)
    cfg = [('remote.origin.url', remote), ('remote.origin.promisor', 'true'),
           ('remote.origin.partialclonefilter', 'blob:none'), ('core.symlinks', 'false'),
           ('submodule.recurse', 'false'), ('fetch.recurseSubmodules', 'false')]
    for key, value in cfg:
        _git(workdir, 'config', key, value, env=env)
    _git(workdir, 'fetch', '--no-tags', '--no-recurse-submodules', '--depth=1', '--filter=blob:none',
         'origin', merge_base_sha, head_sha, env=env)
    for sha in (merge_base_sha, head_sha):
        if _git(workdir, 'cat-file', '-t', sha, env=env, check=False).strip() != b'commit':
            raise UpstreamUnavailable('PR의 commit을 가져오지 못했습니다.')
    return workdir


def _remove_tree(root):
    """git 객체 파일은 읽기 전용이라 Windows에서는 권한을 풀어야 지워진다."""
    def writable(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    shutil.rmtree(root, onexc=writable)


@contextlib.contextmanager
def revision_workspace(remote, merge_base_sha, head_sha, *, token=None, protocols='https'):
    """임시 bare 작업 공간. 블록을 벗어나면 원문과 함께 삭제한다."""
    root = tempfile.mkdtemp(prefix='gitviz-ai-')
    try:
        yield fetch_revision_objects(Path(root) / 'repo.git', remote, merge_base_sha, head_sha,
                                     token=token, protocols=protocols)
    finally:
        _remove_tree(root)


def _env(protocols='https', token=None):
    env = git_environment(token)
    env['GIT_ALLOW_PROTOCOL'] = protocols
    return env


# ── 변경 목록 ───────────────────────────────────────────────────────────

def list_changed_files(repo_path, merge_base_sha, head_sha, *, env=None) -> list[ChangedFile]:
    """merge-base..head의 전체 변경 파일 목록. 두 commit의 tree만 읽고 blob은 내려받지 않는다.

    이름 변경은 OID가 같은 경우(R100)만 묶는다. 내용이 바뀐 이름 변경의 짝은 선택한 파일에 한해
    `_pair_renames`가 찾는다(유사도 판정에는 blob이 필요하다). 바이너리 여부도 내용을 읽을 때 판단한다.
    """
    env = env or _env()
    raw = _git(repo_path, 'diff', '--raw', '--no-abbrev', '-z', '-M100%', '--no-ext-diff', '--no-textconv',
               merge_base_sha, head_sha, env=env)
    return _parse_raw(raw)


def _parse_raw(raw: bytes) -> list[ChangedFile]:
    parts = raw.split(b'\0')
    files, i = [], 0
    while i < len(parts) and parts[i]:
        meta = parts[i].decode('utf-8', 'replace').lstrip(':').split(' ')
        old_mode, new_mode, old_oid, new_oid, status = meta[0], meta[1], meta[2], meta[3], meta[4]
        kind = status[0]
        if kind == 'R':
            old_path = parts[i + 1].decode('utf-8', 'replace')
            path = parts[i + 2].decode('utf-8', 'replace')
            i += 3
        else:
            old_path = None
            path = parts[i + 1].decode('utf-8', 'replace')
            i += 2
        if kind not in _STATUS_TYPES:
            kind = 'M'
        files.append(ChangedFile(
            path=path, old_path=old_path, change_type=_STATUS_TYPES[kind],
            old_oid=None if ZERO_OID.match(old_oid) else old_oid,
            new_oid=None if ZERO_OID.match(new_oid) else new_oid,
            old_mode=old_mode, new_mode=new_mode))
    return files


MAX_RENAME_CANDIDATES = 20


def _pair_renames(repo_path, merge_base_sha, head_sha, by_path, selected, env) -> None:
    """선택한 '추가' 파일 중 내용이 바뀐 이름 변경의 원본을 찾아 by_path를 갱신한다.

    비교 대상은 선택한 추가 파일과 정책상 읽을 수 있는 삭제 파일 후보(최대 MAX_RENAME_CANDIDATES개)로
    제한해, 그 밖의 파일 blob은 내려받지 않는다. 후보가 많으면 짝짓기를 건너뛰고 추가 파일로 둔다.
    """
    added = [p for p in selected if by_path[p].change_type is ChangeType.added
             and content_policy.mode_exclusion(None, by_path[p].new_mode) is None
             and content_policy.path_exclusion(p) is None]
    candidates = [c.path for c in by_path.values() if c.change_type is ChangeType.deleted
                  and content_policy.mode_exclusion(c.old_mode, None) is None
                  and content_policy.path_exclusion(c.path) is None]
    if not added or not candidates or len(candidates) > MAX_RENAME_CANDIDATES:
        return
    raw = _git(repo_path, 'diff', '--raw', '--no-abbrev', '-z', '-M', '--no-ext-diff', '--no-textconv',
               merge_base_sha, head_sha, '--', *added, *candidates, env=env)
    for item in _parse_raw(raw):
        if item.change_type is ChangeType.renamed and item.path in added and item.old_path in by_path:
            by_path[item.path] = item


# ── diff·줄 맵 ──────────────────────────────────────────────────────────

def parse_file_diff(text: str) -> tuple[list[Hunk], dict]:
    """한 파일의 unified diff → (hunks, line_map). 줄 번호는 실제 commit의 1부터 시작하는 번호다.

    line_map = {"left": 보이는 변경 전 줄, "right": 보이는 변경 후 줄,
                "right_to_left": 맥락 줄의 변경 후→변경 전 대응}
    """
    hunks, left, right, mapping = [], [], [], {}
    old_no = new_no = 0
    in_hunk = False
    for line in text.splitlines():
        m = _HUNK_RE.match(line)
        if m:
            old_start, old_len = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            new_start, new_len = int(m.group(3)), int(m.group(4) if m.group(4) is not None else 1)
            hunks.append(Hunk(old_start=old_start, old_lines=old_len, new_start=new_start, new_lines=new_len))
            old_no, new_no, in_hunk = old_start, new_start, True
            continue
        if not in_hunk or line.startswith('\\'):
            continue
        if line.startswith('-'):
            left.append(old_no)
            old_no += 1
        elif line.startswith('+'):
            right.append(new_no)
            new_no += 1
        elif line.startswith(' ') or line == '':
            left.append(old_no)
            right.append(new_no)
            mapping[str(new_no)] = old_no
            old_no += 1
            new_no += 1
    return hunks, {'left': left, 'right': right, 'right_to_left': mapping}


def _side_availability(change_type: ChangeType) -> str:
    return {ChangeType.deleted: 'LEFT', ChangeType.added: 'RIGHT'}.get(change_type, 'BOTH')


def _file_diff(repo_path, merge_base_sha, head_sha, changed: ChangedFile, env) -> bytes:
    specs = [changed.path] + ([changed.old_path] if changed.old_path else [])
    rename = ['-M'] if changed.old_path else []
    return _git(repo_path, 'diff', f'-U{CONTEXT_LINES}', *rename, '--no-color', '--no-ext-diff', '--no-textconv',
                merge_base_sha, head_sha, '--', *specs, env=env)


# ── 선택 파일 수집 ──────────────────────────────────────────────────────

def collect_selected_code(repo_path, merge_base_sha, head_sha, paths, *, max_files=50, max_bytes=2_000_000,
                          protocols='https', pr_total_files: int | None = None) -> CollectedInput:
    """선택한 경로의 고정 diff·manifest·coverage를 만든다.

    - 선택 경로는 PR의 실제 변경 목록과 대조한다(없으면 InvalidSelection).
    - 모든 선택 파일은 read / excluded / failed 중 하나이며 사유가 manifest에 남는다.
    - 확정할 수 없는 비교(commit 없음)는 예외로 알리고 부분 결과를 만들지 않는다.
    - 선택하지 않은 파일의 blob은 내려받지 않는다. 목록은 tree만으로 만들고 내용은 선택 파일에 한해 읽는다.
    - pr_total_files: PR 전체 파일 수(GitHub의 changed_files). 없으면 목록 항목 수(내용이 바뀐 이름 변경은
      삭제+추가 두 항목으로 세어 약간 많을 수 있다).
    """
    env = _env(protocols)
    if not paths:
        raise InvalidSelection('선택한 파일이 없습니다.')
    if len(set(paths)) != len(paths):
        raise InvalidSelection('같은 경로가 중복 선택됐습니다.')
    if len(paths) > max_files:
        raise InputTooLarge('선택한 파일 수가 한도를 넘습니다.')
    for sha in (merge_base_sha, head_sha):
        if _git(repo_path, 'cat-file', '-t', sha, env=env, check=False).strip() != b'commit':
            raise MergeBaseUnavailable('비교할 commit을 찾을 수 없습니다.')

    changed = list_changed_files(repo_path, merge_base_sha, head_sha, env=env)
    by_path = {c.path: c for c in changed}
    unknown = [p for p in paths if p not in by_path]
    if unknown:
        raise InvalidSelection('PR 변경 목록에 없는 경로가 선택됐습니다.')

    _pair_renames(repo_path, merge_base_sha, head_sha, by_path, paths, env)
    files, total, exclusions = [], 0, {}
    for path in paths:
        item = _collect_one(repo_path, merge_base_sha, head_sha, by_path[path], env)
        if item.payload is not None:
            total += item.byte_count
            if total > max_bytes:
                raise InputTooLarge('선택한 코드의 전체 크기가 한도를 넘습니다.')
        else:
            exclusions[path] = item.manifest.exclusion_reason.value
        files.append(item)

    coverage = _coverage(files, pr_total_files if pr_total_files is not None else len(changed))
    return CollectedInput(merge_base_sha=merge_base_sha, head_sha=head_sha, files=files, coverage=coverage,
                          total_bytes=total, exclusions=exclusions)


def _entry(changed: ChangedFile, state: ContentState, *, reason=None, content_hash=None, hunks=None):
    return ManifestFile(path=changed.path, old_path=changed.old_path, change_type=changed.change_type,
                        old_blob_oid=changed.old_oid, new_blob_oid=changed.new_oid, content_hash=content_hash,
                        language=content_policy.language_for(changed.path), content_state=state,
                        exclusion_reason=reason, hunks=hunks or [])


def _skipped(changed: ChangedFile, state: ContentState, reason: Limitation) -> CollectedFile:
    return CollectedFile(manifest=_entry(changed, state, reason=reason), payload=None, line_map={},
                         side_availability=_side_availability(changed.change_type), byte_count=0)


_BINARY_MARKER = re.compile(rb'^(?:Binary files .* differ|GIT binary patch)$', re.MULTILINE)
_DIFF_HEADER = re.compile(rb'^diff --git a/(.*) b/(.*)$', re.MULTILINE)


def _only_expected_file(raw: bytes, changed: ChangedFile) -> bool:
    """diff가 선택한 파일 하나의 것인지 확인한다(헤더가 정확히 하나이고 경로가 일치)."""
    headers = _DIFF_HEADER.findall(raw)
    expected = ((changed.old_path or changed.path).encode(), changed.path.encode())
    return len(headers) == 1 and headers[0] == expected


def _collect_one(repo_path, merge_base_sha, head_sha, changed: ChangedFile, env) -> CollectedFile:
    reason = content_policy.mode_exclusion(changed.old_mode, changed.new_mode)
    if reason is None:
        reason = content_policy.path_exclusion(changed.path, old_path=changed.old_path)
    if reason is not None:
        return _skipped(changed, ContentState.excluded, reason)
    try:
        raw = _file_diff(repo_path, merge_base_sha, head_sha, changed, env)
    except UpstreamUnavailable:
        return _skipped(changed, ContentState.failed, Limitation.fetch_failed)
    if b'\0' in raw or _BINARY_MARKER.search(raw):
        return _skipped(changed, ContentState.excluded, Limitation.binary)
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return _skipped(changed, ContentState.excluded, Limitation.binary)   # 텍스트로 해석할 수 없는 인코딩
    if not _only_expected_file(raw, changed):
        # pathspec이 의도와 다른 파일을 끌어들였다면 전송 범위를 확정할 수 없으므로 막는다.
        return _skipped(changed, ContentState.failed, Limitation.fetch_failed)
    reason = content_policy.content_exclusion(text, byte_count=len(raw))
    if reason is not None:
        return _skipped(changed, ContentState.excluded, reason)
    hunks, line_map = parse_file_diff(text)
    if not hunks:
        return _skipped(changed, ContentState.failed, Limitation.fetch_failed)   # 보낼 변경이 없다
    entry = _entry(changed, ContentState.read, content_hash=hashlib.sha256(raw).hexdigest(), hunks=hunks)
    return CollectedFile(manifest=entry, payload=raw, line_map=line_map,
                         side_availability=_side_availability(changed.change_type), byte_count=len(raw))


def _coverage(files: list[CollectedFile], pr_total: int) -> Coverage:
    read = sum(1 for f in files if f.payload is not None)
    if read == 0:
        content = ContentCoverage.none
    elif read == len(files):
        content = ContentCoverage.complete
    else:
        content = ContentCoverage.partial
    limitations = []
    if len(files) < pr_total:
        limitations.append(Limitation.not_selected)
    for f in files:
        reason = f.manifest.exclusion_reason
        if reason is not None and reason not in limitations:
            limitations.append(reason)
    # 목록은 git이 확정한 merge-base..head 전체 diff이므로 complete다.
    return Coverage(inventory_coverage=InventoryCoverage.complete, selected_content_coverage=content,
                    analysis_coverage=ContentCoverage.none, selected_files=len(files), pr_total_files=pr_total,
                    limitations=limitations)
