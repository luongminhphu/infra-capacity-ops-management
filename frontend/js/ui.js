/* =========================================================
   ui.js — shared building blocks of the super-app shell
   (docs/design/super-app.md §3): status pill (4 standard
   levels), inline status message, confirm dialog, empty state.
   Module helpers (bkPill, licSetStatus, spPill…) delegate here
   so every page looks and behaves the same.
   ========================================================= */

/* Status pill. tone: ok | warn | bad | crit | na (AN TOÀN / THEO DÕI / CẢNH BÁO / VƯỢT NGƯỠNG / không áp dụng). */
function uiPill(tone, label) {
  const t = /^[a-z][a-z0-9-]*$/.test(String(tone || '')) ? tone : 'na';   // ok | warn | bad | crit | na | prio-*
  return `<span class="pill ${t}">${esc(label)}</span>`;
}

/* Inline status line under a form / toolbar. kind: '' | 'ok' | 'error'. Empty message hides it. */
function uiSetStatus(target, msg, kind, opts = {}) {
  const el = typeof target === 'string' ? $(target) : target;
  if (!el) return;
  el.textContent = msg || '';
  if (opts.hideWhenEmpty) el.classList.toggle('hidden', !msg);
  el.classList.toggle('tone-crit', kind === 'error');
  el.classList.toggle('error', kind === 'error');
  el.classList.toggle('ok', kind === 'ok');
  if (msg && opts.scroll) el.scrollIntoView({ block: 'nearest' });
}

/* Confirm dialog (replaces window.confirm). Resolves true / false. Works on top of another open dialog. */
function uiConfirm(message, opts = {}) {
  let dlg = $('#uiConfirm');
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'uiConfirm';
    dlg.className = 'src-dialog ui-confirm';
    dlg.setAttribute('aria-labelledby', 'uiConfirmText');
    document.body.appendChild(dlg);
  }
  dlg.innerHTML = `<form method="dialog">
      <p class="ui-confirm-text" id="uiConfirmText"></p>
      <div class="src-dialog-actions"><span class="src-spacer"></span>
        <button class="btn btn-ghost btn-sm" value="no" type="submit">${esc(opts.cancelLabel || 'Huỷ')}</button>
        <button class="btn ${opts.danger === false ? 'btn-primary' : 'btn-secondary btn-danger'} btn-sm" value="yes" type="submit" autofocus>${esc(opts.okLabel || 'Đồng ý')}</button>
      </div></form>`;
  $('#uiConfirmText', dlg).textContent = message;
  return new Promise(resolve => {
    dlg.addEventListener('close', () => resolve(dlg.returnValue === 'yes'), { once: true });
    dlg.returnValue = '';
    dlg.showModal();
  });
}

/* Empty state block. */
function uiEmpty(text, action) {
  return `<div class="empty-state">${esc(text)}${action ? ` ${action}` : ''}</div>`;
}

/* Link to the page of one object (thẻ Host / VM). */
function uiEntityHref(type, name, vcenterKey) {
  const q = new URLSearchParams({ type, name: name || '' });
  if (vcenterKey) q.set('vc', vcenterKey);
  return `#/entity?${q}`;
}
function uiEntityLink(type, name, vcenterKey, strong = true) {
  if (!name) return '—';
  const label = strong ? `<strong>${esc(name)}</strong>` : esc(name);
  return `<a class="entity-link" href="${esc(uiEntityHref(type, name, vcenterKey))}" title="Mở trang ${type === 'host' ? 'host' : 'VM'}">${label}</a>`;
}
