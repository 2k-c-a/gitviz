import './profile';
/** P06 real Electron/source agent with test-only injected provider, no HTTP calls. */
import assert from 'node:assert/strict';
import { app, safeStorage } from 'electron';
import { execFileSync, spawn, type SpawnOptions } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, writeFile, readFile, readdir, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { Agent } from '../src/agent';
import { Contracts } from '../src/contracts';
import { registerHealth } from '../src/ipc';
import { registerProjects } from '../src/projects';
import { registerProposals } from '../src/proposals';
import { registerImpact } from '../src/impact';
import { registerValidation } from '../src/validation';
import { registerScheme, serveAssets, APP_URL } from '../src/app-protocol';
import { createWindow } from '../src/window';
import { loadKey } from '../src/secrets';

registerScheme(); app.on('window-all-closed', () => {});
let phase = 'startup';
const deadline = setTimeout(() => { process.stderr.write(`FAIL P06 deadline (${phase})\n`); app.exit(1); }, 90000);
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
  const temporary = await mkdtemp(join(tmpdir(), 'gitviz-p06-'));
  const root = join(temporary, '한글 project'); await mkdir(root);
  const git = (...args: string[]) => execFileSync(join(process.env.ProgramFiles ?? 'C:/Program Files', 'Git/cmd/git.exe'), ['-C', root, ...args], {
    env: { SystemRoot: process.env.SystemRoot, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: 'NUL' }, stdio: 'pipe', windowsHide: true });
  git('init', '-b', 'main'); git('config', 'user.name', 'P06 fixture'); git('config', 'user.email', 'fixture@example.invalid'); git('config', 'core.autocrlf', 'false');
  await writeFile(join(root, 'app.py'), 'def own(): return 1\r\nvalue = 1\r\nown()\r\n'); await writeFile(join(root, 'app.ts'), 'export function hello(){return 1;}\nconst value = 1;\nhello();\n'); git('add', '.'); git('commit', '-m', 'fixture');
  await writeFile(join(root, 'app.py'), 'def own(): return 1\r\nvalue = 2\r\nown()\r\n'); await writeFile(join(root, 'app.ts'), 'export function hello(){return 1;}\nconst value = 2;\nhello();\n');

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
    ['-X', 'utf8', '-B', join(repository, 'tests/workspace/fixture_refactor_agent.py'), ...args],
    { ...options, cwd: repository })) as typeof spawn;
  const agent = new Agent(contracts, launch);
  const directory = join(temporary, 'data');
  const executable = app.isPackaged ? join(process.resourcesPath, 'agent/gitviz-workspace-agent.exe') : join(__dirname, '../../../build-output/agent/dist/agent/gitviz-workspace-agent.exe');
  const key = () => loadKey(join(directory, 'key.dpapi'), { available: () => safeStorage.isAsyncEncryptionAvailable(), encrypt: value => safeStorage.encryptStringAsync(value), decrypt: async value => (await safeStorage.decryptStringAsync(value)).result });
  const window = createWindow(false);
  const health = registerHealth(window, () => agent.health());
  const projects = registerProjects(window, agent, contracts, { select: async () => root, confirm: async () => true });
  let nativeConsent = false;
  const proposals = registerProposals(window, agent, contracts, { confirm: async () => nativeConsent });
  const impact = registerImpact(window, agent, contracts);
  // Comparison lives with validation; no test tool is linked, so nothing executes.
  const validation = registerValidation(window, agent, contracts, { confirm: async () => false });
  const evaluate = (source: string) => window.webContents.executeJavaScript(source);
  async function wait(source: string): Promise<void> {
    for (let i = 0; i < 200; i++) { if (await evaluate(source)) return; await new Promise(resolve => setTimeout(resolve, 30)); }
    throw new Error('ASSERTION: ' + source.slice(0, 70));
  }
  try {
    await serveAssets(join(__dirname, '../ui')); await agent.start(executable, directory, key); await window.loadURL(APP_URL);
    phase = 'register'; const project = await evaluate('workspace.selectAndRegister()');
    await evaluate(`document.getElementById('reload-projects').click()`);
    await wait(`document.querySelectorAll('#project-picker option').length===2`);
    await evaluate(`document.getElementById('project-picker').value=${JSON.stringify(project.id)};document.getElementById('project-picker').dispatchEvent(new Event('change'));`);
    await wait(`document.querySelectorAll('#change-selection input').length>=2`);
    phase = 'one-click-refactor';
    // Selecting files and pressing AI 리팩토링 captures, previews, starts and turns the result into a proposal.
    await evaluate(`window.__snapshots=[];window.addEventListener('workspace-snapshot-changed',event=>{if(event.detail)window.__snapshots.push(event.detail)})`);
    await evaluate(`for(const item of document.querySelectorAll('#change-selection input'))if(['app.py','app.ts'].includes(item.dataset.path))item.checked=true;document.getElementById('quick-refactor').click()`);
    // 2026-11-12: the result stays in the 파일 tab as a diff card (assist.js); the advanced proposal view gets the same proposal.
    await wait(`!!document.querySelector('#assist .ws-assist-proposal .ws-diff-add') && document.getElementById('proposal-view').textContent.includes('pending')`);
    // The flow no longer jumps to the advanced proposals tab.
    assert.equal(await evaluate(`document.getElementById('ws-panel-proposals').hidden`), true);
    assert.deepEqual(await hashes(root), before);
    await assert.rejects(evaluate(`workspace.applyProposal({projectId:${JSON.stringify(project.id)},proposalId:crypto.randomUUID(),approvalId:crypto.randomUUID(),idempotencyKey:crypto.randomUUID()})`), /APPROVAL_REQUIRED/);
    // Owner decision (2026-11-01): the single 적용 button approves and applies without a native prompt.
    phase = 'apply'; await evaluate(`document.querySelector('#assist .ws-assist-proposal button.btn-primary').click()`);
    await wait(`document.querySelector('#proposal-view h3')?.textContent.startsWith('applied')`);
    phase = 'auto-compare';
    await wait(`!!document.querySelector('#assist .ws-assist-summary')`);
    assert.equal(await evaluate(`document.getElementById('comparison-view').childElementCount>0 && document.querySelector('#assist .ws-assist-summary').textContent.includes('영향 범위')`), true);
    const applied = await hashes(root); assert.equal(before['.git/index'], applied['.git/index']);
    assert.notEqual(before['app.py'], applied['app.py']); assert.notEqual(before['app.ts'], applied['app.ts']);
    phase = 'impact'; await evaluate(`document.getElementById('analyze-impact').click()`);
    await wait(`document.getElementById('impact-view').textContent.includes('typescript-6.0.3-v1')`);
    assert.equal(await evaluate(`document.getElementById('impact-view').textContent.includes('python-ast-v1')`), true);
    assert.equal(await evaluate(`document.getElementById('impact-view').textContent.includes('unavailable')`), true);
    phase = 'new-refactor';
    await evaluate(`document.getElementById('reload-changes').click()`);
    await wait(`document.querySelectorAll('#change-selection input').length>=2`);
    await evaluate(`for(const item of document.querySelectorAll('#change-selection input'))if(['app.py','app.ts'].includes(item.dataset.path))item.checked=true;document.getElementById('capture-changes').click()`);
    await wait(`document.getElementById('capture-message').textContent==='선택한 입력을 고정했습니다.'`);
    // The advanced AI 점검 form keeps its own kind; the inline flow no longer flips it, so pick refactor here.
    await evaluate(`document.getElementById('review-kind').value='refactor';document.getElementById('review-kind').dispatchEvent(new Event('change'));document.getElementById('review-purpose').value='interrupt fixture';document.getElementById('review-purpose').dispatchEvent(new Event('input'));document.getElementById('review-preview').click()`);
    await wait(`document.getElementById('review-preview-meta').textContent.includes('user-pc')`);
    await evaluate(`document.getElementById('review-consent').click();document.getElementById('review-start').click()`);
    await wait(`document.getElementById('review-result').textContent.includes('succeeded')`);
    await evaluate(`document.querySelector('#review-result button').click()`);
    await wait(`document.querySelector('#proposal-view h3')?.textContent.startsWith('pending')`);
    await evaluate(`document.querySelectorAll('#proposal-view button')[0].click()`);
    await wait(`document.querySelector('#proposal-view h3')?.textContent.startsWith('recovery_required')`);
    phase = 'explicit-recovery'; nativeConsent = false;
    await evaluate(`document.querySelectorAll('#proposal-view button')[3].click()`);
    await new Promise(resolve => setTimeout(resolve, 100));
    nativeConsent = true; await evaluate(`document.querySelectorAll('#proposal-view button')[3].click()`);
    await wait(`document.querySelector('#proposal-view h3')?.textContent.startsWith('restored')`);
    assert.deepEqual(await hashes(root), applied);
    phase = 'second-fix-after-apply';
    // A second card from the same review still holds the pre-apply snapshot; its fix must re-read the applied file.
    await evaluate(`document.getElementById('proposal-view').replaceChildren();window.dispatchEvent(new CustomEvent('workspace-assist',{detail:{kind:'refactor',snapshot:window.__snapshots[0]}}))`);
    await wait(`!!document.querySelector('#assist .ws-assist-proposal .ws-diff-add') && document.getElementById('proposal-view').textContent.includes('pending')`);
    assert.equal(await evaluate(`!document.querySelector('#assist .ws-banner-warn')`), true);
    await evaluate(`[...document.querySelectorAll('#assist .ws-assist-proposal button')].find(button=>button.textContent==='거절').click()`);
    await wait(`document.querySelector('#assist .ws-assist-proposal').textContent.includes('거절했습니다')`);
    process.stdout.write('PASS P06 real renderer/main/source agent, refactor generation/one-button apply/auto before-after comparison/recovery, Python/TS worker evidence, index and external-change preservation\n');

  } finally { const diagnostic = await readFile(join(directory, 'p06-fixture-apply-error.json'), 'utf8').catch(() => ''); if (diagnostic) process.stderr.write(diagnostic + '\n'); validation(); impact(); proposals(); health(); projects(); window.destroy(); await agent.stop(); clearTimeout(deadline); await rm(temporary, { recursive: true, force: true }); }
}).then(() => app.exit(0)).catch(error => { process.stderr.write(`FAIL P06 (${phase}): ${error instanceof Error ? error.message.slice(0, 120) : 'assertion'}\n`); app.exit(1); });
