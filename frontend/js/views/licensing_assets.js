/* License tabs 1–2: ESXi & phần cứng, phân loại OS — moved out of licensing.js (audit FE-29). */

/* =========================================================
   1. ESXi & phần cứng
   ========================================================= */
function renderLicHardware() {
  const st = licState();
  const inv = st.inv, t = inv.totals;
  const ratio = t.physicalCores > 0 ? (t.vcpuAllocated / t.physicalCores) : NaN;
  const kpis = [
    ['Host', licNum(t.hosts), `${Object.keys(inv.distribution.model).length} model · ${Object.keys(inv.distribution.esxiVersion).length} phiên bản hypervisor`],
    ['CPU socket', licNum(t.sockets), 'Số CPU vật lý — cơ sở mức sàn 16 core/CPU (VMware) và 8 core/CPU (Windows Server)'],
    ['Physical core', licNum(t.physicalCores), 'Core vật lý — đơn vị tính license, KHÔNG phải vCPU'],
    ['Logical thread', licNum(t.logicalThreads), 'Hyper-thread — không dùng để tính license'],
    ['vCPU đã cấp', licNum(t.vcpuAllocated), `Tỷ lệ vCPU : physical core = ${isFinite(ratio) ? licNum(ratio, 2) : '—'}`],
    ['VM', licNum(t.vms), 'Trong phạm vi lọc'],
  ];
  const distChips = (obj, label) => `<div class="lic-dist"><span class="lic-dist-label">${label}</span>${Object.entries(obj).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${uiTag(`${k} · ${v}`)}`).join('')}</div>`;

  // Group hosts: vCenter → cluster
  const groups = {};
  inv.hosts.forEach(h => {
    const k = `${h.vcenterKey || 'import'}||${h.cluster || '—'}`;
    (groups[k] = groups[k] || { vcenterKey: h.vcenterKey || 'import', cluster: h.cluster || '—', hosts: [] }).hosts.push(h);
  });
  const rows = Object.values(groups).sort((a, b) => (a.vcenterKey + a.cluster).localeCompare(b.vcenterKey + b.cluster)).map(g => {
    const key = `${g.vcenterKey}||${g.cluster}`;
    const open = !!st.hwOpen[key];
    const sum = (f) => g.hosts.reduce((s, h) => s + (Number(h[f]) || 0), 0);
    const models = [...new Set(g.hosts.map(h => h.model || '—'))].join(', ');
    const versions = [...new Set(g.hosts.map(h => h.esxiVersion || '—'))].join(', ');
    const ha = g.hosts.some(h => h.clusterHa), drs = g.hosts.some(h => h.clusterDrs);
    const head = `<tr class="lic-cluster-row" data-hw-toggle="${esc(key)}">
      <td><span class="lic-caret">${open ? '▾' : '▸'}</span> <b>${esc(g.cluster)}</b><div class="muted-sub">${esc(g.vcenterKey)}</div></td>
      <td>${esc(models)}</td><td>${esc(versions)}</td>
      <td class="num">${g.hosts.length}</td><td class="num">${licNum(sum('sockets'))}</td><td class="num"><b>${licNum(sum('cores'))}</b></td>
      <td class="num">${licNum(sum('threads'))}</td><td class="num">${licNum(sum('ramGB'))}</td><td class="num">${licNum(sum('vmCount'))}</td>
      <td>${ha ? uiPill('ok', 'HA') : uiPill('na', 'HA tắt')} ${drs ? uiPill('ok', 'DRS') : uiPill('na', 'DRS tắt')}</td>
    </tr>`;
    const hostRows = open ? g.hosts.map(h => `<tr class="lic-host-row">
      <td class="lic-indent">${esc(h.host)}${h.flags?.length ? ` ${uiPill('warn', 'giả định', h.flags.join(', '))}` : ''}</td>
      <td>${esc(h.vendor || '')} ${esc(h.model || '—')}<div class="muted-sub">${esc(h.cpuModel || '')}</div></td>
      <td>${esc(h.esxiVersion || '—')}${h.esxiBuild ? `<div class="muted-sub">build ${esc(h.esxiBuild)}</div>` : ''}</td>
      <td class="num">1</td><td class="num">${licNum(h.sockets)}</td><td class="num"><b>${licNum(h.cores)}</b></td>
      <td class="num">${licNum(h.threads)}</td><td class="num">${licNum(h.ramGB)}</td><td class="num">${licNum(h.vmCount)}</td>
      <td><span class="muted-sub">WS lic. core ${licNum(h.wsLicensableCores)}${h.wsFloorApplied ? ' (sàn)' : ''} · VMware core ${licNum(h.vmwareCores)}${h.vmwareFloorApplied ? ' (sàn)' : ''}</span></td>
    </tr>`).join('') : '';
    return head + hostRows;
  }).join('');

  return `
    <div class="kpi-strip lic-kpi-6">${kpis.map(([l, v, s]) => `<div class="kpi-cell"><div class="kpi-l">${l}</div><div class="kpi-v">${v}</div><div class="kpi-s">${s}</div></div>`).join('')}</div>
    <section class="table-card">
      <div class="panel-head">
        <div><div class="panel-title" role="heading" aria-level="2">Host theo nguồn / cluster</div><p class="panel-sub">Bấm vào dòng cluster để xem từng host. Physical core là số core vật lý của CPU; logical thread là số luồng khi bật Hyper-Threading; vCPU là tài nguyên ảo cấp cho VM — chỉ physical core được dùng để tính license.</p></div>
        <div class="panel-toolbar">
          <button class="btn btn-secondary btn-sm" data-lic-action="hw-expand" type="button">Mở tất cả</button>
          <button class="btn btn-secondary btn-sm" data-lic-action="hw-collapse" type="button">Thu gọn</button>
          <button class="btn btn-secondary btn-sm" data-lic-action="export-hw" type="button">Xuất Excel</button>
        </div>
      </div>
      ${distChips(inv.distribution.model, 'Model')}${distChips(inv.distribution.esxiVersion, 'Hypervisor')}
      <div class="table-scroll"><table class="data-table lic-table">
        <thead><tr><th>Cluster / Host</th><th>Model</th><th>Hypervisor</th><th class="num">Host</th><th class="num">Socket</th><th class="num">Physical core</th><th class="num">Logical thread</th><th class="num">RAM GB</th><th class="num">VM</th><th>HA / DRS · license core</th></tr></thead>
        <tbody>${rows || uiEmptyRow(10, 'Không có host trong phạm vi.')}</tbody>
      </table></div>
      <div class="row-count">${inv.hosts.length} host · ${Object.keys(groups).length} cluster</div>
    </section>`;
}

/* =========================================================
   2. Phân loại OS
   ========================================================= */
function renderLicOs() {
  const st = licState();
  const s = st.inv.summary;
  const cats = Object.entries(s.byCategory);
  const clusters = [...new Set(st.inv.vms.map(v => v.cluster).filter(Boolean))].sort();
  const opt = (obj, cur, allLabel) => `<option value="all"${cur === 'all' ? ' selected' : ''}>${allLabel}</option>` + Object.entries(obj).map(([k, l]) => `<option value="${k}"${cur === k ? ' selected' : ''}>${esc(l)}</option>`).join('');
  return `
    <div class="kpi-strip lic-kpi-5">${cats.map(([k, b]) => `<div class="kpi-cell lic-cat-card" role="button" tabindex="0" aria-pressed="${st.filters.category === k}" data-lic-cat="${k}" title="Lọc theo ${LIC_CATEGORY_LABEL[k]}"><div class="kpi-l">${LIC_CATEGORY_LABEL[k]}</div><div class="kpi-v">${b.count}</div><div class="kpi-s">${b.poweredOn} đang bật · ${licNum(b.vcpu)} vCPU · ${licNum(b.ramGB)} GB</div></div>`).join('')}</div>
    <section class="table-card">
      <div class="panel-head">
        <div><div class="panel-title" role="heading" aria-level="2">VM inventory &amp; phân loại guest OS</div>
          <p class="panel-sub">Nguồn bằng chứng theo thứ tự ưu tiên: Override thủ công → VMware Tools (OS thực tế guest báo về) → Cấu hình VM (guestId đặt lúc tạo, có thể lỗi thời) → Tên VM. Edition Windows Server không lấy được từ vCenter, chỉ có khi override. Độ tin cậy: ${Object.entries(s.byConfidence).map(([k, v]) => `${LIC_CONF_LABEL[k]} ${v}`).join(' · ')}.</p></div>
        <div class="panel-toolbar">
          <select id="licOsCat" data-lic-filter="category">${opt(LIC_CATEGORY_LABEL, st.filters.category, 'Tất cả phân loại')}</select>
          <select id="licOsConf" data-lic-filter="confidence">${opt(LIC_CONF_LABEL, st.filters.confidence, 'Mọi độ tin cậy')}</select>
          <select id="licOsSrc" data-lic-filter="source">${opt(LIC_SOURCE_LABEL, st.filters.source, 'Mọi nguồn')}</select>
          <select id="licOsCluster" data-lic-filter="cluster"><option value="all">Tất cả cluster</option>${clusters.map(c => `<option value="${esc(c)}"${st.filters.cluster === c ? ' selected' : ''}>${esc(c)}</option>`).join('')}</select>
          <input id="licOsSearch" placeholder="Tìm VM / IP / OS / host…" value="${esc(st.filters.q)}">
          <button class="btn btn-secondary btn-sm" data-lic-action="export-os" type="button">Xuất Excel</button>
          <button class="btn btn-secondary btn-sm hidden" data-ai-act="os" data-ai-gate="os_classify" type="button">AI gợi ý VM chưa rõ</button>
        </div>
      </div>
      <div id="licAiOs" class="ai-out" aria-live="polite"></div>
      <div id="licOverrideEditor"></div>
      <div class="table-scroll"><table class="data-table lic-table" id="licOsTable">
        <thead><tr><th>VM</th><th>IP address</th><th>Cluster / Host</th><th>Trạng thái</th><th class="num">vCPU</th><th class="num">RAM GB</th><th>Phân loại</th><th>OS version</th><th>Edition</th><th>Nguồn</th><th>Độ tin cậy</th><th>Ghi chú</th><th></th></tr></thead>
        <tbody></tbody></table></div>
      <div class="row-count" id="licOsFoot"></div>
    </section>`;
}

function licFilteredVms() {
  const st = licState();
  const f = st.filters, q = (f.q || '').toLowerCase();
  return st.inv.vms.filter(v => {
    if (f.category !== 'all' && v.os.category !== f.category) return false;
    if (f.confidence !== 'all' && v.os.confidence !== f.confidence) return false;
    if (f.source !== 'all' && v.os.source !== f.source) return false;
    if (f.cluster !== 'all' && v.cluster !== f.cluster) return false;
    if (q) {
      const hay = `${v.vm} ${v.ipAddress || ''} ${v.host} ${v.cluster} ${v.os.osVersion || ''} ${v.os.evidence?.guestFullName || ''} ${v.os.evidence?.toolsGuestFullName || ''}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function renderLicOsTable() {
  const tb = $('#licOsTable tbody');
  if (!tb) return;
  const rows = licFilteredVms();
  const confCls = { confirmed: 'ok', high: 'ok', medium: 'warn', low: 'bad', none: 'na' };
  tb.innerHTML = rows.slice(0, 1500).map((v, i) => {
    const o = v.os;
    const ev = o.evidence || {};
    const title = `guestId: ${ev.guestId || '—'}\nconfig: ${ev.guestFullName || '—'}\ntools: ${ev.toolsGuestFullName || '—'} (${ev.toolsStatus || '—'})\nhostname: ${ev.guestHostname || '—'}`;
    return `<tr>
      <td><b>${esc(v.vm)}</b>${o.overridden ? ' ' + uiTag('override', 'Override thủ công') : ''}</td>
      <td class="mono"${ev.guestHostname ? ` title="hostname: ${esc(ev.guestHostname)}"` : ''}>${esc(v.ipAddress || '—')}</td>
      <td>${esc(v.cluster || '—')}<div class="muted-sub">${esc(v.host || '—')}</div></td>
      <td>${v.isPoweredOn ? uiPill('ok', 'Bật') : uiPill('na', 'Tắt')}</td>
      <td class="num">${licNum(v.vcpu)}</td><td class="num">${licNum(v.ramGB)}</td>
      <td>${uiTag(o.categoryLabel)}</td>
      <td title="${esc(title)}">${esc(o.osVersion || '—')}</td>
      <td>${esc(o.osEdition || '—')}</td>
      <td>${esc(LIC_SOURCE_LABEL[o.source] || o.source)}</td>
      <td>${uiPill(confCls[o.confidence] || 'na', LIC_CONF_LABEL[o.confidence] || o.confidence)}</td>
      <td class="lic-note">${esc(o.note || '')}</td>
      <td>${uiIsAdmin() ? `<button class="btn btn-secondary btn-sm" data-lic-action="override" data-idx="${st_idx(v)}" type="button">Ghi đè</button>` : ''}</td>
    </tr>`;
  }).join('') || uiEmptyRow(13, 'Không có VM khớp bộ lọc.');
  $('#licOsFoot').textContent = `${rows.length} / ${licState().inv.vms.length} VM${rows.length > 1500 ? ' (hiển thị 1500 dòng đầu — thu hẹp bộ lọc hoặc xuất Excel)' : ''}`;
}
function st_idx(v) { return licState().inv.vms.indexOf(v); }

function renderLicOverrideEditor(vm) {
  const st = licState();
  const box = $('#licOverrideEditor');
  if (!box) return;
  if (!vm) { box.innerHTML = ''; return; }
  const o = vm.os;
  const existing = st.overrides?.find(x => x.vcenterKey === (vm.vcenterKey || null) && ((vm.instanceUuid && x.instanceUuid === vm.instanceUuid) || (!x.instanceUuid && x.vm === vm.vm)));
  const ev = o.evidence || {};
  box.innerHTML = `<div class="planning-panel lic-editor">
    <div class="planning-panel-title">Override phân loại OS — ${esc(vm.vm)} <span class="muted-sub">(${esc(vm.cluster || '')} · ${esc(vm.host || '')})</span></div>
    <div class="muted-sub">Bằng chứng từ vCenter: guestId <b>${esc(ev.guestId || '—')}</b> · cấu hình <b>${esc(ev.guestFullName || '—')}</b> · VMware Tools <b>${esc(ev.toolsGuestFullName || '—')}</b> (${esc(ev.toolsStatus || '—')}) · hostname <b>${esc(ev.guestHostname || '—')}</b>. Hệ thống suy ra: ${esc(o.categoryLabel)} / ${esc(o.osVersion || '—')} (${esc(LIC_SOURCE_LABEL[o.source])}, ${esc(LIC_CONF_LABEL[o.confidence])}).</div>
    <div class="sim-form-grid">
      <div class="field"><label>Phân loại</label><select id="licOvCat">${Object.entries(LIC_CATEGORY_LABEL).map(([k, l]) => `<option value="${k}"${(existing?.category || o.category) === k ? ' selected' : ''}>${l}</option>`).join('')}</select></div>
      <div class="field"><label>OS version</label><input id="licOvVer" value="${esc(existing?.osVersion || o.osVersion || '')}" placeholder="VD: Windows Server 2019"></div>
      <div class="field"><label>Edition (nếu đã kiểm tra)</label><input id="licOvEd" value="${esc(existing?.osEdition || '')}" placeholder="Standard / Datacenter" list="licEdList"><datalist id="licEdList"><option value="Standard"><option value="Datacenter"><option value="Essentials"></datalist></div>
      <div class="field"><label>Ghi chú / bằng chứng</label><input id="licOvNote" value="${esc(existing?.note || '')}" placeholder="VD: kiểm tra winver qua RDP ngày …"></div>
    </div>
    <div class="sim-actions">
      <button class="btn btn-primary btn-sm" data-lic-action="override-save" type="button">Lưu ghi đè</button>
      ${existing ? `<button class="btn btn-secondary btn-sm btn-danger" data-lic-action="override-delete" data-id="${existing.id}" type="button">Xoá override</button>` : ''}
      <button class="btn btn-secondary btn-sm" data-lic-action="override-cancel" type="button">Đóng</button>
    </div>
  </div>`;
  box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
