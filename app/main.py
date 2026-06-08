import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

# ── data-col 팀: 동기 DB / repositories 라우터 ────────────────────────────────
from app.core.database import Base, engine
from app.models import repository      # noqa: F401  (테이블 등록용 import)
from app.models import analysis_job    # noqa: F401
from app.api.repositories import router as repository_router

# ── feat/login 팀: 비동기 auth 라우터 ────────────────────────────────────────
from app.routers import auth as auth_router

app = FastAPI(title="GitViz API", version="0.1.0")

# ── CORS (프론트엔드 localhost:3000 허용) ─────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 세션 미들웨어 (HttpOnly 서명 쿠키, auth 로그인에 필요) ────────────────────
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY", "change-me-in-production"),
    session_cookie="gitviz_session",
    max_age=60 * 60 * 24 * 7,   # 7일
    https_only=False,
    same_site="lax",
)

# ── 테이블 생성 (동기 엔진 기준) ───────────────────────────────────────────────
Base.metadata.create_all(bind=engine)

# ── 라우터 등록 ───────────────────────────────────────────────────────────────
app.include_router(repository_router)
app.include_router(auth_router.router)


@app.get("/")
def root():
    return {"message": "GitViz API Running"}
