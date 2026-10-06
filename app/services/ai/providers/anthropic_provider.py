"""Anthropic Messages API adapter (공식 SDK).

- 호출 한 번 = HTTP 요청 한 번. SDK의 자동 재시도(max_retries)를 끈다: 재시도는 비용 예약과 함께 job 계층이 결정한다.
- 요청에는 tools·tool_choice·fallbacks·beta·sampling 옵션이 없다. 모델이 도구를 실행하거나 다른 모델로 바뀔 수 없다.
- 구조화 출력(output_config.format)과 effort만 사용한다. thinking은 모델 기본(적응형)에 맡기며 끌 수 없다.
- 오류는 ProviderFailure로 분류한다(과금·재시도 판단). 요청·응답 본문과 API key는 예외·repr·로그에 넣지 않는다.
"""
import time
from decimal import Decimal

import anthropic
import httpx2

from app.services.ai import prompts, token_budget
from app.services.ai.providers.base import (GenerationRequest, ProviderError, ProviderFailure, ProviderProfile,
                                            ProviderResponse, TokenLimitExceeded, Usage, parse_json_object)

COUNT_SCHEMA_MARGIN_TOKENS = 400        # count_tokens에 포함되지 않는 출력 schema 지시분의 여유
_SAFE_CONNECT_ERRORS = (httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.PoolTimeout, httpx2.ProxyError,
                        httpx2.UnsupportedProtocol)
_FINISH_BY_STOP_REASON = {'end_turn': 'complete', 'max_tokens': 'truncated', 'refusal': 'refused'}
MAX_RETRY_AFTER = 3600


class ProviderConfigError(Exception):
    pass


def _retry_after(headers) -> int | None:
    try:
        value = int(float(headers.get('retry-after', '')))
    except (TypeError, ValueError):
        return None
    return max(1, min(value, MAX_RETRY_AFTER))


class AnthropicProvider:
    def __init__(self, profile: ProviderProfile, *, client: anthropic.Anthropic | None = None,
                 api_key: str | None = None):
        if profile.provider != 'anthropic':
            raise ProviderConfigError('profile이 anthropic이 아닙니다.')
        self.profile = profile
        if client is None:
            # 서버 공용 key로 대체하지 않는다. 호출하는 쪽이 작업에 고정된 사용자 key를 주입해야 한다.
            if not api_key:
                raise ProviderConfigError('사용자 API key가 주입되지 않았습니다.')
            client = anthropic.Anthropic(api_key=api_key, timeout=float(profile.timeout_seconds), max_retries=0)
        self.client = client

    def __repr__(self) -> str:
        return f'AnthropicProvider(profile={self.profile.id!r})'

    # ── 요청 ──
    def _messages(self, request: GenerationRequest) -> list[dict]:
        return [{'role': 'user', 'content': request.user}]

    def _max_output(self, request: GenerationRequest) -> int:
        limit = self.profile.max_output_tokens if request.max_output_tokens is None else request.max_output_tokens
        if limit < 1 or limit > self.profile.max_output_tokens:
            raise ValueError('max_output_tokens가 profile 한도를 벗어났습니다.')
        return limit

    def _params(self, request: GenerationRequest) -> dict:
        return {
            'model': self.profile.model,
            'max_tokens': self._max_output(request),
            'system': request.system,
            'messages': self._messages(request),
            'output_config': {'effort': self.profile.effort,
                              'format': {'type': 'json_schema', 'schema': prompts.output_schema(request.kind)}},
        }

    def estimate_tokens(self, request: GenerationRequest) -> int:
        """외부로 보내지 않는 로컬 추정(보수적)."""
        return token_budget.estimate_prompt_tokens(self.profile, request.system, request.user)

    def count_tokens(self, request: GenerationRequest) -> int:
        """제공자의 토큰 수 계산(코드가 전송되므로 사용자 동의 이후에만 호출한다). 과금되지 않는 호출이다."""
        try:
            result = self.client.messages.count_tokens(model=self.profile.model, system=request.system,
                                                       messages=self._messages(request))
        except Exception as exc:
            raise ProviderError(self.classify_error(exc, billable=False)) from None
        return int(result.input_tokens) + COUNT_SCHEMA_MARGIN_TOKENS

    def verify_credential(self) -> None:
        """사용자 key가 이 profile의 모델을 쓸 수 있는지 제공자에 확인한다(모델 조회: 코드를 보내지 않고 과금되지 않는다)."""
        try:
            self.client.models.retrieve(self.profile.model)
        except Exception as exc:
            raise ProviderError(self.classify_error(exc, billable=False)) from None

    def generate(self, request: GenerationRequest) -> ProviderResponse:
        profile = self.profile
        if request.kind not in ('summary', 'review'):
            raise ValueError('kind는 summary 또는 review여야 합니다.')
        params_max = self._max_output(request)
        try:                                              # 한도를 넘는 입력은 호출(과금) 전에 거부한다
            token_budget.check_input_limit(profile, self.estimate_tokens(request), params_max)
        except TokenLimitExceeded:
            raise ProviderError(ProviderFailure('input_too_large', retryable=False, outcome_uncertain=False)) from None
        started = time.monotonic()
        try:
            message = self.client.messages.create(**self._params(request))
        except Exception as exc:
            raise ProviderError(self.classify_error(exc)) from None
        if not self._well_formed(message):                # 처리 여부를 알 수 없는 응답: 자동 재호출 금지
            raise ProviderError(ProviderFailure('response_lost', retryable=False, outcome_uncertain=True))
        return self._to_response(message, time.monotonic() - started)

    @staticmethod
    def _well_formed(message) -> bool:
        """정상 응답은 type=message, role=assistant, content 목록을 가진다. 게이트웨이 HTML이나 빈 JSON이 200으로
        오면 SDK는 문자열이나 빈 Message를 그대로 돌려주므로 여기서 걸러낸다."""
        return (isinstance(message, anthropic.types.Message) and message.type == 'message'
                and message.role == 'assistant' and isinstance(message.content, list))

    # ── 응답 ──
    def _to_response(self, message, latency: float) -> ProviderResponse:
        blocks = list(getattr(message, 'content', None) or [])
        text = ''.join(getattr(b, 'text', '') or '' for b in blocks if getattr(b, 'type', None) == 'text')
        unexpected = any(getattr(b, 'type', None) not in ('text', 'thinking', 'redacted_thinking') for b in blocks)
        finish = _FINISH_BY_STOP_REASON.get(getattr(message, 'stop_reason', None), 'incomplete')
        if unexpected:
            finish = 'incomplete'                         # 도구를 주지 않았으므로 tool 호출 블록은 있을 수 없다
        elif finish == 'complete' and not text.strip():
            finish = 'empty'
        usage = self._usage(getattr(message, 'usage', None))
        parsed, parse_error = (parse_json_object(text) if finish == 'complete' else (None, None))
        details = getattr(message, 'stop_details', None)
        return ProviderResponse(
            request_id=getattr(message, '_request_id', None), model=getattr(message, 'model', None), finish=finish,
            text=text, parsed=parsed, parse_error=parse_error, usage=usage,
            cost=token_budget.calculate_cost(self.profile, usage) if usage else None, latency_seconds=latency,
            refusal_category=getattr(details, 'category', None) if finish == 'refused' else None)

    @staticmethod
    def _usage(raw) -> Usage | None:
        """입력·출력 토큰이 모두 올바른 정수일 때만 사용량으로 인정한다(없으면 비용을 알 수 없다)."""
        def count(name, default=None):
            value = getattr(raw, name, default)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        if raw is None:
            return None
        input_tokens, output_tokens = count('input_tokens'), count('output_tokens')
        if input_tokens is None or output_tokens is None:
            return None
        return Usage(input_tokens=input_tokens, output_tokens=output_tokens,
                     cache_creation_input_tokens=count('cache_creation_input_tokens', 0) or 0,
                     cache_read_input_tokens=count('cache_read_input_tokens', 0) or 0)

    # ── 실패 분류 ──
    def classify_error(self, exc: BaseException, *, billable: bool = True) -> ProviderFailure:
        """billable=False(count_tokens)이면 결과가 불명확해도 과금되지 않으므로 outcome_uncertain을 쓰지 않는다."""
        def failure(kind, *, retryable=False, uncertain=False, **extra):
            return ProviderFailure(kind, retryable=retryable, outcome_uncertain=uncertain and billable, **extra)

        if isinstance(exc, anthropic.APIStatusError):
            status = exc.status_code
            request_id = getattr(exc, 'request_id', None)
            common = {'http_status': status, 'request_id': request_id}
            if status == 429:
                return failure('rate_limited', retryable=True, retry_after=_retry_after(exc.response.headers), **common)
            if status in (408, 504):                      # 중간 게이트웨이/서버 timeout: 처리됐을 수 있다
                return failure('response_lost', uncertain=True, **common)
            if status >= 500:                             # 500·502·503·529: 처리되지 않은 서버 오류
                return failure('server_error', retryable=True, **common)
            kinds = {401: 'auth_failed', 403: 'auth_failed', 402: 'billing_error', 404: 'model_unavailable',
                     413: 'input_too_large'}
            return failure(kinds.get(status, 'request_rejected'), **common)
        if isinstance(exc, anthropic.APITimeoutError):
            if isinstance(exc.__cause__, _SAFE_CONNECT_ERRORS):
                return failure('connection_failed', retryable=True)      # 요청이 서버에 닿기 전
            return failure('timeout', uncertain=True)                    # 응답을 기다리다 시간 초과
        if isinstance(exc, anthropic.APIConnectionError):
            if isinstance(exc.__cause__, _SAFE_CONNECT_ERRORS):
                return failure('connection_failed', retryable=True)
            return failure('response_lost', uncertain=True)              # 전송 도중/응답 도중 연결 끊김
        return failure('unknown_error', uncertain=True)
