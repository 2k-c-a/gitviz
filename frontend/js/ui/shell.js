import { parseRoute as parseAiRoute, buildRoute as buildAiRoute } from '../ai-state.js';
import { parseState, nextQuery } from '../viz-state.js';
import { workspaceEntryUrl } from '../platform/workspace-navigation.js';
import { element, createIcon, enhanceTooltips, observeActions } from './components.js';

const webRoot = new URL('../../', import.meta.url);
const pages = { overview: '분석 개요', timeline: '개발 활동', network: '협업 네트워크', 'code-analysis': '파일 분석', history: '분석 이력', 'ai-review': 'AI 리뷰' };
const glyphs = { overview: '▦', timeline: '▥', network: '◎', 'code-analysis': '▤', history: '◷', 'ai-review': '◇' };
export function pageUrl(page, search = location.search) {
  const url = new URL(`pages/${page}.html`, webRoot);
  const state = parseState(search);
  url.search = page === 'ai-review' ? (state.repo ? `?repo=${state.repo}` : '') : nextQuery(search);
  return url.href;
}

export function mountShell({ local = false, account = null } = {}) {
  if (document.querySelector('.app-shell')) return;
  document.documentElement.lang = 'ko';
  document.body.classList.add('app-body');
  const currentPage = local ? 'workspace' : location.pathname.match(/\/([^/]+)\.html$/)?.[1] || 'index';
  const root = element('div', 'app-shell');
  const skip = element('a', 'app-skip', '본문으로 건너뛰기'); skip.href = '#app-main';
  skip.addEventListener('click', event => { event.preventDefault(); document.getElementById('app-main')?.focus(); });
  const sidebar = element('aside', 'app-sidebar'); sidebar.id = 'app-sidebar';
  const brand = element('a', 'app-brand'); brand.href = new URL('index.html', webRoot).href;
  brand.append(element('span', 'app-brand-mark', 'G'), element('span', 'app-nav-label', 'GitViz'));
  sidebar.append(brand);
  const nav = element('nav', 'app-nav'); nav.setAttribute('aria-label', '전역 내비게이션');
  sidebar.append(nav);
  const toolbar = element('header', 'app-toolbar');
  const menu = element('button', 'app-menu', '☰'); menu.type = 'button'; menu.setAttribute('aria-label', '내비게이션 열기'); menu.setAttribute('aria-controls', sidebar.id); menu.setAttribute('aria-expanded', 'false');
  const setNavOpen = open => { root.classList.toggle('nav-open', open); menu.setAttribute('aria-expanded', String(open)); menu.setAttribute('aria-label', open ? '내비게이션 닫기' : '내비게이션 열기'); };
  const closeNav = element('button', 'app-nav-close', '×'); closeNav.type = 'button'; closeNav.setAttribute('aria-label', '내비게이션 닫기');
  closeNav.addEventListener('click', () => { setNavOpen(false); menu.focus(); }); sidebar.append(closeNav);
  menu.addEventListener('click', () => {
    const open = !root.classList.contains('nav-open'); setNavOpen(open);
    if (open) { sidebar.inert = false; nav.querySelector('a')?.focus(); }
  });
  const heading = element('div', 'app-heading');
  const mode = element('span', 'ui-badge ui-badge-info', local ? '로컬 작업' : '원격 분석');
  const repo = element('strong', 'app-repository', local ? '프로젝트를 선택하세요' : '저장소를 선택하세요'); repo.id = 'shell-repository';
  const context = element('span', 'app-context'); context.id = 'shell-context';
  heading.append(repo, context);
  const themeLabel = element('label', 'app-theme');
  const themeText = element('span', 'ui-sr-only', '화면 테마');
  const theme = element('select'); theme.id = 'shell-theme'; theme.setAttribute('aria-label', '화면 테마');
  theme.dataset.tooltip = '시스템 설정 또는 라이트·다크 테마를 선택하세요';
  for (const [value, text] of [['system', '시스템 테마'], ['light', '라이트'], ['dark', '다크']]) {
    const option = element('option', '', text); option.value = value; theme.append(option);
  }
  theme.value = document.documentElement.dataset.themePreference || 'system';
  theme.addEventListener('change', () => window.dispatchEvent(new CustomEvent('gitviz-theme-select', { detail: theme.value })));
  window.addEventListener('gitviz-theme-changed', event => { theme.value = event.detail.preference; });
  themeLabel.append(themeText, theme);
  toolbar.append(menu, mode, heading, themeLabel);
  if (account) toolbar.append(account);
  const main = local ? document.querySelector('body > main') : element('main');
  main.id = 'app-main'; main.tabIndex = -1; main.classList.add('app-main');
  if (local) main.classList.add('workspace-content');
  const footer = element('footer', 'app-statusbar');
  footer.append(element('span', '', local ? '로컬 프로젝트 · 변경과 검증' : 'GitHub 저장소 · 저장된 분석 결과'));
  const status = element('span', 'app-status-text', local ? '로컬 구성 확인 중' : '저장소 선택 전'); status.id = 'shell-status';
  footer.append(status);
  if (!local) {
    // Move the existing document; retain IDs, event handlers, forms and canvas objects.
    for (const node of [...document.body.children]) {
      if (['SCRIPT', 'STYLE'].includes(node.tagName) || ['navbar-container', 'modal-container'].includes(node.id)) continue;
      main.append(node);
    }
  }
  root.append(sidebar, toolbar, main, footer); document.body.prepend(skip, root);
  const link = (label, icon, href, selected = false, parent = nav) => {
    const a = element('a', 'app-nav-link'); a.href = href; a.title = label; a.setAttribute('aria-label', label);
    const mark = element('span', 'app-nav-icon'); mark.append(createIcon(icon)); mark.setAttribute('aria-hidden', 'true');
    a.append(mark, element('span', 'app-nav-label', label));
    if (selected) a.setAttribute('aria-current', 'page');
    parent.append(a); return a;
  };
  const group = text => nav.append(element('p', 'app-nav-group', text));
  link('저장소 탐색', '⌕', new URL('pages/search.html', webRoot).href, currentPage === 'search');
  link('최근·내 분석', '▦', new URL('pages/myprojects.html', webRoot).href, currentPage === 'myprojects');
  link('URL로 분석 시작', '↗', new URL('index.html', webRoot).href, !local && currentPage === 'index');
  const repositoryNav = element('div', 'app-repository-nav'); nav.append(repositoryNav);
  let repositoryLinks = [];
  let repositoryId;
  const buildRepositoryLinks = () => {
    const state = parseState(location.search);
    repositoryId = state.repo; repositoryNav.replaceChildren(); repositoryLinks = [];
    if (!local && state.repo) {
      repositoryNav.append(element('p', 'app-nav-group', '현재 저장소'));
      for (const [page, label] of Object.entries(pages)) {
        const a = link(label, glyphs[page], pageUrl(page), currentPage === page, repositoryNav);
        a.dataset.shellPage = page; repositoryLinks.push(a);
      }
    } else if (!local) link('AI 리뷰', '◇', pageUrl('ai-review'), currentPage === 'ai-review', repositoryNav);
  };
  buildRepositoryLinks();
  const settingsHref = () => { const url = new URL(pageUrl('settings')); if (currentPage === 'ai-review') url.searchParams.set('return', buildAiRoute(parseAiRoute(location.search))); return url.href; };
  const settingsLink = link('설정 · API Key', '⚙', settingsHref(), currentPage === 'settings');
  const workspace = local ? new URL('index.html', location.href).href : workspaceEntryUrl(location.href);
  if (workspace) {
    group('워크스페이스');
    const a = link('로컬 프로젝트·알림', '▣', workspace, local); a.dataset.workspaceEntry = '';
  }
  if (local) {
    const sectionLinks = [];
    for (const section of main.querySelectorAll(':scope > section')) {
      const title = section.querySelector('h2'); if (!title) continue;
      section.id ||= `workspace-section-${sectionLinks.length}`;
      section.classList.add('workspace-panel'); section.tabIndex = -1;
      const a = link(title.textContent, '·', '#'+section.id);
      a.addEventListener('click', event => {
        event.preventDefault(); setNavOpen(false); section.scrollIntoView({ block: 'start' }); section.focus({ preventScroll: true });
        sectionLinks.forEach(item => item.removeAttribute('aria-current')); a.setAttribute('aria-current', 'location');
      });
      sectionLinks.push(a);
    }
    const picker = document.getElementById('project-picker');
    const updateProject = () => { repo.textContent = picker?.value ? picker.selectedOptions[0].textContent : '프로젝트를 선택하세요'; };
    picker?.addEventListener('change', updateProject); updateProject();
    const health = document.getElementById('status');
    if (health) {
      status.textContent = health.textContent;
      new MutationObserver(() => { status.textContent = health.textContent; }).observe(health, { childList: true, subtree: true, characterData: true });
    }
  }
  const refreshContext = () => {
    const state = parseState(location.search);
    if (!local) {
      if (state.repo !== repositoryId) buildRepositoryLinks();
      const title = document.querySelector('#tl-repo-name, .js-repo-title, #hist-repo-title, #ai-title');
      const name = title?.dataset.repositoryLabel || title?.textContent?.trim();
      repo.textContent = state.repo ? (name && name !== '—' && name !== 'AI 리뷰' ? name : `저장소 #${state.repo}`) : '저장소를 선택하세요';
      const banner = document.getElementById('analysis-context');
      const period = banner?.querySelector('.ctx-item')?.textContent;
      context.textContent = state.snapshot ? `결과 #${state.snapshot}${period ? ` · ${period}` : ''}` : state.repo ? '저장소 맥락' : '검색 또는 최근 분석에서 선택';
      status.textContent = document.getElementById('load-error') ? '결과 조회 실패 · 본문에서 확인' : banner ? '저장 결과 조회됨 · 수집 범위는 본문 참조' : '원격 분석';
      settingsLink.href = settingsHref();
      for (const a of repositoryLinks) a.href = pageUrl(a.dataset.shellPage);
      const entry = document.querySelector('[data-workspace-entry]');
      const entryUrl = workspaceEntryUrl(location.href); if (entry && entryUrl) entry.href = entryUrl;
    }
  };
  if (!local) {
    new MutationObserver(refreshContext).observe(main, { childList: true, subtree: true, characterData: true });
    window.addEventListener('popstate', refreshContext); refreshContext();
  }
  // Keep existing route controls operable with a keyboard without replacing their action API.
  observeActions(main);
  for (const title of main.querySelectorAll('.dash-title')) {
    title.setAttribute('role', 'heading'); title.setAttribute('aria-level', '1');
    const header = title.closest('.dash-head'); header?.classList.add('ui-page-header');
    if (pages[currentPage] && header) {
      title.classList.add('ui-repo-source'); title.setAttribute('aria-hidden', 'true'); title.removeAttribute('role'); title.removeAttribute('aria-level');
      header.querySelector('.dash-sub')?.classList.add('ui-repo-source');
      title.before(element('h1', 'ui-page-title', pages[currentPage]));
    }
  }
  for (const tabs of main.querySelectorAll('.sub-tabs')) {
    tabs.remove(); // The sidebar is the single repository navigation, including the compact rail.
  }
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && root.classList.contains('nav-open')) { event.preventDefault(); setNavOpen(false); menu.focus(); }
  });
  document.addEventListener('pointerdown', event => {
    if (root.classList.contains('nav-open') && !sidebar.contains(event.target) && !menu.contains(event.target)) setNavOpen(false);
  });
  const narrow = window.matchMedia('(width < 720px)');
  const updateSidebar = () => { sidebar.inert = narrow.matches && !root.classList.contains('nav-open'); };
  new MutationObserver(updateSidebar).observe(root, { attributes: true, attributeFilter: ['class'] }); narrow.addEventListener('change', updateSidebar); updateSidebar();
  enhanceTooltips();
  document.dispatchEvent(new Event('gitviz-shell-ready'));
}
