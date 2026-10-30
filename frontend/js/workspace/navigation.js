/** Inbox click = navigation only. The agent re-checks project FK on every target read; nothing is executed or approved here. */
import { readWorkspaceEntry } from '/web/js/platform/workspace-navigation.js';
const api = window.workspace;
const picker = document.getElementById('project-picker');
const entry = readWorkspaceEntry(location.href);
if (entry) {
  const notice = document.createElement('p');
  notice.textContent = entry.repositoryId === null ? '로컬 통합 알림으로 이동했습니다.' :
    `원격 저장소 #${entry.repositoryId}${entry.snapshotId === null ? '' : ` · 저장 결과 #${entry.snapshotId}`}에서 이동했습니다. 로컬 프로젝트 연결은 별도로 확인해야 합니다.`;
  document.getElementById('inbox-detail')?.append(notice);
  document.getElementById('inbox-title')?.scrollIntoView({ block: 'nearest' });
}

function node(tag, text) { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; return element; }
function code(error) { return String(error?.message ?? error).replace(/^.*Error invoking remote method.*Error: /, ''); }
const SECTIONS = { review: 'review-result', validation: 'validation-view', proposal: 'proposal-view' };
const REVIEW_STATUS = { succeeded: '완료', failed: '실패', cancelled: '취소', uncertain: '결과 불명(자동 재전송 없음)', running: '실행 중', queued: '대기', cancel_requested: '취소 요청' };

function summary(kind, value) {
  if (kind === 'review') {
    const rows = [`상태: ${REVIEW_STATUS[value.status] ?? value.status}`, `종류: ${value.kind === 'refactor' ? '리팩토링 제안' : '리뷰'}`,
      `AI 추론 지적: ${value.findings.length}건 (실행으로 검증한 결함이 아닙니다)`];
    if (value.errorCode) rows.push(`오류 코드: ${value.errorCode}`);
    rows.push('전체 근거와 판단 기록은 "로컬 리뷰 이력"에서 확인하세요.');
    return rows;
  }
  const rows = [`상태: ${value.status}`, `종료 코드: ${value.exitCode ?? '없음'}`, `진단 위치: ${value.diagnostics?.length ?? 0}건`];
  if (value.truncated) rows.push('출력이 상한에서 잘렸습니다.');
  rows.push('전체 출력은 검증 섹션에서 다시 조회할 수 있습니다. 재실행은 별도 승인이 필요합니다.');
  return rows;
}

/** Returns a user-facing message; renders a text-only summary into `detail`. */
export async function openTarget(item, detail) {
  detail.replaceChildren();
  const target = item.target;
  if (item.targetState !== 'available') return '대상 기록을 찾을 수 없습니다. 삭제·만료되었거나 접근할 수 없는 대상입니다.';
  if (![...picker.options].some(option => option.value === target.projectId)) return '이 알림의 프로젝트가 현재 목록에 없습니다. 프로젝트 목록을 새로고침하거나 다시 연결하세요.';
  if (picker.value !== target.projectId) {
    picker.value = target.projectId;
    picker.dispatchEvent(new Event('change'));  // Project modules reset synchronously before the target is shown.
  }
  if (target.kind === 'analysis') {
    try { await api.openRemoteAnalysis({ projectId: target.projectId, analysisId: target.resourceId }); return '권한을 확인하고 서버 분석으로 이동했습니다.'; }
    catch (error) { return code(error); }
  }
  let value;
  try {
    if (target.kind === 'review') value = await api.getReview({ projectId: target.projectId, runId: target.resourceId });
    else if (target.kind === 'validation') value = await api.getValidation({ projectId: target.projectId, runId: target.resourceId, offset: 0, limit: 10 });
    else value = await api.getProposal({ projectId: target.projectId, proposalId: target.resourceId });
  } catch (error) {
    const reason = code(error);
    return ['RESOURCE_NOT_FOUND', 'PROJECT_UNAVAILABLE', 'PROJECT_MOVED', 'PROJECT_RECONNECT_REQUIRED', 'INPUT_EXPIRED'].includes(reason)
      ? `대상을 열 수 없습니다(${reason}). 프로젝트 위치·접근 권한 또는 기록 보존 상태를 확인하세요.` : reason;
  }
  if (picker.value !== target.projectId || value.projectId !== target.projectId) return '프로젝트 선택이 바뀌어 이동을 중단했습니다.';
  if (target.kind === 'proposal') window.dispatchEvent(new CustomEvent('workspace-proposal-selected', { detail: value }));
  else for (const row of summary(target.kind, value)) detail.append(node('p', row));
  window.dispatchEvent(new CustomEvent('workspace-open-target', { detail: { kind: target.kind } }));  // layout.js shows the matching project tab.
  document.getElementById(SECTIONS[target.kind])?.scrollIntoView({ block: 'nearest' });
  return '대상으로 이동했습니다.';
}
