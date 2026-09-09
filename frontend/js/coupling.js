/* ── 코드 영향도 페이지: Coupling API → DOM/그래프 연동 ──
   coup-graph-svg가 있을 때(=Coupling 페이지)만 동작한다. */
import { getRepoId, fetchCoupling } from './api.js';

const svgEl = document.getElementById('coup-graph-svg');

if (svgEl) {
  initCouplingPage().catch(err => console.error('[coupling] 로드 실패:', err));
}

const num = (n) => (n ?? 0).toLocaleString('en-US');
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt2 = (n) => (n ?? 0).toFixed(2);
const basename = (p) => (p || '').split('/').pop();

async function initCouplingPage() {
  const id = getRepoId();
  const data = await fetchCoupling(id, 100, 0);
  if (data.error) throw new Error(data.error);

  const meta = data.meta || {};
  const pairs = data.pairs || [];
  renderMetrics(meta);
  renderGraph(pairs);
  renderPairsList(pairs);
}

function renderMetrics(meta) {
  setText('coup-m-pairs', meta.total_pairs != null ? num(meta.total_pairs) : '—');
  setText('coup-m-unit', meta.co_change_unit ? `${meta.co_change_unit === 'pr' ? 'PR' : '커밋'} 단위 · 총 ${num(meta.total_units)}개` : ' ');
  setText('coup-m-high', meta.high_coupling_count != null ? num(meta.high_coupling_count) : '—');
  setText('coup-m-avg', meta.avg_adjusted_score != null ? fmt2(meta.avg_adjusted_score) : '—');
  setText('coup-m-excluded', meta.excluded_unit_count != null ? num(meta.excluded_unit_count) : '—');
}

function renderGraph(pairs) {
  if (!svgEl || typeof d3 === 'undefined') return;
  const width = svgEl.clientWidth || 600;
  const height = 200;
  const svg = d3.select(svgEl).attr('viewBox', `0 0 ${width} ${height}`);
  svg.selectAll('*').remove();

  if (!pairs.length) {
    svg.append('text').attr('x', width / 2).attr('y', height / 2).attr('text-anchor', 'middle')
      .style('fill', 'var(--text3)').style('font-size', '12px').text('결합도 데이터가 없습니다.');
    return;
  }

  const top = pairs.slice(0, 30);
  const nodeMap = new Map();
  top.forEach(p => {
    if (!nodeMap.has(p.file_a_id)) nodeMap.set(p.file_a_id, { id: p.file_a_id, path: p.file_a_path });
    if (!nodeMap.has(p.file_b_id)) nodeMap.set(p.file_b_id, { id: p.file_b_id, path: p.file_b_path });
  });
  const nodes = Array.from(nodeMap.values());
  const links = top.map(p => ({
    source: p.file_a_id, target: p.file_b_id,
    score: p.adjusted_score, coupling: p.coupling_score,
  }));

  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(55).strength(0.3))
    .force('charge', d3.forceManyBody().strength(-70))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collide', d3.forceCollide(14))
    .stop();
  for (let i = 0; i < 250; i++) sim.tick();
  nodes.forEach(n => {
    n.x = Math.max(18, Math.min(width - 18, n.x));
    n.y = Math.max(14, Math.min(height - 14, n.y));
  });

  const maxScore = Math.max(...links.map(l => l.score), 0.01);
  svg.append('g').selectAll('line').data(links).join('line')
    .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
    .attr('x2', d => d.target.x).attr('y2', d => d.target.y)
    .style('stroke', d => d.coupling >= 0.7 ? '#E24B4A' : '#378ADD')
    .style('stroke-width', d => 0.5 + 2.5 * (d.score / maxScore))
    .style('stroke-opacity', 0.5);

  const nodeSel = svg.append('g').selectAll('g').data(nodes).join('g')
    .style('cursor', 'pointer')
    .on('click', (_, d) => window.openFileDrilldown && window.openFileDrilldown(d.id));
  nodeSel.append('circle')
    .attr('cx', d => d.x).attr('cy', d => d.y).attr('r', 5)
    .style('fill', '#378ADD').style('stroke', 'var(--bg)').style('stroke-width', 1.5);
  nodeSel.append('title').text(d => d.path);
}

function renderPairsList(pairs) {
  const wrap = document.getElementById('coup-pairs-list');
  if (!wrap) return;
  if (!pairs.length) {
    wrap.innerHTML = '<div style="font-size:12px;color:var(--text3);text-align:center;padding:24px;">강결합 파일 쌍이 없습니다.</div>';
    return;
  }
  wrap.innerHTML = pairs.slice(0, 30).map(p => `
    <div class="coup-row">
      <div class="coup-file" style="cursor:pointer;" title="${esc(p.file_a_path)}" onclick="window.openFileDrilldown(${p.file_a_id})">${esc(basename(p.file_a_path))}</div>
      <div style="color:var(--text3);font-size:11px;flex-shrink:0;">↔</div>
      <div class="coup-file" style="cursor:pointer;" title="${esc(p.file_b_path)}" onclick="window.openFileDrilldown(${p.file_b_id})">${esc(basename(p.file_b_path))}</div>
      <div class="coup-cnt">${num(p.co_change_count)}회</div>
      <div class="coup-pct">${Math.round(p.coupling_score * 100)}%</div>
    </div>`).join('');
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}
