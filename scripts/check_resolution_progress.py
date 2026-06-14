"""백그라운드 resolution/백필 진행 상황 확인"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import SessionLocal

db = SessionLocal()
print("author_login filled:",
      db.execute(text("SELECT COUNT(*) FROM pull_requests WHERE author_login IS NOT NULL")).scalar(),
      "/", db.execute(text("SELECT COUNT(*) FROM pull_requests")).scalar())
print("contributors auto:",
      db.execute(text("SELECT COUNT(*) FROM contributors WHERE resolution_status = 'auto'")).scalar(),
      "/", db.execute(text("SELECT COUNT(*) FROM contributors")).scalar())
print("aliases by rule:",
      db.execute(text("""
          SELECT COALESCE(resolution_rule, '(none)'), COUNT(*)
          FROM contributor_aliases GROUP BY resolution_rule
      """)).fetchall())
print("pr_authors linked:",
      db.execute(text("SELECT COUNT(*) FROM pull_requests WHERE author_contributor_id IS NOT NULL")).scalar())

total, auto = db.execute(text("""
    SELECT COUNT(*), COUNT(*) FILTER (WHERE c.resolution_status = 'auto')
    FROM commits cm JOIN contributors c ON c.id = cm.contributor_id
    WHERE cm.repository_id = 1
""")).fetchone()
print(f"commit-weighted coverage: {auto}/{total} ({auto/total*100:.1f}%)")
db.close()
