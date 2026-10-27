const api = window.workspace;
const picker = document.getElementById('project-picker');
const before = document.getElementById('comparison-before');
const after = document.getElementById('comparison-after');
const view = document.getElementById('comparison-view');
const snapshots = new Map();
const runs = new Map();
let generation = 0;
let proposalId = null;
let pending = null;
function node(tag, text) { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; return element; }
window.addEventListener('workspace-project-changed', () => { generation++; snapshots.clear(); runs.clear(); proposalId = null; pending = null; before.replaceChildren(); after.replaceChildren(); view.replaceChildren(); });
window.addEventListener('workspace-snapshot-changed', event => {
  const value = event.detail; if (!value || value.projectId !== picker.value || snapshots.has(value.snapshotId)) return;
  snapshots.set(value.snapshotId, value);
  for (const select of [before, after]) { const option = node('option', `${value.capturedAt} · ${value.snapshotId}`); option.value = value.snapshotId; select.append(option); }
  after.value = value.snapshotId; pending = null;
});
window.addEventListener('workspace-validation-finished', event => { const value = event.detail; if (value.projectId === picker.value) { runs.set(value.snapshotId, value.runId); pending = null; } });
window.addEventListener('workspace-proposal-selected', event => { if (event.detail?.projectId === picker.value) proposalId = event.detail.proposalId; });
document.getElementById('compare-snapshots').addEventListener('click', async () => {
  const turn = generation; const projectId = picker.value;
  if (!snapshots.has(before.value) || !snapshots.has(after.value)) { view.textContent = '전후 snapshot을 각각 캡처하세요. baseline 검증은 변경 전에 명시 실행해야 합니다.'; return; }
  const body = { projectId, beforeSnapshotId: before.value, afterSnapshotId: after.value, proposalId: null,
    beforeRunId: runs.get(before.value) || null, afterRunId: runs.get(after.value) || null };
  // A selected proposal may belong to a different baseline; explicit pairing is optional.
  if (proposalId && document.getElementById('comparison-link-proposal').checked) body.proposalId = proposalId;
  const encoded = JSON.stringify(body);
  if (!pending || pending.body !== encoded) pending = { body: encoded, key: crypto.randomUUID() };
  try {
    const value = await api.createComparison({ ...body, idempotencyKey: pending.key });
    if (turn !== generation) return;
    view.replaceChildren(node('h3', `검증 비교 ${value.validationStatus}`), node('p', value.reasons.join(', ')),
      node('p', `변경 파일 ${value.changedFiles} · 추가 ${value.additions ?? '미측정'} · 삭제 ${value.deletions ?? '미측정'}`),
      node('pre', JSON.stringify({ before: value.before, after: value.after }, null, 2)),
      node('h4', '목적'), node('p', value.purpose || '연결한 제안 없음'), node('h4', '기대효과 · 예측'), node('p', value.expectedEffects.join(', ')), node('h4', '관찰 결과 · 실행 상태'), node('pre', JSON.stringify(value.validationOutcomes, null, 2)), node('h4', '남은 위험'), node('p', value.remainingRisks.join(', ')), node('p', value.diffLimitations.join(', ')),
      node('p', '표시한 AST 지표는 선택 입력의 실측입니다. 품질·속도 개선이나 개별 테스트 전이는 자동 주장하지 않습니다.'));
  } catch (error) { if (turn === generation) view.textContent = String(error?.message || error).replace(/^.*Error invoking remote method.*Error: /, ''); }
});
