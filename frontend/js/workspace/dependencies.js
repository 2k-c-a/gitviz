// 시스템 환경 확인(시작하기 화면): 구성 요소 검사 → 빠진 항목 설치 → 다시 시작. 앱 전용(window.workspace가 있을 때만 동작).
const api = window.workspace;
const panel = document.getElementById('ws-deps');

const STATUS = {
  ok: { label: '정상 설치', tone: 'ok' },
  missing: { label: '미설치', tone: 'error' },
  outdated: { label: '버전 불일치', tone: 'warn' },
  unsupported: { label: '지원하지 않음', tone: 'error' },
};
const STEPS = [['system', '시스템 환경 확인'], ['check', '의존성 검사'], ['install', '누락된 의존성 설치'], ['done', '준비 완료']];
const STEP_STATE = { wait: ['대기', ''], run: ['진행 중', 'warn'], ok: ['완료', 'ok'], skip: ['필요 없음', 'ok'], error: ['오류', 'error'] };
const ERRORS = {
  DOWNLOAD_FAILED: '설치 파일을 내려받지 못했습니다. 인터넷 연결(회사 네트워크라면 github.com 접근 허용)을 확인한 뒤 다시 시도하세요.',
  INTEGRITY_FAILED: '내려받은 파일이 공식 배포본과 일치하지 않아 설치하지 않았습니다. 잠시 뒤 다시 시도하거나 공식 다운로드 페이지에서 직접 설치하세요.',
  INSTALL_FAILED: 'Git 설치 프로그램이 실패했습니다. 공식 다운로드 페이지에서 직접 설치할 수 있습니다.',
  INSTALL_TIMEOUT: 'Git 설치가 15분 안에 끝나지 않아 중단했습니다. 다시 시도하거나 직접 설치하세요.',
  NOT_DETECTED: '설치는 끝났지만 Git을 찾지 못했습니다. 앱을 다시 시작해 보고, 그래도 안 되면 직접 설치하세요.',
  NO_USER_DIRECTORY: '사용자 프로그램 폴더(%LOCALAPPDATA%)를 찾을 수 없어 설치할 수 없습니다.',
  BUSY: '이미 설치를 진행하고 있습니다.',
};
const STAGES = { download: '공식 배포본 내려받는 중', verify: '무결성(SHA-256) 확인 중', install: 'Git for Windows 설치 중 (내 계정에만)', check: '설치 결과 확인 중' };

const $ = id => document.getElementById(id);
const code = error => String(error?.message ?? error).replace(/^.*Error invoking remote method.*Error: /, '');
const mb = bytes => `${(bytes / 1048576).toFixed(1)}MB`;

function chip(text, tone) {
  const span = document.createElement('span');
  span.className = 'ws-chip';
  if (tone) span.dataset.tone = tone;
  span.textContent = text;
  return span;
}

function steps(states) {
  $('ws-deps-steps').replaceChildren(...STEPS.map(([id, name], index) => {
    const [label, tone] = STEP_STATE[states[id] ?? 'wait'];
    const li = document.createElement('li');
    const num = document.createElement('span'); num.className = 'ws-num'; num.setAttribute('aria-hidden', 'true'); num.textContent = String(index + 1);
    const title = document.createElement('span'); title.className = 'ws-grow'; title.textContent = name;
    li.append(num, title, chip(label, tone));
    return li;
  }));
}

function list(report) {
  $('ws-deps-list').replaceChildren(...report.items.map(item => {
    const li = document.createElement('li');
    const body = document.createElement('span'); body.className = 'ws-grow';
    const name = document.createElement('strong'); name.textContent = item.name;
    const detail = document.createElement('span'); detail.className = 'ws-note';
    detail.textContent = [item.version ? `현재 ${item.version}` : '', `필요 ${item.expected}`, item.note].filter(Boolean).join(' · ');
    body.append(name, document.createElement('br'), detail);
    const status = STATUS[item.status] ?? STATUS.missing;
    li.append(body, chip(status.label, status.tone));
    if (item.action === 'install') li.append(chip('설치 필요', 'warn'));
    return li;
  }));
}

function render(report, installed = false) {
  list(report);
  const pending = report.items.filter(item => item.required && item.status !== 'ok');
  const installable = pending.filter(item => item.action === 'install');
  const blocked = pending.filter(item => item.action !== 'install');
  const system = report.items.filter(item => item.id === 'os' || item.id === 'arch').every(item => item.status === 'ok');
  steps({ system: system ? 'ok' : 'error', check: 'ok', install: installed ? 'ok' : installable.length ? 'wait' : 'skip', done: report.ready ? 'ok' : 'wait' });
  $('ws-deps-install').hidden = !installable.length || blocked.length > 0;
  $('ws-deps-download').hidden = !installable.length;
  $('ws-deps-relaunch').hidden = !installed;
  $('ws-deps-details').open = !report.ready;
  $('ws-deps-summary').textContent = installed
    ? '설치를 마쳤습니다. 앱을 다시 시작하면 로컬 프로젝트를 사용할 수 있습니다.'
    : report.ready ? '필요한 구성 요소가 모두 준비됐습니다.'
      : blocked.length ? `이 PC에서는 실행할 수 없는 조건이 있습니다: ${blocked.map(item => item.name).join(', ')}. 자세히 보기를 확인하세요.`
        : `설치가 필요한 항목이 ${installable.length}개 있습니다: ${installable.map(item => item.name).join(', ')}. '필요한 항목 설치'를 누르세요.`;
}

async function check() {
  $('ws-deps-recheck').disabled = true;
  steps({ system: 'run' });
  try { render(await api.checkDependencies()); }
  catch (error) { steps({ system: 'error' }); $('ws-deps-summary').textContent = `환경을 확인하지 못했습니다(${code(error)}). 다시 확인을 눌러 주세요.`; }
  finally { $('ws-deps-recheck').disabled = false; }
}

async function install() {
  const bar = $('ws-deps-progress');
  for (const id of ['ws-deps-install', 'ws-deps-recheck']) $(id).disabled = true;
  steps({ system: 'ok', check: 'ok', install: 'run' });
  bar.hidden = false; bar.removeAttribute('value');
  try {
    const report = await api.installDependency({ id: 'git' }, progress => {
      const text = STAGES[progress?.stage] ?? '설치 중';
      if (progress?.stage === 'download' && progress.total) {
        bar.max = progress.total; bar.value = progress.received ?? 0;
        $('ws-deps-summary').textContent = `${text} · ${mb(progress.received ?? 0)} / ${mb(progress.total)}`;
      } else { bar.removeAttribute('value'); $('ws-deps-summary').textContent = `${text}…`; }
    });
    render(report, true);
  } catch (error) {
    steps({ system: 'ok', check: 'ok', install: 'error' });
    const reason = code(error);
    $('ws-deps-summary').textContent = ERRORS[reason] ?? `설치하지 못했습니다(${reason}).`;
    $('ws-deps-download').hidden = false;
  } finally {
    bar.hidden = true;
    for (const id of ['ws-deps-install', 'ws-deps-recheck']) $(id).disabled = false;
  }
}

if (panel && api?.checkDependencies) {
  $('ws-deps-recheck').addEventListener('click', () => { void check(); });
  $('ws-deps-install').addEventListener('click', () => { void install(); });
  $('ws-deps-relaunch').addEventListener('click', () => { void api.relaunchApp().catch(error => { $('ws-deps-summary').textContent = ERRORS[code(error)] ?? code(error); }); });
  $('ws-deps-download').addEventListener('click', () => { void api.openGitDownload().catch(() => {}); });
  void check();
} else if (panel) panel.hidden = true;
