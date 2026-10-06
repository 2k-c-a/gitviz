"""AI API 공용 dependency: 로그인 사용자, CSRF, 오류 응답."""
import hmac
import secrets
from uuid import UUID, uuid4

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.ai_settings import get_ai_settings
from app.routers.auth import _get_session_user_id
from app.schemas.ai import ErrorBody, ErrorCode, ErrorEnvelope
from app.services.ai.access import AIError

CSRF_SESSION_KEY = 'csrf_token'
CSRF_HEADER = 'X-CSRF-Token'
SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})


def require_ai_user(request: Request) -> UUID:
    """AI 기능이 켜져 있고 로그인한 사용자의 ID. 아니면 AIError."""
    if not get_ai_settings().enabled:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '사용할 수 없는 기능입니다.')
    user_id = _get_session_user_id(request)
    if user_id is None:
        raise AIError(ErrorCode.AUTH_REQUIRED, '로그인이 필요합니다.')
    return user_id


def issue_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def require_csrf(request: Request) -> None:
    """변경 요청의 Origin과 X-CSRF-Token을 검사한다. 안전한 메서드는 통과."""
    if request.method.upper() in SAFE_METHODS:
        return
    fail = AIError(ErrorCode.CSRF_FAILED, '요청을 확인할 수 없습니다.')
    origin = (request.headers.get('origin') or '').strip().lower()
    if origin not in get_ai_settings().allowed_origins:
        raise fail
    expected = request.session.get(CSRF_SESSION_KEY)
    given = request.headers.get(CSRF_HEADER, '')
    if not isinstance(expected, str) or not expected or not hmac.compare_digest(expected, given):
        raise fail


def ai_error_response(request: Request, exc: AIError) -> JSONResponse:
    request_id = (request.headers.get('x-request-id') or '')[:64] or str(uuid4())
    body = ErrorEnvelope(error=ErrorBody(code=exc.code, message=exc.message, retryable=exc.retryable,
                                         request_id=request_id, details=exc.details))
    headers = {'Cache-Control': 'private, no-store', **exc.headers}
    return JSONResponse(body.model_dump(mode='json'), status_code=exc.status_code, headers=headers)
