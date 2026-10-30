// node tests/frontend/test_viz_modules.cjs — DOM·서버 없이 시각화 순수 모듈 검증
// (URL 상태 규칙 · 포맷 · 계산 버전 정책 · 상태 폴링 컨트롤러)
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');

const js = (f) => import(pathToFileURL(path.resolve(__dirname, '..', '..', 'frontend', 'js', f)).href);

/* ── 가짜 타이머: advance(ms)만큼 시간을 흘리며 예약된 콜백을 순서대로 실행 ── */
function fakeClock() {
  let t = 0, seq = 0;
  const q = [];
  const flush = () => new Promise((r) => setImmediate(r));
  return {
    now: () => t,
    setTimer(fn, ms) { const id = ++seq; q.push({ id, at: t + ms, fn }); return id; },
    clearTimer(id) { const i = q.findIndex((x) => x.id === id); if (i >= 0) q.splice(i, 1); },
    async advance(ms) {
      const end = t + ms;
      for (;;) {
        q.sort((a, b) => a.at - b.at || a.id - b.id);
        const next = q[0];
        if (!next || next.at > end) break;
        q.shift(); t = next.at; next.fn();
        for (let i = 0; i < 5; i++) await flush();
      }
      t = end;
      for (let i = 0; i < 3; i++) await flush();
    },
    pending: () => q.length,
  };
}

const resp = (status, body, headers = {}) => ({
  ok: status >= 200 && status < 300, status,
  headers: { get: (k) => headers[k] ?? headers[k.toLowerCase()] ?? null },
  json: async () => { if (body === undefined) throw new SyntaxError('bad json'); return body; },
});
const running = (extra = {}) => resp(200, { status: 'running', progress_detail: { stage: 'collect' }, commit_count: 1, ...extra });

/* 스크립트된 응답을 순서대로 돌려주는 fetch. 호출 시각·동시 진행 수를 기록 */
function scriptedFetch(clock, script) {
  const calls = [];
  let inflight = 0, maxInflight = 0;
  const fn = async (url, opts) => {
    calls.push({ at: clock.now(), url, opts });
    inflight += 1; maxInflight = Math.max(maxInflight, inflight);
    try {
      const item = script.length > 1 ? script.shift() : script[0];
      if (item instanceof Error) throw item;
      return typeof item === 'function' ? item() : item;
    } finally { inflight -= 1; }
  };
  fn.calls = calls; fn.maxInflight = () => maxInflight;
  return fn;
}

function makePoller(mod, clock, fetchImpl, handlers = {}) {
  const log = { updates: [], notices: [], stops: [] };
  const poller = mod.createStatusPoller({
    url: '/repositories/1/status', fetchImpl,
    onUpdate: (s) => { log.updates.push(s); return handlers.onUpdate?.(s); },
    onNotice: (n) => log.notices.push(n),
    onStop: (s) => log.stops.push(s),
    setTimer: clock.setTimer, clearTimer: clock.clearTimer, now: clock.now, isHidden: () => false,
  });
  return { poller, log };
}

async function main() {
  const state = await js('viz-state.js');
  const fmt = await js('fmt.js');
  const policy = await js('policy.js');
  const pollMod = await js('poll.js');

  /* ───────── viz-state: 탭 이동 시 snapshot·정책 보존, 저장소 간 혼입 차단 ───────── */
  assert.deepEqual(state.parseState('?repo=2&snapshot=7&sort_by=idf'), { repo: 2, snapshot: 7, sortBy: 'idf' });
  assert.equal(state.parseState('?snapshot=7').snapshot, null, 'repo 없이 온 snapshot은 무시');
  for (const bad of ['abc', '-1', '0', '1e3', '2.5', '', '7;DROP']) {
    assert.equal(state.parseState(`?repo=2&snapshot=${bad}`).snapshot, null, `잘못된 snapshot(${bad}) 무시`);
  }
  assert.equal(state.parseState('?repo=2&sort_by=weird').sortBy, null);
  assert.equal(state.nextQuery('?repo=2&snapshot=7', undefined), '?repo=2&snapshot=7', '같은 저장소 탭 이동은 snapshot 유지');
  assert.equal(state.nextQuery('?repo=2&snapshot=7', ''), '?repo=2&snapshot=7');
  assert.equal(state.nextQuery('?repo=2&snapshot=7', 2), '?repo=2&snapshot=7', '같은 repo를 명시해도 유지');
  assert.equal(state.nextQuery('?repo=2&snapshot=7&sort_by=idf', 3), '?repo=3', '다른 저장소로 갈 때 snapshot·정책을 가져가지 않는다');
  assert.equal(state.nextQuery('?repo=2&snapshot=7&sort_by=idf', undefined), '?repo=2&snapshot=7&sort_by=idf', 'IDF 정책 보존');
  assert.equal(state.nextQuery('?repo=2&snapshot=7', 2, 9), '?repo=2&snapshot=9', '명시한 snapshot이 우선');
  assert.equal(state.nextQuery('?repo=2&snapshot=7', 5, 9), '?repo=5&snapshot=9');
  assert.equal(state.nextQuery('', undefined), '', 'URL에 repo가 없으면 빈 쿼리(페이지가 기본 저장소 결정)');
  assert.equal(state.nextQuery('?snapshot=7', undefined), '', 'repo 없는 snapshot은 옮기지 않는다');
  assert.equal(state.buildQuery({ repo: 2, snapshot: 7, sortBy: 'coupling' }), '?repo=2&snapshot=7', '기본 정렬은 URL에 남기지 않는다');
  assert.equal(state.effectiveSort(state.parseState('?repo=2')), 'coupling');

  /* ───────── fmt: null/0/표본 구분 ───────── */
  assert.equal(fmt.fmtPct(0.02), '2%');
  assert.equal(fmt.fmtPct(1), '100%');
  assert.equal(fmt.fmtPct(0), '0%');
  assert.equal(fmt.fmtPct(0.004), '<1%', '0%와 1% 미만 구분');
  assert.equal(fmt.fmtPct(0.075), '7.5%');
  assert.equal(fmt.fmtPct(null), '—');
  assert.equal(fmt.num(null), '—', 'null은 0으로 바꾸지 않는다');
  assert.equal(fmt.num(0), '0');
  assert.equal(fmt.fmtScore(null), '—');
  assert.equal(fmt.fmtScore(0), '0.00');
  assert.equal(fmt.fmtDelta(null), '—');
  assert.equal(fmt.fmtDelta(0), '0.00');
  assert.equal(fmt.fmtDelta(0.25), '+0.25');
  assert.equal(fmt.fmtDelta(-0.25), '−0.25');
  assert.equal(fmt.fmtWindow({ window_start: null, window_end: '2026-10-03T00:00:00+00:00' }).startsWith('전체 이력 ~'), true);
  assert.equal(fmt.windowDays({ window_start: '2026-04-06T00:00:00+00:00', window_end: '2026-10-03T00:00:00+00:00' }), 180);

  /* ───────── policy ───────── */
  assert.equal(policy.policyOf('gitviz-2026.10-priority-v3').kind, 'priority');
  assert.equal(policy.policyOf('gitviz-2026.10-snapshot-v2').kind, 'legacy');
  assert.equal(policy.policyOf('gitviz-9999').kind, 'unknown');
  assert.ok(policy.policyOf('gitviz-2026.10-snapshot-v2').summaryText.includes('CurrentRisk'), '구버전 설명은 구버전 산식을 말한다');
  assert.ok(!policy.policyOf('gitviz-2026.10-priority-v3').summaryText.includes('CurrentRisk'), '신버전 설명에 CurrentRisk 없음');
  assert.ok(!policy.policyOf('gitviz-2026.10-priority-v3').summaryText.includes('refactor'), '신버전 설명에 refactor 초기화 없음');
  assert.equal(policy.scoreOf({ maintenance_priority: null, risk_score: 0.9 }), null, '키가 있으면 null을 다른 값으로 대체하지 않는다');
  assert.equal(policy.scoreOf({ risk_score: 0.4 }), 0.4, '구버전은 risk_score');
  assert.equal(policy.scoreOf({ maintenance_priority: 0 }), 0, '0은 0');

  const files = [
    { path: 'z/no_history.md', maintenance_priority: null, evidence_status: 'no_history', observation_count: 0 },
    { path: 'b/missing.py', maintenance_priority: null, evidence_status: 'metrics_missing', observation_count: 9 },
    { path: 'a/low.py', maintenance_priority: 0.9, evidence_status: 'low_support', observation_count: 2 },
    { path: 'c/tie2.py', maintenance_priority: 0.5, evidence_status: 'observed', observation_count: 10 },
    { path: 'c/tie1.py', maintenance_priority: 0.5, evidence_status: 'observed', observation_count: 30 },
    { path: 'c/tie0.py', maintenance_priority: 0.5, evidence_status: 'observed', observation_count: 30 },
    { path: 'd/zero.py', maintenance_priority: 0, evidence_status: 'low_support', observation_count: 2 },
  ];
  assert.deepEqual([...files].sort(policy.comparePriority).map((f) => f.path),
    ['a/low.py', 'c/tie0.py', 'c/tie1.py', 'c/tie2.py', 'd/zero.py', 'b/missing.py', 'z/no_history.md'],
    '점수순 → 동률은 변경 횟수 많은 순 → 경로순, null은 뒤(자료 부족 → 관측 변경 없음), 0은 점수 있는 쪽');

  /* 수집 범위·분석 근거: '완료'·'충분'을 단정하지 않고 확인 가능한 상태만 낸다 */
  const full = { commit_count: 86, merged_pr_count: 23, prs_with_files: 23, prs_with_linked_commits: 23, target_tree_file_count: 129, collected_target_file_count: 129 };
  const partial = policy.assessDataQuality({ commit_count: 29, merged_pr_count: 9, prs_with_files: 0, prs_with_linked_commits: 0, target_tree_file_count: 18, collected_target_file_count: 18, low_support_threshold: 5 });
  assert.equal(partial.collection, 'partial', 'job이 done이어도 PR 상세 0/9는 부분 수집');
  assert.equal(partial.evidence, 'limited');
  const empty = policy.assessDataQuality({ commit_count: 0, merged_pr_count: 0, prs_with_files: 0, prs_with_linked_commits: 0, target_tree_file_count: 4, collected_target_file_count: 4 });
  assert.equal(empty.evidence, 'none');
  assert.equal(empty.collection, 'checked', '관측 커밋 0은 수집 실패가 아니라 별도 상태');
  const ok = policy.assessDataQuality(full);
  assert.equal(ok.collection, 'checked', '누락이 확인되지 않아도 "완료"가 아니라 "확인된 범위 누락 없음"');
  assert.equal(ok.evidence, 'no_limits_found', '"충분"이 아니라 "확인된 제한 없음"');
  assert.equal(policy.assessDataQuality({ ...full, prs_with_files: 20 }).collection, 'partial', 'PR 상세가 일부만 있으면 부분 수집');
  assert.ok(policy.assessDataQuality({ ...full, prs_with_files: 20 }).collectionReasons.some((r) => r.includes('20/23') && r.includes('파일 상세')));
  const unlinked = policy.assessDataQuality({ ...full, prs_with_linked_commits: 0 });
  assert.equal(unlinked.collection, 'partial', 'PR 커밋 연결이 없으면 수집 범위 일부 누락');
  assert.equal(unlinked.evidence, 'limited', 'PR 커밋 연결이 없는데 근거 충분으로 보이면 안 된다');
  assert.ok(unlinked.collectionReasons.some((r) => r.includes('0/23') && r.includes('커밋 연결')));
  /* 수치가 없는 항목은 0으로 읽지 않는다 — 누락으로 단정하지도, 완료로 단정하지도 않고 '확인 불가' */
  const { prs_with_linked_commits: _omit, ...noLink } = full;
  const unknownLink = policy.assessDataQuality(noLink);
  assert.equal(unknownLink.collection, 'unverified');
  assert.deepEqual(unknownLink.collectionReasons, []);
  assert.deepEqual(unknownLink.unverified, ['PR 커밋 연결 수']);
  assert.equal(unknownLink.evidence, 'limited');
  assert.ok(unknownLink.notes.some((n) => n.includes('확인 불가')), '연결 수 없음은 0/23이 아니라 확인 불가');
  const blank = policy.assessDataQuality({});
  assert.equal(blank.collection, 'unverified', '수치가 하나도 없으면 완료로 보지 않는다');
  assert.equal(blank.evidence, 'unknown');
  assert.equal(policy.assessDataQuality({ commit_count: 3, unidentified_commits: 3 }).evidence, 'limited');
  for (const text of [...Object.values(policy.COLLECTION_TEXT), ...Object.values(policy.EVIDENCE_TEXT)]) {
    assert.ok(!/완료|충분/.test(text), `표시 문구에 완료·충분 단정 없음: ${text}`);
  }
  assert.ok(policy.COLLECTION_NOTE.includes('수집 완료를 확인한 것이 아닙니다'));
  assert.equal(policy.couplingUnitText({ co_change_unit: 'commit', fallback_reason: 'all_collected_prs_filtered' }), '커밋 단위 (대체: 전부 필터됨)');
  assert.equal(policy.FALLBACK_LABELS.no_merged_prs, 'PR 없음');
  assert.equal(policy.FALLBACK_LABELS.pr_details_missing, '상세 부족');

  /* ───────── poll: Retry-After / backoff ───────── */
  assert.equal(pollMod.parseRetryAfter('7'), 7000);
  assert.equal(pollMod.parseRetryAfter('abc'), null);
  assert.equal(pollMod.parseRetryAfter(null), null);
  assert.equal(pollMod.parseRetryAfter(new Date(1000 + 30000).toUTCString(), 1000), 30000);
  assert.equal(pollMod.parseRetryAfter('999999'), 3600 * 1000, '상한 1시간');
  assert.deepEqual([1, 2, 3, 4, 5].map((n) => pollMod.backoff(n, 2000, 30000)), [2000, 4000, 8000, 16000, 30000]);

  /* 401: 한 번만 조회하고 종료, 이후 요청 없음 */
  {
    const clock = fakeClock();
    const f = scriptedFetch(clock, [resp(401, { detail: '로그인이 필요합니다.' })]);
    const { poller, log } = makePoller(pollMod, clock, f);
    poller.start(); await clock.advance(120000);
    assert.equal(f.calls.length, 1);
    assert.equal(log.stops[0].reason, 'auth');
    assert.equal(poller.active, false);
    assert.equal(clock.pending(), 0, '종료 후 예약된 타이머 없음');
  }
  /* 404: 서버 문구를 노출하지 않고 종료 */
  {
    const clock = fakeClock();
    const f = scriptedFetch(clock, [resp(404, { detail: '비공개 저장소 foo/bar 가 존재하지만 권한 없음' })]);
    const { poller, log } = makePoller(pollMod, clock, f);
    poller.start(); await clock.advance(120000);
    assert.equal(f.calls.length, 1);
    assert.equal(log.stops[0].reason, 'not_found');
    assert.equal(log.stops[0].message, null, '404는 서버 detail을 전달하지 않는다');
  }
  /* 429 + Retry-After: 헤더 값만큼 기다렸다가 재시도 */
  {
    const clock = fakeClock();
    const f = scriptedFetch(clock, [resp(429, { detail: 'x' }, { 'Retry-After': '7' }), running()]);
    const { poller, log } = makePoller(pollMod, clock, f);
    poller.start(); await clock.advance(6900);
    assert.equal(f.calls.length, 1, 'Retry-After 7초 이전에는 재요청 없음');
    assert.equal(log.notices[0].kind, 'rate_limit');
    assert.equal(log.notices[0].retryAt, 7000, '재시도 시점을 알려준다');
    assert.equal(log.notices[0].retryAfterHeader, true);
    await clock.advance(200);
    assert.equal(f.calls.length, 2);
    assert.equal(f.calls[1].at, 7000);
    assert.equal(log.updates.length, 1);
    poller.stop();
  }
  /* 429 + Retry-After 없음: 5s → 10s → 20s 지수 backoff, 횟수 제한 후 종료 */
  {
    const clock = fakeClock();
    const f = scriptedFetch(clock, [resp(429, { detail: 'limit' })]);
    const { poller, log } = makePoller(pollMod, clock, f);
    poller.start(); await clock.advance(10 * 60 * 1000);
    const gaps = f.calls.slice(1, 4).map((c, i) => c.at - f.calls[i].at);
    assert.deepEqual(gaps, [5000, 10000, 20000]);
    assert.equal(f.calls.length, pollMod.POLL.MAX_RATE_LIMIT + 1, '429는 횟수 제한 — 무한 대기 없음');
    assert.equal(log.stops[0].reason, 'rate_limit');
    assert.equal(log.notices[0].retryAfterHeader, false);
    await clock.advance(10 * 60 * 1000);
    assert.equal(f.calls.length, pollMod.POLL.MAX_RATE_LIMIT + 1, '종료 후 추가 요청 없음');
  }
  /* 502/503/네트워크 오류: 지수 backoff 후 횟수 제한, 실패 상태 전달 */
  for (const bad of [resp(502, {}), resp(503, {}), new TypeError('Failed to fetch'), resp(200, undefined)]) {
    const clock = fakeClock();
    const f = scriptedFetch(clock, [bad]);
    const { poller, log } = makePoller(pollMod, clock, f);
    poller.start(); await clock.advance(10 * 60 * 1000);
    assert.equal(f.calls.length, pollMod.POLL.MAX_TRANSIENT + 1, `일시 오류는 ${pollMod.POLL.MAX_TRANSIENT}회까지만 재시도`);
    assert.deepEqual(f.calls.slice(1).map((c, i) => c.at - f.calls[i].at), [2000, 4000, 8000, 16000, 30000]);
    assert.equal(log.stops[0].reason, 'unreachable');
    assert.equal(log.notices.length, pollMod.POLL.MAX_TRANSIENT);
    assert.equal(poller.active, false);
  }
  /* 복구되면 카운터 초기화 */
  {
    const clock = fakeClock();
    const f = scriptedFetch(clock, [resp(502, {}), resp(502, {}), running(), resp(502, {}), running({ commit_count: 2 })]);
    const { poller, log } = makePoller(pollMod, clock, f);
    poller.start(); await clock.advance(60000);
    assert.equal(log.notices.filter((n) => n.kind === 'transient').map((n) => n.attempt).join(','), '1,2,1', '성공 후 오류는 다시 1회차부터');
    poller.stop();
  }
  /* 그 외 4xx(403/409/422)는 재시도 없이 종료 */
  for (const code of [400, 403, 409, 422]) {
    const clock = fakeClock();
    const f = scriptedFetch(clock, [resp(code, { detail: `에러 ${code}` })]);
    const { poller, log } = makePoller(pollMod, clock, f);
    poller.start(); await clock.advance(60000);
    assert.equal(f.calls.length, 1);
    assert.equal(log.stops[0].reason, 'client_error');
    assert.equal(log.stops[0].status, code);
    assert.equal(log.stops[0].message, `에러 ${code}`);
  }
  /* 200이지만 작업 없음({"error":...}) → 무한 대기 대신 종료 */
  {
    const clock = fakeClock();
    const f = scriptedFetch(clock, [resp(200, { error: 'No analysis job found' })]);
    const { poller, log } = makePoller(pollMod, clock, f);
    poller.start(); await clock.advance(120000);
    assert.equal(f.calls.length, pollMod.POLL.MAX_NO_JOB);
    assert.equal(log.stops[0].reason, 'no_job');
    assert.equal(log.updates.length, 0);
  }
  /* 직렬 요청: 응답이 늦어도 다음 요청을 겹쳐 보내지 않는다 */
  {
    const clock = fakeClock();
    let release;
    const slow = () => new Promise((r) => { release = () => r(running()); });
    const f = scriptedFetch(clock, [slow]);
    const { poller } = makePoller(pollMod, clock, f);
    poller.start(); await clock.advance(120000);
    assert.equal(f.calls.length, 1, '응답이 오기 전에는 요청 1개뿐');
    assert.equal(f.maxInflight(), 1);
    poller.stop();
  }
  /* 닫힘: 진행 중 요청의 늦은 응답은 무시하고 이후 요청도 없다 */
  {
    const clock = fakeClock();
    let release;
    const slow = () => new Promise((r) => { release = () => r(running()); });
    const f = scriptedFetch(clock, [slow]);
    const { poller, log } = makePoller(pollMod, clock, f);
    poller.start(); await clock.advance(100);
    assert.equal(f.calls.length, 1);
    assert.equal(f.calls[0].opts.signal.aborted, false);
    poller.stop();
    assert.equal(f.calls[0].opts.signal.aborted, true, '닫으면 진행 중 요청을 abort');
    release(); await clock.advance(120000);
    assert.equal(log.updates.length, 0, '닫힌 뒤 늦게 온 응답은 화면에 반영하지 않는다');
    assert.equal(f.calls.length, 1, '닫힌 뒤 추가 요청 없음');
    assert.equal(clock.pending(), 0);
  }
  /* 닫았다가 곧바로 다시 열어도 이전 요청이 새 폴링을 망치지 않는다 */
  {
    const clock = fakeClock();
    let releaseOld;
    const f = scriptedFetch(clock, [() => new Promise((r) => { releaseOld = () => r(running({ commit_count: 111 })); }), running({ commit_count: 2 })]);
    const { poller, log } = makePoller(pollMod, clock, f);
    poller.start(); await clock.advance(10);
    poller.restart(); await clock.advance(10);
    releaseOld(); await clock.advance(10);
    assert.deepEqual(log.updates.map((u) => u.commit_count), [2], '이전 세대 응답(111)은 버려지고 새 세대 응답만 반영');
    poller.stop();
  }
  /* 조회 간격 적응: 변화가 없으면 늘리고, 변하면 기본으로 */
  {
    const clock = fakeClock();
    const f = scriptedFetch(clock, [running(), running(), running(), running(), running(), running(), running(), running(), running(), running(), running(), running(), running({ commit_count: 99 }), running({ commit_count: 99 })]);
    const { poller } = makePoller(pollMod, clock, f);
    poller.start(); await clock.advance(5 * 60 * 1000);
    const gaps = f.calls.slice(1).map((c, i) => c.at - f.calls[i].at);
    assert.equal(gaps[0], 2000);
    assert.ok(gaps[4] > gaps[0], '변화 없는 동안 간격 증가');
    assert.equal(Math.max(...gaps), pollMod.POLL.MAX_MS, '상한 10초');
    const idx = f.calls.length; // 변화 이후 간격이 기본으로 복귀했는지는 nextInterval로 확인
    assert.equal(pollMod.nextInterval(0), 2000);
    assert.equal(pollMod.nextInterval(100), 10000);
    assert.equal(pollMod.nextInterval(0, true), 15000, '가려진 탭은 느리게');
    poller.stop();
  }
  /* nudge: 정상 간격 대기 중에는 즉시 조회, 429/오류 재시도 대기 중에는 건너뛰지 않는다 */
  {
    const clock = fakeClock();
    const f = scriptedFetch(clock, [running(), running(), resp(429, {}, { 'Retry-After': '30' }), running()]);
    const { poller } = makePoller(pollMod, clock, f);
    poller.start(); await clock.advance(10);
    assert.equal(f.calls.length, 1);
    poller.nudge(); await clock.advance(10);
    assert.equal(f.calls.length, 2, '정상 간격 대기 중 nudge는 즉시 한 번 조회');
    poller.nudge(); await clock.advance(10);
    assert.equal(f.calls.length, 3);
    poller.nudge(); await clock.advance(10);
    assert.equal(f.calls.length, 3, '429 Retry-After 대기 중에는 nudge가 대기를 건너뛰지 않는다');
    await clock.advance(30000);
    assert.equal(f.calls.length, 4, 'Retry-After 후에는 정상 재개');
    poller.stop();
    poller.nudge(); await clock.advance(10);
    assert.equal(f.calls.length, 4, '중지된 폴러는 nudge해도 조회하지 않는다');
  }
  /* 완료('stop' 반환)하면 폴링 종료 */
  {
    const clock = fakeClock();
    const f = scriptedFetch(clock, [running(), resp(200, { status: 'done', snapshot_id: 5 })]);
    const { poller, log } = makePoller(pollMod, clock, f, { onUpdate: (s) => (s.status === 'done' ? 'stop' : undefined) });
    poller.start(); await clock.advance(60000);
    assert.equal(f.calls.length, 2);
    assert.equal(log.updates.at(-1).snapshot_id, 5);
    assert.equal(poller.active, false);
  }
  /* 렌더 오류가 나도 폴링은 계속된다 */
  {
    const clock = fakeClock();
    const f = scriptedFetch(clock, [running(), running({ commit_count: 2 })]);
    const origError = console.error; console.error = () => {};
    const { poller } = makePoller(pollMod, clock, f, { onUpdate: () => { throw new Error('render'); } });
    poller.start(); await clock.advance(10000);
    console.error = origError;
    assert.ok(f.calls.length >= 2);
    poller.stop();
  }

  console.log('시각화 순수 모듈 검증 통과 (viz-state · fmt · policy · poll)');
}

main().catch((e) => { console.error(e); process.exit(1); });
