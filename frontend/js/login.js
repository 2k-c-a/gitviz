import { request, isDesktop, startLogin } from './platform/transport.js';
/* ── 인증 상태 ── */
export let isLoggedIn = false;
export let currentUser = null;
globalThis.addEventListener?.('gitviz-account-changed', () => { isLoggedIn = false; currentUser = null; });

/* 앱 시작 시 /api/auth/me 호출해 로그인 상태 확인 */
export async function initAuth() {
  try {
    const res = await request('/api/auth/me', { credentials: 'include' });
    if (res.ok) {
      currentUser = await res.json();
      isLoggedIn = true;
    } else {
      isLoggedIn = false;
      currentUser = null;
    }
  } catch {
    isLoggedIn = false;
    currentUser = null;
  }
  updateAuthUI();
}

export function openLoginModal() {
  const modal = document.getElementById('login-modal');
  if (!modal) return;
  modal.classList.add('open');
}

export function closeLoginModal() {
  document.getElementById('login-modal').classList.remove('open');
}

/* GitHub OAuth 로그인 페이지로 이동 */
export function githubLogin() {
  if (isDesktop()) return startLogin();
  window.location.href = '/api/auth/github';
}

/* 로그아웃 */
export async function logout() {
  if (isDesktop()) {
    try { await globalThis.desktop.auth.logout(); }
    finally {
      isLoggedIn = false; currentUser = null;
      for (const storage of [localStorage, sessionStorage]) {
        for (const key of Object.keys(storage)) if (key.startsWith('gitviz_')) storage.removeItem(key);
      }
      location.href = '/index.html';
    }
    return;
  }
  await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' });
  isLoggedIn = false;
  currentUser = null;
  closeUserMenu();
  updateAuthUI();
  location.href = '/';
}

export function goMyProjects() {
  location.href =
    window.location.pathname.includes('/pages/')
      ? './myprojects.html'
      : './pages/myprojects.html';
}

/* 접근 제한 */
export function requireLogin() {
  if (!isLoggedIn) {
    location.href = '/';
    return false;
  }
  return true;
}

export function toggleUserMenu() {
  const dd = document.getElementById('user-dropdown');
  dd.style.display = dd.style.display === 'block' ? 'none' : 'block';
  document.getElementById('nav-account-trigger')?.setAttribute('aria-expanded', String(dd.style.display === 'block'));
}

export function closeUserMenu() {
  const dd = document.getElementById('user-dropdown');
  if (dd) dd.style.display = 'none';
  document.getElementById('nav-account-trigger')?.setAttribute('aria-expanded', 'false');
}

export function updateAuthUI() {
  const loginBtn = document.getElementById('nav-login-btn');
  const userMenu = document.getElementById('nav-user-menu');
  if (!loginBtn || !userMenu) return;

  loginBtn.style.display = isLoggedIn ? 'none' : 'block';
  userMenu.style.display  = isLoggedIn ? 'flex' : 'none';

  if (isLoggedIn && currentUser) {
    const login  = currentUser.github_login || '';
    const name   = currentUser.display_name || login;
    const avatar = currentUser.avatar_url;

    const navAvatar = document.getElementById('nav-avatar');
    if (navAvatar) {
      if (avatar) {
        const image = document.createElement('img'); image.src = avatar; image.alt = ''; navAvatar.replaceChildren(image);
      } else {
        navAvatar.textContent = login[0]?.toUpperCase() || '?';
      }
    }
    const navUsername = document.getElementById('nav-username');
    if (navUsername) navUsername.textContent = login;

    const dropdownName = document.getElementById('user-dropdown-name');
    if (dropdownName) dropdownName.textContent = name;
  }

  /* 인증 필요 요소: 로그아웃 상태에서 흐리게 + 클릭 차단 */
  document.querySelectorAll('[data-auth]').forEach(el => {
    el.setAttribute('aria-disabled', String(!isLoggedIn));
    if ('disabled' in el) el.disabled = !isLoggedIn;
    el.style.opacity       = isLoggedIn ? '' : '0.4';
    el.style.pointerEvents = isLoggedIn ? '' : 'none';
    el.title               = isLoggedIn ? '' : '로그인이 필요합니다';
  });
}

/* 외부 클릭 시 드롭다운 닫기 */
document.addEventListener('click', e => {
  const menu = document.getElementById('nav-user-menu');
  if (menu && !menu.contains(e.target)) closeUserMenu();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && document.getElementById('user-dropdown')?.style.display === 'block') {
    closeUserMenu(); document.getElementById('nav-account-trigger')?.focus();
  }
});

window.openLoginModal  = openLoginModal;
window.closeLoginModal = closeLoginModal;
window.githubLogin     = githubLogin;
window.logout          = logout;
window.toggleUserMenu  = toggleUserMenu;
window.closeUserMenu   = closeUserMenu;
window.updateAuthUI    = updateAuthUI;
window.goMyProjects    = goMyProjects;
window.initAuth        = initAuth;
