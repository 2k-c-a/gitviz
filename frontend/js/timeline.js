/* ── 타임라인 페이지: 결과 API → DOM/차트 연동 ──
   chart-timeline 캔버스가 있을 때(=타임라인 페이지)만 동작한다. */
import { dark, gridC, textC, tooltipBg, tooltipBorder } from './utils.js';
import { fmtWindow, windowDays } from './fmt.js';
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
const num = (n) => (n ?? 0).toLocaleString('en-US');
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const ymToLabel = (ym) => ym.slice(2).replace('-', '.');           // '2025-04' → '25.04'
const fmtDate = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}`;
};

const TYPE_META = {
  feature:  { label: '기능 추가', color: '#378ADD' },
  refactor: { label: '리팩토링',  color: '#EF9F27' },
  bugfix:   { label: '버그 픽스', color: '#1D9E75' },
  docs:     { label: '문서',      color: '#F0997B' },
  chore:    { label: '잡무',      color: '#C0DD97' },
  other:    { label: '기타',      color: '#B4B2A9' },
};
const RELEASE_DOT = { major: 'major', minor: 'minor', patch: 'patch' };
const HEAT_COLORS = ['var(--bg2)', '#B5D4F4', '#85B7EB', '#378ADD', '#185FA5'];

async function initTimelinePage() {
  const id = getRepoId();
  const [meta, summary, timeline, types, releases, contributors, heatmap] = await Promise.all([
    fetchSnapshotMeta(id),
    fetchSummary(id),
    fetchTimeline(id, 36),
    fetchCommitTypes(id),
    fetchReleases(id, 5),
    fetchTopContributors(id, 5),
    fetchContributorHeatmap(id, 16, 8),
  ]);

  if (summary.error) throw new Error(summary.error);

  renderHeader(summary, meta.snapshot);
  renderTimelineChart(timeline);
  renderDonut(types);
  renderReleases(releases.releases || []);
  renderContributors(contributors.contributors || []);
  renderHeatmap(heatmap);
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
    `월별 커밋 수 · 활성 기여자 수 · 버그 픽스 비율. 관측 기간 ${period}(종료 시각 제외) 안의 기록만 보여주며 전체 누적 수가 아닙니다.`);

  setText('tl-m-commits', num(s.total_commits));
  setText('tl-m-commits-sub', `분석 종료 시점 이전 30일 ${num(s.commits_30d)}개`);
  setText('tl-m-contributors', num(s.active_contributors_90d));
  setText('tl-m-releases', num(s.total_releases));
  setText('tl-m-releases-sub', s.latest_release ? `최신 ${s.latest_release}` : '릴리즈 없음');
  setText('tl-m-bugfix', s.bugfix_pct != null ? `${s.bugfix_pct}%` : '—');
}

/* ── 타임라인 차트 ── */
function renderTimelineChart(t) {
  if (!t.months || !t.months.length) {
    const box = timelineCanvas.parentElement;
    box.innerHTML = '<div style="height:100%;display:flex;align-items:center;justify-content:center;font-size:12px;color:var(--text3);text-align:center;">관측 구간에 커밋이 없습니다 (수집 실패가 아니라 구간 밖의 변경일 수 있습니다).</div>';
    return;
  }
  new Chart(timelineCanvas.getContext('2d'), {
    type: 'bar',
    data: {
      labels: t.months.map(ymToLabel),
      datasets: [
        { label: '커밋 수', data: t.commits, backgroundColor: '#B5D4F4', borderRadius: 2, yAxisID: 'y', order: 3 },
        { label: '활성 기여자 수', data: t.active_contributors, type: 'line', borderColor: '#1D9E75', backgroundColor: 'transparent', borderWidth: 2, pointRadius: 0, tension: 0.4, yAxisID: 'y2', order: 2 },
        { label: '버그 픽스 비율(%)', data: t.bugfix_pct, type: 'line', borderColor: '#EF9F27', backgroundColor: 'transparent', borderWidth: 1.5, borderDash: [4, 3], pointRadius: 0, tension: 0.4, yAxisID: 'y3', order: 1 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: { backgroundColor: tooltipBg, borderColor: tooltipBorder, borderWidth: 0.5, titleColor: dark ? '#c2c0b6' : '#3d3d3a', bodyColor: textC, padding: 10 },
      },
      scales: {
        x: { grid: { color: gridC, lineWidth: 0.5 }, ticks: { color: textC, maxTicksLimit: 14 }, border: { color: 'transparent' } },
        y: { position: 'left', grid: { color: gridC, lineWidth: 0.5 }, ticks: { color: textC, maxTicksLimit: 5 }, border: { color: 'transparent' } },
        y2: { position: 'right', grid: { display: false }, ticks: { color: '#1D9E75', maxTicksLimit: 4 }, border: { color: 'transparent' } },
        y3: { display: false },
      },
    },
  });
}

/* ── 커밋 유형 도넛 + 범례 ── */
function renderDonut(t) {
  const donutCanvas = document.getElementById('chart-donut');
  const types = t.types || [];
  const meta = (k) => TYPE_META[k] || { label: k, color: '#B4B2A9' };

  if (donutCanvas) {
    new Chart(donutCanvas.getContext('2d'), {
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
    legend.innerHTML = types.map(x => {
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
    const badge = r.release_type === 'major' ? ' <span class="badge badge-blue" style="font-size:10px;">major</span>' : '';
    const line = i < releases.length - 1 ? '<div class="rel-line"></div>' : '';
    const meta = [fmtDate(r.published_at), r.commit_count ? `커밋 ${num(r.commit_count)}개` : null].filter(Boolean).join(' · ');
    return `<div class="rel-row"><div class="rel-dot-col"><div class="rel-dot ${dot}"></div>${line}</div>`
      + `<div><div class="rel-ver">${esc(r.tag_name)}${badge}</div><div class="rel-date">${esc(meta)}</div></div></div>`;
  }).join('');
}

/* ── 상위 기여자 ── */
function renderContributors(contributors) {
  const wrap = document.getElementById('tl-contrib-list');
  if (!wrap) return;
  if (!contributors.length) { wrap.innerHTML = '<div class="c-meta" style="padding:4px;">기여자 정보 없음</div>'; return; }

  const max = contributors[0].commit_count || 1;
  wrap.innerHTML = contributors.map(c => {
    const name = c.github_login || c.display_name || '익명';
    const pct = Math.round(100 * c.commit_count / max);
    const recent = c.last_commit_at ? ` · 마지막 커밋 ${fmtDate(c.last_commit_at)}` : '';  // 오래된 저장 결과에서 '오늘 기준 N일 전'으로 읽히지 않게 날짜로 표시
    return `<div class="c-row" style="cursor:pointer;" onclick="window.openContributorDrilldown(${c.id})" title="소유 파일·협업자 보기">`
      + `<div class="avatar" style="background:#E6F1FB;color:#185FA5;">${esc(name[0].toUpperCase())}</div>`
      + `<div class="c-info"><div class="c-name">${esc(name)}</div><div class="c-meta">커밋 ${num(c.commit_count)}개${recent}</div></div>`
      + `<div class="bar-wrap"><div class="bar-fill bar-blue" style="width:${pct}%;"></div></div>`
      + `<div class="c-count">${num(c.commit_count)}</div></div>`;
  }).join('');
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
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}
