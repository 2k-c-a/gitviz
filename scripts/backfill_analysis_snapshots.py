"""기존 로컬 클론/수집 DB에서 결과 저장. 기본은 dry-run, --apply로 확정.

원본 수집 데이터와 과거 작업을 갱신하지 않는다. private/접근 불가 저장소는 건너뛴다.
기존 이름 기반 클론은 origin 검증 후 읽기만 하며 새 수집기는 ID 경로를 사용한다.
"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse, json, os
from sqlalchemy import text
from git import Repo
from fastapi import HTTPException
from app.core.database import SessionLocal
from app.models.repository import Repository
from app.models.analysis_snapshot import AnalysisSnapshot
from app.services.repository_access import github_metadata, canonical_github_url, remember_access
from app.services.analysis_snapshots import build_snapshot, analysis_parameters, payload_digest, canonical_payload
from app.collectors.git.clone_manager import REPO_ROOT, repository_path, snapshot_target


def run(apply=False, ids=None, offline=False):
    report=[]
    with SessionLocal() as listing:
        rows=listing.query(Repository.id).order_by(Repository.id).all()
    for (rid,) in rows:
        if ids and rid not in ids:continue
        with SessionLocal() as db:
            db.connection(execution_options={'isolation_level':'REPEATABLE READ'})
            db.execute(text("SET LOCAL statement_timeout = '120s'"))
            repo=db.get(Repository,rid)
            try:
                print(f'processing repository_id={rid}',flush=True)
                if offline:
                    # 외부 권한을 주장하거나 공개 상태를 변경하지 않는다. HTTP 조회는 별도로
                    # 현재 GitHub 인증을 확인하므로 private/unknown 데이터도 공개되지 않는다.
                    metadata={'canonical_url':canonical_github_url(repo.url),'private':False}
                else:
                    metadata=github_metadata(repo.url,os.getenv('GITHUB_TOKEN'),public_fallback=True)
                if metadata['private']:
                    report.append({'repository_id':rid,'status':'private_skipped'});continue
                if not offline:
                    if repo.github_id is None:
                        report.append({'repository_id':rid,'status':'unverified_legacy_identity_register_again'});continue
                    if repo.github_id != metadata['id']:
                        report.append({'repository_id':rid,'status':'github_identity_mismatch'});continue
                    remember_access(db,repo,metadata,None)
                existing=db.query(AnalysisSnapshot).filter_by(repository_id=rid).order_by(AnalysisSnapshot.id.desc()).first()
                if existing:
                    digest=payload_digest(existing.payload)
                    if digest != existing.payload_sha256:
                        replacement=AnalysisSnapshot(repository_id=rid,target_sha=existing.target_sha,
                            analyzed_at=existing.analyzed_at,window_start=existing.window_start,window_end=existing.window_end,
                            formula_version=existing.formula_version,parameters={**existing.parameters,'hash_format':'canonical-json-numbers-v1','supersedes_snapshot_id':existing.id},
                            payload=canonical_payload(existing.payload),payload_sha256=digest)
                        if apply:db.add(replacement);db.flush()
                        existing=replacement
                    if apply:db.commit()
                    report.append({'repository_id':rid,'status':'already_saved','snapshot_id':existing.id});continue
                paths=[repository_path(rid),REPO_ROOT/repo.name]
                local=next((p for p in paths if p.is_dir() and p.resolve().parent==REPO_ROOT.resolve()),None)
                if local is None or canonical_github_url(Repo(local).remotes.origin.url)!=metadata['canonical_url']:
                    if apply:db.commit()
                    report.append({'repository_id':rid,'status':'verified_clone_required'});continue
                parameters=analysis_parameters()
                parameters['source_mode']='existing_verified_local_clone_and_collected_database'
                sha,live,reachable=snapshot_target(local)
                # HEAD보다 DB가 덜 수집됐어도 숨기지 않는다: quality에 SHA 기준 파일 수집률 표시.
                snapshot=build_snapshot(db,repo,None,target_sha=sha,live_paths=live,reachable_shas=reachable,
                                        parameters=parameters,persist=apply)
                quality=snapshot.payload['data_quality']
                if apply:db.commit()
                report.append({'repository_id':rid,'status':'saved' if apply else 'dry_run',
                               'snapshot_id':snapshot.id,'target_sha':sha,'data_quality':quality})
            except HTTPException as exc:
                db.rollback()
                if exc.status_code not in {404,401}:raise
                report.append({'repository_id':rid,'status':'access_unavailable','http_status':exc.status_code})
    print(json.dumps(report,ensure_ascii=False,default=str,indent=2))


def repair_hashes(apply=False):
    """수집/공개 여부/권한을 변경하지 않고 기존 저장 결과만 새 형식으로 복제한다."""
    report=[]
    with SessionLocal() as db:
        db.connection(execution_options={'isolation_level':'REPEATABLE READ'})
        ids=db.query(AnalysisSnapshot.repository_id).distinct().all()
        for (rid,) in ids:
            existing=db.query(AnalysisSnapshot).filter_by(repository_id=rid).order_by(AnalysisSnapshot.id.desc()).first()
            digest=payload_digest(existing.payload)
            if digest == existing.payload_sha256 and existing.parameters.get('hash_format')=='canonical-json-numbers-v1':continue
            replacement=AnalysisSnapshot(repository_id=rid,target_sha=existing.target_sha,
                analyzed_at=existing.analyzed_at,window_start=existing.window_start,window_end=existing.window_end,
                formula_version=existing.formula_version,parameters={**existing.parameters,'hash_format':'canonical-json-numbers-v1','supersedes_snapshot_id':existing.id},
                payload=canonical_payload(existing.payload),payload_sha256=digest)
            if apply:db.add(replacement);db.flush()
            report.append({'repository_id':rid,'old_snapshot':existing.id,'new_snapshot':replacement.id})
        if apply:db.commit()
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true')
    parser.add_argument('--repair-hashes',action='store_true')
    parser.add_argument('--offline-existing-data',action='store_true',help='로컬 origin만 검증. 공개 여부/권한 정보는 변경하지 않음')
    parser.add_argument('--repository-id',type=int,action='append')
    args=parser.parse_args()
    if args.repair_hashes:repair_hashes(args.apply)
    else:run(args.apply,args.repository_id,args.offline_existing_data)
