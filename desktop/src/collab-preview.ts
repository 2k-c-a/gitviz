// In-app preview of a co-edited HTML file set (AD-34). Each viewer renders their own copy of the shared document:
// the page hands main the current texts and image/font bytes, and a gitviz-preview://<id>/ protocol serves them from
// memory, so relative <link>, <script src>, ES module imports and CSS url() resolve as they would on a web server.
// Other people's JavaScript runs here, so the window is sandboxed: no preload, its own in-memory session,
// no top-level navigation away, no new windows, downloads or permissions, and no plain http, file or private addresses.
import { randomBytes } from 'node:crypto';
import { BrowserWindow, session, type Session } from 'electron';

export const PREVIEW_SCHEME = 'gitviz-preview';
const MAX_PREVIEWS = 3;
const LIMITS = { textFiles: 40, textChars: 400 * 1024, totalChars: 4 * 1024 * 1024, assets: 30, asset: 2 * 1024 * 1024, assetBytes: 10 * 1024 * 1024 };
const TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8', '.htm': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8', '.cjs': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8', '.md': 'text/plain; charset=utf-8', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
  '.webp': 'image/webp', '.avif': 'image/avif', '.ico': 'image/x-icon', '.bmp': 'image/bmp', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf', '.otf': 'font/otf',
};
export function contentType(path: string): string {
  const dot = path.lastIndexOf('.');
  return (dot >= 0 && TYPES[path.slice(dot).toLowerCase()]) || 'text/plain; charset=utf-8';
}

/** A root-relative path as the page sends it: forward slashes, no `.`/`..` segments, nothing Windows cannot name. */
export function validPath(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= 400 && !value.startsWith('/') &&
    value.split('/').every(part => part && part !== '.' && part !== '..' && !/[\\<>:"|?*]/.test(part) && ![...part].some(c => c.charCodeAt(0) < 32));
}

/** Loopback, link-local and private ranges (and their names): other people's code must not reach this PC or its network. */
export function privateHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, '');
  if (host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.local') || host.endsWith('.internal') || !host.includes('.') && !host.includes(':')) return true;
  const v4 = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(host);
  if (v4) {
    const [a, b] = [Number(v4[1]), Number(v4[2])];
    return a === 0 || a === 10 || a === 127 || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) ||
      (a === 100 && b >= 64 && b <= 127) || a >= 224;
  }
  if (host.includes(':')) {
    if (host === '::' || host === '::1') return true;
    const mapped = /^::ffff:(\d+\.\d+\.\d+\.\d+)$/.exec(host);
    if (mapped) return privateHost(mapped[1]);
    return /^f[cd]/.test(host) || /^fe[89ab]/.test(host) || /^ff/.test(host);
  }
  return false;
}

/** Whether a request from a preview must be cancelled. Only this preview's own files, data/blob URLs and (when allowed) public https/wss. */
export function blockedRequest(raw: string, id: string, external: boolean): boolean {
  let url: URL;
  try { url = new URL(raw); } catch { return true; }
  if (url.protocol === `${PREVIEW_SCHEME}:`) return url.host !== id;
  if (url.protocol === 'data:' || url.protocol === 'blob:' || url.protocol === 'devtools:') return false;
  if (url.protocol !== 'https:' && url.protocol !== 'wss:') return true;
  return !external || !!url.username || !!url.password || privateHost(url.hostname);
}

export function previewCsp(external: boolean): string {
  const remote = external ? ' https:' : '';
  return [`default-src 'self' data: blob: 'unsafe-inline' 'unsafe-eval'${remote}`, `connect-src 'self'${external ? ' https: wss:' : ''}`,
    "object-src 'none'", "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'"].join('; ');
}

interface Preview { id: string; window: BrowserWindow; session: Session; entry: string; texts: Map<string, { path: string; text: string }>;
  assets: Map<string, { path: string; bytes: Uint8Array<ArrayBuffer> }>; external: boolean; hosts: Set<string> }
type Content = { entry?: unknown; files?: unknown; assets?: unknown; external?: unknown };

function readTexts(value: unknown): Map<string, { path: string; text: string }> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('INVALID_REQUEST');
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length > LIMITS.textFiles) throw new Error('INVALID_REQUEST');
  let total = 0;
  const texts = new Map<string, { path: string; text: string }>();
  for (const [path, text] of entries) {
    if (!validPath(path) || typeof text !== 'string' || text.length > LIMITS.textChars || (total += text.length) > LIMITS.totalChars) throw new Error('INVALID_REQUEST');
    texts.set(path.toLowerCase(), { path, text });
  }
  return texts;
}

function readAssets(value: unknown, into: Map<string, { path: string; bytes: Uint8Array<ArrayBuffer> }>): void {
  if (value === undefined) return;
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('INVALID_REQUEST');
  const next = new Map(into);
  for (const [path, bytes] of Object.entries(value as Record<string, unknown>)) {
    if (!validPath(path) || !(bytes instanceof Uint8Array) || bytes.length > LIMITS.asset) throw new Error('INVALID_REQUEST');
    next.set(path.toLowerCase(), { path, bytes: new Uint8Array(bytes) });
  }
  let total = 0;
  for (const item of next.values()) total += item.bytes.length;
  if (next.size > LIMITS.assets || total > LIMITS.assetBytes) throw new Error('LIMIT');
  into.clear();
  for (const [key, item] of next) into.set(key, item);
}

export class Previews {
  private readonly previews = new Map<string, Preview>();

  constructor(private readonly devTools = false, private readonly show = true) {}

  open(value: unknown): { preview: string; hosts: string[] } {
    if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(name => !['entry', 'files', 'assets', 'external'].includes(name))) throw new Error('INVALID_REQUEST');
    const { entry, files, assets, external } = value as Content;
    if (!validPath(entry) || typeof external !== 'boolean') throw new Error('INVALID_REQUEST');
    const texts = readTexts(files);
    if (!texts.has(entry.toLowerCase()) || !/\.html?$/i.test(entry)) throw new Error('INVALID_REQUEST');
    const binaries = new Map<string, { path: string; bytes: Uint8Array<ArrayBuffer> }>();
    readAssets(assets, binaries);
    if (this.previews.size >= MAX_PREVIEWS) throw new Error('LIMIT');
    // A host name must be a valid label: a letter then hex.
    const id = `p${randomBytes(16).toString('hex')}`;
    const own = session.fromPartition(`gitviz-preview-${id}`); // no "persist:" prefix: memory only, nothing on disk
    const preview: Preview = { id, window: null as unknown as BrowserWindow, session: own, entry, texts, assets: binaries, external, hosts: new Set() };
    this.secure(preview);
    const window = new BrowserWindow({ width: 1100, height: 760, title: this.title(preview), autoHideMenuBar: true, show: this.show,
      webPreferences: { session: own, sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true, webviewTag: false, devTools: this.devTools,
        spellcheck: false, navigateOnDragDrop: false, safeDialogs: true } });
    preview.window = window;
    window.setMenu(null);
    const origin = `${PREVIEW_SCHEME}://${id}/`;
    const stay = (event: { preventDefault: () => void }, url: string) => { if (!url.startsWith(origin)) event.preventDefault(); };
    window.webContents.on('will-navigate', stay);
    window.webContents.on('will-redirect', stay);
    window.webContents.on('will-attach-webview', event => event.preventDefault());
    window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    window.webContents.on('page-title-updated', event => { event.preventDefault(); });
    window.once('closed', () => { this.forget(preview); });
    this.previews.set(id, preview);
    void window.loadURL(`${origin}${entry.split('/').map(encodeURIComponent).join('/')}`).catch(() => {});
    return { preview: id, hosts: [] };
  }

  /** Replaces the texts (all of them each time), adds image/font bytes, or switches external access; then reloads. */
  update(value: unknown): { hosts: string[] } {
    if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(name => !['preview', 'files', 'assets', 'external'].includes(name))) throw new Error('INVALID_REQUEST');
    const { preview: id, files, assets, external } = value as Content & { preview?: unknown };
    const preview = typeof id === 'string' ? this.previews.get(id) : undefined;
    if (!preview || preview.window.isDestroyed()) throw new Error('NOT_OPEN');
    if (external !== undefined && typeof external !== 'boolean') throw new Error('INVALID_REQUEST');
    const texts = files === undefined ? preview.texts : readTexts(files);
    readAssets(assets, preview.assets);
    preview.texts = texts;
    if (typeof external === 'boolean' && external !== preview.external) { preview.external = external; preview.hosts.clear(); }
    preview.window.setTitle(this.title(preview));
    preview.window.webContents.reloadIgnoringCache();
    return { hosts: [...preview.hosts] };
  }

  close(value: unknown): { closed: boolean } {
    const id = value && typeof value === 'object' ? (value as { preview?: unknown }).preview : undefined;
    const preview = typeof id === 'string' ? this.previews.get(id) : undefined;
    if (!preview) return { closed: false };
    if (!preview.window.isDestroyed()) preview.window.destroy(); // 'closed' forgets it
    this.forget(preview);
    return { closed: true };
  }

  closeAll(): void { for (const preview of [...this.previews.values()]) this.close({ preview: preview.id }); }

  private title(preview: Preview): string {
    const hosts = [...preview.hosts];
    const reach = !preview.external ? '외부 연결 꺼짐' : hosts.length ? `외부 연결: ${hosts.slice(0, 2).join(', ')}${hosts.length > 2 ? ` 외 ${hosts.length - 2}곳` : ''}` : '외부 연결 없음';
    return `미리보기 · ${preview.entry} — ${reach}`;
  }

  private secure(preview: Preview): void {
    const own = preview.session;
    own.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    own.setPermissionCheckHandler(() => false);
    own.setDevicePermissionHandler(() => false);
    own.on('will-download', event => event.preventDefault());
    own.webRequest.onBeforeRequest((details, callback) => {
      const blocked = blockedRequest(details.url, preview.id, preview.external);
      if (!blocked) {
        try {
          const url = new URL(details.url);
          if ((url.protocol === 'https:' || url.protocol === 'wss:') && !preview.hosts.has(url.host)) {
            preview.hosts.add(url.host);
            if (!preview.window?.isDestroyed()) preview.window?.setTitle(this.title(preview));
          }
        } catch { /* blocked above */ }
      }
      callback({ cancel: blocked });
    });
    own.protocol.handle(PREVIEW_SCHEME, request => {
      let url: URL;
      try { url = new URL(request.url); } catch { return new Response(null, { status: 400 }); }
      if (url.host !== preview.id || request.method !== 'GET') return new Response(null, { status: 404 });
      let path: string;
      try { path = decodeURIComponent(url.pathname).slice(1) || preview.entry; } catch { return new Response(null, { status: 400 }); }
      const text = preview.texts.get(path.toLowerCase());
      const asset = text ? undefined : preview.assets.get(path.toLowerCase());
      if (!text && !asset) return new Response('이 파일은 공동 편집 묶음에 없습니다.', { status: 404, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
      const headers = { 'Content-Type': contentType(path), 'Content-Security-Policy': previewCsp(preview.external), 'X-Content-Type-Options': 'nosniff',
        'Cache-Control': 'no-store', 'Cross-Origin-Opener-Policy': 'same-origin', 'Referrer-Policy': 'no-referrer' };
      return new Response(text ? text.text : asset!.bytes, { status: 200, headers });
    });
  }

  private forget(preview: Preview): void {
    if (this.previews.get(preview.id) !== preview) return;
    this.previews.delete(preview.id);
    try { preview.session.protocol.unhandle(PREVIEW_SCHEME); } catch { /* already gone */ }
    void preview.session.clearStorageData().catch(() => {});
    void preview.session.clearCache().catch(() => {});
  }
}
