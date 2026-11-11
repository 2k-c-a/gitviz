import { ipcMain, Notification, type BrowserWindow, type IpcMainInvokeEvent } from 'electron';
import { trustedSender } from './ipc';
import { errorCode, log } from './app-log';
import type { DesktopAuth } from './auth';
import type { CollabBundles } from './collab-bundle';
import type { CollabFiles } from './collab-files';
import type { Previews } from './collab-preview';
import type { CollabRunner } from './collab-run';
import type { CommunityLive, LiveEvent } from './community-live';
import { badgeOf, CommunityNotifier, communityUrl, showsChat, type NotificationSummary } from './community-notifications';
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
      log('warn', 'remote-failed', { channel, code: errorCode(error) });
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

/** Community notifications (2026-11-11): Windows notifications for new chat and schedule invitations on any screen,
 *  and unread counts (no text) for the sidebar badge. Clicking a notification opens that community's tab. */
export function registerCommunityNotifications(window: BrowserWindow, remote: Remote | null, auth: DesktopAuth | null): () => void {
  let last: NotificationSummary | null = null;
  const toasts = new Set<Notification>();  // Keep a reference until closed, or the click handler is collected.
  const send = (): void => { if (!window.isDestroyed()) window.webContents.send('desktop:community-notifications', badgeOf(last)); };
  const open = (repositoryId: number, tab: 'chat' | 'schedule'): void => {
    if (window.isDestroyed()) return;
    if (window.isMinimized()) window.restore();
    window.show(); window.focus();
    void window.loadURL(communityUrl(repositoryId, tab)).catch(() => {});
  };
  const notifier = remote && auth && Notification.isSupported() ? new CommunityNotifier({
    request: value => remote.request(value),
    account: () => auth.identity()?.generation ?? null,
    viewingChat: repositoryId => !window.isDestroyed() && window.isFocused() && !window.isMinimized()
      && showsChat(window.webContents.getURL(), repositoryId),
    publish: summary => { last = summary; send(); },
    show: toast => {
      const notification = new Notification({ title: toast.title, body: toast.body });
      toasts.add(notification);
      notification.on('click', () => { toasts.delete(notification); open(toast.repositoryId, toast.tab); });
      notification.on('close', () => toasts.delete(notification));
      notification.show();
    },
  }) : null;
  if (notifier) log('info', 'community-notifications', { enabled: true });
  notifier?.start();
  ipcMain.handle('desktop:community-notifications-refresh', (event, ...args: unknown[]) => {
    if (!trustedSender(event, window) || args.length) throw new Error('ACCESS_DENIED');
    notifier?.refresh();
    return badgeOf(last);
  });
  window.webContents.on('did-finish-load', send);
  return () => {
    notifier?.stop(); toasts.clear();
    ipcMain.removeHandler('desktop:community-notifications-refresh');
    if (!window.isDestroyed()) window.webContents.off('did-finish-load', send);
  };
}

/** Co-editing file access (AD-29) and file sets with preview (AD-34) for the community page only.
 *  Any navigation or closing the window forgets every open file and set and closes the previews. */
export function registerCollabFiles(window: BrowserWindow, files: CollabFiles, bundles: CollabBundles | null = null, previews: Previews | null = null,
  runner: CollabRunner | null = null): () => void {
  const fromCommunity = (event: IpcMainInvokeEvent) => {
    if (!trustedSender(event, window)) return false;
    try { return new URL(event.senderFrame?.url ?? '').pathname === '/web/pages/community.html'; } catch { return false; }
  };
  const channels: Record<string, (args: unknown[]) => unknown> = {
    'desktop:collab-open': args => { if (args.length) throw new Error('ACCESS_DENIED'); return files.open(); },
    'desktop:collab-save': args => { if (args.length !== 1) throw new Error('ACCESS_DENIED'); return files.save(args[0]); },
    'desktop:collab-close': args => { if (args.length !== 1) throw new Error('ACCESS_DENIED'); return files.close(args[0]); },
  };
  const one = (run: (value: unknown) => unknown) => (args: unknown[]) => { if (args.length !== 1) throw new Error('ACCESS_DENIED'); return run(args[0]); };
  if (bundles) Object.assign(channels, {
    'desktop:collab-bundle-open': (args: unknown[]) => { if (args.length) throw new Error('ACCESS_DENIED'); return bundles.open(); },
    'desktop:collab-bundle-widen': one(value => bundles.widen(value)),
    'desktop:collab-bundle-add': one(value => bundles.add(value)),
    'desktop:collab-bundle-scan': one(value => bundles.scan(value)),
    'desktop:collab-bundle-approve': one(value => bundles.approve(value)),
    'desktop:collab-bundle-save': one(value => bundles.save(value)),
    'desktop:collab-bundle-create': one(value => bundles.create(value)),
    'desktop:collab-bundle-close': one(value => bundles.close(value)),
  });
  if (previews) Object.assign(channels, {
    'desktop:collab-preview-open': one(value => previews.open(value)),
    'desktop:collab-preview-update': one(value => previews.update(value)),
    'desktop:collab-preview-close': one(value => previews.close(value)),
  });
  // Each participant runs the shared code in their own app; nothing is sent anywhere (AD-34 phase 2·3).
  if (runner) Object.assign(channels, {
    'desktop:collab-run-runtimes': one(value => runner.runtimes(value)),
    'desktop:collab-run': one(value => runner.run(value)),
    'desktop:collab-run-stop': (args: unknown[]) => { if (args.length) throw new Error('ACCESS_DENIED'); return runner.stop(); },
  });
  for (const [channel, run] of Object.entries(channels)) ipcMain.handle(channel, async (event, ...args: unknown[]) => {
    if (!fromCommunity(event)) throw new Error('ACCESS_DENIED');
    try { return await run(args); }
    catch (error) {
      log('warn', 'collab-failed', { channel, code: errorCode(error) });
      const allowed = /^(?:ACCESS_DENIED|INVALID_REQUEST|INVALID_FILE|FILE_TOO_LARGE|NOT_TEXT|NOT_OPEN|FILE_CHANGED|SAVE_FAILED|NOT_ALLOWED|OUTSIDE_ROOT|CANNOT_WIDEN|NOT_PENDING|LIMIT|EXISTS|BUSY|STOPPED|NO_COMPILER|SANDBOX_UNAVAILABLE|RUN_UNAVAILABLE)$/;
      const fallback = channel === 'desktop:collab-open' || channel === 'desktop:collab-bundle-open' ? 'INVALID_FILE' : channel.startsWith('desktop:collab-preview-') ? 'PREVIEW_FAILED'
        : channel.startsWith('desktop:collab-run') ? 'RUN_UNAVAILABLE' : 'SAVE_FAILED';
      // eslint-disable-next-line preserve-caught-error -- Never expose OS paths or raw file system errors.
      throw new Error(error instanceof Error && allowed.test(error.message) ? error.message : fallback);
    }
  });
  const forget = () => { files.closeAll(); bundles?.closeAll(); previews?.closeAll(); runner?.stop(); };
  const navigate = (details: { isMainFrame: boolean; isSameDocument: boolean }) => { if (details.isMainFrame && !details.isSameDocument) forget(); };
  window.webContents.on('did-start-navigation', navigate);
  return () => {
    forget();
    if (!window.isDestroyed()) window.webContents.off('did-start-navigation', navigate);
    for (const channel of Object.keys(channels)) ipcMain.removeHandler(channel);
  };
}
