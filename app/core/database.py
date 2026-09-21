from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Supabase Supavisor 풀러가 Session 모드(DATABASE_URL은 포트 5432)라 동시 접속을
# 프로젝트 전체 기준 15개로 제한한다. SQLAlchemy 기본값(pool_size=5 + max_overflow=10
# = 최대 15)은 이 엔진 프로세스 하나만으로 한도를 다 채울 수 있어 uvicorn +
# Celery worker를 같이 띄우면 바로 "max clients reached in session mode"가 난다.
# → 프로세스당 최대 커넥션을 작게 제한(5) + 오래 열린 유휴 커넥션은 주기적으로 재활용.
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=3,
    max_overflow=2,
    pool_recycle=300,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()