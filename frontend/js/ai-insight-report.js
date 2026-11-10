/* ── AI 자동 분석 리포트: 정렬·요약·보고서 내보내기 ──
   - DOM 없이 동작하는 순수 함수만 둔다(화면은 ai-insights.js, 회귀는 tests/frontend/test_ai_insights_frontend.cjs).
   - 보고서는 서버가 검증해 저장한 결과만 옮긴다. 새 해석·수치를 만들지 않는다.
   - HTML 보고서는 외부 자원·스크립트가 없는 단일 파일이고 모든 값은 이스케이프한다. 링크는 GitHub https 주소만 만든다. */

export const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'info'];
export const SEVERITY_LABEL = { critical: '심각', high: '높음', medium: '보통', low: '낮음', info: '정보' };
export const CONFIDENCE_LABEL = { high: '신뢰도 높음', medium: '신뢰도 보통', low: '신뢰도 낮음' };
const CONFIDENCE_ORDER = ['high', 'medium', 'low'];
const rank = (list, value) => { const i = list.indexOf(value); return i < 0 ? list.length : i; };
const severityOf = (item) => (SEVERITY_ORDER.includes(item?.severity) ? item.severity : 'info');

/* 위험 수준 → 신뢰도 순. 같은 값이면 원래 순서를 지킨다. */
export function sortInsights(items) {
  return (items || []).map((item, i) => [item, i])
    .sort(([a, ai], [b, bi]) => rank(SEVERITY_ORDER, severityOf(a)) - rank(SEVERITY_ORDER, severityOf(b))
      || rank(CONFIDENCE_ORDER, a.confidence) - rank(CONFIDENCE_ORDER, b.confidence) || ai - bi)
    .map(([item]) => item);
}

export function severityCounts(categories) {
  const counts = Object.fromEntries(SEVERITY_ORDER.map((s) => [s, 0]));
  for (const c of categories || []) for (const i of c.insights || []) counts[severityOf(i)] += 1;
  return counts;
}

const day = (iso) => { const d = iso ? new Date(iso) : null; return d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString('ko-KR') : '—'; };
const time = (iso) => { const d = iso ? new Date(iso) : null; return d && !Number.isNaN(d.getTime()) ? d.toLocaleString('ko-KR') : '—'; };

export function codeBasisText(code) {
  if (!code?.requested) return '꺼짐';
  if (!code.available) return '요청했지만 서버에 저장소 사본이 없어 읽지 못함';
  return `파일 ${(code.filesRead || []).length}개 · 약 ${Number(code.charsSent || 0).toLocaleString('ko-KR')}자 전송`;
}

/* 화면과 보고서가 같이 쓰는 분석 기준 표. 앞의 행이 먼저 보이고, technical=true는 접어 둔다. */
export function basisRows(report) {
  const result = report?.result || {};
  const b = result.basis || {};
  return [
    { label: '기준 커밋', value: (b.targetSha || '').slice(0, 10) || '—', code: true },
    { label: '분석 기간', value: `최근 180일 (${day(b.windowStart)} ~ ${day(b.windowEnd)})` },
    { label: 'AI 모델', value: b.model || '—' },
    { label: '코드 열람', value: codeBasisText(b.code) },
    { label: '생성 시각', value: time(result.generatedAt || report?.createdAt) },
    { label: '분석 결과 번호', value: `#${b.snapshotId ?? '—'}`, technical: true },
    { label: '계산 버전', value: b.formulaVersion || '—', technical: true },
    { label: '프롬프트·도구 버전', value: `${b.promptVersion || '—'} · ${b.toolVersion || '—'}`, technical: true },
    { label: 'AI 호출 수', value: `${report?.callCount ?? 0}회`, technical: true },
  ];
}

export function refsOf(item) {
  const code = (item.relatedCode || []).map((c) => ({ ...c, label: `${c.path}:${c.startLine}${c.endLine !== c.startLine ? `-${c.endLine}` : ''}` }));
  return { code, files: item.relatedFiles || [], commits: item.relatedCommits || [], people: item.relatedContributors || [] };
}
export const refCount = (item) => { const r = refsOf(item); return r.code.length + r.files.length + r.commits.length + r.people.length; };

/* GitHub 주소는 https://github.com/<owner>/<repo> 형태만 받는다(그 밖이면 링크를 만들지 않는다). */
const safeBase = (base) => (/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(base || '') ? base : null);
const pathUrl = (p) => String(p).split('/').map(encodeURIComponent).join('/');
function codeUrl(base, sha, c) {
  if (!base || !sha) return null;
  const anchor = `#L${c.startLine}${c.endLine !== c.startLine ? `-L${c.endLine}` : ''}`;
  return `${base}/blob/${encodeURIComponent(sha)}/${pathUrl(c.path)}${anchor}`;
}
const commitUrl = (base, sha) => (base ? `${base}/commit/${encodeURIComponent(sha)}` : null);

const orderedCategories = (result) => (result?.categories || []).map((c) => ({ ...c, insights: sortInsights(c.insights) }));
const highlightItems = (result) => {
  const byId = new Map((result?.categories || []).flatMap((c) => (c.insights || []).map((i) => [i.id, { ...i, categoryLabel: c.label }])));
  return (result?.highlights || []).map((id) => byId.get(id)).filter(Boolean);
};

export function reportFileName(repoName, report, ext) {
  const name = String(repoName || 'repository').replace(/[^A-Za-z0-9가-힣._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'repository';
  const d = new Date(report?.result?.generatedAt || report?.createdAt || Date.now());
  const stamp = Number.isNaN(d.getTime()) ? 'report' : `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`;
  return `gitviz-ai-report-${name}-${stamp}.${ext}`;
}

const DISCLAIMER = [
  '근거 수치는 GitViz가 저장된 분석 결과에서 계산한 값이고, 해석과 추천은 AI의 의견입니다.',
  '지적이 없다는 것이 문제가 없다는 뜻은 아닙니다. 공동 변경은 코드 의존성이, 유지보수 우선순위는 결함 확률이 아닙니다.',
];

/* ── Markdown ── */
const mdText = (s) => String(s ?? '').replace(/\r?\n+/g, ' ').replace(/([\\`*_[\]<>|#])/g, '\\$1');
const mdCode = (s) => `\`${String(s ?? '').replace(/`/g, '\'')}\``;

export function toMarkdown(report, { repoName = '', githubBase = null } = {}) {
  const result = report?.result || {};
  const base = safeBase(githubBase);
  const sha = result.basis?.targetSha;
  const cats = orderedCategories(result);
  const counts = severityCounts(cats);
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const out = [`# ${mdText(repoName || '저장소')} · AI 자동 분석 보고서`, ''];
  out.push('## 분석 기준', '', '| 항목 | 값 |', '|---|---|');
  for (const row of basisRows(report)) out.push(`| ${row.label} | ${row.code ? mdCode(row.value) : mdText(row.value)} |`);
  out.push('', '## 요약', '', `인사이트 ${total}개 — ${SEVERITY_ORDER.filter((s) => counts[s]).map((s) => `${SEVERITY_LABEL[s]} ${counts[s]}`).join(' · ') || '없음'}`, '');
  out.push('| 범주 | 인사이트 | 가장 높은 위험 수준 |', '|---|---|---|');
  for (const c of cats) out.push(`| ${mdText(c.label)} | ${c.insights.length}개 | ${c.insights.length ? SEVERITY_LABEL[severityOf(c.insights[0])] : '—'} |`);
  const highlights = highlightItems(result);
  if (highlights.length) {
    out.push('', '### 주요 인사이트', '');
    highlights.forEach((i, n) => out.push(`${n + 1}. **[${SEVERITY_LABEL[severityOf(i)]}]** ${mdText(i.title)} _(${mdText(i.categoryLabel)})_`));
  }
  for (const c of cats) {
    out.push('', `## ${mdText(c.label)} (${c.insights.length}개)`, '');
    if (!c.insights.length) out.push('근거가 확인된 인사이트가 없습니다. 지적이 없다는 것이 문제가 없다는 뜻은 아닙니다.');
    c.insights.forEach((i, n) => {
      out.push(`### ${n + 1}. ${mdText(i.title)}`, '', `**위험 수준** ${SEVERITY_LABEL[severityOf(i)]} · ${CONFIDENCE_LABEL[i.confidence] || mdText(i.confidence)}`, '', mdText(i.summary), '');
      if (i.recommendation) out.push(`> **추천 조치 (AI 의견)** ${mdText(i.recommendation)}`, '');
      if (i.evidence?.length) { out.push('**근거 (GitViz 계산 값)**', ''); i.evidence.forEach((e) => out.push(`- ${mdText(e.label)}: **${mdText(e.value)}**`)); out.push(''); }
      if (i.interpretation) out.push(`**해석 (AI 의견)** ${mdText(i.interpretation)}`, '');
      const r = refsOf(i);
      const lines = [
        r.code.length ? `- 근거 코드: ${r.code.map((x) => { const u = codeUrl(base, sha, x); return u ? `[${mdText(x.label)}](${u})` : mdCode(x.label); }).join(', ')}` : null,
        r.files.length ? `- 관련 파일·디렉터리: ${r.files.map(mdCode).join(', ')}` : null,
        r.commits.length ? `- 관련 커밋: ${r.commits.map((s) => { const u = commitUrl(base, s); return u ? `[${mdCode(s.slice(0, 10))}](${u})` : mdCode(s.slice(0, 10)); }).join(', ')}` : null,
        r.people.length ? `- 관련 기여자: ${r.people.map(mdText).join(', ')}` : null,
      ].filter(Boolean);
      if (lines.length) out.push(...lines, '');
    });
    if (c.limitations?.length) { out.push('', '_한계_', ''); c.limitations.forEach((t) => out.push(`- ${mdText(t)}`)); }
  }
  out.push('', '---', '', ...DISCLAIMER.map((t) => `_${t}_`), '', `_GitViz에서 ${time(new Date().toISOString())}에 내보냄_`, '');
  return out.join('\n');
}

/* ── HTML (단일 파일, 인쇄해 PDF로 저장 가능) ── */
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const a = (href, text) => (href ? `<a href="${esc(href)}">${esc(text)}</a>` : `<code>${esc(text)}</code>`);

const STYLE = `
:root{--bg:#fff;--text:#1f2328;--muted:#59636e;--line:#d8dee4;--soft:#f6f8fa;--critical:#b42318;--high:#d92d20;--medium:#b54708;--low:#175cd3;--info:#475467}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--text:#e6edf3;--muted:#9198a1;--line:#30363d;--soft:#161b22;--critical:#ff7b72;--high:#ffa198;--medium:#e3b341;--low:#79c0ff;--info:#9198a1}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.65 -apple-system,"Segoe UI","Malgun Gothic",sans-serif}
main{max-width:860px;margin:0 auto;padding:40px 20px 60px}h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:36px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h3{font-size:16px;margin:0}.sub{color:var(--muted);margin:0 0 24px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600;width:34%}
.counts{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 14px}.count{border:1px solid var(--line);border-radius:10px;padding:8px 14px;min-width:84px}.count b{display:block;font-size:22px;line-height:1.2}
.count span,.meta{color:var(--muted);font-size:13px}.count.zero{color:var(--muted)}.sev{display:inline-block;font-size:12px;font-weight:700;padding:1px 8px;border-radius:999px;border:1px solid currentColor;margin-right:6px}
.sev-critical{color:var(--critical)}.sev-high{color:var(--high)}.sev-medium{color:var(--medium)}.sev-low{color:var(--low)}.sev-info{color:var(--info)}
.card{border:1px solid var(--line);border-left:4px solid var(--line);border-radius:10px;padding:14px 18px;margin:0 0 14px;break-inside:avoid}
.card-critical{border-left-color:var(--critical)}.card-high{border-left-color:var(--high)}.card-medium{border-left-color:var(--medium)}.card-low{border-left-color:var(--low)}
.card p{margin:8px 0}.rec{background:var(--soft);border-radius:8px;padding:10px 12px;margin:10px 0}.label{font-size:12.5px;font-weight:700;color:var(--muted);margin:10px 0 2px}
ul{margin:4px 0;padding-left:20px}code{font:12.5px ui-monospace,Consolas,monospace;background:var(--soft);padding:1px 5px;border-radius:4px}a{color:var(--low)}
.note{color:var(--muted);font-size:13px;border-top:1px solid var(--line);margin-top:36px;padding-top:12px}
@media print{body{font-size:12.5px}main{padding:0}h2{break-after:avoid}a{color:inherit;text-decoration:none}}`;

export function toHtml(report, { repoName = '', githubBase = null } = {}) {
  const result = report?.result || {};
  const base = safeBase(githubBase);
  const sha = result.basis?.targetSha;
  const cats = orderedCategories(result);
  const counts = severityCounts(cats);
  const total = Object.values(counts).reduce((x, y) => x + y, 0);
  const title = `${repoName || '저장소'} · AI 자동 분석 보고서`;
  const rows = basisRows(report).map((r) => `<tr><th>${esc(r.label)}</th><td>${r.code ? `<code>${esc(r.value)}</code>` : esc(r.value)}</td></tr>`).join('');
  const countBoxes = `<div class="count"><b>${total}</b><span>전체 인사이트</span></div>`
    + SEVERITY_ORDER.map((s) => `<div class="count sev-${s}${counts[s] ? '' : ' zero'}"><b>${counts[s]}</b><span>${SEVERITY_LABEL[s]}</span></div>`).join('');
  const catRows = cats.map((c) => `<tr><td>${esc(c.label)}</td><td>${c.insights.length}개</td><td>${c.insights.length ? `<span class="sev sev-${severityOf(c.insights[0])}">${SEVERITY_LABEL[severityOf(c.insights[0])]}</span>` : '—'}</td></tr>`).join('');
  const highlights = highlightItems(result);
  const card = (i, n) => {
    const s = severityOf(i);
    const r = refsOf(i);
    const refs = [
      r.code.length ? `<div class="label">근거 코드 (분석 기준 커밋)</div><div>${r.code.map((x) => a(codeUrl(base, sha, x), x.label)).join(', ')}</div>` : '',
      r.files.length ? `<div class="label">관련 파일·디렉터리</div><div>${r.files.map((f) => `<code>${esc(f)}</code>`).join(' ')}</div>` : '',
      r.commits.length ? `<div class="label">관련 커밋</div><div>${r.commits.map((c) => a(commitUrl(base, c), c.slice(0, 10))).join(', ')}</div>` : '',
      r.people.length ? `<div class="label">관련 기여자</div><div>${r.people.map(esc).join(', ')}</div>` : '',
    ].join('');
    return `<article class="card card-${s}"><h3>${n + 1}. ${esc(i.title)}</h3>
<div class="meta"><span class="sev sev-${s}">${SEVERITY_LABEL[s]}</span>${esc(CONFIDENCE_LABEL[i.confidence] || i.confidence || '')}</div>
<p>${esc(i.summary)}</p>
${i.recommendation ? `<div class="rec"><div class="label">추천 조치 (AI 의견)</div>${esc(i.recommendation)}</div>` : ''}
${i.evidence?.length ? `<div class="label">근거 (GitViz 계산 값)</div><ul>${i.evidence.map((e) => `<li>${esc(e.label)}: <b>${esc(e.value)}</b></li>`).join('')}</ul>` : ''}
${i.interpretation ? `<div class="label">해석 (AI 의견)</div><p>${esc(i.interpretation)}</p>` : ''}
${refs}</article>`;
  };
  const sections = cats.map((c) => `<h2>${esc(c.label)} <span class="meta">${c.insights.length}개</span></h2>
${c.insights.length ? c.insights.map(card).join('\n') : '<p class="meta">근거가 확인된 인사이트가 없습니다. 지적이 없다는 것이 문제가 없다는 뜻은 아닙니다.</p>'}
${c.limitations?.length ? `<div class="label">한계</div><ul class="meta">${c.limitations.map((t) => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}`).join('\n');
  return `<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>${esc(title)}</title><style>${STYLE}</style></head>
<body><main>
<h1>${esc(title)}</h1><p class="sub">GitViz 저장된 분석 결과를 AI가 해석한 보고서입니다.</p>
<h2>분석 기준</h2><table>${rows}</table>
<h2>요약</h2><div class="counts">${countBoxes}</div>
<table><tr><th>범주</th><th>인사이트</th><th>가장 높은 위험 수준</th></tr>${catRows}</table>
${highlights.length ? `<h3 style="margin-top:20px">주요 인사이트</h3><ol>${highlights.map((i) => `<li><span class="sev sev-${severityOf(i)}">${SEVERITY_LABEL[severityOf(i)]}</span>${esc(i.title)} <span class="meta">(${esc(i.categoryLabel)})</span></li>`).join('')}</ol>` : ''}
${sections}
<p class="note">${DISCLAIMER.map(esc).join('<br>')}<br>GitViz에서 ${esc(time(new Date().toISOString()))}에 내보냄</p>
</main></body></html>
`;
}

/* 브라우저·앱 모두: Blob 주소로 내려받는다(앱은 저장 위치를 묻는 창이 뜬다). */
export function downloadText(fileName, text, mime) {
  const url = URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` }));
  const link = document.createElement('a');
  link.href = url; link.download = fileName; link.rel = 'noopener';
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
