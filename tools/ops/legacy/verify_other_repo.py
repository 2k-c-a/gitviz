"""타 레포 검증: 전체 수집 + 증분 재실행 확인.

API(create_repository) 흐름을 모사해 Repository/AnalysisJob 행을 만들고,
analyze_repository 태스크를 동기로 호출(.delay 아님)해 파이프라인 전체를 실행한다.
1회차 = 전체 수집, 2회차 = 같은 URL 증분 (중복 없이 신규만 추가되는지 확인).

사용법: python tools/ops/legacy/verify_other_repo.py <github_url> [--run2]
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from sqlalchemy import text
from app.core.database import SessionLocal
from app.models.repository import Repository
from app.models.analysis_job import AnalysisJob
from app.tasks.analyze_repository import analyze_repository

URL = sys.argv[1] if len(sys.argv) > 1 else "https://github.com/pallets/itsdangerous"
URL = URL.rstrip("/").removesuffix(".git")


def counts(db, repo_id):
    q = {
        "commits": "SELECT COUNT(*) FROM commits WHERE repository_id=:r",
        "files": "SELECT COUNT(*) FROM files WHERE repository_id=:r",
        "contributors": "SELECT COUNT(*) FROM contributors WHERE repository_id=:r",
        "pull_requests": "SELECT COUNT(*) FROM pull_requests WHERE repository_id=:r",
        "releases": "SELECT COUNT(*) FROM releases WHERE repository_id=:r",
        "cfc": """SELECT COUNT(*) FROM commit_file_changes cfc
                  JOIN commits c ON c.id=cfc.commit_id WHERE c.repository_id=:r""",
        "pr_commits": """SELECT COUNT(*) FROM pr_commits pc
                  JOIN pull_requests pr ON pr.id=pc.pull_request_id WHERE pr.repository_id=:r""",
        "pr_reviews": """SELECT COUNT(*) FROM pr_reviews rv
                  JOIN pull_requests pr ON pr.id=rv.pull_request_id WHERE pr.repository_id=:r""",
        "commits_linked": "SELECT COUNT(*) FROM commits WHERE repository_id=:r AND contributor_id IS NOT NULL",
        "auto": "SELECT COUNT(*) FROM contributors WHERE repository_id=:r AND resolution_status='auto'",
    }
    return {k: db.execute(text(v), {"r": repo_id}).scalar() for k, v in q.items()}


def get_or_create(db):
    repo = db.query(Repository).filter(Repository.url == URL).order_by(Repository.id).first()
    if repo:
        print(f"기존 repo 재사용: id={repo.id}")
    else:
        repo = Repository(url=URL, name=URL.split("/")[-1], platform="github")
        db.add(repo)
        db.commit()
        db.refresh(repo)
        print(f"신규 repo 생성: id={repo.id}")
    db.add(AnalysisJob(repository_id=repo.id, status="pending", triggered_by="verify"))
    db.commit()
    return repo.id


db = SessionLocal()
db.execute(text("SET statement_timeout = 0"))
repo_id = get_or_create(db)
repo_count = db.execute(text("SELECT COUNT(*) FROM repositories WHERE url=:u"), {"u": URL}).scalar()
print(f"URL '{URL}' 의 repository 행 수: {repo_count} (1이어야 정상)")
db.close()

print(f"\n=== analyze_repository({repo_id}) 동기 실행 시작 ===")
analyze_repository(repo_id)
print("=== 실행 종료 ===\n")

db = SessionLocal()
c = counts(db, repo_id)
print("=== 수집 결과 ===")
for k, v in c.items():
    print(f"  {k}: {v}")
job = (db.query(AnalysisJob).filter(AnalysisJob.repository_id == repo_id)
       .order_by(AnalysisJob.id.desc()).first())
print(f"  job.status={job.status} last_commit_sha={job.last_commit_sha} "
      f"completed_at={job.completed_at}")
db.close()
