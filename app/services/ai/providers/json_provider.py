"""Single-request REST adapters. No redirects, proxy environment, retries or fallback.

Cloud endpoints are constants. Local endpoints are administrator configured and
restricted to loopback. Neither requests nor upstream errors enter exception text.
"""
import copy
import time
from urllib.parse import urlsplit

import httpx2 as httpx

from app.services.ai import prompts, token_budget
from app.services.ai.providers.base import (
    ProviderError, ProviderFailure, ProviderResponse, Usage, parse_json_object,
)


class ProviderConfigError(ValueError):
    pass


def local_endpoint(url):
    try:
        p = urlsplit(url)
        port = p.port
        valid = (p.scheme == 'http' and p.hostname in ('localhost', '127.0.0.1', '::1')
                 and not p.username and not p.password and not p.query and not p.fragment
                 and p.path in ('', '/') and port is not None)
    except ValueError:
        valid = False
    if not valid:
        raise ProviderConfigError('Local AI endpoint must be an explicit loopback HTTP port')
    return url.rstrip('/')


def strict_schema(kind, schema_version=None, *, enforce_lengths=False):
    schema = copy.deepcopy(prompts.output_schema(kind, schema_version, enforce_lengths=enforce_lengths))
    def visit(node):
        if isinstance(node, dict):
            node.pop('default', None)
            if node.get('type') == 'object':
                node['additionalProperties'] = False
                node['required'] = list(node.get('properties', {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(schema)
    return schema


def integer(value):
    return type(value) is int and value >= 0


class JsonProvider:
    base_url = ''
    def __init__(self, profile, *, api_key=None, client=None):
        self.profile = profile
        if profile.requires_api_key and not api_key:
            raise ProviderConfigError('User API key is required')
        self._headers = self.headers(api_key)
        self._client = client

    def headers(self, key):
        return {}

    def output_schema(self, kind):
        return strict_schema(kind, self.profile.schema_version(kind), enforce_lengths=self.profile.provider == 'openai')

    def classify_error(self, exc):
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
            return ProviderFailure('connection_failed', True, False)
        return ProviderFailure('timeout' if isinstance(exc, httpx.TimeoutException) else 'response_lost', False, True)

    def call(self, path, body=None, *, billable=False, method='POST'):
        client = self._client or httpx.Client(timeout=self.profile.timeout_seconds,
                                              trust_env=False, follow_redirects=False)
        try:
            response = client.request(method, self.base_url + path, json=body, headers=self._headers)
        except Exception as exc:
            failure = self.classify_error(exc)
            if not billable:
                failure = ProviderFailure(failure.kind, True, False)
            raise ProviderError(failure) from None
        finally:
            if self._client is None:
                client.close()
        status = response.status_code
        if status != 200:
            kind = ('auth_failed' if status in (401, 403) else 'model_unavailable' if status == 404
                    else 'rate_limited' if status == 429 else 'response_lost' if status >= 500 or status in (408, 202)
                    else 'request_rejected')
            uncertain = billable and kind == 'response_lost'
            raise ProviderError(ProviderFailure(kind, not uncertain and (status == 429 or status >= 500),
                                                uncertain, http_status=status)) from None
        try:
            if len(response.content) > 1_000_000:
                raise ValueError()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except Exception:
            raise ProviderError(ProviderFailure('response_lost', not billable, billable)) from None

    def estimate_tokens(self, request):
        # Include output schema, which is also part of the provider's input.
        import json
        return token_budget.estimate_prompt_tokens(self.profile, request.system,
                    request.user + json.dumps(self.output_schema(request.kind)))

    def output_limit(self, request):
        limit = self.profile.max_output_tokens if request.max_output_tokens is None else request.max_output_tokens
        if type(limit) is not int or not 0 < limit <= self.profile.max_output_tokens:
            raise ProviderConfigError('Invalid output token limit')
        token_budget.check_input_limit(self.profile, self.estimate_tokens(request), limit)
        return limit

    def result(self, data, text, finish, usage, started):
        if not isinstance(text, str):
            raise ProviderError(ProviderFailure('response_lost', False, True)) from None
        parsed, error = parse_json_object(text)
        return ProviderResponse(request_id=data.get('id') or data.get('responseId'),
            model=data.get('model') or data.get('modelVersion'), finish=finish,
            text=text, parsed=parsed, parse_error=error, usage=usage,
            cost=token_budget.calculate_cost(self.profile, usage) if usage is not None else None,
            latency_seconds=time.monotonic() - started)


class OpenAIProvider(JsonProvider):
    base_url = 'https://api.openai.com/v1'
    def headers(self, key):
        return {'Authorization': 'Bearer ' + key}

    def params(self, request):
        return {'model': self.profile.model, 'instructions': request.system, 'input': request.user,
                'text': {'format': {'type': 'json_schema', 'name': 'gitviz_' + request.kind,
                                   'strict': True, 'schema': self.output_schema(request.kind)}}}

    def verify_credential(self):
        self.call('/models/' + self.profile.model, method='GET')

    def count_tokens(self, request):
        self.output_limit(request)
        data = self.call('/responses/input_tokens', self.params(request))
        count = data.get('input_tokens')
        if not integer(count):
            raise ProviderError(ProviderFailure('response_lost', True, False))
        return count

    def generate(self, request):
        limit = self.output_limit(request)
        started = time.monotonic()
        data = self.call('/responses', {**self.params(request), 'max_output_tokens': limit,
                    'store': False, 'reasoning': {'effort': self.profile.effort}}, billable=True)
        try:
            blocks = [c for item in data['output'] if item.get('type') == 'message' for c in item['content']]
            refused = any(c.get('type') == 'refusal' for c in blocks)
            text = ''.join(c['text'] for c in blocks if c.get('type') == 'output_text')
            finish = ('refused' if refused else 'complete' if data['status'] == 'completed'
                      else 'truncated' if data.get('incomplete_details', {}).get('reason') == 'max_output_tokens'
                      else 'incomplete')
            u = data.get('usage') or {}
            usage = Usage(u['input_tokens'], u['output_tokens']) if all(integer(u.get(k)) for k in
                            ('input_tokens', 'output_tokens')) else None
        except (KeyError, TypeError, AttributeError):
            raise ProviderError(ProviderFailure('response_lost', False, True)) from None
        return self.result(data, text, finish, usage, started)


class GeminiProvider(JsonProvider):
    base_url = 'https://generativelanguage.googleapis.com/v1beta'
    def headers(self, key):
        return {'x-goog-api-key': key}

    def params(self, request):
        return {'systemInstruction': {'parts': [{'text': request.system}]},
                'contents': [{'role': 'user', 'parts': [{'text': request.user}]}],
                'generationConfig': {'responseMimeType': 'application/json',
                                     'responseJsonSchema': self.output_schema(request.kind)}}

    def verify_credential(self):
        self.call('/models/' + self.profile.model, method='GET')

    def count_tokens(self, request):
        self.output_limit(request)
        data = self.call('/models/' + self.profile.model + ':countTokens',
                         {'generateContentRequest': {'model': 'models/' + self.profile.model,
                                                     **self.params(request)}})
        count = data.get('totalTokens')
        if not integer(count):
            raise ProviderError(ProviderFailure('response_lost', True, False))
        return count

    def generate(self, request):
        limit = self.output_limit(request)
        started = time.monotonic()
        params = self.params(request)
        params['generationConfig']['maxOutputTokens'] = limit
        data = self.call('/models/' + self.profile.model + ':generateContent', params, billable=True)
        try:
            if data.get('promptFeedback', {}).get('blockReason'):
                text, finish = '', 'refused'
            else:
                candidate = data['candidates'][0]
                reason = candidate['finishReason']
                text = ''.join(p['text'] for p in candidate.get('content', {}).get('parts', [])
                               if 'text' in p and not p.get('thought'))
                finish = ('complete' if reason == 'STOP' else 'truncated' if reason == 'MAX_TOKENS'
                          else 'refused' if reason in ('SAFETY', 'RECITATION', 'BLOCKLIST', 'PROHIBITED_CONTENT')
                          else 'incomplete')
            u = data.get('usageMetadata') or {}
            usage = (Usage(u['promptTokenCount'], u.get('candidatesTokenCount', 0) + u.get('thoughtsTokenCount', 0))
                     if integer(u.get('promptTokenCount')) and integer(u.get('candidatesTokenCount'))
                     and integer(u.get('thoughtsTokenCount', 0)) else None)
        except (KeyError, TypeError, IndexError, AttributeError):
            raise ProviderError(ProviderFailure('response_lost', False, True)) from None
        return self.result(data, text, finish, usage, started)


class LocalChatProvider(JsonProvider):
    """Same-PC LM Studio/KoboldCpp chat API. Model IDs are administrator pinned;
    these APIs do not attest to model file hashes, unlike Ollama's digest API.
    """
    def __init__(self, profile, **kwargs):
        self.base_url = local_endpoint(profile.endpoint) + '/v1'
        if not profile.model.strip() or profile.execution_location != 'local' or profile.requires_api_key:
            raise ProviderConfigError('A configured keyless local model is required')
        super().__init__(profile, **kwargs)

    def verify_credential(self):
        data = self.call('/models', method='GET')
        if not any(isinstance(m, dict) and m.get('id') == self.profile.model for m in data.get('data', [])):
            raise ProviderError(ProviderFailure('model_unavailable', False, False))

    def count_tokens(self, request):
        self.output_limit(request)
        return self.estimate_tokens(request)

    def prepare_generation(self, request):
        self.output_limit(request)
        self.verify_credential()
        return lambda: self._generate_verified(request)

    def generate(self, request):
        return self.prepare_generation(request)()

    def _generate_verified(self, request):
        limit = self.output_limit(request)
        started = time.monotonic()
        data = self.call('/chat/completions', {
            'model': self.profile.model, 'stream': False,
            'messages': [{'role': 'system', 'content': request.system},
                         {'role': 'user', 'content': request.user}],
            'max_tokens': limit,
            'response_format': {'type': 'json_schema', 'json_schema': {
                'name': 'gitviz_' + request.kind, 'strict': True, 'schema': self.output_schema(request.kind)}},
        }, billable=True)
        try:
            choice = data['choices'][0]
            message = choice['message']
            text = message.get('content') or ''
            reason = choice.get('finish_reason')
            finish = ('refused' if message.get('refusal') or reason == 'content_filter'
                      else 'complete' if reason == 'stop' else 'truncated' if reason == 'length'
                      else 'incomplete')
            u = data.get('usage') or {}
            usage = Usage(u['prompt_tokens'], u['completion_tokens']) if all(
                integer(u.get(k)) for k in ('prompt_tokens', 'completion_tokens')) else None
        except (KeyError, TypeError, IndexError, AttributeError):
            raise ProviderError(ProviderFailure('response_lost', False, True)) from None
        result = self.result(data, text, finish, usage, started)
        from decimal import Decimal
        result.cost = Decimal('0')
        return result


class OllamaProvider(JsonProvider):
    def __init__(self, profile, **kwargs):
        self.base_url = local_endpoint(profile.endpoint)
        if not profile.model_digest or profile.model.endswith(':cloud'):
            raise ProviderConfigError('A pinned installed local model digest is required')
        super().__init__(profile, **kwargs)

    def verify_credential(self):
        data = self.call('/api/tags', method='GET')
        matches = [m for m in data.get('models', []) if m.get('name') == self.profile.model
                   and m.get('digest') == self.profile.model_digest]
        if not matches:
            raise ProviderError(ProviderFailure('model_unavailable', False, False))
        info = self.call('/api/show', {'model': self.profile.model})
        # Cloud/remote models must never be treated as local execution.
        if info.get('remote_host') or info.get('remote_model'):
            raise ProviderError(ProviderFailure('model_unavailable', False, False))

    def count_tokens(self, request):
        self.output_limit(request)
        # Ollama has no preflight tokenizer. Conservative byte bound, not exact.
        return self.estimate_tokens(request)

    def prepare_generation(self, request):
        # Finish potentially slow model metadata I/O before the service's final
        # permission/cancellation check. The returned call sends code once.
        self.verify_credential()
        return lambda: self._generate_verified(request)

    def generate(self, request):
        self.output_limit(request)
        return self.prepare_generation(request)()

    def _generate_verified(self, request):
        limit = self.output_limit(request)
        started = time.monotonic()
        data = self.call('/api/chat', {'model': self.profile.model, 'stream': False,
                    'messages': [{'role': 'system', 'content': request.system},
                                 {'role': 'user', 'content': request.user}],
                    'format': strict_schema(request.kind),
                    'options': {'num_ctx': self.profile.context_window, 'num_predict': limit}}, billable=True)
        try:
            text = data['message']['content']
            finish = ('complete' if data.get('done') is True and data.get('done_reason') == 'stop'
                      else 'truncated' if data.get('done_reason') == 'length' else 'incomplete')
            usage = (Usage(data['prompt_eval_count'], data['eval_count']) if all(integer(data.get(k))
                     for k in ('prompt_eval_count', 'eval_count')) else None)
        except (KeyError, TypeError):
            raise ProviderError(ProviderFailure('response_lost', False, True)) from None
        result = self.result(data, text, finish, usage, started)
        # No provider fee, even if the local server omits token counters.
        from decimal import Decimal
        result.cost = Decimal('0')
        return result
