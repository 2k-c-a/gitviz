/* ── AI 작업·입력 상태 폴링 (DOM 의존 없음) ──
   기존 poll.js의 원칙(직렬 요청·stop 시 abort·429 Retry-After·지수 backoff·숨긴 탭 감속)을 따르되,
   AI endpoint의 오류 형식(AiApiError)과 종료 조건을 쓴다.
   - fetchOnce(signal) : 한 번 조회해 본문을 돌려주거나 AiApiError를 던진다.
   - isDone(body)      : true면 종료(작업 완료·실패·취소, 입력 준비 끝).
   - signature(body)   : 변화 감지(같으면 간격을 늘린다). 매번 달라지는 값은 넣지 않는다.
   - 401·404·그 밖의 4xx는 즉시 멈추고, 429·5xx·네트워크 오류는 횟수 제한 안에서만 다시 시도한다.
   - stop()/세대 변경 후 도착한 응답은 버린다(저장소·작업 전환 시 이전 응답이 새 화면을 덮지 않는다). */
import { POLL, backoff, nextInterval } from './poll.js';

export function createAiPoller({
  fetchOnce,
  isDone = () => false,
  signature = (b) => JSON.stringify(b),
  onUpdate = () => {},
  onNotice = () => {},
  onStop = () => {},
  setTimer = (fn, ms) => setTimeout(fn, ms),
  clearTimer = (id) => clearTimeout(id),
  now = () => Date.now(),
  isHidden = () => (typeof document !== 'undefined' && document.hidden),
}) {
  let gen = 0;
  let timer = null;
  let controller = null;
  let inFlight = false;
  let active = false;
  let lastSig = null, idle = 0, transient = 0, rateLimited = 0;
  let waiting = null;

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

  async function tick(myGen) {
    if (!active || myGen !== gen || inFlight) return;
    inFlight = true;
    controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    let body;
    try {
      body = await fetchOnce(controller?.signal);
    } catch (err) {
      if (myGen !== gen) return;                      // 우리가 abort했거나 이미 다른 세대
      inFlight = false;
      if (err?.name === 'AbortError') return;
      return handleError(myGen, err);
    }
    if (myGen !== gen) return;
    inFlight = false;
    transient = 0; rateLimited = 0;
    const sig = signature(body);
    idle = sig === lastSig ? idle + 1 : 0;
    lastSig = sig;
    let done = false;
    try { done = !!isDone(body); } catch { done = false; }
    try { onUpdate(body, { done }); } catch (e) { console.error('[ai-poll] 화면 갱신 실패:', e?.name || 'error'); }
    if (myGen !== gen) return;                        // onUpdate 안에서 stop/restart 했을 수 있다
    if (done) { active = false; clearAll(); return onStop({ reason: 'done', status: 200 }); }
    schedule(nextInterval(idle, isHidden()), myGen);
  }

  function handleError(myGen, err) {
    const status = err?.status ?? 0;
    if (status === 401) return finish(myGen, { reason: 'auth', status, error: err });
    if (status === 404) return finish(myGen, { reason: 'not_found', status, error: err });
    if (status === 429) {
      rateLimited += 1;
      if (rateLimited > POLL.MAX_RATE_LIMIT) return finish(myGen, { reason: 'rate_limit', status, error: err });
      const wait = err.retryAfter != null ? err.retryAfter : backoff(rateLimited, POLL.RATE_BASE_MS, POLL.RATE_MAX_MS);
      onNotice({ kind: 'rate_limit', status, attempt: rateLimited, max: POLL.MAX_RATE_LIMIT, retryAt: now() + wait });
      return schedule(wait, myGen, 'backoff');
    }
    if (status === 0 || status >= 500) {
      transient += 1;
      if (transient > POLL.MAX_TRANSIENT) return finish(myGen, { reason: 'unreachable', status, error: err });
      const wait = backoff(transient, POLL.TRANSIENT_BASE_MS, POLL.TRANSIENT_MAX_MS);
      onNotice({ kind: 'transient', status, attempt: transient, max: POLL.MAX_TRANSIENT, retryAt: now() + wait });
      return schedule(wait, myGen, 'backoff');
    }
    return finish(myGen, { reason: 'client_error', status, error: err });
  }

  function reset() { lastSig = null; idle = 0; transient = 0; rateLimited = 0; }

  return {
    start() {
      gen += 1; clearAll(); reset(); inFlight = false; active = true; waiting = null;
      const myGen = gen;
      timer = setTimer(() => { timer = null; tick(myGen); }, 0);
    },
    stop() { gen += 1; active = false; inFlight = false; waiting = null; clearAll(); },
    restart() { this.start(); },
    /* 탭이 다시 보일 때: 정상 대기 중이면 지금 한 번 확인(429·오류 대기는 건너뛰지 않는다) */
    nudge() {
      if (!active || inFlight || waiting !== 'normal' || timer == null) return;
      clearTimer(timer); timer = null; waiting = null;
      tick(gen);
    },
    get active() { return active; },
  };
}
