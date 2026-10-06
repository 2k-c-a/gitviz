"""사용자 API key(BYOK). 키는 사용자별로 AES-GCM 암호화해 저장하고 버전을 둔다.

- 교체하면 새 버전이 active가 되고 옛 버전은 retired다. 옛 버전의 암호문은 그 버전에 고정된 진행 중 작업이 모두 끝나면 삭제한다.
- 삭제하면 모든 버전의 암호문을 즉시 지우고(행은 감사·비용 기록용으로 남는다) 고정된 진행 중 작업을 취소한다.
- 암호문은 한 번 지워지면 되살릴 수 없고, 저장된 암호문은 바뀌지 않는다(DB trigger).
"""
from sqlalchemy import (CHAR, CheckConstraint, Column, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer,
                        LargeBinary, Text, UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base
from app.models.ai_base import HEX64, created_at, updated_at, uuid_pk


class AICredential(Base):
    __tablename__ = "ai_credentials"
    id = uuid_pk()
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="active", server_default="active")
    preferred_profile_id = Column(Text)
    created_at = created_at()
    updated_at = updated_at()
    __table_args__ = (
        UniqueConstraint("owner_user_id", "provider", name="uq_ai_credentials_owner_provider"),
        UniqueConstraint("id", "owner_user_id", name="uq_ai_credentials_id_owner"),
        CheckConstraint("status IN ('active', 'deleted')", name="ck_ai_credentials_status"),
        CheckConstraint("provider ~ '^[a-z][a-z0-9_-]{1,31}$'", name="ck_ai_credentials_provider"),
    )


class AICredentialVersion(Base):
    __tablename__ = "ai_credential_versions"
    id = uuid_pk()
    credential_id = Column(UUID(as_uuid=True), nullable=False)
    owner_user_id = Column(UUID(as_uuid=True), nullable=False)
    version = Column(Integer, nullable=False)
    status = Column(Text, nullable=False, default="active", server_default="active")
    encrypted_key = Column(LargeBinary)
    key_version = Column(Text)
    key_hint = Column(Text, nullable=False)
    key_fingerprint = Column(CHAR(64), nullable=False)
    validated_at = Column(DateTime(timezone=True))
    created_at = created_at()
    retired_at = Column(DateTime(timezone=True))
    secret_purged_at = Column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(["credential_id", "owner_user_id"],
                             ["ai_credentials.id", "ai_credentials.owner_user_id"],
                             name="fk_ai_credential_versions_credential", ondelete="CASCADE"),
        UniqueConstraint("credential_id", "version", name="uq_ai_credential_versions_credential_version"),
        UniqueConstraint("id", "owner_user_id", name="uq_ai_credential_versions_id_owner"),
        CheckConstraint("status IN ('active', 'retired', 'deleted')", name="ck_ai_credential_versions_status"),
        CheckConstraint("version >= 1", name="ck_ai_credential_versions_version"),
        CheckConstraint("(encrypted_key IS NULL) = (key_version IS NULL)", name="ck_ai_credential_versions_secret"),
        CheckConstraint("status <> 'active' OR encrypted_key IS NOT NULL",
                        name="ck_ai_credential_versions_active_has_secret"),
        CheckConstraint("status <> 'deleted' OR encrypted_key IS NULL",
                        name="ck_ai_credential_versions_deleted_has_no_secret"),
        CheckConstraint(f"key_fingerprint ~ '{HEX64}'", name="ck_ai_credential_versions_fingerprint"),
        CheckConstraint("length(key_hint) BETWEEN 1 AND 40", name="ck_ai_credential_versions_hint"),
        Index("uq_ai_credential_versions_one_active", "credential_id", unique=True,
              postgresql_where=text("status = 'active'")),
    )
