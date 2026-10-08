/* ── 인수인계 화면 ──
   흐름: 인계 대상(파일 묶음) → 준비 체크폼 → 초안 생성 → 편집 → Markdown 내보내기.
   - 주소(?repo=&target=)가 유일한 기준이다. 서버가 판단하며 버튼 비활성은 안내일 뿐이다(권한·만료·버전은 서버 응답 코드로 안내).
   - 모든 동적 문자열은 textContent로만 넣는다(innerHTML에 데이터를 넣지 않는다). 링크는 저장·표시만 하고 열거나 읽지 않는다.
   - 미입력은 "미확인"이며, 준비 완료는 작성자 기록이다. AI 지적·미확인 내용은 이 화면 어디에서도 확정 사실로 쓰지 않는다. */
import { createAiClient } from './ai-api.js';
import * as S from './ai-state.js';
import * as HS from './ai-handover-state.js';

const api = createAiClient();
const guard = S.createGuard();
const $ = (id) => document.getElementById(id);
let route = HS.parseRoute(location.search);
let ctx = null;

/* ───────────────────────── DOM 도우미 ───────────────────────── */
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = String(v);
    else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, '');
    else e.setAttribute(k, String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    e.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return e;
}
const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };
const mount = (el, ...nodes) => { clear(el); nodes.flat().forEach((n) => n && el.append(n)); return el; };
const chip = (text, tone = 'info', title) => h('span', { class: `ai-chip ${tone}`, title }, text);
const fmtTime = (iso) => { const d = iso ? new Date(iso) : null; return d && !Number.isNaN(d.getTime()) ? d.toLocaleString('ko-KR') : '—'; };
const button = (text, onclick, { cls = 'btn-secondary', disabled = false, title } = {}) => h('button', { type: 'button', class: `${cls} ai-btn`, disabled, title, onclick }, text);
const link = (text, next, cls = '') => h('a', { class: `ai-link ${cls}`, href: `${location.pathname}${HS.buildRoute(next)}`,
  onclick: (ev) => { if (ev.metaKey || ev.ctrlKey || ev.shiftKey) return; ev.preventDefault(); navigate(next); } }, text);

function notice(kind, title, body, actions = []) {
  const el = h('div', { class: `ai-banner ${kind}`, role: kind === 'error' ? 'alert' : 'status' },
    h('div', { class: 'ai-banner-title', text: title }), body ? h('div', { class: 'ai-banner-body', text: body }) : null,
    actions.length ? h('div', { class: 'ai-banner-actions' }, actions) : null);
  $('ho-notice').append(el);
  return el;
}
const clearNotices = () => clear($('ho-notice'));

/* 오류 안내: 인수인계 전용 문구가 있으면 그것을, 없으면 공통 안내를 쓴다. */
function showError(err, { reload = null } = {}) {
  const special = HS.describeHandoverError(err);
  if (special) {
    const actions = [];
    if (reload && special.action && special.action.startsWith('reload')) actions.push(button('최신 내용 보기', reload, { cls: 'btn-primary' }));
    notice(special.tone === 'warn' ? 'warn' : 'error', special.title, special.body, actions);
    return special;
  }
  const d = S.describeError(err);
  const actions = [];
  if (d.action === 'login') actions.push(button('GitHub로 로그인', () => (window.githubLogin ? window.githubLogin() : (location.href = '/api/auth/github')), { cls: 'btn-primary' }));
  if (d.action === 'reload') actions.push(button('새로고침', () => location.reload()));
  if (d.action === 'retry' && reload) actions.push(button('다시 시도', reload));
  notice(d.tone === 'warn' ? 'warn' : 'error', d.title, d.body, actions);
  return d;
}

/* 버튼 하나의 요청 실행: 진행 중에는 다시 누를 수 없고, 실패해도 입력은 그대로 둔다. */
async function run(btn, fn) {
  if (btn.disabled) return;
  btn.disabled = true;
  try { await fn(); } finally { btn.disabled = false; }
}

/* ───────────────────────── 이동·세대 ───────────────────────── */
function navigate(next, { replace = false } = {}) {
  const url = `${location.pathname}${HS.buildRoute(next)}`;
  if (replace) history.replaceState(null, '', url); else history.pushState(null, '', url);
  return load();
}
window.addEventListener('popstate', () => { load(); });

async function load() {
  const token = guard.begin();
  route = HS.parseRoute(location.search);
  ctx = { token, repo: route.repo };
  clearNotices();
  mount($('ho-main'));
  $('ho-title').textContent = '인수인계';
  if (route.invalid.length) notice('warn', '주소의 일부 값을 무시했습니다', `올바르지 않은 값: ${route.invalid.join(', ')}`);
  let me;
  try { me = await api.me(token.signal); } catch (e) { if (e?.name === 'AbortError' || !token.current()) return; return showError(e, { reload: load }); }
  if (!token.current()) return;
  if (!me) {
    return mount($('ho-main'), h('section', { class: 'ai-card' }, h('h2', { text: '로그인이 필요합니다' }),
      button('GitHub로 로그인', () => (window.githubLogin ? window.githubLogin() : (location.href = '/api/auth/github')), { cls: 'btn-primary' })));
  }
  if (!route.repo) {
    return mount($('ho-main'), h('section', { class: 'ai-card' }, h('h2', { text: '저장소를 먼저 선택해 주세요' }),
      h('p', { class: 'ai-muted', text: '인수인계는 저장소마다 인계 대상을 정합니다. AI 리뷰 화면에서 프로젝트를 고른 뒤 이 화면으로 오세요.' }),
      h('a', { class: 'btn-secondary ai-btn ai-link', href: 'ai-review.html' }, 'AI 리뷰로 이동')));
  }
  try { await (route.target ? renderTarget(token) : renderTargets(token)); } catch (e) {
    if (e?.name === 'AbortError' || !token.current()) return;
    showError(e, { reload: load });
  }
}

/* ───────────────────────── 대상 목록·만들기 ───────────────────────── */
async function snapshotOptions(token) {
  try { return (await api.historyOptions(route.repo, token.signal)).snapshots.filter((s) => s.eligible); } catch (e) { if (e?.name === 'AbortError') throw e; return []; }
}

function targetForm({ initial = null, snapshots, onSubmit, submitLabel }) {
  const name = h('input', { class: 'ai-input', type: 'text', maxlength: 120, 'aria-label': '대상 이름', placeholder: '예: 로그인 기능 인계' });
  const paths = h('textarea', { class: 'ai-input', rows: 5, 'aria-label': '대상 파일', placeholder: '파일 경로를 한 줄에 하나씩 (예: src/login.ts)' });
  const purpose = h('input', { class: 'ai-input', type: 'text', maxlength: 500, 'aria-label': '인계 목적', placeholder: '인계 목적(선택)' });
  const giver = h('input', { class: 'ai-input', type: 'text', maxlength: 100, 'aria-label': '인계자 메모', placeholder: '인계자 이름(메모, 선택)' });
  const receiver = h('input', { class: 'ai-input', type: 'text', maxlength: 100, 'aria-label': '인수자 메모', placeholder: '인수자 이름(메모, 선택)' });
  const snapshot = h('select', { class: 'ai-input', 'aria-label': '분석 snapshot' }, h('option', { value: '', text: '분석 snapshot 지정 안 함 (이력 근거 없음)' }),
    snapshots.map((s) => h('option', { value: String(s.snapshot_id), text: `#${s.snapshot_id} · SHA ${S.shortSha(s.target_sha)} · ${fmtTime(s.analyzed_at)}` })));
  if (initial) {
    name.value = initial.name; paths.value = initial.selected_paths.join('\n'); purpose.value = initial.purpose || '';
    giver.value = initial.giver_name || ''; receiver.value = initial.receiver_name || '';
    if (initial.snapshot_id && ![...snapshot.options].some((o) => o.value === String(initial.snapshot_id))) {
      snapshot.append(h('option', { value: String(initial.snapshot_id), text: `#${initial.snapshot_id} (현재 지정됨)` }));
    }
    snapshot.value = initial.snapshot_id ? String(initial.snapshot_id) : '';
  }
  const status = h('div', { class: 'ai-inline', role: 'status' });
  const submit = button(submitLabel, async () => {
    const parsed = HS.parsePaths(paths.value);
    if (!name.value.trim()) { status.textContent = '이름을 입력해 주세요.'; return; }
    if (parsed.errors.length) { status.textContent = parsed.errors[0]; return; }
    status.textContent = '';
    await run(submit, () => onSubmit({ name: name.value.trim(), selected_paths: parsed.paths, purpose: purpose.value.trim() || null, giver_name: giver.value.trim() || null,
      receiver_name: receiver.value.trim() || null, snapshot: snapshot.value ? Number(snapshot.value) : null }, status));
  }, { cls: 'btn-primary' });
  return h('div', { class: 'ho-form' }, h('label', { text: '이름' }), name, h('label', { text: '대상 파일 (한 줄에 하나)' }), paths,
    h('p', { class: 'ai-muted', text: '기능 이름은 직접 적은 설명을 씁니다. 파일 이름만 보고 기능을 확정하지 않습니다.' }),
    h('label', { text: '메모(선택)' }), purpose, h('div', { class: 'ai-row' }, giver, receiver),
    h('label', { text: '이력 근거로 쓸 분석 snapshot' }), snapshot,
    h('p', { class: 'ai-muted', text: '검증을 통과한 snapshot만 고를 수 있습니다. 지정하지 않으면 이력에 대해 아무것도 확인하지 않은 것으로 적습니다.' }), h('div', { class: 'ai-row' }, submit), status);
}

async function renderTargets(token) {
  const [list, snapshots] = await Promise.all([api.handoverTargets(route.repo, token.signal), snapshotOptions(token)]);
  if (!token.current()) return;
  const items = list.targets.length ? h('ul', { class: 'ho-targets' }, list.targets.map((t) => h('li', {},
    link(t.name, { repo: route.repo, target: t.target_id }), ' ', chip(`파일 ${t.selected_paths.length}개`, 'info'), ' ',
    t.needs_reconfirm ? chip('재확인 필요', 'warn', '대상 범위가 바뀐 뒤 준비 정보를 다시 확인하지 않았습니다') : null, ' ',
    t.has_document ? chip('문서 있음', 'ok') : null)))
    : h('p', { class: 'ai-muted', text: '아직 인계 대상이 없습니다. 아래에서 인계할 파일 묶음을 만들어 주세요.' });
  const form = targetForm({ snapshots, submitLabel: '인계 대상 만들기', onSubmit: async (v, status) => {
    try {
      const t = await api.createHandoverTarget({ repository_id: route.repo, name: v.name, selected_paths: v.selected_paths, purpose: v.purpose, giver_name: v.giver_name,
        receiver_name: v.receiver_name, snapshot_id: v.snapshot }, token.signal);
      if (token.current()) navigate({ repo: route.repo, target: t.target_id });
    } catch (e) { if (e?.name !== 'AbortError' && token.current()) { status.textContent = ''; showError(e); } }
  } });
  mount($('ho-main'), h('section', { class: 'ai-card' }, h('h2', { text: '인계 대상' }), items), h('section', { class: 'ai-card' }, h('h2', { text: '새 인계 대상' }), form));
}

/* ───────────────────────── 대상 화면 ───────────────────────── */
async function fetchTarget(token) {
  const [target, prep] = await Promise.all([api.handoverTarget(route.target, token.signal), api.preparation(route.target, token.signal)]);
  let doc = null;
  try { doc = await api.handoverDocument(route.target, token.signal); } catch (e) { if (e?.code !== 'RESOURCE_NOT_FOUND') throw e; }
  return { target, prep, doc };
}

async function renderTarget(token) {
  const data = await fetchTarget(token);
  if (!token.current()) return;
  if (data.target.repository_id !== route.repo) throw Object.assign(new Error('x'), { status: 404, code: 'RESOURCE_NOT_FOUND', details: {} });   // 주소와 다른 저장소의 응답은 그리지 않는다
  const [snapshots, jobs] = await Promise.all([snapshotOptions(token),
    api.jobs(route.repo, { limit: 50, status: 'succeeded' }, token.signal).then((r) => r.jobs).catch((e) => { if (e?.name === 'AbortError') throw e; return []; })]);
  if (!token.current()) return;
  ctx.data = data;
  ctx.snapshots = snapshots;
  ctx.jobs = jobs;
  drawTarget();
}

async function refresh() {
  const token = ctx.token;
  try { ctx.data = await fetchTarget(token); } catch (e) { if (e?.name !== 'AbortError' && token.current()) showError(e); return; }
  if (token.current()) drawTarget();
}

function drawTarget() {
  const { target } = ctx.data;
  $('ho-title').textContent = `인수인계 · ${target.name}`;
  mount($('ho-main'), h('p', {}, link('← 인계 대상 목록', { repo: route.repo })), targetCard(), preparationCard(), generationCard(), documentCard());
}

function targetCard() {
  const { target } = ctx.data;
  const body = h('div', {});
  const view = () => mount(body,
    h('dl', { class: 'ai-dl' },
      h('dt', { text: '대상 파일' }), h('dd', {}, h('ul', {}, target.selected_paths.map((p) => h('li', {}, h('code', { text: p }))))),
      target.purpose ? [h('dt', { text: '목적' }), h('dd', { text: target.purpose })] : null,
      target.giver_name ? [h('dt', { text: '인계자(메모)' }), h('dd', { text: target.giver_name })] : null,
      target.receiver_name ? [h('dt', { text: '인수자(메모)' }), h('dd', { text: target.receiver_name })] : null,
      h('dt', { text: '분석 snapshot' }), h('dd', { text: target.snapshot_id ? `#${target.snapshot_id}` : '지정 안 함 (이력 근거 없음)' })),
    h('p', { class: 'ai-muted', text: `대상 버전 ${target.version} · 수정 ${fmtTime(target.updated_at)} · 이름은 메모이며 역할이나 권한을 주지 않습니다.` }),
    h('div', { class: 'ai-row' }, button('대상 수정', edit), button('대상 삭제', remove, { cls: 'btn-secondary danger' })));
  function edit() {
    mount(body, targetForm({ initial: target, snapshots: ctx.snapshots, submitLabel: '저장', onSubmit: async (v, status) => {
      const patch = { expected_version: target.version, name: v.name, selected_paths: v.selected_paths, purpose: v.purpose || '', giver_name: v.giver_name || '', receiver_name: v.receiver_name || '' };
      if (v.snapshot) patch.snapshot_id = v.snapshot; else patch.clear_snapshot = true;
      try { await api.patchHandoverTarget(target.target_id, patch, ctx.token.signal); await refresh(); }
      catch (e) { if (e?.name !== 'AbortError' && ctx.token.current()) { status.textContent = ''; showError(e, { reload: refresh }); if (e?.code === 'VERSION_CONFLICT') await refresh(); } }
    } }), h('div', { class: 'ai-row' }, button('취소', view)));
  }
  async function remove() {
    if (!window.confirm('이 인계 대상과 준비 정보·초안·문서를 모두 삭제할까요? 되돌릴 수 없습니다.')) return;
    try { await api.deleteHandoverTarget(target.target_id, ctx.token.signal); navigate({ repo: route.repo }); } catch (e) { if (e?.name !== 'AbortError') showError(e); }
  }
  view();
  return h('section', { class: 'ai-card' }, h('h2', { text: '인계 대상' }), body);
}

/* ── 준비 체크폼 ── */
function preparationCard() {
  const { prep, target } = ctx.data;
  const state = HS.itemsFromView(prep);
  const rows = HS.ITEM_KEYS.map((key) => {
    const status = h('select', { class: 'ai-input', 'aria-label': `${HS.ITEM_LABEL[key]} 상태` }, HS.STATUS_OPTIONS.map(([v, t]) => h('option', { value: v, text: t })));
    const text = h('textarea', { class: 'ai-input', rows: 2, maxlength: 2000, 'aria-label': `${HS.ITEM_LABEL[key]} 내용`, placeholder: '내용(2000자까지)' });
    const url = h('input', { class: 'ai-input', type: 'text', maxlength: 500, 'aria-label': `${HS.ITEM_LABEL[key]} 링크`, placeholder: '문서 링크(http/https, 저장만 함)' });
    const problem = h('div', { class: 'ai-inline ho-problem', role: 'status' });
    status.value = state[key].status; text.value = state[key].text; url.value = state[key].link;
    const sync = () => {
      state[key] = { status: status.value, text: text.value, link: url.value };
      problem.textContent = HS.itemProblem(key, state[key]) || '';
    };
    [status, text, url].forEach((el) => el.addEventListener('input', sync));
    const item = prep.items.find((i) => i.key === key);
    return h('div', { class: 'ho-item', 'data-item': key }, h('div', { class: 'ai-row between' }, h('b', { text: HS.ITEM_LABEL[key] }),
      item.updated_version ? h('span', { class: 'ai-muted', text: `v${item.updated_version}에서 변경 · ${fmtTime(item.updated_at)}` }) : chip('미확인', 'info')),
    h('div', { class: 'ai-muted', text: HS.ITEM_HINT[key] }), status, text, url, problem);
  });
  const summary = h('p', { class: 'ai-muted', text: HS.preparationSummary(prep) });
  const status = h('div', { class: 'ai-inline', role: 'status' });
  const save = button(prep.needs_reconfirm ? '대상을 확인하고 저장' : '준비 정보 저장', async () => {
    const problems = HS.preparationProblems(state);
    if (Object.keys(problems).length) { status.textContent = `${HS.ITEM_LABEL[Object.keys(problems)[0]]}: ${Object.values(problems)[0]}`; return; }
    status.textContent = '';
    await run(save, async () => {
      clearNotices();
      try {
        await api.savePreparation(target.target_id, HS.buildPreparationBody({ expectedVersion: prep.version, targetVersion: target.version, items: state }), ctx.token.signal);
        await refresh();
        if (ctx.token.current()) notice('ok', '준비 정보를 저장했습니다', '입력하지 않은 항목은 미확인으로 남습니다.');
      } catch (e) {
        if (e?.name === 'AbortError' || !ctx.token.current()) return;
        showError(e, { reload: refresh });
        if (e?.code === 'VERSION_CONFLICT') await refresh();
      }
    });
  }, { cls: 'btn-primary' });
  return h('section', { class: 'ai-card', id: 'ho-prep' }, h('h2', { text: '준비 체크폼' }),
    prep.needs_reconfirm ? h('div', { class: 'ai-banner warn' }, h('div', { class: 'ai-banner-title', text: '대상 범위가 바뀌었습니다' }),
      h('div', { class: 'ai-banner-body', text: '이전 입력은 보존돼 있지만 새 대상에 대한 확인은 아닙니다. 내용을 확인한 뒤 "대상을 확인하고 저장"을 눌러 주세요.' })) : null,
    h('p', { class: 'ai-muted', text: `${HS.READY_NOTE} ${HS.LINK_NOTE}` }), summary,
    prep.version ? h('p', { class: 'ai-muted', text: `준비 정보 버전 ${prep.version} (대상 버전 ${prep.target_version} 기준) · ${fmtTime(prep.created_at)}` }) : h('p', { class: 'ai-muted', text: '아직 저장된 준비 정보가 없습니다. 모든 항목은 미확인입니다.' }),
    h('div', { class: 'ho-items' }, rows), h('div', { class: 'ai-row' }, save), status);
}

/* ── 초안 생성 ── */
function resultSelect(kind, label) {
  const opts = HS.resultOptions(ctx.jobs, kind);
  const sel = h('select', { class: 'ai-input', 'aria-label': label }, h('option', { value: '', text: `${label} 선택 안 함` }),
    opts.map((o) => h('option', { value: o.resultId, text: o.label })));
  return sel;
}

function generationCard() {
  const { target, prep, doc } = ctx.data;
  const summary = resultSelect('summary', 'AI 변경 설명');
  const review = resultSelect('review', 'AI 코드 리뷰');
  const status = h('div', { class: 'ai-inline', role: 'status' });
  const make = (force) => async () => {
    clearNotices();
    status.textContent = '';
    try {
      const out = await api.generateHandover(target.target_id, { expected_target_version: target.version, expected_preparation_version: prep.version,
        summary_result_id: summary.value || null, review_result_id: review.value || null, force }, ctx.token.signal);
      await refresh();
      if (!ctx.token.current()) return;
      if (out.generation.reused) notice('info', '기준이 같아 기존 초안을 그대로 사용합니다', '같은 기준으로 새 원본이 필요하면 "새 원본 만들기"를 눌러 주세요.');
      else notice('ok', `초안 #${out.generation.number}을 만들었습니다`, out.document_updated ? '편집하지 않은 문서는 새 초안을 따라갑니다.' : '편집한 문서는 덮어쓰지 않았습니다. 필요하면 문서 영역에서 새 원본으로 바꿀 수 있습니다.');
    } catch (e) {
      if (e?.name === 'AbortError' || !ctx.token.current()) return;
      showError(e, { reload: refresh });
      if (e?.code === 'VERSION_CONFLICT') await refresh();
    }
  };
  const first = button(doc ? '초안 생성' : '초안 만들기', null, { cls: 'btn-primary' });
  first.addEventListener('click', () => run(first, make(false)));
  const again = button('새 원본 만들기', null);
  again.addEventListener('click', () => run(again, make(true)));
  return h('section', { class: 'ai-card', id: 'ho-gen' }, h('h2', { text: '초안 생성' }), h('p', { class: 'ai-muted', text: HS.DRAFT_NOTE }),
    h('p', { class: 'ai-muted', text: '선택한 대상 버전·준비 정보 버전·AI 결과·피드백 기준이 초안에 고정됩니다. 선택하지 않은 자료는 "확인되지 않음"으로 적습니다.' }),
    h('div', { class: 'ai-row' }, summary, review), h('div', { class: 'ai-row' }, first, doc ? again : null), status);
}

/* ── 편집 문서 ── */
function documentCard() {
  const { doc } = ctx.data;
  if (!doc) return h('section', { class: 'ai-card', id: 'ho-doc' }, h('h2', { text: '인수인계 문서' }), h('p', { class: 'ai-muted', text: '아직 문서가 없습니다. 먼저 초안을 만들어 주세요.' }));
  const editor = h('textarea', { class: 'ai-input ho-editor', rows: 24, 'aria-label': '인수인계 문서(Markdown)', spellcheck: 'false' });
  editor.value = doc.markdown;
  const status = h('div', { class: 'ai-inline', role: 'status' });
  const count = h('span', { class: 'ai-muted' });
  const dirty = () => editor.value !== doc.markdown;
  const sync = () => { count.textContent = `${editor.value.length.toLocaleString('ko-KR')}자 / 100,000자${dirty() ? ' · 저장하지 않은 변경 있음' : ''}`; };
  editor.addEventListener('input', sync);
  sync();
  const save = button('문서 저장', async () => {
    await run(save, async () => {
      status.textContent = '';
      clearNotices();
      try {
        await api.saveHandoverDocument(doc.document_id, { expected_version: doc.version, markdown: editor.value }, ctx.token.signal);
        await refresh();
        if (ctx.token.current()) notice('ok', '문서를 저장했습니다', '내보내기에는 저장된 내용이 쓰입니다.');
      } catch (e) {
        if (e?.name === 'AbortError' || !ctx.token.current()) return;
        const mine = editor.value;
        showError(e, { reload: refresh });
        if (e?.code === 'VERSION_CONFLICT') { await refresh(); const box = document.querySelector('.ho-editor'); if (box) mount($('ho-lost'), lostEdit(mine)); }
      }
    });
  }, { cls: 'btn-primary' });
  const stale = HS.staleLines(doc.stale_reasons);
  const exportLink = h('a', { class: 'btn-secondary ai-btn ai-link', href: HS.exportUrl(doc.document_id), download: '' }, 'Markdown 내보내기');
  exportLink.addEventListener('click', (ev) => { if (dirty() && !window.confirm('저장하지 않은 변경은 내보내기에 포함되지 않습니다. 저장된 문서를 내보낼까요?')) ev.preventDefault(); });
  const adopt = doc.newer_generation_id ? h('div', { class: 'ai-banner info' }, h('div', { class: 'ai-banner-title', text: `더 새로운 원본(#${doc.newer_generation_number})이 있습니다` }),
    h('div', { class: 'ai-banner-body', text: `이 문서는 원본 #${doc.base_generation_number} 기준입니다. 내 편집 내용은 자동으로 덮어쓰지 않습니다. 새 원본으로 바꾸면 현재 문서는 편집 이력에 남습니다.` }),
    h('div', { class: 'ai-banner-actions' }, button('새 원본으로 바꾸기', async (ev) => {
      if (!window.confirm('문서를 새 원본으로 바꿀까요? 지금 내용은 편집 이력에 남습니다.')) return;
      await run(ev.currentTarget, async () => {
        try { await api.adoptHandoverGeneration(doc.document_id, { expected_version: doc.version, generation_id: doc.newer_generation_id }, ctx.token.signal); await refresh(); }
        catch (e) { if (e?.name !== 'AbortError' && ctx.token.current()) { showError(e, { reload: refresh }); if (e?.code === 'VERSION_CONFLICT') await refresh(); } }
      });
    }))) : null;
  return h('section', { class: 'ai-card', id: 'ho-doc' }, h('h2', { text: '인수인계 문서' }),
    h('p', { class: 'ai-muted', text: `문서 버전 ${doc.version} · 원본 #${doc.base_generation_number} 기준 · ${doc.edited ? '편집됨' : '편집하지 않음'} · ${fmtTime(doc.updated_at)}` }),
    stale.length ? h('div', { class: 'ai-banner warn' }, h('div', { class: 'ai-banner-title', text: '이 문서의 기준이 바뀌었습니다' }), h('ul', {}, stale.map((l) => h('li', { text: l }))),
      h('div', { class: 'ai-banner-body', text: '내용은 그대로 두었습니다. 최신 기준이 필요하면 초안을 새로 만들어 비교해 보세요.' })) : null,
    adopt, h('div', { id: 'ho-lost' }), editor, h('div', { class: 'ai-row between' }, count, h('span', { class: 'ai-muted', text: 'HTML 태그·이미지·위험한 링크는 저장되지 않습니다.' })),
    h('div', { class: 'ai-row' }, save, exportLink), status);
}

/* 문서 버전 충돌로 최신 문서를 불러온 뒤에도 내가 쓴 내용을 잃지 않도록 따로 보여 준다. */
function lostEdit(text) {
  return h('div', { class: 'ai-banner warn' }, h('div', { class: 'ai-banner-title', text: '저장하지 못한 내 편집 내용' }),
    h('textarea', { class: 'ai-input', rows: 8, readonly: true, 'aria-label': '저장하지 못한 내 편집 내용' }, text),
    h('div', { class: 'ai-banner-actions' }, button('이 내용으로 편집창 채우기', () => { const box = document.querySelector('.ho-editor'); if (box) { box.value = text; box.dispatchEvent(new Event('input')); } })));
}

/* ───────────────────────── 시작 ───────────────────────── */
load();
