import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import Scope


class NoCacheStaticFiles(StaticFiles):
    """정적 파일에 캐시 방지 헤더를 붙여, 프론트엔드 수정 후 브라우저가
    이전 버전의 HTML/JS/CSS를 계속 캐시해 보여주는 문제를 막는다."""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

# ── data-col 팀: 동기 DB / repositories 라우터 ────────────────────────────────
from app.models import repository      # noqa: F401  (테이블 등록용 import)
from app.models import analysis_job    # noqa: F401
from app.api.repositories import router as repository_router
from app.api.analytics import router as analytics_router
from app.api.snapshots import router as snapshots_router

# ── feat/login 팀: 비동기 auth 라우터 ────────────────────────────────────────
from app.routers import auth as auth_router

app = FastAPI(title="GitViz API", version="0.1.0")

# ── 세션 미들웨어 (HttpOnly 서명 쿠키, auth 로그인에 필요) ────────────────────
session_secret = os.getenv("SESSION_SECRET_KEY", "")
if not session_secret or session_secret == "change-me-in-production":
    raise RuntimeError("SESSION_SECRET_KEY에 고유한 비밀키를 설정해야 합니다.")
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    session_cookie="gitviz_session",
    max_age=60 * 60 * 24 * 7,   # 7일
    https_only=os.getenv("SESSION_COOKIE_SECURE", "0") == "1",
    same_site="lax",
)

# 스키마는 서버 실행 전 `python -m alembic upgrade head`로 준비한다.
# 서버 import/startup 중에는 운영 DB에 DDL을 실행하지 않는다.

# ── 라우터 등록 ───────────────────────────────────────────────────────────────
app.include_router(repository_router)
app.include_router(analytics_router)
app.include_router(snapshots_router)
app.include_router(auth_router.router)

# ── 프론트엔드 정적 파일 서빙 (API 라우터 이후 마지막에 위치) ─────────────────
app.mount("/", NoCacheStaticFiles(directory="frontend", html=True), name="frontend")
