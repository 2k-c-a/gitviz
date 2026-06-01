"""
GitHub OAuth 인증 서비스

담당 역할:
  1. authorization code → access token 교환 (GitHub API 호출)
  2. access token → GitHub 사용자 정보 조회
  3. access token AES-256-GCM 암복호화
  4. USERS 테이블 upsert (github_id 기준)
"""

import base64
import os
import secrets

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

# ── 환경변수 ──────────────────────────────────────────────────────────────────
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")

# 32바이트(256비트) hex 문자열 → bytes
# 없으면 런타임 에러 대신 빈 키로 두고 서비스 기동을 막지 않음
_raw_key = os.getenv("TOKEN_ENCRYPTION_KEY", "")
TOKEN_ENCRYPTION_KEY: bytes = bytes.fromhex(_raw_key) if len(_raw_key) == 64 else b""


# ── AES-256-GCM 암복호화 ──────────────────────────────────────────────────────

def encrypt_token(plaintext: str) -> str:
    """
    AES-256-GCM으로 plaintext 암호화.
    반환값 형식: base64(nonce || ciphertext_with_tag)
    """
    if not TOKEN_ENCRYPTION_KEY:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY 환경변수가 설정되지 않았습니다.")

    aesgcm = AESGCM(TOKEN_ENCRYPTION_KEY)
    nonce = secrets.token_bytes(12)          # GCM 권장 96-bit nonce
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt_token(encoded: str) -> str:
    """encrypt_token()으로 암호화된 값을 복호화해 원문 반환."""
    if not TOKEN_ENCRYPTION_KEY:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY 환경변수가 설정되지 않았습니다.")

    raw = base64.urlsafe_b64decode(encoded)
    nonce, ct = raw[:12], raw[12:]
    aesgcm = AESGCM(TOKEN_ENCRYPTION_KEY)
    return aesgcm.decrypt(nonce, ct, None).decode()


# ── GitHub API 호출 ───────────────────────────────────────────────────────────

async def exchange_code_for_token(code: str) -> str:
    """
    GitHub authorization code → access token 교환.
    https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#2-users-are-redirected-back-to-your-site-by-github
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
            },
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()

    if "error" in body:
        raise ValueError(f"GitHub OAuth 에러: {body.get('error_description', body['error'])}")

    return body["access_token"]


async def fetch_github_user(access_token: str) -> dict:
    """
    access token으로 GitHub /user 엔드포인트를 호출해 사용자 정보 반환.
    반환 키: id, login, name, avatar_url, email
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()


# ── USERS 테이블 upsert ───────────────────────────────────────────────────────

async def upsert_user(db: AsyncSession, github_user: dict, access_token: str) -> User:
    """
    github_user: fetch_github_user() 반환값
    access_token: 원문 토큰 (DB 저장 전 암호화)

    - 신규 사용자면 INSERT
    - 기존 사용자면 login/display_name/avatar_url/email/token/last_login_at UPDATE
    반환: 저장된 User ORM 인스턴스
    """
    from datetime import datetime, timezone

    github_id: int = github_user["id"]
    encrypted = encrypt_token(access_token)
    now = datetime.now(timezone.utc)

    result = await db.execute(select(User).where(User.github_id == github_id))
    user: User | None = result.scalar_one_or_none()

    if user is None:
        user = User(
            github_id=github_id,
            github_login=github_user.get("login", ""),
            display_name=github_user.get("name") or github_user.get("login", ""),
            avatar_url=github_user.get("avatar_url"),
            email=github_user.get("email"),
            access_token_encrypted=encrypted,
            last_login_at=now,
        )
        db.add(user)
    else:
        user.github_login = github_user.get("login", user.github_login)
        user.display_name = github_user.get("name") or github_user.get("login", user.display_name)
        user.avatar_url = github_user.get("avatar_url", user.avatar_url)
        user.email = github_user.get("email", user.email)
        user.access_token_encrypted = encrypted
        user.last_login_at = now

    await db.commit()
    await db.refresh(user)
    return user
