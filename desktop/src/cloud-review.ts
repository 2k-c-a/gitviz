import { createHash, randomUUID } from 'node:crypto';
import type { Agent } from './agent';
import type { RemoteWorkspace } from './remote-workspace';
import { CLOUD, cloudHttp, type CloudHttp, type CloudReply } from './cloud-http';
import { decode } from './contracts';

interface Context { projectId: string; profileId: keyof typeof CLOUD; credentialRef: string; proof: Record<string, unknown>; generation: number; apiKey: string | null; expires: number; requestHash: string; requestBytes: number }
/** BYOK is held in main for one preview only; agent exchanges contain no API key. */
export class CloudReview {
  private readonly previews = new Map<string, Context>();
  private readonly active = new Map<string, AbortController>();
  private disposed = false;
  private readonly cleanupTimer: NodeJS.Timeout;
  constructor(private readonly agent: Pick<Agent, 'call'>, private readonly mapping: Pick<RemoteWorkspace, 'publicProof'>,
    private readonly generation: () => number | null, private readonly http: CloudHttp = cloudHttp) {
    this.cleanupTimer = setInterval(() => this.cleanup(), 1000); this.cleanupTimer.unref();
  }
  dispose(): void { this.disposed = true; clearInterval(this.cleanupTimer); this.previews.clear(); for (const controller of this.active.values()) controller.abort(); this.active.clear(); }
  cancel(runId: string): void { this.active.get(runId)?.abort(); }
  private cleanup(): void {
    for (const [key, value] of this.previews) {
      if (value.expires <= Date.now()) { value.apiKey = null; this.previews.delete(key); }
      else if (this.generation() !== value.generation) value.apiKey = null;
    }
  }
  private async authorize(value: Context): Promise<void> {
    if (this.disposed || this.generation() !== value.generation || value.expires <= Date.now()) throw new Error('ACCOUNT_CHANGED');
    const proof = await this.mapping.publicProof(value.projectId);
    if (this.generation() !== value.generation || Object.keys(value.proof).filter(key => key !== 'expiresAt').some(key => value.proof[key] !== proof[key])) throw new Error('CLOUD_MAPPING_REQUIRED');
    await this.agent.call('workspace.reviews.cloudContext', { phase: 'authorize', profileId: value.profileId,
      credentialRef: value.credentialRef, credentialVersion: 1, proof }, { projectId: value.projectId });
    if (this.generation() !== value.generation) {
      await this.agent.call('workspace.reviews.cloudContext', { phase: 'revoke' }, { projectId: value.projectId });
      throw new Error('ACCOUNT_CHANGED');
    }
  }
  async preview(projectId: string, payload: Record<string, unknown>, apiKey: unknown): Promise<Record<string, unknown>> {
    this.cleanup();
    if (this.disposed || this.previews.size >= 16) throw new Error('BUSY');
    if (!Object.hasOwn(CLOUD, String(payload.profileId)) || typeof apiKey !== 'string' || !/^[!-~]{16,512}$/.test(apiKey)) throw new Error('INVALID_REQUEST');
    const generation = this.generation(); if (generation === null) throw new Error('AUTH_REQUIRED');
    const proof = await this.mapping.publicProof(projectId);
    const value: Context = { projectId, profileId: payload.profileId as keyof typeof CLOUD, credentialRef: randomUUID(), proof,
      generation, apiKey, expires: Date.now() + 300000, requestHash: '', requestBytes: 0 };
    await this.authorize(value);
    const result = await this.agent.call('workspace.reviews.preview', { ...payload, credentialRef: value.credentialRef }, { projectId });
    if (this.generation() !== generation) throw new Error('ACCOUNT_CHANGED');
    value.requestHash = String(result.requestHash); value.requestBytes = Number(result.requestBytes);
    this.previews.set(String(result.previewId), value);
    return result;
  }
  async start(value: Record<string, unknown>, forward: () => Promise<Record<string, unknown>>): Promise<unknown> {
    if (this.disposed) throw new Error('ACCESS_DENIED');
    this.cleanup();
    const context = this.previews.get(String(value.previewId));
    if (!context) return await forward(); // Agent rejects expired/unknown preview; durable replay never sends twice.
    if (context.projectId !== value.projectId || this.generation() !== context.generation || this.disposed) throw new Error('ACCOUNT_CHANGED');
    if (context.apiKey !== null) await this.authorize(context);
    const result = await forward();
    if (context.apiKey !== null) {
      const key = context.apiKey; context.apiKey = null;
      if (result.credentialRef === context.credentialRef && ['running', 'queued'].includes(String(result.status))) void this.exchange(context, String(result.runId), key);
    }
    return result;
  }
  private async exchange(context: Context, runId: string, key: string): Promise<void> {
    const controller = new AbortController(); this.active.set(runId, controller);
    const timer = setTimeout(() => controller.abort(), 90000);
    const account = setInterval(() => { if (this.disposed || this.generation() !== context.generation) controller.abort(); }, 100);
    let reply: CloudReply = { httpStatus: 0, rawBase64: '', errorCode: 'PROVIDER_ERROR', uncertain: false };
    let taken = false;
    let sent = false;
    try {
      let request: Record<string, unknown> = {};
      for (let attempt = 0; attempt < 100 && !controller.signal.aborted; attempt++) {
        request = await this.agent.call('workspace.reviews.cloudExchange', { phase: 'take', runId }, { projectId: context.projectId });
        if (request.ready === true) { taken = true; break; }
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      if (!taken || controller.signal.aborted || this.generation() !== context.generation) throw new Error('ACCOUNT_CHANGED');
      const wire = Buffer.from(String(request.bodyBase64), 'base64'), route = CLOUD[context.profileId];
      if (request.credentialRef !== context.credentialRef || request.profileId !== context.profileId || request.provider !== route.provider || request.destination !== `https://${route.host}` ||
        wire.length !== context.requestBytes || request.requestBytes !== wire.length || request.requestHash !== context.requestHash ||
        createHash('sha256').update(wire).digest('hex') !== context.requestHash) throw new Error('PRECONDITION_FAILED');
      // A fresh public proof immediately precedes the only billable request.
      await this.authorize(context);
      if (controller.signal.aborted || this.generation() !== context.generation) throw new Error('ACCOUNT_CHANGED');
      sent = true;
      reply = await this.http(context.profileId, wire, key, controller.signal);
      if (reply.httpStatus !== 200) reply.rawBase64 = '';
      if (reply.httpStatus === 200) {
        const raw = Buffer.from(reply.rawBase64, 'base64');
        const stack: unknown[] = [decode(raw)];
        let reflected = raw.includes(Buffer.from(key));
        while (stack.length && !reflected) {
          const item = stack.pop();
          if (typeof item === 'string') reflected = item.includes(key);
          else if (Array.isArray(item)) stack.push(...item);
          else if (item && typeof item === 'object') for (const [name, value] of Object.entries(item)) { stack.push(name, value); }
        }
        if (reflected) reply = { httpStatus: 0, rawBase64: '', errorCode: 'PROVIDER_ERROR', uncertain: true };
      }
    } catch (error) {
      reply = { httpStatus: 0, rawBase64: '', errorCode: error instanceof Error && error.message === 'OUTPUT_LIMIT' ? 'OUTPUT_LIMIT' : 'PROVIDER_ERROR', uncertain: sent };
    } finally {
      clearTimeout(timer); clearInterval(account); this.active.delete(runId);
      try { if (taken) await this.agent.call('workspace.reviews.cloudExchange', { phase: 'complete', runId, ...reply }, { projectId: context.projectId }); }
      catch { /* Agent crash/restart leaves its durable attempt uncertain; never resend. */ }
    }
  }
}
