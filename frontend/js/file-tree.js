/* ── 파일 분석의 디렉토리 트리: 탐색기형 들여쓰기 트리 ──
   한 줄에 폴더나 파일 하나를 두고 숫자를 바로 적는다(화면 이동·확대 없음).
   보이는 줄만 그려서 파일이 많은 저장소에서도 모두 펼칠 수 있다.
   모델(buildTreeModel·visibleRows)은 DOM 없이 동작해 테스트에서 바로 부른다.
   점수가 없는 파일(null)은 0으로 바꾸지 않는다 — 평균·합계에서 빼고 '—'로 표시한다. */
import { esc, num, DASH } from './fmt.js';

const ROW_H = 32;     // 줄 높이(px) — 보이는 줄 계산과 CSS(.ft-row)가 함께 쓴다
const OVERSCAN = 8;   // 화면 위아래로 미리 그려 두는 줄 수

/* /tree 계층(computeAverages를 거친 것)을 화면용 노드로 바꾼다.
   폴더마다 하위 파일 수·임계값 이상 파일 수·수정량 합·버그 수정 합을 센다. */
export function buildTreeModel(tree, { scoreOf, threshold }) {
  let seq = 0;
  let maxHigh = 0;
  const walk = (src, parent) => {
    const dir = Array.isArray(src.children);
    const name = parent ? String(src.name ?? '') : '';
    const path = !parent ? '' : dir ? (parent.path ? `${parent.path}/${name}` : name) : (src.path || (parent.path ? `${parent.path}/${name}` : name));
    const node = { id: seq++, name, path, dir, depth: parent ? parent.depth + 1 : -1, parent, data: src, children: null };
    if (!dir) {
      const score = scoreOf(src);
      node.score = score;
      node.fileCount = 1;
      node.high = score != null && score >= threshold ? 1 : 0;
      node.churn = src.churn ?? null;
      node.bugfix = src.bugfix_count ?? null;
      return node;
    }
    node.children = src.children.map(child => walk(child, node));
    // 하위가 폴더 하나뿐이면 한 줄로 합친다(VS Code의 압축 폴더처럼 'main/java/org').
    while (parent && node.children.length === 1 && node.children[0].dir) {
      const only = node.children[0];
      node.name = `${node.name}/${only.name}`;
      node.path = only.path;
      node.data = only.data;
      node.children = only.children;
      node.children.forEach(c => { c.parent = node; });
    }
    const sum = (key) => {
      const values = node.children.map(c => c[key]).filter(v => v != null);
      return values.length ? values.reduce((a, b) => a + b, 0) : null;
    };
    node.fileCount = node.children.reduce((s, c) => s + c.fileCount, 0);
    node.high = node.children.reduce((s, c) => s + c.high, 0);
    node.churn = sum('churn');
    node.bugfix = sum('bugfix');
    node.score = src.avgRisk ?? null;   // 점수가 있는 하위 파일의 평균(computeAverages)
    if (parent) maxHigh = Math.max(maxHigh, node.high);
    return node;
  };
  const root = walk(tree || { children: [] }, null);
  root.maxHigh = maxHigh;
  return root;
}

const byName = (a, b) => a.name.localeCompare(b.name, 'ko');
const nullsLastDesc = (av, bv) => (av == null ? (bv == null ? 0 : 1) : bv == null ? -1 : bv - av);
export const TREE_SORTS = {
  score: {
    label: '변경 집중도 순',
    // 폴더는 임계값 이상 파일 수 → 평균 점수, 파일은 점수. 값이 없으면 뒤에 둔다.
    compare: (a, b) => (a.dir ? (b.high - a.high) || nullsLastDesc(a.score, b.score) : nullsLastDesc(a.score, b.score)) || byName(a, b),
  },
  name: { label: '이름순', compare: byName },
};

function sortedChildren(node, sort) {
  const compare = (TREE_SORTS[sort] || TREE_SORTS.score).compare;
  return [...node.children].sort((a, b) => (Number(b.dir) - Number(a.dir)) || compare(a, b));
}

/* 지금 보여 줄 줄 목록. 검색어가 있으면 경로가 맞는 파일과 그 상위 폴더만 남기고 모두 펼친다. */
export function visibleRows(base, { expanded, sort = 'score', query = '' }) {
  const q = query.trim().toLowerCase();
  const rows = [];
  const matches = new Map();
  const hit = (node) => {
    if (!q) return true;
    if (matches.has(node.id)) return matches.get(node.id);
    const value = node.dir ? node.children.some(hit) : node.path.toLowerCase().includes(q);
    matches.set(node.id, value);
    return value;
  };
  const walk = (node, depth) => {
    for (const child of sortedChildren(node, sort)) {
      if (!hit(child)) continue;
      const open = child.dir && (q ? true : expanded.has(child.path));
      rows.push({ node: child, depth, open });
      if (open) walk(child, depth + 1);
    }
  };
  if (base?.dir) walk(base, 0);
  return rows;
}

/* 경로로 폴더를 찾는다. 합쳐진 폴더('main/java')는 마지막 경로로 찾는다. */
export function findNode(root, path) {
  if (!path) return root;
  for (const child of root.children || []) {
    if (!child.dir) continue;
    if (child.path === path) return child;
    if (path.startsWith(`${child.path}/`)) return findNode(child, path);
  }
  return null;
}

export function allFolderPaths(node, out = new Set()) {
  for (const c of node.children || []) if (c.dir) { out.add(c.path); allFolderPaths(c, out); }
  return out;
}

/* 이름에서 검색어 부분만 <mark>로 감싼다 (나머지는 이스케이프) */
function highlight(text, q) {
  if (!q) return esc(text);
  const i = text.toLowerCase().indexOf(q);
  if (i < 0) return esc(text);
  return `${esc(text.slice(0, i))}<mark>${esc(text.slice(i, i + q.length))}</mark>${esc(text.slice(i + q.length))}`;
}

const params = () => new URLSearchParams(location.search);

/* 트리 화면을 host 안에 만든다.
   opts: { model, policyLabel, threshold, riskColor, fmtScore, githubUrl(node), onOpenFile(data), query, onQuery(q) } */
export function mountFileTree(host, opts) {
  const { model, threshold, riskColor, fmtScore } = opts;
  let sort = TREE_SORTS[params().get('tree_sort')] ? params().get('tree_sort') : 'score';
  let base = findNode(model, params().get('tree_root') || '') || model;
  let query = opts.query || '';
  const expanded = new Set();
  let rows = [];
  let active = -1;
  let frame = 0;

  host.innerHTML = `
    <div class="ft-toolbar">
      <input type="search" class="cx-search ft-search" placeholder="파일 경로 검색 (예: api/, analytics)" autocomplete="off" aria-label="트리에서 파일 경로 검색">
      <select class="ft-sort" aria-label="트리 정렬">${Object.entries(TREE_SORTS).map(([k, s]) => `<option value="${k}">${esc(s.label)}</option>`).join('')}</select>
      <button type="button" class="rbtn ft-collapse">모두 접기</button>
      <button type="button" class="rbtn ft-expand">모두 펼치기</button>
    </div>
    <nav class="ft-crumbs" aria-label="현재 폴더"></nav>
    <div class="ft-table">
      <div class="ft-row ft-head" aria-hidden="true">
        <span>이름</span><span>파일</span><span title="${esc(opts.policyLabel)} ${threshold} 이상인 하위 파일 수 (0.7은 표시 임계값이며 상위 30%를 뜻하지 않음)">변경 집중 파일</span>
        <span title="폴더는 값이 있는 하위 파일의 평균">${esc(opts.policyLabel)}</span><span class="ft-opt" title="최근 180일의 추가+삭제 줄 수">수정량</span><span class="ft-opt">버그 수정</span><span class="ft-opt">주요 수정자</span><span></span>
      </div>
      <div class="ft-scroll" role="tree" tabindex="0" aria-label="디렉토리 트리"><div class="ft-spacer"></div></div>
    </div>
    <div class="ft-status" role="status"></div>`;
  const search = host.querySelector('.ft-search');
  const sortEl = host.querySelector('.ft-sort');
  const crumbs = host.querySelector('.ft-crumbs');
  const scroller = host.querySelector('.ft-scroll');
  const spacer = host.querySelector('.ft-spacer');
  const status = host.querySelector('.ft-status');
  search.value = query;
  sortEl.value = sort;

  const saveUrl = () => {
    const url = new URL(location.href);
    url.searchParams.set('tree_sort', sort);
    if (base.path) url.searchParams.set('tree_root', base.path); else url.searchParams.delete('tree_root');
    history.replaceState(null, '', url);
  };

  const scoreCell = (node) => {
    if (node.score == null) return `<span class="cx-na" title="${node.dir ? `${esc(opts.policyLabel)} 값이 있는 하위 파일이 없습니다` : `${esc(opts.policyLabel)} 값이 없습니다 (0이 아님)`}">${DASH}</span>`;
    const color = riskColor(node.score);
    return `<span class="hm-val ft-score" style="background:${color}22;color:${color};" title="${node.dir ? `하위 파일의 평균 ${esc(opts.policyLabel)}` : esc(opts.policyLabel)}">${esc(fmtScore(node.score))}</span>`;
  };
  const highCell = (node) => {
    if (!node.dir) return '';
    const width = model.maxHigh ? Math.round(100 * node.high / model.maxHigh) : 0;
    return `<span class="ft-high"><b>${num(node.high)}</b><span class="ft-bar"><span style="width:${node.high ? Math.max(4, width) : 0}%"></span></span></span>`;
  };
  const countCell = (value, unit) => (value == null ? `<span class="cx-na">${DASH}</span>` : `${num(value)}${unit}`);
  const bfCell = (node) => {
    if (node.dir) return '';
    const bf = node.data.bus_factor;
    if (bf == null) return `<span class="cx-na" title="식별된 기여가 없습니다 (0이 아님)">${DASH}</span>`;
    return node.data.is_bus_factor_risk ? `<span class="ft-bf1" title="한 명에게 변경 기여 80% 이상 집중">${num(bf)}</span>` : num(bf);
  };

  function rowHtml(row, index) {
    const { node, depth, open } = row;
    const q = query.trim().toLowerCase();
    const url = opts.githubUrl(node);
    const caret = node.dir ? `<span class="ft-caret" aria-hidden="true">${open ? '▾' : '▸'}</span>` : '<span class="ft-caret" aria-hidden="true"></span>';
    const label = node.dir ? `${highlight(node.name, q)}/` : highlight(node.name, q);
    const actions = [
      node.dir ? `<button type="button" class="ft-act" data-focus="${esc(node.path)}" tabindex="-1" title="이 폴더만 보기">⤢</button>` : '',
      url ? `<a class="ft-act" href="${esc(url)}" target="_blank" rel="noopener" tabindex="-1" title="GitHub에서 열기">↗</a>` : '',
    ].join('');
    return `<div class="ft-row ft-item${node.dir ? ' is-dir' : ''}${index === active ? ' is-active' : ''}" id="ft-node-${node.id}" role="treeitem"
        aria-level="${depth + 1}" aria-selected="${index === active}"${node.dir ? ` aria-expanded="${open}"` : ''} data-index="${index}" style="top:${index * ROW_H}px" title="${esc(node.path)}">
      <span class="ft-name" style="padding-left:${depth * 16}px">${caret}<span class="ft-label">${label}</span></span>
      <span>${node.dir ? num(node.fileCount) : ''}</span>
      <span>${highCell(node)}</span>
      <span>${scoreCell(node)}</span>
      <span class="ft-opt">${countCell(node.churn, '줄')}</span>
      <span class="ft-opt">${countCell(node.bugfix, '회')}</span>
      <span class="ft-opt">${bfCell(node)}</span>
      <span class="ft-acts">${actions}</span>
    </div>`;
  }

  function paint() {
    frame = 0;
    const top = scroller.scrollTop;
    const height = scroller.clientHeight || 480;
    const from = Math.max(0, Math.floor(top / ROW_H) - OVERSCAN);
    const to = Math.min(rows.length, Math.ceil((top + height) / ROW_H) + OVERSCAN);
    let html = '';
    for (let i = from; i < to; i++) html += rowHtml(rows[i], i);
    spacer.style.height = `${rows.length * ROW_H}px`;
    spacer.innerHTML = html;
    if (active >= 0 && rows[active]) scroller.setAttribute('aria-activedescendant', `ft-node-${rows[active].node.id}`);
    else scroller.removeAttribute('aria-activedescendant');
  }
  const schedule = () => { if (!frame) frame = requestAnimationFrame(paint); };

  function renderCrumbs() {
    const parts = [];
    for (let n = base; n; n = n.parent) parts.unshift(n);
    crumbs.innerHTML = parts.map((n, i) => (i === parts.length - 1
      ? `<span aria-current="location">${esc(n.parent ? n.name : '전체')}</span>`
      : `<button type="button" class="ft-crumb" data-path="${esc(n.path)}">${esc(n.parent ? n.name : '전체')}</button>`)).join('<span class="ft-sep" aria-hidden="true">/</span>');
  }

  function refresh({ keep } = {}) {
    const activePath = keep && rows[active] ? rows[active].node.path : null;
    rows = visibleRows(base, { expanded, sort, query });
    active = activePath != null ? rows.findIndex(r => r.node.path === activePath) : -1;
    if (!model.fileCount) status.textContent = '표시할 파일이 없습니다.';
    else if (!rows.length) status.textContent = query.trim() ? `'${query.trim()}'와 일치하는 파일이 없습니다.` : '이 폴더에 파일이 없습니다.';
    else status.textContent = `${base.path || '전체'} · 파일 ${num(base.fileCount)}개${query.trim() ? ` · 검색 결과 ${num(rows.filter(r => !r.node.dir).length)}개` : ''}`;
    renderCrumbs();
    saveUrl();
    paint();
  }

  function reveal(index) {
    if (index < 0) return;
    const y = index * ROW_H;
    if (y < scroller.scrollTop) scroller.scrollTop = y;
    else if (y + ROW_H > scroller.scrollTop + scroller.clientHeight) scroller.scrollTop = y + ROW_H - scroller.clientHeight;
  }
  function setActive(index) {
    active = Math.max(0, Math.min(rows.length - 1, index));
    reveal(active);
    paint();
  }
  function toggle(index, open) {
    const row = rows[index];
    if (!row?.node.dir || query.trim()) return;
    const next = open ?? !row.open;
    if (next) expanded.add(row.node.path); else expanded.delete(row.node.path);
    active = index;
    refresh({ keep: true });
  }
  function focusFolder(path) {
    base = findNode(model, path) || model;
    scroller.scrollTop = 0;
    active = -1;
    refresh();
  }
  function activate(index) {
    const row = rows[index];
    if (!row) return;
    if (row.node.dir) toggle(index);
    else { setActive(index); opts.onOpenFile(row.node.data); }
  }

  scroller.addEventListener('scroll', schedule, { passive: true });
  new ResizeObserver(schedule).observe(scroller);
  spacer.addEventListener('click', (event) => {
    const focus = event.target.closest('[data-focus]');
    if (focus) { event.stopPropagation(); focusFolder(focus.dataset.focus); return; }
    if (event.target.closest('a.ft-act')) return;   // GitHub 링크는 그대로 연다
    const item = event.target.closest('.ft-item');
    if (item) { scroller.focus({ preventScroll: true }); activate(Number(item.dataset.index)); }
  });
  scroller.addEventListener('keydown', (event) => {
    if (!rows.length) return;
    const row = rows[active];
    const keys = {
      ArrowDown: () => setActive(active + 1),
      ArrowUp: () => setActive(active < 0 ? 0 : active - 1),
      Home: () => setActive(0),
      End: () => setActive(rows.length - 1),
      ArrowRight: () => { if (!row) setActive(0); else if (row.node.dir && !row.open) toggle(active, true); else if (row.node.dir) setActive(active + 1); },
      ArrowLeft: () => {
        if (!row) return;
        if (row.node.dir && row.open && !query.trim()) { toggle(active, false); return; }
        const parent = rows.findLastIndex((r, i) => i < active && r.depth === row.depth - 1);
        if (parent >= 0) setActive(parent);
      },
      Enter: () => activate(active < 0 ? 0 : active),
      ' ': () => activate(active < 0 ? 0 : active),
    };
    if (!keys[event.key]) return;
    event.preventDefault();
    keys[event.key]();
  });
  crumbs.addEventListener('click', (event) => {
    const crumb = event.target.closest('[data-path]');
    if (crumb) focusFolder(crumb.dataset.path);
  });
  sortEl.addEventListener('change', () => { sort = sortEl.value; refresh({ keep: true }); });
  search.addEventListener('input', () => { query = search.value; scroller.scrollTop = 0; active = -1; refresh(); opts.onQuery?.(query); });
  host.querySelector('.ft-collapse').addEventListener('click', () => { expanded.clear(); scroller.scrollTop = 0; active = -1; refresh(); });
  host.querySelector('.ft-expand').addEventListener('click', () => { allFolderPaths(base, expanded); refresh({ keep: true }); });

  refresh();
  return {
    setQuery(value) { if (value === query) return; query = value; search.value = value; scroller.scrollTop = 0; active = -1; refresh(); },
  };
}
