"""Community notifications over HTTP on a SQLite fixture (no PostgreSQL, no network).

Rules under test: unread chat counts only other people's messages after the read position, a new member starts at the
newest message, the read position only moves forward, muting silences chat but not invitations, an invitation appears
once for each participant other than the author until the app marks it seen, and removed members get nothing.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['DATABASE_URL'] = 'postgresql://gitviz_test:unused@localhost/gitviz_test'
os.environ['DATABASE_URL_ASYNC'] = ''
os.environ.pop('REDIS_URL', None)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from app.api.ai.dependencies import require_csrf  # noqa: E402
from app.api.community import router  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.models.analysis_job import AnalysisJob  # noqa: E402
from app.models.community import (  # noqa: E402
    CommunityChatMessage,
    CommunityEvent,
    CommunityEventParticipant,
    CommunityMember,
    CommunityPost,
)
from app.models.contributor import Contributor  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.user import User  # noqa: E402


@compiles(JSONB, 'sqlite')
def sqlite_json(type_, compiler, **kw):
    return 'JSON'


KST = timezone(timedelta(hours=9))
BASE = '/api/community/v1/repositories/1'
NOTIFY = '/api/community/v1/notifications'


def at(day, hour=10):
    return datetime(2026, 11, day, hour, tzinfo=KST).isoformat()


class CommunityNotificationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})

        @event.listens_for(self.engine, 'connect')
        def foreign_keys(connection, record):
            connection.execute('PRAGMA foreign_keys=ON')

        for model in (User, Repository, AnalysisJob, Contributor, CommunityMember, CommunityPost, CommunityChatMessage,
                      CommunityEvent, CommunityEventParticipant):
            model.__table__.create(self.engine)
        self.factory = sessionmaker(self.engine)
        self.users = {name: uuid4() for name in ('owner', 'alice', 'bob')}
        with self.factory.begin() as db:
            for i, (name, uid) in enumerate(self.users.items()):
                db.add(User(id=uid, github_id=100 + i, github_login=name.capitalize()))
            db.add(Repository(id=1, url='https://github.com/acme/tool', name='tool', github_id=1, is_private=False))
            db.flush()
            db.add(AnalysisJob(repository_id=1, status='done', triggered_by_user_id=self.users['owner']))
            for name in ('alice', 'bob'):
                db.add(Contributor(repository_id=1, primary_email=f'{name}@example.test', github_login=name))
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key='notifications-fixture')

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
        for name in ('owner', 'alice', 'bob'):
            self.assertEqual(self.as_(name).get(BASE).status_code, 200)

    def tearDown(self):
        for client in self.clients.values():
            client.close()
        self.engine.dispose()

    def as_(self, name):
        if name not in self.clients:
            client = TestClient(self.app)
            client.get(f'/fixture/login/{name}')
            self.clients[name] = client
        return self.clients[name]

    def summary(self, who):
        response = self.as_(who).get(NOTIFY)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def say(self, who, text):
        response = self.as_(who).post(f'{BASE}/chat/messages', json={'body': text})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def test_unread_chat_counts_other_peoples_messages_after_the_read_position(self):
        self.assertEqual(self.summary('bob')['unread_total'], 0)  # Sets bob's starting point.
        self.say('alice', '안녕하세요')
        last = self.say('alice', '회의 언제 할까요?\n오늘 저녁?')
        self.say('bob', '좋아요')  # Own messages never count.
        room = self.summary('bob')['communities'][0]
        self.assertEqual((room['repository']['name'], room['unread_chat'], room['muted']), ('tool', 2, False))
        self.assertEqual(room['latest']['id'], last)
        self.assertEqual(room['latest']['preview'], '회의 언제 할까요? 오늘 저녁?')
        self.assertEqual(room['latest']['author']['github_login'], 'Alice')
        self.assertEqual(self.as_('bob').put(f'{BASE}/chat/read', json={'message_id': last}).json()['chat_read_id'], last)
        self.assertEqual(self.summary('bob')['communities'][0]['unread_chat'], 0)
        # The position never moves back and never past the newest message.
        self.assertEqual(self.as_('bob').put(f'{BASE}/chat/read', json={'message_id': 1}).json()['chat_read_id'], last)
        self.assertLessEqual(self.as_('bob').put(f'{BASE}/chat/read', json={'message_id': 10 ** 9}).json()['chat_read_id'], last + 1)

    def test_a_new_member_starts_at_the_newest_message(self):
        self.say('alice', '예전 대화')
        self.assertEqual(self.summary('owner')['communities'][0]['unread_chat'], 0)
        self.say('alice', '새 대화')
        self.assertEqual(self.summary('owner')['communities'][0]['unread_chat'], 1)

    def test_muting_silences_chat_but_not_invitations(self):
        self.summary('bob')
        self.say('alice', '알림 시험')
        self.assertEqual(self.as_('bob').put(f'{BASE}/notifications', json={'muted': True}).json(), {'muted': True})
        self.assertTrue(self.as_('bob').get(BASE).json()['me']['chat_muted'])
        self.as_('alice').post(f'{BASE}/events', json={'title': '중간 점검', 'event_type': 'meeting', 'starts_at': at(12),
                                                       'ends_at': at(12, 11), 'participant_ids': [str(self.users['bob'])]})
        state = self.summary('bob')
        self.assertEqual((state['communities'][0]['muted'], state['communities'][0]['unread_chat']), (True, 1))
        self.assertEqual(state['unread_total'], 1)  # Only the invitation counts.
        self.assertEqual([i['title'] for i in state['invites']], ['중간 점검'])

    def test_invitations_appear_once_for_participants_other_than_the_author(self):
        people = [str(self.users[name]) for name in ('alice', 'bob', 'owner')]
        created = self.as_('alice').post(f'{BASE}/events', json={'title': '스프린트 회의', 'event_type': 'meeting',
                                                                 'starts_at': at(13), 'ends_at': at(13, 11), 'participant_ids': people})
        event_id = created.json()['id']
        self.assertEqual(self.summary('alice')['invites'], [])  # The author added themselves.
        invite = self.summary('bob')['invites'][0]
        self.assertEqual((invite['event_id'], invite['title'], invite['author']['github_login'], invite['repository']['id']),
                         (event_id, '스프린트 회의', 'Alice', 1))
        self.assertEqual(invite['utc_offset'], 540)
        # Editing keeps the people already notified quiet.
        self.assertEqual(self.as_('bob').post(f'{NOTIFY}/invites/seen', json={'event_ids': [event_id]}).json(), {'marked': 1})
        self.as_('alice').patch(f'{BASE}/events/{event_id}', json={'title': '스프린트 회의(변경)', 'event_type': 'meeting',
                                                                   'starts_at': at(13), 'ends_at': at(13, 12), 'participant_ids': people})
        self.assertEqual(self.summary('bob')['invites'], [])
        self.assertEqual(len(self.summary('owner')['invites']), 1)
        # Someone else's seen request cannot clear owner's invitation; deleting the event withdraws it.
        self.as_('bob').post(f'{NOTIFY}/invites/seen', json={'event_ids': [event_id]})
        self.assertEqual(len(self.summary('owner')['invites']), 1)
        self.as_('alice').delete(f'{BASE}/events/{event_id}')
        self.assertEqual(self.summary('owner')['invites'], [])

    def test_input_is_validated_and_removed_members_get_nothing(self):
        self.assertEqual(self.as_('bob').post(f'{NOTIFY}/invites/seen', json={'event_ids': []}).status_code, 422)
        self.assertEqual(self.as_('bob').post(f'{NOTIFY}/invites/seen', json={'event_ids': [0]}).status_code, 422)
        self.assertEqual(self.as_('bob').put(f'{BASE}/chat/read', json={'message_id': 0}).status_code, 422)
        self.assertEqual(self.as_('bob').put(f'{BASE}/notifications', json={'muted': 'yes'}).status_code, 422)
        self.assertEqual(TestClient(self.app).get(NOTIFY).status_code, 401)
        self.assertEqual(self.as_('owner').patch(f'{BASE}/members/{self.users["bob"]}', json={'status': 'removed'}).status_code, 200)
        self.assertEqual(self.summary('bob'), {'communities': [], 'invites': [], 'unread_total': 0})
        self.assertEqual(self.as_('bob').put(f'{BASE}/chat/read', json={'message_id': 1}).status_code, 403)


if __name__ == '__main__':
    unittest.main()
