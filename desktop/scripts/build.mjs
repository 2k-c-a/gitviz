import { cp, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { createRequire } from 'node:module';
const desktop = fileURLToPath(new URL('..', import.meta.url));
await mkdir(resolve(desktop, 'dist/schema'), { recursive: true });
// Release builds embed the sign-in server origin (public, not a secret) so a fresh install can sign in without any setup.
const serverOrigin = process.env.GITVIZ_BUILD_SERVER_ORIGIN || null;
if (serverOrigin) {
  const url = new URL(serverOrigin);
  if (url.protocol !== 'https:' || url.origin !== serverOrigin || url.username || url.password) throw new Error('GITVIZ_BUILD_SERVER_ORIGIN must be a bare https:// origin');
}
// Local staging builds (bootstrap -App -Staging) also embed that server's self-signed certificate. Loopback origins only.
let certificate = null;
if (process.env.GITVIZ_BUILD_SERVER_CA) {
  const host = serverOrigin && new URL(serverOrigin).hostname;
  if (!['localhost', '127.0.0.1', '[::1]'].includes(host)) throw new Error('GITVIZ_BUILD_SERVER_CA needs a loopback GITVIZ_BUILD_SERVER_ORIGIN');
  const { X509Certificate } = await import('node:crypto');
  const parsed = new X509Certificate(await readFile(process.env.GITVIZ_BUILD_SERVER_CA));
  if (!(host.startsWith('[') || /^\d/.test(host) ? parsed.checkIP(host.replace(/^\[|\]$/g, '')) : parsed.checkHost(host))) throw new Error(`GITVIZ_BUILD_SERVER_CA does not name ${host}`);
  certificate = parsed.toString();
}
await mkdir(resolve(desktop, 'dist/config'), { recursive: true });
await writeFile(resolve(desktop, 'dist/config/server.json'), JSON.stringify({ origin: serverOrigin, ...(certificate ? { certificate } : {}) }));
await cp(resolve(desktop, '../contracts/workspace/v1'), resolve(desktop, 'dist/schema'), { recursive: true });
await cp(resolve(desktop, 'ui'), resolve(desktop, 'dist/ui'), { recursive: true });
await cp(resolve(desktop, '../frontend/pages/workspace.html'), resolve(desktop, 'dist/ui/index.html'));
await cp(resolve(desktop, '../frontend/js/workspace/projects.js'), resolve(desktop, 'dist/ui/projects.js'));
await cp(resolve(desktop, '../frontend/js/workspace/status.js'), resolve(desktop, 'dist/ui/git-status.js'));
await cp(resolve(desktop, '../frontend/js/workspace/desktop-auth.js'), resolve(desktop, 'dist/ui/desktop-auth.js'));
for (const name of ['proposal', 'diff', 'impact', 'validation', 'comparison', 'inbox', 'navigation', 'layout', 'ai-settings', 'auto-compare', 'dependencies']) await cp(resolve(desktop, `../frontend/js/workspace/${name}.js`), resolve(desktop, `dist/ui/${name}.js`));
await cp(resolve(desktop, '../frontend/js/workspace/review.js'), resolve(desktop, 'dist/ui/review.js'));
await cp(resolve(desktop, '../frontend/js/workspace/changes.js'), resolve(desktop, 'dist/ui/changes.js'));
// One body catalog for main and tests: the AI bodies plus the community bodies (names never overlap).
const bodyFiles = ['ai-bodies.json', 'community-bodies.json'];
const bodies = {};
for (const name of bodyFiles) {
  for (const [key, schema] of Object.entries(JSON.parse(await readFile(resolve(desktop, `../contracts/desktop/v1/${name}`), 'utf8')))) {
    if (key in bodies) throw new Error(`Duplicate desktop body schema ${key}`);
    bodies[key] = schema;
  }
}
await writeFile(resolve(desktop, 'dist/schema/ai-bodies.json'), JSON.stringify(bodies));
const { endpoints } = createRequire(import.meta.url)('../dist/src/remote-contracts.js');
await writeFile(resolve(desktop, 'dist/ui/desktop-endpoints.json'), JSON.stringify(endpoints));
await cp(resolve(desktop, '../frontend'), resolve(desktop, 'dist/ui/web'), { recursive: true, filter: source => !source.endsWith('workspace.html') });
// Co-editing editor (AD-29): CodeMirror 6 + Yjs bundled for the app only; the web server never serves it.
await (await import('esbuild')).build({ entryPoints: [resolve(desktop, 'scripts/collab-editor.entry.mjs')], outfile: resolve(desktop, 'dist/ui/web/js/collab-editor.bundle.js'),
  bundle: true, format: 'iife', globalName: 'GitVizCollab', target: 'es2022', minify: true, legalComments: 'eof', logLevel: 'warning',
  // The page loads it with import(), where a top-level var stays module-scoped; publish the API on globalThis explicitly.
  footer: { js: 'globalThis.GitVizCollab = GitVizCollab;' } });
const manifest = { '/desktop-endpoints.json': { file: 'desktop-endpoints.json', mime: 'application/json' } };
const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.png': 'image/png', '.svg': 'image/svg+xml', '.jpg': 'image/jpeg', '.json': 'application/json' };
async function inventory(relative) {
  for (const entry of await readdir(resolve(desktop, 'dist/ui', relative), { withFileTypes: true })) {
    const path = `${relative}/${entry.name}`;
    if (entry.isDirectory()) await inventory(path);
    else if (entry.isFile()) {
      const type = mime[path.slice(path.lastIndexOf('.'))];
      if (type) manifest[`/${path}`] = { file: path, mime: type };
    }
  }
}
await inventory('web');
await writeFile(resolve(desktop, 'dist/ui/assets.json'), JSON.stringify(manifest));
const preload = await readFile(resolve(desktop, 'dist/preload/preload.js'), 'utf8');
const imports = [...preload.matchAll(/require\(["']([^"']+)["']\)/g)].map(match => match[1]);
if (imports.some(name => name !== 'electron') || /\bimport\s*\(/.test(preload)) throw new Error('UNSAFE_PRELOAD_IMPORT');
