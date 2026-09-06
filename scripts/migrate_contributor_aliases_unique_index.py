"""contributor_aliases에 유니크 인덱스 추가 (migrate_schema_v2.py가 정의했지만
이 DB 인스턴스엔 적용 안 돼있던 부분 — identity resolver의 ON CONFLICT가 이 인덱스를
전제로 함, 없으면 InvalidColumnReference로 크래시)."""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import SessionLocal

db = SessionLocal()
db.execute(text("""
    CREATE UNIQUE INDEX IF NOT EXISTS uq_contributor_aliases_cid_email_name
    ON contributor_aliases (contributor_id, COALESCE(email, ''), COALESCE(name, ''))
"""))
db.commit()
print("OK  uq_contributor_aliases_cid_email_name")
db.close()
