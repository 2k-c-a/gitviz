import { request } from './platform/transport.js';
import { esc, num } from './fmt.js';
/* ── 내 분석 프로젝트 페이지: 로그인한 사용자가 직접 분석 요청한 레포 목록 ──
   myproj-list가 있을 때(=내 분석 프로젝트 페이지)만 동작한다. 로그인 여부는
   /api/auth/me 응답으로 직접 판단한다(login.js의 currentUser는 main.js의
   initAuth()가 끝나야 채워지는데, 이 모듈은 그보다 먼저 평가될 수 있어
   전역 상태 타이밍에 기대지 않는 편이 안전하다). */

const listEl = document.getElementById('myproj-list');

const GITHUB_ICON = `<svg width="14" height="14" viewBox="0 0 16 16" fill="var(--text3)"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>`;

function homeUrl() {
  return window.location.pathname.includes('/pages/') ? '../index.html' : './index.html';
}

function fmtRelative(iso) {
  if (!iso) return '';
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return '방금 전';
  if (mins < 60) return `${mins}분 전`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}시간 전`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}일 전`;
  return `${Math.floor(days / 30)}개월 전`;
}

const STATUS_BADGE = {
  done:         ['badge-green', 'var(--green)', '완료'],
  running:      ['badge-blue',  'var(--blue)',  '분석 중'],
  pending:      ['badge-blue',  'var(--blue)',  '대기 중'],
  rate_limited: ['badge-blue',  'var(--blue)',  'Rate Limit 대기'],
  failed:       ['badge-red',   'var(--red)',   '실패'],
  cancelled:    ['badge-gray',  'var(--text3)', '교체됨'],
  unknown:      ['badge-gray',  'var(--text3)', '알 수 없음'],
};

function statusBadge(repo) {
  if (repo.is_stale) {
    return `<span class="badge badge-red"><span class="status-dot" style="background:var(--red);display:inline-block;margin-right:4px;"></span>재시작 필요</span>`;
  }
  const [cls, color, label] = STATUS_BADGE[repo.status] || STATUS_BADGE.unknown;
  return `<span class="badge ${cls}"><span class="status-dot" style="background:${color};display:inline-block;margin-right:4px;"></span>${label}</span>`;
}

function metaLine(repo) {
  const parts = [];
  // 수집한 기본 브랜치의 누적 커밋 수 — 결과 화면의 '최근 180일 커밋'(분석 구간 안)과 다르다
  if (repo.commit_count) parts.push(`수집 커밋 ${num(repo.commit_count)}개`);
  if (repo.contributor_count) parts.push(`기여자 ${num(repo.contributor_count)}명`);
  if (repo.status === 'done') parts.push(`파일 ${num(repo.file_count)}개`);
  else if (!parts.length) parts.push('데이터 수집 중...');
  return parts.join(' · ');
}

function statusCallout(repo) {
  if (repo.status === 'done') {
    if (!repo.file_count) {
      return `<div class="rec-box">아직 파일 데이터가 없습니다.</div>`;
    }
    const bfText = repo.median_bus_factor != null ? `<strong style="margin:0 2px;">${repo.median_bus_factor}</strong>` : '<strong style="margin:0 2px;">—</strong>';
    if (repo.high_risk_count > 0 || repo.bus_factor_1_count > 0) {
      return `<div style="display:flex;align-items:center;gap:6px;padding:7px 10px;background:var(--coral-light);border:0.5px solid var(--coral-mid);border-radius:var(--r-md);font-size:12px;color:var(--coral-dark);">
        점수 표시 임계값(≥0.7) 이상 파일 <strong style="margin:0 2px;">${num(repo.high_risk_count)}개</strong> · 한 명 집중 파일 <strong style="margin:0 2px;">${num(repo.bus_factor_1_count)}개</strong> · 파일별 주요 수정자 수 중앙값 ${bfText}
      </div>`;
    }
    return `<div style="display:flex;align-items:center;gap:6px;padding:7px 10px;background:var(--teal-light);border:0.5px solid var(--teal-mid);border-radius:var(--r-md);font-size:12px;color:var(--teal-dark);">
      한 명 집중 파일 <strong style="margin:0 2px;">없음</strong> · 파일별 주요 수정자 수 중앙값 ${bfText} · 관측 이력 기준이며 실제 지식 분포와 다를 수 있습니다
    </div>`;
  }

  if (['running', 'pending', 'rate_limited'].includes(repo.status)) {
    const msg = (repo.progress_detail && repo.progress_detail.status_message) || '분석 대기 중...';
    return `<div style="display:flex;align-items:center;gap:6px;padding:7px 10px;background:var(--blue-light);border:0.5px solid var(--blue-mid);border-radius:var(--r-md);font-size:12px;color:var(--blue-dark);">
      ⟳ ${esc(msg)}
    </div>`;
  }

  if (repo.status === 'failed') {
    // 재분석이 실패해도 이전에 성공한 저장 결과는 그대로 열 수 있다
    const keep = repo.last_analyzed_at ? ' 마지막으로 성공한 결과는 "마지막 성공 결과"에서 열 수 있습니다.' : '';
    return `<div style="display:flex;align-items:center;gap:6px;padding:7px 10px;background:var(--red-light);border:0.5px solid var(--red-mid);border-radius:var(--r-md);font-size:12px;color:var(--red-dark);">
      ✕ ${esc(repo.error_message || '분석 실패 — 원인을 알 수 없습니다.')}${keep}
    </div>`;
  }

  if (repo.status === 'cancelled') {
    return `<div class="rec-box" style="font-size:12px;">이전 분석을 새 분석 요청으로 바꿨습니다. 새 작업의 진행 상황을 확인하세요.</div>`;
  }

  return '';
}

function actionButtons(repo, repoName, cardId) {
  if (repo.status === 'done') {
    return `
      <button class="btn-secondary" style="font-size:12px;padding:5px 10px;" data-action="goPage" data-args="${encodeURIComponent(JSON.stringify(['overview', repo.id]))}">결과 보기</button>
      <button class="btn-secondary" style="font-size:12px;padding:5px 10px;" data-auth="1" data-action="openReanalyzeToast" data-args="${encodeURIComponent(JSON.stringify([repo.id]))}">재분석</button>
      <button class="btn-secondary" style="font-size:12px;padding:5px 10px;color:var(--red-dark);border-color:var(--red-mid);" data-action="openRemoveRepoModal" data-args="${encodeURIComponent(JSON.stringify([repoName,cardId,repo.id]))}">제거</button>`;
  }
  if (['running', 'pending', 'rate_limited'].includes(repo.status)) {
    return `<button class="btn-secondary" style="font-size:12px;padding:5px 10px;" data-action="openJobModal" data-args="${encodeURIComponent(JSON.stringify(['poll', repo.id]))}">진행 확인</button>`;
  }
  if (repo.status === 'cancelled') {
    return `<button class="btn-secondary" style="font-size:12px;padding:5px 10px;" data-action="openJobModal" data-args="${encodeURIComponent(JSON.stringify(['poll', repo.id]))}">진행 확인</button>`;
  }
  if (repo.status === 'failed') {
    const last = repo.last_analyzed_at
      ? `<button class="btn-secondary" style="font-size:12px;padding:5px 10px;" data-action="goPage" data-args="${encodeURIComponent(JSON.stringify(['overview', repo.id]))}">마지막 성공 결과</button>` : '';
    return `
      ${last}
      <button class="btn-secondary" style="font-size:12px;padding:5px 10px;" data-auth="1" data-action="openReanalyzeToast" data-args="${encodeURIComponent(JSON.stringify([repo.id]))}">재시도</button>
      <button class="btn-secondary" style="font-size:12px;padding:5px 10px;color:var(--red-dark);border-color:var(--red-mid);" data-action="openRemoveRepoModal" data-args="${encodeURIComponent(JSON.stringify([repoName,cardId,repo.id]))}">제거</button>`;
  }
  return `<button class="btn-secondary" style="font-size:12px;padding:5px 10px;color:var(--red-dark);border-color:var(--red-mid);" data-action="openRemoveRepoModal" data-args="${encodeURIComponent(JSON.stringify([repoName,cardId,repo.id]))}">제거</button>`;
}

function buildCard(repo) {
  const repoName = (repo.url || '').replace('https://github.com/', '');
  const cardId = `myproj-card-${repo.id}`;
  const branchBadge = repo.default_branch ? `<span class="badge badge-gray">${esc(repo.default_branch)}</span>` : '';
  const langBadge = repo.language ? `<span class="badge badge-blue">${esc(repo.language)}</span>` : '';
  const lastAt = repo.status === 'done' ? repo.last_analyzed_at : (repo.started_at || repo.my_last_triggered_at);
  const lastLabel = repo.status === 'done' ? '마지막 분석' : (repo.status === 'failed' ? '마지막 시도' : '시작');

  return `
    <div class="panel" id="${cardId}" style="padding:16px 20px;">
      <div style="display:flex;align-items:flex-start;gap:14px;">
        <div style="flex:1;min-width:0;">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px;">
            ${GITHUB_ICON}
            <a href="${esc(repo.url)}" target="_blank" rel="noopener" style="font-size:15px;font-weight:600;color:var(--text);text-decoration:none;" title="GitHub에서 열기">${esc(repoName)}</a>
            ${branchBadge}
            ${langBadge}
          </div>
          <div style="font-size:12px;color:var(--text2);margin-bottom:10px;">${metaLine(repo)}</div>
          ${statusCallout(repo)}
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:10px;flex-shrink:0;">
          ${statusBadge(repo)}
          <div style="font-size:var(--ui-text-xs);color:var(--text3);white-space:nowrap;">${lastAt ? `${lastLabel} ${fmtRelative(lastAt)}` : ''}</div>
          <div style="display:flex;gap:6px;">${actionButtons(repo, repoName, cardId)}</div>
        </div>
      </div>
    </div>`;
}

function renderSummary(repos) {
  const total = repos.length;
  const highRisk = repos.filter(r => r.status === 'done' && (r.high_risk_count > 0 || r.bus_factor_1_count > 0)).length;
  const inProgress = repos.filter(r => ['pending', 'running', 'rate_limited'].includes(r.status)).length;
  const failed = repos.filter(r => r.status === 'failed').length;

  const metricsEl = document.getElementById('myproj-metrics');
  if (!metricsEl) return;
  metricsEl.innerHTML = `
    <div class="metric-card"><div class="m-label">총 분석 프로젝트</div><div class="m-val">${num(total)}</div><div class="m-sub">내 저장소 목록</div></div>
    <div class="metric-card"><div class="m-label">검토 신호가 있는 프로젝트</div><div class="m-val${highRisk ? ' m-dn' : ''}">${num(highRisk)}</div><div class="m-sub">한 명 집중 파일 또는 점수 표시 임계값(≥0.7) 이상 파일 보유</div></div>
    <div class="metric-card"><div class="m-label">분석 중</div><div class="m-val" style="color:var(--blue);">${num(inProgress)}</div><div class="m-sub">작업 진행 중</div></div>
    <div class="metric-card"><div class="m-label">분석 실패</div><div class="m-val${failed ? ' m-dn' : ''}">${num(failed)}</div><div class="m-sub">재시도 필요</div></div>`;
}

function renderList(repos) {
  if (!repos.length) {
    listEl.innerHTML = `
      <div class="panel" style="padding:40px 20px;text-align:center;">
        <div style="font-size:13px;color:var(--text2);margin-bottom:14px;">아직 분석을 요청한 저장소가 없습니다.</div>
        <button class="btn-primary" data-action="goHome">새 저장소 분석하기</button>
      </div>`;
    return;
  }
  listEl.innerHTML = repos.map(buildCard).join('');
}

function loadingState() {
  return `<div class="panel" style="padding:40px 20px;text-align:center;color:var(--text3);font-size:13px;">불러오는 중...</div>`;
}

function errorState(msg) {
  return `<div class="panel" style="padding:40px 20px;text-align:center;color:var(--red-dark);font-size:13px;">${esc(msg)}</div>`;
}

/* ── 내 GitHub 저장소 탭: github.com/repos와 같은 범위(소유·협업자·소속 조직). 처음 열 때 불러온다. ── */
const GITHUB_PER_PAGE = 30;
const githubListEl = document.getElementById('myproj-github-list');
const githubMoreEl = document.getElementById('myproj-github-more');
const github = { page: 0, loaded: false, loading: false, generation: 0 };
// 이미 분석된 저장소의 ID(정규화한 URL → id). 분석한 프로젝트 목록과 최근 공개 분석에서 채운다.
const analyzedIds = new Map();
const normalizeUrl = (url) => String(url || '').trim().replace(/\.git$/i, '').replace(/\/+$/, '').toLowerCase();

async function loadAnalyzedIds() {
  await Promise.allSettled(['/repositories?limit=100', '/repositories/mine'].map(async (path) => {
    const response = await request(path, { credentials: 'include' });
    if (response.ok) for (const repo of await response.json()) analyzedIds.set(normalizeUrl(repo.url), repo.id);
  }));
}

function githubCard(repo) {
  const fullName = `${repo.owner || ''}/${repo.repo_name || ''}`;
  const id = repo.is_registered ? analyzedIds.get(normalizeUrl(repo.url)) : undefined;
  const badges = [
    repo.private ? '<span class="badge badge-gray">비공개</span>' : '',
    repo.fork ? '<span class="badge badge-gray">포크</span>' : '',
    repo.archived ? '<span class="badge badge-gray">보관됨</span>' : '',
    repo.language ? `<span class="badge badge-blue">${esc(repo.language)}</span>` : '',
    repo.is_registered ? '<span class="badge badge-green">이미 분석됨</span>' : '',
  ].join('');
  const pushed = repo.pushed_at ? `<span>최근 push ${fmtRelative(repo.pushed_at)}</span>` : '';
  const action = id
    ? `<button type="button" class="btn-secondary" style="font-size:12px;padding:5px 10px;" data-action="goPage" data-args="${encodeURIComponent(JSON.stringify(['overview', id]))}">결과 보기</button>`
    : '<button type="button" class="btn-primary myproj-github-start" style="font-size:12px;padding:5px 10px;">분석 시작</button>';
  return `
    <div class="panel myproj-github-card" data-url="${esc(repo.url)}" style="padding:14px 20px;">
      <div style="display:flex;align-items:flex-start;gap:14px;">
        <div style="flex:1;min-width:0;">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px;">
            ${GITHUB_ICON}
            <a href="${esc(repo.url)}" target="_blank" rel="noopener" data-action="stop" style="font-size:14px;font-weight:600;color:var(--text);text-decoration:none;" title="GitHub에서 열기">${esc(fullName)}</a>
            ${badges}
          </div>
          <div style="font-size:12px;color:var(--text2);margin-bottom:6px;">${esc(repo.description || '설명이 없습니다.')}</div>
          <div style="display:flex;gap:12px;flex-wrap:wrap;font-size:var(--ui-text-xs);color:var(--text3);">
            <span>★ ${num(repo.stars)}</span><span>포크 ${num(repo.forks)}</span>${pushed}
          </div>
        </div>
        <div style="flex-shrink:0;">${action}</div>
      </div>
    </div>`;
}

const GITHUB_ERRORS = {
  401: 'GitHub 인증이 만료됐습니다. 로그아웃한 뒤 다시 로그인해 주세요.',
  429: 'GitHub API 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.',
};

async function loadGithubPage(reset = false) {
  if (!githubListEl || (github.loading && !reset)) return;
  const generation = reset ? ++github.generation : github.generation;
  if (reset) { github.page = 0; githubListEl.innerHTML = loadingState(); }
  github.loading = true;
  if (githubMoreEl) githubMoreEl.disabled = true;
  const page = github.page + 1;
  try {
    const [response] = await Promise.all([
      request(`/search/my-github-repositories?per_page=${GITHUB_PER_PAGE}&page=${page}`, { credentials: 'include' }),
      reset ? loadAnalyzedIds() : null,
    ]);
    if (generation !== github.generation) return;
    if (!response.ok) {
      const message = GITHUB_ERRORS[response.status] || `저장소 목록을 불러오지 못했습니다 (HTTP ${response.status}).`;
      if (page === 1) githubListEl.innerHTML = errorState(message);
      else githubListEl.insertAdjacentHTML('beforeend', errorState(message));
      if (githubMoreEl) githubMoreEl.hidden = true;
      return;
    }
    const data = await response.json();
    const results = Array.isArray(data.results) ? data.results : [];
    if (page === 1) {
      githubListEl.innerHTML = results.length ? results.map(githubCard).join('')
        : '<div class="panel" style="padding:40px 20px;text-align:center;font-size:13px;color:var(--text2);">접근할 수 있는 GitHub 저장소가 없습니다.</div>';
    } else {
      githubListEl.insertAdjacentHTML('beforeend', results.map(githubCard).join(''));
    }
    github.page = page; github.loaded = true;
    if (githubMoreEl) githubMoreEl.hidden = !data.has_next;
  } catch {
    if (generation !== github.generation) return;
    if (page === 1) githubListEl.innerHTML = errorState('저장소 목록을 불러올 수 없습니다. 잠시 후 다시 시도해 주세요.');
  } finally {
    if (generation === github.generation) {
      github.loading = false;
      if (githubMoreEl) githubMoreEl.disabled = false;
    }
  }
}

function startGithubAnalysis(url) {
  const hidden = document.getElementById('url-input');
  if (!hidden || !url || typeof window.openJobModal !== 'function') return;
  hidden.value = url;
  window.openJobModal('new');
}

const TABS = [['myproj-tab-analyzed', 'myproj-analyzed'], ['myproj-tab-github', 'myproj-github']];

function selectTab(tabId, { focus = false } = {}) {
  for (const [tab, panel] of TABS) {
    const on = tab === tabId;
    const tabEl = document.getElementById(tab);
    const panelEl = document.getElementById(panel);
    if (!tabEl || !panelEl) continue;
    tabEl.classList.toggle('active', on);
    tabEl.setAttribute('aria-selected', String(on));
    tabEl.tabIndex = on ? 0 : -1;
    panelEl.hidden = !on;
    if (on && focus) tabEl.focus();
  }
  const url = new URL(location.href);
  if (tabId === 'myproj-tab-github') url.searchParams.set('tab', 'github'); else url.searchParams.delete('tab');
  history.replaceState(history.state, '', url);
  if (tabId === 'myproj-tab-github' && !github.loaded && !github.loading) void loadGithubPage(true);
}

function bindGithubTab() {
  const tablist = document.querySelector('.myproj-tabs');
  if (!tablist || !githubListEl) return;
  tablist.addEventListener('click', (event) => {
    const tab = event.target.closest('[role="tab"]');
    if (tab) selectTab(tab.id);
  });
  tablist.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const ids = TABS.map(([tab]) => tab);
    const current = ids.indexOf(document.activeElement?.id);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? ids.length - 1
      : (current + (event.key === 'ArrowRight' ? 1 : ids.length - 1)) % ids.length;
    selectTab(ids[next], { focus: true });
  });
  githubMoreEl?.addEventListener('click', () => void loadGithubPage());
  githubListEl.addEventListener('click', (event) => {
    const start = event.target.closest('.myproj-github-start');
    if (start) startGithubAnalysis(start.closest('.myproj-github-card')?.dataset.url || '');
  });
  if (new URLSearchParams(location.search).get('tab') === 'github') selectTab('myproj-tab-github');
}

async function initMyProjectsPage() {
  listEl.innerHTML = loadingState();

  let meRes, reposRes;
  try {
    [meRes, reposRes] = await Promise.all([
      request('/api/auth/me', { credentials: 'include' }),
      request('/repositories/mine', { credentials: 'include' }),
    ]);
  } catch {
    listEl.innerHTML = errorState('목록을 불러올 수 없습니다. 잠시 후 다시 시도해 주세요.');
    return;
  }

  if (meRes.status === 401 || reposRes.status === 401) {
    location.href = homeUrl();
    return;
  }

  if (meRes.ok) {
    const me = await meRes.json();
    const avatarEl = document.getElementById('myproj-avatar');
    const nameEl = document.getElementById('myproj-username');
    if (avatarEl) {
      if (me.avatar_url) avatarEl.innerHTML = `<img src="${esc(me.avatar_url)}" style="width:100%;height:100%;border-radius:50%;object-fit:cover;">`;
      else avatarEl.textContent = (me.github_login || '?')[0].toUpperCase();
    }
    if (nameEl) nameEl.textContent = me.github_login || '';
  }

  if (!reposRes.ok) {
    listEl.innerHTML = errorState(`목록을 불러오지 못했습니다 (HTTP ${reposRes.status}).`);
    return;
  }

  const repos = await reposRes.json();
  renderSummary(repos);
  renderList(repos);
}

if (listEl) {
  initMyProjectsPage();
  bindGithubTab();
  // 분석 진행 모달을 닫을 때(modal.js) 목록을 다시 불러와 뱃지/메트릭을 갱신하기 위한 훅.
  // 내 GitHub 저장소 탭은 '이미 분석됨' 표시가 바뀔 수 있으므로 보고 있으면 다시 불러오고, 아니면 다음에 열 때 불러온다.
  window.refreshMyProjects = () => {
    void initMyProjectsPage();
    github.loaded = false;
    if (!document.getElementById('myproj-github')?.hidden) void loadGithubPage(true);
  };
}
