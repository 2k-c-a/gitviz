"""PostgreSQL: multi-provider BYOK and keyless local consent -> job -> result."""
import dataclasses
import json
import os
import sys
import unittest
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID, uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_ai_api import ApiCase, job_runner_run, S
from app.services.ai import credentials, input_store
from app.services.ai.providers import registry
from app.services.ai.access import AIAction, AIError, authorize_ai
from app.schemas.ai import ErrorCode
import httpx2
from sqlalchemy import text
from test_ai_multi_provider import payload, DIGEST


def setUpModule():
    S.migrate()


class MultiProvider(ApiCase):
    def setUp(self):
        super().setUp()
        self.local_env = S.ai_env(AI_ALLOW_EXTERNAL_TRANSFER=1, AI_OLLAMA_ENABLED=1, AI_OLLAMA_MODEL='qwen2.5-coder:7b',
                                  AI_OLLAMA_MODEL_DIGEST=DIGEST)
        self.local_env.start()
        self.addCleanup(self.local_env.stop)
        self.provider_http = []
        self.on_model_check = None
        real = registry.create_provider
        def create(profile, **kwargs):
            def handle(req):
                self.provider_http.append(req)
                if req.url.path == '/api/tags':
                    return httpx2.Response(200, json={'models': [{'name': profile.model, 'digest': DIGEST}]})
                if req.url.path == '/api/show' or req.method == 'GET':
                    if req.url.path == '/api/show' and self.on_model_check:
                        self.on_model_check()
                    return httpx2.Response(200, json={})
                if req.url.path.endswith('input_tokens'):
                    return httpx2.Response(200, json={'input_tokens': 5000})
                if req.url.path.endswith(':countTokens'):
                    return httpx2.Response(200, json={'totalTokens': 5000})
                data = payload(profile.provider)
                return httpx2.Response(200, json=data)
            return real(profile, **kwargs, client=httpx2.Client(transport=httpx2.MockTransport(handle)))
        self.provider_patch = patch.object(registry, 'create_provider', side_effect=create)
        self.provider_patch.start()
        self.addCleanup(self.provider_patch.stop)

    def local_consent(self, w):
        with S.Session() as db:
            preview = input_store.load_preview(db, w.input_id, w.owner, profile_id='ollama-local-v1')
        t = preview.transfer
        return {'input_id': str(w.input_id), 'manifest_hash': preview.manifest_hash, 'provider': t.provider,
                'profile_id': t.profile_id, 'credential_version': None, 'policy_version': preview.policy_version,
                'profile_fingerprint': t.profile_fingerprint}

    def test_local_generation_without_key_or_external_transfer(self):
        w = self.ready()
        credentials.delete_credential(self.db, w.owner, 'anthropic')
        with S.ai_env(AI_ALLOW_EXTERNAL_TRANSFER=0):
            body = self.local_consent(w)
            response = self.post_job(w, consent=body, key='local-idempotent')
            self.assertEqual(response.status_code, 202, response.text)
            job_id = UUID(response.json()['job_id'])
            row = self.job_row(job_id)
            self.assertIsNone(row['credential_version_id'])
            self.assertEqual(row['provider'], 'ollama')
            self.assertEqual(job_runner_run(self, job_id), 'succeeded')
            self.assertEqual(self.post_job(w, consent=body, key='local-idempotent').status_code, 200)
            self.assertEqual(len([r for r in self.provider_http if r.url.path == '/api/chat']), 1)
            self.assertEqual(self.rows('SELECT spent_amount FROM usage_budgets')[0]['spent_amount'], Decimal(0))
            self.assertEqual(self.reservations(), ['settled'])
            self.assertIn(('authorize', 'local_generate'), self.events)

    def test_cloud_byok_generation_separates_providers(self):
        for provider, pid, key in [('openai', 'gpt-5.4-v1', 'sk-proj-' + 'a' * 40),
                                 ('gemini', 'gemini-3.5-flash-lite-v1', 'AIza' + 'b' * 35)]:
            with self.subTest(provider=provider):
                w = self.ready(pr_number=7 if provider == 'openai' else 8)
                result = credentials.register_key(self.db, w.owner, provider, key,
                            preferred_profile_id=pid, verify=False)
                with S.Session() as db:
                    preview = input_store.load_preview(db, w.input_id, w.owner, profile_id=pid)
                self.assertEqual(preview.transfer.provider, provider)
                body = self.consent(w, provider=provider, profile_id=pid,
                            credential_version=result.credential.version)
                response = self.post_job(w, consent=body)
                self.assertEqual(response.status_code, 202, response.text)
                job_id = UUID(response.json()['job_id'])
                self.assertEqual(job_runner_run(self, job_id), 'succeeded')
                self.assertEqual(self.job_row(job_id)['provider'], provider)

    def test_local_consent_rejects_digest_change_missing_pin_and_disabled(self):
        w = self.ready()
        body = self.local_consent(w)
        with patch.dict(os.environ, {'AI_OLLAMA_MODEL_DIGEST': 'b' * 64}):
            self.error(self.post_job(w, consent=body), 409, 'PREVIEW_CHANGED')
        missing = dict(body)
        missing.pop('profile_fingerprint')
        self.error(self.post_job(w, consent=missing), 409, 'PREVIEW_CHANGED')
        with patch.dict(os.environ, {'AI_OLLAMA_ENABLED': '0'}):
            self.error(self.post_job(w, consent=body), 409, 'PREVIEW_CHANGED')
        self.assertFalse(self.provider_http)

    def test_local_profile_changed_before_worker_never_calls_model(self):
        w = self.ready()
        response = self.post_job(w, consent=self.local_consent(w))
        job_id = UUID(response.json()['job_id'])
        with patch.dict(os.environ, {'AI_OLLAMA_MODEL_DIGEST': 'b' * 64}):
            self.assertEqual(job_runner_run(self, job_id), 'failed')
        self.assertEqual(self.job_row(job_id)['error_code'], 'PROFILE_CHANGED')
        self.assertFalse(self.provider_http)

    def test_local_access_keeps_owner_and_policy_checks_private_repo_allowed(self):
        w = self.ready()
        with S.ai_env(AI_ALLOW_EXTERNAL_TRANSFER=0), patch('app.services.ai.access.verify_repository_access',
                                                return_value=(None, {'private': True})):
            authorize_ai(self.db, w.owner, w.repo, AIAction.local_generate, 'code-transfer-v1')
            with self.assertRaises(AIError):
                authorize_ai(self.db, w.owner, w.repo, AIAction.generate, 'code-transfer-v1')
            with self.assertRaises(AIError):
                authorize_ai(self.db, w.owner, w.repo, AIAction.local_generate, 'unknown-policy')
        body = self.local_consent(w)
        self.login(self.make_user())
        self.error(self.post_job(w, consent=body), 404, 'RESOURCE_NOT_FOUND')

    def test_cloud_null_key_version_does_not_bypass_byok(self):
        w = self.ready()
        self.error(self.post_job(w, consent=self.consent(w, credential_version=None)), 409, 'PREVIEW_CHANGED')

    def test_local_model_check_finishes_before_last_cancel_check(self):
        w = self.ready()
        response = self.post_job(w, consent=self.local_consent(w))
        job_id = UUID(response.json()['job_id'])
        self.on_model_check = lambda: input_store.delete_input(self.db, w.input_id, w.owner)
        self.assertEqual(job_runner_run(self, job_id), 'cancelled')
        self.assertFalse(any(r.url.path == '/api/chat' for r in self.provider_http))
        self.assertEqual(self.reservations(), [])

    def test_exhausted_cloud_budget_does_not_block_zero_fee_local_execution(self):
        w = self.ready()
        self.db.execute(text("""INSERT INTO usage_budgets(scope, owner_user_id, period_start, currency,
                             amount_limit, spent_amount) VALUES('user', :o, date_trunc('month', now())::date,
                             'USD', 20, 21)"""), {'o': w.owner})
        self.db.commit()
        response = self.post_job(w, consent=self.local_consent(w))
        self.assertEqual(response.status_code, 202, response.text)
        job_id = UUID(response.json()['job_id'])
        self.assertEqual(job_runner_run(self, job_id), 'succeeded')
        self.assertEqual(self.rows('SELECT spent_amount FROM usage_budgets')[0]['spent_amount'], Decimal(21))
        self.assertEqual(self.reservations(), ['settled'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
