/* Delegated, named UI actions. No eval, executable attribute text or property traversal. */
const names = ['handleAnalyze', 'openContributorDrilldown', '__ddPushFile', '__ddPushContributor', 'openJobModal',
  'closeJobModal', 'goPage', 'goHome', 'goAiReview', 'goMyProjects', 'githubLogin', 'logout', 'closeLoginModal',
  'simulateJobState', 'viewResult', 'retryJob', 'openReanalyzeToast', 'openRemoveRepoModal', 'closeToast',
  'startReanalyze', 'closeIsolatedPanel', 'drilldownBack', 'closeDrilldown', 'confirmRemoveRepo',
  'closeRemoveRepoModal', 'toggleUserMenu', 'closeUserMenu', 'reloginFromJob', 'resumePolling', 'viewLastSuccess', '_restartStaleJob'];
const actions = Object.fromEntries(names.map(name => [name, (...args) => { if (typeof window[name] === 'function') return window[name](...args); }]));
actions.reloadPage = () => location.reload();
actions.latestResult = query => { if (typeof query === 'string' && /^\?repo=\d+$/.test(query)) location.href = location.pathname + query; };
actions.stop = () => {};
document.addEventListener('click', event => {
  const target = event.target.closest?.('[data-action]');
  if (!target || !Object.hasOwn(actions, target.dataset.action)) return;
  if (target.dataset.action === 'stop') { event.stopPropagation(); return; }
  event.preventDefault(); event.stopPropagation();
  let args = [];
  try { args = JSON.parse(decodeURIComponent(target.dataset.args || '%5B%5D')); }
  catch { return; }
  if (!Array.isArray(args) || args.length > 4 || args.some(value => !['string', 'number', 'boolean'].includes(typeof value))) return;
  const result = actions[target.dataset.action](...args);
  if (result?.catch) result.catch(() => {
    let notice = document.getElementById('desktop-auth-notice');
    if (!notice) { notice = document.createElement('p'); notice.id = 'desktop-auth-notice'; document.body.prepend(notice); }
    notice.textContent = '요청을 완료하지 못했습니다. 로그인 또는 서버 연결을 확인하고 다시 시도하세요.';
  });
});
for (const [eventName, key] of [['mouseover', 'hover'], ['mouseout', 'leave']]) document.addEventListener(eventName, event => {
  const target = event.target.closest?.(`[data-${key}]`);
  if (target && ['', 'var(--bg2)', 'var(--red-dark)', 'var(--red)', 'var(--red-light)'].includes(target.dataset[key])) target.style.background = target.dataset[key];
});
