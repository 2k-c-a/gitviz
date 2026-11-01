import assert from 'node:assert/strict';
import { test } from 'node:test';
import { CommunityLive, EventParser, streamClient, type LiveEvent, type StreamResponse } from '../src/community-live';

const encode = (text: string) => new TextEncoder().encode(text);

function feed(): { response: StreamResponse; write: (text: string) => void; end: () => void; cancelled: () => boolean } {
  let control!: ReadableStreamDefaultController<Uint8Array>; let cancelled = false;
  const body = new ReadableStream<Uint8Array>({ start: c => { control = c; }, cancel: () => { cancelled = true; } });
  return { response: { status: 200, contentType: 'text/event-stream; charset=utf-8', body }, write: text => control.enqueue(encode(text)),
    end: () => control.close(), cancelled: () => cancelled };
}

async function until(check: () => boolean, ms = 2000): Promise<void> {
  const end = Date.now() + ms;
  while (!check()) { if (Date.now() > end) throw new Error('timed out'); await new Promise(resolve => setTimeout(resolve, 5)); }
}

test('the parser keeps only known events with an object payload, across chunk boundaries', () => {
  const parser = new EventParser();
  assert.deepEqual(parser.push(encode('retry: 3000\n\n: ping\n\nevent: mess')), []);
  const bytes = encode('age\ndata: {"id":1,"body":"안녕"}\n\n');
  // A multi-byte character split between reads still decodes.
  assert.deepEqual(parser.push(bytes.slice(0, 30)), []);
  assert.deepEqual(parser.push(bytes.slice(30)), [{ type: 'message', data: { id: 1, body: '안녕' } }]);
  assert.deepEqual(parser.push(encode('event: script\ndata: {}\n\nevent: deleted\ndata: [1]\n\nevent: deleted\ndata: nope\n\n')), []);
  assert.deepEqual(parser.push(encode('event: presence\r\ndata: {"online":\r\ndata: []}\r\n\r\n')), [{ type: 'presence', data: { online: [] } }]);
  assert.throws(() => new EventParser().push(encode('data: ' + 'x'.repeat(70 * 1024))), /OUTPUT_LIMIT/);
});

test('the stream client sends only the bearer token to the chat stream path', async () => {
  const seen: { url: string; init: RequestInit }[] = [];
  const fake = (async (url: string, init: RequestInit) => {
    seen.push({ url, init });
    return new Response('', { status: 200, headers: { 'content-type': 'text/event-stream' } });
  }) as unknown as typeof fetch;
  const http = streamClient('https://gitviz.example', fake);
  const response = await http('/api/community/v1/repositories/7/chat/stream', 'tok', new AbortController().signal);
  assert.equal(response.contentType, 'text/event-stream');
  assert.equal(seen[0].url, 'https://gitviz.example/api/community/v1/repositories/7/chat/stream');
  assert.deepEqual(seen[0].init.headers, { Accept: 'text/event-stream', Authorization: 'Bearer tok' });
  assert.equal(seen[0].init.credentials, 'omit'); assert.equal(seen[0].init.redirect, 'error');
  for (const path of ['/api/community/v1/repositories/0/chat/stream', '/api/community/v1/repositories/7/chat/messages', '//evil/x'])
    await assert.rejects(http(path, 'tok', new AbortController().signal), /INVALID_REQUEST/, path);
  assert.throws(() => streamClient('http://gitviz.example', fake), /INVALID_SERVER/);
});

test('live events reach the window, a dropped stream reconnects, and removal ends it for good', async () => {
  const events: LiveEvent[] = []; const feeds: ReturnType<typeof feed>[] = []; const paths: string[] = [];
  const live = new CommunityLive(async (path, token) => {
    paths.push(`${path} ${token}`); const next = feed(); feeds.push(next); return next.response;
  }, () => ({ token: 't', generation: 1 }), () => assert.fail('no sign-out'), event => events.push(event), { minDelay: 5, maxDelay: 20 });
  assert.throws(() => live.subscribe({ repositoryId: 0 }), /INVALID_REQUEST/);
  assert.throws(() => live.subscribe({ repositoryId: 3, extra: 1 }), /INVALID_REQUEST/);
  assert.deepEqual(live.subscribe({ repositoryId: 3 }), { subscribed: true });
  await until(() => feeds.length === 1);
  feeds[0].write('retry: 3000\n\nevent: ready\ndata: {"online":[]}\n\nevent: message\ndata: {"id":5}\n\n');
  await until(() => events.some(e => e.type === 'message'));
  feeds[0].end();
  await until(() => feeds.length === 2);
  assert.deepEqual(paths, ['/api/community/v1/repositories/3/chat/stream t', '/api/community/v1/repositories/3/chat/stream t']);
  feeds[1].write('event: removed\ndata: {}\n\n');
  await until(() => events.some(e => e.type === 'status' && e.data.state === 'stopped'));
  assert.deepEqual(events.map(e => e.type === 'status' ? `status:${String(e.data.state)}` : e.type), [
    'status:connecting', 'status:live', 'ready', 'message', 'status:reconnecting', 'status:connecting', 'removed', 'status:stopped']);
  assert.equal(events.at(-1)?.data.code, 'MEMBER_REMOVED');
  assert.equal(feeds[1].cancelled(), true);
  assert.equal(live.active, 0);
  await new Promise(resolve => setTimeout(resolve, 40));
  assert.equal(feeds.length, 2);
});

test('lost access, sign-out and unsubscribe stop the stream without retrying', async () => {
  let invalidated = 0; let status = 403; let generation = 1; const events: LiveEvent[] = []; let calls = 0;
  const current = feed();
  const live = new CommunityLive(async () => { calls++; return status === 200 ? current.response : { status, contentType: 'application/json', body: null }; },
    () => generation ? { token: 't', generation } : null, () => { invalidated++; }, event => events.push(event), { minDelay: 5 });
  live.subscribe({ repositoryId: 1 });
  await until(() => events.some(e => e.data.state === 'stopped'));
  assert.equal(events.at(-1)?.data.code, 'PERMISSION_DENIED');
  status = 401; events.length = 0;
  live.subscribe({ repositoryId: 1 });
  await until(() => events.some(e => e.data.state === 'stopped'));
  assert.equal(events.at(-1)?.data.code, 'AUTH_REQUIRED'); assert.equal(invalidated, 1);
  // Another account appears between pings: the open stream ends at the next chunk.
  status = 200; events.length = 0;
  live.subscribe({ repositoryId: 1 });
  await until(() => calls === 3);
  current.write('event: ready\ndata: {"online":[]}\n\n');
  await until(() => events.some(e => e.type === 'ready'));
  generation = 2; current.write(': ping\n\n');
  await until(() => events.some(e => e.data.state === 'stopped'));
  assert.equal(current.cancelled(), true);
  generation = 0;
  assert.throws(() => live.subscribe({ repositoryId: 1 }), /AUTH_REQUIRED/);
  // Unsubscribing during a wait sends nothing more.
  generation = 1; status = 500; events.length = 0;
  live.subscribe({ repositoryId: 1 });
  await until(() => events.some(e => e.data.state === 'reconnecting'));
  live.unsubscribe(); const seen = events.length; const before = calls;
  await new Promise(resolve => setTimeout(resolve, 40));
  assert.equal(events.length, seen); assert.equal(calls, before);
});

test('a silent connection counts as dead after the idle limit and reconnects', async () => {
  const feeds: ReturnType<typeof feed>[] = []; const events: LiveEvent[] = [];
  const live = new CommunityLive(async (_path, _token, signal) => {
    const next = feed(); feeds.push(next);
    signal.addEventListener('abort', () => { try { next.end(); } catch { /* already closed */ } });
    return next.response;
  }, () => ({ token: 't', generation: 1 }), () => {}, event => events.push(event), { minDelay: 5, idle: 30 });
  live.subscribe({ repositoryId: 2 });
  await until(() => feeds.length >= 2);
  live.unsubscribe();
  assert.ok(events.some(e => e.data.state === 'reconnecting'));
});
