import { BrowserWindow, screen, dialog, shell } from 'electron';
import { dirname, join, parse } from 'node:path';
import { existsSync, lstatSync, openSync, closeSync, readFileSync, writeFileSync, fsyncSync, renameSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { isAppDocument } from './app-protocol';

export function externalRepositoryLink(value: string): string | null {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.hostname !== 'github.com' || url.port || url.username || url.password || url.search ||
      !/^\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+(?:\/(?:tree|blob|commit|pull)\/[A-Za-z0-9_./%-]+)?\/?$/.test(url.pathname) ||
      (url.hash && !/^#L\d+(?:-L\d+)?$/.test(url.hash))) return null;
    return url.href;
  } catch { return null; }
}

function rejectStateLinks(path: string): void {
  let current = path;
  for (;;) {
    if (existsSync(current) && lstatSync(current).isSymbolicLink()) throw new Error('ACCESS_DENIED');
    if (current === parse(current).root) break;
    current = dirname(current);
  }
}

export function createWindow(show = true, stateFile?: string): BrowserWindow {
  let bounds: { x?: number; y?: number; width: number; height: number } = { width: 900, height: 640 };
  let maximized = false;
  if (stateFile) {
    try {
      rejectStateLinks(stateFile);
      const info = lstatSync(stateFile);
      if (!info.isFile() || info.size > 2048) throw new Error('INVALID_REQUEST');
      const saved = JSON.parse(readFileSync(stateFile, 'utf8')) as Record<string, unknown>;
      if (!['x', 'y', 'width', 'height'].every(key => Number.isSafeInteger(saved[key])) || typeof saved.maximized !== 'boolean') throw new Error('INVALID_REQUEST');
      const rectangle = { x: saved.x as number, y: saved.y as number, width: saved.width as number, height: saved.height as number };
      const area = screen.getDisplayMatching(rectangle).workArea;
      const width = Math.max(600, Math.min(rectangle.width, area.width));
      const height = Math.max(400, Math.min(rectangle.height, area.height));
      bounds = { x: Math.max(area.x, Math.min(rectangle.x, area.x + area.width - width)), y: Math.max(area.y, Math.min(rectangle.y, area.y + area.height - height)), width, height };
      maximized = saved.maximized;
    } catch { /* Missing/invalid/off-policy window state uses safe display defaults. */ }
  }
  const window = new BrowserWindow({ ...bounds, minWidth: 600, minHeight: 400, show,
    webPreferences: { preload: join(__dirname, '../preload/preload.js'), nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, webviewTag: false, devTools: false } });
  window.webContents.setWindowOpenHandler(details => {
    const url = externalRepositoryLink(details.url);
    if (url) void dialog.showMessageBox(window, { type: 'question', title: 'GitHub 외부 링크', message: '시스템 브라우저에서 GitHub 링크를 여시겠습니까?',
      detail: url, buttons: ['취소', '열기'], defaultId: 0, cancelId: 0 }).then(async result => {
      if (result.response === 1 && !window.isDestroyed()) await shell.openExternal(url);
    }).catch(() => {});
    return { action: 'deny' };
  });
  window.webContents.on('will-navigate', (event, url) => { if (!isAppDocument(url)) event.preventDefault(); });
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  window.webContents.session.setPermissionCheckHandler(() => false);
  if (maximized) window.maximize();
  if (stateFile) window.on('close', () => {
    try {
      rejectStateLinks(stateFile);
      if (!existsSync(dirname(stateFile))) return;
      const temporary = `${stateFile}.${randomUUID()}.tmp`;
      const handle = openSync(temporary, 'wx', 0o600);
      try { writeFileSync(handle, JSON.stringify({ ...window.getNormalBounds(), maximized: window.isMaximized() })); fsyncSync(handle); }
      finally { closeSync(handle); }
      renameSync(temporary, stateFile);
    } catch { /* Window metadata failure must not block agent termination. */ }
  });
  return window;
}
