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
function reportArchiveOn() { return uiPref('reportArchive', false) === true; }

/* ---------- Per-user preferences, kept on the server (/api/me/prefs) ----------
   Theme, history range / grouping, AI summary and archive toggles follow the user across browsers.
   localStorage mirrors them (theme is applied before sign-in, no flash) and holds the old values once,
   which are sent up the first time the server has none. */
const UI_PREF_KEYS = { theme: 'vco.theme', historyMonths: 'vco.historyMonths', historyGroup: 'vco.historyGroupV2',
  reportAi: 'vco.reportAi', reportArchive: 'vco.reportArchive' };
const uiPrefs = { data: {}, loaded: false, loading: null, timer: null, pending: {} };
function uiPrefLocal(key) {
  try {
    const raw = localStorage.getItem(UI_PREF_KEYS[key]);
    if (raw === null) return undefined;
    if (key === 'reportAi' || key === 'reportArchive') return raw === '1';
    if (key === 'historyMonths') return parseInt(raw, 10);
    return raw;
  } catch (err) { return undefined; }
}
function uiPref(key, fallback) {
  const v = key in uiPrefs.data ? uiPrefs.data[key] : uiPrefLocal(key);
  return v === undefined || v === null || Number.isNaN(v) ? fallback : v;
}
function uiSetPref(key, value) {
  uiPrefs.data[key] = value;
  try { localStorage.setItem(UI_PREF_KEYS[key], typeof value === 'boolean' ? (value ? '1' : '0') : String(value)); } catch (err) { /* private mode */ }
  uiPrefs.pending[key] = value;
  clearTimeout(uiPrefs.timer);
  uiPrefs.timer = setTimeout(uiFlushPrefs, 400);
}
function uiFlushPrefs() {
  const body = uiPrefs.pending;
  uiPrefs.pending = {};
  if (!Object.keys(body).length || typeof apiRequest !== 'function') return;
  apiRequest('/me/prefs', { method: 'PUT', body: JSON.stringify(body) }).catch(() => { /* preference only */ });
}
/* After sign-in: read the server copy; values only this browser knows are sent up once. */
function uiLoadPrefs() {
  if (!uiPrefs.loading) uiPrefs.loading = uiReadPrefs().finally(() => { if (!uiPrefs.loaded) uiPrefs.loading = null; });
  return uiPrefs.loading;
}
async function uiReadPrefs() {
  let got;
  try { got = (await apiRequest('/me/prefs')) || {}; } catch (err) { return uiPrefs.data; }
  uiPrefs.data = { ...got, ...uiPrefs.data };   // a toggle clicked while loading wins
  uiPrefs.loaded = true;
  Object.keys(UI_PREF_KEYS).forEach((k) => {
    if (!(k in uiPrefs.data)) { const v = uiPrefLocal(k); if (v !== undefined && !Number.isNaN(v)) uiPrefs.pending[k] = v; }
    else { try { const v = uiPrefs.data[k]; localStorage.setItem(UI_PREF_KEYS[k], typeof v === 'boolean' ? (v ? '1' : '0') : String(v)); } catch (err) { /* ignore */ } }
  });
  if (Object.keys(uiPrefs.pending).length) { Object.assign(uiPrefs.data, uiPrefs.pending); uiFlushPrefs(); }
  return uiPrefs.data;
}
/* Sign-out: the next user starts from their own server copy. */
function uiResetPrefs() { uiPrefs.data = {}; uiPrefs.loaded = false; uiPrefs.loading = null; }

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

/* Empty / loading states — one look everywhere (text is escaped; `action` is trusted HTML). */
function uiEmpty(text, action) {
  return `<div class="empty-state">${esc(text)}${action ? ` ${action}` : ''}</div>`;
}
/* One-line note inside a panel (đang tải, chưa có dữ liệu…); tone = crit / warn for errors. */
function uiNote(text, tone) {
  return `<div class="empty-note${tone ? ` tone-${tone}` : ''}">${esc(text)}</div>`;
}
/* Empty table body: one full-width row. */
function uiEmptyRow(colspan, text) {
  return `<tr><td colspan="${Number(colspan) || 1}" class="empty-note">${esc(text)}</td></tr>`;
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

/* ---------- Charts: colours from the HSC tokens (styles.css), one Chart instance per key ---------- */
function chartPalette() {
  const cs = getComputedStyle(document.documentElement);
  const g = (name) => cs.getPropertyValue(name).trim();
  return { text:g('--text'), muted:g('--muted'), divider:g('--divider'), primary:g('--primary'), accent:g('--accent'), ok:g('--ok'), warn:g('--warn'), bad:g('--bad'), crit:g('--crit'), faint:g('--faint'),
    // Series / sequential colours for multi-line or stacked charts.
    series: [1, 2, 3, 4, 5, 6, 7, 8].map(i => g(`--chart-s${i}`)), deep: g('--chart-deep'), amber: g('--chart-index'),
    seq: [1, 2, 3, 4, 5].map(i => g(`--chart-seq-${i}`)) };
}

function destroyChart(key) {
  if (appState.charts[key]) { appState.charts[key].destroy(); appState.charts[key] = null; }
}

/* ---------- Sortable tables (state in appState.sort.table) ---------- */
function applySort(rows, tableKey, valueFn) {
  const s = appState.sort.table[tableKey];
  if (!s) return rows;
  const sorted = [...rows].sort((a, b) => {
    const av = valueFn(a, s.key), bv = valueFn(b, s.key);
    if (typeof av === 'string' || typeof bv === 'string') return String(av).localeCompare(String(bv)) * (s.dir === 'asc' ? 1 : -1);
    const an = isFinite(av) ? av : -Infinity, bn = isFinite(bv) ? bv : -Infinity;
    return (an - bn) * (s.dir === 'asc' ? 1 : -1);
  });
  return sorted;
}

function bindSortableHeaders(tableId, tableKey, renderFn) {
  $$(`#${tableId} th[data-key]`).forEach(th => {
    th.onclick = () => {
      const key = th.dataset.key;
      const current = appState.sort.table[tableKey];
      let dir = 'desc';
      if (current && current.key === key) dir = current.dir === 'desc' ? 'asc' : 'desc';
      appState.sort.table[tableKey] = { key, dir };
      renderFn();
    };
  });
}

function highlightSortHeaders(tableId, tableKey) {
  const s = appState.sort.table[tableKey];
  $$(`#${tableId} th[data-key]`).forEach(th => {
    th.classList.remove('sorted', 'sorted-asc');
    if (s && th.dataset.key === s.key) th.classList.add(s.dir === 'desc' ? 'sorted' : 'sorted-asc');
  });
}
