import { bindPanel, action } from './ui/analytics-view.js';
import { element, setText } from './ui/components.js';
import { esc, num, fmtWindow, windowDays, DASH } from './fmt.js';
/* ── 협업 네트워크 페이지 ──
   net-graph-svg가 있을 때(=협업 네트워크 페이지)만 동작한다.

   데이터(서버 app/services/collab_network.py)
   - nodes: 표시할 개발자. 커밋·PR 참여·협업 강도 순위의 합집합(selected_by), 연결이 없는 개발자는 isolated 목록으로 따로.
   - pairs: 개발자 쌍마다 하나. 공동 수정(방향 없음) + 리뷰 A→B / B→A. strength는 저장된 방향별 강도의 합.
   - project / view: 저장소 전체 통계와 지금 그래프의 통계를 구분한다.
   예전 서버(pairs 없음)의 응답도 방향별 edges로 쌍을 만들어 그린다.

   그리기(d3 force)
   - 그래프 영역의 실제 픽셀 크기를 좌표계로 쓰고, 같은 커뮤니티(Louvain)는 같은 중심점으로 끌어 모은다.
   - 관계 종류·강도 필터는 선만 바꾸고 배치는 다시 계산하지 않는다. 표시 인원을 바꿀 때만 새로 받아 오며,
     이미 있던 개발자는 위치를 이어받는다. 창·패널 크기가 바뀌어도 위치와 확대 상태를 유지한다. */
import { getRepoId, fetchNetwork, fetchNetworkTopPairs, fetchContributorCollaborators, fetchSnapshotMeta } from './api.js';
import { renderLoadError } from './page-context.js';
import { assessDataQuality } from './policy.js';

const svgEl = document.getElementById('net-graph-svg');

const fmt3 = (n) => n == null ? DASH : Number(n).toFixed(3);
const count = (n, unit) => n == null ? DASH : `${num(n)}${unit}`;
const MAIN_COMMUNITIES = 5;
export const communityColor = (rank) => rank != null && rank < MAIN_COMMUNITIES ? `var(--chart-${rank + 1})` : 'var(--chart-6)';
const RELATIONS = [['all', '모든 관계'], ['co_edit', '공동 수정만'], ['review', '리뷰만']];
const STRENGTHS = [['all', '모든 강도'], ['no_weak', '약한 관계 제외'], ['medium', '중간 이상'], ['strong', '강한 관계만']];
const STRENGTH_TEXT = { no_weak: '하위 30% 제외', medium: '하위 50% 제외', strong: '상위 30%만' };
const LABEL_ZOOM = 1.8;
const LABEL_TOP = 10;

const NODE_LABEL_CHARS = 8;
export function shortName(name) {
  const chars = Array.from(String(name || '익명'));
  return chars.length > NODE_LABEL_CHARS ? `${chars.slice(0, NODE_LABEL_CHARS - 1).join('')}…` : chars.join('');
}

const state = { relation: 'all', strength: 'all', paused: false, selected: null, hover: null };
let data = null;              // normalized network response
let graph = null;             // current d3 scene
let networkGeneration = 0;
let detailGeneration = 0;
let snapshotMeta = null;

if (svgEl && typeof d3 !== 'undefined') {
  initNetworkPage().catch(err => {
    console.error('[network] 로드 실패:', err);
    renderLoadError(err);
  });
}

/* ── 응답 정리: 새 서버는 그대로, 예전 서버(pairs 없음)는 방향별 edges로 쌍을 만든다 ── */
export function normalizeNetwork(net) {
  const nodes = (net.nodes || []).map(n => ({ ...n }));
  const byId = new Map(nodes.map(n => [n.id, n]));
  let pairs = Array.isArray(net.pairs) ? net.pairs.map(p => ({ ...p })) : null;
  const legacy = !pairs;
  if (legacy) {
    const map = new Map();
    for (const e of net.edges || []) {
      const a = Math.min(e.source, e.target), b = Math.max(e.source, e.target), key = `${a}-${b}`;
      const entry = map.get(key) || { a, b, co_edit_raw: 0, review_a_to_b: 0, review_b_to_a: 0, strength: 0 };
      entry.co_edit_raw = Math.max(entry.co_edit_raw, e.co_edit_raw ?? (e.review_directed == null ? 1 : 0));
      if (e.source === a) entry.review_a_to_b = e.review_directed || 0; else entry.review_b_to_a = e.review_directed || 0;
      entry.strength += e.edge_weight || 0;
      map.set(key, entry);
    }
    pairs = [...map.values()].map(p => ({ ...p, co_edit: p.co_edit_raw > 0, review: p.review_a_to_b > 0 || p.review_b_to_a > 0,
      co_edit_files: null, co_pr_count: null, reviews_a_to_b: null, reviews_b_to_a: null, prs: null }));
  }
  pairs = pairs.filter(p => byId.has(p.a) && byId.has(p.b));
  pairs.forEach(p => { p.a_name = byId.get(p.a).name; p.b_name = byId.get(p.b).name; });
  if (legacy) {
    const degree = new Map(), size = new Map();
    pairs.forEach(p => { degree.set(p.a, (degree.get(p.a) || 0) + 1); degree.set(p.b, (degree.get(p.b) || 0) + 1); });
    nodes.forEach(n => { if (n.cluster != null && degree.get(n.id)) size.set(n.cluster, (size.get(n.cluster) || 0) + 1); });
    const order = [...size.keys()].sort((a, b) => size.get(b) - size.get(a) || a - b);
    const sorted = nodes.map(n => n.betweenness_centrality || 0).sort((a, b) => b - a);
    const threshold = sorted[Math.max(0, Math.ceil(nodes.length * 0.15) - 1)] || 0;
    nodes.forEach(n => Object.assign(n, {
      degree: degree.get(n.id) || 0, isolated: false, pr_count: n.pr_count ?? null, reviews_given: null,
      is_hub: threshold > 0 && (n.betweenness_centrality || 0) >= threshold,
      community_rank: degree.get(n.id) && n.cluster != null ? order.indexOf(n.cluster) : null,
      community_size: degree.get(n.id) && n.cluster != null ? size.get(n.cluster) : null,
      degree_centrality: null, selected_by: ['commits'],
    }));
  }
  const m = net.metrics || {};
  const strengths = pairs.map(p => p.strength).sort((a, b) => a - b);
  const q = (x) => strengths.length ? strengths[Math.min(strengths.length - 1, Math.max(0, Math.ceil(x * strengths.length) - 1))] : null;
  return {
    legacy, nodes, pairs, byId,
    project: net.project || { total_developers: m.total_contributor_count, total_relations: m.total_edge_count,
      community_count: m.cluster_count, isolated_count: m.isolated_contributor_count, density: m.network_density,
      total_commits: null, total_prs: net.data_quality?.merged_pr_count ?? null },
    communities: net.communities || [],
    isolated: net.isolated || [],
    cutoffs: net.strength_cutoffs || { no_weak: q(0.3), medium: q(0.5), strong: q(0.7) },
    selection: net.selection || { limit: nodes.length, criteria: ['commits'], pr_participation_available: false },
    detailsAvailable: !!net.details_available,
    reviewAvailable: net.review_available ?? pairs.some(p => p.review),
    largePrRule: net.large_pr_rule || '',
    dataQuality: net.data_quality || {},
  };
}

/* ── 페이지 ── */
async function initNetworkPage() {
  const id = getRepoId();
  buildToolbar(id);
  bindSideKeys();
  try { snapshotMeta = (await fetchSnapshotMeta(id))?.snapshot || null; } catch { snapshotMeta = null; }
  renderPeriod();
  await Promise.all([loadNetwork(id), bindPanel(document.getElementById('net-top-pairs'), () => fetchNetworkTopPairs(id, 10), pairs => renderTopPairs(Array.isArray(pairs) ? pairs : []))]);
}

function renderPeriod() {
  const days = windowDays(snapshotMeta);
  setText('net-period', snapshotMeta ? `분석 기간 ${fmtWindow(snapshotMeta)}${days != null ? ` · ${days}일` : ''} · 기간은 분석할 때 정해집니다` : '');
}

function buildToolbar(id) {
  const bar = document.getElementById('net-toolbar');
  const select = (sid, label, options, value, onChange) => {
    const box = element('label', 'net-field'); box.append(element('span', '', label));
    const s = element('select'); s.id = sid; s.setAttribute('aria-label', label);
    options.forEach(([v, text]) => { const o = element('option', '', text); o.value = String(v); o.selected = String(v) === String(value); s.append(o); });
    s.addEventListener('change', () => onChange(s.value)); box.append(s); return box;
  };
  const limit = select('net-limit', '표시 인원', [25, 50, 100, 200].map(n => [n, `최대 ${n}명`]), 50, () => void loadNetwork(id));
  const relation = select('net-relation', '관계', RELATIONS, state.relation, v => { state.relation = v; applyFilters(); });
  const strength = select('net-strength', '연결 강도', STRENGTHS, state.strength, v => { state.strength = v; applyFilters(); });
  const pause = action('배치 일시정지', () => {
    state.paused = !state.paused;
    if (state.paused) graph?.sim.stop(); else graph?.sim.alpha(0.3).restart();
    pause.textContent = state.paused ? '배치 재개' : '배치 일시정지'; pause.setAttribute('aria-pressed', String(state.paused));
  });
  pause.id = 'net-pause';
  const panel = document.getElementById('net-panel');
  const full = action('전체 화면', () => { if (document.fullscreenElement) void document.exitFullscreen(); else void panel.requestFullscreen?.(); });
  document.addEventListener('fullscreenchange', () => { full.textContent = document.fullscreenElement ? '전체 화면 닫기' : '전체 화면'; });
  const zoomBy = k => graph && d3.select(svgEl).call(graph.zoom.scaleBy, k);
  bar.append(relation, strength, limit,
    action('확대', () => zoomBy(1.3)), action('축소', () => zoomBy(1 / 1.3)),
    action('보기 초기화', () => graph && d3.select(svgEl).call(graph.zoom.transform, d3.zoomIdentity)), pause, full);
}

function loadNetwork(id) {
  const generation = ++networkGeneration;
  const limit = Number(document.getElementById('net-limit')?.value || 50);
  setText('net-status', '결과 조회 중');
  return bindPanel(document.getElementById('net-graph-wrap'), () => fetchNetwork(id, limit, 0), net => {
    if (generation !== networkGeneration) return;
    data = normalizeNetwork(net);
    state.selected = null;
    renderProject(); renderGraph(); renderLegend(); renderIsolated(); renderNodeList(); applyFilters(); showSummary();
    setText('net-status', '결과 조회 완료');
  });
}

/* ── 프로젝트 전체 통계(저장소 전체 기준) ── */
function renderProject() {
  const p = data.project;
  setText('net-m-contributors', count(p.total_developers, ''));
  setText('net-m-density', p.density != null ? `관계 밀도 ${fmt3(p.density)}` : ' ');
  setText('net-m-commits', count(p.total_commits, ''));
  setText('net-m-prs', count(p.total_prs, ''));
  setText('net-m-edges', p.total_relations != null ? `협업 관계 ${num(p.total_relations)}개` : ' ');
  setText('net-m-clusters', count(p.community_count, ''));
  setText('net-m-isolated', p.isolated_count != null ? `연결이 발견되지 않은 개발자 ${num(p.isolated_count)}명` : ' ');
}

/* ── 필터: 선만 바꾼다(배치 재계산 없음) ── */
function visiblePairs() {
  if (!data) return [];
  const cut = data.cutoffs[state.strength];
  return data.pairs.filter(p => (state.relation === 'all' || (state.relation === 'co_edit' ? p.co_edit : p.review))
    && (state.strength === 'all' || cut == null || p.strength >= cut));
}

function applyFilters() {
  if (!data) return;
  const shown = new Set(visiblePairs());
  const connected = new Set([...shown].flatMap(p => [p.a, p.b]));
  if (graph) {
    graph.linkSel.attr('display', p => shown.has(p) ? null : 'none')
      .attr('stroke-dasharray', p => (state.relation === 'review' || !p.co_edit) ? '5 4' : null)
      .attr('marker-end', p => p.review_a_to_b > 0 && state.relation !== 'co_edit' ? 'url(#net-arrow)' : null)
      .attr('marker-start', p => p.review_b_to_a > 0 && state.relation !== 'co_edit' ? 'url(#net-arrow)' : null);
    graph.hitSel.attr('display', p => shown.has(p) ? null : 'none');
    graph.nodeSel.classed('net-unlinked', n => !connected.has(n.id));
    restyle();
  }
  const communities = new Set(data.nodes.filter(n => n.community_rank != null).map(n => n.community_rank));
  const cut = state.strength === 'all' ? '' : ` · 강도 ${STRENGTH_TEXT[state.strength]}`;
  const criteria = data.selection.criteria.map(c => ({ commits: '커밋 수', prs: 'PR 참여', collaboration: '협업 강도' })[c]).join('·');
  setText('net-view-stats', data.nodes.length
    ? `현재 화면: 개발자 ${num(data.nodes.length)}명 · 관계 ${num(shown.size)}개 · 커뮤니티 ${num(communities.size)}개 · 화면 안 연결 없음 ${num(data.nodes.length - connected.size)}명${cut}. `
      + `${criteria} 상위를 번갈아 골라 최대 ${num(data.selection.limit)}명을 보여 줍니다${data.selection.connected_developers != null ? ` (연결이 있는 개발자 ${num(data.selection.connected_developers)}명 중)` : ''}.`
    : '현재 화면: 표시할 협업 관계가 없습니다.');
}

/* ── 그래프 ── */
function measure() {
  return { W: Math.max(320, Math.round(svgEl.clientWidth || 700)), H: Math.max(300, Math.round(svgEl.clientHeight || 460)) };
}

// 커뮤니티 중심점: 7개 이하는 한 원 위에 고르게, 그보다 많으면 해바라기 배열로 퍼뜨린다. 커뮤니티 밖(-1)은 가운데.
function communityCenters(W, H) {
  const ranks = [...new Set(data.nodes.map(n => n.community_rank ?? -1))].sort((a, b) => (a < 0) - (b < 0) || a - b);
  const main = ranks.filter(r => r >= 0);
  const rx = W * 0.3, ry = H * 0.3;
  const centers = new Map([[-1, { x: W / 2, y: H / 2 }]]);
  main.forEach((rank, i) => {
    if (main.length === 1) { centers.set(rank, { x: W / 2, y: H / 2 }); return; }
    const ring = main.length <= 7, angle = ring ? -Math.PI / 2 + i * 2 * Math.PI / main.length : i * 2.39996;
    const r = ring ? 1 : Math.sqrt((i + 1) / main.length);
    centers.set(rank, { x: W / 2 + rx * r * Math.cos(angle), y: H / 2 + ry * r * Math.sin(angle) });
  });
  return centers;
}

function renderGraph() {
  const previous = new Map((graph?.nodes || []).map(n => [n.id, { x: n.x, y: n.y }]));
  const keepTransform = graph ? d3.zoomTransform(svgEl) : d3.zoomIdentity;
  graph?.sim.stop(); graph?.resize?.disconnect();
  const svg = d3.select(svgEl);
  svg.selectAll('*').remove();
  const { W, H } = measure();
  svg.attr('viewBox', `0 0 ${W} ${H}`).attr('preserveAspectRatio', 'xMidYMid meet');
  svg.append('defs').append('marker').attr('id', 'net-arrow').attr('viewBox', '0 -4 8 8').attr('refX', 7).attr('markerUnits', 'userSpaceOnUse')
    .attr('markerWidth', 9).attr('markerHeight', 9).attr('orient', 'auto-start-reverse').append('path').attr('d', 'M0,-4L8,0L0,4').attr('class', 'net-arrow-head');
  const scene = svg.append('g');
  if (!data.nodes.length) {
    svg.append('text').attr('x', W / 2).attr('y', H / 2).attr('text-anchor', 'middle').attr('class', 'net-empty-text')
      .text(data.project.total_developers ? '수집된 기록에서 협업 관계가 발견되지 않았습니다' : '협업 데이터가 없습니다');
    graph = null; renderEmptyNote(); return;
  }
  renderEmptyNote();

  const nodes = data.nodes;
  const maxCommit = Math.max(1, ...nodes.map(n => n.commit_count || 0));
  // 크기는 커밋 수의 제곱근(적은 개발자도 보이게). 사람이 많으면 한 사람이 차지할 면적에 맞춰 함께 줄인다.
  const space = Math.sqrt(W * H / nodes.length) / 75;
  const room = Math.max(0.45, Math.min(1, space));           // 노드 크기
  const reach = Math.max(0.45, Math.min(1.8, space));        // 선 길이: 사람이 적으면 넓게 쓴다
  const radius = n => (4 + 11 * Math.sqrt((n.commit_count || 0) / maxCommit)) * room;
  const maxStrength = Math.max(1e-9, ...data.pairs.map(p => p.strength));
  const rel = p => Math.sqrt(p.strength / maxStrength);
  let centers = communityCenters(W, H);
  const centerOf = n => centers.get(n.community_rank ?? -1) || { x: W / 2, y: H / 2 };
  nodes.forEach((n, i) => {
    const old = previous.get(n.id), c = centerOf(n);
    n.x = old?.x ?? c.x + 30 * Math.cos(i * 2.39996) * Math.sqrt(i % 9 + 1) / 3; n.y = old?.y ?? c.y + 30 * Math.sin(i * 2.39996) * Math.sqrt(i % 9 + 1) / 3;
  });
  const links = data.pairs.map(p => Object.assign(p, { source: p.a, target: p.b }));
  const sameCommunity = l => (l.source.community_rank ?? -1) === (l.target.community_rank ?? -1);
  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(l => ((sameCommunity(l) ? 40 : 100) + 30 * (1 - rel(l))) * reach).strength(l => sameCommunity(l) ? 0.2 + 0.45 * rel(l) : 0.03))
    .force('charge', d3.forceManyBody().strength(-Math.max(18, Math.min(220, 7000 / nodes.length))).distanceMax(Math.min(W, H) * 0.5))
    .force('x', d3.forceX(n => centerOf(n).x).strength(0.08))
    .force('y', d3.forceY(n => centerOf(n).y).strength(0.1))
    .force('collide', d3.forceCollide(n => radius(n) + 3 * reach).iterations(2));
  if (state.paused) sim.stop();

  const linkSel = scene.append('g').attr('class', 'net-links').selectAll('line').data(links).join('line')
    .attr('class', 'net-link').attr('stroke-width', p => 1 + 4 * rel(p)).style('stroke-opacity', nodes.length > 100 ? 0.2 : nodes.length > 40 ? 0.4 : 0.6);
  const hitSel = scene.append('g').selectAll('line').data(links).join('line').attr('class', 'net-link-hit')
    .on('mouseenter', (event, p) => { state.hover = { type: 'pair', p }; showPairTip(event, p); })
    .on('mousemove', (event, p) => showPairTip(event, p))
    .on('mouseleave', () => { state.hover = null; hideTip(); })
    .on('click', (_event, p) => selectPair(p));
  const nodeSel = scene.append('g').selectAll('g').data(nodes, n => n.id).join('g').attr('class', 'net-node')
    .attr('tabindex', 0).attr('role', 'button')
    .attr('aria-label', n => `${n.name} · 커밋 ${num(n.commit_count)}개 · 연결 ${num(n.degree)}명${n.is_hub ? ' · 허브' : ''} · 상세 보기`)
    .on('click', (_event, n) => selectNode(n.id))
    .on('keydown', (event, n) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectNode(n.id); } })
    .on('mouseenter', (event, n) => { state.hover = { type: 'node', id: n.id }; showNodeTip(event, n); updateLabels(); })
    .on('mousemove', (event, n) => showNodeTip(event, n))
    .on('mouseleave', () => { state.hover = null; hideTip(); updateLabels(); })
    .call(d3.drag().on('start', (event, n) => { if (!event.active && !state.paused) sim.alphaTarget(0.2).restart(); n.fx = n.x; n.fy = n.y; })
      .on('drag', (event, n) => { n.fx = event.x; n.fy = event.y; if (state.paused) { n.x = event.x; n.y = event.y; tick(); } })
      .on('end', (event, n) => { if (!event.active) sim.alphaTarget(0); n.fx = null; n.fy = null; }));
  nodeSel.append('circle').attr('class', 'net-hit').attr('r', n => Math.max(radius(n) + 4, 12));
  nodeSel.filter(n => n.is_hub).append('circle').attr('class', 'net-hub-ring').attr('r', n => radius(n) + 3.5);
  nodeSel.append('circle').attr('class', 'net-dot').attr('r', radius).style('fill', n => communityColor(n.community_rank));
  nodeSel.filter(n => n.is_hub).append('path').attr('class', 'net-hub-mark')
    .attr('d', n => { const r = radius(n) + 3.5, x = r * 0.72, y = -r * 0.72; return `M${x},${y - 3.5}L${x + 3.5},${y}L${x},${y + 3.5}L${x - 3.5},${y}Z`; });
  const labelSel = nodeSel.append('text').attr('class', 'net-node-label').attr('text-anchor', 'middle').text(n => shortName(n.name));

  const commitRank = new Map([...nodes].sort((a, b) => (b.commit_count || 0) - (a.commit_count || 0) || a.id - b.id).map((n, i) => [n.id, i]));
  const zoom = d3.zoom().scaleExtent([0.3, 6]).on('zoom', event => { scene.attr('transform', event.transform); updateLabels(); });
  svg.call(zoom).on('dblclick.zoom', null);
  svg.on('click', event => { if (event.target === svgEl) clearSelection(); });

  let ticks = 0;
  function tick() {
    const pad = 14;
    nodes.forEach(n => { n.x = Math.max(pad, Math.min(graph.W - pad, n.x)); n.y = Math.max(pad, Math.min(graph.H - pad, n.y)); });
    const ends = l => {
      const dx = l.target.x - l.source.x, dy = l.target.y - l.source.y, len = Math.hypot(dx, dy) || 1;
      const s = radius(l.source) + 2, t = radius(l.target) + 2;
      return [l.source.x + dx / len * s, l.source.y + dy / len * s, l.target.x - dx / len * t, l.target.y - dy / len * t];
    };
    for (const sel of [linkSel, hitSel]) sel.each(function (l) { const [x1, y1, x2, y2] = ends(l); this.setAttribute('x1', x1); this.setAttribute('y1', y1); this.setAttribute('x2', x2); this.setAttribute('y2', y2); });
    nodeSel.attr('transform', n => `translate(${n.x},${n.y})`);
    if (++ticks % 20 === 0) updateLabels();
  }
  sim.on('tick', tick).on('end', updateLabels);

  graph = { sim, svg, scene, zoom, nodes, links, nodeSel, linkSel, hitSel, labelSel, radius, commitRank, W, H, tick };
  svg.call(zoom.transform, keepTransform);
  tick();

  // 창·패널 크기 변경: 좌표계와 커뮤니티 중심만 다시 잡고 위치·확대는 유지한다.
  graph.resize = new ResizeObserver(() => {
    const size = measure();
    if (!graph || (Math.abs(size.W - graph.W) < 3 && Math.abs(size.H - graph.H) < 3)) return;
    const sx = size.W / graph.W, sy = size.H / graph.H;
    nodes.forEach(n => { n.x *= sx; n.y *= sy; });
    graph.W = size.W; graph.H = size.H; svg.attr('viewBox', `0 0 ${size.W} ${size.H}`);
    centers = communityCenters(size.W, size.H);
    sim.force('x').x(n => centerOf(n).x); sim.force('y').y(n => centerOf(n).y);
    tick();
    if (!state.paused) sim.alpha(Math.max(sim.alpha(), 0.08)).restart();
  });
  graph.resize.observe(svgEl);
}

/* 라벨: 선택·이웃·마우스·허브·커밋 상위 10명 순으로, 화면에서 겹치지 않는 것만 쓴다. 1.8배 이상 확대하면 모두 후보. */
function updateLabels() {
  if (!graph) return;
  const t = d3.zoomTransform(svgEl), k = t.k;
  const near = neighborSet();
  const priority = n => {
    if (state.selected?.type === 'node' && state.selected.id === n.id) return 1000;
    if (state.hover?.type === 'node' && state.hover.id === n.id) return 950;
    if (state.selected?.type === 'pair' && (state.selected.p.a === n.id || state.selected.p.b === n.id)) return 900;
    if (near && near.has(n.id)) return 800;
    if (n.is_hub) return 500;
    const rank = graph.commitRank.get(n.id);
    if (rank < LABEL_TOP) return 400 - rank;
    return k >= LABEL_ZOOM ? 100 - Math.min(99, rank) / 100 : 0;
  };
  const scale = svgEl.clientWidth ? svgEl.clientWidth / graph.W : 1;
  // 화면 픽셀 기준으로 아래 → 위 → 오른쪽 → 왼쪽 순서로 놓아 보고, 겹치지 않는 첫 자리를 쓴다.
  const placed = graph.nodes.map(n => { const x = t.applyX(n.x) * scale, y = t.applyY(n.y) * scale, r = graph.radius(n) * k * scale; return [x - r, y - r, x + r, y + r]; });
  const hits = box => placed.some(b => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1]);
  const spot = new Map();
  [...graph.nodes].map(n => [n, priority(n)]).filter(([, p]) => p > 0).sort((a, b) => b[1] - a[1]).forEach(([n]) => {
    const text = shortName(n.name);
    const w = Array.from(text).reduce((sum, ch) => sum + (/[^ -~]/.test(ch) ? 11 : 6.4), 2), h = 13;
    const x = t.applyX(n.x) * scale, y = t.applyY(n.y) * scale, r = graph.radius(n) * k * scale;
    const options = [
      ['below', [x - w / 2, y + r + 2, x + w / 2, y + r + 2 + h]],
      ['above', [x - w / 2, y - r - 2 - h, x + w / 2, y - r - 2]],
      ['right', [x + r + 3, y - h / 2, x + r + 3 + w, y + h / 2]],
      ['left', [x - r - 3 - w, y - h / 2, x - r - 3, y + h / 2]],
    ];
    const free = options.find(([, box]) => !hits(box));
    if (!free) return;
    placed.push(free[1]); spot.set(n.id, free[0]);
  });
  const unit = 1 / (k * scale);
  graph.labelSel.attr('display', n => spot.has(n.id) ? null : 'none')
    .style('font-size', `${11 * unit}px`).style('stroke-width', `${3 * unit}px`)
    .attr('text-anchor', n => ({ right: 'start', left: 'end' })[spot.get(n.id)] || 'middle')
    .attr('dominant-baseline', 'central')
    .attr('x', n => { const r = graph.radius(n); return spot.get(n.id) === 'right' ? r + 3 * unit : spot.get(n.id) === 'left' ? -r - 3 * unit : 0; })
    .attr('y', n => { const r = graph.radius(n); return spot.get(n.id) === 'above' ? -r - 8.5 * unit : spot.get(n.id) === 'below' ? r + 8.5 * unit : 0; });
}

function neighborSet() {
  if (state.selected?.type !== 'node' || !data) return null;
  const id = state.selected.id, set = new Set([id]);
  visiblePairs().forEach(p => { if (p.a === id) set.add(p.b); if (p.b === id) set.add(p.a); });
  return set;
}

function restyle() {
  if (!graph) return;
  const near = neighborSet();
  const sel = state.selected;
  const pairOn = p => sel?.type === 'pair' ? p === sel.p : sel?.type === 'node' ? (p.a === sel.id || p.b === sel.id) : true;
  const nodeOn = n => sel?.type === 'pair' ? (n.id === sel.p.a || n.id === sel.p.b) : near ? near.has(n.id) : true;
  graph.nodeSel.classed('net-dim', n => !nodeOn(n)).classed('net-selected', n => sel?.type === 'node' && n.id === sel.id)
    .attr('aria-pressed', n => String(sel?.type === 'node' && n.id === sel.id));
  graph.linkSel.classed('net-dim', p => !pairOn(p)).classed('net-active', p => !!sel && pairOn(p));
  updateLabels();
}

function selectNode(id) {
  state.selected = { type: 'node', id };
  restyle();
  const node = data.byId.get(id) || data.isolated.find(x => x.id === id);
  if (node) void showNodeDetail(node);
}

function selectPair(p) {
  state.selected = { type: 'pair', p };
  restyle(); showPairDetail(p);
}

function clearSelection() {
  if (!state.selected) return;
  state.selected = null; restyle(); showSummary();
}

function bindSideKeys() {
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && state.selected && !document.querySelector('#drilldown-modal.open')) clearSelection(); });
}

/* ── 툴팁 ── */
const tip = () => document.getElementById('net-tooltip');
function placeTip(event, html) {
  const box = tip(), wrap = document.getElementById('net-graph-wrap');
  if (!box || !wrap) return;
  box.innerHTML = html; box.hidden = false;
  const r = wrap.getBoundingClientRect();
  const x = Math.min(r.width - box.offsetWidth - 8, event.clientX - r.left + 14), y = Math.min(r.height - box.offsetHeight - 8, event.clientY - r.top + 14);
  box.style.left = `${Math.max(8, x)}px`; box.style.top = `${Math.max(8, y)}px`;
}
function hideTip() { const box = tip(); if (box) box.hidden = true; }
function showNodeTip(event, n) {
  placeTip(event, `<b>${esc(n.name)}</b>${n.is_hub ? ' <span class="net-tag">허브</span>' : ''}
    <div>커밋 ${count(n.commit_count, '개')} · 병합 PR ${count(n.pr_count, '개')}</div>
    <div>연결된 개발자 ${count(n.degree, '명')}</div>
    ${n.is_hub ? '<div class="net-tip-note">다른 개발자들 사이를 잇는 위치(매개 중심성 상위 15%)입니다.</div>' : ''}`);
}
function relationText(p) {
  return p.co_edit && p.review ? '공동 수정 + 리뷰' : p.co_edit ? '공동 수정' : '리뷰';
}
function strengthLevel(p) {
  const c = data?.cutoffs || {};
  if (c.strong != null && p.strength >= c.strong) return '강함(상위 30%)';
  if (c.medium != null && p.strength >= c.medium) return '중간';
  if (c.no_weak != null && p.strength >= c.no_weak) return '보통';
  return '약함(하위 30%)';
}
function reviewText(p) {
  const parts = [];
  if (p.review_a_to_b > 0) parts.push(`${esc(p.a_name)} → ${esc(p.b_name)} ${p.reviews_a_to_b != null ? `${num(p.reviews_a_to_b)}회` : ''}`);
  if (p.review_b_to_a > 0) parts.push(`${esc(p.b_name)} → ${esc(p.a_name)} ${p.reviews_b_to_a != null ? `${num(p.reviews_b_to_a)}회` : ''}`);
  return parts.join(' · ');
}
function showPairTip(event, p) {
  placeTip(event, `<b>${esc(p.a_name)} ↔ ${esc(p.b_name)}</b>
    <div>${relationText(p)} · 강도 ${strengthLevel(p)}</div>
    ${p.co_edit ? `<div>함께 수정한 PR ${count(p.co_pr_count, '개')}</div>` : ''}
    ${p.review ? `<div>리뷰 ${reviewText(p)}</div>` : ''}`);
}

/* ── 범례 ── */
function renderLegend() {
  const box = document.getElementById('net-legend');
  if (!box) return;
  const shown = data.communities.length ? data.communities : [...new Set(data.nodes.filter(n => n.community_rank != null).map(n => n.community_rank))].map(rank => ({ rank, size: null }));
  const main = shown.filter(c => c.rank < MAIN_COMMUNITIES);
  const others = shown.filter(c => c.rank >= MAIN_COMMUNITIES);
  const sw = color => `<span class="net-swatch" style="background:${color}"></span>`;
  box.innerHTML = `
    ${main.map(c => `<span class="net-leg">${sw(communityColor(c.rank))}커뮤니티 ${c.rank + 1}${c.size ? ` · ${num(c.size)}명` : ''}</span>`).join('')}
    ${others.length ? `<span class="net-leg">${sw('var(--chart-6)')}기타 커뮤니티 ${num(others.length)}개</span>` : ''}
    <span class="net-leg"><svg width="26" height="10" aria-hidden="true"><line x1="1" y1="5" x2="25" y2="5" class="net-link" stroke-width="2.5"/></svg>공동 수정(굵을수록 강함)</span>
    ${data.reviewAvailable ? '<span class="net-leg"><svg width="26" height="10" aria-hidden="true"><line x1="1" y1="5" x2="19" y2="5" class="net-link" stroke-width="2" stroke-dasharray="5 4"/><path d="M18,1L25,5L18,9Z" class="net-arrow-head"/></svg>리뷰(리뷰어 → 작성자)</span>' : '<span class="net-leg net-muted">리뷰 기록 없음 — 리뷰 관계는 그리지 않습니다</span>'}
    <span class="net-leg"><svg width="18" height="18" aria-hidden="true"><circle cx="9" cy="9" r="5" class="net-dot-sample"/><circle cx="9" cy="9" r="8" class="net-hub-ring"/></svg>허브(테두리·◆)</span>
    <span class="net-leg"><svg width="18" height="18" aria-hidden="true"><circle cx="9" cy="9" r="6" class="net-unlinked-sample"/></svg>필터 후 화면 안 연결 없음</span>
    <span class="net-leg net-muted">노드 크기 = 커밋 수</span>`;
}

function renderNodeList() {
  const list = document.getElementById('net-node-list'), search = document.getElementById('net-search');
  if (!list || !search) return;
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    list.replaceChildren(...data.nodes.filter(n => String(n.name || '익명').toLowerCase().includes(q)).map(n => {
      const b = action(n.name, () => selectNode(n.id)); b.title = `${n.name} · 커밋 ${num(n.commit_count)}개`; return b;
    }));
  };
  search.oninput = draw; draw();
}

function renderIsolated() {
  const box = document.getElementById('net-isolated'), list = document.getElementById('net-isolated-list');
  if (!box || !list) return;
  box.hidden = !data.isolated.length;
  setText('net-isolated-summary', `연결이 발견되지 않은 개발자 ${num(data.project.isolated_count ?? data.isolated.length)}명`);
  list.innerHTML = `<p class="p-note">수집된 PR·리뷰 기록과 분석 기준에서 다른 개발자와의 연결이 발견되지 않았다는 뜻입니다. 실제로 협업하지 않았다는 뜻은 아닙니다(예: PR 없이 직접 push, 리뷰 기록 미수집). 그래프에는 그리지 않습니다.</p>`;
  const rows = element('div', 'net-isolated-rows');
  data.isolated.forEach(x => {
    const b = action('', () => selectNode(x.id), 'net-isolated-row');
    b.innerHTML = `<span>${esc(x.name)}</span><span>커밋 ${count(x.commit_count, '개')}</span><span>병합 PR ${count(x.pr_count, '개')}</span><span class="net-tag muted">연결 없음</span>`;
    rows.append(b);
  });
  list.append(rows);
}

function renderEmptyNote() {
  const box = document.getElementById('net-empty-note');
  if (!box) return;
  if (data.nodes.length && data.pairs.length) { box.innerHTML = ''; return; }
  const { parts } = collectionHint(data.dataQuality);
  box.innerHTML = `<div class="cx-fallback"><b>협업 관계 0개</b> — 협업이 없다는 뜻이 아닙니다. 공동 수정은 PR 파일 상세와 PR↔커밋 연결로, 리뷰는 리뷰 기록으로 찾기 때문에, PR 없이 직접 push하는 저장소나 수집 범위가 부족한 경우에도 0이 나옵니다. ${parts.length ? `현재 수집 범위: ${esc(parts.join(' · '))}.` : ''}</div>`;
}

/* 협업 연결 0개를 '협업이 없다'로 읽지 않게 한다. */
function collectionHint(dq) {
  dq = dq || {};
  const a = assessDataQuality(dq);
  const parts = [];
  if (dq.merged_pr_count === 0) parts.push('관측 구간에 병합된 PR이 없습니다');
  else if (dq.merged_pr_count == null) parts.push('병합 PR 수를 확인할 수 없습니다');
  else {
    if (dq.prs_with_files == null) parts.push('PR 파일 상세 수는 확인할 수 없습니다');
    else if (dq.prs_with_files < dq.merged_pr_count) parts.push(`병합 PR ${num(dq.merged_pr_count)}개 중 파일 상세가 수집된 PR은 ${num(dq.prs_with_files)}개입니다`);
    if (dq.prs_with_linked_commits == null) parts.push('PR↔커밋 연결 수는 확인할 수 없습니다');
    else if (dq.prs_with_linked_commits < dq.merged_pr_count) parts.push(`PR↔커밋 연결은 ${num(dq.prs_with_linked_commits)}/${num(dq.merged_pr_count)}개입니다`);
  }
  return { parts, partial: a.collection === 'partial' };
}

/* ── 오른쪽 상세 패널 ── */
const side = () => document.getElementById('net-side');
const stat = (label, value, title = '') => `<div class="net-stat"${title ? ` title="${esc(title)}"` : ''}><span>${esc(label)}</span><b>${value}</b></div>`;
const prList = (prs, emptyText) => prs == null
  ? `<p class="p-note">${esc(emptyText)}</p>`
  : prs.length ? `<ul class="net-pr-list">${prs.map(pr => `<li><span class="net-pr-no">#${esc(pr.number)}</span> ${esc(pr.title || '')}</li>`).join('')}</ul>` : '<p class="p-note">해당하는 PR이 없습니다.</p>';
const NO_DETAILS = '이 결과에는 PR 목록이 없습니다. 다시 분석하면 표시됩니다.';

function showSummary() {
  const box = side();
  if (!box || !data) return;
  detailGeneration++;
  const hubs = data.nodes.filter(n => n.is_hub).sort((a, b) => (b.betweenness_centrality || 0) - (a.betweenness_centrality || 0));
  const notes = [];
  if (!data.detailsAvailable) notes.push('이 결과는 쌍별 공동 PR 수·리뷰 횟수·PR 목록을 저장하기 전에 만들어졌습니다. 다시 분석하면 표시됩니다.');
  if (!data.reviewAvailable) notes.push('리뷰 기록이 없어 리뷰 관계는 그리지 않습니다.');
  if (data.selection.pr_participation_available === false && !data.legacy) notes.push('이 결과에는 PR 참여 수가 없어 커밋·협업 강도 기준으로만 개발자를 골랐습니다.');
  if (data.largePrRule) notes.push(data.largePrRule + '합니다.');
  box.innerHTML = `
    <div class="net-side-head"><div class="net-side-title">프로젝트 요약</div><div class="net-side-sub">개발자나 선을 선택하면 상세가 나옵니다</div></div>
    <div class="net-stats">
      ${stat('협업 관계', count(data.project.total_relations, '개'), '저장소 전체')}
      ${stat('커뮤니티', count(data.project.community_count, '개'), '연결된 개발자 무리(Louvain)')}
      ${stat('허브', count(data.project.hub_count ?? hubs.length, '명'), '매개 중심성 상위 15%')}
      ${stat('연결 없음', count(data.project.isolated_count, '명'))}
    </div>
    <div class="net-sec">커뮤니티</div>
    ${data.communities.length ? `<ul class="net-plain">${data.communities.slice(0, 8).map(c => `<li><span class="net-swatch" style="background:${communityColor(c.rank)}"></span>커뮤니티 ${c.rank + 1} · ${num(c.size)}명 <span class="net-muted">(화면 ${num(c.shown)}명)</span></li>`).join('')}${data.communities.length > 8 ? `<li class="net-muted">외 ${num(data.communities.length - 8)}개</li>` : ''}</ul>` : '<p class="p-note">커뮤니티를 이룬 연결이 없습니다.</p>'}
    <div class="net-sec">화면의 허브</div>
    ${hubs.length ? `<div class="net-chips">${hubs.slice(0, 8).map(n => `<button type="button" class="net-chip" data-node="${n.id}">${esc(n.name)}</button>`).join('')}</div><p class="p-note">허브는 다른 개발자들 사이를 잇는 위치라는 뜻이며, 리더나 핵심 개발자를 뜻하지 않습니다.</p>` : '<p class="p-note">화면에 허브가 없습니다.</p>'}
    ${notes.length ? `<div class="net-sec">데이터 기준</div><ul class="net-notes">${notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}`;
  box.querySelectorAll('[data-node]').forEach(b => b.addEventListener('click', () => selectNode(Number(b.dataset.node))));
}

function sideHeader(title, sub) {
  return `<div class="net-side-head"><button type="button" class="net-back" data-back>← 요약</button><div class="net-side-title">${title}</div>${sub ? `<div class="net-side-sub">${sub}</div>` : ''}</div>`;
}
function bindBack(box) { box.querySelector('[data-back]')?.addEventListener('click', clearSelection); }

async function showNodeDetail(node) {
  const box = side();
  if (!box) return;
  const generation = ++detailGeneration;
  const community = node.community_rank != null ? `<span class="net-swatch" style="background:${communityColor(node.community_rank)}"></span>커뮤니티 ${node.community_rank + 1} · ${num(node.community_size)}명` : '커뮤니티 없음(연결 없음)';
  const base = `${sideHeader(esc(node.name), `${community}${node.is_hub ? ' · <span class="net-tag">허브</span>' : ''}`)}
    <div class="net-stats">
      ${stat('커밋', count(node.commit_count, '개'))}
      ${stat('병합 PR', count(node.pr_count, '개'), node.pr_count == null ? '이 결과에는 PR 수가 없습니다' : '관측 구간에 병합된, 이 개발자가 작성한 PR')}
      ${stat('리뷰한 PR', count(node.reviews_given, '개'))}
      ${stat('협업 연결', count(node.degree, '명'), '저장소 전체에서 연결된 개발자 수')}
      ${stat('연결 중심성', fmt3(node.degree_centrality), 'Degree centrality: 연결된 개발자 수 ÷ (전체 개발자 − 1)')}
      ${stat('매개 중심성', fmt3(node.betweenness_centrality), 'Betweenness centrality: 다른 개발자들 사이의 최단 경로에 놓이는 정도')}
    </div>
    ${node.is_hub ? '<p class="p-note">허브: 매개 중심성 상위 15%. 다른 개발자들 사이를 잇는 위치라는 뜻이며 리더·핵심 개발자를 뜻하지 않습니다.</p>' : ''}
    ${node.selected_by?.length ? `<p class="p-note">화면에 표시된 이유: ${node.selected_by.map(c => ({ commits: '커밋 수 상위', prs: 'PR 참여 상위', collaboration: '협업 강도 상위' })[c]).join(', ')}</p>` : ''}`;
  box.innerHTML = `${base}<div class="net-sec">주요 협업자</div><p class="p-note">불러오는 중…</p>`;
  bindBack(box);
  let collab = null;
  try { collab = await fetchContributorCollaborators(getRepoId(), node.id, 5); } catch (error) { collab = { error: error.message }; }
  if (generation !== detailGeneration) return;
  const people = collab?.collaborators_detail || (collab?.collaborators || []).map(c => ({ contributor: c.contributor, strength: c.edge_weight, co_pr_count: null }));
  const top = people.slice(0, 5);
  box.innerHTML = `${base}
    <div class="net-sec">주요 협업자 TOP 5</div>
    ${collab?.error ? `<p class="p-note">불러오지 못했습니다: ${esc(collab.error)}</p>` : top.length ? `<ul class="net-plain">${top.map(c => `<li><button type="button" class="ui-list-link" data-node="${c.contributor.id}">${esc(c.contributor.name)}</button> <span class="net-muted">${c.co_pr_count != null ? `공동 PR ${num(c.co_pr_count)}개` : ''}${c.reviews_out ? ` · 리뷰함 ${num(c.reviews_out)}회` : ''}${c.reviews_in ? ` · 리뷰받음 ${num(c.reviews_in)}회` : ''}</span></li>`).join('')}</ul>` : '<p class="p-note">수집된 기록에서 연결된 개발자가 없습니다.</p>'}
    <div class="net-sec">공동 참여 PR</div>
    ${collab?.error ? '' : prList(collab?.shared_prs ?? null, NO_DETAILS)}
    <button type="button" class="btn-secondary net-more" data-more>파일 지분 등 전체 상세</button>`;
  bindBack(box);
  box.querySelectorAll('[data-node]').forEach(b => b.addEventListener('click', () => {
    const id = Number(b.dataset.node);
    if (data.byId.has(id)) selectNode(id);
  }));
  box.querySelector('[data-more]')?.addEventListener('click', () => window.openContributorDrilldown?.(node.id));
}

function showPairDetail(p) {
  const box = side();
  if (!box) return;
  detailGeneration++;
  box.innerHTML = `${sideHeader(`${esc(p.a_name)} ↔ ${esc(p.b_name)}`, relationText(p))}
    <div class="net-stats">
      ${stat('협업 강도', `${fmt3(p.strength)} <small>${strengthLevel(p)}</small>`, '공동 수정 0.6 + 방향별 리뷰 0.4. 저장소 안에서만 비교할 수 있는 상대 값')}
      ${stat('함께 수정한 PR', p.co_edit ? count(p.co_pr_count, '개') : '0개')}
      ${stat('함께 수정한 파일', p.co_edit ? count(p.co_edit_files, '건') : '0건', '같은 PR에서 둘 다 고친 (PR, 파일) 수')}
      ${stat('공동 수정 점수', fmt3(p.co_edit_score ?? (p.co_edit_raw != null ? 0.6 * p.co_edit_raw : null)))}
    </div>
    <div class="net-sec">리뷰</div>
    ${p.review ? `<ul class="net-plain">
      ${p.review_a_to_b > 0 ? `<li>${esc(p.a_name)} → ${esc(p.b_name)} ${p.reviews_a_to_b != null ? `${num(p.reviews_a_to_b)}회` : ''} <span class="net-muted">점수 ${fmt3(0.4 * p.review_a_to_b)}</span></li>` : ''}
      ${p.review_b_to_a > 0 ? `<li>${esc(p.b_name)} → ${esc(p.a_name)} ${p.reviews_b_to_a != null ? `${num(p.reviews_b_to_a)}회` : ''} <span class="net-muted">점수 ${fmt3(0.4 * p.review_b_to_a)}</span></li>` : ''}</ul>`
      : `<p class="p-note">${data?.reviewAvailable ? '두 사람 사이의 리뷰 기록이 없습니다.' : '이 저장소는 리뷰 기록이 수집되지 않았습니다.'}</p>`}
    <div class="net-sec">관련 PR</div>
    ${prList(p.prs ?? null, NO_DETAILS)}
    <div class="net-sec">개발자 상세</div>
    <div class="net-chips"><button type="button" class="net-chip" data-person="${p.a}">${esc(p.a_name)}</button><button type="button" class="net-chip" data-person="${p.b}">${esc(p.b_name)}</button></div>`;
  bindBack(box);
  box.querySelectorAll('[data-person]').forEach(b => b.addEventListener('click', () => {
    const id = Number(b.dataset.person);
    if (data?.byId.has(id)) selectNode(id); else window.openContributorDrilldown?.(id);
  }));
}

/* ── 하단: 주요 협업 쌍(저장소 전체) ── */
function renderTopPairs(pairs) {
  const wrap = document.getElementById('net-top-pairs');
  if (!wrap) return;
  if (!pairs.length) {
    const { parts } = collectionHint(data?.dataQuality);
    wrap.innerHTML = `<div class="c-meta" style="padding:4px;">협업 쌍이 없습니다. 협업이 없었다는 뜻이 아니라 PR 상세·리뷰 수집 범위가 부족한 경우도 포함합니다${parts.length ? ` (${esc(parts.join(' · '))})` : ''}.</div>`;
    return;
  }
  const max = Math.max(1e-9, ...pairs.map(p => p.edge_weight || 0));
  wrap.innerHTML = `<div class="net-bars">${pairs.map((p, i) => {
    const kinds = [p.co_edit_raw > 0 ? '공동 수정' : null, (p.review_directed_a_to_b > 0 || p.review_directed_b_to_a > 0) ? '리뷰' : null].filter(Boolean).join(' + ') || '관계';
    return `<button type="button" class="net-bar-row" data-pair="${i}" title="${esc(p.contributor_a.name)} ↔ ${esc(p.contributor_b.name)} · ${kinds} · 강도 ${fmt3(p.edge_weight)}">
      <span class="net-bar-name">${esc(p.contributor_a.name)} ↔ ${esc(p.contributor_b.name)}</span>
      <span class="net-bar-track"><span class="net-bar-fill" style="width:${Math.max(2, 100 * (p.edge_weight || 0) / max)}%"></span></span>
      <span class="net-bar-value">${fmt3(p.edge_weight)} <small>${kinds}</small></span></button>`;
  }).join('')}</div>`;
  wrap.querySelectorAll('[data-pair]').forEach(b => b.addEventListener('click', () => {
    const p = pairs[Number(b.dataset.pair)];
    const a = Math.min(p.contributor_a.id, p.contributor_b.id), z = Math.max(p.contributor_a.id, p.contributor_b.id);
    const inGraph = data?.pairs.find(x => x.a === a && x.b === z);
    if (inGraph) { selectPair(inGraph); document.getElementById('net-panel')?.scrollIntoView({ block: 'start' }); return; }
    const flip = p.contributor_a.id !== a;
    showPairDetail({ a, b: z, a_name: flip ? p.contributor_b.name : p.contributor_a.name, b_name: flip ? p.contributor_a.name : p.contributor_b.name,
      strength: p.edge_weight, co_edit: p.co_edit_raw > 0, co_edit_raw: p.co_edit_raw, review_a_to_b: flip ? p.review_directed_b_to_a : p.review_directed_a_to_b,
      review_b_to_a: flip ? p.review_directed_a_to_b : p.review_directed_b_to_a, review: (p.review_directed_a_to_b || 0) + (p.review_directed_b_to_a || 0) > 0,
      co_pr_count: null, co_edit_files: null, reviews_a_to_b: null, reviews_b_to_a: null, prs: null });
  }));
}
