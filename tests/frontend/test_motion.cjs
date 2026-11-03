// node tests/frontend/test_motion.cjs — 가짜 DOM·가짜 Motion으로 frontend/js/ui/motion.js의 동작을 검증한다
// (지표 숫자 카운트가 새 값을 덮어쓰지 않는지 · 화면 아래 카드가 계속 숨겨지지 않는지 · 팝업 감시 범위)
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');

const tick = () => new Promise((resolve) => setImmediate(resolve));

/* ── 최소한의 가짜 DOM ── */
const APP_MAIN = { name: '.app-main' };
class FakeElement {
  constructor({ classes = [], text = '', top = 0, id = '', inAppMain = false } = {}) {
    this.classes = new Set(classes);
    this.id = id;
    this.text = text;
    this.top = top;
    this.inAppMain = inAppMain;
    this.nodeType = 1;
    this.isConnected = true;
    this.hidden = false;
    this.matchCalls = 0;
    this.style = { opacity: '', transform: '', removeProperty(name) { this[name] = ''; } };
    this.classList = { contains: (name) => this.classes.has(name) };
  }
  get textContent() { return this.text; }
  set textContent(value) { this.text = String(value); }
  matches(selector) {
    this.matchCalls += 1;
    return selector.split(',').map((part) => part.trim()).some((part) => (
      (part.startsWith('.') && this.classes.has(part.slice(1))) || (part.startsWith('#') && this.id === part.slice(1))));
  }
  closest(selector) {
    if (this.matches(selector)) return this;
    return this.inAppMain && selector === '.app-main' ? APP_MAIN : null;
  }
  contains(other) { return other === this; }
  querySelector() { return this.panel ?? null; }
  querySelectorAll() { return []; }
  getBoundingClientRect() { return { top: this.top }; }
  getClientRects() { return [{}]; }
}

const registry = [];
const body = new FakeElement();
body.querySelectorAll = (selector) => registry.filter((element) => element.matches(selector));

/* ── 가짜 Motion: 호출만 기록하고, 프레임은 테스트가 직접 진행시킨다 ── */
const animations = [];
const watchers = [];
const fakeMotion = {
  animate(...args) {
    const record = { args, options: args[2], stopped: false };
    animations.push(record);
    return { stop() { record.stopped = true; }, finished: Promise.resolve() };
  },
  inView(element, callback, options) {
    const record = { element, callback, options, stopped: false };
    watchers.push(record);
    return () => { record.stopped = true; };
  },
};

/* ── 가짜 타이머: 예약만 기록 ── */
const realSetTimeout = globalThis.setTimeout;
const realClearTimeout = globalThis.clearTimeout;
const timers = [];
const fakeSetTimeout = (fn, ms) => { const timer = { fn, ms, cleared: false }; timers.push(timer); return timer; };
const fakeClearTimeout = (timer) => { if (timer) timer.cleared = true; };
const livingTimers = () => timers.filter((timer) => !timer.cleared && timer.ms === 2500);

const observers = [];
globalThis.MutationObserver = class { constructor(callback) { observers.push(callback); } observe() {} };
globalThis.matchMedia = () => ({ matches: false });
globalThis.requestAnimationFrame = (fn) => setImmediate(fn);
globalThis.innerHeight = 800;
globalThis.Motion = fakeMotion;
globalThis.document = {
  body,
  head: { append() {} },
  querySelector: (selector) => (selector === '.app-main' ? APP_MAIN : null),
  querySelectorAll: (selector) => registry.filter((element) => element.matches(selector)),
  addEventListener() {},
  createElement: () => ({}),
};

const added = (...nodes) => observers[0]([{ type: 'childList', target: body, addedNodes: nodes }]);
const counting = () => animations.filter((record) => typeof record.args[0] === 'number');
const reset = () => { animations.length = 0; watchers.length = 0; timers.length = 0; };

const tests = [];
const test = (name, fn) => tests.push({ name, fn });

/* 1. 숫자 카운트 */
test('카운트가 끝나면 원래 문자열로 맞춘다', async () => {
  reset();
  const metric = new FakeElement({ classes: ['m-val'], text: '1,284' });
  registry.push(metric); added(metric);
  const [count] = counting();
  assert.ok(count, '10 이상 숫자는 카운트 애니메이션을 시작한다');
  count.options.onUpdate(642.4);
  assert.equal(metric.textContent, '642');
  count.options.onUpdate(1284);
  assert.equal(metric.textContent, '1,284');
  count.options.onComplete();
  assert.equal(metric.textContent, '1,284');
});

test('카운트 도중 페이지가 새 값을 쓰면 옛 값으로 덮어쓰지 않는다', async () => {
  reset();
  const metric = new FakeElement({ classes: ['m-val'], text: '1,284' });
  registry.push(metric); added(metric);
  const [count] = counting();
  count.options.onUpdate(600);
  assert.equal(metric.textContent, '600');
  metric.textContent = '2,000'; // 범위를 바꿔 같은 요소의 값이 새로 채워진 경우
  count.options.onUpdate(900);
  assert.equal(metric.textContent, '2,000', '새 값이 카운트 프레임에 덮이면 안 된다');
  count.options.onComplete();
  assert.equal(metric.textContent, '2,000', '끝날 때 옛 값으로 되돌리면 안 된다');
});

test('카운트 도중 값이 "—"로 바뀌어도 그대로 둔다', async () => {
  reset();
  const metric = new FakeElement({ classes: ['bus-num'], text: '40' });
  registry.push(metric); added(metric);
  const [count] = counting();
  count.options.onUpdate(12);
  metric.textContent = '—';
  count.options.onUpdate(30);
  count.options.onComplete();
  assert.equal(metric.textContent, '—');
});

test('10 미만 숫자는 세지 않는다', async () => {
  reset();
  const metric = new FakeElement({ classes: ['m-val'], text: '7' });
  registry.push(metric); added(metric);
  assert.equal(counting().length, 0);
});

/* 2. 화면 아래 카드 */
test('화면 아래 카드도 스크롤 알림이 없으면 타이머가 보이게 한다', async () => {
  reset();
  globalThis.setTimeout = fakeSetTimeout; globalThis.clearTimeout = fakeClearTimeout;
  try {
    const card = new FakeElement({ classes: ['panel'], top: 2000 });
    registry.push(card); added(card); await tick();
    assert.equal(card.style.opacity, '0', '처음에는 숨겨 둔다');
    assert.equal(watchers.length, 1, '스크롤로 보일 때를 기다린다');
    const pending = livingTimers();
    assert.ok(pending.length >= 1, '안전장치 타이머가 걸려 있어야 한다');
    pending.forEach((timer) => timer.fn());
    assert.equal(card.style.opacity, '', '알림이 오지 않아도 계속 투명하면 안 된다');
  } finally { globalThis.setTimeout = realSetTimeout; globalThis.clearTimeout = realClearTimeout; }
});

test('화면 아래 카드가 보이면 애니메이션을 재생하고 타이머를 새로 건다', async () => {
  reset();
  globalThis.setTimeout = fakeSetTimeout; globalThis.clearTimeout = fakeClearTimeout;
  try {
    const card = new FakeElement({ classes: ['panel'], top: 2000 });
    registry.push(card); added(card); await tick();
    watchers[0].callback();
    assert.ok(animations.some((record) => record.args[0] === card), '보이는 순간 올라오는 애니메이션이 재생된다');
    assert.equal(livingTimers().length, 1, '처음 타이머는 취소하고 하나만 남는다');
  } finally { globalThis.setTimeout = realSetTimeout; globalThis.clearTimeout = realClearTimeout; }
});

test('처음부터 보이는 카드는 기존처럼 재생하고 타이머를 건다', async () => {
  reset();
  globalThis.setTimeout = fakeSetTimeout; globalThis.clearTimeout = fakeClearTimeout;
  try {
    const card = new FakeElement({ classes: ['panel'], top: 100 });
    registry.push(card); added(card); await tick();
    assert.equal(watchers.length, 0);
    assert.ok(animations.some((record) => record.args[0] === card));
    assert.equal(livingTimers().length, 1);
  } finally { globalThis.setTimeout = realSetTimeout; globalThis.clearTimeout = realClearTimeout; }
});

test('.app-main 밖의 카드는 화면 기준으로, 안의 카드는 .app-main 기준으로 감시한다', async () => {
  reset();
  const outside = new FakeElement({ classes: ['panel'], top: 2000 });
  const inside = new FakeElement({ classes: ['panel'], top: 2000, inAppMain: true });
  registry.push(outside, inside); added(outside, inside); await tick();
  const rootOf = (element) => watchers.find((watcher) => watcher.element === element).options.root;
  assert.equal(rootOf(outside), undefined, '.app-main 밖 요소에 .app-main을 root로 쓰면 알림이 오지 않는다');
  assert.equal(rootOf(inside), APP_MAIN);
});

/* 6. 팝업 감시 */
test('팝업이 열릴 때만 한 번 애니메이션하고, 같은 요소의 속성 변경은 한 번만 처리한다', async () => {
  reset();
  const overlay = new FakeElement({ classes: ['modal-overlay'] });
  overlay.panel = new FakeElement({ classes: ['modal'] });
  registry.push(overlay);
  observers[0]([{ type: 'attributes', target: overlay }]);
  assert.equal(animations.length, 0, '닫혀 있으면 아무것도 하지 않는다');
  overlay.classes.add('open');
  observers[0]([{ type: 'attributes', target: overlay }, { type: 'attributes', target: overlay }, { type: 'attributes', target: overlay }]);
  assert.equal(animations.length, 2, '배경 페이드 1회 + 패널 spring 1회');
});

test('팝업이 아닌 요소의 속성 변경은 선택자 비교를 한 번으로 끝낸다', async () => {
  reset();
  const bar = new FakeElement({ classes: ['chart-bar'] });
  const before = bar.matchCalls;
  observers[0]([{ type: 'attributes', target: bar }, { type: 'attributes', target: bar }]);
  assert.equal(animations.length, 0);
  assert.ok(bar.matchCalls - before <= 1, `팝업 목록 6개를 매번 비교하지 않는다 (비교 ${bar.matchCalls - before}회)`);
});

(async () => {
  await import(pathToFileURL(path.resolve(__dirname, '..', '..', 'frontend', 'js', 'ui', 'motion.js')).href);
  await tick(); await tick();
  assert.equal(observers.length, 1, 'motion.js가 MutationObserver를 한 개 설치한다');

  let failed = 0;
  for (const { name, fn } of tests) {
    try { await fn(); console.log(`PASS ${name}`); } catch (error) { failed += 1; console.log(`FAIL ${name}\n     ${String(error.message).split('\n')[0]}`); }
  }
  if (failed) { console.log(`\n${failed}/${tests.length} 실패`); process.exit(1); }
  console.log(`\nmotion.js 검증 통과 (${tests.length}/${tests.length})`);
})();
