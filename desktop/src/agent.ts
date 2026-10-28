import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import type { Health, Operation, Request } from '../contracts/workspace';
import { unavailable } from '../contracts/workspace';
import { Contracts, FrameReader, MAX_FRAME } from './contracts';

type Pending = { operation: Operation; projectId?: string; resolve: (value: Record<string, unknown>) => void; reject: (error: Error) => void; timer: NodeJS.Timeout };
export class Agent {
  private child: ChildProcessWithoutNullStreams | undefined;
  private readonly pending = new Map<string, Pending>();
  private state: Health = unavailable();
  private dead = false;
  private exit: Promise<void> = Promise.resolve();
  constructor(private readonly contracts: Contracts, private readonly launch: typeof spawn = spawn) {}
  health(): Health { return structuredClone(this.state); }

  async start(executable: string, dataDirectory: string, key: () => Promise<Buffer>): Promise<void> {
    if (this.child || this.dead) throw new Error('BUSY');
    // No project .env, DATABASE_URL, OAuth, credentials or caller-supplied environment.
    const env: NodeJS.ProcessEnv = {};
    for (const name of ['SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'LOCALAPPDATA']) if (process.env[name]) env[name] = process.env[name];
    env.PYTHONDONTWRITEBYTECODE = '1'; env.PYTHONUTF8 = '1';
    const child = this.launch(executable, ['--data-dir', dataDirectory, '--parent-pid', String(process.pid)], { cwd: process.resourcesPath, env, shell: false, windowsHide: true, stdio: 'pipe' });
    this.child = child;
    this.exit = new Promise(resolve => { child.once('close', () => { this.fail(); resolve(); }); });
    child.once('error', () => this.fail());
    child.stdin.on('error', () => { this.fail(); child.kill(); });
    // Discard raw stderr; never render/log potentially sensitive child output.
    let diagnosticsBytes = 0;
    child.stderr.on('data', (chunk: Buffer) => {
      diagnosticsBytes += chunk.length;
      if (diagnosticsBytes > 65536) { this.fail(); child.kill(); }
    });
    const reader = new FrameReader();
    child.stdout.on('data', (chunk: Buffer) => {
      try {
        for (const value of reader.push(chunk)) {
          this.contracts.response(value);
          const entry = this.pending.get(value.requestId);
          if (!entry) throw new Error('UNSUPPORTED_PROTOCOL');
          clearTimeout(entry.timer); this.pending.delete(value.requestId);
          if (!value.ok) { entry.reject(new Error(value.error.code)); continue; }
          const result = value.result;
          this.contracts.result(entry.operation, result);
          if (entry.projectId && (!entry.operation.startsWith('workspace.projects.') ? result.projectId !== entry.projectId : ['workspace.projects.get', 'workspace.projects.updateSettings', 'workspace.projects.setEditor'].includes(entry.operation) && result.id !== entry.projectId)) throw new Error('UNSUPPORTED_PROTOCOL');
          const correct = entry.operation.endsWith('handshake') ? result.state === 'initializing' : entry.operation.endsWith('health') ? ['ready', 'degraded', 'unavailable'].includes(String(result.state)) : entry.operation.endsWith('shutdown') ? result.accepted === true : true;
          if (!correct) { entry.reject(new Error('UNSUPPORTED_PROTOCOL')); throw new Error('UNSUPPORTED_PROTOCOL'); }
          entry.resolve(result);
        }
      } catch { this.fail(); child.kill(); }
    });
    child.stdout.on('end', () => {
      try { reader.finish(); } catch { /* Truncated protocol is a component failure. */ }
      this.fail(); child.kill();
    });
    const deadline = setTimeout(() => { this.fail(); child.kill(); }, 15000);
    try {
      const hello = await this.request('workspace.system.handshake', { appVersion: '0.8.0', protocolVersion: 1, localSchemaRange: { min: 7, max: 7 } });
      const capabilities = hello.capabilities as string[];
      const range = hello.localSchemaRange as { min: number; max: number };
      if (range.min > 7 || range.max < 7 || range.min > range.max) throw new Error('UNSUPPORTED_PROTOCOL');
      if (!['workspace.system.health', 'workspace.system.shutdown'].every(op => capabilities.includes(op))) throw new Error('UNSUPPORTED_PROTOCOL');
      const secret = await key();
      try { this.write({ type: 'bootstrap', protocolVersion: 1, keyVersion: 1, keyBase64: secret.toString('base64') }, 'bootstrap'); }
      finally { secret.fill(0); }
      const health = await this.request('workspace.system.health', {});
      this.state = health as unknown as Health;
      if (this.state.state !== 'ready') throw new Error('STORAGE_UNAVAILABLE');
    } catch { this.fail(); child.kill(); throw new Error('STORAGE_UNAVAILABLE'); }
    finally { clearTimeout(deadline); }
  }

  private write(value: unknown, kind: 'request' | 'bootstrap'): void {
    this.contracts.validate(kind, value);
    const bytes = Buffer.from(JSON.stringify(value));
    if (bytes.length > MAX_FRAME || this.dead || !this.child?.stdin.writable) throw new Error('STORAGE_UNAVAILABLE');
    this.child.stdin.write(Buffer.concat([bytes, Buffer.from('\n')]));
  }
  call(operation: Operation, payload: Record<string, unknown>, options: { projectId?: string; idempotencyKey?: string } = {}): Promise<Record<string, unknown>> {
    if (this.state.state !== 'ready' || operation.startsWith('workspace.system.')) return Promise.reject(new Error('STORAGE_UNAVAILABLE'));
    return this.request(operation, payload, options);
  }
  private request(operation: Operation, payload: Record<string, unknown>, options: { projectId?: string; idempotencyKey?: string } = {}): Promise<Record<string, unknown>> {
    if (this.pending.size >= 16 || this.dead) return Promise.reject(new Error('BUSY'));
    const request: Request = { protocolVersion: 1, requestId: randomUUID(), operation, payload, ...options };
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(request.requestId); reject(new Error('TIMED_OUT')); this.fail(); this.child?.kill(); }, operation.startsWith('workspace.system.') ? 5000 : ['workspace.changes.capture', 'workspace.proposals.apply', 'workspace.proposals.recover', 'workspace.validation.prepare', 'workspace.validation.approve', 'workspace.validation.start', 'workspace.validation.get', 'workspace.comparisons.create'].includes(operation) ? 60000 : 15000);
      this.pending.set(request.requestId, { operation, projectId: options.projectId, resolve, reject, timer });
      try { this.write(request, 'request'); } catch { clearTimeout(timer); this.pending.delete(request.requestId); reject(new Error('STORAGE_UNAVAILABLE')); }
    });
  }
  private fail(): void {
    const errorCode = this.state.errorCode;
    this.dead = true; this.state = { ...unavailable(), ...(errorCode ? { errorCode } : {}) };
    for (const entry of this.pending.values()) { clearTimeout(entry.timer); entry.reject(new Error('STORAGE_UNAVAILABLE')); }
    this.pending.clear();
  }
  async stop(): Promise<void> {
    const child = this.child;
    if (!child) return;
    if (!this.dead) { try { await this.request('workspace.system.shutdown', {}); child.stdin.end(); } catch { /* kill below */ } }
    let timer: NodeJS.Timeout | undefined;
    await Promise.race([this.exit, new Promise<void>(resolve => { timer = setTimeout(resolve, 5000); })]);
    if (timer) clearTimeout(timer);
    if (child.exitCode === null) child.kill();
    this.fail();
  }
}
