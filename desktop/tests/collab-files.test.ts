import assert from 'node:assert/strict';
import { mkdtemp, readdir, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { CollabFiles, MAX_FILE_BYTES } from '../src/collab-files';

async function folder(): Promise<string> { return mkdtemp(join(tmpdir(), 'gitviz-collab-')); }

test('the page gets a handle, the name and LF text; saving restores CRLF and the BOM', async () => {
  const dir = await folder();
  try {
    const path = join(dir, 'app.py');
    await writeFile(path, '﻿print(1)\r\nprint(2)\r\n');
    const files = new CollabFiles(async () => path, async () => false);
    const opened = await files.open();
    assert.ok(!('cancelled' in opened));
    assert.deepEqual(Object.keys(opened).sort(), ['handle', 'language', 'name', 'text']);
    assert.equal(opened.name, 'app.py');
    assert.equal(opened.language, 'python');
    assert.equal(opened.text, 'print(1)\nprint(2)\n');
    assert.ok(Object.values(opened).every(value => !String(value).includes(dir)), 'the path never reaches the page');
    assert.deepEqual(await files.save({ handle: opened.handle, text: 'print(1)\nprint(3)\n' }), { saved: true });
    assert.equal(await readFile(path, 'utf8'), '﻿print(1)\r\nprint(3)\r\n');
    // Saving again right away needs no confirmation: the stored hash follows our own write. No temp file is left behind.
    await files.save({ handle: opened.handle, text: 'x\n' });
    assert.deepEqual(await readdir(dir), ['app.py']);
    assert.equal(files.close({ handle: opened.handle }).closed, true);
    await assert.rejects(files.save({ handle: opened.handle, text: '' }), /NOT_OPEN/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('a file changed on disk is overwritten only after the host confirms', async () => {
  const dir = await folder();
  try {
    const path = join(dir, 'notes.md');
    await writeFile(path, 'a\n');
    let answer = false; let asked = 0;
    const files = new CollabFiles(async () => path, async name => { asked++; assert.equal(name, 'notes.md'); return answer; });
    const opened = await files.open();
    assert.ok(!('cancelled' in opened));
    assert.equal(opened.language, 'markdown');
    await writeFile(path, 'changed elsewhere\n');
    await assert.rejects(files.save({ handle: opened.handle, text: 'mine\n' }), /FILE_CHANGED/);
    assert.equal(await readFile(path, 'utf8'), 'changed elsewhere\n');
    answer = true;
    await files.save({ handle: opened.handle, text: 'mine\n' });
    assert.equal(await readFile(path, 'utf8'), 'mine\n');
    assert.equal(asked, 2);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('only small UTF-8 regular files open; bad save requests are refused', async () => {
  const dir = await folder();
  try {
    const big = join(dir, 'big.txt'); await writeFile(big, 'x'.repeat(MAX_FILE_BYTES + 1));
    const binary = join(dir, 'bin.dat'); await writeFile(binary, Buffer.from([0x00, 0x01, 0x02]));
    const latin = join(dir, 'latin.txt'); await writeFile(latin, Buffer.from([0x63, 0x61, 0x66, 0xe9]));
    const plain = join(dir, 'plain.txt'); await writeFile(plain, 'ok');
    const open = (path: string | null) => new CollabFiles(async () => path, async () => true).open();
    await assert.rejects(open(big), /FILE_TOO_LARGE/);
    await assert.rejects(open(binary), /NOT_TEXT/);
    await assert.rejects(open(latin), /NOT_TEXT/);
    await assert.rejects(open(dir), /INVALID_FILE/);
    assert.deepEqual(await open(null), { cancelled: true });
    const link = join(dir, 'link.txt');
    // Creating symlinks needs developer mode or admin on Windows; check it when the OS lets us.
    if (await symlink(plain, link).then(() => true, () => false)) await assert.rejects(open(link), /INVALID_FILE/);
    const files = new CollabFiles(async () => plain, async () => true);
    const opened = await files.open();
    assert.ok(!('cancelled' in opened));
    assert.equal(opened.language, 'plain');
    for (const bad of [null, [], { handle: opened.handle }, { handle: opened.handle, text: 1 }, { handle: opened.handle, text: '', path: 'x' },
      { handle: opened.handle, text: 'x'.repeat(400 * 1024 + 1) }]) await assert.rejects(files.save(bad), /INVALID_REQUEST/);
    files.closeAll();
    await assert.rejects(files.save({ handle: opened.handle, text: '' }), /NOT_OPEN/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});
