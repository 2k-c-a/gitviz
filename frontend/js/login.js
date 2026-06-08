/* ── 인증 상태 ── */
export let isLoggedIn = false;
export let currentUser = null;

const API_BASE = 'http://localhost:8000';

/* 앱 시작 시 /api/auth/me 호출해 로그인 상태 확인 */
export async function initAuth() {
  try {
    const res = await fetch(`${API_BASE}/api/auth/me`, { credentials: 'include' });
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
  window.location.href = `${API_BASE}/api/auth/github`;
}

/* 로그아웃 */
export async function logout() {
  await fetch(`${API_BASE}/api/auth/logout`, { method: 'POST', credentials: 'include' });
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
}

export function closeUserMenu() {
  const dd = document.getElementById('user-dropdown');
  if (dd) dd.style.display = 'none';
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
        navAvatar.innerHTML = `<img src="${avatar}" style="width:100%;height:100%;border-radius:50%;object-fit:cover;">`;
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

window.openLoginModal  = openLoginModal;
window.closeLoginModal = closeLoginModal;
window.githubLogin     = githubLogin;
window.logout          = logout;
window.toggleUserMenu  = toggleUserMenu;
window.closeUserMenu   = closeUserMenu;
window.updateAuthUI    = updateAuthUI;
window.goMyProjects    = goMyProjects;
window.initAuth        = initAuth;
