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
const deadline = setTimeout(() => { process.stderr.write(`FAIL P03 renderer deadline (${phase})\n`); app.exit(1); }, 60000);
const uid = '11111111-1111-4111-8111-111111111111';
const token = 'f'.repeat(64);
const user = { id: uid, github_login: 'fixture', display_name: 'P03 Fixture', avatar_url: null, email: null };
const issued = { sessionToken: token, sessionId: uid, user, expiresAt: new Date(Date.now() + 300000).toISOString(), capabilities: ['remote-v1'], apiCapabilities: { desktopProtocol: 1, aiContract: '1.9' } };
const snapshot = { id: 7, repository_id: 2, target_sha: 'a'.repeat(40), payload_sha256: 'b'.repeat(64), formula_version: 'v1', window_start: '2026-01-01T00:00:00Z', window_end: '2026-06-01T00:00:00Z', analyzed_at: '2026-06-01T00:00:00Z' };
const repo = { id: 2, url: 'https://github.com/fixture/p03', name: 'P03 Fixture', status: 'done', default_branch: 'main', last_analyzed_at: snapshot.analyzed_at, latest_snapshot_id: 7 };
const requests: string[] = [];
const requestMethods: string[] = [];
let unauthorized = false;
const http: Http = async (path, method, _body, givenToken) => {
  requests.push(path); assert.equal(givenToken, token);
  requestMethods.push(method);
  if (unauthorized) return { status: 401, body: { detail: 'fixture signed out' }, headers: {} };
  const url = new URL(path, 'https://fixture.test'); const p = url.pathname;
  let body: unknown = {};
  if (p === '/api/desktop/v1/session') body = issued;
  else if (p === '/api/auth/me') body = user;
  else if (p === '/repositories' || p === '/repositories/mine') body = [repo];
  else if (p === '/repositories/2') body = repo;
  else if (p.endsWith('/analytics/snapshot')) body = { snapshot, data_quality: { commit_count: 1, contributor_count: 1 } };
  else if (p.endsWith('/summary')) body = { repository: repo, total_commits: 1, total_contributors: 1, commits_30d: 1, active_contributors_90d: 1, total_releases: 0, bugfix_pct: 0 };
  else if (p.endsWith('/timeline')) body = { months: ['2026-05'], commits: [1], active_contributors: [1], bugfix_pct: [0] };
  else if (p.endsWith('/commit-types')) body = { types: [{ type: 'feature', count: 1, pct: 100 }] };
  else if (p.endsWith('/contributor-heatmap')) body = { weeks: [], contributors: [] };
  else if (p.endsWith('/top-contributors')) body = { contributors: [] };
  else if (p.endsWith('/releases')) body = { releases: [] };
  else if (p.endsWith('/network')) body = { nodes: [{ id: 1, name: 'Fixture', commit_count: 1 }], edges: [], metrics: { total_contributor_count: 1, total_edge_count: 0 }, data_quality: {} };
  else if (p.endsWith('/top-pairs')) body = [];
  else if (p.endsWith('/tree')) body = { tree: { name: 'root', children: [] }, files: [], data_quality: { commit_count: 1 } };
  else if (/\/files\/(998|999)\/detail$/.test(p)) {
    const slow = p.includes('/998/'); if (slow) await new Promise(resolve => setTimeout(resolve, 200));
    body = { file: { id: slow ? 998 : 999, path: slow ? 'src/older.ts' : 'src/fixture.ts' }, editors: [], coupled_files: [], co_change_unit: 'pr', maintenance_priority: 0.5, evidence_status: 'observed', change_freq: 10, churn: 80, bugfix_count: 1, observation_count: 10 };
  }
  else if (p.endsWith('/risk-scores') || p.endsWith('/bus-factor') || p.endsWith('/coupling')) body = [];
  else if (p.endsWith('/snapshots')) body = { repository_id: 2, snapshots: [snapshot], next_before_id: null };
  else if (p === '/api/ai/v1/providers') body = { providers: [] };
  else if (p === '/api/ai/v1/usage') body = { period_start: '2026-10-01', currency: 'USD', user_spent: '0', user_reserved: '0', user_limit: '20', user_remaining: '20', server_budget_enabled: true, uncertain_reservations: 0, daily_jobs_used: 0, daily_job_limit: 30 };
  else if (p === '/search/my-github-repositories') {
    const page = Number(url.searchParams.get('page'));
    body = page === 1
      ? { page: 1, per_page: 30, has_next: true, results: [
        { owner: 'fixture', repo_name: 'p03', url: repo.url, description: 'Analyzed fixture', stars: 1, forks: 0, private: false, is_registered: true },
        { owner: 'fixture-org', repo_name: 'secret', url: 'https://github.com/fixture-org/secret', description: '<b>raw</b>', stars: 0, forks: 0, private: true, fork: true, is_registered: false }] }
      : { page: 2, per_page: 30, has_next: false, results: [{ owner: 'fixture', repo_name: 'page-two', url: 'https://github.com/fixture/page-two', is_registered: false }] };
  }
  else if (p === '/search/github-repositories') body = { query: 'fixture', total_count: 1, results: [{ owner: 'fixture', repo_name: 'p03', url: repo.url, description: 'Search fixture', stars: 1, forks: 0, is_registered: true }] };
  return { status: method === 'DELETE' ? 204 : 200, body, headers: {} };
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
  try {
    for (const [page, ready] of [
      ['settings', `document.getElementById('ai-title')?.textContent.includes('설정') && document.getElementById('ai-main')?.textContent.includes('돌아가기') && !document.getElementById('ai-main').textContent.includes('저장 위치')`],
      ['overview', `document.getElementById('tl-m-commits')?.textContent==='1' && !!Chart.getChart('chart-timeline') && document.getElementById('overview-collaboration')?.textContent.includes('전체 기여자 1명')`],
      ['network', `document.getElementById('net-status')?.textContent==='결과 조회 완료' && !!document.querySelector('#net-graph-svg circle')`],
      ['code-analysis', `document.getElementById('cx-status')?.textContent.includes('#7')`],
      ['history', `document.querySelector('#hist-tbody')?.textContent.includes('#7') || document.querySelector('#hist-tbody')?.textContent.includes('7')`],
      ['myprojects', `document.body.textContent.includes('fixture/p03')`],
      ['search', `!!document.getElementById('search-input') && document.getElementById('nav-username')?.textContent==='fixture'`],
      ['ai-review', `document.getElementById('nav-username')?.textContent==='fixture' && document.body.textContent.includes('AI')`],
    ]) {
      phase = page!; const before = errors.length;
      await window.loadURL(`gitviz://app/web/pages/${page}.html?repo=2&snapshot=7`);
      await wait(ready!);
      await wait(`!!document.querySelector('.app-shell') && !!document.querySelector('.app-detail')`);
      assert.equal(await window.webContents.executeJavaScript(`getComputedStyle(document.querySelector('.app-shell')).display`), 'grid');
      assert.equal(await window.webContents.executeJavaScript(`document.documentElement.lang`), 'ko');
      assert.equal(await window.webContents.executeJavaScript(`document.querySelectorAll('#app-main').length`), 1);
      assert.equal(await window.webContents.executeJavaScript(`document.querySelectorAll('.sub-tabs').length`), 0, 'single repository navigation');
      assert.equal(await window.webContents.executeJavaScript(`typeof workspace`), 'undefined', 'remote pages must not gain the local bridge');
      assert.equal(await window.webContents.executeJavaScript(`typeof require`), 'undefined');
      assert.equal(errors.length, before, `renderer error in ${page}`);
    }
    phase = 'search-results';
    await window.loadURL('gitviz://app/web/pages/search.html');
    await wait(`!!document.querySelector('.app-shell') && document.getElementById('nav-username')?.textContent==='fixture'`);
    const beforeSelect = requestMethods.length;
    await window.webContents.executeJavaScript(`document.getElementById('search-input').value='fixture'; document.getElementById('search-btn').click()`);
    await wait(`!!document.querySelector('.search-start-btn') && document.getElementById('search-results').textContent.includes('fixture/p03')`);
    assert.equal(requestMethods.slice(beforeSelect).some(method => method !== 'GET'), false, 'search is read-only');
    phase = 'my-github-repositories';
    // 최근·내 분석의 '내 GitHub 저장소' 탭: 열 때만 불러오고, 분석된 저장소는 결과로, 나머지는 분석 시작으로 연결한다.
    const beforeGithub = requests.length;
    await window.loadURL('gitviz://app/web/pages/myprojects.html');
    await wait(`document.getElementById('myproj-list')?.textContent.includes('fixture/p03') && document.getElementById('myproj-github')?.hidden === true`);
    assert.equal(requests.slice(beforeGithub).some(path => path.includes('my-github-repositories')), false, 'loads only when the tab opens');
    await window.webContents.executeJavaScript(`document.getElementById('myproj-tab-github').click()`);
    await wait(`document.getElementById('myproj-github-list')?.textContent.includes('fixture-org/secret') && !document.getElementById('myproj-github-more').hidden`);
    assert.equal(await window.webContents.executeJavaScript(`[document.getElementById('myproj-analyzed').hidden, document.getElementById('myproj-tab-github').getAttribute('aria-selected'), location.search]`).then(v => JSON.stringify(v)), JSON.stringify([true, 'true', '?tab=github']));
    assert.equal(await window.webContents.executeJavaScript(`[...document.querySelectorAll('.myproj-github-card')].map(c => c.querySelector('[data-action="goPage"]') ? 'result' : c.querySelector('.myproj-github-start') ? 'start' : '-').join()`), 'result,start');
    assert.match(await window.webContents.executeJavaScript(`document.querySelectorAll('.myproj-github-card')[1].textContent`), /비공개[\s\S]*포크[\s\S]*<b>raw<\/b>/, 'badges shown; description escaped');
    assert.equal(await window.webContents.executeJavaScript(`document.querySelectorAll('#myproj-github-list b').length`), 0);
    await window.webContents.executeJavaScript(`document.getElementById('myproj-github-more').click()`);
    await wait(`document.getElementById('myproj-github-list').textContent.includes('fixture/page-two') && document.getElementById('myproj-github-more').hidden`);
    assert.equal(requestMethods.slice(beforeGithub).some(method => method !== 'GET'), false, 'listing is read-only');
    await window.webContents.executeJavaScript(`document.querySelector('.myproj-github-start').click()`);
    await wait(`document.getElementById('url-input')?.value === 'https://github.com/fixture-org/secret' && document.getElementById('job-modal')?.classList.contains('open') && document.getElementById('modal-repo')?.textContent.includes('secret')`);
    await window.loadURL('gitviz://app/web/pages/myprojects.html?tab=github');
    await wait(`document.getElementById('myproj-analyzed')?.hidden === true && document.getElementById('myproj-github-list')?.textContent.includes('fixture/p03')`);
    phase = 'ai-analysis-tabs';
    // 커밋·PR 단위 분석(AI 리뷰)은 왼쪽 목록의 최상위 항목이 아니라 현재 저장소 AI 분석의 세 번째 탭이다.
    await window.loadURL('gitviz://app/web/pages/ai-insights.html?repo=2&snapshot=7');
    await wait(`[...document.querySelectorAll('#page-ai-insights .ai-tabs [role="tab"]')].map(t=>t.textContent).join()==='자동 분석,대화형 분석,커밋·PR 단위 분석'`);
    assert.equal(await window.webContents.executeJavaScript(`[...document.querySelectorAll('.app-nav-link')].some(a=>a.getAttribute('aria-label')==='AI 리뷰')`), false, 'no top-level AI review entry');
    await window.webContents.executeJavaScript(`document.getElementById('insight-tab-chat').click()`);
    await wait(`location.search.includes('tab=chat') && document.getElementById('insight-auto').hidden && !document.getElementById('insight-chat').hidden`);
    await window.webContents.executeJavaScript(`document.getElementById('insight-tab-review').click()`);
    await wait(`location.pathname.endsWith('/ai-review.html') && location.search==='?repo=2' && !document.getElementById('ai-analysis-tabs').hidden`);
    await wait(`document.querySelector('.app-nav-link[aria-current="page"]')?.getAttribute('aria-label')==='AI 분석' && document.getElementById('ai-tab-review').getAttribute('aria-selected')==='true'`);
    await window.webContents.executeJavaScript(`document.querySelector('#ai-analysis-tabs [data-insight-tab="chat"]').click()`);
    await wait(`location.pathname.endsWith('/ai-insights.html') && location.search==='?repo=2&tab=chat' && document.getElementById('insight-tab-chat')?.getAttribute('aria-selected')==='true' && !document.getElementById('insight-chat').hidden`);
    await window.loadURL('gitviz://app/web/pages/ai-review.html');
    await wait(`!!document.getElementById('ai-main')?.textContent.trim() && document.getElementById('ai-analysis-tabs').hidden`);
    phase = 'delegated-navigation';
    // The old activity address still lands on the merged overview with the same result.
    await window.loadURL('gitviz://app/web/pages/timeline.html?repo=2&snapshot=7');
    await wait(`location.pathname.endsWith('/overview.html') && location.search.includes('snapshot=7') && document.getElementById('tl-m-commits')?.textContent==='1'`);
    await window.webContents.executeJavaScript(`document.querySelector('[data-shell-page="network"]').click()`);
    await wait(`location.pathname.endsWith('/network.html') && location.search.includes('snapshot=7') && document.getElementById('net-status')?.textContent==='결과 조회 완료'`);
    phase = 'shell-interactions';
    await wait(`document.querySelector('.app-nav-link[aria-current="page"]')?.textContent.includes('협업')`);
    assert.match(await window.webContents.executeJavaScript(`document.querySelector('[data-shell-page="overview"]').href`), /repo=2&snapshot=7/);
    assert.equal(await window.webContents.executeJavaScript(`document.querySelectorAll('#shell-repository, #shell-context, .app-toolbar > .ui-badge').length`), 0, 'top bar has no mode or repository caption');
    await window.webContents.executeJavaScript(`window.dispatchEvent(new CustomEvent('gitviz-theme-select',{detail:'dark'}))`);
    assert.equal(await window.webContents.executeJavaScript(`document.documentElement.dataset.theme`), 'dark');
    assert.equal(await window.webContents.executeJavaScript(`getComputedStyle(document.body).backgroundColor`), 'rgb(17, 22, 29)');
    assert.equal(await window.webContents.executeJavaScript(`document.querySelectorAll('[data-shell-page="timeline"]').length`), 0, 'activity merged into overview');
    await window.webContents.executeJavaScript(`document.querySelector('[data-shell-page="overview"]').click()`);
    await wait(`location.pathname.endsWith('/overview.html') && !!document.querySelector('.app-detail') && document.getElementById('tl-m-commits')?.textContent==='1'`);
    assert.equal(await window.webContents.executeJavaScript(`document.documentElement.dataset.theme`), 'dark', 'theme persists across documents');
    await window.webContents.executeJavaScript(`window.dispatchEvent(new CustomEvent('gitviz-theme-select',{detail:'light'}))`);
    assert.equal(await window.webContents.executeJavaScript(`Chart.getChart('chart-timeline').data.datasets[0].backgroundColor`), '#2563eb');
    for (const [width, height] of [[600, 400], [900, 640], [1280, 800], [1920, 1080]]) {
      phase = `layout-${width}`;
      window.setContentSize(width!, height!);
      await wait(`Math.abs(innerWidth - ${width}) <= 1`);
      const dimensions = await window.webContents.executeJavaScript(`({body:document.body.scrollWidth, width:innerWidth, main:document.getElementById('app-main').getBoundingClientRect().width})`) as { body: number; width: number; main: number };
      assert.ok(dimensions.body <= dimensions.width + 1, `shell overflow ${width}`);
      assert.ok(dimensions.main > 0, `content hidden ${width}`);
      if (width === 900) assert.equal(await window.webContents.executeJavaScript(`Math.round(document.querySelector('.app-sidebar').getBoundingClientRect().width)`), 56, 'default window keeps a navigation rail');
      await window.webContents.executeJavaScript(`window.openFileDrilldown(999)`);
      await wait(`document.querySelector('.app-shell').classList.contains('has-detail')`);
      await wait(`document.getElementById('dd-title').textContent==='fixture.ts'`);
      await new Promise(resolve => setTimeout(resolve, 100));
      const narrowDetail = await window.webContents.executeJavaScript(`matchMedia('(width < 1280px)').matches`) as boolean;
      assert.equal(await window.webContents.executeJavaScript(`getComputedStyle(document.getElementById('app-main')).display==='none'`), narrowDetail, `detail mode at viewport ${dimensions.width}`);
      assert.ok(await window.webContents.executeJavaScript(`document.getElementById('drilldown-modal').getBoundingClientRect().width >= 300`), 'detail must occupy visible space');
      // Escape must close the panel, not trigger any mutation API.
      window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Escape' });
      window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Escape' });
      await wait(`!document.getElementById('drilldown-modal').classList.contains('open')`);
    }
    window.setContentSize(900, 640);
    await wait(`Math.abs(innerWidth - 900) <= 1`);
    phase = 'detail-request-race';
    await window.webContents.executeJavaScript(`window.openFileDrilldown(998); window.openFileDrilldown(999)`);
    await new Promise(resolve => setTimeout(resolve, 350));
    assert.equal(await window.webContents.executeJavaScript(`document.getElementById('dd-title').textContent`), 'fixture.ts', 'old response must not overwrite new selection');
    await window.webContents.executeJavaScript(`window.closeDrilldown()`);
    phase = 'sidebar-keyboard';
    await window.webContents.executeJavaScript(`document.querySelector('.app-menu').click()`);
    await wait(`document.activeElement.matches('.app-nav a')`);
    window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Escape' });
    window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Escape' });
    await wait(`!document.querySelector('.app-shell').classList.contains('nav-open') && document.activeElement.matches('.app-menu')`);
    phase = 'modal-keyboard';
    await window.webContents.executeJavaScript(`document.querySelector('[data-action="openReanalyzeToast"]').focus(); window.openLoginModal()`);
    await wait(`document.getElementById('login-modal').contains(document.activeElement)`);
    window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Tab' });
    window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Tab' });
    await wait(`document.activeElement.matches('#login-modal button')`);
    window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Escape' });
    window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Escape' });
    await wait(`!document.getElementById('login-modal').classList.contains('open') && document.activeElement.matches('[data-action="openReanalyzeToast"]')`);
    phase = 'tooltip-keyboard';
    // Hidden fixture windows may update activeElement without dispatching native focusin.
    await window.webContents.executeJavaScript(`document.getElementById('shell-theme').focus(); document.getElementById('shell-theme').dispatchEvent(new FocusEvent('focusin', {bubbles:true}))`);
    await wait(`document.querySelector('[role="tooltip"]')?.textContent.includes('테마 전환')`);
    phase = 'boundaries';
    const state = await window.webContents.executeJavaScript(`desktop.auth.status()`);
    assert.equal(state.state, 'authenticated'); assert.equal('sessionToken' in state, false);
    await assert.rejects(window.webContents.executeJavaScript(`desktop.remote.request({endpointKey:'me',path:{},query:{},headers:{Authorization:'forged'}})`));
    await assert.rejects(window.webContents.executeJavaScript(`fetch('https://example.com')`));
    await window.webContents.executeJavaScript(`window.__p03xss=false; const p=document.createElement('img');p.setAttribute('onerror','window.__p03xss=true');p.src='missing';document.body.append(p);`);
    assert.equal(await window.webContents.executeJavaScript('window.__p03xss'), false);
    phase = 'no-default-repository'; const before = requests.length;
    await window.loadURL('gitviz://app/web/pages/overview.html');
    await wait(`!!document.getElementById('load-error')`);
    await wait(`document.getElementById('nav-username')?.textContent==='fixture'`);
    assert.equal(requests.slice(before).some(path => /\/repositories\/1(?:\/|$)/.test(path)), false);
    phase = 'unauthorized'; unauthorized = true;
    await window.webContents.executeJavaScript(`void import('/web/js/platform/transport.js').then(m=>m.request('/api/auth/me')).catch(()=>{})`);
    await wait(`location.pathname==='/index.html'`);
    assert.equal(auth.identity(), null);
    await wait(`!!document.querySelector('.workspace-content') && !!document.querySelector('.app-shell')`);
    assert.equal(await window.webContents.executeJavaScript(`document.getElementById('shell-theme').dataset.theme`), 'light');
    assert.equal(await window.webContents.executeJavaScript(`document.querySelectorAll('#project-picker').length`), 1);
    assert.equal(await window.webContents.executeJavaScript(`getComputedStyle(document.querySelector('.app-shell')).display`), 'grid');
    if (process.env.GITVIZ_UI_REPORT) writeFileSync(process.env.GITVIZ_UI_REPORT, JSON.stringify({ status: 'passed', pages: 8, sizes: ['600x400', '900x640', '1280x800', '1920x1080'], fixtureOnly: true, checks: ['navigation-context', 'theme-chart-update', 'detail-responsive', 'modal-focus-escape-return', 'CSP-bridge', 'workspace-preserved'] }));
    process.stdout.write('PASS sandbox renderer: existing pages, shell navigation/context, responsive detail, theme/chart, modal keyboard, bridge/CSP, no default repo, 401 invalidation\n');
  } finally { unregisterHealth(); unregister(); window.destroy(); clearTimeout(deadline); }
}).then(() => app.exit(0)).catch(error => {
  if (process.env.GITVIZ_UI_REPORT) writeFileSync(process.env.GITVIZ_UI_REPORT, JSON.stringify({ status: 'failed', phase, message: error instanceof Error ? error.message : 'assertion' }));
  process.stderr.write(`FAIL P03 renderer (${phase}): ${error instanceof Error ? error.message.slice(0, 160) : 'assertion'}\n`); app.exit(1);
});
