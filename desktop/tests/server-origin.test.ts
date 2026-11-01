import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readEmbeddedOrigin, serverOrigin, validOrigin } from '../src/server-origin';

test('only bare https origins are accepted', () => {
  assert.equal(validOrigin('https://gitviz.example'), 'https://gitviz.example');
  assert.equal(validOrigin('https://localhost:8443'), 'https://localhost:8443');
  for (const value of ['http://gitviz.example', 'https://gitviz.example/', 'https://gitviz.example/api', 'https://user:pw@gitviz.example', 'https://gitviz.example?x=1', 'gitviz.example', '', null, 42]) assert.equal(validOrigin(value), null, String(value));
});
test('embedded origin wins in a package; environment overrides only in development or when nothing is embedded', () => {
  const embedded = 'https://gitviz.example', other = 'https://attacker.example';
  assert.equal(serverOrigin(true, embedded, other), embedded);
  assert.equal(serverOrigin(true, embedded, undefined), embedded);
  assert.equal(serverOrigin(false, embedded, other), other);
  assert.equal(serverOrigin(false, embedded, 'http://bad'), embedded);
  assert.equal(serverOrigin(true, null, 'https://localhost:8443'), 'https://localhost:8443');
  assert.equal(serverOrigin(true, null, 'not a url'), null);
  assert.equal(serverOrigin(true, null, undefined), null);
});
test('embedded config file is read defensively', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'gitviz-origin-'));
  try {
    const file = join(directory, 'server.json');
    assert.equal(readEmbeddedOrigin(file), null);
    await writeFile(file, JSON.stringify({ origin: 'https://gitviz.example' })); assert.equal(readEmbeddedOrigin(file), 'https://gitviz.example');
    await writeFile(file, JSON.stringify({ origin: null })); assert.equal(readEmbeddedOrigin(file), null);
    await writeFile(file, JSON.stringify({ origin: 'http://gitviz.example' })); assert.equal(readEmbeddedOrigin(file), null);
    await writeFile(file, 'corrupt'); assert.equal(readEmbeddedOrigin(file), null);
  } finally { await rm(directory, { recursive: true }); }
});
