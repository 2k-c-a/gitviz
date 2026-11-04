import './profile';
/** Actual sandbox pages with an in-process server transport fixture. No external OAuth/API. */
import assert from 'node:assert/strict';
import { app, BrowserWindow } from 'electron';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DesktopAuth } from '../src/auth';
import { Remote, type Http } from '../src/remote';
import { RemoteContracts } from '../src/remote-contracts';
import { registerCollabFiles, registerCommunityLive, registerDesktop } from '../src/desktop-ipc';
import { CollabFiles } from '../src/collab-files';
import { CollabBundles } from '../src/collab-bundle';
import { Previews } from '../src/collab-preview';
import { CollabRunner } from '../src/collab-run';
import { CommunityLive, type StreamHttp } from '../src/community-live';
import { registerScheme, serveAssets } from '../src/app-protocol';
import { createWindow } from '../src/window';
import { registerHealth } from '../src/ipc';
import { unavailable } from '../contracts/workspace';

registerScheme(); app.on('window-all-closed', () => {});
// A synthetic microphone so voice chat runs real getUserMedia/WebRTC; the permission still goes through the app's handler.
app.commandLine.appendSwitch('use-fake-device-for-media-stream');
let phase = 'startup';
const deadline = setTimeout(() => { process.stderr.write(`FAIL Phase 3 renderer deadline (${phase})\n`); app.exit(1); }, 90000);
const uid = '11111111-1111-4111-8111-111111111111';
const token = 'f'.repeat(64);
const user = { id: uid, github_login: 'fixture', display_name: 'P03 Fixture', avatar_url: null, email: null };
const issued = { sessionToken: token, sessionId: uid, user, expiresAt: new Date(Date.now() + 300000).toISOString(), capabilities: ['remote-v1'], apiCapabilities: { desktopProtocol: 1, aiContract: '1.9' } };
let heatmapWeeksAsked = '';
const snapshot = { id: 7, repository_id: 2, target_sha: 'a'.repeat(40), payload_sha256: 'b'.repeat(64), formula_version: 'v1', window_start: '2026-01-01T00:00:00Z', window_end: '2026-06-01T00:00:00Z', analyzed_at: '2026-06-01T00:00:00Z' };
const repo = { id: 2, url: 'https://github.com/fixture/p03', name: 'P03 Fixture', status: 'done', default_branch: 'main', last_analyzed_at: snapshot.analyzed_at, latest_snapshot_id: 7 };
const requests: string[] = [];
const requestMethods: string[] = [];
const unauthorized = false;
const inputId = '22222222-2222-4222-8222-222222222222', jobId = '33333333-3333-4333-8333-333333333333', resultId = '44444444-4444-4444-8444-444444444444', sourceId = '55555555-5555-4555-8555-555555555555';
const model = { provider: 'openai', profile_id: 'fixture-v1', model: 'fixture-model', display_name: 'Fixture model', allowed: true, execution_location: 'external', requires_api_key: true, input_price_per_mtok: '1', output_price_per_mtok: '1', max_input_tokens: 10000, max_output_tokens: 1000 };
// AI keys live in the app (AD-33): a fake vault stands in for DPAPI; the fixture server only sees the per-request header.
const appKeys: Record<string, string> = {};
const fakeVault = { get: async (provider: string) => appKeys[provider] ?? null,
  status: async () => ({ anthropic: !!appKeys.anthropic, openai: !!appKeys.openai, gemini: !!appKeys.gemini }) };
const jobKeys: (string | undefined)[] = [];
let inputSource: 'commit' | 'pr' = 'commit';
let typeFailure = false;
// Community fixture: the fixture user starts as admin of repository 2 with one other member.
const peer = { id: '66666666-6666-4666-8666-666666666666', github_login: 'peer', display_name: null, avatar_url: null };
const author = { id: uid, github_login: 'fixture', display_name: 'P03 Fixture', avatar_url: null };
const communityPosts: { id: number; title: string; body: string; comments: { id: number; body: string }[] }[] = [];
const communityBodies: unknown[] = [];
const goodInvite = 'i'.repeat(43);
let communityAdmin = uid;
let inviteCount = 0;
// Chat fixture: history over the allowlisted Remote, live events written into a fake stream by the test.
const chatMessages: { id: number; author: typeof author; author_id: string; body: string; created_at: string }[] = [];
const chatStreams: { path: string; write: (text: string) => void; cancelled: () => boolean }[] = [];
const chatStream: StreamHttp = async path => {
  let control!: ReadableStreamDefaultController<Uint8Array>; let cancelled = false;
  const body = new ReadableStream<Uint8Array>({ start: c => { control = c; }, cancel: () => { cancelled = true; } });
  chatStreams.push({ path, write: text => { if (!cancelled) control.enqueue(new TextEncoder().encode(text)); }, cancelled: () => cancelled });
  return { status: 200, contentType: 'text/event-stream', body };
};
// Voice fixture: the app takes seat A; a second Chromium peer in its own partition holds seat B and answers over real WebRTC.
const seatA = 'A'.repeat(22); const seatB = 'B'.repeat(22);
let voiceJoined = false; let voiceFull = false; let voiceLeft = 0;
const voiceSignals: Record<string, unknown>[] = [];
let voicePeer: BrowserWindow | null = null;
const voiceSeats = () => ({ participants: [{ session_id: seatB, user: peer }, ...(voiceJoined ? [{ session_id: seatA, user: author }] : [])], capacity: 3 });
// Co-editing fixture: the peer is the real editor bundle in its own window; the fixture relays Yjs messages like the server does.
const collabSid = 'S'.repeat(22);
const siteIndex = '<!doctype html>\r\n<link rel="stylesheet" href="css/site.css">\r\n<link rel="stylesheet" href="../outside.css">\r\n<script src="https://cdn.example.org/lib.js"></script>\r\n<img src="img/logo.png" alt="logo"><a href="about.html">about</a>\r\n';
const siteCss = 'body { color: rgb(1, 2, 3) }\r\n';
let collabSessions: Record<string, unknown>[] = [];
const collabRelays: Record<string, unknown>[] = [];
let collabPeer: BrowserWindow | null = null;
let collabLeft = 0;
const collabSession = (appHosts: boolean, seats: string[]) => ({ session_id: collabSid, file_name: appHosts ? 'index.html' : 'app.py', language: appHosts ? 'html' : 'python',
  file_count: appHosts ? 4 : 1, host: appHosts ? author : peer,
  seats: seats.map(seat => ({ seat_id: seat, user: seat === seatA ? author : peer, host: (seat === seatA) === appHosts })), capacity: 3, created_at: 1 });
const sse = (type: string, data: unknown) => `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
function communityFixture(p: string, method: string, payload: Record<string, unknown>): { status: number; body: unknown } {
  if (method !== 'GET') communityBodies.push(payload);
  const base = '/api/community/v1/repositories/2';
  const view = () => ({ repository: { id: 2, url: repo.url, name: repo.name }, me: { role: communityAdmin === uid ? 'admin' : 'member', joined_via: 'analyzer' },
    admin: communityAdmin === uid ? author : peer, member_count: 2 });
  const comments = (post: typeof communityPosts[number]) => post.comments.map(c => ({ id: c.id, author, author_id: uid, body: c.body, deleted: false, created_at: snapshot.analyzed_at, updated_at: null }));
  const postView = (post: typeof communityPosts[number], full: boolean) => ({ id: post.id, author, author_id: uid, title: post.title, body: post.body,
    created_at: snapshot.analyzed_at, updated_at: null, deleted: false, ...(full ? { comments: comments(post) } : { comment_count: post.comments.length }) });
  const invite = { id: '77777777-7777-4777-8777-777777777777', expires_at: '2026-12-01T00:00:00Z', max_uses: 5, use_count: 0, revoked: false };
  if (p === '/api/community/v1/communities') return { status: 200, body: { communities: [{ repository: view().repository, role: view().me.role, joined: true }] } };
  if (p === '/api/community/v1/invites/redeem') return payload.token === goodInvite ? { status: 200, body: { repository_id: 2 } }
    : { status: 410, body: { detail: { code: 'INVITE_INVALID', message: '만료됐거나 취소된 초대 링크입니다.' } } };
  if (p === base) return { status: 200, body: view() };
  if (p === `${base}/members`) return { status: 200, body: { members: [
    { user: communityAdmin === uid ? author : peer, role: 'admin', status: 'active', joined_via: 'analyzer', joined_at: snapshot.analyzed_at },
    { user: communityAdmin === uid ? peer : author, role: 'member', status: 'active', joined_via: 'contributor', joined_at: snapshot.analyzed_at }] } };
  if (p === `${base}/admin`) { communityAdmin = String(payload.user_id); return { status: 200, body: { ok: true } }; }
  if (p === `${base}/invites` && method === 'POST') { inviteCount++; return { status: 201, body: { invite, url: `https://fixture.test/pages/community.html#invite=${goodInvite}` } }; }
  if (p === `${base}/invites`) return { status: 200, body: { invites: Array.from({ length: inviteCount }, () => invite) } };
  if (p === `${base}/posts` && method === 'POST') {
    const post = { id: communityPosts.length + 1, title: String(payload.title), body: String(payload.body), comments: [] };
    communityPosts.push(post); return { status: 201, body: { id: post.id } };
  }
  if (p === `${base}/chat/messages` && method === 'POST') {
    const message = { id: chatMessages.length + 1, author, author_id: uid, body: String(payload.body), created_at: snapshot.analyzed_at };
    chatMessages.push(message);
    // The server also publishes it to every open stream, the sender's included.
    chatStreams.at(-1)?.write(sse('message', message));
    return { status: 201, body: message };
  }
  if (p === `${base}/voice`) return { status: 200, body: voiceSeats() };
  if (p === `${base}/voice/join`) {
    if (voiceFull) return { status: 409, body: { detail: { code: 'VOICE_FULL', message: '음성 채팅은 한 번에 3명까지 참여할 수 있습니다.' } } };
    voiceJoined = true;
    chatStreams.at(-1)?.write(sse('voice', voiceSeats()));
    return { status: 200, body: { session_id: seatA, ...voiceSeats(), heartbeat_seconds: 10, ice_servers: [] } };
  }
  if (p === `${base}/voice/heartbeat`) return { status: 200, body: voiceSeats() };
  if (p === `${base}/voice/leave`) { voiceLeft++; voiceJoined = false; chatStreams.at(-1)?.write(sse('voice', voiceSeats())); return { status: 200, body: { ok: true } }; }
  if (p === `${base}/voice/signal`) {
    voiceSignals.push(payload);
    void voicePeer?.webContents.executeJavaScript(`window.receive(${JSON.stringify(payload)})`);
    return { status: 200, body: { ok: true } };
  }
  if (p === `${base}/collab` && method === 'POST') {
    collabSessions = [collabSession(true, [seatA])];
    chatStreams.at(-1)?.write(sse('collab-sessions', { sessions: collabSessions, ended: [] }));
    return { status: 201, body: { session_id: collabSid, seat_id: seatA, session: collabSessions[0], heartbeat_seconds: 10 } };
  }
  if (p === `${base}/collab`) return { status: 200, body: { sessions: collabSessions, capacity: 3 } };
  if (p === `${base}/collab/${collabSid}/join`) {
    collabSessions = [collabSession(false, [seatB, seatA])];
    chatStreams.at(-1)?.write(sse('collab-sessions', { sessions: collabSessions, ended: [] }));
    return { status: 200, body: { session_id: collabSid, seat_id: seatA, session: collabSessions[0], heartbeat_seconds: 10 } };
  }
  if (p === `${base}/collab/${collabSid}/heartbeat`) return { status: 200, body: { session_id: collabSid, seat_id: seatA, session: collabSessions[0], heartbeat_seconds: 10 } };
  if (p === `${base}/collab/${collabSid}/leave`) {
    collabLeft++; collabSessions = [];
    chatStreams.at(-1)?.write(sse('collab-sessions', { sessions: [], ended: [collabSid] }));
    return { status: 200, body: { ok: true } };
  }
  if (p === `${base}/collab/${collabSid}/relay`) {
    collabRelays.push(payload);
    if (!payload.to || payload.to === seatB) void collabPeer?.webContents.executeJavaScript(`window.receive(${JSON.stringify({ ...payload, from: seatA })})`);
    return { status: 200, body: { ok: true } };
  }
  if (p === `${base}/chat/messages`) return { status: 200, body: { messages: chatMessages, next_before: null } };
  if (p === `${base}/posts`) return { status: 200, body: { posts: [...communityPosts].reverse().map(post => postView(post, false)), next_before: null } };
  const parts = p.slice(base.length).split('/');
  const post = parts[1] === 'posts' ? communityPosts[Number(parts[2]) - 1] : undefined;
  if (post && parts[3] === 'comments') { post.comments.push({ id: post.comments.length + 1, body: String(payload.body) }); return { status: 201, body: { id: post.comments.length } }; }
  if (post && parts.length === 3) return { status: 200, body: postView(post, true) };
  return { status: 403, body: { detail: { code: 'INVITE_REQUIRED', message: '이 저장소의 기여자가 아니면 초대 링크로만 참여할 수 있습니다.' } } };
}
const files =Array.from({ length: 52 }, (_, i) => ({ file_id: 999 + i, path: i ? `src/module-${i}.ts` : 'src/fixture.ts', name: i ? `module-${i}.ts` : 'fixture.ts', observation_count: 100 - i, churn: i === 51 ? 9000 : i === 50 ? null : 1000 - i, bugfix_count: i === 7 ? 40 : i === 50 ? null : i % 5, maintenance_priority: 0.5, risk_score: 0.5, evidence_status: 'observed', bus_factor: 1, coupling_score: 0.5, coupling_support: 3, coupled_file_count: 1 }));
const preview = () => ({ input_id: inputId, repository_id: 2, source_type: inputSource, pr_number: inputSource === 'pr' ? 1 : null, pr_title: 'Fixture change', base_sha: 'b'.repeat(40), head_sha: 'a'.repeat(40), status: 'ready', execution_valid: true, reviewable: true, manifest_hash: 'c'.repeat(64), policy_version: 'code-transfer-v1', files: [{ file_id: sourceId, path: 'src/fixture.ts', content_state: 'read', change_type: 'modified', byte_count: 50, hunks: [] }], coverage: { pr_total_files: 1, selected_files: 1, inventory_coverage: 'complete', selected_content_coverage: 'complete', analysis_coverage: 'complete', limitations: [] }, transfer: { ...model }, token_estimate: { input_tokens: 20, max_output_tokens: 100, within_limit: true, estimated_max_cost: '0.01' } });

const http: Http = async (path, method, _body, givenToken, _idempotency, aiKey) => {
  requests.push(path); assert.equal(givenToken, token);
  requestMethods.push(method);
  if (unauthorized) return { status: 401, body: { detail: 'fixture signed out' }, headers: {} };
  const payload = _body ? JSON.parse(_body) as Record<string, unknown> : {};
  const url = new URL(path, 'https://fixture.test'); const p = url.pathname;
  let body: unknown = {};
  if (p.startsWith('/api/community/v1/')) return { ...communityFixture(p, method, payload), headers: {} };
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
  else if (p.endsWith('/contributor-heatmap')) { heatmapWeeksAsked = url.searchParams.get('weeks') ?? ''; body = { weeks: [], contributors: [] }; }
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
  else if (p === '/api/ai/v1/providers') body = { providers: [{ id: 'openai', display_name: 'Fixture provider', key_format: 'Fixture key', models: [model] }] };
  else if (p === '/api/ai/v1/pulls') body = { items: [{ number: 1, title: 'Fixture PR', head_sha: 'a'.repeat(40), base_ref: 'main', head_ref: 'work' }], next_cursor: null };
  else if (p === '/api/ai/v1/commits') body = { items: [{ sha: 'a'.repeat(40), message: 'Fixture commit', committed_at: snapshot.analyzed_at }], next_cursor: null };
  else if (p.endsWith('/files') && (p.includes('/commits/') || p.includes('/pulls/'))) body = { base_sha: 'b'.repeat(40), head_sha: 'a'.repeat(40), pr_total_files: 1, inventory_coverage: 'complete', next_cursor: null, files: [{ path: 'src/fixture.ts', change_type: 'modified', has_patch: true, additions: 1, deletions: 1 }] };
  else if (p === '/api/ai/v1/inputs' && method === 'POST') { inputSource = payload.commit_sha ? 'commit' : 'pr'; body = { input_id: inputId, repository_id: 2 }; }
  else if (p === `/api/ai/v1/inputs/${inputId}`) body = preview();
  else if (p === `/api/ai/v1/inputs/${inputId}/files/${sourceId}`) body = { path: 'src/fixture.ts', lines: [{ kind: 'added', right_line: 1, left_line: null, text: 'return value;' }] };
  else if (p === '/api/ai/v1/jobs' && method === 'POST') { assert.equal((payload.consent as Record<string, unknown>).manifest_hash, 'c'.repeat(64)); assert.equal('credential_version' in (payload.consent as Record<string, unknown>), false); jobKeys.push(aiKey); body = { job_id: jobId, repository_id: 2 }; }
  else if (p === '/api/ai/v1/jobs') body = { jobs: [], next_cursor: null };
  else if (p === `/api/ai/v1/jobs/${jobId}`) body = { job_id: jobId, repository_id: 2, result_id: resultId, input_id: inputId, status: 'succeeded', kind: 'review', created_at: snapshot.analyzed_at };
  else if (p === `/api/ai/v1/results/${resultId}`) body = { result_id: resultId, job_id: jobId, repository_id: 2, input_id: inputId, kind: 'review', finding_count: 2, human_validation: 'unverified', coverage: preview().coverage };
  else if (p === `/api/ai/v1/results/${resultId}/findings`) body = { findings: [1, 2].map(i => ({ finding_id: `${i}6666666-6666-4666-8666-666666666666`, source_file_id: sourceId, finding: { ordinal: i, title: `Finding ${i}`, priority: i, category: 'correctness', human_validation: 'unverified', location: { path: i === 1 ? 'src/fixture.ts' : 'src/other.ts', side: 'RIGHT', start_line: 1, end_line: 1 }, condition: 'Fixture condition', impact: 'Fixture impact', verification: 'Inspect source' } })), next_cursor: null };
  else if (p === `/api/ai/v1/results/${resultId}/history`) body = { evidence: [] };
  else if (p === '/api/ai/v1/history/snapshots') body = { snapshots: [] };
  else if (p === '/api/ai/v1/shared/results') body = { results: [], next_cursor: null };

  else if (p === '/api/ai/v1/usage') body = { period_start: '2026-10-01', currency: 'USD', user_spent: '0', user_reserved: '0', user_limit: '20', user_remaining: '20', server_budget_enabled: true, uncertain_reservations: 0, daily_jobs_used: 0, daily_job_limit: 30 };
  else if (p === '/search/github-repositories' && path.includes('q=paged')) {
    const page = Number(new URL(path, 'https://fixture.test').searchParams.get('page'));
    body = { query: 'paged', total_count: 45, page, per_page: 20, results: Array.from({ length: page === 3 ? 5 : 20 }, (_, i) => ({ owner: 'fixture', repo_name: `paged-${page}-${i}`, url: `https://github.com/fixture/paged-${page}-${i}`, description: null, stars: 0, forks: 0, is_registered: false })) };
  }
  else if (p === '/search/github-repositories') body = { query: 'fixture', total_count: 1, results: [{ owner: 'fixture', repo_name: 'p03', url: repo.url, description: 'Search fixture', stars: 1, forks: 0, is_registered: true }] };
  return { status: 200, body, headers: {} };
};

void app.whenReady().then(async () => {
  await serveAssets(join(__dirname, '../ui'));
  const contracts = new RemoteContracts(JSON.parse(readFileSync(join(__dirname, '../schema/ai-bodies.json'), 'utf8')) as Record<string, Record<string, unknown>>);
  const auth = new DesktopAuth('https://fixture.test', http, async () => { throw new Error('UNEXPECTED_EXTERNAL_BROWSER'); }, { read: async () => issued, write: async () => {} });
  await auth.restore();
  const remote = new Remote(contracts, http, auth.identity, auth.invalidate, fakeVault);
  const window = createWindow(false); const unregister = registerDesktop(window, auth, remote, undefined, fakeVault);
  const unregisterLive = registerCommunityLive(window, emit => new CommunityLive(chatStream, auth.identity, auth.invalidate, emit));
  window.once('closed', unregisterLive);
  // A small site: the start page loads a stylesheet and an image, links to another page, and refers outside its folder and to a CDN.
  const collabDir = mkdtempSync(join(tmpdir(), 'gitviz-collab-e2e-'));
  mkdirSync(join(collabDir, 'css')); mkdirSync(join(collabDir, 'img'));
  writeFileSync(join(collabDir, 'index.html'), siteIndex);
  writeFileSync(join(collabDir, 'css', 'site.css'), siteCss);
  writeFileSync(join(collabDir, 'img', 'logo.png'), Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64'));
  writeFileSync(join(collabDir, 'about.html'), '<p>about</p>\r\n');
  writeFileSync(join(collabDir, 'extra.css'), 'body { border-top: 4px solid red }\r\n');
  let overwrite = false;
  const collabFile = join(collabDir, 'index.html');
  const unregisterCollab = registerCollabFiles(window, new CollabFiles(async () => collabFile, async () => overwrite),
    new CollabBundles(async () => collabFile, async () => overwrite, collabDir), new Previews(false, false),
    new CollabRunner(join(__dirname, '../../node_modules/pyodide')));
  const runtimes = await new CollabRunner(join(__dirname, '../../node_modules/pyodide')).runtimes({});
  window.webContents.setBackgroundThrottling(false);
  const unregisterHealth = registerHealth(window, unavailable);
  const errors: string[] = [];
  window.webContents.on('console-message', details => { if (details.level === 'error') errors.push(details.message.replace(/[A-Za-z]:[^\n]*/g, '[path]')); });
  async function wait(expression: string): Promise<void> {
    for (let i = 0; i < 100; i++) {
      if (await window.webContents.executeJavaScript(expression).catch((error: unknown) => { throw new Error(`wait ${String(error)} :: ${expression.slice(0, 200)}`); })) return;
      await new Promise(resolve => setTimeout(resolve, 30));
    }
    throw new Error(`ASSERTION:${phase}:${expression.slice(0, 130)}`);
  }
  async function clickText(text: string) {
    await window.webContents.executeJavaScript(`(() => { const button = [...document.querySelectorAll('button,a')].find(e => e.textContent.trim() === ${JSON.stringify(text)} && e.getClientRects().length); if(!button) throw new Error('missing action'); button.click(); })()`).catch((error: unknown) => { throw new Error(`click ${text}: ${String(error)}`); });
  }
  try {
    phase = 'search-paging';
    await window.loadURL('gitviz://app/web/pages/search.html?q=paged');
    await wait(`document.querySelectorAll('.search-card').length===20 && !document.getElementById('search-pager').hidden`);
    assert.equal(await window.webContents.executeJavaScript(`[...document.querySelectorAll('#search-pager button')].map(b=>b.textContent+(b.disabled?'-':'')).join()`), '‹ 이전-,1-,2,3,다음 ›');
    assert.equal(requests.filter(path => path.includes('q=paged')).at(-1)?.includes('page=1'), true);
    await window.webContents.executeJavaScript(`document.querySelector('#search-pager [aria-label="다음 페이지"]').click()`);
    await wait(`document.querySelector('.search-card')?.dataset.url.endsWith('paged-2-0') && document.getElementById('search-summary').textContent.includes('21–40번째') && location.search.includes('page=2')`);
    await window.webContents.executeJavaScript(`document.querySelector('#search-pager [aria-label="3페이지"]').click()`);
    await wait(`document.querySelectorAll('.search-card').length===5 && document.querySelector('#search-pager [aria-label="다음 페이지"]').disabled`);
    // Reload restores the page from the URL; a new search starts again at page 1.
    await window.loadURL('gitviz://app/web/pages/search.html?q=paged&page=2');
    await wait(`document.querySelector('.search-card')?.dataset.url.endsWith('paged-2-0')`);
    await window.webContents.executeJavaScript(`document.getElementById('search-btn').click()`);
    await wait(`document.querySelector('.search-card')?.dataset.url.endsWith('paged-1-0') && !location.search.includes('page=')`);
    phase = 'search-to-overview';
    await window.loadURL('gitviz://app/web/pages/search.html');
    await wait(`!!document.querySelector('.app-shell') && document.getElementById('nav-username')?.textContent==='fixture'`);
    await window.webContents.executeJavaScript(`document.getElementById('search-input').value='fixture'; document.getElementById('search-btn').click()`);
    await wait(`!!document.querySelector('.search-result-btn')`);
    await window.webContents.executeJavaScript(`document.querySelector('.search-result-btn').click()`);
    await wait(`location.pathname.endsWith('/overview.html') && document.getElementById('overview-files')?.textContent.includes('src/fixture.ts') && !!Chart.getChart('chart-timeline')`);
    assert.equal(requestMethods.some(method => method !== 'GET'), false);
    assert.equal(requests.filter(path => path.includes('/analytics/snapshot')).length, 1, 'snapshot metadata coalesced');
    phase = 'explicit-analysis-start-to-result';
    await window.loadURL('gitviz://app/web/pages/search.html?q=fixture');
    await wait(`!!document.querySelector('.search-start-btn') && document.getElementById('nav-username')?.textContent==='fixture'`);
    await window.webContents.executeJavaScript(`document.querySelector('.search-start-btn').click()`);
    await wait(`document.getElementById('job-modal').classList.contains('open')`);
    await wait(`document.getElementById('modal-title').textContent==='분석 완료'`);
    await clickText('결과 보기 →');
    await wait(`location.pathname.endsWith('/overview.html') && location.search.includes('snapshot=7') && !!Chart.getChart('chart-timeline')`);
    assert.equal(requestMethods.filter(method => method === 'POST').length, 1, 'one explicit analysis request');
    phase = 'partial-panel-retry'; typeFailure = true;
    await window.loadURL('gitviz://app/web/pages/overview.html?repo=2&snapshot=7');
    await wait(`!!document.querySelector('#overview-types .ui-state-error') && !!Chart.getChart('chart-timeline')`);
    typeFailure = false;
    await window.webContents.executeJavaScript(`document.querySelector('#overview-types button').click()`);
    await wait(`!!Chart.getChart('chart-donut')`);
    phase = 'contributor-file-back';
    await window.loadURL('gitviz://app/web/pages/overview.html?repo=2&snapshot=7');
    await wait(`!!document.querySelector('#tl-contrib-list button') && !!Chart.getChart('chart-timeline')`);
    await window.webContents.executeJavaScript(`document.querySelector('#tl-contrib-list button').click()`);
    await wait(`document.getElementById('dd-title').textContent==='Fixture' && document.getElementById('dd-body').textContent.includes('src/fixture.ts')`);
    await window.webContents.executeJavaScript(`document.querySelector('[data-action="__ddPushFile"]').click()`);
    await wait(`document.getElementById('dd-title').textContent==='fixture.ts'`);
    await window.webContents.executeJavaScript(`document.getElementById('dd-back-btn').click()`);
    await wait(`document.getElementById('dd-title').textContent==='Fixture'`);
    // 히트맵은 관측 구간(2026-01-01 목 ~ 2026-06-01 월, 151일) 전체: 6/1 주부터 1/1이 든 주까지 23주를 요청한다. 텍스트 보기는 없다.
    assert.equal(heatmapWeeksAsked, '23');
    assert.equal(await window.webContents.executeJavaScript(`JSON.stringify([document.getElementById('tl-heatmap-range').textContent, document.getElementById('act-heatmap').closest('.panel').textContent.includes('텍스트로 보기')])`), JSON.stringify(['관측 구간 전체 · 최근 151일', false]));
    // 결과 상단에는 '결과 조회 성공·일부만 수집·근거 제한적·점검 우선순위 (v3)' 같은 상태 칩을 두지 않는다. 확인할 항목은 '수집 범위·분석 기준' 안에 남는다.
    assert.equal(await window.webContents.executeJavaScript(`JSON.stringify([document.querySelectorAll('#analysis-context .ctx-chip').length, /결과 조회 성공|근거 제한적|일부만 수집/.test(document.getElementById('analysis-context').textContent), document.querySelector('#analysis-context .ctx-details summary').textContent.startsWith('수집 범위·분석 기준'), [...document.querySelectorAll('#analysis-context .info-table .key')].map(e => e.textContent).filter(k => ['계산 버전', '표본 부족 기준', '수집 범위 주의'].includes(k)).length, !!document.querySelector('#analysis-context .info-table .key')])`), JSON.stringify([0, false, true, 0, true]));
    // 타임라인은 관측 구간 전체의 커밋 수(막대)·활성 기여자(선)·버그 수정 비율(점선, 오른쪽 % 축)을 한 그래프에 함께 보여 준다(범위·지표 선택 없음).
    await window.webContents.executeJavaScript(`window.closeDrilldown()`);
    assert.equal(await window.webContents.executeJavaScript(`(() => { const c = Chart.getChart('chart-timeline'); return JSON.stringify([c.data.datasets.map(d => [d.type, d.yAxisID]), c.options.scales.y1.max, c.options.scales.y1.position, ['chart-timeline-contributors','chart-timeline-bugfix'].map(id => !!document.getElementById(id)), !!c.canvas.closest('.panel').querySelector('select')]); })()`), JSON.stringify([[['bar', 'y'], ['line', 'y'], ['line', 'y1']], 100, 'right', [false, false], false]));
    assert.equal(await window.webContents.executeJavaScript(`document.querySelectorAll('[aria-label="월별 지표"]').length`), 0);
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
    // 목록 정렬은 수정량 순(기본)·버그 수정 순 두 가지다. 수정량이 가장 큰 파일이 맨 위, 수정량 없는 파일은 맨 뒤.
    assert.equal(await window.webContents.executeJavaScript(`JSON.stringify([[...document.getElementById('cx-list-order').options].map(o => o.textContent), document.getElementById('cx-list-order').value, document.querySelector('#cx-list-tbody tr .cx-file-detail').textContent, document.querySelector('#cx-list-tbody tr').children[2].textContent])`), JSON.stringify([['수정량 순', '버그 수정 순'], 'churn', 'src/module-51.ts', '9,000줄']));
    await window.webContents.executeJavaScript(`document.getElementById('cx-list-next').click()`);
    assert.equal(await window.webContents.executeJavaScript(`document.querySelectorAll('#cx-list-tbody tr').length`), 2);
    assert.equal(await window.webContents.executeJavaScript(`[...document.querySelectorAll('#cx-list-tbody tr .cx-file-detail')].pop().textContent`), 'src/module-50.ts');
    await window.webContents.executeJavaScript(`(() => { const o=document.getElementById('cx-list-order'); o.value='bugfix'; o.dispatchEvent(new Event('change')); })()`);
    assert.equal(await window.webContents.executeJavaScript(`JSON.stringify([document.querySelector('#cx-list-tbody tr .cx-file-detail').textContent, document.querySelector('#cx-list-tbody tr').children[3].textContent, document.getElementById('cx-list-note').textContent.startsWith('버그 수정 순')])`), JSON.stringify(['src/module-7.ts', '40회', true]));
    // 트리 위 버튼은 정렬이 아니라 색 기준이라 '…순' 없이 지표 이름만 쓴다(이 fixture는 계산 버전이 없어 '분석 점수').
    assert.equal(await window.webContents.executeJavaScript(`[...document.querySelectorAll('#cx-sort-toggle')].map(e => e.getAttribute('aria-label') + ':' + e.textContent).join()`), '트리 색 기준:색 기준분석 점수함께 수정 정도주요 수정자 수');
    await window.webContents.executeJavaScript(`(() => { const o=document.getElementById('cx-list-order'); o.value='churn'; o.dispatchEvent(new Event('change')); })()`);
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
    phase = 'settings-app-keys';
    await window.loadURL('gitviz://app/web/pages/settings.html');
    await wait(`!!document.querySelector('.app-shell') && document.getElementById('ai-main').textContent.includes('커밋·PR 단위 분석으로 돌아가기') && !document.getElementById('ai-main').textContent.includes('저장 위치')`);
    assert.equal(await window.webContents.executeJavaScript(`!!document.querySelector('input[type="password"]')`), false, 'the server-side key form is gone');
    assert.ok(await window.webContents.executeJavaScript(`document.getElementById('ai-keys').textContent.includes('키 없음')`));
    phase = 'AI-commit-preview-consent-result';
    await window.loadURL('gitviz://app/web/pages/ai-review.html?repo=2');
    await wait(`document.getElementById('ai-main')?.textContent.includes('Fixture PR')`);
    await clickText('커밋'); await wait(`document.getElementById('ai-main').textContent.includes('Fixture commit')`);
    await clickText('파일 선택');
    await wait(`document.querySelectorAll('.ai-file input[type="checkbox"]').length>0`);
    await window.webContents.executeJavaScript(`document.querySelector('.ai-file input[type="checkbox"]').click()`); await clickText('미리보기 만들기');
    await wait(`!!document.getElementById('ai-consent-check')`);
    assert.equal(await window.webContents.executeJavaScript(`[...document.querySelectorAll('button')].find(e=>e.textContent==='코드 리뷰 생성').disabled`), true);
    assert.ok(await window.webContents.executeJavaScript(`document.querySelector('.ai-blockers').textContent.includes('앱 설정에서 openai API 키')`));
    assert.ok(await window.webContents.executeJavaScript(`!![...document.querySelectorAll('button')].find(e=>e.textContent==='API 키 저장하러 가기')`));
    appKeys.openai = 'fixture-test-value-0123456789';                           // saved through the app's own settings
    await window.webContents.executeJavaScript(`location.reload()`); await wait(`!!document.getElementById('ai-consent-check') && document.getElementById('ai-keys').textContent.includes('앱에 키 저장됨')`);
    assert.equal(await window.webContents.executeJavaScript(`document.getElementById('ai-consent-check').checked`), false, 'reload requires fresh consent');
    assert.equal(await window.webContents.executeJavaScript(`document.body.textContent.includes(${JSON.stringify(appKeys.openai)})`), false, 'renderer never shows the key');
    await window.webContents.executeJavaScript(`document.getElementById('ai-consent-check').click()`); await clickText('코드 리뷰 생성');
    await wait(`document.querySelectorAll('.ai-finding').length===2`);
    assert.deepEqual(jobKeys, [appKeys.openai], 'main attached the saved key to the job request only');
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
    phase = 'community';
    const js = (code: string) => window.webContents.executeJavaScript(code).catch((error: unknown) => { throw new Error(`${String(error)} :: ${code.slice(0, 160)}`); });
    await window.loadURL('gitviz://app/web/pages/community.html');
    await wait(`[...document.querySelectorAll('.app-nav-link')].some(a=>a.getAttribute('aria-label')==='커뮤니티' && a.getAttribute('aria-current')==='page') && !!document.querySelector('.community-list-item')`);
    assert.equal(await js(`document.querySelector('.community-list-item .community-badge').textContent`), '관리자');
    await js(`document.querySelector('.community-list-item').click()`);
    await wait(`location.search.includes('id=2') && document.querySelector('.community-head')?.textContent.includes('내 역할 관리자') && !!document.querySelector('[data-tab="invites"]')`);
    await clickText('새 글 쓰기');
    // User text is rendered as text, never as markup.
    const markup = '<img src=x>\n둘째 줄';
    await js(`(()=>{const f=document.querySelector('.community-form');f.querySelector('input').value='첫 글';f.querySelector('textarea').value=${JSON.stringify(markup)};f.requestSubmit();})()`);
    await wait(`location.search.includes('post=1') && document.querySelector('.community-text')?.textContent===${JSON.stringify(markup)} && !document.querySelector('.community-text img')`);
    await js(`(()=>{const f=document.querySelector('.community-comment-form');f.querySelector('textarea').value='댓글입니다';f.requestSubmit();})()`);
    await wait(`[...document.querySelectorAll('.community-comment .community-text')].map(e=>e.textContent).join()==='댓글입니다'`);
    await clickText('‹ 글 목록');
    await wait(`document.querySelector('.community-post-card')?.textContent.includes('댓글 1')`);
    await js(`document.querySelector('[data-tab="invites"]').click()`);
    await wait(`!!document.querySelector('.community-invite-form')`);
    await js(`(()=>{const f=document.querySelector('.community-invite-form');f.querySelectorAll('select')[1].value='5';f.requestSubmit();})()`);
    await wait(`document.querySelector('.community-invite-url')?.value.endsWith('#invite=${goodInvite}') && document.querySelectorAll('.community-invites .community-member').length===1`);
    // Group chat: history first, then live events from main; the sender's own echo shows once; markup stays text.
    await js(`document.querySelector('[data-tab="chat"]').click()`);
    await wait(`!document.querySelector('.community-chat-empty')?.hidden && document.querySelector('.community-chat-status')?.textContent==='연결하는 중…'`);
    for (let i = 0; i < 100 && !chatStreams.length; i++) await new Promise(resolve => setTimeout(resolve, 20));
    assert.deepEqual(chatStreams.map(stream => stream.path), ['/api/community/v1/repositories/2/chat/stream']);
    chatStreams[0].write(sse('ready', { online: [author, peer] }));
    await wait(`document.querySelector('.community-chat-status').textContent==='실시간 연결됨' && document.querySelector('.community-chat-online h3').textContent==='접속 중 2명'`);
    const chatMarkup = '<img src=x onerror=alert(1)>\n둘째 줄';
    await js(`(()=>{const t=document.querySelector('.community-chat-form textarea');t.value=${JSON.stringify(chatMarkup)};t.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));})()`);
    await wait(`document.querySelectorAll('.community-chat-message').length===1 && document.querySelector('.community-chat-mine .community-text')?.textContent===${JSON.stringify(chatMarkup)} && !document.querySelector('.community-chat-log img') && document.querySelector('.community-chat-form textarea').value===''`);
    // Shift+Enter is a line break, not a send.
    await js(`(()=>{const t=document.querySelector('.community-chat-form textarea');t.value='x';t.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',shiftKey:true,bubbles:true,cancelable:true}));})()`);
    chatStreams[0].write(sse('message', { id: 7, author: peer, author_id: peer.id, body: '안녕하세요', created_at: snapshot.analyzed_at }));
    await wait(`document.querySelectorAll('.community-chat-message').length===2 && document.querySelector('.community-chat-message:last-child')?.textContent.includes('@peer') && !document.querySelector('.community-chat-message:last-child').classList.contains('community-chat-mine')`);
    chatStreams[0].write(sse('deleted', { id: 7 }) + sse('presence', { online: [author] }) + sse('script', { online: [] }));
    await wait(`document.querySelectorAll('.community-chat-message').length===1 && document.querySelector('.community-chat-online h3').textContent==='접속 중 1명'`);
    assert.equal(chatMessages.length, 1, 'Shift+Enter did not send');
    // Voice: one seat is taken by the peer; the camera stays denied while the microphone is allowed on this page.
    phase = 'community-voice';
    await wait(`document.querySelector('.community-voice h3')?.textContent==='음성 채팅 1/3' && document.querySelector('.community-voice-people').textContent.includes('@peer')`);
    assert.equal(await js(`navigator.mediaDevices.getUserMedia({ video: true }).then(s => { s.getTracks().forEach(t => t.stop()); return 'allowed'; }, e => e.name)`), 'NotAllowedError');
    voicePeer = new BrowserWindow({ show: false, webPreferences: { partition: 'voice-peer-fixture', sandbox: true, contextIsolation: true, nodeIntegration: false } });
    await voicePeer.loadURL('about:blank');
    await voicePeer.webContents.executeJavaScript(`(() => {
      window.out = []; window.chain = Promise.resolve();
      const ctx = new AudioContext(); const tone = ctx.createOscillator(); const sink = ctx.createMediaStreamDestination(); tone.connect(sink); tone.start();
      const pc = window.pc = new RTCPeerConnection();
      for (const track of sink.stream.getTracks()) pc.addTrack(track, sink.stream);
      pc.ontrack = event => { window.heard = event.track.kind; };
      pc.onicecandidate = event => { if (event.candidate) window.out.push({ kind: 'ice', candidate: event.candidate.candidate, sdp_mid: event.candidate.sdpMid, sdp_mline_index: event.candidate.sdpMLineIndex }); };
      const handle = async m => {
        if (m.kind === 'offer') { await pc.setRemoteDescription({ type: 'offer', sdp: m.sdp }); await pc.setLocalDescription(await pc.createAnswer()); window.out.push({ kind: 'answer', sdp: pc.localDescription.sdp }); }
        else if (m.kind === 'ice') await pc.addIceCandidate({ candidate: m.candidate, sdpMid: m.sdp_mid, sdpMLineIndex: m.sdp_mline_index });
      };
      window.receive = m => { window.chain = window.chain.then(() => handle(m)).catch(e => { window.failed = String(e); }); };
    })()`);
    // Relay the peer's answer and candidates into the app's chat stream, addressed to seat A.
    const pump = setInterval(() => {
      void voicePeer?.webContents.executeJavaScript('window.out.splice(0)').then((out: Record<string, unknown>[]) => {
        for (const message of out) chatStreams.at(-1)?.write(sse('voice-signal', { from: seatB, to: seatA, ...message }));
      }).catch(() => {});
    }, 25);
    try {
      await clickText('음성 참여');
      await wait(`document.querySelector('.community-voice h3').textContent==='음성 채팅 2/3' && document.querySelector('.community-voice-people').textContent.includes('(나)')`);
      for (let i = 0; i < 200 && !(await voicePeer.webContents.executeJavaScript(`window.pc.connectionState==='connected' && window.heard==='audio'`)); i++) await new Promise(resolve => setTimeout(resolve, 25));
      assert.equal(await voicePeer.webContents.executeJavaScript('window.failed || window.pc.connectionState'), 'connected');
      assert.equal(await voicePeer.webContents.executeJavaScript('window.heard'), 'audio', 'the peer receives the app microphone');
      await wait(`[...document.querySelectorAll('.community-voice audio')].some(a => a.srcObject?.getAudioTracks()[0]?.readyState === 'live')`);
      assert.deepEqual([voiceSignals[0].session_id, voiceSignals[0].to, voiceSignals[0].kind], [seatA, seatB, 'offer'], 'the newcomer calls the seats already there');
      assert.ok(voiceSignals.some(signal => signal.kind === 'ice'));
      await clickText('음소거');
      await wait(`document.querySelector('.community-voice [aria-pressed="true"]')?.textContent==='음소거 해제'`);
      await clickText('나가기');
      await wait(`document.querySelector('.community-voice h3').textContent==='음성 채팅 1/3' && !document.querySelector('.community-voice audio') && document.querySelector('.community-voice-status').textContent==='음성 채팅에서 나왔습니다.'`);
      assert.equal(voiceLeft, 1);
      // A full room says so and keeps nothing open.
      voiceFull = true;
      await clickText('음성 참여');
      await wait(`document.querySelector('.community-voice-status').textContent.includes('3명까지') && !!document.querySelector('.community-voice .btn-primary') && !document.querySelector('.community-voice audio')`);
    } finally { clearInterval(pump); voicePeer.destroy(); voicePeer = null; }
    // Co-editing (AD-34): the app shares a start file and what it loads, edits it with a real second editor, previews it,
    // approves a reference the peer added; then it joins the peer's share as a guest.
    phase = 'community-collab';
    await js(`document.querySelector('[data-tab="collab"]').click()`);
    for (let i = 0; i < 100 && chatStreams.length < 2; i++) await new Promise(resolve => setTimeout(resolve, 20));
    chatStreams[1].write(sse('ready', { online: [author, peer] }));
    await wait(`document.querySelector('.community-collab-list')?.textContent==='진행 중인 공동 편집이 없습니다.'`);
    collabPeer = new BrowserWindow({ show: false, webPreferences: { partition: 'collab-peer-fixture', sandbox: true, contextIsolation: true, nodeIntegration: false } });
    await collabPeer.loadURL('about:blank');
    // The bundle's last statement evaluates to the module object, which cannot cross back; end with undefined.
    await collabPeer.webContents.executeJavaScript(`${readFileSync(join(__dirname, '../ui/web/js/collab-editor.bundle.js'), 'utf8')}\n;void 0`);
    await collabPeer.webContents.executeJavaScript(`(() => {
      const lib = window.GitVizCollab; window.out = []; window.assetPieces = 0;
      const b64 = bytes => { let s = ''; for (const b of bytes) s += String.fromCharCode(b); return btoa(s); };
      const raw = text => Uint8Array.from(atob(text), c => c.charCodeAt(0));
      let manifest, files, hosting;
      const push = (kind, data, to, path) => window.out.push({ kind, data: b64(data), ...(to ? { to } : {}), ...(path ? { path } : {}) });
      const watch = (path, doc) => doc.on('update', (update, origin) => { if (origin !== lib.REMOTE) push('update', update, null, path); });
      const addFile = (path, text) => {
        if (files[path]) return files[path];
        const file = files[path] = lib.createFile(text);
        watch(path, file.doc);
        file.awareness.on('update', (_change, origin) => { if (origin !== lib.REMOTE) push('awareness', lib.encodeAwareness(file.awareness, [file.doc.clientID]), null, path); });
        lib.setPresence(file.awareness, { name: '@peer', color: '#db2777' });
        return file;
      };
      window.start = text => {
        manifest = lib.createManifest(); files = {}; hosting = text !== null; watch('', manifest.doc);
        if (hosting) {
          manifest.doc.transact(() => { manifest.files.set('app.py', 'python'); manifest.files.set('main.c', 'c'); manifest.meta.set('entry', 'app.py'); });
          addFile('app.py', text); addFile('main.c', '#include <stdio.h>\\nint main(void) { printf("%d\\\\n", 6 * 7); return 0; }\\n');
        }
      };
      window.receive = m => {
        const path = m.path || ''; const bytes = raw(m.data);
        if (!path) {
          if (m.kind === 'sync-request') { if (hosting) push('sync', lib.encodeStateAsUpdate(manifest.doc, bytes), m.from); return; }
          if (m.kind !== 'update' && m.kind !== 'sync') return;
          lib.applyUpdate(manifest.doc, bytes);
          for (const name of manifest.files.keys()) if (!files[name]) push('sync-request', lib.encodeStateVector(addFile(name, null).doc), m.from, name);
          return;
        }
        if (m.kind === 'asset') { window.assetPieces++; return; }
        const file = files[path] || (manifest.files.has(path) ? addFile(path, null) : null);
        if (!file) return;
        if (m.kind === 'update' || m.kind === 'sync') lib.applyUpdate(file.doc, bytes);
        else if (m.kind === 'awareness') lib.applyAwareness(file.awareness, bytes);
        else if (m.kind === 'sync-request') push('sync', lib.encodeStateAsUpdate(file.doc, bytes), m.from, path);
      };
      window.request = to => push('sync-request', lib.encodeStateVector(manifest.doc), to);
      window.requestAsset = (to, path) => push('asset-request', new Uint8Array([1]), to, path);
      window.listing = () => [...manifest.files.keys()].sort().join() + '|' + [...manifest.assets.keys()].sort().join();
      window.text = path => files[path] ? files[path].ytext.toString() : null;
      window.append = (path, text) => files[path].ytext.insert(files[path].ytext.length, text);
      window.names = path => files[path] ? [...files[path].awareness.getStates().values()].map(s => s.user && s.user.name).filter(Boolean).sort().join() : '';
    })()`);
    const peerJs = (code: string) => collabPeer!.webContents.executeJavaScript(code).catch((error: unknown) => { throw new Error(`peer ${String(error)} :: ${code.slice(0, 120)}`); });
    async function peerWait(expression: string, expected: unknown): Promise<void> {
      for (let i = 0; i < 150 && (await peerJs(expression)) !== expected; i++) await new Promise(resolve => setTimeout(resolve, 20));
      assert.equal(await peerJs(expression), expected, `peer ${expression}`);
    }
    const collabPump = setInterval(() => {
      void collabPeer?.webContents.executeJavaScript('window.out.splice(0)').then((out: { kind: string; data: string; to?: string; path?: string }[]) => {
        for (const m of out) chatStreams.at(-1)?.write(sse('collab', { session_id: collabSid, from: seatB, to: m.to ?? null, kind: m.kind, data: m.data, ...(m.path ? { path: m.path } : {}) }));
      }).catch(() => {});
    }, 25);
    const appText = `[...document.querySelectorAll('.community-collab-editor .cm-line')].map(line => line.textContent).join('\\n')`;
    const previewWindow = () => BrowserWindow.getAllWindows().find(item => item !== window && item !== collabPeer && !item.isDestroyed()) ?? null;
    try {
      await peerJs('window.start(null)');
      await clickText('내 파일 공유하기');
      // The host reviews the set first: references followed, the outside one and the CDN listed, a page link offered as a candidate.
      await wait(`document.querySelector('.community-collab-head')?.textContent.includes('공유할 묶음 확인 · index.html') && document.querySelectorAll('.community-collab-files li').length===3`);
      assert.equal(await js(`[...document.querySelectorAll('.community-collab-files li')].map(li => li.textContent.split(' · ')[0]).join()`), 'index.html,css/site.css,img/logo.png');
      assert.ok(!(await js('document.body.textContent')).includes(collabDir), 'no absolute path on the page');
      await js(`document.querySelectorAll('.community-collab-report').forEach(box => { box.open = true; })`);
      assert.ok((await js(`document.querySelector('.community-collab').textContent`)).includes('../outside.css ← index.html · 공유 범위 밖'));
      assert.ok((await js(`document.querySelector('.community-collab').textContent`)).includes('cdn.example.org'));
      await js(`[...document.querySelectorAll('.community-collab-report button')].find(b => b.textContent === '추가').click()`);
      await wait(`document.querySelectorAll('.community-collab-files li').length===4 && document.querySelector('.community-collab-files').textContent.includes('about.html')`);
      await clickText('공유 시작');
      await wait(`document.querySelector('.community-collab-status')?.textContent.startsWith('파일을 공유했습니다') && ${appText}===${JSON.stringify(siteIndex.replace(/\r\n/g, '\n'))}`);
      // The peer joins, gets the file list, each text file and the image (in pieces, only to its seat).
      collabSessions = [collabSession(true, [seatA, seatB])];
      chatStreams[1].write(sse('collab-sessions', { sessions: collabSessions, ended: [] }));
      await wait(`document.querySelector('.community-collab-people')?.textContent.includes('@peer')`);
      await peerJs(`window.request(${JSON.stringify(seatA)})`);
      await peerWait('window.listing()', 'about.html,css/site.css,index.html|img/logo.png');
      await peerWait(`window.text('css/site.css')`, siteCss.replace(/\r\n/g, '\n'));
      await peerJs(`window.requestAsset(${JSON.stringify(seatA)}, 'img/logo.png')`);
      await peerWait('window.assetPieces', 1);
      await peerWait(`window.names('index.html')`, '@fixture,@peer');
      // Both edit index.html; the peer also adds a stylesheet reference, which waits for the host's approval.
      await peerJs(`window.append('index.html', '<link rel="stylesheet" href="extra.css">\\n')`);
      await wait(`${appText}.endsWith('<link rel="stylesheet" href="extra.css">\\n')`);
      await js(`(() => { const content = document.querySelector('.community-collab-editor .cm-content'); content.focus(); document.execCommand('insertText', false, '<!-- a -->'); })()`);
      const merged = `<!-- a -->${siteIndex.replace(/\r\n/g, '\n')}<link rel="stylesheet" href="extra.css">\n`;
      await peerWait(`window.text('index.html')`, merged);
      await wait(`document.querySelector('.community-collab-pending')?.textContent.includes('extra.css')`);
      assert.equal(await peerJs('window.listing()'), 'about.html,css/site.css,index.html|img/logo.png', 'nothing is read before the host approves');
      await js(`[...document.querySelectorAll('.community-collab-pending button')].find(b => b.textContent === '추가').click()`);
      await peerWait('window.listing()', 'about.html,css/site.css,extra.css,index.html|img/logo.png');
      // The preview renders the shared state with its separate CSS and image; plain http and private addresses are refused.
      await clickText('미리보기');
      for (let i = 0; i < 100 && !previewWindow(); i++) await new Promise(resolve => setTimeout(resolve, 30));
      const preview = previewWindow();
      assert.ok(preview, 'preview window');
      const previewJs = (code: string) => preview.webContents.executeJavaScript(code);
      for (let i = 0; i < 150; i++) {
        try { if (await previewJs(`getComputedStyle(document.body).color==='rgb(1, 2, 3)' && getComputedStyle(document.body).borderTopWidth==='4px' && document.querySelector('img')?.naturalWidth===1`)) break; }
        catch { /* still loading */ }
        await new Promise(resolve => setTimeout(resolve, 30));
      }
      assert.equal(await previewJs(`getComputedStyle(document.body).color + '|' + getComputedStyle(document.body).borderTopWidth + '|' + document.querySelector('img').naturalWidth`), 'rgb(1, 2, 3)|4px|1');
      assert.equal(await previewJs(`fetch('http://example.org/').then(() => 'loaded', () => 'blocked')`), 'blocked');
      assert.equal(await previewJs(`fetch('https://127.0.0.1/').then(() => 'loaded', () => 'blocked')`), 'blocked');
      assert.equal(await previewJs(`fetch('gitviz://app/index.html').then(() => 'loaded', () => 'blocked')`), 'blocked');
      assert.equal(await previewJs(`typeof window.desktop + typeof window.workspace + typeof require`), 'undefinedundefinedundefined');
      // Save writes every shared text file with its own line endings; a file changed on disk is kept unless the host confirms.
      await clickText('저장');
      await wait(`document.querySelector('.community-collab-status').textContent.startsWith('저장했습니다')`);
      assert.equal(readFileSync(join(collabDir, 'index.html'), 'utf8'), merged.replace(/\n/g, '\r\n'));
      writeFileSync(join(collabDir, 'index.html'), 'changed\r\n');
      await clickText('저장');
      await wait(`document.querySelector('.community-collab-status').textContent.includes('다른 곳에서 바뀌어 저장하지 않은 파일: index.html')`);
      assert.equal(readFileSync(join(collabDir, 'index.html'), 'utf8'), 'changed\r\n');
      overwrite = true;
      await clickText('저장');
      await wait(`document.querySelector('.community-collab-status').textContent.startsWith('저장했습니다')`);
      assert.equal(readFileSync(join(collabDir, 'index.html'), 'utf8'), merged.replace(/\n/g, '\r\n'));
      assert.ok(collabRelays.length > 0 && collabRelays.every(relay => relay.seat_id === seatA && !JSON.stringify(relay).includes('gitviz-collab-e2e')));
      assert.ok(collabRelays.filter(relay => relay.kind === 'sync' || relay.kind === 'asset').every(relay => relay.to === seatB));
      assert.ok(communityBodies.some(body => JSON.stringify(body) === JSON.stringify({ file_name: 'index.html', language: 'html', file_count: 4 })), 'only the start file name and the count go to the server');
      // New files: a reference the peer adds to a missing file can be created, and the host can name a new file; save writes both.
      phase = 'collab-new-missing';
      await peerJs(`window.append('index.html', '<script src="js/app2.js"></script>\\n')`);
      await wait(`[...document.querySelectorAll('.community-collab-report summary')].some(s => s.textContent.startsWith('없는 파일을 참조함')) && document.querySelector('.community-collab').textContent.includes('js/app2.js')`);
      await js(`[...document.querySelectorAll('.community-collab-report button')].find(b => b.textContent === '만들기').click()`);
      await wait(`[...document.querySelectorAll('.community-collab-file')].some(b => b.textContent === 'js/app2.js (새 파일)')`);
      await peerWait('window.listing()', 'about.html,css/site.css,extra.css,index.html,js/app2.js|img/logo.png');
      phase = 'collab-new-named';
      await clickText('새 파일');
      await js(`(() => { const input = document.querySelector('.community-collab-create-input'); input.value = '../escape.txt'; input.form.requestSubmit(); })()`);
      await wait(`document.querySelector('.community-collab-status').textContent.startsWith('공유할 수 없는 파일')`);
      await js(`(() => { const input = document.querySelector('.community-collab-create-input'); input.value = 'notes/todo.txt'; input.form.requestSubmit(); })()`);
      await wait(`[...document.querySelectorAll('.community-collab-file')].some(b => b.textContent === 'notes/todo.txt (새 파일)')`);
      await js(`(() => { const content = document.querySelector('.community-collab-editor .cm-content'); content.focus(); document.execCommand('insertText', false, '할 일'); })()`);
      await peerWait(`window.text('notes/todo.txt')`, '할 일');
      assert.ok(!existsSync(join(collabDir, 'notes')), 'nothing is written before saving');
      await clickText('저장');
      await wait(`document.querySelector('.community-collab-status').textContent.startsWith('저장했습니다') && ![...document.querySelectorAll('.community-collab-file')].some(b => b.textContent.endsWith('(새 파일)'))`);
      assert.equal(readFileSync(join(collabDir, 'notes', 'todo.txt'), 'utf8'), '할 일');
      assert.equal(readFileSync(join(collabDir, 'js', 'app2.js'), 'utf8'), '');
      await clickText('공유 끝내기');
      await clickText('공유 끝내기 확인');
      await wait(`document.querySelector('.community-collab-status')?.textContent==='공유를 끝냈습니다.' && !document.querySelector('.community-collab-editor') && document.querySelector('.community-collab-list').textContent==='진행 중인 공동 편집이 없습니다.'`);
      for (let i = 0; i < 100 && previewWindow(); i++) await new Promise(resolve => setTimeout(resolve, 30));
      assert.equal(previewWindow(), null, 'ending the share closes the preview');
      assert.equal(collabLeft, 1);
      // As a guest the editor stays read-only until the host's state arrives; the host ending the share keeps the text, read-only.
      await peerJs(`window.start('x = 1\\n')`);
      collabSessions = [collabSession(false, [seatB])];
      chatStreams[1].write(sse('collab-sessions', { sessions: collabSessions, ended: [] }));
      await wait(`document.querySelector('.community-collab-card')?.textContent.includes('@peer 공유 · 1/3명')`);
      await js(`document.querySelector('.community-collab-card button').click()`);
      await wait(`document.querySelector('.community-collab-status')?.textContent.startsWith('함께 편집하는 중') && ${appText}==='x = 1\\n' && document.querySelector('.community-collab-editor .cm-content').contentEditable==='true'`);
      assert.deepEqual(collabRelays.filter(relay => relay.kind === 'sync-request').map(relay => [relay.to, relay.path ?? '']), [[seatB, ''], [seatB, 'app.py'], [seatB, 'main.c']]);
      await peerWait(`window.names('app.py')`, '@fixture,@peer');
      // Anyone can run: Python runs in the app's sandbox without asking; the output stays here.
      phase = 'collab-run-python';
      await peerJs(`window.append('app.py', 'print("from peer", x, input())\\n')`);
      await wait(`${appText}.endsWith('input())\\n') && !document.querySelector('.community-collab-run').hidden`);
      await js(`(() => { const box = document.querySelector('.community-collab-stdin-input'); box.value = 'abc'; })()`);
      const relaysBeforeRun = collabRelays.length;
      await clickText('실행');
      await wait(`document.querySelector('.community-collab-stdout')?.textContent==='from peer 1 abc\\n' && document.querySelector('.community-collab-run-summary').textContent.startsWith('끝났습니다 · 종료 코드 0')`);
      assert.ok(collabRelays.slice(relaysBeforeRun).every(relay => relay.kind !== 'update' || relay.path !== 'app.py'), 'running sends nothing');
      // Native code written by someone else asks first and shows what changed since my last run.
      phase = 'collab-run-c';
      await js(`[...document.querySelectorAll('.community-collab-file')].find(b => b.textContent === 'main.c').click()`);
      await wait(`document.querySelector('.community-collab-run-head')?.textContent.startsWith('실행 · main.c')`);
      if (runtimes.c) {
        await clickText('실행');
        await wait(`document.querySelector('.community-collab-confirm')?.textContent.includes('main.c · 처음 실행(전체)')`);
        await clickText('확인하고 실행');
        await wait(`document.querySelector('.community-collab-stdout')?.textContent.replace(/\\r/g, '')==='42\\n'`);
        await clickText('실행');
        await wait(`!document.querySelector('.community-collab-confirm') && document.querySelector('.community-collab-run-summary')?.textContent.startsWith('끝났습니다')`);
        await peerJs(`window.append('main.c', '// changed by peer\\n')`);
        await wait(`${appText}.includes('// changed by peer')`);
        await clickText('실행');
        await wait(`document.querySelector('.community-collab-confirm')?.textContent.includes('+ // changed by peer')`);
        await clickText('취소');
        await wait(`!document.querySelector('.community-collab-confirm')`);
      } else assert.ok((await js(`document.querySelector('.community-collab-run').textContent`)).includes('gcc 또는 clang'));
      collabSessions = [];
      chatStreams[1].write(sse('collab-sessions', { sessions: [], ended: [collabSid] }));
      await wait(`document.querySelector('.community-collab-status')?.textContent.startsWith('호스트가 공유를 끝냈습니다') && document.querySelector('.community-collab-editor .cm-content').contentEditable==='false' && ${appText}.startsWith('#include <stdio.h>')`);
      await clickText('목록으로');
      await wait(`!document.querySelector('.community-collab-editor') && !!document.querySelector('.community-collab-list')`);
    } finally { clearInterval(collabPump); collabPeer.destroy(); collabPeer = null; }
    phase = 'community';
    await js(`document.querySelector('[data-tab="members"]').click()`);
    await wait(`document.querySelectorAll('.community-member').length===2`);
    // Leaving the chat and co-editing tabs closes their streams in main.
    for (let i = 0; i < 100 && !chatStreams.every(stream => stream.cancelled()); i++) await new Promise(resolve => setTimeout(resolve, 20));
    assert.equal(chatStreams.every(stream => stream.cancelled()), true, 'the chat stream ends when the tab closes');
    // Handing over the admin role takes two clicks; afterwards the admin-only invites tab is gone.
    await clickText('관리자 넘기기');
    assert.equal(communityAdmin, uid);
    await clickText('관리자 넘기기 확인');
    await wait(`document.querySelector('.community-head')?.textContent.includes('내 역할 멤버') && !document.querySelector('[data-tab="invites"]')`);
    assert.equal(communityAdmin, peer.id);
    // A pasted invite link joins and opens the community; a bad one explains itself.
    const paste = (value: string) => js(`document.getElementById('community-invite-input').value=${JSON.stringify(value)};document.getElementById('community-join').requestSubmit()`);
    await paste(`https://fixture.test/pages/community.html#invite=${'z'.repeat(43)}`);
    await wait(`document.getElementById('community-join-message').textContent.includes('만료됐거나')`);
    await paste(`https://fixture.test/pages/community.html#invite=${goodInvite}`);
    await wait(`document.getElementById('community-join-message').textContent==='참여했습니다.' && location.search.includes('id=2')`);
    // A gitviz-join link (main turns it into ?invite=) only fills the form: no request until the user presses 참여.
    const joinsBefore = requests.filter(path => path.includes('/invites/redeem')).length;
    await window.loadURL(`gitviz://app/web/pages/community.html?invite=${goodInvite}`);
    await wait(`document.getElementById('community-invite-input')?.value===${JSON.stringify(goodInvite)} && document.getElementById('community-join-message').textContent.includes('참여를 누르면') && !location.search.includes('invite=')`);
    assert.equal(requests.filter(path => path.includes('/invites/redeem')).length, joinsBefore);
    assert.equal(await js(`document.getElementById('community-open-app').hidden`), true, 'the app never offers to open itself');
    await js(`document.getElementById('community-join').requestSubmit()`);
    await wait(`document.getElementById('community-join-message').textContent==='참여했습니다.'`);
    assert.deepEqual(communityBodies.slice(0, 3),[{ title: '첫 글', body: markup }, { body: '댓글입니다' }, { expires_in_hours: 168, max_uses: 5 }]);
    assert.equal(errors.length, 0, `renderer errors: ${errors.join(',')}`);
    process.stdout.write('PASS Phase 3 renderer: search/paging/selection/overview, community posts/comments/chat/voice (real WebRTC peer)/co-editing file set (real Yjs peer, references, host approval, sandboxed preview, new files, host save, guest sync, Python run, confirmed C run)/invites/admin handover, partial retry, contributor-file-back, monthly metric, graph zoom/filter/selection, file pagination/search/view restoration, app-held AI key status, commit/PR selection, preview/fresh consent, review result/filter/code. Fixture transport only.\n');
  } finally { unregisterCollab(); unregisterHealth(); unregister(); window.destroy(); rmSync(collabDir, { recursive: true, force: true }); clearTimeout(deadline); }
}).then(() => app.exit(0)).catch(error => {
  if (process.env.GITVIZ_UI_REPORT) writeFileSync(process.env.GITVIZ_UI_REPORT, JSON.stringify({ status: 'failed', phase, message: error instanceof Error ? error.message : 'assertion' }));
  process.stderr.write(`FAIL Phase 3 renderer (${phase}): ${error instanceof Error ? error.message.slice(0, 160) : 'assertion'}\n`); app.exit(1);
});
