import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Contracts, decode, FrameReader, MAX_FRAME } from '../src/contracts';

const directory = join(__dirname, '../schema');
const contracts: Contracts = new Contracts(directory);
test('shared Python/TS schema fixtures', () => {
  const fixtures = JSON.parse(readFileSync(join(directory, 'fixtures.json'), 'utf8')) as { name: string; schema: 'request' | 'response' | 'bootstrap'; value: unknown; valid: boolean }[];
  for (const fixture of fixtures) {
    if (fixture.valid) assert.doesNotThrow(() => contracts.validate(fixture.schema, fixture.value), fixture.name);
    else assert.throws(() => contracts.validate(fixture.schema, fixture.value), fixture.name);
  }
});
test('parser rejects duplicate keys, invalid UTF8, overflow, depth, surrogate, oversized frames', () => {
  for (const raw of [Buffer.from('{"a":1,"a":2}'), Buffer.from('{"a":NaN}'), Buffer.from('{"a":1e999}'), Buffer.from([255]), Buffer.from('"\\ud800"'), Buffer.from('['.repeat(17) + '0' + ']'.repeat(17)), Buffer.alloc(MAX_FRAME + 1), Buffer.alloc(0)]) assert.throws(() => decode(raw));
});
test('stream framing handles arbitrary split/coalescing and truncated EOF', () => {
  const reader = new FrameReader();
  assert.deepEqual(reader.push(Buffer.from('{"x":')), []);
  assert.deepEqual(reader.push(Buffer.from('1}\n{"x":2}\n')), [{ x: 1 }, { x: 2 }]);
  reader.finish();
  reader.push(Buffer.from('{'));
  assert.throws(() => reader.finish());
});
test('notification delete result is checked against its own definition', () => {
  // A missing per-operation mapping makes main reject a valid answer and restart the agent.
  const id = '00000000-0000-4000-8000-000000000001';
  assert.doesNotThrow(() => contracts.result('workspace.notifications.delete', { projectId: id, notificationId: id, deleted: true }));
  // main also requires the answer to name the request's project, so projectId is part of the shape.
  assert.throws(() => contracts.result('workspace.notifications.delete', { notificationId: id, deleted: true }));
});
