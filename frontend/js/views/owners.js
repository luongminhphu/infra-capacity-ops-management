/* =========================================================
   Quản lý thông tin → Hệ thống & Owner (ICO v2 G2a): hệ thống nghiệp vụ, owner (Team Lead, email Entra),
   VM thuộc hệ thống; tab "VM chưa có owner" (độ phủ). Data: /api/systems*, nhập / xuất Excel cùng mẫu.
   Ghi: admin hoặc người có quyền "Quản trị Hệ thống & Owner" (Cài đặt → Người dùng & quyền).
   ========================================================= */
const osState = { sub: 'systems', list: null, total: 0, facets: null, cov: null, error: null, sel: new Set(), file: null,
  f: { q: '', owner: '', department: '', criticality: '', env: '' }, covQ: '' };
const OS_CRIT = { 1: 'crit', 2: 'bad', 3: 'warn', 4: 'ok' };

const osCan = () => !!((osState.facets && osState.facets.canManage) || uiIsAdmin() || (currentUser && currentUser.ownerAdmin));

async function osLoad() {
  try {
    const f = osState.f, qs = new URLSearchParams({ limit: '500' });
    Object.entries(f).forEach(([k, v]) => { if (v) qs.set(k, v); });
    const [list, facets, cov] = await Promise.all([apiRequest(`/systems?${qs}`), osState.facets ? osState.facets : apiRequest('/systems/facets'),
      apiRequest(`/systems/coverage?${new URLSearchParams({ q: osState.covQ, limit: '500' })}`)]);
    osState.list = list.items; osState.facets = facets; osState.cov = cov; osState.error = null;
  } catch (err) { osState.error = err.message; }
}

async function renderOwnersView() {
  const mount = $('#ownersMount');
  if (!mount) return;
  osBind();
  if (!osState.list) { mount.innerHTML = uiLoading(); await osLoad(); }
  osRender();
}

function osKpis() {
  const c = osState.cov;
  return uiKpiStrip([
    { label: 'Hệ thống', valueHtml: `${fmtInt(osState.list.length)}<small>đang hoạt động</small>`,
      subHtml: `${fmtInt(osState.list.filter(s => !s.ownerEmail).length)} hệ thống chưa có owner` },
    { label: 'VM có owner', valueHtml: c.ownedPct === null ? '—' : `${esc(uiNumber(c.ownedPct, 1))}<small>%</small>`,
      subHtml: `${fmtInt(c.owned)} / ${fmtInt(c.total)} VM trong sổ CI` },
    { label: 'VM chưa có owner', valueHtml: `${fmtInt(c.unassigned + c.inSystemNoOwner)}<small>VM</small>`,
      subHtml: `${fmtInt(c.unassigned)} chưa thuộc hệ thống · ${fmtInt(c.inSystemNoOwner)} thuộc hệ thống chưa có owner` },
  ]);
}

function osHead() {
  const can = osCan();
  return `<div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Hệ thống & Owner</div>
      <p class="panel-sub">Owner là Team Lead đại diện nhóm dự án / phòng ban (email Entra). Mỗi VM thuộc tối đa một hệ thống; gán tay luôn thắng gán tự động.</p></div>
    <div class="panel-toolbar infra-toolbar">${can ? `<button class="btn btn-secondary btn-sm" type="button" data-os-add><i data-lucide="plus"></i>Thêm hệ thống</button>
      <button class="btn btn-secondary btn-sm" type="button" data-os-import><i data-lucide="upload"></i>Nhập Excel</button>` : ''}
      <button class="btn btn-ghost btn-sm" type="button" data-os-export><i data-lucide="download"></i>Xuất Excel</button>
      <button class="btn btn-ghost btn-sm" type="button" data-os-reload><i data-lucide="refresh-cw"></i>Tải lại</button></div></div>`;
}

function osRender() {
  const mount = $('#ownersMount');
  if (!mount) return;
  if (osState.error) { mount.innerHTML = uiError(osState.error); return; }
  const act = document.activeElement, keep = act && mount.contains(act) && act.id ? [act.id, act.selectionStart] : null;
  mount.innerHTML = osKpis() + (osState.sub === 'unowned' ? osUnownedHtml() : osSystemsHtml());
  refreshIcons();
  if (keep) {                                   // filters re-render the panel: keep the caret in the box being typed in
    const el = document.getElementById(keep[0]);
    if (el) { el.focus(); if (typeof keep[1] === 'number' && el.setSelectionRange) el.setSelectionRange(keep[1], keep[1]); }
  }
  if (osState.sub === 'unowned') osBulkBar();
}

function osSystemsHtml() {
  const f = osState.f, fc = osState.facets;
  const opt = (v, t, cur) => `<option value="${esc(v)}"${String(v) === String(cur) ? ' selected' : ''}>${esc(t)}</option>`;
  const rows = osState.list.map(s => `<tr class="infra-row" tabindex="0" data-os-id="${esc(s.id)}">
      <td><b>${esc(s.name)}</b><span class="muted-sub mono">${esc(s.code)}</span></td>
      <td>${esc(s.project || '—')}<span class="muted-sub">${esc(s.department || '')}</span></td>
      <td>${s.ownerEmail ? (s.ownerName ? `${esc(s.ownerName)}<span class="muted-sub">${esc(s.ownerEmail)}</span>` : esc(s.ownerEmail)) : uiPill('warn', 'Chưa có owner')}</td>
      <td>${uiTag(s.criticalityLabel)}</td><td>${esc(s.envLabel)}</td><td class="num">${fmtInt(s.vmCount)}</td></tr>`).join('');
  return `<section class="panel">${osHead()}
      <div class="panel-toolbar infra-toolbar dc-filters">
        <input type="search" id="osQ" placeholder="Tìm mã, tên, dự án…" value="${esc(f.q)}" aria-label="Tìm hệ thống">
        <input type="search" id="osOwner" placeholder="Email / tên owner" value="${esc(f.owner)}" aria-label="Lọc theo owner">
        <select id="osDept" aria-label="Phòng ban">${opt('', 'Mọi phòng ban', f.department)}${fc.departments.map(d => opt(d, d, f.department)).join('')}</select>
        <select id="osCrit" aria-label="Mức độ quan trọng">${opt('', 'Mọi mức quan trọng', f.criticality)}${fc.criticality.map(c => opt(c.value, c.label, f.criticality)).join('')}</select>
        <select id="osEnv" aria-label="Môi trường">${opt('', 'Mọi môi trường', f.env)}${fc.envs.map(e => opt(e.value, e.label, f.env)).join('')}</select>
      </div>
      <div class="table-scroll" role="region" tabindex="0" aria-label="Hệ thống"><table class="data-table infra-table">
        <caption class="sr-only">Hệ thống nghiệp vụ và owner</caption>
        <thead><tr><th>Hệ thống</th><th>Dự án · phòng ban</th><th>Owner</th><th>Mức quan trọng</th><th>Môi trường</th><th class="num">VM</th></tr></thead>
        <tbody>${rows || uiEmptyRow(6, osCan() ? 'Chưa có hệ thống — bấm “Thêm hệ thống” hoặc “Nhập Excel”.' : 'Chưa có hệ thống khớp bộ lọc.')}</tbody></table></div>
      <p class="muted-sub">${fmtInt(osState.list.length)} hệ thống${osState.list.length >= 500 ? ' (hiện 500 dòng đầu — lọc để thu hẹp)' : ''}. Mức quan trọng 1 = quan trọng nhất.</p>
    </section>`;
}

function osUnownedHtml() {
  const c = osState.cov, can = osCan();
  const rows = c.items.map(v => `<tr>${can ? `<td class="dc-pick"><input type="checkbox" data-os-sel="${esc(v.ciId)}"${osState.sel.has(v.ciId) ? ' checked' : ''} aria-label="Chọn ${esc(v.vm)}"></td>` : ''}
      <td>${uiEntityLink('vm', v.vm, v.vcenterKey, false)}</td><td>${esc(v.vcenterKey || '—')}</td><td>${esc(v.cluster || '—')}</td>
      <td>${v.powerOn ? uiPill('ok', 'Bật') : uiPill('na', 'Tắt')}</td></tr>`).join('');
  return `<section class="panel"><div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">VM chưa thuộc hệ thống (${fmtInt(c.itemsTotal)})</div>
      <p class="panel-sub">VM đang có trong sổ CI nhưng chưa gán hệ thống. Gán ở đây, trên thẻ VM, hoặc bằng file Excel (sheet VM).</p></div>
      <div class="panel-toolbar infra-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-os-reload><i data-lucide="refresh-cw"></i>Tải lại</button></div></div>
      <div class="panel-toolbar infra-toolbar dc-filters"><input type="search" id="osCovQ" placeholder="Tìm tên VM…" value="${esc(osState.covQ)}" aria-label="Tìm VM"></div>
      ${can ? '<div class="dc-bulk" id="osBulk" role="status"></div>' : ''}
      <div class="table-scroll" role="region" tabindex="0" aria-label="VM chưa có owner"><table class="data-table infra-table">
        <caption class="sr-only">VM chưa thuộc hệ thống</caption>
        <thead><tr>${can ? '<th class="dc-pick"></th>' : ''}<th>VM</th><th>vCenter</th><th>Cluster</th><th>Nguồn</th></tr></thead>
        <tbody>${rows || uiEmptyRow(can ? 5 : 4, 'Mọi VM đã thuộc một hệ thống.')}</tbody></table></div>
      <p class="muted-sub">${c.itemsTotal > c.items.length ? `Hiện ${fmtInt(c.items.length)} / ${fmtInt(c.itemsTotal)} VM — tìm để thu hẹp. ` : ''}Độ phủ owner tính trên VM đang hoạt động của sổ CI.</p></section>`;
}

function osBulkBar() {
  const bar = $('#osBulk');
  if (!bar) return;
  const n = osState.sel.size;
  bar.innerHTML = n ? `<span><b>${fmtInt(n)}</b> VM đã chọn</span>
      <button class="btn btn-primary btn-sm" type="button" data-os-bulk>Gán vào hệ thống</button>
      <button class="btn btn-ghost btn-sm" type="button" data-os-clear>Bỏ chọn</button>`
    : '<span class="muted-sub">Đánh dấu ô đầu dòng để gán nhiều VM vào một hệ thống.</span>';
  bar.classList.toggle('active', n > 0);
}

/* Pick a system and assign the VMs (shared with the VM card). A VM already assigned by hand elsewhere: asks before moving. */
async function osAssignDialog(ciIds, label, onDone) {
  let systems;
  try { systems = (await apiRequest('/systems?limit=500')).items; } catch (err) { uiAlert(err.message); return; }
  if (!systems.length) { uiAlert('Chưa có hệ thống nào — tạo hệ thống trước (Quản lý thông tin → Hệ thống & Owner).'); return; }
  uiPanelDialog(`Gán hệ thống — ${esc(label)}`, `
    <div class="form-grid-aligned dc-form">
      <label class="field sp-span2"><span>Hệ thống</span><select id="osaSys">${systems.map(s => `<option value="${esc(s.id)}">${esc(`${s.code} — ${s.name}${s.ownerEmail ? ` (${s.ownerEmail})` : ''}`)}</option>`).join('')}</select></label>
    </div>
    <label class="infra-check"><input type="checkbox" id="osaMove"> Chuyển sang hệ thống này nếu VM đã thuộc hệ thống khác</label>`,
  `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
   <button class="btn btn-primary btn-sm" type="button" id="osaSave">Gán</button>`);
  $('#osaSave').onclick = async () => {
    $('#osaSave').disabled = true;
    try {
      const r = await apiRequest(`/systems/${$('#osaSys').value}/members`, { method: 'POST', body: JSON.stringify({ ciIds, move: $('#osaMove').checked }) });
      $('#spDialog').close();
      uiAlert(`Đã gán ${fmtInt(r.added)} VM${r.moved ? `, chuyển ${fmtInt(r.moved)} VM` : ''}${r.unchanged ? `; ${fmtInt(r.unchanged)} VM đã ở hệ thống này` : ''}.`);
      if (onDone) await onDone();
    } catch (err) { uiSetStatus($('#spDialogMsg'), err.message, 'error'); $('#osaSave').disabled = false; }
  };
}

function osForm(s) {
  const v = s || { criticality: 3, env: 'prod' }, fc = osState.facets;
  const inp = (id, label, val, extra = '') => `<label class="field"><span>${esc(label)}</span><input id="${id}" value="${esc(val ?? '')}" ${extra}></label>`;
  const opt = (val, t, cur) => `<option value="${esc(val)}"${String(val) === String(cur) ? ' selected' : ''}>${esc(t)}</option>`;
  uiPanelDialog(s ? `Sửa hệ thống — ${esc(s.code)}` : 'Thêm hệ thống', `
    <div class="form-grid-aligned dc-form">
      ${inp('osfName', 'Tên hệ thống', v.name, 'maxlength="255" required')}
      ${inp('osfProject', 'Dự án', v.project, 'maxlength="128" list="osProjects"')}
      ${inp('osfDept', 'Phòng ban', v.department, 'maxlength="128" list="osDepts"')}
      ${inp('osfOwner', 'Owner (email)', v.ownerEmail, 'type="email" maxlength="255" autocomplete="off" placeholder="team.lead@congty.vn"')}
      ${inp('osfOwnerName', 'Tên owner', v.ownerName, 'maxlength="128"')}
      ${inp('osfDeputy', 'Người thay thế (email)', v.deputyEmail, 'type="email" maxlength="255" autocomplete="off"')}
      ${inp('osfManager', 'Quản lý trực tiếp (email)', v.managerEmail, 'type="email" maxlength="255" autocomplete="off"')}
      <label class="field"><span>Mức độ quan trọng</span><select id="osfCrit">${fc.criticality.map(c => opt(c.value, c.label, v.criticality)).join('')}</select></label>
      <label class="field"><span>Môi trường</span><select id="osfEnv">${fc.envs.map(e => opt(e.value, e.label, v.env)).join('')}</select></label>
      ${inp('osfCost', 'Trung tâm chi phí', v.costCenter, 'maxlength="64"')}
      <label class="field sp-span2"><span>Ghi chú</span><input id="osfNote" maxlength="500" value="${esc(v.note || '')}"></label>
    </div>
    <datalist id="osProjects">${fc.projects.map(p => `<option value="${esc(p)}">`).join('')}</datalist>
    <datalist id="osDepts">${fc.departments.map(d => `<option value="${esc(d)}">`).join('')}</datalist>
    <p class="muted-sub dc-form-help">Owner là Team Lead đại diện nhóm dự án / phòng ban, dùng email đăng nhập Microsoft (UPN). Đổi owner được ghi nhật ký.</p>`,
  `${s && osCan() ? '<button class="btn btn-ghost btn-sm btn-danger" type="button" id="osfDel">Xoá hệ thống</button>' : ''}
   <span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
   <button class="btn btn-primary btn-sm" type="button" id="osfSave">${s ? 'Lưu' : 'Thêm'}</button>`);
  $('#osfSave').onclick = async () => {
    const g = (id) => $(id).value.trim();
    const body = { name: g('#osfName'), project: g('#osfProject'), department: g('#osfDept'), ownerEmail: g('#osfOwner'), ownerName: g('#osfOwnerName'),
      deputyEmail: g('#osfDeputy'), managerEmail: g('#osfManager'), criticality: Number(g('#osfCrit')), env: g('#osfEnv'),
      costCenter: g('#osfCost'), note: g('#osfNote') };
    $('#osfSave').disabled = true;
    try {
      await apiRequest(s ? `/systems/${s.id}` : '/systems', { method: s ? 'PUT' : 'POST', body: JSON.stringify(body) });
      $('#spDialog').close();
      osState.facets = null; await osLoad(); osRender();
    } catch (err) { uiSetStatus($('#spDialogMsg'), err.message, 'error'); $('#osfSave').disabled = false; }
  };
  const del = $('#osfDel');
  if (del) del.onclick = async () => {
    if (!await uiConfirm(`Xoá hệ thống ${s.code} — ${s.name}? ${fmtInt(s.vmCount || 0)} VM sẽ thành chưa có owner.`, { danger: true })) return;
    try { await apiRequest(`/systems/${s.id}`, { method: 'DELETE' }); $('#spDialog').close(); await osLoad(); osRender(); }
    catch (err) { uiSetStatus($('#spDialogMsg'), err.message, 'error'); }
  };
}

async function osOpen(id) {
  const s = osState.list.find(x => x.id === id);
  if (!s) return;
  let vms = [];
  try { vms = (await apiRequest(`/systems/${id}/members`)).items; } catch (err) { uiAlert(err.message); return; }
  const can = osCan();
  const src = { manual: 'Gán tay', attribute: 'Thuộc tính vCenter', tag: 'vSphere tag', rule: 'Quy tắc', itsm: 'ServiceDesk' };
  const kv = (l, v) => `<div class="sp-dl-item"><dt>${esc(l)}</dt><dd>${v}</dd></div>`;
  uiPanelDialog(`${esc(s.code)} — ${esc(s.name)}`, `
    <dl class="sp-dl">
      ${kv('Owner', s.ownerEmail ? `${esc(s.ownerName || '')} <span class="mono">${esc(s.ownerEmail)}</span>` : uiPill('warn', 'Chưa có owner'))}
      ${kv('Người thay thế', esc(s.deputyEmail || '—'))}${kv('Quản lý trực tiếp', esc(s.managerEmail || '—'))}
      ${kv('Dự án · phòng ban', esc([s.project, s.department].filter(Boolean).join(' · ') || '—'))}
      ${kv('Mức quan trọng · môi trường', `${uiTag(s.criticalityLabel)} ${esc(s.envLabel)}`)}
      ${s.costCenter ? kv('Trung tâm chi phí', esc(s.costCenter)) : ''}${s.note ? kv('Ghi chú', esc(s.note)) : ''}
      ${kv('Cập nhật', esc(`${spTimeSafe(s.updatedAt)}${s.updatedBy ? ' · ' + s.updatedBy : ''}`))}
    </dl>
    <div class="sp-h">VM của hệ thống (${fmtInt(vms.length)})</div>
    <div class="table-scroll" role="region" tabindex="0" aria-label="VM của hệ thống"><table class="data-table">
      <thead><tr><th>VM</th><th>Cluster</th><th>Nguồn gán</th>${can ? '<th></th>' : ''}</tr></thead>
      <tbody>${vms.map(v => `<tr><td>${uiEntityLink('vm', v.vm, v.vcenterKey, false)}<span class="muted-sub">${esc(v.vcenterKey)}</span></td>
        <td>${esc(v.cluster || '—')}</td><td>${uiTag(src[v.source] || v.source)}<span class="muted-sub">${esc(v.assignedBy || '')}</span></td>
        ${can ? `<td><button class="btn btn-ghost btn-sm" type="button" data-os-unassign="${esc(v.ciId)}">Bỏ gán</button></td>` : ''}</tr>`).join('')
        || uiEmptyRow(can ? 4 : 3, 'Chưa có VM — gán ở tab “VM chưa có owner” hoặc trên thẻ VM.')}</tbody></table></div>`,
  `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Đóng</button>
   ${can ? '<button class="btn btn-primary btn-sm" type="button" id="osEdit"><i data-lucide="pencil"></i>Sửa</button>' : ''}`);
  const edit = $('#osEdit');
  if (edit) edit.onclick = () => osForm(s);
  $('#spDialogBody').onclick = async (e) => {
    const b = e.target.closest('[data-os-unassign]');
    if (!b) return;
    try {
      await apiRequest(`/systems/${id}/members`, { method: 'DELETE', body: JSON.stringify({ ciIds: [Number(b.dataset.osUnassign)] }) });
      await osLoad(); osRender(); osOpen(id);
    } catch (err) { uiSetStatus($('#spDialogMsg'), err.message, 'error'); }
  };
}

function osImportDialog() {
  osState.file = null;
  uiPanelDialog('Nhập Hệ thống & Owner từ Excel', `
    <p class="muted-sub">Sheet <b>Hệ thống</b>: Mã, Tên hệ thống, Dự án, Phòng ban, Owner (email), Tên owner, Người thay thế (email),
      Quản lý trực tiếp (email), Mức độ quan trọng (1–4), Môi trường (prod / uat / dev / dr / other). Để trống mã để tạo mới; có mã thì cập nhật.
      Sheet <b>VM</b>: VM, vCenter, Hệ thống (mã hoặc tên). Dùng file “Xuất Excel” làm mẫu.</p>
    ${uiDropzone('osFile', 'Chọn file .xlsx', 'Tối đa 10 MB', '.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}
    <div id="osPreview"></div>`,
  `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
   <button class="btn btn-secondary btn-sm" type="button" id="osPrev" disabled>Kiểm tra</button>
   <button class="btn btn-primary btn-sm" type="button" id="osCommit" disabled>Nhập</button>`);
  const msg = $('#spDialogMsg');
  $('#osFile').addEventListener('change', (e) => { osState.file = e.target.files[0] || null; $('#osPrev').disabled = !osState.file; $('#osCommit').disabled = true; $('#osPreview').innerHTML = ''; });
  const send = (path) => { const fd = new FormData(); fd.append('file', osState.file); return apiRequest(path, { method: 'POST', body: fd }); };
  $('#osPrev').onclick = async () => {
    uiSetStatus(msg, 'Đang đọc file…');
    try {
      const p = await send('/systems/import/preview');
      $('#osPreview').innerHTML = osPreviewHtml(p);
      uiSetStatus(msg, p.counts.rows ? `Sẵn sàng ghi ${fmtInt(p.counts.rows)} thay đổi.` : 'Không có thay đổi nào.', p.counts.rows ? 'ok' : 'error');
      $('#osCommit').disabled = !p.counts.rows;
    } catch (err) { uiSetStatus(msg, err.message, 'error'); }
  };
  $('#osCommit').onclick = async () => {
    $('#osCommit').disabled = true;
    try {
      const r = await send('/systems/import/commit');
      $('#spDialog').close();
      osState.facets = null; await osLoad(); osRender();
      uiAlert(`Đã nhập: ${fmtInt(r.systemsNew)} hệ thống mới, ${fmtInt(r.systemsUpdated)} cập nhật; gán ${fmtInt(r.vmsAdded)} VM, chuyển ${fmtInt(r.vmsMoved)} VM${r.errors ? `; ${fmtInt(r.errors)} dòng lỗi bị bỏ qua` : ''}.`);
    } catch (err) { uiSetStatus(msg, err.message, 'error'); $('#osCommit').disabled = false; }
  };
}

function osPreviewHtml(p) {
  const c = p.counts;
  const act = { new: uiPill('ok', 'Mới'), update: uiPill('warn', 'Cập nhật'), move: uiPill('warn', 'Chuyển hệ thống'), same: '<span class="muted-sub">Không đổi</span>' };
  return `<dl class="sp-dl">
      <div class="sp-dl-item"><dt>Hệ thống</dt><dd>${fmtInt(c.systemsNew)} mới · ${fmtInt(c.systemsUpdate)} cập nhật · ${fmtInt(c.systemsSame)} không đổi</dd></div>
      <div class="sp-dl-item"><dt>VM</dt><dd>${fmtInt(c.vmsNew)} gán mới · ${fmtInt(c.vmsMove)} chuyển hệ thống · ${fmtInt(c.vmsSame)} không đổi</dd></div>
      <div class="sp-dl-item"><dt>Dòng lỗi</dt><dd>${fmtInt(c.errors)}</dd></div></dl>
    ${p.errors.length ? `<details class="dc-issues" open><summary>Dòng lỗi — bỏ qua (${fmtInt(p.errors.length)})</summary><ul>${p.errors.map(e => `<li>${esc(e.sheet)} dòng ${esc(e.row)} ${esc(e.ref || '')}: ${esc(e.error)}</li>`).join('')}</ul></details>` : ''}
    <div class="table-scroll dc-preview" role="region" tabindex="0" aria-label="Xem trước"><table class="data-table">
      <caption class="sr-only">Xem trước dữ liệu nhập</caption>
      <thead><tr><th>Sheet · dòng</th><th>Nội dung</th><th>Thay đổi</th></tr></thead>
      <tbody>${p.systems.filter(r => r.action !== 'same').map(r => `<tr><td>Hệ thống · ${esc(r.row)}</td><td>${esc(r.code || 'mã mới')} — ${esc(r.name)}<span class="muted-sub">${esc(r.ownerEmail || 'chưa có owner')}</span></td><td>${act[r.action]}</td></tr>`).join('')}
        ${p.vms.filter(r => r.action !== 'same').slice(0, 200).map(r => `<tr><td>VM · ${esc(r.row)}</td><td>${esc(r.vm)}<span class="muted-sub">→ ${esc(r.system)}</span></td><td>${act[r.action]}</td></tr>`).join('')}
      </tbody></table></div>`;
}

function osBind() {
  const mount = $('#ownersMount');
  if (!mount || mount.dataset.bound) return;
  mount.dataset.bound = '1';
  let timer = null;
  const reloadSoon = () => { clearTimeout(timer); timer = setTimeout(async () => { await osLoad(); osRender(); }, 300); };
  mount.addEventListener('input', (e) => {
    const map = { osQ: 'q', osOwner: 'owner' };
    if (map[e.target.id]) { osState.f[map[e.target.id]] = e.target.value; reloadSoon(); }
    else if (e.target.id === 'osCovQ') { osState.covQ = e.target.value; reloadSoon(); }
  });
  mount.addEventListener('change', async (e) => {
    const map = { osDept: 'department', osCrit: 'criticality', osEnv: 'env' };
    if (map[e.target.id]) { osState.f[map[e.target.id]] = e.target.value; await osLoad(); osRender(); return; }
    if (e.target.dataset.osSel) {
      const x = Number(e.target.dataset.osSel);
      if (e.target.checked) osState.sel.add(x); else osState.sel.delete(x);
      osBulkBar();
    }
  });
  mount.addEventListener('click', async (e) => {
    if (e.target.closest('[data-os-reload]')) { mount.innerHTML = uiLoading(); osState.facets = null; await osLoad(); osRender(); return; }
    if (e.target.closest('[data-os-add]')) { osForm(null); return; }
    if (e.target.closest('[data-os-import]')) { osImportDialog(); return; }
    if (e.target.closest('[data-os-export]')) { uiDownload(`${API_BASE}/systems/export.xlsx`, { method: 'GET' }, 'he-thong-owner.xlsx'); return; }
    if (e.target.closest('[data-os-clear]')) { osState.sel.clear(); $$('[data-os-sel]').forEach(c => { c.checked = false; }); osBulkBar(); return; }
    if (e.target.closest('[data-os-bulk]')) {
      osAssignDialog([...osState.sel], `${fmtInt(osState.sel.size)} VM`, async () => { osState.sel.clear(); await osLoad(); osRender(); });
      return;
    }
    const row = e.target.closest('[data-os-id]');
    if (row && !e.target.closest('a, button, input')) osOpen(Number(row.dataset.osId));
  });
  mount.addEventListener('keydown', (e) => {
    const row = e.target.closest('[data-os-id]');
    if (row && e.target === row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); osOpen(Number(row.dataset.osId)); }
  });
}
