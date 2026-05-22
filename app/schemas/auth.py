from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class UserMe(BaseModel):
    """GET /api/auth/me 응답 스키마 — 민감 정보(access_token_encrypted) 제외"""

    id: UUID
    github_id: int
    github_login: str
    display_name: str | None = None
    avatar_url: str | None = None
    email: str | None = None
    created_at: datetime
    last_login_at: datetime | None = None

    model_config = {"from_attributes": True}


class OAuthCallbackQuery(BaseModel):
    """GitHub OAuth 콜백 쿼리 파라미터"""

    code: str = Field(..., description="GitHub이 발급한 임시 authorization code")
    state: str = Field(..., description="CSRF 방지용 state 값")
