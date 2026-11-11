/* ── AI 분석 화면 (자동 분석 리포트 · 대화형 분석) ──
   - 주소 ?repo=&snapshot= 가 기준이다. snapshot이 없으면 서버가 그 저장소의 최신 분석 결과를 쓴다.
   - 앱 전용. API key는 앱(main)이 이 PC에 보관하고 실행 요청에만 붙인다. 이 화면은 key를 보지 못하고 저장 여부만 안다.
   - 실행은 서버가 하고, 이 화면은 접수 후 상태를 폴링해 진행 단계(도구 조회·AI 해석·근거 검증)를 보여 준다.
   - 근거(evidence)·관련 파일/커밋/기여자는 서버가 도구 결과와 대조해 검증한 값만 온다. 링크는 기존 상세 보기(드릴다운)·
     파일 분석 화면·GitHub 커밋 주소로만 만든다.
   - 모든 동적 문자열은 textContent로만 넣는다(innerHTML에 데이터를 넣지 않는다). */
import { createAiClient } from './ai-api.js';
import { createAiPoller } from './ai-poll.js';
import { parseState } from './viz-state.js';
import { pageUrl } from './ui/shell.js';
import { SETTINGS_ENTRY_URL } from './platform/workspace-navigation.js';
import { getRepoGithubInfo } from './github.js';
import { SEVERITY_ORDER, sortInsights, severityCounts, basisRows, refCount, toMarkdown, toHtml, reportFileName, downloadText } from './ai-insight-report.js';

const SEVERITY = { critical: ['심각', 'error'], high: ['높음', 'error'], medium: ['보통', 'warn'], low: ['낮음', 'info'], info: ['정보', 'info'] };
const CONFIDENCE = { high: '신뢰도 높음', medium: '신뢰도 보통', low: '신뢰도 낮음' };
const ACTIVE = ['queued', 'running'];
const MIN_SEVERITY = [['info', '전체'], ['medium', '보통 이상'], ['high', '높음 이상']];
const SUGGESTIONS = [
  '이 프로젝트에서 변경 집중도가 가장 높은 파일은?',
  '최근 6개월 동안 가장 많이 기여한 개발자는 누구야?',
  '최근 버그 수정이 늘어난 영역이 있어?',
  '한 사람에게 변경이 몰린 디렉터리는?',
  '협업이 가장 활발한 개발자 쌍은?',
  '이 프로젝트는 기간별로 어떻게 변해 왔어?',
];
const TOOL_LABEL = {
  getProjectOverview: '프로젝트 개요', getCommitTimeline: '월별 커밋 추이', getCommitTypeStatistics: '커밋 유형 분포',
  getCommitStatistics: '커밋 통계', getContributorStatistics: '기여자 통계', getTopCollaborationPairs: '협업 쌍',
  getContributorCollaboration: '기여자 협업 관계', getDeveloperActivity: '개발자 활동', getOwnershipConcentration: '소유 집중도(BF80)',
  getDirectoryStatistics: '디렉터리 통계', getFileChangeFrequency: '파일 변경 빈도', getFileChangeHistory: '파일 변경 이력',
  getCoChangedFiles: '함께 변경된 파일', getTopCoupledPairs: '공동 변경 쌍', getRelatedCommits: '관련 커밋',
  getCommitDetails: '커밋 상세', compareDevelopmentPeriods: '기간 비교', getProjectEvolution: '프로젝트 변화',
  listDirectory: '코드 디렉터리', readFile: '코드 읽기', searchCode: '코드 검색',
};
const FAILURE = {
  OUTPUT_UNVERIFIED: 'AI 답변이 근거 검증을 통과하지 못해 표시하지 않았습니다(조회 결과에 없는 파일·커밋·수치를 언급했습니다). 질문을 바꿔 다시 시도해 보세요.',
  OUTPUT_INVALID: 'AI 응답 형식이 올바르지 않아 표시하지 않았습니다.',
  OUTPUT_TRUNCATED: 'AI 응답이 길이 제한으로 잘려 표시하지 않았습니다.',
  PROVIDER_REFUSED: 'AI 제공자가 응답을 거부했습니다.',
  TOO_MANY_STEPS: '정해진 조회 횟수 안에 답을 만들지 못했습니다. 질문을 좁혀 보세요.',
  CREDENTIAL_UNAVAILABLE: 'API 키를 사용할 수 없습니다. 앱 설정에서 키를 확인하세요.',
  CREDENTIAL_REQUIRED: '앱 설정에서 이 AI 제공자의 API 키를 먼저 저장하세요.',
  AI_KEY_REQUIRED: '앱 설정에서 이 AI 제공자의 API 키를 먼저 저장하세요.',
  PROVIDER_AUTH: 'AI 제공자가 API 키를 거부했습니다(키 오류 또는 결제 문제). 앱 설정에서 키를 확인하세요.',
  RUN_TIMEOUT: '실행이 제한 시간 안에 끝나지 않았습니다(실행 중 서버가 다시 시작된 경우도 이렇게 끝납니다). 다시 실행하세요.',
  QUEUE_UNAVAILABLE: '실행을 시작하지 못했습니다. 잠시 후 다시 시도하세요.',
  SNAPSHOT_UNAVAILABLE: '분석 결과를 찾을 수 없습니다. 먼저 저장소를 분석하세요.',
  TRANSFER_NOT_ALLOWED: '비공개 저장소의 분석 결과는 외부 AI로 보낼 수 없습니다. 로컬 모델을 선택하세요.',
  RESOURCE_NOT_FOUND: '저장소 접근 권한을 확인하지 못했습니다.',
  INPUT_TOO_LARGE: '조회 결과가 모델의 입력 한도를 넘었습니다.',
  INTERNAL_ERROR: '서버 오류로 실행하지 못했습니다.',
};
const failureText = (code) => FAILURE[code] || `실행하지 못했습니다 (${code || '알 수 없음'}).`;

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = String(v);
    else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, '');
    else e.setAttribute(k, String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    e.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return e;
}
const mount = (el, ...nodes) => { el.replaceChildren(...nodes.flat().filter(Boolean)); return el; };
const chip = (text, tone = 'info', title) => h('span', { class: `ai-chip ${tone}`, title }, text);
const fmtTime = (iso) => { const d = iso ? new Date(iso) : null; return d && !Number.isNaN(d.getTime()) ? d.toLocaleString('ko-KR') : '—'; };
const fmtDay = (iso) => { const d = iso ? new Date(iso) : null; return d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString('ko-KR') : '—'; };
const button = (text, onclick, { cls = 'btn-secondary', disabled = false, title } = {}) =>
  h('button', { type: 'button', class: `${cls} ai-btn`, disabled, title, onclick }, text);
const banner = (kind, title, body) => h('div', { class: `ai-banner ${kind}`, role: kind === 'error' ? 'alert' : 'status' },
  h('div', { class: 'ai-banner-title', text: title }), body ? h('div', { class: 'ai-banner-body', text: body }) : null);

function progressText(ev) {
  const tool = TOOL_LABEL[ev.tool] || '조회';
  if (ev.stage === 'category') return ev.status === 'done' ? `${ev.label}: 인사이트 ${ev.insights ?? 0}개` : `${ev.label} 분석 시작`;
  if (ev.stage === 'tool') {
    if (ev.status === 'running') return `${tool} 조회 중${ev.reason ? ` · ${ev.reason}` : ''}`;
    return `${tool} 조회 ${ev.status === 'error' ? '실패(입력을 확인하지 못함)' : '완료'}`;
  }
  if (ev.stage === 'ai') return ev.attempt > 1 ? 'AI 해석 보완 요청 중' : ev.round ? `AI가 필요한 자료를 판단하는 중 (${ev.round}단계)` : 'AI 해석 중';
  if (ev.stage === 'validate') return '답변의 근거를 조회 결과와 대조하는 중';
  return '진행 중';
}
const progressList = (events, limit = 10) => h('ol', { class: 'ins-progress', 'aria-label': '진행 단계' },
  (events || []).slice(-limit).map((ev) => h('li', { class: ev.status === 'error' ? 'error' : '', text: progressText(ev) })));

function main(page) {
  const $ = (id) => document.getElementById(id);
  const { repo, snapshot } = parseState(location.search);
  const api = createAiClient();
  const state = { me: null, models: [], allModels: [], profileId: null, github: null, repoName: '',
    includeCode: false, reports: [], report: null, minSeverity: 'info', openRefs: new Set(), conversations: [], conversation: null, sending: false };
  let reportPoller = null;
  let chatPoller = null;

  const showError = (target, e, fallback) => mount(target, banner('error', fallback, e?.message || failureText(e?.code)));

  /* ── 관련 파일·커밋·기여자 링크 ── */
  function refs(item, targetSha) {
    const links = item.links || { files: {}, contributors: {} };
    const code = (item.relatedCode || []).map((c) => {
      const label = `${c.path}:${c.startLine}${c.endLine !== c.startLine ? `-${c.endLine}` : ''}`;
      const anchor = `#L${c.startLine}${c.endLine !== c.startLine ? `-L${c.endLine}` : ''}`;
      if (state.github?.base && targetSha) {
        const href = `${state.github.base}/blob/${encodeURIComponent(targetSha)}/${c.path.split('/').map(encodeURIComponent).join('/')}${anchor}`;
        return h('a', { class: 'ins-ref', href, target: '_blank', rel: 'noopener noreferrer', title: '분석 기준 커밋의 해당 줄을 GitHub에서 보기' }, label);
      }
      const fileId = links.files?.[c.path];
      return fileId ? button(label, () => window.openFileDrilldown?.(fileId), { cls: 'ins-ref', title: `${c.path} 상세 보기` })
        : h('code', { class: 'ins-ref', text: label });
    });
    const files = (item.relatedFiles || []).map((path) => {
      const fileId = links.files?.[path];
      if (fileId) return button(path, () => window.openFileDrilldown?.(fileId), { cls: 'ins-ref', title: `${path} 상세 보기` });
      return h('a', { class: 'ins-ref', href: pageUrl('code-analysis'), title: `${path} · 파일 분석 화면에서 보기` }, `${path}/`);
    });
    const commits = (item.relatedCommits || []).map((sha) => state.github?.base
      ? h('a', { class: 'ins-ref', href: `${state.github.base}/commit/${encodeURIComponent(sha)}`, target: '_blank', rel: 'noopener noreferrer', title: 'GitHub에서 커밋 보기' }, sha.slice(0, 10))
      : h('code', { class: 'ins-ref', text: sha.slice(0, 10) }));
    const people = (item.relatedContributors || []).map((name) => {
      const cid = links.contributors?.[name];
      return cid ? button(name, () => window.openContributorDrilldown?.(cid), { cls: 'ins-ref', title: `${name} 활동 보기` })
        : h('span', { class: 'ins-ref', text: name });
    });
    const group = (label, nodes) => nodes.length ? h('div', null, h('div', { class: 'ins-label', text: label }), h('div', { class: 'ins-refs' }, nodes)) : null;
    return [group('근거 코드 (분석 기준 커밋)', code), group('관련 파일·디렉터리', files), group('관련 커밋', commits), group('관련 기여자', people)];
  }
  const evidenceList = (evidence) => h('ul', { class: 'ins-evidence' }, (evidence || []).map((e) =>
    h('li', { title: `${TOOL_LABEL[e.tool] || e.tool || ''} 조회 결과 (${e.toolCallId})` }, h('span', { class: 'ins-ev-label', text: e.label }), h('b', { text: e.value }))));

  /* ── 상단: 모델·전송 안내 ── */
  async function loadSetup() {
    const setup = $('insight-setup');
    try {
      state.me = await api.me();
    } catch (e) { return showError(setup, e, '로그인 상태를 확인하지 못했습니다.'); }
    if (!state.me) {
      mount(setup, h('div', { class: 'ai-card' }, h('h2', { text: '로그인이 필요합니다' }),
        h('p', { class: 'ai-muted', text: 'AI 분석은 본인의 AI 키나 로컬 모델로 실행하고 결과는 본인에게만 보입니다.' }),
        h('div', { class: 'ai-row' }, button('GitHub로 로그인', () => (window.githubLogin ? window.githubLogin() : (location.href = '/api/auth/github')), { cls: 'btn-primary' }))));
      return false;
    }
    const keyStatus = globalThis.desktop?.aiKeys?.status;
    if (!keyStatus) {
      mount(setup, h('div', { class: 'ai-card' }, h('h2', { text: 'AI 분석은 GitViz 앱에서만 쓸 수 있습니다' }),
        h('p', { class: 'ai-muted', text: 'API 키를 이 PC의 앱에만 보관하고 실행할 때만 쓰는 방식이라 웹 브라우저에서는 실행할 수 없습니다.' })));
      return false;
    }
    let listed, saved;
    try {
      [listed, saved] = await Promise.all([api.insightModels(), keyStatus()]);
    } catch (e) {
      if (e.status === 404 || e.code === 'RESOURCE_NOT_FOUND') {
        mount(setup, h('div', { class: 'ai-card' }, h('h2', { text: 'AI 기능을 쓸 수 없습니다' }), h('p', { class: 'ai-muted', text: '이 서버에서는 AI 분석이 꺼져 있습니다.' })));
        return false;
      }
      if (/STORAGE_UNAVAILABLE/.test(String(e?.message || ''))) return showError(setup, e, 'Windows 계정 보호 저장소를 쓸 수 없어 저장한 API 키를 확인하지 못했습니다.');
      showError(setup, e, 'AI 설정을 불러오지 못했습니다.');
      return false;
    }
    state.allModels = listed.models || [];
    state.models = state.allModels.filter((m) => m.execution_location === 'local' || !m.requires_api_key || saved?.[m.provider]);
    let preferred = null;                                   // 앱 설정에서 고른 기본 모델(같은 앱 저장소)
    try { preferred = JSON.parse(localStorage.getItem('gitviz.workspace.ai') || '{}').profileId || null; } catch { /* 기본값 없음 */ }
    state.profileId = state.models.find((m) => m.profile_id === preferred)?.profile_id || state.models[0]?.profile_id || null;
    renderSetup();
    return true;
  }
  const providerOf = (profileId) => state.allModels.find((m) => m.profile_id === profileId)?.provider;
  function renderSetup() {
    const setup = $('insight-setup');
    if (!state.models.length) {
      return mount(setup, h('div', { class: 'ai-card' }, h('h2', { text: '사용할 AI 모델이 없습니다' }),
        h('p', { class: 'ai-muted', text: '앱 설정의 AI 항목에서 Anthropic·OpenAI·Gemini 중 하나의 API 키를 저장하세요. 키는 이 PC에만 보관됩니다.' }),
        h('div', { class: 'ai-row' }, h('a', { class: 'btn-secondary ai-btn', href: SETTINGS_ENTRY_URL }, '설정 열기'))));
    }
    const model = state.models.find((m) => m.profile_id === state.profileId);
    const external = model?.execution_location !== 'local';
    const select = h('select', { 'aria-label': 'AI 모델', class: 'ai-input', onchange: (ev) => { state.profileId = ev.target.value; renderSetup(); renderAuto(); renderChat(); } },
      state.models.map((m) => h('option', { value: m.profile_id, selected: m.profile_id === state.profileId }, m.display_name)));
    const notice = external
      ? '선택한 외부 AI에 이 저장소의 분석 수치, 파일·디렉터리 경로, 기여자 이름, 커밋 메시지 제목이 전송됩니다. 아래에서 켜지 않으면 소스 코드는 보내지 않습니다. 비공개 저장소는 외부 AI로 분석할 수 없습니다. 호출 비용은 앱에 저장한 API 키의 계정에 청구되고 횟수 제한이 없으니 사용량에 주의하세요. 키는 실행할 때만 서버로 보내져 그 실행에만 쓰이고 저장되지 않습니다.'
      : '같은 서버의 로컬 모델로 실행합니다. 외부 AI로 데이터를 보내지 않습니다.';
    const codeToggle = h('label', { class: 'ins-code-toggle' },
      h('input', { type: 'checkbox', id: 'insight-include-code', checked: state.includeCode,
        onchange: (ev) => { state.includeCode = ev.target.checked; renderSetup(); } }),
      h('span', null, h('b', { text: '소스 코드도 읽게 하기' }), ' — 분석 기준 커밋의 파일 일부를 AI가 직접 읽고 줄 단위로 근거를 댑니다. 답변이 구체적이지만 호출 비용이 늘어납니다.'));
    const codeNotice = !state.includeCode ? null : external
      ? `켜면 소스 코드 일부(한 번 실행에 최대 약 8만 자, 파일 20개)도 ${model?.display_name || '외부 AI'}에 전송됩니다. 비밀 정보가 의심되는 파일(.env·키 파일·토큰 패턴), 생성·잠금 파일, 바이너리는 보내지 않습니다. 비공개 저장소에는 쓸 수 없습니다.`
      : '켜면 서버의 로컬 모델이 소스 코드 일부를 읽습니다. 외부로 보내지 않습니다.';
    mount(setup, h('div', { class: 'ai-card' },
      h('div', { class: 'ai-row between' }, h('h2', { text: '분석에 사용할 AI' }), select),
      h('p', { class: 'ai-muted', text: notice }),
      codeToggle,
      codeNotice ? h('p', { class: 'ai-muted', text: codeNotice }) : null));
  }
  const canRun = () => !!state.profileId;

  /* ── 자동 분석 ── */
  async function loadReports(selectId) {
    try {
      state.reports = (await api.insightReports(repo)).reports || [];
    } catch (e) { return showError($('insight-auto'), e, '자동 분석 목록을 불러오지 못했습니다.'); }
    const id = selectId || state.reports[0]?.id;
    state.report = null;
    if (id) await openReport(id);
    else renderAuto();
  }
  async function openReport(id) {
    reportPoller?.stop();
    try { state.report = await api.insightReport(id); } catch (e) { return showError($('insight-auto'), e, '자동 분석 결과를 불러오지 못했습니다.'); }
    renderAuto();
    if (ACTIVE.includes(state.report.status)) {
      reportPoller = createAiPoller({
        fetchOnce: (signal) => api.insightReport(id, signal),
        isDone: (r) => !ACTIVE.includes(r.status),
        signature: (r) => `${r.status}:${(r.progress || []).length}`,
        onUpdate: (r) => { state.report = r; renderAuto(); },
        onStop: (info) => { if (info.reason === 'done') loadReports(id); },
      });
      reportPoller.start();
    }
  }
  async function startReport() {
    try {
      const created = await api.createInsightReport({ repositoryId: repo, snapshotId: snapshot, profileId: state.profileId,
        provider: providerOf(state.profileId), includeCode: state.includeCode });
      await loadReports(created.id);
    } catch (e) { showError($('insight-auto-notice') || $('insight-notice'), e, '자동 분석을 시작하지 못했습니다.'); }
  }
  function codeBasis(code) {
    if (!code?.requested) return '코드 열람 꺼짐';
    if (!code.available) return '코드 열람 요청했지만 서버에 저장소 사본이 없어 읽지 못함';
    return `코드 열람: 파일 ${code.filesRead.length}개 · 약 ${Number(code.charsSent).toLocaleString('ko-KR')}자 전송`;
  }
  function insightCard(item, targetSha) {
    const [sevText, sevTone] = SEVERITY[item.severity] || SEVERITY.info;
    const refsTotal = refCount(item);
    const refsBox = refsTotal ? h('details', { class: 'ins-refs-box', open: state.openRefs.has(item.id),
      ontoggle: (ev) => { if (ev.target.open) state.openRefs.add(item.id); else state.openRefs.delete(item.id); } },
      h('summary', { text: `관련 코드·파일·커밋·기여자 ${refsTotal}개` }), refs(item, targetSha)) : null;
    return h('article', { class: `ins-card sev-${item.severity}`, id: `insight-${item.id}` },
      h('div', { class: 'ins-card-head' }, chip(sevText, sevTone, '위험 수준'), h('h4', { text: item.title }),
        h('span', { class: 'ins-confidence', text: CONFIDENCE[item.confidence] || item.confidence || '' })),
      h('p', { class: 'ins-summary', text: item.summary }),
      item.recommendation ? h('div', { class: 'ins-rec' }, h('div', { class: 'ins-label', text: '추천 조치 (AI 의견)' }), h('p', { text: item.recommendation })) : null,
      item.evidence?.length ? h('div', null, h('div', { class: 'ins-label', text: '근거 (GitViz 계산 값)' }), evidenceList(item.evidence)) : null,
      item.interpretation ? h('div', null, h('div', { class: 'ins-label', text: '해석 (AI 의견)' }), h('p', { text: item.interpretation })) : null,
      refsBox);
  }
  function exportReport(kind) {
    const r = state.report;
    if (!r || r.status !== 'succeeded') return;
    const opts = { repoName: state.repoName || `저장소 #${repo}`, githubBase: state.github?.base || null };
    if (kind === 'md') downloadText(reportFileName(opts.repoName, r, 'md'), toMarkdown(r, opts), 'text/markdown');
    else downloadText(reportFileName(opts.repoName, r, 'html'), toHtml(r, opts), 'text/html');
  }
  const scrollTo = (id) => document.getElementById(id)?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  function renderAuto() {
    const box = $('insight-auto');
    const r = state.report;
    const running = r && ACTIVE.includes(r.status);
    const done = r?.status === 'succeeded';
    const head = h('div', { class: 'ai-card' },
      h('div', { class: 'ai-row between' },
        h('h2', { text: 'AI 자동 분석' }),
        h('div', { class: 'ai-row' },
          state.reports.length ? h('select', { class: 'ai-input', 'aria-label': '이전 자동 분석', onchange: (ev) => openReport(ev.target.value) },
            state.reports.map((x) => h('option', { value: x.id, selected: x.id === r?.id }, `${fmtTime(x.createdAt)} · ${{ queued: '대기', running: '진행 중', succeeded: '완료', failed: '실패' }[x.status] || x.status}`))) : null,
          button(running ? '분석 진행 중…' : 'AI 자동 분석 시작', startReport, { cls: 'btn-primary', disabled: running || !canRun() }))),
      h('p', { class: 'ai-muted', text: '종합 · 기술부채·유지보수 위험 · 협업 · 변경 영향도 · 이상 징후 · 진화 6개 범주를 저장된 분석 결과로 점검합니다. 범주마다 서버가 정한 도구로 자료를 조회하고 AI가 해석합니다.' }),
      done ? h('div', { class: 'ai-row ins-export', role: 'group', 'aria-label': '보고서로 내보내기' },
        h('span', { class: 'ins-label', text: '보고서로 내보내기' }),
        button('HTML (인쇄·PDF용)', () => exportReport('html'), { title: '브라우저로 열어 인쇄하면 PDF로 저장할 수 있습니다' }),
        button('Markdown', () => exportReport('md'), { title: 'GitHub 이슈·위키·문서에 붙여 넣기 좋습니다' })) : null,
      h('div', { id: 'insight-auto-notice', 'aria-live': 'polite' }));
    if (!r) return mount(box, head, h('div', { class: 'ai-card ai-muted', text: '아직 자동 분석 결과가 없습니다.' }));
    if (running) return mount(box, head, h('div', { class: 'ai-card' }, h('h3', { text: '분석 진행 중' }), progressList(r.progress)));
    if (r.status === 'failed') return mount(box, head, banner('error', '자동 분석이 실패했습니다', failureText(r.errorCode)));
    const result = r.result || {};
    const b = result.basis || {};
    const categories = (result.categories || []).map((c, n) => ({ ...c, anchor: `insight-cat-${c.key || n}`, insights: sortInsights(c.insights) }));
    const counts = severityCounts(categories);
    const total = Object.values(counts).reduce((x, y) => x + y, 0);
    const minRank = SEVERITY_ORDER.indexOf(state.minSeverity);
    const visible = (i) => SEVERITY_ORDER.indexOf(SEVERITY[i.severity] ? i.severity : 'info') <= minRank;

    const tiles = h('div', { class: 'ins-tiles', role: 'list', 'aria-label': '위험 수준별 인사이트 수' },
      h('div', { class: 'ins-tile', role: 'listitem' }, h('b', { text: total }), h('span', { text: '전체 인사이트' })),
      SEVERITY_ORDER.map((sev) => h('div', { class: `ins-tile sev-${sev}${counts[sev] ? '' : ' zero'}`, role: 'listitem' }, h('b', { text: counts[sev] }), h('span', { text: SEVERITY[sev][0] }))));
    const nav = h('nav', { class: 'ins-nav', 'aria-label': '범주로 이동' }, categories.map((c) => {
      const top = c.insights[0];
      return h('button', { type: 'button', class: 'ins-nav-item', onclick: () => scrollTo(c.anchor) },
        h('span', { class: `ins-dot${top ? ` sev-${top.severity}` : ''}`, title: top ? `가장 높은 위험 수준: ${(SEVERITY[top.severity] || SEVERITY.info)[0]}` : '인사이트 없음' }),
        h('span', { text: c.label }), h('span', { class: 'ins-nav-count', text: c.insights.length }));
    }));
    const byId = new Map(categories.flatMap((c) => c.insights.map((i) => [i.id, { ...i, categoryLabel: c.label }])));
    const highlights = (result.highlights || []).map((id) => byId.get(id)).filter(Boolean);
    const rows = basisRows(r);
    const basisList = (list) => h('dl', { class: 'ins-basis-grid' }, list.map((row) => [h('dt', { text: row.label }), h('dd', null, row.code ? h('code', { text: row.value, title: b.targetSha || '' }) : row.value)]));
    const summary = h('div', { class: 'ai-card ins-summary-card' },
      h('h3', { text: '한눈에 보기' }), tiles, nav,
      highlights.length ? h('div', { class: 'ins-highlights-box' }, h('div', { class: 'ins-label', text: '먼저 볼 인사이트' }),
        h('ol', { class: 'ins-highlights' }, highlights.map((i) => h('li', null,
          h('button', { type: 'button', class: 'ins-highlight', onclick: () => scrollTo(`insight-${i.id}`) },
            chip((SEVERITY[i.severity] || SEVERITY.info)[0], (SEVERITY[i.severity] || SEVERITY.info)[1]), h('span', { class: 'ins-hl-title', text: i.title }),
            h('span', { class: 'ins-hl-cat', text: i.categoryLabel })))))) : null,
      h('div', { class: 'ins-basis' }, h('div', { class: 'ins-label', text: '분석 기준' }), basisList(rows.filter((x) => !x.technical)),
        h('details', { class: 'ins-details' }, h('summary', { text: '기술 정보 (버전·호출 수)' }), basisList(rows.filter((x) => x.technical)))),
      (result.notes || []).map((t) => h('p', { class: 'ai-muted', text: t })));
    const filter = h('div', { class: 'ai-row ins-filter' }, h('span', { class: 'ins-label', text: '표시할 위험 수준' }),
      h('div', { class: 'ai-tabs ui-tabs', role: 'group', 'aria-label': '표시할 위험 수준' }, MIN_SEVERITY.map(([value, label]) =>
        h('button', { type: 'button', class: `ai-tab${state.minSeverity === value ? ' active' : ''}`, 'aria-pressed': String(state.minSeverity === value),
          onclick: () => { state.minSeverity = value; renderAuto(); } }, label))));
    const sections = categories.map((c) => {
      const shown = c.insights.filter(visible);
      const hidden = c.insights.length - shown.length;
      return h('section', { class: 'ins-category', id: c.anchor },
        h('h3', null, c.label, chip(`${c.insights.length}개`, 'info')),
        !c.insights.length ? h('p', { class: 'ai-muted', text: '근거가 확인된 인사이트가 없습니다. 지적이 없다는 것이 문제가 없다는 뜻은 아닙니다.' })
          : h('div', { class: 'ins-cards' }, shown.map((i) => insightCard(i, b.targetSha)),
            hidden ? h('p', { class: 'ai-muted', text: `선택한 위험 수준보다 낮은 인사이트 ${hidden}개를 숨겼습니다.` }) : null),
        c.limitations?.length ? h('details', { class: 'ins-details' }, h('summary', { text: `이 범주의 한계 ${c.limitations.length}개` }),
          h('ul', { class: 'ins-limits' }, c.limitations.map((t) => h('li', { text: t })))) : null);
    });
    mount(box, head, summary, filter, sections);
  }

  /* ── 대화형 분석 ── */
  async function loadConversations(selectId) {
    try {
      state.conversations = (await api.insightConversations(repo)).conversations || [];
    } catch (e) { return showError($('insight-chat'), e, '대화 목록을 불러오지 못했습니다.'); }
    const id = selectId === undefined ? state.conversation?.id || state.conversations[0]?.id : selectId;
    if (id) await openConversation(id);
    else { state.conversation = null; renderChat(); }
  }
  async function openConversation(id) {
    chatPoller?.stop();
    try { state.conversation = await api.insightConversation(id); } catch (e) { return showError($('insight-chat'), e, '대화를 불러오지 못했습니다.'); }
    renderChat();
    const last = state.conversation.messages.at(-1);
    if (last && ACTIVE.includes(last.status)) {
      chatPoller = createAiPoller({
        fetchOnce: (signal) => api.insightConversation(id, signal),
        isDone: (c) => !ACTIVE.includes(c.messages.at(-1)?.status),
        signature: (c) => `${c.messages.length}:${c.messages.at(-1)?.status}:${(c.messages.at(-1)?.progress || []).length}`,
        onUpdate: (c) => { state.conversation = c; renderChat(); },
        onStop: (info) => { if (info.reason === 'done') loadConversations(id); },
      });
      chatPoller.start();
    }
  }
  async function ask(question) {
    const text = (question || '').trim();
    if (!text || state.sending) return;
    state.sending = true;
    renderChat();
    try {
      let id = state.conversation?.id;
      let profileId = state.conversation?.providerProfile;
      if (!id) {
        const created = await api.createInsightConversation({ repositoryId: repo, snapshotId: snapshot, profileId: state.profileId, includeCode: state.includeCode });
        ({ id, providerProfile: profileId } = created);
      }
      const provider = providerOf(profileId);
      if (!provider) throw Object.assign(new Error('이 대화의 모델은 지금 쓸 수 없습니다. 새 대화를 시작하세요.'), { code: 'INVALID_SELECTION' });
      await api.askInsight(id, text, provider);
      state.sending = false;
      await loadConversations(id);
    } catch (e) {
      state.sending = false;
      renderChat();
      showError($('insight-chat-notice'), e, '질문을 보내지 못했습니다.');
    }
  }
  async function resetConversation() {
    if (!state.conversation) return;
    if (!window.confirm('이 대화를 지웁니다. 진행 중인 답변도 취소됩니다. 계속할까요?')) return;
    chatPoller?.stop();
    try { await api.deleteInsightConversation(state.conversation.id); } catch (e) { return showError($('insight-chat-notice'), e, '대화를 지우지 못했습니다.'); }
    state.conversation = null;
    await loadConversations(null);
  }
  function block(b) {
    if (b.type === 'table') {
      return h('div', { class: 'ins-table-wrap' }, b.title ? h('div', { class: 'ins-label', text: b.title }) : null,
        h('table', { class: 'ins-table' }, h('thead', null, h('tr', null, (b.columns || []).map((c) => h('th', { text: c })))),
          h('tbody', null, (b.rows || []).map((row) => h('tr', null, row.map((cell) => h('td', { text: cell })))))));
    }
    if (b.type === 'list') return h('div', null, b.title ? h('div', { class: 'ins-label', text: b.title }) : null, h('ul', null, (b.items || []).map((t) => h('li', { text: t }))));
    if (b.type === 'stat') return h('div', { class: 'ins-metric' }, h('span', { class: 'ins-label', text: b.title }), h('b', { text: `${b.value}${b.unit ? ` ${b.unit}` : ''}` }));
    return null;
  }
  function message(m) {
    if (m.role === 'user') return h('div', { class: 'ins-msg user', text: m.content?.text || '' });
    if (ACTIVE.includes(m.status)) return h('div', { class: 'ins-msg assistant', 'aria-busy': 'true' }, h('div', { class: 'ins-label', text: '답변을 준비하는 중' }), progressList(m.progress, 8));
    if (m.status === 'failed') return h('div', { class: 'ins-msg assistant' }, banner('error', '답변하지 못했습니다', failureText(m.errorCode)));
    const c = m.content || {};
    const calls = c.toolCalls || [];
    return h('div', { class: 'ins-msg assistant' },
      h('p', { class: 'ins-answer', text: c.text || '' }),
      (c.blocks || []).map(block),
      c.evidence?.length ? h('div', null, h('div', { class: 'ins-label', text: '근거 (GitViz 계산 값)' }), evidenceList(c.evidence)) : null,
      refs(c, c.basis?.targetSha),
      c.limitations?.length ? h('ul', { class: 'ins-limits' }, c.limitations.map((t) => h('li', { text: t }))) : null,
      h('details', { class: 'ins-details' }, h('summary', { text: `조회한 도구 ${calls.length}개 · 최근 180일(${fmtDay(c.analysisPeriod?.start)} ~ ${fmtDay(c.analysisPeriod?.end)}) · 모델 ${c.basis?.model || '—'} · ${codeBasis(c.basis?.code)}` }),
        h('ul', { class: 'ins-limits' }, calls.map((t) => h('li', { text: `${t.callId} ${TOOL_LABEL[t.tool] || t.tool}${Object.keys(t.args || {}).length ? ` ${JSON.stringify(t.args)}` : ''}${t.ok ? '' : ' (실패)'}` })))));
  }
  function renderChat() {
    const box = $('insight-chat');
    const conv = state.conversation;
    const busy = state.sending || (conv && ACTIVE.includes(conv.messages.at(-1)?.status));
    const list = h('div', { class: 'ins-convs' },
      button('+ 새 대화', () => { chatPoller?.stop(); state.conversation = null; renderChat(); }, { disabled: !canRun() }),
      state.conversations.map((c) => h('button', { type: 'button', class: `ins-conv${c.id === conv?.id ? ' active' : ''}`, title: c.title, onclick: () => openConversation(c.id) }, c.title)));
    const input = h('textarea', { class: 'ai-input', maxlength: 1000, rows: 2, placeholder: '예) 최근 버그 수정이 늘어난 영역은?', 'aria-label': '질문', disabled: busy || !canRun() });
    input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); ask(input.value); } });
    const thread = h('div', { class: 'ins-thread', 'aria-live': 'polite' },
      conv ? conv.messages.map(message) : h('div', null, h('p', { class: 'ai-muted', text: '저장된 분석 결과에 대해 질문하세요. AI가 필요한 도구를 골라 조회한 뒤 근거와 함께 답합니다. 대화는 본인에게만 보입니다.' }),
        h('div', { class: 'ins-suggest' }, SUGGESTIONS.map((q) => button(q, () => ask(q), { cls: 'ins-ref', disabled: busy || !canRun() })))));
    mount(box, h('div', { class: 'ai-card' },
      h('div', { class: 'ai-row between' }, h('h2', null, conv ? conv.title : 'AI 대화형 분석',
        conv ? chip(conv.includeCode ? '코드 열람 켜짐' : '코드 열람 꺼짐', conv.includeCode ? 'ok' : 'info', '대화를 시작할 때 정한 설정입니다. 바꾸려면 새 대화를 시작하세요.') : null),
        conv ? button('대화 초기화', resetConversation, { cls: 'btn-secondary danger', disabled: state.sending }) : null),
      h('div', { id: 'insight-chat-notice', 'aria-live': 'polite' }),
      h('div', { class: 'ins-chat' }, list, h('div', null, thread,
        h('div', { class: 'ins-compose' }, input, button(busy ? '답변 중…' : '보내기', () => ask(input.value), { cls: 'btn-primary', disabled: busy || !canRun() })),
        h('p', { class: 'ai-muted', text: 'AI 답변은 참고 자료입니다. 공동 변경은 코드 의존성이, 변경 집중도는 결함 확률이 아닙니다.' })))));
  }

  /* ── 탭·시작 ── */
  // 자동·대화형은 이 화면의 패널이고, 커밋·PR 단위 분석은 같은 저장소의 AI 리뷰 화면이다.
  function selectTab(name) {
    for (const [tab, panel] of [['insight-tab-auto', 'insight-auto'], ['insight-tab-chat', 'insight-chat']]) {
      const on = tab === `insight-tab-${name}`;
      $(tab).classList.toggle('active', on); $(tab).setAttribute('aria-selected', String(on)); $(panel).hidden = !on;
    }
    const url = new URL(location.href);
    if (name === 'chat') url.searchParams.set('tab', 'chat'); else url.searchParams.delete('tab');
    history.replaceState(history.state, '', url);
  }
  // 설정 문제로 자동·대화형을 쓸 수 없어도 커밋·PR 단위 분석(자체 모델·키 설정)으로는 갈 수 있게 한다.
  function hideInsightTabs() {
    for (const id of ['insight-tab-auto', 'insight-tab-chat', 'insight-auto', 'insight-chat']) $(id).hidden = true;
  }
  $('insight-tab-auto').addEventListener('click', () => selectTab('auto'));
  $('insight-tab-chat').addEventListener('click', () => selectTab('chat'));
  $('insight-tab-review').addEventListener('click', () => {
    location.href = new URL(`ai-review.html?repo=${repo}`, location.href).href;
  });
  if (new URLSearchParams(location.search).get('tab') === 'chat') selectTab('chat');
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { reportPoller?.nudge(); chatPoller?.nudge(); } });

  (async () => {
    if (!repo) {
      mount($('insight-notice'), banner('warn', '저장소를 먼저 선택하세요', '최근·내 분석에서 저장소를 열고 분석 개요의 [AI 분석] 버튼으로 들어오세요.'));
      page.querySelector('.ai-tabs').hidden = true;
      return;
    }
    api.repository(repo).then((r) => {
      state.repoName = r.name || '';
      const title = $('insight-title');
      title.textContent = `${state.repoName || `저장소 #${repo}`} · AI 분석`;
      title.dataset.repositoryLabel = state.repoName;
    }).catch(() => {});
    getRepoGithubInfo(repo).then((info) => { state.github = info; }).catch(() => {});
    if (!(await loadSetup())) { hideInsightTabs(); return; }
    renderAuto(); renderChat();
    await Promise.all([loadReports(), loadConversations()]);
  })();
}

const page = document.getElementById('page-ai-insights');
if (page) main(page);
