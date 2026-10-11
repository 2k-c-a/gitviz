"""Cloud REST and same-PC Ollama contracts; all HTTP uses MockTransport."""
import dataclasses
import json
import os
import sys
import traceback
import unittest
from decimal import Decimal
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx2
from app.services.ai.providers import registry
from app.services.ai.providers.base import GenerationRequest, ProviderError, TokenLimitExceeded
from app.services.ai.providers.json_provider import (
    OpenAIProvider, GeminiProvider, OllamaProvider, ProviderConfigError, local_endpoint, strict_schema,
)
from app.services.ai.credentials import normalize_key

TEXT = json.dumps({'schema_version': 'review-output-v1', 'findings': []})
DIGEST = 'a' * 64
REQUEST = GenerationRequest('review', 'SYSTEM_SECRET_CODE', 'USER_SECRET_CODE')
KEY = 'sk-project-SECRET0123456789abcdef'


def payload(provider):
    if provider == 'openai':
        return {'id': 'resp1', 'status': 'completed', 'output': [{'type': 'message',
                'content': [{'type': 'output_text', 'text': TEXT}]}],
                'usage': {'input_tokens': 10, 'output_tokens': 20}}
    if provider == 'gemini':
        return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': TEXT}]}}],
                'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 20, 'thoughtsTokenCount': 5}}
    return {'done': True, 'done_reason': 'stop', 'message': {'content': TEXT},
            'prompt_eval_count': 10, 'eval_count': 20}


class Adapters(unittest.TestCase):
    def test_local_chat_runtimes(self):
        for runtime in ('lmstudio', 'koboldcpp'):
            for reason in ('stop', 'length', 'tool_calls'):
                with self.subTest(runtime=runtime, reason=reason):
                    profile = dataclasses.replace(registry.get_profile(runtime + '-local-v1'), model='installed-model')
                    requests = []
                    def handle(req):
                        requests.append(req)
                        if req.method == 'GET':
                            return httpx2.Response(200, json={'data': [{'id': profile.model}]})
                        body = json.loads(req.content)
                        self.assertEqual(body['model'], profile.model)
                        self.assertFalse(body['stream'])
                        self.assertNotIn('tools', body)
                        self.assertEqual(body['response_format']['json_schema']['schema'], strict_schema('review', 'review-output-v2'))
                        return httpx2.Response(200, json={'choices': [{'message': {'content': TEXT}, 'finish_reason': reason}]})
                    with httpx2.Client(transport=httpx2.MockTransport(handle)) as client:
                        adapter = registry.create_provider(profile, client=client)
                        adapter.count_tokens(REQUEST)
                        self.assertEqual(requests, [])  # no code transmitted for estimation
                        result = adapter.generate(REQUEST)
                    self.assertEqual([r.url.path for r in requests], ['/v1/models', '/v1/chat/completions'])
                    self.assertEqual(result.finish, {'stop': 'complete', 'length': 'truncated', 'tool_calls': 'incomplete'}[reason])
                    self.assertEqual(result.cost, Decimal('0'))
                    self.assertIsNone(result.usage)

    def test_local_chat_rejects_missing_model_and_never_falls_back(self):
        profile = dataclasses.replace(registry.get_profile('lmstudio-local-v1'), model='installed-model')
        paths = []
        def handle(req):
            paths.append(req.url.path)
            return httpx2.Response(200, json={'data': [{'id': 'different-model'}]})
        with httpx2.Client(transport=httpx2.MockTransport(handle)) as client:
            with self.assertRaises(ProviderError):
                registry.create_provider(profile, client=client).generate(REQUEST)
        self.assertEqual(paths, ['/v1/models'])
        with self.assertRaises(ProviderConfigError):
            registry.create_provider(dataclasses.replace(profile, endpoint='http://192.168.1.2:1234'))

    def test_local_chat_runtime_settings(self):
        for runtime, prefix in (('lmstudio', 'LMSTUDIO'), ('koboldcpp', 'KOBOLDCPP')):
            with patch.dict(os.environ, {f'AI_{prefix}_ENABLED': '0'}):
                self.assertNotIn(runtime + '-local-v1', registry.allowed_profile_ids())
            with patch.dict(os.environ, {f'AI_{prefix}_ENABLED': '1', f'AI_{prefix}_MODEL': 'installed-model'}):
                profile = registry.get_profile(runtime + '-local-v1')
                self.assertEqual(profile.model, 'installed-model')
                self.assertFalse(profile.requires_api_key)
                self.assertEqual(profile.execution_location, 'local')
                with patch.dict(os.environ, {f'AI_{prefix}_MODEL': 'other-model'}):
                    self.assertNotEqual(profile.fingerprint(), registry.get_profile(profile.id).fingerprint())

    def adapter(self, name, handler=None):
        self.requests = []
        profile = registry.get_profile({'openai': 'gpt-5.4-v1', 'gemini': 'gemini-3.5-flash-lite-v1',
                                         'ollama': 'ollama-local-v1'}[name])
        if name == 'ollama':
            profile = dataclasses.replace(profile, model_digest=DIGEST)
        def handle(req):
            self.requests.append(req)
            if handler:
                return handler(req)
            if req.url.path == '/api/tags':
                return httpx2.Response(200, json={'models': [{'name': profile.model, 'digest': DIGEST}]})
            if req.url.path == '/api/show' or req.method == 'GET':
                return httpx2.Response(200, json={})
            if req.url.path.endswith('input_tokens'):
                return httpx2.Response(200, json={'input_tokens': 50})
            if req.url.path.endswith(':countTokens'):
                return httpx2.Response(200, json={'totalTokens': 50})
            return httpx2.Response(200, json=payload(name))
        client = httpx2.Client(transport=httpx2.MockTransport(handle), trust_env=False, follow_redirects=False)
        self.addCleanup(client.close)
        return registry.create_provider(profile, api_key=None if name == 'ollama' else KEY, client=client)

    def test_all_adapters_generate_strict_json_without_tools(self):
        for name in ('openai', 'gemini', 'ollama'):
            with self.subTest(name=name):
                provider = self.adapter(name)
                response = provider.generate(REQUEST)
                self.assertTrue(response.usable)
                self.assertEqual(response.parsed['findings'], [])
                body = json.loads(self.requests[-1].content)
                self.assertNotIn('tools', body)
                self.assertNotIn('fallbacks', body)
                self.assertNotIn(KEY, self.requests[-1].url.query.decode())
                if name == 'ollama':
                    self.assertNotIn('authorization', self.requests[-1].headers)
                    self.assertEqual(response.cost, Decimal(0))
                    self.assertFalse(body['stream'])
                elif name == 'gemini':
                    self.assertEqual(response.usage.output_tokens, 25)
                else:
                    self.assertFalse(body['store'])

    def test_schema_in_count_and_generation_matches(self):
        for name in ('openai', 'gemini'):
            for kind in ('summary', 'review'):
                with self.subTest(name=name, kind=kind):
                    provider = self.adapter(name)
                    req = dataclasses.replace(REQUEST, kind=kind)
                    self.assertEqual(provider.count_tokens(req), 50)
                    provider.generate(req)
                    count, generation = [json.loads(r.content) for r in self.requests]
                    if name == 'openai':
                        self.assertEqual(count['text'], generation['text'])
                    else:
                        self.assertEqual(count['generateContentRequest']['generationConfig']['responseJsonSchema'],
                                         generation['generationConfig']['responseJsonSchema'])

    def test_strict_schema_requires_every_object_property(self):
        def check(node):
            if isinstance(node, dict):
                if node.get('type') == 'object':
                    self.assertFalse(node['additionalProperties'])
                    self.assertEqual(set(node['required']), set(node.get('properties', {})))
                for val in node.values(): check(val)
            elif isinstance(node, list):
                for val in node: check(val)
        for kind in ('summary', 'review'): check(strict_schema(kind))

    def test_fixed_cloud_endpoints_ignore_environment(self):
        with patch.dict(os.environ, {'OPENAI_BASE_URL': 'https://evil.invalid',
                    'GOOGLE_GEMINI_BASE_URL': 'https://evil.invalid', 'HTTP_PROXY': 'https://evil.invalid'}):
            for name, host in [('openai', 'api.openai.com'), ('gemini', 'generativelanguage.googleapis.com')]:
                provider = self.adapter(name)
                provider.verify_credential()
                self.assertEqual(self.requests[0].url.host, host)

    def test_redirects_are_rejected_not_followed(self):
        for name in ('openai', 'gemini'):
            provider = self.adapter(name, lambda r: httpx2.Response(307, headers={'location': 'https://evil.invalid'}))
            with self.assertRaises(ProviderError): provider.generate(REQUEST)
            self.assertEqual(len(self.requests), 1)

    def test_retry_and_uncertainty_without_second_http_request(self):
        for name in ('openai', 'gemini'):
            for status, retry, uncertain in [(429, True, False), (401, False, False),
                            (403, False, False), (404, False, False), (400, False, False),
                            (408, False, True), (500, False, True), (504, False, True)]:
                with self.subTest(name=name, status=status):
                    provider = self.adapter(name, lambda r: httpx2.Response(status, text=KEY + REQUEST.user))
                    with self.assertRaises(ProviderError) as caught: provider.generate(REQUEST)
                    self.assertEqual(caught.exception.failure.retryable, retry)
                    self.assertEqual(caught.exception.failure.outcome_uncertain, uncertain)
                    self.assertEqual(len(self.requests), 1)
                    self.assertNotIn(KEY, str(caught.exception))

    def test_timeout_and_connect_failure(self):
        for name in ('openai', 'gemini'):
            for cls, uncertain in [(httpx2.ReadTimeout, True), (httpx2.ConnectError, False)]:
                def fail(r): raise cls(KEY + REQUEST.user)
                provider = self.adapter(name, fail)
                try: provider.generate(REQUEST)
                except ProviderError as exc:
                    self.assertEqual(exc.failure.outcome_uncertain, uncertain)
                    rendered = traceback.format_exc()
                    self.assertNotIn(KEY, rendered)
                    self.assertNotIn(REQUEST.user, rendered)
                else: self.fail('expected ProviderError')

    def test_bad_200_response_is_uncertain(self):
        for name in ('openai', 'gemini'):
            for body in ('invalid', [], {}, {'output': 'malformed'}):
                provider = self.adapter(name, lambda r: httpx2.Response(200, json=body))
                with self.assertRaises(ProviderError) as caught: provider.generate(REQUEST)
                self.assertTrue(caught.exception.failure.outcome_uncertain)

    def test_usage_missing_is_unknown_not_zero(self):
        for name in ('openai', 'gemini'):
            data = payload(name)
            data.pop('usage' if name == 'openai' else 'usageMetadata')
            provider = self.adapter(name, lambda r: httpx2.Response(200, json=data))
            response = provider.generate(REQUEST)
            self.assertIsNone(response.cost)
            self.assertIsNone(response.usage)

    def test_output_limits_and_input_limits_prevent_http(self):
        for name in ('openai', 'gemini', 'ollama'):
            provider = self.adapter(name)
            for limit in (0, -1, True, 4001):
                with self.assertRaises(ProviderConfigError): provider.generate(dataclasses.replace(REQUEST, max_output_tokens=limit))
            with self.assertRaises(TokenLimitExceeded): provider.generate(dataclasses.replace(REQUEST, user='x' * 50000))
            self.assertEqual(self.requests, [])

    def test_local_urls_reject_ssrf_credentials_paths_and_remote(self):
        for url in ('https://127.0.0.1:11434', 'http://evil.invalid:11434', 'http://127.0.0.1@evil.invalid:11434',
                    'http://127.0.0.1:bad', 'http://127.0.0.1:11434/evil', 'http://127.0.0.1:11434?url=evil',
                    'http://127.0.0.1:11434#evil', 'http://127.0.0.1'):
            with self.subTest(url=url), self.assertRaises(ProviderConfigError): local_endpoint(url)

    def test_local_digest_mismatch_and_remote_model_never_generate(self):
        for remote in (False, True):
            def handle(r):
                if r.url.path == '/api/tags':
                    return httpx2.Response(200, json={'models': [{'name': 'qwen2.5-coder:7b',
                        'digest': DIGEST if remote else 'b' * 64}]})
                return httpx2.Response(200, json={'remote_host': 'https://ollama.com'})
            provider = self.adapter('ollama', handle)
            with self.assertRaises(ProviderError): provider.generate(REQUEST)
            self.assertFalse(any(r.url.path == '/api/chat' for r in self.requests))

    def test_keyless_provider_and_cloud_user_key_requirement(self):
        for pid in ('gpt-5.4-v1', 'gemini-3.5-flash-lite-v1'):
            with self.assertRaises(ProviderConfigError): registry.create_provider(registry.get_profile(pid))
        self.assertEqual(normalize_key('openai', KEY), KEY)
        self.assertEqual(normalize_key('gemini', 'AIza' + 'a' * 35), 'AIza' + 'a' * 35)

    def test_local_registration_disabled_by_default_and_dynamic_fingerprint(self):
        with patch.dict(os.environ, {'AI_OLLAMA_ENABLED': '0'}):
            self.assertNotIn('ollama-local-v1', registry.allowed_profile_ids())
        with patch.dict(os.environ, {'AI_OLLAMA_ENABLED': '1', 'AI_OLLAMA_MODEL_DIGEST': DIGEST}):
            before = registry.get_profile('ollama-local-v1').fingerprint()
            with patch.dict(os.environ, {'AI_OLLAMA_MODEL_DIGEST': 'b' * 64}):
                self.assertNotEqual(before, registry.get_profile('ollama-local-v1').fingerprint())


if __name__ == '__main__':
    unittest.main(verbosity=2)
