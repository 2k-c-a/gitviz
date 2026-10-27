import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { loadKey, type ProtectedStorage } from '../src/secrets';
import { spawn } from 'node:child_process';

const storage: ProtectedStorage = { available: async () => true, encrypt: async value => Buffer.from(value).reverse(), decrypt: async value => Buffer.from(value).reverse().toString() };
test('protected key survives restart; corruption never overwrites or regenerates', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'gitviz-secret-'));
  const file = join(directory, 'key');
  try {
    const key = await loadKey(file, storage);
    assert.deepEqual(await loadKey(file, storage), key);
    assert.notDeepEqual(await readFile(file), key);
    await writeFile(file, 'corrupt');
    await assert.rejects(loadKey(file, storage), /STORAGE_UNAVAILABLE/);
    assert.equal(await readFile(file, 'utf8'), 'corrupt');
  } finally {
    assert.equal(dirname(resolve(directory)), resolve(tmpdir())); assert.ok(basename(directory).startsWith('gitviz-secret-'));
    await rm(directory, { recursive: true });
  }
});
test('OS protection unavailable fails closed', async () => {
  await assert.rejects(loadKey('unused', { ...storage, available: async () => false }), /STORAGE_UNAVAILABLE/);
});
test('missing key beside encrypted data never generates a replacement', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'gitviz-key-loss-'));
  try {
    await writeFile(join(directory, 'secret-check.bin'), 'encrypted-fixture');
    await assert.rejects(loadKey(join(directory, 'key'), storage), /STORAGE_UNAVAILABLE/);
    await assert.rejects(readFile(join(directory, 'key')), { code: 'ENOENT' });
  } finally {
    assert.equal(dirname(resolve(directory)), resolve(tmpdir())); assert.ok(basename(directory).startsWith('gitviz-key-loss-'));
    await rm(directory, { recursive: true });
  }
});

test('key publication survives termination at open/write/flush/publish/cleanup boundaries', async () => {
  for (const phase of ['opened', 'partial', 'synced', 'published', 'cleaned']) {
    const directory = await mkdtemp(join(tmpdir(), 'gitviz-key-cut-'));
    const file = join(directory, 'key');
    const source = `
const fs=require('node:fs/promises');
const target=process.argv[1],file=process.argv[2];
async function checkpoint(name){if(name===target){console.log(name);setInterval(()=>{},1000);await new Promise(()=>{});}}
const open=fs.open,link=fs.link,unlink=fs.unlink;
fs.open=async(...args)=>{
  const h=await open(...args);
  if(!String(args[0]).endsWith('.pending'))return h;
  await checkpoint('opened');
  return {writeFile:async b=>{await h.writeFile(b.subarray(0,2));await checkpoint('partial');await h.writeFile(b.subarray(2));},sync:async()=>{await h.sync();await checkpoint('synced');},close:()=>h.close()};
};
fs.link=async(...a)=>{await link(...a);await checkpoint('published');};
fs.unlink=async(...a)=>{await unlink(...a);await checkpoint('cleaned');};
const {loadKey}=require(${JSON.stringify(resolve(__dirname, '../src/secrets.js'))});
loadKey(file,{available:async()=>true,encrypt:async s=>Buffer.from(s).reverse(),decrypt:async b=>Buffer.from(b).reverse().toString()}).then(()=>process.exit(2)).catch(()=>process.exit(3));
`;
    const child = spawn(process.execPath, ['-e', source, phase, file], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    const exit = new Promise<void>(resolve => child.once('exit', () => resolve()));
    let deadline: NodeJS.Timeout | undefined;
    try {
      const reached = await new Promise<string>((resolve, reject) => {
        deadline = setTimeout(() => reject(new Error('checkpoint timeout')), 5000);
        child.stdout.once('data', (data: Buffer) => resolve(data.toString().trim()));
        child.once('error', reject);
      });
      assert.equal(reached, phase);
      child.kill(); await exit;
      let published: Buffer | undefined;
      try { published = await readFile(file); } catch (error) { assert.equal((error as NodeJS.ErrnoException).code, 'ENOENT'); }
      const recovered = await loadKey(file, storage);
      assert.equal(recovered.length, 32); assert.deepEqual(await loadKey(file, storage), recovered);
      if (published) assert.deepEqual(await readFile(file), published, 'published key must never be overwritten');
    } finally {
      if (deadline) clearTimeout(deadline);
      if (child.exitCode === null) child.kill();
      await exit;
      assert.equal(dirname(resolve(directory)), resolve(tmpdir())); assert.ok(basename(directory).startsWith('gitviz-key-cut-'));
      await rm(directory, { recursive: true });
    }
  }
});

test('database without its key and concurrent key creation both fail closed', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'gitviz-key-cut-'));
  const file = join(directory, 'key');
  try {
    const database = join(directory, 'workspace.sqlite3');
    await writeFile(database, 'existing-encrypted-state');
    await assert.rejects(loadKey(file, storage), /STORAGE_UNAVAILABLE/);
    await assert.rejects(readFile(file), { code: 'ENOENT' });
    await rm(database);
    const winner = Buffer.from(Buffer.alloc(32, 7).toString('base64')).reverse();
    await assert.rejects(loadKey(file, { ...storage, encrypt: async value => { await writeFile(file, winner); return Buffer.from(value).reverse(); } }), /STORAGE_UNAVAILABLE/);
    assert.deepEqual(await readFile(file), winner);
  } finally {
    assert.equal(dirname(resolve(directory)), resolve(tmpdir())); assert.ok(basename(directory).startsWith('gitviz-key-cut-'));
    await rm(directory, { recursive: true });
  }
});
