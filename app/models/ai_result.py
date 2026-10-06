"""AI 결과·지적·피드백. 결과 원본과 지적은 DB trigger로 변경이 막힌다(migration a7c1e5b2d903)."""
from sqlalchemy import (CheckConstraint, Column, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Text,
                        UniqueConstraint, CHAR)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base
from app.models.ai_base import HEX64, created_at, updated_at, uuid_pk


class AIResult(Base):
    __tablename__ = "ai_results"
    id = uuid_pk()
    job_id = Column(UUID(as_uuid=True), nullable=False)
    owner_user_id = Column(UUID(as_uuid=True), nullable=False)
    repository_id = Column(Integer, nullable=False)
    payload = Column(JSONB, nullable=False)
    payload_hash = Column(CHAR(64), nullable=False)
    coverage = Column(JSONB, nullable=False)
    structure_validation = Column(Text, nullable=False)
    human_validation = Column(Text, nullable=False, default="unverified", server_default="unverified")
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = created_at()
    __table_args__ = (
        ForeignKeyConstraint(["job_id", "owner_user_id", "repository_id"],
                             ["ai_jobs.id", "ai_jobs.owner_user_id", "ai_jobs.repository_id"],
                             name="fk_ai_results_job", ondelete="CASCADE"),
        UniqueConstraint("job_id", name="uq_ai_results_job"),
        UniqueConstraint("id", "owner_user_id", "repository_id", name="uq_ai_results_id_owner_repo"),
        CheckConstraint(f"payload_hash ~ '{HEX64}'", name="ck_ai_results_hash"),
        CheckConstraint("jsonb_typeof(payload) = 'object' AND jsonb_typeof(coverage) = 'object'",
                        name="ck_ai_results_json"),
        CheckConstraint("structure_validation IN ('passed','failed')", name="ck_ai_results_structure"),
        CheckConstraint("human_validation IN ('unverified','valid','false_positive','uncertain')",
                        name="ck_ai_results_human"),
    )


class AIFinding(Base):
    __tablename__ = "ai_findings"
    id = uuid_pk()
    result_id = Column(UUID(as_uuid=True), ForeignKey("ai_results.id", ondelete="CASCADE"), nullable=False)
    ordinal = Column(Integer, nullable=False)
    fingerprint = Column(CHAR(64), nullable=False)
    category = Column(Text, nullable=False)
    priority = Column(Integer, nullable=False)
    title = Column(Text, nullable=False)
    detail = Column(JSONB, nullable=False)
    source_file_id = Column(UUID(as_uuid=True), ForeignKey("ai_input_files.id", ondelete="CASCADE"), nullable=False)
    side = Column(Text, nullable=False)
    start_line = Column(Integer, nullable=False)
    end_line = Column(Integer, nullable=False)
    created_at = created_at()
    __table_args__ = (
        UniqueConstraint("result_id", "ordinal", name="uq_ai_findings_result_ordinal"),
        CheckConstraint("ordinal >= 1 AND priority >= 1", name="ck_ai_findings_ordinal"),
        CheckConstraint(f"fingerprint ~ '{HEX64}'", name="ck_ai_findings_fingerprint"),
        CheckConstraint("side IN ('LEFT','RIGHT')", name="ck_ai_findings_side"),
        CheckConstraint("start_line > 0 AND start_line <= end_line", name="ck_ai_findings_lines"),
        CheckConstraint("jsonb_typeof(detail) = 'object'", name="ck_ai_findings_detail"),
        Index("ix_ai_findings_source_file", "source_file_id"),
    )


class FindingFeedback(Base):
    __tablename__ = "finding_feedback"
    id = uuid_pk()
    finding_id = Column(UUID(as_uuid=True), ForeignKey("ai_findings.id", ondelete="CASCADE"), nullable=False)
    author_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    verdict = Column(Text, nullable=False)
    note = Column(Text)
    version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = created_at()
    updated_at = updated_at()
    __table_args__ = (
        UniqueConstraint("finding_id", "author_user_id", name="uq_finding_feedback_finding_author"),
        CheckConstraint("verdict IN ('valid','false_positive','uncertain')", name="ck_finding_feedback_verdict"),
        CheckConstraint("version > 0", name="ck_finding_feedback_version"),
        CheckConstraint("note IS NULL OR length(note) <= 2000", name="ck_finding_feedback_note"),
    )
