/* =========================================================
   Chi phí & Hợp đồng IT — Nhà cung cấp (hồ sơ, tên viết tắt) và So sánh báo giá
   (gói 3 báo giá: đọc file, kiểm tra công thức, so sánh, phiếu đánh giá NCC, chọn NCC).
   Parsing, checks, alignment and scores are computed by the backend (spend/quotes.py).
   Vendor columns always use the short name (label) returned by the API.
   ========================================================= */

const qtState = { list: null, current: null, comparison: null, tab: 'quotes', bound: false, error: null };
const vdState = { list: null, q: '', bound: false, error: null };
const QT_STATUS = { draft: ['na', 'Nháp'], comparing: ['warn', 'Đang so sánh'], selected: ['ok', 'Đã chọn NCC'], closed: ['na', 'Đóng'] };
const QT_STATE = { pending: ['na', 'Chưa đọc'], extracted: ['warn', 'Chờ kiểm tra'], verified: ['ok', 'Đã kiểm tra'], failed: ['bad', 'Không đọc được'] };
const QT_WS = { cabling: 'Cabling (gồm Hub)', network: 'Network', vc: 'Video Conference', other: 'Khác' };
const QT_LINE = { item: 'Hàng / dịch vụ', note: 'Tiêu đề nhóm', discount: 'Chiết khấu', subtotal: 'Cộng', vat: 'VAT', total: 'Tổng' };
const VD_STATUS = { active: ['ok', 'Đang dùng'], paused: ['warn', 'Tạm ngừng'], blocked: ['bad', 'Không hợp tác'] };
const qtNum = (v, d = 0) => uiNumber(v, d);
const qtRaw = (v) => (v === null || v === undefined) ? '' : String(v);
/* Money typed in an input, read like the backend (parse_number): 1.631.800.000 / 1,631,800,000 / 1631800000 / 1.234,5. */
function qtParseMoney(s) {
  const t = String(s ?? '').replace(/\s/g, '');
  if (!/^-?[\d.,]+$/.test(t)) return null;
  const dot = t.lastIndexOf('.'), com = t.lastIndexOf(',');
  let n;
  if (dot >= 0 && com >= 0) n = dot > com ? t.replace(/,/g, '') : t.replace(/\./g, '').replace(',', '.');
  else {
    const sep = dot >= 0 ? '.' : (com >= 0 ? ',' : '');
    const parts = sep ? t.split(sep) : [t];
    n = parts.length > 1 && parts.slice(1).every(p => p.length === 3) ? parts.join('') : (sep ? t.replace(sep, '.') : t);
  }
  const v = Number(n);
  return Number.isFinite(v) ? v : null;
}
/* Shown grouped vi-VN in inputs (1.631.800.000; decimals 1.234,56), like everywhere else in the app. */
const qtMoneyIn = (v) => (v === null || v === undefined || v === '') ? '' : Number(v).toLocaleString('vi-VN', { maximumFractionDigits: 2 });
function qtMoneyFix(input) {
  const v = qtParseMoney(input.value);
  if (v !== null) input.value = qtMoneyIn(v);
}
const qtVatLabel = (q) => (q.vatRates && q.vatRates.length > 1)
  ? `VAT ${q.vatRates.map(r => r === 0 ? 'KCT' : `${qtNum(r, 2)}%`).join(' / ')}`
  : (q.vatPct !== null ? `VAT ${qtNum(q.vatPct, 2)}%` : '');
const qtUrl = (code, ...parts) => ['/spend/quotes', encodeURIComponent(code), ...parts.map(p => encodeURIComponent(p))].join('/');

async function qtDownload(path, fallback, btn) {
  if (btn) btn.disabled = true;
  try { await uiDownload(`/api${path}`, {}, fallback); }
  catch (err) { uiAlert(err.message); } finally { if (btn) btn.disabled = false; }
}

/* =================================================================== Nhà cung cấp */
async function renderVendorsPage() {
  try { vdState.list = await apiRequest('/spend/vendor-profiles'); vdState.error = null; }
  catch (err) { vdState.list = []; vdState.error = err.message; }
  vdRender();
  vdBind();
}

function vdRender() {
  const q = vdState.q.toLowerCase();
  const list = (vdState.list || []).filter(v => !q || [v.name, v.shortName, ...(v.aliases || []), ...(v.tags || [])].some(x => (x || '').toLowerCase().includes(q)));
  const noShort = (vdState.list || []).filter(v => !v.shortName).length;
  const rows = list.map(v => `<tr data-vd-id="${esc(v.id)}" class="sp-row" tabindex="0">
      <td><b>${esc(v.shortName || '—')}</b></td>
      <td class="wrap">${esc(v.name)}${v.aliases.length ? `<span class="muted-sub">Tên khác: ${esc(v.aliases.join(' · '))}</span>` : ''}</td>
      <td class="wrap">${v.tags.map(t => `${uiTag(t)}`).join(' ') || '—'}</td>
      <td>${spPill(...(VD_STATUS[v.status] || ['na', v.statusLabel]))}</td>
      <td class="num">${esc(v.quotes)}<span class="muted-sub">chọn ${esc(v.won)}</span></td>
      <td class="num">${esc(v.contracts)}</td>
      <td class="num">${spMoney(v.opexTotal + v.capexTotal)}</td></tr>`).join('');
  $('#spendMount').innerHTML = `
    <section class="table-card">
      <div class="panel-head">
        <div><div class="panel-title" role="heading" aria-level="2">Nhà cung cấp</div>
          <p class="panel-sub">Tên viết tắt dùng thống nhất trong bảng so sánh, phiếu đánh giá và tờ trình. Tên gọi khác giúp nhận đúng NCC khi tên trên báo giá viết khác.</p></div>
      </div>
      ${noShort ? `<p class="criteria-status">${esc(noShort)} nhà cung cấp chưa có tên viết tắt.</p>` : ''}
      <div class="sp-filters"><label class="field sp-search"><span>Tìm</span><input type="text" data-vd-q value="${esc(vdState.q)}" placeholder="Tên, tên viết tắt, lĩnh vực"></label></div>
      ${vdState.error ? `${uiError(vdState.error)}` : ''}
      <div class="table-scroll"><table class="data-table sp-table">
        <thead><tr><th>Tên viết tắt</th><th>Tên đầy đủ</th><th>Lĩnh vực</th><th>Trạng thái</th><th class="num">Báo giá</th><th class="num">Hợp đồng</th><th class="num">Opex + Capex</th></tr></thead>
        <tbody>${rows || uiEmptyRow(7, 'Chưa có nhà cung cấp — NCC được tạo khi nhập Opex, Capex hoặc upload báo giá.')}</tbody>
      </table></div>
    </section>`;
  refreshIcons();
}

function vdBind() {
  if (vdState.bound) return;
  vdState.bound = true;
  const mount = $('#spendMount');
  mount.addEventListener('input', (e) => {
    if (spState.sub !== 'vendors' || !e.target.closest('[data-vd-q]')) return;
    vdState.q = e.target.value;
    const pos = e.target.selectionStart;
    vdRender();
    const box = $('[data-vd-q]'); if (box) { box.focus(); box.setSelectionRange(pos, pos); }
  });
  mount.addEventListener('click', (e) => {
    const row = spState.sub === 'vendors' && e.target.closest('[data-vd-id]');
    if (row) vdOpen(row.dataset.vdId);
  });
  mount.addEventListener('keydown', (e) => {
    const row = spState.sub === 'vendors' && e.target.closest('[data-vd-id]');
    if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); vdOpen(row.dataset.vdId); }
  });
}

async function vdOpen(id) {
  let v;
  try { v = await apiRequest(`/spend/vendor-profiles/${encodeURIComponent(id)}`); } catch (err) { uiAlert(err.message); return; }
  const ro = !spCan('editor') ? ' disabled' : '';
  const hist = (title, rows, empty) => `<div class="sp-h">${title}</div>${rows.length ? `<ul class="sp-cc">${rows.join('')}</ul>` : `<p class="muted-sub">${empty}</p>`}`;
  const body = `<form id="vdForm" class="sp-form" novalidate><div class="src-grid">
      <label class="field"><span>Tên viết tắt</span><input name="shortName" maxlength="64" value="${esc(v.shortName || '')}" placeholder="Vd: THRAKONE"${ro}></label>
      <label class="field"><span>Tên đầy đủ *</span><input name="name" maxlength="255" value="${esc(v.name)}"${ro}></label>
      <label class="field sp-span2"><span>Tên gọi khác (mỗi dòng một tên)</span><textarea name="aliases" rows="2"${ro}>${esc(v.aliases.join('\n'))}</textarea></label>
      <label class="field"><span>Mã số thuế</span><input name="taxCode" maxlength="32" value="${esc(v.taxCode || '')}"${ro}></label>
      <label class="field"><span>Lĩnh vực (cách nhau dấu phẩy)</span><input name="tags" maxlength="500" value="${esc(v.tags.join(', '))}" placeholder="Cabling, Network, Video Conference"${ro}></label>
      <label class="field sp-span2"><span>Địa chỉ</span><input name="address" maxlength="500" value="${esc(v.address || '')}"${ro}></label>
      <label class="field"><span>Người liên hệ</span><input name="contactName" maxlength="128" value="${esc(v.contactName || '')}"${ro}></label>
      <label class="field"><span>Email</span><input name="contactEmail" type="email" maxlength="255" value="${esc(v.contactEmail || '')}"${ro}></label>
      <label class="field"><span>Điện thoại</span><input name="contactPhone" maxlength="64" value="${esc(v.contactPhone || '')}"${ro}></label>
      <label class="field"><span>Website</span><input name="website" maxlength="255" value="${esc(v.website || '')}"${ro}></label>
      <label class="field"><span>Trạng thái</span><select name="status"${ro}>${Object.entries(VD_STATUS).map(([k, [, t]]) => spOpt(k, t, v.status)).join('')}</select></label>
      <label class="field"><span>Lý do (khi tạm ngừng / không hợp tác)</span><input name="statusReason" maxlength="500" value="${esc(v.statusReason || '')}"${ro}></label>
      <label class="field sp-span2"><span>Ghi chú</span><textarea name="notes" rows="2" maxlength="5000"${ro}>${esc(v.notes || '')}</textarea></label>
    </div></form>
    ${hist('Báo giá', v.quotes.map(q => `<li><b>${esc(q.code)}</b> — ${esc(q.title)} · ${spMoney(q.total)}${q.won ? ' · ' + uiPill('ok', 'Được chọn') : ''}${q.evalTotal !== null ? ` · điểm ${esc(qtNum(q.evalTotal, 2))} (${esc(q.evalClass)})` : ''}</li>`), 'Chưa có báo giá.')}
    ${hist('Hợp đồng', v.contracts.map(c => `<li><b>${esc(c.code)}</b> — ${esc(c.title)}</li>`), 'Chưa có hợp đồng.')}
    ${hist('Opex', v.opex.map(o => `<li><b>${esc(o.code)}</b> — ${esc(o.title)} · FY ${esc(o.fiscalYear)} · ${spMoney(o.amountGross)}</li>`), 'Chưa có khoản Opex.')}
    ${hist('Hạng mục Capex', v.capexItems.map(i => `<li><b>${esc(i.projectCode)}</b> — ${esc(i.description)} · ${spMoney(i.amountGross)}</li>`), 'Chưa có hạng mục Capex.')}`;
  uiPanelDialog(`Nhà cung cấp — ${esc(v.label)}`, body, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Đóng</button>
    ${spCan('editor') ? '<button class="btn btn-primary btn-sm" type="button" id="vdSave">Lưu</button>' : ''}`);
  spOnce('#vdSave', async () => {
    const fd = Object.fromEntries(new FormData($('#vdForm')).entries());
    const payload = { ...fd, aliases: fd.aliases.split('\n').map(s => s.trim()).filter(Boolean),
      tags: fd.tags.split(',').map(s => s.trim()).filter(Boolean) };
    try {
      await apiRequest(`/spend/vendor-profiles/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(payload) });
      spMsg('Đã lưu.', true);
      if (spState.sub === 'vendors') { vdState.list = await apiRequest('/spend/vendor-profiles'); vdRender(); }
    } catch (err) { spMsg(err.message); }
  });
}

/* =================================================================== So sánh báo giá */
async function renderQuotesPage() {
  qtBind();
  if (qtState.current) { await qtOpen(qtState.current.code); return; }
  try { qtState.list = await apiRequest('/spend/quotes'); qtState.error = null; }
  catch (err) { qtState.list = []; qtState.error = err.message; }
  qtRenderList();
}

/* Gói báo giá as a grouped list (pages/danh-sach.md). The package itself is a workbench (quotes, line comparison,
   evaluation, decision) too wide for a side pane, so it keeps its full-width page. */
function qtRenderList() {
  const list = qtState.list || [];
  const row = (r) => `<li class="sp-li" role="option" id="qt-opt-${esc(r.code)}" data-qt-code="${esc(r.code)}" aria-selected="false">
      <span class="hm-lv lv-${esc((QT_STATUS[r.status] || ['na'])[0])}" aria-hidden="true"></span>
      <span class="sp-li-t">${esc(r.title)}</span>
      <span class="sp-li-r"><b class="num">${esc(r.quotations)}/${esc(r.minQuotes)} báo giá</b>${r.selectedTotal ? spMoney(r.selectedTotal) : esc(spTime(r.updatedAt))}</span>
      <span class="sp-li-m">${esc(r.code)} · ${esc(r.workstreamLabel)}${r.selectedVendor ? ` · chọn ${esc(r.selectedVendor)}` : ''}</span>
      <span class="sr-only">${esc((QT_STATUS[r.status] || ['', r.statusLabel])[1])}</span></li>`;
  const groups = ['comparing', 'draft', 'selected', 'closed'].map(k => {
    const g = list.filter(r => r.status === k);
    return g.length ? `<li class="hm-group" role="presentation"><span>${esc(QT_STATUS[k][1])}</span><span>${g.length}</span></li>${g.map(row).join('')}` : '';
  }).join('');
  $('#spendMount').innerHTML = `
    <section class="panel qt-list-panel">
      <div class="panel-head">
        <div><div class="panel-title" role="heading" aria-level="2">So sánh báo giá</div>
          <p class="panel-sub">Mỗi gói nhận báo giá của nhiều NCC (mặc định 3). Hệ thống đọc file, kiểm tra công thức tiền, so sánh từng hạng mục và lập phiếu đánh giá NCC.</p></div>
        <div class="panel-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-qt-prices><i data-lucide="search"></i>Tra cứu đơn giá</button>${spCan('editor') ? '<button class="btn btn-primary btn-sm" type="button" data-qt-new><i data-lucide="plus"></i>Tạo gói báo giá</button>' : ''}</div>
      </div>
      ${qtState.error ? `${uiError(qtState.error)}` : ''}
      ${groups ? `<ul class="hm-rows" id="qtList" role="listbox" tabindex="0" aria-label="Gói báo giá, nhóm theo trạng thái">${groups}</ul>` : uiEmpty(spCan('editor') ? 'Chưa có gói báo giá. Bấm “Tạo gói báo giá”.' : 'Chưa có gói báo giá.')}
      <p class="hm-hint"><span>${list.length} gói</span><span><kbd>J</kbd> <kbd>K</kbd> chuyển · <kbd>Enter</kbd> mở</span></p>
    </section>`;
  refreshIcons();
}

function qtBind() {
  if (qtState.bound) return;
  qtState.bound = true;
  const mount = $('#spendMount');
  mount.addEventListener('input', (e) => {
    if (spState.sub !== 'quotes' || !e.target.matches('#qtCrit .qt-score, #qtCrit .qt-w, #qtEval [name="conclusion"]')) return;
    e.target.classList.remove('qt-sugg'); e.target.removeAttribute('title');
    qtEvalLive();
  });
  mount.addEventListener('click', (e) => {
    if (spState.sub !== 'quotes') return;
    const row = e.target.closest('[data-qt-code]');
    if (row) return qtOpen(row.dataset.qtCode);
    if (e.target.closest('[data-qt-new]')) return qtOpenForm();
    if (e.target.closest('[data-qt-prices]')) return qtOpenPrices();
    if (e.target.closest('[data-qt-back]')) { qtState.current = null; return renderQuotesPage(); }
    const tab = e.target.closest('[data-qt-tab]');
    if (tab) { qtState.tab = tab.dataset.qtTab; return qtRenderDetail(); }
    const act = e.target.closest('[data-qt-act]');
    if (act) return qtAction(act.dataset.qtAct, act.dataset.qid, act);
  });
  mount.addEventListener('keydown', (e) => {
    if (spState.sub !== 'quotes' || !e.target.closest('#qtList')) return;
    const k = e.key.toLowerCase();
    if (k === 'enter' || k === ' ') {
      const sel = $('#qtList [aria-selected="true"]') || $('#qtList [role="option"]');
      if (sel) { e.preventDefault(); qtOpen(sel.dataset.qtCode); }
      return;
    }
    uiListKeys(e, 'qtCode', ($('#qtList [aria-selected="true"]') || {}).dataset?.qtCode, (code) => uiListSelect('#qtList', 'qtCode', code, true));
  });
}

async function qtOpenForm(r) {
  let capex = [], renov = [];
  try { [capex, renov] = await Promise.all([apiRequest('/spend/capex'), apiRequest('/spend/renovations')]); } catch (err) { /* suggestions only */ }
  const p = r || { workstream: 'cabling', minQuotes: 3 };
  const body = `<form id="qtForm" class="sp-form" novalidate><div class="src-grid">
      <label class="field sp-span2"><span>Tên gói *</span><input name="title" maxlength="255" value="${esc(p.title || '')}" placeholder="Vd: Thi công hạ tầng cáp FL9"></label>
      <label class="field"><span>Khối</span><select name="workstream">${Object.entries(QT_WS).map(([k, t]) => spOpt(k, t, p.workstream)).join('')}</select></label>
      <label class="field"><span>Số báo giá tối thiểu</span><input name="minQuotes" type="number" min="1" max="10" value="${esc(p.minQuotes || 3)}"></label>
      <label class="field"><span>Dự toán trước VAT (đ)</span><input name="estimateAmount" inputmode="numeric" value="${esc(qtRaw(p.estimateAmount))}"></label>
      <label class="field"><span>Dự án Capex</span><input name="capexCode" list="qtCapexList" maxlength="20" value="${esc((p.capex && p.capex.code) || '')}" placeholder="CPX-2026-0001"></label>
      <label class="field"><span>Dự án cải tạo văn phòng</span><select name="renovationCode">${spOpt('', '— không —', (p.renovation && p.renovation.code) || '')}${renov.filter(x => !x.isHistorical).map(x => spOpt(x.code, `${x.floor} (${x.year}) — ${x.name}`, (p.renovation && p.renovation.code) || '')).join('')}</select></label>
      <label class="field sp-span2"><span>Mô tả / phạm vi</span><textarea name="description" rows="3" maxlength="5000">${esc(p.description || '')}</textarea></label>
    </div><datalist id="qtCapexList">${capex.map(c => `<option value="${esc(c.code)}">${esc(c.name)}</option>`).join('')}</datalist></form>
    ${r ? '' : '<p class="muted-sub">Phiếu đánh giá được tạo sẵn: khung tiêu chuẩn 70% (6 tiêu chí) và khung mở rộng 30% theo khối — sửa được ở tab Đánh giá.</p>'}`;
  uiPanelDialog(r ? `Sửa gói ${esc(r.code)}` : 'Tạo gói báo giá', body, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
    <button class="btn btn-primary btn-sm" type="button" id="qtSave">${r ? 'Lưu' : 'Tạo'}</button>`);
  spOnce('#qtSave', async () => {
    const fd = Object.fromEntries(new FormData($('#qtForm')).entries());
    try {
      const d = await apiRequest(r ? qtUrl(r.code) : '/spend/quotes', { method: r ? 'PUT' : 'POST', body: JSON.stringify(fd) });
      $('#spDialog').close();
      await qtOpen(d.code);
    } catch (err) { spMsg(err.message); }
  });
}

async function qtOpen(code) {
  try {
    const [d, c] = await Promise.all([apiRequest(qtUrl(code)), apiRequest(qtUrl(code, 'comparison'))]);
    qtState.current = d; qtState.comparison = c;
  } catch (err) { uiAlert(err.message); qtState.current = null; return; }
  qtRenderDetail();
}

function qtLocked() { const d = qtState.current; return !d || d.status === 'selected' || d.status === 'closed'; }

function qtRenderDetail() {
  const d = qtState.current;
  const tabs = [['quotes', `Báo giá (${d.quotations.length})`], ['compare', 'So sánh'], ['eval', 'Đánh giá NCC'], ['decide', 'Kết luận']];
  const body = { quotes: qtTabQuotes, compare: qtTabCompare, eval: qtTabEval, decide: qtTabDecide }[qtState.tab] || qtTabQuotes;
  $('#spendMount').innerHTML = `
    <section class="panel">
      <div class="panel-head">
        <div><button class="btn btn-ghost btn-sm" type="button" data-qt-back><i data-lucide="arrow-left"></i>Danh sách gói</button>
          <div class="panel-title" role="heading" aria-level="2">${esc(d.code)} — ${esc(d.title)}</div>
          <p class="panel-sub">${esc(d.workstreamLabel)} · dự toán ${spMoney(d.estimateAmount)}${d.capex ? ` · Capex ${esc(d.capex.code)}` : ''}${d.renovation ? ` · cải tạo ${esc(d.renovation.floor)} (${esc(d.renovation.code)})` : ''} · ${spPill(...(QT_STATUS[d.status] || ['na', d.statusLabel]))}</p></div>
        <div class="panel-toolbar">
          ${spCan('editor') && !qtLocked() ? '<button class="btn btn-ghost btn-sm" type="button" data-qt-act="edit"><i data-lucide="pencil"></i>Sửa gói</button>' : ''}
          ${spCan('admin') && !qtLocked() ? '<button class="btn btn-ghost btn-sm" type="button" data-qt-act="delete"><i data-lucide="trash-2"></i>Xoá</button>' : ''}
        </div>
      </div>
      ${d.warnings.map(w => `<p class="criteria-status">${esc(w)}</p>`).join('')}
      <div class="sim-tabbar" role="tablist">${tabs.map(([k, t]) => `<button class="sim-tab${qtState.tab === k ? ' active' : ''}" type="button" role="tab" aria-selected="${qtState.tab === k}" data-qt-tab="${k}">${esc(t)}</button>`).join('')}</div>
      <div id="qtTabBody">${body(d)}</div>
    </section>`;
  refreshIcons();
  if (qtState.tab === 'eval' && $('#qtCrit')) qtEvalLive();     // totals include the pre-filled suggestions
}

function qtTabQuotes(d) {
  const canEdit = spCan('editor') && !qtLocked();
  const cards = d.quotations.map(q => {
    const crit = q.findings.filter(f => f.severity === 'crit').length;
    const pdf = q.file && /\.pdf$/i.test(q.file.name);
    return `<div class="qt-card">
      <div class="qt-card-head"><b>${esc(q.vendorLabel)}</b>${q.vendorHasShortName ? '' : ' <span class="muted-sub sr-inline">chưa có tên viết tắt</span>'}
        ${spPill(...(QT_STATE[q.state] || ['na', q.stateLabel]))}</div>
      <dl class="sp-dl">
        <div class="sp-dl-item"><dt>Trước VAT (cộng dòng)</dt><dd>${spMoney(q.subtotalVnd)}</dd></div>
        <div class="sp-dl-item"><dt>Tổng NCC ghi</dt><dd>${spMoney(q.totalVnd)}${qtVatLabel(q) ? ` <span class="muted-sub sr-inline">${esc(qtVatLabel(q))}</span>` : ''}</dd></div>
        <div class="sp-dl-item"><dt>Dòng hàng</dt><dd>${esc(q.itemCount)}${q.method ? ` <span class="muted-sub sr-inline">${esc({ excel: 'đọc Excel', docx: 'đọc Word', ai: 'đọc bằng AI', manual: 'nhập tay' }[q.method] || q.method)}</span>` : ''}</dd></div>
        <div class="sp-dl-item"><dt>File</dt><dd>${q.file ? `<a href="/api/spend/files/${encodeURIComponent(q.file.id)}" download>${esc(q.file.name)}</a>` : '—'}</dd></div>
      </dl>
      ${q.message ? `<p class="muted-sub">${esc(q.message)}</p>` : ''}
      ${q.findings.length ? `<ul class="qt-findings">${q.findings.map(f => `<li class="${f.severity === 'crit' ? 'crit' : 'warn'}"><b>${esc(f.code)}</b> ${esc(f.message)}</li>`).join('')}</ul>`
        : (q.itemCount ? '<p class="qt-ok">Không phát hiện sai lệch công thức.</p>' : '')}
      <div class="sp-inline">
        <button class="btn btn-ghost btn-sm" type="button" data-qt-act="lines" data-qid="${esc(q.id)}"><i data-lucide="table"></i>${canEdit ? 'Xem / sửa dòng' : 'Xem dòng'}</button>
        ${canEdit && q.file && !pdf ? `<button class="btn btn-ghost btn-sm" type="button" data-qt-act="extract" data-qid="${esc(q.id)}"><i data-lucide="refresh-cw"></i>Đọc lại file</button>` : ''}
        ${canEdit && pdf ? `<button class="btn btn-ghost btn-sm" type="button" data-qt-act="extract" data-qid="${esc(q.id)}"${d.aiEnabled ? '' : ' disabled title="AI chưa bật (Cài đặt → AI)"'}><i data-lucide="sparkles"></i>Đọc PDF bằng AI</button>` : ''}
        ${canEdit && q.state === 'extracted' ? `<button class="btn btn-primary btn-sm" type="button" data-qt-act="verify" data-qid="${esc(q.id)}"><i data-lucide="check"></i>Đã kiểm tra${crit ? ' (giữ lỗi của NCC)' : ''}</button>` : ''}
        ${canEdit ? `<button class="btn btn-ghost btn-sm" type="button" data-qt-act="remove" data-qid="${esc(q.id)}"><i data-lucide="trash-2"></i>Xoá</button>` : ''}
      </div></div>`;
  }).join('');
  const vendors = (spState.meta && spState.meta.vendors || []).filter(v => v.active);
  const upload = canEdit ? `<div class="ct-upload"><div class="sp-h">Thêm báo giá</div>
      ${uiDropzone('qtFile', 'Chọn file báo giá', 'Excel, Word hoặc PDF', '.xlsx,.docx,.pdf')}
      <div class="sp-inline"><input type="text" id="qtVendor" list="qtVendorList" maxlength="255" placeholder="Nhà cung cấp (tên, tên viết tắt hoặc tên khác)">
        <button class="btn btn-primary btn-sm" type="button" data-qt-act="upload"><i data-lucide="upload"></i>Upload</button></div>
      <p class="muted-sub">Excel / Word được đọc ngay. PDF scan: bấm “Đọc PDF bằng AI” (chỉ khi quản trị đã bật AI) hoặc nhập dòng bằng tay. Mọi số đọc được phải được người kiểm tra trước khi chọn NCC.</p>
      <datalist id="qtVendorList">${vendors.map(v => `<option value="${esc(v.shortName || v.name)}">${esc(v.name)}</option>`).join('')}</datalist></div>` : '';
  return `${cards ? `<div class="qt-cards">${cards}</div>` : '<p class="empty-note">Chưa có báo giá.</p>'}${upload}<p class="src-result" id="qtMsg" role="status"></p>`;
}

function qtTabCompare(d) {
  const c = qtState.comparison;
  if (!c || !c.columns.length) return '<p class="empty-note">Chưa có báo giá để so sánh.</p>';
  const cols = c.columns;
  const head = cols.map(x => `<th class="num" colspan="2">${esc(x.label)}<span class="muted-sub">${spPill(...(QT_STATE[x.state] || ['na', x.state]))}</span></th>`).join('');
  const sub = cols.map(() => '<th class="num">Đơn giá</th><th class="num">Thành tiền</th>').join('');
  const rows = c.rows.map((r, i) => `<tr><td>${i + 1}</td><td class="wrap">${esc(r.description)}${r.spreadPct ? `<span class="muted-sub">chênh ${esc(qtNum(r.spreadPct, 1))}%</span>` : ''}</td><td>${esc(r.unit || '')}</td>
      ${cols.map(x => { const cell = r.cells[String(x.quotationId)]; const lo = r.minQuotation === String(x.quotationId) && cols.length > 1 && r.spreadPct;
        return cell ? `<td class="num">${qtNum(cell.unitPriceVnd)}<span class="muted-sub">SL ${esc(qtNum(cell.qty, 3))}</span></td><td class="num${lo ? ' qt-min' : ''}">${qtNum(cell.amountVnd)}</td>` : '<td class="num">—</td><td class="num qt-missing">không có</td>'; }).join('')}</tr>`).join('');
  const foot = (label, key, fmt) => `<tr class="qt-foot"><td></td><td><b>${label}</b></td><td></td>${cols.map(x => `<td></td><td class="num"><b>${fmt(x[key], x)}</b></td>`).join('')}</tr>`;
  return `${qtAiBlock(d)}<div class="sp-inline qt-bar"><span class="muted-sub">${esc(c.commonRows)}/${esc(c.rows.length)} hạng mục có ở mọi báo giá. Ô xanh: thấp nhất trong hàng.</span>
      <span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-qt-act="xlsx-compare"><i data-lucide="file-spreadsheet"></i>Xuất Excel so sánh</button></div>
    <div class="table-scroll"><table class="data-table qt-matrix">
      <thead><tr><th rowspan="2">#</th><th rowspan="2">Hạng mục</th><th rowspan="2">ĐVT</th>${head}</tr><tr>${sub}</tr></thead>
      <tbody>${rows}</tbody>
      <tfoot>${foot('Trước VAT', 'subtotalVnd', v => qtNum(v))}
        ${foot('Hạng mục chung', 'commonTotalVnd', v => qtNum(v))}
        ${foot('Tổng NCC ghi', 'totalVnd', v => qtNum(v))}
        ${foot('Xếp hạng', 'rank', (v, x) => v ? `#${v}${x.diffPct ? ` (+${qtNum(x.diffPct, 1)}%)` : ''}` : '—')}
        ${foot('Lỗi công thức', 'critFindings', v => v ? `${v} lỗi` : '0')}</tfoot>
    </table></div>
    <div class="sp-h">Điều kiện thương mại</div>
    <div class="table-scroll"><table class="data-table"><thead><tr><th></th>${cols.map(x => `<th>${esc(x.label)}</th>`).join('')}</tr></thead><tbody>
      ${[['Hiệu lực', 'validUntil', spDate], ['Giao hàng', 'deliveryTerms'], ['Bảo hành', 'warrantyTerms'], ['Thanh toán', 'paymentTerms']].map(([l, k, f]) =>
        `<tr><td><b>${l}</b></td>${cols.map(x => `<td class="wrap">${esc(f ? f(x[k]) : (x[k] || '—'))}</td>`).join('')}</tr>`).join('')}
    </tbody></table></div>`;
}

function qtTabEval(d) {
  const ev = d.evaluation, cols = ev.columns, ro = !(spCan('editor') && !qtLocked()) ? ' disabled' : '';
  if (!cols.length) return '<p class="empty-note">Thêm báo giá trước khi đánh giá NCC.</p>';
  const sgAll = ev.suggestion || {}, sg = sgAll.scores || {};
  const anySug = !ro && ev.criteria.some(c => cols.some(x => (c.scores[String(x.quotationId)] ?? null) === null && sg[String(c.id)] && sg[String(c.id)][String(x.quotationId)] !== undefined));
  const conclusion = ev.conclusion || (!ro && sgAll.conclusion) || '';
  const pickLabel = (cols.find(x => x.quotationId === sgAll.pick) || {}).label;
  const crit = (sec) => ev.criteria.filter(c => c.section === sec).map(c => `<tr data-crit data-sec="${sec}">
      <td><input type="text" class="qt-crit-name" aria-label="Tiêu chí" value="${esc(c.name)}" maxlength="255"${ro}></td>
      <td class="num in"><input type="number" class="qt-w" aria-label="Tỷ trọng (%) — ${esc(c.name)}" min="1" max="70" step="0.5" value="${esc(Math.round(c.weight * 1000) / 10)}"${ro}></td>
      ${cols.map(x => { const q = String(x.quotationId), saved = c.scores[q], sug = sg[String(c.id)] && sg[String(c.id)][q];
        const useSug = (saved === null || saved === undefined) && sug !== undefined && !ro;
        return `<td class="num in"><input type="number" class="qt-score${useSug ? ' qt-sugg' : ''}" aria-label="${esc(x.label)} — ${esc(c.name)}" min="1" max="10" step="0.05" data-q="${esc(q)}" data-cid="${esc(c.id)}" value="${esc(qtRaw(useSug ? sug : saved))}"${useSug ? ' title="Điểm gợi ý — sửa được, lưu khi bấm Lưu đánh giá"' : ''}${ro}></td>`; }).join('')}
      ${ro ? '' : '<td class="act"><button class="btn btn-ghost btn-sm icon-btn" type="button" data-qt-act="crit-del" aria-label="Bỏ tiêu chí"><i data-lucide="x"></i></button></td>'}</tr>`).join('');
  const sec = (s, label) => `<tr class="qt-sec"><td colspan="${2 + cols.length + (ro ? 0 : 1)}"><b>${label}</b>${ro || s === 'A' ? '' : ' <button class="btn btn-ghost btn-sm" type="button" data-qt-act="crit-add">+ tiêu chí</button>'}</td></tr>`;
  const hint = Object.keys(ev.priceScoreHint).length ? `Gợi ý điểm Giá thành (rẻ nhất 10, mỗi 5% cao hơn trừ 1): ${cols.map(x => `${esc(x.label)} ${esc(qtNum(ev.priceScoreHint[String(x.quotationId)], 1))}`).join(' · ')}` : '';
  return `<form id="qtEval" class="sp-form" novalidate><div class="src-grid">
      <label class="field"><span>Bộ phận đánh giá</span><input name="department" maxlength="64" value="${esc(ev.department || '')}"${ro}></label>
      <label class="field"><span>Ngày đánh giá</span><input name="date" type="date" value="${esc(ev.date || '')}"${ro}></label>
      <label class="field sp-span2"><span>Loại hình sản phẩm, dịch vụ</span><input name="service" maxlength="255" value="${esc(ev.service || '')}"${ro}></label>
      <label class="field sp-span2"><span>Kết luận</span><input name="conclusion" maxlength="255" value="${esc(conclusion)}"${!ev.conclusion && conclusion ? ' class="qt-sugg" title="Đề xuất theo giá tốt nhất — sửa được"' : ''} placeholder="Đề xuất chọn …"${ro}></label>
      <label class="field"><span>Người thực hiện đánh giá</span><input name="evaluator" maxlength="128" value="${esc(ev.evaluator || '')}"${ro}></label>
      <label class="field"><span>Trưởng Bộ phận phê duyệt</span><input name="approver" maxlength="128" value="${esc(ev.approver || '')}"${ro}></label>
    </div></form>
    <div class="table-scroll"><table class="data-table qt-eval tbl-inputs" style="--tbl-first:280px;--tbl-col:112px;--tbl-n:${cols.length + 1};--tbl-act:${ro ? 0 : 48}px">
      <colgroup><col><col class="c-num">${cols.map(() => '<col class="c-num">').join('')}${ro ? '' : '<col class="c-act">'}</colgroup>
      <thead><tr><th>Tiêu chí đánh giá</th><th class="num">Tỷ trọng (%)</th>${cols.map(x => `<th class="num" title="${esc(x.label)}">${esc(x.label)}</th>`).join('')}${ro ? '' : '<th></th>'}</tr></thead>
      <tbody id="qtCrit">${sec('A', 'A. Khung đánh giá tiêu chuẩn — 70%')}${crit('A')}${sec('B', 'B. Khung đánh giá mở rộng — 30% (2–4 tiêu chí)')}${crit('B')}</tbody>
      <tfoot><tr class="qt-foot"><td><b>Tổng điểm</b></td><td></td>${cols.map(x => `<td class="num"><b data-qt-total="${esc(x.quotationId)}">${esc(qtNum(ev.totals[String(x.quotationId)], 2))}</b></td>`).join('')}${ro ? '' : '<td></td>'}</tr>
        <tr class="qt-foot"><td><b>Loại đánh giá</b></td><td></td>${cols.map(x => `<td class="num"><b data-qt-class="${esc(x.quotationId)}">${esc(ev.classes[String(x.quotationId)] || '—')}</b></td>`).join('')}${ro ? '' : '<td></td>'}</tr></tfoot>
    </table></div>
    ${anySug ? `<p class="note-line qt-sugg-note">${uiTag('Gợi ý')}<span>Ô in nghiêng là điểm gợi ý: <b>Giá thành</b> theo giá (rẻ nhất 10, mỗi 5% cao hơn trừ 1), tiêu chí khác 10${pickLabel ? ` — <b>${esc(pickLabel)}</b> giá tốt nhất` : ''}. Sửa nếu cần rồi bấm <b>Lưu đánh giá</b>.</span></p>` : ''}
    <p class="muted-sub">Thang điểm 1–10. Xếp loại: ≥ 8 A · ≥ 7 B · ≥ 6 C · dưới 6 Không đạt. ${anySug ? '' : hint}</p>
    <div class="sp-inline"><span class="src-spacer"></span>
      ${ro || !sgAll.pick ? '' : '<button class="btn btn-ghost btn-sm" type="button" data-qt-act="eval-suggest" title="Điền lại điểm Giá thành theo giá và câu kết luận đề xuất NCC giá tốt nhất"><i data-lucide="sparkles"></i>Gợi ý lại theo giá</button>'}
      <button class="btn btn-ghost btn-sm" type="button" data-qt-act="xlsx-eval"><i data-lucide="file-spreadsheet"></i>Xuất phiếu đánh giá (Excel)</button>
      ${ro ? '' : '<button class="btn btn-primary btn-sm" type="button" data-qt-act="eval-save">Lưu đánh giá</button>'}</div>
    <p class="src-result" id="qtMsg" role="status"></p>`;
}

/* Live total / class of the evaluation table, same formula as quotes.weighted_total / classify on the server. */
const qtClassOf = (t) => t === null ? '—' : t >= 8 ? 'A' : t >= 7 ? 'B' : t >= 6 ? 'C' : 'Không đạt';
function qtEvalLive() {
  const rows = $$('#qtCrit [data-crit]');
  $$('[data-qt-total]').forEach(el => {
    const q = el.dataset.qtTotal;
    let sum = 0, ok = rows.length > 0;
    rows.forEach(tr => {
      const w = Number(tr.querySelector('.qt-w').value) / 100, inp = tr.querySelector(`.qt-score[data-q="${q}"]`);
      if (!inp || inp.value === '') { ok = false; return; }
      sum += w * Number(inp.value);
    });
    const t = ok ? Math.round(sum * 100) / 100 : null;
    el.textContent = t === null ? '—' : qtNum(t, 2);
    const c = $(`[data-qt-class="${q}"]`); if (c) c.textContent = qtClassOf(t);
  });
}

/* "Gợi ý lại theo giá": price criterion from the current prices, conclusion = cheapest vendor. Other scores stay. */
function qtEvalApplySuggestion(d) {
  const sg = (d.evaluation.suggestion || {});
  (sg.priceCriteria || []).forEach(cid => {
    $$(`#qtCrit .qt-score[data-cid="${cid}"]`).forEach(inp => {
      const v = sg.scores[String(cid)] && sg.scores[String(cid)][inp.dataset.q];
      if (v !== undefined) { inp.value = v; inp.classList.add('qt-sugg'); }
    });
  });
  $$('#qtCrit .qt-score').forEach(inp => {
    if (inp.value === '') { const v = sg.scores[inp.dataset.cid] && sg.scores[inp.dataset.cid][inp.dataset.q]; if (v !== undefined) { inp.value = v; inp.classList.add('qt-sugg'); } }
  });
  const concl = $('#qtEval [name="conclusion"]');
  if (concl && sg.conclusion) { concl.value = sg.conclusion; concl.classList.add('qt-sugg'); }
  qtEvalLive();
  qtMsg('Đã điền gợi ý theo giá — kiểm tra rồi bấm Lưu đánh giá.', true);
}

function qtTabDecide(d) {
  const sel = d.quotations.find(q => q.id === d.selectedQuotationId);
  const verified = d.quotations.filter(q => q.state === 'verified');
  const ev = d.evaluation;
  if (sel) {
    return `<dl class="sp-dl">
        <div class="sp-dl-item"><dt>NCC được chọn</dt><dd><b>${esc(sel.vendorLabel)}</b> — ${esc(sel.vendorName || '')}</dd></div>
        <div class="sp-dl-item"><dt>Giá trị</dt><dd>${spMoney(sel.subtotalVnd)} trước VAT · ${spMoney(sel.totalVnd)} sau VAT</dd></div>
        <div class="sp-dl-item"><dt>Điểm đánh giá</dt><dd>${esc(qtNum(ev.totals[String(sel.id)], 2))} (${esc(ev.classes[String(sel.id)] || '—')})</dd></div>
        <div class="sp-dl-item"><dt>Người chọn</dt><dd>${esc(d.selectedBy || '')} · ${esc(spTime(d.selectedAt))}</dd></div>
      </dl>${d.selectionReason ? `<p class="sp-desc">${esc(d.selectionReason)}</p>` : ''}
      <div class="sp-h">Bước tiếp theo</div>
      <div class="sp-inline">
        ${d.capex ? `${uiPill('ok', `Capex ${d.capex.code}`)}` : (spCan('editor') ? '<button class="btn btn-ghost btn-sm" type="button" data-qt-act="to-capex"><i data-lucide="package-plus"></i>Tạo dự án Capex</button>' : '')}
        ${d.contracts.length ? d.contracts.map(c => `${uiPill('ok', `HĐ ${c.code}`)}`).join(' ') : (spCan('editor') && d.capex ? '<button class="btn btn-ghost btn-sm" type="button" data-qt-act="to-contract"><i data-lucide="scroll-text"></i>Tạo hợp đồng</button>' : '')}
        ${spCan('editor') ? '<button class="btn btn-ghost btn-sm" type="button" data-qt-act="to-proposal"><i data-lucide="file-text"></i>Soạn tờ trình Mẫu B</button>' : ''}
        <span class="src-spacer"></span>
        ${spCan('approver') ? '<button class="btn btn-ghost btn-sm" type="button" data-qt-act="reopen"><i data-lucide="rotate-ccw"></i>Mở lại để sửa</button>' : ''}
      </div>
      <p class="muted-sub">Tờ trình Mẫu B lấy bảng so sánh, phiếu đánh giá, lý do lựa chọn, tóm tắt khác biệt (nếu có) và so sánh lịch sử cải tạo (nếu gói gắn dự án cải tạo).</p>
      <p class="src-result" id="qtMsg" role="status"></p>`;
  }
  const opts = d.quotations.map(q => `<label class="qt-choice"><input type="radio" name="qtPick" value="${esc(q.id)}"${q.state === 'verified' ? '' : ' disabled'}>
      <b>${esc(q.vendorLabel)}</b> · ${spMoney(q.subtotalVnd)} trước VAT · điểm ${esc(qtNum(ev.totals[String(q.id)], 2))} ${esc(ev.classes[String(q.id)] || '')}
      ${q.state === 'verified' ? '' : '<span class="muted-sub sr-inline">chưa kiểm tra</span>'}</label>`).join('');
  return `<p class="panel-sub">Đã kiểm tra ${esc(verified.length)}/${esc(d.minQuotes)} báo giá. Chỉ chọn được báo giá đã kiểm tra; ít hơn ${esc(d.minQuotes)} báo giá thì phải ghi lý do.</p>
    <div class="qt-choices">${opts || '<p class="empty-note">Chưa có báo giá.</p>'}</div>
    <label class="field sp-span2"><span>Lý do lựa chọn</span><textarea id="qtReason" rows="3" maxlength="5000" placeholder="Vd: giá thấp nhất, điểm đánh giá cao nhất; hoặc lý do chỉ có 1–2 báo giá"></textarea></label>
    ${spCan('approver') ? '<button class="btn btn-primary btn-sm" type="button" data-qt-act="select"><i data-lucide="badge-check"></i>Chọn nhà cung cấp</button>' : '<p class="muted-sub">Cần quyền Duyệt để chọn nhà cung cấp.</p>'}
    <p class="src-result" id="qtMsg" role="status"></p>`;
}

function qtMsg(text, ok) {
  const el = $('#qtMsg');
  if (!el) { if (text && !ok) uiAlert(text); return; }
  el.classList.add('src-result');
  uiSetStatus(el, text, text ? (ok ? 'ok' : 'error') : '');
}

function qtReadEval() {
  const fd = Object.fromEntries(new FormData($('#qtEval')).entries());
  fd.criteria = $$('#qtCrit [data-crit]').map(tr => {
    const scores = {};
    tr.querySelectorAll('.qt-score').forEach(i => { if (i.value !== '') scores[i.dataset.q] = i.value; });
    return { section: tr.dataset.sec, name: tr.querySelector('.qt-crit-name').value, weight: String(Number(tr.querySelector('.qt-w').value) / 100), scores };
  });
  return fd;
}

async function qtAction(act, qid, btn) {
  const d = qtState.current, code = d.code;
  const run = async (fn, okText) => {
    btn.disabled = true;
    try { await fn(); await qtOpen(code); if (okText) qtMsg(okText, true); }
    catch (err) { qtMsg(err.message); } finally { btn.disabled = false; }
  };
  if (act === 'edit') return qtOpenForm(d);
  if (act === 'delete') {
    if (!await uiConfirm(`Xoá gói ${code} và mọi báo giá, file đính kèm?`)) return;
    try { await apiRequest(qtUrl(code), { method: 'DELETE' }); qtState.current = null; renderQuotesPage(); } catch (err) { uiAlert(err.message); }
    return;
  }
  if (act === 'upload') {
    const vendor = $('#qtVendor').value.trim(), file = $('#qtFile').files[0];
    if (!vendor) { qtMsg('Nhập nhà cung cấp.'); return; }
    const fd = new FormData(); fd.append('vendorName', vendor); if (file) fd.append('file', file);
    return run(() => apiRequest(qtUrl(code, 'quotations'), { method: 'POST', body: fd }), file ? 'Đã thêm báo giá.' : 'Đã thêm báo giá — nhập dòng bằng tay.');
  }
  if (act === 'extract') return run(() => apiRequest(qtUrl(code, 'quotations', qid, 'extract'), { method: 'POST' }), 'Đã đọc lại — kiểm tra từng dòng với file gốc.');
  if (act === 'verify') {
    const q = d.quotations.find(x => String(x.id) === String(qid));
    return run(() => apiRequest(qtUrl(code, 'quotations', qid, 'verify'), { method: 'POST', body: JSON.stringify({ rowVersion: q && q.rowVersion }) }), 'Đã đánh dấu kiểm tra.');
  }
  if (act === 'remove') {
    if (!await uiConfirm('Xoá báo giá này và file đính kèm?')) return;
    return run(() => apiRequest(qtUrl(code, 'quotations', qid), { method: 'DELETE' }));
  }
  if (act === 'lines') return qtOpenLines(qid);
  if (act === 'xlsx-compare') return qtDownload(qtUrl(code, 'comparison.xlsx'), `so-sanh-bao-gia-${code}.xlsx`, btn);
  if (act === 'xlsx-eval') return qtDownload(qtUrl(code, 'evaluation.xlsx'), `evaluation-form-${code}.xlsx`, btn);
  if (act === 'crit-add') {
    const rows = $$('#qtCrit [data-crit][data-sec="B"]');
    if (rows.length >= 4) { qtMsg('Khung mở rộng tối đa 04 tiêu chí.'); return; }
    const tpl = rows[rows.length - 1].cloneNode(true);
    // new criterion: weight 0 for the person to set, every vendor 10 by default (editable, like the others)
    tpl.querySelectorAll('input').forEach(i => {
      i.value = i.classList.contains('qt-w') ? '0' : i.classList.contains('qt-score') ? '10' : '';
      i.classList.toggle('qt-sugg', i.classList.contains('qt-score'));
      delete i.dataset.cid;
    });
    rows[rows.length - 1].after(tpl);
    return;
  }
  if (act === 'crit-del') { btn.closest('tr').remove(); return; }
  if (act === 'eval-suggest') return qtEvalApplySuggestion(d);
  if (act === 'eval-save') return run(() => apiRequest(qtUrl(code, 'evaluation'), { method: 'PUT', body: JSON.stringify(qtReadEval()) }), 'Đã lưu đánh giá.');
  if (act === 'select') {
    const pick = $('input[name="qtPick"]:checked');
    if (!pick) { qtMsg('Chọn một báo giá đã kiểm tra.'); return; }
    return run(() => apiRequest(qtUrl(code, 'select'), { method: 'POST', body: JSON.stringify({ quotationId: Number(pick.value), reason: $('#qtReason').value }) }), 'Đã chọn nhà cung cấp.');
  }
  if (act === 'ai-summary') return run(() => apiRequest(qtUrl(code, 'ai-summary'), { method: 'POST' }), 'AI đã tóm tắt khác biệt — đọc và đối chiếu trước khi dùng.');
  if (act === 'to-capex') return qtOpenToCapex(d);
  if (act === 'to-contract') return run(async () => { const r = await apiRequest(qtUrl(code, 'contract'), { method: 'POST' }); qtMsg(`Đã tạo hợp đồng ${r.contractCode}.`, true); }, 'Đã tạo hợp đồng.');
  if (act === 'to-proposal') return qtOpenToProposal(d);
  if (act === 'reopen') {
    if (!await uiConfirm('Mở lại gói để sửa? Lựa chọn NCC hiện tại sẽ bị huỷ.')) return;
    return run(() => apiRequest(qtUrl(code, 'reopen'), { method: 'POST' }));
  }
}

/* ---------- Line editor ---------- */
function qtLineRow(l = {}, ro = '') {
  return `<tr data-line>
    <td><select class="ql-type"${ro}>${Object.entries(QT_LINE).map(([k, t]) => spOpt(k, t, l.lineType || 'item')).join('')}</select></td>
    <td><input class="ql-desc" maxlength="2000" value="${esc(l.description || '')}"${ro}></td>
    <td><input class="ql-model" maxlength="255" value="${esc(l.model || '')}"${ro}></td>
    <td><input class="ql-unit" maxlength="32" value="${esc(l.unit || '')}"${ro}></td>
    <td><input class="ql-qty num" inputmode="decimal" value="${esc(qtRaw(l.qty))}"${ro}></td>
    <td><input class="ql-price num qt-money" inputmode="decimal" value="${esc(qtMoneyIn(l.unitPrice))}"${ro}></td>
    <td><input class="ql-amount num qt-money" inputmode="decimal" value="${esc(qtMoneyIn(l.amount))}"${ro}></td>
    <td><input class="ql-vat num" inputmode="decimal" value="${esc(qtRaw(l.vatPct))}" placeholder="theo BG" title="Thuế suất dòng: 8, 10… KCT = 0; để trống = VAT % của báo giá"${ro}></td>
    <td><input class="ql-vatamt num qt-money" inputmode="decimal" value="${esc(qtMoneyIn(l.vatAmount))}"${ro}></td>
    <td><input class="ql-sets num" inputmode="decimal" value="${esc(qtRaw(l.sets))}" placeholder="1" title="Dòng là chi tiết cho 1 phòng / bộ của một gói tính N lần: nhập N"${ro}></td>
    ${ro ? '' : '<td><button class="btn btn-ghost btn-sm" type="button" data-ql-del aria-label="Xoá dòng"><i data-lucide="x"></i></button></td>'}</tr>`;
}

async function qtOpenLines(qid) {
  const code = qtState.current.code;
  let q;
  try { q = await apiRequest(qtUrl(code, 'quotations', qid)); } catch (err) { uiAlert(err.message); return; }
  const ro = !(spCan('editor') && !qtLocked()) ? ' disabled' : '';
  const bad = new Set(q.findings.filter(f => f.lineId).map(f => f.lineId));
  const f = (name, label, val, attrs = '') => `<label class="field"><span>${label}</span><input name="${name}" value="${esc(/qt-money/.test(attrs) ? qtMoneyIn(val) : qtRaw(val))}"${attrs}${ro}></label>`;
  const body = `<form id="qlForm" class="sp-form" novalidate><div class="src-grid qt-head-grid">
      ${f('quoteNo', 'Số báo giá', q.quoteNo, ' maxlength="64"')}${f('quoteDate', 'Ngày báo giá', q.quoteDate, ' type="date"')}
      ${f('validUntil', 'Hiệu lực đến', q.validUntil, ' type="date"')}${f('currency', 'Tiền tệ', q.currency, ' maxlength="3"')}
      ${f('fxRate', 'Tỷ giá (nếu ngoại tệ)', q.fxRate, ' inputmode="decimal" class="qt-money"')}${f('vatPct', 'VAT (%)', q.vatPct, ' inputmode="decimal"')}
      ${f('statedSubtotal', 'Trước VAT — NCC ghi', q.statedSubtotal, ' inputmode="decimal" class="qt-money"')}${f('statedDiscount', 'Chiết khấu — NCC ghi', q.statedDiscount, ' inputmode="decimal" class="qt-money"')}
      ${f('statedVat', 'VAT — NCC ghi', q.statedVat, ' inputmode="decimal" class="qt-money"')}${f('statedTotal', 'Tổng — NCC ghi', q.statedTotal, ' inputmode="decimal" class="qt-money"')}
      ${f('deliveryTerms', 'Giao hàng', q.deliveryTerms, ' maxlength="500"')}${f('warrantyTerms', 'Bảo hành', q.warrantyTerms, ' maxlength="500"')}
      ${f('paymentTerms', 'Thanh toán', q.paymentTerms, ' maxlength="500"')}
    </div></form>
    ${q.findings.length ? `<ul class="qt-findings">${q.findings.map(x => `<li class="${x.severity === 'crit' ? 'crit' : 'warn'}"><b>${esc(x.code)}</b> ${esc(x.message)}</li>`).join('')}</ul>` : ''}
    <div class="table-scroll"><table class="data-table qt-lines"><thead><tr><th>Loại</th><th>Mô tả</th><th>Model</th><th>ĐVT</th><th class="num">SL</th><th class="num">Đơn giá</th><th class="num">Thành tiền</th><th class="num">VAT %</th><th class="num">Thuế GTGT</th><th class="num">Số bộ</th>${ro ? '' : '<th></th>'}</tr></thead>
      <tbody id="qlBody">${(q.lines || []).map(l => qtLineRow(l, ro).replace('<tr data-line>', `<tr data-line${bad.has(l.id) ? ' class="qt-bad"' : ''}>`)).join('')}</tbody></table></div>
    ${ro ? '' : '<button class="btn btn-ghost btn-sm" type="button" id="qlAdd"><i data-lucide="plus"></i>Thêm dòng</button>'}
    <p class="muted-sub">Sửa cho khớp file gốc của NCC — không “sửa hộ” lỗi tính toán của NCC; lỗi đó cần NCC xác nhận lại. Lưu sẽ bỏ dấu “Đã kiểm tra”.</p>`;
  uiPanelDialog(`Dòng báo giá — ${esc(q.vendorLabel)}`, body, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Đóng</button>
    ${ro ? '' : '<button class="btn btn-primary btn-sm" type="button" id="qlSave">Lưu &amp; kiểm tra lại</button>'}`);
  $('#spDialog').classList.add('qt-wide');
  $('#spDialog').addEventListener('close', () => $('#spDialog').classList.remove('qt-wide'), { once: true });
  $('#spDialogBody').onchange = (e) => { if (e.target.matches('input.qt-money')) qtMoneyFix(e.target); };
  $('#spDialogBody').onclick = (e) => {
    if (e.target.closest('#qlAdd')) { $('#qlBody').insertAdjacentHTML('beforeend', qtLineRow()); refreshIcons(); }
    const del = e.target.closest('[data-ql-del]');
    if (del) del.closest('tr').remove();
  };
  spOnce('#qlSave', async () => {
    const payload = { ...Object.fromEntries(new FormData($('#qlForm')).entries()), rowVersion: q.rowVersion };
    payload.lines = $$('#qlBody [data-line]').map(tr => ({
      lineType: tr.querySelector('.ql-type').value, description: tr.querySelector('.ql-desc').value, model: tr.querySelector('.ql-model').value,
      unit: tr.querySelector('.ql-unit').value, qty: tr.querySelector('.ql-qty').value, unitPrice: tr.querySelector('.ql-price').value,
      amount: tr.querySelector('.ql-amount').value, vatPct: tr.querySelector('.ql-vat').value.trim(),
      vatAmount: tr.querySelector('.ql-vatamt').value, sets: tr.querySelector('.ql-sets').value.trim() })).filter(l => l.description.trim());
    try {
      await apiRequest(qtUrl(code, 'quotations', qid), { method: 'PUT', body: JSON.stringify(payload) });
      $('#spDialog').close();
      await qtOpen(code);
    } catch (err) { spMsg(err.message); }
  });
}


/* ---------- V3: AI differences, unit price history, follow-up ---------- */
function qtAiBlock(d) {
  const s = d.aiSummary;
  const can = spCan('editor') && d.quotations.length >= 2;
  const btn = can ? `<button class="btn btn-ghost btn-sm" type="button" data-qt-act="ai-summary"${d.aiEnabled ? '' : ' disabled title="AI chưa bật (Cài đặt → Tờ trình & AI)"'}><i data-lucide="sparkles"></i>${s ? 'Phân tích lại' : 'AI: tóm tắt khác biệt nội dung'}</button>` : '';
  if (!s) return can ? `<div class="qt-ai"><div class="sp-inline"><span class="muted-sub">AI đọc dòng hàng, model, số lượng và điều khoản của từng NCC để chỉ ra khác biệt về nội dung / cấu hình (giá do hệ thống tính).</span><span class="src-spacer"></span>${btn}</div></div>` : '';
  const list = (title, items) => items.length ? `<div class="sp-h">${title}</div><ul class="sp-cc">${items.map(x => `<li>${x}</li>`).join('')}</ul>` : '';
  return `<div class="qt-ai">
    <div class="sp-inline"><b>Khác biệt nội dung (AI)</b><span class="muted-sub sr-inline">${esc(s.by || '')} · ${esc(spTime(s.at))} — cần đối chiếu với báo giá gốc</span><span class="src-spacer"></span>${btn}</div>
    ${s.summary ? `<p class="sp-desc">${esc(s.summary)}</p>` : ''}
    ${list('Khác biệt', s.differences.map(x => `<b>${esc(x.topic)}</b>${x.vendors.length ? ` <span class="muted-sub sr-inline">${esc(x.vendors.join(', '))}</span>` : ''} — ${esc(x.detail)}`))}
    ${list('Rủi ro nếu chọn giá thấp nhất mà chưa làm rõ', s.risks.map(esc))}
    ${list('Câu hỏi gửi NCC', s.questions.map(esc))}
  </div>`;
}

async function qtOpenPrices() {
  uiPanelDialog('Tra cứu đơn giá theo mặt hàng', `
    <p class="panel-sub">Tìm theo model hoặc tên hàng trong các báo giá <b>đã kiểm tra</b>; đơn giá quy đổi VND, trước VAT.</p>
    <div class="sp-inline"><input type="text" id="qpQ" maxlength="80" placeholder="Vd: C9300-48T, Meraki, cáp Cat6A"><button class="btn btn-primary btn-sm" type="button" id="qpGo">Tìm</button></div>
    <div id="qpOut"></div>`, '<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Đóng</button>');
  $('#spDialog').classList.add('qt-wide');
  $('#spDialog').addEventListener('close', () => $('#spDialog').classList.remove('qt-wide'), { once: true });
  const go = async () => {
    try {
      const r = await apiRequest(`/spend/price-history?q=${encodeURIComponent($('#qpQ').value.trim())}`);
      const stats = r.byModel.filter(m => m.model !== '—').map(m => `<tr><td><b>${esc(m.model)}</b></td><td>${m.years.map(y => `${esc(y.year)}: ${esc(qtNum(y.min))} – <b>${esc(qtNum(y.median))}</b> – ${esc(qtNum(y.max))} <span class="muted-sub sr-inline">(${esc(y.count)} báo giá)</span>`).join('<br>')}</td></tr>`).join('');
      $('#qpOut').innerHTML = `${stats ? `<div class="sp-h">Theo model và năm (thấp nhất – trung vị – cao nhất)</div><div class="table-scroll"><table class="data-table"><tbody>${stats}</tbody></table></div>` : ''}
        <div class="sp-h">Chi tiết (${esc(r.items.length)}${r.truncated ? '+' : ''} dòng)</div>
        <div class="table-scroll"><table class="data-table"><thead><tr><th>Ngày</th><th>NCC</th><th>Gói</th><th>Hàng</th><th class="num">SL</th><th class="num">Đơn giá (VND)</th></tr></thead><tbody>
        ${r.items.map(i => `<tr><td>${esc(spDate(i.date))}</td><td>${esc(i.vendor)}${i.selected ? ' ' + uiPill('ok', 'chọn') : ''}</td><td>${esc(i.request)}<span class="muted-sub">${esc(i.requestTitle)}</span></td>
          <td class="wrap">${esc(i.description)}${i.model ? `<span class="muted-sub">${esc(i.model)}</span>` : ''}</td><td class="num">${esc(qtNum(i.qty, 3))} ${esc(i.unit || '')}</td><td class="num">${esc(qtNum(i.unitPriceVnd))}</td></tr>`).join('') || uiEmptyRow(6, 'Không có dòng nào.')}
        </tbody></table></div>`;
      spMsg('');
    } catch (err) { spMsg(err.message); }
  };
  spOnce('#qpGo', go);
  $('#qpQ').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
  $('#qpQ').focus();
}

function qtOpenToCapex(d) {
  const cats = (spState.meta.categories || []).filter(c => c.active);
  const sel = d.quotations.find(q => q.id === d.selectedQuotationId);
  const body = `<form id="qcForm" class="sp-form" novalidate><div class="src-grid">
      <label class="field sp-span2"><span>Tên dự án</span><input name="name" maxlength="255" value="${esc(d.title)}"></label>
      <label class="field"><span>Loại chi phí *</span><select name="categoryId">${spOpt('', '— Chọn —', '')}${cats.map(c => spOpt(c.id, c.name, '')).join('')}</select></label>
      <label class="field"><span>Hạng mục</span><select name="mode">${spOpt('total', `1 hạng mục tổng (${sel ? spMoney(sel.subtotalVnd) : ''})`, 'total')}${spOpt('lines', `Mỗi dòng báo giá một hạng mục (${sel ? sel.itemCount : 0} dòng)`, '')}</select></label>
      <label class="field"><span>Bảo hành / license (năm)</span><input name="coverageYears" type="number" min="0" max="10" value="0"></label>
      <label class="field"><span>Bắt đầu bảo hành</span><input name="startDate" type="date"></label>
    </div></form><p class="muted-sub">NCC: <b>${esc(sel ? sel.vendorLabel : '')}</b>, VAT theo báo giá. Có số năm bảo hành thì Capex tự sinh Opex gia hạn dự kiến.</p>`;
  uiPanelDialog('Tạo dự án Capex từ báo giá được chọn', body, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
    <button class="btn btn-primary btn-sm" type="button" id="qcSave">Tạo Capex</button>`);
  spOnce('#qcSave', async () => {
    const fd = Object.fromEntries(new FormData($('#qcForm')).entries());
    if (!fd.categoryId) { spMsg('Chọn loại chi phí.'); return; }
    if (!fd.startDate) delete fd.startDate;
    try { const r = await apiRequest(qtUrl(d.code, 'capex'), { method: 'POST', body: JSON.stringify(fd) }); $('#spDialog').close(); await qtOpen(d.code); qtMsg(`Đã tạo dự án ${r.capexCode}.`, true); }
    catch (err) { spMsg(err.message); }
  });
}

async function qtOpenToProposal(d) {
  let tpls = [];
  try { tpls = (await apiRequest('/spend/proposals/templates')).filter(t => t.active && t.kind === 'B'); } catch (err) { /* shown below */ }
  if (!tpls.length) { qtMsg('Chưa có mẫu tờ trình Mẫu B đang dùng — admin tải mẫu ở Cài đặt → Tờ trình & AI.'); return; }
  const sel = d.quotations.find(q => q.id === d.selectedQuotationId);
  uiPanelDialog('Soạn tờ trình Mẫu B từ gói báo giá', `<form id="qpForm" class="sp-form" novalidate><div class="src-grid">
      <label class="field sp-span2"><span>Về việc *</span><input name="subject" maxlength="255" value="${esc(`Đề xuất lựa chọn nhà cung cấp — ${d.title}`)}"></label>
      <label class="field"><span>Mẫu</span><select name="templateId">${tpls.map(t => spOpt(t.id, t.name, tpls[0].id)).join('')}</select></label>
      <label class="field sp-span2"><span>Bối cảnh cho AI</span><textarea name="contextNote" rows="3" placeholder="Tuỳ chọn" maxlength="8000">${esc(sel ? `Đề xuất chọn ${sel.vendorLabel}. ${d.selectionReason || ''}` : '')}</textarea></label>
    </div></form>`, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
    <button class="btn btn-primary btn-sm" type="button" id="qpSave">Tạo tờ trình</button>`);
  spOnce('#qpSave', async () => {
    const fd = Object.fromEntries(new FormData($('#qpForm')).entries());
    try {
      const p = await apiRequest('/spend/proposals', { method: 'POST', body: JSON.stringify({ ...fd, kind: 'B', templateId: Number(fd.templateId), quoteCode: d.code }) });
      $('#spDialog').close();
      location.hash = '#/spend-proposals';
      setTimeout(() => { if (typeof ppOpenDetail === 'function') ppOpenDetail(p.code); }, 400);
    } catch (err) { spMsg(err.message); }
  });
}
