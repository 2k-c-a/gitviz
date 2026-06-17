/* ── 공용: 분석 결과 페이지(network/risk/coupling/busfactor) 헤더를 실제 repo로 채움 ──
   .js-repo-title 요소가 있는 페이지에서만 동작한다(타임라인은 자체 timeline.js가 처리). */
import { getRepoId, fetchSummary } from './api.js';

(async function fillRepoHeader() {
  const titles = document.querySelectorAll('.js-repo-title');
  if (!titles.length) return;

  try {
    const s = await fetchSummary(getRepoId());
    if (s.error) return;
    const repo = s.repository || {};
    // url에서 owner/repo 추출 (예: facebook/react). 실패 시 name fallback.
    const m = (repo.url || '').match(/github\.com\/([^/]+\/[^/]+?)(?:\.git)?\/?$/);
    const full = (m && m[1]) || repo.name || '—';
    titles.forEach(t => { t.textContent = full; });
  } catch (e) {
    console.error('[repoHeader] 로드 실패:', e);
  }
})();
