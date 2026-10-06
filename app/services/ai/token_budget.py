"""호출 전 토큰·비용 계산과 한도 검사.

- 로컬 추정(estimate_*)은 코드를 외부로 보내지 않고 쓸 수 있다(전송 미리보기용). 제공자 토크나이저를 쓸 수 없으므로
  profile의 보수적 bytes_per_token으로 계산하며, 정확한 값은 동의 이후 count_tokens로 다시 확인한다.
- 한도를 넘는 입력은 호출 전에 거부한다(check_input_limit). 입력 토큰은 profile.max_input_tokens(설계 기본 24,000)
  이하, 입력+최대 출력은 모델 컨텍스트 이하여야 한다.
- 비용은 Decimal이며 올림한다. 예약액은 최대 입력+최대 출력 기준, 정산액은 실제 usage 기준이다.
"""
import math
from decimal import ROUND_CEILING, Decimal

from app.services.ai import prompts
from app.services.ai.providers.base import ProviderProfile, TokenLimitExceeded, Usage

CENT_FRACTION = Decimal('0.000001')
MILLION = Decimal(1_000_000)
MESSAGE_OVERHEAD_TOKENS = 50           # role·구조 표기 등 메시지 틀
FILE_HEADER_BYTES = 160                # 파일 블록 머리줄과 경계 표시
DESCRIPTION_OVERHEAD_BYTES = 120


def estimate_prompt_tokens(profile: ProviderProfile, system: str, user: str) -> int:
    """실제 system·user 문자열의 로컬 추정."""
    size = len(system.encode('utf-8')) + len(user.encode('utf-8'))
    return math.ceil(size / profile.bytes_per_token) + MESSAGE_OVERHEAD_TOKENS


def estimate_input_tokens(profile: ProviderProfile, kind: str, *, payload_bytes: int, description_chars: int,
                          file_count: int) -> int:
    """미리보기용: 프롬프트를 아직 만들지 않았을 때 파일 크기·개수로 계산한 로컬 추정.

    system 프롬프트 크기, 파일마다의 머리줄, 줄 번호 표기(줄당 약 14바이트)를 포함한다.
    """
    system_bytes = len(prompts.system_template(kind).encode('utf-8'))
    # payload는 unified diff 바이트다. 렌더링은 줄마다 번호 머리를 붙이므로 그만큼 늘어난다(보수적 배율).
    rendered_payload = math.ceil(payload_bytes * prompts.RENDER_EXPANSION)
    total = (system_bytes + rendered_payload + description_chars * 3 + DESCRIPTION_OVERHEAD_BYTES
             + FILE_HEADER_BYTES * file_count)
    return math.ceil(total / profile.bytes_per_token) + MESSAGE_OVERHEAD_TOKENS


def check_input_limit(profile: ProviderProfile, input_tokens: int, max_output_tokens: int | None = None) -> None:
    """한도를 넘으면 TokenLimitExceeded. 호출(과금) 전에 호출한다."""
    output = profile.max_output_tokens if max_output_tokens is None else max_output_tokens
    if input_tokens > profile.max_input_tokens:
        raise TokenLimitExceeded('input', input_tokens, profile.max_input_tokens)
    if input_tokens + output > profile.context_window:
        raise TokenLimitExceeded('context', input_tokens + output, profile.context_window)


def _ceil(amount: Decimal) -> Decimal:
    return amount.quantize(CENT_FRACTION, rounding=ROUND_CEILING)


def cost_of(profile: ProviderProfile, input_tokens: int, output_tokens: int) -> Decimal:
    return _ceil(Decimal(input_tokens) * profile.input_price_per_mtok / MILLION
                 + Decimal(output_tokens) * profile.output_price_per_mtok / MILLION)


def calculate_cost(profile: ProviderProfile, usage: Usage) -> Decimal:
    """실제 usage의 비용(추정 단가 기준). 캐시 토큰은 보수적으로 입력 단가로 합산한다."""
    return cost_of(profile, usage.billed_input_tokens, usage.output_tokens)


def max_cost(profile: ProviderProfile, estimated_input_tokens: int, max_output_tokens: int | None = None) -> Decimal:
    """비용 예약액: 추정 입력 + 최대 출력이 모두 과금된다고 가정한 상한."""
    output = profile.max_output_tokens if max_output_tokens is None else max_output_tokens
    return cost_of(profile, estimated_input_tokens, output)
