/* ── AI 리뷰 화면 ──
   흐름: 키·모델(A) → PR 선택(B) → 파일 선택(C) → 미리보기·전송 동의(D) → 실행(E) → 결과·피드백(F).
   - 주소(?repo=&pr=&input=&job=&retry=&force=)가 유일한 기준이다. 저장된 값·기본 저장소를 쓰지 않는다.
   - 화면 이동마다 세대(guard)를 올려 이전 요청을 abort하고, 늦게 온 응답은 버린다. 응답의 repository_id가 주소와 다르면 그리지 않는다.
   - 모든 동적 문자열은 textContent로만 넣는다(innerHTML에 데이터를 넣지 않는다). 링크는 이 화면의 내부 주소만 만든다.
   - 서버가 판단한다: 버튼 비활성은 안내일 뿐이고, 허용·만료·권한은 서버 응답 코드로 안내한다. */
import { createAiClient, newIdempotencyKey } from './ai-api.js';
import { createAiPoller } from './ai-poll.js';
import * as S from './ai-state.js';

const api = createAiClient();
const guard = S.createGuard();
const intents = {
  input: S.createIntent(newIdempotencyKey), job: S.createIntent(newIdempotencyKey), retry: S.createIntent(newIdempotencyKey),
};

const $ = (id) => document.getElementById(id);
let route = S.parseRoute(location.search);
let poller = null;
let clockTimer = null;
let ctx = null;                 // 현재 화면 세대의 상태(저장소·작업 단위). 세대가 바뀌면 통째로 버린다.
const shared = { me: null, providers: null, creds: null, usage: null, aiDisabled: false, keysOpen: false, keyMsg: '' };

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

/* 내부 링크만 만든다(검증된 정수·uuid로 만든 주소) */
const link = (text, next, cls = '') => h('a', { class: `ai-link ${cls}`, href: `${location.pathname}${S.buildRoute(next)}`,
  onclick: (ev) => { if (ev.metaKey || ev.ctrlKey || ev.shiftKey) return; ev.preventDefault(); navigate(next); } }, text);

const button = (text, onclick, { cls = 'btn-secondary', disabled = false, title } = {}) =>
  h('button', { type: 'button', class: `${cls} ai-btn`, disabled, title, onclick }, text);

function banner(kind, title, body, actions = []) {
  const box = $('ai-notice');
  const el = h('div', { class: `ai-banner ${kind}`, role: kind === 'error' ? 'alert' : 'status' },
    h('div', { class: 'ai-banner-title', text: title }), body ? h('div', { class: 'ai-banner-body', text: body }) : null,
    actions.length ? h('div', { class: 'ai-banner-actions' }, actions) : null);
  box.append(el);
  return el;
}
const clearNotices = () => clear($('ai-notice'));

/* 오류 안내 + 행동 버튼. onRetry가 있으면 "다시 시도"를 붙인다. */
function showError(err, { onRetry = null } = {}) {
  const d = S.describeError(err);
  const actions = [];
  if (d.action === 'login') actions.push(button('GitHub로 로그인', () => (window.githubLogin ? window.githubLogin() : (location.href = '/api/auth/github')), { cls: 'btn-primary' }));
  if (d.action === 'keys') actions.push(button('API key 설정 열기', () => openKeys()));
  if (d.action === 'usage') actions.push(button('사용량 보기', () => { openKeys(); $('ai-usage')?.scrollIntoView?.({ block: 'center' }); }));
  if (d.action === 'reload') actions.push(button('새로고침', () => location.reload()));
  if (d.action === 'home' || d.action === 'reselect') actions.push(link('PR 선택으로', { repo: route.repo }, 'btn-secondary ai-btn'));
  if (d.action === 'reload_preview' && ctx?.reloadPreview) actions.push(button('미리보기 다시 확인', () => ctx.reloadPreview(), { cls: 'btn-primary' }));
  if ((d.action === 'retry' || d.action === 'reload_findings') && onRetry) actions.push(button('다시 시도', onRetry));
  let body = d.body;
  if (d.retryAfter) body += ` (${S.fmtDuration(d.retryAfter / 1000)} 뒤에 다시 시도할 수 있습니다)`;
  banner(d.tone === 'warn' ? 'warn' : 'error', d.title, body, actions);
  return d;
}

/* ───────────────────────── 이동·세대 ───────────────────────── */
function navigate(next, { replace = false } = {}) {
  const url = `${location.pathname}${S.buildRoute(next)}`;
  if (replace) history.replaceState(null, '', url); else history.pushState(null, '', url);
  return load();
}

function stopAll() {
  if (poller) { poller.stop(); poller = null; }
  if (clockTimer) { clearInterval(clockTimer); clockTimer = null; }
}

async function load() {
  const token = guard.begin();                         // 이전 세대의 요청 abort·응답 폐기
  stopAll();
  route = S.parseRoute(location.search);
  ctx = { token, repo: route.repo };                   // 저장소·작업 단위 상태는 매번 새로 시작한다
  clearNotices();
  shared.keyMsg = '';
  mount($('ai-scope'));
  mount($('ai-main'));
  renderHeader();
  if (route.view === S.VIEWS.NO_REPO) return renderNoRepo();
  if (route.invalid.length) banner('warn', '주소의 일부 값을 무시했습니다', `올바르지 않은 값: ${route.invalid.join(', ')}`);
  try {
    shared.me = await api.me(token.signal);
  } catch (e) {
    if (e?.name === 'AbortError' || !token.current()) return;
    return showError(e, { onRetry: load });
  }
  if (!token.current()) return;
  if (!shared.me) return renderLogin();
  ctx.repoInfo = null;
  api.repository(route.repo, token.signal).then((r) => {
    if (!token.current()) return;
    ctx.repoInfo = r;
    renderHeader();
  }).catch(() => { /* 이름을 못 읽어도 화면은 계속된다 */ });
  await loadKeys(token);
  if (!token.current() || shared.aiDisabled) return;
  const views = {
    [S.VIEWS.HOME]: renderHome, [S.VIEWS.FILES]: renderFiles, [S.VIEWS.PREVIEW]: renderPreview, [S.VIEWS.JOB]: renderJob,
  };
  await views[route.view](token);
}

window.addEventListener('popstate', () => { load(); });
document.addEventListener('visibilitychange', () => { if (!document.hidden && poller) poller.nudge(); });
window.addEventListener('pagehide', stopAll);

/* ───────────────────────── 머리·상태 화면 ───────────────────────── */
function repoLabel() {
  const m = (ctx?.repoInfo?.url || '').match(/github\.com\/([^/]+\/[^/]+?)(?:\.git)?\/?$/);
  return (m && m[1]) || ctx?.repoInfo?.name || (route.repo ? `저장소 #${route.repo}` : '저장소 미선택');
}

function renderHeader() {
  const title = $('ai-title');
  title.textContent = route.repo ? `AI 리뷰 · ${repoLabel()}` : 'AI 리뷰';
  const steps = [['home', 'PR 선택'], ['files', '파일 선택'], ['preview', '미리보기·동의'], ['job', '실행·결과']];
  const at = { [S.VIEWS.HOME]: 0, [S.VIEWS.FILES]: 1, [S.VIEWS.PREVIEW]: 2, [S.VIEWS.JOB]: 3 }[route.view];
  mount($('ai-steps'), steps.map(([, label], i) => h('li', { class: `ai-step${i === at ? ' current' : i < at ? ' done' : ''}`, 'aria-current': i === at ? 'step' : null }, `${i + 1}. ${label}`)));
  $('ai-steps').hidden = route.view === S.VIEWS.NO_REPO;
}

function renderNoRepo() {
  mount($('ai-main'), h('div', { class: 'ai-card' },
    h('h2', { text: '저장소를 먼저 선택하세요' }),
    h('p', { class: 'ai-muted', text: 'AI 리뷰는 저장소를 명시해야 시작할 수 있습니다. 이전에 보던 저장소나 기본 저장소를 대신 쓰지 않습니다.' }),
    h('div', { class: 'ai-row' },
      h('a', { class: 'btn-primary ai-btn', href: './myprojects.html', text: '내 분석 프로젝트에서 고르기' }),
      h('a', { class: 'btn-secondary ai-btn', href: './search.html', text: '저장소 검색' }))));
}

function renderLogin() {
  mount($('ai-main'), h('div', { class: 'ai-card' },
    h('h2', { text: '로그인이 필요합니다' }),
    h('p', { class: 'ai-muted', text: 'GitHub로 로그인하면 이 저장소의 PR을 선택해 AI 리뷰를 받을 수 있습니다. 로그인하면 이 주소로 돌아옵니다.' }),
    button('GitHub로 로그인', () => (window.githubLogin ? window.githubLogin() : (location.href = '/api/auth/github')), { cls: 'btn-primary' })));
}

/* ───────────────────────── A. 키·모델 ───────────────────────── */
function openKeys() {
  shared.keysOpen = true;
  const panel = $('ai-keys');
  const d = panel.querySelector('details');
  if (d) { d.open = true; d.scrollIntoView?.({ block: 'start' }); }
}

async function loadKeys(token) {
  try {
    const [providers, creds] = await Promise.all([api.providers(token.signal), api.credentials(token.signal)]);
    if (!token.current()) return;
    shared.providers = providers; shared.creds = creds.credentials;
  } catch (e) {
    if (e?.name === 'AbortError' || !token.current()) return;
    if (e.status === 404 || e.code === 'RESOURCE_NOT_FOUND') {
      shared.aiDisabled = true;
      mount($('ai-main'), h('div', { class: 'ai-card' }, h('h2', { text: 'AI 기능을 쓸 수 없습니다' }),
        h('p', { class: 'ai-muted', text: '이 서버에서는 AI 기능이 꺼져 있거나 사용할 수 없습니다.' })));
      return;
    }
    return showError(e, { onRetry: load });
  }
  shared.aiDisabled = false;
  api.usage(token.signal).then((u) => { if (token.current()) { shared.usage = u; renderKeys(); } }).catch(() => {});
  renderKeys();
}

function currentCredential() { return (shared.creds || []).find((c) => c.provider === 'anthropic') || null; }

function renderKeys() {
  const root = $('ai-keys');
  const provider = shared.providers?.providers?.[0];
  if (!provider) return mount(root);
  const cred = currentCredential();
  const transferOk = shared.providers.external_transfer_allowed;
  const keyInput = h('input', { type: 'password', id: 'ai-key-input', autocomplete: 'off', spellcheck: 'false', placeholder: 'sk-ant-…', 'aria-label': 'API key', maxlength: 400 });
  let selected = cred?.preferred_profile_id || provider.models.find((m) => m.allowed)?.profile_id || null;
  const msg = h('div', { class: 'ai-inline', role: 'status' }, shared.keyMsg);
  const models = h('div', { class: 'ai-models' }, provider.models.map((m) => {
    const radio = h('input', { type: 'radio', name: 'ai-model', value: m.profile_id, checked: m.profile_id === selected, disabled: !m.allowed,
      onchange: () => { selected = m.profile_id; refreshButtons(); } });
    return h('label', { class: `ai-model${m.allowed ? '' : ' disabled'}` }, radio,
      h('span', { class: 'ai-model-main' }, h('b', { text: m.display_name || m.model }), h('code', { text: m.model })),
      h('span', { class: 'ai-model-meta' }, `입력 $${m.input_price_per_mtok} / 출력 $${m.output_price_per_mtok} (100만 토큰) · 입력 한도 ${S.fmtTokens(m.max_input_tokens)} · 출력 한도 ${S.fmtTokens(m.max_output_tokens)}`),
      m.allowed ? null : chip('관리자가 허용하지 않음', 'warn'));
  }));
  const save = button(cred ? 'key 교체(확인 후 저장)' : 'key 확인 후 저장', async () => {
    const value = keyInput.value;
    keyInput.value = '';                                        // 제출 즉시 화면에서 지운다
    if (!value.trim()) { msg.textContent = 'API key를 입력하세요.'; return; }
    await run(save, msg, async () => {
      const out = await api.putCredential('anthropic', { apiKey: value, profileId: selected });
      shared.keyMsg = out.changed ? `저장했습니다 (${out.credential.key_hint}, v${out.credential.version}).` : '같은 key입니다. 변경 없음.';
      await refreshCreds();
    });
  }, { cls: 'btn-primary', disabled: !transferOk });
  const setModel = button('기본 모델만 변경', async () => {
    await run(setModel, msg, async () => { await api.setPreferredProfile('anthropic', selected); shared.keyMsg = '기본 모델을 바꿨습니다.'; await refreshCreds(); });
  });
  const del = button('key 삭제', async () => {
    const n = cred?.in_flight_jobs || 0;
    if (!window.confirm(`API key를 삭제합니다. 이 key에 고정된 진행 중 작업 ${n}개가 취소됩니다. 계속할까요?`)) return;
    await run(del, msg, async () => { const out = await api.deleteCredential('anthropic'); shared.keyMsg = `삭제했습니다(취소된 작업 ${out.jobs_cancelled}개).`; await refreshCreds(); });
  }, { cls: 'btn-secondary danger' });
  function refreshButtons() { setModel.disabled = !cred || selected === cred.preferred_profile_id || !selected; }
  refreshButtons();

  const status = cred
    ? [chip('key 등록됨', 'ok'), h('code', { text: cred.key_hint }), ` v${cred.version} · 기본 모델 ${cred.preferred_profile_id || '—'} · 확인 ${cred.validated_at ? fmtTime(cred.validated_at) : '안 함'}`,
      cred.in_flight_jobs ? chip(`진행 중 작업 ${cred.in_flight_jobs}개`, 'info') : null]
    : [chip('key 없음', 'warn'), ' 코드를 보내기 전에 본인의 API key를 등록해야 합니다.'];
  const usage = shared.usage;
  mount(root, h('details', { class: 'ai-card ai-keys', open: shared.keysOpen || !cred, ontoggle: (ev) => { shared.keysOpen = ev.target.open; } },
    h('summary', {}, h('b', { text: 'API key·제공자·모델' }), ' ', ...status),
    h('div', { class: 'ai-keys-body' },
      !transferOk ? h('div', { class: 'ai-banner warn' }, h('div', { class: 'ai-banner-title', text: '이 서버는 코드의 외부 전송을 허용하지 않습니다' }),
        h('div', { class: 'ai-banner-body', text: 'key 등록과 AI 실행이 막혀 있습니다.' })) : null,
      h('p', { class: 'ai-muted', text: `제공자: ${provider.display_name || provider.id}. key는 암호화되어 저장되며 화면에는 마스킹된 힌트만 표시됩니다. key 확인은 코드를 보내지 않고 비용이 들지 않습니다. 사용한 만큼의 비용은 본인 key 계정에 청구됩니다.` }),
      models,
      h('label', { class: 'ai-field' }, h('span', { text: cred ? '새 API key(교체할 때만)' : 'API key' }), keyInput),
      h('div', { class: 'ai-row' }, save, cred ? setModel : null, cred ? del : null),
      msg,
      usage ? h('div', { class: 'ai-usage', id: 'ai-usage' },
        `이번 달 사용 ${S.fmtMoney(usage.user_spent, usage.currency)} / 한도 ${S.fmtMoney(usage.user_limit, usage.currency)} (예약 ${S.fmtMoney(usage.user_reserved, usage.currency)}, 남음 ${S.fmtMoney(usage.user_remaining, usage.currency)})`
        + ` · 오늘 요청 ${usage.daily_jobs_used}/${usage.daily_job_limit}`
        + (usage.uncertain_reservations ? ` · 결과를 알 수 없는 호출 ${usage.uncertain_reservations}건(비용이 발생했을 수 있음)` : '')) : null)));
}

async function refreshCreds() {
  const token = ctx.token;
  try {
    const [creds, usage] = await Promise.all([api.credentials(token.signal), api.usage(token.signal).catch(() => null)]);
    if (!token.current()) return;
    shared.creds = creds.credentials; shared.usage = usage; shared.keysOpen = true;
    renderKeys();
    ctx.onCredentialsChanged?.();
  } catch (e) { if (e?.name !== 'AbortError' && token.current()) showError(e); }
}

/* 버튼 하나의 중복 클릭을 막고 오류를 안내한다 */
async function run(btn, msgEl, fn) {
  if (btn.disabled) return;
  btn.disabled = true;
  if (msgEl) msgEl.textContent = '';
  const token = ctx.token;
  try { await fn(); }
  catch (e) {
    if (e?.name === 'AbortError' || !token.current()) return;
    const d = S.describeError(e);
    if (msgEl) msgEl.textContent = `${d.title}. ${d.body}`;
    if (d.action === 'login' || d.action === 'reload') showError(e);
  } finally { btn.disabled = false; }
}

/* ───────────────────────── B. PR 선택·최근 작업 ───────────────────────── */
async function renderHome(token) {
  const list = h('div', { class: 'ai-prs', 'aria-live': 'polite' });
  const more = button('더 보기', () => loadPage(false));
  more.hidden = true;
  let state = 'open', cursor = null, gen = 0;
  const tabs = h('div', { class: 'ai-tabs', role: 'tablist' }, [['open', '열림'], ['closed', '닫힘'], ['merged', '병합']].map(([v, label]) =>
    h('button', { type: 'button', role: 'tab', class: `ai-tab${v === state ? ' active' : ''}`, 'data-state': v, 'aria-selected': v === state,
      onclick: () => { state = v; cursor = null; tabs.querySelectorAll('.ai-tab').forEach((t) => { const on = t.dataset.state === v; t.classList.toggle('active', on); t.setAttribute('aria-selected', String(on)); }); loadPage(true); } }, label)));
  async function loadPage(reset) {
    const my = ++gen;
    if (reset) { cursor = null; mount(list, h('div', { class: 'ai-muted', text: '불러오는 중…' })); more.hidden = true; }
    try {
      const page = await api.pulls(route.repo, { state, cursor }, token.signal);
      if (!token.current() || my !== gen) return;
      if (reset) clear(list);
      if (!page.items.length && reset) list.append(h('div', { class: 'ai-muted', text: `${{ open: '열린', closed: '닫힌', merged: '병합된' }[state]} PR이 없습니다.` }));
      page.items.forEach((pr) => list.append(prRow(pr)));
      cursor = page.next_cursor;
      more.hidden = !cursor;
    } catch (e) {
      if (e?.name === 'AbortError' || !token.current() || my !== gen) return;
      clear(list);
      showError(e, { onRetry: () => loadPage(true) });
    }
  }
  function prRow(pr) {
    return h('div', { class: 'ai-pr' },
      h('div', { class: 'ai-pr-main' },
        h('b', { text: `#${pr.number}` }), ' ', h('span', { class: 'ai-pr-title', text: pr.title || '(제목 없음)' }),
        pr.draft ? chip('초안', 'info') : null, pr.is_fork ? chip('포크', 'warn', '포크 저장소의 브랜치') : null,
        pr.head_repo_deleted ? chip('원본 브랜치 저장소 삭제됨', 'warn') : null),
      h('div', { class: 'ai-pr-meta' }, `${pr.author_login || '알 수 없음'} · ${pr.base_ref || '?'} ← ${pr.head_ref || '?'} · head ${S.shortSha(pr.head_sha)} · 갱신 ${fmtTime(pr.updated_at)}`),
      link('파일 선택', { repo: route.repo, pr: pr.number }, 'btn-primary ai-btn'));
  }
  mount($('ai-main'),
    h('section', { class: 'ai-card' }, h('h2', { text: 'PR 선택' }),
      h('p', { class: 'ai-muted', text: 'GitHub의 현재 PR 목록입니다. 선택하면 그 시점의 SHA를 기준으로 파일을 고릅니다.' }), tabs, list, h('div', { class: 'ai-center' }, more)),
    h('section', { class: 'ai-card', id: 'ai-jobs-card' }, h('h2', { text: '이 저장소의 최근 AI 작업' })));
  loadPage(true);
  renderJobsList(token, $('ai-jobs-card'));
}

async function renderJobsList(token, card) {
  const body = h('div', { class: 'ai-jobs' });
  const more = button('더 보기', () => page());
  more.hidden = true;
  let cursor = null;
  card.append(body, h('div', { class: 'ai-center' }, more));
  async function page() {
    try {
      const out = await api.jobs(route.repo, { cursor, kind: null }, token.signal);
      if (!token.current()) return;
      const rows = out.jobs.filter((j) => j.kind !== 'prepare');
      if (!rows.length && !cursor) body.append(h('div', { class: 'ai-muted', text: '아직 AI 작업이 없습니다.' }));
      rows.forEach((j) => {
        if (j.repository_id !== route.repo) return;             // 다른 저장소의 작업은 그리지 않는다
        const g = S.describeJob(j);
        body.append(h('div', { class: 'ai-job-row' }, chip(S.KIND_LABEL[j.kind] || j.kind, 'info'), chip(g.title, g.tone === 'ok' ? 'ok' : g.tone === 'error' ? 'error' : 'warn'),
          h('span', { class: 'ai-muted', text: `${j.model || ''} · ${fmtTime(j.created_at)}${j.generation ? ` · 재실행 ${j.generation}` : ''}` }),
          link('열기', { repo: route.repo, job: j.job_id })));
      });
      cursor = out.next_cursor; more.hidden = !cursor;
    } catch (e) { if (e?.name !== 'AbortError' && token.current()) body.append(h('div', { class: 'ai-muted', text: `작업 목록을 불러오지 못했습니다(${S.describeError(e).title}).` })); }
  }
  page();
}

/* ───────────────────────── C. 파일 선택 ───────────────────────── */
async function renderFiles(token) {
  const pr = route.pr;
  const state = { files: [], selected: new Set(), base: null, head: null, total: null, inventory: null, cursor: null, drift: false, filter: '', loading: false };
  const list = h('div', { class: 'ai-files' });
  const scope = h('div', { class: 'ai-scope-line' });
  const info = h('div', { class: 'ai-sha' });
  const makeBtn = button('미리보기 만들기', () => create(), { cls: 'btn-primary', disabled: true });
  const moreBtn = button('다음 파일 더 불러오기', () => loadPage());
  moreBtn.hidden = true;
  const filter = h('input', { type: 'search', class: 'ai-input', placeholder: '경로로 찾기', 'aria-label': '경로 필터', oninput: () => { state.filter = filter.value.toLowerCase(); draw(); } });
  const msg = h('div', { class: 'ai-inline', role: 'status' });

  function visible() { return state.files.filter((f) => !state.filter || f.path.toLowerCase().includes(state.filter) || (f.old_path || '').toLowerCase().includes(state.filter)); }
  function draw() {
    const rows = visible();
    mount(list, rows.length ? rows.map((f) => h('label', { class: 'ai-file' },
      h('input', { type: 'checkbox', checked: state.selected.has(f.path), onchange: (ev) => { ev.target.checked ? state.selected.add(f.path) : state.selected.delete(f.path); drawSummary(); } }),
      h('span', { class: 'ai-file-path' }, f.old_path ? `${f.old_path} → ${f.path}` : f.path),
      chip(S.CHANGE_LABEL[f.change_type] || f.change_type, 'info'), h('span', { class: 'ai-muted', text: `+${f.additions} −${f.deletions}` }),
      f.has_patch ? null : chip('diff 없음', 'warn', '바이너리이거나 너무 커서 GitHub가 diff를 주지 않았습니다. 코드를 가져오지 못해 제외될 수 있습니다.')))
      : h('div', { class: 'ai-muted', text: state.files.length ? '조건에 맞는 파일이 없습니다.' : '파일이 없습니다.' }));
    drawSummary();
  }
  function drawSummary() {
    const total = state.total == null ? '알 수 없음' : `${state.total}개`;
    mount(scope, `선택 ${state.selected.size}개 · 불러온 파일 ${state.files.length}개 · PR 전체 ${total}`,
      state.inventory && state.inventory !== 'complete' ? chip(state.inventory === 'partial' ? '전체 목록 일부만 확인(GitHub 한도)' : '전체 파일 수를 알 수 없음', 'warn') : null,
      state.cursor ? chip('아직 더 있음', 'info') : null);
    makeBtn.disabled = !state.selected.size || state.drift || state.loading;
    moreBtn.hidden = !state.cursor;
    mount(info, h('span', {}, `PR #${pr} · 이 목록의 기준 SHA — base `), h('code', { title: state.base || '', text: S.shortSha(state.base) }),
      ' · head ', h('code', { title: state.head || '', text: S.shortSha(state.head) }),
      h('span', { class: 'ai-muted', text: ' (미리보기를 만들 때 이 SHA가 그대로인지 서버가 다시 확인하고, 그 SHA의 코드로 고정합니다)' }));
  }
  async function loadPage() {
    if (state.loading) return;
    state.loading = true; drawSummary();
    try {
      const page = await api.pullFiles(route.repo, pr, { cursor: state.cursor }, token.signal);
      if (!token.current()) return;
      if (state.base && (page.base_sha !== state.base || page.head_sha !== state.head)) {
        state.drift = true;
        banner('warn', 'PR이 바뀌었습니다', '파일 목록을 불러오는 중에 PR의 SHA가 달라졌습니다. 새로고침해서 새 SHA로 다시 선택하세요.', [button('새로고침', () => load(), { cls: 'btn-primary' })]);
      }
      if (!state.base) { state.base = page.base_sha; state.head = page.head_sha; }
      state.total = page.pr_total_files; state.inventory = page.inventory_coverage; state.cursor = page.next_cursor;
      page.files.forEach((f) => { if (!state.files.some((x) => x.path === f.path)) state.files.push(f); });
      state.loading = false; draw();
    } catch (e) {
      state.loading = false; drawSummary();
      if (e?.name === 'AbortError' || !token.current()) return;
      showError(e, { onRetry: loadPage });
    }
  }
  async function create() {
    if (makeBtn.disabled) return;
    makeBtn.disabled = true;
    const body = { repository_id: route.repo, pr_number: pr, expected_base_sha: state.base, expected_head_sha: state.head, selected_paths: [...state.selected].sort() };
    const key = intents.input.keyFor(body);
    try {
      const out = await api.createInput(body, key, token.signal);
      intents.input.settle(200);
      if (!token.current()) return;
      if (out.repository_id !== route.repo) throw Object.assign(new Error('x'), { status: 0, code: 'BAD_RESPONSE' });
      navigate({ repo: route.repo, pr, input: out.input_id });
    } catch (e) {
      if (e?.name === 'AbortError' || !token.current()) return;
      intents.input.settle(e.status);
      makeBtn.disabled = false;
      const d = showError(e, { onRetry: create });
      if (d.code === 'PR_CHANGED') { state.drift = true; makeBtn.disabled = true; }
    }
  }
  mount($('ai-main'), h('section', { class: 'ai-card' },
    h('div', { class: 'ai-row between' }, h('h2', { text: `PR #${pr} 파일 선택` }), link('← PR 목록', { repo: route.repo })),
    info,
    h('p', { class: 'ai-muted', text: 'AI에는 선택한 파일의 변경 diff(와 주변 줄)와 PR 제목·설명만 전송됩니다. 비밀로 의심되는 파일·바이너리·생성 파일 등은 다음 단계에서 제외 사유와 함께 표시됩니다.' }),
    h('div', { class: 'ai-row' }, filter,
      button('불러온 파일 모두 선택', () => { visible().forEach((f) => state.selected.add(f.path)); draw(); }),
      button('선택 해제', () => { state.selected.clear(); draw(); })),
    scope, list, h('div', { class: 'ai-center' }, moreBtn), msg,
    h('div', { class: 'ai-row' }, makeBtn)));
  drawSummary();
  await loadPage();
}

/* ───────────────────────── D. 미리보기·동의 ───────────────────────── */
function dl(rows) {
  return h('dl', { class: 'ai-dl' }, rows.filter(Boolean).map(([k, v]) => [h('dt', { text: k }), h('dd', {}, v)]));
}

function diffTable(lines, { highlight = null, limit = 400 } = {}) {
  const shown = lines.slice(0, limit);
  const rows = shown.map((l) => {
    const hot = highlight && ((highlight.side === 'LEFT' ? l.left_line : l.right_line) >= highlight.start && (highlight.side === 'LEFT' ? l.left_line : l.right_line) <= highlight.end);
    return h('tr', { class: `ai-ln ${l.kind}${hot ? ' hot' : ''}` }, h('td', { class: 'n', text: l.left_line ?? '' }), h('td', { class: 'n', text: l.right_line ?? '' }),
      h('td', { class: 'k', text: { added: '+', removed: '−', context: ' ' }[l.kind] || ' ' }), h('td', { class: 'c' }, h('code', { text: l.text })));
  });
  return h('div', { class: 'ai-diff', tabindex: 0 }, h('table', {}, h('tbody', {}, rows)),
    lines.length > limit ? h('div', { class: 'ai-muted', text: `… 나머지 ${lines.length - limit}줄은 표시하지 않았습니다.` }) : null);
}

async function renderPreview(token) {
  const st = { preview: null, checked: false, busy: false, retryJob: null };
  const body = h('div', { class: 'ai-preview' });
  mount($('ai-main'), body);
  ctx.reloadPreview = () => { st.checked = false; return fetchPreview(); };
  ctx.onCredentialsChanged = () => { st.checked = false; fetchPreview(); };

  if (route.retry) {
    try {
      const j = await api.job(route.retry, token.signal);
      if (!token.current()) return;
      if (j.repository_id !== route.repo || j.input_id !== route.input) throw Object.assign(new Error('x'), { status: 404, code: 'RESOURCE_NOT_FOUND' });
      st.retryJob = j;
    } catch (e) { if (e?.name === 'AbortError' || !token.current()) return; return showError(e, { onRetry: load }); }
  }

  async function fetchPreview() {
    try {
      const p = await api.input(route.input, token.signal);
      if (!token.current()) return;
      if (p.repository_id !== route.repo) throw Object.assign(new Error('x'), { status: 404, code: 'RESOURCE_NOT_FOUND' });   // 다른 저장소의 입력은 그리지 않는다
      st.preview = p;
      draw();
      if (p.status === 'building') startInputPoll(); else if (poller) { poller.stop(); poller = null; }
    } catch (e) {
      if (e?.name === 'AbortError' || !token.current()) return;
      mount(body); showError(e, { onRetry: fetchPreview });
    }
  }
  function startInputPoll() {
    if (poller) return;
    poller = createAiPoller({
      fetchOnce: (signal) => api.input(route.input, signal), isDone: (p) => p.status !== 'building', signature: S.inputSignature,
      onUpdate: (p) => { if (!token.current() || p.repository_id !== route.repo) return; st.preview = p; draw(); },
      onNotice: (n) => { if (token.current()) { clearNotices(); banner('warn', n.kind === 'rate_limit' ? '요청이 많아 잠시 기다립니다' : '연결이 불안정해 다시 시도합니다', `${n.attempt}/${n.max}회 재시도 중입니다.`); } },
      onStop: (info) => { poller = null; if (info.reason !== 'done' && token.current()) showError(info.error, { onRetry: fetchPreview }); },
    });
    poller.start();
  }
  async function submit(kind) {
    if (st.busy) return;
    const consent = S.buildConsent(st.preview);
    if (!consent || !st.checked) return;
    st.busy = true; draw();
    try {
      let out;
      if (st.retryJob) {
        const key = intents.retry.keyFor({ retry: route.retry, consent });
        out = await api.retryJob(route.retry, consent, key, token.signal);
        intents.retry.settle(200);
      } else {
        const payload = { kind, consent, force_regenerate: route.force };
        out = await api.createJob(payload, intents.job.keyFor(payload), token.signal);
        intents.job.settle(200);
      }
      if (!token.current()) return;
      if (out.repository_id !== route.repo) throw Object.assign(new Error('x'), { status: 0, code: 'BAD_RESPONSE' });
      navigate({ repo: route.repo, job: out.job_id });
    } catch (e) {
      if (e?.name === 'AbortError' || !token.current()) return;
      (st.retryJob ? intents.retry : intents.job).settle(e.status);
      st.busy = false; st.checked = false; draw();
      const d = showError(e, { onRetry: () => submit(kind) });
      if (d.action === 'keys') openKeys();
      if (d.action === 'reload_preview') fetchPreview();
    }
  }
  function draw() {
    const p = st.preview;
    if (!p) return;
    const blockers = S.consentBlockers(p);
    const consent = S.buildConsent(p);
    const cov = p.coverage;
    const filesRead = p.files.filter((f) => f.content_state === 'read');
    const fileList = h('div', { class: 'ai-files-table' }, h('table', {},
      h('thead', {}, h('tr', {}, ['파일', '변경', '상태', '크기', '변경 위치(hunk)', ''].map((t) => h('th', { text: t })))),
      h('tbody', {}, p.files.map((f) => {
        const diffBox = h('tr', { class: 'ai-diff-row', hidden: true }, h('td', { colspan: 6 }));
        const open = button('전송될 diff 보기', async (ev) => {
          const td = diffBox.firstChild;
          if (!diffBox.hidden) { diffBox.hidden = true; return; }
          diffBox.hidden = false; mount(td, h('span', { class: 'ai-muted', text: '불러오는 중…' }));
          try {
            const c = await api.inputFile(p.input_id, f.file_id, {}, token.signal);
            if (!token.current()) return;
            mount(td, diffTable(c.lines));
          } catch (e) { if (e?.name !== 'AbortError' && token.current()) mount(td, h('span', { class: 'ai-muted', text: `${S.describeError(e).title}: ${S.describeError(e).body}` })); }
        }, { disabled: f.content_state !== 'read' || p.status !== 'ready' });
        return [h('tr', { class: `ai-frow ${f.content_state}` },
          h('td', { class: 'ai-file-path' }, f.old_path ? `${f.old_path} → ${f.path}` : f.path),
          h('td', { text: S.CHANGE_LABEL[f.change_type] || f.change_type }),
          h('td', {}, chip(S.CONTENT_STATE_LABEL[f.content_state] || f.content_state, f.content_state === 'read' ? 'ok' : 'warn'),
            f.exclusion_reason ? h('div', { class: 'ai-muted', text: S.limitationLabel(f.exclusion_reason) }) : null),
          h('td', { text: S.fmtBytes(f.byte_count) }),
          h('td', { class: 'ai-muted', text: f.hunks?.length ? `${f.hunks.length}개 (${f.hunks.map((x) => `${x.new_start}+${x.new_lines}`).slice(0, 4).join(', ')}${f.hunks.length > 4 ? ', …' : ''})` : '—' }),
          h('td', {}, open)), diffBox];
      }))));
    const t = p.transfer;
    const est = p.token_estimate;
    const consentBox = h('div', { class: 'ai-consent' },
      h('h3', { text: '전송 동의' }),
      t ? dl([['제공자·모델', `${t.provider} / ${t.model}`], ['사용할 API key', t.credential_configured ? `${t.key_hint} (v${t.credential_version})` : '등록되지 않음'],
        ['외부 전송', t.external_transfer_allowed ? '이 서버에서 허용됨' : '이 서버에서 허용되지 않음']]) : null,
      shared.providers ? h('div', { class: 'ai-models compact' }, shared.providers.providers[0].models.map((m) => h('label', { class: `ai-model${m.allowed ? '' : ' disabled'}` },
        h('input', { type: 'radio', name: 'ai-consent-model', checked: t?.profile_id === m.profile_id, disabled: !m.allowed || !t?.credential_configured || st.busy,
          onchange: async () => { st.checked = false; try { await api.setPreferredProfile('anthropic', m.profile_id); await refreshCreds(); } catch (e) { if (e?.name !== 'AbortError') showError(e); } } }),
        h('span', { text: `${m.display_name || m.model} ($${m.input_price_per_mtok}/$${m.output_price_per_mtok})` })))) : null,
      blockers.length ? h('ul', { class: 'ai-blockers' }, blockers.map((b) => h('li', { text: b }))) : null,
      h('label', { class: 'ai-check' },
        h('input', { type: 'checkbox', id: 'ai-consent-check', checked: st.checked, disabled: !consent || st.busy, onchange: (ev) => { st.checked = ev.target.checked; draw(); } }),
        h('span', { text: S.consentStatement(p) })),
      h('div', { class: 'ai-row' },
        st.retryJob
          ? button(`다시 실행(${S.KIND_LABEL[st.retryJob.kind]})`, () => submit(st.retryJob.kind), { cls: 'btn-primary', disabled: !consent || !st.checked || st.busy })
          : [button(route.force ? '변경 설명 새로 생성' : '변경 설명 생성', () => submit('summary'), { cls: 'btn-primary', disabled: !consent || !st.checked || st.busy }),
            button(route.force ? '코드 리뷰 새로 생성' : '코드 리뷰 생성', () => submit('review'), { cls: 'btn-primary', disabled: !consent || !st.checked || st.busy })]),
      h('p', { class: 'ai-muted', text: '설명과 리뷰는 서로 독립된 실행입니다(리뷰에 설명 결과를 넣지 않습니다). 실행하면 위 key의 계정에 비용이 청구됩니다. 취소해도 이미 시작된 호출의 비용은 발생할 수 있습니다.' }));
    const prev = st.retryJob ? (() => { const g = S.describeJob(st.retryJob); return h('div', { class: 'ai-banner warn' }, h('div', { class: 'ai-banner-title', text: `이전 실행: ${g.title}` }), h('div', { class: 'ai-banner-body', text: `${g.body} 다시 실행하려면 아래에서 다시 동의해야 합니다.` })); })() : null;
    mount(body, prev,
      h('section', { class: 'ai-card' },
        h('div', { class: 'ai-row between' }, h('h2', { text: '전송 미리보기' }), chip({ building: '준비 중', ready: '준비 완료', failed: '준비 실패', expired: '만료·삭제됨' }[p.status] || p.status, p.status === 'ready' ? 'ok' : p.status === 'building' ? 'info' : 'warn')),
        p.status === 'building' ? h('p', { class: 'ai-muted', text: `코드를 고정하는 중입니다(${S.STAGE_LABEL[p.prepare_job?.stage] || '대기'}). 진행률은 알 수 없습니다.` }) : null,
        p.prepare_job?.status === 'failed' ? h('div', { class: 'ai-banner error' }, h('div', { class: 'ai-banner-title', text: '코드 준비에 실패했습니다' }), h('div', { class: 'ai-banner-body', text: `오류 코드: ${p.prepare_job.error_code || '알 수 없음'}. 파일을 다시 선택해 새로 준비하세요.` })) : null,
        dl([['PR', `#${p.pr_number} (${p.pr_state})`], ['제목', p.pr_title || '(없음)'],
          ['고정 SHA', h('span', { class: 'ai-shas' }, h('span', {}, 'base ', h('code', { text: p.base_sha })), h('span', {}, 'head ', h('code', { text: p.head_sha })), h('span', {}, 'merge-base ', h('code', { text: p.merge_base_sha || '—' })))],
          ['AI로 가는 PR 설명', p.pr_body ? h('details', {}, h('summary', { text: `${p.pr_body.length}자${p.pr_body_truncated ? ' (너무 길어 잘림)' : ''}` }), h('pre', { class: 'ai-pre', text: p.pr_body })) : '없음'],
          ['유효 기한', p.valid_until ? `${fmtTime(p.valid_until)}까지 새 실행 시작 가능 · 원문은 ${fmtTime(p.expires_at)}에 삭제` : `원문은 ${fmtTime(p.expires_at)}에 삭제`]])),
      p.status === 'building' ? null : h('section', { class: 'ai-card' }, h('h2', { text: '전송 범위' }),
        dl(S.coverageLines(cov, { beforeRun: true }).map((l) => [l.label, l.value]).concat([['범위 요약', S.scopeSummary(cov)]])),
        S.coverageLimitations(cov).length ? h('div', { class: 'ai-limits' }, '제외·누락 사유: ', S.coverageLimitations(cov).map((l) => chip(l, 'warn'))) : null,
        h('p', { class: 'ai-muted', text: `전송되는 파일 ${filesRead.length}개 / 선택 ${p.files.length}개. 제외된 파일은 AI에 보내지 않습니다.` }),
        fileList),
      p.status === 'ready' && est ? h('section', { class: 'ai-card' }, h('h2', { text: '예상 비용' }),
        dl([['입력 토큰(추정)', `${S.fmtTokens(est.input_tokens)} ${est.limit ? `/ 한도 ${S.fmtTokens(est.limit)}` : ''}`],
          ['최대 출력 토큰', S.fmtTokens(est.max_output_tokens)], ['예상 비용 상한', S.fmtMoney(est.estimated_max_cost, est.currency || 'USD')],
          ['모델', est.profile || '—']]),
        est.within_limit === false ? chip('입력 한도 초과 — 실행할 수 없습니다', 'error') : null,
        h('p', { class: 'ai-muted', text: '로컬의 보수적 추정이며 실제 비용은 더 낮을 수 있습니다. 실행 직전에 제공자의 토큰 수로 다시 확인하고, 상한을 넘으면 실행하지 않습니다.' })) : null,
      h('section', { class: 'ai-card' }, consentBox,
        h('div', { class: 'ai-row' }, button('이 입력 삭제(원문 삭제·연결 작업 취소)', async () => {
          if (!window.confirm('이 입력의 원문을 삭제하고 연결된 작업을 취소합니다. 계속할까요?')) return;
          try { await api.deleteInput(p.input_id); if (token.current()) navigate({ repo: route.repo, pr: p.pr_number }); } catch (e) { if (e?.name !== 'AbortError' && token.current()) showError(e); }
        }, { cls: 'btn-secondary danger' }), link('파일 다시 선택', { repo: route.repo, pr: p.pr_number }))));
  }
  await fetchPreview();
}

/* ───────────────────────── E. 실행·F. 결과 ───────────────────────── */
async function renderJob(token) {
  const holder = h('div', { class: 'ai-job' });
  mount($('ai-main'), holder);
  const st = { job: null, resultLoaded: false };
  async function apply(job) {
    if (!token.current()) return;
    if (job.repository_id !== route.repo) {                            // 다른 저장소의 작업은 그리지 않는다
      stopAll(); mount(holder); return showError({ status: 404, code: 'RESOURCE_NOT_FOUND', details: {} });
    }
    st.job = job;
    drawProgress(job);
    if (job.status === 'succeeded' && job.result_id && !st.resultLoaded) { st.resultLoaded = true; stopAll(); await renderResult(token, holder, job); }
  }
  function drawProgress(job) {
    if (st.resultLoaded) return;
    const g = S.describeJob(job);
    const elapsed = h('span', { class: 'ai-muted' });
    const tick = () => { elapsed.textContent = ` · 경과 ${S.fmtDuration((Date.now() - new Date(job.created_at).getTime()) / 1000)}`; };
    if (!S.isTerminalStatus(job.status)) { tick(); if (clockTimer) clearInterval(clockTimer); clockTimer = setInterval(tick, 1000); } else if (clockTimer) { clearInterval(clockTimer); clockTimer = null; }
    const actions = [];
    if (g.canCancel) actions.push(button('취소', () => cancel(), { cls: 'btn-secondary danger' }));
    if (g.canRetry) actions.push(button('다시 실행(동의 다시 받음)', () => navigate({ repo: route.repo, input: job.input_id, retry: job.job_id }), { cls: 'btn-primary' }));
    if (g.action === 'keys') actions.push(button('API key 설정 열기', () => openKeys()));
    if (g.action === 'login') actions.push(button('GitHub로 로그인', () => (window.githubLogin ? window.githubLogin() : (location.href = '/api/auth/github')), { cls: 'btn-primary' }));
    if (g.action === 'reselect' || g.action === 'usage') actions.push(link('PR 선택으로', { repo: route.repo }, 'btn-secondary ai-btn'));
    if (S.isTerminalStatus(job.status)) actions.push(button('이 작업 삭제', () => removeJob(), { cls: 'btn-secondary danger' }));
    mount(holder, h('section', { class: `ai-card ai-progress ${g.tone}` },
      h('div', { class: 'ai-row between' }, h('h2', { text: `${S.KIND_LABEL[job.kind] || job.kind} · ${g.title}` }), chip(job.status, g.tone === 'ok' ? 'ok' : g.tone === 'info' ? 'info' : g.tone === 'warn' ? 'warn' : 'error')),
      h('p', { class: g.costRisk ? 'ai-cost-risk' : '', text: g.body }), elapsed,
      dl([['모델', job.model ? `${job.provider} / ${job.model}` : '—'], ['시도', job.generation ? `재실행 ${job.generation}번째` : '첫 실행'], ['생성', fmtTime(job.created_at)],
        job.finished_at ? ['종료', fmtTime(job.finished_at)] : null,
        job.outcome_uncertain ? ['비용', '호출 결과를 알 수 없어 비용이 발생했을 수 있습니다'] : null, job.error_code ? ['오류 코드', h('code', { text: job.error_code })] : null]),
      h('div', { class: 'ai-row' }, actions), h('p', { class: 'ai-muted', text: '재실행은 항상 직접 시작해야 하며 자동으로 다시 실행되지 않습니다.' })));
  }
  async function cancel() {
    try { const j = await api.cancelJob(route.job, token.signal); await apply(j); if (!S.isTerminalStatus(j.status)) startPoll(); }
    catch (e) { if (e?.name !== 'AbortError' && token.current()) showError(e); }
  }
  async function removeJob() {
    if (!window.confirm('이 작업과 결과·피드백을 삭제합니다. 계속할까요?')) return;
    try { await api.deleteJob(route.job, token.signal); if (token.current()) navigate({ repo: route.repo }); }
    catch (e) { if (e?.name !== 'AbortError' && token.current()) showError(e); }
  }
  ctx.removeJob = removeJob;
  function startPoll() {
    if (poller) poller.stop();
    poller = createAiPoller({
      fetchOnce: (signal) => api.job(route.job, signal), isDone: (j) => S.isTerminalStatus(j.status), signature: S.jobSignature,
      onUpdate: (j) => { clearNotices(); apply(j); },
      onNotice: (n) => { if (token.current()) { clearNotices(); banner('warn', n.kind === 'rate_limit' ? '요청이 많아 잠시 기다립니다' : '연결이 불안정해 다시 시도합니다', `${n.attempt}/${n.max}회 재시도 중입니다. 작업은 서버에서 계속됩니다.`); } },
      onStop: (info) => { poller = null; if (info.reason !== 'done' && token.current()) { mount(holder); showError(info.error, { onRetry: load }); } },
    });
    poller.start();
  }
  try {
    const job = await api.job(route.job, token.signal);
    if (!token.current()) return;
    await apply(job);
    if (!S.isTerminalStatus(job.status) && token.current()) startPoll();
  } catch (e) { if (e?.name === 'AbortError' || !token.current()) return; mount(holder); showError(e, { onRetry: load }); }
}

async function renderResult(token, holder, job) {
  mount(holder, h('div', { class: 'ai-muted', text: '결과를 불러오는 중…' }));
  let result, input = null, findings = [], cursor = null, currentHead = null;
  try {
    result = await api.result(job.result_id, token.signal);
    if (!token.current()) return;
    if (result.repository_id !== route.repo || result.job_id !== job.job_id) throw Object.assign(new Error('x'), { status: 404, code: 'RESOURCE_NOT_FOUND', details: {} });
    const [page, inp] = await Promise.all([
      result.kind === 'review' ? api.findings(result.result_id, {}, token.signal) : Promise.resolve({ findings: [], next_cursor: null }),
      api.input(result.input_id, token.signal).catch(() => null)]);
    if (!token.current()) return;
    findings = page.findings; cursor = page.next_cursor; input = inp && inp.repository_id === route.repo ? inp : null;
  } catch (e) { if (e?.name === 'AbortError' || !token.current()) return; mount(holder); return showError(e, { onRetry: load }); }
  if (input?.pr_number) {                                             // 현재 PR head와 비교(실패해도 결과는 보인다)
    api.pullFiles(route.repo, input.pr_number, { limit: 1 }, token.signal).then((p) => { if (token.current()) { currentHead = p.head_sha; drawStale(); } }).catch(() => {});
  }
  const staleBox = h('div', { class: 'ai-stale' });
  function drawStale() {
    const note = S.staleShaNotice(input?.head_sha, currentHead);
    mount(staleBox, note ? h('div', { class: 'ai-banner warn' }, h('div', { class: 'ai-banner-title', text: '이전 SHA의 결과입니다' }), h('div', { class: 'ai-banner-body', text: note })) : null);
  }
  const summaryBox = h('div', { class: 'ai-valid' });
  const listBox = h('div', { class: 'ai-findings' });
  const moreBtn = button('지적 더 보기', async () => {
    try { const page = await api.findings(result.result_id, { cursor }, token.signal); if (!token.current()) return; findings = findings.concat(page.findings); cursor = page.next_cursor; drawFindings(); }
    catch (e) { if (e?.name !== 'AbortError' && token.current()) showError(e); }
  });
  function drawSummaryBox() {
    const sum = S.findingsSummary(findings);
    mount(summaryBox,
      chip('구조 검증 통과', 'ok', S.STRUCTURE_NOTE), ' ', chip(result.human_validation === 'unverified' ? '사람 검증 전' : result.human_validation, 'warn'), ' ',
      result.kind === 'review' ? h('span', { text: ` ${S.humanValidationText(sum)}${cursor ? ' (불러온 지적 기준)' : ''}` }) : null,
      h('div', { class: 'ai-muted', text: `${S.STRUCTURE_NOTE} ${S.MODEL_NOTE}` }));
  }
  async function reloadFindings() {
    try {
      let all = [], next = null, first = true;
      do { const page = await api.findings(result.result_id, { cursor: next }, token.signal); if (!token.current()) return; all = all.concat(page.findings); next = page.next_cursor; first = false; } while (next && all.length < findings.length + 20 && !first);
      findings = all; cursor = next; drawFindings();
    } catch (e) { if (e?.name !== 'AbortError' && token.current()) showError(e); }
  }
  function drawFindings() {
    drawSummaryBox();
    if (result.kind !== 'review') return mount(listBox);
    if (!findings.length) return mount(listBox, h('div', { class: 'ai-empty' }, h('b', { text: '지적 0건' }), h('p', { text: S.emptyFindingsText(result.coverage) })));
    mount(listBox, findings.map((f) => findingCard(f)), cursor ? h('div', { class: 'ai-center' }, moreBtn) : null);
  }
  function codeButton(fileId, loc) {
    const out = h('div', { class: 'ai-code' });
    const b = button('근거 코드 보기', async () => {
      if (out.firstChild) { clear(out); return; }
      mount(out, h('span', { class: 'ai-muted', text: '불러오는 중…' }));
      try {
        const range = loc ? { side: loc.side, startLine: Math.max(1, loc.start_line - 6), endLine: loc.end_line + 6 } : {};
        const c = await api.inputFile(result.input_id, fileId, range, token.signal);
        if (!token.current()) return;
        mount(out, diffTable(c.lines, { highlight: loc ? { side: loc.side, start: loc.start_line, end: loc.end_line } : null }),
          h('div', { class: 'ai-muted', text: `${c.path} · 이 입력(SHA ${S.shortSha(input?.head_sha)})의 고정된 코드입니다.` }));
      } catch (e) {
        if (e?.name === 'AbortError' || !token.current()) return;
        const d = S.describeError(e);
        mount(out, h('div', { class: 'ai-banner warn' }, h('div', { class: 'ai-banner-title', text: e.code === 'INPUT_EXPIRED' ? '원문 코드를 더 이상 볼 수 없습니다' : d.title },),
          h('div', { class: 'ai-banner-body', text: e.code === 'INPUT_EXPIRED' ? '입력의 보관 기간이 지났거나 삭제돼 원문이 없습니다. 결과에 저장된 인용만 확인할 수 있습니다.' : d.body })));
      }
    });
    return h('div', {}, b, out);
  }
  function findingCard(f) {
    const x = f.finding;
    let verdict = f.feedback?.verdict || null;
    const note = h('textarea', { class: 'ai-input', rows: 2, maxlength: 2000, placeholder: '메모(선택, 2000자까지)', 'aria-label': '피드백 메모' });
    note.value = f.feedback?.note || '';
    const status = h('div', { class: 'ai-inline', role: 'status' });
    const buttons = ['valid', 'false_positive', 'uncertain'].map((v) => h('button', { type: 'button', class: `ai-verdict${verdict === v ? ' on' : ''}`, 'data-verdict': v, 'aria-pressed': String(verdict === v),
      onclick: () => { verdict = v; buttons.forEach((b) => { const on = b.dataset.verdict === v; b.classList.toggle('on', on); b.setAttribute('aria-pressed', String(on)); }); } }, S.VERDICT_LABEL[v]));
    const verdictChip = chip(S.VERDICT_LABEL[x.human_validation] || x.human_validation, x.human_validation === 'unverified' ? 'warn' : 'ok');
    const mine = h('span', { class: 'ai-muted', text: f.feedback ? `내 검증: ${S.VERDICT_LABEL[f.feedback.verdict]} (v${f.feedback.version})` : '내 검증: 미검증' });
    const save = button('피드백 저장', async () => {
      if (!verdict) { status.textContent = '유효·오탐·불확실 중 하나를 고르세요.'; return; }
      await run(save, status, async () => {
        try {
          const out = await api.putFeedback(x.finding_id || f.finding_id, { verdict, note: note.value, expectedVersion: f.feedback?.version || 0 }, token.signal);
          f.feedback = out; f.finding.human_validation = out.verdict;
          status.textContent = `저장했습니다 (v${out.version}).`; drawSummaryBox();
          verdictChip.textContent = S.VERDICT_LABEL[out.verdict]; verdictChip.className = 'ai-chip ok';
          mine.textContent = `내 검증: ${S.VERDICT_LABEL[out.verdict]} (v${out.version})`;
        } catch (e) {
          if (e?.code === 'VERSION_CONFLICT') { showError(e); await reloadFindings(); return; }
          throw e;
        }
      });
    });
    return h('article', { class: 'ai-finding', 'data-finding': f.finding_id },
      h('div', { class: 'ai-row between' }, h('h3', { text: `${x.ordinal}. ${x.title}` }),
        h('span', {}, chip(x.category, 'info'), chip(`검토 순서 ${x.priority}`, 'info', '장애 등급이 아니라 검토할 순서입니다'),
          verdictChip)),
      h('div', { class: 'ai-loc' }, h('code', { text: S.locationText(x.location) })),
      dl([['조건', x.condition], ['영향', x.impact], ['확인 방법', x.verification], x.suggestion ? ['제안', x.suggestion] : null,
        f.quote ? ['인용', h('pre', { class: 'ai-pre', text: f.quote })] : null,
        x.limitations?.length ? ['한계', x.limitations.map(S.limitationLabel).join(', ')] : null]),
      codeButton(f.source_file_id, x.location),
      h('div', { class: 'ai-feedback' }, h('div', { class: 'ai-row' }, buttons), note, h('div', { class: 'ai-row' }, save, mine), status));
  }
  function summaryBody() {
    const p = result.payload;
    return h('div', { class: 'ai-summary' },
      dl([['개요(코드에서 관찰한 사실)', p.overview], p.author_intent ? ['작성자 의도(PR 설명 기반)', p.author_intent] : null,
        p.unknowns?.length ? ['알 수 없는 것', h('ul', {}, p.unknowns.map((u) => h('li', { text: u })))] : null]),
      h('h3', { text: '파일별 변경' }),
      (p.file_changes || []).length ? h('div', {}, p.file_changes.map((c) => h('div', { class: 'ai-fchange' }, h('code', { text: c.path }), h('p', { text: c.summary }), codeButton(c.file_id, null))))
        : h('p', { class: 'ai-muted', text: '파일별 설명이 없습니다(모델이 보고하지 않음). 변경이 없다는 뜻이 아닙니다.' }));
  }
  const cov = result.coverage;
  mount(holder, staleBox,
    h('section', { class: 'ai-card' },
      h('div', { class: 'ai-row between' }, h('h2', { text: `${S.KIND_LABEL[result.kind]} 결과` }), h('span', { class: 'ai-muted', text: `${result.provider || ''} / ${result.model || ''} · ${fmtTime(result.created_at)}` })),
      dl([['분석 기준', input ? h('span', {}, `PR #${input.pr_number} · base `, h('code', { title: input.base_sha, text: S.shortSha(input.base_sha) }), ' · head ', h('code', { title: input.head_sha, text: S.shortSha(input.head_sha) })) : '입력 정보를 읽을 수 없음'],
        ['범위 요약', S.scopeSummary(cov)], ...S.coverageLines(cov).map((l) => [l.label, l.value]),
        S.coverageLimitations(cov).length ? ['제외·누락 사유', S.coverageLimitations(cov).join(' · ')] : null,
        ['결과 보관', `${fmtTime(result.expires_at)}까지`]]),
      summaryBox),
    result.kind === 'review' ? h('section', { class: 'ai-card' }, h('h2', { text: `지적 ${result.finding_count}건` }), listBox) : h('section', { class: 'ai-card' }, summaryBody()),
    h('section', { class: 'ai-card' }, h('div', { class: 'ai-row' },
      link('같은 입력으로 새로 실행', { repo: route.repo, input: result.input_id, force: true }, 'btn-secondary ai-btn'), link('PR 선택으로', { repo: route.repo }, 'btn-secondary ai-btn'),
      button('이 작업 삭제', () => ctx.removeJob(), { cls: 'btn-secondary danger' }))));
  drawStale();
  drawFindings();
}

/* ───────────────────────── 시작 ───────────────────────── */
load();
