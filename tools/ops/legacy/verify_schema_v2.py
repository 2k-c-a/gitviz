"""ERD v2 마이그레이션 검증: 건수 보존, 타입 전환, 백필 결과"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from sqlalchemy import text
from app.core.database import SessionLocal

db = SessionLocal()
db.execute(text("SET statement_timeout = 0"))

print("=== 건수 (마이그레이션 전과 동일해야 함) ===")
for t in ["commits", "files", "contributors", "pull_requests", "releases",
          "commit_file_changes", "pr_commits", "pr_reviews", "pull_request_files",
          "analysis_jobs", "contributor_aliases"]:
    n = db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
    print(f"  {t}: {n}")

print("\n=== 날짜 컬럼 타입 ===")
rows = db.execute(text("""
    SELECT table_name, column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND column_name IN ('committed_at','merged_at','opened_at','submitted_at','published_at')
    ORDER BY table_name, column_name
""")).fetchall()
for r in rows:
    print(f"  {r[0]}.{r[1]}: {r[2]}")

print("\n=== repositories ===")
r = db.execute(text("SELECT id, url, name, platform, analysis_window_months FROM repositories")).fetchall()
for row in r:
    print(f"  {row}")

print("\n=== analysis_jobs status 값 ===")
for row in db.execute(text("SELECT status, COUNT(*) FROM analysis_jobs GROUP BY status")).fetchall():
    print(f"  {row[0]}: {row[1]}")

print("\n=== 백필 결과 ===")
print("  commits.changed_file_count IS NULL:",
      db.execute(text("SELECT COUNT(*) FROM commits WHERE changed_file_count IS NULL")).scalar())
print("  pull_requests.review_duration_hours IS NULL (merged):",
      db.execute(text("SELECT COUNT(*) FROM pull_requests WHERE merged_at IS NOT NULL AND review_duration_hours IS NULL")).scalar())
print("  committed_at NULL:",
      db.execute(text("SELECT COUNT(*) FROM commits WHERE committed_at IS NULL")).scalar())
print("  committed_at min/max:",
      db.execute(text("SELECT MIN(committed_at), MAX(committed_at) FROM commits")).fetchone())

db.close()
