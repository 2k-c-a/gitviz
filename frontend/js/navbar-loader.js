import { nextQuery, parseState } from './viz-state.js';

export async function loadNavbar() {

  const isPage =
    window.location.pathname.includes('/pages/');

  const navbarPath = isPage
    ? './components/navbar.html'
    : './pages/components/navbar.html';

  const response = await fetch(navbarPath);

  const html = await response.text();

  document.getElementById('navbar-container').innerHTML = html;

  // 햄버거 메뉴 이벤트
  const hamburgerBtn = document.getElementById('hamburger-btn');
  const navLinks = document.getElementById('nav-links');

  if (hamburgerBtn && navLinks) {
    hamburgerBtn.addEventListener('click', () => {
      navLinks.classList.toggle('open');
    });
  }
}

/* 화면 이동.
   - goPage('network')            : 같은 저장소의 탭 이동 → 지금 주소의 repo·snapshot·정렬 정책을 유지
   - goPage('timeline', repoId)   : 그 저장소로 이동. 저장소가 바뀌면 이전 snapshot은 가져가지 않는다
   - goPage('timeline', repoId, snapshotId) : 그 저장소의 특정 결과(예: 방금 끝난 분석)로 이동 */
window.goPage = function(page, repoId, snapshotId){

  const isPage =
    window.location.pathname.includes('/pages/');

  const ROOT =
    isPage ? './' : './pages/';

  location.href =
    ROOT + page + '.html' + nextQuery(location.search, repoId, snapshotId);
};

/* AI 리뷰: 지금 주소에 repo가 명시돼 있을 때만 그 저장소로 간다. 없으면 저장소를 고르게 하는 화면으로 간다
   (localStorage·기본 repo=1 같은 숨은 기본값을 쓰지 않는다. snapshot·정렬 상태도 가져가지 않는다). */
window.goAiReview = function(){
  const isPage = window.location.pathname.includes('/pages/');
  const repo = parseState(location.search).repo;
  location.href = (isPage ? './' : './pages/') + 'ai-review.html' + (repo != null ? `?repo=${repo}` : '');
};

window.goHome = function(){

  const isPage =
    window.location.pathname.includes('/pages/');

  location.href =
    isPage ? '../index.html'
           : './index.html';
};

/* loadNavbar() 는 main.js 에서 await 로 호출하므로 자동 실행 제거 */