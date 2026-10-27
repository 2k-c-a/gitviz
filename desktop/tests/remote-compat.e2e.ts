import './profile';
/** Actual sandbox pages with an in-process server transport fixture. No external OAuth/API. */
import assert from 'node:assert/strict';
import { app } from 'electron';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { DesktopAuth } from '../src/auth';
import { Remote, type Http } from '../src/remote';
import { RemoteContracts } from '../src/remote-contracts';
import { registerDesktop } from '../src/desktop-ipc';
import { registerScheme, serveAssets } from '../src/app-protocol';
import { createWindow } from '../src/window';

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
let unauthorized = false;
const http: Http = async (path, method, _body, givenToken) => {
  requests.push(path); assert.equal(givenToken, token);
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
  else if (p.endsWith('/risk-scores') || p.endsWith('/bus-factor') || p.endsWith('/coupling')) body = [];
  else if (p.endsWith('/snapshots')) body = { repository_id: 2, snapshots: [snapshot], next_before_id: null };
  else if (p === '/api/ai/v1/providers') body = { providers: [], external_transfer_allowed: false };
  else if (p === '/api/ai/v1/credentials') body = { credentials: [] };
  else if (p === '/api/ai/v1/usage') body = { currency: 'USD', used: '0', reserved: '0', limit: '20' };
  else if (p === '/search/github-repositories') body = { query: 'fixture', total_count: 0, results: [] };
  return { status: method === 'DELETE' ? 204 : 200, body, headers: {} };
};

void app.whenReady().then(async () => {
  await serveAssets(join(__dirname, '../ui'));
  const contracts = new RemoteContracts(JSON.parse(readFileSync(join(__dirname, '../schema/ai-bodies.json'), 'utf8')) as Record<string, Record<string, unknown>>);
  const auth = new DesktopAuth('https://fixture.test', http, async () => { throw new Error('UNEXPECTED_EXTERNAL_BROWSER'); }, { read: async () => issued, write: async () => {} });
  await auth.restore();
  const remote = new Remote(contracts, http, auth.identity, auth.invalidate);
  const window = createWindow(false); const unregister = registerDesktop(window, auth, remote);
  const errors: string[] = [];
  window.webContents.on('console-message', details => { if (details.level === 'error') errors.push(details.message.replace(/[A-Za-z]:[^\n]*/g, '[path]')); });
  async function wait(expression: string): Promise<void> {
    for (let i = 0; i < 100; i++) {
      if (await window.webContents.executeJavaScript(expression)) return;
      await new Promise(resolve => setTimeout(resolve, 30));
    }
    throw new Error(`ASSERTION:${phase}`);
  }
  try {
    for (const [page, ready] of [
      ['timeline', `document.getElementById('tl-m-commits')?.textContent==='1' && typeof Chart==='function'`],
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
      assert.equal(await window.webContents.executeJavaScript(`typeof require`), 'undefined');
      assert.equal(errors.length, before, `renderer error in ${page}`);
    }
    phase = 'delegated-navigation';
    await window.loadURL('gitviz://app/web/pages/timeline.html?repo=2&snapshot=7');
    await wait(`document.getElementById('tl-m-commits')?.textContent==='1'`);
    await window.webContents.executeJavaScript(`document.querySelector('[data-action="goPage"][data-args="%5B%22network%22%5D"]').click()`);
    await wait(`location.pathname.endsWith('/network.html') && location.search.includes('snapshot=7') && document.getElementById('net-status')?.textContent==='결과 조회 완료'`);
    phase = 'boundaries';
    const state = await window.webContents.executeJavaScript(`desktop.auth.status()`);
    assert.equal(state.state, 'authenticated'); assert.equal('sessionToken' in state, false);
    await assert.rejects(window.webContents.executeJavaScript(`desktop.remote.request({endpointKey:'me',path:{},query:{},headers:{Authorization:'forged'}})`));
    await assert.rejects(window.webContents.executeJavaScript(`fetch('https://example.com')`));
    await window.webContents.executeJavaScript(`window.__p03xss=false; const p=document.createElement('img');p.setAttribute('onerror','window.__p03xss=true');p.src='missing';document.body.append(p);`);
    assert.equal(await window.webContents.executeJavaScript('window.__p03xss'), false);
    phase = 'no-default-repository'; const before = requests.length;
    await window.loadURL('gitviz://app/web/pages/timeline.html');
    await wait(`!!document.getElementById('load-error')`);
    await wait(`document.getElementById('nav-username')?.textContent==='fixture'`);
    assert.equal(requests.slice(before).some(path => /\/repositories\/1(?:\/|$)/.test(path)), false);
    phase = 'unauthorized'; unauthorized = true;
    await window.webContents.executeJavaScript(`void import('/web/js/platform/transport.js').then(m=>m.request('/api/auth/me')).catch(()=>{})`);
    await wait(`location.pathname==='/index.html'`);
    assert.equal(auth.identity(), null);
    process.stdout.write('PASS P03 actual sandbox renderer E01-E10 page fixture, snapshot URL, local Chart/D3, auth bridge/allowlist/CSP, no default repo, 401 invalidation\n');
  } finally { unregister(); window.destroy(); clearTimeout(deadline); }
}).then(() => app.exit(0)).catch(error => {
  process.stderr.write(`FAIL P03 renderer (${phase}): ${error instanceof Error ? error.message.slice(0, 160) : 'assertion'}\n`); app.exit(1);
});
