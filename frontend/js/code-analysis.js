/* ── 코드분석 페이지: /tree API → 트리 다이어그램 + 종합 리스트 + 정렬 토글 ──
   cx-svg가 있을 때(=코드분석 페이지)만 동작한다. risk.js/coupling.js/busfactor.js에 있던
   렌더 로직 중 재사용할 부분만 옮겨왔다.
   기존 4개 페이지(위험도/코드영향도/Bus Factor/BF 트리맵)를 이 한 페이지로 통합한다. */
import { getRepoId, fetchRepoTree } from './api.js';
import { getRepoGithubInfo, githubFileUrl as ghFileUrl, githubFolderUrl as ghFolderUrl } from './github.js';

const svgEl = document.getElementById('cx-svg');

const num = (n) => (n ?? 0).toLocaleString('en-US');
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt2 = (n) => (n ?? 0).toFixed(2);

/* ── 색상 인코딩 — 정렬 기준(위험도/영향도/BF)이 바뀌면 트리 색도 함께 바뀐다 ── */
const BF_COLORS = { 1: '#E24B4A', 2: '#EF9F27', 3: '#378ADD', 4: '#1D9E75', 5: '#639922' };
const bfColor = (bf) => bf == null ? '#B4B2A9' : BF_COLORS[Math.min(5, Math.max(1, Math.round(bf)))];

const riskColor = (score) => {
  if (score == null) return '#B4B2A9';
  if (score >= 0.7) return '#E24B4A';
  if (score >= 0.5) return '#EF9F27';
  if (score >= 0.3) return '#378ADD';
  return '#639922';
};

const couplingColor = (score) => {
  if (score == null) return '#B4B2A9';
  if (score >= 0.7) return '#E24B4A';
  if (score >= 0.4) return '#EF9F27';
  if (score > 0) return '#378ADD';
  return '#639922';
};

/* 정렬 토글(9번)과 트리 색 인코딩(6번)이 공유하는 메트릭 정의 —
   key: 디렉토리 평균값을 저장할 필드명, leafKey: /tree files[] 원본 필드명,
   dir: 방향(desc=값이 클수록 위험, asc=값이 작을수록 위험) */
const METRICS = {
  risk:     { key: 'avgRisk',     leafKey: 'risk_score',     color: riskColor,     label: '위험도순',  dir: 'desc',
              legendTitle: 'Risk Score', legendItems: [['안전 (<0.3)', '#639922'], ['0.3~0.5', '#378ADD'], ['0.5~0.7', '#EF9F27'], ['위험 (≥0.7)', '#E24B4A']] },
  coupling: { key: 'avgCoupling', leafKey: 'coupling_score', color: couplingColor, label: '영향도순',  dir: 'desc',
              legendTitle: '결합도', legendItems: [['낮음 (0)', '#639922'], ['0~0.4', '#378ADD'], ['0.4~0.7', '#EF9F27'], ['강결합 (≥0.7)', '#E24B4A']] },
  bf:       { key: 'avgBF',       leafKey: 'bus_factor',     color: bfColor,       label: 'BF순',     dir: 'asc',
              legendTitle: 'Bus Factor', legendItems: [['1 (위험)', BF_COLORS[1]], ['2', BF_COLORS[2]], ['3', BF_COLORS[3]], ['4', BF_COLORS[4]], ['5+ (안전)', BF_COLORS[5]]] },
};

let currentMetric = 'risk';
let TREE = { name: 'root', children: [] };
let FILES = [];

/* 폴더명/파일명 클릭 → GitHub 이동에 필요한 레포 정보 (summary에서 가져옴) */
let GH_INFO = null;

async function initCodeAnalysisPage() {
  renderSortToggle();

  const id = getRepoId();
  const [data, ghInfo] = await Promise.all([fetchRepoTree(id), getRepoGithubInfo(id)]);
  TREE = data.tree || { name: 'root', children: [] };
  FILES = data.files || [];
  computeAverages(TREE);

  GH_INFO = ghInfo;

  renderSummary();
  renderLegend();
  renderList();

  if (!TREE.children || !TREE.children.length) {
    renderEmptyTree();
    return;
  }

  /* "모두 펼치기"는 파일이 많은 레포에서 SVG 노드가 수천 개로 불어나 브라우저가 멎을 수 있어 막아둔다 */
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

/* 디렉토리 노드의 평균 Risk/BF/Coupling을 자식들로부터 post-order로 역산해 채운다
   (/tree는 leaf 값만 내려주므로, 세 지표를 한 번에 미리 계산해두면 정렬 토글 시
   재계산 없이 바로 색만 바꿀 수 있다). 하위 파일 수(fileCount)도 같이 센다 —
   접힌 폴더는 d3 계층에서 자식이 숨겨져 leaves()로 셀 수 없기 때문. */
function computeAverages(node) {
  if (!node.children) {
    node.avgRisk = node.risk_score;
    node.avgBF = node.bus_factor;
    node.avgCoupling = node.coupling_score;
    node.fileCount = 1;
    return;
  }
  node.children.forEach(computeAverages);
  node.fileCount = node.children.reduce((s, c) => s + c.fileCount, 0);
  const avg = (vals) => {
    const v = vals.filter(x => x != null);
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
  };
  node.avgRisk = avg(node.children.map(c => c.avgRisk));
  node.avgBF = avg(node.children.map(c => c.avgBF));
  node.avgCoupling = avg(node.children.map(c => c.avgCoupling));
}

function renderSummary() {
  const highRisk = FILES.filter(f => f.risk_score != null && f.risk_score >= 0.7).length;
  const bf1 = FILES.filter(f => f.is_bus_factor_risk).length;
  const highCoupling = FILES.filter(f => f.coupling_score != null && f.coupling_score >= 0.7).length;
  const risks = FILES.map(f => f.risk_score).filter(v => v != null);
  const avgRisk = risks.length ? risks.reduce((a, b) => a + b, 0) / risks.length : null;

  setText('cx-m-highrisk', num(highRisk));
  setText('cx-m-avgrisk', avgRisk != null ? fmt2(avgRisk) : '—');
  setText('cx-m-bf1', num(bf1));
  setText('cx-m-coupling', num(highCoupling));
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
  if (d.data.avgRisk != null) parts.push(`Risk ${d.data.avgRisk.toFixed(2)}`);
  if (d.data.avgBF != null) parts.push(`BF ${d.data.avgBF.toFixed(1)}`);
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
    `<div class="leg-item"><div class="leg-dot" style="background:${color};"></div>${metric.legendTitle} ${esc(label)}</div>`
  ).join('');
  const shapes = [
    ['dir', '폴더 (숫자 = 하위 파일 수)'],
    ['file', '파일'],
    ['folded', '접힌 폴더 (클릭하면 펼침)'],
  ].map(([cls, label]) => `<div class="leg-item"><span class="leg-shape ${cls}"></span>${label}</div>`).join('');
  legend.innerHTML = `${colors}<div style="flex-basis:100%;height:0;"></div>${shapes}`;
}

/* 트리 노드에 마우스를 올리거나 클릭하면 우측 카드에 Risk/BF/결합도를 한번에 보여준다 */
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

  const chip = (val, color) => `<span class="hm-val" style="padding:2px 8px;background:${color}22;color:${color};font-weight:600;">${esc(val)}</span>`;
  const row = (label, html) => `<div class="cx-detail-row"><span>${esc(label)}</span>${html}</div>`;

  if (!isDir(d)) {
    metricsEl.innerHTML = [
      row('Risk Score', chip(d.data.risk_score != null ? fmt2(d.data.risk_score) : '—', riskColor(d.data.risk_score))),
      row('Bus Factor', chip(d.data.bus_factor ?? '—', bfColor(d.data.bus_factor)) + (d.data.is_bus_factor_risk ? '<span class="badge badge-red" style="margin-left:4px;">승계위험</span>' : '')),
      row('결합도', chip(d.data.coupling_score != null ? fmt2(d.data.coupling_score) : '—', couplingColor(d.data.coupling_score))),
      row('결합 파일 수', `<span>${num(d.data.coupled_file_count)}</span>`),
    ].join('');
  } else {
    metricsEl.innerHTML = [
      row('평균 Risk', chip(d.data.avgRisk != null ? fmt2(d.data.avgRisk) : '—', riskColor(d.data.avgRisk))),
      row('평균 Bus Factor', chip(d.data.avgBF != null ? fmt2(d.data.avgBF) : '—', bfColor(d.data.avgBF))),
      row('평균 결합도', chip(d.data.avgCoupling != null ? fmt2(d.data.avgCoupling) : '—', couplingColor(d.data.avgCoupling))),
      row('하위 파일 수', `<span>${num(d.data.fileCount)}개</span>`),
    ].join('');
  }
}

function renderList() {
  const tbody = document.getElementById('cx-list-tbody');
  const note = document.getElementById('cx-list-note');
  if (!tbody) return;

  const metric = METRICS[currentMetric];
  if (note) note.textContent = `${metric.label} · 상위 20개`;

  if (!FILES.length) {
    tbody.innerHTML = '<tr><td class="hm-td" colspan="5" style="text-align:center;color:var(--text3);padding:24px;">파일 데이터가 없습니다.</td></tr>';
    return;
  }

  const sorted = [...FILES].sort((a, b) => {
    const av = a[metric.leafKey], bv = b[metric.leafKey];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return metric.dir === 'asc' ? av - bv : bv - av;
  }).slice(0, 20);

  const byId = new Map(sorted.map(f => [f.file_id, f]));

  tbody.innerHTML = sorted.map(f => {
    const bfBadge = f.is_bus_factor_risk
      ? '<span class="badge badge-red" title="이 파일을 아는 사람이 한 명뿐입니다">BF=1</span>' : '';
    const couplingBadge = f.coupling_score >= 0.7
      ? '<span class="badge badge-amber" style="margin-left:4px;" title="강결합 파일">강결합</span>' : '';
    const fileUrl = githubFileUrl(f.path);
    const pathHtml = fileUrl
      ? `<a href="${esc(fileUrl)}" target="_blank" rel="noopener" class="cx-file-link" title="GitHub에서 열기">${esc(f.path)}</a>`
      : esc(f.path);
    return `<tr style="cursor:pointer;" data-file-id="${f.file_id}" title="수정한 사람·영향 파일 보기">
      <td class="hm-td file" title="${esc(f.path)}">${pathHtml}${bfBadge}${couplingBadge}</td>
      <td class="hm-td"><span class="hm-val" style="background:${riskColor(f.risk_score)}22;color:${riskColor(f.risk_score)};font-weight:600;">${f.risk_score != null ? fmt2(f.risk_score) : '—'}</span></td>
      <td class="hm-td" style="text-align:center;">${f.bus_factor ?? '—'}</td>
      <td class="hm-td"><span class="hm-val" style="background:${couplingColor(f.coupling_score)}22;color:${couplingColor(f.coupling_score)};">${f.coupling_score != null ? fmt2(f.coupling_score) : '—'}</span></td>
      <td class="hm-td" style="text-align:center;">${num(f.coupled_file_count)}</td>
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

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

if (svgEl && typeof d3 !== 'undefined') {
  initCodeAnalysisPage().catch(err => console.error('[code-analysis] 로드 실패:', err));
}
