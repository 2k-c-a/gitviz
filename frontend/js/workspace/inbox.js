import { openTarget } from './navigation.js';

const api = window.workspace;
const list = document.getElementById('inbox-list');
const message = document.getElementById('inbox-message');
const detail = document.getElementById('inbox-detail');
const unread = document.getElementById('inbox-unread');
const more = document.getElementById('inbox-more');
const filterProject = document.getElementById('inbox-project');
const filterSource = document.getElementById('inbox-source');
const filterSeverity = document.getElementById('inbox-severity');
const filterRead = document.getElementById('inbox-read');
let cursor = null;
let generation = 0;
let refreshTimer = null;

const TYPES = {
  analysis: ['analysis.succeeded', 'analysis.failed'],
  review: ['review.succeeded', 'review.failed', 'review.cancelled', 'review.uncertain'],
  validation: ['validation.succeeded', 'validation.failed', 'validation.cancelled', 'validation.timed_out', 'validation.output_limit', 'validation.interrupted', 'validation.launch_failed', 'validation.precondition_failed'],
  proposal: ['proposal.applied', 'proposal.recovery_required', 'proposal.restored'],
};
const LABELS = {
  'analysis.succeeded': '서버 분석 완료', 'analysis.failed': '서버 분석 실패',
  'review.succeeded': '로컬 AI 리뷰 완료', 'review.failed': '로컬 AI 리뷰 실패', 'review.cancelled': '로컬 AI 리뷰 취소', 'review.uncertain': '로컬 AI 리뷰 결과 불명',
  'validation.succeeded': '검증 명령 성공', 'validation.failed': '검증 명령 실패', 'validation.cancelled': '검증 취소', 'validation.timed_out': '검증 시간 초과',
  'validation.output_limit': '검증 출력 상한 도달', 'validation.interrupted': '검증 중단(앱 재시작)', 'validation.launch_failed': '검증 실행 실패',
  'validation.precondition_failed': '검증 전제 조건 불일치', 'proposal.applied': '제안 적용 완료', 'proposal.recovery_required': '제안 적용 복구 필요', 'proposal.restored': '제안 복구 완료',
};
const SEVERITY = { error: '오류', warning: '주의', info: '정보' };

function node(tag, text) { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; return element; }
function fail(error) { message.textContent = String(error?.message ?? error).replace(/^.*Error invoking remote method.*Error: /, ''); }
function cause(item) {
  const s = item.summary;
  if (item.causeCode === 'ai-findings') return `AI 추론 지적 ${s.findings}건 · 최고 ${s.highestFindingSeverity ?? 'unknown'} · 실행으로 검증한 결함 아님`;
  if (item.causeCode === 'ai-no-findings') return 'AI 추론 지적 0건 · 결함 없음을 뜻하지 않음';
  if (item.causeCode === 'ai-refactor-ready') return '리팩토링 제안 생성 · 파일 쓰기는 별도 승인';
  if (item.causeCode === 'command-nonzero-exit') return `결정적 명령 실패 · 종료 코드 ${s.exitCode ?? '없음'}`;
  if (item.causeCode === 'command-exit-zero') return '결정적 명령 종료 코드 0';
  return `원인 코드 ${item.causeCode}`;
}
function filters() {
  return { projectIds: filterProject.value ? [filterProject.value] : null, types: filterSource.value ? TYPES[filterSource.value] : null,
    severities: filterSeverity.value ? [filterSeverity.value] : null, read: filterRead.value };
}
function syncProjects() {
  const selected = filterProject.value;
  filterProject.replaceChildren(new Option('모든 프로젝트', ''));
  for (const option of document.getElementById('project-picker').options) if (option.value) filterProject.add(new Option(option.textContent, option.value));
  if ([...filterProject.options].some(option => option.value === selected)) filterProject.value = selected;
}
function row(item) {
  const entry = node('li');
  const when = new Date(item.occurredAt * 1000).toLocaleString();
  entry.append(node('strong', `[${SEVERITY[item.severity]}] ${LABELS[item.type] ?? item.type}`),
    node('span', ` · ${item.projectName} · ${cause(item)} · ${when}${item.count > 1 ? ` · ${item.count}회 수신` : ''}${item.readAt ? ' · 읽음' : ''}`));
  const open = node('button', item.targetState === 'available' ? '대상으로 이동' : '대상 없음');
  open.type = 'button'; open.disabled = item.targetState !== 'available';
  open.addEventListener('click', async () => { message.textContent = await openTarget(item, detail); });
  const toggle = node('button', item.readAt ? '안 읽음으로' : '읽음으로');
  toggle.type = 'button';
  toggle.addEventListener('click', async () => {
    const turn = generation;
    try { await api.markNotificationRead({ projectId: item.projectId, notificationId: item.notificationId, expectedVersion: item.version, read: !item.readAt, idempotencyKey: crypto.randomUUID() }); if (turn === generation) await load(); }
    catch (error) { if (turn === generation) { fail(error); await load(); } }
  });
  entry.append(open, toggle);
  return entry;
}
async function load(append = false) {
  const turn = ++generation;
  try {
    const page = await api.listNotifications({ filters: filters(), cursor: append ? cursor : null, limit: 30 });
    if (turn !== generation) return;
    if (!append) { list.replaceChildren(); message.textContent = page.items.length ? '' : '표시할 알림이 없습니다.'; }
    for (const item of page.items) list.append(row(item));
    cursor = page.nextCursor; more.hidden = !cursor;
    unread.textContent = `읽지 않은 알림 ${page.unreadCount}건`;
  } catch (error) { if (turn === generation) fail(error); }
}
function scheduleRefresh() { clearTimeout(refreshTimer); refreshTimer = setTimeout(() => { void load(); }, 300); }

if (api?.listNotifications) {
  for (const element of [filterProject, filterSource, filterSeverity, filterRead]) element.addEventListener('change', () => { cursor = null; void load(); });
  document.getElementById('inbox-refresh').addEventListener('click', () => { syncProjects(); void load(); });
  more.addEventListener('click', () => { void load(true); });
  window.addEventListener('workspace-project-changed', syncProjects);
  void (async () => {
    for (let attempt = 0; attempt < 30; attempt++) {
      try {
        if ((await api.getHealth()).state === 'ready') {
          syncProjects();
          await api.subscribeEvents({ afterSequence: 0 }, () => scheduleRefresh());
          await load();
          return;
        }
      } catch { /* A manual refresh can retry. */ }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    message.textContent = '알림을 불러오지 못했습니다. 새로고침을 눌러 다시 시도하세요.';
  })();
  window.addEventListener('pagehide', () => { void api.unsubscribeEvents(); });
}
