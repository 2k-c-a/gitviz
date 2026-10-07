"""AI 제공자 adapter의 공통 계약: profile, 요청·응답, 실패 분류.

adapter는 DB·GitHub·추가 코드 취득·shell에 접근하지 않는다. 입력(system/user 문자열)을 받아 한 번 호출하고
결과·사용량·실패 분류만 돌려준다. 어떤 도구도 제공하지 않으므로 모델이 임의의 tool을 실행할 수 없다.

실패 분류의 핵심은 `outcome_uncertain`이다.
- False: 요청이 처리되지 않았음이 확실하다(거절·한도·서버 오류 응답, 연결 전 실패). 비용 예약을 해제하고,
  `retryable`이면 backoff 후 다시 호출해도 안전하다.
- True: 요청이 제공자에 닿았을 수 있고 응답을 받지 못했다(응답 대기 중 timeout·연결 끊김). 과금 여부를 알 수 없으므로
  예약을 uncertain으로 남기고 자동으로 다시 호출하지 않는다.
"""
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from app.schemas.ai import JobKind, canonical_hash

MAX_RAW_OUTPUT_BYTES = 65_536

# 작업 error_code(문자열). handler가 JobFailed/RetryableError에 싣는다.
ERROR_CODES = {
    'rate_limited': 'PROVIDER_RATE_LIMITED', 'server_error': 'PROVIDER_UNAVAILABLE',
    'connection_failed': 'PROVIDER_UNAVAILABLE', 'timeout': 'PROVIDER_TIMEOUT',
    'response_lost': 'PROVIDER_TIMEOUT', 'auth_failed': 'PROVIDER_AUTH', 'billing_error': 'PROVIDER_AUTH',
    'model_unavailable': 'PROVIDER_REJECTED', 'request_rejected': 'PROVIDER_REJECTED',
    'input_too_large': 'INPUT_TOO_LARGE', 'unknown_error': 'PROVIDER_ERROR',
}


@dataclass(frozen=True)
class ProviderProfile:
    """버전이 고정된 모델 profile. 값을 바꾸려면 새 id(버전)를 만든다. 이용자가 URL·모델을 지정할 수 없다."""
    id: str
    provider: str
    model: str
    display_name: str
    price_version: str
    currency: str
    input_price_per_mtok: Decimal
    output_price_per_mtok: Decimal
    context_window: int
    max_input_tokens: int
    max_output_tokens: int
    timeout_seconds: int
    effort: str
    bytes_per_token: float                  # 호출 전 로컬 추정에 쓰는 보수적 값(실제 토큰 수는 count_tokens로 확인)
    summary_prompt: str
    review_prompt: str
    summary_schema: str
    review_schema: str

    def prompt_version(self, kind: str) -> str:
        return {JobKind.summary.value: self.summary_prompt, JobKind.review.value: self.review_prompt}[kind]

    def schema_version(self, kind: str) -> str:
        return {JobKind.summary.value: self.summary_schema, JobKind.review.value: self.review_schema}[kind]

    def fingerprint(self) -> str:
        """작업 fingerprint에 넣는 profile 전체(모델·가격·한도·timeout·프롬프트·schema) 해시."""
        data = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in self.__dict__.items()}
        return canonical_hash(data)


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def billed_input_tokens(self) -> int:
        """캐시를 쓰지 않지만, 응답에 캐시 토큰이 있으면 보수적으로 입력 단가로 합산한다."""
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens


@dataclass(frozen=True)
class GenerationRequest:
    """한 번의 호출. tools·fallback·sampling 옵션이 없다(필드 자체가 없다)."""
    kind: str
    system: str = field(repr=False)       # 코드가 들어 있으므로 repr·로그에 나오지 않게 한다
    user: str = field(repr=False)
    max_output_tokens: int | None = None      # None이면 profile 값


@dataclass
class ProviderResponse:
    """호출이 끝까지 처리된 응답(과금됨). finish가 'complete'가 아니면 출력은 쓸 수 없다."""
    request_id: str | None
    model: str | None
    finish: str                       # complete | truncated | refused | empty | incomplete
    text: str = field(repr=False)     # 모델 출력(코드 인용 포함)은 repr에 나오지 않게 한다
    parsed: dict | None = field(repr=False)
    parse_error: str | None
    usage: Usage | None
    cost: Decimal | None              # profile 가격으로 계산한 추정액. usage가 없으면 None
    latency_seconds: float
    refusal_category: str | None = None

    @property
    def usage_reported(self) -> bool:
        return self.usage is not None

    @property
    def usable(self) -> bool:
        return self.finish == 'complete' and self.parsed is not None


@dataclass(frozen=True)
class ProviderFailure:
    kind: str
    retryable: bool
    outcome_uncertain: bool
    retry_after: int | None = None
    http_status: int | None = None
    request_id: str | None = None

    @property
    def error_code(self) -> str:
        return ERROR_CODES.get(self.kind, 'PROVIDER_ERROR')


class ProviderError(Exception):
    """호출이 처리되지 않았거나 결과를 알 수 없다. 메시지에는 분류만 담고 요청·응답 본문은 담지 않는다."""

    def __init__(self, failure: ProviderFailure):
        super().__init__(f'provider call failed: {failure.kind}')
        self.failure = failure


class TokenLimitExceeded(Exception):
    def __init__(self, limit_kind: str, estimated: int, limit: int):
        super().__init__(f'{limit_kind} limit exceeded')
        self.limit_kind, self.estimated, self.limit = limit_kind, estimated, limit


class Provider(Protocol):
    profile: ProviderProfile

    def generate(self, request: GenerationRequest) -> ProviderResponse: ...
    def estimate_tokens(self, request: GenerationRequest) -> int: ...
    def count_tokens(self, request: GenerationRequest) -> int: ...
    def verify_credential(self) -> None: ...
    def classify_error(self, exc: BaseException) -> ProviderFailure: ...


def parse_json_object(text: str) -> tuple[dict | None, str | None]:
    """모델 출력 전체가 JSON 객체 하나여야 한다. 코드 펜스·앞뒤 설명·NaN은 거부한다."""
    if len(text.encode('utf-8')) > MAX_RAW_OUTPUT_BYTES:
        return None, 'output_too_large'

    def reject(constant):
        raise ValueError(constant)
    try:
        value = json.loads(text, parse_constant=reject)
    except (ValueError, RecursionError):
        return None, 'invalid_json'
    if not isinstance(value, dict):
        return None, 'not_json_object'
    return value, None


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()
