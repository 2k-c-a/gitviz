import { bindPanel, contributorList, action } from './ui/analytics-view.js';
import { chartColor, token } from './ui/chart-theme.js';
import { element, setText } from './ui/components.js';
/* ── 분석 개요의 개발 활동 영역: 결과 API → DOM/차트 연동 ──
   chart-timeline 캔버스가 있을 때(=분석 개요 페이지)만 동작한다. */
import { textC, tooltipBg, tooltipBorder } from './utils.js';
import { esc, num, fmtWindow } from './fmt.js';
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
    // 릴리즈는 타임라인 위 표시용이라 받지 못해도 그래프는 그린다.
    bindPanel(timelineCanvas.closest('.panel'), () => Promise.all([fetchTimeline(id, 0), fetchReleases(id, 500).catch(() => null)]), ([t, rel]) => renderTimelineChart(t, rel?.releases || [])),
    bindPanel(document.getElementById('tl-donut-legend').closest('.panel'), () => fetchCommitTypes(id), renderDonut),
    bindPanel(document.getElementById('tl-release-list'), () => fetchReleases(id, 10), data => renderReleases(data.releases || [])),
    bindPanel(document.getElementById('act-heatmap'), () => fetchContributorHeatmap(id, heatmapWeeks(meta.snapshot), 8), renderHeatmap),
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
  setText('tl-repo-name', repo.name || '—');
  setText('tl-branch', repo.default_branch || 'main');
  setText('tl-dash-sub',
    `${snap && snap.window_start ? '최근 180일' : '분석 기간'}(${fmtWindow(snap)}) 커밋 ${num(s.total_commits)}개 · 기여자 ${num(s.total_contributors)}명`);
  setText('tl-window-note',
    '월별 커밋 수 · 활성 기여자 수 · 버그 수정 분류 비율. 최근 180일의 기록입니다.');

  setText('tl-heatmap-range', snap && snap.window_start ? '최근 180일' : '분석 종료 시점 기준 최근 26주');

  setText('tl-m-commits', num(s.total_commits));
  setText('tl-m-commits-sub', `최근 30일 · ${num(s.commits_30d)}개`);
  setText('tl-m-contributors', num(s.active_contributors_90d));
  setText('tl-m-releases', num(s.total_releases));
  setText('tl-m-releases-sub', s.latest_release ? `최신 ${s.latest_release}` : '릴리즈 없음');
  setText('tl-m-bugfix', s.bugfix_pct != null ? `${s.bugfix_pct}%` : '—');
}

/* ── 타임라인 차트 ──
   관측 구간 전체의 세 지표를 한 카드 안에 띠 세 개로 위아래로 쌓는다. 가로축(월)은 하나를 같이 쓰고,
   각 띠는 자기 세로 축을 쓴다(단위가 달라 한 축에 겹치면 작은 값이 납작해진다).
   마우스를 올리면 세로 기준선 하나와 그 달의 세 값을 한 툴팁에 보여 준다. */
const TIMELINE_SERIES = [
  { key: 'commits', label: '커밋 수', unit: '건', short: '커밋(건)', color: 1, type: 'bar', axis: 'y', weight: 1.3 },
  { key: 'active_contributors', label: '활성 기여자', unit: '명', short: '기여자(명)', color: 2, type: 'line', axis: 'yContributors', weight: 1 },
  { key: 'bugfix_pct', label: '버그 수정 분류 비율', unit: '%', short: '버그 수정(%)', color: 4, type: 'line', axis: 'yBugfix', weight: 1, max: 100 },
];

/* 릴리즈 표시: 릴리즈가 나온 달의 커밋 띠 바로 위에 ◆(여러 개면 개수). 태그는 툴팁과 월 상세에 나온다. */
const RELEASE_MARK_SPACE = 16;
const timelineReleases = {
  id: 'gitvizTimelineReleases',
  afterDatasetsDraw(chart) {
    const byMonth = chart.$gitvizReleases;
    if (!byMonth?.size) return;
    const ctx = chart.ctx, x = chart.scales.x, top = chart.scales.y.top - RELEASE_MARK_SPACE / 2, color = token('--ui-text', '#111827');
    ctx.save();
    chart.data.labels.forEach((month, i) => {
      const tags = byMonth.get(month);
      if (!tags) return;
      const cx = x.getPixelForValue(i), r = 5;
      ctx.beginPath(); ctx.moveTo(cx, top - r); ctx.lineTo(cx + r, top); ctx.lineTo(cx, top + r); ctx.lineTo(cx - r, top); ctx.closePath();
      ctx.fillStyle = color; ctx.fill();
      if (tags.length > 1) { ctx.font = '600 11px sans-serif'; ctx.textBaseline = 'middle'; ctx.fillText(`×${tags.length}`, cx + r + 3, top); }
    });
    ctx.restore();
  },
};
const releaseMonths = (releases) => {
  const map = new Map();
  for (const r of releases) {
    const month = typeof r.published_at === 'string' ? r.published_at.slice(0, 7) : null;
    if (!month) continue;
    if (!map.has(month)) map.set(month, []);
    map.get(month).push(r.tag_name || r.name || '이름 없음');
  }
  return map;
};

/* 세 띠를 관통하는 세로 기준선. 툴팁이 가리키는 달에만 그린다. */
const timelineCrosshair = {
  id: 'gitvizTimelineCrosshair',
  afterDatasetsDraw(chart) {
    const active = chart.tooltip?.getActiveElements?.() || [];
    if (!active.length) return;
    const x = active[0].element.x, { top, bottom } = chart.chartArea, ctx = chart.ctx;
    ctx.save(); ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom);
    ctx.lineWidth = 1; ctx.strokeStyle = chartColor(6); ctx.setLineDash([3, 3]); ctx.stroke(); ctx.restore();
  },
};

function renderTimelineChart(t, releases = []) {
  const panel = timelineCanvas.closest('.panel');
  const months = t.months || [];
  const selected = element('p', 'p-note'); selected.setAttribute('role', 'status');
  const numbers = element('details'); numbers.append(element('summary', '', '월별 수치와 상세 선택'));
  const rows = element('div', 'analysis-list'); numbers.append(rows);
  const value = (key, i) => t[key]?.[i] == null ? '—' : num(t[key][i]);
  const byMonth = releaseMonths(releases);
  const releaseText = i => byMonth.has(months[i]) ? ` · 릴리즈 ${byMonth.get(months[i]).join(', ')}` : '';
  const select = i => { selected.textContent = `${months[i]} · 커밋 ${value('commits', i)}건 · 활성 기여자 ${value('active_contributors', i)}명 · 버그 수정 분류 ${value('bugfix_pct', i)}%${releaseText(i)}`; };
  const legend = panel.querySelector('.legend');
  timelineCanvas.parentElement.hidden = !months.length; numbers.hidden = !months.length;
  if (legend) legend.hidden = !months.length;
  Chart.getChart(timelineCanvas)?.destroy();
  if (months.length) {
    timelineCanvas.setAttribute('role', 'img');
    timelineCanvas.setAttribute('aria-label', '월별 커밋 수, 활성 기여자 수, 버그 수정 분류 비율');
    const scales = { x: { ticks: { maxTicksLimit: 12 } } };
    // 같은 stack 이름의 축은 위아래로 나뉜다. 목록의 마지막 축이 가장 위 띠가 되므로 거꾸로 넣는다.
    [...TIMELINE_SERIES].reverse().forEach(series => {
      scales[series.axis] = {
        stack: 'timeline', stackWeight: series.weight, offset: true, beginAtZero: true, position: 'left',
        ...(series.max ? { max: series.max } : {}),
        ticks: { maxTicksLimit: 4, precision: 0 },
        title: { display: true, text: series.short },
        border: { display: true },
      };
    });
    const chart = new Chart(timelineCanvas, {
      type: 'bar',
      data: {
        labels: months,
        datasets: TIMELINE_SERIES.map(series => ({
          type: series.type, label: series.label, data: t[series.key] || [], yAxisID: series.axis, gitvizColorIndex: series.color,
          backgroundColor: chartColor(series.color), borderColor: chartColor(series.color),
          borderWidth: series.type === 'line' ? 2 : 0, pointRadius: 3, pointHoverRadius: 5, tension: 0, maxBarThickness: 28,
        })),
      },
      plugins: [timelineCrosshair, timelineReleases],
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        layout: { padding: { top: byMonth.size ? RELEASE_MARK_SPACE : 0 } },
        interaction: { mode: 'index', intersect: false, axis: 'x' },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: item => {
            const series = TIMELINE_SERIES[item.datasetIndex];
            return ` ${series.label} ${item.raw == null ? '—' : `${num(item.raw)}${series.unit}`}`;
          }, footer: items => items[0] && byMonth.has(months[items[0].dataIndex]) ? `◆ 릴리즈 ${byMonth.get(months[items[0].dataIndex]).join(', ')}` : '' } },
        },
        scales,
        onClick: (_event, points) => { if (points[0]) select(points[0].index); },
      },
    });
    chart.$gitvizReleases = byMonth; chart.draw();
  }
  rows.replaceChildren(...months.map((month, i) => action(`${month} · 커밋 ${value('commits', i)}건 · 활성 기여자 ${value('active_contributors', i)}명 · 버그 수정 ${value('bugfix_pct', i)}%${releaseText(i)}`, () => select(i))));
  selected.textContent = months.length ? '그래프의 월 또는 월별 수치를 선택하세요. 최근 180일을 보여 줍니다.' : '최근 180일에 커밋이 없습니다.';
  panel.append(selected, numbers);
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
    legend.innerHTML = !types.length ? '최근 180일에 분류할 커밋이 없습니다.' : types.map(x => {
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

/* ── 기여자별 활동 히트맵 ──
   관측 구간(기본 180일)과 같은 기간을 보여 준다. 서버는 분석 종료 시점이 속한 주부터 거꾸로 weeks주를 주므로,
   구간 시작일이 속한 주까지 들어가도록 주 수를 센다. 구간이 없는 이전 결과는 26주(약 180일). */
const DAY_MS = 86400000;
function heatmapWeeks(snap) {
  const start = Date.parse(snap?.window_start), end = new Date(snap?.window_end);
  if (!Number.isFinite(start) || Number.isNaN(end.getTime())) return 26;
  const endMonday = Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), end.getUTCDate()) - ((end.getUTCDay() + 6) % 7) * DAY_MS;
  return Math.min(104, Math.max(1, Math.ceil((endMonday - start) / (7 * DAY_MS)) + 1));
}

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
}

