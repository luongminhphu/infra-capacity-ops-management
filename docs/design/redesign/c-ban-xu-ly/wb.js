/* Mockup: listbox selection drives the detail pane (J/K or arrows, Enter, E = xong, H = ẩn 7 ngày).
   Each option points to a <template> holding its detail; options without one keep the current pane. */
(() => {
  const list = document.querySelector('[role="listbox"]');
  const pane = document.getElementById('detail');
  if (!list || !pane) return;
  const opts = () => [...list.querySelectorAll('[role="option"]')];
  const show = (opt) => {
    opts().forEach(o => o.setAttribute('aria-selected', String(o === opt)));
    list.setAttribute('aria-activedescendant', opt.id);
    opt.scrollIntoView({ block: 'nearest' });
    const tpl = document.getElementById(opt.dataset.detail || '');
    if (!tpl) return;
    const swap = () => { pane.replaceChildren(tpl.content.cloneNode(true)); if (window.lucide) window.lucide.createIcons(); };
    if (document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) document.startViewTransition(swap); else swap();
  };
  list.addEventListener('click', (e) => { const o = e.target.closest('[role="option"]'); if (o) show(o); });
  list.addEventListener('keydown', (e) => {
    const all = opts(); const i = all.findIndex(o => o.getAttribute('aria-selected') === 'true');
    const k = e.key.toLowerCase();
    if (k === 'arrowdown' || k === 'j') { e.preventDefault(); show(all[Math.min(i + 1, all.length - 1)]); }
    else if (k === 'arrowup' || k === 'k') { e.preventDefault(); show(all[Math.max(i - 1, 0)]); }
    else if ((k === 'e' || k === 'h') && all[i]) { all[i].classList.add('is-done'); show(all[Math.min(i + 1, all.length - 1)]); }
  });
})();
