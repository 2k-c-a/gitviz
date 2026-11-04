import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { AppLog, errorCode, mask } from '../src/app-log';

test('masks tokens, keys, e-mail addresses and user folders, and cuts long values', () => {
  const home = 'C:\\Users\\alice';
  assert.equal(mask('open C:\\Users\\alice\\project\\a.py failed', home), 'open %USERPROFILE%\\project\\a.py failed');
  assert.equal(mask('D:\\Users\\bob\\x', home), '%USERPROFILE%\\x');
  for (const secret of ['sk-ant-api03-abcdefghijk', 'ghp_abcdefghijklmnop', 'AIzaSyAbcdefghijkl', 'gvz_abcdefghijk'])
    assert.ok(!mask(`value ${secret} end`, home).includes(secret.slice(4)), secret);
  assert.equal(mask('Authorization: Bearer abc.def', home), 'Authorization: Bearer [masked]');
  assert.equal(mask('token=abc123 password: hunter2', home), 'token=[masked] password: [masked]');
  assert.equal(mask(`sha ${'a'.repeat(40)}`, home), 'sha [masked]');
  assert.equal(mask('mail me@example.com', home), 'mail [email]');
  assert.equal(mask('a\r\nb', home), 'a b');
  assert.equal(mask('ab '.repeat(200), home).length, 301);
  assert.equal(errorCode(new Error('FILE_CHANGED')), 'FILE_CHANGED');
  assert.equal(errorCode(new Error('ENOENT: no such file C:\\Users\\alice\\x')), 'Error');
  assert.equal(errorCode(Object.assign(new Error('raw'), { code: 'ENOENT' })), 'ENOENT');
  assert.equal(errorCode('x'), 'UNKNOWN');
});

test('writes one JSON line per event, keeps only simple named fields, and rotates at the size limit', () => {
  const directory = mkdtempSync(join(tmpdir(), 'gitviz-log-'));
  try {
    const logger = new AppLog(join(directory, 'logs'), 400, 3, () => new Date('2026-11-04T00:00:00Z'));
    logger.write('info', 'app-start', { version: '0.8.0', packaged: false, count: 2, 'bad name': 'x', nested: undefined, nan: Number.NaN });
    const [line] = readFileSync(join(directory, 'logs', 'app.log'), 'utf8').trim().split('\n');
    assert.deepEqual(JSON.parse(line), { time: '2026-11-04T00:00:00.000Z', level: 'info', event: 'app-start', version: '0.8.0', packaged: false, count: 2, nan: null });
    for (let i = 0; i < 40; i++) logger.write('warn', 'collab-failed', { channel: 'desktop:collab-bundle-save', code: 'SAVE_FAILED', i });
    assert.deepEqual(readdirSync(join(directory, 'logs')).sort(), ['app.1.log', 'app.2.log', 'app.log']);
    for (const name of readdirSync(join(directory, 'logs'))) assert.ok(readFileSync(join(directory, 'logs', name)).length <= 400, name);
    // A broken target never throws.
    new AppLog(join(directory, 'logs', 'app.log', 'x')).write('error', 'x');
  } finally { rmSync(directory, { recursive: true, force: true }); }
});
