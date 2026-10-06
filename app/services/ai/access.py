"""AI 작업의 저장소 권한·전송 정책 검사.

권한을 확인하지 못하면 허용하지 않는다. 기존 repository_access를 그대로 활용한다.
"""
from enum import Enum
from uuid import UUID

from fastapi import HTTPException

from app.core.ai_settings import get_ai_settings
from app.schemas.ai import ERROR_HTTP_STATUS, ErrorCode
from app.services.repository_access import verify_repository_access


class AIError(Exception):
    """ErrorEnvelope로 변환되는 AI 도메인 오류."""

    def __init__(self, code: ErrorCode, message: str, *, retryable: bool = False,
                 details: dict | None = None, headers: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        self.headers = headers or {}

    @property
    def status_code(self) -> int:
        return ERROR_HTTP_STATUS[self.code]


class AIAction(str, Enum):
    read = 'read'          # 보관된 입력·결과 조회
    delete = 'delete'
    prepare = 'prepare'    # 코드 취득(외부 전송 없음)
    generate = 'generate'  # AI 제공자로 코드 전송

    @property
    def sends_externally(self) -> bool:
        return self is AIAction.generate


def _from_http(exc: HTTPException) -> AIError:
    if exc.status_code == 401:
        return AIError(ErrorCode.REAUTH_REQUIRED, 'GitHub 인증을 갱신해 주세요.')
    if exc.status_code == 404:
        return AIError(ErrorCode.RESOURCE_NOT_FOUND, '저장소를 찾을 수 없거나 접근 권한이 없습니다.')
    if exc.status_code == 429:
        return AIError(ErrorCode.UPSTREAM_RATE_LIMIT, 'GitHub 접근 확인 한도에 도달했습니다.', retryable=True)
    if exc.status_code == 409:
        return AIError(ErrorCode.INVALID_STATE, '저장소 식별 정보를 확인할 수 없습니다.')
    return AIError(ErrorCode.ACCESS_CHECK_UNAVAILABLE, '저장소 접근 권한을 확인할 수 없습니다.', retryable=True)


def authorize_ai(db, user_id: UUID, repo_id: int, action: AIAction, policy_version: str):
    """로그인 사용자의 현재 저장소 접근과(외부 전송이면) 전송 허용·정책 버전을 검사한다."""
    if not user_id:
        raise AIError(ErrorCode.AUTH_REQUIRED, '로그인이 필요합니다.')
    settings = get_ai_settings()
    if not settings.enabled:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '사용할 수 없는 기능입니다.')
    try:
        repo, metadata = verify_repository_access(db, repo_id, user_id)
    except HTTPException as exc:
        raise _from_http(exc) from None
    except Exception:
        # 확인 장애를 권한 있음으로 추정하지 않는다.
        raise AIError(ErrorCode.ACCESS_CHECK_UNAVAILABLE, '저장소 접근 권한을 확인할 수 없습니다.',
                      retryable=True) from None
    if action.sends_externally:
        if not settings.allow_external_transfer:
            raise AIError(ErrorCode.TRANSFER_NOT_ALLOWED, '코드의 외부 전송이 허용되지 않았습니다.')
        if metadata.get('private') is not False:
            # MVP 정책: 비공개(또는 공개 여부를 확인하지 못한) 저장소의 코드는 외부로 보내지 않는다.
            raise AIError(ErrorCode.TRANSFER_NOT_ALLOWED, '비공개 저장소의 코드는 외부로 전송할 수 없습니다.')
        if policy_version not in settings.policy_versions:
            raise AIError(ErrorCode.TRANSFER_NOT_ALLOWED, '허용되지 않은 전송 정책 버전입니다.')
    return repo, metadata


def ensure_owner(resource_owner_id: UUID | None, user_id: UUID) -> None:
    """다른 작성자의 자료는 존재 여부를 숨긴다."""
    if resource_owner_id is None or resource_owner_id != user_id:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '자료를 찾을 수 없습니다.')
