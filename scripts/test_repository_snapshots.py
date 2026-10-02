"""python scripts/test_repository_snapshots.py: 접근 제어와 저장 결과 회귀 검사."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from app.models.repository import Repository
from app.models.analysis_snapshot import AnalysisSnapshot
from app.services.repository_access import canonical_github_url, verify_repository_access, github_metadata
from app.services.analysis_snapshots import analysis_parameters, SnapshotInput, serve_snapshot, payload_digest
from app.collectors.git.clone_manager import repository_path, git_environment
from app.api.analytics import router, _coupled_files_for


class Regression(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Repository.__table__.create(self.engine)
        AnalysisSnapshot.__table__.create(self.engine)
        self.Session=sessionmaker(self.engine)
        now=datetime(2026,1,1,tzinfo=timezone.utc)
        with self.Session() as db:
            for rid in [1,2]:
                db.add(Repository(id=rid,name='same',url=f'https://github.com/owner{rid}/same',github_id=rid,is_private=False))
                db.add(AnalysisSnapshot(id=rid,repository_id=rid,target_sha=str(rid)*40,analyzed_at=now,
                    window_end=now,formula_version='test',parameters={'hash_format':'canonical-json-numbers-v1'},payload={'summary':{'value':rid},'data_quality':{}},payload_sha256=payload_digest({'summary':{'value':rid},'data_quality':{}})))
            db.commit()
        app=FastAPI();app.add_middleware(SessionMiddleware,secret_key='test');app.include_router(router)
        self.client=TestClient(app)
        self.session_patch=patch('app.api.snapshot_route.SessionLocal',self.Session);self.session_patch.start()
        self.metadata_patch=patch('app.services.repository_access.github_metadata',side_effect=lambda url,token,**kw: {'id':int(url.split('owner')[1][0]),'private':False});self.metadata_patch.start()
    def tearDown(self):
        self.metadata_patch.stop();self.session_patch.stop();self.client.close();self.engine.dispose()
    def test_read_is_pinned_and_cross_repo_snapshot_denied(self):
        first=self.client.get('/repositories/1/analytics/summary?snapshot_id=1')
        self.assertEqual(first.status_code,200);self.assertEqual(first.json()['value'],1)
        self.assertEqual(self.client.get('/repositories/2/analytics/summary?snapshot_id=1').status_code,404)
        self.assertEqual(first.headers['cache-control'],'private, no-store')
    def test_private_denies_shared_token_and_anonymous(self):
        with patch('app.services.repository_access.github_metadata',return_value={'id':1,'private':True}):
            self.assertEqual(self.client.get('/repositories/1/analytics/summary').status_code,404)
            with self.Session() as db, patch('app.services.repository_access.user_github_token',return_value='personal'):
                self.assertEqual(verify_repository_access(db,1,'user')[0].id,1)
    def test_revocation_and_replaced_identity_fail_closed(self):
        with patch('app.services.repository_access.github_metadata',side_effect=HTTPException(404,'revoked')):
            self.assertEqual(self.client.get('/repositories/1/analytics/summary').status_code,404)
        with patch('app.services.repository_access.github_metadata',return_value={'id':999,'private':False}):
            self.assertEqual(self.client.get('/repositories/1/analytics/summary').status_code,404)
    def test_missing_snapshot_and_invalid_filters(self):
        self.assertEqual(self.client.get('/repositories/1/analytics/summary?snapshot_id=-1').status_code,422)
        self.assertEqual(self.client.get('/repositories/1/analytics/summary?limit=nan').status_code,422)
        with self.Session() as db:
            db.add(Repository(id=3,name='same',url='https://github.com/owner3/same',github_id=3,is_private=False));db.commit()
        self.assertEqual(self.client.get('/repositories/3/analytics/summary').status_code,409)
    def test_no_anonymous_write(self):
        with self.Session() as db, self.assertRaises(HTTPException) as error:
            verify_repository_access(db,1,write=True)
        self.assertEqual(error.exception.status_code,401)
    def test_url_and_storage_isolation(self):
        self.assertEqual(canonical_github_url('https://github.com/Owner/Repo.git/'),'https://github.com/owner/repo')
        for url in ['file:///tmp/x','https://evil.com/a/b','https://github.com@evil.com/a/b','https://github.com/a/../b','https://github.com/a/b?x=1','https://github.com:443/a/b']:
            with self.assertRaises(ValueError):canonical_github_url(url)
        self.assertNotEqual(repository_path(1),repository_path(2))
        for rid in [0,-1,True,'../a']:
            with self.assertRaises(ValueError):repository_path(rid)
        self.assertNotIn('personal',git_environment('personal')['GIT_CONFIG_VALUE_0'])
    def test_fixed_period_and_future_exclusion_sql(self):
        parameters=analysis_parameters('2024-01-01T00:00:00Z','2025-10-01T00:00:00Z')
        class Capture:
            def execute(self,sql,params):return str(sql),params
        sql,params=SnapshotInput(Capture(),1,parameters,['a.py'],['abc']).execute('SELECT now() FROM commits')
        self.assertIn('committed_at < :snapshot_end',sql)
        self.assertIn('merged_at < :snapshot_end',sql)
        self.assertIn('sha = ANY(:snapshot_shas)',sql)
        self.assertNotIn('now()',sql)
        self.assertEqual(params['snapshot_shas'],['abc'])
        with self.assertRaises(ValueError):analysis_parameters('2025-01-01','2026-01-01')
    def test_asymmetric_coupling_and_low_support(self):
        pair={'file_a_id':1,'file_a_path':'a','file_b_id':2,'file_b_path':'b','co_change_count':2,
            'change_count_a':100,'change_count_b':2,'a_to_b_ratio':.02,'b_to_a_ratio':1,
            'coupling_score':1,'adjusted_score':1,'low_support':True}
        a=_coupled_files_for({'pairs':[pair]},1)[0];b=_coupled_files_for({'pairs':[pair]},2)[0]
        self.assertEqual(a['selected_to_other_ratio'],.02);self.assertEqual(b['selected_to_other_ratio'],1)
        self.assertTrue(a['low_support'])
    def test_reads_do_not_modify_persisted_payload(self):
        with self.Session() as db:
            snapshot=db.get(AnalysisSnapshot,1)
            first=serve_snapshot(snapshot,'summary',{});first['value']=99
            self.assertEqual(serve_snapshot(snapshot,'summary',{})['value'],1)
    def test_jsonb_numeric_hash_stability(self):
        self.assertEqual(payload_digest({'entropy':-0.0,'score':1.0}),payload_digest({'entropy':0,'score':1}))
    def test_etag_still_checks_current_github_authorization(self):
        from types import SimpleNamespace
        url='https://github.com/etag-fixture/private'
        data={'id':555,'private':True,'html_url':url}
        replies=[SimpleNamespace(status_code=200,ok=True,headers={'ETag':'v1'},json=lambda:data),
                 SimpleNamespace(status_code=304,ok=True,headers={}),
                 SimpleNamespace(status_code=404,ok=False,headers={})]
        with patch('app.services.repository_access.requests.get',side_effect=replies) as get:
            self.assertTrue(github_metadata(url,'personal')['private'])
            self.assertTrue(github_metadata(url,'personal')['private'])
            self.assertEqual(get.call_args.kwargs['headers']['If-None-Match'],'v1')
            with self.assertRaises(HTTPException) as exc:github_metadata(url,'personal')
            self.assertEqual(exc.exception.status_code,404)
            self.assertEqual(get.call_count,3)
    def test_personal_rate_limit_does_not_fall_back_to_other_credentials(self):
        from types import SimpleNamespace
        with patch('app.services.repository_access.requests.get',return_value=SimpleNamespace(status_code=403,ok=False,headers={})) as get:
            with self.assertRaises(HTTPException) as exc:github_metadata('https://github.com/rate-fixture/private','personal')
            self.assertEqual(exc.exception.status_code,429);self.assertEqual(get.call_count,1)
    def test_payload_corruption_is_rejected(self):
        with self.Session() as db:
            row=db.get(AnalysisSnapshot,1);row.payload={'summary':{'value':999},'data_quality':{}};db.commit()
        self.assertEqual(self.client.get('/repositories/1/analytics/summary').status_code,409)
    def test_unverified_legacy_identity_cannot_expose_old_data(self):
        with self.Session() as db:
            repo=db.get(Repository,1);repo.github_id=None;db.commit()
        self.assertEqual(self.client.get('/repositories/1/analytics/summary').status_code,409)


if __name__=='__main__':unittest.main()
