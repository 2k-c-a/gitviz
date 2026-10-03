/* ── 계산 버전(formula_version)별 지표 정책 · 근거 상태 · 변경 결합 메타 해석 (순수 함수) ──
   화면 문구가 "지금 열려 있는 스냅샷의 실제 정책"을 가리키게 하는 단일 출처다.
   구버전 스냅샷을 열면 그 버전의 설명을, 신버전이면 새 정책의 설명을 보여준다. */

export const FORMULA_V3 = 'gitviz-2026.10-priority-v3';   // 유지보수 점검 우선순위 (백분위 평균)
export const FORMULA_V2 = 'gitviz-2026.10-snapshot-v2';   // 이전: Risk Score = 0.5·CurrentRisk + 0.5·StructuralRisk

/* 표시 임계값 — 상위 퍼센트가 아니라 점수 값 자체에 대한 구분선이다 */
export const DISPLAY_THRESHOLD = 0.7;
export const STRONG_COUPLING = 0.7;

const PRIORITY = {
  kind: 'priority',
  scoreName: '유지보수 점검 우선순위',
  scoreShort: '점검 우선순위',
  sortLabel: '점검 우선순위순',
  versionLabel: '점검 우선순위 (v3)',
  leadText: '점검 우선순위는 변경 횟수·Churn·버그 수정 횟수의 백분위 평균으로 저장소 안에서 상대적으로 계산한 점수입니다. 결함 확률이 아니며 0도 안전을 뜻하지 않습니다.',
  summaryText:
    '점검 우선순위는 관측 구간 안에서 변경 횟수·Churn(추가+삭제 줄 수)·버그 수정 커밋 수를 파일 간 백분위(0~1)로 바꿔 평균한 상대 점수입니다. 결함 확률이 아니며, 0도 안전을 뜻하지 않습니다. 다른 저장소와 직접 비교할 수 없습니다.',
  thresholdText:
    '색·배지의 0.7은 표시 임계값입니다. 상위 30%를 뜻하지 않으며, 구성 요소 백분위의 평균값 자체가 상위 퍼센트도 아닙니다.',
  hasComponents: true,
  windowFixed: true,
};

const LEGACY = {
  kind: 'legacy',
  scoreName: 'Risk Score (이전 계산 버전)',
  scoreShort: 'Risk Score',
  sortLabel: 'Risk Score순',
  versionLabel: '이전 버전 (v2)',
  leadText: '이전 계산 버전(v2)의 Risk Score입니다. 새 정책(점검 우선순위)과 직접 비교할 수 없으며 결함 확률이 아닙니다.',
  summaryText:
    '이 결과는 이전 계산 버전(snapshot-v2)입니다. Risk Score = 0.5×CurrentRisk + 0.5×StructuralRisk이며, CurrentRisk는 파일의 마지막 refactor 분류 커밋 이후(없으면 첫 커밋 이후) 구간의 변경 빈도·Churn·Entropy·버그 비율·공동 변경으로, StructuralRisk는 전체 기간의 refactor·버그 수정·Churn 비율로 계산했습니다. BF·Entropy의 관측 구간도 마지막 refactor 커밋 이후입니다. 새 버전(점검 우선순위)과 직접 비교할 수 없으며 결함 확률이 아닙니다.',
  thresholdText: '색·배지의 0.7은 표시 임계값이며 상위 30%를 뜻하지 않습니다.',
  hasComponents: false,
  windowFixed: false,
};

const UNKNOWN = {
  kind: 'unknown',
  scoreName: '분석 점수',
  scoreShort: '분석 점수',
  sortLabel: '점수순',
  versionLabel: '알 수 없는 버전',
  leadText: '이 화면이 알지 못하는 계산 버전입니다. 점수는 저장소 안의 상대 값이며 결함 확률이 아닙니다.',
  summaryText:
    '이 화면이 알지 못하는 계산 버전입니다. 점수는 저장소 안의 상대 값이며 결함 확률이 아닙니다. 정확한 산식은 저장된 계산 버전의 문서를 확인하세요.',
  thresholdText: '색·배지의 0.7은 표시 임계값이며 상위 30%를 뜻하지 않습니다.',
  hasComponents: false,
  windowFixed: false,
};

export function policyOf(formulaVersion) {
  if (formulaVersion === FORMULA_V3) return PRIORITY;
  if (formulaVersion === FORMULA_V2) return LEGACY;
  return UNKNOWN;
}

/* 점수 필드: 새 버전은 maintenance_priority가 기준이고 risk_score는 구버전 호환 별칭이다.
   키가 있으면(값이 null이어도) maintenance_priority만 본다 — null을 다른 값으로 대체하지 않는다. */
export const scoreOf = (f) => (f && 'maintenance_priority' in f ? f.maintenance_priority : f?.risk_score ?? null);

/* ── 파일 근거 상태 ──
   no_history      : 관측 구간에 변경이 없음 (점수 없음, 0이 아님)
   low_support     : 표본 부족 (점수는 있으나 변경이 적음)
   metrics_missing : 점수 계산 입력(Churn 등) 누락 → 자료 부족. 점수 순위 뒤로 배치
   observed        : 정상 관측 */
export const EVIDENCE = {
  no_history:      { label: '관측 변경 없음', tone: 'gray',  rank: 2 },
  metrics_missing: { label: '자료 부족 (계산 입력 누락)', tone: 'amber', rank: 1 },
  low_support:     { label: '표본 부족', tone: 'amber', rank: 0 },
  observed:        { label: '', tone: 'none', rank: 0 },
};
export const evidenceOf = (status) => EVIDENCE[status] || EVIDENCE.observed;

/* 점수 순위 비교: 점수 있는 파일 → 점수 높은 순, 같으면 변경 횟수 많은 순 → 경로순.
   점수 없는 파일(null)은 뒤로: 계산 입력 누락(자료 부족) → 관측 변경 없음 순. null은 0으로 취급하지 않는다. */
export function comparePriority(a, b) {
  const av = scoreOf(a), bv = scoreOf(b);
  if (av != null && bv != null) {
    if (av !== bv) return bv - av;
    return ((b.observation_count ?? 0) - (a.observation_count ?? 0)) || String(a.path).localeCompare(String(b.path));
  }
  if (av != null) return -1;
  if (bv != null) return 1;
  const ra = evidenceOf(a.evidence_status).rank, rb = evidenceOf(b.evidence_status).rank;
  return (ra - rb) || String(a.path).localeCompare(String(b.path));
}

/* ── 수집 범위 / 분석 근거 상태 ──
   조회 성공 ≠ 수집 완료 ≠ 분석 근거 충분. 세 가지를 따로 판정해 보여준다. */
export function assessDataQuality(dq) {
  dq = dq || {};
  const threshold = dq.low_support_threshold ?? 5;
  const collectionReasons = [];
  if (dq.target_tree_file_count != null && dq.collected_target_file_count != null
      && dq.collected_target_file_count < dq.target_tree_file_count) {
    collectionReasons.push(`기준 SHA 파일 ${dq.collected_target_file_count}/${dq.target_tree_file_count}개만 결과에 포함`);
  }
  if ((dq.merged_pr_count ?? 0) > 0 && (dq.prs_with_files ?? 0) < dq.merged_pr_count) {
    collectionReasons.push(`병합 PR ${dq.prs_with_files ?? 0}/${dq.merged_pr_count}개만 파일 상세 수집`);
  }
  const collection = collectionReasons.length ? 'partial' : 'complete';

  const evidenceReasons = [];
  let evidence;
  if (dq.commit_count === 0) {
    evidence = 'none';
    evidenceReasons.push('관측 구간에 커밋이 없습니다(수집 실패가 아니라 구간 밖의 변경일 수 있음)');
  } else {
    if (dq.commit_count != null && dq.commit_count < threshold) evidenceReasons.push(`관측 커밋 ${dq.commit_count}개 (<${threshold})`);
    if (collection === 'partial') evidenceReasons.push('수집 범위 일부 누락');
    if (dq.commit_count > 0 && dq.unidentified_commits === dq.commit_count) evidenceReasons.push('모든 커밋의 작성자가 미식별');
    evidence = evidenceReasons.length ? 'limited' : 'sufficient';
  }

  const notes = [];
  if ((dq.unidentified_commits ?? 0) > 0 && dq.unidentified_commits !== dq.commit_count) notes.push(`작성자 미식별 커밋 ${dq.unidentified_commits}개`);
  if ((dq.github_account_unlinked ?? 0) > 0) notes.push(`GitHub 계정 미연결 기여자 ${dq.github_account_unlinked}명`);
  if ((dq.merged_pr_count ?? 0) > 0) notes.push(`병합 PR의 커밋 연결 ${dq.prs_with_linked_commits ?? 0}/${dq.merged_pr_count}`);
  return { collection, collectionReasons, evidence, evidenceReasons, notes, threshold };
}

/* ── 변경 결합 메타 해석 ── */
export const FALLBACK_LABELS = {
  no_merged_prs: 'PR 없음',
  pr_details_missing: '상세 부족',
  all_collected_prs_filtered: '전부 필터됨',
};
export const FALLBACK_DETAILS = {
  no_merged_prs: '관측 구간에 병합된 PR이 없어 커밋 단위로 대체했습니다.',
  pr_details_missing: '병합 PR은 있으나 변경 파일 상세가 수집되지 않아 커밋 단위로 대체했습니다.',
  all_collected_prs_filtered: '파일 상세가 있는 PR이 모두 대규모 변경으로 제외되어 커밋 단위로 대체했습니다.',
};

export function couplingUnitText(meta) {
  if (!meta) return '';
  if (meta.co_change_unit === 'pr') return '병합 PR 단위';
  const why = FALLBACK_LABELS[meta.fallback_reason];
  return why ? `커밋 단위 (대체: ${why})` : '커밋 단위';
}
