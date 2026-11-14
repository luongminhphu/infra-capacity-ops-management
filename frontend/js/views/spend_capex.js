/* =========================================================
   Chi phí & Hợp đồng IT — trang Capex (dự án đầu tư).
   The renewal dates, fiscal years and amounts of generated Opex are computed by
   the backend (backend/app/spend/capex.py); the form asks /capex/preview instead
   of re-implementing the rules here. Shares spState / helpers with spend.js.
   ========================================================= */

const cpxState = { list: null, filter: { fy: '', status: '', q: '' }, bound: false, error: null };
const CPX_STATUS = { draft: ['na', 'Nháp'], approved: ['ok', 'Đã duyệt'], in_progress: ['ok', 'Đang triển khai'], closed: ['na', 'Đã đóng'] };
const CPX_OPEX_STATE = { managed: ['na', 'Theo Capex'], confirmed: ['ok', 'Đã xác nhận'], edited: ['warn', 'Đã sửa tay'] };
// Allowed moves and the role they need — mirrors PROJECT_TRANSITIONS in capex.py (the API re-checks).
const CPX_MOVES = {
  draft: [['approved', 'Duyệt dự án', 'approver']],
  approved: [['in_progress', 'Bắt đầu triển khai', 'editor'], ['draft', 'Trả về nháp', 'approver']],
  in_progress: [['closed', 'Đóng dự án', 'editor']],
  closed: [['in_progress', 'Mở lại', 'approver']],
};

async function cpxLoad() {
  const f = cpxState.filter, qs = new URLSearchParams();
  Object.entries(f).forEach(([k, v]) => { if (v) qs.set(k, v); });
  try { cpxState.list = await apiRequest(`/spend/capex?${qs}`); cpxState.error = null; }
  catch (err) { cpxState.error = err.message; cpxState.list = []; }
}

async function renderCapexPage() {
  await cpxLoad();
  cpxRender();
  cpxBind();
}

function cpxRender() {
  const m = spState.meta, f = cpxState.filter, list = cpxState.list || [];
  const total = list.reduce((s, p) => s + p.amountGross, 0), opex = list.reduce((s, p) => s + p.opexGross, 0);
  const drafts = list.filter(p => p.status === 'draft').length;
  const rows = list.map(p => `<tr data-cpx-code="${esc(p.code)}" tabindex="0" class="sp-row">
      <td><b>${esc(p.code)}</b><span class="muted-sub">NTC ${esc(p.fiscalYear)}</span></td>
      <td class="wrap">${esc(p.name)}<span class="muted-sub">${esc(p.ownerName || '')}</span></td>
      <td>${spDate(p.goLiveDate)}</td>
      <td class="num">${esc(p.itemCount)}</td>
      <td class="num">${spMoney(p.amountGross)}</td>
      <td class="num">${p.opexCount ? `${spMoney(p.opexGross)}<span class="muted-sub">${esc(p.opexCount)} khoản</span>` : '—'}</td>
      <td>${spPill(...(CPX_STATUS[p.status] || ['na', p.statusLabel]))}</td></tr>`).join('');
  $('#spendMount').innerHTML = `
    <section class="kpi-grid">
      <div class="kpi-card"><div class="label">Tổng giá trị Capex (sau VAT)</div><div class="value">${spMoney(total)}</div><div class="sub">${list.length} dự án theo bộ lọc</div></div>
      <div class="kpi-card"><div class="label">Opex gia hạn phát sinh</div><div class="value">${spMoney(opex)}</div><div class="sub">Tổng các kỳ gia hạn đã sinh từ các dự án này</div></div>
      <div class="kpi-card"><div class="label">Dự án chờ duyệt</div><div class="value ${drafts ? 'tone-warn' : ''}">${drafts}</div><div class="sub">Nháp chưa sinh Opex — cần quyền Duyệt</div></div>
      <div class="kpi-card"><div class="label">Năm tài chính hiện tại</div><div class="value">${esc(m.currentFiscalYear)}</div><div class="sub">Bắt đầu tháng ${esc(m.settings.fiscalStartMonth)}</div></div>
    </section>
    <section class="table-card">
      <div class="panel-head">
        <div><div class="panel-title">Dự án Capex</div><p class="panel-sub">Mỗi hạng mục có thời hạn bảo hành / license sẽ sinh một khoản Opex gia hạn “Dự kiến” khi dự án được duyệt.</p></div>
        <div class="panel-toolbar">${spCan('editor') ? '<button class="btn btn-primary btn-sm" type="button" data-cpx-new><i data-lucide="plus"></i>Thêm dự án</button>' : ''}</div>
      </div>
      <div class="sp-filters">
        <label class="field"><span>Năm tài chính</span><select data-cpx-filter="fy">${spOpt('', 'Tất cả', f.fy)}${m.fiscalYears.map(y => spOpt(y, `NTC ${y}`, f.fy)).join('')}</select></label>
        <label class="field"><span>Trạng thái</span><select data-cpx-filter="status">${spOpt('', 'Tất cả', f.status)}${Object.entries(CPX_STATUS).map(([k, [, t]]) => spOpt(k, t, f.status)).join('')}</select></label>
        <label class="field sp-search"><span>Tìm</span><input type="text" data-cpx-filter="q" value="${esc(f.q)}" placeholder="Mã hoặc tên dự án"></label>
      </div>
      ${cpxState.error ? `<p class="criteria-status error">${esc(cpxState.error)}</p>` : ''}
      <div class="table-scroll"><table class="data-table sp-table">
        <thead><tr><th>Mã</th><th>Dự án</th><th>Go-live</th><th class="num">Hạng mục</th><th class="num">Giá trị sau VAT</th><th class="num">Opex gia hạn</th><th>Trạng thái</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="7" class="empty-note">${spCan('editor') ? 'Chưa có dự án — bấm “Thêm dự án”.' : 'Chưa có dự án.'}</td></tr>`}</tbody>
      </table></div>
    </section>`;
  refreshIcons();
}

let cpxSearchTimer = null;
function cpxBind() {
  if (cpxState.bound) return;
  cpxState.bound = true;
  const mount = $('#spendMount');
  mount.addEventListener('change', async (e) => {
    const el = e.target.closest('[data-cpx-filter]');
    if (!el || el.dataset.cpxFilter === 'q') return;
    cpxState.filter[el.dataset.cpxFilter] = el.value;
    await cpxLoad(); cpxRender();
  });
  mount.addEventListener('input', (e) => {
    const el = e.target.closest('[data-cpx-filter="q"]');
    if (!el) return;
    clearTimeout(cpxSearchTimer);
    cpxSearchTimer = setTimeout(async () => {
      cpxState.filter.q = el.value.trim(); await cpxLoad(); cpxRender();
      const box = $('[data-cpx-filter="q"]'); if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
    }, 350);
  });
  mount.addEventListener('click', (e) => {
    const row = e.target.closest('[data-cpx-code]');
    if (row) return cpxOpenDetail(row.dataset.cpxCode);
    if (e.target.closest('[data-cpx-new]')) return cpxOpenForm(null);
  });
  mount.addEventListener('keydown', (e) => {
    const row = e.target.closest('[data-cpx-code]');
    if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); cpxOpenDetail(row.dataset.cpxCode); }
  });
}

async function cpxRefresh() {
  await spLoadMeta(true);
  if (appState.view === 'spend' && spState.sub === 'capex') { await cpxLoad(); cpxRender(); }
  else if (appState.view === 'spend') spRefresh();
}

function cpxPlanCell(it) {
  if (!it.plan) return `<span class="muted-sub bk-wrap">${esc(it.noPlanReason || '—')}</span>`;
  return `${spDate(it.plan.startDate)} · NTC ${esc(it.plan.fiscalYear)}<span class="muted-sub">${spMoney(it.plan.amountGross)}${it.effectivePct !== null ? ` (${esc(it.effectivePct)}% giá trị)` : ' (nhập tay)'}</span>`;
}

/* ---------- Detail ---------- */
async function cpxOpenDetail(code) {
  let p;
  try { await spLoadMeta(); p = await apiRequest(`/spend/capex/${encodeURIComponent(code)}`); }
  catch (err) { alert(err.message); return; }
  const canConfirm = spCan('approver');
  const itemRows = p.items.map(it => {
    const o = it.opex;
    const st = o ? CPX_OPEX_STATE[o.state] : null;
    const opexCell = o ? `<button class="btn btn-ghost btn-sm sp-link" type="button" data-sp-goto="${esc(o.code)}">${esc(o.code)}</button>
        ${spPill(st[0], st[1])}${o.differsFromPlan ? ` ${spPill('bad', 'Lệch kế hoạch')}` : ''}<span class="muted-sub">${spMoney(o.amountGross)} · NTC ${esc(o.fiscalYear)}</span>`
      : (p.generating && it.plan ? '<span class="muted-sub">Chưa sinh</span>' : '—');
    const box = canConfirm && o && o.state === 'managed'
      ? `<input type="checkbox" data-cpx-pick="${esc(o.code)}" aria-label="Chọn ${esc(o.code)} để xác nhận">` : '';
    return `<tr><td>${box}</td>
      <td class="wrap">${esc(it.description)}<span class="muted-sub">${esc(it.categoryName || '')} · ${esc(it.vendorName || 'chưa có NCC')}</span></td>
      <td class="num">${spMoney(it.amountGross)}</td>
      <td class="num">${it.coverageYears ? `${esc(it.coverageYears)} năm` : '—'}<span class="muted-sub">từ ${spDate(it.startDate || p.goLiveDate)}</span></td>
      <td>${cpxPlanCell(it)}</td><td>${opexCell}</td></tr>`;
  }).join('');
  const orphans = p.orphanOpex.length ? `<h4 class="sp-h">Opex của dự án không còn gắn hạng mục</h4>
    <p class="panel-sub">Hạng mục đã bị xoá sau khi khoản Opex được xác nhận hoặc sửa tay — xem lại và ngừng nếu không còn cần.</p>
    <ul class="sp-files">${p.orphanOpex.map(o => `<li><button class="btn btn-ghost btn-sm sp-link" type="button" data-sp-goto="${esc(o.code)}">${esc(o.code)}</button> ${esc(o.title)} <span class="muted-sub">NTC ${esc(o.fiscalYear)} · ${spMoney(o.amountGross)}</span></li>`).join('')}</ul>` : '';
  const dl = (label, value) => `<div class="sp-dl-item"><dt>${label}</dt><dd>${value}</dd></div>`;
  const body = `
    <div class="sp-detail-head">${spPill(...(CPX_STATUS[p.status] || ['na', p.statusLabel]))}</div>
    ${p.generating ? '' : '<div class="insight-box"><strong>Dự án nháp.</strong> Cột “Gia hạn dự kiến” là bản xem trước; Opex chỉ được sinh khi người có quyền Duyệt duyệt dự án.</div>'}
    <dl class="sp-dl">
      ${dl('Năm tài chính', `NTC ${esc(p.fiscalYear)}`)}
      ${dl('Go-live', spDate(p.goLiveDate))}
      ${dl('Giá trị trước VAT', spMoney(p.amountNet))}
      ${dl('Giá trị sau VAT', `<b>${spMoney(p.amountGross)}</b>`)}
      ${dl('Cost center', esc(p.costCenterName || '—'))}
      ${dl('Người phụ trách', esc(p.ownerName || '—'))}
      ${dl('Cập nhật', `${esc(p.updatedBy || p.createdBy)} · ${esc(spTime(p.updatedAt || p.createdAt))}`)}
    </dl>
    ${p.description ? `<p class="sp-desc">${esc(p.description)}</p>` : ''}
    <h4 class="sp-h">Hạng mục và Opex gia hạn</h4>
    <div class="table-scroll table-scroll-flat"><table class="data-table">
      <thead><tr><th></th><th>Hạng mục</th><th class="num">Giá trị sau VAT</th><th class="num">Thời hạn đi kèm</th><th>Gia hạn dự kiến</th><th>Opex</th></tr></thead>
      <tbody>${itemRows || '<tr><td colspan="6" class="empty-note">Chưa có hạng mục.</td></tr>'}</tbody></table></div>
    <p class="panel-sub">“Theo Capex”: khoản tự cập nhật khi sửa dự án. Đã xác nhận hoặc sửa tay thì không bị ghi đè — nếu kế hoạch đổi sẽ hiện “Lệch kế hoạch”.</p>
    ${orphans}`;
  const btn = (attr, label, cls = 'btn-secondary') => `<button class="btn ${cls} btn-sm" type="button" ${attr}>${label}</button>`;
  const moves = (CPX_MOVES[p.status] || []).filter(([, , role]) => spCan(role))
    .map(([to, label]) => btn(`data-cpx-move="${esc(to)}"`, label, to === 'approved' ? 'btn-primary' : 'btn-secondary')).join('');
  const actions = [
    spCan('admin') ? btn('id="cpxDelete"', 'Xoá', 'btn-secondary btn-danger') : '',
    '<span class="src-spacer"></span>',
    canConfirm && p.items.some(i => i.opex && i.opex.state === 'managed') ? btn('id="cpxConfirm"', 'Xác nhận Opex đã chọn') : '',
    spCan('editor') ? btn('id="cpxProposal"', 'Soạn tờ trình') : '',
    spCan('editor') && p.status !== 'closed' ? btn('id="cpxEdit"', 'Sửa') : '',
    moves,
  ].join('');
  spDialog(`${esc(p.code)} — ${esc(p.name)}`, body, actions);
  $('#spDialogBody').onclick = async (e) => {
    const go = e.target.closest('[data-sp-goto]');
    if (go) return spOpenDetail(go.dataset.spGoto);
    const mv = e.target.closest('[data-cpx-move]');
    if (mv) {
      if (mv.dataset.cpxMove === 'draft' && !await uiConfirm('Trả về nháp sẽ xoá các Opex dự kiến chưa xác nhận của dự án. Tiếp tục?')) return;
      mv.disabled = true;
      try {
        const r = await apiRequest(`/spend/capex/${encodeURIComponent(p.code)}/status`, { method: 'POST', body: JSON.stringify({ status: mv.dataset.cpxMove }) });
        await cpxRefresh(); await cpxOpenDetail(p.code); cpxReconcileMsg(r.reconcile);
      } catch (err) { spMsg(err.message); mv.disabled = false; }
    }
  };
  spOnce('#cpxEdit', async () => cpxOpenForm(p));
  spOnce('#cpxProposal', async () => { if (typeof ppOpenCreate === 'function') ppOpenCreate({ capexCode: p.code, subject: p.name }); });
  spOnce('#cpxConfirm', async () => {
    const codes = $$('[data-cpx-pick]:checked').map(b => b.dataset.cpxPick);
    if (!codes.length) { spMsg('Chọn ít nhất một khoản Opex “Theo Capex”.'); return; }
    try {
      await apiRequest(`/spend/capex/${encodeURIComponent(p.code)}/opex/confirm`, { method: 'POST', body: JSON.stringify({ codes }) });
      await cpxRefresh(); await cpxOpenDetail(p.code); spMsg(`Đã xác nhận ${codes.length} khoản.`, true);
    } catch (err) { spMsg(err.message); }
  });
  spOnce('#cpxDelete', async () => {
    if (!await uiConfirm(`Xoá vĩnh viễn ${p.code}? Các Opex dự kiến chưa xác nhận của dự án cũng bị xoá.`)) return;
    try { await apiRequest(`/spend/capex/${encodeURIComponent(p.code)}`, { method: 'DELETE' }); $('#spDialog').close(); cpxRefresh(); }
    catch (err) { spMsg(err.message); }
  });
}

function cpxReconcileMsg(r) {
  if (!r) return;
  const parts = [];
  if (r.created.length) parts.push(`sinh ${r.created.length} Opex`);
  if (r.updated.length) parts.push(`cập nhật ${r.updated.length}`);
  if (r.removed.length) parts.push(`xoá ${r.removed.length}`);
  if (r.kept.length) parts.push(`giữ nguyên ${r.kept.length} khoản đã xác nhận / sửa tay`);
  if (parts.length) spMsg(`Opex dự kiến: ${parts.join(', ')}.`, true);
}

/* ---------- Create / edit ---------- */
function cpxItemRow(m, it = {}) {
  const cats = m.categories.filter(c => c.active || c.id === it.categoryId);
  return `<div class="cpx-item" data-item-id="${esc(it.id || '')}">
    <div class="cpx-item-grid">
      <label class="field"><span>Loại *</span><select data-i="categoryId">${spOpt('', '— Chọn —', it.categoryId || '')}${cats.map(c => spOpt(c.id, `${c.name}${c.renewalPct !== null ? ` (${c.renewalPct}%)` : ''}`, it.categoryId)).join('')}</select></label>
      <label class="field cpx-span2"><span>Mô tả hạng mục *</span><input type="text" data-i="description" maxlength="255" value="${esc(it.description || '')}" placeholder="Vd: 4 máy chủ Dell R760"></label>
      <label class="field"><span>Nhà cung cấp</span><input type="text" data-i="vendorName" list="spVendorList" maxlength="255" value="${esc(it.vendorName || '')}"></label>
      <label class="field"><span>Trước VAT (đ) *</span><input type="number" data-i="amountNet" min="0" step="1" value="${esc(it.amountNet ?? '')}"></label>
      <label class="field"><span>VAT (%)</span><input type="number" data-i="vatPct" min="0" max="100" step="0.01" value="${esc(it.vatPct ?? 10)}"></label>
      <label class="field"><span>Bảo hành / license (năm)</span><input type="number" data-i="coverageYears" min="0" max="10" step="1" value="${esc(it.coverageYears ?? 0)}"></label>
      <label class="field"><span>Bắt đầu bảo hành</span><input type="date" data-i="startDate" value="${esc(it.startDate || '')}" title="Để trống = ngày go-live của dự án"></label>
      <label class="field"><span>% gia hạn</span><input type="number" data-i="renewalPct" min="0" max="100" step="0.01" value="${esc(it.renewalPct ?? '')}" placeholder="Theo loại"></label>
      <label class="field"><span>Hoặc số tiền gia hạn (đ)</span><input type="number" data-i="renewalAmount" min="0" step="1" value="${esc(it.renewalAmount ?? '')}" placeholder="Tự tính"></label>
    </div>
    <div class="cpx-item-foot"><span class="muted-sub bk-wrap" data-i-preview></span>
      <button class="btn btn-ghost btn-sm btn-danger" type="button" data-cpx-item-del>Bỏ hạng mục</button></div>
  </div>`;
}

function cpxCollect(p) {
  const form = $('#cpxForm');
  const v = (n) => form.querySelector(`[name="${n}"]`).value.trim();
  const out = { name: v('name'), fiscalYear: v('fiscalYear') ? Number(v('fiscalYear')) : null, goLiveDate: v('goLiveDate') || null,
    costCenterId: v('costCenterId') ? Number(v('costCenterId')) : null, ownerName: v('ownerName'), description: v('description') };
  out.items = $$('#cpxItems .cpx-item').map(row => {
    const g = (k) => row.querySelector(`[data-i="${k}"]`).value.trim();
    const num = (k) => (g(k) === '' ? null : Number(g(k)));
    const item = { categoryId: num('categoryId'), description: g('description'), vendorName: g('vendorName'), amountNet: num('amountNet'),
      vatPct: num('vatPct'), coverageYears: num('coverageYears') || 0, startDate: g('startDate') || null,
      renewalPct: num('renewalPct'), renewalAmount: num('renewalAmount') };
    if (row.dataset.itemId) item.id = Number(row.dataset.itemId);
    return item;
  });
  if (p) out.rowVersion = p.rowVersion;
  return out;
}

async function cpxOpenForm(p) {
  const m = spState.meta, v = p || { items: [] };
  const body = `<form id="cpxForm" class="sp-form" novalidate>
    <div class="src-grid">
      <label class="field sp-span2"><span>Tên dự án *</span><input name="name" maxlength="255" value="${esc(v.name || '')}" placeholder="Vd: Mở rộng cụm PROD 2026"></label>
      <label class="field"><span>Ngày go-live</span><input type="date" name="goLiveDate" value="${esc(v.goLiveDate || '')}"></label>
      <label class="field"><span>Năm tài chính</span><input type="number" name="fiscalYear" min="2000" max="2100" value="${esc(v.fiscalYear || '')}" placeholder="Tự tính từ go-live"></label>
      <label class="field"><span>Cost center</span><select name="costCenterId">${spOpt('', '—', v.costCenterId || '')}${m.costCenters.filter(c => c.active || c.id === v.costCenterId).map(c => spOpt(c.id, `${c.code} — ${c.name}`, v.costCenterId)).join('')}</select></label>
      <label class="field"><span>Người phụ trách</span><input name="ownerName" maxlength="128" value="${esc(v.ownerName || '')}"></label>
      <label class="field sp-span2"><span>Mô tả</span><textarea name="description" rows="2" maxlength="5000">${esc(v.description || '')}</textarea></label>
    </div>
    <h4 class="sp-h">Hạng mục</h4>
    <p class="panel-sub">Gia hạn bắt đầu ngày sau khi hết thời hạn đi kèm; số tiền = % gia hạn × giá trị trước VAT (hoặc số nhập tay). Bấm “Xem trước Opex” để kiểm tra trước khi lưu.</p>
    <div id="cpxItems">${(v.items.length ? v.items : [{}]).map(it => cpxItemRow(m, it)).join('')}</div>
    <div class="sim-actions"><button class="btn btn-ghost btn-sm" type="button" id="cpxItemAdd"><i data-lucide="plus"></i>Thêm hạng mục</button>
      <button class="btn btn-secondary btn-sm" type="button" id="cpxPreview">Xem trước Opex</button></div>
    <datalist id="spVendorList">${m.vendors.filter(x => x.active).map(x => `<option value="${esc(x.name)}"></option>`).join('')}</datalist>
  </form>`;
  spDialog(p ? `Sửa ${esc(p.code)}` : 'Thêm dự án Capex', body,
    `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
     <button class="btn btn-primary btn-sm" type="button" id="cpxSave">Lưu</button>`);
  $('#cpxItemAdd').onclick = () => { $('#cpxItems').insertAdjacentHTML('beforeend', cpxItemRow(m)); refreshIcons(); };
  $('#cpxItems').onclick = (e) => { const b = e.target.closest('[data-cpx-item-del]'); if (b) b.closest('.cpx-item').remove(); };
  spOnce('#cpxPreview', async () => {
    try {
      const r = await apiRequest('/spend/capex/preview', { method: 'POST', body: JSON.stringify(cpxCollect(null)) });
      const rows = $$('#cpxItems .cpx-item');
      r.items.forEach(x => {
        const el = rows[x.index] && rows[x.index].querySelector('[data-i-preview]');
        if (!el) return;
        el.textContent = x.plan
          ? `→ Opex gia hạn từ ${spDate(x.plan.startDate)} đến ${spDate(x.plan.endDate)}, NTC ${x.plan.fiscalYear}: ${spMoney(x.plan.amountNet)} trước VAT, ${spMoney(x.plan.amountGross)} sau VAT${x.effectivePct !== null ? ` (${x.effectivePct}%)` : ''}`
          : `→ Không sinh Opex: ${x.noPlanReason}`;
      });
      spMsg('');
    } catch (err) { spMsg(err.message); }
  });
  spOnce('#cpxSave', async () => {
    try {
      const saved = await apiRequest(p ? `/spend/capex/${encodeURIComponent(p.code)}` : '/spend/capex',
        { method: p ? 'PUT' : 'POST', body: JSON.stringify(cpxCollect(p)) });
      await cpxRefresh(); await cpxOpenDetail(saved.code); cpxReconcileMsg(saved.reconcile);
    } catch (err) { spMsg(err.message); }
  });
}
