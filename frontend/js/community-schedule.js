import { element, createState } from './ui/components.js';
import { fmtDateTime } from './fmt.js';
/* ── 커뮤니티 일정 탭: 월간·주간·일간 캘린더, 유형·프로젝트·담당자 필터, 검색, 등록·상세·수정·삭제, 참석 응답 ──
   community.js의 renderCommunity()가 ?tab=schedule일 때 renderSchedule(target, ctx)를 부른다.
   서버 기준: 멤버 누구나 등록, 작성자·관리자만 수정·삭제, '참여자만' 일정은 작성자·담당자·참여자에게만 보인다.
   개발 연동은 서버가 수집한 PR·릴리스와 이 커뮤니티의 스프린트·마일스톤 일정을 쓴다. 이슈는 번호와 GitHub 링크만 둔다.
   사용자 글은 모두 textContent로만 그린다. 시각은 이 PC의 시간대로 보여 준다. */

export const TYPES = {
  meeting: { label: '회의', group: 'meeting', tone: 'general' },
  team_meeting: { label: '팀 미팅', group: 'meeting', tone: 'general' },
  tech_share: { label: '기술 공유', group: 'general', tone: 'general' },
  community_event: { label: '커뮤니티 행사', group: 'general', tone: 'general' },
  other: { label: '기타 일정', group: 'general', tone: 'general' },
  task: { label: '개발 작업', group: 'task', tone: 'task' },
  code_review: { label: '코드 리뷰', group: 'code_review', tone: 'review' },
  sprint: { label: '스프린트', group: 'sprint', tone: 'plan' },
  milestone: { label: '마일스톤', group: 'milestone', tone: 'plan' },
  deploy: { label: '배포', group: 'release', tone: 'release' },
  release: { label: '릴리즈', group: 'release', tone: 'release' },
};
const TYPE_SECTIONS = [['일반 일정', ['meeting', 'team_meeting', 'tech_share', 'community_event', 'other']],
  ['개발 프로세스 일정', ['task', 'code_review', 'sprint', 'milestone', 'deploy', 'release']]];
const FILTERS = [['meeting', '회의'], ['general', '일반'], ['task', '개발 작업'], ['code_review', '코드 리뷰'], ['sprint', '스프린트'],
  ['milestone', '마일스톤'], ['release', '배포 및 릴리즈']];
const STATUS = { planned: '예정', in_progress: '진행 중', done: '완료', cancelled: '취소' };
const RESPONSE = { pending: '응답 전', accepted: '참석', declined: '불참', tentative: '미정' };
const RECURRENCE = { none: '반복 안 함', daily: '매일', weekly: '매주', monthly: '매월' };
const REMINDERS = [[null, '알림 없음'], [0, '시작할 때'], [10, '10분 전'], [30, '30분 전'], [60, '1시간 전'], [1440, '하루 전']];
const PR_STATE = { open: '열림', merged: '병합됨', closed: '닫힘' };
const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];
const DAY = 86400000;

const pad = value => String(value).padStart(2, '0');
const dayKey = date => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
const startOfDay = date => new Date(date.getFullYear(), date.getMonth(), date.getDate());
const addDays = (date, days) => new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);
const fromKey = key => { const [y, m, d] = key.split('-').map(Number); return new Date(y, m - 1, d); };
const timeText = date => `${pad(date.getHours())}:${pad(date.getMinutes())}`;
/* 서버 본문은 초와 UTC 오프셋이 있는 ISO 형식이어야 한다(반복 일정은 이 오프셋 기준으로 계산된다). */
function offsetIso(date) {
  const minutes = -date.getTimezoneOffset(), sign = minutes >= 0 ? '+' : '-', abs = Math.abs(minutes);
  return `${dayKey(date)}T${pad(date.getHours())}:${pad(date.getMinutes())}:00${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}
const localInput = date => `${dayKey(date)}T${timeText(date)}`;
/* 종일 일정의 끝은 다음 날 0시(포함하지 않음)로 저장한다. 표시할 마지막 날은 그 직전이다. */
function lastDay(item) {
  const start = new Date(item.starts_at), end = new Date(item.ends_at);
  if (end <= start) return startOfDay(start);
  const edge = item.all_day || (end.getHours() === 0 && end.getMinutes() === 0) ? new Date(end.getTime() - 1) : end;
  return startOfDay(edge);
}
function spansDay(item, day) { const start = startOfDay(new Date(item.starts_at)); return start <= day && day <= lastDay(item); }
function whenText(item) {
  const start = new Date(item.starts_at), end = new Date(item.ends_at), last = lastDay(item);
  const date = d => `${d.getMonth() + 1}월 ${d.getDate()}일(${WEEKDAYS[d.getDay()]})`;
  if (item.all_day) return dayKey(start) === dayKey(last) ? `${date(start)} 종일` : `${date(start)} ~ ${date(last)} 종일`;
  if (dayKey(start) === dayKey(end) || +end === +start) return `${date(start)} ${timeText(start)}${+end > +start ? ` ~ ${timeText(end)}` : ''}`;
  return `${date(start)} ${timeText(start)} ~ ${date(end)} ${timeText(end)}`;
}

function typeBadge(type) {
  const meta = TYPES[type] || TYPES.other;
  const badge = element('span', `sched-badge sched-tone-${meta.tone}`, meta.label);
  return badge;
}

export function renderSchedule(target, ctx) {
  const { api, view, who, button, confirmButton, go } = ctx;
  const currentRepo = view.repository.id;
  const today = startOfDay(new Date());
  const state = { mode: 'month', cursor: today, selected: today, scope: 'repo', assignee: '', types: new Set(), query: '',
    items: [], links: null, loading: 0, me: ctx.myId?.() ?? null };

  const header = element('section', 'panel sched-head');
  const titleRow = element('div', 'sched-title-row');
  const titles = element('div');
  titles.append(element('h3', 'ui-panel-title', '일정'), element('p', 'p-sub', '커뮤니티 및 개발 프로젝트 일정을 확인하세요.'));
  const search = element('form', 'sched-search'); search.setAttribute('role', 'search');
  const searchInput = element('input'); searchInput.type = 'search'; searchInput.maxLength = 100; searchInput.placeholder = '일정 검색';
  searchInput.setAttribute('aria-label', '일정 검색');
  const searchButton = element('button', 'btn-secondary', '검색'); searchButton.type = 'submit';
  search.append(searchInput, searchButton);
  const add = button('일정 추가', 'btn-primary', () => openForm({ date: state.selected }));
  titleRow.append(titles, search, add);
  const reminder = element('div', 'sched-reminder'); reminder.setAttribute('role', 'status'); reminder.hidden = true;

  const nav = element('div', 'ui-toolbar sched-nav');
  const prev = button('‹', 'btn-secondary', () => move(-1)); prev.setAttribute('aria-label', '이전 기간');
  const todayButton = button('오늘', 'btn-secondary', () => { state.cursor = today; state.selected = today; void load(); });
  const next = button('›', 'btn-secondary', () => move(1)); next.setAttribute('aria-label', '다음 기간');
  const period = element('h4', 'sched-period'); period.setAttribute('aria-live', 'polite');
  const picker = element('input'); picker.type = 'date'; picker.setAttribute('aria-label', '날짜 선택');
  picker.addEventListener('change', () => { if (!picker.value) return; state.cursor = fromKey(picker.value); state.selected = state.cursor; void load(); });
  const modes = element('div', 'sched-modes'); modes.setAttribute('role', 'group'); modes.setAttribute('aria-label', '보기');
  for (const [key, label] of [['month', '월간'], ['week', '주간'], ['day', '일간']]) {
    const b = button(label, 'btn-secondary', () => { state.mode = key; state.cursor = state.selected; void load(); });
    b.dataset.mode = key; modes.append(b);
  }
  nav.append(prev, todayButton, next, period, picker, modes);

  const filters = element('div', 'sched-filters');
  const chips = element('div', 'sched-chips'); chips.setAttribute('role', 'group'); chips.setAttribute('aria-label', '일정 유형 필터');
  const all = button('전체 일정', 'sched-chip', () => { state.types.clear(); paint(); });
  chips.append(all);
  for (const [key, label] of FILTERS) {
    const chip = button(label, `sched-chip sched-chip-${key}`, () => { if (state.types.has(key)) state.types.delete(key); else state.types.add(key); paint(); });
    chip.dataset.filter = key; chips.append(chip);
  }
  const scope = element('select'); scope.setAttribute('aria-label', '프로젝트');
  scope.append(new Option(`이 저장소(${view.repository.name || view.repository.url})`, 'repo'), new Option('내 모든 커뮤니티', 'all'));
  scope.addEventListener('change', () => { state.scope = scope.value; void load(); });
  const assignee = element('select'); assignee.setAttribute('aria-label', '담당자');
  assignee.append(new Option('담당자 전체', ''), new Option('내가 담당', 'me'));
  assignee.addEventListener('change', () => { state.assignee = assignee.value; paint(); });
  filters.append(chips, scope, assignee);
  header.append(titleRow, reminder, nav, filters);

  const message = element('p', 'p-sub sched-message'); message.setAttribute('role', 'status');
  const layout = element('div', 'sched-layout');
  const calendar = element('section', 'panel sched-calendar');
  const side = element('aside', 'panel sched-side'); side.setAttribute('aria-label', '선택한 날짜의 일정');
  layout.append(calendar, side);
  target.replaceChildren(header, message, layout);

  search.addEventListener('submit', event => { event.preventDefault(); state.query = searchInput.value.trim(); if (state.query) void runSearch(); else paint(); });
  searchInput.addEventListener('search', () => { if (!searchInput.value) { state.query = ''; paint(); } });

  function move(step) {
    const c = state.cursor;
    state.cursor = state.mode === 'month' ? new Date(c.getFullYear(), c.getMonth() + step, 1) : addDays(c, step * (state.mode === 'week' ? 7 : 1));
    if (state.mode !== 'month' || state.selected.getMonth() !== state.cursor.getMonth()) state.selected = state.cursor;
    void load();
  }

  function range() {
    const c = state.cursor;
    if (state.mode === 'month') { const first = new Date(c.getFullYear(), c.getMonth(), 1); const start = addDays(first, -first.getDay()); return [start, addDays(start, 42)]; }
    if (state.mode === 'week') { const start = addDays(c, -c.getDay()); return [start, addDays(start, 7)]; }
    return [startOfDay(c), addDays(c, 1)];
  }

  async function load() {
    const run = ++state.loading;
    const [start, end] = range();
    const query = new URLSearchParams({ start: start.toISOString(), end: end.toISOString() });
    const path = state.scope === 'all' ? `/api/community/v1/events?${query}` : `/api/community/v1/repositories/${currentRepo}/events?${query}`;
    message.textContent = '일정을 불러오는 중…';
    try {
      const value = await api('GET', path);
      if (run !== state.loading || !target.isConnected) return;
      state.items = value.events; message.textContent = value.truncated ? '일정이 너무 많아 일부만 표시합니다. 기간을 좁혀 보세요.' : '';
    } catch (error) {
      if (run !== state.loading || !target.isConnected) return;
      state.items = []; message.textContent = error.message;
    }
    paint();
  }

  async function loadLinks() {
    if (state.links) return state.links;
    state.links = await api('GET', `/api/community/v1/repositories/${currentRepo}/event-links`);
    const known = new Set([...assignee.options].map(o => o.value));
    for (const member of state.links.members) if (!known.has(member.id)) assignee.append(new Option(who(member), member.id));
    return state.links;
  }

  function matches(item) {
    if (state.types.size && !state.types.has((TYPES[item.event_type] || TYPES.other).group)) return false;
    if (state.assignee === 'me') return !!state.me && item.assignee?.id === state.me;
    if (state.assignee) return item.assignee?.id === state.assignee;
    return true;
  }

  function paint() {
    if (!target.isConnected) return;
    all.setAttribute('aria-pressed', String(!state.types.size));
    for (const chip of chips.querySelectorAll('[data-filter]')) chip.setAttribute('aria-pressed', String(state.types.has(chip.dataset.filter)));
    for (const b of modes.children) { b.className = b.dataset.mode === state.mode ? 'btn-primary' : 'btn-secondary'; b.setAttribute('aria-pressed', String(b.dataset.mode === state.mode)); }
    picker.value = dayKey(state.selected);
    const items = state.items.filter(matches);
    const c = state.cursor;
    if (state.mode === 'month') period.textContent = `${c.getFullYear()}년 ${c.getMonth() + 1}월`;
    else if (state.mode === 'week') { const [s, e] = range(); const last = addDays(e, -1); period.textContent = `${s.getFullYear()}년 ${s.getMonth() + 1}월 ${s.getDate()}일 ~ ${last.getMonth() + 1}월 ${last.getDate()}일`; }
    else period.textContent = `${c.getFullYear()}년 ${c.getMonth() + 1}월 ${c.getDate()}일 (${WEEKDAYS[c.getDay()]})`;
    if (state.mode === 'month') paintMonth(items); else if (state.mode === 'week') paintWeek(items); else paintDay(items);
    if (state.query) return; // 검색 결과가 오른쪽 패널을 쓰는 중
    paintSide(items);
    checkReminders();
  }

  function chip(item, compact) {
    const meta = TYPES[item.event_type] || TYPES.other;
    const b = button('', `sched-event sched-tone-${meta.tone}${item.status === 'cancelled' ? ' sched-cancelled' : ''}`, event => { event.stopPropagation(); void openDetail(item); });
    const time = item.all_day ? '' : `${timeText(new Date(item.starts_at))} `;
    b.textContent = `${compact ? time : ''}${item.title}`;
    b.title = `${meta.label} · ${whenText(item)} · ${item.title}${state.scope === 'all' ? ` · ${item.repository.name}` : ''}`;
    return b;
  }

  function sorted(items) { return [...items].sort((a, b) => (b.all_day - a.all_day) || a.starts_at.localeCompare(b.starts_at) || a.title.localeCompare(b.title)); }

  function paintMonth(items) {
    const [start] = range();
    const grid = element('div', 'sched-month'); grid.setAttribute('role', 'grid'); grid.setAttribute('aria-label', period.textContent);
    const head = element('div', 'sched-weekdays'); head.setAttribute('role', 'row');
    WEEKDAYS.forEach((name, index) => { const cell = element('div', `sched-weekday${index === 0 ? ' sched-sun' : index === 6 ? ' sched-sat' : ''}`, name); cell.setAttribute('role', 'columnheader'); head.append(cell); });
    grid.append(head);
    for (let week = 0; week < 6; week++) {
      const row = element('div', 'sched-week'); row.setAttribute('role', 'row');
      for (let d = 0; d < 7; d++) {
        const day = addDays(start, week * 7 + d);
        const cell = element('div', 'sched-day'); cell.setAttribute('role', 'gridcell'); cell.tabIndex = 0;
        if (day.getMonth() !== state.cursor.getMonth()) cell.classList.add('sched-outside');
        if (+day === +today) cell.classList.add('sched-today');
        if (+day === +state.selected) { cell.classList.add('sched-selected'); cell.setAttribute('aria-selected', 'true'); }
        const list = sorted(items.filter(item => spansDay(item, day)));
        cell.setAttribute('aria-label', `${day.getMonth() + 1}월 ${day.getDate()}일, 일정 ${list.length}개`);
        cell.append(element('span', `sched-date${d === 0 ? ' sched-sun' : d === 6 ? ' sched-sat' : ''}`, String(day.getDate())));
        for (const item of list.slice(0, 3)) cell.append(chip(item, true));
        if (list.length > 3) cell.append(element('span', 'sched-more', `+${list.length - 3}개 더`));
        const select = () => { state.selected = day; if (day.getMonth() !== state.cursor.getMonth()) { state.cursor = day; void load(); } else paint(); };
        cell.addEventListener('click', select);
        cell.addEventListener('dblclick', () => openForm({ date: day }));
        cell.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); } });
        row.append(cell);
      }
      grid.append(row);
    }
    calendar.replaceChildren(grid, element('p', 'p-sub sched-hint', '날짜를 누르면 오른쪽에 그날 일정이 보이고, 두 번 누르면 그날로 일정을 추가합니다.'));
  }

  function paintWeek(items) {
    const [start] = range();
    const grid = element('div', 'sched-weekview');
    for (let d = 0; d < 7; d++) {
      const day = addDays(start, d);
      const column = element('section', `sched-column${+day === +today ? ' sched-today' : ''}${+day === +state.selected ? ' sched-selected' : ''}`);
      const head = button(`${WEEKDAYS[d]} ${day.getMonth() + 1}/${day.getDate()}`, `sched-column-head${d === 0 ? ' sched-sun' : d === 6 ? ' sched-sat' : ''}`, () => { state.selected = day; paint(); });
      head.addEventListener('dblclick', () => openForm({ date: day }));
      column.append(head);
      const list = sorted(items.filter(item => spansDay(item, day)));
      if (!list.length) column.append(element('p', 'p-sub sched-empty', '일정 없음'));
      for (const item of list) column.append(chip(item, true));
      grid.append(column);
    }
    calendar.replaceChildren(grid);
  }

  function paintDay(items) {
    const day = startOfDay(state.cursor);
    const list = sorted(items.filter(item => spansDay(item, day)));
    const box = element('div', 'sched-dayview');
    const allDay = list.filter(item => item.all_day || startOfDay(new Date(item.starts_at)) < day);
    const allRow = element('div', 'sched-hour sched-allday');
    allRow.append(element('span', 'sched-hour-label', '종일'));
    const allSlot = element('div', 'sched-hour-slot'); allDay.forEach(item => allSlot.append(chip(item, false))); allRow.append(allSlot);
    box.append(allRow);
    for (let hour = 0; hour < 24; hour++) {
      const row = element('div', 'sched-hour');
      row.append(element('span', 'sched-hour-label', `${pad(hour)}:00`));
      const slot = element('div', 'sched-hour-slot');
      for (const item of list) if (!allDay.includes(item) && new Date(item.starts_at).getHours() === hour) slot.append(chip(item, true));
      slot.addEventListener('dblclick', () => { const at = new Date(day); at.setHours(hour); openForm({ date: day, at }); });
      row.append(slot); box.append(row);
    }
    calendar.replaceChildren(box);
  }

  function card(item) {
    const meta = TYPES[item.event_type] || TYPES.other;
    const box = element('article', `sched-card sched-tone-${meta.tone}`);
    const title = button(item.title, 'sched-card-title', () => openDetail(item));
    const line = element('div', 'sched-card-meta');
    line.append(typeBadge(item.event_type), element('span', 'p-sub', whenText(item)));
    if (item.status !== 'planned') line.append(element('span', 'community-badge', STATUS[item.status]));
    if (item.visibility === 'participants') line.append(element('span', 'community-badge', '참여자만'));
    box.append(title, line);
    const people = [];
    if (item.assignee) people.push(`담당 ${who(item.assignee)}`);
    if (item.participants.length) people.push(`참여 ${item.participants.map(who).join(', ')}`);
    if (people.length) box.append(element('p', 'p-sub sched-card-people', people.join(' · ')));
    box.append(element('p', 'p-sub', `프로젝트 ${item.repository.name}${item.my_response ? ` · 내 응답 ${RESPONSE[item.my_response]}` : ''}`));
    return box;
  }

  function paintSide(items) {
    const day = state.selected;
    const list = sorted(items.filter(item => spansDay(item, day)));
    side.replaceChildren(element('h4', 'ui-panel-title', `${day.getMonth() + 1}월 ${day.getDate()}일 (${WEEKDAYS[day.getDay()]})`));
    if (!list.length) {
      const empty = createState({ kind: 'empty', title: '일정이 없습니다', message: state.types.size || state.assignee ? '필터에 맞는 일정이 없습니다.' : '이 날짜에 일정을 추가해 보세요.' });
      side.append(empty, button('이 날짜에 일정 추가', 'btn-secondary', () => openForm({ date: day })));
      return;
    }
    for (const item of list) side.append(card(item));
  }

  async function runSearch() {
    const run = ++state.loading;
    side.replaceChildren(createState({ kind: 'loading', title: '검색하는 중' }));
    try {
      const value = await api('GET', `/api/community/v1/repositories/${currentRepo}/events?${new URLSearchParams({ q: state.query })}`);
      if (run !== state.loading || !target.isConnected) return;
      const head = element('div', 'sched-side-head');
      head.append(element('h4', 'ui-panel-title', `'${value.query}' 검색 결과 ${value.events.length}개`),
        button('검색 닫기', 'btn-secondary', () => { state.query = ''; searchInput.value = ''; paint(); }));
      side.replaceChildren(head);
      if (!value.events.length) side.append(element('p', 'p-sub', '제목이나 설명에 검색어가 들어간 일정이 없습니다. 이 저장소의 일정만 검색합니다.'));
      for (const item of value.events) side.append(card(item));
    } catch (error) { if (run === state.loading) side.replaceChildren(element('p', 'p-sub', error.message)); }
  }

  /* 알림: 이 화면을 열어 둔 동안, 내가 작성·담당·참여하는 일정의 알림 시각이 지나면 위에 띄운다(앱 밖 알림은 없음). */
  const dismissed = new Set();
  function checkReminders() {
    if (!state.me) { reminder.hidden = true; return; }
    const now = Date.now();
    const due = state.items.filter(item => item.reminder_minutes !== null && item.reminder_minutes !== undefined && item.status !== 'cancelled'
      && (item.author_id === state.me || item.assignee?.id === state.me || item.participants.some(p => p.id === state.me))
      && !dismissed.has(`${item.id}:${item.starts_at}`)
      && now >= Date.parse(item.starts_at) - item.reminder_minutes * 60000 && now < Date.parse(item.starts_at) + 60000);
    reminder.replaceChildren();
    reminder.hidden = !due.length;
    for (const item of due) {
      const left = Math.max(0, Math.round((Date.parse(item.starts_at) - now) / 60000));
      const row = element('div', 'sched-reminder-row');
      row.append(element('strong', '', left ? `${left}분 후 시작` : '지금 시작'), button(item.title, 'sched-card-title', () => openDetail(item)),
        button('닫기', 'btn-secondary', () => { dismissed.add(`${item.id}:${item.starts_at}`); checkReminders(); }));
      reminder.append(row);
    }
  }
  const timer = setInterval(() => { if (!target.isConnected) clearInterval(timer); else checkReminders(); }, 60000);

  /* ── 상세 ── */
  function dialogShell(titleText) {
    const dialog = element('dialog', 'sched-dialog');
    const head = element('div', 'sched-dialog-head');
    const heading = element('h2', '', titleText); heading.id = `sched-dialog-${Date.now()}`;
    dialog.setAttribute('aria-labelledby', heading.id);
    const close = () => { dialog.close(); dialog.remove(); };
    head.append(heading, button('닫기', 'btn-secondary', close));
    dialog.append(head);
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    dialog.addEventListener('click', event => { if (event.target === dialog) close(); });
    document.body.append(dialog); dialog.showModal();
    return { dialog, close, heading };
  }

  function field(label, value) {
    const row = element('div', 'sched-field');
    row.append(element('dt', '', label));
    const dd = element('dd'); if (value instanceof Node) dd.append(value); else dd.textContent = value ?? '—'; row.append(dd);
    return row;
  }

  function externalLink(text, href) { const a = element('a', 'sched-link', text); a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a; }

  async function openDetail(item) {
    const repositoryId = item.repository?.id ?? currentRepo;
    const { dialog, close, heading } = dialogShell(item.title);
    const body = element('div', 'sched-dialog-body');
    body.append(createState({ kind: 'loading', title: '일정을 여는 중' }));
    dialog.append(body);
    let detail;
    try { detail = await api('GET', `/api/community/v1/repositories/${repositoryId}/events/${item.id}`); }
    catch (error) { body.replaceChildren(element('p', 'p-sub', error.message)); return; }
    heading.textContent = detail.title;
    const meta = element('div', 'sched-card-meta');
    meta.append(typeBadge(detail.event_type), element('span', 'community-badge', STATUS[detail.status]),
      element('span', 'community-badge', detail.visibility === 'participants' ? '참여자만 공개' : '커뮤니티 공개'));
    const occurrence = { ...detail, starts_at: item.starts_at, ends_at: item.ends_at };
    const list = element('dl', 'sched-fields');
    list.append(field('일시', whenText(occurrence) + (detail.recurrence !== 'none' ? ` · ${RECURRENCE[detail.recurrence]} 반복${detail.recurrence_until ? `(${dayKey(new Date(detail.recurrence_until))}까지)` : ''}` : '')));
    if (detail.description) { const text = element('div', 'community-text', detail.description); list.append(field('설명', text)); }
    list.append(field('담당자', detail.assignee ? who(detail.assignee) : '없음'));
    const responses = element('ul', 'sched-responses');
    for (const r of detail.responses) { const li = element('li'); li.append(element('span', '', who(r.user)), element('span', `community-badge sched-response-${r.response}`, RESPONSE[r.response])); responses.append(li); }
    list.append(field('참여자', detail.responses.length ? responses : '없음'));
    list.append(field('프로젝트', detail.repository_url ? externalLink(detail.repository.name, detail.repository_url) : detail.repository.name));
    if (detail.location) list.append(field('장소', detail.location));
    if (detail.meeting_url) {
      const box = element('span', 'sched-inline');
      box.append(externalLink(detail.meeting_url, detail.meeting_url), button('복사', 'btn-secondary', async () => { try { await navigator.clipboard.writeText(detail.meeting_url); } catch { /* 복사 권한이 없으면 링크를 직접 고른다 */ } }));
      list.append(field('화상회의', box));
    }
    if (detail.reminder_minutes !== null) list.append(field('알림', (REMINDERS.find(([v]) => v === detail.reminder_minutes) || [null, `${detail.reminder_minutes}분 전`])[1]));
    if (detail.goal) list.append(field(detail.event_type === 'milestone' ? '목표' : '스프린트 목표', detail.goal));
    if (detail.version) list.append(field('버전', detail.version));
    if (detail.environment) list.append(field('배포 환경', detail.environment));

    const dev = element('dl', 'sched-fields');
    const linkedEvent = linked => {
      if (!linked) return '없음';
      if (linked.hidden) return '비공개 일정';
      return button(`${linked.title} · ${whenText(linked)}${linked.goal ? ` · 목표: ${linked.goal}` : ''}`, 'sched-card-title', () => { close(); void openDetail({ ...linked, repository: detail.repository }); });
    };
    dev.append(field('스프린트', linkedEvent(detail.sprint)), field('마일스톤', linkedEvent(detail.milestone)));
    const pulls = element('ul', 'sched-links');
    for (const pr of detail.pulls) {
      const li = element('li'); li.append(externalLink(`#${pr.number} ${pr.title || ''}`.trim(), pr.url));
      li.append(element('span', 'p-sub', pr.found
        ? ` ${PR_STATE[pr.state] || pr.state || ''} · 작성자 @${pr.author_login || '?'}${pr.reviewers.length ? ` · 리뷰어 ${pr.reviewers.map(r => `@${r}`).join(', ')}` : ' · 리뷰어 없음'}`
        : ' 분석에서 수집되지 않은 PR입니다(다음 분석 후 정보가 보입니다).'));
      pulls.append(li);
    }
    dev.append(field('Pull Request', detail.pulls.length ? pulls : '없음'));
    const issues = element('ul', 'sched-links');
    for (const issue of detail.issues) { const li = element('li'); li.append(externalLink(`#${issue.number}`, issue.url), element('span', 'p-sub', ' 이슈 상태는 GitHub에서 확인하세요.')); issues.append(li); }
    dev.append(field('Issue', detail.issues.length ? issues : '없음'));
    if (detail.release) {
      const box = element('span');
      box.append(externalLink(detail.release.tag, detail.release.url), element('span', 'p-sub', detail.release.found
        ? ` ${detail.release.name || ''}${detail.release.published_at ? ` · 게시 ${fmtDateTime(detail.release.published_at)}` : ''}${detail.release.prerelease ? ' · 사전 릴리스' : ''}`
        : ' 분석에서 수집되지 않은 릴리스입니다.'));
      dev.append(field('릴리스', box));
    }
    const attachments = element('ul', 'sched-links');
    for (const file of detail.attachments) { const li = element('li'); li.append(externalLink(file.name, file.url)); attachments.append(li); }
    if (detail.attachments.length) dev.append(field('첨부 링크', attachments));

    const discussion = element('div', 'sched-inline');
    if (detail.post) discussion.append(button(`의견 글 열기: ${detail.post.title}`, 'btn-secondary', () => { close(); go({ id: repositoryId, tab: null, post: detail.post.id }); }));
    else if (detail.can_edit && repositoryId === currentRepo) discussion.append(button('의견 나눌 글 만들기', 'btn-secondary', async event => {
      event.currentTarget.disabled = true;
      try {
        const { id } = await api('POST', `/api/community/v1/repositories/${repositoryId}/posts`, { title: `[일정] ${detail.title}`.slice(0, 200), body: `${whenText(occurrence)}\n\n${detail.description || '이 일정에 대한 의견을 댓글로 남겨 주세요.'}`.slice(0, 20000) });
        await api('PATCH', `/api/community/v1/repositories/${repositoryId}/events/${detail.id}`, { ...bodyOf(detail), post_id: id });
        close(); go({ id: repositoryId, tab: null, post: id });
      } catch (error) { status.textContent = error.message; event.currentTarget.disabled = false; }
    }));
    else discussion.append(element('span', 'p-sub', '연결된 글이 없습니다.'));

    const status = element('p', 'p-sub'); status.setAttribute('role', 'status');
    const actions = element('div', 'ui-toolbar sched-actions');
    if (detail.can_respond) {
      const answers = element('div', 'sched-modes'); answers.setAttribute('role', 'group'); answers.setAttribute('aria-label', '참석 여부');
      for (const [key, label] of [['accepted', '참석'], ['tentative', '미정'], ['declined', '불참']]) {
        const b = button(label, detail.my_response === key ? 'btn-primary' : 'btn-secondary', async () => {
          try { await api('PUT', `/api/community/v1/repositories/${repositoryId}/events/${detail.id}/response`, { response: key }); close(); await refreshAfter(); void openDetail(item); }
          catch (error) { status.textContent = error.message; }
        });
        b.setAttribute('aria-pressed', String(detail.my_response === key)); answers.append(b);
      }
      actions.append(element('span', 'p-sub', '내 참석 여부'), answers);
    }
    if (detail.can_edit) {
      if (repositoryId === currentRepo) actions.append(button('수정', 'btn-secondary', () => { close(); void openForm({ detail }); }));
      else actions.append(button('이 일정의 커뮤니티로 이동', 'btn-secondary', () => { close(); go({ id: repositoryId, tab: 'schedule', post: null }); }));
      actions.append(confirmButton(detail.recurrence !== 'none' ? '반복 일정 전체 삭제' : '삭제', async () => {
        try { await api('DELETE', `/api/community/v1/repositories/${repositoryId}/events/${detail.id}`); close(); await refreshAfter(); }
        catch (error) { status.textContent = error.message; }
      }));
    }
    const footer = element('p', 'p-sub', `만든 사람 ${who(detail.author)} · ${fmtDateTime(detail.created_at)}${detail.updated_at ? ` · 수정 ${fmtDateTime(detail.updated_at)}` : ''}`);
    body.replaceChildren(meta, list, element('h3', 'sched-subtitle', '개발 프로세스 연동'), dev, element('h3', 'sched-subtitle', '의견'), discussion, actions, status, footer);
  }

  async function refreshAfter() { state.links = null; await load(); if (state.query) await runSearch(); }

  function bodyOf(detail) {
    return { title: detail.title, description: detail.description || '', event_type: detail.event_type, status: detail.status,
      starts_at: offsetIso(new Date(detail.series_starts_at)), ends_at: offsetIso(new Date(Date.parse(detail.series_starts_at) + (Date.parse(detail.ends_at) - Date.parse(detail.starts_at)))),
      all_day: detail.all_day, assignee_id: detail.assignee?.id ?? null, participant_ids: detail.responses.map(r => r.user.id),
      location: detail.location || null, meeting_url: detail.meeting_url || null, visibility: detail.visibility,
      reminder_minutes: detail.reminder_minutes, recurrence: detail.recurrence,
      recurrence_until: detail.recurrence_until ? offsetIso(new Date(detail.recurrence_until)) : null,
      sprint_id: detail.sprint?.id ?? null, milestone_id: detail.milestone?.id ?? null, post_id: detail.post?.id ?? null,
      issue_numbers: detail.issues.map(i => i.number), pull_numbers: detail.pulls.map(p => p.number), release_tag: detail.release?.tag ?? null,
      goal: detail.goal || null, version: detail.version || null, environment: detail.environment || null, attachments: detail.attachments };
  }

  /* ── 등록·수정 ── */
  async function openForm({ date, at, detail } = {}) {
    const editing = !!detail;
    const { dialog, close } = dialogShell(editing ? '일정 수정' : '일정 추가');
    const form = element('form', 'sched-form'); form.noValidate = true;
    dialog.append(form);
    form.append(createState({ kind: 'loading', title: '입력 항목을 준비하는 중' }));
    let links;
    try { links = await loadLinks(); } catch (error) { form.replaceChildren(element('p', 'p-sub', error.message)); return; }
    const v = detail ? bodyOf(detail) : null;
    const input = (type, label, value = '', attrs = {}) => {
      const wrap = element('label', 'sched-input'); wrap.append(element('span', '', label));
      const el = type === 'textarea' ? element('textarea') : element('input');
      if (type !== 'textarea') el.type = type;
      Object.assign(el, attrs); el.value = value ?? ''; wrap.append(el); return [wrap, el];
    };
    const select = (label, options, value) => {
      const wrap = element('label', 'sched-input'); wrap.append(element('span', '', label));
      const el = element('select'); for (const [val, text] of options) el.append(new Option(text, val ?? ''));
      el.value = value ?? ''; wrap.append(el); return [wrap, el];
    };
    const grid = (...children) => { const box = element('div', 'sched-grid'); box.append(...children); return box; };
    const fieldset = (legend, ...children) => { const box = element('fieldset', 'sched-fieldset'); box.append(element('legend', '', legend), ...children); return box; };

    // 기본 정보
    const [titleWrap, title] = input('text', '일정 제목 *', v?.title, { maxLength: 200, required: true });
    const [descWrap, description] = input('textarea', '일정 설명', v?.description, { maxLength: 10000, rows: 3 });
    const typeWrap = element('label', 'sched-input'); typeWrap.append(element('span', '', '일정 유형 *'));
    const type = element('select');
    for (const [label, keys] of TYPE_SECTIONS) { const group = element('optgroup'); group.label = label; for (const key of keys) group.append(new Option(TYPES[key].label, key)); type.append(group); }
    type.value = v?.event_type || 'meeting'; typeWrap.append(type);
    const [statusWrap, status] = select('진행 상태', Object.entries(STATUS), v?.status || 'planned');
    const allDayWrap = element('label', 'sched-check'); const allDay = element('input'); allDay.type = 'checkbox'; allDay.checked = !!v?.all_day; allDayWrap.append(allDay, element('span', '', '종일 일정'));
    const baseStart = v ? new Date(v.starts_at) : at || (() => { const d = new Date(date || today); const hour = new Date().getHours() + 1; d.setHours(+startOfDay(d) === +today && hour < 24 ? hour : 9, 0, 0, 0); return d; })();
    const baseEnd = v ? new Date(v.ends_at) : new Date(baseStart.getTime() + 3600000);
    const [startWrap, start] = input('datetime-local', '시작 *', '', { required: true });
    const [endWrap, end] = input('datetime-local', '종료 *', '', { required: true });
    const setTimes = (s, e) => {
      if (allDay.checked) { start.type = 'date'; end.type = 'date'; start.value = dayKey(s); end.value = dayKey(e > s && v?.all_day ? new Date(e.getTime() - 1) : e); }
      else { start.type = 'datetime-local'; end.type = 'datetime-local'; start.value = localInput(s); end.value = localInput(e); }
    };
    setTimes(baseStart, baseEnd);
    allDay.addEventListener('change', () => {
      const s = start.value ? new Date(allDay.checked ? start.value : `${start.value}T00:00`) : baseStart;
      const e = end.value ? new Date(allDay.checked ? end.value : `${end.value}T00:00`) : baseEnd;
      if (allDay.checked) { start.type = 'date'; end.type = 'date'; start.value = dayKey(s); end.value = dayKey(e); }
      else { const ss = new Date(s); ss.setHours(9, 0, 0, 0); const ee = new Date(e); ee.setHours(10, 0, 0, 0); start.type = 'datetime-local'; end.type = 'datetime-local'; start.value = localInput(ss); end.value = localInput(ee); }
    });
    const [goalWrap, goal] = input('textarea', '목표(스프린트·마일스톤)', v?.goal, { maxLength: 2000, rows: 2 });
    const [versionWrap, version] = input('text', '버전(배포·릴리즈)', v?.version, { maxLength: 100, placeholder: '예: 1.3.0' });
    const [envWrap, environment] = input('text', '배포 환경', v?.environment, { maxLength: 100, placeholder: '예: staging, production' });
    const typeFields = () => {
      goalWrap.hidden = !['sprint', 'milestone'].includes(type.value);
      versionWrap.hidden = envWrap.hidden = !['deploy', 'release'].includes(type.value);
    };
    type.addEventListener('change', typeFields); typeFields();

    // 참여자 정보
    const memberOptions = [['', '없음'], ...links.members.map(m => [m.id, who(m)])];
    const [assigneeWrap, assigneeSelect] = select('담당자', memberOptions, v?.assignee_id);
    const people = element('div', 'sched-people'); people.setAttribute('role', 'group'); people.setAttribute('aria-label', '참여자');
    const chosen = new Set(v?.participant_ids || []);
    for (const member of links.members) {
      const label = element('label', 'sched-check'); const box = element('input'); box.type = 'checkbox'; box.value = member.id; box.checked = chosen.has(member.id);
      label.append(box, element('span', '', who(member))); people.append(label);
    }
    const responsesNote = element('p', 'p-sub', '참석 여부는 참여자가 일정 상세에서 직접 답합니다. 기존 참여자의 응답은 수정해도 유지됩니다.');

    // 개발 프로세스 연동
    const [sprintWrap, sprint] = select('스프린트 연결', [['', '없음'], ...links.sprints.filter(s => s.id !== detail?.id).map(s => [String(s.id), `${s.title} (${dayKey(new Date(s.starts_at))})`])], v?.sprint_id ? String(v.sprint_id) : '');
    const [milestoneWrap, milestone] = select('마일스톤 연결', [['', '없음'], ...links.milestones.filter(s => s.id !== detail?.id).map(s => [String(s.id), `${s.title} (${dayKey(new Date(s.starts_at))})`])], v?.milestone_id ? String(v.milestone_id) : '');
    const pullWrap = element('label', 'sched-input'); pullWrap.append(element('span', '', 'Pull Request 연결(여러 개 선택, 최대 10개)'));
    const pulls = element('select'); pulls.multiple = true; pulls.size = Math.min(6, Math.max(2, links.pulls.length));
    const selectedPulls = new Set(v?.pull_numbers || []);
    for (const pr of links.pulls) { const o = new Option(`#${pr.number} ${pr.title || ''} · ${PR_STATE[pr.state] || pr.state || ''}`, String(pr.number)); o.selected = selectedPulls.has(pr.number); pulls.append(o); }
    for (const number of selectedPulls) if (!links.pulls.some(pr => pr.number === number)) { const o = new Option(`#${number} (수집되지 않은 PR)`, String(number)); o.selected = true; pulls.append(o); }
    pullWrap.append(pulls);
    if (!links.pulls.length) pullWrap.append(element('span', 'p-sub', '분석에서 수집된 PR이 없습니다. 아래 번호 입력란에 PR 번호를 적을 수 있습니다.'));
    const [pullExtraWrap, pullExtra] = input('text', 'PR 번호 직접 입력(쉼표로 구분)', '', { placeholder: '예: 12, 15' });
    const [issueWrap, issues] = input('text', 'Issue 번호(쉼표로 구분)', (v?.issue_numbers || []).join(', '), { placeholder: '예: 34, 35' });
    const issueNote = element('p', 'p-sub', 'GitViz는 이슈를 수집하지 않으므로 번호와 GitHub 링크만 저장합니다.');
    const [releaseWrap, release] = select('릴리스 연결', [['', '없음'], ...links.releases.map(r => [r.tag, `${r.tag}${r.name && r.name !== r.tag ? ` · ${r.name}` : ''}`]),
      ...(v?.release_tag && !links.releases.some(r => r.tag === v.release_tag) ? [[v.release_tag, `${v.release_tag} (수집되지 않음)`]] : [])], v?.release_tag || '');

    // 추가 설정
    const [locationWrap, location] = input('text', '장소', v?.location, { maxLength: 200 });
    const [meetingWrap, meeting] = input('url', '화상회의 URL', v?.meeting_url, { maxLength: 500, placeholder: 'https://' });
    const attachmentList = element('div', 'sched-attachments');
    const addAttachment = (name = '', url = '') => {
      const row = element('div', 'sched-attachment');
      const n = element('input'); n.type = 'text'; n.maxLength = 200; n.placeholder = '이름'; n.value = name; n.setAttribute('aria-label', '첨부 이름');
      const u = element('input'); u.type = 'url'; u.maxLength = 500; u.placeholder = 'https://'; u.value = url; u.setAttribute('aria-label', '첨부 링크');
      row.append(n, u, button('빼기', 'btn-secondary', () => row.remove())); attachmentList.append(row);
    };
    for (const file of v?.attachments || []) addAttachment(file.name, file.url);
    const attachWrap = element('div', 'sched-input');
    attachWrap.append(element('span', '', '첨부 링크(최대 10개)'), attachmentList,
      button('첨부 링크 추가', 'btn-secondary', () => { if (attachmentList.childElementCount < 10) addAttachment(); }),
      element('span', 'p-sub', '파일은 서버에 올리지 않습니다. 드라이브·GitHub 등에 있는 파일의 링크를 남기세요.'));
    const [reminderWrap, reminderSelect] = select('알림 설정', REMINDERS.map(([val, text]) => [val === null ? '' : String(val), text]), v?.reminder_minutes === null || v?.reminder_minutes === undefined ? '' : String(v.reminder_minutes));
    const reminderNote = element('p', 'p-sub', '알림은 이 일정 화면을 열어 둔 동안 작성자·담당자·참여자에게 화면 위쪽에 표시됩니다.');
    const [recurWrap, recurrence] = select('반복 일정', Object.entries(RECURRENCE), v?.recurrence || 'none');
    const [untilWrap, until] = input('date', '반복 종료일', v?.recurrence_until ? dayKey(new Date(v.recurrence_until)) : '');
    const recurFields = () => { untilWrap.hidden = recurrence.value === 'none'; };
    recurrence.addEventListener('change', recurFields); recurFields();
    const visibility = element('div', 'sched-people'); visibility.setAttribute('role', 'radiogroup'); visibility.setAttribute('aria-label', '공개 범위');
    for (const [key, label] of [['community', '커뮤니티 멤버 모두'], ['participants', '작성자·담당자·참여자만']]) {
      const wrap = element('label', 'sched-check'); const radio = element('input'); radio.type = 'radio'; radio.name = 'sched-visibility'; radio.value = key; radio.checked = (v?.visibility || 'community') === key;
      wrap.append(radio, element('span', '', label)); visibility.append(wrap);
    }

    const message = element('p', 'sched-error'); message.setAttribute('role', 'alert');
    const submit = element('button', 'btn-primary', editing ? '저장' : '등록'); submit.type = 'submit';
    const actions = element('div', 'ui-toolbar sched-actions'); actions.append(submit, button('취소', 'btn-secondary', close));
    form.replaceChildren(
      fieldset('기본 정보', titleWrap, descWrap, grid(typeWrap, statusWrap), allDayWrap, grid(startWrap, endWrap), goalWrap, grid(versionWrap, envWrap)),
      fieldset('참여자 정보', assigneeWrap, element('span', 'sched-label', '참여자'), people, responsesNote),
      fieldset('개발 프로세스 연동', element('p', 'p-sub', `연결 프로젝트: ${view.repository.name || view.repository.url}`), grid(sprintWrap, milestoneWrap), pullWrap, pullExtraWrap, issueWrap, issueNote, releaseWrap),
      fieldset('추가 설정', grid(locationWrap, meetingWrap), attachWrap, grid(reminderWrap, recurWrap), reminderNote, untilWrap, element('span', 'sched-label', '공개 범위'), visibility),
      message, actions);
    title.focus();

    const numbers = text => text.split(/[\s,#]+/).filter(Boolean);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      message.textContent = '';
      const fail = (text, focus) => { message.textContent = text; focus?.focus(); };
      if (!title.value.trim()) return fail('일정 제목을 입력하세요.', title);
      if (!start.value || !end.value) return fail('시작과 종료 시각을 입력하세요.', start.value ? end : start);
      const s = allDay.checked ? fromKey(start.value) : new Date(start.value);
      const e = allDay.checked ? addDays(fromKey(end.value), 1) : new Date(end.value);
      if (Number.isNaN(+s) || Number.isNaN(+e)) return fail('날짜 형식을 확인하세요.', start);
      if (e < s || (allDay.checked && fromKey(end.value) < s)) return fail('종료가 시작보다 빠를 수 없습니다.', end);
      if (e - s > 366 * DAY) return fail('일정 기간은 1년을 넘을 수 없습니다.', end);
      const extra = numbers(pullExtra.value), issueList = numbers(issues.value);
      if ([...extra, ...issueList].some(n => !/^\d{1,9}$/.test(n) || Number(n) < 1)) return fail('PR·Issue 번호는 숫자로 적으세요.', extra.length ? pullExtra : issues);
      const pullNumbers = [...new Set([...[...pulls.selectedOptions].map(o => Number(o.value)), ...extra.map(Number)])];
      const issueNumbers = [...new Set(issueList.map(Number))];
      if (pullNumbers.length > 10 || issueNumbers.length > 10) return fail('PR과 Issue는 각각 10개까지 연결할 수 있습니다.', pulls);
      if (meeting.value && !/^https:\/\/\S+$/.test(meeting.value.trim())) return fail('화상회의 URL은 https://로 시작해야 합니다.', meeting);
      const files = [];
      for (const row of attachmentList.children) {
        const [n, u] = row.querySelectorAll('input');
        if (!n.value.trim() && !u.value.trim()) continue;
        if (!n.value.trim() || !/^https:\/\/\S+$/.test(u.value.trim())) return fail('첨부는 이름과 https:// 링크를 함께 적으세요.', n.value.trim() ? u : n);
        files.push({ name: n.value.trim(), url: u.value.trim() });
      }
      const untilDate = recurrence.value !== 'none' && until.value ? addDays(fromKey(until.value), 1) : null;
      if (untilDate && untilDate <= s) return fail('반복 종료일은 시작일 이후여야 합니다.', until);
      if (untilDate) untilDate.setTime(untilDate.getTime() - 60000); // 종료일 당일 23:59까지
      const body = {
        title: title.value.trim(), description: description.value, event_type: type.value, status: status.value,
        starts_at: offsetIso(s), ends_at: offsetIso(e), all_day: allDay.checked,
        assignee_id: assigneeSelect.value || null, participant_ids: [...people.querySelectorAll('input:checked')].map(b => b.value),
        location: location.value.trim() || null, meeting_url: meeting.value.trim() || null,
        visibility: visibility.querySelector('input:checked')?.value || 'community',
        reminder_minutes: reminderSelect.value === '' ? null : Number(reminderSelect.value),
        recurrence: recurrence.value, recurrence_until: untilDate ? offsetIso(untilDate) : null,
        sprint_id: sprint.value ? Number(sprint.value) : null, milestone_id: milestone.value ? Number(milestone.value) : null,
        post_id: v?.post_id ?? null, issue_numbers: issueNumbers, pull_numbers: pullNumbers, release_tag: release.value || null,
        goal: !goalWrap.hidden && goal.value.trim() ? goal.value.trim() : null,
        version: !versionWrap.hidden && version.value.trim() ? version.value.trim() : null,
        environment: !envWrap.hidden && environment.value.trim() ? environment.value.trim() : null, attachments: files,
      };
      submit.disabled = true;
      try {
        if (editing) await api('PATCH', `/api/community/v1/repositories/${currentRepo}/events/${detail.id}`, body);
        else await api('POST', `/api/community/v1/repositories/${currentRepo}/events`, body);
        close();
        state.selected = startOfDay(s);
        if (state.mode === 'month' ? state.selected.getMonth() !== state.cursor.getMonth() || state.selected.getFullYear() !== state.cursor.getFullYear() : true) state.cursor = state.selected;
        await refreshAfter();
      } catch (error) { fail(error.message); submit.disabled = false; }
    });
  }

  void load();
  void loadLinks().then(() => paint()).catch(() => {});
}
