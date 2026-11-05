import { request, isDesktop } from './platform/transport.js';
import { githubLogin } from './login.js';
import { createState, element } from './ui/components.js';
import { fmtDateTime } from './fmt.js';
/* ── 커뮤니티 페이지: 저장소별 글·댓글, 실시간 채팅·음성 채팅·공동 편집(앱), 멤버, 초대 링크 ──
   community-main이 있을 때(=커뮤니티 페이지)만 동작한다. 사용자 글은 모두 textContent로만 그린다.
   주소: ?id=<저장소 ID>&tab=posts|chat|collab|members|invites&post=<글 ID>, 초대 링크는 #invite=<토큰>. */

const mainEl = document.getElementById('community-main');
const listEl = document.getElementById('community-list');
const joinForm = document.getElementById('community-join');
const joinInput = document.getElementById('community-invite-input');
const joinMessage = document.getElementById('community-join-message');
const PENDING_INVITE = 'gitviz_community_invite';
const PAGE = 20;
const TOKEN = /(?:#invite=)?([A-Za-z0-9_-]{43})\s*$/;
let csrf = null;
let view = null; // 현재 커뮤니티 응답
let generation = 0;

class CommunityError extends Error {
  constructor(message, status, code) { super(message); this.status = status; this.code = code; }
}

async function loadCsrf() {
  const res = await request('/api/auth/csrf', { credentials: 'same-origin', cache: 'no-store' });
  const body = res.ok ? await res.json().catch(() => null) : null;
  if (!body?.token) throw new CommunityError('로그인이 필요합니다.', res.status, 'AUTH_REQUIRED');
  return body.token;
}

async function api(method, path, body, retried = false) {
  const headers = { Accept: 'application/json' };
  if (method !== 'GET') {
    headers['Content-Type'] = 'application/json';
    // 웹은 세션 쿠키라 CSRF 토큰을 붙인다. 데스크톱은 main의 bearer 세션이라 필요 없다.
    if (!isDesktop()) headers['X-CSRF-Token'] = csrf ??= await loadCsrf();
  }
  let res;
  try {
    res = await request(path, { method, headers, credentials: 'same-origin', cache: 'no-store', body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new CommunityError('서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요.', 0, 'NETWORK');
  }
  const data = await res.json().catch(() => null);
  if (res.ok) return data;
  const detail = data?.detail;
  const code = detail?.code || data?.error?.code || (res.status === 401 ? 'AUTH_REQUIRED' : null);
  if (code === 'CSRF_FAILED' && !retried) { csrf = null; return api(method, path, body, true); }
  const message = (typeof detail === 'object' && detail?.message) || (typeof detail === 'string' && detail) || data?.error?.message
    || (res.status === 401 ? '로그인이 필요합니다.' : res.status === 422 ? '입력한 내용을 확인하세요.' : `요청을 처리하지 못했습니다 (HTTP ${res.status}).`);
  throw new CommunityError(message, res.status, code);
}

const params = () => new URLSearchParams(location.search);
function go(changes) {
  const url = new URL(location.href);
  for (const [key, value] of Object.entries(changes)) {
    if (value === null || value === undefined || value === '') url.searchParams.delete(key); else url.searchParams.set(key, String(value));
  }
  history.pushState(null, '', url);
  void route();
}

function button(label, className, onClick) {
  const b = element('button', className, label); b.type = 'button'; b.addEventListener('click', onClick); return b;
}

/* 지우기·내보내기처럼 되돌리기 어려운 동작은 한 번 더 눌러야 실행한다. */
function confirmButton(label, onConfirm) {
  let armed = false;
  const b = button(label, 'btn-secondary', async () => {
    if (!armed) { armed = true; b.textContent = `${label} 확인`; b.classList.add('community-armed'); setTimeout(() => { armed = false; b.textContent = label; b.classList.remove('community-armed'); }, 4000); return; }
    b.disabled = true;
    try { await onConfirm(); } finally { b.disabled = false; }
  });
  return b;
}

function who(user) { return user ? (user.display_name ? `${user.display_name} (@${user.github_login})` : `@${user.github_login}`) : '알 수 없음'; }

function showError(target, error, retry) {
  if (error.code === 'AUTH_REQUIRED') {
    const box = createState({ kind: 'empty', title: '로그인이 필요합니다', message: 'GitHub 계정으로 로그인하면 기여한 저장소의 커뮤니티에 자동으로 참여합니다.' });
    box.querySelector('div').append(button('GitHub로 로그인', 'btn-primary', () => githubLogin()));
    target.replaceChildren(box); return;
  }
  target.replaceChildren(createState({ kind: 'error', title: '불러오지 못했습니다', message: error.message, retry }));
}

async function loadList() {
  listEl.replaceChildren(element('p', 'p-sub', '불러오는 중…'));
  try {
    const { communities } = await api('GET', '/api/community/v1/communities');
    listEl.replaceChildren();
    if (!communities.length) { listEl.append(element('p', 'p-sub', '아직 참여한 커뮤니티가 없습니다. 기여한 저장소를 분석하거나 초대 링크로 참여하세요.')); return; }
    const current = Number(params().get('id'));
    for (const item of communities) {
      const b = button(item.repository.name || item.repository.url, 'community-list-item', () => go({ id: item.repository.id, tab: null, post: null }));
      if (item.repository.id === current) b.setAttribute('aria-current', 'true');
      b.append(element('span', 'community-badge', item.role === 'admin' ? '관리자' : item.joined ? '멤버' : '참여 가능'));
      listEl.append(b);
    }
  } catch (error) {
    if (error.code === 'AUTH_REQUIRED') listEl.replaceChildren(element('p', 'p-sub', '로그인하면 내 커뮤니티가 보입니다.'));
    else listEl.replaceChildren(element('p', 'p-sub', error.message));
    if (!params().get('id')) showError(mainEl, error, route);
  }
}

async function route() {
  const run = ++generation;
  stopCollab();
  const id = Number(params().get('id'));
  // 같은 커뮤니티 안에서 탭만 바꿀 때는 실시간 연결과 음성 채팅을 그대로 둔다.
  if (hub && hub.repositoryId !== id) stopHub();
  if (!id) {
    view = null;
    mainEl.replaceChildren(createState({ kind: 'empty', title: '커뮤니티를 고르세요', message: '위 목록에서 저장소 커뮤니티를 고르거나 받은 초대 링크로 참여하세요.' }));
    return;
  }
  const target = hub ? hub.content : mainEl;
  target.replaceChildren(createState({ kind: 'loading', title: '커뮤니티를 여는 중' }));
  try {
    const value = await api('GET', `/api/community/v1/repositories/${id}`);
    if (run !== generation) return;
    view = value;
    renderCommunity();
  } catch (error) {
    if (run !== generation) return;
    if (error.code === 'INVITE_REQUIRED') {
      stopHub();
      mainEl.replaceChildren(createState({ kind: 'empty', title: '초대가 필요합니다', message: error.message }));
    } else showError(hub ? hub.content : mainEl, error, route);
  }
}

function renderCommunity() {
  const tab = params().get('tab') || 'posts';
  const admin = view.me.role === 'admin';
  const head = element('section', 'panel community-head');
  const title = element('h2', 'ui-panel-title', view.repository.name || view.repository.url);
  const meta = element('p', 'p-sub', `멤버 ${view.member_count}명 · 관리자 ${who(view.admin)} · 내 역할 ${admin ? '관리자' : '멤버'}`);
  const tabs = element('div', 'ui-toolbar community-tabs'); tabs.setAttribute('role', 'tablist');
  for (const [key, label] of [['posts', '글'], ['chat', '채팅'], ...(collabAvailable() ? [['collab', '공동 편집']] : []), ['members', '멤버'], ...(admin ? [['invites', '초대 링크']] : [])]) {
    const t = button(label, key === tab ? 'btn-primary' : 'btn-secondary', () => go({ tab: key === 'posts' ? null : key, post: null }));
    t.setAttribute('role', 'tab'); t.setAttribute('aria-selected', String(key === tab)); t.dataset.tab = key;
    tabs.append(t);
  }
  head.append(title, meta, tabs);
  const body = element('div', 'community-body');
  const live = ensureHub();
  live.listeners.clear(); // 이전 탭의 실시간 처리기
  live.content.replaceChildren(head, body);
  stopCollab();
  const post = Number(params().get('post'));
  if (tab === 'chat') void renderChat(body);
  else if (tab === 'collab' && collabAvailable()) void renderCollab(body);
  else if (tab === 'members') void renderMembers(body);
  else if (tab === 'invites' && admin) void renderInvites(body);
  else if (post) void renderPost(body, post);
  else void renderPosts(body);
}

const repoPath = suffix => `/api/community/v1/repositories/${view.repository.id}${suffix}`;

function postForm({ title = '', body = '', submitLabel, onSubmit, onCancel }) {
  const form = element('form', 'panel community-form');
  const titleInput = element('input'); titleInput.type = 'text'; titleInput.maxLength = 200; titleInput.required = true; titleInput.value = title;
  titleInput.placeholder = '제목'; titleInput.setAttribute('aria-label', '글 제목');
  const bodyInput = element('textarea'); bodyInput.maxLength = 20000; bodyInput.required = true; bodyInput.rows = 6; bodyInput.value = body;
  bodyInput.placeholder = '내용'; bodyInput.setAttribute('aria-label', '글 내용');
  const message = element('p', 'p-sub'); message.setAttribute('role', 'status');
  const actions = element('div', 'ui-toolbar');
  const submit = element('button', 'btn-primary', submitLabel); submit.type = 'submit';
  actions.append(submit); if (onCancel) actions.append(button('취소', 'btn-secondary', onCancel));
  form.append(titleInput, bodyInput, actions, message);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!titleInput.value.trim() || !bodyInput.value.trim()) { message.textContent = '제목과 내용을 입력하세요.'; return; }
    submit.disabled = true; message.textContent = '';
    try { await onSubmit(titleInput.value.trim(), bodyInput.value); }
    catch (error) { message.textContent = error.message; }
    finally { submit.disabled = false; }
  });
  return form;
}

async function renderPosts(target) {
  const composeSlot = element('div');
  const open = button('새 글 쓰기', 'btn-primary', () => {
    open.hidden = true;
    composeSlot.replaceChildren(postForm({ submitLabel: '등록', onCancel: () => { composeSlot.replaceChildren(); open.hidden = false; },
      onSubmit: async (title, body) => { const { id } = await api('POST', repoPath('/posts'), { title, body }); go({ post: id }); } }));
    composeSlot.querySelector('input')?.focus();
  });
  const list = element('div', 'community-posts');
  const more = button('더 보기', 'btn-secondary', () => load(more.dataset.before)); more.hidden = true;
  target.replaceChildren(open, composeSlot, list, more);
  async function load(before) {
    more.disabled = true;
    try {
      const page = await api('GET', repoPath(`/posts?limit=${PAGE}${before ? `&before=${before}` : ''}`));
      if (!before && !page.posts.length) list.append(createState({ kind: 'empty', title: '아직 글이 없습니다', message: '첫 글을 남겨 보세요.' }));
      for (const item of page.posts) {
        const card = element('article', 'panel community-post-card');
        const link = button(item.title, 'community-post-title', () => go({ post: item.id }));
        card.append(link, element('p', 'community-post-snippet', item.body),
          element('p', 'p-sub', `${who(item.author)} · ${fmtDateTime(item.created_at)} · 댓글 ${item.comment_count}`));
        list.append(card);
      }
      more.hidden = !page.next_before; more.dataset.before = page.next_before ?? '';
    } catch (error) { showError(list, error, () => load(before)); }
    finally { more.disabled = false; }
  }
  await load();
}

async function renderPost(target, postId) {
  target.replaceChildren(createState({ kind: 'loading', title: '글을 여는 중' }));
  let post;
  try { post = await api('GET', repoPath(`/posts/${postId}`)); }
  catch (error) { showError(target, error, () => renderPost(target, postId)); return; }
  const mine = !!post.author_id && post.author_id === myId();
  const admin = view.me.role === 'admin';
  const article = element('article', 'panel community-post');
  const back = button('‹ 글 목록', 'btn-secondary', () => go({ post: null }));
  const heading = element('h2', 'ui-panel-title', post.title);
  const meta = element('p', 'p-sub', `${who(post.author)} · ${fmtDateTime(post.created_at)}${post.updated_at ? ' · 수정됨' : ''}`);
  const body = element('div', 'community-text', post.body);
  const actions = element('div', 'ui-toolbar');
  if (mine) actions.append(button('수정', 'btn-secondary', () => {
    article.replaceWith(postForm({ title: post.title, body: post.body, submitLabel: '저장', onCancel: () => renderPost(target, postId),
      onSubmit: async (title, text) => { await api('PATCH', repoPath(`/posts/${postId}`), { title, body: text }); await renderPost(target, postId); } }));
  }));
  if (mine || admin) actions.append(confirmButton('삭제', async () => { await api('DELETE', repoPath(`/posts/${postId}`)); go({ post: null }); }));
  article.append(back, heading, meta, body, actions);
  const comments = element('section', 'panel community-comments');
  comments.append(element('h3', 'ui-panel-title', `댓글 ${post.comments.filter(c => !c.deleted).length}`));
  for (const comment of post.comments) comments.append(commentView(comment, target, postId, admin));
  const form = element('form', 'community-comment-form');
  const input = element('textarea'); input.rows = 3; input.maxLength = 5000; input.placeholder = '댓글을 남기세요'; input.setAttribute('aria-label', '댓글');
  const submit = element('button', 'btn-primary', '댓글 등록'); submit.type = 'submit';
  const message = element('p', 'p-sub'); message.setAttribute('role', 'status');
  form.append(input, submit, message);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!input.value.trim()) return;
    submit.disabled = true;
    try { await api('POST', repoPath(`/posts/${postId}/comments`), { body: input.value }); await renderPost(target, postId); }
    catch (error) { message.textContent = error.message; submit.disabled = false; }
  });
  comments.append(form);
  target.replaceChildren(article, comments);
}

function commentView(comment, target, postId, admin) {
  const item = element('div', 'community-comment');
  if (comment.deleted) { item.append(element('p', 'p-sub', '삭제된 댓글입니다.')); return item; }
  const mine = comment.author_id === myId();
  item.append(element('p', 'p-sub', `${who(comment.author)} · ${fmtDateTime(comment.created_at)}${comment.updated_at ? ' · 수정됨' : ''}`),
    element('div', 'community-text', comment.body));
  const actions = element('div', 'ui-toolbar');
  if (mine) actions.append(button('수정', 'btn-secondary', () => {
    const editor = element('form', 'community-comment-form');
    const input = element('textarea'); input.rows = 3; input.maxLength = 5000; input.value = comment.body; input.setAttribute('aria-label', '댓글 수정');
    const save = element('button', 'btn-primary', '저장'); save.type = 'submit';
    const message = element('p', 'p-sub'); message.setAttribute('role', 'status');
    editor.append(input, save, button('취소', 'btn-secondary', () => editor.replaceWith(item)), message);
    editor.addEventListener('submit', async event => {
      event.preventDefault();
      try { await api('PATCH', repoPath(`/comments/${comment.id}`), { body: input.value }); await renderPost(target, postId); }
      catch (error) { message.textContent = error.message; }
    });
    item.replaceWith(editor);
  }));
  if (mine || admin) actions.append(confirmButton('삭제', async () => { await api('DELETE', repoPath(`/comments/${comment.id}`)); await renderPost(target, postId); }));
  if (actions.childElementCount) item.append(actions);
  return item;
}

/* 내 사용자 ID: 멤버 목록·글 작성자 비교용. 서버 응답의 me에는 ID가 없으므로 /api/auth/me로 한 번만 확인한다. */
let me = null;
const myId = () => me?.id ?? null;
async function loadMe() {
  try { const res = await request('/api/auth/me', { credentials: 'same-origin', cache: 'no-store' }); if (res.ok) me = await res.json(); } catch { me = null; }
}

const JOINED = { analyzer: '첫 분석', contributor: '기여자', invite: '초대' };
async function renderMembers(target) {
  target.replaceChildren(createState({ kind: 'loading', title: '멤버를 불러오는 중' }));
  let members;
  try { ({ members } = await api('GET', repoPath('/members'))); }
  catch (error) { showError(target, error, () => renderMembers(target)); return; }
  const admin = view.me.role === 'admin';
  const panel = element('section', 'panel');
  const message = element('p', 'p-sub'); message.setAttribute('role', 'status');
  const list = element('ul', 'community-members');
  for (const member of members) {
    const row = element('li', 'community-member');
    row.append(element('span', 'community-member-name', who(member.user)),
      element('span', 'community-badge', member.role === 'admin' ? '관리자' : member.status === 'removed' ? '내보냄' : '멤버'),
      element('span', 'p-sub', `${JOINED[member.joined_via] || ''} · ${fmtDateTime(member.joined_at)}`));
    if (admin && member.role !== 'admin' && member.user) {
      const act = async (action, done) => {
        try { await action(); message.textContent = done; await refreshCommunity(); }
        catch (error) { message.textContent = error.message; }
      };
      if (member.status === 'active') {
        row.append(confirmButton('관리자 넘기기', () => act(() => api('POST', repoPath('/admin'), { user_id: member.user.id }), `${who(member.user)}에게 관리자를 넘겼습니다.`)),
          confirmButton('내보내기', () => act(() => api('PATCH', repoPath(`/members/${member.user.id}`), { status: 'removed' }), '내보냈습니다.')));
      } else {
        row.append(button('복원', 'btn-secondary', () => act(() => api('PATCH', repoPath(`/members/${member.user.id}`), { status: 'active' }), '다시 참여시켰습니다.')));
      }
    }
    list.append(row);
  }
  panel.append(element('h3', 'ui-panel-title', `멤버 ${members.filter(m => m.status === 'active').length}명`), list, message);
  if (admin) panel.append(element('p', 'p-sub', '관리자를 넘기면 나는 일반 멤버가 됩니다. 내보낸 사람은 기여자여도, 초대 링크가 있어도 다시 들어오지 못하고 복원해야 합니다.'));
  target.replaceChildren(panel);
}

async function refreshCommunity() {
  view = await api('GET', `/api/community/v1/repositories/${view.repository.id}`);
  renderCommunity(); void loadList();
}

async function renderInvites(target) {
  const panel = element('section', 'panel');
  const form = element('form', 'ui-toolbar community-invite-form');
  const expires = element('select', 'ui-filter'); expires.setAttribute('aria-label', '유효 기간');
  for (const [hours, label] of [[24, '1일'], [168, '7일'], [720, '30일']]) { const o = element('option', '', `${label} 동안 유효`); o.value = String(hours); expires.append(o); }
  expires.value = '168';
  const uses = element('select', 'ui-filter'); uses.setAttribute('aria-label', '사용 횟수');
  for (const [value, label] of [['', '사용 횟수 제한 없음'], ['1', '1명만'], ['5', '5명까지'], ['20', '20명까지']]) { const o = element('option', '', label); o.value = value; uses.append(o); }
  const create = element('button', 'btn-primary', '초대 링크 만들기'); create.type = 'submit';
  form.append(expires, uses, create);
  const created = element('div', 'community-invite-created');
  const active = element('ul', 'community-invites');
  panel.append(element('h3', 'ui-panel-title', '초대 링크'),
    element('p', 'p-sub', '링크를 가진 사람은 기여자가 아니어도 참여할 수 있습니다. 링크는 만들 때 한 번만 보이니 바로 복사해 전달하세요.'), form, created, active);
  target.replaceChildren(panel);
  form.addEventListener('submit', async event => {
    event.preventDefault(); create.disabled = true;
    try {
      const result = await api('POST', repoPath('/invites'), { expires_in_hours: Number(expires.value), ...(uses.value ? { max_uses: Number(uses.value) } : {}) });
      const field = element('input', 'community-invite-url'); field.readOnly = true; field.value = result.url; field.setAttribute('aria-label', '만든 초대 링크');
      const status = element('span', 'p-sub');
      const copy = button('복사', 'btn-secondary', async () => {
        try { await navigator.clipboard.writeText(result.url); status.textContent = '복사했습니다.'; }
        catch { field.select(); status.textContent = '링크를 선택해 두었으니 직접 복사하세요.'; }
      });
      created.replaceChildren(field, copy, status);
      await listInvites();
    } catch (error) { created.replaceChildren(element('p', 'p-sub', error.message)); }
    finally { create.disabled = false; }
  });
  async function listInvites() {
    try {
      const { invites } = await api('GET', repoPath('/invites'));
      active.replaceChildren();
      if (!invites.length) { active.append(element('li', 'p-sub', '사용할 수 있는 초대 링크가 없습니다.')); return; }
      for (const invite of invites) {
        const row = element('li', 'community-member');
        row.append(element('span', '', `${fmtDateTime(invite.expires_at)}까지`),
          element('span', 'p-sub', invite.max_uses ? `${invite.use_count}/${invite.max_uses}명 사용` : `${invite.use_count}명 사용`),
          confirmButton('취소', async () => { await api('DELETE', repoPath(`/invites/${invite.id}`)); await listInvites(); }));
        active.append(row);
      }
    } catch (error) { active.replaceChildren(element('li', 'p-sub', error.message)); }
  }
  await listInvites();
}

/* ── 실시간 단체 채팅 (AD-27) ──
   보내기는 일반 요청, 받기는 서버 전송 이벤트다. 앱은 main이 로그인 토큰으로 스트림을 받아 desktop.community로 넘기고,
   웹은 같은 출처 EventSource(세션 쿠키)를 쓴다. 어느 쪽이든 이 화면에는 { type, data } 객체만 들어온다. */
const LIVE_TYPES = ['ready', 'message', 'deleted', 'presence', 'removed', 'voice', 'voice-signal', 'collab-sessions', 'collab'];
let closeLive = null;
function stopLive() { const close = closeLive; closeLive = null; close?.(); }

function openLive(repositoryId, onEvent) {
  stopLive();
  let open = true;
  const emit = event => { if (open) onEvent(event); };
  if (isDesktop()) {
    const bridge = globalThis.desktop?.community;
    if (!bridge) { emit({ type: 'status', data: { state: 'stopped', code: 'UNAVAILABLE' } }); return; }
    bridge.subscribe({ repositoryId }, emit).catch(error => emit({ type: 'status', data: { state: 'stopped', code: error?.message || 'UNAVAILABLE' } }));
    closeLive = () => { open = false; void bridge.unsubscribe().catch(() => {}); };
    return;
  }
  const source = new EventSource(`/api/community/v1/repositories/${repositoryId}/chat/stream`);
  for (const type of LIVE_TYPES) source.addEventListener(type, event => {
    let data; try { data = JSON.parse(event.data); } catch { return; }
    if (!data || typeof data !== 'object') return;
    emit({ type, data });
    if (type === 'removed') { source.close(); emit({ type: 'status', data: { state: 'stopped', code: 'MEMBER_REMOVED' } }); }
  });
  // 끊기면 브라우저가 알아서 다시 붙는다. 권한이 없어 거절되면(CLOSED) 더 시도하지 않는다.
  source.addEventListener('error', () => emit({ type: 'status', data: source.readyState === EventSource.CLOSED ? { state: 'stopped', code: 'NETWORK' } : { state: 'reconnecting' } }));
  closeLive = () => { open = false; source.close(); };
}

/* 커뮤니티 하나를 여는 동안 실시간 연결과 음성 채팅은 하나씩만 둔다. 음성 채팅은 화면 왼쪽에 늘 떠 있고 탭을 바꿔도 끊기지 않는다.
   채팅·공동 편집 탭은 listen()으로 같은 연결의 이벤트를 받는다. 탭이 늦게 열려도 지금 연결 상태(ready 또는 status)를 먼저 받는다. */
let hub = null;
function stopHub() {
  const current = hub; hub = null;
  if (!current) return;
  current.listeners.clear(); stopVoice(); stopLive();
}

function ensureHub() {
  const repositoryId = view.repository.id;
  if (hub?.repositoryId === repositoryId) return hub;
  stopHub();
  const side = element('aside', 'community-stage-side'); side.setAttribute('aria-label', '음성 채팅');
  const voicePanel = element('section', 'panel community-voice');
  side.append(voicePanel);
  const content = element('div', 'community-stage-main');
  const stage = element('div', 'community-stage');
  stage.append(side, content);
  mainEl.replaceChildren(stage);
  const current = { repositoryId, content, listeners: new Set(), ready: false, online: [], status: null };
  hub = current;
  // 나갈 때도 이 커뮤니티 주소로 보낸다(다른 커뮤니티로 옮기는 중에 view가 바뀌어도).
  const voice = createVoice({ panel: voicePanel, path: suffix => `/api/community/v1/repositories/${repositoryId}${suffix}` });
  function onEvent(event) {
    if (hub !== current) return;
    const { type, data } = event;
    if (type === 'ready') { current.ready = true; current.status = null; if (Array.isArray(data.online)) current.online = data.online; void voice.refresh(); }
    else if (type === 'presence') { if (Array.isArray(data.online)) current.online = data.online; }
    else if (type === 'voice') voice.update(data);
    else if (type === 'voice-signal') voice.signal(data);
    else if (type === 'status') {
      current.ready = false; current.status = event;
      if (data.state === 'stopped') {
        closeLive = null;
        if (CHAT_FINAL.includes(data.code) && voice.joined) void voice.leave(CHAT_STOPPED[data.code]);
      }
    }
    for (const listener of [...current.listeners]) listener(event);
  }
  current.connect = () => {
    current.ready = false; current.status = { type: 'status', data: { state: 'connecting' } };
    openLive(repositoryId, onEvent);
  };
  current.listen = listener => {
    current.listeners.add(listener);
    if (current.ready) listener({ type: 'ready', data: { online: current.online } });
    else if (current.status) listener(current.status);
  };
  current.connect();
  return current;
}

const CHAT_STOPPED = {
  MEMBER_REMOVED: '커뮤니티에서 내보내져 채팅을 볼 수 없습니다.',
  AUTH_REQUIRED: '로그인이 끝나 채팅 연결을 멈췄습니다. 다시 로그인하세요.',
  ACCOUNT_CHANGED: '계정이 바뀌어 채팅 연결을 멈췄습니다.',
  PERMISSION_DENIED: '이 커뮤니티의 채팅에 참여할 수 없습니다.',
  NOT_FOUND: '커뮤니티를 찾을 수 없습니다.',
  SERVER_NOT_CONFIGURED: '서버가 설정되지 않아 채팅을 쓸 수 없습니다.',
};
const CHAT_FINAL = ['MEMBER_REMOVED', 'PERMISSION_DENIED', 'NOT_FOUND', 'SERVER_NOT_CONFIGURED'];

async function renderChat(target) {
  const run = generation;
  const admin = view.me.role === 'admin';
  const panel = element('section', 'panel community-chat');
  const bar = element('div', 'ui-toolbar community-chat-bar');
  const status = element('p', 'p-sub community-chat-status', '연결하는 중…'); status.setAttribute('role', 'status');
  const reconnect = button('다시 연결', 'btn-secondary', () => connect()); reconnect.hidden = true;
  bar.append(status, reconnect);
  const older = button('이전 메시지', 'btn-secondary community-chat-older', () => loadPage(older.dataset.before)); older.hidden = true;
  const log = element('div', 'community-chat-log'); log.setAttribute('role', 'log'); log.setAttribute('aria-label', '채팅 메시지'); log.tabIndex = 0;
  const empty = element('p', 'p-sub community-chat-empty', '아직 메시지가 없습니다. 먼저 인사해 보세요.'); empty.hidden = true;
  const form = element('form', 'community-chat-form');
  const input = element('textarea'); input.rows = 2; input.maxLength = 2000; input.placeholder = '메시지 입력 (Enter 보내기, Shift+Enter 줄바꿈)'; input.setAttribute('aria-label', '채팅 메시지');
  const send = element('button', 'btn-primary', '보내기'); send.type = 'submit';
  const message = element('p', 'p-sub'); message.setAttribute('role', 'status');
  form.append(input, send);
  panel.append(bar, older, log, empty, form, message);
  const side = element('aside', 'community-chat-side');
  const onlinePanel = element('section', 'panel community-chat-online');
  const onlineTitle = element('h3', 'ui-panel-title', '접속 중');
  const online = element('ul', 'community-chat-people');
  onlinePanel.append(onlineTitle, online);
  side.append(onlinePanel);
  const layout = element('div', 'community-chat-layout');
  layout.append(panel, side);
  target.replaceChildren(layout);
  const live = hub;

  const rows = new Map();
  const nearBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 48;
  function row(item) {
    const mine = !!item.author_id && item.author_id === myId();
    const node = element('div', mine ? 'community-chat-message community-chat-mine' : 'community-chat-message'); node.dataset.id = String(item.id);
    node.append(element('p', 'p-sub', `${who(item.author)} · ${fmtDateTime(item.created_at)}`), element('div', 'community-text', item.body));
    if (mine || admin) node.append(confirmButton('삭제', async () => {
      try { await api('DELETE', repoPath(`/chat/messages/${item.id}`)); drop(item.id); }
      catch (error) { message.textContent = error.message; }
    }));
    return node;
  }
  function add(item) {
    if (!item || !Number.isSafeInteger(item.id) || rows.has(item.id) || typeof item.body !== 'string') return;
    const stick = nearBottom();
    const node = row(item); rows.set(item.id, node);
    // id 순서대로 끼워 넣는다. 실시간으로 온 것과 기록을 다시 읽은 것이 겹쳐도 한 번만 보인다.
    const after = [...log.children].find(child => Number(child.dataset.id) > item.id);
    log.insertBefore(node, after ?? null);
    empty.hidden = true;
    if (stick && !after) log.scrollTop = log.scrollHeight;
  }
  function drop(id) { rows.get(id)?.remove(); rows.delete(id); empty.hidden = rows.size > 0; }
  function people(list) {
    if (!Array.isArray(list)) return;
    online.replaceChildren(...list.map(user => element('li', 'community-member-name', who(user))));
    onlineTitle.textContent = `접속 중 ${list.length}명`;
  }
  let paged = false;
  async function loadPage(before) {
    older.disabled = true;
    try {
      const page = await api('GET', repoPath(`/chat/messages?limit=50${before ? `&before=${before}` : ''}`));
      if (run !== generation) return;
      const fromBottom = log.scrollHeight - log.scrollTop;
      for (const item of page.messages) add(item);
      // 이전 메시지를 위에 붙여도 보던 위치는 그대로 둔다. 다시 연결해 최근 기록을 채울 때는 '이전 메시지' 위치를 건드리지 않는다.
      if (before) log.scrollTop = log.scrollHeight - fromBottom;
      else if (!paged) log.scrollTop = log.scrollHeight;
      if (before || !paged) { older.hidden = !page.next_before; older.dataset.before = page.next_before ?? ''; paged = true; }
      empty.hidden = rows.size > 0;
    } catch (error) { message.textContent = error.message; }
    finally { older.disabled = false; }
  }
  let ready = false;
  function setInput(enabled) { input.disabled = !enabled; send.disabled = !enabled; }
  function onEvent({ type, data }) {
    if (run !== generation) return;
    if (type === 'ready') {
      status.textContent = '실시간 연결됨'; reconnect.hidden = true; setInput(true); people(data.online);
      // 다시 연결됐다면 끊긴 사이의 메시지를 채운다.
      if (ready) void loadPage();
      ready = true;
    } else if (type === 'message') add(data);
    else if (type === 'deleted') drop(data.id);
    else if (type === 'presence') people(data.online);
    else if (type === 'status') {
      if (data.state === 'connecting' && !ready) status.textContent = '연결하는 중…';
      else if (data.state === 'reconnecting') status.textContent = '연결이 끊겨 다시 연결하는 중…';
      else if (data.state === 'stopped') {
        status.textContent = CHAT_STOPPED[data.code] || '채팅 연결이 끊겼습니다.';
        people([]);
        const final = CHAT_FINAL.includes(data.code);
        reconnect.hidden = final;
        if (final) setInput(false);
      }
    }
  }
  function connect() { status.textContent = '연결하는 중…'; reconnect.hidden = true; live.connect(); }

  input.addEventListener('keydown', event => {
    // 한글 조합 중 Enter는 글자를 확정하는 것이라 보내지 않는다.
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); form.requestSubmit(); }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const text = input.value;
    if (!text.trim() || send.disabled) return;
    send.disabled = true; message.textContent = '';
    try { add(await api('POST', repoPath('/chat/messages'), { body: text })); input.value = ''; log.scrollTop = log.scrollHeight; }
    catch (error) { message.textContent = error.message; }
    finally { send.disabled = input.disabled; input.focus(); }
  });
  await loadPage();
  if (run !== generation) return;
  live.listen(onEvent);
}

/* ── 실시간 공동 편집 (AD-29 → AD-34 파일 묶음, 앱 전용) ──
   호스트가 시작 파일을 고르면 앱이 그 파일이 불러오는 HTML·CSS·JS·이미지·글꼴을 따라가 묶음을 만들고, 호스트가 확인한 뒤 공유한다.
   최대 3명이 함께 고친다. 편집기는 CodeMirror 6, 동시 편집은 Yjs(CRDT). 파일은 호스트 PC에만 있고 저장도 호스트 앱만 한다.
   공유 문서는 '목록' 문서(파일·이미지 목록, 미리보기 시작 파일) 하나와 텍스트 파일마다 문서 하나로 나뉘고, 메시지에 상대 경로(path)를 붙인다.
   이미지·글꼴은 읽기 전용이며 호스트가 조각으로 나눠 보내고 받는 쪽이 해시로 확인한다.
   편집 중 새로 생긴 참조는 호스트가 승인해야 호스트 앱이 읽어 공유한다. 서버는 자리와 메시지만 전달하고 내용을 저장하지 않는다. */
let closeCollab = null;
function stopCollab() { const close = closeCollab; closeCollab = null; close?.(); }
const COLLAB_COLORS = ['#2563eb', '#db2777', '#059669', '#d97706', '#7c3aed', '#0891b2'];
const collabAvailable = () => isDesktop() && !!globalThis.desktop?.collab?.openBundle;
let collabLib = null;
async function loadCollabLib() {
  if (!collabLib) { await import(new URL('./collab-editor.bundle.js', import.meta.url).href); collabLib = globalThis.GitVizCollab; }
  if (!collabLib?.createManifest) throw new Error('편집기를 불러오지 못했습니다.');
  return collabLib;
}
function toBase64(bytes) {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}
function fromBase64(text) { const binary = atob(text); const bytes = new Uint8Array(binary.length); for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i); return bytes; }
function seatColor(seat) { let h = 0; for (const c of seat) h = (h * 31 + c.charCodeAt(0)) >>> 0; return COLLAB_COLORS[h % COLLAB_COLORS.length]; }
async function sha256Hex(bytes) { return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(b => b.toString(16).padStart(2, '0')).join(''); }
const fmtSize = bytes => bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)}MB` : bytes >= 1024 ? `${Math.ceil(bytes / 1024)}KB` : `${bytes}B`;
const baseName = path => path.slice(path.lastIndexOf('/') + 1);
const isHtml = path => /\.html?$/i.test(path || '');
const ASSET_PIECE = 128 * 1024; // 원본 바이트. base64로 약 17만 자라 relay 한 번(20만 자)에 들어간다.
const MAX_ASSET = 2 * 1024 * 1024;
const FILE_ERRORS = {
  FILE_TOO_LARGE: '크기 제한을 넘는 파일입니다(텍스트 100KB, 이미지·글꼴 2MB).', NOT_TEXT: 'UTF-8 텍스트 파일만 공유할 수 있습니다.', INVALID_FILE: '일반 파일만 공유할 수 있습니다.',
  FILE_CHANGED: '파일이 공유한 뒤 다른 곳에서 바뀌어 저장하지 않았습니다.', SAVE_FAILED: '파일을 저장하지 못했습니다.', NOT_OPEN: '공유한 파일이 닫혔습니다.',
  NOT_ALLOWED: '공유할 수 없는 파일입니다(숨김 파일, 링크, 지원하지 않는 형식 등).', OUTSIDE_ROOT: '공유 범위(루트 폴더) 밖의 파일입니다. 먼저 범위를 넓히세요.',
  CANNOT_WIDEN: '더 넓힐 수 없습니다(Git 저장소 루트, 세 단계 위, 드라이브·사용자 폴더 제한).', NOT_PENDING: '추가할 수 있는 파일이 아닙니다.',
  LIMIT: '묶음 한도를 넘습니다(텍스트 20개·1MB, 이미지·글꼴 30개·10MB).', PREVIEW_FAILED: '미리보기를 열지 못했습니다.',
  EXISTS: '이미 있는 파일입니다. ‘파일 추가’로 넣으세요.', BUSY: '다른 실행이 끝난 뒤 다시 실행하세요.', STOPPED: '실행을 멈췄습니다.',
  NO_COMPILER: '이 PC에 필요한 컴파일러가 없습니다.', SANDBOX_UNAVAILABLE: '실행 제한(Job Object)을 걸 수 없어 실행하지 않았습니다. PowerShell이 제한 모드인지 확인하세요.',
  RUN_UNAVAILABLE: '실행 환경을 시작하지 못했습니다.',
};
const fileError = error => FILE_ERRORS[(error?.message || '').replace(/^.*Error: /, '')] || error?.message || '파일을 다루지 못했습니다.';
const REASONS = { outside: '공유 범위 밖', hidden: '숨김·제외 폴더', link: '링크·바로 가기', type: '지원하지 않는 형식', size: '크기 초과', missing: '파일 없음', limit: '묶음 한도 초과',
  signature: '형식이 확장자와 다름', 'not-text': '텍스트가 아님' };

async function renderCollab(target) {
  const run = generation;
  const main = element('section', 'panel community-collab');
  target.replaceChildren(main);
  const live = hub;
  const desktop = globalThis.desktop.collab;

  const status = element('p', 'p-sub community-collab-status'); status.setAttribute('role', 'status');
  let sessions = [];
  let draft = null; // 호스트가 공유하기 전 확인 중인 묶음
  let current = null;

  /* ── 메시지 ── */
  function send(kind, data, to, path) {
    const mine = current;
    if (!mine) return Promise.resolve();
    mine.outbox = mine.outbox.then(() => current === mine && !mine.ended
      ? api('POST', repoPath(`/collab/${mine.sid}/relay`), { seat_id: mine.seat, kind, data: toBase64(data), ...(to ? { to } : {}), ...(path ? { path } : {}) }) : null)
      .catch(error => { if (current === mine && error.code === 'COLLAB_ENDED') end('공동 편집이 끝났습니다.'); });
    return mine.outbox;
  }
  function sessionOf(sid) { return sessions.find(s => s.session_id === sid) ?? null; }
  function others() { const s = current && sessionOf(current.sid); return s ? s.seats.filter(seat => seat.seat_id !== current.seat) : []; }
  function hostSeat() { return sessionOf(current?.sid)?.seats.find(seat => seat.host)?.seat_id ?? current?.hostSeat ?? null; }
  function docOf(path) { return path ? current.files.get(path)?.doc ?? null : current.manifest.doc; }
  function requestSync(path = '') {
    // 내가 가진 상태(state vector)를 보내 빠진 변경분을 받는다. 처음 들어온 손님은 호스트에게서 전체를 받는다.
    if (!current || current.ended) return;
    const doc = docOf(path);
    if (!doc) return;
    const vector = collabLib.encodeStateVector(doc);
    for (const seat of others()) void send('sync-request', vector, seat.seat_id, path);
  }
  function textsOf(mine = current) {
    const texts = {};
    for (const [path, file] of mine.files) texts[path] = file.ytext.toString();
    return texts;
  }

  /* ── 목록 화면 ── */
  function renderList() {
    if (current || draft) return;
    const share = button('내 파일 공유하기', 'btn-primary', () => void openDraft());
    const intro = element('p', 'p-sub', '시작 파일을 고르면 그 파일이 불러오는 CSS·JS·이미지·글꼴을 함께 찾아 최대 3명이 같이 고칩니다. 파일은 공유한 사람 PC에만 있고, 저장도 공유한 사람만 합니다. HTML은 앱 안에서 미리볼 수 있습니다.');
    const list = element('div', 'community-collab-list');
    if (!sessions.length) list.append(element('p', 'p-sub', '진행 중인 공동 편집이 없습니다.'));
    for (const session of sessions) {
      const card = element('div', 'community-collab-card');
      const full = session.seats.length >= session.capacity;
      const mine = session.seats.some(seat => seat.host && seat.user?.id === myId());
      const count = Number(session.file_count) || 1;
      card.append(element('span', 'community-member-name', count > 1 ? `${session.file_name} 외 ${count - 1}개` : session.file_name),
        element('span', 'p-sub', `${who(session.host)} 공유 · ${session.seats.length}/${session.capacity}명`));
      const join = button(full ? '가득 참' : '참여', 'btn-secondary', () => void startGuest(session.session_id));
      join.disabled = full || mine;
      card.append(join);
      list.append(card);
    }
    main.replaceChildren(element('h3', 'ui-panel-title', '공동 편집'), intro, share, list, status);
  }

  /* ── 호스트: 묶음 확인 ── */
  function reportList(title, items, render) {
    if (!items.length) return null;
    const box = element('details', 'community-collab-report');
    box.append(element('summary', null, `${title} (${items.length})`));
    const list = element('ul', 'community-collab-report-list');
    for (const item of items) { const li = element('li'); li.append(...[render(item)].flat()); list.append(li); }
    box.append(list);
    return box;
  }
  function fileRow(file) {
    return `${file.path} · ${file.kind === 'binary' ? '읽기 전용' : '편집'} · ${fmtSize(file.size)}`;
  }
  function renderDraft() {
    const view = draft.view;
    const head = element('div', 'ui-toolbar community-collab-head');
    head.append(element('span', 'community-member-name', `공유할 묶음 확인 · ${view.entry}`));
    const root = element('p', 'p-sub', `공유 범위(루트): ${view.root}${view.canWiden ? '' : ' — 더 넓힐 수 없음'}`);
    const actions = element('div', 'ui-toolbar');
    const start = button('공유 시작', 'btn-primary', () => void startHost());
    const widen = button('범위 넓히기(상위 폴더)', 'btn-secondary', () => void draftAction(() => desktop.widenBundle({ bundle: view.bundle })));
    widen.disabled = !view.canWiden;
    actions.append(start, widen, button('파일 추가', 'btn-secondary', () => void draftAction(() => desktop.addToBundle({ bundle: view.bundle }))),
      button('취소', 'btn-secondary', () => void cancelDraft()));
    const files = element('ul', 'community-collab-files');
    for (const file of view.files) files.append(element('li', file.path === view.entry ? 'is-entry' : null, fileRow(file)));
    const parts = [head, root, actions, status, element('h4', 'community-collab-subtitle', `함께 공유할 파일 ${view.files.length}개`), files];
    const candidates = view.candidates.filter(item => !view.files.some(file => file.path === item.path));
    const extra = [
      reportList('추가 후보(페이지 링크·fetch)', candidates, item => [element('span', null, `${item.path}${item.from ? ` ← ${item.from}` : ''} `),
        button('추가', 'btn-secondary btn-small', () => void draftAction(() => desktop.addToBundle({ bundle: view.bundle, path: item.path })))]),
      reportList('제외한 참조', view.excluded, item => `${item.spec} ← ${item.from} · ${REASONS[item.reason] ?? item.reason}`),
      reportList('찾지 못한 참조(동적 경로·패키지)', view.unresolved, item => `${item.spec} ← ${item.from}:${item.line}`),
      reportList('외부 사이트(미리보기에서 불러옴)', view.external, host => host),
    ].filter(Boolean);
    if (view.excluded.some(item => item.reason === 'outside') && view.canWiden) parts.push(element('p', 'p-sub', '공유 범위 밖이라 빠진 참조가 있습니다. 필요하면 범위를 넓히세요.'));
    main.replaceChildren(...parts, ...extra);
  }
  async function openDraft() {
    if (current || draft) return;
    let view;
    try { view = await desktop.openBundle(); } catch (error) { status.textContent = fileError(error); return; }
    if (!view || view.cancelled || run !== generation) return;
    draft = { view };
    status.textContent = '공유할 파일을 확인한 뒤 ‘공유 시작’을 누르세요.';
    renderDraft();
  }
  async function draftAction(action) {
    if (!draft) return;
    let view;
    try { view = await action(); } catch (error) { status.textContent = fileError(error); return; }
    if (!draft || !view || view.cancelled) return;
    // 넓히기는 새 보고서를, 추가는 늘어난 파일만 돌려준다. 보고서는 남긴다.
    draft.view = view.excluded.length || view.unresolved.length || view.external.length || view.entry !== draft.view.entry ? view
      : { ...view, excluded: draft.view.excluded, unresolved: draft.view.unresolved, external: draft.view.external };
    status.textContent = '';
    renderDraft();
  }
  async function cancelDraft() {
    const mine = draft; draft = null;
    if (mine) await desktop.closeBundle({ bundle: mine.view.bundle }).catch(() => {});
    status.textContent = '';
    renderList();
  }

  /* ── 세션 화면 ── */
  function renderSession() {
    const mine = current;
    const session = sessionOf(mine.sid);
    const head = element('div', 'ui-toolbar community-collab-head');
    head.append(element('span', 'community-member-name', session?.file_name ?? mine.fileName));
    const people = element('span', 'community-collab-people');
    for (const seat of session?.seats ?? []) {
      const chip = element('span', 'community-collab-chip', `${who(seat.user)}${seat.host ? ' · 호스트' : ''}${seat.seat_id === mine.seat ? ' (나)' : ''}`);
      chip.style.borderColor = seatColor(seat.seat_id);
      people.append(chip);
    }
    head.append(people);
    const actions = element('div', 'ui-toolbar');
    const entry = mine.manifest.meta.get('entry');
    if (isHtml(entry) && mine.synced) {
      actions.append(button(mine.preview ? '미리보기 새로고침' : '미리보기', 'btn-secondary', () => void openPreview()));
      if (mine.preview) actions.append(button(mine.previewExternal ? '외부 연결 끄기' : '외부 연결 켜기', 'btn-secondary', () => void toggleExternal()));
    }
    if (mine.ended) actions.append(button('목록으로', 'btn-secondary', () => { closeSession(); renderList(); }));
    else if (mine.host) actions.append(button('저장', 'btn-primary', () => void save()), button('파일 추가', 'btn-secondary', () => void hostAdd()),
      button('새 파일', 'btn-secondary', () => showCreate()), confirmButton('공유 끝내기', () => leave()));
    else actions.append(button('나가기', 'btn-secondary', () => void leave()));
    const notices = [];
    if (mine.preview) notices.push(element('p', 'p-sub community-collab-preview-note', !mine.previewExternal ? '미리보기: 외부 연결 꺼짐(묶음 안의 파일만)'
      : mine.preview.hosts.length ? `미리보기가 외부 사이트에 연결함: ${mine.preview.hosts.join(', ')}` : '미리보기: 외부 사이트 연결 없음'));
    const pending = mine.host ? mine.scan.pending : (mine.manifest.meta.get('pending') ?? []);
    if (pending.length) {
      const box = element('div', 'community-collab-pending');
      box.append(element('strong', null, mine.host ? '편집 중 새로 생긴 참조 — 추가할까요?' : '호스트 승인 대기'));
      for (const path of pending) {
        const row = element('div', 'community-collab-pending-row');
        row.append(element('span', null, path));
        if (mine.host) row.append(button('추가', 'btn-secondary btn-small', () => void approve(path, true)), button('무시', 'btn-secondary btn-small', () => void approve(path, false)));
        box.append(row);
      }
      notices.push(box);
    }
    if (mine.host && mine.createForm && !mine.ended) notices.push(mine.createForm);
    if (mine.host) {
      const extra = [
        reportList('없는 파일을 참조함(새로 만들 수 있음)', (mine.scan.missing ?? []).filter(path => !mine.files.has(path)),
          path => [element('span', null, `${path} `), button('만들기', 'btn-secondary btn-small', () => void createFile(path))]),
        reportList('추가 후보(페이지 링크·fetch)', mine.scan.candidates.filter(item => !mine.files.has(item.path) && !mine.manifest.assets.has(item.path)),
          item => [element('span', null, `${item.path} `), button('추가', 'btn-secondary btn-small', () => void hostAdd(item.path))]),
        reportList('찾지 못한 참조(동적 경로·패키지)', mine.scan.unresolved, item => `${item.spec} ← ${item.from}:${item.line}`),
      ].filter(Boolean);
      notices.push(...extra);
    }
    main.replaceChildren(head, actions, status, ...notices, mine.workspace, mine.runPanel);
    renderTree();
    renderRun();
  }
  function renderTree() {
    const mine = current;
    const tree = mine.tree;
    const paths = [...mine.manifest.files.keys()].sort();
    const assets = [...mine.manifest.assets.keys()].sort();
    tree.replaceChildren();
    for (const path of [...paths, ...assets]) {
      const asset = mine.manifest.assets.has(path);
      const kind = asset ? (/^font\//.test(mine.manifest.assets.get(path)?.mime) ? ' (글꼴)' : ' (이미지)') : mine.created.has(path) ? ' (새 파일)' : '';
      const item = button(`${path}${kind}`, `community-collab-file${path === mine.active ? ' is-active' : ''}`, () => select(path));
      item.title = asset ? '읽기 전용(이미지·글꼴)' : path;
      if (!asset && !mine.files.get(path)?.synced) item.classList.add('is-loading');
      if (asset && !mine.assets.has(path)) item.classList.add('is-loading');
      tree.append(item);
    }
  }
  function select(path) {
    const mine = current;
    if (!mine) return;
    const previous = mine.active && mine.files.get(mine.active);
    if (previous && mine.active !== path) { collabLib.setPresence(previous.awareness, null); }
    mine.editor?.destroy(); mine.editor = null;
    mine.active = path;
    const file = mine.files.get(path);
    if (file) {
      collabLib.setPresence(file.awareness, mine.user);
      mine.editor = collabLib.createEditor({ parent: mine.mount, file, language: mine.manifest.files.get(path) || 'plain', readOnly: mine.ended || !file.synced });
      mine.mount.replaceChildren(mine.editor.view.dom);
    } else if (mine.manifest.assets.has(path)) {
      const info = mine.manifest.assets.get(path);
      const box = element('div', 'community-collab-asset');
      const bytes = mine.assets.get(path);
      if (!bytes) box.append(element('p', 'p-sub', '받는 중…'));
      else if (/^image\//.test(info.mime)) { const img = element('img'); img.alt = path; img.src = `data:${info.mime};base64,${toBase64(bytes)}`; box.append(img); }
      else box.append(element('p', 'p-sub', `글꼴 파일 · ${fmtSize(bytes.length)}`));
      box.append(element('p', 'p-sub', `${path} · 읽기 전용 · ${fmtSize(info.size)}`));
      mine.mount.replaceChildren(box);
    } else mine.mount.replaceChildren(element('p', 'p-sub', '파일을 고르세요.'));
    renderTree();
    renderRun();
  }

  /* ── 문서 연결 ── */
  function watchDoc(mine, path, doc) {
    doc.on('update', (update, origin) => {
      if (mine.ended) return;
      if (origin !== collabLib.REMOTE) {
        // 타자 몇 번을 한 메시지로 묶어 보낸다.
        const queue = mine.queue.get(path) ?? [];
        queue.push(update); mine.queue.set(path, queue);
        if (!mine.flush.has(path)) mine.flush.set(path, setTimeout(() => {
          mine.flush.delete(path);
          const merged = collabLib.mergeUpdates(mine.queue.get(path).splice(0));
          void send('update', merged, null, path);
        }, 50));
      }
      if (path) { schedulePreview(); scheduleScan(); }
    });
  }
  function addFile(mine, path, text) {
    if (mine.files.has(path)) return mine.files.get(path);
    const file = collabLib.createFile(mine.host ? text : null);
    file.synced = mine.host;
    mine.files.set(path, file);
    watchDoc(mine, path, file.doc);
    let cursorTimer = null;
    file.awareness.on('update', (_change, origin) => {
      if (origin === collabLib.REMOTE || mine.ended || cursorTimer) return;
      cursorTimer = setTimeout(() => { cursorTimer = null; void send('awareness', collabLib.encodeAwareness(file.awareness, [file.doc.clientID]), null, path); }, 100);
    });
    return file;
  }
  /* 손님: 목록이 바뀌면 새 파일 문서를 만들어 동기화를 요청하고, 없는 이미지·글꼴을 호스트에게 요청한다. */
  function followManifest() {
    const mine = current;
    if (!mine || mine.host) return;
    for (const path of mine.manifest.files.keys()) if (!mine.files.has(path)) { addFile(mine, path); requestSync(path); }
    requestAssets();
    if (!mine.active || (!mine.files.has(mine.active) && !mine.manifest.assets.has(mine.active))) select(mine.manifest.meta.get('entry') ?? [...mine.manifest.files.keys()][0]);
    else renderSession();
  }
  function requestAssets() {
    const mine = current;
    const host = hostSeat();
    if (!mine || mine.host || !host) return;
    const now = Date.now();
    for (const path of mine.manifest.assets.keys()) {
      const slot = mine.chunks.get(path);
      // 조각이 오고 있으면 기다리고, 8초 동안 아무것도 오지 않았을 때만 다시 요청한다(받은 조각은 남긴다).
      if (mine.assets.has(path) || (slot && now - slot.at < 8000)) continue;
      mine.chunks.set(path, { parts: slot?.parts ?? [], total: slot?.total ?? 0, at: now });
      void send('asset-request', new Uint8Array([1]), host, path);
    }
  }
  function sendAsset(path, to) {
    const bytes = current.assets.get(path);
    if (!bytes) return;
    const total = Math.max(1, Math.ceil(bytes.length / ASSET_PIECE));
    for (let index = 0; index < total; index++) {
      const piece = bytes.subarray(index * ASSET_PIECE, (index + 1) * ASSET_PIECE);
      const framed = new Uint8Array(8 + piece.length);
      const header = new DataView(framed.buffer);
      header.setUint32(0, index); header.setUint32(4, total);
      framed.set(piece, 8);
      void send('asset', framed, to, path);
    }
  }
  async function receiveAsset(path, bytes) {
    const mine = current;
    const info = mine.manifest.assets.get(path);
    if (!info || mine.assets.has(path) || bytes.length < 8) return;
    const header = new DataView(bytes.buffer, bytes.byteOffset, 8);
    const index = header.getUint32(0), total = header.getUint32(4);
    if (!total || index >= total || total * ASSET_PIECE > MAX_ASSET + ASSET_PIECE) return;
    const slot = mine.chunks.get(path) ?? { parts: [], total, at: 0 };
    if (slot.total !== total) { slot.parts = []; slot.total = total; }
    slot.parts[index] = bytes.slice(8);
    slot.at = Date.now();
    mine.chunks.set(path, slot);
    if (slot.parts.filter(Boolean).length !== total) return;
    const size = slot.parts.reduce((sum, part) => sum + part.length, 0);
    const whole = new Uint8Array(size);
    let offset = 0;
    for (const part of slot.parts) { whole.set(part, offset); offset += part.length; }
    mine.chunks.delete(path);
    if (size !== info.size || await sha256Hex(whole) !== info.sha256) { status.textContent = `${path}: 받은 파일이 원본과 달라 버렸습니다.`; return; }
    if (current !== mine) return;
    mine.assets.set(path, whole);
    if (mine.preview) void updatePreview({ assets: { [path]: whole } });
    if (mine.active === path) select(path); else renderTree();
  }

  async function begin(reply, { host, draftView = null }) {
    const lib = await loadCollabLib();
    if (run !== generation) return;
    const workspace = element('div', 'community-collab-workspace');
    const tree = element('nav', 'community-collab-tree'); tree.setAttribute('aria-label', '공유한 파일');
    const mount = element('div', 'community-collab-editor');
    workspace.append(tree, mount);
    const user = { name: me?.github_login ? `@${me.github_login}` : '나', color: seatColor(reply.seat_id) };
    current = { sid: reply.session_id, seat: reply.seat_id, host, bundle: draftView?.bundle ?? null, fileName: reply.session.file_name, user,
      hostSeat: reply.session.seats?.find(seat => seat.host)?.seat_id ?? null,
      manifest: lib.createManifest(), files: new Map(), assets: new Map(), chunks: new Map(), active: null, editor: null,
      workspace, tree, mount, synced: host, queue: new Map(), flush: new Map(), outbox: Promise.resolve(), ended: false, timer: null, retry: null,
      preview: null, previewExternal: true, previewTimer: null, scan: { pending: [], missing: [], unresolved: [], candidates: [] }, scanTimer: null,
      created: new Set(), createForm: null, runPanel: element('section', 'community-collab-run'), stdin: element('textarea', 'community-collab-stdin-input'),
      runtimes: null, running: false, output: null, confirmRun: null, lastRun: new Map() };
    const mine = current;
    mine.runPanel.hidden = true;
    mine.stdin.rows = 3; mine.stdin.placeholder = '프로그램에 줄 입력(한 줄에 하나)'; mine.stdin.setAttribute('aria-label', '실행 입력(stdin)');
    void loadRuntimes(mine);
    watchDoc(mine, '', mine.manifest.doc);
    if (host) {
      mine.manifest.doc.transact(() => {
        for (const file of draftView.files) {
          if (file.kind === 'text') { mine.manifest.files.set(file.path, file.language || 'plain'); addFile(mine, file.path, file.text); }
          else { mine.manifest.assets.set(file.path, { sha256: file.sha256, size: file.size, mime: file.mime }); mine.assets.set(file.path, file.bytes); }
        }
        mine.manifest.meta.set('entry', draftView.entry);
      });
      mine.scan.candidates = draftView.candidates;
      mine.scan.unresolved = draftView.unresolved;
    } else {
      mine.manifest.files.observe(() => { if (current === mine) followManifest(); });
      mine.manifest.assets.observe(() => { if (current === mine) followManifest(); });
      mine.manifest.meta.observe(() => { if (current === mine && !mine.ended) renderSession(); });
    }
    const every = Math.max(5, Number(reply.heartbeat_seconds) || 10) * 1000;
    mine.timer = setInterval(async () => {
      try { const value = await api('POST', repoPath(`/collab/${mine.sid}/heartbeat`), { seat_id: mine.seat }); if (current === mine) updateSessions(value.session); }
      catch (error) { if (current === mine && ['COLLAB_ENDED', 'MEMBER_REMOVED', 'AUTH_REQUIRED'].includes(error.code)) end('공동 편집이 끝났습니다.'); }
    }, every);
    updateSessions(reply.session);
    status.textContent = host ? '파일을 공유했습니다. 다른 멤버가 참여하면 함께 편집됩니다.' : '호스트와 동기화하는 중…';
    renderSession();
    if (host) select(draftView.files.some(file => file.path === draftView.entry && file.kind === 'text') ? draftView.entry : draftView.files[0].path);
    else {
      mine.mount.replaceChildren(element('p', 'p-sub', '호스트와 동기화하는 중…'));
      requestSync('');
      // 호스트 응답이 늦거나 빠진 파일이 있으면 다시 요청한다.
      mine.retry = setInterval(() => {
        if (mine.ended || current !== mine) { clearInterval(mine.retry); return; }
        if (!mine.synced) { requestSync(''); return; }
        for (const [path, file] of mine.files) if (!file.synced) requestSync(path);
        requestAssets();
      }, 4000);
    }
  }

  function updateSessions(session) {
    if (!session) return;
    sessions = [...sessions.filter(s => s.session_id !== session.session_id), session].sort((a, b) => a.created_at - b.created_at);
    if (current && !current.ended && current.sid === session.session_id) renderSession();
  }

  function onCollab(data) {
    const mine = current;
    if (!mine || mine.ended || data.session_id !== mine.sid || (data.to && data.to !== mine.seat) || typeof data.data !== 'string') return;
    const path = typeof data.path === 'string' ? data.path : '';
    let bytes; try { bytes = fromBase64(data.data); } catch { return; }
    const fromHost = data.from === hostSeat();
    try {
      if (!path) {
        // 목록 문서는 호스트만 쓴다: 호스트는 받은 변경을 무시하고, 손님은 호스트가 보낸 것만 받는다.
        if (data.kind === 'sync-request') { if (mine.host) void send('sync', collabLib.encodeStateAsUpdate(mine.manifest.doc, bytes), data.from); return; }
        if (mine.host || !fromHost || (data.kind !== 'update' && data.kind !== 'sync')) return;
        collabLib.applyUpdate(mine.manifest.doc, bytes);
        if (data.kind === 'sync' && !mine.synced) { mine.synced = true; status.textContent = '함께 편집하는 중입니다. 저장은 호스트가 합니다.'; followManifest(); }
        return;
      }
      if (data.kind === 'asset-request') { if (mine.host && mine.manifest.assets.has(path)) sendAsset(path, data.from); return; }
      if (data.kind === 'asset') { if (!mine.host && fromHost) void receiveAsset(path, bytes); return; }
      if (!mine.manifest.files.has(path)) return;
      const file = mine.files.get(path) ?? addFile(mine, path);
      if (data.kind === 'update') { collabLib.applyUpdate(file.doc, bytes); file.othersChanged = true; }
      else if (data.kind === 'awareness') collabLib.applyAwareness(file.awareness, bytes);
      else if (data.kind === 'sync-request') {
        void send('sync', collabLib.encodeStateAsUpdate(file.doc, bytes), data.from, path);
        if (mine.active === path) void send('awareness', collabLib.encodeAwareness(file.awareness, [file.doc.clientID]), data.from, path);
      } else if (data.kind === 'sync') {
        collabLib.applyUpdate(file.doc, bytes);
        file.othersChanged = true;
        if (!file.synced) {
          file.synced = true;
          if (mine.active === path) { mine.editor?.setReadOnly(false); void send('awareness', collabLib.encodeAwareness(file.awareness, [file.doc.clientID]), null, path); }
          renderTree();
        }
      }
    } catch { status.textContent = '받은 변경을 적용하지 못했습니다.'; }
  }

  function onSessions(data) {
    if (!Array.isArray(data.sessions)) return;
    sessions = data.sessions;
    if (current && !current.ended && (data.ended || []).includes(current.sid)) end(current.host ? '공유가 끝났습니다.' : '호스트가 공유를 끝냈습니다. 내용은 복사해 둘 수 있습니다.');
    else if (current && !current.ended) renderSession();
    else if (!current && !draft) renderList();
  }

  /* ── 미리보기 (각자 자기 앱에서, 지금 공유 문서 내용으로) ── */
  function allAssets(mine) { const out = {}; for (const [path, bytes] of mine.assets) out[path] = bytes; return out; }
  async function openPreview() {
    const mine = current;
    if (!mine) return;
    if (mine.preview) { await updatePreview({ files: textsOf(mine) }); return; }
    try {
      const opened = await desktop.openPreview({ entry: mine.manifest.meta.get('entry'), files: textsOf(mine), assets: allAssets(mine), external: mine.previewExternal });
      if (current !== mine) { void desktop.closePreview({ preview: opened.preview }).catch(() => {}); return; }
      mine.preview = { id: opened.preview, hosts: opened.hosts };
      status.textContent = '미리보기 창을 열었습니다. 편집하면 잠시 뒤 새로고침됩니다.';
    } catch (error) { status.textContent = fileError(error); }
    renderSession();
  }
  async function updatePreview(change) {
    const mine = current;
    if (!mine?.preview) return;
    try {
      const reply = await desktop.updatePreview({ preview: mine.preview.id, ...change });
      if (current === mine && mine.preview) { const changed = reply.hosts.join() !== mine.preview.hosts.join(); mine.preview.hosts = reply.hosts; if (changed) renderSession(); }
    } catch (error) {
      if (current !== mine) return;
      if (/NOT_OPEN/.test(error?.message || '')) { mine.preview = null; renderSession(); } else status.textContent = fileError(error);
    }
  }
  function schedulePreview() {
    const mine = current;
    if (!mine?.preview) return;
    clearTimeout(mine.previewTimer);
    mine.previewTimer = setTimeout(() => { if (current === mine) void updatePreview({ files: textsOf(mine) }); }, 500);
  }
  async function toggleExternal() {
    const mine = current;
    if (!mine?.preview) return;
    mine.previewExternal = !mine.previewExternal;
    await updatePreview({ external: mine.previewExternal });
    renderSession();
  }

  /* ── 호스트: 새 참조 확인·추가 ── */
  function scheduleScan() {
    const mine = current;
    if (!mine?.host || !mine.bundle) return;
    clearTimeout(mine.scanTimer);
    mine.scanTimer = setTimeout(async () => {
      if (current !== mine || mine.ended) return;
      try {
        const found = await desktop.scanBundle({ bundle: mine.bundle, files: textsOf(mine) });
        if (current !== mine) return;
        const changed = found.pending.join() !== mine.scan.pending.join() || (found.missing ?? []).join() !== (mine.scan.missing ?? []).join() || found.candidates.length !== mine.scan.candidates.length || found.unresolved.length !== mine.scan.unresolved.length;
        mine.scan = { ...found, missing: found.missing ?? [] };
        if (found.pending.join() !== (mine.manifest.meta.get('pending') ?? []).join()) mine.manifest.meta.set('pending', found.pending);
        if (changed) renderSession();
      } catch { /* 다음 편집 때 다시 확인한다 */ }
    }, 1000);
  }
  function include(view) {
    const mine = current;
    if (!mine || !view?.files) return 0;
    let added = 0;
    mine.manifest.doc.transact(() => {
      for (const file of view.files) {
        if (mine.manifest.files.has(file.path) || mine.manifest.assets.has(file.path)) continue;
        added++;
        if (file.kind === 'text') { mine.manifest.files.set(file.path, file.language || 'plain'); addFile(mine, file.path, file.text); }
        else { mine.manifest.assets.set(file.path, { sha256: file.sha256, size: file.size, mime: file.mime }); mine.assets.set(file.path, file.bytes); }
      }
    });
    if (mine.preview && added) void updatePreview({ files: textsOf(mine), assets: allAssets(mine) });
    return added;
  }
  async function approve(path, accept) {
    const mine = current;
    if (!mine?.host) return;
    try {
      const view = await desktop.approveInBundle({ bundle: mine.bundle, path, accept });
      if (current !== mine) return;
      const added = accept ? include(view) : 0;
      mine.scan.pending = mine.scan.pending.filter(item => item !== path);
      mine.manifest.meta.set('pending', mine.scan.pending);
      status.textContent = accept ? `${path}${added > 1 ? ` 외 ${added - 1}개` : ''} 파일을 추가했습니다.` : `${path}는 이번 공유에서 다시 묻지 않습니다.`;
    } catch (error) { status.textContent = fileError(error); }
    renderSession();
  }
  async function hostAdd(path) {
    const mine = current;
    if (!mine?.host) return;
    try {
      const view = await desktop.addToBundle(path ? { bundle: mine.bundle, path } : { bundle: mine.bundle });
      if (current !== mine || !view || view.cancelled) return;
      const added = include(view);
      status.textContent = added ? `파일 ${added}개를 추가했습니다.` : '이미 공유 중인 파일입니다.';
    } catch (error) { status.textContent = fileError(error); }
    renderSession();
  }

  /* ── 새 파일 (호스트): 디스크에는 저장할 때 만든다 ── */
  function showCreate(path = '') {
    const mine = current;
    if (!mine?.host) return;
    const form = element('form', 'community-collab-create');
    const input = element('input', 'community-collab-create-input');
    input.type = 'text'; input.value = path; input.placeholder = '예: js/util.js, src/helper.py'; input.setAttribute('aria-label', '새 파일 경로(공유 범위 기준)');
    form.append(element('label', 'p-sub', '새 파일 경로(공유 범위 기준)'), input, button('만들기', 'btn-primary btn-small', () => {}), button('취소', 'btn-secondary btn-small', () => { mine.createForm = null; renderSession(); }));
    form.querySelector('.btn-primary').type = 'submit';
    form.addEventListener('submit', event => { event.preventDefault(); void createFile(input.value); });
    mine.createForm = form;
    renderSession();
    input.focus();
  }
  async function createFile(path) {
    const mine = current;
    if (!mine?.host || !path.trim()) return;
    try {
      const view = await desktop.createInBundle({ bundle: mine.bundle, path: path.trim() });
      if (current !== mine) return;
      const before = new Set(mine.files.keys());
      include(view);
      const made = [...mine.files.keys()].find(name => !before.has(name));
      if (made) { mine.created.add(made); mine.scan.missing = (mine.scan.missing ?? []).filter(item => item !== made); }
      mine.createForm = null;
      status.textContent = made ? `${made} 파일을 만들었습니다. 저장하면 디스크에 생깁니다.` : '이미 공유 중인 파일입니다.';
      renderSession();
      if (made) select(made);
    } catch (error) { status.textContent = fileError(error); }
  }

  /* ── 실행 (AD-34 2·3단계): 누구나 자기 앱에서 지금 내용으로 실행하고, 결과는 공유하지 않는다 ── */
  const runLanguage = path => /\.py$/i.test(path || '') ? 'python' : /\.c$/i.test(path || '') ? 'c' : /\.(?:cpp|cc|cxx)$/i.test(path || '') ? 'cpp' : /\.java$/i.test(path || '') ? 'java' : null;
  function runTarget(mine) {
    if (runLanguage(mine.active) && mine.files.has(mine.active)) return mine.active;
    const entry = mine.manifest.meta.get('entry');
    return runLanguage(entry) && mine.files.has(entry) ? entry : null;
  }
  async function loadRuntimes(mine) {
    if (mine.runtimes || !desktop.runtimes) return;
    try { mine.runtimes = await desktop.runtimes(); } catch { mine.runtimes = { python: null, c: null, cpp: null, java: null }; }
    if (current === mine && !mine.ended) renderRun();
  }
  function renderRun() {
    const mine = current;
    if (!mine) return;
    const panel = mine.runPanel;
    const target = runTarget(mine);
    if (!target || !desktop.run) { panel.hidden = true; panel.replaceChildren(); return; }
    panel.hidden = false;
    const language = runLanguage(target);
    const tool = mine.runtimes?.[language];
    const head = element('div', 'ui-toolbar community-collab-run-head');
    const start = button(mine.running ? '실행 중…' : '실행', 'btn-primary', () => void startRun());
    start.disabled = !!mine.running || !mine.synced || !!(mine.runtimes && !tool);
    head.append(element('strong', null, `실행 · ${target}`), start);
    if (mine.running) head.append(button('중지', 'btn-secondary', () => void desktop.stopRun().catch(() => {})));
    const note = element('p', 'p-sub', !mine.runtimes ? '실행 환경을 확인하는 중…' : tool ? `${tool} · 결과는 나에게만 보입니다.`
      : language === 'java' ? '이 PC에 JDK(javac·java)가 없어 실행할 수 없습니다.' : `이 PC에 ${language === 'c' ? 'gcc 또는 clang' : 'g++ 또는 clang++'}이 없어 실행할 수 없습니다.`);
    const input = element('details', 'community-collab-stdin');
    input.open = !!mine.stdin.value;
    input.append(element('summary', null, '입력(stdin)'), mine.stdin);
    panel.replaceChildren(...[head, note, input, mine.confirmRun, mine.output].filter(Boolean));
  }
  /* 같은 줄 수만큼 짝지어 빼고 남은 줄: 바뀐 내용을 훑어보기에 충분한 간단한 비교 */
  function lineDiff(before, after) {
    const count = lines => { const map = new Map(); for (const line of lines) map.set(line, (map.get(line) ?? 0) + 1); return map; };
    const old = (before ?? '').split('\n'), now = after.split('\n');
    const oldLeft = count(old), nowLeft = count(now);
    const added = now.filter(line => { const n = oldLeft.get(line) ?? 0; if (n) { oldLeft.set(line, n - 1); return false; } return true; });
    const removed = before === undefined ? [] : old.filter(line => { const n = nowLeft.get(line) ?? 0; if (n) { nowLeft.set(line, n - 1); return false; } return true; });
    return { added, removed };
  }
  function changedByOthers(mine) {
    const out = [];
    for (const [path, file] of mine.files) {
      if (!file.othersChanged) continue;
      const before = mine.lastRun.get(path), after = file.ytext.toString();
      if (before === after) continue;
      out.push({ path, before, ...lineDiff(before, after) });
    }
    return out;
  }
  function showRunConfirm(mine, changed) {
    const box = element('div', 'community-collab-confirm');
    box.append(element('strong', null, '다른 사람이 쓰거나 바꾼 코드를 이 PC에서 실행합니다'),
      element('p', 'p-sub', 'C/C++·Java는 이 PC의 컴파일러로 임시 폴더에서 실행됩니다. 메모리·프로세스 수·시간은 제한하지만 네트워크와 내 파일에는 접근할 수 있습니다. 바뀐 줄을 확인하세요.'));
    for (const item of changed.slice(0, 10)) {
      const details = element('details', 'community-collab-diff');
      details.open = changed.length <= 3;
      details.append(element('summary', null, `${item.path}${item.before === undefined ? ' · 처음 실행(전체)' : ''} · +${item.added.length} −${item.removed.length}`));
      const shown = [...item.removed.slice(0, 100).map(line => `- ${line}`), ...item.added.slice(0, 200).map(line => `+ ${line}`)];
      details.append(element('pre', null, shown.join('\n') + (item.added.length > 200 || item.removed.length > 100 ? '\n…' : '')));
      box.append(details);
    }
    if (changed.length > 10) box.append(element('p', 'p-sub', `외 ${changed.length - 10}개 파일`));
    const actions = element('div', 'ui-toolbar');
    actions.append(button('확인하고 실행', 'btn-primary', () => void startRun(true)), button('취소', 'btn-secondary', () => { mine.confirmRun = null; renderRun(); }));
    box.append(actions);
    mine.confirmRun = box;
    renderRun();
  }
  const EXIT_NAMES = { 3221225477: '메모리 접근 오류', 3221225725: '스택 넘침', 3221225495: '메모리 부족', 3221226505: '비정상 종료' };
  function runSummary(result) {
    const seconds = (result.durationMs / 1000).toFixed(1);
    if (result.timedOut) return `시간 제한으로 멈췄습니다(${seconds}초).`;
    if (result.phase === 'compile') return `컴파일하지 못했습니다 · 종료 코드 ${result.exitCode ?? '—'}`;
    const name = EXIT_NAMES[result.exitCode];
    return `끝났습니다 · 종료 코드 ${result.exitCode ?? '—'}${name ? ` (${name})` : ''} · ${seconds}초${result.truncated ? ' · 출력이 길어 일부만 표시' : ''}`;
  }
  function renderOutput(result) {
    const box = element('div', 'community-collab-output');
    box.append(element('p', 'p-sub community-collab-run-summary', runSummary(result)));
    if (result.stdout) box.append(element('pre', 'community-collab-stdout', result.stdout));
    if (result.stderr) box.append(element('pre', 'community-collab-stderr', result.stderr));
    if (!result.stdout && !result.stderr) box.append(element('p', 'p-sub', '출력이 없습니다.'));
    return box;
  }
  async function startRun(confirmed = false) {
    const mine = current;
    const target = mine && runTarget(mine);
    if (!mine || !target || mine.running) return;
    // Python은 앱 안 격리 환경이라 묻지 않는다. 내 PC 컴파일러로 도는 언어만, 남이 바꾼 내용이 있으면 먼저 보여 준다.
    if (runLanguage(target) !== 'python' && !confirmed) {
      const changed = changedByOthers(mine);
      if (changed.length) { showRunConfirm(mine, changed); return; }
    }
    const files = textsOf(mine);
    // 확인 기준은 내 PC에서 직접 실행한 내용이다. 격리된 Python 실행은 확인한 것으로 치지 않는다.
    if (runLanguage(target) !== 'python') {
      for (const [path, text] of Object.entries(files)) mine.lastRun.set(path, text);
      for (const file of mine.files.values()) file.othersChanged = false;
    }
    mine.confirmRun = null; mine.running = true; mine.output = element('p', 'p-sub', '실행하는 중…');
    renderRun();
    try {
      const result = await desktop.run({ entry: target, files, stdin: mine.stdin.value });
      if (current === mine) mine.output = renderOutput(result);
    } catch (error) {
      if (current === mine) mine.output = element('p', 'p-sub', fileError(error));
    } finally {
      if (current === mine) { mine.running = false; renderRun(); }
    }
  }

  /* ── 끝내기·저장 ── */
  function end(text) {
    const mine = current;
    if (!mine || mine.ended) return;
    mine.ended = true;
    clearInterval(mine.timer); clearInterval(mine.retry); clearTimeout(mine.previewTimer); clearTimeout(mine.scanTimer);
    for (const timer of mine.flush.values()) clearTimeout(timer);
    mine.editor?.setReadOnly(true);
    status.textContent = text;
    renderSession();
  }
  function closeSession() {
    if (!current) return;
    const mine = current; current = null;
    clearInterval(mine.timer); clearInterval(mine.retry); clearTimeout(mine.previewTimer); clearTimeout(mine.scanTimer);
    for (const timer of mine.flush.values()) clearTimeout(timer);
    if (mine.running) void desktop.stopRun().catch(() => {});
    mine.editor?.destroy();
    for (const file of mine.files.values()) collabLib.destroyFile(file);
    mine.manifest.doc.destroy();
    if (mine.preview) void desktop.closePreview({ preview: mine.preview.id }).catch(() => {});
    if (mine.bundle) void desktop.closeBundle({ bundle: mine.bundle }).catch(() => {});
  }
  async function leave() {
    const mine = current;
    if (!mine) return;
    closeSession();
    if (!mine.ended) await api('POST', repoPath(`/collab/${mine.sid}/leave`), { seat_id: mine.seat }).catch(() => {});
    status.textContent = mine.host ? '공유를 끝냈습니다.' : '공동 편집에서 나왔습니다.';
    renderList();
  }
  async function save() {
    const mine = current;
    if (!mine?.host) return;
    status.textContent = '저장하는 중…';
    try {
      const { results } = await desktop.saveBundle({ bundle: mine.bundle, files: [...mine.files].map(([path, file]) => ({ path, text: file.ytext.toString() })) });
      const failed = results.filter(item => !item.saved);
      for (const item of results) if (item.saved) mine.created.delete(item.path);
      if (current === mine && !mine.ended) renderTree();
      const time = new Date().toLocaleTimeString();
      if (!failed.length) status.textContent = `저장했습니다 (${results.length}개 파일, ${time}).`;
      else {
        const changed = failed.filter(item => item.error === 'FILE_CHANGED').map(item => item.path);
        const other = failed.filter(item => item.error !== 'FILE_CHANGED').map(item => item.path);
        status.textContent = [changed.length ? `다른 곳에서 바뀌어 저장하지 않은 파일: ${changed.join(', ')}` : '', other.length ? `저장하지 못한 파일: ${other.join(', ')}` : '']
          .filter(Boolean).join(' · ') + (results.length > failed.length ? ` (나머지 ${results.length - failed.length}개는 저장함)` : '');
      }
    } catch (error) { status.textContent = fileError(error); }
  }

  async function startHost() {
    if (current || !draft) return;
    const view = draft.view;
    const entry = view.files.find(file => file.path === view.entry);
    let reply;
    try { reply = await api('POST', repoPath('/collab'), { file_name: baseName(view.entry), language: entry?.language || 'plain', file_count: view.files.length }); }
    catch (error) { status.textContent = error.message; return; }
    draft = null;
    await begin(reply, { host: true, draftView: view });
  }
  async function startGuest(sid) {
    if (current || draft) return;
    let reply;
    try { reply = await api('POST', repoPath(`/collab/${sid}/join`)); }
    catch (error) { status.textContent = error.message; return; }
    if (reply.session?.file_count == null) {
      // 파일 하나만 주고받던 이전 버전 앱의 공유는 문서 구조가 달라 함께 편집할 수 없다.
      void api('POST', repoPath(`/collab/${sid}/leave`), { seat_id: reply.seat_id }).catch(() => {});
      status.textContent = '이전 버전 앱에서 시작한 공동 편집이라 참여할 수 없습니다. 공유한 사람에게 앱 업데이트를 요청하세요.';
      return;
    }
    await begin(reply, { host: false });
  }
  async function refresh() {
    try { const value = await api('GET', repoPath('/collab')); if (run === generation) onSessions({ sessions: value.sessions, ended: [] }); }
    catch (error) { status.textContent = error.message; }
  }

  let ready = false;
  function onEvent({ type, data }) {
    if (run !== generation) return;
    if (type === 'ready') {
      void refresh();
      if (ready && current?.synced && !current.host) { requestSync(''); for (const path of current.files.keys()) requestSync(path); }
      ready = true;
    }
    else if (type === 'collab-sessions') onSessions(data);
    else if (type === 'collab') onCollab(data);
    else if (type === 'status' && data.state === 'stopped') {
      status.textContent = CHAT_STOPPED[data.code] || '연결이 끊겼습니다.';
      if (CHAT_FINAL.includes(data.code)) end(status.textContent);
    } else if (type === 'status' && data.state === 'reconnecting') status.textContent = '연결이 끊겨 다시 연결하는 중…';
  }

  closeCollab = () => {
    const mine = current; closeSession();
    if (draft) { void desktop.closeBundle({ bundle: draft.view.bundle }).catch(() => {}); draft = null; }
    if (mine && !mine.ended) void api('POST', repoPath(`/collab/${mine.sid}/leave`), { seat_id: mine.seat }).catch(() => {});
  };
  renderList();
  live.listen(onEvent);
}

/* ── 음성 채팅 (AD-28) ──
   커뮤니티마다 한 번에 3명까지. 소리는 참여자끼리 WebRTC로 직접 주고받고, 서버는 자리와 연결 신호(offer/answer/ICE)만 전달한다.
   새로 들어온 사람이 이미 있는 사람들에게 연결을 건다. 신호는 채팅 스트림으로 나에게 온 것만 받는다. */
let closeVoice = null;
function stopVoice() { const close = closeVoice; closeVoice = null; close?.(); }

function createVoice({ panel, path, onState }) {
  const title = element('h3', 'ui-panel-title', '음성 채팅');
  const list = element('ul', 'community-chat-people community-voice-people');
  const status = element('p', 'p-sub community-voice-status'); status.setAttribute('role', 'status');
  const actions = element('div', 'ui-toolbar');
  const joinButton = button('음성 참여', 'btn-primary', () => void join());
  const muteButton = button('음소거', 'btn-secondary', () => setMuted(!muted)); muteButton.hidden = true;
  const leaveButton = button('나가기', 'btn-secondary', () => void leave()); leaveButton.hidden = true;
  actions.append(joinButton, muteButton, leaveButton);
  const audios = element('div'); audios.hidden = true;
  panel.append(title, list, actions, status, audios);

  let capacity = 3; let participants = []; let session = null; let local = null; let ice = []; let timer = null; let muted = false; let busy = false;
  const peers = new Map(); // 상대 자리 ID → { pc, audio, pending: [ICE 후보] }
  const chains = new Map(); // 상대 자리 ID → 그 상대 신호 처리 순서
  let outbox = Promise.resolve();

  function render() {
    title.textContent = `음성 채팅 ${participants.length}/${capacity}`;
    list.replaceChildren(...participants.map(p => {
      const item = element('li', 'community-member-name', `${who(p.user)}${p.session_id === session ? ' (나)' : ''}`);
      const peer = peers.get(p.session_id);
      if (peer && ['failed', 'disconnected'].includes(peer.pc.connectionState)) item.append(element('span', 'p-sub', ' · 연결 안 됨'));
      return item;
    }));
    if (!participants.length) list.append(element('li', 'p-sub', '아무도 없습니다.'));
    joinButton.hidden = !!session; muteButton.hidden = leaveButton.hidden = !session;
    joinButton.disabled = busy || participants.length >= capacity;
    joinButton.textContent = participants.length >= capacity ? `가득 참 (${capacity}명)` : '음성 참여';
    muteButton.textContent = muted ? '음소거 해제' : '음소거';
    muteButton.setAttribute('aria-pressed', String(muted));
    onState?.({ joined: !!session });
  }

  // 신호는 하나씩 보낸다. 앱은 동시에 보낼 수 있는 요청 수가 정해져 있다.
  function send(to, kind, extra) {
    const mine = session;
    outbox = outbox.then(() => mine === session ? api('POST', path('/voice/signal'), { session_id: mine, to, kind, ...extra }) : null).catch(() => {});
    return outbox;
  }

  function peer(id) {
    let entry = peers.get(id);
    if (entry) return entry;
    const pc = new RTCPeerConnection({ iceServers: ice });
    const audio = element('audio'); audio.autoplay = true; audios.append(audio);
    entry = { pc, audio, pending: [] };
    peers.set(id, entry);
    for (const track of local.getAudioTracks()) pc.addTrack(track, local);
    pc.addEventListener('track', event => { audio.srcObject = event.streams[0] ?? new MediaStream([event.track]); });
    pc.addEventListener('icecandidate', event => {
      if (event.candidate) void send(id, 'ice', { candidate: event.candidate.candidate, sdp_mid: event.candidate.sdpMid, sdp_mline_index: event.candidate.sdpMLineIndex });
    });
    pc.addEventListener('connectionstatechange', render);
    return entry;
  }
  function hangUp(id) {
    const entry = peers.get(id); if (!entry) return;
    entry.pc.close(); entry.audio.srcObject = null; entry.audio.remove(); peers.delete(id); chains.delete(id);
  }
  async function flush(entry) {
    for (const candidate of entry.pending.splice(0)) await entry.pc.addIceCandidate(candidate).catch(() => {});
  }
  async function call(id) {
    const entry = peer(id);
    await entry.pc.setLocalDescription(await entry.pc.createOffer());
    await send(id, 'offer', { sdp: entry.pc.localDescription.sdp });
  }

  async function handle(data) {
    if (!session || data.to !== session || typeof data.from !== 'string' || data.from === session) return;
    if (!participants.some(p => p.session_id === data.from) && data.kind !== 'offer') return;
    if (data.kind === 'offer' && typeof data.sdp === 'string') {
      const entry = peer(data.from);
      await entry.pc.setRemoteDescription({ type: 'offer', sdp: data.sdp });
      await flush(entry);
      await entry.pc.setLocalDescription(await entry.pc.createAnswer());
      await send(data.from, 'answer', { sdp: entry.pc.localDescription.sdp });
    } else if (data.kind === 'answer' && typeof data.sdp === 'string') {
      const entry = peers.get(data.from);
      if (entry && entry.pc.signalingState === 'have-local-offer') { await entry.pc.setRemoteDescription({ type: 'answer', sdp: data.sdp }); await flush(entry); }
    } else if (data.kind === 'ice' && typeof data.candidate === 'string') {
      const entry = peers.get(data.from) ?? peer(data.from);
      const candidate = { candidate: data.candidate, sdpMid: data.sdp_mid ?? null, sdpMLineIndex: data.sdp_mline_index ?? null };
      if (entry.pc.remoteDescription) await entry.pc.addIceCandidate(candidate).catch(() => {}); else entry.pending.push(candidate);
    }
  }
  function signal(data) {
    if (!data || typeof data !== 'object' || !session) return;
    // 같은 상대의 신호는 온 순서대로 처리한다.
    const run = () => handle(data).catch(() => { status.textContent = '상대와 연결하지 못했습니다.'; });
    chains.set(data.from, (chains.get(data.from) ?? Promise.resolve()).then(run));
  }

  function update(value) {
    if (!value || !Array.isArray(value.participants)) return;
    capacity = Number.isSafeInteger(value.capacity) ? value.capacity : capacity;
    participants = value.participants.filter(p => p && typeof p.session_id === 'string');
    // 나간 사람과의 연결은 닫는다. 내 자리는 하트비트가 확인한다(먼저 보낸 알림이 늦게 와도 나를 내보내지 않게).
    for (const id of [...peers.keys()]) if (!participants.some(p => p.session_id === id)) hangUp(id);
    render();
  }

  function cleanup(text) {
    clearInterval(timer); timer = null;
    for (const id of [...peers.keys()]) hangUp(id);
    local?.getTracks().forEach(track => track.stop()); local = null;
    if (session) participants = participants.filter(p => p.session_id !== session);
    session = null; muted = false; busy = false;
    if (text !== undefined) status.textContent = text;
    render();
  }

  async function join() {
    if (session || busy) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof RTCPeerConnection !== 'function') { status.textContent = '이 환경에서는 음성 채팅을 쓸 수 없습니다.'; return; }
    busy = true; render(); status.textContent = '마이크를 준비하는 중…';
    try { local = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false }); }
    catch { busy = false; status.textContent = '마이크를 쓸 수 없습니다. 마이크 연결과 권한을 확인하세요.'; render(); return; }
    let joined;
    try { joined = await api('POST', path('/voice/join')); }
    catch (error) { cleanup(error.message); return; }
    if (!local) return; // 기다리는 사이 화면을 떠났다
    session = joined.session_id; ice = Array.isArray(joined.ice_servers) ? joined.ice_servers : []; busy = false;
    status.textContent = '음성 채팅에 참여했습니다.';
    update(joined);
    const every = Math.max(5, Number(joined.heartbeat_seconds) || 10) * 1000;
    timer = setInterval(async () => {
      const mine = session;
      try { const value = await api('POST', path('/voice/heartbeat'), { session_id: mine }); if (mine === session) update(value); }
      catch (error) { if (mine === session && ['VOICE_SESSION_ENDED', 'MEMBER_REMOVED', 'AUTH_REQUIRED'].includes(error.code)) cleanup('음성 채팅 연결이 끝났습니다.'); }
    }, every);
    for (const p of participants) if (p.session_id !== session) await call(p.session_id).catch(() => { status.textContent = '상대와 연결하지 못했습니다.'; });
  }

  async function leave(text = '음성 채팅에서 나왔습니다.') {
    const mine = session;
    cleanup(text);
    if (mine) await api('POST', path('/voice/leave'), { session_id: mine }).catch(() => {});
  }

  function setMuted(value) {
    muted = value;
    local?.getAudioTracks().forEach(track => { track.enabled = !muted; });
    render();
  }

  async function refresh() {
    try { update(await api('GET', path('/voice'))); } catch { /* 채팅 연결 상태가 따로 알려 준다 */ }
  }

  render();
  closeVoice = () => { void leave(''); };
  return { update, signal, refresh, leave, get joined() { return !!session; } };
}

async function redeem(raw) {
  const token = TOKEN.exec(String(raw || '').trim())?.[1];
  if (!token) { joinMessage.textContent = '초대 링크 형식이 아닙니다. 받은 링크 전체를 붙여 넣으세요.'; return; }
  joinMessage.textContent = '참여하는 중…';
  try {
    const { repository_id: id } = await api('POST', '/api/community/v1/invites/redeem', { token });
    try { sessionStorage.removeItem(PENDING_INVITE); } catch { /* 저장소를 못 써도 참여는 끝났다 */ }
    joinInput.value = ''; joinMessage.textContent = '참여했습니다.';
    await loadList(); go({ id, tab: null, post: null });
  } catch (error) {
    if (error.code === 'AUTH_REQUIRED') {
      // 로그인 후 같은 페이지로 돌아오면 이어서 참여한다.
      try { sessionStorage.setItem(PENDING_INVITE, token); } catch { /* 링크를 다시 열면 된다 */ }
      joinMessage.textContent = '로그인한 뒤 이어서 참여합니다.';
      showError(mainEl, error);
    } else joinMessage.textContent = error.message;
  }
}

/* 링크로 받은 초대는 바로 참여시키지 않고 칸에 채워 둔다. 링크만으로 누군가를 몰래 참여시키지 못하게 참여 버튼은 사용자가 누른다. */
function offerInvite(token) {
  joinInput.value = token;
  joinMessage.textContent = '초대 링크를 받았습니다. 참여를 누르면 커뮤니티에 들어갑니다.';
  const appLink = document.getElementById('community-open-app');
  // 브라우저에서 연 경우: 설치된 GitViz 앱으로 넘겨 앱에서 참여할 수 있게 한다(gitviz-join은 초대 토큰만 싣는다).
  if (appLink && !isDesktop()) { appLink.href = `gitviz-join://invite/${token}`; appLink.hidden = false; }
  joinForm.querySelector('button[type="submit"]')?.focus();
}

async function init() {
  joinForm.addEventListener('submit', event => { event.preventDefault(); void redeem(joinInput.value); });
  window.addEventListener('popstate', () => { void route(); });
  window.addEventListener('pagehide', () => { stopCollab(); stopVoice(); stopLive(); });
  // 웹은 주소의 # 뒤(서버 로그에 남지 않음), 앱은 gitviz-join 링크를 main이 ?invite=로 넘긴다. 읽은 즉시 주소에서 지운다.
  const query = params().get('invite');
  const offered = TOKEN.exec(location.hash.startsWith('#invite=') ? location.hash : query || '')?.[1] ?? null;
  if (offered || query) {
    const url = new URL(location.href); url.hash = ''; url.searchParams.delete('invite');
    history.replaceState(null, '', url);
  }
  // 로그인 전에 참여를 눌렀다면 로그인 후 이어서 참여한다.
  let resumed = null;
  if (!offered) { try { resumed = sessionStorage.getItem(PENDING_INVITE); } catch { resumed = null; } }
  await loadMe();
  await loadList();
  if (offered) { offerInvite(offered); await route(); }
  else if (resumed) await redeem(resumed);
  else await route();
}

if (mainEl) void init();
