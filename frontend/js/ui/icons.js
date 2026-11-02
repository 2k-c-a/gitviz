/* Lucide icons as inline SVG. Static markup and generated templates use <span data-icon="name"></span>;
   scripts call lucideIcon(). Nothing is fetched, so this works under the desktop CSP (no remote assets). */
import { ICONS } from './icon-data.js';

const NS = 'http://www.w3.org/2000/svg';

/* The text glyphs older code passes to createIcon() map onto Lucide names. */
export const GLYPH_ICONS = {
  '⌕': 'search', '▦': 'layout-dashboard', '↗': 'link-2', '▥': 'chart-no-axes-column', '◎': 'share-2', '▤': 'file-code',
  '◷': 'history', '◇': 'sparkles', '☷': 'users', '▣': 'folder-git-2', '⚙': 'settings', '◔': 'bell', '◉': 'circle-user',
  '☀': 'sun', '☾': 'moon', '☰': 'menu', '×': 'x', '·': 'dot',
};

export function lucideIcon(name, { size = 18, strokeWidth = 1.75, className = '' } = {}) {
  const svg = document.createElementNS(NS, 'svg');
  for (const [key, value] of Object.entries({
    viewBox: '0 0 24 24', width: String(size), height: String(size), fill: 'none', stroke: 'currentColor',
    'stroke-width': String(strokeWidth), 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false',
  })) svg.setAttribute(key, value);
  svg.setAttribute('class', `lucide lucide-${name}${className ? ` ${className}` : ''}`);
  for (const [tag, attrs] of ICONS[name] || ICONS.dot) {
    const node = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    svg.append(node);
  }
  return svg;
}

/* Fills every empty [data-icon] placeholder under root. data-icon-size and data-icon-stroke are optional. */
export function hydrateIcons(root = document) {
  const nodes = [...root.querySelectorAll('[data-icon]:empty')];
  if (root.matches?.('[data-icon]:empty')) nodes.unshift(root);
  for (const node of nodes) {
    node.append(lucideIcon(node.dataset.icon, { size: Number(node.dataset.iconSize) || 16, strokeWidth: Number(node.dataset.iconStroke) || 1.75 }));
    node.classList.add('ui-icon'); node.setAttribute('aria-hidden', 'true');
  }
}

export function observeIcons(root = document.body) {
  hydrateIcons(root);
  new MutationObserver(records => {
    for (const record of records) for (const node of record.addedNodes) if (node.nodeType === 1) hydrateIcons(node);
  }).observe(root, { childList: true, subtree: true });
}

if (typeof document !== 'undefined') {
  const start = () => observeIcons(document.body);
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start, { once: true });
}
