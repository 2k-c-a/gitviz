import { app, dialog, safeStorage, shell, type BrowserWindow } from 'electron';
import { join } from 'node:path';
import { readFileSync } from 'node:fs';
import { getCACertificates, setDefaultCACertificates } from 'node:tls';
import { DesktopAuth, SessionVault } from './auth';
import { Remote, httpsClient } from './remote';
import { RemoteContracts } from './remote-contracts';
import { registerCollabFiles, registerCommunityLive, registerDesktop } from './desktop-ipc';
import { CollabFiles } from './collab-files';
import { CommunityLive, streamClient } from './community-live';
import { Agent } from './agent';
import { Contracts } from './contracts';
import { registerScheme, serveAssets, APP_URL } from './app-protocol';
import { registerHealth } from './ipc';
import { AiKeyVault, loadKey } from './secrets';
import { createWindow } from './window';
import { readEmbeddedCertificate, readEmbeddedOrigin, serverOrigin } from './server-origin';
import { registerProjects } from './projects';
import { registerProposals } from './proposals';
import { registerImpact } from './impact';
import { registerValidation } from './validation';
import { registerEditor } from './editor';
import { registerNotifications } from './notifications';
import { RemoteWorkspace } from './remote-workspace';
import { confirmRemote, registerRemoteWorkspace } from './remote-workspace-ipc';
import { registerDiagnostics } from './diagnostics-ipc';
import { CloudReview } from './cloud-review';
import { HistoricalImpact } from './historical-impact';
import { createUpdateService, DISABLED_UPDATE_POLICY } from './update';
import { parentProcess, runSquirrel, squirrelAction } from './squirrel';
import { communityJoinUrl, inviteFromArgv, protocolRegistration } from './deep-link';
import { DEBUG_EXIT_CODE, forbiddenDebugSwitches } from './debug-guard';

// First thing: a packaged release refuses to run with a debugging port (see debug-guard.ts). process.exit stops this module synchronously.
if (forbiddenDebugSwitches(app.isPackaged, process.argv).length > 0) process.exit(DEBUG_EXIT_CODE);
registerScheme();
let window: BrowserWindow | undefined;
let agent: Agent | undefined;
let quitting = false;
const squirrel = squirrelAction(process.platform, process.argv, process.execPath, { localAppData: process.env.LOCALAPPDATA, parent: () => parentProcess(process.ppid) });
const protocol = protocolRegistration(app.isPackaged, process.platform, process.execPath);
// Uninstall removes the invite link handler too, so nothing of the app stays in the user's registry.
if (squirrel?.args[0] === '--removeShortcut' && protocol) { try { app.removeAsDefaultProtocolClient(protocol.scheme, protocol.path, protocol.args); } catch { /* best effort */ } }
if (squirrel) runSquirrel(squirrel, () => app.quit());
else if (!app.requestSingleInstanceLock()) app.quit();
else {
  // Per-user (HKCU) registration, refreshed on every start so it follows updates.
  if (protocol) { try { app.setAsDefaultProtocolClient(protocol.scheme, protocol.path, protocol.args); } catch { /* links then simply do not open the app */ } }
  let pendingInvite = inviteFromArgv(process.argv);
  let started = false;
  const openInvite = (token: string | null): void => {
    if (!token || !window || window.isDestroyed()) return;
    void window.loadURL(communityJoinUrl(token)).catch(() => {});
  };
  app.on('second-instance', (_event, argv) => {
    if (window && !window.isDestroyed()) { if (window.isMinimized()) window.restore(); window.focus(); }
    // A link clicked while the app runs arrives here; before the first page loads it waits for startup.
    const token = inviteFromArgv(argv);
    if (started) openInvite(token); else if (token) pendingInvite = token;
  });
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
    window.once('closed', registerDiagnostics(window, () => agent!.health()));
    window.once('closed', registerProposals(window, agent, contracts));
    window.once('closed', registerValidation(window, agent, contracts));
    window.once('closed', registerEditor(window, agent, contracts, directory));
    window.once('closed', registerNotifications(window, agent, contracts));
    // Deployment-controlled HTTPS origin only (embedded at build time, see server-origin.ts); never taken from renderer input.
    const serverConfig = join(__dirname, '../config/server.json');
    const embeddedOrigin = readEmbeddedOrigin(serverConfig);
    const origin = serverOrigin(app.isPackaged, embeddedOrigin, process.env.GITVIZ_SERVER_ORIGIN);
    // A local staging build also trusts its own server's self-signed certificate, so it works when started from Explorer.
    const certificate = origin && origin === embeddedOrigin ? readEmbeddedCertificate(serverConfig, origin) : null;
    if (certificate) setDefaultCACertificates([...getCACertificates('default'), certificate]);
    let auth: DesktopAuth | null = null;
    let remote: Remote | null = null;
    // BYOK provider keys (DPAPI): local review and AI analysis share them. Main attaches them; the renderer gets booleans.
    const aiKeys = new AiKeyVault(join(directory, 'ai-keys.dpapi'), {
      available: () => safeStorage.isAsyncEncryptionAvailable(), encrypt: value => safeStorage.encryptStringAsync(value),
      decrypt: async value => (await safeStorage.decryptStringAsync(value)).result,
    });
    if (origin) {
      const http = httpsClient(origin);
      auth = new DesktopAuth(origin, http, url => shell.openExternal(url), new SessionVault(join(directory, 'desktop-session.dpapi'), origin, {
        available: () => safeStorage.isAsyncEncryptionAvailable(), encrypt: value => safeStorage.encryptStringAsync(value),
        decrypt: async value => (await safeStorage.decryptStringAsync(value)).result,
      }));
      const bodies = JSON.parse(readFileSync(join(__dirname, '../schema/ai-bodies.json'), 'utf8')) as Record<string, Record<string, unknown>>;
      remote = new Remote(new RemoteContracts(bodies), http, auth.identity, auth.invalidate, aiKeys);
    }
    window.once('closed', registerDesktop(window, auth, remote, createUpdateService(app.getVersion(), DISABLED_UPDATE_POLICY), aiKeys));
    const signedIn = auth;
    window.once('closed', registerCollabFiles(window, new CollabFiles(
      async () => { const picked = await dialog.showOpenDialog(window!, { title: '공동 편집할 파일 선택', properties: ['openFile'] }); return picked.canceled ? null : picked.filePaths[0] ?? null; },
      async name => (await dialog.showMessageBox(window!, { type: 'warning', title: '파일이 바뀌었습니다', message: `${name} 파일이 공유한 뒤 다른 곳에서 바뀌었습니다. 공동 편집한 내용으로 덮어쓸까요?`,
        buttons: ['취소', '덮어쓰기'], defaultId: 0, cancelId: 0 })).response === 1)));
    window.once('closed', registerCommunityLive(window, signedIn && origin
      ? emit => new CommunityLive(streamClient(origin), signedIn.identity, signedIn.invalidate, emit) : null));
    const remoteWorkspace = auth && remote && origin ? new RemoteWorkspace(agent, auth, remote, origin, detail => confirmRemote(window!, detail)) : null;
    window.once('closed', registerRemoteWorkspace(window, remoteWorkspace));
    // Cloud BYOK no longer needs sign-in or a project connection (owner decision 2026-11-01).
    const cloud = new CloudReview(agent);
    window.once('closed', registerProjects(window, agent, contracts, undefined, cloud, aiKeys));
    window.once('closed', registerImpact(window, agent, contracts, remoteWorkspace && remote ? new HistoricalImpact(agent, remoteWorkspace, remote) : null));
    const invite = pendingInvite; pendingInvite = null; started = true;
    await window.loadURL(invite ? communityJoinUrl(invite) : APP_URL);
    // A fixed packaged executable only: no renderer/config-provided executable path.
    const executable = join(process.resourcesPath, 'agent', 'gitviz-workspace-agent.exe');
    try {
      await agent.start(executable, directory, () => loadKey(join(directory, 'key.dpapi'), {
        available: () => safeStorage.isAsyncEncryptionAvailable(),
        encrypt: value => safeStorage.encryptStringAsync(value),
        decrypt: async value => (await safeStorage.decryptStringAsync(value)).result,
      }));
      await auth?.restore();
      await remoteWorkspace?.resume();
    } catch { /* Health exposes only unavailable, never exceptions or child stderr. */ }
  }).catch(() => app.quit());
}
