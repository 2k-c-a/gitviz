import { currentUser } from './login.js';

/* ── page routing ── */
const PAGES = { index:'index', myprojects:'myprojects', timeline:'timeline', network:'network', 'code-analysis':'code-analysis' };
export function showPage(id) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
  document.getElementById('page-' + id).classList.add('active');
  const navId = 'nav-' + id;
  const el = document.getElementById(navId);
  if (el) el.classList.add('active');
  if (id === 'myprojects') syncMyProjectsHeader();
  window.scrollTo(0, 0);
}
export function syncMyProjectsHeader() {
  if (!currentUser) return;
  const av = document.getElementById('myproj-avatar');
  const un = document.getElementById('myproj-username');
  if (av) av.textContent = currentUser.login[0].toUpperCase();
  if (un) un.textContent = currentUser.login;
}


window.showPage = showPage;