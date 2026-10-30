/** Information architecture of the local workspace: views, project tabs, guided review steps and shortcuts.
 * Presentation only. Every action still runs through the original module handlers and element ids;
 * this module never calls the agent or the network and never executes or approves anything.
 */
import { readWorkspaceEntry } from '/web/js/platform/workspace-navigation.js';

const $ = id => document.getElementById(id);
const main = document.querySelector('main');
const picker = $('project-picker');
const TABS = [['overview', '개요'], ['changes', '변경'], ['ai', 'AI 점검'], ['proposals', '개선 제안'], ['tests', '테스트·린트'], ['compare', '전후 비교'], ['impact', '영향 범위']];
const TAB_IDS = TABS.map(([id]) => id);
const VIEW_TITLE = { start: '시작하기', projects: '프로젝트', inbox: '알림', account: '계정·연결', settings: '설정' };
const ui = { view: 'projects', tab: 'overview', auto: true, projectId: '', listed: false };
const data = { git: null, current: null, ids: new Set(), recovery: 0, review: 'idle', step: 1 };

function node(tag, text, className) { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; if (className) element.className = className; return element; }
const hasProjects = () => [...picker.options].some(option => option.value);
const projectName = () => picker.value ? picker.selectedOptions[0]?.textContent ?? '' : '';

function updateTitle() {
  if (ui.view !== 'project') { document.title = `${VIEW_TITLE[ui.view]} · GitViz Workspace`; return; }
  const branch = data.git ? (data.git.detached ? 'detached HEAD' : data.git.branch) : '';
  document.title = `${projectName()}${branch ? ` — ${branch}` : ''} · GitViz Workspace`;
}

export function show(view, tab = ui.tab, { focus = false, user = true } = {}) {
  if (user) ui.auto = false;
  if (view === 'project' && !picker.value) view = hasProjects() ? 'projects' : 'start';
  ui.view = view;
  if (view === 'project' && TAB_IDS.includes(tab)) ui.tab = tab;
  for (const section of main.querySelectorAll(':scope > [data-view]')) section.hidden = section.dataset.view !== view;
  for (const panel of main.querySelectorAll('[data-tab-panel]')) panel.hidden = panel.dataset.tabPanel !== ui.tab;
  for (const button of main.querySelectorAll('[data-tab]')) {
    const on = button.dataset.tab === ui.tab; button.setAttribute('aria-selected', String(on)); button.tabIndex = on ? 0 : -1;
  }
  for (const link of document.querySelectorAll('[data-ws-view]')) {
    const name = link.dataset.wsView;
    if (name === view || (name === 'projects' && (view === 'project' || view === 'start'))) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  }
  updateTitle(); main.scrollTop = 0;
  if (focus) (view === 'project' ? main.querySelector(`[data-tab-panel="${ui.tab}"]`) : main.querySelector(`[data-view="${view}"]`))?.focus({ preventScroll: true });
  window.dispatchEvent(new CustomEvent('workspace-view-changed', { detail: { view, tab: ui.tab } }));
}

/* ---------- project list and header ---------- */
function choose(projectId) {
  picker.value = projectId; picker.dispatchEvent(new Event('change'));
  show('project', 'overview', { focus: true });
}
function renderProjects() {
  const list = $('ws-project-list'); list.replaceChildren();
  const options = [...picker.options].filter(option => option.value);
  $('ws-project-empty').hidden = !ui.listed || options.length > 0;
  for (const option of options) {
    const item = node('li'); const open = node('button', '열기'); open.type = 'button';
    open.setAttribute('aria-label', `${option.textContent} 열기`);
    open.addEventListener('click', () => choose(option.value));
    item.append(node('strong', option.textContent), node('span', option.value === picker.value ? '현재 프로젝트' : '', 'ws-note'), node('span', '', 'ws-grow'), open);
    list.append(item);
  }
}
function chips() {
  const git = data.git;
  const set = (id, text, tone) => { const element = $(id); element.hidden = !text; element.textContent = text; if (tone) element.dataset.tone = tone; else delete element.dataset.tone; };
  if (!git) { set('ws-chip-branch', ''); set('ws-chip-changes', ''); set('ws-chip-conflicts', ''); return; }
  const changes = git.staged.length + git.unstaged.length + git.untracked.length;
  set('ws-chip-branch', git.unborn ? `초기 커밋 없음 · ${git.branch ?? ''}` : git.detached ? `detached HEAD · ${String(git.head).slice(0, 8)}` : git.branch);
  set('ws-chip-changes', `변경 ${changes}`, changes ? 'warn' : 'ok');
  set('ws-chip-conflicts', `충돌 ${git.conflicts.length}`, git.conflicts.length ? 'error' : 'ok');
}
function syncProjectLabels() { $('ws-connect-project').textContent = projectName() || '선택 안 됨'; }

/* ---------- "what to do next" ---------- */
function todo() {
  const git = data.git; let item;
  const changes = git ? git.staged.length + git.unstaged.length + git.untracked.length : 0;
  const error = $('project-message').textContent.trim();
  if (!ui.projectId) item = ['프로젝트를 선택하세요.', '프로젝트 목록에서 하나를 고르거나 새 폴더를 등록하세요.', '프로젝트 목록', () => show('projects', undefined, { focus: true })];
  else if (data.recovery) item = ['복구가 필요한 제안이 있습니다.', '파일이 일부만 바뀌었을 수 있습니다. 먼저 복구하세요.', '복구 목록 보기', () => show('project', 'proposals', { focus: true })];
  else if (!git && error) item = ['프로젝트 정보를 읽지 못했습니다.', error, '다시 시도', () => picker.dispatchEvent(new Event('change'))];
  else if (!git) item = ['Git 상태를 읽는 중입니다.', '잠시 기다려 주세요.', null];
  else if (data.review === 'result') item = ['AI 점검 결과가 있습니다.', '지적을 확인하고 맞는지 직접 판정하세요.', '결과 보기', () => { setStep(4); show('project', 'ai', { focus: true }); }];
  else if (data.current) item = ['점검 대상이 준비됐습니다.', 'AI 점검을 시작하거나 테스트·린트를 실행해 보세요.', 'AI 점검 시작', () => show('project', 'ai', { focus: true })];
  else if (changes) item = [`변경 ${changes}개가 있습니다.`, '점검할 파일을 고르세요.', '변경 보기', () => show('project', 'changes', { focus: true })];
  else item = ['변경이 없습니다.', '파일을 수정한 뒤 새로고침하세요.', '새로고침', () => picker.dispatchEvent(new Event('change'))];
  $('ws-todo-title').textContent = item[0]; $('ws-todo-text').textContent = item[1];
  const action = $('ws-todo-action'); action.hidden = !item[2]; action.textContent = item[2] ? `${item[2]} →` : '';
  action.onclick = item[3];
}
function syncState() {
  main.dataset.hasSnapshot = String(!!data.current);
  main.dataset.snapshotCount = String(Math.min(2, data.ids.size));
  const proposals = document.querySelector('#ws-tab-proposals');
  proposals.dataset.alert = data.recovery ? 'true' : 'false'; proposals.textContent = data.recovery ? '개선 제안 ⚠' : '개선 제안';
  $('ws-proposal-empty').hidden = !!$('proposal-view').childElementCount || data.recovery > 0;
  const snapshot = data.current;
  $('ws-ai-target').textContent = snapshot ? `담은 변경: 파일 ${snapshot.coverage.selected}개 선택 · ${snapshot.coverage.read}개 읽음 · ${snapshot.coverage.excluded}개 제외 (${snapshot.capturedAt})` : '담은 변경이 없습니다. 변경 탭에서 먼저 담으세요.';
  $('ws-ai-next-1').disabled = !snapshot;
  chips(); todo(); stepper();
}

/* ---------- guided AI review steps ---------- */
function stepper() {
  const hasBytes = $('review-preview-bytes').textContent.trim() !== '';
  for (const panel of main.querySelectorAll('[data-step]')) panel.hidden = Number(panel.dataset.step) !== data.step;
  for (const button of main.querySelectorAll('[data-step-button]')) {
    const step = Number(button.dataset.stepButton);
    if (step === data.step) button.setAttribute('aria-current', 'step'); else button.removeAttribute('aria-current');
    button.disabled = (step === 2 && !data.current) || (step === 3 && !hasBytes);
  }
}
function setStep(step) { data.step = step; stepper(); }
function syncReviewFields() {
  main.dataset.cloud = String($('review-profile').value !== 'ollama-local-v1');
  main.dataset.dup = String(Number($('review-generation').value) > 1);
}

/* ---------- decorations that need no change to the producing modules ---------- */
const RISKS = [[/승인한 제안 적용|원본 쓰기 별도 승인|원복 범위/, 'write'], [/계획 검토.*실행/, 'exec']];
function tagRisks() {
  for (const button of main.querySelectorAll('#proposal-view button, #validation-plans button')) {
    if (button.dataset.risk) continue;
    const match = RISKS.find(([pattern]) => pattern.test(button.textContent));
    if (match) button.dataset.risk = match[1];
  }
}
function tagEvidence() {
  for (const paragraph of $('impact-view').querySelectorAll(':scope > p')) {
    const text = paragraph.textContent;
    const kind = text.startsWith('AI 추론') ? 'ai' : text.startsWith('과거 공동 변경') ? 'history' : /정적 바인딩|미해결 참조/.test(text) ? 'static' : '';
    if (kind) paragraph.dataset.evidence = kind;
  }
}

/* ---------- command palette ---------- */
function commands() {
  const out = [];
  if (ui.projectId) for (const [id, label] of TABS) out.push([`탭 이동: ${label}`, () => show('project', id, { focus: true })]);
  out.push(['프로젝트 목록', () => show('projects', undefined, { focus: true })], ['알림', () => show('inbox', undefined, { focus: true })],
    ['계정·연결', () => show('account', undefined, { focus: true })], ['설정', () => show('settings', undefined, { focus: true })],
    ['프로젝트 폴더 등록', () => $('register-project').click()]);
  for (const option of picker.options) if (option.value) out.push([`프로젝트 전환: ${option.textContent}`, () => choose(option.value)]);
  for (const [value, label] of [['system', '시스템'], ['light', '라이트'], ['dark', '다크']]) out.push([`테마: ${label}`, () => { const select = $('shell-theme'); if (select) { select.value = value; select.dispatchEvent(new Event('change')); } }]);
  return out;
}
let palette = null;
function closePalette() { if (!palette) return; palette.root.remove(); palette.opener?.focus?.(); palette = null; }
function openPalette() {
  if (palette) { palette.input.focus(); return; }
  const root = node('div', undefined, 'ws-palette-backdrop');
  const box = node('div', undefined, 'ws-palette'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true'); box.setAttribute('aria-label', '명령 팔레트');
  const input = node('input'); input.type = 'text'; input.placeholder = '할 일 검색 (Enter: 실행, Esc: 닫기)'; input.setAttribute('aria-label', '명령 검색'); input.autocomplete = 'off';
  const list = node('ul'); list.setAttribute('role', 'listbox'); list.setAttribute('aria-label', '명령 목록');
  box.append(input, list); root.append(box); document.body.append(root);
  palette = { root, input, opener: document.activeElement, index: 0, items: [] };
  const render = () => {
    const query = input.value.trim().toLowerCase();
    palette.items = commands().filter(([label]) => label.toLowerCase().includes(query));
    palette.index = Math.min(palette.index, Math.max(0, palette.items.length - 1));
    list.replaceChildren();
    palette.items.forEach(([label, run], index) => {
      const item = node('li', label); item.setAttribute('role', 'option'); item.setAttribute('aria-selected', String(index === palette.index));
      item.addEventListener('click', () => { closePalette(); run(); }); list.append(item);
    });
    if (!palette.items.length) list.append(node('li', '일치하는 명령이 없습니다.', 'ws-note'));
  };
  input.addEventListener('input', () => { palette.index = 0; render(); });
  input.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown') { palette.index = Math.min(palette.items.length - 1, palette.index + 1); render(); event.preventDefault(); }
    else if (event.key === 'ArrowUp') { palette.index = Math.max(0, palette.index - 1); render(); event.preventDefault(); }
    else if (event.key === 'Enter') { const chosen = palette.items[palette.index]; if (chosen) { closePalette(); chosen[1](); } event.preventDefault(); }
    else if (event.key === 'Escape') { closePalette(); event.preventDefault(); }
    else if (event.key === 'Tab') event.preventDefault(); // Keep focus inside the dialog.
  });
  root.addEventListener('pointerdown', event => { if (event.target === root) closePalette(); });
  render(); input.focus();
}

/* ---------- events ---------- */
window.addEventListener('workspace-navigate', event => show(event.detail.view, undefined, { focus: true }));
document.addEventListener('click', event => {
  const view = event.target.closest?.('[data-go-view]'); if (view) { show(view.dataset.goView, undefined, { focus: true }); return; }
  const tab = event.target.closest?.('[data-go-tab]'); if (tab) { show('project', tab.dataset.goTab, { focus: true }); return; }
  const tabButton = event.target.closest?.('[data-tab]'); if (tabButton) { show('project', tabButton.dataset.tab); return; }
  const stepButton = event.target.closest?.('[data-step-button]'); if (stepButton) { setStep(Number(stepButton.dataset.stepButton)); return; }
  const go = event.target.closest?.('[data-step-go]'); if (go) setStep(Number(go.dataset.stepGo));
});
$('ws-start-register').addEventListener('click', () => $('register-project').click());
$('ws-health-retry').addEventListener('click', () => $('refresh').click());
$('ws-health-diagnostics').addEventListener('click', () => $('export-diagnostics').click());
main.querySelector('.ws-tabs').addEventListener('keydown', event => {
  const index = TAB_IDS.indexOf(ui.tab);
  const next = { ArrowRight: (index + 1) % TAB_IDS.length, ArrowLeft: (index + TAB_IDS.length - 1) % TAB_IDS.length, Home: 0, End: TAB_IDS.length - 1 }[event.key];
  if (next === undefined) return;
  event.preventDefault(); show('project', TAB_IDS[next]); $(`ws-tab-${TAB_IDS[next]}`).focus();
});
document.addEventListener('keydown', event => {
  if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
  const key = event.key.toLowerCase();
  if (key === 'k') { event.preventDefault(); openPalette(); }
  else if (key === 'o') { event.preventDefault(); $('register-project').click(); }
  else if (/^[1-7]$/.test(key) && picker.value) { event.preventDefault(); show('project', TAB_IDS[Number(key) - 1], { focus: true }); }
});
window.addEventListener('workspace-project-changed', event => {
  const id = event.detail?.projectId ?? ''; const changed = id !== ui.projectId; ui.projectId = id;
  if (changed) { data.git = null; data.current = null; data.ids.clear(); data.recovery = 0; data.review = 'idle'; data.step = 1; }
  renderProjects(); syncProjectLabels(); syncState();
  if (!id) { if (ui.view === 'project') show(hasProjects() ? 'projects' : 'start', undefined, { user: false }); }
  else if (changed) show('project', 'overview');
});
window.addEventListener('workspace-git-status', event => { if (event.detail.projectId === picker.value) { data.git = event.detail.state; updateTitle(); syncState(); } });
window.addEventListener('workspace-snapshot-changed', event => {
  data.current = event.detail; if (event.detail) data.ids.add(event.detail.snapshotId);
  data.review = 'idle'; data.step = 1; syncState();
});
window.addEventListener('workspace-open-target', event => {
  const tab = { review: 'ai', validation: 'tests', proposal: 'proposals' }[event.detail.kind];
  if (event.detail.kind === 'review') data.step = 4;
  if (tab) show('project', tab, { focus: false });
  stepper();
});
$('review-profile').addEventListener('change', syncReviewFields);
$('review-generation').addEventListener('input', syncReviewFields);
picker.addEventListener('change', () => { syncProjectLabels(); updateTitle(); });

const watch = (target, callback, options = { childList: true, subtree: true, characterData: true }) => new MutationObserver(callback).observe(target, options);
watch(picker, () => {
  ui.listed = true; renderProjects(); syncProjectLabels();
  if (ui.auto && (ui.view === 'projects' || ui.view === 'start')) show(hasProjects() ? 'projects' : 'start', undefined, { user: false });
}, { childList: true });
watch($('review-preview-bytes'), () => {
  const hasBytes = $('review-preview-bytes').textContent.trim() !== '';
  if (hasBytes) { data.review = 'previewed'; data.step = 3; } else if (data.step === 3) data.step = 2;
  stepper();
});
watch($('review-result'), () => {
  const has = $('review-result').childElementCount > 0;
  if (has) { data.review = 'result'; data.step = 4; } else if (data.review === 'result') data.review = 'idle';
  syncState();
}, { childList: true });
watch($('proposal-recovery-list'), () => { data.recovery = $('proposal-recovery-list').querySelectorAll('button').length; syncState(); tagRisks(); }, { childList: true });
watch($('proposal-view'), () => { tagRisks(); syncState(); }, { childList: true });
watch($('validation-plans'), tagRisks, { childList: true });
watch($('impact-view'), tagEvidence, { childList: true });
watch($('project-message'), todo);
watch($('desktop-account'), () => {
  const text = $('desktop-account').textContent;
  $('ws-account-chip')?.replaceChildren(document.createTextNode(text.startsWith('원격 계정: ') ? text.slice('원격 계정: '.length) : '로그인하지 않음'));
});
watch($('inbox-unread'), () => {
  const count = Number($('inbox-unread').textContent.match(/\d+/)?.[0] ?? 0);
  for (const link of document.querySelectorAll('[data-ws-view="inbox"]')) {
    let badge = link.querySelector('.ws-badge');
    if (!count) { badge?.remove(); continue; }
    if (!badge) { badge = node('span', undefined, 'ws-badge'); link.append(badge); }
    badge.textContent = String(count); badge.setAttribute('aria-label', `읽지 않은 알림 ${count}건`);
  }
});
watch($('status'), () => {
  const text = $('status').textContent;
  const bad = /준비할 수 없습니다|복구해야|확인할 수 없습니다/.test(text);
  $('ws-health').hidden = !bad; $('ws-health-text').textContent = bad ? text : '';
});

const entry = readWorkspaceEntry(location.href);
syncReviewFields(); renderProjects(); syncProjectLabels(); syncState();
show(entry ? 'inbox' : 'projects', 'overview', { user: !!entry });
