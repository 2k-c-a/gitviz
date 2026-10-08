"""AI 팀(S13): 팀·구성원·연결 저장소·명시적 공유·보고서. 개인 결과·작업은 바꾸지 않고, 팀 자료는 이 테이블에만 있다."""
from sqlalchemy import (CHAR, CheckConstraint, Column, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, PrimaryKeyConstraint, Text,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base
from app.models.ai_base import HEX64, created_at, updated_at, uuid_pk


def _user(ondelete="CASCADE"):
    return ForeignKey("users.id", ondelete=ondelete)


class Team(Base):
    __tablename__ = "teams"
    id = uuid_pk()
    name = Column(Text, nullable=False)
    created_by = Column(UUID(as_uuid=True), _user("SET NULL"))
    visibility_epoch = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = created_at()
    updated_at = updated_at()
    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 80", name="ck_teams_name"),
        CheckConstraint("visibility_epoch >= 1", name="ck_teams_epoch"),
    )


class TeamMember(Base):
    __tablename__ = "team_members"
    id = uuid_pk()
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), _user(), nullable=False)
    role = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="invited", server_default="invited")
    invited_by = Column(UUID(as_uuid=True), _user("SET NULL"))
    created_at = created_at()
    updated_at = updated_at()
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),
        CheckConstraint("role IN ('admin', 'member', 'viewer')", name="ck_team_members_role"),
        CheckConstraint("status IN ('invited', 'active')", name="ck_team_members_status"),
        Index("ix_team_members_user", "user_id", "status"),
    )


class TeamRepository(Base):
    __tablename__ = "team_repositories"
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    repository_id = Column(Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    added_by = Column(UUID(as_uuid=True), _user("SET NULL"))
    created_at = created_at()
    __table_args__ = (PrimaryKeyConstraint("team_id", "repository_id", name="pk_team_repositories"),)


class TeamResourceShare(Base):
    __tablename__ = "team_resource_shares"
    id = uuid_pk()
    team_id = Column(UUID(as_uuid=True), nullable=False)
    repository_id = Column(Integer, nullable=False)
    resource_type = Column(Text, nullable=False)
    review_result_id = Column(UUID(as_uuid=True))
    handover_target_id = Column(UUID(as_uuid=True))
    shared_by = Column(UUID(as_uuid=True), _user(), nullable=False)
    pinned_target_version = Column(Integer)
    pinned_preparation_version = Column(Integer)
    created_at = created_at()
    __table_args__ = (
        ForeignKeyConstraint(["team_id", "repository_id"], ["team_repositories.team_id", "team_repositories.repository_id"],
                             name="fk_team_shares_team_repo", ondelete="CASCADE"),
        ForeignKeyConstraint(["review_result_id", "shared_by", "repository_id"],
                             ["ai_results.id", "ai_results.owner_user_id", "ai_results.repository_id"], name="fk_team_shares_result", ondelete="CASCADE"),
        ForeignKeyConstraint(["handover_target_id", "shared_by", "repository_id"],
                             ["handover_targets.id", "handover_targets.owner_user_id", "handover_targets.repository_id"],
                             name="fk_team_shares_target", ondelete="CASCADE"),
        UniqueConstraint("team_id", "review_result_id", name="uq_team_shares_result"),
        UniqueConstraint("team_id", "handover_target_id", name="uq_team_shares_target"),
        CheckConstraint("resource_type IN ('review_result', 'handover_target')", name="ck_team_shares_type"),
        CheckConstraint("(resource_type = 'review_result' AND review_result_id IS NOT NULL AND handover_target_id IS NULL "
                        "AND pinned_target_version IS NULL AND pinned_preparation_version IS NULL) OR "
                        "(resource_type = 'handover_target' AND handover_target_id IS NOT NULL AND review_result_id IS NULL "
                        "AND pinned_target_version IS NOT NULL AND pinned_preparation_version IS NOT NULL)", name="ck_team_shares_resource"),
        Index("ix_team_shares_team", "team_id", "created_at"),
        Index("ix_team_shares_sharer", "shared_by"),
    )


class TeamReport(Base):
    __tablename__ = "team_reports"
    id = uuid_pk()
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    created_by = Column(UUID(as_uuid=True), _user("SET NULL"))
    repository_ids = Column(JSONB, nullable=False)
    source_manifest = Column(JSONB, nullable=False)
    payload = Column(JSONB, nullable=False)
    markdown = Column(Text, nullable=False)
    payload_hash = Column(CHAR(64), nullable=False)
    visibility_epoch = Column(Integer, nullable=False)
    template_version = Column(Text, nullable=False)
    created_at = created_at()
    __table_args__ = (
        CheckConstraint(f"payload_hash ~ '{HEX64}'", name="ck_team_reports_hash"),
        CheckConstraint("jsonb_typeof(repository_ids) = 'array' AND jsonb_typeof(source_manifest) = 'object' AND jsonb_typeof(payload) = 'object'",
                        name="ck_team_reports_json"),
        CheckConstraint("char_length(markdown) <= 100000 AND visibility_epoch >= 1", name="ck_team_reports_size"),
        Index("ix_team_reports_team", "team_id", created_at.desc()),
    )
