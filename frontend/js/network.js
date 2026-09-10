/* ── 협업 네트워크 페이지: Network API → DOM/force-directed 그래프 연동 ──
   net-graph-svg가 있을 때(=Network 페이지)만 동작한다. */
import { getRepoId, fetchNetwork, fetchNetworkTopPairs } from './api.js';

const svgEl = document.getElementById('net-graph-svg');

if (svgEl && typeof d3 !== 'undefined') {
  initNetworkPage().catch(err => console.error('[network] 로드 실패:', err));
}

const num = (n) => (n ?? 0).toLocaleString('en-US');
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt3 = (n) => (n ?? 0).toFixed(3);
const avatarLetter = (name) => esc((name || '?')[0].toUpperCase());

const CLUSTER_COLORS = ['#378ADD', '#1D9E75', '#EF9F27', '#E24B4A', '#534AB7', '#639922', '#F0997B', '#185FA5', '#9FE1CB', '#F09595'];
const clusterColor = (c) => c == null ? '#B4B2A9' : CLUSTER_COLORS[c % CLUSTER_COLORS.length];

async function initNetworkPage() {
  const id = getRepoId();
  const [network, topPairs] = await Promise.all([
    fetchNetwork(id, 50, 0),
    fetchNetworkTopPairs(id, 8),
  ]);
  renderMetrics(network.metrics || {});
  renderGraph(network.nodes || [], network.edges || []);
  renderTopPairs(Array.isArray(topPairs) ? topPairs : []);
}

function renderMetrics(m) {
  setText('net-m-contributors', m.total_contributor_count != null ? num(m.total_contributor_count) : '—');
  setText('net-m-density', m.network_density != null ? `밀도 ${fmt3(m.network_density)}` : ' ');
  setText('net-m-edges', m.total_edge_count != null ? num(m.total_edge_count) : '—');
  setText('net-m-clusters', m.cluster_count != null ? num(m.cluster_count) : '—');
  setText('net-m-maxcluster', m.max_cluster_size ? `최대 클러스터 ${num(m.max_cluster_size)}명` : ' ');
  setText('net-m-isolated', m.isolated_contributor_count != null ? num(m.isolated_contributor_count) : '—');
  setText('net-m-isolated-ratio', m.isolated_ratio != null ? `전체의 ${Math.round(m.isolated_ratio * 100)}%` : ' ');
}

function renderGraph(nodes, edges) {
  const width = svgEl.clientWidth || 700;
  const height = 420;
  const svg = d3.select(svgEl).attr('viewBox', `0 0 ${width} ${height}`);
  svg.selectAll('*').remove();

  if (!nodes.length) {
    svg.append('text').attr('x', width / 2).attr('y', height / 2).attr('text-anchor', 'middle')
      .style('fill', 'var(--text3)').style('font-size', '12px').text('협업 데이터가 없습니다.');
    return;
  }

  const maxCommit = Math.max(1, ...nodes.map(n => n.commit_count || 0));
  const radius = (n) => 4 + 14 * Math.sqrt((n.commit_count || 0) / maxCommit);

  const hubThreshold = [...nodes].map(n => n.betweenness_centrality || 0).sort((a, b) => b - a)[Math.max(0, Math.ceil(nodes.length * 0.15) - 1)] || Infinity;

  const nodeById = new Map(nodes.map(n => [n.id, n]));
  const links = edges
    .filter(e => nodeById.has(e.source) && nodeById.has(e.target))
    .map(e => ({ ...e }));
  const maxWeight = Math.max(0.001, ...links.map(l => l.edge_weight || 0));

  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(70).strength(l => 0.15 + 0.5 * (l.edge_weight / maxWeight)))
    .force('charge', d3.forceManyBody().strength(-140))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collide', d3.forceCollide(d => radius(d) + 6));

  const linkSel = svg.append('g').selectAll('line').data(links).join('line')
    .style('stroke', '#378ADD')
    .style('stroke-width', d => 0.5 + 3 * (d.edge_weight / maxWeight))
    .style('stroke-opacity', 0.35);

  const nodeSel = svg.append('g').selectAll('g').data(nodes).join('g')
    .style('cursor', 'pointer')
    .call(drag(sim))
    .on('click', (_, d) => window.openContributorDrilldown && window.openContributorDrilldown(d.id));

  nodeSel.append('circle')
    .attr('r', d => radius(d))
    .style('fill', d => clusterColor(d.cluster))
    .style('stroke', d => (d.betweenness_centrality || 0) >= hubThreshold && hubThreshold > 0 ? 'var(--text)' : 'var(--bg)')
    .style('stroke-width', d => (d.betweenness_centrality || 0) >= hubThreshold && hubThreshold > 0 ? 2.5 : 1);

  nodeSel.append('title').text(d => `${d.name} · 커밋 ${num(d.commit_count)}개${(d.betweenness_centrality || 0) >= hubThreshold ? ' · 허브' : ''}`);

  sim.on('tick', () => {
    linkSel
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
  });
}

function drag(sim) {
  function dragstarted(event, d) {
    if (!event.active) sim.alphaTarget(0.3).restart();
    d.fx = d.x; d.fy = d.y;
  }
  function dragged(event, d) {
    d.fx = event.x; d.fy = event.y;
  }
  function dragended(event, d) {
    if (!event.active) sim.alphaTarget(0);
    d.fx = null; d.fy = null;
  }
  return d3.drag().on('start', dragstarted).on('drag', dragged).on('end', dragended);
}

function renderTopPairs(pairs) {
  const wrap = document.getElementById('net-top-pairs');
  if (!wrap) return;
  if (!pairs.length) {
    wrap.innerHTML = '<div class="c-meta" style="padding:4px;">협업 쌍 데이터가 없습니다.</div>';
    return;
  }
  wrap.innerHTML = pairs.map(p => {
    const a = p.contributor_a, b = p.contributor_b;
    return `<div class="c-row" style="margin-bottom:10px;">
      <div class="avatar" style="cursor:pointer;background:#E6F1FB;color:#185FA5;" onclick="window.openContributorDrilldown(${a.id})">${avatarLetter(a.name)}</div>
      <div class="c-info"><div class="c-name" style="cursor:pointer;" onclick="window.openContributorDrilldown(${a.id})">${esc(a.name)}</div></div>
      <div style="color:var(--text3);font-size:12px;flex-shrink:0;">↔</div>
      <div class="avatar" style="cursor:pointer;background:#E6F1FB;color:#185FA5;" onclick="window.openContributorDrilldown(${b.id})">${avatarLetter(b.name)}</div>
      <div class="c-info"><div class="c-name" style="cursor:pointer;" onclick="window.openContributorDrilldown(${b.id})">${esc(b.name)}</div></div>
      <div class="c-count">${fmt3(p.edge_weight)}</div>
    </div>`;
  }).join('');
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}
