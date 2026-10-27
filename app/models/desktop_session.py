"""Additive server authentication tables; no local workspace/source data."""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, LargeBinary, String, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.core.database import Base


class DesktopAuthTransaction(Base):
    __tablename__ = 'desktop_auth_transaction'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    challenge = Column(String(64), nullable=False)
    poll_secret_hash = Column(String(64), nullable=False)
    display_code = Column(String(12), nullable=False)
    device_label = Column(String(80), nullable=False)
    issuer_hash = Column(String(64), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'))
    browser_session_binding_hash = Column(String(64))
    state = Column(String(12), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    next_poll_at = Column(DateTime(timezone=True), nullable=False)
    exchange_key_hash = Column(String(64))
    issuance_response_ciphertext = Column(LargeBinary)
    response_expires_at = Column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("state IN ('pending','authorized','exchanged','expired','denied')", name='ck_desktop_flow_state'),
        Index('ix_desktop_flow_expiry', 'expires_at', 'state'),
        Index('ix_desktop_flow_issuer', 'issuer_hash', 'created_at'),
    )


class DesktopSession(Base):
    __tablename__ = 'desktop_session'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    device_label = Column(String(80), nullable=False)
    capabilities = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True))
    last_used_at = Column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index('ix_desktop_session_user', 'user_id', 'revoked_at'),)
