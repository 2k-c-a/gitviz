import { dialog, ipcMain, type BrowserWindow } from 'electron';
import type { RemoteWorkspace } from './remote-workspace';
import { workspaceSender } from './ipc';

export async function confirmRemote(window: BrowserWindow, detail: string): Promise<boolean> {
  return (await dialog.showMessageBox(window, { type: 'question', title: '로컬·서버 저장소 연결', message: '이 계정의 서버 저장소와 연결할까요?', detail,
    buttons: ['취소', '연결'], defaultId: 0, cancelId: 0 })).response === 1;
}
export function registerRemoteWorkspace(window: BrowserWindow, service: RemoteWorkspace | null): () => void {
  const channels: string[] = [];
  for (const [channel, keys, action] of [
    ['workspace:connect-remote', ['projectId', 'repositoryId'], async (v: Record<string, unknown>) => service!.connect(String(v.projectId), Number(v.repositoryId))],
    ['workspace:remote-connection', ['projectId'], async (v: Record<string, unknown>) => service!.status(String(v.projectId))],
    ['workspace:open-remote-analysis', ['projectId', 'analysisId'], async (v: Record<string, unknown>) => {
      const url = await service!.open(String(v.projectId), String(v.analysisId));
      if (window.isDestroyed()) throw new Error('ACCESS_DENIED');
      await window.loadURL(url);
      return { opened: true };
    }],
  ] as const) {
    channels.push(channel);
    ipcMain.handle(channel, async (event, ...args: unknown[]) => {
      if (!workspaceSender(event, window)) throw new Error('ACCESS_DENIED');
      if (!service) throw new Error('AUTH_UNAVAILABLE');
      const value = args[0];
      if (args.length !== 1 || !value || typeof value !== 'object' || Array.isArray(value)) throw new Error('INVALID_REQUEST');
      const v = value as Record<string, unknown>;
      if (Object.keys(v).length !== keys.length || keys.some(key => !(key in v)) || Object.keys(v).some(key => !(keys as readonly string[]).includes(key)) ||
        typeof v.projectId !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v.projectId) ||
        ('repositoryId' in v && (!Number.isSafeInteger(v.repositoryId) || Number(v.repositoryId) <= 0)) ||
        ('analysisId' in v && (typeof v.analysisId !== 'string' || !/^[0-9a-f-]{36}$/.test(v.analysisId)))) throw new Error('INVALID_REQUEST');
      try { return await action(v); }
      catch (error) {
        // eslint-disable-next-line preserve-caught-error -- renderer receives classification only
        throw new Error(error instanceof Error && /^[A-Z_]+$/.test(error.message) ? error.message : 'INVALID_REQUEST');
      }
    });
  }
  return () => { service?.dispose(); for (const channel of channels) ipcMain.removeHandler(channel); };
}
