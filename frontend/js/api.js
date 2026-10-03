/* ── 결과 Analytics API 클라이언트 ──
   프론트는 FastAPI가 같은 오리진(/)에서 서빙하므로 상대경로로 호출한다.

   어떤 저장 결과(snapshot)를 볼지는 주소(?repo=&snapshot=)가 유일한 기준이다.
   주소에 snapshot이 없으면 그 저장소의 최신 결과를 연다. (sessionStorage에 마지막으로 연 결과를
   기억해 두었다가 되살리는 방식은 쓰지 않는다 — 다른 결과를 열고 뒤로 가면 엉뚱한 결과가 열린다.) */
import { parseState, effectiveSort, DEFAULT_SORT } from './viz-state.js';
import { renderContextBanner } from './page-context.js';

const REPO_KEY = 'gitviz_repo_id';

/* 예전 버전이 남긴 snapshot 기억값 정리 — 더 이상 쓰지 않는다 */
try {
  Object.keys(sessionStorage).filter(k => k.startsWith('gitviz_snapshot_')).forEach(k => sessionStorage.removeItem(k));
} catch { /* 저장소 접근이 막힌 환경 */ }

/* 현재 보고 있는 repo_id를 기억 (홈·검색 등 주소에 repo가 없는 화면에서의 기본값용) */
export function setRepoId(id) {
  if (Number.isInteger(Number(id))) localStorage.setItem(REPO_KEY, String(id));
}

/* 분석 대상 repo_id 결정: ?repo=<id> → localStorage → 기본 1. */
export function getRepoId() {
  const fromQuery = parseState(location.search).repo;
  if (fromQuery != null) {
    setRepoId(fromQuery);
    return fromQuery;
  }
  const stored = parseInt(localStorage.getItem(REPO_KEY), 10);
  if (Number.isInteger(stored)) return stored;
  return 1;
}

/* 주소의 snapshot (repo가 함께 있고 지금 저장소와 같을 때만) */
export function getSnapshotId(repoId = getRepoId()) {
  const st = parseState(location.search);
  return st.repo === repoId ? st.snapshot : null;
}

/* 변경 결합 정렬 정책 — 주소의 sort_by가 기준 (coupling | idf) */
export const getSortPolicy = () => effectiveSort(parseState(location.search));

export function setSortPolicy(policy) {
  const url = new URL(location.href);
  if (policy === DEFAULT_SORT) url.searchParams.delete('sort_by');
  else url.searchParams.set('sort_by', policy);
  history.replaceState(null, '', url);
}

/* API 오류 — status로 화면별 안내를 나눈다 */
export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

async function readJSON(url) {
  let res;
  try {
    res = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
  } catch {
    throw new ApiError('서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.', 0, null);
  }
  let body = null;
  try { body = await res.json(); } catch { /* JSON이 아닌 응답 */ }
  if (!res.ok) {
    const detail = typeof body?.detail === 'string' ? body.detail : null;
    throw new ApiError(detail || `분석 결과 조회 실패 (${res.status})`, res.status, detail);
  }
  return body;
}

/* 저장소별 결과 메타 — 한 번만 조회한다. 주소에 snapshot이 있으면 그 결과를, 없으면 최신 결과를 연다.
   조회가 끝나면 주소에 repo/snapshot을 확정해 넣어, 이후 탭 이동·새로고침·뒤로 가기에서도 같은 결과를 본다. */
const snapshotMetas = new Map();
export function fetchSnapshotMeta(id) {
  if (!snapshotMetas.has(id)) {
    const requested = getSnapshotId(id);
    const query = requested != null ? `?snapshot_id=${requested}` : '';
    snapshotMetas.set(id, readJSON(`/repositories/${id}/analytics/snapshot${query}`).then(data => {
      const url = new URL(location.href);
      url.searchParams.set('repo', String(id));
      url.searchParams.set('snapshot', String(data.snapshot.id));
      history.replaceState(null, '', url);
      renderContextBanner(data);
      return data;
    }));
  }
  return snapshotMetas.get(id);
}

async function getJSON(url) {
  const match = url.match(/^\/repositories\/(\d+)\/analytics/);
  if (!match) return readJSON(url);
  const id = Number(match[1]);
  const meta = await fetchSnapshotMeta(id);
  return readJSON(`${url}${url.includes('?') ? '&' : '?'}snapshot_id=${meta.snapshot.id}`);
}

const base = (id) => `/repositories/${id}/analytics`;

export const fetchSummary         = (id) => getJSON(`${base(id)}/summary`);
export const fetchTimeline        = (id, months = 36) => getJSON(`${base(id)}/timeline?months=${months}`);
export const fetchCommitTypes     = (id) => getJSON(`${base(id)}/commit-types`);
export const fetchReleases        = (id, limit = 5) => getJSON(`${base(id)}/releases?limit=${limit}`);
export const fetchTopContributors = (id, limit = 5) => getJSON(`${base(id)}/top-contributors?limit=${limit}`);
export const fetchContributorHeatmap = (id, weeks = 16, limit = 8) => getJSON(`${base(id)}/contributor-heatmap?weeks=${weeks}&limit=${limit}`);
export const fetchRiskScores = (id, limit = 50) => getJSON(`${base(id)}/risk-scores?limit=${limit}`);
export const fetchContributorOwnedFiles = (id, contributorId, limit = 20) =>
  getJSON(`${base(id)}/contributors/${contributorId}/owned-files?limit=${limit}`);
export const fetchContributorCollaborators = (id, contributorId, limit = 20) =>
  getJSON(`${base(id)}/contributors/${contributorId}/collaborators?limit=${limit}`);
export const fetchFileDetail = (id, fileId, limit = 20) =>
  getJSON(`${base(id)}/files/${fileId}/detail?limit=${limit}`);
export const fetchBusFactor = (id, limit = 50) =>
  getJSON(`${base(id)}/bus-factor?limit=${limit}`);
/* sortBy: 'coupling'(원 결합도·공동 변경 횟수, 기본) | 'idf'(IDF 보정, 실험) — 서버에 그대로 전달한다 */
export const fetchCoupling = (id, limit = 100, minScore = 0, sortBy = DEFAULT_SORT) =>
  getJSON(`${base(id)}/coupling?limit=${limit}&min_score=${minScore}&sort_by=${encodeURIComponent(sortBy)}`);
export const fetchFileCoupling = (id, fileId, limit = 20) =>
  getJSON(`${base(id)}/coupling/${fileId}?limit=${limit}`);
export const fetchNetwork = (id, limitNodes = 50, minEdgeWeight = 0) =>
  getJSON(`${base(id)}/network?limit_nodes=${limitNodes}&min_edge_weight=${minEdgeWeight}`);
export const fetchNetworkTopPairs = (id, limit = 5) =>
  getJSON(`${base(id)}/network/top-pairs?limit=${limit}`);
export const fetchRepoTree = (id) => getJSON(`${base(id)}/tree`);

/* 파일별 원시 지표(변경 횟수·Churn·버그 수정 횟수·백분위 구성 요소) — 한 결과당 한 번만 받아 file_id로 찾는다 */
const riskIndexes = new Map();
export function fetchRiskIndex(id) {
  if (!riskIndexes.has(id)) {
    riskIndexes.set(id, fetchRiskScores(id, 10000).then(r => new Map((r.files || []).map(f => [f.file_id, f]))));
  }
  return riskIndexes.get(id);
}

/* ── 분석 이력·비교 (결과 단위가 아니라 저장소 단위 API) ── */
export const fetchSnapshots = (id, { limit = 20, beforeId = null } = {}) =>
  readJSON(`/repositories/${id}/snapshots?limit=${limit}${beforeId != null ? `&before_id=${beforeId}` : ''}`);
export const fetchSnapshotCompare = (id, before, after, { offset = 0, limit = 100 } = {}) =>
  readJSON(`/repositories/${id}/snapshots/compare?before=${before}&after=${after}&offset=${offset}&limit=${limit}`);
