/* ── 결과 화면 공통 안내 배너 / 조회 실패 안내 ──
   저장 결과 번호·분석 기준(관측 기간)·계산 정책과 함께, 서로 다른 세 가지 상태를 따로 보여준다:
     1) 결과 조회   : 저장된 결과를 불러왔는가
     2) 수집 범위   : 수집이 계획한 범위를 채웠는가 (job이 done이어도 PR 상세 0/9 같은 부분 수집이 있다)
     3) 분석 근거   : 이 결과를 해석할 표본이 있는가
   긴 ISO 시각·SHA·해시 같은 기술 정보는 접어 두고 필요할 때 펼친다. */
import { esc, fmtDateTime, fmtWindow, windowDays, num } from './fmt.js';
import { policyOf, assessDataQuality, COLLECTION_TEXT, EVIDENCE_TEXT, STATUS_TONE, COLLECTION_NOTE, evidenceNote } from './policy.js';
import { parseState, buildQuery } from './viz-state.js';

const host = () => document.querySelector('.page.active .body') || document.querySelector('.page.active') || document.querySelector('main') || document.body;

function techRows(snap, dq) {
  const rows = [
    ['분석 구간', `${snap.window_start ? fmtDateTime(snap.window_start) : '전체 이력'} ~ ${fmtDateTime(snap.window_end)} (종료 시각 제외)`],
    ['분석 실행 시각', fmtDateTime(snap.analyzed_at)],
    ['기준 SHA', snap.target_sha],
    ['계산 버전', snap.formula_version],
    ['결과 해시', (snap.payload_sha256 || '').slice(0, 16)],
    ['관측 커밋', `${num(dq.commit_count)}개 · 작성자 미식별 ${num(dq.unidentified_commits)}개 · 기여자 ${num(dq.contributor_count)}명 (GitHub 계정 미연결 ${num(dq.github_account_unlinked)}명)`],
    ['병합 PR', `${num(dq.merged_pr_count)}개 중 파일 상세 ${num(dq.prs_with_files)}개 · 커밋 연결 ${num(dq.prs_with_linked_commits)}개`],
    ['기준 SHA 파일', `${num(dq.collected_target_file_count)} / ${num(dq.target_tree_file_count)}개 결과에 포함`],
    ['표본 부족 기준', `변경 ${num(dq.low_support_threshold ?? 5)}건 미만 (표시 정책이며 통계적 신뢰수준이 아님)`],
  ];
  if (dq.coverage_scope) rows.push(['수집 범위 주의', dq.coverage_scope]);
  return rows.map(([k, v]) => `<tr><td class="key">${esc(k)}</td><td class="val">${esc(v)}</td></tr>`).join('');
}

export function renderContextBanner(data) {
  const snap = data.snapshot || data._snapshot;
  if (!snap) return;
  const dq = data.data_quality || {};
  const policy = policyOf(snap.formula_version);
  const a = assessDataQuality(dq);

  let el = document.getElementById('analysis-context');
  if (!el) {
    el = document.createElement('div');
    el.id = 'analysis-context';
    el.className = 'ctx-banner';
    const container = host();
    if (!container) return;
    container.prepend(el);
  }

  const days = windowDays(snap);
  const windowText = snap.window_start
    ? `${fmtWindow(snap)}${days != null ? ` · ${days}일` : ''} (종료 시각 제외)`
    : `${fmtWindow(snap)} (이전 버전: 구간 제한 없음)`;

  const reasons = [
    ...a.collectionReasons.map(r => `수집 범위: ${r}`),
    ...a.unverified.map(r => `수집 범위: ${r}를 확인할 수 없음`),
    ...(a.evidence !== 'no_limits_found' ? a.evidenceReasons.map(r => `분석 근거: ${r}`) : []),
  ];
  const collectionTip = a.collectionReasons.length || a.unverified.length
    ? [...a.collectionReasons, ...a.unverified.map(r => `${r} 확인 불가`)].join(' / ')
    : `${COLLECTION_NOTE} ${dq.coverage_scope || ''}`.trim();
  const legacy = policy.kind !== 'priority'
    ? `<div class="ctx-legacy">${policy.kind === 'legacy' ? '이전 계산 버전(v2)으로 저장된 결과입니다.' : '이 화면이 알지 못하는 계산 버전입니다.'} 최신 정책(점검 우선순위 v3)과 직접 비교할 수 없습니다.</div>`
    : '';

  el.innerHTML = `
    <div class="ctx-row">
      <span class="ctx-id" title="이 화면이 보고 있는 저장된 분석 결과 번호">저장 결과 #${esc(snap.id)}</span>
      <span class="ctx-item"><b>관측 기간</b> ${esc(windowText)}</span>
      <span class="ctx-item"><b>지표 정책</b> ${esc(policy.versionLabel)}</span>
      <a class="ctx-link" href="#" onclick="goPage('history');return false;">분석 이력·비교 →</a>
    </div>
    <div class="ctx-row">
      <span class="ctx-chip ok" title="저장된 결과를 불러왔습니다. 수집이 끝났거나 분석 근거가 충분하다는 뜻이 아닙니다.">결과 조회 성공</span>
      <span class="ctx-chip ${STATUS_TONE[a.collection]}" title="${esc(collectionTip)}">${COLLECTION_TEXT[a.collection]}</span>
      <span class="ctx-chip ${STATUS_TONE[a.evidence]}" title="${esc(a.evidenceReasons.join(' / ') || evidenceNote(a.threshold))}">${EVIDENCE_TEXT[a.evidence]}</span>
      ${a.notes.length ? `<span class="ctx-note">${esc(a.notes.join(' · '))}</span>` : ''}
    </div>
    ${reasons.length ? `<ul class="ctx-reasons">${reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}
    ${legacy}
    <details class="ctx-details"><summary>수집 범위 · 기술 정보</summary>
      <table class="info-table">${techRows(snap, dq)}</table>
    </details>`;
}

/* 결과 조회 실패 안내 — 404(없는/남의 저장소 결과)·409(결과 없음/무결성)·네트워크 오류를 구분한다 */
export function renderLoadError(err) {
  const container = host();
  if (!container) return;
  const st = parseState(location.search);
  let title = '분석 결과를 불러오지 못했습니다';
  let body = err?.message || '알 수 없는 오류';
  let actions = '<a class="btn-secondary" href="#" onclick="location.reload();return false;">다시 시도</a>';

  if (err?.status === 404) {
    title = st.snapshot != null ? `저장 결과 #${st.snapshot}을(를) 열 수 없습니다` : '분석 결과를 찾을 수 없습니다';
    body = '이 저장소의 결과가 아니거나 삭제되었거나 접근 권한이 없을 수 있습니다.';
    if (st.snapshot != null) {
      const latest = buildQuery({ repo: st.repo });
      actions = `<a class="btn-secondary" href="#" onclick="location.href=location.pathname+'${latest}';return false;">최신 결과 보기</a>`;
    }
  } else if (err?.status === 409) {
    title = '표시할 수 있는 결과가 없습니다';
  } else if (err?.status === 0) {
    title = '서버에 연결할 수 없습니다';
  }
  const old = document.getElementById('load-error');
  if (old) old.remove();
  const box = document.createElement('div');
  box.id = 'load-error';
  box.className = 'ctx-error';
  box.innerHTML = `<div class="ctx-error-title">${esc(title)}</div>
    <div class="ctx-error-body">${esc(body)}</div>
    <div class="ctx-error-actions">${actions}
      <a class="btn-secondary" href="#" onclick="goPage('history');return false;">분석 이력 보기</a></div>`;
  container.prepend(box);
}
