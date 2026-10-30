import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createHash } from 'node:crypto';
import * as ts from 'typescript';
import { parseTypescript, type Source } from '../src/impact-worker';
function source(path: string, text: string): Source { return { path, text, facet: 'selected', fileId: path, sourceHash: createHash('sha256').update(text).digest('hex') }; }
test('TypeScript/JavaScript virtual parser resolves selected lexical functions and never reads tsconfig/lib/project files', () => {
  const before = ts.sys.readFile;
  ts.sys.readFile = () => { throw new Error('UNEXPECTED_FILE_READ'); };
  try {
    const result = parseTypescript([source('a.ts', 'export function hello(){ return 1; }\n'), source('b.ts', 'import {hello} from "./a";\nhello();\n'), source('c.js', 'function own(){return 2;}\nown(); obj.dynamic();\n')]);
    assert.ok(result.records.some(r => r.referenceKind === 'import' && r.path === 'b.ts'));
    assert.ok(result.records.some(r => r.symbol === 'hello' && r.resolved && r.targetFileId === 'a.ts'));
    assert.ok(result.records.some(r => r.symbol === 'own' && r.resolved));
    assert.ok(result.records.some(r => r.symbol === 'obj.dynamic' && !r.resolved));
    assert.ok(result.records.every(r => r.observed && r.kind === 'static_reference'));
  } finally { ts.sys.readFile = before; }
});
test('external imports, rebound calls, cross-facet paths and invalid source hashes never become confirmed references', () => {
  const result = parseTypescript([source('x.ts', 'import x from "outside";\nfunction f(){}\nf=()=>{}; f();\n')]);
  assert.equal(result.records.find(r => r.symbol === 'f')?.resolved, false);
  const staged = { ...source('a.ts', 'export function f(){}'), facet: 'staged' };
  const unstaged = { ...source('b.ts', 'import {f} from "./a"; f();'), facet: 'unstaged' };
  assert.equal(parseTypescript([staged, unstaged]).records.find(r => r.path === 'b.ts' && r.symbol === 'f')?.resolved, false);
  assert.throws(() => parseTypescript([{ ...source('a.js', 'x()'), sourceHash: '0'.repeat(64) }]));
});
