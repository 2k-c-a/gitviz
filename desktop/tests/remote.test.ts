import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { RemoteContracts, endpoints } from '../src/remote-contracts';
import { Remote, httpsClient, type Http } from '../src/remote';
import { DesktopAuth } from '../src/auth';

const bodies = JSON.parse(readFileSync(join(__dirname, '../schema/ai-bodies.json'), 'utf8')) as Record<string, Record<string, unknown>>;
const contracts = new RemoteContracts(bodies);
const uid = '11111111-1111-4111-8111-111111111111';
const token = 't'.repeat(64);
const issued = () => ({ sessionToken: token, sessionId: uid, user: { id: uid, github_login: 'tester', display_name: null }, expiresAt: new Date(Date.now() + 300000).toISOString(), capabilities: ['remote-v1'], apiCapabilities: { desktopProtocol: 1, aiContract: '1.9' } });

test('remote route allowlist rejects URL/header/auth proxies and validates existing body schemas', () => {
  const input = { endpointKey: 'timeline', path: { repositoryId: 8 }, query: { months: 36, snapshot_id: 12 } };
  assert.deepEqual(contracts.route(input), { method: 'GET', path: '/repositories/8/analytics/timeline?months=36&snapshot_id=12' });
  for (const invalid of [{ ...input, url: 'https://evil.test' }, { ...input, headers: { Authorization: 'forged' } }, { ...input, endpointKey: 'logout' },
    { ...input, path: { repositoryId: '../1' } }, { ...input, query: { unknown: 'a' } }, { ...input, body: {} }]) assert.throws(() => contracts.route(invalid));
  const feedback = { endpointKey: 'feedback', path: { findingId: uid }, query: {}, body: { verdict: 'accepted', note: null, expected_version: null } };
  // Assert the exported schema is used, including nested refs; invalid enum and extra fields fail.
  assert.throws(() => contracts.route({ ...feedback, body: { verdict: 'invented', note: null, expected_version: 1 } }));
  assert.throws(() => contracts.route({ ...feedback, body: { verdict: 'accepted', secret: 'x' } }));
  for (const definition of Object.values(endpoints)) assert.ok(!definition.template.includes('/auth/') || definition.template === '/api/auth/me');
});

test('HTTPS transport omits cookies, follows no redirect, bounds output and returns safe headers only', async () => {
  let captured: RequestInit | undefined;
  const http = httpsClient('https://server.test', async (_url, options) => {
    captured = options;
    return new Response('{"ok":true}', { status: 200, headers: { 'content-type': 'application/json', 'set-cookie': 'secret', 'authorization': 'secret', 'retry-after': '2' } });
  });
  const result = await http('/repositories', 'GET', undefined, token);
  assert.equal(captured?.redirect, 'error'); assert.equal(captured?.credentials, 'omit');
  assert.deepEqual(result.headers, { 'retry-after': '2' });
  assert.equal(new Headers(captured?.headers).get('Cookie'), null);
  assert.throws(() => httpsClient('http://server.test'));
  await assert.rejects(http('//evil.test', 'GET'));
  await assert.rejects(httpsClient('https://server.test', async () => new Response('<html>no</html>'))('/x', 'GET'));
  await assert.rejects(httpsClient('https://server.test', async () => new Response('x'.repeat(8 * 1024 * 1024 + 1), { headers: { 'content-type': 'application/json' } }))('/x', 'GET'));
});

test('account generation drops a late response, 401 invalidates, mutations never retry automatically', async () => {
  let generation = 1, calls = 0, release: (() => void) | undefined;
  const http: Http = async () => { calls++; await new Promise<void>(resolve => { release = resolve; }); return { status: 200, body: {}, headers: {} }; };
  const remote = new Remote(contracts, http, () => ({ token, generation }), () => { generation++; });
  const pending = remote.request({ endpointKey: 'mine', path: {}, query: {} });
  generation++; release!(); await assert.rejects(pending, /ACCOUNT_CHANGED/); assert.equal(calls, 1);
  const unauthorized = new Remote(contracts, async () => ({ status: 401, body: {}, headers: {} }), () => ({ token, generation }), () => { generation++; });
  const before = generation; assert.equal((await unauthorized.request({ endpointKey: 'mine', path: {}, query: {} })).status, 401); assert.equal(generation, before + 1);
});

test('auth holds secrets in main, lost exchange response reuses key, logout revokes only desktop session', async () => {
  const realNow = Date.now; let clock = realNow(); Date.now = () => clock;
  let exchanged = false; const exchangeKeys: (string | undefined)[] = []; let saved: unknown = null;
  const urls: string[] = [];
  const http: Http = async (path, method, body, _token, key) => {
    if (path.endsWith('transactions')) {
      const request = JSON.parse(body!) as Record<string, unknown>; assert.equal(typeof request.challenge, 'string');
      assert.equal('clientSecret' in request, false);
      return { status: 201, headers: {}, body: { flowId: uid, pollSecret: 'p'.repeat(43), displayCode: 'AABBCCDD', browserUrl: `https://server.test/api/desktop/v1/auth/authorize?flow=${uid}`, expiresAt: new Date(clock + 300000).toISOString(), pollAfterMs: 2000 } };
    }
    if (path.endsWith('exchange')) { exchangeKeys.push(key); if (!exchanged) { exchanged = true; throw new Error('NETWORK_UNAVAILABLE'); } return { status: 200, body: issued(), headers: {} }; }
    assert.equal(path, '/api/desktop/v1/session'); assert.equal(method, 'DELETE'); return { status: 204, body: null, headers: {} };
  };
  const auth = new DesktopAuth('https://server.test', http, async url => { urls.push(url); }, { read: async () => null, write: async value => { saved = value; } });
  try {
    const started = await auth.start(); assert.equal(started.state, 'pending'); assert.equal('pollSecret' in started, false); assert.equal('verifier' in started, false);
    clock += 2001; await assert.rejects(auth.status(), /NETWORK_UNAVAILABLE/);
    clock += 2001; const complete = await auth.status(); assert.equal(complete.state, 'authenticated'); assert.equal('sessionToken' in complete, false);
    assert.equal(exchangeKeys[0], exchangeKeys[1]); assert.ok(saved); assert.equal(urls.length, 1);
    assert.equal((await auth.logout()).state, 'signed-out'); assert.equal(saved, null); assert.equal(auth.identity(), null);
  } finally { Date.now = realNow; }
});

test('offline logout quarantines encrypted session across restart and prevents account restore', async () => {
  let stored: (ReturnType<typeof issued> & { pendingRevocation?: boolean }) | null = issued();
  let offline = true; const methods: string[] = [];
  const http: Http = async (_path, method) => {
    methods.push(method);
    if (method === 'DELETE' && offline) throw new Error('NETWORK_UNAVAILABLE');
    return { status: method === 'DELETE' ? 204 : 200, body: issued(), headers: {} };
  };
  const vault = { read: async () => stored, write: async (value: typeof stored) => { stored = value; } };
  const auth = new DesktopAuth('https://server.test', http, async () => {}, vault);
  await auth.restore(); await assert.rejects(auth.logout(), /NETWORK_UNAVAILABLE/);
  assert.equal(auth.identity(), null); assert.equal(stored?.pendingRevocation, true);
  const restarted = new DesktopAuth('https://server.test', http, async () => {}, vault);
  await assert.rejects(restarted.restore(), /NETWORK_UNAVAILABLE/);
  assert.equal(restarted.identity(), null); await assert.rejects(restarted.start(), /REVOCATION_REQUIRED/);
  offline = false; await restarted.logout(); assert.equal(stored, null);
  assert.deepEqual(methods, ['GET', 'DELETE', 'DELETE', 'DELETE']);
});
