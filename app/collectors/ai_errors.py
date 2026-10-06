"""AI 코드 수집 오류. 수집기는 DB·모델을 모르므로 계약의 ErrorCode만 실어 올린다."""
from app.schemas.ai import ErrorCode


class CollectionError(Exception):
    code = ErrorCode.UPSTREAM_UNAVAILABLE
    retryable = False

    def __init__(self, message: str, *, code: ErrorCode | None = None, retryable: bool | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable


class PRChanged(CollectionError):
    """기대한 base/head와 다르거나 취득 도중 PR이 바뀌었다."""
    code = ErrorCode.PR_CHANGED
    retryable = True


class MergeBaseUnavailable(CollectionError):
    """merge-base를 확정할 수 없어 비교 범위를 정할 수 없다."""
    code = ErrorCode.INVALID_STATE


class InvalidSelection(CollectionError):
    code = ErrorCode.INVALID_SELECTION


class InputTooLarge(CollectionError):
    code = ErrorCode.INPUT_TOO_LARGE


class ResourceNotFound(CollectionError):
    code = ErrorCode.RESOURCE_NOT_FOUND


class ReauthRequired(CollectionError):
    code = ErrorCode.REAUTH_REQUIRED


class RateLimited(CollectionError):
    code = ErrorCode.UPSTREAM_RATE_LIMIT
    retryable = True

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class UpstreamUnavailable(CollectionError):
    code = ErrorCode.UPSTREAM_UNAVAILABLE
    retryable = True
