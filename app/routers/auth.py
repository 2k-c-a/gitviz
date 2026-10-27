"""
인증 라우터 — GitHub OAuth 2.0

엔드포인트:
  GET  /api/auth/github    → GitHub 로그인 페이지로 302 리다이렉트
  GET  /api/auth/callback  → GitHub 콜백 처리, 세션 설정 후 프론트로 리다이렉트
  GET  /api/auth/me        → 현재 로그인 사용자 정보 반환 (401 if not logged in)
  POST /api/auth/logout    → 세션 초기화
"""

import os
import secrets
import uuid
from urllib.parse import urlencode

from dotenv import load_dotenv
load_dotenv()

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.auth import UserMe
from app.services.auth_service import (
    exchange_code_for_token,
    fetch_github_user,
    upsert_user,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ── 환경변수 ──────────────────────────────────────────────────────────────────
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8000")

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_SCOPES = "read:user,repo"


# ── 헬퍼: 세션에서 로그인 사용자 ID 꺼내기 ───────────────────────────────────

def _get_session_user_id(request: Request) -> uuid.UUID | None:
    from app.services.desktop_auth import DesktopPrincipal
    principal = getattr(request.state, 'desktop_principal', None)
    if isinstance(principal, DesktopPrincipal):
        return principal.user_id
    raw = request.session.get("user_id")
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.get("/github", summary="GitHub OAuth 로그인 시작")
async def github_login(request: Request):
    """
    CSRF state를 세션에 저장하고 GitHub 인증 페이지로 리다이렉트.
    """
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    # Signed browser session binds the existing OAuth state to one desktop flow.
    request.session['desktop_oauth_flow'] = request.session.get('desktop_flow')

    params = urlencode(
        {
            "client_id": GITHUB_CLIENT_ID,
            "scope": GITHUB_SCOPES,
            "state": state,
        }
    )
    return RedirectResponse(
        url=f"{GITHUB_AUTHORIZE_URL}?{params}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/callback", summary="GitHub OAuth 콜백")
async def github_callback(
    request: Request,
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """
    1. state 검증 (CSRF 방지)
    2. code → access token 교환
    3. GitHub /user 조회
    4. USERS 테이블 upsert
    5. 세션에 user_id 기록 후 프론트로 리다이렉트
    """
    # ① CSRF state 검증
    stored_state = request.session.pop("oauth_state", None)
    desktop_flow = request.session.pop('desktop_oauth_flow', None)
    if not stored_state or stored_state != state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="유효하지 않은 OAuth state입니다. 다시 로그인해 주세요.",
        )

    # ② code → token
    try:
        access_token = await exchange_code_for_token(code)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"GitHub 토큰 교환 실패: {exc}",
        )

    # ③ GitHub 사용자 정보 조회
    try:
        github_user = await fetch_github_user(access_token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"GitHub 사용자 정보 조회 실패: {exc}",
        )

    # ④ DB upsert
    user = await upsert_user(db, github_user, access_token)

    # ⑤ 세션 기록 → 프론트로 이동
    request.session["user_id"] = str(user.id)
    request.session.pop("csrf_token", None)  # 로그인 시 CSRF 토큰 재발급
    return RedirectResponse(
        url=(f'/api/desktop/v1/auth/authorize?{urlencode({"flow": desktop_flow})}'
             if desktop_flow and desktop_flow == request.session.get('desktop_flow') else f"{FRONTEND_URL}/"),
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/me", response_model=UserMe, summary="현재 로그인 사용자 정보")
async def get_me(request: Request, db: AsyncSession = Depends(get_db)):
    """
    세션에서 user_id를 꺼내 DB 조회 후 반환.
    로그인하지 않은 경우 401 반환.
    """
    from sqlalchemy import select
    from app.models.user import User

    user_id = _get_session_user_id(request)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다.",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사용자를 찾을 수 없습니다. 다시 로그인해 주세요.",
        )

    return user


@router.get("/csrf", summary="CSRF 토큰 발급")
async def get_csrf_token(request: Request):
    """AI 등 변경 요청이 X-CSRF-Token 헤더로 보낼 세션 바인딩 토큰."""
    from app.api.ai.dependencies import issue_csrf_token

    return JSONResponse({"token": issue_csrf_token(request)}, headers={"Cache-Control": "private, no-store"})


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="로그아웃")
async def logout(request: Request):
    """세션 전체를 초기화한다 (쿠키는 브라우저 재요청 시 만료)."""
    request.session.clear()
