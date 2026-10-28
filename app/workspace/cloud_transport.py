"""Pure cloud wire/response codecs. Networking and API keys remain in main."""
from __future__ import annotations

import json
from typing import Any, cast

from .contracts import decode
from .refactor_models import output_schema
from .review_transport import Reply, TransportFailure

DESTINATIONS = {'anthropic': ('api.anthropic.com', '/v1/messages'),
                'openai': ('api.openai.com', '/v1/responses'),
                'gemini': ('generativelanguage.googleapis.com', '/v1beta/models/{model}:generateContent')}


def body(profile: dict[str, Any], system: str, user: str) -> bytes:
    schema = output_schema(profile['schemaVersion'])
    provider = profile['provider']
    if provider == 'openai':
        def strict(value: Any) -> None:
            if isinstance(value, dict):
                mapping = cast(dict[str, Any], value)
                mapping.pop('default', None)
                if mapping.get('type') == 'object':
                    mapping['additionalProperties'] = False
                    mapping['required'] = list(mapping.get('properties', {}))
                for nested in mapping.values():
                    strict(nested)
            elif isinstance(value, list):
                for nested in cast(list[Any], value):
                    strict(nested)
        strict(schema)
        value = {'model': profile['model'], 'instructions': system, 'input': user, 'store': False,
                 'max_output_tokens': profile['maxOutputTokens'], 'reasoning': {'effort': profile['effort']},
                 'text': {'format': {'type': 'json_schema', 'name': 'gitviz_workspace', 'strict': True, 'schema': schema}}}
    elif provider == 'anthropic':
        value = {'model': profile['model'], 'max_tokens': profile['maxOutputTokens'], 'system': system,
                 'messages': [{'role': 'user', 'content': user}],
                 'output_config': {'effort': profile['effort'], 'format': {'type': 'json_schema', 'schema': schema}}}
    elif provider == 'gemini':
        value = {'systemInstruction': {'parts': [{'text': system}]}, 'contents': [{'role': 'user', 'parts': [{'text': user}]}],
                 'generationConfig': {'responseMimeType': 'application/json', 'responseJsonSchema': schema, 'maxOutputTokens': profile['maxOutputTokens']}}
    else:
        raise TransportFailure('PROVIDER_BLOCKED', False)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def parse(profile: dict[str, Any], raw: bytes) -> Reply:
    value = decode(raw)
    try:
        provider = profile['provider']
        if provider == 'anthropic':
            if value['model'] != profile['model'] or value['stop_reason'] != 'end_turn' or value.get('type') != 'message' or value.get('role') != 'assistant':
                raise ValueError
            blocks = value['content']
            if any(item.get('type') not in ('text', 'thinking', 'redacted_thinking') for item in blocks):
                raise ValueError
            text = ''.join(item['text'] for item in blocks if item.get('type') == 'text')
            usage = value.get('usage', {})
            inputs = usage.get('input_tokens')
            if type(inputs) is int:
                inputs += usage.get('cache_creation_input_tokens', 0) + usage.get('cache_read_input_tokens', 0)
            outputs = usage.get('output_tokens')
        elif provider == 'openai':
            if value.get('model') != profile['model'] or value.get('status') != 'completed':
                raise ValueError
            blocks = [part for item in value['output'] if item.get('type') == 'message' for part in item['content']]
            if any(part.get('type') != 'output_text' for part in blocks) or any(item.get('type') not in ('message', 'reasoning') for item in value['output']):
                raise ValueError
            text = ''.join(part['text'] for part in blocks)
            usage = value.get('usage', {})
            inputs, outputs = usage.get('input_tokens'), usage.get('output_tokens')
        elif provider == 'gemini':
            if value.get('modelVersion') != profile['model'] or value.get('promptFeedback', {}).get('blockReason') or len(value['candidates']) != 1:
                raise ValueError
            candidate = value['candidates'][0]
            if candidate['finishReason'] != 'STOP' or any('functionCall' in part for part in candidate['content']['parts']):
                raise ValueError
            text = ''.join(part['text'] for part in candidate['content']['parts'] if not part.get('thought'))
            usage = value.get('usageMetadata', {})
            inputs, outputs = usage.get('promptTokenCount'), usage.get('candidatesTokenCount')
            if type(outputs) is int:
                outputs += usage.get('thoughtsTokenCount', 0)
        else:
            raise ValueError
        if not text:
            raise ValueError
        normalized = {'inputTokens': inputs, 'outputTokens': outputs} if all(type(n) is int and 0 <= n <= 2147483647 for n in (inputs, outputs)) else None
        return Reply(text.encode(), normalized)
    except (KeyError, ValueError, TypeError, AttributeError, IndexError):
        raise TransportFailure('OUTPUT_INCOMPLETE', True) from None


