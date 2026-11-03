import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, readdir, readFile, rm, utimes, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { AppLog, errorCode, formatLine } from '../src/app-log';

const TIME = new Date('2026-11-03T01:02:03.000Z');

test('only code-like error values pass; free text never does', () => {
  assert.equal(errorCode(new Error('STORAGE_UNAVAILABLE')), 'STORAGE_UNAVAILABLE');
  assert.equal(errorCode(Object.assign(new Error('open C:\\Users\\someone\\x failed'), { code: 'ENOENT' })), 'ENOENT');
  assert.equal(errorCode(new TypeError('token ghp_secret is invalid')), 'TypeError');
  assert.equal(errorCode(new Error('Bearer abc.def')), 'Error');
  for (const value of ['STORAGE_UNAVAILABLE', null, undefined, { message: 'X' }, 42]) assert.equal(errorCode(value), 'ERROR');
});

test('lines carry the event and redact any field value that is not a short code', () => {
  assert.equal(formatLine(TIME, 'app.start', { version: '0.7.0', packaged: true, exitCode: 3 }), '2026-11-03T01:02:03.000Z app.start version=0.7.0 packaged=true exitCode=3\n');
  const line = formatLine(TIME, 'agent.failed', { code: 'https://server.example/x?token=1', path: 'C:\\Users\\someone', mail: 'a@b.example', skip: undefined, health: null });
  assert.equal(line, '2026-11-03T01:02:03.000Z agent.failed code=[redacted] path=[redacted] mail=[redacted] health=null\n');
  assert.equal(formatLine(TIME, 'git.check', { 'bad key': 'x', found: false }), '2026-11-03T01:02:03.000Z git.check found=false\n');
  assert.ok(!formatLine(TIME, 'app.quit', { code: 'a\nb' }).slice(0, -1).includes('\n'));
});

test('the log rotates by size and keeps a bounded number of files', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'gitviz-log-'));
  try {
    const log = new AppLog(join(directory, 'logs'), { maxBytes: 200, files: 3, now: () => TIME });
    for (let index = 0; index < 40; index += 1) log.write('agent.ready', { state: 'ready', index });
    const names = (await readdir(join(directory, 'logs'))).sort();
    assert.deepEqual(names, ['app.1.log', 'app.2.log', 'app.log']);
    for (const name of names) assert.ok((await readFile(join(directory, 'logs', name))).length <= 200, name);
    assert.match(await readFile(log.path, 'utf8'), /index=39\n$/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('old and surplus rotated files are pruned at start; the current file and unrelated files stay', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'gitviz-log-'));
  try {
    for (const name of ['app.log', 'app.1.log', 'app.2.log', 'app.9.log', 'other.txt']) await writeFile(join(directory, name), 'x\n');
    const old = new Date(TIME.getTime() - 15 * 86_400_000);
    await utimes(join(directory, 'app.2.log'), old, old);
    await utimes(join(directory, 'app.log'), old, old);
    new AppLog(directory, { files: 5, maxAgeDays: 14, now: () => TIME });
    assert.deepEqual((await readdir(directory)).sort(), ['app.1.log', 'app.log', 'other.txt']);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('an unusable directory never throws', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'gitviz-log-'));
  try {
    const file = join(directory, 'not-a-directory');
    await writeFile(file, 'x');
    const log = new AppLog(join(file, 'logs'));
    assert.doesNotThrow(() => log.write('app.start'));
  } finally { await rm(directory, { recursive: true, force: true }); }
});
