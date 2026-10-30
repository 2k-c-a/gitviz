"""저장 구조/조회 API의 운영 DB 통합 검사. 트랜잭션은 읽기 전용."""
import sys,json,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import httpx
from sqlalchemy import text
from app.core.database import SessionLocal
from app.models.analysis_snapshot import AnalysisSnapshot
from app.models.repository import Repository
from app.collectors.git.clone_manager import repository_path, REPO_ROOT
from git import Repo
from app.services.analysis_snapshots import serve_snapshot, payload_digest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware


def main(auth_fixture=False):
    checks=[]
    def check(name,condition):
        checks.append({'check':name,'passed':bool(condition)})
        print(('PASS ' if condition else 'FAIL ')+name,flush=True)
        if not condition:raise AssertionError(name)
    with SessionLocal() as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        check('migration',db.execute(text('SELECT version_num FROM alembic_version')).scalar()=='f6b8d0a2c4e1')
        check('immutable trigger installed',bool(db.execute(text("SELECT COUNT(*) FROM pg_trigger WHERE tgname='analysis_snapshot_immutable' AND NOT tgisinternal")).scalar()))
        for (rid,) in db.query(AnalysisSnapshot.repository_id).distinct():
            latest=db.query(AnalysisSnapshot).filter_by(repository_id=rid).order_by(AnalysisSnapshot.id.desc()).first()
            check(f'latest payload hash repo {rid}',payload_digest(latest.payload)==latest.payload_sha256)
        snapshot=db.query(AnalysisSnapshot).filter_by(repository_id=9).order_by(AnalysisSnapshot.id.desc()).first()
        check('saved snapshot exists',snapshot is not None)
        sid=snapshot.id
        digest=payload_digest(snapshot.payload)
        check('persisted payload hash',digest==snapshot.payload_sha256)
        repo=db.get(Repository,9)
        local=repository_path(9) if repository_path(9).exists() else REPO_ROOT/repo.name
        reachable=Repo(local).git.rev_list(snapshot.target_sha).splitlines()
        check('window and target ancestry',snapshot.payload['data_quality']['commit_count']==db.execute(text('SELECT COUNT(*) FROM commits WHERE repository_id=9 AND committed_at < :end AND sha=ANY(:shas)'),{'end':snapshot.window_end,'shas':reachable}).scalar())
        expected=serve_snapshot(snapshot,'summary',{})
        fid=snapshot.payload['tree']['files'][0]['file_id']
        cid=snapshot.payload['contributor_index'][0]['id']
    from contextlib import ExitStack
    with ExitStack() as stack:
        if auth_fixture:
            # 현재 GitHub 한도와 무관하게 실제 운영 DB의 저장값/응답 계약을 검사한다.
            # 현재 GitHub 권한 검증을 통과했다는 의미는 아니다. 프로덕션 코드 변경 없음.
            from app.api.analytics import router
            from app.core.database import engine
            from sqlalchemy import event
            def readonly(conn):conn.exec_driver_sql('SET TRANSACTION READ ONLY')
            event.listen(engine,'begin',readonly)
            stack.callback(event.remove,engine,'begin',readonly)
            stack.enter_context(patch('app.api.snapshot_route.verify_repository_access',return_value=(None,None)))
            app=FastAPI();app.add_middleware(SessionMiddleware,secret_key='test-only');app.include_router(router)
            client=stack.enter_context(TestClient(app))
        else:
            client=stack.enter_context(httpx.Client(base_url='http://localhost:8000',timeout=45))
        routes=['snapshot','summary','timeline','commit-types','releases','top-contributors','risk-scores','bus-factor','coupling','network','network/top-pairs','tree','contributor-heatmap',f'files/{fid}/detail',f'coupling/{fid}',f'contributors/{cid}/owned-files',f'contributors/{cid}/collaborators']
        for route in routes:
            res=client.get(f'/repositories/9/analytics/{route}',params={'snapshot_id':sid})
            check(f'HTTP {route}',res.status_code==200)
            if isinstance(res.json(),dict):check(f'pinned {route}',res.json()['_snapshot']['id']==sid)
        first=client.get(f'/repositories/9/analytics/summary?snapshot_id={sid}').json()
        second=client.get(f'/repositories/9/analytics/summary?snapshot_id={sid}').json()
        check('repeat identical response',first==second==expected)
        check('cross-repository replay denied',client.get(f'/repositories/6/analytics/tree?snapshot_id={sid}').status_code==404)
        check('unknown repository has no snapshot',client.get('/repositories/999999/analytics/tree').status_code==(409 if auth_fixture else 404))
        small=client.get('/repositories/6/analytics/tree').json()
        check('small evidence labelled',small['files'][0]['evidence_status']=='low_support' and small['files'][0]['observation_count']==1)
        empty=client.get('/repositories/11/analytics/tree').json()
        check('empty data explicitly reported',empty['file_count']==0 and empty['data_quality']['commit_count']==0 and empty['data_quality']['target_tree_file_count']>0)
        coupling=client.get(f'/repositories/9/analytics/coupling?snapshot_id={sid}').json()
        check('directional ratios/support',all(p['a_to_b_ratio']==round(p['co_change_count']/p['change_count_a'],4) and p['low_support']==(p['co_change_count']<5) for p in coupling['pairs']))
    from tests.server.validation_reports import write_validation_report
    output = write_validation_report('snapshot-integration', {'authorization_mode':'test_fixture' if auth_fixture else 'live_github','checks':checks})
    print(f'Validation report: {output}')
    print(f'{len(checks)} checks passed')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--auth-fixture',action='store_true')
    main(parser.parse_args().auth_fixture)
