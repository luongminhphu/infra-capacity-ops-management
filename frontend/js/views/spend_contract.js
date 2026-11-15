/* =========================================================
   Chi phí & Hợp đồng IT — trang Hợp đồng: version (một nhánh), so sánh tô màu
   theo bên chỉnh sửa, chốt Final, bản ký, lịch xoá version cũ.
   Comparison and attribution are computed by the backend (spend/contracts.py).
   ========================================================= */

const ctState = { list: null, filter: { status: '', q: '' }, bound: false, error: null };
const CT_STATUS = { draft: ['na', 'Nháp'], negotiating: ['warn', 'Đang đàm phán'], final: ['ok', 'Đã chốt (Final)'], signed: ['ok', 'Đã ký'] };
const CT_PARTY = { legal: 'Legal (nội bộ)', finance: 'Finance (nội bộ)', partner: 'Đối tác' };

async function ctLoad() {
  const qs = new URLSearchParams();
  Object.entries(ctState.filter).forEach(([k, v]) => { if (v) qs.set(k, v); });
  try { ctState.list = await apiRequest(`/spend/contracts?${qs}`); ctState.error = null; }
  catch (err) { ctState.error = err.message; ctState.list = []; }
}

async function renderContractsPage() {
  await ctLoad();
  ctRender();
  ctBind();
}

function ctPurgeText(c) {
  if (c.purgeDoneAt) return `Đã xoá file version cũ (${spTime(c.purgeDoneAt)})`;
  if (c.purgeCancelledAt) return `Đã huỷ lịch xoá (${c.purgeCancelledBy || ''})`;
  if (c.purgeDueAt) return `Xoá file version cũ lúc ${spTime(c.purgeDueAt)}`;
  return '';
}

function ctRender() {
  const f = ctState.filter, list = ctState.list || [];
  const rows = list.map(c => `<tr data-ct-code="${esc(c.code)}" class="sp-row" tabindex="0">
      <td><b>${esc(c.code)}</b></td>
      <td class="wrap">${esc(c.title)}<span class="muted-sub">${c.linkKind === 'opex' ? 'Opex' : 'Capex'} ${esc(c.linkCode || '')} · ${esc(c.linkTitle || '')}</span></td>
      <td class="wrap">${esc(c.vendorName || '—')}</td>
      <td>${spPill(...(CT_STATUS[c.status] || ['na', c.statusLabel]))}</td>
      <td>${c.currentVersion ? `v${esc(c.currentVersion)}` : '—'}${c.finalVersion ? `<span class="muted-sub">Final: v${esc(c.finalVersion)}</span>` : ''}</td>
      <td class="wrap"><span class="muted-sub bk-wrap">${esc(ctPurgeText(c))}</span></td>
      <td>${esc(spTime(c.updatedAt))}<span class="muted-sub">${esc(c.updatedBy || '')}</span></td></tr>`).join('');
  $('#spendMount').innerHTML = `
    <section class="table-card">
      <div class="panel-head">
        <div><div class="panel-title">Hợp đồng</div><p class="panel-sub">Mỗi hợp đồng gắn với một khoản Opex hoặc một dự án Capex. Mỗi lần upload tạo một version mới, không sửa được; chỉ một nhánh version.</p></div>
        <div class="panel-toolbar">${spCan('editor') ? '<button class="btn btn-primary btn-sm" type="button" data-ct-new><i data-lucide="plus"></i>Thêm hợp đồng</button>' : ''}</div>
      </div>
      <div class="sp-filters">
        <label class="field"><span>Trạng thái</span><select data-ct-filter="status">${spOpt('', 'Tất cả', f.status)}${Object.entries(CT_STATUS).map(([k, [, t]]) => spOpt(k, t, f.status)).join('')}</select></label>
        <label class="field sp-search"><span>Tìm</span><input type="text" data-ct-filter="q" value="${esc(f.q)}" placeholder="Mã hoặc tên hợp đồng"></label>
      </div>
      ${ctState.error ? `<p class="criteria-status error">${esc(ctState.error)}</p>` : ''}
      <div class="table-scroll"><table class="data-table sp-table">
        <thead><tr><th>Mã</th><th>Hợp đồng</th><th>Nhà cung cấp</th><th>Trạng thái</th><th>Version</th><th>Lưu trữ</th><th>Cập nhật</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="7" class="empty-note">${spCan('editor') ? 'Chưa có hợp đồng — bấm “Thêm hợp đồng”.' : 'Chưa có hợp đồng.'}</td></tr>`}</tbody>
      </table></div>
    </section>`;
  refreshIcons();
}

let ctSearchTimer = null;
function ctBind() {
  if (ctState.bound) return;
  ctState.bound = true;
  const mount = $('#spendMount');
  mount.addEventListener('change', async (e) => {
    const el = e.target.closest('[data-ct-filter]');
    if (!el || el.dataset.ctFilter === 'q') return;
    ctState.filter[el.dataset.ctFilter] = el.value; await ctLoad(); ctRender();
  });
  mount.addEventListener('input', (e) => {
    const el = e.target.closest('[data-ct-filter="q"]');
    if (!el) return;
    clearTimeout(ctSearchTimer);
    ctSearchTimer = setTimeout(async () => {
      ctState.filter.q = el.value.trim(); await ctLoad(); ctRender();
      const box = $('[data-ct-filter="q"]'); if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
    }, 350);
  });
  mount.addEventListener('click', (e) => {
    const row = e.target.closest('[data-ct-code]');
    if (row) return ctOpenDetail(row.dataset.ctCode);
    if (e.target.closest('[data-ct-new]')) return ctOpenCreate();
  });
  mount.addEventListener('keydown', (e) => {
    const row = e.target.closest('[data-ct-code]');
    if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); ctOpenDetail(row.dataset.ctCode); }
  });
}

async function ctRefresh() {
  if (appState.view === 'spend' && spState.sub === 'contracts') { await ctLoad(); ctRender(); }
}

/* ---------- Create ---------- */
async function ctOpenCreate(prefill) {
  let opex = [], capexList = [];
  try {
    [opex, capexList] = await Promise.all([apiRequest('/spend/opex?status=all&size=200'), apiRequest('/spend/capex')]);
    opex = opex.items;
  } catch (err) { /* lists are only suggestions */ }
  const p = prefill || {};
  const body = `<form id="ctForm" class="sp-form" novalidate><div class="src-grid">
      <label class="field sp-span2"><span>Tên hợp đồng *</span><input name="title" maxlength="255" value="${esc(p.title || '')}" placeholder="Vd: Hợp đồng gia hạn bảo hành Dell 2027"></label>
      <label class="field"><span>Gắn với *</span><select name="kind">${spOpt('opex', 'Khoản Opex', p.kind || 'opex')}${spOpt('capex', 'Dự án Capex', p.kind)}</select></label>
      <label class="field"><span>Mã Opex / Capex *</span><input name="code" list="ctCodeList" maxlength="20" value="${esc(p.code || '')}" placeholder="OPX-2026-0001 hoặc CPX-2026-0001"></label>
      <label class="field"><span>Nhà cung cấp</span><input name="vendorName" list="spVendorList" maxlength="255" placeholder="Mặc định theo khoản Opex"></label>
      <label class="field sp-span2"><span>Ghi chú</span><textarea name="note" rows="2" maxlength="5000"></textarea></label>
    </div>
    <datalist id="ctCodeList">${opex.map(o => `<option value="${esc(o.code)}">${esc(o.title)}</option>`).join('')}${capexList.map(c => `<option value="${esc(c.code)}">${esc(c.name)}</option>`).join('')}</datalist>
    <datalist id="spVendorList">${spState.meta.vendors.filter(x => x.active).map(x => `<option value="${esc(x.name)}"></option>`).join('')}</datalist>
  </form>`;
  spDialog('Thêm hợp đồng', body, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
    <button class="btn btn-primary btn-sm" type="button" id="ctSave">Tạo</button>`);
  spOnce('#ctSave', async () => {
    const fd = Object.fromEntries(new FormData($('#ctForm')).entries());
    const payload = { title: fd.title, note: fd.note, vendorName: fd.vendorName || undefined };
    payload[fd.kind === 'capex' ? 'capexCode' : 'opexCode'] = fd.code.trim();
    try { const c = await apiRequest('/spend/contracts', { method: 'POST', body: JSON.stringify(payload) }); await ctRefresh(); ctOpenDetail(c.code); }
    catch (err) { spMsg(err.message); }
  });
}

/* ---------- Detail ---------- */
async function ctOpenDetail(code) {
  let c;
  try { await spLoadMeta(); c = await apiRequest(`/spend/contracts/${encodeURIComponent(code)}`); }
  catch (err) { alert(err.message); return; }
  const open = ['draft', 'negotiating'].includes(c.status);
  const dl = (label, value) => `<div class="sp-dl-item"><dt>${label}</dt><dd>${value}</dd></div>`;
  const linkBtn = c.linkKind === 'opex'
    ? `<button class="btn btn-ghost btn-sm sp-link" type="button" data-sp-goto="${esc(c.linkCode)}">${esc(c.linkCode)}</button>`
    : `<button class="btn btn-ghost btn-sm sp-link" type="button" data-cpx-goto="${esc(c.linkCode)}">${esc(c.linkCode)}</button>`;
  const timeline = c.versions.map(v => `<li class="ct-tl-item ct-party-${esc(v.party)}">
      <span class="ct-tl-dot" aria-hidden="true"></span>
      <div class="ct-tl-body">
        <div><b>v${esc(v.versionNo)}</b> · <span class="ct-party-tag">${esc(v.partyLabel)}</span>${v.isFinal ? ` ${spPill('ok', 'Final')}` : ''}</div>
        <div class="muted-sub sr-inline">${esc(v.uploadedBy)} · ${esc(spTime(v.uploadedAt))}</div>
        ${v.note ? `<div class="ct-tl-note">${esc(v.note)}</div>` : ''}
        <div>${v.file && !v.file.purged ? `<a href="/api/spend/files/${encodeURIComponent(v.file.id)}" download>${esc(v.file.name)}</a> <span class="muted-sub sr-inline">${(v.file.sizeBytes / 1024).toFixed(0)} KB</span>`
          : `<span class="muted-sub sr-inline">File đã xoá theo chính sách lưu trữ (còn giữ thông tin kiểm toán)</span>`}</div>
      </div></li>`).join('');
  const comparable = c.versions.filter(v => v.hasText).map(v => v.versionNo).sort((a, b) => a - b);
  const vOpts = (sel) => comparable.map(n => spOpt(n, `v${n}`, sel)).join('');
  const compare = comparable.length >= 2 ? `<div class="sp-inline ct-compare-bar">
      <label class="field"><span>Từ</span><select id="ctFrom">${vOpts(comparable[comparable.length - 2])}</select></label>
      <label class="field"><span>Đến</span><select id="ctTo">${vOpts(comparable[comparable.length - 1])}</select></label>
      <button class="btn btn-secondary btn-sm" type="button" id="ctCompare">So sánh</button></div>
      <div id="ctDiff"></div>` : '<p class="muted-sub">Cần ít nhất hai version còn nội dung để so sánh.</p>';
  const upload = open && spCan('editor') ? `<div class="ct-upload">
      <h4 class="sp-h">Upload version mới (v${c.currentVersion + 1})</h4>
      <p class="panel-sub">Phải dựa trên bản mới nhất (v${c.currentVersion || 0}). Nếu ai đó đã upload bản mới hơn, hệ thống sẽ từ chối và yêu cầu tải bản mới về sửa tiếp.</p>
      <div class="src-grid">
        <label class="field"><span>File .docx *</span><input type="file" id="ctFile" accept=".docx"></label>
        <label class="field"><span>Bên chỉnh sửa *</span><select id="ctParty">${Object.entries(CT_PARTY).map(([k, t]) => spOpt(k, t, 'legal')).join('')}</select></label>
        <label class="field sp-span2"><span>Ghi chú</span><input type="text" id="ctNote" maxlength="1000" placeholder="Vd: Đối tác đề nghị giảm giá còn 95 triệu"></label>
      </div>
      <button class="btn btn-primary btn-sm" type="button" id="ctUpload" data-base="${esc(c.currentVersion)}">Upload v${c.currentVersion + 1}</button></div>` : '';
  const finalize = open && spCan('approver') && c.currentVersion ? `<div class="sp-inline"><label class="field"><span>Version chốt</span><select id="ctFinalV">${c.versions.map(v => spOpt(v.versionNo, `v${v.versionNo} · ${v.partyLabel}`, c.currentVersion)).join('')}</select></label>
      <button class="btn btn-primary btn-sm" type="button" id="ctFinalize">Chốt Final</button></div>` : '';
  const signForm = c.status === 'final' && spCan('approver') ? `<label class="field"><span>Bản ký (scan PDF)</span><input type="file" id="ctSigned" accept=".pdf"></label>` : '';
  const purge = c.purgeDueAt ? `<div class="insight-box">${esc(ctPurgeText(c))}. Chỉ giữ bản Final${c.signedFile ? ' và bản ký' : ''}; thông tin version và nhật ký vẫn giữ.
      ${spCan('admin') && !c.purgeDoneAt ? (c.purgeCancelledAt ? '<button class="btn btn-ghost btn-sm" type="button" id="ctPurgeResume">Bật lại lịch xoá</button>'
        : '<button class="btn btn-ghost btn-sm" type="button" id="ctPurgeCancel">Huỷ lịch xoá</button>') : ''}</div>` : '';
  const body = `
    <div class="sp-detail-head">${spPill(...(CT_STATUS[c.status] || ['na', c.statusLabel]))}</div>
    <dl class="sp-dl">
      ${dl('Gắn với', `${c.linkKind === 'opex' ? 'Opex' : 'Capex'} ${linkBtn}<span class="muted-sub">${esc(c.linkTitle || '')}</span>`)}
      ${dl('Nhà cung cấp', esc(c.vendorName || '—'))}
      ${dl('Version hiện tại', c.currentVersion ? `v${esc(c.currentVersion)}` : '—')}
      ${dl('Chốt Final', c.finalVersion ? `v${esc(c.finalVersion)} · ${esc(c.finalizedBy)} · ${esc(spTime(c.finalizedAt))}` : '—')}
      ${dl('Bản ký', c.signedFile ? `<a href="/api/spend/files/${encodeURIComponent(c.signedFile.id)}" download>${esc(c.signedFile.name)}</a><span class="muted-sub">${esc(c.signedBy)} · ${esc(spTime(c.signedAt))}</span>` : '—')}
      ${dl('Tạo', `${esc(c.createdBy)} · ${esc(spTime(c.createdAt))}`)}
    </dl>
    ${c.note ? `<p class="sp-desc">${esc(c.note)}</p>` : ''}
    ${purge}
    <h4 class="sp-h">Thanh toán</h4>
    <div id="ctPayments"><p class="muted-sub">Đang tải…</p></div>
    ${upload}
    <h4 class="sp-h">Lịch sử version</h4>
    ${timeline ? `<ol class="ct-timeline">${timeline}</ol>` : '<p class="muted-sub">Chưa có version nào.</p>'}
    <h4 class="sp-h">So sánh version</h4>
    <div class="ct-legend">${Object.entries(CT_PARTY).map(([k, t]) => `<span class="ct-party-${esc(k)}"><i class="ct-swatch"></i>${esc(t)}</span>`).join('')}</div>
    ${compare}
    ${finalize || signForm ? `<h4 class="sp-h">Chốt &amp; ký</h4>${finalize}${signForm}` : ''}`;
  const btn = (attr, label, cls = 'btn-secondary') => `<button class="btn ${cls} btn-sm" type="button" ${attr}>${label}</button>`;
  const actions = [
    spCan('admin') && open ? btn('id="ctDelete"', 'Xoá', 'btn-secondary btn-danger') : '',
    '<span class="src-spacer"></span>',
    c.status === 'draft' && spCan('editor') && c.currentVersion ? btn('id="ctNegotiate"', 'Chuyển sang đàm phán') : '',
  ].join('');
  spDialog(`${esc(c.code)} — ${esc(c.title)}`, body, actions);
  if (typeof payRender === 'function') payRender(c.code);
  const reopen = async () => { await ctRefresh(); await ctOpenDetail(c.code); };
  const post = (path, payload) => apiRequest(`/spend/contracts/${encodeURIComponent(c.code)}/${path}`, { method: 'POST', body: JSON.stringify(payload || {}) });
  $('#spDialogBody').onclick = (e) => {
    const go = e.target.closest('[data-sp-goto]');
    if (go) return spOpenDetail(go.dataset.spGoto);
    const cpx = e.target.closest('[data-cpx-goto]');
    if (cpx && typeof cpxOpenDetail === 'function') return cpxOpenDetail(cpx.dataset.cpxGoto);
  };
  spOnce('#ctUpload', async () => {
    const file = $('#ctFile').files[0];
    if (!file) { spMsg('Chọn file .docx.'); return; }
    const fd = new FormData();
    fd.append('file', file); fd.append('editorParty', $('#ctParty').value);
    fd.append('baseVersion', $('#ctUpload').dataset.base); fd.append('note', $('#ctNote').value);
    try { await apiRequest(`/spend/contracts/${encodeURIComponent(c.code)}/versions`, { method: 'POST', body: fd }); await reopen(); spMsg('Đã upload version mới.', true); }
    catch (err) { spMsg(err.message); }
  });
  spOnce('#ctCompare', async () => {
    try {
      const d = await apiRequest(`/spend/contracts/${encodeURIComponent(c.code)}/diff?from=${encodeURIComponent($('#ctFrom').value)}&to=${encodeURIComponent($('#ctTo').value)}`);
      $('#ctDiff').innerHTML = ctDiffHtml(d);
    } catch (err) { spMsg(err.message); }
  });
  spOnce('#ctFinalize', async () => {
    if (!await uiConfirm('Chốt Final? Sau khi chốt không nhận version mới; file các version khác sẽ bị xoá sau thời gian chờ.')) return;
    try { await post('finalize', { versionNo: Number($('#ctFinalV').value) }); await reopen(); } catch (err) { spMsg(err.message); }
  });
  spOnce('#ctNegotiate', async () => { try { await post('negotiate'); await reopen(); } catch (err) { spMsg(err.message); } });
  spOnce('#ctPurgeCancel', async () => { try { await post('purge/cancel'); await reopen(); } catch (err) { spMsg(err.message); } });
  spOnce('#ctPurgeResume', async () => { try { await post('purge/resume'); await reopen(); } catch (err) { spMsg(err.message); } });
  spOnce('#ctDelete', async () => {
    if (!await uiConfirm(`Xoá vĩnh viễn ${c.code} và toàn bộ file version? Thao tác được ghi nhật ký.`)) return;
    try { await apiRequest(`/spend/contracts/${encodeURIComponent(c.code)}`, { method: 'DELETE' }); $('#spDialog').close(); ctRefresh(); }
    catch (err) { spMsg(err.message); }
  });
  const signed = $('#ctSigned');
  if (signed) signed.onchange = async () => {
    const f = signed.files[0];
    if (!f) return;
    const fd = new FormData(); fd.append('file', f);
    try { await apiRequest(`/spend/contracts/${encodeURIComponent(c.code)}/sign`, { method: 'POST', body: fd }); await reopen(); spMsg('Đã cập nhật bản ký — hợp đồng chuyển sang “Đã ký”.', true); }
    catch (err) { spMsg(err.message); signed.value = ''; }
  };
}

/* ---------- Diff rendering ---------- */
function ctDiffHtml(d) {
  const tag = (b) => `<span class="ct-who">v${esc(b.version)} · ${esc(CT_PARTY[b.party] || b.party)}</span>`;
  const parts = d.blocks.map(b => {
    if (b.op === 'equal') {
      const ps = b.paragraphs;
      const show = ps.length <= 4 ? ps : [ps[0], null, ps[ps.length - 1]];
      return show.map(p => p === null ? `<div class="ct-skip">… ${ps.length - 2} đoạn không đổi …</div>` : `<p class="ct-eq">${esc(p)}</p>`).join('');
    }
    if (b.op === 'insert') return `<div class="ct-chg ct-party-${esc(b.party)}">${tag(b)}<ins>${esc(b.text)}</ins></div>`;
    if (b.op === 'delete') return `<div class="ct-chg ct-party-${esc(b.party)}">${tag(b)}<del>${esc(b.text)}</del></div>`;
    return `<div class="ct-chg ct-party-${esc(b.party)}">${tag(b)}<span>${b.words.map(w =>
      w.op === 'equal' ? esc(w.text) : w.op === 'insert' ? `<ins>${esc(w.text)}</ins>` : `<del>${esc(w.text)}</del>`).join('')}</span></div>`;
  }).join('');
  const counts = Object.entries(d.changesByParty).filter(([, n]) => n).map(([k, n]) => `${esc(CT_PARTY[k])}: ${esc(n)}`).join(' · ');
  return `<p class="panel-sub">v${esc(d.from)} → v${esc(d.to)} · ${counts || 'không có thay đổi'}. Gạch ngang: bị xoá; gạch chân: thêm mới; màu theo bên đã sửa.</p>
    <div class="ct-diff">${parts || '<p class="muted-sub">Hai version giống nhau.</p>'}</div>`;
}
