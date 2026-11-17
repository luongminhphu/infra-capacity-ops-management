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
function uiPill(tone, label, title) {
  const t = /^[a-z][a-z0-9-]*$/.test(String(tone || '')) ? tone : 'na';   // ok | warn | bad | crit | na | prio-*
  return `<span class="pill ${t}"${title ? ` title="${esc(title)}"` : ''}>${esc(label)}</span>`;
}

/* ---------- List → detail (MASTER §6, audit FE-20): one keyboard model and one pane / dialog for every page ---------- */
function uiPaneOrDialog(paneSel, title, code, body, actions) {
  const pane = $(paneSel);
  if (pane && matchMedia('(min-width: 1024px)').matches) {
    if (!$('#spDialog').open) $('#spDialogBody').innerHTML = '';
    const render = () => {
      pane.innerHTML = `<div class="sp-pane-head"><h3 id="spPaneTitle">${title}</h3><span class="muted-sub">${esc(code)}</span></div>
        <div class="sp-pane-actions">${(actions || '').replace('<span class="src-spacer"></span>', '').trim()}</div>
        <p class="src-result" id="spDialogMsg" role="status"></p>
        <div class="sp-dialog-body">${body}</div>`;
      refreshIcons();
    };
    render();
    return pane;
  }
  uiPanelDialog(`${esc(code)} — ${title}`, body, actions);
  return $('#spDialogBody');
}

/* Keyboard for a list → detail listbox (one model for every page, MASTER §6): J / K / ↑ ↓ / Home / End move,
   Enter / Space open the current item. `open(key, focus)` renders the detail; `onEnter(key)` (optional) replaces
   what Enter does — e.g. move focus into the detail pane with uiFocusDetail. */
function uiListKeys(e, keyAttr, current, open, onEnter) {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const lb = e.currentTarget === e.target ? e.target : e.target.closest('[role="listbox"]');
  if (!lb) return;
  const keys = [...lb.querySelectorAll('[role="option"]')].map(o => o.dataset[keyAttr]);
  const i = keys.indexOf(current), k = e.key.toLowerCase();
  let next = null;
  if (k === 'arrowdown' || k === 'j') next = keys[Math.min(i + 1, keys.length - 1)];
  else if (k === 'arrowup' || k === 'k') next = keys[Math.max(i - 1, 0)];
  else if (k === 'home') next = keys[0];
  else if (k === 'end') next = keys[keys.length - 1];
  else if (k === 'enter' || k === ' ') {
    if (onEnter && current) { e.preventDefault(); onEnter(current); return; }
    next = current || keys[0];
  }
  if (!next) return;
  e.preventDefault();
  open(next, true);
}

/* Move keyboard focus into a detail pane (Enter on a list row) and bring it into view on small screens. */
function uiFocusDetail(sel) {
  const d = typeof sel === 'string' ? $(sel) : sel;
  if (!d) return;
  if (!d.hasAttribute('tabindex')) d.setAttribute('tabindex', '-1');
  d.focus({ preventScroll: true });
  d.scrollIntoView({ block: 'nearest' });
}

/* Mark the selected option of a listbox (aria-selected + aria-activedescendant). */
function uiListSelect(listSel, keyAttr, key, focus) {
  const lb = $(listSel);
  if (!lb) return;
  let sel = null;
  lb.querySelectorAll('[role="option"]').forEach(o => { const on = o.dataset[keyAttr] === String(key); o.setAttribute('aria-selected', String(on)); if (on) sel = o; });
  if (sel) { lb.setAttribute('aria-activedescendant', sel.id); sel.scrollIntoView({ block: 'nearest' }); } else lb.removeAttribute('aria-activedescendant');
  if (focus) lb.focus();
}


/* The shared panel dialog (#spDialog in index.html): title is trusted HTML built by the caller (escape data!). */
function uiPanelDialog(title, body, actions) {
  const dlg = $('#spDialog');
  $('#spDialogBody').onclick = null;
  $('#spDialogBody').innerHTML = `
    <div class="src-dialog-head"><div class="panel-title" role="heading" aria-level="2" id="spDialogTitle">${title}</div>
      <button class="btn btn-ghost btn-sm" type="button" data-sp-close aria-label="Đóng"><i data-lucide="x"></i></button></div>
    <div class="sp-dialog-body">${body}</div>
    <p class="src-result" id="spDialogMsg" role="status"></p>
    <div class="src-dialog-actions">${actions || ''}</div>`;
  if (!dlg.open) dlg.showModal();
  refreshIcons();
  if (!dlg.dataset.bound) {
    dlg.dataset.bound = '1';
    dlg.addEventListener('click', (e) => { if (e.target.closest('[data-sp-close]')) dlg.close(); });
  }
}

/* File pickers as a drop zone (no native "Choose File" button, which is English and unstyled):
   <label class="dropzone" data-ui-drop="<idle text>"> wraps an sr-only file input. One document-level
   binding shows the chosen file name and accepts drag & drop; the page keeps its own change handler. */
function uiDropzone(id, title, sub, accept, extraCls = '') {
  return `<label class="dropzone dropzone-sm ${extraCls}" data-ui-drop="${esc(title)}">
      <input type="file" id="${esc(id)}" accept="${esc(accept)}" class="sr-only">
      <i data-lucide="upload" aria-hidden="true"></i>
      <span class="dz-main"><b>${esc(title)}</b> hoặc kéo thả vào đây</span>
      ${sub ? `<span class="dz-sub">${esc(sub)}</span>` : ''}</label>`;
}
(function bindUiDropzones() {
  const zoneOf = (e) => e.target && e.target.closest ? e.target.closest('.dropzone[data-ui-drop]') : null;
  document.addEventListener('change', (e) => {
    const i = e.target;
    if (!(i instanceof HTMLInputElement) || i.type !== 'file') return;
    const z = i.closest('.dropzone[data-ui-drop]'), m = z && z.querySelector('.dz-main');
    if (m) m.innerHTML = i.files && i.files.length ? `<b>${esc(i.files[0].name)}</b> — chọn file khác` : `<b>${esc(z.dataset.uiDrop)}</b> hoặc kéo thả vào đây`;
  });
  ['dragenter', 'dragover'].forEach(t => document.addEventListener(t, (e) => { const z = zoneOf(e); if (z) { e.preventDefault(); z.classList.add('over'); } }));
  ['dragleave', 'drop'].forEach(t => document.addEventListener(t, (e) => { const z = zoneOf(e); if (z) z.classList.remove('over'); }));
  document.addEventListener('drop', (e) => {
    const z = zoneOf(e);
    if (!z) return;
    e.preventDefault();
    const i = z.querySelector('input[type="file"]');
    if (!i || !e.dataTransfer || !e.dataTransfer.files.length) return;
    i.files = e.dataTransfer.files;
    i.dispatchEvent(new Event('change', { bubbles: true }));
  });
})();

/* Neutral label (kind, origin, "Lịch sử", "Demo"…): no status glyph, never one of the 4 levels. */
function uiTag(label, title) {
  return `<span class="tag"${title ? ` title="${esc(title)}"` : ''}>${esc(label)}</span>`;
}

/* Shared configuration (capacity thresholds, report logo, backup scope / schedules / thresholds,
   license rules and entitlements) is admin-only; the API enforces it, the UI only hides the controls. */
const ADMIN_ONLY_NOTE = 'Chỉ quản trị viên được sửa.';
function uiIsAdmin() { return !!(typeof currentUser !== 'undefined' && currentUser && currentUser.isAdmin); }

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
  return cur && cur !== 'mixed' ? `${n} ${esc(cur)}` : n;
}

/* "15/11/2026 09:30:00" (full) or "15/11 09:30" (short). Unparsable input is returned as text. */
function uiDateTime(iso, style = 'full') {
  if (!iso) return UI_DASH;
  const d = new Date(iso);
  if (isNaN(d)) return esc(String(iso));          // never hand raw input back into HTML (FE-41)
  return style === 'short'
    ? d.toLocaleString('vi-VN', { hour12: false, hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })
    : d.toLocaleString('vi-VN', { hour12: false });
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
/* Loading / error states shared by every page (FE-22 / FE-31): one look, announced to screen readers.
   uiError keeps the server message (already written for users) and can offer one retry action. */
function uiLoading(text = 'Đang tải…') {
  return `<div class="empty-note ui-loading" role="status" aria-busy="true">${esc(text)}</div>`;
}
function uiError(text, retryAttr) {
  return `<div class="empty-note tone-crit ui-error" role="alert">${esc(text)}${retryAttr ? ` <button class="btn btn-secondary btn-sm" type="button" ${retryAttr}>Thử lại</button>` : ''}</div>`;
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

/* ---------- Charts: colours from the tokens (css/tokens.css), one Chart instance per key ---------- */
function chartPalette() {
  const cs = getComputedStyle(document.documentElement);
  const g = (name) => cs.getPropertyValue(name).trim();
  return { text:g('--text-primary'), muted:g('--text-secondary'), divider:g('--border-subtle'), primary:g('--brand'), accent:g('--accent'), ok:g('--status-ok'), warn:g('--status-warn'), bad:g('--status-bad'), crit:g('--status-crit'), faint:g('--text-tertiary'),
    // MASTER §8 (FE-06): at most 3 categorical series colours + `other` grey ("Khác" / total / not emphasised).
    // More categories → chartSeriesColor(i) greys out everything after the third. `seq` = one hue, light → dark,
    // only for ordered buckets (sizes, steps), never for unrelated categories.
    series: [1, 2, 3].map(i => g(`--chart-s${i}`)), other: g('--chart-s4'), deep: g('--chart-deep'),
    seq: [1, 2, 3, 4, 5].map(i => g(`--chart-seq-${i}`)),
    // MASTER.md §Biểu đồ: grid / axis ink and threshold bands (80–100 % THEO DÕI, > 100 % VƯỢT NGƯỠNG).
    grid: g('--chart-grid'), axis: g('--chart-axis'), bandWarn: g('--chart-band-warn'), bandBad: g('--chart-band-bad') };
}

/* Colour of the i-th categorical series: s1–s3, then grey (emphasise the first three, MASTER §8). */
function chartSeriesColor(i, palette) {
  const c = palette || chartPalette();
  return i < c.series.length ? c.series[i] : c.other;
}

/* Same colour at alpha a (0–1) for fills; works for #rgb / #rrggbb / rgb() tokens (replaces `color + '22'`). */
function chartAlpha(color, a) {
  const s = String(color || '').trim();
  let r, g, b;
  const hex = s.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (hex) {
    const h = hex[1].length === 3 ? hex[1].split('').map(x => x + x).join('') : hex[1];
    [r, g, b] = [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16));
  } else {
    const m = s.match(/rgba?\(([^)]+)\)/i);
    if (!m) return s;
    [r, g, b] = m[1].split(',').map(x => parseFloat(x));
  }
  return `rgba(${r}, ${g}, ${b}, ${a})`;
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

/* Charts use the product font (self-hosted Be Vietnam Pro) instead of Chart.js' Helvetica default. */
if (window.Chart) {
  Chart.defaults.font.family = '"Be Vietnam Pro", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif';
  Chart.defaults.locale = 'vi-VN';   // axis ticks / tooltips: 1.234,5 like every other number (Q7)
}

/* ---------- Data marks (MASTER.md §7–8) ----------
   uiBullet: % of the overcommit limit as a bar with notches at 80 % (THEO DÕI) and 100 % (limit).
   The fill is the series color, never a status color: the status is said by the pill next to it.
   Scale 0–120 % so an overrun is visible past the 100 % notch. */
function uiBullet(pct, label) {
  const ok = isFinite(pct);
  const w = ok ? Math.max(0, Math.min(pct, 120)) / 1.2 : 0;
  const text = ok ? `${fmtNum(pct, 1)}%` : 'NA';
  return `<span class="bullet${ok && pct > 100 ? ' over' : ''}" role="img" aria-label="${esc(`${label ? `${label}: ` : ''}${text} ngưỡng`)}"><i style="width:${w.toFixed(2)}%"></i></span>`;
}

/* uiKpiStrip: one joined band of KPIs (label, value, small unit, sub, optional bullet + status pill). */
function uiKpiStrip(items) {
  return `<div class="kpi-strip">${items.map(k => `<div class="kpi-cell${k.muted ? ' muted' : ''}">
      <div class="kpi-l">${esc(k.label)}</div>
      <div class="kpi-v">${k.valueHtml !== undefined ? k.valueHtml : esc(k.value)}${k.unit ? `<small>${esc(k.unit)}</small>` : ''}</div>
      ${k.subHtml !== undefined ? `<div class="kpi-s">${k.subHtml}</div>` : k.sub ? `<div class="kpi-s">${esc(k.sub)}</div>` : ''}
      ${k.pct !== undefined && k.pct !== null ? `<div class="kpi-b">${uiBullet(k.pct, k.label)}${k.status ? uiPill(k.status.cls, k.status.text) : ''}</div>`
        : k.status ? `<div class="kpi-st">${uiPill(k.status.cls, k.status.text)}</div>` : ''}
    </div>`).join('')}</div>`;
}

/* uiSmallMultiples: one cell per entity (cluster / vCenter) with CPU / RAM (/ Storage) bullets. */
function uiSmallMultiples(rows, opts = {}) {
  if (!rows.length) return uiEmpty(opts.empty || 'Chưa có dữ liệu.');
  return `<div class="sm-grid">${rows.map(r => `<article class="sm-cell">
      <h3 class="sm-h"><span class="sm-name">${r.nameHtml || esc(r.name)}</span>${r.status ? uiPill(r.status.cls, r.status.text) : ''}</h3>
      ${r.metrics.map(m => `<div class="sm-row"><span>${esc(m.label)}</span>${uiBullet(m.pct, `${r.name} ${m.label}`)}<b class="num">${isFinite(m.pct) ? `${fmtNum(m.pct, 1)}%` : 'NA'}</b></div>`).join('')}
      ${r.sub ? `<p class="sm-sub">${esc(r.sub)}</p>` : ''}</article>`).join('')}</div>
    ${opts.legend === false ? '' : '<p class="sm-legend">% của ngưỡng overcommit. Vạch mảnh = 80 % (bắt đầu THEO DÕI), vạch đậm = 100 % (ngưỡng).</p>'}`;
}

/* Common Chart.js scales / legend in design tokens (grid, axis ink, product font). */
function uiChartBase(colors, extra = {}) {
  return Object.assign({ maintainAspectRatio: false, responsive: true,
    plugins: { legend: { labels: { color: colors.text, boxWidth: 12, boxHeight: 12 } } },
    scales: { x: { ticks: { color: colors.axis || colors.muted }, grid: { color: colors.grid || colors.divider } },
              y: { beginAtZero: true, ticks: { color: colors.axis || colors.muted }, grid: { color: colors.grid || colors.divider } } } }, extra);
}


/* ---------- Accessible names for generated form controls (ui-ux-pro-max review, WCAG 1.3.1 / 4.1.2) ----------
   Many tables and toolbars are built from template strings with placeholder-only inputs. Give every visible
   control without a label an aria-label from, in order: its column header, its placeholder, the short text just
   before it. Runs after DOM changes (batched per frame); never overrides a real <label>, aria-label or title. */
function uiAutoLabel(root) {
  (root || document).querySelectorAll('input:not([type=hidden]):not([type=file]), select, textarea').forEach(el => {
    if ((el.labels && el.labels.length) || el.hasAttribute('aria-label') || el.hasAttribute('aria-labelledby') || el.title) return;
    let name = '';
    const cell = el.closest('td');
    if (cell) {
      const table = cell.closest('table');
      const head = table && table.tHead && table.tHead.rows[0];
      let col = 0;
      for (const c of cell.parentElement.cells) { if (c === cell) break; col += c.colSpan || 1; }
      if (head) { let i = 0; for (const th of head.cells) { if (col >= i && col < i + (th.colSpan || 1)) { name = th.textContent; break; } i += th.colSpan || 1; } }
    }
    if (!name) {   // <div class="field"><label>Tên</label><input></div>: the label is not tied to the control
      const field = el.closest('.field');
      const lab = field && field.querySelector(':scope > label, :scope > span');
      if (lab && !lab.contains(el)) name = lab.textContent;
    }
    if (!name) name = el.getAttribute('placeholder') || '';
    if (!name) { const prev = el.previousElementSibling; if (prev && /^(SPAN|B|STRONG|SMALL|LABEL)$/.test(prev.tagName)) name = prev.textContent; }
    name = name.replace(/\s+/g, ' ').trim();
    if (name) el.setAttribute('aria-label', name.slice(0, 80));
  });
}
(function () {
  let queued = false;
  const run = () => { queued = false; uiAutoLabel(document); };
  const start = () => {
    run();
    new MutationObserver(() => { if (!queued) { queued = true; requestAnimationFrame(run); } }).observe(document.body, { childList: true, subtree: true });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();


/* ---------- "Xem bảng" under every chart (MASTER §8; ui-ux-pro-max chart A11y Fallback) ----------
   One Chart.js plugin for all charts: after each update it (re)builds a collapsed <details> with the
   chart's own labels × datasets, so the numbers are readable without the picture (screen readers,
   colour blindness, copy / paste). Text only (textContent), never HTML from the data. A chart can opt
   out with options.plugins.icoDataTable = false (e.g. sparklines whose numbers are printed beside). */
function uiChartTableFormat(v) {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'object') v = v.y !== undefined ? v.y : (v.r !== undefined ? v.r : v.x);
  const n = Number(v);
  return isFinite(n) ? n.toLocaleString('vi-VN', { maximumFractionDigits: 2 }) : String(v);
}
const uiChartTablePlugin = {
  id: 'icoDataTable',
  afterUpdate(chart, _args, opts) {
    if (opts === false || (opts && opts.enabled === false)) return;
    const canvas = chart.canvas;
    const host = canvas && canvas.parentElement;
    if (!host || !host.parentElement) return;
    const sets = (chart.data.datasets || []).filter(d => !d.hidden && Array.isArray(d.data));
    const labels = chart.data.labels || [];
    let box = host.nextElementSibling;
    if (!box || !box.classList.contains('chart-table')) {
      box = document.createElement('details');
      box.className = 'chart-table';
      box.innerHTML = '<summary>Xem bảng</summary><div class="table-scroll table-scroll-flat"><table class="data-table"><caption class="sr-only"></caption><thead></thead><tbody></tbody></table></div>';
      host.insertAdjacentElement('afterend', box);
    }
    if (!sets.length) { box.hidden = true; return; }
    box.hidden = false;
    const title = (chart.options.plugins && chart.options.plugins.title && chart.options.plugins.title.text) || canvas.getAttribute('aria-label') || 'Dữ liệu biểu đồ';
    box.querySelector('caption').textContent = String(title);
    const thead = box.querySelector('thead'), tbody = box.querySelector('tbody');
    thead.textContent = ''; tbody.textContent = '';
    const hr = document.createElement('tr');
    for (const h of ['', ...sets.map(d => d.label || '')]) { const th = document.createElement('th'); th.textContent = h; th.scope = 'col'; hr.appendChild(th); }
    thead.appendChild(hr);
    const rows = Math.max(labels.length, ...sets.map(d => d.data.length));
    for (let i = 0; i < Math.min(rows, 400); i++) {
      const tr = document.createElement('tr');
      const th = document.createElement('th'); th.scope = 'row';
      th.textContent = Array.isArray(labels[i]) ? labels[i].join(' ') : (labels[i] ?? String(i + 1));
      tr.appendChild(th);
      for (const d of sets) { const td = document.createElement('td'); td.className = 'num'; td.textContent = uiChartTableFormat(d.data[i]); tr.appendChild(td); }
      tbody.appendChild(tr);
    }
  },
  afterDestroy(chart) {
    const host = chart.canvas && chart.canvas.parentElement;
    const box = host && host.nextElementSibling;
    if (box && box.classList.contains('chart-table')) box.remove();
  },
};
/* Line series told apart by line style and point shape too, not hue alone (validation 16/11, ui-ux-pro-max
   chart rule "distinct colors + distinct line styles"). Only for charts with >= 2 line series; a dataset
   that sets its own borderDash (threshold / total lines) keeps it, and then the others vary by point shape. */
const UI_SERIES_DASH = [[], [6, 4], [2, 3], [8, 3, 2, 3]];
const UI_SERIES_POINT = ['circle', 'rectRot', 'triangle', 'rect'];
const uiSeriesStylePlugin = {
  id: 'icoSeriesStyle',
  beforeUpdate(chart) {
    const isLine = d => (d.type || chart.config.type) === 'line';
    const sets = (chart.data.datasets || []).filter(isLine);
    const series = sets.filter(d => d._icoStyled || d.borderDash === undefined);
    if (series.length < 2) return;
    // a chart that already uses a dashed line to mean something ("tổng", "ngưỡng") keeps dashes for that:
    // its other series differ by point shape only
    const authorDash = sets.some(d => !d._icoStyled && Array.isArray(d.borderDash) && d.borderDash.length);
    series.forEach((d, i) => {
      d._icoStyled = true;
      if (!authorDash) d.borderDash = UI_SERIES_DASH[i % UI_SERIES_DASH.length];
      d.pointStyle = UI_SERIES_POINT[i % UI_SERIES_POINT.length];
    });
  },
};
if (typeof Chart !== 'undefined') Chart.register(uiChartTablePlugin, uiSeriesStylePlugin);
