/* 내 프로젝트 선택: 선택 버튼을 누르기 전에는 모델·PR 화면으로 이동하지 않는다. */
export function createProjectPicker({ loadProjects, onSelect, onLogin }) {
  let dialog = null, controller = null;
  const node = (tag, text, cls) => {
    const el = document.createElement(tag);
    if (text) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  };
  const action = (label, fn, cls = 'btn-secondary ai-btn') => {
    const el = node('button', label, cls);
    el.type = 'button'; el.addEventListener('click', fn); return el;
  };
  function close() {
    controller?.abort(); controller = null;
    const old = dialog; dialog = null;
    if (old) { old.close(); old.remove(); }
  }
  function open() {
    if (dialog) return;
    const current = node('dialog', null, 'ai-project-dialog');
    dialog = current;
    current.setAttribute('aria-labelledby', 'ai-project-title');
    current.setAttribute('aria-describedby', 'ai-project-description');
    const title = node('h2', '내 분석 프로젝트'); title.id = 'ai-project-title';
    const description = node('p', '리뷰할 프로젝트의 AI PR 리뷰 버튼을 누르면 모델을 선택할 수 있습니다.', 'ai-muted');
    description.id = 'ai-project-description';
    const header = node('div', null, 'ai-row between');
    header.append(title, action('닫기', close));
    const list = node('div', null, 'ai-project-list'); list.setAttribute('aria-live', 'polite');
    current.append(header, description, list);
    current.addEventListener('cancel', (ev) => { ev.preventDefault(); close(); });
    current.addEventListener('click', (ev) => { if (ev.target === current) close(); });
    document.body.append(current); current.showModal();
    async function refresh() {
      controller?.abort();
      const request = new AbortController(); controller = request;
      list.replaceChildren(node('p', '프로젝트를 불러오는 중…', 'ai-muted'));
      try {
        const projects = await loadProjects(request.signal);
        if (dialog !== current || request.signal.aborted) return;
        list.replaceChildren();
        if (!projects.length) {
          list.append(node('p', '아직 분석을 요청한 프로젝트가 없습니다.', 'ai-empty'));
          const search = node('a', '저장소 검색', 'btn-primary ai-btn');
          search.href = './search.html'; list.append(search); return;
        }
        const labels = { done: '분석 완료', running: '분석 중', pending: '분석 대기',
          rate_limited: '수집 대기', failed: '분석 실패', cancelled: '분석 교체됨' };
        for (const project of projects) {
          const row = node('article', null, 'ai-project-row');
          const info = node('div', null, 'ai-project-info');
          const name = (project.url || '').replace(/^https:\/\/github\.com\//, '').replace(/\.git\/?$/, '')
            || project.name || `저장소 #${project.id}`;
          info.append(node('h3', name), node('p', [labels[project.status] || '분석 상태 미확인',
            project.language, project.default_branch].filter(Boolean).join(' · '), 'ai-muted'));
          row.append(info, action('AI PR 리뷰', () => { close(); onSelect(project.id); }, 'btn-primary ai-btn'));
          list.append(row);
        }
      } catch (err) {
        if (dialog !== current || request.signal.aborted) return;
        list.replaceChildren(node('p', err.status === 401 ? '로그인 후 내 프로젝트를 선택할 수 있습니다.'
          : '프로젝트 목록을 불러오지 못했습니다.', 'ai-empty'));
        list.append(err.status === 401 ? action('GitHub로 로그인', () => { close(); onLogin(); }, 'btn-primary ai-btn')
          : action('다시 시도', refresh));
      }
    }
    refresh();
  }
  return { open, close };
}
