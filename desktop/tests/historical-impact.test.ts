import assert from 'node:assert/strict';
import test from 'node:test';
import { HistoricalImpact } from '../src/historical-impact';
import type { Operation } from '../contracts/workspace';

function fixture() {
  let head = 'a'.repeat(40), accountId = '11', generation = 1, deny = false, corrupt = false, switchDuringRead = false;
  const calls: unknown[] = [];
  let binding: Record<string, unknown> | null = null;
  const value: Record<string, unknown> = { projectId: 'p', analysisId: 'a', historical: { status: 'unavailable', reason: 'authenticated-remote-mapping-required' } };
  const proof = () => ({ accountId, serverOrigin: 'https://fixture.invalid', repositoryId: 2, githubId: 123, canonicalUrl: 'https://github.com/o/r', connectionId: 'c', configHash: 'b'.repeat(64), visibility: 'private', expiresAt: Date.now() / 1000 + 30 });
  const metadata = () => ({ id: 7, target_sha: head, payload_sha256: 'c'.repeat(64), formula_version: 'fixture-v1', window_start: null, window_end: '2026-10-28T00:00:00Z' });
  const agent = { async call(operation: Operation, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    assert.equal(operation, 'workspace.impact.historical');
    if (payload.phase === 'inspect') { if (switchDuringRead) generation++; return { head: 'a'.repeat(40), paths: ['local/selected.py'], binding }; }
    binding = Object.fromEntries(['accountId', 'serverOrigin', 'repositoryId', 'githubId', 'canonicalUrl', 'connectionId', 'configHash'].map(key => [key, proof()[key as keyof ReturnType<typeof proof>]]));
    return { ...value, historical: payload.historical };
  } };
  const mapping = { generation: () => generation, async readProof() { if (deny) throw new Error('REMOTE_ACCESS_DENIED'); return proof(); } };
  const remote = { async request(request: unknown) {
    calls.push(request); assert.equal(JSON.stringify(request).includes('local/selected.py'), false, 'local paths never leave main');
    const endpoint = (request as { endpointKey: string }).endpointKey;
    return { status: 200, headers: {}, body: endpoint === 'snapshotMeta' ? { snapshot: metadata() } : {
      _snapshot: { ...metadata(), payload_sha256: (corrupt ? 'd' : 'c').repeat(64) },
      meta: { co_change_unit: 'pr', total_units: 10, excluded_unit_count: 2, total_pairs: 1 }, pairs: [{ file_a_path: 'local/selected.py', file_b_path: 'related.py',
        co_change_count: 3, change_count_a: 5, change_count_b: 6, a_to_b_ratio: .6, b_to_a_ratio: .5, coupling_score: .6, low_support: true }] } };
  } };
  return { service: new HistoricalImpact(agent, mapping, remote), value, calls, mismatch: () => { head = 'e'.repeat(40); }, deny: () => { deny = true; }, corrupt: () => { corrupt = true; }, changeAccount: () => { accountId = '12'; generation++; }, switchDuringRead: () => { switchDuringRead = true; } };
}
test('historical cochange pins snapshot/hash/head and never sends local paths or promotes history to static references', async () => {
  const h = fixture(), result = await h.service.decorate(h.value), history = result.historical as Record<string, unknown>;
  assert.equal(history.status, 'available'); assert.equal(history.serverSnapshotId, 7); assert.equal(history.reason, 'historical-cochange-not-a-dependency');
  assert.equal((history.pairs as unknown[]).length, 1); assert.equal(h.calls.length, 2);
  h.changeAccount(); assert.equal((await h.service.decorate(result).then(v => v.historical) as Record<string, unknown>).status, 'unavailable');
});
test('account change while reading cached evidence drops it before returning to renderer', async () => {
  const h = fixture(), saved = await h.service.decorate(h.value);
  h.switchDuringRead();
  assert.equal((await h.service.decorate(saved).then(v => v.historical) as Record<string, unknown>).status, 'unavailable');
});
test('head mismatch, tampered snapshot metadata or missing permission remain unavailable', async () => {
  for (const mode of ['head', 'hash', 'permission']) {
    const h = fixture();
    if (mode === 'head') h.mismatch(); else if (mode === 'hash') h.corrupt(); else h.deny();
    const result = await h.service.decorate(h.value);
    assert.equal((result.historical as Record<string, unknown>).status, 'unavailable');
    assert.equal('pairs' in (result.historical as object), false);
  }
});
