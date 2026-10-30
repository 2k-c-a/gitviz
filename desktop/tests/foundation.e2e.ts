import { profileDirectory } from './profile';
/** Real Electron process tests, not a Node mock of BrowserWindow. */
import assert from 'node:assert/strict';
import { app, safeStorage } from 'electron';
import { basename, dirname, join, resolve, sep } from 'node:path';
import { realpathSync, writeFileSync } from 'node:fs';
import { mkdtemp, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { APP_URL, registerScheme, serveAssets } from '../src/app-protocol';
import { createWindow } from '../src/window';
import { registerHealth, trustedSender } from '../src/ipc';
import { Agent } from '../src/agent';
import { Contracts } from '../src/contracts';
import { loadKey } from '../src/secrets';

registerScheme();
// Keep the test process alive while asynchronous cleanup and evidence writes finish.
app.on('window-all-closed', () => {});
function report(status: 'passed' | 'failed'): void {
  const file = process.env.GITVIZ_VERIFICATION_REPORT;
  if (!file) return;
  // Windows may expose the same temporary directory through an 8.3 alias.
  // Resolve existing directories before enforcing the temporary-folder boundary.
  const parent = realpathSync.native(dirname(resolve(file)));
  const temporary = realpathSync.native(tmpdir());
  if (!parent.toLowerCase().startsWith((temporary + sep).toLowerCase())) throw new Error('ACCESS_DENIED');
  writeFileSync(join(parent, basename(file)), JSON.stringify({ status, packaged: app.isPackaged, checks: status === 'passed' ? ['sandbox-preload', 'CSP', 'sender', 'window-restore', 'bundled-agent', 'DPAPI-restart', 'storage-recovery', 'shutdown'] : [] }), { flag: 'wx' });
}
const deadline = setTimeout(() => { report('failed'); app.exit(1); }, 45000);
void app.whenReady().then(async () => {
  const directory = await mkdtemp(join(tmpdir(), 'gitviz-foundation-'));
  const window = createWindow(false);
  const agent = new Agent(new Contracts(join(__dirname, '../schema')));
  let activeAgent = agent;
  const unregister = registerHealth(window, () => activeAgent.health());
  try {
    assert.equal(realpathSync.native(app.getPath('userData')), realpathSync.native(profileDirectory));
    assert.equal(realpathSync.native(app.getPath('sessionData')), realpathSync.native(profileDirectory));
    await serveAssets(join(__dirname, '../ui'));
    await window.loadURL(APP_URL);
    const state = await window.webContents.executeJavaScript(`(async()=>({node:typeof require,process:typeof process,keys:Object.keys(window.workspace),health:await window.workspace.getHealth()}))()`);
    assert.equal(state.node, 'undefined'); assert.equal(state.process, 'undefined');
    assert.deepEqual(state.keys.sort(), ['connectRemote', 'getRemoteConnection', 'openRemoteAnalysis', 'exportDiagnostics', 'runValidation', 'getValidation', 'cancelValidation', 'createComparison', 'getComparison', 'configureEditor', 'openEditor', 'copyLocation', 'setEditor', 'analyzeImpact', 'applyProposal', 'approveProposal', 'cancelReview', 'captureChanges', 'createProposal', 'detectValidation', 'feedbackReview', 'getGitStatus', 'getHealth', 'getImpact', 'getProject', 'getProposal', 'getReview', 'listProjects', 'listRecoveryProposals', 'listReviews', 'planValidation', 'previewFile', 'previewReview', 'recoverProposal', 'rejectProposal', 'requestProposalRevision', 'selectAndRegister', 'startReview', 'unregisterProject', 'updateSettings', 'listNotifications', 'markNotificationRead', 'subscribeEvents', 'unsubscribeEvents'].sort()); assert.equal(state.health.state, 'unavailable');
    assert.equal(trustedSender({ sender: window.webContents, senderFrame: null } as never, window), false);
    await assert.rejects(window.webContents.executeJavaScript(`fetch('https://example.com').then(r=>r.text())`));
    await window.webContents.executeJavaScript(`window.__xss = false; const element=document.createElement('script');element.textContent='window.__xss=true';document.body.append(element);`);
    assert.equal(await window.webContents.executeJavaScript('window.__xss'), false);
    const actualSender = { sender: window.webContents, senderFrame: window.webContents.mainFrame };
    assert.equal(trustedSender(actualSender as never, window), true);
    const otherWindow = createWindow(false);
    try { assert.equal(trustedSender({ ...actualSender, sender: otherWindow.webContents } as never, window), false); }
    finally { otherWindow.destroy(); }
    const stateFile = join(directory, 'window.json');
    await writeFile(stateFile, JSON.stringify({ x: -999999, y: -999999, width: 800, height: 500, maximized: false }));
    const restored = createWindow(false, stateFile);
    assert.ok(restored.getBounds().x > -999999);
    await new Promise<void>(resolve => { restored.once('closed', resolve); restored.close(); });
    assert.ok(JSON.parse(await readFile(stateFile, 'utf8')).x > -999999);
    const executable = app.isPackaged ? join(process.resourcesPath, 'agent/gitviz-workspace-agent.exe') : join(__dirname, '../../../build-output/agent/dist/agent/gitviz-workspace-agent.exe');
    for (let turn = 0; turn < 2; turn++) {
      const current = turn === 0 ? agent : new Agent(new Contracts(join(__dirname, '../schema')));
      await current.start(executable, directory, () => loadKey(join(directory, 'key.dpapi'), {
        available: () => safeStorage.isAsyncEncryptionAvailable(),
        encrypt: value => safeStorage.encryptStringAsync(value),
        decrypt: async value => (await safeStorage.decryptStringAsync(value)).result,
      }));
      assert.equal(current.health().state, 'ready');
      await current.stop();
    }
    const keyFile = join(directory, 'key.dpapi');
    const probeFile = join(directory, 'secret-check.bin');
    const encryptedProbe = await readFile(probeFile);
    const protectedKey = await readFile(keyFile);
    const keyProvider = () => loadKey(keyFile, {
      available: () => safeStorage.isAsyncEncryptionAvailable(), encrypt: value => safeStorage.encryptStringAsync(value),
      decrypt: async value => (await safeStorage.decryptStringAsync(value)).result,
    });
    // Failure injection affects generated fixtures only, never a user profile.
    await writeFile(probeFile, 'corrupted-encrypted-fixture');
    const broken = new Agent(new Contracts(join(__dirname, '../schema'))); activeAgent = broken;
    try {
      await assert.rejects(broken.start(executable, directory, keyProvider)); await broken.stop();
      assert.equal(broken.health().errorCode, 'RECOVERY_REQUIRED');
      assert.equal(await readFile(probeFile, 'utf8'), 'corrupted-encrypted-fixture');
      const health = await window.webContents.executeJavaScript('window.workspace.getHealth()');
      assert.equal(health.errorCode, 'RECOVERY_REQUIRED');
    } finally { await broken.stop(); await writeFile(probeFile, encryptedProbe); }
    for (const failure of ['missing-key', 'corrupt-key']) {
      if (failure === 'missing-key') await rename(keyFile, `${keyFile}.fixture`);
      else await writeFile(keyFile, 'corrupt-protected-fixture');
      const denied = new Agent(new Contracts(join(__dirname, '../schema')));
      try {
        await assert.rejects(denied.start(executable, directory, keyProvider)); await denied.stop();
        if (failure === 'missing-key') await assert.rejects(readFile(keyFile), { code: 'ENOENT' });
        else assert.equal(await readFile(keyFile, 'utf8'), 'corrupt-protected-fixture');
        assert.deepEqual(await readFile(probeFile), encryptedProbe);
      } finally {
        await denied.stop();
        if (failure === 'missing-key') await rename(`${keyFile}.fixture`, keyFile);
        else await writeFile(keyFile, protectedKey);
      }
    }
    const recovered = new Agent(new Contracts(join(__dirname, '../schema')));
    try { await recovered.start(executable, directory, keyProvider); assert.equal(recovered.health().state, 'ready'); }
    finally { await recovered.stop(); }
    console.log('PASS actual sandbox/preload/CSP/sender, bundled agent, DPAPI restart and shutdown');
  } finally {
    unregister(); window.destroy(); await activeAgent.stop(); await agent.stop();
    assert.equal(dirname(resolve(directory)), resolve(tmpdir())); assert.ok(basename(directory).startsWith('gitviz-foundation-'));
    await rm(directory, { recursive: true }); clearTimeout(deadline);
  }
  report('passed'); app.exit(0);
}).catch(() => { console.error('FAIL foundation E2E'); clearTimeout(deadline); report('failed'); app.exit(1); });
