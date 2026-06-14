"""기존 repo의 commit_type 분류 + is_large_commit 백필 (업무지시서 3-2).

사용법: python scripts/backfill_commit_type.py [repository_id=1]
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import SessionLocal
from app.collectors.git.commit_classifier import classify_commit

RID = int(sys.argv[1]) if len(sys.argv) > 1 else 1
LARGE_THRESHOLD = int(os.getenv("LARGE_CHANGE_THRESHOLD", "15"))
LARGE_PCT = float(os.getenv("LARGE_CHANGE_PCT", "0.10"))
CHUNK = 2000

db = SessionLocal()
db.execute(text("SET statement_timeout = 0"))

# --- commit_type 분류 ---
rows = db.execute(text(
    "SELECT id, message FROM commits WHERE repository_id = :r"
), {"r": RID}).fetchall()
print(f"분류 대상 커밋: {len(rows)}")

updates = []
for cid, msg in rows:
    ctype, csource, cconf = classify_commit(msg or "")
    updates.append({"cid": cid, "t": ctype, "s": csource, "c": cconf})

upd_sql = text(
    "UPDATE commits SET commit_type = :t, commit_type_source = :s, "
    "commit_type_confidence = :c WHERE id = :cid"
)
for i in range(0, len(updates), CHUNK):
    for u in updates[i:i + CHUNK]:
        db.execute(upd_sql, u)
    db.commit()
    print(f"  분류 진행: {min(i + CHUNK, len(updates))}/{len(updates)}")

# --- is_large_commit ---
total_files = db.execute(text(
    "SELECT COUNT(*) FROM files WHERE repository_id = :r"
), {"r": RID}).scalar()
db.execute(text("""
    UPDATE commits SET is_large_commit = (
        changed_file_count > :thr
        OR (:total > 0 AND changed_file_count > :total * :pct)
    )
    WHERE repository_id = :r
"""), {"thr": LARGE_THRESHOLD, "total": total_files, "pct": LARGE_PCT, "r": RID})
db.commit()

# --- 결과 분포 ---
print("\n=== commit_type 분포 ===")
for row in db.execute(text("""
    SELECT commit_type, commit_type_source, COUNT(*)
    FROM commits WHERE repository_id = :r
    GROUP BY commit_type, commit_type_source ORDER BY COUNT(*) DESC
"""), {"r": RID}).fetchall():
    print(f"  {row[0]:10s} [{row[1]}]: {row[2]}")

large = db.execute(text(
    "SELECT COUNT(*) FROM commits WHERE repository_id = :r AND is_large_commit"
), {"r": RID}).scalar()
total = db.execute(text(
    "SELECT COUNT(*) FROM commits WHERE repository_id = :r"
), {"r": RID}).scalar()
print(f"\nis_large_commit: {large}/{total} ({large/total*100:.1f}%)  "
      f"(threshold={LARGE_THRESHOLD}, pct={LARGE_PCT}, total_files={total_files})")
db.close()
