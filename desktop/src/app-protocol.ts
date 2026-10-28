import { protocol } from 'electron';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

export const APP_URL = 'gitviz://app/index.html';
const assets: Record<string, { file: string; mime: string }> = {
  '/index.html': { file: 'index.html', mime: 'text/html; charset=utf-8' },
  '/status.js': { file: 'status.js', mime: 'text/javascript; charset=utf-8' },
  '/styles.css': { file: 'styles.css', mime: 'text/css; charset=utf-8' },
  '/projects.js': { file: 'projects.js', mime: 'text/javascript; charset=utf-8' },
  '/git-status.js': { file: 'git-status.js', mime: 'text/javascript; charset=utf-8' },
  '/desktop-auth.js': { file: 'desktop-auth.js', mime: 'text/javascript; charset=utf-8' },
  '/proposal.js': { file: 'proposal.js', mime: 'text/javascript; charset=utf-8' },
  '/diff.js': { file: 'diff.js', mime: 'text/javascript; charset=utf-8' },
  '/validation.js': { file: 'validation.js', mime: 'text/javascript; charset=utf-8' }, '/comparison.js': { file: 'comparison.js', mime: 'text/javascript; charset=utf-8' }, '/impact.js': { file: 'impact.js', mime: 'text/javascript; charset=utf-8' },
  '/review.js': { file: 'review.js', mime: 'text/javascript; charset=utf-8' },
  '/changes.js': { file: 'changes.js', mime: 'text/javascript; charset=utf-8' },
  '/inbox.js': { file: 'inbox.js', mime: 'text/javascript; charset=utf-8' },
  '/navigation.js': { file: 'navigation.js', mime: 'text/javascript; charset=utf-8' },
};
const documents = new Set(['/index.html', '/web/index.html', ...['timeline', 'network', 'code-analysis', 'search', 'myprojects', 'history', 'ai-review'].map(name => `/web/pages/${name}.html`)]);
export function isAppDocument(value: string): boolean {
  try { const url = new URL(value); return url.protocol === 'gitviz:' && url.host === 'app' && !url.username && !url.password &&
    documents.has(url.pathname) && url.search.length <= 4096 && !url.hash; } catch { return false; }
}
export const CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'none'; img-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
export function registerScheme(): void {
  protocol.registerSchemesAsPrivileged([{ scheme: 'gitviz', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
}
export async function serveAssets(directory: string): Promise<void> {
  let bundled: Record<string, { file: string; mime: string }> = {};
  try { bundled = JSON.parse(await readFile(join(directory, 'assets.json'), 'utf8')) as typeof bundled; }
  catch { /* P01 standalone fixture directories can contain only the fixed assets. */ }
  protocol.handle('gitviz', async request => {
    const url = new URL(request.url);
    const asset = assets[url.pathname] ?? bundled[url.pathname];
    if (request.method !== 'GET' || url.host !== 'app' || url.username || url.password || url.search.length > 4096 || !asset) return new Response(null, { status: 404 });
    const csp = url.pathname.startsWith('/web/') ? "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; font-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'" : CSP;
    try {
      return new Response(new Uint8Array(await readFile(join(directory, asset.file))), { headers: { 'Content-Type': asset.mime, 'Content-Security-Policy': csp, 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store' } });
    } catch { return new Response(null, { status: 503 }); }
  });
}
