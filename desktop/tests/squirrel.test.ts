import assert from 'node:assert/strict';
import test from 'node:test';
import { squirrelAction } from '../src/squirrel';

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
