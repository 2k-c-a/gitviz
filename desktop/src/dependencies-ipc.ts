import { app, ipcMain, net, shell, type BrowserWindow } from 'electron';
import { log, errorCode } from './app-log';
import { workspaceSender } from './ipc';
import { checkDependencies, defaultCheckEnvironment, defaultInstallerEnvironment, DependencyError, GIT_DOWNLOAD_PAGE, installGit,
  type DependencyReport } from './dependencies';

const CHANNELS = ['workspace:dependencies-check', 'workspace:dependencies-install', 'workspace:dependencies-relaunch', 'workspace:dependencies-open-download'];

/** Environment check / Git install for the workspace page. The renderer names only an item id; every path and URL is fixed here. */
export function registerDependencies(window: BrowserWindow, agentExecutable: string | null): () => void {
  let busy = false, disposed = false;
  const check = async (): Promise<DependencyReport> => checkDependencies(defaultCheckEnvironment(agentExecutable));
  const summary = (report: DependencyReport) => Object.fromEntries(report.items.map(item => [item.id, item.status]));
  const guard = (event: Electron.IpcMainInvokeEvent) => { if (disposed || !workspaceSender(event, window)) throw new Error('ACCESS_DENIED'); };
  ipcMain.handle('workspace:dependencies-check', async (event, ...args: unknown[]) => {
    guard(event);
    if (args.length) throw new Error('INVALID_REQUEST');
    const report = await check();
    log('info', 'dependency-check', { ready: report.ready, ...summary(report) });
    return report;
  });
  ipcMain.handle('workspace:dependencies-install', async (event, ...args: unknown[]) => {
    guard(event);
    const options = args[0] as { id?: unknown } | undefined;
    if (args.length !== 1 || !options || typeof options !== 'object' || Object.keys(options).length !== 1 || options.id !== 'git') throw new Error('INVALID_REQUEST');
    if (busy) throw new Error('BUSY');
    busy = true;
    const started = Date.now();
    try {
      // Only install what is actually missing or too old; a working Git is never reinstalled.
      const before = await check();
      if (before.items.find(item => item.id === 'git')?.status === 'ok') return before;
      log('info', 'dependency-install-start', { id: 'git', from: before.items.find(item => item.id === 'git')?.status ?? 'unknown' });
      const report = await installGit(defaultInstallerEnvironment(app.getPath('temp'), url => net.fetch(url, { redirect: 'follow', cache: 'no-store' }), check),
        value => { if (!disposed && !window.isDestroyed()) window.webContents.send('workspace:dependencies-progress', value); });
      log('info', 'dependency-install-done', { id: 'git', ms: Date.now() - started });
      return report;
    } catch (error) {
      const code = error instanceof DependencyError ? error.code : 'INSTALL_FAILED';
      log('error', 'dependency-install-failed', { id: 'git', code, cause: errorCode(error), ms: Date.now() - started });
      // eslint-disable-next-line preserve-caught-error -- only the code crosses IPC; causes may carry paths
      throw new Error(code);
    } finally { busy = false; }
  });
  // The agent resolves Git once when it starts, so a fresh install takes effect after a restart.
  ipcMain.handle('workspace:dependencies-relaunch', (event, ...args: unknown[]) => {
    guard(event);
    if (args.length) throw new Error('INVALID_REQUEST');
    if (busy) throw new Error('BUSY');
    log('info', 'dependency-relaunch');
    app.relaunch();
    app.quit();
    return true;
  });
  ipcMain.handle('workspace:dependencies-open-download', async (event, ...args: unknown[]) => {
    guard(event);
    if (args.length) throw new Error('INVALID_REQUEST');
    await shell.openExternal(GIT_DOWNLOAD_PAGE);
    return true;
  });
  return () => { disposed = true; for (const channel of CHANNELS) ipcMain.removeHandler(channel); };
}
