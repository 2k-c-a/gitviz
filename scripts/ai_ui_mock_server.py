"""AI 리뷰 화면을 브라우저에서 확인하기 위한 모의 서버. 실제 백엔드·DB·GitHub·AI 제공자를 쓰지 않는다.

    python scripts/ai_ui_mock_server.py [port]      # 기본 8765
    http://127.0.0.1:8765/pages/ai-review.html?repo=1

frontend/를 그대로 서빙하고 /api/ai/v1/* 응답을 계약(docs/AI_INTERFACE_CONTRACT.md)의 모양으로 흉내 낸다.
시나리오는 /mock/set?key=value (여러 개 가능)와 /mock/reset으로 바꾼다:
  unauth=1  disabled=1  pulls_error=429|503|404  xss=1  outcome=success|uncertain|result_lost|cancelled|access|keyless
  empty=1  expired=1  no_reviewable=1  head_changed=1  transfer_off=1  no_key=1  fail_create=<에러코드>  slow=1
"""
import sys
import time
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI()

SHA = lambda c: c * 40  # noqa: E731
STATE: dict = {}
FLAGS: dict = {}
XSS = '<img src=x onerror="window.__xss=1"><script>window.__xss=1</script>'


def reset():
    STATE.clear()
    FLAGS.clear()
    STATE.update(cred=None, inputs={}, jobs={}, results={}, findings={}, feedback={}, ver=0, spent=0.0)


reset()


def err(status, code, message='x', details=None, headers=None):
    return JSONResponse({'error': {'code': code, 'message': message, 'retryable': False, 'request_id': 'mock', 'details': details or {}}},
                        status_code=status, headers={'Cache-Control': 'private, no-store', **(headers or {})})


def guard(request: Request, mutating=False):
    if FLAGS.get('unauth'):
        return err(401, 'AUTH_REQUIRED')
    if FLAGS.get('disabled'):
        return err(404, 'RESOURCE_NOT_FOUND')
    if mutating and not request.headers.get('x-csrf-token'):
        return err(403, 'CSRF_FAILED')
    return None


def ok(body, status=200):
    return JSONResponse(body, status_code=status, headers={'Cache-Control': 'private, no-store'})


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def later(hours):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() + hours * 3600))


# ── 인증·저장소 ──
@app.get('/api/auth/me')
def me():
    if FLAGS.get('unauth'):
        return JSONResponse({'detail': 'no'}, status_code=401)
    return {'id': 'u1', 'login': 'tester'}


@app.get('/api/auth/csrf')
def csrf():
    if FLAGS.get('unauth'):
        return JSONResponse({'detail': 'no'}, status_code=401)
    return JSONResponse({'token': 'mock-csrf-token-0123456789abcdef0123456789abcdef'})


@app.get('/repositories/{rid}')
def repo(rid: int):
    return {'id': rid, 'url': f'https://github.com/acme/widgets-{rid}', 'name': f'widgets-{rid}', 'status': 'done'}


# ── 제어 ──
@app.get('/mock/set')
def mock_set(request: Request):
    for k, v in request.query_params.items():
        FLAGS[k] = v
    return {'flags': FLAGS}


@app.get('/mock/reset')
def mock_reset():
    reset()
    return {'ok': True}


@app.get('/mock/seed')
def mock_seed(request: Request):
    """입력(준비 완료)과 작업을 서버에서 바로 만들고 폴링을 advance번 진행시킨다. 나머지 query는 시나리오 flag다."""
    q = dict(request.query_params)
    kind, advance, paths = q.pop('kind', 'review'), int(q.pop('advance', 5)), q.pop('paths', 'main.py,.env').split(',')
    reset()
    FLAGS.update(q)
    if not FLAGS.get('no_key'):
        STATE['cred'] = {'version': 1, 'hint': 'sk-ant-…1111', 'profile': 'claude-opus-5-5-v1'}
    iid = str(uuid4())
    STATE['inputs'][iid] = {'id': iid, 'repo': 1, 'pr': 7, 'files': make_files(paths), 'gets': 3}
    out = {'input': iid}
    if kind != 'none':
        j = new_job(kind, iid)
        for _ in range(advance):
            job_view(j)
        out['job'] = j['id']
    return out


@app.get('/mock/state')
def mock_state():
    return {'flags': FLAGS, 'jobs': {k: {'status': v['status'], 'polls': v['polls']} for k, v in STATE['jobs'].items()},
            'cred': STATE['cred'] is not None, 'create_calls': STATE.get('create_calls', 0), 'jobs_created': len(STATE['jobs'])}


# ── 키·모델 ──
MODELS = [
    {'profile_id': 'claude-opus-5-5-v1', 'model': 'claude-opus-5-5', 'display_name': 'Claude Opus 5.5', 'input_price_per_mtok': '4.00', 'output_price_per_mtok': '20.00',
     'max_input_tokens': 24000, 'max_output_tokens': 4000, 'allowed': True},
    {'profile_id': 'claude-sonnet-5-5-v1', 'model': 'claude-sonnet-5-5', 'display_name': 'Claude Sonnet 5.5', 'input_price_per_mtok': '2.00', 'output_price_per_mtok': '10.00',
     'max_input_tokens': 24000, 'max_output_tokens': 4000, 'allowed': not FLAGS.get('x')},
]


def cred_view():
    c = STATE['cred']
    return {'provider': 'anthropic', 'status': 'active', 'version': c['version'], 'key_hint': c['hint'], 'preferred_profile_id': c['profile'],
            'validated_at': now(), 'created_at': now(), 'updated_at': now(),
            'in_flight_jobs': sum(1 for j in STATE['jobs'].values() if j['status'] in ('queued', 'running'))}


@app.get('/api/ai/v1/providers')
def providers(request: Request):
    if (e := guard(request)):
        return e
    return ok({'providers': [{'id': 'anthropic', 'display_name': 'Anthropic', 'models': MODELS}], 'external_transfer_allowed': not FLAGS.get('transfer_off')})


@app.get('/api/ai/v1/credentials')
def credentials(request: Request):
    if (e := guard(request)):
        return e
    if FLAGS.get('no_key') and STATE['cred'] is None:
        return ok({'credentials': []})
    if STATE['cred'] is None and not FLAGS.get('no_key') and not STATE.get('touched'):
        STATE['cred'] = {'version': 1, 'hint': 'sk-ant-…1111', 'profile': 'claude-opus-5-5-v1'}
    return ok({'credentials': [cred_view()] if STATE['cred'] else []})


@app.put('/api/ai/v1/credentials/{provider}')
async def put_cred(provider: str, request: Request):
    if (e := guard(request, True)):
        return e
    body = await request.json()
    STATE['touched'] = True
    key = body.get('api_key', '')
    if key.endswith('BAD'):
        return err(422, 'CREDENTIAL_INVALID', 'sk-ant-LEAK', {'reason': 'auth_failed'})
    changed = STATE['cred'] is None or not STATE['cred']['hint'].endswith(key[-4:])
    STATE['cred'] = {'version': (STATE['cred']['version'] + 1) if STATE['cred'] and changed else (STATE['cred'] or {'version': 1})['version'],
                     'hint': f'sk-ant-…{key[-4:]}', 'profile': body.get('profile_id') or 'claude-opus-5-5-v1'}
    return ok({'credential': cred_view(), 'changed': changed, 'previous_version': None})


@app.patch('/api/ai/v1/credentials/{provider}')
async def patch_cred(provider: str, request: Request):
    if (e := guard(request, True)):
        return e
    if STATE['cred'] is None:
        return err(404, 'RESOURCE_NOT_FOUND')
    STATE['cred']['profile'] = (await request.json())['profile_id']
    return ok(cred_view())


@app.delete('/api/ai/v1/credentials/{provider}')
def del_cred(provider: str, request: Request):
    if (e := guard(request, True)):
        return e
    STATE['touched'] = True
    STATE['cred'] = None
    n = 0
    for j in STATE['jobs'].values():
        if j['status'] in ('queued', 'running'):
            j['status'] = 'cancelled'
            n += 1
    return ok({'provider': provider, 'status': 'deleted', 'jobs_cancelled': n})


@app.get('/api/ai/v1/usage')
def usage(request: Request):
    if (e := guard(request)):
        return e
    return ok({'period_start': '2026-10-01', 'currency': 'USD', 'user_limit': '20.000000', 'user_spent': f'{STATE["spent"]:.6f}', 'user_reserved': '0.000000',
               'user_remaining': f'{20 - STATE["spent"]:.6f}', 'server_budget_enabled': False,
               'uncertain_reservations': 1 if any(j.get('uncertain') for j in STATE['jobs'].values()) else 0, 'daily_jobs_used': len(STATE['jobs']), 'daily_job_limit': 30})


# ── PR·파일 ──
def pr(n, **o):
    d = {'number': n, 'github_pr_id': 9000 + n, 'title': f'PR {n}: add feature', 'state': 'open', 'draft': False, 'author_login': 'dev', 'base_ref': 'main',
         'base_sha': SHA('a'), 'head_ref': f'feat-{n}', 'head_sha': SHA('b'), 'head_repo_full_name': 'acme/widgets', 'head_repo_deleted': False, 'is_fork': False,
         'updated_at': '2026-10-01T00:00:00Z'}
    d.update(o)
    return d


@app.get('/api/ai/v1/pulls')
def pulls(request: Request, repository_id: int, state: str = 'open', cursor: str | None = None):
    if (e := guard(request)):
        return e
    code = FLAGS.get('pulls_error')
    if code:
        return {'429': err(429, 'UPSTREAM_RATE_LIMIT', headers={'Retry-After': '9'}), '503': err(503, 'UPSTREAM_UNAVAILABLE'), '404': err(404, 'RESOURCE_NOT_FOUND')}[code]
    title = XSS if FLAGS.get('xss') else None
    items = [pr(7, state=state, title=title or 'PR 7: add feature'), pr(8, state=state, is_fork=True, draft=True, title='PR 8: wip')]
    if cursor:
        items = [pr(9, state=state, title='PR 9: older')]
    return ok({'items': items, 'next_cursor': None if cursor else 'page-2'})


FILES = [('main.py', None, 'modified', 3, 1, True), ('src/new_mod.py', None, 'added', 12, 0, True), ('assets/logo.bin', None, 'modified', 0, 0, False),
         ('.env', None, 'modified', 1, 0, True), ('src/b.py', 'src/a.py', 'renamed', 2, 2, True)]


@app.get('/api/ai/v1/pulls/{n}/files')
def pull_files(n: int, request: Request, repository_id: int, cursor: str | None = None):
    if (e := guard(request)):
        return e
    head = SHA('c') if FLAGS.get('head_changed') else SHA('b')
    return ok({'files': [{'path': p, 'old_path': o, 'change_type': c, 'additions': a, 'deletions': d, 'blob_sha': SHA('d'), 'has_patch': h}
                         for p, o, c, a, d, h in FILES], 'base_sha': SHA('a'), 'head_sha': head, 'pr_total_files': 8, 'inventory_coverage': 'complete', 'next_cursor': None})


# ── 입력 ──
def coverage(selected, read, analysis='none'):
    return {'inventory_coverage': 'complete', 'selected_content_coverage': 'complete' if read == selected else ('partial' if read else 'none'),
            'analysis_coverage': analysis, 'selected_files': selected, 'pr_total_files': 8, 'limitations': ['not_selected'] + (['secret_suspected'] if read < selected else [])}


def make_files(paths):
    out = []
    for p in paths:
        spec = next((f for f in FILES if f[0] == p), (p, None, 'modified', 1, 0, True))
        excluded = p == '.env' or p.endswith('.bin') or FLAGS.get('no_reviewable')
        out.append({'file_id': str(uuid4()), 'path': p, 'old_path': spec[1], 'change_type': spec[2], 'language': 'python', 'content_state': 'excluded' if excluded else 'read',
                    'exclusion_reason': ('secret_suspected' if p == '.env' else 'binary') if excluded else None, 'side_availability': 'BOTH', 'byte_count': 0 if excluded else 420,
                    'content_hash': None if excluded else 'e' * 64, 'hunks': [] if excluded else [{'old_start': 1, 'old_lines': 3, 'new_start': 1, 'new_lines': 4}]})
    return out


def input_view(i):
    i['gets'] += 1
    building = i['gets'] <= 2
    status = 'building' if building else ('expired' if FLAGS.get('expired') else 'ready')
    files = [] if building else i['files']
    read = sum(1 for f in files if f['content_state'] == 'read')
    t = STATE['cred']
    return {'input_id': i['id'], 'status': status, 'repository_id': i['repo'], 'pr_number': i['pr'], 'base_sha': SHA('a'), 'head_sha': SHA('b'), 'merge_base_sha': SHA('f'),
            'pr_state': 'open', 'pr_title': XSS if FLAGS.get('xss') else 'PR 7: add feature', 'pr_body': (XSS + '\nbody text') if FLAGS.get('xss') else 'Adds the feature',
            'pr_body_truncated': False, 'coverage': coverage(len(i['files']) or 1, read), 'files': files, 'manifest_hash': 'c' * 64, 'policy_version': 'code-transfer-v1',
            'provider_profile': t['profile'] if t else 'claude-opus-5-5-v1',
            'token_estimate': None if building else {'input_tokens': 6400, 'method': 'local-conservative-v1', 'profile': t['profile'] if t else 'claude-opus-5-5-v1', 'limit': 24000,
                                                     'within_limit': True, 'max_output_tokens': 4000, 'estimated_max_cost': '0.105600', 'currency': 'USD'},
            'valid_until': None if building else later(-1 if FLAGS.get('expired') else 24), 'expires_at': later(24 * 7), 'execution_valid': (not building) and not FLAGS.get('expired'),
            'raw_available': not building and not FLAGS.get('expired'), 'reviewable': read > 0 and not building,
            'prepare_job': {'job_id': str(uuid4()), 'status': 'running' if building else 'succeeded', 'stage': 'input_preparation' if building else None, 'error_code': None},
            'transfer': {'provider': 'anthropic', 'profile_id': t['profile'] if t else 'claude-opus-5-5-v1', 'model': 'claude-opus-5-5' if not t or '5-5-v1' in t['profile'] and 'opus' in t['profile'] else 'claude-sonnet-5-5',
                         'external_transfer_allowed': not FLAGS.get('transfer_off'), 'credential_configured': t is not None, 'credential_version': t['version'] if t else None,
                         'key_hint': t['hint'] if t else None}}


@app.post('/api/ai/v1/inputs')
async def create_input(request: Request):
    if (e := guard(request, True)):
        return e
    body = await request.json()
    iid = str(uuid4())
    STATE['inputs'][iid] = {'id': iid, 'repo': body['repository_id'], 'pr': body['pr_number'], 'files': make_files(body['selected_paths']), 'gets': 0}
    return ok(input_view(STATE['inputs'][iid]), 202)


@app.get('/api/ai/v1/inputs/{iid}')
def get_input(iid: str, request: Request):
    if (e := guard(request)):
        return e
    i = STATE['inputs'].get(iid)
    if not i:
        return err(404, 'RESOURCE_NOT_FOUND')
    return ok(input_view(i))


@app.get('/api/ai/v1/inputs/{iid}/files/{fid}')
def input_file(iid: str, fid: str, request: Request, side: str | None = None, start_line: int | None = None, end_line: int | None = None):
    if (e := guard(request)):
        return e
    i = STATE['inputs'].get(iid)
    f = next((x for x in (i or {'files': []})['files'] if x['file_id'] == fid), None) or (STATE['inputs'].get(iid) and STATE['inputs'][iid]['files'][0])
    if not i or not f:
        return err(404, 'RESOURCE_NOT_FOUND')
    if FLAGS.get('expired') and STATE['jobs']:
        return err(410, 'INPUT_EXPIRED')
    lines = [{'kind': 'context', 'left_line': 1, 'right_line': 1, 'text': 'a = 1'}, {'kind': 'removed', 'left_line': 2, 'right_line': None, 'text': 'b = 2'},
             {'kind': 'added', 'left_line': None, 'right_line': 2, 'text': XSS if FLAGS.get('xss') else 'b = 20'}, {'kind': 'context', 'left_line': 3, 'right_line': 3, 'text': 'c = 3'}]
    if side:
        key = 'left_line' if side == 'LEFT' else 'right_line'
        lines = [l for l in lines if l[key] is not None]
    return ok({'file_id': fid, 'path': f['path'], 'old_path': None, 'change_type': 'modified', 'language': 'python', 'side_availability': 'BOTH', 'content_state': 'read',
               'exclusion_reason': None, 'content_hash': 'e' * 64, 'lines': lines})


@app.delete('/api/ai/v1/inputs/{iid}')
def del_input(iid: str, request: Request):
    if (e := guard(request, True)):
        return e
    STATE['inputs'].pop(iid, None)
    return JSONResponse({'input_id': iid, 'status': 'expired'}, status_code=202)


# ── 작업·결과 ──
def job_view(j):
    out = j['outcome']
    j['polls'] += 1
    p = j['polls']
    status, stage, error, uncertain = 'queued', 'queued', None, False
    if j['status'] == 'cancelled':
        status, stage = 'cancelled', None
    elif p == 1:
        status, stage = 'queued', 'queued'
    elif p == 2:
        status, stage = 'running', 'input_preparation'
    elif p == 3:
        status, stage = 'running', 'provider_call'
    elif FLAGS.get('slow') and p < 12:
        status, stage = 'running', 'provider_call'
    elif j['status'] == 'cancel_requested':
        status, stage = 'cancelled', None
    else:
        stage = None
        status = {'success': 'succeeded', 'uncertain': 'failed', 'result_lost': 'failed', 'access': 'failed', 'keyless': 'failed', 'cancelled': 'cancelled'}[out]
        error = {'uncertain': 'OUTCOME_UNCERTAIN', 'result_lost': 'RESULT_LOST', 'access': 'ACCESS_REVOKED', 'keyless': 'CREDENTIAL_UNAVAILABLE'}.get(out)
        uncertain = out == 'uncertain'
        j['uncertain'] = uncertain
        if status == 'succeeded' and not j.get('settled'):
            j['settled'] = True
            STATE['spent'] += 0.07
    if j['status'] == 'cancel_requested' and status in ('queued', 'running'):
        status = 'cancel_requested'
    j['status'] = status if status in ('succeeded', 'failed', 'cancelled') else j['status'] if j['status'] == 'cancel_requested' else 'running'
    rid = j['result_id'] if status == 'succeeded' else None
    return {'job_id': j['id'], 'kind': j['kind'], 'status': status, 'stage': stage, 'coverage': coverage(len(j['files']) or 1, sum(1 for f in j['files'] if f['content_state'] == 'read'),
            'complete' if status == 'succeeded' else 'none'), 'result_id': rid, 'retry_after_seconds': None, 'cancel_requested': status == 'cancel_requested', 'error_code': error,
            'repository_id': j['repo'], 'input_id': j['input'], 'generation': j['generation'], 'provider': 'anthropic', 'model': 'claude-opus-5-5', 'outcome_uncertain': uncertain,
            'created_at': j['created'], 'finished_at': now() if status in ('succeeded', 'failed', 'cancelled') else None}


def new_job(body_kind, inp, generation=0):
    jid, rid = str(uuid4()), str(uuid4())
    i = STATE['inputs'][inp]
    j = {'id': jid, 'kind': body_kind, 'repo': i['repo'], 'input': inp, 'files': i['files'], 'status': 'queued', 'polls': 0, 'generation': generation, 'created': now(), 'result_id': rid,
         'outcome': FLAGS.get('outcome', 'success')}
    STATE['jobs'][jid] = j
    if body_kind == 'review':
        fid = next((f['file_id'] for f in i['files'] if f['content_state'] == 'read'), str(uuid4()))
        n = 0 if FLAGS.get('empty') else 3
        STATE['findings'][rid] = [{'finding_id': str(uuid4()), 'result_id': rid, 'source_file_id': fid, 'quote': XSS if FLAGS.get('xss') else 'b = 20', 'feedback': None,
                                   'finding': {'ordinal': k, 'title': (XSS if FLAGS.get('xss') and k == 1 else f'지적 {k}: 매직 넘버'), 'category': 'maintainability', 'priority': k,
                                               'condition': 'b가 읽힐 때', 'impact': '의미가 불분명함', 'evidence_ids': [fid], 'verification': 'b 사용처를 검색', 'suggestion': '상수로 이름 붙이기',
                                               'limitations': [], 'location': {'path': 'main.py', 'side': 'RIGHT', 'start_line': 2, 'end_line': 2, 'revision_id': str(uuid4())},
                                               'human_validation': 'unverified'}} for k in range(1, n + 1)]
    STATE['results'][rid] = {'result_id': rid, 'job_id': jid, 'kind': body_kind, 'repository_id': i['repo'], 'input_id': inp, 'payload_hash': 'f' * 64, 'structure_validation': 'passed',
                             'human_validation': 'unverified', 'provider': 'anthropic', 'model': 'claude-opus-5-5', 'finding_count': len(STATE['findings'].get(rid, [])),
                             'created_at': now(), 'expires_at': later(24 * 30)}
    return j


@app.post('/api/ai/v1/jobs')
async def create_job(request: Request):
    if (e := guard(request, True)):
        return e
    if FLAGS.get('fail_create'):
        return err({'PREVIEW_CHANGED': 409, 'CREDENTIAL_REQUIRED': 422, 'USER_LIMIT': 429, 'BUDGET_LIMIT': 429, 'INPUT_EXPIRED': 410}.get(FLAGS['fail_create'], 422), FLAGS['fail_create'])
    body = await request.json()
    if STATE['cred'] is None:
        return err(422, 'CREDENTIAL_REQUIRED')
    time.sleep(0.3 if FLAGS.get('slow_create') else 0)
    STATE['create_calls'] = STATE.get('create_calls', 0) + 1
    seen = STATE.setdefault('idem', {})
    key = request.headers.get('idempotency-key')
    if key in seen:
        return ok(job_view(STATE['jobs'][seen[key]]), 200)
    j = new_job(body['kind'], body['consent']['input_id'], 1 if body.get('force_regenerate') else 0)
    seen[key] = j['id']
    return ok(job_view(j), 202)


@app.get('/api/ai/v1/jobs')
def list_jobs(request: Request, repository_id: int, cursor: str | None = None, limit: int = 20):
    if (e := guard(request)):
        return e
    rows = [job_view(j) for j in STATE['jobs'].values() if j['repo'] == repository_id]
    return ok({'jobs': rows, 'next_cursor': None})


@app.get('/api/ai/v1/jobs/{jid}')
def get_job(jid: str, request: Request):
    if (e := guard(request)):
        return e
    j = STATE['jobs'].get(jid)
    if not j:
        return err(404, 'RESOURCE_NOT_FOUND')
    if FLAGS.get('job_404_after') and j['polls'] >= int(FLAGS['job_404_after']):
        return err(404, 'RESOURCE_NOT_FOUND')
    if FLAGS.get('job_429') and j['polls'] == 1:
        j['polls'] += 1
        return err(429, 'UPSTREAM_RATE_LIMIT', headers={'Retry-After': '2'})
    return ok(job_view(j))


@app.post('/api/ai/v1/jobs/{jid}/cancel')
def cancel(jid: str, request: Request):
    if (e := guard(request, True)):
        return e
    j = STATE['jobs'].get(jid)
    if not j:
        return err(404, 'RESOURCE_NOT_FOUND')
    j['status'] = 'cancel_requested' if j['polls'] >= 2 else 'cancelled'
    return ok(job_view(j), 202 if j['status'] == 'cancel_requested' else 200)


@app.post('/api/ai/v1/jobs/{jid}/retry')
async def retry(jid: str, request: Request):
    if (e := guard(request, True)):
        return e
    j = STATE['jobs'].get(jid)
    if not j:
        return err(404, 'RESOURCE_NOT_FOUND')
    FLAGS['outcome'] = 'success'
    return ok(job_view(new_job(j['kind'], j['input'], j['generation'] + 1)), 202)


@app.delete('/api/ai/v1/jobs/{jid}')
def delete_job(jid: str, request: Request):
    if (e := guard(request, True)):
        return e
    STATE['jobs'].pop(jid, None)
    return {'job_id': jid, 'status': 'deleted'}


@app.get('/api/ai/v1/results/{rid}')
def get_result(rid: str, request: Request):
    if (e := guard(request)):
        return e
    r = STATE['results'].get(rid)
    if not r:
        return err(404, 'RESOURCE_NOT_FOUND')
    cov = coverage(3, 2, 'complete')
    payload = {'kind': r['kind'], 'schema_version': 'x', 'findings': []} if r['kind'] == 'review' else {
        'kind': 'summary', 'overview': XSS if FLAGS.get('xss') else 'main.py의 b 값이 바뀝니다.', 'author_intent': 'PR 설명: 기능 추가', 'unknowns': ['호출부는 보이지 않음'],
        'file_changes': [{'path': 'main.py', 'file_id': next(iter(STATE['inputs'].values()))['files'][0]['file_id'], 'summary': 'b를 20으로 변경', 'evidence_ids': []}]}
    return ok({**r, 'payload': payload, 'coverage': cov})


@app.get('/api/ai/v1/results/{rid}/findings')
def get_findings(rid: str, request: Request, cursor: str | None = None, limit: int = 20):
    if (e := guard(request)):
        return e
    return ok({'result_id': rid, 'findings': STATE['findings'].get(rid, []), 'next_cursor': None})


@app.get('/api/ai/v1/history/snapshots')
def history_snapshots(request: Request, repository_id: int = 0):
    if (e := guard(request)):
        return e
    return ok({'repository_id': repository_id, 'snapshots': [
        {'snapshot_id': 7, 'target_sha': 'a' * 40, 'analyzed_at': now(), 'window_start': None, 'window_end': now(), 'formula_version': 'f', 'eligible': True, 'reasons': [], 'reason_texts': []},
        {'snapshot_id': 3, 'target_sha': 'b' * 40, 'analyzed_at': now(), 'window_start': None, 'window_end': now(), 'formula_version': 'old', 'eligible': False,
         'reasons': ['formula_unsupported'], 'reason_texts': ['지원하지 않는 계산 버전']}]})


def history_row(rid: str, sid: int, excluded: bool):
    base = {'evidence_id': '00000000-0000-4000-8000-00000000000%d' % sid, 'result_id': rid, 'snapshot_id': sid, 'as_of': now(), 'created_at': now(), 'payload_hash': 'e' * 64}
    if excluded:
        return {**base, 'status': 'excluded', 'exclusion_reasons': ['formula_unsupported'], 'evidence': {'basis': {}, 'paths': [], 'data_quality': {}, 'notes': ['검증하지 못한 snapshot이라 근거로 쓰지 않았습니다.', '지원하지 않는 계산 버전']}}
    return {**base, 'status': 'ok', 'exclusion_reasons': [], 'evidence': {
        'basis': {'snapshot_id': sid, 'target_sha': 'a' * 40}, 'data_quality': {'commit_count': 42, 'unidentified_commits': 5, 'contributor_count': 4, 'merged_pr_count': 20, 'co_change_unit': 'pull_request'},
        'notes': ['이 PR의 기준과 snapshot 기준이 같습니다.', '함께 바뀐 파일은 검토 후보일 뿐 누락이나 오류가 아닙니다.'],
        'paths': [{'path': 'main.py', 'match': 'path', 'status': 'observed', 'observations': {'change_freq': 12, 'churn': 340, 'bugfix_count': 4},
                   'ownership': {'observation_count': 12, 'contributor_count': 3, 'top_owner_share': 0.55},
                   'co_change': [{'path': 'tests/test_main.py', 'co_change_count': 6, 'changed_in_pr': False, 'low_support': False}], 'limitations': []}]}}


@app.get('/api/ai/v1/results/{rid}/history')
def get_history(rid: str, request: Request):
    if (e := guard(request)):
        return e
    return ok({'result_id': rid, 'evidence': [history_row(rid, s, x) for s, x in STATE.setdefault('history', {}).get(rid, [])]})


@app.post('/api/ai/v1/results/{rid}/history')
async def attach_history(rid: str, request: Request):
    if (e := guard(request, True)):
        return e
    body = await request.json()
    STATE.setdefault('history', {}).setdefault(rid, []).append((body['snapshot_id'], body['snapshot_id'] == 3))
    return ok(history_row(rid, body['snapshot_id'], body['snapshot_id'] == 3), 201)


@app.put('/api/ai/v1/findings/{fid}/feedback')
async def feedback(fid: str, request: Request):
    if (e := guard(request, True)):
        return e
    body = await request.json()
    for items in STATE['findings'].values():
        for f in items:
            if f['finding_id'] == fid:
                cur = (f['feedback'] or {}).get('version', 0)
                if FLAGS.get('conflict') or cur != body['expected_version']:
                    FLAGS.pop('conflict', None)
                    return err(409, 'VERSION_CONFLICT', details={'current_version': cur})
                f['feedback'] = {'verdict': body['verdict'], 'note': body.get('note'), 'version': cur + 1, 'updated_at': now()}
                f['finding']['human_validation'] = body['verdict']
                return ok(f['feedback'])
    return err(404, 'RESOURCE_NOT_FOUND')


# ── 인수인계(S12) 모의: 실제 서버의 버전 충돌·미확인 기본값·편집본 보존 규칙만 흉내 낸다 ──
HO_KEYS = ['assignee', 'description', 'run_method', 'verify_method', 'joint_review', 'receiver_run']
HO_LABEL = {'assignee': '인수 담당자', 'description': '기능 설명', 'run_method': '실행 방법', 'verify_method': '수정 후 확인 절차', 'joint_review': '공동 검토', 'receiver_run': '인수자 실행 확인'}
HO_STATUS = {'unknown': '미확인', 'needed': '준비 필요', 'ready': '준비 완료 (작성자 기록)'}


def ho():
    return STATE.setdefault('ho', {'targets': {}, 'preps': {}, 'gens': {}, 'docs': {}})


def ho_target_view(t):
    prep = ho()['preps'].get(t['target_id'], [])
    last = prep[-1] if prep else None
    return {**t, 'preparation_version': len(prep), 'needs_reconfirm': bool(last and last['target_version'] < t['scope_version']),
            'has_document': t['target_id'] in ho()['docs']}


def ho_prep_view(t):
    prep = ho()['preps'].get(t['target_id'], [])
    last = prep[-1] if prep else None
    items = []
    for k in HO_KEYS:
        it = (last['items'].get(k) if last else None) or {'status': 'unknown', 'text': None, 'link': None, 'updated_version': None, 'updated_at': None}
        items.append({'key': k, 'label': HO_LABEL[k], 'status': it['status'], 'status_label': HO_STATUS[it['status']], 'text': it['text'], 'link': it['link'],
                      'updated_version': it['updated_version'], 'updated_at': it['updated_at']})
    return {'target_id': t['target_id'], 'version': len(prep), 'target_version': last['target_version'] if last else None,
            'needs_reconfirm': bool(last and last['target_version'] < t['scope_version']), 'items': items, 'created_at': last['created_at'] if last else None}


def ho_doc_view(t):
    d = ho()['docs'].get(t['target_id'])
    if not d:
        return None
    gens = ho()['gens'].get(t['target_id'], [])
    base = next(g for g in gens if g['generation_id'] == d['base_generation_id'])
    newest = gens[-1]
    stale = [r for r in FLAGS.get('ho_stale', '').split(',') if r]
    return {'document_id': d['document_id'], 'target_id': t['target_id'], 'version': d['version'], 'edited': d['edited'], 'markdown': d['markdown'],
            'base_generation_id': base['generation_id'], 'base_generation_number': base['number'], 'stale_reasons': stale,
            'newer_generation_id': newest['generation_id'] if newest['number'] > base['number'] else None,
            'newer_generation_number': newest['number'] if newest['number'] > base['number'] else None, 'updated_at': d['updated_at']}


def ho_markdown(t, number):
    prep = ho_prep_view(t)
    lines = [f"# 인수인계 초안: {t['name']}", '', '- AI가 만든 설명과 코드 리뷰 지적은 사람이 검증하기 전에는 확정 사실이 아닙니다.', '', '## 6. 준비 상태 (사용자 입력)', '']
    lines += [f"- **{i['label']}** — {i['status_label']}" for i in prep['items']]
    lines += ['', f'원본 #{number}']
    return '\n'.join(lines) + '\n'


def ho_find(tid):
    return ho()['targets'].get(tid)


@app.get('/api/ai/v1/handover-targets')
def ho_list(request: Request, repository_id: int = 0):
    if (e := guard(request)):
        return e
    return ok({'targets': [ho_target_view(t) for t in ho()['targets'].values() if t['repository_id'] == repository_id]})


@app.post('/api/ai/v1/handover-targets')
async def ho_create(request: Request):
    if (e := guard(request, True)):
        return e
    b = await request.json()
    tid = str(uuid4())
    t = {'target_id': tid, 'repository_id': b['repository_id'], 'name': b['name'], 'purpose': b.get('purpose'), 'giver_name': b.get('giver_name'),
         'receiver_name': b.get('receiver_name'), 'selected_paths': b['selected_paths'], 'snapshot_id': b.get('snapshot_id'), 'version': 1, 'scope_version': 1,
         'created_at': now(), 'updated_at': now()}
    ho()['targets'][tid] = t
    return ok(ho_target_view(t), 201)


@app.get('/api/ai/v1/handover-targets/{tid}')
def ho_get(tid: str, request: Request):
    if (e := guard(request)):
        return e
    t = ho_find(tid)
    return ok(ho_target_view(t)) if t else err(404, 'RESOURCE_NOT_FOUND')


@app.patch('/api/ai/v1/handover-targets/{tid}')
async def ho_patch(tid: str, request: Request):
    if (e := guard(request, True)):
        return e
    t = ho_find(tid)
    if not t:
        return err(404, 'RESOURCE_NOT_FOUND')
    b = await request.json()
    if b['expected_version'] != t['version']:
        return err(409, 'VERSION_CONFLICT', details={'reason': 'TARGET_VERSION', 'current_version': t['version']})
    scope = False
    for k in ('name', 'purpose', 'giver_name', 'receiver_name'):
        if b.get(k) is not None:
            t[k] = b[k] or None
    if b.get('selected_paths') is not None and b['selected_paths'] != t['selected_paths']:
        t['selected_paths'], scope = b['selected_paths'], True
    if b.get('clear_snapshot') and t['snapshot_id'] is not None:
        t['snapshot_id'], scope = None, True
    elif b.get('snapshot_id') and b['snapshot_id'] != t['snapshot_id']:
        t['snapshot_id'], scope = b['snapshot_id'], True
    t['version'] += 1
    if scope:
        t['scope_version'] = t['version']
    t['updated_at'] = now()
    return ok(ho_target_view(t))


@app.delete('/api/ai/v1/handover-targets/{tid}')
def ho_delete(tid: str, request: Request):
    if (e := guard(request, True)):
        return e
    ho()['targets'].pop(tid, None)
    return ok({'target_id': tid, 'status': 'deleted'})


@app.get('/api/ai/v1/handover-targets/{tid}/preparation')
def ho_prep_get(tid: str, request: Request):
    if (e := guard(request)):
        return e
    t = ho_find(tid)
    return ok(ho_prep_view(t)) if t else err(404, 'RESOURCE_NOT_FOUND')


@app.put('/api/ai/v1/handover-targets/{tid}/preparation')
async def ho_prep_put(tid: str, request: Request):
    if (e := guard(request, True)):
        return e
    t = ho_find(tid)
    if not t:
        return err(404, 'RESOURCE_NOT_FOUND')
    b = await request.json()
    preps = ho()['preps'].setdefault(tid, [])
    if b['confirmed_target_version'] != t['version']:
        return err(409, 'VERSION_CONFLICT', details={'reason': 'TARGET_CHANGED', 'current_target_version': t['version']})
    if b['expected_version'] != len(preps) or FLAGS.get('ho_conflict'):
        FLAGS.pop('ho_conflict', None)
        return err(409, 'VERSION_CONFLICT', details={'reason': 'PREPARATION_VERSION', 'current_version': len(preps)})
    items = {}
    for k in HO_KEYS:
        it = b['items'].get(k) or {'status': 'unknown'}
        if it['status'] == 'unknown' and (it.get('text') or it.get('link')):
            return err(422, 'INVALID_SELECTION', message='미확인 항목에는 내용을 둘 수 없습니다.', details={'item': k})
        items[k] = {'status': it['status'], 'text': it.get('text'), 'link': it.get('link'), 'updated_version': len(preps) + 1 if it['status'] != 'unknown' else None, 'updated_at': now() if it['status'] != 'unknown' else None}
    preps.append({'version': len(preps) + 1, 'target_version': t['version'], 'items': items, 'created_at': now()})
    return ok(ho_prep_view(t))


@app.post('/api/ai/v1/handover-targets/{tid}/generations')
async def ho_generate(tid: str, request: Request):
    if (e := guard(request, True)):
        return e
    t = ho_find(tid)
    if not t:
        return err(404, 'RESOURCE_NOT_FOUND')
    b = await request.json()
    gens = ho()['gens'].setdefault(tid, [])
    npreps = len(ho()['preps'].get(tid, []))
    if b['expected_target_version'] != t['version']:
        return err(409, 'VERSION_CONFLICT', details={'reason': 'TARGET_CHANGED', 'current_target_version': t['version']})
    if b['expected_preparation_version'] != npreps:
        return err(409, 'VERSION_CONFLICT', details={'reason': 'PREPARATION_CHANGED', 'current_version': npreps})
    if gens and gens[-1]['preparation_version'] == npreps and gens[-1]['target_version'] == t['version'] and not b.get('force'):
        g = gens[-1]
        return ok({'generation': {**g, 'reused': True}, 'document_id': ho()['docs'][tid]['document_id'], 'document_updated': False})
    g = {'generation_id': str(uuid4()), 'target_id': tid, 'number': len(gens) + 1, 'target_version': t['version'], 'preparation_version': npreps,
         'template_version': 'handover-v1', 'sources': {}, 'stale_reasons': [], 'markdown': ho_markdown(t, len(gens) + 1), 'created_at': now(), 'reused': False}
    gens.append(g)
    d = ho()['docs'].get(tid)
    updated = False
    if d is None:
        d = ho()['docs'][tid] = {'document_id': str(uuid4()), 'markdown': g['markdown'], 'version': 1, 'edited': False, 'base_generation_id': g['generation_id'], 'updated_at': now()}
        updated = True
    elif not d['edited']:
        d.update(markdown=g['markdown'], version=d['version'] + 1, base_generation_id=g['generation_id'], updated_at=now())
        updated = True
    return ok({'generation': g, 'document_id': d['document_id'], 'document_updated': updated}, 201)


@app.get('/api/ai/v1/handover-targets/{tid}/document')
def ho_doc_get(tid: str, request: Request):
    if (e := guard(request)):
        return e
    t = ho_find(tid)
    view = ho_doc_view(t) if t else None
    return ok(view) if view else err(404, 'RESOURCE_NOT_FOUND')


def ho_doc_by_id(did):
    for tid, d in ho()['docs'].items():
        if d['document_id'] == did:
            return ho_find(tid), d
    return None, None


@app.put('/api/ai/v1/handover-documents/{did}')
async def ho_doc_put(did: str, request: Request):
    if (e := guard(request, True)):
        return e
    t, d = ho_doc_by_id(did)
    if not d:
        return err(404, 'RESOURCE_NOT_FOUND')
    b = await request.json()
    if b['expected_version'] != d['version']:
        return err(409, 'VERSION_CONFLICT', details={'reason': 'DOCUMENT_VERSION', 'current_version': d['version']})
    if '<script' in b['markdown'] or 'javascript:' in b['markdown']:
        return err(422, 'INVALID_SELECTION', message='문서에 저장할 수 없는 내용이 있습니다.', details={'issues': ['raw_html']})
    d.update(markdown=b['markdown'], version=d['version'] + 1, edited=True, updated_at=now())
    return ok(ho_doc_view(t))


@app.post('/api/ai/v1/handover-documents/{did}/adopt')
async def ho_adopt(did: str, request: Request):
    if (e := guard(request, True)):
        return e
    t, d = ho_doc_by_id(did)
    if not d:
        return err(404, 'RESOURCE_NOT_FOUND')
    b = await request.json()
    if b['expected_version'] != d['version']:
        return err(409, 'VERSION_CONFLICT', details={'reason': 'DOCUMENT_VERSION', 'current_version': d['version']})
    g = next(x for x in ho()['gens'][t['target_id']] if x['generation_id'] == b['generation_id'])
    d.update(markdown=g['markdown'], base_generation_id=g['generation_id'], version=d['version'] + 1, edited=False, updated_at=now())
    return ok(ho_doc_view(t))


@app.get('/api/ai/v1/handover-documents/{did}/export')
def ho_export(did: str, request: Request):
    if (e := guard(request)):
        return e
    t, d = ho_doc_by_id(did)
    if not d:
        return err(404, 'RESOURCE_NOT_FOUND')
    from fastapi.responses import Response
    return Response(content=(d['markdown'] + '\n---\n\n(모의 내보내기)\n').encode('utf-8'), media_type='text/markdown; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="handover-{did[:8]}-v{d["version"]}.md"', 'Cache-Control': 'private, no-store'})


# ── 팀(S13) 모의: 역할·구성원·공유·보고서 차단 규칙의 모양만 흉내 낸다 ──
def tm():
    return STATE.setdefault('tm', {'teams': {}, 'members': {}, 'repos': {}, 'shares': {}, 'reports': {}})


def tm_role(tid):
    m = next((x for x in tm()['members'].get(tid, []) if x['user_id'] == 'u1'), None)
    return m


def tm_team_view(tid):
    t = tm()['teams'][tid]
    me = tm_role(tid)
    members = tm()['members'][tid]
    return {'team_id': tid, 'name': t['name'], 'role': me['role'], 'status': me['status'], 'visibility_epoch': t['epoch'],
            'member_count': len([m for m in members if m['status'] == 'active']) if me['status'] == 'active' else 0, 'created_at': t['created_at']}


def tm_need(tid, minimum='viewer'):
    me = tm_role(tid) if tid in tm()['teams'] else None
    if me is None or me['status'] != 'active':
        return None, err(404, 'RESOURCE_NOT_FOUND')
    rank = {'viewer': 1, 'member': 2, 'admin': 3}
    if rank[me['role']] < rank[minimum]:
        return None, err(403, 'PERMISSION_DENIED', details={'required_role': minimum, 'role': me['role']})
    return me, None


@app.get('/mock/team_seed')
def tm_seed(request: Request):
    """팀 하나(내 역할은 role 쿼리, 기본 admin)와 받은 초대 하나를 만든다."""
    role = request.query_params.get('role', 'admin')
    tm().update(teams={}, members={}, repos={}, shares={}, reports={})
    tid = str(uuid4())
    tm()['teams'][tid] = {'name': '점검 팀', 'epoch': 1, 'created_at': now()}
    tm()['members'][tid] = [{'user_id': 'u1', 'github_login': 'tester', 'display_name': None, 'role': role, 'status': 'active', 'created_at': now()},
                            {'user_id': 'u2', 'github_login': 'bo', 'display_name': 'Bo', 'role': 'member', 'status': 'active', 'created_at': now()}]
    tm()['repos'][tid] = [{'repository_id': 1, 'name': 'widgets', 'url': 'https://github.com/acme/widgets', 'created_at': now()}]
    inv = str(uuid4())
    tm()['teams'][inv] = {'name': '초대받은 팀', 'epoch': 1, 'created_at': now()}
    tm()['members'][inv] = [{'user_id': 'u1', 'github_login': 'tester', 'display_name': None, 'role': 'viewer', 'status': 'invited', 'created_at': now()}]
    tm()['repos'][inv] = []
    return {'team': tid, 'invited': inv}


@app.get('/api/ai/v1/teams')
def tm_list(request: Request):
    if (e := guard(request)):
        return e
    return ok({'teams': [tm_team_view(t) for t in tm()['teams'] if tm_role(t)]})


@app.post('/api/ai/v1/teams')
async def tm_create(request: Request):
    if (e := guard(request, True)):
        return e
    b = await request.json()
    tid = str(uuid4())
    tm()['teams'][tid] = {'name': b['name'], 'epoch': 1, 'created_at': now()}
    tm()['members'][tid] = [{'user_id': 'u1', 'github_login': 'tester', 'display_name': None, 'role': 'admin', 'status': 'active', 'created_at': now()}]
    tm()['repos'][tid] = []
    return ok(tm_team_view(tid), 201)


@app.get('/api/ai/v1/teams/{tid}')
def tm_get(tid: str, request: Request):
    if (e := guard(request)):
        return e
    _, bad = tm_need(tid)
    return bad or ok(tm_team_view(tid))


@app.patch('/api/ai/v1/teams/{tid}')
async def tm_rename(tid: str, request: Request):
    if (e := guard(request, True)):
        return e
    _, bad = tm_need(tid, 'admin')
    if bad:
        return bad
    tm()['teams'][tid]['name'] = (await request.json())['name']
    return ok(tm_team_view(tid))


@app.delete('/api/ai/v1/teams/{tid}')
def tm_delete(tid: str, request: Request):
    if (e := guard(request, True)):
        return e
    _, bad = tm_need(tid, 'admin')
    if bad:
        return bad
    tm()['teams'].pop(tid)
    return ok({'team_id': tid, 'status': 'deleted'})


@app.get('/api/ai/v1/teams/{tid}/members')
def tm_members(tid: str, request: Request):
    if (e := guard(request)):
        return e
    me, bad = tm_need(tid)
    if bad:
        return bad
    return ok({'members': [m for m in tm()['members'][tid] if m['status'] == 'active' or me['role'] == 'admin']})


@app.post('/api/ai/v1/teams/{tid}/members')
async def tm_invite(tid: str, request: Request):
    if (e := guard(request, True)):
        return e
    _, bad = tm_need(tid, 'admin')
    if bad:
        return bad
    b = await request.json()
    if b['github_login'] == 'nobody':
        return err(404, 'RESOURCE_NOT_FOUND')
    m = {'user_id': 'u-' + b['github_login'], 'github_login': b['github_login'], 'display_name': None, 'role': b['role'], 'status': 'invited', 'created_at': now()}
    tm()['members'][tid].append(m)
    return ok(m, 201)


@app.patch('/api/ai/v1/teams/{tid}/members/{uid}')
async def tm_role_patch(tid: str, uid: str, request: Request):
    if (e := guard(request, True)):
        return e
    _, bad = tm_need(tid, 'admin')
    if bad:
        return bad
    role = (await request.json())['role']
    m = next(x for x in tm()['members'][tid] if x['user_id'] == uid)
    if m['role'] == 'admin' and role != 'admin' and len([x for x in tm()['members'][tid] if x['role'] == 'admin' and x['status'] == 'active']) <= 1:
        return err(409, 'VERSION_CONFLICT', details={'reason': 'LAST_ADMIN'})
    m['role'] = role
    if role == 'viewer':
        tm()['shares'][tid] = [s for s in tm()['shares'].get(tid, []) if s['shared_by'] != uid]
    tm()['teams'][tid]['epoch'] += 1
    return ok(m)


@app.delete('/api/ai/v1/teams/{tid}/members/{uid}')
def tm_remove(tid: str, uid: str, request: Request):
    if (e := guard(request, True)):
        return e
    if uid != 'u1':
        _, bad = tm_need(tid, 'admin')
        if bad:
            return bad
    members = tm()['members'].get(tid, [])
    m = next((x for x in members if x['user_id'] == uid), None)
    if not m:
        return err(404, 'RESOURCE_NOT_FOUND')
    if m['role'] == 'admin' and m['status'] == 'active' and len([x for x in members if x['role'] == 'admin' and x['status'] == 'active']) <= 1:
        return err(409, 'VERSION_CONFLICT', details={'reason': 'LAST_ADMIN'})
    members.remove(m)
    tm()['shares'][tid] = [s for s in tm()['shares'].get(tid, []) if s['shared_by'] != uid]
    tm()['teams'][tid]['epoch'] += 1
    return ok({'team_id': tid, 'user_id': uid, 'status': 'removed'})


@app.post('/api/ai/v1/teams/{tid}/invitation/accept')
def tm_accept(tid: str, request: Request):
    if (e := guard(request, True)):
        return e
    m = tm_role(tid) if tid in tm()['teams'] else None
    if not m or m['status'] != 'invited':
        return err(404, 'RESOURCE_NOT_FOUND')
    m['status'] = 'active'
    return ok(tm_team_view(tid))


@app.get('/api/ai/v1/teams/{tid}/repositories')
def tm_repos(tid: str, request: Request):
    if (e := guard(request)):
        return e
    _, bad = tm_need(tid)
    return bad or ok({'repositories': tm()['repos'][tid]})


@app.post('/api/ai/v1/teams/{tid}/repositories')
async def tm_repo_add(tid: str, request: Request):
    if (e := guard(request, True)):
        return e
    _, bad = tm_need(tid, 'admin')
    if bad:
        return bad
    rid = (await request.json())['repository_id']
    r = {'repository_id': rid, 'name': 'repo-%d' % rid, 'url': None, 'created_at': now()}
    tm()['repos'][tid].append(r)
    return ok(r, 201)


@app.delete('/api/ai/v1/teams/{tid}/repositories/{rid}')
def tm_repo_del(tid: str, rid: int, request: Request):
    if (e := guard(request, True)):
        return e
    _, bad = tm_need(tid, 'admin')
    if bad:
        return bad
    tm()['repos'][tid] = [r for r in tm()['repos'][tid] if r['repository_id'] != rid]
    tm()['shares'][tid] = [s for s in tm()['shares'].get(tid, []) if s['repository_id'] != rid]
    tm()['teams'][tid]['epoch'] += 1
    return ok({'team_id': tid, 'repository_id': rid, 'status': 'removed'})


@app.get('/api/ai/v1/teams/{tid}/shares')
def tm_shares(tid: str, request: Request):
    if (e := guard(request)):
        return e
    _, bad = tm_need(tid)
    return bad or ok({'shares': tm()['shares'].setdefault(tid, [])})


@app.post('/api/ai/v1/teams/{tid}/shares')
async def tm_share_add(tid: str, request: Request):
    if (e := guard(request, True)):
        return e
    _, bad = tm_need(tid, 'member')
    if bad:
        return bad
    b = await request.json()
    s = {'share_id': str(uuid4()), 'team_id': tid, 'repository_id': 1, 'resource_type': b['resource_type'], 'resource_id': b['resource_id'], 'shared_by': 'u1',
         'shared_by_login': 'tester', 'status': 'active', 'created_at': now()}
    tm()['shares'].setdefault(tid, []).append(s)
    return ok(s, 201)


@app.delete('/api/ai/v1/teams/{tid}/shares/{sid}')
def tm_share_del(tid: str, sid: str, request: Request):
    if (e := guard(request, True)):
        return e
    me, bad = tm_need(tid)
    if bad:
        return bad
    s = next((x for x in tm()['shares'].get(tid, []) if x['share_id'] == sid), None)
    if not s:
        return err(404, 'RESOURCE_NOT_FOUND')
    if s['shared_by'] != 'u1' and me['role'] != 'admin':
        return err(403, 'PERMISSION_DENIED', details={'role': me['role']})
    tm()['shares'][tid].remove(s)
    tm()['teams'][tid]['epoch'] += 1
    return ok({'share_id': sid, 'status': 'unshared'})


@app.get('/api/ai/v1/teams/{tid}/shares/{sid}/content')
def tm_share_content(tid: str, sid: str, request: Request):
    if (e := guard(request)):
        return e
    _, bad = tm_need(tid)
    if bad:
        return bad
    s = next((x for x in tm()['shares'].get(tid, []) if x['share_id'] == sid), None)
    if not s:
        return err(404, 'RESOURCE_NOT_FOUND')
    if FLAGS.get('share_outdated'):
        return err(422, 'INVALID_STATE', details={'reason': 'share_outdated'})
    content = ({'findings': [{'ordinal': 1, 'title': XSS if FLAGS.get('xss') else 'SQL 문자열 결합', 'category': 'security', 'verdict': 'valid', 'condition': '사용자 입력이 쿼리에 결합됨',
                              'impact': '주입 가능', 'location': {'path': 'main.py', 'start_line': 2}}], 'history': [], 'note': '판정은 작성자의 판단입니다.'}
               if s['resource_type'] == 'review_result' else
               {'name': '로그인 인계', 'selected_paths': ['a.py'], 'items': [{'key': 'assignee', 'label': '인수 담당자', 'status': 'ready', 'status_label': '준비 완료 (작성자 기록)'}], 'note': '작성자가 기록한 상태입니다.'})
    return ok({'share_id': sid, 'resource_type': s['resource_type'], 'repository_id': s['repository_id'], 'content': content})


@app.get('/api/ai/v1/teams/{tid}/reports')
def tm_reports(tid: str, request: Request):
    if (e := guard(request)):
        return e
    _, bad = tm_need(tid)
    if bad:
        return bad
    epoch = tm()['teams'][tid]['epoch']
    return ok({'reports': [{**{k: r[k] for k in ('report_id', 'team_id', 'repository_ids', 'created_by', 'visibility_epoch', 'created_at')}, 'visibility_current': r['visibility_epoch'] == epoch}
                           for r in tm()['reports'].get(tid, [])]})


@app.post('/api/ai/v1/teams/{tid}/reports')
async def tm_report_add(tid: str, request: Request):
    if (e := guard(request, True)):
        return e
    _, bad = tm_need(tid, 'member')
    if bad:
        return bad
    b = await request.json()
    r = {'report_id': str(uuid4()), 'team_id': tid, 'repository_ids': b['repository_ids'], 'created_by': 'u1', 'visibility_epoch': tm()['teams'][tid]['epoch'],
         'template_version': 'team-report-v1', 'source_manifest': {}, 'created_at': now(), 'stale_reasons': [],
         'payload': {'totals': {'valid': 1, 'unverified': 2, 'uncertain': 0, 'false_positive': 1}},
         'markdown': '# 팀 점검 보고서\n\n- 문제 집계는 사람이 유효로 판정한 지적만 셉니다.\n'}
    tm()['reports'].setdefault(tid, []).append(r)
    return ok(r, 201)


@app.get('/api/ai/v1/teams/{tid}/reports/{rid}')
def tm_report_get(tid: str, rid: str, request: Request):
    if (e := guard(request)):
        return e
    _, bad = tm_need(tid)
    if bad:
        return bad
    r = next((x for x in tm()['reports'].get(tid, []) if x['report_id'] == rid), None)
    if not r:
        return err(404, 'RESOURCE_NOT_FOUND')
    reasons = []
    if r['visibility_epoch'] != tm()['teams'][tid]['epoch']:
        reasons.append('visibility_changed')
    if FLAGS.get('repo_lost'):
        reasons.append('repository_access_lost')
    if reasons:
        return err(403, 'REPORT_BLOCKED', details={'reasons': reasons})
    return ok({**r, 'stale_reasons': ['feedback_changed'] if FLAGS.get('report_stale') else []})


@app.delete('/api/ai/v1/teams/{tid}/reports/{rid}')
def tm_report_del(tid: str, rid: str, request: Request):
    if (e := guard(request, True)):
        return e
    _, bad = tm_need(tid, 'member')
    if bad:
        return bad
    tm()['reports'][tid] = [r for r in tm()['reports'].get(tid, []) if r['report_id'] != rid]
    return ok({'report_id': rid, 'status': 'deleted'})


app.mount('/', StaticFiles(directory=str(ROOT / 'frontend'), html=True), name='frontend')

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]) if len(sys.argv) > 1 else 8765, log_level='warning')
