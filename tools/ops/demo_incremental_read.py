"""실제 클론으로 증분 읽기 효과 직접 시연.

repo 11(itsdangerous)의 DB 커서(MAX committed_at)를 기준으로,
collect_commits를 전체(full) vs since(증분)로 호출해 읽는 양 차이를 보여준다.
"""

import sys, os
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text
from app.core.database import SessionLocal
from app.collectors.git.commit_collector import collect_commits
from app.collectors.git.commit_file_change_collector import collect_commit_file_changes

REPO_ID = 11
CLONE = r"storage/repos/itsdangerous"
BUFFER_DAYS = int(os.getenv("INCREMENTAL_BUFFER_DAYS", "7"))

db = SessionLocal()
cursor = db.execute(text(
    "SELECT MAX(committed_at) FROM commits WHERE repository_id = :r"
), {"r": REPO_ID}).scalar()
db.close()

since = (cursor - timedelta(days=BUFFER_DAYS)).date().isoformat()
print(f"DB 커서(MAX committed_at) = {cursor}")
print(f"증분 since = {since} (버퍼 {BUFFER_DAYS}일)\n")

full_c = collect_commits(CLONE)
inc_c = collect_commits(CLONE, since=since)
print(f"커밋 읽기:   전체 {len(full_c):>6}건  →  증분 {len(inc_c):>6}건")

full_ch = collect_commit_file_changes(CLONE)
inc_ch = collect_commit_file_changes(CLONE, since=since)
print(f"변경 읽기:   전체 {len(full_ch):>6}건  →  증분 {len(inc_ch):>6}건")

print(f"\n→ 재실행 시 git에서 읽는 양이 커밋 {len(full_c)}→{len(inc_c)}, "
      f"변경 {len(full_ch)}→{len(inc_ch)}로 감소 (중복은 DB upsert가 차단)")
