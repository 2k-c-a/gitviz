/** One place for AI review and AI refactor in the 파일 tab (2026-11-12 owner decision).
 * 리뷰 → 지적 카드 → '이 문제 고치기' → diff → 적용 → 적용 전후 테스트 + 영향 범위, without leaving the tab.
 * It only calls the same window.workspace APIs the advanced tabs use; the agent still enforces consent,
 * exact patches, preconditions and the one-time apply capability. Nothing is staged, committed or pushed.
 */
import { aiDefaults } from './ai-settings.js';

const api = window.workspace;
const picker = document.getElementById('project-picker');
const root = document.getElementById('assist');

let turn = 0;            // bumps on every new request or project change; stale callbacks compare against it
let timer = null;
const candidates = new Map(); // projectId -> test candidate (or null when none) found by detectValidation

const CATEGORY = { security: '보안', correctness: '정확성', performance: '성능', quality: '품질', testing: '테스트' };
const SEVERITY = { critical: '심각', high: '높음', medium: '보통', low: '낮음', unknown: '미정' };
const TONE = { critical: 'error', high: 'error', medium: 'warn', low: 'ok', unknown: '' };
const FAILURES = {
  PRECONDITION_FAILED: 'AI가 만든 수정안이 지금 파일 내용과 정확히 맞지 않아 쓸 수 없습니다. 다시 실행하면 새 수정안을 받습니다.',
  PATCH_INVALID: 'AI가 만든 수정안의 형식이 올바르지 않아 쓸 수 없습니다. 다시 실행해 보세요.',
  PATCH_NO_CHANGE: '이 지적은 이미 고쳐져 있어 바꿀 내용이 없습니다. 다른 카드로 넘어가세요.',
  OUTPUT_INVALID: 'AI 응답 형식이 올바르지 않습니다. 다시 실행해 보세요.',
  PATCH_TOO_LARGE: '수정 범위가 너무 큽니다(한 번에 120줄까지). 파일을 줄이거나 목적을 좁혀 다시 실행하세요.',
  PATCH_SCOPE_UNSUPPORTED: '이 파일은 AI 리팩토링으로 고칠 수 없는 종류입니다(설정·의존성 파일 등).',
  INPUT_TOO_LARGE: '보낼 내용이 너무 큽니다. 파일 수를 줄여 다시 시도하세요.',
  EMPTY_INPUT: '점검할 내용이 없습니다. 파일을 체크했는지 확인하세요.',
  CONTENT_EXCLUDED: '비밀 값으로 보이는 내용이 있어 보내지 않았습니다.',
  API_KEY_REQUIRED: '이 모델의 API 키가 없습니다. 설정에서 키를 저장하거나 다른 모델을 고르세요.',
  CLOUD_MAPPING_REQUIRED: '클라우드 모델 준비가 끝나지 않았습니다. 앱을 다시 시작한 뒤 시도하세요.',
  BUDGET_EXCEEDED: '이 앱의 월 사용 한도(USD 20)를 넘습니다.',
  BUSY: '다른 작업이 진행 중입니다. 잠시 뒤 다시 시도하세요.',
  RESOURCE_NOT_FOUND: '담은 파일 정보를 찾을 수 없습니다. 파일을 다시 체크하고 실행하세요.',
  SNAPSHOT_EXPIRED: '담은 파일 정보가 만료됐습니다. 파일을 다시 체크하고 실행하세요.',
  PROVIDER_ERROR: 'AI 제공자가 오류를 돌려줬습니다. 잠시 뒤 다시 실행하세요.',
  CANCELLED: '요청을 취소했습니다.',
};

const code = error => String(error?.message || error).replace(/^.*Error invoking remote method.*Error: /, '');
const explain = value => FAILURES[value] ?? `처리하지 못했습니다 (${value}).`;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function button(text, onClick, className = '') {
  const element = node('button', text, className); element.type = 'button';
  element.addEventListener('click', onClick);
  return element;
}
function chip(text, tone) { const element = node('span', text, 'ws-chip'); if (tone) element.dataset.tone = tone; return element; }

/* ---------- frame ---------- */
const status = node('p', '', 'ws-message'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
const body = node('div', undefined, 'ws-assist-body');
root.append(status, body);
function busy(text) { status.textContent = text; status.dataset.busy = 'true'; }
function idle(text = '') { status.textContent = text; delete status.dataset.busy; }
function reset() { turn++; clearTimeout(timer); idle(); body.replaceChildren(); root.hidden = true; }
window.addEventListener('workspace-project-changed', reset);
reset();

/* ---------- AI request: preview → start → poll (a repeat with the same content reuses the earlier run) ---------- */
async function startRun(context, kind, purpose, { again = false } = {}) {
  const { profileId } = aiDefaults();
  const base = { projectId: context.projectId, snapshotId: context.snapshot.snapshotId, kind, purpose, profileId };
  // The agent hands back the earlier run for the same content and run number; '다시 실행' takes the next free number.
  for (let generation = again ? 2 : 1; generation <= 20; generation++) {
    const preview = await api.previewReview({ ...base, generation });
    try {
      const run = await api.startReview({ projectId: context.projectId, previewId: preview.previewId, previewHash: preview.previewHash,
        manifestHash: preview.manifestHash, maxEstimatedCost: preview.maxEstimatedCost, acknowledged: true,
        duplicateRiskAcknowledged: generation > 1, idempotencyKey: crypto.randomUUID() });
      if (run === null) throw new Error('CANCELLED');
      const fresh = ['queued', 'running', 'cancel_requested'].includes(run.status);
      if (fresh || !again) return { run, reused: !fresh };
    } catch (error) { if (code(error) !== 'CONFLICT') throw error; }
  }
  throw new Error('CONFLICT');
}
async function finish(context, run, mine) {
  let value = run;
  while (['queued', 'running', 'cancel_requested'].includes(value.status)) {
    await pause(1000);
    if (mine !== turn) return null;
    value = await api.getReview({ projectId: context.projectId, runId: value.runId });
  }
  return value;
}

/* ---------- entry: the 파일 tab's AI 리뷰 / AI 리팩토링 buttons ---------- */
window.addEventListener('workspace-assist', event => {
  const { kind, snapshot } = event.detail;
  if (!snapshot || snapshot.projectId !== picker.value) return;
  const context = { projectId: snapshot.projectId, snapshot };
  if (kind === 'refactor') void refactor(context, aiDefaults().purpose);
  else void review(context);
});

async function review(context, { again = false } = {}) {
  const mine = ++turn; clearTimeout(timer); root.hidden = false; body.replaceChildren();
  busy('AI가 코드를 읽고 있습니다… 보통 20초~1분 걸립니다.');
  try {
    const { run, reused } = await startRun(context, 'review', aiDefaults().purpose, { again });
    const value = await finish(context, run, mine);
    if (!value || mine !== turn) return;
    renderReview(context, value, reused);
  } catch (error) { if (mine === turn) failed(code(error), () => review(context, { again: true })); }
}

function failed(reason, retry) {
  idle();
  const box = node('div', undefined, 'ws-banner ws-banner-warn');
  box.append(node('span', explain(reason)));
  if (retry) box.append(button('다시 실행 (비용이 다시 듭니다)', retry));
  body.replaceChildren(box);
}
function reusedNote(retry) {
  const box = node('div', undefined, 'ws-banner ws-banner-info');
  box.append(node('span', '같은 내용으로 이전에 실행한 결과입니다. AI를 다시 부르지 않았습니다.'), button('다시 실행 (비용이 다시 듭니다)', retry));
  return box;
}

/* ---------- review result: one card per finding ---------- */
function renderReview(context, value, reused) {
  if (value.status !== 'succeeded') { failed(value.errorCode || value.status, () => review(context, { again: true })); return; }
  const findings = value.findings ?? [];
  idle(findings.length ? `AI 지적 ${findings.length}건 · 실행해서 확인한 결함이 아니라 코드를 읽고 짚은 것입니다.` : '');
  const parts = [];
  if (reused) parts.push(reusedNote(() => review(context, { again: true })));
  if (!findings.length) parts.push(node('p', 'AI가 짚은 문제가 없습니다. 지적이 없다고 결함이 없다는 뜻은 아닙니다.', 'ws-assist-empty'));
  const order = { P0: 0, P1: 1, P2: 2, P3: 3 };
  for (const finding of [...findings].sort((a, b) => order[a.priority] - order[b.priority])) parts.push(findingCard(context, value, finding));
  body.replaceChildren(...parts);
}

function findingCard(context, run, finding) {
  const card = node('article', undefined, 'ws-assist-card');
  const head = node('div', undefined, 'ws-assist-head');
  head.append(chip(`${SEVERITY[finding.severity] ?? finding.severity} · ${finding.priority}`, TONE[finding.severity]),
    node('strong', CATEGORY[finding.category] ?? finding.category));
  card.append(head);
  for (const [label, text] of [['문제', finding.condition], ['영향', finding.impact], ['고치는 방법', finding.suggestion]]) {
    const row = node('p', undefined, 'ws-assist-row'); row.append(node('span', label, 'ws-assist-label'), document.createTextNode(text)); card.append(row);
  }
  for (const evidence of finding.evidence) {
    card.append(node('p', `${evidence.path} · ${evidence.startLine === evidence.endLine ? `${evidence.startLine}행` : `${evidence.startLine}–${evidence.endLine}행`}`, 'ws-assist-where'),
      node('pre', evidence.quote, 'ws-assist-code'));
  }
  const actions = node('div', undefined, 'ws-toolbar');
  const fix = button('이 문제 고치기', () => { void refactor(context, purposeFor(finding)); }, 'btn-primary'); fix.dataset.risk = 'send';
  actions.append(fix, verdictControls(context, run, finding));
  card.append(actions);
  return card;
}

/** The refactor request is scoped to one finding so the model changes only what the card names. */
function purposeFor(finding) {
  const where = finding.evidence.map(item => `${item.path} ${item.startLine}${item.endLine !== item.startLine ? `–${item.endLine}` : ''}행`).join(', ');
  const text = `다음 지적만 고쳐 주세요. 다른 줄은 바꾸지 마세요.\n[${CATEGORY[finding.category] ?? finding.category} · ${finding.priority}] ${finding.condition}\n고치는 방법: ${finding.suggestion}\n위치: ${where}`;
  return text.slice(0, 1000);
}

function verdictControls(context, run, finding) {
  const group = node('span', undefined, 'ws-assist-verdict');
  let version = finding.feedback.version;
  const choices = [['valid', '맞는 지적'], ['false_positive', '잘못된 지적']];
  const buttons = choices.map(([verdict, label]) => {
    const element = button(label, async () => {
      for (const other of buttons) other.disabled = true;
      try {
        const response = await api.feedbackReview({ projectId: context.projectId, findingId: finding.findingId, expectedVersion: version,
          verdict, note: finding.feedback.note ?? '', idempotencyKey: crypto.randomUUID() });
        const saved = response.findings.find(item => item.findingId === finding.findingId);
        if (saved) { version = saved.feedback.version; mark(saved.feedback.verdict); }
      } catch (error) { idle(`판정을 저장하지 못했습니다 (${code(error)}).`); }
      finally { for (const other of buttons) other.disabled = false; }
    });
    element.dataset.verdict = verdict;
    return element;
  });
  const mark = current => { for (const element of buttons) element.setAttribute('aria-pressed', String(element.dataset.verdict === current)); };
  mark(finding.feedback.verdict);
  group.append(node('span', '사람 판정', 'ws-assist-label'), ...buttons);
  return group;
}

/* ---------- refactor → proposal (diff) ---------- */
async function refactor(context, purpose, { again = false } = {}) {
  const mine = ++turn; clearTimeout(timer); root.hidden = false;
  const keep = [...body.children].filter(element => element.classList.contains('ws-assist-card'));
  const holder = node('section', undefined, 'ws-assist-fix');
  body.replaceChildren(holder, ...keep);
  holder.scrollIntoView?.({ block: 'nearest' });
  busy('AI가 수정안을 만들고 있습니다… 보통 30초~1분 걸립니다.');
  const retry = () => refactor(context, purpose, { again: true });
  try {
    // Another card may have been applied since the review; the fix must start from the file as it is now.
    const current = await freshSnapshot(context);
    if (mine !== turn) return;
    const { run, reused } = await startRun(current, 'refactor', purpose, { again });
    const value = await finish(current, run, mine);
    if (!value || mine !== turn) return;
    if (value.errorCode === 'PATCH_NO_CHANGE') {
      // Typically an earlier card's fix already covered this one; asking again would only cost money.
      idle(); const box = node('div', undefined, 'ws-banner ws-banner-info');
      box.append(node('span', explain('PATCH_NO_CHANGE'))); holder.replaceChildren(box); return;
    }
    if (value.status !== 'succeeded' || !value.refactor) {
      idle(); const box = node('div', undefined, 'ws-banner ws-banner-warn');
      box.append(node('span', explain(value.errorCode || value.status)), button('다시 실행 (비용이 다시 듭니다)', retry));
      holder.replaceChildren(...(reused ? [reusedNote(retry)] : []), box); return;
    }
    const proposal = await api.createProposal({ projectId: context.projectId, refactorRunId: value.runId, idempotencyKey: crypto.randomUUID() });
    if (mine !== turn) return;
    window.dispatchEvent(new CustomEvent('workspace-proposal-selected', { detail: proposal }));
    idle('수정안이 준비됐습니다. 바뀌는 줄을 확인하고 적용하세요.');
    holder.replaceChildren(...(reused ? [reusedNote(retry)] : []), proposalView(context, proposal, mine));
  } catch (error) {
    if (mine !== turn) return;
    idle(); const box = node('div', undefined, 'ws-banner ws-banner-warn');
    box.append(node('span', explain(code(error))), button('다시 실행 (비용이 다시 듭니다)', retry));
    holder.replaceChildren(box);
  }
}

/** Re-reads the reviewed files whole so a refactor never starts from content that an earlier apply replaced. */
async function freshSnapshot(context) {
  const paths = [...new Set(context.snapshot.files.filter(file => file.state === 'read').map(file => file.path))];
  if (!paths.length) return context;
  const snapshot = await api.captureChanges({ projectId: context.projectId, items: paths.map(path => ({ facet: 'selected', path })),
    includeUntracked: false, idempotencyKey: crypto.randomUUID() });
  return { projectId: context.projectId, snapshot };
}

function diffView(change) {
  const wrap = node('div', undefined, 'ws-assist-diff');
  wrap.append(node('p', change.path, 'ws-assist-where'));
  const pre = node('pre', undefined, 'ws-assist-code');
  for (const line of change.patch.split('\n').slice(2)) {
    if (!line) continue;
    const kind = line.startsWith('@@') ? 'hunk' : line[0] === '+' ? 'add' : line[0] === '-' ? 'del' : 'ctx';
    const row = node('span', `${line}\n`, `ws-diff-${kind}`); pre.append(row);
  }
  wrap.append(pre);
  return wrap;
}

function proposalView(context, proposal, mine) {
  const card = node('article', undefined, 'ws-assist-card ws-assist-proposal');
  card.append(node('h3', 'AI 수정안'));
  if (proposal.expectedEffects.length) card.append(node('p', `예상 효과 (예측): ${proposal.expectedEffects.join(' ')}`, 'ws-assist-row'));
  if (proposal.risks.length) card.append(node('p', `주의: ${proposal.risks.join(' ')}`, 'ws-assist-row'));
  for (const change of proposal.changes) card.append(diffView(change));
  const actions = node('div', undefined, 'ws-toolbar');
  const apply = button('적용', () => { void applyProposal(context, proposal, card, actions, mine); }, 'btn-primary'); apply.dataset.risk = 'write';
  const reject = button('거절', async () => {
    try { await api.rejectProposal({ projectId: context.projectId, proposalId: proposal.proposalId, expectedVersion: proposal.version, idempotencyKey: crypto.randomUUID() }); actions.replaceChildren(node('p', '이 수정안을 거절했습니다. 파일은 바뀌지 않았습니다.', 'ws-assist-row')); idle(); }
    catch (error) { idle(`거절하지 못했습니다 (${code(error)}).`); }
  });
  actions.append(apply, reject, node('span', '적용하면 파일만 바뀌고 스테이징·커밋은 하지 않습니다.', 'ws-assist-hint'));
  card.append(actions);
  return card;
}

/* ---------- apply: tests before → apply → tests after → impact ---------- */
function steps(labels) {
  const list = node('ol', undefined, 'ws-assist-steps');
  const items = labels.map(label => { const item = node('li', label); item.dataset.state = 'wait'; list.append(item); return item; });
  return { list, set(index, state, text) { items[index].dataset.state = state; if (text) items[index].textContent = text; } };
}

async function applyProposal(context, proposal, card, actions, mine) {
  for (const element of actions.querySelectorAll('button')) element.disabled = true;
  const progress = steps(['적용 전 테스트', '파일에 적용', '적용 후 테스트', '영향 범위 찾기']);
  actions.replaceChildren(progress.list);
  busy('적용하고 확인하는 중입니다…');
  const { projectId } = proposal;
  try {
    progress.set(0, 'run');
    const before = await runTests(projectId, proposal.snapshotId);
    if (mine !== turn) return;
    progress.set(0, before.run ? 'done' : 'skip', before.run ? `적용 전 테스트: ${testText(before)}` : `적용 전 테스트 건너뜀 · ${before.reason}`);

    progress.set(1, 'run');
    const accepted = await api.approveProposal({ projectId, proposalId: proposal.proposalId, expectedVersion: proposal.version,
      patchHash: proposal.patchHash, preconditionsHash: proposal.preconditionsHash, idempotencyKey: crypto.randomUUID() });
    if (!accepted) { progress.set(1, 'skip', '적용을 취소했습니다.'); idle(); return; }
    const applied = await api.applyProposal({ projectId, proposalId: proposal.proposalId, approvalId: accepted.approvalId, idempotencyKey: crypto.randomUUID() });
    if (mine !== turn) return;
    window.dispatchEvent(new CustomEvent('workspace-proposal-selected', { detail: applied }));
    if (applied.status !== 'applied') { progress.set(1, 'fail', '적용을 끝내지 못했습니다. 고급 → 개선 제안에서 복구 상태를 확인하세요.'); idle(); return; }
    progress.set(1, 'done', `파일에 적용했습니다 (${applied.changes.map(change => change.path).join(', ')}).`);

    progress.set(2, 'run');
    const after = await afterSnapshot(projectId, applied);
    const tested = before.run ? await runTests(projectId, after.snapshotId) : { reason: before.reason };
    if (mine !== turn) return;
    progress.set(2, tested.run ? 'done' : 'skip', tested.run ? `적용 후 테스트: ${testText(tested)}` : `적용 후 테스트 건너뜀 · ${tested.reason}`);
    let comparison = null;
    try {
      comparison = await api.createComparison({ projectId, beforeSnapshotId: proposal.snapshotId, afterSnapshotId: after.snapshotId,
        proposalId: proposal.proposalId, beforeRunId: tested.run ? before.run.runId : null, afterRunId: tested.run ? tested.run.runId : null, idempotencyKey: crypto.randomUUID() });
      window.dispatchEvent(new CustomEvent('workspace-comparison-ready', { detail: { projectId, value: comparison } }));
    } catch { comparison = null; }

    progress.set(3, 'run');
    let impact;
    try { impact = await findImpact(projectId, applied); } catch (error) { impact = { error: code(error) }; }
    if (mine !== turn) return;
    progress.set(3, impact.error ? 'skip' : 'done', impact.error ? `영향 범위를 찾지 못했습니다 (${impact.error}).` : `영향 범위: 호출하는 곳 ${impact.callers.length}곳`);
    idle('적용했습니다. 아래에서 테스트 결과와 영향 범위를 확인하세요.');
    card.after(summaryView(before, tested, comparison, impact));
  } catch (error) {
    if (mine !== turn) return;
    const reason = code(error);
    idle(reason === 'PRECONDITION_FAILED' ? '수정안을 만든 뒤 파일이 바뀌어 적용하지 않았습니다. 다시 실행해 새 수정안을 받으세요.' : explain(reason));
  }
}

/** Captures the applied files again so the after-test and the comparison read the written content. */
async function afterSnapshot(projectId, applied) {
  const paths = new Set(applied.changes.map(change => change.path));
  const state = await api.getGitStatus({ projectId, recentLimit: 1 });
  const items = [];
  for (const facet of ['unstaged', 'staged', 'untracked']) for (const file of state[facet]) if (paths.has(file.path) && !items.some(item => item.path === file.path)) items.push({ facet, path: file.path });
  if (!items.length) throw new Error('NO_CHANGES_AFTER_APPLY');
  const value = await api.captureChanges({ projectId, items, includeUntracked: items.some(item => item.facet === 'untracked'), idempotencyKey: crypto.randomUUID() });
  window.dispatchEvent(new CustomEvent('workspace-snapshot-changed', { detail: value }));
  return value;
}

/* ---------- tests: found once per project, the native trust prompt appears on the first run only ---------- */
async function testCandidate(projectId) {
  if (candidates.has(projectId)) return candidates.get(projectId);
  const detected = await api.detectValidation({ projectId, cwdRelative: '.', packageManager: null });
  const usable = detected.candidates.filter(item => item.available);
  const chosen = usable.find(item => /test/i.test(item.name) && !/lint/i.test(item.name)) ?? null;
  candidates.set(projectId, chosen);
  return chosen;
}
async function runTests(projectId, snapshotId) {
  let candidate;
  try { candidate = await testCandidate(projectId); } catch (error) { return { reason: `테스트 도구를 찾지 못했습니다 (${code(error)})` }; }
  if (!candidate) return { reason: '테스트 도구를 찾지 못했습니다' };
  try {
    const plan = await api.planValidation({ projectId, snapshotId, candidateId: candidate.candidateId, idempotencyKey: crypto.randomUUID() });
    let run = await api.runValidation({ projectId, planId: plan.planId, idempotencyKey: crypto.randomUUID() });
    if (!run) return { reason: '테스트 실행을 허용하지 않았습니다' };
    for (let i = 0; run.status === 'running' && i < 1200; i++) { await pause(500); run = await api.getValidation({ projectId, runId: run.runId, offset: 0, limit: 10 }); }
    let text = ''; let page = run;
    for (let i = 0; i < 200; i++) {
      text += page.output.map(item => item.text).join('');
      if (page.nextOffset === null) break;
      page = await api.getValidation({ projectId, runId: run.runId, offset: page.nextOffset, limit: 10 });
    }
    window.dispatchEvent(new CustomEvent('workspace-validation-finished', { detail: run }));
    return { run, counts: countTests(text) };
  } catch (error) { return { reason: `테스트를 실행하지 못했습니다 (${code(error)})` }; }
}
/** Reads the runner's own summary line (pytest, unittest, Jest/Vitest style); missing numbers stay unknown. */
export function countTests(text) {
  const tail = text.slice(-4000);
  const pick = pattern => { const all = [...tail.matchAll(pattern)]; return all.length ? Number(all.at(-1)[1]) : null; };
  const failed = pick(/(\d+)\s+failed/gi), passed = pick(/(\d+)\s+passed/gi), errors = pick(/(\d+)\s+errors?\b/gi);
  const unittest = tail.match(/Ran (\d+) tests?[\s\S]*?(OK|FAILED \((?:failures=(\d+))?(?:, )?(?:errors=(\d+))?\))/);
  if (failed === null && passed === null && unittest) {
    const bad = Number(unittest[3] ?? 0) + Number(unittest[4] ?? 0);
    return { failed: bad, passed: Number(unittest[1]) - bad, errors: 0 };
  }
  return { failed, passed, errors };
}
function testText(result) {
  const { counts, run } = result;
  const parts = [];
  if (counts?.failed) parts.push(`${counts.failed}개 실패`);
  if (counts?.errors) parts.push(`${counts.errors}개 오류`);
  if (counts?.passed !== null && counts?.passed !== undefined) parts.push(`${counts.passed}개 통과`);
  if (!parts.length) parts.push(run.status === 'succeeded' ? '통과' : '실패');
  return parts.join(' · ');
}
const green = result => result.run?.status === 'succeeded' && !result.counts?.failed && !result.counts?.errors;

/* ---------- impact: who calls the functions the patch touched ---------- */
const DEFINITION = [/^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)/, /^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)/,
  /^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>/];
const definitionName = text => { for (const pattern of DEFINITION) { const match = text.match(pattern); if (match) return match[1]; } return null; };

async function fileLines(projectId, snapshotId, fileId) {
  const lines = [];
  for (let start = 1; start < 20000; start += 200) {
    const page = await api.previewFile({ projectId, snapshotId, fileId, side: 'RIGHT', startLine: start, endLine: start + 199 });
    for (const line of page.lines) if (line.kind !== 'removed') lines[line.line] = line.text;
    if (page.lines.length < 200) break;
  }
  return lines;
}
/** Nearest definition above a line, with the decorator lines right above it (e.g. a Flask route). */
function enclosing(lines, lineNumber) {
  for (let at = Math.min(lineNumber, lines.length - 1); at >= 1; at--) {
    const name = lines[at] === undefined ? null : definitionName(lines[at]);
    if (!name) continue;
    const decorators = [];
    for (let up = at - 1; up >= 1 && /^\s*@/.test(lines[up] ?? ''); up--) decorators.unshift(lines[up].trim());
    return { name, line: at, decorators };
  }
  return null;
}
function changedLines(patch) {
  const result = []; let old = 0;
  for (const line of patch.split('\n').slice(2)) {
    const hunk = line.match(/^@@ -(\d+)/);
    if (hunk) { old = Number(hunk[1]); continue; }
    if (line.startsWith('-')) { result.push(old); old++; } else if (line.startsWith('+')) result.push(Math.max(1, old - 1)); else if (line) old++;
  }
  return result;
}

async function findImpact(projectId, applied) {
  const changedPaths = new Set(applied.changes.map(change => change.path));
  const names = new Set();
  for (const change of applied.changes) {
    let lines = [];
    try { lines = await fileLines(projectId, applied.snapshotId, change.fileId); } catch { lines = []; }
    for (const at of changedLines(change.patch)) { const found = enclosing(lines, at); if (found) names.add(found.name); }
    for (const line of change.patch.split('\n')) { const name = definitionName(line.slice(1)); if (name && /^[ +-]/.test(line)) names.add(name); }
  }
  // Files that changed together with the patched files in local Git history are the first place to look.
  const partners = [];
  try {
    const history = await api.getChangeHistory({ projectId });
    for (const pair of history.pairs) {
      const other = changedPaths.has(pair.fileA) ? pair.fileB : changedPaths.has(pair.fileB) ? pair.fileA : null;
      if (other && !changedPaths.has(other) && !partners.some(item => item.path === other)) partners.push({ path: other, together: pair.coChangeCount, score: pair.score });
    }
  } catch { /* History is optional evidence. */ }
  const scan = partners.slice(0, 8).map(item => item.path);
  // Neighbours in the same folders with the same file type cover callers that never changed together.
  for (const path of changedPaths) {
    const directory = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
    const extension = path.slice(path.lastIndexOf('.'));
    try {
      const tree = await api.getFileTree({ projectId, directory });
      for (const entry of tree.entries) if (entry.kind === 'file' && entry.path.endsWith(extension) && !changedPaths.has(entry.path) && !scan.includes(entry.path) && entry.size <= 200000) scan.push(entry.path);
    } catch { /* Folder listing is best effort. */ }
  }
  const callers = [];
  if (names.size && scan.length) {
    const snapshot = await api.captureChanges({ projectId, items: scan.slice(0, 12).map(path => ({ facet: 'selected', path })), includeUntracked: false, idempotencyKey: crypto.randomUUID() });
    let page = await api.analyzeImpact({ projectId, snapshotId: snapshot.snapshotId });
    const records = [...page.records];
    for (let i = 0; page.nextOffset !== null && i < 10; i++) { page = await api.getImpact({ projectId, analysisId: page.analysisId, offset: page.nextOffset }); records.push(...page.records); }
    const files = new Map(snapshot.files.map(file => [file.path, file]));
    const cache = new Map();
    for (const record of records) {
      if (record.referenceKind !== 'call' || changedPaths.has(record.path)) continue;
      const name = [...names].find(candidate => record.symbol === candidate || record.symbol.endsWith(`.${candidate}`));
      if (!name) continue;
      const file = files.get(record.path);
      if (file && !cache.has(record.path)) { try { cache.set(record.path, await fileLines(projectId, snapshot.snapshotId, file.fileId)); } catch { cache.set(record.path, []); } }
      callers.push({ path: record.path, line: record.line, symbol: record.symbol, inside: enclosing(cache.get(record.path) ?? [], record.line) });
    }
  }
  return { names: [...names], callers, partners: partners.slice(0, 3) };
}

/* ---------- summary card ---------- */
function summaryView(before, after, comparison, impact) {
  const card = node('article', undefined, 'ws-assist-card ws-assist-summary');
  card.append(node('h3', '적용 결과'));
  if (comparison) card.append(node('p', `바뀐 파일 ${comparison.changedFiles}개 · 추가 ${comparison.additions ?? '?'}줄 · 삭제 ${comparison.deletions ?? '?'}줄 · 스테이징·커밋은 하지 않았습니다.`, 'ws-assist-row'));

  const tests = node('div', undefined, 'ws-assist-tests');
  tests.append(node('span', '테스트', 'ws-assist-label'));
  if (before.run && after.run) {
    tests.append(chip(`적용 전 ${testText(before)}`, green(before) ? 'ok' : 'error'), node('span', '→', 'ws-assist-arrow'), chip(`적용 후 ${testText(after)}`, green(after) ? 'ok' : 'error'));
    if (!green(before) && green(after)) tests.append(node('span', '실패하던 테스트가 이제 통과합니다.', 'ws-assist-good'));
    else if (green(before) && !green(after)) tests.append(node('span', '적용 뒤 실패하는 테스트가 생겼습니다. 바뀐 줄을 확인하세요.', 'ws-assist-bad'));
  } else tests.append(node('span', after.reason || before.reason || '실행하지 않았습니다.'));
  card.append(tests);

  const reach = node('div', undefined, 'ws-assist-impact');
  reach.append(node('span', '영향 범위', 'ws-assist-label'));
  if (impact.error) reach.append(node('span', `찾지 못했습니다 (${impact.error}).`));
  else {
    if (impact.names.length) reach.append(node('p', `고친 함수: ${impact.names.map(name => `${name}()`).join(', ')}`, 'ws-assist-row'));
    if (impact.callers.length) {
      const list = node('ul', undefined, 'ws-assist-callers');
      for (const caller of impact.callers) {
        const item = node('li');
        item.append(node('strong', `${caller.path} ${caller.line}행`), document.createTextNode(` · ${caller.inside ? `${caller.inside.name}() 안에서` : ''} ${caller.symbol}() 호출`));
        for (const decorator of caller.inside?.decorators ?? []) item.append(node('code', decorator));
        list.append(item);
      }
      reach.append(list);
    } else reach.append(node('p', '이 함수를 부르는 곳을 찾지 못했습니다. 동적 호출이나 다른 폴더의 호출은 찾지 못할 수 있습니다.', 'ws-assist-row'));
    if (impact.partners.length) reach.append(node('p', `Git 이력에서 자주 같이 바뀐 파일: ${impact.partners.map(item => `${item.path} (함께 ${item.together}회)`).join(', ')}`, 'ws-assist-row'));
  }
  card.append(reach, node('p', '테스트 수와 호출 위치는 실제 실행·구문 분석 결과이고, AI 수정의 효과는 예측입니다.', 'ws-assist-hint'));
  return card;
}
