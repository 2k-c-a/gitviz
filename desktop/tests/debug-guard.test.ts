import assert from 'node:assert/strict';
import test from 'node:test';
import { debugSwitches, forbiddenDebugSwitches } from '../src/debug-guard';

const exe = 'C:\\Users\\tester\\AppData\\Local\\GitVizWorkspace\\app-0.7.0\\GitVizWorkspace.exe';

test('a packaged release refuses every spelling of the remote debugging switches', () => {
  for (const argument of [
    '--remote-debugging-port=9222', '--remote-debugging-port=0', '--remote-debugging-port', '-remote-debugging-port=9222', '/remote-debugging-port=9222',
    '--REMOTE-DEBUGGING-PORT=9222', '--remote-debugging-pipe', '--remote-debugging-address=0.0.0.0', '--remote-debugging-socket-name=x', ' --remote-debugging-port=1 ',
    '--inspect', '--inspect=9229', '--inspect-brk=9229', '--inspect-port=9229', '--debug-port=5858',
  ]) assert.equal(forbiddenDebugSwitches(true, [exe, argument]).length, 1, argument);
  assert.deepEqual(forbiddenDebugSwitches(true, [exe, '--user-data-dir=C:\\x', '--remote-debugging-port=9222']), ['--remote-debugging-port']);
});

test('a development run keeps its debugging switches', () => {
  assert.deepEqual(forbiddenDebugSwitches(false, [exe, '--remote-debugging-port=9222', '--inspect']), []);
});

test('other launch arguments, including other security-related ones, are left alone', () => {
  const others = ['--user-data-dir=C:\\x', '--no-sandbox', '--disable-web-security', '--force-device-scale-factor=2', '--squirrel-uninstall', '--enable-logging', '--inspector', 'remote-debugging-port', 'C:\\remote-debugging-port=1'];
  assert.deepEqual(debugSwitches(others), []);
  assert.deepEqual(forbiddenDebugSwitches(true, [exe, ...others]), []);
});
