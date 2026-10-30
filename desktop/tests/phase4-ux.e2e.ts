import './profile';
/** Actual sandbox pages with an in-process server transport fixture. No external OAuth/API. */
import assert from 'node:assert/strict';
import { app, BrowserWindow, type NativeImage } from 'electron';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { DesktopAuth } from '../src/auth';
import { Remote, type Http } from '../src/remote';
import { RemoteContracts } from '../src/remote-contracts';
import { registerDesktop } from '../src/desktop-ipc';
import { registerScheme, serveAssets } from '../src/app-protocol';
import { registerHealth } from '../src/ipc';
import { unavailable } from '../contracts/workspace';

app.disableHardwareAcceleration(); app.commandLine.appendSwitch('force-device-scale-factor', '1');
registerScheme(); app.on('window-all-closed', () => {});
let phase = 'startup';
const deadline = setTimeout(() => { process.stderr.write(`FAIL Phase 4 renderer deadline (${phase})\n`); app.exit(1); }, 90000);
const uid = '11111111-1111-4111-8111-111111111111';
const token = 'f'.repeat(64);
const user = { id: uid, github_login: 'fixture', display_name: 'P03 Fixture', avatar_url: null, email: null };
const issued = { sessionToken: token, sessionId: uid, user, expiresAt: new Date(Date.now() + 300000).toISOString(), capabilities: ['remote-v1'], apiCapabilities: { desktopProtocol: 1, aiContract: '1.9' } };
const snapshot = { id: 7, repository_id: 2, target_sha: 'a'.repeat(40), payload_sha256: 'b'.repeat(64), formula_version: 'v1', window_start: '2026-01-01T00:00:00Z', window_end: '2026-06-01T00:00:00Z', analyzed_at: '2026-06-01T00:00:00Z' };
const repo = { id: 2, url: 'https://github.com/fixture/p03', name: 'P03 Fixture', status: 'done', default_branch: 'main', last_analyzed_at: snapshot.analyzed_at, latest_snapshot_id: 7 };
const requests: string[] = [];
const requestMethods: string[] = [];
const unauthorized = false;
const output = join(__dirname, '../../../.validation/phase4');
const checks: { name: string; passed: boolean; detail?: unknown }[] = [];
let releaseUsage: (() => void) | null = null;
let releaseJob: (() => void) | null = null, jobViews = 0;
let usageDelay = false, invalidKey = false, stress = false, emptyData = false, emptyReview = false, richCharts = false;
let searchStatus = 200, snapshotStatus = 200, jobStatus = 'succeeded', analysisStatus = 'done';
let pageRequests = 0;
let blankSearch = false;

const inputId = '22222222-2222-4222-8222-222222222222', jobId = '33333333-3333-4333-8333-333333333333', resultId = '44444444-4444-4444-8444-444444444444', sourceId = '55555555-5555-4555-8555-555555555555';
const model = { provider: 'openai', profile_id: 'fixture-v1', model: 'fixture-model', display_name: 'Fixture model', allowed: true, execution_location: 'external', requires_api_key: true, input_price_per_mtok: '1', output_price_per_mtok: '1', max_input_tokens: 10000, max_output_tokens: 1000 };
let credential: { provider: string; key_hint: string; version: number; preferred_profile_id: string; validated_at: string; in_flight_jobs: number } | null = null;
let inputSource: 'commit' | 'pr' = 'commit';
const typeFailure = false;
const files = Array.from({ length: 1000 }, (_, i) => ({ file_id: 999 + i, path: i ? i === 999 ? 'src/' + 'very-long-directory-name/'.repeat(14) + '긴-파일-이름.ts' : `src/module-${i}.ts` : 'src/fixture.ts', name: i ? `module-${i}.ts` : 'fixture.ts', observation_count: 1000 - i, maintenance_priority: 0.5, risk_score: 0.5, evidence_status: 'observed', bus_factor: 1, coupling_score: 0.5, coupling_support: 3, coupled_file_count: 1 }));
const preview = () => ({ input_id: inputId, repository_id: 2, source_type: inputSource, pr_number: inputSource === 'pr' ? 1 : null, pr_title: 'Fixture change', base_sha: 'b'.repeat(40), head_sha: 'a'.repeat(40), status: 'ready', execution_valid: true, reviewable: true, manifest_hash: 'c'.repeat(64), policy_version: 'code-transfer-v1', files: [{ file_id: sourceId, path: 'src/fixture.ts', content_state: 'read', change_type: 'modified', byte_count: 50, hunks: [] }], coverage: { pr_total_files: 1, selected_files: 1, inventory_coverage: 'complete', selected_content_coverage: 'complete', analysis_coverage: 'complete', limitations: [] }, transfer: { ...model, external_transfer_allowed: true, credential_configured: !!credential, credential_version: credential?.version || 1, key_hint: 'fixture-masked' }, token_estimate: { input_tokens: 20, max_output_tokens: 100, within_limit: true, estimated_max_cost: '0.01' } });

const http: Http = async (path, method, _body, givenToken) => {
  requests.push(path); assert.equal(givenToken, token);
  requestMethods.push(method);
  if (unauthorized) return { status: 401, body: { detail: 'fixture signed out' }, headers: {} };
  const payload = _body ? JSON.parse(_body) as Record<string, unknown> : {};
  const url = new URL(path, 'https://fixture.test'); const p = url.pathname;
  let body: unknown = {};
  if (p === `/api/ai/v1/jobs/${jobId}` && jobStatus === 'queued') {
    jobViews++; if (jobViews > 1) await new Promise<void>(resolve => { releaseJob = resolve; });
  }
  if (p === `/api/ai/v1/shared/results/${resultId}`) return { status: 200, body: { mine: false, author: { login: 'reviewer' }, result: { result_id: resultId, repository_id: 2, kind: 'review', finding_count: 40, coverage: preview().coverage, model: 'fixture-model', provider: 'openai', created_at: snapshot.analyzed_at } }, headers: {} };
  if (p.endsWith('/analytics/snapshot') && snapshotStatus !== 200) return { status: snapshotStatus, body: { detail: 'INTERNAL_DATABASE_TRACE fixture diagnostic, should not appear in UX' }, headers: {} };
  if (p === '/search/github-repositories' && searchStatus !== 200) return { status: searchStatus, body: { detail: 'fixture failure' }, headers: {} };
  if (p === '/search/github-repositories' && blankSearch) return { status: 200, body: { query: 'fixture', total_count: 0, results: [] }, headers: {} };
  if (p === '/api/ai/v1/usage' && usageDelay) await new Promise<void>(resolve => { releaseUsage = resolve; });
  if (p === '/api/ai/v1/credentials/openai' && method === 'PUT' && invalidKey) return { status: 422, body: { error: { code: 'CREDENTIAL_INVALID', message: 'provider reflected fixture-test-value', details: {}, retryable: false } }, headers: {} };
  if (p.endsWith('/analytics/summary') && emptyData) return { status: 200, body: { repository: repo, total_commits: 0, total_contributors: 0, active_contributors_90d: 0, total_releases: 0, bugfix_pct: null }, headers: {} };
  if (p.endsWith('/timeline') && emptyData) return { status: 200, body: { months: [], commits: [], active_contributors: [], bugfix_pct: [] }, headers: {} };
  if (p.endsWith('/top-contributors') && emptyData) return { status: 200, body: { contributors: [] }, headers: {} };
  if (p.endsWith('/commit-types') && emptyData) return { status: 200, body: { types: [] }, headers: {} };
  if (p.endsWith('/tree') && emptyData) return { status: 200, body: { tree: { name: 'root', children: [] }, files: [], data_quality: {} }, headers: {} };
  if (p.endsWith('/network') && stress) {
    const count = Math.min(200, Number(url.searchParams.get('limit_nodes') || 50));
    const nodes = Array.from({ length: count }, (_, i) => ({ id: i + 1, name: `contributor-${i + 1}`, commit_count: count - i, cluster: i % 10 }));
    body = { nodes, edges: nodes.flatMap(n => [1, 2, 3].map(offset => ({ source: n.id, target: (n.id + offset) % count + 1, edge_weight: 0.5 }))), metrics: { total_contributor_count: 200, total_edge_count: 600, cluster_count: 10 } };
    return { status: 200, body, headers: {} };
  }
  if (p.endsWith('/top-pairs')) return { status: 200, body: [{ contributor_a: { id: 1, name: 'Fixture' }, contributor_b: { id: 2, name: 'Reviewer' }, edge_weight: 0.8, co_edit_raw: 1, review_directed_a_to_b: 1, review_directed_b_to_a: 0 }], headers: {} };
  if (p.endsWith('/files/1000/detail')) return { status: 200, body: { file: { id: 1000, path: 'src/module-1.ts' }, editors: [{ contributor_id: 1, name: 'Fixture', share: 1 }], coupled_files: [], co_change_unit: 'pr' }, headers: {} };

  if (p === '/api/desktop/v1/session') body = issued;
  else if (p === '/api/auth/me') body = user;
  else if (p === '/repositories' && method === 'POST') body = { repository_id: 2 };
  else if (p === '/repositories/2/status') { await new Promise(resolve => setTimeout(resolve, 100)); body = { repository_id: 2, status: analysisStatus, error_message: analysisStatus === 'failed' ? 'Fixture analysis failed' : null, snapshot_id: 7, commit_count: 1, pr_count: 1 }; }
  else if (p === '/repositories' || p === '/repositories/mine') body = [repo];
  else if (p === '/repositories/2') body = repo;
  else if (p.endsWith('/analytics/snapshot')) body = { snapshot, data_quality: { commit_count: 1, contributor_count: 1 } };
  else if (p.endsWith('/summary')) body = { repository: repo, total_commits: 1, total_contributors: 1, commits_30d: 1, active_contributors_90d: 1, total_releases: 0, bugfix_pct: 0 };
  else if (p.endsWith('/timeline') && richCharts) body = { months: ['2026-03', '2026-04', '2026-05'], commits: [3, 5, 2], active_contributors: [1, 2, 2], bugfix_pct: [0, 20, 10] };
  else if (p.endsWith('/timeline')) body = { months: ['2026-05'], commits: [1], active_contributors: [1], bugfix_pct: [0] };
  else if (p.endsWith('/commit-types') && typeFailure) return { status: 503, body: { detail: 'fixture temporary error' }, headers: {} };
  else if (p.endsWith('/commit-types') && richCharts) body = { types: ([['feature', 10], ['refactor', 3], ['bugfix', 5], ['docs', 2], ['chore', 1], ['other', 1]] as [string, number][]).map(([type, count]) => ({ type, count, pct: count * 100 / 22 })) };
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
    body = { file: { id: slow ? 998 : 999, path: slow ? 'src/older.ts' : 'src/fixture.ts' }, editors: [{ contributor_id: 1, name: 'Fixture', share: 1 }], coupled_files: [{ file_id: 1000, path: 'src/module-1.ts', co_change_count: 3, coupling_score: 0.75, adjusted_score: 0.6, selected_change_count: 4, other_change_count: 6, selected_to_other_ratio: 0.75, other_to_selected_ratio: 0.5 }], co_change_unit: 'pr', maintenance_priority: 0.5, evidence_status: 'observed', change_freq: 10, churn: 80, bugfix_count: 1, observation_count: 10 };
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
  else if (p === `/api/ai/v1/jobs/${jobId}`) body = { job_id: jobId, repository_id: 2, result_id: resultId, input_id: inputId, status: jobStatus, error_code: jobStatus === 'failed' ? 'PROVIDER_AUTH' : null, kind: 'review', created_at: snapshot.analyzed_at };
  else if (p === `/api/ai/v1/results/${resultId}`) body = { result_id: resultId, job_id: jobId, repository_id: 2, input_id: inputId, kind: 'review', provider: 'openai', model: 'fixture-model', created_at: snapshot.analyzed_at, expires_at: snapshot.analyzed_at, finding_count: 40, human_validation: 'unverified', coverage: preview().coverage };
  else if (p === `/api/ai/v1/results/${resultId}/findings` || p === `/api/ai/v1/shared/results/${resultId}/findings`) {
    const second = !!url.searchParams.get('cursor');
    if (second) { pageRequests++; await new Promise(resolve => setTimeout(resolve, 150)); }
    body = { findings: emptyReview ? [] : Array.from({ length: 20 }, (_, index) => { const i = index + 1 + (second ? 20 : 0); return { ...(p.includes('/shared/') ? { feedbacks: [] } : {}), finding_id: `${i.toString().padStart(8, '0')}-6666-4666-8666-666666666666`, source_file_id: sourceId, finding: { ordinal: i, title: `Finding ${i}`, priority: (i % 3) + 1, category: 'correctness', human_validation: 'unverified', location: { path: 'src/' + 'long-path/'.repeat(12) + 'file.ts', side: 'RIGHT', start_line: i, end_line: i }, condition: 'Fixture condition '.repeat(30), impact: 'Fixture impact '.repeat(20), verification: 'Inspect source', suggestion: 'Suggested change '.repeat(20) } }; }), next_cursor: emptyReview || second ? null : 'page-2' };
  }
  else if (p === `/api/ai/v1/results/${resultId}/history`) body = { evidence: [] };
  else if (p === '/api/ai/v1/history/snapshots') body = { snapshots: [] };
  else if (p === '/api/ai/v1/shared/results') body = { results: [], next_cursor: null };

  else if (p === '/api/ai/v1/usage') body = { period_start: '2026-10-01', currency: 'USD', user_spent: '0', user_reserved: '0', user_limit: '20', user_remaining: '20', server_budget_enabled: true, uncertain_reservations: 0, daily_jobs_used: 0, daily_job_limit: 30 };
  else if (p === '/search/github-repositories') body = { query: 'fixture', total_count: 1, results: [{ owner: 'fixture', repo_name: 'p03', url: repo.url, description: 'Search fixture', stars: 1, forks: 0, is_registered: true }] };
  return { status: 200, body, headers: {} };
};


void app.whenReady().then(async () => {
  mkdirSync(output, { recursive: true });
  await serveAssets(join(__dirname, '../ui'));
  const contracts = new RemoteContracts(JSON.parse(readFileSync(join(__dirname, '../schema/ai-bodies.json'), 'utf8')) as Record<string, Record<string, unknown>>);
  const auth = new DesktopAuth('https://fixture.test', http, async () => { throw new Error('UNEXPECTED_EXTERNAL_BROWSER'); }, { read: async () => issued, write: async () => {} }); await auth.restore();
  const remote = new Remote(contracts, http, auth.identity, auth.invalidate);
  const window = new BrowserWindow({ width: 900, height: 640, frame: false, show: false, webPreferences: { preload: join(__dirname, '../preload/preload.js'), offscreen: true, backgroundThrottling: false, sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true } });
  const unregister = registerDesktop(window, auth, remote); const unregisterHealth = registerHealth(window, unavailable);
  let frame: NativeImage | undefined, paints = 0;
  window.webContents.on('paint', (_event, _dirty, image) => { frame = image; paints++; });
  async function wait(expression: string) {
    for (let i = 0; i < 180; i++) { if (await window.webContents.executeJavaScript(expression)) return; await new Promise(resolve => setTimeout(resolve, 25)); }
    throw new Error(`${phase}: ${expression}`);
  }
  // `detail` is only evaluated when the check fails, so a flaky failure reports what the page actually held.
  async function check(name: string, expression: string, detail?: string) {
    const passed = !!await window.webContents.executeJavaScript(expression);
    checks.push({ name, passed, ...(!passed && detail ? { detail: JSON.stringify(await window.webContents.executeJavaScript(detail)) } : {}) });
  }
  async function clickText(text: string) {
    await window.webContents.executeJavaScript(`(() => { const b = [...document.querySelectorAll('button,a')].find(e => e.textContent.trim() === ${JSON.stringify(text)} && e.getClientRects().length); if(!b) throw new Error('missing action'); b.click(); })()`);
  }
  async function load(page: string, query = '?repo=2&snapshot=7') {
    phase = page; await window.loadURL(`gitviz://app/web/pages/${page}.html${query}`); await wait(`!!document.querySelector('.app-shell') && !!document.querySelector('.app-detail')`);
  }
  async function capture(name: string, width: number, height: number, theme: string, selector?: string) {
    phase = name; window.setContentSize(width, height); await wait(`innerWidth===${width} && innerHeight===${height}`);
    await window.webContents.executeJavaScript(`document.getElementById('shell-theme').value=${JSON.stringify(theme)};document.getElementById('shell-theme').dispatchEvent(new Event('change')); if(typeof Chart!=='undefined') for(const chart of Object.values(Chart.instances)){chart.stop();chart.options.animation=false;chart.update('none')} document.getElementById('shell-status').textContent='QA 검증용 데이터 · 실서비스 아님'`);
    // ResizeObserver and Chart.js settle after the native viewport event. Check the settled layout.
    for (let i = 0; i < 20; i++) { await new Promise(resolve => setTimeout(resolve, 25)); if (await window.webContents.executeJavaScript(`document.getElementById('app-main').scrollWidth <= document.getElementById('app-main').clientWidth + 1`)) break; }
    await check(`${name}: no main horizontal overflow`, `document.getElementById('app-main').scrollWidth <= document.getElementById('app-main').clientWidth + 1`);
    if (selector) await window.webContents.executeJavaScript(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'start'})`);
    if (name === 'file-detail-1280-dark.png') {
      const debug = await window.webContents.executeJavaScript(`(() => { const m=document.getElementById('app-main'), r=m.getBoundingClientRect();return {width:m.clientWidth,scroll:m.scrollWidth,grid:getComputedStyle(document.querySelector('.overview-grid')).gridTemplateColumns,containers:getComputedStyle(m).containerType,wide:[...m.querySelectorAll('*')].filter(e=>e.getBoundingClientRect().right>r.right+1&&e.getBoundingClientRect().width).slice(0,12).map(e=>({tag:e.tagName,cls:e.className,id:e.id,width:e.getBoundingClientRect().width,right:e.getBoundingClientRect().right}))}; })()`);
      writeFileSync(join(output,'layout-diagnostics.json'),JSON.stringify(debug,null,2));
    }
    const before = paints; window.webContents.invalidate();
    for (let i=0; i<100; i++) { if(paints>before && frame?.getSize().width===width) break; await new Promise(resolve=>setTimeout(resolve,20)); }
    assert.ok(frame && paints>before); await new Promise(resolve=>setTimeout(resolve,100)); writeFileSync(join(output,name),frame!.toPNG());
  }
  try {
    phase = 'settings delayed usage'; usageDelay = true; await load('settings', ''); await wait(`!!document.getElementById('ai-key-input')`);
    await window.webContents.executeJavaScript(`document.getElementById('ai-key-input').focus();document.getElementById('ai-key-input').value='fixture-test-value'`);
    assert.ok(releaseUsage); (releaseUsage as () => void)(); usageDelay=false; await new Promise(resolve=>setTimeout(resolve,200));
    await check('P1 usage response preserves key draft and focus', `document.getElementById('ai-key-input').value==='fixture-test-value' && document.activeElement.id==='ai-key-input'`);
    await window.webContents.executeJavaScript(`document.getElementById('ai-key-input').value=''`);
    invalidKey = true; await clickText('key 확인 후 저장'); await check('missing key inline guidance', `document.getElementById('ai-keys').textContent.includes('API key를 입력하세요')`);
    await window.webContents.executeJavaScript(`document.getElementById('ai-key-input').value='fixture-test-value'`); await clickText('key 확인 후 저장');
    await wait(`document.getElementById('ai-keys').textContent.includes('API key를 쓸 수 없습니다')`);
    await check('invalid key raw value erased and not rendered', `document.getElementById('ai-key-input').value==='' && !document.body.textContent.includes('provider reflected') && !JSON.stringify(localStorage).includes('fixture-test-value')`);
    invalidKey=false; await capture('settings-600-dark.png',600,400,'dark');

    snapshotStatus=503; await load('overview'); await wait(`!!document.getElementById('load-error')`);
    await check('P2 technical server details hidden', `!document.getElementById('load-error').textContent.includes('INTERNAL_DATABASE_TRACE')`);
    snapshotStatus=200; await clickText('다시 시도'); await wait(`!!document.querySelector('#overview-trend canvas')`);
    await check('snapshot load retry recovers', `!!document.querySelector('#overview-trend canvas')`);
    for(const status of [403,404,429]) {
      searchStatus=status; await load('search','?q=fixture'); await wait(`!!document.querySelector('#search-results .ui-state-error')`);
      await check(`search ${status} error with retry`, `!!document.querySelector('#search-results button')${status===403?" && document.getElementById('search-results').textContent.includes('권한')":''}`);
    }
    searchStatus=200; blankSearch=true; await load('search','?q=fixture'); await wait(`document.getElementById('search-results').textContent.includes('검색 결과가 없습니다')`);
    await capture('search-empty-600-light.png',600,400,'light','#search-results'); blankSearch=false;

    analysisStatus='failed'; await load('search','?q=fixture');await wait(`!!document.querySelector('.search-select-btn') && document.getElementById('nav-username')?.textContent==='fixture'`);
    await window.webContents.executeJavaScript(`document.querySelector('.search-select-btn').click()`);await clickText('새 분석 요청');await wait(`document.getElementById('modal-title').textContent==='분석 실패'`);
    await wait(`!![...document.querySelectorAll('#modal-footer button')].find(e=>e.textContent.includes('마지막 성공 결과'))`);
    await check('A analysis failure offers retry and previous successful result', `!!document.querySelector('#modal-footer [data-action="retryJob"]') && !!document.querySelector('#modal-footer [data-action="viewLastSuccess"]')`);
    await window.webContents.executeJavaScript(`document.querySelector('#modal-footer [data-action="viewLastSuccess"]').click()`);await wait(`location.pathname.endsWith('/overview.html') && location.search.includes('snapshot=7')`);analysisStatus='done';
    await load('overview'); await wait(`!!document.querySelector('#overview-trend canvas') && document.querySelector('#overview-files button')`);
    // Chart.js caches shared element options and only refreshes them on non-direct updates, so a theme switch must not depend on a later resize.
    const trendBar = `(() => { const c = Chart.getChart(document.querySelector('#overview-trend canvas')); return { theme: document.documentElement.dataset.theme, rendered: c.getDatasetMeta(0).data.map(bar => bar.options.backgroundColor), token: getComputedStyle(document.documentElement).getPropertyValue('--chart-1').trim() }; })()`;
    for (const theme of ['dark', 'light', 'dark']) {
      await window.webContents.executeJavaScript(`document.getElementById('shell-theme').value=${JSON.stringify(theme)};document.getElementById('shell-theme').dispatchEvent(new Event('change'))`);
      await check(`bar chart follows a switch to ${theme} without any resize`, `(() => { const v = ${trendBar}; return v.theme === ${JSON.stringify(theme)} && v.rendered.length > 0 && v.rendered.every(color => color === v.token); })()`, trendBar);
    }
    await capture('overview-900-dark.png',900,640,'dark'); await check('dark chart axis title uses readable theme text', `Chart.getChart(document.querySelector('#overview-trend canvas')).options.scales.y.title.color===getComputedStyle(document.documentElement).getPropertyValue('--ui-muted').trim()`); await capture('overview-1920-light.png',1920,1080,'light'); await check('rendered chart bars follow theme switch', `Chart.getChart(document.querySelector('#overview-trend canvas')).getDatasetMeta(0).data[0].options.backgroundColor===getComputedStyle(document.documentElement).getPropertyValue('--chart-1').trim()`, `(() => { const c = Chart.getChart(document.querySelector('#overview-trend canvas')); return { theme: document.documentElement.dataset.theme, rendered: c.getDatasetMeta(0).data.map(bar => bar.options.backgroundColor), dataset: c.data.datasets[0].backgroundColor, token: getComputedStyle(document.documentElement).getPropertyValue('--chart-1').trim() }; })()`);
    await window.webContents.executeJavaScript(`document.querySelector('#overview-contributors button').click()`); await wait(`document.getElementById('dd-title').textContent==='Fixture'`);
    await window.webContents.executeJavaScript(`document.querySelector('[data-action="__ddPushFile"]').click()`); await wait(`document.getElementById('dd-title').textContent==='fixture.ts'`);
    await window.webContents.executeJavaScript(`document.querySelector('#dd-body [data-action="__ddPushContributor"]').click()`);await wait(`document.getElementById('dd-title').textContent==='Fixture'`);
    await window.webContents.executeJavaScript(`document.getElementById('dd-back-btn').click()`);await wait(`document.getElementById('dd-title').textContent==='fixture.ts'`);
    await window.webContents.executeJavaScript(`document.querySelector('[data-action="__ddPushFile"]').click()`); await wait(`document.getElementById('dd-title').textContent==='module-1.ts'`);
    await window.webContents.executeJavaScript(`document.getElementById('dd-back-btn').click()`); await wait(`document.getElementById('dd-title').textContent==='fixture.ts'`);
    await capture('file-detail-1280-dark.png',1280,800,'dark');
    window.webContents.sendInputEvent({type:'keyDown',keyCode:'Escape'}); window.webContents.sendInputEvent({type:'keyUp',keyCode:'Escape'}); await wait(`!document.getElementById('drilldown-modal').classList.contains('open')`);
    checks.push({name:'B/D contributor → file → cochanged file → back → close',passed:true});

    await load('code-analysis'); await wait(`document.querySelectorAll('#cx-list-tbody tr').length===50`);
    await check('file table headers stay readable', `document.querySelector('#cx-file-list-table thead').getBoundingClientRect().height <= 80`);
    await check('1000 files render only one page', `document.querySelectorAll('#cx-list-tbody tr').length===50`);
    await window.webContents.executeJavaScript(`(()=>{const input=document.getElementById('cx-search');input.value='긴-파일';input.dispatchEvent(new Event('input'));})()`);
    await wait(`document.querySelectorAll('#cx-list-tbody .cx-file-detail').length===1`); await window.webContents.executeJavaScript(`document.getElementById('app-main').scrollTop=document.getElementById('cx-list-tbody').closest('.panel').offsetTop-20`); await capture('files-long-path-600-light.png',600,640,'light','#cx-list-tbody');
    await window.webContents.executeJavaScript(`document.querySelector('[data-view="coupling"]').click()`); await wait(`!!document.querySelector('#cx-cp-body .cx-pair-file')`); await check('D cochange count and directional denominators visible', `document.getElementById('cx-cp-body').textContent.includes('3회') && document.getElementById('cx-cp-body').textContent.includes('(3/4)')`);

    stress=true; await load('network'); await wait(`!!document.getElementById('net-limit') && document.getElementById('net-status').textContent==='결과 조회 완료'`);
    await window.webContents.executeJavaScript(`document.getElementById('net-limit').value='200'`); await clickText('필터 적용'); await wait(`document.querySelectorAll('#net-node-list button').length===200`); await new Promise(resolve=>setTimeout(resolve,800)); await clickText('배치 일시정지');
    await check('200 graph nodes, 600 edges and top pair', `document.querySelectorAll('#net-graph-svg [role="button"]').length===200 && document.querySelectorAll('#net-graph-svg line').length===600 && document.getElementById('net-top-pairs').textContent.includes('Reviewer')`);
    await window.webContents.executeJavaScript(`document.getElementById('app-main').scrollTop=document.getElementById('net-graph-svg').closest('.panel').offsetTop-16`); await capture('network-900-dark.png',900,640,'dark'); await window.webContents.executeJavaScript(`(()=>{const i=document.getElementById('net-search');i.value='contributor-200';i.dispatchEvent(new Event('input'));document.querySelector('#net-node-list button').focus();})()`);
    await check('network keyboard alternative narrows results', `document.querySelectorAll('#net-node-list button').length===1 && document.activeElement.textContent==='contributor-200'`); stress=false;

    await window.webContents.executeJavaScript(`document.querySelector('#net-top-pairs button').click()`);await wait(`document.getElementById('dd-title').textContent==='Fixture'`);
    await window.webContents.executeJavaScript(`document.querySelector('#dd-body [data-action="__ddPushFile"]').click()`);await wait(`document.getElementById('dd-title').textContent==='fixture.ts'`);await window.webContents.executeJavaScript(`window.closeDrilldown()`);
    checks.push({name:'C top pair → contributor → related file',passed:true});
    await load('ai-review','?repo=2'); await wait(`document.querySelectorAll('[role="tab"]').length===3`);
    await window.webContents.executeJavaScript(`document.getElementById('ai-pr-tab-open').focus()`);
    window.webContents.sendInputEvent({type:'keyDown',keyCode:'Right'});window.webContents.sendInputEvent({type:'keyUp',keyCode:'Right'});
    await wait(`document.getElementById('ai-pr-tab-closed').getAttribute('aria-selected')==='true'`);
    await check('AI badges meet shared minimum text size', `parseFloat(getComputedStyle(document.querySelector('.ai-chip')).fontSize)>=12`);
    await check('PR tabs roving focus and panel relation', `document.querySelectorAll('[role="tab"][tabindex="0"]').length===1 && document.activeElement.id==='ai-pr-tab-closed' && document.getElementById('ai-pr-list').getAttribute('aria-labelledby')==='ai-pr-tab-closed'`);
    credential={provider:'openai',key_hint:'fixture-masked',version:1,preferred_profile_id:'fixture-v1',validated_at:snapshot.analyzed_at,in_flight_jobs:0};
    jobStatus='queued';jobViews=0;await load('ai-review',`?repo=2&job=${jobId}`);
    await wait(`document.querySelector('.ai-progress')?.textContent.includes('queued')===true`);
    await check('E queued job shows progress before result', `!!document.querySelector('.ai-progress') && !document.querySelector('.ai-finding')`);
    for(let i=0;i<100&&!releaseJob;i++) await new Promise(resolve=>setTimeout(resolve,10)); assert.ok(releaseJob);jobStatus='succeeded';(releaseJob as () => void)();
    await wait(`document.querySelectorAll('.ai-finding').length===20`);

    await capture('review-long-900-light.png',900,640,'light','.ai-findings');
    const before=pageRequests; await window.webContents.executeJavaScript(`(()=>{const b=[...document.querySelectorAll('button')].find(e=>e.textContent==='지적 더 보기');b.click();b.click();})()`); await wait(`document.querySelectorAll('.ai-finding').length>=40`); await new Promise(resolve=>setTimeout(resolve,100));
    await check('P1 findings pagination ignores double activation', `document.querySelectorAll('.ai-finding').length===40`); checks.push({name:'P1 one request for one findings page',passed:pageRequests-before===1,detail:pageRequests-before});
    await load('ai-review',`?repo=2&shared=${resultId}`);await wait(`document.querySelectorAll('.ai-finding').length===20`);
    const sharedBefore=pageRequests;await window.webContents.executeJavaScript(`(()=>{const b=[...document.querySelectorAll('button')].find(e=>e.textContent==='지적 더 보기');b.click();b.click();})()`);await wait(`document.querySelectorAll('.ai-finding').length>=40`);
    await check('shared review stays read-only with one pagination append', `document.querySelectorAll('.ai-finding').length===40 && !document.querySelector('.ai-finding textarea')`);checks.push({name:'shared findings one page request',passed:pageRequests-sharedBefore===1});
    emptyReview=true; await load('ai-review',`?repo=2&job=${jobId}`); await wait(`document.getElementById('ai-main').textContent.includes('지적 0건')`); await check('empty review never implies safe code', `document.getElementById('ai-main').textContent.includes('문제가 없다는 뜻이 아닙니다')`); emptyReview=false;
    jobStatus='failed'; await load('ai-review',`?repo=2&job=${jobId}`); await wait(`document.getElementById('ai-main').textContent.includes('API key가 거부됐습니다')`); await check('E provider auth failure offers keys recovery', `!![...document.querySelectorAll('button')].find(e=>e.textContent==='API key 설정 열기')`); jobStatus='succeeded';
    credential=null; await load('ai-review',`?repo=2&input=${inputId}`); await wait(`!!document.getElementById('ai-consent-check')`); await check('F missing key blocks execution', `document.getElementById('ai-consent-check').disabled && [...document.querySelectorAll('button')].find(e=>e.textContent==='코드 리뷰 생성').disabled`);

    credential={provider:'openai',key_hint:'fixture-masked',version:1,preferred_profile_id:'fixture-v1',validated_at:snapshot.analyzed_at,in_flight_jobs:0};await load('ai-review','?repo=2');await wait(`document.getElementById('ai-main').textContent.includes('Fixture PR')`);await clickText('파일 선택');await wait(`!!document.querySelector('.ai-file input[type="checkbox"]')`);
    await window.webContents.executeJavaScript(`document.querySelector('.ai-file input[type="checkbox"]').click()`);await clickText('미리보기 만들기');await wait(`!!document.getElementById('ai-consent-check')`);
    await check('E PR source confirmed in preview', `document.getElementById('ai-main').textContent.includes('PR #1') && !document.getElementById('ai-consent-check').checked`);
    await window.webContents.executeJavaScript(`document.getElementById('ai-consent-check').click()`);await clickText('코드 리뷰 생성');await wait(`document.querySelectorAll('.ai-finding').length===20`);
    checks.push({name:'E PR → selected files → preview → consent → result',passed:true});
    emptyData=true; await load('overview'); await wait(`document.getElementById('overview-contributors').textContent.includes('표시할 데이터가 없습니다')`); await check('zero and unavailable ratio are distinct', `document.getElementById('overview-kpis').textContent.includes('0개') && document.getElementById('overview-kpis').textContent.includes('—')`); await capture('overview-empty-600-dark.png',600,400,'dark');
    await load('timeline'); await wait(`document.getElementById('tl-window-note').textContent.includes('월별') && !!document.querySelector('.analysis-toolbar select')`); await check('empty activity hides meaningless plot axes', `document.getElementById('chart-timeline').parentElement.hidden && !Chart.getChart('chart-timeline')`); await capture('activity-empty-1280-light.png',1280,800,'light'); emptyData=false;
    // The other charts that share the theme update path: activity bar/line (three metrics, two colour indexes) and the doughnut.
    // Themes are switched with no resize in between; expected colours come from the CSS tokens, not from the implementation.
    richCharts = true; await load('timeline');
    const tok = (name: string) => `getComputedStyle(document.documentElement).getPropertyValue('${name}').trim()`;
    const switchTheme = (theme: string) => window.webContents.executeJavaScript(`document.getElementById('shell-theme').value=${JSON.stringify(theme)};document.getElementById('shell-theme').dispatchEvent(new Event('change'))`);
    const timelineState = (color: number) => `(() => { const c = Chart.getChart('chart-timeline'); const meta = c.getDatasetMeta(0); const line = c.config.type === 'line'; return { type: c.config.type, theme: document.documentElement.dataset.theme, token: ${tok(`--chart-${color}`)}, scaleTitle: c.options.scales.y.title.color, muted: ${tok('--ui-muted')}, bars: line ? [] : meta.data.map(e => e.options.backgroundColor), line: line ? meta.dataset.options.borderColor : null, points: line ? meta.data.map(e => e.options.backgroundColor + '/' + e.options.borderColor) : [] }; })()`;
    const timelineOk = (color: number, type: string, theme: string) => `(() => { const v = ${timelineState(color)}; return v.type === ${JSON.stringify(type)} && v.theme === ${JSON.stringify(theme)} && v.scaleTitle === v.muted && (v.type === 'bar' ? v.bars.length === 3 && v.bars.every(c => c === v.token) : v.line === v.token && v.points.length === 3 && v.points.every(c => c === v.token + '/' + v.token)); })()`;
    const donutState = `(() => { const c = Chart.getChart('chart-donut'); const order = { '기능 추가': 1, '리팩토링': 4, '버그 픽스': 2, '문서': 5, '잡무': 3, '기타': 6 }; return { theme: document.documentElement.dataset.theme, expected: c.data.labels.map(label => ${tok("--chart-' + order[label] + '")}), rendered: c.getDatasetMeta(0).data.map(arc => arc.options.backgroundColor) }; })()`;
    const donutOk = (theme: string) => `(() => { const v = ${donutState}; return v.theme === ${JSON.stringify(theme)} && v.rendered.length === 6 && v.rendered.every((color, i) => color === v.expected[i]); })()`;
    await wait(`!!Chart.getChart('chart-timeline') && Chart.getChart('chart-timeline').getDatasetMeta(0).data.length === 3 && !!Chart.getChart('chart-donut') && Chart.getChart('chart-donut').getDatasetMeta(0).data.length === 6`);
    // Let the doughnut's entrance animation finish before switching, so only the theme switch is measured.
    await wait(`(() => { const c = Chart.getChart('chart-donut'); const t = n => ${tok("--chart-' + n + '")}; const order = { '기능 추가': 1, '리팩토링': 4, '버그 픽스': 2, '문서': 5, '잡무': 3, '기타': 6 }; return c.getDatasetMeta(0).data.every((arc, i) => arc.options.backgroundColor === t(order[c.data.labels[i]])); })()`);
    for (const [metric, type, color] of [['commits', 'bar', 1], ['active_contributors', 'line', 2], ['bugfix_pct', 'line', 4]] as const) {
      await window.webContents.executeJavaScript(`(() => { const s = document.querySelector('select[aria-label="월별 지표"]'); s.value = ${JSON.stringify(metric)}; s.dispatchEvent(new Event('change')); })()`);
      await wait(`Chart.getChart('chart-timeline')?.config.type === ${JSON.stringify(type)} && Chart.getChart('chart-timeline').getDatasetMeta(0).data.length === 3`);
      for (const theme of ['dark', 'light', 'dark', 'light']) {
        await switchTheme(theme);
        await check(`activity ${type} chart (${metric}) follows a switch to ${theme} without any resize`, timelineOk(color, type, theme), timelineState(color));
      }
    }
    for (const theme of ['dark', 'light', 'dark', 'light']) {
      await switchTheme(theme);
      await check(`activity doughnut slices follow a switch to ${theme} without any resize`, donutOk(theme), donutState);
    }
    richCharts = false;
    writeFileSync(join(output,'ux-results.json'),JSON.stringify({fixtureOnly:true,checks},null,2));
    checks.forEach(c=>process.stdout.write(`${c.passed?'PASS':'FAIL'} ${c.name}${c.detail===undefined?'':` (${c.detail})`}\n`));
    if(checks.some(c=>!c.passed)) throw new Error('UX checks failed; see ux-results.json');
  } finally { unregisterHealth();unregister();window.destroy();clearTimeout(deadline); }
}).then(()=>app.exit(0)).catch(error=>{process.stderr.write(`FAIL Phase 4 (${phase}): ${String(error)}\n`);app.exit(1);});
