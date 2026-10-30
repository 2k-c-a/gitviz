import { randomUUID } from 'node:crypto';
import type { Agent } from './agent';
import type { Remote } from './remote';
import type { RemoteWorkspace } from './remote-workspace';

const unavailable = (value: Record<string, unknown>, reason: string): Record<string, unknown> => ({ ...value, historical: { status: 'unavailable', reason } });
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const text = (value: unknown, limit = 4096): value is string => typeof value === 'string' && value.length > 0 && value.length <= limit && ![...value].some(char => char.charCodeAt(0) < 32);
const ratio = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('BAD_RESPONSE');
  return value as Record<string, unknown>;
}
export class HistoricalImpact {
  constructor(private readonly agent: Pick<Agent, 'call'>, private readonly mapping: Pick<RemoteWorkspace, 'readProof' | 'generation'>,
    private readonly remote: Pick<Remote, 'request'>) {}
  async decorate(value: Record<string, unknown>): Promise<Record<string, unknown>> {
    const projectId = String(value.projectId), analysisId = String(value.analysisId);
    const generation = this.mapping.generation();
    if (generation === null) return unavailable(value, 'remote-evidence-unavailable');
    try {
      const proof = await this.mapping.readProof(projectId);
      const context = await this.agent.call('workspace.impact.historical', { phase: 'inspect', analysisId }, { projectId, idempotencyKey: randomUUID() });
      if (this.mapping.generation() !== generation) return unavailable(value, 'remote-binding-changed');
      const history = object(value.historical);
      if (history.status === 'available') {
        const binding = object(context.binding);
        if (Object.keys(binding).some(key => binding[key] !== proof[key])) return unavailable(value, 'remote-binding-changed');
        return value;
      }
      const paths = new Set(context.paths as string[]);
      if (!text(context.head, 64)) return unavailable(value, 'snapshot-head-unavailable');
      const repositoryId = Number(proof.repositoryId);
      const metaResponse = await this.remote.request({ endpointKey: 'snapshotMeta', path: { repositoryId }, query: {} });
      if (metaResponse.status !== 200) throw new Error('REMOTE_ACCESS_DENIED');
      const metadata = object(object(metaResponse.body).snapshot);
      if (!integer(metadata.id) || metadata.id < 1 || !text(metadata.target_sha, 64) || !/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(metadata.target_sha) ||
        !text(metadata.payload_sha256, 64) || !/^[0-9a-f]{64}$/.test(metadata.payload_sha256) || !text(metadata.formula_version, 128) ||
        !text(metadata.window_end, 128) || !Number.isFinite(Date.parse(metadata.window_end)) ||
        (metadata.window_start !== null && (!text(metadata.window_start, 128) || !Number.isFinite(Date.parse(metadata.window_start))))) throw new Error('BAD_RESPONSE');
      if (metadata.target_sha !== context.head) return unavailable(value, 'snapshot-head-mismatch');
      const response = await this.remote.request({ endpointKey: 'coupling', path: { repositoryId }, query: { snapshot_id: metadata.id, limit: 200, min_score: 0, sort_by: 'coupling' } });
      if (response.status !== 200) throw new Error('REMOTE_ACCESS_DENIED');
      const body = object(response.body), pinned = object(body._snapshot), stats = object(body.meta);
      if (pinned.id !== metadata.id || pinned.target_sha !== metadata.target_sha || pinned.payload_sha256 !== metadata.payload_sha256 ||
        !['pr', 'commit'].includes(String(stats.co_change_unit)) || !integer(stats.total_units) || !integer(stats.excluded_unit_count) ||
        !integer(stats.total_pairs) || !Array.isArray(body.pairs) || body.pairs.length > 200 || stats.total_pairs < body.pairs.length) throw new Error('BAD_RESPONSE');
      const pairs: Record<string, unknown>[] = [];
      let pairBytes = 0, outputTruncated = false;
      for (const raw of body.pairs) {
        const pair = object(raw);
        if (!text(pair.file_a_path) || !text(pair.file_b_path) || !integer(pair.co_change_count) || !integer(pair.change_count_a) || !integer(pair.change_count_b) ||
          !ratio(pair.a_to_b_ratio) || !ratio(pair.b_to_a_ratio) || !ratio(pair.coupling_score) || typeof pair.low_support !== 'boolean' ||
          pair.co_change_count > Math.min(pair.change_count_a, pair.change_count_b)) throw new Error('BAD_RESPONSE');
        if (paths.has(pair.file_a_path) || paths.has(pair.file_b_path)) {
          const selected = { pathA: pair.file_a_path, pathB: pair.file_b_path,
          support: pair.co_change_count, changesA: pair.change_count_a, changesB: pair.change_count_b, ratioAtoB: pair.a_to_b_ratio,
          ratioBtoA: pair.b_to_a_ratio, score: pair.coupling_score, lowSupport: pair.low_support };
          const bytes = Buffer.byteLength(JSON.stringify(selected));
          if (pairBytes + bytes > 60000) outputTruncated = true;
          else { pairs.push(selected); pairBytes += bytes; }
        }
      }
      const latest = await this.mapping.readProof(projectId);
      if (Object.keys(proof).filter(key => key !== 'expiresAt').some(key => proof[key] !== latest[key])) return unavailable(value, 'remote-binding-changed');
      const historical = { status: 'available', reason: 'historical-cochange-not-a-dependency', repositoryId, serverSnapshotId: metadata.id,
        targetSha: metadata.target_sha, payloadSha256: metadata.payload_sha256, formulaVersion: metadata.formula_version,
        windowStart: metadata.window_start, windowEnd: metadata.window_end, coChangeUnit: stats.co_change_unit, totalUnits: stats.total_units,
        excludedUnits: stats.excluded_unit_count, totalPairs: stats.total_pairs, truncated: outputTruncated || stats.total_pairs > body.pairs.length, pairs };
      const attached = await this.agent.call('workspace.impact.historical', { phase: 'attach', analysisId, proof: latest, historical }, { projectId, idempotencyKey: randomUUID() });
      if (this.mapping.generation() !== generation) return unavailable(value, 'remote-binding-changed');
      return { ...value, historical: attached.historical };
    } catch { return unavailable(value, 'remote-evidence-unavailable'); }
  }
}
