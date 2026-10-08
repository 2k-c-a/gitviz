// node scripts/test_ai_team_frontend.cjs — DOM·서버 없이 팀 화면의 순수 모듈 검증
// (주소 · 역할 표시 · 오류·차단 문구 · 공유 후보 · API 클라이언트 · 화면 코드의 안전한 렌더링)
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

test('주소: team·report만 읽고 올바르지 않은 값은 버린다', async () => {
  const TS = await js('ai-team-state.js');
  assert.deepEqual(TS.parseRoute(`?team=${U1}&report=${U2}`), { team: U1, report: U2, invalid: [] });
  assert.deepEqual(TS.parseRoute('?team=x&report=y'), { team: null, report: null, invalid: ['team'] });
  assert.deepEqual(TS.parseRoute(`?team=${U1}&report=zzz`), { team: U1, report: null, invalid: ['report'] });
  assert.equal(TS.parseRoute(`?report=${U2}`).report, null);                         // 팀 없이 보고서만 있는 주소는 쓰지 않는다
  assert.equal(TS.buildRoute({ team: U1, report: U2 }), `?team=${U1}&report=${U2}`);
  assert.equal(TS.buildRoute({ report: U2 }), '');
  assert.equal(TS.buildRoute({}), '');
});

test('역할 표시: 조회자는 공유·생성할 수 없고 관리는 관리자만(안내일 뿐 서버가 판단한다)', async () => {
  const TS = await js('ai-team-state.js');
  const matrix = { admin: [true, true, true, true], member: [true, true, true, false], viewer: [true, false, false, false] };
  for (const [role, [view, share, gen, manage]] of Object.entries(matrix)) {
    assert.deepEqual([TS.can(role, 'view'), TS.can(role, 'share'), TS.can(role, 'generate_report'), TS.can(role, 'manage')], [view, share, gen, manage], role);
  }
  assert.equal(TS.can('owner', 'view'), false);
  assert.equal(TS.can('admin', 'unknown_action'), false);
  assert.deepEqual(Object.keys(TS.ROLE_LABEL), ['admin', 'member', 'viewer']);
});

test('오류 안내: 권한 부족·차단 이유·마지막 관리자·공유 변경', async () => {
  const TS = await js('ai-team-state.js');
  const denied = TS.describeTeamError({ code: 'PERMISSION_DENIED', status: 403, details: { required_role: 'admin', role: 'viewer' } });
  assert.match(denied.body, /관리자 이상/);
  assert.match(denied.body, /조회자/);
  const blocked = TS.describeTeamError({ code: 'REPORT_BLOCKED', status: 403, details: { reasons: ['visibility_changed', 'repository_access_lost'] } });
  assert.equal(blocked.blocked, true);
  assert.match(blocked.body, /다시 생성/);
  assert.match(blocked.body, /접근할 수 없는 곳/);
  assert.deepEqual(blocked.reasons, ['visibility_changed', 'repository_access_lost']);
  assert.match(TS.describeTeamError({ code: 'VERSION_CONFLICT', details: { reason: 'LAST_ADMIN' } }).title, /관리자가 한 명 이상/);
  assert.match(TS.describeTeamError({ code: 'VERSION_CONFLICT', details: { reason: 'VISIBILITY_CHANGED' } }).title, /범위가 바뀌었습니다/);
  assert.match(TS.describeTeamError({ code: 'INVALID_STATE', details: { reason: 'share_outdated' } }).body, /작성자가 다시 공유/);
  assert.equal(TS.describeTeamError({ code: 'RESOURCE_NOT_FOUND', status: 404 }).notFound, true);
  assert.equal(TS.describeTeamError({ code: 'USER_LIMIT', status: 429 }), null);      // 그 밖의 오류는 공통 안내가 맡는다
  assert.equal(TS.blockLines(['unknown_reason']).length, 1);
  assert.equal(TS.staleLines(['feedback_changed']).length, 1);
});

test('문구: 판정은 공유한 사람의 판단이고 오탐은 집계에서 제외, 확정·안전 표현을 쓰지 않는다', async () => {
  const TS = await js('ai-team-state.js');
  const S = await js('ai-state.js');
  assert.match(TS.VERDICT_LABEL.valid, /공유한 사람의 판정/);
  assert.match(TS.VERDICT_LABEL.false_positive, /집계 제외/);
  assert.match(TS.VERDICT_LABEL.unverified, /검토 대기/);
  assert.match(TS.REPORT_NOTE, /유효로 판정한 지적만 세고/);
  assert.match(TS.REPORT_NOTE, /합치지 않습니다/);
  assert.match(TS.SHARE_NOTE, /코드 원문·인용과 내 메모는 공유하지 않으며/);
  assert.match(TS.SHARE_NOTE, /언제든 공유를 해제/);
  assert.equal(TS.problemSummary({ valid: 2, unverified: 3, uncertain: 1, false_positive: 4 }), '문제 집계(유효로 판정): 2 · 검토 대기: 3 · 불확실: 1 · 오탐(집계 제외): 4');
  assert.equal(TS.problemSummary(undefined), '문제 집계(유효로 판정): 0 · 검토 대기: 0 · 불확실: 0 · 오탐(집계 제외): 0');
  const all = [...Object.values(TS.VERDICT_LABEL), ...Object.values(TS.BLOCK_TEXT), ...Object.values(TS.STALE_TEXT), ...Object.values(TS.PERMISSION_NOTE),
    ...Object.values(TS.SHARE_STATUS_LABEL), TS.REPORT_NOTE, TS.SHARE_NOTE];
  for (const text of all) for (const phrase of S.FORBIDDEN_PHRASES) assert.ok(!text.includes(phrase), `${phrase}: ${text}`);
});

test('공유 후보: 내 성공한 같은 저장소의 리뷰와 인계 대상만, 이미 공유한 것은 표시', async () => {
  const TS = await js('ai-team-state.js');
  const jobs = [{ kind: 'review', status: 'succeeded', result_id: U1, repository_id: 7, created_at: 'a' }, { kind: 'summary', status: 'succeeded', result_id: U2, repository_id: 7 },
    { kind: 'review', status: 'failed', result_id: U2, repository_id: 7 }, { kind: 'review', status: 'succeeded', result_id: U2, repository_id: 8 },
    { kind: 'review', status: 'succeeded', result_id: null, repository_id: 7 }];
  const targets = [{ target_id: U2, repository_id: 7, name: '로그인 인계' }, { target_id: U1, repository_id: 8, name: '다른 저장소' }];
  const shares = [{ resource_id: U1 }];
  const out = TS.shareCandidates({ jobs, targets, shares, repositoryId: 7 });
  assert.deepEqual(out.map((c) => [c.type, c.id, c.shared]), [['review_result', U1, true], ['handover_target', U2, false]]);
  assert.deepEqual(TS.shareCandidates({ jobs: undefined, targets: undefined, shares: undefined, repositoryId: 7 }), []);
});

test('API 클라이언트: 팀 요청의 주소·방법·CSRF와 오류 코드', async () => {
  const { createAiClient } = await js('ai-api.js');
  const calls = [];
  const fetchImpl = async (u, o = {}) => {
    const url = String(u);
    calls.push({ url, method: o.method || 'GET', headers: o.headers || {}, body: o.body });
    if (url === '/api/auth/csrf') return { ok: true, status: 200, json: async () => ({ token: 'tok' }) };
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  const api = createAiClient({ fetchImpl });
  await api.teams(); await api.createTeam('팀'); await api.team(U1); await api.renameTeam(U1, 'x'); await api.deleteTeam(U1);
  await api.teamMembers(U1); await api.inviteMember(U1, 'bo', 'member'); await api.changeMemberRole(U1, U2, 'viewer'); await api.removeMember(U1, U2); await api.acceptInvitation(U1);
  await api.teamRepositories(U1); await api.addTeamRepository(U1, 7); await api.removeTeamRepository(U1, 7);
  await api.teamShares(U1); await api.createShare(U1, 'review_result', U2); await api.deleteShare(U1, U2); await api.shareContent(U1, U2);
  await api.teamReports(U1); await api.createTeamReport(U1, [7]); await api.teamReport(U1, U2); await api.deleteTeamReport(U1, U2);
  const seen = calls.filter((c) => c.url !== '/api/auth/csrf').map((c) => `${c.method} ${c.url}`);
  const b = '/api/ai/v1/teams';
  assert.deepEqual(seen, [`GET ${b}`, `POST ${b}`, `GET ${b}/${U1}`, `PATCH ${b}/${U1}`, `DELETE ${b}/${U1}`, `GET ${b}/${U1}/members`, `POST ${b}/${U1}/members`,
    `PATCH ${b}/${U1}/members/${U2}`, `DELETE ${b}/${U1}/members/${U2}`, `POST ${b}/${U1}/invitation/accept`, `GET ${b}/${U1}/repositories`, `POST ${b}/${U1}/repositories`,
    `DELETE ${b}/${U1}/repositories/7`, `GET ${b}/${U1}/shares`, `POST ${b}/${U1}/shares`, `DELETE ${b}/${U1}/shares/${U2}`, `GET ${b}/${U1}/shares/${U2}/content`,
    `GET ${b}/${U1}/reports`, `POST ${b}/${U1}/reports`, `GET ${b}/${U1}/reports/${U2}`, `DELETE ${b}/${U1}/reports/${U2}`]);
  for (const c of calls.filter((x) => x.method !== 'GET' && x.url !== '/api/auth/csrf')) assert.equal(c.headers['X-CSRF-Token'], 'tok', c.url);
  for (const c of calls.filter((x) => x.method === 'GET')) assert.equal(c.headers['X-CSRF-Token'], undefined, c.url);
  const blocked = createAiClient({ fetchImpl: async () => ({ ok: false, status: 403, headers: { get: () => null },
    json: async () => ({ error: { code: 'REPORT_BLOCKED', message: 'x', details: { reasons: ['visibility_changed'] } } }) }) });
  await assert.rejects(blocked.teamReport(U1, U2), (e) => e.code === 'REPORT_BLOCKED' && e.status === 403 && e.details.reasons[0] === 'visibility_changed');
});

test('화면 코드: 동적 값을 HTML로 해석하지 않고, 차단된 보고서는 내용을 그리지 않는다', async () => {
  for (const file of ['ai-team.js', 'ai-team-state.js']) {
    const src = fs.readFileSync(path.join(root, file), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');   // 주석에 쓴 설명은 제외
    for (const banned of ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function', 'srcdoc']) assert.ok(!src.includes(banned), `${file}: ${banned}`);
  }
  const page = fs.readFileSync(path.join(root, '..', 'pages', 'ai-team.html'), 'utf8');
  assert.ok(!/<script(?![^>]*src=)/i.test(page), '인라인 스크립트 금지');
  const ui = fs.readFileSync(path.join(root, 'ai-team.js'), 'utf8');
  assert.ok(!/fetch\(/.test(ui), 'API 클라이언트만 사용한다');
  const open = ui.slice(ui.indexOf('async function openReport'), ui.indexOf('/* ───────────────────────── 시작'));
  assert.ok(/catch \(e\)[\s\S]*mount\(box\);[\s\S]*showError\(e\)/.test(open), '차단·오류 시 보고서 영역을 비우고 이유만 보여 준다');
  assert.ok(!/localStorage|sessionStorage|indexedDB/.test(ui), '팀 자료를 브라우저 저장소에 남기지 않는다');
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
