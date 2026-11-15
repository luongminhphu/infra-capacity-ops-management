/* =========================================================
   Chi phí & Hợp đồng IT — Tổng quan chi phí (kế hoạch ngân sách).
   Every number comes from POST /api/spend/reports/budget, the same builder
   as the PDF and the Excel (backend/app/spend/report.py), so the three agree.
   ========================================================= */

const srState = { filters: null, report: null, saved: [], error: null, bound: false };
const SR_OPEX_ST = [['planned', 'Dự kiến'], ['active', 'Đang hiệu lực'], ['renewed', 'Đã gia hạn'], ['stopped', 'Ngừng']];
const SR_CAPEX_ST = [['draft', 'Nháp'], ['approved', 'Đã duyệt'], ['in_progress', 'Đang triển khai'], ['closed', 'Đã đóng']];

function srDefaultFilters() {
  const cur = spState.meta.currentFiscalYear;
  return { fiscalYears: [cur, cur + 1], kind: 'all', categoryIds: [], vendorIds: [], costCenterIds: [],
    opexStatuses: ['planned', 'active', 'renewed'], capexStatuses: ['approved', 'in_progress', 'closed'],
    expiryFrom: null, expiryTo: null, amountMin: null, amountMax: null };
}

const srShort = (v) => {
  if (v === null || v === undefined) return '—';
  if (v === 0) return '0';
  return Math.abs(v) >= 1e9 ? `${(v / 1e9).toLocaleString('vi-VN', { maximumFractionDigits: 2 })} tỷ`
    : `${(v / 1e6).toLocaleString('vi-VN', { maximumFractionDigits: 1 })} tr`;
};

async function srLoad() {
  try {
    srState.report = await apiRequest('/spend/reports/budget', { method: 'POST', body: JSON.stringify(srState.filters) });
    srState.error = null;
  } catch (err) { srState.error = err.message; }
}

async function srLoadSaved() {
  try { srState.saved = await apiRequest('/spend/reports/filters'); } catch (err) { srState.saved = []; }
}

async function renderSpendOverview() {
  if (!srState.filters) srState.filters = srDefaultFilters();
  await Promise.all([srLoad(), srLoadSaved()]);
  srRender();
  srBind();
}

/* ---------- Filters ---------- */
function srMulti(name, options, selected, label) {
  return `<label class="field"><span>${label}</span><select multiple size="4" data-sr="${name}">${options.map(([v, t]) =>
    `<option value="${esc(v)}"${selected.map(String).includes(String(v)) ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select></label>`;
}
function srChecks(name, options, selected) {
  return options.map(([v, t]) => `<label class="finding-filter-item"><input type="checkbox" data-sr-check="${name}" value="${esc(v)}" ${selected.includes(v) ? 'checked' : ''}><span>${esc(t)}</span></label>`).join('');
}

function srFiltersHtml() {
  const m = spState.meta, f = srState.filters;
  const years = Array.from(new Set([...m.fiscalYears, m.currentFiscalYear + 2])).sort();
  return `<section class="panel sr-filters">
    <div class="panel-head panel-head-tight">
      <div><div class="panel-title">Bộ lọc</div><p class="panel-sub">Giữ Ctrl / ⌘ để chọn nhiều. Khoảng ngày hết hạn chỉ áp dụng cho Opex (Capex bị loại khi có khoảng ngày).</p></div>
      <div class="panel-toolbar">
        <select data-sr-saved aria-label="Bộ lọc đã lưu"><option value="">Bộ lọc đã lưu…</option>${srState.saved.map(s => `<option value="${esc(s.id)}">${esc(s.name)}${s.shared ? ' (dùng chung)' : ''}${s.mine ? '' : ` — ${esc(s.owner)}`}</option>`).join('')}</select>
        <button class="btn btn-ghost btn-sm" type="button" data-sr-del-saved>Xoá bộ lọc đã chọn</button>
      </div>
    </div>
    <div class="sr-grid">
      ${srMulti('fiscalYears', years.map(y => [y, `NTC ${y}`]), f.fiscalYears, 'Năm tài chính')}
      <label class="field"><span>Phạm vi</span><select data-sr="kind">${spOpt('all', 'Opex + Capex', f.kind)}${spOpt('opex', 'Chỉ Opex', f.kind)}${spOpt('capex', 'Chỉ Capex', f.kind)}</select></label>
      ${srMulti('categoryIds', m.categories.map(c => [c.id, c.name]), f.categoryIds, 'Loại chi phí')}
      ${srMulti('vendorIds', m.vendors.map(v => [v.id, v.name]), f.vendorIds, 'Nhà cung cấp')}
      ${srMulti('costCenterIds', m.costCenters.map(c => [c.id, `${c.code} — ${c.name}`]), f.costCenterIds, 'Cost center')}
      <div class="field"><span>Trạng thái Opex</span>${srChecks('opexStatuses', SR_OPEX_ST, f.opexStatuses)}</div>
      <div class="field"><span>Trạng thái Capex</span>${srChecks('capexStatuses', SR_CAPEX_ST, f.capexStatuses)}</div>
      <div class="field"><span>Hết hạn (Opex)</span>
        <input type="date" data-sr="expiryFrom" value="${esc(f.expiryFrom || '')}" aria-label="Hết hạn từ ngày">
        <input type="date" data-sr="expiryTo" value="${esc(f.expiryTo || '')}" aria-label="Hết hạn đến ngày"></div>
      <div class="field"><span>Số tiền sau VAT (đ)</span>
        <input type="number" min="0" step="1" data-sr="amountMin" value="${esc(f.amountMin ?? '')}" placeholder="Từ" aria-label="Số tiền từ">
        <input type="number" min="0" step="1" data-sr="amountMax" value="${esc(f.amountMax ?? '')}" placeholder="Đến" aria-label="Số tiền đến"></div>
    </div>
    <div class="sim-actions">
      <button class="btn btn-primary btn-sm" type="button" data-sr-apply>Áp dụng</button>
      <button class="btn btn-ghost btn-sm" type="button" data-sr-reset>Đặt lại</button>
      <span class="src-spacer"></span>
      <input type="text" data-sr-name maxlength="100" placeholder="Tên bộ lọc" aria-label="Tên bộ lọc để lưu">
      <label class="finding-filter-item"><input type="checkbox" data-sr-shared><span>Dùng chung</span></label>
      <button class="btn btn-secondary btn-sm" type="button" data-sr-save>Lưu bộ lọc</button>
      <button class="btn btn-secondary btn-sm" type="button" data-spend-export="xlsx"><i data-lucide="sheet"></i>Excel</button>
      <button class="btn btn-primary btn-sm" type="button" data-spend-export="pdf"><i data-lucide="file-text"></i>PDF</button>
    </div>
    <p class="criteria-status" id="srMsg" role="status"></p>
  </section>`;
}

function srReadFilters() {
  const f = srDefaultFilters();
  const mount = $('#spendMount');
  mount.querySelectorAll('select[multiple][data-sr]').forEach(s => {
    f[s.dataset.sr] = Array.from(s.selectedOptions).map(o => Number(o.value));
  });
  if (!f.fiscalYears.length) f.fiscalYears = srDefaultFilters().fiscalYears;
  f.kind = mount.querySelector('[data-sr="kind"]').value;
  ['opexStatuses', 'capexStatuses'].forEach(k => {
    f[k] = Array.from(mount.querySelectorAll(`[data-sr-check="${k}"]:checked`)).map(i => i.value);
    if (!f[k].length) f[k] = srDefaultFilters()[k];
  });
  ['expiryFrom', 'expiryTo'].forEach(k => { f[k] = mount.querySelector(`[data-sr="${k}"]`).value || null; });
  ['amountMin', 'amountMax'].forEach(k => { const v = mount.querySelector(`[data-sr="${k}"]`).value; f[k] = v === '' ? null : Number(v); });
  return f;
}

/* ---------- Report body ---------- */
function srBar(pct) {
  return `<span class="sr-bar" aria-hidden="true"><span style="width:${Math.max(0, Math.min(100, Number(pct) || 0))}%"></span></span> ${esc((pct ?? 0).toLocaleString('vi-VN'))}%`;
}

function srBodyHtml() {
  const r = srState.report;
  if (srState.error) return `<p class="criteria-status error">${esc(srState.error)}</p>`;
  if (!r) return uiNote('Đang tải…');
  const c = r.compare, t = r.totals;
  const soon = r.expiringByQuarter.reduce((s, g) => s + g.total, 0), soonN = r.expiringByQuarter.reduce((s, g) => s + g.count, 0);
  const delta = c.deltaPct === null ? '—' : `${c.deltaPct > 0 ? '+' : ''}${c.deltaPct.toLocaleString('vi-VN')}%`;
  const maxYear = Math.max(1, ...r.byYear.map(y => y.total));
  const decisions = r.decisions.map((d, i) => `<li>${spPill(d.cls, ['Ngay', 'Cao', 'Kế hoạch'][['crit', 'bad', 'warn'].indexOf(d.cls)] || '')}
      <b>${esc(d.title)}</b> (${spMoney(d.amount)}) → ${esc(d.decision)} <span class="muted-sub sr-inline">· ${esc(d.timeframe)}</span></li>`).join('');
  const years = r.byYear.map(y => `<div class="sr-year">
      <div class="sr-year-label">NTC ${esc(y.fiscalYear)}</div>
      <div class="sr-year-bars">
        <span class="sr-seg sr-opex" style="width:${(y.opex / maxYear * 100).toFixed(2)}%" title="Opex ${esc(spMoney(y.opex))}"></span><span class="sr-seg sr-capex" style="width:${(y.capex / maxYear * 100).toFixed(2)}%" title="Capex ${esc(spMoney(y.capex))}"></span>
      </div>
      <div class="sr-year-val num">${spMoney(y.total)}<span class="muted-sub">Opex ${srShort(y.opex)} · Capex ${srShort(y.capex)}</span></div></div>`).join('');
  const cats = r.byCategory.map(x => `<tr><td>${esc(x.category)}</td><td class="num">${esc(x.count)}</td><td class="num">${spMoney(x.opex)}</td><td class="num">${spMoney(x.capex)}</td><td class="num"><b>${spMoney(x.total)}</b></td><td>${srBar(x.sharePct)}</td></tr>`).join('');
  const quarters = r.expiringByQuarter.map(g => `<tr class="sr-group"><td colspan="5"><b>${esc(g.label)}</b> · ${esc(g.count)} khoản · ${spMoney(g.total)}</td></tr>` +
    g.items.map(i => `<tr data-sp-goto-row="${esc(i.code)}" class="sp-row" tabindex="0"><td><b>${esc(i.code)}</b></td><td class="wrap">${esc(i.title)}<span class="muted-sub">${esc(i.vendor)}</span></td>
      <td>${spDate(i.endDate)}</td><td>${spPill(i.daysLeft < 0 ? 'crit' : i.daysLeft <= 30 ? 'bad' : i.daysLeft <= 90 ? 'warn' : 'ok', i.daysLeft < 0 ? `Quá ${-i.daysLeft} ngày` : `${i.daysLeft} ngày`)}</td>
      <td class="num">${spMoney(i.amountGross)}</td></tr>`).join('')).join('');
  const fromCapex = r.fromCapex.items.map(i => `<tr data-sp-goto-row="${esc(i.code)}" class="sp-row" tabindex="0"><td><b>${esc(i.code)}</b></td><td class="wrap">${esc(i.title)}</td><td>NTC ${esc(i.fiscalYear)}</td><td>${esc(i.capexCode || '—')}</td><td>${spPill(i.status === 'planned' ? 'na' : 'ok', i.statusLabel)}</td><td class="num">${spMoney(i.amountGross)}</td></tr>`).join('');
  const vendors = r.topVendors.map((v, i) => `<tr><td>${i + 1}</td><td>${esc(v.vendor)}</td><td class="num">${esc(v.count)}</td><td class="num">${spMoney(v.opex)}</td><td class="num">${spMoney(v.capex)}</td><td class="num"><b>${spMoney(v.total)}</b></td><td>${srBar(v.sharePct)}</td></tr>`).join('');
  const empty = (n) => `<tr><td colspan="${n}" class="empty-note">Không có dòng nào theo bộ lọc.</td></tr>`;
  return `
    <section class="kpi-grid">
      <div class="kpi-card"><div class="label">NTC ${esc(c.current.fiscalYear)}</div><div class="value">${srShort(c.current.total)}</div><div class="sub">Opex ${srShort(c.current.opex)} · Capex ${srShort(c.current.capex)}</div></div>
      <div class="kpi-card"><div class="label">NTC ${esc(c.next.fiscalYear)}</div><div class="value">${srShort(c.next.total)}</div><div class="sub">So với năm nay: <b class="${(c.deltaPct || 0) > 10 ? 'tone-warn' : ''}">${esc(delta)}</b>${c.inFilter ? '' : ' (năm không nằm trong bộ lọc)'}</div></div>
      <div class="kpi-card"><div class="label">Opex hết hạn trong 12 tháng</div><div class="value ${soon ? 'tone-bad' : 'tone-ok'}">${srShort(soon)}</div><div class="sub">${soonN} khoản cần quyết định gia hạn / ngừng</div></div>
      <div class="kpi-card"><div class="label">Opex phát sinh từ Capex</div><div class="value">${srShort(r.fromCapex.total)}</div><div class="sub">${r.fromCapex.count} khoản gia hạn trong các năm đã lọc</div></div>
    </section>
    <section class="panel">
      <div class="panel-title">${esc(r.headline)}</div>
      <p class="panel-sub">${esc(r.scopeLabel)} · ${t.count} dòng · tổng ${spMoney(t.total)} (trước VAT ${spMoney(t.net)})</p>
      ${decisions ? `<ol class="sr-decisions">${decisions}</ol>` : '<p class="muted-sub">Không có khoản nào cần quyết định trong phạm vi lọc.</p>'}
    </section>
    <div class="sr-two">
      <section class="panel"><div class="panel-title">Theo năm tài chính</div>
        <div class="sr-legend"><span><i class="sr-dot sr-opex"></i>Opex</span><span><i class="sr-dot sr-capex"></i>Capex</span></div>
        ${years || '<p class="empty-note">Không có dữ liệu.</p>'}</section>
      <section class="panel"><div class="panel-title">Nhà cung cấp lớn nhất</div>
        <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>#</th><th>Nhà cung cấp</th><th class="num">Dòng</th><th class="num">Opex</th><th class="num">Capex</th><th class="num">Tổng</th><th>Tỷ trọng</th></tr></thead><tbody>${vendors || empty(7)}</tbody></table></div></section>
    </div>
    <section class="panel"><div class="panel-title">Theo loại chi phí</div>
      <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Loại</th><th class="num">Dòng</th><th class="num">Opex</th><th class="num">Capex</th><th class="num">Tổng</th><th>Tỷ trọng</th></tr></thead><tbody>${cats || empty(6)}</tbody></table></div></section>
    <section class="panel"><div class="panel-title">Opex hết hạn theo quý tài chính</div>
      <p class="panel-sub">Khoản dự kiến / đang hiệu lực thuộc các năm tài chính đã lọc, hết hạn từ 90 ngày trước đến 12 tháng tới (hoặc theo khoảng ngày đã lọc). Bấm một dòng để mở khoản.</p>
      <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Mã</th><th>Dịch vụ</th><th>Hết hạn</th><th>Còn</th><th class="num">Sau VAT</th></tr></thead><tbody>${quarters || empty(5)}</tbody></table></div></section>
    <section class="panel"><div class="panel-title">Opex phát sinh từ Capex</div>
      <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Mã Opex</th><th>Gia hạn</th><th>NTC</th><th>Dự án</th><th>Trạng thái</th><th class="num">Sau VAT</th></tr></thead><tbody>${fromCapex || empty(6)}</tbody></table></div></section>
    ${r.rowsTotal > r.rows.length ? `<p class="muted-sub">Màn hình hiển thị số tổng hợp; danh sách đủ ${r.rowsTotal} dòng có trong file Excel.</p>` : ''}`;
}

function srRender() {
  $('#spendMount').innerHTML = srFiltersHtml() + `<div id="srBody">${srBodyHtml()}</div>`;
  refreshIcons();
}

function srMsg(text, ok) {
  const el = $('#srMsg');
  if (!el) return;
  el.classList.add('criteria-status');
  uiSetStatus(el, text, text ? (ok ? 'ok' : 'error') : '');
}

/* ---------- Export (also used by the report centre) ---------- */
async function spendExport(kind, btn) {
  const filters = (appState.view === 'spend' && spState.sub === 'overview' && $('#spendMount [data-sr="kind"]')) ? srReadFilters() : {};
  if (btn) btn.disabled = true;
  try {
    await uiDownload(`/api/spend/reports/budget.${kind}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(kind === 'pdf' && typeof reportAiOn === 'function' && reportAiOn() ? { ...filters, ai: true } : filters) },
      `ke-hoach-ngan-sach.${kind}`);
  } catch (err) {
    if ($('#srMsg')) srMsg(err.message); else uiAlert(err.message);
  } finally { if (btn) btn.disabled = false; }
}
// [data-spend-export] clicks are delegated in routes.js (the Reports page works before this file loads).

function srBind() {
  if (srState.bound) return;
  srState.bound = true;
  const mount = $('#spendMount');
  mount.addEventListener('click', async (e) => {
    if (!(appState.view === 'spend' && spState.sub === 'overview')) return;
    const t = (s) => e.target.closest(s);
    if (t('[data-sr-apply]')) {
      srState.filters = srReadFilters();
      await srLoad(); $('#srBody').innerHTML = srBodyHtml(); srMsg('');
    } else if (t('[data-sr-reset]')) {
      srState.filters = srDefaultFilters(); await srLoad(); srRender();
    } else if (t('[data-sr-save]')) {
      const name = mount.querySelector('[data-sr-name]').value.trim();
      if (!name) { srMsg('Nhập tên bộ lọc.'); return; }
      try {
        await apiRequest('/spend/reports/filters', { method: 'POST', body: JSON.stringify({ name, filters: srReadFilters(),
          shared: mount.querySelector('[data-sr-shared]').checked }) });
        await srLoadSaved(); srState.filters = srReadFilters(); srRender(); srMsg(`Đã lưu bộ lọc “${name}”.`, true);
      } catch (err) { srMsg(err.message); }
    } else if (t('[data-sr-del-saved]')) {
      const id = mount.querySelector('[data-sr-saved]').value;
      if (!id) { srMsg('Chọn một bộ lọc đã lưu.'); return; }
      try { await apiRequest(`/spend/reports/filters/${encodeURIComponent(id)}`, { method: 'DELETE' }); await srLoadSaved(); srRender(); srMsg('Đã xoá bộ lọc.', true); }
      catch (err) { srMsg(err.message); }
    } else {
      const row = t('[data-sp-goto-row]');
      if (row) spOpenDetail(row.dataset.spGotoRow);
    }
  });
  mount.addEventListener('change', async (e) => {
    const sel = e.target.closest('[data-sr-saved]');
    if (!sel || !sel.value) return;
    const s = srState.saved.find(x => String(x.id) === sel.value);
    if (!s) return;
    srState.filters = { ...srDefaultFilters(), ...s.filters };
    await srLoad(); srRender();
    const again = $('[data-sr-saved]'); if (again) again.value = String(s.id);
    srMsg(`Đang dùng bộ lọc “${s.name}”.`, true);
  });
  mount.addEventListener('keydown', (e) => {
    const row = e.target.closest('[data-sp-goto-row]');
    if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); spOpenDetail(row.dataset.spGotoRow); }
  });
}
