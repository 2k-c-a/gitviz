import { bindPanel, chart, contributorList, action } from './ui/analytics-view.js';
import { element, setText } from './ui/components.js';
/* ── 분석 개요의 개발 활동 영역: 결과 API → DOM/차트 연동 ──
   chart-timeline 캔버스가 있을 때(=분석 개요 페이지)만 동작한다. */
import { textC, tooltipBg, tooltipBorder } from './utils.js';
import { esc, num, fmtWindow, windowDays } from './fmt.js';
import { renderLoadError } from './page-context.js';
import {
  getRepoId,
  fetchSnapshotMeta,
  fetchSummary,
  fetchTimeline,
  fetchCommitTypes,
  fetchReleases,
  fetchTopContributors,
  fetchContributorHeatmap
} from './api.js';

const timelineCanvas = document.getElementById('chart-timeline');

if (timelineCanvas && typeof Chart !== 'undefined') {
  initTimelinePage().catch(err => {
    console.error('[timeline] 로드 실패:', err);
    renderLoadError(err);
  });
}

/* ── helpers ── */
const fmtDate = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}`;
};

const TYPE_META = {
  feature:  { label: '기능 추가', color: 'var(--chart-1)' },
  refactor: { label: '리팩토링',  color: 'var(--chart-4)' },
  bugfix:   { label: '버그 수정', color: 'var(--chart-2)' },
  docs:     { label: '문서',      color: 'var(--chart-5)' },
  chore:    { label: '유지 작업', color: 'var(--chart-3)' },
  other:    { label: '기타',      color: 'var(--chart-6)' },
};
const RELEASE_DOT = { major: 'major', minor: 'minor', patch: 'patch' };
const HEAT_COLORS = Array.from({ length: 5 }, (_, i) => `var(--chart-heat-${i})`);

async function initTimelinePage() {
  const id = getRepoId();
  const meta = await fetchSnapshotMeta(id);
  await Promise.all([
    bindPanel(document.querySelector('.metrics-4'), () => fetchSummary(id), data => renderHeader(data, meta.snapshot)),
    bindPanel(timelineCanvas.closest('.panel'), () => fetchTimeline(id, 0), renderTimelineChart),
    bindPanel(document.getElementById('tl-donut-legend').closest('.panel'), () => fetchCommitTypes(id), renderDonut),
    bindPanel(document.getElementById('tl-release-list'), () => fetchReleases(id, 10), data => renderReleases(data.releases || [])),
    bindPanel(document.getElementById('act-heatmap'), () => fetchContributorHeatmap(id, 16, 8), renderHeatmap),
    bindPanel(document.getElementById('tl-contrib-list').closest('.panel'), () => fetchTopContributors(id, 50), data => {
      const rows = data.contributors || [];
      const input = element('input'); input.type = 'search'; input.placeholder = '불러온 상위 50명에서 이름 검색'; input.setAttribute('aria-label', '기여자 이름 검색');
      const draw = () => renderContributors(rows.filter(row => (row.github_login || row.display_name || '').toLowerCase().includes(input.value.toLowerCase())));
      input.addEventListener('input', draw); document.getElementById('tl-contrib-list').before(input); draw();
    }),
  ]);
}

/* ── 헤더 + 지표 카드 ──
   요약·타임라인·협업의 수치는 전체 누적이 아니라 저장 결과의 관측 구간(기본 180일) 안의 값이다.
   기간은 결과에 고정된 window_start/window_end를 그대로 보여준다. */
function renderHeader(s, snap) {
  const repo = s.repository || {};
  const days = windowDays(snap);
  const period = `${fmtWindow(snap)}${days != null ? ` (${days}일)` : ''}`;
  setText('tl-repo-name', repo.name || '—');
  setText('tl-branch', repo.default_branch || 'main');
  setText('tl-dash-sub',
    `관측 구간 커밋 ${num(s.total_commits)}개 · 기여자 ${num(s.total_contributors)}명 · 관측 기간 ${period}`);
  setText('tl-window-note',
    '월별 커밋 수 · 활성 기여자 수 · 버그 수정 분류 비율. 관측 구간 안의 기록입니다.');

  setText('tl-m-commits', num(s.total_commits));
  setText('tl-m-commits-sub', `최근 30일 · ${num(s.commits_30d)}개`);
  setText('tl-m-contributors', num(s.active_contributors_90d));
  setText('tl-m-releases', num(s.total_releases));
  setText('tl-m-releases-sub', s.latest_release ? `최신 ${s.latest_release}` : '릴리즈 없음');
  setText('tl-m-bugfix', s.bugfix_pct != null ? `${s.bugfix_pct}%` : '—');
}

/* ── 타임라인 차트 ── */
// 세 지표를 같은 월 축의 작은 그래프 3개로 함께 보여 준다. 단위와 크기가 달라(건·명·%) 한 그래프의 여러 y축에 겹치지 않는다.
const TIMELINE_SERIES = [
  { key: 'commits', label: '커밋 수 (건)', color: 1, type: 'bar' },
  { key: 'active_contributors', label: '활성 기여자 (명)', color: 2, type: 'line', id: 'chart-timeline-contributors' },
  { key: 'bugfix_pct', label: '버그 수정 분류 비율 (%)', color: 4, type: 'line', percent: true, id: 'chart-timeline-bugfix' },
];

function timelineCanvases() {
  const first = timelineCanvas.parentElement;
  first.classList.add('activity-chart-multi');
  if (!first.previousElementSibling?.classList.contains('activity-chart-title')) first.before(element('div', 'activity-chart-title', TIMELINE_SERIES[0].label));
  let last = first;
  return TIMELINE_SERIES.map((series, i) => {
    if (i === 0) return timelineCanvas;
    let canvas = document.getElementById(series.id);
    if (!canvas) {
      const wrap = element('div', 'activity-chart activity-chart-multi');
      canvas = element('canvas'); canvas.id = series.id; wrap.append(canvas);
      last.after(element('div', 'activity-chart-title', series.label), wrap);
    }
    last = canvas.parentElement;
    return canvas;
  });
}

function renderTimelineChart(t) {
  const panel = timelineCanvas.closest('.panel');
  const controls = element('div', 'analysis-toolbar');
  const canvases = timelineCanvases();
  const range = element('select'); range.setAttribute('aria-label', '스냅샷 내 표시할 관측 월 수');
  for (const [value, label] of [[0, '관측 구간 전체'], [3, '마지막 3개 관측 월'], [6, '마지막 6개 관측 월']]) {
    const option = element('option', '', label); option.value = value; range.append(option);
  }
  const selected = element('p', 'p-note'); selected.setAttribute('role', 'status');
  const numbers = element('details'); numbers.append(element('summary', '', '월별 수치와 상세 선택'));
  const rows = element('div', 'analysis-list'); numbers.append(rows);
  const draw = () => {
    const offset = Number(range.value) ? Math.max(0, t.months.length - Number(range.value)) : 0;
    const months = t.months.slice(offset);
    const select = i => { selected.textContent = `${months[i]} · 커밋 ${num(t.commits[offset+i])}건 · 활성 기여자 ${num(t.active_contributors?.[offset+i])}명 · 버그 수정 분류 ${t.bugfix_pct?.[offset+i] == null ? '—' : t.bugfix_pct?.[offset+i] + '%'}`; };
    TIMELINE_SERIES.forEach((series, i) => {
      const canvas = canvases[i];
      canvas.parentElement.hidden = !months.length; canvas.parentElement.previousElementSibling.hidden = !months.length;
      if (!months.length) { Chart.getChart(canvas)?.destroy(); return; }
      chart(canvas, { labels: months, values: (t[series.key] || []).slice(offset), label: series.label, color: series.color, percent: !!series.percent, type: series.type, onSelect: select, alignBars: true });
    });
    const value = (key, i) => t[key]?.[offset + i] == null ? '—' : num(t[key][offset + i]);
    rows.replaceChildren(...months.map((month, i) => action(`${month} · 커밋 ${value('commits', i)}건 · 활성 기여자 ${value('active_contributors', i)}명 · 버그 수정 ${value('bugfix_pct', i)}%`, () => select(i))));
    selected.textContent = months.length ? '차트의 데이터 또는 월별 수치를 선택하세요. 표시 범위는 현재 스냅샷 안에서만 바뀝니다.' : '관측 구간에 커밋이 없습니다.';
  };
  controls.hidden = !t.months.length; numbers.hidden = !t.months.length;
  controls.append(range); panel.querySelector('.p-note').after(controls);
  panel.querySelector('.legend')?.remove(); panel.append(selected, numbers);
  range.addEventListener('change', draw); draw();
}

/* ── 커밋 유형 도넛 + 범례 ── */
function renderDonut(t) {
  const donutCanvas = document.getElementById('chart-donut');
  const types = t.types || [];
  const meta = (k) => TYPE_META[k] || { label: k, color: '#B4B2A9' };

  if (donutCanvas) {
    donutCanvas.hidden = !types.length; Chart.getChart(donutCanvas)?.destroy();
    if (types.length) new Chart(donutCanvas.getContext('2d'), {
      type: 'doughnut',
      data: {
        labels: types.map(x => meta(x.type).label),
        datasets: [{ data: types.map(x => x.count), backgroundColor: types.map(x => meta(x.type).color), borderWidth: 0, hoverOffset: 4 }],
      },
      options: {
        responsive: false, cutout: '68%',
        plugins: { legend: { display: false }, tooltip: { backgroundColor: tooltipBg, borderColor: tooltipBorder, borderWidth: 0.5, bodyColor: textC } },
      },
    });
  }

  const legend = document.getElementById('tl-donut-legend');
  if (legend) {
    legend.innerHTML = !types.length ? '관측 구간에 분류할 커밋이 없습니다.' : types.map(x => {
      const m = meta(x.type);
      return `<div class="donut-leg-row"><div class="donut-leg-label"><div class="donut-swatch" style="background:${m.color};"></div>${esc(m.label)}</div><div class="donut-pct">${x.pct}%</div></div>`;
    }).join('');
  }
}

/* ── 릴리즈 히스토리 ── */
function renderReleases(releases) {
  const wrap = document.getElementById('tl-release-list');
  if (!wrap) return;
  if (!releases.length) { wrap.innerHTML = '<div class="rel-date" style="padding:4px;">릴리즈 정보 없음</div>'; return; }

  wrap.innerHTML = releases.map((r, i) => {
    const dot = RELEASE_DOT[r.release_type] || 'patch';
    const badge = r.release_type === 'major' ? ' <span class="badge badge-blue" style="font-size:var(--ui-text-xs);">major</span>' : '';
    const line = i < releases.length - 1 ? '<div class="rel-line"></div>' : '';
    const meta = [fmtDate(r.published_at), r.commit_count ? `커밋 ${num(r.commit_count)}개` : null].filter(Boolean).join(' · ');
    return `<div class="rel-row"><div class="rel-dot-col"><div class="rel-dot ${dot}"></div>${line}</div>`
      + `<div><div class="rel-ver">${esc(r.tag_name)}${badge}</div><div class="rel-date">${esc(meta)}</div></div></div>`;
  }).join('');
}

/* ── 상위 기여자 ── */
function renderContributors(rows) {
  const host = document.getElementById('tl-contrib-list'); host.replaceChildren(); contributorList(host, rows);
}

/* ── 기여자별 활동 히트맵 ── */
function heatLevel(count, max) {
  if (!count) return 0;
  const ratio = count / max;
  if (ratio > 0.75) return 4;
  if (ratio > 0.5) return 3;
  if (ratio > 0.25) return 2;
  return 1;
}

function renderHeatmap(data) {
  const wrap = document.getElementById('act-heatmap');
  if (!wrap) return;

  const weeks = data.weeks || [];
  const contributors = data.contributors || [];

  if (!weeks.length || !contributors.length) {
    wrap.innerHTML = '<div style="font-size:12px;color:var(--text3);text-align:center;padding:24px 8px;">활동 데이터가 없습니다.</div>';
    return;
  }

  const max = Math.max(1, ...contributors.flatMap(c => c.counts));

  let lastMonth = null;
  const monthsRow = weeks.map(w => {
    const d = new Date(w);
    const m = d.getMonth();
    const label = m !== lastMonth ? `${m + 1}월` : '';
    lastMonth = m;
    return `<div class="act-m">${label}</div>`;
  }).join('');

  const rows = contributors.map(c => {
    const cells = c.counts.map((count, i) => {
      const level = heatLevel(count, max);
      const d = new Date(weeks[i]);
      const dateLabel = `${d.getMonth() + 1}.${d.getDate()}`;
      return `<div class="act-cell" style="background:${HEAT_COLORS[level]};" title="${esc(c.name)} · ${dateLabel} 주 · 커밋 ${num(count)}개"></div>`;
    }).join('');
    return `<div class="act-row"><div class="act-label" title="${esc(c.name)}">${esc(c.name)}</div>${cells}</div>`;
  }).join('');

  wrap.innerHTML = `<div class="act-months">${monthsRow}</div>${rows}`;
  const details = element('details'); details.append(element('summary', '', '주별 커밋 수를 텍스트로 보기'));
  contributors.forEach(c => details.append(element('p', 'p-note', `${c.name}: ${weeks.map((w, i) => `${w} ${num(c.counts[i])}건`).join(' · ')}`)));
  wrap.append(details);
}

