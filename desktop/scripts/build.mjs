import { cp, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { createRequire } from 'node:module';
const desktop = fileURLToPath(new URL('..', import.meta.url));
await mkdir(resolve(desktop, 'dist/schema'), { recursive: true });
await cp(resolve(desktop, '../contracts/workspace/v1'), resolve(desktop, 'dist/schema'), { recursive: true });
await cp(resolve(desktop, 'ui'), resolve(desktop, 'dist/ui'), { recursive: true });
await cp(resolve(desktop, '../frontend/pages/workspace.html'), resolve(desktop, 'dist/ui/index.html'));
await cp(resolve(desktop, '../frontend/js/workspace/projects.js'), resolve(desktop, 'dist/ui/projects.js'));
await cp(resolve(desktop, '../frontend/js/workspace/status.js'), resolve(desktop, 'dist/ui/git-status.js'));
await cp(resolve(desktop, '../frontend/js/workspace/desktop-auth.js'), resolve(desktop, 'dist/ui/desktop-auth.js'));
for (const name of ['proposal', 'diff', 'impact', 'validation', 'comparison', 'inbox', 'navigation']) await cp(resolve(desktop, `../frontend/js/workspace/${name}.js`), resolve(desktop, `dist/ui/${name}.js`));
await cp(resolve(desktop, '../frontend/js/workspace/review.js'), resolve(desktop, 'dist/ui/review.js'));
await cp(resolve(desktop, '../frontend/js/workspace/changes.js'), resolve(desktop, 'dist/ui/changes.js'));
await cp(resolve(desktop, '../contracts/desktop/v1/ai-bodies.json'), resolve(desktop, 'dist/schema/ai-bodies.json'));
const { endpoints } = createRequire(import.meta.url)('../dist/src/remote-contracts.js');
await writeFile(resolve(desktop, 'dist/ui/desktop-endpoints.json'), JSON.stringify(endpoints));
await cp(resolve(desktop, '../frontend'), resolve(desktop, 'dist/ui/web'), { recursive: true, filter: source => !source.endsWith('workspace.html') });
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
