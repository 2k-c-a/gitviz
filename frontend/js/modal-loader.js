import { enhanceOverlays } from './ui/components.js';

export async function loadModal() {

  const isPage =
    window.location.pathname.includes('/pages/');

  const modalPath = isPage
    ? './components/modal.html'
    : './pages/components/modal.html';

  const response = await fetch(modalPath + '?v=' + Date.now());

  const html = await response.text();

  document.getElementById('modal-container').innerHTML = html;
  const detail = document.getElementById('drilldown-modal');
  const shell = document.querySelector('.app-shell');
  if (detail && shell) { detail.classList.add('app-detail'); shell.append(detail); }
  enhanceOverlays();
}
