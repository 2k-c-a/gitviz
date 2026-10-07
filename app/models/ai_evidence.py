"""AI 이력 근거. 결과의 코드 지적과 분리된 불변 기록이며, 근거 숫자는 분석 snapshot의 저장된 payload에서만 가져온다."""
from sqlalchemy import (CHAR, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Index, Integer, Text, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base
from app.models.ai_base import HEX64, created_at, uuid_pk


class AIEvidence(Base):
    __tablename__ = "ai_evidence"
    id = uuid_pk()
    result_id = Column(UUID(as_uuid=True), nullable=False)
    owner_user_id = Column(UUID(as_uuid=True), nullable=False)
    repository_id = Column(Integer, nullable=False)
    kind = Column(Text, nullable=False, default="history", server_default="history")
    snapshot_id = Column(Integer, nullable=False)
    snapshot_sha256 = Column(CHAR(64), nullable=False)
    status = Column(Text, nullable=False)
    exclusion_reasons = Column(JSONB, nullable=False, server_default="[]")
    as_of = Column(DateTime(timezone=True), nullable=False)
    payload = Column(JSONB, nullable=False)
    payload_hash = Column(CHAR(64), nullable=False)
    created_at = created_at()
    __table_args__ = (
        ForeignKeyConstraint(["result_id", "owner_user_id", "repository_id"],
                             ["ai_results.id", "ai_results.owner_user_id", "ai_results.repository_id"],
                             name="fk_ai_evidence_result", ondelete="CASCADE"),
        ForeignKeyConstraint(["snapshot_id", "repository_id"], ["analysis_snapshots.id", "analysis_snapshots.repository_id"],
                             name="fk_ai_evidence_snapshot"),
        UniqueConstraint("result_id", "snapshot_id", "kind", name="uq_ai_evidence_result_snapshot_kind"),
        CheckConstraint("kind IN ('history')", name="ck_ai_evidence_kind"),
        CheckConstraint("status IN ('ok', 'excluded')", name="ck_ai_evidence_status"),
        CheckConstraint(f"snapshot_sha256 ~ '{HEX64}' AND payload_hash ~ '{HEX64}'", name="ck_ai_evidence_hashes"),
        CheckConstraint("jsonb_typeof(payload) = 'object' AND jsonb_typeof(exclusion_reasons) = 'array'", name="ck_ai_evidence_json"),
        CheckConstraint("(status = 'ok' AND jsonb_array_length(exclusion_reasons) = 0) OR "
                        "(status = 'excluded' AND jsonb_array_length(exclusion_reasons) > 0)", name="ck_ai_evidence_reasons"),
        Index("ix_ai_evidence_result", "result_id"),
        Index("ix_ai_evidence_snapshot", "snapshot_id"),
    )
