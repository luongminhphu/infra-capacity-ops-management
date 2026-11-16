/* =========================================================
   Super app SA3 — thẻ đối tượng (Host / VM) and global search
   (Ctrl + K command palette). docs/design/super-app.md §2.3–2.4.
   ========================================================= */

/* ---------------------------------------------------------------- entity page (#/entity?type=host&name=…&vc=…) */
function entityParams() {
  const q = new URLSearchParams((location.hash.split('?')[1]) || '');
  return { type: q.get('type') === 'vm' ? 'vm' : 'host', name: q.get('name') || '', vc: q.get('vc') || '' };
}

const enPct = (v) => (v === null || v === undefined) ? '—' : `${Math.round(v)}%`;
const enVal = (v, suffix = '') => (v === null || v === undefined || v === '') ? '—' : `${esc(v)}${suffix}`;
const enDl = (pairs) => `<dl class="sp-dl en-dl">${pairs.map(([l, v]) => `<div class="sp-dl-item"><dt>${esc(l)}</dt><dd>${v}</dd></div>`).join('')}</dl>`;
const EN_SEV = { critical: ['crit', 'Nghiêm trọng'], warning: ['warn', 'Cảnh báo'], info: ['na', 'Thông tin'] };

async function renderEntityView() {
  const mount = $('#entityMount');
  if (!mount) return;
  const p = entityParams();
  if (!p.name) { mount.innerHTML = uiEmpty('Chưa chọn đối tượng — tìm bằng Ctrl + K.'); return; }
  mount.innerHTML = uiNote('Đang tải…');
  let d;
  try { d = await apiRequest(`/entities/${p.type}?${new URLSearchParams({ name: p.name, vcenter: p.vc })}`); }
  catch (err) { mount.innerHTML = `<section class="panel">${uiEmpty(err.message)}</section>`; return; }
  $('#pageTitle').textContent = d.name;
  document.title = `${d.name} — Infra Capacity Ops Management`;
  mount.innerHTML = p.type === 'host' ? entityHostHtml(d) : entityVmHtml(d);
  refreshIcons();
}

function enFindings(fs) {
  if (!fs.length) return '<p class="qt-ok">Không có phát hiện compliance.</p>';
  return `<ul class="home-list">${fs.map(f => `<li class="home-item"><div class="home-item-main">${uiPill(...(EN_SEV[f.severity] || ['na', f.severity]))}
    <b>${esc(f.typeLabel)}</b> <span class="muted-sub sr-inline mono">${esc(f.code || '')}</span>
    <p class="home-item-detail">${esc(f.metric || '')} — ${esc(f.recommendation || '')}</p></div></li>`).join('')}</ul>`;
}

function entityHostHtml(d) {
  const hw = d.hardware, c = d.capacity, cl = d.clusterStatus;
  const vms = d.vms.map(v => `<tr><td>${uiEntityLink('vm', v.name, d.vcenterKey, false)}</td><td>${v.powerOn ? uiPill('ok', 'Bật') : uiPill('na', 'Tắt')}</td>
      <td class="num">${enVal(v.vcpu)}</td><td class="num">${enVal(v.ramGB)}</td><td>${enVal(v.ip)}</td></tr>`).join('');
  const dev = d.devices === null ? '' : `<section class="panel"><div class="panel-title" role="heading" aria-level="2">Bảo hành / hợp đồng (Opex)</div>
      ${d.devices.length ? `<ul class="home-list">${d.devices.map(x => `<li class="home-item"><div class="home-item-main">
        <a class="home-item-title" href="${esc(homeLink('spend-opex', `code=${encodeURIComponent(x.code)}`))}">${esc(x.code)} — ${esc(x.title)}</a>
        <p class="home-item-detail">Serial ${esc(x.serial)} · hết hạn ${esc(spDateSafe(x.endDate))} · ${esc(x.statusLabel)}</p></div></li>`).join('')}</ul>`
        : '<p class="muted-sub">Chưa có khoản Opex nào khai báo thiết bị này (Opex → Thiết bị liên quan → tên host).</p>'}</section>`;
  return `
    <section class="panel"><div class="panel-head"><div><div class="eyebrow">Host · ${esc(d.vcenterKey || 'import')}</div>
      <div class="panel-title" role="heading" aria-level="2">${esc(d.name)} ${c.overall ? uiPill(c.overall.cls, c.overall.text) : ''}</div>
      <p class="panel-sub">Cluster <b>${esc(d.cluster || '—')}</b> · ${esc(c.vmCount ?? 0)} VM đang chạy</p></div>
      <div class="panel-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-nav-link="dashboard">Capacity</button>
        <button class="btn btn-ghost btn-sm" type="button" data-nav-link="lic-hardware">ESXi & phần cứng</button></div></div>
      ${enDl([['CPU dùng (theo ngưỡng)', enPct(c.cpuPct)], ['RAM dùng (theo ngưỡng)', enPct(c.ramPct)], ['vCPU cấp', enVal(c.vcpuUsed)],
              ['RAM cấp (GB)', enVal(c.ramUsedGB)], ['Hãng / model', `${enVal(hw.vendor)} ${hw.model ? esc(hw.model) : ''}`],
              ['CPU', `${enVal(hw.cpuModel)}`], ['Socket × core', `${enVal(hw.cpuSockets)} × ${enVal(hw.cores)}`], ['RAM (GB)', enVal(hw.ramGB)],
              ['ESXi', `${enVal(hw.esxiVersion)} ${hw.esxiBuild ? `(build ${esc(hw.esxiBuild)})` : ''}`], ['Trạng thái host', enVal(hw.status)]])}
    </section>
    ${cl ? `<section class="panel"><div class="panel-title" role="heading" aria-level="2">Cluster ${esc(cl.name)} ${uiPill(cl.overall.cls, cl.overall.text)}</div>
      ${enDl([['Host', enVal(cl.hosts)], ['VM', enVal(cl.vms)], ['CPU', enPct(cl.cpuPct)], ['RAM', enPct(cl.ramPct)],
              ['HA', cl.ha === true ? 'Bật' : cl.ha === false ? 'Tắt' : '—'], ['DRS', cl.drs === true ? 'Bật' : cl.drs === false ? 'Tắt' : '—']])}</section>` : ''}
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Phát hiện compliance</div>${enFindings(d.findings)}</section>
    ${dev}
    <section class="table-card"><div class="panel-title" role="heading" aria-level="2">VM trên host (${esc(d.vms.length)})</div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>VM</th><th>Nguồn</th><th class="num">vCPU</th><th class="num">RAM (GB)</th><th>IP</th></tr></thead>
      <tbody>${vms || `<tr><td colspan="5">${uiEmpty('Không có VM.')}</td></tr>`}</tbody></table></div></section>`;
}

function entityVmHtml(d) {
  const c = d.config, os = d.os, b = d.backup;
  const cov = b ? { protected: ['ok', 'Có backup'], failing: ['warn', 'Có job nhưng lỗi'], unprotected: ['crit', 'Chưa backup'] }[b.coverage] : null;
  const backup = b === null ? '' : `<section class="panel"><div class="panel-title" role="heading" aria-level="2">Backup ${cov ? uiPill(...cov) : uiPill('na', 'Không xác định')}</div>
      ${b.client ? enDl([['Client', enVal(b.client.client)], ['Đánh giá', uiPill(({ ok: 'ok', warn: 'warn', crit: 'crit' })[b.client.status] || 'na', b.client.statusLabel)],
                         ['Policy', enVal((b.client.policies || []).join(', '))], ['Thành công gần nhất', b.client.lastSuccessAt ? esc(spTimeSafe(b.client.lastSuccessAt)) : '—'],
                         ['Đã qua (giờ)', enVal(b.client.hoursSinceSuccess)], ['RPO (giờ)', enVal(b.client.rpoHours)],
                         ['Lỗi liên tiếp', enVal(b.client.consecutiveFail)], ['Kết quả lần cuối', enVal(b.client.lastStatusText)]])
        : '<p class="muted-sub">Không khớp client backup nào (theo tên VM, hostname guest hoặc IP).</p>'}
      <button class="btn btn-ghost btn-sm" type="button" data-nav-link="bk-coverage">Độ phủ backup VM</button></section>`;
  return `
    <section class="panel"><div class="panel-head"><div><div class="eyebrow">VM · ${esc(d.vcenterKey || 'import')}</div>
      <div class="panel-title" role="heading" aria-level="2">${esc(d.name)} ${c.isPoweredOn ? uiPill('ok', 'Đang chạy') : uiPill('na', 'Tắt')}</div>
      <p class="panel-sub">Cluster <b>${esc(d.cluster || '—')}</b> · host ${d.host ? uiEntityLink('host', d.host, d.vcenterKey, false) : '—'}</p></div>
      <div class="panel-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-nav-link="history">Xu hướng & biến động</button>
        <button class="btn btn-ghost btn-sm" type="button" data-nav-link="lic-os">Hệ điều hành</button></div></div>
      ${enDl([['vCPU', enVal(c.vcpu)], ['RAM (GB)', enVal(c.ramGB)], ['Disk cấp / dùng (GB)', `${enVal(c.diskProvGB)} / ${enVal(c.diskUsedGB)}`],
              ['Datastore', enVal(c.datastore)], ['IP', enVal(c.ipAddress)], ['Hostname guest', enVal(c.guestHostname)],
              ['VMware Tools', enVal(c.toolsStatus)], ['Không hoạt động (ngày)', enVal(c.lastActivityDays)], ['Tắt (ngày)', enVal(c.powerOffDays)],
              ...(os ? [['Hệ điều hành', `${enVal(os.label)} ${os.version ? esc(os.version) : ''} ${os.edition ? esc(os.edition) : ''}${os.overridden ? ' <span class="pill na">ghi đè</span>' : ''}`]] : [])])}
    </section>
    ${backup}
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Phát hiện compliance</div>${enFindings(d.findings)}</section>`;
}


/* ---------------------------------------------------------------- command palette (Ctrl + K) */
const palState = { timer: null, seq: 0, items: [], active: 0 };
const PAL_TYPE = { page: 'Trang', action: 'Thao tác', host: 'Host', vm: 'VM', client: 'Client backup', opex: 'Opex', capex: 'Capex',
  contract: 'Hợp đồng', quote: 'Báo giá', renovation: 'Cải tạo', proposal: 'Tờ trình', vendor: 'NCC' };
const PAL_ACTIONS = [
  { label: 'Tạo khoản Opex', page: 'spend-opex', run: () => uiRequire('spend').then(() => spOpenForm()), role: 'editor', feature: 'spend' },
  { label: 'Tạo gói báo giá', page: 'spend-quotes', run: () => uiRequire('spend').then(() => qtOpenForm()), role: 'editor', feature: 'spend' },
  { label: 'Tạo dự án cải tạo văn phòng', page: 'spend-renovations', run: () => uiRequire('spend').then(() => rnOpenForm()), role: 'editor', feature: 'spend' },
  { label: 'Xem kết quả quy hoạch tài nguyên', page: 'planning', run: () => typeof runPlanningPreview === 'function' && runPlanningPreview() },
  { label: 'Đồng bộ dữ liệu ngay', run: () => { const b = $('#btnSyncNow'); if (b) b.click(); }, admin: true },
];

function palPages(q) {
  const out = [];
  MENU.forEach(g => g.entries.forEach(en => en.tabs.forEach(([id, , tab]) => {
    if (!navItemVisible(id)) return;
    const label = en.tabs.length > 1 ? `${en.label} › ${tab}` : en.label;
    const it = NAV_ITEMS[id];
    if (!q || `${label} ${it.label} ${it.title} ${it.desc || ''}`.toLowerCase().includes(q)) out.push({ type: 'page', label, sub: g.label || 'ICO', page: id });
  })));
  return out;
}

function palActions(q) {
  return PAL_ACTIONS.filter(a => (!a.feature || featureOn(a.feature)) && (!a.role || spendRoleOk(a.role)) && (!a.admin || uiIsAdmin()) && (!a.page || navItemVisible(a.page))
    && (!q || a.label.toLowerCase().includes(q))).map(a => ({ type: 'action', label: a.label, sub: 'Thao tác', action: a }));
}

function openPalette() {
  let dlg = $('#palDialog');
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'palDialog';
    dlg.className = 'src-dialog pal-dialog';
    dlg.setAttribute('aria-label', 'Tìm kiếm');
    dlg.innerHTML = `<input type="text" id="palInput" placeholder="Tìm host, VM, mã OPX / CPX / HD / BG / CTVP / TT, nhà cung cấp, trang…" autocomplete="off" aria-controls="palList">
      <ul class="pal-list" id="palList" role="listbox"></ul><p class="pal-hint">↑ ↓ chọn · Enter mở · Esc đóng</p>`;
    document.body.appendChild(dlg);
    $('#palInput').addEventListener('input', palQuery);
    $('#palInput').addEventListener('keydown', palKeys);
    $('#palList').addEventListener('click', (e) => { const li = e.target.closest('[data-pal]'); if (li) palGo(Number(li.dataset.pal)); });
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  }
  $('#palInput').value = '';
  palShow(palActions('').concat(palPages('')).slice(0, 12));
  dlg.showModal();
  $('#palInput').focus();
}

function palShow(items) {
  palState.items = items; palState.active = 0;
  $('#palList').innerHTML = items.map((it, i) => `<li class="pal-item${i === 0 ? ' active' : ''}" role="option" data-pal="${i}" aria-selected="${i === 0}">
      <span class="pal-type">${esc(PAL_TYPE[it.type] || it.type)}</span><span class="pal-label">${esc(it.label)}</span><span class="pal-sub">${esc(it.sub || '')}</span></li>`).join('')
    || '<li class="pal-empty">Không tìm thấy.</li>';
}

function palQuery() {
  const raw = $('#palInput').value.trim();
  const q = raw.toLowerCase();
  const local = palActions(q).concat(palPages(q));
  palShow(local.slice(0, 20));
  clearTimeout(palState.timer);
  if (q.length < 2) return;
  const seq = ++palState.seq;
  palState.timer = setTimeout(async () => {
    try {
      const r = await apiRequest(`/search?q=${encodeURIComponent(raw)}`);
      if (seq !== palState.seq) return;
      palShow(r.items.concat(local).slice(0, 40));
    } catch (err) { /* keep local results */ }
  }, 250);
}

function palKeys(e) {
  const n = palState.items.length;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    if (!n) return;
    palState.active = (palState.active + (e.key === 'ArrowDown' ? 1 : n - 1)) % n;
    $$('#palList .pal-item').forEach((li, i) => { li.classList.toggle('active', i === palState.active); li.setAttribute('aria-selected', i === palState.active); });
    const cur = $$('#palList .pal-item')[palState.active]; if (cur) cur.scrollIntoView({ block: 'nearest' });
  } else if (e.key === 'Enter') { e.preventDefault(); palGo(palState.active); }
}

function palGo(i) {
  const it = palState.items[i];
  if (!it) return;
  $('#palDialog').close();
  if (it.entity) { location.hash = uiEntityHref(it.entity.type, it.entity.name, it.entity.vcenterKey); return; }
  if (it.action) {
    if (it.action.page) navigateTo(it.action.page);
    setTimeout(() => it.action.run(), 300);
    return;
  }
  if (it.page && it.query) location.hash = homeLink(it.page, it.query);
  else if (it.page) navigateTo(it.page);
}

document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
    if (!currentUser) return;
    e.preventDefault();
    const dlg = $('#palDialog');
    if (dlg && dlg.open) dlg.close(); else openPalette();
  }
});
