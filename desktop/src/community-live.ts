// Community chat live events (AD-27). Main process only: the bearer token never reaches the renderer.
// The server pushes server-sent events; sending stays an allowlisted Remote request.

export interface LiveEvent { type: 'ready' | 'message' | 'deleted' | 'presence' | 'removed' | 'voice' | 'voice-signal' | 'status'; data: Record<string, unknown> }
export type StreamResponse = { status: number; contentType: string; body: ReadableStream<Uint8Array> | null };
export type StreamHttp = (path: string, token: string, signal: AbortSignal) => Promise<StreamResponse>;

const EVENTS = new Set(['ready', 'message', 'deleted', 'presence', 'removed', 'voice', 'voice-signal']);
const MAX_EVENT = 64 * 1024;

export function streamClient(origin: string, transport: typeof fetch = fetch): StreamHttp {
  const base = new URL(origin);
  if (base.protocol !== 'https:' || base.origin !== origin || base.username || base.password) throw new Error('INVALID_SERVER');
  return async (path, token, signal) => {
    if (!/^\/api\/community\/v1\/repositories\/[1-9]\d{0,15}\/chat\/stream$/.test(path)) throw new Error('INVALID_REQUEST');
    // Same boundary as the request client: no cookies, redirects or renderer headers.
    const response = await transport(origin + path, { method: 'GET', headers: { Accept: 'text/event-stream', Authorization: `Bearer ${token}` },
      credentials: 'omit', redirect: 'error', cache: 'no-store', signal });
    return { status: response.status, contentType: response.headers.get('content-type')?.toLowerCase() ?? '', body: response.body };
  };
}

/** Incremental text/event-stream parser that only keeps named events with a JSON object payload. */
export class EventParser {
  private buffer = '';
  private readonly decoder = new TextDecoder('utf-8', { fatal: true });
  push(chunk: Uint8Array): LiveEvent[] {
    this.buffer += this.decoder.decode(chunk, { stream: true });
    const out: LiveEvent[] = [];
    for (;;) {
      const match = /\r\n\r\n|\n\n|\r\r/.exec(this.buffer);
      if (!match) break;
      const block = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      let type = 'message'; const data: string[] = [];
      for (const line of block.split(/\r\n|\n|\r/)) {
        if (!line || line.startsWith(':')) continue;
        const colon = line.indexOf(':');
        const field = colon < 0 ? line : line.slice(0, colon);
        const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '');
        if (field === 'event') type = value;
        else if (field === 'data') data.push(value);
      }
      if (!data.length || !EVENTS.has(type)) continue;
      let parsed: unknown;
      try { parsed = JSON.parse(data.join('\n')); } catch { continue; }
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) out.push({ type: type as LiveEvent['type'], data: parsed as Record<string, unknown> });
    }
    if (this.buffer.length > MAX_EVENT) throw new Error('OUTPUT_LIMIT');
    return out;
  }
}

export interface LiveOptions { minDelay?: number; maxDelay?: number; idle?: number }

/** One room at a time per window. Reconnects with backoff; stops for good on sign-out, removal or lost access. */
export class CommunityLive {
  private controller: AbortController | null = null;
  private room = 0;
  private readonly minDelay: number;
  private readonly maxDelay: number;
  private readonly idle: number;

  constructor(private readonly http: StreamHttp,
    private readonly identity: () => { token: string; generation: number } | null,
    private readonly invalidate: () => void,
    private readonly emit: (event: LiveEvent) => void,
    options: LiveOptions = {}) {
    this.minDelay = options.minDelay ?? 1000;
    this.maxDelay = options.maxDelay ?? 30000;
    // The server pings every 15 seconds; three missed pings count as a dead connection.
    this.idle = options.idle ?? 45000;
  }

  subscribe(value: unknown): { subscribed: true } {
    const repositoryId = value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === 1
      ? (value as { repositoryId?: unknown }).repositoryId : undefined;
    if (typeof repositoryId !== 'number' || !Number.isSafeInteger(repositoryId) || repositoryId < 1) throw new Error('INVALID_REQUEST');
    if (!this.identity()) throw new Error('AUTH_REQUIRED');
    this.unsubscribe();
    const controller = new AbortController();
    this.controller = controller; this.room = repositoryId;
    void this.run(repositoryId, controller.signal);
    return { subscribed: true };
  }

  unsubscribe(): { subscribed: false } {
    this.controller?.abort(); this.controller = null; this.room = 0;
    return { subscribed: false };
  }

  get active(): number { return this.room; }

  private send(signal: AbortSignal, event: LiveEvent): void { if (!signal.aborted) this.emit(event); }

  private stop(signal: AbortSignal, code: string): void {
    this.send(signal, { type: 'status', data: { state: 'stopped', code } });
    if (!signal.aborted) { this.controller = null; this.room = 0; }
  }

  private async run(repositoryId: number, signal: AbortSignal): Promise<void> {
    let delay = this.minDelay;
    const path = `/api/community/v1/repositories/${repositoryId}/chat/stream`;
    while (!signal.aborted) {
      const who = this.identity();
      if (!who) { this.stop(signal, 'AUTH_REQUIRED'); return; }
      this.send(signal, { type: 'status', data: { state: 'connecting' } });
      const attempt = new AbortController();
      let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
      // Abort the request and release the body even if the transport ignores the signal once the body is streaming.
      const abort = () => { attempt.abort(); void reader?.cancel().catch(() => {}); };
      signal.addEventListener('abort', abort, { once: true });
      let timer: ReturnType<typeof setTimeout> | undefined;
      const arm = () => { clearTimeout(timer); timer = setTimeout(abort, this.idle); };
      try {
        arm();
        const response = await this.http(path, who.token, attempt.signal);
        if (this.identity()?.generation !== who.generation) { void response.body?.cancel().catch(() => {}); this.stop(signal, 'ACCOUNT_CHANGED'); return; }
        if (response.status === 401) { void response.body?.cancel().catch(() => {}); this.invalidate(); this.stop(signal, 'AUTH_REQUIRED'); return; }
        if (response.status === 403 || response.status === 404) { void response.body?.cancel().catch(() => {}); this.stop(signal, response.status === 404 ? 'NOT_FOUND' : 'PERMISSION_DENIED'); return; }
        if (response.status !== 200 || !response.contentType.startsWith('text/event-stream') || !response.body) {
          void response.body?.cancel().catch(() => {});
          throw new Error('BAD_RESPONSE');
        }
        const parser = new EventParser();
        reader = response.body.getReader();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          arm();
          // Sign-out or another account: drop the stream by the next ping at the latest.
          if (this.identity()?.generation !== who.generation) { void reader.cancel().catch(() => {}); this.stop(signal, 'AUTH_REQUIRED'); return; }
          for (const event of parser.push(value)) {
            if (event.type === 'ready') { delay = this.minDelay; this.send(signal, { type: 'status', data: { state: 'live' } }); }
            this.send(signal, event);
            if (event.type === 'removed') { void reader.cancel().catch(() => {}); this.stop(signal, 'MEMBER_REMOVED'); return; }
          }
        }
      } catch { /* Network loss, idle timeout or a bad stream: reconnect below unless unsubscribed. */ }
      finally { clearTimeout(timer); signal.removeEventListener('abort', abort); }
      if (signal.aborted) return;
      this.send(signal, { type: 'status', data: { state: 'reconnecting', retryIn: delay } });
      await new Promise<void>(resolve => {
        const wait = setTimeout(done, delay);
        function done() { clearTimeout(wait); signal.removeEventListener('abort', done); resolve(); }
        signal.addEventListener('abort', done, { once: true });
      });
      delay = Math.min(delay * 2, this.maxDelay);
    }
  }
}
