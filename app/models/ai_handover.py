"""AI 인수인계(S12). 준비 정보 버전과 생성 원본은 추가만 하고, 편집본 문서만 expected_version으로 갱신한다."""
from sqlalchemy import (CHAR, Boolean, CheckConstraint, Column, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Text, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base
from app.models.ai_base import HEX64, created_at, updated_at, uuid_pk


def _owner():
    return ForeignKey("users.id", ondelete="CASCADE")


class HandoverTarget(Base):
    __tablename__ = "handover_targets"
    id = uuid_pk()
    owner_user_id = Column(UUID(as_uuid=True), _owner(), nullable=False)
    repository_id = Column(Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    purpose = Column(Text)
    giver_name = Column(Text)
    receiver_name = Column(Text)
    selected_paths = Column(JSONB, nullable=False)
    snapshot_id = Column(Integer)
    version = Column(Integer, nullable=False, default=1, server_default="1")
    scope_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = created_at()
    updated_at = updated_at()
    __table_args__ = (
        ForeignKeyConstraint(["snapshot_id", "repository_id"], ["analysis_snapshots.id", "analysis_snapshots.repository_id"],
                             name="fk_handover_targets_snapshot"),
        UniqueConstraint("id", "owner_user_id", "repository_id", name="uq_handover_targets_id_owner_repo"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="ck_handover_targets_name"),
        CheckConstraint("(purpose IS NULL OR char_length(purpose) <= 500) AND (giver_name IS NULL OR char_length(giver_name) <= 100) "
                        "AND (receiver_name IS NULL OR char_length(receiver_name) <= 100)", name="ck_handover_targets_memo"),
        CheckConstraint("jsonb_typeof(selected_paths) = 'array' AND jsonb_array_length(selected_paths) BETWEEN 1 AND 50", name="ck_handover_targets_paths"),
        CheckConstraint("version >= 1 AND scope_version >= 1 AND scope_version <= version", name="ck_handover_targets_version"),
        Index("ix_handover_targets_owner_repo", "owner_user_id", "repository_id", updated_at.desc()),
    )


class HandoverPreparationVersion(Base):
    __tablename__ = "handover_preparation_versions"
    id = uuid_pk()
    target_id = Column(UUID(as_uuid=True), nullable=False)
    owner_user_id = Column(UUID(as_uuid=True), nullable=False)
    repository_id = Column(Integer, nullable=False)
    version = Column(Integer, nullable=False)
    target_version = Column(Integer, nullable=False)
    items = Column(JSONB, nullable=False)
    author_user_id = Column(UUID(as_uuid=True), _owner(), nullable=False)
    created_at = created_at()
    __table_args__ = (
        ForeignKeyConstraint(["target_id", "owner_user_id", "repository_id"],
                             ["handover_targets.id", "handover_targets.owner_user_id", "handover_targets.repository_id"],
                             name="fk_handover_prep_target", ondelete="CASCADE"),
        UniqueConstraint("target_id", "version", name="uq_handover_prep_target_version"),
        CheckConstraint("version >= 1 AND target_version >= 1", name="ck_handover_prep_version"),
        CheckConstraint("jsonb_typeof(items) = 'object'", name="ck_handover_prep_items"),
    )


class HandoverGeneration(Base):
    __tablename__ = "handover_generations"
    id = uuid_pk()
    target_id = Column(UUID(as_uuid=True), nullable=False)
    owner_user_id = Column(UUID(as_uuid=True), nullable=False)
    repository_id = Column(Integer, nullable=False)
    number = Column(Integer, nullable=False)
    fingerprint = Column(CHAR(64), nullable=False)
    target_version = Column(Integer, nullable=False)
    preparation_version = Column(Integer, nullable=False, default=0, server_default="0")
    sources = Column(JSONB, nullable=False)
    body = Column(JSONB, nullable=False)
    markdown = Column(Text, nullable=False)
    body_hash = Column(CHAR(64), nullable=False)
    template_version = Column(Text, nullable=False)
    created_at = created_at()
    __table_args__ = (
        ForeignKeyConstraint(["target_id", "owner_user_id", "repository_id"],
                             ["handover_targets.id", "handover_targets.owner_user_id", "handover_targets.repository_id"],
                             name="fk_handover_generations_target", ondelete="CASCADE"),
        UniqueConstraint("target_id", "number", name="uq_handover_generations_number"),
        UniqueConstraint("id", "target_id", name="uq_handover_generations_id_target"),
        CheckConstraint(f"fingerprint ~ '{HEX64}' AND body_hash ~ '{HEX64}'", name="ck_handover_generations_hashes"),
        CheckConstraint("jsonb_typeof(sources) = 'object' AND jsonb_typeof(body) = 'object'", name="ck_handover_generations_json"),
        CheckConstraint("char_length(markdown) <= 100000", name="ck_handover_generations_size"),
        CheckConstraint("number >= 1 AND target_version >= 1 AND preparation_version >= 0", name="ck_handover_generations_numbers"),
    )


class HandoverDocument(Base):
    __tablename__ = "handover_documents"
    id = uuid_pk()
    target_id = Column(UUID(as_uuid=True), nullable=False)
    owner_user_id = Column(UUID(as_uuid=True), nullable=False)
    repository_id = Column(Integer, nullable=False)
    base_generation_id = Column(UUID(as_uuid=True), nullable=False)
    markdown = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1, server_default="1")
    edited = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = created_at()
    updated_at = updated_at()
    __table_args__ = (
        ForeignKeyConstraint(["target_id", "owner_user_id", "repository_id"],
                             ["handover_targets.id", "handover_targets.owner_user_id", "handover_targets.repository_id"],
                             name="fk_handover_documents_target", ondelete="CASCADE"),
        ForeignKeyConstraint(["base_generation_id", "target_id"], ["handover_generations.id", "handover_generations.target_id"],
                             name="fk_handover_documents_generation", ondelete="CASCADE"),
        UniqueConstraint("target_id", name="uq_handover_documents_target"),
        UniqueConstraint("id", "target_id", name="uq_handover_documents_id_target"),
        CheckConstraint("char_length(markdown) <= 100000", name="ck_handover_documents_size"),
        CheckConstraint("version >= 1", name="ck_handover_documents_version"),
    )


class HandoverDocumentVersion(Base):
    __tablename__ = "handover_document_versions"
    id = uuid_pk()
    document_id = Column(UUID(as_uuid=True), ForeignKey("handover_documents.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    kind = Column(Text, nullable=False)
    markdown = Column(Text, nullable=False)
    base_generation_id = Column(UUID(as_uuid=True), nullable=False)
    author_user_id = Column(UUID(as_uuid=True), _owner(), nullable=False)
    created_at = created_at()
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_handover_document_versions"),
        CheckConstraint("kind IN ('generated', 'edited', 'adopted')", name="ck_handover_document_versions_kind"),
        CheckConstraint("char_length(markdown) <= 100000 AND version >= 1", name="ck_handover_document_versions_size"),
    )
