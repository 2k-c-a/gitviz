import { ipcMain, type BrowserWindow } from 'electron';
import { trustedSender } from './ipc';
import type { DesktopAuth } from './auth';
import type { Remote } from './remote';
import type { UpdateService } from './update';

export function registerDesktop(window: BrowserWindow, auth: DesktopAuth | null, remote: Remote | null, update?: UpdateService): () => void {
  const channels = ['desktop:auth-start', 'desktop:auth-status', 'desktop:auth-logout', 'desktop:remote', 'desktop:update-status'];
  for (const channel of channels) ipcMain.handle(channel, async (event, ...args: unknown[]) => {
    if (!trustedSender(event, window) || args.length !== (channel === 'desktop:remote' ? 1 : 0)) throw new Error('ACCESS_DENIED');
    if (channel === 'desktop:update-status') { if (!update) throw new Error('UPDATE_UNAVAILABLE'); return update.status(); }
    if (!auth || !remote) throw new Error('SERVER_NOT_CONFIGURED');
    try {
      if (channel === 'desktop:remote') return await remote.request(args[0]);
      if (channel === 'desktop:auth-start') return await auth.start();
      if (channel === 'desktop:auth-logout') return await auth.logout();
      return await auth.status();
    } catch (error) {
      const allowed = /^(?:INVALID_REQUEST|AUTH_REQUIRED|ACCOUNT_CHANGED|BAD_RESPONSE|UNSUPPORTED_SERVER|AUTH_UNAVAILABLE|BROWSER_UNAVAILABLE|RATE_LIMITED|NETWORK_UNAVAILABLE|REVOCATION_REQUIRED|STORAGE_UNAVAILABLE|BUSY)$/;
      // eslint-disable-next-line preserve-caught-error -- Never expose credentials, raw HTTP exceptions or OS paths.
      throw new Error(error instanceof Error && allowed.test(error.message) ? error.message : 'REMOTE_UNAVAILABLE');
    }
  });
  return () => { for (const channel of channels) ipcMain.removeHandler(channel); };
}
