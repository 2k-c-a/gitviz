/* ── 타임라인 페이지: 결과 API → DOM/차트 연동 ──
   chart-timeline 캔버스가 있을 때(=타임라인 페이지)만 동작한다. */
import { dark, gridC, textC, tooltipBg, tooltipBorder } from './utils.js';
import {
  getRepoId,
  fetchSummary,
  fetchTimeline,
  fetchCommitTypes,
  fetchReleases,
  fetchTopContributors
} from './api.js';

const timelineCanvas = document.getElementById('chart-timeline');

if (timelineCanvas && typeof Chart !== 'undefined') {
  initTimelinePage().catch(err => console.error('[timeline] 로드 실패:', err));
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
const daysAgo = (iso) => {
  if (!iso) return '';
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  if (days <= 0) return '오늘';
  return `${days}일 전`;
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

async function initTimelinePage() {
  const id = getRepoId();
  const [summary, timeline, types, releases, contributors] = await Promise.all([
    fetchSummary(id),
    fetchTimeline(id, 36),
    fetchCommitTypes(id),
    fetchReleases(id, 5),
    fetchTopContributors(id, 5),
  ]);

  if (summary.error) throw new Error(summary.error);

  renderHeader(summary);
  renderTimelineChart(timeline);
  renderDonut(types);
  renderReleases(releases.releases || []);
  renderContributors(contributors.contributors || []);
}

/* ── 헤더 + 지표 카드 ── */
function renderHeader(s) {
  const repo = s.repository || {};
  setText('tl-repo-name', repo.name || '—');
  setText('tl-branch', repo.default_branch || 'main');
  setText('tl-dash-sub',
    `커밋 ${num(s.total_commits)}개 · 기여자 ${num(s.total_contributors)}명 · `
    + (repo.last_analyzed_at ? `마지막 분석 ${fmtDate(repo.last_analyzed_at)}` : '분석 정보 없음'));

  setText('tl-m-commits', num(s.total_commits));
  setText('tl-m-commits-sub', `▲ 최근 30일 +${num(s.commits_30d)}`);
  setText('tl-m-contributors', num(s.active_contributors_90d));
  setText('tl-m-releases', num(s.total_releases));
  setText('tl-m-releases-sub', s.latest_release ? `최신 ${s.latest_release}` : '릴리즈 없음');
  setText('tl-m-bugfix', s.bugfix_pct != null ? `${s.bugfix_pct}%` : '—');
}

/* ── 타임라인 차트 ── */
function renderTimelineChart(t) {
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
    const recent = c.last_commit_at ? ` · 최근 ${daysAgo(c.last_commit_at)}` : '';
    return `<div class="c-row"><div class="avatar" style="background:#E6F1FB;color:#185FA5;">${esc(name[0].toUpperCase())}</div>`
      + `<div class="c-info"><div class="c-name">${esc(name)}</div><div class="c-meta">커밋 ${num(c.commit_count)}개${recent}</div></div>`
      + `<div class="bar-wrap"><div class="bar-fill bar-blue" style="width:${pct}%;"></div></div>`
      + `<div class="c-count">${num(c.commit_count)}</div></div>`;
  }).join('');
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}
