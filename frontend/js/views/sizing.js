/* =========================================================
   Sizing máy chủ — size physical hosts from a requested-VM list.
   Input: the Dev team's VM list (typed in or imported from Excel/CSV),
   sizing assumptions and candidate host configurations.
   Output: hosts needed per configuration (N+x), binding resource,
   resulting overcommit, storage to provision, budget, plus a check of
   whether an existing cluster could absorb the request instead.
   Calculation runs on the backend (backend/app/sizing.py) so the Excel
   export and a future PDF share the same math.
   ========================================================= */

const SIZING_STORE_KEY = 'vco.sizing.v1';

const SIZING_DEFAULT_PARAMS = {
  environment: '', cpu_ratio: 3, ht_factor: 2, ram_ratio: 1.0, max_util_pct: 80, growth_pct: 20,
  redundancy: 1, min_hosts: 2, reserve_cores: 2, reserve_ram_gb: 16,
  storage_mode: 'san', storage_overhead_pct: 25, storage_protection_factor: 2.0,
  optimize_for: 'hosts', annual_growth_pct: 15, horizon_years: 3, tco_years: 5, warranty_included_years: 3,
  warranty_ext_mode: 'amount', warranty_ext_values: [], license_price_per_core: null, license_min_cores_per_socket: 16,
  license_edition: 'VMware vSphere Foundation (VVF)', platform: 'vmware',
};
// Advisory thresholds — mirror of backend sizing_advisory.AdvisoryThresholds / THRESHOLD_META.
const SIZING_DEFAULT_ADVISORY = {
  compute_max_gb_per_vcpu: 3, memory_min_gb_per_vcpu: 6, stranded_warn_pct: 35, balance_ok_gap_pct: 15, balance_min_load_pct: 40,
  db_cpu_per_core_max: 4, high_cpu_per_core_warn: 6, ram_ratio_warn: 1, low_runway_pct: 10, failover_comfort_pct: 60, failover_margin_pct: 2,
  vsan_free_min_pct: 25, cheaper_alt_pct: 3, cluster_info_hosts: 32, cluster_warn_hosts: 64,
  db_keywords: 'db, database, sql, mssql, mysql, postgres, postgresql, oracle, mongo, mongodb, redis, kafka, elastic, elasticsearch, sap, hana, cassandra, pi data archive, pi archive, data archive, piarchive, historian, osisoft, aveva',
  dc_keywords: 'dc, domain controller, active directory, ad ds, addc',
  backup_keywords: 'backup, veeam, commvault, netbackup, veritas, avamar, networker, rubrik, cohesity',
};
const SIZING_ADVISORY_META = [
  ['compute_max_gb_per_vcpu', 'Workload thiên CPU khi RAM/vCPU ≤', 'GB'],
  ['memory_min_gb_per_vcpu', 'Workload thiên RAM khi RAM/vCPU >', 'GB'],
  ['stranded_warn_pct', 'Cảnh báo tài nguyên dư khi ≥', '%'],
  ['balance_ok_gap_pct', 'Coi là cân đối khi CPU và RAM chênh <', '%'],
  ['balance_min_load_pct', 'Chỉ xét cân đối khi tải yếu tố quyết định ≥', '%'],
  ['db_cpu_per_core_max', 'Tỷ lệ chia sẻ CPU tối đa cho CSDL', 'vCPU/core'],
  ['high_cpu_per_core_warn', 'Cảnh báo tỷ lệ chia sẻ CPU >', 'vCPU/core'],
  ['ram_ratio_warn', 'Cảnh báo tỷ lệ chia sẻ RAM >', 'x'],
  ['low_runway_pct', 'Cảnh báo dư địa tăng trưởng <', '%'],
  ['failover_comfort_pct', 'Tải khi mất host được điểm tối đa ≤', '%'],
  ['failover_margin_pct', 'Sát ngưỡng khi tải lúc mất host cách mức tối đa ≤', 'điểm %'],
  ['vsan_free_min_pct', 'Dung lượng trống khuyến nghị cho lưu trữ HCI ≥', '%'],
  ['cheaper_alt_pct', 'Nêu phương án rẻ hơn khi TCO thấp hơn ≥', '%'],
  ['cluster_info_hosts', 'Gợi ý chia cụm khi số host >', 'host'],
  ['cluster_warn_hosts', 'Cảnh báo cụm quá lớn khi số host >', 'host'],
  ['db_keywords', 'Từ khoá nhận diện VM CSDL / middleware (phân cách bằng dấu phẩy)', ''],
  ['dc_keywords', 'Từ khoá nhận diện Domain Controller', ''],
  ['backup_keywords', 'Từ khoá nhận diện máy chủ backup', ''],
];
// Mirror of backend platforms.py (labels, licence rules, default reserves).
const SIZING_PLATFORMS = {
  vmware: { editions: ['VMware vSphere Foundation (VVF)', 'VMware Cloud Foundation (VCF)', 'VMware vSphere Standard (VVS)', 'VMware vSphere Enterprise Plus'],
    licMin: 16, reserve: [2, 16], hint: 'ESXi · vCenter · vSphere HA / DRS · vSAN. License theo core (tối thiểu 16 core / CPU).' },
  nutanix: { editions: ['Nutanix Cloud Infrastructure (NCI) Pro', 'Nutanix Cloud Infrastructure (NCI) Ultimate', 'Nutanix Cloud Platform (NCP)'],
    licMin: 0, reserve: [4, 48], hint: 'AHV · Prism · AOS. Mỗi host có Controller VM (CVM) ~8–12 vCPU, 32–48 GB RAM — đã đặt vào phần hypervisor chiếm.' },
  hyperv: { editions: ['Windows Server Datacenter', 'Windows Server Standard', 'Azure Stack HCI / Azure Local'],
    licMin: 8, reserve: [2, 16], hint: 'Hyper-V · Failover Clustering · S2D. License Windows Server theo core (tối thiểu 8 core / CPU, 16 core / host).' },
  proxmox: { editions: ['Proxmox VE Subscription Basic', 'Proxmox VE Subscription Standard', 'Proxmox VE Subscription Premium', 'Proxmox VE Community'],
    licMin: 0, reserve: [4, 32], hint: 'KVM · Proxmox HA · Ceph. Subscription theo CPU socket; Ceph cần thêm RAM/CPU trên mỗi host.' },
  generic: { editions: ['Theo nền tảng được chọn'], licMin: 16, reserve: [2, 16],
    hint: 'Báo cáo dùng khái niệm chung (hypervisor, HA, lưu trữ HCI…), phụ lục có bảng tên gọi tương ứng các nền tảng.' },
};

function sizingFillEditions(platform, keep) {
  const sel = $('#szLicEdition');
  if (!sel) return;
  const pf = SIZING_PLATFORMS[platform] || SIZING_PLATFORMS.vmware;
  sel.innerHTML = pf.editions.map(e => `<option>${esc(e)}</option>`).join('');
  if (keep && pf.editions.includes(keep)) sel.value = keep;
  const hint = $('#szPlatformHint');
  if (hint) hint.textContent = pf.hint;
}

/* Switching platform applies its licence rule, edition list and typical hypervisor reserve. */
function sizingApplyPlatform(platform) {
  const pf = SIZING_PLATFORMS[platform] || SIZING_PLATFORMS.vmware;
  const p = sizingState().params;
  p.platform = platform;
  p.license_min_cores_per_socket = pf.licMin;
  p.reserve_cores = pf.reserve[0];
  p.reserve_ram_gb = pf.reserve[1];
  p.license_edition = pf.editions[0];
  sizingFillEditions(platform, p.license_edition);
  ['szLicMin', 'szReserveCores', 'szReserveRam'].forEach(id => { const el = document.getElementById(id); if (el) el.value = { szLicMin: pf.licMin, szReserveCores: pf.reserve[0], szReserveRam: pf.reserve[1] }[id]; });
  persistSizing();
}

const SIZING_DEFAULT_DOC = { customerName: '', customerContact: '', preparedOrg: '', preparedBy: '', approvedBy: '', documentCode: '',
  version: '1.0', versionNote: '', inputSource: '', inputDate: '', outOfScope: '' };
// Series colours: chartPalette().series (s1–s3) + .other grey; chartSeriesColor(i) greys out series after the 3rd.
const SIZING_DEFAULT_PROFILES = [
  { name: '2×16C / 512 GB', sockets: 2, coresPerSocket: 16, ramGB: 512, diskGB: 0, unitPrice: null },
  { name: '2×24C / 768 GB', sockets: 2, coresPerSocket: 24, ramGB: 768, diskGB: 0, unitPrice: null },
  { name: '2×32C / 1 TB', sockets: 2, coresPerSocket: 32, ramGB: 1024, diskGB: 0, unitPrice: null },
];

let sizingSeq = 1;
const nextSizingId = () => `sz${Date.now().toString(36)}${(sizingSeq++).toString(36)}`;

function sizingState() {
  if (!appState.sizing) {
    appState.sizing = { vms: [], params: { ...SIZING_DEFAULT_PARAMS }, profiles: deepClone(SIZING_DEFAULT_PROFILES), doc: { ...SIZING_DEFAULT_DOC }, report: sizingDefaultReport(), result: null, warnings: [], bound: false, sensKey: 'growth' };
    try {
      const raw = localStorage.getItem(SIZING_STORE_KEY);
      if (raw) {
        const saved = JSON.parse(raw);
        if (Array.isArray(saved.vms)) appState.sizing.vms = saved.vms;
        if (saved.params) appState.sizing.params = { ...SIZING_DEFAULT_PARAMS, ...saved.params };
        if (saved.doc) appState.sizing.doc = { ...SIZING_DEFAULT_DOC, ...saved.doc };
        if (saved.report) appState.sizing.report = { ...sizingDefaultReport(), ...saved.report };
        // Older builds auto-filled the sample project name into "Tên dự án / hệ thống" and persisted it; drop it once.
        if (/^Ví dụ\s*[—-]\s*ứng dụng 3 lớp$/i.test(String(appState.sizing.params.environment || '').trim())) appState.sizing.params.environment = '';
        if (Array.isArray(saved.profiles) && saved.profiles.length) appState.sizing.profiles = saved.profiles;
      }
    } catch (e) { /* ignore corrupt storage */ }
    appState.sizing.params.advisory = { ...SIZING_DEFAULT_ADVISORY, ...(appState.sizing.params.advisory || {}) };
    appState.sizing.vms.forEach(v => { if (!v.id) v.id = nextSizingId(); });
    appState.sizing.profiles.forEach(p => { if (!p.id) p.id = nextSizingId(); });
  }
  return appState.sizing;
}

function persistSizing() {
  const st = sizingState();
  try {
    localStorage.setItem(SIZING_STORE_KEY, JSON.stringify({ vms: st.vms, params: st.params, profiles: st.profiles, doc: st.doc, report: st.report }));
  } catch (e) { /* storage full / disabled */ }
}

/* ---------- Formatting helpers ---------- */
const szNum = (v, d = 0) => (v === null || v === undefined || !isFinite(v)) ? '—' : Number(v).toLocaleString('vi-VN', { maximumFractionDigits: d, minimumFractionDigits: d });
const szGB = (gb) => (gb === null || gb === undefined || !isFinite(gb)) ? '—' : (gb >= 1024 ? `${szNum(gb / 1024, 1)} TB` : `${szNum(gb)} GB`);
const szMoney = (v) => uiNumber(v, 3);
const szOptNum = (v) => { const s = String(v ?? '').trim(); if (!s) return null; const n = cleanNum(s); return isFinite(n) && n > 0 ? n : null; };

/* ---------- Params <-> DOM ---------- */
const SIZING_PARAM_FIELDS = {
  environment: 'szEnv', cpu_ratio: 'szCpuRatio', ht_factor: 'szHt', ram_ratio: 'szRamRatio', max_util_pct: 'szUtil',
  growth_pct: 'szGrowth', redundancy: 'szRedundancy', min_hosts: 'szMinHosts', reserve_cores: 'szReserveCores',
  reserve_ram_gb: 'szReserveRam', storage_mode: 'szStorageMode', storage_overhead_pct: 'szStorageOverhead',
  storage_protection_factor: 'szProtection', optimize_for: 'szOptimize', annual_growth_pct: 'szAnnualGrowth',
  horizon_years: 'szHorizon', license_price_per_core: 'szLicPrice', license_min_cores_per_socket: 'szLicMin', license_edition: 'szLicEdition',
  platform: 'szPlatform', tco_years: 'szTcoYears', warranty_included_years: 'szWarrantyInc', warranty_ext_mode: 'szWarrantyMode',
};
const SIZING_DOC_FIELDS = { customerName: 'szCustomer', customerContact: 'szContact', preparedOrg: 'szPreparedOrg', preparedBy: 'szPreparedBy',
  approvedBy: 'szApprovedBy', documentCode: 'szDocCode', version: 'szVersion', versionNote: 'szVersionNote',
  inputSource: 'szInputSource', inputDate: 'szInputDate', outOfScope: 'szOutOfScope' };

/* One input per warranty-extension year (years after the warranty included with the hardware). */
function renderWarrantyYears(force = false) {
  const box = $('#szWarrantyYears');
  if (!box) return;
  const p = sizingState().params;
  const years = Math.max(1, Number(p.tco_years) || 5);
  const inc = Math.max(0, Math.min(years, Number(p.warranty_included_years) || 0));
  const vals = Array.isArray(p.warranty_ext_values) ? p.warranty_ext_values : [];
  const pct = p.warranty_ext_mode === 'pct';
  const n = years - inc;
  // Rebuild only when the set of years or the unit changes — rebuilding while the user is moving
  // into one of these inputs would destroy the field they just clicked.
  const sig = `${inc}|${n}|${pct ? 'pct' : 'amount'}`;
  if (!force && box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  if (n <= 0) { box.innerHTML = ''; return; }
  box.innerHTML = Array.from({ length: n }, (_, i) => `
    <div class="field"><label for="szWy${i}">Gia hạn BH năm ${inc + i + 1} ${pct ? '(% giá host)' : '(/ host)'}</label>
      <input id="szWy${i}" data-wy="${i}" value="${vals[i] == null ? '' : esc(vals[i])}" placeholder="${pct ? 'VD: 8' : 'VD: 30.000.000'}"></div>`).join('')
    + `<div class="sz-wy-note">Năm 1–${inc}: bảo hành đã gồm trong giá mua. Để trống = chưa có giá (TCO sẽ ghi chú thiếu).</div>`;
  box.querySelectorAll('input[data-wy]').forEach(inp => inp.addEventListener('change', () => {
    const arr = (Array.isArray(p.warranty_ext_values) ? p.warranty_ext_values.slice() : []);
    const i = Number(inp.dataset.wy);
    while (arr.length < n) arr.push(null);
    const v = String(inp.value).trim();
    arr[i] = v === '' ? null : Math.max(0, cleanNum(v));
    p.warranty_ext_values = arr.slice(0, n);
    persistSizing();
  }));
}

function sizingParamsToDom() {
  const p = sizingState().params;
  sizingFillEditions(p.platform || 'vmware', p.license_edition);
  renderWarrantyYears();
  Object.entries(SIZING_PARAM_FIELDS).forEach(([key, id]) => {
    const el = document.getElementById(id);
    if (el) el.value = p[key] ?? '';
  });
  const doc = sizingState().doc;
  Object.entries(SIZING_DOC_FIELDS).forEach(([key, id]) => { const el = document.getElementById(id); if (el) el.value = doc[key] || ''; });
  $('#szProtectionField').classList.toggle('hidden', p.storage_mode !== 'local');
}

function sizingParamsFromDom() {
  const p = sizingState().params;
  Object.entries(SIZING_PARAM_FIELDS).forEach(([key, id]) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (key === 'environment' || key === 'storage_mode' || key === 'optimize_for' || key === 'license_edition' || key === 'platform' || key === 'warranty_ext_mode') p[key] = el.value.trim();
    else if (key === 'tco_years') p[key] = Math.min(10, Math.max(1, Math.round(cleanNum(el.value)) || 5));
    else if (key === 'warranty_included_years') p[key] = Math.min(10, Math.max(0, Math.round(cleanNum(el.value)) || 0));
    else if (key === 'license_price_per_core') p[key] = szOptNum(el.value);
    else if (key === 'redundancy' || key === 'min_hosts' || key === 'license_min_cores_per_socket') p[key] = Math.max(0, Math.round(cleanNum(el.value)));
    else if (key === 'horizon_years') p[key] = Math.min(10, Math.max(1, Math.round(cleanNum(el.value)) || 3));
    else p[key] = cleanNum(el.value);
  });
  if (p.min_hosts < 1) p.min_hosts = 1;
  const doc = sizingState().doc;
  Object.entries(SIZING_DOC_FIELDS).forEach(([key, id]) => { const el = document.getElementById(id); if (el) doc[key] = el.value.trim(); });
  $('#szProtectionField').classList.toggle('hidden', p.storage_mode !== 'local');
  persistSizing();
  return p;
}

/* ---------- Advisory thresholds ---------- */
function renderSizingAdvisoryFields() {
  const box = $('#szAdvFields');
  if (!box) return;
  const adv = sizingState().params.advisory;
  box.innerHTML = SIZING_ADVISORY_META.map(([key, label, unit]) => {
    const changed = String(adv[key]) !== String(SIZING_DEFAULT_ADVISORY[key]);
    const wide = key === 'db_keywords';
    return `<div class="field ${wide ? 'sz-adv-wide' : ''} ${changed ? 'sz-adv-changed' : ''}"><label for="szAdv_${key}" title="Mặc định: ${esc(SIZING_DEFAULT_ADVISORY[key])}${unit ? ' ' + unit : ''}">${esc(label)}${unit ? ` (${unit})` : ''}${changed ? ' *' : ''}</label>
      <input id="szAdv_${key}" data-adv="${key}" value="${esc(adv[key])}"></div>`;
  }).join('');
  box.querySelectorAll('input[data-adv]').forEach(inp => inp.addEventListener('change', () => {
    const key = inp.dataset.adv;
    if (key === 'db_keywords') adv[key] = inp.value.trim();
    else {
      const n = cleanNum(inp.value);
      adv[key] = isFinite(n) && n > 0 ? (key.startsWith('cluster_') ? Math.round(n) : n) : SIZING_DEFAULT_ADVISORY[key];
    }
    persistSizing();
    renderSizingAdvisoryFields();
  }));
  const det = $('#szAdvDetails');
  if (det && SIZING_ADVISORY_META.some(([k]) => String(adv[k]) !== String(SIZING_DEFAULT_ADVISORY[k]))) det.open = true;
}

/* ---------- VM table ---------- */
function sizingTotals(vms) {
  return vms.reduce((a, v) => {
    const q = Math.max(0, Math.round(cleanNum(v.qty)));
    a.count += q; a.vcpu += q * cleanNum(v.vcpu); a.ram += q * cleanNum(v.ramGB); a.disk += q * cleanNum(v.diskGB);
    return a;
  }, { count: 0, vcpu: 0, ram: 0, disk: 0 });
}

function renderSizingVmTable() {
  const st = sizingState();
  const tbody = $('#sizingVmTable tbody');
  if (!st.vms.length) {
    tbody.innerHTML = `<tr><td colspan="11"><div class="empty-note">Chưa có VM nào. Bấm <b>+ Thêm dòng</b> để nhập tay, <b>Import Excel/CSV</b> để đọc file từ team Dev, hoặc <b>Nạp ví dụ</b> để xem thử.</div></td></tr>`;
  } else {
    tbody.innerHTML = st.vms.map((v, i) => {
      const q = Math.max(0, Math.round(cleanNum(v.qty)));
      return `
      <tr data-id="${v.id}">
        <td class="muted">${i + 1}</td>
        <td><input class="sz-cell sz-name" value="${esc(v.name)}" placeholder="VD: APP-WEB"></td>
        <td><input class="sz-cell sz-qty num" value="${esc(v.qty)}"></td>
        <td><input class="sz-cell sz-vcpu num" value="${esc(v.vcpu)}"></td>
        <td><input class="sz-cell sz-ram num" value="${esc(v.ramGB)}"></td>
        <td><input class="sz-cell sz-disk num" value="${esc(v.diskGB)}"></td>
        <td class="sz-total">${szNum(q * cleanNum(v.vcpu))}</td>
        <td class="sz-total">${szGB(q * cleanNum(v.ramGB))}</td>
        <td class="sz-total">${szGB(q * cleanNum(v.diskGB))}</td>
        <td><input class="sz-cell sz-note" value="${esc(v.note || '')}" placeholder="Vai trò, OS…"></td>
        <td><button class="btn btn-secondary btn-sm icon-btn sz-remove" type="button" title="Xoá dòng">✕</button></td>
      </tr>`;
    }).join('');
  }
  const t = sizingTotals(st.vms);
  $('#sizingVmFoot').innerHTML = st.vms.length
    ? `<b>${st.vms.length}</b> dòng · <b>${szNum(t.count)}</b> VM · <b>${szNum(t.vcpu)}</b> vCPU · <b>${szGB(t.ram)}</b> RAM · <b>${szGB(t.disk)}</b> disk`
    : '';

  // Bind cell edits: update state on input, recompute totals on change.
  $$('#sizingVmTable tbody tr[data-id]').forEach(tr => {
    const row = st.vms.find(v => v.id === tr.dataset.id);
    if (!row) return;
    const sync = () => {
      row.name = tr.querySelector('.sz-name').value;
      row.qty = tr.querySelector('.sz-qty').value;
      row.vcpu = tr.querySelector('.sz-vcpu').value;
      row.ramGB = tr.querySelector('.sz-ram').value;
      row.diskGB = tr.querySelector('.sz-disk').value;
      row.note = tr.querySelector('.sz-note').value;
      persistSizing();
    };
    tr.querySelectorAll('.sz-cell').forEach(inp => {
      inp.addEventListener('input', sync);
      inp.addEventListener('change', () => { sync(); renderSizingVmTable(); });
      inp.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); sync(); if (tr === tr.parentElement.lastElementChild) { addSizingVmRow(); } else { renderSizingVmTable(); } }
      });
    });
    tr.querySelector('.sz-remove').addEventListener('click', () => {
      st.vms = st.vms.filter(v => v.id !== row.id);
      persistSizing(); renderSizingVmTable();
    });
  });
}

function addSizingVmRow(preset) {
  const st = sizingState();
  st.vms.push({ id: nextSizingId(), name: '', qty: 1, vcpu: 4, ramGB: 16, diskGB: 100, note: '', ...(preset || {}) });
  persistSizing();
  renderSizingVmTable();
  const last = $('#sizingVmTable tbody tr:last-child .sz-name');
  if (last) last.focus();
}

/* ---------- Candidate host profiles ---------- */
function renderSizingProfiles() {
  const st = sizingState();
  const list = $('#sizingProfiles');
  const local = st.params.storage_mode === 'local';
  list.innerHTML = st.profiles.map(p => `
    <div class="multi-row-item sz-profile" data-id="${p.id}">
      <div class="sim-form-grid sz-profile-grid">
        <div class="field"><label>Tên cấu hình</label><input class="pName" value="${esc(p.name)}" placeholder="VD: R760 2×24C"></div>
        <div class="field"><label>Sockets</label><input class="pSockets" value="${esc(p.sockets)}"></div>
        <div class="field"><label>Cores / socket</label><input class="pCores" value="${esc(p.coresPerSocket)}"></div>
        <div class="field"><label>RAM GB / host</label><input class="pRam" value="${esc(p.ramGB)}"></div>
        <div class="field ${local ? '' : 'hidden'}"><label>Đĩa cục bộ GB / host</label><input class="pDisk" value="${esc(p.diskGB || 0)}"></div>
        <div class="field"><label>Đơn giá / host (tuỳ chọn)</label><input class="pPrice" value="${p.unitPrice == null ? '' : esc(p.unitPrice)}" placeholder="VND"></div>
      </div>
      <button class="btn btn-secondary btn-sm icon-btn sz-profile-remove" type="button" title="Xoá cấu hình" ${st.profiles.length <= 1 ? 'disabled' : ''}>✕</button>
    </div>`).join('');

  $$('#sizingProfiles .sz-profile').forEach(el => {
    const p = st.profiles.find(x => x.id === el.dataset.id);
    if (!p) return;
    const sync = () => {
      p.sockets = Math.max(1, Math.round(cleanNum(el.querySelector('.pSockets').value)) || 1);
      p.coresPerSocket = Math.max(1, Math.round(cleanNum(el.querySelector('.pCores').value)) || 1);
      p.ramGB = Math.max(1, cleanNum(el.querySelector('.pRam').value) || 1);
      p.diskGB = cleanNum(el.querySelector('.pDisk').value);
      p.unitPrice = szOptNum(el.querySelector('.pPrice').value);
      const typed = el.querySelector('.pName').value.trim();
      p.name = typed || `${p.sockets}×${p.coresPerSocket}C / ${p.ramGB >= 1024 ? (p.ramGB / 1024).toLocaleString('vi-VN', { maximumFractionDigits: 1 }) + ' TB' : p.ramGB + ' GB'}`;
      persistSizing();
    };
    el.querySelectorAll('input').forEach(inp => inp.addEventListener('change', () => { sync(); renderSizingProfiles(); }));
    el.querySelector('.sz-profile-remove').addEventListener('click', () => {
      st.profiles = st.profiles.filter(x => x.id !== p.id);
      persistSizing(); renderSizingProfiles();
    });
  });
}

/* ---------- Payload + run ---------- */
function sizingPayload() {
  const st = sizingState();
  sizingParamsFromDom();
  const vms = st.vms
    .map(v => ({ name: String(v.name || '').trim(), qty: Math.max(0, Math.round(cleanNum(v.qty))), vcpu: cleanNum(v.vcpu), ramGB: cleanNum(v.ramGB), diskGB: cleanNum(v.diskGB), note: String(v.note || '') }))
    .filter(v => v.qty > 0 && (v.vcpu > 0 || v.ramGB > 0));
  const profiles = st.profiles.map(p => ({ name: p.name, sockets: p.sockets, coresPerSocket: p.coresPerSocket, ramGB: p.ramGB, diskGB: p.diskGB || 0, unitPrice: p.unitPrice == null ? null : p.unitPrice }));
  const params = { ...st.params, advisory: { ...SIZING_DEFAULT_ADVISORY, ...(st.params.advisory || {}) } };
  if (params.storage_mode !== 'local') params.storage_protection_factor = 1.0;
  const nExt = Math.max(0, (Number(params.tco_years) || 5) - (Number(params.warranty_included_years) || 0));
  params.warranty_ext_values = (Array.isArray(params.warranty_ext_values) ? params.warranty_ext_values : []).slice(0, nExt)
    .map(v => (v === '' || v == null || !isFinite(v)) ? null : Number(v));
  const rep = st.report || sizingDefaultReport();
  params.pick_profile = rep.pick || '';
  params.host_override = Math.max(0, Math.round(cleanNum(rep.hosts)) || 0);
  return { vms, params, profiles };
}

async function runSizing(btn, scroll = true) {
  const payload = sizingPayload();
  if (!payload.vms.length) {
    showValidationBanner(['Danh sách VM trống hoặc chưa có dòng nào hợp lệ (cần số lượng > 0 và vCPU/RAM > 0).'], []);
    return;
  }
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = 'Đang tính…'; }
  try {
    const result = await api.calcSizing(payload);
    sizingState().result = result;
    renderSizingAdjust(result);
    renderSizingResult(result, payload, scroll);
  } catch (err) {
    showValidationBanner([`Không tính được sizing: ${err.message}`], []);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

/* ---------- Existing-cluster absorption check ---------- */
function sizingAbsorptionRows(demand) {
  if (!appState.hosts || !appState.hosts.length) return [];
  const clusters = getClusterList();
  return clusters.map(name => {
    const hosts = appState.hosts.filter(h => h.cluster === name);
    const vms = appState.vms.filter(v => v.cluster === name);
    const before = buildCapacityModel(hosts, vms);
    const synthetic = { vm: '__sizing__', cluster: name, isPoweredOn: true, vcpu: demand.vcpuPlanned, ramGB: demand.ramPlannedGB, diskProvGB: 0, diskUsedGB: 0 };
    const after = buildCapacityModel(hosts, vms.concat([synthetic]));
    // N+1 after absorbing: drop the largest host.
    let n1 = null;
    if (hosts.length > 1) {
      const biggest = hosts.reduce((a, h) => (h.ramGB > a.ramGB ? h : a), hosts[0]);
      const rest = hosts.filter(h => h !== biggest);
      const m = buildCapacityModel(rest, vms.concat([synthetic]));
      n1 = { cpuPct: m.cpuPct, ramPct: m.ramPct, ok: m.cpuPct <= 100 && m.ramPct <= 100 };
    }
    const worst = Math.max(after.cpuPct || 0, after.ramPct || 0);
    const cls = worst > 100 ? 'crit' : worst > 80 ? 'warn' : 'ok';
    // Headroom left after absorbing: how many more project-average VMs fit under 80% of the policy ceiling.
    let extraAvgVms = null;
    if (demand.avgVcpu > 0 && demand.avgRamGB > 0 && isFinite(after.cpuPct) && isFinite(after.ramPct)) {
      const capVcpu = after.totalLogicalCores * appState.policy.cpuOvercommitMax * 0.8;
      const capRam = after.totalRamGB * appState.policy.ramOvercommitMax * 0.8;
      extraAvgVms = Math.max(0, Math.floor(Math.min((capVcpu - after.usedVcpu) / demand.avgVcpu, (capRam - after.usedRamGB) / demand.avgRamGB)));
    }
    return { name, hostCount: hosts.length, before, after, n1, cls, extraAvgVms };
  });
}

/* ---------- View entry + bindings ---------- */
function bindSizingView() {
  const st = sizingState();
  if (st.bound) return;
  st.bound = true;

  $('#btnSizingAddRow').addEventListener('click', () => addSizingVmRow());
  $('#btnSizingClear').addEventListener('click', async () => {
    if (!st.vms.length || await uiConfirm('Xoá toàn bộ danh sách VM yêu cầu?')) { st.vms = []; persistSizing(); renderSizingVmTable(); $('#sizingResult').classList.add('hidden'); $('#sizingAdjust').classList.add('hidden'); }
  });
  $('#btnSizingImport').addEventListener('click', () => $('#sizingFile').click());
  $('#sizingFile').addEventListener('change', e => { importSizingFile(e.target.files && e.target.files[0]); e.target.value = ''; });
  $('#btnSizingTemplate').addEventListener('click', async e => {
    const b = e.currentTarget; const l = b.textContent; b.disabled = true; b.textContent = 'Đang tải…';
    try { await api.downloadSizingTemplate(); } catch (err) { showValidationBanner([`Không tải được file mẫu: ${err.message}`], []); }
    finally { b.disabled = false; b.textContent = l; }
  });
  $('#btnSizingSample').addEventListener('click', loadSizingSample);
  $('#btnSizingAddProfile').addEventListener('click', () => {
    st.profiles.push({ id: nextSizingId(), name: '', sockets: 2, coresPerSocket: 32, ramGB: 1024, diskGB: 0, unitPrice: null });
    const last = st.profiles[st.profiles.length - 1];
    last.name = `${last.sockets}×${last.coresPerSocket}C / 1 TB`;
    persistSizing(); renderSizingProfiles();
  });
  $('#btnSizingRun').addEventListener('click', e => runSizing(e.currentTarget));
  bindSizingAdjust();
  $('#btnSzAdvReset').addEventListener('click', () => { st.params.advisory = { ...SIZING_DEFAULT_ADVISORY }; persistSizing(); renderSizingAdvisoryFields(); });
  $('#btnSizingExport').addEventListener('click', e => exportSizingXlsx(e.currentTarget));
  $('#btnSizingPdf').addEventListener('click', e => exportSizingPdf(e.currentTarget));
  $('#sizingPdfDropdown').addEventListener('toggle', e => {
    if (!e.currentTarget.open) return;
    szPdfStatus('');
    refreshSizingLogoPreview();
    try { refreshSizingPdfOptions(); }
    catch (err) { console.error('Sizing PDF options failed', err); szPdfStatus(`Không nạp được danh sách cluster: ${err.message}`, true); }
  });
  $('#btnSzLogoBrowse').addEventListener('click', () => $('#szLogoFile').click());
  $('#szLogoFile').addEventListener('change', e => { const f = e.target.files && e.target.files[0]; e.target.value = ''; onSizingLogoFile(f).catch(err => szPdfStatus(err.message, true)); });
  $('#btnSzLogoReset').addEventListener('click', () => { setSizingLogoOverride(null); refreshSizingLogoPreview(); });
  document.querySelectorAll('input[name="szPdfMode"]').forEach(r => r.addEventListener('change', refreshSizingPdfOptions));
  document.addEventListener('click', e => {
    const dd = $('#sizingPdfDropdown');
    if (dd && dd.open && !dd.contains(e.target)) dd.removeAttribute('open');
  });

  Object.values(SIZING_DOC_FIELDS).forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', () => sizingParamsFromDom());
  });
  Object.values(SIZING_PARAM_FIELDS).forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('change', () => {
      if (id === 'szPlatform') sizingApplyPlatform(el.value);
      sizingParamsFromDom();
      if (id === 'szWarrantyMode') sizingState().params.warranty_ext_values = [];  // amounts and % are different units
      if (id === 'szTcoYears' || id === 'szWarrantyInc' || id === 'szWarrantyMode') {
        // Let the click that triggered this change land first, then rebuild and give focus back.
        setTimeout(() => {
          const active = document.activeElement;
          const idx = active && active.dataset && active.dataset.wy != null ? Number(active.dataset.wy) : null;
          renderWarrantyYears(id === 'szWarrantyMode');
          if (idx != null) { const again = document.getElementById(`szWy${idx}`); if (again) again.focus(); }
          persistSizing();
        }, 0);
      }
      if (id === 'szStorageMode') renderSizingProfiles();
    });
  });
}

function renderSizingView() {
  sizingState();
  bindSizingView();
  sizingParamsToDom();
  renderSizingAdvisoryFields();
  renderSizingVmTable();
  renderSizingProfiles();
  if (appState.sizing.result) { renderSizingAdjust(appState.sizing.result); renderSizingResult(appState.sizing.result, sizingPayload(), false); }
}

window.renderSizingView = renderSizingView;
