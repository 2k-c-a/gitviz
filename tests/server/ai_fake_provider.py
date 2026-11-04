"""통합 검증용 가짜 Anthropic API 서버(로컬 전용). 실제 제공자·외부 네트워크를 쓰지 않는다.

    python tests/server/ai_fake_provider.py <port>

worker가 AI_TEST_PROVIDER_BASE_URL=http://127.0.0.1:<port>로 이 서버를 호출한다. 동작은 /__mode로 바꾼다:
  POST /__mode  {"mode": "ok" | "slow" | "drop" | "status" | "bad_json" | "fake_location" | "fabricate", "delay": 초, "status": 코드}
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
STATE = {'mode': 'ok', 'delay': 0.0, 'status': 500, 'count': 1200, 'messages': 0, 'count_tokens': 0, 'models': 0, 'keys': [], 'last_model': None,
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



# ── AI 인사이트(자동·대화형 분석) 가짜 응답 ──
# 프롬프트의 도구 결과 블록에서 실제 값을 골라 근거 검증을 통과하는 JSON을 만든다.
# mode='fabricate'는 도구 결과에 없는 커밋 SHA·파일을 본문에 넣어 검증이 거부하는지 시험한다.
CALL_RE = re.compile(r'<<<(B[0-9a-f]+):BEGIN call=(T\d+) tool=(\w+) args=(.*?)>>>\n(.*?)\n<<<\1:END>>>', re.S)
QUESTION_RE = re.compile(r'<<<(B[0-9a-f]+):BEGIN question>>>\n(.*?)\n<<<\1:END>>>', re.S)
INSIGHT_ARGS = {'path': '', 'directory': '', 'contributor': '', 'sha': '', 'commitType': '', 'sortBy': '', 'query': '',
                'limit': 0, 'recentMonths': 0, 'baselineMonths': 0, 'depth': 0, 'startLine': 0, 'endLine': 0}
_PATH_KEYS = ('path', 'file_a', 'file_b', 'file_a_path', 'file_b_path')
_NAME_KEYS = ('name', 'author', 'contributor', 'collaborator')
_SKIP_NUMBERS = ('snapshot_id', 'limit', 'depth')


def insight_calls(user: str):
    out = []
    for _, call_id, tool, _, body in CALL_RE.findall(user):
        try:
            result = json.loads(body)
        except ValueError:
            continue
        out.append((call_id, tool, result))
    return out


def _facts(result):
    """결과에서 (첫 수치 키·값, 첫 경로, 첫 기여자, 첫 SHA)를 찾는다."""
    found = {'number': None, 'path': None, 'name': None, 'sha': None}

    def visit(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                if k not in ('period', 'notes'):          # 기간 메타데이터가 아니라 데이터 값을 근거로 고른다
                    visit(v, k)
        elif isinstance(node, list):
            for v in node:
                visit(v, key)
        elif isinstance(node, bool) or node is None:
            return
        elif isinstance(node, (int, float)):
            if found['number'] is None and key not in _SKIP_NUMBERS and not str(key).startswith('total'):
                found['number'] = (key, node)
        elif isinstance(node, str):
            if key in _PATH_KEYS and found['path'] is None:
                found['path'] = node
            elif key in _NAME_KEYS and found['name'] is None and node:
                found['name'] = node
            elif key == 'sha' and found['sha'] is None:
                found['sha'] = node
    visit(result)
    return found


def _references(calls):
    files, names, shas, evidence, code = [], [], [], [], []
    for call_id, tool, result in calls:
        if 'error' in result:
            continue
        if tool == 'readFile' and len(code) < 2:     # 실제로 받은 줄 범위 안에서 인용한다
            code.append({'path': result['path'], 'startLine': result['start_line'],
                         'endLine': min(result['end_line'], result['start_line'] + 4)})
        f = _facts(result)
        if f['number'] is not None and len(evidence) < 2:
            evidence.append({'toolCallId': call_id, 'label': f'{tool}.{f["number"][0]}', 'value': str(f['number'][1])})
        if f['path'] and f['path'] not in files:
            files.append(f['path'])
        if f['name'] and f['name'] not in names:
            names.append(f['name'])
        if f['sha'] and f['sha'] not in shas:
            shas.append(f['sha'])
    return evidence, files[:2], names[:2], shas[:1], code


def _chat_tool(question: str):
    token = re.search(r'[\w./-]+\.[A-Za-z]{1,5}', question)
    if token and ('코드' in question or '구현' in question or '역할' in question):
        return 'readFile', {**INSIGHT_ARGS, 'path': token.group(0), 'startLine': 1, 'endLine': 60}
    if token and ('같이' in question or '함께' in question):
        return 'getCoChangedFiles', {**INSIGHT_ARGS, 'path': token.group(0)}
    if '기여' in question or '개발자' in question:
        return 'getContributorStatistics', {**INSIGHT_ARGS, 'limit': 5}
    if '버그' in question or '변했' in question:
        return 'compareDevelopmentPeriods', {**INSIGHT_ARGS, 'recentMonths': 1, 'baselineMonths': 3}
    if '위험' in question:
        return 'getFileChangeFrequency', {**INSIGHT_ARGS, 'sortBy': 'maintenance_priority', 'limit': 5}
    return 'getProjectOverview', dict(INSIGHT_ARGS)


def build_insight_output(system: str, user: str, mode: str = 'ok'):
    calls = insight_calls(user)
    evidence, files, names, shas, code = _references(calls)
    if mode == 'fabricate':
        code = code + [{'path': 'src/invented/ghost_module.py', 'startLine': 1, 'endLine': 3}]
    invented = ' 커밋 0badc0ffee1234567 과 src/invented/ghost_module.py 가 원인입니다.' if mode == 'fabricate' else ''
    if 'insight_report-output-v1' in system:
        if not evidence:
            return {'schema_version': 'insight_report-output-v1', 'insights': [], 'limitations': ['근거 부족']}
        return {'schema_version': 'insight_report-output-v1', 'insights': [{
            'title': '관측 데이터 요약', 'summary': f"{evidence[0]['label']} 값은 {evidence[0]['value']}입니다.{invented}",
            'severity': 'medium' if len(evidence) > 1 else 'info', 'confidence': 'high',
            'evidence': evidence, 'interpretation': '변경이 이 영역에 모일 가능성이 있습니다.',
            'recommendation': '관련 파일의 변경 이력을 함께 검토해 보세요.', 'relatedFiles': files,
            'relatedCommits': shas, 'relatedContributors': names, 'relatedCode': code}], 'limitations': ['가짜 공급자 응답입니다.']}
    found = QUESTION_RE.search(user)
    question = found.group(2) if found else ''
    if not calls:
        tool, args = _chat_tool(question)
        return {'schema_version': 'insight_chat-output-v1', 'action': 'tools',
                'calls': [{'tool': tool, 'reason': '질문에 필요한 자료', 'args': args}],
                'answer': {'text': '', 'blocks': [], 'evidence': [], 'relatedFiles': [], 'relatedCommits': [],
                           'relatedContributors': [], 'relatedCode': [], 'limitations': []}}
    lines = [f"- {e['label']}: {e['value']}" for e in evidence] or ['- 확인된 수치가 없습니다.']
    return {'schema_version': 'insight_chat-output-v1', 'action': 'answer', 'calls': [],
            'answer': {'text': '데이터:\n' + '\n'.join(lines) + f'\n해석: 관측 구간 기준의 경향입니다.{invented}',
                       'blocks': [{'type': 'table', 'title': '근거', 'columns': ['항목', '값'],
                                   'rows': [[e['label'], e['value']] for e in evidence], 'items': [], 'value': '',
                                   'unit': ''}],
                       'evidence': evidence, 'relatedFiles': files, 'relatedCommits': shas,
                       'relatedContributors': names, 'relatedCode': code, 'limitations': ['가짜 공급자 응답입니다.']}}

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
                STATE.update({k: body[k] for k in ('mode', 'delay', 'status', 'count') if k in body})
            return self._json(200, {'ok': True})
        if self.path == '/__reset':
            with LOCK:
                STATE.update({'mode': 'ok', 'delay': 0.0, 'status': 500, 'count': 1200, 'messages': 0, 'count_tokens': 0, 'models': 0, 'keys': [], 'last_model': None, 'last_tools': None})
            return self._json(200, {'ok': True})
        key = self._note_key()
        body = self._body()
        if self.path == '/v1/messages/count_tokens':
            with LOCK:
                STATE['count_tokens'] += 1
            return self._json(200, {'input_tokens': STATE['count']})
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
            if 'insight_report-output-v1' in system or 'insight_chat-output-v1' in system:
                out = build_insight_output(system, user, mode)
            else:
                out = build_output(system, user, mode)
            text = '{not json' if mode == 'bad_json' else json.dumps(out)
            message = {'id': 'msg_fake', 'type': 'message', 'role': 'assistant', 'model': body.get('model'),
                       'content': [{'type': 'text', 'text': text}], 'stop_reason': 'end_turn', 'stop_sequence': None,
                       'usage': {'input_tokens': 1200, 'output_tokens': 300}}
            if mode == 'no_usage':                               # usage가 없는 응답(비용을 알 수 없다)
                del message['usage']
            return self._json(200, message)
        self._json(404, {'type': 'error', 'error': {'type': 'not_found_error', 'message': 'x'}})


def serve(port: int):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == '__main__':
    serve(int(sys.argv[1]))
