import './profile';
import assert from 'node:assert/strict';
import { app, safeStorage } from 'electron';
import { execFileSync } from 'node:child_process';
import { mkdtemp, mkdir, writeFile, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { Agent } from '../src/agent';
import { Contracts } from '../src/contracts';
import { DesktopAuth } from '../src/auth';
import { Remote, type Http } from '../src/remote';
import { RemoteContracts } from '../src/remote-contracts';
import { RemoteWorkspace } from '../src/remote-workspace';
import { registerRemoteWorkspace } from '../src/remote-workspace-ipc';
import { registerProjects } from '../src/projects';
import { registerHealth } from '../src/ipc';
import { registerDesktop } from '../src/desktop-ipc';
import { registerNotifications } from '../src/notifications';
import { registerScheme, serveAssets, APP_URL } from '../src/app-protocol';
import { createWindow } from '../src/window';
import { loadKey } from '../src/secrets';
import { CloudReview } from '../src/cloud-review';
import { createHash } from 'node:crypto';
import { registerImpact } from '../src/impact';
import { HistoricalImpact } from '../src/historical-impact';
import { registerDiagnostics } from '../src/diagnostics-ipc';
import { cloudRecovery } from './cloud-recovery';

registerScheme(); app.on('window-all-closed', () => {});
let phase = 'startup';
const deadline = setTimeout(() => { process.stderr.write(`FAIL P08b deadline ${phase}\n`); app.exit(1); }, 90000);
void app.whenReady().then(async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'gitviz-p08b-'));
  const root = join(temporary, '한글 remote fixture'); await mkdir(root);
  const git = (...args: string[]) => execFileSync(join(process.env.ProgramFiles ?? 'C:/Program Files', 'Git/cmd/git.exe'), ['-C', root, ...args], {
    env: { SystemRoot: process.env.SystemRoot, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: 'NUL' }, stdio: 'pipe', windowsHide: true });
  git('init', '-b', 'main'); git('config', 'user.name', 'fixture'); git('config', 'user.email', 'fixture@example.invalid');
  await writeFile(join(root, 'app.py'), 'value = 1\n'); git('add', '.'); git('commit', '-m', 'fixture'); git('remote', 'add', 'origin', 'git@github.com:o/r.git');
  const user = { id: '11111111-1111-4111-8111-111111111111', github_login: 'fixture', display_name: null };
  const issued = { user, sessionId: user.id, sessionToken: 'a'.repeat(64), expiresAt: new Date(Date.now() + 300000).toISOString(), capabilities: ['remote-v1'], apiCapabilities: { desktopProtocol: 1, aiContract: '1.9' } };
  let allowed = false, switchAccount = false, denied = false, job = 3, status = 'done', githubId = 123, visibility = 'public';
  let reviewAllowed = false, modelCalls = 0, approvedHash = '', snapshotHead = '';
  let diagnosticPath: string | null = null;
  const byok = 'fixture-only-byok-never-in-agent-123456';
  const http: Http = async (path, method) => {
    assert.equal(method, 'GET', 'P08b never mutates server data');
    if (path === '/api/desktop/v1/session') return { status: 200, headers: {}, body: issued };
    if (path === '/api/desktop/v1/repositories/2/identity') return { status: denied ? 404 : 200, headers: {}, body: { accountId: user.id, repositoryId: 2, githubId, canonicalUrl: 'https://github.com/o/r', visibility } };
    if (path === '/repositories/2/status') return { status: 200, headers: {}, body: { job_id: job, status, snapshot_id: status === 'done' ? 7 : null } };
    if (path === '/repositories/2/analytics/snapshot') return { status: 200, headers: {}, body: { snapshot: { id: 7, target_sha: snapshotHead,
      payload_sha256: 'c'.repeat(64), formula_version: 'fixture-v1', window_start: null, window_end: '2026-10-28T00:00:00Z' } } };
    if (path === '/repositories/2/analytics/coupling?snapshot_id=7&limit=200&min_score=0&sort_by=coupling') return { status: 200, headers: {}, body: {
      _snapshot: { id: 7, target_sha: snapshotHead, payload_sha256: 'c'.repeat(64) },
      meta: { co_change_unit: 'pr', total_units: 10, excluded_unit_count: 1, total_pairs: 1 }, pairs: [{ file_a_path: 'app.py', file_b_path: 'related.py',
        co_change_count: 3, change_count_a: 5, change_count_b: 6, a_to_b_ratio: .6, b_to_a_ratio: .5, coupling_score: .6, low_support: true }] } };
    if (path === '/api/auth/me') return { status: 200, headers: {}, body: user };
    return { status: 200, headers: {}, body: [] };
  };
  const auth = new DesktopAuth('https://fixture.test', http, async () => { throw new Error('UNEXPECTED_BROWSER'); }, { read: async () => issued, write: async () => {} });
  await auth.restore();
  const contracts = new Contracts(join(__dirname, '../schema')), agent = new Agent(contracts);
  const originalCall = agent.call.bind(agent);
  agent.call = async (operation, payload, options) => { assert.equal(JSON.stringify(payload).includes(byok), false); return await originalCall(operation, payload, options); };
  const remote = new Remote(new RemoteContracts(JSON.parse(await readFile(join(__dirname, '../schema/ai-bodies.json'), 'utf8')) as Record<string, Record<string, unknown>>), http, auth.identity, auth.invalidate);
  const window = createWindow(false), evaluate = (source: string) => window.webContents.executeJavaScript(source);
  const coordinator = new RemoteWorkspace(agent, auth, remote, 'https://fixture.test', async detail => {
    assert.match(detail, /GitHub ID: 123/); assert.equal(detail.includes(root), true); if (switchAccount) auth.invalidate(); return allowed;
  });
  const cloud = new CloudReview(agent, coordinator, () => auth.identity()?.generation ?? null, async (profile, bytes, key) => {
    assert.equal(profile, 'gpt-5.4-v1'); assert.equal(key, byok);
    assert.equal(createHash('sha256').update(bytes).digest('hex'), approvedHash); modelCalls++;
    const body = { model: 'gpt-5.4-2026-03-05', status: 'completed', output: [{ type: 'message', content: [{ type: 'output_text',
      text: JSON.stringify({ schemaVersion: 'workspace-review-v1', findings: [], limitations: ['Fake provider; no model quality assertion.'] }) }] }], usage: { input_tokens: 100, output_tokens: 50 } };
    return { httpStatus: 200, rawBase64: Buffer.from(JSON.stringify(body)).toString('base64'), errorCode: null, uncertain: false };
  });
  const cleanup = [registerHealth(window, () => agent.health()), registerProjects(window, agent, contracts, { select: async () => root, confirm: async (_window, message, detail) => {
    if (message.includes('AI에')) { assert.match(detail, /최대 비용 예약 추정 USD/); return reviewAllowed; } return true;
  } }, cloud),
    registerDesktop(window, auth, remote), registerNotifications(window, agent, contracts), registerRemoteWorkspace(window, coordinator),
    registerImpact(window, agent, contracts, new HistoricalImpact(agent, coordinator, remote)), registerDiagnostics(window, () => agent.health(), async () => diagnosticPath)];
  const directory = join(temporary, 'data');
  try {
    const executable = app.isPackaged ? join(process.resourcesPath, 'agent/gitviz-workspace-agent.exe') : join(__dirname, '../../../build-output/agent/dist/agent/gitviz-workspace-agent.exe');
    await serveAssets(join(__dirname, '../ui'));
    await agent.start(executable, directory, () => loadKey(join(directory, 'key.dpapi'), { available: () => safeStorage.isAsyncEncryptionAvailable(), encrypt: value => safeStorage.encryptStringAsync(value), decrypt: async value => (await safeStorage.decryptStringAsync(value)).result }));
    await window.loadURL(APP_URL);
    const project = await evaluate('workspace.selectAndRegister()');
    const connect = () => evaluate(`workspace.connectRemote({projectId:${JSON.stringify(project.id)},repositoryId:2})`);
    phase = 'cancel'; assert.equal(await connect(), null);
    phase = 'forged-proof'; await assert.rejects(evaluate(`workspace.connectRemote({projectId:${JSON.stringify(project.id)},repositoryId:2,accountId:'forged'})`));
    phase = 'account-change'; allowed = true; switchAccount = true; await assert.rejects(connect(), /ACCOUNT_CHANGED/);
    await auth.restore(); switchAccount = false;
    phase = 'connect'; assert.equal((await connect()).projectId, project.id);
    const filters = { projectIds: null, types: null, severities: null, read: 'all' };
    const inbox = () => evaluate(`workspace.listNotifications(${JSON.stringify({ filters, cursor: null, limit: 30 })})`);
    phase = 'baseline'; await coordinator.poll(); assert.equal((await inbox()).items.length, 0);
    phase = 'transition'; job = 4; status = 'running'; await coordinator.poll(); status = 'done'; await coordinator.poll(); await coordinator.poll();
    const page = await inbox(); assert.equal(page.items.length, 1); assert.equal(page.items[0].type, 'analysis.succeeded'); assert.equal(page.items[0].count, 1);
    const analysisId = page.items[0].target.resourceId;
    phase = 'cloud-private-block';
    const snapshot = await evaluate(`workspace.captureChanges({projectId:${JSON.stringify(project.id)},items:[{facet:'selected',path:'app.py'}],includeUntracked:false,idempotencyKey:crypto.randomUUID()})`);
    snapshotHead = snapshot.head;
    const previewCode = `workspace.previewReview({projectId:${JSON.stringify(project.id)},snapshotId:${JSON.stringify(snapshot.snapshotId)},kind:'review',purpose:'Review fixture',profileId:'gpt-5.4-v1',generation:1,apiKey:${JSON.stringify(byok)}})`;
    visibility = 'private'; await assert.rejects(evaluate(previewCode), /CLOUD_MAPPING_REQUIRED/); assert.equal(modelCalls, 0); visibility = 'public';
    phase = 'cloud-native-cancel'; const preview = await evaluate(previewCode); approvedHash = preview.requestHash;
    assert.equal(JSON.stringify(preview).includes(byok), false);
    const start = { projectId: project.id, previewId: preview.previewId, previewHash: preview.previewHash, manifestHash: preview.manifestHash,
      maxEstimatedCost: preview.maxEstimatedCost, acknowledged: true, duplicateRiskAcknowledged: false, idempotencyKey: '22222222-2222-4222-8222-222222222222' };
    assert.equal(await evaluate(`workspace.startReview(${JSON.stringify(start)})`), null); assert.equal(modelCalls, 0);
    phase = 'cloud-exact-transfer'; reviewAllowed = true; const run = await evaluate(`workspace.startReview(${JSON.stringify(start)})`);
    let reviewed = run;
    for (let i = 0; i < 200 && ['queued', 'running'].includes(reviewed.status); i++) {
      await new Promise(resolve => setTimeout(resolve, 25)); reviewed = await evaluate(`workspace.getReview({projectId:${JSON.stringify(project.id)},runId:${JSON.stringify(run.runId)}})`);
    }
    assert.equal(reviewed.status, 'succeeded'); assert.equal(modelCalls, 1);
    await evaluate(`workspace.startReview(${JSON.stringify(start)})`); assert.equal(modelCalls, 1);
    phase = 'historical-evidence';
    const impact = await evaluate(`workspace.analyzeImpact({projectId:${JSON.stringify(project.id)},snapshotId:${JSON.stringify(snapshot.snapshotId)},proposalId:null})`);
    assert.equal(impact.historical.status, 'available'); assert.equal(impact.historical.serverSnapshotId, 7); assert.equal(impact.historical.pairs[0].support, 3);
    denied = true;
    const hidden = await evaluate(`workspace.getImpact({projectId:${JSON.stringify(project.id)},analysisId:${JSON.stringify(impact.analysisId)},offset:0})`);
    assert.equal(hidden.historical.status, 'unavailable'); denied = false;
    phase = 'minimal-diagnostics';
    assert.deepEqual(await evaluate('workspace.exportDiagnostics()'), { exported: false });
    diagnosticPath = join(temporary, 'diagnostics.json');
    assert.deepEqual(await evaluate('workspace.exportDiagnostics()'), { exported: true });
    const exported = await readFile(diagnosticPath, 'utf8'); assert.equal(exported.includes(byok), false); assert.equal(exported.includes(root), false);
    await assert.rejects(evaluate('workspace.exportDiagnostics()'), /DIAGNOSTICS_UNAVAILABLE/); assert.equal(await readFile(diagnosticPath, 'utf8'), exported);
    phase = 'permission-recheck'; denied = true; await assert.rejects(coordinator.open(project.id, analysisId), /REMOTE_ACCESS_DENIED/); denied = false;
    phase = 'identity-recheck'; githubId = 124; await assert.rejects(coordinator.open(project.id, analysisId), /REMOTE_IDENTITY_MISMATCH/); githubId = 123;
    phase = 'navigation-proof'; assert.equal(await coordinator.open(project.id, analysisId), 'gitviz://app/web/pages/timeline.html?repo=2&snapshot=7');
    phase = 'navigate';
    await evaluate(`void workspace.openRemoteAnalysis({projectId:${JSON.stringify(project.id)},analysisId:${JSON.stringify(analysisId)}}).catch(()=>{});true`);
    for (let i = 0; i < 200 && window.webContents.getURL() !== 'gitviz://app/web/pages/timeline.html?repo=2&snapshot=7'; i++) await new Promise(resolve => setTimeout(resolve, 25));
    assert.equal(window.webContents.getURL(), 'gitviz://app/web/pages/timeline.html?repo=2&snapshot=7');
    phase = 'config-change'; git('remote', 'set-url', 'origin', 'https://github.com/other/repo');
    await assert.rejects(coordinator.open(project.id, analysisId), /PRECONDITION_FAILED/);
    phase = 'cloud-process-recovery'; await cloudRecovery(executable, contracts, temporary);
    process.stdout.write('PASS P08b frozen agent/native cancel/account/identity/transition/permission/navigation/config\n');
    process.stdout.write('PASS cloud main-only BYOK/private block/native cancel/exact bytes/one request/output settlement\n');
    process.stdout.write('PASS historical pinned snapshot/no local path upload/permission recheck and diagnostics cancel/new-file/no overwrite\n');
    process.stdout.write('PASS frozen cloud handoff termination/restart/uncertain reservation/no automatic resend\n');
  } finally { for (const dispose of cleanup) dispose(); await agent.stop(); window.destroy(); clearTimeout(deadline); }
  app.exit(0);
}).catch(() => { process.stderr.write(`FAIL P08b ${phase}\n`); clearTimeout(deadline); app.exit(1); });
