"""One bounded PC loopback call; no redirects, proxy, shell, tools or fallback."""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, cast

from .contracts import ProtocolError, decode
from .refactor_models import output_schema


@dataclass
class Reply:
    raw: bytes = field(repr=False)
    usage: dict[str, int] | None = None
    request_id: str | None = None


class TransportFailure(Exception):
    def __init__(self, code: str, uncertain: bool):
        super().__init__(code)
        self.code = code
        self.uncertain = uncertain


Transport = Callable[[dict[str, Any], str, str, threading.Event], Reply]


def request_body(profile: dict[str, Any], system: str, user: str) -> bytes:
    if profile['provider'] in ('anthropic', 'openai', 'gemini'):
        from .cloud_transport import body
        return body(profile, system, user)
    return json.dumps({'model': profile['model'], 'stream': False, 'format': output_schema(profile.get('schemaVersion', 'workspace-review-v1')),
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        'options': {'num_ctx': profile['contextWindow'], 'num_predict': profile['maxOutputTokens']}},
        sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def pc_ollama(profile: dict[str, Any], system: str, user: str, cancelled: threading.Event) -> Reply:
    # Renderer cannot supply a URL; assert the whole fixed identity at the I/O boundary.
    if profile['endpoint'] != 'http://127.0.0.1:11434' or profile['provider'] != 'ollama':
        raise TransportFailure('PROVIDER_BLOCKED', False)
    connection = http.client.HTTPConnection('127.0.0.1', 11434, timeout=profile['timeoutSeconds'])
    sent = False
    timer: threading.Timer | None = None
    try:
        if cancelled.is_set():
            raise TransportFailure('CANCELLED', False)
        started = time.monotonic()
        connection.connect()
        remaining = profile['timeoutSeconds'] - (time.monotonic() - started)
        if remaining <= 0:
            raise TransportFailure('PROVIDER_TIMEOUT', False)
        connected_socket = connection.sock
        def deadline() -> None:
            if connected_socket:
                try:
                    connected_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connected_socket.close()
        if connected_socket:
            connected_socket.settimeout(remaining)
        timer = threading.Timer(remaining, deadline)
        timer.daemon = True
        timer.start()
        if cancelled.is_set():
            raise TransportFailure('CANCELLED', False)
        body = request_body(profile, system, user)
        sent = True  # Any error after this point can have reached the runtime.
        connection.request('POST', '/api/chat', body, {'Content-Type': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise TransportFailure('PROVIDER_REJECTED', True)
        content = response.read(262145)
        if len(content) > 262144:
            raise TransportFailure('OUTPUT_LIMIT', True)
        decoded = decode(content)
        if not isinstance(decoded, dict):
            raise TransportFailure('OUTPUT_INVALID', True)
        result = cast(dict[str, Any], decoded)
        if result.get('model') != profile['model']:
            raise TransportFailure('PROFILE_CHANGED', True)
        if result.get('done') is not True or result.get('done_reason') != 'stop':
            raise TransportFailure('OUTPUT_INCOMPLETE', True)
        raw_message = result.get('message')
        if not isinstance(raw_message, dict):
            raise TransportFailure('OUTPUT_INVALID', True)
        message = cast(dict[str, Any], raw_message)
        if not isinstance(message.get('content'), str):
            raise TransportFailure('OUTPUT_INVALID', True)
        if message.get('tool_calls'):
            raise TransportFailure('OUTPUT_INVALID', True)
        usage = None
        if all(type(result.get(k)) is int and 0 <= result[k] <= 2147483647 for k in ('prompt_eval_count', 'eval_count')):
            usage = {'inputTokens': result['prompt_eval_count'], 'outputTokens': result['eval_count']}
        return Reply(message['content'].encode(), usage)
    except TransportFailure:
        raise
    except (OSError, socket.timeout, ValueError, KeyError, TypeError, ProtocolError):
        raise TransportFailure('PROVIDER_UNAVAILABLE', sent) from None
    finally:
        if timer:
            timer.cancel()
        connection.close()
