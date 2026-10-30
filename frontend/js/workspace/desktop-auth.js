const notice = document.getElementById('desktop-account');
const login = document.getElementById('desktop-login');
const logout = document.getElementById('desktop-logout');
async function refresh() {
  try {
    const state = await window.desktop.auth.status();
    notice.textContent = state.state === 'authenticated' ? `원격 계정: ${state.user.github_login}`
      : state.state === 'revoke-pending' ? '로컬 로그아웃 완료. 서버 세션 폐기를 다시 시도하세요.' : '원격 계정에 로그인하지 않았습니다.';
    logout.disabled = !['authenticated', 'revoke-pending', 'pending'].includes(state.state);
  } catch { notice.textContent = '원격 서버 설정 또는 연결을 확인하세요.'; }
}
login.addEventListener('click', () => { location.href = 'web/pages/search.html'; });
logout.addEventListener('click', async () => {
  try { await window.desktop.auth.logout(); } catch { /* State below distinguishes pending revocation. */ }
  await refresh();
});
void refresh();
setInterval(() => { if (!document.hidden) void refresh(); }, 3000);
