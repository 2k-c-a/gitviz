"""Dependency-free tests executing actual analytics SQL on SQLite fixtures.

SQLite FILTER aggregates match the new ranking queries. PostgreSQL-specific
snapshot scoping is separately inspected here and exercised by integration CI.
"""
import ast
import math
import json
import sqlite3
import statistics
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.services.metric_policy import percentile_rank, large_change_threshold, DEFAULT_WINDOW_DAYS


def load_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    body = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), 'exec'), namespace)


ns = {'math':math, 'Session':object, 'datetime':datetime, 'timezone':timezone,
      'timedelta':timedelta, 'text':lambda s:s, 'DEFAULT_WINDOW_DAYS':DEFAULT_WINDOW_DAYS,
      'percentile_rank':percentile_rank,'statistics':statistics}
ns['Depends']=lambda x:None
ns['get_db']=None
ns['router']=SimpleNamespace(get=lambda *args,**kwargs:lambda f:f)
load_functions(ROOT/'app/api/analytics.py', {'_ownership_analysis','_metric_window',
    '_fetch_recent_ownership','_compute_risk_scores','get_risk_scores','_coupled_files_for','_compute_coupling','get_coupling',
    '_module_of','get_bus_factor_analysis','get_repo_tree','_build_tree','_per_file_coupling_summary'},ns)
load_functions(ROOT/'app/services/coupled_files.py', {'_coupled_files_for'}, ns)
for node in ast.parse((ROOT/'app/services/coupled_files.py').read_text(encoding='utf-8')).body:
    if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='COUPLED_SORT_KEYS' for t in node.targets):
        exec(compile(ast.Module(body=[node],type_ignores=[]),'coupled-sort-keys','exec'),ns)
analytics_tree=ast.parse((ROOT/'app/api/analytics.py').read_text(encoding='utf-8'))
for node in analytics_tree.body:
    if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'_PR_PAIRS_SQL','_COMMIT_PAIRS_SQL'} for t in node.targets):
        exec(compile(ast.Module(body=[node],type_ignores=[]),'pair-sql','exec'),ns)
import copy
class HTTPError(Exception):
    def __init__(self, status, message): self.status_code=status; super().__init__(message)
snap_ns = {'datetime':datetime,'timezone':timezone,'timedelta':timedelta,
           'DEFAULT_WINDOW_DAYS':180,'LOW_SUPPORT_THRESHOLD':5,'FORMULA_VERSION':'test-v3',
           'large_change_threshold':large_change_threshold,'copy':copy,'HTTPException':HTTPError,
           'text':lambda sql:sql,'json':json}
load_functions(ROOT/'app/services/analysis_snapshots.py', {'utc_datetime','analysis_parameters',
    'SnapshotInput','FrozenRows','bounded','serve_snapshot'},snap_ns)
snap_ns['snapshot_metadata']=lambda snapshot:{'formula_version':snapshot.formula_version}


class Rows:
    def __init__(self, cursor): self.cursor = cursor
    def mappings(self): return self
    def scalar(self):return self.cursor.fetchone()[0]
    def one(self):return self.all()[0]
    def all(self):
        rows = [dict(r) for r in self.cursor.fetchall()]
        for r in rows:
            for k in ('first_observed_at','last_refactor_at'):
                if r.get(k): r[k] = datetime.fromisoformat(r[k])
        return rows


class DB:
    def __init__(self):
        self.info = {'analysis_as_of':datetime(2026,10,1,tzinfo=timezone.utc)}
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
          CREATE TABLE files(id INTEGER,repository_id INTEGER,path TEXT,is_deleted BOOLEAN);
          CREATE TABLE contributors(id INTEGER,display_name TEXT,github_login TEXT);
          CREATE TABLE commits(id INTEGER,repository_id INTEGER,contributor_id INTEGER,commit_type TEXT,committed_at TEXT,is_large_commit BOOLEAN DEFAULT false);
          CREATE TABLE commit_file_changes(commit_id INTEGER,file_id INTEGER,additions INTEGER,deletions INTEGER);
          CREATE TABLE pull_requests(id INTEGER,repository_id INTEGER,state TEXT,is_large_pr BOOLEAN);
          CREATE TABLE pull_request_files(pull_request_id INTEGER,file_id INTEGER);
          INSERT INTO files VALUES (1,1,'a.py',false),(2,1,'b.py',false),(3,1,'missing.py',false),(4,1,'deleted.py',true),(5,2,'other.py',false);
          INSERT INTO contributors VALUES (1,'Alice','alice'),(2,'Bob','bob');
        ''')
    def execute(self, sql, params):
        params = {k:v.isoformat() if isinstance(v,datetime) else v for k,v in params.items()}
        return Rows(self.conn.execute(sql,params))
    def change(self,cid,fid,day,kind='feature',owner=1,lines=10,rid=1):
        self.conn.execute('INSERT INTO commits(id,repository_id,contributor_id,commit_type,committed_at) VALUES (?,?,?,?,?)',(cid,rid,owner,kind,day+'T00:00:00+00:00'))
        self.conn.execute('INSERT INTO commit_file_changes VALUES (?,?,?,0)',(cid,fid,lines))


class PolicyTests(unittest.TestCase):
    def test_pr_missing_and_filtered_fallbacks_are_distinguished(self):
        db=DB()
        try:
            result=ns['_compute_coupling'](db,1)
            self.assertEqual(result['fallback_reason'],'no_merged_prs')
            db.conn.execute("INSERT INTO pull_requests VALUES(1,1,'merged',false)")
            result=ns['_compute_coupling'](db,1)
            self.assertEqual(result['fallback_reason'],'pr_details_missing')
            self.assertEqual(result['pr_file_coverage'],0)
            db.conn.execute('INSERT INTO pull_request_files VALUES(1,1)')
            self.assertEqual(ns['_compute_coupling'](db,1)['co_change_unit'],'pr')
            db.conn.execute('UPDATE pull_requests SET is_large_pr=true')
            self.assertEqual(ns['_compute_coupling'](db,1)['fallback_reason'],'all_collected_prs_filtered')
        finally:db.conn.close()
    def test_directional_coupling_and_support_use_actual_sql(self):
        db=DB()
        try:
            for i in range(1,11):
                db.change(i,1,'2026-09-01')
            db.conn.executemany('INSERT INTO commit_file_changes VALUES(?,?,?,?)',[(1,2,1,0),(2,2,1,0)])
            pair=ns['_compute_coupling'](db,1)['pairs'][0]
            self.assertEqual(pair['coupling_score'],1)
            self.assertEqual(pair['a_to_b_ratio'],.2)
            self.assertEqual(pair['b_to_a_ratio'],1)
            self.assertTrue(pair['low_support'])
            self.assertEqual(ns['_coupled_files_for']({'pairs':[pair]},2)[0]['selected_to_other_ratio'],1)
        finally:db.conn.close()
    def test_snapshot_default_window_and_filter_recalculation(self):
        params=snap_ns['analysis_parameters'](end='2026-10-01T00:00:00Z')
        self.assertEqual(params['window_start'],'2026-04-04T00:00:00+00:00')
        class Capture:
            def execute(self,sql,args):return sql,args
        scope=snap_ns['SnapshotInput'](Capture(),1,params,['a','b','c'],['abc'])
        sql,args=scope.execute('SELECT * FROM commits')
        self.assertEqual(args['snapshot_large_threshold'],15)
        self.assertIn('AS is_large_commit',sql)
        self.assertIn('AS is_large_pr',sql)
        self.assertIn('sha = ANY(:snapshot_shas)',sql)
        self.assertEqual(scope.info['analysis_window_start'].isoformat(),params['window_start'])
    def test_snapshot_sorting_preserves_payload_and_rejects_invalid_policy(self):
        pairs=[{'file_a_id':1,'file_b_id':2,'co_change_count':3,'coupling_score':.9,'adjusted_score':.1},
               {'file_a_id':1,'file_b_id':3,'co_change_count':5,'coupling_score':.8,'adjusted_score':2}]
        snapshot=SimpleNamespace(formula_version='v3',payload={'coupling':{'pairs':pairs,'meta':{}},'data_quality':{}})
        raw=snap_ns['serve_snapshot'](snapshot,'coupling',{})
        idf=snap_ns['serve_snapshot'](snapshot,'coupling',{'sort_by':'idf'})
        self.assertEqual(raw['pairs'][0]['file_b_id'],2)
        self.assertEqual(idf['pairs'][0]['file_b_id'],3)
        self.assertEqual(snapshot.payload['coupling']['pairs'],pairs)
        self.assertNotIn('sort_by',snapshot.payload['coupling']['meta'])
        with self.assertRaises(HTTPError):
            snap_ns['serve_snapshot'](snapshot,'coupling',{'sort_by':'unknown'})
    def test_percentiles_ties_missing_and_constants(self):
        self.assertEqual(percentile_rank({'a':0,'b':10,'c':10,'d':20,'e':None}),
                         {'a':0,'b':.5,'c':.5,'d':1,'e':None})
        self.assertEqual(percentile_rank({'a':3,'b':3,'c':None}),{'a':0,'b':0,'c':None})
        self.assertEqual(percentile_rank({}),{})
    def test_large_filter_small_and_large_repositories(self):
        for n,threshold in [(3,15),(20,15),(150,15),(151,16),(1000,100)]:
            self.assertEqual(large_change_threshold(n),threshold)
    def test_bf_entropy_concentration_and_empty(self):
        owners=[{'contributor_id':i,'name':str(i),'cnt':n} for i,n in enumerate([8,2])]
        value=ns['_ownership_analysis'](owners)
        self.assertEqual(value['bus_factor'],1)
        self.assertEqual(value['top_owner_share'],.8)
        self.assertAlmostEqual(value['entropy_normalized'],value['entropy'])
        self.assertIsNone(ns['_ownership_analysis']([])['entropy'])
        self.assertTrue(ns['_ownership_analysis']([owners[1]])['low_support'])
    def test_fixed_window_future_deleted_other_repo_and_no_refactor_reset(self):
        db=DB()
        try:
            db.change(1,1,'2026-04-04',owner=2) # inclusive start
            db.change(2,1,'2026-09-01','refactor')
            db.change(3,1,'2026-09-02','bugfix',owner=None)
            db.change(4,1,'2026-04-03') # outside
            db.change(5,1,'2026-10-01') # exclusive end
            db.change(6,2,'2026-09-02')
            db.change(7,4,'2026-09-02') # deleted
            db.change(8,5,'2026-09-02',rid=2)
            rows=ns['_compute_risk_scores'](db,1)
            self.assertEqual({r['file_id'] for r in rows},{1,2})
            a=next(r for r in rows if r['file_id']==1)
            self.assertEqual(a['change_freq'],3)
            self.assertEqual(a['bugfix_count'],1)
            self.assertEqual(a['contributor_count'],2)
            self.assertEqual(a['identified_change_count'],2)
            self.assertAlmostEqual(a['unidentified_share'],1/3)
            self.assertEqual(a['maintenance_priority'],1)
            self.assertIsNone(a['structural_risk'])
        finally: db.conn.close()
    def test_unidentified_changes_keep_ownership_unknown(self):
        db=DB()
        try:
            db.change(1,1,'2026-09-01',owner=None)
            row=ns['_compute_risk_scores'](db,1)[0]
            self.assertIsNone(row['entropy'])
            self.assertIsNone(row['bus_factor'])
            self.assertEqual(row['unidentified_share'],1)
            self.assertEqual(row['maintenance_priority'],0)
        finally:db.conn.close()
    def test_missing_churn_is_not_zero_or_a_complete_score(self):
        db=DB()
        try:
            db.change(1,1,'2026-09-01',lines=None)
            db.change(2,2,'2026-09-01')
            rows=ns['get_risk_scores'](1,db=db)
            missing=next(f for f in rows['files'] if f['file_id']==1)
            self.assertIsNone(missing['churn'])
            self.assertIsNone(missing['risk_score'])
            self.assertEqual(missing['evidence_status'],'metrics_missing')
            self.assertEqual(rows['summary']['avg_risk_score'],0)
        finally:db.conn.close()
    def test_tree_and_bf_share_ownership_and_missing_values(self):
        db=DB()
        try:
            db.change(1,1,'2026-09-01',owner=1)
            db.change(2,1,'2026-09-02',owner=None)
            tree=ns['get_repo_tree'](1,db=db)
            a=next(f for f in tree['files'] if f['file_id']==1)
            missing=next(f for f in tree['files'] if f['file_id']==3)
            bf=ns['get_bus_factor_analysis'](1,db=db)
            self.assertEqual(a['bus_factor'],bf['files'][0]['bf80'])
            self.assertEqual(a['unidentified_share'],.5)
            self.assertEqual(bf['files'][0]['total_change_count'],2)
            self.assertEqual(bf['files'][0]['observation_count'],1)
            self.assertEqual(bf['summary']['file_bf80_median'],1)
            self.assertIsNone(missing['risk_score'])
            self.assertIsNone(missing['entropy_normalized'])
            self.assertEqual(missing['evidence_status'],'no_history')
        finally:db.conn.close()
    def test_explicit_window_is_respected(self):
        db=DB()
        try:
            db.info['analysis_window_start']=datetime(2026,9,1,tzinfo=timezone.utc)
            db.change(1,1,'2026-08-31')
            db.change(2,1,'2026-09-01')
            self.assertEqual(ns['_compute_risk_scores'](db,1)[0]['change_freq'],1)
        finally:db.conn.close()


if __name__=='__main__':unittest.main()
