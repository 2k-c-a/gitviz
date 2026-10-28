import { dialog, ipcMain, type BrowserWindow } from 'electron';
import { randomUUID } from 'node:crypto';
import { Agent } from './agent';
import { Contracts } from './contracts';
import { workspaceSender } from './ipc';
import type { Operation } from '../contracts/workspace';

export interface NativeProjectDialogs {
  select(window: BrowserWindow): Promise<string | null>;
  confirm(window: BrowserWindow, message: string, detail: string): Promise<boolean>;
}
const nativeDialogs: NativeProjectDialogs = {
  async select(window) { const result = await dialog.showOpenDialog(window, { properties: ['openDirectory'], title: '로컬 Git 프로젝트 선택' }); return result.canceled || result.filePaths.length !== 1 ? null : result.filePaths[0]!; },
  async confirm(window, message, detail) { return (await dialog.showMessageBox(window, { type: 'question', buttons: ['취소', '허용'], defaultId: 0, cancelId: 0, message, detail })).response === 1; },
};
export function registerProjects(window: BrowserWindow, agent: Pick<Agent, 'call'>, contracts: Contracts, native: NativeProjectDialogs = nativeDialogs): () => void {
  const channels: string[] = [];
  let selecting = false;
  function handle(channel: string, action: (...args: unknown[]) => Promise<unknown>): void {
    channels.push(channel);
    ipcMain.handle(channel, async (event, ...args: unknown[]) => {
      if (!workspaceSender(event, window)) throw new Error('ACCESS_DENIED');
      try { return await action(...args); }
      catch (error) {
        // eslint-disable-next-line preserve-caught-error -- IPC errors must not retain native paths or child diagnostics.
        throw new Error(error instanceof Error && /^[A-Z_]+$/.test(error.message) ? error.message : 'INVALID_REQUEST');
      }
    });
  }
  handle('workspace:select-project', async (...args) => {
    if (args.length) throw new Error('INVALID_REQUEST');
    if (selecting) throw new Error('BUSY');
    selecting = true;
    try {
      const selected = await native.select(window);
      if (selected === null) return null;
      let selectedRoot = selected;
      let expandedRoot = false;
      let reconnectApproved = false;
      const metadataScopes: string[] = [];
      for (let step = 0; step < 5; step++) {
        const inspected = await agent.call('workspace.projects.selectAndRegister', { phase: 'inspect', selectedRoot, metadataScopes, expandedRoot, reconnectApproved }, { idempotencyKey: randomUUID() });
        if (typeof inspected.selectionId === 'string') {
          return await agent.call('workspace.projects.selectAndRegister', { phase: 'register', selectionId: inspected.selectionId }, { idempotencyKey: randomUUID() });
        }
        const confirmation = inspected.confirmation as { kind: string; path: string };
        const accepted = await native.confirm(window, confirmation.kind === 'ROOT_GRANT_REQUIRED' ? '저장소 최상위 폴더로 범위를 확대할까요?' : confirmation.kind === 'PROJECT_RECONNECT_REQUIRED' ? '이동한 프로젝트를 새 위치에 재연결할까요?' : '외부 Git 메타데이터 접근을 허용할까요?', `${confirmation.path}\n파일 본문 접근은 선택한 프로젝트 root로 제한됩니다.`);
        if (!accepted) return null;
        if (confirmation.kind === 'ROOT_GRANT_REQUIRED') { selectedRoot = confirmation.path; expandedRoot = true; }
        else if (confirmation.kind === 'PROJECT_RECONNECT_REQUIRED') reconnectApproved = true;
        else metadataScopes.push(confirmation.path);
      }
      throw new Error('ACCESS_DENIED');
    } finally { selecting = false; }
  });
  const methods: [string, Operation][] = [
    ['list-projects', 'workspace.projects.list'], ['get-project', 'workspace.projects.get'],
    ['update-settings', 'workspace.projects.updateSettings'], ['unregister-project', 'workspace.projects.unregister'], ['git-status', 'workspace.git.status'], ['set-editor', 'workspace.projects.setEditor'],
    ['review-preview', 'workspace.reviews.preview'], ['review-start', 'workspace.reviews.start'], ['review-get', 'workspace.reviews.get'], ['review-list', 'workspace.reviews.list'], ['review-cancel', 'workspace.reviews.cancel'], ['review-feedback', 'workspace.reviews.feedback'],
    ['capture-changes', 'workspace.changes.capture'], ['preview-file', 'workspace.files.preview'], ['detect-validation', 'workspace.validation.detect'], ['plan-validation', 'workspace.validation.plan'],
  ];
  for (const [name, operation] of methods) handle(`workspace:${name}`, async (...args) => {
    if (args.length !== 1 || !args[0] || typeof args[0] !== 'object' || Array.isArray(args[0])) throw new Error('INVALID_REQUEST');
    const value = args[0] as Record<string, unknown>;
    const { projectId, idempotencyKey, ...payload } = value;
    const request = { protocolVersion: 1, requestId: randomUUID(), operation, payload, ...(projectId !== undefined ? { projectId } : {}), ...(idempotencyKey !== undefined ? { idempotencyKey } : {}) };
    contracts.request(request);
    if (operation.endsWith('unregister')) {
      const project = await agent.call('workspace.projects.get', {}, { projectId: request.projectId });
      if (!await native.confirm(window, `${String(project.displayName)}의 로컬 등록을 해제할까요?`, '원본 프로젝트 폴더와 Git 파일은 보존됩니다.')) return null;
    }
    return await agent.call(operation, payload, { ...(request.projectId ? { projectId: request.projectId } : {}), ...(request.idempotencyKey ? { idempotencyKey: request.idempotencyKey } : {}) });
  });
  return () => { for (const channel of channels) ipcMain.removeHandler(channel); };
}
