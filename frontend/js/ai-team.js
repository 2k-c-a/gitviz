/* ── 팀 화면 ──
   흐름: 팀 목록·초대 → 팀(구성원·저장소·공유·보고서). 주소(?team=&report=)가 유일한 기준이다.
   - 서버가 판단한다. 버튼을 숨기거나 막는 것은 안내일 뿐이고 허용 여부는 서버 응답 코드로 안내한다(권한·저장소 접근·공유 유효성은 요청마다 다시 확인된다).
   - 개인 자료는 자동으로 공유되지 않는다. 작성자가 팀을 지정해 "공유"한 것만 보이고, 판정(verdict)은 공유한 사람의 판단이다.
   - 모든 동적 문자열은 textContent로만 넣는다(HTML 해석 API를 쓰지 않는다). 차단된 보고서의 내용은 받지도 그리지도 않는다. */
import { createAiClient } from './ai-api.js';
import * as S from './ai-state.js';
import * as TS from './ai-team-state.js';

const api = createAiClient();
const guard = S.createGuard();
const $ = (id) => document.getElementById(id);
let route = TS.parseRoute(location.search);
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
const link = (text, next, cls = '') => h('a', { class: `ai-link ${cls}`, href: `${location.pathname}${TS.buildRoute(next)}`,
  onclick: (ev) => { if (ev.metaKey || ev.ctrlKey || ev.shiftKey) return; ev.preventDefault(); navigate(next); } }, text);

function notice(kind, title, body, actions = []) {
  const el = h('div', { class: `ai-banner ${kind}`, role: kind === 'error' ? 'alert' : 'status' }, h('div', { class: 'ai-banner-title', text: title }),
    body ? h('div', { class: 'ai-banner-body', text: body }) : null, actions.length ? h('div', { class: 'ai-banner-actions' }, actions) : null);
  $('tm-notice').append(el);
  return el;
}
const clearNotices = () => clear($('tm-notice'));

function showError(err, { reload = null } = {}) {
  const special = TS.describeTeamError(err);
  if (special) {
    notice(special.tone === 'warn' ? 'warn' : 'error', special.title, special.body, reload && !special.blocked ? [button('다시 불러오기', reload)] : []);
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

async function run(btn, fn) {
  if (btn.disabled) return;
  btn.disabled = true;
  try { await fn(); } finally { btn.disabled = false; }
}

/* 동작 하나: 실패하면 안내하고, 상태가 달라졌을 수 있는 오류(충돌·권한·찾을 수 없음)는 최신 상태를 다시 불러온다. */
async function act(fn, { done = null } = {}) {
  clearNotices();
  try {
    const out = await fn();
    await refresh();
    if (done && ctx.token.current()) notice('ok', done, '');
    return out;
  } catch (e) {
    if (e?.name === 'AbortError' || !ctx.token.current()) return null;
    showError(e, { reload: refresh });
    if (['VERSION_CONFLICT', 'PERMISSION_DENIED', 'RESOURCE_NOT_FOUND'].includes(e?.code)) await refresh();
    return null;
  }
}

/* ───────────────────────── 이동·세대 ───────────────────────── */
function navigate(next, { replace = false } = {}) {
  const url = `${location.pathname}${TS.buildRoute(next)}`;
  if (replace) history.replaceState(null, '', url); else history.pushState(null, '', url);
  return load();
}
window.addEventListener('popstate', () => { load(); });

async function load() {
  const token = guard.begin();
  route = TS.parseRoute(location.search);
  ctx = { token };
  clearNotices();
  mount($('tm-main'));
  $('tm-title').textContent = '팀';
  if (route.invalid.length) notice('warn', '주소의 일부 값을 무시했습니다', `올바르지 않은 값: ${route.invalid.join(', ')}`);
  let me;
  try { me = await api.me(token.signal); } catch (e) { if (e?.name === 'AbortError' || !token.current()) return; return showError(e, { reload: load }); }
  if (!token.current()) return;
  if (!me) {
    return mount($('tm-main'), h('section', { class: 'ai-card' }, h('h2', { text: '로그인이 필요합니다' }),
      button('GitHub로 로그인', () => (window.githubLogin ? window.githubLogin() : (location.href = '/api/auth/github')), { cls: 'btn-primary' })));
  }
  ctx.me = me;
  await refresh();
}

async function refresh() {
  const token = ctx.token;
  try {
    if (!route.team) {
      const list = await api.teams(token.signal);
      if (!token.current()) return;
      ctx.teams = list.teams;
      return drawTeams();
    }
    const [team, members, repos, shares, reports] = await Promise.all([api.team(route.team, token.signal), api.teamMembers(route.team, token.signal),
      api.teamRepositories(route.team, token.signal), api.teamShares(route.team, token.signal), api.teamReports(route.team, token.signal)]);
    if (!token.current()) return;
    Object.assign(ctx, { team, members: members.members, repos: repos.repositories, shares: shares.shares, reports: reports.reports });
    drawTeam();
    if (route.report) await openReport(route.report);
  } catch (e) {
    if (e?.name === 'AbortError' || !token.current()) return;
    showError(e, { reload: refresh });
  }
}

/* ───────────────────────── 팀 목록 ───────────────────────── */
function drawTeams() {
  const active = ctx.teams.filter((t) => t.status === 'active');
  const invited = ctx.teams.filter((t) => t.status === 'invited');
  const name = h('input', { class: 'ai-input', type: 'text', maxlength: 80, 'aria-label': '팀 이름', placeholder: '팀 이름' });
  const create = button('팀 만들기', () => run(create, async () => {
    if (!name.value.trim()) { notice('warn', '팀 이름을 입력해 주세요', ''); return; }
    clearNotices();
    try { const t = await api.createTeam(name.value.trim(), ctx.token.signal); if (ctx.token.current()) navigate({ team: t.team_id }); }
    catch (e) { if (e?.name !== 'AbortError') showError(e); }
  }), { cls: 'btn-primary' });
  mount($('tm-main'),
    invited.length ? h('section', { class: 'ai-card' }, h('h2', { text: '받은 초대' }), h('ul', { class: 'ho-targets' }, invited.map((t) => h('li', {},
      h('b', { text: t.name }), ' ', chip(TS.ROLE_LABEL[t.role], 'info'), ' ',
      button('수락', () => act(() => api.acceptInvitation(t.team_id, ctx.token.signal), { done: `${t.name} 팀에 참여했습니다` }), { cls: 'btn-primary' }), ' ',
      button('거절', () => act(() => api.removeMember(t.team_id, ctx.me.id, ctx.token.signal), { done: '초대를 거절했습니다' })))))) : null,
    h('section', { class: 'ai-card' }, h('h2', { text: '내 팀' }), active.length
      ? h('ul', { class: 'ho-targets' }, active.map((t) => h('li', {}, link(t.name, { team: t.team_id }), ' ', chip(TS.ROLE_LABEL[t.role], 'info'), ' ', chip(`구성원 ${t.member_count}명`, 'info'))))
      : h('p', { class: 'ai-muted', text: '아직 속한 팀이 없습니다.' })),
    h('section', { class: 'ai-card' }, h('h2', { text: '새 팀' }), h('p', { class: 'ai-muted', text: '팀을 만들거나 참여해도 내 자료는 공유되지 않습니다. 자료는 작성자가 직접 공유할 때만 팀에 보입니다.' }),
      h('div', { class: 'ai-row' }, name, create)));
}

/* ───────────────────────── 팀 화면 ───────────────────────── */

function drawTeam() {
  const { team } = ctx;
  $('tm-title').textContent = `팀 · ${team.name}`;
  mount($('tm-main'), h('p', {}, link('← 팀 목록', {})), headerCard(), membersCard(), reposCard(), sharesCard(), reportsCard(), h('div', { id: 'tm-report' }));
}

function headerCard() {
  const { team } = ctx;
  const leave = button('팀 나가기', async () => {
    if (!window.confirm('이 팀에서 나갈까요? 내가 공유한 자료는 모두 공유 해제되고, 이 팀의 기존 보고서는 차단됩니다.')) return;
    clearNotices();
    try { await api.removeMember(team.team_id, ctx.me.id, ctx.token.signal); navigate({}); } catch (e) { if (e?.name !== 'AbortError') { showError(e); await refresh(); } }
  });
  const parts = [h('div', { class: 'ai-row between' }, h('h2', { text: team.name }), h('span', {}, chip(TS.ROLE_LABEL[team.role], 'info'), ' ', chip(`구성원 ${team.member_count}명`, 'info'))),
    h('p', { class: 'ai-muted', text: `${TS.PERMISSION_NOTE[team.role]} 보이는 범위는 팀 역할, 내 저장소 접근, 공유 상태가 모두 맞을 때입니다(열 때마다 다시 확인).` })];
  const actions = [leave];
  if (TS.can(team.role, 'manage')) {
    actions.unshift(button('이름 변경', async () => { const n = window.prompt('새 팀 이름', team.name); if (n && n.trim()) await act(() => api.renameTeam(team.team_id, n.trim(), ctx.token.signal), { done: '이름을 바꿨습니다' }); }),
      button('팀 삭제', async () => {
        if (!window.confirm('팀을 삭제할까요? 구성원·공유·보고서가 모두 사라집니다(개인 자료는 그대로입니다).')) return;
        try { await api.deleteTeam(team.team_id, ctx.token.signal); navigate({}); } catch (e) { if (e?.name !== 'AbortError') showError(e); }
      }, { cls: 'btn-secondary danger' }));
  }
  return h('section', { class: 'ai-card' }, parts, h('div', { class: 'ai-row' }, actions));
}

function membersCard() {
  const { team } = ctx;
  const manage = TS.can(team.role, 'manage');
  const rows = ctx.members.map((m) => {
    const role = h('select', { class: 'ai-input', 'aria-label': `${m.github_login} 역할`, disabled: !manage }, TS.ROLE_ORDER.map((r) => h('option', { value: r, text: TS.ROLE_LABEL[r] })));
    role.value = m.role;
    role.addEventListener('change', () => act(() => api.changeMemberRole(team.team_id, m.user_id, role.value, ctx.token.signal), { done: '역할을 바꿨습니다' }));
    return h('li', {}, h('b', { text: m.github_login }), m.display_name ? h('span', { class: 'ai-muted', text: ` (${m.display_name})` }) : null, ' ', m.status === 'invited' ? chip('초대 중', 'warn') : null, ' ', role, ' ',
      manage ? button(m.status === 'invited' ? '초대 취소' : '제거', () => {
        if (m.status === 'active' && !window.confirm(`${m.github_login}님을 제거할까요? 그 사람이 공유한 자료는 모두 공유 해제되고 이 팀의 기존 보고서는 차단됩니다.`)) return;
        act(() => api.removeMember(team.team_id, m.user_id, ctx.token.signal), { done: '구성원을 제거했습니다' });
      }) : null);
  });
  let invite = null;
  if (manage) {
    const login = h('input', { class: 'ai-input', type: 'text', maxlength: 255, 'aria-label': 'GitHub 사용자 이름', placeholder: 'GitHub 사용자 이름' });
    const role = h('select', { class: 'ai-input', 'aria-label': '초대할 역할' }, TS.ROLE_ORDER.map((r) => h('option', { value: r, text: TS.ROLE_LABEL[r] })));
    role.value = 'member';
    const go = button('초대', () => run(go, async () => {
      if (!login.value.trim()) return;
      await act(() => api.inviteMember(team.team_id, login.value.trim(), role.value, ctx.token.signal), { done: '초대했습니다. 수락해야 구성원이 됩니다.' });
    }), { cls: 'btn-primary' });
    invite = h('div', { class: 'ai-row' }, login, role, go);
  }
  return h('section', { class: 'ai-card' }, h('h2', { text: '구성원' }), h('ul', { class: 'ho-targets' }, rows), invite,
    manage ? h('p', { class: 'ai-muted', text: 'GitViz에 로그인한 적 있는 GitHub 사용자만 초대할 수 있습니다. 초대받은 사람이 수락해야 구성원이 됩니다.' }) : null);
}

function reposCard() {
  const { team } = ctx;
  const manage = TS.can(team.role, 'manage');
  const list = ctx.repos.length ? h('ul', { class: 'ho-targets' }, ctx.repos.map((r) => h('li', {}, h('b', { text: r.name || `저장소 #${r.repository_id}` }), ' ', h('code', { text: r.url || '' }), ' ',
    manage ? button('연결 해제', () => {
      if (!window.confirm('연결을 해제할까요? 이 저장소의 공유는 모두 사라지고 이 팀의 기존 보고서는 차단됩니다.')) return;
      act(() => api.removeTeamRepository(team.team_id, r.repository_id, ctx.token.signal), { done: '연결을 해제했습니다' });
    }) : null))) : h('p', { class: 'ai-muted', text: '연결된 저장소가 없습니다. 관리자가 저장소를 연결해야 공유할 수 있습니다.' });
  let add = null;
  if (manage) {
    const sel = h('select', { class: 'ai-input', 'aria-label': '연결할 내 프로젝트' }, h('option', { value: '', text: '내 프로젝트에서 선택' }));
    api.myProjects(ctx.token.signal).then((ps) => {
      if (!ctx.token.current()) return;
      const linked = new Set(ctx.repos.map((r) => r.repository_id));
      ps.filter((p) => !linked.has(p.id)).forEach((p) => sel.append(h('option', { value: String(p.id), text: p.name || p.url || `#${p.id}` })));
    }).catch(() => {});
    const go = button('연결', () => run(go, async () => { if (sel.value) await act(() => api.addTeamRepository(team.team_id, Number(sel.value), ctx.token.signal), { done: '저장소를 연결했습니다' }); }), { cls: 'btn-primary' });
    add = h('div', { class: 'ai-row' }, sel, go);
  }
  return h('section', { class: 'ai-card' }, h('h2', { text: '연결된 저장소' }), list, add,
    h('p', { class: 'ai-muted', text: '연결하는 관리자가 그 저장소에 접근할 수 있어야 하며, 공유와 보고서는 연결된 저장소의 자료만 다룹니다.' }));
}

function sharesCard() {
  const { team } = ctx;
  const meId = ctx.me.id;
  const rows = ctx.shares.length ? ctx.shares.map((s) => {
    const mine = s.shared_by === meId;
    const detail = h('div', { class: 'tm-share-detail' });
    return h('li', {}, h('b', { text: TS.SHARE_TYPE_LABEL[s.resource_type] }), ' ', chip(TS.SHARE_STATUS_LABEL[s.status], s.status === 'active' ? 'ok' : 'warn'), ' ',
      h('span', { class: 'ai-muted', text: `${s.shared_by_login || ''} · ${fmtTime(s.created_at)}` }), ' ',
      button('내용 보기', async () => {
        clearNotices();
        try { const c = await api.shareContent(team.team_id, s.share_id, ctx.token.signal); if (ctx.token.current()) mount(detail, shareContentView(c)); }
        catch (e) { if (e?.name !== 'AbortError' && ctx.token.current()) { mount(detail); showError(e); } }
      }), ' ',
      (mine || team.role === 'admin') ? button('공유 해제', () => {
        if (!window.confirm('공유를 해제할까요? 이 팀의 기존 보고서는 차단되고 새로 생성해야 합니다.')) return;
        act(() => api.deleteShare(team.team_id, s.share_id, ctx.token.signal), { done: '공유를 해제했습니다' });
      }) : null, detail);
  }) : [h('li', { class: 'ai-muted', text: '공유된 자료가 없습니다. 팀 구성원이 직접 공유해야 여기에 나타납니다.' })];
  return h('section', { class: 'ai-card' }, h('h2', { text: '공유된 자료' }), h('p', { class: 'ai-muted', text: TS.SHARE_NOTE }), h('ul', { class: 'ho-targets' }, rows),
    TS.can(team.role, 'share') ? shareForm() : h('p', { class: 'ai-muted', text: '조회자는 자료를 공유할 수 없습니다.' }));
}

function shareContentView(c) {
  const content = c.content || {};
  if (c.resource_type === 'handover_target') {
    return h('div', { class: 'ai-banner info' }, h('div', { class: 'ai-banner-title', text: `${content.name} (파일 ${(content.selected_paths || []).length}개)` }),
      h('ul', {}, (content.items || []).map((i) => h('li', { text: `${i.label} — ${i.status_label}` }))), h('p', { class: 'ai-muted', text: content.note || '' }));
  }
  return h('div', { class: 'ai-banner info' }, h('div', { class: 'ai-banner-title', text: `지적 ${(content.findings || []).length}건` }),
    h('ul', {}, (content.findings || []).map((f) => h('li', {}, h('b', { text: f.title }), ' ', chip(TS.VERDICT_LABEL[f.verdict] || f.verdict, f.verdict === 'valid' ? 'ok' : 'warn'), ' ',
      h('code', { text: f.location ? `${f.location.path}:${f.location.start_line}` : '' }), f.condition ? h('div', { class: 'ai-muted', text: `조건: ${f.condition}` }) : null, f.impact ? h('div', { class: 'ai-muted', text: `영향: ${f.impact}` }) : null))),
    h('p', { class: 'ai-muted', text: content.note || '' }));
}

function shareForm() {
  const { team } = ctx;
  const repoSel = h('select', { class: 'ai-input', 'aria-label': '저장소' }, ctx.repos.map((r) => h('option', { value: String(r.repository_id), text: r.name || `#${r.repository_id}` })));
  const list = h('div', { class: 'ho-items' });
  const load = async () => {
    mount(list);
    if (!repoSel.value) return;
    const repositoryId = Number(repoSel.value);
    try {
      const [jobs, targets] = await Promise.all([api.jobs(repositoryId, { limit: 50, status: 'succeeded' }, ctx.token.signal).then((r) => r.jobs).catch(() => []), api.handoverTargets(repositoryId, ctx.token.signal).then((r) => r.targets).catch(() => [])]);
      if (!ctx.token.current()) return;
      const cands = TS.shareCandidates({ jobs, targets, shares: ctx.shares, repositoryId });
      mount(list, cands.length ? cands.map((c) => h('div', { class: 'ai-row' }, h('span', { text: c.label }), c.shared ? chip('이미 공유함', 'ok') : null,
        button(c.shared && c.type === 'handover_target' ? '다시 공유(현재 상태로)' : '팀에 공유', () => {
          if (!window.confirm('이 자료를 팀에 공유할까요? 팀 구성원이 내용을 볼 수 있습니다(코드 원문·인용과 내 메모는 제외).')) return;
          act(() => api.createShare(team.team_id, c.type, c.id, ctx.token.signal), { done: '공유했습니다' });
        }, { disabled: c.shared && c.type === 'review_result' }))) : h('p', { class: 'ai-muted', text: '이 저장소에서 공유할 수 있는 내 자료가 없습니다.' }));
    } catch (e) { if (e?.name !== 'AbortError') showError(e); }
  };
  repoSel.addEventListener('change', load);
  load();
  return h('div', {}, h('h3', { text: '내 자료 공유하기' }), ctx.repos.length ? [repoSel, list] : h('p', { class: 'ai-muted', text: '연결된 저장소가 없어 공유할 수 없습니다.' }));
}

/* ── 보고서 ── */
function reportsCard() {
  const { team } = ctx;
  const boxes = ctx.repos.map((r) => ({ r, input: h('input', { type: 'checkbox', value: String(r.repository_id), 'aria-label': `보고서에 포함: ${r.name || r.repository_id}` }) }));
  const generate = button('보고서 생성', () => run(generate, async () => {
    const ids = boxes.filter((b) => b.input.checked).map((b) => Number(b.input.value));
    if (!ids.length) { notice('warn', '저장소를 선택해 주세요', ''); return; }
    const out = await act(() => api.createTeamReport(team.team_id, ids, ctx.token.signal), { done: '보고서를 만들었습니다' });
    if (out && ctx.token.current()) navigate({ team: team.team_id, report: out.report_id });
  }), { cls: 'btn-primary' });
  const rows = ctx.reports.length ? ctx.reports.map((r) => h('li', {}, link(`보고서 ${fmtTime(r.created_at)}`, { team: team.team_id, report: r.report_id }), ' ',
    r.visibility_current ? chip('범위 유효(열 때 다시 확인)', 'info') : chip('범위가 바뀌어 차단됨', 'warn'), ' ',
    (team.role === 'admin' || (team.role === 'member' && r.created_by === ctx.me.id)) ? button('삭제', () => {
      if (window.confirm('보고서를 삭제할까요?')) act(() => api.deleteTeamReport(team.team_id, r.report_id, ctx.token.signal), { done: '삭제했습니다' });
    }) : null)) : [h('li', { class: 'ai-muted', text: '아직 만든 보고서가 없습니다.' })];
  return h('section', { class: 'ai-card' }, h('h2', { text: '보고서' }), h('p', { class: 'ai-muted', text: TS.REPORT_NOTE }), h('ul', { class: 'ho-targets' }, rows),
    TS.can(team.role, 'generate_report') ? h('div', {}, h('h3', { text: '새 보고서' }), h('div', { class: 'ho-items' }, boxes.map((b) => h('label', { class: 'ai-row' }, b.input, ` ${b.r.name || `저장소 #${b.r.repository_id}`}`))),
      h('p', { class: 'ai-muted', text: '포함한 모든 저장소에 접근할 수 있는 구성원만 이 보고서를 볼 수 있습니다. 일부만 접근하는 사람에게는 접근 범위에 맞춰 따로 생성해야 합니다.' }), h('div', { class: 'ai-row' }, generate))
      : h('p', { class: 'ai-muted', text: '조회자는 보고서를 만들 수 없습니다.' }));
}

async function openReport(reportId) {
  const token = ctx.token;
  const box = $('tm-report');
  if (!box) return;
  mount(box, h('section', { class: 'ai-card' }, h('p', { class: 'ai-muted', text: '보고서를 확인하는 중…' })));
  try {
    const r = await api.teamReport(route.team, reportId, token.signal);
    if (!token.current()) return;
    const stale = TS.staleLines(r.stale_reasons);
    mount(box, h('section', { class: 'ai-card', id: 'tm-report-card' }, h('h2', { text: '보고서' }),
      h('p', { class: 'ai-muted', text: `${TS.problemSummary(r.payload?.totals)} · 생성 ${fmtTime(r.created_at)}` }),
      stale.length ? h('div', { class: 'ai-banner warn' }, h('div', { class: 'ai-banner-title', text: '이 보고서의 기준이 바뀌었습니다' }), h('ul', {}, stale.map((l) => h('li', { text: l })))) : null,
      h('pre', { class: 'ai-pre tm-report-text', text: r.markdown })));
  } catch (e) {
    if (e?.name === 'AbortError' || !token.current()) return;
    mount(box);
    showError(e);                                  // 차단된 보고서는 이유만 보이고 내용은 없다
  }
}

/* ───────────────────────── 시작 ───────────────────────── */
load();
