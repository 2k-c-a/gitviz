import { ipcMain, utilityProcess, type BrowserWindow } from 'electron';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import type { Agent } from './agent';
import type { Contracts } from './contracts';
import { workspaceSender } from './ipc';
import type { Source, Reference } from './impact-worker';

async function parse(files: Source[]): Promise<{ records: Reference[]; limitations: string[] }> {
  if (!files.length) return { records: [], limitations: [] };
  const env: NodeJS.ProcessEnv = {};
  for (const key of ['SystemRoot', 'WINDIR', 'TEMP', 'TMP']) if (process.env[key]) env[key] = process.env[key];
  const child = utilityProcess.fork(join(__dirname, 'impact-worker.js'), [], { stdio: 'pipe', env, execArgv: ['--max-old-space-size=256'], serviceName: 'GitViz fixed snapshot parser' });
  return new Promise((resolve, reject) => {
    let done = false; let bytes = 0;
    const timer = setTimeout(() => finish(new Error('PARSER_UNAVAILABLE')), 8000);
    function finish(error?: Error, result?: { records: Reference[]; limitations: string[] }): void {
      if (done) return; done = true; clearTimeout(timer); child.kill();
      if (error || !result) reject(error ?? new Error('PARSER_UNAVAILABLE')); else resolve(result);
    }
    for (const stream of [child.stdout, child.stderr]) stream?.on('data', (chunk: Buffer) => { bytes += chunk.length; if (bytes > 32768) finish(new Error('PARSER_UNAVAILABLE')); });
    child.on('exit', () => { if (!done) finish(new Error('PARSER_UNAVAILABLE')); });
    child.on('message', (value: unknown) => {
      if (!value || typeof value !== 'object' || Buffer.byteLength(JSON.stringify(value)) > 200000) { finish(new Error('OUTPUT_LIMIT')); return; }
      const data = value as { ok?: unknown; records?: unknown; limitations?: unknown };
      if (data.ok !== true || !Array.isArray(data.records) || !Array.isArray(data.limitations)) { finish(new Error('PARSER_UNAVAILABLE')); return; }
      finish(undefined, { records: data.records as Reference[], limitations: data.limitations as string[] });
    });
    child.postMessage({ files });
  });
}

export function registerImpact(window: BrowserWindow, agent: Pick<Agent, 'call'>, contracts: Contracts): () => void {
  let busy = false;
  ipcMain.handle('workspace:impact-analyze', async (event, options: unknown) => {
    if (!workspaceSender(event, window)) throw new Error('ACCESS_DENIED');
    if (!options || typeof options !== 'object' || Array.isArray(options)) throw new Error('INVALID_REQUEST');
    const value = options as Record<string, unknown>;
    const { projectId, ...payload } = value;
    contracts.request({ protocolVersion: 1, requestId: randomUUID(), operation: 'workspace.impact.prepare', projectId, payload });
    if (busy) throw new Error('BUSY'); busy = true;
    try {
      const scope = { projectId: String(projectId) };
      const prepared = await agent.call('workspace.impact.prepare', payload, scope);
      const files: Source[] = [];
      for (const file of prepared.files as { fileId: string; language: string; path: string; facet: string; sourceHash: string; byteCount: number }[]) {
        if (file.language !== 'typescript') continue;
        const chunks: Buffer[] = [];
        for (let offset = 0; offset < file.byteCount; offset += 16000) {
          const read = await agent.call('workspace.impact.read', { sessionId: prepared.sessionId, fileId: file.fileId, offset }, scope);
          chunks.push(Buffer.from(String(read.chunkBase64), 'base64'));
        }
        files.push({ fileId: file.fileId, path: file.path, facet: file.facet, sourceHash: file.sourceHash, text: Buffer.concat(chunks).toString('utf8') });
      }
      const parsed = await parse(files);
      for (let index = 0; index < parsed.records.length; index += 25) await agent.call('workspace.impact.append', { sessionId: prepared.sessionId, records: parsed.records.slice(index, index + 25) }, scope);
      return await agent.call('workspace.impact.finish', { sessionId: prepared.sessionId, limitations: parsed.limitations }, { ...scope, idempotencyKey: randomUUID() });
    } catch (error) {
      // eslint-disable-next-line preserve-caught-error -- No parser/code/native diagnostics cross the renderer boundary.
      throw new Error(error instanceof Error && /^[A-Z_]+$/.test(error.message) ? error.message : 'PARSER_UNAVAILABLE');
    } finally { busy = false; }
  });
  ipcMain.handle('workspace:impact-get', async (event, options: unknown) => {
    if (!workspaceSender(event, window) || !options || typeof options !== 'object' || Array.isArray(options)) throw new Error('INVALID_REQUEST');
    const { projectId, ...payload } = options as Record<string, unknown>;
    contracts.request({ protocolVersion: 1, requestId: randomUUID(), operation: 'workspace.impact.get', projectId, payload });
    return await agent.call('workspace.impact.get', payload, { projectId: String(projectId) });
  });
  return () => { ipcMain.removeHandler('workspace:impact-analyze'); ipcMain.removeHandler('workspace:impact-get'); };
}
