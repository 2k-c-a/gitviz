import './profile';
/** Real sandbox + OS-protected session persistence; system browser is an injected fixture. */
import assert from 'node:assert/strict';
import { app, safeStorage } from 'electron';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { DesktopAuth, SessionVault } from '../src/auth';
import { Remote, type Http } from '../src/remote';
import { RemoteContracts } from '../src/remote-contracts';
import { registerDesktop } from '../src/desktop-ipc';
import { registerScheme, serveAssets } from '../src/app-protocol';
import { createWindow } from '../src/window';

registerScheme(); app.on('window-all-closed', () => {});
const uid = '11111111-1111-4111-8111-111111111111';
const token = 's'.repeat(64);
const deadline = setTimeout(() => { process.stderr.write('FAIL P03 auth deadline\n'); app.exit(1); }, 30000);
let phase = 'start';
void app.whenReady().then(async () => {
  const directory = await mkdtemp(join(tmpdir(), 'gitviz-p03-auth-'));
  const file = join(directory, 'session.dpapi');
  const vault = new SessionVault(file, 'https://fixture.test', {
    available: () => safeStorage.isAsyncEncryptionAvailable(), encrypt: value => safeStorage.encryptStringAsync(value),
    decrypt: async value => (await safeStorage.decryptStringAsync(value)).result,
  });
  const issued = { sessionToken: token, sessionId: uid, user: { id: uid, github_login: 'fixture', display_name: null }, expiresAt: new Date(Date.now() + 300000).toISOString(), capabilities: ['remote-v1'], apiCapabilities: { desktopProtocol: 1, aiContract: '1.9' } };
  let opened = 0, revoked = false;
  const http: Http = async (path, method, _body, givenToken) => {
    if (path.endsWith('transactions')) return { status: 201, body: { flowId: uid, pollSecret: 'p'.repeat(43), displayCode: 'AABBCCDD', browserUrl: `https://fixture.test/api/desktop/v1/auth/authorize?flow=${uid}`, expiresAt: new Date(Date.now() + 300000).toISOString(), pollAfterMs: 2000 }, headers: {} };
    if (path.endsWith('exchange')) return { status: 200, body: issued, headers: {} };
    assert.equal(givenToken, token);
    if (method === 'DELETE') { revoked = true; return { status: 204, body: null, headers: {} }; }
    return { status: revoked ? 401 : 200, body: issued, headers: {} };
  };
  const auth = new DesktopAuth('https://fixture.test', http, async url => { assert.equal(url, `https://fixture.test/api/desktop/v1/auth/authorize?flow=${uid}`); opened++; }, vault);
  const remote = new Remote(new RemoteContracts(JSON.parse(await readFile(join(__dirname, '../schema/ai-bodies.json'), 'utf8')) as Record<string, Record<string, unknown>>), http, auth.identity, auth.invalidate);
  await serveAssets(join(__dirname, '../ui'));
  const window = createWindow(false); const unregister = registerDesktop(window, auth, remote);
  try {
    await window.loadURL('gitviz://app/web/pages/search.html');
    phase = 'browser-flow';
    const pending = await window.webContents.executeJavaScript('desktop.auth.start()');
    assert.equal(pending.state, 'pending'); assert.equal(opened, 1);
    assert.equal(JSON.stringify(pending).includes('pollSecret'), false);
    assert.equal(JSON.stringify(pending).includes('verifier'), false);
    await new Promise(resolve => setTimeout(resolve, 2050));
    const active = await window.webContents.executeJavaScript('desktop.auth.status()');
    assert.equal(active.state, 'authenticated'); assert.equal(JSON.stringify(active).includes(token), false);
    assert.equal((await readFile(file)).includes(Buffer.from(token)), false);
    phase = 'restart';
    const restored = new DesktopAuth('https://fixture.test', http, async () => { throw new Error('UNEXPECTED_BROWSER'); }, vault);
    await restored.restore(); assert.equal(restored.identity()?.token, token);
    assert.equal((await window.webContents.executeJavaScript(`desktop.auth.start({url:'https://evil.test'})`)).state, 'authenticated');
    assert.equal(opened, 1);
    // Extra renderer arguments cannot become a server origin, token or browser URL.
    phase = 'logout';
    await window.webContents.executeJavaScript('desktop.auth.logout()'); assert.equal(revoked, true); assert.equal(auth.identity(), null);
    await assert.rejects(readFile(file), { code: 'ENOENT' });
    process.stdout.write('PASS P03 real sandbox auth flow/status/logout, no renderer token, actual DPAPI persistence/restart/revocation\n');
  } finally { unregister(); window.destroy(); clearTimeout(deadline); await rm(directory, { recursive: true, force: true }); }
}).then(() => app.exit(0)).catch(() => { process.stderr.write(`FAIL P03 auth (${phase})\n`); app.exit(1); });
