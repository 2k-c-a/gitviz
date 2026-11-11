import { element, setText } from './ui/components.js';
import { action } from './ui/analytics-view.js';
/* ── 코드분석 페이지: /tree API → 파일 목록 + 디렉토리 트리(file-tree.js) ──
   cx-tree가 있을 때(=코드분석 페이지)만 동작한다.
   화면의 지표 이름·설명은 지금 열려 있는 스냅샷의 계산 버전(policy.js)을 따른다:
   새 버전은 '변경 집중도', 이전 버전은 'Risk Score(이전 계산 버전)'로 설명한다.
   null은 0으로 바꾸지 않는다 — 값 없음('—')·0·표본 부족은 서로 다른 상태로 표시한다. */
import { getRepoId, fetchRepoTree, fetchSnapshotMeta } from './api.js';
import { getRepoGithubInfo, githubFileUrl as ghFileUrl, githubFolderUrl as ghFolderUrl } from './github.js';
import { esc, num, fmtScore, fmtScoreStable, fmtPct, fmtWindow, DASH } from './fmt.js';
import {
  policyOf, scoreOf, assessDataQuality,
  DISPLAY_THRESHOLD, STRONG_COUPLING,
} from './policy.js';
import { renderLoadError } from './page-context.js';
import { buildTreeModel, mountFileTree } from './file-tree.js';

const treeEl = document.getElementById('cx-tree');

const fmt2 = (n) => fmtScoreStable(n);

/* ── 색상 인코딩 — 회색 = 값 없음(0이 아님) ── */
const NO_VALUE = '#B4B2A9';

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

/* 목록 정렬 비교 함수 — 값 없는 파일은 항상 뒤 */
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

let TREE = { name: 'root', children: [] };
let FILES = [];
let POLICY = policyOf(null);
let SNAP = null;          // 지금 열려 있는 저장 결과의 메타 (id, window_*, formula_version …)
let DQ = {};              // data_quality — 수집 범위·표본 정보
let searchText = new URLSearchParams(location.search).get('file_q') || '';
// 파일 목록 정렬: 수정량 순·버그 수정 순. 예전 주소의 다른 정렬(changes·risk·coupling·bf)은 수정량 순으로 연다.
// 값이 없는 파일(수정량·버그 수정 수를 싣기 전에 만든 결과 등)은 0으로 보지 않고 뒤에 둔다.
const LIST_ORDERS = {
  churn:  { label: '수정량 순', compare: nullsLast(f => f.churn, 'desc') },
  bugfix: { label: '버그 수정 순', compare: nullsLast(f => f.bugfix_count, 'desc') },
};
let listOrder = new URLSearchParams(location.search).get('file_order') || 'churn';
if (!LIST_ORDERS[listOrder]) listOrder = 'churn';
let listPage = Math.max(0, Number(new URLSearchParams(location.search).get('file_page')) || 0);

/* 폴더명/파일명 클릭 → GitHub 이동에 필요한 레포 정보 (summary에서 가져옴) */
let GH_INFO = null;

async function initCodeAnalysisPage() {
  const id = getRepoId();
  const [meta, data, ghInfo] = await Promise.all([fetchSnapshotMeta(id), fetchRepoTree(id), getRepoGithubInfo(id)]);
  SNAP = meta.snapshot;
  POLICY = policyOf(SNAP.formula_version);
  DQ = data.data_quality || meta.data_quality || {};
  applyPolicyText();
  setText('cx-status', DQ.commit_count === 0 ? '최근 180일에 커밋 없음' : `저장 결과 #${SNAP.id} 조회 완료`);
  TREE = data.tree || { name: 'root', children: [] };
  FILES = data.files || [];
  computeAverages(TREE);

  GH_INFO = ghInfo;

  renderSummary();
  renderList();
  setupSearch();
  setupFileViews();
  setupTree();
}

const githubFolderUrl = (folderPath) => ghFolderUrl(GH_INFO, folderPath);
const githubFileUrl = (filePath) => ghFileUrl(GH_INFO, filePath);

/* 디렉토리 노드의 평균 점수/BF/Coupling을 자식들로부터 post-order로 역산해 채운다
   (/tree는 leaf 값만 내려준다). 트리의 폴더 점수는 avgRisk를 쓴다. 하위 파일 수(fileCount)도 같이 센다. */
export function computeAverages(node) {
  if (!node.children) {
    node.avgRisk = scoreOf(node);
    node.avgBF = node.bus_factor;
    node.avgCoupling = couplingOf(node);   // 후보 없는 파일(0으로 내려옴)은 값 없음 — 폴더 평균에 0으로 넣지 않는다
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
  setText('cx-dash-sub', `코드 분석 — ${p.scoreShort} · 주요 수정자 수 · 함께 수정 정도`);
  setText('cx-l-high', `${p.scoreShort} ≥ ${DISPLAY_THRESHOLD} 파일`);
  setText('cx-s-high', '표시 임계값 기준이며 상위 30%를 뜻하지 않음');
  setText('cx-l-avg', `평균 ${p.scoreShort}`);
  setText('cx-l-bf', '한 명 집중 파일');
  setText('cx-s-bf', '한 명에게 변경 기여 80% 이상 집중');
  setText('cx-l-cp', '자주 함께 수정되는 파일');
  setText('cx-s-cp', `함께 수정 정도 ≥ ${STRONG_COUPLING} (실제 의존성 아님)`);
  setText('cx-th-score', p.scoreShort);
  setText('cx-th-bf', '주요 수정자 수');
  setText('cx-th-cp', '함께 수정 정도');

  const note = document.getElementById('cx-policy-note');
  if (note) {
    const win = SNAP.window_start
      ? `최근 180일: 이 결과에 고정된 ${fmtWindow(SNAP)}입니다(종료 시각 제외). 모든 파일에 같은 기간을 적용합니다.`
      : `이 결과는 구간 제한 없이 전체 이력을 대상으로 계산했습니다(${fmtWindow(SNAP)}).`;
    const lead = `${p.leadText} 색·배지의 0.7은 표시 임계값이며 상위 30%를 뜻하지 않습니다.`;
    const more = [win, p.summaryText, p.thresholdText,
      '폴더는 점수가 있는 하위 파일의 평균입니다. 점수가 없는 파일(관측 변경 없음·계산 입력 누락)은 평균에서 제외하며 0으로 취급하지 않습니다.',
      '주요 수정자 수는 파일 변경 커밋의 80%를 차지하는 최소 기여자 수(식별된 기여 기준)이고, 함께 수정 정도는 실제 의존성이 아닌 과거 함께 수정 신호입니다.'];
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
      + chip('ok', '정상 관측', count('observed'), `최근 180일 변경 ${threshold}건 이상`)
      + chip('amber', '표본 부족', count('low_support'), `변경 ${threshold}건 미만 — 점수는 있으나 근거가 적습니다`)
      + chip('amber', '자료 부족', count('metrics_missing'), '계산 입력(수정량 등) 누락 — 점수를 계산하지 못해 순위 뒤에 둡니다')
      + chip('gray', '관측 변경 없음', count('no_history'), '최근 180일에 변경이 없어 점수가 없습니다(0이 아님, 안전을 뜻하지도 않음)');
  }
}

/* ── 표시 도우미: 값 없음('—')과 0, 표본 부족을 서로 다른 모양으로 ── */
const chip = (val, color) => `<span class="hm-val" style="padding:2px 8px;background:${color}22;color:${color};font-weight:600;">${esc(val)}</span>`;
const tag = (tone, text, title = '') => `<span class="cx-tag ${tone}"${title ? ` title="${esc(title)}"` : ''}>${esc(text)}</span>`;
const na = (reason) => `<span class="cx-na" title="${esc(reason)}">${DASH}</span>`;

const SCORE_NA = {
  no_history: '최근 180일에 변경이 없어 점수가 없습니다 (0이 아니며 안전을 뜻하지도 않습니다)',
  metrics_missing: '점수 계산에 필요한 입력(수정량 등)이 빠져 있어 계산하지 못했습니다 (자료 부족)',
};
const scoreNaReason = (f) => SCORE_NA[f.evidence_status] || '점수가 없습니다';
const BF_NA = '식별된 기여가 없어 주요 수정자 수를 계산하지 못했습니다 (0이 아님)';
const BUGFIX_NA = '관측 변경이 없거나 버그 수정 수를 싣기 전에 만든 결과라 값이 없습니다 (0이 아님). 버그 수정 순에서는 뒤에 둡니다';
const CHURN_NA = '관측 변경이 없거나 줄 수를 알 수 없는 변경이 있어, 또는 수정량을 싣기 전에 만든 결과라 수정량이 없습니다 (0이 아님). 수정량 순에서는 뒤에 둡니다';

/* 파일 행 아래에 붙는 근거 표시 — 점수 0·점수 없음·표본 부족을 문장으로 구분 */
function fileNotes(f) {
  const notes = [];
  const ev = f.evidence_status;
  if (ev === 'no_history') notes.push(tag('gray', '관측 변경 없음', SCORE_NA.no_history));
  else if (ev === 'metrics_missing') notes.push(tag('amber', '자료 부족 · 계산 입력 누락', SCORE_NA.metrics_missing));
  else if (ev === 'low_support') notes.push(tag('amber', `표본 부족 (${num(f.observation_count)}건)`, `최근 180일 변경 ${num(f.observation_count)}건 — 점수 0도 안전을 뜻하지 않습니다`));
  if (ev !== 'no_history' && f.unidentified_share != null && f.unidentified_share > 0) {
    notes.push(tag('gray', `작성자 미식별 ${fmtPct(f.unidentified_share)}`, '작성자를 식별하지 못한 변경 — 주요 수정자 수·기여 분산도 계산에서 제외됩니다'));
  }
  if (f.coupling_support > 0 && f.coupling_support < 5) notes.push(tag('gray', `함께 수정 정도 표본 ${f.coupling_support}회`, '함께 수정 5건 미만 — 표본 부족(표시 정책)'));
  return notes.join('');
}

const LIST_LIMIT = 50;

function renderList() {
  const tbody = document.getElementById('cx-list-tbody');
  const note = document.getElementById('cx-list-note');
  if (!tbody) return;

  const metric = LIST_ORDERS[listOrder] || LIST_ORDERS.churn;
  const q = searchText.trim().toLowerCase();
  const matched = q ? FILES.filter(f => f.path.toLowerCase().includes(q)) : FILES;
  const limit = LIST_LIMIT;
  listPage = Math.floor(Math.min(listPage, Math.max(0, Math.ceil(matched.length / limit) - 1)));
  const sorted = [...matched].sort(metric.compare).slice(listPage * limit, (listPage + 1) * limit);
  document.getElementById('cx-list-prev')?.toggleAttribute('disabled', listPage === 0);
  document.getElementById('cx-list-next')?.toggleAttribute('disabled', (listPage + 1) * limit >= matched.length);
  if (document.getElementById('cx-list-page')) document.getElementById('cx-list-page').textContent = `${matched.length}개 · ${listPage + 1} / ${Math.max(1, Math.ceil(matched.length / limit))}페이지`;
  saveFileListContext();
  if (note) {
    note.textContent = `${metric.label} · ${num(matched.length)}개 중 ${num(sorted.length)}개 표시 · 변경 횟수·수정량·버그 수정 수는 ${SNAP && SNAP.window_start ? '최근 180일' : '전체 이력'} 기준`;
  }

  if (!FILES.length) {
    tbody.innerHTML = '<tr><td class="hm-td" colspan="8" style="text-align:center;color:var(--text3);padding:24px;">파일 데이터가 없습니다.</td></tr>';
    return;
  }
  if (!sorted.length) {
    tbody.innerHTML = `<tr><td class="hm-td" colspan="8" style="text-align:center;color:var(--text3);padding:24px;">'${esc(searchText.trim())}'와 일치하는 파일이 없습니다.</td></tr>`;
    return;
  }

  const byId = new Map(sorted.map(f => [f.file_id, f]));

  tbody.innerHTML = sorted.map(f => {
    const sc = scoreOf(f);
    const cp = couplingOf(f);
    const bfBadge = f.is_bus_factor_risk
      ? '<span class="badge badge-red" title="최근 180일에서 한 명에게 파일 변경 기여의 80% 이상이 집중됩니다">한 명 집중</span>' : '';
    const couplingBadge = cp != null && cp >= STRONG_COUPLING
      ? '<span class="badge badge-amber" style="margin-left:4px;" title="함께 수정 정도가 높은 파일 — 실제 의존성을 뜻하지 않습니다">자주 함께 수정</span>' : '';
    const fileUrl = githubFileUrl(f.path);
    const pathHtml = `<button type="button" class="ui-list-link cx-file-detail">${esc(f.path)}</button>${fileUrl ? ` <a href="${esc(fileUrl)}" target="_blank" rel="noopener" class="cx-file-link" aria-label="${esc(f.path)} GitHub에서 열기">GitHub ↗</a>` : ''}`;
    return `<tr style="cursor:pointer;" data-file-id="${f.file_id}" title="수정한 사람·함께 변경된 파일 보기">
      <td class="hm-td file" title="${esc(f.path)}">${pathHtml}${bfBadge}${couplingBadge}<div class="cx-notes">${fileNotes(f)}</div></td>
      <td class="hm-td">${f.observation_count == null ? DASH : num(f.observation_count)}회</td>
      <td class="hm-td">${f.churn == null ? na(CHURN_NA) : `${num(f.churn)}줄`}</td>
      <td class="hm-td">${f.bugfix_count == null ? na(BUGFIX_NA) : `${num(f.bugfix_count)}회`}</td>
      <td class="hm-td">${sc != null ? `<span class="hm-val" style="background:${riskColor(sc)}22;color:${riskColor(sc)};font-weight:600;">${fmt2(sc)}</span>` : na(scoreNaReason(f))}</td>
      <td class="hm-td" style="text-align:center;">${f.bus_factor != null ? f.bus_factor : na(BF_NA)}</td>
      <td class="hm-td">${cp != null ? `<span class="hm-val" style="background:${couplingColor(cp)}22;color:${couplingColor(cp)};">${fmt2(cp)}</span>` : na('함께 수정 2건 이상인 함께 수정된 후보가 없습니다')}</td>
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
  input.value = searchText;
  input.addEventListener('input', () => { searchText = input.value; listPage = 0; renderList(); treeView?.setQuery(searchText); });
  const controls = element('div', 'analysis-toolbar');
  const order = element('select'); order.id = 'cx-list-order'; order.setAttribute('aria-label', '파일 목록 정렬');
  Object.entries(LIST_ORDERS).map(([key, m]) => [key, m.label]).forEach(([key, label]) => { const option = element('option', '', label); option.value = key; order.append(option); });
  order.value = listOrder; order.addEventListener('change', () => { listOrder = order.value; listPage = 0; renderList(); });
  const prev = action('이전', () => { listPage--; renderList(); }); prev.id = 'cx-list-prev';
  const next = action('다음', () => { listPage++; renderList(); }); next.id = 'cx-list-next';
  const page = element('span'); page.id = 'cx-list-page'; page.setAttribute('role', 'status');
  controls.append(order, prev, page, next); input.after(controls); renderList();
}

if (treeEl) {
  initCodeAnalysisPage().catch(err => {
    setText('cx-status', '결과 조회 실패');
    console.error('[code-analysis] 로드 실패:', err);
    renderLoadError(err);
  });
}

/* 디렉토리 트리(file-tree.js). 검색어는 파일 목록과 함께 쓴다. */
let treeView = null;
function setupTree() {
  const model = buildTreeModel(TREE, { scoreOf, threshold: DISPLAY_THRESHOLD });
  treeView = mountFileTree(treeEl, {
    model, threshold: DISPLAY_THRESHOLD, riskColor, fmtScore: fmt2, policyLabel: POLICY.scoreShort, query: searchText,
    githubUrl: (node) => (node.dir ? githubFolderUrl(node.path) : githubFileUrl(node.path)),
    onOpenFile: (data) => { if (data.file_id != null && window.openFileDrilldown) window.openFileDrilldown(data.file_id, data); },
    onQuery: (q) => {
      searchText = q; listPage = 0; renderList();
      const input = document.getElementById('cx-search'); if (input) input.value = q;
    },
  });
}

function saveFileListContext() {
  const url = new URL(location.href); url.searchParams.set('file_q', searchText); url.searchParams.set('file_order', listOrder); url.searchParams.set('file_page', listPage); history.replaceState(null, '', url);
}
function setupFileViews() {
  const list = document.getElementById('cx-list-tbody').closest('.panel');
  const tree = treeEl.closest('.panel');
  tree.before(list);
  const controls = element('nav', 'analysis-toolbar'); controls.setAttribute('aria-label', '파일 분석 보기');
  const views = [['files', '파일 목록', list], ['tree', '디렉토리 트리', tree]];
  const select = key => { views.forEach(([value, , panel]) => { panel.hidden = value !== key; }); controls.querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.view === key))); const url = new URL(location.href); url.searchParams.set('file_view', key); history.replaceState(null, '', url); };
  views.forEach(([key, label]) => { const button = action(label, () => select(key)); button.dataset.view = key; controls.append(button); });
  list.before(controls);
  const requested = new URLSearchParams(location.search).get('file_view'); select(views.some(v => v[0] === requested) ? requested : 'files');
}
