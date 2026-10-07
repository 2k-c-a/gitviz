"""S6 provider adapter 테스트. 외부 송신 없이 HTTP 계층을 mock한다(httpx2.MockTransport).

실제 모델 응답의 의미 정확도는 이 테스트의 범위가 아니다(S10에서 별도 평가). 여기서는 요청 모양, 호출 횟수,
실패 분류(안전한 재시도 vs 응답 유실·과금 불명), 한도 검사, 사용량·비용, 비밀 비노출을 검증한다.
"""
import dataclasses
import json
import logging
import os
import sys
import traceback
import unittest
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402
sys.path.insert(0, str(support.ROOT))

import anthropic  # noqa: E402
import httpx2  # noqa: E402

from app.core import ai_settings  # noqa: E402
from app.schemas.ai import ChangeType, DiffLine, DiffLineKind, PRTransferMetadata  # noqa: E402
from app.services.ai import prompts, token_budget  # noqa: E402
from app.services.ai.providers import registry  # noqa: E402
from app.services.ai.providers.anthropic_provider import AnthropicProvider, ProviderConfigError  # noqa: E402
from app.services.ai.providers.base import (GenerationRequest, ProviderError, ProviderFailure,  # noqa: E402
                                            TokenLimitExceeded, Usage, parse_json_object)

PROFILE = registry.get_profile('claude-opus-5-5-v1')
API_KEY = 'sk-ant-test-SUPERSECRETKEY'
CODE_MARKER = 'UNIQUE_CODE_MARKER_8f3a'
VALID_TEXT = json.dumps({'schema_version': 'review-output-v1', 'findings': []})


def sent_input(extra_line=CODE_MARKER):
    lines = [DiffLine(kind=DiffLineKind.context, left_line=1, right_line=1, text='def f():'),
             DiffLine(kind=DiffLineKind.added, right_line=2, text=f'    return "{extra_line}"')]
    f = prompts.SentFile(handle='F1', file_id=UUID(int=1), path='a.py', old_path=None, change_type=ChangeType.modified,
                         side_availability='BOTH', language='python', lines=lines)
    return prompts.SentInput(files=[f], metadata=PRTransferMetadata(title='t', body='b'))


def request(kind='review', **kw):
    rendered = prompts.render_request(kind, sent_input())
    return GenerationRequest(kind=kind, system=rendered.system, user=rendered.user, **kw)


def message_json(text=VALID_TEXT, stop='end_turn', usage='default', content=None, **extra):
    body = {'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': 'claude-opus-5-5',
            'content': content if content is not None else [{'type': 'text', 'text': text}],
            'stop_reason': stop, 'stop_sequence': None}
    if usage == 'default':
        usage = {'input_tokens': 100, 'output_tokens': 50}
    if usage is not None:
        body['usage'] = usage
    body.update(extra)
    return body


def error_json(kind='api_error'):
    return {'type': 'error', 'error': {'type': kind, 'message': 'upstream said something'}}


class Recorder:
    """MockTransport handler: 요청을 기록하고 준비한 응답(또는 예외)을 돌려준다."""

    def __init__(self, responder):
        self.responder = responder
        self.requests = []

    def __call__(self, req: httpx2.Request) -> httpx2.Response:
        self.requests.append(req)
        result = self.responder(req) if callable(self.responder) else self.responder
        if isinstance(result, BaseException):
            raise result
        return result

    @property
    def calls(self):
        return len(self.requests)


def make_provider(responder, profile=PROFILE):
    recorder = Recorder(responder)
    client = anthropic.Anthropic(api_key=API_KEY, max_retries=0, timeout=profile.timeout_seconds,
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(recorder)))
    return AnthropicProvider(profile, client=client), recorder


def ok(body=None, headers=None, status=200):
    return httpx2.Response(status, json=body if body is not None else message_json(),
                           headers={'request-id': 'req_abc', **(headers or {})})


class RequestShape(unittest.TestCase):
    def test_exactly_the_documented_request_and_nothing_else(self):
        provider, rec = make_provider(lambda r: ok())
        provider.generate(request())
        self.assertEqual(rec.calls, 1)
        req = rec.requests[0]
        self.assertEqual((req.method, req.url.path), ('POST', '/v1/messages'))
        self.assertEqual(req.headers['x-api-key'], API_KEY)
        self.assertIn('anthropic-version', req.headers)
        self.assertNotIn('anthropic-beta', req.headers)                       # beta 기능을 쓰지 않는다
        body = json.loads(req.content)
        # 허용 필드 목록과 정확히 일치: tools·tool_choice·fallbacks·thinking·sampling·stream 없음
        self.assertEqual(set(body), {'model', 'max_tokens', 'system', 'messages', 'output_config'})
        self.assertEqual(body['model'], PROFILE.model)
        self.assertEqual(body['max_tokens'], PROFILE.max_output_tokens)
        self.assertEqual([m['role'] for m in body['messages']], ['user'])
        self.assertEqual(body['output_config']['effort'], PROFILE.effort)
        self.assertEqual(body['output_config']['format']['type'], 'json_schema')
        self.assertEqual(body['output_config']['format']['schema'], prompts.output_schema('review'))
        for forbidden in ('tools', 'tool_choice', 'fallbacks', 'thinking', 'temperature', 'top_p', 'top_k', 'stream',
                          'betas', 'metadata', 'stop_sequences'):
            self.assertNotIn(forbidden, body)

    def test_untrusted_code_is_only_in_the_user_message_inside_boundary_blocks(self):
        provider, rec = make_provider(lambda r: ok())
        provider.generate(request())
        body = json.loads(rec.requests[0].content)
        self.assertNotIn(CODE_MARKER, body['system'])
        user = body['messages'][0]['content']
        self.assertIn(CODE_MARKER, user)
        boundary = body['system'].split('`<<<')[1].split(':BEGIN')[0] if '`<<<' in body['system'] else None
        self.assertTrue(boundary and boundary.startswith('B'))
        self.assertIn(f'<<<{boundary}:BEGIN file handle=F1', user)
        self.assertTrue(user.index(CODE_MARKER) > user.index(f'<<<{boundary}:BEGIN file'))
        self.assertTrue(user.index(CODE_MARKER) < user.rindex(f'<<<{boundary}:END>>>'))

    def test_model_and_limits_come_only_from_the_profile(self):
        other = dataclasses.replace(PROFILE, id='test-profile-v2', model='some-other-model', max_output_tokens=1234)
        provider, rec = make_provider(lambda r: ok(), other)
        provider.generate(request())
        body = json.loads(rec.requests[0].content)
        self.assertEqual((body['model'], body['max_tokens']), ('some-other-model', 1234))
        provider.generate(request(max_output_tokens=500))
        self.assertEqual(json.loads(rec.requests[1].content)['max_tokens'], 500)
        for bad in (0, 1235, -1):
            with self.assertRaises(ValueError):
                provider.generate(request(max_output_tokens=bad))
        self.assertEqual(rec.calls, 2)

    def test_summary_requests_use_the_summary_schema_and_unknown_kinds_are_rejected(self):
        provider, rec = make_provider(lambda r: ok())
        provider.generate(request('summary'))
        self.assertEqual(json.loads(rec.requests[0].content)['output_config']['format']['schema'],
                         prompts.output_schema('summary'))
        with self.assertRaises(ValueError):
            provider.generate(GenerationRequest(kind='prepare', system='s', user='u'))

    def test_client_is_configured_for_single_attempts_and_the_profile_timeout(self):
        provider = AnthropicProvider(PROFILE, api_key=API_KEY)                    # 사용자 key를 주입한다
        self.assertEqual(provider.client.max_retries, 0)                           # 재시도는 job 계층이 결정한다
        self.assertEqual(provider.client.timeout, 90.0)
        # 서버 환경변수의 공용 key로는 만들 수 없다(주입하지 않으면 거부)
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': API_KEY}):
            with self.assertRaises(ProviderConfigError):
                AnthropicProvider(PROFILE)
        for empty in (None, ''):
            with self.assertRaises(ProviderConfigError):
                AnthropicProvider(PROFILE, api_key=empty)
        with self.assertRaises(ProviderConfigError):
            AnthropicProvider(dataclasses.replace(PROFILE, provider='other'), api_key=API_KEY)


class Responses(unittest.TestCase):
    def test_success_fields_usage_and_cost(self):
        provider, _ = make_provider(lambda r: ok())
        response = provider.generate(request())
        self.assertEqual((response.request_id, response.finish, response.model), ('req_abc', 'complete', 'claude-opus-5-5'))
        self.assertEqual(response.parsed, {'schema_version': 'review-output-v1', 'findings': []})
        self.assertEqual(response.usage, Usage(100, 50))
        self.assertEqual(response.cost, Decimal('0.001400'))        # 100*$4/M + 50*$20/M
        self.assertTrue(response.usable and response.usage_reported)
        self.assertGreaterEqual(response.latency_seconds, 0)

    def test_thinking_blocks_are_ignored_and_cache_tokens_are_billed_conservatively(self):
        body = message_json(content=[{'type': 'thinking', 'thinking': '', 'signature': 'x'},
                                     {'type': 'text', 'text': VALID_TEXT}],
                            usage={'input_tokens': 10, 'output_tokens': 5, 'cache_creation_input_tokens': 20,
                                   'cache_read_input_tokens': 30})
        response = make_provider(lambda r: ok(body))[0].generate(request())
        self.assertEqual((response.finish, response.usage.billed_input_tokens), ('complete', 60))
        self.assertEqual(response.cost, token_budget.cost_of(PROFILE, 60, 5))

    def test_usable_output_requires_a_single_json_object(self):
        cases = {
            'prose': ('Here is the review: ' + VALID_TEXT, 'invalid_json'),
            'fenced': ('```json\n' + VALID_TEXT + '\n```', 'invalid_json'),
            'trailing': (VALID_TEXT + ' thanks', 'invalid_json'),
            'array': ('[1, 2]', 'not_json_object'),
            'string': ('"hi"', 'not_json_object'),
            'nan': ('{"a": NaN}', 'invalid_json'),
            'truncated_json': (VALID_TEXT[:-3], 'invalid_json'),
            'huge': (json.dumps({'x': 'a' * 70_000}), 'output_too_large'),
        }
        for name, (text, error) in cases.items():
            with self.subTest(name):
                response = make_provider(lambda r: ok(message_json(text)))[0].generate(request())
                self.assertEqual((response.finish, response.parsed, response.parse_error), ('complete', None, error))
                self.assertFalse(response.usable)
                self.assertEqual(response.cost, Decimal('0.001400'))                # 호출은 끝났으므로 과금된다

    def test_stop_reasons(self):
        truncated = make_provider(lambda r: ok(message_json('{"a":', stop='max_tokens')))[0].generate(request())
        self.assertEqual((truncated.finish, truncated.parsed, truncated.cost), ('truncated', None, Decimal('0.001400')))
        refused = make_provider(lambda r: ok(message_json('', stop='refusal', stop_details={
            'type': 'refusal', 'category': 'cyber', 'explanation': 'x'})))[0].generate(request())
        self.assertEqual((refused.finish, refused.refusal_category, refused.usable), ('refused', 'cyber', False))
        empty = make_provider(lambda r: ok(message_json('   ')))[0].generate(request())
        self.assertEqual(empty.finish, 'empty')
        other = make_provider(lambda r: ok(message_json(stop='pause_turn')))[0].generate(request())
        self.assertEqual(other.finish, 'incomplete')
        tool = message_json(content=[{'type': 'tool_use', 'id': 't1', 'name': 'x', 'input': {}}], stop='tool_use')
        self.assertEqual(make_provider(lambda r: ok(tool))[0].generate(request()).finish, 'incomplete')

    def test_missing_or_invalid_usage_means_unknown_cost_not_zero(self):
        for usage in (None, {}, {'input_tokens': 10}, {'input_tokens': None, 'output_tokens': 5},
                      {'input_tokens': -1, 'output_tokens': 5}, {'input_tokens': True, 'output_tokens': 5},
                      {'input_tokens': '10', 'output_tokens': 5}):
            with self.subTest(usage=usage):
                try:
                    response = make_provider(lambda r: ok(message_json(usage=usage)))[0].generate(request())
                except ProviderError as err:       # SDK가 응답 형식 자체를 거부하는 경우도 비용을 모르는 불명 결과다
                    self.assertTrue(err.failure.outcome_uncertain)
                    continue
                self.assertIsNone(response.usage)
                self.assertIsNone(response.cost)
                self.assertFalse(response.usage_reported)
                self.assertTrue(response.usable)                    # 출력은 쓸 수 있지만 비용은 추정액으로 정산해야 한다

    def test_garbage_200_body_is_a_lost_response_not_a_success(self):
        provider, rec = make_provider(lambda r: httpx2.Response(200, content=b'<html>gateway</html>',
                                                                headers={'content-type': 'text/html'}))
        with self.assertRaises(ProviderError) as ctx:
            provider.generate(request())
        self.assertTrue(ctx.exception.failure.outcome_uncertain)
        self.assertFalse(ctx.exception.failure.retryable)
        self.assertEqual(rec.calls, 1)
        for body in ({'unexpected': 1}, {}, {'type': 'message', 'role': 'user', 'content': []},
                     {'type': 'message', 'role': 'assistant', 'content': None}):       # 모양이 깨진 JSON 응답도 같다
            provider, rec = make_provider(lambda r, b=body: httpx2.Response(200, json=b))
            with self.assertRaises(ProviderError) as ctx:
                provider.generate(request())
            self.assertEqual((ctx.exception.failure.kind, ctx.exception.failure.outcome_uncertain,
                              ctx.exception.failure.retryable), ('response_lost', True, False), body)


class FailureClassification(unittest.TestCase):
    def fail(self, responder, **kw):
        provider, rec = make_provider(responder)
        with self.assertRaises(ProviderError) as ctx:
            provider.generate(request(**kw))
        self.assertEqual(rec.calls, 1, '실패해도 HTTP 요청은 정확히 한 번이어야 한다(숨은 자동 재시도 금지)')
        return ctx.exception.failure

    def test_rate_limit_is_safe_to_retry_and_carries_retry_after(self):
        f = self.fail(lambda r: httpx2.Response(429, json=error_json('rate_limit_error'), headers={'retry-after': '17'}))
        self.assertEqual((f.kind, f.retryable, f.outcome_uncertain, f.retry_after, f.http_status),
                         ('rate_limited', True, False, 17, 429))
        self.assertEqual(f.error_code, 'PROVIDER_RATE_LIMITED')
        for header, expected in (({}, None), ({'retry-after': 'soon'}, None), ({'retry-after': '99999'}, 3600),
                                 ({'retry-after': '0'}, 1)):
            self.assertEqual(self.fail(lambda r: httpx2.Response(429, json=error_json(), headers=header)).retry_after,
                             expected)

    def test_provider_server_errors_with_a_request_id_are_safe_to_retry(self):
        for status in (500, 502, 503, 529):
            with self.subTest(status=status):
                f = self.fail(lambda r: httpx2.Response(status, json=error_json(), headers={'request-id': 'req_abc'}))
                self.assertEqual((f.kind, f.retryable, f.outcome_uncertain, f.request_id), ('server_error', True, False, 'req_abc'))
                self.assertEqual(f.error_code, 'PROVIDER_UNAVAILABLE')

    def test_5xx_of_unknown_origin_may_have_been_processed(self):
        """request-id가 없는 5xx(프록시·게이트웨이·로드밸런서)는 제공자가 처리한 뒤 만든 응답일 수 있다."""
        bodies = (lambda s: httpx2.Response(s, text='<html>Bad Gateway</html>'),
                  lambda s: httpx2.Response(s, json=error_json()),                 # 본문이 제공자 형식이어도 헤더가 없으면 확신할 수 없다
                  lambda s: httpx2.Response(s))
        for status in (500, 502, 503, 529):
            for make in bodies:
                with self.subTest(status=status):
                    f = self.fail(lambda r: make(status))
                    self.assertEqual((f.kind, f.retryable, f.outcome_uncertain), ('response_lost', False, True))
                    self.assertEqual(f.error_code, 'PROVIDER_TIMEOUT')

    def test_gateway_timeouts_may_have_been_processed(self):
        for status in (408, 504):
            with self.subTest(status=status):
                f = self.fail(lambda r: httpx2.Response(status, text='timeout'))
                self.assertEqual((f.kind, f.retryable, f.outcome_uncertain), ('response_lost', False, True))
                self.assertEqual(f.error_code, 'PROVIDER_TIMEOUT')

    def test_permanent_rejections_are_never_retried(self):
        cases = {400: 'request_rejected', 401: 'auth_failed', 402: 'billing_error', 403: 'auth_failed',
                 404: 'model_unavailable', 413: 'input_too_large', 418: 'request_rejected', 422: 'request_rejected'}
        for status, kind in cases.items():
            with self.subTest(status=status):
                f = self.fail(lambda r: httpx2.Response(status, json=error_json('invalid_request_error')))
                self.assertEqual((f.kind, f.retryable, f.outcome_uncertain, f.http_status), (kind, False, False, status))
        self.assertEqual(self.fail(lambda r: httpx2.Response(401, json=error_json())).error_code, 'PROVIDER_AUTH')
        self.assertEqual(self.fail(lambda r: httpx2.Response(413, json=error_json())).error_code, 'INPUT_TOO_LARGE')

    def test_failures_before_the_request_reaches_the_server_are_safe(self):
        for exc in (httpx2.ConnectError('refused'), httpx2.ConnectTimeout('slow'), httpx2.PoolTimeout('busy')):
            with self.subTest(exc=type(exc).__name__):
                f = self.fail(lambda r, e=exc: e)
                self.assertEqual((f.kind, f.retryable, f.outcome_uncertain), ('connection_failed', True, False))

    def test_waiting_for_the_response_that_never_comes_is_uncertain_and_not_auto_retried(self):
        for exc in (httpx2.ReadTimeout('no response'), httpx2.WriteTimeout('stalled')):
            with self.subTest(exc=type(exc).__name__):
                f = self.fail(lambda r, e=exc: e)
                self.assertEqual((f.kind, f.retryable, f.outcome_uncertain), ('timeout', False, True))
        for exc in (httpx2.RemoteProtocolError('peer closed'), httpx2.ReadError('reset'), RuntimeError('boom')):
            with self.subTest(exc=type(exc).__name__):
                f = self.fail(lambda r, e=exc: e)
                self.assertEqual((f.kind, f.retryable, f.outcome_uncertain), ('response_lost', False, True))

    def test_unknown_exceptions_are_treated_as_uncertain(self):
        provider, _ = make_provider(lambda r: ok())
        f = provider.classify_error(ValueError('???'))
        self.assertEqual((f.kind, f.retryable, f.outcome_uncertain), ('unknown_error', False, True))

    def test_over_limit_input_is_rejected_before_any_http_call(self):
        provider, rec = make_provider(lambda r: ok())
        huge = GenerationRequest(kind='review', system='s', user='x' * 200_000)
        with self.assertRaises(ProviderError) as ctx:
            provider.generate(huge)
        self.assertEqual((ctx.exception.failure.kind, ctx.exception.failure.outcome_uncertain,
                          ctx.exception.failure.retryable), ('input_too_large', False, False))
        self.assertEqual(rec.calls, 0)
        small_context = dataclasses.replace(PROFILE, id='tiny', context_window=3000, max_input_tokens=24_000)
        tiny, rec2 = make_provider(lambda r: ok(), small_context)
        with self.assertRaises(ProviderError):                     # 입력+최대 출력이 모델 컨텍스트를 넘는다
            tiny.generate(GenerationRequest(kind='review', system='s', user='x' * 4000))
        self.assertEqual(rec2.calls, 0)

    def test_errors_never_carry_secrets_or_code(self):
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        root = logging.getLogger()
        root.addHandler(handler)
        old_level = root.level
        root.setLevel(logging.DEBUG)
        try:
            provider, _ = make_provider(lambda r: httpx2.Response(
                400, json={'type': 'error', 'error': {'type': 'invalid_request_error',
                                                      'message': f'bad input {CODE_MARKER} {API_KEY}'}}))
            with self.assertRaises(ProviderError) as ctx:
                provider.generate(request())
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)
        rendered = (str(ctx.exception) + repr(ctx.exception.failure) + repr(provider) + ' '.join(r.getMessage() for r in records)
                    + ''.join(traceback.format_exception(ctx.exception)))
        for secret in (API_KEY, 'SUPERSECRETKEY', CODE_MARKER, 'upstream said'):
            self.assertNotIn(secret, rendered)


class CountTokens(unittest.TestCase):
    def test_count_tokens_is_a_separate_unbilled_call(self):
        provider, rec = make_provider(lambda r: httpx2.Response(200, json={'input_tokens': 1234}))
        self.assertEqual(provider.count_tokens(request()), 1234)             # 여유분을 임의로 더하지 않는다
        self.assertEqual((rec.requests[0].url.path, rec.calls), ('/v1/messages/count_tokens', 1))
        body = json.loads(rec.requests[0].content)
        self.assertEqual(set(body), {'model', 'system', 'messages', 'output_config'})
        self.assertEqual(body['model'], PROFILE.model)

    def test_count_tokens_includes_the_exact_output_schema_that_generate_sends(self):
        for kind in ('summary', 'review'):
            with self.subTest(kind=kind):
                provider, rec = make_provider(lambda r: httpx2.Response(200, json={'input_tokens': 10}))
                req = request(kind)
                provider.count_tokens(req)
                counted = json.loads(rec.requests[0].content)
                provider, rec = make_provider(lambda r: httpx2.Response(200, json=message_json()))
                provider.generate(req)
                sent = json.loads(rec.requests[0].content)
                self.assertEqual(counted['output_config'], sent['output_config'])
                self.assertEqual(counted['output_config']['format']['schema'], prompts.output_schema(kind))
                self.assertEqual((counted['model'], counted['system'], counted['messages']),
                                 (sent['model'], sent['system'], sent['messages']))

    def test_unknown_origin_5xx_on_count_tokens_is_retryable_without_billing_uncertainty(self):
        provider, _ = make_provider(lambda r: httpx2.Response(502, text='bad gateway'))
        with self.assertRaises(ProviderError) as ctx:
            provider.count_tokens(request())
        f = ctx.exception.failure
        self.assertEqual((f.retryable, f.outcome_uncertain), (True, False))

    def test_request_and_response_objects_do_not_expose_bodies_in_repr(self):
        req = request()
        resp = provider_response = make_provider(lambda r: httpx2.Response(200, json=message_json(
            text=json.dumps({'schema_version': 'review-output-v1', 'findings': [], 'note': CODE_MARKER}))))[0].generate(req)
        sent = sent_input()
        rendered = prompts.render_request('review', sent)
        shown = ' '.join(repr(o) + str(o) for o in (req, resp, sent, sent.files[0], rendered))
        self.assertNotIn(CODE_MARKER, shown)
        self.assertNotIn('def f():', shown)
        self.assertIn('review', repr(req))
        self.assertIn('finish', repr(provider_response))

    def test_count_tokens_failures_are_never_billing_uncertain(self):
        provider, _ = make_provider(lambda r: httpx2.ReadTimeout('slow'))
        with self.assertRaises(ProviderError) as ctx:
            provider.count_tokens(request())
        self.assertEqual((ctx.exception.failure.kind, ctx.exception.failure.outcome_uncertain), ('timeout', False))
        provider, _ = make_provider(lambda r: httpx2.Response(429, json=error_json(), headers={'retry-after': '5'}))
        with self.assertRaises(ProviderError) as ctx:
            provider.count_tokens(request())
        self.assertEqual((ctx.exception.failure.kind, ctx.exception.failure.retry_after), ('rate_limited', 5))


class Profiles(unittest.TestCase):
    def test_default_profile_pins_model_price_limits_and_timeout(self):
        p = PROFILE
        self.assertEqual((p.id, p.provider, p.model, p.display_name), ('claude-opus-5-5-v1', 'anthropic', 'claude-opus-5-5',
                                                                   'Claude Opus 5.5'))
        self.assertEqual((p.max_input_tokens, p.max_output_tokens, p.timeout_seconds), (24_000, 4_000, 90))
        self.assertEqual((p.input_price_per_mtok, p.output_price_per_mtok, p.currency), (Decimal('4.00'), Decimal('20.00'), 'USD'))
        self.assertEqual((p.context_window, p.price_version, p.effort), (1_000_000, '2026-09-25', 'low'))
        self.assertEqual((p.prompt_version('summary'), p.prompt_version('review')), ('summary-v1', 'review-v1'))
        self.assertEqual((p.schema_version('summary'), p.schema_version('review')),
                         ('summary-output-v1', 'review-output-v1'))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            p.model = 'x'
        self.assertIs(registry.get_profile('claude-opus-5-5-v1'), PROFILE)

    def test_any_change_to_the_profile_changes_its_fingerprint(self):
        base = PROFILE.fingerprint()
        self.assertEqual(base, registry.get_profile('claude-opus-5-5-v1').fingerprint())
        for change in ({'model': 'x'}, {'input_price_per_mtok': Decimal('5.00')}, {'output_price_per_mtok': Decimal('21')},
                       {'max_input_tokens': 1}, {'max_output_tokens': 1}, {'timeout_seconds': 1}, {'effort': 'high'},
                       {'context_window': 1}, {'review_prompt': 'review-v2'}, {'review_schema': 'review-output-v2'},
                       {'price_version': '2027-01-01'}, {'bytes_per_token': 3.0}):
            with self.subTest(change=change):
                self.assertNotEqual(dataclasses.replace(PROFILE, **change).fingerprint(), base)

    def test_unknown_profiles_and_providers_do_not_fall_back_to_anything(self):
        with self.assertRaises(registry.UnknownProfile):
            registry.get_profile('gpt-whatever')
        foreign = dataclasses.replace(PROFILE, id='foreign-v1', provider='some-other-company')
        with self.assertRaises(registry.UnknownProvider):
            registry.create_provider(foreign)
        with patch.dict(os.environ, {'AI_PROVIDER_PROFILE': 'nope'}):
            with self.assertRaises(registry.UnknownProfile):
                registry.current_profile()
        provider = registry.create_provider(PROFILE, api_key=API_KEY)
        self.assertIsInstance(provider, AnthropicProvider)
        with self.assertRaises(ProviderConfigError):                                # 공용 key로 대체하지 않는다
            registry.create_provider(PROFILE)

    def test_settings_validation_checks_profile_and_credentials_before_any_call(self):
        env = {'AI_ENABLED': '1', 'AI_INPUT_ENCRYPTION_KEYS': 'v1:' + 'a1' * 32, 'AI_INPUT_KEY_VERSION': 'v1',
               'AI_CREDENTIAL_ENCRYPTION_KEYS': 'c1:' + 'b2' * 32, 'AI_CREDENTIAL_KEY_VERSION': 'c1',
               'AI_ALLOWED_ORIGINS': 'http://localhost:8000', 'DATABASE_URL': 'postgresql://u:p@localhost/db',
               'DATABASE_URL_ASYNC': '', 'DATABASE_SSL_CA': '', 'AI_PROVIDER_PROFILE': 'claude-opus-5-5-v1',
               'AI_ALLOWED_PROFILES': ''}
        def problems(**over):
            with patch.dict(os.environ, {**env, **over}):
                return ai_settings.validate_ai_settings()
        # 서버 공용 API key는 필요 없다(사용자가 자신의 key를 등록한다). 대신 사용자 key 암호화 키가 필요하다.
        self.assertEqual(problems(AI_ALLOW_EXTERNAL_TRANSFER='1', ANTHROPIC_API_KEY=''), [])
        self.assertEqual(problems(AI_ALLOW_EXTERNAL_TRANSFER='0'), [])
        self.assertTrue(any('AI_CREDENTIAL_ENCRYPTION_KEYS' in p for p in problems(AI_CREDENTIAL_ENCRYPTION_KEYS='')))
        self.assertTrue(any('AI_CREDENTIAL_KEY_VERSION' in p for p in problems(AI_CREDENTIAL_KEY_VERSION='c9')))
        self.assertTrue(any('달라야' in p for p in problems(AI_CREDENTIAL_ENCRYPTION_KEYS='v1:' + 'a1' * 32,
                                                          AI_CREDENTIAL_KEY_VERSION='v1')))   # 입력 키와 같은 키 금지
        self.assertTrue(any('AI_PROVIDER_PROFILE' in p for p in problems(AI_PROVIDER_PROFILE='unknown-profile')))
        self.assertTrue(any('AI_PROVIDER_PROFILE' in p for p in problems(AI_ALLOWED_PROFILES='claude-sonnet-5-5-v1')))
        self.assertEqual(problems(AI_ALLOWED_PROFILES='claude-opus-5-5-v1,claude-sonnet-5-5-v1'), [])
        leaked = ' '.join(problems(AI_CREDENTIAL_ENCRYPTION_KEYS='', AI_PROVIDER_PROFILE='zzz'))
        self.assertNotIn('b2' * 32, leaked)


class TokenBudget(unittest.TestCase):
    def test_limits_reject_before_calling(self):
        token_budget.check_input_limit(PROFILE, PROFILE.max_input_tokens)                    # 경계값은 허용
        with self.assertRaises(TokenLimitExceeded) as ctx:
            token_budget.check_input_limit(PROFILE, PROFILE.max_input_tokens + 1)
        self.assertEqual((ctx.exception.limit_kind, ctx.exception.limit), ('input', 24_000))
        small = dataclasses.replace(PROFILE, context_window=10_000, max_input_tokens=24_000)
        with self.assertRaises(TokenLimitExceeded) as ctx:
            token_budget.check_input_limit(small, 7_000, 4_000)
        self.assertEqual(ctx.exception.limit_kind, 'context')
        token_budget.check_input_limit(small, 6_000, 4_000)
        with self.assertRaises(TokenLimitExceeded):
            token_budget.check_input_limit(PROFILE, 100, 4_000_000)

    def test_cost_is_decimal_and_rounded_up(self):
        self.assertEqual(token_budget.cost_of(PROFILE, 1_000_000, 0), Decimal('4.000000'))
        self.assertEqual(token_budget.cost_of(PROFILE, 0, 1_000_000), Decimal('20.000000'))
        self.assertEqual(token_budget.cost_of(PROFILE, 1, 0), Decimal('0.000004'))
        odd = dataclasses.replace(PROFILE, input_price_per_mtok=Decimal('0.3333335'))
        self.assertEqual(token_budget.cost_of(odd, 1, 0), Decimal('0.000001'))                # 올림
        self.assertEqual(token_budget.cost_of(PROFILE, 0, 0), Decimal('0.000000'))
        self.assertEqual(token_budget.calculate_cost(PROFILE, Usage(100, 50)), Decimal('0.001400'))
        reserve = token_budget.max_cost(PROFILE, 10_000)
        self.assertEqual(reserve, Decimal('0.120000'))                                       # 10k*$4/M + 4k*$20/M
        self.assertGreaterEqual(reserve, token_budget.calculate_cost(PROFILE, Usage(10_000, PROFILE.max_output_tokens)))
        self.assertEqual(token_budget.max_cost(PROFILE, 10_000, 1000), Decimal('0.060000'))

    def test_preview_estimate_is_conservative_relative_to_the_rendered_prompt(self):
        for lines in (5, 200, 2000):
            with self.subTest(lines=lines):
                body = [DiffLine(kind=DiffLineKind.added, right_line=i + 1, text=f'    value_{i} = compute({i})')
                        for i in range(lines)]
                f = prompts.SentFile(handle='F1', file_id=UUID(int=1), path='pkg/module.py', old_path=None,
                                     change_type=ChangeType.added, side_availability='RIGHT', language='python', lines=body)
                sent = prompts.SentInput(files=[f], metadata=PRTransferMetadata(title='Title', body='Body text'))
                rendered = prompts.render_request('review', sent)
                actual = token_budget.estimate_prompt_tokens(PROFILE, rendered.system, rendered.user)
                payload = sum(len(l.text.encode()) + 12 for l in body)          # unified diff 바이트에 가까운 값
                preview = token_budget.estimate_input_tokens(PROFILE, 'review', payload_bytes=payload,
                                                             description_chars=len('Title') + len('Body text'), file_count=1)
                self.assertGreaterEqual(preview, actual * 0.98)
                self.assertLessEqual(preview, actual * 1.7)

    def test_estimates_grow_with_input(self):
        sizes = [token_budget.estimate_input_tokens(PROFILE, 'review', payload_bytes=n, description_chars=0, file_count=1)
                 for n in (0, 1_000, 10_000, 100_000)]
        self.assertEqual(sizes, sorted(sizes))
        self.assertGreater(sizes[-1], PROFILE.max_input_tokens)
        self.assertEqual(parse_json_object('{"a": 1}'), ({'a': 1}, None))

    def test_prompt_files_are_pinned_to_their_version(self):
        prompts.verify_prompt_digests()
        with patch.dict(prompts.PROMPT_DIGESTS, {'review_v1.txt': '0' * 64}):
            with self.assertRaises(RuntimeError):
                prompts.verify_prompt_digests()


if __name__ == '__main__':
    unittest.main(verbosity=2)
