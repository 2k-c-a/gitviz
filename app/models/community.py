"""Project communities: one per analyzed repository. Members, URL invites, posts, comments and a shared schedule.

Contributors join automatically, everyone else only through an invite, and the first analyzer starts as admin.
Content is plain text and only ever soft-deleted, so moderation keeps a record.
"""
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.core.database import Base


class CommunityMember(Base):
    __tablename__ = 'community_members'
    id = Column(Integer, primary_key=True)
    repository_id = Column(Integer, ForeignKey('repositories.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    role = Column(String(12), nullable=False)
    status = Column(String(12), nullable=False, server_default='active')
    joined_via = Column(String(12), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint('repository_id', 'user_id', name='uq_community_member'),
        CheckConstraint("role IN ('admin','member')", name='ck_community_member_role'),
        CheckConstraint("status IN ('active','removed')", name='ck_community_member_status'),
        CheckConstraint("joined_via IN ('analyzer','contributor','invite')", name='ck_community_member_via'),
        # At most one admin per community; transfer swaps roles in one transaction.
        Index('uq_community_admin', 'repository_id', unique=True,
              postgresql_where=text("role = 'admin'"), sqlite_where=text("role = 'admin'")),
    )


class CommunityInvite(Base):
    __tablename__ = 'community_invites'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id = Column(Integer, ForeignKey('repositories.id', ondelete='CASCADE'), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    max_uses = Column(Integer)
    use_count = Column(Integer, nullable=False, server_default='0')
    revoked_at = Column(DateTime(timezone=True))


class CommunityPost(Base):
    __tablename__ = 'community_posts'
    id = Column(Integer, primary_key=True)
    repository_id = Column(Integer, ForeignKey('repositories.id', ondelete='CASCADE'), nullable=False)
    author_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True))
    deleted_at = Column(DateTime(timezone=True))
    __table_args__ = (Index('ix_community_posts_feed', 'repository_id', 'id'),)


class CommunityComment(Base):
    __tablename__ = 'community_comments'
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey('community_posts.id', ondelete='CASCADE'), nullable=False)
    author_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True))
    deleted_at = Column(DateTime(timezone=True))
    __table_args__ = (Index('ix_community_comments_post', 'post_id', 'id'),)


class CommunityChatMessage(Base):
    """Group chat line. Live delivery is a server-sent event; this table is the history new members scroll back through."""
    __tablename__ = 'community_chat_messages'
    id = Column(Integer, primary_key=True)
    repository_id = Column(Integer, ForeignKey('repositories.id', ondelete='CASCADE'), nullable=False)
    author_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at = Column(DateTime(timezone=True))
    __table_args__ = (Index('ix_community_chat_room', 'repository_id', 'id'),)


class CommunityEvent(Base):
    """One schedule entry (2026-11-07). A recurring entry is one row expanded per query; edits apply to the series.

    `details` holds what only some types use: goal (sprint, milestone), version and environment (deploy, release),
    linked issue and pull request numbers, a release tag and attachment links. Sprints and milestones are events of
    those types that other events point at through sprint_id / milestone_id.
    """
    __tablename__ = 'community_events'
    id = Column(Integer, primary_key=True)
    repository_id = Column(Integer, ForeignKey('repositories.id', ondelete='CASCADE'), nullable=False)
    author_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, server_default='')
    event_type = Column(String(20), nullable=False)
    status = Column(String(12), nullable=False, server_default='planned')
    starts_at = Column(DateTime(timezone=True), nullable=False)
    ends_at = Column(DateTime(timezone=True), nullable=False)
    all_day = Column(Boolean, nullable=False, server_default='false')
    assignee_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'))
    location = Column(String(200))
    meeting_url = Column(String(500))
    visibility = Column(String(12), nullable=False, server_default='community')
    reminder_minutes = Column(Integer)
    recurrence = Column(String(8), nullable=False, server_default='none')
    recurrence_until = Column(DateTime(timezone=True))
    sprint_id = Column(Integer, ForeignKey('community_events.id', ondelete='SET NULL'))
    milestone_id = Column(Integer, ForeignKey('community_events.id', ondelete='SET NULL'))
    post_id = Column(Integer, ForeignKey('community_posts.id', ondelete='SET NULL'))
    details = Column(JSONB, nullable=False, server_default='{}')
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True))
    deleted_at = Column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("event_type IN ('meeting','team_meeting','tech_share','community_event','other','task',"
                        "'code_review','sprint','milestone','deploy','release')", name='ck_community_event_type'),
        CheckConstraint("status IN ('planned','in_progress','done','cancelled')", name='ck_community_event_status'),
        CheckConstraint("visibility IN ('community','participants')", name='ck_community_event_visibility'),
        CheckConstraint("recurrence IN ('none','daily','weekly','monthly')", name='ck_community_event_recurrence'),
        CheckConstraint('ends_at >= starts_at', name='ck_community_event_range'),
        Index('ix_community_events_calendar', 'repository_id', 'starts_at'),
    )


class CommunityEventParticipant(Base):
    __tablename__ = 'community_event_participants'
    event_id = Column(Integer, ForeignKey('community_events.id', ondelete='CASCADE'), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    response = Column(String(10), nullable=False, server_default='pending')
    responded_at = Column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("response IN ('pending','accepted','declined','tentative')", name='ck_community_event_response'),
        Index('ix_community_event_participants_user', 'user_id'),
    )
