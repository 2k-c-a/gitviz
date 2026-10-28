import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { CloudReview } from '../src/cloud-review';
import type { Operation } from '../contracts/workspace';

const KEY = 'fixture-only-key-never-crosses-stdio';
function fixture() {
  let generation = 1, deny = false, mismatch = false, reflected = false, escaped = false, sent = 0;
  const calls: { operation: Operation; payload: Record<string, unknown> }[] = [];
  const wire = Buffer.from('{"approved":"fixture"}'), hash = createHash('sha256').update(wire).digest('hex');
  let reference: unknown;
  let finished: () => void = () => {};
  const done = new Promise<void>(resolve => { finished = resolve; });
  const agent = { async call(operation: Operation, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    calls.push({ operation, payload }); assert.equal(JSON.stringify(payload).includes(KEY), false, 'agent never receives BYOK');
    if (operation === 'workspace.reviews.cloudContext') { reference = payload.credentialRef; return { projectId: 'p', accepted: true }; }
    if (operation === 'workspace.reviews.preview') return { projectId: 'p', previewId: 'preview', requestHash: hash, requestBytes: wire.length };
    if (operation === 'workspace.reviews.cloudExchange' && payload.phase === 'take') return { projectId: 'p', ready: true, credentialRef: reference,
      profileId: 'gpt-5.4-v1', provider: 'openai', destination: 'https://api.openai.com', requestBytes: wire.length, requestHash: mismatch ? 'forged' : hash, bodyBase64: wire.toString('base64') };
    if (operation === 'workspace.reviews.cloudExchange' && payload.phase === 'complete') { finished(); return { projectId: 'p', ready: false }; }
    return { projectId: 'p', runId: 'run', status: 'running' };
  } };
  const mapping = { async publicProof() { if (deny) throw new Error('CLOUD_MAPPING_REQUIRED'); return { visibility: 'public', accountId: '11', configHash: 'a', expiresAt: Date.now() / 1000 + 30 }; } };
  const cloud = new CloudReview(agent, mapping, () => generation, async (_profile, bytes, key) => {
    assert.equal(key, KEY); assert.deepEqual(bytes, wire); sent++;
    const raw = escaped ? '{"secret":"' + [...KEY].map(char => '\\u' + char.charCodeAt(0).toString(16).padStart(4, '0')).join('') + '"}' : reflected ? JSON.stringify({ secret: KEY }) : '{}';
    return { httpStatus: 200, rawBase64: Buffer.from(raw).toString('base64'), errorCode: null, uncertain: false };
  });
  return { cloud, calls, done, result: () => ({runId:'run',status:'running',credentialRef:reference}), sent: () => sent, deny: () => { deny = true; }, changeAccount: () => { generation++; }, mismatch: () => { mismatch = true; }, reflect: (encode = false) => { reflected = true; escaped = encode; } };
}
test('BYOK stays in main, exact approved bytes send once, durable replay does not resend', async () => {
  const h = fixture();
  try {
    await h.cloud.preview('p', { profileId: 'gpt-5.4-v1' }, KEY);
    const start = () => h.cloud.start({ projectId: 'p', previewId: 'preview' }, async () => h.result());
    await start(); await h.done; await start(); assert.equal(h.sent(), 1);
    assert.equal(h.calls.some(call => JSON.stringify(call.payload).includes(KEY)), false);
  } finally { h.cloud.dispose(); }
});
test('account or public-proof change after preview blocks before starting a provider request', async () => {
  for (const mode of ['account', 'private']) {
    const h = fixture(); let started = false;
    try {
      await h.cloud.preview('p', { profileId: 'gpt-5.4-v1' }, KEY);
      if (mode === 'account') h.changeAccount(); else h.deny();
      await assert.rejects(h.cloud.start({ projectId: 'p', previewId: 'preview' }, async () => { started = true; return {}; }));
      assert.equal(started, false); assert.equal(h.sent(), 0);
    } finally { h.cloud.dispose(); }
  }
});
test('forged request hash cannot send and upstream reflected API key cannot cross stdio', async () => {
  for (const mode of ['hash', 'reflection', 'escaped-reflection']) {
    const h = fixture();
    try {
      await h.cloud.preview('p', { profileId: 'gpt-5.4-v1' }, KEY);
      if (mode === 'hash') h.mismatch(); else h.reflect(mode === 'escaped-reflection');
      await h.cloud.start({ projectId: 'p', previewId: 'preview' }, async () => h.result());
      await h.done;
      assert.equal(h.sent(), mode === 'hash' ? 0 : 1);
      const complete = h.calls.find(call => call.payload.phase === 'complete')!;
      assert.equal(complete.payload.rawBase64, ''); assert.equal(complete.payload.errorCode, 'PROVIDER_ERROR');
    } finally { h.cloud.dispose(); }
  }
});
