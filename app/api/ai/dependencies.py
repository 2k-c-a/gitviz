"""AI API 공용 dependency: 로그인 사용자, CSRF, 앱이 보낸 사용자 API key, 오류 응답."""
import hmac
import re
import secrets
from uuid import UUID, uuid4

from fastapi import Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.ai_settings import get_ai_settings
from app.api.auth import _get_session_user_id
from app.schemas.ai import ErrorBody, ErrorCode, ErrorEnvelope
from app.services.ai.access import AIError

CSRF_SESSION_KEY = 'csrf_token'
CSRF_HEADER = 'X-CSRF-Token'
SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})
# 앱 전용 BYOK(AD-32 ⑧, AD-33): 앱 main 프로세스가 AI를 부르는 요청에만 붙이는 사용자 key. 서버는 저장·기록하지 않는다.
KEY_HEADER = 'x-gitviz-ai-key'
KEY_PATTERN = re.compile(r'[!-~]{16,512}')


def require_ai_user(request: Request) -> UUID:
    """로그인한 사용자의 ID. 아니면 AIError."""
    user_id = _get_session_user_id(request)
    if user_id is None:
        raise AIError(ErrorCode.AUTH_REQUIRED, '로그인이 필요합니다.')
    return user_id


def request_api_key(request: Request, profile) -> str | None:
    """이 모델에 쓸 사용자 key. 로컬 모델은 key를 쓰지 않는다(헤더가 와도 버린다). 값은 오류 메시지에 넣지 않는다."""
    if not profile.requires_api_key:
        return None
    key = (request.headers.get(KEY_HEADER) or '').strip()
    if not key:
        raise AIError(ErrorCode.CREDENTIAL_REQUIRED, '앱 설정에서 이 AI 제공자의 API 키를 먼저 저장해 주세요.')
    if not KEY_PATTERN.fullmatch(key):
        raise AIError(ErrorCode.INVALID_SELECTION, 'API 키 형식이 올바르지 않습니다.')
    return key


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
    from app.services.desktop_auth import DesktopPrincipal
    if isinstance(getattr(request.state, 'desktop_principal', None), DesktopPrincipal):
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


async def ai_validation_error_response(request: Request, exc: RequestValidationError) -> JSONResponse:
    """AI API의 요청 검증 오류를 ErrorEnvelope로 바꾼다. 입력값은 담지 않고 문제 필드만 알린다."""
    if request.url.path.startswith('/api/desktop/'):
        return JSONResponse({'detail': '요청 형식이 올바르지 않습니다.'}, status_code=422,
                            headers={'Cache-Control': 'private, no-store'})
    if not request.url.path.startswith('/api/ai/'):
        return await request_validation_exception_handler(request, exc)
    fields = sorted({'.'.join(str(p) for p in err['loc'] if p != 'body') or 'request' for err in exc.errors()})
    error = AIError(ErrorCode.INVALID_SELECTION, '요청 형식이 올바르지 않습니다.', details={'fields': fields})
    return ai_error_response(request, error)
