// node tests/frontend/test_file_tree.cjs — 디렉토리 트리 모델(집계·정렬·검색·펼침)을 DOM 없이 검증
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');

Object.defineProperty(globalThis, 'location', { value: { search: '', href: 'http://localhost/' }, configurable: true, writable: true });
const load = (f) => import(pathToFileURL(path.resolve(__dirname, '..', '..', 'frontend', 'js', f)).href);

async function main() {
  const { buildTreeModel, visibleRows, findNode, allFolderPaths } = await load('file-tree.js');
  const scoreOf = (f) => f.score;
  const file = (name, dir, score, churn = null, bugfix = null) => ({ name, path: dir ? `${dir}/${name}` : name, score, churn, bugfix_count: bugfix });
  const tree = { name: 'root', children: [
    { name: 'app', avgRisk: 0.6, children: [
      { name: 'api', avgRisk: 0.75, children: [file('a.py', 'app/api', 0.9, 100, 3), file('b.py', 'app/api', 0.6, 50, null)] },
      file('main.py', 'app', null, null, null),
    ] },
    { name: 'docs', avgRisk: 0.1, children: [file('readme.md', 'docs', 0.1, 5, 0)] },
    file('setup.py', '', 0.8, 10, 1),
  ] };

  const model = buildTreeModel(tree, { scoreOf, threshold: 0.7 });
  const app = findNode(model, 'app');
  const api = findNode(model, 'app/api');
  assert.equal(model.fileCount, 5);
  assert.equal(app.fileCount, 3);
  assert.equal(app.high, 1, '임계값 이상 파일만 센다');
  assert.equal(model.high, 2);
  assert.equal(api.churn, 150, '수정량 합');
  assert.equal(api.bugfix, 3, '값 없는 파일(null)은 합에서 뺀다');
  assert.equal(findNode(model, 'app/main.py'), null, '파일 경로는 폴더 찾기에 쓰지 않는다');
  assert.equal(api.path, 'app/api');
  assert.equal(model.maxHigh, 1);
  const emptyDir = buildTreeModel({ children: [{ name: 'x', children: [file('y', 'x', null)] }] }, { scoreOf, threshold: 0.7 });
  assert.equal(findNode(emptyDir, 'x').churn, null, '모두 값 없음이면 0이 아니라 값 없음');

  /* 접힌 상태: 최상위만. 폴더가 파일보다 먼저, 점수순에서는 임계값 이상 파일 수가 많은 폴더가 위 */
  let rows = visibleRows(model, { expanded: new Set(), sort: 'score' });
  assert.deepEqual(rows.map(r => r.node.path), ['app', 'docs', 'setup.py']);
  rows = visibleRows(model, { expanded: new Set(['app', 'app/api']), sort: 'score' });
  assert.deepEqual(rows.map(r => r.node.path), ['app', 'app/api', 'app/api/a.py', 'app/api/b.py', 'app/main.py', 'docs', 'setup.py']);
  assert.deepEqual(rows.map(r => r.depth), [0, 1, 2, 2, 1, 0, 0]);
  assert.equal(rows[0].open, true);
  rows = visibleRows(model, { expanded: new Set(['app/api']), sort: 'score' });
  assert.equal(rows.length, 3, '상위 폴더가 접혀 있으면 하위의 펼침 상태는 보이지 않는다');

  /* 이름순 */
  const named = buildTreeModel({ children: [file('b.js', '', 0.9), file('a.js', '', 0.1)] }, { scoreOf, threshold: 0.7 });
  assert.deepEqual(visibleRows(named, { expanded: new Set(), sort: 'name' }).map(r => r.node.name), ['a.js', 'b.js']);
  assert.deepEqual(visibleRows(named, { expanded: new Set(), sort: 'score' }).map(r => r.node.name), ['b.js', 'a.js']);
  const nulls = buildTreeModel({ children: [file('n.js', '', null), file('z.js', '', 0)] }, { scoreOf, threshold: 0.7 });
  assert.deepEqual(visibleRows(nulls, { expanded: new Set(), sort: 'score' }).map(r => r.node.name), ['z.js', 'n.js'], '점수 0은 값, 값 없음은 뒤');

  /* 검색: 맞는 파일과 그 상위 폴더만, 모두 펼친다 */
  rows = visibleRows(model, { expanded: new Set(), sort: 'score', query: 'API/b' });
  assert.deepEqual(rows.map(r => r.node.path), ['app', 'app/api', 'app/api/b.py']);
  assert.equal(visibleRows(model, { expanded: new Set(), query: 'nothing' }).length, 0);

  /* 폴더만 보기: 기준 폴더의 자식부터 깊이 0 */
  rows = visibleRows(api, { expanded: new Set() });
  assert.deepEqual(rows.map(r => [r.node.name, r.depth]), [['a.py', 0], ['b.py', 0]]);
  assert.deepEqual([...allFolderPaths(model)].sort(), ['app', 'app/api', 'docs']);

  /* 하위가 폴더 하나뿐인 폴더는 한 줄로 합친다. 경로 찾기·검색은 그대로 */
  const chain = buildTreeModel({ children: [{ name: 'src', children: [{ name: 'main', children: [{ name: 'java', children: [file('A.java', 'src/main/java', 0.9), file('B.java', 'src/main/java', 0.2)] }] }] }, file('pom.xml', '', 0.1)] }, { scoreOf, threshold: 0.7 });
  rows = visibleRows(chain, { expanded: new Set(['src/main/java']), sort: 'score' });
  assert.deepEqual(rows.map(r => [r.node.name, r.depth]), [['src/main/java', 0], ['A.java', 1], ['B.java', 1], ['pom.xml', 0]]);
  assert.equal(findNode(chain, 'src/main/java').fileCount, 2);
  assert.equal(findNode(chain, 'src/main'), null, '합쳐진 중간 경로는 따로 없다');
  assert.equal(findNode(chain, 'src/main/java').high, 1);
  assert.deepEqual(visibleRows(chain, { expanded: new Set(), query: 'b.java' }).map(r => r.node.path), ['src/main/java', 'src/main/java/B.java']);

  console.log('test_file_tree: ok');
}
main().catch((error) => { console.error(error); process.exit(1); });
