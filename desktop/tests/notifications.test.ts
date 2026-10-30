import assert from 'node:assert/strict';
import test from 'node:test';
import { EventRelay, type EventBatch, type EventTimer, type Signal } from '../src/notifications';

function harness(batches: EventBatch[], live = () => true) {
  const sent: Signal[] = [], reads: number[] = [];
  let pending: (() => void) | null = null;
  const timer: EventTimer = { set: action => { pending = action; return 1; }, clear: () => { pending = null; } };
  const relay = new EventRelay(async after => { reads.push(after); const batch = batches.shift(); if (!batch) throw new Error('AGENT_DOWN'); return batch; },
    signal => sent.push(signal), live, timer, 10);
  return { relay, sent, reads, tick: async () => { const action = pending; pending = null; action?.(); await new Promise(resolve => setImmediate(resolve)); }, scheduled: () => pending !== null };
}
const item = (sequence: number) => ({ sequence, eventId: `e${sequence}`, projectId: 'p', type: 'validation.failed', resourceId: 'r', sourceRevision: 1 });
const batch = (events: number[], lastSequence: number, extra: Partial<EventBatch> = {}): EventBatch => ({ events: events.map(item), lastSequence, reset: false, hasMore: false, ...extra });

test('subscribe from 0 starts at the current head and never replays history as signals', async () => {
  const h = harness([batch([], 7), batch([8], 8)]);
  assert.equal(await h.relay.subscribe(0), true);
  assert.equal(h.relay.position, 7);
  await h.tick();
  assert.deepEqual(h.reads, [0, 7]);
  assert.deepEqual(h.sent, [{ kind: 'event', ...item(8) }]);
  assert.equal(Object.keys(h.sent[0]!).sort().join(), 'eventId,kind,projectId,resourceId,sequence,sourceRevision,type');
});

test('gaps, truncated batches and a reset store all ask the renderer to resync from the durable list', async () => {
  const h = harness([batch([3], 3), batch([5], 9, { hasMore: true }), batch([], 2, { reset: true })]);
  await h.relay.subscribe(2);
  await h.tick(); await h.tick(); await h.tick();
  assert.deepEqual(h.sent.map(signal => signal.kind), ['event', 'resync', 'event', 'resync', 'resync']);
  assert.equal(h.relay.position, 2);
});

test('agent failure keeps polling; unsubscribe, closed documents and disposal stop it', async () => {
  let open = true;
  const failing = harness([], () => open);
  await assert.rejects(failing.relay.subscribe(0));  // The head read failed: no subscription is created.
  assert.equal(failing.relay.position, null);
  const h = harness([], () => open);
  assert.equal(await h.relay.subscribe(4), true);
  await h.tick();
  assert.equal(h.scheduled(), true, 'a failed read is retried');
  h.relay.unsubscribe();
  assert.equal(h.scheduled(), false);
  await h.relay.subscribe(4);
  open = false;
  await h.tick();
  assert.equal(h.relay.position, null, 'navigated away from the workspace document');
  assert.equal(h.sent.length, 0);
  open = true;
  await h.relay.subscribe(4);
  h.relay.dispose();
  assert.equal(h.scheduled(), false);
  assert.equal(await h.relay.subscribe(4), false);
});
