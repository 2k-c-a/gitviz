import { ipcMain, type BrowserWindow, type IpcMainInvokeEvent } from 'electron';
import { trustedSender } from './ipc';
import type { DesktopAuth } from './auth';
import type { CollabFiles } from './collab-files';
import type { CommunityLive, LiveEvent } from './community-live';
import type { Remote } from './remote';
import type { AiKeyVault } from './secrets';
import type { UpdateService } from './update';

export function registerDesktop(window: BrowserWindow, auth: DesktopAuth | null, remote: Remote | null, update?: UpdateService,
  keys: Pick<AiKeyVault, 'status'> | null = null): () => void {
  const channels = ['desktop:auth-start', 'desktop:auth-status', 'desktop:auth-logout', 'desktop:remote', 'desktop:update-status', 'desktop:ai-key-status'];
  for (const channel of channels) ipcMain.handle(channel, async (event, ...args: unknown[]) => {
    if (!trustedSender(event, window) || args.length !== (channel === 'desktop:remote' ? 1 : 0)) throw new Error('ACCESS_DENIED');
    if (channel === 'desktop:update-status') { if (!update) throw new Error('UPDATE_UNAVAILABLE'); return update.status(); }
    // Booleans only: whether a key is saved per provider, never key text.
    if (channel === 'desktop:ai-key-status') {
      try { return keys ? await keys.status() : { anthropic: false, openai: false, gemini: false }; }
      catch { throw new Error('STORAGE_UNAVAILABLE'); }
    }
    if (!auth || !remote) throw new Error('SERVER_NOT_CONFIGURED');
    try {
      if (channel === 'desktop:remote') return await remote.request(args[0]);
      if (channel === 'desktop:auth-start') return await auth.start();
      if (channel === 'desktop:auth-logout') return await auth.logout();
      return await auth.status();
    } catch (error) {
      const allowed = /^(?:INVALID_REQUEST|AUTH_REQUIRED|ACCOUNT_CHANGED|BAD_RESPONSE|UNSUPPORTED_SERVER|AUTH_UNAVAILABLE|BROWSER_UNAVAILABLE|RATE_LIMITED|NETWORK_UNAVAILABLE|REVOCATION_REQUIRED|STORAGE_UNAVAILABLE|BUSY|AI_KEY_REQUIRED)$/;
      // eslint-disable-next-line preserve-caught-error -- Never expose credentials, raw HTTP exceptions or OS paths.
      throw new Error(error instanceof Error && allowed.test(error.message) ? error.message : 'REMOTE_UNAVAILABLE');
    }
  });
  return () => { for (const channel of channels) ipcMain.removeHandler(channel); };
}

/** Chat live events for the page in this window only; any page navigation or sign-out ends the stream. */
export function registerCommunityLive(window: BrowserWindow, create: ((emit: (event: LiveEvent) => void) => CommunityLive) | null): () => void {
  const live = create?.(event => { if (!window.isDestroyed()) window.webContents.send('desktop:community-event', event); }) ?? null;
  ipcMain.handle('desktop:community-subscribe', (event, ...args: unknown[]) => {
    if (!trustedSender(event, window) || args.length !== 1) throw new Error('ACCESS_DENIED');
    if (!live) throw new Error('SERVER_NOT_CONFIGURED');
    return live.subscribe(args[0]);
  });
  ipcMain.handle('desktop:community-unsubscribe', (event, ...args: unknown[]) => {
    if (!trustedSender(event, window) || args.length) throw new Error('ACCESS_DENIED');
    return live?.unsubscribe() ?? { subscribed: false };
  });
  const navigate = (details: { isMainFrame: boolean; isSameDocument: boolean }) => { if (details.isMainFrame && !details.isSameDocument) live?.unsubscribe(); };
  window.webContents.on('did-start-navigation', navigate);
  return () => {
    live?.unsubscribe();
    if (!window.isDestroyed()) window.webContents.off('did-start-navigation', navigate);
    ipcMain.removeHandler('desktop:community-subscribe'); ipcMain.removeHandler('desktop:community-unsubscribe');
  };
}

/** Co-editing file access (AD-29) for the community page only. Any navigation or closing the window forgets every open file. */
export function registerCollabFiles(window: BrowserWindow, files: CollabFiles): () => void {
  const fromCommunity = (event: IpcMainInvokeEvent) => {
    if (!trustedSender(event, window)) return false;
    try { return new URL(event.senderFrame?.url ?? '').pathname === '/web/pages/community.html'; } catch { return false; }
  };
  const channels: Record<string, (args: unknown[]) => unknown> = {
    'desktop:collab-open': args => { if (args.length) throw new Error('ACCESS_DENIED'); return files.open(); },
    'desktop:collab-save': args => { if (args.length !== 1) throw new Error('ACCESS_DENIED'); return files.save(args[0]); },
    'desktop:collab-close': args => { if (args.length !== 1) throw new Error('ACCESS_DENIED'); return files.close(args[0]); },
  };
  for (const [channel, run] of Object.entries(channels)) ipcMain.handle(channel, async (event, ...args: unknown[]) => {
    if (!fromCommunity(event)) throw new Error('ACCESS_DENIED');
    try { return await run(args); }
    catch (error) {
      const allowed = /^(?:ACCESS_DENIED|INVALID_REQUEST|INVALID_FILE|FILE_TOO_LARGE|NOT_TEXT|NOT_OPEN|FILE_CHANGED|SAVE_FAILED)$/;
      // eslint-disable-next-line preserve-caught-error -- Never expose OS paths or raw file system errors.
      throw new Error(error instanceof Error && allowed.test(error.message) ? error.message : channel === 'desktop:collab-open' ? 'INVALID_FILE' : 'SAVE_FAILED');
    }
  });
  const navigate = (details: { isMainFrame: boolean; isSameDocument: boolean }) => { if (details.isMainFrame && !details.isSameDocument) files.closeAll(); };
  window.webContents.on('did-start-navigation', navigate);
  return () => {
    files.closeAll();
    if (!window.isDestroyed()) window.webContents.off('did-start-navigation', navigate);
    for (const channel of Object.keys(channels)) ipcMain.removeHandler(channel);
  };
}
