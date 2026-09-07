/* ── 위험도 페이지: Risk Score API → DOM/테이블 연동 ──
   risk-table-body가 있을 때(=위험도 페이지)만 동작한다. */
import { getRepoId, fetchRiskScores } from './api.js';

const tableBody = document.getElementById('risk-table-body');

if (tableBody) {
  initRiskPage().catch(err => console.error('[risk] 로드 실패:', err));
}

const num = (n) => (n ?? 0).toLocaleString('en-US');
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt2 = (n) => (n ?? 0).toFixed(2);

async function initRiskPage() {
  const id = getRepoId();
  const data = await fetchRiskScores(id, 50);
  renderSummary(data.summary || {});
  renderTable(data.files || []);
}

function renderSummary(s) {
  setText('risk-m-high', s.high_risk_count != null ? num(s.high_risk_count) : '—');
  setText('risk-m-avg', s.avg_risk_score != null ? fmt2(s.avg_risk_score) : '—');
  setText('risk-m-coupling', s.hidden_coupling_count != null ? num(s.hidden_coupling_count) : '—');
  setText('risk-m-entropy', s.avg_entropy != null ? fmt2(s.avg_entropy) : '—');
}

/* 컬럼별 상대값(표시된 행 중 최댓값 대비)으로 4단계 색상 버킷 계산 */
function relLevel(value, max) {
  if (!value) return 0;
  const ratio = value / max;
  if (ratio > 0.75) return 4;
  if (ratio > 0.5) return 3;
  if (ratio > 0.25) return 2;
  return 1;
}

/* Risk Score는 전체 파일 기준 절대 정규화 값이라 고정 임계값으로 버킷 */
function riskLevel(score) {
  if (score >= 0.7) return 4;
  if (score >= 0.5) return 3;
  if (score >= 0.3) return 2;
  if (score > 0) return 1;
  return 0;
}

function renderTable(files) {
  if (!tableBody) return;

  if (!files.length) {
    tableBody.innerHTML = '<tr><td class="hm-td" colspan="7" style="text-align:center;color:var(--text3);padding:24px;">위험도 데이터가 없습니다.</td></tr>';
    return;
  }

  const maxFreq = Math.max(1, ...files.map(f => f.change_freq));
  const maxChurn = Math.max(1, ...files.map(f => f.churn));
  const maxEntropy = Math.max(1, ...files.map(f => f.entropy));

  tableBody.innerHTML = files.map(f => {
    const bfBadge = f.is_bus_factor_risk
      ? '<span class="badge badge-red" title="Bus Factor = 1 — 이 파일을 아는 사람이 한 명뿐입니다">BF=1</span>'
      : '';
    return `<tr>
      <td class="hm-td file" title="${esc(f.path)}">${esc(f.path)}${bfBadge}</td>
      <td class="hm-td"><span class="hm-val hm-${relLevel(f.change_freq, maxFreq)}">${num(f.change_freq)}</span></td>
      <td class="hm-td"><span class="hm-val hm-${relLevel(f.churn, maxChurn)}">${num(Math.round(f.churn))}</span></td>
      <td class="hm-td"><span class="hm-val hm-${relLevel(f.entropy, maxEntropy)}">${fmt2(f.entropy)}</span></td>
      <td class="hm-td"><span class="hm-val hm-${relLevel(f.bug_ratio, 1)}">${Math.round(f.bug_ratio * 100)}%</span></td>
      <td class="hm-td"><span class="hm-val hm-${relLevel(f.co_change, 1)}">${fmt2(f.co_change)}</span></td>
      <td class="hm-td"><span class="hm-val hm-${riskLevel(f.risk_score)}" style="font-weight:600;">${fmt2(f.risk_score)}</span></td>
    </tr>`;
  }).join('');
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}
