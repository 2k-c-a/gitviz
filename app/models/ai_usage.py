"""AI 사용량 예산·예약과 감사 기록. 감사 기록에는 코드·토큰·메모 본문을 넣지 않는다."""
from sqlalchemy import (CheckConstraint, Column, Date, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint,
                        CHAR, text)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base
from app.models.ai_base import created_at, updated_at, uuid_pk


class UsageBudget(Base):
    """owner_user_id가 NULL이면 서버 전체 예산."""
    __tablename__ = "usage_budgets"
    id = uuid_pk()
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    period_start = Column(Date, nullable=False)
    currency = Column(CHAR(3), nullable=False)
    amount_limit = Column(Numeric(18, 6), nullable=False)
    reserved_amount = Column(Numeric(18, 6), nullable=False, default=0, server_default="0")
    spent_amount = Column(Numeric(18, 6), nullable=False, default=0, server_default="0")
    version = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = created_at()
    updated_at = updated_at()
    __table_args__ = (
        UniqueConstraint("owner_user_id", "period_start", "currency", postgresql_nulls_not_distinct=True,
                         name="uq_usage_budgets_owner_period_currency"),
        CheckConstraint("amount_limit >= 0 AND reserved_amount >= 0 AND spent_amount >= 0 AND version >= 0",
                        name="ck_usage_budgets_amounts"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_usage_budgets_currency"),
    )


class UsageReservation(Base):
    __tablename__ = "usage_reservations"
    id = uuid_pk()
    # 작업을 삭제해도 비용 기록은 남긴다.
    job_id = Column(UUID(as_uuid=True), ForeignKey("ai_jobs.id", ondelete="SET NULL"))
    attempt_no = Column(Integer, nullable=False)
    budget_id = Column(UUID(as_uuid=True), ForeignKey("usage_budgets.id"), nullable=False)
    reserved_amount = Column(Numeric(18, 6), nullable=False)
    settled_amount = Column(Numeric(18, 6))
    status = Column(Text, nullable=False, default="reserved", server_default="reserved")
    created_at = created_at()
    updated_at = updated_at()
    __table_args__ = (
        UniqueConstraint("job_id", "attempt_no", "budget_id", name="uq_usage_reservations_job_attempt_budget"),
        CheckConstraint("attempt_no >= 1", name="ck_usage_reservations_attempt"),
        CheckConstraint("reserved_amount >= 0 AND COALESCE(settled_amount,0) >= 0",
                        name="ck_usage_reservations_amounts"),
        CheckConstraint("status IN ('reserved','settled','uncertain','released')",
                        name="ck_usage_reservations_status"),
        CheckConstraint("status <> 'settled' OR settled_amount IS NOT NULL", name="ck_usage_reservations_settled"),
        Index("ix_usage_reservations_budget", "budget_id"),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id = uuid_pk()
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    repository_id = Column(Integer, ForeignKey("repositories.id", ondelete="SET NULL"))
    action = Column(Text, nullable=False)
    object_type = Column(Text, nullable=False)
    object_id = Column(Text)
    request_id = Column(Text, nullable=False)
    details = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = created_at()
    __table_args__ = (
        CheckConstraint("jsonb_typeof(details) = 'object' AND NOT (details ?| ARRAY['code','source','token',"
                        "'access_token','note','content','payload','diff'])", name="ck_audit_events_details"),
        Index("ix_audit_events_actor_created", "actor_user_id", "created_at"),
    )
