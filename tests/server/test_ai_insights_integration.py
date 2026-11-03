"""AI 인사이트 통합 시험: 실제 Git 저장소 이력 → 실제 수집 task·스냅샷 → API → worker task → 가짜 Anthropic HTTP 서버.

- 이 저장소(GitViz)의 main 브랜치를 임시 폴더로 복제하고 analyze_repository task를 그대로 실행한다.
  GitHub API(PR·릴리스·신원 통합)와 GitHub 접근 확인만 대역으로 바꾼다. 외부 네트워크를 쓰지 않는다.
- AI 공급자는 tests/server/ai_fake_provider.py(로컬 HTTP)이고, 사용자 key 등록·복호화·Anthropic 어댑터는 실제 경로다.
- GITVIZ_TEST_DATABASE_URL(이름이 gitviz_test로 시작하는 격리 DB)이 필요하다.
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

# 이 시험은 실제 저장소 이력 전체를 넣으므로 공유 시험 DB를 오염시키지 않도록 임시 DB(이름은 gitviz_test로 시작)에서만
# 실행하고 끝나면 지운다. 스냅샷 계산이 원본 테이블을 public.*로 읽으므로 schema 분리로는 격리할 수 없다.
BASE_URL = os.environ.get('GITVIZ_TEST_DATABASE_URL') or ''
ADMIN = None
if BASE_URL:
    from sqlalchemy.engine import make_url
    _base = make_url(BASE_URL)
    if not (_base.database or '').startswith(support.TEST_DB_PREFIX):
        raise RuntimeError(f'DB 이름은 {support.TEST_DB_PREFIX}로 시작해야 합니다.')
    TEMP_DB = f'{_base.database}_insight_{uuid4().hex[:10]}'
    ADMIN = create_engine(BASE_URL, isolation_level='AUTOCOMMIT')
    with ADMIN.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{TEMP_DB}"'))
    os.environ['GITVIZ_TEST_DATABASE_URL'] = _base.set(database=TEMP_DB).render_as_string(hide_password=False)
support.require_test_database()
ROOT = support.ROOT


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


PROVIDER_PORT = _free_port()
ORIGIN = 'http://localhost:8000'
os.environ.update({
    'AI_ENABLED': '1', 'AI_ALLOW_EXTERNAL_TRANSFER': '1', 'AI_ALLOWED_ORIGINS': ORIGIN,
    'AI_CREDENTIAL_ENCRYPTION_KEYS': 'c1:' + 'b2' * 32, 'AI_CREDENTIAL_KEY_VERSION': 'c1',
    'AI_INPUT_ENCRYPTION_KEYS': 'v1:' + 'a1' * 32, 'AI_INPUT_KEY_VERSION': 'v1',
    'AI_TEST_PROVIDER_BASE_URL': f'http://127.0.0.1:{PROVIDER_PORT}', 'AI_DAILY_JOB_LIMIT': '20',
})

import ai_credential_support as C  # noqa: E402
import requests  # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from app.api import auth as auth_router  # noqa: E402
from app.api.ai import insights as insights_api  # noqa: E402
from app.api.ai.dependencies import ai_error_response, ai_validation_error_response  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.services.ai.access import AIError  # noqa: E402
from app.tasks import ai_insights as insight_tasks  # noqa: E402
from app.tasks import analyze_repository as analyze_task  # noqa: E402

CATEGORIES = {'overview', 'maintenance_risk', 'collaboration', 'change_impact', 'anomaly', 'evolution'}


class _NoNetworkClient:
    """GitHub 클라이언트 대역: 생성은 되지만 어떤 요청도 보내지 않는다."""
    rate_limit_hits = 0
    total_wait_seconds = 0

    def __init__(self, *a, **kw):
        pass


def build_app():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key='insight-test', same_site='lax')
    app.add_exception_handler(AIError, ai_error_response)
    app.add_exception_handler(RequestValidationError, ai_validation_error_response)
    app.include_router(auth_router.router)
    app.include_router(insights_api.router)

    @app.post('/test/login/{user_id}')
    async def login(user_id: str, request: Request):
        request.session['user_id'] = user_id
        return {}
    return app


class InsightIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from alembic import command
        from alembic.config import Config
        command.upgrade(Config(str(ROOT / 'alembic.ini')), 'head')
        cls.tmp = tempfile.mkdtemp(prefix='gitviz-insight-')
        cls.repo_path = os.path.join(cls.tmp, 'repo')
        cls.make_clone()
        cls.log = open(os.path.join(cls.tmp, 'provider.log'), 'wb')
        cls.provider = subprocess.Popen([sys.executable, str(ROOT / 'tests/server/ai_fake_provider.py'),
                                         str(PROVIDER_PORT)], stdout=cls.log, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                requests.get(f'http://127.0.0.1:{PROVIDER_PORT}/__stats', timeout=1)
                break
            except requests.RequestException:
                time.sleep(0.1)
        suffix = uuid4().int % 10**9
        cls.owner, cls.other = uuid4(), uuid4()
        cls.private = {}                                  # repository_id → 비공개 여부(접근 확인 대역)
        cls.denied = set()                                # (user_id, repository_id) 접근 불가
        cls._access = patch('app.services.ai.access.verify_repository_access', side_effect=cls.verify)
        cls._access.start()
        # 코드 열람은 수집 worker가 남긴 clone을 읽는다. 이 시험에서는 수집에 쓴 임시 clone이 그 자리다.
        cls._clone = patch('app.services.ai.insight_service.code_repository_path', side_effect=lambda rid: cls.repo_path)
        cls._clone.start()
        with SessionLocal() as db:
            for n, (uid, login) in enumerate(((cls.owner, 'insight-owner'), (cls.other, 'insight-other'))):
                db.execute(text('INSERT INTO users(id, github_id, github_login) VALUES (:i, :g, :l)'),
                           {'i': uid, 'g': suffix + n, 'l': f'{login}-{suffix}'})
            cls.repo_id = db.execute(text("""INSERT INTO repositories(url, name, github_id, is_private)
                VALUES (:u, 'gitviz', :g, false) RETURNING id"""),
                {'u': f'https://github.com/insight-test/gitviz-{suffix}', 'g': suffix}).scalar_one()
            cls.private_repo = db.execute(text("""INSERT INTO repositories(url, name, github_id, is_private)
                VALUES (:u, 'secret', :g, true) RETURNING id"""),
                {'u': f'https://github.com/insight-test/secret-{suffix}', 'g': suffix + 1}).scalar_one()
            job_id = db.execute(text("""INSERT INTO analysis_jobs(repository_id, status, triggered_by_user_id, parameters)
                VALUES (:r, 'pending', :u, '{}') RETURNING id"""), {'r': cls.repo_id, 'u': cls.owner}).scalar_one()
            db.commit()
            cls.key_version = C.ensure_credential(db, cls.owner)
            C.ensure_credential(db, cls.other)
        cls.private.update({cls.repo_id: False, cls.private_repo: True})

        def analysis_access(db, repository_id, user_id=None, **kw):
            repo = db.query(analyze_task.Repository).filter_by(id=repository_id).one()
            return repo, {'id': repo.github_id, 'canonical_url': repo.url, 'private': False, 'default_branch': 'main'}
        real_identity = analyze_task.run_identity_resolution

        def local_identity(db, repository_id, **kw):    # 이메일 연결·noreply 등 로컬 단계만(GitHub API 단계 제외)
            return real_identity(db, repository_id, client=None, stage2_limit=0)
        with patch.object(analyze_task, 'verify_repository_access', side_effect=analysis_access), \
                patch.object(analyze_task, 'clone_repository', return_value=cls.repo_path), \
                patch.object(analyze_task, 'GitHubClient', _NoNetworkClient), \
                patch.object(analyze_task, 'GraphQLClient', _NoNetworkClient), \
                patch.object(analyze_task, 'collect_pull_requests', return_value=[]), \
                patch.object(analyze_task, 'collect_releases', return_value=[]), \
                patch.object(analyze_task, 'run_identity_resolution', side_effect=local_identity):
            analyze_task.analyze_repository(cls.repo_id, str(cls.owner), job_id)
        with SessionLocal() as db:
            job = db.execute(text('SELECT status, error_message, parameters FROM analysis_jobs WHERE id = :j'),
                             {'j': job_id}).one()
            assert job.status == 'done', job.error_message
            cls.snapshot_id = job.parameters['snapshot_id']
            cls.payload = db.execute(text('SELECT payload FROM analysis_snapshots WHERE id = :s'),
                                     {'s': cls.snapshot_id}).scalar_one()
            cls.source_counts = cls.counts(db)

    @classmethod
    def make_clone(cls):
        """원본 작업 폴더의 main(없으면 origin/main) 이력으로 GitHub clone과 같은 모양의 저장소를 만든다.

        origin/HEAD가 기본 브랜치 main을 가리키게 해서, 원본 작업 폴더의 현재 브랜치와 무관하게 수집된다.
        CI는 checkout에 fetch-depth: 0이 필요하다(얕은 clone이면 이력이 1개뿐이다).
        """
        def git(*args, cwd=None):
            return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()
        for ref in ('refs/heads/main', 'refs/remotes/origin/main'):
            found = subprocess.run(['git', 'rev-parse', '--verify', '--quiet', ref + '^{commit}'], cwd=ROOT,
                                   capture_output=True, text=True)
            if found.returncode == 0:
                sha = found.stdout.strip()
                break
        else:
            raise RuntimeError('main 브랜치 이력이 없습니다(CI checkout에는 fetch-depth: 0이 필요합니다).')
        git('init', '--quiet', cls.repo_path)
        git('fetch', '--quiet', '--no-tags', str(ROOT), f'{sha}:refs/remotes/origin/main', cwd=cls.repo_path)
        git('checkout', '--quiet', '-b', 'main', 'refs/remotes/origin/main', cwd=cls.repo_path)
        git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main', cwd=cls.repo_path)

    @classmethod
    def tearDownClass(cls):
        cls._access.stop()
        cls._clone.stop()
        cls.provider.terminate()
        cls.provider.wait(10)
        cls.log.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        from app.core.database import engine
        engine.dispose()
        with ADMIN.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{TEMP_DB}" WITH (FORCE)'))
        ADMIN.dispose()

    @classmethod
    def verify(cls, db, repository_id, user_id=None, **kw):
        if repository_id not in cls.private or (str(user_id), repository_id) in cls.denied:
            raise HTTPException(404, '저장소를 찾을 수 없거나 접근 권한이 없습니다.')
        repo = db.execute(text('SELECT id FROM repositories WHERE id = :r'), {'r': repository_id}).one()
        return repo, {'private': cls.private[repository_id]}

    @classmethod
    def counts(cls, db):
        return {t: db.execute(text(f'SELECT count(*) FROM {t} WHERE repository_id = :r'), {'r': cls.repo_id}).scalar()
                for t in ('commits', 'files', 'contributors', 'analysis_snapshots')} | {
            'payload_sha256': db.execute(text('SELECT payload_sha256 FROM analysis_snapshots WHERE id = :s'),
                                         {'s': cls.snapshot_id}).scalar()}

    # ── helpers ──
    def setUp(self):
        requests.post(f'http://127.0.0.1:{PROVIDER_PORT}/__mode', json={'mode': 'ok'}, timeout=5)
        self.client = TestClient(build_app())
        self.queued = []
        self._delay = [patch.object(getattr(insight_tasks, name), 'delay',
                                    side_effect=lambda rid, name=name: self.queued.append((name, rid)))
                       for name in ('run_insight_report', 'run_insight_answer')]
        for p in self._delay:
            p.start()
        self.login(self.owner)

    def tearDown(self):
        for p in self._delay:
            p.stop()
        self.client.close()

    def login(self, user):
        self.client.post(f'/test/login/{user}')
        self.token = self.client.get('/api/auth/csrf').json()['token']

    def post(self, path, body):
        return self.client.post(path, json=body, headers={'Origin': ORIGIN, 'X-CSRF-Token': self.token})

    def run_queued(self):
        """worker 대신 접수된 Celery task 본문을 그대로 실행한다(SessionLocal로 실제 DB 사용)."""
        results = [getattr(insight_tasks, name)(rid) for name, rid in self.queued]
        self.queued.clear()
        return results

    def error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()['error']['code'], code)

    def stats(self):
        return requests.get(f'http://127.0.0.1:{PROVIDER_PORT}/__stats', timeout=5).json()

    def db_rows(self, sql, **params):
        with SessionLocal() as db:
            return db.execute(text(sql), params).mappings().all()

    def assert_references_exist(self, item):
        ids = {f['path']: f['id'] for f in self.payload['file_index']}
        for path, file_id in item['links']['files'].items():          # 링크 id는 스냅샷의 실제 파일 id다
            self.assertEqual(ids[path], file_id)
        known = {c['id'] for c in self.payload['contributor_index']}
        self.assertTrue(set(item['links']['contributors'].values()) <= known)
        paths = {f['path'] for f in self.payload['file_index']}
        dirs = {p.rsplit('/', i)[0] for p in paths for i in range(1, p.count('/') + 1)}
        for path in item['relatedFiles']:
            self.assertIn(path, paths | dirs)
        for sha in item['relatedCommits']:
            self.assertTrue(self.db_rows('SELECT 1 FROM commits WHERE repository_id = :r AND sha = :s',
                                         r=self.repo_id, s=sha), sha)
        names = {c['github_login'] or c['display_name'] for c in self.payload['contributor_index']} | \
                {c['display_name'] for c in self.payload['contributor_index']}
        for name in item['relatedContributors']:
            self.assertIn(name, names)

    # ── 시험 ──
    def test_real_history_snapshot_is_the_input(self):
        self.assertGreater(self.payload['summary']['total_commits'], 50)
        self.assertGreater(len(self.payload['risk-scores']['files']), 20)

    def test_automatic_report_on_real_history(self):
        before = self.stats()['messages']
        response = self.post('/api/ai/v1/insights/reports', {'repository_id': self.repo_id, 'profile_id': C.OPUS})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        self.assertEqual(response.json()['status'], 'queued')
        self.assertEqual(self.run_queued(), ['succeeded'])
        report = self.client.get(f"/api/ai/v1/insights/reports/{response.json()['id']}").json()
        self.assertEqual(report['status'], 'succeeded', report)
        result = report['result']
        self.assertEqual({c['key'] for c in result['categories']}, CATEGORIES)
        self.assertEqual(result['basis']['snapshotId'], self.snapshot_id)
        self.assertEqual(result['basis']['targetSha'], self.payload['summary']['repository']['target_sha'])
        self.assertTrue(result['basis']['windowStart'] and result['basis']['windowEnd'])
        insights = [i for c in result['categories'] for i in c['insights']]
        self.assertGreaterEqual(len(insights), 5)
        for insight in insights:
            self.assertIn(insight['severity'], ('info', 'low', 'medium', 'high', 'critical'))
            self.assertTrue(insight['evidence'])
            calls = {c['callId']: c for c in next(c for c in result['categories']
                                                   if c['key'] == insight['category'])['toolCalls']}
            for evidence in insight['evidence']:
                self.assertIn(evidence['toolCallId'], calls)
                self.assertTrue(calls[evidence['toolCallId']]['ok'])
            self.assert_references_exist(insight)
        self.assertLessEqual(len(result['highlights']), 5)
        self.assertEqual(self.stats()['messages'] - before, report['callCount'])
        self.assertIn(C.TEST_KEY, self.stats()['keys'])           # 사용자 key로만 호출한다
        progress = [e['stage'] for e in report['progress']]
        self.assertIn('tool', progress)
        self.assertEqual([r['id'] for r in self.client.get(
            f'/api/ai/v1/insights/reports?repository_id={self.repo_id}').json()['reports']][0], report['id'])

    def test_conversation_answers_from_real_data_and_keeps_history(self):
        created = self.post('/api/ai/v1/insights/conversations', {'repository_id': self.repo_id, 'profile_id': C.OPUS})
        self.assertEqual(created.status_code, 201, created.text)
        cid = created.json()['id']
        ask = self.post(f'/api/ai/v1/insights/conversations/{cid}/messages',
                        {'question': '최근 6개월 동안 가장 많이 기여한 개발자는 누구야?'})
        self.assertEqual(ask.status_code, 202, ask.text)
        self.assertEqual(self.run_queued(), ['succeeded'])
        conversation = self.client.get(f'/api/ai/v1/insights/conversations/{cid}').json()
        answer = conversation['messages'][-1]
        self.assertEqual((answer['role'], answer['status']), ('assistant', 'succeeded'), answer)
        content = answer['content']
        self.assertEqual([c['tool'] for c in content['toolCalls']], ['getContributorStatistics'])
        top = self.db_rows("""SELECT count(*) AS n FROM commits WHERE repository_id = :r AND committed_at >= :s
                              AND committed_at < :e AND on_default_branch IS NOT FALSE
                              GROUP BY contributor_id ORDER BY n DESC LIMIT 1""", r=self.repo_id,
                           s=content['analysisPeriod']['start'], e=content['analysisPeriod']['end'])[0]['n']
        self.assertIn(str(top), [e['value'] for e in content['evidence']])     # 근거 수치 = DB 실제 값
        self.assert_references_exist(content)

        top_path = self.payload['risk-scores']['files'][0]['path']
        self.post(f'/api/ai/v1/insights/conversations/{cid}/messages',
                  {'question': f'{top_path} 와 같이 자주 바뀌는 파일은?'})
        self.assertEqual(self.run_queued(), ['succeeded'])
        conversation = self.client.get(f'/api/ai/v1/insights/conversations/{cid}').json()
        self.assertEqual([m['role'] for m in conversation['messages']], ['user', 'assistant'] * 2)
        second = conversation['messages'][-1]['content']
        self.assertEqual(second['toolCalls'][0]['tool'], 'getCoChangedFiles')
        self.assertEqual(second['toolCalls'][0]['args'], {'path': top_path})
        self.assert_references_exist(second)

        self.assertEqual(self.client.delete(f'/api/ai/v1/insights/conversations/{cid}',
                                            headers={'Origin': ORIGIN, 'X-CSRF-Token': self.token}).status_code, 200)
        listed = self.client.get(f'/api/ai/v1/insights/conversations?repository_id={self.repo_id}').json()
        self.assertNotIn(cid, [c['id'] for c in listed['conversations']])

    def test_fabricated_answer_is_not_stored(self):
        cid = self.post('/api/ai/v1/insights/conversations',
                        {'repository_id': self.repo_id, 'profile_id': C.OPUS}).json()['id']
        requests.post(f'http://127.0.0.1:{PROVIDER_PORT}/__mode', json={'mode': 'fabricate'}, timeout=5)
        self.post(f'/api/ai/v1/insights/conversations/{cid}/messages', {'question': '위험한 파일은 어디야?'})
        self.assertEqual(self.run_queued(), ['OUTPUT_UNVERIFIED'])
        answer = self.client.get(f'/api/ai/v1/insights/conversations/{cid}').json()['messages'][-1]
        self.assertEqual((answer['status'], answer['errorCode']), ('failed', 'OUTPUT_UNVERIFIED'))
        self.assertNotIn('ghost_module', str(answer))

    def test_access_isolation_and_transfer_policy(self):
        # 비공개 저장소는 클라우드 모델로 분석하지 않는다.
        self.error(self.post('/api/ai/v1/insights/reports', {'repository_id': self.private_repo, 'profile_id': C.OPUS}),
                   403, 'TRANSFER_NOT_ALLOWED')
        # CSRF 없이 변경 요청 불가.
        response = self.client.post('/api/ai/v1/insights/reports', json={'repository_id': self.repo_id, 'profile_id': C.OPUS})
        self.assertEqual(response.status_code, 403)
        # 다른 사용자는 남의 리포트·대화를 볼 수 없다(존재도 숨긴다).
        report = self.post('/api/ai/v1/insights/reports', {'repository_id': self.repo_id, 'profile_id': C.OPUS}).json()
        self.run_queued()
        cid = self.post('/api/ai/v1/insights/conversations', {'repository_id': self.repo_id, 'profile_id': C.OPUS}).json()['id']
        self.login(self.other)
        self.error(self.client.get(f"/api/ai/v1/insights/reports/{report['id']}"), 404, 'RESOURCE_NOT_FOUND')
        self.error(self.client.get(f'/api/ai/v1/insights/conversations/{cid}'), 404, 'RESOURCE_NOT_FOUND')
        self.assertNotIn(report['id'], [r['id'] for r in self.client.get(
            f'/api/ai/v1/insights/reports?repository_id={self.repo_id}').json()['reports']])
        # 접근 권한을 잃은 사용자는 자기 자료도 다시 볼 수 없고 새 질문도 못 한다.
        self.login(self.owner)
        self.denied.add((str(self.owner), self.repo_id))
        try:
            self.error(self.client.get(f"/api/ai/v1/insights/reports/{report['id']}"), 404, 'RESOURCE_NOT_FOUND')
            self.error(self.post(f'/api/ai/v1/insights/conversations/{cid}/messages', {'question': '요약해 줘'}),
                       404, 'RESOURCE_NOT_FOUND')
        finally:
            self.denied.discard((str(self.owner), self.repo_id))
        # 실행 도중 권한을 잃으면 결과를 남기지 않는다.
        self.post(f'/api/ai/v1/insights/conversations/{cid}/messages', {'question': '요약해 줘'})
        self.denied.add((str(self.owner), self.repo_id))
        try:
            self.assertEqual(self.run_queued(), ['RESOURCE_NOT_FOUND'])
        finally:
            self.denied.discard((str(self.owner), self.repo_id))

    def test_report_with_source_code_reads_the_snapshot_commit(self):
        response = self.post('/api/ai/v1/insights/reports',
                             {'repository_id': self.repo_id, 'profile_id': C.OPUS, 'include_code': True})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertTrue(response.json()['includeCode'])
        self.assertEqual(self.run_queued(), ['succeeded'])
        report = self.client.get(f"/api/ai/v1/insights/reports/{response.json()['id']}").json()
        code = report['result']['basis']['code']
        self.assertEqual((code['requested'], code['available']), (True, True))
        self.assertTrue(code['filesRead'])
        self.assertGreater(code['charsSent'], 0)
        tools = {c['tool'] for cat in report['result']['categories'] for c in cat['toolCalls']}
        self.assertTrue({'listDirectory', 'readFile'} <= tools, tools)
        cited = [ref for cat in report['result']['categories'] for i in cat['insights'] for ref in i['relatedCode']]
        self.assertTrue(cited)
        target = report['result']['basis']['targetSha']
        for ref in cited:                                      # 인용한 줄은 기준 커밋 파일에 실제로 있다
            body = subprocess.run(['git', 'show', f"{target}:{ref['path']}"], cwd=self.repo_path, capture_output=True,
                                  check=True).stdout.decode('utf-8').splitlines()
            self.assertLessEqual(ref['endLine'], len(body))

    def test_conversation_reads_code_only_when_enabled(self):
        question = 'app/main.py 코드의 역할은?'
        on = self.post('/api/ai/v1/insights/conversations',
                       {'repository_id': self.repo_id, 'profile_id': C.OPUS, 'include_code': True}).json()
        self.assertTrue(on['includeCode'])
        self.post(f"/api/ai/v1/insights/conversations/{on['id']}/messages", {'question': question})
        self.assertEqual(self.run_queued(), ['succeeded'])
        content = self.client.get(f"/api/ai/v1/insights/conversations/{on['id']}").json()['messages'][-1]['content']
        self.assertEqual([(c['tool'], c['ok']) for c in content['toolCalls']], [('readFile', True)])
        self.assertEqual(content['relatedCode'][0]['path'], 'app/main.py')
        self.assertEqual(content['basis']['code']['filesRead'], ['app/main.py'])

        off = self.post('/api/ai/v1/insights/conversations', {'repository_id': self.repo_id, 'profile_id': C.OPUS}).json()
        self.assertFalse(off['includeCode'])
        self.post(f"/api/ai/v1/insights/conversations/{off['id']}/messages", {'question': question})
        self.run_queued()
        content = self.client.get(f"/api/ai/v1/insights/conversations/{off['id']}").json()['messages'][-1]['content']
        self.assertEqual([(c['tool'], c['ok']) for c in content['toolCalls']], [('readFile', False)])   # 꺼져 있으면 읽지 않는다
        self.assertEqual(content['relatedCode'], [])
        self.assertEqual(content['basis']['code'], {'requested': False, 'available': False, 'charsSent': 0, 'filesRead': []})

    def test_zz_analysis_is_read_only(self):
        with SessionLocal() as db:
            self.assertEqual(self.counts(db), self.source_counts)
        self.assertEqual(subprocess.run(['git', 'status', '--porcelain'], cwd=self.repo_path, capture_output=True,
                                        text=True, check=True).stdout, '')


if __name__ == '__main__':
    unittest.main(verbosity=2)
