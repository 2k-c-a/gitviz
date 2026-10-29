import { request } from './platform/transport.js';
/* ── 분석 이력 · 두 결과 비교 페이지 ──
   hist-tbody가 있을 때(=분석 이력 페이지)만 동작한다.
   - GET /repositories/{id}/snapshots        : 메타데이터만 최신순, before_id 페이지네이션(결과 payload는 받지 않는다)
   - GET /repositories/{id}/snapshots/compare : 변경 파일을 offset/limit으로 나눠 받는다. summary는 전체 기준.
   이 페이지는 특정 결과를 "고정"하지 않는다: 분석 결과 조회(/analytics/snapshot)를 부르지 않아
   주소의 snapshot을 바꾸거나 결과 안내 배너를 띄우지 않는다. */
import { getRepoId, fetchSnapshots, fetchSnapshotCompare, ApiError } from './api.js';
import { parseState } from './viz-state.js';
import { esc, num, fmtScore, fmtDelta, fmtDateTime, fmtWindow, windowDays, DASH } from './fmt.js';
import { policyOf, assessDataQuality, COLLECTION_TEXT, EVIDENCE_TEXT, STATUS_TONE, COLLECTION_NOTE, evidenceNote } from './policy.js';

const tbody = document.getElementById('hist-tbody');

const PAGE = 20;          // 목록 한 번에 20개
const CHANGES_PAGE = 100; // 비교 변경 파일 한 번에 100개 (서버 기본값, 최대 1000)

let REPO_ID = null;
let SNAPSHOTS = [];       // 불러온 결과 메타 (최신 → 과거)
let NEXT_BEFORE = null;   // 목록의 다음 페이지 커서 (null이면 마지막)
let CURRENT = null;       // 지금 보고 있던 결과 (주소의 snapshot)
let CMP = null;           // {before, after, data, changes, filter, loading}

const tag = (tone, text, title = '') => `<span class="cx-tag ${tone}"${title ? ` title="${esc(title)}"` : ''}>${esc(text)}</span>`;
const setText = (id, t) => { const el = document.getElementById(id); if (el) el.textContent = t; };

function showError(err) {
  const box = document.getElementById('hist-error');
  if (!box) return;
  let title = '분석 이력을 불러오지 못했습니다';
  let body = err?.message || '알 수 없는 오류';
  if (err?.status === 404) { title = '저장소를 찾을 수 없습니다'; body = '저장소가 삭제되었거나 접근 권한이 없습니다.'; }
  else if (err?.status === 0) title = '서버에 연결할 수 없습니다';
  box.innerHTML = `<div class="ctx-error"><div class="ctx-error-title">${esc(title)}</div><div class="ctx-error-body">${esc(body)}</div></div>`;
}

const versionTag = (snap) => {
  const p = policyOf(snap.formula_version);
  return tag(p.kind === 'priority' ? 'blue' : 'gray', p.versionLabel, snap.formula_version);
};

function windowText(snap) {
  const d = windowDays(snap);
  return `${fmtWindow(snap)}${d != null ? ` · ${d}일` : ''}`;
}

/* ── 목록 ── */
function renderList() {
  if (!SNAPSHOTS.length) {
    tbody.innerHTML = '<tr><td class="hm-td" colspan="8" style="text-align:center;color:var(--text3);padding:24px;">저장된 분석 결과가 없습니다. 분석을 먼저 실행해 주세요.</td></tr>';
  } else {
    const beforeSel = Number(document.getElementById('cmp-before').value) || null;
    const afterSel = Number(document.getElementById('cmp-after').value) || null;
    tbody.innerHTML = SNAPSHOTS.map(s => `<tr>
      <td class="hm-td" style="text-align:center;"><input type="radio" name="pick-before" value="${s.id}" ${s.id === beforeSel ? 'checked' : ''} aria-label="#${s.id}을 시작으로"></td>
      <td class="hm-td" style="text-align:center;"><input type="radio" name="pick-after" value="${s.id}" ${s.id === afterSel ? 'checked' : ''} aria-label="#${s.id}을 종료로"></td>
      <td class="hm-td"><b>#${s.id}</b>${s.id === CURRENT ? ' ' + tag('ok', '보고 있던 결과') : ''}</td>
      <td class="hm-td" title="${esc(s.analyzed_at)}">${esc(fmtDateTime(s.analyzed_at))}</td>
      <td class="hm-td" title="${esc(`${s.window_start || '전체 이력'} ~ ${s.window_end}`)}">${esc(windowText(s))}</td>
      <td class="hm-td">${versionTag(s)}</td>
      <td class="hm-td" style="font-family:monospace;" title="${esc(s.target_sha)}">${esc((s.target_sha || '').slice(0, 10))}</td>
      <td class="hm-td"><button class="btn-secondary hist-open" data-id="${s.id}" style="padding:3px 10px;font-size:12px;">열기</button></td>
    </tr>`).join('');
  }
  setText('hist-count', `${num(SNAPSHOTS.length)}개 표시${NEXT_BEFORE != null ? ' · 더 있음' : ''}`);
  const more = document.getElementById('hist-more');
  more.style.display = NEXT_BEFORE != null ? '' : 'none';

  tbody.querySelectorAll('.hist-open').forEach(b => b.addEventListener('click', () => window.goPage('code-analysis', REPO_ID, Number(b.dataset.id))));
  tbody.querySelectorAll('input[name="pick-before"]').forEach(r => r.addEventListener('change', () => { document.getElementById('cmp-before').value = r.value; updateWarning(); }));
  tbody.querySelectorAll('input[name="pick-after"]').forEach(r => r.addEventListener('change', () => { document.getElementById('cmp-after').value = r.value; updateWarning(); }));
}

function fillSelects(keep = true) {
  const opts = SNAPSHOTS.map(s => `<option value="${s.id}">#${s.id} · ${esc(fmtDateTime(s.analyzed_at))} · ${esc(policyOf(s.formula_version).versionLabel)}</option>`).join('');
  for (const id of ['cmp-before', 'cmp-after']) {
    const sel = document.getElementById(id);
    const prev = sel.value;
    sel.innerHTML = opts;
    if (keep && prev && SNAPSHOTS.some(s => String(s.id) === prev)) sel.value = prev;
  }
}

function pickDefaults() {
  const before = document.getElementById('cmp-before'), after = document.getElementById('cmp-after');
  if (SNAPSHOTS.length >= 2) { after.value = String(SNAPSHOTS[0].id); before.value = String(SNAPSHOTS[1].id); }
  else if (SNAPSHOTS.length === 1) { after.value = before.value = String(SNAPSHOTS[0].id); }
}

const byId = (id) => SNAPSHOTS.find(s => s.id === Number(id));

/* 선택 직후 보여 주는 사전 안내 — 최종 판단은 서버(409/404/422)가 한다 */
function updateWarning() {
  const box = document.getElementById('cmp-warn');
  const b = byId(document.getElementById('cmp-before').value), a = byId(document.getElementById('cmp-after').value);
  const notes = [];
  if (b && a) {
    if (b.id === a.id) notes.push('시작과 종료가 같은 결과입니다. 변경 파일이 없는 것이 정상입니다.');
    else if (b.id > a.id) notes.push('시작이 종료보다 최신 결과입니다. 증가/감소 방향이 시간 순서와 반대로 읽힙니다.');
    if (b.formula_version !== a.formula_version) {
      notes.push(`두 결과의 계산 버전이 다릅니다(${policyOf(b.formula_version).versionLabel} ↔ ${policyOf(a.formula_version).versionLabel}). 서버가 직접 비교를 거부할 수 있습니다.`);
    }
  }
  box.innerHTML = notes.map(n => `<div class="cx-fallback">${esc(n)}</div>`).join('');
}

/* ── 비교 ── */
async function runCompare(offset = 0) {
  /* '더 보기'는 처음 비교한 두 결과를 이어서 받는다(그 사이 선택을 바꿔도 섞이지 않게) */
  const beforeId = offset > 0 && CMP ? CMP.before : Number(document.getElementById('cmp-before').value);
  const afterId = offset > 0 && CMP ? CMP.after : Number(document.getElementById('cmp-after').value);
  const out = document.getElementById('cmp-result');
  if (!beforeId || !afterId) { out.innerHTML = '<div class="cx-empty">비교할 두 결과를 선택해 주세요.</div>'; return; }

  if (offset === 0) {
    CMP = { before: beforeId, after: afterId, data: null, changes: [], filter: 'all', loading: true };
    out.innerHTML = '<div class="cx-empty">비교 중...</div>';
    const url = new URL(location.href);
    url.searchParams.set('before', String(beforeId));
    url.searchParams.set('after', String(afterId));
    history.replaceState(null, '', url);
  } else if (!CMP || CMP.loading) { return; }
  else { CMP.loading = true; }

  const token = CMP;
  try {
    const data = await fetchSnapshotCompare(REPO_ID, beforeId, afterId, { offset, limit: CHANGES_PAGE });
    if (CMP !== token) return;   // 그 사이 다른 비교를 시작했다면 이전 응답은 버린다
    CMP.data = data;
    CMP.changes = offset === 0 ? data.changes : CMP.changes.concat(data.changes);
    CMP.loading = false;
    renderCompare();
  } catch (err) {
    if (CMP !== token) return;
    CMP.loading = false;
    renderCompareError(err, beforeId, afterId, offset);
  }
}

function renderCompareError(err, beforeId, afterId, offset) {
  const out = document.getElementById('cmp-result');
  const b = byId(beforeId), a = byId(afterId);
  let title = '비교하지 못했습니다';
  let body = err.message;
  if (err instanceof ApiError) {
    if (err.status === 409) {
      const versions = b && a && b.formula_version !== a.formula_version
        ? `시작 #${b.id}은(는) ${policyOf(b.formula_version).versionLabel}, 종료 #${a.id}은(는) ${policyOf(a.formula_version).versionLabel}입니다. ` : '';
      title = '이 두 결과는 직접 비교할 수 없습니다';
      body = `${versions}${err.detail || '계산 버전이 다른 결과는 직접 비교할 수 없습니다.'} 같은 계산 버전의 결과끼리 선택해 주세요.`;
    } else if (err.status === 404) {
      title = '비교할 결과를 찾을 수 없습니다';
      body = err.detail || '같은 저장소의 결과만 비교할 수 있습니다.';
    } else if (err.status === 422) {
      title = '결과 번호가 올바르지 않습니다';
      body = '시작과 종료 결과를 목록에서 다시 선택해 주세요.';
    } else if (err.status === 0) {
      title = '서버에 연결할 수 없습니다';
    }
  }
  const retry = offset > 0 ? `<button class="btn-secondary" id="cmp-retry" style="margin-top:8px;">다시 시도</button>` : '';
  const html = `<div class="ctx-error"><div class="ctx-error-title">${esc(title)}</div><div class="ctx-error-body">${esc(body)}</div>${retry}</div>`;
  if (offset > 0) { out.insertAdjacentHTML('beforeend', html); document.getElementById('cmp-retry')?.addEventListener('click', () => { document.querySelector('#cmp-result .ctx-error')?.remove(); runCompare(offset); }); }
  else out.innerHTML = html;
}

/* 지표 하나의 before → after 표시. 값 없음(null)과 0, 증가/감소/동일, 한쪽 값 없음(자료 부족)을 구분한다 */
function metricCell(m, status, { digits = 2, zeroLabel = null } = {}) {
  /* zeroLabel: 이 지표에서 0이 '값 0'이 아니라 '해당 없음'일 때의 표기 (함께 수정 정도 0 = 함께 수정 2건 이상인 후보 쌍이 없음) */
  const fmtV = (v) => (v == null ? DASH : v === 0 && zeroLabel ? zeroLabel : (digits === 0 ? num(v) : fmtScore(v, digits)));
  if (m.before == null && m.after == null) return `<span class="cx-na" title="양쪽 모두 값 없음">${DASH}</span>`;
  const pair = `${fmtV(m.before)} → ${fmtV(m.after)}`;
  if (m.delta == null) {
    // 파일이 한쪽에만 있으면(추가/제외) 값이 없는 게 자연스럽다. 양쪽에 파일이 있는데 한쪽만 값이 없으면 자료 부족.
    const lack = status === 'retained' ? tag('amber', '자료 부족', '한쪽 결과에 값이 없어 차이를 계산하지 못했습니다') : '';
    return `<span>${pair}</span> ${lack}`;
  }
  if (m.delta === 0) return `<span>${pair}</span> ${tag('gray', '변화 없음')}`;
  const up = m.delta > 0;
  return `<span>${pair}</span> ${tag(up ? 'amber' : 'blue', `${up ? '↑ 증가' : '↓ 감소'} ${fmtDelta(m.delta, digits)}`, '각 결과 안에서 정규화한 값의 차이이며 품질 변화가 아닙니다')}`;
}

const STATUS_TAG = {
  added: () => tag('ok', '추가', '분석 결과에 새로 나타남 — 실제 Git 추가가 아닐 수 있음'),
  removed: () => tag('red', '제외', '분석 결과에서 사라짐 — 실제 Git 삭제가 아닐 수 있음'),
  retained: () => tag('gray', '양쪽에 있음'),
};

function hasLack(c) {
  return c.status === 'retained' && Object.values(c.metrics).some(m => (m.before == null) !== (m.after == null));
}
const FILTERS = {
  all: { label: '전체', test: () => true },
  added: { label: '추가', test: (c) => c.status === 'added' },
  removed: { label: '제외', test: (c) => c.status === 'removed' },
  up: { label: '점수 증가', test: (c) => (c.metrics.risk_score.delta ?? 0) > 0 },
  down: { label: '점수 감소', test: (c) => (c.metrics.risk_score.delta ?? 0) < 0 },
  lack: { label: '자료 부족', test: hasLack },
};

const tagTone = (status) => (STATUS_TONE[status] === 'warn' ? 'amber' : STATUS_TONE[status]);

function dqLine(side, snap, dq) {
  const a = assessDataQuality(dq);
  const collectionTip = a.collectionReasons.length || a.unverified.length
    ? [...a.collectionReasons, ...a.unverified.map(r => `${r} 확인 불가`)].join(' / ')
    : COLLECTION_NOTE;
  return `<tr><td class="key">${side} #${snap.id}</td>
    <td class="val" style="text-align:left;">${esc(windowText(snap))} · ${versionTag(snap)}
    <div>${tag(tagTone(a.collection), COLLECTION_TEXT[a.collection], collectionTip)}
    ${tag(tagTone(a.evidence), EVIDENCE_TEXT[a.evidence], a.evidenceReasons.join(' / ') || evidenceNote(a.threshold))}
    <small style="color:var(--text3);">관측 커밋 ${num(dq?.commit_count)}개 · 파일 ${num(dq?.collected_target_file_count)}/${num(dq?.target_tree_file_count)}개</small></div></td></tr>`;
}

function renderCompare() {
  const out = document.getElementById('cmp-result');
  const d = CMP.data;
  const policy = policyOf(d.after.formula_version);
  const interp = d.interpretation || {};
  const bdq = interp.before_data_quality || {}, adq = interp.after_data_quality || {};
  const incomplete = assessDataQuality(bdq).collection !== 'checked' || assessDataQuality(adq).collection !== 'checked';
  const s = d.summary || {};
  const pg = d.pagination || {};

  const counts = Object.fromEntries(Object.entries(FILTERS).map(([k, f]) => [k, CMP.changes.filter(f.test).length]));
  const rows = CMP.changes.filter(FILTERS[CMP.filter].test).map(c => `<tr>
      <td class="hm-td file" title="${esc(c.path)}" style="max-width:300px;">${esc(c.path)}</td>
      <td class="hm-td" style="text-align:center;">${STATUS_TAG[c.status]()}</td>
      <td class="hm-td">${metricCell(c.metrics.risk_score, c.status)}</td>
      <td class="hm-td">${metricCell(c.metrics.bus_factor, c.status, { digits: 0 })}</td>
      <td class="hm-td">${metricCell(c.metrics.coupling_score, c.status, { zeroLabel: '후보 없음' })}</td>
    </tr>`).join('');

  const filterBar = Object.entries(FILTERS).map(([k, f]) =>
    `<div class="rbtn${k === CMP.filter ? ' active' : ''}" data-filter="${k}">${f.label} ${num(counts[k])}</div>`).join('');
  const shown = CMP.changes.length;
  const more = pg.next_offset != null;

  out.innerHTML = `
    <div class="metrics-4" style="margin:14px 0 12px;">
      <div class="metric-card"><div class="m-label">결과에 나타남 (추가)</div><div class="m-val">${num(s.added)}</div><div class="m-sub">시작에는 없고 종료에 있는 파일</div></div>
      <div class="metric-card"><div class="m-label">결과에서 사라짐 (제외)</div><div class="m-val">${num(s.removed)}</div><div class="m-sub">시작에는 있고 종료에 없는 파일</div></div>
      <div class="metric-card"><div class="m-label">양쪽에 있는 파일</div><div class="m-val">${num(s.retained)}</div><div class="m-sub">그중 지표가 달라진 파일 ${num(s.changed)}개</div></div>
      <div class="metric-card"><div class="m-label">관측 기간 변경</div><div class="m-val">${interp.window_changed ? '있음' : '없음'}</div><div class="m-sub">${interp.window_changed ? '기간이 달라 변화에 기간 차이가 섞입니다' : '두 결과의 기간이 같습니다'}</div></div>
    </div>
    <table class="info-table" style="margin-bottom:10px;">${dqLine('시작', d.before, bdq)}${dqLine('종료', d.after, adq)}</table>
    ${incomplete ? '<div class="cx-fallback">파일 또는 PR 수집 범위가 일부만 수집됐거나 확인되지 않은 결과가 있어, 추가·제외가 실제 Git에서의 추가·삭제라고 단정할 수 없습니다.</div>' : ''}
    <ul class="cx-notes-list">
      <li>${esc(interp.file_matching || '')}</li>
      <li>${esc(interp.presence_changes || '')}</li>
      <li>${esc(interp.risk_delta || '')}</li>
    </ul>
    <div class="range-btns" id="cmp-filters" style="margin:10px 0;flex-wrap:wrap;">${filterBar}</div>
    <div style="font-size:var(--ui-text-xs);color:var(--text2);margin-bottom:6px;">변경 파일 ${num(pg.total_changes)}개 중 ${num(shown)}개 불러옴${more ? ' · 필터와 개수는 불러온 파일 안에서만 적용됩니다' : ''}</div>
    <div class="hm-wrap"><table class="hm-table">
      <thead><tr><th class="hm-th left">파일</th><th class="hm-th">상태</th><th class="hm-th">${esc(policy.scoreShort)} (시작 → 종료)</th><th class="hm-th">주요 수정자 수</th><th class="hm-th">함께 수정 정도</th></tr></thead>
      <tbody>${rows || `<tr><td class="hm-td" colspan="5" style="text-align:center;color:var(--text3);padding:20px;">${pg.total_changes ? '이 필터에 해당하는 파일이 없습니다.' : '변경된 파일이 없습니다. 두 결과의 파일 집합과 지표가 같습니다.'}</td></tr>`}</tbody>
    </table></div>
    ${more ? `<div style="text-align:center;margin-top:10px;"><button class="btn-secondary" id="cmp-more">변경 파일 더 보기 (${num(pg.total_changes - shown)}개 남음)</button></div>` : ''}`;

  out.querySelectorAll('[data-filter]').forEach(el => el.addEventListener('click', () => { CMP.filter = el.dataset.filter; renderCompare(); }));
  document.getElementById('cmp-more')?.addEventListener('click', () => runCompare(pg.next_offset));
}

/* ── 초기화 ── */
async function loadMore() {
  const btn = document.getElementById('hist-more');
  btn.disabled = true;
  try {
    const r = await fetchSnapshots(REPO_ID, { limit: PAGE, beforeId: NEXT_BEFORE });
    SNAPSHOTS = SNAPSHOTS.concat(r.snapshots || []);
    NEXT_BEFORE = r.next_before_id ?? null;
    fillSelects(true);
    renderList();
  } catch (err) { showError(err); }
  finally { btn.disabled = false; }
}

async function initHistoryPage() {
  const st = parseState(location.search);
  REPO_ID = getRepoId();
  CURRENT = st.repo === REPO_ID ? st.snapshot : null;

  request(`/repositories/${REPO_ID}`, { credentials: 'include' })
    .then(r => (r.ok ? r.json() : null))
    .then(r => { if (r && r.url) setText('hist-repo-title', r.url.replace('https://github.com/', '')); })
    .catch(() => {});

  const r = await fetchSnapshots(REPO_ID, { limit: PAGE });
  SNAPSHOTS = r.snapshots || [];
  NEXT_BEFORE = r.next_before_id ?? null;
  fillSelects(false);
  pickDefaults();

  const q = new URLSearchParams(location.search);
  const wantBefore = Number(q.get('before')), wantAfter = Number(q.get('after'));
  const restore = wantBefore > 0 && wantAfter > 0 && byId(wantBefore) && byId(wantAfter);
  if (restore) {
    document.getElementById('cmp-before').value = String(wantBefore);
    document.getElementById('cmp-after').value = String(wantAfter);
  }
  renderList();
  updateWarning();

  document.getElementById('hist-more').addEventListener('click', loadMore);
  document.getElementById('cmp-run').addEventListener('click', () => runCompare(0));
  for (const id of ['cmp-before', 'cmp-after']) {
    document.getElementById(id).addEventListener('change', () => { updateWarning(); renderList(); });
  }
  if (restore) runCompare(0);   // 주소에 비교 대상이 남아 있으면(새로고침·뒤로 가기) 같은 비교를 다시 보여 준다
}

if (tbody) {
  initHistoryPage().catch(err => {
    console.error('[history] 로드 실패:', err);
    tbody.innerHTML = '<tr><td class="hm-td" colspan="8" style="text-align:center;color:var(--text3);padding:24px;">불러오지 못했습니다.</td></tr>';
    setText('hist-count', '');
    showError(err);
  });
}
