/* =========================================================
   Ngân sách & chi phí — Nhập Excel cho Opex / Capex (backend: spend/importer.py).
   Step 1: choose the file (or download the template) → "Kiểm tra" (nothing is written).
   Step 2: review every row (tạo / bỏ qua vì trùng / lỗi) → "Nhập". The file is sent again on
   commit and checked again by the server, so what is written is what was shown.
   ========================================================= */

const spImp = { kind: null, file: null, preview: null };
const SP_IMP_ACTION = { create: ['ok', 'Tạo mới'], skip: ['na', 'Bỏ qua'], error: ['crit', 'Lỗi'] };
const SP_IMP_STATUS = { planned: 'Dự kiến', active: 'Đang hiệu lực', renewed: 'Đã gia hạn', stopped: 'Ngừng',
  draft: 'Nháp', approved: 'Đã duyệt', in_progress: 'Đang triển khai', closed: 'Đã đóng' };

function spImpOpen(kind) {
  spImp.kind = kind; spImp.file = null; spImp.preview = null;
  const label = kind === 'opex' ? 'Opex' : 'Capex';
  const dlg = uiDialog('spImportDlg', { title: `Nhập ${label} từ Excel`, className: 'sp-import-dlg' });
  dlg.body.innerHTML = `
    <p class="panel-sub">${kind === 'opex'
      ? 'Dùng file mẫu hoặc chính file Excel đang theo dõi: chỉ cần một dòng tiêu đề có các cột Loại chi phí, Nội dung dịch vụ, Nhà cung cấp, Ngày bắt đầu, Số tiền (tên cột tiếng Việt hoặc tiếng Anh, có thể có dòng tiêu đề bảng phía trên). Các kỳ nối tiếp của cùng dịch vụ được tự liên kết “Đã gia hạn”.'
      : 'Mỗi dòng là một hạng mục; các dòng cùng Tên dự án (+ Năm tài chính) gộp thành một dự án, ô tên dự án để trống = dự án của dòng trên. Dự án đã duyệt sẽ tự sinh Opex gia hạn cho hạng mục có số năm bảo hành.'}
      Dòng đã có trong hệ thống được bỏ qua, nên nhập lại cùng một file không tạo trùng.</p>
    <div class="sim-actions">
      <button class="btn btn-secondary btn-sm" type="button" data-imp-template><i data-lucide="download"></i>Tải file mẫu</button>
      <label class="btn btn-secondary btn-sm"><i data-lucide="upload"></i><span data-imp-name>Chọn file .xlsx</span>
        <input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" data-imp-file hidden></label>
      <button class="btn btn-primary btn-sm" type="button" data-imp-check disabled>Kiểm tra</button>
    </div>
    <p class="criteria-status" id="spImpMsg" role="status"></p>
    <div data-imp-result></div>`;
  refreshIcons();
  const body = dlg.body;
  body.querySelector('[data-imp-template]').onclick = async () => {
    try { await uiDownload(`/api/spend/import/template.xlsx?kind=${kind}`, {}, `mau-nhap-${kind}.xlsx`); }
    catch (err) { uiSetStatus('#spImpMsg', err.message, 'error'); }
  };
  body.querySelector('[data-imp-file]').onchange = (e) => {
    spImp.file = e.target.files[0] || null; spImp.preview = null;
    body.querySelector('[data-imp-name]').textContent = spImp.file ? spImp.file.name : 'Chọn file .xlsx';
    body.querySelector('[data-imp-check]').disabled = !spImp.file;
    body.querySelector('[data-imp-result]').innerHTML = '';
    uiSetStatus('#spImpMsg', '', '');
  };
  body.querySelector('[data-imp-check]').onclick = async (e) => {
    const b = e.currentTarget; b.disabled = true;
    uiSetStatus('#spImpMsg', 'Đang đọc file…', '');
    try {
      const fd = new FormData(); fd.append('file', spImp.file);
      spImp.preview = await apiRequest(`/spend/import/preview?kind=${kind}`, { method: 'POST', body: fd });
      uiSetStatus('#spImpMsg', '', '');
      spImpRender(body);
    } catch (err) { uiSetStatus('#spImpMsg', err.message, 'error'); }
    finally { b.disabled = false; }
  };
  dlg.open();
}

function spImpIssues(r) {
  const parts = [...(r.errors || []).map(t => `<span class="tone-crit">${esc(t)}</span>`), ...(r.warnings || []).map(t => `<span class="tone-warn">${esc(t)}</span>`)];
  if (r.skipReason) parts.unshift(esc(r.skipReason));
  return parts.join('<br>');
}

function spImpRender(body) {
  const pv = spImp.preview, s = pv.summary, kind = spImp.kind;
  const head = `<div class="sp-imp-sum">
      <span>Sheet “${esc(pv.sheet)}”, tiêu đề dòng ${esc(pv.headerRow)} · nhận ${esc(Object.keys(pv.columns).length)} cột</span>
      ${spPill('ok', `${s.create} ${kind === 'opex' ? 'dòng' : 'dự án'} tạo mới`)} ${s.skip ? spPill('na', `${s.skip} bỏ qua`) : ''} ${s.error ? spPill('crit', `${s.error} lỗi`) : ''}
      ${kind === 'opex' ? `${s.renewedLinks ? spPill('ok', `${s.renewedLinks} liên kết gia hạn`) : ''} · tổng sau VAT ${spMoney(s.amountGross)}` : ` · ${esc(s.items)} hạng mục${s.orphans ? ` · ${esc(s.orphans)} dòng không có tên dự án` : ''}`}
    </div>`;
  let table;
  if (kind === 'opex') {
    table = `<table class="data-table"><thead><tr><th>Dòng</th><th></th><th>Nội dung</th><th>Nhà cung cấp</th><th>Thời hạn</th><th class="num">Trước VAT</th><th>Trạng thái</th><th>Ghi chú</th></tr></thead><tbody>
      ${pv.rows.map(r => `<tr><td class="num">${esc(r.row)}</td><td>${spPill(...SP_IMP_ACTION[r.action])}</td>
        <td class="wrap">${esc(r.title || '—')}<span class="muted-sub">${esc(r.categoryName || '')}</span></td><td class="wrap">${esc(r.vendorName || '—')}</td>
        <td>${spDate(r.startDate)} – ${spDate(r.endDate)}</td><td class="num">${spMoney(r.amountNet)}</td>
        <td>${esc(SP_IMP_STATUS[r.status] || '—')}${r.predecessorRow ? `<span class="muted-sub">tiếp nối dòng ${esc(r.predecessorRow)}</span>` : ''}</td>
        <td class="wrap">${spImpIssues(r)}</td></tr>`).join('')}</tbody></table>`;
  } else {
    table = `<table class="data-table"><thead><tr><th></th><th>Dự án</th><th>FY</th><th>Trạng thái</th><th class="num">Hạng mục</th><th class="num">Trước VAT</th><th>Ghi chú</th></tr></thead><tbody>
      ${pv.projects.map(p => `<tr><td>${spPill(...SP_IMP_ACTION[p.action])}</td><td class="wrap">${esc(p.name)}<span class="muted-sub">dòng ${esc(p.rows.map(r => r.row).join(', '))}</span></td>
        <td>${esc(p.fiscalYear || '—')}</td><td>${esc(SP_IMP_STATUS[p.status] || '—')}</td><td class="num">${esc(p.rows.length)}</td><td class="num">${spMoney(p.amountNet)}</td>
        <td class="wrap">${spImpIssues(p)}${p.rows.filter(r => r.errors.length).map(r => `<br><span class="tone-crit">Dòng ${esc(r.row)}: ${esc(r.errors.join('; '))}</span>`).join('')}</td></tr>`).join('')}</tbody></table>`;
  }
  const n = s.create;
  body.querySelector('[data-imp-result]').innerHTML = `${head}<div class="table-scroll sp-imp-table">${table}</div>
    <div class="sim-actions">
      ${s.error ? `<label class="finding-filter-item"><input type="checkbox" data-imp-skip><span>Chỉ nhập ${n} ${kind === 'opex' ? 'dòng' : 'dự án'} hợp lệ, bỏ qua dòng lỗi</span></label>` : ''}
      <span class="src-spacer"></span>
      <button class="btn btn-primary btn-sm" type="button" data-imp-commit ${n && !s.error ? '' : 'disabled'}>Nhập ${n} ${kind === 'opex' ? 'khoản Opex' : 'dự án Capex'}</button>
    </div>`;
  const skip = body.querySelector('[data-imp-skip]'), go = body.querySelector('[data-imp-commit]');
  if (skip) skip.onchange = () => { go.disabled = !(skip.checked && n); };
  go.onclick = async () => {
    go.disabled = true;
    uiSetStatus('#spImpMsg', 'Đang nhập…', '');
    try {
      const fd = new FormData(); fd.append('file', spImp.file);
      const r = await apiRequest(`/spend/import/commit?kind=${kind}&skipErrors=${skip && skip.checked ? 'true' : 'false'}`, { method: 'POST', body: fd });
      body.querySelector('[data-imp-result]').innerHTML = `<div class="insight-box"><strong>Đã nhập ${esc(r.created.length)} ${kind === 'opex' ? 'khoản Opex' : 'dự án Capex'}.</strong>
        ${r.created.length ? `<span class="muted-sub">${esc(r.created.slice(0, 30).join(', '))}${r.created.length > 30 ? '…' : ''}</span>` : ''}</div>`;
      uiSetStatus('#spImpMsg', '', '');
      spImpReload();
    } catch (err) { uiSetStatus('#spImpMsg', err.message, 'error'); go.disabled = false; }
  };
}

async function spImpReload() {
  try {
    await spLoadMeta(true);              // new vendors
    if (spState.sub === 'capex' && typeof renderCapexPage === 'function') await renderCapexPage();
    else if (spState.sub === 'opex') { await spLoadList(); spRenderPage(); }
  } catch (err) { /* the list refreshes on the next visit */ }
}

document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-sp-import]');
  if (b) { e.preventDefault(); spImpOpen(b.dataset.spImport); }
});
