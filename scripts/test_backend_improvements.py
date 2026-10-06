"""격리 PostgreSQL에서 설치·작업 복구·결과 비교를 검증한다.

GITVIZ_TEST_DATABASE_URL은 이름이 gitviz_test로 시작하는 전용 DB여야 한다.
운영 .env의 DB는 사용하지 않는다.
"""
import os
import sys
import unittest
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import ExitStack
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
URL = os.environ.get('GITVIZ_TEST_DATABASE_URL')
if not URL:
    raise RuntimeError('GITVIZ_TEST_DATABASE_URL에 격리 PostgreSQL을 지정하세요.')
from sqlalchemy.engine import make_url
if not (make_url(URL).database or '').startswith('gitviz_test'):
    raise RuntimeError('DB 이름은 gitviz_test로 시작해야 합니다.')
os.environ['DATABASE_URL'] = URL
os.environ['DATABASE_URL_ASYNC'] = URL.replace('postgresql://', 'postgresql+asyncpg://', 1)
os.environ['SESSION_SECRET_KEY'] = 'isolated-validation-secret'

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text, event
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from app.core.database import engine, SessionLocal, Base
from app.models import Repository, AnalysisJob, AnalysisSnapshot
from app.models.user import User
from app.api.repositories import _trigger_analysis
from app.api.snapshots import router
from app.services.job_lifecycle import job_staleness, LOCK_NAMESPACE
from app.services.analysis_snapshots import payload_digest
from app.services.snapshot_comparison import compare_snapshots


def snapshot(repo_id, risk=.3, **kw):
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    payload = {'tree': {'files': [{'path':'a.py', 'risk_score':risk, 'bus_factor':1, 'coupling_score':.2}]},
               'data_quality': {'commit_count':10}}
    return AnalysisSnapshot(repository_id=repo_id, target_sha='a'*40, analyzed_at=now,
        window_start=None, window_end=now, formula_version='v1',
        parameters={'hash_format':'canonical-json-numbers-v1'}, payload=payload,
        payload_sha256=payload_digest(payload), **kw)


class Backend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = Config(str(ROOT / 'alembic.ini'))
        command.upgrade(cls.config, 'head')

    def setUp(self):
        self.db = SessionLocal()
        self.repo = Repository(url='https://github.com/test/repo',name='repo',github_id=None,is_private=False)
        self.db.add(self.repo); self.db.commit()
        self.repo.github_id=self.repo.id; self.db.commit()

    def tearDown(self):
        self.db.close()

    def job(self, status, age=86400):
        now = datetime.now(timezone.utc)
        job = AnalysisJob(repository_id=self.repo.id,status=status,
            created_at=now-timedelta(seconds=age),started_at=None if status=='pending' else now-timedelta(seconds=age))
        self.db.add(job);self.db.commit()
        return job

    def test_all_model_columns_present(self):
        inspector=inspect(engine)
        for table in list(Base.metadata.tables.values())+[User.__table__]:
            actual={c['name'] for c in inspector.get_columns(table.name)}
            self.assertFalse(set(table.columns.keys())-actual,table.name)
        self.assertEqual(self.db.execute(text('SELECT version_num FROM alembic_version')).scalar(),'c3e9a1b7d550')

    def test_priority_policy_with_actual_postgres_snapshot_scope(self):
        from app.services.analysis_snapshots import analysis_parameters, SnapshotInput
        from app.api.analytics import _compute_risk_scores, _compute_coupling
        rid=self.repo.id
        fid=self.db.execute(text("INSERT INTO files(repository_id,path,is_deleted) VALUES(:rid,'policy.py',false) RETURNING id"),{'rid':rid}).scalar()
        for i,kind in enumerate(['feature','refactor','bugfix']):
            cid=self.db.execute(text("""INSERT INTO commits(repository_id,sha,commit_type,committed_at,changed_file_count,is_large_commit)
                VALUES(:rid,:sha,:kind,:date,2,true) RETURNING id"""),
                {'rid':rid,'sha':f'policy-{i}','kind':kind,'date':datetime(2026,9,1+i,tzinfo=timezone.utc)}).scalar()
            self.db.execute(text('INSERT INTO commit_file_changes(commit_id,file_id,additions,deletions) VALUES(:cid,:fid,10,0)'),{'cid':cid,'fid':fid})
        params=analysis_parameters(end='2026-10-01T00:00:00Z')
        scoped=SnapshotInput(self.db,rid,params,['policy.py'],['policy-0','policy-1','policy-2'])
        flags=scoped.execute(text('SELECT is_large_commit FROM commits'),{}).mappings().all()
        self.assertTrue(all(not r['is_large_commit'] for r in flags))
        priority=_compute_risk_scores(scoped,rid)[0]
        self.assertEqual(priority['change_freq'],3)
        self.assertEqual(priority['bugfix_count'],1)
        self.assertIsNone(priority['entropy'])
        self.assertEqual(_compute_coupling(scoped,rid)['fallback_reason'],'no_merged_prs')
        # 실제 계산 결과를 JSONB로 저장한 후 HTTP 조회가 저장값을 사용하는지 확인한다.
        from app.services.analysis_snapshots import build_snapshot
        from app.services.metric_policy import FORMULA_VERSION
        from app.api.analytics import router as analytics_router
        saved=build_snapshot(self.db,self.repo,None,target_sha='a'*40,
            live_paths=['policy.py'],reachable_shas=['policy-0','policy-1','policy-2'],
            parameters=params)
        self.db.commit()
        self.db.expire(saved)
        self.assertEqual(saved.formula_version,FORMULA_VERSION)
        self.assertEqual(saved.payload_sha256,payload_digest(saved.payload))
        stored=saved.payload['risk-scores']['files'][0]
        self.assertEqual(stored['change_freq'],3)
        self.assertEqual(stored['bugfix_count'],1)
        self.assertEqual(stored['maintenance_priority'],stored['risk_score'])
        app=FastAPI();app.add_middleware(SessionMiddleware,secret_key='test')
        app.include_router(analytics_router)
        with TestClient(app) as client, patch('app.services.repository_access.github_metadata',
                return_value={'id':self.repo.github_id,'private':False}), patch(
                'app.api.analytics._compute_risk_scores',side_effect=AssertionError('GET must not recompute')):
            response=client.get(f'/repositories/{rid}/analytics/risk-scores?snapshot_id={saved.id}')
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['files'][0],stored)

    def test_upgrade_old_revision_preserves_data(self):
        schema='gitviz_validation_'+uuid4().hex
        config=Config(str(ROOT/'alembic.ini'))
        with engine.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA {schema}'))
            connection.execute(text(f'SET search_path TO {schema}'))
            connection.commit()
            config.attributes['connection']=connection
            try:
                command.upgrade(config,'393e33b2dcbb')
                connection.execute(text("INSERT INTO repositories(id,url,name) VALUES(1,'https://github.com/test/legacy','legacy')"))
                connection.execute(text("INSERT INTO commits(id,repository_id,commit_hash,message,committed_at) VALUES(1,1,'legacy-sha','preserved','2026-10-01T00:00:00Z')"))
                connection.execute(text("INSERT INTO files(id,repository_id,path) VALUES(1,1,'old.py')"))
                connection.execute(text('CREATE TABLE commit_file_change (id SERIAL PRIMARY KEY, commit_id INTEGER REFERENCES commits(id), file_id INTEGER REFERENCES files(id), additions INTEGER, deletions INTEGER)'))
                connection.execute(text('INSERT INTO commit_file_change(commit_id,file_id,additions,deletions) VALUES(1,1,10,2)'))
                connection.commit()
                command.upgrade(config,'e2f3a4b5c6d7')
                connection.execute(text('ALTER TABLE commits DROP COLUMN on_default_branch'))
                connection.execute(text('ALTER TABLE analysis_jobs DROP COLUMN triggered_by_user_id'))
                connection.execute(text("CREATE TABLE users (id UUID PRIMARY KEY, github_id BIGINT UNIQUE NOT NULL, github_login VARCHAR(255) NOT NULL, display_name VARCHAR(255), avatar_url TEXT, email VARCHAR(255), created_at TIMESTAMPTZ DEFAULT now(), last_login_at TIMESTAMPTZ)"))
                connection.execute(text("INSERT INTO users(id,github_id,github_login) VALUES('00000000-0000-0000-0000-000000000001',1,'preserved-user')"))
                connection.commit()
                command.upgrade(config,'head')
                self.assertEqual(connection.execute(text('SELECT message FROM commits')).scalar(),'preserved')
                self.assertEqual(connection.execute(text('SELECT additions FROM commit_file_changes')).scalar(),10)
                self.assertEqual(connection.execute(text('SELECT github_login FROM users')).scalar(),'preserved-user')
                self.assertIn('on_default_branch',{c['name'] for c in inspect(connection).get_columns('commits',schema=schema)})
                self.assertIn('triggered_by_user_id',{c['name'] for c in inspect(connection).get_columns('analysis_jobs',schema=schema)})
                self.assertEqual(connection.execute(text('SELECT version_num FROM alembic_version')).scalar(),'c3e9a1b7d550')
            finally:
                connection.rollback()
                connection.execute(text('RESET search_path'))
                connection.commit()

    def test_old_pending_can_restart_and_queue_failure_can_retry(self):
        old=self.job('pending')
        with patch('app.api.repositories.analyze_repository.delay') as queue:
            new_id=_trigger_analysis(self.db,self.repo.id,None)
            queue.assert_called_once()
        self.db.refresh(old);self.assertEqual(old.status,'cancelled')
        self.assertNotEqual(old.id,new_id)
        current=self.db.get(AnalysisJob,new_id);current.status='failed';self.db.commit()
        with patch('app.api.repositories.analyze_repository.delay',side_effect=RuntimeError('offline')):
            with self.assertRaises(HTTPException) as exc:_trigger_analysis(self.db,self.repo.id,None)
            self.assertEqual(exc.exception.status_code,503)
        with patch('app.api.repositories.analyze_repository.delay'):
            self.assertGreater(_trigger_analysis(self.db,self.repo.id,None),new_id)

    def test_live_worker_lock_prevents_replacement(self):
        old=self.job('running')
        with engine.connect() as worker:
            worker.execute(text('SELECT pg_advisory_lock(:ns,:rid)'),{'ns':LOCK_NAMESPACE,'rid':self.repo.id})
            try:
                with patch('app.api.repositories.analyze_repository.delay') as queue:
                    with self.assertRaises(HTTPException) as exc:_trigger_analysis(self.db,self.repo.id,None)
                    self.assertEqual(exc.exception.status_code,409);queue.assert_not_called()
                self.db.rollback();self.db.refresh(old);self.assertEqual(old.status,'running')
            finally:
                worker.execute(text('SELECT pg_advisory_unlock(:ns,:rid)'),{'ns':LOCK_NAMESPACE,'rid':self.repo.id})

    def test_recent_pending_not_replaced(self):
        old=self.job('pending',age=10)
        with patch('app.api.repositories.analyze_repository.delay') as queue:
            with self.assertRaises(HTTPException):_trigger_analysis(self.db,self.repo.id,None)
            queue.assert_not_called()
        self.db.rollback();self.db.refresh(old);self.assertEqual(old.status,'pending')

    def test_cancelled_delivery_does_not_collect(self):
        from app.tasks.analyze_repository import analyze_repository
        old=self.job('cancelled')
        with patch('app.tasks.analyze_repository.clone_repository') as clone:
            analyze_repository.run(self.repo.id,job_id=old.id)
            clone.assert_not_called()
        self.db.refresh(old);self.assertEqual(old.status,'cancelled')

    def test_worker_success_saves_job_and_snapshot_together(self):
        from app.tasks.analyze_repository import analyze_repository
        job=self.job('pending',age=0)
        metadata={'id':self.repo.github_id,'private':False,'canonical_url':self.repo.url}
        replacements={'clone_repository':'unused-validation-path','get_default_branch':None,
            'collect_commits':[],'collect_files':[],'collect_commit_file_changes':[],
            'collect_pull_requests':[],'collect_releases':[],'run_identity_resolution':{},
            'snapshot_target':('a'*40,[],[])}
        with ExitStack() as stack:
            stack.enter_context(patch('app.services.repository_access.github_metadata',return_value=metadata))
            for name,value in replacements.items():
                stack.enter_context(patch('app.tasks.analyze_repository.'+name,return_value=value))
            analyze_repository.run(self.repo.id,job_id=job.id)
        self.db.refresh(job)
        self.assertEqual(job.status,'done',job.error_message)
        saved=self.db.query(AnalysisSnapshot).filter_by(analysis_job_id=job.id).one()
        self.assertEqual(job.parameters['snapshot_id'],saved.id)
        self.assertEqual(job.last_commit_sha,saved.target_sha)
        self.assertIsNotNone(job.completed_at)

    def test_countdown_and_invalid_time_are_safe(self):
        now=datetime.now(timezone.utc)
        job=SimpleNamespace(status='rate_limited',progress_detail={'resume_at':(now+timedelta(seconds=60)).isoformat()},created_at=now,started_at=now)
        self.assertEqual(job_staleness(job,now)['remaining_seconds'],60)
        job.progress_detail={'resume_at':'invalid'}
        self.assertFalse(job_staleness(job,now)['is_stale'])
        job.status='pending';job.created_at=(now-timedelta(days=2)).replace(tzinfo=None)
        self.assertTrue(job_staleness(job,now)['is_stale'])

    def test_comparison_null_added_removed_and_versions(self):
        a,b=snapshot(self.repo.id),snapshot(self.repo.id,risk=None)
        b.payload['tree']['files'].append({'path':'new.py','risk_score':.8})
        result=compare_snapshots(a,b)
        self.assertEqual(result['summary'],{'added':1,'removed':0,'retained':1,'changed':1})
        self.assertIsNone(result['changes'][0]['metrics']['risk_score']['delta'])
        b.payload['tree']['files']=[]
        self.assertEqual(compare_snapshots(a,b)['summary']['removed'],1)
        b.formula_version='v2'
        with self.assertRaises(HTTPException) as exc:compare_snapshots(a,b)
        self.assertEqual(exc.exception.status_code,409)

    def test_http_history_compare_authorization_and_hash(self):
        a,b=snapshot(self.repo.id),snapshot(self.repo.id,risk=.7)
        self.db.add_all([a,b]);self.db.commit()
        app=FastAPI();app.add_middleware(SessionMiddleware,secret_key='test');app.include_router(router)
        with TestClient(app) as client, patch('app.services.repository_access.github_metadata',return_value={'id':self.repo.id,'private':False}):
            url=f'/repositories/{self.repo.id}/snapshots'
            history=client.get(url+'?limit=1')
            self.assertEqual(history.status_code,200)
            self.assertEqual(history.json()['next_before_id'],b.id)
            page=client.get(url+f'?before_id={b.id}')
            self.assertEqual(page.json()['snapshots'][0]['id'],a.id)
            comparison=client.get(url+f'/compare?before={a.id}&after={b.id}')
            self.assertEqual(comparison.status_code,200)
            self.assertEqual(comparison.json()['changes'][0]['metrics']['risk_score']['delta'],.4)
            self.assertEqual(comparison.headers['cache-control'],'private, no-store')
            self.assertEqual(comparison.json()['pagination']['total_changes'],1)
            self.assertEqual(client.get(url+f'/compare?before={a.id}&after={b.id}&offset=1').json()['changes'],[])
            self.assertEqual(client.get(url+'/compare?before=0&after=1').status_code,422)
            other=Repository(url='https://github.com/test/other',name='other',is_private=False)
            self.db.add(other);self.db.commit()
            foreign=snapshot(other.id);self.db.add(foreign);self.db.commit()
            self.assertEqual(client.get(url+f'/compare?before={a.id}&after={foreign.id}').status_code,404)
            self.assertEqual(compare_snapshots(a,a)['changes'],[])
            with self.assertRaises(HTTPException):compare_snapshots(a,foreign)
            with patch('app.services.repository_access.github_metadata',return_value={'id':self.repo.id,'private':True}):
                self.assertEqual(client.get(url).status_code,404)
            with patch('app.services.repository_access.github_metadata',side_effect=HTTPException(404,'revoked')):
                self.assertEqual(client.get(url+'/compare?before=1&after=2').status_code,404)
            corrupted=snapshot(self.repo.id,risk=.5)
            corrupted.payload_sha256='0'*64
            self.db.add(corrupted);self.db.commit()
            self.assertEqual(client.get(url+f'/compare?before={a.id}&after={corrupted.id}').status_code,409)

    def test_snapshot_is_immutable_and_head_is_repeatable(self):
        item=snapshot(self.repo.id);self.db.add(item);self.db.commit()
        with self.assertRaises(Exception):
            self.db.execute(text('UPDATE analysis_snapshots SET target_sha=:sha WHERE id=:sid'),{'sha':'b'*40,'sid':item.id})
        self.db.rollback()
        command.upgrade(self.config,'head')

    def test_app_import_has_no_schema_writes(self):
        def prohibit_ddl(conn,cursor,statement,params,context,many):
            if statement.lstrip().upper().startswith(('CREATE','ALTER','DROP')):
                raise AssertionError('앱 import에서 DDL 실행')
        event.listen(engine,'before_cursor_execute',prohibit_ddl)
        try:
            import app.main
            self.assertFalse(app.main.app.router.on_startup)
        finally:event.remove(engine,'before_cursor_execute',prohibit_ddl)

    def test_local_async_auth_database_connects(self):
        from app.database import engine as async_engine
        async def check():
            async with async_engine.connect() as connection:
                self.assertEqual((await connection.execute(text('SELECT 1'))).scalar(),1)
            await async_engine.dispose()
        asyncio.run(check())


if __name__=='__main__':unittest.main()
