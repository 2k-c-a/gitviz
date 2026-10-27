import './profile';
/** P05 actual renderer/main/frozen agent/Git acceptance; source fixtures only. */
import assert from 'node:assert/strict';
import { app, safeStorage } from 'electron';
import { execFileSync, spawn, type SpawnOptions, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { mkdtemp, mkdir, writeFile, readFile, readdir, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { createServer } from 'node:http';
import { Agent } from '../src/agent';
import { Contracts } from '../src/contracts';
import { registerHealth } from '../src/ipc';
import { registerProjects } from '../src/projects';
import { registerScheme, serveAssets, APP_URL } from '../src/app-protocol';
import { createWindow } from '../src/window';
import { loadKey } from '../src/secrets';

registerScheme(); app.on('window-all-closed', () => {});
let phase = 'startup';
const deadline = setTimeout(() => { process.stderr.write(`FAIL P05 deadline (${phase})\n`); app.exit(1); }, 90000);
async function hashes(directory: string, prefix = ''): Promise<Record<string, string>> {
  const result: Record<string, string> = {};
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name), name = prefix + entry.name;
    if (entry.isDirectory()) Object.assign(result, await hashes(path, name + '/'));
    else result[name] = createHash('sha256').update(await readFile(path)).digest('hex');
  }
  return result;
}
void app.whenReady().then(async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'gitviz-p05-preview-'));
  const root = join(temporary, '한글 project'); await mkdir(root);
  const git = (...args: string[]) => execFileSync(join(process.env.ProgramFiles ?? 'C:/Program Files', 'Git/cmd/git.exe'), ['-C', root, ...args], {
    env: { SystemRoot: process.env.SystemRoot, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: 'NUL' }, stdio: 'pipe', windowsHide: true });
  git('init', '-b', 'main'); git('config', 'user.name', 'P05 fixture'); git('config', 'user.email', 'fixture@example.invalid'); git('config', 'core.autocrlf', 'false');
  await writeFile(join(root, 'app.py'), 'value = 1\r\nprint(value)\r\n'); git('add', '.'); git('commit', '-m', 'fixture');
  await writeFile(join(root, 'app.py'), 'value = 2\r\nprint(value)\r\n'); git('add', 'app.py');
  await writeFile(join(root, 'app.py'), 'value = 3\r\nprint(value)\r\n');
  await writeFile(join(root, 'package.json'), JSON.stringify({ scripts: { pretest: 'echo MUST_NOT_EXECUTE', test: 'node test.js' }, devDependencies: { typescript: '1' } }));
  await writeFile(join(root, 'package-lock.json'), '{}');
  const before = await hashes(root);
  let modelCalls = 0;
  let requestHash = '';
  // Own the fixed loopback endpoint before permitting any start request. If an
  // actual runtime already owns it, fail before sending; never call that runtime.
  const model = createServer((request, response) => {
    const chunks: Buffer[] = []; let size = 0;
    request.on('data', (chunk: Buffer) => { size += chunk.length; if (size > 65536) request.destroy(); else chunks.push(chunk); });
    request.on('end', () => {
      try {
        assert.equal(request.method, 'POST'); assert.equal(request.url, '/api/chat');
        const raw = Buffer.concat(chunks); requestHash = createHash('sha256').update(raw).digest('hex');
        const body = JSON.parse(raw.toString('utf8')) as { model: string; messages: { role: string; content: string }[]; options: { num_ctx: number }; tools?: unknown };
        assert.equal(body.model, 'qwen2.5-coder:7b'); assert.equal(body.options.num_ctx, 32768); assert.equal(body.tools, undefined);
        const input = JSON.parse(body.messages.find(message => message.role === 'user')!.content) as { files: { fileId: string; facet: string; path: string; lines: { right_line: number | null; text: string }[] }[] };
        const file = input.files[0]!; const line = file.lines.find(item => item.right_line !== null)!;
        modelCalls++;
        if (modelCalls === 2) return; // Received, but no response: exercise a genuine process-kill boundary.
        const output = { schemaVersion: 'workspace-review-v1', findings: [{ category: 'testing', priority: 'P2', severity: 'unknown', severityReason: 'Fixture evidence only.',
          condition: 'If the fixture branch is reached.', impact: 'Human review required.', evidence: [{ fileId: file.fileId, facet: file.facet, path: file.path,
            side: 'RIGHT', startLine: line.right_line, endLine: line.right_line, quote: line.text }], suggestion: 'Add a focused test.', verification: 'not_executed', limitations: ['Fake response; no model quality assertion.'] }], limitations: [] };
        response.writeHead(200, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify({ model: body.model, done: true, done_reason: 'stop', message: { role: 'assistant', content: JSON.stringify(output) }, prompt_eval_count: 100, eval_count: 50 }));
      } catch { response.writeHead(400); response.end('{}'); }
    });
  });
  const contracts = new Contracts(join(__dirname, '../schema'));
  const processSlot: { child?: ChildProcessWithoutNullStreams } = {};
  const trackedLaunch = ((executable: string, args: readonly string[], options: SpawnOptions) => {
    const child = spawn(executable, args, { ...options, stdio: 'pipe' }); processSlot.child = child; return child;
  }) as typeof spawn;
  let agent = new Agent(contracts, trackedLaunch);
  const directory = join(temporary, 'data');
  const executable = app.isPackaged ? join(process.resourcesPath, 'agent/gitviz-workspace-agent.exe') : join(__dirname, '../../../dist/workspace-agent/agent/gitviz-workspace-agent.exe');
  const key = () => loadKey(join(directory, 'key.dpapi'), { available: () => safeStorage.isAsyncEncryptionAvailable(), encrypt: value => safeStorage.encryptStringAsync(value), decrypt: async value => (await safeStorage.decryptStringAsync(value)).result });
  const window = createWindow(false);
  const health = registerHealth(window, () => agent.health());
  let projects = registerProjects(window, agent, contracts, { select: async () => root, confirm: async () => true });
  const evaluate = (source: string) => window.webContents.executeJavaScript(source);
  async function wait(source: string): Promise<void> {
    for (let i = 0; i < 200; i++) { if (await evaluate(source)) return; await new Promise(resolve => setTimeout(resolve, 30)); }
    throw new Error('ASSERTION');
  }
  try {
    await new Promise<void>((resolve, reject) => { model.once('error', reject); model.listen(11434, '127.0.0.1', () => { model.off('error', reject); resolve(); }); });
    await serveAssets(join(__dirname, '../ui')); await agent.start(executable, directory, key); await window.loadURL(APP_URL);
    phase = 'register'; const project = await evaluate('workspace.selectAndRegister()');
    await evaluate(`document.getElementById('reload-projects').click()`);
    await wait(`document.querySelectorAll('#project-picker option').length===2`);
    await evaluate(`document.getElementById('project-picker').value=${JSON.stringify(project.id)};document.getElementById('project-picker').dispatchEvent(new Event('change'));`);
    await wait(`document.querySelectorAll('#change-selection input').length>=2`);
    phase = 'capture';
    await evaluate(`for(const item of document.querySelectorAll('#change-selection input'))if(item.dataset.path==='app.py')item.checked=true;document.getElementById('capture-changes').click()`);
    await wait(`document.getElementById('capture-message').textContent==='선택한 입력을 고정했습니다.'`);
    await evaluate(`document.querySelector('#capture-result button').click()`);
    await wait(`document.getElementById('fixed-preview').textContent.includes('value =')`);
    phase = 'review-preview';
    await evaluate(`document.getElementById('review-preview').click()`);
    await wait(`document.getElementById('review-preview-meta').textContent.includes('user-pc')`);
    assert.equal(await evaluate(`document.getElementById('review-start').disabled`), true);
    assert.equal(await evaluate(`document.getElementById('review-preview-bytes').textContent.includes('value =')`), true);
    await assert.rejects(agent.call('workspace.reviews.preview', { snapshotId: (await agent.call('workspace.reviews.list', { offset: 0, limit: 1 }, { projectId: project.id })).snapshotId ?? randomUUID(), kind: 'review', purpose: 'fixture', profileId: 'gpt-5.4-v1', generation: 1 }, { projectId: project.id }), /CLOUD_MAPPING_REQUIRED/);
    phase = 'review-loopback-fixture';
    assert.equal(modelCalls, 0);
    await evaluate(`document.getElementById('review-consent').click();document.getElementById('review-start').click()`);
    await wait(`document.getElementById('review-result').textContent.includes('succeeded')`);
    assert.equal(modelCalls, 1);
    assert.equal(await evaluate(`document.getElementById('review-preview-meta').textContent.includes(${JSON.stringify(requestHash)})`), true);
    assert.equal(await evaluate(`document.getElementById('review-result').textContent.includes('unverified')`), true);
    phase = 'review-crash-recovery';
    await evaluate(`document.getElementById('review-generation').value='2';document.getElementById('review-generation').dispatchEvent(new Event('input'));document.getElementById('review-preview').click()`);
    await wait(`document.getElementById('review-preview-meta').textContent.includes('user-pc')`);
    await evaluate(`document.getElementById('review-consent').click();document.getElementById('review-duplicate-risk').click();document.getElementById('review-start').click()`);
    for (let i = 0; i < 200 && modelCalls < 2; i++) await new Promise(resolve => setTimeout(resolve, 20));
    assert.equal(modelCalls, 2);
    const child = processSlot.child; assert.ok(child);
    const closed = new Promise<void>(resolve => { child.once('close', () => resolve()); });
    assert.equal(child.kill(), true); await closed; await agent.stop();
    agent = new Agent(contracts, trackedLaunch); await agent.start(executable, directory, key);
    projects(); projects = registerProjects(window, agent, contracts, { select: async () => root, confirm: async () => true });
    const restored = await agent.call('workspace.reviews.list', { offset: 0, limit: 10 }, { projectId: project.id });
    const runs = restored.runs as { runId: string; status: string }[];
    assert.equal(runs.length, 2); assert.ok(runs.some(run => run.status === 'succeeded'));
    const interrupted = runs.find(run => run.status === 'uncertain'); assert.ok(interrupted);
    const recovered = await agent.call('workspace.reviews.get', { runId: interrupted.runId }, { projectId: project.id });
    assert.equal(recovered.outcomeUncertain, true); assert.equal(recovered.attemptCount, 1); assert.equal(recovered.budgetReserved, '0');
    assert.equal(modelCalls, 2);
    phase = 'detect'; await evaluate(`document.getElementById('detect-validation').click()`);
    await wait(`document.getElementById('detected-tools').textContent.includes('pretest')`);
    assert.equal(await evaluate(`document.getElementById('detected-tools').textContent.includes('MUST_NOT_EXECUTE')`), true);
    phase = 'plan';
    assert.equal(await evaluate(`document.querySelector('#detected-tools button').disabled`), false);
    await evaluate(`document.querySelector('#detected-tools button').click()`);
    await wait(`document.getElementById('detected-tools').textContent.includes('"executionAllowed": false')`);
    const captured = await agent.call('workspace.changes.capture', { items: [{ facet: 'staged', path: 'app.py' }], includeUntracked: false }, { projectId: project.id, idempotencyKey: randomUUID() });
    phase = 'fixed-preview'; await writeFile(join(root, 'app.py'), 'changed after capture\r\n');
    const file = (captured.files as { fileId: string }[])[0]!;
    const fixed = await agent.call('workspace.files.preview', { snapshotId: captured.snapshotId, fileId: file.fileId, side: 'RIGHT', startLine: 1, endLine: 20 }, { projectId: project.id });
    assert.ok((fixed.lines as { text: string }[]).some(line => line.text.includes('value = 2')));
    await assert.rejects(agent.call('workspace.files.preview', { snapshotId: captured.snapshotId, fileId: file.fileId, side: 'RIGHT', startLine: 1, endLine: 20 }, { projectId: randomUUID() }), /RESOURCE_NOT_FOUND/);
    await writeFile(join(root, 'app.py'), 'value = 3\r\nprint(value)\r\n');
    assert.deepEqual(await hashes(root), before);
    process.stdout.write('PASS P05 frozen agent preview/cloud block, consent/wire hash/result via owned loopback fake provider, kill/restart uncertain without resend, actual renderer/main/Git, source/index preservation; no real model calls\n');
  } finally { health(); projects(); window.destroy(); await agent.stop(); model.closeAllConnections(); await new Promise<void>(resolve => model.close(() => resolve())); clearTimeout(deadline); await rm(temporary, { recursive: true, force: true }); }
}).then(() => app.exit(0)).catch(error => { process.stderr.write(`FAIL P05 (${phase}): ${error instanceof Error ? error.message.slice(0, 120) : 'assertion'}\n`); app.exit(1); });
