/* ── Bus Factor 페이지: 분석 API → DOM/차트 연동 ──
   bf-files-tbody가 있을 때(=Bus Factor 페이지)만 동작한다. */
import { getRepoId, fetchBusFactor } from './api.js';

const tbody = document.getElementById('bf-files-tbody');

if (tbody) {
  initBusFactorPage().catch(err => console.error('[busfactor] 로드 실패:', err));
}

const num = (n) => (n ?? 0).toLocaleString('en-US');
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt2 = (n) => (n ?? 0).toFixed(2);
const pct = (n) => Math.round((n ?? 0) * 100);
const avatarLetter = (name) => esc((name || '?')[0].toUpperCase());

const BF_COLORS = { '1': '#E24B4A', '2': '#EF9F27', '3': '#378ADD', '4': '#1D9E75', '5+': '#639922' };

async function initBusFactorPage() {
  const id = getRepoId();
  const data = await fetchBusFactor(id, 50);
  if (data.error) throw new Error(data.error);

  renderSummary(data.summary || {});
  renderDonut(data.distribution || []);
  renderModules(data.risky_modules || []);
  renderOwners(data.top_owners || []);
  renderFiles(data.files || []);
}

function renderSummary(s) {
  setText('bf-m-project', s.project_bus_factor ?? '—');
  setText('bf-m-highrisk', s.high_risk_file_count != null ? num(s.high_risk_file_count) : '—');
  setText('bf-m-entropy', s.avg_entropy != null ? fmt2(s.avg_entropy) : '—');
  setText('bf-m-modules', s.risky_module_count != null ? num(s.risky_module_count) : '—');
}

function renderDonut(distribution) {
  const canvas = document.getElementById('bf-donut-chart');
  const total = distribution.reduce((sum, d) => sum + d.file_count, 0) || 1;

  if (canvas && typeof Chart !== 'undefined') {
    new Chart(canvas.getContext('2d'), {
      type: 'doughnut',
      data: {
        labels: distribution.map(d => `BF ${d.bus_factor}`),
        datasets: [{
          data: distribution.map(d => d.file_count),
          backgroundColor: distribution.map(d => BF_COLORS[d.bus_factor] || '#B4B2A9'),
          borderWidth: 0, hoverOffset: 4,
        }],
      },
      options: {
        responsive: false, cutout: '68%',
        plugins: { legend: { display: false } },
      },
    });
  }

  const legend = document.getElementById('bf-donut-legend');
  if (!legend) return;
  if (!distribution.length) {
    legend.innerHTML = '<div style="font-size:12px;color:var(--text3);">분포 데이터가 없습니다.</div>';
    return;
  }
  legend.innerHTML = distribution.map(d => {
    const color = BF_COLORS[d.bus_factor] || '#B4B2A9';
    const p = Math.round(100 * d.file_count / total);
    return `<div class="donut-leg-row"><div class="donut-leg-label"><div class="donut-swatch" style="background:${color};"></div>BF ${esc(d.bus_factor)} · ${num(d.file_count)}개</div><div class="donut-pct">${p}%</div></div>`;
  }).join('');
}

function renderModules(modules) {
  const wrap = document.getElementById('bf-modules-grid');
  if (!wrap) return;
  if (!modules.length) {
    wrap.innerHTML = '<div style="grid-column:1/-1;font-size:12px;color:var(--text3);text-align:center;padding:16px;">위험 모듈이 없습니다.</div>';
    return;
  }
  wrap.innerHTML = modules.slice(0, 8).map(m => {
    const warn = m.bf1_ratio >= 0.3 ? ' warn' : '';
    return `<div class="bus-card${warn}">
      <div class="bus-file" title="${esc(m.module)}">${esc(m.module)}</div>
      <div class="bus-num">${pct(m.bf1_ratio)}%</div>
      <div class="bus-lbl">BF=1 ${num(m.bf1_count)} / ${num(m.file_count)}개</div>
    </div>`;
  }).join('');
}

function renderOwners(owners) {
  const wrap = document.getElementById('bf-owners-list');
  if (!wrap) return;
  if (!owners.length) {
    wrap.innerHTML = '<div class="c-meta" style="padding:4px;">독점 소유 기여자가 없습니다.</div>';
    return;
  }
  const max = owners[0].exclusive_file_count || 1;
  wrap.innerHTML = owners.slice(0, 8).map(o => {
    const p = Math.round(100 * o.exclusive_file_count / max);
    return `<div class="c-row" style="cursor:pointer;" onclick="window.openContributorDrilldown(${o.contributor_id})">
      <div class="avatar" style="background:#FCEBEB;color:#A32D2D;">${avatarLetter(o.name)}</div>
      <div class="c-info"><div class="c-name">${esc(o.name)}</div><div class="c-meta">독점 파일 ${num(o.exclusive_file_count)}개</div></div>
      <div class="bar-wrap"><div class="bar-fill bar-red" style="width:${p}%;"></div></div>
      <div class="c-count">${num(o.exclusive_file_count)}</div>
    </div>`;
  }).join('');
}

function renderFiles(files) {
  if (!tbody) return;
  if (!files.length) {
    tbody.innerHTML = '<tr><td class="hm-td" colspan="4" style="text-align:center;color:var(--text3);padding:24px;">소유권 데이터가 없습니다.</td></tr>';
    return;
  }
  tbody.innerHTML = files.map(f => {
    const bfBadge = f.bus_factor === 1
      ? '<span class="badge badge-red" style="margin-left:0;">1</span>'
      : String(f.bus_factor ?? '—');
    const top = (f.owners || [])[0];
    const ownerText = top ? `${esc(top.name)} (${pct(top.share)}%)` : '—';
    return `<tr style="cursor:pointer;" onclick="window.openFileDrilldown(${f.file_id})">
      <td class="hm-td file" title="${esc(f.path)}">${esc(f.path)}</td>
      <td class="hm-td" style="text-align:center;">${bfBadge}</td>
      <td class="hm-td" style="text-align:center;">${fmt2(f.entropy)}</td>
      <td class="hm-td" style="font-size:11px;color:var(--text2);">${ownerText}</td>
    </tr>`;
  }).join('');
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}
