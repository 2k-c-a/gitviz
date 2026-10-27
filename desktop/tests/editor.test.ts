import test from 'node:test';
import assert from 'node:assert/strict';
import { editorArguments } from '../src/editor';

test('fixed editor argv preserves absolute Unicode/metacharacter paths as one argument', () => {
  const path = 'C:\\한글 space\\-name & x.ts';
  assert.deepEqual(editorArguments(path, 12, 3), ['--reuse-window', '--goto', `${path}:12:3`]);
  for (const unsafe of ['relative.ts', '--help', '//host/share/a.ts', '\\\\host\\share\\a.ts', 'C:\\bad\nfile.ts']) assert.throws(() => editorArguments(unsafe, 1, 1));
  assert.throws(() => editorArguments(path, -1, 1));
  assert.throws(() => editorArguments(path, 1, Number.NaN));
});
