import './profile';
/** P07 real renderer/native gate/source agent/Windows Job; temporary Python tests only. */
import assert from 'node:assert/strict';
import { app, safeStorage } from 'electron';
import { execFileSync, spawn, type SpawnOptions } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, writeFile, readFile, rm, access, realpath } from 'node:fs/promises';
import { join, sep } from 'node:path';
import { tmpdir } from 'node:os';
import { Agent } from '../src/agent';
import { Contracts } from '../src/contracts';
import { registerHealth } from '../src/ipc';
import { registerProjects } from '../src/projects';
import { registerProposals } from '../src/proposals';
import { registerImpact } from '../src/impact';
import { registerValidation } from '../src/validation';
import { registerDesktop } from '../src/desktop-ipc';
import { registerScheme, serveAssets, APP_URL } from '../src/app-protocol';
import { createWindow } from '../src/window';
import { loadKey } from '../src/secrets';

registerScheme(); app.on('window-all-closed', () => {});
async function removeFixture(path: string): Promise<void> {
  const resolved = await realpath(path); const expected = (await realpath(tmpdir())) + sep + 'gitviz-p07-';
  if (!resolved.startsWith(expected)) throw new Error('FIXTURE_SCOPE_CHANGED');
  await rm(resolved, { recursive: true, force: true });
}
let phase = 'startup';
const deadline = setTimeout(() => { process.stderr.write(`FAIL P07 deadline ${phase}\n`); app.exit(1); }, 120000);
void app.whenReady().then(async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'gitviz-p07-'));
  const root = join(temporary, '한글 project'); await mkdir(root);
  const git = (...args: string[]) => execFileSync(join(process.env.ProgramFiles ?? 'C:/Program Files', 'Git/cmd/git.exe'), ['-C', root, ...args], {
    env: { SystemRoot: process.env.SystemRoot, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: 'NUL' }, windowsHide: true, stdio: 'pipe' });
  git('init', '-b', 'main'); git('config', 'user.name', 'P07 fixture'); git('config', 'user.email', 'fixture@example.invalid'); git('config', 'core.autocrlf', 'false');
  await writeFile(join(root, 'app.py'), 'x = 1\r\n');
  await writeFile(join(root, 'app.ts'), 'export function own(x){return x;}\n');
  await writeFile(join(root, 'pyproject.toml'), '[tool.unittest]\n');
  const marker = join(temporary, 'executed.marker');
  await writeFile(join(root, 'test_fixture.py'), `import unittest,app\nfrom pathlib import Path\nPath(${JSON.stringify(marker)}).write_text('fixture')\nclass Check(unittest.TestCase):\n def test_value(self): self.assertEqual(app.x,2)\n`);
  git('add', '.'); git('commit', '-m', 'fixture'); await writeFile(join(root, 'app.py'), 'x = 2\r\n');
  await writeFile(join(root, 'app.ts'), 'export function own(x){if(x&&x>1){return x+1;}return x-1;}\n');
  const indexHash = createHash('sha256').update(await readFile(join(root, '.git/index'))).digest('hex');
  const repository = join(__dirname, '../../..');
  const python = app.isPackaged ? '' : execFileSync(join(repository, '.validation/venv-workspace/Scripts/python.exe'), ['-X', 'utf8', '-B', '-c', 'import sys;print(sys._base_executable)'], { windowsHide: true }).toString().trim();
  const launch = ((_exe: string, args: readonly string[], options: SpawnOptions) => spawn(python,
    ['-X', 'utf8', '-B', join(repository, 'tests/workspace/fixture_validation_agent.py'), ...args], { ...options, cwd: repository })) as typeof spawn;
  const contracts = new Contracts(join(__dirname, '../schema')); const agent = app.isPackaged ? new Agent(contracts) : new Agent(contracts, launch);
  const directory = join(temporary, 'data'); const window = createWindow(false);
  let approved = false;
  const cleanup = [registerHealth(window, () => agent.health()), registerProjects(window, agent, contracts, { select: async () => root, confirm: async () => true }),
    registerProposals(window, agent, contracts), registerImpact(window, agent, contracts), registerDesktop(window, null, null),
    registerValidation(window, agent, contracts, { confirm: async () => approved })];
  const evaluate = (source: string) => window.webContents.executeJavaScript(source);
  async function wait(source: string) {
    for (let i = 0; i < 600; i++) { if (await evaluate(source)) return; await new Promise(resolve => setTimeout(resolve, 30)); }
    throw new Error('ASSERTION: ' + source.slice(0, 90));
  }
  async function capture() {
    await evaluate(`document.getElementById('reload-changes').click()`);
    await wait(`Array.from(document.querySelectorAll('#change-selection input')).some(item=>item.dataset.path==='app.py')`);
    await evaluate(`for(const item of document.querySelectorAll('#change-selection input'))item.checked=['app.py','app.ts'].includes(item.dataset.path);document.getElementById('capture-changes').click()`);
    await wait(`document.getElementById('capture-message').textContent==='선택한 입력을 고정했습니다.'`);
  }
  async function plan() {
    await evaluate(`document.getElementById('detect-validation').click()`);
    await wait(`document.querySelector('#detected-tools button')!==null`);
    const count = await evaluate(`document.querySelectorAll('#validation-plans button').length`) as number;
    await evaluate(`document.querySelector('#detected-tools button').click()`);
    await wait(`document.querySelectorAll('#validation-plans button').length===${count + 1}`);
  }
  try {
    await serveAssets(join(__dirname, '../ui'));
    await agent.start(app.isPackaged ? join(process.resourcesPath, 'agent/gitviz-workspace-agent.exe') : 'source-fixture-only', directory, () => loadKey(join(directory, 'key.dpapi'), {
      available: () => safeStorage.isAsyncEncryptionAvailable(), encrypt: value => safeStorage.encryptStringAsync(value), decrypt: async value => (await safeStorage.decryptStringAsync(value)).result }));
    await window.loadURL(APP_URL);
    const project = await evaluate('workspace.selectAndRegister()');
    await evaluate(`document.getElementById('reload-projects').click()`); await wait(`document.querySelectorAll('#project-picker option').length===2`);
    await evaluate(`document.getElementById('project-picker').value=${JSON.stringify(project.id)};document.getElementById('project-picker').dispatchEvent(new Event('change'))`);
    phase = 'baseline-plan'; await capture(); await plan();
    phase = 'native-cancel'; await evaluate(`document.querySelector('#validation-plans button').click()`);
    await wait(`document.querySelector('#validation-plans button').disabled===false`);
    await assert.rejects(access(marker));
    await assert.rejects(evaluate(`workspace.runValidation({projectId:${JSON.stringify(project.id)},planId:crypto.randomUUID(),idempotencyKey:crypto.randomUUID(),capability:'${'0'.repeat(64)}'})`), /INVALID_REQUEST/);
    approved = true; phase = 'baseline-run'; await evaluate(`document.querySelector('#validation-plans button').click()`);
    await wait(`document.querySelector('#validation-view h3')?.textContent.startsWith('succeeded')`);
    await access(marker);
    phase = 'after-run'; await writeFile(join(root, 'app.py'), 'x = 3\r\n'); await writeFile(join(root, 'app.ts'), 'export function own(x){return x+1;}\n'); await capture(); await plan();
    await evaluate(`Array.from(document.querySelectorAll('#validation-plans button')).at(-1).click()`);
    await wait(`document.querySelector('#validation-view h3')?.textContent.startsWith('failed')`);
    phase = 'comparison'; await evaluate(`document.getElementById('comparison-before').selectedIndex=0;document.getElementById('compare-snapshots').click()`);
    await wait(`document.querySelector('#comparison-view h3')?.textContent==='검증 비교 comparable'`);
    assert.equal(await evaluate(`document.getElementById('comparison-view').textContent.includes('"javascriptFunctionCount": 1')`), true);
    assert.equal(createHash('sha256').update(await readFile(join(root, '.git/index'))).digest('hex'), indexHash);
    assert.equal((await readFile(join(root, 'app.py'), 'utf8')), 'x = 3\r\n');
    assert.equal(await evaluate(`document.querySelectorAll('#comparison-before option').length`), 2);
    await assert.rejects(evaluate(`workspace.getValidation({projectId:crypto.randomUUID(),runId:crypto.randomUUID(),offset:0,limit:10})`), /RESOURCE_NOT_FOUND/);
    phase = 'cancel-tree';
    const childMarker = join(temporary, 'grandchild.pid');
    await writeFile(join(root, 'test_fixture.py'), `import subprocess,sys,time,unittest\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(60)'])\nPath(${JSON.stringify(childMarker)}).write_text(str(p.pid))\nclass Check(unittest.TestCase):\n def test_wait(self): time.sleep(60)\n`);
    await evaluate(`Array.from(document.querySelectorAll('#validation-plans button')).at(-1).click()`);
    for (let i = 0; i < 400; i++) { if (await access(childMarker).then(() => true, () => false)) break; await new Promise(resolve => setTimeout(resolve, 30)); }
    const pid = Number(await readFile(childMarker, 'utf8'));
    assert.ok(Number.isInteger(pid) && pid > 0);
    await evaluate(`document.getElementById('validation-cancel').click()`);
    await wait(`document.querySelector('#validation-view h3')?.textContent.startsWith('cancelled')`);
    let alive = true;
    for (let i = 0; i < 100; i++) { try { process.kill(pid, 0); } catch { alive = false; break; } await new Promise(resolve => setTimeout(resolve, 30)); }
    assert.equal(alive, false);
    // Bounded result is text-only and cannot expose native capabilities.
    assert.equal(await evaluate(`typeof workspace.invoke`), 'undefined');
    process.stdout.write('PASS P07 renderer/native cancel+trust/agent/Windows Job baseline pass→after fail/Python+TS metrics/cancelled grandchild/index preservation; installed Python fixture tools; no model calls\n');
  } finally { for (const close of cleanup) close(); window.destroy(); await agent.stop(); clearTimeout(deadline); await removeFixture(temporary); }
}).then(() => app.exit(0)).catch(error => { process.stderr.write(`FAIL P07 ${phase}: ${error instanceof Error ? error.message.slice(0, 160) : 'assertion'}\n`); app.exit(1); });
