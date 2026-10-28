import { ipcMain, type BrowserWindow } from 'electron';
import { randomUUID } from 'node:crypto';
import type { Agent } from './agent';
import type { Contracts } from './contracts';
import type { Operation } from '../contracts/workspace';
import { isAppDocument } from './app-protocol';
import { workspaceSender } from './ipc';

export interface EventTimer { set(action: () => void, delay: number): unknown; clear(handle: unknown): void }
const timers: EventTimer = { set: (action, delay) => setTimeout(action, delay), clear: handle => clearTimeout(handle as NodeJS.Timeout) };
interface EventItem { sequence: number; eventId: string; projectId: string; type: string; resourceId: string; sourceRevision: number }
export interface EventBatch { events: EventItem[]; lastSequence: number; reset: boolean; hasMore: boolean }
export type Signal = ({ kind: 'event' } & EventItem) | { kind: 'resync' };

/** Polls the agent's durable event log for one window. Signals only say "refresh"; the inbox list stays the source of truth. */
export class EventRelay {
  private after: number | null = null;
  private handle: unknown = null;
  private polling = false;
  private disposed = false;
  constructor(private readonly read: (after: number, limit: number) => Promise<EventBatch>, private readonly send: (signal: Signal) => void,
    private readonly live: () => boolean, private readonly timer: EventTimer = timers, private readonly interval = 2000) {}
  get position(): number | null { return this.after; }
  async subscribe(afterSequence: number): Promise<boolean> {
    this.unsubscribe();
    // 0 means "from now": the renderer loads the list itself, so history is not replayed as signals.
    const start = afterSequence === 0 ? (await this.read(0, 1)).lastSequence : afterSequence;
    if (this.disposed || !this.live()) return false;
    this.after = start;
    this.schedule();
    return true;
  }
  unsubscribe(): void { this.after = null; if (this.handle !== null) this.timer.clear(this.handle); this.handle = null; }
  dispose(): void { this.disposed = true; this.unsubscribe(); }
  private schedule(): void { if (this.after !== null && !this.disposed) this.handle = this.timer.set(() => { void this.poll(); }, this.interval); }
  async poll(): Promise<void> {
    this.handle = null;
    if (this.after === null || this.polling) return;
    if (!this.live()) { this.unsubscribe(); return; }
    this.polling = true;
    try {
      const batch = await this.read(this.after, 50);
      if (this.after === null) return;  // Unsubscribed while the read was in flight.
      for (const item of batch.events) {
        if (item.sequence !== this.after + 1) this.send({ kind: 'resync' });  // Gap: refresh the durable list.
        this.after = item.sequence;
        this.send({ kind: 'event', sequence: item.sequence, eventId: item.eventId, projectId: item.projectId, type: item.type,
          resourceId: item.resourceId, sourceRevision: item.sourceRevision });
      }
      if (batch.reset) { this.after = batch.lastSequence; this.send({ kind: 'resync' }); }
      else if (batch.hasMore) this.send({ kind: 'resync' });
    } catch { /* Agent unavailable: keep the subscription; the next tick retries. */ }
    finally { this.polling = false; if (this.handle === null) this.schedule(); }
  }
}

export function registerNotifications(window: BrowserWindow, agent: Pick<Agent, 'call'>, contracts: Contracts, timer: EventTimer = timers): () => void {
  const channels: string[] = [];
  function request(operation: Operation, value: Record<string, unknown>): () => Promise<Record<string, unknown>> {
    const { projectId, idempotencyKey, ...payload } = value;
    contracts.request({ protocolVersion: 1, requestId: randomUUID(), operation, payload,
      ...(projectId === undefined ? {} : { projectId }), ...(idempotencyKey === undefined ? {} : { idempotencyKey }) });
    return () => agent.call(operation, payload, { ...(projectId === undefined ? {} : { projectId: String(projectId) }),
      ...(idempotencyKey === undefined ? {} : { idempotencyKey: String(idempotencyKey) }) });
  }
  function workspaceDocument(): boolean {
    if (window.isDestroyed()) return false;
    const raw = window.webContents.getURL();
    if (!isAppDocument(raw)) return false;
    const url = new URL(raw);
    return url.pathname === '/index.html' && url.href === raw;
  }
  const relay = new EventRelay(async (after, limit) => await request('workspace.events.read', { afterSequence: after, limit, projectIds: null })() as unknown as EventBatch,
    signal => { if (workspaceDocument()) window.webContents.send('workspace:event', signal); }, workspaceDocument, timer);
  function handle(name: string, allowed: string[], action: (value: Record<string, unknown>) => Promise<unknown>): void {
    channels.push(name);
    ipcMain.handle(name, async (event, ...args: unknown[]) => {
      if (!workspaceSender(event, window)) throw new Error('ACCESS_DENIED');
      const value = args[0];
      if (args.length !== 1 || !value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(key => !allowed.includes(key))) throw new Error('INVALID_REQUEST');
      try { return await action(value as Record<string, unknown>); }
      catch (error) {
        // eslint-disable-next-line preserve-caught-error -- no paths or payloads in renderer errors
        throw new Error(error instanceof Error && /^[A-Z_]+$/.test(error.message) ? error.message : 'INVALID_REQUEST');
      }
    });
  }
  handle('workspace:list-notifications', ['filters', 'cursor', 'limit'], value => request('workspace.notifications.list', value)());
  handle('workspace:mark-notification-read', ['projectId', 'idempotencyKey', 'notificationId', 'expectedVersion', 'read'],
    value => request('workspace.notifications.markRead', value)());
  handle('workspace:events-subscribe', ['afterSequence'], async value => {
    request('workspace.events.read', { afterSequence: value.afterSequence, limit: 50, projectIds: null });  // Shape check only.
    return { subscribed: await relay.subscribe(Number(value.afterSequence)) };
  });
  handle('workspace:events-unsubscribe', [], async () => { relay.unsubscribe(); return { subscribed: false }; });
  return () => { relay.dispose(); for (const channel of channels) ipcMain.removeHandler(channel); };
}
