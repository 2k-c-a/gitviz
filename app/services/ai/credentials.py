"""사용자 API key(BYOK)의 등록·교체·삭제와 작업 실행 시의 안전한 사용.

규칙
- key는 사용자별로 AES-GCM 암호화해 저장한다(AAD = 사용자·key 버전 id). 응답·로그·큐·작업 옵션·audit에는 key도,
  key로 복원할 수 있는 값도 넣지 않는다. 화면에는 마스킹된 힌트(앞 7자 + 끝 4자)만 보인다.
- 본인 key만 쓴다. 작업이 쓰는 key는 작업에 고정된 버전이며, 그 버전의 소유자가 작업 소유자와 같아야 한다(DB 복합 FK).
- 교체하면 새 버전이 active가 되고 옛 버전은 retired다. 옛 버전에 고정된 진행 중 작업은 끝까지 옛 key·옛 모델로 실행되어
  설정이 섞이지 않고, 그런 작업이 없어지면 옛 암호문을 지운다. 삭제하면 모든 암호문을 즉시 지우고 고정된 작업을 취소한다.
- key를 확인하는 호출은 코드를 보내지 않지만 남의 key를 시험하는 데 악용되지 않도록 사용자별 시간당 횟수를 제한한다.
"""
import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.ai_settings import get_ai_settings
from app.models.ai_credential import AICredential, AICredentialVersion
from app.models.ai_usage import AuditEvent
from app.schemas.ai import CredentialView, ErrorCode, TransferView
from app.services.ai import job_service
from app.services.ai.access import AIError
from app.services.ai.crypto import InputCryptoError, decrypt_secret, encrypt_secret
from app.services.ai.providers import registry
from app.services.ai.providers.base import ProviderError, ProviderProfile

ACTIVE_JOB_STATUSES = ('queued', 'running', 'retry_wait', 'cancel_requested')
KEY_PATTERNS = {'anthropic': re.compile(r'^sk-ant-[A-Za-z0-9_-]{16,380}$')}
HINT_PREFIX = {'anthropic': 7}
VERIFY_WINDOW = timedelta(hours=1)


class CredentialUnavailable(Exception):
    """작업에 고정된 key를 쓸 수 없다(삭제됨·암호문 삭제됨·복호화 실패·소유자 불일치·고정한 profile이 바뀜)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class RegisterResult:
    credential: CredentialView
    changed: bool
    previous_version: int | None


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


# ── key 형식·마스킹·식별자 ──────────────────────────────────────────────

def normalize_key(provider: str, raw: str) -> str:
    """앞뒤 공백(붙여넣기 줄바꿈)은 지우고, 중간 공백·제어문자·비ASCII·알려진 형식 위반은 거부한다."""
    key = (raw or '').strip()
    pattern = KEY_PATTERNS.get(provider)
    if pattern is None or not key.isascii() or any(c.isspace() or ord(c) < 33 for c in key) or not pattern.match(key):
        raise AIError(ErrorCode.CREDENTIAL_INVALID, 'API key 형식이 올바르지 않습니다.')
    return key


def mask_key(provider: str, key: str) -> str:
    """앞 몇 글자(제공자 접두사)와 끝 4글자만 보인다."""
    return f"{key[:HINT_PREFIX.get(provider, 3)]}…{key[-4:]}"


def key_fingerprint(key: str) -> str:
    """같은 key를 다시 보냈는지 알아보는 식별자(HMAC). 서버 비밀 없이는 key를 추정하는 데 쓸 수 없다."""
    secret = os.getenv('SESSION_SECRET_KEY', '').encode()
    return hmac.new(b'gitviz-ai-credential-fingerprint:' + secret, key.encode(), hashlib.sha256).hexdigest()


def _aad(owner_id: UUID, version_id: UUID) -> bytes:
    return f'credential:{owner_id}:{version_id}'.encode()


# ── 조회 ────────────────────────────────────────────────────────────────

def _in_flight(db: Session, credential_id: UUID) -> int:
    return db.execute(text("""
        SELECT count(*) FROM ai_jobs j JOIN ai_credential_versions v ON v.id = j.credential_version_id
        WHERE v.credential_id = :c AND j.status IN ('queued', 'running', 'retry_wait', 'cancel_requested')"""),
                      {'c': credential_id}).scalar()


def _view(db: Session, credential: AICredential, version: AICredentialVersion) -> CredentialView:
    return CredentialView(provider=credential.provider, version=version.version, key_hint=version.key_hint,
                          preferred_profile_id=credential.preferred_profile_id, validated_at=version.validated_at,
                          created_at=credential.created_at, updated_at=credential.updated_at,
                          in_flight_jobs=_in_flight(db, credential.id))


def get_active(db: Session, owner_id: UUID, provider: str):
    """(credential, 활성 version) 또는 None. 본인 것만 조회된다."""
    row = db.execute(select(AICredential, AICredentialVersion).join(
        AICredentialVersion, (AICredentialVersion.credential_id == AICredential.id)
        & (AICredentialVersion.owner_user_id == AICredential.owner_user_id))
        .where(AICredential.owner_user_id == owner_id, AICredential.provider == provider,
               AICredential.status == 'active', AICredentialVersion.status == 'active')
        .execution_options(populate_existing=True)).one_or_none()
    return (row[0], row[1]) if row else None


def list_credentials(db: Session, owner_id: UUID) -> list[CredentialView]:
    rows = db.execute(select(AICredential, AICredentialVersion).join(
        AICredentialVersion, (AICredentialVersion.credential_id == AICredential.id)
        & (AICredentialVersion.owner_user_id == AICredential.owner_user_id))
        .where(AICredential.owner_user_id == owner_id, AICredential.status == 'active',
               AICredentialVersion.status == 'active').order_by(AICredential.provider)
        .execution_options(populate_existing=True)).all()
    return [_view(db, c, v) for c, v in rows]


def preferred_profile(db: Session, owner_id: UUID) -> ProviderProfile:
    """사용자가 고른 모델(없거나 서버가 허용하지 않으면 기본 profile). 미리보기 추정에 쓴다."""
    for info in registry.PROVIDERS.values():
        found = get_active(db, owner_id, info.id)
        if found and found[0].preferred_profile_id and registry.is_allowed(found[0].preferred_profile_id):
            return registry.get_profile(found[0].preferred_profile_id)
    default = registry.current_profile()
    if registry.is_allowed(default.id):
        return default
    allowed = registry.allowed_profile_ids()
    return registry.get_profile(allowed[0]) if allowed else default


def transfer_view(db: Session, owner_id: UUID, profile: ProviderProfile) -> TransferView:
    found = get_active(db, owner_id, profile.provider)
    return TransferView(provider=profile.provider, profile_id=profile.id, model=profile.model,
                        external_transfer_allowed=get_ai_settings().allow_external_transfer,
                        credential_configured=found is not None,
                        credential_version=found[1].version if found else None,
                        key_hint=found[1].key_hint if found else None)


# ── 등록·교체 ───────────────────────────────────────────────────────────

def _audit(db: Session, owner_id: UUID, action: str, details: dict, request_id: str = '-') -> None:
    db.add(AuditEvent(actor_user_id=owner_id, action=action, object_type='credential', request_id=request_id,
                      details=details))


def _check_verify_limit(db: Session, owner_id: UUID, now: datetime) -> None:
    """key 확인 호출 횟수 제한(시험·남의 key 대입 방지). 횟수는 확인 전에 기록해 실패·지연도 센다."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {'k': f'ai-credential-verify:{owner_id}'})
    used = db.execute(text("""SELECT count(*) FROM audit_events WHERE actor_user_id = :o
                              AND action = 'credential.verify_attempt' AND created_at > :since"""),
                      {'o': owner_id, 'since': now - VERIFY_WINDOW}).scalar()
    if used >= get_ai_settings().credential_verify_limit_per_hour:
        db.rollback()
        raise AIError(ErrorCode.USER_LIMIT, 'API key 확인 횟수 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.',
                      headers={'Retry-After': '3600'})
    db.execute(text("""INSERT INTO audit_events(actor_user_id, action, object_type, request_id, details, created_at)
                       VALUES(:o, 'credential.verify_attempt', 'credential', '-', '{}', :now)"""),
               {'o': owner_id, 'now': now})
    db.commit()


def default_verifier(profile: ProviderProfile, api_key: str) -> None:
    registry.create_provider(profile, api_key=api_key).verify_credential()


def _verify(profile: ProviderProfile, api_key: str, verifier: Callable[[ProviderProfile, str], None]) -> None:
    try:
        verifier(profile, api_key)
    except ProviderError as err:
        kind = err.failure.kind
        if kind in ('auth_failed', 'billing_error'):
            raise AIError(ErrorCode.CREDENTIAL_INVALID, '제공자가 이 API key를 거부했습니다.',
                          details={'reason': kind}) from None
        if kind == 'model_unavailable':
            raise AIError(ErrorCode.CREDENTIAL_INVALID, '이 API key로는 선택한 모델을 사용할 수 없습니다.',
                          details={'reason': kind}) from None
        if kind == 'rate_limited':
            headers = {'Retry-After': str(err.failure.retry_after)} if err.failure.retry_after else {}
            raise AIError(ErrorCode.UPSTREAM_RATE_LIMIT, '제공자의 요청 한도에 도달했습니다.', retryable=True,
                          headers=headers) from None
        raise AIError(ErrorCode.UPSTREAM_UNAVAILABLE, '제공자에 연결할 수 없어 API key를 확인하지 못했습니다.',
                      retryable=True) from None


def _choose_profile(provider: str, requested: str | None, existing: AICredential | None) -> ProviderProfile:
    if requested is not None:
        profile = registry.PROFILES.get(requested)
        if profile is None or profile.provider != provider or not registry.is_allowed(requested):
            raise AIError(ErrorCode.INVALID_SELECTION, '지원하지 않거나 허용되지 않은 모델입니다.')
        return profile
    if existing and existing.preferred_profile_id and registry.is_allowed(existing.preferred_profile_id):
        return registry.get_profile(existing.preferred_profile_id)
    options = [registry.get_profile(pid) for pid in registry.allowed_profile_ids()
               if registry.get_profile(pid).provider == provider]
    if not options:
        raise AIError(ErrorCode.INVALID_SELECTION, '이 제공자에서 허용된 모델이 없습니다.')
    default = registry.current_profile()
    return default if default in options else options[0]


def register_key(db: Session, owner_id: UUID, provider: str, api_key: str, *, preferred_profile_id: str | None = None,
                 verify: bool = True, verifier: Callable[[ProviderProfile, str], None] | None = None,
                 request_id: str = '-', now: datetime | None = None) -> RegisterResult:
    """key를 등록하거나(같은 key면 변경 없음) 새 버전으로 교체한다. 제공자 확인에 실패하면 저장하지 않는다."""
    now = _now(now)
    if provider not in registry.PROVIDERS:
        raise AIError(ErrorCode.INVALID_SELECTION, '지원하지 않는 제공자입니다.')
    key = normalize_key(provider, api_key)
    fingerprint = key_fingerprint(key)
    existing = db.execute(select(AICredential).where(AICredential.owner_user_id == owner_id,
                                                      AICredential.provider == provider)).scalar_one_or_none()
    profile = _choose_profile(provider, preferred_profile_id, existing)
    active = get_active(db, owner_id, provider)
    if active is not None and active[1].key_fingerprint == fingerprint:
        return _unchanged(db, owner_id, active, preferred_profile_id, now)

    validated_at = None
    if verify:
        _check_verify_limit(db, owner_id, now)
        db.rollback()                                    # 외부 호출 중에는 DB transaction을 열어 두지 않는다
        _verify(profile, key, verifier or default_verifier)
        validated_at = now
    try:
        return _store(db, owner_id, provider, key, fingerprint, profile, validated_at, request_id, now)
    except Exception:
        db.rollback()
        raise


def _unchanged(db, owner_id, active, preferred_profile_id, now) -> RegisterResult:
    credential, version = active
    if preferred_profile_id is not None and preferred_profile_id != credential.preferred_profile_id:
        profile = registry.PROFILES.get(preferred_profile_id)
        if profile is None or profile.provider != credential.provider or not registry.is_allowed(preferred_profile_id):
            raise AIError(ErrorCode.INVALID_SELECTION, '지원하지 않거나 허용되지 않은 모델입니다.')
        credential.preferred_profile_id, credential.updated_at = preferred_profile_id, now
        db.commit()
    view = _view(db, credential, version)
    db.commit()
    return RegisterResult(view, False, None)


def _store(db, owner_id, provider, key, fingerprint, profile, validated_at, request_id, now) -> RegisterResult:
    settings = get_ai_settings()
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
               {'k': f'ai-credential:{owner_id}:{provider}'})
    credential = db.execute(select(AICredential).where(AICredential.owner_user_id == owner_id,
                                                        AICredential.provider == provider).with_for_update()
                            .execution_options(populate_existing=True)).scalar_one_or_none()
    previous = None
    if credential is None:
        credential = AICredential(owner_user_id=owner_id, provider=provider, preferred_profile_id=profile.id,
                                  created_at=now, updated_at=now)
        db.add(credential)
        db.flush()
        number = 1
    else:
        current = db.execute(select(AICredentialVersion).where(
            AICredentialVersion.credential_id == credential.id, AICredentialVersion.status == 'active')
            .with_for_update()).scalar_one_or_none()
        if current is not None:
            if current.key_fingerprint == fingerprint:                      # 동시에 같은 key가 먼저 저장됐다
                db.commit()
                return RegisterResult(_view(db, credential, current), False, None)
            previous = current.version
            current.status, current.retired_at = 'retired', now           # 새 작업은 새 key만 쓴다
            db.flush()
        number = db.execute(select(func.coalesce(func.max(AICredentialVersion.version), 0) + 1)
                            .where(AICredentialVersion.credential_id == credential.id)).scalar()
        credential.status, credential.preferred_profile_id, credential.updated_at = 'active', profile.id, now
    version_id = uuid4()
    version = AICredentialVersion(
        id=version_id, credential_id=credential.id, owner_user_id=owner_id, version=number, status='active',
        encrypted_key=encrypt_secret(key.encode(), settings.credential_key_version, _aad(owner_id, version_id)),
        key_version=settings.credential_key_version, key_hint=mask_key(provider, key), key_fingerprint=fingerprint,
        validated_at=validated_at, created_at=now)
    db.add(version)
    db.flush()
    _audit(db, owner_id, 'credential.replaced' if previous else 'credential.registered',
           {'provider': provider, 'version': number, 'previous_version': previous, 'verified': validated_at is not None},
           request_id)
    if previous:
        purge_unused_retired(db, now, credential.id)
    view = _view(db, credential, version)
    db.commit()
    return RegisterResult(view, True, previous)


# ── 삭제 ────────────────────────────────────────────────────────────────

def delete_credential(db: Session, owner_id: UUID, provider: str, *, request_id: str = '-',
                      now: datetime | None = None) -> int:
    """모든 버전의 암호문을 즉시 지우고 그 key에 고정된 진행 중 작업을 취소한다. 취소한 작업 수를 돌려준다."""
    now = _now(now)
    credential = db.execute(select(AICredential).where(
        AICredential.owner_user_id == owner_id, AICredential.provider == provider,
        AICredential.status == 'active').with_for_update()).scalar_one_or_none()
    if credential is None:
        db.rollback()
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '등록된 API key가 없습니다.')
    db.execute(text("""UPDATE ai_credential_versions SET status = 'deleted', encrypted_key = NULL, key_version = NULL,
                       secret_purged_at = COALESCE(secret_purged_at, :now), retired_at = COALESCE(retired_at, :now)
                       WHERE credential_id = :c AND (status <> 'deleted' OR encrypted_key IS NOT NULL)"""),
               {'now': now, 'c': credential.id})
    credential.status, credential.updated_at = 'deleted', now
    jobs = db.execute(text("""SELECT j.id FROM ai_jobs j JOIN ai_credential_versions v ON v.id = j.credential_version_id
                              WHERE v.credential_id = :c AND j.status IN ('queued', 'running', 'retry_wait', 'cancel_requested')"""),
                      {'c': credential.id}).scalars().all()
    _audit(db, owner_id, 'credential.deleted', {'provider': provider, 'jobs_cancelled': len(jobs)}, request_id)
    db.commit()                                          # 암호문 삭제를 먼저 확정한다(이후 새 claim은 key를 열 수 없다)
    for job_id in jobs:
        try:
            job_service.request_cancel(db, job_id, owner_id, now=now)
        except AIError:
            pass                                         # 그 사이 끝났거나 삭제된 작업
    return len(jobs)


# ── 유지관리 ────────────────────────────────────────────────────────────

def purge_unused_retired(db: Session, now: datetime | None = None, credential_id: UUID | None = None) -> int:
    """고정된 진행 중 작업이 없는 옛(retired) 버전의 암호문을 지운다. 호출자의 transaction 안에서 실행된다."""
    result = db.execute(text("""
        UPDATE ai_credential_versions v SET encrypted_key = NULL, key_version = NULL, secret_purged_at = :now
        WHERE v.status = 'retired' AND v.encrypted_key IS NOT NULL AND (CAST(:c AS uuid) IS NULL OR v.credential_id = :c)
          AND NOT EXISTS (SELECT 1 FROM ai_jobs j WHERE j.credential_version_id = v.id
                          AND j.status IN ('queued', 'running', 'retry_wait', 'cancel_requested'))"""),
                        {'now': _now(now), 'c': credential_id})
    return result.rowcount


def run_credential_maintenance(db: Session, *, now: datetime | None = None) -> dict:
    purged = purge_unused_retired(db, now)
    db.commit()
    return {'secrets_purged': purged}


# ── 작업 실행 시 사용 ───────────────────────────────────────────────────

def open_secret_for_job(db: Session, job_id: UUID) -> str:
    """작업에 고정된 key의 평문. 작업 소유자의 key만 열리고(복합 FK + 여기서 다시 확인) 메모리 밖에는 남기지 않는다."""
    row = db.execute(text("""
        SELECT v.id AS version_id, v.owner_user_id, v.status, v.encrypted_key, v.key_version, j.owner_user_id AS job_owner
        FROM ai_jobs j JOIN ai_credential_versions v ON v.id = j.credential_version_id
        WHERE j.id = :j"""), {'j': job_id}).one_or_none()
    if row is None:
        raise CredentialUnavailable('no_credential')
    if row.owner_user_id != row.job_owner:
        raise CredentialUnavailable('owner_mismatch')
    if row.status == 'deleted':
        raise CredentialUnavailable('deleted')
    if row.encrypted_key is None:
        raise CredentialUnavailable('purged')
    try:
        return decrypt_secret(bytes(row.encrypted_key), row.key_version,
                              _aad(row.owner_user_id, row.version_id)).decode()
    except InputCryptoError:
        raise CredentialUnavailable('decrypt_failed') from None


def provider_for_job(db: Session, job_id: UUID, **provider_kwargs):
    """작업에 고정된 profile·key로 provider를 만든다. 서버 공용 key나 다른 모델로 대체하지 않는다."""
    job = db.execute(text("""SELECT provider, model, provider_profile, profile_fingerprint FROM ai_jobs WHERE id = :j"""),
                     {'j': job_id}).one_or_none()
    if job is None or job.provider_profile is None:
        raise CredentialUnavailable('no_credential')
    try:
        profile = registry.get_profile(job.provider_profile)
    except registry.UnknownProfile:
        raise CredentialUnavailable('profile_unknown') from None
    if (profile.fingerprint() != job.profile_fingerprint or profile.provider != job.provider
            or profile.model != job.model):
        raise CredentialUnavailable('profile_changed')   # 고정한 모델·가격·한도가 달라졌다. 같은 동의로 보낼 수 없다
    secret = open_secret_for_job(db, job_id)
    return registry.create_provider(profile, api_key=secret, **provider_kwargs)
