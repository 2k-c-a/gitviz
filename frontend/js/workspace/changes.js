const api = window.workspace;
const picker = document.getElementById('project-picker');
const list = document.getElementById('change-selection');
const message = document.getElementById('capture-message');
const result = document.getElementById('capture-result');
const preview = document.getElementById('fixed-preview');
const detected = document.getElementById('detected-tools');
const tree = document.getElementById('file-tree');
let generation = 0;
let snapshot = null;
let pendingCapture = null;
const planKeys = new Map();
/** path -> facets ('staged' | 'unstaged' | 'untracked') from the latest Git status; directories get 'changed'. */
let changed = new Map();

const MESSAGES = {
  CHANGESET_TOO_LARGE: '변경 파일 또는 저장소 항목이 너무 많아 조회할 수 없습니다. 커밋·stash로 변경 수를 줄이거나 더 작은 저장소 범위를 선택해 주세요.',
  INPUT_TOO_LARGE: '담을 내용이 너무 큽니다(한 번에 약 1MB, 파일 하나 200KB까지). 파일 수를 줄여 다시 담으세요.',
  CAPTURE_CHANGED: '담는 동안 파일이 바뀌었습니다. 새로고침한 뒤 다시 시도하세요.',
  RESOURCE_NOT_FOUND: '파일이나 폴더를 찾을 수 없습니다. 새로고침하세요.',
  CONFLICT: '병합 충돌이 남아 있어 담을 수 없습니다. 충돌을 먼저 해결하세요.',
  EDITOR_UNAVAILABLE_OR_STALE: '에디터를 열지 못했습니다. 설정에서 에디터 실행 파일을 지정했는지 확인하세요.',
};
function node(tag, text, className) { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; if (className) element.className = className; return element; }
function fail(error) { const code = String(error?.message || error).replace(/^.*Error invoking remote method.*Error: /, ''); message.textContent = MESSAGES[code] ?? code; }
function clear() {
  window.dispatchEvent(new CustomEvent('workspace-snapshot-changed', { detail: null }));
  generation++; snapshot = null; pendingCapture = null; planKeys.clear(); changed = new Map();
  for (const target of [list, result, preview, detected, tree]) target.replaceChildren();
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
      changed.set(file.path, [...(changed.get(file.path) ?? []), facet]);
      for (let index = file.path.lastIndexOf('/'); index > 0; index = file.path.lastIndexOf('/', index - 1)) changed.set(file.path.slice(0, index), ['changed']);
    }
    if (!list.childNodes.length) list.append(node('p', '변경된 파일이 없습니다. 아래 전체 파일에서 점검할 파일을 고르세요.', 'ws-note'));
  } catch (error) { if (turn === generation) fail(error); }
  if (generation === turn) await expand(tree, '', projectId, turn);
}

/* ---------- file tree: one directory per request, loaded when a folder is opened ---------- */
const FACET_LABEL = { staged: 'S', unstaged: 'M', untracked: 'U' };
function badge(path) {
  const facets = changed.get(path);
  if (!facets) return null;
  const label = facets.includes('changed') ? '•' : facets.map(facet => FACET_LABEL[facet]).join('');
  const element = node('span', label, 'ws-tree-badge'); element.title = facets.includes('changed') ? '안에 변경된 파일이 있습니다' : facets.join(', ');
  return element;
}
function fileRow(entry, projectId, turn) {
  const row = node('div', undefined, 'ws-tree-row'); row.setAttribute('role', 'treeitem');
  const checkbox = node('input'); checkbox.type = 'checkbox'; checkbox.dataset.facet = 'selected'; checkbox.dataset.path = entry.path;
  checkbox.setAttribute('aria-label', `${entry.path} 선택`);
  const open = node('button', entry.name, 'ws-tree-file'); open.type = 'button'; open.title = `${entry.path} — 기본 에디터로 열기`;
  open.addEventListener('click', async () => {
    try {
      await api.openEditor({ projectId, path: entry.path, line: 1, column: 1, editorId: document.getElementById('editor-id').value });
      if (generation === turn) message.textContent = `${entry.path}을(를) 에디터로 열었습니다.`;
    } catch (error) { if (generation === turn) fail(error); }
  });
  row.append(checkbox, open);
  const mark = badge(entry.path); if (mark) row.append(mark);
  if (entry.size > 200000) row.append(node('span', '200KB 초과 · 점검 제외', 'ws-note'));
  return row;
}
function folderRow(entry, projectId, turn) {
  if (entry.kind !== 'directory') {
    const row = node('div', undefined, 'ws-tree-row'); row.setAttribute('role', 'treeitem');
    row.append(node('span', entry.name), node('span', entry.kind === 'repository' ? '하위 저장소 · 열지 않음' : '링크 · 지원 안 함', 'ws-note'));
    return row;
  }
  const folder = node('details', undefined, 'ws-tree-folder'); folder.setAttribute('role', 'treeitem');
  const summary = node('summary', entry.name); const mark = badge(entry.path); if (mark) summary.append(mark);
  const children = node('div', undefined, 'ws-tree-children'); children.setAttribute('role', 'group');
  folder.append(summary, children);
  folder.addEventListener('toggle', () => {
    folder.setAttribute('aria-expanded', String(folder.open));
    if (folder.open && !folder.dataset.loaded) { folder.dataset.loaded = 'true'; void expand(children, entry.path, projectId, turn); }
  });
  folder.setAttribute('aria-expanded', 'false');
  return folder;
}
async function expand(target, directory, projectId, turn) {
  target.replaceChildren(node('p', '불러오는 중…', 'ws-note'));
  try {
    const value = await api.getFileTree({ projectId, directory });
    if (generation !== turn || picker.value !== projectId) return;
    target.replaceChildren(...value.entries.map(entry => entry.kind === 'file' ? fileRow(entry, projectId, turn) : folderRow(entry, projectId, turn)));
    const notes = [];
    if (!value.entries.length) notes.push('표시할 파일이 없습니다.');
    if (value.truncated) notes.push(`처음 ${value.entries.length}개만 표시합니다.`);
    if (value.ignoredCount) notes.push(`Git이 무시하는 항목 ${value.ignoredCount}개는 숨겼습니다.`);
    if (value.skippedCount) notes.push(`이름 때문에 다룰 수 없는 항목 ${value.skippedCount}개는 숨겼습니다.`);
    if (notes.length) target.append(node('p', notes.join(' '), 'ws-note'));
  } catch (error) {
    if (generation !== turn) return;
    const code = String(error?.message || error).replace(/^.*Error invoking remote method.*Error: /, '');
    target.replaceChildren(node('p', `폴더를 읽지 못했습니다: ${MESSAGES[code] ?? code}`, 'ws-note'));
  }
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
const actions = ['capture-changes', 'quick-review', 'quick-refactor'].map(id => document.getElementById(id));
async function capture() {
  const projectId = picker.value; if (!projectId) return null;
  const turn = generation;
  const fromChanges = [...list.querySelectorAll('input:checked')].map(input => ({ facet: input.dataset.facet, path: input.dataset.path }));
  // A path picked in both places is sent once, as its change (the diff), not as the whole file.
  const changedPaths = new Set(fromChanges.map(item => item.path));
  const fromTree = [...tree.querySelectorAll('input:checked')].map(input => ({ facet: 'selected', path: input.dataset.path }));
  const items = [...fromChanges, ...fromTree.filter(item => !changedPaths.has(item.path))];
  if (!items.length) { message.textContent = '점검할 파일을 하나 이상 고르세요.'; return null; }
  const body = { projectId, items, includeUntracked: document.getElementById('include-untracked').checked };
  const encoded = JSON.stringify(body);
  if (!pendingCapture || pendingCapture.body !== encoded) pendingCapture = { body: encoded, key: crypto.randomUUID() };
  for (const button of actions) button.disabled = true;
  try {
    const value = await api.captureChanges({ ...body, idempotencyKey: pendingCapture.key });
    if (generation !== turn) return null;
    pendingCapture = null; showSnapshot(value, turn); message.textContent = '선택한 입력을 고정했습니다.';
    return value;
  } catch (error) { if (turn === generation) fail(error); return null; }
  finally { for (const button of actions) button.disabled = false; }
}
document.getElementById('capture-changes').addEventListener('click', () => { void capture(); });
for (const [id, kind] of [['quick-review', 'review'], ['quick-refactor', 'refactor']]) document.getElementById(id).addEventListener('click', async () => {
  const value = await capture();
  if (value) window.dispatchEvent(new CustomEvent('workspace-quick-ai', { detail: { kind, snapshotId: value.snapshotId } }));
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
        if (!snapshot || snapshot.projectId !== projectId) { fail('변경을 먼저 담으세요.'); return; }
        const snapshotId = snapshot.snapshotId; const key = `${snapshotId}:${candidate.candidateId}`;
        if (!planKeys.has(key)) planKeys.set(key, crypto.randomUUID());
        try {
          const plan = await api.planValidation({ projectId, snapshotId, candidateId: candidate.candidateId, idempotencyKey: planKeys.get(key) });
          if (generation === turn && snapshot?.snapshotId === snapshotId) {
            row.append(node('pre', JSON.stringify(plan, null, 2)), node('p', '계획을 저장했습니다. 아래 검증 실행에서 확인 창으로 승인하세요.'));
            window.dispatchEvent(new CustomEvent('workspace-validation-plan', { detail: plan }));
            window.dispatchEvent(new CustomEvent('workspace-validation-candidate', { detail: { projectId, candidateId: candidate.candidateId } }));
          }
        } catch (error) { if (generation === turn) fail(error); }
      });
      row.append(button); detected.append(row);
    }
  } catch (error) { if (generation === turn) fail(error); }
});
