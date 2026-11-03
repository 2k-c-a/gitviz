/* Page motion, powered by Motion (https://motion.dev — the animation engine behind Framer Motion), vendored under
   vendor/motion-14.9.0. Pure enhancement: if the library cannot load or the user prefers reduced motion, nothing here runs
   and the page is complete without it. Cards and panels fade up as they appear, metric numbers count up, and dialogs,
   menus and toasts spring open. The element appearing in the DOM is what triggers it, so existing render code is untouched. */

const VENDOR = new URL('../../vendor/motion-14.9.0/motion.js', import.meta.url).href;
const REDUCED = typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : { matches: true };
const EASE_OUT = [0.2, 0.8, 0.2, 1];

/* Cards that rise into place. Things inside dialogs or marked data-motion="off" (polled, constantly re-rendered) stay still. */
const ENTER = '.panel, .metric-card, .ui-panel, .ui-state, .repo-item, .search-card, .community-list-item, .ctx-banner, .ws-panel, .ws-todo, .landing-card, .dash-head';
const SKIP = '[data-motion="off"], .modal-overlay, dialog, .toast';
/* Containers that open and close by toggling a class, style or hidden. [selector, how the open state is read, scale origin] */
const POPUPS = [
  ['.modal-overlay', el => el.classList.contains('open')],
  ['.toast', el => el.classList.contains('open')],
  ['.popover', el => el.classList.contains('open')],
  ['#user-dropdown', el => el.style.display !== 'none'],
  ['.ws-menu', el => !el.hidden],
  ['.ws-palette-backdrop', () => true],
];
const POPUP_SELECTOR = POPUPS.map(([selector]) => selector).join(',');

let motionPromise;
function loadMotion() {
  if (globalThis.Motion) return Promise.resolve(globalThis.Motion);
  motionPromise ||= new Promise(resolve => {
    const script = document.createElement('script');
    script.src = VENDOR;
    script.onload = () => resolve(globalThis.Motion || null);
    script.onerror = () => resolve(null);
    document.head.append(script);
  });
  return motionPromise;
}

/* Animations leave their end state in inline style; clear it so the stylesheet is in charge again afterwards. */
function release(animation, element, properties) {
  // Motion commits the end state after the finished promise settles, so clear one frame later.
  const done = () => requestAnimationFrame(() => { for (const name of properties) element.style.removeProperty(name); });
  try { (animation.finished ?? animation).then(done, done); } catch { done(); }
}

function onceVisible(element, motion, run) {
  // The observer root must contain the element, or it never reports: outside .app-main the viewport is the root.
  const root = element.closest('.app-main');
  const stop = motion.inView(element, () => { stop(); run(); }, { root: root || undefined, amount: 0.1 });
}

function setup(motion) {
  const { animate } = motion;
  const seen = new WeakSet();
  let pending = [];
  let scheduled = false;

  const rise = (element, index) => {
    const show = () => { element.style.removeProperty('opacity'); element.style.removeProperty('transform'); };
    const play = () => {
      try {
        const animation = animate(element, { opacity: [0, 1], transform: ['translateY(12px)', 'translateY(0px)'] },
          { duration: 0.45, delay: Math.min(index, 9) * 0.045, ease: EASE_OUT });
        release(animation, element, ['opacity', 'transform']);
      } catch { show(); }
    };
    // Content must never stay hidden because an animation failed or a scroll callback never came: the timer is the
    // backstop, for cards below the fold too. Revealing a card re-arms it for the animation that then plays.
    let timer;
    const backstop = () => { clearTimeout(timer); timer = setTimeout(show, 2500); };
    const rect = element.getBoundingClientRect();
    element.style.opacity = '0'; // Hold the start state through the stagger delay.
    if (rect.top > innerHeight - 24) {
      try { onceVisible(element, motion, () => { show(); play(); backstop(); }); } catch { show(); return; }
    } else {
      play();
    }
    backstop();
  };

  const flush = () => {
    scheduled = false;
    const batch = pending.filter(el => el.isConnected && !seen.has(el)); pending = [];
    // One entrance per visual block: a card inside an entering card rides along with its parent.
    const blocks = batch.filter(el => !batch.some(other => other !== el && other.contains(el)));
    let index = 0;
    for (const element of blocks) {
      if (!element.getClientRects().length || element.closest(SKIP)) continue;
      seen.add(element);
      if (index < 24) rise(element, index++);
    }
  };

  const queue = element => {
    pending.push(element);
    if (!scheduled) { scheduled = true; queueMicrotask(flush); }
  };

  const scan = node => {
    if (node.matches?.(ENTER)) queue(node);
    for (const match of node.querySelectorAll?.(ENTER) ?? []) queue(match);
  };

  /* Metric numbers: "—" → 1,284 counts up once. The final text is always the exact original string, unless the page
     writes a new value into the element while it counts: then the count stops and the page's value stays. */
  const counted = new WeakSet();
  const countUp = element => {
    if (counted.has(element)) return;
    const text = element.textContent.trim();
    const match = /^([^\d-]*)(-?[\d,]+(?:\.\d+)?)(.*)$/.exec(text);
    if (!match) return;
    const [, prefix, number, suffix] = match;
    const target = Number(number.replaceAll(',', ''));
    if (!Number.isFinite(target) || Math.abs(target) < 10) return;
    counted.add(element);
    const decimals = number.includes('.') ? number.split('.')[1].length : 0;
    const grouped = number.includes(',');
    const format = value => {
      const fixed = value.toFixed(decimals);
      return prefix + (grouped ? Number(fixed).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) : fixed) + suffix;
    };
    // `written` is what the count last put there; any other text means the page replaced the value mid-count.
    let written = element.textContent;
    let cancelled = false;
    let controls;
    const ours = () => !cancelled && element.textContent === written;
    const cancel = () => { cancelled = true; try { controls?.stop?.(); } catch { /* the flag already ends the count */ } };
    controls = animate(0, target, {
      duration: 0.9,
      ease: EASE_OUT,
      onUpdate: value => {
        if (!ours()) { cancel(); return; }
        written = format(value);
        element.textContent = written;
      },
      onComplete: () => { if (ours()) element.textContent = text; },
    });
  };
  const scanCounters = node => {
    if (node.matches?.('.m-val, .bus-num')) countUp(node);
    for (const match of node.querySelectorAll?.('.m-val, .bus-num') ?? []) countUp(match);
  };

  /* Popups: spring the panel in when the container turns visible. */
  const state = new WeakMap();
  const popup = element => {
    if (!element.matches(POPUP_SELECTOR)) return; // Most class/style changes on a page are not popups: one check, no list walk.
    const entry = POPUPS.find(([selector]) => element.matches(selector));
    if (!entry) return;
    const open = Boolean(entry[1](element));
    const was = state.get(element) ?? false;
    state.set(element, open);
    if (!open || was) return;
    const panel = element.matches('.modal-overlay, .ws-palette-backdrop') ? element.querySelector('.modal, .ws-palette') : element;
    if (element.matches('.modal-overlay, .ws-palette-backdrop')) release(animate(element, { opacity: [0, 1] }, { duration: 0.18 }), element, ['opacity']);
    if (panel) release(animate(panel, { opacity: [0, 1], transform: ['scale(0.96) translateY(8px)', 'scale(1) translateY(0px)'] },
      { type: 'spring', stiffness: 520, damping: 34, mass: 0.9 }), panel, ['opacity', 'transform']);
  };

  const observer = new MutationObserver(records => {
    const toggled = new Set(); // Charts and animations restyle the same element many times per batch; look at it once.
    for (const record of records) {
      if (record.type === 'attributes') { toggled.add(record.target); continue; }
      if (record.type === 'characterData') { const el = record.target.parentElement; if (el?.matches('.m-val, .bus-num')) countUp(el); continue; }
      if (record.target.matches?.('.m-val, .bus-num')) countUp(record.target);
      for (const node of record.addedNodes) if (node.nodeType === 1) { scan(node); scanCounters(node); }
    }
    for (const element of toggled) popup(element);
  });
  observer.observe(document.body, {
    childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['class', 'style', 'hidden'],
  });
  scan(document.body); scanCounters(document.body);
  for (const [selector] of POPUPS) for (const element of document.querySelectorAll(selector)) popup(element);
}

if (typeof document !== 'undefined' && !REDUCED.matches) {
  const start = () => loadMotion().then(motion => { if (motion && !REDUCED.matches) setup(motion); });
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start, { once: true });
}
