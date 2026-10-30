import assert from 'node:assert/strict';
import test from 'node:test';
import { ReviewConsent } from '../src/projects';

const preview = { projectId: 'project', previewId: 'preview', previewHash: 'a', manifestHash: 'b', maxEstimatedCost: '0',
  destination: 'http://127.0.0.1:11434', executionLocation: 'user-pc', kind: 'review', files: [{ path: 'src/example.py' }],
  requestBytes: 4338, requestHash: 'c'.repeat(64), expiresAt: 300 };
const input = { projectId: 'project', previewId: 'preview', previewHash: 'a', manifestHash: 'b', maxEstimatedCost: '0',
  acknowledged: true, duplicateRiskAcknowledged: false, idempotencyKey: 'key' };

test('review start requires matching unexpired agent preview before any native prompt', async () => {
  let now = 0, prompts = 0, starts = 0;
  const consent = new ReviewConsent(() => false, () => now);
  const confirm = async () => { prompts++; return true; };
  const forward = async () => { starts++; return input; };
  await assert.rejects(consent.start(input, confirm, forward), /APPROVAL_REQUIRED/);
  consent.remember(preview);
  for (const forged of [{ previewId: 'other' }, { projectId: 'other' }, { previewHash: 'forged' },
    { manifestHash: 'forged' }, { maxEstimatedCost: '1' }]) {
    await assert.rejects(consent.start({ ...input, ...forged }, confirm, forward), /APPROVAL_REQUIRED/);
  }
  now = 300001;
  await assert.rejects(consent.start(input, confirm, forward), /APPROVAL_REQUIRED/);
  assert.equal(prompts, 0); assert.equal(starts, 0);
});

test('native cancellation sends nothing; approval forwards unchanged payload and binds idempotency body', async () => {
  const consent = new ReviewConsent(() => false, () => 0);
  consent.remember(preview);
  let starts = 0, prompts = 0;
  const forward = async () => { starts++; return input; };
  assert.equal(await consent.start(input, async detail => {
    prompts++;
    for (const required of [preview.destination, '4338', 'src/example.py', 'cccccccccccccccc', '회수할 수 없습니다']) assert.ok(detail.includes(required));
    return false;
  }, forward), null);
  assert.equal(starts, 0);
  const confirm = async () => { prompts++; return true; };
  assert.deepEqual(await consent.start(input, confirm, forward), input);
  assert.deepEqual(await consent.start({ ...input }, confirm, forward), input);
  assert.equal(prompts, 2); assert.equal(starts, 2);
  await assert.rejects(consent.start({ ...input, duplicateRiskAcknowledged: true }, confirm, forward), /CONFLICT/);
});

test('only one native confirmation is active and closing/disposal prevents late transfer', async () => {
  let closed = false, starts = 0;
  const consent = new ReviewConsent(() => closed, () => 0);
  consent.remember(preview);
  let release!: (value: boolean) => void;
  const pending = consent.start(input, () => new Promise<boolean>(resolve => { release = resolve; }), async () => { starts++; });
  await assert.rejects(consent.start({ ...input, idempotencyKey: 'other' }, async () => true, async () => {}), /BUSY/);
  closed = true; release(true);
  await assert.rejects(pending, /ACCESS_DENIED/);
  assert.equal(starts, 0);
  consent.dispose(); closed = false;
  await assert.rejects(consent.start(input, async () => true, async () => {}), /ACCESS_DENIED/);
});

test('preview cache bounds, immutable paths, expiry during prompt and refactor consent', async () => {
  let now = 0;
  const consent = new ReviewConsent(() => false, () => now);
  for (let i = 0; i < 32; i++) consent.remember({ ...preview, previewId: String(i) });
  assert.throws(() => consent.remember(preview), /BUSY/);
  now = 300001;
  const mutable = { ...preview, expiresAt: 900, kind: 'refactor', files: [{ path: 'trusted.ts' }] };
  consent.remember(mutable); mutable.files[0]!.path = 'forged.ts';
  await assert.rejects(consent.start(input, async detail => {
    assert.ok(detail.includes('refactor')); assert.ok(detail.includes('trusted.ts')); assert.ok(!detail.includes('forged.ts'));
    now += 300001; return true;
  }, async () => assert.fail('expired consent must not transfer')), /APPROVAL_REQUIRED/);
});
