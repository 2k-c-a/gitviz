const notice = document.getElementById('desktop-account');
const login = document.getElementById('desktop-login');
const logout = document.getElementById('desktop-logout');
async function refresh() {
  try {
    const state = await window.desktop.auth.status();
    notice.textContent = state.state === 'authenticated' ? `원격 계정: ${state.user.github_login}`
      : state.state === 'pending' ? '브라우저에서 GitHub로 로그인하면 앱이 자동으로 로그인됩니다.'
      : state.state === 'revoke-pending' ? '이 PC에서는 로그아웃했습니다. 서버 세션 종료는 다시 시도하세요.' : '원격 계정에 로그인하지 않았습니다.';
    logout.disabled = !['authenticated', 'revoke-pending', 'pending'].includes(state.state);
    login.disabled = false;
  } catch (error) {
    // The 3-second refresh can overlap a start or an exchange poll; keep the current text.
    if (String(error?.message || error).includes('BUSY')) return;
    // No deployment server origin: signing in cannot work, so say so instead of opening a page that fails.
    const unconfigured = String(error?.message || error).includes('SERVER_NOT_CONFIGURED');
    login.disabled = unconfigured;
    notice.textContent = unconfigured ? '이 앱에는 로그인 서버가 설정되어 있지 않아 로그인할 수 없습니다. 로컬 기능은 모두 쓸 수 있습니다.' : '원격 서버 설정 또는 연결을 확인하세요.';
  }
}
// One click: open GitHub sign-in in the browser; refresh() polls the exchange until the browser approval lands.
login.addEventListener('click', async () => {
  login.disabled = true;
  try { await window.desktop.auth.start(); }
  catch (error) {
    notice.textContent = String(error?.message || error).includes('BROWSER_UNAVAILABLE') ? '브라우저를 열지 못했습니다.' : '로그인을 시작하지 못했습니다. 서버 연결을 확인하세요.';
  }
  await refresh();
});
logout.addEventListener('click', async () => {
  try { await window.desktop.auth.logout(); } catch { /* State below distinguishes pending revocation. */ }
  await refresh();
});
void refresh();
setInterval(() => { if (!document.hidden) void refresh(); }, 3000);
