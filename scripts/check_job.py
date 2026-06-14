"""특정 job의 상태/진행도 확인"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import SessionLocal

job_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
db = SessionLocal()
if job_id:
    row = db.execute(text(
        "SELECT id, repository_id, status, progress_detail, started_at, completed_at "
        "FROM analysis_jobs WHERE id = :i"
    ), {"i": job_id}).fetchone()
else:
    row = db.execute(text(
        "SELECT id, repository_id, status, progress_detail, started_at, completed_at "
        "FROM analysis_jobs ORDER BY id DESC LIMIT 1"
    )).fetchone()
print("job:", row[0], "repo:", row[1], "status:", row[2])
print("progress:", row[3])
print("started:", row[4], "completed:", row[5])
db.close()
