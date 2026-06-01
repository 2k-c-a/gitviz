/**
 * useAuth — 앱 전역 인증 상태 관리 훅
 *
 * 사용 예:
 *   const { user, loading, login, logout } = useAuth();
 *
 * - user: null(비로그인) | UserMe 객체(로그인)
 * - loading: 초기 /me 조회 중 true
 * - login(): GitHub 로그인 페이지로 이동
 * - logout(): 세션 삭제 후 user → null
 */

import { useState, useEffect, useCallback, createContext, useContext } from 'react';
import { fetchMe, logout as apiLogout, redirectToGitHubLogin } from '../api/auth';

// ── Context ──────────────────────────────────────────────────────────────────

const AuthContext = createContext(null);

/**
 * AuthProvider: App 루트에서 한 번만 감싸면 됩니다.
 *
 * <AuthProvider>
 *   <App />
 * </AuthProvider>
 */
export function AuthProvider({ children }) {
  const [user, setUser]       = useState(null);
  const [loading, setLoading] = useState(true);   // 초기 세션 확인 중

  // 앱 최초 마운트 시 세션 유효성 확인
  useEffect(() => {
    fetchMe()
      .then(setUser)
      .catch(() => setUser(null))   // 401 등 에러 → 비로그인 처리
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(() => {
    redirectToGitHubLogin();
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      setUser(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

/**
 * useAuth: AuthProvider 하위 컴포넌트에서 인증 상태를 가져옵니다.
 */
export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth는 <AuthProvider> 안에서만 사용할 수 있습니다.');
  }
  return ctx;
}
