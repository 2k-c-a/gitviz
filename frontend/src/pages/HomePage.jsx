/**
 * HomePage — 로그인 전/후 분기되는 랜딩 페이지
 */

import { useAuth } from '../hooks/useAuth.jsx';

export default function HomePage() {
  const { user, loading, login } = useAuth();

  if (loading) {
    return <div style={styles.center}>로딩 중…</div>;
  }

  if (user) {
    return (
      <div style={styles.center}>
        <h2>안녕하세요, {user.display_name ?? user.github_login}님!</h2>
        <p style={styles.sub}>
          GitHub 저장소를 분석하려면 상단 메뉴의 <strong>내 분석 프로젝트</strong>를 클릭하세요.
        </p>
      </div>
    );
  }

  return (
    <div style={styles.center}>
      <h1 style={styles.title}>GitViz</h1>
      <p style={styles.sub}>GitHub 저장소의 협업 구조와 코드 위험을 시각화합니다.</p>
      <button onClick={login} style={styles.btn}>
        GitHub으로 로그인
      </button>
    </div>
  );
}

const styles = {
  center: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 'calc(100vh - 57px)',
    gap: 16,
    padding: 24,
  },
  title: { fontSize: '2.5rem', fontWeight: 700, margin: 0 },
  sub: { color: '#57606a', maxWidth: 420, textAlign: 'center', lineHeight: 1.6 },
  btn: {
    marginTop: 8,
    padding: '12px 28px',
    background: '#238636',
    color: '#fff',
    border: 'none',
    borderRadius: 8,
    cursor: 'pointer',
    fontWeight: 700,
    fontSize: '1rem',
  },
};
