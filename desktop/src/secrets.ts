import { mkdir, open, readFile, lstat, link, rename, unlink } from 'node:fs/promises';
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

export type AiProvider = 'anthropic' | 'openai' | 'gemini';
export const AI_PROVIDERS: readonly AiProvider[] = ['anthropic', 'openai', 'gemini'];
const AI_KEY = /^[!-~]{16,512}$/;
/** BYOK provider keys at rest: one DPAPI blob owned by main. Keys never cross to the renderer or the agent. */
export class AiKeyVault {
  constructor(private readonly file: string, private readonly storage: ProtectedStorage) {}
  private async load(): Promise<Partial<Record<AiProvider, string>>> {
    await rejectLinks(this.file);
    try {
      const info = await lstat(this.file); if (!info.isFile() || info.size > 16384) throw new Error('STORAGE_UNAVAILABLE');
      const value = JSON.parse(await this.storage.decrypt(await readFile(this.file))) as unknown;
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('STORAGE_UNAVAILABLE');
      const keys: Partial<Record<AiProvider, string>> = {};
      for (const provider of AI_PROVIDERS) {
        const key = (value as Record<string, unknown>)[provider];
        if (typeof key === 'string' && AI_KEY.test(key)) keys[provider] = key;
      }
      return keys;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return {};
      // eslint-disable-next-line preserve-caught-error -- No decrypted secret or filesystem path in errors.
      throw new Error('STORAGE_UNAVAILABLE');
    }
  }
  async status(): Promise<Record<AiProvider, boolean>> {
    const keys = await this.load();
    return { anthropic: Boolean(keys.anthropic), openai: Boolean(keys.openai), gemini: Boolean(keys.gemini) };
  }
  async get(provider: AiProvider): Promise<string | null> { return (await this.load())[provider] ?? null; }
  async set(provider: AiProvider, key: string | null): Promise<void> {
    if (!AI_PROVIDERS.includes(provider) || (key !== null && !AI_KEY.test(key))) throw new Error('INVALID_REQUEST');
    const keys = await this.load();
    if (key === null) delete keys[provider]; else keys[provider] = key;
    if (!Object.keys(keys).length) {
      try { await unlink(this.file); } catch (error) {
        // eslint-disable-next-line preserve-caught-error -- No filesystem path in errors.
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw new Error('STORAGE_UNAVAILABLE');
      }
      return;
    }
    if (!await this.storage.available()) throw new Error('STORAGE_UNAVAILABLE');
    await mkdir(dirname(this.file), { recursive: true });
    const temporary = `${this.file}.${randomUUID()}.pending`;
    try {
      const handle = await open(temporary, 'wx', 0o600);
      try { await handle.writeFile(await this.storage.encrypt(JSON.stringify(keys))); await handle.sync(); } finally { await handle.close(); }
      await rename(temporary, this.file);
    } catch {
      try { await unlink(temporary); } catch { /* Nothing staged. */ }
      throw new Error('STORAGE_UNAVAILABLE');
    }
  }
}
