import './profile';
/** Phase 5: the local workspace information architecture on the real renderer, real main/IPC, frozen agent and a real Git fixture.
 * No cloud provider, no remote server and no external editor are involved; project files are never written.
 */
import assert from 'node:assert/strict';
import { app, BrowserWindow, safeStorage, type NativeImage } from 'electron';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, writeFile, readFile, readdir, rm } from 'node:fs/promises';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { Agent } from '../src/agent';
import { Contracts } from '../src/contracts';
import { registerHealth } from '../src/ipc';
import { registerProjects } from '../src/projects';
import { registerImpact } from '../src/impact';
import { registerProposals } from '../src/proposals';
import { registerValidation } from '../src/validation';
import { registerEditor } from '../src/editor';
import { registerNotifications } from '../src/notifications';
import { registerDiagnostics } from '../src/diagnostics-ipc';
import { registerDesktop } from '../src/desktop-ipc';
import { registerRemoteWorkspace } from '../src/remote-workspace-ipc';
import { registerScheme, serveAssets, APP_URL } from '../src/app-protocol';
import { loadKey } from '../src/secrets';

app.disableHardwareAcceleration();
app.commandLine.appendSwitch('force-device-scale-factor', '1');
registerScheme(); app.on('window-all-closed', () => {});
let phase = 'startup';
const checks: [string, boolean][] = [];
const deadline = setTimeout(() => { process.stderr.write(`FAIL Phase 5 deadline (${phase})\n`); app.exit(1); }, 150000);
const output = join(__dirname, '../../../.validation/phase5');
async function hashes(directory: string, prefix = ''): Promise<Record<string, string>> {
  const result: Record<string, string> = {};
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.name === '.git') continue;
    const path = join(directory, entry.name), name = prefix + entry.name;
    if (entry.isDirectory()) Object.assign(result, await hashes(path, name + '/'));
    else result[name] = createHash('sha256').update(await readFile(path)).digest('hex');
  }
  return result;
}
void app.whenReady().then(async () => {
  mkdirSync(output, { recursive: true });
  const temporary = await mkdtemp(join(tmpdir(), 'gitviz-p5-'));
  const root = join(temporary, 'sample project'); await mkdir(root);
  const git = (...args: string[]) => execFileSync(join(process.env.ProgramFiles ?? 'C:/Program Files', 'Git/cmd/git.exe'), ['-C', root, ...args], {
    env: { SystemRoot: process.env.SystemRoot, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: 'NUL' }, stdio: 'pipe', windowsHide: true });
  git('init', '-b', 'main'); git('config', 'user.name', 'P5 fixture'); git('config', 'user.email', 'fixture@example.invalid'); git('config', 'core.autocrlf', 'false');
  await writeFile(join(root, 'app.py'), 'value = 1\nprint(value)\n'); await writeFile(join(root, 'util.py'), 'from app import value\n');
  git('add', '.'); git('commit', '-m', 'fixture');
  await writeFile(join(root, 'app.py'), 'value = 2\nprint(value)\n'); git('add', 'app.py'); await writeFile(join(root, 'util.py'), 'from app import value\nprint(value)\n');
  await writeFile(join(root, 'package.json'), JSON.stringify({ scripts: { test: 'node test.js' } })); await writeFile(join(root, 'package-lock.json'), '{}');
  const before = await hashes(root);
  const contracts = new Contracts(join(__dirname, '../schema')); const agent = new Agent(contracts);
  const directory = join(temporary, 'data');
  const executable = app.isPackaged ? join(process.resourcesPath, 'agent/gitviz-workspace-agent.exe') : join(__dirname, '../../../dist/workspace-agent/agent/gitviz-workspace-agent.exe');
  const key = () => loadKey(join(directory, 'key.dpapi'), { available: () => safeStorage.isAsyncEncryptionAvailable(), encrypt: value => safeStorage.encryptStringAsync(value), decrypt: async value => (await safeStorage.decryptStringAsync(value)).result });
  const window = new BrowserWindow({ width: 900, height: 640, frame: false, show: false, webPreferences: { preload: join(__dirname, '../preload/preload.js'), offscreen: true, backgroundThrottling: false, sandbox: true, contextIsolation: true, nodeIntegration: false } });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  let frame: NativeImage | undefined; let paints = 0;
  window.webContents.on('paint', (_event, _dirty, image) => { frame = image; paints++; });
  const health = registerHealth(window, () => agent.health());
  // The same IPC modules main.ts registers, without a server origin (no remote, no cloud provider).
  const others = [registerDiagnostics(window, () => agent.health()), registerProposals(window, agent, contracts), registerValidation(window, agent, contracts), registerEditor(window, agent, contracts, directory),
    registerNotifications(window, agent, contracts), registerDesktop(window, null, null), registerRemoteWorkspace(window, null), registerImpact(window, agent, contracts, null)];
  const projects = registerProjects(window, agent, contracts, { select: async () => root, confirm: async () => true });
  const evaluate = (source: string) => window.webContents.executeJavaScript(source);
  async function wait(source: string): Promise<void> {
    for (let i = 0; i < 300; i++) { if (await evaluate(source)) return; await new Promise(resolve => setTimeout(resolve, 30)); }
    throw new Error(`ASSERTION: ${phase}: ${source.slice(0, 140)}`);
  }
  const check = async (name: string, source: string) => { phase = name; const ok = Boolean(await evaluate(source)); checks.push([name, ok]); assert.ok(ok, `check failed: ${name}`); };
  const visible = (selector: string) => `(()=>{const e=document.querySelector(${JSON.stringify(selector)});return !!e && !e.hidden && getComputedStyle(e).display!=='none' && e.getClientRects().length>0})()`;
  const key3 = (k: string) => evaluate(`document.dispatchEvent(new KeyboardEvent('keydown',{key:${JSON.stringify(k)},ctrlKey:true,bubbles:true}))`);
  async function capture(name: string, width: number, height: number) {
    phase = name; window.setContentSize(width, height);
    await wait(`innerWidth===${width} && innerHeight===${height}`);
    const start = paints; window.webContents.invalidate();
    for (let i = 0; i < 100; i++) { if (paints > start && frame?.getSize().width === width) break; await new Promise(resolve => setTimeout(resolve, 30)); }
    assert.ok(frame && paints > start, `no fresh painted frame for ${name}`);
    await new Promise(resolve => setTimeout(resolve, 150)); writeFileSync(join(output, name), frame.toPNG());
  }
  try {
    await serveAssets(join(__dirname, '../ui')); await agent.start(executable, directory, key); await window.loadURL(APP_URL);

    phase = 'first-run';
    await wait(visible('[data-view="start"]'));
    await check('no project: start guide shown, project views hidden', `${visible('[data-view="start"]')} && !${visible('[data-view="project"]')} && !${visible('[data-view="projects"]')}`);
    await check('rail lists only the four views', `[...document.querySelectorAll('[data-ws-view]')].map(a=>a.dataset.wsView).join()==='projects,inbox,account,settings'`);
    await check('account chip says not signed in', `document.getElementById('ws-account-chip').textContent==='로그인하지 않음'`);
    await capture('01-start-900.png', 900, 640);

    phase = 'register';
    await evaluate(`document.getElementById('ws-start-register').click()`);
    await wait(`${visible('[data-view="project"]')} && document.getElementById('ws-chip-changes').textContent.startsWith('변경')`);
    await check('registering opens the project overview', `${visible('[data-tab-panel="overview"]')} && document.getElementById('ws-tab-overview').getAttribute('aria-selected')==='true'`);
    await check('branch and change chips come from the real Git status', `document.getElementById('ws-chip-branch').textContent==='main' && document.getElementById('ws-chip-changes').textContent==='변경 4'`);
    await check('todo points at the changes tab', `document.getElementById('ws-todo-title').textContent.includes('변경 4개') && document.getElementById('ws-todo-action').textContent.startsWith('변경 보기')`);
    await check('window title carries project and branch', `document.title.includes('sample project') && document.title.includes('main')`);
    await capture('02-overview-900.png', 900, 640);

    phase = 'tabs';
    await evaluate(`document.getElementById('ws-todo-action').click()`);
    await wait(visible('[data-tab-panel="changes"]'));
    await check('todo action opens changes', `${visible('[data-tab-panel="changes"]')} && !${visible('[data-tab-panel="overview"]')}`);
    await key3('3');
    await check('Ctrl+3 opens AI review with the no-target notice', `${visible('[data-tab-panel="ai"]')} && ${visible('#ws-panel-ai .ws-need-snapshot')} && document.querySelector('[data-step-button="2"]').disabled`);
    await evaluate(`document.getElementById('ws-tab-ai').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}))`);
    await check('arrow keys move between tabs', `document.getElementById('ws-tab-proposals').getAttribute('aria-selected')==='true' && ${visible('#ws-proposal-empty')}`);
    await key3('2');

    phase = 'capture';
    await wait(`document.querySelectorAll('#change-selection input').length>=2`);
    await evaluate(`for(const item of document.querySelectorAll('#change-selection input'))item.checked=item.dataset.facet!=='untracked';document.getElementById('capture-changes').click()`);
    await wait(`document.getElementById('capture-message').textContent==='선택한 입력을 고정했습니다.'`);
    await check('capture hides the no-target notice and unlocks step 2', `!${visible('#ws-panel-ai .ws-need-snapshot')} && document.body.querySelector('main').dataset.hasSnapshot==='true'`);
    await check('overview todo moves on after capture', `document.getElementById('ws-todo-title').textContent.includes('점검 대상이 준비')`);
    await capture('03-changes-captured-900.png', 900, 640);

    phase = 'ai-steps';
    await key3('3');
    await check('step 1 shows the captured target and enables next', `document.getElementById('ws-ai-target').textContent.includes('담은 변경') && !document.getElementById('ws-ai-next-1').disabled && ${visible('[data-step="1"]')}`);
    await evaluate(`document.getElementById('ws-ai-next-1').click()`);
    await check('step 2 hides API key for the local model and the duplicate-cost consent', `${visible('[data-step="2"]')} && !${visible('#review-api-key')} && !${visible('#review-duplicate-risk')}`);
    await evaluate(`(()=>{const s=document.getElementById('review-profile');s.value='claude-sonnet-5-5-v1';s.dispatchEvent(new Event('change'))})()`);
    await check('API key field appears only for cloud models', visible('#review-api-key'));
    await evaluate(`(()=>{const s=document.getElementById('review-profile');s.value='ollama-local-v1';s.dispatchEvent(new Event('change'))})()`);
    await evaluate(`(()=>{const g=document.getElementById('review-generation');g.value='2';g.dispatchEvent(new Event('input'))})()`);
    await check('duplicate-cost consent appears when the run number is above 1', `getComputedStyle(document.getElementById('review-duplicate-risk').closest('label')).display!=='none'`);
    await evaluate(`(()=>{const g=document.getElementById('review-generation');g.value='1';g.dispatchEvent(new Event('input'))})()`);
    await check('preview button carries its risk label', `getComputedStyle(document.getElementById('review-preview'),'::after').content==='"읽기"'`);
    await check('start button is labelled as sending and disabled before consent', `getComputedStyle(document.getElementById('review-start'),'::after').content==='"전송"' && document.getElementById('review-start').disabled`);
    await capture('04-ai-step2-900.png', 900, 640);

    phase = 'tests-compare-impact';
    await key3('5');
    await evaluate(`document.getElementById('detect-validation').click()`);
    await wait(`document.getElementById('detected-tools').textContent.includes('package.json')`);
    await check('tool detection works from the tests tab', `document.querySelectorAll('#detected-tools button').length>=1`);
    await key3('6');
    await check('compare needs two snapshots and says so', `${visible('#ws-panel-compare .ws-need-two-snapshots')}`);
    await key3('7');
    await evaluate(`document.getElementById('analyze-impact').click()`);
    await wait(`document.getElementById('impact-view').childElementCount>0`);
    await check('impact keeps the separate evidence kinds labelled', `!!document.querySelector('#impact-view [data-evidence="history"]')`);
    await capture('05-impact-900.png', 900, 640);

    phase = 'views';
    await key3('4');
    await check('proposals tab explains how to create one', visible('#ws-proposal-empty'));
    await evaluate(`document.querySelector('[data-ws-view="inbox"]').click()`);
    await check('rail opens the inbox view and marks it current', `${visible('[data-view="inbox"]')} && document.querySelector('[data-ws-view="inbox"]').getAttribute('aria-current')==='page'`);
    await evaluate(`document.getElementById('ws-account-chip').click()`);
    await check('account chip opens the account view', `${visible('[data-view="account"]')} && document.getElementById('ws-connect-project').textContent==='sample project'`);
    await evaluate(`document.querySelector('[data-ws-view="settings"]').click()`);
    await check('settings holds editor, project defaults and diagnostics', `${visible('#editor-id')} && ${visible('#recent-limit')} && ${visible('#export-diagnostics')}`);

    phase = 'palette';
    await key3('k');
    await check('Ctrl+K opens the command palette', visible('.ws-palette'));
    await evaluate(`(()=>{const i=document.querySelector('.ws-palette input');i.value='개요';i.dispatchEvent(new Event('input'))})()`);
    await evaluate(`document.querySelector('.ws-palette input').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))`);
    await check('palette command runs and closes', `!document.querySelector('.ws-palette') && ${visible('[data-tab-panel="overview"]')}`);
    await key3('k'); await evaluate(`document.querySelector('.ws-palette input').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`);
    await check('Escape closes the palette', `!document.querySelector('.ws-palette')`);

    phase = 'overflow';
    const sizes: [number, number][] = [[600, 400], [900, 640], [1280, 800]];
    for (const [width, height] of sizes) {
      await capture(`10-size-${width}-overview.png`, width, height);
      for (const tab of ['overview', 'changes', 'ai', 'proposals', 'tests', 'compare', 'impact']) {
        await evaluate(`document.getElementById('ws-tab-${tab}').click()`);
        await check(`no horizontal overflow at ${width}x${height} (${tab})`, `(()=>{const m=document.querySelector('main');return m.scrollWidth<=m.clientWidth+1})()`);
      }
      for (const view of ['inbox', 'account', 'settings', 'projects']) {
        await evaluate(`window.dispatchEvent(new CustomEvent('workspace-navigate',{detail:{view:'${view}'}}))`);
        await check(`no horizontal overflow at ${width}x${height} (${view})`, `(()=>{const m=document.querySelector('main');return m.scrollWidth<=m.clientWidth+1})()`);
      }
      await evaluate(`document.querySelector('[data-ws-view="projects"]').click()`);
      await evaluate(`document.querySelector('#ws-project-list button').click()`);
      await check(`project header stays short at ${width}x${height}`, `document.querySelector('.ws-project-bar').getBoundingClientRect().height <= ${height <= 400 ? 110 : 140}`);
    }
    await capture('11-size-600-tab.png', 600, 400); await capture('12-size-1280-overview.png', 1280, 800);
    phase = 'preservation'; assert.deepEqual(await hashes(root), before); checks.push(['project files unchanged', true]);
    writeFileSync(join(output, 'phase5-result.json'), JSON.stringify({ status: 'passed', checks: checks.length, rendering: 'Electron offscreen software paint', fixtureOnly: true }, null, 1));
    process.stdout.write(`PASS Phase 5 workspace IA: ${checks.length} checks (real renderer/main/frozen agent/Git fixture), screenshots in ${output}\n`);
  } finally { health(); projects(); for (const unregister of others) unregister(); window.destroy(); await agent.stop(); clearTimeout(deadline); await rm(temporary, { recursive: true, force: true }); }
}).then(() => app.exit(0)).catch(error => { process.stderr.write(`FAIL Phase 5 (${phase}): ${error instanceof Error ? error.message.slice(0, 300) : 'assertion'}\nchecks passed: ${checks.filter(([, ok]) => ok).length}/${checks.length}\n`); app.exit(1); });
