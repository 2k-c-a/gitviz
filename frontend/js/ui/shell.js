import { parseRoute as parseAiRoute, buildRoute as buildAiRoute } from '../ai-state.js';
import { parseState, nextQuery } from '../viz-state.js';
import { element, createIcon, enhanceTooltips, observeActions } from './components.js';
import { SETTINGS_ENTRY_URL } from '../platform/workspace-navigation.js';
import './motion.js';

const webRoot = new URL('../../', import.meta.url);
const pages = { overview: '분석 개요', network: '협업 네트워크', 'code-analysis': '파일 분석', history: '분석 이력', 'ai-insights': 'AI 분석', 'ai-review': 'AI 분석' };
const glyphs = { overview: '▦', network: '◎', 'code-analysis': '▤', history: '◷', 'ai-insights': '✦', 'ai-review': '◇' };
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
  const menu = element('button', 'app-menu'); menu.append(createIcon('menu', 20)); menu.type = 'button'; menu.setAttribute('aria-label', '내비게이션 열기'); menu.setAttribute('aria-controls', sidebar.id); menu.setAttribute('aria-expanded', 'false');
  const setNavOpen = open => { root.classList.toggle('nav-open', open); menu.setAttribute('aria-expanded', String(open)); menu.setAttribute('aria-label', open ? '내비게이션 닫기' : '내비게이션 열기'); };
  const closeNav = element('button', 'app-nav-close'); closeNav.append(createIcon('x', 18)); closeNav.type = 'button'; closeNav.setAttribute('aria-label', '내비게이션 닫기');
  closeNav.addEventListener('click', () => { setNavOpen(false); menu.focus(); }); sidebar.append(closeNav);
  menu.addEventListener('click', () => {
    const open = !root.classList.contains('nav-open'); setNavOpen(open);
    if (open) { sidebar.inert = false; nav.querySelector('a')?.focus(); }
  });
  // No mode badge or repository caption in the top bar: the sidebar and each page's own header name the context.
  const spacer = element('span', 'app-toolbar-spacer');
  // One toggle: the icon shows the theme in use (moon = dark, sun = light); until pressed it follows Windows.
  const theme = element('button', 'app-theme'); theme.type = 'button'; theme.id = 'shell-theme';
  theme.dataset.tooltip = '라이트·다크 테마 전환';
  const showTheme = current => {
    theme.replaceChildren(createIcon(current === 'dark' ? '☾' : '☀'));
    theme.dataset.theme = current; theme.setAttribute('aria-label', current === 'dark' ? '다크 모드 · 라이트 모드로 전환' : '라이트 모드 · 다크 모드로 전환');
  };
  showTheme(document.documentElement.dataset.theme);
  theme.addEventListener('click', () => window.dispatchEvent(new CustomEvent('gitviz-theme-select', { detail: theme.dataset.theme === 'dark' ? 'light' : 'dark' })));
  window.addEventListener('gitviz-theme-changed', event => showTheme(event.detail.theme));
  toolbar.append(menu, spacer, theme);
  if (account) toolbar.append(account);
  const main = local ? document.querySelector('body > main') : element('main');
  main.id = 'app-main'; main.tabIndex = -1; main.classList.add('app-main');
  if (local) main.classList.add('workspace-content');
  const footer = element('footer', 'app-statusbar');
  footer.append(element('span', '', local ? '내 PC 프로젝트' : 'GitHub 저장소 분석'));
  const status = element('span', 'app-status-text', local ? '로컬 구성 확인 중' : '저장소를 고르세요'); status.id = 'shell-status';
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
  const toView = (a, view) => {
    a.dataset.wsView = view;
    a.addEventListener('click', event => { event.preventDefault(); setNavOpen(false); window.dispatchEvent(new CustomEvent('workspace-navigate', { detail: { view } })); });
    return a;
  };
  const group = text => nav.append(element('p', 'app-nav-group', text));
  const inApp = location.protocol === 'gitviz:';
  // The same entries in the same order on web pages and in the local workspace, so the rail never changes when moving
  // between them. The app's own work (local projects, notifications) comes first (2026-11-08, AD-23 ④ revised: 알림 shown,
  // 계정·연결 still hidden; sign-in lives in the account chip).
  if (local || inApp) {
    group('내 PC');
    if (local) { toView(link('프로젝트', '▣', '#'), 'projects'); toView(link('알림', '◔', '#'), 'inbox'); }
    else { link('프로젝트', '▣', new URL('../index.html', webRoot).href); link('알림', '◔', new URL('../index.html?view=inbox', webRoot).href); }
  }
  // One entry starts an analysis: the search box takes a keyword or a GitHub address. The landing page stays reachable from the logo.
  group('GitHub 저장소');
  link('내 분석', '▦', new URL('pages/myprojects.html', webRoot).href, currentPage === 'myprojects');
  link('새 분석', '⌕', new URL('pages/search.html', webRoot).href, currentPage === 'search' || (!local && currentPage === 'index'));
  // AI review by commit/PR lives under the repository's AI 분석 (its third tab), not as a top-level entry.
  link('커뮤니티', '☷', new URL('pages/community.html', webRoot).href, currentPage === 'community');
  const settingsHref = () => { const url = new URL(pageUrl('settings')); if (currentPage === 'ai-review') url.searchParams.set('return', buildAiRoute(parseAiRoute(location.search))); return url.href; };
  // In the app, 설정 always opens the app's settings view (AI keys and models, editor; AD-33) from every screen —
  // the same place as 'API 키 저장하러 가기'. The web keeps its own settings page.
  const settingsLink = local ? toView(link('설정', '⚙', '#'), 'settings')
    : link('설정', '⚙', inApp ? SETTINGS_ENTRY_URL : settingsHref(), !inApp && currentPage === 'settings');
  settingsLink.classList.add('app-nav-settings');
  const repositoryNav = element('div', 'app-repository-nav'); nav.append(repositoryNav);
  let repositoryLinks = [];
  let repositoryId;
  const buildRepositoryLinks = () => {
    const state = parseState(location.search);
    repositoryId = state.repo; repositoryNav.replaceChildren(); repositoryLinks = [];
    if (!local && state.repo) {
      repositoryNav.append(element('p', 'app-nav-group', '현재 저장소'));
      for (const [page, label] of Object.entries(pages)) {
        if (page === 'ai-review') continue; // A tab inside AI 분석.
        const selected = currentPage === page || (page === 'ai-insights' && currentPage === 'ai-review');
        const a = link(label, glyphs[page], pageUrl(page), selected, repositoryNav);
        a.dataset.shellPage = page; repositoryLinks.push(a);
      }
    }
  };
  buildRepositoryLinks();
  if (local) {
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
      const banner = document.getElementById('analysis-context');
      status.textContent = document.getElementById('load-error') ? '분석 결과를 불러오지 못했습니다' : banner ? '저장된 분석 결과를 보는 중' : '';
      if (!inApp) settingsLink.href = settingsHref();
      for (const a of repositoryLinks) a.href = pageUrl(a.dataset.shellPage);
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
