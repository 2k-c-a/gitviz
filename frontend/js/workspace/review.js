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

function node(tag, text) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; return el; }
function fail(error) {
  const reason = String(error?.message || error).replace(/^.*Error invoking remote method.*Error: /, '');
  const guidance = {
    CLOUD_MAPPING_REQUIRED: '현재 계정으로 연결한 공개 저장소만 cloud를 사용할 수 있습니다. 연결·공개 여부·권한을 다시 확인하세요. PC 모델 경로는 사용할 수 있습니다.',
    BUDGET_EXCEEDED: '이 앱의 월 USD20 예약·사용 한도를 넘습니다. 미정산·결과 불명 예약도 포함하므로 제공자 사용량과 기록을 확인하세요.',
    ACCOUNT_CHANGED: '계정이 바뀌어 전송을 중단했습니다. 새 계정에서 미리보기와 동의를 다시 진행하세요.',
    AUTH_REQUIRED: '원격 계정에 로그인한 뒤 저장소 연결을 확인하세요.',
  };
  message.textContent = guidance[reason] ?? reason;
}
function invalidate() {
  previewEpoch++;
  approved = null; startKey = null; consent.checked = false; start.disabled = true;
  bytes.textContent = ''; meta.replaceChildren();
}
function reset() {
  generation++; clearTimeout(timer); snapshot = null; current = null; document.getElementById('review-api-key').value = '';
  invalidate(); result.replaceChildren(); history.replaceChildren(); message.textContent = ''; cancel.disabled = true;
}
window.addEventListener('workspace-project-changed', reset);
window.addEventListener('workspace-snapshot-changed', event => { reset(); snapshot = event.detail; });
for (const id of ['review-purpose', 'review-generation', 'review-kind', 'review-profile', 'review-api-key']) document.getElementById(id).addEventListener('input', invalidate);
consent.addEventListener('change', () => { start.disabled = !approved || !consent.checked; });

document.getElementById('review-preview').addEventListener('click', async () => {
  invalidate(); const turn = generation; const epoch = previewEpoch; const selected = snapshot;
  if (!selected || selected.projectId !== picker.value) { message.textContent = '먼저 변경 입력을 캡처하세요.'; return; }
  try {
    const options = { projectId: picker.value, snapshotId: selected.snapshotId, kind: document.getElementById('review-kind').value,
      purpose: document.getElementById('review-purpose').value, profileId: document.getElementById('review-profile').value,
      ...(document.getElementById('review-profile').value === 'ollama-local-v1' ? {} : { apiKey: document.getElementById('review-api-key').value }),
      generation: Number(document.getElementById('review-generation').value) };
    document.getElementById('review-api-key').value = '';
    const pending = api.previewReview(options); delete options.apiKey;
    const value = await pending;
    if (generation !== turn || previewEpoch !== epoch || snapshot?.snapshotId !== selected.snapshotId) return;
    approved = value; startKey = crypto.randomUUID();
    meta.textContent = `실행 위치 ${value.executionLocation} · 대상 ${value.destination} · 모델 ${value.profile.model} · 입력 추정 ${value.estimatedInputTokens} tokens · provider 요금 추정 USD ${value.maxEstimatedCost}\n입력 ${value.inputHash}\n전송 ${value.manifestHash}\nprofile ${value.profileFingerprint}\n정책 ${value.policyVersion}\n선택 ${value.coverage.selected} / 읽음 ${value.coverage.read} / 제외 ${value.coverage.excluded}`;
    bytes.textContent = `SYSTEM\n${value.system}\n\nUSER\n${value.user}`;
    meta.append(document.createTextNode(`\n실제 요청 ${value.requestBytes} bytes · SHA256 ${value.requestHash}`));
    message.textContent = '위 내용이 실제 전송 범위입니다. 동의는 5분 동안 유효합니다.';
  } catch (error) { if (generation === turn) fail(error); }
  finally { document.getElementById('review-api-key').value = ''; }
});

function show(value) {
  current = value; result.replaceChildren(node('p', `작업 ${value.runId} · ${value.status} · 시도 ${value.attemptCount} · 예약 USD ${value.budgetReserved}${value.errorCode ? ` · ${value.errorCode}` : ''}`));
  cancel.disabled = !['running', 'queued'].includes(value.status);
  if (value.outcomeUncertain) result.append(node('p', '요청 처리·과금 여부가 불명확합니다. 예약을 유지하며 자동 재송신하지 않습니다.'));
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
  } catch (error) { if (generation === turn) fail(error); }
}
start.addEventListener('click', async () => {
  if (!approved || !consent.checked) return;
  const value = approved; const turn = generation; start.disabled = true;
  try {
    const response = await api.startReview({ projectId: value.projectId, previewId: value.previewId, previewHash: value.previewHash,
      manifestHash: value.manifestHash, maxEstimatedCost: value.maxEstimatedCost, acknowledged: true,
      duplicateRiskAcknowledged: document.getElementById('review-duplicate-risk').checked, idempotencyKey: startKey });
    if (generation !== turn || approved?.previewId !== value.previewId || snapshot?.snapshotId !== value.snapshotId) return;
      if (response === null) { message.textContent = '사용자가 전송을 취소했습니다'; start.disabled = !consent.checked; return; }
    clearTimeout(timer); show(response); void poll(value.projectId, response.runId, turn);
  } catch (error) { if (generation === turn) { fail(error); start.disabled = !consent.checked; } }
});
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
