import { execFileSync, spawn } from 'node:child_process';
import { appendFileSync, existsSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join, win32 } from 'node:path';

/** Squirrel.Windows runs the installed exe with --squirrel-* once per lifecycle event; the app must answer and exit. */
export interface CleanupPlan { root: string; script: string }
export type SquirrelAction = { updateExe: string; args: string[]; cleanup?: CleanupPlan } | { updateExe: null; args: [] };
export interface ParentProcess { pid: number; name: string }
export interface SquirrelEnvironment { localAppData?: string; exists?: (path: string) => boolean; parent?: () => ParentProcess | null }

const INSTALL_DIRECTORY = 'GitVizWorkspace';
export const CLEANUP_LOG = 'GitVizWorkspace-uninstall-cleanup.log';
export const CLEANUP_SCRIPT = 'GitVizWorkspace-uninstall-cleanup.ps1';
export const CLEANUP_TASK = 'GitVizWorkspaceUninstallCleanup';
// Letters, digits, space and a few path characters only. Anything else (quotes, ';', '&', '$', '`', '%', ...) means no cleanup, never an escaped one.
const SAFE_ROOT = /^[A-Za-z]:\\[\p{L}\p{N} _.()~\-\\]+$/u;
const UPDATER = /^(?:update|squirrel)\.exe$/i;

const normalized = (path: string): string => win32.normalize(path).replace(/\\+$/, '').toLowerCase();

/** Image name of a process, from tasklist (no WMI, which a restricted session may not be allowed to query). */
export function parentProcess(pid: number): ParentProcess | null {
  try {
    const tasklist = win32.join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'tasklist.exe');
    const output = execFileSync(tasklist, ['/FI', `PID eq ${pid}`, '/FO', 'CSV', '/NH'], { encoding: 'utf8', windowsHide: true, timeout: 5000 });
    const match = /^"([^"]+)","(\d+)"/m.exec(output);
    return match && Number(match[2]) === pid ? { pid, name: match[1] } : null;
  } catch { return null; }
}

/**
 * Squirrel's Update.exe cannot delete itself or files its uninstall hook still has open, so the install directory is left behind.
 * Plan the removal of exactly %LOCALAPPDATA%\GitVizWorkspace, derived from the running exe; user data lives elsewhere and is never touched.
 * Only when this process was started by Squirrel's updater: launching the exe by hand with the same argument must not remove an installation.
 */
export function cleanupPlan(execPath: string, environment: SquirrelEnvironment): CleanupPlan | null {
  const { localAppData, exists = existsSync } = environment;
  if (!localAppData || !win32.isAbsolute(execPath)) return null;
  const versionDirectory = win32.dirname(execPath);
  const root = win32.dirname(versionDirectory);
  if (!/^app-\d+\.\d+\.\d+/.test(win32.basename(versionDirectory))) return null;
  if (win32.basename(root) !== INSTALL_DIRECTORY || normalized(win32.dirname(root)) !== normalized(localAppData)) return null;
  if (!SAFE_ROOT.test(root) || !exists(win32.join(root, 'Update.exe'))) return null;
  const parent = environment.parent?.();
  if (!parent || !Number.isSafeInteger(parent.pid) || parent.pid <= 4 || !UPDATER.test(parent.name)) return null;
  const literal = root.replaceAll("'", "''");
  const script = [
    // First thing, before anything can fail: proof that the helper process ran at all.
    `$log = Join-Path $env:TEMP '${CLEANUP_LOG}'`,
    "function Log($text) { Add-Content -LiteralPath $log -Value ((Get-Date).ToString('s') + ' helper: ' + $text) -Encoding ASCII }",
    "Log 'process started'",
    'try {',
    `  $root = '${literal}'`,
    `  $parentId = ${parent.pid}`,
    "  $prefix = $root + '\\'",
    '  function Inside($path) { $path -and $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) }',
    '  Log "waiting for the uninstaller (pid $parentId)"',
    '  # The uninstaller is our parent; it cannot be removed while it runs, and it removes what it can when it finishes.',
    '  Wait-Process -Id $parentId -Timeout 120 -ErrorAction SilentlyContinue',
    "  Log 'uninstaller finished'",
    '  # Chromium helper processes can outlive the main process for a moment; wait until nothing from the install directory is running.',
    '  $deadline = (Get-Date).AddSeconds(120); $busy = @()',
    '  while ((Get-Date) -lt $deadline) {',
    '    $busy = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { Inside $_.Path })',
    '    if ($busy.Count -eq 0) { break }',
    '    Start-Sleep -Milliseconds 500',
    '  }',
    "  Log ('still running from the install directory: ' + (@($busy | ForEach-Object { $_.ProcessName }) -join ','))",
    '  for ($i = 0; $i -lt 10 -and (Test-Path -LiteralPath $root); $i++) {',
    '    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue',
    "    if (Test-Path -LiteralPath $root) { Log ('attempt ' + ($i + 1) + ': ' + @(Get-ChildItem -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue).Count + ' items remain'); Start-Sleep -Seconds 1 }",
    '  }',
    "  Log ('finished; install directory exists=' + (Test-Path -LiteralPath $root))",
    "} catch { Log ('error: ' + $_) }",
    // One-shot: remove the scheduled task and this script whatever happened above.
    `schtasks.exe /Delete /TN ${CLEANUP_TASK} /F | Out-Null`,
    'Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue',
  ].join('\r\n');
  return { root, script };
}

/** Fixed arguments only: nothing from argv beyond the event name is forwarded. */
export function squirrelAction(platform: string, argv: readonly string[], execPath: string, environment: SquirrelEnvironment = {}): SquirrelAction | null {
  const event = argv[1];
  if (platform !== 'win32' || typeof event !== 'string' || !event.startsWith('--squirrel-')) return null;
  const updateExe = join(dirname(dirname(execPath)), 'Update.exe');
  const exe = basename(execPath);
  if (event === '--squirrel-install' || event === '--squirrel-updated') return { updateExe, args: ['--createShortcut', exe] };
  if (event === '--squirrel-uninstall') {
    const cleanup = cleanupPlan(execPath, environment);
    return { updateExe, args: ['--removeShortcut', exe], ...(cleanup ? { cleanup } : {}) };
  }
  return { updateExe: null, args: [] };
}

const defaultLog = (line: string): void => {
  try { appendFileSync(join(tmpdir(), CLEANUP_LOG), `${new Date().toISOString().slice(0, 19)} app: ${line}\r\n`, 'ascii'); } catch { /* diagnostics only */ }
};

/**
 * Squirrel's Update.exe waits for every process the uninstall hook starts, so any helper we start ourselves (detached, `cmd start`, ...)
 * is waited for by the very process it is waiting for. A one-shot scheduled task is started by the Task Scheduler service instead, outside that tree.
 */
function scheduleCleanup(cleanup: CleanupPlan, launch: typeof spawn, log: (line: string) => void, write: (path: string, text: string) => void, next: () => void): void {
  const systemRoot = process.env.SystemRoot ?? 'C:\\Windows';
  const scriptPath = win32.join(tmpdir(), CLEANUP_SCRIPT);
  const powershell = win32.join(systemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
  const command = `"${powershell}" -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "${scriptPath}"`;
  // schtasks accepts at most 261 characters in /TR; a path with anything unusual is not scheduled rather than escaped.
  if (!SAFE_ROOT.test(scriptPath) || command.length > 260) { log(`cleanup not scheduled: unsuitable script path (${scriptPath.length} characters)`); next(); return; }
  try { write(scriptPath, `\uFEFF${cleanup.script}`); } catch (error) { log(`cleanup script could not be written: ${error instanceof Error ? error.message : String(error)}`); next(); return; }
  const schtasks = win32.join(systemRoot, 'System32', 'schtasks.exe');
  const run = (args: string[], then: (code: number | null) => void): void => {
    let ended = false;
    const end = (code: number | null): void => { if (ended) return; ended = true; log(`schtasks ${args[0]} exit ${code}`); then(code); };
    try {
      const child = launch(schtasks, args, { stdio: 'ignore', windowsHide: true, cwd: systemRoot });
      child.once('error', error => { log(`schtasks ${args[0]} failed to start: ${error.message}`); end(null); });
      child.once('close', end);
    } catch (error) { log(`schtasks ${args[0]} could not be launched: ${error instanceof Error ? error.message : String(error)}`); end(null); }
  };
  run(['/Create', '/TN', CLEANUP_TASK, '/TR', command, '/SC', 'ONCE', '/ST', '00:00', '/F'], code => { if (code === 0) run(['/Run', '/TN', CLEANUP_TASK], () => next()); else next(); });
}

export function runSquirrel(action: SquirrelAction, done: () => void, launch: typeof spawn = spawn, log: (line: string) => void = defaultLog, write: (path: string, text: string) => void = (path, text) => writeFileSync(path, text, 'utf8')): void {
  if (action.updateExe === null) { done(); return; }
  const uninstall = action.args[0] === '--removeShortcut';
  const cleanup = 'cleanup' in action ? action.cleanup : undefined;
  if (uninstall) log(`uninstall hook; cleanup ${cleanup ? 'planned' : 'not planned (not started by the updater or not the per-user install)'}`);
  let finished = false;
  const finish = (): void => {
    if (finished) return;
    finished = true;
    if (cleanup) scheduleCleanup(cleanup, launch, log, write, done); else done();
  };
  const child = launch(action.updateExe, action.args, { detached: true, stdio: 'ignore', windowsHide: true });
  child.once('error', finish); child.once('close', finish);
}
