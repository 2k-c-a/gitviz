export const OPERATIONS = ['workspace.system.handshake', 'workspace.system.health', 'workspace.system.shutdown',
  'workspace.projects.selectAndRegister', 'workspace.projects.list', 'workspace.projects.get',
  'workspace.projects.updateSettings', 'workspace.projects.unregister', 'workspace.git.status',
  'workspace.reviews.preview', 'workspace.reviews.start', 'workspace.reviews.get', 'workspace.reviews.list', 'workspace.reviews.cancel', 'workspace.reviews.feedback',
  'workspace.changes.capture', 'workspace.files.preview', 'workspace.validation.detect', 'workspace.validation.plan',
  'workspace.proposals.create', 'workspace.proposals.get', 'workspace.proposals.reject', 'workspace.proposals.requestRevision', 'workspace.proposals.approve', 'workspace.proposals.apply', 'workspace.proposals.recoveryPlan', 'workspace.proposals.approveRecovery', 'workspace.proposals.recover', 'workspace.impact.prepare', 'workspace.impact.read', 'workspace.impact.append', 'workspace.impact.finish', 'workspace.impact.get', 'workspace.impact.historical', 'workspace.projects.setEditor', 'workspace.impact.release', 'workspace.files.locate', 'workspace.proposals.listRecovery', 'workspace.validation.prepare', 'workspace.validation.approve', 'workspace.validation.start', 'workspace.validation.get', 'workspace.validation.cancel', 'workspace.comparisons.create', 'workspace.comparisons.get', 'workspace.notifications.list', 'workspace.notifications.markRead', 'workspace.events.read', 'workspace.projects.connectRemote', 'workspace.remote.observeAnalysis', 'workspace.remote.getAnalysis', 'workspace.remote.connections', 'workspace.reviews.cloudContext', 'workspace.reviews.cloudExchange'] as const;
export type Operation = typeof OPERATIONS[number];
export type State = 'ready' | 'degraded' | 'unavailable';
export interface Health {
  state: State;
  components: { store: State; secrets: State; lifecycle: State };
  errorCode?: string;
}
export interface Request {
  protocolVersion: 1;
  requestId: string;
  operation: Operation;
  payload: Record<string, unknown>;
  projectId?: string;
  idempotencyKey?: string;
}
export type Response = { protocolVersion: 1; requestId: string } & (
  { ok: true; result: Record<string, unknown> } |
  { ok: false; error: { code: string; message: string; retryable: boolean; details: { operation?: string; limitBytes?: number; observedBytes?: number; limitItems?: number; observedItems?: number } } }
);
export const unavailable = (): Health => ({ state: 'unavailable', components: { store: 'unavailable', secrets: 'unavailable', lifecycle: 'unavailable' } });
