"""Community real-time co-editing (AD-29): a host shares one local file, up to three people edit it together.

The file stays on the host's PC: the host's app reads it and is the only one that writes it back.
The server keeps only who is in which session (entries that expire unless heartbeats refresh them) and relays
Yjs document updates, sync messages and cursor (awareness) updates as events on the chat stream. It stores no file content.
"""
import secrets
import time
import uuid

from app.services import community as rules
from app.services import community_chat as chat
from app.services import community_voice as rooms

CAPACITY = 3
TTL = 30
HEARTBEAT_SECONDS = 10
SESSIONS_PER_COMMUNITY = 5
CREATE_LIMIT = 10  # per minute per user
RELAY_LIMIT = 1200  # per minute per user; the editor batches keystrokes
RELAY_BYTES = 28_000_000  # base64 characters per minute per user (about 20MB): a file set's images and fonts pass through


def session_key(repository_id):
    return f'community:collab:{int(repository_id)}'


def _token():
    return secrets.token_urlsafe(16)


def _prune(sessions, now):
    """Drop expired seats; a session whose host seat is gone has ended."""
    ended = []
    for sid, session in list(sessions.items()):
        seats = {s: v for s, v in session['seats'].items() if v[1] > now}
        if session['host_seat'] not in seats:
            del sessions[sid]
            ended.append(sid)
        elif seats != session['seats']:
            sessions[sid] = {**session, 'seats': seats}
    return ended


def _users(db, sessions):
    ids = {uuid.UUID(user) for session in sessions.values() for user, _ in session['seats'].values()}
    return rules.users_by_id(db, ids)


def sessions_view(db, sessions):
    people = _users(db, sessions)
    rows = []
    for sid, session in sessions.items():
        seats = [{'seat_id': seat, 'user': rules.user_view(people.get(uuid.UUID(user))), 'host': seat == session['host_seat']}
                 for seat, (user, _) in session['seats'].items()]
        seats.sort(key=lambda s: (not s['host'], ((s['user'] or {}).get('github_login') or '').lower()))
        rows.append({'session_id': sid, 'file_name': session['file_name'], 'language': session['language'],
                     'file_count': session.get('file_count'),
                     'host': next((s['user'] for s in seats if s['host']), None), 'seats': seats, 'capacity': CAPACITY,
                     'created_at': session['created_at']})
    return sorted(rows, key=lambda r: r['created_at'])


def announce(db, repository_id, sessions, ended=()):
    chat.publish(repository_id, 'collab-sessions', {'sessions': sessions_view(db, sessions), 'ended': list(ended)})


def listing(db, repository_id, user_id):
    rules.membership(db, repository_id, user_id)
    with rooms.locked(session_key(repository_id), TTL) as sessions:
        ended = _prune(sessions, time.time())
        snapshot = dict(sessions)
    if ended:
        announce(db, repository_id, snapshot, ended)
    return {'sessions': sessions_view(db, snapshot), 'capacity': CAPACITY}


def _seat_reply(db, sid, seat, snapshot):
    view = next(s for s in sessions_view(db, snapshot) if s['session_id'] == sid)
    return {'session_id': sid, 'seat_id': seat, 'session': view, 'heartbeat_seconds': HEARTBEAT_SECONDS}


def create(db, repository_id, user_id, body):
    rules.membership(db, repository_id, user_id)
    rooms.hit(f'collab-create:{user_id}', CREATE_LIMIT)
    sid, seat = _token(), _token()
    with rooms.locked(session_key(repository_id), TTL) as sessions:
        now = time.time()
        ended = _prune(sessions, now)
        # One shared file per host: sharing another one ends the previous session.
        for old in [s for s, v in sessions.items() if v['seats'].get(v['host_seat'], [None])[0] == str(user_id)]:
            del sessions[old]
            ended.append(old)
        if len(sessions) >= SESSIONS_PER_COMMUNITY:
            raise rules.fail(409, 'COLLAB_LIMIT', f'한 커뮤니티에서 동시에 {SESSIONS_PER_COMMUNITY}개까지 공동 편집할 수 있습니다.')
        sessions[sid] = {'file_name': body.file_name, 'language': body.language, 'file_count': body.file_count, 'host_seat': seat,
                         'seats': {seat: [str(user_id), now + TTL]}, 'created_at': now}
        snapshot = dict(sessions)
    announce(db, repository_id, snapshot, ended)
    return _seat_reply(db, sid, seat, snapshot)


def join(db, repository_id, user_id, sid):
    rules.membership(db, repository_id, user_id)
    rooms.hit(f'collab-create:{user_id}', CREATE_LIMIT)
    seat = _token()
    with rooms.locked(session_key(repository_id), TTL) as sessions:
        now = time.time()
        ended = _prune(sessions, now)
        session = sessions.get(sid)
        if not session:
            raise rules.fail(404, 'COLLAB_ENDED', '공동 편집이 끝났습니다.')
        seats = dict(session['seats'])
        if seats[session['host_seat']][0] == str(user_id):
            raise rules.fail(409, 'INVALID_STATE', '내가 공유한 파일입니다.')
        # One seat per person: joining again (reload, another window) replaces the old seat.
        for old in [s for s, (user, _) in seats.items() if user == str(user_id)]:
            del seats[old]
        if len(seats) >= CAPACITY:
            raise rules.fail(409, 'COLLAB_FULL', f'공동 편집은 한 번에 {CAPACITY}명까지 참여할 수 있습니다.')
        seats[seat] = [str(user_id), now + TTL]
        sessions[sid] = {**session, 'seats': seats}
        snapshot = dict(sessions)
    announce(db, repository_id, snapshot, ended)
    return _seat_reply(db, sid, seat, snapshot)


def heartbeat(db, repository_id, user_id, sid, seat):
    rules.membership(db, repository_id, user_id)
    with rooms.locked(session_key(repository_id), TTL) as sessions:
        now = time.time()
        ended = _prune(sessions, now)
        session = sessions.get(sid)
        mine = session and session['seats'].get(seat)
        alive = bool(mine) and mine[0] == str(user_id)
        if alive:
            sessions[sid] = {**session, 'seats': {**session['seats'], seat: [str(user_id), now + TTL]}}
        snapshot = dict(sessions)
    if ended:
        announce(db, repository_id, snapshot, ended)
    if not alive:
        raise rules.fail(404, 'COLLAB_ENDED', '공동 편집이 끝났습니다.')
    return _seat_reply(db, sid, seat, snapshot)


def leave(db, repository_id, user_id, sid, seat):
    """A guest leaves; the host leaving ends the session for everyone."""
    if not user_id:
        raise rules.fail(401, 'AUTH_REQUIRED', '로그인이 필요합니다.')
    with rooms.locked(session_key(repository_id), TTL) as sessions:
        session = sessions.get(sid)
        mine = session and session['seats'].get(seat)
        changed = bool(mine) and mine[0] == str(user_id)
        ended = []
        if changed and seat == session['host_seat']:
            del sessions[sid]
            ended.append(sid)
        elif changed:
            sessions[sid] = {**session, 'seats': {s: v for s, v in session['seats'].items() if s != seat}}
        snapshot = dict(sessions)
    if changed:
        announce(db, repository_id, snapshot, ended)
    return {'ok': True}


def drop_user(db, repository_id, user_id):
    """A removed member loses every seat at once; sessions they host end."""
    with rooms.locked(session_key(repository_id), TTL) as sessions:
        ended, changed = [], False
        for sid, session in list(sessions.items()):
            seats = {s: v for s, v in session['seats'].items() if v[0] != str(user_id)}
            if seats == session['seats']:
                continue
            changed = True
            if session['host_seat'] not in seats:
                del sessions[sid]
                ended.append(sid)
            else:
                sessions[sid] = {**session, 'seats': seats}
        snapshot = dict(sessions)
    if changed:
        announce(db, repository_id, snapshot, ended)


def relay(db, repository_id, user_id, sid, body):
    """Forward one Yjs message from a seat to the other seats (or to one seat for sync replies)."""
    rules.membership(db, repository_id, user_id)
    rooms.hit(f'collab-relay:{user_id}', RELAY_LIMIT)
    rooms.hit(f'collab-bytes:{user_id}', RELAY_BYTES, weight=len(body.data))
    with rooms.locked(session_key(repository_id), TTL) as sessions:
        _prune(sessions, time.time())
        session = sessions.get(sid)
        seats = dict(session['seats']) if session else {}
    mine = seats.get(body.seat_id)
    if not mine or mine[0] != str(user_id):
        raise rules.fail(404, 'COLLAB_ENDED', '공동 편집이 끝났습니다.')
    if body.to is not None:
        if body.to not in seats or body.to == body.seat_id:
            raise rules.fail(404, 'NOT_FOUND', '상대가 공동 편집에 없습니다.')
        targets = [body.to]
    else:
        targets = [s for s in seats if s != body.seat_id]
    users = sorted({seats[s][0] for s in targets})
    if users:
        message = {'session_id': sid, 'from': body.seat_id, 'to': body.to, 'kind': body.kind, 'data': body.data, 'to_users': users}
        if body.path is not None:
            message['path'] = body.path
        chat.publish(repository_id, 'collab', message)
    return {'ok': True}
