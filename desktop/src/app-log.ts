// App log (K-02): a rotating, line-per-event file in the user's data folder (`<userData>\logs\app.log`, 1MB × 5 files).
// Main process only. It records what happened (event names, error codes, versions, counts and durations), never content:
// strings are masked for tokens, keys, e-mail addresses and user folder paths, and each value is cut short. The renderer cannot
// write to it, and the diagnostics export does not include it; a user sends the file to support themselves.
import { appendFileSync, mkdirSync, renameSync, rmSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

export const LOG_MAX_BYTES = 1024 * 1024;
export const LOG_FILES = 5;
const MAX_VALUE = 300;
type Level = 'info' | 'warn' | 'error';
type Value = string | number | boolean | null | undefined;

/** Removes secrets and personal paths from a string. Exported for tests. */
export function mask(text: string, home = homedir()): string {
  let out = text;
  if (home && home.length > 3) out = out.split(home).join('%USERPROFILE%').split(home.replace(/\\/g, '/')).join('%USERPROFILE%');
  out = out
    .replace(/\b[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s'"]+/gi, '%USERPROFILE%')
    .replace(/\bBearer\s+[^\s"',;]+/gi, 'Bearer [masked]')
    .replace(/\b(token|key|secret|password|authorization)(\s*[:=]\s*)("?)(?!Bearer\b|\[masked\])[^\s"',;]+/gi, '$1$2$3[masked]')
    .replace(/\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|AIza[A-Za-z0-9_-]{8,}|gvz_[A-Za-z0-9_-]{8,})/g, '[masked]')
    .replace(/[A-Za-z0-9+/_-]{32,}={0,2}/g, '[masked]')
    .replace(/[\w.+-]+@[\w-]+\.[\w.-]+/g, '[email]')
    .replace(/[\r\n\t]+/g, ' ');
  return out.length > MAX_VALUE ? `${out.slice(0, MAX_VALUE)}…` : out;
}

export class AppLog {
  private readonly file: string;
  constructor(private readonly directory: string, private readonly maxBytes = LOG_MAX_BYTES, private readonly files = LOG_FILES, private readonly now = () => new Date()) {
    this.file = join(directory, 'app.log');
  }

  write(level: Level, event: string, fields: Record<string, Value> = {}): void {
    try {
      const entry: Record<string, Value> = { time: this.now().toISOString(), level, event: mask(event).slice(0, 80) };
      for (const [name, value] of Object.entries(fields)) {
        if (!/^[A-Za-z][A-Za-z0-9_]{0,39}$/.test(name) || value === undefined) continue;
        entry[name] = typeof value === 'string' ? mask(value) : typeof value === 'number' && !Number.isFinite(value) ? null : value;
      }
      const line = `${JSON.stringify(entry)}\n`;
      mkdirSync(this.directory, { recursive: true });
      this.rotate(Buffer.byteLength(line));
      appendFileSync(this.file, line, { encoding: 'utf8', mode: 0o600 });
    } catch { /* Logging must never break the app. */ }
  }

  private rotate(incoming: number): void {
    let size: number;
    try { size = statSync(this.file).size; } catch { return; }
    if (size + incoming <= this.maxBytes) return;
    rmSync(join(this.directory, `app.${this.files - 1}.log`), { force: true });
    for (let index = this.files - 2; index >= 1; index--) {
      try { renameSync(join(this.directory, `app.${index}.log`), join(this.directory, `app.${index + 1}.log`)); } catch { /* missing */ }
    }
    renameSync(this.file, join(this.directory, 'app.1.log'));
  }
}

let current: AppLog | null = null;
/** Starts logging into `<directory>`; before this, log() does nothing. */
export function startLog(directory: string): AppLog { current = new AppLog(directory); return current; }
export function log(level: Level, event: string, fields?: Record<string, Value>): void { current?.write(level, event, fields); }
/** An error's code-like message only (e.g. FILE_CHANGED), or its class name; never a stack or a raw message with content. */
export function errorCode(error: unknown): string {
  const message = error instanceof Error ? error.message : typeof error === 'string' ? error : '';
  if (/^[A-Z][A-Z0-9_]{2,40}$/.test(message)) return message;
  const code = (error as { code?: unknown } | null)?.code;
  if (typeof code === 'string' && /^[A-Z][A-Z0-9_]{2,40}$/.test(code)) return code;
  return error instanceof Error ? error.name : 'UNKNOWN';
}
