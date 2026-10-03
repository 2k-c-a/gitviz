// node scripts/test_metric_display.cjs — DOM 없이 집계와 설명 회귀 검증
// code-analysis.js / drilldown.js 를 실제 ES 모듈로 불러온다(브라우저 전역은 최소한으로 대체).
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');

const stub = (name, value) => Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
const store = () => ({ getItem: () => null, setItem() {}, removeItem() {} });
stub('window', globalThis);
stub('document', { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [], addEventListener() {} });
stub('localStorage', store());
stub('sessionStorage', {});
stub('location', { search: '', pathname: '/pages/code-analysis.html', href: 'http://localhost/pages/code-analysis.html' });

const load = (f) => import(pathToFileURL(path.resolve(__dirname, '..', 'frontend', 'js', f)).href);

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
  assert.ok(explanation.includes('80%'), 'BF80=1 설명');
  assert.ok(explanation.includes('결함 발생 확률이 아닌'));
  assert.ok(!explanation.includes('아는 사람이 한 명뿐'));
  assert.ok(!explanation.includes('영향을 줄 수'), '미래 영향 단정 없음');
  assert.ok(explanation.includes('실제 의존성'), '결합도는 실제 의존성이 아님을 밝힌다');
  assert.ok(!explanation.includes('refactor'), '새 버전 설명에 refactor 초기화 없음');

  const zero = dd.buildRiskExplanation({ maintenance_priority: 0 }, { bus_factor: null }, v3p);
  assert.ok(zero.includes('0.00') && zero.includes('안전을 보장하지는 않습니다'), '0은 점수 있음(안전 아님)');
  assert.equal(dd.buildRiskExplanation({ maintenance_priority: null }, {}, v3p), null, '점수 없음이면 높다/낮다를 말하지 않는다');
  assert.equal(dd.buildRiskExplanation(null, {}, v3p), null);

  const legacy = policy.policyOf('gitviz-2026.10-snapshot-v2');
  const old = dd.buildRiskExplanation({ risk_score: 0.3 }, { bus_factor: 2 }, legacy);
  assert.ok(old.includes('Risk Score 0.30'), '이전 버전은 Risk Score 표기');

  console.log('집계·지표 설명 회귀 검증 통과');
}

main().catch((e) => { console.error(e); process.exit(1); });
