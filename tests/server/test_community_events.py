"""Community schedule over HTTP on a SQLite fixture (no PostgreSQL, no network).

Rules under test: members create events, the author or admin edits and deletes, participants answer for themselves,
participants-only events stay hidden from everyone else (the admin too), recurring events expand per range in the
offset they were entered with, and development links reuse collected pull requests and releases.
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
    CommunityComment,
    CommunityEvent,
    CommunityEventParticipant,
    CommunityMember,
    CommunityPost,
)
from app.models.contributor import Contributor  # noqa: E402
from app.models.pr_review import PrReview  # noqa: E402
from app.models.pull_request import PullRequest  # noqa: E402
from app.models.release import Release  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.user import User  # noqa: E402


@compiles(JSONB, 'sqlite')
def sqlite_json(type_, compiler, **kw):
    return 'JSON'


KST = timezone(timedelta(hours=9))
BASE = '/api/community/v1/repositories/1'


def at(day, hour=10, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=KST).isoformat()


class CommunityEventTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})

        @event.listens_for(self.engine, 'connect')
        def foreign_keys(connection, record):
            connection.execute('PRAGMA foreign_keys=ON')

        for model in (User, Repository, AnalysisJob, Contributor, CommunityMember, CommunityPost, CommunityComment,
                      CommunityEvent, CommunityEventParticipant, PullRequest, PrReview, Release):
            model.__table__.create(self.engine)
        self.factory = sessionmaker(self.engine)
        self.users = {name: uuid4() for name in ('owner', 'alice', 'bob', 'carol', 'outsider')}
        with self.factory.begin() as db:
            for i, (name, uid) in enumerate(self.users.items()):
                db.add(User(id=uid, github_id=100 + i, github_login=name.capitalize()))
            db.add(Repository(id=1, url='https://github.com/acme/tool', name='tool', github_id=1, is_private=False))
            db.add(Repository(id=2, url='https://github.com/acme/other', name='other', github_id=2, is_private=False))
            db.flush()
            db.add(AnalysisJob(repository_id=1, status='done', triggered_by_user_id=self.users['owner']))
            db.add(AnalysisJob(repository_id=2, status='done', triggered_by_user_id=self.users['alice']))
            for name in ('alice', 'bob', 'carol'):
                db.add(Contributor(repository_id=1, primary_email=f'{name}@example.test', github_login=name))
            db.add(PullRequest(id=7, repository_id=1, pr_number=42, title='Add cache', state='open', author_login='alice',
                               opened_at=datetime(2026, 11, 2, tzinfo=timezone.utc)))
            db.flush()
            db.add(PrReview(pull_request_id=7, reviewer_login='bob', state='approved'))
            db.add(Release(repository_id=1, tag_name='v1.2.0', name='1.2.0', published_at=datetime(2026, 10, 1, tzinfo=timezone.utc)))
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key='events-fixture')

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
        # Opening the community joins contributors and makes the first analyzer admin.
        for name in ('owner', 'alice', 'bob', 'carol'):
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

    def code(self, response):
        return response.json()['detail']['code']

    def create(self, who='alice', **fields):
        body = {'title': '스프린트 회의', 'event_type': 'meeting', 'starts_at': at(10), 'ends_at': at(10, 11)} | fields
        response = self.as_(who).post(f'{BASE}/events', json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def month(self, who, start=at(1, 0), end=at(30, 23)):
        response = self.as_(who).get(f'{BASE}/events', params={'start': start, 'end': end})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['events']

    def test_members_create_and_see_events_and_outsiders_are_refused(self):
        event_id = self.create(description='안건: 배포 일정', location='회의실 A', meeting_url='https://meet.example/abc',
                               reminder_minutes=30)
        items = self.month('bob')
        self.assertEqual([i['id'] for i in items], [event_id])
        self.assertEqual((items[0]['title'], items[0]['event_type'], items[0]['location']), ('스프린트 회의', 'meeting', '회의실 A'))
        self.assertEqual(items[0]['starts_at'], '2026-11-10T01:00:00+00:00')
        detail = self.as_('bob').get(f'{BASE}/events/{event_id}').json()
        self.assertEqual((detail['author']['github_login'], detail['meeting_url'], detail['can_edit']),
                         ('Alice', 'https://meet.example/abc', False))
        self.assertEqual(self.code(self.as_('outsider').get(f'{BASE}/events', params={'start': at(1), 'end': at(30)})),
                         'INVITE_REQUIRED')
        self.assertEqual(TestClient(self.app).get(f'{BASE}/events', params={'start': at(1), 'end': at(30)}).status_code, 401)

    def test_input_is_validated(self):
        post = lambda body: self.as_('alice').post(f'{BASE}/events', json=body)  # noqa: E731
        base = {'title': 't', 'event_type': 'meeting', 'starts_at': at(10), 'ends_at': at(10, 11)}
        self.assertEqual(post(base | {'ends_at': at(9)}).status_code, 422)
        self.assertEqual(post(base | {'starts_at': '2026-11-10T10:00:00'}).status_code, 422)  # no offset
        self.assertEqual(post(base | {'event_type': 'party'}).status_code, 422)
        self.assertEqual(post(base | {'meeting_url': 'javascript:alert(1)'}).status_code, 422)
        self.assertEqual(post(base | {'title': '   '}).status_code, 422)
        self.assertEqual(post(base | {'recurrence_until': at(20)}).status_code, 422)
        self.assertEqual(self.code(post(base | {'participant_ids': [str(self.users['outsider'])]})), 'INVALID_INPUT')
        self.assertEqual(self.as_('alice').get(f'{BASE}/events', params={'start': at(1), 'end': '2027-03-01T00:00:00+09:00'}).status_code, 422)

    def test_only_author_or_admin_edits_and_deletes(self):
        event_id = self.create()
        body = {'title': '회의(변경)', 'event_type': 'team_meeting', 'starts_at': at(11), 'ends_at': at(11, 12)}
        self.assertEqual(self.code(self.as_('bob').patch(f'{BASE}/events/{event_id}', json=body)), 'PERMISSION_DENIED')
        self.assertEqual(self.as_('alice').patch(f'{BASE}/events/{event_id}', json=body).status_code, 200)
        detail = self.as_('owner').get(f'{BASE}/events/{event_id}').json()
        self.assertEqual((detail['title'], detail['event_type'], detail['can_edit']), ('회의(변경)', 'team_meeting', True))
        self.assertIsNotNone(detail['updated_at'])
        self.assertEqual(self.code(self.as_('bob').delete(f'{BASE}/events/{event_id}')), 'PERMISSION_DENIED')
        self.assertEqual(self.as_('owner').delete(f'{BASE}/events/{event_id}').status_code, 200)
        self.assertEqual(self.month('alice'), [])
        self.assertEqual(self.as_('alice').get(f'{BASE}/events/{event_id}').status_code, 404)

    def test_participants_answer_for_themselves_and_answers_survive_edits(self):
        bob, carol = str(self.users['bob']), str(self.users['carol'])
        event_id = self.create(participant_ids=[bob, carol], assignee_id=bob)
        self.assertEqual(self.as_('bob').put(f'{BASE}/events/{event_id}/response', json={'response': 'accepted'}).status_code, 200)
        self.assertEqual(self.code(self.as_('owner').put(f'{BASE}/events/{event_id}/response', json={'response': 'accepted'})),
                         'PERMISSION_DENIED')
        self.assertEqual(self.as_('bob').put(f'{BASE}/events/{event_id}/response', json={'response': 'maybe'}).status_code, 422)
        # Dropping carol keeps bob's answer.
        body = {'title': '회의', 'event_type': 'meeting', 'starts_at': at(10), 'ends_at': at(10, 11), 'participant_ids': [bob]}
        self.assertEqual(self.as_('alice').patch(f'{BASE}/events/{event_id}', json=body).status_code, 200)
        detail = self.as_('bob').get(f'{BASE}/events/{event_id}').json()
        self.assertEqual([(r['user']['github_login'], r['response']) for r in detail['responses']], [('Bob', 'accepted')])
        self.assertIsNone(detail['assignee'])
        self.assertEqual((detail['my_response'], detail['can_respond']), ('accepted', True))

    def test_participants_only_events_are_hidden_from_everyone_else_including_the_admin(self):
        event_id = self.create(visibility='participants', participant_ids=[str(self.users['bob'])])
        self.assertEqual([i['id'] for i in self.month('bob')], [event_id])
        self.assertEqual([i['id'] for i in self.month('alice')], [event_id])
        for name in ('carol', 'owner'):
            self.assertEqual(self.month(name), [])
            self.assertEqual(self.code(self.as_(name).get(f'{BASE}/events/{event_id}')), 'NOT_FOUND')
            self.assertEqual(self.as_(name).delete(f'{BASE}/events/{event_id}').status_code, 404)
        search = self.as_('carol').get(f'{BASE}/events', params={'q': '스프린트'}).json()
        self.assertEqual(search['events'], [])

    def test_recurring_events_expand_in_the_entered_offset(self):
        weekly = self.create(title='주간 회의', starts_at=at(2, 9), ends_at=at(2, 10), recurrence='weekly',
                             recurrence_until=at(23, 9))
        items = [i for i in self.month('bob') if i['id'] == weekly]
        self.assertEqual([i['starts_at'] for i in items],
                         ['2026-11-02T00:00:00+00:00', '2026-11-09T00:00:00+00:00', '2026-11-16T00:00:00+00:00',
                          '2026-11-23T00:00:00+00:00'])
        # A KST midnight all-day event on the 31st recurs monthly only in months that have a 31st.
        monthly = self.create(title='월말 점검', all_day=True, recurrence='monthly',
                              starts_at=datetime(2026, 10, 31, tzinfo=KST).isoformat(),
                              ends_at=datetime(2026, 11, 1, tzinfo=KST).isoformat())
        dates = []
        for month in range(11, 13):
            start = datetime(2026, month, 1, tzinfo=KST)
            items = self.as_('bob').get(f'{BASE}/events', params={'start': start.isoformat(),
                                                                   'end': (start + timedelta(days=31)).isoformat()}).json()['events']
            dates += [datetime.fromisoformat(i['starts_at']).astimezone(KST).date().isoformat() for i in items if i['id'] == monthly]
        self.assertEqual(sorted(set(dates)), ['2026-12-31'])
        daily = self.create(title='스탠드업', starts_at=at(1, 9, 30), ends_at=at(1, 9, 45), recurrence='daily')
        week = self.as_('bob').get(f'{BASE}/events', params={'start': at(20, 0), 'end': at(27, 0)}).json()['events']
        self.assertEqual(len([i for i in week if i['id'] == daily]), 7)

    def test_development_links_reuse_collected_pull_requests_releases_sprints_and_posts(self):
        sprint = self.create(title='Sprint 12', event_type='sprint', starts_at=at(2, 0), ends_at=at(15, 23), goal='캐시 도입')
        milestone = self.create(title='1.3 출시', event_type='milestone', starts_at=at(28, 0), ends_at=at(28, 0))
        post = self.as_('alice').post(f'{BASE}/posts', json={'title': '리뷰 의견', 'body': '본문'}).json()['id']
        review = self.create(title='캐시 PR 리뷰', event_type='code_review', sprint_id=sprint, milestone_id=milestone, post_id=post,
                             pull_numbers=[42, 99], issue_numbers=[5], release_tag='v1.2.0',
                             attachments=[{'name': '설계 문서', 'url': 'https://docs.example/design'}])
        detail = self.as_('bob').get(f'{BASE}/events/{review}').json()
        self.assertEqual(detail['pulls'][0] | {'opened_at': None},
                         {'number': 42, 'url': 'https://github.com/acme/tool/pull/42', 'found': True, 'title': 'Add cache',
                          'state': 'open', 'author_login': 'alice', 'opened_at': None, 'merged_at': None, 'reviewers': ['bob']})
        self.assertEqual(detail['pulls'][1], {'number': 99, 'url': 'https://github.com/acme/tool/pull/99', 'found': False})
        self.assertEqual(detail['issues'], [{'number': 5, 'url': 'https://github.com/acme/tool/issues/5'}])
        self.assertEqual((detail['release']['found'], detail['release']['url']), (True, 'https://github.com/acme/tool/releases/tag/v1.2.0'))
        self.assertEqual((detail['sprint']['title'], detail['sprint']['goal']), ('Sprint 12', '캐시 도입'))
        self.assertEqual(detail['milestone']['title'], '1.3 출시')
        self.assertEqual(detail['post'], {'id': post, 'title': '리뷰 의견'})
        self.assertEqual(detail['attachments'], [{'name': '설계 문서', 'url': 'https://docs.example/design'}])
        links = self.as_('bob').get(f'{BASE}/event-links').json()
        self.assertEqual([p['number'] for p in links['pulls']], [42])
        self.assertEqual([r['tag'] for r in links['releases']], ['v1.2.0'])
        self.assertEqual(([s['id'] for s in links['sprints']], [m['id'] for m in links['milestones']]), ([sprint], [milestone]))
        self.assertEqual({m['github_login'] for m in links['members']}, {'Owner', 'Alice', 'Bob', 'Carol'})
        # Links must stay inside the community and point at the right kind of event.
        bad = {'title': 'x', 'event_type': 'task', 'starts_at': at(3), 'ends_at': at(3, 11)}
        for extra in ({'sprint_id': milestone}, {'milestone_id': sprint}, {'post_id': 999}):
            self.assertEqual(self.code(self.as_('alice').post(f'{BASE}/events', json=bad | extra)), 'INVALID_INPUT')

    def test_search_and_the_cross_community_calendar(self):
        self.create(title='배포 리허설', event_type='deploy', version='1.3.0', environment='staging')
        self.create(title='기술 공유', event_type='tech_share', starts_at=at(12), ends_at=at(12, 11))
        found = self.as_('bob').get(f'{BASE}/events', params={'q': '리허설'}).json()['events']
        self.assertEqual([i['title'] for i in found], ['배포 리허설'])
        self.assertEqual(self.as_('bob').get(f'{BASE}/events', params={'q': '%'}).json()['events'], [])
        # Alice is in both communities (contributor of 1, first analyzer of 2); bob only in 1.
        self.assertEqual(self.as_('alice').get('/api/community/v1/repositories/2').status_code, 200)
        other = self.as_('alice').post('/api/community/v1/repositories/2/events',
                                       json={'title': '다른 저장소 회의', 'event_type': 'meeting', 'starts_at': at(13), 'ends_at': at(13, 11)})
        self.assertEqual(other.status_code, 201, other.text)
        params = {'start': at(1, 0), 'end': at(30, 0)}
        mine = self.as_('alice').get('/api/community/v1/events', params=params).json()['events']
        self.assertEqual([(i['repository']['name'], i['title']) for i in mine],
                         [('tool', '배포 리허설'), ('tool', '기술 공유'), ('other', '다른 저장소 회의')])
        self.assertEqual(len(self.as_('bob').get('/api/community/v1/events', params=params).json()['events']), 2)

    def test_removed_members_lose_the_calendar(self):
        self.create()
        self.assertEqual(self.as_('owner').patch(f'{BASE}/members/{self.users["bob"]}', json={'status': 'removed'}).status_code, 200)
        self.assertEqual(self.code(self.as_('bob').get(f'{BASE}/events', params={'start': at(1), 'end': at(30)})), 'MEMBER_REMOVED')
        self.assertEqual(self.as_('bob').get('/api/community/v1/events', params={'start': at(1), 'end': at(30)}).json()['events'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
