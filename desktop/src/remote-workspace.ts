import { randomUUID } from 'node:crypto';
import type { Agent } from './agent';
import type { DesktopAuth } from './auth';
import type { Remote } from './remote';

interface Identity { accountId: string; repositoryId: number; githubId: number; canonicalUrl: string; visibility: 'public' | 'private' }
interface Connection extends Omit<Identity, 'visibility'> { connectionId: string; serverOrigin: string; remoteName: string; configHash: string }
interface Inspection { projectId: string; projectVersion: number; displayName: string; canonicalRoot: string; configHash: string; remotes: { name: string; canonicalUrl: string }[]; connection: Connection | null }
const id = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0;
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('BAD_RESPONSE');
  return value as Record<string, unknown>;
}

/** Proofs and credentials stay in main. Renderer inputs are local ID + server repository ID only. */
export class RemoteWorkspace {
  private busy = false;
  private disposed = false;
  private readonly watches = new Map<string, { generation: number; connectionId: string }>();
  private handle: NodeJS.Timeout | undefined;
  private polling = false;
  private nextPoll = 0;
  private lastGeneration: number | null = null;
  private readonly authenticationTimer: NodeJS.Timeout;
  constructor(private readonly agent: Pick<Agent, 'call'>, private readonly auth: Pick<DesktopAuth, 'identity' | 'status'>,
    private readonly remote: Pick<Remote, 'request'>, private readonly origin: string,
    private readonly confirm: (detail: string) => Promise<boolean>) {
    if (new URL(origin).origin !== origin || !origin.startsWith('https://')) throw new Error('INVALID_SERVER');
    this.authenticationTimer = setInterval(() => {
      const generation = this.auth.identity()?.generation ?? null;
      if (generation !== this.lastGeneration) {
        this.lastGeneration = generation; this.watches.clear();
        if (generation !== null) void this.resume().catch(() => {});
      }
    }, 3000);
    this.authenticationTimer.unref();
  }
  dispose(): void { this.disposed = true; this.watches.clear(); clearTimeout(this.handle); clearInterval(this.authenticationTimer); }
  generation(): number | null { return this.disposed ? null : this.auth.identity()?.generation ?? null; }
  async resume(): Promise<void> {
    const account = await this.account();
    const saved = await this.agent.call('workspace.remote.connections', { accountId: account.accountId, serverOrigin: this.origin });
    this.check(account.generation);
    for (const projectId of saved.projectIds as string[]) {
      try { await this.status(projectId); } catch { /* Reconnect/access problems remain explicit when the project is selected. */ }
    }
  }
  private async account(): Promise<{ accountId: string; generation: number }> {
    const before = this.auth.identity();
    if (this.disposed || !before) throw new Error('AUTH_REQUIRED');
    const state = await this.auth.status();
    if (state.state !== 'authenticated' || !state.user || this.auth.identity()?.generation !== before.generation) throw new Error('ACCOUNT_CHANGED');
    return { accountId: state.user.id, generation: before.generation };
  }
  private check(generation: number): void {
    if (this.disposed || this.auth.identity()?.generation !== generation) throw new Error('ACCOUNT_CHANGED');
  }
  private async identity(repositoryId: number, accountId: string): Promise<Identity> {
    const response = await this.remote.request({ endpointKey: 'repositoryIdentity', path: { repositoryId }, query: {} });
    if (response.status === 429) this.backoff(response.headers['retry-after']);
    if (response.status !== 200) throw new Error(response.status === 429 ? 'RATE_LIMITED' : 'REMOTE_ACCESS_DENIED');
    const value = object(response.body);
    if (Object.keys(value).sort().join(',') !== 'accountId,canonicalUrl,githubId,repositoryId,visibility' || value.accountId !== accountId ||
      value.repositoryId !== repositoryId || !id(value.githubId) || typeof value.canonicalUrl !== 'string' ||
      !['public', 'private'].includes(String(value.visibility)) ||
      !/^https:\/\/github\.com\/[a-z0-9_.-]+\/[a-z0-9_.-]+$/.test(value.canonicalUrl) ||
      value.canonicalUrl.split('/').slice(3).some(part => part === '.' || part === '..')) throw new Error('BAD_RESPONSE');
    return { accountId, repositoryId, githubId: value.githubId, canonicalUrl: value.canonicalUrl, visibility: value.visibility as 'public' | 'private' };
  }
  private async inspect(projectId: string): Promise<Inspection> {
    return await this.agent.call('workspace.projects.connectRemote', { phase: 'inspect' }, { projectId, idempotencyKey: randomUUID() }) as unknown as Inspection;
  }
  private backoff(retry: string | undefined): void {
    const delay = retry && /^\d+$/.test(retry) ? Number(retry) * 1000 : retry ? Date.parse(retry) - Date.now() : 10000;
    this.nextPoll = Date.now() + Math.max(2000, Math.min(Number.isFinite(delay) ? delay : 10000, 300000));
  }
  async connect(projectId: string, repositoryId: number): Promise<Record<string, unknown> | null> {
    if (this.busy) throw new Error('BUSY');
    if (!this.watches.has(projectId) && this.watches.size >= 16) throw new Error('BUSY');
    this.busy = true;
    try {
      const account = await this.account();
      const proof = await this.identity(repositoryId, account.accountId);
      const inspected = await this.inspect(projectId);
      const matches = inspected.remotes.filter(item => item.canonicalUrl === proof.canonicalUrl);
      if (!matches.length) throw new Error('REMOTE_IDENTITY_MISMATCH');
      const selected = matches.find(item => item.name === 'origin') ?? matches[0]!;
      if (!await this.confirm(`로컬 프로젝트: ${inspected.displayName}\n폴더: ${inspected.canonicalRoot}\n프로젝트 ID: ${projectId}\n서버: ${this.origin}\n계정: ${account.accountId}\n저장소: ${proof.canonicalUrl}\nGitHub ID: ${proof.githubId}\nremote: ${selected.name}\n소스 전송 없이 연결 정보와 추적한 분석 상태만 저장합니다.`)) return null;
      this.check(account.generation);
      const checked = await this.identity(repositoryId, account.accountId);
      this.check(account.generation);
      if (JSON.stringify(checked) !== JSON.stringify(proof)) throw new Error('PRECONDITION_FAILED');
      const result = await this.agent.call('workspace.projects.connectRemote', { phase: 'connect', accountId: proof.accountId,
        repositoryId: proof.repositoryId, githubId: proof.githubId, canonicalUrl: proof.canonicalUrl,
        serverOrigin: this.origin, remoteName: selected.name, configHash: inspected.configHash,
        expectedVersion: inspected.projectVersion }, { projectId, idempotencyKey: randomUUID() });
      this.check(account.generation);
      this.watch(projectId, String(result.connectionId), account.generation);
      return result;
    } finally { this.busy = false; }
  }
  private watch(projectId: string, connectionId: string, generation: number): void {
    if (!this.watches.has(projectId) && this.watches.size >= 16) throw new Error('BUSY');
    this.watches.set(projectId, { connectionId, generation });
    if (!this.handle && !this.disposed) this.handle = setTimeout(() => { this.handle = undefined; void this.poll(); }, 2000);
  }
  async status(projectId: string): Promise<Record<string, unknown>> {
    const account = await this.account();
    const inspected = await this.inspect(projectId);
    this.check(account.generation);
    const connection = inspected.connection;
    if (!connection || connection.accountId !== account.accountId || connection.serverOrigin !== this.origin) return { connected: false };
    if (connection.configHash !== inspected.configHash) throw new Error('PRECONDITION_FAILED');
    this.watch(projectId, connection.connectionId, account.generation);
    return { connected: true, repositoryId: connection.repositoryId, githubId: connection.githubId, canonicalUrl: connection.canonicalUrl };
  }
  async poll(): Promise<void> {
    if (this.polling || this.disposed) return;
    this.polling = true;
    try {
      if (Date.now() < this.nextPoll) return;
      for (const [projectId, watch] of this.watches) {
        try {
          this.check(watch.generation);
          const account = await this.account();
          const inspected = await this.inspect(projectId);
          const connection = inspected.connection;
          if (!connection || connection.connectionId !== watch.connectionId || connection.accountId !== account.accountId ||
            connection.serverOrigin !== this.origin || connection.configHash !== inspected.configHash) throw new Error('PRECONDITION_FAILED');
          const proof = await this.identity(connection.repositoryId, account.accountId);
          if (proof.githubId !== connection.githubId || proof.canonicalUrl !== connection.canonicalUrl) throw new Error('PRECONDITION_FAILED');
          const response = await this.remote.request({ endpointKey: 'repositoryStatus', path: { repositoryId: connection.repositoryId }, query: {} });
          this.check(watch.generation);
          if (response.status === 429) {
            this.backoff(response.headers['retry-after']);
            break;
          }
          if (response.status !== 200) throw new Error('REMOTE_ACCESS_DENIED');
          const body = object(response.body);
          if (body.job_id === null || body.job_id === undefined) continue;
          const status = body.status === 'pending' ? 'queued' : body.status;
          if (!id(body.job_id) || !['queued', 'running', 'done', 'failed'].includes(String(status)) ||
            (body.snapshot_id !== null && body.snapshot_id !== undefined && !id(body.snapshot_id))) throw new Error('BAD_RESPONSE');
          await this.agent.call('workspace.remote.observeAnalysis', { connectionId: connection.connectionId, accountId: account.accountId,
            serverOrigin: this.origin, jobId: body.job_id, status, snapshotId: body.snapshot_id ?? null,
          }, { projectId, idempotencyKey: randomUUID() });
        } catch (error) {
          if (this.watches.get(projectId) !== watch) continue; // An old account's callback cannot remove a newer watch.
          if (error instanceof Error && ['ACCOUNT_CHANGED', 'AUTH_REQUIRED', 'PRECONDITION_FAILED', 'REMOTE_ACCESS_DENIED'].includes(error.message)) this.watches.delete(projectId);
          this.nextPoll = Math.max(this.nextPoll, Date.now() + 10000);
          if (Date.now() < this.nextPoll) break;
        }
      }
    } finally {
      this.polling = false;
      if (!this.disposed && this.watches.size && !this.handle) this.handle = setTimeout(() => { this.handle = undefined; void this.poll(); }, 2000);
    }
  }
  async open(projectId: string, analysisId: string): Promise<string> {
    const account = await this.account();
    const saved = await this.agent.call('workspace.remote.getAnalysis', { analysisId, accountId: account.accountId, serverOrigin: this.origin }, { projectId });
    const proof = await this.identity(Number(saved.repositoryId), account.accountId);
    this.check(account.generation);
    if (proof.githubId !== saved.githubId || proof.canonicalUrl !== saved.canonicalUrl) throw new Error('REMOTE_IDENTITY_MISMATCH');
    await this.agent.call('workspace.remote.getAnalysis', { analysisId, accountId: account.accountId, serverOrigin: this.origin }, { projectId });
    this.check(account.generation);
    return `gitviz://app/web/pages/timeline.html?repo=${proof.repositoryId}${id(saved.snapshotId) ? `&snapshot=${saved.snapshotId}` : ''}`;
  }
  async readProof(projectId: string): Promise<Record<string, unknown>> {
    const account = await this.account();
    const inspected = await this.inspect(projectId), connection = inspected.connection;
    if (!connection || connection.accountId !== account.accountId || connection.serverOrigin !== this.origin || connection.configHash !== inspected.configHash) throw new Error('CLOUD_MAPPING_REQUIRED');
    const proof = await this.identity(connection.repositoryId, account.accountId);
    this.check(account.generation);
    if (proof.githubId !== connection.githubId || proof.canonicalUrl !== connection.canonicalUrl) throw new Error('REMOTE_IDENTITY_MISMATCH');
    return { ...proof, serverOrigin: this.origin, connectionId: connection.connectionId, configHash: inspected.configHash, expiresAt: Date.now() / 1000 + 30 };
  }
  async publicProof(projectId: string): Promise<Record<string, unknown>> {
    const proof = await this.readProof(projectId);
    if (proof.visibility !== 'public') throw new Error('CLOUD_MAPPING_REQUIRED');
    return proof;
  }
}
