import { mkdir, open, readFile, lstat, link, unlink } from 'node:fs/promises';
import { dirname, join, parse } from 'node:path';
import { randomBytes, randomUUID } from 'node:crypto';

export interface ProtectedStorage {
  available(): Promise<boolean>;
  encrypt(value: string): Promise<Buffer>;
  decrypt(value: Buffer): Promise<string>;
}

export async function rejectLinks(path: string): Promise<void> {
  let current = path;
  for (;;) {
    try { if ((await lstat(current)).isSymbolicLink()) throw new Error('ACCESS_DENIED'); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
    if (current === parse(current).root) break;
    current = dirname(current);
  }
}

export async function loadKey(file: string, storage: ProtectedStorage): Promise<Buffer> {
  if (!await storage.available()) throw new Error('STORAGE_UNAVAILABLE');
  await rejectLinks(file);
  await mkdir(dirname(file), { recursive: true });
  try {
    const info = await lstat(file);
    if (!info.isFile() || info.size > 16384) throw new Error('STORAGE_UNAVAILABLE');
    const value = await storage.decrypt(await readFile(file));
    if (!/^[A-Za-z0-9+/]{43}=$/.test(value)) throw new Error('STORAGE_UNAVAILABLE');
    const key = Buffer.from(value, 'base64');
    if (key.length !== 32) throw new Error('STORAGE_UNAVAILABLE');
    return key;
  } catch (error) {
    // eslint-disable-next-line preserve-caught-error -- Retaining OS exceptions could expose secret data or local paths.
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw new Error('STORAGE_UNAVAILABLE');
  }
  for (const name of ['secret-check.bin', 'workspace.sqlite3']) {
    try {
      await lstat(join(dirname(file), name));
      throw new Error('STORAGE_UNAVAILABLE');
    } catch (error) {
      // eslint-disable-next-line preserve-caught-error -- Do not retain filesystem exception paths.
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw new Error('STORAGE_UNAVAILABLE');
    }
  }
  const key = randomBytes(32);
  const temporary = `${file}.${randomUUID()}.pending`;
  try {
    const ciphertext = await storage.encrypt(key.toString('base64'));
    const handle = await open(temporary, 'wx', 0o600);
    try { await handle.writeFile(ciphertext); await handle.sync(); } finally { await handle.close(); }
    // Publish a fully flushed file exclusively; rename could overwrite another key.
    // Failure on a filesystem without hard links is intentionally fail-closed.
    await link(temporary, file);
    return key;
  } catch { key.fill(0); throw new Error('STORAGE_UNAVAILABLE'); }
  finally { try { await unlink(temporary); } catch { /* Preserve failed encrypted staging files for diagnosis. */ } }
}
