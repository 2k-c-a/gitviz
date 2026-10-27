import { request } from './platform/transport.js';
/* ── 작업 상태 폴링 컨트롤러 (DOM 의존 없음) ──
   GET /repositories/{id}/status 를 반복 조회한다.
   - 요청은 항상 직렬: 이전 요청이 끝나야 다음 요청을 예약한다(중복 요청 없음).
   - stop() 하면 진행 중 요청을 abort하고 이후 응답은 무시한다(닫힌 모달의 폴링 없음).
   - 응답 종류별 처리:
       401            → 즉시 종료(재로그인 안내)
       404            → 즉시 종료(접근 불가/삭제 — 비공개 여부는 구분하지 않는다)
       429            → Retry-After(초 또는 날짜)를 우선, 없으면 지수 backoff. 횟수 제한 후 종료
       5xx·네트워크 오류·깨진 JSON → 지수 backoff로 횟수 제한 재시도 후 종료
       그 외 4xx      → 즉시 종료
       200이지만 작업 없음({"error":...}) → 몇 번 확인 후 종료
   - 상태가 변하지 않는 동안 조회 간격을 점차 늘린다(변하면 다시 기본 간격). */

export const POLL = {
  BASE_MS: 2000,
  MAX_MS: 10000,
  HIDDEN_MIN_MS: 15000,        // 탭이 가려져 있을 땐 느리게
  GROW_AFTER: 2,               // 변화 없는 조회가 이만큼 쌓일 때마다 간격 1.5배
  MAX_TRANSIENT: 5,            // 연속 5xx/네트워크 오류 허용 횟수
  TRANSIENT_BASE_MS: 2000,
  TRANSIENT_MAX_MS: 30000,
  MAX_RATE_LIMIT: 8,           // 연속 429 허용 횟수
  RATE_BASE_MS: 5000,
  RATE_MAX_MS: 60000,
  MAX_NO_JOB: 3,
};

/* Retry-After: 초 단위 정수 또는 HTTP 날짜. 해석 불가면 null */
export function parseRetryAfter(value, now = Date.now()) {
  if (value == null || value === '') return null;
  const s = String(value).trim();
  if (/^\d+$/.test(s)) return Math.min(parseInt(s, 10), 3600) * 1000;
  const t = Date.parse(s);
  if (Number.isNaN(t)) return null;
  return Math.min(Math.max(0, t - now), 3600 * 1000);
}

export const backoff = (attempt, base, max) => Math.min(max, base * 2 ** Math.max(0, attempt - 1));

/* 변화 감지용 서명 — remaining_seconds처럼 매번 달라지는 값은 넣지 않는다 */
export const statusSignature = (s) =>
  [s.status, JSON.stringify(s.progress_detail ?? {}), s.commit_count, s.pr_count, s.contributors_total, s.is_stale].join('|');

export function nextInterval(idle, hidden = false) {
  const grown = POLL.BASE_MS * 1.5 ** Math.floor(idle / POLL.GROW_AFTER);
  const ms = Math.min(POLL.MAX_MS, grown);
  return hidden ? Math.max(ms, POLL.HIDDEN_MIN_MS) : ms;
}

export function createStatusPoller({
  url,
  fetchImpl = (u, o) => request(u, o),
  onUpdate,                       // (status) => 'stop' | void   — 정상 응답. 'stop'을 돌려주면 종료(완료·실패 등)
  onNotice = () => {},            // ({kind, message, retryAt, attempt, max, status}) — 재시도 대기 안내
  onStop = () => {},              // ({reason, status, message}) — 더 이상 조회하지 않음
  setTimer = (fn, ms) => setTimeout(fn, ms),
  clearTimer = (id) => clearTimeout(id),
  now = () => Date.now(),
  isHidden = () => (typeof document !== 'undefined' && document.hidden),
}) {
  let gen = 0;                    // stop/start 때마다 증가 — 이전 세대의 늦은 응답을 버린다
  let timer = null;
  let controller = null;
  let inFlight = false;
  let active = false;
  let lastSig = null, idle = 0, transient = 0, rateLimited = 0, noJob = 0;
  let waiting = null;             // 지금 기다리는 이유: 'normal'(정상 간격) | 'backoff'(429·오류 재시도 대기)

  const clearAll = () => {
    if (timer != null) { clearTimer(timer); timer = null; }
    if (controller) { try { controller.abort(); } catch { /* noop */ } controller = null; }
  };

  function schedule(ms, myGen, kind = 'normal') {
    if (!active || myGen !== gen) return;
    waiting = kind;
    timer = setTimer(() => { timer = null; waiting = null; tick(myGen); }, ms);
  }

  function finish(myGen, info) {
    if (myGen !== gen) return;
    active = false;
    clearAll();
    onStop(info);
  }

  async function readDetail(res) {
    try {
      const body = await res.json();
      return typeof body?.detail === 'string' ? body.detail : null;
    } catch { return null; }
  }

  async function tick(myGen) {
    if (!active || myGen !== gen || inFlight) return;
    inFlight = true;
    controller = typeof AbortController !== 'undefined' ? new AbortController() : null;

    let res;
    try {
      res = await fetchImpl(url, { credentials: 'include', cache: 'no-store', signal: controller?.signal });
    } catch {
      if (myGen !== gen) return;               // 우리가 abort한 요청 — 이미 새 세대가 시작됐을 수 있어 상태를 건드리지 않는다
      inFlight = false;
      return handleTransient(myGen, null);
    }
    if (myGen !== gen) return;
    inFlight = false;

    if (res.ok) {
      let body;
      try { body = await res.json(); } catch { body = undefined; }
      if (myGen !== gen) return;
      if (body === undefined) return handleTransient(myGen, res.status);
      if (!body || typeof body !== 'object' || body.error || typeof body.status !== 'string') {
        noJob += 1;
        if (noJob >= POLL.MAX_NO_JOB) return finish(myGen, { reason: 'no_job', status: res.status, message: '분석 작업을 찾을 수 없습니다.' });
        return schedule(POLL.BASE_MS * 2, myGen, 'backoff');
      }
      transient = 0; rateLimited = 0; noJob = 0;
      const sig = statusSignature(body);
      idle = sig === lastSig ? idle + 1 : 0;
      lastSig = sig;
      let verdict;
      try { verdict = onUpdate(body); } catch (err) { console.error('[poll] 화면 갱신 실패:', err); }   // 렌더 오류로 폴링이 멈추지 않게
      if (myGen !== gen) return;               // onUpdate 안에서 stop/restart 했을 수 있다
      if (verdict === 'stop') { active = false; clearAll(); return; }
      return schedule(nextInterval(idle, isHidden()), myGen);
    }

    const status = res.status;
    if (status === 401) {
      const message = await readDetail(res);
      return finish(myGen, { reason: 'auth', status, message });
    }
    if (status === 404) {
      return finish(myGen, { reason: 'not_found', status, message: null });   // 서버 문구는 쓰지 않는다
    }
    if (status === 429) {
      const retryHeader = res.headers && typeof res.headers.get === 'function' ? res.headers.get('Retry-After') : null;
      rateLimited += 1;
      if (rateLimited > POLL.MAX_RATE_LIMIT) {
        return finish(myGen, { reason: 'rate_limit', status, message: await readDetail(res) });
      }
      const fromHeader = parseRetryAfter(retryHeader, now());
      const wait = fromHeader ?? backoff(rateLimited, POLL.RATE_BASE_MS, POLL.RATE_MAX_MS);
      onNotice({ kind: 'rate_limit', status, attempt: rateLimited, max: POLL.MAX_RATE_LIMIT, retryAt: now() + wait, retryAfterHeader: fromHeader != null });
      return schedule(wait, myGen, 'backoff');
    }
    if (status >= 500) return handleTransient(myGen, status);
    return finish(myGen, { reason: 'client_error', status, message: await readDetail(res) });
  }

  function handleTransient(myGen, status) {
    transient += 1;
    if (transient > POLL.MAX_TRANSIENT) {
      return finish(myGen, { reason: 'unreachable', status, message: null });
    }
    const wait = backoff(transient, POLL.TRANSIENT_BASE_MS, POLL.TRANSIENT_MAX_MS);
    onNotice({ kind: 'transient', status, attempt: transient, max: POLL.MAX_TRANSIENT, retryAt: now() + wait });
    schedule(wait, myGen, 'backoff');
  }

  function reset() { lastSig = null; idle = 0; transient = 0; rateLimited = 0; noJob = 0; }

  return {
    /* 첫 조회는 지연 없이 바로 */
    start() {
      gen += 1; clearAll(); reset(); inFlight = false; active = true; waiting = null;
      const myGen = gen;
      timer = setTimer(() => { timer = null; tick(myGen); }, 0);
    },
    stop() { gen += 1; active = false; inFlight = false; waiting = null; clearAll(); },
    /* 사용자가 "다시 확인"을 눌렀을 때 — 카운터를 지우고 새로 시작 */
    restart() { this.start(); },
    /* 탭이 다시 보일 때 등: 정상 간격으로 기다리는 중이면 지금 바로 한 번 확인한다.
       429·오류 재시도 대기 중에는 서버가 요청한 대기를 건너뛰지 않는다. */
    nudge() {
      if (!active || inFlight || waiting !== 'normal' || timer == null) return;
      clearTimer(timer); timer = null; waiting = null;
      tick(gen);
    },
    get active() { return active; },
  };
}
