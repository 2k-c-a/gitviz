import { mountShell } from './shell.js';
// The account chip mirrors the desktop sign-in state (layout.js updates it) and opens the account view.
const account = document.createElement('button');
account.type = 'button'; account.id = 'ws-account-chip'; account.className = 'ws-chip ws-chip-button'; account.textContent = '로그인하지 않음';
account.setAttribute('aria-label', '계정·연결 열기');
account.addEventListener('click', () => window.dispatchEvent(new CustomEvent('workspace-navigate', { detail: { view: 'account' } })));
mountShell({ local: true, account });
