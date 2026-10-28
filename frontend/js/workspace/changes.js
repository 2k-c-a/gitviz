const api = window.workspace;
const picker = document.getElementById('project-picker');
const list = document.getElementById('change-selection');
const message = document.getElementById('capture-message');
const result = document.getElementById('capture-result');
const preview = document.getElementById('fixed-preview');
const detected = document.getElementById('detected-tools');
let generation = 0;
let snapshot = null;
let pendingCapture = null;
const planKeys = new Map();

function node(tag, text) { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; return element; }
function fail(error) { const code = String(error?.message || error).replace(/^.*Error invoking remote method.*Error: /, ''); message.textContent = code === 'CHANGESET_TOO_LARGE' ? '변경 파일 또는 저장소 항목이 너무 많아 조회할 수 없습니다. 커밋·stash로 변경 수를 줄이거나 더 작은 저장소 범위를 선택해 주세요.' : code; }
function clear() {
  window.dispatchEvent(new CustomEvent('workspace-snapshot-changed', { detail: null }));
  generation++; snapshot = null; pendingCapture = null; planKeys.clear();
  for (const target of [list, result, preview, detected]) target.replaceChildren();
  message.textContent = '';
}
async function load() {
  clear(); const turn = generation; const projectId = picker.value;
  if (!projectId) return;
  try {
    const state = await api.getGitStatus({ projectId, recentLimit: 1 });
    if (generation !== turn || picker.value !== projectId) return;
    for (const facet of ['staged', 'unstaged', 'untracked']) for (const file of state[facet]) {
      const row = node('label'); const checkbox = node('input'); checkbox.type = 'checkbox';
      checkbox.dataset.facet = facet; checkbox.dataset.path = file.path;
      row.append(checkbox, document.createTextNode(`${facet} · ${file.path}`));
      list.append(row, node('br'));
    }
    if (!list.childNodes.length) list.append(node('p', '캡처할 변경이 없습니다.'));
  } catch (error) { if (turn === generation) fail(error); }
}
function showSnapshot(value, turn) {
  snapshot = value;
  window.dispatchEvent(new CustomEvent('workspace-snapshot-changed', { detail: value }));
  preview.replaceChildren();
  result.replaceChildren(node('p', `고정 입력 ${value.inputHash} · 선택 ${value.coverage.selected} / 읽음 ${value.coverage.read} / 제외 ${value.coverage.excluded} · 전체 변경 ${value.coverage.changed}`));
  for (const file of value.files) {
    const row = node('p', `${file.facet} · ${file.path}${file.exclusionReason ? ` · 제외: ${file.exclusionReason}` : ''}`);
    if (file.state === 'read') for (const side of ['LEFT', 'RIGHT']) {
      if (!(side === 'LEFT' ? file.beforeHash : file.afterHash)) continue;
      const button = node('button', side === 'LEFT' ? '고정 이전 내용' : '고정 이후 내용');
      button.addEventListener('click', async () => {
        try {
          const response = await api.previewFile({ projectId: value.projectId, snapshotId: value.snapshotId, fileId: file.fileId, side, startLine: 1, endLine: 200 });
          if (generation !== turn || snapshot?.snapshotId !== value.snapshotId) return;
          preview.textContent = response.lines.map(line => `${line.line} ${line.text}`).join('\n') || '요청 범위의 diff 문맥 줄이 없습니다.';
        } catch (error) { if (generation === turn) fail(error); }
      });
      row.append(button);
    }
    if (file.state === 'read' && file.afterHash) {
      const open = node('button', '에디터에서 현재 파일 열기');
      open.addEventListener('click', async () => {
        try { await api.openEditor({ projectId: value.projectId, snapshotId: value.snapshotId, fileId: file.fileId, line: 1, column: 1, editorId: document.getElementById('editor-id').value }); }
        catch (error) { if (generation === turn) fail(error); }
      });
      const copy = node('button', '파일 위치 복사');
      copy.addEventListener('click', async () => {
        try { await api.copyLocation({ projectId: value.projectId, snapshotId: value.snapshotId, fileId: file.fileId, line: 1, column: 1 }); }
        catch (error) { if (generation === turn) fail(error); }
      }); row.append(open, copy);
    }
    result.append(row);
  }
}
document.getElementById('reload-changes').addEventListener('click', () => { void load(); });
window.addEventListener('workspace-project-changed', () => { void load(); });
document.getElementById('capture-changes').addEventListener('click', async event => {
  const projectId = picker.value; if (!projectId) return;
  const turn = generation;
  const items = [...list.querySelectorAll('input:checked')].map(input => ({ facet: input.dataset.facet, path: input.dataset.path }));
  const body = { projectId, items, includeUntracked: document.getElementById('include-untracked').checked };
  const encoded = JSON.stringify(body);
  if (!pendingCapture || pendingCapture.body !== encoded) pendingCapture = { body: encoded, key: crypto.randomUUID() };
  event.target.disabled = true;
  try {
    const value = await api.captureChanges({ ...body, idempotencyKey: pendingCapture.key });
    if (generation !== turn) return;
    pendingCapture = null; showSnapshot(value, turn); message.textContent = '선택한 입력을 고정했습니다.';
  } catch (error) { if (turn === generation) fail(error); }
  finally { event.target.disabled = false; }
});
document.getElementById('detect-validation').addEventListener('click', async () => {
  const projectId = picker.value; const turn = generation;
  if (!projectId) return;
  try {
    const value = await api.detectValidation({ projectId, cwdRelative: document.getElementById('detection-cwd').value, packageManager: document.getElementById('package-manager').value || null });
    if (generation !== turn) return;
    detected.replaceChildren(node('p', value.evidence.map(item => `${item.language}${item.framework ? ' / ' + item.framework : ''} · ${item.path}`).join(', ')), node('p', value.conflicts.join(', ')));
    for (const candidate of value.candidates) {
      const row = node('div'); row.append(node('p', `${candidate.name} · ${candidate.packageManager}${candidate.available ? '' : ' · 실행 도구 설정 필요'}`), node('pre', JSON.stringify(candidate.scripts, null, 2)));
      const button = node('button', '고정 입력에 명령 계획 연결'); button.disabled = !candidate.available;
      button.addEventListener('click', async () => {
        if (!snapshot || snapshot.projectId !== projectId) { fail('변경 입력을 먼저 캡처하세요.'); return; }
        const snapshotId = snapshot.snapshotId; const key = `${snapshotId}:${candidate.candidateId}`;
        if (!planKeys.has(key)) planKeys.set(key, crypto.randomUUID());
        try {
          const plan = await api.planValidation({ projectId, snapshotId, candidateId: candidate.candidateId, idempotencyKey: planKeys.get(key) });
          if (generation === turn && snapshot?.snapshotId === snapshotId) { row.append(node('pre', JSON.stringify(plan, null, 2)), node('p', '계획을 저장했습니다. 아래 검증 실행에서 native 승인하세요.')); window.dispatchEvent(new CustomEvent('workspace-validation-plan', { detail: plan })); }
        } catch (error) { if (generation === turn) fail(error); }
      });
      row.append(button); detected.append(row);
    }
  } catch (error) { if (generation === turn) fail(error); }
});
