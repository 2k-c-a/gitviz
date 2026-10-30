/** Owned frozen-process termination after cloud request handoff; no provider/network call. */
import assert from 'node:assert/strict';
import { safeStorage } from 'electron';
import { execFileSync, spawn, type ChildProcessWithoutNullStreams, type SpawnOptions } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { Agent } from '../src/agent';
import type { Contracts } from '../src/contracts';
import { loadKey } from '../src/secrets';

export async function cloudRecovery(executable: string, contracts: Contracts, temporary: string): Promise<void> {
  const root = join(temporary, 'cloud-recovery-root'), directory = join(temporary, 'cloud-recovery-data'); await mkdir(root);
  const git = (...args: string[]) => execFileSync(join(process.env.ProgramFiles ?? 'C:/Program Files', 'Git/cmd/git.exe'), ['-C', root, ...args], {
    env: { SystemRoot: process.env.SystemRoot, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: 'NUL' }, stdio: 'pipe', windowsHide: true });
  git('init', '-b', 'main'); git('config', 'user.name', 'fixture'); git('config', 'user.email', 'fixture@example.invalid');
  await writeFile(join(root, 'app.py'), 'value = 1\n'); git('add', '.'); git('commit', '-m', 'fixture'); git('remote', 'add', 'origin', 'https://github.com/o/r');
  const slot: { child?: ChildProcessWithoutNullStreams } = {};
  const launch = ((binary: string, args: readonly string[], options: SpawnOptions) => {
    const child = spawn(binary, args, { ...options, stdio: 'pipe' }); slot.child = child; return child;
  }) as typeof spawn;
  const key = () => loadKey(join(directory, 'key.dpapi'), { available: () => safeStorage.isAsyncEncryptionAvailable(),
    encrypt: value => safeStorage.encryptStringAsync(value), decrypt: async value => (await safeStorage.decryptStringAsync(value)).result });
  let agent = new Agent(contracts, launch);
  try {
    await agent.start(executable, directory, key);
    const selected = await agent.call('workspace.projects.selectAndRegister', { phase: 'inspect', selectedRoot: root, metadataScopes: [], expandedRoot: false, reconnectApproved: false }, { idempotencyKey: randomUUID() });
    const project = await agent.call('workspace.projects.selectAndRegister', { phase: 'register', selectionId: selected.selectionId }, { idempotencyKey: randomUUID() });
    const scope = { projectId: String(project.id) };
    const inspected = await agent.call('workspace.projects.connectRemote', { phase: 'inspect' }, { ...scope, idempotencyKey: randomUUID() });
    const proof = { accountId: '11', serverOrigin: 'https://fixture.invalid', repositoryId: 2, githubId: 123, canonicalUrl: 'https://github.com/o/r', configHash: inspected.configHash };
    const connected = await agent.call('workspace.projects.connectRemote', { phase: 'connect', ...proof, expectedVersion: inspected.projectVersion, remoteName: 'origin' }, { ...scope, idempotencyKey: randomUUID() });
    const snapshot = await agent.call('workspace.changes.capture', { items: [{ facet: 'selected', path: 'app.py' }], includeUntracked: false }, { ...scope, idempotencyKey: randomUUID() });
    const credentialRef = randomUUID();
    await agent.call('workspace.reviews.cloudContext', { phase: 'authorize', profileId: 'gpt-5.4-v1', credentialRef, credentialVersion: 1,
      proof: { ...proof, connectionId: connected.connectionId, visibility: 'public', expiresAt: Date.now() / 1000 + 30 } }, scope);
    const preview = await agent.call('workspace.reviews.preview', { snapshotId: snapshot.snapshotId, kind: 'review', purpose: 'Recovery fixture', profileId: 'gpt-5.4-v1', generation: 1, credentialRef }, scope);
    const consent = { previewId: preview.previewId, previewHash: preview.previewHash, manifestHash: preview.manifestHash, maxEstimatedCost: preview.maxEstimatedCost, acknowledged: true, duplicateRiskAcknowledged: false };
    const operationKey = randomUUID();
    const run = await agent.call('workspace.reviews.start', consent, { ...scope, idempotencyKey: operationKey });
    let taken = false;
    for (let i = 0; i < 100; i++) {
      const request = await agent.call('workspace.reviews.cloudExchange', { phase: 'take', runId: run.runId }, scope);
      if (request.ready === true) { taken = true; break; }
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    assert.equal(taken, true); assert.ok(Number(run.budgetReserved) > 0);
    assert.ok(slot.child); const child = slot.child;
    const closed = new Promise<void>(resolve => child.once('close', () => resolve()));
    child.kill(); await closed; await agent.stop();
    agent = new Agent(contracts, launch); await agent.start(executable, directory, key);
    const recovered = await agent.call('workspace.reviews.get', { runId: run.runId }, scope);
    assert.equal(recovered.status, 'uncertain'); assert.equal(recovered.attemptCount, 1); assert.equal(recovered.outcomeUncertain, true);
    assert.equal(recovered.budgetReserved, run.budgetReserved);
    const replay = await agent.call('workspace.reviews.start', consent, { ...scope, idempotencyKey: operationKey });
    assert.equal(replay.runId, run.runId); assert.equal(replay.status, 'uncertain');
    const request = await agent.call('workspace.reviews.cloudExchange', { phase: 'take', runId: run.runId }, scope);
    assert.equal(request.ready, false, 'restart cannot replay a billable request');
    assert.equal(await readFile(join(root, 'app.py'), 'utf8'), 'value = 1\n');
  } finally { await agent.stop(); }
}
