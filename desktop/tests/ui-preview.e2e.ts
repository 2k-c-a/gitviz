/** Render the actual bundled UI with explicit, deterministic fixture data.
 * Offscreen software painting avoids hidden-window compositor capture failures.
 * This entry is test-only; the application never imports these fixtures.
 */
import './profile';
import assert from 'node:assert/strict';
import { app, BrowserWindow, type NativeImage } from 'electron';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { registerScheme, serveAssets } from '../src/app-protocol';
import { DesktopAuth } from '../src/auth';
import { Remote, type Http } from '../src/remote';
import { RemoteContracts } from '../src/remote-contracts';
import { registerDesktop } from '../src/desktop-ipc';

app.disableHardwareAcceleration();
app.commandLine.appendSwitch('force-device-scale-factor', '1');
registerScheme(); app.on('window-all-closed', () => {});
let phase = 'startup';
const deadline = setTimeout(() => { process.stderr.write(`FAIL UI preview timeout: ${phase}\n`); app.exit(1); }, 30000);
const output = join(__dirname, '../../../.validation/phase3/screenshots');
const uid = '11111111-1111-4111-8111-111111111111';
const user = { id: uid, github_login: 'ui-preview', display_name: 'UI preview', avatar_url: null };
const issued = { user, sessionId: uid, sessionToken: 'f'.repeat(64), expiresAt: new Date(Date.now() + 300000).toISOString(), capabilities: ['remote-v1'], apiCapabilities: { desktopProtocol: 1, aiContract: '1.9' } };
const snapshot = { id: 7, repository_id: 2, target_sha: 'a'.repeat(40), payload_sha256: 'b'.repeat(64), formula_version: 'gitviz-2026.10-priority-v3', window_start: '2026-05-01T00:00:00Z', window_end: '2026-10-29T00:00:00Z', analyzed_at: '2026-10-29T00:00:00Z' };
const repository = { id: 2, name: 'preview/gitviz-ui', url: 'https://github.com/preview/gitviz-ui', default_branch: 'main', target_sha: snapshot.target_sha };
const quality = { commit_count: 240, contributor_count: 6, unidentified_commits: 0, github_account_unlinked: 0, merged_pr_count: 36, prs_with_files: 36, prs_with_linked_commits: 36, target_tree_file_count: 124, collected_target_file_count: 124, low_support_threshold: 5 };
const http: Http = async (path, method) => {
  assert.equal(method, 'GET', 'preview never mutates data or calls a provider');
  const p = new URL(path, 'https://fixture.test').pathname;
  let body: unknown;
  if (p === '/api/desktop/v1/session') body = issued;
  else if (p === '/api/auth/me') body = user;
  else if (p.endsWith('/analytics/snapshot')) body = { snapshot, data_quality: quality };
  else if (p.endsWith('/summary')) body = { repository, total_commits: 240, commits_30d: 45, total_contributors: 6, active_contributors_90d: 6, total_releases: 3, latest_release: 'v0.7.0', bugfix_pct: 18.8 };
  else if (p.endsWith('/timeline')) body = { months: ['2026-05', '2026-06', '2026-07', '2026-08', '2026-09', '2026-10'], commits: [28, 42, 35, 51, 39, 45], active_contributors: [3, 4, 4, 6, 5, 6], bugfix_pct: [14, 19, 17, 22, 15, 20] };
  else if (p.endsWith('/commit-types')) body = { types: [{ type: 'feature', count: 100, pct: 41.7 }, { type: 'bugfix', count: 45, pct: 18.8 }, { type: 'refactor', count: 40, pct: 16.7 }, { type: 'docs', count: 25, pct: 10.4 }, { type: 'chore', count: 20, pct: 8.3 }, { type: 'other', count: 10, pct: 4.2 }] };
  else if (p.endsWith('/releases')) body = { releases: [{ tag_name: 'v0.7.0', published_at: '2026-10-28', release_type: 'minor' }, { tag_name: 'v0.6.0', published_at: '2026-09-20', release_type: 'minor' }] };
  else if (p.endsWith('/top-contributors')) body = { contributors: [{ id: 1, github_login: 'reviewer-a', commit_count: 84 }, { id: 2, github_login: 'reviewer-b', commit_count: 68 }, { id: 3, github_login: 'reviewer-c', commit_count: 52 }] };
  else if (p.endsWith('/contributor-heatmap')) body = { weeks: Array.from({ length: 12 }, (_, i) => new Date(Date.UTC(2026, 7, 3 + i * 7)).toISOString().slice(0, 10)), contributors: [1, 2, 3].map(id => ({ id, name: `reviewer-${String.fromCharCode(96 + id)}`, counts: Array.from({ length: 12 }, (_, week) => (week * 3 + id * 5) % 12) })) };
  else if (p.endsWith('/tree')) body = { tree: { name: 'root', children: [] }, files: ['src/ui/shell.ts', 'src/analysis/snapshot.ts', 'src/api/github.ts', 'src/ui/detail.ts', 'src/analysis/network.ts'].map((path, i) => ({ file_id: 999 + i, path, observation_count: 24 - i * 3 })) };
  else if (p.endsWith('/network')) body = networkFixture();
  else if (p.endsWith('/network/top-pairs')) body = [[1, 'minji-kim', 7, 'yuna.jung', 0.82], [7, 'yuna.jung', 13, 'kangmin', 0.61], [1, 'minji-kim', 2, 'jaehyun.park', 0.55], [3, 'seoyeon', 5, 'ara-choi', 0.32]].map(([a, an, b, bn, w]) => ({ contributor_a: { id: a, name: an }, contributor_b: { id: b, name: bn }, edge_weight: w, co_edit_raw: 0.4, review_directed_a_to_b: a === 1 ? 0.2 : 0, review_directed_b_to_a: 0 }));
  else if (p.endsWith('/collaborators')) body = { contributor: { id: 1, name: 'minji-kim' }, collaborators: [], collaborator_count: 4, collaborators_detail: [['yuna.jung', 7, 6, 2, 1], ['jaehyun.park', 2, 5, 3, 0], ['seoyeon', 3, 3, 0, 2], ['dohyun-lee', 4, 2, 1, 0]].map(([name, id, prs, out, inn]) => ({ contributor: { id, name }, strength: 0.5, co_edit_score: 0.3, review_out_score: 0.1, review_in_score: 0.1, co_edit_files: Number(prs) * 3, co_pr_count: prs, reviews_out: out, reviews_in: inn })), shared_prs: [{ number: 128, title: 'fix: 캐시 무효화 순서', merged_at: '2026-05-02' }, { number: 99, title: 'feat: 검색 필터 추가', merged_at: '2026-04-11' }, { number: 87, title: 'refactor: 수집기 재시도 정리', merged_at: '2026-03-20' }] };
  else if (p.endsWith('/files/999/detail')) body = { file: { id: 999, path: 'src/ui/shell.ts' }, maintenance_priority: 0.62, evidence_status: 'observed', change_freq: 24, churn: 380, bugfix_count: 4, priority_components: { change_freq: 0.7, churn: 0.66, bugfix_count: 0.5 }, observation_count: 24, contributor_count: 2, top_owner_share: 0.75, bf80: 2, bus_factor: 2, entropy_normalized: 0.81, editors: [{ contributor_id: 1, name: 'reviewer-a', share: 0.75 }, { contributor_id: 2, name: 'reviewer-b', share: 0.25 }], co_change_unit: 'pr', coupled_files: [], coupled_total: 0, sort_by: 'coupling' };
  else return { status: 404, body: { detail: 'Fixture endpoint unavailable' }, headers: {} };
  return { status: 200, body, headers: {} };
};

/** 2.0 서버 형식: 커뮤니티 3개 + 연결 없는 개발자, 쌍마다 공동 수정·방향별 리뷰. */
function networkFixture() {
  const names = ['minji-kim', 'jaehyun.park', 'seoyeon', 'dohyun-lee', 'ara-choi', 'hyunwoo', 'yuna.jung', 'taeyang', 'sora-han', 'jiwoo', 'bomi-yoon', 'eunji', 'kangmin', 'haneul-oh', 'serin', 'minho-shin', 'dain', 'jun-seo'];
  const nodes = names.map((name, i) => ({ id: i + 1, name, commit_count: Math.round(160 / (i + 1)) + 3, pr_count: Math.round(40 / (i + 1)) + 1, reviews_given: (i * 7) % 12, degree: 0, degree_centrality: 0.2, betweenness_centrality: i === 0 ? 0.42 : i === 6 ? 0.31 : i === 12 ? 0.22 : 0.02 * (i % 4), is_hub: [0, 6, 12].includes(i), cluster: Math.floor(i / 6), community_rank: Math.floor(i / 6), community_size: 6, isolated: false, selected_by: ['commits'] }));
  type Pair = { a: number; b: number; strength: number; [key: string]: unknown };
  const pairs: Pair[] = [];
  const add = (a: number, b: number, co: number, ab: number, ba: number) => pairs.push({ a, b, co_edit_raw: co, review_a_to_b: ab, review_b_to_a: ba, strength: 0.3 * co + 0.4 * (ab + ba), co_edit: co > 0, review: ab + ba > 0, co_edit_score: 0.6 * co, co_edit_files: co ? Math.round(co * 20) : 0, co_pr_count: co ? Math.round(co * 8) : 0, reviews_a_to_b: Math.round(ab * 10), reviews_b_to_a: Math.round(ba * 10), prs: [{ number: 120 + a + b, title: 'fix: 캐시 무효화 순서', merged_at: '2026-05-02' }, { number: 98 + a, title: 'feat: 검색 필터 추가', merged_at: '2026-04-11' }] });
  for (let c = 0; c < 3; c++) for (let i = 0; i < 6; i++) for (let j = i + 1; j < 6; j++) if ((i + j + c) % 3 !== 1) add(c * 6 + i + 1, c * 6 + j + 1, ((i * 3 + j) % 5 + 1) / 5, j === i + 1 ? 0.5 : 0, i === 0 && j === 3 ? 0.3 : 0);
  add(1, 7, 0.4, 0.2, 0.1); add(7, 13, 0.3, 0, 0.4); add(2, 14, 0, 0.3, 0);
  pairs.forEach(pair => { nodes[pair.a - 1].degree++; nodes[pair.b - 1].degree++; });
  const sorted = pairs.map(pair => pair.strength).sort((x, y) => x - y), q = (x: number) => sorted[Math.ceil(x * sorted.length) - 1];
  return { nodes, edges: [], pairs: pairs.map(pair => ({ ...pair, a_name: names[pair.a - 1], b_name: names[pair.b - 1] })), metrics: { total_contributor_count: 22, total_edge_count: pairs.length },
    project: { total_developers: 22, total_commits: 742, total_prs: 168, total_relations: pairs.length, community_count: 3, isolated_count: 4, density: 0.21, hub_count: 3 },
    view: {}, selection: { limit: 50, criteria: ['commits', 'prs', 'collaboration'], connected_developers: 18, pr_participation_available: true },
    communities: [0, 1, 2].map(rank => ({ id: rank, size: 6, rank, shown: 6 })),
    isolated: ['direct-pusher', 'docs-bot', 'kim.ci', 'old-maintainer'].map((name, i) => ({ id: 100 + i, name, commit_count: 12 - i * 3, pr_count: 0 })),
    strength_cutoffs: { no_weak: q(0.3), medium: q(0.5), strong: q(0.7) }, hub_rule: { share: 0.15, threshold: 0.22 }, details_available: true, review_available: true,
    large_pr_rule: '변경 파일이 max(15, 대상 트리 파일 수의 10%)개 이상인 PR은 공동 수정 계산에서 제외', data_quality: { merged_pr_count: 168, prs_with_files: 168, prs_with_linked_commits: 168 } };
}

void app.whenReady().then(async () => {
  mkdirSync(output, { recursive: true });
  await serveAssets(join(__dirname, '../ui'));
  const auth = new DesktopAuth('https://fixture.test', http, async () => { throw new Error('NO_EXTERNAL_BROWSER'); }, { read: async () => issued, write: async () => {} });
  await auth.restore();
  const contracts = new RemoteContracts(JSON.parse(readFileSync(join(__dirname, '../schema/ai-bodies.json'), 'utf8')) as Record<string, Record<string, unknown>>);
  const remote = new Remote(contracts, http, auth.identity, auth.invalidate);
  const window = new BrowserWindow({ width: 900, height: 640, frame: false, show: false, webPreferences: { preload: join(__dirname, '../preload/preload.js'), offscreen: true, backgroundThrottling: false, sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true } });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  const unregister = registerDesktop(window, auth, remote);
  let frame: NativeImage | undefined;
  let paints = 0;
  window.webContents.on('paint', (_event, _dirty, image) => { frame = image; paints++; });
  async function wait(expression: string): Promise<void> {
    for (let i = 0; i < 150; i++) { if (await window.webContents.executeJavaScript(expression)) return; await new Promise(resolve => setTimeout(resolve, 30)); }
    throw new Error(`UI preview assertion: ${phase}: ${expression}`);
  }
  async function capture(name: string, width: number, height: number, theme: string) {
    phase = name; window.setContentSize(width, height);
    await wait(`innerWidth===${width} && innerHeight===${height}`);
    await window.webContents.executeJavaScript(`window.dispatchEvent(new CustomEvent('gitviz-theme-select',{detail:${JSON.stringify(theme)}})); for(const chart of Object.values(Chart.instances)){chart.stop();chart.options.animation=false;chart.update('none')} document.getElementById('shell-status').textContent='UI 미리보기 · 검증용 데이터'`);
    if (theme === 'dark') {
      assert.equal(await window.webContents.executeJavaScript(`getComputedStyle(document.getElementById('tl-m-commits') || document.querySelector('#overview-kpis .m-val') || document.getElementById('net-m-commits')).color`), 'rgb(230, 237, 245)');
      assert.equal(await window.webContents.executeJavaScript(`getComputedStyle(document.getElementById('analysis-context')).backgroundColor`), 'rgb(26, 34, 45)');
    }
    const before = paints; window.webContents.invalidate();
    for (let i = 0; i < 100; i++) { if (paints > before && frame?.getSize().width === width) break; await new Promise(resolve => setTimeout(resolve, 30)); }
    assert.ok(frame && paints > before, `no fresh painted frame for ${name}`);
    await new Promise(resolve => setTimeout(resolve, 150));
    writeFileSync(join(output, name), frame!.toPNG());
  }
  try {
    await window.loadURL('gitviz://app/web/pages/overview.html?repo=2&snapshot=7');
    await wait(`!!document.querySelector('.app-detail') && document.getElementById('tl-m-commits').textContent==='240'`);
    await capture('activity-900-light.png', 900, 640, 'light');
    await capture('activity-900-dark.png', 900, 640, 'dark');
    await window.webContents.executeJavaScript(`window.openFileDrilldown(999)`);
    await wait(`document.getElementById('dd-title').textContent==='shell.ts'`);
    await capture('detail-1280-dark.png', 1280, 800, 'dark');
    await window.loadURL('gitviz://app/web/pages/overview.html?repo=2&snapshot=7');
    await wait(`!!Chart.getChart('chart-timeline') && document.querySelectorAll('#overview-files button').length===5 && !!Chart.getChart('chart-donut') && !!document.querySelector('.app-detail')`);
    await capture('overview-1440-light.png', 1440, 1080, 'light');
    await capture('overview-1440-dark.png', 1440, 1080, 'dark');
    await capture('overview-900-light.png', 900, 640, 'light');
    await window.setContentSize(1440, 1080);
    await wait(`innerWidth===1440 && Math.abs(Chart.getChart('chart-timeline').width - document.getElementById('chart-timeline').parentElement.clientWidth) < 2`);
    await new Promise(resolve => setTimeout(resolve, 300));
    await window.webContents.executeJavaScript(`(() => { const c = Chart.getChart('chart-timeline'); const el = c.getDatasetMeta(0).data[4], r = c.canvas.getBoundingClientRect(); c.canvas.dispatchEvent(new MouseEvent('mousemove', { clientX: r.left + el.x, clientY: r.top + c.chartArea.top + 40, bubbles: true })); })()`);
    await wait(`Chart.getChart('chart-timeline').tooltip.getActiveElements().length===3 && Chart.getChart('chart-timeline').$gitvizReleases?.size > 0 && Chart.getChart('chart-timeline').tooltip.footer?.join('').includes('v0.6.0')`);
    await capture('overview-hover-1440-light.png', 1440, 1080, 'light');
    await window.loadURL('gitviz://app/web/pages/network.html?repo=2&snapshot=7');
    await wait(`document.getElementById('net-status').textContent==='결과 조회 완료' && document.querySelectorAll('#net-graph-svg .net-link').length>20 && !!document.querySelector('#net-top-pairs button')`);
    await new Promise(resolve => setTimeout(resolve, 2500));
    await window.webContents.executeJavaScript(`document.getElementById('app-main').scrollTop=document.getElementById('net-panel').offsetTop-12`);
    await capture('network-1440-light.png', 1440, 1000, 'light');
    await window.webContents.executeJavaScript(`document.querySelector('#net-node-list button').click()`);
    await wait(`document.querySelector('#net-side .net-side-title')?.textContent==='minji-kim' && !!document.querySelector('#net-side [data-more]')`);
    await capture('network-node-1440-dark.png', 1440, 1000, 'dark');
    await window.webContents.executeJavaScript(`(()=>{const s=document.getElementById('net-relation');s.value='review';s.dispatchEvent(new Event('change'));const h=[...document.querySelectorAll('#net-graph-svg .net-link-hit')].find(e=>e.getAttribute('display')!=='none');h.dispatchEvent(new MouseEvent('click',{bubbles:true}))})()`);
    await wait(`document.querySelector('#net-side .net-side-title')?.textContent.includes('↔')`);
    await capture('network-pair-1440-light.png', 1440, 1000, 'light');
    await capture('network-900-light.png', 900, 640, 'light');
    writeFileSync(join(output, 'preview-result.json'), JSON.stringify({ status: 'passed', fixtureOnly: true, rendering: 'Electron offscreen software paint', captures: ['activity-900-light.png', 'activity-900-dark.png', 'detail-1280-dark.png', 'overview-1440-light.png', 'overview-1440-dark.png', 'overview-900-light.png', 'network-1440-light.png', 'network-node-1440-dark.png', 'network-pair-1440-light.png', 'network-900-light.png', 'overview-hover-1440-light.png'] }));
    process.stdout.write(`PASS UI preview: ${output}\n`);
  } finally { unregister(); window.destroy(); clearTimeout(deadline); }
}).then(() => app.exit(0)).catch(error => { process.stderr.write(`FAIL UI preview (${phase}): ${String(error)}\n`); app.exit(1); });
