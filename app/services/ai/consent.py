"""전송 동의 확인과 작업에 고정할 실행 설정(PinnedConfig).

사용자는 미리보기에서 (1) 보낼 코드 범위(manifest_hash), (2) 제공자·모델, (3) 쓸 API key 버전을 확인한 뒤 동의한다.
서버는 동의 내용이 지금의 입력·허용 목록·key와 같을 때만 그 설정을 작업에 고정한다. 하나라도 다르면 PREVIEW_CHANGED로
새 확인을 요구한다. 고정된 설정은 이후 key를 바꾸거나 profile 선택이 달라져도 바뀌지 않는다.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.ai_settings import get_ai_settings
from app.models.ai_input import AIInput
from app.schemas.ai import ErrorCode, InputManifest, PRTransferMetadata, TransferConsent, canonical_hash
from app.services.ai import credentials, input_builder
from app.services.ai.access import AIError, ensure_owner
from app.services.ai.providers import registry
from app.services.ai.providers.base import ProviderProfile


@dataclass(frozen=True)
class PinnedConfig:
    provider: str
    profile: ProviderProfile
    profile_fingerprint: str
    credential_version_id: UUID | None
    credential_version: int | None
    key_hint: str | None
    input_id: UUID
    repository_id: int
    manifest_hash: str
    policy_version: str

    @property
    def model(self) -> str:
        return self.profile.model


def resolve_consent(db: Session, owner_id: UUID, consent: TransferConsent, *, now: datetime | None = None) -> PinnedConfig:
    """동의를 현재 상태와 대조해 고정할 설정을 돌려준다. 본인 입력·본인 key만 쓸 수 있다."""
    now = now or datetime.now(timezone.utc)
    settings = get_ai_settings()
    if consent.policy_version not in settings.policy_versions:
        raise AIError(ErrorCode.TRANSFER_NOT_ALLOWED, '허용되지 않은 전송 정책 버전입니다.')

    profile = registry.PROFILES.get(consent.profile_id)
    if profile is None or profile.provider != consent.provider or consent.provider not in registry.PROVIDERS:
        raise AIError(ErrorCode.INVALID_SELECTION, '지원하지 않는 제공자 또는 모델입니다.')
    profile = registry.get_profile(consent.profile_id)
    if profile.execution_location == 'external' and not settings.allow_external_transfer:
        raise AIError(ErrorCode.TRANSFER_NOT_ALLOWED, '코드의 외부 전송이 허용되지 않았습니다.')
    if (consent.profile_fingerprint is not None and consent.profile_fingerprint != profile.fingerprint()
            or not profile.requires_api_key and consent.profile_fingerprint != profile.fingerprint()):
        raise AIError(ErrorCode.PREVIEW_CHANGED, '모델 설정이 확인한 뒤 바뀌었습니다.')
    if not registry.is_allowed(profile.id):
        raise AIError(ErrorCode.PREVIEW_CHANGED, '이 모델은 더 이상 허용되지 않습니다. 모델을 다시 선택해 주세요.')

    row = db.execute(select(AIInput).where(AIInput.id == consent.input_id)
                     .execution_options(populate_existing=True)).scalar_one_or_none()
    if row is None:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '입력을 찾을 수 없습니다.')
    ensure_owner(row.owner_user_id, owner_id)
    if row.status == 'expired' or now >= row.expires_at or (row.valid_until is not None and now >= row.valid_until):
        raise AIError(ErrorCode.INPUT_EXPIRED, '입력의 실행 유효기간이 지났습니다.')
    if row.status != 'ready':
        raise AIError(ErrorCode.INVALID_STATE, '준비가 끝난 입력이 아닙니다.')
    if consent.manifest_hash != (row.manifest_hash or '').strip() or consent.policy_version != row.policy_version:
        raise AIError(ErrorCode.PREVIEW_CHANGED, '전송할 자료가 확인한 내용과 다릅니다. 미리보기를 다시 확인해 주세요.')

    manifest = InputManifest(**row.manifest)
    read = [f for f in manifest.files if f.content_state.value == 'read']
    if not read:
        raise AIError(ErrorCode.INVALID_STATE, 'AI에 보낼 수 있는 파일이 없습니다(NO_REVIEWABLE_CONTENT).',
                      details={'reason': 'NO_REVIEWABLE_CONTENT'})
    payload_bytes = db.execute(text("SELECT coalesce(sum(byte_count), 0) FROM ai_input_files WHERE input_id = :i "
                                    "AND content_state = 'read'"), {'i': row.id}).scalar()
    estimate = input_builder.estimate_tokens(int(payload_bytes), manifest.pr_metadata or PRTransferMetadata(),
                                             len(read), profile=profile)
    if not estimate.within_limit:
        raise AIError(ErrorCode.INPUT_TOO_LARGE, '선택한 모델의 입력 토큰 한도를 넘습니다.',
                      details={'estimated_tokens': estimate.input_tokens, 'limit': estimate.limit})

    if not profile.requires_api_key:
        if consent.credential_version is not None:
            raise AIError(ErrorCode.PREVIEW_CHANGED, '로컬 실행은 API key를 사용하지 않습니다.')
        return PinnedConfig(provider=profile.provider, profile=profile, profile_fingerprint=profile.fingerprint(),
                            credential_version_id=None, credential_version=None, key_hint=None,
                            input_id=row.id, repository_id=row.repository_id,
                            manifest_hash=consent.manifest_hash, policy_version=row.policy_version)
    found = credentials.get_active(db, owner_id, profile.provider)
    if found is None:
        raise AIError(ErrorCode.CREDENTIAL_REQUIRED, f'{profile.provider} API key를 먼저 등록해 주세요.')
    version = found[1]
    if version.version != consent.credential_version:
        raise AIError(ErrorCode.PREVIEW_CHANGED, 'API key가 확인한 뒤 바뀌었습니다. 다시 확인해 주세요.')
    return PinnedConfig(provider=profile.provider, profile=profile, profile_fingerprint=profile.fingerprint(),
                        credential_version_id=version.id, credential_version=version.version,
                        key_hint=version.key_hint, input_id=row.id, repository_id=row.repository_id,
                        manifest_hash=consent.manifest_hash, policy_version=row.policy_version)


def generation_fingerprint(owner_id: UUID, kind: str, input_hash: str, pinned: PinnedConfig) -> str:
    """생성 작업 fingerprint: owner·kind·입력·제공자/모델/프롬프트/schema/정책/profile 설정.

    key 버전은 넣지 않는다. 같은 입력·같은 설정이면 key가 바뀌어도 이미 성공한 결과를 재사용한다."""
    profile = pinned.profile
    return canonical_hash({'owner': str(owner_id), 'kind': kind, 'input': input_hash, 'provider': pinned.provider,
                           'model': profile.model, 'profile': pinned.profile_fingerprint,
                           'prompt': profile.prompt_version(kind), 'schema': profile.schema_version(kind),
                           'policy': pinned.policy_version})
