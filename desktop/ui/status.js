'use strict';
const status = document.getElementById('status');
const refresh = document.getElementById('refresh');
// The window loads before the agent has started, so the first health answer is "unavailable" even on a healthy start.
// Re-check for a short while before reporting a problem; a manual refresh gets a single, immediate answer.
const POLL_MS = 500;
const POLL_LIMIT = 30;
let attempts = 0;
let timer;
async function update(automatic = false) {
  window.clearTimeout(timer);
  refresh.disabled = true;
  try {
    const health = await window.workspace.getHealth();
    if (health.state === 'ready') status.textContent = '로컬 워크스페이스 기반이 준비되었습니다.';
    else if (['RECOVERY_REQUIRED', 'UNSUPPORTED_PROTOCOL'].includes(health.errorCode)) status.textContent = '저장된 상태를 확인하거나 복구해야 합니다. 기존 자료는 보존되어 있습니다.';
    else if (automatic && !health.errorCode && attempts++ < POLL_LIMIT) {
      status.textContent = '로컬 구성을 확인하는 중입니다.';
      timer = window.setTimeout(() => void update(true), POLL_MS);
    } else status.textContent = '로컬 구성을 준비할 수 없습니다. 저장 자료와 실행 환경을 확인해 주세요.';
  } catch { status.textContent = '상태를 확인할 수 없습니다.'; }
  finally { refresh.disabled = false; }
}
refresh.addEventListener('click', () => void update());
void update(true);
