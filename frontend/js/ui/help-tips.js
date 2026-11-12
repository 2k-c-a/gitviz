// 작은 안내 문구(설명 문장)를 숨기고 그 자리(또는 바로 앞 제목)에 '?' 버튼을 둔다. 버튼을 누르면 문구가 말풍선으로 열린다.
// 화면 코드는 바꾸지 않는다: 원래 요소는 DOM에 그대로 두고(display만 숨김) 열 때마다 현재 글자를 읽는다.
// 빈 상태·오류·진행 문구, id·aria-live가 있는 상태 문구, 작성자·날짜 같은 메타데이터(문장으로 끝나지 않음)는 그대로 보인다.
const NOTE = '.ws-note, .ai-muted, .p-sub, .p-note, .dash-sub, .dd-note, .ctx-note, .leg-note, .myproj-github-note, .popover-note';
const SKIP_INSIDE = 'details, table, button, a, label, summary, nav, select, [role="status"], [role="alert"], [aria-live], .app-sidebar, .app-statusbar, .ui-tooltip, .help-pop, [contenteditable]';
const INLINE = new Set(['STRONG', 'B', 'EM', 'I', 'CODE', 'BR', 'SPAN', 'KBD']);
const HEADING = 'h1, h2, h3, h4, h5, h6, legend, strong, b, .ins-label, .ui-panel-title';
const SENTENCE = /(다|요)[.!]?\)?$/;
const STATUS = /없습니다|않았습니다|못했습니다|실패|오류|중입니다|중…|불러오는|기다리/;
const MAX_FONT_PX = 13;
const done = new WeakSet();
const textOf = (node) => node.textContent.replace(/\s+/g, ' ').trim();

function eligible(node) {
  if (done.has(node) || node.id || !['P', 'DIV', 'SPAN', 'SMALL'].includes(node.tagName)) return false;
  if (node.closest(SKIP_INSIDE)) return false;
  if ([...node.querySelectorAll('*')].some((child) => !INLINE.has(child.tagName) || child.matches(NOTE))) return false;
  const text = textOf(node);
  if (text.length < 14 || !SENTENCE.test(text) || STATUS.test(text)) return false;
  return parseFloat(getComputedStyle(node).fontSize) <= MAX_FONT_PX;
}

// '?'를 둘 곳: 바로 앞 형제가 제목이면 그 제목 끝, 제목 한 개를 가진 작은 줄이면 그 제목 끝, 아니면 문구 자리.
function anchorFor(node) {
  let prev = node.previousElementSibling;
  while (prev?.tagName === 'BR') prev = prev.previousElementSibling;
  if (!prev || prev.hidden) return null;
  if (prev.matches(HEADING)) return prev;
  if (prev.children.length <= 3) return prev.querySelector(':scope > h1, :scope > h2, :scope > h3, :scope > h4');
  return null;
}

let pop = null; let openButton = null;
function closePop() {
  openButton?.setAttribute('aria-expanded', 'false');
  pop?.remove(); pop = null; openButton = null;
}
function openPop(button) {
  if (openButton === button) { closePop(); return; }
  closePop();
  const texts = button.helpTargets.filter((t) => t.isConnected).map(textOf).filter(Boolean);
  if (!texts.length) return;
  pop = document.createElement('div');
  pop.className = 'help-pop'; pop.id = 'help-pop'; pop.setAttribute('role', 'note');
  for (const text of texts) { const p = document.createElement('p'); p.textContent = text; pop.append(p); }
  document.body.append(pop);
  openButton = button; button.setAttribute('aria-expanded', 'true'); button.setAttribute('aria-controls', pop.id);
  const rect = button.getBoundingClientRect();
  pop.style.left = `${Math.max(12, Math.min(rect.left - 8, window.innerWidth - pop.offsetWidth - 12))}px`;
  const below = rect.bottom + 6;
  pop.style.top = `${below + pop.offsetHeight > window.innerHeight - 12 ? Math.max(12, rect.top - pop.offsetHeight - 6) : below}px`;
}

function makeButton() {
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'help-tip'; button.textContent = '?';
  button.setAttribute('aria-label', '설명 보기'); button.setAttribute('aria-expanded', 'false');
  button.helpTargets = [];
  button.addEventListener('click', (event) => { event.preventDefault(); event.stopPropagation(); openPop(button); });
  return button;
}

const buttons = new Set();
function attach(node) {
  done.add(node);
  node.classList.add('help-hidden');
  const anchor = anchorFor(node);
  let button = anchor && [...anchor.children].find((c) => c.classList.contains('help-tip'));
  if (!button) {
    button = makeButton();
    if (anchor) anchor.append(' ', button); else node.before(button);
    buttons.add(button);
  }
  button.helpTargets.push(node);
  node.helpButton = button;
}

function sync() {
  for (const node of document.querySelectorAll(NOTE)) {
    if (done.has(node)) {
      // 화면이 제목을 다시 그려 버튼이 사라졌으면 다시 단다.
      if (!node.helpButton?.isConnected && node.isConnected) { done.delete(node); node.helpButton?.helpTargets.splice(0); if (eligible(node)) attach(node); else node.classList.remove('help-hidden'); }
      continue;
    }
    if (eligible(node)) attach(node);
  }
  for (const button of buttons) {
    button.helpTargets = button.helpTargets.filter((t) => t.isConnected);
    if (!button.helpTargets.length || !button.isConnected) { if (openButton === button) closePop(); button.remove(); buttons.delete(button); continue; }
    button.hidden = button.helpTargets.every((t) => t.hidden || t.closest('[hidden]'));
  }
}

export function enhanceHelpTips(root = document.body) {
  let queued = false;
  const schedule = () => { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; sync(); }); } };
  new MutationObserver(schedule).observe(root, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['hidden'] });
  document.addEventListener('click', (event) => { if (pop && !pop.contains(event.target)) closePop(); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && pop) { const b = openButton; closePop(); b?.focus(); } });
  window.addEventListener('resize', closePop);
  document.addEventListener('scroll', (event) => { if (pop && !pop.contains(event.target)) closePop(); }, true);
  sync();
}
