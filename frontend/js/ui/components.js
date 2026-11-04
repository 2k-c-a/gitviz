/* Small DOM primitives. Text is always assigned as textContent, never interpreted as markup. */
import { lucideIcon, GLYPH_ICONS } from './icons.js';

export function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* Sets the text of the element with this id; a page that lacks the element is left alone. */
export function setText(id, text) {
  const node = document.getElementById(id);
  if (node) node.textContent = text;
}

/* Accepts a legacy text glyph (⌕ ▦ ...) or a Lucide icon name. */
export function createIcon(symbol, size = 19) {
  return lucideIcon(GLYPH_ICONS[symbol] || symbol, { size });
}

export function createState({ kind = 'empty', title, message, retry, retryLabel = '다시 시도' }) {
  const box = element('section', `ui-state ui-state-${kind}`);
  box.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  if (kind === 'loading') {
    box.setAttribute('aria-busy', 'true');
    const spinner = element('span', 'ui-spinner'); spinner.setAttribute('aria-hidden', 'true'); box.append(spinner);
  }
  const content = element('div');
  content.append(element('h2', '', title));
  if (message) content.append(element('p', '', message));
  if (kind === 'loading') {
    for (let i = 0; i < 2; i++) { const line = element('span', 'ui-skeleton'); line.setAttribute('aria-hidden', 'true'); content.append(line); }
  }
  if (retry) {
    const button = element('button', 'btn-secondary', retryLabel); button.type = 'button'; button.addEventListener('click', retry); content.append(button);
  }
  box.append(content); return box;
}

/* Applies to existing class-toggled overlays too, so callers retain their close/cancel semantics. */
export function enhanceOverlays(root = document) {
  const overlays = [...root.querySelectorAll('.modal-overlay')];
  const stack = [];
  const openers = new WeakMap();
  const inerted = new Map();
  const isModal = overlay => overlay.id !== 'drilldown-modal' || !window.matchMedia('(min-width: 1280px)').matches;
  const focusables = overlay => [...overlay.querySelectorAll('a[href], button, input, select, textarea, [tabindex]')]
    .filter(node => !node.disabled && node.tabIndex >= 0 && node.getClientRects().length && !node.closest('[hidden], [inert]'));
  const restoreInert = () => { for (const [node, previous] of inerted) node.inert = previous; inerted.clear(); };
  const sync = () => {
    restoreInert();
    for (const overlay of overlays) {
      const open = overlay.classList.contains('open');
      const index = stack.indexOf(overlay);
      if (open && index < 0) {
        openers.set(overlay, document.activeElement); stack.push(overlay);
        const heading = overlay.querySelector('.modal-title');
        if (heading) { heading.tabIndex = -1; heading.focus(); }
      } else if (!open && index >= 0) {
        stack.splice(index, 1);
        const opener = openers.get(overlay);
        if (opener?.isConnected) queueMicrotask(() => {
          // A user may already have focused another control while the close mutation settles.
          if (overlay.contains(document.activeElement) || document.activeElement === document.body) opener.focus();
        });
      }
      overlay.setAttribute('aria-modal', String(isModal(overlay)));
    }
    const detail = document.getElementById('drilldown-modal');
    document.querySelector('.app-shell')?.classList.toggle('has-detail', !!detail?.classList.contains('open'));
    const top = stack.at(-1);
    if (top && isModal(top)) {
      // Inert siblings all the way up, preserving any pre-existing inert state.
      let branch = top;
      while (branch.parentElement && branch !== document.body) {
        for (const sibling of branch.parentElement.children) {
          if (sibling !== branch && sibling instanceof HTMLElement && !['SCRIPT', 'STYLE'].includes(sibling.tagName)) {
            inerted.set(sibling, sibling.inert); sibling.inert = true;
          }
        }
        branch = branch.parentElement;
      }
    }
  };
  for (const overlay of overlays) {
    overlay.setAttribute('role', 'dialog');
    const heading = overlay.querySelector('.modal-title');
    if (heading) { heading.id ||= `${overlay.id}-title`; overlay.setAttribute('aria-labelledby', heading.id); }
    observeActions(overlay);
  }
  const observer = new MutationObserver(sync);
  overlays.forEach(overlay => observer.observe(overlay, { attributes: true, attributeFilter: ['class'] }));
  const media = window.matchMedia('(min-width: 1280px)'); media.addEventListener('change', sync);
  document.addEventListener('keydown', event => {
    const overlay = stack.at(-1);
    if (!overlay || document.querySelector('dialog[open]')) return;
    if (event.key === 'Escape') {
      const close = overlay.querySelector('[data-action="closeDrilldown"], [data-action="closeJobModal"], [data-action="closeLoginModal"], [data-action="closeIsolatedPanel"], [data-action="closeRemoveRepoModal"]');
      if (close) { event.preventDefault(); event.stopImmediatePropagation(); close.click(); }
    }
    if (event.key === 'Tab' && isModal(overlay)) {
      const items = focusables(overlay); const first = items[0]; const last = items.at(-1);
      if (!first) { event.preventDefault(); return; }
      if (!overlay.contains(document.activeElement) || document.activeElement.tabIndex < 0 || (event.shiftKey && document.activeElement === first) || (!event.shiftKey && document.activeElement === last)) {
        event.preventDefault(); (event.shiftKey ? last : first).focus();
      }
    }
  }, true);
  sync();
}

export function observeActions(root) {
  const upgrade = container => {
    const nodes = [...container.querySelectorAll('[data-action]')];
    if (container.matches?.('[data-action]')) nodes.unshift(container);
    for (const target of nodes) {
      if (target.matches('button, a[href], input, select, textarea')) continue;
      target.setAttribute('role', 'button'); target.tabIndex = 0;
    }
  };
  upgrade(root);
  new MutationObserver(records => {
    for (const record of records) for (const node of record.addedNodes) if (node.nodeType === 1) upgrade(node);
  }).observe(root, { childList: true, subtree: true });
}

export function enhanceTooltips() {
  let tip; let trigger;
  const hide = () => {
    if (trigger && tip) {
      const ids = (trigger.getAttribute('aria-describedby') || '').split(' ').filter(id => id && id !== tip.id);
      if (ids.length) trigger.setAttribute('aria-describedby', ids.join(' ')); else trigger.removeAttribute('aria-describedby');
    }
    tip?.remove(); tip = null; trigger = null;
  };
  const show = target => {
    hide(); if (!target) return;
    trigger = target; tip = element('div', 'ui-tooltip', target.dataset.tooltip); tip.id = 'ui-tooltip'; tip.setAttribute('role', 'tooltip');
    target.setAttribute('aria-describedby', `${target.getAttribute('aria-describedby') || ''} ${tip.id}`.trim());
    document.body.append(tip);
    const rect = target.getBoundingClientRect();
    tip.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - tip.offsetWidth - 12))}px`;
    tip.style.top = `${Math.max(12, Math.min(rect.bottom + 8, window.innerHeight - tip.offsetHeight - 12))}px`;
  };
  document.addEventListener('focusin', event => show(event.target.closest('[data-tooltip]')));
  document.addEventListener('focusout', hide);
  document.addEventListener('pointerover', event => { const target = event.target.closest('[data-tooltip]'); if (target && target !== trigger) show(target); });
  document.addEventListener('pointerout', event => {
    if (trigger && event.target.closest('[data-tooltip]') === trigger && !trigger.contains(event.relatedTarget) && document.activeElement !== trigger) hide();
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') hide(); });
  document.addEventListener('scroll', () => { if (trigger && document.activeElement === trigger) show(trigger); else hide(); }, true);
}
