/* =========================================================
   Cổng Owner (ICO v2 G2c): the whole UI of an owner-scope account (Entra app role ICO.Owner only).
   Separate shell — no menu, no app data: only /api/auth/me, /api/owner/* and sign-out (the server refuses the rest).
   ========================================================= */
const opState = { systems: [], vms: [], system: '' };

function showOwnerShell(user) {
  $('#loginScreen').classList.add('hidden');
  $('#appShell').classList.add('hidden');
  $('#ownerShell').classList.remove('hidden');
  $('#ownerUserLabel').textContent = user.displayName || user.username;
  document.title = 'Cổng Owner — Infra Capacity Ops Management';
  const m = /^#\/owner-review\?c=(\d+)/.exec(location.hash);       // e-mail link already rewritten for the app shell
  if (m) history.replaceState(null, '', `#/owner/review/${m[1]}`);
  ownerShellRoute();
}

async function renderOwnerPortal() {
  const mount = $('#ownerMount');
  mount.innerHTML = uiLoading();
  try {
    const q = opState.system ? `?system=${encodeURIComponent(opState.system)}` : '';
    const [sys, vms, rv] = await Promise.all([apiRequest('/owner/systems'), apiRequest(`/owner/vms${q}`), apiRequest('/owner/reviews')]);
    opState.systems = sys.items; opState.vms = vms.items; opState.reviews = rv.items;
  } catch (err) { mount.innerHTML = uiError(err.message); return; }
  if (!opState.systems.length) {
    mount.innerHTML = `<section class="panel">${uiEmpty('Tài khoản của bạn chưa là owner hay người thay thế của hệ thống nào. Liên hệ nhóm hạ tầng nếu đây là nhầm lẫn.')}</section>`;
    return;
  }
  const opt = (v, t) => `<option value="${esc(v)}"${String(v) === String(opState.system) ? ' selected' : ''}>${esc(t)}</option>`;
  const cards = opState.systems.map(s => `<tr><td><b>${esc(s.name)}</b><span class="muted-sub mono">${esc(s.code)}</span></td>
      <td>${esc([s.project, s.department].filter(Boolean).join(' · ') || '—')}</td><td>${uiTag(s.criticalityLabel)}</td>
      <td>${esc(s.envLabel)}</td><td class="num">${fmtInt(s.vmCount)}</td></tr>`).join('');
  const rows = opState.vms.map(v => `<tr><td><b>${esc(v.vm)}</b><span class="muted-sub">${esc(v.vcenterKey)} · ${esc(v.cluster || '—')}</span></td>
      <td>${esc(v.systemCode)}</td><td>${v.powerOn ? uiPill('ok', 'Bật') : uiPill('na', 'Tắt')}</td>
      <td class="num">${esc(uiNumber(v.vcpu))}</td><td class="num">${esc(uiNumber(v.ramGB))}</td></tr>`).join('');
  const reviews = (opState.reviews || []).map(r => `<li class="home-item"><div class="home-item-main">
      <a class="home-item-title" href="#/owner/review/${esc(r.id)}">Rà soát ${esc(r.periodLabel)} — ${fmtInt(r.decided)} / ${fmtInt(r.items)} VM đã xác nhận</a>
      <p class="home-item-detail">${r.status === 'open' ? `Hạn ${esc(spDateSafe(r.dueAt))}` : 'Đã đóng'}</p></div>
      ${r.status === 'open' && r.decided < r.items ? uiPill('warn', 'Cần xác nhận') : uiPill('ok', r.status === 'open' ? 'Đã xác nhận đủ' : 'Đã đóng')}</li>`).join('');
  mount.innerHTML = `
    ${reviews ? `<section class="panel"><div class="panel-title" role="heading" aria-level="2">Rà soát định kỳ</div><ul class="home-list">${reviews}</ul></section>` : ''}
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Hệ thống của bạn (${fmtInt(opState.systems.length)})</div>
      <p class="panel-sub">Hệ thống bạn là owner hoặc người thay thế. Nhóm hạ tầng dùng danh sách này để hỏi bạn xác nhận VM trong đợt rà soát 6 tháng.</p>
      <div class="table-scroll" role="region" tabindex="0" aria-label="Hệ thống"><table class="data-table">
        <thead><tr><th>Hệ thống</th><th>Dự án · phòng ban</th><th>Mức quan trọng</th><th>Môi trường</th><th class="num">VM</th></tr></thead>
        <tbody>${cards}</tbody></table></div></section>
    <section class="table-card"><div class="panel-head"><div class="panel-title" role="heading" aria-level="2">VM (${fmtInt(opState.vms.length)})</div>
      <div class="panel-toolbar"><select id="opSystem" aria-label="Lọc theo hệ thống">${opt('', 'Mọi hệ thống của bạn')}${opState.systems.map(s => opt(s.id, `${s.code} — ${s.name}`)).join('')}</select></div></div>
      <div class="table-scroll" role="region" tabindex="0" aria-label="VM"><table class="data-table">
        <thead><tr><th>VM</th><th>Hệ thống</th><th>Nguồn</th><th class="num">vCPU</th><th class="num">RAM (GB)</th></tr></thead>
        <tbody>${rows || uiEmptyRow(5, 'Hệ thống chưa có VM nào được gán.')}</tbody></table></div></section>`;
  $('#opSystem').onchange = (e) => { opState.system = e.target.value; renderOwnerPortal(); };
}

document.addEventListener('click', async (e) => {
  if (!e.target.closest('#ownerLogout')) return;
  try { const r = await api.logout(); if (r && r.logoutUrl) { location.href = r.logoutUrl; return; } } catch (err) { /* already signed out */ }
  location.reload();
});

/* ---------------------------------------------------------------- Rà soát của owner (G3b): #/owner/review/<id>
   Shared by the owner shell and the full app (an owner may also be a full user). Mobile first: one card per VM. */
const orvState = { mount: null, cid: null, data: null, sel: new Set() };
const ORV_DECISION_HELP = { keep: '', decommission: 'Ngày thu hồi dự kiến', resize: 'vCPU / RAM đề xuất', transfer: 'Email owner mới', not_mine: '' };

function orvFields(prefix, d, it) {
  const sig = (it && it.signals) || {};
  const today = new Date(Date.now() + 7 * 3600e3).toISOString().slice(0, 10);
  return `<div class="orv-extra" data-orv-extra="decommission"${d === 'decommission' ? '' : ' hidden'}>
      <label class="field"><span>Ngày thu hồi dự kiến</span><input type="date" id="${prefix}Date" min="${today}" value="${esc((it && it.actionDate) || '')}"></label></div>
    <div class="orv-extra orv-two" data-orv-extra="resize"${d === 'resize' ? '' : ' hidden'}>
      <label class="field"><span>vCPU đề xuất</span><input type="number" min="1" step="1" id="${prefix}Vcpu" value="${esc((it && it.newVcpu) || '')}" placeholder="${esc(sig.vcpu || '')}"></label>
      <label class="field"><span>RAM đề xuất (GB)</span><input type="number" min="1" step="1" id="${prefix}Ram" value="${esc((it && it.newRamGB) || '')}" placeholder="${esc(sig.ramGB || '')}"></label></div>
    <div class="orv-extra" data-orv-extra="transfer"${d === 'transfer' ? '' : ' hidden'}>
      <label class="field"><span>Email owner mới</span><input type="email" id="${prefix}To" maxlength="255" value="${esc((it && it.transferTo) || '')}" autocomplete="off"></label></div>
    <label class="field"><span>Ghi chú</span><input id="${prefix}Note" maxlength="500" value="${esc((it && it.note) || '')}"></label>`;
}

function orvPayload(prefix, decision) {
  const v = (id) => { const el = document.getElementById(prefix + id); return el ? el.value.trim() : ''; };
  const num = (id) => (v(id) ? Number(v(id)) : null);
  return { decision, actionDate: v('Date') || null, newVcpu: num('Vcpu'), newRamGB: num('Ram'), transferTo: v('To'), note: v('Note') };
}

async function renderOwnerReview(mount, cid) {
  orvState.mount = mount; orvState.cid = cid; orvState.sel = new Set();
  mount.innerHTML = uiLoading();
  try { orvState.data = await apiRequest(`/owner/reviews/${encodeURIComponent(cid)}`); }
  catch (err) { mount.innerHTML = `<section class="panel">${uiEmpty(err.message)}</section>`; return; }
  orvRender();
}

/* Read-only one-line summary of a decision: date, new size, new owner, note. */
function orvDetail(i, label) {
  const parts = [label[i.decision] || i.decision];
  if (i.decision === 'decommission' && i.actionDate) parts[0] += ` ngày ${spDateSafe(i.actionDate)}`;
  if (i.decision === 'resize') parts[0] += ` còn ${[i.newVcpu ? `${uiNumber(i.newVcpu)} vCPU` : '', i.newRamGB ? `${uiNumber(i.newRamGB)} GB RAM` : ''].filter(Boolean).join(' · ')}`;
  if (i.decision === 'transfer' && i.transferTo) parts[0] += ` cho ${i.transferTo}`;
  if (i.note) parts.push(i.note);
  return parts.join(' — ');
}

function orvRender() {
  const d = orvState.data, mount = orvState.mount, edit = d.editable;
  const opt = (v, t, cur) => `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc(t)}</option>`;
  const label = Object.fromEntries(d.decisions.map(x => [x.value, x.label]));
  const done = d.items.filter(i => i.decision).length;
  const cards = d.items.map(i => {
    const s = i.signals || {};
    const p = `orv${i.id}`;
    return `<article class="orv-card${i.flagged ? ' is-flagged' : ''}" data-orv-item="${esc(i.id)}">
      <div class="orv-head">${edit ? `<input type="checkbox" class="orv-pick" data-orv-sel="${esc(i.id)}"${orvState.sel.has(i.id) ? ' checked' : ''} aria-label="Chọn ${esc(i.vm)}">` : ''}
        <div class="orv-title"><b>${esc(i.vm)}</b><span class="muted-sub">${esc(i.systemCode || '—')} · ${esc(i.vcenterKey)}</span></div>
        ${i.decision ? uiPill('ok', label[i.decision] || i.decision) : uiPill('warn', 'Chưa xác nhận')}</div>
      <p class="orv-meta">${esc(uiNumber(s.vcpu))} vCPU · ${esc(uiNumber(s.ramGB))} GB RAM · ${esc(uiNumber(s.diskProvGB))} GB disk${s.poweredOn === false ? ' · đang tắt' : ''}</p>
      ${i.flagged ? `<p class="orv-reason">${uiTag('Nên rà')} ${esc(i.reasons.join('; '))}</p>` : ''}
      ${edit ? `<div class="orv-form">
        <label class="field"><span>Quyết định</span><select id="${p}Dec" data-orv-dec="${p}">${opt('', '— Chọn —', i.decision || '')}${d.decisions.map(x => opt(x.value, x.label, i.decision || '')).join('')}</select></label>
        ${orvFields(p, i.decision || '', i)}
        <button class="btn btn-primary btn-sm" type="button" data-orv-save="${esc(i.id)}">Lưu</button></div>`
        : (i.decision ? `<p class="muted-sub">${esc(orvDetail(i, label))}</p>` : '')}
    </article>`;
  }).join('');
  mount.innerHTML = `
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Rà soát ${esc(d.periodLabel)}</div>
      <p class="panel-sub">${edit ? `Xác nhận từng VM trước <b>${esc(spDateSafe(d.dueAt))}</b>: còn dùng, thu hồi, giảm cấu hình, chuyển owner hoặc không phải của bạn.`
        : 'Kỳ rà soát đã đóng — quyết định còn thiếu do team hạ tầng xác nhận.'}</p>
      <p class="orv-progress">${uiPill(done === d.items.length ? 'ok' : 'warn', `${fmtInt(done)} / ${fmtInt(d.items.length)} VM đã xác nhận`)}</p>
      ${edit ? `<div class="orv-bulk" id="orvBulk"></div>` : ''}
      <p class="criteria-status" id="orvMsg" role="status"></p></section>
    <div class="orv-list">${cards || uiEmpty('Không có VM nào trong kỳ này.')}</div>`;
  orvBulkBar();
  orvBind();
  refreshIcons();
}

function orvBulkBar() {
  const bar = $('#orvBulk');
  if (!bar) return;
  const n = orvState.sel.size, d = orvState.data;
  const opt = (v, t) => `<option value="${esc(v)}">${esc(t)}</option>`;
  bar.innerHTML = `<label class="infra-check"><input type="checkbox" id="orvAll"${n && n === d.items.length ? ' checked' : ''}> Chọn tất cả (${fmtInt(d.items.length)})</label>
    ${n ? `<div class="orv-form"><label class="field"><span>Quyết định cho ${fmtInt(n)} VM</span><select id="orvBDec" data-orv-dec="orvB">${opt('', '— Chọn —')}${d.decisions.map(x => opt(x.value, x.label)).join('')}</select></label>
      ${orvFields('orvB', '', null)}<button class="btn btn-primary btn-sm" type="button" id="orvBSave">Áp dụng cho ${fmtInt(n)} VM</button></div>` : ''}`;
}

async function orvSave(ids, prefix) {
  const dec = $(`#${prefix}Dec`).value;
  if (!dec) { uiSetStatus('#orvMsg', 'Chọn quyết định trước.', 'error'); return; }
  try {
    await apiRequest(`/owner/reviews/${encodeURIComponent(orvState.cid)}/items`, { method: 'PUT', body: JSON.stringify({ itemIds: ids, ...orvPayload(prefix, dec) }) });
    orvState.data = await apiRequest(`/owner/reviews/${encodeURIComponent(orvState.cid)}`);
    orvState.sel = new Set();
    orvRender();
    uiSetStatus('#orvMsg', `Đã lưu ${fmtInt(ids.length)} VM.`, 'ok');
  } catch (err) { uiSetStatus('#orvMsg', err.message, 'error'); }
}

function orvBind() {
  const mount = orvState.mount;
  mount.onchange = (e) => {
    const t = e.target;
    if (t.dataset.orvDec) {                 // show the fields of the chosen decision
      const box = t.closest('.orv-form');
      box.querySelectorAll('[data-orv-extra]').forEach(x => { x.hidden = x.dataset.orvExtra !== t.value; });
      return;
    }
    if (t.dataset.orvSel) { const id = Number(t.dataset.orvSel); if (t.checked) orvState.sel.add(id); else orvState.sel.delete(id); orvBulkBar(); return; }
    if (t.id === 'orvAll') { orvState.sel = new Set(t.checked ? orvState.data.items.map(i => i.id) : []); $$('[data-orv-sel]').forEach(c => { c.checked = t.checked; }); orvBulkBar(); }
  };
  mount.onclick = (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.orvSave) orvSave([Number(b.dataset.orvSave)], `orv${b.dataset.orvSave}`);
    else if (b.id === 'orvBSave') orvSave([...orvState.sel], 'orvB');
  };
}

/* Owner shell routing: #/owner/review/<id> opens the review, anything else the portal home. */
function ownerShellRoute() {
  const m = /^#\/owner\/review\/(\d+)/.exec(location.hash);
  if (m) renderOwnerReview($('#ownerMount'), Number(m[1])); else renderOwnerPortal();
}
window.addEventListener('hashchange', () => { if ($('#ownerShell') && !$('#ownerShell').classList.contains('hidden')) ownerShellRoute(); });

/* Full app: same page as a view (#/owner-review?c=<id>); the e-mail link #/owner/review/<id> is rewritten to it. */
function renderOwnerReviewView() {
  const c = new URLSearchParams((location.hash.split('?')[1]) || '').get('c');
  const mount = $('#ownerReviewMount');
  if (!mount) return;
  if (!/^\d+$/.test(c || '')) { mount.innerHTML = uiEmpty('Chưa chọn kỳ rà soát — mở từ email hoặc Cổng Owner.'); return; }
  renderOwnerReview(mount, Number(c));
}
(function rewriteReviewLink() {
  const m = /^#\/owner\/review\/(\d+)/.exec(location.hash);
  if (m) history.replaceState(null, '', `#/owner-review?c=${m[1]}`);
}());
