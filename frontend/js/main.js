import { loadNavbar } from './navbar-loader.js';
import { loadModal } from './modal-loader.js';

import {
  openLoginModal,
  closeLoginModal,
  githubLogin,
  logout,
  toggleUserMenu,
  closeUserMenu,
  updateAuthUI,
  initAuth
} from './login.js';

import { showPage } from './routing.js';

import { handleAnalyze, fillUrl } from './utils.js';

import {
  openJobModal,
  closeJobModal,
  simulateJobState,
  openReanalyzeToast,
  closeToast,
  startReanalyze,
  openRemoveRepoModal,
  closeRemoveRepoModal,
  confirmRemoveRepo,
  openIsolatedPanel,
  closeIsolatedPanel,
  toggleNoisePopover,
  closeNoisePopover,
  saveNoiseFilter
} from './modal.js';

import './chart.js';

await loadNavbar();
await loadModal();

/* 로그인 상태 초기화 (백엔드 /api/auth/me 호출) */
await initAuth();

/* 전역 등록 */
window.openLoginModal  = openLoginModal;
window.closeLoginModal = closeLoginModal;
window.githubLogin     = githubLogin;
window.logout          = logout;
window.toggleUserMenu  = toggleUserMenu;
window.closeUserMenu   = closeUserMenu;
window.updateAuthUI    = updateAuthUI;

window.showPage     = showPage;
window.handleAnalyze = handleAnalyze;
window.fillUrl      = fillUrl;

window.openJobModal      = openJobModal;
window.closeJobModal     = closeJobModal;
window.simulateJobState  = simulateJobState;

window.openReanalyzeToast  = openReanalyzeToast;
window.closeToast          = closeToast;
window.startReanalyze      = startReanalyze;

window.openRemoveRepoModal  = openRemoveRepoModal;
window.closeRemoveRepoModal = closeRemoveRepoModal;
window.confirmRemoveRepo    = confirmRemoveRepo;

window.openIsolatedPanel  = openIsolatedPanel;
window.closeIsolatedPanel = closeIsolatedPanel;

window.toggleNoisePopover = toggleNoisePopover;
window.closeNoisePopover  = closeNoisePopover;
window.saveNoiseFilter    = saveNoiseFilter;
