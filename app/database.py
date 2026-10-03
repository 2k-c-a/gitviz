from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import NullPool
from sqlalchemy.engine import make_url
from dotenv import load_dotenv
import os
import ssl
import uuid

load_dotenv()

# async 드라이버용 URL: postgresql+asyncpg://...
# data-col 팀의 sync URL(postgresql://)은 app/core/database.py 에서 별도 관리
DATABASE_URL = os.getenv("DATABASE_URL_ASYNC") or os.getenv("DATABASE_URL", "")

# asyncpg 드라이버 prefix 보정 (sync URL이 들어온 경우 자동 변환)
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# ── Supabase 풀러(pgbouncer, transaction mode) + asyncpg 연결 설정 ─────────────
#   • ssl: 명시적 컨텍스트를 주지 않으면 asyncpg가 기본 클라이언트 인증서 경로
#          (~/.postgresql/...)를 로드하려다 비ASCII 홈 경로에서 깨진다(EILSEQ).
#          Supabase 풀러 인증서 체인은 self-signed 이므로 검증은 끈다.
#   • statement_cache_size / prepared_statement_*: transaction pooling에서는
#          prepared statement가 커넥션 간 공유되지 않아 DuplicatePreparedStatement가
#          난다 → 캐시 끄고 statement 이름을 매번 고유화.
def _build_async_engine(url: str):
    if not url:
        return None
    # 로컬 PostgreSQL/Docker는 기본적으로 TLS를 제공하지 않는다.
    local = make_url(url).host in {'localhost', '127.0.0.1', '::1'}
    ssl_ctx = False
    if not local:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
    return create_async_engine(
        url,
        echo=False,
        poolclass=NullPool,
        connect_args={
            "ssl": ssl_ctx,
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__asyncpg_{uuid.uuid4()}__",
        },
    )


engine = _build_async_engine(DATABASE_URL)

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
