import Ajv, { type ValidateFunction } from 'ajv';

type Rule = 'id' | 'uuid' | 'sha' | 'provider' | 'text' | 'token' | 'number' | 'boolean';
interface Endpoint { method: string; template: string; path: Record<string, Rule>; query: Record<string, Rule>; body?: string; key?: boolean; aiKey?: boolean }
const definition = (method: string, template: string, path: Record<string, Rule> = {}, query: Record<string, Rule> = {}, body?: string, key?: boolean): Endpoint => ({ method, template, path, query, ...(body ? { body } : {}), ...(key ? { key } : {}) });
const analytics = (suffix: string, query: Record<string, Rule> = {}, path: Record<string, Rule> = {}) => definition('GET', `/repositories/{repositoryId}/analytics/${suffix}`, { repositoryId: 'id', ...path }, { snapshot_id: 'id', ...query });
const ai = (method: string, suffix: string, path: Record<string, Rule> = {}, query: Record<string, Rule> = {}, body?: string, key?: boolean) => definition(method, `/api/ai/v1/${suffix}`, path, query, body, key);
const page: Record<string, Rule> = { cursor: 'text', limit: 'number' };
const aiProviderOf = (body: unknown): string => {
  const record = body as Record<string, unknown>;
  const consent = record.consent as Record<string, unknown> | undefined;
  return String(record.provider ?? consent?.provider);
};
export const endpoints: Record<string, Endpoint> = {
  me: definition('GET', '/api/auth/me'),
  repositories: definition('GET', '/repositories', {}, { limit: 'number' }),
  mine: definition('GET', '/repositories/mine'),
  removeMine: definition('DELETE', '/repositories/mine/{repositoryId}', { repositoryId: 'id' }),
  repository: definition('GET', '/repositories/{repositoryId}', { repositoryId: 'id' }),
  repositoryIdentity: definition('GET', '/api/desktop/v1/repositories/{repositoryId}/identity', { repositoryId: 'id' }),
  registerRepository: definition('POST', '/repositories', {}, { github_url: 'text' }),
  reanalyze: definition('POST', '/repositories/{repositoryId}/reanalyze', { repositoryId: 'id' }),
  repositoryStatus: definition('GET', '/repositories/{repositoryId}/status', { repositoryId: 'id' }),
  search: definition('GET', '/search/github-repositories', {}, { q: 'text', sort: 'text', per_page: 'number', page: 'number' }),
  myGithubRepositories: definition('GET', '/search/my-github-repositories', {}, { per_page: 'number', page: 'number' }),
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
  providers: ai('GET', 'providers'), usage: ai('GET', 'usage'),
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
  insightModels: ai('GET', 'insights/models'),
  insightReports: ai('GET', 'insights/reports', {}, { repository_id: 'id' }),
  createInsightReport: ai('POST', 'insights/reports', {}, {}, 'CreateInsightReportRequest'),
  insightReport: ai('GET', 'insights/reports/{reportId}', { reportId: 'uuid' }),
  insightConversations: ai('GET', 'insights/conversations', {}, { repository_id: 'id' }),
  createInsightConversation: ai('POST', 'insights/conversations', {}, {}, 'CreateInsightConversationRequest'),
  insightConversation: ai('GET', 'insights/conversations/{conversationId}', { conversationId: 'uuid' }),
  askInsight: ai('POST', 'insights/conversations/{conversationId}/messages', { conversationId: 'uuid' }, {}, 'AskInsightQuestionRequest'),
  deleteInsightConversation: ai('DELETE', 'insights/conversations/{conversationId}', { conversationId: 'uuid' }),
  feedback: ai('PUT', 'findings/{findingId}/feedback', { findingId: 'uuid' }, {}, 'FeedbackRequest'),
};
endpoints.pullFiles = ai('GET', 'pulls/{pr}/files', { pr: 'id' }, { repository_id: 'id', ...page });
// AI analysis and AI review are app-only BYOK (AD-32 ⑧, AD-33): main attaches the saved key of the request's provider
// (body.provider, or body.consent.provider for review jobs); the renderer never sees it.
endpoints.createInsightReport.aiKey = true;
endpoints.askInsight.aiKey = true;
endpoints.createJob.aiKey = true;
endpoints.retryJob.aiKey = true;
// Project community (AD-25). Members only; the server enforces roles, the allowlist only shapes requests.
const community = (method: string, suffix: string, path: Record<string, Rule> = {}, query: Record<string, Rule> = {}, body?: string) =>
  definition(method, `/api/community/v1/${suffix}`, path, query, body);
const repo: Record<string, Rule> = { repositoryId: 'id' };
Object.assign(endpoints, {
  communities: community('GET', 'communities'),
  community: community('GET', 'repositories/{repositoryId}', repo),
  communityMembers: community('GET', 'repositories/{repositoryId}/members', repo),
  updateCommunityMember: community('PATCH', 'repositories/{repositoryId}/members/{userId}', { ...repo, userId: 'uuid' }, {}, 'UpdateMemberRequest'),
  transferCommunityAdmin: community('POST', 'repositories/{repositoryId}/admin', repo, {}, 'TransferAdminRequest'),
  communityInvites: community('GET', 'repositories/{repositoryId}/invites', repo),
  createCommunityInvite: community('POST', 'repositories/{repositoryId}/invites', repo, {}, 'CreateInviteRequest'),
  revokeCommunityInvite: community('DELETE', 'repositories/{repositoryId}/invites/{inviteId}', { ...repo, inviteId: 'uuid' }),
  redeemCommunityInvite: community('POST', 'invites/redeem', {}, {}, 'RedeemInviteRequest'),
  communityPosts: community('GET', 'repositories/{repositoryId}/posts', repo, { before: 'id', limit: 'number' }),
  createCommunityPost: community('POST', 'repositories/{repositoryId}/posts', repo, {}, 'CreatePostRequest'),
  communityPost: community('GET', 'repositories/{repositoryId}/posts/{postId}', { ...repo, postId: 'id' }),
  updateCommunityPost: community('PATCH', 'repositories/{repositoryId}/posts/{postId}', { ...repo, postId: 'id' }, {}, 'UpdatePostRequest'),
  deleteCommunityPost: community('DELETE', 'repositories/{repositoryId}/posts/{postId}', { ...repo, postId: 'id' }),
  createCommunityComment: community('POST', 'repositories/{repositoryId}/posts/{postId}/comments', { ...repo, postId: 'id' }, {}, 'CreateCommentRequest'),
  updateCommunityComment: community('PATCH', 'repositories/{repositoryId}/comments/{commentId}', { ...repo, commentId: 'id' }, {}, 'UpdateCommentRequest'),
  deleteCommunityComment: community('DELETE', 'repositories/{repositoryId}/comments/{commentId}', { ...repo, commentId: 'id' }),
  // Group chat (AD-27): history and sending here; live events come through community-live.ts.
  communityChatMessages: community('GET', 'repositories/{repositoryId}/chat/messages', repo, { before: 'id', limit: 'number' }),
  sendCommunityChat: community('POST', 'repositories/{repositoryId}/chat/messages', repo, {}, 'CreateChatMessageRequest'),
  deleteCommunityChat: community('DELETE', 'repositories/{repositoryId}/chat/messages/{messageId}', { ...repo, messageId: 'id' }),
  // Voice chat (AD-28): seats and WebRTC signaling only; audio goes peer to peer, never through the server.
  communityVoice: community('GET', 'repositories/{repositoryId}/voice', repo),
  joinCommunityVoice: community('POST', 'repositories/{repositoryId}/voice/join', repo),
  heartbeatCommunityVoice: community('POST', 'repositories/{repositoryId}/voice/heartbeat', repo, {}, 'VoiceSessionRequest'),
  leaveCommunityVoice: community('POST', 'repositories/{repositoryId}/voice/leave', repo, {}, 'VoiceSessionRequest'),
  signalCommunityVoice: community('POST', 'repositories/{repositoryId}/voice/signal', repo, {}, 'VoiceSignalRequest'),
  // Co-editing (AD-29): sessions and Yjs relay only; the shared file stays on the host's PC.
  collabSessions: community('GET', 'repositories/{repositoryId}/collab', repo),
  createCollab: community('POST', 'repositories/{repositoryId}/collab', repo, {}, 'CreateCollabSessionRequest'),
  joinCollab: community('POST', 'repositories/{repositoryId}/collab/{sessionId}/join', { ...repo, sessionId: 'token' }),
  heartbeatCollab: community('POST', 'repositories/{repositoryId}/collab/{sessionId}/heartbeat', { ...repo, sessionId: 'token' }, {}, 'CollabSeatRequest'),
  leaveCollab: community('POST', 'repositories/{repositoryId}/collab/{sessionId}/leave', { ...repo, sessionId: 'token' }, {}, 'CollabSeatRequest'),
  relayCollab: community('POST', 'repositories/{repositoryId}/collab/{sessionId}/relay', { ...repo, sessionId: 'token' }, {}, 'CollabRelayRequest'),
});

export const UUID_PATTERN = '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';
const property = (rule: Rule) => {
  if (rule === 'id') return { type: 'integer', minimum: 1, maximum: Number.MAX_SAFE_INTEGER };
  if (rule === 'number') return { type: 'number', minimum: 0, maximum: 1000000 };
  if (rule === 'boolean') return { type: 'boolean' };
  const patterns = { uuid: UUID_PATTERN, sha: '^(?:[0-9a-f]{40}|[0-9a-f]{64})$', provider: '^[a-z][a-z0-9_-]{1,31}$', token: '^[A-Za-z0-9_-]{22}$', text: '^[^\\u0000-\\u001f\\u007f]*$' };
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
  route(value: unknown): { method: string; path: string; body?: string; idempotencyKey?: string; aiKeyProvider?: string } {
    this.validate(value);
    const endpoint = endpoints[value.endpointKey];
    const path = endpoint.template.replace(/\{(\w+)\}/g, (_match, key: string) => encodeURIComponent(String(value.path[key])));
    const query = new URLSearchParams(Object.entries(value.query).map(([k, v]) => [k, String(v)]));
    return { method: endpoint.method, path: path + (query.size ? `?${query}` : ''),
      ...(endpoint.body ? { body: JSON.stringify(value.body) } : {}), ...(value.idempotencyKey ? { idempotencyKey: value.idempotencyKey } : {}),
      ...(endpoint.aiKey ? { aiKeyProvider: aiProviderOf(value.body) } : {}) };
  }
}
