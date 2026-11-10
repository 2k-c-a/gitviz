// node tests/frontend/test_ai_insights_frontend.cjs — DOM·서버 없이 AI 분석 화면의 API 클라이언트와 화면 규칙 검증
const path = require('node:path');
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');

const ROOT = path.resolve(__dirname, '..', '..');
const js = (f) => import(pathToFileURL(path.join(ROOT, 'frontend', 'js', f)).href);
const read = (f) => fs.readFileSync(path.join(ROOT, f), 'utf8');
const U1 = '11111111-1111-4111-8111-111111111111';

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

function recorder() {
  const calls = [];
  const fetchImpl = async (url, opts = {}) => {
    calls.push({ url: String(url), method: opts.method || 'GET', headers: opts.headers || {}, body: opts.body ? JSON.parse(opts.body) : undefined });
    if (String(url) === '/api/auth/csrf') return { ok: true, status: 200, json: async () => ({ token: 'csrf-1' }) };
    return { ok: true, status: 200, json: async () => ({ id: U1 }), headers: { get: () => null } };
  };
  return { calls, fetchImpl };
}

test('API 클라이언트: 인사이트 경로·본문·CSRF', async () => {
  const { createAiClient } = await js('ai-api.js');
  const { calls, fetchImpl } = recorder();
  const api = createAiClient({ fetchImpl, authMode: 'cookie' });
  await api.insightModels();
  await api.insightReports(7);
  await api.createInsightReport({ repositoryId: 7, snapshotId: 3, profileId: 'claude-opus-5-5-v1', provider: 'anthropic', includeCode: true });
  await api.insightReport(U1);
  await api.insightConversations(7);
  await api.createInsightConversation({ repositoryId: 7, profileId: 'claude-opus-5-5-v1' });
  await api.insightConversation(U1);
  await api.askInsight(U1, '가장 위험한 파일은?', 'anthropic');
  await api.deleteInsightConversation(U1);
  const api_calls = calls.filter((c) => c.url !== '/api/auth/csrf').slice(1);
  assert.equal(calls[0].url, '/api/ai/v1/insights/models');
  assert.deepEqual(api_calls.map((c) => `${c.method} ${c.url}`), [
    'GET /api/ai/v1/insights/reports?repository_id=7',
    'POST /api/ai/v1/insights/reports',
    `GET /api/ai/v1/insights/reports/${U1}`,
    'GET /api/ai/v1/insights/conversations?repository_id=7',
    'POST /api/ai/v1/insights/conversations',
    `GET /api/ai/v1/insights/conversations/${U1}`,
    `POST /api/ai/v1/insights/conversations/${U1}/messages`,
    `DELETE /api/ai/v1/insights/conversations/${U1}`,
  ]);
  assert.deepEqual(api_calls[1].body, { repository_id: 7, snapshot_id: 3, profile_id: 'claude-opus-5-5-v1', provider: 'anthropic', include_code: true });
  assert.deepEqual(api_calls[4].body, { repository_id: 7, snapshot_id: null, profile_id: 'claude-opus-5-5-v1', include_code: false });  // 기본은 꺼짐
  assert.deepEqual(api_calls[6].body, { question: '가장 위험한 파일은?', provider: 'anthropic' });
  for (const c of api_calls.filter((x) => x.method !== 'GET')) {
    assert.equal(c.headers['X-CSRF-Token'], 'csrf-1');
    assert.ok(!Object.keys(c.headers).some((k) => /ai-key/i.test(k)));        // 화면은 key 헤더를 만들지 않는다(앱 main이 붙인다)
  }
  assert.equal(calls.filter((c) => c.url === '/api/auth/csrf').length, 1);
});

test('앱 계약: 화면이 호출하는 인사이트 경로가 데스크톱 허용 목록에 있다', () => {
  const contracts = read('desktop/src/remote-contracts.ts');
  for (const [name, method, route] of [
    ['insightModels', 'GET', 'insights/models'],
    ['insightReports', 'GET', 'insights/reports'], ['createInsightReport', 'POST', 'insights/reports'],
    ['insightReport', 'GET', 'insights/reports/{reportId}'], ['insightConversations', 'GET', 'insights/conversations'],
    ['createInsightConversation', 'POST', 'insights/conversations'], ['insightConversation', 'GET', 'insights/conversations/{conversationId}'],
    ['askInsight', 'POST', 'insights/conversations/{conversationId}/messages'], ['deleteInsightConversation', 'DELETE', 'insights/conversations/{conversationId}'],
  ]) assert.ok(contracts.includes(`${name}: ai('${method}', '${route}'`), name);
});

test('BYOK: 앱이 key가 없다고 하면 서버 오류가 아니라 키 저장 안내를 보인다', async () => {
  const { createAiClient } = await js('ai-api.js');
  const fetchImpl = async (url) => {
    if (String(url) === '/api/auth/csrf') return { ok: true, status: 200, json: async () => ({ token: 'c' }) };
    throw new Error("Error invoking remote method 'desktop:remote': Error: AI_KEY_REQUIRED");
  };
  const api = createAiClient({ fetchImpl, authMode: 'cookie' });
  await assert.rejects(api.askInsight(U1, '질문', 'openai'), (e) => e.code === 'AI_KEY_REQUIRED' && /API 키/.test(e.message));
  const contracts = read('desktop/src/remote-contracts.ts');
  assert.match(contracts, /endpoints\.createInsightReport\.aiKey = true/);
  assert.match(contracts, /endpoints\.askInsight\.aiKey = true/);
  const source = read('frontend/js/ai-insights.js');
  assert.match(source, /desktop\?\.aiKeys\?\.status/);                  // 저장 여부(boolean)만 묻는다
  assert.match(source, /앱에서만 쓸 수 있습니다/);
  assert.doesNotMatch(source, /api\.credentials\(/);                    // 서버 key 저장소를 쓰지 않는다
});

test('화면 규칙: 데이터는 textContent로만 넣고 외부 스크립트·인라인 핸들러가 없다', () => {
  const source = read('frontend/js/ai-insights.js');
  assert.doesNotMatch(source, /\.(innerHTML|outerHTML)\s*[+]?=|insertAdjacentHTML\(|document\.write|eval\(/);
  assert.match(source, /if \(page\) main\(page\)/);                 // 자기 페이지에서만 동작
  const html = read('frontend/pages/ai-insights.html');
  assert.doesNotMatch(html, /<script[^>]+src="https?:\/\//);
  assert.doesNotMatch(html, /\bon\w+\s*=/);
  assert.match(html, /id="page-ai-insights"/);
});

test('진입점: 분석 개요의 AI 분석 버튼과 저장소 메뉴', async () => {
  const overview = read('frontend/pages/overview.html');
  assert.match(overview, /id="btn-ai-insights"[^>]*>AI 분석</);
  assert.match(read('frontend/js/overview.js'), /btn-ai-insights[\s\S]*pageUrl\('ai-insights'\)/);
  assert.match(read('frontend/js/ui/shell.js'), /'ai-insights': 'AI 분석'/);
});

test('코드 열람: 기본은 꺼져 있고 켤 때 전송 범위를 알린다', () => {
  const source = read('frontend/js/ai-insights.js');
  assert.match(source, /includeCode: false/);
  assert.match(source, /소스 코드도 읽게 하기/);
  assert.match(source, /비밀 정보가 의심되는 파일/);
  assert.match(source, /blob\/\$\{encodeURIComponent\(targetSha\)\}/);             // 코드 링크는 분석 기준 커밋으로
  assert.match(source, /b\.type === 'stat'/);
});

test('문구: 단정 표현을 쓰지 않는다', () => {
  const source = read('frontend/js/ai-insights.js');
  assert.match(source, /지적이 없다는 것이 문제가 없다는 뜻은 아닙니다/);
  assert.match(source, /공동 변경은 코드 의존성이, 유지보수 우선순위는 결함 확률이 아닙니다/);
  assert.doesNotMatch(source, /결함 확률(입니다|이다)|오류 없음/);
});

const SAMPLE_REPORT = {
  id: U1, status: 'succeeded', callCount: 7, createdAt: '2026-11-10T01:00:00Z',
  result: {
    generatedAt: '2026-11-10T01:05:00Z',
    basis: { snapshotId: 12, targetSha: 'a'.repeat(40), windowStart: '2026-05-14T00:00:00Z', windowEnd: '2026-11-10T00:00:00Z',
      formulaVersion: 'gitviz-2026.10-priority-v3', model: 'fixture-model', promptVersion: 'p1', toolVersion: 't1', code: { requested: false } },
    highlights: ['i2'],
    notes: ['근거는 GitViz 값입니다.'],
    categories: [
      { key: 'overall', label: '종합', limitations: ['표본이 적음'], insights: [
        { id: 'i1', title: '낮은 위험', summary: '요약 1', severity: 'low', confidence: 'high', evidence: [{ label: '변경', value: '3회', tool: 'getProjectOverview', toolCallId: 'c1' }] },
        { id: 'i2', title: '<script>alert(1)</script> 높은 위험 | 표', summary: '요약 *2*', severity: 'high', confidence: 'medium',
          recommendation: '테스트를 추가하세요', interpretation: '집중됨', evidence: [{ label: 'BF80', value: '1', tool: 'getOwnershipConcentration', toolCallId: 'c2' }],
          relatedCode: [{ path: 'src/a b.js', startLine: 3, endLine: 5 }], relatedCommits: ['b'.repeat(40)], relatedFiles: ['src/'], relatedContributors: ['kim'] },
        { id: 'i3', title: '보통', summary: '요약 3', severity: 'medium', confidence: 'low' },
      ] },
      { key: 'collab', label: '협업', insights: [] },
    ],
  },
};

test('자동 분석 리포트: 위험 수준→신뢰도 순 정렬과 개수', async () => {
  const R = await js('ai-insight-report.js');
  const sorted = R.sortInsights(SAMPLE_REPORT.result.categories[0].insights).map((i) => i.id);
  assert.deepEqual(sorted, ['i2', 'i3', 'i1']);
  assert.deepEqual(R.severityCounts(SAMPLE_REPORT.result.categories), { critical: 0, high: 1, medium: 1, low: 1, info: 0 });
  assert.equal(R.refCount(SAMPLE_REPORT.result.categories[0].insights[1]), 4);
  assert.ok(R.basisRows(SAMPLE_REPORT).some((r) => r.technical && r.value === '7회'));
  assert.match(R.reportFileName('owner/repo name', SAMPLE_REPORT, 'md'), /^gitviz-ai-report-owner-repo-name-\d{8}\.md$/);
});

test('보고서 내보내기: Markdown은 범주·근거·추천을 담고 특수 문자를 이스케이프한다', async () => {
  const R = await js('ai-insight-report.js');
  const md = R.toMarkdown(SAMPLE_REPORT, { repoName: 'owner/repo', githubBase: 'https://github.com/owner/repo' });
  assert.match(md, /^# owner\/repo · AI 자동 분석 보고서/);
  assert.match(md, /## 종합 \(3개\)/);
  assert.match(md, /## 협업 \(0개\)[\s\S]*지적이 없다는 것이 문제가 없다는 뜻은 아닙니다/);
  assert.match(md, /\\<script\\>/);                                    // 원문 HTML이 그대로 들어가지 않는다
  assert.match(md, /높은 위험 \\\| 표/);                               // 표 구분자 이스케이프
  assert.match(md, /> \*\*추천 조치 \(AI 의견\)\*\* 테스트를 추가하세요/);
  assert.match(md, /\(https:\/\/github\.com\/owner\/repo\/blob\/a{40}\/src\/a%20b\.js#L3-L5\)/);
  assert.ok(md.indexOf('높은 위험') < md.indexOf('### 3. 낮은 위험'));    // 위험 순으로 번호를 붙인다
});

test('보고서 내보내기: HTML은 단일 파일이고 값을 이스케이프하며 GitHub 주소만 링크한다', async () => {
  const R = await js('ai-insight-report.js');
  const html = R.toHtml(SAMPLE_REPORT, { repoName: 'owner/repo', githubBase: 'https://github.com/owner/repo' });
  assert.match(html, /^<!doctype html>/);
  assert.doesNotMatch(html, /<script/i);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(html, /Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"/);
  assert.doesNotMatch(html, /<(link|img|iframe)\b/i);
  assert.match(html, /href="https:\/\/github\.com\/owner\/repo\/commit\/b{40}"/);
  const unsafe = R.toHtml(SAMPLE_REPORT, { repoName: 'x', githubBase: 'javascript:alert(1)//github.com/a/b' });
  assert.doesNotMatch(unsafe, /href=/);                                     // 검증되지 않은 주소는 링크로 만들지 않는다
});

test('화면 규칙: 자동 분석에 요약·범주 이동·위험 필터·내보내기가 있고, 커밋·PR 화면에는 모델 선택 카드가 없다', () => {
  const source = read('frontend/js/ai-insights.js');
  assert.match(source, /보고서로 내보내기/);
  assert.match(source, /exportReport\('html'\)/);
  assert.match(source, /exportReport\('md'\)/);
  assert.match(source, /범주로 이동/);
  assert.match(source, /표시할 위험 수준/);
  const review = read('frontend/js/ai-review.js');
  assert.match(review, /if \(!settingsMode\) \{ pickDefaultProfile\(\); mount\(root\); return; \}/);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`PASS ${name}`); } catch (e) { failed += 1; console.error(`FAIL ${name}\n${e.stack}`); }
  }
  console.log(`${tests.length - failed}/${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
