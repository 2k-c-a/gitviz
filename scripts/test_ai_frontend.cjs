// node scripts/test_ai_frontend.cjs — DOM·서버 없이 AI 리뷰 화면의 순수 모듈 검증
// (주소 규칙 · 세대 guard · 멱등 key · 문구/오류 매핑 · API 클라이언트 · 폴링)
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');

const js = (f) => import(pathToFileURL(path.resolve(__dirname, '..', 'frontend', 'js', f)).href);

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

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const U1 = '11111111-1111-4111-8111-111111111111';
const U2 = '22222222-2222-4222-8222-222222222222';

test('로컬 동의: key·외부 전송 없이 모델 fingerprint를 고정한다', async () => {
  const S = await js('ai-state.js');
  const preview = { input_id: U1, status: 'ready', execution_valid: true, reviewable: true,
    manifest_hash: 'a'.repeat(64), policy_version: 'code-transfer-v1', files: [{ content_state: 'read' }],
    transfer: { provider: 'ollama', profile_id: 'ollama-local-v1', model: 'qwen2.5-coder:7b',
      execution_location: 'local', requires_api_key: false, external_transfer_allowed: false,
      credential_configured: true, credential_version: null, profile_fingerprint: 'b'.repeat(64), model_digest: 'c'.repeat(64) } };
  assert.deepEqual(S.consentBlockers(preview), []);
  assert.equal(S.buildConsent(preview).credential_version, null);
  assert.equal(S.buildConsent(preview).profile_fingerprint, 'b'.repeat(64));
  assert.match(S.consentStatement(preview), /같은 PC/);
  assert.match(S.consentStatement(preview), /API 요금은 없습니다/);
  const cloud = { ...preview, transfer: { ...preview.transfer, execution_location: 'external', requires_api_key: true, credential_configured: false } };
  assert.equal(S.buildConsent(cloud), null);
  assert.equal(S.consentBlockers(cloud).length, 2);
});

test('미리보기 API: 선택 profile을 query로 보낸다', async () => {
  const { createAiClient } = await js('ai-api.js');
  let url;
  const api = createAiClient({ fetchImpl: async (u) => { url = String(u); return { ok: true, status: 200, json: async () => ({}) }; } });
  await api.input(U1, undefined, 'gpt-5.4-v1');
  assert.match(url, /profile_id=gpt-5.4-v1/);
});

test('로컬 중단 안내: 외부 API 과금 위험과 구분한다', async () => {
  const S = await js('ai-state.js');
  for (const provider of ['ollama', 'lmstudio', 'koboldcpp']) {
    const local = S.describeJob({ provider, status: 'failed', error_code: 'PROVIDER_TIMEOUT', outcome_uncertain: true });
    assert.equal(local.costRisk, false);
    assert.match(local.body, /API 요금은 없/);
    assert.match(local.body, /자동으로 다시 실행하지 않습니다/);
  }
  const cloud = S.describeJob({ provider: 'openai', status: 'failed', error_code: 'PROVIDER_TIMEOUT', outcome_uncertain: true });
  assert.equal(cloud.costRisk, true);
});

/* ───────────────────────── 주소 ───────────────────────── */
test('사용량: 금액·횟수 무제한을 0달러 예산으로 표시하지 않는다', async () => {
  const S = await js('ai-state.js');
  const usage = { currency: 'USD', user_spent: '99', user_reserved: '1', user_remaining: '0', user_limit: '0.000000', daily_jobs_used: 50, daily_job_limit: 0 };
  const text = S.usageStatement(usage);
  assert.match(text, /오늘 요청 50회/);
  assert.doesNotMatch(text, /상한 없음|남음|한도 \$0|50\/0/);
  const capped = S.usageStatement({ ...usage, user_limit: '200', daily_job_limit: 100 });
  assert.match(capped, /한도/);
  assert.match(capped, /50\/100/);
});

test('제공자 선택: 로컬 AI로 묶되 런타임·profile과 활성 여부는 유지한다', async () => {
  const S = await js('ai-state.js');
  const providers = [{ id: 'openai', display_name: 'OpenAI', models: [{ provider: 'openai', execution_location: 'external' }] },
    ...['ollama', 'lmstudio', 'koboldcpp'].map((id) => ({ id, display_name: id + ' (같은 PC)',
      models: [{ provider: id, profile_id: id + '-local-v1', execution_location: 'local', model: id + '-model', allowed: id === 'ollama' }] }))];
  const options = S.selectableProviders(providers);
  assert.deepEqual(options.map((p) => p.display_name), ['OpenAI', '로컬 AI']);
  assert.deepEqual(options[1].models.map((m) => m.provider), ['ollama', 'lmstudio', 'koboldcpp']);
  assert.deepEqual(options[1].models.map((m) => m.allowed), [true, false, false]);
  assert.equal(options[1].models[2].profile_id, 'koboldcpp-local-v1');
  assert.equal(providers[1].models[0].display_name, undefined);
});
test('주소: repo가 없으면 어떤 값도 쓰지 않는다(기본 저장소 없음)', async () => {
  const s = await js('ai-state.js');
  for (const search of ['', '?', '?pr=3', `?job=${U1}`, `?input=${U1}&pr=2`, '?repo=abc', '?repo=0', '?repo=-1', '?repo=1.5']) {
    const r = s.parseRoute(search);
    assert.equal(r.repo, null, search);
    assert.equal(r.view, s.VIEWS.NO_REPO, search);
    assert.equal(r.pr, null);
    assert.equal(r.job, null);
  }
});

test('주소: 화면 결정 순서 job > input > pr > home, 잘못된 값은 무시하고 알린다', async () => {
  const s = await js('ai-state.js');
  assert.equal(s.parseRoute('?repo=4').view, 'home');
  assert.equal(s.parseRoute('?repo=4&pr=9').view, 'files');
  assert.equal(s.parseRoute(`?repo=4&pr=9&input=${U1}`).view, 'preview');
  assert.equal(s.parseRoute(`?repo=4&pr=9&input=${U1}&job=${U2}`).view, 'job');
  const bad = s.parseRoute('?repo=4&pr=x&job=not-a-uuid');
  assert.deepEqual([bad.view, bad.pr, bad.job], ['home', null, null]);
  assert.deepEqual(bad.invalid.sort(), ['job', 'pr']);
  assert.equal(s.parseRoute(`?repo=4&input=${U1.toUpperCase()}`).input, U1);          // 대소문자 정규화
  assert.equal(s.parseRoute(`?repo=4&retry=${U2}`).retry, null);                       // input 없이 retry는 무시
  assert.equal(s.parseRoute(`?repo=4&input=${U1}&retry=${U2}`).retry, U2);
});

test('주소: 만들기·왕복, 저장소가 바뀌면 PR·입력·작업을 가져가지 않는다', async () => {
  const s = await js('ai-state.js');
  assert.equal(s.buildRoute({}), '');
  assert.equal(s.buildRoute({ pr: 3 }), '');                                           // repo 없이는 만들지 않는다
  assert.equal(s.buildRoute({ repo: 5 }), '?repo=5');
  assert.equal(s.buildRoute({ repo: 5, pr: 3 }), '?repo=5&pr=3');
  assert.equal(s.buildRoute({ repo: 5, pr: 3, input: U1 }), `?repo=5&input=${U1}`);
  assert.equal(s.buildRoute({ repo: 5, pr: 3, input: U1, job: U2 }), `?repo=5&job=${U2}`);
  assert.equal(s.buildRoute({ repo: 5, input: U1, retry: U2 }), `?repo=5&input=${U1}&retry=${U2}`);
  assert.equal(s.buildRoute({ repo: 5, input: U1, force: true }), `?repo=5&input=${U1}&force=1`);
  assert.equal(s.buildRoute({ repo: 5, pr: 3, force: true }), '?repo=5&pr=3');                      // input 없이 force는 쓰지 않는다
  assert.equal(s.parseRoute(`?repo=5&input=${U1}&force=1`).force, true);
  assert.equal(s.parseRoute(`?repo=5&input=${U1}&retry=${U2}&force=1`).force, false);
  assert.equal(s.parseRoute('?repo=5&pr=3&force=1').force, false);
  assert.equal(s.routeForRepo(8), '?repo=8');
  for (const r of [{ repo: 5, pr: 3 }, { repo: 5, input: U1 }, { repo: 5, job: U2 }]) {
    const back = s.parseRoute(s.buildRoute(r));
    for (const k of Object.keys(r)) assert.equal(back[k], r[k]);
  }
});

/* ───────────────────────── 세대·멱등 ───────────────────────── */
test('guard: 새 세대가 시작되면 이전 요청은 abort되고 늦은 응답은 폐기된다', async () => {
  const s = await js('ai-state.js');
  const g = s.createGuard();
  const a = g.begin();
  assert.equal(a.current(), true);
  const b = g.begin();
  assert.equal(a.current(), false);
  assert.equal(a.signal.aborted, true);
  assert.equal(b.current(), true);
  assert.equal(b.signal.aborted, false);
  assert.equal(g.generation, 2);
});

test('멱등 key: 같은 내용은 같은 key, 내용이 바뀌면 새 key, 서버가 답하면 끝, 네트워크 오류는 유지', async () => {
  const s = await js('ai-state.js');
  let n = 0;
  const intent = s.createIntent(() => `key-${++n}`);
  const p1 = { kind: 'review', consent: { b: 2, a: 1 } };
  const k1 = intent.keyFor(p1);
  assert.equal(intent.keyFor({ consent: { a: 1, b: 2 }, kind: 'review' }), k1);          // 필드 순서와 무관
  assert.equal(intent.keyFor(p1), k1);                                                    // 더블클릭
  intent.settle(0);                                                                       // 네트워크 오류: 같은 key로 재시도
  assert.equal(intent.keyFor(p1), k1);
  intent.settle(503);
  assert.equal(intent.keyFor(p1), k1);
  assert.notEqual(intent.keyFor({ ...p1, kind: 'summary' }), k1);                         // 다른 요청은 새 key
  const k3 = intent.keyFor(p1);
  intent.settle(202);                                                                     // 접수됨: 의도가 끝났다
  assert.notEqual(intent.keyFor(p1), k3);
  const k4 = intent.keyFor(p1);
  intent.settle(409);
  assert.notEqual(intent.keyFor(p1), k4);
});

/* ───────────────────────── 포맷·문구 ───────────────────────── */
test('포맷: SHA·금액·시간·크기', async () => {
  const s = await js('ai-state.js');
  assert.equal(s.shortSha('a'.repeat(40)), 'aaaaaaa');
  assert.equal(s.shortSha(null), '—');
  assert.equal(s.fmtMoney('0.086000'), '$0.09');
  assert.equal(s.fmtMoney('0.004'), '$0.0040');
  assert.equal(s.fmtMoney(null), '알 수 없음');
  assert.equal(s.fmtMoney('abc'), '알 수 없음');
  assert.equal(s.fmtDuration(45), '45초');
  assert.equal(s.fmtDuration(125), '2분 5초');
  assert.equal(s.fmtBytes(2048), '2.0 KB');
});

test('coverage: 전체 변경·선택·분석 분모를 따로 말하고 실행 전 none은 분석 실패가 아니다', async () => {
  const s = await js('ai-state.js');
  const cov = { inventory_coverage: 'complete', selected_content_coverage: 'partial', analysis_coverage: 'none', selected_files: 3, pr_total_files: 8, limitations: ['not_selected', 'secret_suspected'] };
  assert.equal(s.scopeSummary(cov), '전체 변경 8개 · 선택 3개');
  assert.equal(s.scopeSummary(cov, { analyzed: 2 }), '전체 변경 8개 · 선택 3개 · 분석 2개');
  assert.equal(s.scopeSummary({ ...cov, pr_total_files: null, inventory_coverage: 'unknown' }), '전체 변경 알 수 없음 · 선택 3개');
  const before = s.coverageLines(cov, { beforeRun: true });
  assert.match(before[2].value, /아직 분석 전/);
  const after = s.coverageLines(cov, { beforeRun: false });
  assert.match(after[2].value, /분석된 범위 없음/);
  assert.equal(before[1].value, '선택한 파일 중 일부만 코드를 확보');
  assert.deepEqual(s.coverageLimitations(cov), ['선택하지 않은 파일', '비밀 정보로 의심돼 제외']);
  assert.match(s.limitationLabel('weird'), /알 수 없는 사유/);
  assert.equal(s.scopeSummary(null), '범위 정보 없음');
});

test('지적 0건·미검증 문구는 "오류 없음"으로 읽히지 않는다', async () => {
  const s = await js('ai-state.js');
  const cov = { inventory_coverage: 'complete', selected_content_coverage: 'complete', analysis_coverage: 'complete', selected_files: 4, pr_total_files: 4, limitations: [] };
  const texts = [s.emptyFindingsText(cov), s.emptyFindingsText(null), s.humanValidationText(s.findingsSummary([])),
    s.humanValidationText(s.findingsSummary([{ finding: { human_validation: 'unverified' } }])), s.STRUCTURE_NOTE, s.MODEL_NOTE];
  for (const t of texts) for (const bad of s.FORBIDDEN_PHRASES) assert.ok(!t.includes(bad), `${bad} in ${t}`);
  const empty = s.emptyFindingsText(cov);
  assert.match(empty, /지적 0건/);
  assert.match(empty, /문제가 없다는 뜻이 아닙니다/);
  assert.match(empty, /사람이 검증하기 전/);
  assert.match(empty, /선택한 파일 전체를 분석/);
  assert.match(empty, /전체 변경 4개 · 선택 4개/);
  const sum = s.findingsSummary([{ finding: { human_validation: 'valid' } }, { finding: { human_validation: 'unverified' } }, { finding: {} }, { finding: { human_validation: 'false_positive' } }]);
  assert.deepEqual(sum, { total: 4, unverified: 2, valid: 1, false_positive: 1, uncertain: 0 });
  assert.equal(s.humanValidationText(sum), '사람 검증 2/4 · 유효 1 · 오탐 1 · 미검증 2');
  assert.match(s.STRUCTURE_NOTE, /보증이 아닙니다/);
});

test('이전 SHA 경고', async () => {
  const s = await js('ai-state.js');
  const a = 'a'.repeat(40), b = 'b'.repeat(40);
  assert.equal(s.staleShaNotice(a, a), null);
  assert.equal(s.staleShaNotice(null, a), null);
  assert.equal(s.staleShaNotice(a, null), null);
  const note = s.staleShaNotice(a, b);
  assert.match(note, /이전 SHA\(aaaaaaa\)/);
  assert.match(note, /현재 head는 bbbbbbb/);
});

test('위치 문구', async () => {
  const s = await js('ai-state.js');
  assert.equal(s.locationText({ path: 'a/b.py', side: 'RIGHT', start_line: 3, end_line: 3 }), 'a/b.py:3 (변경 후(RIGHT))');
  assert.equal(s.locationText({ path: 'a/b.py', side: 'LEFT', start_line: 3, end_line: 9 }), 'a/b.py:3-9 (변경 전(LEFT))');
});

/* ───────────────────────── 동의 ───────────────────────── */
const previewOk = () => ({
  input_id: U1, status: 'ready', reviewable: true, execution_valid: true, manifest_hash: 'c'.repeat(64), policy_version: 'code-transfer-v1',
  files: [{ content_state: 'read' }, { content_state: 'read' }, { content_state: 'excluded' }],
  token_estimate: { within_limit: true },
  transfer: { provider: 'anthropic', profile_id: 'claude-opus-5-5-v1', model: 'claude-opus-5-5', external_transfer_allowed: true, credential_configured: true, credential_version: 3, key_hint: 'sk-ant-…1111' },
});

test('동의: 미리보기 값을 그대로 쓰고 하나라도 막히면 만들지 않는다', async () => {
  const s = await js('ai-state.js');
  const p = previewOk();
  assert.deepEqual(s.consentBlockers(p), []);
  assert.deepEqual(s.buildConsent(p), { input_id: U1, manifest_hash: 'c'.repeat(64), provider: 'anthropic', profile_id: 'claude-opus-5-5-v1', credential_version: 3, policy_version: 'code-transfer-v1' });
  const cases = [
    [{ status: 'building' }, /준비하는 중/],
    [{ status: 'expired' }, /지났거나 원문이 삭제/],
    [{ status: 'failed' }, /실패/],
    [{ execution_valid: false }, /유효기간/],
    [{ reviewable: false }, /보낼 수 있는 파일이 없습니다/],
    [{ transfer: { ...p.transfer, external_transfer_allowed: false } }, /외부 전송을 허용하지 않습니다/],
    [{ transfer: { ...p.transfer, credential_configured: false } }, /API key를 먼저 등록/],
    [{ transfer: null }, /전송 대상/],
    [{ token_estimate: { within_limit: false } }, /토큰 한도/],
  ];
  for (const [over, re] of cases) {
    const q = { ...p, ...over };
    assert.ok(s.consentBlockers(q).some((m) => re.test(m)), JSON.stringify(over));
    assert.equal(s.buildConsent(q), null);
  }
  assert.deepEqual(s.consentBlockers(null), ['미리보기를 불러오지 못했습니다.']);
  const text = s.consentStatement(p);
  assert.match(text, /선택한 2개 파일/);
  assert.match(text, /anthropic \/ claude-opus-5-5/);
  assert.match(text, /sk-ant-…1111 \(v3\)/);
  assert.match(text, /code-transfer-v1/);
});

/* ───────────────────────── 오류·작업 상태 ───────────────────────── */
test('오류 안내: 코드별 행동과 비밀 노출 없음', async () => {
  const s = await js('ai-state.js');
  const E = (status, code, details = {}) => ({ status, code, details });
  const act = (e) => s.describeError(e).action;
  assert.equal(act(E(0, 'NETWORK')), 'retry');
  assert.equal(act(E(401, 'AUTH_REQUIRED')), 'login');
  assert.equal(act(E(401, 'REAUTH_REQUIRED')), 'login');
  assert.equal(act(E(403, 'CSRF_FAILED')), 'reload');
  assert.equal(act(E(404, 'RESOURCE_NOT_FOUND')), 'home');
  assert.equal(act(E(404, 'RESOURCE_NOT_FOUND', { reason: 'JOB_DELETED' })), 'home');
  assert.match(s.describeError(E(404, 'RESOURCE_NOT_FOUND', { reason: 'JOB_DELETED' })).title, /삭제된 작업/);
  assert.match(s.describeError(E(404, 'RESOURCE_NOT_FOUND')).body, /구분하지 않습니다|알려 드리지 않습니다/);        // 권한 없음·삭제·남의 자료를 구분하지 않는다
  assert.equal(act(E(409, 'PR_CHANGED')), 'reselect');
  assert.equal(act(E(409, 'PREVIEW_CHANGED')), 'reload_preview');
  assert.equal(act(E(409, 'VERSION_CONFLICT')), 'reload_findings');
  assert.equal(act(E(410, 'INPUT_EXPIRED')), 'reselect');
  assert.equal(act(E(410, 'RESULT_EXPIRED')), 'home');
  assert.equal(act(E(422, 'CREDENTIAL_REQUIRED')), 'keys');
  assert.equal(act(E(422, 'CREDENTIAL_INVALID')), 'keys');
  assert.match(s.describeError(E(422, 'CREDENTIAL_INVALID', { reason: 'model_unavailable' })).body, /선택한 모델/);
  assert.equal(act(E(422, 'INVALID_STATE', { reason: 'NO_REVIEWABLE_CONTENT' })), 'reselect');
  assert.equal(act(E(429, 'BUDGET_LIMIT')), 'usage');
  assert.equal(s.describeError(E(429, 'USER_LIMIT')).tone, 'warn');
  assert.equal(act(E(429, 'UPSTREAM_RATE_LIMIT')), 'retry');
  assert.equal(act(E(503, 'UPSTREAM_UNAVAILABLE')), 'retry');
  assert.equal(act(E(503, 'ACCESS_CHECK_UNAVAILABLE')), 'retry');
  assert.equal(act(E(502, null)), 'retry');
  assert.equal(act(E(500, 'BAD_RESPONSE')), 'retry');
  assert.equal(s.describeError({ status: 429, code: 'USER_LIMIT', retryAfter: 5000 }).retryAfter, 5000);
  for (const code of ['AUTH_REQUIRED', 'PR_CHANGED', 'CREDENTIAL_INVALID', 'BUDGET_LIMIT', 'UNKNOWN_CODE']) {
    const d = s.describeError({ status: 400, code, message: 'sk-ant-api03-SECRET' });
    assert.ok(!JSON.stringify(d).includes('SECRET'), code);                                  // 서버 메시지를 그대로 쓰지 않는다
  }
});

test('작업 안내: 불명·저장 실패는 비용 위험과 명시적 재실행만 안내한다', async () => {
  const s = await js('ai-state.js');
  const job = (status, extra = {}) => ({ status, stage: null, cancel_requested: false, ...extra });
  const q = s.describeJob(job('queued', { stage: 'queued' }));
  assert.deepEqual([q.canCancel, q.canRetry, q.tone], [true, false, 'info']);
  assert.match(q.body, /아무것도 전송하지 않았습니다/);
  const r = s.describeJob(job('running', { stage: 'provider_call' }));
  assert.equal(r.title, '모델 호출 중');
  assert.ok(r.canCancel);
  assert.equal(r.body, '');
  const w = s.describeJob(job('retry_wait', { retry_after_seconds: 30 }));
  assert.match(w.body, /30초 뒤/);
  assert.match(w.body, /처리되지 않았음이 확실한 경우만/);
  const c = s.describeJob(job('cancel_requested'));
  assert.match(c.body, /비용은 발생할 수 있습니다/);
  assert.equal(c.canCancel, false);
  const unc = s.describeJob(job('failed', { error_code: 'OUTCOME_UNCERTAIN', outcome_uncertain: true }));
  assert.ok(unc.costRisk && unc.canRetry && unc.retryExplicit);
  assert.match(unc.body, /비용이 이미 발생했을 수 있습니다/);
  assert.match(unc.body, /자동으로 다시 실행하지 않았습니다/);
  const lost = s.describeJob(job('failed', { error_code: 'RESULT_LOST' }));
  assert.match(lost.body, /다시 실행하면 비용이 다시 듭니다/);
  assert.ok(lost.canRetry);
  const revoked = s.describeJob(job('failed', { error_code: 'ACCESS_REVOKED' }));
  assert.equal(revoked.canRetry, false);
  assert.match(revoked.body, /결과를 공개하지 않았습니다/);
  assert.equal(s.describeJob(job('failed', { error_code: 'CREDENTIAL_UNAVAILABLE' })).action, 'keys');
  assert.equal(s.describeJob(job('failed', { error_code: 'INPUT_EXPIRED' })).action, 'reselect');
  const unknown = s.describeJob(job('failed', { error_code: 'SOMETHING_NEW' }));
  assert.match(unknown.body, /사용량을 확인/);
  assert.ok(unknown.costRisk);
  const cancelled = s.describeJob(job('cancelled'));
  assert.ok(cancelled.canRetry && cancelled.retryExplicit);
  assert.equal(s.describeJob(job('succeeded')).tone, 'ok');
  assert.equal(s.describeJob(null), null);
  for (const [code, g] of Object.entries(s.JOB_ERRORS)) {
    assert.ok(g.title && g.body, code);
    for (const bad of s.FORBIDDEN_PHRASES) assert.ok(!g.body.includes(bad) && !g.title.includes(bad), code);
  }
  assert.ok(s.isTerminalStatus('failed') && s.isTerminalStatus('cancelled') && !s.isTerminalStatus('cancel_requested'));
});

/* ───────────────────────── API 클라이언트 ───────────────────────── */
function res(status, body, headers = {}) {
  return {
    ok: status >= 200 && status < 300, status,
    headers: { get: (k) => headers[k] ?? headers[k.toLowerCase()] ?? null },
    json: async () => { if (body === undefined) throw new SyntaxError('bad json'); return body; },
  };
}
const envelope = (code, extra = {}) => ({ error: { code, message: `m-${code}`, retryable: false, request_id: 'r1', details: {}, ...extra } });

test('api: GET은 CSRF 없이, 변경 요청은 CSRF·멱등 key와 함께 보낸다', async () => {
  const { createAiClient } = await js('ai-api.js');
  const calls = [];
  const client = createAiClient({ fetchImpl: async (url, opts = {}) => {
    calls.push([url, opts]);
    if (url === '/api/auth/csrf') return res(200, { token: 'tok-1' });
    return res(200, { ok: true });
  } });
  await client.providers();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1].method, 'GET');
  assert.equal(calls[0][1].headers['X-CSRF-Token'], undefined);
  assert.equal(calls[0][1].cache, 'no-store');
  assert.equal(calls[0][1].credentials, 'same-origin');
  await client.createJob({ kind: 'review' }, 'idem-1');
  const post = calls.at(-1);
  assert.equal(post[0], '/api/ai/v1/jobs');
  assert.deepEqual([post[1].method, post[1].headers['X-CSRF-Token'], post[1].headers['Idempotency-Key'], post[1].headers['Content-Type']], ['POST', 'tok-1', 'idem-1', 'application/json']);
  assert.equal(post[1].body, JSON.stringify({ kind: 'review' }));
  await client.cancelJob(U1);                                                           // 두 번째 변경 요청은 토큰을 다시 받지 않는다
  assert.equal(calls.filter(([u]) => u === '/api/auth/csrf').length, 1);
  await client.pulls(7, { state: 'merged', cursor: 'c 1' });
  assert.equal(calls.at(-1)[0], '/api/ai/v1/pulls?repository_id=7&state=merged&cursor=c+1&limit=30');
  await client.jobs(7, { inputId: U1 });
  assert.match(calls.at(-1)[0], /repository_id=7/);
  assert.match(calls.at(-1)[0], new RegExp(`input_id=${U1}`));
  assert.ok(!calls.at(-1)[0].includes('cursor='));
});

test('api: CSRF가 낡았으면 새로 받아 같은 멱등 key로 한 번만 다시 보낸다', async () => {
  const { createAiClient } = await js('ai-api.js');
  let csrfN = 0;
  const posts = [];
  const client = createAiClient({ fetchImpl: async (url, opts = {}) => {
    if (url === '/api/auth/csrf') return res(200, { token: `t${++csrfN}` });
    posts.push(opts);
    return posts.length === 1 ? res(403, envelope('CSRF_FAILED')) : res(202, { job_id: U1 });
  } });
  const out = await client.createJob({ a: 1 }, 'same-key');
  assert.equal(out.job_id, U1);
  assert.equal(posts.length, 2);
  assert.deepEqual(posts.map((p) => p.headers['X-CSRF-Token']), ['t1', 't2']);
  assert.deepEqual(posts.map((p) => p.headers['Idempotency-Key']), ['same-key', 'same-key']);
  const always = createAiClient({ fetchImpl: async (url) => (url === '/api/auth/csrf' ? res(200, { token: 'x' }) : res(403, envelope('CSRF_FAILED'))) });
  await assert.rejects(() => always.cancelJob(U1), (e) => e.code === 'CSRF_FAILED');       // 무한 재시도 없음
});

test('api: 오류 정규화(코드·details·Retry-After) 와 비정상 응답', async () => {
  const { createAiClient, AiApiError } = await js('ai-api.js');
  const make = (r) => createAiClient({ fetchImpl: async () => (typeof r === 'function' ? r() : r), now: () => 0 });
  await assert.rejects(() => make(res(409, envelope('PREVIEW_CHANGED', { details: { x: 1 } }))).job(U1), (e) => {
    assert.ok(e instanceof AiApiError);
    assert.deepEqual([e.status, e.code, e.details, e.requestId], [409, 'PREVIEW_CHANGED', { x: 1 }, 'r1']);
    return true;
  });
  await assert.rejects(() => make(res(429, envelope('USER_LIMIT'), { 'Retry-After': '7' })).usage(), (e) => e.retryAfter === 7000 && e.status === 429);
  await assert.rejects(() => make(res(502, undefined)).job(U1), (e) => e.status === 502 && e.code === 'BAD_RESPONSE');   // HTML 응답
  await assert.rejects(() => make(res(401, { detail: 'x' })).job(U1), (e) => e.status === 401 && e.code === 'AUTH_REQUIRED');
  await assert.rejects(() => make(res(500, { detail: 'boom' })).job(U1), (e) => e.status === 500 && e.code === 'BAD_RESPONSE');
  await assert.rejects(() => make(res(200, undefined)).job(U1), (e) => e.code === 'BAD_RESPONSE');                      // 200인데 JSON이 아님
  await assert.rejects(() => make(res(200, [1, 2])).job(U1), (e) => e.code === 'BAD_RESPONSE');                        // 객체가 아님
  await assert.rejects(() => make(() => { throw new TypeError('fetch failed'); }).job(U1), (e) => e.status === 0 && e.code === 'NETWORK' && e.retryable);
  const abort = createAiClient({ fetchImpl: async () => { const e = new Error('x'); e.name = 'AbortError'; throw e; } });
  await assert.rejects(() => abort.job(U1), (e) => e.name === 'AbortError');                                          // abort는 오류 화면으로 가지 않는다
  const me = createAiClient({ fetchImpl: async () => res(401, { detail: 'x' }) });
  assert.equal(await me.me(), null);
});

test('api: key는 요청 본문에만 있고 오류·메시지에 남지 않는다', async () => {
  const { createAiClient } = await js('ai-api.js');
  const bodies = [];
  const client = createAiClient({ fetchImpl: async (url, opts = {}) => {
    if (url === '/api/auth/csrf') return res(200, { token: 't' });
    bodies.push(opts.body);
    return res(422, envelope('CREDENTIAL_INVALID', { details: { reason: 'auth_failed' } }));
  } });
  await assert.rejects(() => client.putCredential('anthropic', { apiKey: 'sk-ant-api03-SECRETVALUE', profileId: 'claude-opus-5-5-v1' }), (e) => {
    assert.ok(!JSON.stringify({ m: e.message, d: e.details, c: e.code }).includes('SECRETVALUE'));
    return true;
  });
  assert.deepEqual(JSON.parse(bodies[0]), { api_key: 'sk-ant-api03-SECRETVALUE', profile_id: 'claude-opus-5-5-v1', verify: true });
});

test('api: 멱등 key 생성은 호출마다 다르다', async () => {
  const { newIdempotencyKey } = await js('ai-api.js');
  const keys = new Set(Array.from({ length: 50 }, newIdempotencyKey));
  assert.equal(keys.size, 50);
  for (const k of keys) assert.match(k, /^[\x21-\x7e]{1,255}$/);
});

/* ───────────────────────── 접수 응답 유실·재전송 ───────────────────────── */
/* 모의 서버: Idempotency-Key가 같으면 같은 작업을 돌려준다(실제 서버와 같은 규칙). 작업·generation 수를 센다. */
function fakeAcceptServer(repo = 5) {
  const byKey = new Map();
  const jobs = [];
  return {
    jobs, byKey,
    accept(key) {
      if (!byKey.has(key)) { const job = { job_id: `00000000-0000-4000-8000-${String(jobs.length + 1).padStart(12, '0')}`, repository_id: repo, generation: jobs.length }; jobs.push(job); byKey.set(key, job); }
      return byKey.get(key);
    },
  };
}
const payloadJob = { kind: 'review', consent: { input_id: U1, manifest_hash: 'c'.repeat(64) }, force_regenerate: false };

test('접수: 202인데 응답 JSON이 깨졌어도 같은 key로 다시 보내 작업이 늘지 않는다', async () => {
  const s = await js('ai-state.js');
  const { createAiClient, newIdempotencyKey } = await js('ai-api.js');
  const server = fakeAcceptServer();
  const intent = s.createIntent(newIdempotencyKey);
  let broken = true;
  const client = createAiClient({ fetchImpl: async (url, opts = {}) => {
    if (url === '/api/auth/csrf') return res(200, { token: 't' });
    const job = server.accept(opts.headers['Idempotency-Key']);          // 서버는 접수했다
    return broken ? res(202, undefined) : res(202, job);                  // 첫 응답은 본문이 깨져 도착한다
  } });
  const send = (key) => client.createJob(payloadJob, key);
  const valid = (o) => s.isAcceptedFor(o, 'job_id', 5);
  await assert.rejects(() => s.submitWithIntent(intent, payloadJob, send, valid), (e) => e.status === 202 && e.code === 'BAD_RESPONSE' && s.isAmbiguousError(e));
  assert.equal(server.jobs.length, 1);
  broken = false;
  const out = await s.submitWithIntent(intent, payloadJob, send, valid);   // 사용자가 다시 누른다
  assert.equal(server.jobs.length, 1);                                    // 새 작업·generation이 생기지 않았다
  assert.equal(out.job_id, server.jobs[0].job_id);
  assert.equal(server.byKey.size, 1);                                     // 두 번 다 같은 key
  const later = await s.submitWithIntent(intent, payloadJob, (key) => Promise.resolve(server.accept(key)), valid);   // 접수가 확인된 뒤의 새 요청은 새 key
  assert.notEqual(later.job_id, out.job_id);
  assert.equal(server.jobs.length, 2);
});

test('접수: 검증 전에는 key를 버리지 않는다({} 응답·다른 저장소·id 없음·uuid 아님)', async () => {
  const s = await js('ai-state.js');
  let n = 0;
  const server = fakeAcceptServer(5);
  const intent = s.createIntent(() => `k${++n}`);
  const valid = (o) => s.isAcceptedFor(o, 'job_id', 5);
  const bad = [{}, null, [], { job_id: 'not-a-uuid', repository_id: 5 }, { job_id: U1, repository_id: 6 }, { job_id: U1 }, 'ok'];
  for (const response of bad) {
    const calls = [];
    await assert.rejects(() => s.submitWithIntent(intent, payloadJob, async (key) => { calls.push(key); server.accept(key); return response; }, valid),
      (e) => e.code === 'BAD_RESPONSE' && e.status === 0, JSON.stringify(response));
    assert.deepEqual(calls, ['k1']);                                      // 같은 key
  }
  assert.equal(n, 1);                                                    // key가 한 번도 새로 만들어지지 않았다
  assert.equal(server.jobs.length, 1);
  const ok = await s.submitWithIntent(intent, payloadJob, async (key) => server.accept(key), valid);
  assert.equal(ok.job_id, server.jobs[0].job_id);
  assert.equal(server.jobs.length, 1);
});

test('접수: 접수 여부를 모르는 오류는 key 유지, 서버가 분명히 거절하면 key 종료', async () => {
  const s = await js('ai-state.js');
  const { AiApiError } = await js('ai-api.js');
  const valid = (o) => s.isAcceptedFor(o, 'job_id', 5);
  const ambiguous = [new AiApiError('x', { status: 0, code: 'NETWORK' }), new AiApiError('x', { status: 503, code: 'UPSTREAM_UNAVAILABLE' }), new AiApiError('x', { status: 500, code: 'BAD_RESPONSE' }),
    new AiApiError('x', { status: 502, code: null }), new AiApiError('x', { status: 202, code: 'BAD_RESPONSE' }), Object.assign(new Error('x'), { name: 'AbortError' })];
  for (const err of ambiguous) {
    let n = 0;
    const intent = s.createIntent(() => `k${++n}`);
    await assert.rejects(() => s.submitWithIntent(intent, payloadJob, async () => { throw err; }, valid));
    await assert.rejects(() => s.submitWithIntent(intent, payloadJob, async () => { throw err; }, valid));
    assert.equal(n, 1, `${err.status}/${err.code}/${err.name}`);          // 두 번 모두 같은 key
  }
  for (const [status, code] of [[409, 'PREVIEW_CHANGED'], [422, 'CREDENTIAL_REQUIRED'], [410, 'INPUT_EXPIRED'], [429, 'USER_LIMIT'], [403, 'CSRF_FAILED'], [404, 'RESOURCE_NOT_FOUND']]) {
    let n = 0;
    const intent = s.createIntent(() => `k${++n}`);
    await assert.rejects(() => s.submitWithIntent(intent, payloadJob, async () => { throw new AiApiError('x', { status, code }); }, valid));
    await assert.rejects(() => s.submitWithIntent(intent, payloadJob, async () => { throw new AiApiError('x', { status, code }); }, valid));
    assert.equal(n, 2, code);                                              // 분명한 거절 뒤에는 새 요청
  }
});

test('접수: isAcceptedFor / isAmbiguousError 경계', async () => {
  const s = await js('ai-state.js');
  assert.equal(s.isAcceptedFor({ input_id: U1, repository_id: 5 }, 'input_id', 5), true);
  assert.equal(s.isAcceptedFor({ input_id: U1.toUpperCase(), repository_id: 5 }, 'input_id', 5), true);
  assert.equal(s.isAcceptedFor({ input_id: U1, repository_id: '5' }, 'input_id', 5), false);
  assert.equal(s.isAcceptedFor(undefined, 'job_id', 5), false);
  assert.equal(s.isAmbiguousError({ status: 422, code: 'INVALID_SELECTION' }), false);
  assert.equal(s.isAmbiguousError({ status: 404, code: 'RESOURCE_NOT_FOUND' }), false);
  assert.equal(s.isAmbiguousError(undefined), true);
});

test('미리보기: 코드 확보 상태를 "전송됨"이라고 표시하지 않는다', async () => {
  const s = await js('ai-state.js');
  assert.equal(s.contentStateLabel('read', 'ready'), '전송 예정');
  assert.equal(s.contentStateLabel('read', 'building'), '코드 확보됨');
  assert.equal(s.contentStateLabel('read', 'expired'), '코드 확보됨');
  assert.equal(s.contentStateLabel('excluded', 'ready'), '제외됨');
  assert.equal(s.contentStateLabel('failed', 'ready'), '가져오기 실패');
  for (const status of ['building', 'ready', 'failed', 'expired']) {
    for (const state of ['read', 'excluded', 'failed']) assert.ok(!s.contentStateLabel(state, status).includes('전송됨'), `${state}/${status}`);
  }
  for (const label of Object.values(s.CONTENT_STATE_LABEL)) assert.ok(!label.includes('전송됨'));
});

/* ───────────────────────── 폴링 ───────────────────────── */
const apiErr = async (status, extra = {}) => {
  const { AiApiError } = await js('ai-api.js');
  return new AiApiError('x', { status, code: extra.code || null, retryAfter: extra.retryAfter ?? null });
};

test('poll: 직렬 조회, 변화 없으면 간격을 늘리고 종료 상태에서 멈춘다', async () => {
  const { createAiPoller } = await js('ai-poll.js');
  const clock = fakeClock();
  const updates = [];
  let n = 0, concurrent = 0, maxConcurrent = 0, stopped = null;
  const poller = createAiPoller({
    fetchOnce: async () => { concurrent += 1; maxConcurrent = Math.max(maxConcurrent, concurrent); await new Promise((r) => setImmediate(r)); concurrent -= 1; n += 1; return { status: n >= 6 ? 'succeeded' : 'running' }; },
    isDone: (b) => b.status === 'succeeded', signature: (b) => b.status,
    onUpdate: (b) => updates.push(b.status), onStop: (i) => { stopped = i; },
    setTimer: clock.setTimer, clearTimer: clock.clearTimer, now: clock.now, isHidden: () => false,
  });
  poller.start();
  await clock.advance(0);
  assert.equal(n, 1);
  await clock.advance(60000);
  assert.equal(n, 6);
  assert.equal(maxConcurrent, 1);
  assert.equal(stopped.reason, 'done');
  assert.equal(poller.active, false);
  assert.equal(clock.pending(), 0);
  const before = n;
  await clock.advance(60000);
  assert.equal(n, before);                                                                  // 종료 뒤에는 더 조회하지 않는다
});

test('poll: stop 뒤에 도착한 응답은 버리고 진행 중 요청은 abort된다(작업·저장소 전환)', async () => {
  const { createAiPoller } = await js('ai-poll.js');
  const clock = fakeClock();
  let release, aborted = false;
  const updates = [];
  const poller = createAiPoller({
    fetchOnce: (signal) => new Promise((resolve) => { release = () => resolve({ status: 'running', who: 'old' }); signal?.addEventListener('abort', () => { aborted = true; }); }),
    onUpdate: (b) => updates.push(b), setTimer: clock.setTimer, clearTimer: clock.clearTimer, now: clock.now, isHidden: () => false,
  });
  poller.start();
  await clock.advance(0);
  poller.stop();
  assert.equal(aborted, true);
  release();
  await clock.advance(1000);
  assert.deepEqual(updates, []);                                                            // 늦은 응답이 새 화면을 덮지 않는다
  assert.equal(clock.pending(), 0);
});

test('poll: 401·404·4xx는 즉시 멈춘다', async () => {
  const { createAiPoller } = await js('ai-poll.js');
  for (const [status, reason] of [[401, 'auth'], [404, 'not_found'], [422, 'client_error'], [409, 'client_error']]) {
    const clock = fakeClock();
    let stop = null, calls = 0;
    const err = await apiErr(status);
    const p = createAiPoller({ fetchOnce: async () => { calls += 1; throw err; }, onStop: (i) => { stop = i; }, setTimer: clock.setTimer, clearTimer: clock.clearTimer, now: clock.now, isHidden: () => false });
    p.start();
    await clock.advance(60000);
    assert.equal(calls, 1, String(status));
    assert.equal(stop.reason, reason);
    assert.equal(stop.error.status, status);
  }
});

test('poll: 429는 Retry-After를 따르고 5xx·네트워크 오류는 횟수 제한 backoff', async () => {
  const { createAiPoller } = await js('ai-poll.js');
  const { POLL } = await js('poll.js');
  {
    const clock = fakeClock();
    const times = [], notices = [];
    const err = await apiErr(429, { retryAfter: 12000 });
    const p = createAiPoller({ fetchOnce: async () => { times.push(clock.now()); if (times.length === 1) throw err; return { status: 'running' }; },
      onNotice: (n) => notices.push(n), setTimer: clock.setTimer, clearTimer: clock.clearTimer, now: clock.now, isHidden: () => false });
    p.start();
    await clock.advance(11999);
    assert.equal(times.length, 1);                                                          // 서버가 요청한 대기를 건너뛰지 않는다
    await clock.advance(1);
    assert.equal(times.length, 2);
    assert.equal(notices[0].kind, 'rate_limit');
    assert.equal(notices[0].retryAt, 12000);
    p.stop();
  }
  {
    const clock = fakeClock();
    let calls = 0, stop = null;
    const notices = [];
    const err = await apiErr(503);
    const p = createAiPoller({ fetchOnce: async () => { calls += 1; throw err; }, onNotice: (n) => notices.push(n.attempt),
      onStop: (i) => { stop = i; }, setTimer: clock.setTimer, clearTimer: clock.clearTimer, now: clock.now, isHidden: () => false });
    p.start();
    await clock.advance(10 * 60 * 1000);
    assert.equal(calls, POLL.MAX_TRANSIENT + 1);
    assert.equal(stop.reason, 'unreachable');
    assert.deepEqual(notices, [1, 2, 3, 4, 5]);
  }
  {
    const clock = fakeClock();
    let calls = 0, stop = null;
    const err = await apiErr(0, { code: 'NETWORK' });
    const p = createAiPoller({ fetchOnce: async () => { calls += 1; if (calls < 3) throw err; return { status: 'running' }; },
      onStop: (i) => { stop = i; }, setTimer: clock.setTimer, clearTimer: clock.clearTimer, now: clock.now, isHidden: () => false });
    p.start();
    await clock.advance(20000);
    assert.ok(calls >= 3);
    assert.equal(stop, null);                                                               // 복구되면 계속한다
    p.stop();
  }
});

test('poll: 숨긴 탭은 느리게, nudge는 정상 대기 중에만 즉시 확인한다', async () => {
  const { createAiPoller } = await js('ai-poll.js');
  const { POLL } = await js('poll.js');
  const clock = fakeClock();
  let hidden = true, calls = 0;
  const p = createAiPoller({ fetchOnce: async () => { calls += 1; return { status: 'running' }; }, signature: () => 'same',
    setTimer: clock.setTimer, clearTimer: clock.clearTimer, now: clock.now, isHidden: () => hidden });
  p.start();
  await clock.advance(0);
  assert.equal(calls, 1);
  await clock.advance(POLL.HIDDEN_MIN_MS - 1);
  assert.equal(calls, 1);
  hidden = false;
  p.nudge();                                                                                // 탭이 다시 보이면 바로 한 번
  await clock.advance(0);
  assert.equal(calls, 2);
  p.stop();
  p.nudge();
  await clock.advance(60000);
  assert.equal(calls, 2);
});

test('poll: onUpdate 안에서 stop해도 다음 조회를 예약하지 않는다', async () => {
  const { createAiPoller } = await js('ai-poll.js');
  const clock = fakeClock();
  let calls = 0;
  let poller;
  poller = createAiPoller({ fetchOnce: async () => { calls += 1; return { status: 'running' }; }, onUpdate: () => poller.stop(),
    setTimer: clock.setTimer, clearTimer: clock.clearTimer, now: clock.now, isHidden: () => false });
  poller.start();
  await clock.advance(60000);
  assert.equal(calls, 1);
  assert.equal(clock.pending(), 0);
});

/* ───────────────────────── 실행 ───────────────────────── */
test('프로젝트 선택: 본인 목록 조회·취소 신호와 응답 검증', async () => {
  const { createAiClient } = await js('ai-api.js');
  const controller = new AbortController();
  const projects = [{ id: 2, url: 'https://github.com/team/repo' }];
  const client = createAiClient({ fetchImpl: async (url, options) => {
    assert.equal(url, '/repositories/mine');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.cache, 'no-store');
    assert.equal(options.signal, controller.signal);
    return { ok: true, status: 200, json: async () => projects };
  } });
  assert.deepEqual(await client.myProjects(controller.signal), projects);
  for (const body of [{}, [null], [{id:0}], [{id:'2'}]]) {
    const bad = createAiClient({ fetchImpl: async () => ({ok:true,status:200,json:async()=>body}) });
    await assert.rejects(bad.myProjects(), e => e.code === 'BAD_RESPONSE');
  }
  const unauth = createAiClient({ fetchImpl: async () => ({ok:false,status:401}) });
  await assert.rejects(unauth.myProjects(), e => e.code === 'AUTH_REQUIRED');
});

test('이력 근거: snapshot 값만 옮기고 후보·제외 이유를 오류로 단정하지 않는다', async () => {
  const s = await js('ai-state.js');
  const ok = s.historyEvidenceSummary({ status: 'ok', exclusion_reasons: [], evidence: { notes: ['n1'], data_quality: { commit_count: 42, unidentified_commits: 5, co_change_unit: 'pull_request' },
    paths: [{ path: 'b.py', match: 'old_path', matched_path: 'a.py', status: 'observed', observations: { change_freq: 3, churn: 9, bugfix_count: 0 },
      ownership: { observation_count: 3, contributor_count: 1, top_owner_share: 1 }, co_change: [{ path: 't.py', co_change_count: 6, changed_in_pr: false, low_support: true }], limitations: ['x'] }] } });
  assert.equal(ok.excluded, false);
  assert.deepEqual(ok.quality.map((q) => [q.label, q.value]), [['커밋', '42'], ['작성자 미식별 커밋', '5'], ['함께 변경 단위', 'pull_request']]);
  const p = ok.paths[0];
  assert.equal(p.matched, 'a.py');
  assert.deepEqual(p.observations, ['변경 3회', 'churn 9', '버그 수정 0회']);
  assert.deepEqual(p.coChange, [{ path: 't.py', count: 6, changedInPr: false, lowSupport: true }]);
  const ex = s.historyEvidenceSummary({ status: 'excluded', exclusion_reasons: ['formula_unsupported'], evidence: { notes: ['제외'], paths: [{ path: 'leak.py' }] } });
  assert.deepEqual([ex.excluded, ex.paths, ex.reasons], [true, [], ['formula_unsupported']]);   // 제외된 snapshot의 값은 싣지 않는다
  const { createAiClient } = await js('ai-api.js');
  const calls = [];
  const c = createAiClient({ fetchImpl: async (u) => { calls.push(String(u)); return { ok: true, status: 200, json: async () => ({ result_id: 'x', evidence: [], snapshots: [], repository_id: 1 }) }; } });
  await c.history('00000000-0000-4000-8000-000000000001');
  await c.historyOptions(5);
  assert.ok(calls[0].endsWith('/results/00000000-0000-4000-8000-000000000001/history') && calls[1].includes('/history/snapshots?repository_id=5'));
});

test('커밋 주소·라벨 및 조회 API', async () => {
  const s = await js('ai-state.js');
  const sha = 'a'.repeat(40);
  const route = s.parseRoute(s.buildRoute({ repo: 5, commit: sha }));
  assert.equal(route.view, s.VIEWS.FILES);
  assert.equal(route.commit, sha);
  assert.equal(route.pr, null);
  assert.equal(s.sourceLabel({ source_type: 'commit', head_sha: sha }), `커밋 ${s.shortSha(sha)}`);
  assert.ok(s.parseRoute('?repo=5&commit=bad').invalid.includes('commit'));
  assert.equal(s.parseRoute(`?repo=5&pr=7&commit=${sha}`).commit, null);
  const { createAiClient } = await js('ai-api.js');
  const urls = [];
  const client = createAiClient({ fetchImpl: async (u) => { urls.push(String(u)); return { ok: true, status: 200, json: async () => ({}) }; } });
  await client.commits(5, { cursor: 'next' });
  await client.commitFiles(5, sha);
  assert.ok(urls[0].includes('/commits?repository_id=5') && urls[0].includes('cursor=next'));
  assert.ok(urls[1].includes(`/commits/${sha}/files?repository_id=5`));
});

test('팀원 결과(읽기 전용): 주소와 조회 API', async () => {
  const s = await js('ai-state.js');
  const route = s.parseRoute(s.buildRoute({ repo: 5, shared: U1 }));
  assert.equal(route.view, s.VIEWS.SHARED);
  assert.equal(route.shared, U1);
  assert.equal(s.buildRoute({ repo: 5, shared: U1 }), `?repo=5&shared=${U1}`);
  assert.equal(s.buildRoute({ shared: U1 }), '');                                       // repo 없이는 만들지 않는다
  assert.equal(s.parseRoute(`?shared=${U1}`).view, s.VIEWS.NO_REPO);
  assert.equal(s.parseRoute(`?repo=5&shared=${U1}&job=${U2}`).view, s.VIEWS.JOB);        // 내 작업이 우선
  assert.equal(s.parseRoute(`?repo=5&shared=${U1}&job=${U2}`).shared, null);
  assert.equal(s.parseRoute('?repo=5&shared=nope').view, s.VIEWS.HOME);
  assert.ok(s.parseRoute('?repo=5&shared=nope').invalid.includes('shared'));
  const { createAiClient } = await js('ai-api.js');
  const calls = [];
  const client = createAiClient({ fetchImpl: async (u, o) => { calls.push([String(u), o.method]); return { ok: true, status: 200, json: async () => ({}) }; } });
  await client.sharedResults(5, { cursor: 'c1' });
  await client.sharedResult(U1);
  await client.sharedFindings(U1, { cursor: 'c2' });
  assert.ok(calls[0][0].includes('/api/ai/v1/shared/results?repository_id=5') && calls[0][0].includes('cursor=c1'));
  assert.ok(calls[1][0].endsWith(`/api/ai/v1/shared/results/${U1}`));
  assert.ok(calls[2][0].includes(`/api/ai/v1/shared/results/${U1}/findings`) && calls[2][0].includes('cursor=c2'));
  assert.deepEqual(calls.map((c) => c[1]), ['GET', 'GET', 'GET']);                      // 읽기만 한다
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`PASS ${name}`); }
    catch (e) { failed += 1; console.log(`FAIL ${name}\n  ${e && e.stack ? e.stack.split('\n').slice(0, 4).join('\n  ') : e}`); }
  }
  console.log(`\n${tests.length - failed}/${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
