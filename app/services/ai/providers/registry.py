"""고정된 provider profile 목록과 provider 생성. 설정에서 고른 profile 이름만 쓸 수 있고 임의 모델·URL은 받지 않는다.

다른 제공자·모델로의 자동 대체(fallback)는 없다. 외부 송신 대상이 달라지면 사용자의 확인 범위가 달라지므로 새 profile을
만들어 명시적으로 선택해야 한다.
"""
from decimal import Decimal

from app.core.ai_settings import get_ai_settings
from app.schemas.ai import REVIEW_SCHEMA_VERSION, SUMMARY_SCHEMA_VERSION
from app.services.ai.providers.base import Provider, ProviderProfile

DEFAULT_PROFILE_ID = 'claude-review-v1'

PROFILES: dict[str, ProviderProfile] = {
    DEFAULT_PROFILE_ID: ProviderProfile(
        id=DEFAULT_PROFILE_ID, provider='anthropic', model='claude-opus-5-5',
        price_version='2026-09-25', currency='USD',
        input_price_per_mtok=Decimal('4.00'), output_price_per_mtok=Decimal('20.00'),
        context_window=1_000_000,
        max_input_tokens=24_000,        # 설계 한도(입력 24,000 토큰 이하)
        max_output_tokens=4_000,        # 설계 한도(출력 최대 4,000, 생각 토큰 포함)
        timeout_seconds=90,             # 모델 호출 90초 제한
        effort='low',
        bytes_per_token=2.5,            # 호출 전 로컬 추정용 보수적 값. 실제 토큰 수는 count_tokens로 확인한다
        summary_prompt='summary-v1', review_prompt='review-v1',
        summary_schema=SUMMARY_SCHEMA_VERSION, review_schema=REVIEW_SCHEMA_VERSION),
}


class UnknownProfile(Exception):
    pass


class UnknownProvider(Exception):
    pass


def get_profile(profile_id: str) -> ProviderProfile:
    try:
        return PROFILES[profile_id]
    except KeyError:
        raise UnknownProfile(f'등록되지 않은 provider profile: {profile_id}') from None


def current_profile() -> ProviderProfile:
    return get_profile(get_ai_settings().provider_profile)


def create_provider(profile: ProviderProfile | None = None, **kwargs) -> Provider:
    profile = profile or current_profile()
    if profile.provider == 'anthropic':
        from app.services.ai.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(profile, **kwargs)
    raise UnknownProvider(f'지원하지 않는 provider: {profile.provider}')
