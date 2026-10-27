import { dialog, ipcMain, type BrowserWindow } from 'electron';
import { randomBytes, randomUUID } from 'node:crypto';
import type { Agent } from './agent';
import type { Contracts } from './contracts';
import type { Operation } from '../contracts/workspace';
import { trustedSender } from './ipc';

interface Approval {
  project: string; proposal: string; secret: Buffer; expires: number; body: string; payload: Record<string, unknown>;
  approvalId?: string; executionKey?: string;
}
export interface ProposalDialogs { confirm(window: BrowserWindow, message: string, detail: string): Promise<boolean> }
const dialogs: ProposalDialogs = { async confirm(window, message, detail) {
  return (await dialog.showMessageBox(window, { type: 'warning', message, detail, buttons: ['취소', '승인'], defaultId: 0, cancelId: 0 })).response === 1;
} };

export function registerProposals(window: BrowserWindow, agent: Pick<Agent, 'call'>, contracts: Contracts, native: ProposalDialogs = dialogs): () => void {
  const channels: string[] = []; const approvals = new Map<string, Approval>(); const intents = new Map<string, Approval>();
  let confirming = false;
  const cleanup = () => { for (const [key, value] of intents) if (value.expires < Date.now()) { value.secret.fill(0); intents.delete(key); if (value.approvalId) approvals.delete(value.approvalId); } };
  function handle(channel: string, allowed: string[], action: (value: Record<string, unknown>) => Promise<unknown>): void {
    channels.push(channel);
    ipcMain.handle(channel, async (event, options: unknown) => {
      if (!trustedSender(event, window)) throw new Error('ACCESS_DENIED');
      if (!options || typeof options !== 'object' || Array.isArray(options) || Object.keys(options).some(key => !allowed.includes(key))) throw new Error('INVALID_REQUEST');
      cleanup();
      try { return await action(options as Record<string, unknown>); }
      catch (error) {
        // eslint-disable-next-line preserve-caught-error -- No token, source or native path in renderer errors.
        throw new Error(error instanceof Error && /^[A-Z_]+$/.test(error.message) ? error.message : 'INVALID_REQUEST');
      }
    });
  }
  function request(operation: Operation, value: Record<string, unknown>, internal: Record<string, unknown> = {}): { payload: Record<string, unknown>; options: { projectId: string; idempotencyKey?: string } } {
    const { projectId, idempotencyKey, ...body } = value;
    const payload = { ...body, ...internal };
    contracts.request({ protocolVersion: 1, requestId: randomUUID(), operation, projectId, payload, ...(idempotencyKey === undefined ? {} : { idempotencyKey }) });
    return { payload, options: { projectId: String(projectId), ...(idempotencyKey === undefined ? {} : { idempotencyKey: String(idempotencyKey) }) } };
  }
  for (const [name, operation, fields] of [
    ['list-recovery', 'workspace.proposals.listRecovery', []],
    ['create', 'workspace.proposals.create', ['refactorRunId', 'idempotencyKey']],
    ['get', 'workspace.proposals.get', ['proposalId']],
    ['reject', 'workspace.proposals.reject', ['proposalId', 'expectedVersion', 'idempotencyKey']],
    ['revision', 'workspace.proposals.requestRevision', ['proposalId', 'expectedVersion', 'instruction', 'idempotencyKey']],
  ] as [string, Operation, string[]][]) handle('workspace:proposal-' + name, ['projectId', ...fields], async value => {
    const call = request(operation, value); return await agent.call(operation, call.payload, call.options);
  });
  handle('workspace:proposal-approve', ['projectId', 'proposalId', 'expectedVersion', 'patchHash', 'preconditionsHash', 'idempotencyKey'], async value => {
    const probe = request('workspace.proposals.approve', value, { capability: '0'.repeat(64) });
    const key = `${String(value.projectId)}:approve:${String(value.idempotencyKey)}`; const body = JSON.stringify(probe.payload);
    let record = intents.get(key);
    if (record && record.body !== body) throw new Error('CONFLICT');
    if (!record) {
      if (confirming || intents.size >= 32) throw new Error('BUSY'); confirming = true;
      try {
        const current = await agent.call('workspace.proposals.get', { proposalId: value.proposalId }, { projectId: probe.options.projectId });
        if (current.version !== value.expectedVersion || current.patchHash !== value.patchHash || current.preconditionsHash !== value.preconditionsHash) throw new Error('PRECONDITION_FAILED');
        if (!await native.confirm(window, '이 고정 제안을 원본 파일에 적용하도록 승인할까요?', `${String(current.purpose)}\npatch ${String(current.patchHash)}\npreconditions ${String(current.preconditionsHash)}\n${(current.changes as { path: string }[]).map(file => file.path).join('\n')}\n2단계 게시 중 원래 경로가 잠시 없을 수 있습니다. 실패 시 자동 원복하지 않고 명시 복구가 필요합니다. index·commit·push는 변경하지 않습니다.`)) return null;
        record = { project: String(value.projectId), proposal: String(value.proposalId), secret: randomBytes(32), expires: Date.now() + 300000, body, payload: { ...value } };
        intents.set(key, record);
      } finally { confirming = false; }
    }
    const call = request('workspace.proposals.approve', value, { capability: record.secret.toString('hex') });
    const approved = await agent.call('workspace.proposals.approve', call.payload, call.options);
    record.approvalId = String(approved.approvalId); approvals.set(record.approvalId, record);
    return approved;
  });
  handle('workspace:proposal-apply', ['projectId', 'proposalId', 'approvalId', 'idempotencyKey'], async value => {
    request('workspace.proposals.apply', value, { capability: '0'.repeat(64) });
    const record = approvals.get(String(value.approvalId));
    if (!record || record.project !== value.projectId || record.proposal !== value.proposalId || record.expires < Date.now()) throw new Error('APPROVAL_REQUIRED');
    if (record.executionKey && record.executionKey !== value.idempotencyKey) throw new Error('APPROVAL_REQUIRED');
    record.executionKey = String(value.idempotencyKey);
    const call = request('workspace.proposals.apply', value, { capability: record.secret.toString('hex') });
    return await agent.call('workspace.proposals.apply', call.payload, call.options);
  });
  handle('workspace:proposal-recover', ['projectId', 'proposalId', 'expectedVersion', 'idempotencyKey'], async value => {
    if (typeof value.expectedVersion !== 'number' || !Number.isInteger(value.expectedVersion) || value.expectedVersion < 1) throw new Error('INVALID_REQUEST');
    request('workspace.proposals.recover', { projectId: value.projectId, proposalId: value.proposalId,
      approvalId: '00000000-0000-4000-8000-000000000000', recoveryHash: '0'.repeat(64), idempotencyKey: value.idempotencyKey }, { capability: '0'.repeat(64) });
    const get = request('workspace.proposals.recoveryPlan', { projectId: value.projectId, proposalId: value.proposalId });
    const key = `${String(value.projectId)}:recover:${String(value.idempotencyKey)}`;
    const body = JSON.stringify(value);
    let record = intents.get(key);
    if (record && record.body !== body) throw new Error('CONFLICT');
    if (!record) {
      if (confirming || intents.size >= 32) throw new Error('BUSY'); confirming = true;
      try {
        const plan = await agent.call('workspace.proposals.recoveryPlan', get.payload, get.options);
        if (plan.version !== value.expectedVersion) throw new Error('CONFLICT');
        if (!await native.confirm(window, '앱이 변경한 내용만 원복하도록 승인할까요?', `${JSON.stringify(plan.files, null, 2)}\n복구 ${String(plan.recoveryHash)}\n현재 identity/hash가 다르면 원복하지 않습니다. index는 보존하며 2단계 복구가 중단되면 다시 확인해야 합니다.`)) return null;
        record = { project: String(value.projectId), proposal: String(value.proposalId), secret: randomBytes(32), expires: Date.now() + 300000, body,
          payload: { projectId: value.projectId, proposalId: value.proposalId, recoveryHash: plan.recoveryHash, idempotencyKey: value.idempotencyKey } };
        intents.set(key, record);
      } finally { confirming = false; }
    }
    if (!record.approvalId) {
      const call = request('workspace.proposals.approveRecovery', record.payload, { capability: record.secret.toString('hex') });
      const approved = await agent.call('workspace.proposals.approveRecovery', call.payload, call.options); record.approvalId = String(approved.approvalId);
    }
    const call = request('workspace.proposals.recover', { ...record.payload, approvalId: record.approvalId }, { capability: record.secret.toString('hex') });
    return await agent.call('workspace.proposals.recover', call.payload, call.options);
  });
  return () => { for (const channel of channels) ipcMain.removeHandler(channel); for (const record of intents.values()) record.secret.fill(0); intents.clear(); approvals.clear(); };
}
