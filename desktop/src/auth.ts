import { randomBytes, randomUUID, createHash } from 'node:crypto';
import { readFile, lstat, open, rename, unlink } from 'node:fs/promises';
import type { Http, RemoteResponse } from './remote';
import { rejectLinks, type ProtectedStorage } from './secrets';

type User = { id: string; github_login: string; display_name: string | null };
interface Session { sessionToken: string; sessionId: string; user: User; expiresAt: string; capabilities: string[]; apiCapabilities: { desktopProtocol: number; aiContract: string }; pendingRevocation?: boolean }
interface Flow { flowId: string; verifier: string; pollSecret: string; key: string; displayCode: string; expires: number; nextPoll: number }
export interface AuthState { state: 'signed-out' | 'pending' | 'authenticated' | 'revoke-pending'; user?: User; expiresAt?: string; displayCode?: string; pollAfterMs?: number }
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function record(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('BAD_RESPONSE'); return value as Record<string, unknown>; }
function session(value: unknown): Session {
  const body = record(value); const user = record(body.user); const api = record(body.apiCapabilities);
  if (typeof body.sessionToken !== 'string' || !/^[A-Za-z0-9_-]{64}$/.test(body.sessionToken) ||
      typeof body.sessionId !== 'string' || !uuid.test(body.sessionId) || typeof user.id !== 'string' || !uuid.test(user.id) ||
      typeof user.github_login !== 'string' || user.github_login.length > 255 || !(user.display_name === null || typeof user.display_name === 'string') ||
      typeof body.expiresAt !== 'string' || !Number.isFinite(Date.parse(body.expiresAt)) || Date.parse(body.expiresAt) <= Date.now() ||
      !Array.isArray(body.capabilities) || !body.capabilities.includes('remote-v1') || api.desktopProtocol !== 1 || api.aiContract !== '1.9') throw new Error('UNSUPPORTED_SERVER');
  return { sessionToken: body.sessionToken, sessionId: body.sessionId, user: { id: user.id, github_login: user.github_login, display_name: user.display_name as string | null },
    expiresAt: body.expiresAt, capabilities: ['remote-v1'], apiCapabilities: { desktopProtocol: 1, aiContract: '1.9' }, ...(body.pendingRevocation === true ? { pendingRevocation: true } : {}) };
}

export class SessionVault {
  constructor(private readonly file: string, private readonly origin: string, private readonly storage: ProtectedStorage) {}
  async read(): Promise<Session | null> {
    await rejectLinks(this.file);
    try {
      const info = await lstat(this.file); if (!info.isFile() || info.size > 32768) throw new Error('STORAGE_UNAVAILABLE');
      const value = record(JSON.parse(await this.storage.decrypt(await readFile(this.file))));
      if (value.origin !== this.origin) throw new Error('STORAGE_UNAVAILABLE');
      return session(value.session);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
      // eslint-disable-next-line preserve-caught-error -- No decrypted secret or filesystem path in errors.
      throw new Error('STORAGE_UNAVAILABLE');
    }
  }
  async write(value: Session | null): Promise<void> {
    await rejectLinks(this.file);
    if (!value) { try { await unlink(this.file); } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; } return; }
    if (!await this.storage.available()) throw new Error('STORAGE_UNAVAILABLE');
    const encrypted = await this.storage.encrypt(JSON.stringify({ origin: this.origin, session: value }));
    const temporary = `${this.file}.${randomUUID()}.pending`;
    const handle = await open(temporary, 'wx', 0o600);
    try { await handle.writeFile(encrypted); await handle.sync(); } finally { await handle.close(); }
    await rename(temporary, this.file);
  }
}

export class DesktopAuth {
  private active: Session | null = null;
  private flow: Flow | null = null;
  private revoking: Session | null = null;
  private generation = 0;
  private busy = false;
  constructor(private readonly origin: string, private readonly http: Http, private readonly openBrowser: (url: string) => Promise<void>,
    private readonly vault: Pick<SessionVault, 'read' | 'write'>) {}
  identity = (): { token: string; generation: number } | null => {
    if (this.active && Date.parse(this.active.expiresAt) > Date.now()) return { token: this.active.sessionToken, generation: this.generation };
    if (this.active) this.invalidate();
    return null;
  };
  invalidate = (): void => { this.active = null; this.flow = null; this.generation++; void this.vault.write(null).catch(() => {}); };
  private view(): AuthState {
    if (this.revoking) return { state: 'revoke-pending' };
    if (this.active && this.identity()) return { state: 'authenticated', user: this.active.user, expiresAt: this.active.expiresAt };
    if (this.flow) return { state: 'pending', displayCode: this.flow.displayCode, pollAfterMs: Math.max(0, this.flow.nextPoll - Date.now()) };
    return { state: 'signed-out' };
  }
  private async exclusively<T>(action: () => Promise<T>): Promise<T> {
    if (this.busy) throw new Error('BUSY'); this.busy = true;
    try { return await action(); } finally { this.busy = false; }
  }
  async restore(): Promise<void> {
    await this.exclusively(async () => {
      const stored = await this.vault.read(); if (!stored) return;
      if (stored.pendingRevocation) {
        this.revoking = stored; this.generation++;
        const result = await this.http('/api/desktop/v1/session', 'DELETE', undefined, stored.sessionToken);
        if (![204, 401].includes(result.status)) throw new Error('REVOCATION_REQUIRED');
        await this.vault.write(null); this.revoking = null; return;
      }
      const res = await this.http('/api/desktop/v1/session', 'GET', undefined, stored.sessionToken);
      if (res.status === 401) { await this.vault.write(null); return; }
      if (res.status !== 200) throw new Error('AUTH_UNAVAILABLE');
      const checked = session({ ...record(res.body), sessionToken: stored.sessionToken });
      if (checked.user.id !== stored.user.id || checked.sessionId !== stored.sessionId) throw new Error('BAD_RESPONSE');
      this.active = checked; this.generation++;
    });
  }
  async start(): Promise<AuthState> {
    return this.exclusively(async () => {
      if (this.revoking) throw new Error('REVOCATION_REQUIRED');
      if (this.active) return this.view();
      if (this.flow && this.flow.expires > Date.now()) return this.view();
      const verifier = randomBytes(32).toString('base64url');
      const response = await this.http('/api/desktop/v1/auth/transactions', 'POST', JSON.stringify({
        challenge: createHash('sha256').update(verifier).digest('hex'), deviceLabel: 'GitViz Desktop' }));
      if (response.status !== 201) throw new Error(response.status === 429 ? 'RATE_LIMITED' : 'UNSUPPORTED_SERVER');
      const body = record(response.body);
      if (typeof body.flowId !== 'string' || !uuid.test(body.flowId) || typeof body.pollSecret !== 'string' || !/^[A-Za-z0-9_-]{43,128}$/.test(body.pollSecret) ||
          typeof body.displayCode !== 'string' || !/^[A-F0-9]{8}$/.test(body.displayCode) || typeof body.expiresAt !== 'string' ||
          !Number.isFinite(Date.parse(body.expiresAt)) || Date.parse(body.expiresAt) <= Date.now() || Date.parse(body.expiresAt) > Date.now() + 301000 || body.pollAfterMs !== 2000 ||
          body.browserUrl !== `${this.origin}/api/desktop/v1/auth/authorize?flow=${body.flowId}`) throw new Error('BAD_RESPONSE');
      this.flow = { flowId: body.flowId, verifier, pollSecret: body.pollSecret, key: randomUUID(), displayCode: body.displayCode,
        expires: Date.parse(body.expiresAt), nextPoll: Date.now() + 2000 };
      try { await this.openBrowser(body.browserUrl as string); } catch { this.flow = null; throw new Error('BROWSER_UNAVAILABLE'); }
      return this.view();
    });
  }
  async status(): Promise<AuthState> {
    return this.exclusively(async () => {
      if (!this.flow) return this.view();
      const flow = this.flow;
      if (flow.expires <= Date.now()) { this.flow = null; return this.view(); }
      if (flow.nextPoll > Date.now()) return this.view();
      // Advance before I/O: errors cannot trigger a tight retry loop or a fresh key.
      flow.nextPoll = Date.now() + 2000;
      const response: RemoteResponse = await this.http('/api/desktop/v1/auth/exchange', 'POST', JSON.stringify({
        flowId: flow.flowId, pollSecret: flow.pollSecret, verifier: flow.verifier }), undefined, flow.key);
      if (response.status === 202 || response.status === 429) {
        const retry = response.headers['retry-after'];
        const ms = retry && /^\d+$/.test(retry) ? Number(retry) * 1000 : retry ? Date.parse(retry) - Date.now() : 2000;
        flow.nextPoll = Date.now() + Math.max(2000, Math.min(Number.isFinite(ms) ? ms : 2000, 300000));
        return this.view();
      }
      if (response.status >= 500) throw new Error('AUTH_UNAVAILABLE');
      if (response.status !== 200) { this.flow = null; throw new Error('AUTH_REQUIRED'); }
      const issued = session(response.body);
      try { await this.vault.write(issued); }
      catch { this.revoking = issued; this.flow = null; this.generation++; throw new Error('STORAGE_UNAVAILABLE'); }
      this.active = issued; this.flow = null; this.generation++;
      return this.view();
    });
  }
  async logout(): Promise<AuthState> {
    return this.exclusively(async () => {
      this.revoking = this.revoking ?? this.active; this.active = null; this.flow = null; this.generation++;
      // Quarantined encrypted credentials survive restart only to retry revocation,
      // never to authenticate an account after an offline logout.
      if (this.revoking) {
        await this.vault.write({ ...this.revoking, pendingRevocation: true });
        const response = await this.http('/api/desktop/v1/session', 'DELETE', undefined, this.revoking.sessionToken);
        if (![204, 401].includes(response.status)) throw new Error('REVOCATION_REQUIRED');
        await this.vault.write(null);
        this.revoking = null;
      } else await this.vault.write(null);
      return this.view();
    });
  }
}
