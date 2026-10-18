/* ── 드릴다운 팝업: 기여자 ↔ 파일 상세, 스택형 뒤로가기 ──
   타임라인 "상위 기여자" 클릭, 코드분석 "파일" 클릭 시 열리는 공용 모달.
   기여자 팝업 안에서 협업자를 클릭하면 그 사람 팝업이, 파일 팝업 안에서 함께 변경된
   파일을 클릭하면 그 파일 팝업이 스택으로 쌓인다 — 뒤로가기로 한 단계씩 복귀.

   파일 상세의 설명은 지금 열려 있는 스냅샷의 계산 버전·관측 기간·정렬 정책을 그대로 가리킨다.
   null(값 없음)·0·표본 부족은 서로 다른 모양으로 보여 준다. */
import {
  getRepoId,
  fetchContributorOwnedFiles,
  fetchContributorCollaborators,
  fetchFileDetail,
  fetchSnapshotMeta,
  getSortPolicy,
} from './api.js';
import { getRepoGithubInfo, githubFileUrl } from './github.js';
import { esc, num, fmtScore, fmtScoreStable, fmtPct, fmtWindow, DASH } from './fmt.js';
import { policyOf, scoreOf, evidenceOf, DISPLAY_THRESHOLD, STRONG_COUPLING } from './policy.js';

const overlay  = () => document.getElementById('drilldown-modal');
const titleEl  = () => document.getElementById('dd-title');
const subEl    = () => document.getElementById('dd-subtitle');
const bodyEl   = () => document.getElementById('dd-body');
const backBtn  = () => document.getElementById('dd-back-btn');
const codeBtn  = () => document.getElementById('dd-code-btn');

const pct = (n) => Math.round((n ?? 0) * 100);
const avatarLetter = (name) => esc((name || '?')[0].toUpperCase());
const tag = (tone, text, title = '') => `<span class="cx-tag ${tone}"${title ? ` title="${esc(title)}"` : ''}>${esc(text)}</span>`;
const na = (reason) => `<span class="cx-na" title="${esc(reason)}">${DASH}</span>`;
const has = (o, k) => o != null && o[k] !== undefined;

let _stack = []; // [{type:'contributor', id} | {type:'file', id}]

export function openContributorDrilldown(contributorId) {
  _stack = [{ type: 'contributor', id: contributorId }];
  renderTop();
}

/* extra: 코드분석 트리(/tree)에서 이미 가져와 둔 점수/함께 수정 정도 등 — 있으면 상세 응답과 함께 설명에 쓴다.
   없는 호출부(중첩 드릴다운 등)는 그냥 생략된다. */
export function openFileDrilldown(fileId, extra) {
  _stack = [{ type: 'file', id: fileId, extra }];
  renderTop();
}

function pushFrame(frame) {
  _stack.push(frame);
  renderTop();
}

export function drilldownBack() {
  if (_stack.length > 1) {
    _stack.pop();
    renderTop();
  }
}

export function closeDrilldown() {
  const el = overlay();
  if (el) el.classList.remove('open');
  _stack = [];
}

async function renderTop() {
  const el = overlay();
  if (!el) return;
  el.classList.add('open');
  backBtn().style.display = _stack.length > 1 ? '' : 'none';
  codeBtn().style.display = 'none';

  const frame = _stack[_stack.length - 1];
  bodyEl().innerHTML = '<div style="padding:24px;text-align:center;color:var(--text3);font-size:12px;">불러오는 중...</div>';

  try {
    if (frame.type === 'contributor') await renderContributor(frame.id);
    else await renderFile(frame.id, frame.extra);
  } catch (err) {
    console.error('[drilldown] 로드 실패:', err);
    bodyEl().innerHTML = `<div style="padding:24px;text-align:center;color:var(--red-dark);font-size:12px;">불러오지 못했습니다.${err && err.message ? `<br><small>${esc(err.message)}</small>` : ''}</div>`;
  }
}

async function renderContributor(contributorId) {
  const repoId = getRepoId();
  const [owned, collab] = await Promise.all([
    fetchContributorOwnedFiles(repoId, contributorId, 20),
    fetchContributorCollaborators(repoId, contributorId, 10),
  ]);
  if (owned.error || collab.error) throw new Error(owned.error || collab.error);

  const name = owned.contributor?.name || collab.contributor?.name || '익명';
  titleEl().textContent = name;
  titleEl().title = name;
  subEl().style.fontFamily = '';
  subEl().textContent = `관측 구간에 기여한 파일 ${num(owned.owned_file_count)}개`;

  const files = owned.files || [];
  const filesHtml = files.length
    ? files.map(f => `
        <div class="coup-row" style="cursor:pointer;" onclick="window.__ddPushFile(${Number(f.file_id)})" title="${esc(f.path)}">
          <div class="coup-file">${esc(f.path)}${f.is_bus_factor_risk ? ' <span class="badge badge-red" style="font-size:9px;">한 명 집중</span>' : ''}</div>
          <div class="coup-pct" title="이 파일의 관측 구간 변경 중 이 기여자의 비율">${pct(f.share)}%</div>
        </div>`).join('')
    : '<div style="padding:12px;text-align:center;color:var(--text3);font-size:12px;">기여 파일 정보 없음</div>';

  const collaborators = collab.collaborators || [];
  const collabHtml = collaborators.length
    ? collaborators.map(c => `
        <div class="c-row" style="cursor:pointer;" onclick="window.__ddPushContributor(${Number(c.contributor.id)})">
          <div class="avatar" style="background:#E6F1FB;color:#185FA5;">${avatarLetter(c.contributor.name)}</div>
          <div class="c-info"><div class="c-name">${esc(c.contributor.name)}</div><div class="c-meta">협업 강도 ${(c.edge_weight * 100).toFixed(1)}</div></div>
        </div>`).join('')
    : '<div style="padding:12px;text-align:center;color:var(--text3);font-size:12px;">협업 데이터 없음 (PR 상세·리뷰 수집 범위가 부족한 경우도 포함)</div>';

  bodyEl().innerHTML = `
    <div style="font-size:12px;font-weight:600;color:var(--text2);margin:14px 0 6px;">기여 파일 (관측 구간 변경 지분순)</div>
    <div style="display:flex;flex-direction:column;gap:2px;max-height:180px;overflow-y:auto;">${filesHtml}</div>
    <div style="font-size:12px;font-weight:600;color:var(--text2);margin:16px 0 6px;">협업자 (협업 강도 순)</div>
    <div style="display:flex;flex-direction:column;gap:4px;max-height:180px;overflow-y:auto;">${collabHtml}</div>
  `;
}

/* 점수가 있을 때만 "왜 이 점수인지"를 한두 문장으로 요약한다. 점수가 없으면 높다/낮다를 말하지 않는다.
   extra: 목록/트리의 파일 행(점수·함께 수정 정도), detail: 파일 상세 응답. */
export function buildRiskExplanation(extra, detail, policy = policyOf(null)) {
  if (!extra) return null;
  const score = scoreOf(extra);
  if (score == null) return null;
  const reasons = [];
  reasons.push(score >= DISPLAY_THRESHOLD
    ? `${policy.scoreShort} ${fmtScoreStable(score)} — 표시 임계값(${DISPLAY_THRESHOLD}) 이상입니다`
    : `${policy.scoreShort} ${fmtScoreStable(score)} — 표시 임계값(${DISPLAY_THRESHOLD}) 미만입니다. 낮은 점수가 안전을 보장하지는 않습니다`);
  if (detail && detail.bus_factor === 1) reasons.push('관측 구간에서 한 명에게 파일 변경 기여의 80% 이상이 집중됩니다');
  if (extra.coupling_score >= STRONG_COUPLING && (extra.coupling_support ?? 1) > 0) {
    const cnt = extra.coupled_file_count ?? 0;
    reasons.push(`함께 변경되는 비율이 높은 파일이 ${cnt}개 있습니다. 과거 동반 변경 기록이며 실제 의존성이나 미래 영향을 뜻하지 않습니다`);
  }
  reasons.push('결함 발생 확률이 아닌 저장소 안의 상대 값입니다');
  return reasons.join(' · ');
}

/* ── 점검 우선순위 블록 ── */
function priorityBlock({ policy, score, ev, row, extra, detail }) {
  const evMeta = evidenceOf(ev);
  const evTag = evMeta.label ? ` ${tag(evMeta.tone, evMeta.label)}` : '';
  let html = `<div class="dd-sec-title">${esc(policy.scoreName)}</div>
    <div class="dd-score-line"><b class="dd-score${score == null ? ' na' : ''}">${fmtScoreStable(score)}</b>${evTag}</div>`;

  if (score == null) {
    const why = ev === 'no_history'
      ? '관측 구간에 변경이 없어 점수가 없습니다. 0이 아니며 안전하다는 뜻도 아닙니다.'
      : ev === 'metrics_missing'
        ? '점수 계산에 필요한 입력(수정량 등)이 누락되어 점수를 계산하지 못했습니다(자료 부족). 점수 순위에서는 뒤에 둡니다.'
        : '점수가 없습니다. 이 결과에서는 이 파일의 근거 상태를 확인할 수 없어, 변경 이력이 없다고 단정하지 않습니다.';
    html += `<div class="dd-note">${esc(why)}</div>`;
  }

  if (policy.hasComponents && row) {
    const comp = row.priority_components || {};
    const line = (label, raw, pc) => `<tr><td>${esc(label)}</td><td class="r">${raw}</td><td class="r">${pc != null ? fmtScore(pc) : DASH}</td></tr>`;
    html += `<table class="dd-table"><thead><tr><th>구성 요소</th><th class="r">원시 값 (관측 구간)</th><th class="r" title="저장소 안의 다른 파일과 비교한 위치 0~1">백분위</th></tr></thead><tbody>
      ${line('변경 횟수', row.change_freq != null ? `${num(row.change_freq)}회` : DASH, comp.change_freq)}
      ${line('수정량 (추가+삭제 줄)', row.churn != null ? `${num(row.churn)}줄` : '<span class="cx-na" title="수정량 계산 입력 누락">계산 입력 누락</span>', comp.churn)}
      ${line('버그 수정 커밋', row.bugfix_count != null ? `${num(row.bugfix_count)}회` : DASH, comp.bugfix_count)}
      </tbody></table>
      <div class="dd-note">세 백분위의 평균이 점검 우선순위입니다. 평균값 자체는 상위 퍼센트가 아니라 저장소 안의 상대 위치입니다. 0도 안전을 뜻하지 않습니다. 버그 수정 횟수는 커밋 메시지 분류 기반의 약한 신호입니다.</div>`;
  } else if (policy.kind === 'legacy' && row && (row.current_risk != null || row.structural_risk != null)) {
    html += `<div class="dd-note">이전 계산 버전의 구성: CurrentRisk ${fmtScore(row.current_risk)} · StructuralRisk ${fmtScore(row.structural_risk)} (각 0.5 가중)</div>`;
  }

  const explanation = buildRiskExplanation(extra || row, detail, policy);
  if (explanation) html += `<div class="rec-box" style="margin-top:6px;font-size:12px;">${esc(explanation)}</div>`;
  if (policy.kind !== 'priority') html += `<div class="dd-note">${esc(policy.summaryText)}</div>`;
  return html;
}

/* ── 기여 분포 블록 ── */
function contributionBlock({ policy, snap, detail, row, extra }) {
  const bf = detail.bf80 ?? detail.bus_factor;
  const totalChanges = row ? row.change_freq : extra?.observation_count;
  const unidentified = has(detail, 'unidentified_share') && detail.unidentified_share != null ? detail.unidentified_share : row?.unidentified_share;
  const cells = [];
  const kv = (k, v) => cells.push(`<tr><td>${esc(k)}</td><td class="r">${v}</td></tr>`);
  /* 주요 수정자 수는 있는데 세부 값이 없으면 '식별된 기여 없음'이 아니라 이 결과 형식에 없는 값이다(이전 계산 버전) */
  const absent = bf != null ? '이 결과(이전 계산 버전)에는 저장되지 않은 값입니다' : '식별된 기여가 없습니다 (0이 아님)';

  kv('주요 수정자 수', bf != null
    ? `<b>${esc(bf)}</b>${detail.is_bus_factor_risk ? ` ${tag('red', '한 명에게 집중')}` : ''}`
    : na('식별된 기여가 없어 주요 수정자 수을 계산하지 못했습니다 (0이 아님)'));
  if (has(detail, 'top_owner_share')) kv('최대 기여자 비율', detail.top_owner_share != null ? fmtPct(detail.top_owner_share) : na(absent));
  if (has(detail, 'contributor_count')) {
    kv('식별된 기여자 수', detail.contributor_count != null ? `${num(detail.contributor_count)}명` : na(absent));
  }
  if (has(detail, 'entropy_normalized')) {
    kv('기여 분산도 (0~1)', detail.entropy_normalized != null
      ? `${fmtScore(detail.entropy_normalized)}${detail.contributor_count === 1 ? ' <small>(기여자 1명 → 0)</small>' : ''}`
      : na(bf != null ? absent : '식별된 기여가 없어 계산하지 못했습니다 (기여자 1명이면 0, 없으면 값 없음)'));
  }
  if (has(detail, 'observation_count')) {
    const identified = detail.observation_count != null ? num(detail.observation_count) : na(bf != null ? absent : '식별된 변경 없음 (작성자 미식별)');
    kv('변경 수 (식별 / 전체)', `${identified} / ${totalChanges != null ? num(totalChanges) : DASH}`
      + (detail.low_support ? ` ${tag('amber', '표본 부족', '식별된 변경이 5건 미만입니다(표시 정책)')}` : ''));
  }
  if (unidentified != null) kv('작성자 미식별 비율', fmtPct(unidentified));

  const windowText = snap.window_start
    ? `관측 구간은 이 결과에 고정된 ${fmtWindow(snap)}입니다(종료 시각 제외).`
    : `이 결과는 구간 제한 없이 전체 이력을 대상으로 했습니다. 관측 구간은 파일의 마지막 refactor 분류 커밋 이후이며, 없으면 전체 이력입니다(이전 계산 버전).`;
  return `<div class="dd-sec-title">기여 분포 (주요 수정자 수)</div>
    <table class="dd-table"><tbody>${cells.join('')}</tbody></table>
    <div class="dd-note">주요 수정자 수는 관측 구간의 파일 변경 커밋 중 80%를 차지하는 최소 기여자 수입니다. 1이면 한 명에게 관측 변경 기여가 집중되었다는 뜻이며, 실제 지식 보유 인원이 아닙니다. 작성자가 식별되지 않은 변경은 계산에서 제외합니다. ${esc(windowText)}</div>`;
}

/* ── 함께 변경된 파일 블록 ──
   선택 정책(coupling | idf)에 따라 정렬하고, 설명도 그 정책을 가리킨다. */
const SORT_TEXT = {
  coupling: '함께 수정 정도 → 함께 수정 횟수순',
  idf: 'IDF 보정 점수순 (실험)',
};
const COUPLED_SHOWN = 20;

function sortCoupled(files, policy) {
  const key = policy === 'idf' ? 'adjusted_score' : 'coupling_score';
  return [...files].sort((a, b) => (b[key] - a[key]) || (b.co_change_count - a.co_change_count) || (a.file_id - b.file_id));
}

/* 서버가 전체 후보를 선택한 정책으로 정렬한 뒤 잘라 보냈는지(sort_by 에코 + 전체 후보 수) 확인한다.
   그렇다면 서버 순서를 그대로 쓴다. 아니라면(정렬을 지원하지 않는 응답) 받은 후보 안에서만 정렬하고
   전체 후보 기준의 순위가 아니라고 표시한다. */
export function coupledCandidates(detail, sortPolicy) {
  const fetched = detail.coupled_files || [];
  const serverSorted = detail.sort_by === sortPolicy && Number.isInteger(detail.coupled_total);
  const all = serverSorted ? fetched : sortCoupled(fetched, sortPolicy);
  return {
    serverSorted,
    shown: all.slice(0, COUPLED_SHOWN),
    total: serverSorted ? detail.coupled_total : all.length,
    fetched: all.length,
  };
}

function couplingBlock({ detail, sortPolicy }) {
  const { serverSorted, shown, total, fetched } = coupledCandidates(detail, sortPolicy);
  const idf = sortPolicy === 'idf';
  const scopeText = serverSorted
    ? `전체 후보 ${num(total)}개를 이 기준으로 서버에서 정렬한 상위 ${num(shown.length)}개`
    : `조회된 후보 ${num(fetched)}개 안에서만 정렬했습니다. 전체 후보 기준의 정확한 순위가 아닐 수 있습니다`;
  const rows = shown.map(f => `
    <tr style="cursor:pointer;" onclick="window.__ddPushFile(${Number(f.file_id)})" title="${esc(f.path)}">
      <td class="file">${esc(f.path)}</td>
      <td class="r">${num(f.co_change_count)}회${f.low_support ? `<div>${tag('amber', `표본 ${num(f.co_change_count)}회`, '함께 수정 5건 미만 — 표본 부족(표시 정책이며 통계적 신뢰수준이 아님)')}</div>` : ''}</td>
      <td class="r">${fmtScore(f.coupling_score)}</td>
      <td class="r" title="이 파일이 변경된 ${num(f.selected_change_count)}번 중 ${num(f.co_change_count)}번 상대 파일도 함께 변경된 과거 비율">${fmtPct(f.selected_to_other_ratio)}<small> (${num(f.co_change_count)}/${num(f.selected_change_count)})</small></td>
      <td class="r" title="상대 파일이 변경된 ${num(f.other_change_count)}번 중 ${num(f.co_change_count)}번 이 파일도 함께 변경된 과거 비율">${fmtPct(f.other_to_selected_ratio)}<small> (${num(f.co_change_count)}/${num(f.other_change_count)})</small></td>
      ${idf ? `<td class="r">${fmtScore(f.adjusted_score)}</td>` : ''}
    </tr>`).join('');

  const table = shown.length
    ? `<div class="dd-scroll"><table class="dd-table dd-coupled"><thead><tr><th>상대 파일</th><th class="r">함께 수정</th><th class="r">함께 수정 정도</th><th class="r">이 파일 → 상대</th><th class="r">상대 → 이 파일</th>${idf ? '<th class="r">IDF 보정</th>' : ''}</tr></thead><tbody>${rows}</tbody></table></div>`
    : '<div style="padding:12px;text-align:center;color:var(--text3);font-size:12px;">함께 수정이 2건 이상인 파일이 없습니다(변경이 없다는 뜻이 아닙니다)</div>';

  const unit = detail.co_change_unit === 'pr' ? '병합 PR' : '커밋';
  return `<div class="dd-sec-title">함께 변경된 파일 <small>(과거 동반 변경 · ${esc(SORT_TEXT[sortPolicy])})</small>${serverSorted ? '' : ` ${tag('amber', '조회된 후보 안에서 정렬', '서버가 전체 후보를 정렬해 보내지 않은 응답입니다')}`}</div>
    ${table}
    <div class="dd-note">${unit} 단위의 함께 수정 기록입니다${detail.co_change_unit === 'pr' ? '' : '(파일 상세가 있는 병합 PR이 없어 커밋으로 대체)'}.
      '이 파일 → 상대'는 이 파일이 바뀐 변경 중 상대 파일도 함께 바뀐 과거 비율이고, '상대 → 이 파일'은 그 반대입니다.
      미래 영향 확률이나 실제 의존성(import)이 아닙니다. 함께 수정 2건 이상인 쌍만 있고 5건 미만은 표본 부족으로 표시합니다.
      정렬: ${esc(SORT_TEXT[sortPolicy])}${idf ? ' — 기본 정렬이 아닌 실험용 선택입니다' : ' — 기본'} · ${esc(scopeText)}.</div>`;
}

async function renderFile(fileId, extra) {
  const repoId = getRepoId();
  const sortPolicy = getSortPolicy();
  /* 후보 정렬은 서버가 전체 후보에 대해 한 뒤 잘라 보낸다 — 잘린 목록을 화면에서 다시 정렬하지 않는다 */
  const [detail, meta, ghInfo] = await Promise.all([
    fetchFileDetail(repoId, fileId, COUPLED_SHOWN, sortPolicy),
    fetchSnapshotMeta(repoId),
    getRepoGithubInfo(repoId),
  ]);
  if (detail.error) throw new Error(detail.error);

  const snap = meta.snapshot;
  const policy = policyOf(snap.formula_version);
  /* 점수 구성·근거 상태는 상세 응답이 그 파일 자체의 값을 준다. 점수 상위 목록에 없다는 이유로
     '관측 변경 없음'을 추정하지 않는다 — 응답에도 목록(extra)에도 상태가 없으면 상태를 단정하지 않는다. */
  const row = ['priority_components', 'change_freq', 'current_risk', 'structural_risk'].some(k => has(detail, k)) ? detail : undefined;
  const ev = detail.evidence_status ?? extra?.evidence_status;
  const score = has(detail, 'maintenance_priority') || has(detail, 'risk_score') ? scoreOf(detail) : extra ? scoreOf(extra) : null;

  const fileName = detail.file.path.split('/').pop();
  titleEl().textContent = fileName;
  titleEl().title = detail.file.path;
  subEl().style.fontFamily = 'monospace';
  subEl().textContent = detail.file.path;

  const codeUrl = githubFileUrl(ghInfo, detail.file.path);
  if (codeUrl) {
    codeBtn().href = codeUrl;
    codeBtn().style.display = '';
  } else {
    codeBtn().style.display = 'none';
  }

  const editors = detail.editors || [];
  const editorsHtml = editors.length
    ? editors.map(e => `
        <div class="c-row" style="cursor:pointer;" onclick="window.__ddPushContributor(${Number(e.contributor_id)})">
          <div class="avatar" style="background:#E6F1FB;color:#185FA5;">${avatarLetter(e.name)}</div>
          <div class="c-info"><div class="c-name">${esc(e.name)}</div><div class="c-meta">변경 지분 ${pct(e.share)}%</div></div>
        </div>`).join('')
    : '<div style="padding:12px;text-align:center;color:var(--text3);font-size:12px;">식별된 수정 기록 없음 (작성자 미식별이거나 관측 구간에 변경 없음)</div>';

  bodyEl().innerHTML = `
    <div class="dd-window">관측 구간 ${esc(fmtWindow(snap))}${snap.window_start ? ' (종료 시각 제외)' : ''} · 저장 결과 #${esc(snap.id)} · ${esc(policy.versionLabel)}</div>
    ${priorityBlock({ policy, score, ev, row, extra, detail })}
    ${contributionBlock({ policy, snap, detail, row, extra })}
    <div class="dd-sec-title">수정한 사람 <small>(변경 지분순)</small></div>
    <div style="display:flex;flex-direction:column;gap:4px;max-height:160px;overflow-y:auto;">${editorsHtml}</div>
    ${couplingBlock({ detail, sortPolicy })}
  `;
}

window.__ddPushContributor = (id) => pushFrame({ type: 'contributor', id });
window.__ddPushFile        = (id) => pushFrame({ type: 'file', id });
window.openContributorDrilldown = openContributorDrilldown;
window.openFileDrilldown        = openFileDrilldown;
window.drilldownBack             = drilldownBack;
window.closeDrilldown             = closeDrilldown;
