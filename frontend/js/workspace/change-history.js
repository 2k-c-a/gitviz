// 변경 분석 탭: 로컬 프로젝트의 Git 이력(최근 180일)에서 자주 수정된 파일, 같이 바뀐 파일, 주별 타임라인을 보여 준다.
// 계산은 에이전트(workspace.history.analyze)가 하고, 이 모듈은 탭을 처음 열 때와 '다시 분석'을 누를 때만 요청한다.
const api = window.workspace;
const panel = document.getElementById('ws-panel-history');
const picker = document.getElementById('project-picker');
const $ = id => document.getElementById(id);

const MESSAGES = {
  GIT_NOT_FOUND: 'Git을 찾을 수 없습니다. 시작하기의 시스템 환경 확인에서 Git을 설치하세요.',
  CHANGESET_TOO_LARGE: '최근 180일 이력이 너무 커서 읽지 못했습니다.',
  OUTPUT_LIMIT: '최근 180일 이력이 너무 커서 읽지 못했습니다.',
  TIMED_OUT: '이력을 읽는 데 시간이 너무 오래 걸려 중단했습니다.',
  UNSUPPORTED_FILENAME: 'UTF-8이 아닌 파일 이름이 있어 분석할 수 없습니다.',
  UNSUPPORTED_REPOSITORY: '이 저장소 설정(include·partial clone 등)은 지원하지 않습니다.',
  METADATA_GRANT_REQUIRED: '외부 Git 메타데이터를 읽으려면 프로젝트를 다시 등록해 범위를 허용해야 합니다.',
  RESOURCE_NOT_FOUND: '프로젝트를 찾을 수 없습니다. 프로젝트 목록을 새로고침하세요.',
};
const SVG = 'http://www.w3.org/2000/svg';
let loadedFor = '';
let turn = 0;

function node(tag, text, className) { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; if (className) element.className = className; return element; }
function svg(tag, attributes) { const element = document.createElementNS(SVG, tag); for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value)); return element; }
const code = error => String(error?.message ?? error).replace(/^.*Error invoking remote method.*Error: /, '');
const pad = value => String(value).padStart(2, '0');
function day(iso) { const date = new Date(iso); return Number.isNaN(date.getTime()) ? '—' : `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`; }
const count = value => (value === null || value === undefined ? '—' : Number(value).toLocaleString('ko-KR'));
function chip(text, tone) { const span = node('span', text, 'ws-chip'); if (tone) span.dataset.tone = tone; return span; }

function table(headers, rows) {
  const element = node('table', undefined, 'ws-table');
  const head = node('thead'); const headRow = node('tr');
  for (const [label, numeric] of headers) { const th = node('th', label); th.scope = 'col'; if (numeric) th.className = 'ws-num-cell'; headRow.append(th); }
  head.append(headRow);
  const body = node('tbody');
  for (const cells of rows) {
    const tr = node('tr');
    cells.forEach((cell, index) => { const td = node('td'); if (headers[index][1]) td.className = 'ws-num-cell'; td.append(...(Array.isArray(cell) ? cell : [cell])); tr.append(td); });
    body.append(tr);
  }
  element.append(head, body);
  return element;
}

function fileButton(path, projectId) {
  const button = node('button', path, 'ws-link'); button.type = 'button';
  button.title = '기본 에디터로 열기';
  button.addEventListener('click', async () => {
    try { await api.openEditor({ projectId, path, line: 1, column: 1, editorId: $('editor-id')?.value ?? '' }); }
    catch (error) { $('history-message').textContent = code(error) === 'EDITOR_UNAVAILABLE_OR_STALE' ? '에디터를 열지 못했습니다. 설정에서 에디터를 지정했는지 확인하세요.' : `파일을 열지 못했습니다(${code(error)}).`; }
  });
  return button;
}

function timeline(weeks) {
  const box = $('history-timeline');
  if (!weeks.length) { box.replaceChildren(node('p', '표시할 기간이 없습니다.', 'ws-note')); return; }
  const width = 720, height = 160, top = 12, bottom = 24, left = 28, gap = 2;
  const max = Math.max(1, ...weeks.map(week => week.commits));
  const slot = (width - left) / weeks.length;
  const scale = value => (value / max) * (height - top - bottom);
  const chart = svg('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', class: 'ws-timeline-chart',
    'aria-label': `주별 커밋 수, 최대 ${max}개` });
  for (const value of [0, max]) {
    const y = height - bottom - scale(value);
    chart.append(svg('line', { x1: left, x2: width, y1: y, y2: y, class: 'ws-timeline-grid' }));
    const label = svg('text', { x: left - 4, y: y + 4, 'text-anchor': 'end', class: 'ws-timeline-label' }); label.textContent = String(value); chart.append(label);
  }
  let month = '';
  weeks.forEach((week, index) => {
    const x = left + index * slot + gap / 2, w = Math.max(1, slot - gap);
    const group = svg('g', {});
    const title = svg('title', {}); title.textContent = `${week.weekStart} 주 · 커밋 ${week.commits}개 · 버그 수정 ${week.bugfix}개 · 작성자 ${week.authors}명`;
    group.append(title, svg('rect', { x, width: w, y: height - bottom - scale(week.commits), height: scale(week.commits), class: 'ws-timeline-bar' }));
    if (week.bugfix) group.append(svg('rect', { x, width: w, y: height - bottom - scale(week.bugfix), height: scale(week.bugfix), class: 'ws-timeline-fix' }));
    chart.append(group);
    const current = week.weekStart.slice(0, 7);
    if (current !== month) {
      month = current;
      // A label that would run past the right edge is right-aligned to the edge instead of being cut off.
      const edge = x > width - 30;
      const label = svg('text', { x: edge ? width : x, y: height - 8, 'text-anchor': edge ? 'end' : 'start', class: 'ws-timeline-label' }); label.textContent = `${Number(current.slice(5))}월`; chart.append(label);
    }
  });
  box.replaceChildren(chart);
}

function render(value, projectId) {
  const parts = [`${day(value.windowStart)} ~ ${day(value.windowEnd)}`, `커밋 ${count(value.commitCount)}개`, `작성자 ${count(value.authorCount)}명`, `현재 파일 ${count(value.treeFileCount)}개`];
  if (value.excludedLargeCommits) parts.push(`파일 ${count(value.largeChangeThreshold)}개를 넘게 바꾼 대형 커밋 ${count(value.excludedLargeCommits)}개는 같이 바뀐 파일에서 제외`);
  if (value.truncated) parts.push('커밋이 너무 많아 최근 20,000개까지만 읽었습니다');
  $('history-summary').textContent = parts.join(' · ');
  timeline(value.timeline);

  const files = $('history-files');
  if (!value.files.length) files.replaceChildren(node('p', '최근 180일 동안 바뀐 현재 파일이 없습니다.', 'ws-note'));
  else {
    files.replaceChildren(table([['파일'], ['변경', true], ['변경 줄', true], ['버그 수정', true], ['작성자', true], ['마지막 변경'], ['점검 우선순위', true]],
      value.files.map(item => [fileButton(item.path, projectId), count(item.changeCount), count(item.churn), count(item.bugfixCount), count(item.authorCount), day(item.lastChangedAt),
        [document.createTextNode(item.priority === null ? '—' : String(Math.round(item.priority * 100))), ...(item.lowSupport ? [' ', chip('표본 적음', 'warn')] : [])]])));
    if (value.fileCount > value.files.length) files.append(node('p', `변경된 현재 파일 ${count(value.fileCount)}개 중 상위 ${value.files.length}개입니다.`, 'ws-note'));
  }

  const pairs = $('history-pairs');
  if (!value.pairs.length) pairs.replaceChildren(node('p', '2번 이상 함께 바뀐 파일 쌍이 없습니다.', 'ws-note'));
  else {
    pairs.replaceChildren(table([['파일 A'], ['파일 B'], ['함께 바뀜', true], ['각자 바뀜', true], ['결합도', true]],
      value.pairs.map(item => [fileButton(item.fileA, projectId), fileButton(item.fileB, projectId), count(item.coChangeCount),
        `${count(item.changeCountA)} / ${count(item.changeCountB)}`,
        [document.createTextNode(`${Math.round(item.score * 100)}%`), ...(item.lowSupport ? [' ', chip('표본 적음', 'warn')] : [])]])));
    if (value.pairCount > value.pairs.length) pairs.append(node('p', `함께 바뀐 파일 쌍 ${count(value.pairCount)}개 중 결합도 상위 ${value.pairs.length}개입니다.`, 'ws-note'));
  }
}

function clear() {
  turn++; loadedFor = '';
  for (const id of ['history-summary', 'history-timeline', 'history-files', 'history-pairs', 'history-message']) $(id).replaceChildren();
}

async function load() {
  const projectId = picker.value;
  if (!projectId) { clear(); return; }
  clear(); const mine = turn; loadedFor = projectId;
  const button = $('history-reload'); button.disabled = true;
  $('history-message').textContent = '최근 180일 이력을 읽는 중입니다…';
  try {
    const value = await api.getChangeHistory({ projectId });
    if (mine !== turn || picker.value !== projectId) return;
    $('history-message').textContent = '';
    render(value, projectId);
  } catch (error) {
    if (mine !== turn) return;
    loadedFor = '';
    const reason = code(error);
    $('history-message').textContent = MESSAGES[reason] ?? `분석하지 못했습니다(${reason}).`;
  } finally { if (mine === turn) button.disabled = false; }
}

if (panel && api?.getChangeHistory) {
  $('history-reload').addEventListener('click', () => { void load(); });
  window.addEventListener('workspace-project-changed', () => { clear(); if (!panel.hidden) void load(); });
  window.addEventListener('workspace-view-changed', event => {
    if (event.detail?.view === 'project' && event.detail.tab === 'history' && picker.value && loadedFor !== picker.value) void load();
  });
}
