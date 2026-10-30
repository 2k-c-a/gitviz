"""검증용 repository 삭제 (자식 → 부모 순, 단일 트랜잭션).

사용법: python tools/ops/cleanup_test_repo.py <repository_id>
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text
from app.core.database import SessionLocal

RID = int(sys.argv[1])
assert RID != 1, "repo 1(react 정식 데이터)은 삭제 금지"

db = SessionLocal()
db.execute(text("SET statement_timeout = 0"))

deletes = [
    ("commit_file_changes", """
        DELETE FROM commit_file_changes
        WHERE commit_id IN (SELECT id FROM commits WHERE repository_id=:r)
           OR file_id   IN (SELECT id FROM files   WHERE repository_id=:r)"""),
    ("pr_commits", """
        DELETE FROM pr_commits
        WHERE pull_request_id IN (SELECT id FROM pull_requests WHERE repository_id=:r)
           OR commit_id       IN (SELECT id FROM commits       WHERE repository_id=:r)"""),
    ("pr_reviews", """
        DELETE FROM pr_reviews
        WHERE pull_request_id IN (SELECT id FROM pull_requests WHERE repository_id=:r)"""),
    ("pull_request_files", """
        DELETE FROM pull_request_files
        WHERE pull_request_id IN (SELECT id FROM pull_requests WHERE repository_id=:r)
           OR file_id         IN (SELECT id FROM files         WHERE repository_id=:r)"""),
    ("pull_requests", "DELETE FROM pull_requests WHERE repository_id=:r"),
    ("releases", "DELETE FROM releases WHERE repository_id=:r"),
    ("contributor_aliases", """
        DELETE FROM contributor_aliases
        WHERE contributor_id IN (SELECT id FROM contributors WHERE repository_id=:r)"""),
    ("commits", "DELETE FROM commits WHERE repository_id=:r"),
    ("files", "DELETE FROM files WHERE repository_id=:r"),
    ("contributors", "DELETE FROM contributors WHERE repository_id=:r"),
    ("analysis_jobs", "DELETE FROM analysis_jobs WHERE repository_id=:r"),
    ("repositories", "DELETE FROM repositories WHERE id=:r"),
]
try:
    for label, q in deletes:
        n = db.execute(text(q), {"r": RID}).rowcount
        print(f"  {label}: {n} rows")
    db.commit()
    print("\nCOMMIT 완료")
except Exception:
    db.rollback()
    print("\nROLLBACK")
    raise

remaining = db.execute(text("SELECT id, url FROM repositories ORDER BY id")).fetchall()
print("남은 repositories:", [(r[0], r[1].split("/")[-1]) for r in remaining])
db.close()
