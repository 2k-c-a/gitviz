"""운영 DB 통합 검증용 서버: 기존 라우터/프론트를 읽기 전용으로 실행한다.

python -m uvicorn tests.server.serve_readonly_validation:app --host 127.0.0.1 --port 8000
app.main의 create_all/startup DDL을 실행하지 않으며, POST/PUT/PATCH/DELETE를 차단한다.
"""
import os

# PostgreSQL 세션 전체를 읽기 전용으로 제한한다(동기 psycopg2 연결).
os.environ["PGOPTIONS"] = "-c default_transaction_read_only=on -c statement_timeout=45000"

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import event
from app.core.database import engine


@event.listens_for(engine, "begin")
def enforce_read_only(connection):
    # Supabase 풀러가 PGOPTIONS를 무시할 수 있어 트랜잭션마다 명시한다.
    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
    connection.exec_driver_sql("SET LOCAL statement_timeout = '45s'")

from app.api.analytics import router as analytics_router
from app.api.repositories import router as repository_router
from app.routers.auth import router as auth_router

app = FastAPI(title="GitViz read-only integration validation")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET_KEY", "validation-only"),
                   session_cookie="gitviz_validation_session")


@app.middleware("http")
async def read_only(request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"} or request.url.path in {
        "/api/auth/github", "/api/auth/callback"
    }:
        return JSONResponse({"detail": "Read-only validation server"}, status_code=405)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


app.include_router(repository_router)
app.include_router(analytics_router)
app.include_router(auth_router)
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
