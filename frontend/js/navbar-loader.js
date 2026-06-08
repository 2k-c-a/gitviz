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

window.goPage = function(page){

  const isPage =
    window.location.pathname.includes('/pages/');

  const ROOT =
    isPage ? './' : './pages/';

  location.href =
    ROOT + page + '.html';
};

window.goHome = function(){

  const isPage =
    window.location.pathname.includes('/pages/');

  location.href =
    isPage ? '../index.html'
           : './index.html';
};

loadNavbar();