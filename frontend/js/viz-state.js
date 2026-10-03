/* ── 화면 간 이동 상태(repo / snapshot / sort_by)를 URL에서 읽고 다음 URL로 옮기는 규칙 ──
   DOM·fetch에 의존하지 않는 순수 함수만 둔다(node 테스트 가능).

   URL이 유일한 기준이다. 저장된 값(sessionStorage 등)으로 snapshot을 되살리지 않는다 —
   A 결과를 보다가 B를 열고 뒤로 가면, 마지막으로 "불러온" 결과(B)가 아니라 지금 주소의
   결과(A)를 계속 봐야 하기 때문이다. */

/* 변경 결합 정렬 정책: coupling = 원 결합도·공동 변경 횟수(기본), idf = IDF 보정(실험) */
export const SORT_POLICIES = ['coupling', 'idf'];
export const DEFAULT_SORT = 'coupling';

const toId = (v) => {
  const s = String(v ?? '').trim();
  const n = /^\d+$/.test(s) ? parseInt(s, 10) : NaN;
  return Number.isSafeInteger(n) && n > 0 ? n : null;
};

export function parseState(search) {
  const p = new URLSearchParams(search || '');
  const repo = toId(p.get('repo'));
  const sort = p.get('sort_by');
  return {
    repo,
    /* snapshot_id는 저장소별 ID다. repo 없이 단독으로 온 값은 어느 저장소의 것인지 알 수 없어 무시한다. */
    snapshot: repo != null ? toId(p.get('snapshot')) : null,
    sortBy: SORT_POLICIES.includes(sort) ? sort : null,
  };
}

export const effectiveSort = (state) => state.sortBy ?? DEFAULT_SORT;

export function buildQuery({ repo = null, snapshot = null, sortBy = null } = {}) {
  const p = new URLSearchParams();
  if (repo != null) {
    p.set('repo', String(repo));
    if (snapshot != null) p.set('snapshot', String(snapshot));
    if (sortBy && sortBy !== DEFAULT_SORT) p.set('sort_by', sortBy);
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}

/* 다음 화면으로 갈 때 옮길 상태.
   - targetRepo 생략/빈 값: 같은 저장소 안의 탭 이동 → 현재 URL의 repo/snapshot/sort_by 유지
   - targetRepo가 현재 저장소와 같음: 마찬가지로 유지
   - targetRepo가 다른 저장소: 이전 snapshot·정렬 정책을 가져가지 않는다(저장소 간 혼입 방지)
   - snapshotId 명시: 그 저장소의 해당 결과로 이동(방금 끝난 분석 등) */
export function nextState(currentSearch, targetRepo, snapshotId) {
  const cur = parseState(currentSearch);
  const repo = toId(targetRepo) ?? cur.repo;
  if (repo == null) return { repo: null, snapshot: null, sortBy: null };
  const sameRepo = cur.repo === repo;
  return {
    repo,
    snapshot: toId(snapshotId) ?? (sameRepo ? cur.snapshot : null),
    sortBy: sameRepo ? cur.sortBy : null,
  };
}

export const nextQuery = (currentSearch, targetRepo, snapshotId) =>
  buildQuery(nextState(currentSearch, targetRepo, snapshotId));
