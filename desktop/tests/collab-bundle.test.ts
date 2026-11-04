import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { CollabBundles, LIMITS, nameReason, resolveRef, widerRoots, type BundleView } from '../src/collab-bundle';
import { analyze, classify } from '../src/collab-refs';
import { blockedRequest, contentType, previewCsp, privateHost, validPath } from '../src/collab-preview';

const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0]);
const WOFF2 = Buffer.concat([Buffer.from('wOF2'), Buffer.alloc(8)]);

async function site(files: Record<string, string | Buffer>): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), 'gitviz-bundle-'));
  for (const [path, body] of Object.entries(files)) {
    await mkdir(join(dir, ...path.split('/').slice(0, -1)), { recursive: true });
    await writeFile(join(dir, ...path.split('/')), body);
  }
  return dir;
}
const paths = (view: BundleView) => view.files.map(file => file.path).sort();
function bundles(pick: string | (() => string | null), confirm = async () => true, home = 'C:\\no-such-home') {
  return new CollabBundles(async () => (typeof pick === 'function' ? pick() : pick), confirm, home);
}

test('references: HTML, CSS and JS loads are found; links and fetch literals become candidates', async () => {
  const html = await analyze('html', [
    '<!doctype html><html><head>',
    '<link rel="stylesheet" href="css/site.css?v=2"><link rel="icon" href="/img/icon.png"><link rel="canonical" href="other.html">',
    '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/x@1/x.css">',
    '<style>body { background: url("img/bg.png") }</style>',
    '<script type="module">import { a } from "./js/a.js"; import("./js/lazy.js"); import(name);</script>',
    '</head><body style="background:url(img/b.webp)">',
    '<img src="img/a.png" srcset="img/a2.png 2x, img/a3.png 3x"><a href="about.html">about</a><a href="#top">top</a>',
    '<script src="js/main.js"></script><script src="//unpkg.com/y"></script><img src="data:image/png;base64,AAAA">',
    '<!-- <script src="comment.js"></script> -->',
    '</body></html>',
  ].join('\n'));
  assert.deepEqual(html.local.map(ref => ref.spec).sort(), ['./js/a.js', './js/lazy.js', '/img/icon.png', 'css/site.css', 'img/a.png', 'img/a2.png', 'img/a3.png', 'img/b.webp', 'img/bg.png', 'js/main.js'].sort());
  assert.deepEqual(html.external.sort(), ['cdn.jsdelivr.net', 'unpkg.com']);
  assert.deepEqual(html.candidates.map(ref => ref.spec), ['about.html']);
  assert.equal(html.unresolved.length, 1);
  assert.equal(html.local.find(ref => ref.spec === 'js/main.js')?.line, 8);

  const css = await analyze('css', '/* @import "skip.css"; */\n@import "base.css";\n@import url(theme.css);\n@font-face { src: url(\'../fonts/a.woff2\') format("woff2"); }\n.a { background: url(data:image/png;base64,AA) }');
  assert.deepEqual(css.local.map(ref => [ref.spec, ref.line]), [['base.css', 2], ['theme.css', 3], ['../fonts/a.woff2', 4]]);

  const js = await analyze('js', "// import './comment.js'\nimport a from './a.js';\nexport * from './b.mjs';\nimport 'lodash';\nconst w = new Worker('./worker.js');\nfetch('data.json'); fetch(url);\nconst s = \"import('./string.js')\";\nimport.meta.url;");
  assert.deepEqual(js.local.map(ref => ref.spec).sort(), ['./a.js', './b.mjs', './worker.js']);
  assert.deepEqual(js.candidates.map(ref => ref.spec), ['data.json']);
  assert.deepEqual(js.unresolved.map(ref => ref.spec).sort(), ['fetch(url)', 'lodash']);

  // TypeScript the lexer cannot read still yields its imports.
  const ts = await analyze('js', "import type { A } from './types';\nimport { b } from './b';\nconst x = <T,>(v: T) => v;\n");
  assert.ok(ts.local.some(ref => ref.spec === './b'));

  assert.deepEqual(classify('javascript:alert(1)'), { type: 'ignore' });
  assert.deepEqual(classify('C:/secret.txt'), { type: 'local', path: 'C:/secret.txt' }); // a drive letter is not a scheme; resolveRef refuses it
});

test('paths: resolution stays inside the root; hidden, node_modules and device names are refused', () => {
  assert.equal(resolveRef('index.html', 'css/a.css'), 'css/a.css');
  assert.equal(resolveRef('pages/a.html', '../css/a.css'), 'css/a.css');
  assert.equal(resolveRef('pages/a.html', '/css/a.css'), 'css/a.css');
  assert.equal(resolveRef('pages/a.html', 'img%20x/b.png'), 'pages/img x/b.png');
  assert.equal(resolveRef('pages/a.html', 'sub\\c.js'), 'pages/sub/c.js');
  for (const spec of ['../outside.css', '../../x.js', '/../x.js', 'C:/Windows/win.ini', '//server/share/x', 'a/../../x', 'dir/']) assert.equal(resolveRef('index.html', spec), null, spec);
  assert.equal(nameReason('.env'), 'hidden');
  assert.equal(nameReason('a/.git/config'), 'hidden');
  assert.equal(nameReason('node_modules/x/index.js'), 'hidden');
  assert.equal(nameReason('NUL.txt'), 'hidden');
  assert.equal(nameReason('a/b:stream.css'), 'hidden');
  assert.equal(nameReason('css/site.css'), null);
});

test('a start file brings its references; outside, hidden, wrong type and bad signatures are excluded', async () => {
  const dir = await site({
    'web/index.html': '<link rel="stylesheet" href="css/site.css"><script type="module" src="js/main.js"></script><img src="img/logo.png"><img src="img/fake.png">' +
      '<script src="../secret.js"></script><link rel="stylesheet" href=".hidden/x.css"><img src="video.mp4"><img src="missing.png"><a href="about.html">a</a>',
    'web/css/site.css': '@font-face { src: url(../fonts/a.woff2) } body { background: url(/img/bg.png) }',
    'web/js/main.js': "import { util } from './util.js';\nfetch('data.json');\nimport 'react';",
    'web/js/util.js': 'export const util = 1;\r\n',
    'web/img/logo.png': PNG, 'web/img/bg.png': PNG, 'web/img/fake.png': 'not a png', 'web/fonts/a.woff2': WOFF2,
    'web/.hidden/x.css': 'a{}', 'web/video.mp4': 'x', 'web/about.html': '<p>about</p>', 'web/data.json': '{}', 'secret.js': 'secret',
  });
  try {
    const set = bundles(join(dir, 'web', 'index.html'));
    const view = await set.open();
    assert.ok('bundle' in view);
    assert.equal(view.entry, 'index.html');
    assert.deepEqual(paths(view), ['css/site.css', 'fonts/a.woff2', 'img/bg.png', 'img/logo.png', 'index.html', 'js/main.js', 'js/util.js']);
    assert.ok(view.root.endsWith('web') && !view.root.includes(dir), 'only the folder name, never the absolute path');
    assert.ok(JSON.stringify(view.files.map(file => ({ ...file, bytes: undefined }))).indexOf(dir) < 0);
    const reasons = Object.fromEntries(view.excluded.map(item => [item.spec, item.reason]));
    assert.deepEqual(reasons, { 'img/fake.png': 'signature', '../secret.js': 'outside', '.hidden/x.css': 'hidden', 'video.mp4': 'type', 'missing.png': 'missing' });
    assert.deepEqual(view.candidates.map(item => item.path).sort(), ['about.html', 'js/data.json']);
    assert.deepEqual(view.unresolved.map(item => item.spec), ['react']);
    const util = view.files.find(file => file.path === 'js/util.js')!;
    assert.equal(util.text, 'export const util = 1;\n');
    assert.equal(util.language, 'javascript');
    const logo = view.files.find(file => file.path === 'img/logo.png')!;
    assert.equal(logo.kind, 'binary'); assert.equal(logo.mime, 'image/png'); assert.equal(logo.sha256?.length, 64); assert.equal(logo.bytes?.length, PNG.length);
    // A candidate is added by path and follows its own references; anything else needs the dialog.
    const added = await set.add({ bundle: view.bundle, path: 'about.html' });
    assert.ok('bundle' in added && paths(added).includes('about.html'));
    await assert.rejects(set.add({ bundle: view.bundle, path: '../secret.js' }), /NOT_PENDING/);
    // Saving writes only set files, keeps CRLF, and reports per file.
    const saved = await set.save({ bundle: view.bundle, files: [{ path: 'js/util.js', text: 'export const util = 2;\n' }, { path: 'nope.js', text: '' }] });
    assert.deepEqual(saved.results, [{ path: 'js/util.js', saved: true }, { path: 'nope.js', saved: false, error: 'NOT_OPEN' }]);
    assert.equal(await readFile(join(dir, 'web', 'js', 'util.js'), 'utf8'), 'export const util = 2;\r\n');
    assert.equal(set.close({ bundle: view.bundle }).closed, true);
    await assert.rejects(set.scan({ bundle: view.bundle, files: {} }), /NOT_OPEN/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('new references from editing wait for the host: nothing is read before approval, declined paths are not asked again', async () => {
  const dir = await site({ 'index.html': '<p>hi</p>', 'extra.css': 'body{}', 'photo.png': PNG, 'private.txt': 'x', '.env': 'SECRET=1' });
  try {
    const set = bundles(join(dir, 'index.html'));
    const view = await set.open();
    assert.ok('bundle' in view);
    const edited = '<link rel="stylesheet" href="extra.css"><img src="photo.png"><img src="../outside.png"><link rel="stylesheet" href=".env"><script src="nope.js"></script>';
    const scan = await set.scan({ bundle: view.bundle, files: { 'index.html': edited, 'not-in-set.html': '<script src="private.txt"></script>' } });
    assert.deepEqual(scan.pending.sort(), ['extra.css', 'photo.png']);
    await assert.rejects(set.approve({ bundle: view.bundle, path: 'private.txt', accept: true }), /NOT_PENDING/);
    assert.deepEqual(await set.approve({ bundle: view.bundle, path: 'photo.png', accept: false }), { declined: true });
    const approved = await set.approve({ bundle: view.bundle, path: 'extra.css', accept: true });
    assert.ok('bundle' in approved && paths(approved).includes('extra.css'));
    const again = await set.scan({ bundle: view.bundle, files: { 'index.html': edited } });
    assert.deepEqual(again.pending, [], 'declined and approved paths are not asked again');
    await assert.rejects(set.scan({ bundle: view.bundle, files: [] }), /INVALID_REQUEST/);
    await assert.rejects(set.approve({ bundle: view.bundle, path: 'x', accept: true, extra: 1 }), /INVALID_REQUEST/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('the root widens to the repository root at most; a picked file outside the root is refused', async () => {
  const dir = await site({ 'repo/.git/HEAD': 'ref', 'repo/shared/theme.css': 'a{}', 'repo/app/web/index.html': '<link rel="stylesheet" href="../../shared/theme.css">', 'outside.txt': 'x' });
  try {
    let picked: string | null = join(dir, 'repo', 'app', 'web', 'index.html');
    const set = bundles(() => picked);
    const view = await set.open();
    assert.ok('bundle' in view);
    assert.equal(view.canWiden, true);
    assert.deepEqual(view.excluded.map(item => item.reason), ['outside']);
    assert.deepEqual(await widerRoots(join(dir, 'repo', 'app', 'web'), 'C:\\no-such-home'), [join(dir, 'repo', 'app'), join(dir, 'repo')]);
    const wider = await set.widen({ bundle: view.bundle });
    assert.equal(wider.entry, 'web/index.html');
    const widest = await set.widen({ bundle: view.bundle });
    assert.equal(widest.entry, 'app/web/index.html');
    assert.deepEqual(paths(widest), ['app/web/index.html', 'shared/theme.css']);
    assert.equal(widest.canWiden, false);
    await assert.rejects(set.widen({ bundle: view.bundle }), /CANNOT_WIDEN/);
    picked = join(dir, 'outside.txt');
    await assert.rejects(set.add({ bundle: view.bundle }), /OUTSIDE_ROOT/);
    picked = null;
    assert.deepEqual(await set.add({ bundle: view.bundle }), { cancelled: true });
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('without a repository the root widens three levels and never to the profile folder or its parents', async () => {
  const dir = await site({ 'home/projects/a/b/c/index.html': '<p></p>' });
  try {
    const home = join(dir, 'home');
    const roots = await widerRoots(join(home, 'projects', 'a', 'b', 'c'), home);
    assert.deepEqual(roots, [join(home, 'projects', 'a', 'b'), join(home, 'projects', 'a'), join(home, 'projects')]);
    assert.deepEqual(await widerRoots(join(home, 'projects'), home), [], 'the profile folder itself is never a root');
    await mkdir(join(home, 'Desktop', 'site'), { recursive: true });
    assert.deepEqual(await widerRoots(join(home, 'Desktop', 'site'), home), [], 'nor Desktop itself');
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('links inside the root and set limits are refused', async () => {
  const many: Record<string, string> = { 'index.html': Array.from({ length: LIMITS.textFiles + 2 }, (_, i) => `<script src="s${i}.js"></script>`).join('') };
  for (let i = 0; i < LIMITS.textFiles + 2; i++) many[`s${i}.js`] = `// ${i}`;
  const dir = await site({ ...many, 'real/a.css': 'a{}' });
  try {
    const view = await bundles(join(dir, 'index.html')).open();
    assert.ok('bundle' in view);
    assert.equal(view.files.length, LIMITS.textFiles);
    assert.ok(view.excluded.some(item => item.reason === 'limit'));
    let linked = true;
    try { await symlink(join(dir, 'real'), join(dir, 'linked'), 'junction'); } catch { linked = false; }
    if (linked) {
      await writeFile(join(dir, 'page.html'), '<link rel="stylesheet" href="linked/a.css">');
      const page = await bundles(join(dir, 'page.html')).open();
      assert.ok('bundle' in page);
      assert.deepEqual(page.excluded.map(item => item.reason), ['link']);
    }
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('preview: only its own files, data/blob and public https/wss when allowed; CSP follows the switch', () => {
  const id = 'p0123456789abcdef0123456789abcdef';
  assert.equal(blockedRequest(`gitviz-preview://${id}/css/a.css`, id, true), false);
  assert.equal(blockedRequest('gitviz-preview://pother/css/a.css', id, true), true);
  assert.equal(blockedRequest('https://cdn.jsdelivr.net/npm/x', id, true), false);
  assert.equal(blockedRequest('https://cdn.jsdelivr.net/npm/x', id, false), true);
  assert.equal(blockedRequest('wss://echo.example.org/', id, true), false);
  for (const url of ['http://example.org/', 'file:///C:/Windows/win.ini', 'gitviz://app/index.html', 'https://localhost/', 'https://127.0.0.1/', 'https://10.1.2.3/',
    'https://192.168.0.1/', 'https://172.20.0.1/', 'https://169.254.169.254/latest', 'https://[::1]/', 'https://[fd00::1]/', 'https://router/', 'https://printer.local/',
    'https://user:pw@example.org/', 'https://2130706433/', 'not a url']) assert.equal(blockedRequest(url, id, true), true, url);
  assert.equal(blockedRequest('data:text/plain,hi', id, false), false);
  assert.equal(privateHost('example.org'), false);
  assert.match(previewCsp(true), /default-src 'self' data: blob: 'unsafe-inline' 'unsafe-eval' https:/);
  assert.match(previewCsp(true), /connect-src 'self' https: wss:/);
  assert.doesNotMatch(previewCsp(false), /https:|wss:/);
  assert.match(previewCsp(false), /frame-ancestors 'none'/);
  assert.equal(contentType('a/B.CSS'), 'text/css; charset=utf-8');
  assert.equal(contentType('font.woff2'), 'font/woff2');
  assert.equal(validPath('css/a.css'), true);
  for (const bad of ['/a.css', '../a.css', 'a/../b', 'a\\b', 'C:x', '', 'a//b']) assert.equal(validPath(bad), false, bad);
});

test('code references: Python imports, C headers with their sources and Java packages join the set only when the files exist', async () => {
  const py = await analyze('py', '# import commented\nimport os, helper as h\nfrom pkg.tool import twice\nfrom . import sibling\nfrom ..up import x\ns = """\nimport fake\n"""\n');
  const specs = py.optional.map(item => item.spec);
  for (const expected of ['./helper.py', '/helper.py', './os.py', './pkg/tool.py', './pkg/tool/twice.py', './sibling.py', '../up.py']) assert.ok(specs.includes(expected), expected);
  assert.ok(!specs.some(spec => spec.includes('commented') || spec.includes('fake')));
  const c = await analyze('c', '// #include "skip.h"\n#include <stdio.h>\n#include "lib/util.h"\n');
  assert.deepEqual(c.local.map(item => item.spec), ['lib/util.h']);
  assert.deepEqual(c.optional.map(item => item.spec), ['lib/util.c', 'lib/util.cpp', 'lib/util.cc', 'lib/util.cxx']);
  const java = await analyze('java', 'package app.core;\nimport java.util.List;\nimport util.Text;\nimport static util.Calc.add;\nimport other.*;\nclass Main { Helper h; String s = "Fake"; }\n');
  const javaSpecs = java.optional.map(item => item.spec);
  for (const expected of ['./../../util/Text.java', './../../util/Calc.java', './Helper.java', './Main.java']) assert.ok(javaSpecs.includes(expected), expected);
  assert.ok(!javaSpecs.some(spec => spec.includes('Fake') || spec.includes('java/util') || spec.includes('other')));

  const dir = await site({
    'main.py': 'import os\nimport helper\nfrom pkg.tool import twice\n', 'helper.py': 'X = 1\n', 'pkg/__init__.py': '', 'pkg/tool.py': 'from . import inner\n', 'pkg/inner.py': '',
    'c/main.c': '#include "util.h"\nint main(void){return 0;}\n', 'c/util.h': 'int f(void);\n', 'c/util.c': 'int f(void){return 1;}\n',
    'src/app/Main.java': 'package app;\nimport util.Text;\nclass Main { Helper h; }\n', 'src/app/Helper.java': 'package app;\nclass Helper {}\n', 'src/util/Text.java': 'package util;\npublic class Text {}\n',
  });
  try {
    const python = await bundles(join(dir, 'main.py')).open();
    assert.ok('bundle' in python);
    assert.deepEqual(paths(python), ['helper.py', 'main.py', 'pkg/__init__.py', 'pkg/inner.py', 'pkg/tool.py']);
    assert.deepEqual(python.excluded, [], 'standard library imports are not reported as missing');
    const cSet = await bundles(join(dir, 'c', 'main.c')).open();
    assert.ok('bundle' in cSet);
    assert.deepEqual(paths(cSet), ['main.c', 'util.c', 'util.h']);
    const javaSet = bundles(join(dir, 'src', 'app', 'Main.java'));
    const narrow = await javaSet.open();
    assert.ok('bundle' in narrow);
    assert.deepEqual(paths(narrow), ['Helper.java', 'Main.java']);
    const wide = await javaSet.widen({ bundle: narrow.bundle });
    assert.deepEqual(paths(wide), ['app/Helper.java', 'app/Main.java', 'util/Text.java']);
    // A new import of a module file waits for approval like any new reference; module guesses never count as missing.
    const set = bundles(join(dir, 'main.py'));
    const view = await set.open();
    assert.ok('bundle' in view);
    await writeFile(join(dir, 'extra.py'), '');
    const scan = await set.scan({ bundle: view.bundle, files: { 'main.py': 'import helper\nimport extra\nimport nothing_here\n' } });
    assert.deepEqual(scan.pending, ['extra.py']);
    assert.deepEqual(scan.missing, []);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('new files: added by name inside the root, listed when referenced but missing, and created on disk only on save', async () => {
  const dir = await site({ 'site/index.html': '<p>hi</p>\r\n', 'site/taken.js': 'x', 'outside.js': 'y' });
  try {
    let confirm = false;
    const set = bundles(join(dir, 'site', 'index.html'), async () => confirm);
    const view = await set.open();
    assert.ok('bundle' in view);
    const scan = await set.scan({ bundle: view.bundle, files: { 'index.html': '<script src="js/app.js"></script><script src="taken.js"></script><link href="x.unknown" rel="stylesheet">' } });
    assert.deepEqual(scan.missing, ['js/app.js']);
    assert.deepEqual(scan.pending, ['taken.js']);
    for (const bad of ['../outside2.js', '.env', 'node_modules/a.js', 'a/con.js', 'x.exe', 'pic.svg', '', 'a/']) await assert.rejects(set.create({ bundle: view.bundle, path: bad }), /NOT_ALLOWED/, bad);
    await assert.rejects(set.create({ bundle: view.bundle, path: 'taken.js' }), /EXISTS/);
    await assert.rejects(set.create({ bundle: view.bundle, path: 'index.html' }), /EXISTS/);
    await assert.rejects(set.create({ bundle: view.bundle, path: 'a.js', extra: 1 }), /INVALID_REQUEST/);
    const created = await set.create({ bundle: view.bundle, path: 'js/app.js' });
    assert.deepEqual(created.files.find(file => file.path === 'js/app.js'), { path: 'js/app.js', kind: 'text', size: 0, language: 'javascript', text: '' });
    await set.create({ bundle: view.bundle, path: 'notes/a/b.txt' });
    await assert.rejects(set.create({ bundle: view.bundle, path: 'js/app.js' }), /EXISTS/);
    await assert.rejects(readFile(join(dir, 'site', 'js', 'app.js')), /ENOENT/, 'nothing on disk before saving');
    // Someone else made the file in the meantime: like an edited file, the host decides.
    await mkdir(join(dir, 'site', 'notes', 'a'), { recursive: true });
    await writeFile(join(dir, 'site', 'notes', 'a', 'b.txt'), 'theirs');
    const saved = await set.save({ bundle: view.bundle, files: [{ path: 'js/app.js', text: 'a\nb\n' }, { path: 'notes/a/b.txt', text: 'mine' }] });
    assert.deepEqual(saved.results, [{ path: 'js/app.js', saved: true }, { path: 'notes/a/b.txt', saved: false, error: 'FILE_CHANGED' }]);
    assert.equal(await readFile(join(dir, 'site', 'js', 'app.js'), 'utf8'), 'a\r\nb\r\n', 'the start file line endings');
    assert.equal(await readFile(join(dir, 'site', 'notes', 'a', 'b.txt'), 'utf8'), 'theirs');
    confirm = true;
    const again = await set.save({ bundle: view.bundle, files: [{ path: 'js/app.js', text: 'c' }, { path: 'notes/a/b.txt', text: 'mine' }] });
    assert.ok(again.results.every(item => item.saved));
    assert.equal(await readFile(join(dir, 'site', 'notes', 'a', 'b.txt'), 'utf8'), 'mine');
    // After the first save it is an ordinary file: an outside change is detected by content.
    confirm = false;
    await writeFile(join(dir, 'site', 'js', 'app.js'), 'changed');
    assert.deepEqual((await set.save({ bundle: view.bundle, files: [{ path: 'js/app.js', text: 'd' }] })).results, [{ path: 'js/app.js', saved: false, error: 'FILE_CHANGED' }]);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('a new file under a junction that leaves the root is refused', async () => {
  const dir = await site({ 'site/index.html': '<p>hi</p>', 'elsewhere/keep.txt': 'x' });
  try {
    await symlink(join(dir, 'elsewhere'), join(dir, 'site', 'linked'), 'junction');
    const set = bundles(join(dir, 'site', 'index.html'));
    const view = await set.open();
    assert.ok('bundle' in view);
    await assert.rejects(set.create({ bundle: view.bundle, path: 'linked/new.txt' }), /NOT_ALLOWED/);
    await assert.rejects(set.create({ bundle: view.bundle, path: 'linked/deeper/new.txt' }), /NOT_ALLOWED/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});
