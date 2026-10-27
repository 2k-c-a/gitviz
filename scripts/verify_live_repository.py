"""실제 공개 저장소를 격리 DB에서 수집/분석하고 Git 이력과 대조한다.

GITVIZ_TEST_DATABASE_URL=.../gitviz_test_live python scripts/verify_live_repository.py
GitHub 데이터는 대역 없이 호출한다. OAuth 세션과 큐 transport만 테스트 환경이다.
"""
import os
import sys
import json
import base64
import math
import secrets
import argparse
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sqlalchemy.engine import make_url
url=os.environ.get('GITVIZ_TEST_DATABASE_URL','')
if not url or not (make_url(url).database or '').startswith('gitviz_test'):
    raise RuntimeError('gitviz_test로 시작하는 격리 DB를 지정하세요.')
os.environ['DATABASE_URL']=url
os.environ['DATABASE_URL_ASYNC']=url.replace('postgresql://','postgresql+asyncpg://',1)
os.environ['SESSION_SECRET_KEY']=secrets.token_hex(32)
os.environ['IDENTITY_STAGE2_LIMIT']='0'  # 비인증 Search 한도 소모 방지. 보고서에 명시.
os.environ['REDIS_URL']='memory://'

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from app.core.database import SessionLocal
from app.core.celery_app import celery
from app.models import Repository, AnalysisJob, AnalysisSnapshot
from app.models.user import User
from app.services.analysis_snapshots import utc_datetime
from app.collectors.git import clone_manager
from app.collectors.github import github_client, graphql_pr_collector
from git import Repo

clone_manager.REPO_ROOT=ROOT/'.validation'/'live-clones'
celery.conf.update(task_always_eager=True,task_eager_propagates=True,
                   task_store_eager_result=False,result_backend='cache+memory://',broker_url='memory://')

# 실검증이 인증 부족으로 한 시간 대기하지 않도록 transport의 대기만 제한한다.
# 외부 데이터 응답을 대역으로 바꾸지는 않는다.
import time as real_time
class BoundedTime:
    spent=0
    def __getattr__(self,name):return getattr(real_time,name)
    def sleep(self,seconds):
        if seconds>5 or self.spent+seconds>15:
            raise RuntimeError('검증 대기 한도 초과: 인증/API 한도를 확인하세요.')
        self.spent+=seconds
        return real_time.sleep(seconds)
github_client.time=BoundedTime()
graphql_pr_collector.time=BoundedTime()
original_query=graphql_pr_collector.GraphQLClient.query
def authenticated_query(self,*args,**kwargs):
    if not self.token:
        raise RuntimeError('GraphQL 인증 토큰이 없어 PR 상세 검증을 진행할 수 없습니다.')
    return original_query(self,*args,**kwargs)
graphql_pr_collector.GraphQLClient.query=authenticated_query


def independent_changes(git, shas):
    expected={}
    for sha in shas:
        records=git.git.diff_tree('--root','--no-commit-id','--numstat','-r','-M','-z',sha).split('\0')
        i=0
        while i<len(records):
            record=records[i];i+=1
            if not record:continue
            parts=record.split('\t',2)
            if len(parts)!=3:continue
            adds,deletes,path=parts
            if not path:  # -z rename는 old/new path를 별도 레코드로 보낸다.
                i+=1;path=records[i];i+=1
            expected[(sha,path)]=(0 if adds=='-' else int(adds),0 if deletes=='-' else int(deletes))
    return expected


def main(names,output):
    command.upgrade(Config(str(ROOT/'alembic.ini')),'head')
    from app.main import app
    checks=[];repos=[]
    def check(label,condition,details=None):
        checks.append({'check':label,'passed':bool(condition),'details':details})
        print(('PASS ' if condition else 'FAIL ')+label,flush=True)
        return bool(condition)
    uid=uuid4()
    with SessionLocal() as db:
        db.execute(User.__table__.insert().values(id=uid,github_id=-(int(uid)%10**12),github_login='validation-session'))
        db.commit()
    cookie=TimestampSigner(os.environ['SESSION_SECRET_KEY']).sign(base64.b64encode(json.dumps({'user_id':str(uid)}).encode())).decode()
    with TestClient(app) as client:
        client.cookies.set('gitviz_session',cookie)
        for name in names:
            record={'requested_repo':name}
            repos.append(record)
            fixed_end=datetime.now(timezone.utc).isoformat()
            response=client.post('/repositories',params={'github_url':'https://github.com/'+name,'analysis_end':fixed_end})
            if not check(name+' POST analysis',response.status_code==200,{'status':response.status_code}):
                record['error']=response.text[:300];continue
            rid=response.json()['repository_id'];jid=response.json()['job_id']
            record.update(repository_id=rid,job_id=jid,window_end=fixed_end)
            with SessionLocal() as db:
                job=db.get(AnalysisJob,jid)
                saved=db.query(AnalysisSnapshot).filter_by(analysis_job_id=jid).first()
                record.update(job_status=job.status,error=job.error_message,snapshot_id=saved.id if saved else None)
                ready=check(name+' job done + saved snapshot',job.status=='done' and saved is not None,record.copy())
                if not ready:continue
                sid=saved.id;sha=saved.target_sha
                record['target_sha']=sha
                check(name+' completion points to snapshot',(job.parameters or {}).get('snapshot_id')==sid)
                git=Repo(clone_manager.repository_path(rid))
                reachable=git.git.rev_list(sha).splitlines()
                eligible=[s for s in reachable if git.commit(s).committed_datetime<utc_datetime(fixed_end)]
                raw=db.execute(text('SELECT sha FROM commits WHERE repository_id=:rid AND on_default_branch IS TRUE'),{'rid':rid}).scalars().all()
                check(name+' default-branch SHA set',set(raw)==set(git.git.rev_list('HEAD').splitlines()))
                expected=independent_changes(git,git.git.rev_list('--all').splitlines())
                actual={(r.sha,r.path):(r.additions,r.deletions) for r in db.execute(text('''
                    SELECT c.sha,f.path,cf.additions,cf.deletions FROM commit_file_changes cf
                    JOIN commits c ON c.id=cf.commit_id JOIN files f ON f.id=cf.file_id WHERE c.repository_id=:rid
                '''),{'rid':rid})}
                check(name+' file changes and churn equal independent Git diff',actual==expected,
                      {'git_pairs':len(expected),'db_pairs':len(actual),'missing':len(expected.keys()-actual.keys()),'extra':len(actual.keys()-expected.keys())})
                target_paths={p for p in git.git.ls_tree('-r','-z','--name-only',sha).split('\0') if p}
                record.update(git_commit_count=len(eligible),target_file_count=len(target_paths),file_change_count=len(actual),data_quality=saved.payload['data_quality'])
                counts_before=dict(db.execute(text('''SELECT
                    (SELECT count(*) FROM commits WHERE repository_id=:rid) AS commits,
                    (SELECT count(*) FROM commit_file_changes cf JOIN commits c ON c.id=cf.commit_id WHERE c.repository_id=:rid) AS changes,
                    (SELECT count(*) FROM files WHERE repository_id=:rid) AS files'''),{'rid':rid}).mappings().one())
                digest=saved.payload_sha256
                own_rows=db.execute(text('''SELECT f.path,c.sha,c.committed_at,c.commit_type,c.contributor_id
                    FROM commit_file_changes cf JOIN commits c ON c.id=cf.commit_id JOIN files f ON f.id=cf.file_id
                    WHERE c.repository_id=:rid'''),{'rid':rid}).mappings().all()
            def get(route):
                r=client.get(f'/repositories/{rid}/analytics/{route}',params={'snapshot_id':sid})
                check(name+' GET '+route,r.status_code==200,{'status':r.status_code})
                return r.json() if r.status_code==200 else None
            summary=get('summary');tree=get('tree');risk=get('risk-scores');bf=get('bus-factor')
            network=get('network');coupling=get('coupling');get('timeline');get('contributor-heatmap')
            if summary:check(name+' summary commit count',summary['total_commits']==len(eligible))
            if tree:
                check(name+' target SHA tree paths',{f['path'] for f in tree['files']}==target_paths)
                risk_by={f['file_id']:f for f in risk['files']} if risk else {}
                bf_by={f['file_id']:f for f in bf['files']} if bf else {}
                observed=[f for f in tree['files'] if f['risk_score'] is not None]
                check(name+' risk/BF endpoint consistency',all(f['file_id'] in risk_by and f['file_id'] in bf_by and f['risk_score']==risk_by[f['file_id']]['risk_score'] and f['bus_factor']==bf_by[f['file_id']]['bus_factor'] for f in observed))
                groups=defaultdict(list)
                for row in own_rows:
                    if row['sha'] in eligible:groups[row['path']].append(row)
                independent_bf={}
                for path,rows in groups.items():
                    refactors=[r['committed_at'] for r in rows if r['commit_type']=='refactor']
                    start=max(refactors) if refactors else min(r['committed_at'] for r in rows)
                    owners=Counter(r['contributor_id'] for r in rows if r['committed_at']>=start and r['contributor_id'] is not None)
                    total=sum(owners.values());acc=0
                    for n,value in enumerate(sorted(owners.values(),reverse=True),1):
                        acc+=value
                        if 5*acc>=4*total:independent_bf[path]=n;break
                check(name+' BF independent cumulative-80pct count',all(f['bus_factor']==independent_bf.get(f['path']) for f in tree['files']))
                if tree['files']:get(f"files/{tree['files'][0]['file_id']}/detail")
            if network:check(name+' network endpoints valid',all(e['source'] in {n['id'] for n in network['nodes']} and e['target'] in {n['id'] for n in network['nodes']} for e in network['edges']))
            quality=record['data_quality']
            # squash/rebase의 원본 PR SHA는 기본 브랜치에서 도달하지 않을 수 있다.
            # PR 커밋 연결 비율은 별도 한계로 보고하고 파일 상세 수집률을 검사한다.
            check(name+' PR file detail coverage',quality['prs_with_files']==quality['merged_pr_count'],quality)
            first=client.get(f'/repositories/{rid}/analytics/summary',params={'snapshot_id':sid}).json()
            check(name+' repeated pinned summary identical',first==summary)
            again=client.post(f'/repositories/{rid}/reanalyze',params={'analysis_end':fixed_end})
            check(name+' reanalysis request',again.status_code==200,{'status':again.status_code})
            with SessionLocal() as db:
                job2=db.query(AnalysisJob).filter_by(repository_id=rid).order_by(AnalysisJob.id.desc()).first()
                saved2=db.query(AnalysisSnapshot).filter_by(analysis_job_id=job2.id).first()
                record['reanalysis_job_status']=job2.status
                if check(name+' reanalysis done',job2.status=='done' and saved2 is not None):
                    check(name+' fixed-input payload hash identical',saved2.payload_sha256==digest)
                    counts_after=dict(db.execute(text('''SELECT
                        (SELECT count(*) FROM commits WHERE repository_id=:rid) AS commits,
                        (SELECT count(*) FROM commit_file_changes cf JOIN commits c ON c.id=cf.commit_id WHERE c.repository_id=:rid) AS changes,
                        (SELECT count(*) FROM files WHERE repository_id=:rid) AS files'''),{'rid':rid}).mappings().one())
                    check(name+' reanalysis inserts no duplicate raw rows',counts_before==counts_after,counts_after)
                    compare=client.get(f'/repositories/{rid}/snapshots/compare',params={'before':sid,'after':saved2.id})
                    check(name+' identical snapshot comparison',compare.status_code==200 and compare.json()['changes']==[])
    result={'executed_at':datetime.now(timezone.utc).isoformat(),'mode':{'github':'live REST APIs; GraphQL blocked without configured token','git':'live clone','session':'signed test fixture, OAuth callback not exercised','queue':'Celery eager transport, Redis not exercised','identity_stage2':'disabled to limit unauthenticated API usage','wait_budget':'maximum 5s per sleep and 15s total per collector'},'repositories':repos,'checks':checks}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"RESULT {sum(x['passed'] for x in checks)}/{len(checks)} checks passed; {output}",flush=True)
    return all(x['passed'] for x in checks)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--repos',nargs='+',default=['octocat/git-consortium','navdeep-G/samplemod'])
    from scripts.validation_reports import new_report_path
    parser.add_argument('--output',type=Path,default=new_report_path('live-repository'))
    args=parser.parse_args()
    raise SystemExit(0 if main(args.repos,args.output) else 1)
