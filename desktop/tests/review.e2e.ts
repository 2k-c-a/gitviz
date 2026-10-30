import './profile';
/** P05 real Electron/source agent with test-only injected provider, no HTTP calls. */
import assert from 'node:assert/strict';
import { app, safeStorage } from 'electron';
import { execFileSync, spawn, type SpawnOptions } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { mkdtemp, mkdir, writeFile, readFile, readdir, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
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
  const temporary = await mkdtemp(join(tmpdir(), 'gitviz-p05-'));
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
  const contracts = new Contracts(join(__dirname, '../schema')); const repository = join(__dirname, '../../..');
  // Windows venv python.exe is a launcher: use its base interpreter so the
  // production parent-PID check observes Electron as the actual OS parent.
  const python = execFileSync(join(repository, '.validation/venv-workspace/Scripts/python.exe'),
    ['-X', 'utf8', '-B', '-c', 'import sys; print(sys._base_executable)'], { windowsHide: true }).toString().trim();
  const launch = ((_executable: string, args: readonly string[], options: SpawnOptions) => spawn(
    python,
    ['-X', 'utf8', '-B', join(repository, 'tests/workspace/fixture_review_agent.py'), ...args],
    { ...options, cwd: repository })) as typeof spawn;
  const agent = new Agent(contracts, launch);
  const directory = join(temporary, 'data');
  const executable = app.isPackaged ? join(process.resourcesPath, 'agent/gitviz-workspace-agent.exe') : join(__dirname, '../../../build-output/agent/dist/agent/gitviz-workspace-agent.exe');
  const key = () => loadKey(join(directory, 'key.dpapi'), { available: () => safeStorage.isAsyncEncryptionAvailable(), encrypt: value => safeStorage.encryptStringAsync(value), decrypt: async value => (await safeStorage.decryptStringAsync(value)).result });
  const window = createWindow(false);
  const health = registerHealth(window, () => agent.health());
  const projects = registerProjects(window, agent, contracts, { select: async () => root, confirm: async () => true });
  const evaluate = (source: string) => window.webContents.executeJavaScript(source);
  async function wait(source: string): Promise<void> {
    for (let i = 0; i < 200; i++) { if (await evaluate(source)) return; await new Promise(resolve => setTimeout(resolve, 30)); }
    throw new Error('ASSERTION');
  }
  try {
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
    phase = 'review-consent';
    await evaluate(`document.getElementById('review-consent').click();document.getElementById('review-start').click()`);
    await wait(`document.getElementById('review-result').textContent.includes('succeeded')`);
    assert.equal(await evaluate(`document.getElementById('review-result').textContent.includes('unknown')`), true);
    assert.equal(await evaluate(`document.getElementById('review-result').textContent.includes('unverified')`), true);
    phase = 'review-feedback';
    await evaluate(`document.querySelector('#review-result select').value='false_positive';document.querySelector('#review-result textarea').value='UI fixture private note';document.querySelector('#review-result button').click()`);
    await wait(`document.querySelector('#review-result select')?.value==='false_positive'`);
    await evaluate(`document.getElementById('review-history').click()`);
    await wait(`document.querySelectorAll('#review-history-list button').length===1`);
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
    process.stdout.write('PASS P05 real renderer/main/source agent/injected provider, consent, validated finding, feedback/history, source/index preservation\n');
  } finally { health(); projects(); window.destroy(); await agent.stop(); clearTimeout(deadline); await rm(temporary, { recursive: true, force: true }); }
}).then(() => app.exit(0)).catch(error => { process.stderr.write(`FAIL P05 (${phase}): ${error instanceof Error ? error.message.slice(0, 120) : 'assertion'}\n`); app.exit(1); });
