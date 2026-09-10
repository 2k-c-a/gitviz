/* ── Bus Factor 트리맵 (목업 데이터 뼈대) ──
   tm-svg가 있을 때(=트리맵 페이지)만 동작한다. 실제 백엔드 엔드포인트가 아직 없어
   디렉토리 구조 + 파일별 bus_factor/entropy를 흉내 낸 목업 트리를 그대로 렌더링한다.
   API가 정해지면 MOCK_TREE 자리만 fetch 결과로 교체하면 되도록 트리 모양(name/children/
   value/bus_factor/entropy)을 실제 계약 후보와 최대한 비슷하게 맞춰뒀다. */

const svgEl = document.getElementById('tm-svg');

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const BF_COLORS = { 1: '#E24B4A', 2: '#EF9F27', 3: '#378ADD', 4: '#1D9E75', 5: '#639922' };
const bfColor = (bf) => bf == null ? '#B4B2A9' : BF_COLORS[Math.min(5, Math.max(1, Math.round(bf)))];

/* ── 목업 트리 (실제 저장소 구조를 본떠 만든 가짜 데이터) ── */
const file = (name, bus_factor, entropy, value = 1) => ({ name, value, bus_factor, entropy, file_id: null });

const MOCK_TREE = {
  name: 'root',
  children: [
    {
      name: 'app', children: [
        { name: 'api', children: [
          file('analytics.py', 2, 0.85), file('repositories.py', 1, 0.31), file('iot.py', 1, 0.12),
        ]},
        { name: 'models', children: [
          file('commit.py', 1, 0.28), file('repository.py', 1, 0.33), file('pull_request.py', 1, 0.19),
          file('contributor.py', 1, 0.22), file('file.py', 2, 0.61),
        ]},
        { name: 'collectors', children: [
          { name: 'git', children: [
            file('commit_collector.py', 2, 0.71), file('commit_classifier.py', 1, 0.14),
            file('file_collector.py', 1, 0.09), file('clone_manager.py', 1, 0.18),
          ]},
          { name: 'github', children: [
            file('github_client.py', 3, 1.22), file('graphql_pr_collector.py', 2, 0.66),
            file('pr_collector.py', 1, 0.24), file('release_collector.py', 1, 0.11),
          ]},
          { name: 'identity', children: [ file('resolver.py', 2, 0.58) ] },
        ]},
        { name: 'tasks', children: [
          file('analyze_repository.py', 4, 1.85), file('progress.py', 2, 0.44),
        ]},
      ],
    },
    {
      name: 'frontend', children: [
        { name: 'js', children: [
          file('api.js', 3, 0.92), file('main.js', 4, 1.41), file('timeline.js', 2, 0.53),
          file('risk.js', 2, 0.47), file('busfactor.js', 1, 0.08), file('coupling.js', 1, 0.08),
          file('network.js', 1, 0.08), file('drilldown.js', 2, 0.51), file('modal.js', 3, 1.05),
          file('routing.js', 1, 0.15), file('utils.js', 2, 0.39), file('login.js', 1, 0.21),
        ]},
        { name: 'css', children: [
          file('root.css', 3, 0.88), file('main.css', 2, 0.62), file('index.css', 2, 0.57),
          file('intro.css', 1, 0.11), file('bar.css', 1, 0.06),
        ]},
        { name: 'pages', children: [
          file('timeline.html', 2, 0.41), file('risk.html', 1, 0.19), file('coupling.html', 1, 0.22),
          file('network.html', 1, 0.22), file('busfactor.html', 1, 0.24), file('developers.html', 1, 0.05),
        ]},
      ],
    },
    {
      name: 'migrations/versions', children: [
        file('1f96eac1ec08_add_commit_file_changes.py', 1, 0.05),
        file('393e33b2dcbb_create_files_table.py', 1, 0.05),
        file('b5c6d7e8f9a0_add_github_api_tables.py', 1, 0.05),
        file('f9a0b1c2d3e4_add_unique_constraint.py', 1, 0.05),
      ],
    },
    {
      name: 'scripts', children: [
        file('backfill_commit_type.py', 1, 0.04), file('cleanup_duplicates.py', 1, 0.04),
        file('migrate_schema_v2.py', 1, 0.04), file('run_identity_resolution.py', 1, 0.04),
        file('show_summary.py', 1, 0.04), file('verify_schema_v2.py', 1, 0.04),
      ],
    },
  ],
};

/* 디렉토리 노드의 avgBF를 자식들의 평균으로 역산(post-order) — 실제 API에선 서버가 내려줄 값이지만
   목업이라 여기서 계산해서 채워 넣는다. */
function computeAvgBF(node) {
  if (!node.children) {
    node.avgBF = node.bus_factor;
    return node.avgBF;
  }
  const vals = node.children.map(computeAvgBF).filter(v => v != null);
  node.avgBF = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  return node.avgBF;
}
computeAvgBF(MOCK_TREE);

let path = [MOCK_TREE]; // 브레드크럼 스택 — path[path.length-1]이 현재 보고 있는 디렉토리

function initTreemapPage() {
  renderLegend();
  render();
  window.addEventListener('resize', render);
}

function renderLegend() {
  const legend = document.getElementById('tm-legend');
  if (!legend) return;
  const items = [
    ['1 (위험)', BF_COLORS[1]], ['2', BF_COLORS[2]], ['3', BF_COLORS[3]], ['4', BF_COLORS[4]], ['5+ (안전)', BF_COLORS[5]],
  ];
  legend.innerHTML = items.map(([label, color]) =>
    `<div class="leg-item"><div class="leg-dot" style="background:${color};"></div>BF ${label}</div>`
  ).join('');
}

function renderBreadcrumb() {
  const wrap = document.getElementById('tm-breadcrumb');
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
      render();
    });
  });
}

function render() {
  renderBreadcrumb();

  const current = path[path.length - 1];
  const width = svgEl.clientWidth || 900;
  const height = 420;
  const svg = d3.select(svgEl).attr('viewBox', `0 0 ${width} ${height}`);
  svg.selectAll('*').remove();

  if (!current.children || !current.children.length) {
    svg.append('text').attr('x', width / 2).attr('y', height / 2).attr('text-anchor', 'middle')
      .style('fill', 'var(--text3)').style('font-size', '12px').text('빈 디렉토리입니다.');
    return;
  }

  const root = d3.hierarchy({ ...current, children: current.children })
    .sum(d => d.children ? 0 : (d.value || 1))
    .sort((a, b) => b.value - a.value);

  d3.treemap().size([width, height]).paddingInner(2).round(true)(root);

  const cell = svg.selectAll('g').data(root.children).join('g')
    .attr('transform', d => `translate(${d.x0},${d.y0})`)
    .style('cursor', 'pointer')
    .on('click', (_, d) => {
      if (d.data.children) {
        path = [...path, d.data];
        render();
      } else if (d.data.file_id != null && window.openFileDrilldown) {
        window.openFileDrilldown(d.data.file_id);
      }
    });

  cell.append('rect')
    .attr('width', d => Math.max(0, d.x1 - d.x0))
    .attr('height', d => Math.max(0, d.y1 - d.y0))
    .style('fill', d => bfColor(d.data.avgBF))
    .style('fill-opacity', d => d.data.children ? 0.55 : 0.85)
    .style('stroke', 'var(--bg)')
    .style('stroke-width', 1.5);

  cell.append('title')
    .text(d => `${d.data.name}${d.data.children ? ` (파일 ${d.leaves().length}개)` : ''} · BF ${d.data.avgBF != null ? d.data.avgBF.toFixed(1) : '—'}${d.data.entropy != null ? ` · Entropy ${d.data.entropy.toFixed(2)}` : ''}`);

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
}

if (svgEl && typeof d3 !== 'undefined') {
  initTreemapPage();
}
