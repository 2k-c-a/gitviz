import type { RemoteContracts } from './remote-contracts';

export interface RemoteResponse { status: number; body: unknown; headers: Record<string, string> }
export type Http = (path: string, method: string, body?: string, token?: string, key?: string) => Promise<RemoteResponse>;

export function httpsClient(origin: string, transport: typeof fetch = fetch): Http {
  const base = new URL(origin);
  if (base.protocol !== 'https:' || base.origin !== origin || base.username || base.password) throw new Error('INVALID_SERVER');
  return async (path, method, body, token, key) => {
    if (!path.startsWith('/') || path.startsWith('//') || /[\\\r\n#]/.test(path)) throw new Error('INVALID_REQUEST');
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (token) headers.Authorization = `Bearer ${token}`;
    if (key) headers['Idempotency-Key'] = key;
    // No cookie jar, automatic redirects, proxy credentials or renderer headers.
    let response: Response;
    try { response = await transport(origin + path, { method, headers, body, credentials: 'omit', redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(15000) }); }
    catch { throw new Error('NETWORK_UNAVAILABLE'); }
    const chunks: Uint8Array[] = []; let size = 0;
    const reader = response.body?.getReader();
    try {
      if (reader) for (;;) {
        const { done, value } = await reader.read(); if (done) break;
        size += value.byteLength;
        if (size > 8 * 1024 * 1024) { await reader.cancel(); throw new Error('OUTPUT_LIMIT'); }
        chunks.push(value);
      }
    } catch { throw new Error('BAD_RESPONSE'); }
    let parsed: unknown = null;
    if (response.status !== 204) {
      if (!response.headers.get('content-type')?.toLowerCase().includes('application/json')) throw new Error('BAD_RESPONSE');
      try { parsed = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks))); }
      catch { throw new Error('BAD_RESPONSE'); }
      if (!parsed || typeof parsed !== 'object') throw new Error('BAD_RESPONSE');
    }
    const safe: Record<string, string> = {};
    for (const name of ['retry-after', 'x-request-id']) {
      const v = response.headers.get(name); if (v && v.length <= 128 && !/[\r\n]/.test(v)) safe[name] = v;
    }
    return { status: response.status, body: parsed, headers: safe };
  };
}

export class Remote {
  private pending = 0;
  constructor(private readonly contracts: RemoteContracts, private readonly http: Http,
    private readonly identity: () => { token: string; generation: number } | null,
    private readonly invalidate: () => void) {}
  async request(value: unknown): Promise<RemoteResponse> {
    const route = this.contracts.route(value);
    if (this.pending >= 8) throw new Error('BUSY');
    const before = this.identity(); if (!before) throw new Error('AUTH_REQUIRED');
    this.pending++;
    try {
      const response = await this.http(route.path, route.method, route.body, before.token, route.idempotencyKey);
      if (this.identity()?.generation !== before.generation) throw new Error('ACCOUNT_CHANGED');
      if (response.status === 401) this.invalidate();
      return response;
    } finally { this.pending--; }
  }
}
