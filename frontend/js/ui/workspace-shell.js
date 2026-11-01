import { mountShell } from './shell.js';
// The account chip mirrors the desktop sign-in state (layout.js updates its text) and opens a small account menu.
const SIGNED_OUT = '로그인';
const wrap = document.createElement('span'); wrap.className = 'ws-account';
const account = document.createElement('button');
account.type = 'button'; account.id = 'ws-account-chip'; account.className = 'ws-chip ws-chip-button'; account.textContent = SIGNED_OUT;
account.setAttribute('aria-label', '내 계정 메뉴'); account.setAttribute('aria-haspopup', 'menu'); account.setAttribute('aria-expanded', 'false');
const menu = document.createElement('div'); menu.id = 'ws-account-menu'; menu.className = 'ws-menu'; menu.setAttribute('role', 'menu'); menu.hidden = true;
function item(text, action) {
  const button = document.createElement('button'); button.type = 'button'; button.setAttribute('role', 'menuitem'); button.textContent = text;
  button.addEventListener('click', () => { close(); action(); }); menu.append(button); return button;
}
// Settings and project connection live in the sidebar (owner decision); the menu holds sign-in/out only.
const sign = item('로그인', () => document.getElementById(account.textContent === SIGNED_OUT ? 'desktop-login' : 'desktop-logout')?.click());
function close() { menu.hidden = true; account.setAttribute('aria-expanded', 'false'); }
account.addEventListener('click', () => {
  if (!menu.hidden) { close(); return; }
  const signedOut = account.textContent === SIGNED_OUT;
  // Signed out with a server: one click starts sign-in. The menu only explains a missing server or offers logout.
  const login = document.getElementById('desktop-login');
  if (signedOut && login && !login.disabled) { login.click(); return; }
  sign.disabled = !!document.getElementById(signedOut ? 'desktop-login' : 'desktop-logout')?.disabled;
  sign.textContent = signedOut ? (sign.disabled ? '로그인 · 서버가 설정되지 않음' : '로그인') : '로그아웃';
  menu.hidden = false; account.setAttribute('aria-expanded', 'true'); menu.querySelector('button')?.focus();
});
menu.addEventListener('keydown', event => {
  const items = [...menu.querySelectorAll('button:not(:disabled)')]; const index = items.indexOf(document.activeElement);
  if (event.key === 'Escape') { close(); account.focus(); }
  else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); items[(index + (event.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus(); }
});
document.addEventListener('click', event => { if (!wrap.contains(event.target)) close(); });
wrap.append(account, menu);
mountShell({ local: true, account: wrap });
