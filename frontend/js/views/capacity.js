/* =========================================================
   views/capacity.js — Dashboard kỹ thuật, Compliance, Inventory (host / VM) và các nút xuất của chúng.
   Reads the capacity model in core.js (buildCapacityModel, buildClusterRows, buildHostRows,
   buildComplianceFindings, …); the maths stays in core.js so it keeps matching capacity.py.
   ========================================================= */

/* =========================================================
   Render: Dashboard view (KPIs, charts, cluster table)
   ========================================================= */

function barCellHtml(pct, extraLabel) {
  // Bullet with 80 / 100 % notches (MASTER.md §7); the row's pill carries the status, not the bar color.
  return `<div class="mini-bar-cell">${uiBullet(pct)}<span class="num">${fmtPct(pct)}${extraLabel ? ' · ' + esc(extraLabel) : ''}</span></div>`;
}

function renderCapacityKpis() {
  const hosts = getFilteredHosts();
  const vms = getFilteredVms();
  const model = buildCapacityModel(hosts, vms);
  // CPU%/RAM% đã tính theo overcommit ratio / ngưỡng overcommit (xem buildCapacityModel) nên dùng ngưỡng cố định 80%/100%.
  const cpuStatus = getStatusForPct(model.cpuPct, 80, 100);
  const ramStatus = getStatusForPct(model.ramPct, 80, 100);
  const dsStatus = getStatusForPct(model.storagePct, appState.policy.dsWarn, appState.policy.dsHigh);

  const cards = [
    {
      label: 'CPU — Tổng / Đã dùng / Còn trống', value: `${fmtInt(model.usedVcpu)} / ${fmtInt(model.totalLogicalCores)} <span class="mono" style="font-size:.6em;color:var(--text-secondary)">pCPU logic</span>`,
      sub: `Còn trống: ${fmtInt(model.freeLogicalCores)} pCPU (${fmtInt(model.totalCores)} core vật lý × HT${fmtNum(model.htFactor,0)}) · Overcommit ${fmtX(model.cpuOvercommit)}`,
      pct: model.cpuPct, status: cpuStatus
    },
    {
      label: 'RAM — Tổng / Đã dùng / Còn trống', value: `${fmtNum(model.usedRamGB,0)} / ${fmtNum(model.totalRamGB,0)} <span class="mono" style="font-size:.6em;color:var(--text-secondary)">GB</span>`,
      sub: `Còn trống: ${fmtNum(model.freeRamGB,0)} GB · Overcommit ${fmtX(model.ramOvercommit)}`,
      pct: model.ramPct, status: ramStatus
    },
    appState.policy.includeStorage ? {
      label: 'Storage — Cấp phát / Đã dùng / Còn trống', value: `${fmtNum(model.usedStorageGB,0)} / ${fmtNum(model.provStorageGB,0)} <span class="mono" style="font-size:.6em;color:var(--text-secondary)">GB</span>`,
      sub: `Còn trống: ${fmtNum(model.freeStorageGB,0)} GB`,
      pct: model.storagePct, status: dsStatus
    } : {
      label: 'Storage — không đánh giá', value: 'Quản lý ở tầng SAN dùng chung',
      sub: 'Thick provisioning làm % usage không phản ánh dung lượng thực. Bật lại trong "Tiêu chí đánh giá".',
      pct: null, status: null, muted: true
    },
    {
      label: 'Hosts / VMs', value: `${fmtInt(model.hostCount)} <span class="mono" style="font-size:.6em;color:var(--text-secondary)">hosts</span>`,
      sub: `${fmtInt(model.vmCount)} VM tổng · ${fmtInt(model.poweredOnCount)} on / ${fmtInt(model.poweredOffCount)} off`,
      pct: null, status: null
    }
  ];

  const box = $('#capacityKpis');
  box.className = 'kpi-strip-wrap';
  box.innerHTML = uiKpiStrip(cards.map(c => ({ label: c.label, valueHtml: c.value, sub: c.sub, pct: c.pct, status: c.status, muted: c.muted })));
}

function renderClusterTable() {
  const rows = buildClusterRows().filter(r => appState.filters.cluster === 'all' || r.name === appState.filters.cluster);
  const tbody = $('#clusterTable tbody');
  const showStorage = !!appState.policy.includeStorage;
  $$('#clusterTable th.col-storage').forEach(th => th.classList.toggle('hidden', !showStorage));
  if (!rows.length) { tbody.innerHTML = `<tr><td colspan="9"><div class="empty-note">Chưa có dữ liệu cluster.</div></td></tr>`; return; }

  const sorted = applySort(rows, 'clusterTable', (r, key) => {
    switch (key) {
      case 'name': return r.name;
      case 'hosts': return r.hosts;
      case 'vms': return r.vms;
      case 'cpuPct': return r.model.cpuPct;
      case 'ramPct': return r.model.ramPct;
      case 'storagePct': return r.model.storagePct;
      case 'cpuOvercommit': return r.model.cpuOvercommit;
      case 'ramOvercommit': return r.model.ramOvercommit;
      case 'status': return SEVERITY_RANK(r.overall.cls);
      default: return 0;
    }
  });

  tbody.innerHTML = sorted.map(r => `
    <tr>
      <td><strong>${esc(r.name)}</strong></td>
      <td>${r.hosts}</td>
      <td>${r.vms}</td>
      <td>${barCellHtml(r.model.cpuPct)}</td>
      <td>${barCellHtml(r.model.ramPct)}</td>
      ${showStorage ? `<td>${barCellHtml(r.model.storagePct)}</td>` : ''}
      <td class="mono">${fmtX(r.model.cpuOvercommit)}</td>
      <td class="mono">${fmtX(r.model.ramOvercommit)}</td>
      <td>${uiPill(r.overall.cls, r.overall.text)}</td>
    </tr>
  `).join('');
}

function SEVERITY_RANK(cls) { return { crit:0, bad:1, warn:2, ok:3, na:4 }[cls] ?? 5; }

function renderDashboardCharts() {
  if (typeof Chart === 'undefined') return;
  const colors = chartPalette();
  const clusterRows = buildClusterRows();
  const hostRows = buildHostRows().sort((a,b) => Math.max(b.cpuPct||0,b.ramPct||0) - Math.max(a.cpuPct||0,a.ramPct||0)).slice(0, 10);
  const dsRows = buildDatastoreRows().slice(0, 10);

  ['clusterUtil','hostUtil','powerState','datastore','overcommit'].forEach(destroyChart);   // clusterUtil / powerState: legacy keys
  const showStorage = !!appState.policy.includeStorage;
  const dsCard = $('#datastoreChartCard');
  const dsGrid = $('#datastoreChartGrid');
  if (dsCard) dsCard.classList.toggle('hidden', !showStorage);
  if (dsGrid) dsGrid.classList.toggle('storage-off', !showStorage);
  const utilSub = $('#clusterUtilSub');
  if (utilSub) utilSub.textContent = showStorage ? 'CPU / RAM / Storage theo % ngưỡng của từng cluster.' : 'CPU / RAM theo % ngưỡng overcommit của từng cluster. Storage không đánh giá (SAN dùng chung).';

  // Cluster utilization as small multiples (one cell per cluster, CPU / RAM / Storage bullets) —
  // same numbers as the cluster table; replaces a grouped bar chart that hid the 80 / 100 % limits.
  $('#clusterUtilSm').innerHTML = uiSmallMultiples(clusterRows.map(c => ({
    name: c.name, status: c.overall, sub: `${c.hosts} host · ${c.vms} VM`,
    metrics: [{ label: 'CPU', pct: c.model.cpuPct }, { label: 'RAM', pct: c.model.ramPct },
      ...(showStorage ? [{ label: 'Storage', pct: c.model.storagePct }] : [])] })), { empty: 'Chưa có dữ liệu cluster.' });

  const series = colors.series;
  appState.charts.hostUtil = new Chart($('#chartHostUtil'), {
    type: 'bar',
    data: {
      labels: hostRows.map(h => h.host),
      datasets: [
        { label:'CPU % ngưỡng', data: hostRows.map(h => Number((h.cpuPct||0).toFixed(1))), backgroundColor: series[0], borderRadius: 3, barPercentage: .8 },
        { label:'RAM % ngưỡng', data: hostRows.map(h => Number((h.ramPct||0).toFixed(1))), backgroundColor: series[1], borderRadius: 3, barPercentage: .8 }
      ]
    },
    options: uiChartBase(colors, { indexAxis:'y',
      scales: { x: { beginAtZero:true, suggestedMax: 100, ticks:{color:colors.axis}, grid:{color:colors.grid} },
                // reserve room for full host names (they were clipped on the left)
                y: { ticks:{color:colors.text, autoSkip:false}, grid:{display:false}, afterFit: (sc) => { sc.width = Math.max(sc.width, 190); } } } })
  });

  // Powered on / off: a single labelled bar instead of a neon status-green donut (power state is not a status).
  const vms = getFilteredVms();
  const onCount = vms.filter(v => v.isPoweredOn).length;
  const offCount = vms.length - onCount;
  const onPct = vms.length ? onCount / vms.length * 100 : 0;
  $('#powerStateBox').innerHTML = vms.length ? `
    <div class="split-bar" role="img" aria-label="${esc(`${onCount} VM bật, ${offCount} VM tắt`)}"><i style="width:${onPct.toFixed(2)}%"></i></div>
    <dl class="split-legend"><div><dt><span class="sw s1"></span>Đang bật</dt><dd class="num">${fmtInt(onCount)} <small>${fmtNum(onPct, 0)}%</small></dd></div>
      <div><dt><span class="sw s4"></span>Đang tắt</dt><dd class="num">${fmtInt(offCount)} <small>${fmtNum(100 - onPct, 0)}%</small></dd></div></dl>
    <p class="sm-legend">${fmtInt(vms.length)} VM trong phạm vi. VM tắt không tính vào CPU / RAM.</p>` : uiEmpty('Chưa có VM trong phạm vi.');

  if (showStorage) appState.charts.datastore = new Chart($('#chartDatastore'), {
    type: 'bar',
    data: {
      labels: dsRows.map(d => d.name),
      datasets: [
        { label:'Provisioned GB', data: dsRows.map(d => Number(d.provGB.toFixed(1))), backgroundColor: colors.other, borderRadius: 3 },
        { label:'Used GB', data: dsRows.map(d => Number(d.usedGB.toFixed(1))), backgroundColor: colors.series[0], borderRadius: 3 }
      ]
    },
    options: { maintainAspectRatio:false, plugins:{legend:{labels:{color:colors.text}}}, scales:{ x:{ticks:{color:colors.muted},grid:{color:colors.divider}}, y:{beginAtZero:true,ticks:{color:colors.muted},grid:{color:colors.divider}} } }
  });

  const cpuOcMax = appState.policy.cpuOvercommitMax;
  const ramOcMax = appState.policy.ramOvercommitMax;
  appState.charts.overcommit = new Chart($('#chartOvercommit'), {
    data: {
      labels: clusterRows.map(c => c.name),
      datasets: [
        { type:'bar', label:'CPU overcommit (x)', data: clusterRows.map(c => Number((c.model.cpuOvercommit||0).toFixed(2))), backgroundColor: colors.series[0], borderRadius: 3 },
        { type:'bar', label:'RAM overcommit (x)', data: clusterRows.map(c => Number((c.model.ramOvercommit||0).toFixed(2))), backgroundColor: colors.series[1], borderRadius: 3 },
        { type:'line', label:`Ngưỡng CPU (${fmtNum(cpuOcMax,1)}x)`, data: clusterRows.map(() => cpuOcMax), borderColor: colors.text, borderWidth: 1.5, borderDash: [6,4], pointRadius: 0, fill: false },  // limits in ink, not status colors
        { type:'line', label:`Ngưỡng RAM (${fmtNum(ramOcMax,1)}x)`, data: clusterRows.map(() => ramOcMax), borderColor: colors.muted, borderWidth: 1.5, borderDash: [2,3], pointRadius: 0, fill: false }
      ]
    },
    options: { maintainAspectRatio:false, plugins:{legend:{labels:{color:colors.text}}}, scales:{ x:{ticks:{color:colors.muted},grid:{color:colors.divider}}, y:{beginAtZero:true,ticks:{color:colors.muted},grid:{color:colors.divider}} } }
  });
}
/* =========================================================
   Render: Compliance & Inventory views
   ========================================================= */

/* ---------- Compliance ---------- */
const SEVERITY_LABEL = { critical:'Nghiêm trọng', warning:'Cảnh báo', info:'Thông tin' };   // same words as the PDF (SEVERITY_LABEL_VI)

function renderComplianceKpis() {
  const findings = buildComplianceFindings();
  const crit = findings.filter(f => f.severity === 'critical').length;
  const warn = findings.filter(f => f.severity === 'warning').length;
  const info = findings.filter(f => f.severity === 'info').length;
  const byType = {};
  findings.forEach(f => byType[f.type] = (byType[f.type]||0) + 1);

  const box = $('#complianceKpis');
  box.className = 'kpi-strip-wrap';
  box.innerHTML = uiKpiStrip([
    { label: 'Tổng phát hiện', valueHtml: `<span class="${crit ? 'tone-crit' : warn ? 'tone-warn' : ''}">${fmtInt(findings.length)}</span>`, sub: `Nghiêm trọng ${crit} · Cảnh báo ${warn} · Thông tin ${info}` },
    { label: 'Host CPU / RAM vượt ngưỡng', value: String((byType.host_cpu || 0) + (byType.host_ram || 0)), sub: 'Mức dùng của host vượt ngưỡng cấu hình' },
    { label: 'Cluster vượt ngưỡng overcommit', value: String((byType.overcommit_cpu || 0) + (byType.overcommit_ram || 0)), sub: 'vCPU:pCPU hoặc vRAM:pRAM vượt ngưỡng' },
    { label: 'VM cần rà soát', value: String((byType.vm_idle || 0) + (byType.vm_off || 0) + (byType.vm_overprovision || 0)), sub: 'Idle lâu, tắt lâu hoặc cấp dư vCPU' },
  ]);
}

const cpState = { selected: '', bound: false };
const CP_TONE = { critical: 'crit', warning: 'warn', info: 'na' };

/* Findings as a list grouped by severity → detail pane (pages/danh-sach.md). Same rows / filters as before
   (getFilteredCompliance); the detail shows the recommendation and links to the host / VM card. */
function renderComplianceTable() {
  const rows = getFilteredCompliance().slice().sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] || String(a.cluster).localeCompare(String(b.cluster)) || String(a.entity).localeCompare(String(b.entity)));
  const list = $('#complianceList');
  if (!list) return;
  $('#complianceRowCount').textContent = `${rows.length} phát hiện`;
  if (!rows.some(r => r.code === cpState.selected)) cpState.selected = rows.length ? rows[0].code : '';
  const groups = ['critical', 'warning', 'info'].map(sev => {
    const g = rows.filter(r => r.severity === sev);
    if (!g.length) return '';
    return `<li class="hm-group" role="presentation"><span>${esc(SEVERITY_LABEL[sev])}</span><span>${g.length}</span></li>` + g.map(f => `
      <li class="hm-row" role="option" id="cp-${esc(f.code)}" data-cp="${esc(f.code)}" aria-selected="${f.code === cpState.selected}">
        <span class="hm-lv lv-${CP_TONE[f.severity]}" aria-hidden="true"></span>
        <span class="hm-t">${esc(f.entity)}</span>
        <span class="hm-r"><code>${esc(f.code)}</code></span>
        <span class="hm-m">${esc(f.typeLabel)} · ${esc(f.cluster || '—')}${f.vcenterKey ? ' · ' + esc(f.vcenterKey) : ''}</span>
        <span class="sr-only">${esc(SEVERITY_LABEL[f.severity])}</span></li>`).join('');
  }).join('');
  list.innerHTML = groups || `<li role="presentation">${uiEmpty('Không có phát hiện nào khớp bộ lọc.')}</li>`;
  const sel = list.querySelector('[aria-selected="true"]');
  if (sel) list.setAttribute('aria-activedescendant', sel.id); else list.removeAttribute('aria-activedescendant');
  cpRenderDetail(rows);
  cpBind();
}

function cpRenderDetail(rows) {
  const box = $('#complianceDetail');
  const f = (rows || getFilteredCompliance()).find(r => r.code === cpState.selected);
  if (!box) return;
  if (!f) { box.innerHTML = uiEmpty('Chọn một phát hiện ở danh sách bên trái.'); return; }
  const entity = /^host_/.test(f.type) ? uiEntityLink('host', f.entity, f.vcenterKey) : /^vm_/.test(f.type) ? uiEntityLink('vm', f.entity, f.vcenterKey) : `<b>${esc(f.entity)}</b>`;
  box.innerHTML = `
    <div class="hm-d-top">${uiPill(CP_TONE[f.severity], SEVERITY_LABEL[f.severity])}<h3>${esc(f.typeLabel)}: ${esc(f.entity)}</h3></div>
    <div class="hm-d-meta"><span>Mã đối soát <code>${esc(f.code)}</code></span><span>Cluster ${esc(f.cluster || '—')}</span>${f.vcenterKey ? `<span>${esc(f.vcenterKey)}</span>` : ''}</div>
    <div class="hm-d-sec"><h4>Nên làm gì</h4><p>${esc(f.recommendation)}</p></div>
    <div class="hm-d-sec"><h4>Chỉ số</h4><p class="mono">${esc(f.metric)}</p></div>
    <div class="hm-d-sec"><h4>Đối tượng</h4><p>${entity}</p></div>
    <p class="muted-sub">Mã đối soát giống nhau trên web, PDF Vận hành và PDF Kỹ thuật — dùng để tra ngược từ báo cáo.</p>`;
}

function cpBind() {
  const list = $('#complianceList');
  if (!list || cpState.bound) return;
  cpState.bound = true;
  const select = (code, focus) => {
    cpState.selected = code;
    list.querySelectorAll('[role="option"]').forEach(o => o.setAttribute('aria-selected', String(o.dataset.cp === code)));
    const o = list.querySelector(`[data-cp="${CSS.escape(code)}"]`);
    if (o) { list.setAttribute('aria-activedescendant', o.id); o.scrollIntoView({ block: 'nearest' }); }
    cpRenderDetail();
    if (focus) list.focus();
    else if (matchMedia('(max-width:1023px)').matches) { const d = $('#complianceDetail'); if (d) d.scrollIntoView({ block: 'start' }); }
  };
  list.addEventListener('click', (e) => { const o = e.target.closest('[data-cp]'); if (o) select(o.dataset.cp); });
  list.addEventListener('keydown', (e) => {
    const codes = [...list.querySelectorAll('[data-cp]')].map(o => o.dataset.cp);
    const i = codes.indexOf(cpState.selected), k = e.key.toLowerCase();
    if (k === 'arrowdown' || k === 'j') { e.preventDefault(); if (codes.length) select(codes[Math.min(i + 1, codes.length - 1)], true); }
    else if (k === 'arrowup' || k === 'k') { e.preventDefault(); if (codes.length) select(codes[Math.max(i - 1, 0)], true); }
  });
}

function renderComplianceView() {
  renderComplianceKpis();
  renderComplianceTable();
}

/* ---------- Inventory: Host table ---------- */
// Rows exactly as the Host table shows them (global filter + search + sort);
// shared by the renderer and the Excel export so "what you see is what you get".
function getHostTableRows() {
  const rows = buildHostRows().filter(h => {
    if (appState.filters.cluster !== 'all' && h.cluster !== appState.filters.cluster) return false;
    if (appState.filters.host !== 'all' && h.host !== appState.filters.host) return false;
    if (appState.hostTableSearch.trim()) {
      const q = appState.hostTableSearch.trim().toLowerCase();
      if (!(h.host + ' ' + h.cluster).toLowerCase().includes(q)) return false;
    }
    return true;
  });
  return applySort(rows, 'hostTable', (r, key) => r[key]);
}

function renderHostTable() {
  const rows = getHostTableRows();
  const tbody = $('#hostTable tbody');
  $('#hostRowCount').textContent = `${rows.length} hosts`;
  if (!rows.length) { tbody.innerHTML = `<tr><td colspan="10"><div class="empty-note">Không có host phù hợp.</div></td></tr>`; return; }

  tbody.innerHTML = rows.map(h => `
    <tr>
      <td>${esc(h.cluster)}</td>
      <td>${uiEntityLink('host', h.host, h.vcenterKey)}</td>
      <td>${uiPill(h.overall.cls, h.overall.text)}</td>
      <td class="mono">${fmtInt(h.cores)}</td>
      <td class="mono">${fmtNum(h.ramGB,0)}</td>
      <td class="mono">${fmtInt(h.vcpuUsed)}</td>
      <td>${barCellHtml(h.cpuPct)}</td>
      <td class="mono">${fmtNum(h.ramUsedGB,0)}</td>
      <td>${barCellHtml(h.ramPct)}</td>
      <td class="mono">${h.vmCount}</td>
    </tr>
  `).join('');
}

/* ---------- Inventory: VM table ---------- */
function getVmTableRows() {
  const rows = getFilteredVms().filter(v => {
    if (appState.vmClusterFilter !== 'all' && v.cluster !== appState.vmClusterFilter) return false;
    if (appState.vmStatusFilter === 'on' && !v.isPoweredOn) return false;
    if (appState.vmStatusFilter === 'off' && v.isPoweredOn) return false;
    if (appState.vmTableSearch.trim()) {
      const q = appState.vmTableSearch.trim().toLowerCase();
      if (!(v.vm + ' ' + v.ipAddress + ' ' + v.cluster + ' ' + v.host).toLowerCase().includes(q)) return false;
    }
    return true;
  }).map(v => ({ ...v, diskUsedPct: v.diskProvGB > 0 ? (v.diskUsedGB / v.diskProvGB) * 100 : NaN }));
  return applySort(rows, 'vmTable', (r, key) => r[key]);
}

// Cluster dropdown of the VM table: clusters visible under the global
// vCenter/cluster filter; keeps the current choice when still valid.
function syncVmClusterFilterOptions() {
  const sel = $('#vmClusterFilter');
  if (!sel) return;
  const clusters = Array.from(new Set(getFilteredVms().map(v => v.cluster).filter(Boolean).map(String))).sort((a, b) => a.localeCompare(b));
  if (!clusters.includes(appState.vmClusterFilter)) appState.vmClusterFilter = 'all';
  sel.innerHTML = `<option value="all">Mọi cluster (${clusters.length})</option>` +
    clusters.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
  sel.value = appState.vmClusterFilter;
}

function renderVmTable() {
  const rows = getVmTableRows();
  const tbody = $('#vmTable tbody');
  $('#vmRowCount').textContent = `${rows.length} VMs`;
  if (!rows.length) { tbody.innerHTML = `<tr><td colspan="12"><div class="empty-note">Không có VM phù hợp.</div></td></tr>`; return; }

  tbody.innerHTML = rows.map(v => `
    <tr>
      <td>${esc(v.cluster)}</td>
      <td>${esc(v.host || '-')}</td>
      <td>${uiEntityLink('vm', v.vm, v.vcenterKey)}</td>
      <td><span class="tag${v.isPoweredOn ? ' tag-on' : ''}">${esc(v.isPoweredOn ? 'Đang bật' : 'Đang tắt')}</span></td>
      <td class="mono">${esc(v.ipAddress || '-')}</td>
      <td class="mono">${fmtInt(v.vcpu)}</td>
      <td class="mono">${fmtNum(v.ramGB,0)}</td>
      <td class="mono">${fmtNum(v.diskProvGB,0)}</td>
      <td class="mono">${fmtNum(v.diskUsedGB,0)}</td>
      <td>${barCellHtml(v.diskUsedPct)}</td>
      <td class="mono">${v.isPoweredOn && isFinite(v.lastActivityDays) ? fmtInt(v.lastActivityDays) : '-'}</td>
      <td class="mono">${!v.isPoweredOn && isFinite(v.powerOffDays) ? fmtInt(v.powerOffDays) : '-'}</td>
    </tr>
  `).join('');
}

function renderInventoryView() {
  syncVmClusterFilterOptions();
  renderHostTable();
  renderVmTable();
  bindSortableHeaders('hostTable', 'hostTable', renderHostTable);
  bindSortableHeaders('vmTable', 'vmTable', renderVmTable);
  highlightSortHeaders('hostTable', 'hostTable');
  highlightSortHeaders('vmTable', 'vmTable');
}

/* ---------- Export Excel (Inventory tables, as displayed) ---------- */
function num(v, digits) {
  if (v === null || v === undefined || !isFinite(v)) return null;
  return digits === undefined ? v : Number(Number(v).toFixed(digits));
}

async function exportInventoryXlsx(kind, btn) {
  const originalLabel = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = 'Đang tạo Excel...';
  try {
    const scope = [
      currentVcenterFilter && currentVcenterFilter !== 'all' ? `vCenter ${currentVcenterFilter}` : 'Toàn bộ vCenter',
      appState.filters.cluster !== 'all' ? `cluster ${appState.filters.cluster}` : null,
    ].filter(Boolean).join(' · ');
    let payload;
    if (kind === 'hosts') {
      const rows = getHostTableRows().map(h => ({
        cluster: h.cluster, host: h.host, cores: num(h.cores), ramGB: num(h.ramGB, 1), vcpuUsed: num(h.vcpuUsed),
        cpuPct: num(h.cpuPct, 1), ramUsedGB: num(h.ramUsedGB, 1), ramPct: num(h.ramPct, 1),
        cpuOvercommit: num(h.cpuOvercommit, 2), ramOvercommit: num(h.ramOvercommit, 2), vmCount: num(h.vmCount), status: h.overall.text,
      }));
      payload = { filename: 'ico_hosts', sheets: [{ name: 'Hosts', rows, columns: [
        { key: 'cluster', label: 'Cluster' }, { key: 'host', label: 'Host' }, { key: 'cores', label: 'CPU Cores', fmt: 'int' },
        { key: 'ramGB', label: 'RAM GB', fmt: 'num1' }, { key: 'vcpuUsed', label: 'vCPU dùng', fmt: 'int' },
        { key: 'cpuPct', label: 'CPU % (theo overcommit)', fmt: 'pct' }, { key: 'ramUsedGB', label: 'RAM dùng GB', fmt: 'num1' },
        { key: 'ramPct', label: 'RAM % (theo overcommit)', fmt: 'pct' }, { key: 'cpuOvercommit', label: 'CPU overcommit (x)' },
        { key: 'ramOvercommit', label: 'RAM overcommit (x)' }, { key: 'vmCount', label: 'Số VM', fmt: 'int' }, { key: 'status', label: 'Status' },
      ] }] };
    } else {
      const rows = getVmTableRows().map(v => ({
        cluster: v.cluster, host: v.host || '', vm: v.vm, powerState: v.powerState, ipAddress: v.ipAddress || '',
        vcpu: num(v.vcpu), ramGB: num(v.ramGB, 1), diskProvGB: num(v.diskProvGB, 1), diskUsedGB: num(v.diskUsedGB, 1),
        diskUsedPct: num(v.diskUsedPct, 1), datastore: v.datastore || '',
        lastActivityDays: v.isPoweredOn ? num(v.lastActivityDays) : null,
        powerOffDays: !v.isPoweredOn ? num(v.powerOffDays) : null,
      }));
      payload = { filename: 'ico_vms', sheets: [{ name: 'VMs', rows, columns: [
        { key: 'cluster', label: 'Cluster' }, { key: 'host', label: 'Host' }, { key: 'vm', label: 'VM' }, { key: 'powerState', label: 'Power' },
        { key: 'ipAddress', label: 'IP' }, { key: 'vcpu', label: 'vCPU', fmt: 'int' }, { key: 'ramGB', label: 'RAM GB', fmt: 'num1' },
        { key: 'diskProvGB', label: 'Disk Prov GB', fmt: 'num1' }, { key: 'diskUsedGB', label: 'Disk Used GB', fmt: 'num1' },
        { key: 'diskUsedPct', label: 'Disk Used %', fmt: 'pct' }, { key: 'datastore', label: 'Datastore' },
        { key: 'lastActivityDays', label: 'Idle (ngày)', fmt: 'int' }, { key: 'powerOffDays', label: 'Tắt (ngày)', fmt: 'int' },
      ] }] };
    }
    payload.sheets.push({ name: 'Phạm vi', columns: [{ key: 'k', label: 'Thông tin' }, { key: 'v', label: 'Giá trị' }], rows: [
      { k: 'Phạm vi', v: scope },
      { k: 'Số dòng', v: payload.sheets[0].rows.length },
      { k: 'Bộ lọc bảng', v: kind === 'hosts'
          ? (appState.hostTableSearch.trim() ? `tìm "${appState.hostTableSearch.trim()}"` : 'không')
          : [appState.vmClusterFilter !== 'all' ? `cluster ${appState.vmClusterFilter}` : null,
             appState.vmStatusFilter !== 'all' ? `power ${appState.vmStatusFilter}` : null,
             appState.vmTableSearch.trim() ? `tìm "${appState.vmTableSearch.trim()}"` : null].filter(Boolean).join(' · ') || 'không' },
      { k: 'Xuất lúc', v: new Date().toLocaleString('vi-VN') },
    ] });
    await api.downloadXlsx(payload);
  } catch (err) {
    showValidationBanner([`Không thể xuất Excel: ${err.message}`], []);
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalLabel;
  }
}

/* ---------- Export CSV ---------- */
function csvEscape(v) {
  const s = String(v ?? '');
  if (/[",\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}

function downloadBlob(filename, content, mime) {
  uiSaveBlob(new Blob([content], { type: mime }), filename);
}

function exportCsv() {
  const hostRows = buildHostRows();
  const vmRows = getFilteredVms();
  const compliance = buildComplianceFindings();

  const sections = [];

  sections.push('=== HOSTS ===');
  sections.push(['Cluster','Host','CPU Cores','RAM GB','vCPU Used','CPU % (overcommit)','RAM Used GB','RAM % (overcommit)','VM Count','Status'].join(','));
  hostRows.forEach(h => sections.push([h.cluster,h.host,h.cores,h.ramGB,h.vcpuUsed,(h.cpuPct||0).toFixed(1),h.ramUsedGB.toFixed(1),(h.ramPct||0).toFixed(1),h.vmCount,h.overall.text].map(csvEscape).join(',')));

  sections.push('');
  sections.push('=== VMS ===');
  sections.push(['Cluster','Host','VM','Power State','IP','vCPU','RAM GB','Disk Prov GB','Disk Used GB','Idle Days','Power Off Days'].join(','));
  vmRows.forEach(v => sections.push([v.cluster,v.host,v.vm,v.powerState,v.ipAddress,v.vcpu,v.ramGB,v.diskProvGB.toFixed(1),v.diskUsedGB.toFixed(1),v.lastActivityDays ?? '',v.powerOffDays ?? ''].map(csvEscape).join(',')));

  sections.push('');
  sections.push('=== COMPLIANCE FINDINGS ===');
  sections.push(['Code','Severity','Type','Entity','Cluster','Metric','Recommendation'].join(','));
  compliance.forEach(f => sections.push([f.code,f.severity,f.typeLabel,f.entity,f.cluster,f.metric,f.recommendation].map(csvEscape).join(',')));

  downloadBlob(`ico-capacity-export-${Date.now()}.csv`, sections.join('\n'), 'text/csv;charset=utf-8');
}
