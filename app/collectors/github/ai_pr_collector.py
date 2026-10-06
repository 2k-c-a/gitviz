"""AI 리뷰용 PR 목록·파일 목록·리비전 고정. GitHub API만 사용하며 DB·모델을 호출하지 않는다.

- 모든 요청은 기준 저장소(base)의 API 경로로만 보낸다. PR이 가리키는 fork URL은 따라가지 않는다.
- 취득 도중 PR의 base/head가 바뀌면 확정하지 않고 PRChanged로 알린다.
- 기존 pr_collector.collect_pull_requests(병합된 PR 일괄 수집)는 그대로 둔다.
"""
import base64
import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass, field

import requests
from pydantic import ValidationError

from app.collectors.ai_errors import (CollectionError, InvalidSelection, MergeBaseUnavailable, PRChanged,
                                      RateLimited, ReauthRequired, ResourceNotFound, UpstreamUnavailable)
from app.collectors.github.github_client import GitHubRateLimited
from app.schemas.ai import ErrorCode, InventoryCoverage, PRRevision, canonical_hash

STATES = ('open', 'closed', 'merged')
MAX_PR_PAGE = 50
MAX_FILE_PAGE = 100
GITHUB_FILES_CAP = 3000          # GitHub PR files API가 나열할 수 있는 최대 파일 수
BODY_LIMIT = 8000
COMPARE_MODE = 'merge-base-diff'
_NAME_RE = re.compile(r'^[A-Za-z0-9_.-]+$')
_SHA_RE = re.compile(r'^[0-9a-f]{40}([0-9a-f]{24})?$')
_FILE_TYPES = {'added': 'added', 'removed': 'deleted', 'modified': 'modified', 'changed': 'modified',
               'renamed': 'renamed', 'copied': 'added', 'unchanged': 'modified'}


class InvalidCursor(CollectionError):
    code = ErrorCode.INVALID_STATE


@dataclass(frozen=True)
class RepoRef:
    repository_id: int
    github_id: int
    owner: str
    name: str

    def __post_init__(self):
        if not (_NAME_RE.match(self.owner) and _NAME_RE.match(self.name)) or self.owner in ('.', '..') \
                or self.name in ('.', '..'):
            raise ValueError('저장소 이름이 올바르지 않습니다.')

    @property
    def full_name(self) -> str:
        return f'{self.owner}/{self.name}'


@dataclass
class CapturedRevision:
    repository_id: int
    pr_number: int
    github_pr_id: int
    base_sha: str
    head_sha: str
    merge_base_sha: str
    state_at_capture: str
    metadata: dict = field(default_factory=dict)
    revision_fingerprint: str = ''

    @property
    def revision(self) -> PRRevision:
        return PRRevision(repo_id=self.repository_id, pr_number=self.pr_number, base_sha=self.base_sha,
                          head_sha=self.head_sha, merge_base_sha=self.merge_base_sha)


@dataclass
class PRPage:
    items: list[dict]
    next_cursor: str | None


@dataclass
class PRFilesPage:
    files: list[dict]
    next_cursor: str | None
    base_sha: str
    head_sha: str
    pr_total_files: int | None
    inventory_coverage: InventoryCoverage


# ── 서명한 opaque cursor ────────────────────────────────────────────────

def _secret() -> bytes:
    secret = os.getenv('SESSION_SECRET_KEY', '')
    if not secret:
        raise RuntimeError('SESSION_SECRET_KEY가 필요합니다.')
    return b'gitviz-ai-cursor:' + secret.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def encode_cursor(kind: str, repo_id: int, page: int, **extra) -> str:
    body = json.dumps({'k': kind, 'r': repo_id, 'p': page, **extra}, sort_keys=True, separators=(',', ':')).encode()
    return _b64(body) + '.' + _b64(hmac.new(_secret(), body, hashlib.sha256).digest()[:16])


def decode_cursor(cursor: str, kind: str, repo_id: int, **expected) -> dict:
    try:
        body_part, sig_part = cursor.split('.', 1)
        body, sig = _unb64(body_part), _unb64(sig_part)
        data = json.loads(body)
    except Exception:
        raise InvalidCursor('cursor가 올바르지 않습니다.') from None
    good = hmac.compare_digest(sig, hmac.new(_secret(), body, hashlib.sha256).digest()[:16])
    if not good or not isinstance(data, dict) or data.get('k') != kind or data.get('r') != repo_id \
            or not isinstance(data.get('p'), int) or data['p'] < 1 \
            or any(data.get(key) != value for key, value in expected.items()):
        raise InvalidCursor('cursor가 올바르지 않습니다.')
    return data


# ── 요청·오류 분류 ──────────────────────────────────────────────────────

def _request(client, url, params=None):
    try:
        return client.get(url, params=params)
    except GitHubRateLimited as exc:
        raise RateLimited('GitHub 요청 한도에 도달했습니다.', retry_after=exc.wait_seconds) from None
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status == 401:
            raise ReauthRequired('GitHub 인증을 갱신해 주세요.') from None
        if status in (403, 404):
            raise ResourceNotFound('PR 또는 저장소를 찾을 수 없거나 접근 권한이 없습니다.') from None
        if status in (409, 422):
            raise PRChanged('GitHub가 요청한 비교를 거부했습니다.') from None
        raise UpstreamUnavailable('GitHub 요청에 실패했습니다.') from None
    except (requests.ConnectionError, requests.Timeout):
        raise UpstreamUnavailable('GitHub에 연결할 수 없습니다.') from None


def _json(response, kind):
    try:
        data = response.json()
    except ValueError:
        raise UpstreamUnavailable('GitHub 응답을 해석할 수 없습니다.') from None
    if not isinstance(data, kind):
        raise UpstreamUnavailable('GitHub 응답 형식이 예상과 다릅니다.')
    return data


def _repo_url(client, repo: RepoRef, tail: str) -> str:
    return f'{client.BASE_URL}/repos/{repo.owner}/{repo.name}/{tail}'


def _sha(value) -> str:
    if not isinstance(value, str) or not _SHA_RE.match(value):
        raise UpstreamUnavailable('GitHub 응답의 commit SHA가 올바르지 않습니다.')
    return value


def _pr_state(pr: dict) -> str:
    if pr.get('merged') or pr.get('merged_at'):
        return 'merged'
    return 'closed' if pr.get('state') == 'closed' else 'open'


def _heads(pr: dict) -> tuple[str, str]:
    try:
        return _sha(pr['base']['sha']), _sha(pr['head']['sha'])
    except (KeyError, TypeError):
        raise UpstreamUnavailable('GitHub 응답에 base/head 정보가 없습니다.') from None


def _summary(pr: dict, repo: RepoRef) -> dict:
    if not isinstance(pr, dict) or not isinstance(pr.get('number'), int) or not isinstance(pr.get('id'), int):
        raise UpstreamUnavailable('GitHub 응답의 PR 정보가 올바르지 않습니다.')
    base_sha, head_sha = _heads(pr)
    head_repo = (pr.get('head') or {}).get('repo')
    head_full = head_repo.get('full_name') if isinstance(head_repo, dict) else None
    return {
        'number': pr['number'], 'github_pr_id': pr['id'], 'title': pr.get('title') or '',
        'state': _pr_state(pr), 'draft': bool(pr.get('draft')),
        'author_login': (pr.get('user') or {}).get('login'),
        'base_ref': pr['base'].get('ref'), 'base_sha': base_sha,
        'head_ref': pr['head'].get('ref'), 'head_sha': head_sha,
        'head_repo_full_name': head_full,
        'head_repo_deleted': head_full is None,
        'is_fork': head_full is not None and head_full.lower() != repo.full_name.lower(),
        'updated_at': pr.get('updated_at'),
    }


# ── 공개 인터페이스 ─────────────────────────────────────────────────────

def list_ai_prs(client, repo: RepoRef, state: str, cursor: str | None = None, limit: int = 30) -> PRPage:
    """state별 PR 한 페이지. merged는 closed 중 병합된 것만 보인다(페이지가 limit보다 적을 수 있다)."""
    if state not in STATES:
        raise InvalidSelection('state는 open/closed/merged 중 하나여야 합니다.')
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_PR_PAGE:
        raise InvalidSelection(f'limit은 1~{MAX_PR_PAGE}이어야 합니다.')
    page = decode_cursor(cursor, 'pr', repo.repository_id, s=state, l=limit)['p'] if cursor else 1
    response = _request(client, _repo_url(client, repo, 'pulls'), {
        'state': 'open' if state == 'open' else 'closed', 'sort': 'updated', 'direction': 'desc',
        'per_page': limit, 'page': page})
    items = []
    for pr in _json(response, list):
        entry = _summary(pr, repo)
        if entry['state'] != state:      # closed에는 병합된 PR이 섞여 오므로 요청한 state만 남긴다
            continue
        items.append(entry)
    has_next = 'rel="next"' in (response.headers or {}).get('Link', '')
    return PRPage(items=items,
                  next_cursor=encode_cursor('pr', repo.repository_id, page + 1, s=state, l=limit) if has_next else None)


def list_pr_files(client, repo: RepoRef, pr_number: int, cursor: str | None = None,
                  limit: int = MAX_FILE_PAGE) -> PRFilesPage:
    """PR 전체 파일 목록의 한 페이지(고정 입력이 아니다). 현재 base/head SHA와 목록 품질을 함께 준다."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_FILE_PAGE:
        raise InvalidSelection(f'limit은 1~{MAX_FILE_PAGE}이어야 합니다.')
    page = decode_cursor(cursor, 'files', repo.repository_id, n=pr_number, l=limit)['p'] if cursor else 1
    pr = _json(_request(client, _repo_url(client, repo, f'pulls/{pr_number}')), dict)
    base_sha, head_sha = _heads(pr)
    response = _request(client, _repo_url(client, repo, f'pulls/{pr_number}/files'),
                        {'per_page': limit, 'page': page})
    files = []
    for f in _json(response, list):
        files.append({
            'path': f.get('filename', ''), 'old_path': f.get('previous_filename'),
            'change_type': _FILE_TYPES.get(f.get('status'), 'modified'),
            'additions': f.get('additions', 0), 'deletions': f.get('deletions', 0),
            'blob_sha': f.get('sha'), 'has_patch': 'patch' in f})
    total = pr.get('changed_files')
    total = total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else None
    if total is None:
        inventory = InventoryCoverage.unknown
    elif total > GITHUB_FILES_CAP:
        inventory = InventoryCoverage.partial      # API가 3000개까지만 나열한다
    else:
        inventory = InventoryCoverage.complete
    has_next = 'rel="next"' in (response.headers or {}).get('Link', '') and page * limit < GITHUB_FILES_CAP
    return PRFilesPage(
        files=files, base_sha=base_sha, head_sha=head_sha, pr_total_files=total, inventory_coverage=inventory,
        next_cursor=encode_cursor('files', repo.repository_id, page + 1, n=pr_number, l=limit) if has_next else None)


def capture_revision(client, repo: RepoRef, pr_number: int, expected_base_sha: str,
                     expected_head_sha: str) -> CapturedRevision:
    """미리보기 때의 base/head와 일치하는 PR의 리비전을 고정한다.

    PR 조회 → merge-base 조회 → PR 재조회로 취득 중 변경을 감지한다. 확정할 수 없으면 예외다.
    """
    first = _json(_request(client, _repo_url(client, repo, f'pulls/{pr_number}')), dict)
    summary = _summary(first, repo)      # 필수 필드 검증
    base_sha, head_sha = summary['base_sha'], summary['head_sha']
    if (base_sha, head_sha) != (expected_base_sha, expected_head_sha):
        raise PRChanged('PR의 base 또는 head가 미리보기 이후 변경됐습니다.')
    try:
        compare = _json(_request(client, _repo_url(client, repo, f'compare/{base_sha}...{head_sha}')), dict)
    except ResourceNotFound:
        raise MergeBaseUnavailable('PR의 비교 대상 commit을 찾을 수 없습니다.') from None
    merge_commit = compare.get('merge_base_commit')
    try:
        merge_base_sha = _sha((merge_commit or {}).get('sha'))
    except UpstreamUnavailable:
        raise MergeBaseUnavailable('merge-base를 확정할 수 없습니다.') from None
    second = _json(_request(client, _repo_url(client, repo, f'pulls/{pr_number}')), dict)
    if _heads(second) != (base_sha, head_sha) or _pr_state(second) != _pr_state(first):
        raise PRChanged('PR을 가져오는 도중 base 또는 head가 변경됐습니다.')

    body = first.get('body') or ''
    metadata = {
        'title': summary['title'], 'body': body[:BODY_LIMIT], 'body_truncated': len(body) > BODY_LIMIT,
        'author_login': summary['author_login'], 'draft': summary['draft'],
        'base_ref': summary['base_ref'], 'head_ref': summary['head_ref'],
        'head_repo_full_name': summary['head_repo_full_name'], 'head_repo_deleted': summary['head_repo_deleted'],
        'is_fork': summary['is_fork'], 'changed_files': first.get('changed_files'),
        'commits': first.get('commits'), 'additions': first.get('additions'), 'deletions': first.get('deletions'),
    }
    captured = CapturedRevision(
        repository_id=repo.repository_id, pr_number=pr_number, github_pr_id=first['id'], base_sha=base_sha,
        head_sha=head_sha, merge_base_sha=merge_base_sha, state_at_capture=_pr_state(first), metadata=metadata,
        revision_fingerprint=canonical_hash({
            'github_repo_id': repo.github_id, 'github_pr_id': first['id'], 'base_sha': base_sha,
            'head_sha': head_sha, 'merge_base_sha': merge_base_sha, 'compare': COMPARE_MODE}))
    try:
        captured.revision      # 계약 DTO로 검증
    except ValidationError:
        raise UpstreamUnavailable('GitHub 응답의 PR 정보가 올바르지 않습니다.') from None
    return captured


def verify_unchanged(client, repo: RepoRef, pr_number: int, base_sha: str, head_sha: str) -> None:
    """입력 준비를 마치기 전에 PR의 base/head가 고정한 값 그대로인지 다시 확인한다(다르면 PRChanged)."""
    pr = _json(_request(client, _repo_url(client, repo, f'pulls/{pr_number}')), dict)
    if _heads(pr) != (base_sha, head_sha):
        raise PRChanged('PR의 base 또는 head가 입력을 준비하는 동안 변경됐습니다.')
