import './profile';
/** P08b/P09 navigation boundary. Fixture HTTP only; no agent, OAuth or model execution. */
import assert from 'node:assert/strict';
import { app } from 'electron';
import { join } from 'node:path';
import { readFileSync } from 'node:fs';
import { DesktopAuth } from '../src/auth';
import { Remote, type Http } from '../src/remote';
import { RemoteContracts } from '../src/remote-contracts';
import { registerDesktop } from '../src/desktop-ipc';
import { registerHealth } from '../src/ipc';
import { unavailable } from '../contracts/workspace';
import { registerScheme, serveAssets } from '../src/app-protocol';
import { createWindow } from '../src/window';

registerScheme(); app.on('window-all-closed', () => {});
let phase = 'startup';
const deadline = setTimeout(() => { process.stderr.write(`FAIL workspace navigation deadline (${phase})\n`); app.exit(1); }, 30000);
void app.whenReady().then(async () => {
  await serveAssets(join(__dirname, '../ui'));
  const user = { id: '11111111-1111-4111-8111-111111111111', github_login: 'fixture', display_name: null };
  const issued = { user, sessionId: user.id, sessionToken: 'a'.repeat(64), expiresAt: new Date(Date.now() + 300000).toISOString(),
    capabilities: ['remote-v1'], apiCapabilities: { desktopProtocol: 1, aiContract: '1.9' } };
  const requests: string[] = [];
  const http: Http = async (path, method) => {
    assert.equal(method, 'GET', 'navigation never mutates the server'); requests.push(path);
    return { status: 200, headers: {}, body: path === '/api/desktop/v1/session' ? issued : path === '/api/auth/me' ? user : [] };
  };
  const auth = new DesktopAuth('https://fixture.test', http, async () => { throw new Error('UNEXPECTED_BROWSER'); }, { read: async () => issued, write: async () => {} });
  await auth.restore();
  const remote = new Remote(new RemoteContracts(JSON.parse(readFileSync(join(__dirname, '../schema/ai-bodies.json'), 'utf8')) as Record<string, Record<string, unknown>>), http, auth.identity, auth.invalidate);
  const window = createWindow(false);
  const desktop = registerDesktop(window, auth, remote), health = registerHealth(window, unavailable);
  const evaluate = (code: string) => window.webContents.executeJavaScript(code);
  async function wait(code: string): Promise<void> {
    for (let i = 0; i < 150; i++) { if (await evaluate(code)) return; await new Promise(resolve => setTimeout(resolve, 30)); }
    throw new Error(`ASSERTION:${phase}`);
  }
  try {
    phase = 'web-navbar';
    await window.loadURL('gitviz://app/web/index.html?repo=2&snapshot=7');
    await wait(`document.querySelector('[data-workspace-entry]')?.hidden===false`);
    assert.equal(await evaluate('typeof workspace'), 'undefined');
    assert.equal(await evaluate(`document.querySelector('[data-workspace-entry]').href`), 'gitviz://app/index.html?view=inbox&remoteRepositoryId=2&remoteSnapshotId=7');
    phase = 'context-banner';
    await evaluate(`import('/web/js/page-context.js').then(m=>m.renderContextBanner({snapshot:{id:7,target_sha:'fixture',window_end:'2026-10-28T00:00:00Z',analyzed_at:'2026-10-28T00:00:00Z',formula_version:'fixture'},data_quality:{}}))`);
    assert.equal(await evaluate(`document.querySelector('#analysis-context [data-workspace-entry]').href`), 'gitviz://app/index.html?view=inbox&remoteRepositoryId=2&remoteSnapshotId=7');
    phase = 'workspace-entry';
    await evaluate(`document.querySelector('#analysis-context [data-workspace-entry]').click()`);
    await wait(`location.pathname==='/index.html' && document.getElementById('inbox-detail')?.textContent.includes('별도로 확인')`);
    assert.equal(await evaluate(`typeof workspace.getHealth`), 'function');
    assert.equal(await evaluate(`document.getElementById('project-picker').value`), '');
    assert.equal(requests.some(path => path.includes('/repositories/2')), false, 'hints do not trigger remote reads or connection');
    process.stdout.write('PASS workspace navigation: web bridge absent, app-only entries, explicit untrusted context, no automatic connection/execution\n');
  } finally { desktop(); health(); window.destroy(); clearTimeout(deadline); }
}).then(() => app.exit(0)).catch(error => { process.stderr.write(`FAIL workspace navigation (${phase}): ${error instanceof Error ? error.message : 'assertion'}\n`); app.exit(1); });
