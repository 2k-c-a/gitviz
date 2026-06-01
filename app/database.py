from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from dotenv import load_dotenv
import os

load_dotenv()

# async 드라이버용 URL: postgresql+asyncpg://...
# data-col 팀의 sync URL(postgresql://)은 app/core/database.py 에서 별도 관리
DATABASE_URL = os.getenv("DATABASE_URL_ASYNC") or os.getenv("DATABASE_URL", "")

# asyncpg 드라이버 prefix 보정 (sync URL이 들어온 경우 자동 변환)
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False) if DATABASE_URL else None

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
) if engine else None


class Base(DeclarativeBase):
    pass


async def get_db():
    if AsyncSessionLocal is None:
        raise RuntimeError("DATABASE_URL 환경변수가 설정되지 않았습니다.")
    async with AsyncSessionLocal() as session:
        yield session
