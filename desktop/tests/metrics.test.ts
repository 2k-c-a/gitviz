import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import * as ts from 'typescript';
import { measureTypescript } from '../src/metrics';

test('fixed JS/TS AST and duplicate-token metrics use snapshot text without filesystem/config execution', () => {
  const source = 'export function own(x:number){if(x && x>1){return x+1;}else{return x-1;}}';
  const hash = createHash('sha256').update(source).digest('hex');
  const read = ts.sys.readFile;
  ts.sys.readFile = () => { throw new Error('filesystem forbidden'); };
  try {
    const files = ['a.ts', 'b.ts'].map((path, i) => ({ fileId: String(i), path, facet: 'selected', sourceHash: hash, text: source }));
    const result = measureTypescript(files);
    assert.deepEqual(result, measureTypescript(files));
    assert.equal(result[0]!.functionCount, 1);
    assert.equal(result[0]!.branchMetric, 3);
    assert.ok(result[0]!.tokenWindows! > 0);
    assert.equal(result[0]!.duplicateWindows, result[0]!.tokenWindows);
    assert.throws(() => measureTypescript([{ ...files[0]!, sourceHash: '0'.repeat(64) }]));
    const broken = 'function (';
    const invalid = measureTypescript([{ ...files[0]!, text: broken, sourceHash: createHash('sha256').update(broken).digest('hex') }]);
    assert.equal(invalid[0]!.functionCount, null);
  } finally { ts.sys.readFile = read; }
});
