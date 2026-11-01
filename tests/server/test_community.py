"""Community rules over HTTP on a SQLite fixture (no PostgreSQL row-lock claim, no network).

Owner decisions under test: contributors join automatically, others only by invite, the first analyzer is admin,
and the admin can hand the role over.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['DATABASE_URL'] = 'postgresql://gitviz_test:unused@localhost/gitviz_test'
os.environ['DATABASE_URL_ASYNC'] = ''
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event, select  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from app.api.ai.dependencies import require_csrf  # noqa: E402
from app.api.community import router  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.models.analysis_job import AnalysisJob  # noqa: E402
from app.models.community import (  # noqa: E402
    CommunityChatMessage,
    CommunityComment,
    CommunityInvite,
    CommunityMember,
    CommunityPost,
)
from app.models.contributor import Contributor  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import community as service  # noqa: E402
from app.services import community_chat as chat  # noqa: E402
from app.services import community_voice as voice  # noqa: E402


@compiles(JSONB, 'sqlite')
def sqlite_json(type_, compiler, **kw):
    return 'JSON'


@event.listens_for(Session, 'loaded_as_persistent')
def sqlite_timezone(session, obj):
    # Fixture only: SQLite drops the offset that PostgreSQL TIMESTAMPTZ keeps.
    for column in obj.__table__.columns:
        value = getattr(obj, column.key)
        if isinstance(value, datetime) and value.tzinfo is None:
            setattr(obj, column.key, value.replace(tzinfo=timezone.utc))


class CommunityTests(unittest.TestCase):
    def setUp(self):
        # Invite links are built from the public origin; pin it so the test does not depend on test order.
        self.origin = patch.dict(os.environ, DESKTOP_PUBLIC_ORIGIN='https://gitviz.example')
        self.origin.start()
        self.engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})

        @event.listens_for(self.engine, 'connect')
        def foreign_keys(connection, record):
            connection.execute('PRAGMA foreign_keys=ON')

        for model in (User, Repository, AnalysisJob, Contributor, CommunityMember, CommunityInvite, CommunityPost,
                      CommunityComment, CommunityChatMessage):
            model.__table__.create(self.engine)
        self.factory = sessionmaker(self.engine)
        self.users = {name: uuid4() for name in ('owner', 'later', 'contrib', 'outsider', 'guest')}
        with self.factory.begin() as db:
            for i, (name, uid) in enumerate(self.users.items()):
                db.add(User(id=uid, github_id=100 + i, github_login=name.capitalize()))
            db.add(Repository(id=1, url='https://github.com/acme/tool', name='tool', github_id=1, is_private=False))
            db.add(Repository(id=2, url='https://github.com/acme/other', name='other', github_id=2, is_private=False))
            db.flush()
            db.add(AnalysisJob(repository_id=1, status='done', triggered_by_user_id=self.users['owner']))
            db.flush()
            db.add(AnalysisJob(repository_id=1, status='done', triggered_by_user_id=self.users['later']))
            db.add(Contributor(repository_id=1, primary_email='c@example.test', github_login='contrib'))
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key='community-fixture')

        def database():
            with self.factory() as db:
                yield db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[require_csrf] = lambda: None
        app.include_router(router)

        @app.get('/fixture/login/{name}')
        def login(name: str, request: Request):
            request.session['user_id'] = str(self.users[name])
            return {'ok': True}

        self.app = app
        self.clients = {}
        self.bus, self.sessions = chat._bus, chat.session_factory
        chat._bus, chat.session_factory = chat.LocalBus(), self.factory

    def tearDown(self):
        for client in self.clients.values():
            client.close()
        chat._bus, chat.session_factory = self.bus, self.sessions
        self.engine.dispose()
        self.origin.stop()

    def as_(self, name):
        if name not in self.clients:
            client = TestClient(self.app)
            client.get(f'/fixture/login/{name}')
            self.clients[name] = client
        return self.clients[name]

    def code(self, response):
        return response.json()['detail']['code']

    def invite(self, **body):
        response = self.as_('owner').post('/api/community/v1/repositories/1/invites', json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def token(self, invite):
        return invite['url'].split('#invite=', 1)[1]

    def test_first_analyzer_is_admin_contributor_joins_and_outsider_needs_invite(self):
        self.assertEqual(TestClient(self.app).get('/api/community/v1/repositories/1').status_code, 401)
        # A later analyzer is not the first one and is not a contributor.
        self.assertEqual(self.code(self.as_('later').get('/api/community/v1/repositories/1')), 'INVITE_REQUIRED')
        view = self.as_('contrib').get('/api/community/v1/repositories/1').json()
        self.assertEqual(view['me'], {'role': 'member', 'joined_via': 'contributor'})
        self.assertEqual(view['admin']['github_login'], 'Owner')
        self.assertEqual(view['member_count'], 2)
        self.assertEqual(self.as_('owner').get('/api/community/v1/repositories/1').json()['me']['role'], 'admin')
        self.assertEqual(self.code(self.as_('outsider').get('/api/community/v1/repositories/1')), 'INVITE_REQUIRED')
        self.assertEqual(self.as_('outsider').get('/api/community/v1/repositories/99').status_code, 404)
        listed = self.as_('contrib').get('/api/community/v1/communities').json()['communities']
        self.assertEqual([(c['repository']['id'], c['joined']) for c in listed], [(1, True)])

    def test_invite_link_is_hashed_single_purpose_and_bounded(self):
        self.as_('contrib').get('/api/community/v1/repositories/1')
        self.assertEqual(self.code(self.as_('contrib').post('/api/community/v1/repositories/1/invites', json={})),
                         'ADMIN_REQUIRED')
        created = self.invite(max_uses=1, expires_in_hours=24)
        token = self.token(created)
        self.assertTrue(created['url'].startswith('https://gitviz.example/pages/community.html#invite='))
        with self.factory() as db:
            stored = db.scalar(select(CommunityInvite))
            self.assertEqual(stored.token_hash, service.digest(token))
            self.assertNotIn(token, stored.token_hash)
        joined = self.as_('outsider').post('/api/community/v1/invites/redeem', json={'token': token})
        self.assertEqual(joined.json(), {'repository_id': 1})
        self.assertEqual(self.as_('outsider').get('/api/community/v1/repositories/1').json()['me']['joined_via'], 'invite')
        # Reopening as a member does not use it up again; a second new person finds it used up.
        self.assertEqual(self.as_('outsider').post('/api/community/v1/invites/redeem', json={'token': token}).status_code, 410)
        self.assertEqual(self.code(self.as_('guest').post('/api/community/v1/invites/redeem', json={'token': token})),
                         'INVITE_INVALID')
        revoked = self.invite()
        self.as_('owner').delete(f"/api/community/v1/repositories/1/invites/{revoked['invite']['id']}")
        self.assertEqual(self.as_('guest').post('/api/community/v1/invites/redeem',
                                                json={'token': self.token(revoked)}).status_code, 410)
        expired = self.invite()
        with self.factory.begin() as db:
            db.get(CommunityInvite, __import__('uuid').UUID(expired['invite']['id'])).expires_at = service.now() - timedelta(seconds=1)
        self.assertEqual(self.as_('guest').post('/api/community/v1/invites/redeem',
                                                json={'token': self.token(expired)}).status_code, 410)
        self.assertEqual(self.as_('guest').post('/api/community/v1/invites/redeem', json={'token': 'x'}).status_code, 422)
        self.assertEqual(self.as_('owner').get('/api/community/v1/repositories/1/invites').json()['invites'], [])

    def test_admin_hands_over_role_and_removed_members_stay_out(self):
        self.as_('contrib').get('/api/community/v1/repositories/1')
        contrib = str(self.users['contrib'])
        owner = str(self.users['owner'])
        self.assertEqual(self.code(self.as_('contrib').post('/api/community/v1/repositories/1/admin',
                                                            json={'user_id': owner})), 'ADMIN_REQUIRED')
        self.assertEqual(self.code(self.as_('owner').patch(f'/api/community/v1/repositories/1/members/{owner}',
                                                           json={'status': 'removed'})), 'INVALID_STATE')
        self.assertEqual(self.as_('owner').post('/api/community/v1/repositories/1/admin',
                                                json={'user_id': contrib}).status_code, 200)
        self.assertEqual(self.as_('contrib').get('/api/community/v1/repositories/1').json()['me']['role'], 'admin')
        self.assertEqual(self.as_('owner').get('/api/community/v1/repositories/1').json()['me']['role'], 'member')
        with self.factory() as db:
            admins = db.scalars(select(CommunityMember).where(CommunityMember.role == 'admin')).all()
            self.assertEqual([a.user_id for a in admins], [self.users['contrib']])
        # The new admin removes the old one; being the first analyzer does not bring them back.
        self.as_('contrib').patch(f'/api/community/v1/repositories/1/members/{owner}', json={'status': 'removed'})
        self.assertEqual(self.code(self.as_('owner').get('/api/community/v1/repositories/1')), 'MEMBER_REMOVED')
        token = self.token(self.as_('contrib').post('/api/community/v1/repositories/1/invites', json={}).json())
        self.assertEqual(self.code(self.as_('owner').post('/api/community/v1/invites/redeem', json={'token': token})),
                         'MEMBER_REMOVED')
        members = self.as_('contrib').get('/api/community/v1/repositories/1/members').json()['members']
        self.assertIn(('Owner', 'removed'), [(m['user']['github_login'], m['status']) for m in members])
        self.as_('contrib').patch(f'/api/community/v1/repositories/1/members/{owner}', json={'status': 'active'})
        self.assertEqual(self.as_('owner').get('/api/community/v1/repositories/1').json()['me']['role'], 'member')

    def test_posts_comments_permissions_paging_and_rate_limit(self):
        owner, contrib = self.as_('owner'), self.as_('contrib')
        contrib.get('/api/community/v1/repositories/1')
        self.assertEqual(self.as_('outsider').post('/api/community/v1/repositories/1/posts',
                                                   json={'title': 'x', 'body': 'y'}).status_code, 403)
        self.assertEqual(contrib.post('/api/community/v1/repositories/1/posts',
                                      json={'title': 'a\nb', 'body': 'y'}).status_code, 422)
        first = contrib.post('/api/community/v1/repositories/1/posts',
                             json={'title': '질문', 'body': '<script>x</script>\n줄바꿈'}).json()['id']
        comment = owner.post(f'/api/community/v1/repositories/1/posts/{first}/comments', json={'body': '답변'}).json()['id']
        detail = contrib.get(f'/api/community/v1/repositories/1/posts/{first}').json()
        self.assertEqual(detail['body'], '<script>x</script>\n줄바꿈')
        self.assertEqual([c['body'] for c in detail['comments']], ['답변'])
        self.assertEqual(self.code(owner.patch(f'/api/community/v1/repositories/1/posts/{first}',
                                               json={'title': 't', 'body': 'b'})), 'PERMISSION_DENIED')
        self.assertEqual(self.code(contrib.patch(f'/api/community/v1/repositories/1/comments/{comment}',
                                                 json={'body': 'b'})), 'PERMISSION_DENIED')
        self.assertEqual(contrib.patch(f'/api/community/v1/repositories/1/posts/{first}',
                                       json={'title': '수정', 'body': 'b'}).status_code, 200)
        # A post from another community is not reachable through this one.
        self.assertEqual(contrib.get(f'/api/community/v1/repositories/2/posts/{first}').status_code, 403)
        # One post above plus nine more reaches the limit of ten per minute.
        for i in range(9):
            contrib.post('/api/community/v1/repositories/1/posts', json={'title': f'p{i}', 'body': 'b'})
        limited = contrib.post('/api/community/v1/repositories/1/posts', json={'title': 'one too many', 'body': 'b'})
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers['retry-after'], '60')
        page = contrib.get('/api/community/v1/repositories/1/posts', params={'limit': 5}).json()
        self.assertEqual(len(page['posts']), 5)
        rest = contrib.get('/api/community/v1/repositories/1/posts', params={'limit': 5, 'before': page['next_before']}).json()
        self.assertEqual([p['id'] for p in rest['posts']][-1], first)
        self.assertEqual(rest['posts'][-1]['comment_count'], 1)
        self.assertIsNone(rest['next_before'])
        # The admin can delete someone else's post; it leaves the feed and takes no more comments.
        self.assertEqual(owner.delete(f'/api/community/v1/repositories/1/posts/{first}').status_code, 200)
        self.assertEqual(contrib.get(f'/api/community/v1/repositories/1/posts/{first}').status_code, 404)
        self.assertEqual(owner.post(f'/api/community/v1/repositories/1/posts/{first}/comments',
                                    json={'body': 'late'}).status_code, 404)
        self.assertNotIn(first, [p['id'] for p in contrib.get('/api/community/v1/repositories/1/posts',
                                                              params={'limit': 50}).json()['posts']])
        with self.factory() as db:
            self.assertIsNotNone(db.get(CommunityPost, first).deleted_at)

    def test_chat_history_send_delete_and_rate_limit(self):
        owner, contrib = self.as_('owner'), self.as_('contrib')
        contrib.get('/api/community/v1/repositories/1')
        url = '/api/community/v1/repositories/1/chat/messages'
        self.assertEqual(self.code(self.as_('outsider').post(url, json={'body': 'hi'})), 'INVITE_REQUIRED')
        self.assertEqual(self.as_('outsider').get('/api/community/v1/repositories/1/chat/stream').status_code, 403)
        self.assertEqual(TestClient(self.app).get('/api/community/v1/repositories/1/chat/stream').status_code, 401)
        self.assertEqual(contrib.post(url, json={'body': ''}).status_code, 422)
        self.assertEqual(contrib.post(url, json={'body': 'a\x00'}).status_code, 422)
        self.assertEqual(contrib.post(url, json={'body': '   '}).status_code, 422)
        first = contrib.post(url, json={'body': '<b>안녕</b>\n둘째 줄'})
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(first.json()['author']['github_login'], 'Contrib')
        mine = owner.post(url, json={'body': '관리자'}).json()['id']
        self.assertEqual(self.code(contrib.delete(f'{url}/{mine}')), 'PERMISSION_DENIED')
        # The admin removes anyone's message; a removed message is gone for everyone.
        self.assertEqual(owner.delete(f"{url}/{first.json()['id']}").status_code, 200)
        self.assertEqual(owner.delete(f"{url}/{first.json()['id']}").status_code, 404)
        self.assertEqual([m['body'] for m in contrib.get(url).json()['messages']], ['관리자'])
        for i in range(19):
            self.assertEqual(owner.post(url, json={'body': f'm{i}'}).status_code, 201)
        limited = owner.post(url, json={'body': 'one too many'})
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers['retry-after'], '30')
        page = contrib.get(url, params={'limit': 5}).json()
        self.assertEqual([m['body'] for m in page['messages']], [f'm{i}' for i in range(14, 19)])
        rest = contrib.get(url, params={'limit': 50, 'before': page['next_before']}).json()
        self.assertEqual(rest['messages'][0]['body'], '관리자')
        self.assertIsNone(rest['next_before'])
        # Messages stay in their own room.
        self.assertEqual(owner.get('/api/community/v1/repositories/2/chat/messages').status_code, 403)

    def test_chat_stream_delivers_messages_presence_and_ends_for_a_removed_member(self):
        import asyncio
        import json
        self.as_('contrib').get('/api/community/v1/repositories/1')
        url = '/api/community/v1/repositories/1/chat/messages'

        async def connected():
            return False

        def parse(chunk):
            fields = dict(line.split(': ', 1) for line in chunk.strip().splitlines() if not line.startswith(':'))
            return fields.get('event'), json.loads(fields['data']) if 'data' in fields else None

        def logins(data):
            return sorted(u['github_login'] for u in data['online'])

        async def scenario():
            stream = chat.stream(1, self.users['contrib'], connected, ping=0.05)
            self.assertEqual(await anext(stream), 'retry: 3000\n\n')
            kind, data = parse(await anext(stream))
            self.assertEqual((kind, logins(data)), ('ready', ['Contrib']))
            # Its own arrival is announced to the room, itself included.
            kind, data = parse(await anext(stream))
            self.assertEqual((kind, logins(data)), ('presence', ['Contrib']))
            # The admin connects too; the first stream hears about it, and about them leaving.
            other = chat.stream(1, self.users['owner'], connected, ping=0.05)
            await anext(other)
            await anext(other)
            kind, data = parse(await anext(stream))
            self.assertEqual((kind, logins(data)), ('presence', ['Contrib', 'Owner']))
            await other.aclose()
            kind, data = parse(await anext(stream))
            self.assertEqual((kind, logins(data)), ('presence', ['Contrib']))
            self.assertEqual(await anext(stream), ': ping\n\n')
            sent = await asyncio.to_thread(self.as_('owner').post, url, json={'body': '실시간'})
            kind, data = parse(await anext(stream))
            self.assertEqual((kind, data['body'], data['author']['github_login']), ('message', '실시간', 'Owner'))
            await asyncio.to_thread(self.as_('owner').delete, f"{url}/{sent.json()['id']}")
            self.assertEqual(parse(await anext(stream)), ('deleted', {'id': sent.json()['id']}))
            # Removing this member ends their stream at once.
            await asyncio.to_thread(self.as_('owner').patch,
                                    f"/api/community/v1/repositories/1/members/{self.users['contrib']}",
                                    json={'status': 'removed'})
            self.assertEqual(parse(await anext(stream)), ('removed', {}))
            with self.assertRaises(StopAsyncIteration):
                await anext(stream)
            self.assertEqual(await chat.bus().online(1), set())
            self.assertEqual(chat.bus().rooms[chat.channel(1)], set())

        asyncio.run(scenario())

    def test_chat_stream_stops_when_the_client_disconnects(self):
        import asyncio

        async def scenario():
            gone = asyncio.Event()

            async def disconnected():
                return gone.is_set()

            stream = chat.stream(1, self.users['owner'], disconnected, ping=0.05)
            await anext(stream)
            await anext(stream)
            gone.set()
            with self.assertRaises(StopAsyncIteration):
                await anext(stream)
            self.assertEqual(await chat.bus().online(1), set())

        asyncio.run(scenario())


    def voice_members(self):
        # owner (admin), contrib (contributor), outsider and guest by invite: four members for a room of three.
        self.as_('contrib').get('/api/community/v1/repositories/1')
        for name in ('outsider', 'guest'):
            token = self.token(self.invite())
            self.assertEqual(self.as_(name).post('/api/community/v1/invites/redeem', json={'token': token}).status_code, 200)

    def test_voice_room_holds_three_one_seat_each_and_expires_silent_seats(self):
        self.voice_members()
        base = '/api/community/v1/repositories/1/voice'
        self.assertEqual(self.code(self.as_('later').post(f'{base}/join')), 'INVITE_REQUIRED')
        self.assertEqual(TestClient(self.app).post(f'{base}/join').status_code, 401)
        seats = {}
        for name in ('owner', 'contrib', 'outsider'):
            joined = self.as_(name).post(f'{base}/join')
            self.assertEqual(joined.status_code, 200, joined.text)
            seats[name] = joined.json()['session_id']
        self.assertEqual(joined.json()['capacity'], 3)
        self.assertEqual(joined.json()['heartbeat_seconds'], 10)
        self.assertEqual(sorted(p['user']['github_login'] for p in joined.json()['participants']), ['Contrib', 'Outsider', 'Owner'])
        full = self.as_('guest').post(f'{base}/join')
        self.assertEqual((full.status_code, self.code(full)), (409, 'VOICE_FULL'))
        # Joining again replaces one's own seat instead of taking a second one.
        again = self.as_('owner').post(f'{base}/join').json()
        self.assertEqual(len(again['participants']), 3)
        self.assertNotIn(seats['owner'], [p['session_id'] for p in again['participants']])
        self.assertEqual(self.code(self.as_('owner').post(f'{base}/heartbeat', json={'session_id': seats['owner']})), 'VOICE_SESSION_ENDED')
        seats['owner'] = again['session_id']
        # Someone else's seat cannot be refreshed or given up.
        self.assertEqual(self.code(self.as_('contrib').post(f'{base}/heartbeat', json={'session_id': seats['owner']})), 'VOICE_SESSION_ENDED')
        self.as_('contrib').post(f'{base}/leave', json={'session_id': seats['owner']})
        self.assertEqual(len(self.as_('guest').get(base).json()['participants']), 3)
        self.assertEqual(self.as_('contrib').post(f'{base}/leave', json={'session_id': seats['contrib']}).status_code, 200)
        self.assertEqual(self.as_('guest').post(f'{base}/join').status_code, 200)
        # A seat without heartbeats expires and frees its place.
        with patch.object(voice.time, 'time', return_value=voice.time.time() + voice.TTL + 1):
            self.assertEqual(self.as_('contrib').get(base).json()['participants'], [])
        self.assertEqual(self.as_('owner').post(f'{base}/heartbeat', json={'session_id': 'x'}).status_code, 422)

    def test_voice_signals_reach_only_the_addressed_seat_and_removal_frees_the_seat(self):
        import asyncio
        import json
        self.voice_members()
        base = '/api/community/v1/repositories/1/voice'
        owner = self.as_('owner').post(f'{base}/join').json()['session_id']
        contrib = self.as_('contrib').post(f'{base}/join').json()['session_id']
        offer = {'session_id': owner, 'to': contrib, 'kind': 'offer', 'sdp': 'v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\n'}
        for bad in ({**offer, 'candidate': 'x'}, {**offer, 'kind': 'ice'}, {**offer, 'sdp': 'a\x00'}, {**offer, 'kind': 'hangup'},
                    {'session_id': owner, 'to': contrib, 'kind': 'ice', 'candidate': 'a\nb'}):
            self.assertEqual(self.as_('owner').post(f'{base}/signal', json=bad).status_code, 422, bad)
        self.assertEqual(self.code(self.as_('contrib').post(f'{base}/signal', json=offer)), 'VOICE_SESSION_ENDED')
        self.assertEqual(self.code(self.as_('owner').post(f'{base}/signal', json={**offer, 'to': 'z' * 22})), 'NOT_FOUND')

        async def connected():
            return False

        def parse(chunk):
            fields = dict(line.split(': ', 1) for line in chunk.strip().splitlines() if not line.startswith(':'))
            return fields.get('event'), json.loads(fields['data']) if 'data' in fields else None

        async def scenario():
            streams = {name: chat.stream(1, self.users[name], connected, ping=0.05) for name in ('contrib', 'outsider')}
            for stream in streams.values():
                await anext(stream)
                await anext(stream)

            async def next_event(name, kinds):
                while True:
                    kind, data = parse(await anext(streams[name]))
                    if kind in kinds:
                        return kind, data

            sent = await asyncio.to_thread(self.as_('owner').post, f'{base}/signal', json=offer)
            self.assertEqual(sent.status_code, 200, sent.text)
            ice = {'session_id': owner, 'to': contrib, 'kind': 'ice', 'candidate': 'candidate:1 1 udp 1 127.0.0.1 5000 typ host',
                   'sdp_mid': '0', 'sdp_mline_index': 0}
            await asyncio.to_thread(self.as_('owner').post, f'{base}/signal', json=ice)
            kind, data = await next_event('contrib', {'voice-signal'})
            self.assertEqual(data, {'from': owner, 'to': contrib, 'kind': 'offer', 'sdp': offer['sdp']})
            kind, data = await next_event('contrib', {'voice-signal'})
            self.assertEqual((data['kind'], data['candidate'], data['sdp_mline_index']), ('ice', ice['candidate'], 0))
            # The bystander sees nothing addressed to someone else; its next event is the seat change below.
            await asyncio.to_thread(self.as_('owner').patch, f"/api/community/v1/repositories/1/members/{self.users['contrib']}",
                                    json={'status': 'removed'})
            kind, data = await next_event('outsider', {'voice-signal', 'voice'})
            self.assertEqual(kind, 'voice')
            self.assertEqual([p['user']['github_login'] for p in data['participants']], ['Owner'])
            for stream in streams.values():
                await stream.aclose()

        asyncio.run(scenario())
        self.assertEqual(self.code(self.as_('contrib').post(f'{base}/join')), 'MEMBER_REMOVED')

    def test_voice_ice_servers_come_from_the_environment_with_short_lived_turn_credentials(self):
        self.assertEqual(voice.ice_servers(self.users['owner']), [])
        env = {'VOICE_STUN_URLS': 'stun:stun.example:3478, http://bad, stun:a b',
               'VOICE_TURN_URLS': 'turn:turn.example:3478?transport=udp,turns:turn.example:5349', 'VOICE_TURN_SECRET': 's3cret'}
        with patch.dict(os.environ, env), patch.object(voice.time, 'time', return_value=1000):
            stun, turn = voice.ice_servers('u1')
        self.assertEqual(stun, {'urls': 'stun:stun.example:3478'})
        self.assertEqual(turn['urls'], ['turn:turn.example:3478?transport=udp', 'turns:turn.example:5349'])
        self.assertEqual(turn['username'], f'{1000 + 12 * 3600}:u1')
        import base64
        import hashlib
        import hmac
        self.assertEqual(turn['credential'], base64.b64encode(hmac.new(b's3cret', turn['username'].encode(), hashlib.sha1).digest()).decode())
        self.assertNotIn('s3cret', str(turn))
        with patch.dict(os.environ, {'VOICE_TURN_URLS': 'turn:turn.example', 'VOICE_TURN_SECRET': ''}):
            self.assertEqual(voice.ice_servers('u1'), [])

    def test_changes_require_csrf_outside_the_desktop_session(self):
        del self.app.dependency_overrides[require_csrf]
        client = self.as_('contrib')
        self.assertEqual(client.get('/api/community/v1/repositories/1').status_code, 200)
        from app.services.ai.access import AIError
        with self.assertRaises(AIError) as rejected:
            client.post('/api/community/v1/repositories/1/posts', json={'title': 't', 'body': 'b'})
        self.assertEqual(rejected.exception.code.value, 'CSRF_FAILED')


if __name__ == '__main__':
    unittest.main()
