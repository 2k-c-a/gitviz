/* ── 코드분석 페이지: /tree API → 트리 다이어그램 + 종합 리스트 + 기여 분포 + 변경 결합 ──
   cx-svg가 있을 때(=코드분석 페이지)만 동작한다.
   화면의 지표 이름·설명은 지금 열려 있는 스냅샷의 계산 버전(policy.js)을 따른다:
   새 버전은 '유지보수 점검 우선순위', 이전 버전은 'Risk Score(이전 계산 버전)'로 설명한다.
   null은 0으로 바꾸지 않는다 — 값 없음('—')·0·표본 부족은 서로 다른 상태로 표시한다. */
import { getRepoId, fetchRepoTree, fetchSnapshotMeta, fetchBusFactor, fetchCoupling, getSortPolicy, setSortPolicy } from './api.js';
import { getRepoGithubInfo, githubFileUrl as ghFileUrl, githubFolderUrl as ghFolderUrl } from './github.js';
import { esc, num, fmtScore, fmtScoreStable, fmtPct, fmtWindow, DASH } from './fmt.js';
import {
  policyOf, scoreOf, evidenceOf, comparePriority, assessDataQuality,
  couplingUnitText, FALLBACK_DETAILS, DISPLAY_THRESHOLD, STRONG_COUPLING,
} from './policy.js';
import { renderLoadError } from './page-context.js';

const svgEl = document.getElementById('cx-svg');

const fmt2 = (n) => fmtScoreStable(n);

/* ── 색상 인코딩 — 정렬 기준이 바뀌면 트리 색도 함께 바뀐다. 회색 = 값 없음(0이 아님) ── */
const NO_VALUE = '#B4B2A9';
const BF_COLORS = { 1: '#E24B4A', 2: '#EF9F27', 3: '#378ADD', 4: '#1D9E75', 5: '#639922' };
const bfColor = (bf) => bf == null ? NO_VALUE : BF_COLORS[Math.min(5, Math.max(1, Math.round(bf)))];

/* 점수 색 구간은 표시 임계값이다(상위 퍼센트가 아님). 낮은 점수(녹색)도 안전을 뜻하지 않는다. */
const riskColor = (score) => {
  if (score == null) return NO_VALUE;
  if (score >= 0.7) return '#E24B4A';
  if (score >= 0.5) return '#EF9F27';
  if (score >= 0.3) return '#378ADD';
  return '#639922';
};

const couplingColor = (score) => {
  if (score == null) return NO_VALUE;
  if (score >= STRONG_COUPLING) return '#E24B4A';
  if (score >= 0.4) return '#EF9F27';
  if (score > 0) return '#378ADD';
  return '#639922';
};

/* 정렬 토글과 트리 색 인코딩이 공유하는 지표 정의 —
   key: 디렉토리 평균값을 저장할 필드명, compare: 목록 정렬 비교 함수(값 없는 파일은 항상 뒤) */
const nullsLast = (get, dir) => (a, b) => {
  const av = get(a), bv = get(b);
  if (av == null && bv == null) return String(a.path).localeCompare(String(b.path));
  if (av == null) return 1;
  if (bv == null) return -1;
  if (av !== bv) return dir === 'asc' ? av - bv : bv - av;
  return ((b.observation_count ?? 0) - (a.observation_count ?? 0)) || String(a.path).localeCompare(String(b.path));
};
/* /tree의 coupling_score는 후보 쌍이 없으면 0으로 내려온다 — 그 0은 '결합 없음'이 아니라 '후보 없음'이다 */
const couplingOf = (f) => (f.coupling_support === 0 ? null : f.coupling_score);

const METRICS = {
  risk:     { key: 'avgRisk',     color: riskColor,     label: '점검 우선순위순', compare: comparePriority,
              legendTitle: '점검 우선순위', legendItems: [] },
  coupling: { key: 'avgCoupling', color: couplingColor, label: '변경 결합순', compare: nullsLast(couplingOf, 'desc'),
              legendTitle: '결합도', legendItems: [['후보 없음', NO_VALUE], ['낮음 (0~0.4)', '#378ADD'], ['0.4~0.7', '#EF9F27'], ['강결합 (≥0.7)', '#E24B4A']] },
  bf:       { key: 'avgBF',       color: bfColor,       label: 'BF80순', compare: nullsLast((f) => f.bus_factor, 'asc'),
              legendTitle: 'BF80', legendItems: [['식별된 기여 없음', NO_VALUE], ['1 (한 명에게 집중)', BF_COLORS[1]], ['2', BF_COLORS[2]], ['3', BF_COLORS[3]], ['4', BF_COLORS[4]], ['5+', BF_COLORS[5]]] },
};

/* 계산 버전에 맞춰 점수 지표의 이름·범례를 바꾼다 */
function applyPolicyToMetrics() {
  const m = METRICS.risk;
  m.label = POLICY.sortLabel;
  m.legendTitle = POLICY.scoreShort;
  m.legendItems = [['점수 없음', NO_VALUE], ['<0.3', '#639922'], ['0.3~0.5', '#378ADD'], ['0.5~0.7', '#EF9F27'], [`≥${DISPLAY_THRESHOLD} (표시 임계값)`, '#E24B4A']];
}

let currentMetric = 'risk';
let TREE = { name: 'root', children: [] };
let FILES = [];
let POLICY = policyOf(null);
let SNAP = null;          // 지금 열려 있는 저장 결과의 메타 (id, window_*, formula_version …)
let DQ = {};              // data_quality — 수집 범위·표본 정보
let REPO_ID = null;
let searchText = '';

/* 폴더명/파일명 클릭 → GitHub 이동에 필요한 레포 정보 (summary에서 가져옴) */
let GH_INFO = null;

async function initCodeAnalysisPage() {
  renderSortToggle();

  const id = getRepoId();
  REPO_ID = id;
  const [meta, data, ghInfo] = await Promise.all([fetchSnapshotMeta(id), fetchRepoTree(id), getRepoGithubInfo(id)]);
  SNAP = meta.snapshot;
  POLICY = policyOf(SNAP.formula_version);
  DQ = data.data_quality || meta.data_quality || {};
  applyPolicyToMetrics();
  applyPolicyText();
  renderSortToggle();
  setText('cx-status', DQ.commit_count === 0 ? '관측 구간에 커밋 없음' : `저장 결과 #${SNAP.id} 조회 완료`);
  TREE = data.tree || { name: 'root', children: [] };
  FILES = data.files || [];
  computeAverages(TREE);

  GH_INFO = ghInfo;

  renderSummary();
  renderLegend();
  renderList();
  setupSearch();
  loadContributionPanel(id);
  loadCouplingPanel(id);

  if (!TREE.children || !TREE.children.length) {
    renderEmptyTree();
    return;
  }

  /* "모두 펼치기"는 파일이 많은 레포에서 SVG 노드가 수천 개로 불어나 브라우저가 멎을 수 있어 막아둔다.
     트리는 처음에 root만 펼친 상태로 시작하고, 목록은 상위 20개(검색 시 최대 50개)만 그려 대형 저장소에서도 가볍게 유지한다. */
  const expandBtn = document.getElementById('cx-expand-btn');
  if (expandBtn && FILES.length > EXPAND_ALL_LIMIT) {
    expandBtn.disabled = true;
    expandBtn.title = `파일이 ${EXPAND_ALL_LIMIT}개를 넘어 한 번에 펼칠 수 없습니다. 폴더를 하나씩 펼쳐보세요.`;
  }

  setupCanvas();
  buildHierarchy();
  update(root, { animate: false });
  fit(false, FIT_FLOOR);
}

const githubFolderUrl = (folderPath) => ghFolderUrl(GH_INFO, folderPath);
const githubFileUrl = (filePath) => ghFileUrl(GH_INFO, filePath);

/* 디렉토리 노드의 평균 점수/BF/Coupling을 자식들로부터 post-order로 역산해 채운다
   (/tree는 leaf 값만 내려주므로, 세 지표를 한 번에 미리 계산해두면 정렬 토글 시
   재계산 없이 바로 색만 바꿀 수 있다). 하위 파일 수(fileCount)도 같이 센다 —
   접힌 폴더는 d3 계층에서 자식이 숨겨져 leaves()로 셀 수 없기 때문. */
export function computeAverages(node) {
  if (!node.children) {
    node.avgRisk = scoreOf(node);
    node.avgBF = node.bus_factor;
    node.avgCoupling = node.coupling_score;
    node.fileCount = 1;
    node.metricCounts = {
      avgRisk: node.avgRisk == null ? 0 : 1,
      avgBF: node.avgBF == null ? 0 : 1,
      avgCoupling: node.avgCoupling == null ? 0 : 1,
    };
    return;
  }
  node.children.forEach(computeAverages);
  node.fileCount = node.children.reduce((s, c) => s + c.fileCount, 0);
  // 폴더 평균끼리 단순 평균하면 디렉토리 구조에 따라 결과가 달라진다.
  // 지표별로 값이 있는 하위 파일 수를 사용하여 파일당 동일 가중치를 준다.
  node.metricCounts = {};
  for (const key of ['avgRisk', 'avgBF', 'avgCoupling']) {
    const count = node.children.reduce((s, c) => s + c.metricCounts[key], 0);
    const sum = node.children.reduce((s, c) => s + (c[key] ?? 0) * c.metricCounts[key], 0);
    node.metricCounts[key] = count;
    node[key] = count ? sum / count : null;
  }
}

/* 열려 있는 스냅샷의 계산 버전에 맞춰 화면의 고정 문구를 바꾼다 */
function applyPolicyText() {
  const p = POLICY;
  setText('cx-dash-sub', `코드 분석 — ${p.scoreShort} · BF80 · 공동 변경 결합도`);
  setText('cx-l-high', `${p.scoreShort} ≥ ${DISPLAY_THRESHOLD} 파일`);
  setText('cx-s-high', '표시 임계값 기준이며 상위 30%를 뜻하지 않음');
  setText('cx-l-avg', `평균 ${p.scoreShort}`);
  setText('cx-l-bf', 'BF80 = 1 파일');
  setText('cx-s-bf', '한 명에게 변경 기여 80% 이상 집중');
  setText('cx-l-cp', '강결합 후보 파일');
  setText('cx-s-cp', `공동 변경 결합도 ≥ ${STRONG_COUPLING} (실제 의존성 아님)`);
  setText('cx-th-score', p.scoreShort);
  setText('cx-th-bf', 'BF80');
  setText('cx-th-cp', '결합도');

  const note = document.getElementById('cx-policy-note');
  if (note) {
    const win = SNAP.window_start
      ? `관측 기간은 이 결과에 고정된 ${fmtWindow(SNAP)}입니다(종료 시각 제외). 모든 파일에 같은 기간을 적용합니다.`
      : `이 결과는 구간 제한 없이 전체 이력을 대상으로 계산했습니다(${fmtWindow(SNAP)}).`;
    const lead = `${p.leadText} 색·배지의 0.7은 표시 임계값이며 상위 30%를 뜻하지 않습니다.`;
    const more = [win, p.summaryText, p.thresholdText,
      '폴더는 점수가 있는 하위 파일의 평균입니다. 점수가 없는 파일(관측 변경 없음·계산 입력 누락)은 평균에서 제외하며 0으로 취급하지 않습니다.',
      'BF80은 파일 변경 커밋의 80%를 차지하는 최소 기여자 수(식별된 기여 기준)이고, 결합도는 실제 의존성이 아닌 과거 공동 변경 신호입니다.'];
    note.innerHTML = `${esc(lead)} <details class="cx-more"><summary>지표 상세 설명</summary>${more.map(t => `<p>${esc(t)}</p>`).join('')}</details>`;
  }
}

function renderSummary() {
  const scores = FILES.map(scoreOf).filter(v => v != null);
  const over = scores.filter(v => v >= DISPLAY_THRESHOLD).length;
  const avg = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
  const bf1 = FILES.filter(f => f.is_bus_factor_risk).length;
  const strong = FILES.filter(f => couplingOf(f) != null && couplingOf(f) >= STRONG_COUPLING).length;

  setText('cx-m-highrisk', FILES.length ? num(over) : DASH);
  setText('cx-m-avgrisk', avg != null ? fmt2(avg) : DASH);
  setText('cx-s-avg', `점수가 있는 파일 ${num(scores.length)}개 기준 · 점수 없는 ${num(FILES.length - scores.length)}개 제외`);
  setText('cx-m-bf1', FILES.length ? num(bf1) : DASH);
  setText('cx-m-coupling', FILES.length ? num(strong) : DASH);

  /* 근거 상태는 서로 겹치지 않는 네 가지다 — 점수 0·점수 없음·표본 부족을 한눈에 구분한다 */
  const evidenceBox = document.getElementById('cx-evidence');
  if (evidenceBox) {
    const count = (s) => FILES.filter(f => (f.evidence_status || 'observed') === s).length;
    const threshold = assessDataQuality(DQ).threshold;
    const chip = (tone, label, n, tip) => `<span class="cx-tag ${tone}" title="${esc(tip)}">${esc(label)} <b>${num(n)}</b></span>`;
    evidenceBox.innerHTML = `<span class="cx-evidence-title">파일 ${num(FILES.length)}개의 관측 상태</span>`
      + chip('ok', '정상 관측', count('observed'), `관측 구간 변경 ${threshold}건 이상`)
      + chip('amber', '표본 부족', count('low_support'), `변경 ${threshold}건 미만 — 점수는 있으나 근거가 적습니다`)
      + chip('amber', '자료 부족', count('metrics_missing'), '계산 입력(Churn 등) 누락 — 점수를 계산하지 못해 순위 뒤에 둡니다')
      + chip('gray', '관측 변경 없음', count('no_history'), '관측 구간에 변경이 없어 점수가 없습니다(0이 아님, 안전을 뜻하지도 않음)');
  }
}

function renderSortToggle() {
  const wrap = document.getElementById('cx-sort-toggle');
  if (!wrap) return;
  wrap.innerHTML = Object.entries(METRICS).map(([key, m]) =>
    `<div class="rbtn${key === currentMetric ? ' active' : ''}" data-metric="${key}">${m.label}</div>`
  ).join('');
  wrap.querySelectorAll('[data-metric]').forEach(el => {
    el.addEventListener('click', () => {
      if (el.dataset.metric === currentMetric) return;
      currentMetric = el.dataset.metric;
      renderSortToggle();
      recolorTree();
      renderLegend();
      renderList();
    });
  });
}

const EXPAND_ALL_LIMIT = 400;

/* ── 트리 다이어그램(노드-링크) ──
   /tree 계층을 위→아래로 뻗는 트리로 그린다. 폴더(큰 원)를 클릭하면 하위 폴더·파일이 펼쳐지고
   다시 클릭하면 접힌다. 펼침 상태는 root(d3.hierarchy)가 계속 들고 있고(d.children=펼침 /
   d._children=접힘), 정렬 토글은 배치는 그대로 두고 원 색만 바꾼다. 화면은 드래그로 이동,
   Ctrl+휠·+/− 버튼으로 확대/축소한다. */
const UNIT_X = 44;                  // 가로 간격 단위 — 실제 간격은 원 반지름 기준(separation)
const MIN_ROW_GAP = 64, ROW_PAD = 10; // 세로 간격(단계 사이)의 최소값·여백 — 나머지는 라벨 높이에 맞춰 assignRows가 정함
const R_FILE = 13;                  // 파일 원 반지름 (폴더는 하위 파일 수에 따라 20~30)
const MIN_ZOOM = 0.08, MAX_ZOOM = 2.5;
const FIT_FLOOR = 0.9;             // 처음/리셋 화면은 글자가 읽히는 배율 아래로 줄이지 않는다 (넘치면 root 기준으로 보여주고 드래그로 탐색)
const REVEAL_FLOOR = 0.7;           // 폴더를 펼칠 때 자동 축소의 하한
const TIER_GAP = 16, MAX_TIERS = 4; // 라벨끼리 겹치면 위/아래로 엇갈려 배치
const MAX_LABEL = 14;
const DUR = 380;
const EASE = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

let root = null;                    // d3.hierarchy — 펼침 상태 유지
let selected = null;                // 클릭해서 고른 노드 (상세 카드에 계속 표시)
let hovered = null;
let layout, viewport, gLinks, gNodes, zoomer;
let userMoved = false;              // 드래그·확대/축소를 한 번이라도 했는지

const isDir = (d) => !!d.data.children;
const nodeR = (d) => (isDir(d) ? 20 + 10 * Math.min(1, Math.log10(d.data.fileCount + 1) / 3) : R_FILE);
const nodeColor = (d) => { const m = METRICS[currentMetric]; return m.color(d.data[m.key]); };
const nodePath = (d) => (d.parent ? d.uid : '');
const fmtCount = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n));

/* 라벨이 길면 줄인다. 파일은 확장자를 남겨 알아볼 수 있게 한다. */
function shortName(d) {
  const name = d.parent ? d.data.name : 'root';
  if (name.length <= MAX_LABEL) return name;
  const dot = name.lastIndexOf('.');
  const ext = !isDir(d) && dot > 0 ? name.slice(dot) : '';
  if (ext && ext.length <= 5) return name.slice(0, MAX_LABEL - ext.length - 1) + '…' + ext;
  return name.slice(0, MAX_LABEL - 1) + '…';
}

/* 라벨 폭 추정 (13px 기준, 한글은 넓게) — 엇갈림 배치·클릭 영역·화면 범위 계산에 쓴다.
   실제 글자 폭은 렌더 전에는 잴 수 없어서(숨겨진 탭에서는 0) 글자 수로 어림한다. */
const labelW = (d) => [...shortName(d)].reduce((w, c) => w + (c.charCodeAt(0) > 0x2e80 ? 13 : isDir(d) ? 7.6 : 7.1), 0) + 10;

/* 같은 단계(depth)에서 라벨이 가로로 겹치면 한 칸씩 더 멀리 엇갈려 둔다 (위 라벨·아래 라벨은 각각 따로).
   노드 간격을 좁혀도 이름이 서로 가리지 않고, 이름 클릭 영역도 겹치지 않는다. */
function assignTiers(nodes) {
  const groups = new Map();
  for (const d of nodes) {
    const key = `${d.depth}${d.children ? 'a' : 'b'}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(d);
  }
  for (const list of groups.values()) {
    list.sort((a, b) => a.x - b.x);
    const ends = []; // 칸별로 마지막 라벨의 오른쪽 끝
    for (const d of list) {
      const half = labelW(d) / 2;
      let t = ends.findIndex((e) => d.x - half >= e);
      if (t < 0) t = ends.length < MAX_TIERS ? ends.length : ends.indexOf(Math.min(...ends));
      ends[t] = d.x + half + 4;
      d.tier = t;
    }
  }
}

/* 라벨 기준선 y(노드 중심 기준): 펼친 폴더는 원 위, 나머지는 원 아래 — 링크와 겹치지 않게 */
const labelY = (d) => (d.children ? -(nodeR(d) + 8 + d.tier * TIER_GAP) : nodeR(d) + 18 + d.tier * TIER_GAP);

/* 단계(행)마다 높이를 따로 정한다: 윗 행의 아래 라벨 + 아랫 행의 위 라벨이 차지하는 만큼만 띄운다.
   엇갈린 라벨이 다음 행과 부딪히지 않으면서, 라벨이 적은 행은 선이 짧게 붙는다. */
function assignRows(nodes) {
  const maxDepth = d3.max(nodes, (d) => d.depth);
  const down = new Array(maxDepth + 1).fill(0);
  const up = new Array(maxDepth + 1).fill(0);
  for (const d of nodes) {
    if (d.children) { // 펼친 폴더: 라벨이 위, 아래로는 원만
      down[d.depth] = Math.max(down[d.depth], nodeR(d));
      up[d.depth] = Math.max(up[d.depth], -labelY(d) + 14);
    } else {          // 파일·접힌 폴더: 라벨이 아래, 위로는 원만
      down[d.depth] = Math.max(down[d.depth], labelY(d) + 6);
      up[d.depth] = Math.max(up[d.depth], nodeR(d) + 2);
    }
  }
  const rowY = [0];
  for (let k = 1; k <= maxDepth; k++) rowY[k] = rowY[k - 1] + Math.max(MIN_ROW_GAP, down[k - 1] + up[k] + ROW_PAD);
  for (const d of nodes) d.y = rowY[d.depth];
}

function nodeTitle(d) {
  const parts = [d.parent ? d.data.name : 'root'];
  if (isDir(d)) parts.push(`파일 ${num(d.data.fileCount)}개`);
  parts.push(`${isDir(d) ? '평균 ' : ''}${POLICY.scoreShort} ${d.data.avgRisk != null ? d.data.avgRisk.toFixed(2) : '없음'}`);
  if (d.data.avgBF != null) parts.push(`BF80 ${d.data.avgBF.toFixed(1)}`);
  if (d.data.avgCoupling != null) parts.push(`결합도 ${d.data.avgCoupling.toFixed(2)}`);
  return parts.join(' · ');
}

/* 폴더 → 파일 순, 이름순으로 정렬한 계층을 만들고 root만 펼친 초기 상태로 둔다.
   uid는 경로라서 다시 만들어도(처음으로) 같은 노드끼리 화면에서 이어진다. */
function buildHierarchy() {
  root = d3.hierarchy(TREE);
  root.sort((a, b) => (Number(isDir(b)) - Number(isDir(a))) || d3.ascending(a.data.name, b.data.name));
  root.each((d) => { d.uid = d.parent ? (d.parent.parent ? `${d.parent.uid}/${d.data.name}` : d.data.name) : '/'; });
  root.eachAfter((d) => { if (d !== root && d.children) { d._children = d.children; d.children = null; } });
  root.x0 = 0; root.y0 = 0;
  selected = null; hovered = null;
}

function expandAll(d) {
  if (d._children) { d.children = d._children; d._children = null; }
  (d.children || []).forEach(expandAll);
}

function setupCanvas() {
  /* 가로 간격은 두 원의 반지름 합 + 여백으로 잡아 노드를 촘촘히 모은다 (라벨은 엇갈림 배치로 겹침 방지) */
  layout = d3.tree().nodeSize([UNIT_X, 1])
    .separation((a, b) => (nodeR(a) + nodeR(b) + (a.parent === b.parent ? 16 : 28)) / UNIT_X);
  const svg = d3.select(svgEl);
  svg.selectAll('*').remove();
  viewport = svg.append('g');
  gLinks = viewport.append('g');
  gNodes = viewport.append('g');

  zoomer = d3.zoom()
    .scaleExtent([MIN_ZOOM, MAX_ZOOM])
    .clickDistance(4) // 클릭 중 손이 조금 흔들려도 드래그가 아니라 클릭으로 본다 (기본값 0이면 1px만 움직여도 클릭이 무시됨)
    .interpolate(d3.interpolate) // 이동만 할 때 확대·축소가 튀지 않도록 선형 보간
    // 휠은 Ctrl/⌘와 함께일 때만 확대 — 그냥 휠은 페이지 스크롤에 양보한다. 더블클릭 확대는 끈다.
    .filter((e) => (e.type === 'wheel' ? (e.ctrlKey || e.metaKey) : (e.type !== 'dblclick' && !e.ctrlKey && !e.button)))
    .on('zoom', (e) => {
      if (e.sourceEvent) userMoved = true; // 사용자가 직접 옮긴 뒤에는 크기가 바뀌어도 화면을 되돌리지 않는다
      viewport.attr('transform', e.transform);
      svgEl.classList.toggle('cx-zoomed-out', e.transform.k < 0.5); // 너무 작아지면 라벨은 숨김
    });
  svg.call(zoomer);

  /* 첫 렌더 시점엔 레이아웃(CSS 로딩·그리드)이 아직 확정되지 않아 SVG 폭이 틀릴 수 있다.
     크기가 바뀌면(창 리사이즈 포함) 사용자가 화면을 건드리기 전까지는 다시 맞춘다. */
  new ResizeObserver(() => {
    if (!userMoved && root && svgEl.clientWidth > 0) fit(false, FIT_FLOOR);
  }).observe(svgEl);

  const zoomBy = (f) => { userMoved = true; svg.transition().duration(220).call(zoomer.scaleBy, f); };
  const bind = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
  bind('cx-zoom-in', () => zoomBy(1.4));
  bind('cx-zoom-out', () => zoomBy(1 / 1.4));
  bind('cx-zoom-fit', () => fit());
  bind('cx-root-btn', () => { buildHierarchy(); update(root); fit(true, FIT_FLOOR); });
  bind('cx-expand-btn', () => { expandAll(root); update(root); fit(); });
}

function camera(T, animate = true) {
  const svg = d3.select(svgEl);
  if (animate) svg.transition().duration(DUR).ease(EASE).call(zoomer.transform, T);
  else svg.call(zoomer.transform, T);
}

function boundsOf(nodes) {
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const d of nodes) {
    const r = nodeR(d);
    const half = Math.max(r, labelW(d) / 2);
    x0 = Math.min(x0, d.x - half); x1 = Math.max(x1, d.x + half);
    y0 = Math.min(y0, d.children ? d.y + labelY(d) - 14 : d.y - r - 4);
    y1 = Math.max(y1, d.children ? d.y + r + 4 : d.y + labelY(d) + 6);
  }
  return { x0, x1, y0, y1 };
}

/* 트리 전체가 한 화면에 들어오도록 맞춘다 (작은 트리는 확대하지 않고 가운데 정렬).
   minK를 주면 그 배율 아래로는 줄이지 않는다 — 글자가 읽히는 크기를 지키고, 넘치는 쪽은
   root 기준(가로)·맨 위(세로)로 보여준 뒤 드래그로 탐색하게 한다. */
function fit(animate = true, minK = MIN_ZOOM) {
  userMoved = false;
  const b = boundsOf(root.descendants());
  const w = svgEl.clientWidth || 900, h = svgEl.clientHeight || 560, pad = 28;
  const bw = b.x1 - b.x0, bh = b.y1 - b.y0;
  const k = Math.max(minK, Math.min(1, (w - 2 * pad) / bw, (h - 2 * pad) / bh));
  const tx = k * bw > w - 2 * pad ? w / 2 - k * root.x : w / 2 - k * (b.x0 + b.x1) / 2;
  const ty = k * bh > h - 2 * pad ? pad - k * b.y0 : h / 2 - k * (b.y0 + b.y1) / 2;
  camera(d3.zoomIdentity.translate(tx, ty).scale(k), animate);
}

/* 펼친 하위 노드가 화면 밖으로 나가면 필요한 만큼만 이동해서 보여준다.
   그래도 안 들어가면 축소하되, 글자가 읽히는 하한(REVEAL_FLOOR)까지만 줄인다. */
function revealTransform(T, b) {
  const w = svgEl.clientWidth || 900, h = svgEl.clientHeight || 560, pad = 24;
  const fitK = Math.min((w - 2 * pad) / (b.x1 - b.x0), (h - 2 * pad) / (b.y1 - b.y0));
  const k = fitK < T.k ? Math.max(MIN_ZOOM, fitK, Math.min(T.k, REVEAL_FLOOR)) : T.k;
  const place = (lo, hi, t0, size) => {
    const a = k * lo + t0, z = k * hi + t0;
    if (z - a > size - 2 * pad) return size / 2 - k * (lo + hi) / 2;
    if (a < pad) return t0 + (pad - a);
    if (z > size - pad) return t0 - (z - (size - pad));
    return t0;
  };
  const rescaled = k !== T.k;
  const tx = rescaled ? w / 2 - k * (b.x0 + b.x1) / 2 : place(b.x0, b.x1, T.x, w);
  const ty = rescaled ? h / 2 - k * (b.y0 + b.y1) / 2 : place(b.y0, b.y1, T.y, h);
  return d3.zoomIdentity.translate(tx, ty).scale(k);
}

/* 레이아웃을 다시 계산하고 노드·링크를 갱신한다. source = 방금 펼치거나 접은 노드 —
   새 노드는 그 자리에서 뻗어나오고, 사라지는 노드는 그 자리로 모인다.
   anchor(화면 좌표)를 주면 source가 화면에서 제자리에 머물도록 카메라를 같이 옮긴다. */
function update(source, { animate = true, anchor = null, reveal = false } = {}) {
  const dur = animate ? DUR : 0;
  layout(root);
  const nodes = root.descendants();
  const links = root.links();
  assignTiers(nodes);
  assignRows(nodes);
  if (selected && !nodes.includes(selected)) selected = null;
  if (hovered && !nodes.includes(hovered)) hovered = null;

  /* 링크 */
  const link = gLinks.selectAll('line').data(links, (d) => d.target.uid);
  link.exit().transition().duration(dur).ease(EASE)
    .attr('x1', source.x).attr('y1', source.y).attr('x2', source.x).attr('y2', source.y)
    .remove();
  link.enter().append('line').attr('class', 'cx-link')
    .attr('x1', source.x0).attr('y1', source.y0).attr('x2', source.x0).attr('y2', source.y0)
    .merge(link)
    .transition().duration(dur).ease(EASE)
    .attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y)
    .attr('x2', (d) => d.target.x).attr('y2', (d) => d.target.y);

  /* 노드 */
  const node = gNodes.selectAll('g.cx-node').data(nodes, (d) => d.uid);
  node.exit().transition().duration(dur).ease(EASE)
    .attr('transform', `translate(${source.x},${source.y})`).style('opacity', 0).remove();

  const enter = node.enter().append('g').attr('class', 'cx-node')
    .attr('transform', `translate(${source.x0},${source.y0})`).style('opacity', 0)
    .on('click', (_, d) => onNodeClick(d))
    .on('mouseenter', (_, d) => { hovered = d; refreshDetail(); highlightPath(d); })
    .on('mouseleave', () => { hovered = null; refreshDetail(); highlightPath(null); });
  enter.append('title');
  enter.append('circle').attr('class', 'cx-halo');
  enter.append('circle').attr('class', 'cx-ring');
  enter.append('circle').attr('class', 'cx-dot');
  enter.append('text').attr('class', 'cx-count');
  /* 이름 영역(투명 박스 + 글자): 클릭 → GitHub. 원 클릭은 펼침/상세로 따로 둔다.
     박스를 글자보다 넉넉히 잡아 작은 글자를 정확히 못 눌러도 열린다. */
  const name = enter.append('g').attr('class', 'cx-name')
    .attr('transform', (d) => `translate(0,${labelY(d)})`)
    .on('click', (event, d) => {
      event.stopPropagation();
      const url = isDir(d) ? githubFolderUrl(nodePath(d)) : githubFileUrl(d.data.path);
      if (url) window.open(url, '_blank', 'noopener');
    });
  name.append('line').attr('class', 'cx-leader');
  name.append('rect').attr('class', 'cx-name-hit').append('title');
  name.append('text').attr('class', 'cx-label');

  const all = enter.merge(node);
  all.classed('is-selected', (d) => d === selected);
  all.select('title').text(nodeTitle);
  all.select('.cx-halo').attr('r', (d) => nodeR(d) + 5).style('stroke', nodeColor)
    .style('opacity', (d) => (d._children ? 0.75 : 0)); // 점선 테두리 = 접힌 폴더
  all.select('.cx-ring').attr('r', (d) => nodeR(d) + 4);
  all.select('.cx-dot').attr('r', nodeR).style('fill', nodeColor);
  all.select('.cx-count').text((d) => (isDir(d) ? fmtCount(d.data.fileCount) : ''));
  const names = all.select('.cx-name'); // 원점 = 라벨 기준선 (노드 중심에서 labelY만큼 떨어진 곳)
  names.select('.cx-label').classed('is-dir', isDir).text(shortName);
  names.select('.cx-name-hit')
    .attr('x', (d) => -labelW(d) / 2).attr('y', -15).attr('width', labelW).attr('height', 21)
    .select('title').text((d) => `GitHub에서 열기 · ${d.parent ? (d.data.path || nodePath(d)) : d.data.name}`);
  names.select('.cx-leader') // 엇갈려 멀어진 라벨(tier>0)은 점선으로 원과 이어준다
    .style('display', (d) => (d.tier > 0 ? null : 'none'))
    .attr('x1', 0).attr('x2', 0)
    .attr('y1', (d) => (d.children ? -nodeR(d) - labelY(d) : nodeR(d) - labelY(d)))
    .attr('y2', (d) => (d.children ? 4 : -14));
  names.transition().duration(dur).ease(EASE)
    .attr('transform', (d) => `translate(0,${labelY(d)})`);
  all.transition().duration(dur).ease(EASE)
    .attr('transform', (d) => `translate(${d.x},${d.y})`).style('opacity', 1);

  /* 카메라 */
  if (anchor) {
    const k = d3.zoomTransform(svgEl).k;
    let T = d3.zoomIdentity.translate(anchor[0] - k * source.x, anchor[1] - k * source.y).scale(k);
    if (reveal) T = revealTransform(T, boundsOf(source.descendants()));
    camera(T, animate);
  }

  root.each((d) => { d.x0 = d.x; d.y0 = d.y; });
  refreshDetail();
}

function onNodeClick(d) {
  selected = d;
  refreshSelection();
  refreshDetail();
  if (isDir(d)) {
    if (!d.children && !d._children) return; // 빈 폴더
    const anchor = d3.zoomTransform(svgEl).apply([d.x, d.y]);
    const opening = !d.children;
    if (opening) { d.children = d._children; d._children = null; }
    else { d._children = d.children; d.children = null; }
    update(d, { anchor, reveal: opening });
  } else if (d.data.file_id != null && window.openFileDrilldown) {
    window.openFileDrilldown(d.data.file_id, d.data);
  }
}

function refreshSelection() {
  gNodes.selectAll('g.cx-node').classed('is-selected', (d) => d === selected);
}

function refreshDetail() {
  showDetail(hovered || selected);
}

/* 호버한 노드까지 이어지는 가지(root → 노드)를 강조 */
function highlightPath(d) {
  const ids = new Set(d ? d.ancestors().map((n) => n.uid) : []);
  gLinks.selectAll('line').classed('is-hot', (l) => ids.has(l.target.uid));
}

/* 정렬 토글을 바꾸면 배치·펼침 상태는 그대로 두고 색만 부드럽게 바꾼다 */
function recolorTree() {
  if (!gNodes) return;
  const nodes = gNodes.selectAll('g.cx-node');
  nodes.select('.cx-dot').transition().duration(300).style('fill', nodeColor);
  nodes.select('.cx-halo').transition().duration(300).style('stroke', nodeColor);
}

function renderEmptyTree() {
  const svg = d3.select(svgEl);
  svg.selectAll('*').remove();
  svg.append('text').attr('x', '50%').attr('y', '50%').attr('text-anchor', 'middle')
    .style('fill', 'var(--text3)').style('font-size', '12px').text('표시할 파일이 없습니다.');
  ['cx-root-btn', 'cx-expand-btn'].forEach((id) => { const el = document.getElementById(id); if (el) el.disabled = true; });
  const zoomCtrl = document.querySelector('.cx-zoom-ctrl');
  if (zoomCtrl) zoomCtrl.style.display = 'none';
}

function renderLegend() {
  const legend = document.getElementById('cx-legend');
  if (!legend) return;
  const metric = METRICS[currentMetric];
  const colors = metric.legendItems.map(([label, color]) =>
    `<div class="leg-item"><div class="leg-dot" style="background:${color};"></div>${esc(metric.legendTitle)} ${esc(label)}</div>`
  ).join('');
  const shapes = [
    ['dir', '폴더 (숫자 = 하위 파일 수)'],
    ['file', '파일'],
    ['folded', '접힌 폴더 (클릭하면 펼침)'],
  ].map(([cls, label]) => `<div class="leg-item"><span class="leg-shape ${cls}"></span>${label}</div>`).join('');
  const note = currentMetric === 'risk'
    ? `<div class="leg-note">회색 = 점수 없음(관측 변경 없음·자료 부족, 0이 아님). 색 구간은 표시 임계값이며 낮은 점수가 안전을 뜻하지는 않습니다.</div>` : '';
  legend.innerHTML = `${colors}<div style="flex-basis:100%;height:0;"></div>${shapes}${note}`;
}

/* ── 표시 도우미: 값 없음('—')과 0, 표본 부족을 서로 다른 모양으로 ── */
const chip = (val, color) => `<span class="hm-val" style="padding:2px 8px;background:${color}22;color:${color};font-weight:600;">${esc(val)}</span>`;
const tag = (tone, text, title = '') => `<span class="cx-tag ${tone}"${title ? ` title="${esc(title)}"` : ''}>${esc(text)}</span>`;
const na = (reason) => `<span class="cx-na" title="${esc(reason)}">${DASH}</span>`;
const has = (o, k) => o != null && o[k] !== undefined;

const SCORE_NA = {
  no_history: '관측 구간에 변경이 없어 점수가 없습니다 (0이 아니며 안전을 뜻하지도 않습니다)',
  metrics_missing: '점수 계산에 필요한 입력(Churn 등)이 누락되어 계산하지 못했습니다 (자료 부족)',
};
const scoreNaReason = (f) => SCORE_NA[f.evidence_status] || '점수가 없습니다';
const BF_NA = '식별된 기여가 없어 BF80을 계산하지 못했습니다 (0이 아님)';
const ENTROPY_NA = '식별된 기여가 없어 계산하지 못했습니다 (기여자 1명이면 0, 없으면 값 없음)';

/* 트리 노드에 마우스를 올리거나 클릭하면 우측 카드에 점수/BF80/결합도를 한번에 보여준다 */
function showDetail(d) {
  const pathEl = document.getElementById('cx-detail-path');
  const metricsEl = document.getElementById('cx-detail-metrics');
  if (!pathEl || !metricsEl) return;

  if (!d) {
    pathEl.className = 'cx-detail-empty';
    pathEl.textContent = '노드에 마우스를 올려보세요';
    metricsEl.innerHTML = '';
    return;
  }

  pathEl.className = 'cx-detail-path';
  pathEl.textContent = d.parent ? (d.data.path || nodePath(d)) : 'root (전체)';

  const row = (label, html) => `<div class="cx-detail-row"><span>${esc(label)}</span><span class="cx-detail-val">${html}</span></div>`;

  if (!isDir(d)) {
    const f = d.data;
    const sc = scoreOf(f);
    const ev = evidenceOf(f.evidence_status);
    const cp = couplingOf(f);
    const rows = [
      row(POLICY.scoreShort, (sc != null ? chip(fmt2(sc), riskColor(sc)) : na(scoreNaReason(f))) + (ev.label ? tag(ev.tone, ev.label) : '')),
      row('변경 횟수 (관측 구간)', f.observation_count != null ? `${num(f.observation_count)}회` : DASH),
      row('BF80', (f.bus_factor != null ? chip(f.bus_factor, bfColor(f.bus_factor)) : na(BF_NA)) + (f.is_bus_factor_risk ? tag('red', '한 명에게 집중') : '')),
    ];
    if (has(f, 'top_owner_share')) rows.push(row('최대 기여자 비율', f.top_owner_share != null ? fmtPct(f.top_owner_share) : na(BF_NA)));
    if (has(f, 'contributor_count')) rows.push(row('식별된 기여자 수', f.evidence_status === 'no_history' ? na('관측 구간에 변경 없음') : `${num(f.contributor_count)}명`));
    if (has(f, 'entropy_normalized')) {
      rows.push(row('엔트로피 (정규화)', f.entropy_normalized != null
        ? `${fmt2(f.entropy_normalized)}${f.contributor_count === 1 ? ' <small>(기여자 1명 → 0)</small>' : ''}` : na(ENTROPY_NA)));
    }
    if (has(f, 'unidentified_share')) rows.push(row('작성자 미식별 비율', f.unidentified_share != null ? fmtPct(f.unidentified_share) : na('관측 구간에 변경 없음')));
    rows.push(row('결합도 (최대)', cp != null
      ? chip(fmt2(cp), couplingColor(cp)) + (f.coupling_support < 5 ? tag('gray', `표본 ${f.coupling_support}회`, '공동 변경 5건 미만 — 표본 부족(표시 정책)') : '')
      : na('공동 변경 2건 이상인 결합 후보가 없습니다')));
    rows.push(row(`강결합 후보 (≥${STRONG_COUPLING})`, cp != null ? `${num(f.coupled_file_count)}개` : DASH));
    metricsEl.innerHTML = rows.join('');
  } else {
    const n = d.data.metricCounts || {};
    metricsEl.innerHTML = [
      row(`평균 ${POLICY.scoreShort}`, d.data.avgRisk != null ? chip(fmt2(d.data.avgRisk), riskColor(d.data.avgRisk)) + `<small>${num(n.avgRisk)}/${num(d.data.fileCount)}개</small>` : na('점수가 있는 하위 파일이 없습니다')),
      row('파일별 BF80 평균', d.data.avgBF != null ? chip(fmt2(d.data.avgBF), bfColor(d.data.avgBF)) + `<small>${num(n.avgBF)}개</small>` : na(BF_NA)),
      row('평균 결합도', d.data.avgCoupling != null ? chip(fmt2(d.data.avgCoupling), couplingColor(d.data.avgCoupling)) : na('값이 있는 하위 파일이 없습니다')),
      row('하위 파일 수', `${num(d.data.fileCount)}개`),
    ].join('');
  }
}

/* 파일 행 아래에 붙는 근거 표시 — 점수 0·점수 없음·표본 부족을 문장으로 구분 */
function fileNotes(f) {
  const notes = [];
  const ev = f.evidence_status;
  if (ev === 'no_history') notes.push(tag('gray', '관측 변경 없음', SCORE_NA.no_history));
  else if (ev === 'metrics_missing') notes.push(tag('amber', '자료 부족 · 계산 입력 누락', SCORE_NA.metrics_missing));
  else if (ev === 'low_support') notes.push(tag('amber', `표본 부족 (${num(f.observation_count)}건)`, `관측 구간 변경 ${num(f.observation_count)}건 — 점수 0도 안전을 뜻하지 않습니다`));
  if (ev !== 'no_history' && f.unidentified_share != null && f.unidentified_share > 0) {
    notes.push(tag('gray', `작성자 미식별 ${fmtPct(f.unidentified_share)}`, '작성자를 식별하지 못한 변경 — BF80·엔트로피 계산에서 제외됩니다'));
  }
  if (f.coupling_support > 0 && f.coupling_support < 5) notes.push(tag('gray', `결합도 표본 ${f.coupling_support}회`, '공동 변경 5건 미만 — 표본 부족(표시 정책)'));
  return notes.join('');
}

const LIST_LIMIT = 20, SEARCH_LIMIT = 50;

function renderList() {
  const tbody = document.getElementById('cx-list-tbody');
  const note = document.getElementById('cx-list-note');
  if (!tbody) return;

  const metric = METRICS[currentMetric];
  const q = searchText.trim().toLowerCase();
  const matched = q ? FILES.filter(f => f.path.toLowerCase().includes(q)) : FILES;
  const limit = q ? SEARCH_LIMIT : LIST_LIMIT;
  const sorted = [...matched].sort(metric.compare).slice(0, limit);
  if (note) {
    note.textContent = q
      ? `${metric.label} · 검색 결과 ${num(matched.length)}개 중 ${num(sorted.length)}개 표시`
      : `${metric.label} · 상위 ${num(Math.min(limit, matched.length))}개 · 동률은 변경 횟수 많은 순 → 경로순 · 값 없는 파일은 항상 뒤`;
  }

  if (!FILES.length) {
    tbody.innerHTML = '<tr><td class="hm-td" colspan="5" style="text-align:center;color:var(--text3);padding:24px;">파일 데이터가 없습니다.</td></tr>';
    return;
  }
  if (!sorted.length) {
    tbody.innerHTML = `<tr><td class="hm-td" colspan="5" style="text-align:center;color:var(--text3);padding:24px;">'${esc(searchText.trim())}'와 일치하는 파일이 없습니다.</td></tr>`;
    return;
  }

  const byId = new Map(sorted.map(f => [f.file_id, f]));

  tbody.innerHTML = sorted.map(f => {
    const sc = scoreOf(f);
    const cp = couplingOf(f);
    const bfBadge = f.is_bus_factor_risk
      ? '<span class="badge badge-red" title="관측 구간에서 한 명에게 파일 변경 기여의 80% 이상이 집중됩니다">BF80=1</span>' : '';
    const couplingBadge = cp != null && cp >= STRONG_COUPLING
      ? '<span class="badge badge-amber" style="margin-left:4px;" title="공동 변경 결합도가 높은 파일 — 실제 의존성을 뜻하지 않습니다">강결합</span>' : '';
    const fileUrl = githubFileUrl(f.path);
    const pathHtml = fileUrl
      ? `<a href="${esc(fileUrl)}" target="_blank" rel="noopener" class="cx-file-link" title="GitHub에서 열기">${esc(f.path)}</a>`
      : esc(f.path);
    return `<tr style="cursor:pointer;" data-file-id="${f.file_id}" title="수정한 사람·함께 변경된 파일 보기">
      <td class="hm-td file" title="${esc(f.path)}">${pathHtml}${bfBadge}${couplingBadge}<div class="cx-notes">${fileNotes(f)}</div></td>
      <td class="hm-td">${sc != null ? `<span class="hm-val" style="background:${riskColor(sc)}22;color:${riskColor(sc)};font-weight:600;">${fmt2(sc)}</span>` : na(scoreNaReason(f))}</td>
      <td class="hm-td" style="text-align:center;">${f.bus_factor != null ? f.bus_factor : na(BF_NA)}</td>
      <td class="hm-td">${cp != null ? `<span class="hm-val" style="background:${couplingColor(cp)}22;color:${couplingColor(cp)};">${fmt2(cp)}</span>` : na('공동 변경 2건 이상인 결합 후보가 없습니다')}</td>
      <td class="hm-td" style="text-align:center;">${cp != null ? num(f.coupled_file_count) : DASH}</td>
    </tr>`;
  }).join('');

  tbody.querySelectorAll('.cx-file-link').forEach(a => {
    a.addEventListener('click', (e) => e.stopPropagation());
  });

  tbody.querySelectorAll('[data-file-id]').forEach(row => {
    row.addEventListener('click', () => {
      const fileId = parseInt(row.dataset.fileId, 10);
      if (window.openFileDrilldown) window.openFileDrilldown(fileId, byId.get(fileId));
    });
  });
}

function setupSearch() {
  const input = document.getElementById('cx-search');
  if (!input) return;
  input.addEventListener('input', () => { searchText = input.value; renderList(); });
}

/* ── 기여 분포 (BF80) ──
   BF80 = 관측 구간의 파일 변경 커밋 중 80%를 차지하는 최소 기여자 수(식별된 기여 기준).
   프로젝트 전체의 지식 보유 인원이 아니다. 엔트로피가 높다고 해서 위험색을 쓰지 않는다. */
const bfNum = (v) => (v == null ? DASH : Number.isInteger(v) ? String(v) : v.toFixed(1));

async function loadContributionPanel(id) {
  const box = document.getElementById('cx-bf-body');
  if (!box) return;
  try {
    renderContribution(await fetchBusFactor(id, 20), box);
  } catch (err) {
    box.innerHTML = `<div class="cx-panel-error">기여 분포를 불러오지 못했습니다: ${esc(err.message)}</div>`;
  }
}

function renderContribution(bf, box) {
  const s = bf.summary || {};
  const dist = bf.distribution || [];
  const files = bf.files || [];
  const analyzed = dist.reduce((a, d) => a + d.file_count, 0);
  const unidentifiedFiles = FILES.filter(f => f.evidence_status !== 'no_history' && f.bus_factor == null).length;

  if (!analyzed) {
    box.innerHTML = `<div class="cx-empty">식별된 기여가 있는 파일이 없습니다. 작성자가 식별되지 않은 변경만 있거나 관측 구간에 변경이 없는 경우입니다(0이 아닌 '값 없음').</div>`;
    return;
  }

  setText('cx-s-bf', `파일별 BF80 중앙값 ${bfNum(s.file_bf80_median)} · 한 명에게 변경 기여 80% 이상 집중`);

  const maxCount = Math.max(1, ...dist.map(d => d.file_count));
  const distHtml = dist.map(d => {
    const color = bfColor(d.bus_factor === '5+' ? 5 : Number(d.bus_factor));
    return `<div class="cx-dist-row"><span class="cx-dist-label">BF80 ${esc(d.bus_factor)}</span>
      <div class="cx-dist-bar"><div style="width:${Math.max(2, Math.round(100 * d.file_count / maxCount))}%;background:${color};"></div></div>
      <span class="cx-dist-n">${num(d.file_count)}개</span></div>`;
  }).join('');

  const owners = (bf.top_owners || []).slice(0, 5).map(o =>
    `<div class="c-row" style="cursor:pointer;" onclick="window.openContributorDrilldown(${Number(o.contributor_id)})">
      <div class="c-info"><div class="c-name">${esc(o.name)}</div><div class="c-meta">한 명에게 집중된 파일 ${num(o.exclusive_file_count)}개</div></div></div>`).join('')
    || '<div class="c-meta">BF80=1 파일이 없습니다.</div>';
  const modules = (bf.risky_modules || []).filter(m => m.bf1_count > 0).slice(0, 5).map(m =>
    `<div class="c-row"><div class="c-info"><div class="c-name" style="font-family:monospace;font-size:12px;">${esc(m.module)}</div>
      <div class="c-meta">BF80=1 ${num(m.bf1_count)}/${num(m.file_count)}개 (${fmtPct(m.bf1_ratio)})</div></div></div>`).join('')
    || '<div class="c-meta">BF80=1 파일이 있는 모듈이 없습니다.</div>';

  const rows = files.map(f => `<tr style="cursor:pointer;" data-file-id="${f.file_id}">
      <td class="hm-td file" title="${esc(f.path)}">${esc(f.path)}${f.low_support ? ` ${tag('amber', '표본 부족', `식별된 변경 ${num(f.observation_count)}건 (5건 미만)`)}` : ''}</td>
      <td class="hm-td" style="text-align:center;">${chip(f.bf80 ?? f.bus_factor, bfColor(f.bf80 ?? f.bus_factor))}</td>
      <td class="hm-td" style="text-align:center;">${f.top_owner_share != null ? fmtPct(f.top_owner_share) : DASH}</td>
      <td class="hm-td" style="text-align:center;">${num(f.contributor_count)}명</td>
      <td class="hm-td" style="text-align:center;" title="기여자 1명이면 0, 식별된 기여가 없으면 값 없음">${f.entropy_normalized != null ? fmt2(f.entropy_normalized) : DASH}</td>
      <td class="hm-td" style="text-align:center;" title="식별된 변경 수 / 전체 변경 수">${num(f.observation_count)} / ${num(f.total_change_count)}</td>
      <td class="hm-td" style="text-align:center;">${f.unidentified_share != null ? fmtPct(f.unidentified_share) : DASH}</td>
    </tr>`).join('');

  box.innerHTML = `
    <div class="metrics-4" style="margin-bottom:12px;">
      <div class="metric-card"><div class="m-label">파일별 BF80 중앙값</div><div class="m-val">${bfNum(s.file_bf80_median)}</div><div class="m-sub">식별된 기여가 있는 ${num(analyzed)}개 파일 기준 · 프로젝트 전체 지식 인원이 아님</div></div>
      <div class="metric-card"><div class="m-label">BF80 = 1 파일</div><div class="m-val">${num(s.high_risk_file_count)}</div><div class="m-sub">한 명에게 관측 변경 기여가 집중</div></div>
      <div class="metric-card"><div class="m-label">BF80=1 비중이 큰 모듈</div><div class="m-val">${num(s.risky_module_count)}</div><div class="m-sub">모듈 내 BF80=1 파일 30% 이상</div></div>
      <div class="metric-card"><div class="m-label">기여 식별 불가 파일</div><div class="m-val">${num(unidentifiedFiles)}</div><div class="m-sub">변경은 있으나 작성자 미식별 — BF80 계산 제외(0 아님)</div></div>
    </div>
    <div class="col-1-1" style="margin-bottom:12px;">
      <div><div class="cx-sub-title">BF80 분포 (파일 수)</div>${distHtml}</div>
      <div class="col-1-1">
        <div><div class="cx-sub-title">한 명에게 집중된 파일이 많은 기여자</div><div class="contrib-list">${owners}</div></div>
        <div><div class="cx-sub-title">BF80=1 비중이 큰 모듈</div><div class="contrib-list">${modules}</div></div>
      </div>
    </div>
    <div class="hm-wrap"><table class="hm-table">
      <thead><tr>
        <th class="hm-th left">파일 (BF80 낮은 순 · 상위 ${num(files.length)}개)</th><th class="hm-th">BF80</th><th class="hm-th">최대 기여자 비율</th>
        <th class="hm-th">식별된 기여자</th><th class="hm-th" title="기여가 고르게 퍼진 정도(0~1). 높다고 위험하다는 뜻이 아님">엔트로피(정규화)</th>
        <th class="hm-th" title="식별된 변경 수 / 전체 변경 수">변경 수 (식별/전체)</th><th class="hm-th">작성자 미식별</th>
      </tr></thead><tbody>${rows}</tbody></table></div>`;

  box.querySelectorAll('tr[data-file-id]').forEach(tr => {
    tr.addEventListener('click', () => { if (window.openFileDrilldown) window.openFileDrilldown(parseInt(tr.dataset.fileId, 10)); });
  });
}

/* ── 변경 결합 (공동 변경 후보) ──
   기본 정렬은 원 결합도·공동 변경 횟수. IDF 보정은 사용자가 고르는 실험 정렬이며,
   고르면 실제 요청에 sort_by=idf를 전달한다. 선택은 주소(?sort_by=)에 남아 탭 이동·뒤로 가기에서 유지된다. */
const SORT_LABELS = { coupling: '결합도·공동 변경 횟수순 (기본)', idf: 'IDF 보정순 (실험)' };

function renderCouplingToggle() {
  const wrap = document.getElementById('cx-cp-toggle');
  if (!wrap) return;
  const current = getSortPolicy();
  wrap.innerHTML = Object.entries(SORT_LABELS).map(([key, label]) =>
    `<div class="rbtn${key === current ? ' active' : ''}" data-sort="${key}">${label}</div>`).join('');
  wrap.querySelectorAll('[data-sort]').forEach(el => {
    el.addEventListener('click', () => {
      if (el.dataset.sort === getSortPolicy()) return;
      setSortPolicy(el.dataset.sort);
      loadCouplingPanel(REPO_ID);
    });
  });
}

async function loadCouplingPanel(id) {
  const body = document.getElementById('cx-cp-body');
  if (!body) return;
  renderCouplingToggle();
  body.innerHTML = '<div class="cx-empty">불러오는 중...</div>';
  const policy = getSortPolicy();
  try {
    const data = await fetchCoupling(id, 50, 0, policy);
    if (policy !== getSortPolicy()) return;   // 그 사이 정책을 또 바꿨다면 오래된 응답은 버린다
    renderCoupling(data, body);
  } catch (err) {
    body.innerHTML = `<div class="cx-panel-error">변경 결합을 불러오지 못했습니다: ${esc(err.message)}</div>`;
  }
}

function renderCoupling(data, body) {
  const m = data.meta || {};
  const pairs = data.pairs || [];
  const cov = m.pr_file_coverage;
  const metaChips = [
    tag('blue', `공동 변경 단위: ${couplingUnitText(m)}`, FALLBACK_DETAILS[m.fallback_reason] || ''),
    tag(cov == null ? 'gray' : cov < 1 ? 'amber' : 'ok',
      cov == null ? 'PR 파일 상세: 병합 PR 없음' : `PR 파일 상세 보유 ${fmtPct(cov)} (${num(m.prs_with_files)}/${num(m.merged_pr_count)}개 PR)`,
      '파일 상세가 하나 이상 있는 병합 PR의 비율입니다. 전체 수집 완료율이 아닙니다.'),
    tag('gray', `대규모 변경으로 제외된 단위 ${num(m.excluded_unit_count)}개 (분석 단위 ${num(m.total_units)}개)`, '변경 파일이 너무 많은 PR/커밋은 결합 계산에서 제외합니다'),
    tag('gray', `후보 쌍 ${num(m.total_pairs)}개 · 강결합(≥${STRONG_COUPLING}) ${num(m.high_coupling_count)}쌍 · ${num(pairs.length)}개 표시`),
    tag('gray', `서버 정렬: ${m.sort_by === 'idf' ? 'IDF 보정 점수' : '결합도 → 공동 변경 횟수'}`),
  ].join('');
  const fallback = m.co_change_unit === 'commit' && FALLBACK_DETAILS[m.fallback_reason]
    ? `<div class="cx-fallback">${esc(FALLBACK_DETAILS[m.fallback_reason])}</div>` : '';

  if (!pairs.length) {
    body.innerHTML = `<div class="cx-meta-row">${metaChips}</div>${fallback}
      <div class="cx-empty">결합 후보 0건입니다. 변경이 없다는 뜻이 아닙니다 — 공동 변경이 2건 이상인 쌍만 후보가 되며,
      대규모 변경으로 제외된 단위가 ${num(m.excluded_unit_count)}개(분석 단위 ${num(m.total_units)}개) 있습니다.</div>`;
    return;
  }

  const file = (id, path) => `<div><span class="cx-pair-file" data-file-id="${id}" title="${esc(path)}">${esc(path)}</span></div>`;
  const rows = pairs.map((p, i) => `<tr>
      <td class="hm-td" style="text-align:center;">${i + 1}</td>
      <td class="hm-td file" style="max-width:340px;">${file(p.file_a_id, p.file_a_path)}${file(p.file_b_id, p.file_b_path)}</td>
      <td class="hm-td" style="text-align:center;">${num(p.co_change_count)}회${p.low_support ? `<div>${tag('amber', `표본 ${num(p.co_change_count)}회`, '공동 변경 5건 미만 — 표본 부족(표시 정책이며 통계적 신뢰수준이 아님)')}</div>` : ''}</td>
      <td class="hm-td"><span class="hm-val" style="background:${couplingColor(p.coupling_score)}22;color:${couplingColor(p.coupling_score)};font-weight:600;" title="공동 변경 횟수 ÷ min(두 파일의 변경 횟수)">${fmt2(p.coupling_score)}</span></td>
      <td class="hm-td" style="text-align:center;" title="A가 변경된 ${num(p.change_count_a)}번 중 ${num(p.co_change_count)}번 B도 함께 변경된 과거 비율">${fmtPct(p.a_to_b_ratio)} <small>(${num(p.co_change_count)}/${num(p.change_count_a)})</small></td>
      <td class="hm-td" style="text-align:center;" title="B가 변경된 ${num(p.change_count_b)}번 중 ${num(p.co_change_count)}번 A도 함께 변경된 과거 비율">${fmtPct(p.b_to_a_ratio)} <small>(${num(p.co_change_count)}/${num(p.change_count_b)})</small></td>
      <td class="hm-td" style="text-align:center;color:var(--text2);" title="IDF 보정 점수(실험)">${fmt2(p.adjusted_score)}</td>
    </tr>`).join('');

  body.innerHTML = `<div class="cx-meta-row">${metaChips}</div>${fallback}
    <div class="hm-wrap"><table class="hm-table">
      <thead><tr><th class="hm-th">#</th><th class="hm-th left">파일 A ↔ 파일 B</th><th class="hm-th">공동 변경</th><th class="hm-th">결합도</th>
        <th class="hm-th" title="A가 바뀐 변경 중 B도 함께 바뀐 과거 비율">A → B</th><th class="hm-th" title="B가 바뀐 변경 중 A도 함께 바뀐 과거 비율">B → A</th><th class="hm-th">IDF 보정</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;

  body.querySelectorAll('.cx-pair-file').forEach(el => {
    el.addEventListener('click', () => { if (window.openFileDrilldown) window.openFileDrilldown(parseInt(el.dataset.fileId, 10)); });
  });
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

if (svgEl && typeof d3 !== 'undefined') {
  initCodeAnalysisPage().catch(err => {
    setText('cx-status', '결과 조회 실패');
    console.error('[code-analysis] 로드 실패:', err);
    renderLoadError(err);
  });
}
