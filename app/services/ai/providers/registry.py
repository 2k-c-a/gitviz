"""지원하는 제공자·모델(profile) 목록과 provider 생성.

- 사용자는 이 목록에서 제공자와 모델을 고르고 자신의 API key만 등록한다. 임의 모델·URL은 받지 않는다.
- profile은 버전이 고정된 불변 값이다(모델·가격·한도·timeout·프롬프트·schema). 값을 바꾸려면 새 id를 만든다.
- provider는 호출마다 사용자 자격증명을 주입받아 만든다. 서버 공용 key는 없다.
- 다른 제공자·모델로의 자동 대체(fallback)는 없다. 외부 송신 대상이 달라지면 사용자의 확인 범위가 달라지므로
  새 선택과 새 전송 동의가 필요하다.
"""
from dataclasses import dataclass, replace
import os
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
    'openai': ProviderInfo('openai', 'OpenAI', 'sk-로 시작하는 API key'),
    'gemini': ProviderInfo('gemini', 'Google Gemini', 'Google AI Studio API key'),
    'ollama': ProviderInfo('ollama', 'Ollama (같은 PC)', 'API key 불필요'),
    'lmstudio': ProviderInfo('lmstudio', 'LM Studio (같은 PC)', 'API key 불필요'),
    'koboldcpp': ProviderInfo('koboldcpp', 'KoboldCpp (같은 PC)', 'API key 불필요'),
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

PROFILES['gpt-5.4-v1'] = replace(PROFILES[DEFAULT_PROFILE_ID], id='gpt-5.4-v1', provider='openai',
    model='gpt-5.4-2026-03-05', display_name='GPT-5.4', price_version='2026-10-07',
    input_price_per_mtok=Decimal('2.50'), output_price_per_mtok=Decimal('15'), effort='none', bytes_per_token=1)
PROFILES['gemini-3.5-flash-lite-v1'] = replace(PROFILES[DEFAULT_PROFILE_ID], id='gemini-3.5-flash-lite-v1',
    provider='gemini', model='gemini-3.5-flash-lite', display_name='Gemini 3.5 Flash-Lite',
    price_version='2026-10-07', input_price_per_mtok=Decimal('0.30'), output_price_per_mtok=Decimal('2.50'),
    bytes_per_token=1)
PROFILES['ollama-local-v1'] = replace(PROFILES[DEFAULT_PROFILE_ID], id='ollama-local-v1', provider='ollama',
    model='qwen2.5-coder:7b', display_name='로컬 Ollama', price_version='local-no-provider-fee',
    input_price_per_mtok=Decimal('0'), output_price_per_mtok=Decimal('0'), requires_api_key=False,
    execution_location='local', context_window=32768, max_input_tokens=24000, bytes_per_token=1,
    timeout_seconds=90, endpoint='http://127.0.0.1:11434')

LOCAL_RUNTIMES = {'ollama': ('OLLAMA', 11434), 'lmstudio': ('LMSTUDIO', 1234),
                  'koboldcpp': ('KOBOLDCPP', 5001)}
for _runtime in ('lmstudio', 'koboldcpp'):
    _id = _runtime + '-local-v1'
    PROFILES[_id] = replace(PROFILES['ollama-local-v1'], id=_id, provider=_runtime,
        model='', display_name=PROVIDERS[_runtime].display_name, context_window=8192,
        max_input_tokens=4000, max_output_tokens=4000, timeout_seconds=180,
        endpoint=f'http://127.0.0.1:{LOCAL_RUNTIMES[_runtime][1]}',
        summary_prompt='summary-v2', review_prompt='review-v2',
        summary_schema='summary-output-v2', review_schema='review-output-v2')

# Keep v1 immutable so accepted jobs retain their consent and cost limits.
# The larger input allowance is a new selectable profile for each cloud model.
for _previous_id in ('claude-opus-5-5-v1', 'claude-sonnet-5-5-v1', 'gpt-5.4-v1', 'gemini-3.5-flash-lite-v1'):
    _previous = PROFILES[_previous_id]
    _expanded_id = _previous_id.removesuffix('-v1') + '-v2'
    PROFILES[_expanded_id] = replace(_previous, id=_expanded_id,
        display_name=_previous.display_name + ' (입력 64K)', max_input_tokens=64_000)


for _previous_id in ('claude-opus-5-5-v2', 'claude-sonnet-5-5-v2', 'gpt-5.4-v2', 'gemini-3.5-flash-lite-v2'):
    _previous = PROFILES[_previous_id]
    _expanded_id = _previous_id.removesuffix('-v2') + '-v3'
    PROFILES[_expanded_id] = replace(_previous, id=_expanded_id,
        display_name=_previous.display_name + ' · 확장 출력', summary_prompt='summary-v2', review_prompt='review-v2',
        summary_schema='summary-output-v2', review_schema='review-output-v2')


for _previous_id in ('claude-opus-5-5-v3', 'claude-sonnet-5-5-v3', 'gpt-5.4-v3', 'gemini-3.5-flash-lite-v3'):
    _previous = PROFILES[_previous_id]
    _expanded_id = _previous_id.removesuffix('-v3') + '-v4'
    PROFILES[_expanded_id] = replace(_previous, id=_expanded_id,
        display_name=PROFILES[_previous_id.removesuffix('-v3') + '-v1'].display_name,
        max_input_tokens=128_000, max_output_tokens=16_384, timeout_seconds=180)


class UnknownProfile(Exception):
    pass


class UnknownProvider(Exception):
    pass


def get_profile(profile_id: str) -> ProviderProfile:
    if profile_id in ('lmstudio-local-v1', 'koboldcpp-local-v1'):
        profile = PROFILES[profile_id]
        prefix, port = LOCAL_RUNTIMES[profile.provider]
        return replace(profile, model=os.getenv(f'AI_{prefix}_MODEL', '').strip(),
            endpoint=os.getenv(f'AI_{prefix}_URL', f'http://127.0.0.1:{port}').strip(),
            context_window=int(os.getenv(f'AI_{prefix}_CONTEXT_WINDOW', '8192')),
            max_input_tokens=int(os.getenv(f'AI_{prefix}_MAX_INPUT_TOKENS', '4000')),
            max_output_tokens=int(os.getenv(f'AI_{prefix}_MAX_OUTPUT_TOKENS', '4000')),
            timeout_seconds=int(os.getenv(f'AI_{prefix}_TIMEOUT_SECONDS', '180')))
    if profile_id == 'ollama-local-v1':
        return replace(PROFILES[profile_id], model=os.getenv('AI_OLLAMA_MODEL', 'qwen2.5-coder:7b').strip(),
                       model_digest=os.getenv('AI_OLLAMA_MODEL_DIGEST', '').strip(),
                       endpoint=os.getenv('AI_OLLAMA_URL', 'http://127.0.0.1:11434').strip())
    try:
        return PROFILES[profile_id]
    except KeyError:
        raise UnknownProfile(f'등록되지 않은 provider profile: {profile_id}') from None


def allowed_profile_ids() -> list[str]:
    """서버 관리자가 허용한 profile(AI_ALLOWED_PROFILES). 비어 있으면 등록된 모든 profile."""
    allowed = get_ai_settings().allowed_profiles
    return [pid for pid in PROFILES if (not allowed or pid in allowed)
            and (PROFILES[pid].execution_location != 'local'
                 or os.getenv('AI_' + LOCAL_RUNTIMES[PROFILES[pid].provider][0] + '_ENABLED', '0') == '1')]


def is_allowed(profile_id: str) -> bool:
    return profile_id in allowed_profile_ids()


def current_profile() -> ProviderProfile:
    """사용자가 모델을 고르기 전의 기본 profile(미리보기 추정 등)."""
    return get_profile(get_ai_settings().provider_profile)


def profiles_of(provider: str) -> list[ProviderProfile]:
    profiles = [get_profile(p.id) for p in PROFILES.values() if p.provider == provider]
    return sorted(profiles, key=lambda p: (p.max_input_tokens, p.id.rsplit('-v', 1)[-1]), reverse=True)


def supported_options() -> list[dict]:
    """사용자에게 보여줄 지원 제공자·모델 목록."""
    allowed = set(allowed_profile_ids())
    out = []
    for info in PROVIDERS.values():
        models = [{
            'profile_id': p.id, 'provider': p.provider, 'model': p.model,
            'display_name': (PROFILES[p.id.removesuffix('-v3') + '-v1'].display_name
                             if p.id.endswith('-v3') else p.display_name),
            'currency': p.currency, 'input_price_per_mtok': str(p.input_price_per_mtok),
            'output_price_per_mtok': str(p.output_price_per_mtok), 'price_version': p.price_version,
            'context_window': p.context_window, 'max_input_tokens': p.max_input_tokens,
            'max_output_tokens': p.max_output_tokens, 'allowed': p.id in allowed,
            'requires_api_key': p.requires_api_key, 'execution_location': p.execution_location}
            for p in profiles_of(info.id) if p.id.endswith('-v4') or p.execution_location == 'local']
        out.append({'id': info.id, 'display_name': info.display_name, 'key_format': info.key_format, 'models': models})
    return out


def create_provider(profile: ProviderProfile, *, api_key: str | None = None, **kwargs) -> Provider:
    """사용자 자격증명(api_key)을 주입해 provider를 만든다. 환경변수의 공용 key로 대체하지 않는다."""
    if profile.provider == 'anthropic':
        from app.services.ai.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(profile, api_key=api_key, **kwargs)
    from app.services.ai.providers.json_provider import OpenAIProvider, GeminiProvider, OllamaProvider, LocalChatProvider
    adapter = {'openai': OpenAIProvider, 'gemini': GeminiProvider, 'ollama': OllamaProvider,
               'lmstudio': LocalChatProvider, 'koboldcpp': LocalChatProvider}.get(profile.provider)
    if adapter:
        return adapter(profile, api_key=api_key, **kwargs)
    raise UnknownProvider(f'지원하지 않는 provider: {profile.provider}')
