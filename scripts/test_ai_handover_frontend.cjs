// node scripts/test_ai_handover_frontend.cjs — DOM·서버 없이 인수인계 화면의 순수 모듈 검증
// (주소 · 경로·항목 입력 안내 · 요청 본문 · 오류·기준 변경 문구 · API 클라이언트 · 화면 코드의 안전한 렌더링)
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..', 'frontend', 'js');
const js = (f) => import(pathToFileURL(path.join(root, f)).href);
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const U1 = '11111111-1111-4111-8111-111111111111';
const U2 = '22222222-2222-4222-8222-222222222222';

test('주소: repo·target만 읽고 올바르지 않은 값은 버린다', async () => {
  const HS = await js('ai-handover-state.js');
  assert.deepEqual(HS.parseRoute(`?repo=7&target=${U1}`), { repo: 7, target: U1, invalid: [] });
  assert.deepEqual(HS.parseRoute('?repo=abc&target=x'), { repo: null, target: null, invalid: ['repo', 'target'] });
  assert.deepEqual(HS.parseRoute('?repo=0'), { repo: null, target: null, invalid: ['repo'] });
  assert.equal(HS.parseRoute(`?target=${U1}`).repo, null);
  assert.equal(HS.buildRoute({ repo: 7, target: U1 }), `?repo=7&target=${U1}`);
  assert.equal(HS.buildRoute({ target: U1 }), '');                          // 저장소 없이 대상만 있는 주소는 만들지 않는다
  assert.equal(HS.buildRoute({}), '');
});

test('경로 입력: 상대 경로만, 중복 제거, 50개 제한', async () => {
  const HS = await js('ai-handover-state.js');
  assert.deepEqual(HS.parsePaths('./a.py\r\nb\\c.py\n\na.py\n').paths, ['a.py', 'b/c.py']);
  for (const bad of ['../x', '/abs', 'a//b', 'a/./b', 'a/../b', `x${'y'.repeat(500)}`, 'a\u0000b']) {
    const r = HS.parsePaths(bad);
    assert.deepEqual(r.paths, [], bad);
    assert.ok(r.errors.length, bad);
  }
  assert.ok(HS.parsePaths('').errors.length);
  const many = Array.from({ length: 51 }, (_, i) => `f${i}.py`).join('\n');
  assert.ok(HS.parsePaths(many).errors.some((e) => /50개/.test(e)));
});

test('준비 항목: 완료 조건·미확인에 내용 금지·링크는 http(s)만', async () => {
  const HS = await js('ai-handover-state.js');
  for (const key of ['assignee', 'joint_review', 'receiver_run']) {
    assert.ok(HS.itemProblem(key, { status: 'ready', text: '', link: 'https://x.y/z' }), key);        // 내용이 꼭 필요한 항목은 링크만으로 완료할 수 없다
    assert.equal(HS.itemProblem(key, { status: 'ready', text: '내용' }), null);
  }
  for (const key of ['description', 'run_method', 'verify_method']) {
    assert.ok(HS.itemProblem(key, { status: 'ready' }), key);
    assert.equal(HS.itemProblem(key, { status: 'ready', link: 'https://x.y/z' }), null, key);
    assert.equal(HS.itemProblem(key, { status: 'ready', text: '설명' }), null, key);
  }
  assert.ok(HS.itemProblem('assignee', { status: 'unknown', text: '이름' }));                       // 미확인 항목에는 내용을 둘 수 없다
  assert.equal(HS.itemProblem('assignee', { status: 'unknown' }), null);
  assert.equal(HS.itemProblem('assignee', { status: 'needed' }), null);                            // 준비 필요는 내용 없이도 기록할 수 있다
  for (const link of ['javascript:alert(1)', 'ftp://x.y', 'http://', 'https://a b', '//evil.com', 'file:///etc/passwd', 'https://a.b/<x>']) {
    assert.ok(HS.itemProblem('description', { status: 'needed', link }), link);
  }
  assert.equal(HS.itemProblem('description', { status: 'needed', link: 'https://wiki.example/doc?a=1&b=2' }), null);
});

test('요청 본문: 여섯 항목을 모두 보내고 입력하지 않은 항목은 unknown이다', async () => {
  const HS = await js('ai-handover-state.js');
  const body = HS.buildPreparationBody({ expectedVersion: 2, targetVersion: 3, items: { assignee: { status: 'ready', text: ' 이인수 ', link: '' } } });
  assert.deepEqual(Object.keys(body.items), HS.ITEM_KEYS);
  assert.deepEqual(body.items.assignee, { status: 'ready', text: '이인수', link: null });
  for (const key of HS.ITEM_KEYS.filter((k) => k !== 'assignee')) assert.deepEqual(body.items[key], { status: 'unknown', text: null, link: null }, key);   // 미확인을 준비 필요로 바꾸지 않는다
  assert.equal(body.expected_version, 2);
  assert.equal(body.confirmed_target_version, 3);
  const view = HS.itemsFromView({ items: [{ key: 'assignee', status: 'needed', text: null, link: null }] });
  assert.equal(view.assignee.status, 'needed');
  assert.equal(view.description.status, 'unknown');
});

test('문구: 준비 완료는 항상 작성자 기록이고 미확인은 확정 문구로 바뀌지 않는다', async () => {
  const HS = await js('ai-handover-state.js');
  assert.equal(HS.STATUS_LABEL.unknown, '미확인');
  assert.equal(HS.STATUS_LABEL.needed, '준비 필요');
  assert.match(HS.STATUS_LABEL.ready, /작성자 기록/);
  assert.match(HS.READY_NOTE, /확인한 것이 아닙니다/);
  assert.match(HS.LINK_NOTE, /열거나 내용을 읽지 않습니다/);
  assert.match(HS.DRAFT_NOTE, /확정 사실이 아니며/);
  const S = await js('ai-state.js');
  for (const text of [...Object.values(HS.STATUS_LABEL), HS.READY_NOTE, HS.LINK_NOTE, HS.DRAFT_NOTE, ...Object.values(HS.STALE_TEXT), ...Object.values(HS.ITEM_HINT)]) {
    for (const phrase of S.FORBIDDEN_PHRASES) assert.ok(!text.includes(phrase), `${phrase}: ${text}`);
  }
  assert.equal(HS.preparationSummary({ items: [{ status: 'unknown' }, { status: 'unknown' }, { status: 'needed' }, { status: 'ready' }] }), '미확인 2 · 준비 필요 1 · 준비 완료(작성자 기록) 1');
});

test('오류·기준 변경 안내: 이유별 문구와 문서 저장 거절 이유', async () => {
  const HS = await js('ai-handover-state.js');
  const conflict = (reason) => HS.describeHandoverError({ code: 'VERSION_CONFLICT', status: 409, details: { reason } });
  assert.match(conflict('TARGET_CHANGED').title, /인계 대상이 바뀌었습니다/);
  assert.match(conflict('PREPARATION_VERSION').title, /준비 정보/);
  assert.equal(conflict('DOCUMENT_VERSION').action, 'reload_document');
  assert.match(conflict('DOCUMENT_VERSION').body, /내가 쓴 내용은/);                               // 충돌해도 내 편집 내용을 잃지 않는다고 안내한다
  assert.ok(conflict('SOMETHING_NEW').title);
  const issues = HS.describeHandoverError({ code: 'INVALID_SELECTION', details: { issues: ['raw_html', 'unsafe_link', 'image'] } });
  assert.match(issues.body, /HTML 태그/);
  assert.match(issues.body, /링크/);
  assert.match(issues.body, /이미지/);
  assert.match(HS.describeHandoverError({ code: 'INVALID_STATE', details: { issues: ['raw_html'] } }).title, /내보낼 수 없는/);
  assert.equal(HS.describeHandoverError({ code: 'RESOURCE_NOT_FOUND', status: 404 }), null);            // 그 밖의 오류는 공통 안내가 맡는다
  assert.deepEqual(HS.staleLines(['feedback_changed', 'target_changed']).length, 2);
  assert.match(HS.staleLines(['feedback_changed'])[0], /피드백이 이 초안을 만든 뒤 바뀌었습니다/);
  assert.match(HS.staleLines(['unknown_reason'])[0], /기준이 바뀌었습니다/);
});

test('미저장 편집: 다시 그릴 때 같은 버전이면 되살리고 문서가 바뀌면 따로 보관한다', async () => {
  const HS = await js('ai-handover-state.js');
  const doc = { version: 3, markdown: '저장된 본문' };
  assert.equal(HS.draftAction(null, doc), 'none');                                               // 입력한 것이 없다
  assert.equal(HS.draftAction({ text: '저장된 본문', version: 3 }, doc), 'none');                   // 바뀐 것이 없다
  assert.equal(HS.draftAction({ text: '쓰던 내용', version: 3 }, doc), 'restore');                  // 새 원본 생성 등: 문서는 그대로이니 입력을 되살린다
  assert.equal(HS.draftAction({ text: '쓰던 내용', version: 2 }, doc), 'lost');                     // 충돌·새 원본 채택: 문서가 바뀌었으니 내 내용을 따로 보여 준다
  assert.equal(HS.draftAction({ text: '방금 저장한 내용', version: 2 }, { version: 3, markdown: '방금 저장한 내용' }), 'none');   // 저장 성공 뒤에는 되살릴 것이 없다
  assert.equal(HS.draftAction({ text: 'x', version: 1 }, null), 'none');
});

test('보관한 편집 내용: 저장하거나 버릴 때만 줄어들고 다시 그려도 유지된다', async () => {
  const HS = await js('ai-handover-state.js');
  let list = [];
  list = HS.addLostDraft(list, '첫 번째 내용');                           // 미저장 입력 → 재생성 → 보관
  assert.deepEqual(list, ['첫 번째 내용']);
  // 화면을 다시 그리는 동안 목록은 그대로다(다시 재생성해도 사라지지 않는다): 같은 내용을 다시 보관해도 중복되지 않는다
  assert.deepEqual(HS.addLostDraft(list, '첫 번째 내용'), ['첫 번째 내용']);
  list = HS.addLostDraft(list, '두 번째 내용');                           // 이어서 또 생기면 둘 다 남는다
  assert.deepEqual(list, ['첫 번째 내용', '두 번째 내용']);
  assert.deepEqual(HS.addLostDraft(list, ''), list);                     // 빈 입력은 보관하지 않는다
  assert.deepEqual(HS.removeLostDraft(list, '첫 번째 내용'), ['두 번째 내용']);   // 명시적으로 버릴 때만 줄어든다
  assert.deepEqual(HS.removeLostDraft(list, '없는 내용'), list);
  let many = [];
  for (let i = 0; i < 8; i += 1) many = HS.addLostDraft(many, `내용 ${i}`);
  assert.deepEqual(many, ['내용 3', '내용 4', '내용 5', '내용 6', '내용 7']);       // 최근 5개
  assert.deepEqual(HS.addLostDraft(undefined, 'x'), ['x']);
  const src = fs.readFileSync(path.join(root, 'ai-handover.js'), 'utf8');
  assert.match(src, /renderLost\(\);\s*\/\/ 보관한 내용은 다시 그려도 사라지지 않는다/);   // 그릴 때마다 보관 목록을 다시 보여 준다
  assert.ok(!/lostDrafts\.clear\(/.test(src));
  assert.equal((src.match(/lostDrafts\.delete\(/g) || []).length, 2, '보관 목록을 지우는 곳은 명시적으로 버릴 때(removeLost)와 저장에 성공했을 때뿐이다');
});

test('보관 내용 경고는 모든 대상 기준이고, 준비 체크폼 입력도 다시 그릴 때 지켜진다', async () => {
  const HS = await js('ai-handover-state.js');
  assert.equal(HS.hasAnyLost(new Map()), false);
  assert.equal(HS.hasAnyLost(new Map([['t1', []]])), false);
  assert.equal(HS.hasAnyLost(new Map([['t1', ['내용']], ['t2', []]])), true);              // 현재 대상이 아니어도(목록 화면에서도) 경고한다
  assert.equal(HS.hasAnyLost(undefined), false);
  const prep = { version: 2, items: [{ key: 'assignee', status: 'ready', text: '이인수', link: null }] };
  const same = { version: 2, items: { assignee: { status: 'ready', text: '이인수', link: '' } } };
  assert.equal(HS.prepDraftAction(same, prep), 'none');                                        // 서버 내용과 같으면 되살릴 것이 없다
  const edited = { version: 2, items: { assignee: { status: 'ready', text: '박인수', link: '' } } };
  assert.equal(HS.prepDraftAction(edited, prep), 'restore');                                   // 같은 버전: 입력하던 내용을 되살린다
  assert.equal(HS.prepDraftAction({ ...edited, version: 1 }, prep), 'replaced');               // 다른 곳에서 바뀜: 최신으로 바뀌었다고 알린다
  assert.equal(HS.prepDraftAction({ version: 1, items: { assignee: { status: 'ready', text: '이인수' } } }, prep), 'none');   // 방금 저장한 내용
  assert.equal(HS.prepDraftAction(null, prep), 'none');
  const src = fs.readFileSync(path.join(root, 'ai-handover.js'), 'utf8');
  const body = src.slice(src.indexOf('async function refresh()'), src.indexOf('function restorePrepDraft'));
  assert.ok(body.indexOf('pendingDraft()') > body.indexOf('await fetchTarget('), '입력은 응답을 받은 뒤 화면을 바꾸기 직전에 확보한다(대기 중 입력 보존)');
  const between = body.slice(body.indexOf('pendingDraft()'), body.indexOf('drawTarget()'));
  assert.ok(!between.includes('await'), '확보와 화면 교체 사이에는 비동기 대기가 없어야 한다');
  const loadStart = src.indexOf('async function load()');
  const load = src.slice(loadStart, src.indexOf('route = HS.parseRoute', loadStart));
  assert.ok(load.includes('pendingDraft()') && load.includes('addLost('), '화면을 떠나기 전에 쓰던 내용을 보관한다');
});

test('초안에 쓸 결과: 성공한 같은 종류만 고른다', async () => {
  const HS = await js('ai-handover-state.js');
  const jobs = [{ kind: 'review', status: 'succeeded', result_id: U1, created_at: 'a' }, { kind: 'summary', status: 'succeeded', result_id: U2 },
    { kind: 'review', status: 'failed', result_id: U2 }, { kind: 'review', status: 'succeeded', result_id: null }, { kind: 'review', status: 'succeeded', result_id: 'not-a-uuid' }];
  assert.deepEqual(HS.resultOptions(jobs, 'review').map((o) => o.resultId), [U1]);
  assert.deepEqual(HS.resultOptions(jobs, 'summary').map((o) => o.resultId), [U2]);
  assert.equal(HS.exportUrl(U1), `/api/ai/v1/handover-documents/${U1}/export?format=markdown`);
  assert.equal(HS.exportUrl('../../x'), null);                                                      // 검증된 uuid로만 주소를 만든다
});

test('API 클라이언트: 인수인계 요청의 주소·방법·CSRF', async () => {
  const { createAiClient } = await js('ai-api.js');
  const calls = [];
  const fetchImpl = async (u, o = {}) => {
    const url = String(u);
    calls.push({ url, method: o.method || 'GET', headers: o.headers || {}, body: o.body });
    if (url === '/api/auth/csrf') return { ok: true, status: 200, json: async () => ({ token: 'tok' }) };
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  const api = createAiClient({ fetchImpl });
  await api.handoverTargets(7);
  await api.createHandoverTarget({ repository_id: 7, name: 'n', selected_paths: ['a.py'] });
  await api.handoverTarget(U1);
  await api.patchHandoverTarget(U1, { expected_version: 1, name: 'x' });
  await api.deleteHandoverTarget(U1);
  await api.preparation(U1);
  await api.savePreparation(U1, { expected_version: 0 });
  await api.generateHandover(U1, { expected_target_version: 1 });
  await api.handoverGenerations(U1);
  await api.handoverDocument(U1);
  await api.saveHandoverDocument(U2, { expected_version: 1, markdown: 'x' });
  await api.adoptHandoverGeneration(U2, { expected_version: 1, generation_id: U1 });
  const seen = calls.filter((c) => c.url !== '/api/auth/csrf').map((c) => `${c.method} ${c.url}`);
  assert.deepEqual(seen, [
    'GET /api/ai/v1/handover-targets?repository_id=7', 'POST /api/ai/v1/handover-targets', `GET /api/ai/v1/handover-targets/${U1}`,
    `PATCH /api/ai/v1/handover-targets/${U1}`, `DELETE /api/ai/v1/handover-targets/${U1}`, `GET /api/ai/v1/handover-targets/${U1}/preparation`,
    `PUT /api/ai/v1/handover-targets/${U1}/preparation`, `POST /api/ai/v1/handover-targets/${U1}/generations`, `GET /api/ai/v1/handover-targets/${U1}/generations`,
    `GET /api/ai/v1/handover-targets/${U1}/document`, `PUT /api/ai/v1/handover-documents/${U2}`, `POST /api/ai/v1/handover-documents/${U2}/adopt`]);
  for (const c of calls.filter((x) => x.method !== 'GET' && x.url !== '/api/auth/csrf')) assert.equal(c.headers['X-CSRF-Token'], 'tok', c.url);
  for (const c of calls.filter((x) => x.method === 'GET')) assert.equal(c.headers['X-CSRF-Token'], undefined, c.url);
  const conflict = createAiClient({ fetchImpl: async (u) => (String(u) === '/api/auth/csrf'
    ? { ok: true, status: 200, json: async () => ({ token: 't' }) }
    : { ok: false, status: 409, headers: { get: () => null }, json: async () => ({ error: { code: 'VERSION_CONFLICT', message: 'x', details: { reason: 'DOCUMENT_VERSION', current_version: 3 } } }) }) });
  await assert.rejects(conflict.saveHandoverDocument(U2, { expected_version: 1, markdown: 'x' }), (e) => e.code === 'VERSION_CONFLICT' && e.details.reason === 'DOCUMENT_VERSION');
});

test('화면 코드: 동적 값을 HTML로 해석하는 API를 쓰지 않는다', async () => {
  for (const file of ['ai-handover.js', 'ai-handover-state.js']) {
    const src = fs.readFileSync(path.join(root, file), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');   // 주석에 쓴 설명은 제외
    for (const banned of ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function', 'srcdoc']) {
      assert.ok(!src.includes(banned), `${file}: ${banned}`);
    }
  }
  const page = fs.readFileSync(path.join(root, '..', 'pages', 'ai-handover.html'), 'utf8');
  assert.ok(!/<script(?![^>]*src=)/i.test(page), '인라인 스크립트 금지');
  const ui = fs.readFileSync(path.join(root, 'ai-handover.js'), 'utf8');
  assert.ok(!/fetch\(/.test(ui), '링크·외부 주소를 직접 요청하지 않는다(API 클라이언트만 사용)');
  assert.ok(!/window\.open|location\.href\s*=\s*[^'"(]/.test(ui.replace(/location\.href = '\/api\/auth\/github'/g, '')), '데이터로 이동하지 않는다');
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`PASS ${name}`); }
    catch (e) { failed += 1; console.log(`FAIL ${name}\n  ${e && e.stack ? e.stack.split('\n').slice(0, 4).join('\n  ') : e}`); }
  }
  console.log(`\n${tests.length - failed}/${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
