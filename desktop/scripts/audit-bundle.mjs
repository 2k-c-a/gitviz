/** Inspect ASAR entries without executing their code or extracting files. */
import { listPackage, statFile, extractFile } from '@electron/asar';
import { lstat, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve, sep } from 'node:path';

const archive = resolve(process.argv[2]);
const info = await lstat(archive);
if (!info.isFile() || info.isSymbolicLink() || info.size > 1024 * 1024 * 1024) throw new Error('INVALID_ARCHIVE');
const paths = listPackage(archive, {}).map(path => path.replaceAll('\\', '/').replace(/^\//, ''));
const packages = [];
for (const path of paths) {
  if (path.split('/').some(part => ['.git', '.validation', 'userdata'].includes(part.toLowerCase())) ||
    /(?:^|\/)\.env(?:\.|$)|(?:\.dpapi|\.sqlite3?|\.db)$|(?:^|\/)secret-check\.bin$/i.test(path)) throw new Error('PRIVATE_DATA_IN_ASAR');
  // @electron/asar resolves archive paths with the platform separator.
  const native = path.split('/').join(sep);
  const entry = statFile(archive, native, false);
  if ('link' in entry) throw new Error('LINK_IN_ASAR');
  if (/^node_modules\/(?:@[^/]+\/)?[^/]+\/package\.json$/.test(path)) {
    const metadata = JSON.parse(extractFile(archive, native, false).toString('utf8'));
    packages.push({ name: metadata.name, version: metadata.version, license: metadata.license ?? null });
  }
}
const report = { classification: 'static-bundle-inventory-not-vulnerability-or-license-audit', entries: paths.length,
  privateDataFiles: false, archiveSha256: createHash('sha256').update(await readFile(archive)).digest('hex'), packages };
if (process.argv[3]) await writeFile(resolve(process.argv[3]), JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
process.stdout.write(`PASS ASAR bundle: entries=${paths.length} packages=${packages.length} private data absent\n`);
