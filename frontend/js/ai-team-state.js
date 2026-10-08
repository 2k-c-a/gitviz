/* ── 팀 화면의 순수 로직 (DOM·네트워크 없음, node에서 시험한다) ──
   - 서버가 판단한다: 여기의 권한 표시는 버튼을 보이거나 숨기는 안내일 뿐이고, 허용 여부는 서버 응답 코드로 안내한다.
   - 개인 자료는 자동으로 공유되지 않는다. 판정(verdict)은 공유한 사람의 판단이며 GitViz가 확인한 것이 아니다. */

export const ROLE_LABEL = { admin: '관리자', member: '멤버', viewer: '조회자' };
export const ROLE_ORDER = ['admin', 'member', 'viewer'];
export const SHARE_TYPE_LABEL = { review_result: '코드 리뷰 결과', handover_target: '인계 준비 상태' };
export const SHARE_STATUS_LABEL = { active: '공유 중', outdated: '공유한 뒤 바뀜(다시 공유 필요)', expired: '보관 기간 만료', source_deleted: '원본 삭제됨' };
export const VERDICT_LABEL = { valid: '유효(공유한 사람의 판정)', unverified: '검토 대기', uncertain: '불확실', false_positive: '오탐(문제 집계 제외)' };
export const SHARE_NOTE = '공유하면 팀 구성원이 그 자료를 볼 수 있습니다. 코드 원문·인용과 내 메모는 공유하지 않으며, 공유하지 않은 자료는 보이지 않습니다. 언제든 공유를 해제할 수 있습니다.';
export const REPORT_NOTE = '보고서는 팀에 공유된 자료만 정리합니다. 문제 집계는 사람이 유효로 판정한 지적만 세고, 오탐은 집계에서 제외하며, 같은 지적 후보는 합치지 않습니다. 모델을 호출하지 않습니다.';
export const PERMISSION_NOTE = {
  admin: '구성원·저장소·공유를 관리하고 보고서를 만들 수 있습니다.',
  member: '내 자료를 공유하고 보고서를 만들 수 있습니다.',
  viewer: '공유된 자료와 보고서를 볼 수 있습니다.',
};

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isUuid = (v) => typeof v === 'string' && UUID_RE.test(v);

export function parseRoute(search) {
  const q = new URLSearchParams(search || '');
  const invalid = [];
  const pick = (key) => {
    const raw = q.get(key);
    if (raw && !isUuid(raw)) { invalid.push(key); return null; }
    return raw ? raw.toLowerCase() : null;
  };
  const team = pick('team');
  const report = team ? pick('report') : null;
  return { team, report, invalid };
}

export function buildRoute({ team = null, report = null } = {}) {
  const p = new URLSearchParams();
  if (team) { p.set('team', team); if (report) p.set('report', report); }
  const s = p.toString();
  return s ? `?${s}` : '';
}

/* 역할별 화면 표시(안내용). 서버가 같은 규칙으로 판단한다. */
const RANK = { viewer: 1, member: 2, admin: 3 };
export const can = (role, action) => (RANK[role] || 0) >= ({ view: 1, share: 2, generate_report: 2, manage: 3 }[action] || 99);

export const BLOCK_TEXT = {
  visibility_changed: '구성원·역할·저장소 연결·공유 범위가 바뀐 뒤라 이 보고서는 차단됐습니다. 새 범위로 다시 생성해야 합니다.',
  share_removed: '이 보고서의 원본 공유가 해제됐습니다.',
  share_outdated: '공유한 뒤 원본이 바뀌었거나 보관 기간이 지났습니다.',
  repository_unlinked: '보고서에 포함된 저장소가 팀에서 연결 해제됐습니다.',
  repository_access_lost: '보고서에 포함된 저장소 중 접근할 수 없는 곳이 있어 볼 수 없습니다. 접근할 수 있는 저장소만으로 다시 생성해야 합니다.',
  access_check_unavailable: '저장소 접근을 확인할 수 없어 열 수 없습니다. 잠시 후 다시 시도해 주세요.',
};
export const STALE_TEXT = { feedback_changed: '공유한 사람이 AI 지적의 판정을 바꿨습니다. 이 보고서는 이전 판정 기준입니다.' };

export const blockLines = (reasons) => (reasons || []).map((r) => BLOCK_TEXT[r] || `볼 수 없습니다(${r}).`);
export const staleLines = (reasons) => (reasons || []).map((r) => STALE_TEXT[r] || `기준이 바뀌었습니다(${r}).`);

/* 팀 API 오류 → 안내. 해당하지 않으면 null(호출하는 쪽의 공통 안내를 쓴다). */
export function describeTeamError(err) {
  const code = err?.code || null;
  const reason = err?.details?.reason || null;
  const out = (title, body, tone = 'error', extra = {}) => ({ title, body, tone, specific: true, ...extra });
  if (code === 'PERMISSION_DENIED') return out('이 역할로는 할 수 없습니다', `${ROLE_LABEL[err.details?.required_role] || '더 높은 역할'} 이상만 할 수 있습니다. 현재 역할: ${ROLE_LABEL[err.details?.role] || '—'}.`);
  if (code === 'REPORT_BLOCKED') return out('이 보고서는 지금 볼 수 없습니다', blockLines(err.details?.reasons).join(' ') || '권한 범위가 바뀌어 차단됐습니다.', 'warn', { blocked: true, reasons: err.details?.reasons || [] });
  if (code === 'VERSION_CONFLICT') {
    if (reason === 'LAST_ADMIN') return out('관리자가 한 명 이상 있어야 합니다', '다른 사람을 관리자로 지정한 뒤 진행해 주세요.', 'warn');
    if (reason === 'VISIBILITY_CHANGED') return out('생성하는 동안 팀의 범위가 바뀌었습니다', '구성원·저장소·공유가 바뀌었습니다. 최신 상태를 확인한 뒤 다시 생성해 주세요.', 'warn');
    if (reason === 'ALREADY_MEMBER') return out('이미 팀 구성원입니다', '', 'warn');
  }
  if (code === 'INVALID_STATE' && /^share_/.test(reason || '')) {
    return out('공유 내용을 볼 수 없습니다', '공유한 뒤 원본이 바뀌었거나 보관 기간이 지났습니다. 작성자가 다시 공유해야 합니다.', 'warn');
  }
  if (code === 'RESOURCE_NOT_FOUND') return out('찾을 수 없습니다', '팀 구성원이 아니거나, 삭제됐거나, 저장소 접근 권한이 없습니다.', 'error', { notFound: true });
  return null;
}

/* 공유할 수 있는 자료 목록(내 성공한 코드 리뷰 결과, 내 인계 대상). 이미 공유한 것은 표시만 한다. */
export function shareCandidates({ jobs, targets, shares, repositoryId }) {
  const shared = new Set((shares || []).map((s) => s.resource_id));
  const results = (jobs || []).filter((j) => j && j.kind === 'review' && j.status === 'succeeded' && isUuid(j.result_id) && j.repository_id === repositoryId)
    .map((j) => ({ type: 'review_result', id: j.result_id, label: `코드 리뷰 · ${j.finished_at || j.created_at || ''}`.trim(), shared: shared.has(j.result_id) }));
  const handovers = (targets || []).filter((t) => t && isUuid(t.target_id) && t.repository_id === repositoryId)
    .map((t) => ({ type: 'handover_target', id: t.target_id, label: `인계 대상 · ${t.name}`, shared: shared.has(t.target_id) }));
  return [...results, ...handovers];
}

export function problemSummary(totals) {
  const t = totals || {};
  return `문제 집계(유효로 판정): ${t.valid || 0} · 검토 대기: ${t.unverified || 0} · 불확실: ${t.uncertain || 0} · 오탐(집계 제외): ${t.false_positive || 0}`;
}
