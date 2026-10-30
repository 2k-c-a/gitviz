import './profile';
/** The workspace window loads before the agent starts. A healthy start must never flash the "cannot prepare" warning,
 * and an agent that never starts must still end in that warning. Real renderer, real main IPC and the frozen agent; no network.
 */
import assert from 'node:assert/strict';
import { app, BrowserWindow, safeStorage } from 'electron';
import { mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { Agent } from '../src/agent';
import { Contracts } from '../src/contracts';
import { registerHealth } from '../src/ipc';
import { registerScheme, serveAssets, APP_URL } from '../src/app-protocol';
import { loadKey } from '../src/secrets';

app.disableHardwareAcceleration();
registerScheme(); app.on('window-all-closed', () => {});
let phase = 'startup';
const checks: string[] = [];
const deadline = setTimeout(() => { process.stderr.write(`FAIL status health deadline (${phase})\n`); app.exit(1); }, 90000);
const WARNING = '로컬 구성을 준비할 수 없습니다';
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

void app.whenReady().then(async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'gitviz-status-'));
  const contracts = new Contracts(join(__dirname, '../schema'));
  const real = app.isPackaged ? join(process.resourcesPath, 'agent/gitviz-workspace-agent.exe') : join(__dirname, '../../../dist/workspace-agent/agent/gitviz-workspace-agent.exe');
  await serveAssets(join(__dirname, '../ui'));

  async function scenario(name: string, executable: string, startDelay: number, body: (ctx: { evaluate: (source: string) => Promise<unknown>; seen: Set<string>; started: Promise<void> }) => Promise<void>) {
    phase = name;
    const directory = join(temporary, name.replace(/\W+/g, '-'));
    const agent = new Agent(contracts);
    const key = () => loadKey(join(directory, 'key.dpapi'), { available: () => safeStorage.isAsyncEncryptionAvailable(), encrypt: value => safeStorage.encryptStringAsync(value), decrypt: async value => (await safeStorage.decryptStringAsync(value)).result });
    const window = new BrowserWindow({ width: 900, height: 640, show: false, webPreferences: { preload: join(__dirname, '../preload/preload.js'), sandbox: true, contextIsolation: true, nodeIntegration: false } });
    const unregister = registerHealth(window, () => agent.health());
    const evaluate = (source: string) => window.webContents.executeJavaScript(source);
    const seen = new Set<string>();
    let sampling = true;
    const sampler = (async () => {
      while (sampling) {
        try { seen.add(String(await evaluate(`(()=>{const h=document.getElementById('ws-health');return document.getElementById('status').textContent+'|'+(h&&!h.hidden?'banner':'')})()`))); } catch { /* page not ready yet */ }
        await sleep(40);
      }
    })();
    try {
      await window.loadURL(APP_URL);
      // The agent starts after the page has already asked for its health, exactly like main.ts.
      const started = sleep(startDelay).then(() => agent.start(executable, directory, key)).catch(() => { /* health reports unavailable */ });
      await body({ evaluate, seen, started });
    } finally {
      sampling = false; await sampler; unregister();
      await agent.stop().catch(() => undefined); window.destroy();
    }
  }
  const wait = async (evaluate: (s: string) => Promise<unknown>, source: string, ms: number) => {
    for (let i = 0; i < ms / 50; i++) { if (await evaluate(source)) return; await sleep(50); }
    throw new Error(`ASSERTION: ${phase}: ${source.slice(0, 120)}`);
  };
  const ok = (name: string, value: boolean) => { checks.push(name); assert.ok(value, `check failed: ${name}`); };

  try {
    await scenario('late-agent', real, 2000, async ({ evaluate, seen, started }) => {
      await wait(evaluate, `document.getElementById('status').textContent.length>0`, 5000);
      ok('while the agent is still starting the status says it is checking', await evaluate(`document.getElementById('status').textContent==='로컬 구성을 확인하는 중입니다.'`) === true);
      ok('and no warning banner is shown', await evaluate(`document.getElementById('ws-health').hidden`) === true);
      await started;
      await wait(evaluate, `document.getElementById('status').textContent==='로컬 워크스페이스 기반이 준비되었습니다.'`, 15000);
      ok('once the agent is ready the status says so without any user action', true);
      ok('the warning text and banner were never shown during a healthy start', ![...seen].some(text => text.includes(WARNING) || text.endsWith('|banner')));
      ok('the checking state was actually observed', [...seen].some(text => text.startsWith('로컬 구성을 확인하는 중입니다.')));
    });
    await scenario('agent-never-starts', join(temporary, 'missing-agent.exe'), 0, async ({ evaluate }) => {
      await wait(evaluate, `document.getElementById('status').textContent==='로컬 구성을 확인하는 중입니다.'`, 5000);
      ok('an agent that cannot start is first reported as checking', true);
      await wait(evaluate, `document.getElementById('status').textContent.startsWith(${JSON.stringify(WARNING)})`, 25000);
      ok('after the retry window the warning is shown', await evaluate(`!document.getElementById('ws-health').hidden`) === true);
      ok('the warning offers the manual refresh', await evaluate(`!document.getElementById('ws-health-retry').hidden && !document.getElementById('refresh').disabled`) === true);
    });
    process.stdout.write(`PASS status health: ${checks.length} checks (real renderer/main IPC/frozen agent; late start never warns, missing agent still warns)\n`);
    await rm(temporary, { recursive: true, force: true });
    clearTimeout(deadline); app.exit(0);
  } catch (error) {
    process.stderr.write(`FAIL ${phase}: ${error instanceof Error ? error.message : String(error)}\n`);
    clearTimeout(deadline); app.exit(1);
  }
});
