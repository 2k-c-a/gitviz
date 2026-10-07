/* ── AI API 클라이언트 (/api/ai/v1) ──
   - 변경 요청에는 CSRF 토큰(X-CSRF-Token)을 붙인다. 토큰이 틀리면(403 CSRF_FAILED) 한 번 새로 받아 같은 요청을 다시 보낸다.
   - 접수 요청(POST /inputs·/jobs·/retry)은 호출하는 쪽이 정한 Idempotency-Key를 붙인다.
   - 오류는 AiApiError 하나로 정규화한다: status(네트워크 오류는 0), code, details, retryAfter(ms), requestId.
     HTML·깨진 JSON 같은 비정상 응답은 BAD_RESPONSE다. 요청·응답 본문과 API key는 로그에 남기지 않는다.
   - 저장소·작업은 항상 호출자가 인자로 준다. 기본 저장소나 저장된 값은 쓰지 않는다. */
import { parseRetryAfter } from './poll.js';

export class AiApiError extends Error {
  constructor(message, { status = 0, code = null, details = {}, retryable = false, requestId = null, retryAfter = null } = {}) {
    super(message);
    this.name = 'AiApiError';
    this.status = status;
    this.code = code;
    this.details = details || {};
    this.retryable = retryable;
    this.requestId = requestId;
    this.retryAfter = retryAfter;       // ms
  }
}

export function newIdempotencyKey() {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  const bytes = new Uint8Array(16);
  if (c && c.getRandomValues) c.getRandomValues(bytes);
  else for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

const BASE = '/api/ai/v1';

function query(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}

export function createAiClient({ fetchImpl = (u, o) => fetch(u, o), now = () => Date.now() } = {}) {
  let csrf = null;

  async function loadCsrf(signal) {
    let res;
    try {
      res = await fetchImpl('/api/auth/csrf', { credentials: 'same-origin', cache: 'no-store', signal });
    } catch (e) {
      if (e?.name === 'AbortError') throw e;
      throw new AiApiError('서버에 연결할 수 없습니다.', { status: 0, code: 'NETWORK', retryable: true });
    }
    if (res.status === 401) throw new AiApiError('로그인이 필요합니다.', { status: 401, code: 'AUTH_REQUIRED' });
    let body = null;
    try { body = await res.json(); } catch { /* 아래에서 처리 */ }
    if (!res.ok || !body || typeof body.token !== 'string' || !body.token) {
      throw new AiApiError('요청 토큰을 받지 못했습니다.', { status: res.status, code: res.ok ? 'BAD_RESPONSE' : null });
    }
    csrf = body.token;
    return csrf;
  }

  async function parse(res) {
    let body;
    try { body = await res.json(); } catch { body = undefined; }
    if (res.ok) {
      if (body === undefined || body === null || typeof body !== 'object' || Array.isArray(body)) {
        throw new AiApiError('서버 응답을 해석할 수 없습니다.', { status: res.status, code: 'BAD_RESPONSE' });
      }
      return body;
    }
    const err = body && typeof body === 'object' ? body.error : null;
    const header = res.headers && typeof res.headers.get === 'function' ? res.headers.get('Retry-After') : null;
    if (err && typeof err.code === 'string') {
      throw new AiApiError(String(err.message || ''), {
        status: res.status, code: err.code, details: err.details || {}, retryable: !!err.retryable,
        requestId: err.request_id || null, retryAfter: parseRetryAfter(header, now()),
      });
    }
    /* ErrorEnvelope가 아닌 응답(프록시 HTML, FastAPI 기본 오류 등) */
    throw new AiApiError('서버가 예상하지 못한 응답을 보냈습니다.', {
      status: res.status, code: res.status === 401 ? 'AUTH_REQUIRED' : 'BAD_RESPONSE', retryAfter: parseRetryAfter(header, now()),
    });
  }

  async function send(method, path, { params, body, signal, idempotencyKey } = {}, retried = false) {
    const mutating = method !== 'GET';
    const headers = { Accept: 'application/json' };
    if (mutating) {
      if (!csrf) await loadCsrf(signal);
      headers['X-CSRF-Token'] = csrf;
      headers['Content-Type'] = 'application/json';
      if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;
    }
    let res;
    try {
      res = await fetchImpl(`${path}${query(params)}`, {
        method, headers, credentials: 'same-origin', cache: 'no-store', signal,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      if (e?.name === 'AbortError') throw e;
      throw new AiApiError('서버에 연결할 수 없습니다.', { status: 0, code: 'NETWORK', retryable: true });
    }
    try {
      return await parse(res);
    } catch (e) {
      if (mutating && !retried && e instanceof AiApiError && e.code === 'CSRF_FAILED') {
        csrf = null;                               // 토큰이 낡았다: 새로 받아 같은 요청(같은 멱등 key)을 한 번만 다시 보낸다
        return send(method, path, { params, body, signal, idempotencyKey }, true);
      }
      throw e;
    }
  }

  const get = (path, params, signal) => send('GET', path, { params, signal });
  const id = (v) => encodeURIComponent(v);

  return {
    async myProjects(signal) {
      let res;
      try { res = await fetchImpl('/repositories/mine', { credentials: 'same-origin', cache: 'no-store', signal }); }
      catch (e) { if (e?.name === 'AbortError') throw e; throw new AiApiError('서버에 연결할 수 없습니다.', { code: 'NETWORK', retryable: true }); }
      if (!res.ok) throw new AiApiError('프로젝트 목록을 불러오지 못했습니다.', { status: res.status, code: res.status === 401 ? 'AUTH_REQUIRED' : null });
      let projects;
      try { projects = await res.json(); } catch { /* 검증에서 거부 */ }
      if (!Array.isArray(projects) || projects.some((p) => !p || !Number.isSafeInteger(p.id) || p.id <= 0)) {
        throw new AiApiError('프로젝트 목록 응답을 해석할 수 없습니다.', { status: res.status, code: 'BAD_RESPONSE' });
      }
      return projects;
    },
    /* 로그인 확인: 로그인하지 않았으면 null */
    async me(signal) {
      let res;
      try { res = await fetchImpl('/api/auth/me', { credentials: 'same-origin', cache: 'no-store', signal }); }
      catch (e) { if (e?.name === 'AbortError') throw e; throw new AiApiError('서버에 연결할 수 없습니다.', { status: 0, code: 'NETWORK', retryable: true }); }
      if (res.status === 401) return null;
      if (!res.ok) throw new AiApiError('로그인 상태를 확인하지 못했습니다.', { status: res.status });
      try { return await res.json(); } catch { throw new AiApiError('서버 응답을 해석할 수 없습니다.', { status: res.status, code: 'BAD_RESPONSE' }); }
    },
    async repository(repoId, signal) {
      let res;
      try { res = await fetchImpl(`/repositories/${id(repoId)}`, { credentials: 'same-origin', cache: 'no-store', signal }); }
      catch (e) { if (e?.name === 'AbortError') throw e; throw new AiApiError('서버에 연결할 수 없습니다.', { status: 0, code: 'NETWORK', retryable: true }); }
      if (!res.ok) throw new AiApiError('저장소 정보를 불러오지 못했습니다.', { status: res.status, code: res.status === 404 ? 'RESOURCE_NOT_FOUND' : null });
      try { return await res.json(); } catch { throw new AiApiError('서버 응답을 해석할 수 없습니다.', { status: res.status, code: 'BAD_RESPONSE' }); }
    },
    providers: (signal) => get(`${BASE}/providers`, null, signal),
    credentials: (signal) => get(`${BASE}/credentials`, null, signal),
    putCredential: (provider, { apiKey, profileId, verify = true }, signal) =>
      send('PUT', `${BASE}/credentials/${id(provider)}`, { body: { api_key: apiKey, profile_id: profileId || null, verify }, signal }),
    setPreferredProfile: (provider, profileId, signal) =>
      send('PATCH', `${BASE}/credentials/${id(provider)}`, { body: { profile_id: profileId }, signal }),
    deleteCredential: (provider, signal) => send('DELETE', `${BASE}/credentials/${id(provider)}`, { signal }),
    usage: (signal) => get(`${BASE}/usage`, null, signal),

    pulls: (repositoryId, { state = 'open', cursor = null, limit = 30 } = {}, signal) =>
      get(`${BASE}/pulls`, { repository_id: repositoryId, state, cursor, limit }, signal),
    pullFiles: (repositoryId, pr, { cursor = null, limit = 100 } = {}, signal) =>
      get(`${BASE}/pulls/${id(pr)}/files`, { repository_id: repositoryId, cursor, limit }, signal),

    createInput: (body, idempotencyKey, signal) => send('POST', `${BASE}/inputs`, { body, idempotencyKey, signal }),
    input: (inputId, signal, profileId = null) => get(`${BASE}/inputs/${id(inputId)}`, { profile_id: profileId }, signal),
    inputFile: (inputId, fileId, { side, startLine, endLine } = {}, signal) =>
      get(`${BASE}/inputs/${id(inputId)}/files/${id(fileId)}`, { side, start_line: startLine, end_line: endLine }, signal),
    deleteInput: (inputId, signal) => send('DELETE', `${BASE}/inputs/${id(inputId)}`, { signal }),

    createJob: (body, idempotencyKey, signal) => send('POST', `${BASE}/jobs`, { body, idempotencyKey, signal }),
    retryJob: (jobId, consent, idempotencyKey, signal) =>
      send('POST', `${BASE}/jobs/${id(jobId)}/retry`, { body: { consent }, idempotencyKey, signal }),
    job: (jobId, signal) => get(`${BASE}/jobs/${id(jobId)}`, null, signal),
    jobs: (repositoryId, { cursor = null, limit = 20, kind = null, status = null, inputId = null } = {}, signal) =>
      get(`${BASE}/jobs`, { repository_id: repositoryId, cursor, limit, kind, status, input_id: inputId }, signal),
    cancelJob: (jobId, signal) => send('POST', `${BASE}/jobs/${id(jobId)}/cancel`, { signal }),
    deleteJob: (jobId, signal) => send('DELETE', `${BASE}/jobs/${id(jobId)}`, { signal }),

    result: (resultId, signal) => get(`${BASE}/results/${id(resultId)}`, null, signal),
    findings: (resultId, { cursor = null, limit = 20 } = {}, signal) =>
      get(`${BASE}/results/${id(resultId)}/findings`, { cursor, limit }, signal),
    historyOptions: (repositoryId, signal) => get(`${BASE}/history/snapshots`, { repository_id: repositoryId }, signal),
    history: (resultId, signal) => get(`${BASE}/results/${id(resultId)}/history`, null, signal),
    attachHistory: (resultId, snapshotId, signal) => send('POST', `${BASE}/results/${id(resultId)}/history`, { body: { snapshot_id: snapshotId }, signal }),
    putFeedback: (findingId, { verdict, note, expectedVersion }, signal) =>
      send('PUT', `${BASE}/findings/${id(findingId)}/feedback`,
        { body: { verdict, note: note || null, expected_version: expectedVersion }, signal }),
  };
}
