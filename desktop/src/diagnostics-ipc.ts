import { app, dialog, ipcMain, type BrowserWindow } from 'electron';
import { open } from 'node:fs/promises';
import type { Health } from '../contracts/workspace';
import { workspaceSender } from './ipc';
import { diagnostics } from './diagnostics';

export function registerDiagnostics(window: BrowserWindow, health: () => Health,
  select: () => Promise<string | null> = async () => {
    const result = await dialog.showSaveDialog(window, { title: '최소 진단 정보 저장', defaultPath: 'gitviz-diagnostics.json', filters: [{ name: 'JSON', extensions: ['json'] }] });
    return result.canceled || !result.filePath ? null : result.filePath;
  }): () => void {
  let busy = false, disposed = false;
  ipcMain.handle('workspace:export-diagnostics', async (event, ...args: unknown[]) => {
    if (!workspaceSender(event, window) || args.length || disposed) throw new Error('ACCESS_DENIED');
    if (busy) throw new Error('BUSY');
    busy = true;
    try {
      const selected = await select();
      if (selected === null) return { exported: false };
      if (disposed || window.isDestroyed()) throw new Error('ACCESS_DENIED');
      const bytes = diagnostics(health(), app.getVersion());
      // Native path selection grants this one new file only; never overwrite or follow a destination link.
      const file = await open(selected, 'wx', 0o600);
      try { await file.writeFile(bytes, 'utf8'); await file.sync(); } finally { await file.close(); }
      return { exported: true };
    } catch (error) {
      // eslint-disable-next-line preserve-caught-error -- diagnostics errors must not expose destination paths
      throw new Error(error instanceof Error && error.message === 'ACCESS_DENIED' ? 'ACCESS_DENIED' : 'DIAGNOSTICS_UNAVAILABLE');
    } finally { busy = false; }
  });
  return () => { disposed = true; ipcMain.removeHandler('workspace:export-diagnostics'); };
}
