"""Short-lived public-client flow. Callers own and commit the DB transaction."""
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from sqlalchemy import select, func, text, delete

from app.models.desktop_session import DesktopAuthTransaction, DesktopSession
from app.models.user import User

CAPABILITIES = ['remote-v1']
API_CAPABILITIES = {'desktopProtocol': 1, 'aiContract': '1.9'}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def now():
    return datetime.now(timezone.utc)


def settings():
    if os.getenv('DESKTOP_AUTH_ENABLED', '0') != '1':
        raise HTTPException(404, '사용할 수 없는 기능입니다.')
    origin = os.getenv('DESKTOP_PUBLIC_ORIGIN', '')
    parsed = urlsplit(origin)
    key = os.getenv('DESKTOP_AUTH_RESPONSE_KEY', '')
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or not re.fullmatch(r'[0-9a-fA-F]{64}', key)):
        raise RuntimeError('Desktop authentication configuration is invalid.')
    if os.getenv('SESSION_COOKIE_SECURE') != '1':
        raise RuntimeError('Desktop browser authentication requires secure session cookies.')
    return origin, bytes.fromhex(key)


def unavailable():
    return HTTPException(401, '인증 요청을 확인할 수 없습니다.', headers={'Cache-Control': 'no-store'})


def create_flow(db, challenge, label, issuer):
    origin, _ = settings()
    # Shared across workers. No client-supplied forwarding header is trusted here.
    issuer_hash = digest(issuer)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int(issuer_hash[:15], 16)})
    moment = now()
    # Authentication-only TTL cleanup, with replay grace; never touches user/analysis data.
    db.execute(delete(DesktopAuthTransaction).where(DesktopAuthTransaction.expires_at < moment - timedelta(seconds=60)))
    recent = db.scalar(select(func.count()).select_from(DesktopAuthTransaction).where(
        DesktopAuthTransaction.issuer_hash == issuer_hash,
        DesktopAuthTransaction.created_at > moment - timedelta(minutes=5)))
    if recent >= 10:
        raise HTTPException(429, '잠시 후 다시 시도해 주세요.', headers={'Retry-After': '60'})
    poll_secret = secrets.token_urlsafe(32)
    flow = DesktopAuthTransaction(id=uuid.uuid4(), challenge=challenge, poll_secret_hash=digest(poll_secret),
        display_code=secrets.token_hex(4).upper(), device_label=label, issuer_hash=issuer_hash,
        state='pending', created_at=moment, expires_at=moment + timedelta(minutes=5), next_poll_at=moment)
    db.add(flow)
    db.flush()
    return {'flowId': str(flow.id), 'browserUrl': f'{origin}/api/desktop/v1/auth/authorize?flow={flow.id}',
            'pollSecret': poll_secret, 'displayCode': flow.display_code,
            'expiresAt': flow.expires_at.isoformat(), 'pollAfterMs': 2000}


def locked_flow(db, flow_id):
    return db.scalar(select(DesktopAuthTransaction).where(DesktopAuthTransaction.id == flow_id).with_for_update())


def bind_browser(db, flow_id, binding):
    flow = locked_flow(db, flow_id)
    if not flow or flow.state != 'pending' or flow.expires_at <= now():
        raise unavailable()
    hashed = digest(binding)
    if flow.browser_session_binding_hash and not hmac.compare_digest(flow.browser_session_binding_hash, hashed):
        raise unavailable()
    flow.browser_session_binding_hash = hashed
    return flow


def authorize(db, flow_id, binding, user_id, display_code, approve):
    flow = bind_browser(db, flow_id, binding)
    if not hmac.compare_digest(flow.display_code, display_code):
        raise unavailable()
    flow.user_id = user_id
    flow.state = 'authorized' if approve else 'denied'


def user_view(user):
    return {'id': str(user.id), 'github_login': user.github_login, 'display_name': user.display_name,
            'avatar_url': user.avatar_url, 'email': user.email}


def exchange(db, flow_id, poll_secret, verifier, key):
    _, encryption_key = settings()
    flow = locked_flow(db, flow_id)
    moment = now()
    if (not flow or not hmac.compare_digest(flow.poll_secret_hash, digest(poll_secret))
            or not hmac.compare_digest(flow.challenge, digest(verifier))):
        raise unavailable()
    if flow.state == 'exchanged':
        if not hmac.compare_digest(flow.exchange_key_hash, digest(key)):
            raise HTTPException(409, '이미 처리한 인증 요청입니다.')
        if flow.response_expires_at <= moment or not flow.issuance_response_ciphertext:
            flow.issuance_response_ciphertext = None
            raise unavailable()
        blob = flow.issuance_response_ciphertext
        result = json.loads(AESGCM(encryption_key).decrypt(blob[:12], blob[12:], flow.id.bytes))
        session = db.get(DesktopSession, uuid.UUID(result['sessionId']))
        if not session or session.revoked_at or session.expires_at <= moment:
            raise unavailable()
        return 200, result
    if flow.expires_at <= moment:
        raise unavailable()
    if flow.state == 'denied':
        raise unavailable()
    if flow.next_poll_at > moment:
        raise HTTPException(429, '잠시 후 다시 시도해 주세요.', headers={'Retry-After': '2'})
    flow.next_poll_at = moment + timedelta(seconds=2)
    if flow.state == 'pending':
        return 202, {'state': 'pending'}
    if flow.state != 'authorized' or not flow.user_id:
        raise unavailable()
    user = db.get(User, flow.user_id)
    if not user:
        raise unavailable()
    token = secrets.token_urlsafe(48)
    session = DesktopSession(id=uuid.uuid4(), user_id=user.id, token_hash=digest(token),
        device_label=flow.device_label, capabilities=CAPABILITIES, created_at=moment,
        expires_at=moment + timedelta(hours=8), last_used_at=moment)
    db.add(session)
    result = {'sessionToken': token, 'sessionId': str(session.id), 'user': user_view(user),
              'capabilities': CAPABILITIES, 'expiresAt': session.expires_at.isoformat(),
              'apiCapabilities': API_CAPABILITIES}
    nonce = secrets.token_bytes(12)
    flow.state = 'exchanged'
    flow.exchange_key_hash = digest(key)
    flow.response_expires_at = moment + timedelta(seconds=60)
    flow.issuance_response_ciphertext = nonce + AESGCM(encryption_key).encrypt(
        nonce, json.dumps(result).encode(), flow.id.bytes)
    return 200, result


@dataclass(frozen=True)
class DesktopPrincipal:
    user_id: uuid.UUID
    session_id: uuid.UUID


def authenticate(db, token, allow_revoked=False):
    settings()
    if not re.fullmatch(r'[A-Za-z0-9_-]{64}', token):
        raise unavailable()
    session = db.scalar(select(DesktopSession).where(DesktopSession.token_hash == digest(token)))
    if not session or session.expires_at <= now() or (session.revoked_at and not allow_revoked):
        raise unavailable()
    if not db.get(User, session.user_id):
        raise unavailable()
    session.last_used_at = now()
    return DesktopPrincipal(session.user_id, session.id)
