"""S8 생성·결과·피드백 HTTP API 테스트: /api/ai/v1/jobs, /results, /findings, /usage.

실제 PostgreSQL·실제 job runner·실제 provider adapter(HTTP만 MockTransport)를 쓴다. 저장소 접근 확인은 대역으로 바꾼다.
생성 실행 자체는 test_ai_generation.py가 검증하므로 여기서는 API가 그 서비스를 올바르게 연결하는지를 본다.
"""
import json
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402

support.require_test_database()
sys.path.insert(0, str(support.ROOT))

import ai_credential_support as C  # noqa: E402
import ai_input_support as S  # noqa: E402
import httpx2  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from app.api.ai import feedback as feedback_api  # noqa: E402
from app.api.ai import inputs as inputs_api  # noqa: E402
from app.api.ai import jobs as jobs_api  # noqa: E402
from app.api.ai import results as results_api  # noqa: E402
from app.api.ai import usage as usage_api  # noqa: E402
from app.api.ai.dependencies import ai_error_response, ai_validation_error_response  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.schemas.ai import ErrorCode, canonical_hash  # noqa: E402
from app.services.ai import job_service  # noqa: E402
from app.services.ai.access import AIAction, AIError  # noqa: E402
from test_ai_generation import Gen, USAGE  # noqa: E402  (생성 실행 테스트의 공용 대역)

ORIGIN = 'http://localhost:8000'


def setUpModule():
    S.migrate()


def build_app():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key='api-test', same_site='lax')
    app.add_exception_handler(AIError, ai_error_response)
    app.add_exception_handler(RequestValidationError, ai_validation_error_response)
    for module in (auth_router, jobs_api, results_api, feedback_api, usage_api, inputs_api):
        app.include_router(module.router)

    @app.post('/test/login/{user_id}')
    async def login(user_id: str, request: Request):
        request.session['user_id'] = user_id
        return {}
    return app


class ApiCase(Gen):
    def setUp(self):
        super().setUp()
        self.deny = {}                       # action 값 → 낼 오류 (저장소 접근 확인 대역)
        self.api_calls = []

        def authorize(db, user_id, repo_id, action, policy):
            self.api_calls.append((action.value, repo_id))
            if action.value in self.deny:
                raise self.deny[action.value]
            if '*' in self.deny:
                raise self.deny['*']
            return None, {'private': False}
        self._api_patch = patch.object(jobs_api, 'authorize_ai', side_effect=authorize)
        self._api_patch.start()
        self.client = TestClient(build_app())
        self.token = None

    def tearDown(self):
        self._api_patch.stop()
        self.client.close()
        super().tearDown()

    # ── 요청 helper ──
    def login(self, user):
        self.client.post(f'/test/login/{user}')
        self.token = self.client.get('/api/auth/csrf').json()['token']

    def headers(self, key=None, **extra):
        headers = {'Origin': ORIGIN, 'X-CSRF-Token': self.token, **extra}
        if key:
            headers['Idempotency-Key'] = key
        return headers

    def consent(self, w, **over):
        row = self.input_row(w.input_id)
        found = self.rows("""SELECT v.version FROM ai_credential_versions v JOIN ai_credentials c ON c.id = v.credential_id
                              WHERE c.owner_user_id = :o AND v.status = 'active'""", o=w.owner)
        version = found[0]['version'] if found else 1
        data = {'input_id': str(w.input_id), 'manifest_hash': (row['manifest_hash'] or 'a' * 64).strip(), 'provider': 'anthropic',
                'profile_id': C.OPUS, 'credential_version': version, 'policy_version': 'code-transfer-v1'}
        data.update(over)
        return data

    def post_job(self, w, kind='review', key=None, force=False, consent=None):
        body = {'kind': kind, 'consent': consent or self.consent(w), 'force_regenerate': force}
        return self.client.post('/api/ai/v1/jobs', json=body, headers=self.headers(key or uuid4().hex))

    def error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()['error']['code'], code)
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        return response.json()['error']

    def ready(self, kind='review', **kw):
        """입력 준비·key 등록을 끝낸 상태와 로그인. (작업은 아직 없다)"""
        owner, repo = self.make_user(), self.make_repo()
        _, _, input_id, _ = self.prepared(owner, repo, now=datetime.now(timezone.utc), pr_number=kw.get('pr_number', 7))
        C.ensure_credential(self.db, owner, key=C.TEST_KEY)
        self.login(owner)
        return type('W', (), {'owner': owner, 'repo': repo, 'input_id': input_id})()

    def succeeded(self, kind='review'):
        """API로 접수하고 worker로 실행해 성공시킨다."""
        w = self.ready()
        response = self.post_job(w, kind)
        self.assertEqual(response.status_code, 202, response.text)
        w.job_id = UUID(response.json()['job_id'])
        self.output = self.valid_output(kind, w.job_id)
        self.assertEqual(job_runner_run(self, w.job_id), 'succeeded')
        return w

    def counts(self):
        return {t: self.scalar(f'SELECT count(*) FROM {t}') for t in ('ai_jobs', 'task_outbox', 'ai_attempts',
                                                                        'usage_reservations', 'ai_results', 'audit_events')}


def job_runner_run(case, job_id):
    from app.services.ai import job_runner
    case.db.rollback()
    return job_runner.run_job(job_id, session_factory=S.Session)


class Access(ApiCase):
    def test_login_feature_flag_and_csrf_for_every_route(self):
        w = self.ready()
        anon = TestClient(build_app())
        routes = (('post', '/api/ai/v1/jobs'), ('get', '/api/ai/v1/jobs?repository_id=1'), ('get', f'/api/ai/v1/jobs/{uuid4()}'),
                  ('post', f'/api/ai/v1/jobs/{uuid4()}/cancel'), ('post', f'/api/ai/v1/jobs/{uuid4()}/retry'),
                  ('delete', f'/api/ai/v1/jobs/{uuid4()}'), ('get', f'/api/ai/v1/results/{uuid4()}'),
                  ('get', f'/api/ai/v1/results/{uuid4()}/findings'), ('put', f'/api/ai/v1/findings/{uuid4()}/feedback'),
                  ('get', '/api/ai/v1/usage'))
        for method, path in routes:
            self.error(getattr(anon, method)(path), 401, 'AUTH_REQUIRED')
        with S.ai_env(AI_ENABLED=0):
            for method, path in routes:
                if method == 'get':
                    self.error(getattr(self.client, method)(path), 404, 'RESOURCE_NOT_FOUND')
            self.error(self.post_job(w), 404, 'RESOURCE_NOT_FOUND')
        body = {'kind': 'review', 'consent': self.consent(w)}
        self.error(self.client.post('/api/ai/v1/jobs', json=body, headers={'Idempotency-Key': 'k'}), 403, 'CSRF_FAILED')
        self.error(self.client.post('/api/ai/v1/jobs', json=body, headers={**self.headers('k'), 'Origin': 'https://evil.example'}),
                   403, 'CSRF_FAILED')
        for method, path in (('post', f'/api/ai/v1/jobs/{uuid4()}/cancel'), ('delete', f'/api/ai/v1/jobs/{uuid4()}'),
                             ('put', f'/api/ai/v1/findings/{uuid4()}/feedback')):
            self.error(getattr(self.client, method)(path), 403, 'CSRF_FAILED')
        self.assertEqual(self.scalar("SELECT count(*) FROM ai_jobs WHERE kind <> 'prepare'"), 0)

    def test_validation_errors_use_the_error_envelope_without_echoing_input(self):
        w = self.ready()
        for body in ({'kind': 'prepare', 'consent': self.consent(w)}, {'kind': 'review'}, {'kind': 'review', 'consent': self.consent(w), 'x': 1},
                     {'kind': 'review', 'consent': {**self.consent(w), 'manifest_hash': 'zz'}}):
            error = self.error(self.client.post('/api/ai/v1/jobs', json=body, headers=self.headers('k1')), 422, 'INVALID_SELECTION')
            self.assertIn('fields', error['details'])
        self.error(self.client.post('/api/ai/v1/jobs', json={'kind': 'review', 'consent': self.consent(w)}, headers=self.headers()),
                   422, 'INVALID_STATE')                                                      # Idempotency-Key 없음
        self.error(self.client.post('/api/ai/v1/jobs', json={'kind': 'review', 'consent': self.consent(w)},
                                    headers=self.headers('bad key with spaces')), 422, 'INVALID_STATE')
        self.assertEqual(self.scalar("SELECT count(*) FROM ai_jobs WHERE kind <> 'prepare'"), 0)


class CreateJob(ApiCase):
    def test_accepts_with_202_pins_the_consent_and_enqueues_exactly_once(self):
        w = self.ready()
        before = self.counts()
        r = self.post_job(w, 'review', key='k1')
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(r.headers['cache-control'], 'private, no-store')
        view = r.json()
        self.assertEqual((view['kind'], view['status'], view['stage'], view['generation'], view['provider'], view['model']),
                         ('review', 'queued', 'queued', 0, 'anthropic', 'claude-opus-5-5'))
        self.assertEqual((view['result_id'], view['outcome_uncertain'], view['error_code']), (None, False, None))
        self.assertEqual(view['coverage']['analysis_coverage'], 'none')
        row = self.job_row(UUID(view['job_id']))
        self.assertEqual((row['provider_profile'], row['consent_manifest_hash'].strip()),
                         (C.OPUS, self.input_row(w.input_id)['manifest_hash'].strip()))
        self.assertIsNotNone(row['credential_version_id'])
        after = self.counts()
        self.assertEqual((after['ai_jobs'] - before['ai_jobs'], after['task_outbox'] - before['task_outbox']), (1, 1))
        self.assertEqual(self.http, [])                                                       # 접수만으로는 아무것도 전송하지 않는다
        audit = self.rows("SELECT details FROM audit_events WHERE action = 'job.created'")[0]['details']
        self.assertEqual((audit['kind'], audit['model']), ('review', 'claude-opus-5-5'))
        self.assertNotIn(C.TEST_KEY, json.dumps(audit))

    def test_idempotency_key_replays_the_first_job_even_after_the_key_changed(self):
        w = self.ready()
        first = self.post_job(w, 'review', key='same')
        again = self.post_job(w, 'review', key='same')
        self.assertEqual((first.status_code, again.status_code, again.json()['job_id']), (202, 200, first.json()['job_id']))
        consent = self.consent(w)
        from app.services.ai import credentials
        with S.Session() as db:                                                               # key를 교체해 동의가 낡았다
            credentials.register_key(db, w.owner, 'anthropic', 'sk-ant-api03-REPLACEDKEY0123456789abcdef', verify=False)
        replay = self.client.post('/api/ai/v1/jobs', json={'kind': 'review', 'consent': consent, 'force_regenerate': False},
                                  headers=self.headers('same'))
        self.assertEqual((replay.status_code, replay.json()['job_id']), (200, first.json()['job_id']))
        stale = self.post_job(w, 'review', key='other', consent=consent)                       # 새 key + 낡은 동의는 거부
        self.error(stale, 409, 'PREVIEW_CHANGED')
        self.error(self.post_job(w, 'summary', key='same'), 409, 'IDEMPOTENCY_CONFLICT')       # 같은 key 다른 요청
        self.assertEqual(self.scalar("SELECT count(*) FROM ai_jobs WHERE kind <> 'prepare'"), 1)

    def test_same_fingerprint_reuse_and_explicit_regeneration(self):
        w = self.succeeded()
        reused = self.post_job(w, 'review', key='n1')
        self.assertEqual((reused.status_code, reused.json()['job_id'], reused.json()['status']), (200, str(w.job_id), 'succeeded'))
        forced = self.post_job(w, 'review', key='n2', force=True)
        self.assertEqual((forced.status_code, forced.json()['generation'], forced.json()['status']), (202, 1, 'queued'))
        pending = self.post_job(w, 'review', key='n3')                                          # 진행 중이면 그 작업을 돌려준다
        self.assertEqual((pending.status_code, pending.json()['job_id']), (200, forced.json()['job_id']))
        self.assertEqual(self.scalar("SELECT count(*) FROM ai_jobs WHERE kind <> 'prepare'"), 2)

    def test_summary_and_review_are_independent_jobs(self):
        w = self.ready()
        a, b = self.post_job(w, 'summary', key='a'), self.post_job(w, 'review', key='b')
        self.assertEqual((a.status_code, b.status_code), (202, 202))
        self.assertNotEqual(a.json()['job_id'], b.json()['job_id'])

    def test_consent_must_match_scope_model_and_key(self):
        w = self.ready()
        self.error(self.post_job(w, consent=self.consent(w, manifest_hash='e' * 64)), 409, 'PREVIEW_CHANGED')
        self.error(self.post_job(w, consent=self.consent(w, credential_version=9)), 409, 'PREVIEW_CHANGED')
        self.error(self.post_job(w, consent=self.consent(w, policy_version='code-transfer-v0')), 403, 'TRANSFER_NOT_ALLOWED')
        self.error(self.post_job(w, consent=self.consent(w, profile_id='gpt-5-v1')), 422, 'INVALID_SELECTION')
        with S.ai_env(AI_ALLOWED_PROFILES=C.SONNET, AI_ALLOW_EXTERNAL_TRANSFER=1):
            self.error(self.post_job(w), 409, 'PREVIEW_CHANGED')
        with S.ai_env(AI_ALLOW_EXTERNAL_TRANSFER=0):
            self.error(self.post_job(w), 403, 'TRANSFER_NOT_ALLOWED')
        self.assertEqual(self.scalar("SELECT count(*) FROM ai_jobs WHERE kind <> 'prepare'"), 0)
        self.assertEqual(self.post_job(w).status_code, 202)

    def test_missing_key_and_blocked_repository(self):
        w = self.ready()
        credential = self.rows('SELECT id FROM ai_credentials WHERE owner_user_id = :o', o=w.owner)[0]['id']
        with S.Session() as db:
            from app.services.ai import credentials
            credentials.delete_credential(db, w.owner, 'anthropic')
        self.error(self.post_job(w), 422, 'CREDENTIAL_REQUIRED')
        self.assertIsNotNone(credential)
        C.ensure_credential(self.db, w.owner, key=C.TEST_KEY)
        self.deny['generate'] = AIError(ErrorCode.TRANSFER_NOT_ALLOWED, 'private repository')
        self.error(self.post_job(w), 403, 'TRANSFER_NOT_ALLOWED')                              # 비공개 저장소는 전송 불가
        self.deny['generate'] = AIError(ErrorCode.RESOURCE_NOT_FOUND, 'no access')
        self.error(self.post_job(w), 404, 'RESOURCE_NOT_FOUND')
        self.deny.clear()

    def test_inputs_that_are_not_reviewable_expired_deleted_building_or_foreign_are_blocked(self):
        owner, repo = self.make_user(), self.make_repo()
        C.ensure_credential(self.db, owner, key=C.TEST_KEY)
        self.login(owner)
        # reviewable=false: 선택한 파일이 모두 제외돼 AI에 보낼 내용이 없다
        _, _, empty, _ = self.prepared(owner, repo, ['.env', 'link.py'], now=datetime.now(timezone.utc))
        w = type('W', (), {'owner': owner, 'repo': repo, 'input_id': empty})()
        error = self.error(self.post_job(w), 422, 'INVALID_STATE')
        self.assertEqual(error['details'], {'reason': 'NO_REVIEWABLE_CONTENT'})
        # 만료
        _, _, old, _ = self.prepared(owner, repo, now=datetime.now(timezone.utc), pr_number=8)
        w2 = type('W', (), {'owner': owner, 'repo': repo, 'input_id': old})()
        self.consent(w2)
        self.bypass("UPDATE ai_inputs SET valid_until = now() - interval '1 minute' WHERE id = :i", i=old)
        self.error(self.post_job(w2), 410, 'INPUT_EXPIRED')
        # 삭제된 입력
        _, _, gone, _ = self.prepared(owner, repo, now=datetime.now(timezone.utc), pr_number=9)
        w3 = type('W', (), {'owner': owner, 'repo': repo, 'input_id': gone})()
        consent = self.consent(w3)
        self.assertEqual(self.client.delete(f'/api/ai/v1/inputs/{gone}', headers=self.headers()).status_code, 202)
        self.error(self.post_job(w3, consent=consent), 410, 'INPUT_EXPIRED')
        # 준비 중(building)
        building = self.submit(owner, repo, pr_number=10).job.input_id
        w4 = type('W', (), {'owner': owner, 'repo': repo, 'input_id': building})()
        self.error(self.post_job(w4, consent=self.consent(w4, manifest_hash='a' * 64)), 422, 'INVALID_STATE')
        # 다른 사용자의 입력은 존재를 숨긴다
        other = self.make_user()
        C.ensure_credential(self.db, other, key=C.TEST_KEY)
        _, _, theirs, _ = self.prepared(other, self.make_repo(), now=datetime.now(timezone.utc))
        w5 = type('W', (), {'owner': other, 'repo': repo, 'input_id': theirs})()
        self.error(self.post_job(w5), 404, 'RESOURCE_NOT_FOUND')
        self.assertEqual(self.scalar("SELECT count(*) FROM ai_jobs WHERE kind <> 'prepare'"), 0)

    def test_daily_limit_and_oversized_body(self):
        w = self.ready()
        with S.ai_env(AI_DAILY_JOB_LIMIT=1, AI_ALLOW_EXTERNAL_TRANSFER=1):
            self.assertEqual(self.post_job(w, 'review', key='1').status_code, 202)
            self.error(self.post_job(w, 'summary', key='2'), 429, 'USER_LIMIT')
        r = self.client.post('/api/ai/v1/jobs', content=b'{"kind":"review","pad":"' + b'x' * 9000 + b'"}',
                             headers={**self.headers('big'), 'Content-Type': 'application/json'})
        self.assertIn(r.status_code, (413, 422))


class ReadJobs(ApiCase):
    def test_full_flow_job_result_and_findings_through_http(self):
        w = self.succeeded()
        job = self.client.get(f'/api/ai/v1/jobs/{w.job_id}')
        self.assertEqual(job.status_code, 200)
        self.assertEqual(job.headers['cache-control'], 'private, no-store')
        view = job.json()
        self.assertEqual((view['status'], view['stage'], view['error_code']), ('succeeded', None, None))
        self.assertEqual(view['coverage']['analysis_coverage'], 'complete')
        result = self.client.get(f'/api/ai/v1/results/{view["result_id"]}')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers['cache-control'], 'private, no-store')
        body = result.json()
        self.assertEqual((body['kind'], body['human_validation'], body['structure_validation'], body['finding_count']),
                         ('review', 'unverified', 'passed', 1))
        self.assertEqual(body['payload_hash'], canonical_hash(body['payload']))
        findings = self.client.get(f'/api/ai/v1/results/{view["result_id"]}/findings').json()
        self.assertEqual(len(findings['findings']), 1)
        item = findings['findings'][0]
        self.assertEqual((item['finding']['human_validation'], item['finding']['location']['path'], item['quote'], item['feedback']),
                         ('unverified', 'main.py', 'b = 20', None))
        self.assertNotIn('file_id', json.dumps(item['finding']))
        self.assertEqual(self.client.get('/api/ai/v1/jobs', params={'repository_id': w.repo}).json()['jobs'][0]['job_id'], str(w.job_id))

    def test_reads_have_no_side_effects(self):
        w = self.succeeded()
        before = self.counts()
        calls = len(self.http)
        result_id = self.client.get(f'/api/ai/v1/jobs/{w.job_id}').json()['result_id']
        for path in (f'/api/ai/v1/jobs/{w.job_id}', f'/api/ai/v1/jobs?repository_id={w.repo}', f'/api/ai/v1/results/{result_id}',
                     f'/api/ai/v1/results/{result_id}/findings', '/api/ai/v1/usage'):
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.counts(), before)
        self.assertEqual(len(self.http), calls)

    def test_other_users_and_lost_repository_access_hide_the_resources(self):
        w = self.succeeded()
        result_id = self.client.get(f'/api/ai/v1/jobs/{w.job_id}').json()['result_id']
        finding_id = self.rows('SELECT id FROM ai_findings')[0]['id']
        with patch.object(jobs_api, 'authorize_ai', side_effect=AIError(ErrorCode.RESOURCE_NOT_FOUND, 'gone')):
            for path in (f'/api/ai/v1/jobs/{w.job_id}', f'/api/ai/v1/results/{result_id}',
                         f'/api/ai/v1/results/{result_id}/findings', f'/api/ai/v1/jobs?repository_id={w.repo}'):
                self.error(self.client.get(path), 404, 'RESOURCE_NOT_FOUND')
            self.error(self.client.put(f'/api/ai/v1/findings/{finding_id}/feedback', headers=self.headers(),
                                       json={'verdict': 'valid', 'expected_version': 0}), 404, 'RESOURCE_NOT_FOUND')
        stranger = self.make_user()
        self.login(stranger)
        for path in (f'/api/ai/v1/jobs/{w.job_id}', f'/api/ai/v1/results/{result_id}', f'/api/ai/v1/results/{result_id}/findings'):
            self.error(self.client.get(path), 404, 'RESOURCE_NOT_FOUND')
        self.error(self.client.post(f'/api/ai/v1/jobs/{w.job_id}/cancel', headers=self.headers()), 404, 'RESOURCE_NOT_FOUND')
        self.error(self.client.delete(f'/api/ai/v1/jobs/{w.job_id}', headers=self.headers()), 404, 'RESOURCE_NOT_FOUND')
        self.error(self.client.put(f'/api/ai/v1/findings/{finding_id}/feedback', headers=self.headers(),
                                   json={'verdict': 'valid', 'expected_version': 0}), 404, 'RESOURCE_NOT_FOUND')
        self.assertEqual(self.client.get('/api/ai/v1/jobs', params={'repository_id': w.repo}).json()['jobs'], [])   # 목록에도 없다

    def test_expired_result_is_gone(self):
        w = self.succeeded()
        result_id = self.client.get(f'/api/ai/v1/jobs/{w.job_id}').json()['result_id']
        finding_id = self.rows('SELECT id FROM ai_findings')[0]['id']
        self.bypass("UPDATE ai_results SET expires_at = now() - interval '1 minute'")
        for path in (f'/api/ai/v1/results/{result_id}', f'/api/ai/v1/results/{result_id}/findings'):
            self.error(self.client.get(path), 410, 'RESULT_EXPIRED')
        self.error(self.client.put(f'/api/ai/v1/findings/{finding_id}/feedback', headers=self.headers(),
                                   json={'verdict': 'valid', 'expected_version': 0}), 410, 'RESULT_EXPIRED')

    def test_job_list_uses_a_stable_keyset_without_gaps_or_duplicates(self):
        owner, repo = self.make_user(), self.make_repo()
        _, _, input_id, _ = self.prepared(owner, repo, now=datetime.now(timezone.utc))
        self.login(owner)
        ids = [self.world_for(owner, repo, 'review', input_id=input_id).job.id for _ in range(5)]
        self.bypass("UPDATE ai_jobs SET created_at = now() WHERE kind = 'review'")        # 같은 시각이어도 id로 순서가 고정된다
        seen, cursor, pages = [], None, 0
        while True:
            params = {'repository_id': repo, 'kind': 'review', 'limit': 2}
            if cursor:
                params['cursor'] = cursor
            page = self.client.get('/api/ai/v1/jobs', params=params).json()
            seen += [j['job_id'] for j in page['jobs']]
            pages += 1
            cursor = page['next_cursor']
            if cursor is None:
                break
            if pages == 2:                                                                  # 도중에 새 작업이 생겨도 이미 본 구간은 흔들리지 않는다
                self.world_for(owner, repo, 'review', input_id=input_id)
        self.assertEqual((pages, len(seen), len(set(seen))), (3, 5, 5))
        self.assertEqual(set(seen), {str(i) for i in ids})
        self.assertEqual(seen, sorted(seen, key=lambda s: (s,), reverse=True))              # created_at 동률이면 id 내림차순
        for bad in ('!!!', 'e30', json_cursor(['x', 'y'])):
            self.error(self.client.get('/api/ai/v1/jobs', params={'repository_id': repo, 'cursor': bad}), 422, 'INVALID_SELECTION')
        self.assertEqual(self.client.get('/api/ai/v1/jobs', params={'repository_id': repo, 'limit': 500}).status_code, 422)
        self.assertEqual(self.client.get('/api/ai/v1/jobs').status_code, 422)               # repository_id가 필요하다

    def test_job_list_filters_and_scopes(self):
        w = self.ready()
        self.post_job(w, 'summary', key='s')
        self.post_job(w, 'review', key='r')
        listing = lambda **p: self.client.get('/api/ai/v1/jobs', params={'repository_id': w.repo, **p}).json()['jobs']
        self.assertEqual(len(listing()), 3)                                                   # 입력 준비 작업 포함
        self.assertEqual([j['kind'] for j in listing(kind='summary')], ['summary'])
        self.assertEqual(len(listing(status='succeeded', kind='prepare')), 1)
        self.assertEqual(listing(status='failed'), [])
        self.assertEqual(listing(input_id=str(uuid4())), [])
        self.assertEqual(self.client.get('/api/ai/v1/jobs', params={'repository_id': w.repo + 999}).json()['jobs'], [])

    def test_running_job_reports_a_stage_and_retry_after(self):
        w = self.ready()
        job_id = UUID(self.post_job(w).json()['job_id'])
        with S.Session() as db:
            job_service.claim_job(db, job_id)
        view = self.client.get(f'/api/ai/v1/jobs/{job_id}').json()
        self.assertEqual((view['status'], view['stage']), ('running', 'input_preparation'))
        self.assertEqual(view['retry_after_seconds'], None)


def json_cursor(parts):
    import base64
    return base64.urlsafe_b64encode(json.dumps(parts).encode()).decode().rstrip('=')


class CancelRetryDelete(ApiCase):
    def test_cancel_uses_the_existing_state_transitions(self):
        w = self.ready()
        queued = UUID(self.post_job(w, 'review', key='a').json()['job_id'])
        r = self.client.post(f'/api/ai/v1/jobs/{queued}/cancel', headers=self.headers())
        self.assertEqual((r.status_code, r.json()['status']), (200, 'cancelled'))
        again = self.client.post(f'/api/ai/v1/jobs/{queued}/cancel', headers=self.headers())
        self.assertEqual((again.status_code, again.json()['status']), (200, 'cancelled'))      # 멱등
        running = UUID(self.post_job(w, 'summary', key='b').json()['job_id'])
        with S.Session() as db:
            job_service.claim_job(db, running)
        r = self.client.post(f'/api/ai/v1/jobs/{running}/cancel', headers=self.headers())
        self.assertEqual((r.status_code, r.json()['status'], r.json()['cancel_requested']), (202, 'cancel_requested', True))
        self.assertEqual(self.client.post(f'/api/ai/v1/jobs/{uuid4()}/cancel', headers=self.headers()).status_code, 404)

    def test_cancel_works_without_current_repository_access(self):
        w = self.ready()
        job_id = self.post_job(w).json()['job_id']
        self.deny['*'] = AIError(ErrorCode.RESOURCE_NOT_FOUND, 'gone')
        self.assertEqual(self.client.post(f'/api/ai/v1/jobs/{job_id}/cancel', headers=self.headers()).status_code, 200)

    def test_retry_creates_a_new_generation_only_on_explicit_request_with_new_consent(self):
        w = self.ready()
        job_id = UUID(self.post_job(w).json()['job_id'])
        self.error(self.client.post(f'/api/ai/v1/jobs/{job_id}/retry', headers=self.headers('r0'),
                                    json={'consent': self.consent(w)}), 422, 'INVALID_STATE')   # 진행 중이면 재시도할 수 없다

        def lost(request):
            raise httpx2.ReadTimeout('slow')
        self.responder = lost
        self.assertEqual(job_runner_run(self, job_id), 'failed')
        failed = self.client.get(f'/api/ai/v1/jobs/{job_id}').json()
        self.assertEqual((failed['status'], failed['error_code'], failed['outcome_uncertain']), ('failed', 'OUTCOME_UNCERTAIN', True))
        self.assertEqual(self.client.get('/api/ai/v1/usage').json()['uncertain_reservations'], 1)
        self.assertEqual(job_runner_run(self, job_id), 'skipped')                             # 자동으로는 다시 실행되지 않는다
        self.assertEqual(len(self.messages_calls()), 1)
        self.error(self.client.post(f'/api/ai/v1/jobs/{job_id}/retry', headers=self.headers('r1'),
                                    json={'consent': self.consent(w, manifest_hash='e' * 64)}), 409, 'PREVIEW_CHANGED')
        self.error(self.client.post(f'/api/ai/v1/jobs/{job_id}/retry', headers=self.headers('r1'),
                                    json={'consent': self.consent(w, input_id=str(uuid4()))}), 409, 'PREVIEW_CHANGED')
        self.error(self.client.post(f'/api/ai/v1/jobs/{job_id}/retry', headers=self.headers(), json={'consent': self.consent(w)}),
                   422, 'INVALID_STATE')                                                      # Idempotency-Key 필요
        r = self.client.post(f'/api/ai/v1/jobs/{job_id}/retry', headers=self.headers('r2'), json={'consent': self.consent(w)})
        self.assertEqual((r.status_code, r.json()['generation'], r.json()['status']), (202, 1, 'queued'))
        replay = self.client.post(f'/api/ai/v1/jobs/{job_id}/retry', headers=self.headers('r2'), json={'consent': self.consent(w)})
        self.assertEqual((replay.status_code, replay.json()['job_id']), (200, r.json()['job_id']))
        self.responder = None
        self.output = self.valid_output('review', UUID(r.json()['job_id']))
        self.assertEqual(job_runner_run(self, UUID(r.json()['job_id'])), 'succeeded')
        usage = self.client.get('/api/ai/v1/usage').json()
        self.assertEqual(usage['uncertain_reservations'], 1)                                  # 불명 호출의 예약은 정리될 때까지 남는다
        self.assertEqual(self.client.get(f'/api/ai/v1/jobs/{job_id}').json()['status'], 'failed')   # 원래 작업은 바뀌지 않는다

    def test_retry_is_refused_for_succeeded_prepare_and_foreign_jobs(self):
        w = self.succeeded()
        self.error(self.client.post(f'/api/ai/v1/jobs/{w.job_id}/retry', headers=self.headers('x'), json={'consent': self.consent(w)}),
                   422, 'INVALID_STATE')
        prepare_id = self.rows("SELECT id FROM ai_jobs WHERE kind = 'prepare'")[0]['id']
        self.error(self.client.post(f'/api/ai/v1/jobs/{prepare_id}/retry', headers=self.headers('y'), json={'consent': self.consent(w)}),
                   422, 'INVALID_STATE')
        self.login(self.make_user())
        self.error(self.client.post(f'/api/ai/v1/jobs/{w.job_id}/retry', headers=self.headers('z'), json={'consent': self.consent(w)}),
                   404, 'RESOURCE_NOT_FOUND')

    def test_result_lost_job_is_only_regenerated_by_an_explicit_retry(self):
        w = self.ready()
        job_id = UUID(self.post_job(w).json()['job_id'])
        self.output = self.valid_output('review', job_id)

        class Crash(BaseException):
            pass
        with patch('app.services.ai.generation_service._record_request_id', side_effect=Crash()):
            with self.assertRaises(Crash):
                job_runner_run(self, job_id)
        with S.Session() as db:
            db.execute(text("UPDATE ai_jobs SET lease_until = now() - interval '1 minute' WHERE id = :j"), {'j': job_id})
            db.commit()
        with S.Session() as db:
            job_service.recover_expired(db)
        view = self.client.get(f'/api/ai/v1/jobs/{job_id}').json()
        self.assertEqual((view['status'], view['error_code']), ('failed', 'RESULT_LOST'))
        self.assertEqual(job_runner_run(self, job_id), 'skipped')
        self.assertEqual(len(self.messages_calls()), 1)
        r = self.client.post(f'/api/ai/v1/jobs/{job_id}/retry', headers=self.headers('again'), json={'consent': self.consent(w)})
        self.assertEqual((r.status_code, r.json()['generation']), (202, 1))

    def test_delete_removes_finished_jobs_with_results_and_feedback(self):
        w = self.succeeded()
        result_id = self.client.get(f'/api/ai/v1/jobs/{w.job_id}').json()['result_id']
        finding_id = self.rows('SELECT id FROM ai_findings')[0]['id']
        self.client.put(f'/api/ai/v1/findings/{finding_id}/feedback', headers=self.headers(),
                        json={'verdict': 'valid', 'note': 'confirmed', 'expected_version': 0})
        running = UUID(self.post_job(w, 'summary', key='s').json()['job_id'])
        self.error(self.client.delete(f'/api/ai/v1/jobs/{running}', headers=self.headers()), 422, 'INVALID_STATE')   # 진행 중
        r = self.client.delete(f'/api/ai/v1/jobs/{w.job_id}', headers=self.headers())
        self.assertEqual((r.status_code, r.json()), (200, {'job_id': str(w.job_id), 'status': 'deleted'}))
        self.assertEqual(r.headers['cache-control'], 'private, no-store')
        for path in (f'/api/ai/v1/jobs/{w.job_id}', f'/api/ai/v1/results/{result_id}', f'/api/ai/v1/results/{result_id}/findings'):
            self.error(self.client.get(path), 404, 'RESOURCE_NOT_FOUND')
        self.assertEqual([self.scalar(f'SELECT count(*) FROM {t}') for t in ('ai_results', 'ai_findings', 'finding_feedback')], [0, 0, 0])
        self.assertGreaterEqual(self.scalar('SELECT count(*) FROM usage_reservations'), 1)    # 비용 기록은 남는다
        self.error(self.client.delete(f'/api/ai/v1/jobs/{w.job_id}', headers=self.headers()), 404, 'RESOURCE_NOT_FOUND')
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_events WHERE action = 'job.deleted'"), 1)
        fresh = self.post_job(w, 'review', key='after-delete')                                  # 삭제한 작업은 재사용되지 않는다
        self.assertEqual(fresh.status_code, 202)


class Feedback(ApiCase):
    def setUp(self):
        super().setUp()
        self.w = self.ready()
        job_id = UUID(self.post_job(self.w).json()['job_id'])
        handle = self.handle_of(job_id)
        self.output = {'schema_version': 'review-output-v1', 'findings': [
            self.finding(handle, title=f'finding {i}', priority=i) for i in (1, 2, 3)]}
        self.assertEqual(job_runner_run(self, job_id), 'succeeded')
        self.result_id = self.client.get(f'/api/ai/v1/jobs/{job_id}').json()['result_id']
        self.finding_ids = [r['id'] for r in self.rows('SELECT id FROM ai_findings ORDER BY ordinal')]

    def put(self, index=0, **body):
        body.setdefault('expected_version', 0)
        body.setdefault('verdict', 'valid')
        return self.client.put(f'/api/ai/v1/findings/{self.finding_ids[index]}/feedback', headers=self.headers(), json=body)

    def test_feedback_versions_and_conflicts(self):
        r = self.put(note='looks right')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()['verdict'], r.json()['note'], r.json()['version']), ('valid', 'looks right', 1))
        self.assertEqual(r.headers['cache-control'], 'private, no-store')
        stale = self.put(verdict='false_positive')                                           # 처음이라고 하지만 이미 있다
        self.assertEqual(self.error(stale, 409, 'VERSION_CONFLICT')['details'], {'current_version': 1})
        self.error(self.put(expected_version=5, verdict='uncertain'), 409, 'VERSION_CONFLICT')
        r2 = self.put(verdict='false_positive', note=None, expected_version=1)
        self.assertEqual((r2.status_code, r2.json()['version'], r2.json()['verdict'], r2.json()['note']), (200, 2, 'false_positive', None))
        self.assertEqual(self.error(self.put(expected_version=1), 409, 'VERSION_CONFLICT')['details'], {'current_version': 2})
        self.assertEqual(self.rows('SELECT verdict, version FROM finding_feedback')[0]['version'], 2)
        self.assertEqual(self.put(index=1, expected_version=0, verdict='uncertain').json()['version'], 1)   # 지적마다 따로

    def test_original_result_stays_immutable_and_feedback_is_layered_on_the_view(self):
        before = self.client.get(f'/api/ai/v1/results/{self.result_id}').json()
        self.put(verdict='false_positive', note='not an issue')
        after = self.client.get(f'/api/ai/v1/results/{self.result_id}').json()
        self.assertEqual(before, after)                                                      # 원본 결과(payload·human_validation)는 그대로
        self.assertEqual(after['human_validation'], 'unverified')
        listed = self.client.get(f'/api/ai/v1/results/{self.result_id}/findings').json()['findings']
        self.assertEqual([f['finding']['human_validation'] for f in listed], ['false_positive', 'unverified', 'unverified'])
        self.assertEqual((listed[0]['feedback']['note'], listed[0]['feedback']['version']), ('not an issue', 1))
        self.assertIsNone(listed[1]['feedback'])
        self.assertEqual(self.rows('SELECT human_validation FROM ai_results')[0]['human_validation'], 'unverified')
        for sql in ("UPDATE ai_findings SET title = 'x'", "UPDATE ai_results SET payload = '{}'::jsonb"):
            with self.assertRaises(Exception):
                self.db.execute(text(sql))
            self.db.rollback()

    def test_only_the_result_owner_can_give_feedback_and_only_valid_requests(self):
        for body in ({'verdict': 'bogus', 'expected_version': 0}, {'verdict': 'valid'}, {'verdict': 'valid', 'expected_version': -1},
                     {'verdict': 'valid', 'expected_version': 0, 'note': 'x' * 2001}, {'verdict': 'valid', 'expected_version': 0, 'extra': 1}):
            r = self.client.put(f'/api/ai/v1/findings/{self.finding_ids[0]}/feedback', headers=self.headers(), json=body)
            self.error(r, 422, 'INVALID_SELECTION')
        self.error(self.client.put(f'/api/ai/v1/findings/{uuid4()}/feedback', headers=self.headers(),
                                   json={'verdict': 'valid', 'expected_version': 0}), 404, 'RESOURCE_NOT_FOUND')
        self.login(self.make_user())
        self.error(self.put(), 404, 'RESOURCE_NOT_FOUND')
        self.assertEqual(self.scalar('SELECT count(*) FROM finding_feedback'), 0)
        audit = self.rows("SELECT details FROM audit_events WHERE action = 'finding.feedback'")
        self.assertEqual(audit, [])

    def test_feedback_note_is_not_written_to_the_audit_log(self):
        self.put(note='secret-looking note SECRETNOTE123')
        details = json.dumps(self.rows("SELECT details FROM audit_events WHERE action = 'finding.feedback'")[0]['details'])
        self.assertNotIn('SECRETNOTE123', details)
        self.assertEqual(json.loads(details), {'verdict': 'valid', 'version': 1})

    def test_concurrent_feedback_with_the_same_version_lets_exactly_one_win(self):
        outcomes = []
        owner = self.w.owner

        def run(verdict):
            client = TestClient(build_app())
            client.post(f'/test/login/{owner}')
            token = client.get('/api/auth/csrf').json()['token']
            r = client.put(f'/api/ai/v1/findings/{self.finding_ids[2]}/feedback',
                           headers={'Origin': ORIGIN, 'X-CSRF-Token': token},
                           json={'verdict': verdict, 'expected_version': 0})
            outcomes.append(r.status_code)
            client.close()
        threads = [threading.Thread(target=run, args=(v,)) for v in ('valid', 'false_positive', 'uncertain', 'valid')]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(outcomes), [200, 409, 409, 409])
        self.assertEqual(self.scalar('SELECT count(*) FROM finding_feedback'), 1)

    def test_findings_are_paged_by_ordinal(self):
        first = self.client.get(f'/api/ai/v1/results/{self.result_id}/findings', params={'limit': 2}).json()
        self.assertEqual(([f['finding']['ordinal'] for f in first['findings']], bool(first['next_cursor'])), ([1, 2], True))
        second = self.client.get(f'/api/ai/v1/results/{self.result_id}/findings',
                                 params={'limit': 2, 'cursor': first['next_cursor']}).json()
        self.assertEqual(([f['finding']['ordinal'] for f in second['findings']], second['next_cursor']), ([3], None))
        self.error(self.client.get(f'/api/ai/v1/results/{self.result_id}/findings', params={'cursor': 'zz'}), 422, 'INVALID_SELECTION')


class Usage(ApiCase):
    def test_usage_reflects_spend_limits_and_budget_mode(self):
        w = self.succeeded()
        usage = self.client.get('/api/ai/v1/usage')
        self.assertEqual(usage.headers['cache-control'], 'private, no-store')
        body = usage.json()
        self.assertEqual((body['currency'], body['user_limit'], body['server_budget_enabled'], body['uncertain_reservations']),
                         ('USD', '20.000000', False, 0))
        self.assertEqual(float(body['user_spent']), float(self.expected_cost(USAGE)))
        self.assertEqual(float(body['user_reserved']), 0)
        self.assertAlmostEqual(float(body['user_remaining']), 20 - float(self.expected_cost(USAGE)), places=5)
        self.assertEqual((body['daily_jobs_used'], body['daily_job_limit']), (1, 30))
        self.login(self.make_user())
        other = self.client.get('/api/ai/v1/usage').json()
        self.assertEqual((float(other['user_spent']), other['daily_jobs_used']), (0.0, 0))     # 다른 사용자의 사용량은 보이지 않는다
        self.assertIsNotNone(w)


if __name__ == '__main__':
    unittest.main(verbosity=2)
