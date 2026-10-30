import { request } from './platform/transport.js';
/* ── 내 분석 프로젝트 페이지: 로그인한 사용자가 직접 분석 요청한 레포 목록 ──
   myproj-list가 있을 때(=내 분석 프로젝트 페이지)만 동작한다. 로그인 여부는
   /api/auth/me 응답으로 직접 판단한다(login.js의 currentUser는 main.js의
   initAuth()가 끝나야 채워지는데, 이 모듈은 그보다 먼저 평가될 수 있어
   전역 상태 타이밍에 기대지 않는 편이 안전하다). */

const listEl = document.getElementById('myproj-list');

const num = (n) => (n ?? 0).toLocaleString('en-US');
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

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
  // 수집한 기본 브랜치의 누적 커밋 수 — 결과 화면의 '관측 구간 커밋'(분석 구간 안)과 다르다
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
    return `<div class="rec-box" style="font-size:12px;">이전 분석이 새 분석 요청으로 교체되었습니다. 새 작업의 진행 상황을 확인하세요.</div>`;
  }

  return '';
}

function actionButtons(repo, repoName, cardId) {
  if (repo.status === 'done') {
    return `
      <button class="btn-secondary" style="font-size:12px;padding:5px 10px;" data-action="goPage" data-args="${encodeURIComponent(JSON.stringify(['overview', repo.id]))}">결과 보기</button>
      <button class="btn-secondary" style="font-size:12px;padding:5px 10px;" data-auth="1" data-action="openReanalyzeToast" data-args="${encodeURIComponent(JSON.stringify([repo.id]))}">재분석</button>
      <button class="btn-secondary" style="font-size:12px;padding:5px 10px;color:var(--red-dark);border-color:var(--red-mid);" data-action="openRemoveRepoModal" data-args="${encodeURIComponent(JSON.stringify([repoName,cardId]))}">제거</button>`;
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
      <button class="btn-secondary" style="font-size:12px;padding:5px 10px;color:var(--red-dark);border-color:var(--red-mid);" data-action="openRemoveRepoModal" data-args="${encodeURIComponent(JSON.stringify([repoName,cardId]))}">제거</button>`;
  }
  return `<button class="btn-secondary" style="font-size:12px;padding:5px 10px;color:var(--red-dark);border-color:var(--red-mid);" data-action="openRemoveRepoModal" data-args="${encodeURIComponent(JSON.stringify([repoName,cardId]))}">제거</button>`;
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
    <div class="metric-card"><div class="m-label">총 분석 프로젝트</div><div class="m-val">${num(total)}</div><div class="m-sub">내 레포지토리 목록</div></div>
    <div class="metric-card"><div class="m-label">검토 신호가 있는 프로젝트</div><div class="m-val${highRisk ? ' m-dn' : ''}">${num(highRisk)}</div><div class="m-sub">한 명 집중 파일 또는 점수 표시 임계값(≥0.7) 이상 파일 보유</div></div>
    <div class="metric-card"><div class="m-label">분석 중</div><div class="m-val" style="color:var(--blue);">${num(inProgress)}</div><div class="m-sub">작업 진행 중</div></div>
    <div class="metric-card"><div class="m-label">분석 실패</div><div class="m-val${failed ? ' m-dn' : ''}">${num(failed)}</div><div class="m-sub">재시도 필요</div></div>`;
}

function renderList(repos) {
  if (!repos.length) {
    listEl.innerHTML = `
      <div class="panel" style="padding:40px 20px;text-align:center;">
        <div style="font-size:13px;color:var(--text2);margin-bottom:14px;">아직 분석 요청한 레포지토리가 없습니다.</div>
        <button class="btn-primary" data-action="goHome">새 레포지토리 분석하기</button>
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
  // 재분석 진행 모달을 닫을 때(modal.js) 목록을 다시 불러와 뱃지/메트릭을 갱신하기 위한 훅
  window.refreshMyProjects = initMyProjectsPage;
}
