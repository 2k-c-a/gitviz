import { request } from './platform/transport.js';

import { isLoggedIn, openLoginModal } from './login.js';
import { parseRepoName, urlInput, fmtDuration } from './utils.js';
import { showPage } from './routing.js';
import { getRepoId, setRepoId, fetchSnapshots } from './api.js';
import { createStatusPoller } from './poll.js';

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

/* 폴링 상태 — 요청 스케줄링·재시도·중단은 poll.js(createStatusPoller)가 맡는다 */
let _poller = null;
let _pollRepoId = null;
let _currentRepoId = null;
let _currentRepoUrl = null;
let _currentSnapshotId = null;   // 완료된 job이 만든 저장 결과 — 결과 보기에서 이 ID를 명시한다
let _staleHandled = false;
let _lastOpen = { mode: undefined, repoId: undefined };   // 재시도에서 같은 방식으로 다시 열기 위해
let _failedFor = null;           // 실패 상태로 표시 중인 repoId (마지막 성공 결과 조회 응답이 늦게 올 때 확인용)

/* 파이프라인 4단계 — 순서가 곧 진행 순서(백엔드 progress_detail.stage와 대응).
   app/tasks/analyze_repository.py가 각 단계 진입 시 stage를 이 값으로 갱신한다. */
const PIPE_STEPS = [
  { key: 'collect',  num: '1', defaultSub: '대기 중...' },
  { key: 'identity', num: '2', defaultSub: '-' },
  { key: 'derived',  num: '3', defaultSub: '점검 우선순위 · 주요 수정자 수 · 협업 중심성' },
  { key: 'snapshot', num: '4', defaultSub: '분석 결과 스냅샷 저장' },
];
const PIPE_STAGE_ORDER = [...PIPE_STEPS.map(p => p.key), 'done'];

/* 파이프라인 한 단계의 아이콘/라벨/서브텍스트를 state('wait'|'active'|'done')로 갱신 */
function _setPipeState(key, state, subText) {
  const step = PIPE_STEPS.find(p => p.key === key);
  const icon = document.getElementById(`pipe-icon-${key}`);
  const label = document.getElementById(`pipe-label-${key}`);
  const sub = document.getElementById(`pipe-sub-${key}`);
  if (icon) {
    icon.className = `pipe-icon ${state}`;
    icon.textContent = state === 'done' ? '✓' : state === 'active' ? '⋯' : (step ? step.num : '');
  }
  if (label) label.className = `pipe-label ${state}`;
  if (sub && subText !== undefined) sub.textContent = subText;
}

/* status가 'done'으로 확인된 시점 — 폴링 타이밍상 중간 단계를 못 봤더라도
   4단계를 전부 완료 상태로 확정 렌더링한다(그래야 일부만 불 켜진 채로
   바로 "분석 완료"로 건너뛰는 것처럼 보이지 않는다). */
function _markPipelineAllDone(s) {
  const commits = s.commit_count ?? 0;
  const prs = s.pr_count ?? 0;
  const contTotal = s.contributors_total ?? 0;
  const parts = [];
  // 이 숫자는 수집한 기본 브랜치의 누적 커밋 수다. 결과 화면의 '관측 구간 커밋'(기본 최근 180일)과 다르다.
  if (commits) parts.push(`수집 커밋 ${commits.toLocaleString()}개`);
  if (prs) parts.push(`PR ${prs.toLocaleString()}개`);
  if (contTotal) parts.push(`기여자 ${contTotal.toLocaleString()}명`);

  _setPipeState('collect', 'done', parts.length ? parts.join(' · ') : '완료');
  _setPipeState('identity', 'done', contTotal ? `기여자 ${contTotal.toLocaleString()}명` : '완료');
  _setPipeState('derived', 'done', '점검 우선순위 · 주요 수정자 수 · 협업 중심성');
  _setPipeState('snapshot', 'done', '분석 결과 스냅샷 저장');
}

export function openJobModal(mode, repoId) {
  _jobState = 'running';
  _stopPolling();
  _lastOpen = { mode, repoId };
  _currentRepoId = repoId || null;
  _currentSnapshotId = null;
  _failedFor = null;
  _staleHandled = false;
  _pollRepoId = null;

  const repoName =
    (mode === 'new' && urlInput && urlInput.value) ? parseRepoName(urlInput.value) : '레포지토리';

  // 모달 초기화
  document.getElementById('modal-title').textContent = '분석 진행 중';
  document.getElementById('modal-repo').textContent = repoName;
  document.getElementById('modal-prog-label').textContent = '분석 요청 중...';
  document.getElementById('modal-error-box').style.display = 'none';
  document.getElementById('modal-footer').innerHTML =
    '<button class="btn-secondary" data-action="closeJobModal">닫기</button>';
  // pipeline 초기화
  const oldDetail = document.getElementById('modal-collect-detail');
  if (oldDetail) oldDetail.remove();
  PIPE_STEPS.forEach(({ key, defaultSub }) => _setPipeState(key, 'wait', defaultSub));

  document.getElementById('job-modal').classList.add('open');

  if (mode === 'new') {
    _startAnalysis(urlInput.value.trim());
  } else if (mode === 'reanalyze' && repoId) {
    // 헤더/카드의 "재분석" → 기존 repo에 대해 증분 재분석을 큐잉하고 바로 폴링
    document.getElementById('modal-prog-label').textContent = '재분석 요청 중...';
    _fillRepoName(repoId);
    _startReanalysis(repoId);
  } else if (mode === 'poll' && repoId) {
    // 홈에서 "진행 상황 보기" → 이미 존재하는 job을 바로 폴링
    _staleHandled = false;
    document.getElementById('modal-prog-label').textContent = '상태 확인 중...';
    _fillRepoName(repoId);
    _startPolling(repoId);
  } else {
    _attachSimButtons();
  }
}

/* 모달 상단에 실제 repo 이름을 채운다 (reanalyze/poll 모드는 URL 입력값이 없어서 별도 조회 필요) */
function _fillRepoName(repoId) {
  request(`${API_BASE}/repositories/${repoId}`).then(r => r.json()).then(d => {
    if (d.url) {
      _currentRepoUrl = d.url;
      document.getElementById('modal-repo').textContent = d.url.replace('https://github.com/', '');
    }
  }).catch(() => {});
}

/* ── 오류 표시 공통 ──
   원인(로그인 만료 / 접근 불가 / 요청 충돌 / 서버 지연 …)을 구분해 보여준다.
   서버 연결 오류로 뭉뚱그리지 않는다. */
const _btn = (label, action, primary = false, args = []) =>
  `<button class="${primary ? 'btn-primary' : 'btn-secondary'}" data-action="${action}" data-args="${encodeURIComponent(JSON.stringify(args))}">${label}</button>`;

function _showError({ heading, title, body, time = '', label, actions = [] }) {
  _jobState = 'error';
  document.getElementById('modal-title').textContent = heading;
  if (label) document.getElementById('modal-prog-label').textContent = label;
  document.getElementById('modal-error-title').textContent = title;
  document.getElementById('modal-error-body').textContent = body;
  document.getElementById('modal-error-time').textContent = time;
  document.getElementById('modal-error-box').style.display = 'block';
  document.getElementById('modal-footer').innerHTML =
    _btn('닫기', 'closeJobModal') + actions.map(([l, fn, primary]) => _btn(l, fn, primary)).join('');
}

/* 시작 요청(POST) 실패를 HTTP 상태별로 안내한다. 백엔드 detail은 사용자에게 보여줄 한글 문장이다. */
async function _showStartFailure(res) {
  let detail = null;
  try { const b = await res.json(); if (typeof b?.detail === 'string') detail = b.detail; } catch { /* 본문 없음 */ }
  const retry = ['다시 시도', 'retryJob', true];
  const status = res.status;

  if (status === 401) {
    return _showError({ heading: '로그인이 필요합니다', title: '분석을 요청하려면 로그인해 주세요', body: detail || '로그인이 만료되었거나 로그인하지 않은 상태입니다.',
      label: '요청하지 못했습니다', actions: [['다시 로그인', 'reloginFromJob', true]] });
  }
  if (status === 404) {
    return _showError({ heading: '분석 요청 실패', title: '레포지토리를 찾을 수 없거나 접근할 수 없습니다', body: 'URL을 확인해 주세요. 존재하지 않거나 접근 권한이 없는 저장소는 구분해서 안내하지 않습니다.',
      label: '요청하지 못했습니다' });
  }
  if (status === 409) {
    // 실행 중인 작업과 충돌 — 네트워크 오류가 아니다. 이미 진행 중이라면 그 진행 상황을 볼 수 있다.
    const actions = _currentRepoId ? [['진행 상황 보기', 'resumePolling', true]] : [];
    return _showError({ heading: '이미 진행 중인 분석이 있습니다', title: '실행 중인 분석 작업과 충돌했습니다',
      body: detail || '같은 저장소의 분석이 아직 실행 중입니다. 끝난 뒤 다시 요청해 주세요.', label: '요청이 거부되었습니다', actions });
  }
  if (status === 422) {
    return _showError({ heading: '분석 요청 실패', title: '요청을 처리할 수 없습니다', body: detail || '입력한 값을 확인해 주세요.', label: '요청하지 못했습니다' });
  }
  if (status === 429) {
    return _showError({ heading: '잠시 후 다시 시도해 주세요', title: 'GitHub 확인 한도에 도달했습니다', body: detail || 'GitHub API 요청 한도에 도달했습니다.',
      label: '요청이 제한되었습니다', actions: [retry] });
  }
  return _showError({ heading: '분석 요청 실패', title: '서버가 요청을 처리하지 못했습니다', body: detail || `요청 실패 (HTTP ${status}). 잠시 후 다시 시도해 주세요.`,
    label: '요청하지 못했습니다', actions: [retry] });
}

/* POST 요청 후 응답의 repository_id로 상태 폴링을 건다 (신규 분석/재분석 공용) */
async function _postAndStartPolling(path) {
  let res;
  try {
    res = await request(`${API_BASE}${path}`, {
      method: 'POST',
      credentials: 'include',
    });
  } catch {
    _showError({ heading: '서버에 연결할 수 없습니다', title: '분석 서버에 연결할 수 없습니다', body: '네트워크 상태와 백엔드 실행 여부를 확인한 뒤 다시 시도해 주세요.',
      label: '요청하지 못했습니다', actions: [['다시 시도', 'retryJob', true]] });
    return;
  }

  if (!res.ok) { await _showStartFailure(res); return; }

  let data;
  try { data = await res.json(); } catch { data = {}; }

  if (!data.repository_id) {
    _showError({ heading: '분석 요청 실패', title: '응답을 해석할 수 없습니다', body: '서버 응답에 저장소 정보가 없습니다. 잠시 후 다시 시도해 주세요.',
      label: '요청하지 못했습니다', actions: [['다시 시도', 'retryJob', true]] });
    return;
  }

  _currentRepoId = data.repository_id;
  document.getElementById('modal-prog-label').textContent = '분석 대기 중...';
  _notifyReposChanged();
  _startPolling(data.repository_id);
}

/* POST /repositories 로 신규 분석을 시작 */
function _startAnalysis(url) {
  return _postAndStartPolling(`/repositories?github_url=${encodeURIComponent(url)}`);
}

/* POST /repositories/{id}/reanalyze 로 기존 repo의 증분 재분석을 시작 */
function _startReanalysis(repoId) {
  return _postAndStartPolling(`/repositories/${repoId}/reanalyze`);
}

/* GET /repositories/{id}/status 폴링.
   요청은 직렬(이전 요청이 끝나야 다음 예약)이고, 상태가 안 바뀌면 간격을 늘리며(2초→최대 10초),
   401/404/429/5xx/네트워크 오류는 각각 따로 처리한다 — 무한 대기 없음. 상세는 poll.js. */
function _startPolling(repoId) {
  _stopPolling();
  _pollRepoId = repoId;
  _poller = createStatusPoller({
    url: `${API_BASE}/repositories/${repoId}/status`,
    onUpdate: _renderStatus,
    onNotice: _showRetryNotice,
    onStop: _onPollStop,
  });
  _poller.start();
}

function _stopPolling() {
  if (_poller) { _poller.stop(); _poller = null; }
  _clearRetryNotice();
}

/* 사용자가 "다시 확인"을 눌렀을 때 — 오류 상자를 닫고 폴링을 처음부터 다시 시작 */
function resumePolling() {
  if (!_pollRepoId && !_currentRepoId) { closeJobModal(); return; }
  const repoId = _pollRepoId || _currentRepoId;
  document.getElementById('modal-error-box').style.display = 'none';
  document.getElementById('modal-title').textContent = '분석 진행 중';
  document.getElementById('modal-prog-label').textContent = '상태 확인 중...';
  document.getElementById('modal-footer').innerHTML = _btn('닫기', 'closeJobModal');
  _failedFor = null;
  _jobState = 'running';
  _startPolling(repoId);
}

/* 재시도 대기 안내: 언제 다시 확인하는지 1초 단위로 보여준다 */
let _noticeTimer = null;
function _clearRetryNotice() {
  if (_noticeTimer) { clearInterval(_noticeTimer); _noticeTimer = null; }
}
function _showRetryNotice(n) {
  _clearRetryNotice();
  const label = document.getElementById('modal-prog-label');
  if (!label) return;
  const head = n.kind === 'rate_limit'
    ? (n.retryAfterHeader ? '서버가 잠시 후 다시 확인하라고 안내했습니다' : '요청이 많아 잠시 대기합니다')
    : '서버 응답이 지연되고 있습니다';
  const render = () => {
    const secs = Math.max(0, Math.ceil((n.retryAt - Date.now()) / 1000));
    label.textContent = secs > 0
      ? `${head} — ${secs}초 후 다시 확인합니다 (${n.attempt}/${n.max})`
      : `${head} — 다시 확인 중... (${n.attempt}/${n.max})`;
  };
  render();
  _noticeTimer = setInterval(render, 1000);
}

/* 폴링이 더 이상 진행되지 않을 때 — 원인별 안내. 조회를 못 한 것과 분석이 실패한 것은 다르다. */
function _onPollStop({ reason, status, message }) {
  _clearRetryNotice();
  const again = ['다시 확인', 'resumePolling', true];
  if (reason === 'auth') {
    return _showError({ heading: '로그인이 필요합니다', title: '로그인이 만료되었습니다', label: '진행 상황을 확인하지 못했습니다',
      body: '다시 로그인한 뒤 진행 상황을 확인해 주세요. 분석 작업은 계속 진행 중일 수 있습니다.', actions: [['다시 로그인', 'reloginFromJob', true]] });
  }
  if (reason === 'not_found') {
    // 비공개 저장소 존재 여부를 드러내지 않도록 "삭제됨/권한 없음"을 구분하지 않는다
    return _showError({ heading: '접근할 수 없습니다', title: '분석 결과에 접근할 수 없습니다', label: '진행 상황을 확인하지 못했습니다',
      body: '저장소가 삭제되었거나 접근 권한이 없습니다.' });
  }
  if (reason === 'rate_limit') {
    return _showError({ heading: '잠시 후 다시 확인해 주세요', title: '요청이 계속 제한되고 있습니다', label: '진행 상황을 확인하지 못했습니다',
      body: message || '조회 요청이 계속 제한되어 자동 확인을 멈췄습니다. 분석 작업은 계속 진행 중일 수 있습니다.', actions: [again] });
  }
  if (reason === 'unreachable') {
    return _showError({ heading: '진행 상황을 확인할 수 없습니다', title: '서버 응답이 없습니다', label: '진행 상황을 확인하지 못했습니다',
      body: `자동 재시도를 모두 사용했습니다${status ? ` (마지막 응답 HTTP ${status})` : ''}. 분석 작업 자체는 계속 진행 중일 수 있으니 잠시 후 다시 확인해 주세요.`, actions: [again] });
  }
  if (reason === 'no_job') {
    return _showError({ heading: '분석 기록이 없습니다', title: '분석 작업을 찾을 수 없습니다', label: '진행 상황을 확인하지 못했습니다',
      body: '이 저장소의 분석 작업 기록이 없습니다. 새로 분석을 요청해 주세요.' });
  }
  return _showError({ heading: '진행 상황을 확인할 수 없습니다', title: `상태 조회 실패${status ? ` (HTTP ${status})` : ''}`, label: '진행 상황을 확인하지 못했습니다',
    body: message || '서버가 상태 조회 요청을 받아들이지 않았습니다.', actions: [again] });
}

/* 실패한 재분석이라도 이전에 성공한 저장 결과가 있으면 그 결과를 열 수 있게 한다 */
async function _offerLastSuccess(repoId) {
  _failedFor = repoId;
  try {
    const r = await fetchSnapshots(repoId, { limit: 1 });
    const last = r.snapshots && r.snapshots[0];
    if (!last || _failedFor !== repoId || !document.getElementById('job-modal').classList.contains('open')) return;
    const footer = document.getElementById('modal-footer');
    footer.insertAdjacentHTML('beforeend', _btn(`마지막 성공 결과 보기 (#${last.id})`, 'viewLastSuccess', false, [last.id]));
  } catch { /* 이력 조회 실패는 조용히 무시 — 실패 안내가 우선 */ }
}

/* 홈 화면의 "최근 분석 요청된 저장소" 목록은 주기적으로 다시 불러오지 않으므로,
   분석이 시작·종료될 때 이 이벤트로 한 번만 갱신을 요청한다(main.js가 수신). */
const _notifyReposChanged = () => window.dispatchEvent(new CustomEvent('gitviz:repos-changed'));

/* status API 응답을 모달 UI에 반영. 'stop'을 돌려주면 폴링을 끝낸다. */
function _renderStatus(s) {
  const detail = s.progress_detail || {};
  const status = s.status;
  _clearRetryNotice();

  if (status === 'done') {
    if (_currentRepoId) setRepoId(_currentRepoId);
    _currentSnapshotId = s.snapshot_id ?? null;
    _markPipelineAllDone(s);
    simulateJobState('done');
    _notifyReposChanged();
    return 'stop';
  }
  if (status === 'failed') {
    _showError({ heading: '분석 실패', title: '분석 실패', label: '분석 실패',
      body: s.error_message || '분석 중 오류가 발생했습니다.', actions: [['다시 시도', 'retryJob', true]] });
    if (_currentRepoId) _offerLastSuccess(_currentRepoId);
    _notifyReposChanged();
    return 'stop';
  }
  if (status === 'cancelled') {
    // 서버가 멈춘 작업을 새 분석 요청으로 안전하게 교체한 상태 — 실패가 아니다
    _showError({ heading: '이전 분석이 교체되었습니다', title: '새 분석 요청으로 교체되었습니다', label: '이 작업은 교체되었습니다',
      body: s.error_message || '응답이 없던 분석 작업이 새 분석 요청으로 교체되었습니다. 새 작업의 진행 상황을 확인하세요.',
      actions: [['새 작업 확인', 'resumePolling', true]] });
    return 'stop';
  }

  // DB 실제값 우선, fallback은 progress_detail
  const commits = s.commit_count ?? detail.commits_collected ?? 0;
  const prs = s.pr_count ?? detail.prs_collected ?? 0;
  const contTotal = s.contributors_total ?? 0;

  // A progress message alone does not confirm that the saved result is available.
  // Wait for status=done (and the result ID) before showing completion/actions.
  const confirmingResult = detail.stage === 'done';
  document.getElementById('modal-title').textContent = confirmingResult ? '분석 결과 확인 중' : '분석 진행 중';
  let label = confirmingResult ? '저장된 분석 결과 확인 중...'
    : detail.status_message || (status === 'pending' ? '분석 대기 중...' : '데이터 수집 중...');
  if (status === 'rate_limited') {
    // remaining_seconds는 서버가 매 폴링(2초)마다 절대 시각(resume_at) 기준으로
    // 다시 계산해서 주는 값이라 — 메시지를 한 번 박아두는 게 아니라 실제로 줄어든다.
    label = s.remaining_seconds != null
      ? `GitHub API 한도 — ${fmtDuration(s.remaining_seconds)} 후 재개`
      : (detail.status_message || 'GitHub API 한도 — 대기 중...');
  }
  document.getElementById('modal-prog-label').textContent = label;

  if (_renderStaleWarning(s)) return 'stop';

  // pipeline: progress_detail.stage(collect/identity/derived/snapshot)를 기준으로
  // 4단계 각각의 상태(wait/active/done)를 결정한다. 백엔드가 각 단계 진입 시점마다
  // stage를 갱신하므로(app/tasks/analyze_repository.py) 문자열 메시지 추측 없이
  // 정확히 몇 단계까지 끝났는지 알 수 있다.
  const curStageIdx = Math.max(0, PIPE_STAGE_ORDER.indexOf(detail.stage || 'collect'));
  const collectParts = [];
  // 수집한 기본 브랜치의 누적 커밋 수 — 결과 화면의 '관측 구간 커밋'(분석 구간 안의 커밋)과 다르다
  if (commits) collectParts.push(`수집 커밋 ${commits.toLocaleString()}개`);
  if (prs) collectParts.push(`PR ${prs.toLocaleString()}개`);
  if (contTotal) collectParts.push(`기여자 ${contTotal.toLocaleString()}명`);
  const subByKey = {
    collect: collectParts.length ? collectParts.join(' · ') : '수집 중...',
    identity: contTotal ? `기여자 ${contTotal.toLocaleString()}명` : '-',
    derived: '점검 우선순위 · 주요 수정자 수 · 협업 중심성',
    snapshot: '분석 결과 스냅샷 저장',
  };
  PIPE_STEPS.forEach(({ key }, idx) => {
    const state = idx < curStageIdx ? 'done' : idx === curStageIdx ? 'active' : 'wait';
    _setPipeState(key, state, subByKey[key]);
  });
}

/* resume_at을 한참 지났는데도 rate_limited/running이면 워커가 멈췄을 가능성이 있다 —
   정상 대기처럼 계속 폴링만 하지 말고 사용자에게 재시작을 안내한다.
   is_stale은 시간 기준의 '복구 후보'일 뿐이라, 워커가 실제로 살아 있으면 재시작이 409로 거부될 수 있다. */
function _renderStaleWarning(s) {
  if (!s.is_stale || _staleHandled) return false;
  _staleHandled = true;
  _stopPolling();

  document.getElementById('modal-title').textContent = '분석이 멈춘 것 같습니다';
  document.getElementById('modal-error-title').textContent = '진행이 멈췄습니다';
  document.getElementById('modal-error-body').textContent =
    '예상 시간이 지났는데도 상태가 바뀌지 않았습니다. 수집 작업이 멈췄을 수 있습니다. 지금까지 수집된 데이터는 남아 있으니 재시작하면 이어서 진행합니다. 다만 작업이 실제로 실행 중이면 재시작이 거부될 수 있습니다.';
  document.getElementById('modal-error-time').textContent =
    s.stale_seconds != null ? `${fmtDuration(s.stale_seconds)}째 응답 없음` : '';
  document.getElementById('modal-error-box').style.display = 'block';
  document.getElementById('modal-footer').innerHTML =
    _btn('닫기', 'closeJobModal') + _btn('재시작', '_restartStaleJob', true);
  return true;
}

/* 멈춘 작업 재시작: 동일 URL로 다시 POST → 이미 수집된 커밋/PR은 upsert로 스킵되고
   중단된 지점(주로 PR 상세 수집)부터 이어서 진행된다. */
async function _restartStaleJob() {
  if (!_currentRepoUrl) { closeJobModal(); return; }
  document.getElementById('modal-error-box').style.display = 'none';
  document.getElementById('modal-title').textContent = '분석 진행 중';
  document.getElementById('modal-prog-label').textContent = '재시작 요청 중...';
  document.getElementById('modal-footer').innerHTML = _btn('닫기', 'closeJobModal');

  let res;
  try {
    res = await request(`${API_BASE}/repositories?github_url=${encodeURIComponent(_currentRepoUrl)}`, {
      method: 'POST',
      credentials: 'include',
    });
  } catch {
    _showError({ heading: '서버에 연결할 수 없습니다', title: '재시작 요청을 보내지 못했습니다', body: '네트워크 상태를 확인한 뒤 다시 시도해 주세요.',
      label: '요청하지 못했습니다', actions: [['다시 시도', '_restartStaleJob', true]] });
    return;
  }

  if (res.status === 409) {
    // 서버가 실제 워커를 확인해 재시작을 거부 — 연결 오류가 아니라 "작업이 아직 실행 중"이라는 뜻.
    // 같은 안내가 반복되지 않도록 stale 경고는 다시 띄우지 않고, 진행 상황을 계속 확인한다.
    _staleHandled = true;
    document.getElementById('modal-title').textContent = '분석 진행 중';
    document.getElementById('modal-error-title').textContent = '재시작이 거부되었습니다';
    document.getElementById('modal-error-body').textContent =
      '수집 작업이 아직 실행 중일 수 있어 서버가 재시작을 허용하지 않았습니다. 진행 상황을 계속 확인합니다.';
    document.getElementById('modal-error-time').textContent = '';
    document.getElementById('modal-error-box').style.display = 'block';
    _startPolling(_currentRepoId);
    return;
  }
  if (!res.ok) { await _showStartFailure(res); return; }

  _staleHandled = false;
  _startPolling(_currentRepoId);
}
window._restartStaleJob = _restartStaleJob;

/* 모달 버튼에서 부르는 동작들 */
function retryJob() {
  const { mode, repoId } = _lastOpen;
  closeJobModal();
  openJobModal(mode, repoId);
}
function reloginFromJob() {
  closeJobModal();
  openLoginModal();
}
function viewLastSuccess(snapshotId) {
  const repoId = _failedFor ?? _currentRepoId;
  closeJobModal();
  window.goPage('timeline', repoId || undefined, snapshotId);
}
// 가려져 있던 탭이 다시 보이면 느린 간격을 기다리지 않고 바로 상태를 확인한다
document.addEventListener('visibilitychange', () => { if (!document.hidden && _poller) _poller.nudge(); });

window.retryJob = retryJob;
window.reloginFromJob = reloginFromJob;
window.viewLastSuccess = viewLastSuccess;
window.resumePolling = resumePolling;

/* 와이어프레임용 시뮬레이션 버튼 (데모 모드에서만) */
function _attachSimButtons() {
  const sim = document.createElement('div');
  sim.dataset.sim = '1';
  sim.style.cssText = 'margin-top:12px;padding-top:10px;border-top:0.5px solid var(--border);font-size:10px;color:var(--text3);';
  sim.innerHTML = `<span style="display:block;margin-bottom:4px;">— 와이어프레임 시뮬레이션 —</span>
    <button class="btn-secondary" style="font-size:10px;padding:3px 8px;margin-right:4px;" data-action="simulateJobState" data-args="%5B%22done%22%5D">완료</button>
    <button class="btn-secondary" style="font-size:10px;padding:3px 8px;margin-right:4px;" data-action="simulateJobState" data-args="%5B%22error-rate-limit%22%5D">Rate Limit</button>
    <button class="btn-secondary" style="font-size:10px;padding:3px 8px;margin-right:4px;" data-action="simulateJobState" data-args="%5B%22error-not-found%22%5D">404</button>
    <button class="btn-secondary" style="font-size:10px;padding:3px 8px;" data-action="simulateJobState" data-args="%5B%22error-unreachable%22%5D">502</button>`;
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
    footer.innerHTML = `<button class="btn-secondary" data-action="closeJobModal">닫기</button>
      <button class="btn-primary" data-action="viewResult">결과 보기 →</button>`;
    return;
  }

  // 에러 상태
  const cfg = ERROR_CONFIGS[state];
  document.getElementById('modal-title').textContent = '분석 실패';
  document.getElementById('modal-error-title').textContent = cfg.title;
  document.getElementById('modal-error-body').textContent = cfg.body;
  document.getElementById('modal-error-time').textContent = cfg.time;
  errBox.style.display = 'block';
  footer.innerHTML = `<button class="btn-secondary" data-action="closeJobModal">닫기</button>` +
    (cfg.retry ? `<button class="btn-primary" data-action="retryJob">재시도</button>` : '');
}

/* 분석 완료 → 방금 분석한 repo의 타임라인 결과로 이동.
   완료된 job의 snapshot_id를 명시해, 이전에 열어 둔 오래된 결과가 선택되지 않게 한다. */
export function viewResult() {
  const repoId = _currentRepoId || undefined;
  const snapshotId = _currentSnapshotId || undefined;
  closeJobModal();
  window.goPage('timeline', repoId, snapshotId);
}

export function closeJobModal() {
  _stopPolling();   // 진행 중 요청을 abort하고 예약된 조회를 취소 — 닫힌 모달은 더 이상 폴링하지 않는다
  _failedFor = null;
  document.getElementById('job-modal').classList.remove('open');
  // 시뮬레이션 div 제거 (열 때마다 appendChild로 추가되므로 닫을 때 전부 제거)
  document.querySelectorAll('#job-modal .modal > div[data-sim]').forEach(el => el.remove());
  // 내 프로젝트 페이지에서 재분석/진행확인 모달을 닫으면 카드 상태(뱃지·메트릭)를 새로 반영
  if (typeof window.refreshMyProjects === 'function') window.refreshMyProjects();
}

/* ── 재분석 토스트 ──
   repoId를 안 넘기면(타임라인/네트워크/코드분석 헤더 버튼) 현재 보고 있는 repo(getRepoId())를 쓰고,
   myprojects 카드처럼 특정 repo를 지정해야 하면 openReanalyzeToast(repo.id)로 호출한다. */
let _reanalyzeTargetId = null;
export function openReanalyzeToast(repoId) {
  if (!isLoggedIn) { openLoginModal(); return; }
  _reanalyzeTargetId = (repoId !== undefined && repoId !== null) ? repoId : null;
  document.getElementById('reanalyze-toast').classList.add('open');
}
export function closeToast() {
  document.getElementById('reanalyze-toast').classList.remove('open');
}

/* 재분석 시작 → 진행 모달을 'reanalyze' 모드로 열면 POST + 폴링 + pipeline UI/에러 처리를
   openJobModal/_startReanalysis가 전부 맡는다(신규 분석과 동일한 인프라 재사용). */
export function startReanalyze() {
  const repoId = _reanalyzeTargetId ?? getRepoId();
  closeToast();
  openJobModal('reanalyze', repoId);
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
