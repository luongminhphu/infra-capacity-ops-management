/* Mockup helpers: theme toggle (dark default, like the app) + lucide icons.
   Static mockups only — not app code. */
(() => {
  const root = document.documentElement;
  try { const t = localStorage.getItem('ico-mock-theme'); if (t) root.dataset.theme = t; } catch (e) { /* storage blocked */ }
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-toggle-theme]');
    if (!b) return;
    const next = root.dataset.theme === 'light' ? 'dark' : 'light';
    const apply = () => { root.dataset.theme = next; b.setAttribute('aria-pressed', String(next === 'light')); };
    if (document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) document.startViewTransition(apply); else apply();
    try { localStorage.setItem('ico-mock-theme', next); } catch (e2) { /* ignore */ }
  });
  const icons = () => { if (window.lucide) window.lucide.createIcons(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', icons); else icons();
})();
