/* Small DOM primitives. Text is always assigned as textContent, never interpreted as markup. */
export function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function createIcon(symbol) {
  const paths = {
    '⌕': 'M10.5 18a7.5 7.5 0 1 0 0-15 7.5 7.5 0 0 0 0 15Zm5.5-2 5 5',
    '▦': 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z',
    '↗': 'M5 19 19 5M8 5h11v11',
    '▥': 'M4 20V5m0 15h17M9 16v-5m5 5V7m5 9v-8',
    '◎': 'M7 7h10M6 9l5 9M18 9l-5 9M7 5a2 2 0 1 0 0 .1M19 5a2 2 0 1 0 0 .1M13 20a2 2 0 1 0 0 .1',
    '▤': 'M5 3h9l5 5v13H5zM14 3v6h5M8 13h8M8 17h6',
    '◷': 'M4 7a9 9 0 1 1-1 8M3 3v5h5M12 7v5l4 2',
    '◇': 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z',
    '▣': 'M3 6h7l2 2h9v12H3zM3 6V4h7l2 2',
    '⚙': 'M9 3h6l1 4 4 1v8l-4 1-1 4H9l-1-4-4-1V8l4-1zM15 12a3 3 0 1 0-6 0 3 3 0 0 0 6 0',
    '·': 'M8 12h8',
  };
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [key, value] of Object.entries({ viewBox: '0 0 24 24', width: '19', height: '19', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.7', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' })) svg.setAttribute(key, value);
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', paths[symbol] || paths['·']); svg.append(path); return svg;
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
