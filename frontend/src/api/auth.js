/**
 * auth.js — 인증 관련 API 호출 함수
 *
 * 백엔드 BaseURL: /api/auth
 * withCredentials: true  → HttpOnly 세션 쿠키를 자동으로 함께 전송
 */

import axios from 'axios';

const api = axios.create({
  // 개발: Vite 프록시(/api → localhost:8000) 덕분에 상대경로 사용
  // 프로덕션: VITE_API_URL 환경변수로 절대 URL 지정
  baseURL: import.meta.env.VITE_API_URL ?? '',
  withCredentials: true,          // HttpOnly 쿠키 자동 첨부
  headers: { 'Content-Type': 'application/json' },
});

/**
 * GitHub 로그인 페이지로 이동.
 * 백엔드가 302 → GitHub 인증 URL 로 리다이렉트하므로
 * axios 대신 window.location 직접 이동.
 */
export function redirectToGitHubLogin() {
  // Vite 프록시가 /api를 백엔드로 넘기므로 절대경로 불필요
  const base = import.meta.env.VITE_API_URL ?? '';
  window.location.href = `${base}/api/auth/github`;
}

/**
 * 현재 로그인된 사용자 정보 조회.
 * @returns {Promise<{id, github_id, github_login, display_name, avatar_url, email, created_at, last_login_at}>}
 * @throws 401 → 로그인되지 않은 상태
 */
export async function fetchMe() {
  const res = await api.get('/api/auth/me');
  return res.data;
}

/**
 * 로그아웃 — 백엔드 세션 초기화.
 * @returns {Promise<void>}
 */
export async function logout() {
  await api.post('/api/auth/logout');
}
