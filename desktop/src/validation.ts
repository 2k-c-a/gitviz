import { dialog, ipcMain, type BrowserWindow } from 'electron';
import { randomBytes, randomUUID } from 'node:crypto';
import { Agent } from './agent';
import { Contracts } from './contracts';
import { workspaceSender } from './ipc';
import type { Operation } from '../contracts/workspace';
import { collectMetrics } from './comparison-metrics';

export interface ValidationDialog { confirm(window: BrowserWindow, detail: string): Promise<boolean> }
const native: ValidationDialog = { async confirm(window, detail) {
  return (await dialog.showMessageBox(window, { type: 'warning', buttons: ['취소', '이 프로젝트를 신뢰하고 계획 실행'],
    defaultId: 0, cancelId: 0, message: '프로젝트 코드는 현재 Windows 사용자 권한으로 파일·네트워크에 접근합니다.', detail })).response === 1;
} };

export function registerValidation(window: BrowserWindow, agent: Pick<Agent, 'call'>, contracts: Contracts, consent = native): () => void {
  const channels: string[] = [];
  const intents = new Map<string, { body: string; cap: Buffer; expires: number; approval?: string; busy: boolean }>();
  let measuring = false;
  let disposed = false;
  function request(operation: Operation, value: Record<string, unknown>, extra: Record<string, unknown> = {}) {
    const { projectId, idempotencyKey, ...payload } = value;
    const message = { protocolVersion: 1, requestId: randomUUID(), operation, projectId, payload: { ...payload, ...extra },
      ...(idempotencyKey === undefined ? {} : { idempotencyKey }) };
    contracts.request(message);
    return () => agent.call(operation, message.payload, { projectId: message.projectId, ...(message.idempotencyKey === undefined ? {} : { idempotencyKey: message.idempotencyKey }) });
  }
  function handle(name: string, action: (value: Record<string, unknown>) => Promise<unknown>) {
    channels.push(name);
    ipcMain.handle(name, async (event, ...args: unknown[]) => {
      if (!workspaceSender(event, window)) throw new Error('ACCESS_DENIED');
      if (args.length !== 1 || !args[0] || typeof args[0] !== 'object' || Array.isArray(args[0])) throw new Error('INVALID_REQUEST');
      try { return await action(args[0] as Record<string, unknown>); }
      catch (error) {
        // eslint-disable-next-line preserve-caught-error -- no native paths, raw output or capabilities in IPC errors
        throw new Error(error instanceof Error && /^[A-Z_]+$/.test(error.message) ? error.message : 'INVALID_REQUEST');
      }
    });
  }
  handle('workspace:run-validation', async value => {
    // Validate renderer shape before issuing any native prompt or minting a token.
    request('workspace.validation.start', { ...value, approvalId: '00000000-0000-4000-8000-000000000000' }, { capability: '0'.repeat(64) });
    const { projectId, planId, idempotencyKey } = value;
    if (Object.keys(value).some(key => !['projectId', 'planId', 'idempotencyKey'].includes(key))) throw new Error('INVALID_REQUEST');
    for (const [key, record] of intents) if (!record.busy && record.expires < Date.now()) { record.cap.fill(0); intents.delete(key); }
    const key = `${String(projectId)}:${String(idempotencyKey)}`, body = JSON.stringify(value);
    let intent = intents.get(key);
    if (intent && intent.body !== body) throw new Error('CONFLICT');
    if (intent?.busy) throw new Error('BUSY');
    if (!intent) {
      if (intents.size >= 32) throw new Error('BUSY');
      intent = { body, cap: randomBytes(32), expires: Date.now() + 300000, busy: false };
      intents.set(key, intent);
    }
    intent.busy = true;
    try {
      if (!intent.approval) {
        const prepared = await request('workspace.validation.prepare', { projectId, planId })();
        if (!await consent.confirm(window, JSON.stringify(prepared, null, 2))) { intent.cap.fill(0); intents.delete(key); return null; }
        if (disposed || window.isDestroyed()) throw new Error('ACCESS_DENIED');
        intent.expires = Date.now() + 300000;
        const approved = await request('workspace.validation.approve', { projectId, planId, idempotencyKey },
          { confirmationHash: prepared.confirmationHash, capability: intent.cap.toString('hex'), trusted: true })();
        intent.approval = String(approved.validationApprovalId);
      }
      if (disposed || window.isDestroyed()) throw new Error('ACCESS_DENIED');
      return await request('workspace.validation.start', { projectId, planId, idempotencyKey, approvalId: intent.approval },
        { capability: intent.cap.toString('hex') })();
    } finally { intent.busy = false; }
  });
  handle('workspace:create-comparison', async value => {
    if (Object.keys(value).some(key => !['projectId', 'idempotencyKey', 'beforeSnapshotId', 'afterSnapshotId', 'proposalId', 'beforeRunId', 'afterRunId'].includes(key))) throw new Error('INVALID_REQUEST');
    request('workspace.comparisons.create', value, { typescriptMeasurements: { before: null, after: null } });
    if (measuring) throw new Error('BUSY'); measuring = true;
    try {
      const before = await collectMetrics(agent, String(value.projectId), String(value.beforeSnapshotId));
      const after = await collectMetrics(agent, String(value.projectId), String(value.afterSnapshotId));
      return await request('workspace.comparisons.create', value, { typescriptMeasurements: { before, after } })();
    } finally { measuring = false; }
  });
  for (const [name, operation] of [
    ['get-validation', 'workspace.validation.get'], ['cancel-validation', 'workspace.validation.cancel'],
    ['get-comparison', 'workspace.comparisons.get'],
  ] as const) handle(`workspace:${name}`, value => request(operation, value)());
  return () => { disposed = true; for (const channel of channels) ipcMain.removeHandler(channel); for (const record of intents.values()) record.cap.fill(0); intents.clear(); };
}
