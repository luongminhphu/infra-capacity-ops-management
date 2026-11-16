/* =========================================================
   Chi phí & Hợp đồng IT — thanh toán hợp đồng: các đợt thanh toán và checklist
   hồ sơ của từng đợt (rendered inside the contract dialog, spend_contract.js).
   Rules (required documents before submit, approver marks paid) live in the
   backend (routers/spend_payments.py).
   ========================================================= */

const PAY_STATUS = { pending: ['na', 'Chờ hồ sơ'], submitted: ['warn', 'Đã nộp hồ sơ'], paid: ['ok', 'Đã thanh toán'] };
const payState = { code: null, data: null, open: new Set(), editing: null };

async function payRender(code) {
  const mount = $('#ctPayments');
  if (!mount) return;
  if (payState.code !== code) { payState.code = code; payState.open = new Set(); payState.editing = null; }
  try { payState.data = await apiRequest(`/spend/contracts/${encodeURIComponent(code)}/payments`); }
  catch (err) { mount.innerHTML = `<p class="criteria-status error">${esc(err.message)}</p>`; return; }
  const d = payState.data;
  const diff = d.differenceFromValue;
  const rows = d.items.map(p => {
    const open = payState.open.has(p.id);
    const progress = p.docsTotal ? Math.round(p.docsDone * 100 / p.docsTotal) : 0;
    const docs = p.documents.map(x => `<li class="pay-doc">
        <label class="finding-filter-item"><input type="checkbox" data-pay-tick="${esc(p.id)}:${esc(x.id)}" ${x.done ? 'checked' : ''}
          ${p.status !== 'pending' || !spCan('editor') ? 'disabled' : ''}><span>${esc(x.name)}${x.required ? ' ' + uiTag('Bắt buộc') : ''}</span></label>
        <span class="muted-sub sr-inline">${x.done ? `${esc(x.doneBy || '')} · ${esc(spTime(x.doneAt))}` : 'Chưa có'}</span>
        ${x.file ? `<a href="/api/spend/files/${encodeURIComponent(x.file.id)}" download>${esc(x.file.name)}</a>` : ''}
        ${x.file && /\.pdf$/i.test(x.file.name) && spCan('editor') ? `<button class="btn btn-ghost btn-sm hidden" type="button" data-ai-gate="invoice_read" data-ai-act="invoice" data-ai-url="${esc(`/spend/contracts/${encodeURIComponent(code)}/payments/${p.id}/documents/${x.id}/ai-read`)}" data-ai-out="payAi-${esc(x.id)}">AI đọc hoá đơn</button>` : ''}
        <div class="ai-out pay-ai" id="payAi-${esc(x.id)}" aria-live="polite"></div>
        ${spCan('editor') && p.status === 'pending' ? `<label class="btn btn-ghost btn-sm pay-file">Đính kèm<input type="file" class="hidden" accept=".pdf,.docx,.xlsx" data-pay-file="${esc(p.id)}:${esc(x.id)}"></label>` : ''}
        ${spCan('editor') && p.status === 'pending' ? `<button class="btn btn-ghost btn-sm btn-danger" type="button" data-pay-deldoc="${esc(p.id)}:${esc(x.id)}" aria-label="Xoá hồ sơ ${esc(x.name)}"><i data-lucide="x"></i></button>` : ''}
      </li>`).join('');
    const btn = (attr, label, cls = 'btn-ghost') => `<button class="btn ${cls} btn-sm" type="button" ${attr}>${label}</button>`;
    const acts = [
      p.status === 'pending' && spCan('editor') ? btn(`data-pay-edit="${esc(p.id)}"`, 'Sửa') : '',
      p.status === 'pending' && spCan('editor') ? btn(`data-pay-del="${esc(p.id)}"`, 'Xoá', 'btn-ghost btn-danger') : '',
      p.status === 'pending' && spCan('editor') ? btn(`data-pay-submit="${esc(p.id)}"`, 'Nộp hồ sơ', 'btn-secondary') : '',
      p.status === 'submitted' && spCan('approver') ? btn(`data-pay-reopen="${esc(p.id)}"`, 'Mở lại') : '',
      p.status === 'submitted' && spCan('approver') ? btn(`data-pay-paid="${esc(p.id)}"`, 'Ghi nhận đã thanh toán', 'btn-secondary') : '',
      p.status === 'paid' && spCan('admin') ? btn(`data-pay-unpay="${esc(p.id)}"`, 'Huỷ ghi nhận') : '',
    ].join('');
    const paidForm = payState.editing === `paid:${p.id}` ? `<div class="sp-inline pay-paid-form">
        <label class="field"><span>Ngày thanh toán</span><input type="date" id="payPaidAt" value="${esc(spState.meta.today)}"></label>
        <label class="field"><span>Số tiền đã trả (đ)</span><input type="number" id="payPaidAmount" min="0" step="1" value="${esc(p.amount)}"></label>
        <label class="field"><span>Số chứng từ / UNC</span><input type="text" id="payRef" maxlength="64"></label>
        <button class="btn btn-primary btn-sm" type="button" data-pay-paid-save="${esc(p.id)}">Lưu</button></div>` : '';
    return `<div class="pay-item ${open ? 'open' : ''}">
      <div class="pay-row" data-pay-toggle="${esc(p.id)}" tabindex="0" role="button" aria-expanded="${open}">
        <div><b>Đợt ${esc(p.seq)}</b> · ${esc(p.title)}<span class="muted-sub">${p.condition ? esc(p.condition) : ''}${p.note ? ` · ${esc(p.note)}` : ''}</span></div>
        <div class="num">${spMoney(p.amount)}${p.percent !== null ? `<span class="muted-sub">${esc(p.percent)}% giá trị</span>` : ''}</div>
        <div>${spPill(p.tone, p.dueLabel)}${p.dueDate ? `<span class="muted-sub">Hạn ${spDate(p.dueDate)}</span>` : ''}</div>
        <div><span class="sr-bar" aria-hidden="true"><span style="width:${progress}%"></span></span> ${esc(p.docsDone)}/${esc(p.docsTotal)} hồ sơ</div>
        <div>${spPill(...(PAY_STATUS[p.status] || ['na', p.statusLabel]))}${p.status === 'paid' ? `<span class="muted-sub">${spDate(p.paidAt)} · ${spMoney(p.paidAmount)}${p.paymentRef ? ` · ${esc(p.paymentRef)}` : ''}</span>` : ''}</div>
      </div>
      <div class="pay-body">
        ${p.requiredMissing.length && p.status === 'pending' ? `<p class="muted-sub bk-wrap">Còn thiếu: ${esc(p.requiredMissing.join('; '))}</p>` : ''}
        <ul class="pay-docs">${docs || '<li class="muted-sub">Chưa có hồ sơ nào trong checklist.</li>'}</ul>
        ${spCan('editor') && p.status === 'pending' ? `<div class="sp-inline"><input type="text" data-pay-newdoc="${esc(p.id)}" maxlength="255" placeholder="Thêm hồ sơ, vd: Bảo lãnh bảo hành" aria-label="Tên hồ sơ mới">
          <label class="finding-filter-item"><input type="checkbox" data-pay-newreq="${esc(p.id)}" checked><span>Bắt buộc</span></label>
          <button class="btn btn-ghost btn-sm" type="button" data-pay-adddoc="${esc(p.id)}">Thêm</button></div>` : ''}
        ${paidForm}
        <div class="sim-actions">${acts}</div>
      </div></div>`;
  }).join('');
  const editP = payState.editing && payState.editing.startsWith('edit:') ? d.items.find(x => `edit:${x.id}` === payState.editing) : null;
  const form = spCan('editor') && (payState.editing === 'new' || editP) ? `<div class="pay-form">
      <div class="src-grid">
        <label class="field sp-span2"><span>Nội dung đợt *</span><input type="text" id="payTitle" maxlength="255" value="${esc(editP ? editP.title : '')}" placeholder="Vd: Tạm ứng 30% khi ký hợp đồng"></label>
        <label class="field"><span>% giá trị hợp đồng</span><input type="number" id="payPct" min="0" max="100" step="0.01" value="${esc(editP && editP.percent !== null ? editP.percent : '')}"></label>
        <label class="field"><span>Hoặc số tiền (đ, sau VAT)</span><input type="number" id="payAmount" min="0" step="1" value="${esc(editP ? editP.amount : '')}" placeholder="Tự tính theo %"></label>
        <label class="field"><span>Hạn thanh toán</span><input type="date" id="payDue" value="${esc(editP && editP.dueDate ? editP.dueDate : '')}"></label>
        <label class="field"><span>Điều kiện</span><input type="text" id="payCond" maxlength="500" value="${esc(editP ? editP.condition || '' : '')}" placeholder="Vd: sau khi nghiệm thu"></label>
        <label class="field sp-span2"><span>Ghi chú</span><input type="text" id="payNote" maxlength="2000" value="${esc(editP ? editP.note || '' : '')}"></label>
      </div>
      ${editP ? '' : '<p class="panel-sub">Checklist hồ sơ được tạo sẵn theo danh sách mặc định (Cài đặt), sửa được sau.</p>'}
      <div class="sim-actions"><button class="btn btn-primary btn-sm" type="button" id="paySave" data-edit="${esc(editP ? editP.id : '')}">${editP ? 'Lưu đợt' : 'Thêm đợt'}</button>
        <button class="btn btn-ghost btn-sm" type="button" id="payCancel">Huỷ</button></div></div>` : '';
  mount.innerHTML = `
    <div class="pay-summary">
      <span>Giá trị tham chiếu: <b>${spMoney(d.contractValue)}</b></span>
      <span>Tổng các đợt: <b>${spMoney(d.plannedTotal)}</b></span>
      <span>Đã thanh toán: <b class="tone-ok">${spMoney(d.paidTotal)}</b></span>
      <span>Còn lại: <b>${spMoney(d.remaining)}</b></span>
      ${d.overdue ? `<span>${spPill('crit', `${d.overdue} đợt quá hạn`)}</span>` : ''}
    </div>
    ${diff ? `<p class="muted-sub bk-wrap">Tổng các đợt ${diff > 0 ? 'lớn hơn' : 'nhỏ hơn'} giá trị Opex / Capex ${spMoney(Math.abs(diff))}.</p>` : ''}
    ${rows || '<p class="muted-sub">Chưa có đợt thanh toán.</p>'}
    ${form}
    ${spCan('editor') && !form ? '<button class="btn btn-ghost btn-sm" type="button" id="payNew"><i data-lucide="plus"></i>Thêm đợt thanh toán</button>' : ''}`;
  refreshIcons();
  if (typeof aiGate === 'function') aiGate(mount);
  payBind(code);
}

function payBind(code) {
  const mount = $('#ctPayments');
  const base = `/spend/contracts/${encodeURIComponent(code)}/payments`;
  const run = async (fn) => { try { await fn(); await payRender(code); } catch (err) { spMsg(err.message); } };
  const ids = (v) => v.split(':').map(encodeURIComponent);
  mount.onclick = async (e) => {
    const t = (s) => e.target.closest(s);
    let b;
    if (t('#payNew')) { payState.editing = 'new'; return payRender(code); }
    if (t('#payCancel')) { payState.editing = null; return payRender(code); }
    if ((b = t('#paySave'))) {
      const body = { title: $('#payTitle').value, percent: $('#payPct').value === '' ? null : Number($('#payPct').value),
        amount: $('#payAmount').value === '' ? null : Number($('#payAmount').value), dueDate: $('#payDue').value || null,
        condition: $('#payCond').value, note: $('#payNote').value };
      const edit = b.dataset.edit;
      if (edit) body.rowVersion = payState.data.items.find(x => String(x.id) === edit).rowVersion;
      return run(async () => {
        await apiRequest(edit ? `${base}/${encodeURIComponent(edit)}` : base, { method: edit ? 'PUT' : 'POST', body: JSON.stringify(body) });
        payState.editing = null;
      });
    }
    if ((b = t('[data-pay-edit]'))) { payState.editing = `edit:${b.dataset.payEdit}`; return payRender(code); }
    if ((b = t('[data-pay-del]')) && await uiConfirm('Xoá đợt thanh toán này cùng checklist hồ sơ?')) return run(() => apiRequest(`${base}/${encodeURIComponent(b.dataset.payDel)}`, { method: 'DELETE' }));
    if ((b = t('[data-pay-submit]'))) return run(() => apiRequest(`${base}/${encodeURIComponent(b.dataset.paySubmit)}/submit`, { method: 'POST' }));
    if ((b = t('[data-pay-reopen]'))) return run(() => apiRequest(`${base}/${encodeURIComponent(b.dataset.payReopen)}/reopen`, { method: 'POST' }));
    if ((b = t('[data-pay-unpay]')) && await uiConfirm('Huỷ ghi nhận thanh toán của đợt này?')) return run(() => apiRequest(`${base}/${encodeURIComponent(b.dataset.payUnpay)}/unpay`, { method: 'POST' }));
    if ((b = t('[data-pay-paid]'))) { payState.editing = `paid:${b.dataset.payPaid}`; payState.open.add(Number(b.dataset.payPaid)); return payRender(code); }
    if ((b = t('[data-pay-paid-save]'))) {
      return run(async () => {
        await apiRequest(`${base}/${encodeURIComponent(b.dataset.payPaidSave)}/paid`, { method: 'POST', body: JSON.stringify({
          paidAt: $('#payPaidAt').value, paidAmount: $('#payPaidAmount').value === '' ? null : Number($('#payPaidAmount').value), paymentRef: $('#payRef').value }) });
        payState.editing = null;
      });
    }
    if ((b = t('[data-pay-adddoc]'))) {
      const id = b.dataset.payAdddoc;
      const name = mount.querySelector(`[data-pay-newdoc="${id}"]`).value.trim();
      if (!name) return;
      return run(() => apiRequest(`${base}/${encodeURIComponent(id)}/documents`, { method: 'POST',
        body: JSON.stringify({ name, required: mount.querySelector(`[data-pay-newreq="${id}"]`).checked }) }));
    }
    if ((b = t('[data-pay-deldoc]'))) { const [p, x] = ids(b.dataset.payDeldoc); return run(() => apiRequest(`${base}/${p}/documents/${x}`, { method: 'DELETE' })); }
    if (t('input, label, a, button')) return;
    if ((b = t('[data-pay-toggle]'))) {
      const id = Number(b.dataset.payToggle);
      if (payState.open.has(id)) payState.open.delete(id); else payState.open.add(id);
      b.closest('.pay-item').classList.toggle('open');
      b.setAttribute('aria-expanded', payState.open.has(id));
    }
  };
  mount.onkeydown = (e) => {
    const b = e.target.closest('[data-pay-toggle]');
    if (b && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); b.click(); }
  };
  mount.onchange = (e) => {
    let b;
    if ((b = e.target.closest('[data-pay-tick]'))) {
      const [p, x] = ids(b.dataset.payTick);
      return run(() => apiRequest(`${base}/${p}/documents/${x}`, { method: 'PUT', body: JSON.stringify({ done: b.checked }) }));
    }
    if ((b = e.target.closest('[data-pay-file]')) && b.files[0]) {
      const [p, x] = ids(b.dataset.payFile);
      const fd = new FormData(); fd.append('file', b.files[0]);
      return run(() => apiRequest(`${base}/${p}/documents/${x}/file`, { method: 'POST', body: fd }));
    }
  };
}

/* ---------- Settings: default checklist (admin) ---------- */
async function payRenderDefaults(mount) {
  if (!mount) return;
  let items;
  try { items = await apiRequest('/spend/admin/payment-docs'); } catch (err) { mount.innerHTML = ''; return; }
  const row = (x = {}) => `<div class="pp-rec-row pay-def-row">
      <input type="text" data-d="name" maxlength="255" value="${esc(x.name || '')}" placeholder="Tên hồ sơ" aria-label="Tên hồ sơ">
      <label class="finding-filter-item"><input type="checkbox" data-d="required" ${x.required !== false ? 'checked' : ''}><span>Bắt buộc</span></label>
      <label class="finding-filter-item"><input type="checkbox" data-d="active" ${x.active !== false ? 'checked' : ''}><span>Dùng</span></label>
      <button class="btn btn-ghost btn-sm" type="button" data-def-del aria-label="Bỏ dòng"><i data-lucide="x"></i></button></div>`;
  mount.innerHTML = `<h5 class="pp-rec-title">Checklist hồ sơ thanh toán mặc định</h5>
    <p class="panel-sub">Tự thêm vào mỗi đợt thanh toán mới; hồ sơ “Bắt buộc” phải tick đủ mới nộp hồ sơ được. Sửa ở đây không đổi các đợt đã tạo.</p>
    <div id="payDefRows">${items.map(row).join('')}</div>
    <div class="sim-actions"><button class="btn btn-ghost btn-sm" type="button" id="payDefAdd"><i data-lucide="plus"></i>Thêm hồ sơ</button>
      <button class="btn btn-secondary btn-sm" type="button" id="payDefSave">Lưu checklist mặc định</button></div>`;
  refreshIcons();
  const msg = (t, ok) => { const el = $('#spSettingsMsg'); if (el) { el.textContent = t || ''; el.className = 'criteria-status' + (t ? (ok ? ' ok' : ' error') : ''); } };
  mount.onclick = async (e) => {
    if (e.target.closest('#payDefAdd')) { $('#payDefRows').insertAdjacentHTML('beforeend', row()); refreshIcons(); return; }
    const del = e.target.closest('[data-def-del]');
    if (del) { del.closest('.pay-def-row').remove(); return; }
    if (e.target.closest('#payDefSave')) {
      const body = { items: $$('#payDefRows .pay-def-row').map(r => ({ name: r.querySelector('[data-d="name"]').value.trim(),
        required: r.querySelector('[data-d="required"]').checked, active: r.querySelector('[data-d="active"]').checked })).filter(x => x.name) };
      try { await apiRequest('/spend/admin/payment-docs', { method: 'PUT', body: JSON.stringify(body) }); msg('Đã lưu checklist mặc định.', true); }
      catch (err) { msg(err.message); }
    }
  };
}
