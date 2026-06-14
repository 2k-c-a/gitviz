"""repository_id별 데이터 현황 확인 (삭제 전 dry-run)"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import SessionLocal

db = SessionLocal()

repos = db.execute(
    text("SELECT id, url, name, platform FROM repositories ORDER BY id")
).fetchall()
print("=== repositories ===")
for r in repos:
    print(f"  id={r[0]}  url={r[1]}  name={r[2]}  platform={r[3]}")

count_queries = {
    "commits": "SELECT repository_id, COUNT(*) FROM commits GROUP BY repository_id ORDER BY repository_id",
    "files": "SELECT repository_id, COUNT(*) FROM files GROUP BY repository_id ORDER BY repository_id",
    "contributors": "SELECT repository_id, COUNT(*) FROM contributors GROUP BY repository_id ORDER BY repository_id",
    "pull_requests": "SELECT repository_id, COUNT(*) FROM pull_requests GROUP BY repository_id ORDER BY repository_id",
    "releases": "SELECT repository_id, COUNT(*) FROM releases GROUP BY repository_id ORDER BY repository_id",
    "analysis_jobs": "SELECT repository_id, COUNT(*) FROM analysis_jobs GROUP BY repository_id ORDER BY repository_id",
    "commit_file_changes": """
        SELECT c.repository_id, COUNT(*) FROM commit_file_changes cfc
        JOIN commits c ON c.id = cfc.commit_id
        GROUP BY c.repository_id ORDER BY c.repository_id""",
    "pr_commits": """
        SELECT pr.repository_id, COUNT(*) FROM pr_commits pc
        JOIN pull_requests pr ON pr.id = pc.pull_request_id
        GROUP BY pr.repository_id ORDER BY pr.repository_id""",
    "pr_reviews": """
        SELECT pr.repository_id, COUNT(*) FROM pr_reviews rv
        JOIN pull_requests pr ON pr.id = rv.pull_request_id
        GROUP BY pr.repository_id ORDER BY pr.repository_id""",
    "pull_request_files": """
        SELECT pr.repository_id, COUNT(*) FROM pull_request_files pf
        JOIN pull_requests pr ON pr.id = pf.pull_request_id
        GROUP BY pr.repository_id ORDER BY pr.repository_id""",
}

for table, q in count_queries.items():
    rows = db.execute(text(q)).fetchall()
    counts = ", ".join(f"repo{r[0]}={r[1]}" for r in rows)
    print(f"{table:22s}: {counts or '(empty)'}")

db.close()
