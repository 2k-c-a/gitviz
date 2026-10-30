// node tests/frontend/test_metric_display.cjs — DOM 없이 집계와 설명 회귀 검증
// code-analysis.js / drilldown.js 를 실제 ES 모듈로 불러온다(브라우저 전역은 최소한으로 대체).
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');

const stub = (name, value) => Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
const store = () => ({ getItem: () => null, setItem() {}, removeItem() {} });
stub('window', globalThis);
const fixtureEvents = new EventTarget();
stub('addEventListener', fixtureEvents.addEventListener.bind(fixtureEvents));
stub('dispatchEvent', fixtureEvents.dispatchEvent.bind(fixtureEvents));
stub('document', { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [], addEventListener() {} });
stub('localStorage', store());
stub('sessionStorage', {});
stub('location', { search: '', pathname: '/pages/code-analysis.html', href: 'http://localhost/pages/code-analysis.html' });

const load = (f) => import(pathToFileURL(path.resolve(__dirname, '..', '..', 'frontend', 'js', f)).href);

async function main() {
  const ca = await load('code-analysis.js');
  const dd = await load('drilldown.js');
  const policy = await load('policy.js');

  /* ── 폴더 평균: 구조와 무관하게 값이 있는 파일 기준, null은 제외 ── */
  const leaf = (risk, bf = null, coupling = null) => ({ risk_score: risk, bus_factor: bf, coupling_score: coupling });
  const tree = { children: [leaf(1, 1, 1), { children: [leaf(0, 3, 0), leaf(0, null, 0), leaf(null)] }] };
  ca.computeAverages(tree);
  assert.equal(tree.avgRisk, 1 / 3, '폴더별 평균 대신 유효 파일별 평균');
  assert.equal(tree.avgBF, 2, '지표별 null 파일 제외');
  assert.equal(tree.avgCoupling, 1 / 3);
  assert.equal(tree.fileCount, 4);
  const flat = { children: [leaf(1, 1, 1), leaf(0, 3, 0), leaf(0, null, 0), leaf(null)] };
  ca.computeAverages(flat);
  assert.equal(tree.avgRisk, flat.avgRisk, '폴더 구조가 달라도 같은 평균');
  const empty = { children: [] };
  ca.computeAverages(empty);
  assert.equal(empty.avgRisk, null);
  assert.equal(empty.fileCount, 0);

  /* ── 함께 수정 정도 평균: /tree는 후보 없는 파일을 coupling_score 0·coupling_support 0으로 내려준다.
        그 0은 '결합 없음'이 아니라 '후보 없음'이라 평균에 넣지 않는다(목록의 '값 없음' 처리와 같다) ── */
  const cand = (score, support) => ({ coupling_score: score, coupling_support: support });
  const ct = { children: [cand(0.8, 6), cand(0, 0), { children: [cand(0.4, 3), cand(0, 0), cand(0, 0)] }] };
  ca.computeAverages(ct);
  assert.equal(ct.metricCounts.avgCoupling, 2, '후보 있는 파일만 센다');
  assert.ok(Math.abs(ct.avgCoupling - 0.6) < 1e-12, `후보 없는 파일을 0으로 넣으면 0.4가 된다: ${ct.avgCoupling}`);
  assert.equal(ct.children[1].avgCoupling, null, '후보 없는 파일은 값 없음(회색)');
  assert.ok(Math.abs(ct.children[2].avgCoupling - 0.4) < 1e-12, '하위 폴더도 후보 있는 파일만');
  const noCand = { children: [cand(0, 0), { children: [cand(0, 0)] }] };
  ca.computeAverages(noCand);
  assert.equal(noCand.avgCoupling, null, '후보가 하나도 없는 폴더는 0이 아니라 값 없음');
  assert.equal(noCand.metricCounts.avgCoupling, 0);
  const zeroWithSupport = { children: [cand(0, 2), cand(0.6, 4)] };
  ca.computeAverages(zeroWithSupport);
  assert.ok(Math.abs(zeroWithSupport.avgCoupling - 0.3) < 1e-12, '후보가 있으면 점수 0도 값으로 평균에 포함');

  /* ── 새 버전: maintenance_priority가 기준. 키가 있으면 null을 risk_score(호환 별칭)로 대체하지 않는다 ── */
  const v3 = { children: [{ maintenance_priority: 0.5, risk_score: 0.5 }, { maintenance_priority: null, risk_score: 0.9 }, { maintenance_priority: 0, risk_score: 0 }] };
  ca.computeAverages(v3);
  assert.equal(v3.avgRisk, 0.25, 'null은 평균에서 제외하고 0은 값으로 포함');
  assert.equal(v3.metricCounts.avgRisk, 2);

  /* ── 파일 상세 설명: 새 정책 문구, 점수 없음/0/낮음 구분 ── */
  const v3p = policy.policyOf('gitviz-2026.10-priority-v3');
  const explanation = dd.buildRiskExplanation({ maintenance_priority: 0.8, coupling_score: 1, coupled_file_count: 2, coupling_support: 3 }, { bus_factor: 1 }, v3p);
  assert.ok(explanation.includes('점검 우선순위 0.80'));
  assert.ok(explanation.includes('표시 임계값(0.7) 이상'));
  assert.ok(explanation.includes('80%'), '한 명 집중 파일 설명');
  assert.ok(explanation.includes('결함 발생 확률이 아닌'));
  assert.ok(!explanation.includes('아는 사람이 한 명뿐'));
  assert.ok(!explanation.includes('영향을 줄 수'), '미래 영향 단정 없음');
  assert.ok(explanation.includes('실제 의존성'), '함께 수정 정도는 실제 의존성이 아님을 밝힌다');
  assert.ok(!explanation.includes('refactor'), '새 버전 설명에 refactor 초기화 없음');

  const zero = dd.buildRiskExplanation({ maintenance_priority: 0 }, { bus_factor: null }, v3p);
  assert.ok(zero.includes('0.00') && zero.includes('안전을 보장하지는 않습니다'), '0은 점수 있음(안전 아님)');
  assert.equal(dd.buildRiskExplanation({ maintenance_priority: null }, {}, v3p), null, '점수 없음이면 높다/낮다를 말하지 않는다');
  assert.equal(dd.buildRiskExplanation(null, {}, v3p), null);

  const legacy = policy.policyOf('gitviz-2026.10-snapshot-v2');
  const old = dd.buildRiskExplanation({ risk_score: 0.3 }, { bus_factor: 2 }, legacy);
  assert.ok(old.includes('Risk Score 0.30'), '이전 버전은 Risk Score 표기');

  /* ── 파일 상세 렌더링: 가짜 fetch로 실제 drilldown.js를 끝까지 실행한다 ── */
  const els = {};
  for (const id of ['drilldown-modal', 'dd-title', 'dd-subtitle', 'dd-body', 'dd-back-btn', 'dd-code-btn']) {
    els[id] = { style: {}, classList: { add() {}, remove() {} }, textContent: '', innerHTML: '', title: '',
      replaceChildren() { this.innerHTML = '불러오는 중...'; } };
  }
  document.getElementById = (id) => els[id] || null;
  document.createElement = () => ({ setAttribute() {}, append() {}, addEventListener() {} });
  stub('history', { replaceState() {} });

  const V3 = 'gitviz-2026.10-priority-v3';
  const coupled = (id, score, adjusted, co) => ({ file_id: id, path: `f${id}.py`, co_change_count: co, coupling_score: score, adjusted_score: adjusted,
    selected_change_count: 10, other_change_count: 10, selected_to_other_ratio: 0.5, other_to_selected_ratio: 0.5, low_support: false });
  let detailBody = {};
  const requested = [];
  stub('fetch', async (url) => {
    requested.push(url);
    const body = url.includes('/analytics/snapshot')
      ? { snapshot: { id: 7, formula_version: V3, window_start: '2026-04-06T00:00:00+00:00', window_end: '2026-10-03T00:00:00+00:00' } }
      : url.includes('/analytics/summary') ? { repository: { url: 'https://github.com/o/r', target_sha: 'abc' } }
      : url.includes('/analytics/files/') ? detailBody : {};
    return { ok: true, status: 200, headers: { get: () => null }, json: async () => body };
  });
  const render = async (fileId, extra) => {
    // Each case substitutes a different server snapshot payload: start a fresh account/cache context.
    dispatchEvent(new Event('gitviz-account-changed'));
    els['dd-body'].innerHTML = '불러오는 중...';
    dd.openFileDrilldown(fileId, extra);
    for (let i = 0; i < 100 && els['dd-body'].innerHTML.includes('불러오는 중'); i++) await new Promise((r) => setImmediate(r));
    return els['dd-body'].innerHTML;
  };
  const detail = (extra = {}) => ({ file: { id: 5, path: 'src/a.py' }, editors: [], co_change_unit: 'pr', coupled_files: [], ...extra });

  /* ② 점수 상위 목록(최대 10,000개) 밖에 있는 실제 변경 파일 — 상세가 준 자기 상태를 쓴다 */
  detailBody = detail({ maintenance_priority: 0.12, evidence_status: 'low_support', change_freq: 3, churn: 40, bugfix_count: 0,
    priority_components: { change_freq: 0.1, churn: 0.2, bugfix_count: 0.05 }, bus_factor: 1, observation_count: 3, low_support: true });
  let html = await render(5);
  assert.ok(!html.includes('관측 변경 없음'), '대형 저장소에서 실제 변경된 파일을 관측 변경 없음으로 표시하지 않는다');
  assert.ok(html.includes('표본 부족') && html.includes('0.12'), '그 파일의 실제 상태와 점수');
  assert.ok(html.includes('3회') && html.includes('40줄'), '원시 값(변경 횟수·수정량)도 상세 응답에서 온다');
  assert.ok(!requested.some((u) => u.includes('risk-scores')), '점수 상위 목록 전체를 따로 받아 이 파일을 찾지 않는다');

  /* 실제로 변경이 없는 파일은 상세 응답이 no_history라고 알려 준다 */
  detailBody = detail({ maintenance_priority: null, risk_score: null, evidence_status: 'no_history' });
  html = await render(5);
  assert.ok(html.includes('관측 변경 없음'), '응답이 no_history일 때만 관측 변경 없음');

  /* 상태를 알 수 없는 응답(기준 SHA에 없는 파일·이전 형식)은 관측 변경 없음으로 단정하지 않는다 */
  detailBody = detail({});
  html = await render(5);
  assert.ok(!html.includes('관측 변경 없음') && !html.includes('변경이 없어 점수가 없습니다') && html.includes('단정하지 않습니다'), '상태 정보가 없으면 추정하지 않는다');
  html = await render(5, { maintenance_priority: 0.4, evidence_status: 'observed', observation_count: 9 });
  assert.ok(html.includes('0.40') && !html.includes('관측 변경 없음'), '목록(extra)의 값이 있으면 그것을 쓴다');

  /* 이전 계산 버전(v2) 응답은 maintenance_priority 없이 risk_score만 온다 */
  detailBody = detail({ risk_score: 0.33, current_risk: 0.3, structural_risk: 0.36 });
  html = await render(5);
  assert.ok(html.includes('0.33'), 'risk_score만 있는 상세 응답도 점수로 읽는다');

  /* ③ 후보 정렬: 서버가 전체 후보를 정렬해 자른 응답이면 그렇게 표시하고, 정렬 기준을 요청에 실어 보낸다 */
  requested.length = 0;
  detailBody = detail({ sort_by: 'coupling', coupled_total: 30, coupled_files: [coupled(9, 0.9, 0.5, 8), coupled(8, 0.8, 2.5, 6)] });
  html = await render(5);
  const detailUrl = requested.find((u) => u.includes('/files/5/detail'));
  assert.ok(detailUrl.includes('sort_by=coupling') && detailUrl.includes('limit=20'), `서버에 정렬 기준을 전달하고 필요한 만큼만 받는다: ${detailUrl}`);
  assert.ok(html.includes('전체 후보 30개를 이 기준으로 서버에서 정렬한 상위 2개'), '서버 정렬 표시');
  assert.ok(!html.includes('조회된 후보 안에서 정렬'));
  assert.ok(html.indexOf('f9.py') < html.indexOf('f8.py'), '서버 순서를 그대로 쓴다');

  /* 서버 정렬을 보장하지 않는 응답은 '조회된 후보 안에서 정렬'이라고 밝힌다 */
  detailBody = detail({ coupled_files: [coupled(8, 0.8, 2.5, 6), coupled(9, 0.9, 0.5, 8)] });
  html = await render(5);
  assert.ok(html.includes('조회된 후보 안에서 정렬') && html.includes('정확한 순위가 아닐 수 있습니다'), '조회된 후보 안에서 정렬 표시');
  assert.ok(html.indexOf('f9.py') < html.indexOf('f8.py'), '조회된 후보 안에서는 선택한 기준으로 정렬');

  const c = dd.coupledCandidates({ sort_by: 'idf', coupled_total: 120, coupled_files: Array.from({ length: 20 }, (_, i) => coupled(i, 0.5, 1, 3)) }, 'idf');
  assert.deepEqual([c.serverSorted, c.total, c.shown.length], [true, 120, 20]);
  const stale = dd.coupledCandidates({ sort_by: 'coupling', coupled_total: 120, coupled_files: [coupled(1, 0.5, 1, 3)] }, 'idf');
  assert.equal(stale.serverSorted, false, '다른 정렬 기준으로 받은 응답은 서버 정렬로 보지 않는다');

  console.log('집계·지표 설명 회귀 검증 통과');
}

main().catch((e) => { console.error(e); process.exit(1); });
