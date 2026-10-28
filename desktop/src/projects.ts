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
interface ReviewPreviewConsent {
  projectId: unknown; previewHash: unknown; manifestHash: unknown; maxEstimatedCost: unknown;
  destination: unknown; executionLocation: unknown; kind: unknown; files: string[];
  requestBytes: unknown; requestHash: string; expiresAt: number;
}
export class ReviewConsent {
  private readonly previews = new Map<string, ReviewPreviewConsent>();
  private readonly intents = new Map<string, { body: string; expiresAt: number }>();
  private confirming = false;
  private disposed = false;
  constructor(private readonly closed: () => boolean, private readonly now = Date.now) {}
  dispose(): void { this.disposed = true; this.previews.clear(); this.intents.clear(); }
  private cleanup(): void {
    for (const [key, value] of this.previews) if (value.expiresAt <= this.now()) this.previews.delete(key);
    for (const [key, value] of this.intents) if (value.expiresAt <= this.now()) this.intents.delete(key);
  }
  remember(value: Record<string, unknown>): void {
    if (this.disposed || this.closed()) throw new Error('ACCESS_DENIED');
    this.cleanup();
    const key = String(value.previewId);
    if (!this.previews.has(key) && this.previews.size >= 32) throw new Error('BUSY');
    this.previews.set(key, {
      projectId: value.projectId, previewHash: value.previewHash, manifestHash: value.manifestHash,
      maxEstimatedCost: value.maxEstimatedCost, destination: value.destination,
      executionLocation: value.executionLocation, kind: value.kind,
      files: (value.files as { path: string }[]).map(file => file.path),
      requestBytes: value.requestBytes, requestHash: String(value.requestHash),
      expiresAt: Math.min(Number(value.expiresAt) * 1000, this.now() + 300000),
    });
  }
  async start(value: Record<string, unknown>, confirm: (detail: string) => Promise<boolean>, forward: () => Promise<unknown>): Promise<unknown> {
    if (this.disposed || this.closed()) throw new Error('ACCESS_DENIED');
    this.cleanup();
    const key = `${String(value.projectId)}:${String(value.idempotencyKey)}`;
    const body = JSON.stringify(Object.keys(value).sort().map(name => [name, value[name]]));
    const intent = this.intents.get(key);
    if (intent && intent.body !== body) throw new Error('CONFLICT');
    if (this.confirming) throw new Error('BUSY');
    if (intent) return await forward();
    const preview = this.previews.get(String(value.previewId));
    if (!preview || preview.expiresAt <= this.now() ||
      ['projectId', 'previewHash', 'manifestHash', 'maxEstimatedCost'].some(name => preview[name as keyof ReviewPreviewConsent] !== value[name])) throw new Error('APPROVAL_REQUIRED');
    if (this.intents.size >= 32) throw new Error('BUSY');
    this.confirming = true;
    try {
      const detail = `${String(preview.executionLocation)} · ${String(preview.destination)}\n${String(preview.kind)}\n` +
        preview.files.slice(0, 20).join('\n') + (preview.files.length > 20 ? `\n외 ${preview.files.length - 20}개` : '') +
        `\n전송 ${String(preview.requestBytes)} bytes · SHA256 ${preview.requestHash.slice(0, 16)}\n취소해도 이미 보낸 내용은 회수할 수 없습니다.`;
      const accepted = await confirm(detail);
      if (this.disposed || this.closed()) throw new Error('ACCESS_DENIED');
      if (!accepted) return null;
      if (preview.expiresAt <= this.now()) throw new Error('APPROVAL_REQUIRED');
      this.intents.set(key, { body, expiresAt: this.now() + 300000 });
      return await forward();
    } finally { this.confirming = false; }
  }
}
export function registerProjects(window: BrowserWindow, agent: Pick<Agent, 'call'>, contracts: Contracts, native: NativeProjectDialogs = nativeDialogs): () => void {
  const channels: string[] = [];
  const consent = new ReviewConsent(() => window.isDestroyed());
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
    ['review-preview', 'workspace.reviews.preview'], ['review-get', 'workspace.reviews.get'], ['review-list', 'workspace.reviews.list'], ['review-cancel', 'workspace.reviews.cancel'], ['review-feedback', 'workspace.reviews.feedback'],
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
    const result = await agent.call(operation, payload, { ...(request.projectId ? { projectId: request.projectId } : {}), ...(request.idempotencyKey ? { idempotencyKey: request.idempotencyKey } : {}) });
    if (operation === 'workspace.reviews.preview') { contracts.result(operation, result); consent.remember(result); }
    return result;
  });
  handle('workspace:review-start', async (...args) => {
    if (args.length !== 1 || !args[0] || typeof args[0] !== 'object' || Array.isArray(args[0])) throw new Error('INVALID_REQUEST');
    const value = args[0] as Record<string, unknown>;
    const { projectId, idempotencyKey, ...payload } = value;
    const request = { protocolVersion: 1, requestId: randomUUID(), operation: 'workspace.reviews.start', projectId, idempotencyKey, payload };
    contracts.request(request);
    return await consent.start(value, detail => native.confirm(window, '로컬 AI에 코드를 전송할까요?', detail),
      () => agent.call('workspace.reviews.start', payload, { projectId: request.projectId, idempotencyKey: request.idempotencyKey }));
  });
  return () => { consent.dispose(); for (const channel of channels) ipcMain.removeHandler(channel); };
}
