import './profile';
/** P02 real renderer/IPC/agent acceptance. No production test IPC is added. */
import assert from 'node:assert/strict';
import { app, safeStorage } from 'electron';
import { execFileSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { mkdtemp, mkdir, writeFile, readdir, readFile, rm } from 'node:fs/promises';
import { join, resolve, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { Agent } from '../src/agent';
import { Contracts } from '../src/contracts';
import { registerHealth } from '../src/ipc';
import { registerProjects, type NativeProjectDialogs } from '../src/projects';
import { APP_URL, registerScheme, serveAssets } from '../src/app-protocol';
import { createWindow } from '../src/window';
import { loadKey } from '../src/secrets';

registerScheme();
app.on('window-all-closed', () => {});
const deadline = setTimeout(() => { console.error('FAIL P02 deadline'); app.exit(1); }, 60000);
async function snapshot(root: string): Promise<Record<string, string>> {
  const files: Record<string, string> = {};
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const file = join(root, entry.name);
    if (entry.isDirectory()) Object.assign(files, await snapshot(file));
    else if (entry.isFile()) files[file] = createHash('sha256').update(await readFile(file)).digest('hex');
  }
  return files;
}
void app.whenReady().then(async () => {
  const root = await mkdtemp(join(tmpdir(), 'gitviz-p02-'));
  const a = join(root, '한글 alpha'); const b = join(root, 'beta');
  const executable = join(process.env.ProgramFiles ?? 'C:/Program Files', 'Git/cmd/git.exe');
  const git = (directory: string, ...args: string[]) => execFileSync(executable, ['-C', directory, ...args], { env: { SystemRoot: process.env.SystemRoot, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: 'NUL' }, windowsHide: true, stdio: 'pipe' });
  for (const [directory, branch] of [[a, 'alpha'], [b, 'beta']]) {
    await mkdir(directory!); git(directory!, 'init', '-b', branch!);
    git(directory!, 'config', 'user.name', 'P02 fixture'); git(directory!, 'config', 'user.email', 'fixture@example.invalid');
    await writeFile(join(directory!, 'file.txt'), 'before\r\n'); git(directory!, 'add', '.'); git(directory!, 'commit', '-m', '<img src=x onerror="window.__xss=true">');
  }
  const linked = join(root, 'linked'); git(a, 'worktree', 'add', '-b', 'linked', linked);
  const sub = join(a, 'subfolder'); await mkdir(sub);
  const beforeA = await snapshot(a); const beforeB = await snapshot(b);
  const contracts = new Contracts(join(__dirname, '../schema'));
  const agent = new Agent(contracts);
  const directory = join(root, 'data');
  const window = createWindow(false);
  let selection: string | null = null; let confirmations = 0; let accept = true;
  const native: NativeProjectDialogs = { select: async () => selection, confirm: async () => { confirmations++; return accept; } };
  let heldProject: string | undefined; let release: (() => void) | undefined; let holding = false;
  const bridge: Pick<Agent, 'call'> = { call: async (...args) => {
    const result = await agent.call(...args);
    if (args[0] === 'workspace.git.status' && args[2]?.projectId === heldProject) {
      holding = true; await new Promise<void>(resolve => { release = resolve; });
    }
    return result;
  } };
  const stopHealth = registerHealth(window, () => agent.health());
  const stopProjects = registerProjects(window, bridge, contracts, native);
  const evaluate = (source: string) => window.webContents.executeJavaScript(source);
  const wait = async (condition: string) => { for (let turn = 0; turn < 100; turn++) { if (await evaluate(condition)) return; await new Promise(resolve => setTimeout(resolve, 20)); } throw new Error('CONDITION_TIMED_OUT'); };
  try {
    await agent.start(join(__dirname, '../../../build-output/agent/dist/agent/gitviz-workspace-agent.exe'), directory, () => loadKey(join(directory, 'key.dpapi'), {
      available: () => safeStorage.isAsyncEncryptionAvailable(), encrypt: value => safeStorage.encryptStringAsync(value), decrypt: async value => (await safeStorage.decryptStringAsync(value)).result,
    }));
    await serveAssets(join(__dirname, '../ui')); await window.loadURL(APP_URL);
    assert.equal(await evaluate('window.workspace.selectAndRegister()'), null);
    selection = sub;
    accept = false;
    assert.equal(await evaluate('window.workspace.selectAndRegister()'), null);
    accept = true;
    const projectA = await evaluate('window.workspace.selectAndRegister()') as { id: string };
    assert.equal(confirmations, 2);
    selection = linked;
    await evaluate('window.workspace.selectAndRegister()');
    assert.equal(confirmations, 4);
    selection = b;
    const projectB = await evaluate('window.workspace.selectAndRegister()') as { id: string };
    await evaluate("document.getElementById('reload-projects').click()");
    await wait("document.getElementById('project-picker').options.length===4");
    heldProject = projectA.id;
    const select = (id: string) => evaluate(`document.getElementById('project-picker').value=${JSON.stringify(id)};document.getElementById('project-picker').dispatchEvent(new Event('change'))`);
    await select(projectA.id);
    for (let turn = 0; turn < 100 && !holding; turn++) await new Promise(resolve => setTimeout(resolve, 20));
    assert.ok(holding);
    await select(projectB.id);
    await wait("document.getElementById('git-state').textContent.includes('beta')");
    release?.(); await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(await evaluate("document.getElementById('git-state').textContent.includes('alpha')"), false);
    assert.equal(await evaluate("document.querySelector('#git-state img')===null"), true);
    assert.equal(await evaluate("document.getElementById('git-state').textContent.includes('<img')"), true);
    const key = randomUUID();
    const update = JSON.stringify({ projectId: projectB.id, recentLimit: 7, expectedVersion: 1, idempotencyKey: key });
    const changed = await evaluate(`window.workspace.updateSettings(${update})`) as { settings: { recentLimit: { version: number } } };
    assert.equal(changed.settings.recentLimit.version, 2);
    assert.deepEqual(await evaluate(`window.workspace.updateSettings(${update})`), changed);
    await assert.rejects(evaluate(`window.workspace.getGitStatus(${JSON.stringify({ projectId: projectB.id, recentLimit: 101 })})`));
    await assert.rejects(evaluate(`window.workspace.getProject(${JSON.stringify({ projectId: randomUUID() })})`));
    await evaluate(`window.workspace.unregisterProject(${JSON.stringify({ projectId: projectB.id, expectedVersion: 1, idempotencyKey: randomUUID() })})`);
    assert.deepEqual(await snapshot(a), beforeA); assert.deepEqual(await snapshot(b), beforeB);
    console.log('PASS P02 native selection/cancel/root grant, real IPC/agent/Git, late response isolation, text-only rendering, CAS/idempotency/unregister, source/index preservation');
  } finally {
    release?.(); stopProjects(); stopHealth(); window.destroy(); await agent.stop();
    assert.equal(dirname(resolve(root)), resolve(tmpdir()));
    await rm(root, { recursive: true }); clearTimeout(deadline);
  }
  app.exit(0);
}).catch(() => { console.error('FAIL P02 E2E'); clearTimeout(deadline); app.exit(1); });
