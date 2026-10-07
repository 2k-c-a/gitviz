"""통합 검증용 가짜 Anthropic API 서버(로컬 전용). 실제 제공자·외부 네트워크를 쓰지 않는다.

    python scripts/ai_fake_provider.py <port>

worker가 AI_TEST_PROVIDER_BASE_URL=http://127.0.0.1:<port>로 이 서버를 호출한다. 동작은 /__mode로 바꾼다:
  POST /__mode  {"mode": "ok" | "slow" | "drop" | "status" | "bad_json" | "fake_location", "delay": 초, "status": 코드}
  GET  /__stats  요청 횟수·본 API key 목록·마지막 모델/출력 설정(요청 본문은 저장하지 않는다)
모델 출력은 요청 프롬프트에서 실제 파일 handle과 추가된 줄 번호를 읽어 검증을 통과하는 JSON을 만든다.
"""
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
STATE = {'mode': 'ok', 'delay': 0.0, 'status': 500, 'messages': 0, 'count_tokens': 0, 'models': 0, 'keys': [], 'last_model': None,
         'last_schema_version': None, 'last_tools': None}
FILE_RE = re.compile(r'<<<(\w+):BEGIN file handle=(F\d+) path=("(?:[^"\\]|\\.)*") ')
ADDED_RE = re.compile(r'^\+ L- R(\d+) \| (.*)$')


def parse_prompt(user: str):
    """첫 파일의 handle·경로와 그 파일에서 추가된 첫 줄(번호, 텍스트)을 읽는다."""
    files, current = [], None
    for line in user.split('\n'):
        m = FILE_RE.match(line)
        if m:
            current = {'handle': m.group(2), 'path': json.loads(m.group(3)), 'added': None}
            files.append(current)
            continue
        if line.startswith('<<<') and ':END>>>' in line:
            current = None
            continue
        if current is not None and current['added'] is None:
            a = ADDED_RE.match(line)
            if a:
                current['added'] = (int(a.group(1)), a.group(2))
    return files


def build_output(system: str, user: str, mode: str):
    files = [f for f in parse_prompt(user) if f['added']]
    is_review = 'review-output-v1' in system
    if not files:
        return {'schema_version': 'review-output-v1', 'findings': []} if is_review else None
    f = files[0]
    line, text = f['added']
    if is_review:
        if mode == 'fake_location':
            line, text = 9999, 'this line does not exist'
        return {'schema_version': 'review-output-v1', 'findings': [{
            'title': 'integration finding', 'category': 'maintainability', 'priority': 1, 'condition': 'when the added line runs',
            'impact': 'unclear behaviour', 'evidence_ids': [f['handle']], 'verification': 'read the changed line',
            'suggestion': 'name the value', 'limitations': [],
            'location': {'path': f['path'], 'side': 'RIGHT', 'start_line': line, 'end_line': line, 'quote': text.strip()}}]}
    return {'schema_version': 'summary-output-v1', 'overview': 'the PR changes a value', 'author_intent': None,
            'file_changes': [{'path': f['path'], 'summary': 'changes a line', 'evidence_ids': [f['handle']]}],
            'unknowns': ['callers are not shown'], 'evidence_ids': [f['handle']]}


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):          # 요청 내용을 로그에 남기지 않는다
        pass

    def _json(self, status, body, headers=None):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('request-id', f'req_fake_{int(time.time() * 1000)}')
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n) or b'{}') if n else {}

    def _note_key(self):
        key = self.headers.get('x-api-key') or ''
        with LOCK:
            if key and key not in STATE['keys']:
                STATE['keys'].append(key)
        return key

    def do_GET(self):
        if self.path == '/__stats':
            with LOCK:
                return self._json(200, dict(STATE))
        key = self._note_key()
        if self.path.startswith('/v1/models/'):
            with LOCK:
                STATE['models'] += 1
            if key.endswith('BADKEY'):
                return self._json(401, {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}})
            return self._json(200, {'id': self.path.rsplit('/', 1)[-1], 'type': 'model', 'display_name': 'fake', 'created_at': '2026-01-01T00:00:00Z'})
        self._json(404, {'type': 'error', 'error': {'type': 'not_found_error', 'message': 'x'}})

    def do_POST(self):
        if self.path == '/__mode':
            body = self._body()
            with LOCK:
                STATE.update({k: body[k] for k in ('mode', 'delay', 'status') if k in body})
            return self._json(200, {'ok': True})
        if self.path == '/__reset':
            with LOCK:
                STATE.update({'mode': 'ok', 'delay': 0.0, 'status': 500, 'messages': 0, 'count_tokens': 0, 'models': 0, 'keys': [], 'last_model': None, 'last_tools': None})
            return self._json(200, {'ok': True})
        key = self._note_key()
        body = self._body()
        if self.path == '/v1/messages/count_tokens':
            with LOCK:
                STATE['count_tokens'] += 1
            return self._json(200, {'input_tokens': 1500})
        if self.path == '/v1/messages':
            with LOCK:
                STATE['messages'] += 1
                mode, delay, status = STATE['mode'], STATE['delay'], STATE['status']
                STATE['last_model'] = body.get('model')
                STATE['last_tools'] = [k for k in ('tools', 'tool_choice') if k in body]
            if key.endswith('BADKEY'):
                return self._json(401, {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}})
            if mode == 'drop':                                  # 요청을 받은 뒤 응답 없이 연결을 끊는다(응답 유실)
                time.sleep(delay)
                self.close_connection = True
                self.connection.close()
                return
            if mode == 'slow':
                time.sleep(delay)
            if mode == 'status':
                return self._json(int(status), {'type': 'error', 'error': {'type': 'api_error', 'message': 'fake'}})
            system = body.get('system') or ''
            user = (body.get('messages') or [{}])[0].get('content') or ''
            out = build_output(system, user, mode)
            text = '{not json' if mode == 'bad_json' else json.dumps(out)
            return self._json(200, {'id': 'msg_fake', 'type': 'message', 'role': 'assistant', 'model': body.get('model'),
                                    'content': [{'type': 'text', 'text': text}], 'stop_reason': 'end_turn', 'stop_sequence': None,
                                    'usage': {'input_tokens': 1200, 'output_tokens': 300}})
        self._json(404, {'type': 'error', 'error': {'type': 'not_found_error', 'message': 'x'}})


def serve(port: int):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == '__main__':
    serve(int(sys.argv[1]))
