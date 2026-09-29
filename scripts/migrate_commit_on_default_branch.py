"""commits.on_default_branch 컬럼 추가 — 화면 커밋 수를 GitHub(기본 브랜치 기준)과 맞추기 위함.

기존 행은 NULL(판정 전)로 남고, 집계는 NULL을 포함하므로 재분석 전까지는 이전과 같은 숫자가 나온다.
저장소를 한 번 재분석하면 채워진다.
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import SessionLocal

db = SessionLocal()
steps = [
    ("commits.on_default_branch",
     "ALTER TABLE commits ADD COLUMN IF NOT EXISTS on_default_branch BOOLEAN"),
]
for label, q in steps:
    db.execute(text(q))
    print("OK ", label)
db.commit()
db.close()
