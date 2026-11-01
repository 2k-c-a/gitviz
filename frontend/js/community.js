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
  stopLive(); stopVoice(); stopCollab();
  const id = Number(params().get('id'));
  if (!id) {
    view = null;
    mainEl.replaceChildren(createState({ kind: 'empty', title: '커뮤니티를 고르세요', message: '왼쪽 목록에서 저장소 커뮤니티를 고르거나 받은 초대 링크로 참여하세요.' }));
    return;
  }
  mainEl.replaceChildren(createState({ kind: 'loading', title: '커뮤니티를 여는 중' }));
  try {
    const value = await api('GET', `/api/community/v1/repositories/${id}`);
    if (run !== generation) return;
    view = value;
    renderCommunity();
  } catch (error) {
    if (run !== generation) return;
    if (error.code === 'INVITE_REQUIRED') {
      mainEl.replaceChildren(createState({ kind: 'empty', title: '초대가 필요합니다', message: error.message }));
    } else showError(mainEl, error, route);
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
  mainEl.replaceChildren(head, body);
  stopLive(); stopVoice(); stopCollab();
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
        catch { field.select(); status.textContent = '선택된 링크를 직접 복사하세요.'; }
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
  const repositoryId = view.repository.id;
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
  const voicePanel = element('section', 'panel community-voice');
  const onlinePanel = element('section', 'panel community-chat-online');
  const onlineTitle = element('h3', 'ui-panel-title', '접속 중');
  const online = element('ul', 'community-chat-people');
  onlinePanel.append(onlineTitle, online);
  side.append(voicePanel, onlinePanel);
  const layout = element('div', 'community-chat-layout');
  layout.append(panel, side);
  target.replaceChildren(layout);
  const voice = createVoice({ panel: voicePanel, path: repoPath });

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
      void voice.refresh();
    } else if (type === 'message') add(data);
    else if (type === 'deleted') drop(data.id);
    else if (type === 'presence') people(data.online);
    else if (type === 'voice') voice.update(data);
    else if (type === 'voice-signal') voice.signal(data);
    else if (type === 'status') {
      if (data.state === 'connecting' && !ready) status.textContent = '연결하는 중…';
      else if (data.state === 'reconnecting') status.textContent = '연결이 끊겨 다시 연결하는 중…';
      else if (data.state === 'stopped') {
        status.textContent = CHAT_STOPPED[data.code] || '채팅 연결이 끊겼습니다.';
        people([]);
        const final = CHAT_FINAL.includes(data.code);
        reconnect.hidden = final;
        if (final) { setInput(false); if (voice.joined) void voice.leave(status.textContent); }
        closeLive = null;
      }
    }
  }
  function connect() { status.textContent = '연결하는 중…'; reconnect.hidden = true; openLive(repositoryId, onEvent); }

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
  connect();
}

/* ── 실시간 공동 편집 (AD-29, 앱 전용) ──
   호스트가 내 PC의 파일 하나를 공유하면 3명까지 함께 고친다. 편집기는 CodeMirror 6, 동시 편집은 Yjs(CRDT).
   파일은 호스트 PC에만 있고 저장도 호스트 앱만 한다. 서버는 자리와 Yjs 변경분만 전달하고 내용을 저장하지 않는다.
   새로 들어온 사람은 호스트에게 상태를 요청해 받고(sync), 이후 변경분(update)과 커서(awareness)를 주고받는다. */
let closeCollab = null;
function stopCollab() { const close = closeCollab; closeCollab = null; close?.(); }
const COLLAB_COLORS = ['#2563eb', '#db2777', '#059669', '#d97706', '#7c3aed', '#0891b2'];
const collabAvailable = () => isDesktop() && !!globalThis.desktop?.collab;
let collabLib = null;
async function loadCollabLib() {
  if (!collabLib) { await import(new URL('./collab-editor.bundle.js', import.meta.url).href); collabLib = globalThis.GitVizCollab; }
  if (!collabLib?.createCollabEditor) throw new Error('편집기를 불러오지 못했습니다.');
  return collabLib;
}
function toBase64(bytes) {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}
function fromBase64(text) { const binary = atob(text); const bytes = new Uint8Array(binary.length); for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i); return bytes; }
function seatColor(seat) { let h = 0; for (const c of seat) h = (h * 31 + c.charCodeAt(0)) >>> 0; return COLLAB_COLORS[h % COLLAB_COLORS.length]; }
const FILE_ERRORS = {
  FILE_TOO_LARGE: '100KB보다 큰 파일은 공유할 수 없습니다.', NOT_TEXT: 'UTF-8 텍스트 파일만 공유할 수 있습니다.', INVALID_FILE: '일반 파일만 공유할 수 있습니다.',
  FILE_CHANGED: '파일이 공유한 뒤 다른 곳에서 바뀌어 저장하지 않았습니다.', SAVE_FAILED: '파일을 저장하지 못했습니다.', NOT_OPEN: '공유한 파일이 닫혔습니다.',
};
const fileError = error => FILE_ERRORS[(error?.message || '').replace(/^.*Error: /, '')] || error?.message || '파일을 다루지 못했습니다.';

async function renderCollab(target) {
  const run = generation;
  const repositoryId = view.repository.id;
  const layout = element('div', 'community-chat-layout');
  const main = element('section', 'panel community-collab');
  const side = element('aside', 'community-chat-side');
  const voicePanel = element('section', 'panel community-voice');
  side.append(voicePanel);
  layout.append(main, side);
  target.replaceChildren(layout);
  const voice = createVoice({ panel: voicePanel, path: repoPath });

  const status = element('p', 'p-sub community-collab-status'); status.setAttribute('role', 'status');
  let sessions = [];
  let current = null; // { sid, seat, host, handle, editor, synced, timer, flush, pending, outbox, ended }

  function send(kind, data, to) {
    const mine = current;
    if (!mine) return Promise.resolve();
    mine.outbox = mine.outbox.then(() => current === mine && !mine.ended
      ? api('POST', repoPath(`/collab/${mine.sid}/relay`), { seat_id: mine.seat, kind, data: toBase64(data), ...(to ? { to } : {}) }) : null)
      .catch(error => { if (current === mine && error.code === 'COLLAB_ENDED') end('공동 편집이 끝났습니다.'); });
    return mine.outbox;
  }
  function sessionOf(sid) { return sessions.find(s => s.session_id === sid) ?? null; }
  function others() { const s = current && sessionOf(current.sid); return s ? s.seats.filter(seat => seat.seat_id !== current.seat) : []; }
  function requestSync() {
    // 다른 자리 모두에게 내가 가진 상태(state vector)를 보내 빠진 변경분을 받는다. 처음 들어온 손님은 호스트에게서 전체를 받는다.
    if (!current || current.ended) return;
    const vector = collabLib.encodeStateVector(current.editor.doc);
    for (const seat of others()) void send('sync-request', vector, seat.seat_id);
  }

  function renderList() {
    if (current) return;
    const share = button('내 파일 공유하기', 'btn-primary', () => void startHost());
    const intro = element('p', 'p-sub', '내 PC의 파일 하나를 공유하면 최대 3명이 함께 고칩니다. 파일은 공유한 사람 PC에만 있고, 저장도 공유한 사람만 합니다(100KB 이하 UTF-8 텍스트).');
    const list = element('div', 'community-collab-list');
    if (!sessions.length) list.append(element('p', 'p-sub', '진행 중인 공동 편집이 없습니다.'));
    for (const session of sessions) {
      const card = element('div', 'community-collab-card');
      const full = session.seats.length >= session.capacity;
      const mine = session.seats.some(seat => seat.host && seat.user?.id === myId());
      card.append(element('span', 'community-member-name', session.file_name),
        element('span', 'p-sub', `${who(session.host)} 공유 · ${session.seats.length}/${session.capacity}명`));
      const join = button(full ? '가득 참' : '참여', 'btn-secondary', () => void startGuest(session.session_id));
      join.disabled = full || mine;
      card.append(join);
      list.append(card);
    }
    main.replaceChildren(element('h3', 'ui-panel-title', '공동 편집'), intro, share, list, status);
  }

  function renderSession() {
    const session = sessionOf(current.sid);
    const head = element('div', 'ui-toolbar community-collab-head');
    head.append(element('span', 'community-member-name', session?.file_name ?? current.fileName));
    const people = element('span', 'community-collab-people');
    for (const seat of session?.seats ?? []) {
      const chip = element('span', 'community-collab-chip', `${who(seat.user)}${seat.host ? ' · 호스트' : ''}${seat.seat_id === current.seat ? ' (나)' : ''}`);
      chip.style.borderColor = seatColor(seat.seat_id);
      people.append(chip);
    }
    head.append(people);
    const actions = element('div', 'ui-toolbar');
    if (current.ended) actions.append(button('목록으로', 'btn-secondary', () => { closeSession(); renderList(); }));
    else if (current.host) actions.append(button('저장', 'btn-primary', () => void save()), confirmButton('공유 끝내기', () => leave()));
    else actions.append(button('나가기', 'btn-secondary', () => void leave()));
    main.replaceChildren(head, actions, status, current.mount);
  }

  async function begin(reply, { host, text, language, handle, fileName }) {
    const lib = await loadCollabLib();
    if (run !== generation) return;
    const mount = element('div', 'community-collab-editor');
    const editor = lib.createCollabEditor({ parent: mount, text: host ? text : null, language, readOnly: !host,
      user: { name: me?.github_login ? `@${me.github_login}` : '나', color: seatColor(reply.seat_id) } });
    current = { sid: reply.session_id, seat: reply.seat_id, host, handle, fileName, editor, mount, synced: host, pending: [], flush: null,
      outbox: Promise.resolve(), ended: false, timer: null, retry: null };
    const mine = current;
    editor.doc.on('update', (update, origin) => {
      if (origin === lib.REMOTE || mine.ended) return;
      // 타자 몇 번을 한 메시지로 묶어 보낸다.
      mine.pending.push(update);
      mine.flush ??= setTimeout(() => { mine.flush = null; const merged = lib.mergeUpdates(mine.pending.splice(0)); void send('update', merged); }, 50);
    });
    let cursorTimer = null;
    editor.awareness.on('update', (_change, origin) => {
      if (origin === lib.REMOTE || mine.ended || cursorTimer) return;
      cursorTimer = setTimeout(() => { cursorTimer = null; void send('awareness', lib.encodeAwareness(editor.awareness, [editor.doc.clientID])); }, 100);
    });
    const every = Math.max(5, Number(reply.heartbeat_seconds) || 10) * 1000;
    mine.timer = setInterval(async () => {
      try { const value = await api('POST', repoPath(`/collab/${mine.sid}/heartbeat`), { seat_id: mine.seat }); if (current === mine) updateSessions(value.session); }
      catch (error) { if (current === mine && ['COLLAB_ENDED', 'MEMBER_REMOVED', 'AUTH_REQUIRED'].includes(error.code)) end('공동 편집이 끝났습니다.'); }
    }, every);
    updateSessions(reply.session);
    status.textContent = host ? '파일을 공유했습니다. 다른 멤버가 참여하면 함께 편집됩니다.' : '호스트와 동기화하는 중…';
    renderSession();
    if (!host) {
      requestSync();
      // 호스트 응답이 늦으면 다시 요청한다.
      mine.retry = setInterval(() => { if (mine.synced || mine.ended) clearInterval(mine.retry); else requestSync(); }, 4000);
    }
  }

  function updateSessions(session) {
    if (!session) return;
    sessions = [...sessions.filter(s => s.session_id !== session.session_id), session].sort((a, b) => a.created_at - b.created_at);
    if (current && !current.ended && current.sid === session.session_id) renderSession();
  }

  function onCollab(data) {
    if (!current || current.ended || data.session_id !== current.sid || (data.to && data.to !== current.seat) || typeof data.data !== 'string') return;
    let bytes; try { bytes = fromBase64(data.data); } catch { return; }
    const { editor } = current;
    try {
      if (data.kind === 'update') collabLib.applyUpdate(editor.doc, bytes);
      else if (data.kind === 'awareness') collabLib.applyAwareness(editor.awareness, bytes);
      else if (data.kind === 'sync-request') {
        void send('sync', collabLib.encodeStateAsUpdate(editor.doc, bytes), data.from);
        void send('awareness', collabLib.encodeAwareness(editor.awareness, [editor.doc.clientID]), data.from);
      } else if (data.kind === 'sync') {
        collabLib.applyUpdate(editor.doc, bytes);
        if (!current.synced) {
          current.synced = true; editor.setReadOnly(false); status.textContent = '함께 편집하는 중입니다. 저장은 호스트가 합니다.';
          void send('awareness', collabLib.encodeAwareness(editor.awareness, [editor.doc.clientID]));
        }
      }
    } catch { status.textContent = '받은 변경을 적용하지 못했습니다.'; }
  }

  function onSessions(data) {
    if (!Array.isArray(data.sessions)) return;
    sessions = data.sessions;
    if (current && !current.ended && (data.ended || []).includes(current.sid)) end(current.host ? '공유가 끝났습니다.' : '호스트가 공유를 끝냈습니다. 내용은 복사해 둘 수 있습니다.');
    else if (current && !current.ended) renderSession();
    else renderList();
  }

  function end(text) {
    if (!current || current.ended) return;
    current.ended = true;
    clearInterval(current.timer); clearInterval(current.retry); clearTimeout(current.flush);
    current.editor.setReadOnly(true);
    status.textContent = text;
    renderSession();
  }
  function closeSession() {
    if (!current) return;
    const mine = current; current = null;
    clearInterval(mine.timer); clearInterval(mine.retry); clearTimeout(mine.flush);
    mine.editor.destroy();
    if (mine.handle) void globalThis.desktop.collab.close({ handle: mine.handle }).catch(() => {});
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
    if (!current?.host) return;
    const mine = current;
    status.textContent = '저장하는 중…';
    try {
      await globalThis.desktop.collab.save({ handle: mine.handle, text: mine.editor.ytext.toString() });
      status.textContent = `저장했습니다 (${new Date().toLocaleTimeString()}).`;
    } catch (error) { status.textContent = fileError(error); }
  }

  async function startHost() {
    if (current) return;
    let opened;
    try { opened = await globalThis.desktop.collab.open(); } catch (error) { status.textContent = fileError(error); return; }
    if (!opened || opened.cancelled) return;
    let reply;
    try { reply = await api('POST', repoPath('/collab'), { file_name: opened.name, language: opened.language }); }
    catch (error) { void globalThis.desktop.collab.close({ handle: opened.handle }).catch(() => {}); status.textContent = error.message; return; }
    await begin(reply, { host: true, text: opened.text, language: opened.language, handle: opened.handle, fileName: opened.name });
  }
  async function startGuest(sid) {
    if (current) return;
    let reply;
    try { reply = await api('POST', repoPath(`/collab/${sid}/join`)); }
    catch (error) { status.textContent = error.message; return; }
    await begin(reply, { host: false, language: reply.session.language, fileName: reply.session.file_name });
  }
  async function refresh() {
    try { const value = await api('GET', repoPath('/collab')); if (run === generation) onSessions({ sessions: value.sessions, ended: [] }); }
    catch (error) { status.textContent = error.message; }
  }

  let ready = false;
  function onEvent({ type, data }) {
    if (run !== generation) return;
    if (type === 'ready') { void voice.refresh(); void refresh(); if (ready && current?.synced) requestSync(); ready = true; }
    else if (type === 'collab-sessions') onSessions(data);
    else if (type === 'collab') onCollab(data);
    else if (type === 'voice') voice.update(data);
    else if (type === 'voice-signal') voice.signal(data);
    else if (type === 'status' && data.state === 'stopped') {
      status.textContent = CHAT_STOPPED[data.code] || '연결이 끊겼습니다.';
      if (CHAT_FINAL.includes(data.code)) { end(status.textContent); if (voice.joined) void voice.leave(status.textContent); }
      closeLive = null;
    } else if (type === 'status' && data.state === 'reconnecting') status.textContent = '연결이 끊겨 다시 연결하는 중…';
  }

  closeCollab = () => { const mine = current; closeSession(); if (mine && !mine.ended) void api('POST', repoPath(`/collab/${mine.sid}/leave`), { seat_id: mine.seat }).catch(() => {}); };
  renderList();
  openLive(repositoryId, onEvent);
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
