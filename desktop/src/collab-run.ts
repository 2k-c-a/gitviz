// Running co-edited code (AD-34 phase 2·3). Every participant runs the shared files in their own app; output stays with them.
// Python: Pyodide (CPython on WebAssembly) in a hidden, sandboxed window of its own (no preload, in-memory session, all
//   network refused, standard library only). Nothing touches this PC's files; a time limit destroys the window.
// C/C++/Java: the compiler installed on this PC (gcc/clang, JDK). The shared texts are copied to a temporary folder, compiled
//   there, and the program runs inside a Windows Job Object (memory, process count, killed with the job) under a time limit.
//   This is resource limiting, not a sandbox: the program can still use the network and the user's files. The page asks
//   before running code that others changed (see community.js).
import { spawn, type ChildProcess } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { BrowserWindow, session } from 'electron';
import { validPath } from './collab-preview';
import { log } from './app-log';

export const RUN_SCHEME = 'gitviz-run';
export type RunLanguage = 'python' | 'c' | 'cpp' | 'java';
export const RUN_LIMITS = { files: 40, fileChars: 400 * 1024, totalChars: 2 * 1024 * 1024, stdin: 64 * 1024, output: 200 * 1024,
  pythonSeconds: 20, compileSeconds: 60, runSeconds: 10, memory: 512 * 1024 * 1024, javaMemory: 1024 * 1024 * 1024, processes: 8 };
export interface RunResult { language: RunLanguage; phase: 'compile' | 'run'; exitCode: number | null; timedOut: boolean; truncated: boolean;
  stdout: string; stderr: string; durationMs: number; tool: string }
export interface Runtimes { python: string; c: string | null; cpp: string | null; java: string | null }

const SOURCES: Record<Exclude<RunLanguage, 'python'>, RegExp> = { c: /\.c$/i, cpp: /\.(?:cpp|cc|cxx)$/i, java: /\.java$/i };
export function languageFor(entry: string): RunLanguage | null {
  if (/\.py$/i.test(entry)) return 'python';
  if (SOURCES.c.test(entry)) return 'c';
  if (SOURCES.cpp.test(entry)) return 'cpp';
  if (SOURCES.java.test(entry)) return 'java';
  return null;
}

type Request = { entry: string; files: Map<string, { path: string; text: string }>; stdin: string; language: RunLanguage };
/** Validates a run request from the page: a runnable entry among the given texts, within the limits. */
export function readRequest(value: unknown): Request {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(name => !['entry', 'files', 'stdin'].includes(name))) throw new Error('INVALID_REQUEST');
  const { entry, files, stdin = '' } = value as { entry?: unknown; files?: unknown; stdin?: unknown };
  if (!validPath(entry) || typeof stdin !== 'string' || stdin.length > RUN_LIMITS.stdin) throw new Error('INVALID_REQUEST');
  if (!files || typeof files !== 'object' || Array.isArray(files)) throw new Error('INVALID_REQUEST');
  const entries = Object.entries(files as Record<string, unknown>);
  if (entries.length > RUN_LIMITS.files) throw new Error('INVALID_REQUEST');
  const texts = new Map<string, { path: string; text: string }>();
  let total = 0;
  for (const [path, text] of entries) {
    if (!validPath(path) || typeof text !== 'string' || text.length > RUN_LIMITS.fileChars || (total += text.length) > RUN_LIMITS.totalChars) throw new Error('INVALID_REQUEST');
    if (texts.has(path.toLowerCase())) throw new Error('INVALID_REQUEST');
    texts.set(path.toLowerCase(), { path, text });
  }
  const language = languageFor(entry);
  if (!language || !texts.has(entry.toLowerCase())) throw new Error('INVALID_REQUEST');
  return { entry: texts.get(entry.toLowerCase())!.path, files: texts, stdin, language };
}

/** The class `java` must start: the entry's package plus its file name. */
export function javaMainClass(entry: string, text: string): string {
  const stripped = text.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, ' ');
  const declared = /^[ \t]*package[ \t]+([\w.]+)[ \t]*;/m.exec(stripped)?.[1];
  const name = entry.slice(entry.lastIndexOf('/') + 1).replace(/\.java$/i, '');
  return declared ? `${declared}.${name}` : name;
}

// ── Python (Pyodide) ─────────────────────────────────────────────────────────────────────────
const PYODIDE_FILES: Record<string, string> = { 'pyodide.mjs': 'text/javascript; charset=utf-8', 'pyodide.asm.mjs': 'text/javascript; charset=utf-8',
  'pyodide.asm.wasm': 'application/wasm', 'python_stdlib.zip': 'application/zip', 'pyodide-lock.json': 'application/json' };
const RUNNER_HTML = '<!doctype html><meta charset="utf-8"><title>run</title><script type="module" src="runner.js"></script>';
// Runs in the sandboxed window. Output is collected (capped) and returned when the program ends.
const RUNNER_JS = `import { loadPyodide } from './pyodide/pyodide.mjs';
const LIMIT = ${RUN_LIMITS.output};
const BASE = '/home/pyodide/project';
globalThis.runPython = async ({ entry, files, stdin }) => {
  const out = { stdout: '', stderr: '', truncated: false };
  const decoders = { stdout: new TextDecoder(), stderr: new TextDecoder() };
  const push = (name, bytes) => {
    if (out[name].length >= LIMIT) { out.truncated = true; return bytes.length; }
    out[name] += decoders[name].decode(bytes, { stream: true });
    if (out[name].length > LIMIT) { out[name] = out[name].slice(0, LIMIT); out.truncated = true; }
    return bytes.length;
  };
  const py = await loadPyodide({ indexURL: new URL('./pyodide/', location.href).href, packages: [], checkAPIVersion: true });
  py.setStdout({ write: bytes => push('stdout', bytes), isatty: false });
  py.setStderr({ write: bytes => push('stderr', bytes), isatty: false });
  let given = false;
  py.setStdin({ stdin: () => { if (given) return null; given = true; return stdin; }, isatty: false });
  for (const [path, text] of Object.entries(files)) {
    const full = BASE + '/' + path;
    py.FS.mkdirTree(full.slice(0, full.lastIndexOf('/')));
    py.FS.writeFile(full, text);
  }
  const target = JSON.stringify(BASE + '/' + entry);
  const code = [
    'import sys, os, runpy, traceback',
    '_entry = ' + target,
    'os.chdir(os.path.dirname(_entry))',
    'sys.path[:0] = [os.path.dirname(_entry), ' + JSON.stringify(BASE) + ']',
    'sys.argv = [_entry]',
    '_code = 0',
    'try:',
    '    runpy.run_path(_entry, run_name="__main__")',
    'except SystemExit as error:',
    '    _code = error.code if isinstance(error.code, int) else (0 if error.code is None else 1)',
    '    if not isinstance(error.code, (int, type(None))): print(error.code, file=sys.stderr)',
    'except BaseException:',
    '    traceback.print_exc()',
    '    _code = 1',
    'sys.stdout.flush(); sys.stderr.flush()',
    '_code',
  ].join('\\n');
  const exitCode = py.runPython(code);
  out.stdout += decoders.stdout.decode(); out.stderr += decoders.stderr.decode();
  out.stderr = out.stderr.split(BASE + '/').join('');
  return { ...out, exitCode: typeof exitCode === 'number' ? exitCode : 0 };
};
`;

class PythonSandbox {
  private files: Map<string, Uint8Array<ArrayBuffer>> | null = null;
  constructor(private readonly directory: string, private readonly show = false) {}

  version(): string { return 'Python 3.14 (Pyodide, 앱 안 격리 실행)'; }

  async run(request: Request, onWindow: (window: BrowserWindow) => void): Promise<RunResult> {
    const started = Date.now();
    const id = `r${randomBytes(16).toString('hex')}`;
    const own = session.fromPartition(`gitviz-run-${id}`); // memory only
    const origin = `${RUN_SCHEME}://${id}/`;
    own.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    own.setPermissionCheckHandler(() => false);
    own.on('will-download', event => event.preventDefault());
    // Only this window's own runner and Pyodide files: no network of any kind.
    own.webRequest.onBeforeRequest((details, callback) => callback({ cancel: !details.url.startsWith(origin) && !details.url.startsWith('devtools:') }));
    const files = await this.load();
    own.protocol.handle(RUN_SCHEME, request => {
      let url: URL;
      try { url = new URL(request.url); } catch { return new Response(null, { status: 400 }); }
      if (url.host !== id || request.method !== 'GET') return new Response(null, { status: 404 });
      const headers = (type: string) => ({ 'Content-Type': type, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'; worker-src 'self' blob:; base-uri 'none'; form-action 'none'" });
      if (url.pathname === '/runner.html') return new Response(RUNNER_HTML, { headers: headers('text/html; charset=utf-8') });
      if (url.pathname === '/runner.js') return new Response(RUNNER_JS, { headers: headers('text/javascript; charset=utf-8') });
      const name = url.pathname.replace(/^\/pyodide\//, '');
      const bytes = url.pathname.startsWith('/pyodide/') ? files.get(name) : undefined;
      return bytes ? new Response(bytes, { headers: headers(PYODIDE_FILES[name]) }) : new Response(null, { status: 404 });
    });
    const window = new BrowserWindow({ show: this.show, width: 400, height: 300, webPreferences: { session: own, sandbox: true, contextIsolation: true,
      nodeIntegration: false, webSecurity: true, webviewTag: false, devTools: false, spellcheck: false, backgroundThrottling: false } });
    window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    window.webContents.on('will-navigate', event => event.preventDefault());
    onWindow(window);
    let timer: NodeJS.Timeout | undefined;
    try {
      await window.loadURL(`${origin}runner.html`);
      const payload = { entry: request.entry, stdin: request.stdin, files: Object.fromEntries([...request.files.values()].map(file => [file.path, file.text])) };
      const timeout = new Promise<'timeout'>(resolve => { timer = setTimeout(() => resolve('timeout'), RUN_LIMITS.pythonSeconds * 1000); });
      const result = await Promise.race([window.webContents.executeJavaScript(`runPython(${JSON.stringify(payload)})`) as Promise<{ stdout: string; stderr: string; truncated: boolean; exitCode: number }>, timeout]);
      if (result === 'timeout') return { language: 'python', phase: 'run', exitCode: null, timedOut: true, truncated: false, stdout: '', stderr: '', durationMs: Date.now() - started, tool: this.version() };
      return { language: 'python', phase: 'run', exitCode: result.exitCode, timedOut: false, truncated: result.truncated,
        stdout: String(result.stdout).slice(0, RUN_LIMITS.output), stderr: String(result.stderr).slice(0, RUN_LIMITS.output), durationMs: Date.now() - started, tool: this.version() };
    } catch (error) {
      if (window.isDestroyed()) throw new Error('STOPPED', { cause: error });
      throw new Error('RUN_UNAVAILABLE', { cause: error });
    } finally {
      clearTimeout(timer);
      if (!window.isDestroyed()) { try { window.webContents.forcefullyCrashRenderer(); } catch { /* gone */ } window.destroy(); }
      try { own.protocol.unhandle(RUN_SCHEME); } catch { /* gone */ }
      void own.clearStorageData().catch(() => {});
    }
  }

  private async load(): Promise<Map<string, Uint8Array<ArrayBuffer>>> {
    if (this.files) return this.files;
    const files = new Map<string, Uint8Array<ArrayBuffer>>();
    try { for (const name of Object.keys(PYODIDE_FILES)) files.set(name, new Uint8Array(await readFile(join(this.directory, name)))); }
    catch (error) { throw new Error('RUN_UNAVAILABLE', { cause: error }); }
    this.files = files;
    return files;
  }
}

// ── C/C++/Java (local compilers) ────────────────────────────────────────────────────────────
// PowerShell puts itself in a new Job Object (kill on close, per-process memory, process count) and starts the program,
// which inherits the job and this pipe's stdin/stdout/stderr. Killing PowerShell closes the job and ends everything in it.
const quotePs = (value: string) => `'${value.replace(/'/g, "''")}'`;
export function jobScript(exe: string, args: string, cwd: string, memory: number, processes: number): string {
  return `$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class GitVizJob {
  [StructLayout(LayoutKind.Sequential)] struct Basic { public long A; public long B; public uint Flags; public UIntPtr MinWs; public UIntPtr MaxWs; public uint Processes; public UIntPtr Affinity; public uint Priority; public uint Scheduling; }
  [StructLayout(LayoutKind.Sequential)] struct Io { public ulong A, B, C, D, E, F; }
  [StructLayout(LayoutKind.Sequential)] struct Extended { public Basic Basic; public Io Io; public UIntPtr ProcessMemory; public UIntPtr JobMemory; public UIntPtr PeakProcess; public UIntPtr PeakJob; }
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr CreateJobObject(IntPtr attributes, string name);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool SetInformationJobObject(IntPtr job, int type, ref Extended info, uint size);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
  [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
  static IntPtr job;
  public static void Enter(long memory, uint processes) {
    job = CreateJobObject(IntPtr.Zero, null);
    if (job == IntPtr.Zero) throw new Exception("job");
    var info = new Extended();
    info.Basic.Flags = 0x2000 | 0x100 | 0x8 | 0x400; // kill on close, process memory, active processes, die on unhandled exception
    info.Basic.Processes = processes;
    info.ProcessMemory = (UIntPtr)(ulong)memory;
    if (!SetInformationJobObject(job, 9, ref info, (uint)Marshal.SizeOf(typeof(Extended)))) throw new Exception("limits");
    if (!AssignProcessToJobObject(job, GetCurrentProcess())) throw new Exception("assign");
  }
}
"@
[GitVizJob]::Enter(${memory}, ${processes})
} catch { [Console]::Error.WriteLine('GITVIZ_JOB_UNAVAILABLE'); exit 197 }
$start = New-Object System.Diagnostics.ProcessStartInfo
$start.FileName = ${quotePs(exe)}
$start.Arguments = ${quotePs(args)}
$start.WorkingDirectory = ${quotePs(cwd)}
$start.UseShellExecute = $false
$process = [System.Diagnostics.Process]::Start($start)
$process.WaitForExit()
exit $process.ExitCode
`;
}

/** One Windows command-line argument (CommandLineToArgvW rules). */
export function quoteArg(value: string): string {
  if (value && !/[\s"]/.test(value)) return value;
  return `"${value.replace(/(\\*)"/g, '$1$1\\"').replace(/(\\+)$/, '$1$1')}"`;
}

type Tools = { c: { cc: string; label: string } | null; cpp: { cxx: string; label: string } | null; java: { javac: string; java: string; label: string } | null };
type Spawned = { exitCode: number | null; timedOut: boolean; truncated: boolean; stdout: string; stderr: string };

export class CollabRunner {
  private tools: Promise<Tools> | null = null;
  private active: { stop: () => void } | null = null;
  private readonly python: PythonSandbox;

  constructor(pyodideDirectory: string, private readonly env: NodeJS.ProcessEnv = process.env, showPython = false) {
    this.python = new PythonSandbox(pyodideDirectory, showPython);
  }

  async runtimes(value?: unknown): Promise<Runtimes> {
    if (value !== undefined && (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(name => name !== 'refresh'))) throw new Error('INVALID_REQUEST');
    if ((value as { refresh?: unknown } | undefined)?.refresh === true) this.tools = null;
    const tools = await (this.tools ??= this.findTools());
    return { python: this.python.version(), c: tools.c?.label ?? null, cpp: tools.cpp?.label ?? null, java: tools.java?.label ?? null };
  }

  async run(value: unknown): Promise<RunResult> {
    const request = readRequest(value);
    if (this.active) throw new Error('BUSY');
    let stopped = false;
    let result: RunResult;
    try {
      if (request.language === 'python') {
        let window: BrowserWindow | null = null;
        this.active = { stop: () => { stopped = true; if (window && !window.isDestroyed()) { try { window.webContents.forcefullyCrashRenderer(); } catch { /* gone */ } window.destroy(); } } };
        result = await this.python.run(request, opened => { window = opened; if (stopped) this.active?.stop(); });
      } else result = await this.native(request, stop => { this.active = { stop: () => { stopped = true; stop(); } }; });
    } catch (error) {
      if (stopped) throw new Error('STOPPED', { cause: error });
      throw error;
    } finally { this.active = null; }
    log('info', 'collab-run', { language: result.language, phase: result.phase, exitCode: result.exitCode, timedOut: result.timedOut, durationMs: result.durationMs, stopped });
    if (stopped) throw new Error('STOPPED');
    return result;
  }

  stop(): { stopped: boolean } {
    const active = this.active;
    active?.stop();
    return { stopped: !!active };
  }

  private childEnv(directory: string): NodeJS.ProcessEnv {
    // Only what compilers and the JDK need: no tokens, keys or project settings from the app's environment.
    const env: NodeJS.ProcessEnv = { TEMP: directory, TMP: directory };
    for (const name of ['PATH', 'Path', 'SystemRoot', 'SYSTEMROOT', 'windir', 'ComSpec', 'PATHEXT', 'SystemDrive', 'JAVA_HOME', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE'])
      if (this.env[name] !== undefined) env[name] = this.env[name];
    return env;
  }

  private async where(name: string): Promise<string | null> {
    const found = await this.spawnCapture(join(this.env.SystemRoot ?? 'C:\\Windows', 'System32', 'where.exe'), [name], tmpdir(), 5);
    const first = found.exitCode === 0 ? found.stdout.split(/\r?\n/).map(line => line.trim()).find(line => /\.exe$/i.test(line)) : undefined;
    return first ?? null;
  }

  private async label(exe: string, args: string[], fallback: string): Promise<string> {
    const output = await this.spawnCapture(exe, args, tmpdir(), 10);
    const line = `${output.stdout}\n${output.stderr}`.split(/\r?\n/).map(text => text.trim()).find(Boolean);
    return (line ?? fallback).slice(0, 120);
  }

  private async findTools(): Promise<Tools> {
    const [gcc, clang, gxx, clangxx, javac, java] = await Promise.all(['gcc', 'clang', 'g++', 'clang++', 'javac', 'java'].map(name => this.where(name)));
    const cc = gcc ?? clang, cxx = gxx ?? clangxx;
    return {
      c: cc ? { cc, label: await this.label(cc, ['--version'], 'C 컴파일러') } : null,
      cpp: cxx ? { cxx, label: await this.label(cxx, ['--version'], 'C++ 컴파일러') } : null,
      java: javac && java ? { javac, java, label: await this.label(javac, ['-version'], 'JDK') } : null,
    };
  }

  private async native(request: Request, onStop: (stop: () => void) => void): Promise<RunResult> {
    const language = request.language as Exclude<RunLanguage, 'python'>;
    const tools = await (this.tools ??= this.findTools());
    const tool = tools[language];
    if (!tool) throw new Error('NO_COMPILER');
    const started = Date.now();
    const directory = await mkdtemp(join(tmpdir(), 'gitviz-run-'));
    let cancel = () => {};
    let cancelled = false;
    onStop(() => { cancelled = true; cancel(); });
    try {
      for (const file of request.files.values()) {
        const target = join(directory, ...file.path.split('/'));
        await mkdir(dirname(target), { recursive: true });
        await writeFile(target, file.text, 'utf8');
      }
      const sources = [...request.files.values()].map(file => file.path).filter(path => SOURCES[language].test(path));
      const label = 'label' in tool ? tool.label : '';
      let compile: Spawned;
      let program: string; let programArgs: string[]; let memory = RUN_LIMITS.memory;
      if (language === 'java') {
        const java = tool as NonNullable<Tools['java']>;
        compile = await this.spawnCapture(java.javac, ['-encoding', 'UTF-8', '-d', '.gitviz-classes', ...sources], directory, RUN_LIMITS.compileSeconds, '', stop => { cancel = stop; });
        program = java.java;
        programArgs = ['-Xmx256m', '-Dfile.encoding=UTF-8', '-Dstdout.encoding=UTF-8', '-Dstderr.encoding=UTF-8', '-cp', '.gitviz-classes',
          javaMainClass(request.entry, request.files.get(request.entry.toLowerCase())!.text)];
        memory = RUN_LIMITS.javaMemory;
      } else {
        const compiler = language === 'c' ? (tool as NonNullable<Tools['c']>).cc : (tool as NonNullable<Tools['cpp']>).cxx;
        const standard = language === 'c' ? '-std=c17' : '-std=c++17';
        compile = await this.spawnCapture(compiler, [standard, '-O1', '-Wall', '-o', 'gitviz-program.exe', ...sources], directory, RUN_LIMITS.compileSeconds, '', stop => { cancel = stop; });
        program = join(directory, 'gitviz-program.exe');
        programArgs = [];
      }
      if (cancelled) throw new Error('STOPPED');
      if (compile.exitCode !== 0 || compile.timedOut) {
        return { language, phase: 'compile', exitCode: compile.exitCode, timedOut: compile.timedOut, truncated: compile.truncated,
          stdout: compile.stdout, stderr: compile.stderr.split(`${directory}\\`).join(''), durationMs: Date.now() - started, tool: label };
      }
      const powershell = join(this.env.SystemRoot ?? 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
      const script = jobScript(program, programArgs.map(quoteArg).join(' '), directory, memory, RUN_LIMITS.processes);
      const run = await this.spawnCapture(powershell, ['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')],
        directory, RUN_LIMITS.runSeconds + 5, request.stdin, stop => { cancel = stop; });
      if (cancelled) throw new Error('STOPPED');
      if (run.exitCode === 197 && run.stderr.includes('GITVIZ_JOB_UNAVAILABLE')) throw new Error('SANDBOX_UNAVAILABLE');
      return { language, phase: 'run', exitCode: run.exitCode, timedOut: run.timedOut, truncated: run.truncated, stdout: run.stdout, stderr: run.stderr,
        durationMs: Date.now() - started, tool: label };
    } finally {
      await rm(directory, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }).catch(() => {});
    }
  }

  /** Runs one process with a time limit and capped output; the whole process tree is ended on timeout, overflow or stop. */
  private spawnCapture(exe: string, args: string[], cwd: string, seconds: number, stdin = '', onStart?: (stop: () => void) => void): Promise<Spawned> {
    return new Promise(resolve => {
      let child: ChildProcess;
      try { child = spawn(exe, args, { cwd, env: this.childEnv(cwd), windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] }); }
      catch { resolve({ exitCode: null, timedOut: false, truncated: false, stdout: '', stderr: '' }); return; }
      const out = { stdout: '', stderr: '', truncated: false, timedOut: false };
      const decoders = { stdout: new TextDecoder(), stderr: new TextDecoder() };
      const end = () => {
        if (child.exitCode !== null || child.pid === undefined) return;
        // taskkill /T ends the tree; closing PowerShell also closes its job, which ends the program.
        spawn(join(this.env.SystemRoot ?? 'C:\\Windows', 'System32', 'taskkill.exe'), ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' }).on('error', () => {});
        child.kill();
      };
      const take = (name: 'stdout' | 'stderr') => (chunk: Buffer) => {
        if (out[name].length >= RUN_LIMITS.output) { out.truncated = true; end(); return; }
        out[name] += decoders[name].decode(chunk, { stream: true });
        if (out[name].length > RUN_LIMITS.output) { out[name] = out[name].slice(0, RUN_LIMITS.output); out.truncated = true; end(); }
      };
      child.stdout!.on('data', take('stdout'));
      child.stderr!.on('data', take('stderr'));
      child.stdin!.on('error', () => {});
      child.stdin!.end(stdin);
      const timer = setTimeout(() => { out.timedOut = true; end(); }, seconds * 1000);
      onStart?.(end);
      child.once('error', () => { clearTimeout(timer); resolve({ exitCode: null, ...out }); });
      child.once('close', code => { clearTimeout(timer); resolve({ exitCode: code, ...out, stdout: out.stdout + decoders.stdout.decode(), stderr: out.stderr + decoders.stderr.decode() }); });
    });
  }
}
