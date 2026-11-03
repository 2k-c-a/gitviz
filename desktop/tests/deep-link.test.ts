import assert from 'node:assert/strict';
import { test } from 'node:test';
import { isAppDocument } from '../src/app-protocol';
import { communityJoinUrl, inviteFromArgv, inviteFromUrl, protocolRegistration } from '../src/deep-link';

const token = 'A'.repeat(20) + 'b_-'.repeat(7) + '9'.repeat(2);

test('only an invite token is read from a gitviz-join link', () => {
  assert.equal(token.length, 43);
  assert.equal(inviteFromUrl(`gitviz-join://invite/${token}`), token);
  assert.equal(inviteFromUrl(`gitviz-join://invite/${token}/`), token);
  for (const value of [
    `gitviz-join://invite/${token}x`, `gitviz-join://invite/${token.slice(1)}`, `gitviz-join://open/${token}`,
    `gitviz-join://invite/${token}?next=/x`, `gitviz-join://invite/${token}#x`, `gitviz-join://user@invite/${token}`,
    `gitviz-join://invite:1/${token}`, `gitviz-join://invite/x/${token}`, `gitviz://app/web/pages/community.html`,
    `https://gitviz.example/pages/community.html#invite=${token}`, '', null, 42,
  ]) assert.equal(inviteFromUrl(value), null, String(value));
});

test('the link is found among Chromium arguments, and nothing else is', () => {
  assert.equal(inviteFromArgv(['C:\\app\\GitVizWorkspace.exe', '--', `gitviz-join://invite/${token}`]), token);
  assert.equal(inviteFromArgv(['C:\\app\\GitVizWorkspace.exe', `GITVIZ-JOIN://invite/${token}`]), token);
  assert.equal(inviteFromArgv(['C:\\app\\GitVizWorkspace.exe', '--allow-file-access', `gitviz-join://invite/bad`]), null);
  assert.equal(inviteFromArgv(['C:\\app\\GitVizWorkspace.exe']), null);
});

test('the app protocol serves the AI analysis page reached from the overview button', () => {
  assert.equal(isAppDocument('gitviz://app/web/pages/ai-insights.html?repo=2&snapshot=5'), true);
  assert.equal(isAppDocument('gitviz://app/web/pages/ai-insights-x.html'), false);
});

test('the link opens only the community page, which the app protocol accepts', () => {
  const url = communityJoinUrl(token);
  assert.equal(url, `gitviz://app/web/pages/community.html?invite=${token}`);
  assert.equal(isAppDocument(url), true);
  assert.throws(() => communityJoinUrl('../x'), /INVALID_INVITE/);
});

test('only a packaged Squirrel install registers, pointing at the stable launcher with -- before the link', () => {
  assert.deepEqual(protocolRegistration(true, 'win32', 'C:\\Users\\u\\AppData\\Local\\GitVizWorkspace\\app-0.7.0\\GitVizWorkspace.exe'),
    { scheme: 'gitviz-join', path: 'C:\\Users\\u\\AppData\\Local\\GitVizWorkspace\\GitVizWorkspace.exe', args: ['--'] });
  assert.equal(protocolRegistration(false, 'win32', 'C:\\Users\\u\\AppData\\Local\\GitVizWorkspace\\app-0.7.0\\GitVizWorkspace.exe'), null);
  assert.equal(protocolRegistration(true, 'linux', '/opt/app-0.7.0/GitVizWorkspace'), null);
  // A portable or development copy outside the per-user install does not take over the scheme.
  assert.equal(protocolRegistration(true, 'win32', 'C:\\build-output\\desktop\\GitVizWorkspace-win32-x64\\GitVizWorkspace.exe'), null);
});
