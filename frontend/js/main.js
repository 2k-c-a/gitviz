import { loadNavbar } from './navbar-loader.js';
import { loadModal } from './modal-loader.js';

import {
  openLoginModal,
  closeLoginModal,
  githubLogin,
  logout,
  toggleUserMenu,
  closeUserMenu,
  updateAuthUI,
  initAuth
} from './login.js';

import { showPage } from './routing.js';
import { handleAnalyze, fillUrl, fmtDuration } from './utils.js';

import {
  openJobModal,
  closeJobModal,
  simulateJobState,
  openReanalyzeToast,
  closeToast,
  startReanalyze,
  openRemoveRepoModal,
  closeRemoveRepoModal,
  confirmRemoveRepo,
  openIsolatedPanel,
  closeIsolatedPanel,
  toggleNoisePopover,
  closeNoisePopover,
  saveNoiseFilter
} from './modal.js';

import './timeline.js';
import './risk.js';
import './repoHeader.js';

/* ── 전역 등록 (await 전에 먼저 등록해야 onclick에서 바로 사용 가능) ── */
window.openLoginModal  = openLoginModal;
window.closeLoginModal = closeLoginModal;
window.githubLogin     = githubLogin;
window.logout          = logout;
window.toggleUserMenu  = toggleUserMenu;
window.closeUserMenu   = closeUserMenu;
window.updateAuthUI    = updateAuthUI;
window.showPage        = showPage;
window.handleAnalyze   = handleAnalyze;
window.fillUrl         = fillUrl;
window.openJobModal      = openJobModal;
window.closeJobModal     = closeJobModal;
window.simulateJobState  = simulateJobState;
window.openReanalyzeToast  = openReanalyzeToast;
window.closeToast          = closeToast;
window.startReanalyze      = startReanalyze;
window.openRemoveRepoModal  = openRemoveRepoModal;
window.closeRemoveRepoModal = closeRemoveRepoModal;
window.confirmRemoveRepo    = confirmRemoveRepo;
window.openIsolatedPanel  = openIsolatedPanel;
window.closeIsolatedPanel = closeIsolatedPanel;
window.toggleNoisePopover = toggleNoisePopover;
window.closeNoisePopover  = closeNoisePopover;
window.saveNoiseFilter    = saveNoiseFilter;

/* ── 비동기 초기화 ── */
await loadNavbar();
await loadModal();
await initAuth();
await loadRecentRepos();

/* ── 최근 분석 요청된 레포지토리 목록 ── */
async function loadRecentRepos() {
  const container = document.getElementById('recent-repo-list');
  if (!container) return;

  let repos;
  try {
    const res = await fetch('/repositories');
    if (!res.ok) throw new Error(res.status);
    repos = await res.json();
  } catch {
    container.innerHTML = '<div class="repo-meta" style="padding:16px 0;">목록을 불러올 수 없습니다.</div>';
    return;
  }

  if (!repos.length) {
    container.innerHTML = '<div class="repo-meta" style="padding:16px 0;">아직 분석 요청된 레포지토리가 없습니다.</div>';
    return;
  }

  container.innerHTML = repos.map(repo => {
    const repoName = repo.url.replace('https://github.com/', '');
    const meta = _buildMeta(repo);
    const badge = _buildBadge(repo.status, repo.is_stale);
    const isActive = ['pending', 'running', 'rate_limited'].includes(repo.status);
    const hasCountdown = repo.status === 'rate_limited' && !repo.is_stale && repo.remaining_seconds != null;
    const linkText = repo.is_stale
      ? '재시작 필요 — 자세히 보기'
      : hasCountdown ? `${fmtDuration(repo.remaining_seconds)} 후 재개` : '진행 상황 보기';
    const countdownAttr = hasCountdown ? ` data-countdown-id="${repo.id}" data-remaining="${repo.remaining_seconds}"` : '';
    const progressLink = isActive
      ? `<span class="pending-link"${countdownAttr} onclick="openJobModal('poll', ${repo.id})">${linkText}</span>`
      : '';
    const clickAttr = repo.status === 'done'
      ? `onclick="window.goPage && window.goPage('timeline', ${repo.id})"` : '';

    return `
      <div class="repo-item${isActive ? ' pending' : ''}" ${clickAttr} style="${repo.status === 'done' ? 'cursor:pointer' : ''}">
        <div>
          <div class="repo-name">${repoName}</div>
          <div class="repo-meta">${meta}</div>
        </div>
        <div class="repo-item-right">
          ${badge}
          ${progressLink}
        </div>
      </div>`;
  }).join('');
}

function _buildMeta(repo) {
  const parts = [];
  if (repo.commit_count) parts.push(`커밋 ${repo.commit_count.toLocaleString()}개`);
  if (repo.pr_count) parts.push(`PR ${repo.pr_count.toLocaleString()}개`);
  if (repo.contributor_count) parts.push(`기여자 ${repo.contributor_count.toLocaleString()}명`);
  if (!parts.length) parts.push('수집 중...');
  return parts.join(' · ');
}

function _buildBadge(status, isStale) {
  if (isStale) {
    return `<span class="badge badge-red"><span class="status-dot" style="background:var(--red)"></span>재시작 필요</span>`;
  }
  const map = {
    done:         ['badge-green', 'var(--green)',  '완료'],
    running:      ['badge-blue',  'var(--blue)',   '수집 중'],
    pending:      ['badge-blue',  'var(--blue)',   '대기 중'],
    rate_limited: ['badge-blue',  'var(--blue)',   'Rate Limit 대기'],
    failed:       ['badge-red',   'var(--red)',    '실패'],
    unknown:      ['badge-gray',  'var(--text3)',  '알 수 없음'],
  };
  const [cls, color, label] = map[status] || map.unknown;
  return `<span class="badge ${cls}"><span class="status-dot" style="background:${color}"></span>${label}</span>`;
}

/* 홈 화면 레포 목록의 "N분 M초 후 재개" 카운트다운을 1초마다 로컬에서 줄인다
   (서버를 매초 두드리지 않고, 값이 dataset에 남아있는 요소만 갱신).
   0에 도달하면 실제 상태가 바뀌었는지 목록을 한 번 다시 불러와 동기화한다. */
setInterval(() => {
  const els = document.querySelectorAll('[data-countdown-id]');
  let hitZero = false;
  els.forEach(el => {
    const remaining = parseInt(el.dataset.remaining, 10);
    if (Number.isNaN(remaining)) return;
    const next = Math.max(0, remaining - 1);
    el.dataset.remaining = String(next);
    el.textContent = next > 0 ? `${fmtDuration(next)} 후 재개` : '재개 확인 중...';
    if (next === 0) hitZero = true;
  });
  if (hitZero) loadRecentRepos();
}, 1000);

/* 카운트다운이 없는 상태 변화(수집 중 → 완료, 멈춤 감지 등)도 놓치지 않도록 주기적으로 재동기화 */
setInterval(() => loadRecentRepos(), 20000);
