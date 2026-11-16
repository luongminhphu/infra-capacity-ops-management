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
// Series colours: chartPalette().series (tokens --chart-s1…8).
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

/* ---------- Result rendering ---------- */
function renderSizingResult(result, payload, scroll = true) {
  const area = $('#sizingResult');
  const d = result.demand;
  const rows = result.results;
  const best = result.recommendedIndex != null ? rows[result.recommendedIndex] : null;
  const p = result.params;
  const anyPrice = rows.some(r => r.budget != null);
  const local = p.storage_mode === 'local';

  const tiles = `
    <div class="kpi-grid sz-kpis">
      <article class="kpi-card"><div class="label">Số VM yêu cầu</div><div class="value">${szNum(d.vmCount)}</div><div class="sub">${payload.vms.length} dòng · TB ${szNum(d.avgVcpu, 1)} vCPU / ${szGB(d.avgRamGB)} / VM</div></article>
      <article class="kpi-card"><div class="label">vCPU</div><div class="value">${szNum(d.vcpu)} <span class="sz-arrow">→</span> ${szNum(d.vcpuPlanned)}</div><div class="sub">sau +${szNum(p.growth_pct)}% tăng trưởng · cần ${szNum(d.logicalCpuNeeded)} pCPU logic @ ${p.cpu_ratio}x</div></article>
      <article class="kpi-card"><div class="label">RAM</div><div class="value">${szGB(d.ramGB)} <span class="sz-arrow">→</span> ${szGB(d.ramPlannedGB)}</div><div class="sub">sau tăng trưởng · RAM vật lý cần ${szGB(d.physRamNeededGB)} @ ${p.ram_ratio}x</div></article>
      <article class="kpi-card"><div class="label">${local ? 'Lưu trữ cục bộ cần' : 'Dung lượng SAN cần cấp'}</div><div class="value">${szGB(d.storageNeededGB)}</div><div class="sub">từ ${szGB(d.diskGB)} yêu cầu · +${szNum(p.growth_pct)}% tăng trưởng · +${szNum(p.storage_overhead_pct)}% overhead${local ? ` · ×${p.storage_protection_factor} bảo vệ` : ''}</div></article>
    </div>`;

  const cmpRows = rows.map(r => {
    const pr = r.profile;
    const failTxt = r.failure.hostsLeft ? `${szNum(r.failure.cpuPct)}% / ${szNum(r.failure.ramPct)}%` : '—';
    const failPill = r.redundancy === 0 ? '<span class="pill na">N</span>'
      : (!r.failure.ok ? '<span class="pill crit">Chưa đạt</span>' : (r.failure.atLimit ? '<span class="pill warn">Sát ngưỡng</span>' : `<span class="pill ok">Đạt N+${r.redundancy}</span>`));
    const status = r.suggested ? '<span class="pill na" title="Phương án tham khảo do hệ thống gợi ý">Tham khảo</span>'
      : (r.recommended ? '<span class="pill ok">Đề xuất</span>' : (r.tight ? '<span class="pill warn">Sát ngưỡng</span>' : `<span class="muted-sub">#${r.rank}</span>`));
    const storageCell = local
      ? `${szGB(r.storage.perHostNeededGB)}/host ${r.storage.ok === false ? '<span class="pill crit">thiếu đĩa</span>' : ''}`
      : '';
    return `
      <tr class="${r.recommended ? 'sz-best' : ''}${r.suggested ? ' sz-suggested' : ''}">
        <td><b>${esc(pr.name)}</b><div class="muted-sub">${esc(pr.sockets)}×${esc(pr.coresPerSocket)}C = ${esc(pr.cores)} cores · ${szGB(pr.ramGB)} RAM${local ? ` · ${szGB(pr.diskGB)} đĩa` : ''}</div></td>
        <td class="num">${r.hostsForCpu}</td>
        <td class="num">${r.hostsForRam}</td>
        <td class="num">+${r.redundancy}</td>
        <td class="num sz-hosts"><b>${r.hostsTotal}</b></td>
        <td>${esc(r.binding)}</td>
        <td class="num">${szNum(r.totals.cores)} / ${szGB(r.totals.ramGB)}</td>
        <td class="num">${r.vcpuPerCore == null ? '—' : szNum(r.vcpuPerCore, 2) + ' : 1'}</td>
        <td class="num">${failTxt} ${failPill}</td>
        <td class="num">${r.spare.extraAvgVms == null ? '—' : (r.spare.extraAvgVms <= 0 ? '0' : '~' + szNum(r.spare.extraAvgVms))}</td>
        ${local ? `<td>${storageCell}</td>` : ''}
        ${anyPrice ? `<td class="num">${r.budget == null ? '—' : szMoney(r.budget)}</td>` : ''}
        <td>${status}</td>
      </tr>`;
  }).join('');

  const absorption = sizingAbsorptionRows(d);
  let absorbHtml = '';
  if (absorption.length) {
    const canAbsorb = absorption.filter(a => a.cls === 'ok' && (!a.n1 || a.n1.ok));
    const absorbRows = absorption.map(a => `
      <tr>
        <td><b>${esc(a.name)}</b><div class="muted-sub">${a.hostCount} host · ${a.before.poweredOnCount} VM bật</div></td>
        <td class="num">${fmtPct(a.before.cpuPct)} → <b>${fmtPct(a.after.cpuPct)}</b></td>
        <td class="num">${fmtPct(a.before.ramPct)} → <b>${fmtPct(a.after.ramPct)}</b></td>
        <td class="num">${a.n1 ? `${fmtPct(a.n1.cpuPct)} / ${fmtPct(a.n1.ramPct)} ${a.n1.ok ? '<span class="pill ok">Đạt</span>' : '<span class="pill crit">Không</span>'}` : '—'}</td>
        <td><span class="pill ${a.cls}">${a.cls === 'ok' ? 'Hấp thụ được' : a.cls === 'warn' ? 'Sát ngưỡng' : 'Vượt ngưỡng'}</span></td>
      </tr>`).join('');
    absorbHtml = `
      <div class="sz-section-title">Hạ tầng hiện hữu có hấp thụ được không?</div>
      <p class="panel-sub">Giả định đưa toàn bộ nhu cầu (sau tăng trưởng) vào từng cluster đang có; % tính theo ngưỡng overcommit của chính sách hiện hành (${appState.policy.cpuOvercommitMax}x CPU · ${appState.policy.ramOvercommitMax}x RAM).</p>
      <div class="table-scroll"><table class="data-table sz-compare">
        <thead><tr><th>Cluster</th><th>CPU % ngưỡng (trước → sau)</th><th>RAM % ngưỡng (trước → sau)</th><th>Khi mất 1 host (CPU / RAM)</th><th>Kết luận</th></tr></thead>
        <tbody>${absorbRows}</tbody>
      </table></div>
      <div class="insight-box">${canAbsorb.length
        ? `<strong>Có thể chưa cần mua host mới:</strong> cluster ${canAbsorb.map(a => esc(a.name)).join(', ')} hấp thụ được toàn bộ nhu cầu mà vẫn dưới 80% ngưỡng và giữ N+1. Phương án mua host bên trên là để tách riêng dự án hoặc để dành dư địa.`
        : `<strong>Không cluster nào hấp thụ được toàn bộ nhu cầu</strong> mà vẫn an toàn — cần bổ sung host theo đề xuất bên trên (hoặc chia nhỏ dự án vào nhiều cluster).`}</div>`;
  }

  area.innerHTML = `
    <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Kết quả sizing${p.environment ? ` — ${esc(p.environment)}` : ''}</div><p class="panel-sub">Số host vật lý cần cho ${szNum(d.vmCount)} VM theo từng cấu hình ứng viên; phương án đề xuất: ít host nhất → không sát ngưỡng → chi phí thấp nhất → điểm tổng hợp cao nhất. Dòng in nghiêng là phương án tham khảo do hệ thống gợi ý.</p></div></div>
    ${best ? `<div class="insight-box sz-headline"><strong>${esc(result.recommendation.headline)}</strong><div class="sz-detail">${esc(result.recommendation.detail)}</div>${result.recommendation.why ? `<div class="sz-detail"><b>${esc(result.recommendation.why)}</b></div>` : ''}</div>` : ''}
    ${tiles}
    <div class="sz-section-title">So sánh cấu hình host</div>
    <div class="table-scroll"><table class="data-table sz-compare">
      <thead><tr>
        <th>Cấu hình</th><th title="Số host cần để đáp ứng vCPU ở overcommit mục tiêu và mức dùng tối đa">Host theo CPU</th><th title="Số host cần để đáp ứng RAM">Host theo RAM</th><th>Dự phòng</th><th>Tổng host</th><th>Yếu tố quyết định</th>
        <th>Tổng cores / RAM</th><th title="Số vCPU (sau tăng trưởng) trên mỗi core vật lý của cụm">vCPU : core</th><th title="% năng lực mục tiêu đang dùng khi mất đúng số host dự phòng">Khi mất host (CPU / RAM)</th><th title="Số VM cỡ trung bình của dự án còn chứa thêm được mà vẫn giữ dự phòng">Chứa thêm (VM)</th>
        ${local ? '<th>Đĩa cục bộ</th>' : ''}${anyPrice ? '<th>Ngân sách ước tính</th>' : ''}<th></th>
      </tr></thead>
      <tbody>${cmpRows}</tbody>
    </table></div>
    ${sizingAdvisoryHtml(result)}
    <div class="sz-assumptions"><b>Giả định:</b> ${result.assumptions.map(esc).join(' ')}</div>
    ${absorbHtml}
  `;
  area.classList.remove('hidden');
  renderSizingCharts(result);
  if (scroll) area.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ---------- Report customisation after the calculation ---------- */
function sizingDefaultReport() {
  return { pick: '', hosts: 0, selectionNote: '', sections: {}, headline: '', note: '', hiddenFindings: [], extraFindings: [] };
}

const SZ_QUICK = [
  ['cpu_ratio', 'Tỷ lệ chia sẻ CPU (vCPU / thread)', 1, 8, 0.5, v => `${v}`],
  ['ram_ratio', 'Tỷ lệ chia sẻ RAM', 1, 2, 0.1, v => `${v} : 1`],
  ['max_util_pct', 'Mức dùng tối đa mỗi host', 50, 95, 5, v => `${v}%`],
  ['growth_pct', 'Dự phòng tăng trưởng', 0, 100, 5, v => `+${v}%`],
  ['annual_growth_pct', 'Tăng trưởng hằng năm (dự báo)', 0, 50, 5, v => `${v}%/năm`],
];
const SZ_SECTIONS = [
  ['scope', 'Phạm vi & dữ liệu đầu vào'], ['demand', 'Nhu cầu tài nguyên'], ['options', 'So sánh phương án'],
  ['scores', 'Bảng điểm', 'options'], ['design', 'Cấu hình đề xuất'],
  ['bom', 'BOM sơ bộ', 'design'], ['arch', 'Kiến trúc logic', 'design'], ['alloc', 'Phân bổ năng lực', 'design'],
  ['tco', 'Chi phí & TCO'], ['scale', 'Khả năng mở rộng'], ['risks', 'Rủi ro & khuyến nghị'], ['plan', 'Kế hoạch triển khai'],
  ['method', 'Giả định & cách tính'], ['existing', 'Hạ tầng hiện hữu (chế độ tận dụng)'], ['vms', 'Phụ lục: Danh sách máy ảo'],
  ['glossary', 'Phụ lục: Thuật ngữ'],
];
let szRecalcTimer = null;
function szRecalcSoon() {
  clearTimeout(szRecalcTimer);
  szRecalcTimer = setTimeout(() => runSizing(null, false), 250);
}

function renderSizingAdjust(result) {
  const box = $('#sizingAdjust');
  if (!box) return;
  const st = sizingState();
  const rep = st.report || (st.report = sizingDefaultReport());
  const p = st.params;
  const sel = result.selection || {};
  const rows = result.results || [];
  const best = result.recommendedIndex != null ? rows[result.recommendedIndex] : null;
  const curKey = rep.pick || '';
  const hostsMin = best ? (best.hostsComputed || best.hostsTotal) : 1;
  const tag = r => (r.suggested ? ' · tham khảo' : '') + (r.tight ? ' · sát ngưỡng' : '') + (!r.failure.ok ? ' · chưa đạt N+1' : '');
  const opts = [`<option value="" ${!curKey ? 'selected' : ''}>Tự động — ${esc(sel.autoName || '—')} (${sel.autoHosts || '—'} host)</option>`]
    .concat(rows.filter(r => r.hostsTotal).map(r => `<option value="${esc(r.key)}" ${r.key === curKey ? 'selected' : ''}>${esc(r.profile.name.replace(' (gợi ý)', ''))} — ${r.hostsComputed || r.hostsTotal} host${tag(r)}</option>`)).join('');
  const quick = SZ_QUICK.map(([k, label, min, max, step, fmt]) => `
    <div class="sz-adj-row"><span class="sz-adj-label">${label}</span>
      <input type="range" data-quick="${k}" min="${min}" max="${max}" step="${step}" value="${esc(p[k])}"><output data-out="${k}">${esc(fmt(p[k]))}</output></div>`).join('')
    + `<div class="sz-adj-row"><span class="sz-adj-label">Dự phòng sự cố</span>
      <select data-quick="redundancy">${[0, 1, 2].map(x => `<option value="${x}" ${Number(p.redundancy) === x ? 'selected' : ''}>N+${x}</option>`).join('')}</select><span></span></div>`;
  const secs = rep.sections || {};
  const cb = (k, label, sub) => `<label class="${sub ? 'sub' : ''}"><input type="checkbox" data-sec="${k}" ${secs[k] === false ? '' : 'checked'}> ${label}</label>`;
  const checks = SZ_SECTIONS.filter(x => !x[2]).map(([k, label]) =>
    `<div class="sz-adj-group">${cb(k, label)}${SZ_SECTIONS.filter(x => x[2] === k).map(([ck, cl]) => cb(ck, cl, true)).join('')}</div>`).join('');
  const hidden = new Set(rep.hiddenFindings || []);
  const SEV = { critical: ['crit', 'Nghiêm trọng'], warning: ['warn', 'Cảnh báo'], info: ['na', 'Lưu ý'], ok: ['ok', 'Đạt'] };
  const fl = ((result.analysis || {}).findings || []).map(f => `<label class="${hidden.has(f.id) ? 'off' : ''}"><input type="checkbox" data-finding="${esc(f.id)}" ${hidden.has(f.id) ? '' : 'checked'}>
      <span class="pill ${SEV[f.severity][0]}">${SEV[f.severity][1]}</span><span class="t">${esc(f.title)}</span></label>`).join('');
  const extras = (rep.extraFindings || []).map((x, i) => `<label><span class="pill ${(SEV[x.severity] || SEV.info)[0]}">${(SEV[x.severity] || SEV.info)[1]}</span>
      <span class="t">${esc(x.title)}</span><button class="btn btn-ghost btn-sm" type="button" data-del-extra="${i}" title="Xoá">✕</button></label>`).join('');
  const changed = rep.pick || rep.hosts || rep.headline || rep.note || (rep.hiddenFindings || []).length || (rep.extraFindings || []).length
    || Object.values(rep.sections || {}).some(v => v === false);
  box.innerHTML = `
    <div class="sz-adj-head"><div><div class="panel-title panel-title-sm">Tuỳ chỉnh báo cáo</div>
      <p class="panel-sub">Điều chỉnh sau khi tính: giả định và phương án áp dụng cho kết quả trên màn hình, PDF và Excel; mục in và nội dung áp dụng cho PDF báo cáo đầy đủ.</p></div>
      <button class="btn btn-secondary btn-sm" type="button" id="btnSzAdjReset" ${changed ? '' : 'disabled'}>Khôi phục mặc định</button></div>
    <div class="sz-adj-grid">
      <details class="sz-adj-box" open><summary>Chỉnh nhanh giả định</summary>${quick}
        <p class="logo-upload-hint">Thả tay để tính lại; giá trị đồng bộ với cột Giả định bên trái.</p></details>
      <details class="sz-adj-box" open><summary>Phương án & số host trong báo cáo</summary>
        <div class="field"><label>Phương án đề xuất</label><select id="szAdjPick">${opts}</select></div>
        <div class="field"><label>Số host đề xuất (tối thiểu ${hostsMin} theo tính toán)</label>
          <input id="szAdjHosts" type="number" min="0" step="1" value="${rep.hosts || ''}" placeholder="${hostsMin} (theo tính toán)"></div>
        <div class="field"><label>Lý do điều chỉnh (ghi vào báo cáo)</label><input id="szAdjSelNote" value="${esc(rep.selectionNote || '')}" placeholder="VD: theo hợp đồng khung phần cứng hiện có"></div>
        ${sel.manual ? `<p class="logo-upload-hint"><b>Đang dùng lựa chọn của người lập</b> — tự động là ${esc(sel.autoName || '')} × ${sel.autoHosts} host.</p>` : ''}</details>
      <details class="sz-adj-box" open><summary>Mục in trong báo cáo</summary><div class="sz-adj-checks">${checks}</div>
        <p class="logo-upload-hint">Mục Tóm tắt luôn được in; số mục, mục lục và chữ cái phụ lục tự đánh lại.</p></details>
      <details class="sz-adj-box" open><summary>Nội dung tư vấn</summary>
        <div class="field"><label>Câu kết luận (để trống = tự động)</label><textarea id="szAdjHeadline" placeholder="${esc((result.recommendation || {}).headline || '')}">${esc(rep.headline || '')}</textarea></div>
        <div class="field"><label>Ghi chú của người lập (in ở mục Tóm tắt)</label><textarea id="szAdjNote" placeholder="VD: điều kiện, cam kết, lưu ý riêng cho khách hàng">${esc(rep.note || '')}</textarea></div>
        <div class="sz-adj-label" style="margin-bottom:4px;">Khuyến nghị in trong báo cáo (bỏ chọn để ẩn)</div>
        <div class="sz-adj-findings">${fl}${extras}</div>
        <details><summary class="muted-sub">+ Thêm khuyến nghị riêng</summary>
          <div class="sz-adj-extra" style="margin-top:6px;">
            <select id="szExSev"><option value="warning">Cảnh báo</option><option value="info" selected>Lưu ý</option><option value="critical">Nghiêm trọng</option><option value="ok">Đạt</option></select>
            <input id="szExArea" placeholder="Lĩnh vực (VD: Mạng)">
            <input id="szExTitle" class="full" placeholder="Nhận định (bắt buộc)">
            <input id="szExDetail" class="full" placeholder="Chi tiết">
            <input id="szExAction" class="full" placeholder="Khuyến nghị">
            <button class="btn btn-secondary btn-sm" type="button" id="btnSzExAdd">Thêm</button></div></details></details>
    </div>`;
  box.classList.remove('hidden');
}

function bindSizingAdjust() {
  const box = $('#sizingAdjust');
  if (!box || box.dataset.bound) return;
  box.dataset.bound = '1';
  const st = () => sizingState();
  const rep = () => (st().report || (st().report = sizingDefaultReport()));
  box.addEventListener('input', e => {
    const k = e.target.dataset.quick;
    if (k && e.target.type === 'range') {
      const def = SZ_QUICK.find(q => q[0] === k);
      const out = box.querySelector(`[data-out="${k}"]`);
      if (out && def) out.textContent = def[5](e.target.value);
    }
  });
  box.addEventListener('change', e => {
    const t = e.target;
    if (t.dataset.quick) {
      const k = t.dataset.quick;
      const v = k === 'redundancy' ? Math.round(cleanNum(t.value)) : cleanNum(t.value);
      st().params[k] = v;
      const el = document.getElementById(SIZING_PARAM_FIELDS[k]);
      if (el) el.value = v;
      persistSizing(); szRecalcSoon(); return;
    }
    if (t.id === 'szAdjPick') { rep().pick = t.value; rep().hosts = 0; persistSizing(); szRecalcSoon(); return; }
    if (t.id === 'szAdjHosts') { rep().hosts = Math.max(0, Math.round(cleanNum(t.value)) || 0); persistSizing(); szRecalcSoon(); return; }
    if (t.id === 'szAdjSelNote') { rep().selectionNote = t.value.trim(); persistSizing(); return; }
    if (t.id === 'szAdjHeadline') { rep().headline = t.value.trim(); persistSizing(); return; }
    if (t.id === 'szAdjNote') { rep().note = t.value.trim(); persistSizing(); return; }
    if (t.dataset.sec) { rep().sections = { ...(rep().sections || {}), [t.dataset.sec]: t.checked }; persistSizing(); return; }
    if (t.dataset.finding) {
      const set = new Set(rep().hiddenFindings || []);
      if (t.checked) set.delete(t.dataset.finding); else set.add(t.dataset.finding);
      rep().hiddenFindings = [...set];
      t.closest('label').classList.toggle('off', !t.checked);
      persistSizing();
    }
  });
  box.addEventListener('click', e => {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.id === 'btnSzExAdd') {
      const title = $('#szExTitle').value.trim();
      if (!title) { $('#szExTitle').focus(); return; }
      rep().extraFindings = (rep().extraFindings || []).concat([{ severity: $('#szExSev').value, area: $('#szExArea').value.trim() || 'Khác', title,
        detail: $('#szExDetail').value.trim(), action: $('#szExAction').value.trim() }]);
      persistSizing(); renderSizingAdjust(st().result); return;
    }
    if (t.dataset.delExtra != null) {
      rep().extraFindings = (rep().extraFindings || []).filter((_, i) => i !== Number(t.dataset.delExtra));
      persistSizing(); renderSizingAdjust(st().result); return;
    }
    if (t.id === 'btnSzAdjReset') {
      const hadSelection = rep().pick || rep().hosts;
      st().report = sizingDefaultReport();
      persistSizing();
      if (hadSelection) szRecalcSoon(); else renderSizingAdjust(st().result);
    }
  });
}

/* ---------- Advisory: findings, charts, roadmap ---------- */
const SZ_SEV = { critical: ['crit', 'Nghiêm trọng'], warning: ['warn', 'Cảnh báo'], info: ['na', 'Lưu ý'], ok: ['ok', 'Đạt'] };
const SZ_READY = { ok: ['ok', 'Sẵn sàng triển khai'], warn: ['warn', 'Triển khai được · cần lưu ý'], crit: ['crit', 'Cần xử lý rủi ro trước'] };
const szMoneyShort = (v) => {
  if (v === null || v === undefined || !isFinite(v)) return '—';
  if (Math.abs(v) >= 1e9) return `${(v / 1e9).toLocaleString('vi-VN', { maximumFractionDigits: 2 })} tỷ`;
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toLocaleString('vi-VN', { maximumFractionDigits: 0 })} tr`;
  return Number(v).toLocaleString('vi-VN');
};

function sizingCapacitySegments(best, d, p) {
  const n = best.hostsTotal, red = best.redundancy, nFail = best.failure.hostsLeft;
  const util = Math.max(0.05, Math.min(1, p.max_util_pct / 100));
  const ph = best.perHost, prof = best.profile;
  const mk = (total, usableHost, req, planned) => [
    ['Yêu cầu dự án', req], ['Dự phòng tăng trưởng', planned - req], ['Còn trống', Math.max(0, usableHost * nFail * util - planned)],
    [`Vùng an toàn ${100 - p.max_util_pct}%`, usableHost * nFail * (1 - util)], [`Dự phòng N+${red}`, usableHost * red], ['Hypervisor', Math.max(0, total - usableHost * n)],
  ];
  return {
    cpu: mk(prof.cores * Math.max(1, p.ht_factor) * p.cpu_ratio * n, ph.logicalCpu * p.cpu_ratio, d.vcpu, d.vcpuPlanned),
    ram: mk(prof.ramGB * p.ram_ratio * n, ph.usableRamGB * p.ram_ratio, d.ramGB, d.ramPlannedGB),
  };
}

function sizingAdvisoryHtml(result) {
  const a = result.analysis;
  if (!a) return '';
  const best = result.recommendedIndex != null ? result.results[result.recommendedIndex] : null;
  const wl = a.workload;
  const anyCost = result.results.some(r => r.advisory && r.advisory.costs && r.advisory.costs.tco);
  const [rcls, rtxt] = SZ_READY[a.readiness] || SZ_READY.ok;
  const fc = a.findingCounts || {};
  const costTiles = best && best.advisory.costs.tco ? `
      <article class="kpi-card"><div class="label">TCO ${best.advisory.costs.years} năm (đề xuất)</div><div class="value">${szMoneyShort(best.advisory.costs.tco)}</div><div class="sub">phần cứng ${szMoneyShort(best.advisory.costs.hardware)} · bảo hành ${szMoneyShort(best.advisory.costs.warranty)} · license ${szMoneyShort(best.advisory.costs.license)}</div></article>
      <article class="kpi-card"><div class="label">Chi phí / VM / tháng</div><div class="value">${szMoneyShort(best.advisory.costs.perVmMonth)}</div><div class="sub">/ VM: ${szMoneyShort(best.advisory.costs.perVm)} · / vCPU: ${szMoneyShort(best.advisory.costs.perVcpu)}</div></article>` : '';
  const insightTiles = best ? `
    <div class="kpi-grid sz-kpis">
      <article class="kpi-card"><div class="label">Đặc tính workload</div><div class="value">${szNum(wl.ramPerVcpu, 1)} GB/vCPU</div><div class="sub">${esc(wl.characterLabel)}</div></article>
      <article class="kpi-card"><div class="label">Dư địa tăng trưởng thêm</div><div class="value">${best.advisory.runway.extraGrowthPct == null ? '—' : '+' + szNum(Math.max(0, best.advisory.runway.extraGrowthPct)) + '%'}</div><div class="sub">ngoài +${szNum(result.params.growth_pct)}% đã dự phòng, vẫn giữ N+${best.redundancy}</div></article>
      <article class="kpi-card"><div class="label">License theo core</div><div class="value">${szNum(best.advisory.license.coresTotal)} core</div><div class="sub">${szNum(best.advisory.license.coresPerHost)}/host${best.advisory.license.paddedCores ? ` · ${szNum(best.advisory.license.paddedCores)} core trả thừa` : ''}</div></article>
      <article class="kpi-card"><div class="label">Cần mở rộng</div><div class="value">${a.projection && a.projection.expandYear != null ? 'Năm ' + a.projection.expandYear : 'Không'}</div><div class="sub">trong ${a.projection ? a.projection.years : '—'} năm · tăng ${szNum(result.params.annual_growth_pct)}%/năm</div></article>
      ${costTiles}
    </div>` : '';

  const findings = (a.findings || []).map(f => {
    const [cls, lbl] = SZ_SEV[f.severity] || SZ_SEV.info;
    return `<div class="sz-finding ${f.severity}"><div><span class="pill ${cls}">${lbl}</span><div class="sz-f-area">${esc(f.area)}</div></div>
      <div><div class="sz-f-title">${esc(f.title)}</div><div class="sz-f-detail">${esc(f.detail)}</div></div>
      <div><b>Khuyến nghị:</b> ${esc(f.action)}</div></div>`;
  }).join('');
  const steps = (a.nextSteps || []).map(st => `<div class="sz-step"><span class="muted-sub">${esc(st.when)}</span><b>${esc(st.phase)}</b>${esc(st.what)}</div>`).join('');
  const sensOpts = [['growth', 'Dự phòng tăng trưởng'], ['cpuRatio', 'Overcommit CPU mục tiêu'], ['maxUtil', 'Mức dùng tối đa / host']]
    .map(([k, l]) => `<option value="${k}" ${sizingState().sensKey === k ? 'selected' : ''}>${l}</option>`).join('');

  return `
    <div class="sz-section-title">Phân tích tư vấn</div>
    <div class="sz-readiness"><span class="pill ${rcls}">${rtxt}</span><span class="muted-sub">${fc.critical || 0} nghiêm trọng · ${fc.warning || 0} cảnh báo · ${fc.info || 0} lưu ý · ${fc.ok || 0} đạt</span></div>
    ${insightTiles}
    <div class="sz-chart-grid three">
      <div class="sz-chart"><div class="sz-chart-title">Phân bố kích thước VM</div><div class="sz-chart-sub">Số VM theo nhóm T-shirt size.</div><div class="chart-wrap"><canvas id="szChartSizes"></canvas></div></div>
      <div class="sz-chart"><div class="sz-chart-title">Nhóm VM chiếm tài nguyên lớn nhất</div><div class="sz-chart-sub">% tổng vCPU / RAM của dự án; ${wl.paretoLines} nhóm chiếm 80% RAM.</div><div class="chart-wrap"><canvas id="szChartTop"></canvas></div></div>
      <div class="sz-chart"><div class="sz-chart-title">Từ yêu cầu đến năng lực lắp đặt</div><div class="sz-chart-sub">RAM (GB): yêu cầu → tăng trưởng → headroom → lắp đặt.</div><div class="chart-wrap"><canvas id="szChartWaterfall"></canvas></div></div>
    </div>
    <div class="sz-chart-grid">
      <div class="sz-chart"><div class="sz-chart-title">Số host theo từng ràng buộc</div><div class="sz-chart-sub">Host cần theo CPU, theo RAM và tổng gồm N+x cho mỗi cấu hình.</div><div class="chart-wrap"><canvas id="szChartHosts"></canvas></div></div>
      <div class="sz-chart"><div class="sz-chart-title">Bảng điểm đa tiêu chí</div><div class="sz-chart-sub">0–100, cao = tốt: số host, chi phí, hiệu suất, cân bằng, chịu lỗi, mở rộng, NUMA.</div><div class="chart-wrap tall"><canvas id="szChartRadar"></canvas></div></div>
    </div>
    ${best ? `<div class="sz-chart-grid">
      <div class="sz-chart"><div class="sz-chart-title">Phân bổ năng lực cluster đề xuất</div><div class="sz-chart-sub">${esc(best.profile.name)} × ${best.hostsTotal}: năng lực ở overcommit mục tiêu được dùng vào đâu.</div><div class="chart-wrap"><canvas id="szChartAlloc"></canvas></div></div>
      <div class="sz-chart"><div class="sz-chart-title">Dự báo nhu cầu host theo năm</div><div class="sz-chart-sub">Tăng trưởng ${szNum(result.params.annual_growth_pct)}%/năm; đường xanh = số host đề xuất lắp đặt.</div><div class="chart-wrap"><canvas id="szChartProjection"></canvas></div></div>
    </div>
    <div class="sz-chart-grid">
      <div class="sz-chart"><div class="panel-head panel-head-tight"><div><div class="sz-chart-title">Phân tích độ nhạy</div><div class="sz-chart-sub">Số host của từng cấu hình khi một giả định thay đổi.</div></div>
        <select id="szSensKey" class="btn-sm">${sensOpts}</select></div><div class="chart-wrap"><canvas id="szChartSens"></canvas></div></div>
      ${anyCost ? `<div class="sz-chart"><div class="sz-chart-title">TCO ${best.advisory.costs.years} năm theo phương án</div><div class="sz-chart-sub">Phần cứng + gia hạn bảo hành + license.</div><div class="chart-wrap"><canvas id="szChartCost"></canvas></div></div>`
        : `<div class="sz-chart"><div class="sz-chart-title">Chi phí</div><div class="sz-chart-sub">Nhập đơn giá host và/hoặc đơn giá license / core để xem TCO, chi phí / VM / tháng và so sánh chi phí giữa các phương án.</div></div>`}
    </div>` : ''}
    ${sizingTcoHtml(best)}
    <div class="sz-section-title">Rủi ro &amp; khuyến nghị</div>
    <div class="sz-findings">${findings || uiNote('Không có nhận định.')}</div>
    ${steps ? `<div class="sz-section-title">Lộ trình triển khai đề xuất</div><div class="sz-steps">${steps}</div>` : ''}`;
}

function sizingTcoHtml(best) {
  const c = best && best.advisory && best.advisory.costs;
  if (!c || !c.tco || !c.yearly || !c.yearly.length) return '';
  const m = v => (v == null ? '—' : szMoneyShort(v));
  const z = v => (v == null ? '<span class="pill warn">chưa nhập</span>' : (v ? szMoneyShort(v) : '<span class="sz-muted">–</span>'));
  const ys = c.yearly;
  const head = `<tr><th>Thành phần</th>${ys.map(y => `<th>Năm ${y.year}</th>`).join('')}<th class="sum">Tổng ${c.years} năm</th></tr>`;
  const row = (label, cells, total, cls = '') => `<tr class="${cls}"><td>${label}</td>${cells.join('')}<td class="sum">${total}</td></tr>`;
  const rows = [
    row('Phần cứng', ys.map(y => `<td>${y.hardware == null ? '—' : z(y.hardware)}</td>`), m(c.hardware)),
    row('License', ys.map(y => `<td>${y.license == null ? '—' : z(y.license)}</td>`), m(c.license)),
    row(`Dịch vụ bảo hành HW<div class="muted-sub">${c.warrantyIncludedYears} năm đầu đã gồm trong giá HW</div>`,
      ys.map(y => `<td>${y.inWarranty ? '<span class="sz-muted">Đã gồm</span>' : z(y.warranty)}</td>`), m(c.warranty || 0)),
    row('Tổng theo năm', ys.map(y => `<td>${m(y.total)}</td>`), m(c.tco), 'total'),
    row('<span class="muted-sub">Lũy kế</span>', ys.map(y => `<td><span class="sz-muted">${m(y.cumulative)}</span></td>`), '', 'cum'),
  ].join('');
  const miss = (c.warrantyMissingYears || []).length
    ? `<p class="logo-upload-hint">Chưa nhập chi phí gia hạn bảo hành năm ${c.warrantyMissingYears.join(', ')} — nhập ở nhóm “Tư vấn &amp; chi phí” để TCO đầy đủ.</p>` : '';
  return `<div class="sz-section-title">Chi phí &amp; TCO ${c.years} năm — phương án đề xuất</div>
    <div class="sz-chart-grid sz-tco-grid">
      <div class="sz-chart"><div class="sz-chart-title">Chi phí theo năm và lũy kế</div>
        <div class="sz-chart-sub">Năm 1 mua phần cứng (gồm bảo hành ${c.warrantyIncludedYears} năm); các năm sau: gia hạn bảo hành + license.</div>
        <div class="chart-wrap"><canvas id="szChartTcoYear"></canvas></div></div>
      <div class="sz-chart"><div class="sz-chart-title">Bảng TCO theo năm</div>
        <div class="table-scroll"><table class="sz-tco-table"><thead>${head}</thead><tbody>${rows}</tbody></table></div>${miss}</div>
    </div>`;
}

function renderSizingCharts(result) {
  ['szSizes', 'szTop', 'szWaterfall', 'szHosts', 'szRadar', 'szAlloc', 'szProjection', 'szSens', 'szCost', 'szTcoYear'].forEach(destroyChart);
  if (typeof Chart === 'undefined' || !result.analysis) return;
  const c = chartPalette();
  const a = result.analysis;
  const d = result.demand;
  const p = result.params;
  const best = result.recommendedIndex != null ? result.results[result.recommendedIndex] : null;
  const rows = result.results.slice().sort((x, y) => (x.rank || 99) - (y.rank || 99));
  const scales = (extra = {}) => ({ x: { ticks: { color: c.muted }, grid: { color: c.divider }, ...(extra.x || {}) }, y: { beginAtZero: true, ticks: { color: c.muted }, grid: { color: c.divider }, ...(extra.y || {}) } });
  const legend = { labels: { color: c.text, boxWidth: 12 } };
  const mk = (key, id, cfg) => { const el = document.getElementById(id); if (el) appState.charts[key] = new Chart(el, cfg); };

  const buckets = a.workload.sizeBuckets.filter(b => b.count);
  mk('szSizes', 'szChartSizes', { type: 'doughnut',
    data: { labels: buckets.map(b => b.label), datasets: [{ data: buckets.map(b => b.count), backgroundColor: c.seq.slice(0, 5).filter((_, i) => a.workload.sizeBuckets[i].count), borderWidth: 0 }] },
    options: { maintainAspectRatio: false, cutout: '62%', plugins: { legend: { ...legend, position: 'bottom' } } } });

  const top = a.workload.topGroups.slice(0, 8);
  mk('szTop', 'szChartTop', { type: 'bar',
    data: { labels: top.map(g => g.name), datasets: [
      { label: '% vCPU', data: top.map(g => +g.vcpuShare.toFixed(1)), backgroundColor: c.primary, borderRadius: 3 },
      { label: '% RAM', data: top.map(g => +g.ramShare.toFixed(1)), backgroundColor: c.accent, borderRadius: 3 }] },
    options: { indexAxis: 'y', maintainAspectRatio: false, plugins: { legend }, scales: scales({ x: { ticks: { color: c.muted, callback: v => v + '%' } } }) } });

  // Floating-bar waterfall (RAM)
  const steps = a.waterfall.ram;
  let prev = 0;
  const wfData = steps.map((st, i) => { const last = i === steps.length - 1; const v = (i === 0 || last) ? [0, st.value] : [Math.min(prev, st.value), Math.max(prev, st.value)]; prev = st.value; return v; });
  mk('szWaterfall', 'szChartWaterfall', { type: 'bar',
    data: { labels: steps.map(st => st.label), datasets: [{ label: 'GB RAM', data: wfData, borderRadius: 3,
      backgroundColor: steps.map((_, i) => i === 0 ? c.primary : i === steps.length - 1 ? c.deep : c.warn) }] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => { const [lo, hi] = ctx.raw; const i = ctx.dataIndex; return (i === 0 || i === steps.length - 1) ? `${szGB(hi)}` : `+${szGB(hi - lo)} → ${szGB(steps[i].value)}`; } } } }, scales: scales() } });

  mk('szHosts', 'szChartHosts', { type: 'bar',
    data: { labels: rows.map(r => r.profile.name), datasets: [
      { label: 'Theo CPU', data: rows.map(r => r.hostsForCpu), backgroundColor: c.primary, borderRadius: 4 },
      { label: 'Theo RAM', data: rows.map(r => r.hostsForRam), backgroundColor: c.accent, borderRadius: 4 },
      { label: 'Tổng (gồm N+x)', data: rows.map(r => r.hostsTotal), backgroundColor: c.deep, borderRadius: 4 }] },
    options: { maintainAspectRatio: false, plugins: { legend }, scales: scales({ y: { ticks: { color: c.muted, precision: 0 } } }) } });

  const axes = a.scoreAxes || [];
  mk('szRadar', 'szChartRadar', { type: 'radar',
    data: { labels: axes.map(x => x.label), datasets: rows.filter(r => r.advisory && r.advisory.scores).slice(0, 5).map((r, i) => ({
      label: `${r.profile.name}${r.recommended ? ' ★' : ''} (${szNum(r.advisory.scoreTotal)})`, data: axes.map(x => +r.advisory.scores[x.key].toFixed(0)),
      borderColor: c.series[i % c.series.length], backgroundColor: c.series[i % c.series.length] + '22', borderWidth: r.recommended ? 2.5 : 1.5, pointRadius: 2 })) },
    options: { maintainAspectRatio: false, plugins: { legend: { ...legend, position: 'bottom' } },
      scales: { r: { min: 0, max: 100, ticks: { display: false, stepSize: 25 }, grid: { color: c.divider }, angleLines: { color: c.divider }, pointLabels: { color: c.text, font: { size: 11 } } } } } });

  if (best) {
    const seg = sizingCapacitySegments(best, d, p);
    const segColors = [c.series[0], c.seq[1], c.series[5], c.series[3], c.series[4], c.series[7]];
    const pct = (arr) => { const t = arr.reduce((s, x) => s + Math.max(0, x[1]), 0) || 1; return arr.map(x => +(Math.max(0, x[1]) / t * 100).toFixed(1)); };
    const cpuPct = pct(seg.cpu), ramPct = pct(seg.ram);
    mk('szAlloc', 'szChartAlloc', { type: 'bar',
      data: { labels: ['CPU', 'RAM'], datasets: seg.cpu.map((s, i) => ({ label: s[0], data: [cpuPct[i], ramPct[i]], backgroundColor: segColors[i] })) },
      options: { indexAxis: 'y', maintainAspectRatio: false, plugins: { legend: { ...legend, position: 'bottom' }, tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${ctx.raw}%` } } },
        scales: { x: { stacked: true, max: 100, ticks: { color: c.muted, callback: v => v + '%' }, grid: { color: c.divider } }, y: { stacked: true, ticks: { color: c.text }, grid: { display: false } } } } });

    const pr = a.projection;
    if (pr) {
      mk('szProjection', 'szChartProjection', { type: 'bar',
        data: { labels: pr.points.map(pt => pt.year === 0 ? 'Hiện tại' : `Năm ${pt.year}`), datasets: [
          { type: 'line', label: `Đã lắp (${pr.installed})`, data: pr.points.map(() => pr.installed), borderColor: c.ok, borderDash: [6, 4], pointRadius: 0, borderWidth: 2 },
          { label: 'Host cần', data: pr.points.map(pt => pt.hosts), backgroundColor: pr.points.map(pt => pt.hosts > pr.installed ? c.crit : c.primary), borderRadius: 4 }] },
        options: { maintainAspectRatio: false, plugins: { legend, tooltip: { callbacks: { afterLabel: ctx => ctx.datasetIndex === 1 ? `Tải +${szNum(pr.points[ctx.dataIndex].growthPct)}% so với hiện tại` : '' } } },
          scales: scales({ y: { ticks: { color: c.muted, precision: 0 } } }) } });
    }

    const drawSens = () => {
      destroyChart('szSens');
      const key = sizingState().sensKey || 'growth';
      const sw = a.sensitivity && a.sensitivity[key];
      if (!sw) return;
      const fmt = key === 'cpuRatio' ? (x => `${x}x`) : (x => `${x}%`);
      mk('szSens', 'szChartSens', { type: 'line',
        data: { labels: sw.x.map(fmt), datasets: sw.series.map((s, i) => ({ label: s.name, data: s.hosts, borderColor: c.series[i % c.series.length], backgroundColor: c.series[i % c.series.length], stepped: false, tension: 0, pointRadius: sw.x.map(x => x === sw.current ? 5 : 2.5) })) },
        options: { maintainAspectRatio: false, plugins: { legend, tooltip: { callbacks: { title: items => `${items[0].label}${sw.x[items[0].dataIndex] === sw.current ? ' (hiện tại)' : ''}` } } },
          scales: scales({ y: { ticks: { color: c.muted, precision: 0 }, title: { display: true, text: 'host', color: c.muted } } }) } });
    };
    drawSens();
    const sel = $('#szSensKey');
    if (sel) sel.addEventListener('change', () => { sizingState().sensKey = sel.value; drawSens(); });

    const anyHw = rows.some(r => !r.suggested && r.advisory.costs.hardware != null);
    const costRows = rows.filter(r => r.advisory.costs.tco && !(anyHw && r.advisory.costs.hardware == null));
    if (costRows.length) {
      mk('szCost', 'szChartCost', { type: 'bar',
        data: { labels: costRows.map(r => r.profile.name + (r.recommended ? ' ★' : '')), datasets: [
          { label: 'Phần cứng', data: costRows.map(r => r.advisory.costs.hardware || 0), backgroundColor: c.primary, borderRadius: 3 },
          { label: 'Gia hạn bảo hành', data: costRows.map(r => r.advisory.costs.warranty || 0), backgroundColor: c.amber, borderRadius: 3 },
          { label: `License ${costRows[0].advisory.costs.years} năm`, data: costRows.map(r => r.advisory.costs.license || 0), backgroundColor: c.accent, borderRadius: 3 }] },
        options: { indexAxis: 'y', maintainAspectRatio: false, plugins: { legend, tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${szMoney(ctx.raw)}`, footer: items => `TCO: ${szMoney(costRows[items[0].dataIndex].advisory.costs.tco)}` } } },
          scales: { x: { stacked: true, ticks: { color: c.muted, callback: v => szMoneyShort(v) }, grid: { color: c.divider } }, y: { stacked: true, ticks: { color: c.text }, grid: { display: false } } } } });
    }
    const bc = best.advisory.costs;
    if (bc && bc.tco && bc.yearly && bc.yearly.length) {
      const ys = bc.yearly;
      mk('szTcoYear', 'szChartTcoYear', { type: 'bar',
        data: { labels: ys.map(y => `Năm ${y.year}`), datasets: [
          { type: 'line', label: 'Lũy kế', data: ys.map(y => y.cumulative), borderColor: c.deep, borderDash: [5, 3], pointRadius: 3, yAxisID: 'y2', order: 0 },
          { label: 'Phần cứng', data: ys.map(y => y.hardware || 0), backgroundColor: c.primary, stack: 's', order: 1 },
          { label: 'License', data: ys.map(y => y.license || 0), backgroundColor: c.accent, stack: 's', order: 1 },
          { label: 'Dịch vụ bảo hành HW', data: ys.map(y => y.warranty || 0), backgroundColor: c.amber, stack: 's', order: 1 }] },
        options: { maintainAspectRatio: false, plugins: { legend, tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${szMoney(ctx.raw)}` } } },
          scales: { x: { stacked: true, ticks: { color: c.muted }, grid: { display: false } },
            y: { stacked: true, beginAtZero: true, ticks: { color: c.muted, callback: v => szMoneyShort(v) }, grid: { color: c.divider } },
            y2: { position: 'right', beginAtZero: true, ticks: { color: c.deep, callback: v => szMoneyShort(v) }, grid: { display: false } } } } });
    }
  }
}

/* ---------- Import / template / sample / export ---------- */
async function importSizingFile(file) {
  if (!file) return;
  try {
    const parsed = await api.importSizingFile(file);
    const st = sizingState();
    const rows = (parsed.rows || []).map(r => ({ id: nextSizingId(), name: r.name, qty: r.qty, vcpu: r.vcpu, ramGB: r.ramGB, diskGB: r.diskGB, note: r.note || '' }));
    if (!rows.length) {
      showValidationBanner([`Không đọc được VM nào từ "${file.name}". ${(parsed.warnings || []).join(' ')}`], []);
      return;
    }
    st.vms = rows;
    persistSizing();
    renderSizingVmTable();
    const t = sizingTotals(rows);
    showValidationBanner(parsed.warnings || [], [`Đã import ${rows.length} dòng (${szNum(t.count)} VM · ${szNum(t.vcpu)} vCPU · ${szGB(t.ram)} RAM · ${szGB(t.disk)} disk) từ "${file.name}"${parsed.sheet ? `, sheet "${parsed.sheet}"` : ''}.`]);
    $('#sizingResult').classList.add('hidden'); $('#sizingAdjust').classList.add('hidden');
  } catch (err) {
    showValidationBanner([`Import thất bại: ${err.message}`], []);
  }
}

async function loadSizingSample() {
  try {
    const defaults = await api.getSizingDefaults();
    const st = sizingState();
    st.vms = (defaults.sampleVms || []).map(r => ({ id: nextSizingId(), name: r.name, qty: r.qty, vcpu: r.vcpu, ramGB: r.ramGB, diskGB: r.diskGB, note: r.note || '' }));
    // Sample data never names the project — the user types the real project/system name so titles stay clean.
    persistSizing();
    renderSizingVmTable();
    $('#sizingResult').classList.add('hidden'); $('#sizingAdjust').classList.add('hidden');
  } catch (err) {
    showValidationBanner([`Không nạp được ví dụ: ${err.message}`], []);
  }
}

function sizingExistingPayload() {
  // Absorption check for the PDF — needs the planned demand, so compute it locally with the same growth factor.
  const payload = sizingPayload();
  const t = sizingTotals(sizingState().vms);
  const g = 1 + Math.max(0, cleanNum(payload.params.growth_pct)) / 100;
  const demand = { vcpuPlanned: t.vcpu * g, ramPlannedGB: t.ram * g, avgVcpu: t.count ? t.vcpu / t.count : 0, avgRamGB: t.count ? t.ram / t.count : 0 };
  const rows = sizingAbsorptionRows(demand);
  const fin = v => (v === null || v === undefined || !isFinite(v)) ? null : v;
  const existing = rows.map(a => ({
    name: a.name, hostCount: a.hostCount,
    cpuBefore: fin(a.before.cpuPct), cpuAfter: fin(a.after.cpuPct), ramBefore: fin(a.before.ramPct), ramAfter: fin(a.after.ramPct),
    n1CpuPct: a.n1 ? fin(a.n1.cpuPct) : null, n1RamPct: a.n1 ? fin(a.n1.ramPct) : null, n1Ok: a.n1 ? a.n1.ok : null, cls: a.cls,
    totalCores: a.after.totalCores, totalRamGB: a.after.totalRamGB, usedVcpu: a.before.usedVcpu, usedRamGB: a.before.usedRamGB,
    poweredOnCount: a.before.poweredOnCount, cpuOcAfter: fin(a.after.cpuOvercommit), ramOcAfter: fin(a.after.ramOvercommit),
    extraAvgVms: a.extraAvgVms,
  }));
  const policyText = appState.policy ? `${appState.policy.cpuOvercommitMax}x CPU · ${appState.policy.ramOvercommitMax}x RAM` : '';
  // PDF content options from the dropdown (default: buy new servers).
  const mode = (document.querySelector('input[name="szPdfMode"]:checked') || {}).value === 'reuse' ? 'reuse' : 'new';
  const clusterSel = $('#szPdfCluster');
  const targetCluster = clusterSel && clusterSel.value ? clusterSel.value : null;
  const includeNewOption = $('#szPdfIncludeNew') ? $('#szPdfIncludeNew').checked : true;
  const includeExisting = $('#szPdfIncludeExisting') ? $('#szPdfIncludeExisting').checked : true;
  const detail = (document.querySelector('input[name="szPdfDetail"]:checked') || {}).value === 'brief' ? 'brief' : 'full';
  const rep = sizingState().report || sizingDefaultReport();
  const reportOptions = { sections: rep.sections || {}, headline: rep.headline || '', note: rep.note || '', selectionNote: rep.selectionNote || '',
    hiddenFindings: rep.hiddenFindings || [], extraFindings: (rep.extraFindings || []).filter(x => x.title && x.title.trim()) };
  return { ...payload, existing, policyText, mode, targetCluster, includeNewOption, includeExisting, detail, ...sizingDocPayload(), reportOptions };
}

/* Document control / scope fields shared by the PDF and Excel exports. */
function sizingDocPayload() {
  const doc = sizingState().doc;
  const logo = sizingLogoOverride();
  const lines = String(doc.outOfScope || '').split(/\r?\n/).map(x => x.trim()).filter(Boolean).slice(0, 20);
  return {
    customerName: doc.customerName || '', customerContact: doc.customerContact || '', preparedOrg: doc.preparedOrg || '',
    preparedBy: doc.preparedBy || '', approvedBy: doc.approvedBy || '', documentCode: doc.documentCode || '',
    version: doc.version || '1.0', versionNote: doc.versionNote || '', inputSource: doc.inputSource || '', inputDate: doc.inputDate || '',
    outOfScope: lines.length ? lines : null, logoDataUrl: logo ? logo.dataUrl : null,
  };
}

/* Fill the "cluster tiếp nhận" select and toggle option rows for the chosen PDF content. */
function refreshSizingPdfOptions() {
  const sel = $('#szPdfCluster');
  if (!sel) return;
  const mode = (document.querySelector('input[name="szPdfMode"]:checked') || {}).value === 'reuse' ? 'reuse' : 'new';
  $('#szPdfReuseOpts').classList.toggle('hidden', mode !== 'reuse');
  $('#szPdfNewOpts').classList.toggle('hidden', mode !== 'new');
  const prev = sel.value;
  let rows = [];
  try {
    const t = sizingTotals(sizingState().vms);
    const g = 1 + Math.max(0, cleanNum(sizingState().params.growth_pct)) / 100;
    rows = sizingAbsorptionRows({ vcpuPlanned: t.vcpu * g, ramPlannedGB: t.ram * g, avgVcpu: t.count ? t.vcpu / t.count : 0, avgRamGB: t.count ? t.ram / t.count : 0 });
  } catch (_) { rows = []; }
  const label = a => `${a.name} — ${a.cls === 'ok' ? 'hấp thụ được' : a.cls === 'warn' ? 'sát ngưỡng' : 'vượt ngưỡng'}${a.n1 ? (a.n1.ok ? ', N+1 đạt' : ', mất N+1') : ''}`;
  sel.innerHTML = `<option value="">Tự chọn cluster phù hợp nhất</option>` + rows.map(a => `<option value="${esc(a.name)}">${esc(label(a))}</option>`).join('');
  if (rows.some(a => a.name === prev)) sel.value = prev;
  const hint = $('#szPdfClusterHint');
  if (hint) hint.textContent = rows.length ? `${rows.length} cluster trong phạm vi vCenter đang chọn; % tính sau khi cộng toàn bộ nhu cầu dự án.` : 'Chưa có dữ liệu cluster hiện hữu — PDF sẽ ghi rõ không có cluster để đối chiếu.';
}

/* ---------- Per-report logo override (falls back to the default report logo) ---------- */
const SIZING_LOGO_KEY = 'vco.sizing.logo.v1';
const SZ_MAX_LOGO_BYTES = 2 * 1024 * 1024;
function sizingLogoOverride() {
  const st = sizingState();
  if (st.logoOverride === undefined) {
    try { st.logoOverride = JSON.parse(localStorage.getItem(SIZING_LOGO_KEY) || 'null'); } catch (e) { st.logoOverride = null; }
  }
  return st.logoOverride;
}
function setSizingLogoOverride(val) {
  sizingState().logoOverride = val;
  try { val ? localStorage.setItem(SIZING_LOGO_KEY, JSON.stringify(val)) : localStorage.removeItem(SIZING_LOGO_KEY); } catch (e) { /* too large for storage: keep for this session */ }
}
async function refreshSizingLogoPreview() {
  const img = $('#szLogoImg'), empty = $('#szLogoEmpty'), hint = $('#szLogoHint'), reset = $('#btnSzLogoReset');
  if (!img) return;
  const ov = sizingLogoOverride();
  let src = ov ? ov.dataUrl : null;
  let note = ov ? `Logo riêng cho báo cáo này: ${ov.name || 'ảnh đã chọn'}.` : '';
  if (!src) {
    try { const res = await api.getLogo(); src = res && res.dataUrl ? res.dataUrl : null; } catch (e) { src = null; }
    note = src ? 'Đang dùng logo mặc định trong Cài đặt báo cáo.' : 'Chưa có logo mặc định — PDF sẽ không có logo.';
  }
  img.classList.toggle('hidden', !src);
  empty.classList.toggle('hidden', !!src);
  if (src) img.src = src; else img.removeAttribute('src');
  reset.classList.toggle('hidden', !ov);
  hint.textContent = `${note} Không chọn → dùng logo mặc định. PNG / JPEG / SVG / WEBP, tối đa 2MB; áp dụng cho PDF và Excel Sizing (logo SVG chỉ hiển thị trên PDF).`;
}
async function onSizingLogoFile(file) {
  if (!file) return;
  if (!/^image\/(png|jpeg|svg\+xml|webp)$/i.test(file.type)) { szPdfStatus('Logo phải là PNG, JPEG, SVG hoặc WEBP.', true); return; }
  if (file.size > SZ_MAX_LOGO_BYTES) { szPdfStatus('Logo vượt quá 2MB cho phép.', true); return; }
  const dataUrl = await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = () => reject(new Error('Không đọc được tệp ảnh.')); r.readAsDataURL(file); });
  setSizingLogoOverride({ dataUrl, name: file.name });
  szPdfStatus('');
  refreshSizingLogoPreview();
}

function szPdfStatus(msg, isError = false) {
  // Inline status inside the options panel — the global banner sits at the top of the page and is easy to miss here.
  uiSetStatus('#szPdfStatus', msg, isError ? 'error' : '', { hideWhenEmpty: true });
}

async function exportSizingPdf(btn) {
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = 'Đang tạo PDF…';
  szPdfStatus('Đang tổng hợp dữ liệu và tạo PDF…');
  try {
    const payload = sizingExistingPayload();
    if (!payload.vms.length) {
      szPdfStatus('Danh sách VM trống hoặc chưa có dòng hợp lệ (cần số lượng > 0 và vCPU/RAM > 0) — chưa có gì để lập đề xuất.', true);
      return;
    }
    await api.downloadSizingPdf(payload);
    szPdfStatus('');
    const dd = $('#sizingPdfDropdown'); if (dd) dd.removeAttribute('open');
  } catch (err) {
    console.error('Sizing PDF export failed', err);
    const msg = `Không tạo được PDF: ${err && err.message ? err.message : err}`;
    szPdfStatus(msg, true);
    showValidationBanner([msg], []);
  } finally { btn.disabled = false; btn.textContent = label; }
}

async function exportSizingXlsx(btn) {
  const payload = { ...sizingPayload(), ...sizingDocPayload() };
  if (!payload.vms.length) { showValidationBanner(['Danh sách VM trống — chưa có gì để xuất.'], []); return; }
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = 'Đang tạo Excel…';
  try { await api.downloadSizingXlsx(payload); }
  catch (err) { showValidationBanner([`Không xuất được Excel: ${err.message}`], []); }
  finally { btn.disabled = false; btn.textContent = label; }
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
