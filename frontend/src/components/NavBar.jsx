/**
 * NavBar — 상단 네비게이션
 * - 로그인 전: "GitHub으로 로그인" 버튼
 * - 로그인 후: 아바타 + 사용자명 + 로그아웃 버튼
 */

import { useAuth } from '../hooks/useAuth.jsx';

export default function NavBar() {
  const { user, loading, login, logout } = useAuth();

  return (
    <nav style={styles.nav}>
      <span style={styles.logo}>GitViz</span>

      <div style={styles.right}>
        {loading ? (
          <span style={styles.dim}>로딩 중…</span>
        ) : user ? (
          <>
            {user.avatar_url && (
              <img
                src={user.avatar_url}
                alt={user.github_login}
                style={styles.avatar}
              />
            )}
            <span style={styles.username}>{user.display_name ?? user.github_login}</span>
            <button onClick={logout} style={styles.btnSecondary}>
              로그아웃
            </button>
          </>
        ) : (
          <button onClick={login} style={styles.btnPrimary}>
            GitHub으로 로그인
          </button>
        )}
      </div>
    </nav>
  );
}

const styles = {
  nav: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '12px 24px',
    background: '#24292f',
    color: '#fff',
  },
  logo: { fontWeight: 700, fontSize: '1.2rem', letterSpacing: '-0.5px' },
  right: { display: 'flex', alignItems: 'center', gap: 12 },
  avatar: { width: 32, height: 32, borderRadius: '50%' },
  username: { fontSize: '0.9rem' },
  dim: { fontSize: '0.85rem', color: '#8b949e' },
  btnPrimary: {
    padding: '7px 16px',
    background: '#238636',
    color: '#fff',
    border: 'none',
    borderRadius: 6,
    cursor: 'pointer',
    fontWeight: 600,
    fontSize: '0.85rem',
  },
  btnSecondary: {
    padding: '6px 14px',
    background: 'transparent',
    color: '#cdd9e5',
    border: '1px solid #444c56',
    borderRadius: 6,
    cursor: 'pointer',
    fontSize: '0.85rem',
  },
};
