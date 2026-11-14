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
const qtNum = (v, d = 0) => (v === null || v === undefined || v === '') ? '—' : Number(v).toLocaleString('vi-VN', { maximumFractionDigits: d });
const qtRaw = (v) => (v === null || v === undefined) ? '' : String(v);
const qtUrl = (code, ...parts) => ['/spend/quotes', encodeURIComponent(code), ...parts.map(p => encodeURIComponent(p))].join('/');

async function qtDownload(path, fallback, btn) {
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(`/api${path}`, { credentials: 'include' });
    if (!res.ok) {
      let msg = `Lỗi tải file (${res.status})`;
      try { msg = (await res.json()).detail || msg; } catch (err) { /* not JSON */ }
      throw new Error(msg);
    }
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = (/filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') || '') || [])[1] || fallback;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  } catch (err) { alert(err.message); } finally { if (btn) btn.disabled = false; }
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
      <td class="wrap">${v.tags.map(t => `<span class="pill na">${esc(t)}</span>`).join(' ') || '—'}</td>
      <td>${spPill(...(VD_STATUS[v.status] || ['na', v.statusLabel]))}</td>
      <td class="num">${esc(v.quotes)}<span class="muted-sub">chọn ${esc(v.won)}</span></td>
      <td class="num">${esc(v.contracts)}</td>
      <td class="num">${spMoney(v.opexTotal + v.capexTotal)}</td></tr>`).join('');
  $('#spendMount').innerHTML = `
    <section class="table-card">
      <div class="panel-head">
        <div><div class="panel-title">Nhà cung cấp</div>
          <p class="panel-sub">Tên viết tắt dùng thống nhất trong bảng so sánh, phiếu đánh giá và tờ trình. Tên gọi khác giúp nhận đúng NCC khi tên trên báo giá viết khác.</p></div>
      </div>
      ${noShort ? `<p class="criteria-status">${esc(noShort)} nhà cung cấp chưa có tên viết tắt.</p>` : ''}
      <div class="sp-filters"><label class="field sp-search"><span>Tìm</span><input type="text" data-vd-q value="${esc(vdState.q)}" placeholder="Tên, tên viết tắt, lĩnh vực"></label></div>
      ${vdState.error ? `<p class="criteria-status error">${esc(vdState.error)}</p>` : ''}
      <div class="table-scroll"><table class="data-table sp-table">
        <thead><tr><th>Tên viết tắt</th><th>Tên đầy đủ</th><th>Lĩnh vực</th><th>Trạng thái</th><th class="num">Báo giá</th><th class="num">Hợp đồng</th><th class="num">Opex + Capex</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="7" class="empty-note">Chưa có nhà cung cấp — NCC được tạo khi nhập Opex, Capex hoặc upload báo giá.</td></tr>'}</tbody>
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
  try { v = await apiRequest(`/spend/vendor-profiles/${encodeURIComponent(id)}`); } catch (err) { alert(err.message); return; }
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
    ${hist('Báo giá', v.quotes.map(q => `<li><b>${esc(q.code)}</b> — ${esc(q.title)} · ${spMoney(q.total)}${q.won ? ' · <span class="pill ok">Được chọn</span>' : ''}${q.evalTotal !== null ? ` · điểm ${esc(qtNum(q.evalTotal, 2))} (${esc(q.evalClass)})` : ''}</li>`), 'Chưa có báo giá.')}
    ${hist('Hợp đồng', v.contracts.map(c => `<li><b>${esc(c.code)}</b> — ${esc(c.title)}</li>`), 'Chưa có hợp đồng.')}
    ${hist('Opex', v.opex.map(o => `<li><b>${esc(o.code)}</b> — ${esc(o.title)} · NTC ${esc(o.fiscalYear)} · ${spMoney(o.amountGross)}</li>`), 'Chưa có khoản Opex.')}
    ${hist('Hạng mục Capex', v.capexItems.map(i => `<li><b>${esc(i.projectCode)}</b> — ${esc(i.description)} · ${spMoney(i.amountGross)}</li>`), 'Chưa có hạng mục Capex.')}`;
  spDialog(`Nhà cung cấp — ${esc(v.label)}`, body, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Đóng</button>
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

function qtRenderList() {
  const rows = (qtState.list || []).map(r => `<tr data-qt-code="${esc(r.code)}" class="sp-row" tabindex="0">
      <td><b>${esc(r.code)}</b></td><td class="wrap">${esc(r.title)}</td><td>${esc(r.workstreamLabel)}</td>
      <td>${spPill(...(QT_STATUS[r.status] || ['na', r.statusLabel]))}</td>
      <td class="num">${esc(r.quotations)}/${esc(r.minQuotes)}</td>
      <td class="wrap">${esc(r.selectedVendor || '—')}${r.selectedTotal ? `<span class="muted-sub">${spMoney(r.selectedTotal)}</span>` : ''}</td>
      <td>${esc(spTime(r.updatedAt))}</td></tr>`).join('');
  $('#spendMount').innerHTML = `
    <section class="table-card">
      <div class="panel-head">
        <div><div class="panel-title">So sánh báo giá</div>
          <p class="panel-sub">Mỗi gói nhận báo giá của nhiều NCC (mặc định 3). Hệ thống đọc file, kiểm tra công thức tiền, so sánh từng hạng mục và lập phiếu đánh giá NCC.</p></div>
        <div class="panel-toolbar">${spCan('editor') ? '<button class="btn btn-primary btn-sm" type="button" data-qt-new><i data-lucide="plus"></i>Tạo gói báo giá</button>' : ''}</div>
      </div>
      ${qtState.error ? `<p class="criteria-status error">${esc(qtState.error)}</p>` : ''}
      <div class="table-scroll"><table class="data-table sp-table">
        <thead><tr><th>Mã</th><th>Gói</th><th>Khối</th><th>Trạng thái</th><th class="num">Báo giá</th><th>NCC được chọn</th><th>Cập nhật</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="7" class="empty-note">Chưa có gói báo giá.</td></tr>'}</tbody>
      </table></div>
    </section>`;
  refreshIcons();
}

function qtBind() {
  if (qtState.bound) return;
  qtState.bound = true;
  const mount = $('#spendMount');
  mount.addEventListener('click', (e) => {
    if (spState.sub !== 'quotes') return;
    const row = e.target.closest('[data-qt-code]');
    if (row) return qtOpen(row.dataset.qtCode);
    if (e.target.closest('[data-qt-new]')) return qtOpenForm();
    if (e.target.closest('[data-qt-back]')) { qtState.current = null; return renderQuotesPage(); }
    const tab = e.target.closest('[data-qt-tab]');
    if (tab) { qtState.tab = tab.dataset.qtTab; return qtRenderDetail(); }
    const act = e.target.closest('[data-qt-act]');
    if (act) return qtAction(act.dataset.qtAct, act.dataset.qid, act);
  });
  mount.addEventListener('keydown', (e) => {
    const row = spState.sub === 'quotes' && e.target.closest('[data-qt-code]');
    if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); qtOpen(row.dataset.qtCode); }
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
  spDialog(r ? `Sửa gói ${esc(r.code)}` : 'Tạo gói báo giá', body, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
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
  } catch (err) { alert(err.message); qtState.current = null; return; }
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
          <div class="panel-title">${esc(d.code)} — ${esc(d.title)}</div>
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
        <div class="sp-dl-item"><dt>Tổng NCC ghi</dt><dd>${spMoney(q.totalVnd)}${q.vatPct !== null ? ` <span class="muted-sub sr-inline">VAT ${esc(qtNum(q.vatPct, 2))}%</span>` : ''}</dd></div>
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
      <div class="sp-inline"><input type="text" id="qtVendor" list="qtVendorList" maxlength="255" placeholder="Nhà cung cấp (tên, tên viết tắt hoặc tên khác)">
        <input type="file" id="qtFile" accept=".xlsx,.docx,.pdf">
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
  return `<div class="sp-inline qt-bar"><span class="muted-sub">${esc(c.commonRows)}/${esc(c.rows.length)} hạng mục có ở mọi báo giá. Ô xanh: thấp nhất trong hàng.</span>
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
  const crit = (sec) => ev.criteria.filter(c => c.section === sec).map(c => `<tr data-crit data-sec="${sec}">
      <td><input type="text" class="qt-crit-name" value="${esc(c.name)}" maxlength="255"${ro}></td>
      <td><input type="number" class="qt-w" min="1" max="70" step="0.5" value="${esc(Math.round(c.weight * 1000) / 10)}"${ro}></td>
      ${cols.map(x => `<td><input type="number" class="qt-score" min="1" max="10" step="0.05" data-q="${esc(x.quotationId)}" value="${esc(qtRaw(c.scores[String(x.quotationId)]))}"${ro}></td>`).join('')}
      ${ro ? '' : '<td><button class="btn btn-ghost btn-sm" type="button" data-qt-act="crit-del" aria-label="Bỏ tiêu chí"><i data-lucide="x"></i></button></td>'}</tr>`).join('');
  const sec = (s, label) => `<tr class="qt-sec"><td colspan="${2 + cols.length + (ro ? 0 : 1)}"><b>${label}</b>${ro || s === 'A' ? '' : ' <button class="btn btn-ghost btn-sm" type="button" data-qt-act="crit-add">+ tiêu chí</button>'}</td></tr>`;
  const hint = Object.keys(ev.priceScoreHint).length ? `Gợi ý điểm Giá thành (rẻ nhất 10, mỗi 5% cao hơn trừ 1): ${cols.map(x => `${esc(x.label)} ${esc(qtNum(ev.priceScoreHint[String(x.quotationId)], 1))}`).join(' · ')}` : '';
  return `<form id="qtEval" class="sp-form" novalidate><div class="src-grid">
      <label class="field"><span>Bộ phận đánh giá</span><input name="department" maxlength="64" value="${esc(ev.department || '')}"${ro}></label>
      <label class="field"><span>Ngày đánh giá</span><input name="date" type="date" value="${esc(ev.date || '')}"${ro}></label>
      <label class="field sp-span2"><span>Loại hình sản phẩm, dịch vụ</span><input name="service" maxlength="255" value="${esc(ev.service || '')}"${ro}></label>
      <label class="field sp-span2"><span>Kết luận</span><input name="conclusion" maxlength="255" value="${esc(ev.conclusion || '')}" placeholder="Để trống: điền “Đề xuất chọn …” khi chọn NCC"${ro}></label>
      <label class="field"><span>Người thực hiện đánh giá</span><input name="evaluator" maxlength="128" value="${esc(ev.evaluator || '')}"${ro}></label>
      <label class="field"><span>Trưởng Bộ phận phê duyệt</span><input name="approver" maxlength="128" value="${esc(ev.approver || '')}"${ro}></label>
    </div></form>
    <div class="table-scroll"><table class="data-table qt-eval">
      <thead><tr><th>Tiêu chí đánh giá</th><th class="num">Tỷ trọng (%)</th>${cols.map(x => `<th class="num">${esc(x.label)}</th>`).join('')}${ro ? '' : '<th></th>'}</tr></thead>
      <tbody id="qtCrit">${sec('A', 'A. Khung đánh giá tiêu chuẩn — 70%')}${crit('A')}${sec('B', 'B. Khung đánh giá mở rộng — 30% (2–4 tiêu chí)')}${crit('B')}</tbody>
      <tfoot><tr class="qt-foot"><td><b>Total</b></td><td></td>${cols.map(x => `<td class="num"><b>${esc(qtNum(ev.totals[String(x.quotationId)], 2))}</b></td>`).join('')}${ro ? '' : '<td></td>'}</tr>
        <tr class="qt-foot"><td><b>Loại đánh giá</b></td><td></td>${cols.map(x => `<td class="num"><b>${esc(ev.classes[String(x.quotationId)] || '—')}</b></td>`).join('')}${ro ? '' : '<td></td>'}</tr></tfoot>
    </table></div>
    <p class="muted-sub">Thang điểm 1–10. Xếp loại: ≥ 8 A · ≥ 7 B · ≥ 6 C · dưới 6 Không đạt. ${hint}</p>
    <div class="sp-inline"><span class="src-spacer"></span>
      <button class="btn btn-ghost btn-sm" type="button" data-qt-act="xlsx-eval"><i data-lucide="file-spreadsheet"></i>Xuất phiếu đánh giá (Excel)</button>
      ${ro ? '' : '<button class="btn btn-primary btn-sm" type="button" data-qt-act="eval-save">Lưu đánh giá</button>'}</div>
    <p class="src-result" id="qtMsg" role="status"></p>`;
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
      ${spCan('approver') ? '<button class="btn btn-ghost btn-sm" type="button" data-qt-act="reopen"><i data-lucide="rotate-ccw"></i>Mở lại để sửa</button>' : ''}
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
  if (!el) { if (text && !ok) alert(text); return; }
  el.textContent = text || '';
  el.className = 'src-result' + (text ? (ok ? ' ok' : ' err') : '');
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
    if (!confirm(`Xoá gói ${code} và mọi báo giá, file đính kèm?`)) return;
    try { await apiRequest(qtUrl(code), { method: 'DELETE' }); qtState.current = null; renderQuotesPage(); } catch (err) { alert(err.message); }
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
    if (!confirm('Xoá báo giá này và file đính kèm?')) return;
    return run(() => apiRequest(qtUrl(code, 'quotations', qid), { method: 'DELETE' }));
  }
  if (act === 'lines') return qtOpenLines(qid);
  if (act === 'xlsx-compare') return qtDownload(qtUrl(code, 'comparison.xlsx'), `so-sanh-bao-gia-${code}.xlsx`, btn);
  if (act === 'xlsx-eval') return qtDownload(qtUrl(code, 'evaluation.xlsx'), `evaluation-form-${code}.xlsx`, btn);
  if (act === 'crit-add') {
    const rows = $$('#qtCrit [data-crit][data-sec="B"]');
    if (rows.length >= 4) { qtMsg('Khung mở rộng tối đa 04 tiêu chí.'); return; }
    const tpl = rows[rows.length - 1].cloneNode(true);
    tpl.querySelectorAll('input').forEach(i => { i.value = i.classList.contains('qt-w') ? '0' : ''; });
    rows[rows.length - 1].after(tpl);
    return;
  }
  if (act === 'crit-del') { btn.closest('tr').remove(); return; }
  if (act === 'eval-save') return run(() => apiRequest(qtUrl(code, 'evaluation'), { method: 'PUT', body: JSON.stringify(qtReadEval()) }), 'Đã lưu đánh giá.');
  if (act === 'select') {
    const pick = $('input[name="qtPick"]:checked');
    if (!pick) { qtMsg('Chọn một báo giá đã kiểm tra.'); return; }
    return run(() => apiRequest(qtUrl(code, 'select'), { method: 'POST', body: JSON.stringify({ quotationId: Number(pick.value), reason: $('#qtReason').value }) }), 'Đã chọn nhà cung cấp.');
  }
  if (act === 'reopen') {
    if (!confirm('Mở lại gói để sửa? Lựa chọn NCC hiện tại sẽ bị huỷ.')) return;
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
    <td><input class="ql-price num" inputmode="decimal" value="${esc(qtRaw(l.unitPrice))}"${ro}></td>
    <td><input class="ql-amount num" inputmode="decimal" value="${esc(qtRaw(l.amount))}"${ro}></td>
    ${ro ? '' : '<td><button class="btn btn-ghost btn-sm" type="button" data-ql-del aria-label="Xoá dòng"><i data-lucide="x"></i></button></td>'}</tr>`;
}

async function qtOpenLines(qid) {
  const code = qtState.current.code;
  let q;
  try { q = await apiRequest(qtUrl(code, 'quotations', qid)); } catch (err) { alert(err.message); return; }
  const ro = !(spCan('editor') && !qtLocked()) ? ' disabled' : '';
  const bad = new Set(q.findings.filter(f => f.lineId).map(f => f.lineId));
  const f = (name, label, val, attrs = '') => `<label class="field"><span>${label}</span><input name="${name}" value="${esc(qtRaw(val))}"${attrs}${ro}></label>`;
  const body = `<form id="qlForm" class="sp-form" novalidate><div class="src-grid qt-head-grid">
      ${f('quoteNo', 'Số báo giá', q.quoteNo, ' maxlength="64"')}${f('quoteDate', 'Ngày báo giá', q.quoteDate, ' type="date"')}
      ${f('validUntil', 'Hiệu lực đến', q.validUntil, ' type="date"')}${f('currency', 'Tiền tệ', q.currency, ' maxlength="3"')}
      ${f('fxRate', 'Tỷ giá (nếu ngoại tệ)', q.fxRate, ' inputmode="decimal"')}${f('vatPct', 'VAT (%)', q.vatPct, ' inputmode="decimal"')}
      ${f('statedSubtotal', 'Trước VAT — NCC ghi', q.statedSubtotal, ' inputmode="decimal"')}${f('statedDiscount', 'Chiết khấu — NCC ghi', q.statedDiscount, ' inputmode="decimal"')}
      ${f('statedVat', 'VAT — NCC ghi', q.statedVat, ' inputmode="decimal"')}${f('statedTotal', 'Tổng — NCC ghi', q.statedTotal, ' inputmode="decimal"')}
      ${f('deliveryTerms', 'Giao hàng', q.deliveryTerms, ' maxlength="500"')}${f('warrantyTerms', 'Bảo hành', q.warrantyTerms, ' maxlength="500"')}
      ${f('paymentTerms', 'Thanh toán', q.paymentTerms, ' maxlength="500"')}
    </div></form>
    ${q.findings.length ? `<ul class="qt-findings">${q.findings.map(x => `<li class="${x.severity === 'crit' ? 'crit' : 'warn'}"><b>${esc(x.code)}</b> ${esc(x.message)}</li>`).join('')}</ul>` : ''}
    <div class="table-scroll"><table class="data-table qt-lines"><thead><tr><th>Loại</th><th>Mô tả</th><th>Model</th><th>ĐVT</th><th class="num">SL</th><th class="num">Đơn giá</th><th class="num">Thành tiền</th>${ro ? '' : '<th></th>'}</tr></thead>
      <tbody id="qlBody">${(q.lines || []).map(l => qtLineRow(l, ro).replace('<tr data-line>', `<tr data-line${bad.has(l.id) ? ' class="qt-bad"' : ''}>`)).join('')}</tbody></table></div>
    ${ro ? '' : '<button class="btn btn-ghost btn-sm" type="button" id="qlAdd"><i data-lucide="plus"></i>Thêm dòng</button>'}
    <p class="muted-sub">Sửa cho khớp file gốc của NCC — không “sửa hộ” lỗi tính toán của NCC; lỗi đó cần NCC xác nhận lại. Lưu sẽ bỏ dấu “Đã kiểm tra”.</p>`;
  spDialog(`Dòng báo giá — ${esc(q.vendorLabel)}`, body, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Đóng</button>
    ${ro ? '' : '<button class="btn btn-primary btn-sm" type="button" id="qlSave">Lưu &amp; kiểm tra lại</button>'}`);
  $('#spDialog').classList.add('qt-wide');
  $('#spDialog').addEventListener('close', () => $('#spDialog').classList.remove('qt-wide'), { once: true });
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
      amount: tr.querySelector('.ql-amount').value })).filter(l => l.description.trim());
    try {
      await apiRequest(qtUrl(code, 'quotations', qid), { method: 'PUT', body: JSON.stringify(payload) });
      $('#spDialog').close();
      await qtOpen(code);
    } catch (err) { spMsg(err.message); }
  });
}
