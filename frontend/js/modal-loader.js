export async function loadModal() {

  const isPage =
    window.location.pathname.includes('/pages/');

  const modalPath = isPage
    ? './components/modal.html'
    : './pages/components/modal.html';

  const response = await fetch(modalPath);

  const html = await response.text();

  document.getElementById('modal-container').innerHTML = html;
}