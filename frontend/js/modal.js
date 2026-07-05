
import { isLoggedIn, openLoginModal } from './login.js';
import { parseRepoName, urlInput } from './utils.js';
import { showPage } from './routing.js';
import { setRepoId } from './api.js';

/* ── 잡 상태 모달 ── */
// state: 'running' | 'done' | 'error-rate-limit' | 'error-not-found' | 'error-unreachable'
let _jobState = 'running';

/* 백엔드 API 베이스 URL.
   FastAPI(uvicorn)가 프론트엔드를 함께 서빙하면 same-origin이라 ''로 두면 된다.
   프론트를 별도 서버(예: :5500)로 띄우면 'http://localhost:8000' 등으로 지정. */
const API_BASE = '';

const ERROR_CONFIGS = {
  'error-rate-limit':  { title: '분석 실패 — GitHub API 한도 초과', body: 'GitHub API 요청 한도에 도달했습니다. 잠시 후 재시도해 주세요.', time: '재시도 가능 시각: 약 13분 후 (25:00)', retry: true },
  'error-not-found':   { title: '분석 실패 — 레포지토리를 찾을 수 없음', body: '레포지토리가 존재하지 않거나 비공개 상태입니다. URL을 확인해 주세요.', time: '', retry: false },
  'error-unreachable': { title: '분석 실패 — 서버 연결 오류', body: '분석 서버에 연결할 수 없습니다. 백엔드가 실행 중인지 확인하고 잠시 후 다시 시도해 주세요.', time: '', retry: true },
};

/* 폴링 상태 */
let _pollTimer = null;
let _currentRepoId = null;

export function openJobModal(mode, repoId) {
  _jobState = 'running';
  _stopPolling();
  _currentRepoId = repoId || null;

  const repoName =
    (mode === 'new' && urlInput && urlInput.value) ? parseRepoName(urlInput.value) : '레포지토리';

  // 모달 초기화
  document.getElementById('modal-title').textContent = '분석 진행 중';
  document.getElementById('modal-repo').textContent = repoName;
  document.getElementById('modal-prog-label').textContent = '분석 요청 중...';
  document.getElementById('modal-error-box').style.display = 'none';
  document.getElementById('modal-footer').innerHTML =
    '<button class="btn-secondary" onclick="closeJobModal()">닫기</button>';
  // pipeline 초기화
  const oldDetail = document.getElementById('modal-collect-detail');
  if (oldDetail) oldDetail.remove();
  ['collect', 'identity'].forEach(key => {
    const icon = document.getElementById(`pipe-icon-${key}`);
    const label = document.getElementById(`pipe-label-${key}`);
    const sub = document.getElementById(`pipe-sub-${key}`);
    if (icon) { icon.className = 'pipe-icon wait'; icon.textContent = key === 'collect' ? '1' : '2'; }
    if (label) label.className = 'pipe-label wait';
    if (sub) sub.textContent = '대기 중...';
  });

  document.getElementById('job-modal').classList.add('open');

  if (mode === 'new') {
    _startAnalysis(urlInput.value.trim());
  } else if (mode === 'poll' && repoId) {
    // 홈에서 "진행 상황 보기" → 이미 존재하는 job을 바로 폴링
    document.getElementById('modal-prog-label').textContent = '상태 확인 중...';
    fetch(`${API_BASE}/repositories/${repoId}`).then(r => r.json()).then(d => {
      if (d.url) {
        const name = d.url.replace('https://github.com/', '');
        document.getElementById('modal-repo').textContent = name;
      }
    }).catch(() => {});
    _startPolling(repoId);
  } else {
    _attachSimButtons();
  }
}

/* POST /repositories 로 분석을 시작하고 상태 폴링을 건다 */
async function _startAnalysis(url) {
  let res;
  try {
    res = await fetch(`${API_BASE}/repositories?github_url=${encodeURIComponent(url)}`, {
      method: 'POST',
      credentials: 'include',
    });
  } catch {
    simulateJobState('error-unreachable');
    return;
  }

  if (!res.ok) {
    const cfg = res.status === 404 ? 'error-not-found' : 'error-unreachable';
    simulateJobState(cfg);
    document.getElementById('modal-error-body').textContent = `요청 실패 (HTTP ${res.status})`;
    return;
  }

  let data;
  try { data = await res.json(); } catch { data = {}; }

  if (!data.repository_id) {
    simulateJobState('error-not-found');
    return;
  }

  _currentRepoId = data.repository_id;
  document.getElementById('modal-prog-label').textContent = '분석 대기 중...';
  _startPolling(data.repository_id);
}

/* GET /repositories/{id}/status 를 2초마다 폴링 */
function _startPolling(repoId) {
  _stopPolling();
  _pollTimer = setInterval(async () => {
    let res;
    try {
      res = await fetch(`${API_BASE}/repositories/${repoId}/status`, { credentials: 'include' });
    } catch {
      return; // 일시적 네트워크 오류는 다음 폴링에서 재시도
    }
    if (!res.ok) return;

    let s;
    try { s = await res.json(); } catch { return; }
    _renderStatus(s);
  }, 2000);
}

function _stopPolling() {
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
}

/* status API 응답을 모달 UI에 반영 */
function _renderStatus(s) {
  const detail = s.progress_detail || {};
  const status = s.status;

  if (status === 'done') {
    _stopPolling();
    if (_currentRepoId) setRepoId(_currentRepoId);   // 결과 페이지가 이 repo를 보도록 기억
    simulateJobState('done');
    return;
  }
  if (status === 'failed') {
    _stopPolling();
    simulateJobState('error-unreachable');
    document.getElementById('modal-error-title').textContent = '분석 실패';
    document.getElementById('modal-error-body').textContent =
      s.error_message || '분석 중 오류가 발생했습니다.';
    document.getElementById('modal-error-time').textContent = '';
    return;
  }

  // DB 실제값 우선, fallback은 progress_detail
  const commits = s.commit_count ?? detail.commits_collected ?? 0;
  const prs = s.pr_count ?? detail.prs_collected ?? 0;
  const contTotal = s.contributors_total ?? 0;

  let label = detail.status_message || (status === 'pending' ? '분석 대기 중...' : '데이터 수집 중...');
  if (status === 'rate_limited') label = detail.status_message || 'GitHub API 한도 — 대기 중...';
  document.getElementById('modal-prog-label').textContent = label;

  // pipeline step 1: 데이터 수집
  const collectIcon = document.getElementById('pipe-icon-collect');
  const collectLabel = document.getElementById('pipe-label-collect');
  const collectSub = document.getElementById('pipe-sub-collect');
  if (collectIcon && collectSub) {
    const parts = [];
    if (commits) parts.push(`커밋 ${commits.toLocaleString()}개`);
    if (prs) parts.push(`PR ${prs.toLocaleString()}개`);
    if (contTotal) parts.push(`기여자 ${contTotal.toLocaleString()}명`);
    collectSub.textContent = parts.length ? parts.join(' · ') : '수집 중...';

    // 커밋이 들어오기 시작하면 '진행 중' 표시
    const isDone = status === 'done';
    const isCollecting = commits > 0 || prs > 0;
    collectIcon.className = `pipe-icon${isDone ? ' done' : isCollecting ? ' active' : ''}`;
    collectIcon.textContent = isDone ? '✓' : isCollecting ? '⋯' : '1';
    if (collectLabel) collectLabel.className = `pipe-label${isDone ? ' done' : isCollecting ? ' active' : ' wait'}`;
  }

  // pipeline step 2: 기여자 식별 — 수집 완료 후 시작
  const identIcon = document.getElementById('pipe-icon-identity');
  const identLabel = document.getElementById('pipe-label-identity');
  const identSub = document.getElementById('pipe-sub-identity');
  if (identIcon && identSub) {
    const isResolvingOrDone = status === 'done' || (detail.status_message || '').includes('신원');
    identIcon.className = `pipe-icon${status === 'done' ? ' done' : isResolvingOrDone ? ' active' : ' wait'}`;
    identIcon.textContent = status === 'done' ? '✓' : isResolvingOrDone ? '⋯' : '2';
    if (identLabel) identLabel.className = `pipe-label${status === 'done' ? ' done' : isResolvingOrDone ? ' active' : ' wait'}`;
    identSub.textContent = contTotal ? `기여자 ${contTotal.toLocaleString()}명` : '-';
  }
}

/* 와이어프레임용 시뮬레이션 버튼 (데모 모드에서만) */
function _attachSimButtons() {
  const sim = document.createElement('div');
  sim.dataset.sim = '1';
  sim.style.cssText = 'margin-top:12px;padding-top:10px;border-top:0.5px solid var(--border);font-size:10px;color:var(--text3);';
  sim.innerHTML = `<span style="display:block;margin-bottom:4px;">— 와이어프레임 시뮬레이션 —</span>
    <button class="btn-secondary" style="font-size:10px;padding:3px 8px;margin-right:4px;" onclick="simulateJobState('done')">완료</button>
    <button class="btn-secondary" style="font-size:10px;padding:3px 8px;margin-right:4px;" onclick="simulateJobState('error-rate-limit')">Rate Limit</button>
    <button class="btn-secondary" style="font-size:10px;padding:3px 8px;margin-right:4px;" onclick="simulateJobState('error-not-found')">404</button>
    <button class="btn-secondary" style="font-size:10px;padding:3px 8px;" onclick="simulateJobState('error-unreachable')">502</button>`;
  document.querySelector('#job-modal .modal').appendChild(sim);
}

export function simulateJobState(state) {
  _jobState = state;
  const progLabel = document.getElementById('modal-prog-label');
  const errBox = document.getElementById('modal-error-box');
  const footer = document.getElementById('modal-footer');

  if (state === 'done') {
    progLabel.textContent = '분석 완료';
    document.getElementById('modal-title').textContent = '분석 완료';
    errBox.style.display = 'none';
    footer.innerHTML = `<button class="btn-secondary" onclick="closeJobModal()">닫기</button>
      <button class="btn-primary" onclick="viewResult()">결과 보기 →</button>`;
    return;
  }

  // 에러 상태
  const cfg = ERROR_CONFIGS[state];
  document.getElementById('modal-title').textContent = '분석 실패';
  document.getElementById('modal-error-title').textContent = cfg.title;
  document.getElementById('modal-error-body').textContent = cfg.body;
  document.getElementById('modal-error-time').textContent = cfg.time;
  errBox.style.display = 'block';
  footer.innerHTML = `<button class="btn-secondary" onclick="closeJobModal()">닫기</button>` +
    (cfg.retry ? `<button class="btn-primary" onclick="closeJobModal();openJobModal()">재시도</button>` : '');
}

/* 분석 완료 → 방금 분석한 repo의 타임라인 결과로 이동 */
export function viewResult() {
  closeJobModal();
  window.goPage('timeline', _currentRepoId || undefined);
}

export function closeJobModal() {
  _stopPolling();
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
window.viewResult = viewResult;

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