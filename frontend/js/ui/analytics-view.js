import { createState, element } from './components.js';
import { chartColor } from './chart-theme.js';

export const valueText = (value, suffix = '') => value == null ? '—' : `${Number(value).toLocaleString('ko-KR')}${suffix}`;
export function action(label, callback, className = 'btn-secondary') {
  const button = element('button', className, label); button.type = 'button'; button.addEventListener('click', callback); return button;
}
const panelGenerations = new WeakMap();
const panelStatuses = new WeakMap();
export async function loadPanel(host, load, render) {
  if (!host) return;
  const generation = (panelGenerations.get(host) || 0) + 1; panelGenerations.set(host, generation);
  host.replaceChildren(createState({ kind: 'loading', title: '분석 결과를 불러오는 중' }));
  try { const data = await load(); if (generation !== panelGenerations.get(host)) return; host.replaceChildren(); render(data, host); }
  catch (error) {
    if (generation !== panelGenerations.get(host)) return;
    host.replaceChildren(createState({ kind: 'error', title: '이 영역을 불러오지 못했습니다', message: error.message, retry: () => loadPanel(host, load, render) }));
  }
}
export function empty(host, message) { host.append(createState({ title: '표시할 데이터가 없습니다', message })); }
export function contributorList(host, rows) {
  if (!rows.length) return empty(host, '관측 구간에 식별된 기여자 기록이 없습니다.');
  for (const row of rows) {
    const name = row.github_login || row.display_name || row.name || '익명';
    const item = element('div', 'analysis-list-row');
    item.append(action(name, () => window.openContributorDrilldown(row.id), 'ui-list-link'), element('span', '', valueText(row.commit_count, '개'))); host.append(item);
  }
}
export function typeBars(host, rows) {
  if (!rows.length) return empty(host, '관측 구간에 분류할 커밋이 없습니다.');
  const names = { feature: '기능 추가', bugfix: '버그 수정', refactor: '리팩토링', docs: '문서', chore: '유지 작업', other: '기타' };
  rows.forEach((row, i) => {
    const line = element('div', 'analysis-type-row');
    const bar = element('progress'); bar.max = 100; if (row.pct == null) bar.hidden = true; else bar.value = row.pct; bar.setAttribute('aria-label', names[row.type] || row.type);
    bar.style.accentColor = `var(--chart-${({ feature: 1, bugfix: 2, refactor: 4, docs: 5, chore: 3, other: 6 })[row.type] || i % 6 + 1})`;
    line.append(element('span', '', names[row.type] || row.type), bar, element('span', '', `${valueText(row.count, '개')} · ${valueText(row.pct, '%')}`)); host.append(line);
  });
}
export function chart(canvas, { labels, values, label, type = 'bar', color = 1, percent = false, onSelect }) {
  canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', label);
  const previous = Chart.getChart(canvas); previous?.destroy();
  return new Chart(canvas, {
    type, data: { labels, datasets: [{ gitvizColorIndex: color, label, data: values, backgroundColor: chartColor(color), borderColor: chartColor(color), borderWidth: type === 'line' ? 2 : 0, pointRadius: 3, tension: 0 }] },
    options: { responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false } },
      scales: { x: { ticks: { maxTicksLimit: 12 } }, y: { beginAtZero: true, ...(percent ? { max: 100 } : {}), title: { display: true, text: percent ? '%' : label.includes('기여자') ? '명' : '건' } } },
      onClick: (_event, points) => { if (points[0] && onSelect) onSelect(points[0].index); },
    },
  });
}

// Preserve existing canvas/DOM identities while giving each legacy panel its own retry.
export function bindPanel(host, load, render) {
  if (!host) return Promise.resolve();
  let status = panelStatuses.get(host);
  if (!status) { status = element('div', 'ui-panel-status'); host.prepend(status); panelStatuses.set(host, status); }
  return loadPanel(status, load, data => { render(data); status.replaceChildren(); });
}
