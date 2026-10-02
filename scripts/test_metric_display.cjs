// node scripts/test_metric_display.cjs — DOM 없이 집계와 설명 회귀 검증
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');

function load(file) {
  const source = fs.readFileSync(path.join(root, file), 'utf8')
    .replace(/^import\s[\s\S]*?;\s*$/gm, '')
    .replace(/^export /gm, '');
  const context = vm.createContext({document: {getElementById: () => null}, window: {}, console});
  vm.runInContext(source, context, {filename: file});
  return context;
}

const ctx = load('frontend/js/code-analysis.js');
const leaf = (risk, bf = null, coupling = null) => ({risk_score: risk, bus_factor: bf, coupling_score: coupling});
const tree = {children: [leaf(1, 1, 1), {children: [leaf(0, 3, 0), leaf(0, null, 0), leaf(null)]}]};
ctx.computeAverages(tree);
assert.equal(tree.avgRisk, 1 / 3, '폴더별 평균 대신 유효 파일별 평균');
assert.equal(tree.avgBF, 2, '지표별 null 파일 제외');
assert.equal(tree.avgCoupling, 1 / 3);
assert.equal(tree.fileCount, 4);
const flat = {children: [leaf(1, 1, 1), leaf(0, 3, 0), leaf(0, null, 0), leaf(null)]};
ctx.computeAverages(flat);
assert.equal(tree.avgRisk, flat.avgRisk, '폴더 구조가 달라도 같은 평균');
const empty = {children: []};
ctx.computeAverages(empty);
assert.equal(empty.avgRisk, null);
assert.equal(empty.fileCount, 0);

const dd = load('frontend/js/drilldown.js');
const explanation = dd.buildRiskExplanation({risk_score: 0.8, coupling_score: 1, coupled_file_count: 2}, {bus_factor: 1});
assert.ok(explanation.includes('80%'));
assert.ok(explanation.includes('결함 발생 확률이 아닙니다'));
assert.ok(!explanation.includes('아는 사람이 한 명뿐'));
assert.ok(!explanation.includes('영향을 줄 수'));
const low = dd.buildRiskExplanation({risk_score: 0}, {bus_factor: null});
assert.ok(low.includes('안전을 보장하지는 않습니다'));
assert.equal(dd.buildRiskExplanation(null, {}), null);
console.log('집계·지표 설명 회귀 검증 통과');
