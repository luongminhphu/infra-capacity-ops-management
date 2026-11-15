/* =========================================================
   Chi phí & Hợp đồng IT — Tờ trình (Mẫu A / B) và cấu hình AI, mẫu, người ký.
   The document is built by the backend from the uploaded template
   (spend/proposal_doc.py); AI writing is optional (spend/ai.py).
   ========================================================= */

const ppState = { list: null, bound: false, error: null, ai: null, templates: null };
const PP_STATUS = { draft: ['na', 'Nháp'], submitted: ['warn', 'Đã trình'], approved: ['ok', 'Đã duyệt'], rejected: ['crit', 'Không duyệt'] };
const PP_KIND = { A: 'Mẫu A — trình Tổng Giám Đốc', B: 'Mẫu B — trình Ban Mua sắm / COO' };
const PP_SECTION_KIND = { editable: ['ok', 'Soạn nội dung'], locked: ['na', 'Giữ nguyên'], opinion: ['na', 'Để trống'], approval: ['warn', 'Bảng ký'], title: ['na', 'Tiêu đề phụ lục'] };

async function ppLoadCommon() {
  try { [ppState.ai, ppState.templates] = await Promise.all([apiRequest('/spend/proposals/ai'), apiRequest('/spend/proposals/templates')]); }
  catch (err) { ppState.ai = ppState.ai || { enabled: false }; ppState.templates = ppState.templates || []; }
}

async function renderProposalsPage() {
  try { ppState.list = await apiRequest('/spend/proposals'); ppState.error = null; }
  catch (err) { ppState.error = err.message; ppState.list = []; }
  await ppLoadCommon();
  const rows = ppState.list.map(p => `<tr data-pp-code="${esc(p.code)}" class="sp-row" tabindex="0">
      <td><b>${esc(p.code)}</b><span class="muted-sub">Mẫu ${esc(p.kind)}</span></td>
      <td class="wrap">${esc(p.subject)}</td>
      <td>${p.capexCode ? `Capex ${esc(p.capexCode)}` : (p.opexCount ? `${esc(p.opexCount)} khoản Opex` : '')}${p.quoteCode ? `${p.capexCode || p.opexCount ? ' · ' : ''}So sánh ${esc(p.quoteCode)}` : ''}</td>
      <td>${spPill(...(PP_STATUS[p.status] || ['na', p.statusLabel]))}</td>
      <td>${p.hasFile ? (p.aiProvider === 'anthropic' ? 'AI soạn' : 'Chỉ điền dữ liệu') : '<span class="muted-sub">Chưa soạn</span>'}</td>
      <td>${esc(spTime(p.updatedAt))}<span class="muted-sub">${esc(p.updatedBy || '')}</span></td></tr>`).join('');
  const noTpl = !ppState.templates.some(t => t.active);
  $('#spendMount').innerHTML = `
    <section class="table-card">
      <div class="panel-head">
        <div><div class="panel-title">Tờ trình</div><p class="panel-sub">Soạn tờ trình từ mẫu .docx của công ty cho một dự án Capex hoặc một / nhiều khoản Opex. ${ppState.ai.enabled ? 'AI đang bật: nội dung từng mục được soạn sẵn để bạn chỉnh tiếp.' : 'AI đang tắt: file chỉ điền các trường dữ liệu và bảng chi phí.'}</p></div>
        <div class="panel-toolbar">${spCan('editor') ? `<button class="btn btn-primary btn-sm" type="button" data-pp-new ${noTpl ? 'disabled title="Chưa có mẫu — admin tải mẫu ở Cài đặt"' : ''}><i data-lucide="plus"></i>Soạn tờ trình</button>` : ''}</div>
      </div>
      ${noTpl ? '<div class="insight-box">Chưa có mẫu tờ trình. Quản trị viên tải Mẫu A / Mẫu B (.docx) ở Cài đặt → Chi phí &amp; nhắc hạn → Tờ trình &amp; AI.</div>' : ''}
      ${ppState.error ? `<p class="criteria-status error">${esc(ppState.error)}</p>` : ''}
      <div class="table-scroll"><table class="data-table sp-table">
        <thead><tr><th>Mã</th><th>Về việc</th><th>Gắn với</th><th>Trạng thái</th><th>File</th><th>Cập nhật</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="6" class="empty-note">Chưa có tờ trình.</td></tr>'}</tbody>
      </table></div>
    </section>`;
  refreshIcons();
  if (!ppState.bound) {
    ppState.bound = true;
    const mount = $('#spendMount');
    mount.addEventListener('click', (e) => {
      if (!(appState.view === 'spend' && spState.sub === 'proposals')) return;
      const row = e.target.closest('[data-pp-code]');
      if (row) return ppOpenDetail(row.dataset.ppCode);
      if (e.target.closest('[data-pp-new]')) return ppOpenCreate();
    });
    mount.addEventListener('keydown', (e) => {
      const row = e.target.closest('[data-pp-code]');
      if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); ppOpenDetail(row.dataset.ppCode); }
    });
  }
}

async function ppRefresh() { if (appState.view === 'spend' && spState.sub === 'proposals') await renderProposalsPage(); }

/* ---------- Create ---------- */
async function ppOpenCreate(prefill) {
  await spLoadMeta();
  await ppLoadCommon();
  const pre = prefill || {};
  let opex = [], capexList = [];
  try { [opex, capexList] = await Promise.all([apiRequest('/spend/opex?status=all&size=200'), apiRequest('/spend/capex')]); opex = opex.items; }
  catch (err) { /* suggestions only */ }
  const tplOpts = (kind) => ppState.templates.filter(t => t.active && t.kind === kind).map(t => spOpt(t.id, t.name, '')).join('');
  const linkKind = pre.capexCode ? 'capex' : 'opex';
  const body = `<form id="ppForm" class="sp-form" novalidate><div class="src-grid">
      <label class="field"><span>Loại mẫu *</span><select name="kind">${spOpt('A', PP_KIND.A, pre.kind || 'A')}${spOpt('B', PP_KIND.B, pre.kind)}</select></label>
      <label class="field"><span>Mẫu *</span><select name="templateId" id="ppTpl">${tplOpts(pre.kind || 'A')}</select></label>
      <label class="field sp-span2"><span>Về việc *</span><input name="subject" maxlength="255" value="${esc(pre.subject || '')}" placeholder="Vd: Gia hạn bảo hành 04 máy chủ Dell R750 cụm PROD năm 2027"></label>
      <label class="field"><span>Đề xuất bởi</span><input name="department" maxlength="128" value="${esc(spState.meta.settings.proposalDepartment || '')}"></label>
      <label class="field"><span>Gắn với *</span><select name="linkKind" id="ppLinkKind">${spOpt('opex', 'Khoản Opex', linkKind)}${spOpt('capex', 'Dự án Capex', linkKind)}</select></label>
      <label class="field sp-span2 pp-opex"><span>Khoản Opex (giữ Ctrl / ⌘ để chọn nhiều)</span><select name="opexCodes" multiple size="6">${opex.map(o => `<option value="${esc(o.code)}"${(pre.opexCodes || []).includes(o.code) ? ' selected' : ''}>${esc(o.code)} — ${esc(o.title)} (${esc(spMoney(o.amountGross))})</option>`).join('')}</select></label>
      <label class="field sp-span2 pp-capex"><span>Dự án Capex</span><select name="capexCode">${spOpt('', '— Chọn —', pre.capexCode || '')}${capexList.map(c => spOpt(c.code, `${c.code} — ${c.name}`, pre.capexCode)).join('')}</select></label>
      <label class="field sp-span2"><span>Bối cảnh, lý do, phương án đã cân nhắc (AI dùng để viết)</span><textarea name="contextNote" rows="5" maxlength="8000" placeholder="Vd: thiết bị hết bảo hành 31/12/2026, đang chạy hệ thống giao dịch; đã hỏi giá 2 nhà cung cấp…"></textarea></label>
    </div>
    <p class="panel-sub">${ppState.ai.enabled ? 'Khi bấm “Soạn bằng AI”, dữ liệu các khoản đã chọn và phần bối cảnh được gửi tới Anthropic. Không ghi mật khẩu hay thông tin cá nhân vào bối cảnh.' : 'AI đang tắt: chỉ điền các trường dữ liệu vào mẫu.'}</p></form>`;
  spDialog('Soạn tờ trình', body, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
    <button class="btn btn-primary btn-sm" type="button" id="ppCreate">Tạo</button>`);
  const form = $('#ppForm');
  const sync = () => {
    const k = form.linkKind.value;
    form.querySelector('.pp-opex').classList.toggle('hidden', k !== 'opex');
    form.querySelector('.pp-capex').classList.toggle('hidden', k !== 'capex');
  };
  form.linkKind.onchange = sync; sync();
  form.kind.onchange = () => { $('#ppTpl').innerHTML = tplOpts(form.kind.value) || '<option value="">(chưa có mẫu loại này)</option>'; };
  spOnce('#ppCreate', async () => {
    const payload = { kind: form.kind.value, templateId: Number(form.templateId.value) || null, subject: form.subject.value.trim(),
      department: form.department.value.trim(), contextNote: form.contextNote.value };
    if (form.linkKind.value === 'capex') payload.capexCode = form.capexCode.value;
    else payload.opexCodes = Array.from(form.opexCodes.selectedOptions).map(o => o.value);
    try { const p = await apiRequest('/spend/proposals', { method: 'POST', body: JSON.stringify(payload) }); await ppRefresh(); ppOpenDetail(p.code); }
    catch (err) { spMsg(err.message); }
  });
}

/* ---------- Detail ---------- */
async function ppOpenDetail(code, note) {
  let p;
  try { await spLoadMeta(); await ppLoadCommon(); p = await apiRequest(`/spend/proposals/${encodeURIComponent(code)}`); }
  catch (err) { alert(err.message); return; }
  const draft = p.status === 'draft';
  const dl = (label, value) => `<div class="sp-dl-item"><dt>${label}</dt><dd>${value}</dd></div>`;
  const linked = p.capex
    ? `Capex <button class="btn btn-ghost btn-sm sp-link" type="button" data-cpx-goto="${esc(p.capex.code)}">${esc(p.capex.code)}</button><span class="muted-sub">${esc(p.capex.name)}</span>`
    : `<ul class="sp-files">${p.opex.map(o => `<li><button class="btn btn-ghost btn-sm sp-link" type="button" data-sp-goto="${esc(o.code)}">${esc(o.code)}</button> ${esc(o.title)} <span class="muted-sub sr-inline">${esc(spMoney(o.amountGross))}</span></li>`).join('')}</ul>`
  const linkedQuote = p.quote ? `<p>So sánh báo giá <b>${esc(p.quote.code)}</b> — ${esc(p.quote.title)}</p>` : '';
  const contracts = p.contracts.length ? `<ul class="sp-files">${p.contracts.map(c => `<li><button class="btn btn-ghost btn-sm sp-link" type="button" data-ct-goto="${esc(c.code)}">${esc(c.code)}</button> ${esc(c.title)}
      ${spCan('editor') ? `<button class="btn btn-ghost btn-sm btn-danger" type="button" data-pp-unlink="${esc(c.code)}">Bỏ liên kết</button>` : ''}</li>`).join('')}</ul>` : '<p class="muted-sub">Chưa gắn hợp đồng.</p>';
  const file = p.file
    ? `<a href="/api/spend/files/${encodeURIComponent(p.file.id)}" download>${esc(p.file.name)}</a><span class="muted-sub">${p.aiProvider === 'anthropic' ? `AI (${esc(p.aiModel || '')})` : 'Chỉ điền dữ liệu'} · ${esc(p.generatedBy || '')} · ${esc(spTime(p.generatedAt))}</span>`
    : '<span class="muted-sub">Chưa soạn file.</span>';
  const edit = draft && spCan('editor') ? `<details class="sp-history"><summary>Sửa nội dung yêu cầu</summary>
      <div class="src-grid">
        <label class="field sp-span2"><span>Về việc *</span><input type="text" id="ppSubject" maxlength="255" value="${esc(p.subject)}"></label>
        <label class="field"><span>Đề xuất bởi</span><input type="text" id="ppDept" maxlength="128" value="${esc(p.department || '')}"></label>
        <label class="field sp-span2"><span>Bối cảnh (AI dùng để viết)</span><textarea id="ppNote" rows="4" maxlength="8000">${esc(p.contextNote || '')}</textarea></label>
      </div><button class="btn btn-secondary btn-sm" type="button" id="ppSaveEdit">Lưu</button></details>` : '';
  const body = `
    <div class="sp-detail-head">${spPill(...(PP_STATUS[p.status] || ['na', p.statusLabel]))} <span class="pill na">${esc(p.kindLabel)}</span></div>
    ${note ? `<div class="insight-box">${esc(note)}</div>` : ''}
    <dl class="sp-dl">
      ${dl('Về việc', esc(p.subject))}
      ${dl('Đề xuất bởi', esc(p.department || '—'))}
      ${dl('Mẫu', `${esc(p.template ? p.template.name : '—')}${p.template && !p.template.active ? ' <span class="pill na">Đã tắt</span>' : ''}`)}
      ${dl('File tờ trình', file)}
      ${dl('Tạo', `${esc(p.createdBy)} · ${esc(spTime(p.createdAt))}`)}
    </dl>
    ${p.contextNote ? `<p class="sp-desc">${esc(p.contextNote)}</p>` : ''}
    <h4 class="sp-h">Gắn với</h4>${linkedQuote}${p.capex || p.opex.length ? linked : ""}
    <h4 class="sp-h">Hợp đồng liên quan</h4>${contracts}
    ${spCan('editor') ? '<div class="sp-inline"><input type="text" id="ppContract" list="ppContractList" maxlength="20" placeholder="Mã hợp đồng HD-…" aria-label="Mã hợp đồng"><button class="btn btn-secondary btn-sm" type="button" id="ppLink">Gắn hợp đồng</button><datalist id="ppContractList"></datalist></div>' : ''}
    ${edit}`;
  const btn = (attr, label, cls = 'btn-secondary') => `<button class="btn ${cls} btn-sm" type="button" ${attr}>${label}</button>`;
  const actions = [
    spCan('admin') && p.status !== 'approved' ? btn('id="ppDelete"', 'Xoá', 'btn-secondary btn-danger') : '',
    '<span class="src-spacer"></span>',
    p.status === 'rejected' && spCan('editor') ? btn('data-pp-cmd="reopen"', 'Mở lại để sửa') : '',
    p.status === 'submitted' && spCan('approver') ? btn('data-pp-cmd="reject"', 'Không duyệt') + btn('data-pp-cmd="approve"', 'Duyệt', 'btn-primary') : '',
    draft && spCan('editor') && p.file ? btn('data-pp-cmd="submit"', 'Đánh dấu đã trình') : '',
    draft && spCan('editor') ? btn('data-pp-gen="0"', p.file ? 'Tạo lại (chỉ dữ liệu)' : 'Chỉ điền dữ liệu') : '',
    draft && spCan('editor') && ppState.ai.enabled ? btn('data-pp-gen="1"', p.file ? 'Soạn lại bằng AI' : 'Soạn bằng AI', 'btn-primary') : '',
  ].join('');
  spDialog(`${esc(p.code)} — ${esc(p.subject)}`, body, actions);
  const reopen = async (msg) => { await ppRefresh(); await ppOpenDetail(p.code, msg); };
  $('#spDialogBody').onclick = (e) => {
    const go = e.target.closest('[data-sp-goto]'); if (go) return spOpenDetail(go.dataset.spGoto);
    const cpx = e.target.closest('[data-cpx-goto]'); if (cpx && typeof cpxOpenDetail === 'function') return cpxOpenDetail(cpx.dataset.cpxGoto);
    const ct = e.target.closest('[data-ct-goto]'); if (ct && typeof ctOpenDetail === 'function') return ctOpenDetail(ct.dataset.ctGoto);
  };
  $('#spDialog').querySelectorAll('[data-pp-gen]').forEach(b => b.onclick = async () => {
    const useAi = b.dataset.ppGen === '1';
    $('#spDialog').querySelectorAll('[data-pp-gen]').forEach(x => { x.disabled = true; });
    spMsg(useAi ? 'AI đang soạn nội dung theo mẫu — có thể mất 1–2 phút…' : 'Đang tạo file…', true);
    try {
      const r = await apiRequest(`/spend/proposals/${encodeURIComponent(p.code)}/generate`, { method: 'POST', body: JSON.stringify({ useAi }) });
      await reopen(r.warning || (useAi ? 'AI đã soạn xong — tải file về để kiểm tra và chỉnh tiếp trước khi trình.' : 'Đã tạo file.'));
    } catch (err) { spMsg(err.message); $('#spDialog').querySelectorAll('[data-pp-gen]').forEach(x => { x.disabled = false; }); }
  });
  $('#spDialog').querySelectorAll('[data-pp-cmd]').forEach(b => b.onclick = async () => {
    try { await apiRequest(`/spend/proposals/${encodeURIComponent(p.code)}/status/${encodeURIComponent(b.dataset.ppCmd)}`, { method: 'POST' }); await reopen(); }
    catch (err) { spMsg(err.message); }
  });
  $('#spDialogBody').querySelectorAll('[data-pp-unlink]').forEach(b => b.onclick = async () => {
    try { await apiRequest(`/spend/proposals/${encodeURIComponent(p.code)}/contracts/${encodeURIComponent(b.dataset.ppUnlink)}`, { method: 'DELETE' }); await reopen(); }
    catch (err) { spMsg(err.message); }
  });
  const dlist = $('#ppContractList');
  if (dlist) apiRequest('/spend/contracts').then(list => { dlist.innerHTML = list.map(c => `<option value="${esc(c.code)}">${esc(c.title)}</option>`).join(''); }).catch(() => {});
  spOnce('#ppLink', async () => {
    try { await apiRequest(`/spend/proposals/${encodeURIComponent(p.code)}/contracts`, { method: 'POST', body: JSON.stringify({ contractCode: $('#ppContract').value.trim() }) }); await reopen(); }
    catch (err) { spMsg(err.message); }
  });
  spOnce('#ppSaveEdit', async () => {
    try { await apiRequest(`/spend/proposals/${encodeURIComponent(p.code)}`, { method: 'PUT', body: JSON.stringify({ subject: $('#ppSubject').value, department: $('#ppDept').value, contextNote: $('#ppNote').value }) }); await reopen('Đã lưu — bấm soạn lại để cập nhật file.'); }
    catch (err) { spMsg(err.message); }
  });
  spOnce('#ppDelete', async () => {
    if (!confirm(`Xoá ${p.code} và file tờ trình? Thao tác được ghi nhật ký.`)) return;
    try { await apiRequest(`/spend/proposals/${encodeURIComponent(p.code)}`, { method: 'DELETE' }); $('#spDialog').close(); ppRefresh(); }
    catch (err) { spMsg(err.message); }
  });
}

/* =========================================================
   Cài đặt → Tờ trình & AI (admin)
   ========================================================= */
async function ppRenderAdmin(mount) {
  let aiCfg, templates, recipients;
  try {
    [aiCfg, templates, recipients] = await Promise.all([apiRequest('/spend/proposals/ai'), apiRequest('/spend/proposals/templates'), apiRequest('/spend/proposals/recipients')]);
  } catch (err) { mount.innerHTML = `<p class="criteria-status error">${esc(err.message)}</p>`; return; }
  const tplRows = templates.map(t => `<tr>
      <td><b>${esc(t.name)}</b><span class="muted-sub">${esc(t.kindLabel)}</span></td>
      <td class="wrap">${t.outline.sections.map(s => `${spPill(...(PP_SECTION_KIND[s.kind] || ['na', s.kind]))} ${esc(s.heading)}`).join('<br>')}</td>
      <td><a href="/api/spend/files/${encodeURIComponent(t.fileId)}" download>Tải mẫu</a><span class="muted-sub">${esc(t.uploadedBy)} · ${esc(spTime(t.uploadedAt))}</span></td>
      <td><label class="switch" title="Dùng mẫu này"><input type="checkbox" role="switch" data-tpl-active="${esc(t.id)}" ${t.active ? 'checked' : ''} aria-label="Dùng ${esc(t.name)}"><span class="slider"></span></label></td>
      <td>${t.usedBy ? `<span class="muted-sub">${esc(t.usedBy)} tờ trình</span>` : `<button class="btn btn-ghost btn-sm btn-danger" type="button" data-tpl-del="${esc(t.id)}">Xoá</button>`}</td></tr>`).join('');
  const recEditor = (kind) => {
    const items = recipients.items.filter(r => r.kind === kind);
    return `<div class="pp-rec" data-rec-kind="${kind}"><h5 class="pp-rec-title">${esc(PP_KIND[kind])}</h5>
      <div class="pp-rec-rows">${(items.length ? items : [{}]).map(r => ppRecRow(recipients.roles, r)).join('')}</div>
      <div class="sim-actions"><button class="btn btn-ghost btn-sm" type="button" data-rec-add="${kind}"><i data-lucide="plus"></i>Thêm người</button>
        <button class="btn btn-secondary btn-sm" type="button" data-rec-save="${kind}">Lưu người ký ${kind}</button></div></div>`;
  };
  mount.innerHTML = `
    <h4 class="sp-h">Tờ trình &amp; AI <span class="badge-admin">Admin</span></h4>
    <div class="sp-settings-grid">
      <div>
        <h5 class="pp-rec-title">AI soạn tờ trình</h5>
        <div class="settings-form">
          <label class="field"><span>Nhà cung cấp</span><select id="aiProvider">${spOpt('none', 'Không dùng AI (chỉ điền dữ liệu)', aiCfg.provider)}${spOpt('anthropic', 'Anthropic (Claude)', aiCfg.provider)}</select></label>
          <label class="field"><span>Model</span><input type="text" id="aiModel" maxlength="64" value="${esc(aiCfg.model)}"></label>
          <label class="field"><span>Mức suy luận (effort)</span><select id="aiEffort">${['low', 'medium', 'high'].map(x => spOpt(x, x, aiCfg.effort)).join('')}</select></label>
          <label class="field"><span>API key</span><input type="password" id="aiKey" autocomplete="off" placeholder="${aiCfg.hasApiKey ? 'Đã lưu — để trống để giữ' : 'sk-ant-…'}"></label>
        </div>
        <label class="finding-filter-item"><input type="checkbox" id="aiAllow" ${aiCfg.allowExternal ? 'checked' : ''}><span>Cho phép gửi dữ liệu tờ trình (các khoản Opex / Capex đã chọn, bối cảnh người soạn nhập) tới Anthropic. Không gửi serial, email, mật khẩu.</span></label>
        <p class="panel-sub">Trạng thái: <b>${aiCfg.enabled ? 'AI đang bật' : 'AI đang tắt'}</b>. Pod cần ra được <code>api.anthropic.com:443</code>.</p>
        <div class="sim-actions"><button class="btn btn-primary btn-sm" type="button" id="aiSave">Lưu cấu hình AI</button>
          <button class="btn btn-secondary btn-sm" type="button" id="aiTest">Kiểm tra kết nối</button></div>
      </div>
      <div>
        <h5 class="pp-rec-title">Tải mẫu tờ trình (.docx)</h5>
        <div class="settings-form">
          <label class="field"><span>Loại</span><select id="tplKind">${spOpt('A', PP_KIND.A, 'A')}${spOpt('B', PP_KIND.B, '')}</select></label>
          <label class="field"><span>Tên mẫu</span><input type="text" id="tplName" maxlength="128" placeholder="Vd: Mẫu A — trình TGĐ 2026"></label>
          <label class="field sp-span2"><span>File .docx</span><input type="file" id="tplFile" accept=".docx"></label>
        </div>
        <p class="panel-sub">Mẫu cần các tiêu đề mục đánh số (1., 2., …) như tờ trình hiện có; phụ lục bắt đầu bằng “Phụ lục …”. Hệ thống tự nhận mục nào soạn nội dung, mục nào giữ nguyên (xung đột lợi ích), mục để trống (Ý kiến …) và bảng ký.</p>
        <div class="sim-actions"><button class="btn btn-primary btn-sm" type="button" id="tplUpload">Tải mẫu lên</button></div>
      </div>
    </div>
    <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Mẫu</th><th>Các mục nhận diện được</th><th>File</th><th>Dùng</th><th></th></tr></thead>
      <tbody>${tplRows || '<tr><td colspan="5" class="empty-note">Chưa có mẫu.</td></tr>'}</tbody></table></div>
    <h5 class="pp-rec-title">Người ký / người nhận theo loại mẫu</h5>
    <p class="panel-sub">Điền vào bảng ký theo đúng thứ tự vai trò trong mẫu; người “Phê duyệt” đầu tiên được ghi vào “Trình đến” và “Kính gửi”. Để trống thì giữ nguyên tên trong mẫu.</p>
    <div class="sp-settings-grid">${recEditor('A')}${recEditor('B')}</div>`;
  refreshIcons();
  const msg = (t, ok) => { const el = $('#spSettingsMsg'); if (el) { el.textContent = t || ''; el.className = 'criteria-status' + (t ? (ok ? ' ok' : ' error') : ''); } };
  const rerender = () => ppRenderAdmin(mount);
  spOnce('#aiSave', async () => {
    try {
      await apiRequest('/spend/proposals/ai', { method: 'PUT', body: JSON.stringify({ provider: $('#aiProvider').value, model: $('#aiModel').value,
        effort: $('#aiEffort').value, apiKey: $('#aiKey').value, allowExternal: $('#aiAllow').checked }) });
      msg('Đã lưu cấu hình AI.', true); rerender();
    } catch (err) { msg(err.message); }
  });
  spOnce('#aiTest', async () => { try { const r = await apiRequest('/spend/proposals/ai/test', { method: 'POST' }); msg(r.message, r.ok); } catch (err) { msg(err.message); } });
  spOnce('#tplUpload', async () => {
    const f = $('#tplFile').files[0];
    if (!f) { msg('Chọn file .docx.'); return; }
    const fd = new FormData(); fd.append('file', f); fd.append('kind', $('#tplKind').value); fd.append('name', $('#tplName').value || f.name);
    try { const t = await apiRequest('/spend/proposals/templates', { method: 'POST', body: fd }); msg(`Đã tải mẫu — nhận diện ${t.outline.sections.length} mục.`, true); rerender(); }
    catch (err) { msg(err.message); }
  });
  mount.onclick = async (e) => {
    const t = (s) => e.target.closest(s);
    let b;
    try {
      if ((b = t('[data-tpl-del]')) && confirm('Xoá mẫu này?')) { await apiRequest(`/spend/proposals/templates/${encodeURIComponent(b.dataset.tplDel)}`, { method: 'DELETE' }); return rerender(); }
      if ((b = t('[data-rec-add]'))) { mount.querySelector(`[data-rec-kind="${b.dataset.recAdd}"] .pp-rec-rows`).insertAdjacentHTML('beforeend', ppRecRow(recipients.roles, {})); return refreshIcons(); }
      if ((b = t('[data-rec-del]'))) { b.closest('.pp-rec-row').remove(); return; }
      if ((b = t('[data-rec-save]'))) {
        const kind = b.dataset.recSave;
        const items = Array.from(mount.querySelectorAll(`[data-rec-kind="${kind}"] .pp-rec-row`)).map(r => ({
          role: r.querySelector('[data-r="role"]').value, salutation: r.querySelector('[data-r="salutation"]').value.trim(),
          name: r.querySelector('[data-r="name"]').value.trim(), title: r.querySelector('[data-r="title"]').value.trim() })).filter(x => x.name || x.title);
        await apiRequest(`/spend/proposals/recipients/${encodeURIComponent(kind)}`, { method: 'PUT', body: JSON.stringify({ items }) });
        msg(`Đã lưu người ký mẫu ${kind}.`, true);
      }
    } catch (err) { msg(err.message); }
  };
  mount.onchange = async (e) => {
    const s = e.target.closest('[data-tpl-active]');
    if (!s) return;
    try { await apiRequest(`/spend/proposals/templates/${encodeURIComponent(s.dataset.tplActive)}`, { method: 'PUT', body: JSON.stringify({ active: s.checked }) }); msg(s.checked ? 'Đã bật mẫu.' : 'Đã tắt mẫu.', true); }
    catch (err) { msg(err.message); }
  };
}

function ppRecRow(roles, r) {
  return `<div class="pp-rec-row">
    <select data-r="role" aria-label="Vai trò">${roles.map(x => spOpt(x.value, x.label, r.role || 'de_xuat')).join('')}</select>
    <input type="text" data-r="salutation" maxlength="16" placeholder="Anh / Chị" value="${esc(r.salutation || '')}" aria-label="Xưng hô">
    <input type="text" data-r="name" maxlength="128" placeholder="Họ và tên" value="${esc(r.name || '')}" aria-label="Họ và tên">
    <input type="text" data-r="title" maxlength="255" placeholder="Chức vụ" value="${esc(r.title || '')}" aria-label="Chức vụ">
    <button class="btn btn-ghost btn-sm" type="button" data-rec-del aria-label="Bỏ dòng"><i data-lucide="x"></i></button></div>`;
}
