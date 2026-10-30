import assert from 'node:assert/strict';
import test from 'node:test';
import { RemoteWorkspace } from '../src/remote-workspace';
import type { Operation } from '../contracts/workspace';

function fixture(confirm: (detail: string) => Promise<boolean> = async () => true) {
  let generation = 1, accountId = '11', githubId = 123, configHash = 'a'.repeat(64), status = 'running';
  let connection: Record<string, unknown> | null = null;
  const calls: { operation: Operation; payload: Record<string, unknown> }[] = [];
  const proof = () => ({ accountId, repositoryId: 2, githubId, canonicalUrl: 'https://github.com/o/r', visibility: 'public' });
  const agent = { async call(operation: Operation, payload: Record<string, unknown>) {
    calls.push({ operation, payload });
    if (operation === 'workspace.projects.connectRemote' && payload.phase === 'inspect') return { projectId: 'p', projectVersion: 1, displayName: 'Fixture', canonicalRoot: 'C:\\fixture', configHash,
      remotes: [{ name: 'origin', canonicalUrl: 'https://github.com/o/r' }], connection };
    if (operation === 'workspace.projects.connectRemote') { connection = { ...payload, connectionId: 'c' }; return { projectId: 'p', connectionId: 'c' }; }
    if (operation === 'workspace.remote.getAnalysis') return { projectId: 'p', ...proof(), snapshotId: 9 };
    return { projectId: 'p', analysisId: 'a' };
  } };
  const auth = { identity: () => ({ token: 'main-only', generation }), status: async () => ({ state: 'authenticated' as const,
    user: { id: accountId, github_login: 'fixture', display_name: null } }) };
  const remote = { async request(value: unknown) { const v = value as { endpointKey: string };
    return { status: 200, headers: {}, body: v.endpointKey === 'repositoryIdentity' ? proof() : { job_id: 3, status, snapshot_id: status === 'done' ? 9 : null } }; } };
  const service = new RemoteWorkspace(agent, auth, remote, 'https://staging.example.invalid', confirm);
  return { service, calls, changeAccount: () => { generation++; accountId = '12'; }, drift: () => { githubId++; }, edit: () => { configHash = 'b'.repeat(64); }, finish: () => { status = 'done'; } };
}
test('native cancel sends no connection write; proof is checked again after consent', async () => {
  const cancelled = fixture(async () => false);
  try { assert.equal(await cancelled.service.connect('p', 2), null); assert.equal(cancelled.calls.some(c => c.payload.phase === 'connect'), false); }
  finally { cancelled.service.dispose(); }
  const h = fixture(async detail => { assert.match(detail, /GitHub ID: 123/); h.drift(); return true; });
  try { await assert.rejects(h.service.connect('p', 2), /PRECONDITION_FAILED/); }
  finally { h.service.dispose(); }
});
test('logout/account change during confirmation cannot forward a connection', async () => {
  const h = fixture(async () => { h.changeAccount(); return true; });
  try { await assert.rejects(h.service.connect('p', 2), /ACCOUNT_CHANGED/); assert.equal(h.calls.some(c => c.payload.phase === 'connect'), false); }
  finally { h.service.dispose(); }
});
test('only main binds observation account; config and account changes stop tracking', async () => {
  const h = fixture();
  try {
    await h.service.connect('p', 2); await h.service.poll(); h.finish(); await h.service.poll();
    const observed = h.calls.filter(c => c.operation === 'workspace.remote.observeAnalysis');
    assert.deepEqual(observed.map(c => c.payload.status), ['running', 'done']);
    assert.equal(observed[0]!.payload.accountId, '11');
    assert.equal('generation' in observed[0]!.payload, false); assert.equal('token' in observed[0]!.payload, false);
    h.edit(); await h.service.poll();
    assert.equal(h.calls.filter(c => c.operation === 'workspace.remote.observeAnalysis').length, 2);
  } finally { h.service.dispose(); }
});
test('opening an analysis builds only a fixed app URL after fresh server identity validation', async () => {
  const h = fixture();
  try {
    assert.equal(await h.service.open('p', 'a'), 'gitviz://app/web/pages/timeline.html?repo=2&snapshot=9');
    h.changeAccount(); await h.service.poll();
    assert.equal(h.calls.filter(c => c.operation === 'workspace.remote.observeAnalysis').length, 0);
  } finally { h.service.dispose(); }
});
