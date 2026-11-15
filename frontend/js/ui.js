/* =========================================================
   ui.js — shared building blocks of the super-app shell
   (docs/design/super-app.md §3): status pill (4 standard
   levels), inline status message, confirm / alert / prompt and
   generic dialogs, empty state, number / money / date formats,
   file downloads. Views must use these instead of their own
   copies (tests/test_frontend_markup.py guards alert / prompt /
   createObjectURL).
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
        ${opts.single ? '' : `<button class="btn btn-ghost btn-sm" value="no" type="submit">${esc(opts.cancelLabel || 'Huỷ')}</button>`}
        <button class="btn ${opts.danger === false ? 'btn-primary' : 'btn-secondary btn-danger'} btn-sm" value="yes" type="submit" autofocus>${esc(opts.okLabel || 'Đồng ý')}</button>
      </div></form>`;
  $('#uiConfirmText', dlg).textContent = message;
  return new Promise(resolve => {
    dlg.addEventListener('close', () => resolve(dlg.returnValue === 'yes'), { once: true });
    dlg.returnValue = '';
    dlg.showModal();
  });
}

/* Message box with a single OK (replaces window.alert). */
function uiAlert(message, opts = {}) {
  return uiConfirm(message, { okLabel: opts.okLabel || 'Đóng', danger: false, single: true });
}

/* One-line text input (replaces window.prompt). Resolves the text, or null when cancelled. */
function uiPrompt(message, defaultValue = '', opts = {}) {
  const dlg = uiDialog('uiPrompt', { title: opts.title || '', narrow: true });
  dlg.body.innerHTML = `<form method="dialog" class="ui-prompt-form"><label class="field"><span id="uiPromptLabel"></span>
      <input id="uiPromptInput" maxlength="${Number(opts.maxLength) || 500}"></label>
      <div class="src-dialog-actions"><span class="src-spacer"></span>
        <button class="btn btn-ghost btn-sm" value="no" type="submit">Huỷ</button>
        <button class="btn btn-primary btn-sm" value="yes" type="submit">${esc(opts.okLabel || 'Đồng ý')}</button></div></form>`;
  $('#uiPromptLabel', dlg.el).textContent = message;
  const input = $('#uiPromptInput', dlg.el);
  input.value = defaultValue || '';
  return new Promise(resolve => {
    dlg.el.addEventListener('close', () => resolve(dlg.el.returnValue === 'yes' ? input.value : null), { once: true });
    dlg.el.returnValue = '';
    dlg.el.showModal();
    input.focus(); input.select();
  });
}

/* Generic modal dialog with the standard head (title + close). Returns { el, body }; the caller fills
   body and calls el.showModal() (or dlg.open()). Re-uses the element with the same id. */
function uiDialog(id, opts = {}) {
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement('dialog');
    el.id = id;
    document.body.appendChild(el);
  }
  el.className = `src-dialog${opts.className ? ' ' + opts.className : ''}${opts.narrow ? ' ui-dialog-narrow' : ''}`;
  el.setAttribute('aria-labelledby', `${id}Title`);
  el.innerHTML = `${opts.title ? `<div class="src-dialog-head"><h3 id="${esc(id)}Title"></h3>
      <button class="btn btn-ghost btn-sm" type="button" data-ui-close aria-label="Đóng"><i data-lucide="x"></i></button></div>` : ''}
    <div class="ui-dialog-body"></div>`;
  if (opts.title) { $(`#${id}Title`, el).textContent = opts.title; el.querySelector('[data-ui-close]').onclick = () => el.close(); }
  if (typeof refreshIcons === 'function') refreshIcons();
  return { el, body: el.querySelector('.ui-dialog-body'), open: () => { if (!el.open) el.showModal(); }, close: () => el.close() };
}

/* ---------- Numbers, money, dates (vi-VN) ---------- */
const UI_DASH = '—';
const uiBlank = (v) => v === null || v === undefined || v === '' || (typeof v === 'number' && !isFinite(v));

/* uiNumber(1234.5, 1) → "1.234,5". digits = max fraction digits; minDigits = fixed digits. */
function uiNumber(v, digits = 0, minDigits = 0) {
  if (uiBlank(v) || !isFinite(Number(v))) return UI_DASH;
  return Number(v).toLocaleString('vi-VN', { maximumFractionDigits: Math.max(digits, minDigits), minimumFractionDigits: minDigits });
}

/* Money: VND → "1.234.567 đ", USD → "1.234,50 USD", any other code → "1.234 EUR", '' → no unit. */
function uiMoney(v, currency = 'VND', digits) {
  if (uiBlank(v) || !isFinite(Number(v))) return UI_DASH;
  const cur = currency || '';
  const d = digits !== undefined ? digits : (cur === 'USD' ? 2 : 0);
  const n = uiNumber(v, d, cur === 'USD' ? d : 0);
  if (cur === 'VND') return `${n} đ`;
  return cur && cur !== 'mixed' ? `${n} ${cur}` : n;
}

/* "15/11/2026 09:30:00" (full) or "15/11 09:30" (short). Unparsable input is returned as text. */
function uiDateTime(iso, style = 'full') {
  if (!iso) return UI_DASH;
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  return style === 'short'
    ? d.toLocaleString('vi-VN', { hour12: false, hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })
    : d.toLocaleString('vi-VN', { hour12: false });
}
function uiDate(iso) {
  if (!iso) return UI_DASH;
  const d = new Date(iso);
  return isNaN(d) ? String(iso) : d.toLocaleDateString('vi-VN');
}
/* Shared by several modules (Trang chủ, AI, Passkey…): kept under the old name. */
const spTimeSafe = (iso) => uiDateTime(iso);

/* The 4 standard levels (+ info) as [pill tone, label] — same words on every page and report. */
const UI_LEVELS = { crit: ['crit', 'Vượt ngưỡng'], bad: ['bad', 'Cảnh báo'], warn: ['warn', 'Theo dõi'], ok: ['ok', 'An toàn'], info: ['na', 'Thông tin'] };
const uiLevelPill = (level) => uiPill(...(UI_LEVELS[level] || UI_LEVELS.info));

/* ---------- Files ---------- */
function uiSaveBlob(blob, filename) {
  const href = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = href; a.download = filename || 'download';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 2000);
}

/* "Lưu vào kho" (Trung tâm báo cáo): every export asks the server to keep a copy; the server archives
   only what backend/app/exports.py marks archivable. */
function reportArchiveOn() {
  try { return localStorage.getItem('vco.reportArchive') === '1'; } catch (err) { return false; }
}

/* Download what an API endpoint returns (PDF / Excel / CSV). Throws ApiError with the server's detail. */
async function uiDownload(url, options = {}, fallbackName = 'download') {
  const headers = new Headers(options.headers || undefined);
  if (reportArchiveOn()) headers.set('X-ICO-Archive', '1');
  const res = await fetch(url, { credentials: 'include', ...options, headers });
  if (res.status === 401) {
    if (window.onApiUnauthorized) window.onApiUnauthorized();
    throw new ApiError(401, 'Phiên đăng nhập đã hết hạn.');
  }
  if (!res.ok) {
    let detail = `Lỗi tải file (${res.status})`;
    try { const data = await res.json(); if (data && data.detail) detail = data.detail; } catch (err) { /* not JSON */ }
    throw new ApiError(res.status, detail);
  }
  const cd = res.headers.get('Content-Disposition') || '';
  const utf = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  const plain = /filename="?([^";]+)"?/i.exec(cd);
  let name = fallbackName;
  try { name = utf ? decodeURIComponent(utf[1]) : (plain ? plain[1] : fallbackName); } catch (err) { name = plain ? plain[1] : fallbackName; }
  uiSaveBlob(await res.blob(), name);
  return name;
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
