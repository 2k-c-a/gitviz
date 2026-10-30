import assert from 'node:assert/strict';
import test from 'node:test';
import { diagnostics } from '../src/diagnostics';
import type { Health } from '../contracts/workspace';

test('diagnostic export projects fixed metadata and excludes raw errors, credentials, paths and extra properties', () => {
  const health = { state: 'ready', components: { store: 'ready', secrets: 'ready', lifecycle: 'ready', raw: 'private-source' },
    errorCode: 'C:\\secret\\token', account: 'private-account', token: 'private-token', path: 'C:\\secret' } as unknown as Health;
  const output = diagnostics(health, '0.7.0', new Date('2026-10-28T00:00:00Z'));
  assert.equal(/private-|C:\\|errorCode/.test(output), false);
  assert.equal(JSON.parse(output).supportedLocalSchema, 8);
  assert.deepEqual(Object.keys(JSON.parse(output).health.components).sort(), ['lifecycle', 'secrets', 'store']);
  assert.throws(() => diagnostics(health, 'https://secret.invalid'));
});
