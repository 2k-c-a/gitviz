import { renderPatch } from './diff.js';
const api = window.workspace;
const picker = document.getElementById('project-picker');
const view = document.getElementById('proposal-view');
const message = document.getElementById('proposal-message');
const recovery = document.getElementById('proposal-recovery-list');
let current = null;
let generation = 0;
let executionKey = null;
let recoveryKey = null;
function node(tag, text) { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; return element; }
function fail(error) {
  const code = String(error?.message || error).replace(/^.*Error invoking remote method.*Error: /, '');
  message.textContent = code;
  if (code === 'RECOVERY_CONFLICT') message.textContent = '현재 파일 또는 임시 보존 파일의 identity/hash가 달라 복구하지 않았습니다. 원본과 journal을 보존했습니다. 표시된 파일을 직접 확인한 뒤 복구 범위를 다시 결정하세요.';
}
function show(value, approvalId = null) {
  current = value; view.replaceChildren(node('h3', `${value.status} · 제안 ${value.proposalId}`), node('p', value.purpose),
    node('p', `고정 입력 ${value.snapshotId}\npatch ${value.patchHash}\npreconditions ${value.preconditionsHash}`));
  for (const [title, values] of [['예측 효과 · 실측 아님', value.expectedEffects], ['위험', value.risks], ['후속 승인 검증 제안 · 아직 실행 안 함', value.validationSuggestions], ['한계', value.limitations]]) {
    view.append(node('h4', title)); for (const text of values) view.append(node('p', text));
  }
  renderPatch(view, value.changes);
  const approve = node('button', '원본 쓰기 별도 승인'); approve.disabled = !['pending', 'approved'].includes(value.status);
  const apply = node('button', '승인한 제안 적용'); apply.disabled = !approvalId || value.status !== 'approved';
  const reject = node('button', '거절'); reject.disabled = !['pending', 'approved'].includes(value.status);
  const instruction = node('textarea'); instruction.maxLength = 500; instruction.placeholder = '재요청 지시 · 새 AI 동의 필요';
  const revise = node('button', '새 생성 미리보기 준비'); revise.disabled = !['pending', 'approved', 'rejected', 'superseded'].includes(value.status);
  const restore = node('button', '원복 범위 확인·별도 승인'); restore.disabled = value.status !== 'recovery_required';
  const still = turn => generation === turn && current?.proposalId === value.proposalId && picker.value === value.projectId;
  approve.addEventListener('click', async () => {
    const turn = generation; approve.disabled = true;
    try {
      const accepted = await api.approveProposal({ projectId: value.projectId, proposalId: value.proposalId, expectedVersion: value.version,
        patchHash: value.patchHash, preconditionsHash: value.preconditionsHash, idempotencyKey: crypto.randomUUID() });
      if (!accepted) return;
      const fresh = await api.getProposal({ projectId: value.projectId, proposalId: value.proposalId });
      if (still(turn)) { executionKey = crypto.randomUUID(); show(fresh, accepted.approvalId); }
    } catch (error) { if (still(turn)) fail(error); } finally { approve.disabled = false; }
  });
  apply.addEventListener('click', async () => {
    const turn = generation; apply.disabled = true;
    try {
      const updated = await api.applyProposal({ projectId: value.projectId, proposalId: value.proposalId, approvalId, idempotencyKey: executionKey });
      if (still(turn)) { show(updated); message.textContent = updated.status === 'applied' ? '목표 hash를 확인했습니다. 적용 후 분석은 새 snapshot을 캡처하세요.' : '복구 상태를 확인하세요.'; }
    } catch (error) { if (still(turn)) { fail(error); try { const fresh = await api.getProposal({ projectId: value.projectId, proposalId: value.proposalId }); if (still(turn)) show(fresh, approvalId); } catch { /* Keep the durable status available for a later refresh. */ } } }
    finally { apply.disabled = false; }
  });
  reject.addEventListener('click', async () => {
    const turn = generation;
    try { const updated = await api.rejectProposal({ projectId: value.projectId, proposalId: value.proposalId, expectedVersion: value.version, idempotencyKey: crypto.randomUUID() }); if (still(turn)) show(updated); }
    catch (error) { if (still(turn)) fail(error); }
  });
  revise.addEventListener('click', async () => {
    const turn = generation;
    try {
      const preview = await api.requestProposalRevision({ projectId: value.projectId, proposalId: value.proposalId,
        expectedVersion: value.version, instruction: instruction.value, idempotencyKey: crypto.randomUUID() });
      if (still(turn)) { const fresh = await api.getProposal({ projectId: value.projectId, proposalId: value.proposalId }); if (still(turn)) { show(fresh); window.dispatchEvent(new CustomEvent('workspace-refactor-preview', { detail: preview })); } }
    } catch (error) { if (still(turn)) fail(error); }
  });
  restore.addEventListener('click', async () => {
    const turn = generation; restore.disabled = true; recoveryKey ??= crypto.randomUUID();
    try { const updated = await api.recoverProposal({ projectId: value.projectId, proposalId: value.proposalId, expectedVersion: value.version, idempotencyKey: recoveryKey }); if (updated && still(turn)) { recoveryKey = null; show(updated); } }
    catch (error) { if (still(turn)) fail(error); } finally { restore.disabled = false; }
  });
  view.append(approve, apply, reject, instruction, revise, restore);
  for (const journal of value.journal) view.append(node('p', `${journal.path} · ${journal.state} · journal ${journal.journalId}`));
}
async function listRecovery() {
  const projectId = picker.value; const turn = generation; if (!projectId) return;
  try {
    const response = await api.listRecoveryProposals({ projectId }); if (generation !== turn) return;
    recovery.replaceChildren();
    for (const item of response.proposals) {
      const button = node('button', `복구 필요 제안 ${item.proposalId}`);
      button.addEventListener('click', async () => { try { const value = await api.getProposal({ projectId, proposalId: item.proposalId }); if (generation === turn) show(value); } catch (error) { if (generation === turn) fail(error); } });
      recovery.append(button);
    }
    if (response.truncated) recovery.append(node('p', '복구 목록 상한에 도달했습니다. 먼저 표시된 제안을 확인하세요.'));
  } catch (error) { if (generation === turn) fail(error); }
}
window.addEventListener('workspace-project-changed', () => { generation++; current = null; executionKey = recoveryKey = null; view.replaceChildren(); recovery.replaceChildren(); message.textContent = ''; void listRecovery(); });
window.addEventListener('workspace-proposal-selected', event => { if (event.detail?.projectId === picker.value) { executionKey = recoveryKey = null; show(event.detail); } });
document.getElementById('reload-recovery').addEventListener('click', () => { void listRecovery(); });
