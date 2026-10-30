"""Identity Resolution 수동 실행.

사용법:
  python tools/ops/run_identity_resolution.py <repository_id> [--stage2-limit N | --stage2-full]
  (기본: 1단계만)
"""

import sys, os, argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text
from app.core.database import SessionLocal
from app.collectors.github.github_client import GitHubClient
from app.collectors.identity.resolver import run_identity_resolution

parser = argparse.ArgumentParser()
parser.add_argument("repository_id", type=int)
parser.add_argument("--stage2-limit", type=int, default=0)
parser.add_argument("--stage2-full", action="store_true")
args = parser.parse_args()

stage2_limit = None if args.stage2_full else args.stage2_limit

db = SessionLocal()
db.execute(text("SET statement_timeout = 0"))
client = GitHubClient() if stage2_limit != 0 else None

result = run_identity_resolution(db, args.repository_id, client=client, stage2_limit=stage2_limit)

print("\n=== Identity Resolution 결과 ===")
print("commits 기본 연결:", result["commits_linked"])
print("1단계 noreply:", result["stage1"])
if "stage2" in result:
    print("2단계 github_api:", result["stage2"])
print("commits alias 연결:", result["commits_linked_by_alias"])
print("pr_reviews 연결:", result["pr_reviews_linked"])
print("pr_authors 연결:", result["pr_authors_linked"])
s = result["stats"]
print(f"\ncontributors: total={s['total']} auto={s['auto']} unresolved={s['unresolved']}"
      f" (unresolved {s['unresolved_ratio']*100:.1f}%)")

db.close()
