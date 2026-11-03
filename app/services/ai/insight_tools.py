"""AI 인사이트(자동·대화형 분석)가 쓰는 읽기 전용 분석 도구.

- 모든 도구는 **한 저장소의 한 스냅샷**에 묶인다. 저장소 ID·기간은 스냅샷에서만 오고 AI가 바꿀 수 없다.
- 계산된 지표(우선순위·BF80·공동 변경·협업 등)는 스냅샷 payload를 그대로 읽는다(재계산 없음).
- 커밋 단위 자료(변경 이력·관련 커밋·기간 비교)는 원본 테이블을 스냅샷과 같은 저장소·관측 구간
  [window_start, window_end)·기본 브랜치 커밋으로 제한해 SELECT만 한다. 사용자·AI 입력은 바인드 파라미터로만 쓴다.
- 결과는 JSON으로 직렬화할 수 있는 dict다. 찾지 못한 대상은 예외 대신 {'error': 코드, 'message', 'candidates'}로 돌려준다.
- 도구 결과에 나온 파일·커밋·기여자·수치만 AI 답변의 근거로 인정한다(Ledger). 이메일은 결과에 넣지 않는다.
- 소스 코드 도구(readFile·searchCode·listDirectory)는 실행할 때 사용자가 코드 열람을 켰고 서버 clone에 기준 커밋이
  있을 때만 동작한다(insight_code). 꺼져 있으면 목록에 보이지 않고 불러도 CODE_ACCESS_DISABLED를 돌려준다.
"""
import json
import math
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from dateutil.relativedelta import relativedelta
from sqlalchemy import text

from app.services.metric_policy import DEFAULT_WINDOW_DAYS, LOW_SUPPORT_THRESHOLD, large_change_threshold

TOOL_VERSION = 'insight-tools-v1'
MAX_LIMIT = 30
DEFAULT_LIMIT = 10
COMMIT_TYPES = ('feature', 'bugfix', 'refactor', 'docs', 'chore', 'other')
SORT_KEYS = ('maintenance_priority', 'change_freq', 'churn', 'bugfix_count', 'bus_factor')
_SHA = re.compile(r'^[0-9a-f]{7,40}$')
_SUBJECT_CHARS = 200


class ToolArgumentError(ValueError):
    """AI가 보낸 도구 인자가 형식에 맞지 않는다(도구를 실행하지 않는다)."""


def _round(value, digits=4):
    return round(float(value), digits) if value is not None else None


def _utc(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value):
    return value.isoformat() if value is not None else None


def _like_prefix(value: str) -> str:
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _subject(message) -> str:
    first = (message or '').strip().split('\n', 1)[0].strip()
    return first[:_SUBJECT_CHARS]


def _directory(path: str, depth: int) -> str:
    parts = path.split('/')
    if len(parts) > depth:
        return '/'.join(parts[:depth])
    return '/'.join(parts[:-1]) if len(parts) > 1 else '(root)'


class InsightData:
    """스냅샷 하나에 묶인 도구 모음. db는 읽기만 한다."""

    def __init__(self, db, snapshot, code=None):
        self.db = db
        self.code = code                                 # CodeReader 또는 None(코드 열람 꺼짐·사용 불가)
        self.snapshot = snapshot
        self.payload = snapshot.payload
        self.repository_id = snapshot.repository_id
        self.window_end = _utc(snapshot.window_end)
        self.window_start = _utc(snapshot.window_start) or self.window_end - timedelta(days=DEFAULT_WINDOW_DAYS)
        self.files = {f['path']: f['id'] for f in self.payload.get('file_index', [])}
        self.file_paths = {f['id']: f['path'] for f in self.payload.get('file_index', [])}
        self.contributors = {c['id']: (c.get('github_login') or c.get('display_name') or '익명')
                             for c in self.payload.get('contributor_index', [])}
        tree_count = (self.payload.get('data_quality') or {}).get('target_tree_file_count') or 0
        self.large_threshold = large_change_threshold(tree_count)

    # ── 공통 ────────────────────────────────────────────────────────────

    @property
    def period(self) -> dict:
        return {'start': _iso(self.window_start), 'end': _iso(self.window_end), 'interval': '[start,end)',
                'snapshot_id': self.snapshot.id, 'target_sha': self.snapshot.target_sha,
                'formula_version': self.snapshot.formula_version}

    def _rows(self, sql: str, params: dict | None = None) -> list[dict]:
        """관측 구간·기본 브랜치 커밋으로 제한한 sc(scoped commits) CTE 위에서 SELECT만 실행한다."""
        body = sql.strip()
        if not body.upper().startswith('SELECT'):
            raise ValueError('도구 조회는 SELECT만 허용합니다.')
        scoped = """
            WITH sc AS (
                SELECT c.id, c.sha, c.contributor_id, c.author_name, c.message, c.commit_type,
                       c.changed_file_count, c.committed_at
                FROM commits c
                WHERE c.repository_id = :insight_rid AND c.committed_at >= :insight_start
                  AND c.committed_at < :insight_end AND c.on_default_branch IS NOT FALSE
            ), sf AS (
                SELECT f.id, f.path FROM files f WHERE f.repository_id = :insight_rid
            ) """ + body
        bound = {**(params or {}), 'insight_rid': self.repository_id, 'insight_start': self.window_start,
                 'insight_end': self.window_end}
        return [dict(r) for r in self.db.execute(text(scoped), bound).mappings().all()]

    def contributor_name(self, contributor_id, fallback=None) -> str:
        if contributor_id is not None and contributor_id in self.contributors:
            return self.contributors[contributor_id]
        return (fallback or '').strip()[:80] or '미확인 기여자'

    def link_ids(self, files, contributors) -> dict:
        """검증을 통과한 경로·기여자 이름을 이 스냅샷의 id로 바꾼다(화면이 기존 상세 보기를 열 때 쓴다)."""
        names = {}
        for name in contributors or []:
            cid, error = self.resolve_contributor(name)
            if not error:
                names[name] = cid
        return {'files': {path: self.files[path] for path in files or [] if path in self.files},
                'contributors': names}

    def resolve_path(self, value: str):
        value = (value or '').strip().strip('/')
        if not value:
            return None, {'error': 'ARGUMENT_REQUIRED', 'message': '파일 경로가 필요합니다.'}
        if value in self.files:
            return value, None
        lowered = value.lower()
        matches = [p for p in self.files if p.lower() == lowered or p.lower().endswith('/' + lowered)]
        if len(matches) == 1:
            return matches[0], None
        candidates = matches or [p for p in self.files if lowered in p.lower()]
        return None, {'error': 'FILE_NOT_FOUND' if not matches else 'FILE_AMBIGUOUS',
                      'message': '이 스냅샷에서 파일을 하나로 특정하지 못했습니다.',
                      'query': value[:200], 'candidates': sorted(candidates)[:8]}

    def resolve_directory(self, value: str):
        value = (value or '').strip().strip('/')
        if not value:
            return None, {'error': 'ARGUMENT_REQUIRED', 'message': '디렉터리가 필요합니다.'}
        if any(p.startswith(value + '/') for p in self.files):
            return value, None
        lowered = value.lower()
        found = sorted({_directory(p, value.count('/') + 1) for p in self.files if p.lower().startswith(lowered + '/')})
        if len(found) == 1:
            return found[0], None
        candidates = sorted({d for p in self.files for d in [_directory(p, 2)] if lowered in d.lower()})
        return None, {'error': 'DIRECTORY_NOT_FOUND', 'message': '이 스냅샷에서 디렉터리를 찾지 못했습니다.',
                      'query': value[:200], 'candidates': candidates[:8]}

    def resolve_contributor(self, value: str):
        value = (value or '').strip()
        if not value:
            return None, {'error': 'ARGUMENT_REQUIRED', 'message': '기여자 이름이 필요합니다.'}
        lowered = value.lower().lstrip('@')
        exact = [cid for cid, name in self.contributors.items() if name.lower() == lowered]
        if not exact:
            for c in self.payload.get('contributor_index', []):
                if lowered in {(c.get('github_login') or '').lower(), (c.get('display_name') or '').lower()}:
                    exact.append(c['id'])
        if len(set(exact)) == 1:
            return exact[0], None
        partial = sorted({name for name in self.contributors.values() if lowered in name.lower()})
        return None, {'error': 'CONTRIBUTOR_NOT_FOUND' if not exact else 'CONTRIBUTOR_AMBIGUOUS',
                      'message': '이 스냅샷에서 기여자를 하나로 특정하지 못했습니다.', 'query': value[:80],
                      'candidates': partial[:8]}

    def _limit(self, value, default=DEFAULT_LIMIT) -> int:
        if value in (None, 0, ''):
            return default
        if type(value) is not int or value < 1:
            raise ToolArgumentError('limit은 1 이상의 정수여야 합니다.')
        return min(value, MAX_LIMIT)

    def _risk_files(self) -> list[dict]:
        return list((self.payload.get('risk-scores') or {}).get('files') or [])

    # ── 3.1 프로젝트·커밋 ──────────────────────────────────────────────

    def get_project_overview(self) -> dict:
        summary = self.payload.get('summary') or {}
        quality = self.payload.get('data_quality') or {}
        risk = self._risk_files()
        return {
            'repository_name': (summary.get('repository') or {}).get('name'),
            'default_branch': (summary.get('repository') or {}).get('default_branch'),
            'period': self.period,
            'total_commits': summary.get('total_commits'), 'commits_last_30d': summary.get('commits_30d'),
            'contributors': summary.get('total_contributors'),
            'active_contributors_last_90d': summary.get('active_contributors_90d'),
            'releases': summary.get('total_releases'), 'latest_release': summary.get('latest_release'),
            'bugfix_commit_pct': summary.get('bugfix_pct'),
            'files_changed_in_window': len(risk),
            'files_in_target_tree': quality.get('target_tree_file_count'),
            'data_quality': {k: quality.get(k) for k in sorted(quality) if not isinstance(quality.get(k), (dict, list))},
            'notes': ['커밋 유형은 메시지 기반 분류이며 결함 여부를 확인한 값이 아닙니다.',
                      '모든 수치는 관측 구간 안의 기본 브랜치 커밋 기준입니다.'],
        }

    def get_commit_timeline(self) -> dict:
        timeline = self.payload.get('timeline') or {}
        months = timeline.get('months') or []
        return {'period': self.period, 'unit': 'month',
                'months': [{'month': m, 'commits': timeline['commits'][i],
                            'active_contributors': timeline['active_contributors'][i],
                            'bugfix_pct': timeline['bugfix_pct'][i]} for i, m in enumerate(months)]}

    def get_commit_type_statistics(self) -> dict:
        types = self.payload.get('commit-types') or {}
        rows = self._rows("""
            SELECT to_char(date_trunc('month', committed_at), 'YYYY-MM') AS month,
                   COALESCE(commit_type, 'other') AS commit_type, COUNT(*) AS commits
            FROM sc GROUP BY 1, 2 ORDER BY 1, 2""")
        monthly: dict[str, dict] = {}
        for r in rows:
            monthly.setdefault(r['month'], {'month': r['month']})[r['commit_type']] = r['commits']
        return {'period': self.period, 'total': types.get('total'), 'types': types.get('types') or [],
                'monthly': list(monthly.values()),
                'notes': ['커밋 메시지 분류(feature·bugfix·refactor·docs·chore·other)입니다.']}

    def get_commit_statistics(self) -> dict:
        row = self._rows("""
            SELECT COUNT(*) AS commits, AVG(changed_file_count) AS avg_files_per_commit,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY changed_file_count) AS median_files_per_commit,
                   COUNT(*) FILTER (WHERE changed_file_count >= :large) AS large_commits,
                   COUNT(DISTINCT contributor_id) AS identified_contributors,
                   COUNT(*) FILTER (WHERE contributor_id IS NULL) AS unidentified_commits
            FROM sc""", {'large': self.large_threshold})[0]
        churn = self._rows("""
            SELECT COALESCE(c.commit_type, 'other') AS commit_type, COUNT(DISTINCT c.id) AS commits,
                   SUM(COALESCE(cfc.additions, 0) + COALESCE(cfc.deletions, 0)) AS churn
            FROM sc c JOIN commit_file_changes cfc ON cfc.commit_id = c.id GROUP BY 1 ORDER BY 2 DESC""")
        return {'period': self.period, 'commits': row['commits'],
                'avg_files_per_commit': _round(row['avg_files_per_commit'], 2),
                'median_files_per_commit': _round(row['median_files_per_commit'], 2),
                'large_commit_threshold_files': self.large_threshold, 'large_commits': row['large_commits'],
                'identified_contributors': row['identified_contributors'],
                'unidentified_commits': row['unidentified_commits'],
                'by_type': [{'commit_type': r['commit_type'], 'commits': r['commits'], 'churn_lines': int(r['churn'] or 0)}
                            for r in churn]}

    # ── 3.3 기여자·협업 ────────────────────────────────────────────────

    def get_contributor_statistics(self, limit=None) -> dict:
        limit = self._limit(limit)
        contributors = (self.payload.get('top-contributors') or {}).get('contributors') or []
        exclusive = {o['contributor_id']: o['exclusive_file_count']
                     for o in (self.payload.get('bus-factor') or {}).get('top_owners') or []}
        total = sum(c.get('commit_count') or 0 for c in contributors) or 0
        return {'period': self.period, 'total_contributors': len(contributors),
                'contributors': [{'name': self.contributor_name(c['id']), 'commits': c['commit_count'],
                                  'commit_share': _round(c['commit_count'] / total) if total else None,
                                  'last_commit_at': c.get('last_commit_at'),
                                  'bf80_single_owner_files': exclusive.get(c['id'], 0)} for c in contributors[:limit]],
                'notes': ['커밋 수는 기여 분량의 한 단면이며 역량이나 실제 업무량을 뜻하지 않습니다.']}

    def get_top_collaboration_pairs(self, limit=None) -> dict:
        pairs = self.payload.get('network/top-pairs') or []
        return {'period': self.period, 'total_pairs': len(pairs),
                'pairs': [{'contributor_a': p['contributor_a']['name'], 'contributor_b': p['contributor_b']['name'],
                           'edge_weight': p['edge_weight'], 'co_edit': p['co_edit_raw'],
                           'review_a_to_b': p['review_directed_a_to_b'], 'review_b_to_a': p['review_directed_b_to_a']}
                          for p in pairs[:self._limit(limit)]],
                'notes': ['협업 강도는 같은 파일 공동 수정과 PR 리뷰 관계로 계산한 값입니다.']}

    def get_contributor_collaboration(self, contributor='', limit=None) -> dict:
        cid, error = self.resolve_contributor(contributor)
        if error:
            return error
        pairs = [p for p in self.payload.get('network/top-pairs') or []
                 if cid in (p['contributor_a']['id'], p['contributor_b']['id'])]
        out = []
        for p in pairs[:self._limit(limit)]:
            mine_a = p['contributor_a']['id'] == cid
            other = p['contributor_b'] if mine_a else p['contributor_a']
            out.append({'collaborator': other['name'], 'edge_weight': p['edge_weight'], 'co_edit': p['co_edit_raw'],
                        'reviews_given': p['review_directed_a_to_b'] if mine_a else p['review_directed_b_to_a'],
                        'reviews_received': p['review_directed_b_to_a'] if mine_a else p['review_directed_a_to_b']})
        return {'period': self.period, 'contributor': self.contributor_name(cid), 'collaborators': out,
                'total_collaborators': len(pairs)}

    def get_developer_activity(self, contributor='', limit=None) -> dict:
        cid, error = self.resolve_contributor(contributor)
        if error:
            return error
        limit = self._limit(limit)
        summary = next((c for c in (self.payload.get('top-contributors') or {}).get('contributors') or []
                        if c['id'] == cid), {})
        monthly = self._rows("""
            SELECT to_char(date_trunc('month', committed_at), 'YYYY-MM') AS month, COUNT(*) AS commits
            FROM sc WHERE contributor_id = :cid GROUP BY 1 ORDER BY 1""", {'cid': cid})
        types = self._rows("""
            SELECT COALESCE(commit_type, 'other') AS commit_type, COUNT(*) AS commits
            FROM sc WHERE contributor_id = :cid GROUP BY 1 ORDER BY 2 DESC""", {'cid': cid})
        directories = self._rows("""
            SELECT f.path, COUNT(DISTINCT c.id) AS commits
            FROM sc c JOIN commit_file_changes cfc ON cfc.commit_id = c.id JOIN sf f ON f.id = cfc.file_id
            WHERE c.contributor_id = :cid GROUP BY f.path""", {'cid': cid})
        by_directory: dict[str, int] = {}
        for r in directories:
            key = _directory(r['path'], 2)
            by_directory[key] = by_directory.get(key, 0) + r['commits']
        owned = []
        for fid, analysis in (self.payload.get('ownership') or {}).items():
            share = next((s for s in analysis.get('shares') or [] if s['contributor_id'] == cid), None)
            if share and int(fid) in self.file_paths:
                owned.append({'path': self.file_paths[int(fid)], 'share': _round(share['share']),
                              'bf80': analysis.get('bus_factor'), 'changes_observed': analysis.get('observation_count')})
        owned.sort(key=lambda o: (-o['share'], o['path']))
        collaboration = self.get_contributor_collaboration(self.contributor_name(cid), 5)
        return {'period': self.period, 'contributor': self.contributor_name(cid),
                'commits': summary.get('commit_count'), 'last_commit_at': summary.get('last_commit_at'),
                'monthly_commits': monthly, 'commit_types': types,
                'top_directories': [{'directory': d, 'file_changes': n} for d, n in
                                    sorted(by_directory.items(), key=lambda x: (-x[1], x[0]))[:limit]],
                'files_owned_most': owned[:limit],
                'collaborators': collaboration.get('collaborators', []),
                'notes': ['담당 영역은 변경 기록으로 추정한 것이며 실제 직무·역량을 뜻하지 않습니다.']}

    def get_ownership_concentration(self, limit=None) -> dict:
        bus = self.payload.get('bus-factor') or {}
        limit = self._limit(limit)
        single = [f for f in bus.get('files') or [] if f.get('bus_factor') == 1]
        single.sort(key=lambda f: (-(f.get('total_change_count') or 0), f['path']))
        return {'period': self.period, 'summary': bus.get('summary') or {},
                'distribution': bus.get('distribution') or [],
                'contributors_with_single_owner_files': [{'name': o['name'], 'files': o['exclusive_file_count']}
                                                         for o in (bus.get('top_owners') or [])[:limit]],
                'modules_by_single_owner_ratio': (bus.get('risky_modules') or [])[:limit],
                'single_owner_files': [{'path': f['path'], 'changes': f.get('total_change_count'),
                                        'top_owner': (f.get('owners') or [{}])[0].get('name'),
                                        'top_owner_share': f.get('top_owner_share'),
                                        'low_support': f.get('low_support')} for f in single[:limit]],
                'notes': ['BF80은 변경 기록의 80%를 차지하는 최소 인원이며 실제 지식 보유 인원이 아닙니다.',
                          f'변경 {LOW_SUPPORT_THRESHOLD}회 미만은 표본이 적어(low_support) 해석에 주의합니다.']}

    # ── 3.2 파일·디렉터리 ──────────────────────────────────────────────

    def get_directory_statistics(self, depth=None, limit=None) -> dict:
        depth = 1 if depth in (None, 0) else depth
        if type(depth) is not int or not 1 <= depth <= 4:
            raise ToolArgumentError('depth는 1~4입니다.')
        limit = self._limit(limit)
        rows = self._rows("""
            SELECT f.path, COUNT(DISTINCT c.id) AS commits,
                   COUNT(DISTINCT c.id) FILTER (WHERE c.commit_type = 'bugfix') AS bugfix_commits,
                   COUNT(DISTINCT c.id) FILTER (WHERE c.commit_type = 'refactor') AS refactor_commits,
                   SUM(COALESCE(cfc.additions, 0) + COALESCE(cfc.deletions, 0)) AS churn
            FROM sc c JOIN commit_file_changes cfc ON cfc.commit_id = c.id JOIN sf f ON f.id = cfc.file_id
            GROUP BY f.path""")
        units = self._rows("""
            SELECT f.path, c.id AS commit_id, c.commit_type, c.contributor_id
            FROM sc c JOIN commit_file_changes cfc ON cfc.commit_id = c.id JOIN sf f ON f.id = cfc.file_id""")
        total_commits = self._rows("SELECT COUNT(*) AS n FROM sc")[0]['n'] or 0
        groups: dict[str, dict] = {}
        for r in rows:
            g = groups.setdefault(_directory(r['path'], depth), {'files_changed': 0, 'churn': 0})
            g['files_changed'] += 1
            g['churn'] += int(r['churn'] or 0)
        sets: dict[str, dict] = {}
        for u in units:
            s = sets.setdefault(_directory(u['path'], depth), {'commits': set(), 'bugfix': set(), 'refactor': set(),
                                                                'contributors': set()})
            s['commits'].add(u['commit_id'])
            if u['commit_type'] in ('bugfix', 'refactor'):
                s[u['commit_type']].add(u['commit_id'])
            if u['contributor_id'] is not None:
                s['contributors'].add(u['contributor_id'])
        risk_by_dir: dict[str, list] = {}
        for f in self._risk_files():
            if f.get('maintenance_priority') is not None:
                risk_by_dir.setdefault(_directory(f['path'], depth), []).append(f['maintenance_priority'])
        out = []
        for name, s in sets.items():
            commits = len(s['commits'])
            scores = risk_by_dir.get(name, [])
            out.append({'directory': name, 'commits': commits,
                        'commit_share': _round(commits / total_commits) if total_commits else None,
                        'bugfix_commits': len(s['bugfix']), 'refactor_commits': len(s['refactor']),
                        'files_changed': groups.get(name, {}).get('files_changed', 0),
                        'churn_lines': groups.get(name, {}).get('churn', 0), 'contributors': len(s['contributors']),
                        'avg_maintenance_priority': _round(sum(scores) / len(scores)) if scores else None})
        out.sort(key=lambda d: (-d['commits'], d['directory']))
        return {'period': self.period, 'depth': depth, 'total_commits': total_commits,
                'directory_count': len(out), 'directories': out[:limit],
                'notes': ['한 커밋이 여러 디렉터리를 바꾸면 각 디렉터리에 한 번씩 셉니다.']}

    def get_file_change_frequency(self, sort_by='', directory='', limit=None) -> dict:
        sort_by = sort_by or 'change_freq'
        if sort_by not in SORT_KEYS:
            raise ToolArgumentError('sortBy는 ' + ', '.join(SORT_KEYS) + ' 중 하나입니다.')
        files = self._risk_files()
        if directory:
            directory, error = self.resolve_directory(directory)
            if error:
                return error
            files = [f for f in files if f['path'].startswith(directory + '/')]
        if sort_by == 'bus_factor':
            files = sorted(files, key=lambda f: (f.get('bus_factor') if f.get('bus_factor') is not None else 999,
                                                 -(f.get('change_freq') or 0), f['path']))
        else:
            files = sorted(files, key=lambda f: (-(f.get(sort_by) if f.get(sort_by) is not None else -1), f['path']))
        return {'period': self.period, 'sort_by': sort_by, 'directory': directory or None,
                'files_with_changes': len(files),
                'files': [{'path': f['path'], 'change_freq': f.get('change_freq'), 'churn_lines': f.get('churn'),
                           'bugfix_count': f.get('bugfix_count'), 'bug_ratio': _round(f.get('bug_ratio')),
                           'maintenance_priority': f.get('maintenance_priority'), 'bf80': f.get('bus_factor'),
                           'contributors': f.get('contributor_count'), 'top_owner_share': _round(f.get('top_owner_share')),
                           'last_refactor_at': f.get('last_refactor_at'), 'evidence_status': f.get('evidence_status')}
                          for f in files[:self._limit(limit)]],
                'notes': ['maintenance_priority는 변경 빈도·변경량·버그 수정 커밋 수의 백분위 평균이며 결함 확률이 아닙니다.']}

    def get_file_change_history(self, path='', limit=None) -> dict:
        path, error = self.resolve_path(path)
        if error:
            return error
        fid = self.files[path]
        rows = self._rows("""
            SELECT c.sha, c.committed_at, COALESCE(c.commit_type, 'other') AS commit_type, c.contributor_id,
                   c.author_name, c.message, c.changed_file_count, cfc.additions, cfc.deletions
            FROM sc c JOIN commit_file_changes cfc ON cfc.commit_id = c.id
            WHERE cfc.file_id = :fid ORDER BY c.committed_at DESC, c.sha""", {'fid': fid})
        monthly: dict[str, int] = {}
        for r in rows:
            key = r['committed_at'].strftime('%Y-%m')
            monthly[key] = monthly.get(key, 0) + 1
        metric = next((f for f in self._risk_files() if f['file_id'] == fid), None)
        return {'period': self.period, 'path': path, 'total_changes': len(rows),
                'bugfix_changes': sum(1 for r in rows if r['commit_type'] == 'bugfix'),
                'refactor_changes': sum(1 for r in rows if r['commit_type'] == 'refactor'),
                'contributors': sorted({self.contributor_name(r['contributor_id'], r['author_name']) for r in rows}),
                'monthly_changes': [{'month': m, 'changes': n} for m, n in sorted(monthly.items())],
                'maintenance_priority': metric.get('maintenance_priority') if metric else None,
                'recent_commits': [{'sha': r['sha'], 'committed_at': _iso(r['committed_at']), 'commit_type': r['commit_type'],
                                    'author': self.contributor_name(r['contributor_id'], r['author_name']),
                                    'subject': _subject(r['message']), 'additions': r['additions'],
                                    'deletions': r['deletions'], 'files_in_commit': r['changed_file_count']}
                                   for r in rows[:self._limit(limit)]]}

    def get_co_changed_files(self, path='', limit=None) -> dict:
        path, error = self.resolve_path(path)
        if error:
            return error
        fid = self.files[path]
        coupling = self.payload.get('coupling') or {}
        meta = coupling.get('meta') or {}
        partners = []
        for p in coupling.get('pairs') or []:
            if fid not in (p['file_a_id'], p['file_b_id']):
                continue
            mine_a = p['file_a_id'] == fid
            partners.append({'path': p['file_b_path'] if mine_a else p['file_a_path'],
                             'co_change_count': p['co_change_count'],
                             'ratio_of_this_file_changes': p['a_to_b_ratio'] if mine_a else p['b_to_a_ratio'],
                             'ratio_of_partner_changes': p['b_to_a_ratio'] if mine_a else p['a_to_b_ratio'],
                             'coupling_score': p['coupling_score'], 'low_support': p['low_support']})
        partners.sort(key=lambda x: (-x['coupling_score'], -x['co_change_count'], x['path']))
        directories: dict[str, int] = {}
        for x in partners:
            d = _directory(x['path'], 2)
            directories[d] = directories.get(d, 0) + x['co_change_count']
        own_changes = next((p['change_count_a'] if p['file_a_id'] == fid else p['change_count_b']
                            for p in coupling.get('pairs') or [] if fid in (p['file_a_id'], p['file_b_id'])), None)
        return {'period': self.period, 'path': path, 'co_change_unit': meta.get('co_change_unit'),
                'changes_of_this_file_in_units': own_changes, 'partner_count': len(partners),
                'co_changed_files': partners[:self._limit(limit)],
                'related_directories': [{'directory': d, 'co_changes': n} for d, n in
                                        sorted(directories.items(), key=lambda x: (-x[1], x[0]))[:8]],
                'notes': ['과거에 함께 변경된 기록이며 실제 코드 의존성(import·호출)을 확인한 것이 아닙니다.',
                          '대형 커밋·PR은 공동 변경 계산에서 제외됩니다.']}

    def get_top_coupled_pairs(self, limit=None) -> dict:
        coupling = self.payload.get('coupling') or {}
        pairs = coupling.get('pairs') or []
        return {'period': self.period, 'meta': {k: v for k, v in (coupling.get('meta') or {}).items()
                                                if not isinstance(v, (dict, list))},
                'pairs': [{'file_a': p['file_a_path'], 'file_b': p['file_b_path'], 'co_change_count': p['co_change_count'],
                           'coupling_score': p['coupling_score'], 'low_support': p['low_support']}
                          for p in pairs[:self._limit(limit)]],
                'notes': ['공동 변경은 코드 의존성이 아닙니다.']}

    # ── 커밋 ──────────────────────────────────────────────────────────

    def get_related_commits(self, path='', directory='', contributor='', commit_type='', limit=None) -> dict:
        conditions, params, applied = [], {}, {}
        if path:
            path, error = self.resolve_path(path)
            if error:
                return error
            conditions.append('EXISTS (SELECT 1 FROM commit_file_changes x WHERE x.commit_id = c.id AND x.file_id = :fid)')
            params['fid'] = self.files[path]
            applied['path'] = path
        if directory:
            directory, error = self.resolve_directory(directory)
            if error:
                return error
            conditions.append("""EXISTS (SELECT 1 FROM commit_file_changes x JOIN sf ON sf.id = x.file_id
                                 WHERE x.commit_id = c.id AND sf.path LIKE :dir ESCAPE '\\')""")
            params['dir'] = _like_prefix(directory) + '/%'
            applied['directory'] = directory
        if contributor:
            cid, error = self.resolve_contributor(contributor)
            if error:
                return error
            conditions.append('c.contributor_id = :cid')
            params['cid'] = cid
            applied['contributor'] = self.contributor_name(cid)
        if commit_type:
            if commit_type not in COMMIT_TYPES:
                raise ToolArgumentError('commitType은 ' + ', '.join(COMMIT_TYPES) + ' 중 하나입니다.')
            conditions.append("COALESCE(c.commit_type, 'other') = :ctype")
            params['ctype'] = commit_type
            applied['commit_type'] = commit_type
        if not conditions:
            raise ToolArgumentError('path, directory, contributor, commitType 중 하나 이상이 필요합니다.')
        where = ' AND '.join(conditions)
        total = self._rows(f'SELECT COUNT(*) AS n FROM sc c WHERE {where}', params)[0]['n']
        rows = self._rows(f"""
            SELECT c.sha, c.committed_at, COALESCE(c.commit_type, 'other') AS commit_type, c.contributor_id,
                   c.author_name, c.message, c.changed_file_count
            FROM sc c WHERE {where} ORDER BY c.committed_at DESC, c.sha LIMIT :lim""",
                          {**params, 'lim': self._limit(limit)})
        return {'period': self.period, 'filters': applied, 'total_matching_commits': total,
                'commits': [{'sha': r['sha'], 'committed_at': _iso(r['committed_at']), 'commit_type': r['commit_type'],
                             'author': self.contributor_name(r['contributor_id'], r['author_name']),
                             'subject': _subject(r['message']), 'files_in_commit': r['changed_file_count']}
                            for r in rows]}

    def get_commit_details(self, sha='') -> dict:
        sha = (sha or '').strip().lower()
        if not _SHA.match(sha):
            raise ToolArgumentError('sha는 7~40자리 16진수여야 합니다.')
        rows = self._rows("""
            SELECT c.id, c.sha, c.committed_at, COALESCE(c.commit_type, 'other') AS commit_type, c.contributor_id,
                   c.author_name, c.message, c.changed_file_count
            FROM sc c WHERE c.sha LIKE :prefix ORDER BY c.sha LIMIT 2""", {'prefix': sha + '%'})
        if len(rows) != 1:
            return {'error': 'COMMIT_NOT_FOUND' if not rows else 'COMMIT_AMBIGUOUS',
                    'message': '이 스냅샷의 관측 구간에서 커밋을 하나로 특정하지 못했습니다.', 'query': sha}
        c = rows[0]
        files = self._rows("""
            SELECT f.path, cfc.additions, cfc.deletions FROM commit_file_changes cfc JOIN sf f ON f.id = cfc.file_id
            WHERE cfc.commit_id = :cid ORDER BY COALESCE(cfc.additions, 0) + COALESCE(cfc.deletions, 0) DESC, f.path""",
                           {'cid': c['id']})
        message = (c['message'] or '').strip()
        return {'period': self.period, 'sha': c['sha'], 'committed_at': _iso(c['committed_at']),
                'commit_type': c['commit_type'], 'author': self.contributor_name(c['contributor_id'], c['author_name']),
                'subject': _subject(message), 'message_excerpt': message[:600],
                'files_changed': len(files), 'is_large_commit': len(files) >= self.large_threshold,
                'additions': sum(f['additions'] or 0 for f in files), 'deletions': sum(f['deletions'] or 0 for f in files),
                'files': [{'path': f['path'], 'additions': f['additions'], 'deletions': f['deletions']} for f in files[:30]]}

    # ── 3.5 기간 비교·이상 징후 ───────────────────────────────────────

    def _periods(self, recent_months: int, baseline_months: int):
        recent_start = max(self.window_end - relativedelta(months=recent_months), self.window_start)
        baseline_start = max(recent_start - relativedelta(months=baseline_months), self.window_start)
        return recent_start, baseline_start

    def compare_development_periods(self, recent_months=None, baseline_months=None, limit=None) -> dict:
        recent_months = 1 if recent_months in (None, 0) else recent_months
        baseline_months = 3 if baseline_months in (None, 0) else baseline_months
        for value in (recent_months, baseline_months):
            if type(value) is not int or not 1 <= value <= 24:
                raise ToolArgumentError('recentMonths·baselineMonths는 1~24입니다.')
        limit = self._limit(limit)
        recent_start, baseline_start = self._periods(recent_months, baseline_months)
        recent_days = max((self.window_end - recent_start).total_seconds() / 86400, 1)
        baseline_days = (recent_start - baseline_start).total_seconds() / 86400
        if baseline_days < 1:
            return {'error': 'NOT_ENOUGH_HISTORY', 'message': '관측 구간이 짧아 기준 기간을 만들 수 없습니다.',
                    'period': self.period}
        params = {'rs': recent_start, 'bs': baseline_start}
        summary = self._rows("""
            SELECT CASE WHEN committed_at >= :rs THEN 'recent' ELSE 'baseline' END AS part,
                   COUNT(*) AS commits,
                   COUNT(*) FILTER (WHERE commit_type = 'bugfix') AS bugfix,
                   COUNT(*) FILTER (WHERE commit_type = 'feature') AS feature,
                   COUNT(*) FILTER (WHERE commit_type = 'refactor') AS refactor,
                   COUNT(*) FILTER (WHERE changed_file_count >= :large) AS large_commits,
                   COUNT(DISTINCT contributor_id) AS active_contributors
            FROM sc WHERE committed_at >= :bs GROUP BY 1""", {**params, 'large': self.large_threshold})
        parts = {r['part']: r for r in summary}

        def period_view(name, start, end, days):
            r = parts.get(name) or {}
            commits = r.get('commits') or 0

            def per30(v):
                return _round((v or 0) * 30 / days, 2)
            return {'start': _iso(start), 'end': _iso(end), 'days': _round(days, 1), 'commits': commits,
                    'commits_per_30d': per30(commits), 'bugfix_commits': r.get('bugfix') or 0,
                    'bugfix_per_30d': per30(r.get('bugfix')),
                    'bugfix_pct': _round(100 * (r.get('bugfix') or 0) / commits, 1) if commits else None,
                    'feature_commits': r.get('feature') or 0, 'refactor_commits': r.get('refactor') or 0,
                    'large_commits': r.get('large_commits') or 0,
                    'active_contributors': r.get('active_contributors') or 0}

        recent = period_view('recent', recent_start, self.window_end, recent_days)
        baseline = period_view('baseline', baseline_start, recent_start, baseline_days)

        def surges(rows, key):
            out = []
            for r in rows:
                recent_rate = r['recent'] * 30 / recent_days
                baseline_rate = r['baseline'] * 30 / baseline_days
                if r['recent'] < 3:
                    continue                               # 표본이 너무 적은 변화는 이상 징후로 보지 않는다
                ratio = recent_rate / baseline_rate if baseline_rate else None
                if ratio is None or ratio >= 2:
                    out.append({key: r[key], 'recent_count': r['recent'], 'baseline_count': r['baseline'],
                                'recent_per_30d': _round(recent_rate, 2), 'baseline_per_30d': _round(baseline_rate, 2),
                                'ratio': _round(ratio, 2)})
            out.sort(key=lambda x: (-(x['ratio'] if x['ratio'] is not None else 1e9), -x['recent_count'], x[key]))
            return out[:limit]

        file_rows = self._rows("""
            SELECT f.path, COUNT(DISTINCT c.id) FILTER (WHERE c.committed_at >= :rs) AS recent,
                   COUNT(DISTINCT c.id) FILTER (WHERE c.committed_at < :rs) AS baseline,
                   COUNT(DISTINCT c.id) FILTER (WHERE c.committed_at >= :rs AND c.commit_type = 'bugfix') AS recent_bugfix,
                   COUNT(*) FILTER (WHERE c.committed_at >= :rs
                                    AND COALESCE(cfc.additions, 0) + COALESCE(cfc.deletions, 0) >= :big) AS recent_big_changes
            FROM sc c JOIN commit_file_changes cfc ON cfc.commit_id = c.id JOIN sf f ON f.id = cfc.file_id
            WHERE c.committed_at >= :bs GROUP BY f.path""", {**params, 'big': 300})
        directories: dict[str, dict] = {}
        for r in file_rows:
            d = directories.setdefault(_directory(r['path'], 2), {'directory': _directory(r['path'], 2), 'recent': 0, 'baseline': 0})
            d['recent'] += r['recent']
            d['baseline'] += r['baseline']
        contributor_rows = self._rows("""
            SELECT contributor_id, COUNT(*) FILTER (WHERE committed_at >= :rs) AS recent,
                   COUNT(*) FILTER (WHERE committed_at < :rs) AS baseline
            FROM sc WHERE committed_at >= :bs AND contributor_id IS NOT NULL GROUP BY contributor_id""", params)
        contributor_changes = []
        for r in contributor_rows:
            recent_rate, baseline_rate = r['recent'] * 30 / recent_days, r['baseline'] * 30 / baseline_days
            if max(r['recent'], r['baseline']) < 3:
                continue
            if baseline_rate and 0.5 < recent_rate / baseline_rate < 2:
                continue
            contributor_changes.append({'contributor': self.contributor_name(r['contributor_id']),
                                        'recent_count': r['recent'], 'baseline_count': r['baseline'],
                                        'recent_per_30d': _round(recent_rate, 2), 'baseline_per_30d': _round(baseline_rate, 2)})
        contributor_changes.sort(key=lambda x: (-abs(x['recent_per_30d'] - x['baseline_per_30d']), x['contributor']))
        repeated_big = sorted(({'path': r['path'], 'recent_big_changes': r['recent_big_changes'], 'recent_changes': r['recent']}
                               for r in file_rows if r['recent_big_changes'] >= 2),
                              key=lambda x: (-x['recent_big_changes'], x['path']))[:limit]
        bugfix_files = sorted(({'path': r['path'], 'recent_bugfix_changes': r['recent_bugfix']}
                               for r in file_rows if r['recent_bugfix'] >= 2),
                              key=lambda x: (-x['recent_bugfix_changes'], x['path']))[:limit]

        def change(a, b):
            return _round((a - b) / b, 4) if b else None

        return {'period': self.period, 'recent': recent, 'baseline': baseline,
                'baseline_truncated_by_window': recent_start - relativedelta(months=baseline_months) < self.window_start,
                'rate_change': {'commits_per_30d': change(recent['commits_per_30d'], baseline['commits_per_30d']),
                                'bugfix_per_30d': change(recent['bugfix_per_30d'], baseline['bugfix_per_30d'])},
                'file_surges': surges(file_rows, 'path'),
                'directory_surges': surges(list(directories.values()), 'directory'),
                'contributor_activity_changes': contributor_changes[:limit],
                'files_with_repeated_large_changes': repeated_big,
                'files_with_repeated_bugfixes_recent': bugfix_files,
                'thresholds': {'surge_ratio': 2, 'minimum_recent_count': 3, 'large_file_change_lines': 300,
                               'large_commit_files': self.large_threshold, 'rates': 'per 30 days'},
                'notes': ['비율은 30일당 건수로 맞춰 비교합니다. 기준 기간에 기록이 없으면 ratio는 null입니다.']}

    # ── 3.6 진화 ──────────────────────────────────────────────────────

    def get_project_evolution(self, limit=None) -> dict:
        limit = self._limit(limit)
        monthly = self._rows("""
            SELECT to_char(date_trunc('month', committed_at), 'YYYY-MM') AS month, COUNT(*) AS commits,
                   COUNT(*) FILTER (WHERE commit_type = 'feature') AS feature,
                   COUNT(*) FILTER (WHERE commit_type = 'bugfix') AS bugfix,
                   COUNT(*) FILTER (WHERE commit_type = 'refactor') AS refactor,
                   COUNT(*) FILTER (WHERE commit_type IN ('docs', 'chore', 'other') OR commit_type IS NULL) AS other
            FROM sc GROUP BY 1 ORDER BY 1""")
        touches = self._rows("""
            SELECT f.path, to_char(date_trunc('month', c.committed_at), 'YYYY-MM') AS month,
                   COUNT(DISTINCT c.id) AS commits, MIN(c.committed_at) AS first_at
            FROM sc c JOIN commit_file_changes cfc ON cfc.commit_id = c.id JOIN sf f ON f.id = cfc.file_id
            GROUP BY f.path, 2""")
        focus: dict[str, dict[str, int]] = {}
        first_seen: dict[str, datetime] = {}
        first_file: dict[str, datetime] = {}
        for r in touches:
            d = _directory(r['path'], 2)
            focus.setdefault(r['month'], {})
            focus[r['month']][d] = focus[r['month']].get(d, 0) + r['commits']
            first_seen[d] = min(first_seen.get(d, r['first_at']), r['first_at'])
            first_file[r['path']] = min(first_file.get(r['path'], r['first_at']), r['first_at'])
        settle = self.window_start + timedelta(days=30)
        new_dirs = sorted(((d, t) for d, t in first_seen.items() if t >= settle), key=lambda x: (x[1], x[0]))
        new_files_by_month: dict[str, int] = {}
        for t in first_file.values():
            if t >= settle:
                new_files_by_month[t.strftime('%Y-%m')] = new_files_by_month.get(t.strftime('%Y-%m'), 0) + 1
        refactor_peaks = sorted((m for m in monthly if m['refactor']), key=lambda m: (-m['refactor'], m['month']))[:3]
        return {'period': self.period,
                'monthly': [{**m, 'feature_share': _round(m['feature'] / m['commits']) if m['commits'] else None,
                             'maintenance_share': _round((m['bugfix'] + m['refactor']) / m['commits']) if m['commits'] else None,
                             'focus_directory': max(focus.get(m['month'], {'(none)': 0}).items(), key=lambda x: (x[1], x[0]))[0]}
                            for m in monthly],
                'directories_first_changed_in_window': [{'directory': d, 'first_change_at': _iso(t)} for d, t in new_dirs[:limit]],
                'files_first_changed_per_month': [{'month': k, 'files': v} for k, v in sorted(new_files_by_month.items())],
                'refactor_peak_months': [{'month': m['month'], 'refactor_commits': m['refactor']} for m in refactor_peaks],
                'notes': ['"처음 변경"은 관측 구간 안의 첫 변경 기록이며 실제 생성 시점이 아닐 수 있습니다(구간 첫 30일은 제외).']}


    # ── 소스 코드(읽기 전용, 기준 커밋) ─────────────────────────────────

    def read_file(self, path='', start_line=None, end_line=None) -> dict:
        return dict(_CODE_OFF) if self.code is None else self.code.read_file(path, start_line, end_line)

    def search_code(self, query='', directory='') -> dict:
        return dict(_CODE_OFF) if self.code is None else self.code.search(query, directory)

    def list_directory(self, directory='') -> dict:
        return dict(_CODE_OFF) if self.code is None else self.code.list_directory(directory)


_CODE_OFF = {'error': 'CODE_ACCESS_DISABLED',
             'message': '이 실행에서는 소스 코드 열람이 꺼져 있거나 서버에 저장소 사본이 없습니다.'}


# ── 도구 목록(AI에 보여 주는 이름·설명·인자) ─────────────────────────────

@dataclass(frozen=True)
class ToolSpec:
    name: str
    method: str
    description: str
    args: dict = field(default_factory=dict)      # AI 인자 이름 → 메서드 인자 이름
    code: bool = False                            # 소스 코드를 AI에 보내는 도구


TOOLS: dict[str, ToolSpec] = {t.name: t for t in (
    ToolSpec('getProjectOverview', 'get_project_overview', '저장소 요약: 관측 구간, 커밋·기여자·릴리스 수, 버그 수정 비율, 데이터 품질.'),
    ToolSpec('getCommitStatistics', 'get_commit_statistics', '커밋 규모 통계: 커밋당 파일 수, 대형 커밋 수, 유형별 변경 줄 수.'),
    ToolSpec('getCommitTimeline', 'get_commit_timeline', '월별 커밋 수·활성 기여자 수·버그 수정 비율.'),
    ToolSpec('getCommitTypeStatistics', 'get_commit_type_statistics', '커밋 유형별 개수·비율과 월별 유형 분포.'),
    ToolSpec('getContributorStatistics', 'get_contributor_statistics', '기여자별 커밋 수·비중·최근 커밋·단독 소유(BF80=1) 파일 수.', {'limit': 'limit'}),
    ToolSpec('getContributorCollaboration', 'get_contributor_collaboration', '한 기여자의 협업 상대(공동 수정·리뷰).', {'contributor': 'contributor', 'limit': 'limit'}),
    ToolSpec('getTopCollaborationPairs', 'get_top_collaboration_pairs', '협업 강도가 큰 기여자 쌍.', {'limit': 'limit'}),
    ToolSpec('getDirectoryStatistics', 'get_directory_statistics', '디렉터리별 커밋 수·비중·버그 수정·리팩터링·변경 줄 수·기여자 수. depth=경로 단계.', {'depth': 'depth', 'limit': 'limit'}),
    ToolSpec('getFileChangeFrequency', 'get_file_change_frequency', '파일별 변경 횟수·변경량·버그 수정 수·유지보수 우선순위·BF80. sortBy=maintenance_priority|change_freq|churn|bugfix_count|bus_factor, directory로 범위 제한.', {'sortBy': 'sort_by', 'directory': 'directory', 'limit': 'limit'}),
    ToolSpec('getFileChangeHistory', 'get_file_change_history', '한 파일의 변경 이력: 월별 변경, 기여자, 최근 커밋.', {'path': 'path', 'limit': 'limit'}),
    ToolSpec('getCoChangedFiles', 'get_co_changed_files', '한 파일과 함께 변경된 파일·빈도·비율·연관 디렉터리(코드 의존성 아님).', {'path': 'path', 'limit': 'limit'}),
    ToolSpec('getTopCoupledPairs', 'get_top_coupled_pairs', '함께 변경되는 정도가 큰 파일 쌍.', {'limit': 'limit'}),
    ToolSpec('getRelatedCommits', 'get_related_commits', '조건(path, directory, contributor, commitType)에 맞는 커밋 목록.', {'path': 'path', 'directory': 'directory', 'contributor': 'contributor', 'commitType': 'commit_type', 'limit': 'limit'}),
    ToolSpec('getCommitDetails', 'get_commit_details', '커밋 하나의 메시지·변경 파일·변경 줄 수.', {'sha': 'sha'}),
    ToolSpec('getDeveloperActivity', 'get_developer_activity', '한 기여자의 월별 활동, 커밋 유형, 주로 바꾼 디렉터리, 비중이 큰 파일, 협업 상대.', {'contributor': 'contributor', 'limit': 'limit'}),
    ToolSpec('getOwnershipConcentration', 'get_ownership_concentration', '변경 기여 집중(BF80) 분포, 단독 소유 파일·모듈.', {'limit': 'limit'}),
    ToolSpec('compareDevelopmentPeriods', 'compare_development_periods', '최근 기간과 직전 기준 기간 비교(30일당 비율): 활동량·버그 수정·급증 파일/디렉터리·기여자 활동 변화·반복 대규모 변경.', {'recentMonths': 'recent_months', 'baselineMonths': 'baseline_months', 'limit': 'limit'}),
    ToolSpec('getProjectEvolution', 'get_project_evolution', '월별 유형 구성(기능/유지보수 비중), 활동 중심 디렉터리, 처음 변경된 디렉터리·파일, 리팩터링 집중 시기.', {'limit': 'limit'}),
    ToolSpec('listDirectory', 'list_directory', '[코드] 기준 커밋의 디렉터리 내용(하위 디렉터리·파일, 파일 수·크기). directory가 ""이면 루트.', {'directory': 'directory'}, code=True),
    ToolSpec('readFile', 'read_file', '[코드] 기준 커밋의 파일 내용(줄 번호 포함, 한 번에 최대 200줄). startLine/endLine으로 범위를 정한다.', {'path': 'path', 'startLine': 'start_line', 'endLine': 'end_line'}, code=True),
    ToolSpec('searchCode', 'search_code', '[코드] 기준 커밋에서 고정 문자열 검색(파일당 최대 5줄, 모두 30줄까지). directory에 디렉터리나 파일을 주면 그 안에서만 찾는다.', {'query': 'query', 'directory': 'directory'}, code=True),
)}

ARG_NAMES = ('path', 'directory', 'contributor', 'sha', 'commitType', 'sortBy', 'query', 'limit', 'recentMonths',
             'baselineMonths', 'depth', 'startLine', 'endLine')
INT_ARGS = {'limit', 'recentMonths', 'baselineMonths', 'depth', 'startLine', 'endLine'}


def tool_catalog(include_code: bool = False) -> str:
    lines = []
    for spec in TOOLS.values():
        if spec.code and not include_code:
            continue
        args = ', '.join(spec.args) or '(인자 없음)'
        lines.append(f'- {spec.name}({args}): {spec.description}')
    return '\n'.join(lines)


def run_tool(data: InsightData, name: str, args: dict | None) -> dict:
    """AI가 고른 도구를 실행한다. 허용 목록 밖의 이름·인자는 실행하지 않고 오류 결과를 돌려준다."""
    spec = TOOLS.get(name)
    if spec is None:
        return {'error': 'UNKNOWN_TOOL', 'message': '허용되지 않은 도구입니다.', 'tool': str(name)[:60]}
    if spec.code and data.code is None:
        return dict(_CODE_OFF)
    kwargs = {}
    for key, value in (args or {}).items():
        if key not in ARG_NAMES:
            return {'error': 'INVALID_ARGUMENT', 'message': f'알 수 없는 인자: {str(key)[:40]}'}
        empty = value in ('', None, 0)
        if empty:
            continue
        if key not in spec.args:
            return {'error': 'INVALID_ARGUMENT', 'message': f'{spec.name}은 {key} 인자를 받지 않습니다.'}
        if key in INT_ARGS and (type(value) is not int or value < 0):
            return {'error': 'INVALID_ARGUMENT', 'message': f'{key}는 0 이상의 정수여야 합니다.'}
        if key not in INT_ARGS and (not isinstance(value, str) or len(value) > 300):
            return {'error': 'INVALID_ARGUMENT', 'message': f'{key}는 300자 이하 문자열이어야 합니다.'}
        kwargs[spec.args[key]] = value
    try:
        return getattr(data, spec.method)(**kwargs)
    except ToolArgumentError as exc:
        return {'error': 'INVALID_ARGUMENT', 'message': str(exc)}
    except (OSError, RuntimeError, subprocess.SubprocessError):     # 코드 도구의 git 조회 실패(원문은 남기지 않는다)
        return {'error': 'CODE_UNAVAILABLE', 'message': '기준 커밋의 코드를 읽지 못했습니다.'}


# ── 근거 원장(Ledger): 도구 결과에 실제로 나온 값 ─────────────────────────

_PATH_KEYS = {'path', 'file_a', 'file_b', 'file_a_path', 'file_b_path'}
_DIR_KEYS = {'directory', 'module', 'focus_directory'}
_NAME_KEYS = {'name', 'author', 'contributor', 'collaborator', 'contributor_a', 'contributor_b', 'top_owner'}


@dataclass
class ToolCall:
    call_id: str
    tool: str
    args: dict
    result: dict

    @property
    def ok(self) -> bool:
        return 'error' not in self.result


@dataclass
class Ledger:
    calls: dict = field(default_factory=dict)          # call_id → ToolCall
    paths: set = field(default_factory=set)
    directories: set = field(default_factory=set)
    shas: set = field(default_factory=set)
    contributors: set = field(default_factory=set)
    numbers: dict = field(default_factory=dict)         # call_id → [float]
    texts: dict = field(default_factory=dict)           # call_id → 직렬화 문자열(소문자)
    code_ranges: dict = field(default_factory=dict)     # path → [(start, end)] 실제로 AI에 보낸 줄 범위
    code_text: str = ''                                 # 보낸 코드 본문(소문자). 코드에 나온 이름의 언급을 허용한다

    def add(self, call: ToolCall) -> None:
        self.calls[call.call_id] = call
        numbers: list[float] = []

        def visit(node, key=None):
            if isinstance(node, dict):
                for k, v in node.items():
                    visit(v, k)
            elif isinstance(node, list):
                for v in node:
                    visit(v, key)
            elif isinstance(node, bool) or node is None:
                return
            elif isinstance(node, (int, float)):
                if math.isfinite(node):
                    numbers.append(float(node))
            elif isinstance(node, str):
                if key in _PATH_KEYS:
                    self.paths.add(node)
                elif key in _DIR_KEYS:
                    self.directories.add(node)
                elif key == 'sha' and _SHA.match(node):
                    self.shas.add(node)
                elif key in _NAME_KEYS:
                    self.contributors.add(node)
                elif key == 'contributors':
                    self.contributors.add(node)
        visit(call.result)
        result = call.result
        if call.ok and call.tool == 'readFile':
            self.code_ranges.setdefault(result['path'], []).append((result['start_line'], result['end_line']))
            self.code_text += '\n' + result['content'].lower()
        elif call.ok and call.tool == 'searchCode':
            for hit in result['matches']:
                self.code_ranges.setdefault(hit['path'], []).append((hit['line'], hit['line']))
                self.code_text += '\n' + hit['text'].lower()
        self.numbers[call.call_id] = numbers
        self.texts[call.call_id] = json.dumps(call.result, ensure_ascii=False, default=str).lower()

    def match_sha(self, value: str):
        value = (value or '').strip().lower()
        if not _SHA.match(value):
            return None
        found = [s for s in self.shas if s.startswith(value)]
        return found[0] if len(found) == 1 else None

    def known_path(self, value: str) -> bool:
        value = (value or '').strip().strip('/')
        return value in self.paths or value in self.directories or any(p.startswith(value + '/') for p in self.paths)

    def code_range(self, path: str, start, end) -> bool:
        """인용한 줄 범위가 이번 실행에서 실제로 읽은 범위 안인지."""
        if type(start) is not int or type(end) is not int or start < 1 or end < start:
            return False
        return any(s <= start and end <= e for s, e in self.code_ranges.get(path, []))

    def in_code(self, token: str) -> bool:
        return bool(self.code_text) and token.lower() in self.code_text

    def known_contributor(self, value: str):
        lowered = (value or '').strip().lower().lstrip('@')
        return next((c for c in self.contributors if c.lower() == lowered), None)

    def supports(self, call_id: str, value: str) -> bool:
        """근거 항목의 값이 해당 도구 결과에 실제로 있는지. 숫자는 표기 자릿수 안에서 같아야 한다(%는 비율도 허용)."""
        if call_id not in self.calls or not self.calls[call_id].ok:
            return False
        value = (value or '').strip()
        if not value:
            return False
        found = re.search(r'-?\d+(?:,\d{3})*(?:\.\d+)?', value)
        if found is None:
            return value.lower() in self.texts[call_id]
        raw = found.group(0).replace(',', '')
        number = float(raw)
        decimals = len(raw.split('.', 1)[1]) if '.' in raw else 0
        tolerance = 0.5 * 10 ** -decimals + 1e-9
        candidates = [number]
        if '%' in value:
            candidates.append(number / 100)
        for candidate in candidates:
            scale = tolerance if candidate == number else tolerance / 100
            if any(abs(n - candidate) <= scale for n in self.numbers[call_id]):
                return True
        return False
