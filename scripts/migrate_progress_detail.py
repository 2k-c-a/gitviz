"""analysis_jobs에 progress_detail(jsonb) 컬럼 추가 (업무지시서 3-1)"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import SessionLocal

db = SessionLocal()
db.execute(text(
    "ALTER TABLE analysis_jobs ADD COLUMN IF NOT EXISTS progress_detail JSONB DEFAULT '{}'::jsonb"
))
db.commit()
print("OK  analysis_jobs.progress_detail")
db.close()
