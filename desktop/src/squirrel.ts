import { spawn } from 'node:child_process';
import { basename, dirname, join } from 'node:path';

/** Squirrel.Windows runs the installed exe with --squirrel-* once per lifecycle event; the app must answer and exit. */
export type SquirrelAction = { updateExe: string; args: string[] } | { updateExe: null; args: [] };

/** Fixed arguments only: nothing from argv beyond the event name is forwarded. */
export function squirrelAction(platform: string, argv: readonly string[], execPath: string): SquirrelAction | null {
  const event = argv[1];
  if (platform !== 'win32' || typeof event !== 'string' || !event.startsWith('--squirrel-')) return null;
  const updateExe = join(dirname(dirname(execPath)), 'Update.exe');
  const exe = basename(execPath);
  if (event === '--squirrel-install' || event === '--squirrel-updated') return { updateExe, args: ['--createShortcut', exe] };
  if (event === '--squirrel-uninstall') return { updateExe, args: ['--removeShortcut', exe] };
  return { updateExe: null, args: [] };
}

export function runSquirrel(action: SquirrelAction, done: () => void): void {
  if (action.updateExe === null) { done(); return; }
  const child = spawn(action.updateExe, action.args, { detached: true, stdio: 'ignore', windowsHide: true });
  child.once('error', done); child.once('close', done);
}
