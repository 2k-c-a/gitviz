/* ── 결과 Analytics API 클라이언트 ──
   프론트는 FastAPI가 같은 오리진(/)에서 서빙하므로 상대경로로 호출한다. */

const REPO_KEY = 'gitviz_repo_id';

/* 현재 보고 있는 repo_id를 기억 (페이지 간 이동 시 유지용) */
export function setRepoId(id) {
  if (Number.isInteger(Number(id))) localStorage.setItem(REPO_KEY, String(id));
}

/* 분석 대상 repo_id 결정: ?repo=<id> → localStorage → 기본 1(react).
   쿼리로 들어온 값은 localStorage에 동기화해 서브탭/내비바 이동 시에도 유지된다. */
export function getRepoId() {
  const fromQuery = parseInt(new URLSearchParams(location.search).get('repo'), 10);
  if (Number.isInteger(fromQuery)) {
    setRepoId(fromQuery);
    return fromQuery;
  }
  const stored = parseInt(localStorage.getItem(REPO_KEY), 10);
  if (Number.isInteger(stored)) return stored;
  return 1;
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} → ${res.status}`);
  return res.json();
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
export const fetchCoupling = (id, limit = 100, minScore = 0) =>
  getJSON(`${base(id)}/coupling?limit=${limit}&min_score=${minScore}`);
export const fetchFileCoupling = (id, fileId, limit = 20) =>
  getJSON(`${base(id)}/coupling/${fileId}?limit=${limit}`);
export const fetchNetwork = (id, limitNodes = 50, minEdgeWeight = 0) =>
  getJSON(`${base(id)}/network?limit_nodes=${limitNodes}&min_edge_weight=${minEdgeWeight}`);
export const fetchNetworkTopPairs = (id, limit = 5) =>
  getJSON(`${base(id)}/network/top-pairs?limit=${limit}`);
export const fetchRepoTree = (id) => getJSON(`${base(id)}/tree`);
