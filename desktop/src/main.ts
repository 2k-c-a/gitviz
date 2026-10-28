import { app, safeStorage, shell, type BrowserWindow } from 'electron';
import { join } from 'node:path';
import { readFileSync } from 'node:fs';
import { DesktopAuth, SessionVault } from './auth';
import { Remote, httpsClient } from './remote';
import { RemoteContracts } from './remote-contracts';
import { registerDesktop } from './desktop-ipc';
import { Agent } from './agent';
import { Contracts } from './contracts';
import { registerScheme, serveAssets, APP_URL } from './app-protocol';
import { registerHealth } from './ipc';
import { loadKey } from './secrets';
import { createWindow } from './window';
import { registerProjects } from './projects';
import { registerProposals } from './proposals';
import { registerImpact } from './impact';
import { registerValidation } from './validation';
import { registerEditor } from './editor';
import { registerNotifications } from './notifications';

registerScheme();
let window: BrowserWindow | undefined;
let agent: Agent | undefined;
let quitting = false;
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (window && !window.isDestroyed()) { if (window.isMinimized()) window.restore(); window.focus(); } });
  app.on('before-quit', event => {
    if (quitting) return;
    event.preventDefault(); quitting = true;
    void (agent?.stop() ?? Promise.resolve()).finally(() => app.quit());
  });
  app.on('window-all-closed', () => app.quit());
  void app.whenReady().then(async () => {
    await serveAssets(join(__dirname, '../ui'));
    const contracts = new Contracts(join(__dirname, '../schema'));
    agent = new Agent(contracts);
    const directory = join(app.getPath('userData'), 'workspace');
    window = createWindow(true, join(directory, 'window.json'));
    const unregister = registerHealth(window, () => agent!.health());
    window.once('closed', unregister);
    window.once('closed', registerProjects(window, agent, contracts));
    window.once('closed', registerProposals(window, agent, contracts));
    window.once('closed', registerImpact(window, agent, contracts));
    window.once('closed', registerValidation(window, agent, contracts));
    window.once('closed', registerEditor(window, agent, contracts, directory));
    window.once('closed', registerNotifications(window, agent, contracts));
    // Deployment-controlled HTTPS origin only; never taken from renderer input.
    const origin = process.env.GITVIZ_SERVER_ORIGIN;
    let auth: DesktopAuth | null = null;
    let remote: Remote | null = null;
    if (origin) {
      const http = httpsClient(origin);
      auth = new DesktopAuth(origin, http, url => shell.openExternal(url), new SessionVault(join(directory, 'desktop-session.dpapi'), origin, {
        available: () => safeStorage.isAsyncEncryptionAvailable(), encrypt: value => safeStorage.encryptStringAsync(value),
        decrypt: async value => (await safeStorage.decryptStringAsync(value)).result,
      }));
      const bodies = JSON.parse(readFileSync(join(__dirname, '../schema/ai-bodies.json'), 'utf8')) as Record<string, Record<string, unknown>>;
      remote = new Remote(new RemoteContracts(bodies), http, auth.identity, auth.invalidate);
    }
    window.once('closed', registerDesktop(window, auth, remote));
    await window.loadURL(APP_URL);
    // A fixed packaged executable only: no renderer/config-provided executable path.
    const executable = join(process.resourcesPath, 'agent', 'gitviz-workspace-agent.exe');
    try {
      await agent.start(executable, directory, () => loadKey(join(directory, 'key.dpapi'), {
        available: () => safeStorage.isAsyncEncryptionAvailable(),
        encrypt: value => safeStorage.encryptStringAsync(value),
        decrypt: async value => (await safeStorage.decryptStringAsync(value)).result,
      }));
      await auth?.restore();
    } catch { /* Health exposes only unavailable, never exceptions or child stderr. */ }
  }).catch(() => app.quit());
}
