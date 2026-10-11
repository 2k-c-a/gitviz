"""PR 리비전과 고정 입력. 확정된 입력은 DB trigger로 변경이 제한된다(migration a7c1e5b2d903)."""
from sqlalchemy import (BigInteger, CheckConstraint, Column, DateTime, ForeignKey, ForeignKeyConstraint, Index,
                        Integer, LargeBinary, Text, UniqueConstraint, CHAR, text)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base
from app.models.ai_base import HEX64, SHA, created_at, updated_at, uuid_pk

_OWNER = ForeignKey("users.id", ondelete="CASCADE")
_REPO = ForeignKey("repositories.id", ondelete="CASCADE")


class PRRevision(Base):
    __tablename__ = "pr_revisions"
    id = uuid_pk()
    owner_user_id = Column(UUID(as_uuid=True), _OWNER, nullable=False)
    repository_id = Column(Integer, _REPO, nullable=False)
    source_type = Column(Text, nullable=False, server_default='pr')
    pr_number = Column(Integer)
    github_pr_id = Column(BigInteger)
    base_sha = Column(Text, nullable=False)
    head_sha = Column(Text, nullable=False)
    merge_base_sha = Column(Text)
    revision_fingerprint = Column(CHAR(64), nullable=False)
    state_at_capture = Column(Text, nullable=False)
    # 'metadata'는 declarative 예약 속성이라 속성 이름만 바꾼다.
    pr_metadata = Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = created_at()
    __table_args__ = (
        UniqueConstraint("owner_user_id", "repository_id", "revision_fingerprint",
                         name="uq_pr_revisions_owner_repo_fingerprint"),
        UniqueConstraint("id", "owner_user_id", "repository_id", name="uq_pr_revisions_id_owner_repo"),
        CheckConstraint("(source_type = 'pr' AND pr_number IS NOT NULL AND pr_number > 0 AND github_pr_id IS NOT NULL AND github_pr_id > 0) OR (source_type = 'commit' AND pr_number IS NULL AND github_pr_id IS NULL)", name="ck_pr_revisions_pr_number"),
        CheckConstraint(f"base_sha ~ '{SHA}' AND head_sha ~ '{SHA}' "
                        f"AND (merge_base_sha IS NULL OR merge_base_sha ~ '{SHA}')", name="ck_pr_revisions_shas"),
        CheckConstraint(f"revision_fingerprint ~ '{HEX64}'", name="ck_pr_revisions_fingerprint"),
        CheckConstraint("(source_type = 'pr' AND state_at_capture IN ('open','closed','merged')) OR (source_type = 'commit' AND state_at_capture = 'commit')", name="ck_pr_revisions_state"),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="ck_pr_revisions_metadata"),
        Index("ix_pr_revisions_owner_repo_created", "owner_user_id", "repository_id", "created_at", "id"),
    )


class AIInput(Base):
    __tablename__ = "ai_inputs"
    id = uuid_pk()
    owner_user_id = Column(UUID(as_uuid=True), nullable=False)
    repository_id = Column(Integer, nullable=False)
    revision_id = Column(UUID(as_uuid=True), nullable=False)
    status = Column(Text, nullable=False, default="building", server_default="building")
    manifest = Column(JSONB)
    manifest_hash = Column(CHAR(64))
    input_hash = Column(CHAR(64))
    coverage = Column(JSONB, nullable=False)
    valid_until = Column(DateTime(timezone=True))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    policy_version = Column(Text, nullable=False)
    finalized_at = Column(DateTime(timezone=True))
    created_at = created_at()
    updated_at = updated_at()
    __table_args__ = (
        ForeignKeyConstraint(["revision_id", "owner_user_id", "repository_id"],
                             ["pr_revisions.id", "pr_revisions.owner_user_id", "pr_revisions.repository_id"],
                             name="fk_ai_inputs_revision", ondelete="CASCADE"),
        UniqueConstraint("id", "owner_user_id", "repository_id", name="uq_ai_inputs_id_owner_repo"),
        CheckConstraint("status IN ('building','ready','failed','expired')", name="ck_ai_inputs_status"),
        CheckConstraint("status <> 'ready' OR (manifest IS NOT NULL AND manifest_hash IS NOT NULL "
                        "AND input_hash IS NOT NULL AND finalized_at IS NOT NULL AND valid_until IS NOT NULL)",
                        name="ck_ai_inputs_ready"),
        CheckConstraint(f"(manifest_hash IS NULL OR manifest_hash ~ '{HEX64}') "
                        f"AND (input_hash IS NULL OR input_hash ~ '{HEX64}')", name="ck_ai_inputs_hashes"),
        CheckConstraint("jsonb_typeof(coverage) = 'object' "
                        "AND (manifest IS NULL OR jsonb_typeof(manifest) = 'object')", name="ck_ai_inputs_json"),
        CheckConstraint("length(policy_version) > 0", name="ck_ai_inputs_policy"),
        Index("ix_ai_inputs_owner_repo_created", "owner_user_id", "repository_id", "created_at", "id"),
        Index("ix_ai_inputs_status_expires", "status", "expires_at"),
    )


class AIInputFile(Base):
    __tablename__ = "ai_input_files"
    id = uuid_pk()
    input_id = Column(UUID(as_uuid=True), ForeignKey("ai_inputs.id", ondelete="CASCADE"), nullable=False)
    path = Column(Text, nullable=False)
    old_path = Column(Text)
    side_availability = Column(Text, nullable=False)
    language = Column(Text)
    content_state = Column(Text, nullable=False)
    reason = Column(Text)
    blob_oids = Column(JSONB)
    content_hash = Column(CHAR(64))
    encrypted_payload = Column(LargeBinary)
    key_version = Column(Text)
    line_map = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    byte_count = Column(BigInteger, nullable=False, server_default="0")
    created_at = created_at()
    __table_args__ = (
        UniqueConstraint("input_id", "path", name="uq_ai_input_files_input_path"),
        CheckConstraint("side_availability IN ('LEFT','RIGHT','BOTH')", name="ck_ai_input_files_side"),
        CheckConstraint("content_state IN ('read','excluded','failed')", name="ck_ai_input_files_state"),
        CheckConstraint("(content_state = 'read' AND reason IS NULL AND content_hash IS NOT NULL) OR "
                        "(content_state <> 'read' AND reason IS NOT NULL)", name="ck_ai_input_files_reason"),
        CheckConstraint(f"content_hash IS NULL OR content_hash ~ '{HEX64}'", name="ck_ai_input_files_hash"),
        CheckConstraint("encrypted_payload IS NULL OR key_version IS NOT NULL", name="ck_ai_input_files_payload_key"),
        CheckConstraint("jsonb_typeof(line_map) = 'object' "
                        "AND (blob_oids IS NULL OR jsonb_typeof(blob_oids) = 'object')", name="ck_ai_input_files_json"),
        CheckConstraint("byte_count >= 0", name="ck_ai_input_files_bytes"),
    )
