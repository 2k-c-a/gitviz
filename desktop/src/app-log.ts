import { appendFileSync, mkdirSync, readdirSync, renameSync, rmSync, statSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Rotating app log (K-02). Like diagnostics.ts it is a fixed projection: only known event names and short
 * code-like values are written. Never pass error messages, URLs, paths, tokens, environment values or agent stderr.
 */
export type LogEvent =
  | 'app.start' | 'app.quit' | 'startup.failed' | 'server.origin' | 'git.check'
  | 'agent.ready' | 'agent.failed' | 'process.crash' | 'renderer.gone' | 'child.gone';
type Value = string | number | boolean | null | undefined;

const FILE = 'app.log';
const SAFE_VALUE = /^[A-Za-z0-9_.:-]{1,64}$/;
const SAFE_KEY = /^[a-z][A-Za-z]{0,31}$/;
const CODE = /^[A-Z][A-Z0-9_]{1,63}$/;
const ERROR_NAMES = new Set(['Error', 'TypeError', 'RangeError', 'SyntaxError', 'ReferenceError', 'EvalError', 'URIError', 'AggregateError']);

/** An upper-case code such as STORAGE_UNAVAILABLE, a built-in error class name, or ERROR. Free text never passes. */
export function errorCode(error: unknown): string {
  if (error instanceof Error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (typeof code === 'string' && CODE.test(code)) return code;
    if (CODE.test(error.message)) return error.message;
    if (ERROR_NAMES.has(error.name)) return error.name;
  }
  return 'ERROR';
}

export function formatLine(time: Date, event: LogEvent, fields: Record<string, Value> = {}): string {
  const parts = [time.toISOString(), event];
  for (const [key, value] of Object.entries(fields)) {
    if (!SAFE_KEY.test(key) || value === undefined) continue;
    const text = value === null ? 'null' : typeof value === 'number' ? (Number.isFinite(value) ? String(value) : 'NaN') : String(value);
    parts.push(`${key}=${typeof value === 'string' && !SAFE_VALUE.test(text) ? '[redacted]' : text}`);
  }
  return parts.join(' ') + '\n';
}

export type LogOptions = { maxBytes?: number; files?: number; maxAgeDays?: number; now?: () => Date };

export class AppLog {
  private readonly maxBytes: number;
  private readonly files: number;
  private readonly now: () => Date;
  private disabled = false;

  constructor(private readonly directory: string, options: LogOptions = {}) {
    this.maxBytes = options.maxBytes ?? 1024 * 1024;
    this.files = Math.max(1, options.files ?? 5);
    this.now = options.now ?? (() => new Date());
    try {
      mkdirSync(directory, { recursive: true });
      this.prune((options.maxAgeDays ?? 14) * 86_400_000);
    } catch { this.disabled = true; }
  }

  get path(): string { return join(this.directory, FILE); }

  /** Logging must never break the app: every failure is swallowed. Synchronous so the line survives a crash. */
  write(event: LogEvent, fields?: Record<string, Value>): void {
    if (this.disabled) return;
    try {
      const line = formatLine(this.now(), event, fields);
      let size = 0;
      try { size = statSync(this.path).size; } catch { /* first write */ }
      if (size > 0 && size + Buffer.byteLength(line) > this.maxBytes) this.rotate();
      appendFileSync(this.path, line, 'utf8');
    } catch { /* best effort */ }
  }

  private rotated(index: number): string { return join(this.directory, `app.${index}.log`); }

  private rotate(): void {
    if (this.files === 1) { rmSync(this.path, { force: true }); return; }
    rmSync(this.rotated(this.files - 1), { force: true });
    for (let index = this.files - 2; index >= 1; index -= 1) {
      try { renameSync(this.rotated(index), this.rotated(index + 1)); } catch { /* missing */ }
    }
    renameSync(this.path, this.rotated(1));
  }

  /** Removes rotated files older than the retention window and any beyond the file count. The current file is kept. */
  private prune(maxAge: number): void {
    const limit = this.now().getTime() - maxAge;
    for (const name of readdirSync(this.directory)) {
      const match = /^app\.(\d+)\.log$/.exec(name);
      if (!match) continue;
      const file = join(this.directory, name);
      try { if (Number(match[1]) >= this.files || statSync(file).mtimeMs < limit) rmSync(file, { force: true }); } catch { /* best effort */ }
    }
  }
}
