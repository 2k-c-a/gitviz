import { isLoggedIn, openLoginModal } from './login.js';
import { openJobModal } from './modal.js';
import { token } from './ui/chart-theme.js';

/* ── URL 유효성 검사 ── */
export function isValidGithubUrl(url) {
  return /^https?:\/\/github\.com\/[\w.\-]+\/[\w.\-]+\/?$/.test(url.trim());
}
export function parseRepoName(url) {
  const m = url.trim().match(/github\.com\/([\w.\-]+\/[\w.\-]+)/);
  return m ? m[1].replace(/\/$/, '') : null;
}

export const urlInput = document.getElementById('url-input');
export const urlFeedback = document.getElementById('url-feedback');

if (urlInput && urlFeedback) {

  urlInput.addEventListener('blur', () => validateUrl());

  urlInput.addEventListener('input', () => {
    // 이전 에러 상태만 지움
    if (urlInput.classList.contains('input-error')) {
      urlInput.classList.remove('input-error');

      urlFeedback.textContent = '';
      urlFeedback.className = 'url-feedback';
    }
  });

}

export function validateUrl() {
  const val = urlInput.value.trim();
  if (!val) {
    urlInput.classList.remove('input-ok', 'input-error');
    urlFeedback.textContent = '';
    urlFeedback.className = 'url-feedback';
    return false;
  }
  if (!isValidGithubUrl(val)) {
    urlInput.classList.add('input-error');
    urlInput.classList.remove('input-ok');
    urlFeedback.textContent = 'GitHub 레포지토리 URL을 입력해 주세요 (예: https://github.com/owner/repo)';
    urlFeedback.className = 'url-feedback error';
    return false;
  }
  urlInput.classList.add('input-ok');
  urlInput.classList.remove('input-error');
  const name = parseRepoName(val);
  urlFeedback.textContent = '✓ ' + name;
  urlFeedback.className = 'url-feedback ok';
  return true;
}

export function fillUrl(url) {
  urlInput.value = url;
  urlInput.classList.add('input-ok');
  urlInput.classList.remove('input-error');
  const name = parseRepoName(url);
  urlFeedback.textContent = '✓ ' + name;
  urlFeedback.className = 'url-feedback ok';
}

export function handleAnalyze() {

  if (!urlInput) return;

  const valid = validateUrl();

  if (!valid) {
    urlInput.focus();
    return;
  }

  if (!isLoggedIn) {
    openLoginModal();
    return;
  }

  openJobModal('new');
}

/* ── range btn toggle ── */
document.querySelectorAll('.range-btns').forEach(group => {
  group.querySelectorAll('.rbtn').forEach(btn => {
    btn.addEventListener('click', () => {
      group.querySelectorAll('.rbtn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });
});

/* ── 시간 표시 ── */
export function fmtDuration(totalSeconds) {
  const s = Math.max(0, Math.round(totalSeconds));
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}시간 ${m % 60}분`;
  if (m > 0) return `${m}분 ${s % 60}초`;
  return `${s}초`;
}

/* ── color helpers ── */
export const dark = document.documentElement?.dataset.theme === 'dark';
export const gridC = token('--ui-border');
export const textC = token('--ui-muted');
export const tooltipBg = token('--ui-surface');
export const tooltipBorder = token('--ui-border-strong');

if (typeof Chart !== 'undefined') {
  Chart.defaults.font.family = token('--ui-font', 'Segoe UI');
  Chart.defaults.font.size   = 12;
}


window.fillUrl = fillUrl;
window.handleAnalyze = handleAnalyze;
window.validateUrl = validateUrl;
