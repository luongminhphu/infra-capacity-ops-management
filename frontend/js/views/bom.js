/* =========================================================
   Quản lý thông tin → Chuẩn hoá BOM.
   Upload BOM thô (Dell Solutions Configurator, Cisco CCW, HPE, Excel thường) → xem trước theo đầu mục
   (Máy chủ, Mạng, Firewall, Storage, SAN Switch, Load Balancer…) → sửa tên / đầu mục / thông số → Xuất Excel.
   Không lưu gì trên máy chủ: bản xem trước nằm ở trình duyệt và được gửi lại khi xuất.
   ========================================================= */

const bomState = { data: null, project: { name: '', customer: '', preparedBy: '', currency: 'USD', vatPct: '', note: '', hideEmpty: true }, busy: false };
const BOM_ROMAN = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X'];

function bomMoney(v, cur) { return uiMoney(v, cur === 'USD' ? 'USD' : 'VND'); }

function bomStatus(text, tone) { uiSetStatus('#bomMsg', text, tone || 'ok'); }

function renderBomView() {
  const mount = $('#bomMount');
  if (!mount) return;
  const p = bomState.project, d = bomState.data;
  const cur = p.currency;
  const field = (id, label, value, attrs = '') => `<label class="field"><span>${label}</span><input id="${id}" value="${esc(value)}" ${attrs}></label>`;
  let body = '';
  if (d) {
    const cats = d.categories.filter(c => d.configs.some(x => x.category === c.key));
    const total = d.configs.reduce((s, c) => s + Number(c.total || 0), 0);
    body = `
      <div class="kpi-strip-wrap">${uiKpiStrip([
        { label: 'Định dạng nhận ra', value: d.layoutLabel, sub: `${d.fileName} · sheet ${d.sheet} · tiêu đề dòng ${d.headerRow}` },
        { label: 'Cấu hình', value: String(d.configs.length), sub: `${cats.length} đầu mục` },
        { label: 'Tổng giá trị (theo file)', value: bomMoney(total, cur), sub: 'chưa VAT · tính lại bằng công thức trong Excel' },
      ])}</div>
      ${d.warnings.length ? `<div class="insight-box">${d.warnings.map(w => `<div>${uiPill('warn', 'Lưu ý')} ${esc(w)}</div>`).join('')}</div>` : ''}
      ${cats.map((c, ci) => `
        <section class="table-card bom-cat">
          <div class="panel-head"><div><div class="panel-title">${esc(BOM_ROMAN[ci] || ci + 1)}. ${esc(c.label)}</div></div>
            <div class="panel-toolbar"><b>${esc(bomMoney(d.configs.filter(x => x.category === c.key).reduce((s, x) => s + Number(x.total || 0), 0), cur))}</b></div></div>
          ${d.configs.map((x, i) => x.category === c.key ? bomConfigCard(x, i, d, cur) : '').join('')}
        </section>`).join('')}`;
  }
  mount.innerHTML = `
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title">Chuẩn hoá BOM</div>
        <p class="panel-sub">Tải file Excel BOM thô — Dell Solutions Configurator, Cisco CCW, HPE hoặc bảng kê thông thường. Hệ thống gom theo đầu mục, tóm tắt thông số mỗi cấu hình (CPU, RAM, ổ đĩa, cổng mạng, HBA, license…) và xuất file Excel trình bày chuẩn, có công thức tổng.</p></div></div>
      <div class="settings-form bom-form">
        <label class="dropzone sp-span2" id="bomDrop">
          <input type="file" id="bomFile" accept=".xlsx" class="sr-only">
          <i data-lucide="file-spreadsheet" aria-hidden="true"></i>
          <span class="dz-main">${d ? `<b>${esc(d.fileName)}</b> — chọn file khác` : '<b>Chọn file BOM .xlsx</b> hoặc kéo thả vào đây'}</span>
          <span class="dz-sub">Dell Solutions Configurator, Cisco CCW, HPE hoặc bảng kê thường. File chỉ đọc để xem trước, không lưu trên máy chủ.</span>
        </label>
        ${field('bomName', 'Tên dự án', p.name, 'maxlength="120"')}
        ${field('bomCustomer', 'Khách hàng', p.customer, 'maxlength="120"')}
        ${field('bomBy', 'Người lập', p.preparedBy || (currentUser && currentUser.username) || '', 'maxlength="80"')}
        <label class="field"><span>Tiền tệ</span><select id="bomCur"><option value="USD"${cur === 'USD' ? ' selected' : ''}>USD</option><option value="VND"${cur === 'VND' ? ' selected' : ''}>VND</option></select></label>
        ${field('bomVat', 'VAT % (để trống = không tính)', p.vatPct, 'type="number" min="0" max="20" step="0.5"')}
        <label class="field sp-span2"><span>Ghi chú đầu file</span><input id="bomNote" maxlength="1000" value="${esc(p.note)}" placeholder="Vd: Giá list, chưa gồm chiết khấu; hiệu lực 30 ngày"></label>
      </div>
      <label class="finding-filter-item"><input type="checkbox" id="bomHide" ${p.hideEmpty ? 'checked' : ''}><span>Ẩn các dòng "No …" / "NONE" / Factory Integrated giá 0 trong file xuất</span></label>
      <div class="sim-actions"><button class="btn btn-primary btn-sm" type="button" id="bomExport" ${d ? '' : 'disabled'}><i data-lucide="download"></i>Xuất Excel</button></div>
      <p class="criteria-status" id="bomMsg" role="status"></p>
    </section>
    ${body}`;
  refreshIcons();
  bomBind();
}

function bomConfigCard(x, i, d, cur) {
  const opts = d.categories.map(c => `<option value="${esc(c.key)}"${c.key === x.category ? ' selected' : ''}>${esc(c.label)}</option>`).join('');
  const shown = x.items.filter(it => !(bomState.project.hideEmpty && it.empty));
  return `<article class="bom-config">
    <div class="bom-config-head">
      <input class="bom-title" data-bom-title="${i}" value="${esc(x.title)}" maxlength="200" aria-label="Tên cấu hình">
      <label class="field bom-mini"><span>Đầu mục</span><select data-bom-cat="${i}">${opts}</select></label>
      <label class="field bom-mini"><span>Số lượng</span><input type="number" min="1" max="100000" data-bom-qty="${i}" value="${esc(x.qty)}"></label>
      ${x.priceBasis !== 'per_unit' && x.qty > 1 ? `<label class="field bom-mini"><span>SL linh kiện ghi theo</span><select data-bom-mode="${i}">
        <option value="per_unit"${x.qtyMode === 'per_unit' ? ' selected' : ''}>mỗi thiết bị</option><option value="total"${x.qtyMode === 'total' ? ' selected' : ''}>tổng tất cả thiết bị</option></select></label>` : ''}
      <div class="bom-total">${esc(bomMoney(x.total, cur))}</div>
    </div>
    <table class="data-table bom-summary"><tbody>
      ${x.summary.map((s, k) => `<tr><th scope="row">${esc(s[0])}</th><td><input data-bom-sum="${i}:${k}" value="${esc(s[1])}" maxlength="800" aria-label="${esc(s[0])}"></td></tr>`).join('')}
    </tbody></table>
    <details class="sp-history"><summary>${esc(shown.length)} dòng hàng${x.items.length > shown.length ? ` (ẩn ${x.items.length - shown.length} dòng giá 0)` : ''}</summary>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>P/N</th><th>Mô tả</th><th>Module</th><th class="num">SL</th><th class="num">Đơn giá</th><th class="num">Thành tiền</th></tr></thead><tbody>
      ${shown.map(it => `<tr${it.isModel ? ' class="bom-model"' : ''}><td>${esc(it.pn || '')}</td><td class="wrap">${esc(it.desc)}</td><td>${esc(it.module || '')}</td>
        <td class="num">${esc(it.qty ?? '')}</td><td class="num">${esc(bomMoney(it.price, cur))}</td><td class="num">${esc(bomMoney(it.total, cur))}</td></tr>`).join('')}
      </tbody></table></div></details>
  </article>`;
}

function bomReadProject() {
  const v = (id) => ($(id) ? $(id).value.trim() : '');
  Object.assign(bomState.project, { name: v('#bomName'), customer: v('#bomCustomer'), preparedBy: v('#bomBy'), currency: v('#bomCur') || 'USD',
    vatPct: v('#bomVat'), note: v('#bomNote'), hideEmpty: !!($('#bomHide') && $('#bomHide').checked) });
}

async function bomResummarise(i) {
  const x = bomState.data.configs[i];
  try { x.summary = (await apiRequest('/bom/summary', { method: 'POST', body: JSON.stringify(x) })).summary; }
  catch (err) { bomStatus(err.message, 'error'); }
  renderBomView();
}

function bomBind() {
  const d = bomState.data;
  const readFile = async (f) => {
    if (!f) return;
    if (!/\.xlsx$/i.test(f.name)) { bomStatus('Chỉ nhận file .xlsx.', 'error'); return; }
    bomReadProject();
    bomStatus('Đang đọc file…');
    const fd = new FormData(); fd.append('file', f);
    try {
      const r = await apiRequest('/bom/parse', { method: 'POST', body: fd });
      bomState.data = r;
      bomState.project.currency = r.currency || bomState.project.currency;
      if (!bomState.project.name && r.projectName) bomState.project.name = r.projectName;
      renderBomView();
      bomStatus(`Đã đọc ${r.configs.length} cấu hình (${r.layoutLabel}). Kiểm tra đầu mục và thông số rồi bấm Xuất Excel.`);
    } catch (err) { bomStatus(err.message, 'error'); }
  };
  $('#bomFile').onchange = (e) => readFile(e.target.files[0]);
  const zone = $('#bomDrop');
  ['dragenter', 'dragover'].forEach(t => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(t => zone.addEventListener(t, () => zone.classList.remove('over')));
  zone.addEventListener('drop', (e) => { e.preventDefault(); readFile(e.dataTransfer.files[0]); });
  ['#bomName', '#bomCustomer', '#bomBy', '#bomVat', '#bomNote'].forEach(id => { const el = $(id); if (el) el.onchange = bomReadProject; });
  $('#bomCur').onchange = () => { bomReadProject(); renderBomView(); };
  $('#bomHide').onchange = () => { bomReadProject(); renderBomView(); };
  if (!d) return;
  const mount = $('#bomMount');
  mount.querySelectorAll('[data-bom-title]').forEach(el => { el.onchange = () => { d.configs[+el.dataset.bomTitle].title = el.value.trim() || 'Cấu hình'; }; });
  mount.querySelectorAll('[data-bom-sum]').forEach(el => { el.onchange = () => { const [i, k] = el.dataset.bomSum.split(':').map(Number); d.configs[i].summary[k][1] = el.value; }; });
  mount.querySelectorAll('[data-bom-cat]').forEach(el => { el.onchange = () => { d.configs[+el.dataset.bomCat].category = el.value; bomResummarise(+el.dataset.bomCat); }; });
  mount.querySelectorAll('[data-bom-mode]').forEach(el => { el.onchange = () => { d.configs[+el.dataset.bomMode].qtyMode = el.value; bomResummarise(+el.dataset.bomMode); }; });
  mount.querySelectorAll('[data-bom-qty]').forEach(el => { el.onchange = () => {
    const x = d.configs[+el.dataset.bomQty];
    const n = Math.max(1, Math.min(100000, parseInt(el.value, 10) || 1));
    if (x.priceBasis === 'per_unit') x.total = (Number(x.total || 0) / Math.max(1, x.qty)) * n;
    x.qty = n;
    bomResummarise(+el.dataset.bomQty);
  }; });
  $('#bomExport').onclick = async (e) => {
    const b = e.currentTarget;
    bomReadProject();
    const vat = parseFloat(bomState.project.vatPct);
    b.disabled = true;
    try {
      await uiDownload(`${API_BASE}/bom/export`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project: { ...bomState.project, vatPct: isFinite(vat) && vat > 0 ? vat : null }, configs: d.configs }) }, 'BOM.xlsx');
      bomStatus('Đã xuất file Excel.');
    } catch (err) { bomStatus(err.message, 'error'); }
    finally { b.disabled = false; }
  };
}
