"""Community voice chat (AD-28): up to three members of a community talk at once over WebRTC, peer to peer.

The server keeps only who is in the room (entries that expire unless heartbeats refresh them) and relays
offers, answers and ICE candidates as events on the chat stream. Audio never passes through the server.
STUN/TURN servers come from the deployment environment; without them only direct (same network) paths work.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from contextlib import contextmanager

from fastapi import HTTPException

from app.services import community as rules
from app.services import community_chat as chat

CAPACITY = 3
TTL = 30
HEARTBEAT_SECONDS = 10
JOIN_LIMIT = 10  # per minute per user
SIGNAL_LIMIT = 300  # per minute per user; one call setup sends a few dozen
TURN_CREDENTIAL_SECONDS = 12 * 3600


def room_key(repository_id):
    return f'community:voice:{int(repository_id)}'


class _LocalRooms:
    def __init__(self):
        self.lock = threading.Lock()
        self.rooms = {}
        self.hits = {}


def _local(live):
    store = getattr(live, 'voice', None)
    if store is None:
        store = live.voice = _LocalRooms()
    return store


@contextmanager
def _room(repository_id):
    """Read-modify-write one room under a lock; changes are written only if the block finishes."""
    live = chat.bus()
    key = room_key(repository_id)
    if isinstance(live, chat.RedisBus):
        client = live.sync
        try:
            lock = client.lock(f'{key}:lock', timeout=5, blocking_timeout=3)
            acquired = lock.acquire()
        except Exception:
            acquired = False
        if not acquired:
            raise rules.fail(503, 'VOICE_UNAVAILABLE', '음성 채팅 서버가 바쁩니다. 잠시 후 다시 시도하세요.')
        try:
            entries = {k.decode() if isinstance(k, bytes) else k: json.loads(v) for k, v in client.hgetall(key).items()}
            before = {s: list(v) for s, v in entries.items()}
            yield entries
            gone = [s for s in before if s not in entries]
            if gone:
                client.hdel(key, *gone)
            changed = {s: json.dumps(v) for s, v in entries.items() if before.get(s) != v}
            if changed:
                client.hset(key, mapping=changed)
            client.expire(key, TTL * 4)
        finally:
            try:
                lock.release()
            except Exception:
                pass
    else:
        store = _local(live)
        with store.lock:
            entries = store.rooms.setdefault(key, {})
            work = {s: list(v) for s, v in entries.items()}
            yield work
            entries.clear()
            entries.update(work)


def _prune(entries, now):
    stale = [s for s, (_, expires) in entries.items() if expires <= now]
    for session in stale:
        del entries[session]
    return bool(stale)


def _hit(name, limit):
    """Count one call in the current minute; raise 429 past the limit."""
    live = chat.bus()
    minute = int(time.time() // 60)
    key = f'community:voice:hits:{name}:{minute}'
    if isinstance(live, chat.RedisBus):
        try:
            count = live.sync.incr(key)
            if count == 1:
                live.sync.expire(key, 120)
        except Exception:
            count = 0
    else:
        store = _local(live)
        with store.lock:
            for old in [k for k in store.hits if not k.endswith(f':{minute}')]:
                del store.hits[old]
            count = store.hits[key] = store.hits.get(key, 0) + 1
    if count > limit:
        raise HTTPException(429, {'code': 'RATE_LIMITED', 'message': '요청이 너무 많습니다. 잠시 후 다시 시도하세요.'},
                            headers={'Retry-After': '60'})


def _urls(name, schemes):
    values = [u.strip() for u in os.getenv(name, '').split(',') if u.strip()]
    return [u for u in values if u.split(':', 1)[0] in schemes and len(u) <= 256 and not any(c.isspace() for c in u)]


def ice_servers(user_id):
    """STUN as given; TURN with short-lived REST credentials (coturn `use-auth-secret`), never the shared secret."""
    servers = [{'urls': url} for url in _urls('VOICE_STUN_URLS', ('stun', 'stuns'))]
    turn, secret = _urls('VOICE_TURN_URLS', ('turn', 'turns')), os.getenv('VOICE_TURN_SECRET', '')
    if turn and secret:
        username = f'{int(time.time()) + TURN_CREDENTIAL_SECONDS}:{user_id}'
        credential = base64.b64encode(hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
        servers.append({'urls': turn, 'username': username, 'credential': credential})
    return servers


def participants_view(db, entries):
    people = rules.users_by_id(db, [uuid.UUID(user) for user, _ in entries.values()])
    rows = [{'session_id': s, 'user': rules.user_view(people.get(uuid.UUID(user)))} for s, (user, _) in entries.items()]
    return sorted(rows, key=lambda r: ((r['user'] or {}).get('github_login') or '').lower())


def announce(db, repository_id, entries):
    chat.publish(repository_id, 'voice', {'participants': participants_view(db, entries), 'capacity': CAPACITY})


def room(db, repository_id, user_id):
    rules.membership(db, repository_id, user_id)
    with _room(repository_id) as entries:
        pruned = _prune(entries, time.time())
        snapshot = dict(entries)
    if pruned:
        announce(db, repository_id, snapshot)
    return {'participants': participants_view(db, snapshot), 'capacity': CAPACITY}


def join(db, repository_id, user_id):
    rules.membership(db, repository_id, user_id)
    _hit(f'join:{user_id}', JOIN_LIMIT)
    session = secrets.token_urlsafe(16)
    with _room(repository_id) as entries:
        now = time.time()
        _prune(entries, now)
        # One seat per person: joining again (another window, a reload) replaces the old seat.
        for old in [s for s, (user, _) in entries.items() if user == str(user_id)]:
            del entries[old]
        if len(entries) >= CAPACITY:
            raise rules.fail(409, 'VOICE_FULL', f'음성 채팅은 한 번에 {CAPACITY}명까지 참여할 수 있습니다.')
        entries[session] = [str(user_id), now + TTL]
        snapshot = dict(entries)
    announce(db, repository_id, snapshot)
    return {'session_id': session, 'participants': participants_view(db, snapshot), 'capacity': CAPACITY,
            'heartbeat_seconds': HEARTBEAT_SECONDS, 'ice_servers': ice_servers(user_id)}


def heartbeat(db, repository_id, user_id, session):
    rules.membership(db, repository_id, user_id)
    with _room(repository_id) as entries:
        now = time.time()
        pruned = _prune(entries, now)
        seat = entries.get(session)
        alive = bool(seat) and seat[0] == str(user_id)
        if alive:
            entries[session] = [str(user_id), now + TTL]
        snapshot = dict(entries)
    if pruned:
        announce(db, repository_id, snapshot)
    if not alive:
        raise rules.fail(404, 'VOICE_SESSION_ENDED', '음성 채팅 연결이 끝났습니다.')
    return {'participants': participants_view(db, snapshot), 'capacity': CAPACITY}


def leave(db, repository_id, user_id, session):
    if not user_id:
        raise rules.fail(401, 'AUTH_REQUIRED', '로그인이 필요합니다.')
    with _room(repository_id) as entries:
        seat = entries.get(session)
        left = bool(seat) and seat[0] == str(user_id)
        if left:
            del entries[session]
        snapshot = dict(entries)
    if left:
        announce(db, repository_id, snapshot)
    return {'ok': True}


def drop_user(db, repository_id, user_id):
    """A removed member loses their seat at once."""
    with _room(repository_id) as entries:
        gone = [s for s, (user, _) in entries.items() if user == str(user_id)]
        for session in gone:
            del entries[session]
        snapshot = dict(entries)
    if gone:
        announce(db, repository_id, snapshot)


def signal(db, repository_id, user_id, body):
    rules.membership(db, repository_id, user_id)
    _hit(f'signal:{user_id}', SIGNAL_LIMIT)
    with _room(repository_id) as entries:
        _prune(entries, time.time())
        mine, target = entries.get(body.session_id), entries.get(body.to)
    if not mine or mine[0] != str(user_id):
        raise rules.fail(404, 'VOICE_SESSION_ENDED', '음성 채팅 연결이 끝났습니다.')
    if not target or body.to == body.session_id:
        raise rules.fail(404, 'NOT_FOUND', '상대가 음성 채팅에 없습니다.')
    data = {'from': body.session_id, 'to': body.to, 'to_user': target[0], 'kind': body.kind}
    if body.kind == 'ice':
        data.update(candidate=body.candidate, sdp_mid=body.sdp_mid, sdp_mline_index=body.sdp_mline_index)
    else:
        data['sdp'] = body.sdp
    chat.publish(repository_id, 'voice-signal', data)
    return {'ok': True}
