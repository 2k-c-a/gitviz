const api = window.workspace;
const picker = document.getElementById('project-picker');
const view = document.getElementById('impact-view');
const message = document.getElementById('impact-message');
let snapshot = null;
let proposal = null;
let generation = 0;
function node(tag, text) { const value = document.createElement(tag); value.textContent = text; return value; }
function show(value, append = false) {
  if (!append) view.replaceChildren(node('p', `고정 입력 ${value.inputHash} · parser 참조 ${value.totalRecords}개`));
  for (const record of value.records) view.append(node('p', `${record.kind} · 관측한 구문 · ${record.toolVersion}\n${record.facet} · ${record.path}:${record.line}:${record.column} · ${record.symbol}\n${record.resolved ? `정적 바인딩 ${record.targetFileId}:${record.targetLine}` : '미해결 참조'} · 실행 call graph 보증 아님`));
  if (!append) {
    for (const inference of value.inferences) view.append(node('p', `AI 추론 · 관측값 아님: ${inference.text}`));
    view.append(node('p', `과거 공동 변경 이력: ${value.historical.status} · ${value.historical.reason} · 0건으로 해석하지 않습니다.`));
    for (const limit of value.limitations) view.append(node('p', limit));
  }
  if (value.nextOffset !== null) {
    const more = node('button', '근거 더 보기'); const turn = generation;
    more.addEventListener('click', async () => { more.disabled = true; try { const page = await api.getImpact({ projectId: value.projectId, analysisId: value.analysisId, offset: value.nextOffset }); if (turn === generation) { more.remove(); show(page, true); } } catch (error) { if (turn === generation) { message.textContent = String(error?.message || error); more.disabled = false; } } });
    view.append(more);
  }
}
window.addEventListener('workspace-project-changed', () => { generation++; snapshot = proposal = null; view.replaceChildren(); message.textContent = ''; });
window.addEventListener('workspace-snapshot-changed', event => { generation++; snapshot = event.detail; view.replaceChildren(); });
window.addEventListener('workspace-proposal-selected', event => { proposal = event.detail; });
document.getElementById('analyze-impact').addEventListener('click', async event => {
  const selected = snapshot; const turn = generation; if (!selected || selected.projectId !== picker.value) { message.textContent = '먼저 고정 입력을 캡처하세요.'; return; }
  event.target.disabled = true;
  try {
    const value = await api.analyzeImpact({ projectId: selected.projectId, snapshotId: selected.snapshotId,
      ...(proposal?.snapshotId === selected.snapshotId && proposal.projectId === selected.projectId ? { proposalId: proposal.proposalId } : {}) });
    if (generation === turn) show(value);
  } catch (error) { if (generation === turn) message.textContent = String(error?.message || error); }
  finally { event.target.disabled = false; }
});
