"""AI 인사이트(자동 분석 리포트·대화형 분석). 기존 ai_jobs(PR·커밋 코드 리뷰)와 섞지 않는다.

모든 행은 한 사용자·한 저장소·한 분석 스냅샷에 묶인다. 결과에는 생성 시각과 기준 데이터(스냅샷·계산 버전·도구 버전)를 함께 남긴다.
"""
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base
from app.models.ai_base import created_at, updated_at, uuid_pk

RUN_STATUSES = "('queued','running','succeeded','failed')"


class AIInsightReport(Base):
    __tablename__ = "ai_insight_reports"
    id = uuid_pk()
    owner_user_id = Column(UUID(as_uuid=True), nullable=False)
    repository_id = Column(Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    snapshot_id = Column(Integer, ForeignKey("analysis_snapshots.id", ondelete="CASCADE"), nullable=False)
    status = Column(Text, nullable=False, server_default="queued")
    provider_profile = Column(Text, nullable=False)
    include_code = Column(Boolean, nullable=False, server_default=text('false'))   # 기준 커밋 코드 열람 허용
    provider = Column(Text, nullable=False)
    model = Column(Text, nullable=False)
    prompt_version = Column(Text, nullable=False)
    tool_version = Column(Text, nullable=False)
    policy_version = Column(Text, nullable=False)
    progress = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    result = Column(JSONB)
    error_code = Column(Text)
    call_count = Column(Integer, nullable=False, server_default="0")
    cost = Column(Numeric(18, 6))
    created_at = created_at()
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    deleted_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(f"status IN {RUN_STATUSES}", name="ck_ai_insight_reports_status"),
        CheckConstraint("jsonb_typeof(progress) = 'array'", name="ck_ai_insight_reports_progress"),
        CheckConstraint("result IS NULL OR jsonb_typeof(result) = 'object'", name="ck_ai_insight_reports_result"),
        CheckConstraint("status <> 'failed' OR error_code IS NOT NULL", name="ck_ai_insight_reports_failed_code"),
        Index("ix_ai_insight_reports_owner_repo", "owner_user_id", "repository_id", "created_at"),
    )


class AIInsightConversation(Base):
    __tablename__ = "ai_insight_conversations"
    id = uuid_pk()
    owner_user_id = Column(UUID(as_uuid=True), nullable=False)
    repository_id = Column(Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    snapshot_id = Column(Integer, ForeignKey("analysis_snapshots.id", ondelete="CASCADE"), nullable=False)
    provider_profile = Column(Text, nullable=False)
    include_code = Column(Boolean, nullable=False, server_default=text('false'))
    title = Column(Text, nullable=False, server_default="")
    created_at = created_at()
    updated_at = updated_at()
    deleted_at = Column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_ai_insight_conversations_owner_repo", "owner_user_id", "repository_id", "updated_at"),
    )


class AIInsightMessage(Base):
    __tablename__ = "ai_insight_messages"
    id = uuid_pk()
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("ai_insight_conversations.id", ondelete="CASCADE"),
                             nullable=False)
    seq = Column(Integer, nullable=False)
    role = Column(Text, nullable=False)
    content = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status = Column(Text, nullable=False, server_default="succeeded")
    progress = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    error_code = Column(Text)
    provider = Column(Text)
    model = Column(Text)
    prompt_version = Column(Text)
    tool_version = Column(Text)
    call_count = Column(Integer, nullable=False, server_default="0")
    cost = Column(Numeric(18, 6))
    created_at = created_at()
    finished_at = Column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_ai_insight_messages_seq"),
        CheckConstraint("role IN ('user','assistant')", name="ck_ai_insight_messages_role"),
        CheckConstraint(f"status IN {RUN_STATUSES}", name="ck_ai_insight_messages_status"),
        CheckConstraint("jsonb_typeof(content) = 'object' AND jsonb_typeof(progress) = 'array'",
                        name="ck_ai_insight_messages_json"),
        CheckConstraint("status <> 'failed' OR error_code IS NOT NULL", name="ck_ai_insight_messages_failed_code"),
    )
