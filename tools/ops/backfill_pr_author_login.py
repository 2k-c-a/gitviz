"""기존 pull_requests의 author_login 백필 (컬럼 추가 전에 수집된 PR 대상).

REST PR 목록을 다시 페이지네이션하며 pr_number → author_login 매핑만 갱신.
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text
from app.core.database import SessionLocal
from app.collectors.github.github_client import GitHubClient
from app.collectors.identity.resolver import link_pr_authors_by_login

REPO_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 1

db = SessionLocal()
db.execute(text("SET statement_timeout = 0"))

url = db.execute(text("SELECT url FROM repositories WHERE id = :r"), {"r": REPO_ID}).scalar()
owner, repo = url.rstrip("/").split("/")[-2:]

client = GitHubClient()
api_url = f"{client.BASE_URL}/repos/{owner}/{repo}/pulls"

updated = scanned = 0
batch = []
for pr in client.paginate(api_url, params={"state": "closed"}):
    if not pr.get("merged_at"):
        continue
    scanned += 1
    login = pr["user"]["login"] if pr.get("user") else None
    if login:
        batch.append({"n": pr["number"], "login": login, "rid": REPO_ID})
    if len(batch) >= 500:
        for b in batch:
            db.execute(text("""
                UPDATE pull_requests SET author_login = :login
                WHERE repository_id = :rid AND pr_number = :n AND author_login IS NULL
            """), b)
        db.commit()
        updated += len(batch)
        batch = []
        print(f"  progress: scanned={scanned} updated~={updated}")

for b in batch:
    db.execute(text("""
        UPDATE pull_requests SET author_login = :login
        WHERE repository_id = :rid AND pr_number = :n AND author_login IS NULL
    """), b)
db.commit()
updated += len(batch)

print(f"backfill done: scanned={scanned} updated~={updated}")

linked = link_pr_authors_by_login(db, REPO_ID)
print(f"pr_authors linked: {linked}")

remaining = db.execute(text(
    "SELECT COUNT(*) FROM pull_requests WHERE repository_id = :r AND author_login IS NULL"
), {"r": REPO_ID}).scalar()
print(f"author_login still NULL: {remaining}")

db.close()
