import { request } from './platform/transport.js';
/* ── 저장소 검색 페이지: GitHub 레포 검색 + 경량 예비 점수 리스트 ──
   search-results가 있을 때(=검색 페이지)만 동작한다.
   GET /search/github-repositories (Track 01)가 채워주는 스키마를 그대로 그린다:
   { query, total_count, results: [{ owner, repo_name, url, description, language,
     stars, forks, open_issues, pushed_at, is_registered, scores:{activity,health,total} }] } */

import { isLoggedIn, openLoginModal } from './login.js';
import { createState, element } from './ui/components.js';

const resultsEl  = document.getElementById('search-results');
const summaryEl  = document.getElementById('search-summary');
const inputEl    = document.getElementById('search-input');
const sortEl     = document.getElementById('search-sort');
const btnEl      = document.getElementById('search-btn');
const selectionEl = document.getElementById('search-selection');
let searchGeneration = 0;
let resultRepos = [];
let registeredReady;

const num = (n) => (n ?? 0).toLocaleString('en-US');
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const GITHUB_ICON = `<svg width="14" height="14" viewBox="0 0 16 16" fill="var(--text3)"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>`;
const STAR_ICON   = `<svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M8 .25a.75.75 0 01.673.418l1.882 3.815 4.21.612a.75.75 0 01.416 1.279l-3.046 2.97.719 4.192a.75.75 0 01-1.088.791L8 12.347l-3.766 1.98a.75.75 0 01-1.088-.79l.72-4.194L.818 6.374a.75.75 0 01.416-1.28l4.21-.611L7.327.668A.75.75 0 018 .25z"/></svg>`;
const FORK_ICON   = `<svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M5 3.25a.75.75 0 11-1.5 0 .75.75 0 011.5 0zm0 2.122a2.25 2.25 0 10-1.5 0v.878A2.25 2.25 0 005.75 8.5h1.5v2.128a2.251 2.251 0 101.5 0V8.5h1.5a2.25 2.25 0 002.25-2.25v-.878a2.25 2.25 0 10-1.5 0v.878a.75.75 0 01-.75.75h-4.5a.75.75 0 01-.75-.75v-.878zm3.75 7.378a.75.75 0 11-1.5 0 .75.75 0 011.5 0zm3-8.75a.75.75 0 100-1.5.75.75 0 000 1.5z"/></svg>`;

/* GET /repositories 로 이미 등록된 레포 url→id 매핑을 만든다. 이미 분석된 카드를
   클릭했을 때 상세 분석 페이지로 바로 보내기 위한 용도(검색 API는 repository_id를
   내려주지 않는다). Track 01의 _normalize_github_url()과 동일한 규칙으로 맞춘다. */
let registeredMap = new Map();
const normalizeUrl = (url) => String(url ?? '').trim().replace(/\/+$/, '').replace(/\.git$/i, '');

async function loadRegisteredMap() {
  const paths = ['/repositories?limit=100', ...(isLoggedIn ? ['/repositories/mine'] : [])];
  await Promise.allSettled(paths.map(async path => {
    const response = await request(path);
    if (response.ok) (await response.json()).forEach(repo => registeredMap.set(normalizeUrl(repo.url), repo.id));
  }));
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

/* 예비 점수(0~100) → 색상 티어. 값이 클수록 건강한 레포라는 뜻이라 green=high로 둔다
   (기존 risk-icon의 hi=위험/red 방향과는 반대이므로 별도 클래스로 분리했다). */
function scoreTier(total) {
  if (total == null) return 'unknown';
  if (total >= 70) return 'high';
  if (total >= 40) return 'mid';
  return 'low';
}

function scoreRing(scores) {
  const total = scores && scores.total != null ? Math.round(scores.total) : null;
  const tier = scoreTier(total);
  const title = scores
    ? `활동성 ${scores.activity ?? '-'}/55 · 이슈 관리 ${scores.health ?? '-'}/45 · 메타데이터 기반 예비 점수이며 코드 품질을 보장하지 않습니다`
    : '점수 정보 없음';
  return `<div class="score-col">
    <div class="score-caption">예비 점수</div>
    <div class="score-ring tier-${tier}" title="${esc(title)}">${total != null ? total : '—'}</div>
  </div>`;
}

/* 종합 점수 옆에 항목별 점수를 칩으로 노출한다(마우스 오버 없이도 점수 근거가 보이도록). */
function scoreBreakdown(scores) {
  if (!scores) return '';
  return `<div class="score-breakdown">
    <span class="chip">활동성 ${scores.activity ?? '-'}/55</span>
    <span class="chip">이슈 관리 ${scores.health ?? '-'}/45</span>
  </div>`;
}

function buildCard(repo) {
  const fullName = `${repo.owner}/${repo.repo_name}`;
  const langBadge = repo.language ? `<span class="badge badge-blue">${esc(repo.language)}</span>` : '';
  const registeredBadge = repo.is_registered ? `<span class="badge badge-green">이미 분석됨</span>` : '';
  const pushedRel = fmtRelative(repo.pushed_at);
  const registeredId = repo.is_registered ? (registeredMap.get(normalizeUrl(repo.url)) ?? '') : '';

  const actionHtml = '<button class="btn-secondary search-select-btn" type="button">분석 대상 선택</button>';

  return `
    <div class="panel search-card" data-url="${esc(repo.url)}" data-registered="${repo.is_registered ? '1' : '0'}" data-repo-id="${registeredId}">
      ${scoreRing(repo.scores)}
      <div class="search-card-body">
        <div class="search-card-title">
          ${GITHUB_ICON}
          <a class="search-card-name" href="${esc(repo.url)}" target="_blank" rel="noopener" data-action="stop" title="GitHub에서 열기">${esc(fullName)}</a>
          ${langBadge}
          ${registeredBadge}
        </div>
        <div class="search-card-desc">${esc(repo.description || '설명이 없습니다.')}</div>
        <div class="search-card-stats">
          <span>${STAR_ICON} ${num(repo.stars)}</span>
          <span>${FORK_ICON} ${num(repo.forks)}</span>
          <span>⚠ 이슈 ${num(repo.open_issues)}</span>
          ${pushedRel ? `<span>업데이트 ${pushedRel}</span>` : ''}
        </div>
        ${scoreBreakdown(repo.scores)}
      </div>
      <div class="search-card-action">
        ${actionHtml}
      </div>
    </div>`;
}

function showLoading() {
  summaryEl.style.display = 'none';
  resultsEl.replaceChildren(createState({ kind: 'loading', title: '저장소를 검색하는 중' }));
}

function showError(msg) {
  summaryEl.style.display = 'none';
  resultsEl.replaceChildren(createState({ kind: 'error', title: '검색하지 못했습니다', message: msg, retry: runSearch }));
}

function showEmpty(query) {
  summaryEl.style.display = 'none';
  resultsEl.replaceChildren(createState({ kind: 'empty', title: '검색 결과가 없습니다', message: `'${query}' 대신 다른 키워드 또는 저장소 이름으로 검색하세요.` }));
}

function renderResults(data) {
  const results = Array.isArray(data.results) ? data.results : [];
  resultRepos = results;
  if (!results.length) {
    showEmpty(data.query ?? inputEl.value.trim());
    return;
  }

  summaryEl.style.display = 'block';
  const totalCount = data.total_count ?? results.length;
  summaryEl.textContent = totalCount > results.length
    ? `'${data.query}' 검색 결과 ${num(totalCount)}개 중 ${results.length}개 표시`
    : `'${data.query}' 검색 결과 ${num(totalCount)}개`;

  resultsEl.innerHTML = results.map(buildCard).join('');
}

function selectRepository(repo) {
  selectionEl.hidden = false;
  selectionEl.replaceChildren();
  selectionEl.append(element('h2', 'ui-panel-title', `선택한 저장소 · ${repo.owner}/${repo.repo_name}`));
  selectionEl.append(element('p', 'p-sub', repo.description || '설명이 없습니다.'));
  const actions = element('div', 'ui-toolbar');
  if (repo.is_registered) {
    const open = element('button', 'btn-primary', '저장된 결과 확인');
    open.type = 'button'; open.addEventListener('click', () => goToDetail(repo.url)); actions.append(open);
  }
  const start = element('button', repo.is_registered ? 'btn-secondary' : 'btn-primary', repo.is_registered ? '새 분석 요청' : '선택한 저장소 분석 시작');
  start.type = 'button'; start.addEventListener('click', () => startAnalysis(repo.url));
  actions.append(start); selectionEl.append(actions);
  const notice = element('p', 'p-sub', '대상 선택만으로 분석을 시작하지 않습니다. 저장된 결과 확인은 새 분석을 만들지 않습니다.');
  selectionEl.append(notice); selectionEl.tabIndex = -1; selectionEl.focus({ preventScroll: true }); selectionEl.scrollIntoView({ block: 'nearest' });
}

/* 홈 화면의 "분석 시작" 흐름(main.js/modal.js의 openJobModal('new'))을 그대로 재사용한다.
   그 흐름은 #url-input의 값을 읽어 POST /repositories 를 호출하고 진행 모달을 띄운다. */
function startAnalysis(url) {
  if (!isLoggedIn) { openLoginModal(); return; }
  const hidden = document.getElementById('url-input');
  if (hidden) hidden.value = url;
  window.openJobModal('new');
}

async function goToDetail(url, repoId) {
  // Auth can finish after the eager search module. Refresh the read-only map when needed.
  if (!repoId && !registeredMap.has(normalizeUrl(url))) await loadRegisteredMap();
  const id = repoId || registeredMap.get(normalizeUrl(url));
  if (id) { window.goPage('overview', id); return; }
  // 조회가 새 분석을 만들지 않도록 ID 미확정은 별도 오류로 남긴다.
  showError('기존 결과의 저장소 ID를 확인하지 못했습니다. 최근·내 분석에서 결과를 열거나 다시 검색하세요. 새 분석은 시작하지 않았습니다.');
}

async function runSearch() {
  const q = inputEl.value.trim();
  if (!q) { inputEl.focus(); return; }
  const generation = ++searchGeneration;
  selectionEl.hidden = true;
  const url = new URL(location.href); url.searchParams.set('q', q); url.searchParams.set('order', sortEl.value); history.replaceState(null, '', url);

  showLoading();
  btnEl.disabled = true;
  try {
    const res = await request(`/search/github-repositories?q=${encodeURIComponent(q)}&sort=${encodeURIComponent(sortEl.value)}&per_page=20`);
    if (generation !== searchGeneration) return;
    if (!res.ok) {
      if (res.status === 401 || res.status === 403) showError('저장소를 검색할 권한이 없습니다. 로그인한 계정을 확인한 뒤 다시 시도하세요.');
      else if (res.status === 429) showError('GitHub 검색 API 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.');
      else showError(`검색에 실패했습니다 (HTTP ${res.status}).`);
      return;
    }
    const data = await res.json();
    await registeredReady;
    if (generation !== searchGeneration) return;
    renderResults(data);
  } catch {
    if (generation !== searchGeneration) return;
    showError('서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.');
  } finally {
    if (generation === searchGeneration) btnEl.disabled = false;
  }
}

function initSearchPage() {
  registeredReady = loadRegisteredMap();
  const saved = new URLSearchParams(location.search);
  inputEl.value = saved.get('q') || '';
  if (['stars', 'updated'].includes(saved.get('order'))) sortEl.value = saved.get('order');

  btnEl.addEventListener('click', runSearch);
  inputEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') runSearch(); });
  sortEl.addEventListener('change', () => { if (inputEl.value.trim()) runSearch(); });

  document.getElementById('search-examples').addEventListener('click', (e) => {
    const chip = e.target.closest('.search-example-chip');
    if (!chip) return;
    inputEl.value = chip.dataset.q;
    runSearch();
  });

  resultsEl.addEventListener('click', (e) => {
    const card = e.target.closest('.search-card');
    if (!card) return;
    if (e.target.closest('.search-select-btn')) {
      const repo = resultRepos.find(item => item.url === card.dataset.url);
      if (repo) selectRepository(repo);
    }
  });
  if (inputEl.value) void runSearch();
}

if (resultsEl) initSearchPage();
