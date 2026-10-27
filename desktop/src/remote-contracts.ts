import Ajv, { type ValidateFunction } from 'ajv';

type Rule = 'id' | 'uuid' | 'sha' | 'provider' | 'text' | 'number' | 'boolean';
interface Endpoint { method: string; template: string; path: Record<string, Rule>; query: Record<string, Rule>; body?: string; key?: boolean }
const definition = (method: string, template: string, path: Record<string, Rule> = {}, query: Record<string, Rule> = {}, body?: string, key?: boolean): Endpoint => ({ method, template, path, query, ...(body ? { body } : {}), ...(key ? { key } : {}) });
const analytics = (suffix: string, query: Record<string, Rule> = {}, path: Record<string, Rule> = {}) => definition('GET', `/repositories/{repositoryId}/analytics/${suffix}`, { repositoryId: 'id', ...path }, { snapshot_id: 'id', ...query });
const ai = (method: string, suffix: string, path: Record<string, Rule> = {}, query: Record<string, Rule> = {}, body?: string, key?: boolean) => definition(method, `/api/ai/v1/${suffix}`, path, query, body, key);
const page: Record<string, Rule> = { cursor: 'text', limit: 'number' };
export const endpoints: Record<string, Endpoint> = {
  me: definition('GET', '/api/auth/me'),
  repositories: definition('GET', '/repositories', {}, { limit: 'number' }),
  mine: definition('GET', '/repositories/mine'),
  repository: definition('GET', '/repositories/{repositoryId}', { repositoryId: 'id' }),
  registerRepository: definition('POST', '/repositories', {}, { github_url: 'text' }),
  reanalyze: definition('POST', '/repositories/{repositoryId}/reanalyze', { repositoryId: 'id' }),
  repositoryStatus: definition('GET', '/repositories/{repositoryId}/status', { repositoryId: 'id' }),
  search: definition('GET', '/search/github-repositories', {}, { q: 'text', sort: 'text', per_page: 'number' }),
  snapshotMeta: analytics('snapshot'), summary: analytics('summary'), timeline: analytics('timeline', { months: 'number' }),
  commitTypes: analytics('commit-types'), releases: analytics('releases', { limit: 'number' }),
  heatmap: analytics('contributor-heatmap', { weeks: 'number', limit: 'number' }), contributors: analytics('top-contributors', { limit: 'number' }),
  risk: analytics('risk-scores', { limit: 'number' }), busFactor: analytics('bus-factor', { limit: 'number' }),
  coupling: analytics('coupling', { limit: 'number', min_score: 'number', sort_by: 'text' }),
  fileCoupling: analytics('coupling/{fileId}', { limit: 'number' }, { fileId: 'id' }),
  network: analytics('network', { limit_nodes: 'number', min_edge_weight: 'number' }),
  networkPairs: analytics('network/top-pairs', { limit: 'number' }),
  ownedFiles: analytics('contributors/{contributorId}/owned-files', { limit: 'number' }, { contributorId: 'id' }),
  collaborators: analytics('contributors/{contributorId}/collaborators', { limit: 'number' }, { contributorId: 'id' }),
  fileDetail: analytics('files/{fileId}/detail', { limit: 'number', sort_by: 'text' }, { fileId: 'id' }), tree: analytics('tree'),
  snapshots: definition('GET', '/repositories/{repositoryId}/snapshots', { repositoryId: 'id' }, { limit: 'number', before_id: 'id' }),
  compare: definition('GET', '/repositories/{repositoryId}/snapshots/compare', { repositoryId: 'id' }, { before: 'id', after: 'id', offset: 'number', limit: 'number' }),
  providers: ai('GET', 'providers'), credentials: ai('GET', 'credentials'), usage: ai('GET', 'usage'),
  putCredential: ai('PUT', 'credentials/{provider}', { provider: 'provider' }, {}, 'RegisterKeyRequest'),
  patchCredential: ai('PATCH', 'credentials/{provider}', { provider: 'provider' }, {}, 'UpdateCredentialRequest'),
  deleteCredential: ai('DELETE', 'credentials/{provider}', { provider: 'provider' }),
  pulls: ai('GET', 'pulls', {}, { repository_id: 'id', state: 'text', ...page }),
  pullFiles: ai('GET', 'pulls/{pr}', { pr: 'id' }),
  prepareInput: ai('POST', 'inputs', {}, {}, 'PrepareInputRequest', true),
  input: ai('GET', 'inputs/{inputId}', { inputId: 'uuid' }, { profile_id: 'text' }),
  inputFile: ai('GET', 'inputs/{inputId}/files/{fileId}', { inputId: 'uuid', fileId: 'uuid' }, { side: 'text', start_line: 'number', end_line: 'number' }),
  deleteInput: ai('DELETE', 'inputs/{inputId}', { inputId: 'uuid' }),
  createJob: ai('POST', 'jobs', {}, {}, 'CreateJobRequest', true),
  jobs: ai('GET', 'jobs', {}, { repository_id: 'id', ...page, kind: 'text', status: 'text', input_id: 'uuid' }),
  job: ai('GET', 'jobs/{jobId}', { jobId: 'uuid' }), cancelJob: ai('POST', 'jobs/{jobId}/cancel', { jobId: 'uuid' }),
  retryJob: ai('POST', 'jobs/{jobId}/retry', { jobId: 'uuid' }, {}, 'RetryJobRequest', true),
  deleteJob: ai('DELETE', 'jobs/{jobId}', { jobId: 'uuid' }),
  result: ai('GET', 'results/{resultId}', { resultId: 'uuid' }), findings: ai('GET', 'results/{resultId}/findings', { resultId: 'uuid' }, page),
  sharedResults: ai('GET', 'shared/results', {}, { repository_id: 'id', ...page }),
  sharedResult: ai('GET', 'shared/results/{resultId}', { resultId: 'uuid' }),
  sharedFindings: ai('GET', 'shared/results/{resultId}/findings', { resultId: 'uuid' }, page),
  historyOptions: ai('GET', 'history/snapshots', {}, { repository_id: 'id' }),
  history: ai('GET', 'results/{resultId}/history', { resultId: 'uuid' }),
  attachHistory: ai('POST', 'results/{resultId}/history', { resultId: 'uuid' }, {}, 'AttachHistoryRequest'),
  commits: ai('GET', 'commits', {}, { repository_id: 'id', cursor: 'text' }),
  commitFiles: ai('GET', 'commits/{sha}/files', { sha: 'sha' }, { repository_id: 'id', cursor: 'text' }),
  feedback: ai('PUT', 'findings/{findingId}/feedback', { findingId: 'uuid' }, {}, 'FeedbackRequest'),
};
endpoints.pullFiles = ai('GET', 'pulls/{pr}/files', { pr: 'id' }, { repository_id: 'id', ...page });

export const UUID_PATTERN = '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';
const property = (rule: Rule) => {
  if (rule === 'id') return { type: 'integer', minimum: 1, maximum: Number.MAX_SAFE_INTEGER };
  if (rule === 'number') return { type: 'number', minimum: 0, maximum: 1000000 };
  if (rule === 'boolean') return { type: 'boolean' };
  const patterns = { uuid: UUID_PATTERN, sha: '^(?:[0-9a-f]{40}|[0-9a-f]{64})$', provider: '^[a-z][a-z0-9_-]{1,31}$', text: '^[^\\u0000-\\u001f\\u007f]*$' };
  return { type: 'string', maxLength: rule === 'text' ? 4096 : 128, pattern: patterns[rule] };
};
const object = (rules: Record<string, Rule>, required: string[] = []) => ({ type: 'object', additionalProperties: false, properties: Object.fromEntries(Object.entries(rules).map(([key, rule]) => [key, property(rule)])), required });

export interface RemoteRequest { endpointKey: string; path: Record<string, string | number>; query: Record<string, string | number | boolean>; body?: unknown; idempotencyKey?: string }
export class RemoteContracts {
  private validators = new Map<string, ValidateFunction>();
  constructor(bodies: Record<string, Record<string, unknown>>) {
    const ajv = new Ajv({ allErrors: false, strict: false, coerceTypes: false, useDefaults: false });
    ajv.addFormat('uuid', new RegExp(UUID_PATTERN));
    ajv.addFormat('password', { type: 'string', validate: () => true });
    ajv.addFormat('date-time', { type: 'string', validate: value => /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value)) });
    for (const [name, endpoint] of Object.entries(endpoints)) {
      this.validators.set(name, ajv.compile({ type: 'object', additionalProperties: false,
        ...(endpoint.body ? { $defs: bodies[endpoint.body].$defs } : {}),
        required: ['endpointKey', 'path', 'query', ...(endpoint.body ? ['body'] : []), ...(endpoint.key ? ['idempotencyKey'] : [])],
        properties: { endpointKey: { const: name }, path: object(endpoint.path, Object.keys(endpoint.path)), query: object(endpoint.query),
          ...(endpoint.body ? { body: bodies[endpoint.body] } : {}),
          ...(endpoint.key ? { idempotencyKey: { type: 'string', minLength: 1, maxLength: 255, pattern: '^[!-~]+$' } } : {}) } }));
    }
  }
  validate(value: unknown): asserts value is RemoteRequest {
    if (!value || typeof value !== 'object' || Array.isArray(value) || Buffer.byteLength(JSON.stringify(value)) > 256000 ||
      !this.validators.get((value as Record<string, unknown>).endpointKey as string)?.(value)) throw new Error('INVALID_REQUEST');
  }
  route(value: unknown): { method: string; path: string; body?: string; idempotencyKey?: string } {
    this.validate(value);
    const endpoint = endpoints[value.endpointKey];
    const path = endpoint.template.replace(/\{(\w+)\}/g, (_match, key: string) => encodeURIComponent(String(value.path[key])));
    const query = new URLSearchParams(Object.entries(value.query).map(([k, v]) => [k, String(v)]));
    return { method: endpoint.method, path: path + (query.size ? `?${query}` : ''),
      ...(endpoint.body ? { body: JSON.stringify(value.body) } : {}), ...(value.idempotencyKey ? { idempotencyKey: value.idempotencyKey } : {}) };
  }
}
