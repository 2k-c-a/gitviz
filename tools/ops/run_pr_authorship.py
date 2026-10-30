"""PR 작성자 기반 resolution 실행 + FK 재연결 + 통계"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text
from app.core.database import SessionLocal
from app.collectors.identity.resolver import (
    stage_pr_authorship, link_commits_by_alias,
    link_pr_reviews_by_login, link_pr_authors_by_login, resolution_stats,
)

REPO_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 1

db = SessionLocal()
db.execute(text("SET statement_timeout = 0"))

print("stage_pr_authorship:", stage_pr_authorship(db, REPO_ID))
print("commits alias 연결:", link_commits_by_alias(db, REPO_ID))
print("pr_reviews 연결:", link_pr_reviews_by_login(db, REPO_ID))
print("pr_authors 연결:", link_pr_authors_by_login(db, REPO_ID))

s = resolution_stats(db, REPO_ID)
print(f"\ncontributors: total={s['total']} auto={s['auto']} unresolved={s['unresolved']}"
      f" (unresolved {s['unresolved_ratio']*100:.1f}%)")

db.close()
