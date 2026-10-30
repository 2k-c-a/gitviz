"""ProgressReporter 동작 검증: 임시 job 만들어 갱신/rate_limited/재개/완료 흐름 확인 후 정리"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text
from app.core.database import SessionLocal
from app.tasks.progress import ProgressReporter

db = SessionLocal()
# 임시 job 생성 (repository_id=1)
job_id = db.execute(text("""
    INSERT INTO analysis_jobs (repository_id, status, triggered_by)
    VALUES (1, 'running', 'progress_test') RETURNING id
""")).scalar()
db.commit()
print("temp job_id:", job_id)


def show(label):
    row = db.execute(text(
        "SELECT status, progress_detail FROM analysis_jobs WHERE id=:i"
    ), {"i": job_id}).fetchone()
    db.commit()  # 다른 세션 커밋을 보기 위해 트랜잭션 갱신
    print(f"  [{label}] status={row[0]} detail={row[1]}")


p = ProgressReporter(job_id)
p.update("커밋 수집 중...", commits_collected=100)
show("after update")

p.on_rate_limit(45)
show("after rate_limit")

p.on_resume()
show("after resume")

p.update("분석 완료", prs_collected=38)
show("after final update")

# 정리
db.execute(text("DELETE FROM analysis_jobs WHERE id=:i"), {"i": job_id})
db.commit()
print("temp job 삭제 완료")
db.close()
