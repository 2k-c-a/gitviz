/* ── 드릴다운 팝업: 기여자 ↔ 파일 상세, 스택형 뒤로가기 ──
   타임라인 "상위 기여자" 클릭, 위험도 탭 "파일" 클릭 시 열리는 공용 모달.
   기여자 팝업 안에서 협업자를 클릭하면 그 사람 팝업이, 파일 팝업 안에서 영향
   파일을 클릭하면 그 파일 팝업이 스택으로 쌓인다 — 뒤로가기로 한 단계씩 복귀. */
import { getRepoId } from './api.js';
import {
  fetchContributorOwnedFiles,
  fetchContributorCollaborators,
  fetchFileDetail,
} from './api.js';
import { getRepoGithubInfo, githubFileUrl } from './github.js';

const overlay  = () => document.getElementById('drilldown-modal');
const titleEl  = () => document.getElementById('dd-title');
const subEl    = () => document.getElementById('dd-subtitle');
const bodyEl   = () => document.getElementById('dd-body');
const backBtn  = () => document.getElementById('dd-back-btn');
const codeBtn  = () => document.getElementById('dd-code-btn');

const num = (n) => (n ?? 0).toLocaleString('en-US');
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const pct = (n) => Math.round((n ?? 0) * 100);
const avatarLetter = (name) => esc((name || '?')[0].toUpperCase());

let _stack = []; // [{type:'contributor', id} | {type:'file', id}]

export function openContributorDrilldown(contributorId) {
  _stack = [{ type: 'contributor', id: contributorId }];
  renderTop();
}

/* extra: 코드분석 트리(/tree)에서 이미 가져와 둔 risk_score/coupling_score 등 —
   있으면 API 재호출 없이 "왜 위험한지" 설명에 바로 쓴다. 없는 호출부(중첩 드릴다운 등)는
   그냥 생략된다. */
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
    bodyEl().innerHTML = '<div style="padding:24px;text-align:center;color:var(--red-dark);font-size:12px;">불러오지 못했습니다.</div>';
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
  subEl().textContent = `소유 파일 ${num(owned.owned_file_count)}개`;

  const files = owned.files || [];
  const filesHtml = files.length
    ? files.map(f => `
        <div class="coup-row" style="cursor:pointer;" onclick="window.__ddPushFile(${f.file_id})" title="${esc(f.path)}">
          <div class="coup-file">${esc(f.path)}${f.is_bus_factor_risk ? ' <span class="badge badge-red" style="font-size:9px;">BF=1</span>' : ''}</div>
          <div class="coup-pct">${pct(f.share)}%</div>
        </div>`).join('')
    : '<div style="padding:12px;text-align:center;color:var(--text3);font-size:12px;">소유 파일 정보 없음</div>';

  const collaborators = collab.collaborators || [];
  const collabHtml = collaborators.length
    ? collaborators.map(c => `
        <div class="c-row" style="cursor:pointer;" onclick="window.__ddPushContributor(${c.contributor.id})">
          <div class="avatar" style="background:#E6F1FB;color:#185FA5;">${avatarLetter(c.contributor.name)}</div>
          <div class="c-info"><div class="c-name">${esc(c.contributor.name)}</div><div class="c-meta">협업 강도 ${(c.edge_weight * 100).toFixed(1)}</div></div>
        </div>`).join('')
    : '<div style="padding:12px;text-align:center;color:var(--text3);font-size:12px;">협업 데이터 없음</div>';

  bodyEl().innerHTML = `
    <div style="font-size:12px;font-weight:600;color:var(--text2);margin:14px 0 6px;">소유 파일 (지분 순)</div>
    <div style="display:flex;flex-direction:column;gap:2px;max-height:180px;overflow-y:auto;">${filesHtml}</div>
    <div style="font-size:12px;font-weight:600;color:var(--text2);margin:16px 0 6px;">협업자 (협업 강도 순)</div>
    <div style="display:flex;flex-direction:column;gap:4px;max-height:180px;overflow-y:auto;">${collabHtml}</div>
  `;
}

/* extra(선택)에 risk_score/coupling_score가 있으면 "왜 위험한지"를 한두 문장으로 요약한다. */
function buildRiskExplanation(extra, detail) {
  if (!extra || extra.risk_score == null) return null;
  const reasons = [];
  if (extra.risk_score >= 0.7) reasons.push('Risk Score가 상위권으로 결함 위험이 높습니다');
  else if (extra.risk_score >= 0.5) reasons.push('Risk Score가 중간 이상으로 주의가 필요합니다');
  if (detail.bus_factor === 1) reasons.push('이 파일을 아는 사람이 한 명뿐입니다(BF=1)');
  if (extra.coupling_score >= 0.7) {
    const cnt = extra.coupled_file_count ?? 0;
    reasons.push(`결합도가 높아 다른 파일 ${cnt}개에 영향을 줄 수 있습니다`);
  }
  if (!reasons.length) reasons.push('현재 특별한 위험 신호는 없습니다');
  return reasons.join(' · ');
}

async function renderFile(fileId, extra) {
  const repoId = getRepoId();
  const detail = await fetchFileDetail(repoId, fileId, 20);
  if (detail.error) throw new Error(detail.error);

  const fileName = detail.file.path.split('/').pop();
  titleEl().textContent = fileName;
  titleEl().title = detail.file.path;
  subEl().style.fontFamily = 'monospace';
  subEl().textContent = detail.file.path;

  const ghInfo = await getRepoGithubInfo(repoId);
  const codeUrl = githubFileUrl(ghInfo, detail.file.path);
  if (codeUrl) {
    codeBtn().href = codeUrl;
    codeBtn().style.display = '';
  } else {
    codeBtn().style.display = 'none';
  }

  const bfBadge = detail.is_bus_factor_risk ? ' <span class="badge badge-red" style="font-size:10px;">BF=1</span>' : '';
  const riskExplanation = buildRiskExplanation(extra, detail);
  const riskHtml = riskExplanation
    ? `<div style="font-size:11px;color:var(--text2);margin-top:4px;">Risk Score <span style="font-weight:600;color:var(--text);">${extra.risk_score.toFixed(2)}</span>${extra.coupling_score != null ? ` · 결합도 ${extra.coupling_score.toFixed(2)}` : ''}</div>
       <div class="rec-box" style="margin-top:6px;">${esc(riskExplanation)}</div>`
    : '';

  const editors = detail.editors || [];
  const editorsHtml = editors.length
    ? editors.map(e => `
        <div class="c-row" style="cursor:pointer;" onclick="window.__ddPushContributor(${e.contributor_id})">
          <div class="avatar" style="background:#E6F1FB;color:#185FA5;">${avatarLetter(e.name)}</div>
          <div class="c-info"><div class="c-name">${esc(e.name)}</div><div class="c-meta">지분 ${pct(e.share)}%</div></div>
        </div>`).join('')
    : '<div style="padding:12px;text-align:center;color:var(--text3);font-size:12px;">수정 기록 없음</div>';

  const coupled = detail.coupled_files || [];
  const coupledHtml = coupled.length
    ? coupled.map(f => `
        <div class="coup-row" style="cursor:pointer;" onclick="window.__ddPushFile(${f.file_id})" title="${esc(f.path)}">
          <div class="coup-file">${esc(f.path)}</div>
          <div class="coup-cnt">${num(f.co_change_count)}회</div>
          <div class="coup-pct">${f.adjusted_score.toFixed(2)}</div>
        </div>`).join('')
    : '<div style="padding:12px;text-align:center;color:var(--text3);font-size:12px;">함께 변경된 파일 없음</div>';

  bodyEl().innerHTML = `
    <div style="font-size:11px;color:var(--text2);margin-top:6px;">Bus Factor ${detail.bus_factor ?? '—'}${bfBadge} · Entropy ${detail.entropy.toFixed(2)}</div>
    ${riskHtml}
    <div style="font-size:12px;font-weight:600;color:var(--text2);margin:14px 0 6px;">수정한 사람 (지분 순)</div>
    <div style="display:flex;flex-direction:column;gap:4px;max-height:160px;overflow-y:auto;">${editorsHtml}</div>
    <div style="font-size:12px;font-weight:600;color:var(--text2);margin:16px 0 6px;">영향을 끼칠 수 있는 파일 (co-change 순)</div>
    <div style="display:flex;flex-direction:column;gap:2px;max-height:160px;overflow-y:auto;">${coupledHtml}</div>
  `;
}

window.__ddPushContributor = (id) => pushFrame({ type: 'contributor', id });
window.__ddPushFile        = (id) => pushFrame({ type: 'file', id });
window.openContributorDrilldown = openContributorDrilldown;
window.openFileDrilldown        = openFileDrilldown;
window.drilldownBack             = drilldownBack;
window.closeDrilldown             = closeDrilldown;
