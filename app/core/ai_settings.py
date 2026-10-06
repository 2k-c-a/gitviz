"""AI 기능 설정. 환경변수에서 읽고, 모델 호출 전에 필요한 보안 설정을 검증한다.

검증 결과(문제 목록)에는 URL·키·경로 값을 넣지 않는다.
"""
import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

LOCAL_HOSTS = frozenset({'localhost', '127.0.0.1', '::1'})
DEFAULT_POLICY_VERSIONS = ('code-transfer-v1',)


def _flag(name: str) -> bool:
    return os.getenv(name, '0').strip() == '1'


def _int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, ''))
    except ValueError:
        return default
    return value if value > 0 else default


def _decimal(name: str, default: str) -> Decimal:
    try:
        value = Decimal(os.getenv(name, '').strip() or default)
    except InvalidOperation:
        return Decimal(default)
    return value if value.is_finite() and value >= 0 else Decimal(default)


def _parse_keys(raw: str) -> dict[str, bytes]:
    """'v1:<64hex>,v2:<64hex>' → {version: key}. 형식이 틀린 항목은 버린다(검증에서 부족으로 드러남)."""
    keys = {}
    for item in filter(None, (p.strip() for p in raw.split(','))):
        version, _, hexkey = item.partition(':')
        if version and len(hexkey) == 64:
            try:
                keys[version] = bytes.fromhex(hexkey)
            except ValueError:
                pass
    return keys


def _origin(url: str) -> str | None:
    parts = urlsplit(url.strip())
    if parts.scheme not in {'http', 'https'} or not parts.hostname:
        return None
    return f'{parts.scheme}://{parts.netloc}'.lower()


@dataclass(frozen=True)
class AISettings:
    enabled: bool = False
    allow_external_transfer: bool = False
    policy_versions: tuple[str, ...] = DEFAULT_POLICY_VERSIONS
    encryption_keys: dict = field(default_factory=dict, repr=False)
    active_key_version: str = ''
    allowed_origins: tuple[str, ...] = ()
    cookie_secure: bool = False
    database_ssl_ca: str = ''
    max_files: int = 50
    max_input_bytes: int = 2_000_000
    daily_job_limit: int = 30
    lease_seconds: int = 120
    max_attempts: int = 3
    queue_deadline_seconds: int = 1800
    retry_delay_seconds: int = 30
    provider_profile: str = 'claude-review-v1'
    input_valid_hours: int = 24
    input_retention_days: int = 7
    prepare_time_limit_seconds: int = 120
    budget_currency: str = 'USD'
    user_monthly_budget: Decimal = Decimal('5')
    server_monthly_budget: Decimal = Decimal('50')


def get_ai_settings() -> AISettings:
    """매 호출마다 환경변수를 읽는다(테스트·설정 변경 반영)."""
    raw_origins = os.getenv('AI_ALLOWED_ORIGINS') or os.getenv('FRONTEND_URL', 'http://localhost:8000')
    origins = [o for o in (_origin(v) for v in raw_origins.split(',')) if o]
    policies = os.getenv('AI_POLICY_VERSIONS') or ','.join(DEFAULT_POLICY_VERSIONS)
    return AISettings(
        enabled=_flag('AI_ENABLED'),
        allow_external_transfer=_flag('AI_ALLOW_EXTERNAL_TRANSFER'),
        policy_versions=tuple(p.strip() for p in policies.split(',') if p.strip()),
        encryption_keys=_parse_keys(os.getenv('AI_INPUT_ENCRYPTION_KEYS', '')),
        active_key_version=os.getenv('AI_INPUT_KEY_VERSION', '').strip(),
        allowed_origins=tuple(origins),
        cookie_secure=_flag('SESSION_COOKIE_SECURE'),
        database_ssl_ca=os.getenv('DATABASE_SSL_CA', '').strip(),
        max_files=_int('AI_MAX_FILES', 50),
        max_input_bytes=_int('AI_MAX_INPUT_BYTES', 2_000_000),
        daily_job_limit=_int('AI_DAILY_JOB_LIMIT', 30),
        lease_seconds=_int('AI_LEASE_SECONDS', 120),
        max_attempts=_int('AI_MAX_ATTEMPTS', 3),
        queue_deadline_seconds=_int('AI_QUEUE_DEADLINE_SECONDS', 1800),
        retry_delay_seconds=_int('AI_RETRY_DELAY_SECONDS', 30),
        provider_profile=os.getenv('AI_PROVIDER_PROFILE', '').strip() or 'claude-review-v1',
        input_valid_hours=_int('AI_INPUT_VALID_HOURS', 24),
        input_retention_days=_int('AI_INPUT_RETENTION_DAYS', 7),
        prepare_time_limit_seconds=_int('AI_PREPARE_TIME_LIMIT_SECONDS', 120),
        budget_currency=(os.getenv('AI_BUDGET_CURRENCY', 'USD').strip().upper() or 'USD')[:3],
        user_monthly_budget=_decimal('AI_USER_MONTHLY_BUDGET', '5'),
        server_monthly_budget=_decimal('AI_SERVER_MONTHLY_BUDGET', '50'),
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
    """AI가 켜져 있을 때 모델 호출 전에 충족해야 하는 설정의 문제 목록. 꺼져 있으면 빈 목록."""
    s = settings or get_ai_settings()
    if not s.enabled:
        return []
    problems = []
    if not s.encryption_keys:
        problems.append('AI_INPUT_ENCRYPTION_KEYS에 유효한 키(버전:64자리 hex)가 없습니다.')
    if s.active_key_version not in s.encryption_keys:
        problems.append('AI_INPUT_KEY_VERSION이 설정된 키 버전에 없습니다.')
    if not s.policy_versions:
        problems.append('AI_POLICY_VERSIONS가 비어 있습니다.')
    if not s.allowed_origins:
        problems.append('AI_ALLOWED_ORIGINS 또는 FRONTEND_URL에 유효한 origin이 없습니다.')
    elif not s.cookie_secure and any(urlsplit(o).hostname not in LOCAL_HOSTS for o in s.allowed_origins):
        problems.append('로컬이 아닌 origin에서는 SESSION_COOKIE_SECURE=1이 필요합니다.')
    from app.services.ai.providers.registry import UnknownProfile, get_profile
    try:
        profile = get_profile(s.provider_profile)
    except UnknownProfile:
        problems.append('AI_PROVIDER_PROFILE이 등록된 provider profile이 아닙니다.')
    else:
        if s.allow_external_transfer and profile.provider == 'anthropic' and not os.getenv('ANTHROPIC_API_KEY', '').strip():
            problems.append('외부 전송을 허용하려면 ANTHROPIC_API_KEY가 필요합니다.')
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


def require_valid_ai_settings(settings: AISettings | None = None) -> None:
    problems = validate_ai_settings(settings)
    if problems:
        raise RuntimeError('AI 설정이 올바르지 않습니다: ' + ' / '.join(problems))
