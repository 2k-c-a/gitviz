const api = window.workspace;
const picker = document.getElementById('project-picker');
const message = document.getElementById('validation-message');
const view = document.getElementById('validation-view');
let generation = 0;
let current = null;
let timer = null;
const attempts = new Map();
function node(tag, text) { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; return element; }
function fail(error) {
  const code = String(error?.message || error).replace(/^.*Error invoking remote method.*Error: /, '');
  message.textContent = code;
  if (code === 'PRECONDITION_FAILED') message.textContent = '입력·설정·환경 조건이 달라 실행하지 않았습니다. 변경을 새로 캡처하고 도구·계획을 다시 준비하세요.';
  if (code === 'COMMAND_NOT_ALLOWED') message.textContent = '도구·환경을 확인하고 설치·수정 모드가 없는 검증 계획을 다시 준비하세요.';
  if (code === 'EDITOR_UNAVAILABLE_OR_STALE') message.textContent = '에디터 실행 파일을 다시 선택해 설정하거나 파일 위치를 복사하세요.';
}
window.addEventListener('workspace-project-changed', () => { generation++; current = null; clearTimeout(timer); view.replaceChildren(); message.textContent = ''; attempts.clear(); });
async function show(value, turn) {
  if (turn !== generation || picker.value !== value.projectId) return;
  current = value; view.replaceChildren(node('h3', `${value.status} · run ${value.runId}`),
    node('p', `snapshot ${value.snapshotId} · plan ${value.planId} · exit ${value.exitCode ?? '미확정'}${value.truncated ? ' · 출력 상한 도달' : ''}`));
  document.getElementById('validation-cancel').disabled = value.status !== 'running';
  const output = node('pre'); view.append(output);
  let page = value;
  for (;;) {
    if (turn !== generation || current?.runId !== value.runId) return;
    for (const item of page.output) output.append(document.createTextNode(`${item.sequence} ${item.stream}: ${item.text}`));
    if (page.nextOffset === null) break;
    page = await api.getValidation({ projectId: value.projectId, runId: value.runId, offset: page.nextOffset, limit: 10 });
  }
  for (const diagnostic of value.diagnostics) {
    const target = diagnostic.columnEncoding === 'utf16' ? `${diagnostic.line}:${diagnostic.column}` : `${diagnostic.line} · 행 시작 열기`;
    const button = node('button', `${diagnostic.path}:${target} ${diagnostic.code} · 원인 확정 아님`);
    button.addEventListener('click', async () => {
      try { await api.openEditor({ projectId: value.projectId, snapshotId: value.snapshotId, fileId: diagnostic.fileId,
        line: diagnostic.line, column: diagnostic.columnEncoding === 'utf16' ? diagnostic.column : 1, editorId: document.getElementById('editor-id').value }); }
      catch (error) { if (turn === generation) fail(error); }
    }); view.append(button);
  }
  if (!value.diagnostics.length && value.status !== 'running') view.append(node('p', '원문 출력 · 연결 가능한 진단 위치 없음'));
  for (const text of value.limitations) view.append(node('p', text));
  if (value.status !== 'running') attempts.delete(`${value.projectId}:${value.planId}`);
  if (value.status !== 'running') window.dispatchEvent(new CustomEvent('workspace-validation-finished', { detail: value }));
}
async function poll(projectId, runId, turn) {
  try {
    const value = await api.getValidation({ projectId, runId, offset: 0, limit: 10 });
    await show(value, turn);
    if (turn === generation && value.status === 'running') timer = setTimeout(() => { void poll(projectId, runId, turn); }, 500);
  } catch (error) { if (turn === generation) fail(error); }
}
window.addEventListener('workspace-validation-plan', event => {
  const plan = event.detail;
  if (plan.projectId !== picker.value) return;
  const turn = generation;
  const section = node('article'); section.append(node('pre', JSON.stringify(plan, null, 2)));
  const run = node('button', '계획 검토·프로젝트 신뢰 확인 후 실행');
  run.addEventListener('click', async () => {
    run.disabled = true;
    const key = `${plan.projectId}:${plan.planId}`;
    if (!attempts.has(key)) attempts.set(key, crypto.randomUUID());
    try {
      const value = await api.runValidation({ projectId: plan.projectId, planId: plan.planId, idempotencyKey: attempts.get(key) });
      if (value && turn === generation) { clearTimeout(timer); await show(value, turn); void poll(value.projectId, value.runId, turn); }
    } catch (error) { if (turn === generation) fail(error); }
    finally { run.disabled = false; }
  }); section.append(run); document.getElementById('validation-plans').append(section);
});
document.getElementById('validation-cancel').addEventListener('click', async () => {
  if (!current) return;
  const turn = generation;
  try { await api.cancelValidation({ projectId: current.projectId, runId: current.runId }); }
  catch (error) { if (turn === generation) fail(error); }
});
document.getElementById('configure-editor').addEventListener('click', async () => {
  try { const value = await api.configureEditor({ editorId: document.getElementById('editor-id').value }); if (value) message.textContent = `${value.editorId} 설정 완료`; }
  catch (error) { fail(error); }
});
window.addEventListener('workspace-project-changed', () => document.getElementById('validation-plans').replaceChildren());

let editorVersion = 1;
window.addEventListener('workspace-project-changed', async () => {
  const turn = generation; const projectId = picker.value;
  if (!projectId) return;
  try {
    const value = await api.getProject({ projectId });
    if (turn !== generation || picker.value !== projectId) return;
    editorVersion = value.settings.editorId?.version || 1;
    document.getElementById('editor-id').value = value.settings.editorId?.value || 'vscode';
  } catch (error) { if (turn === generation) fail(error); }
});
document.getElementById('save-editor').addEventListener('click', async () => {
  const turn = generation; const projectId = picker.value;
  if (!projectId) return;
  try {
    const value = await api.setEditor({ projectId, editorId: document.getElementById('editor-id').value, expectedVersion: editorVersion, idempotencyKey: crypto.randomUUID() });
    if (turn === generation) { editorVersion = value.settings.editorId.version; message.textContent = '프로젝트 기본 에디터를 저장했습니다.'; }
  } catch (error) { if (turn === generation) fail(error); }
});
