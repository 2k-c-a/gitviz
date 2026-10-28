import './profile';
/** P08a real Electron/main relay/source agent: a completed local review appears once in the inbox, reads/marks/navigates, survives restart. */
import assert from 'node:assert/strict';
import { app, safeStorage } from 'electron';
import { execFileSync, spawn, type SpawnOptions } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { Agent } from '../src/agent';
import { Contracts } from '../src/contracts';
import { registerHealth } from '../src/ipc';
import { registerProjects } from '../src/projects';
import { registerNotifications } from '../src/notifications';
import { registerScheme, serveAssets, APP_URL } from '../src/app-protocol';
import { createWindow } from '../src/window';
import { loadKey } from '../src/secrets';

registerScheme(); app.on('window-all-closed', () => {});
let phase = 'startup';
const deadline = setTimeout(() => { process.stderr.write(`FAIL P08 deadline (${phase})\n`); app.exit(1); }, 120000);
const ALL = { projectIds: null, types: null, severities: null, read: 'all' };
void app.whenReady().then(async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'gitviz-p08-'));
  const root = join(temporary, '한글 &amp; project'); await mkdir(root);
  const git = (...args: string[]) => execFileSync(join(process.env.ProgramFiles ?? 'C:/Program Files', 'Git/cmd/git.exe'), ['-C', root, ...args], {
    env: { SystemRoot: process.env.SystemRoot, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: 'NUL' }, stdio: 'pipe', windowsHide: true });
  git('init', '-b', 'main'); git('config', 'user.name', 'P08 fixture'); git('config', 'user.email', 'fixture@example.invalid'); git('config', 'core.autocrlf', 'false');
  await writeFile(join(root, 'app.py'), 'value = 1\r\nprint(value)\r\n'); git('add', '.'); git('commit', '-m', 'fixture');
  await writeFile(join(root, 'app.py'), 'value = 2\r\nprint(value)\r\n');
  const contracts = new Contracts(join(__dirname, '../schema')); const repository = join(__dirname, '../../..');
  const python = execFileSync(join(repository, '.validation/venv-workspace/Scripts/python.exe'),
    ['-X', 'utf8', '-B', '-c', 'import sys; print(sys._base_executable)'], { windowsHide: true }).toString().trim();
  const launch = ((_executable: string, args: readonly string[], options: SpawnOptions) => spawn(
    python, ['-X', 'utf8', '-B', join(repository, 'tests/workspace/fixture_review_agent.py'), ...args], { ...options, cwd: repository })) as typeof spawn;
  const directory = join(temporary, 'data');
  const executable = join(__dirname, '../../../dist/workspace-agent/agent/gitviz-workspace-agent.exe');
  const key = () => loadKey(join(directory, 'key.dpapi'), { available: () => safeStorage.isAsyncEncryptionAvailable(), encrypt: value => safeStorage.encryptStringAsync(value), decrypt: async value => (await safeStorage.decryptStringAsync(value)).result });
  let agent = new Agent(contracts, launch);
  const window = createWindow(false);
  const health = registerHealth(window, () => agent.health());
  const projects = registerProjects(window, agent, contracts, { select: async () => root, confirm: async () => true });
  const notifications = registerNotifications(window, agent, contracts);
  const evaluate = (source: string) => window.webContents.executeJavaScript(source);
  async function wait(source: string, attempts = 300): Promise<void> {
    for (let i = 0; i < attempts; i++) { if (await evaluate(source)) return; await new Promise(resolve => setTimeout(resolve, 50)); }
    throw new Error('ASSERTION');
  }
  try {
    await serveAssets(join(__dirname, '../ui')); await agent.start(executable, directory, key); await window.loadURL(APP_URL);
    phase = 'bridge';
    assert.deepEqual(await evaluate(`[typeof window.ipcRenderer, typeof window.workspace.subscribeEvents, typeof window.workspace.listNotifications]`), ['undefined', 'function', 'function']);
    await wait(`document.getElementById('inbox-message').textContent==='표시할 알림이 없습니다.'`);
    phase = 'register'; const project = await evaluate('workspace.selectAndRegister()');
    await evaluate(`document.getElementById('reload-projects').click()`);
    await wait(`document.querySelectorAll('#project-picker option').length===2`);
    await evaluate(`document.getElementById('project-picker').value=${JSON.stringify(project.id)};document.getElementById('project-picker').dispatchEvent(new Event('change'));`);
    await wait(`document.querySelectorAll('#change-selection input').length>=1`);
    phase = 'capture';
    await evaluate(`for(const item of document.querySelectorAll('#change-selection input'))if(item.dataset.path==='app.py')item.checked=true;document.getElementById('capture-changes').click()`);
    await wait(`document.getElementById('capture-message').textContent==='선택한 입력을 고정했습니다.'`);
    phase = 'review';
    await evaluate(`document.getElementById('review-preview').click()`);
    await wait(`document.getElementById('review-preview-meta').textContent.includes('user-pc')`);
    await evaluate(`document.getElementById('review-consent').click();document.getElementById('review-start').click()`);
    await wait(`document.getElementById('review-result').textContent.includes('succeeded')`);
    phase = 'pushed-signal';  // No manual refresh: the main relay's signal must trigger the reload.
    await wait(`document.querySelectorAll('#inbox-list li').length===1`, 400);
    const text = String(await evaluate(`document.getElementById('inbox-list').textContent`));
    assert.ok(text.includes('로컬 AI 리뷰 완료') && text.includes('AI 추론 지적 1건') && text.includes('실행으로 검증한 결함 아님'), 'ai label');
    assert.ok(text.includes('한글 &amp; project'), 'project name rendered as text, not decoded as HTML');
    assert.ok(!text.includes('value = 2'), 'no source in the inbox');
    assert.equal(await evaluate(`document.getElementById('inbox-unread').textContent`), '읽지 않은 알림 1건');
    phase = 'navigate';
    await evaluate(`document.getElementById('project-picker').value='';document.getElementById('project-picker').dispatchEvent(new Event('change'));`);
    await evaluate(`[...document.querySelectorAll('#inbox-list button')].find(b=>b.textContent==='대상으로 이동').click()`);
    await wait(`document.getElementById('inbox-message').textContent==='대상으로 이동했습니다.'`);
    assert.equal(await evaluate(`document.getElementById('project-picker').value`), project.id);
    assert.ok(String(await evaluate(`document.getElementById('inbox-detail').textContent`)).includes('상태: 완료'));
    phase = 'mark-read';
    await evaluate(`[...document.querySelectorAll('#inbox-list button')].find(b=>b.textContent==='읽음으로').click()`);
    await wait(`document.getElementById('inbox-unread').textContent==='읽지 않은 알림 0건'`);
    await evaluate(`document.getElementById('inbox-read').value='unread';document.getElementById('inbox-read').dispatchEvent(new Event('change'))`);
    await wait(`document.getElementById('inbox-message').textContent==='표시할 알림이 없습니다.'`);
    phase = 'forgery';
    const listed = await agent.call('workspace.notifications.list', { filters: ALL, cursor: null, limit: 30 });
    const item = (listed.items as { notificationId: string; version: number; projectId: string }[])[0]!;
    await assert.rejects(agent.call('workspace.notifications.markRead', { notificationId: item.notificationId, expectedVersion: item.version, read: false },
      { projectId: randomUUID(), idempotencyKey: randomUUID() }), /RESOURCE_NOT_FOUND/);
    await assert.rejects(evaluate(`workspace.listNotifications({filters:${JSON.stringify(ALL)},cursor:null,limit:30,extra:1})`));
    phase = 'restart';
    notifications();
    await agent.stop();
    agent = new Agent(contracts, launch);
    await agent.start(executable, directory, key);
    const again = await agent.call('workspace.notifications.list', { filters: ALL, cursor: null, limit: 30 });
    const kept = (again.items as { notificationId: string; readAt: number | null; count: number }[]);
    assert.deepEqual(kept.map(entry => [entry.notificationId, entry.readAt !== null, entry.count]), [[item.notificationId, true, 1]]);
    process.stdout.write('PASS P08a real renderer/main relay/source agent: pushed refresh, text-only inbox, navigation, read CAS, forged project, restart persistence\n');
  } finally { health(); projects(); notifications(); window.destroy(); await agent.stop(); clearTimeout(deadline); await rm(temporary, { recursive: true, force: true }); }
}).then(() => app.exit(0)).catch(error => { process.stderr.write(`FAIL P08 (${phase}): ${error instanceof Error ? error.message.slice(0, 160) : 'assertion'}\n`); app.exit(1); });
