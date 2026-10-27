'use strict';
const status = document.getElementById('status');
const refresh = document.getElementById('refresh');
async function update() {
  refresh.disabled = true;
  try {
    const health = await window.workspace.getHealth();
    if (health.state === 'ready') status.textContent = '로컬 워크스페이스 기반이 준비되었습니다.';
    else if (['RECOVERY_REQUIRED', 'UNSUPPORTED_PROTOCOL'].includes(health.errorCode)) status.textContent = '저장된 상태를 확인하거나 복구해야 합니다. 기존 자료는 보존되어 있습니다.';
    else status.textContent = '로컬 구성을 준비할 수 없습니다. 저장 자료와 실행 환경을 확인해 주세요.';
  } catch { status.textContent = '상태를 확인할 수 없습니다.'; }
  finally { refresh.disabled = false; }
}
refresh.addEventListener('click', update);
void update();
