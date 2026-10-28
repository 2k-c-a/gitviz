import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import type { request } from 'node:https';
import test from 'node:test';
import { createCloudHttp } from '../src/cloud-http';

test('cloud HTTPS uses fixed host/path/TLS and main-only header; redirects never trigger a second request', async () => {
  let calls = 0;
  const key = 'fixture-key-stays-in-header-only';
  const wire = Buffer.from('{"approved":"bytes"}');
  const transport = ((options: Record<string, unknown>, response: (value: EventEmitter & { statusCode: number }) => void) => {
    calls++;
    assert.equal(options.hostname, 'api.openai.com'); assert.equal(options.path, '/v1/responses'); assert.equal(options.method, 'POST');
    assert.equal(options.port, 443); assert.equal(options.rejectUnauthorized, true); assert.equal(options.agent, false);
    assert.equal((options.headers as Record<string, string>).Authorization, `Bearer ${key}`);
    const child = new EventEmitter() as EventEmitter & { end: (body: Buffer) => void; destroy: () => void };
    child.destroy = () => {};
    child.end = bytes => { assert.deepEqual(bytes, wire); queueMicrotask(() => {
      const upstream = Object.assign(new EventEmitter(), { statusCode: 302 }); response(upstream);
      upstream.emit('data', Buffer.from('{"redirect":"https://evil.invalid"}')); upstream.emit('end');
    }); };
    return child;
  }) as unknown as typeof request;
  const result = await createCloudHttp(transport)('gpt-5.4-v1', wire, key, new AbortController().signal);
  assert.equal(result.httpStatus, 302); assert.equal(calls, 1);
});
