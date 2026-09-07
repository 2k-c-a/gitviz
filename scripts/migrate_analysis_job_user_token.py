"""analysis_jobs에 triggered_by_user_id(uuid) 컬럼 추가.

로그인한 사용자가 분석을 요청하면 이 컬럼에 users.id를 저장해두고,
analyze_repository.py가 이 값으로 그 사용자의 GitHub 토큰(users.access_token_encrypted)을
찾아 수집에 사용한다 — 공용 GITHUB_TOKEN 하나를 모든 분석이 나눠 쓰지 않고
사용자별 rate limit(시간당 5000회)을 쓰게 하기 위함.

users 테이블은 app/database.py의 별도 async Base로 관리되므로 FK 제약은 걸지 않는다.
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import SessionLocal

db = SessionLocal()
db.execute(text(
    "ALTER TABLE analysis_jobs ADD COLUMN IF NOT EXISTS triggered_by_user_id UUID"
))
db.commit()
print("OK  analysis_jobs.triggered_by_user_id")
db.close()
