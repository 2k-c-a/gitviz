import { renderStatus } from './git-status.js';

const api = window.workspace;
const picker = document.getElementById('project-picker');
const message = document.getElementById('project-message');
const target = document.getElementById('git-state');
const recent = document.getElementById('recent-limit');
const more = document.getElementById('more-projects');
let generation = 0;
let listing = 0;
let nextCursor = null;
let project = null;

function note(error) { const code = String(error?.message ?? error).replace(/^.*Error invoking remote method.*Error: /, ''); message.textContent = code === 'REVIEW_DATA_REQUIRES_EXPLICIT_REMOVAL' ? '리뷰·영향·비용·검증·비교 기록이 남아 있어 등록 해제를 보류합니다. 보존·삭제 정책 확인 후 별도 정리가 필요합니다. 원본 프로젝트는 유지됩니다.' : code; }
function controls(enabled) { for (const id of ['reload-git', 'save-project-settings', 'unregister-project']) document.getElementById(id).disabled = !enabled; }
async function selected() {
  const turn = ++generation;
  const projectId = picker.value;
  window.dispatchEvent(new CustomEvent('workspace-project-changed', { detail: { projectId } }));
  project = null; controls(false); target.replaceChildren(); message.textContent = '';
  if (!projectId) return;
  try {
    const value = await api.getProject({ projectId });
    if (turn !== generation || picker.value !== projectId) return;
    project = value; recent.value = String(value.settings.recentLimit.value); controls(true);
    if (value.accessState !== 'available') { note('PROJECT_UNAVAILABLE: 폴더 위치와 접근 권한을 확인하세요.'); return; }
    const state = await api.getGitStatus({ projectId, recentLimit: value.settings.recentLimit.value });
    if (turn === generation && picker.value === projectId) renderStatus(target, state);
  } catch (error) { if (turn === generation) note(error); }
}
async function load(append = false) {
  const turn = ++listing;
  const selectionTurn = generation;
  const selectedAtStart = picker.value;
  try {
    const page = await api.listProjects({ cursor: append ? nextCursor : null, limit: 50 });
    if (turn !== listing || selectionTurn !== generation) return false;
    if (!append) picker.replaceChildren(new Option('프로젝트를 선택하세요', ''));
    for (const item of page.items) picker.add(new Option(`${item.displayName}${item.accessState === 'available' ? '' : ' (접근 불가)'}`, item.id));
    nextCursor = page.nextCursor; more.hidden = !nextCursor;
    if ([...picker.options].some(option => option.value === selectedAtStart)) picker.value = selectedAtStart;
    if (!append) await selected();
    return true;
  } catch (error) { if (turn === listing) note(error); return false; }
}
if (api?.listProjects) {
  void (async () => {
    for (let attempt = 0; attempt < 30; attempt++) {
      try { if ((await api.getHealth()).state === 'ready') { await load(); return; } } catch { /* A later refresh can retry. */ }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  })();
  picker.addEventListener('change', () => { void selected(); });
  document.getElementById('reload-projects').addEventListener('click', () => { void load(); });
  document.getElementById('reload-git').addEventListener('click', () => { void selected(); });
  more.addEventListener('click', () => { void load(true); });
  document.getElementById('register-project').addEventListener('click', async () => {
    const turn = generation;
    try { const registered = await api.selectAndRegister(); if (registered && turn === generation) { const loaded = await load(); if (!loaded || generation !== turn + 1) return; if (![...picker.options].some(option => option.value === registered.id)) picker.add(new Option(registered.displayName, registered.id)); picker.value = registered.id; await selected(); } }
    catch (error) { note(error); }
  });
  document.getElementById('save-project-settings').addEventListener('click', async () => {
    if (!project) return;
    const turn = generation; const value = project;
    try { await api.updateSettings({ projectId: value.id, recentLimit: Number(recent.value), expectedVersion: value.settings.recentLimit.version, idempotencyKey: crypto.randomUUID() }); if (turn === generation) await selected(); }
    catch (error) { if (turn === generation) note(error); }
  });
  document.getElementById('unregister-project').addEventListener('click', async () => {
    if (!project) return;
    const turn = generation; const value = project;
    try { const result = await api.unregisterProject({ projectId: value.id, expectedVersion: value.version, idempotencyKey: crypto.randomUUID() }); if (result && turn === generation) { picker.value = ''; await load(); } }
    catch (error) { if (turn === generation) note(error); }
  });
} else note('로컬 데스크톱 앱에서 사용할 수 있습니다.');
