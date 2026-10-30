/* Loaded before paint and before chart modules. Only a non-sensitive UI preference is persisted. */
(() => {
  const key = 'gitviz.ui.theme';
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  const valid = value => ['system', 'light', 'dark'].includes(value);
  let preference = 'system';
  try { const saved = localStorage.getItem(key); if (valid(saved)) preference = saved; } catch { /* Session-only preference. */ }
  const apply = () => {
    const theme = preference === 'system' ? system.matches ? 'dark' : 'light' : preference;
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.themePreference = preference;
    window.dispatchEvent(new CustomEvent('gitviz-theme-changed', { detail: { theme, preference } }));
  };
  window.addEventListener('gitviz-theme-select', event => {
    if (!valid(event.detail)) return;
    preference = event.detail;
    try { localStorage.setItem(key, preference); } catch { /* Storage can be unavailable. */ }
    apply();
  });
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    preference = valid(event.newValue) ? event.newValue : 'system'; apply();
  });
  system.addEventListener('change', apply);
  apply();
})();
