import { bindPanel, action } from './ui/analytics-view.js';
import { element } from './ui/components.js';
/* ── 협업 네트워크 페이지: Network API → DOM/force-directed 그래프 연동 ──
   net-graph-svg가 있을 때(=Network 페이지)만 동작한다. */
import { getRepoId, fetchNetwork, fetchNetworkTopPairs } from './api.js';
import { renderLoadError } from './page-context.js';
import { assessDataQuality } from './policy.js';

const svgEl = document.getElementById('net-graph-svg');


const num = (n) => n == null ? '—' : Number(n).toLocaleString('ko-KR');
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt3 = (n) => n == null ? '—' : Number(n).toFixed(3);
const avatarLetter = (name) => esc((name || '?')[0].toUpperCase());

const CLUSTER_COLORS = Array.from({ length: 10 }, (_, i) => `var(--chart-${i + 1})`);
const clusterColor = (c) => c == null ? 'var(--ui-muted)' : CLUSTER_COLORS[c % CLUSTER_COLORS.length];

let activeSimulation = null;
let graphZoom = null;
let selectGraphNode = () => {};
let networkGeneration = 0;
async function initNetworkPage() {
  const id = getRepoId();
  const panel = svgEl.closest('.panel');
  const controls = element('div', 'analysis-toolbar');
  const limit = element('select'); limit.id = 'net-limit'; limit.setAttribute('aria-label', '표시 노드 상한');
  [25, 50, 100, 200].forEach(n => { const option = element('option', '', `상위 ${n}명`); option.value = n; option.selected = n === 50; limit.append(option); });
  const weight = element('input'); weight.type = 'number'; weight.min = 0; weight.step = 0.1; weight.value = 0; weight.setAttribute('aria-label', '최소 연결 강도');
  const search = element('input'); search.type = 'search'; search.id = 'net-search'; search.placeholder = '표시된 기여자 이름 검색'; search.setAttribute('aria-label', '표시된 기여자 이름 검색');
  const count = element('p', 'p-note'); count.id = 'net-visible-count'; count.setAttribute('role', 'status');
  const list = element('div', 'analysis-list'); list.id = 'net-node-list';
  let rows = [];
  const drawList = () => { const q = search.value.trim().toLowerCase(); list.replaceChildren(...rows.filter(n => String(n.name || '익명').toLowerCase().includes(q)).map(n => { const b = action(n.name, () => selectGraphNode(n.id)); b.title = `${n.name} · 클러스터 ${n.cluster ?? '확인 불가'} · 커밋 ${num(n.commit_count)}개`; b.setAttribute('aria-label', `${b.title} · 상세 열기`); return b; })); if (!list.children.length) list.append(element('span', 'p-note', '일치하는 표시 노드가 없습니다.')); };
  search.addEventListener('input', drawList);
  const load = () => {
    if (!weight.checkValidity()) { weight.reportValidity(); return; }
    const generation = ++networkGeneration;
    return bindPanel(panel, () => fetchNetwork(id, Number(limit.value), Number(weight.value)), network => {
      if (generation !== networkGeneration) return;
      rows = (network.nodes || []).map(n => ({ ...n }));
      renderMetrics(network.metrics || {}); renderGraph(rows, network.edges || []); renderEmptyEdgeNote(network.metrics || {}, network.data_quality); drawList();
      count.textContent = `표시 ${rows.length}명 · 연결 ${(network.edges || []).length}개. 위 지표와 아래 상위 협업 쌍은 전체 스냅샷 기준입니다.`;
      setText('net-status', '결과 조회 완료');
    });
  };
  let paused = false;
  const pause = action('배치 일시정지', () => { paused = !paused; if (paused) activeSimulation?.stop(); else activeSimulation?.alpha(0.3).restart(); pause.textContent = paused ? '배치 재개' : '배치 일시정지'; pause.setAttribute('aria-pressed', String(paused)); });
  controls.append(limit, element('label', '', '최소 연결 강도'), weight, action('필터 적용', () => { paused = false; pause.textContent = '배치 일시정지'; pause.setAttribute('aria-pressed', 'false'); void load(); }),
    action('확대', () => graphZoom && d3.select(svgEl).call(graphZoom.scaleBy, 1.3)), action('축소', () => graphZoom && d3.select(svgEl).call(graphZoom.scaleBy, 1 / 1.3)), action('보기 초기화', () => graphZoom && d3.select(svgEl).call(graphZoom.transform, d3.zoomIdentity)), pause);
  svgEl.before(controls, count); svgEl.after(search, list);
  await Promise.all([load(), bindPanel(document.getElementById('net-top-pairs'), () => fetchNetworkTopPairs(id, 20), pairs => renderTopPairs(Array.isArray(pairs) ? pairs : [], {}))]);
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
  /* 시뮬레이션 좌표계를 실제 픽셀 폭과 분리 — viewBox 스케일링만으로 반응형 크기 조정,
     브라우저 창 크기와 무관하게 항상 동일한 상대 배치를 유지한다. */
  const width = 700;
  const height = 420;
  activeSimulation?.stop();
  const svg = d3.select(svgEl).attr('viewBox', `0 0 ${width} ${height}`).attr('preserveAspectRatio', 'xMidYMid meet');
  svg.selectAll('*').remove();
  const scene = svg.append('g');
  graphZoom = d3.zoom().scaleExtent([0.25, 5]).on('zoom', event => scene.attr('transform', event.transform));
  svg.call(graphZoom).call(graphZoom.transform, d3.zoomIdentity);
  svg.append('defs').append('marker').attr('id', 'net-arrow').attr('viewBox', '0 -5 10 10').attr('markerUnits', 'userSpaceOnUse').attr('refX', 18).attr('markerWidth', 6).attr('markerHeight', 6).attr('orient', 'auto').append('path').attr('d', 'M0,-5L10,0L0,5').attr('fill', 'var(--chart-1)');


  if (!nodes.length) {
    svg.append('text').attr('x', width / 2).attr('y', height / 2).attr('text-anchor', 'middle')
      .style('fill', 'var(--text3)').style('font-size', '12px').text('협업 데이터가 없습니다.');
    return;
  }

  const maxCommit = Math.max(1, ...nodes.map(n => n.commit_count || 0));
  const radius = (n) => 3 + 8 * Math.sqrt((n.commit_count || 0) / maxCommit);

  const hubThreshold = [...nodes].map(n => n.betweenness_centrality || 0).sort((a, b) => b - a)[Math.max(0, Math.ceil(nodes.length * 0.15) - 1)] || Infinity;

  const nodeById = new Map(nodes.map(n => [n.id, n]));
  const links = edges
    .filter(e => nodeById.has(e.source) && nodeById.has(e.target))
    .map(e => ({ ...e }));
  const maxWeight = Math.max(0.001, ...links.map(l => l.edge_weight || 0));

  /* 엣지가 하나도 없는(고립) 기여자는 허브 클러스터 안에서 다른 노드에 가려지기 쉬우므로,
     중심 인력 대신 바깥 링에 고르게 배치해 항상 개별적으로 보이게 한다. */
  const degree = new Map(nodes.map(n => [n.id, 0]));
  links.forEach(l => {
    const s = typeof l.source === 'object' ? l.source.id : l.source;
    const t = typeof l.target === 'object' ? l.target.id : l.target;
    degree.set(s, (degree.get(s) || 0) + 1);
    degree.set(t, (degree.get(t) || 0) + 1);
  });
  nodes.forEach(n => { n.__isolated = (degree.get(n.id) || 0) === 0; });

  const cx = width / 2, cy = height / 2;
  const ringRadius = Math.min(width, height) / 2 - 55;

  /* 고립 노드는 겹치지 않도록 링 모양으로 고르게 흩어진 위치에서 출발시키되,
     완전히 고정하지 않고 약한 구심력으로 서서히 중앙 쪽으로 끌려오게 한다. */
  const isolatedNodes = nodes.filter(n => n.__isolated);
  isolatedNodes.forEach((n, i) => {
    const angle = (i / isolatedNodes.length) * Math.PI * 2 - Math.PI / 2;
    n.x = cx + ringRadius * Math.cos(angle);
    n.y = cy + ringRadius * Math.sin(angle);
  });

  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(34).strength(l => 0.1 + 0.35 * (l.edge_weight / maxWeight)))
    .force('charge', d3.forceManyBody().strength(d => d.__isolated ? -6 : -85))
    .force('x', d3.forceX(cx).strength(d => d.__isolated ? 0.05 : 0.12))
    .force('y', d3.forceY(cy).strength(d => d.__isolated ? 0.05 : 0.12))
    .force('collide', d3.forceCollide(d => radius(d) + 5));

  activeSimulation = sim;
  const linkSel = scene.append('g').selectAll('line').data(links).join('line')
    .style('stroke', 'var(--chart-1)')
    .style('stroke-width', d => 0.5 + 1.5 * (d.edge_weight / maxWeight))
    .style('stroke-opacity', nodes.length > 100 ? 0.18 : 0.35).attr('marker-end', 'url(#net-arrow)');

  const nodeSel = scene.append('g').selectAll('g').data(nodes).join('g')
    .style('cursor', 'pointer')
    .call(drag(sim))
    .attr('tabindex', 0).attr('role', 'button').attr('aria-label', d => `${d.name} · 클러스터 ${d.cluster ?? '확인 불가'} · 커밋 ${num(d.commit_count)}개 · 상세 열기`)
    .on('click', (_, d) => selectGraphNode(d.id))
    .on('keydown', (event, d) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectGraphNode(d.id); } });
  selectGraphNode = id => {
    const adjacent = new Set([id]); links.forEach(l => { const a = l.source.id ?? l.source, b = l.target.id ?? l.target; if (a === id) adjacent.add(b); if (b === id) adjacent.add(a); });
    nodeSel.style('opacity', n => adjacent.has(n.id) ? 1 : 0.2).attr('aria-pressed', n => String(n.id === id));
    nodeSel.selectAll('circle').style('stroke-width', n => n.id === id ? 4 : 1);
    linkSel.style('stroke-opacity', l => (l.source.id ?? l.source) === id || (l.target.id ?? l.target) === id ? 0.9 : 0.08);
    window.openContributorDrilldown?.(id);
  };


  nodeSel.append('circle')
    .attr('r', d => radius(d))
    .style('fill', d => clusterColor(d.cluster))
    .style('stroke', d => (d.betweenness_centrality || 0) >= hubThreshold && hubThreshold > 0 ? 'var(--text)' : 'var(--bg)')
    .style('stroke-width', d => (d.betweenness_centrality || 0) >= hubThreshold && hubThreshold > 0 ? 2.5 : 1);

  nodeSel.append('title').text(d => `${d.name} · 클러스터 ${d.cluster ?? '확인 불가'} · 커밋 ${num(d.commit_count)}개${(d.betweenness_centrality || 0) >= hubThreshold ? ' · 허브' : ''}`);

  const pad = 8;
  const hubMaxRadius = ringRadius - 25; /* 연결된(허브) 노드가 반발력으로 링 쪽까지 밀려나지 않도록 상한을 둔다 */
  sim.on('tick', () => {
    nodes.forEach(d => {
      /* 고립 노드끼리의 반발력이 누적되면 약한 구심력을 이기고 링 밖으로 서서히 번질 수 있으므로,
         두 그룹 모두 각자의 최대 반경을 넘지 않도록 매 틱마다 다시 끌어당긴다. */
      const maxR = d.__isolated ? ringRadius : hubMaxRadius;
      const dx = d.x - cx, dy = d.y - cy;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist > maxR) {
        const k = maxR / dist;
        d.x = cx + dx * k;
        d.y = cy + dy * k;
      }
      const r = radius(d) + pad;
      d.x = Math.max(r, Math.min(width - r, d.x));
      d.y = Math.max(r, Math.min(height - r, d.y));
    });
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

/* 협업 엣지 0개를 '협업이 없다'로 읽지 않게 한다.
   엣지는 PR 파일 상세·PR↔커밋 연결·리뷰 기록이 있어야 만들어지므로, 수집 범위가 부족하면 0이 나올 수 있다. */
function collectionHint(dq) {
  dq = dq || {};
  const a = assessDataQuality(dq);
  const parts = [];
  if (dq.merged_pr_count === 0) parts.push('관측 구간에 병합된 PR이 없습니다');
  else if (dq.merged_pr_count == null) parts.push('병합 PR 수를 확인할 수 없습니다');
  else {
    /* 수치가 없는 항목은 0으로 읽지 않고 '확인 불가'로 둔다 */
    if (dq.prs_with_files == null) parts.push('PR 파일 상세 수는 확인할 수 없습니다');
    else if (dq.prs_with_files < dq.merged_pr_count) parts.push(`병합 PR ${num(dq.merged_pr_count)}개 중 파일 상세가 수집된 PR은 ${num(dq.prs_with_files)}개입니다`);
    if (dq.prs_with_linked_commits == null) parts.push('PR↔커밋 연결 수는 확인할 수 없습니다');
    else if (dq.prs_with_linked_commits < dq.merged_pr_count) parts.push(`PR↔커밋 연결은 ${num(dq.prs_with_linked_commits)}/${num(dq.merged_pr_count)}개입니다`);
  }
  return { parts, partial: a.collection === 'partial' };
}

function renderEmptyEdgeNote(metrics, dq) {
  const box = document.getElementById('net-empty-note');
  if (!box) return;
  const edges = metrics.total_edge_count;
  if (edges == null || edges > 0) { box.innerHTML = ''; return; }
  const { parts } = collectionHint(dq);
  box.innerHTML = `<div class="cx-fallback"><b>협업 엣지 0개</b> — 협업이 없다는 뜻이 아닙니다. 공동 수정은 PR 파일 상세와 PR↔커밋 연결로, 리뷰는 리뷰 기록으로 계산하므로 수집 범위가 부족하면 0이 나올 수 있습니다.
    ${parts.length ? `현재 수집 범위: ${esc(parts.join(' · '))}.` : ''}</div>`;
}

function renderTopPairs(pairs, dq) {
  const wrap = document.getElementById('net-top-pairs');
  if (!wrap) return;
  if (!pairs.length) {
    const { parts } = collectionHint(dq);
    wrap.innerHTML = `<div class="c-meta" style="padding:4px;">협업 쌍 데이터가 없습니다. 협업이 없었다는 뜻이 아니라 PR 상세·리뷰 수집 범위가 부족한 경우도 포함합니다${parts.length ? ` (${esc(parts.join(' · '))})` : ''}.</div>`;
    return;
  }
  wrap.innerHTML = pairs.map(p => {
    const a = p.contributor_a, b = p.contributor_b;
    return `<div class="c-row" style="margin-bottom:10px;">
      <div class="avatar" style="cursor:pointer;background:var(--ui-accent-soft);color:var(--ui-accent);" data-action="openContributorDrilldown" data-args="${encodeURIComponent(JSON.stringify([a.id]))}">${avatarLetter(a.name)}</div>
      <div class="c-info"><button type="button" class="ui-list-link" data-action="openContributorDrilldown" data-args="${encodeURIComponent(JSON.stringify([a.id]))}">${esc(a.name)}</button></div>
      <div style="color:var(--text3);font-size:12px;flex-shrink:0;">↔</div>
      <div class="avatar" style="cursor:pointer;background:var(--ui-accent-soft);color:var(--ui-accent);" data-action="openContributorDrilldown" data-args="${encodeURIComponent(JSON.stringify([b.id]))}">${avatarLetter(b.name)}</div>
      <div class="c-info"><button type="button" class="ui-list-link" data-action="openContributorDrilldown" data-args="${encodeURIComponent(JSON.stringify([b.id]))}">${esc(b.name)}</button></div>
      <div class="c-count">${fmt3(p.edge_weight)}</div>
    </div>`;
  }).join('');
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

if (svgEl && typeof d3 !== 'undefined') {
  initNetworkPage().catch(err => {
    setText('net-status', '결과 조회 실패');
    console.error('[network] 로드 실패:', err);
    renderLoadError(err);
  });
}
