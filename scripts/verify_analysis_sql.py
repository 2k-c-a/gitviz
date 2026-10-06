"""Compare snapshot SQL plans on real data without saving results or changing DB settings.

Example: python scripts/verify_analysis_sql.py --repository-id 14 --compare-ref HEAD
Each query has a transaction-local timeout. Both builds see the same DB snapshot,
Git target, time window, and formulas; only SnapshotInput's SQL scoping differs.
"""
import argparse
import ast
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from app.core.database import SessionLocal
from app.models.repository import Repository
from app.collectors.git.clone_manager import repository_path, snapshot_target
from app.services import analysis_snapshots as snapshots


class TimedDatabase:
    def __init__(self, db):
        self.db = db
        self.seconds = []

    def query(self, *args):
        return self.db.query(*args)

    def execute(self, sql, params=None):
        start = time.perf_counter()
        rows = [dict(r) for r in self.db.execute(sql, params).mappings().all()]
        self.seconds.append(time.perf_counter() - start)
        return snapshots.FrozenRows(rows)


def previous_scope(ref):
    source = subprocess.run(
        ['git', 'show', f'{ref}:app/services/analysis_snapshots.py'],
        cwd=ROOT, check=True, capture_output=True, encoding='utf-8',
    ).stdout
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.ClassDef) and n.name == 'SnapshotInput')
    namespace = dict(vars(snapshots))
    exec(compile(ast.Module(body=[node], type_ignores=[]), ref, 'exec'), namespace)
    return namespace['SnapshotInput']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository-id', type=int, required=True)
    parser.add_argument('--compare-ref', default='HEAD')
    parser.add_argument('--window-start')
    parser.add_argument('--window-end')
    parser.add_argument('--statement-timeout-seconds', type=int, default=15)
    args = parser.parse_args()
    if not 1 <= args.statement_timeout_seconds <= 60:
        parser.error('statement timeout must be between 1 and 60 seconds')
    parameters = snapshots.analysis_parameters(args.window_start, args.window_end)
    target, paths, shas = snapshot_target(
        repository_path(args.repository_id), snapshots.utc_datetime(parameters['window_end']),
    )
    current_scope = snapshots.SnapshotInput
    scopes = [('baseline', previous_scope(args.compare_ref)),
              ('optimized', current_scope), ('repeat', current_scope)]
    payloads = []
    with SessionLocal() as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        db.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                   {'timeout': f'{args.statement_timeout_seconds}s'})
        repo = db.get(Repository, args.repository_id)
        if repo is None:
            raise ValueError('repository not found')
        for label, scope in scopes:
            timed = TimedDatabase(db)
            start = time.perf_counter()
            with patch.object(snapshots, 'SnapshotInput', scope):
                result = snapshots.build_snapshot(
                    timed, repo, None, target_sha=target, live_paths=paths,
                    reachable_shas=shas, parameters=parameters, persist=False,
                )
            payloads.append(result.payload)
            print(json.dumps({'run': label, 'seconds': round(time.perf_counter()-start, 3),
                              'sql_seconds': round(sum(timed.seconds), 3),
                              'slowest_query_seconds': round(max(timed.seconds), 3),
                              'query_count': len(timed.seconds),
                              'payload_sha256': result.payload_sha256}), flush=True)
        if payloads[0] != payloads[1] or payloads[1] != payloads[2]:
            changed = [k for k in payloads[0]
                       if payloads[0][k] != payloads[1][k] or payloads[1][k] != payloads[2][k]]
            raise AssertionError(f'payload differs: {changed}')
        print('PASS: all payload values and hashes match; no results saved.')


if __name__ == '__main__':
    main()
