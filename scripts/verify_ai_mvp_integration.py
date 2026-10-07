"""AI MVP 통합 검증: 실제 PostgreSQL·Redis + 별도 프로세스(app 서버·dispatcher·Celery worker·가짜 제공자) + HTTP.

fake provider 검증이다. 실제 AI 제공자·GitHub는 쓰지 않고(`scripts/evaluate_ai_review.py --live`는 별도), 실제 모델의 정확도는
이 파일이 말해 주지 않는다. 일반 CI에서 돌릴 수 있다.

필요: GITVIZ_TEST_DATABASE_URL(gitviz_test…), GITVIZ_TEST_REDIS_URL(테스트 전용 Redis, 시작 시 비운다).
    python scripts/verify_ai_mvp_integration.py

검증하는 것: 정상 흐름(키 등록→PR·파일→입력 준비→동의→설명·리뷰→결과·피드백), 큐 발행 실패, worker 중단, 응답 유실,
취소 경쟁, 원문 삭제·기한 삭제, 키 삭제, 권한 경계, 비밀·코드가 로그·DB에 남지 않는지, 호출 횟수(자동 재호출 없음).
"""
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402

URL = support.require_test_database()
REDIS_URL = os.environ.get('GITVIZ_TEST_REDIS_URL')
if not REDIS_URL:
    raise RuntimeError('GITVIZ_TEST_REDIS_URL에 테스트 전용 Redis를 지정하세요(해당 DB를 비웁니다).')
sys.path.insert(0, str(support.ROOT))

import redis  # noqa: E402
import requests  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from itsdangerous import TimestampSigner  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from ai_git_fixture import Fixture  # noqa: E402

ROOT = support.ROOT
SECRET = 'integration-session-secret'
USER_KEY = 'sk-ant-api03-INTEGRATIONKEY0123456789abcdef'
CODE_MARKER = 'b = 20'
TABLES = ('usage_reservations, usage_budgets, audit_events, request_idempotency, task_outbox, ai_attempts, finding_feedback, '
          'ai_findings, ai_results, ai_input_files, ai_jobs, ai_inputs, pr_revisions, ai_credential_versions, ai_credentials')
engine = create_engine(URL, pool_size=5)


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def wait_for(condition, timeout=60.0, interval=0.25, message='조건이 충족되지 않았다'):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(message)


def sql(query, **params):
    with engine.begin() as db:
        result = db.execute(text(query), params)
        return [dict(r._mapping) for r in result] if result.returns_rows else result.rowcount


def scalar(query, **params):
    with engine.begin() as db:
        return db.execute(text(query), params).scalar()


class Api:
    """실제 HTTP로 app 서버를 호출하는 사용자. 세션 쿠키는 앱과 같은 방식으로 서명한다(GitHub OAuth만 생략)."""

    def __init__(self, base, user_id):
        self.base = base
        self.http = requests.Session()
        data = base64.b64encode(json.dumps({'user_id': str(user_id)}).encode())
        self.http.cookies.set('gitviz_session', TimestampSigner(SECRET).sign(data).decode(), domain='127.0.0.1')
        self.headers = {'Origin': base}
        self.csrf_token = None

    def csrf(self):
        self.csrf_token = self.http.get(f'{self.base}/api/auth/csrf', timeout=10).json()['token']

    def call(self, method, path, body=None, key=None):
        headers = dict(self.headers)
        if method != 'GET':
            if not self.csrf_token:
                self.csrf()
            headers['X-CSRF-Token'] = self.csrf_token
        if key:
            headers['Idempotency-Key'] = key
        return self.http.request(method, f'{self.base}{path}', json=body, headers=headers, timeout=30)

    def get(self, path):
        return self.call('GET', path)

    def post(self, path, body=None, key=None):
        return self.call('POST', path, body or {}, key or uuid4().hex)

    def json(self, response, status=None):
        if status is not None:
            assert response.status_code == status, f'{response.status_code}: {response.text[:300]}'
        return response.json()


class Stack(unittest.TestCase):
    """클래스당 한 번 프로세스를 띄운다. 테스트마다 데이터와 가짜 제공자 상태를 초기화한다."""

    @classmethod
    def setUpClass(cls):
        command.upgrade(Config(str(ROOT / 'alembic.ini')), 'head')
        cls.broker = redis.Redis.from_url(REDIS_URL)
        cls.broker.flushdb()
        cls.tmp = tempfile.mkdtemp(prefix='gitviz-ai-it-')
        cls.fx = Fixture(cls.tmp)
        cls.app_port, cls.provider_port = free_port(), free_port()
        cls.base = f'http://127.0.0.1:{cls.app_port}'
        cls.procs, cls.logs, cls.users, cls.repos = {}, {}, [], []
        cls.env = {
            **os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONPATH': str(ROOT), 'REDIS_URL': REDIS_URL, 'SESSION_SECRET_KEY': SECRET,
            'AI_ENABLED': '1', 'AI_ALLOW_EXTERNAL_TRANSFER': '1', 'AI_INPUT_ENCRYPTION_KEYS': 'v1:' + 'a1' * 32, 'AI_INPUT_KEY_VERSION': 'v1',
            'AI_CREDENTIAL_ENCRYPTION_KEYS': 'c1:' + 'b2' * 32, 'AI_CREDENTIAL_KEY_VERSION': 'c1', 'AI_ALLOWED_ORIGINS': cls.base,
            'AI_TEST_PROVIDER_BASE_URL': f'http://127.0.0.1:{cls.provider_port}',
            # 남아 있는 환경변수로 사용자 key가 다른 주소로 가지 않는지 함께 확인한다: 닫힌 포트를 가리키는 값을 심어 둔다.
            'ANTHROPIC_BASE_URL': 'http://127.0.0.1:9', 'ANTHROPIC_API_KEY': 'sk-ant-SERVER-KEY-MUST-NOT-BE-USED',
            'AI_LEASE_SECONDS': '4', 'AI_RETRY_DELAY_SECONDS': '1', 'AI_MAX_ATTEMPTS': '3', 'AI_QUEUE_DEADLINE_SECONDS': '600',
            'AI_DISPATCH_INTERVAL_SECONDS': '0.3', 'AI_MAINTENANCE_INTERVAL_SECONDS': '1', 'AI_USER_MONTHLY_BUDGET': '20',
            'AI_SERVER_MONTHLY_BUDGET': '0', 'AI_DAILY_JOB_LIMIT': '100',
            'AI_IT_FIXTURE': json.dumps({'path': str(cls.fx.path), 'base': cls.fx.m1, 'head': cls.fx.f1, 'merge_base': cls.fx.c0}),
            'AI_HANDLER_MODULES': 'scripts.ai_integration_patches', 'AI_IT_ROLE': 'worker',
        }
        for key in ('DATABASE_URL', 'DATABASE_URL_ASYNC'):
            cls.env[key] = os.environ[key]
        cls.spawn('provider', [sys.executable, 'scripts/ai_fake_provider.py', str(cls.provider_port)], {})
        cls.spawn('app', [sys.executable, 'scripts/ai_integration_app.py', str(cls.app_port)], {'AI_IT_ROLE': 'app'})
        wait_for(lambda: cls.alive(cls.base + '/openapi.json'), 40, message='app 서버가 뜨지 않았다\n' + cls.output('app'))
        wait_for(lambda: cls.alive(f'http://127.0.0.1:{cls.provider_port}/__stats'), 20, message='가짜 제공자가 뜨지 않았다')

    @classmethod
    def alive(cls, url):
        try:
            return requests.get(url, timeout=2).status_code < 500
        except requests.RequestException:
            return False

    @classmethod
    def spawn(cls, name, argv, env_extra):
        cls.stop(name)
        log = tempfile.TemporaryFile()
        cls.logs[name] = log
        cls.procs[name] = subprocess.Popen(argv, cwd=str(ROOT), env={**cls.env, **env_extra}, stdout=log, stderr=subprocess.STDOUT)

    @classmethod
    def stop(cls, name, sig='kill'):
        proc = cls.procs.pop(name, None)
        if proc is not None and proc.poll() is None:
            proc.kill() if sig == 'kill' else proc.terminate()
            proc.wait(timeout=20)

    @classmethod
    def output(cls, name):
        log = cls.logs.get(name)
        if log is None:
            return ''
        log.seek(0)
        return log.read().decode('utf-8', 'replace')

    @classmethod
    def start_worker(cls, name='worker'):
        cls.spawn(name, [sys.executable, '-m', 'celery', '-A', 'app.core.celery_app.celery', 'worker', '--pool=solo', '-Q',
                         'ai_prepare,ai_generate', '--loglevel=WARNING', '-n', f'it-{name}-{uuid4().hex[:6]}@%h',
                         '--without-gossip', '--without-mingle', '--without-heartbeat'], {})

    @classmethod
    def start_dispatcher(cls, redis_url=None):
        cls.spawn('dispatcher', [sys.executable, '-m', 'app.tasks.ai_dispatch'], {'REDIS_URL': redis_url} if redis_url else {})

    @classmethod
    def tearDownClass(cls):
        for name in list(cls.procs):
            cls.stop(name)
        for log in cls.logs.values():
            log.close()
        sql(f'TRUNCATE {TABLES} CASCADE')
        for uid in cls.users:
            sql('DELETE FROM users WHERE id = :i', i=uid)
        for rid in cls.repos:
            sql('DELETE FROM repositories WHERE id = :i', i=rid)
        cls.broker.flushdb()
        from app.collectors.git import ai_diff_collector
        ai_diff_collector._remove_tree(cls.tmp)

    # ── 테스트마다 ──
    def setUp(self):
        self.stop('worker'), self.stop('dispatcher')
        sql(f'TRUNCATE {TABLES} CASCADE')
        self.broker.flushdb()
        self.provider_mode('ok')
        requests.post(f'http://127.0.0.1:{self.provider_port}/__reset', timeout=5)
        self.start_worker()
        self.start_dispatcher()

    def provider_mode(self, mode, **extra):
        requests.post(f'http://127.0.0.1:{self.provider_port}/__mode', json={'mode': mode, **extra}, timeout=5)

    def provider_stats(self):
        return requests.get(f'http://127.0.0.1:{self.provider_port}/__stats', timeout=5).json()

    # ── 데이터·흐름 ──
    def make_user(self):
        uid = uuid4()
        sql("INSERT INTO users(id, github_id, github_login) VALUES(:i, :g, 'itest')", i=uid, g=uuid4().int % 10**12)
        self.users.append(uid)
        return uid

    def make_repo(self):
        rid = scalar("""INSERT INTO repositories(url, name, github_id, is_private) VALUES('https://github.com/acme/widgets', 'widgets', :g, false)
                        RETURNING id""", g=uuid4().int % 10**12)
        self.repos.append(rid)
        return rid

    def world(self, register_key=True):
        api, repo = Api(self.base, self.make_user()), self.make_repo()
        if register_key:
            body = api.json(api.call('PUT', '/api/ai/v1/credentials/anthropic', {'api_key': USER_KEY, 'profile_id': 'claude-opus-5-5-v1', 'verify': True}), 200)
            self.assertTrue(body['changed'])
        return api, repo

    def prepare(self, api, repo, paths=('main.py',)):
        body = {'repository_id': repo, 'pr_number': 7, 'expected_base_sha': self.fx.m1, 'expected_head_sha': self.fx.f1, 'selected_paths': list(paths)}
        created = api.json(api.post('/api/ai/v1/inputs', body), 202)
        input_id = created['input_id']
        preview = wait_for(lambda: (lambda p: p if p['status'] != 'building' else None)(api.get(f'/api/ai/v1/inputs/{input_id}').json()), 90,
                           message='입력 준비가 끝나지 않았다\n' + self.output('worker'))
        self.assertEqual(preview['status'], 'ready', preview)
        return preview

    @staticmethod
    def consent(preview):
        t = preview['transfer']
        return {'input_id': preview['input_id'], 'manifest_hash': preview['manifest_hash'], 'provider': t['provider'], 'profile_id': t['profile_id'],
                'credential_version': t['credential_version'], 'policy_version': preview['policy_version']}

    def submit(self, api, preview, kind='review', **extra):
        return api.post('/api/ai/v1/jobs', {'kind': kind, 'consent': self.consent(preview), 'force_regenerate': False, **extra})

    def job(self, api, job_id):
        return api.get(f'/api/ai/v1/jobs/{job_id}').json()

    def wait_job(self, api, job_id, statuses=('succeeded', 'failed', 'cancelled'), timeout=90):
        return wait_for(lambda: (lambda j: j if j['status'] in statuses else None)(self.job(api, job_id)), timeout,
                        message=f'작업이 {statuses}에 이르지 않았다: {self.job(api, job_id)}\n' + self.output('worker')[-1500:])

    def messages(self):
        return self.provider_stats()['messages']


class HappyPath(Stack):
    def test_key_to_result_to_feedback_through_real_processes(self):
        api, repo = self.world()
        stats = self.provider_stats()
        self.assertEqual((stats['models'], stats['keys']), (1, [USER_KEY]))                       # key 확인은 모델 조회뿐, 사용자 key로만
        prs = api.get(f'/api/ai/v1/pulls?repository_id={repo}&state=open').json()
        self.assertEqual([p['number'] for p in prs['items']], [7])
        files = api.get(f'/api/ai/v1/pulls/7/files?repository_id={repo}').json()
        self.assertEqual((files['base_sha'], files['head_sha']), (self.fx.m1, self.fx.f1))
        preview = self.prepare(api, repo)
        self.assertTrue(preview['reviewable'] and preview['execution_valid'])
        self.assertEqual((preview['base_sha'], preview['head_sha'], preview['transfer']['model']), (self.fx.m1, self.fx.f1, 'claude-opus-5-5'))
        self.assertEqual(self.messages(), 0)                                                    # 동의 전에는 아무것도 전송하지 않았다
        self.assertEqual(self.provider_stats()['count_tokens'], 0)
        review = api.json(self.submit(api, preview, 'review'), 202)
        done = self.wait_job(api, review['job_id'])
        self.assertEqual((done['status'], done['error_code'], done['coverage']['analysis_coverage']), ('succeeded', None, 'complete'))
        stats = self.provider_stats()
        self.assertEqual((stats['count_tokens'], stats['messages'], stats['last_model'], stats['last_tools']), (1, 1, 'claude-opus-5-5', []))
        self.assertEqual(stats['keys'], [USER_KEY])                                              # 서버 공용 key·환경변수 주소는 쓰이지 않았다
        result = api.json(api.get(f'/api/ai/v1/results/{done["result_id"]}'), 200)
        self.assertEqual((result['kind'], result['human_validation'], result['structure_validation'], result['finding_count']), ('review', 'unverified', 'passed', 1))
        findings = api.json(api.get(f'/api/ai/v1/results/{done["result_id"]}/findings'), 200)['findings']
        self.assertEqual(findings[0]['quote'], CODE_MARKER)
        fid = findings[0]['finding_id']
        fb = api.json(api.call('PUT', f'/api/ai/v1/findings/{fid}/feedback', {'verdict': 'valid', 'note': 'ok', 'expected_version': 0}), 200)
        self.assertEqual(fb['version'], 1)
        self.assertEqual(api.call('PUT', f'/api/ai/v1/findings/{fid}/feedback', {'verdict': 'uncertain', 'expected_version': 0}).status_code, 409)
        again = api.json(api.get(f'/api/ai/v1/results/{done["result_id"]}'), 200)
        self.assertEqual((again['payload_hash'], again['human_validation']), (result['payload_hash'], 'unverified'))   # 원본 결과는 불변
        # 설명은 독립 실행이다
        summary = api.json(self.submit(api, preview, 'summary'), 202)
        sdone = self.wait_job(api, summary['job_id'])
        self.assertEqual(sdone['status'], 'succeeded')
        self.assertEqual(self.messages(), 2)
        usage = api.json(api.get('/api/ai/v1/usage'), 200)
        self.assertGreater(float(usage['user_spent']), 0)
        self.assertEqual((float(usage['user_reserved']), usage['uncertain_reservations']), (0.0, 0))
        # 같은 요청을 다시 보내면 새 작업·호출이 생기지 않는다
        reuse = api.json(self.submit(api, preview, 'review'), 200)
        self.assertEqual(reuse['job_id'], review['job_id'])
        self.assertEqual(self.messages(), 2)

    def test_secrets_and_code_are_not_in_logs_or_plaintext_rows(self):
        api, repo = self.world()
        preview = self.prepare(api, repo)
        done = self.wait_job(api, api.json(self.submit(api, preview), 202)['job_id'])
        self.assertEqual(done['status'], 'succeeded')
        for name in ('worker', 'dispatcher', 'app', 'provider'):
            out = self.output(name)
            self.assertNotIn(USER_KEY, out, name)
            self.assertNotIn('INTEGRATIONKEY', out, name)
            self.assertNotIn(CODE_MARKER, out, name)
        stored = ' '.join(str(scalar(q) or '') for q in (
            "SELECT string_agg(request_options::text, ' ') FROM ai_jobs", "SELECT string_agg(details::text, ' ') FROM audit_events",
            "SELECT string_agg(manifest::text || coalesce(coverage::text, ''), ' ') FROM ai_inputs"))
        self.assertNotIn(USER_KEY, stored)
        self.assertNotIn(CODE_MARKER, stored.replace('"quote"', ''))                             # manifest·audit·옵션에 원문 코드가 없다
        raw = scalar("SELECT encode(encrypted_payload, 'escape') FROM ai_input_files WHERE content_state = 'read' LIMIT 1")
        self.assertNotIn(CODE_MARKER, raw)
        raw_key = scalar("SELECT encode(encrypted_key, 'escape') FROM ai_credential_versions WHERE status = 'active'")
        self.assertNotIn('INTEGRATIONKEY', raw_key)


class QueueFailures(Stack):
    def test_publish_failure_and_dispatcher_outage_do_not_lose_the_job(self):
        api, repo = self.world()
        preview = self.prepare(api, repo)
        self.stop('dispatcher')
        self.start_dispatcher(redis_url='redis://127.0.0.1:9/0')                                # 발행이 계속 실패하는 dispatcher
        job_id = api.json(self.submit(api, preview), 202)['job_id']
        wait_for(lambda: scalar('SELECT retry_count FROM task_outbox WHERE job_id = :j', j=job_id) >= 1, 25, message='발행 실패가 25초 안에 기록되지 않았다(닫힌 Redis 앞에서 dispatcher가 오래 막힌다)' + chr(10) + self.output('dispatcher'))
        self.assertEqual(self.job(api, job_id)['status'], 'queued')
        pending = sql("SELECT published_at, retry_count FROM task_outbox WHERE job_id = :j", j=job_id)
        self.assertEqual([r['published_at'] for r in pending], [None])                           # 발행되지 않은 채 outbox에 남아 있다
        self.assertGreaterEqual(pending[0]['retry_count'], 1)                                    # 발행 실패가 기록됐다
        self.assertEqual(self.messages(), 0)
        self.stop('dispatcher')
        self.start_dispatcher()                                                                  # 복구
        done = self.wait_job(api, job_id)
        self.assertEqual(done['status'], 'succeeded')
        self.assertEqual(self.messages(), 1)                                                     # 한 번만 처리됐다

    def test_worker_killed_during_the_provider_call_is_uncertain_and_never_repeated(self):
        api, repo = self.world()
        preview = self.prepare(api, repo)
        self.provider_mode('slow', delay=25)
        job_id = api.json(self.submit(api, preview), 202)['job_id']
        wait_for(lambda: self.messages() == 1, 60, message='제공자 호출이 시작되지 않았다')
        self.stop('worker')                                                                      # kill -9: 호출 도중 worker 소멸
        failed = self.wait_job(api, job_id, ('failed',), 60)
        self.assertEqual((failed['error_code'], failed['outcome_uncertain']), ('OUTCOME_UNCERTAIN', True))
        self.provider_mode('ok')
        self.start_worker()
        time.sleep(8)
        self.assertEqual(self.messages(), 1)                                                     # 새 worker가 다시 호출하지 않았다
        self.assertEqual(self.job(api, job_id)['status'], 'failed')
        usage = api.json(api.get('/api/ai/v1/usage'), 200)
        self.assertEqual(usage['uncertain_reservations'], 1)                                     # 예산 점유가 남아 있다
        # 사용자가 명시적으로 다시 실행하면 새 generation이 만들어진다
        retry = api.json(api.post(f'/api/ai/v1/jobs/{job_id}/retry', {'consent': self.consent(preview)}), 202)
        self.assertEqual(retry['generation'], 1)
        self.assertEqual(self.wait_job(api, retry['job_id'])['status'], 'succeeded')
        self.assertEqual(self.messages(), 2)

    def test_lost_response_is_uncertain_and_not_retried(self):
        api, repo = self.world()
        preview = self.prepare(api, repo)
        self.provider_mode('drop')
        job_id = api.json(self.submit(api, preview), 202)['job_id']
        failed = self.wait_job(api, job_id, ('failed',), 60)
        self.assertEqual((failed['outcome_uncertain'], failed['error_code'] in ('PROVIDER_TIMEOUT', 'OUTCOME_UNCERTAIN')), (True, True))
        time.sleep(6)
        self.assertEqual(self.messages(), 1)
        self.provider_mode('ok')
        self.assertEqual(self.provider_stats()['messages'], 1)

    def test_provider_server_error_with_a_request_id_is_retried_without_charge(self):
        api, repo = self.world()
        preview = self.prepare(api, repo)
        self.provider_mode('status', status=529)
        job_id = api.json(self.submit(api, preview), 202)['job_id']
        wait_for(lambda: self.messages() >= 1, 60)
        self.provider_mode('ok')
        done = self.wait_job(api, job_id, ('succeeded', 'failed'), 90)
        self.assertEqual(done['status'], 'succeeded')                                            # 처리되지 않았음이 확실한 오류만 자동 재시도
        usage = api.json(api.get('/api/ai/v1/usage'), 200)
        self.assertEqual(usage['uncertain_reservations'], 0)

    def test_output_that_fails_validation_is_stored_nowhere_and_not_retried(self):
        api, repo = self.world()
        preview = self.prepare(api, repo)
        self.provider_mode('fake_location')
        job_id = api.json(self.submit(api, preview), 202)['job_id']
        failed = self.wait_job(api, job_id, ('failed',), 60)
        self.assertEqual(failed['error_code'], 'OUTPUT_VALIDATION_FAILED')
        self.assertEqual(scalar('SELECT count(*) FROM ai_results'), 0)
        time.sleep(4)
        self.assertEqual(self.messages(), 1)
        self.assertGreater(float(api.json(api.get('/api/ai/v1/usage'), 200)['user_spent']), 0)   # 호출 비용은 기록된다


class Races(Stack):
    def test_cancel_during_the_call_never_publishes_the_result(self):
        api, repo = self.world()
        preview = self.prepare(api, repo)
        self.provider_mode('slow', delay=6)
        job_id = api.json(self.submit(api, preview), 202)['job_id']
        wait_for(lambda: self.messages() == 1, 60)
        cancel = api.post(f'/api/ai/v1/jobs/{job_id}/cancel')
        self.assertEqual((cancel.status_code, cancel.json()['status']), (202, 'cancel_requested'))
        done = self.wait_job(api, job_id, ('cancelled', 'succeeded', 'failed'), 60)
        self.assertEqual(done['status'], 'cancelled')
        self.assertEqual(scalar('SELECT count(*) FROM ai_results'), 0)
        usage = wait_for(lambda: (lambda u: u if float(u['user_spent']) > 0 else None)(api.get('/api/ai/v1/usage').json()), 30)
        self.assertEqual(float(usage['user_reserved']), 0.0)                                     # 호출 비용은 정산돼 있다

    def test_deleting_the_input_before_the_run_cancels_the_job_without_any_call(self):
        api, repo = self.world()
        preview = self.prepare(api, repo)
        self.stop('dispatcher')
        job_id = api.json(self.submit(api, preview), 202)['job_id']
        deleted = api.call('DELETE', f'/api/ai/v1/inputs/{preview["input_id"]}')
        self.assertEqual(deleted.status_code, 202)
        self.assertEqual(self.job(api, job_id)['status'], 'cancelled')
        self.start_dispatcher()
        time.sleep(3)
        self.assertEqual(self.messages(), 0)
        self.assertEqual(scalar("SELECT count(*) FROM ai_input_files WHERE encrypted_payload IS NOT NULL"), 0)   # 원문은 삭제됐다
        self.assertEqual(self.submit(api, preview).status_code, 410)

    def test_deleting_the_key_cancels_pending_jobs_before_any_call(self):
        api, repo = self.world()
        preview = self.prepare(api, repo)
        self.stop('dispatcher')
        job_id = api.json(self.submit(api, preview), 202)['job_id']
        deleted = api.json(api.call('DELETE', '/api/ai/v1/credentials/anthropic'), 200)
        self.assertEqual(deleted['jobs_cancelled'], 1)
        self.start_dispatcher()
        time.sleep(3)
        self.assertEqual((self.job(api, job_id)['status'], self.messages()), ('cancelled', 0))
        self.assertEqual(scalar('SELECT count(*) FROM ai_credential_versions WHERE encrypted_key IS NOT NULL'), 0)
        self.assertEqual(self.submit(api, preview).status_code, 422)                             # 키 없이는 접수되지 않는다

    def test_expired_inputs_are_purged_by_maintenance_and_cannot_start_new_runs(self):
        api, repo = self.world()
        preview = self.prepare(api, repo)
        with engine.begin() as db:
            db.execute(text('SET LOCAL session_replication_role = replica'))
            db.execute(text("UPDATE ai_inputs SET expires_at = now() - interval '1 minute' WHERE id = :i"), {'i': preview['input_id']})
        wait_for(lambda: scalar("SELECT status FROM ai_inputs WHERE id = :i", i=preview['input_id']) == 'expired', 30, message='만료 처리가 되지 않았다')
        self.assertEqual(scalar("SELECT count(*) FROM ai_input_files WHERE encrypted_payload IS NOT NULL"), 0)
        self.assertEqual(self.submit(api, preview).status_code, 410)
        self.assertEqual(self.messages(), 0)


class Retention(Stack):
    def test_expired_results_are_purged_by_the_dispatcher_and_disappear_from_the_api(self):
        api, repo = self.world()
        preview = self.prepare(api, repo)
        job_id = api.json(self.submit(api, preview), 202)['job_id']
        done = self.wait_job(api, job_id)
        self.assertEqual(done['status'], 'succeeded')
        with engine.begin() as db:
            db.execute(text('SET LOCAL session_replication_role = replica'))
            db.execute(text("UPDATE ai_results SET expires_at = now() - interval '1 minute'"))
        self.assertEqual(api.get(f'/api/ai/v1/results/{done["result_id"]}').status_code, 410)       # 만료 직후에는 410
        wait_for(lambda: scalar('SELECT count(*) FROM ai_results') == 0, 30, message='만료된 결과가 삭제되지 않았다')
        self.assertEqual(api.get(f'/api/ai/v1/results/{done["result_id"]}').status_code, 404)        # 삭제 뒤에는 존재하지 않는다
        self.assertEqual(api.get(f'/api/ai/v1/jobs/{job_id}').status_code, 404)
        self.assertEqual(scalar('SELECT count(*) FROM ai_findings'), 0)
        self.assertGreaterEqual(scalar('SELECT count(*) FROM usage_reservations'), 1)                  # 비용 기록은 남는다
        again = self.submit(api, preview)                                                              # 새 요청은 새 작업(새 key)으로 접수된다
        self.assertEqual(again.status_code, 202)


class Boundaries(Stack):
    def test_other_users_and_anonymous_clients_see_nothing(self):
        api, repo = self.world()
        preview = self.prepare(api, repo)
        job = api.json(self.submit(api, preview), 202)
        done = self.wait_job(api, job['job_id'])
        stranger = Api(self.base, self.make_user())
        for path in (f'/api/ai/v1/jobs/{job["job_id"]}', f'/api/ai/v1/results/{done["result_id"]}', f'/api/ai/v1/inputs/{preview["input_id"]}',
                     f'/api/ai/v1/results/{done["result_id"]}/findings'):
            self.assertEqual(stranger.get(path).status_code, 404, path)
        self.assertEqual(stranger.get('/api/ai/v1/credentials').json()['credentials'], [])
        anon = requests.get(f'{self.base}/api/ai/v1/jobs/{job["job_id"]}', timeout=10)
        self.assertEqual(anon.status_code, 401)
        no_csrf = api.http.post(f'{self.base}/api/ai/v1/jobs/{job["job_id"]}/cancel', headers={'Origin': self.base}, timeout=10)
        self.assertEqual(no_csrf.status_code, 403)
        no_origin = api.http.post(f'{self.base}/api/ai/v1/jobs/{job["job_id"]}/cancel', headers={'X-CSRF-Token': api.csrf_token or 'x'}, timeout=10)
        self.assertEqual(no_origin.status_code, 403)

    def test_invalid_key_is_rejected_by_the_provider_and_never_stored(self):
        api, repo = Api(self.base, self.make_user()), self.make_repo()
        r = api.call('PUT', '/api/ai/v1/credentials/anthropic', {'api_key': 'sk-ant-api03-THISISABADKEY0123456789BADKEY', 'verify': True})
        self.assertEqual((r.status_code, r.json()['error']['code']), (422, 'CREDENTIAL_INVALID'))
        self.assertNotIn('BADKEY', r.text)
        self.assertEqual(scalar('SELECT count(*) FROM ai_credential_versions'), 0)

    def test_analytics_and_auth_endpoints_still_work_beside_the_ai_routes(self):
        self.assertEqual(requests.get(f'{self.base}/api/auth/me', timeout=10).status_code, 401)
        api, _ = self.world(register_key=False)
        self.assertEqual(api.get('/api/auth/me').status_code, 200)
        self.assertEqual(requests.get(f'{self.base}/repositories', timeout=10).status_code, 200)


class EvaluationHarness(Stack):
    """평가 도구(evaluate_ai_review.py)가 실제 HTTP 흐름 전체를 지나 채점하고 예산을 지키는지. 모델은 가짜 제공자다(정확도 평가가 아니다)."""

    def case(self):
        return {'id': 'harness-1', 'pr_number': 7, 'base_sha': self.fx.m1, 'head_sha': self.fx.f1, 'selected_paths': ['main.py'],
                'truths': [{'path': 'main.py', 'side': 'RIGHT', 'start_line': 2, 'end_line': 2}]}

    def test_harness_scores_a_real_run_and_respects_the_budget(self):
        import evaluate_ai_review as ev
        api, repo = self.world()
        runner = ev.Runner(api.http, self.base, budget=5.0, poll_seconds=0.5, timeout=90)
        score = runner.run_case(self.case(), repo)
        self.assertEqual((score.status, score.findings, score.truths), ('succeeded', 1, 1))
        self.assertGreater(score.cost, 0)
        self.assertEqual((score.coverage['analysis_coverage'], score.coverage_source), ('complete', 'result'))   # 결과 API에서 읽은 분석 범위
        # 위치가 겹쳐도 사람이 확인하기 전에는 발견이 아니다: 후보·확인 대기로만 센다
        self.assertEqual((score.candidate_truths, score.matched_truths, len(score.pending)), (1, 0, 1))
        held = ev.summarize([score], {'min_recall': 0.5}, expected_cases=1)
        self.assertEqual((held['verdict'], held['pending_confirmation']), ('hold', 1))
        self.assertEqual(self.messages(), 1)
        # 사람이 화면(API)에서 유효로 판정한 뒤 재채점한다 — 제공자를 다시 호출하지 않는다
        findings = api.get(f'/api/ai/v1/results/{score.result_id}/findings').json()['findings']
        api.json(api.call('PUT', f'/api/ai/v1/findings/{findings[0]["finding_id"]}/feedback', {'verdict': 'valid', 'expected_version': 0}), 200)
        report = {'set': 'tuning', 'summary': {'expected_cases': 1}, 'cases': [score.__dict__]}
        again = ev.rescore(report, [self.case()], api.http, self.base)
        self.assertEqual((again[0].matched_truths, len(again[0].pending), again[0].coverage_source), (1, 0, 'result'))
        self.assertEqual(ev.summarize(again, {'min_recall': 0.5}, expected_cases=1)['verdict'], 'pass')
        self.assertEqual(self.messages(), 1)
        before = self.messages()
        with self.assertRaises(ev.BudgetExceeded):                                                           # 예산이 부족하면 전송(접수) 전에 멈춘다
            ev.Runner(api.http, self.base, budget=0.0001, poll_seconds=0.5, timeout=90).run_case(self.case(), repo)
        self.assertEqual(self.messages(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
