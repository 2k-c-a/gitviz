"""실제 운영 경로(.delay → Redis → worker)로 수집 태스크 투입.

API의 create_repository 흐름을 모사해 Repository/AnalysisJob 행을 만들고
analyze_repository.delay()로 큐에 넣는다. worker가 비동기로 실행한다.

사용법: python scripts/trigger_async.py <github_url>
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.models.repository import Repository
from app.models.analysis_job import AnalysisJob
from app.tasks.analyze_repository import analyze_repository

URL = sys.argv[1] if len(sys.argv) > 1 else "https://github.com/pallets/itsdangerous"
URL = URL.rstrip("/").removesuffix(".git")

db = SessionLocal()
repo = db.query(Repository).filter(Repository.url == URL).order_by(Repository.id).first()
if repo:
    print(f"기존 repo 재사용: id={repo.id}")
else:
    repo = Repository(url=URL, name=URL.split("/")[-1], platform="github")
    db.add(repo)
    db.commit()
    db.refresh(repo)
    print(f"신규 repo 생성: id={repo.id}")

job = AnalysisJob(repository_id=repo.id, status="pending", triggered_by="async_test")
db.add(job)
db.commit()
db.refresh(job)
repo_id, job_id = repo.id, job.id
db.close()

result = analyze_repository.delay(repo_id)
print(f"enqueued: repo_id={repo_id} job_id={job_id} task_id={result.id}")
