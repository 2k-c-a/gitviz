"""지원하는 제공자·모델(profile) 목록과 provider 생성.

- 사용자는 이 목록에서 제공자와 모델을 고르고 자신의 API key만 등록한다. 임의 모델·URL은 받지 않는다.
- profile은 버전이 고정된 불변 값이다(모델·가격·한도·timeout·프롬프트·schema). 값을 바꾸려면 새 id를 만든다.
- provider는 호출마다 사용자 자격증명을 주입받아 만든다. 서버 공용 key는 없다.
- 다른 제공자·모델로의 자동 대체(fallback)는 없다. 외부 송신 대상이 달라지면 사용자의 확인 범위가 달라지므로
  새 선택과 새 전송 동의가 필요하다.
"""
from dataclasses import dataclass
from decimal import Decimal

from app.core.ai_settings import get_ai_settings
from app.schemas.ai import REVIEW_SCHEMA_VERSION, SUMMARY_SCHEMA_VERSION
from app.services.ai.providers.base import Provider, ProviderProfile

DEFAULT_PROFILE_ID = 'claude-opus-5-5-v1'


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    display_name: str
    key_format: str              # 사용자에게 보이는 key 형식 안내


PROVIDERS: dict[str, ProviderInfo] = {
    'anthropic': ProviderInfo('anthropic', 'Anthropic', 'sk-ant-로 시작하는 API key'),
}


def _anthropic_profile(profile_id: str, display_name: str, model: str, input_price: str, output_price: str,
                       context_window: int) -> ProviderProfile:
    return ProviderProfile(
        id=profile_id, provider='anthropic', model=model, display_name=display_name,
        price_version='2026-09-25', currency='USD',
        input_price_per_mtok=Decimal(input_price), output_price_per_mtok=Decimal(output_price),
        context_window=context_window,
        max_input_tokens=24_000,        # 설계 한도(입력 24,000 토큰 이하)
        max_output_tokens=4_000,        # 설계 한도(출력 최대 4,000, 생각 토큰 포함)
        timeout_seconds=90,             # 모델 호출 90초 제한
        effort='low',
        bytes_per_token=2.5,            # 호출 전 로컬 추정용 보수적 값. 실제 토큰 수는 count_tokens로 확인한다
        summary_prompt='summary-v1', review_prompt='review-v1',
        summary_schema=SUMMARY_SCHEMA_VERSION, review_schema=REVIEW_SCHEMA_VERSION)


PROFILES: dict[str, ProviderProfile] = {p.id: p for p in (
    _anthropic_profile('claude-opus-5-5-v1', 'Claude Opus 5.5', 'claude-opus-5-5', '4.00', '20.00', 1_000_000),
    _anthropic_profile('claude-sonnet-5-5-v1', 'Claude Sonnet 5.5', 'claude-sonnet-5-5', '2.00', '10.00', 1_000_000),
)}


class UnknownProfile(Exception):
    pass


class UnknownProvider(Exception):
    pass


def get_profile(profile_id: str) -> ProviderProfile:
    try:
        return PROFILES[profile_id]
    except KeyError:
        raise UnknownProfile(f'등록되지 않은 provider profile: {profile_id}') from None


def allowed_profile_ids() -> list[str]:
    """서버 관리자가 허용한 profile(AI_ALLOWED_PROFILES). 비어 있으면 등록된 모든 profile."""
    allowed = get_ai_settings().allowed_profiles
    return [pid for pid in PROFILES if not allowed or pid in allowed]


def is_allowed(profile_id: str) -> bool:
    return profile_id in allowed_profile_ids()


def current_profile() -> ProviderProfile:
    """사용자가 모델을 고르기 전의 기본 profile(미리보기 추정 등)."""
    return get_profile(get_ai_settings().provider_profile)


def profiles_of(provider: str) -> list[ProviderProfile]:
    return [p for p in PROFILES.values() if p.provider == provider]


def supported_options() -> list[dict]:
    """사용자에게 보여줄 지원 제공자·모델 목록."""
    allowed = set(allowed_profile_ids())
    out = []
    for info in PROVIDERS.values():
        models = [{
            'profile_id': p.id, 'provider': p.provider, 'model': p.model, 'display_name': p.display_name,
            'currency': p.currency, 'input_price_per_mtok': str(p.input_price_per_mtok),
            'output_price_per_mtok': str(p.output_price_per_mtok), 'price_version': p.price_version,
            'context_window': p.context_window, 'max_input_tokens': p.max_input_tokens,
            'max_output_tokens': p.max_output_tokens, 'allowed': p.id in allowed}
            for p in profiles_of(info.id)]
        out.append({'id': info.id, 'display_name': info.display_name, 'key_format': info.key_format, 'models': models})
    return out


def create_provider(profile: ProviderProfile, *, api_key: str | None = None, **kwargs) -> Provider:
    """사용자 자격증명(api_key)을 주입해 provider를 만든다. 환경변수의 공용 key로 대체하지 않는다."""
    if profile.provider == 'anthropic':
        from app.services.ai.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(profile, api_key=api_key, **kwargs)
    raise UnknownProvider(f'지원하지 않는 provider: {profile.provider}')
