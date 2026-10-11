"""Pinned commit reviews. Merge commits use their first parent; roots use an empty tree."""
from app.collectors.ai_errors import PRChanged, UpstreamUnavailable
from app.collectors.github.ai_pr_collector import (
    CapturedRevision, PRFilesPage, PRPage, _request, _json, _repo_url, _sha,
    _FILE_TYPES, encode_cursor, decode_cursor, BODY_LIMIT,
)
from app.schemas.ai import InventoryCoverage, canonical_hash

EMPTY_TREE_SHA = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'


def list_ai_commits(client, repo, cursor=None, limit=30):
    page = decode_cursor(cursor, 'commits', repo.repository_id, l=limit)['p'] if cursor else 1
    response = _request(client, _repo_url(client, repo, 'commits'), {'per_page': limit, 'page': page})
    items = []
    for item in _json(response, list):
        if not isinstance(item, dict) or not isinstance(item.get('commit'), dict):
            raise UpstreamUnavailable('커밋 목록의 형식이 올바르지 않습니다.')
        commit = item.get('commit') or {}
        items.append({'sha': _sha(item.get('sha')), 'message': str(commit.get('message') or '')[:1000],
            'author_login': (item.get('author') or {}).get('login'),
            'committed_at': (commit.get('committer') or {}).get('date')})
    next_page = 'rel="next"' in (response.headers or {}).get('Link', '')
    return PRPage(items, encode_cursor('commits', repo.repository_id, page + 1, l=limit) if next_page else None)


def commit_detail(client, repo, sha, *, page=1, limit=100):
    sha = _sha(sha)
    response = _request(client, _repo_url(client, repo, f'commits/{sha}'), {'per_page': limit, 'page': page})
    data = _json(response, dict)
    if _sha(data.get('sha')) != sha:
        raise PRChanged('선택한 커밋과 GitHub 응답의 SHA가 다릅니다.')
    parents = data.get('parents')
    if not isinstance(parents, list) or any(not isinstance(p, dict) for p in parents):
        raise UpstreamUnavailable('커밋의 부모 정보를 확인할 수 없습니다.')
    base = _sha(parents[0].get('sha')) if parents else EMPTY_TREE_SHA
    return data, base, response


def capture_commit(client, repo, sha, expected_base_sha, expected_head_sha):
    data, base, response = commit_detail(client, repo, sha)
    if base != expected_base_sha or sha != expected_head_sha:
        raise PRChanged('커밋의 비교 기준이 미리보기와 다릅니다.')
    message = str((data.get('commit') or {}).get('message') or '')
    title, _, body = message.partition('\n')
    total = len(data.get('files', [])) if 'rel="next"' not in (response.headers or {}).get('Link', '') else None
    return CapturedRevision(repository_id=repo.repository_id, pr_number=None, github_pr_id=None,
        base_sha=base, head_sha=sha, merge_base_sha=base, state_at_capture='commit', source_type='commit',
        metadata={'title': title[:1000], 'body': body[:BODY_LIMIT], 'body_truncated': len(body) > BODY_LIMIT,
                  'changed_files': total, 'parent_count': len(data['parents']), 'comparison': 'first-parent'},
        revision_fingerprint=canonical_hash({'github_repo_id': repo.github_id, 'source_type': 'commit',
            'base_sha': base, 'head_sha': sha, 'comparison': 'first-parent'}))


def list_commit_files(client, repo, sha, cursor=None, limit=100):
    page = decode_cursor(cursor, 'commit-files', repo.repository_id, sha=sha, l=limit)['p'] if cursor else 1
    data, base, response = commit_detail(client, repo, sha, page=page, limit=limit)
    entries = data.get('files')
    if not isinstance(entries, list) or any(not isinstance(f, dict) for f in entries):
        raise UpstreamUnavailable('커밋의 변경 파일 목록을 확인할 수 없습니다.')
    files = [{'path': f.get('filename', ''), 'old_path': f.get('previous_filename'),
        'change_type': _FILE_TYPES.get(f.get('status'), 'modified'), 'additions': f.get('additions', 0),
        'deletions': f.get('deletions', 0), 'blob_sha': f.get('sha'), 'has_patch': 'patch' in f} for f in entries]
    more = 'rel="next"' in (response.headers or {}).get('Link', '') and page * limit < 3000
    complete = page == 1 and not more and 'rel="next"' not in (response.headers or {}).get('Link', '')
    return PRFilesPage(files, encode_cursor('commit-files', repo.repository_id, page + 1, sha=sha, l=limit) if more else None,
        base, sha, len(files) if complete else None, InventoryCoverage.complete if complete else InventoryCoverage.unknown)
