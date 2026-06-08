
import { isLoggedIn, openLoginModal } from './login.js';
import { parseRepoName, urlInput } from './utils.js';
import { showPage } from './routing.js';

/* ── 잡 상태 모달 ── */
// state: 'running' | 'done' | 'error-rate-limit' | 'error-not-found' | 'error-unreachable'
let _jobState = 'running';

const ERROR_CONFIGS = {
  'error-rate-limit':  { title: '분석 실패 — GitHub API 한도 초과', body: 'GitHub API 요청 한도에 도달했습니다. 잠시 후 재시도해 주세요.', time: '재시도 가능 시각: 약 13분 후 (25:00)', retry: true },
  'error-not-found':   { title: '분석 실패 — 레포지토리를 찾을 수 없음', body: '레포지토리가 존재하지 않거나 비공개 상태입니다. URL을 확인해 주세요.', time: '', retry: false },
  'error-unreachable': { title: '분석 실패 — GitHub 연결 오류', body: 'GitHub 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.', time: '', retry: true },
};

export function openJobModal(mode) {
  _jobState = 'running';
  // 모달 초기화
  document.getElementById('modal-title').textContent = '분석 진행 중';
  document.getElementById('modal-repo').textContent =
    (mode === 'new' && urlInput.value) ? parseRepoName(urlInput.value) : 'tiangolo/fastapi';
  document.getElementById('modal-prog').className = 'prog-fill';
  document.getElementById('modal-prog').style.width = mode === 'new' ? '5%' : '60%';
  document.getElementById('modal-prog-label').textContent = mode === 'new' ? '분석 대기 중...' : '데이터 수집 중...';
  document.getElementById('modal-prog-pct').textContent = mode === 'new' ? '5%' : '60%';
  document.getElementById('modal-error-box').style.display = 'none';
  // footer
  document.getElementById('modal-footer').innerHTML =
    '<button class="btn-secondary" onclick="closeJobModal()">닫기</button>';

  // 에러 시뮬레이션 버튼 (와이어프레임용)
  const sim = document.createElement('div');
  sim.dataset.sim = '1';
  sim.style.cssText = 'margin-top:12px;padding-top:10px;border-top:0.5px solid var(--border);font-size:10px;color:var(--text3);';
  sim.innerHTML = `<span style="display:block;margin-bottom:4px;">— 와이어프레임 시뮬레이션 —</span>
    <button class="btn-secondary" style="font-size:10px;padding:3px 8px;margin-right:4px;" onclick="simulateJobState('done')">완료</button>
    <button class="btn-secondary" style="font-size:10px;padding:3px 8px;margin-right:4px;" onclick="simulateJobState('error-rate-limit')">Rate Limit</button>
    <button class="btn-secondary" style="font-size:10px;padding:3px 8px;margin-right:4px;" onclick="simulateJobState('error-not-found')">404</button>
    <button class="btn-secondary" style="font-size:10px;padding:3px 8px;" onclick="simulateJobState('error-unreachable')">502</button>`;
  document.querySelector('#job-modal .modal').appendChild(sim);

  document.getElementById('job-modal').classList.add('open');
}

export function simulateJobState(state) {
  _jobState = state;
  const prog = document.getElementById('modal-prog');
  const progLabel = document.getElementById('modal-prog-label');
  const progPct = document.getElementById('modal-prog-pct');
  const errBox = document.getElementById('modal-error-box');
  const footer = document.getElementById('modal-footer');

  if (state === 'done') {
    prog.className = 'prog-fill done-fill';
    prog.style.width = '100%';
    progLabel.textContent = '분석 완료';
    progPct.textContent = '100%';
    document.getElementById('modal-title').textContent = '분석 완료';
    errBox.style.display = 'none';
    footer.innerHTML = `<button class="btn-secondary" onclick="closeJobModal()">닫기</button>
      <button class="btn-primary" onclick="closeJobModal();goPage('timeline')">결과 보기 →</button>`;
    return;
  }

  // 에러 상태
  prog.className = 'prog-fill fail-fill';
  const cfg = ERROR_CONFIGS[state];
  document.getElementById('modal-title').textContent = '분석 실패';
  document.getElementById('modal-error-title').textContent = cfg.title;
  document.getElementById('modal-error-body').textContent = cfg.body;
  document.getElementById('modal-error-time').textContent = cfg.time;
  errBox.style.display = 'block';
  footer.innerHTML = `<button class="btn-secondary" onclick="closeJobModal()">닫기</button>` +
    (cfg.retry ? `<button class="btn-primary" onclick="closeJobModal();openJobModal()">재시도</button>` : '');
}

export function closeJobModal() {
  document.getElementById('job-modal').classList.remove('open');
  // 시뮬레이션 div 제거 (열 때마다 appendChild로 추가되므로 닫을 때 전부 제거)
  document.querySelectorAll('#job-modal .modal > div[data-sim]').forEach(el => el.remove());
}

/* ── 재분석 토스트 ── */
export function openReanalyzeToast() {
  if (!isLoggedIn) { openLoginModal(); return; }
  document.getElementById('reanalyze-toast').classList.add('open');
}
export function closeToast() {
  document.getElementById('reanalyze-toast').classList.remove('open');
}
export function startReanalyze() {
  closeToast();
  // 재분석 시작 → 헤더 상태 변경 시뮬레이션
  document.querySelectorAll('#btn-reanalyze').forEach(b => {
    b.disabled = true;
    b.textContent = '분석 중...';
  });
  // 진행 모달은 이미 openJobModal()로 띄울 수 있지만 헤더 버튼 피드백만 보여줌
  setTimeout(() => {
    document.querySelectorAll('#btn-reanalyze').forEach(b => {
      b.disabled = false;
      b.textContent = '재분석';
    });
  }, 3000);
}

/* ── 내 프로젝트 제거 모달 ── */
let _removeRepoTarget = null;
export function openRemoveRepoModal(name, cardId) {
  _removeRepoTarget = cardId;
  document.getElementById('remove-repo-name').textContent = name;
  document.getElementById('remove-repo-modal').classList.add('open');
}
export function closeRemoveRepoModal() {
  document.getElementById('remove-repo-modal').classList.remove('open');
  _removeRepoTarget = null;
}
export function confirmRemoveRepo() {
  if (_removeRepoTarget) {
    const card = document.getElementById(_removeRepoTarget);
    if (card) {
      card.style.transition = 'opacity 0.2s';
      card.style.opacity = '0';
      setTimeout(() => card.remove(), 200);
    }
  }
  closeRemoveRepoModal();
}

/* ── 고립 기여자 패널 ── */
export function openIsolatedPanel() {
  document.getElementById('isolated-modal').classList.add('open');
}
export function closeIsolatedPanel() {
  document.getElementById('isolated-modal').classList.remove('open');
}

/* ── 노이즈 필터 팝오버 ── */
export function toggleNoisePopover(e) {
  e.stopPropagation();
  document.getElementById('noise-popover').classList.toggle('open');
}
export function closeNoisePopover() {

  const popover =
    document.getElementById('noise-popover');

  if (!popover) return;

  popover.classList.remove('open');
}
export function saveNoiseFilter() {
  if (!isLoggedIn) { closeNoisePopover(); openLoginModal(); return; }
  const val = document.getElementById('noise-input').value;
  closeNoisePopover();
  // 메트릭 카드 텍스트 갱신
  document.querySelectorAll('[onclick*="toggleNoisePopover"]').forEach(el => {
    if (el.tagName !== 'SPAN') return;
    el.textContent = `파일 ${val}개 초과 기준 ✎`;
  });
  // 재분석 토스트 대신 간단한 인라인 피드백
  openReanalyzeToast();
}

// 외부 클릭 시 팝오버 닫기
document.addEventListener('click', () => closeNoisePopover());


window.openJobModal = openJobModal;
window.closeJobModal = closeJobModal;
window.simulateJobState = simulateJobState;

window.openReanalyzeToast = openReanalyzeToast;
window.closeToast = closeToast;
window.startReanalyze = startReanalyze;

window.openRemoveRepoModal = openRemoveRepoModal;
window.closeRemoveRepoModal = closeRemoveRepoModal;
window.confirmRemoveRepo = confirmRemoveRepo;

window.openIsolatedPanel = openIsolatedPanel;
window.closeIsolatedPanel = closeIsolatedPanel;

window.toggleNoisePopover = toggleNoisePopover;
window.closeNoisePopover = closeNoisePopover;
window.saveNoiseFilter = saveNoiseFilter;