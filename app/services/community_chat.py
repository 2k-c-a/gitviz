"""Community group chat (AD-27): history in PostgreSQL, live delivery as server-sent events.

Sending is a normal HTTP POST (CSRF, rate limit, desktop allowlist all apply); the stream only pushes events out.
Events cross server processes through Redis pub/sub (REDIS_URL); without Redis a single process uses an in-memory bus.
Presence is best effort: each open stream refreshes its own entry, and stale entries expire on their own.
"""
import asyncio
import json
import os
import time
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from app.core.database import SessionLocal
from app.models.community import CommunityChatMessage, CommunityMember
from app.models.user import User
from app.services import community as rules

SEND_LIMIT = 20
SEND_WINDOW = timedelta(seconds=30)
PING_SECONDS = 15
PRESENCE_TTL = 45
# Tests replace this with their fixture database.
session_factory = SessionLocal


def channel(repository_id):
    return f'community:chat:{int(repository_id)}'


class LocalBus:
    """One process only (development, tests). Publishing may come from a worker thread."""

    def __init__(self):
        self.rooms = {}
        self.present = {}

    def publish(self, name, data):
        for loop, queue in list(self.rooms.get(name, ())):
            loop.call_soon_threadsafe(queue.put_nowait, data)

    async def apublish(self, name, data):
        self.publish(name, data)

    async def subscribe(self, name):
        queue = asyncio.Queue()
        entry = (asyncio.get_running_loop(), queue)
        self.rooms.setdefault(name, set()).add(entry)
        bus = self

        class Subscription:
            async def get(self, timeout):
                try:
                    return await asyncio.wait_for(queue.get(), timeout)
                except TimeoutError:
                    return None

            async def close(self):
                bus.rooms.get(name, set()).discard(entry)

        return Subscription()

    async def touch(self, repository_id, user_id, connection):
        self.present.setdefault(repository_id, {})[connection] = (str(user_id), time.time() + PRESENCE_TTL)

    async def leave(self, repository_id, connection):
        self.present.get(repository_id, {}).pop(connection, None)

    async def online(self, repository_id):
        now = time.time()
        return {user for user, expires in self.present.get(repository_id, {}).values() if expires > now}


class RedisBus:
    def __init__(self, url):
        import redis
        import redis.asyncio
        self.sync = redis.Redis.from_url(url)
        self.client = redis.asyncio.Redis.from_url(url)

    def publish(self, name, data):
        self.sync.publish(name, data)

    async def apublish(self, name, data):
        await self.client.publish(name, data)

    async def subscribe(self, name):
        pubsub = self.client.pubsub()
        await pubsub.subscribe(name)

        class Subscription:
            async def get(self, timeout):
                # A subscribe confirmation also reads as "nothing"; keep waiting until a real message or the timeout.
                loop = asyncio.get_running_loop()
                deadline = loop.time() + timeout
                while (remaining := deadline - loop.time()) > 0:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=remaining)
                    data = message and message.get('data')
                    if data is not None:
                        return data.decode() if isinstance(data, bytes) else data
                return None

            async def close(self):
                await pubsub.unsubscribe(name)
                await pubsub.aclose()

        return Subscription()

    async def touch(self, repository_id, user_id, connection):
        await self.client.hset(f'community:presence:{int(repository_id)}', connection,
                               json.dumps([str(user_id), time.time() + PRESENCE_TTL]))

    async def leave(self, repository_id, connection):
        await self.client.hdel(f'community:presence:{int(repository_id)}', connection)

    async def online(self, repository_id):
        key = f'community:presence:{int(repository_id)}'
        now, users, stale = time.time(), set(), []
        for connection, value in (await self.client.hgetall(key)).items():
            user, expires = json.loads(value)
            if expires > now:
                users.add(user)
            else:
                stale.append(connection)
        if stale:
            await self.client.hdel(key, *stale)
        return users


_bus = None


def bus():
    global _bus
    if _bus is None:
        url = os.getenv('REDIS_URL')
        _bus = RedisBus(url) if url else LocalBus()
    return _bus


def publish(repository_id, kind, data):
    try:
        bus().publish(channel(repository_id), json.dumps({'type': kind, 'data': data}, ensure_ascii=False))
    except Exception:
        # The message is saved; people missing the live event see it on their next history load.
        pass


def message_view(row, people):
    return {'id': row.id, 'author': rules.user_view(people.get(row.author_id)), 'author_id': str(row.author_id),
            'body': row.body, 'created_at': row.created_at.isoformat() if row.created_at else None}


def history(db, repository_id, user_id, before=None, limit=50):
    rules.membership(db, repository_id, user_id)
    query = select(CommunityChatMessage).where(CommunityChatMessage.repository_id == repository_id,
                                               CommunityChatMessage.deleted_at.is_(None))
    if before:
        query = query.where(CommunityChatMessage.id < before)
    rows = list(db.scalars(query.order_by(CommunityChatMessage.id.desc()).limit(limit + 1)))
    page, more = rows[:limit], len(rows) > limit
    people = rules.users_by_id(db, [r.author_id for r in page])
    # Oldest first, the order a chat reads in; `next_before` pages further back.
    return {'messages': [message_view(r, people) for r in reversed(page)], 'next_before': page[-1].id if more else None}


def send(db, repository_id, user_id, body):
    rules.membership(db, repository_id, user_id)
    if not body.strip():
        raise rules.fail(422, 'INVALID_INPUT', '내용을 입력하세요.')
    since = rules.now() - SEND_WINDOW
    recent = db.scalar(select(func.count()).select_from(CommunityChatMessage).where(
        CommunityChatMessage.repository_id == repository_id, CommunityChatMessage.author_id == user_id,
        CommunityChatMessage.created_at > since))
    if recent >= SEND_LIMIT:
        from fastapi import HTTPException
        raise HTTPException(429, {'code': 'RATE_LIMITED', 'message': '메시지를 너무 빨리 보내고 있습니다. 잠시 후 다시 보내세요.'},
                            headers={'Retry-After': '30'})
    row = CommunityChatMessage(repository_id=repository_id, author_id=user_id, body=body, created_at=rules.now())
    db.add(row)
    db.flush()
    return message_view(row, rules.users_by_id(db, [user_id]))


def delete(db, repository_id, user_id, message_id):
    me = rules.membership(db, repository_id, user_id)
    row = db.get(CommunityChatMessage, message_id)
    if not row or row.repository_id != repository_id or row.deleted_at:
        raise rules.fail(404, 'NOT_FOUND', '메시지를 찾을 수 없습니다.')
    if row.author_id != user_id and me.role != 'admin':
        raise rules.fail(403, 'PERMISSION_DENIED', '작성자나 관리자만 지울 수 있습니다.')
    row.deleted_at = rules.now()


def check_member(repository_id, user_id):
    with session_factory() as db:
        rules.membership(db, repository_id, user_id)
        db.commit()


def online_view(repository_id, user_ids):
    """Only people who are still active members are shown, whatever presence still holds."""
    ids = [uuid.UUID(u) for u in user_ids]
    if not ids:
        return []
    with session_factory() as db:
        active = set(db.scalars(select(CommunityMember.user_id).where(
            CommunityMember.repository_id == repository_id, CommunityMember.status == 'active',
            CommunityMember.user_id.in_(ids))))
        people = db.scalars(select(User).where(User.id.in_(active)).order_by(User.github_login)) if active else []
        return [rules.user_view(u) for u in people]


def sse(kind, data):
    return f'event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


async def stream(repository_id, user_id, is_disconnected, ping=PING_SECONDS):
    """Yield server-sent events for one member until they leave, are removed, or disconnect."""
    live = bus()
    connection = uuid.uuid4().hex
    subscription = await live.subscribe(channel(repository_id))

    async def announce():
        online = await run_in_threadpool(online_view, repository_id, await live.online(repository_id))
        await live.apublish(channel(repository_id), json.dumps({'type': 'presence', 'data': {'online': online}},
                                                               ensure_ascii=False))
        return online

    try:
        await live.touch(repository_id, user_id, connection)
        online = await announce()
        yield 'retry: 3000\n\n'
        yield sse('ready', {'online': online})
        while not await is_disconnected():
            raw = await subscription.get(ping)
            if raw is None:
                await live.touch(repository_id, user_id, connection)
                yield ': ping\n\n'
                continue
            event = json.loads(raw)
            if event['type'] == 'removed':
                if event['data'].get('user_id') == str(user_id):
                    yield sse('removed', {})
                    return
                continue
            if event['type'] == 'voice-signal':
                # Signaling is addressed to one person; nobody else's stream carries it.
                data = dict(event['data'])
                if data.pop('to_user', None) != str(user_id):
                    continue
                yield sse('voice-signal', data)
                continue
            yield sse(event['type'], event['data'])
    finally:
        await subscription.close()
        await live.leave(repository_id, connection)
        try:
            await announce()
        except Exception:
            pass
