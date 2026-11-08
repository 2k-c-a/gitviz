"""Change analysis of a local project's Git history: frequently changed files, co-changed pairs and a weekly timeline.

Same rules as the server snapshot (app/services/metric_policy.py, app/api/analytics.py): a fixed window of
DEFAULT_WINDOW_DAYS ending now, files in the HEAD tree as the population, equal-weight percentile ranks of change
count / churn / bugfix count, large commits (max(15, ceil(0.1 * tree files))) excluded from co-change, pairs seen
in at least two commits. Local differences: only commits reachable from HEAD, author names instead of resolved
GitHub identities, and commits as the co-change unit (no pull requests). Nothing is stored; nothing leaves the PC.
git's --since walk assumes committer dates run forward along history; a commit dated far out of order can end the walk early.
"""
from __future__ import annotations

import importlib
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Any

from .git_service import LocalGit
from .scoped_files import ScopeError, pinned_paths

policy = importlib.import_module('app.services.metric_policy')
classifier = importlib.import_module('app.collectors.git.commit_classifier')

MAX_COMMITS = 20000
TOP_FILES = 30
TOP_PAIRS = 30


def parse_log(raw: bytes) -> list[dict[str, Any]]:
    """Records of `git log -z --numstat --format=%x1e%H%x1f%an%x1f%cI%x1f%s`."""
    try:
        text = raw.decode('utf-8', 'strict')
    except UnicodeError:
        raise ScopeError('UNSUPPORTED_FILENAME') from None
    commits: list[dict[str, Any]] = []
    for chunk in text.split('\x1e')[1:]:
        header, _, body = chunk.partition('\0')
        fields = header.split('\x1f')
        if len(fields) != 4:
            raise ScopeError('GIT_UNAVAILABLE')
        files: list[tuple[str, int | None]] = []
        for record in body.lstrip('\n').split('\0'):
            if not record:
                continue
            parts = record.split('\t', 2)
            if len(parts) != 3:
                continue
            added, deleted, path = parts
            files.append((path, int(added) + int(deleted) if added.isdigit() and deleted.isdigit() else None))
        commits.append({'oid': fields[0], 'author': fields[1], 'at': datetime.fromisoformat(fields[2]).astimezone(timezone.utc),
                        'type': classifier.classify_commit(fields[3])[0], 'files': files})
    return commits


def summarize(commits: list[dict[str, Any]], tree: set[str], start: datetime, end: datetime) -> dict[str, Any]:
    window = [commit for commit in commits if start <= commit['at'] < end]
    threshold = policy.large_change_threshold(len(tree))

    # Frequently changed files (server: _compute_risk_scores).
    stats: dict[str, dict[str, Any]] = {}
    for commit in window:
        for path, churn in commit['files']:
            if path not in tree:
                continue
            item = stats.setdefault(path, {'commits': set(), 'churn': 0, 'binary': False, 'bugfix': set(), 'authors': set(), 'last': commit['at']})
            item['commits'].add(commit['oid'])
            if churn is None:
                item['binary'] = True
            else:
                item['churn'] += churn
            if commit['type'] == 'bugfix':
                item['bugfix'].add(commit['oid'])
            item['authors'].add(commit['author'])
            item['last'] = max(item['last'], commit['at'])
    components: dict[str, dict[str, float | None]] = {
        'changeCount': policy.percentile_rank({path: len(item['commits']) for path, item in stats.items()}),
        'churn': policy.percentile_rank({path: None if item['binary'] else item['churn'] for path, item in stats.items()}),
        'bugfixCount': policy.percentile_rank({path: len(item['bugfix']) for path, item in stats.items()})}
    files: list[dict[str, Any]] = []
    for path, item in stats.items():
        ranked = [value for key in components if (value := components[key][path]) is not None]
        priority = round(sum(ranked) / 3, 4) if len(ranked) == 3 else None
        files.append({'path': path, 'changeCount': len(item['commits']), 'churn': None if item['binary'] else item['churn'],
                      'bugfixCount': len(item['bugfix']), 'authorCount': len(item['authors']),
                      'lastChangedAt': item['last'].isoformat(), 'priority': priority,
                      'lowSupport': len(item['commits']) < policy.LOW_SUPPORT_THRESHOLD})
    files.sort(key=lambda item: (-item['changeCount'], -(item['churn'] or 0), item['path']))

    # Co-changed pairs (server: _compute_coupling with commits as the unit).
    units = [sorted({path for path, _ in commit['files'] if path in tree}) for commit in window if len(commit['files']) <= threshold]
    units = [unit for unit in units if unit]
    totals: Counter[str] = Counter(path for unit in units for path in unit)
    together: Counter[tuple[str, str]] = Counter(pair for unit in units for pair in combinations(unit, 2))

    def idf(count: int) -> float:
        return math.log(len(units) / count) if count and count < len(units) else 0.0

    pairs: list[dict[str, Any]] = []
    for (a, b), count in together.items():
        if count < 2:
            continue
        score = count / min(totals[a], totals[b])
        pairs.append({'fileA': a, 'fileB': b, 'coChangeCount': count, 'changeCountA': totals[a], 'changeCountB': totals[b],
                      'score': round(score, 4), 'adjustedScore': round(score * idf(totals[a]) * idf(totals[b]), 4),
                      'lowSupport': count < policy.LOW_SUPPORT_THRESHOLD})
    pairs.sort(key=lambda item: (-item['score'], -item['coChangeCount'], item['fileA'], item['fileB']))

    # Weekly timeline (Monday-based UTC weeks covering the whole window, empty weeks included).
    weeks: dict[str, dict[str, Any]] = {}
    cursor = (start - timedelta(days=start.weekday())).date()
    while cursor < end.date() + timedelta(days=1):
        weeks[cursor.isoformat()] = {'weekStart': cursor.isoformat(), 'commits': 0, 'authors': set(), 'bugfix': 0}
        cursor += timedelta(days=7)
    for commit in window:
        key = (commit['at'] - timedelta(days=commit['at'].weekday())).date().isoformat()
        week = weeks.get(key)
        if week is None:
            continue
        week['commits'] += 1
        week['authors'].add(commit['author'])
        week['bugfix'] += 1 if commit['type'] == 'bugfix' else 0
    timeline = [{**week, 'authors': len(week['authors'])} for week in weeks.values()]

    authors: defaultdict[str, int] = defaultdict(int)
    for commit in window:
        authors[commit['author']] += 1
    return {'windowStart': start.isoformat(), 'windowEnd': end.isoformat(), 'formulaVersion': policy.FORMULA_VERSION,
            'commitCount': len(window), 'authorCount': len(authors), 'treeFileCount': len(tree),
            'largeChangeThreshold': threshold, 'excludedLargeCommits': sum(1 for commit in window if len(commit['files']) > threshold),
            'coChangeUnits': len(units), 'fileCount': len(files), 'pairCount': len(pairs),
            'files': files[:TOP_FILES], 'pairs': pairs[:TOP_PAIRS], 'timeline': timeline}


def analyze(git: LocalGit, grant: dict[str, str], now: datetime | None = None) -> dict[str, Any]:
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=policy.DEFAULT_WINDOW_DAYS)
    with pinned_paths(grant, []):
        try:
            git.head(grant)
        except ScopeError:
            # No commit yet: an empty analysis, not an error.
            return {**summarize([], set(), start, end), 'truncated': False}
        raw = git.history(grant, start.isoformat(), MAX_COMMITS)
        tree = git.tree_paths(grant)
    commits = parse_log(raw)
    return {**summarize(commits, tree, start, end), 'truncated': len(commits) >= MAX_COMMITS}
