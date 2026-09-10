"""Identity Resolution 2단계(GitHub Search API)가 실패한 검색을 매 재분석마다 재시도하던
문제 해결용 컬럼 추가. stage2_email_search가 이 시각을 기록해두면, 쿨다운 기간 안에는
같은 사람을 다시 검색하지 않는다."""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import SessionLocal

db = SessionLocal()
db.execute(text(
    "ALTER TABLE contributors ADD COLUMN IF NOT EXISTS identity_search_attempted_at TIMESTAMPTZ"
))
db.commit()
print("OK  contributors.identity_search_attempted_at")
db.close()
