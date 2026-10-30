import assert from 'node:assert/strict';
import test from 'node:test';
import { EventEmitter } from 'node:events';
import type { spawn } from 'node:child_process';
import { CLEANUP_SCRIPT, CLEANUP_TASK, cleanupPlan, runSquirrel, squirrelAction, type SquirrelEnvironment } from '../src/squirrel';

const exec = 'C:\\Users\\tester\\AppData\\Local\\GitVizWorkspace\\app-0.7.0\\GitVizWorkspace.exe';
const update = 'C:\\Users\\tester\\AppData\\Local\\GitVizWorkspace\\Update.exe';

test('squirrel lifecycle events map to fixed Update.exe arguments and never forward other argv', () => {
  for (const event of ['--squirrel-install', '--squirrel-updated']) assert.deepEqual(squirrelAction('win32', ['app', event, '0.7.0', '--createShortcut=evil'], exec), { updateExe: update, args: ['--createShortcut', 'GitVizWorkspace.exe'] });
  assert.deepEqual(squirrelAction('win32', ['app', '--squirrel-uninstall'], exec), { updateExe: update, args: ['--removeShortcut', 'GitVizWorkspace.exe'] });
  assert.deepEqual(squirrelAction('win32', ['app', '--squirrel-obsolete', '0.6.0'], exec), { updateExe: null, args: [] });
  assert.deepEqual(squirrelAction('win32', ['app', '--squirrel-firstrun'], exec), { updateExe: null, args: [] });
});

test('normal launches, other platforms and look-alike arguments are not treated as squirrel events', () => {
  assert.equal(squirrelAction('win32', ['app'], exec), null);
  assert.equal(squirrelAction('win32', ['app', '--squirrel'], exec), null);
  assert.equal(squirrelAction('win32', ['app', 'x', '--squirrel-install'], exec), null);
  assert.equal(squirrelAction('linux', ['app', '--squirrel-install'], exec), null);
});

const local = 'C:\\Users\\tester\\AppData\\Local';
const present: SquirrelEnvironment = { localAppData: local, exists: path => path === update, parent: () => ({ pid: 4242, name: 'Update.exe' }) };
const installedAt = (base: string) => `${base}\\GitVizWorkspace\\app-0.7.0\\GitVizWorkspace.exe`;

test('uninstall plans removal of exactly the per-user install directory, and nothing else', () => {
  const plan = cleanupPlan(exec, present);
  assert.ok(plan);
  assert.equal(plan.root, 'C:\\Users\\tester\\AppData\\Local\\GitVizWorkspace');
  assert.equal(plan.script.split('$root = ').length - 1, 1);
  assert.ok(plan.script.includes("$root = 'C:\\Users\\tester\\AppData\\Local\\GitVizWorkspace'"));
  assert.match(plan.script, /\$parentId = 4242\r\n/);
  assert.match(plan.script, /Wait-Process -Id \$parentId/);
  for (const forbidden of [/Roaming/i, /\$env:(?!TEMP\b)/i, /userData/i, /gitviz-workspace/i, /Get-CimInstance|Get-WmiObject|wmic/i]) assert.doesNotMatch(plan.script, forbidden);
  // The only filesystem path the script names is the install directory itself.
  assert.deepEqual([...plan.script.matchAll(/[A-Za-z]:\\[^'"\r\n]*/g)].map(match => match[0]), [plan.root]);
  assert.deepEqual(squirrelAction('win32', ['app', '--squirrel-uninstall', '0.7.0'], exec, present), { updateExe: update, args: ['--removeShortcut', 'GitVizWorkspace.exe'], cleanup: plan });
});

test('only the uninstall event ever carries a cleanup plan', () => {
  for (const event of ['--squirrel-install', '--squirrel-updated', '--squirrel-obsolete', '--squirrel-firstrun']) {
    const action = squirrelAction('win32', ['app', event, '0.7.0'], exec, present);
    assert.ok(action && !('cleanup' in action), event);
  }
});

test('cleanup is skipped, never adapted, for anything that is not the installed per-user layout', () => {
  const always: SquirrelEnvironment = { localAppData: local, exists: () => true, parent: present.parent };
  const parentOf = (parent: () => { pid: number; name: string } | null): SquirrelEnvironment => ({ ...always, parent });
  assert.ok(cleanupPlan(exec, always), 'control: the fixture itself is accepted');
  assert.equal(cleanupPlan(exec, { localAppData: local, exists: () => true }), null, 'no parent lookup');
  assert.equal(cleanupPlan(exec, parentOf(() => null)), null, 'parent unknown');
  assert.equal(cleanupPlan(exec, parentOf(() => ({ pid: 4242, name: 'explorer.exe' }))), null, 'started by hand from the shell');
  assert.equal(cleanupPlan(exec, parentOf(() => ({ pid: 4242, name: 'powershell.exe' }))), null, 'started by a script');
  assert.equal(cleanupPlan(exec, parentOf(() => ({ pid: 4242, name: 'Update.exe.evil' }))), null, 'look-alike name');
  for (const pid of [0, 4, -1, 1.5, Number.NaN]) assert.equal(cleanupPlan(exec, parentOf(() => ({ pid, name: 'Update.exe' }))), null, `pid ${pid}`);
  assert.ok(cleanupPlan(exec, parentOf(() => ({ pid: 4242, name: 'squirrel.exe' }))), 'squirrel.exe is the same updater');
  assert.equal(cleanupPlan(exec, { localAppData: local, exists: () => false }), null, 'Update.exe missing');
  assert.equal(cleanupPlan(exec, { exists: () => true }), null, 'no LOCALAPPDATA');
  assert.equal(cleanupPlan(exec, { localAppData: 'C:\\Users\\other\\AppData\\Local', exists: () => true }), null, 'other profile');
  assert.equal(cleanupPlan('C:\\Program Files\\GitVizWorkspace\\app-0.7.0\\GitVizWorkspace.exe', always), null, 'outside LOCALAPPDATA');
  assert.equal(cleanupPlan('C:\\Users\\tester\\AppData\\Local\\Other\\app-0.7.0\\GitVizWorkspace.exe', always), null, 'other directory name');
  assert.equal(cleanupPlan('C:\\Users\\tester\\AppData\\Local\\GitVizWorkspace\\resources\\GitVizWorkspace.exe', always), null, 'not an app-x.y.z directory');
  assert.equal(cleanupPlan('C:\\Users\\tester\\AppData\\Local\\GitVizWorkspace\\Sub\\app-0.7.0\\GitVizWorkspace.exe', always), null, 'nested');
  assert.equal(cleanupPlan('GitVizWorkspace\\app-0.7.0\\GitVizWorkspace.exe', always), null, 'relative');
  // The development tree and verification packages are never an installation.
  assert.equal(cleanupPlan('C:\\Users\\tester\\gitviz\\desktop\\node_modules\\electron\\dist\\electron.exe', always), null);
});

test('a LOCALAPPDATA with unsafe characters yields no plan instead of an escaped script', () => {
  for (const name of ["t'ester", 'te;ster', 'te&ster', 'te$ster', 'te`ster', 'te%ster', 'te\u2019ster', 'te"ster', 'te\nster']) {
    const base = `C:\\Users\\${name}\\AppData\\Local`;
    assert.equal(cleanupPlan(installedAt(base), { localAppData: base, exists: () => true, parent: present.parent }), null, JSON.stringify(name));
  }
  const korean = 'C:\\Users\\김 사용자\\AppData\\Local';
  assert.equal(cleanupPlan(installedAt(korean), { localAppData: korean, exists: () => true, parent: present.parent })?.root, `${korean}\\GitVizWorkspace`);
});

class FakeChild extends EventEmitter { unrefs = 0; pid = 777; unref() { this.unrefs++; } }
function fakeLaunch() {
  const calls: { file: string; args: string[]; options: Record<string, unknown>; child: FakeChild }[] = [];
  const logged: string[] = [];
  const written: { path: string; text: string }[] = [];
  const launch = ((file: string, args: string[], options: Record<string, unknown>) => { const child = new FakeChild(); calls.push({ file, args, options, child }); return child; }) as unknown as typeof spawn;
  return { calls, launch, logged, written, log: (line: string) => { logged.push(line); }, write: (path: string, text: string) => { written.push({ path, text }); } };
}
const removal = { updateExe: update, args: ['--removeShortcut', 'GitVizWorkspace.exe'] };

test('uninstall removes the shortcut, then schedules a one-shot task outside our process tree, then finishes', () => {
  const plan = cleanupPlan(exec, present)!;
  const { calls, launch, log, write, logged, written } = fakeLaunch(); let done = 0;
  runSquirrel({ ...removal, cleanup: plan }, () => { done++; }, launch, log, write);
  assert.equal(calls.length, 1); assert.equal(calls[0].file, update); assert.equal(done, 0);
  calls[0].child.emit('close', 0); calls[0].child.emit('error', new Error('late'));
  assert.equal(written.length, 1, 'the script is written once');
  assert.ok(written[0].path.toLowerCase().endsWith(`\\${CLEANUP_SCRIPT}`.toLowerCase()));
  assert.equal(written[0].text, `\uFEFF${plan.script}`);
  assert.equal(calls.length, 2); assert.equal(done, 0, 'not finished before the task exists');
  const create = calls[1];
  assert.match(create.file, /System32\\schtasks\.exe$/);
  assert.deepEqual(create.args.slice(0, 3), ['/Create', '/TN', CLEANUP_TASK]);
  assert.deepEqual(create.args.slice(5), ['/SC', 'ONCE', '/ST', '00:00', '/F']);
  const command = create.args[create.args.indexOf('/TR') + 1];
  assert.ok(command.length <= 260, 'schtasks /TR limit');
  assert.ok(command.includes(`-File "${written[0].path}"`) && /powershell\.exe" -NoProfile -NonInteractive -WindowStyle Hidden/.test(command));
  assert.doesNotMatch(command, /EncodedCommand/);
  create.child.emit('close', 0);
  assert.equal(calls.length, 3); assert.deepEqual(calls[2].args, ['/Run', '/TN', CLEANUP_TASK]); assert.equal(done, 0);
  calls[2].child.emit('close', 0);
  assert.equal(done, 1);
  assert.match(logged.join('|'), /cleanup planned.*schtasks \/Create exit 0.*schtasks \/Run exit 0/);
  assert.equal(calls.every(call => call.options.detached !== true || call.file === update), true, 'no detached helper of ours');
});

test('a failing shortcut removal still schedules the cleanup; other events schedule nothing', () => {
  const plan = cleanupPlan(exec, present)!;
  const failing = fakeLaunch(); let done = 0;
  runSquirrel({ ...removal, cleanup: plan }, () => { done++; }, failing.launch, failing.log, failing.write);
  failing.calls[0].child.emit('error', new Error('spawn failed'));
  assert.equal(failing.calls.length, 2); assert.equal(failing.calls[1].args[0], '/Create');
  const install = fakeLaunch(); done = 0;
  runSquirrel({ updateExe: update, args: ['--createShortcut', 'GitVizWorkspace.exe'] }, () => { done++; }, install.launch, install.log, install.write);
  install.calls[0].child.emit('close', 0);
  assert.equal(install.calls.length, 1); assert.equal(install.written.length, 0); assert.equal(done, 1);
  const unknown = fakeLaunch(); done = 0;
  runSquirrel({ updateExe: null, args: [] }, () => { done++; }, unknown.launch, unknown.log, unknown.write);
  assert.equal(unknown.calls.length, 0); assert.equal(done, 1);
});

test('every way the scheduling can fail is logged and still lets the uninstall finish', () => {
  const none = fakeLaunch(); let done = 0;
  runSquirrel(removal, () => { done++; }, none.launch, none.log, none.write);
  none.calls[0].child.emit('close', 0);
  assert.equal(none.calls.length, 1); assert.equal(done, 1);
  assert.match(none.logged.join('|'), /cleanup not planned/);

  const plan = cleanupPlan(exec, present)!;
  const unwritable = fakeLaunch(); done = 0;
  runSquirrel({ ...removal, cleanup: plan }, () => { done++; }, unwritable.launch, unwritable.log, () => { throw new Error('disk full'); });
  unwritable.calls[0].child.emit('close', 0);
  assert.equal(unwritable.calls.length, 1); assert.equal(done, 1);
  assert.match(unwritable.logged.join('|'), /could not be written: disk full/);

  const refused = fakeLaunch(); done = 0;
  runSquirrel({ ...removal, cleanup: plan }, () => { done++; }, refused.launch, refused.log, refused.write);
  refused.calls[0].child.emit('close', 0);
  refused.calls[1].child.emit('close', 1);
  assert.equal(refused.calls.length, 2, 'no /Run when /Create failed'); assert.equal(done, 1);
  assert.match(refused.logged.join('|'), /schtasks \/Create exit 1/);

  const broken = fakeLaunch(); done = 0;
  runSquirrel({ ...removal, cleanup: plan }, () => { done++; }, broken.launch, broken.log, broken.write);
  broken.calls[0].child.emit('close', 0);
  broken.calls[1].child.emit('error', new Error('no schtasks'));
  assert.equal(done, 1); assert.match(broken.logged.join('|'), /failed to start: no schtasks/);

  const throwing = fakeLaunch(); done = 0;
  const updater = new FakeChild();
  const launch = ((file: string) => { if (/schtasks/i.test(file)) throw new Error('blocked'); return updater; }) as unknown as typeof spawn;
  runSquirrel({ ...removal, cleanup: plan }, () => { done++; }, launch, throwing.log, throwing.write);
  updater.emit('close', 0);
  assert.equal(done, 1); assert.match(throwing.logged.join('|'), /could not be launched: blocked/);
});
