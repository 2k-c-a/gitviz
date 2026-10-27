"""운영 DB 조회 + 기존 분석 API 통합 검증. 운영 데이터에 쓰지 않는다.

읽기 전용 검증 서버 실행 후: python scripts/verify_production_integration.py
"""
import itertools
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 트랜잭션마다 SET TRANSACTION READ ONLY를 적용한다. create_all은 실행하지 않는다.
import scripts.serve_readonly_validation  # noqa: F401
import httpx
from sqlalchemy import text
from app.core.database import SessionLocal
from app.api.analytics import _fetch_network_edges

results = []


def check(name, condition, details=None):
    results.append({"check": name, "passed": bool(condition), "details": details})
    print(('PASS ' if condition else 'FAIL ') + name, details or '', flush=True)
    if not condition:
        raise AssertionError(name)


def main():
    with SessionLocal() as db:
        check('PostgreSQL transaction_read_only', db.execute(text('SHOW transaction_read_only')).scalar() == 'on')
        repo_ids = set(db.execute(text('SELECT id FROM repositories')).scalars())
        for rid in [9, 1]:
            if rid not in repo_ids:
                continue
            # API와 별개로 실제 변경 레코드를 Python에서 PR/파일별 기여자 집합으로 집계한다.
            rows = db.execute(text('''
                SELECT pc.pull_request_id, cfc.file_id, c.contributor_id
                FROM pr_commits pc
                JOIN pull_requests pr ON pr.id = pc.pull_request_id
                JOIN commits c ON c.id = pc.commit_id
                JOIN commit_file_changes cfc ON cfc.commit_id = c.id
                WHERE pr.repository_id = :rid AND c.repository_id = :rid
                  AND COALESCE(pr.is_large_pr, false) = false
                  AND c.contributor_id IS NOT NULL
                  AND EXISTS (SELECT 1 FROM pull_request_files prf
                              WHERE prf.pull_request_id = pr.id AND prf.file_id = cfc.file_id)
            '''), {'rid': rid}).mappings().all()
            authors = defaultdict(set)
            for row in rows:
                authors[(row['pull_request_id'], row['file_id'])].add(row['contributor_id'])
            expected = Counter()
            for values in authors.values():
                expected.update(itertools.combinations(sorted(values), 2))
            actual = _fetch_network_edges(db, rid)
            directed = {(e['source'], e['target']): e for e in actual['edges']}
            for (a, b), count in expected.items():
                for key in [(a, b), (b, a)]:
                    assert key in directed
                    assert math.isclose(directed[key]['co_edit_raw'], count / actual['total_prs'])
            for (a, b), edge in directed.items():
                assert math.isclose(edge['co_edit_raw'], expected[tuple(sorted((a, b)))] / actual['total_prs'])
            check(f'repo {rid} 공동 수정 SQL = 실제 변경 독립 집계', True,
                  {'pr_file_records': len(rows), 'co_edit_pairs': len(expected), 'directed_edges': len(directed)})

    with httpx.Client(base_url='http://127.0.0.1:8000', timeout=120, trust_env=False) as client:
        def get(rid, route):
            start = time.perf_counter()
            response = client.get(f'/repositories/{rid}/analytics/{route}')
            check(f'repo {rid} GET {route}', response.status_code == 200,
                  {'status': response.status_code, 'seconds': round(time.perf_counter() - start, 2)})
            return response.json()

        for rid in [6, 9, 1, 11]:
            if rid not in repo_ids:
                continue
            summary = get(rid, 'summary')
            tree = get(rid, 'tree')
            files = tree['files']
            check(f'repo {rid} 파일 수 일치', tree['file_count'] == len(files), len(files))
            assert all(f['risk_score'] is None or 0 <= f['risk_score'] <= 1 for f in files)
            assert all(0 <= f['coupling_score'] <= 1 for f in files)
            assert all(f['bus_factor'] is None or f['bus_factor'] >= 1 for f in files)
            network = get(rid, 'network')
            assert len(network['nodes']) <= 50
            node_ids = {n['id'] for n in network['nodes']}
            assert all(e['source'] in node_ids and e['target'] in node_ids and e['edge_weight'] > 0 for e in network['edges'])
            assert all(0 <= n['betweenness_centrality'] <= 1 for n in network['nodes'])
            get(rid, 'network/top-pairs')
            if rid == 9:
                risk = get(rid, 'risk-scores?limit=10000')
                bf = get(rid, 'bus-factor?limit=10000')
                coupling = get(rid, 'coupling?limit=10000')
                risk_by_id = {f['file_id']: f for f in risk['files']}
                bf_by_id = {f['file_id']: f for f in bf['files']}
                for f in files:
                    if f['file_id'] in risk_by_id:
                        assert f['risk_score'] == risk_by_id[f['file_id']]['risk_score']
                    if f['file_id'] in bf_by_id:
                        assert f['bus_factor'] == bf_by_id[f['file_id']]['bus_factor']
                check('repo 9 tree / risk / BF 계약 일치', True)
                assert all(p['co_change_count'] >= 2 and 0 <= p['coupling_score'] <= 1 for p in coupling['pairs'])
                if files:
                    chosen = next((f for f in files if f['bus_factor'] == 1), files[0])
                    detail = get(rid, f"files/{chosen['file_id']}/detail")
                    assert chosen['bus_factor'] == detail['bus_factor']
                    check('repo 9 파일 상세 계약 일치', True, {'file_id': chosen['file_id']})
                    if detail['editors']:
                        cid = detail['editors'][0]['contributor_id']
                        get(rid, f'contributors/{cid}/owned-files')
                        get(rid, f'contributors/{cid}/collaborators')
            if summary['total_commits'] == 0:
                check(f'repo {rid} 커밋 없는 파일 점수 null', all(f['risk_score'] is None for f in files))


if __name__ == '__main__':
    try:
        main()
    finally:
        from scripts.validation_reports import write_validation_report
        output = write_validation_report('production-integration', results)
        print(f'Validation report: {output}', flush=True)
