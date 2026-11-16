/* =========================================================
   Chi phí & Hợp đồng IT — trang Hợp đồng: version (một nhánh), so sánh tô màu
   theo bên chỉnh sửa, chốt Final, bản ký, lịch xoá version cũ.
   Comparison and attribution are computed by the backend (spend/contracts.py).
   ========================================================= */

const ctState = { list: null, filter: { status: '', q: '' }, bound: false, error: null, selected: null };
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
  ctBind();
  await ctShow();
}

function ctPurgeText(c) {
  if (c.purgeDoneAt) return `Đã xoá file version cũ (${spTime(c.purgeDoneAt)})`;
  if (c.purgeCancelledAt) return `Đã huỷ lịch xoá (${c.purgeCancelledBy || ''})`;
  if (c.purgeDueAt) return `Xoá file version cũ lúc ${spTime(c.purgeDueAt)}`;
  return '';
}

const CT_VIEWS = [['', 'Tất cả'], ['draft', 'Nháp'], ['negotiating', 'Đang đàm phán'], ['final', 'Đã chốt'], ['signed', 'Đã ký']];

/* Hợp đồng as list → detail (pages/danh-sach.md): views by status, grouped draft → negotiating → final → signed;
   the detail (versions, payments, compare, finalize) opens beside the list on wide screens, in the dialog otherwise. */
function ctRender() {
  const f = ctState.filter, list = ctState.list || [];
  const row = (c) => `<li class="sp-li" role="option" id="ct-opt-${esc(c.code)}" data-ct-code="${esc(c.code)}" aria-selected="${c.code === ctState.selected}">
      <span class="hm-lv lv-${esc((CT_STATUS[c.status] || ['na'])[0])}" aria-hidden="true"></span>
      <span class="sp-li-t">${esc(c.title)}</span>
      <span class="sp-li-r"><b>${c.currentVersion ? `v${esc(c.currentVersion)}` : 'chưa có bản'}</b>${esc(spTime(c.updatedAt))}</span>
      <span class="sp-li-m">${esc(c.code)} · ${esc(c.vendorName || 'chưa có NCC')} · ${c.linkKind === 'opex' ? 'Opex' : 'Capex'} ${esc(c.linkCode || '')}${ctPurgeText(c) ? ` · ${esc(ctPurgeText(c))}` : ''}</span>
      <span class="sr-only">${esc((CT_STATUS[c.status] || ['', c.statusLabel])[1])}</span></li>`;
  const groups = Object.keys(CT_STATUS).map(k => {
    const g = list.filter(c => c.status === k);
    return g.length ? `<li class="hm-group" role="presentation"><span>${esc(CT_STATUS[k][1])}</span><span>${g.length}</span></li>${g.map(row).join('')}` : '';
  }).join('');
  const cnt = (k) => list.filter(c => c.status === k).length;
  $('#spendMount').innerHTML = `
    <div class="kpi-strip-wrap">${uiKpiStrip([
      { label: 'Đang đàm phán', value: String(cnt('negotiating')), sub: 'Còn nhận version mới' },
      { label: 'Nháp', value: String(cnt('draft')), sub: 'Chưa chuyển sang đàm phán' },
      { label: 'Đã chốt, chờ ký', value: String(cnt('final')), sub: 'Cần upload bản ký (scan PDF)' },
      { label: 'Đã ký', value: String(cnt('signed')), sub: f.status || f.q ? 'Theo bộ lọc hiện tại' : 'Tất cả hợp đồng' },
    ])}</div>
    <h2 class="sr-only">Danh sách hợp đồng</h2>
    <section class="hm-split panel sp-split" aria-label="Danh sách hợp đồng">
      <div class="hm-list">
        <div class="hm-toolbar">
          <div class="hm-views" role="group" aria-label="Chế độ xem">${CT_VIEWS.map(([v, t]) => `<button class="hm-view" type="button" data-ct-view="${v}" aria-pressed="${f.status === v}">${esc(t)}</button>`).join('')}</div>
          ${spCan('editor') ? '<button class="btn btn-primary btn-sm" type="button" data-ct-new><i data-lucide="plus"></i>Thêm hợp đồng</button>' : ''}
        </div>
        <div class="sp-search-row">
          <label class="sr-only" for="ctQ">Tìm hợp đồng</label>
          <input id="ctQ" type="search" data-ct-filter="q" value="${esc(f.q)}" placeholder="Mã hoặc tên hợp đồng">
        </div>
        ${ctState.error ? `${uiError(ctState.error)}` : ''}
        ${groups ? `<ul class="hm-rows" id="ctList" role="listbox" tabindex="0" aria-label="Hợp đồng, nhóm theo trạng thái">${groups}</ul>`
          : uiEmpty(spCan('editor') ? 'Chưa có hợp đồng khớp bộ lọc. Bấm “Thêm hợp đồng”, hoặc tạo từ một khoản Opex.' : 'Chưa có hợp đồng khớp bộ lọc.')}
        <div class="hm-hint"><span>${list.length} hợp đồng</span><span><kbd>J</kbd> <kbd>K</kbd> chuyển</span>
          <span>Mỗi lần upload tạo một version mới, không sửa được.</span></div>
      </div>
      <div class="hm-detail sp-detail" id="ctDetail" aria-live="polite" aria-labelledby="spPaneTitle">${list.length ? uiNote('Đang mở…') : uiEmpty('Chọn một hợp đồng ở danh sách bên trái.')}</div>
    </section>`;
  refreshIcons();
}

/* Render the list and open the selected (or first) contract in the pane. */
async function ctShow() {
  ctRender();
  const list = ctState.list || [];
  if (!list.length || !matchMedia('(min-width: 1024px)').matches) return;
  await ctOpenDetail(list.some(c => c.code === ctState.selected) ? ctState.selected : list[0].code);
}

let ctSearchTimer = null;
function ctBind() {
  if (ctState.bound) return;
  ctState.bound = true;
  const mount = $('#spendMount');
  mount.addEventListener('change', async (e) => {
    const el = e.target.closest('[data-ct-filter]');
    if (!el || el.dataset.ctFilter === 'q') return;
    ctState.filter[el.dataset.ctFilter] = el.value; await ctLoad(); await ctShow();
  });
  mount.addEventListener('input', (e) => {
    const el = e.target.closest('[data-ct-filter="q"]');
    if (!el) return;
    clearTimeout(ctSearchTimer);
    ctSearchTimer = setTimeout(async () => {
      ctState.filter.q = el.value.trim(); await ctLoad(); await ctShow();
      const box = $('[data-ct-filter="q"]'); if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
    }, 350);
  });
  mount.addEventListener('click', async (e) => {
    if (spState.sub !== 'contracts') return;
    const view = e.target.closest('[data-ct-view]');
    if (view) { ctState.filter.status = view.dataset.ctView; await ctLoad(); return ctShow(); }
    const row = e.target.closest('[data-ct-code]');
    if (row) return ctOpenDetail(row.dataset.ctCode);
    if (e.target.closest('[data-ct-new]')) return ctOpenCreate();
  });
  mount.addEventListener('keydown', (e) => {
    if (spState.sub !== 'contracts' || !e.target.closest('#ctList')) return;
    uiListKeys(e, 'ctCode', ctState.selected, (code, focus) => ctOpenDetail(code, focus));
  });
}

async function ctRefresh() {
  if (appState.view === 'spend' && spState.sub === 'contracts') { await ctLoad(); ctRender(); }
  else if (appState.view === 'spend' && spState.sub === 'payments' && typeof renderPaymentsPage === 'function') await renderPaymentsPage(true);
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
  uiPanelDialog('Thêm hợp đồng', body, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
    <button class="btn btn-primary btn-sm" type="button" id="ctSave">Tạo</button>`);
  spOnce('#ctSave', async () => {
    const fd = Object.fromEntries(new FormData($('#ctForm')).entries());
    const payload = { title: fd.title, note: fd.note, vendorName: fd.vendorName || undefined };
    payload[fd.kind === 'capex' ? 'capexCode' : 'opexCode'] = fd.code.trim();
    try { const c = await apiRequest('/spend/contracts', { method: 'POST', body: JSON.stringify(payload) }); $('#spDialog').close(); ctState.selected = c.code; await ctRefresh(); ctOpenDetail(c.code); }
    catch (err) { spMsg(err.message); }
  });
}

/* ---------- Detail ---------- */
async function ctOpenDetail(code, focus) {
  let c;
  try { await spLoadMeta(); c = await apiRequest(`/spend/contracts/${encodeURIComponent(code)}`); }
  catch (err) { uiAlert(err.message); return; }
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
        <div>${v.file && !v.file.purged ? `<a href="/api/spend/files/${encodeURIComponent(v.file.id)}" download>${esc(v.file.name)}</a> <span class="muted-sub sr-inline">${fmtInt(v.file.sizeBytes / 1024)} KB</span>`
          : `<span class="muted-sub sr-inline">File đã xoá theo chính sách lưu trữ (còn giữ thông tin kiểm toán)</span>`}</div>
      </div></li>`).join('');
  const comparable = c.versions.filter(v => v.hasText).map(v => v.versionNo).sort((a, b) => a - b);
  const vOpts = (sel) => comparable.map(n => spOpt(n, `v${n}`, sel)).join('');
  const compare = comparable.length >= 2 ? `<div class="sp-inline ct-compare-bar">
      <label class="field"><span>Từ</span><select id="ctFrom">${vOpts(comparable[comparable.length - 2])}</select></label>
      <label class="field"><span>Đến</span><select id="ctTo">${vOpts(comparable[comparable.length - 1])}</select></label>
      <button class="btn btn-secondary btn-sm" type="button" id="ctCompare">So sánh</button>
      <button class="btn btn-secondary btn-sm hidden" type="button" data-ai-act="contract-diff" data-ai-gate="contract_diff" data-ai-code="${esc(c.code)}">AI tóm tắt thay đổi</button></div>
      <div id="ctAiDiff" class="ai-out" aria-live="polite"></div>
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
    <div id="ctPayments">${uiLoading('Đang tải…')}</div>
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
  ctState.selected = c.code;
  const host = uiPaneOrDialog('#ctDetail', esc(c.title), c.code, body, actions);
  uiListSelect('#ctList', 'ctCode', c.code, focus);
  if (typeof payRender === 'function') payRender(c.code);
  const reopen = async () => { await ctRefresh(); await ctOpenDetail(c.code); };
  const post = (path, payload) => apiRequest(`/spend/contracts/${encodeURIComponent(c.code)}/${path}`, { method: 'POST', body: JSON.stringify(payload || {}) });
  host.onclick = (e) => {
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
  if (typeof aiGate === 'function') aiGate(host);
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
    try {
      await apiRequest(`/spend/contracts/${encodeURIComponent(c.code)}`, { method: 'DELETE' });
      if ($('#spDialog').open) $('#spDialog').close();
      ctState.selected = null;
      if (appState.view === 'spend' && spState.sub === 'contracts') { await ctLoad(); await ctShow(); } else await ctRefresh();
    }
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
