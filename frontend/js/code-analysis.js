/* ── 코드분석 페이지: /tree API → 트리맵 + 종합 리스트 + 정렬 토글 ──
   cx-svg가 있을 때(=코드분석 페이지)만 동작한다. treemap.js(트리 렌더링·breadcrumb·드릴다운)를
   뼈대로 삼고, risk.js/coupling.js/busfactor.js에 있던 렌더 로직 중 재사용할 부분만 옮겨왔다.
   기존 4개 페이지(위험도/코드영향도/Bus Factor/BF 트리맵)를 이 한 페이지로 통합한다. */
import { getRepoId, fetchRepoTree } from './api.js';

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
let path = [TREE]; // 브레드크럼 스택 — path[path.length-1]이 현재 보고 있는 디렉토리

async function initCodeAnalysisPage() {
  renderSortToggle();

  const id = getRepoId();
  const data = await fetchRepoTree(id);
  TREE = data.tree || { name: 'root', children: [] };
  FILES = data.files || [];
  computeAverages(TREE);
  path = [TREE];

  renderSummary();
  renderTree();
  renderList();
  window.addEventListener('resize', renderTree);
}

/* 디렉토리 노드의 평균 Risk/BF/Coupling을 자식들로부터 post-order로 역산해 채운다
   (/tree는 leaf 값만 내려주므로, 세 지표를 한 번에 미리 계산해두면 정렬 토글 시
   재계산 없이 바로 색만 바꿀 수 있다). */
function computeAverages(node) {
  if (!node.children) {
    node.avgRisk = node.risk_score;
    node.avgBF = node.bus_factor;
    node.avgCoupling = node.coupling_score;
    return;
  }
  node.children.forEach(computeAverages);
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
      renderTree();
      renderList();
    });
  });
}

function renderBreadcrumb() {
  const wrap = document.getElementById('cx-breadcrumb');
  if (!wrap) return;
  wrap.innerHTML = path.map((n, i) => {
    const isLast = i === path.length - 1;
    const label = esc(n.name);
    return isLast
      ? `<span style="color:var(--text);font-weight:600;">${label}</span>`
      : `<span style="cursor:pointer;text-decoration:underline;" data-depth="${i}">${label}</span> <span style="color:var(--text3);">/</span> `;
  }).join('');
  wrap.querySelectorAll('[data-depth]').forEach(el => {
    el.addEventListener('click', () => {
      const depth = parseInt(el.dataset.depth, 10);
      path = path.slice(0, depth + 1);
      renderTree();
    });
  });
}

function renderTree() {
  if (!svgEl) return;
  renderBreadcrumb();

  const current = path[path.length - 1];
  const width = svgEl.clientWidth || 900;
  const height = 420;
  const svg = d3.select(svgEl).attr('viewBox', `0 0 ${width} ${height}`);
  svg.selectAll('*').remove();

  if (!current.children || !current.children.length) {
    svg.append('text').attr('x', width / 2).attr('y', height / 2).attr('text-anchor', 'middle')
      .style('fill', 'var(--text3)').style('font-size', '12px').text('빈 디렉토리입니다.');
    renderLegend();
    return;
  }

  const root = d3.hierarchy({ ...current, children: current.children })
    .sum(d => d.children ? 0 : (d.value || 1))
    .sort((a, b) => b.value - a.value);

  d3.treemap().size([width, height]).paddingInner(2).round(true)(root);

  const metric = METRICS[currentMetric];

  const cell = svg.selectAll('g').data(root.children).join('g')
    .attr('transform', d => `translate(${d.x0},${d.y0})`)
    .style('cursor', 'pointer')
    .on('click', (_, d) => {
      if (d.data.children) {
        path = [...path, d.data];
        renderTree();
      } else if (d.data.file_id != null && window.openFileDrilldown) {
        window.openFileDrilldown(d.data.file_id, d.data);
      }
    });

  cell.append('rect')
    .attr('width', d => Math.max(0, d.x1 - d.x0))
    .attr('height', d => Math.max(0, d.y1 - d.y0))
    .style('fill', d => metric.color(d.data[metric.key]))
    .style('fill-opacity', d => d.data.children ? 0.55 : 0.85)
    .style('stroke', 'var(--bg)')
    .style('stroke-width', 1.5);

  cell.append('title')
    .text(d => {
      const parts = [d.data.name];
      if (d.data.children) parts.push(`파일 ${d.leaves().length}개`);
      if (d.data.avgRisk != null) parts.push(`Risk ${d.data.avgRisk.toFixed(2)}`);
      if (d.data.avgBF != null) parts.push(`BF ${d.data.avgBF.toFixed(1)}`);
      if (d.data.avgCoupling != null) parts.push(`결합도 ${d.data.avgCoupling.toFixed(2)}`);
      return parts.join(' · ');
    });

  cell.filter(d => (d.x1 - d.x0) > 34 && (d.y1 - d.y0) > 16)
    .append('text')
    .attr('x', 5).attr('y', 15)
    .style('font-size', '10px')
    .style('fill', d => d.data.children ? 'var(--text)' : '#fff')
    .style('pointer-events', 'none')
    .text(d => {
      const w = d.x1 - d.x0;
      const maxChars = Math.max(3, Math.floor(w / 6));
      return d.data.name.length > maxChars ? d.data.name.slice(0, maxChars - 1) + '…' : d.data.name;
    });

  cell.filter(d => d.data.children && (d.y1 - d.y0) > 30)
    .append('text')
    .attr('x', 5).attr('y', 28)
    .style('font-size', '9px')
    .style('fill', 'var(--text2)')
    .style('pointer-events', 'none')
    .text(d => `${d.leaves().length}개 파일`);

  renderLegend();
}

function renderLegend() {
  const legend = document.getElementById('cx-legend');
  if (!legend) return;
  const metric = METRICS[currentMetric];
  legend.innerHTML = metric.legendItems.map(([label, color]) =>
    `<div class="leg-item"><div class="leg-dot" style="background:${color};"></div>${metric.legendTitle} ${esc(label)}</div>`
  ).join('');
}

function renderList() {
  const tbody = document.getElementById('cx-list-tbody');
  const note = document.getElementById('cx-list-note');
  if (!tbody) return;

  const metric = METRICS[currentMetric];
  if (note) note.textContent = `${metric.label} · 상위 100개`;

  if (!FILES.length) {
    tbody.innerHTML = '<tr><td class="hm-td" colspan="6" style="text-align:center;color:var(--text3);padding:24px;">파일 데이터가 없습니다.</td></tr>';
    return;
  }

  const sorted = [...FILES].sort((a, b) => {
    const av = a[metric.leafKey], bv = b[metric.leafKey];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return metric.dir === 'asc' ? av - bv : bv - av;
  }).slice(0, 100);

  const byId = new Map(sorted.map(f => [f.file_id, f]));

  tbody.innerHTML = sorted.map(f => {
    const bfBadge = f.is_bus_factor_risk
      ? '<span class="badge badge-red" title="이 파일을 아는 사람이 한 명뿐입니다">BF=1</span>' : '';
    const couplingBadge = f.coupling_score >= 0.7
      ? '<span class="badge badge-amber" style="margin-left:4px;" title="강결합 파일">강결합</span>' : '';
    return `<tr style="cursor:pointer;" data-file-id="${f.file_id}" title="수정한 사람·영향 파일 보기">
      <td class="hm-td file" title="${esc(f.path)}">${esc(f.path)}${bfBadge}${couplingBadge}</td>
      <td class="hm-td"><span class="hm-val" style="background:${riskColor(f.risk_score)}22;color:${riskColor(f.risk_score)};font-weight:600;">${f.risk_score != null ? fmt2(f.risk_score) : '—'}</span></td>
      <td class="hm-td" style="text-align:center;">${f.bus_factor ?? '—'}</td>
      <td class="hm-td" style="text-align:center;">${f.entropy != null ? fmt2(f.entropy) : '—'}</td>
      <td class="hm-td"><span class="hm-val" style="background:${couplingColor(f.coupling_score)}22;color:${couplingColor(f.coupling_score)};">${f.coupling_score != null ? fmt2(f.coupling_score) : '—'}</span></td>
      <td class="hm-td" style="text-align:center;">${num(f.coupled_file_count)}</td>
    </tr>`;
  }).join('');

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
