// Host-side file access for co-editing (AD-29). Main process only: the page gets an opaque handle and text, never a path.
// The host picks the file in a native dialog; only the host's app ever writes it, after the host presses save.
import { createHash, randomUUID } from 'node:crypto';
import { lstat, readFile, rename, unlink, writeFile } from 'node:fs/promises';
import { basename, dirname, extname, join } from 'node:path';

export const MAX_FILE_BYTES = 100 * 1024;
const MAX_TEXT = 400 * 1024;
export const LANGUAGES: Record<string, string> = {
  '.js': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript', '.ts': 'typescript', '.mts': 'typescript', '.cts': 'typescript',
  '.jsx': 'jsx', '.tsx': 'tsx', '.py': 'python', '.json': 'json', '.md': 'markdown', '.css': 'css', '.html': 'html', '.htm': 'html',
};

export interface OpenedFile { handle: string; name: string; text: string; language: string }
interface Entry { path: string; hash: string; crlf: boolean; bom: boolean }

export const digest = (bytes: Uint8Array) => createHash('sha256').update(bytes).digest('hex');

export async function readText(path: string): Promise<{ bytes: Buffer; text: string; bom: boolean }> {
  const info = await lstat(path);
  if (!info.isFile() || info.isSymbolicLink()) throw new Error('INVALID_FILE');
  if (info.size > MAX_FILE_BYTES) throw new Error('FILE_TOO_LARGE');
  const bytes = await readFile(path);
  if (bytes.length > MAX_FILE_BYTES) throw new Error('FILE_TOO_LARGE');
  let text: string;
  try { text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes); } catch { throw new Error('NOT_TEXT'); }
  if (text.includes('\u0000')) throw new Error('NOT_TEXT');
  const bom = text.startsWith('﻿');
  return { bytes, text: bom ? text.slice(1) : text, bom };
}

/** Writes shared text back with the file's original line endings and BOM, through a temp file and rename. Returns the new hash. */
export async function writeText(entry: { path: string; crlf: boolean; bom: boolean }, text: string): Promise<string> {
  const body = Buffer.from((entry.bom ? '﻿' : '') + (entry.crlf ? text.replace(/\r?\n/g, '\r\n') : text), 'utf8');
  const temporary = join(dirname(entry.path), `.${basename(entry.path)}.${randomUUID()}.gitviz-tmp`);
  try {
    await writeFile(temporary, body, { flag: 'wx' });
    await rename(temporary, entry.path);
  } catch {
    await unlink(temporary).catch(() => {});
    throw new Error('SAVE_FAILED');
  }
  return digest(body);
}

export class CollabFiles {
  private readonly files = new Map<string, Entry>();

  constructor(private readonly pick: () => Promise<string | null>, private readonly confirmOverwrite: (name: string) => Promise<boolean>) {}

  async open(): Promise<OpenedFile | { cancelled: true }> {
    const path = await this.pick();
    if (!path) return { cancelled: true };
    const { bytes, text, bom } = await readText(path);
    const crlf = text.includes('\r\n');
    const handle = randomUUID();
    this.files.set(handle, { path, hash: digest(bytes), crlf, bom });
    // The editor and the shared document use \n; the original line endings come back on save.
    return { handle, name: basename(path), text: text.replace(/\r\n/g, '\n'), language: LANGUAGES[extname(path).toLowerCase()] ?? 'plain' };
  }

  async save(value: unknown): Promise<{ saved: true }> {
    const { handle, text } = (value && typeof value === 'object' && !Array.isArray(value) ? value : {}) as { handle?: unknown; text?: unknown };
    if (typeof handle !== 'string' || typeof text !== 'string' || text.length > MAX_TEXT || Object.keys(value as object).length !== 2) throw new Error('INVALID_REQUEST');
    const entry = this.files.get(handle);
    if (!entry) throw new Error('NOT_OPEN');
    let current: string | null;
    try { current = digest((await readText(entry.path)).bytes); } catch { current = null; }
    // Someone changed (or removed) the file on disk since it was opened or last saved: the host decides in a native dialog.
    if (current !== entry.hash && !(await this.confirmOverwrite(basename(entry.path)))) throw new Error('FILE_CHANGED');
    entry.hash = await writeText(entry, text);
    return { saved: true };
  }

  close(value: unknown): { closed: boolean } {
    const handle = value && typeof value === 'object' ? (value as { handle?: unknown }).handle : undefined;
    return { closed: typeof handle === 'string' && this.files.delete(handle) };
  }

  closeAll(): void { this.files.clear(); }
}
