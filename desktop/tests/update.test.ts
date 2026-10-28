import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { checkUpdate, compareVersions, createUpdateService, DISABLED_UPDATE_POLICY, parseManifest, verifyInstaller, type UpdateManifest, type UpdatePolicy } from '../src/update';

const policy: UpdatePolicy = { releasePrefix: 'https://github.com/example/gitviz/releases/download/', publisher: '' };
const manifest = (over: Record<string, unknown> = {}) => ({ version: '0.8.0', assetName: 'GitVizSetup.exe', sha256: 'a'.repeat(64), size: 10,
  url: 'https://github.com/example/gitviz/releases/download/v0.8.0/GitVizSetup.exe', ...over });

test('versions compare numerically and manifest strictness rejects redirects, extra keys and bad hashes', () => {
  assert.equal(compareVersions('0.10.0', '0.9.0'), 1); assert.equal(compareVersions('1.0.0', '1.0.0'), 0);
  assert.ok(parseManifest(manifest(), policy));
  for (const bad of [{ url: 'https://evil.invalid/v0.8.0/GitVizSetup.exe' }, { sha256: 'A'.repeat(64) }, { assetName: '..\\x.exe' }, { size: 0 }, { extra: 1 }, { version: '0.8' }])
    assert.equal(parseManifest(manifest(bad), policy), null);
  assert.equal(parseManifest(manifest(), { ...policy, releasePrefix: 'http://github.com/' }), null);
});

test('update check reports unconfigured, current, available and invalid without any network or install path', () => {
  assert.equal(checkUpdate('0.7.0', manifest(), { ...policy, releasePrefix: '' }).status, 'unconfigured');
  assert.equal(checkUpdate('0.8.0', manifest(), policy).status, 'current');
  assert.equal(checkUpdate('0.7.0', manifest(), policy).status, 'available');
  assert.equal(checkUpdate('0.7.0', { nope: true }, policy).status, 'invalid');
});

test('renderer-facing update service is inert: unconfigured by default and never reports an unchecked release as current', () => {
  assert.deepEqual(createUpdateService('0.7.0', DISABLED_UPDATE_POLICY).status(), { status: 'unconfigured', currentVersion: '0.7.0' });
  assert.deepEqual(createUpdateService('0.7.0', policy).status(), { status: 'not-checked', currentVersion: '0.7.0' });
  assert.ok(Object.isFrozen(DISABLED_UPDATE_POLICY));
});

test('installer verification checks size and hash first, and never trusts an installer without a configured publisher', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'gitviz-update-'));
  try {
    const file = join(directory, 'GitVizSetup.exe'); const bytes = Buffer.from('0123456789'); await writeFile(file, bytes);
    const good: UpdateManifest = parseManifest(manifest({ sha256: createHash('sha256').update(bytes).digest('hex') }), policy)!;
    const signed = async () => true, unsigned = async () => false;
    assert.equal(await verifyInstaller(file, { ...good, size: 11 }, policy, signed), 'size-mismatch');
    assert.equal(await verifyInstaller(file, { ...good, sha256: 'b'.repeat(64) }, policy, signed), 'hash-mismatch');
    assert.equal(await verifyInstaller(file, good, policy, signed), 'unsigned-not-trusted');
    assert.equal(await verifyInstaller(file, good, { ...policy, publisher: 'CN=Fixture' }, unsigned), 'signature-mismatch');
    assert.equal(await verifyInstaller(file, good, { ...policy, publisher: 'CN=Fixture' }, signed), 'verified');
  } finally { await rm(directory, { recursive: true, force: true }); }
});
