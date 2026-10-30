"""Identity Resolution용 최소 컬럼 추가 (업무지시서 3-6, v2 스키마 유지)"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from sqlalchemy import text
from app.core.database import SessionLocal

db = SessionLocal()
steps = [
    ("contributors.resolution_status",
     "ALTER TABLE contributors ADD COLUMN IF NOT EXISTS resolution_status VARCHAR DEFAULT 'unresolved'"),
    ("contributor_aliases.resolution_rule",
     "ALTER TABLE contributor_aliases ADD COLUMN IF NOT EXISTS resolution_rule VARCHAR"),
]
for label, q in steps:
    db.execute(text(q))
    print("OK ", label)
db.commit()
db.close()
