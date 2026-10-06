"""AI 작업·시도·멱등성·outbox. 기존 analysis_jobs와 섞지 않는다."""
from sqlalchemy import (Boolean, CheckConstraint, Column, DateTime, ForeignKey, ForeignKeyConstraint, Index,
                        Integer, Numeric, Text, UniqueConstraint, CHAR, text)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base
from app.models.ai_base import HEX64, created_at, updated_at, uuid_pk


class AIJob(Base):
    __tablename__ = "ai_jobs"
    id = uuid_pk()
    owner_user_id = Column(UUID(as_uuid=True), nullable=False)
    repository_id = Column(Integer, nullable=False)
    input_id = Column(UUID(as_uuid=True), nullable=False)
    kind = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="queued", server_default="queued")
    fingerprint = Column(CHAR(64), nullable=False)
    generation = Column(Integer, nullable=False, default=0, server_default="0")
    request_options = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    consent_manifest_hash = Column(CHAR(64))
    provider_profile = Column(Text)
    prompt_version = Column(Text)
    schema_version = Column(Text)
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    cancel_requested_at = Column(DateTime(timezone=True))
    lease_token = Column(UUID(as_uuid=True))
    lease_until = Column(DateTime(timezone=True))
    heartbeat_at = Column(DateTime(timezone=True))
    next_attempt_at = Column(DateTime(timezone=True))
    error_code = Column(Text)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    deleted_at = Column(DateTime(timezone=True))
    created_at = created_at()
    updated_at = updated_at()
    __table_args__ = (
        ForeignKeyConstraint(["input_id", "owner_user_id", "repository_id"],
                             ["ai_inputs.id", "ai_inputs.owner_user_id", "ai_inputs.repository_id"],
                             name="fk_ai_jobs_input", ondelete="CASCADE"),
        UniqueConstraint("owner_user_id", "kind", "fingerprint", "generation",
                         name="uq_ai_jobs_owner_kind_fingerprint_generation"),
        UniqueConstraint("id", "owner_user_id", "repository_id", name="uq_ai_jobs_id_owner_repo"),
        CheckConstraint("kind IN ('prepare','summary','review')", name="ck_ai_jobs_kind"),
        CheckConstraint("status IN ('queued','running','retry_wait','cancel_requested','succeeded','failed',"
                        "'cancelled')", name="ck_ai_jobs_status"),
        CheckConstraint(f"fingerprint ~ '{HEX64}' AND (consent_manifest_hash IS NULL OR "
                        f"consent_manifest_hash ~ '{HEX64}')", name="ck_ai_jobs_fingerprint"),
        CheckConstraint("generation >= 0 AND attempt_count >= 0", name="ck_ai_jobs_counters"),
        CheckConstraint("jsonb_typeof(request_options) = 'object'", name="ck_ai_jobs_options"),
        CheckConstraint("kind = 'prepare' OR (provider_profile IS NOT NULL AND prompt_version IS NOT NULL "
                        "AND schema_version IS NOT NULL)", name="ck_ai_jobs_generation_profile"),
        CheckConstraint("status <> 'running' OR (lease_token IS NOT NULL AND lease_until IS NOT NULL)",
                        name="ck_ai_jobs_running_lease"),
        CheckConstraint("status NOT IN ('succeeded','failed','cancelled') OR finished_at IS NOT NULL",
                        name="ck_ai_jobs_terminal"),
        CheckConstraint("status <> 'failed' OR error_code IS NOT NULL", name="ck_ai_jobs_failed_code"),
        Index("ix_ai_jobs_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_ai_jobs_owner_created", "owner_user_id", "created_at", "id"),
        Index("ix_ai_jobs_input", "input_id"),
    )


class AIAttempt(Base):
    __tablename__ = "ai_attempts"
    id = uuid_pk()
    job_id = Column(UUID(as_uuid=True), ForeignKey("ai_jobs.id", ondelete="CASCADE"), nullable=False)
    attempt_no = Column(Integer, nullable=False)
    lease_token = Column(UUID(as_uuid=True), nullable=False)
    status = Column(Text, nullable=False, default="started", server_default="started")
    provider_request_id = Column(Text)
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    provider_reported_cost = Column(Numeric(18, 6))
    estimated_cost = Column(Numeric(18, 6))
    outcome_uncertain = Column(Boolean, nullable=False, default=False, server_default="false")
    error_code = Column(Text)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    finished_at = Column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("job_id", "attempt_no", name="uq_ai_attempts_job_attempt"),
        CheckConstraint("attempt_no >= 1", name="ck_ai_attempts_no"),
        CheckConstraint("status IN ('started','succeeded','failed','cancelled','uncertain')",
                        name="ck_ai_attempts_status"),
        CheckConstraint("(status = 'uncertain') = outcome_uncertain", name="ck_ai_attempts_uncertain"),
        CheckConstraint("COALESCE(input_tokens,0) >= 0 AND COALESCE(output_tokens,0) >= 0 "
                        "AND COALESCE(provider_reported_cost,0) >= 0 AND COALESCE(estimated_cost,0) >= 0",
                        name="ck_ai_attempts_usage"),
    )


class RequestIdempotency(Base):
    __tablename__ = "request_idempotency"
    id = uuid_pk()
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    route = Column(Text, nullable=False)
    key = Column(Text, nullable=False)
    request_hash = Column(CHAR(64), nullable=False)
    resource_id = Column(UUID(as_uuid=True))
    status = Column(Text, nullable=False, default="in_progress", server_default="in_progress")
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = created_at()
    updated_at = updated_at()
    __table_args__ = (
        UniqueConstraint("owner_user_id", "route", "key", name="uq_request_idempotency_owner_route_key"),
        CheckConstraint("length(key) BETWEEN 1 AND 255 AND length(route) > 0", name="ck_request_idempotency_key"),
        CheckConstraint(f"request_hash ~ '{HEX64}'", name="ck_request_idempotency_hash"),
        CheckConstraint("status IN ('in_progress','completed')", name="ck_request_idempotency_status"),
        Index("ix_request_idempotency_expires", "expires_at"),
    )


class TaskOutbox(Base):
    __tablename__ = "task_outbox"
    id = uuid_pk()
    job_id = Column(UUID(as_uuid=True), ForeignKey("ai_jobs.id", ondelete="CASCADE"), nullable=False)
    dispatch_generation = Column(Integer, nullable=False, default=0, server_default="0")
    event_type = Column(Text, nullable=False)
    available_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    published_at = Column(DateTime(timezone=True))
    retry_count = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = created_at()
    __table_args__ = (
        UniqueConstraint("job_id", "dispatch_generation", "event_type", name="uq_task_outbox_job_generation_event"),
        CheckConstraint("dispatch_generation >= 0 AND retry_count >= 0", name="ck_task_outbox_counters"),
        CheckConstraint("length(event_type) > 0", name="ck_task_outbox_event"),
        Index("ix_task_outbox_unpublished", "available_at", postgresql_where=text("published_at IS NULL")),
    )
