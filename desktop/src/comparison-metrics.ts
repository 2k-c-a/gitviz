import { utilityProcess } from 'electron';
import { join } from 'node:path';
import type { Agent } from './agent';
import type { Source } from './impact-worker';
import type { TypescriptMetric } from './metrics';

async function worker(files: Source[]): Promise<TypescriptMetric[]> {
  if (!files.length) return [];
  const env: NodeJS.ProcessEnv = {};
  for (const key of ['SystemRoot', 'WINDIR', 'TEMP', 'TMP']) if (process.env[key]) env[key] = process.env[key];
  const child = utilityProcess.fork(join(__dirname, 'impact-worker.js'), [], { stdio: 'pipe', env,
    execArgv: ['--max-old-space-size=256'], serviceName: 'GitViz fixed snapshot metrics' });
  return new Promise((resolve, reject) => {
    let done = false, bytes = 0;
    const timer = setTimeout(() => finish(), 8000);
    function finish(metrics?: TypescriptMetric[]): void {
      if (done) return; done = true; clearTimeout(timer); child.kill();
      if (metrics) resolve(metrics); else reject(new Error('PARSER_UNAVAILABLE'));
    }
    for (const stream of [child.stdout, child.stderr]) stream?.on('data', (chunk: Buffer) => { bytes += chunk.length; if (bytes > 32768) finish(); });
    child.on('exit', () => finish());
    child.on('message', (message: unknown) => {
      if (!message || typeof message !== 'object' || Buffer.byteLength(JSON.stringify(message)) > 200000) { finish(); return; }
      const result = message as { ok?: boolean; metrics?: TypescriptMetric[] };
      finish(result.ok && Array.isArray(result.metrics) ? result.metrics : undefined);
    });
    child.postMessage({ mode: 'metrics', files });
  });
}

export async function collectMetrics(agent: Pick<Agent, 'call'>, projectId: string, snapshotId: string): Promise<TypescriptMetric[] | null> {
  const scope = { projectId };
  const prepared = await agent.call('workspace.impact.prepare', { snapshotId, proposalId: null, historySnapshotRef: null }, scope);
  try {
    const sources: Source[] = [];
    for (const file of prepared.files as { fileId: string; path: string; facet: string; sourceHash: string; byteCount: number; language: string }[]) {
      if (file.language !== 'typescript') continue;
      const chunks: Buffer[] = [];
      for (let offset = 0; offset < file.byteCount; offset += 16000) {
        const chunk = await agent.call('workspace.impact.read', { sessionId: prepared.sessionId, fileId: file.fileId, offset }, scope);
        chunks.push(Buffer.from(String(chunk.chunkBase64), 'base64'));
      }
      sources.push({ fileId: file.fileId, path: file.path, facet: file.facet, sourceHash: file.sourceHash, text: Buffer.concat(chunks).toString('utf8') });
    }
    try { return await worker(sources); } catch { return null; }
  } finally { await agent.call('workspace.impact.release', { sessionId: prepared.sessionId }, scope); }
}
