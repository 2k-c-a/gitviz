import { aiDefaults } from './ai-settings.js';
const api = window.workspace;
const picker = document.getElementById('project-picker');
const message = document.getElementById('review-message');
const result = document.getElementById('review-result');
const history = document.getElementById('review-history-list');
const bytes = document.getElementById('review-preview-bytes');
const meta = document.getElementById('review-preview-meta');
const start = document.getElementById('review-start');
const cancel = document.getElementById('review-cancel');
const consent = document.getElementById('review-consent');
let snapshot = null;
let approved = null;
let current = null;
let generation = 0;
let timer = null;
let startKey = null;
let previewEpoch = 0;
// Refactor run started from the one-click button: its success becomes a proposal without another click.
let autoProposal = null;

function node(tag, text) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; return el; }
function fail(error) {
  const reason = String(error?.message || error).replace(/^.*Error invoking remote method.*Error: /, '');
  const guidance = {
    CLOUD_MAPPING_REQUIRED: '클라우드 모델 준비가 끝나지 않았습니다. 앱을 다시 시작한 뒤 시도하세요. 이 PC의 Ollama는 계속 쓸 수 있습니다.',
    BUDGET_EXCEEDED: '이 앱의 월 사용 한도(USD 20)를 넘습니다. 아직 정산되지 않았거나 결과를 알 수 없는 예약도 포함되니 제공자 사용량과 기록을 확인하세요.',
    ACCOUNT_CHANGED: '계정이 바뀌어 전송을 중단했습니다. 새 계정에서 미리보기와 동의를 다시 진행하세요.',
    AUTH_REQUIRED: '원격 계정에 로그인한 뒤 저장소 연결을 확인하세요.',
    API_KEY_REQUIRED: '이 모델의 API 키가 없습니다. 오른쪽 위 내 계정 → 설정에서 키를 저장하거나 다른 모델을 고르세요.',
  };
  message.textContent = guidance[reason] ?? reason;
}
function invalidate() {
  previewEpoch++;
  approved = null; startKey = null; consent.checked = false; start.disabled = true;
  bytes.textContent = ''; meta.replaceChildren();
}
function reset() {
  generation++; clearTimeout(timer); snapshot = null; current = null; autoProposal = null; document.getElementById('review-api-key').value = '';
  invalidate(); result.replaceChildren(); history.replaceChildren(); message.textContent = ''; cancel.disabled = true;
}
function useDefaults() {
  const defaults = aiDefaults(); const profile = document.getElementById('review-profile');
  profile.value = defaults.profileId; profile.dispatchEvent(new Event('change'));
  document.getElementById('review-purpose').value = defaults.purpose;
}
window.addEventListener('workspace-project-changed', reset);
window.addEventListener('workspace-snapshot-changed', event => { reset(); snapshot = event.detail; if (snapshot) useDefaults(); });
for (const id of ['review-purpose', 'review-generation', 'review-kind', 'review-profile', 'review-api-key']) document.getElementById(id).addEventListener('input', invalidate);

/** Builds the exact request; returns the preview, or null when it failed or became stale. */
async function prepare() {
  invalidate(); const turn = generation; const epoch = previewEpoch; const selected = snapshot;
  if (!selected || selected.projectId !== picker.value) { message.textContent = '먼저 변경을 담으세요.'; return null; }
  try {
    const options = { projectId: picker.value, snapshotId: selected.snapshotId, kind: document.getElementById('review-kind').value,
      purpose: document.getElementById('review-purpose').value, profileId: document.getElementById('review-profile').value,
      ...(document.getElementById('review-profile').value === 'ollama-local-v1' || !document.getElementById('review-api-key').value ? {} : { apiKey: document.getElementById('review-api-key').value }),
      generation: Number(document.getElementById('review-generation').value) };
    document.getElementById('review-api-key').value = '';
    const pending = api.previewReview(options); delete options.apiKey;
    const value = await pending;
    if (generation !== turn || previewEpoch !== epoch || snapshot?.snapshotId !== selected.snapshotId) return null;
    approved = value; startKey = crypto.randomUUID(); start.disabled = false;
    meta.textContent = `실행 위치 ${value.executionLocation} · 대상 ${value.destination} · 모델 ${value.profile.model} · 입력 추정 ${value.estimatedInputTokens} tokens · 제공자 요금 추정 USD ${value.maxEstimatedCost}\n입력 ${value.inputHash}\n전송 ${value.manifestHash}\nprofile ${value.profileFingerprint}\n정책 ${value.policyVersion}\n선택 ${value.coverage.selected} / 읽음 ${value.coverage.read} / 제외 ${value.coverage.excluded}`;
    bytes.textContent = `SYSTEM\n${value.system}\n\nUSER\n${value.user}`;
    meta.append(document.createTextNode(`\n실제 요청 ${value.requestBytes} bytes · SHA256 ${value.requestHash}`));
    message.textContent = '위 내용이 실제 전송 범위입니다. 미리보기는 5분 동안 유효합니다.';
    return value;
  } catch (error) { if (generation === turn) fail(error); return null; }
  finally { document.getElementById('review-api-key').value = ''; }
}
document.getElementById('review-preview').addEventListener('click', () => { void prepare(); });

function show(value) {
  current = value; result.replaceChildren(node('p', `작업 ${value.runId} · ${value.status} · 시도 ${value.attemptCount} · 예약 USD ${value.budgetReserved}${value.errorCode ? ` · ${value.errorCode}` : ''}`));
  cancel.disabled = !['running', 'queued'].includes(value.status);
  if (value.outcomeUncertain) result.append(node('p', '요청이 처리됐는지, 요금이 청구됐는지 알 수 없습니다. 예약은 그대로 두고 자동으로 다시 보내지 않습니다.'));
  for (const finding of value.findings) {
    const section = node('article');
    section.append(node('h3', `${finding.category} · ${finding.priority} · ${finding.severity}`),
      node('p', `심각도 근거: ${finding.severityReason}`), node('p', `발생 조건: ${finding.condition}`),
      node('p', `영향: ${finding.impact}`), node('p', `제안: ${finding.suggestion}`),
      node('p', `검증: ${finding.verification} · 원본 사람 판정: ${finding.humanValidation}`));
    for (const evidence of finding.evidence) section.append(node('p', `${evidence.facet} · ${evidence.path} · ${evidence.side} ${evidence.startLine}–${evidence.endLine} · ${evidence.fileId}`), node('pre', evidence.quote));
    for (const limitation of finding.limitations) section.append(node('p', limitation));
    const verdict = node('select');
    for (const name of ['unverified', 'valid', 'false_positive', 'uncertain']) { const option = node('option', name); option.value = name; verdict.append(option); }
    verdict.value = finding.feedback.verdict;
    const note = node('textarea'); note.maxLength = 1000; note.value = finding.feedback.note;
    const save = node('button', '사람 판정 저장');
    save.addEventListener('click', async () => {
      const turn = generation; save.disabled = true;
      try {
        const response = await api.feedbackReview({ projectId: value.projectId, findingId: finding.findingId,
          expectedVersion: finding.feedback.version, verdict: verdict.value, note: note.value, idempotencyKey: crypto.randomUUID() });
        if (turn === generation && current?.runId === value.runId) show(response);
      } catch (error) { if (turn === generation) fail(error); }
      finally { save.disabled = false; }
    });
    section.append(verdict, note, save); result.append(section);
  }
  for (const limitation of value.limitations) result.append(node('p', limitation));
  if (value.kind === 'refactor' && value.refactor) {
    result.append(node('h3', '리팩토링 생성 결과 · 효과는 예측이며 실행 검증 전입니다.'));
    for (const effect of value.refactor.expectedEffects) result.append(node('p', effect));
    for (const change of value.refactor.changes) result.append(node('pre', change.patch));
    const create = node('button', '이 고정 결과를 적용 제안으로 준비');
    create.addEventListener('click', async () => {
      const turn = generation; create.disabled = true;
      try {
        const proposal = await api.createProposal({ projectId: value.projectId, refactorRunId: value.runId, idempotencyKey: crypto.randomUUID() });
        if (turn === generation && picker.value === value.projectId) window.dispatchEvent(new CustomEvent('workspace-proposal-selected', { detail: proposal }));
      } catch (error) { if (turn === generation) fail(error); }
      finally { create.disabled = false; }
    });
    result.append(create);
  }
}
window.addEventListener('workspace-refactor-preview', event => {
  const value = event.detail;
  if (!value || value.projectId !== picker.value) return;
  invalidate(); snapshot = { projectId: value.projectId, snapshotId: value.snapshotId }; approved = value; startKey = crypto.randomUUID();
  document.getElementById('review-kind').value = 'refactor'; document.getElementById('review-purpose').value = value.purpose;
  document.getElementById('review-generation').value = String(value.generation);
  meta.textContent = `재요청 고정 입력 ${value.snapshotId}\n${value.inputHash}\n전송 ${value.requestHash} · ${value.destination} · ${value.profile.model}`;
  bytes.textContent = `SYSTEM\n${value.system}\n\nUSER\n${value.user}`;
  message.textContent = '재요청은 기존 제안의 고정 입력을 사용합니다. 새 내용에 동의한 뒤 생성하세요. 원본 쓰기는 별도 승인입니다.';
});
async function poll(projectId, runId, turn) {
  try {
    const value = await api.getReview({ projectId, runId });
    if (generation !== turn || picker.value !== projectId || current?.runId !== runId) return;
    show(value);
    if (['queued', 'running', 'cancel_requested'].includes(value.status)) timer = setTimeout(() => { void poll(projectId, runId, turn); }, 1000);
    else await propose(value);
  } catch (error) { if (generation === turn) fail(error); }
}
// Owner decision (2026-11-01): the preview is shown, but starting no longer needs a consent checkbox.
async function begin() {
  if (!approved) return null;
  const value = approved; const turn = generation; start.disabled = true;
  try {
    const response = await api.startReview({ projectId: value.projectId, previewId: value.previewId, previewHash: value.previewHash,
      manifestHash: value.manifestHash, maxEstimatedCost: value.maxEstimatedCost, acknowledged: true,
      duplicateRiskAcknowledged: document.getElementById('review-duplicate-risk').checked, idempotencyKey: startKey });
    if (generation !== turn || approved?.previewId !== value.previewId || snapshot?.snapshotId !== value.snapshotId) return null;
    if (response === null) { message.textContent = '전송을 취소했습니다.'; start.disabled = false; return null; }
    clearTimeout(timer); show(response); void poll(value.projectId, response.runId, turn);
    return response;
  } catch (error) { if (generation === turn) { fail(error); start.disabled = false; } return null; }
}
start.addEventListener('click', () => { void begin(); });
/** One-click review/refactor from the changes tab: defaults from settings, preview, start, and for refactor a proposal. */
window.addEventListener('workspace-quick-ai', async event => {
  const { kind, snapshotId } = event.detail;
  if (snapshot?.snapshotId !== snapshotId) return;
  document.getElementById('review-kind').value = kind; document.getElementById('review-generation').value = '1';
  window.dispatchEvent(new CustomEvent('workspace-open-target', { detail: { kind: 'review' } }));
  message.textContent = kind === 'refactor' ? '리팩토링을 요청하고 있습니다…' : '리뷰를 요청하고 있습니다…';
  if (!await prepare()) return;
  const response = await begin();
  if (response && kind === 'refactor') { autoProposal = response.runId; await propose(response); }
});
async function propose(value) {
  if (autoProposal !== value.runId || value.status !== 'succeeded' || !value.refactor) return;
  autoProposal = null; const turn = generation;
  try {
    const proposal = await api.createProposal({ projectId: value.projectId, refactorRunId: value.runId, idempotencyKey: crypto.randomUUID() });
    if (turn !== generation || picker.value !== value.projectId) return;
    window.dispatchEvent(new CustomEvent('workspace-proposal-selected', { detail: proposal }));
    window.dispatchEvent(new CustomEvent('workspace-open-target', { detail: { kind: 'proposal' } }));
  } catch (error) { if (turn === generation) fail(error); }
}
cancel.addEventListener('click', async () => {
  const value = current; if (!value) return; const turn = generation; cancel.disabled = true;
  try {
    const response = await api.cancelReview({ projectId: value.projectId, runId: value.runId,
      expectedVersion: value.version, idempotencyKey: crypto.randomUUID() });
    if (generation === turn && current?.runId === value.runId) show(response);
  } catch (error) { if (generation === turn) fail(error); }
});
document.getElementById('review-history').addEventListener('click', async () => {
  const projectId = picker.value; const turn = generation; if (!projectId) return;
  try {
    const value = await api.listReviews({ projectId, limit: 50, offset: 0 });
    if (turn !== generation) return;
    history.replaceChildren();
    for (const run of value.runs) {
      const button = node('button', `${run.runId} · ${run.status}`);
      button.addEventListener('click', async () => {
        try {
          const response = await api.getReview({ projectId, runId: run.runId });
          if (generation === turn) { clearTimeout(timer); show(response); void poll(projectId, run.runId, turn); }
        } catch (error) { if (generation === turn) fail(error); }
      });
      history.append(button);
    }
  } catch (error) { if (generation === turn) fail(error); }
});
