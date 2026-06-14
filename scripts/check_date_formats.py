"""String 날짜 컬럼 샘플 확인 (timestamptz 전환 가능 여부)"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import SessionLocal

db = SessionLocal()
db.execute(text("SET statement_timeout = 0"))

samples = {
    "commits.committed_at": "SELECT committed_at FROM commits WHERE committed_at IS NOT NULL LIMIT 3",
    "pull_requests.merged_at": "SELECT merged_at FROM pull_requests WHERE merged_at IS NOT NULL LIMIT 3",
    "pull_requests.created_at": "SELECT created_at FROM pull_requests WHERE created_at IS NOT NULL LIMIT 3",
    "pr_reviews.submitted_at": "SELECT submitted_at FROM pr_reviews WHERE submitted_at IS NOT NULL LIMIT 3",
    "releases.published_at": "SELECT published_at FROM releases WHERE published_at IS NOT NULL LIMIT 3",
}
for label, q in samples.items():
    rows = [r[0] for r in db.execute(text(q)).fetchall()]
    print(f"{label}: {rows}")

# 변환 실패할 값이 있는지 사전 검사
casts = {
    "commits.committed_at": "SELECT COUNT(*) FROM commits WHERE committed_at IS NOT NULL AND committed_at !~ '^\\d{4}-\\d{2}-\\d{2}'",
    "pull_requests.merged_at": "SELECT COUNT(*) FROM pull_requests WHERE merged_at IS NOT NULL AND merged_at !~ '^\\d{4}-\\d{2}-\\d{2}'",
    "pull_requests.created_at": "SELECT COUNT(*) FROM pull_requests WHERE created_at IS NOT NULL AND created_at !~ '^\\d{4}-\\d{2}-\\d{2}'",
    "pr_reviews.submitted_at": "SELECT COUNT(*) FROM pr_reviews WHERE submitted_at IS NOT NULL AND submitted_at !~ '^\\d{4}-\\d{2}-\\d{2}'",
    "releases.published_at": "SELECT COUNT(*) FROM releases WHERE published_at IS NOT NULL AND published_at !~ '^\\d{4}-\\d{2}-\\d{2}'",
}
print("\n=== ISO 형식 아닌 값 개수 (0이어야 안전) ===")
for label, q in casts.items():
    print(f"{label}: {db.execute(text(q)).scalar()}")

db.close()
