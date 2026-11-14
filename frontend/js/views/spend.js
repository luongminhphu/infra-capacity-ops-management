/* =========================================================
   Chi phí & Hợp đồng IT — trang Opex và Cài đặt → Chi phí & nhắc hạn.
   Every business rule lives in the backend (backend/app/spend/);
   this file renders, edits and calls the commands. Roles are checked
   again by the API — the UI only hides what the user cannot do.
   ========================================================= */

const spState = {
  sub: 'opex', meta: null, list: null, counts: null, loading: false, error: null, bound: false,
  filter: { fy: '', status: 'open', category: '', vendor: '', costCenter: '', q: '', page: 1 },
  hosts: null, current: null, settings: {},
};
// Link from a reminder: #/spend-opex?code=OPX-2026-0001 (kept before the router rewrites the hash).
let spPendingCode = (/[?&]code=([A-Za-z0-9-]{3,30})/.exec(location.hash || '') || [])[1] || null;
window.addEventListener('hashchange', () => {
  const m = /[?&]code=([A-Za-z0-9-]{3,30})/.exec(location.hash || '');
  if (m && spState.meta) spOpenDetail(m[1]);
  else if (m) spPendingCode = m[1];
});

const SP_RANK = { none: 0, viewer: 1, editor: 2, approver: 3, admin: 4 };
const spCan = (min) => SP_RANK[(spState.meta && spState.meta.role) || 'none'] >= SP_RANK[min];
const spMoney = (v) => (v === null || v === undefined || v === '') ? '—' : `${Number(v).toLocaleString('vi-VN')} đ`;
const spDate = (iso) => iso ? String(iso).slice(0, 10).split('-').reverse().join('/') : '—';
// API timestamps are UTC (offset attached); a bare one is UTC too. Shown in the viewer's local time.
const spTime = (iso) => {
  if (!iso) return '—';
  const s = String(iso), d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(s) ? s : `${s}Z`);
  return isNaN(d) ? s : d.toLocaleString('vi-VN', { hour12: false, hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit', year: 'numeric' });
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
    const [list, expiring, overdue] = await Promise.all([
      apiRequest(`/spend/opex?${qs}`),
      apiRequest('/spend/opex?status=expiring&size=1'),
      apiRequest('/spend/opex?status=overdue&size=1'),
    ]);
    spState.list = list;
    spState.counts = { expiring: expiring.total, overdue: overdue.total };
    spState.error = null;
  } catch (err) { spState.error = err.message; }
  spState.loading = false;
}

async function renderSpendView() {
  const mount = $('#spendMount');
  if (!mount) return;
  if (!spState.meta) mount.innerHTML = '<div class="empty-note">Đang tải…</div>';
  try {
    await spLoadMeta();
  } catch (err) {
    mount.innerHTML = `<div class="panel empty-note">${esc(err.message)}</div>`;
    return;
  }
  if (spState.sub === 'capex' && typeof renderCapexPage === 'function') { await renderCapexPage(); return; }
  if (spState.sub === 'overview' && typeof renderSpendOverview === 'function') { await renderSpendOverview(); return; }
  if (spState.sub === 'contracts' && typeof renderContractsPage === 'function') { await renderContractsPage(); return; }
  if (spState.sub === 'proposals' && typeof renderProposalsPage === 'function') { await renderProposalsPage(); return; }
  if (spState.sub === 'vendors' && typeof renderVendorsPage === 'function') { await renderVendorsPage(); return; }
  if (spState.sub === 'quotes' && typeof renderQuotesPage === 'function') { await renderQuotesPage(); return; }
  if (spState.sub === 'renovations' && typeof renderRenovationsPage === 'function') { await renderRenovationsPage(); return; }
  await spLoadList();
  spRenderPage();
  spBindPage();
  if (spPendingCode) { const c = spPendingCode; spPendingCode = null; spOpenDetail(c); }
}

function spRenderPage() {
  const m = spState.meta, f = spState.filter, l = spState.list || { items: [], total: 0, sumGross: 0 }, c = spState.counts || {};
  const maxDays = Math.max(...m.settings.reminderDays);
  const rows = l.items.map(it => `
    <tr data-sp-code="${esc(it.code)}" tabindex="0" class="sp-row">
      <td><b>${esc(it.code)}</b><span class="muted-sub">NTC ${esc(it.fiscalYear)} · ${esc(it.originLabel || '')}</span></td>
      <td class="wrap">${esc(it.title)}<span class="muted-sub">${esc(it.categoryName || '')}</span></td>
      <td class="wrap">${esc(it.vendorName || '—')}</td>
      <td>${spDate(it.startDate)} – ${spDate(it.endDate)}<span class="muted-sub">${esc(it.billingCycleLabel || '')}</span></td>
      <td>${spDaysCell(it)}</td>
      <td class="num">${spMoney(it.amountGross)}</td>
      <td class="wrap">${esc(it.ownerName)}</td>
      <td>${spPill(it.tone, it.displayLabel)}</td>
    </tr>`).join('');
  const pages = Math.max(1, Math.ceil(l.total / 50));
  $('#spendMount').innerHTML = `
    <section class="kpi-grid">
      <div class="kpi-card"><div class="label">Tổng giá trị (sau VAT)</div><div class="value">${spMoney(l.sumGross)}</div><div class="sub">${l.total} khoản theo bộ lọc · trước VAT ${spMoney(l.sumNet)}</div></div>
      <div class="kpi-card"><div class="label">Sắp hết hạn</div><div class="value ${c.expiring ? 'tone-warn' : 'tone-ok'}">${c.expiring ?? '—'}</div><div class="sub">Đang hiệu lực, hết hạn trong ${maxDays} ngày</div></div>
      <div class="kpi-card"><div class="label">Quá hạn chưa xử lý</div><div class="value ${c.overdue ? 'tone-crit' : 'tone-ok'}">${c.overdue ?? '—'}</div><div class="sub">Đã qua ngày kết thúc nhưng chưa gia hạn / ngừng</div></div>
      <div class="kpi-card"><div class="label">Năm tài chính hiện tại</div><div class="value">${esc(m.currentFiscalYear)}</div><div class="sub">Bắt đầu tháng ${esc(m.settings.fiscalStartMonth)} · vai trò của bạn: ${esc(m.roleLabel)}</div></div>
    </section>
    <section class="table-card">
      <div class="panel-head">
        <div><div class="panel-title">Danh sách Opex</div><p class="panel-sub">Bấm một dòng để xem chi tiết, gia hạn hoặc ngừng. Sắp xếp theo ngày hết hạn.</p></div>
        <div class="panel-toolbar">
          ${spCan('editor') ? '<button class="btn btn-primary btn-sm" type="button" data-sp-new><i data-lucide="plus"></i>Thêm khoản</button>' : ''}
        </div>
      </div>
      <div class="sp-filters">
        <label class="field"><span>Năm tài chính</span><select data-sp-filter="fy">${spOpt('', 'Tất cả', f.fy)}${m.fiscalYears.map(y => spOpt(y, `NTC ${y}`, f.fy)).join('')}</select></label>
        <label class="field"><span>Trạng thái</span><select data-sp-filter="status">${SP_STATUS_FILTER.map(([v, t]) => spOpt(v, t, f.status)).join('')}</select></label>
        <label class="field"><span>Loại chi phí</span><select data-sp-filter="category">${spOpt('', 'Tất cả', f.category)}${m.categories.map(x => spOpt(x.id, x.name, f.category)).join('')}</select></label>
        <label class="field"><span>Nhà cung cấp</span><select data-sp-filter="vendor">${spOpt('', 'Tất cả', f.vendor)}${m.vendors.map(x => spOpt(x.id, x.name, f.vendor)).join('')}</select></label>
        <label class="field"><span>Cost center</span><select data-sp-filter="costCenter">${spOpt('', 'Tất cả', f.costCenter)}${m.costCenters.map(x => spOpt(x.id, `${x.code} — ${x.name}`, f.costCenter)).join('')}</select></label>
        <label class="field sp-search"><span>Tìm</span><input type="text" data-sp-filter="q" value="${esc(f.q)}" placeholder="Mã, dịch vụ, NCC, HĐ / PO, serial…"></label>
      </div>
      ${spState.error ? `<p class="criteria-status error">${esc(spState.error)}</p>` : ''}
      <div class="table-scroll">
        <table class="data-table sp-table">
          <thead><tr><th>Mã</th><th>Dịch vụ</th><th>Nhà cung cấp</th><th>Hiệu lực</th><th>Còn lại</th><th class="num">Sau VAT</th><th>Phụ trách</th><th>Trạng thái</th></tr></thead>
          <tbody>${rows || `<tr><td colspan="8" class="empty-note">${spCan('editor') ? 'Chưa có khoản nào khớp bộ lọc — bấm “Thêm khoản” để nhập.' : 'Chưa có khoản nào khớp bộ lọc.'}</td></tr>`}</tbody>
        </table>
      </div>
      <div class="row-count sp-pager">
        <span>${l.total} khoản · trang ${f.page}/${pages}</span>
        <button class="btn btn-ghost btn-sm" type="button" data-sp-page="${f.page - 1}" ${f.page <= 1 ? 'disabled' : ''}>Trước</button>
        <button class="btn btn-ghost btn-sm" type="button" data-sp-page="${f.page + 1}" ${f.page >= pages ? 'disabled' : ''}>Sau</button>
      </div>
    </section>`;
  refreshIcons();
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
    const row = e.target.closest('[data-sp-code]');
    if (row) return spOpenDetail(row.dataset.spCode);
    if (e.target.closest('[data-sp-new]')) return spOpenForm(null);
    const pg = e.target.closest('[data-sp-page]');
    if (pg && !pg.disabled) { spState.filter.page = Number(pg.dataset.spPage); await spLoadList(); spRenderPage(); }
  });
  mount.addEventListener('keydown', (e) => {
    const row = e.target.closest('[data-sp-code]');
    if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); spOpenDetail(row.dataset.spCode); }
  });
}

async function spRefresh() {
  await spLoadMeta(true);
  await spLoadList();
  if (appState.view === 'spend') spRenderPage();
}

/* ---------- Dialog shell ---------- */
function spDialog(title, body, actions) {
  const dlg = $('#spDialog');
  $('#spDialogBody').onclick = null;
  $('#spDialogBody').innerHTML = `
    <div class="src-dialog-head"><div class="panel-title" id="spDialogTitle">${title}</div>
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
function spMsg(text, ok) {
  const el = $('#spDialogMsg');
  if (!el) return;
  el.textContent = text || '';
  el.className = 'src-result' + (text ? (ok ? ' ok' : ' err') : '');
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
  } catch (err) { alert(err.message); return; }
  spState.current = it;
  const dl = (label, value) => `<div class="sp-dl-item"><dt>${label}</dt><dd>${value}</dd></div>`;
  const link = (c) => c ? `<button class="btn btn-ghost btn-sm sp-link" type="button" data-sp-goto="${esc(c)}">${esc(c)}</button>` : '—';
  const editable = !['renewed', 'stopped'].includes(it.status);
  const assets = it.assets.length ? `<div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Serial</th><th>Mô tả</th><th>Host vCenter</th></tr></thead><tbody>
      ${it.assets.map(a => `<tr><td>${esc(a.serial)}</td><td class="wrap">${esc(a.description || '')}</td><td>${a.hostName ? `${esc(a.hostName)}<span class="muted-sub">${esc(a.vcenterKey || '')}</span>` : '—'}</td></tr>`).join('')}
    </tbody></table></div>` : '<p class="muted-sub">Chưa gắn thiết bị.</p>';
  const filesHtml = `<ul class="sp-files">${it.attachments.map(f => `<li>
      <a href="/api/spend/files/${encodeURIComponent(f.id)}" download>${esc(f.name)}</a>
      <span class="muted-sub">${(f.sizeBytes / 1024).toFixed(0)} KB · ${esc(f.uploadedBy)} · ${esc(spTime(f.uploadedAt))}</span>
      ${spCan('editor') ? `<button class="btn btn-ghost btn-sm btn-danger" type="button" data-sp-detach="${esc(f.id)}">Xoá</button>` : ''}</li>`).join('') || '<li class="muted-sub">Chưa có file.</li>'}</ul>
    ${spCan('editor') ? '<label class="field"><span>Thêm file (PDF, .docx, .xlsx)</span><input type="file" id="spAttach" accept=".pdf,.docx,.xlsx"></label>' : ''}`;
  const body = `
    <div class="sp-detail-head">${spPill(it.tone, it.displayLabel)} ${it.status === 'active' ? spDaysCell(it) : ''}</div>
    <dl class="sp-dl">
      ${dl('Loại chi phí', esc(it.categoryName || '—'))}
      ${dl('Nhà cung cấp', esc(it.vendorName || '—'))}
      ${dl('Hiệu lực', `${spDate(it.startDate)} – ${spDate(it.endDate)}`)}
      ${dl('Chu kỳ / NTC', `${esc(it.billingCycleLabel)} · NTC ${esc(it.fiscalYear)}`)}
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
  spDialog(`${esc(it.code)} — ${esc(it.title)}`, body, actions);
  const body$ = $('#spDialogBody');
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
  spDialog(it ? `Sửa ${esc(it.code)}` : 'Thêm khoản Opex', body,
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
  spDialog(`Gia hạn ${esc(it.code)}`, body, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
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
  spDialog(`Ngừng ${esc(it.code)}`, `<p class="panel-sub">Khoản bị ngừng sẽ không còn được nhắc hạn. Người có quyền Duyệt có thể kích hoạt lại.</p>
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

/* =========================================================
   Cài đặt → Chi phí & nhắc hạn
   ========================================================= */
async function initSpendSettings(user) {
  const panel = $('#spendSettingsPanel');
  if (!panel) return;
  const role = (user && user.spendRole) || 'none';
  panel.classList.toggle('hidden', role === 'none');
  if (role === 'none') return;
  try { await spLoadMeta(true); } catch (err) { panel.classList.add('hidden'); return; }
  spRenderSettings();
}

async function spRenderSettings() {
  const mount = $('#spendSettingsMount');
  const m = spState.meta, s = m.settings, admin = spCan('admin'), dis = admin ? '' : 'disabled';
  const months = Array.from({ length: 12 }, (_, i) => spOpt(i + 1, `Tháng ${i + 1}`, s.fiscalStartMonth)).join('');
  const hours = Array.from({ length: 24 }, (_, i) => spOpt(i, `${String(i).padStart(2, '0')}:00`, s.reminderHour)).join('');
  const cats = m.categories.map(c => `<tr data-cat="${esc(c.id)}">
      <td><code>${esc(c.code)}</code></td>
      <td><input type="text" data-k="name" value="${esc(c.name)}" maxlength="128" ${dis} aria-label="Tên loại"></td>
      <td><select data-k="grp" ${dis} aria-label="Nhóm">${Object.entries(SP_GROUPS).map(([k, t]) => spOpt(k, t, c.grp)).join('')}</select></td>
      <td><input type="number" data-k="renewalPct" min="0" max="100" step="0.01" value="${esc(c.renewalPct ?? '')}" ${dis} placeholder="—" aria-label="% gia hạn"></td>
      <td><input type="checkbox" data-k="active" ${c.active ? 'checked' : ''} ${dis} aria-label="Đang dùng"></td>
      <td>${admin ? '<button class="btn btn-ghost btn-sm" type="button" data-cat-save>Lưu</button>' : ''}</td></tr>`).join('');
  const ccs = m.costCenters.map(c => `<li><b>${esc(c.code)}</b> — ${esc(c.name)}${c.active ? '' : ' <span class="pill na">Không dùng</span>'}</li>`).join('') || '<li class="muted-sub">Chưa có cost center.</li>';
  mount.innerHTML = `
    <div class="sp-settings-grid">
      <div>
        <h4 class="sp-h">Năm tài chính &amp; mốc nhắc</h4>
        <div class="settings-form">
          <label class="field"><span>Tháng bắt đầu năm tài chính</span><select id="spSetMonth" ${dis}>${months}</select></label>
          <label class="field"><span>Mốc nhắc (ngày trước hạn)</span><input type="text" id="spSetDays" value="${esc(s.reminderDays.join(', '))}" ${dis} placeholder="90, 60, 30"></label>
          <label class="field"><span>Giờ gửi nhắc hằng ngày (giờ VN)</span><select id="spSetHour" ${dis}>${hours}</select></label>
          <label class="field"><span>Địa chỉ ứng dụng (link trong tin nhắc)</span><input type="text" id="spSetUrl" value="${esc(s.appBaseUrl)}" ${dis} placeholder="https://ico.congty.vn"></label>
          <label class="field"><span>Đơn vị đề xuất mặc định (tờ trình)</span><input type="text" id="spSetDept" maxlength="128" value="${esc(s.proposalDepartment || '')}" ${dis} placeholder="Phòng Hạ tầng IT"></label>
          <label class="field"><span>Chờ xoá version hợp đồng cũ (ngày)</span><input type="number" id="spSetPurge" min="1" max="365" value="${esc(s.purgeDays)}" ${dis}></label>
        </div>
        <p class="panel-sub">Năm tài chính đặt tên theo năm bắt đầu (tháng 4: 04/2026–03/2027 = NTC 2026). Đổi tháng bắt đầu không làm thay đổi năm tài chính của các khoản đã lưu.</p>
        ${admin ? '<div class="sim-actions"><button class="btn btn-primary btn-sm" type="button" id="spSetSave">Lưu cài đặt</button><span class="criteria-status" id="spSetStatus"></span></div>' : ''}
        <h4 class="sp-h">Cost center</h4>
        <ul class="sp-cc">${ccs}</ul>
        ${spCan('editor') ? `<div class="sp-inline"><input type="text" id="spCcCode" maxlength="32" placeholder="Mã" aria-label="Mã cost center"><input type="text" id="spCcName" maxlength="128" placeholder="Tên bộ phận" aria-label="Tên cost center"><button class="btn btn-secondary btn-sm" type="button" id="spCcAdd">Thêm</button></div>` : ''}
      </div>
      <div>
        <h4 class="sp-h">Loại chi phí</h4>
        <p class="panel-sub">% gia hạn dùng để tự tính Opex dự kiến từ Capex (giai đoạn sau). Loại đã dùng không xoá được, chỉ bỏ chọn “Dùng”.</p>
        <div class="table-scroll table-scroll-flat"><table class="data-table sp-cat-table"><thead><tr><th>Mã</th><th>Tên</th><th>Nhóm</th><th>% gia hạn</th><th>Dùng</th><th></th></tr></thead><tbody>${cats}</tbody></table></div>
        ${admin ? `<div class="sp-inline"><input type="text" id="spCatCode" maxlength="32" placeholder="MÃ_MỚI" aria-label="Mã loại"><input type="text" id="spCatName" maxlength="128" placeholder="Tên loại" aria-label="Tên loại mới"><button class="btn btn-secondary btn-sm" type="button" id="spCatAdd">Thêm loại</button></div>` : ''}
      </div>
    </div>
    ${admin ? '<div id="spAdminMount"><p class="muted-sub">Đang tải cấu hình kênh nhắc…</p></div>' : ''}
    <p class="criteria-status" id="spSettingsMsg"></p>`;
  const msg = (t, ok) => { const el = $('#spSettingsMsg'); el.textContent = t || ''; el.className = 'criteria-status' + (t ? (ok ? ' ok' : ' error') : ''); };
  spOnce('#spSetSave', async () => {
    const days = $('#spSetDays').value.split(/[,;\s]+/).filter(Boolean).map(Number);
    try {
      await apiRequest('/spend/settings', { method: 'PUT', body: JSON.stringify({ fiscalStartMonth: Number($('#spSetMonth').value),
        reminderDays: days, reminderHour: Number($('#spSetHour').value), appBaseUrl: $('#spSetUrl').value.trim(),
        proposalDepartment: $('#spSetDept').value.trim(), purgeDays: Number($('#spSetPurge').value) }) });
      await spLoadMeta(true); spRenderSettings(); msg('Đã lưu cài đặt.', true);
    } catch (err) { msg(err.message); }
  });
  spOnce('#spCcAdd', async () => {
    try { await apiRequest('/spend/cost-centers', { method: 'POST', body: JSON.stringify({ code: $('#spCcCode').value, name: $('#spCcName').value }) }); await spLoadMeta(true); spRenderSettings(); }
    catch (err) { msg(err.message); }
  });
  spOnce('#spCatAdd', async () => {
    try { await apiRequest('/spend/categories', { method: 'POST', body: JSON.stringify({ code: $('#spCatCode').value, name: $('#spCatName').value, grp: 'other' }) }); await spLoadMeta(true); spRenderSettings(); }
    catch (err) { msg(err.message); }
  });
  mount.querySelectorAll('[data-cat-save]').forEach(b => b.onclick = async () => {
    const tr = b.closest('tr'), get = (k) => tr.querySelector(`[data-k="${k}"]`);
    try {
      await apiRequest(`/spend/categories/${encodeURIComponent(tr.dataset.cat)}`, { method: 'PUT', body: JSON.stringify({
        name: get('name').value, grp: get('grp').value, renewalPct: get('renewalPct').value === '' ? null : Number(get('renewalPct').value), active: get('active').checked }) });
      await spLoadMeta(true); msg('Đã lưu loại chi phí.', true);
    } catch (err) { msg(err.message); }
  });
  if (admin) spRenderAdmin();
}

/* ---------- Admin: SMTP, channels, reminder log, roles ---------- */
async function spRenderAdmin() {
  const mount = $('#spAdminMount');
  let smtp, channels, log, users;
  try {
    [smtp, channels, log, users] = await Promise.all([apiRequest('/spend/admin/smtp'), apiRequest('/spend/admin/channels'),
      apiRequest('/spend/admin/reminders?limit=50'), apiRequest('/spend/admin/users')]);
  } catch (err) { mount.innerHTML = `<p class="criteria-status error">${esc(err.message)}</p>`; return; }
  const chRows = channels.map(c => `<tr>
      <td><b>${esc(c.name)}</b><span class="muted-sub">${c.kind === 'discord' ? 'Discord' : 'Email'}</span></td>
      <td class="wrap">${c.kind === 'discord' ? (c.webhookReadable === false ? '<span class="tone-crit">Webhook không giải mã được — nhập lại</span>' : 'Webhook đã lưu (ẩn)')
        : `${esc(c.recipients || '—')}${c.includeOwner ? '<span class="muted-sub">+ email người phụ trách</span>' : ''}`}</td>
      <td>${c.lastTestAt ? `<span class="${c.lastTestOk ? 'tone-ok' : 'tone-crit'}">${c.lastTestOk ? 'OK' : 'Lỗi'}</span><span class="muted-sub">${esc(spTime(c.lastTestAt))}</span>` : '—'}</td>
      <td><label class="switch" title="Bật / tắt kênh"><input type="checkbox" role="switch" data-ch-toggle="${esc(c.id)}" ${c.enabled ? 'checked' : ''} aria-label="Bật kênh ${esc(c.name)}"><span class="slider"></span></label></td>
      <td class="src-actions"><button class="btn btn-secondary btn-sm" type="button" data-ch-test="${esc(c.id)}">Gửi thử</button>
        <button class="btn btn-secondary btn-sm" type="button" data-ch-edit="${esc(c.id)}">Sửa</button>
        <button class="btn btn-secondary btn-sm btn-danger" type="button" data-ch-del="${esc(c.id)}">Xoá</button></td></tr>`).join('');
  const st = log.state || {};
  const STATUS = { sent: ['ok', 'Đã gửi'], failed: ['crit', 'Lỗi'], skipped: ['na', 'Bỏ qua (đã có mốc nhỏ hơn)'], unknown: ['warn', 'Không rõ'], pending: ['na', 'Đang gửi'] };
  const logRows = log.items.map(r => `<tr><td>${esc(spTime(r.createdAt))}</td><td><b>${esc(r.code)}</b><span class="muted-sub">${esc(r.title)}</span></td>
      <td>${esc(r.threshold)} ngày<span class="muted-sub">hết hạn ${spDate(r.endDate)}</span></td><td>${esc(r.channel)}</td>
      <td>${spPill(...(STATUS[r.status] || ['na', r.status]))}${r.error ? `<span class="muted-sub bk-wrap">${esc(r.error)}</span>` : ''}</td>
      <td>${['failed', 'unknown'].includes(r.status) ? `<button class="btn btn-ghost btn-sm" type="button" data-log-retry="${esc(r.id)}">Gửi lại</button>` : ''}</td></tr>`).join('');
  const free = log.storage && log.storage.freeBytes !== null ? `${(log.storage.freeBytes / 1073741824).toFixed(1)} GB trống` : 'không đọc được';
  const userRows = users.items.map(u => {
    const locked = u.isAdmin || u.roleSource === 'entra';
    return `<tr><td><b>${esc(u.displayName || u.username)}</b><span class="muted-sub">${esc(u.username)}</span></td><td>${u.authSource === 'entra' ? 'Entra ID' : 'Cục bộ'}</td>
      <td>${u.isAdmin ? spPill('ok', 'Quản trị — toàn quyền') : `<select data-user-role="${esc(u.id)}" ${locked ? 'disabled' : ''} aria-label="Vai trò ${esc(u.username)}">${users.roles.map(r => spOpt(r.value, r.label, u.spendRole)).join('')}</select>`}
      ${u.roleSource === 'entra' ? '<span class="muted-sub">Từ app role Entra</span>' : ''}</td></tr>`;
  }).join('');
  mount.innerHTML = `
    <div class="sp-settings-grid">
      <div>
        <h4 class="sp-h">Máy chủ SMTP <span class="badge-admin">Admin</span></h4>
        ${smtp.secretsKeyConfigured ? '' : '<div class="insight-box src-warning"><strong>Chưa đặt SECRETS_KEY.</strong> Mật khẩu SMTP / webhook đang mã hoá bằng khoá suy ra từ JWT_SECRET.</div>'}
        <div class="settings-form">
          <label class="field"><span>Máy chủ</span><input type="text" id="spSmtpHost" value="${esc(smtp.host)}" placeholder="smtp.congty.vn"></label>
          <label class="field"><span>Cổng</span><input type="number" id="spSmtpPort" min="1" max="65535" value="${esc(smtp.port)}"></label>
          <label class="field"><span>Bảo mật</span><select id="spSmtpSec">${spOpt('starttls', 'STARTTLS', smtp.security)}${spOpt('ssl', 'SSL/TLS', smtp.security)}${spOpt('none', 'Không (relay nội bộ)', smtp.security)}</select></label>
          <label class="field"><span>Người gửi (From)</span><input type="email" id="spSmtpFrom" value="${esc(smtp.fromAddr)}" placeholder="ico@congty.vn"></label>
          <label class="field"><span>Tài khoản</span><input type="text" id="spSmtpUser" value="${esc(smtp.username)}" autocomplete="off"></label>
          <label class="field"><span>Mật khẩu</span><input type="password" id="spSmtpPw" autocomplete="new-password" placeholder="${smtp.hasPassword ? (smtp.passwordReadable === false ? 'Không giải mã được — nhập lại' : 'Đã lưu — để trống để giữ') : ''}"></label>
        </div>
        <div class="sim-actions"><button class="btn btn-primary btn-sm" type="button" id="spSmtpSave">Lưu SMTP</button></div>
        <h4 class="sp-h">Vai trò người dùng</h4>
        <p class="panel-sub">Xem: đọc dữ liệu. Biên tập: tạo / sửa / gia hạn / ngừng. Duyệt: thêm xác nhận khoản dự kiến, kích hoạt lại. Tài khoản Entra lấy vai trò từ app role ICO.Spend.Viewer / Editor / Approver.</p>
        <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Người dùng</th><th>Nguồn</th><th>Vai trò Chi phí</th></tr></thead><tbody>${userRows}</tbody></table></div>
      </div>
      <div>
        <h4 class="sp-h">Kênh nhắc hạn <span class="badge-admin">Admin</span></h4>
        <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Kênh</th><th>Gửi tới</th><th>Thử gần nhất</th><th>Bật</th><th></th></tr></thead>
          <tbody>${chRows || '<tr><td colspan="5" class="empty-note">Chưa có kênh — thêm email hoặc Discord để bắt đầu nhắc hạn.</td></tr>'}</tbody></table></div>
        <div class="sim-actions"><button class="btn btn-secondary btn-sm" type="button" data-ch-add="email"><i data-lucide="plus"></i>Email</button>
          <button class="btn btn-secondary btn-sm" type="button" data-ch-add="discord"><i data-lucide="plus"></i>Discord</button></div>
        <h4 class="sp-h">Nhật ký nhắc hạn</h4>
        <p class="panel-sub">Lần chạy gần nhất: ${st.at ? esc(spTime(st.at)) : 'chưa chạy'}
          ${st.at ? ` · gửi ${esc(st.sent)} · lỗi ${esc(st.failed)} · bỏ qua ${esc(st.skipped)}` : ''} · volume file: ${esc(free)}</p>
        <div class="sim-actions"><button class="btn btn-secondary btn-sm" type="button" id="spRunNow">Chạy nhắc hạn ngay</button></div>
        <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Thời gian</th><th>Khoản</th><th>Mốc</th><th>Kênh</th><th>Kết quả</th><th></th></tr></thead>
          <tbody>${logRows || '<tr><td colspan="6" class="empty-note">Chưa có lượt nhắc nào.</td></tr>'}</tbody></table></div>
      </div>
    </div>
    <div id="ppAdminMount"></div>
    <div id="payDefaultsMount"></div>`;
  if (typeof payRenderDefaults === 'function') payRenderDefaults($('#payDefaultsMount'));
  if (typeof ppRenderAdmin === 'function') ppRenderAdmin($('#ppAdminMount'));
  refreshIcons();
  const msg = (t, ok) => { const el = $('#spSettingsMsg'); el.textContent = t || ''; el.className = 'criteria-status' + (t ? (ok ? ' ok' : ' error') : ''); };
  spOnce('#spSmtpSave', async () => {
    try {
      await apiRequest('/spend/admin/smtp', { method: 'PUT', body: JSON.stringify({ host: $('#spSmtpHost').value, port: Number($('#spSmtpPort').value),
        security: $('#spSmtpSec').value, fromAddr: $('#spSmtpFrom').value, username: $('#spSmtpUser').value, password: $('#spSmtpPw').value }) });
      msg('Đã lưu cấu hình SMTP.', true); spRenderAdmin();
    } catch (err) { msg(err.message); }
  });
  spOnce('#spRunNow', async () => {
    try { const r = await apiRequest('/spend/admin/reminders/run', { method: 'POST' }); msg(`Đã chạy: gửi ${r.sent}, lỗi ${r.failed}, bỏ qua ${r.skipped}.`, !r.failed); spRenderAdmin(); }
    catch (err) { msg(err.message); }
  });
  mount.onclick = async (e) => {
    const t = (sel) => e.target.closest(sel);
    let b;
    try {
      if ((b = t('[data-ch-add]'))) return spOpenChannel(b.dataset.chAdd, null);
      if ((b = t('[data-ch-edit]'))) return spOpenChannel(null, channels.find(c => String(c.id) === b.dataset.chEdit));
      if ((b = t('[data-ch-test]'))) {
        b.disabled = true;
        const r = await apiRequest(`/spend/admin/channels/${encodeURIComponent(b.dataset.chTest)}/test`, { method: 'POST' });
        msg(r.message, r.ok); return spRenderAdmin();
      }
      if ((b = t('[data-ch-del]')) && await uiConfirm('Xoá kênh nhắc này? Nhật ký gửi của kênh cũng bị xoá.')) {
        await apiRequest(`/spend/admin/channels/${encodeURIComponent(b.dataset.chDel)}`, { method: 'DELETE' }); return spRenderAdmin();
      }
      if ((b = t('[data-log-retry]'))) {
        await apiRequest(`/spend/admin/reminders/${encodeURIComponent(b.dataset.logRetry)}/retry`, { method: 'POST' });
        msg('Đã đánh dấu gửi lại — sẽ gửi ở lần chạy kế tiếp (hoặc bấm “Chạy nhắc hạn ngay”).', true); return spRenderAdmin();
      }
    } catch (err) { msg(err.message); }
  };
  mount.onchange = async (e) => {
    const tg = e.target.closest('[data-ch-toggle]'), ur = e.target.closest('[data-user-role]');
    try {
      if (tg) {
        const c = channels.find(x => String(x.id) === tg.dataset.chToggle);
        await apiRequest(`/spend/admin/channels/${encodeURIComponent(c.id)}`, { method: 'PUT', body: JSON.stringify({ name: c.name, enabled: tg.checked, recipients: c.recipients, includeOwner: c.includeOwner }) });
        msg(tg.checked ? 'Đã bật kênh.' : 'Đã tắt kênh.', true);
      }
      if (ur) {
        await apiRequest(`/spend/admin/users/${encodeURIComponent(ur.dataset.userRole)}/role`, { method: 'PUT', body: JSON.stringify({ role: ur.value }) });
        msg('Đã đổi vai trò — có hiệu lực ở lần tải trang kế tiếp của người dùng đó.', true);
      }
    } catch (err) { msg(err.message); spRenderAdmin(); }
  };
}

function spOpenChannel(kind, c) {
  kind = c ? c.kind : kind;
  const v = c || { name: '', enabled: true, recipients: '', includeOwner: false };
  const body = kind === 'discord'
    ? `<div class="src-grid"><label class="field"><span>Tên kênh</span><input id="spChName" maxlength="128" value="${esc(v.name)}" placeholder="Discord #ha-tang"></label>
        <label class="field"><span>Webhook URL</span><input type="password" id="spChHook" autocomplete="off" placeholder="${c ? 'Đã lưu — để trống để giữ' : 'https://discord.com/api/webhooks/…'}"></label></div>
       <p class="panel-sub">Discord → Cài đặt kênh → Tích hợp → Webhook → Sao chép URL. URL là bí mật: lưu mã hoá, không hiển thị lại.</p>`
    : `<div class="src-grid"><label class="field"><span>Tên kênh</span><input id="spChName" maxlength="128" value="${esc(v.name)}" placeholder="Email nhóm hạ tầng"></label>
        <label class="field"><span>Người nhận (cách nhau dấu phẩy)</span><input id="spChTo" value="${esc(v.recipients)}" placeholder="infra@congty.vn, finance@congty.vn"></label></div>
       <label class="finding-filter-item"><input type="checkbox" id="spChOwner" ${v.includeOwner ? 'checked' : ''}><span>Gửi thêm cho email người phụ trách của từng khoản (chỉ các khoản của họ)</span></label>`;
  spDialog(`${c ? 'Sửa' : 'Thêm'} kênh ${kind === 'discord' ? 'Discord' : 'email'}`, body,
    `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button><button class="btn btn-primary btn-sm" type="button" id="spChSave">Lưu</button>`);
  spOnce('#spChSave', async () => {
    const p = { kind, name: $('#spChName').value, enabled: v.enabled };
    if (kind === 'discord') { const h = $('#spChHook').value.trim(); if (h) p.webhookUrl = h; }
    else { p.recipients = $('#spChTo').value; p.includeOwner = $('#spChOwner').checked; }
    try {
      await apiRequest(c ? `/spend/admin/channels/${encodeURIComponent(c.id)}` : '/spend/admin/channels', { method: c ? 'PUT' : 'POST', body: JSON.stringify(p) });
      $('#spDialog').close(); spRenderAdmin();
    } catch (err) { spMsg(err.message); }
  });
}
