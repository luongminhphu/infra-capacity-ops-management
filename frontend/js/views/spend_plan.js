/* =========================================================
   Ngân sách & chi phí → Kế hoạch năm sau (docs/design/spend-budget-plan.md).
   The backend (spend/budget_plan.py) suggests the lines, computes every amount, total,
   ceiling cut line and plan-vs-actual; this file renders and sends edits. Shares
   spState / spDialog / helpers with spend.js. Roles are re-checked by the API.
   ========================================================= */

const bpState = { list: null, planId: null, plan: null, error: null, bound: false, group: 'all' };
const BP_STATUS = { draft: ['na', 'Nháp'], submitted: ['warn', 'Chờ duyệt'], approved: ['ok', 'Đã duyệt'], superseded: ['na', 'Đã thay thế'] };
const BP_SOURCE = { renewal: 'Gia hạn', committed: 'Đã ghi nhận', capex: 'Dự án Capex', new: 'Mới đề xuất' };
const BP_PRIORITY = { must: 'Bắt buộc', should: 'Nên có', could: 'Tuỳ chọn' };

async function bpLoad() {
  try {
    bpState.list = await apiRequest('/spend/plans');
    const items = bpState.list.items;
    if (!bpState.planId || !items.some(p => p.id === bpState.planId)) {
      // Default: the newest plan of next year, else the newest plan.
      const next = items.find(p => p.fiscalYear === bpState.list.nextFiscalYear);
      bpState.planId = (next || items[0] || {}).id || null;
    }
    bpState.plan = bpState.planId ? await apiRequest(`/spend/plans/${bpState.planId}`) : null;
    bpState.error = null;
  } catch (err) { bpState.error = err.message; }
}

async function renderPlanPage() {
  await bpLoad();
  bpRender();
  bpBind();
}

const bpPct = (v) => (v === null || v === undefined) ? '—' : `${v > 0 ? '+' : ''}${uiNumber(v, 1)}%`;

function bpRender() {
  const mount = $('#spendMount');
  if (bpState.error) { mount.innerHTML = `<section class="panel">${uiNote(bpState.error, 'crit')}</section>`; return; }
  const list = bpState.list, p = bpState.plan;
  const nextY = list.nextFiscalYear;
  const hasOpenNext = list.items.some(x => x.fiscalYear === nextY && x.status !== 'superseded');   // approved: use “Sửa đổi”
  const newBtn = spCan('editor') && !hasOpenNext
    ? `<button class="btn btn-primary btn-sm" type="button" data-bp-create="${esc(nextY)}"><i data-lucide="plus"></i>Lập kế hoạch FY ${esc(nextY)}</button>` : '';
  const picker = list.items.length ? `<label class="field"><span>Kế hoạch</span><select data-bp-pick>${list.items.map(x =>
    spOpt(x.id, `FY ${x.fiscalYear} · v${x.version} · ${x.statusLabel}`, bpState.planId)).join('')}</select></label>` : '';
  if (!p) {
    mount.innerHTML = `<section class="panel">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Kế hoạch ngân sách năm sau</div>
        <p class="panel-sub">Hệ thống tự gợi ý từ dữ liệu đang có: Opex sẽ đến kỳ gia hạn (cộng % tăng giá), Opex đã ghi nhận cho năm sau, dự án Capex của năm sau. Bạn thêm đề xuất mới, đặt mức ưu tiên, trần ngân sách rồi gửi duyệt. Sau khi duyệt, kế hoạch là thước đo để theo dõi thực hiện trong năm.</p></div>
        <div class="panel-toolbar">${newBtn}</div></div>
      ${uiEmpty(spCan('editor') ? `Chưa có kế hoạch — bấm “Lập kế hoạch FY ${nextY}”.` : 'Chưa có kế hoạch nào.')}
    </section>`;
    refreshIcons();
    return;
  }
  const s = p.summary, can = p.can, cut = new Set(s.cutLineIds || []), act = p.actuals;
  const lines = p.lines.filter(l => bpState.group === 'all' || l.source === bpState.group);
  const editable = can.edit;
  const actions = [
    editable ? '<button class="btn btn-secondary btn-sm" type="button" data-bp-act="refresh" title="Cập nhật lại các dòng gợi ý theo dữ liệu Opex / Capex mới nhất; giữ nguyên dòng bạn đã sửa"><i data-lucide="refresh-cw"></i>Làm mới gợi ý</button>' : '',
    editable ? '<button class="btn btn-secondary btn-sm" type="button" data-bp-add><i data-lucide="plus"></i>Thêm đề xuất</button>' : '',
    can.submit ? '<button class="btn btn-primary btn-sm" type="button" data-bp-act="submit">Gửi duyệt</button>' : '',
    can.return ? '<button class="btn btn-secondary btn-sm" type="button" data-bp-act="return">Trả lại</button>' : '',
    can.approve ? '<button class="btn btn-primary btn-sm" type="button" data-bp-act="approve">Duyệt kế hoạch</button>' : '',
    can.apply ? '<button class="btn btn-secondary btn-sm" type="button" data-bp-act="apply" title="Tạo Opex (Dự kiến) / Capex (Nháp) cho các đề xuất mới chưa tạo">Tạo Opex / Capex từ đề xuất mới</button>' : '',
    can.revise ? '<button class="btn btn-secondary btn-sm" type="button" data-bp-act="revise">Sửa đổi (bản mới)</button>' : '',
    '<button class="btn btn-secondary btn-sm" type="button" data-bp-export><i data-lucide="sheet"></i>Excel</button>',
    editable ? '<button class="btn btn-ghost btn-sm btn-danger" type="button" data-bp-act="delete">Xoá bản nháp</button>' : '',
  ].join('');
  const maxQ = Math.max(1, ...s.byQuarter);
  const quarters = s.byQuarter.map((v, i) => `<div class="sr-year"><div class="sr-year-label">Q${i + 1}</div>
      <div class="sr-year-bars"><span class="sr-seg sr-opex" style="width:${(v * 100 / maxQ).toFixed(2)}%"></span></div>
      <div class="sr-year-val num">${spMoney(v)}</div></div>`).join('');
  const cats = s.byCategory.map(c => `<tr><td class="wrap">${esc(c.category)}</td><td class="num">${spMoney(c.baseline)}</td>
      <td class="num">${spMoney(c.plan)}</td><td class="num ${c.delta > 0 ? 'tone-warn' : ''}">${bpPct(c.deltaPct)}</td></tr>`).join('');
  const groups = [['all', 'Tất cả'], ...Object.entries(BP_SOURCE)].map(([k, t]) => {
    const n = k === 'all' ? p.lines.length : p.lines.filter(l => l.source === k).length;
    return `<button type="button" class="rpt-chip${bpState.group === k ? ' active' : ''}" data-bp-group="${esc(k)}">${esc(t)} (${n})</button>`;
  }).join('');
  const rows = lines.map(l => {
    const a = act && act.lines[l.id];
    const noVendor = l.source === 'new' && l.kind === 'opex' && !l.vendorId && !l.appliedCode && l.included;
    const flags = [l.stale ? spPill('warn', 'Nguồn đã thay đổi') : '', cut.has(l.id) ? spPill('crit', 'Vượt trần') : '',
      noVendor ? spPill('warn', 'Thiếu nhà cung cấp') : '',
      l.appliedCode ? spPill('ok', `Đã tạo ${l.appliedCode}`) : ''].join(' ');
    return `<tr class="sp-row${l.included ? '' : ' bp-out'}" data-bp-line="${esc(l.id)}" tabindex="0">
      <td>${editable ? `<input type="checkbox" data-bp-inc="${esc(l.id)}" ${l.included ? 'checked' : ''} aria-label="Đưa vào kế hoạch">` : (l.included ? '✓' : '—')}</td>
      <td><b>${esc(BP_SOURCE[l.source])}</b><span class="muted-sub">${esc(l.sourceCode || l.kind.toUpperCase())}</span></td>
      <td class="wrap">${esc(l.title)}<span class="muted-sub">${esc([l.categoryName, l.vendorName].filter(Boolean).join(' · '))}</span>${flags ? `<span class="muted-sub">${flags}</span>` : ''}</td>
      <td>${spDate(l.startDate)}${l.terms > 1 ? `<span class="muted-sub">${esc(l.terms)} kỳ</span>` : ''}</td>
      <td class="num">${l.source === 'new' ? '—' : spMoney(l.baseNet)}</td>
      <td class="num">${l.amountMode === 'manual' ? 'Nhập tay' : bpPct(l.effectiveUpliftPct)}</td>
      <td class="num"><b>${spMoney(l.amountGross)}</b><span class="muted-sub">trước VAT ${spMoney(l.amountNet)}</span></td>
      <td>${esc(BP_PRIORITY[l.priority])}</td>
      ${act ? `<td class="num">${spMoney(a ? a.actual : 0)}${a && a.refs.length ? `<span class="muted-sub">${esc(a.refs.join(', '))}</span>` : ''}</td>
               <td class="num ${a && a.variance > 0 ? 'tone-bad' : ''}">${a ? spMoney(a.variance) : '—'}</td>` : ''}
      <td class="wrap">${esc(l.note || '')}</td></tr>`;
  }).join('');
  const capTone = s.overCap ? 'tone-crit' : 'tone-ok';
  const unplanned = act && act.unplanned.length ? `<section class="table-card">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Phát sinh ngoài kế hoạch</div>
        <p class="panel-sub">Opex / Capex của FY ${esc(p.fiscalYear)} không thuộc dòng nào của kế hoạch — tổng ${spMoney(act.unplannedTotal)}.</p></div></div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Mã</th><th>Nội dung</th><th class="num">Sau VAT</th></tr></thead>
      <tbody>${act.unplanned.map(u => `<tr><td><b>${esc(u.code)}</b></td><td class="wrap">${esc(u.title)}</td><td class="num">${spMoney(u.amountGross)}</td></tr>`).join('')}</tbody></table></div></section>` : '';
  mount.innerHTML = `
    <section class="panel">
      <div class="panel-head">
        <div><div class="panel-title" role="heading" aria-level="2">${esc(p.name)} ${spPill(...(BP_STATUS[p.status] || ['na', p.statusLabel]))}</div>
          <p class="panel-sub">FY ${esc(p.fiscalYear)} (${spDate(p.fiscalRange[0])} – ${spDate(p.fiscalRange[1])}) · bản v${esc(p.version)} ·
            lập bởi ${esc(p.createdBy)}${p.approvedBy ? ` · duyệt bởi ${esc(p.approvedBy)} ${esc(spTimeSafe(p.approvedAt))}` : ''}</p></div>
        <div class="panel-toolbar">${picker}${newBtn}</div>
      </div>
      <div class="sp-filters">
        <label class="field"><span>% tăng giá gia hạn (mặc định)</span><input type="number" min="0" max="100" step="0.5" data-bp-set="upliftPct" value="${esc(p.upliftPct)}" ${editable ? '' : 'disabled'}></label>
        <label class="field"><span>Trần ngân sách (sau VAT, đồng)</span><input type="number" min="0" step="1000000" data-bp-set="budgetCap" value="${esc(p.budgetCap ?? '')}" placeholder="Không đặt" ${editable ? '' : 'disabled'}></label>
        <label class="field sp-search"><span>Ghi chú / giả định</span><input type="text" maxlength="5000" data-bp-set="notes" value="${esc(p.notes || '')}" placeholder="vd: tỷ giá 25.500, tăng giá license 8%" ${editable ? '' : 'disabled'}></label>
      </div>
      <div class="sim-actions">${actions}</div>
      <p class="criteria-status" id="bpMsg" role="status"></p>
    </section>
    <section class="kpi-strip">
      <div class="kpi-cell"><div class="kpi-l">Kế hoạch FY ${esc(p.fiscalYear)}</div><div class="kpi-v">${spMoney(s.total)}</div>
        <div class="kpi-s">${esc(s.count)} dòng · so với FY ${esc(s.baseline.fiscalYear)}: <b>${bpPct(s.deltaPct)}</b></div></div>
      <div class="kpi-cell"><div class="kpi-l">Thực tế FY ${esc(s.baseline.fiscalYear)}</div><div class="kpi-v">${spMoney(s.baseline.total)}</div>
        <div class="kpi-s">Opex ${spMoney(s.baseline.opex)} · Capex ${spMoney(s.baseline.capex)}</div></div>
      <div class="kpi-cell"><div class="kpi-l">Opex kế hoạch</div><div class="kpi-v">${spMoney(s.byKind.opex)}</div>
        <div class="kpi-s">Capex ${spMoney(s.byKind.capex)} · tăng giá gia hạn ${spMoney(s.renewalUplift)} (trước VAT)</div></div>
      ${act ? `<div class="kpi-cell"><div class="kpi-l">Đã thực hiện</div><div class="kpi-v"><span class="${act.variance > 0 ? 'tone-bad' : ''}">${spMoney(act.actualTotal)}</span></div>
        <div class="kpi-s">${act.usedPct === null ? '—' : uiNumber(act.usedPct, 1) + '% kế hoạch'} · ngoài kế hoạch ${spMoney(act.unplannedTotal)}</div></div>`
      : `<div class="kpi-cell"><div class="kpi-l">Trần ngân sách</div><div class="kpi-v"><span class="${s.cap === null ? '' : capTone}">${s.cap === null ? 'Không đặt' : spMoney(s.cap)}</span></div>
        <div class="kpi-s">${s.overCap ? `Vượt ${spMoney(s.overCap)} — ${esc(s.cutLineIds.length)} dòng dưới đường cắt (theo ưu tiên)` : 'Trong trần'}</div></div>`}
    </section>
    ${s.staleCount ? `<div class="insight-box src-warning"><strong>${esc(s.staleCount)} dòng có nguồn đã thay đổi</strong> (Opex đã gia hạn / ngừng, dự án bị xoá). Kiểm tra rồi bỏ khỏi kế hoạch hoặc xoá.</div>` : ''}
    <div class="sr-two">
      <section class="panel"><div class="panel-title" role="heading" aria-level="2">Theo quý tài chính</div>
        <p class="panel-sub">Theo ngày bắt đầu / thanh toán của từng dòng — dùng để lên dòng tiền. Dòng chưa có ngày tính vào Q1.</p>${quarters}</section>
      <section class="panel"><div class="panel-title" role="heading" aria-level="2">Theo loại chi phí</div>
        <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Loại</th><th class="num">FY ${esc(s.baseline.fiscalYear)}</th><th class="num">Kế hoạch</th><th class="num">Thay đổi</th></tr></thead>
        <tbody>${cats || uiEmptyRow(4, 'Chưa có dòng nào.')}</tbody></table></div></section>
    </div>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Các dòng kế hoạch</div>
        <p class="panel-sub">${editable ? 'Bấm một dòng để sửa % tăng, số tiền, ưu tiên, ghi chú. Bỏ chọn để loại khỏi kế hoạch (cần lý do).' : 'Bấm một dòng để xem chi tiết.'}
          Bỏ khỏi kế hoạch: ${spMoney(s.excludedTotal)} (${esc(s.excludedCount)} dòng).</p></div>
        <div class="panel-toolbar rpt-chips">${groups}</div></div>
      <div class="table-scroll"><table class="data-table sp-table">
        <thead><tr><th></th><th>Nguồn</th><th>Nội dung</th><th>Bắt đầu</th><th class="num">Hiện tại (trước VAT)</th><th class="num">% tăng</th>
          <th class="num">Kế hoạch (sau VAT)</th><th>Ưu tiên</th>${act ? '<th class="num">Thực hiện</th><th class="num">Chênh lệch</th>' : ''}<th>Ghi chú</th></tr></thead>
        <tbody>${rows || uiEmptyRow(act ? 11 : 9, 'Không có dòng nào.')}</tbody></table></div>
    </section>
    ${unplanned}`;
  refreshIcons();
}

function bpMsg(text, ok) { uiSetStatus('#bpMsg', text, ok ? 'ok' : 'error'); }

async function bpCall(path, opts, okText) {
  try {
    const r = await apiRequest(path, opts);
    if (r && r.id) { bpState.plan = r; bpState.planId = r.id; }
    bpState.list = await apiRequest('/spend/plans');
    bpRender();
    if (okText) bpMsg(typeof okText === 'function' ? okText(r) : okText, !(r && r.applied && r.applied.skipped.length));
    return r;
  } catch (err) { bpMsg(err.message); return null; }
}

function bpLineForm(l) {
  const m = spState.meta, isNew = !l || l.source === 'new';
  const v = l || { kind: 'opex', priority: 'should', vatPct: 10, included: true };
  const fixed = isNew ? '' : `<div class="insight-box">${esc(BP_SOURCE[l.source])} ${esc(l.sourceCode || '')}: hiện tại ${spMoney(l.baseNet)} trước VAT${l.terms > 1 ? ` (${esc(l.terms)} kỳ)` : ''}. Để trống “Số tiền kế hoạch” để tính theo % tăng.</div>`;
  return `${fixed}<div class="src-grid">
    ${isNew ? `<label class="field"><span>Loại</span><select id="bpKind">${spOpt('opex', 'Opex (chi phí vận hành)', v.kind)}${spOpt('capex', 'Capex (đầu tư)', v.kind)}</select></label>
      <label class="field"><span>Nội dung *</span><input id="bpTitle" maxlength="255" value="${esc(v.title || '')}" placeholder="vd: Dịch vụ SOC 24/7"></label>
      <label class="field"><span>Loại chi phí *</span><select id="bpCat">${spOpt('', '— Chọn —', '')}${m.categories.map(c => spOpt(c.id, c.name, v.categoryId)).join('')}</select></label>
      <label class="field"><span>Số tiền ước tính (trước VAT) *</span><input id="bpBase" type="number" min="0" step="1000" value="${esc(v.baseNet ?? '')}"></label>` : ''}
    <label class="field"><span>Nhà cung cấp</span><select id="bpVendor">${spOpt('', '— Chưa xác định —', '')}${m.vendors.map(x => spOpt(x.id, x.name, v.vendorId)).join('')}</select></label>
    <label class="field"><span>Cost center</span><select id="bpCc">${spOpt('', '—', '')}${m.costCenters.map(x => spOpt(x.id, `${x.code} — ${x.name}`, v.costCenterId)).join('')}</select></label>
    <label class="field"><span>Ngày bắt đầu / thanh toán</span><input id="bpStart" type="date" value="${esc(v.startDate || '')}" min="${esc(bpState.plan.fiscalRange[0])}" max="${esc(bpState.plan.fiscalRange[1])}"></label>
    <label class="field"><span>VAT (%)</span><input id="bpVat" type="number" min="0" max="100" step="0.5" value="${esc(v.vatPct)}"></label>
    <label class="field"><span>% tăng${isNew ? '' : ' (để trống = mặc định của kế hoạch)'}</span><input id="bpUplift" type="number" min="0" max="100" step="0.5" value="${esc(v.upliftPct ?? '')}"></label>
    <label class="field"><span>Số tiền kế hoạch (trước VAT)</span><input id="bpAmount" type="number" min="0" step="1000" value="${esc(v.amountMode === 'manual' ? v.amountNet : '')}" placeholder="Tự tính"></label>
    <label class="field"><span>Ưu tiên</span><select id="bpPrio">${Object.entries(BP_PRIORITY).map(([k, t]) => spOpt(k, t, v.priority)).join('')}</select></label>
    <label class="field"><span>Đưa vào kế hoạch</span><select id="bpInc">${spOpt('1', 'Có', v.included ? '1' : '0')}${spOpt('0', 'Không', v.included ? '1' : '0')}</select></label>
  </div>
  <label class="field"><span>Ghi chú / lý do${isNew ? ' (vì sao cần)' : ' (bắt buộc khi bỏ khỏi kế hoạch)'}</span><textarea id="bpNote" maxlength="1000" rows="3">${esc(v.note || '')}</textarea></label>`;
}

function bpFormData(isNew) {
  const g = (id) => { const el = $(id); return el ? el.value : undefined; };
  const d = { vendorId: g('#bpVendor') || null, costCenterId: g('#bpCc') || null, startDate: g('#bpStart') || null, vatPct: g('#bpVat'),
    upliftPct: g('#bpUplift') === '' ? null : g('#bpUplift'), amountNet: g('#bpAmount') === '' ? null : g('#bpAmount'),
    priority: g('#bpPrio'), included: g('#bpInc') === '1', note: g('#bpNote') };
  if (isNew) Object.assign(d, { kind: g('#bpKind'), title: g('#bpTitle'), categoryId: g('#bpCat'), baseNet: g('#bpBase') || 0 });
  return d;
}

function bpOpenLine(id) {
  const p = bpState.plan, l = p.lines.find(x => x.id === id);
  if (!l) return;
  if (!p.can.edit) {
    uiPanelDialog(esc(l.title), `<dl class="sp-dl">
      <div class="sp-dl-item"><dt>Nguồn</dt><dd>${esc(BP_SOURCE[l.source])} ${esc(l.sourceCode || '')}</dd></div>
      <div class="sp-dl-item"><dt>Kế hoạch</dt><dd>${spMoney(l.amountGross)} (trước VAT ${spMoney(l.amountNet)})</dd></div>
      <div class="sp-dl-item"><dt>Hiện tại</dt><dd>${spMoney(l.baseNet)}</dd></div>
      <div class="sp-dl-item"><dt>Ưu tiên</dt><dd>${esc(BP_PRIORITY[l.priority])}</dd></div>
      <div class="sp-dl-item"><dt>Ghi chú</dt><dd>${esc(l.note || '—')}</dd></div>
      <div class="sp-dl-item"><dt>Cập nhật</dt><dd>${esc(l.updatedBy || '—')} ${esc(spTimeSafe(l.updatedAt))}</dd></div></dl>`);
    return;
  }
  const isNew = l.source === 'new';
  uiPanelDialog(`Sửa dòng — ${esc(l.title)}`, bpLineForm(l), `${isNew || l.stale ? '<button class="btn btn-ghost btn-sm btn-danger" type="button" id="bpDel">Xoá dòng</button>' : ''}
    <span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
    <button class="btn btn-primary btn-sm" type="button" id="bpSave">Lưu</button>`);
  spOnce('#bpSave', async () => {
    try {
      const r = await apiRequest(`/spend/plans/${p.id}/lines/${l.id}`, { method: 'PUT', body: JSON.stringify(bpFormData(isNew)) });
      bpState.plan = r; $('#spDialog').close(); bpRender(); bpMsg('Đã lưu dòng.', true);
    } catch (err) { spMsg(err.message); }
  });
  spOnce('#bpDel', async () => {
    if (!(await uiConfirm(`Xoá dòng “${l.title}” khỏi kế hoạch?`))) return;
    try { bpState.plan = await apiRequest(`/spend/plans/${p.id}/lines/${l.id}`, { method: 'DELETE' }); $('#spDialog').close(); bpRender(); }
    catch (err) { spMsg(err.message); }
  });
}

function bpOpenNew() {
  uiPanelDialog('Thêm đề xuất mới', bpLineForm(null), `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
    <button class="btn btn-primary btn-sm" type="button" id="bpSave">Thêm</button>`);
  spOnce('#bpSave', async () => {
    try {
      bpState.plan = await apiRequest(`/spend/plans/${bpState.plan.id}/lines`, { method: 'POST', body: JSON.stringify(bpFormData(true)) });
      $('#spDialog').close(); bpRender(); bpMsg('Đã thêm đề xuất.', true);
    } catch (err) { spMsg(err.message); }
  });
}

function bpBind() {
  if (bpState.bound) return;
  bpState.bound = true;
  const mount = $('#spendMount');
  mount.addEventListener('click', async (e) => {
    if (spState.sub !== 'plan') return;
    const p = bpState.plan;
    const create = e.target.closest('[data-bp-create]');
    if (create) {
      const y = create.dataset.bpCreate;
      await bpCall('/spend/plans', { method: 'POST', body: JSON.stringify({ fiscalYear: Number(y) }) },
        (r) => `Đã lập kế hoạch FY ${y}: ${r.lines.length} dòng gợi ý từ Opex / Capex hiện có.`);
      return;
    }
    const grp = e.target.closest('[data-bp-group]');
    if (grp) { bpState.group = grp.dataset.bpGroup; bpRender(); return; }
    if (e.target.closest('[data-bp-add]')) { bpOpenNew(); return; }
    if (e.target.closest('[data-bp-export]')) {
      try { await uiDownload(`/api/spend/plans/${p.id}/export.xlsx`, {}, `ke-hoach-ngan-sach-${p.fiscalYear}.xlsx`); } catch (err) { bpMsg(err.message); }
      return;
    }
    const act = e.target.closest('[data-bp-act]');
    if (act) {
      const cmd = act.dataset.bpAct;
      if (cmd === 'delete') {
        if (!(await uiConfirm('Xoá bản nháp kế hoạch này?'))) return;
        try { await apiRequest(`/spend/plans/${p.id}`, { method: 'DELETE' }); bpState.planId = null; await renderPlanPage(); } catch (err) { bpMsg(err.message); }
        return;
      }
      const confirmText = { approve: 'Duyệt kế hoạch này? Bản đã duyệt trước đó của cùng năm (nếu có) sẽ được thay thế.',
        apply: 'Tạo Opex “Dự kiến” / Capex “Nháp” cho các đề xuất mới đang được đưa vào? Đề xuất Opex cần có nhà cung cấp.' }[cmd];
      if (confirmText && !(await uiConfirm(confirmText))) return;
      let body = { rowVersion: p.rowVersion };
      if (cmd === 'return') {
        const reason = await uiPrompt('Lý do trả lại (người lập sẽ thấy trong nhật ký):');
        if (reason === null) return;
        body.reason = reason;
      }
      if (cmd === 'refresh') { await bpCall(`/spend/plans/${p.id}/refresh`, { method: 'POST' }, (r) => {
        const s = r.refreshed; return `Đã làm mới: thêm ${s.added}, cập nhật ${s.updated}, bỏ ${s.removed}, cần kiểm tra ${s.stale}.`; }); return; }
      const ok = { submit: 'Đã gửi duyệt.', return: 'Đã trả lại để sửa.', approve: 'Đã duyệt kế hoạch.', revise: 'Đã tạo bản sửa đổi (nháp).',
        apply: (r) => `Đã tạo ${r.applied.created.length} khoản${r.applied.created.length ? ': ' + r.applied.created.join(', ') : ''}.${r.applied.skipped.length ? ' Bỏ qua: ' + r.applied.skipped.join('; ') : ''}` }[cmd];
      await bpCall(`/spend/plans/${p.id}/${cmd}`, { method: 'POST', body: JSON.stringify(body) }, ok);
      return;
    }
    if (e.target.closest('[data-bp-inc]')) return;           // handled by change
    const row = e.target.closest('[data-bp-line]');
    if (row) bpOpenLine(Number(row.dataset.bpLine));
  });
  mount.addEventListener('change', async (e) => {
    if (spState.sub !== 'plan') return;
    const p = bpState.plan;
    const pick = e.target.closest('[data-bp-pick]');
    if (pick) { bpState.planId = Number(pick.value); await renderPlanPage(); return; }
    const set = e.target.closest('[data-bp-set]');
    if (set) {
      await bpCall(`/spend/plans/${p.id}`, { method: 'PUT', body: JSON.stringify({ [set.dataset.bpSet]: set.value, rowVersion: p.rowVersion }) }, 'Đã lưu.');
      return;
    }
    const inc = e.target.closest('[data-bp-inc]');
    if (inc) {
      const l = p.lines.find(x => x.id === Number(inc.dataset.bpInc));
      const body = { included: inc.checked };
      if (!inc.checked && l.source !== 'new' && !l.note) {
        const reason = await uiPrompt(`Lý do bỏ “${l.title}” khỏi kế hoạch (vd: ngừng dịch vụ, gộp hợp đồng):`);
        if (!reason) { inc.checked = true; return; }
        body.note = reason;
      }
      await bpCall(`/spend/plans/${p.id}/lines/${l.id}`, { method: 'PUT', body: JSON.stringify(body) });
    }
  });
  mount.addEventListener('keydown', (e) => {
    if (spState.sub !== 'plan' || e.key !== 'Enter') return;
    const row = e.target.closest('[data-bp-line]');
    if (row && !e.target.closest('input')) bpOpenLine(Number(row.dataset.bpLine));
  });
}
