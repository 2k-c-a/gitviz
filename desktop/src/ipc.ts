import { ipcMain, type BrowserWindow, type IpcMainInvokeEvent } from 'electron';
import type { Health } from '../contracts/workspace';
import { isAppDocument } from './app-protocol';

export function trustedSender(event: IpcMainInvokeEvent, window: BrowserWindow): boolean {
  return !window.isDestroyed() && event.sender === window.webContents &&
    event.senderFrame === window.webContents.mainFrame && isAppDocument(event.senderFrame?.url ?? '');
}
export function workspaceSender(event: IpcMainInvokeEvent, window: BrowserWindow): boolean {
  if (!trustedSender(event, window)) return false;
  const raw = event.senderFrame?.url ?? '';
  const url = new URL(raw);
  return url.pathname === '/index.html' && url.href === raw;
}
export function registerHealth(window: BrowserWindow, health: () => Health): () => void {
  ipcMain.handle('workspace:health', (event, ...args: unknown[]) => {
    if (!workspaceSender(event, window) || args.length) throw new Error('ACCESS_DENIED');
    return health();
  });
  return () => ipcMain.removeHandler('workspace:health');
}
