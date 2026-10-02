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

const snapshots = new Map();
export function resetSnapshot(id) {
  sessionStorage.removeItem(`gitviz_snapshot_${id}`);
  snapshots.delete(Number(id));
}
async function readJSON(url) {
  const res = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
  const body = await res.json();
  if (!res.ok) throw new Error(body.detail || `분석 결과 조회 실패 (${res.status})`);
  return body;
}
async function getJSON(url) {
  const match = url.match(/^\/repositories\/(\d+)\/analytics/);
  if (!match) return readJSON(url);
  const id = Number(match[1]);
  if (!snapshots.has(id)) {
    const requested = new URLSearchParams(location.search).get('snapshot') || sessionStorage.getItem(`gitviz_snapshot_${id}`);
    snapshots.set(id, readJSON(`/repositories/${id}/analytics/snapshot${requested ? `?snapshot_id=${encodeURIComponent(requested)}` : ''}`)
      .then(data => {
        sessionStorage.setItem(`gitviz_snapshot_${id}`, String(data.snapshot.id));
        const current = new URL(location.href);
        current.searchParams.set('repo', String(id));
        current.searchParams.set('snapshot', String(data.snapshot.id));
        history.replaceState(null, '', current);
        showAnalysisContext(data);
        return data.snapshot.id;
      }));
  }
  const sid = await snapshots.get(id);
  return readJSON(`${url}${url.includes('?') ? '&' : '?'}snapshot_id=${sid}`);
}
function showAnalysisContext(data) {
  const container = document.querySelector('main') || document.querySelector('.page.active');
  if (!container) return;
  let el = document.getElementById('analysis-context');
  if (!el) {
    el = document.createElement('div'); el.id = 'analysis-context';
    el.style.cssText = 'padding:12px 16px;margin:12px 0;border:1px solid var(--border);border-radius:8px;font-size:12px;line-height:1.7;color:var(--text2)';
    container.prepend(el);
  }
  const s = data.snapshot || data._snapshot, q = data.data_quality || {};
  el.textContent = `저장 결과 #${s.id} · SHA ${s.target_sha.slice(0, 12)} · 분석 구간 ${s.window_start || '전체 이력'} ~ ${s.window_end} (종료 제외) · ${s.formula_version}\n관측 커밋 ${q.commit_count ?? 0}개 · 작성자 미식별 ${q.unidentified_commits ?? 0}개 · GitHub 계정 미연결 ${q.github_account_unlinked ?? 0}명 · 병합 PR의 커밋 연결 ${q.prs_with_linked_commits ?? 0}/${q.merged_pr_count ?? 0} · 기준 SHA 파일 수집 ${q.collected_target_file_count ?? 0}/${q.target_tree_file_count ?? 0}. ${q.coverage_scope || ''}. 5건 미만 표시는 데이터 부족 안내이며 통계적 신뢰수준을 뜻하지 않습니다.`;
  el.style.whiteSpace = 'pre-wrap';
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
