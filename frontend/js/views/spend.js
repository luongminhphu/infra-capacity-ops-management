/* =========================================================
   Chi phí & Hợp đồng IT — trang Opex và Cài đặt → Chi phí & nhắc hạn.
   Every business rule lives in the backend (backend/app/spend/);
   this file renders, edits and calls the commands. Roles are checked
   again by the API — the UI only hides what the user cannot do.
   ========================================================= */

const spState = {
  sub: 'opex', meta: null, list: null, counts: null, loading: false, error: null, bound: false,
  filter: { fy: '', status: 'open', category: '', vendor: '', costCenter: '', q: '', page: 1 },
  hosts: null, current: null, settings: {}, selected: '',
};
// Deep links: #/budget/opex?code=OPX-2026-0001 (reminder e-mails, inbox, search), ?vendor=<id>.
// The code prefix decides which detail opens; kept before the router rewrites the hash.
const SP_LINK_RE = /[?&](code|vendor)=([A-Za-z0-9-]{1,30})/;
let spPendingLink = (() => { const m = SP_LINK_RE.exec(location.hash || ''); return m ? { kind: m[1], value: m[2] } : null; })();
function spOpenLink(link) {
  if (!link) return;
  const v = link.value;
  if (link.kind === 'vendor') { if (typeof vdOpen === 'function') vdOpen(v); return; }
  if (/^CPX-/.test(v) && typeof cpxOpenDetail === 'function') return cpxOpenDetail(v);
  if (/^HD-/.test(v) && typeof ctOpenDetail === 'function') return ctOpenDetail(v);
  if (/^TT-/.test(v) && typeof ppOpenDetail === 'function') return ppOpenDetail(v);
  if (/^BG-/.test(v) && typeof qtOpen === 'function') { qtState.tab = 'quotes'; return qtOpen(v); }
  if (/^CTVP-/.test(v) && typeof rnOpen === 'function') return rnOpen(v);
  return spOpenDetail(v);
}
const SP_ROUTE_RE = /^#\/(spend-|budget|procure|contracts|docs)/;
if (!SP_ROUTE_RE.test(location.hash || '')) spPendingLink = null;
window.addEventListener('hashchange', () => {
  const m = SP_ROUTE_RE.test(location.hash || '') ? SP_LINK_RE.exec(location.hash || '') : null;
  if (!m) { spPendingLink = null; return; }
  const link = { kind: m[1], value: m[2] };
  // Same page already shown: open now; otherwise after the page renders (renderSpendView).
  if (spState.meta && appState.view === 'spend' && !spState.rendering) spOpenLink(link);
  else spPendingLink = link;
});

const SP_RANK = { none: 0, viewer: 1, editor: 2, approver: 3, admin: 4 };
const spCan = (min) => SP_RANK[(spState.meta && spState.meta.role) || 'none'] >= SP_RANK[min];
const spMoney = (v) => uiMoney(v, 'VND', 3);
const spDate = (iso) => iso ? esc(String(iso).slice(0, 10).split('-').reverse().join('/')) : '—';
// API timestamps are UTC (offset attached); a bare one is UTC too. Shown in the viewer's local time.
const spTime = (iso) => {
  if (!iso) return '—';
  const s = String(iso), d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(s) ? s : `${s}Z`);
  return isNaN(d) ? esc(s) : d.toLocaleString('vi-VN', { hour12: false, hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit', year: 'numeric' });
};
const spPill = (tone, label) => uiPill(tone, label);
const spOpt = (value, label, sel) => `<option value="${esc(value)}"${String(sel) === String(value) ? ' selected' : ''}>${esc(label)}</option>`;
const SP_CYCLES = [['yearly', 'Hằng năm'], ['quarterly', 'Hằng quý'], ['monthly', 'Hằng tháng'], ['one_time', 'Một lần']];
const SP_STATUS_FILTER = [['open', 'Cần theo dõi (dự kiến + hiệu lực)'], ['expiring', 'Sắp hết hạn'], ['overdue', 'Quá hạn'],
  ['planned', 'Dự kiến'], ['active', 'Đang hiệu lực'], ['renewed', 'Đã gia hạn'], ['stopped', 'Ngừng'], ['all', 'Tất cả']];
const SP_GROUPS = { hw_warranty: 'Bảo hành phần cứng', license: 'License', dc_maintenance: 'Bảo trì DC', outsourced: 'Thuê ngoài', other: 'Khác' };

function spDaysCell(it) {
  if (it.status !== 'active') return '<span class="muted-sub">—</span>';
  const d = it.daysLeft;
  return spPill(it.tone, d < 0 ? `Quá ${-d} ngày` : d === 0 ? 'Hôm nay' : `${d} ngày`);
}

async function spLoadMeta(force) {
  if (spState.meta && !force) return;
  spState.meta = await apiRequest('/spend/meta');
}

async function spLoadList() {
  const f = spState.filter;
  const qs = new URLSearchParams({ page: f.page, size: 50 });
  ['fy', 'status', 'category', 'vendor', 'costCenter', 'q'].forEach(k => { if (f[k] !== '' && f[k] !== null) qs.set(k, f[k]); });
  spState.loading = true;
  try {
    // one request: the list carries the whole-register expiring / overdue counters (audit API-04)
    const list = await apiRequest(`/spend/opex?${qs}`);
    spState.list = list;
    spState.counts = list.counts || { expiring: 0, overdue: 0 };
    spState.error = null;
  } catch (err) { spState.error = err.message; }
  spState.loading = false;
}

async function renderSpendView() {
  spState.rendering = true;
  try { await spRenderSpendSub(); } finally { spState.rendering = false; }
  if (spPendingLink && spState.meta) { const l = spPendingLink; spPendingLink = null; spOpenLink(l); }
}

async function spRenderSpendSub() {
  const mount = $('#spendMount');
  if (!mount) return;
  // every Chi phí tab shows the same loading state while its data comes (several showed nothing)
  mount.innerHTML = uiLoading();
  try {
    await spLoadMeta();
  } catch (err) {
    mount.innerHTML = `<div class="panel">${uiError(err.message)}</div>`;
    return;
  }
  if (spState.sub === 'capex' && typeof renderCapexPage === 'function') { await renderCapexPage(); return; }
  if (spState.sub === 'plan' && typeof renderPlanPage === 'function') { await renderPlanPage(); return; }
  if (spState.sub === 'overview' && typeof renderSpendOverview === 'function') { await renderSpendOverview(); return; }
  if (spState.sub === 'contracts' && typeof renderContractsPage === 'function') { await renderContractsPage(); return; }
  if (spState.sub === 'proposals' && typeof renderProposalsPage === 'function') { await renderProposalsPage(); return; }
  if (spState.sub === 'vendors' && typeof renderVendorsPage === 'function') { await renderVendorsPage(); return; }
  if (spState.sub === 'quotes' && typeof renderQuotesPage === 'function') { await renderQuotesPage(); return; }
  if (spState.sub === 'renovations' && typeof renderRenovationsPage === 'function') { await renderRenovationsPage(); return; }
  if (spState.sub === 'payments' && typeof renderPaymentsPage === 'function') { await renderPaymentsPage(); return; }
  await spLoadList();
  spRenderPage();
  spBindPage();

}

/* Opex list → detail (design-system/ico/pages/danh-sach.md). The pane exists only here and on wide screens;
   spOpenDetail falls back to the dialog everywhere else (Capex, deep links on phones). */
function spDetailPane() {
  const pane = $('#spDetail');
  return pane && matchMedia('(min-width: 1024px)').matches ? pane : null;
}

/* Shared by Hợp đồng / Đợt thanh toán: render an object's detail in the page's pane on wide screens, else in the
   dialog. Same body / ids either way; the dialog's stale copy is cleared so ids stay unique. `title` is escaped HTML. */
/* List → detail helpers (uiPaneOrDialog, uiListKeys, uiListSelect, uiPanelDialog) live in ui.js (audit FE-20 / FE-25). */

const SP_VIEWS = [['open', 'Cần theo dõi'], ['expiring', 'Sắp hết hạn'], ['overdue', 'Quá hạn'], ['all', 'Tất cả']];

/* Time buckets of the page's items (server order: by end date). Money per bucket = sum of the rows shown. */
function spBuckets(items) {
  const order = [['overdue', 'Quá hạn'], ['d30', 'Trong 30 ngày'], ['d90', '31–90 ngày'], ['later', 'Sau 90 ngày'], ['other', 'Dự kiến / đã gia hạn / ngừng']];
  const key = (it) => it.status !== 'active' ? 'other' : it.daysLeft < 0 ? 'overdue' : it.daysLeft <= 30 ? 'd30' : it.daysLeft <= 90 ? 'd90' : 'later';
  return order.map(([k, label]) => { const g = items.filter(it => key(it) === k); return { k, label, items: g, sum: g.reduce((t, it) => t + (Number(it.amountGross) || 0), 0) }; })
    .filter(g => g.items.length);
}

function spRenderPage() {
  const m = spState.meta, f = spState.filter, l = spState.list || { items: [], total: 0, sumGross: 0 }, c = spState.counts || {};
  const maxDays = Math.max(...m.settings.reminderDays);
  const pages = Math.max(1, Math.ceil(l.total / 50));
  const row = (it) => `<li class="sp-li" role="option" id="sp-opt-${esc(it.code)}" data-sp-code="${esc(it.code)}" aria-selected="${it.code === spState.selected}">
      <span class="hm-lv lv-${esc(it.tone)}" aria-hidden="true"></span>
      <span class="sp-li-t">${esc(it.title)}</span>
      <span class="sp-li-r"><b class="num">${spMoney(it.amountGross)}</b>${it.status === 'active' ? esc(it.daysLeft < 0 ? `quá ${-it.daysLeft} ngày` : it.daysLeft === 0 ? 'hết hạn hôm nay' : `còn ${it.daysLeft} ngày`) : esc(it.displayLabel)}</span>
      <span class="sp-li-m">${esc(it.code)} · ${esc(it.vendorName || 'chưa có NCC')} · ${spDate(it.endDate)}${it.ownerName ? ` · ${esc(it.ownerName)}` : ''}</span>
      <span class="sr-only">${esc(it.displayLabel)}</span></li>`;
  const groups = spBuckets(l.items).map(g => `<li class="hm-group" role="presentation"><span>${esc(g.label)}</span><span class="num">${spMoney(g.sum)}</span></li>${g.items.map(row).join('')}`).join('');
  const extraFilters = [f.fy, f.category, f.vendor, f.costCenter].filter(v => v !== '' && v !== null).length + (SP_VIEWS.some(([v]) => v === f.status) ? 0 : 1);
  $('#spendMount').innerHTML = `
    <div class="kpi-strip-wrap">${uiKpiStrip([
      { label: 'Tổng giá trị sau VAT', valueHtml: spMoney(l.sumGross), sub: `${l.total} khoản theo bộ lọc · trước VAT ${spMoney(l.sumNet)}` },
      { label: 'Sắp hết hạn', valueHtml: `<span class="${c.expiring ? 'tone-warn' : ''}">${esc(c.expiring ?? '—')}</span>`, sub: `Đang hiệu lực, hết hạn trong ${maxDays} ngày` },
      { label: 'Quá hạn chưa xử lý', valueHtml: `<span class="${c.overdue ? 'tone-crit' : ''}">${esc(c.overdue ?? '—')}</span>`, sub: 'Đã qua ngày kết thúc nhưng chưa gia hạn / ngừng' },
      { label: 'Năm tài chính hiện tại', value: String(m.currentFiscalYear), sub: `Bắt đầu tháng ${m.settings.fiscalStartMonth} · vai trò của bạn: ${m.roleLabel}` },
    ])}</div>
    <h2 class="sr-only">Danh sách Opex</h2>
    <section class="hm-split panel sp-split" aria-label="Danh sách Opex">
      <div class="hm-list">
        <div class="hm-toolbar">
          <div class="hm-views" role="group" aria-label="Chế độ xem">${SP_VIEWS.map(([v, t]) => `<button class="hm-view" type="button" data-sp-view="${v}" aria-pressed="${f.status === v}">${esc(t)}${v === 'expiring' && c.expiring ? ` <span class="n">${esc(c.expiring)}</span>` : v === 'overdue' && c.overdue ? ` <span class="n">${esc(c.overdue)}</span>` : ''}</button>`).join('')}</div>
          ${spCan('editor') ? '<button class="btn btn-secondary btn-sm" type="button" data-sp-import="opex" title="Đổ dữ liệu từ file Excel đang quản lý"><i data-lucide="upload"></i><span class="sr-only">Nhập Excel</span></button><button class="btn btn-primary btn-sm" type="button" data-sp-new><i data-lucide="plus"></i>Thêm khoản</button>' : ''}
        </div>
        <div class="sp-search-row">
          <label class="sr-only" for="spQ">Tìm Opex</label>
          <input id="spQ" type="search" data-sp-filter="q" value="${esc(f.q)}" placeholder="Mã, dịch vụ, NCC, HĐ / PO, serial…">
          <details class="sp-more"${extraFilters ? ' open' : ''}><summary>Lọc${extraFilters ? ` <span class="n">${extraFilters}</span>` : ''}</summary>
            <div class="sp-filters sp-filters-pop">
              <label class="field"><span>Năm tài chính</span><select data-sp-filter="fy">${spOpt('', 'Tất cả', f.fy)}${m.fiscalYears.map(y => spOpt(y, `FY ${y}`, f.fy)).join('')}</select></label>
              <label class="field"><span>Trạng thái</span><select data-sp-filter="status">${SP_STATUS_FILTER.map(([v, t]) => spOpt(v, t, f.status)).join('')}</select></label>
              <label class="field"><span>Loại chi phí</span><select data-sp-filter="category">${spOpt('', 'Tất cả', f.category)}${m.categories.map(x => spOpt(x.id, x.name, f.category)).join('')}</select></label>
              <label class="field"><span>Nhà cung cấp</span><select data-sp-filter="vendor">${spOpt('', 'Tất cả', f.vendor)}${m.vendors.map(x => spOpt(x.id, x.name, f.vendor)).join('')}</select></label>
              <label class="field"><span>Cost center</span><select data-sp-filter="costCenter">${spOpt('', 'Tất cả', f.costCenter)}${m.costCenters.map(x => spOpt(x.id, `${x.code} — ${x.name}`, f.costCenter)).join('')}</select></label>
            </div></details>
        </div>
        ${spState.error ? `${uiError(spState.error)}` : ''}
        ${groups ? `<ul class="hm-rows" id="spList" role="listbox" tabindex="0" aria-label="Khoản Opex, sắp theo ngày hết hạn">${groups}</ul>`
          : uiEmpty(spCan('editor') ? 'Chưa có khoản nào khớp bộ lọc. Bấm “Thêm khoản” để nhập, hoặc “Nhập Excel”.' : 'Chưa có khoản nào khớp bộ lọc.')}
        <div class="hm-hint sp-pager"><span>${esc(l.total)} khoản · trang ${esc(f.page)}/${esc(pages)}</span>
          <button class="btn btn-ghost btn-sm" type="button" data-sp-page="${f.page - 1}" ${f.page <= 1 ? 'disabled' : ''}>Trước</button>
          <button class="btn btn-ghost btn-sm" type="button" data-sp-page="${f.page + 1}" ${f.page >= pages ? 'disabled' : ''}>Sau</button>
          <span><kbd>J</kbd> <kbd>K</kbd> chuyển</span></div>
      </div>
      <div class="hm-detail sp-detail" id="spDetail" aria-live="polite" aria-labelledby="spPaneTitle">${l.items.length ? uiNote('Đang mở…') : uiEmpty('Chọn một khoản ở danh sách bên trái.')}</div>
    </section>`;
  refreshIcons();
  if (spDetailPane() && l.items.length) {
    const code = l.items.some(it => it.code === spState.selected) ? spState.selected : l.items[0].code;
    spOpenDetail(code);
  }
}

let spSearchTimer = null;
function spBindPage() {
  const mount = $('#spendMount');
  if (spState.bound) return;
  spState.bound = true;
  mount.addEventListener('change', async (e) => {
    const el = e.target.closest('[data-sp-filter]');
    if (!el || el.dataset.spFilter === 'q') return;
    spState.filter[el.dataset.spFilter] = el.value;
    spState.filter.page = 1;
    await spLoadList(); spRenderPage();
  });
  mount.addEventListener('input', (e) => {
    const el = e.target.closest('[data-sp-filter="q"]');
    if (!el) return;
    clearTimeout(spSearchTimer);
    spSearchTimer = setTimeout(async () => {
      spState.filter.q = el.value.trim(); spState.filter.page = 1;
      await spLoadList(); spRenderPage();
      const box = $('[data-sp-filter="q"]'); if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
    }, 350);
  });
  mount.addEventListener('click', async (e) => {
    const view = e.target.closest('[data-sp-view]');
    if (view) { spState.filter.status = view.dataset.spView; spState.filter.page = 1; await spLoadList(); spRenderPage(); return; }
    const row = e.target.closest('[data-sp-code]');
    if (row) return spOpenDetail(row.dataset.spCode);
    if (e.target.closest('[data-sp-new]')) return spOpenForm(null);
    const pg = e.target.closest('[data-sp-page]');
    if (pg && !pg.disabled) { spState.filter.page = Number(pg.dataset.spPage); await spLoadList(); spRenderPage(); }
  });
  mount.addEventListener('keydown', (e) => {
    const lb = e.target.closest('#spList');
    if (!lb || e.ctrlKey || e.metaKey || e.altKey) return;
    const codes = [...lb.querySelectorAll('[role="option"]')].map(o => o.dataset.spCode);
    const i = codes.indexOf(spState.selected), k = e.key.toLowerCase();
    let next = null;
    if (k === 'arrowdown' || k === 'j') next = codes[Math.min(i + 1, codes.length - 1)];
    else if (k === 'arrowup' || k === 'k') next = codes[Math.max(i - 1, 0)];
    else if (k === 'enter' || k === ' ') next = spState.selected || codes[0];
    if (!next) return;
    e.preventDefault();
    const opt = lb.querySelector(`[data-sp-code="${CSS.escape(next)}"]`);
    if (opt) { lb.setAttribute('aria-activedescendant', opt.id); opt.scrollIntoView({ block: 'nearest' }); }
    spOpenDetail(next);
  });
}

async function spRefresh() {
  await spLoadMeta(true);
  await spLoadList();
  if (appState.view === 'spend') spRenderPage();
}

/* ---------- Dialog shell ---------- */
function spMsg(text, ok) {
  const dlg = $('#spDialog');
  const el = (dlg && dlg.open && $('#spDialogMsg', dlg)) || $('#spDialogMsg');
  if (!el) return;
  el.classList.add('src-result');
  uiSetStatus(el, text, text ? (ok ? 'ok' : 'error') : '');
}
function spOnce(selector, handler) {
  const el = $(selector);
  if (el) el.onclick = async (e) => { e.preventDefault(); el.disabled = true; try { await handler(e); } finally { el.disabled = false; } };
}

/* ---------- Detail ---------- */
async function spOpenDetail(code) {
  let it;
  try {
    await spLoadMeta();
    it = await apiRequest(`/spend/opex/${encodeURIComponent(code)}`);
  } catch (err) { uiAlert(err.message); return; }
  spState.current = it;
  const dl = (label, value) => `<div class="sp-dl-item"><dt>${label}</dt><dd>${value}</dd></div>`;
  const link = (c) => c ? `<button class="btn btn-ghost btn-sm sp-link" type="button" data-sp-goto="${esc(c)}">${esc(c)}</button>` : '—';
  const editable = !['renewed', 'stopped'].includes(it.status);
  const assets = it.assets.length ? `<div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Serial</th><th>Mô tả</th><th>Host vCenter</th></tr></thead><tbody>
      ${it.assets.map(a => `<tr><td>${esc(a.serial)}</td><td class="wrap">${esc(a.description || '')}</td><td>${a.hostName ? `${esc(a.hostName)}<span class="muted-sub">${esc(a.vcenterKey || '')}</span>` : '—'}</td></tr>`).join('')}
    </tbody></table></div>` : '<p class="muted-sub">Chưa gắn thiết bị.</p>';
  const filesHtml = `<ul class="sp-files">${it.attachments.map(f => `<li>
      <a href="/api/spend/files/${encodeURIComponent(f.id)}" download>${esc(f.name)}</a>
      <span class="muted-sub">${fmtInt(f.sizeBytes / 1024)} KB · ${esc(f.uploadedBy)} · ${esc(spTime(f.uploadedAt))}</span>
      ${spCan('editor') ? `<button class="btn btn-ghost btn-sm btn-danger" type="button" data-sp-detach="${esc(f.id)}">Xoá</button>` : ''}</li>`).join('') || '<li class="muted-sub">Chưa có file.</li>'}</ul>
    ${spCan('editor') ? '<label class="field"><span>Thêm file (PDF, .docx, .xlsx)</span><input type="file" id="spAttach" accept=".pdf,.docx,.xlsx"></label>' : ''}`;
  const body = `
    <div class="sp-detail-head">${spPill(it.tone, it.displayLabel)} ${it.status === 'active' ? spDaysCell(it) : ''}</div>
    <dl class="sp-dl">
      ${dl('Loại chi phí', esc(it.categoryName || '—'))}
      ${dl('Nhà cung cấp', esc(it.vendorName || '—'))}
      ${dl('Hiệu lực', `${spDate(it.startDate)} – ${spDate(it.endDate)}`)}
      ${dl('Chu kỳ / FY', `${esc(it.billingCycleLabel)} · FY ${esc(it.fiscalYear)}`)}
      ${dl('Trước VAT', spMoney(it.amountNet))}
      ${dl('Sau VAT', `<b>${spMoney(it.amountGross)}</b> <span class="muted-sub">VAT ${esc(it.vatPct)}%</span>`)}
      ${dl('Mã hợp đồng / PO', `${esc(it.contractRef || '—')} / ${esc(it.poRef || '—')}`)}
      ${dl('Cost center', esc(it.costCenterName || '—'))}
      ${dl('Người phụ trách', `${esc(it.ownerName)}${it.ownerEmail ? `<span class="muted-sub">${esc(it.ownerEmail)}</span>` : ''}`)}
      ${dl('Nguồn gốc', `${esc(it.originLabel)}${it.renewedFromCode ? ` từ ${link(it.renewedFromCode)}` : ''}${it.capexProjectCode ? ` — dự án <button class="btn btn-ghost btn-sm sp-link" type="button" data-cpx-goto="${esc(it.capexProjectCode)}">${esc(it.capexProjectCode)}</button>` : ''}${it.managedByCapex ? '<span class="muted-sub">Tự cập nhật theo Capex cho tới khi được xác nhận hoặc sửa tay</span>' : ''}`)}
      ${it.renewedToCode ? dl('Đã gia hạn sang', link(it.renewedToCode)) : ''}
      ${it.stopReason ? dl('Lý do ngừng', esc(it.stopReason)) : ''}
      ${it.confirmedBy ? dl('Xác nhận', `${esc(it.confirmedBy)} · ${esc(spTime(it.confirmedAt))}`) : ''}
      ${dl('Cập nhật', `${esc(it.updatedBy || it.createdBy)} · ${esc(spTime(it.updatedAt || it.createdAt))}`)}
    </dl>
    ${it.description ? `<p class="sp-desc">${esc(it.description)}</p>` : ''}
    <h4 class="sp-h">Hợp đồng</h4>${(it.contracts || []).length ? `<ul class="sp-files">${it.contracts.map(c => `<li><button class="btn btn-ghost btn-sm sp-link" type="button" data-ct-goto="${esc(c.code)}">${esc(c.code)}</button> ${esc(c.title)}</li>`).join('')}</ul>` : '<p class="muted-sub">Chưa có hợp đồng.</p>'}
    ${spCan('editor') ? '<div class="sp-inline"><button class="btn btn-ghost btn-sm" type="button" data-ct-new-for="1"><i data-lucide="plus"></i>Tạo hợp đồng cho khoản này</button><button class="btn btn-ghost btn-sm" type="button" data-pp-new-for="1"><i data-lucide="file-text"></i>Soạn tờ trình</button></div>' : ''}
    <h4 class="sp-h">Thiết bị liên quan</h4>${assets}
    <h4 class="sp-h">File đính kèm</h4>${filesHtml}
    <details class="sp-history" id="spHistory"><summary>Lịch sử thay đổi</summary><div class="muted-sub">Đang tải…</div></details>`;
  const btn = (attr, label, cls = 'btn-secondary') => `<button class="btn ${cls} btn-sm" type="button" ${attr}>${label}</button>`;
  const actions = [
    spCan('admin') ? btn('id="spDelete"', 'Xoá', 'btn-secondary btn-danger') : '',
    '<span class="src-spacer"></span>',
    spCan('approver') && it.status === 'planned' ? btn('id="spConfirm"', 'Xác nhận') : '',
    spCan('approver') && it.status === 'stopped' ? btn('id="spReactivate"', 'Kích hoạt lại') : '',
    spCan('editor') && ['planned', 'active'].includes(it.status) ? btn('id="spStop"', 'Ngừng') : '',
    spCan('editor') && editable ? btn('id="spEdit"', 'Sửa') : '',
    spCan('editor') && it.status === 'active' ? btn('id="spRenew"', 'Gia hạn', 'btn-primary') : '',
  ].join('');
  const pane = spDetailPane();
  if (pane) {
    // pages/danh-sach.md: the object opens beside the list, actions first. Same content and ids as the dialog;
    // the dialog's stale copy is cleared so the ids stay unique.
    if (!$('#spDialog').open) $('#spDialogBody').innerHTML = '';
    spState.selected = it.code;
    $$('#spList [role="option"]').forEach(o => o.setAttribute('aria-selected', String(o.dataset.spCode === it.code)));
    const render = () => {
      pane.innerHTML = `<div class="sp-pane-head"><h3 id="spPaneTitle">${esc(it.title)}</h3><span class="muted-sub">${esc(it.code)}</span></div>
        <div class="sp-pane-actions">${actions.replace('<span class="src-spacer"></span>', '')}</div>
        <p class="src-result" id="spDialogMsg" role="status"></p>
        <div class="sp-dialog-body">${body}</div>`;
      refreshIcons();
    };
    if (document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) await document.startViewTransition(render).updateCallbackDone; else render();
  } else {
    uiPanelDialog(`${esc(it.code)} — ${esc(it.title)}`, body, actions);
  }
  const body$ = pane || $('#spDialogBody');
  body$.onclick = async (e) => {
    const go = e.target.closest('[data-sp-goto]');
    if (go) return spOpenDetail(go.dataset.spGoto);
    const cpx = e.target.closest('[data-cpx-goto]');
    if (cpx && typeof cpxOpenDetail === 'function') return cpxOpenDetail(cpx.dataset.cpxGoto);
    const ct = e.target.closest('[data-ct-goto]');
    if (ct && typeof ctOpenDetail === 'function') return ctOpenDetail(ct.dataset.ctGoto);
    if (e.target.closest('[data-pp-new-for]') && typeof ppOpenCreate === 'function') return ppOpenCreate({ opexCodes: [it.code], subject: it.title });
    if (e.target.closest('[data-ct-new-for]') && typeof ctOpenCreate === 'function') return ctOpenCreate({ kind: 'opex', code: it.code, title: `Hợp đồng ${it.title}` });
    const del = e.target.closest('[data-sp-detach]');
    if (del && await uiConfirm('Xoá file đính kèm này?')) {
      try { await apiRequest(`/spend/opex/${encodeURIComponent(it.code)}/attachments/${encodeURIComponent(del.dataset.spDetach)}`, { method: 'DELETE' }); spOpenDetail(it.code); }
      catch (err) { spMsg(err.message); }
    }
  };
  const fileInput = $('#spAttach');
  if (fileInput) fileInput.onchange = async () => {
    const file = fileInput.files[0];
    if (!file) return;
    const fd = new FormData(); fd.append('file', file);
    spMsg('Đang tải lên…', true);
    try { await apiRequest(`/spend/opex/${encodeURIComponent(it.code)}/attachments`, { method: 'POST', body: fd }); spOpenDetail(it.code); }
    catch (err) { spMsg(err.message); fileInput.value = ''; }
  };
  $('#spHistory').addEventListener('toggle', async (e) => {
    if (!e.target.open || e.target.dataset.loaded) return;
    e.target.dataset.loaded = '1';
    try {
      const h = await apiRequest(`/spend/opex/${encodeURIComponent(it.code)}/history`);
      e.target.querySelector('div').outerHTML = `<ul class="sp-history-list">${h.map(r => `<li><b>${esc(spActionLabel(r.action))}</b> · ${esc(r.username)} · ${esc(spTime(r.at))}${spChanges(r.detail)}</li>`).join('') || '<li>Chưa có thay đổi.</li>'}</ul>`;
    } catch (err) { e.target.querySelector('div').textContent = err.message; }
  });
  const command = (name, payload) => async () => {
    try { await apiRequest(`/spend/opex/${encodeURIComponent(it.code)}/${name}`, { method: 'POST', body: JSON.stringify(payload || {}) }); await spRefresh(); spOpenDetail(it.code); }
    catch (err) { spMsg(err.message); }
  };
  spOnce('#spConfirm', command('confirm'));
  spOnce('#spReactivate', command('reactivate'));
  spOnce('#spStop', async () => spOpenStop(it));
  spOnce('#spEdit', async () => spOpenForm(it));
  spOnce('#spRenew', async () => spOpenRenew(it));
  spOnce('#spDelete', async () => {
    if (!await uiConfirm(`Xoá vĩnh viễn ${it.code}? Thao tác được ghi nhật ký kiểm toán. Nếu chỉ không dùng nữa, hãy chọn “Ngừng”.`)) return;
    try { await apiRequest(`/spend/opex/${encodeURIComponent(it.code)}`, { method: 'DELETE' }); $('#spDialog').close(); spRefresh(); }
    catch (err) { spMsg(err.message); }
  });
}

const SP_ACTIONS = { create: 'Tạo', update: 'Sửa', renew: 'Gia hạn', stop: 'Ngừng', confirm: 'Xác nhận', reactivate: 'Kích hoạt lại',
  attach: 'Thêm file', detach: 'Xoá file', delete: 'Xoá' };
function spActionLabel(a) { return SP_ACTIONS[String(a).split('.').pop()] || a; }
function spChanges(detail) {
  try {
    const d = JSON.parse(detail || '{}');
    if (d.changes && Object.keys(d.changes).length) {
      return `<span class="muted-sub bk-wrap">${Object.entries(d.changes).map(([k, [a, b]]) => `${esc(k)}: ${esc(a)} → ${esc(b)}`).join(' · ')}</span>`;
    }
    if (d.newCode) return `<span class="muted-sub">→ ${esc(d.newCode)}</span>`;
    if (d.file) return `<span class="muted-sub">${esc(d.file)}</span>`;
    if (d.reason) return `<span class="muted-sub">${esc(d.reason)}</span>`;
  } catch (err) { /* ignore */ }
  return '';
}

/* ---------- Create / edit ---------- */
async function spLoadHosts() {
  if (spState.hosts) return spState.hosts;
  try { spState.hosts = await apiRequest('/spend/hosts'); } catch (err) { spState.hosts = []; }
  return spState.hosts;
}

function spAssetRow(a = {}) {
  return `<div class="sp-asset-row">
    <input type="text" data-a="serial" placeholder="Serial *" maxlength="128" value="${esc(a.serial || '')}" aria-label="Serial">
    <input type="text" data-a="description" placeholder="Mô tả (model, vị trí…)" maxlength="255" value="${esc(a.description || '')}" aria-label="Mô tả thiết bị">
    <input type="text" data-a="hostName" list="spHostList" placeholder="Host vCenter (tuỳ chọn)" maxlength="255" value="${esc(a.hostName || '')}" aria-label="Host vCenter">
    <button class="btn btn-ghost btn-sm" type="button" data-sp-asset-del aria-label="Bỏ thiết bị"><i data-lucide="x"></i></button></div>`;
}

async function spOpenForm(it) {
  const m = spState.meta, v = it || { billingCycle: 'yearly', vatPct: 10, status: 'active', assets: [] };
  const hosts = await spLoadHosts();
  const cats = m.categories.filter(c => c.active || c.id === v.categoryId);
  const body = `<form id="spForm" class="sp-form" novalidate>
    <div class="src-grid">
      <label class="field"><span>Loại chi phí *</span><select name="categoryId" required>${spOpt('', '— Chọn —', v.categoryId || '')}${cats.map(c => spOpt(c.id, c.name, v.categoryId)).join('')}</select></label>
      <label class="field"><span>Nhà cung cấp *</span><input name="vendorName" list="spVendorList" required maxlength="255" value="${esc(v.vendorName || '')}" placeholder="Gõ tên — tên mới sẽ được thêm"></label>
      <label class="field sp-span2"><span>Nội dung dịch vụ *</span><input name="title" required maxlength="255" value="${esc(v.title || '')}" placeholder="Vd: Gia hạn bảo hành 4 máy chủ Dell R750 cụm PROD"></label>
      <label class="field"><span>Ngày bắt đầu *</span><input type="date" name="startDate" required value="${esc(v.startDate || '')}"></label>
      <label class="field"><span>Ngày kết thúc *</span><input type="date" name="endDate" required value="${esc(v.endDate || '')}"></label>
      <label class="field"><span>Chu kỳ</span><select name="billingCycle">${SP_CYCLES.map(([a, b]) => spOpt(a, b, v.billingCycle)).join('')}</select></label>
      <label class="field"><span>Năm tài chính</span><input type="number" name="fiscalYear" min="2000" max="2100" value="${esc(v.fiscalYear || '')}" placeholder="Tự tính từ ngày bắt đầu"></label>
      <label class="field"><span>Số tiền trước VAT (đ) *</span><input type="number" name="amountNet" min="0" step="1" required value="${esc(v.amountNet ?? '')}"></label>
      <label class="field"><span>VAT (%)</span><input type="number" name="vatPct" min="0" max="100" step="0.01" value="${esc(v.vatPct ?? 10)}"></label>
      <label class="field"><span>Số tiền sau VAT (đ)</span><input type="number" name="amountGross" min="0" step="1" value="${it ? esc(v.amountGross) : ''}" placeholder="Tự tính"></label>
      <label class="field"><span>Cost center</span><select name="costCenterId">${spOpt('', '—', v.costCenterId || '')}${m.costCenters.filter(c => c.active || c.id === v.costCenterId).map(c => spOpt(c.id, `${c.code} — ${c.name}`, v.costCenterId)).join('')}</select></label>
      <label class="field"><span>Mã hợp đồng</span><input name="contractRef" maxlength="64" value="${esc(v.contractRef || '')}"></label>
      <label class="field"><span>Mã PO</span><input name="poRef" maxlength="64" value="${esc(v.poRef || '')}"></label>
      <label class="field"><span>Người phụ trách *</span><input name="ownerName" required maxlength="128" value="${esc(v.ownerName || '')}"></label>
      <label class="field"><span>Email người phụ trách</span><input type="email" name="ownerEmail" maxlength="255" value="${esc(v.ownerEmail || '')}" placeholder="Nhận email nhắc nếu kênh email bật tuỳ chọn này"></label>
      ${it ? '' : `<label class="field"><span>Trạng thái</span><select name="status">${spOpt('active', 'Đang hiệu lực', v.status)}${spOpt('planned', 'Dự kiến (kế hoạch)', v.status)}</select></label>`}
      <label class="field sp-span2"><span>Mô tả</span><textarea name="description" rows="2" maxlength="5000">${esc(v.description || '')}</textarea></label>
    </div>
    <h4 class="sp-h">Thiết bị liên quan</h4>
    <div id="spAssets">${(v.assets || []).map(spAssetRow).join('')}</div>
    <button class="btn btn-ghost btn-sm" type="button" id="spAssetAdd"><i data-lucide="plus"></i>Thêm thiết bị</button>
    <datalist id="spVendorList">${m.vendors.filter(x => x.active).map(x => `<option value="${esc(x.name)}"></option>`).join('')}</datalist>
    <datalist id="spHostList">${hosts.map(h => `<option value="${esc(h.hostName)}">${esc([h.vcenterKey, h.cluster, h.vendor, h.model].filter(Boolean).join(' · '))}</option>`).join('')}</datalist>
  </form>`;
  uiPanelDialog(it ? `Sửa ${esc(it.code)}` : 'Thêm khoản Opex', body,
    `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
     <button class="btn btn-primary btn-sm" type="button" id="spSave">Lưu</button>`);
  const form = $('#spForm');
  const grossHint = () => {
    const net = Number(form.amountNet.value), vat = Number(form.vatPct.value || 0);
    form.amountGross.placeholder = form.amountNet.value ? `Tự tính: ${Math.round(net * (100 + vat) / 100).toLocaleString('vi-VN')}` : 'Tự tính';
  };
  form.amountNet.addEventListener('input', grossHint); form.vatPct.addEventListener('input', grossHint); grossHint();
  $('#spAssetAdd').onclick = () => { $('#spAssets').insertAdjacentHTML('beforeend', spAssetRow()); refreshIcons(); };
  $('#spAssets').onclick = (e) => { const b = e.target.closest('[data-sp-asset-del]'); if (b) b.parentElement.remove(); };
  spOnce('#spSave', async () => {
    const fd = new FormData(form), p = {};
    for (const [k, val] of fd.entries()) p[k] = typeof val === 'string' ? val.trim() : val;
    ['amountNet', 'amountGross', 'vatPct', 'fiscalYear', 'categoryId', 'costCenterId'].forEach(k => {
      if (p[k] === '' || p[k] === undefined) delete p[k]; else p[k] = Number(p[k]);
    });
    p.assets = $$('#spAssets .sp-asset-row').map(r => {
      const a = {}; r.querySelectorAll('[data-a]').forEach(i => { a[i.dataset.a] = i.value.trim(); });
      const h = (spState.hosts || []).find(x => x.hostName === a.hostName);
      if (h) a.vcenterKey = h.vcenterKey;
      return a;
    }).filter(a => a.serial || a.description || a.hostName);
    if (p.assets.some(a => !a.serial)) { spMsg('Mỗi thiết bị cần có serial.'); return; }
    if (it) p.rowVersion = it.rowVersion;
    try {
      const saved = await apiRequest(it ? `/spend/opex/${encodeURIComponent(it.code)}` : '/spend/opex',
        { method: it ? 'PUT' : 'POST', body: JSON.stringify(p) });
      await spRefresh();
      spOpenDetail(saved.code);
    } catch (err) { spMsg(err.message); }
  });
}

/* ---------- Renew / stop ---------- */
function spAddDays(iso, n) { const d = new Date(`${iso}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }

function spOpenRenew(it) {
  const start = spAddDays(it.endDate, 1);
  const span = Math.round((new Date(`${it.endDate}T00:00:00Z`) - new Date(`${it.startDate}T00:00:00Z`)) / 86400000);
  const body = `<p class="panel-sub">Khoản ${esc(it.code)} chuyển sang “Đã gia hạn” và dừng nhắc; một khoản mới được tạo cho kỳ tiếp theo (mã mới theo năm tài chính của ngày bắt đầu).</p>
    <form id="spRenewForm" class="sp-form src-grid" novalidate>
      <label class="field"><span>Ngày bắt đầu kỳ mới</span><input type="date" name="startDate" value="${esc(start)}"></label>
      <label class="field"><span>Ngày kết thúc kỳ mới</span><input type="date" name="endDate" value="${esc(spAddDays(start, span))}"></label>
      <label class="field"><span>Số tiền trước VAT (đ)</span><input type="number" name="amountNet" min="0" step="1" value="${esc(it.amountNet)}"></label>
      <label class="field"><span>VAT (%)</span><input type="number" name="vatPct" min="0" max="100" step="0.01" value="${esc(it.vatPct)}"></label>
      <label class="field"><span>Mã hợp đồng mới</span><input name="contractRef" maxlength="64" value="${esc(it.contractRef || '')}"></label>
      <label class="field"><span>Mã PO mới</span><input name="poRef" maxlength="64" value=""></label>
      <label class="field"><span>Trạng thái khoản mới</span><select name="status">${spOpt('active', 'Đang hiệu lực (đã ký)', 'active')}${spOpt('planned', 'Dự kiến (chưa ký)', '')}</select></label>
    </form>`;
  uiPanelDialog(`Gia hạn ${esc(it.code)}`, body, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
    <button class="btn btn-primary btn-sm" type="button" id="spRenewSave">Gia hạn</button>`);
  spOnce('#spRenewSave', async () => {
    const p = Object.fromEntries(new FormData($('#spRenewForm')).entries());
    ['amountNet', 'vatPct'].forEach(k => { if (p[k] === '') delete p[k]; else p[k] = Number(p[k]); });
    Object.keys(p).forEach(k => { if (p[k] === '') delete p[k]; });
    try {
      const n = await apiRequest(`/spend/opex/${encodeURIComponent(it.code)}/renew`, { method: 'POST', body: JSON.stringify(p) });
      await spRefresh(); spOpenDetail(n.code);
    } catch (err) { spMsg(err.message); }
  });
}

function spOpenStop(it) {
  uiPanelDialog(`Ngừng ${esc(it.code)}`, `<p class="panel-sub">Khoản bị ngừng sẽ không còn được nhắc hạn. Người có quyền Duyệt có thể kích hoạt lại.</p>
    <label class="field"><span>Lý do ngừng *</span><textarea id="spStopReason" rows="3" maxlength="500" placeholder="Vd: thiết bị đã thanh lý, chuyển sang nhà cung cấp khác…"></textarea></label>`,
  `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
   <button class="btn btn-primary btn-sm btn-danger" type="button" id="spStopSave">Ngừng</button>`);
  spOnce('#spStopSave', async () => {
    try {
      await apiRequest(`/spend/opex/${encodeURIComponent(it.code)}/stop`, { method: 'POST', body: JSON.stringify({ reason: $('#spStopReason').value }) });
      await spRefresh(); spOpenDetail(it.code);
    } catch (err) { spMsg(err.message); }
  });
}
