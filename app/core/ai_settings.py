"""AI 기능 설정. 환경변수에서 읽는다.

AI 리뷰·AI 분석은 앱 전용 BYOK다(AD-32 ⑧, AD-33): 사용자의 API key는 앱이 보관하고 실행 요청마다 헤더로 보내며,
서버는 key·코드 원문을 저장하지 않는다. 그래서 서버 AI 스위치(AI_ENABLED)·암호화 키·외부 전송 허용·월 예산 설정은 없다.
검증 결과(문제 목록)에는 URL·키·경로 값을 넣지 않는다.
"""
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

LOCAL_HOSTS = frozenset({'localhost', '127.0.0.1', '::1'})
DEFAULT_POLICY_VERSIONS = ('code-transfer-v1',)


def _flag(name: str) -> bool:
    return os.getenv(name, '0').strip() == '1'


def _int(name: str, default: int, *, allow_zero: bool = False) -> int:
    try:
        value = int(os.getenv(name, ''))
    except ValueError:
        return default
    return value if value > 0 or (allow_zero and value == 0) else default


def _origin(url: str) -> str | None:
    parts = urlsplit(url.strip())
    if parts.scheme not in {'http', 'https'} or not parts.hostname:
        return None
    return f'{parts.scheme}://{parts.netloc}'.lower()


@dataclass(frozen=True)
class AISettings:
    insights_enabled: bool = True                     # AI 분석(앱 BYOK): 서버 비밀 없이 동작하므로 기본으로 켠다(AI_INSIGHTS_ENABLED=0으로 끔)
    policy_versions: tuple[str, ...] = DEFAULT_POLICY_VERSIONS
    allowed_origins: tuple[str, ...] = ()
    cookie_secure: bool = False
    database_ssl_ca: str = ''
    max_files: int = 50
    max_input_bytes: int = 2_000_000
    daily_job_limit: int = 0                          # 0이면 하루 실행 한도 없음(2026-11-03 소유자 결정)
    lease_seconds: int = 120
    max_attempts: int = 3
    retry_delay_seconds: int = 30
    provider_profile: str = 'claude-opus-5-5-v1'      # 사용자가 아직 모델을 고르지 않았을 때의 기본 profile
    allowed_profiles: tuple[str, ...] = ()            # 비어 있으면 등록된 모든 profile 허용
    test_provider_base_url: str = ''                  # 통합 시험 전용: 로컬 가짜 제공자 주소(운영에서는 비워 둔다)
    input_valid_hours: int = 24
    input_retention_days: int = 7
    result_retention_days: int = 30
    prepare_time_limit_seconds: int = 120
    budget_currency: str = 'USD'
    # 사용자 key의 비용은 사용자의 제공자 계정에서 통제한다(AD-33). 사용 기록은 남기되 서버가 지출 상한을 두지 않는다.
    user_monthly_budget: Decimal = Decimal('0')
    server_monthly_budget: Decimal = Decimal('0')


def test_provider_url_problem(url: str) -> str | None:
    """통합 시험용 제공자 주소의 문제. 로컬(localhost·127.0.0.1) http(s) 주소만 허용한다(사용자 API key를 외부 주소로 보내지 않는다).
    앱 시작 때의 설정 검증과 provider 어댑터 생성 때 **둘 다** 쓴다: 앱을 거치지 않는 독립 실행(smoke 등)도 같은 규칙을 지킨다."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
        host, _ = parts.hostname, parts.port
    except ValueError:
        return 'AI_TEST_PROVIDER_BASE_URL을 해석할 수 없습니다.'
    if parts.scheme not in ('http', 'https') or host not in LOCAL_HOSTS or parts.username or parts.password:
        return 'AI_TEST_PROVIDER_BASE_URL은 로컬(localhost·127.0.0.1) 주소만 허용합니다. 사용자 API key를 외부 주소로 보내지 않습니다.'
    return None


def get_ai_settings() -> AISettings:
    """매 호출마다 환경변수를 읽는다(테스트·설정 변경 반영)."""
    raw_origins = os.getenv('AI_ALLOWED_ORIGINS') or os.getenv('FRONTEND_URL', 'http://localhost:8000')
    origins = [o for o in (_origin(v) for v in raw_origins.split(',')) if o]
    policies = os.getenv('AI_POLICY_VERSIONS') or ','.join(DEFAULT_POLICY_VERSIONS)
    return AISettings(
        insights_enabled=os.getenv('AI_INSIGHTS_ENABLED', '1').strip() != '0',
        policy_versions=tuple(p.strip() for p in policies.split(',') if p.strip()),
        allowed_origins=tuple(origins),
        cookie_secure=_flag('SESSION_COOKIE_SECURE'),
        database_ssl_ca=os.getenv('DATABASE_SSL_CA', '').strip(),
        max_files=_int('AI_MAX_FILES', 50),
        max_input_bytes=_int('AI_MAX_INPUT_BYTES', 2_000_000),
        daily_job_limit=_int('AI_DAILY_JOB_LIMIT', 0, allow_zero=True),
        lease_seconds=_int('AI_LEASE_SECONDS', 120),
        max_attempts=_int('AI_MAX_ATTEMPTS', 3),
        retry_delay_seconds=_int('AI_RETRY_DELAY_SECONDS', 30),
        provider_profile=os.getenv('AI_PROVIDER_PROFILE', '').strip() or 'claude-opus-5-5-v1',
        allowed_profiles=tuple(p.strip() for p in os.getenv('AI_ALLOWED_PROFILES', '').split(',') if p.strip()),
        test_provider_base_url=os.getenv('AI_TEST_PROVIDER_BASE_URL', '').strip(),
        input_valid_hours=_int('AI_INPUT_VALID_HOURS', 24),
        input_retention_days=_int('AI_INPUT_RETENTION_DAYS', 7),
        result_retention_days=_int('AI_RESULT_RETENTION_DAYS', 30),
        prepare_time_limit_seconds=_int('AI_PREPARE_TIME_LIMIT_SECONDS', 120),
        budget_currency=(os.getenv('AI_BUDGET_CURRENCY', 'USD').strip().upper() or 'USD')[:3],
    )


def same_physical_database(sync_url: str, async_url: str) -> bool:
    """동기/비동기 엔진이 같은 호스트·DB·사용자를 가리키는지 확인한다(풀러 포트 차이는 허용)."""
    try:
        a, b = make_url(sync_url), make_url(async_url)
    except Exception:
        return False
    if not (a.host and a.database):
        return False
    return ((a.host.lower(), a.database, a.username or '') ==
            ((b.host or '').lower(), b.database or '', b.username or ''))


def is_remote_database(url: str) -> bool:
    try:
        return (make_url(url).host or '').lower() not in LOCAL_HOSTS
    except Exception:
        return True


def validate_ai_settings(settings: AISettings | None = None) -> list[str]:
    """AI 설정의 문제 목록(운영 점검 도구가 쓴다). 서버 비밀값은 더 이상 필요하지 않다(AD-33)."""
    s = settings or get_ai_settings()
    problems = []
    if not s.policy_versions:
        problems.append('AI_POLICY_VERSIONS가 비어 있습니다.')
    if not s.allowed_origins:
        problems.append('AI_ALLOWED_ORIGINS 또는 FRONTEND_URL에 유효한 origin이 없습니다.')
    elif not s.cookie_secure and any(urlsplit(o).hostname not in LOCAL_HOSTS for o in s.allowed_origins):
        problems.append('로컬이 아닌 origin에서는 SESSION_COOKIE_SECURE=1이 필요합니다.')
    from app.services.ai.providers.registry import PROFILES
    for name in (s.provider_profile, *s.allowed_profiles):
        if name not in PROFILES:
            problems.append('AI_PROVIDER_PROFILE/AI_ALLOWED_PROFILES에 등록되지 않은 provider profile이 있습니다.')
            break
    else:
        if s.allowed_profiles and s.provider_profile not in s.allowed_profiles:
            problems.append('AI_PROVIDER_PROFILE이 AI_ALLOWED_PROFILES에 포함돼 있지 않습니다.')
    problem = test_provider_url_problem(s.test_provider_base_url)
    if problem:
        problems.append(problem)
    if os.getenv('AI_OLLAMA_ENABLED', '0') == '1':
        import re
        from app.services.ai.providers.json_provider import local_endpoint, ProviderConfigError
        try:
            local_endpoint(os.getenv('AI_OLLAMA_URL', 'http://127.0.0.1:11434'))
        except ProviderConfigError:
            problems.append('AI_OLLAMA_URL은 같은 PC의 loopback HTTP 주소와 포트여야 합니다.')
        if not re.fullmatch(r'(?:sha256:)?[0-9a-f]{64}', os.getenv('AI_OLLAMA_MODEL_DIGEST', '')):
            problems.append('AI_OLLAMA_MODEL_DIGEST에 설치된 모델의 전체 digest가 필요합니다.')
        model = os.getenv('AI_OLLAMA_MODEL', 'qwen2.5-coder:7b')
        if not re.fullmatch(r'[a-zA-Z0-9._/-]+:[a-zA-Z0-9._-]+', model) or model.endswith(':cloud'):
            problems.append('AI_OLLAMA_MODEL은 설치된 로컬 모델의 이름:태그여야 합니다.')
    for runtime, prefix, port in (('LM Studio', 'LMSTUDIO', 1234), ('KoboldCpp', 'KOBOLDCPP', 5001)):
        if os.getenv(f'AI_{prefix}_ENABLED', '0') != '1':
            continue
        from app.services.ai.providers.json_provider import local_endpoint, ProviderConfigError
        try:
            local_endpoint(os.getenv(f'AI_{prefix}_URL', f'http://127.0.0.1:{port}'))
        except ProviderConfigError:
            problems.append(f'AI_{prefix}_URL은 같은 PC의 loopback HTTP 주소와 포트여야 합니다.')
        if not os.getenv(f'AI_{prefix}_MODEL', '').strip():
            problems.append(f'AI_{prefix}_MODEL에 /v1/models의 모델 id가 필요합니다.')
        try:
            context = int(os.getenv(f'AI_{prefix}_CONTEXT_WINDOW', '8192'))
            inputs = int(os.getenv(f'AI_{prefix}_MAX_INPUT_TOKENS', '4000'))
            outputs = int(os.getenv(f'AI_{prefix}_MAX_OUTPUT_TOKENS', '4000'))
            timeout = int(os.getenv(f'AI_{prefix}_TIMEOUT_SECONDS', '180'))
            if min(context, inputs, outputs, timeout) <= 0 or inputs + outputs > context:
                raise ValueError()
        except ValueError:
            problems.append(f'{runtime}의 토큰·시간 한도는 양수이고 입력+출력 한도는 context 이하여야 합니다.')
    sync_url = os.getenv('DATABASE_URL', '')
    async_url = os.getenv('DATABASE_URL_ASYNC') or sync_url
    if not sync_url:
        problems.append('DATABASE_URL이 설정되지 않았습니다.')
    elif not same_physical_database(sync_url, async_url):
        problems.append('DATABASE_URL과 DATABASE_URL_ASYNC가 같은 DB를 가리키지 않습니다.')
    elif is_remote_database(sync_url):
        if not s.database_ssl_ca:
            problems.append('원격 DB는 DATABASE_SSL_CA(CA 인증서 경로)가 필요합니다.')
        elif not Path(s.database_ssl_ca).is_file():
            problems.append('DATABASE_SSL_CA 파일을 읽을 수 없습니다.')
    return problems
