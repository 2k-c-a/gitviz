import './profile';
/** Actual sandbox pages with an in-process server transport fixture. No external OAuth/API. */
import assert from 'node:assert/strict';
import { app } from 'electron';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { DesktopAuth } from '../src/auth';
import { Remote, type Http } from '../src/remote';
import { RemoteContracts } from '../src/remote-contracts';
import { registerDesktop } from '../src/desktop-ipc';
import { registerScheme, serveAssets } from '../src/app-protocol';
import { createWindow } from '../src/window';
import { registerHealth } from '../src/ipc';
import { unavailable } from '../contracts/workspace';

registerScheme(); app.on('window-all-closed', () => {});
let phase = 'startup';
const deadline = setTimeout(() => { process.stderr.write(`FAIL Phase 3 renderer deadline (${phase})\n`); app.exit(1); }, 90000);
const uid = '11111111-1111-4111-8111-111111111111';
const token = 'f'.repeat(64);
const user = { id: uid, github_login: 'fixture', display_name: 'P03 Fixture', avatar_url: null, email: null };
const issued = { sessionToken: token, sessionId: uid, user, expiresAt: new Date(Date.now() + 300000).toISOString(), capabilities: ['remote-v1'], apiCapabilities: { desktopProtocol: 1, aiContract: '1.9' } };
const snapshot = { id: 7, repository_id: 2, target_sha: 'a'.repeat(40), payload_sha256: 'b'.repeat(64), formula_version: 'v1', window_start: '2026-01-01T00:00:00Z', window_end: '2026-06-01T00:00:00Z', analyzed_at: '2026-06-01T00:00:00Z' };
const repo = { id: 2, url: 'https://github.com/fixture/p03', name: 'P03 Fixture', status: 'done', default_branch: 'main', last_analyzed_at: snapshot.analyzed_at, latest_snapshot_id: 7 };
const requests: string[] = [];
const requestMethods: string[] = [];
const unauthorized = false;
const inputId = '22222222-2222-4222-8222-222222222222', jobId = '33333333-3333-4333-8333-333333333333', resultId = '44444444-4444-4444-8444-444444444444', sourceId = '55555555-5555-4555-8555-555555555555';
const model = { provider: 'openai', profile_id: 'fixture-v1', model: 'fixture-model', display_name: 'Fixture model', allowed: true, execution_location: 'external', requires_api_key: true, input_price_per_mtok: '1', output_price_per_mtok: '1', max_input_tokens: 10000, max_output_tokens: 1000 };
let credential: { provider: string; key_hint: string; version: number; preferred_profile_id: string; validated_at: string; in_flight_jobs: number } | null = null;
let inputSource: 'commit' | 'pr' = 'commit';
let typeFailure = false;
const files = Array.from({ length: 52 }, (_, i) => ({ file_id: 999 + i, path: i ? `src/module-${i}.ts` : 'src/fixture.ts', name: i ? `module-${i}.ts` : 'fixture.ts', observation_count: 100 - i, maintenance_priority: 0.5, risk_score: 0.5, evidence_status: 'observed', bus_factor: 1, coupling_score: 0.5, coupling_support: 3, coupled_file_count: 1 }));
const preview = () => ({ input_id: inputId, repository_id: 2, source_type: inputSource, pr_number: inputSource === 'pr' ? 1 : null, pr_title: 'Fixture change', base_sha: 'b'.repeat(40), head_sha: 'a'.repeat(40), status: 'ready', execution_valid: true, reviewable: true, manifest_hash: 'c'.repeat(64), policy_version: 'code-transfer-v1', files: [{ file_id: sourceId, path: 'src/fixture.ts', content_state: 'read', change_type: 'modified', byte_count: 50, hunks: [] }], coverage: { pr_total_files: 1, selected_files: 1, inventory_coverage: 'complete', selected_content_coverage: 'complete', analysis_coverage: 'complete', limitations: [] }, transfer: { ...model, external_transfer_allowed: true, credential_configured: !!credential, credential_version: credential?.version || 1, key_hint: 'fixture-masked' }, token_estimate: { input_tokens: 20, max_output_tokens: 100, within_limit: true, estimated_max_cost: '0.01' } });

const http: Http = async (path, method, _body, givenToken) => {
  requests.push(path); assert.equal(givenToken, token);
  requestMethods.push(method);
  if (unauthorized) return { status: 401, body: { detail: 'fixture signed out' }, headers: {} };
  const payload = _body ? JSON.parse(_body) as Record<string, unknown> : {};
  const url = new URL(path, 'https://fixture.test'); const p = url.pathname;
  let body: unknown = {};
  if (p === '/api/desktop/v1/session') body = issued;
  else if (p === '/api/auth/me') body = user;
  else if (p === '/repositories' && method === 'POST') body = { repository_id: 2 };
  else if (p === '/repositories/2/status') { await new Promise(resolve => setTimeout(resolve, 100)); body = { repository_id: 2, status: 'done', snapshot_id: 7, commit_count: 1, pr_count: 1 }; }
  else if (p === '/repositories' || p === '/repositories/mine') body = [repo];
  else if (p === '/repositories/2') body = repo;
  else if (p.endsWith('/analytics/snapshot')) body = { snapshot, data_quality: { commit_count: 1, contributor_count: 1 } };
  else if (p.endsWith('/summary')) body = { repository: repo, total_commits: 1, total_contributors: 1, commits_30d: 1, active_contributors_90d: 1, total_releases: 0, bugfix_pct: 0 };
  else if (p.endsWith('/timeline')) body = { months: ['2026-05'], commits: [1], active_contributors: [1], bugfix_pct: [0] };
  else if (p.endsWith('/commit-types') && typeFailure) return { status: 503, body: { detail: 'fixture temporary error' }, headers: {} };
  else if (p.endsWith('/commit-types')) body = { types: [{ type: 'feature', count: 1, pct: 100 }] };
  else if (p.endsWith('/contributor-heatmap')) body = { weeks: [], contributors: [] };
  else if (p.endsWith('/top-contributors')) body = { contributors: [{ id: 1, github_login: 'Fixture', commit_count: 1 }] };
  else if (p.endsWith('/releases')) body = { releases: [] };
  else if (p.endsWith('/network')) body = { nodes: [{ id: 1, name: 'Fixture', commit_count: 1 }, { id: 2, name: 'Reviewer', commit_count: 1 }], edges: [{ source: 1, target: 2, edge_weight: 0.8 }], metrics: { total_contributor_count: 1, total_edge_count: 0 }, data_quality: {} };
  else if (p.endsWith('/top-pairs')) body = [];
  else if (p.endsWith('/tree')) body = { tree: { name: 'root', children: files }, files, data_quality: { commit_count: 1 } };
  else if (p.endsWith('/owned-files')) body = { contributor: { id: 1, name: 'Fixture' }, owned_file_count: 1, files: [{ file_id: 999, path: 'src/fixture.ts', share: 1 }] };
  else if (p.endsWith('/collaborators')) body = { contributor: { id: 1, name: 'Fixture' }, collaborators: [] };
  else if (/\/files\/(998|999)\/detail$/.test(p)) {
    const slow = p.includes('/998/'); if (slow) await new Promise(resolve => setTimeout(resolve, 200));
    body = { file: { id: slow ? 998 : 999, path: slow ? 'src/older.ts' : 'src/fixture.ts' }, editors: [{ contributor_id: 1, name: 'Fixture', share: 1 }], coupled_files: [], co_change_unit: 'pr', maintenance_priority: 0.5, evidence_status: 'observed', change_freq: 10, churn: 80, bugfix_count: 1, observation_count: 10 };
  }
  else if (p.endsWith('/coupling')) body = { meta: { co_change_unit: 'pr', sort_by: 'coupling', total_pairs: 1 }, pairs: [{ file_a_id: 999, file_b_id: 1000, file_a_path: 'src/fixture.ts', file_b_path: 'src/module-1.ts', co_change_count: 3, change_count_a: 4, change_count_b: 6, a_to_b_ratio: 0.75, b_to_a_ratio: 0.5, coupling_score: 0.75, adjusted_score: 0.6, low_support: true }] };
  else if (p.endsWith('/risk-scores') || p.endsWith('/bus-factor') || p.endsWith('/coupling')) body = [];
  else if (p.endsWith('/snapshots')) body = { repository_id: 2, snapshots: [snapshot], next_before_id: null };
  else if (p === '/api/ai/v1/providers') body = { providers: [{ id: 'openai', display_name: 'Fixture provider', key_format: 'Fixture key', models: [model] }], external_transfer_allowed: true };
  else if (p === '/api/ai/v1/credentials') body = { credentials: credential ? [credential] : [] };
  else if (p === '/api/ai/v1/credentials/openai') {
    if (method === 'PUT') { assert.equal(payload.api_key, 'fixture-test-value'); credential = { provider: 'openai', key_hint: 'fixture-masked', version: (credential?.version || 0) + 1, preferred_profile_id: 'fixture-v1', validated_at: snapshot.analyzed_at, in_flight_jobs: 0 }; body = { changed: true, credential }; }
    else if (method === 'DELETE') { credential = null; body = { jobs_cancelled: 0 }; }
    else body = { credential };
  }
  else if (p === '/api/ai/v1/pulls') body = { items: [{ number: 1, title: 'Fixture PR', head_sha: 'a'.repeat(40), base_ref: 'main', head_ref: 'work' }], next_cursor: null };
  else if (p === '/api/ai/v1/commits') body = { items: [{ sha: 'a'.repeat(40), message: 'Fixture commit', committed_at: snapshot.analyzed_at }], next_cursor: null };
  else if (p.endsWith('/files') && (p.includes('/commits/') || p.includes('/pulls/'))) body = { base_sha: 'b'.repeat(40), head_sha: 'a'.repeat(40), pr_total_files: 1, inventory_coverage: 'complete', next_cursor: null, files: [{ path: 'src/fixture.ts', change_type: 'modified', has_patch: true, additions: 1, deletions: 1 }] };
  else if (p === '/api/ai/v1/inputs' && method === 'POST') { inputSource = payload.commit_sha ? 'commit' : 'pr'; body = { input_id: inputId, repository_id: 2 }; }
  else if (p === `/api/ai/v1/inputs/${inputId}`) body = preview();
  else if (p === `/api/ai/v1/inputs/${inputId}/files/${sourceId}`) body = { path: 'src/fixture.ts', lines: [{ kind: 'added', right_line: 1, left_line: null, text: 'return value;' }] };
  else if (p === '/api/ai/v1/jobs' && method === 'POST') { assert.equal((payload.consent as Record<string, unknown>).manifest_hash, 'c'.repeat(64)); body = { job_id: jobId, repository_id: 2 }; }
  else if (p === '/api/ai/v1/jobs') body = { jobs: [], next_cursor: null };
  else if (p === `/api/ai/v1/jobs/${jobId}`) body = { job_id: jobId, repository_id: 2, result_id: resultId, input_id: inputId, status: 'succeeded', kind: 'review', created_at: snapshot.analyzed_at };
  else if (p === `/api/ai/v1/results/${resultId}`) body = { result_id: resultId, job_id: jobId, repository_id: 2, input_id: inputId, kind: 'review', finding_count: 2, human_validation: 'unverified', coverage: preview().coverage };
  else if (p === `/api/ai/v1/results/${resultId}/findings`) body = { findings: [1, 2].map(i => ({ finding_id: `${i}6666666-6666-4666-8666-666666666666`, source_file_id: sourceId, finding: { ordinal: i, title: `Finding ${i}`, priority: i, category: 'correctness', human_validation: 'unverified', location: { path: i === 1 ? 'src/fixture.ts' : 'src/other.ts', side: 'RIGHT', start_line: 1, end_line: 1 }, condition: 'Fixture condition', impact: 'Fixture impact', verification: 'Inspect source' } })), next_cursor: null };
  else if (p === `/api/ai/v1/results/${resultId}/history`) body = { evidence: [] };
  else if (p === '/api/ai/v1/history/snapshots') body = { snapshots: [] };
  else if (p === '/api/ai/v1/shared/results') body = { results: [], next_cursor: null };

  else if (p === '/api/ai/v1/usage') body = { period_start: '2026-10-01', currency: 'USD', user_spent: '0', user_reserved: '0', user_limit: '20', user_remaining: '20', server_budget_enabled: true, uncertain_reservations: 0, daily_jobs_used: 0, daily_job_limit: 30 };
  else if (p === '/search/github-repositories') body = { query: 'fixture', total_count: 1, results: [{ owner: 'fixture', repo_name: 'p03', url: repo.url, description: 'Search fixture', stars: 1, forks: 0, is_registered: true }] };
  return { status: 200, body, headers: {} };
};

void app.whenReady().then(async () => {
  await serveAssets(join(__dirname, '../ui'));
  const contracts = new RemoteContracts(JSON.parse(readFileSync(join(__dirname, '../schema/ai-bodies.json'), 'utf8')) as Record<string, Record<string, unknown>>);
  const auth = new DesktopAuth('https://fixture.test', http, async () => { throw new Error('UNEXPECTED_EXTERNAL_BROWSER'); }, { read: async () => issued, write: async () => {} });
  await auth.restore();
  const remote = new Remote(contracts, http, auth.identity, auth.invalidate);
  const window = createWindow(false); const unregister = registerDesktop(window, auth, remote);
  window.webContents.setBackgroundThrottling(false);
  const unregisterHealth = registerHealth(window, unavailable);
  const errors: string[] = [];
  window.webContents.on('console-message', details => { if (details.level === 'error') errors.push(details.message.replace(/[A-Za-z]:[^\n]*/g, '[path]')); });
  async function wait(expression: string): Promise<void> {
    for (let i = 0; i < 100; i++) {
      if (await window.webContents.executeJavaScript(expression)) return;
      await new Promise(resolve => setTimeout(resolve, 30));
    }
    throw new Error(`ASSERTION:${phase}:${expression.slice(0, 130)}`);
  }
  async function clickText(text: string) {
    await window.webContents.executeJavaScript(`(() => { const button = [...document.querySelectorAll('button,a')].find(e => e.textContent.trim() === ${JSON.stringify(text)} && e.getClientRects().length); if(!button) throw new Error('missing action'); button.click(); })()`);
  }
  try {
    phase = 'search-to-overview';
    await window.loadURL('gitviz://app/web/pages/search.html');
    await wait(`!!document.querySelector('.app-shell') && document.getElementById('nav-username')?.textContent==='fixture'`);
    await window.webContents.executeJavaScript(`document.getElementById('search-input').value='fixture'; document.getElementById('search-btn').click()`);
    await wait(`!!document.querySelector('.search-select-btn')`);
    await window.webContents.executeJavaScript(`document.querySelector('.search-select-btn').click()`);
    await clickText('저장된 결과 확인');
    await wait(`location.pathname.endsWith('/overview.html') && document.getElementById('overview-files')?.textContent.includes('src/fixture.ts') && !!document.querySelector('#overview-trend canvas')`);
    assert.equal(requestMethods.some(method => method !== 'GET'), false);
    assert.equal(requests.filter(path => path.includes('/analytics/snapshot')).length, 1, 'snapshot metadata coalesced');
    phase = 'explicit-analysis-start-to-result';
    await window.loadURL('gitviz://app/web/pages/search.html?q=fixture');
    await wait(`!!document.querySelector('.search-select-btn') && document.getElementById('nav-username')?.textContent==='fixture'`);
    await window.webContents.executeJavaScript(`document.querySelector('.search-select-btn').click()`);
    await clickText('새 분석 요청');
    await wait(`document.getElementById('job-modal').classList.contains('open')`);
    await wait(`document.getElementById('modal-title').textContent==='분석 완료'`);
    await clickText('결과 보기 →');
    await wait(`location.pathname.endsWith('/overview.html') && location.search.includes('snapshot=7') && !!document.querySelector('#overview-trend canvas')`);
    assert.equal(requestMethods.filter(method => method === 'POST').length, 1, 'one explicit analysis request');
    phase = 'partial-panel-retry'; typeFailure = true;
    await window.loadURL('gitviz://app/web/pages/overview.html?repo=2&snapshot=7');
    await wait(`!!document.querySelector('#overview-types .ui-state-error') && !!document.querySelector('#overview-trend canvas')`);
    typeFailure = false;
    await window.webContents.executeJavaScript(`document.querySelector('#overview-types button').click()`);
    await wait(`!!document.querySelector('#overview-types progress')`);
    phase = 'contributor-file-back';
    await window.loadURL('gitviz://app/web/pages/timeline.html?repo=2&snapshot=7');
    await wait(`!!document.querySelector('#tl-contrib-list button') && !!Chart.getChart('chart-timeline')`);
    await window.webContents.executeJavaScript(`document.querySelector('#tl-contrib-list button').click()`);
    await wait(`document.getElementById('dd-title').textContent==='Fixture' && document.getElementById('dd-body').textContent.includes('src/fixture.ts')`);
    await window.webContents.executeJavaScript(`document.querySelector('[data-action="__ddPushFile"]').click()`);
    await wait(`document.getElementById('dd-title').textContent==='fixture.ts'`);
    await window.webContents.executeJavaScript(`document.getElementById('dd-back-btn').click()`);
    await wait(`document.getElementById('dd-title').textContent==='Fixture'`);
    await window.webContents.executeJavaScript(`window.closeDrilldown(); const metric=document.querySelector('[aria-label="월별 지표"]'); metric.value='bugfix_pct'; metric.dispatchEvent(new Event('change'));`);
    assert.equal(await window.webContents.executeJavaScript(`Chart.getChart('chart-timeline').options.scales.y.max`), 100);
    await window.webContents.executeJavaScript(`document.querySelector('.analysis-list button').click()`);
    await wait(`document.querySelector('[role="status"]').textContent.includes('2026-05') || [...document.querySelectorAll('[role="status"]')].some(e=>e.textContent.includes('2026-05'))`);
    phase = 'network-interactions';
    await window.loadURL('gitviz://app/web/pages/network.html?repo=2&snapshot=7');
    await wait(`document.querySelectorAll('#net-node-list button').length===2`);
    await clickText('확대'); assert.ok(await window.webContents.executeJavaScript(`document.querySelector('#net-graph-svg').__zoom.k>1`));
    await window.webContents.executeJavaScript(`const search=document.getElementById('net-search');search.value='Fixture';search.dispatchEvent(new Event('input'));document.querySelector('#net-node-list button').click()`);
    await wait(`document.getElementById('dd-title').textContent==='Fixture'`);
    assert.equal(await window.webContents.executeJavaScript(`document.querySelectorAll('#net-graph-svg [aria-pressed="true"]').length`), 1);
    await window.webContents.executeJavaScript(`window.closeDrilldown();document.getElementById('net-limit').value='25'`); await clickText('필터 적용');
    await wait(`document.getElementById('net-status').textContent==='결과 조회 완료'`);
    assert.ok(requests.some(path => path.includes('limit_nodes=25')));
    phase = 'file-search-pagination-views';
    await window.loadURL('gitviz://app/web/pages/code-analysis.html?repo=2&snapshot=7');
    await wait(`document.querySelectorAll('#cx-list-tbody tr').length===50 && !!document.getElementById('cx-list-next')`);
    await window.webContents.executeJavaScript(`document.getElementById('cx-list-next').click()`);
    assert.equal(await window.webContents.executeJavaScript(`document.querySelectorAll('#cx-list-tbody tr').length`), 2);
    await window.webContents.executeJavaScript(`const search=document.getElementById('cx-search');search.value='fixture.ts';search.dispatchEvent(new Event('input'));document.querySelector('.cx-file-detail').click()`);
    await wait(`document.getElementById('dd-title').textContent==='fixture.ts'`);
    await window.webContents.executeJavaScript(`window.closeDrilldown(); document.querySelector('[data-view="tree"]').click()`);
    await wait(`!document.getElementById('cx-svg').closest('.panel').hidden && document.getElementById('cx-list-tbody').closest('.panel').hidden`);
    await window.reload();
    await wait(`document.getElementById('cx-search')?.value==='fixture.ts' && !document.getElementById('cx-svg').closest('.panel').hidden`);
    await window.webContents.executeJavaScript(`document.querySelector('[data-view="coupling"]').click()`);
    await wait(`!!document.querySelector('#cx-cp-body .cx-pair-file')`);
    assert.ok(await window.webContents.executeJavaScript(`document.getElementById('cx-cp-body').textContent.includes('3회') && document.getElementById('cx-cp-body').textContent.includes('75')`));
    await window.webContents.executeJavaScript(`document.querySelector('#cx-cp-body .cx-pair-file').click()`);
    await wait(`document.getElementById('dd-title').textContent==='fixture.ts'`);
    await window.webContents.executeJavaScript(`window.closeDrilldown()`);
    phase = 'settings-key-save';
    await window.loadURL('gitviz://app/web/pages/settings.html');
    await wait(`!!document.getElementById('ai-key-input') && !!document.querySelector('.app-shell')`);
    await window.webContents.executeJavaScript(`document.getElementById('ai-key-input').value='fixture-test-value'`); await clickText('key 확인 후 저장');
    await wait(`document.getElementById('ai-keys').textContent.includes('key 등록됨')`);
    assert.equal(await window.webContents.executeJavaScript(`document.getElementById('ai-key-input').value`), '');
    assert.equal(await window.webContents.executeJavaScript(`document.body.textContent.includes('fixture-test-value') || JSON.stringify(localStorage).includes('fixture-test-value')`), false);
    phase = 'AI-commit-preview-consent-result';
    await window.loadURL('gitviz://app/web/pages/ai-review.html?repo=2');
    await wait(`document.getElementById('ai-main')?.textContent.includes('Fixture PR')`);
    await clickText('커밋'); await wait(`document.getElementById('ai-main').textContent.includes('Fixture commit')`);
    await clickText('파일 선택');
    await wait(`document.querySelectorAll('.ai-file input[type="checkbox"]').length>0`);
    await window.webContents.executeJavaScript(`document.querySelector('.ai-file input[type="checkbox"]').click()`); await clickText('미리보기 만들기');
    await wait(`!!document.getElementById('ai-consent-check')`);
    assert.equal(await window.webContents.executeJavaScript(`[...document.querySelectorAll('button')].find(e=>e.textContent==='코드 리뷰 생성').disabled`), true);
    await clickText('API key·설정');
    await wait(`location.pathname.endsWith('/settings.html') && !!document.getElementById('ai-key-input')`);
    await clickText('AI 리뷰로 돌아가기'); await wait(`!!document.getElementById('ai-consent-check')`);
    assert.equal(await window.webContents.executeJavaScript(`document.getElementById('ai-consent-check').checked`), false, 'return requires fresh consent');
    await window.webContents.executeJavaScript(`document.getElementById('ai-consent-check').click()`); await clickText('코드 리뷰 생성');
    await wait(`document.querySelectorAll('.ai-finding').length===2`);
    await window.webContents.executeJavaScript(`const select=document.querySelector('[aria-label="검토 순서 필터"]');select.value='1';select.dispatchEvent(new Event('change'))`);
    assert.equal(await window.webContents.executeJavaScript(`document.querySelectorAll('.ai-finding').length`), 1);
    await window.webContents.executeJavaScript(`(() => { const note=document.querySelector('.ai-finding textarea'); note.value='Unsaved fixture note'; note.dispatchEvent(new Event('input')); const search=document.querySelector('[aria-label="불러온 지적의 파일 또는 제목 검색"]'); search.focus(); search.value='fixture'; search.dispatchEvent(new Event('input')); })()`);
    assert.equal(await window.webContents.executeJavaScript(`document.activeElement.getAttribute('aria-label')`), '불러온 지적의 파일 또는 제목 검색', 'filter typing retains focus');
    await window.webContents.executeJavaScript(`(() => { const search=document.querySelector('[aria-label="불러온 지적의 파일 또는 제목 검색"]'); search.value=''; search.dispatchEvent(new Event('input')); })()`);
    assert.equal(await window.webContents.executeJavaScript(`document.querySelector('.ai-finding textarea').value`), 'Unsaved fixture note', 'filtering retains feedback draft');
    await clickText('근거 코드 보기'); await wait(`document.querySelector('.ai-code').textContent.includes('return value;')`);
    phase = 'AI-PR-files';
    await window.loadURL('gitviz://app/web/pages/ai-review.html?repo=2');
    await wait(`document.getElementById('ai-main')?.textContent.includes('Fixture PR')`); await clickText('파일 선택');
    await wait(`document.querySelectorAll('.ai-file input[type="checkbox"]').length>0`);
    assert.ok(requests.some(path => path.includes('/pulls/1/files')));
    phase = 'key-replace-delete';
    await window.loadURL('gitviz://app/web/pages/settings.html');
    await wait(`!!document.getElementById('ai-key-input') && document.getElementById('ai-keys').textContent.includes('key 등록됨')`);
    await window.webContents.executeJavaScript(`document.getElementById('ai-key-input').value='fixture-test-value'`); await clickText('key 교체(확인 후 저장)');
    await wait(`document.getElementById('ai-keys').textContent.includes('v2')`);
    await window.webContents.executeJavaScript(`window.confirm=()=>true;void 0`); await clickText('key 삭제');
    await wait(`document.getElementById('ai-keys').textContent.includes('key 없음')`);
    assert.equal(credential, null);
    assert.equal(errors.length, 0, `renderer errors: ${errors.join(',')}`);
    process.stdout.write('PASS Phase 3 renderer: search/selection/overview, partial retry, contributor-file-back, monthly metric, graph zoom/filter/selection, file pagination/search/view restoration, credential save/erase, commit/PR selection, preview/fresh consent, review result/filter/code. Fixture transport only.\n');
  } finally { unregisterHealth(); unregister(); window.destroy(); clearTimeout(deadline); }
}).then(() => app.exit(0)).catch(error => {
  if (process.env.GITVIZ_UI_REPORT) writeFileSync(process.env.GITVIZ_UI_REPORT, JSON.stringify({ status: 'failed', phase, message: error instanceof Error ? error.message : 'assertion' }));
  process.stderr.write(`FAIL Phase 3 renderer (${phase}): ${error instanceof Error ? error.message.slice(0, 160) : 'assertion'}\n`); app.exit(1);
});
