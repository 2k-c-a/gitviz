import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { CloudReview } from '../src/cloud-review';
import type { Operation } from '../contracts/workspace';

const KEY = 'fixture-only-key-never-crosses-stdio';
function fixture() {
  let mismatch = false, reflected = false, escaped = false, sent = 0;
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
  const cloud = new CloudReview(agent, async (_profile, bytes, key) => {
    assert.equal(key, KEY); assert.deepEqual(bytes, wire); sent++;
    const raw = escaped ? '{"secret":"' + [...KEY].map(char => '\\u' + char.charCodeAt(0).toString(16).padStart(4, '0')).join('') + '"}' : reflected ? JSON.stringify({ secret: KEY }) : '{}';
    return { httpStatus: 200, rawBase64: Buffer.from(raw).toString('base64'), errorCode: null, uncertain: false };
  });
  return { cloud, calls, done, result: () => ({runId:'run',status:'running',credentialRef:reference}), sent: () => sent, mismatch: () => { mismatch = true; }, reflect: (encode = false) => { reflected = true; escaped = encode; } };
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
test('local key authorization needs no sign-in or public proof', async () => {
  const h = fixture();
  try {
    await h.cloud.preview('p', { profileId: 'gpt-5.4-v1' }, KEY);
    const authorize = h.calls.find(call => call.operation === 'workspace.reviews.cloudContext')!;
    assert.equal(authorize.payload.phase, 'authorize-local'); assert.equal('proof' in authorize.payload, false);
    await assert.rejects(h.cloud.preview('p', { profileId: 'gpt-5.4-v1' }, 'short'), /INVALID_REQUEST/);
    await assert.rejects(h.cloud.preview('p', { profileId: 'ollama-local-v1' }, KEY), /INVALID_REQUEST/);
  } finally { h.cloud.dispose(); }
});
test('a different project or a closed window blocks before starting a provider request', async () => {
  for (const mode of ['project', 'disposed']) {
    const h = fixture(); let started = false;
    try {
      await h.cloud.preview('p', { profileId: 'gpt-5.4-v1' }, KEY);
      if (mode === 'disposed') h.cloud.dispose();
      await assert.rejects(h.cloud.start({ projectId: mode === 'project' ? 'other' : 'p', previewId: 'preview' }, async () => { started = true; return {}; }));
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
