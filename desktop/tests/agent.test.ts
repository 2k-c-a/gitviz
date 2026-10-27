import assert from 'node:assert/strict';
import { test } from 'node:test';
import { join } from 'node:path';
import { Agent } from '../src/agent';
import { Contracts } from '../src/contracts';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import type { spawn } from 'node:child_process';

function fixtureAgent(reply: (input: Record<string, unknown>, output: PassThrough, diagnostics: PassThrough) => void): Agent {
  const emitter = new EventEmitter();
  const child = Object.assign(emitter, {
    stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(),
    exitCode: null as number | null,
    kill() { this.exitCode = 1; queueMicrotask(() => emitter.emit('close', 1)); return true; },
  });
  child.stdin.on('data', (bytes: Buffer) => reply(JSON.parse(bytes.toString()) as Record<string, unknown>, child.stdout, child.stderr));
  const launch = (() => child) as unknown as typeof spawn;
  return new Agent(new Contracts(join(__dirname, '../schema')), launch);
}

test('unstarted agent reports no native state or paths; stopping is safe', async () => {
  const agent = new Agent(new Contracts(join(__dirname, '../schema')));
  assert.deepEqual(agent.health(), { state: 'unavailable', components: { store: 'unavailable', secrets: 'unavailable', lifecycle: 'unavailable' } });
  await agent.stop();
});
test('unknown response ID and truncated stdout fail before any key is released', async () => {
  for (const invalid of ['unknown-id', 'truncated']) {
    let keyCalls = 0;
    const agent = fixtureAgent((_request, output) => {
      if (invalid === 'truncated') output.end('{');
      else output.write(JSON.stringify({ protocolVersion: 1, requestId: '00000000-0000-4000-8000-000000000001', ok: true, result: { accepted: true } }) + '\n');
    });
    await assert.rejects(agent.start('fixture', 'fixture', async () => { keyCalls++; return Buffer.alloc(32); }));
    assert.equal(keyCalls, 0); assert.equal(agent.health().state, 'unavailable');
    await agent.stop();
  }
});
test('stderr flood after handshake stops initialization and does not become UI output', async () => {
  const agent = fixtureAgent((request, output, diagnostics) => {
    if (request.operation === 'workspace.system.handshake') output.write(JSON.stringify({ protocolVersion: 1, requestId: request.requestId, ok: true, result: { agentVersion: '0.1.0', protocolVersion: 1, localSchemaRange: { min: 6, max: 6 }, capabilities: ['workspace.system.health', 'workspace.system.shutdown'], state: 'initializing' } }) + '\n');
    else diagnostics.write(Buffer.alloc(65537, 65));
  });
  await assert.rejects(agent.start('fixture', 'fixture', async () => Buffer.alloc(32)));
  assert.equal(agent.health().state, 'unavailable'); await agent.stop();
});
test('storage recovery classification survives transport shutdown', async () => {
  const agent = fixtureAgent((request, output) => {
    if (!request.operation) return;
    const result = request.operation === 'workspace.system.handshake'
      ? { agentVersion: '0.1.0', protocolVersion: 1, localSchemaRange: { min: 6, max: 6 }, capabilities: ['workspace.system.health', 'workspace.system.shutdown'], state: 'initializing' }
      : { state: 'unavailable', components: { store: 'unavailable', secrets: 'unavailable', lifecycle: 'ready' }, errorCode: 'RECOVERY_REQUIRED' };
    output.write(JSON.stringify({ protocolVersion: 1, requestId: request.requestId, ok: true, result }) + '\n');
  });
  await assert.rejects(agent.start('fixture', 'fixture', async () => Buffer.alloc(32)));
  await agent.stop();
  assert.equal(agent.health().errorCode, 'RECOVERY_REQUIRED');
});
