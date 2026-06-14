"""commit_type 분류 + 대형 커밋 플래그용 컬럼 추가 (업무지시서 3-2)"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import SessionLocal

db = SessionLocal()
steps = [
    ("commits.commit_type_source",
     "ALTER TABLE commits ADD COLUMN IF NOT EXISTS commit_type_source VARCHAR"),
    ("commits.commit_type_confidence",
     "ALTER TABLE commits ADD COLUMN IF NOT EXISTS commit_type_confidence DOUBLE PRECISION"),
    ("commits.is_large_commit",
     "ALTER TABLE commits ADD COLUMN IF NOT EXISTS is_large_commit BOOLEAN DEFAULT FALSE"),
]
for label, q in steps:
    db.execute(text(q))
    print("OK ", label)
db.commit()
db.close()
