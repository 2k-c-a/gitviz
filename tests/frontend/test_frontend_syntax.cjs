// node tests/frontend/test_frontend_syntax.cjs — 모든 프런트 스크립트가 ES 모듈로 문법 오류 없이 읽히는지 확인한다.
// 한 파일의 문법 오류는 그 파일을 불러오는 화면 전체를 멈춘다(2026-11-11: status.js의 따옴표 하나로 '폴더 등록'이 동작하지 않았다).
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..', '..', 'frontend', 'js');
const files = [];
(function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full); else if (entry.name.endsWith('.js')) files.push(full);
  }
})(root);
assert.ok(files.length > 20, 'frontend/js 파일을 찾지 못했습니다');
const failures = [];
for (const file of files) {
  const result = spawnSync(process.execPath, ['--input-type=module', '--check'], { input: fs.readFileSync(file), encoding: 'utf8' });
  if (result.status !== 0) failures.push(`${path.relative(root, file)}: ${(result.stderr.match(/SyntaxError[^\n]*/) || [result.stderr.trim()])[0]}`);
}
assert.deepEqual(failures, [], `문법 오류가 있는 프런트 스크립트:\n${failures.join('\n')}`);
console.log(`test_frontend_syntax: ok (${files.length} files)`);
