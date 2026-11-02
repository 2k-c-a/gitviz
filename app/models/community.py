"""Project communities: one per analyzed repository. Members, URL invites, posts and comments.

Contributors join automatically, everyone else only through an invite, and the first analyzer starts as admin.
Content is plain text and only ever soft-deleted, so moderation keeps a record.
"""
import uuid

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import UUID
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
