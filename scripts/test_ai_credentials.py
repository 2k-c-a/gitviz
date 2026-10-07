"""사용자 API key(BYOK) 서비스 테스트: 암호화 저장·교체·삭제·접근 제한·작업 고정·전송 동의·사용량.

실제 PostgreSQL을 쓰고 제공자 호출은 mock한다(httpx2.MockTransport). 외부로는 아무것도 보내지 않는다.
"""
import json
import logging
import os
import sys
import threading
import unittest
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402

support.require_test_database()
sys.path.insert(0, str(support.ROOT))

import ai_credential_support as C  # noqa: E402
import ai_input_support as S  # noqa: E402
import anthropic  # noqa: E402
import httpx2  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402

from app.schemas.ai import ErrorCode, TransferConsent, canonical_hash  # noqa: E402
from app.services.ai import consent, credentials, input_store, job_runner, job_service, usage_service  # noqa: E402
from app.services.ai.access import AIError  # noqa: E402
from app.services.ai.providers import registry  # noqa: E402
from app.services.ai.providers.anthropic_provider import AnthropicProvider  # noqa: E402
from app.services.ai.providers.base import ProviderError  # noqa: E402

T0 = S.T0
KEY_A = 'sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAA1111'
KEY_B = 'sk-ant-api03-BBBBBBBBBBBBBBBBBBBBBBBBBBBB2222'
KEY_C = 'sk-ant-api03-CCCCCCCCCCCCCCCCCCCCCCCCCCCC3333'
ALL_KEYS = (KEY_A, KEY_B, KEY_C)
OPUS, SONNET = C.OPUS, C.SONNET


def setUpModule():
    S.migrate()


def ok_verifier(profile, key):
    ok_verifier.calls.append((profile.id, key))


ok_verifier.calls = []


def failing_verifier(kind):
    def verify(profile, key):
        from app.services.ai.providers.base import ProviderFailure
        raise ProviderError(ProviderFailure(kind, retryable=kind in ('server_error', 'connection_failed', 'rate_limited'),
                                            outcome_uncertain=False, retry_after=30 if kind == 'rate_limited' else None))
    return verify


class Base(S.InputCase):
    def setUp(self):
        super().setUp()
        ok_verifier.calls.clear()

    def register(self, owner, key=KEY_A, **kw):
        kw.setdefault('verifier', ok_verifier)
        return credentials.register_key(self.db, owner, 'anthropic', key, **kw)

    def versions(self, owner):
        self.db.rollback()
        return [tuple(r) for r in self.db.execute(text("""
            SELECT v.version, v.status, v.encrypted_key IS NOT NULL FROM ai_credential_versions v
            WHERE v.owner_user_id = :o ORDER BY v.version"""), {'o': owner}).all()]

    def job(self, owner, repo, input_id, pinned=None, kind='review', fingerprint=None, key=None):
        return job_service.create_job(
            self.db, owner_id=owner, repository_id=repo, input_id=input_id, kind=kind,
            fingerprint=fingerprint or canonical_hash([kind, str(uuid4())]), route='POST /api/ai/v1/jobs',
            idempotency_key=key or uuid4().hex, body_hash=canonical_hash(str(uuid4())),
            pinned=pinned or self.pinned(owner, repo, input_id), now=T0 + timedelta(minutes=1)).job

    def stored_text(self):
        """DB 어디에도 key가 평문으로 있으면 안 되는 곳의 내용."""
        self.db.rollback()
        return ' '.join(str(self.db.execute(text(sql)).scalar() or '') for sql in (
            "SELECT string_agg(request_options::text || coalesce(provider_profile,'') || coalesce(error_code,''), ' ') FROM ai_jobs",
            "SELECT string_agg(event_type, ' ') FROM task_outbox",
            "SELECT string_agg(action || object_type || coalesce(object_id,'') || request_id || details::text, ' ') FROM audit_events",
            "SELECT string_agg(key_hint || key_fingerprint || coalesce(key_version,''), ' ') FROM ai_credential_versions",
            "SELECT string_agg(route || key, ' ') FROM request_idempotency"))


class Registration(Base):
    def test_key_is_stored_encrypted_and_only_a_masked_hint_is_returned(self):
        owner = self.make_user()
        result = self.register(owner)
        view = result.credential
        self.assertEqual((result.changed, result.previous_version, view.provider, view.version), (True, None, 'anthropic', 1))
        self.assertEqual(view.key_hint, 'sk-ant-…1111')
        self.assertEqual(view.preferred_profile_id, OPUS)
        self.assertIsNotNone(view.validated_at)
        self.assertNotIn(KEY_A, view.model_dump_json())
        row = self.db.execute(text("SELECT encrypted_key, key_version, key_fingerprint FROM ai_credential_versions")).one()
        raw = bytes(row.encrypted_key)
        self.assertNotIn(KEY_A.encode(), raw)
        self.assertNotIn(b'AAAAAAAA', raw)
        self.assertEqual(row.key_version, 'c1')
        self.assertEqual(len(row.key_fingerprint.strip()), 64)
        self.assertNotIn(KEY_A, row.key_fingerprint)
        self.assertNotIn('AAAAAAAAAAAA', self.stored_text())                               # 어떤 평문 필드에도 없다
        self.assertEqual(ok_verifier.calls, [(OPUS, KEY_A)])                              # 제공자 확인은 한 번
        self.assertEqual([c.provider for c in credentials.list_credentials(self.db, owner)], ['anthropic'])
        self.assertNotIn('AAAAAAAA', ' '.join(c.model_dump_json() for c in credentials.list_credentials(self.db, owner)))

    def test_format_validation_rejects_bad_keys_without_echoing_them(self):
        owner = self.make_user()
        bad = ['', 'short', 'sk-ant-', 'sk-ant-' + 'a' * 5, 'gsk_' + 'a' * 40, 'sk-ant-api03-has space inside123456',
               'sk-ant-api03-line\nbreak1234567890', 'sk-ant-api03-é' + 'a' * 30, 'sk-ant-api03-' + 'a' * 400]
        for key in bad:
            with self.subTest(key=key[:20]):
                with self.assertRaises(AIError) as ctx:
                    self.register(owner, key)
                self.assertEqual(ctx.exception.code, ErrorCode.CREDENTIAL_INVALID)
                self.assertNotIn(key[:20] or 'x', ctx.exception.message) if key else None
        self.assertEqual(ok_verifier.calls, [])                                           # 형식이 틀리면 확인도 하지 않는다
        self.assertEqual(self.versions(owner), [])
        # 붙여넣기 때 따라온 앞뒤 공백·줄바꿈은 지운다
        self.assertTrue(self.register(owner, '  ' + KEY_A + '\n').changed)

    def test_provider_rejection_or_outage_stores_nothing(self):
        owner = self.make_user()
        cases = {'auth_failed': (ErrorCode.CREDENTIAL_INVALID, 'auth_failed'), 'billing_error': (ErrorCode.CREDENTIAL_INVALID, 'billing_error'),
                 'model_unavailable': (ErrorCode.CREDENTIAL_INVALID, 'model_unavailable'),
                 'rate_limited': (ErrorCode.UPSTREAM_RATE_LIMIT, None), 'server_error': (ErrorCode.UPSTREAM_UNAVAILABLE, None),
                 'connection_failed': (ErrorCode.UPSTREAM_UNAVAILABLE, None), 'timeout': (ErrorCode.UPSTREAM_UNAVAILABLE, None)}
        for kind, (code, reason) in cases.items():
            with self.subTest(kind=kind):
                with self.assertRaises(AIError) as ctx:
                    self.register(owner, verifier=failing_verifier(kind))
                self.assertEqual(ctx.exception.code, code)
                if reason:
                    self.assertEqual(ctx.exception.details, {'reason': reason})
                self.assertNotIn(KEY_A, ctx.exception.message + json.dumps(ctx.exception.details))
        self.assertEqual(self.versions(owner), [])
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_credentials'), 0)

    def test_verification_is_rate_limited_per_user_and_counts_failures(self):
        owner, other = self.make_user(), self.make_user()
        with S.ai_env(AI_CREDENTIAL_VERIFY_LIMIT_PER_HOUR=3):
            for _ in range(3):
                with self.assertRaises(AIError):
                    self.register(owner, verifier=failing_verifier('auth_failed'))
            with self.assertRaises(AIError) as ctx:
                self.register(owner)                                                      # 4번째는 제공자를 부르기 전에 막힌다
            self.assertEqual((ctx.exception.code, ctx.exception.headers), (ErrorCode.USER_LIMIT, {'Retry-After': '3600'}))
            self.assertEqual(ok_verifier.calls, [])
            self.assertTrue(self.register(other).changed)                                 # 다른 사용자는 영향 없음
            # 1시간이 지나면 다시 가능
            result = credentials.register_key(self.db, owner, 'anthropic', KEY_B, verifier=ok_verifier,
                                              now=T0 + timedelta(hours=2))
            self.assertTrue(result.changed)

    def test_unverified_registration_and_unsupported_provider_or_model(self):
        owner = self.make_user()
        result = self.register(owner, verify=False)
        self.assertIsNone(result.credential.validated_at)
        self.assertEqual(ok_verifier.calls, [])
        with self.assertRaises(AIError) as ctx:
            credentials.register_key(self.db, owner, 'unsupported', KEY_B, verifier=ok_verifier)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_SELECTION)
        for bad in ('gpt-4-v1', 'nonexistent-profile'):
            with self.assertRaises(AIError) as ctx:
                self.register(owner, KEY_B, preferred_profile_id=bad)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_SELECTION)
        with S.ai_env(AI_ALLOWED_PROFILES=OPUS):                                            # 서버가 허용하지 않은 모델
            with self.assertRaises(AIError) as ctx:
                self.register(owner, KEY_B, preferred_profile_id=SONNET)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_SELECTION)
        self.assertEqual(self.register(owner, KEY_B, preferred_profile_id=SONNET).credential.preferred_profile_id, SONNET)

    def test_concurrent_registration_of_the_same_key_creates_one_version(self):
        owner = self.make_user()
        results = []

        def run():
            with S.Session() as db:
                results.append(credentials.register_key(db, owner, 'anthropic', KEY_A, verifier=ok_verifier))
        threads = [threading.Thread(target=run) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sum(r.changed for r in results), 1)
        self.assertEqual(self.versions(owner), [(1, 'active', True)])


class Replacement(Base):
    def test_same_key_again_changes_nothing_and_a_new_key_creates_a_new_version(self):
        owner = self.make_user()
        first = self.register(owner)
        again = self.register(owner)
        self.assertEqual((again.changed, again.credential.version), (False, 1))
        self.assertEqual(len(ok_verifier.calls), 1)                                       # 같은 key는 다시 확인하지 않는다
        changed = self.register(owner, KEY_B)
        self.assertEqual((changed.changed, changed.previous_version, changed.credential.version), (True, 1, 2))
        self.assertEqual(changed.credential.key_hint, 'sk-ant-…2222')
        self.assertEqual(self.versions(owner), [(1, 'retired', False), (2, 'active', True)])   # 쓰는 작업이 없으면 옛 암호문은 바로 삭제
        self.assertEqual(self.scalar("SELECT count(*) FROM ai_credentials"), 1)
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_events WHERE action = 'credential.replaced'"), 1)
        # 돌아가려면 옛 key를 다시 등록해야 한다(옛 버전은 되살아나지 않는다)
        back = self.register(owner, KEY_A)
        self.assertEqual((back.credential.version, back.previous_version), (3, 2))
        self.assertEqual(self.versions(owner), [(1, 'retired', False), (2, 'retired', False), (3, 'active', True)])
        self.assertEqual(first.credential.version, 1)

    def test_in_flight_jobs_keep_their_pinned_key_and_model_after_replacement(self):
        owner, repo, input_id, _ = self.prepared()
        self.register(owner, KEY_A, preferred_profile_id=OPUS)
        pinned = self.pinned(owner, repo, input_id, profile_id=OPUS)
        queued = self.job(owner, repo, input_id, pinned, fingerprint=canonical_hash('q'))
        other_input = self.prepared(owner, repo, pr_number=8)[2]
        running = self.job(owner, repo, other_input, self.pinned(owner, repo, other_input), fingerprint=canonical_hash('r'))
        lease = job_service.claim_job(self.db, running.id, now=T0 + timedelta(minutes=2))
        self.assertIsNotNone(lease)

        replaced = self.register(owner, KEY_B, preferred_profile_id=SONNET)               # 사용자가 key와 모델을 바꾼다
        self.assertEqual(replaced.credential.in_flight_jobs, 2)
        self.assertEqual(self.versions(owner), [(1, 'retired', True), (2, 'active', True)])   # 고정된 작업이 있어 옛 암호문 유지
        # 진행 중 작업은 옛 key·옛 모델로 실행된다: 설정이 섞이지 않는다
        for job in (queued, running):
            self.db.rollback()
            row = self.job_row(job.id)
            self.assertEqual((row['provider'], row['model'], row['provider_profile']), ('anthropic', 'claude-opus-5-5', OPUS))
            self.assertEqual(credentials.open_secret_for_job(self.db, job.id), KEY_A)
            provider = credentials.provider_for_job(self.db, job.id)
            self.assertEqual((provider.profile.id, provider.profile.model), (OPUS, 'claude-opus-5-5'))
        # 새 작업은 새 key·새 모델로 고정된다
        new_input = self.prepared(owner, repo, pr_number=9)[2]
        fresh = self.job(owner, repo, new_input, self.pinned(owner, repo, new_input, profile_id=SONNET), fingerprint=canonical_hash('n'))
        self.assertEqual(credentials.open_secret_for_job(self.db, fresh.id), KEY_B)
        self.assertEqual(self.job_row(fresh.id)['model'], 'claude-sonnet-5-5')
        # 작업이 모두 끝나면 옛 암호문이 지워진다(유지관리 주기에서)
        job_service.finish_job(self.db, lease, 'succeeded', now=T0 + timedelta(minutes=3))
        self.assertEqual(credentials.run_credential_maintenance(self.db)['secrets_purged'], 0)     # queued 작업이 남아 있다
        job_service.request_cancel(self.db, queued.id, owner, now=T0 + timedelta(minutes=3))
        self.assertEqual(credentials.run_credential_maintenance(self.db)['secrets_purged'], 1)
        self.assertEqual(self.versions(owner), [(1, 'retired', False), (2, 'active', True)])
        with self.assertRaises(credentials.CredentialUnavailable) as ctx:
            credentials.open_secret_for_job(self.db, queued.id)
        self.assertEqual(ctx.exception.reason, 'purged')

    def test_in_progress_job_is_reused_only_for_the_same_key_version(self):
        owner, repo, input_id, _ = self.prepared()
        self.register(owner, KEY_A)
        fp = canonical_hash('same-generation')
        v1_job = self.job(owner, repo, input_id, fingerprint=fp, key='k1')
        again = job_service.create_job(                                                  # 같은 key 버전: 진행 중 작업 재사용
            self.db, owner_id=owner, repository_id=repo, input_id=input_id, kind='review', fingerprint=fp,
            route='POST /api/ai/v1/jobs', idempotency_key='k1b', body_hash=canonical_hash('b1'),
            pinned=self.pinned(owner, repo, input_id), now=T0 + timedelta(minutes=1))
        self.assertEqual((again.created, again.job.id), (False, v1_job.id))
        self.register(owner, KEY_B)                                                       # key 교체 후 새 key에 동의
        v2 = job_service.create_job(
            self.db, owner_id=owner, repository_id=repo, input_id=input_id, kind='review', fingerprint=fp,
            route='POST /api/ai/v1/jobs', idempotency_key='k2', body_hash=canonical_hash('b2'),
            pinned=self.pinned(owner, repo, input_id), now=T0 + timedelta(minutes=1))
        self.assertTrue(v2.created)                                                       # v1 작업을 돌려주지 않는다
        self.assertNotEqual(v2.job.id, v1_job.id)
        self.assertEqual(v2.job.generation, v1_job.generation + 1)
        self.assertEqual(credentials.open_secret_for_job(self.db, v2.job.id), KEY_B)
        old = self.job_row(v1_job.id)                                                     # 기존 작업은 그대로 v1에 고정
        self.assertNotEqual(old['credential_version_id'], self.job_row(v2.job.id)['credential_version_id'])
        self.assertEqual(old['status'], 'queued')
        self.assertEqual(credentials.open_secret_for_job(self.db, v1_job.id), KEY_A)
        # 같은 Idempotency-Key 재전송은 처음 만든 작업(v2)을 그대로 돌려준다
        replay = job_service.create_job(
            self.db, owner_id=owner, repository_id=repo, input_id=input_id, kind='review', fingerprint=fp,
            route='POST /api/ai/v1/jobs', idempotency_key='k2', body_hash=canonical_hash('b2'),
            pinned=self.pinned(owner, repo, input_id), now=T0 + timedelta(minutes=2))
        self.assertEqual((replay.created, replay.job.id), (False, v2.job.id))

    def test_succeeded_result_is_reused_even_with_a_different_key(self):
        owner, repo, input_id, _ = self.prepared()
        self.register(owner, KEY_A)
        fp = canonical_hash('done')
        done = self.job(owner, repo, input_id, fingerprint=fp, key='d1')
        lease = job_service.claim_job(self.db, done.id, now=T0 + timedelta(minutes=2))
        job_service.finish_job(self.db, lease, 'succeeded', now=T0 + timedelta(minutes=3))
        self.register(owner, KEY_B)
        reused = job_service.create_job(
            self.db, owner_id=owner, repository_id=repo, input_id=input_id, kind='review', fingerprint=fp,
            route='POST /api/ai/v1/jobs', idempotency_key='d2', body_hash=canonical_hash('x'),
            pinned=self.pinned(owner, repo, input_id), now=T0 + timedelta(minutes=4))
        self.assertEqual((reused.created, reused.job.id), (False, done.id))
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_jobs WHERE kind = $$review$$'), 1)

    def test_job_creation_races_with_replacement_consistently(self):
        owner, repo, input_id, _ = self.prepared()
        self.register(owner)
        pinned = self.pinned(owner, repo, input_id)
        replacer = S.Session()
        replacer.execute(text("UPDATE ai_credential_versions SET status = 'retired', retired_at = now() WHERE id = :v"),
                         {'v': pinned.credential_version_id})                            # 교체가 먼저(미커밋)
        outcome = {}

        def create():
            with S.Session() as db:
                try:
                    job_service.create_job(db, owner_id=owner, repository_id=repo, input_id=input_id, kind='review',
                                           fingerprint=canonical_hash('x'), route='r', idempotency_key='k',
                                           body_hash=canonical_hash('b'), pinned=pinned, now=T0 + timedelta(minutes=1))
                    outcome['result'] = 'created'
                except AIError as exc:
                    outcome['result'] = exc.code
        thread = threading.Thread(target=create)
        thread.start()
        thread.join(0.7)
        self.assertTrue(thread.is_alive())                                              # 교체가 끝나기를 기다린다
        replacer.commit()
        replacer.close()
        thread.join(10)
        self.assertEqual(outcome['result'], ErrorCode.PREVIEW_CHANGED)                   # 바뀐 key로는 새 작업을 만들 수 없다
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_jobs WHERE kind = $$review$$'), 0)


class Deletion(Base):
    def test_delete_purges_every_secret_and_cancels_pinned_jobs(self):
        owner, repo, input_id, _ = self.prepared()
        self.register(owner, KEY_A)
        queued = self.job(owner, repo, input_id, fingerprint=canonical_hash('q'))
        input2 = self.prepared(owner, repo, pr_number=8)[2]
        running = self.job(owner, repo, input2, fingerprint=canonical_hash('r'))
        lease = job_service.claim_job(self.db, running.id, now=T0 + timedelta(minutes=2))
        self.register(owner, KEY_B)                                                       # 옛 버전은 고정된 작업 때문에 남아 있다
        self.assertEqual(self.versions(owner), [(1, 'retired', True), (2, 'active', True)])
        cancelled = credentials.delete_credential(self.db, owner, 'anthropic', request_id='del-1')
        self.assertEqual(cancelled, 2)
        self.assertEqual(self.versions(owner), [(1, 'deleted', False), (2, 'deleted', False)])
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_credential_versions WHERE encrypted_key IS NOT NULL'), 0)
        self.assertEqual(self.job_row(queued.id)['status'], 'cancelled')
        self.assertEqual(self.job_row(running.id)['status'], 'cancel_requested')
        for job in (queued, running):
            with self.assertRaises(credentials.CredentialUnavailable) as ctx:
                credentials.provider_for_job(self.db, job.id)
            self.assertEqual(ctx.exception.reason, 'deleted')
        self.assertEqual(job_service.finish_job(self.db, lease, 'succeeded', now=T0 + timedelta(minutes=3)), 'cancelled')
        self.assertEqual(credentials.list_credentials(self.db, owner), [])
        self.assertEqual(self.scalar("SELECT request_id FROM audit_events WHERE action = 'credential.deleted'"), 'del-1')
        with self.assertRaises(AIError) as ctx:                                          # 두 번째 삭제는 404
            credentials.delete_credential(self.db, owner, 'anthropic')
        self.assertEqual(ctx.exception.code, ErrorCode.RESOURCE_NOT_FOUND)
        # 삭제 뒤 다시 등록하면 새 버전(번호는 이어진다)
        again = self.register(owner, KEY_C)
        self.assertEqual((again.credential.version, again.changed), (3, True))
        self.assertEqual(credentials.get_active(self.db, owner, 'anthropic')[1].version, 3)

    def test_deleting_someone_elses_key_is_impossible(self):
        owner, stranger = self.make_user(), self.make_user()
        self.register(owner)
        with self.assertRaises(AIError) as ctx:
            credentials.delete_credential(self.db, stranger, 'anthropic')
        self.assertEqual(ctx.exception.code, ErrorCode.RESOURCE_NOT_FOUND)
        self.assertEqual(self.versions(owner), [(1, 'active', True)])


class AccessControl(Base):
    def test_only_the_job_owners_pinned_key_can_be_opened(self):
        a, b = self.make_user(), self.make_user()
        repo = self.make_repo()
        input_a = self.prepared(a, repo)[2]
        self.register(a, KEY_A)
        self.register(b, KEY_B)
        job = self.job(a, repo, input_a)
        self.assertEqual(credentials.open_secret_for_job(self.db, job.id), KEY_A)
        self.assertEqual(credentials.list_credentials(self.db, b)[0].key_hint, 'sk-ant-…2222')
        self.assertIsNone(credentials.get_active(self.db, b, 'openai'))
        # DB가 작업 소유자와 다른 사용자의 key 버전 고정을 거부한다
        version_b = credentials.get_active(self.db, b, 'anthropic')[1]
        with self.assertRaises(DBAPIError):
            with self.db.begin_nested():
                self.db.execute(text("UPDATE ai_jobs SET credential_version_id = :v WHERE id = :j"),
                                {'v': version_b.id, 'j': job.id})
        self.db.rollback()
        # 고정된 설정은 바뀌지 않는다(provider·model·profile·key 버전·동의 해시)
        for column, value in (('provider', 'other'), ('model', 'other'), ('provider_profile', SONNET),
                              ('profile_fingerprint', 'f' * 64), ('credential_version_id', version_b.id),
                              ('consent_manifest_hash', 'e' * 64)):
            with self.assertRaises(DBAPIError):
                with self.db.begin_nested():
                    self.db.execute(text(f"UPDATE ai_jobs SET {column} = :v WHERE id = :j"), {'v': value, 'j': job.id})
            self.db.rollback()

    def test_jobs_cannot_be_created_with_someone_elses_pinned_config(self):
        a, b = self.make_user(), self.make_user()
        repo = self.make_repo()
        input_a = self.prepared(a, repo)[2]
        self.register(a)
        pinned_b = self.pinned(b, repo, input_a)                                           # b의 key로 a의 입력을 지정
        with self.assertRaises((AIError, DBAPIError)):
            self.job(a, repo, input_a, pinned_b)
        self.db.rollback()
        self.assertEqual(self.scalar("SELECT count(*) FROM ai_jobs WHERE kind = 'review'"), 0)
        with self.assertRaises(AIError) as ctx:                                            # 고정 설정이 없는 생성 작업은 거부
            job_service.create_job(self.db, owner_id=a, repository_id=repo, input_id=input_a, kind='review',
                                   fingerprint=canonical_hash('z'), route='r', idempotency_key='k',
                                   body_hash=canonical_hash('z'), now=T0 + timedelta(minutes=1))
        self.assertEqual(ctx.exception.code, ErrorCode.CREDENTIAL_REQUIRED)

    def test_key_never_appears_in_logs_queue_job_options_or_audit(self):
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        root = logging.getLogger()
        old = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            owner, repo, input_id, _ = self.prepared()
            self.register(owner, KEY_A)
            self.register(owner, KEY_B)
            job = self.job(owner, repo, input_id)
            credentials.provider_for_job(self.db, job.id)
            with self.assertRaises(AIError):
                self.register(owner, 'sk-ant-api03-ZZZZZZZZZZZZZZZZZZZZZZ', verifier=failing_verifier('auth_failed'))
            credentials.delete_credential(self.db, owner, 'anthropic')
        finally:
            root.removeHandler(handler)
            root.setLevel(old)
        logs = ' '.join(r.getMessage() + repr(r.args) for r in records)
        for key in ALL_KEYS + ('sk-ant-api03-ZZZZZZZZZZZZZZZZZZZZZZ',):
            self.assertNotIn(key, logs)
            self.assertNotIn(key, self.stored_text())
            self.assertNotIn(key[13:30], logs + self.stored_text())
        # 입력 준비 결과(미리보기)에도 key는 없다
        preview = input_store.load_preview(self.db, input_id, owner, now=T0)
        self.assertNotIn('AAAA', preview.model_dump_json())

    def test_credential_ciphertext_cannot_be_moved_to_another_user_or_version(self):
        a, b = self.make_user(), self.make_user()
        self.register(a, KEY_A)
        self.register(b, KEY_B)
        va = credentials.get_active(self.db, a, 'anthropic')[1]
        vb = credentials.get_active(self.db, b, 'anthropic')[1]
        repo = self.make_repo()
        input_b = self.prepared(b, repo)[2]
        job_b = self.job(b, repo, input_b)
        # 공격 시뮬레이션: trigger를 우회해 a의 암호문을 b의 버전에 덮어쓴다 → AAD(사용자·버전 id) 불일치로 복호화 실패
        with S.Session() as db:
            db.execute(text("SET LOCAL session_replication_role = replica"))
            db.execute(text("UPDATE ai_credential_versions SET encrypted_key = (SELECT encrypted_key FROM "
                            "ai_credential_versions WHERE id = :a) WHERE id = :b"), {'a': va.id, 'b': vb.id})
            db.commit()
        with self.assertRaises(credentials.CredentialUnavailable) as ctx:
            credentials.open_secret_for_job(self.db, job_b.id)
        self.assertEqual(ctx.exception.reason, 'decrypt_failed')

    def test_secret_storage_rules_are_enforced_by_the_database(self):
        owner = self.make_user()
        self.register(owner, KEY_A)
        self.register(owner, KEY_B)                                                       # 1은 retired(+암호문 삭제됨)
        v1 = self.db.execute(text("SELECT id FROM ai_credential_versions WHERE version = 1")).scalar()
        v2 = self.db.execute(text("SELECT id FROM ai_credential_versions WHERE version = 2")).scalar()
        bad = [
            ("UPDATE ai_credential_versions SET status = 'active' WHERE id = :i", v1),                 # 되살리기 금지
            ("UPDATE ai_credential_versions SET encrypted_key = '\\x00', key_version = 'c1' WHERE id = :i", v1),  # 복원 금지
            ("UPDATE ai_credential_versions SET encrypted_key = '\\x01' WHERE id = :i", v2),           # 변경 금지(삭제만 가능)
            ("UPDATE ai_credential_versions SET key_hint = 'x' WHERE id = :i", v2),
            ("UPDATE ai_credential_versions SET key_fingerprint = repeat('0', 64) WHERE id = :i", v2),
            ("UPDATE ai_credential_versions SET owner_user_id = gen_random_uuid() WHERE id = :i", v2),
            ("UPDATE ai_credential_versions SET encrypted_key = NULL, key_version = NULL WHERE id = :i", v2),  # active는 암호문 필요
            ("UPDATE ai_credential_versions SET status = 'active' WHERE id = :i", v2),                 # (이미 active인 행의 중복 활성은 별도)
        ]
        for sql, vid in bad[:-1]:
            with self.subTest(sql=sql[:70]):
                with self.assertRaises(DBAPIError):
                    with self.db.begin_nested():
                        self.db.execute(text(sql), {'i': vid})
                self.db.rollback()
        with self.assertRaises(DBAPIError):                                                # 같은 credential의 active는 하나
            with self.db.begin_nested():
                self.db.execute(text("""INSERT INTO ai_credential_versions(credential_id, owner_user_id, version, status,
                    encrypted_key, key_version, key_hint, key_fingerprint) SELECT credential_id, owner_user_id, 9, 'active',
                    '\\x01', 'c1', 'h', repeat('a', 64) FROM ai_credential_versions WHERE id = :i"""), {'i': v2})
        self.db.rollback()
        with self.assertRaises(DBAPIError):                                                # provider는 바꿀 수 없다
            with self.db.begin_nested():
                self.db.execute(text("UPDATE ai_credentials SET provider = 'other' WHERE owner_user_id = :o"), {'o': owner})
        self.db.rollback()


class Providers(Base):
    def test_supported_options_list_prices_limits_and_server_allow_list(self):
        options = registry.supported_options()
        self.assertEqual([p['id'] for p in options], ['anthropic', 'openai', 'gemini', 'ollama'])
        models = {m['profile_id']: m for m in options[0]['models']}
        self.assertEqual(set(models), {OPUS, SONNET})
        self.assertEqual((models[OPUS]['model'], models[OPUS]['input_price_per_mtok'], models[OPUS]['output_price_per_mtok']),
                         ('claude-opus-5-5', '4.00', '20.00'))
        self.assertEqual((models[SONNET]['model'], models[SONNET]['input_price_per_mtok']), ('claude-sonnet-5-5', '2.00'))
        self.assertTrue(all(m['allowed'] for m in models.values()))
        with S.ai_env(AI_ALLOWED_PROFILES=SONNET):
            allowed = {m['profile_id']: m['allowed'] for m in registry.supported_options()[0]['models']}
            self.assertEqual(allowed, {OPUS: False, SONNET: True})

    def test_verify_credential_calls_only_the_model_lookup_endpoint(self):
        calls = []

        def handler(request):
            calls.append((request.method, request.url.path, request.content))
            return httpx2.Response(200, json={'id': 'claude-opus-5-5', 'type': 'model', 'display_name': 'x',
                                              'created_at': '2026-01-01T00:00:00Z'})
        client = anthropic.Anthropic(api_key=KEY_A, max_retries=0,
                                     http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
        provider = AnthropicProvider(registry.get_profile(OPUS), client=client)
        provider.verify_credential()
        self.assertEqual(calls, [('GET', '/v1/models/claude-opus-5-5', b'')])             # 코드·프롬프트는 보내지 않는다
        for status, kind in ((401, 'auth_failed'), (403, 'auth_failed'), (404, 'model_unavailable'), (429, 'rate_limited'),
                             (500, 'response_lost')):          # request-id 없는 5xx는 출처를 알 수 없다
            client = anthropic.Anthropic(api_key=KEY_A, max_retries=0, http_client=anthropic.DefaultHttpxClient(
                transport=httpx2.MockTransport(lambda r, s=status: httpx2.Response(
                    s, json={'type': 'error', 'error': {'type': 'x', 'message': f'bad {KEY_A}'}}))))
            with self.assertRaises(ProviderError) as ctx:
                AnthropicProvider(registry.get_profile(OPUS), client=client).verify_credential()
            self.assertEqual((ctx.exception.failure.kind, ctx.exception.failure.outcome_uncertain), (kind, False))
            self.assertNotIn(KEY_A, str(ctx.exception))

    def test_default_verifier_injects_the_users_key_not_a_server_key(self):
        seen = []

        class Fake:
            def verify_credential(self):
                pass
        with patch('app.services.ai.credentials.registry.create_provider',
                   side_effect=lambda profile, api_key=None, **kw: seen.append((profile.id, api_key)) or Fake()), \
                patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-ant-SERVER-WIDE-KEY-should-never-be-used'}):
            owner = self.make_user()
            credentials.register_key(self.db, owner, 'anthropic', KEY_A, preferred_profile_id=SONNET)
        self.assertEqual(seen, [(SONNET, KEY_A)])


class Consent(Base):
    def consent_for(self, owner, input_id, **over):
        row = self.input_row(input_id)
        version = credentials.get_active(self.db, owner, 'anthropic')[1].version
        data = dict(input_id=input_id, manifest_hash=(row['manifest_hash'] or 'a' * 64).strip(), provider='anthropic', profile_id=OPUS,
                    credential_version=version, policy_version='code-transfer-v1')
        data.update(over)
        return TransferConsent(**data)

    def resolve(self, owner, consent_obj, **kw):
        with S.ai_env(AI_ALLOW_EXTERNAL_TRANSFER=1):
            return consent.resolve_consent(self.db, owner, consent_obj, now=kw.pop('now', T0 + timedelta(hours=1)))

    def rejected(self, owner, consent_obj, code, **kw):
        with self.assertRaises(AIError) as ctx:
            self.resolve(owner, consent_obj, **kw)
        self.assertEqual(ctx.exception.code, code, ctx.exception.message)
        return ctx.exception

    def test_consent_pins_provider_model_and_key_version(self):
        owner, repo, input_id, _ = self.prepared()
        self.register(owner, KEY_A)
        pinned = self.resolve(owner, self.consent_for(owner, input_id))
        self.assertEqual((pinned.provider, pinned.model, pinned.profile.id, pinned.credential_version, pinned.key_hint),
                         ('anthropic', 'claude-opus-5-5', OPUS, 1, 'sk-ant-…1111'))
        self.assertEqual((pinned.input_id, pinned.repository_id), (input_id, repo))
        job = job_service.create_job(
            self.db, owner_id=owner, repository_id=repo, input_id=input_id, kind='review',
            fingerprint=consent.generation_fingerprint(owner, 'review', 'h' * 64, pinned), route='r', idempotency_key='k',
            body_hash=canonical_hash('b'), pinned=pinned, now=T0 + timedelta(hours=1)).job
        row = self.job_row(job.id)
        self.assertEqual((row['provider'], row['model'], row['provider_profile'], row['prompt_version'], row['schema_version']),
                         ('anthropic', 'claude-opus-5-5', OPUS, 'review-v1', 'review-output-v1'))
        self.assertEqual(row['profile_fingerprint'].strip(), pinned.profile_fingerprint)
        self.assertEqual(row['consent_manifest_hash'].strip(), pinned.manifest_hash)
        self.assertEqual(row['credential_version_id'], pinned.credential_version_id)
        self.assertNotIn(KEY_A, json.dumps(dict(row), default=str))
        # 같은 입력·같은 설정이면 key가 바뀌어도 fingerprint가 같다(결과 재사용), 모델이 다르면 다르다
        self.register(owner, KEY_B)
        again = self.resolve(owner, self.consent_for(owner, input_id))
        self.assertEqual(consent.generation_fingerprint(owner, 'review', 'h' * 64, again),
                         consent.generation_fingerprint(owner, 'review', 'h' * 64, pinned))
        sonnet = self.resolve(owner, self.consent_for(owner, input_id, profile_id=SONNET))
        self.assertNotEqual(consent.generation_fingerprint(owner, 'review', 'h' * 64, sonnet),
                            consent.generation_fingerprint(owner, 'review', 'h' * 64, pinned))
        self.assertNotEqual(consent.generation_fingerprint(owner, 'summary', 'h' * 64, pinned),
                            consent.generation_fingerprint(owner, 'review', 'h' * 64, pinned))

    def test_every_part_of_the_consent_must_match_the_current_state(self):
        owner, repo, input_id, _ = self.prepared()
        self.register(owner)
        base = self.consent_for(owner, input_id)
        self.rejected(owner, self.consent_for(owner, input_id, manifest_hash='e' * 64), ErrorCode.PREVIEW_CHANGED)   # 코드 범위
        self.rejected(owner, self.consent_for(owner, input_id, credential_version=7), ErrorCode.PREVIEW_CHANGED)     # key 버전
        self.rejected(owner, self.consent_for(owner, input_id, policy_version='code-transfer-v0'), ErrorCode.TRANSFER_NOT_ALLOWED)
        self.rejected(owner, self.consent_for(owner, input_id, provider='openai'), ErrorCode.INVALID_SELECTION)
        self.rejected(owner, self.consent_for(owner, input_id, profile_id='gpt-5-v1'), ErrorCode.INVALID_SELECTION)
        self.rejected(owner, base, ErrorCode.INPUT_EXPIRED, now=T0 + timedelta(hours=25))                            # 24시간 유효기간
        with S.ai_env(AI_ALLOWED_PROFILES=SONNET):
            self.rejected(owner, base, ErrorCode.PREVIEW_CHANGED)                                                    # 모델이 더는 허용되지 않음
        with S.ai_env(AI_ALLOW_EXTERNAL_TRANSFER=0):
            with self.assertRaises(AIError) as ctx:
                consent.resolve_consent(self.db, owner, base, now=T0)
            self.assertEqual(ctx.exception.code, ErrorCode.TRANSFER_NOT_ALLOWED)
        self.register(owner, KEY_B)                                                                                  # 확인한 뒤 key 교체
        self.rejected(owner, base, ErrorCode.PREVIEW_CHANGED)
        credentials.delete_credential(self.db, owner, 'anthropic')
        self.rejected(owner, base, ErrorCode.CREDENTIAL_REQUIRED)

    def test_consent_is_per_user_and_input_must_be_reviewable_and_within_the_model_limit(self):
        owner, repo, input_id, _ = self.prepared()
        stranger = self.make_user()
        self.register(owner)
        self.register(stranger, KEY_B)
        exc = self.rejected(stranger, self.consent_for(owner, input_id, credential_version=1), ErrorCode.RESOURCE_NOT_FOUND)
        self.assertEqual(exc.status_code, 404)
        building = self.submit(owner, repo, pr_number=10).job.input_id
        self.rejected(owner, self.consent_for(owner, building, manifest_hash='a' * 64), ErrorCode.INVALID_STATE)
        empty = self.prepared(paths=['.env', 'link.py'])                                                              # 읽을 파일이 없다
        self.register(empty[0], KEY_C)
        exc = self.rejected(empty[0], self.consent_for(empty[0], empty[2]), ErrorCode.INVALID_STATE)
        self.assertIn('NO_REVIEWABLE_CONTENT', exc.message)
        big = dict(max_input_tokens=100)
        import dataclasses
        tiny = dataclasses.replace(registry.get_profile(OPUS), id='tiny-v1', **big)
        with patch.dict(registry.PROFILES, {'tiny-v1': tiny}):
            exc = self.rejected(owner, self.consent_for(owner, input_id, profile_id='tiny-v1'), ErrorCode.INPUT_TOO_LARGE)
        self.assertEqual(exc.details['limit'], 100)

    def test_preview_shows_the_selected_provider_model_and_masked_key_for_consent(self):
        owner, repo, input_id, _ = self.prepared()
        before = input_store.load_preview(self.db, input_id, owner, now=T0)
        self.assertEqual((before.transfer.provider, before.transfer.profile_id, before.transfer.credential_configured,
                          before.transfer.key_hint), ('anthropic', OPUS, False, None))
        self.assertEqual(before.provider_profile, OPUS)
        self.register(owner, KEY_A, preferred_profile_id=SONNET)
        after = input_store.load_preview(self.db, input_id, owner, now=T0)
        self.assertEqual((after.transfer.profile_id, after.transfer.model, after.transfer.credential_configured,
                          after.transfer.credential_version, after.transfer.key_hint),
                         (SONNET, 'claude-sonnet-5-5', True, 1, 'sk-ant-…1111'))
        self.assertEqual(after.token_estimate.profile, SONNET)                             # 고른 모델 기준으로 추정
        self.assertNotEqual(after.token_estimate.estimated_max_cost, before.token_estimate.estimated_max_cost)
        self.assertNotIn(KEY_A, after.model_dump_json())
        with S.ai_env(AI_ALLOW_EXTERNAL_TRANSFER=1):
            self.assertTrue(input_store.load_preview(self.db, input_id, owner, now=T0).transfer.external_transfer_allowed)


class Usage(Base):
    def claimed(self, owner, repo, input_id, fingerprint='f'):
        job = self.job(owner, repo, input_id, fingerprint=canonical_hash(fingerprint))
        return job, job_service.claim_job(self.db, job.id, now=T0 + timedelta(minutes=2))

    def budgets(self):
        self.db.rollback()
        return {r.scope: (r.reserved_amount, r.spent_amount, r.amount_limit)
                for r in self.db.execute(text('SELECT scope, reserved_amount, spent_amount, amount_limit FROM usage_budgets')).all()}

    def test_user_key_usage_is_recorded_against_the_personal_budget_without_touching_the_server_budget(self):
        from decimal import Decimal
        owner, repo, input_id, _ = self.prepared()
        self.register(owner)
        with S.ai_env(AI_USER_MONTHLY_BUDGET=10, AI_SERVER_MONTHLY_BUDGET=0):
            job, lease = self.claimed(owner, repo, input_id)
            ids = usage_service.reserve_attempt(self.db, lease, Decimal('2'), now=T0 + timedelta(minutes=2))
            self.db.commit()
            self.assertEqual(len(ids), 1)                                                   # 개인 예산만
            self.assertEqual(self.budgets(), {'user': (Decimal('2'), 0, Decimal('10'))})
            usage_service.settle_attempt(self.db, job.id, 1, Decimal('1.25'), input_tokens=100, output_tokens=20)
            self.db.commit()
            self.assertEqual(self.budgets(), {'user': (0, Decimal('1.25'), Decimal('10'))})
            attempt = self.db.execute(text('SELECT input_tokens, output_tokens, estimated_cost FROM ai_attempts WHERE job_id = :j'), {'j': job.id}).one()
            self.assertEqual(tuple(attempt), (100, 20, Decimal('1.25')))                    # 비용·토큰 기록은 그대로 남는다

    def test_personal_limit_still_applies_and_the_shared_server_cap_is_optional(self):
        from decimal import Decimal
        owner, repo, input_id, _ = self.prepared()
        self.register(owner)
        with S.ai_env(AI_USER_MONTHLY_BUDGET=1, AI_SERVER_MONTHLY_BUDGET=0):
            job, lease = self.claimed(owner, repo, input_id)
            with self.assertRaises(AIError) as ctx:
                usage_service.reserve_attempt(self.db, lease, Decimal('1.5'), now=T0 + timedelta(minutes=2))
            self.assertEqual((ctx.exception.code, ctx.exception.details), (ErrorCode.BUDGET_LIMIT, {'scope': 'user'}))
            self.db.rollback()
        # 관리자가 서버 합계 상한을 켜면(0보다 큼) 사용자 key 사용량도 그 상한에 포함된다
        with S.ai_env(AI_USER_MONTHLY_BUDGET=10, AI_SERVER_MONTHLY_BUDGET=1):
            with self.assertRaises(AIError) as ctx:
                usage_service.reserve_attempt(self.db, lease, Decimal('1.5'), now=T0 + timedelta(minutes=2))
            self.assertEqual(ctx.exception.details, {'scope': 'server'})
            self.db.rollback()
            usage_service.reserve_attempt(self.db, lease, Decimal('0.5'), now=T0 + timedelta(minutes=2))
            self.db.commit()
            self.assertEqual(set(self.budgets()), {'user', 'server'})

    def test_request_limits_still_apply_with_user_keys(self):
        owner, repo, input_id, _ = self.prepared()
        self.register(owner)
        with S.ai_env(AI_DAILY_JOB_LIMIT=2):
            self.job(owner, repo, input_id, fingerprint=canonical_hash('1'))
            self.job(owner, repo, input_id, fingerprint=canonical_hash('2'))
            with self.assertRaises(AIError) as ctx:
                self.job(owner, repo, input_id, fingerprint=canonical_hash('3'))
            self.assertEqual(ctx.exception.code, ErrorCode.USER_LIMIT)

    def test_a_job_without_a_usable_key_cannot_call_the_provider(self):
        owner, repo, input_id, _ = self.prepared()
        self.register(owner)
        job = self.job(owner, repo, input_id)
        credentials.delete_credential(self.db, owner, 'anthropic')
        with self.assertRaises(credentials.CredentialUnavailable):
            credentials.provider_for_job(self.db, job.id)
        # 고정한 profile이 바뀌었다면(가격·한도·모델) 같은 동의로 보낼 수 없다
        owner2, repo2, input2, _ = self.prepared()
        self.register(owner2, KEY_B)
        job2 = self.job(owner2, repo2, input2)
        import dataclasses
        changed = dataclasses.replace(registry.get_profile(OPUS), output_price_per_mtok=registry.get_profile(OPUS).output_price_per_mtok + 1)
        with patch.dict(registry.PROFILES, {OPUS: changed}):
            with self.assertRaises(credentials.CredentialUnavailable) as ctx:
                credentials.provider_for_job(self.db, job2.id)
        self.assertEqual(ctx.exception.reason, 'profile_changed')
        self.assertIsInstance(credentials.provider_for_job(self.db, job2.id), AnthropicProvider)
        self.assertEqual(credentials.open_secret_for_job(self.db, job2.id), KEY_B)

    def test_prepare_jobs_need_no_credential_and_the_runner_still_works(self):
        owner, repo = self.make_user(), self.make_repo()
        result = self.submit(owner, repo)
        row = self.job_row(result.job.id)
        self.assertEqual((row['kind'], row['credential_version_id'], row['provider'], row['model']), ('prepare', None, None, None))
        self.assertEqual(self.run_prepare(result.job.id), 'succeeded')                    # 입력 준비는 key 없이 동작한다


if __name__ == '__main__':
    unittest.main(verbosity=2)
