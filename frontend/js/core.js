/* =========================================================
   Infra Capacity Ops Management — Core state & utilities
   ========================================================= */
const appState = {
  hosts: [],            // normalized host objects
  vms: [],               // normalized vm objects
  hostsRaw: [],
  vmsRaw: [],
  view: 'dashboard',
  reportTimestamp: null,
  filters: { cluster: 'all', host: 'all' },
  sort: { table: {}, },
  compliance: { search: '', severity: 'all', type: 'all' },
  hostTableSearch: '',
  vmTableSearch: '',
  vmStatusFilter: 'all',
  vmClusterFilter: 'all',
  policy: { idleDays: 30, cpuWarn: 50, cpuHigh: 80, ramWarn: 50, ramHigh: 80, dsWarn: 80, dsHigh: 90, dsCrit: 95,   // datastore = capacity.build_datastore_rows (80 / 90)
  cpuOvercommitMax: 6, ramOvercommitMax: 1.0, htFactor: 2, includeStorage: false },
  charts: {},
  simHistory: [],
  whatif: { cluster: null, tab: 'addHost', drafts: {} },
  validation: { errors: [], warnings: [] }
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const deepClone = (o) => JSON.parse(JSON.stringify(o));
const cleanNum = (v) => { const n = parseFloat(String(v ?? '').replace(/,/g, '').trim()); return isFinite(n) ? n : 0; };
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, m => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[m]));   // ' too: safe in single-quoted attributes
// Display formatting is vi-VN everywhere: 1.234,5 (decision Q7, audit FE-24). Numbers handed to charts,
// CSS widths and CSV keep plain JS numbers / toFixed — only text shown to people goes through these.
const fmtNum = (v, d = 1) => Number(isFinite(v) ? v : 0).toLocaleString('vi-VN', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtPct = (v) => isFinite(v) ? fmtNum(v, 1) + '%' : 'NA';
const fmtInt = (v) => Math.round(isFinite(v) ? v : 0).toLocaleString('vi-VN');
const fmtX = (v, d = 2) => isFinite(v) ? fmtNum(v, d) + 'x' : 'NA';   // overcommit ratio

function formatReportTime(value) {
  if (!value) return '--';
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return '--';
  return new Intl.DateTimeFormat('vi-VN', { dateStyle:'medium', timeStyle:'short' }).format(d);
}

/* CSV / JSON import parsing and row normalisation live in import.js (audit FE-29): core.js keeps only the
   capacity / compliance model, status and data loading (CLAUDE.md). */

/* =========================================================
   Capacity calculations: aggregation, overcommit, status
   ========================================================= */

function getStatusForPct(pct, warnT, highT) {
  if (!isFinite(pct)) return { text:'NA', cls:'na' };
  if (pct > 100) return { text:'VƯỢT NGƯỠNG', cls:'crit' };
  if (pct >= highT) return { text:'CẢNH BÁO', cls:'bad' };
  if (pct >= warnT) return { text:'THEO DÕI', cls:'warn' };
  return { text:'AN TOÀN', cls:'ok' };
}

function combineStatus(...statuses) {
  const order = ['crit','bad','warn','ok','na'];
  const clsList = statuses.map(s => s.cls);
  for (const level of order) {
    if (clsList.includes(level)) {
      if (level === 'na' && clsList.some(c => c !== 'na')) continue;
      return { text: { crit:'VƯỢT NGƯỠNG', bad:'CẢNH BÁO', warn:'THEO DÕI', ok:'AN TOÀN', na:'NA' }[level], cls: level };
    }
  }
  return { text:'NA', cls:'na' };
}

/** Apply what-if deltas (host adds/removes, vm adds/resizes) to base hosts/vms for a target cluster. */
function getEffectiveData(overrides) {
  overrides = overrides || {};
  let hosts = deepClone(appState.hosts);
  let vms = deepClone(appState.vms);

  if (overrides.addHosts) hosts = hosts.concat(overrides.addHosts);
  if (overrides.upgradeHosts && overrides.upgradeHosts.length) {
    // In-place hardware upgrade of existing hosts: replace cores / RAM, keep everything else.
    const map = new Map(overrides.upgradeHosts.map(u => [`${u.cluster}||${(u.host || '').toLowerCase()}`, u]));
    hosts = hosts.map(h => {
      const u = map.get(`${h.cluster}||${(h.host || '').toLowerCase()}`);
      if (!u) return h;
      return { ...h, cores: u.cores != null ? u.cores : h.cores, ramGB: u.ramGB != null ? u.ramGB : h.ramGB };
    });
  }
  if (overrides.removeHostNames && overrides.removeHostNames.length) {
    const set = new Set(overrides.removeHostNames.map(n => (n||'').toLowerCase()));
    hosts = hosts.filter(h => !set.has(h.host.toLowerCase()));
  }
  if (overrides.addVms) vms = vms.concat(overrides.addVms);
  if (overrides.removeVmNames && overrides.removeVmNames.length) {
    const set = new Set(overrides.removeVmNames.map(n => (n||'').toLowerCase()));
    vms = vms.filter(v => !set.has(v.vm.toLowerCase()));
  }
  if (overrides.resizeVm) {
    const target = overrides.resizeVm;
    vms = vms.map(v => {
      if (v.vm.toLowerCase() === target.name.toLowerCase() && v.cluster === target.cluster) {
        return { ...v, vcpu: target.vcpu != null ? target.vcpu : v.vcpu, ramGB: target.ramGB != null ? target.ramGB : v.ramGB };
      }
      return v;
    });
  }
  return { hosts, vms };
}

/** Build full capacity model from hosts + vms (optionally filtered/overridden). */
function buildCapacityModel(hosts, vms) {
  const poweredOn = vms.filter(v => v.isPoweredOn);

  const totalCores = hosts.reduce((a, h) => a + h.cores, 0);
  const totalRamGB = hosts.reduce((a, h) => a + h.ramGB, 0);
  const usedVcpu = poweredOn.reduce((a, v) => a + v.vcpu, 0);
  const usedRamGB = poweredOn.reduce((a, v) => a + v.ramGB, 0);
  const provStorageGB = vms.reduce((a, v) => a + v.diskProvGB, 0);
  const usedStorageGB = vms.reduce((a, v) => a + v.diskUsedGB, 0);

  const storagePct = provStorageGB > 0 ? (usedStorageGB / provStorageGB) * 100 : NaN;

  // pCPU logic = CPU cores vật lý × hệ số Hyperthreading (mặc định 2) — khớp cách vCenter tính số pCPU khả dụng cho VM.
  const htFactor = Math.max(1, appState.policy.htFactor || 2);
  const totalLogicalCores = totalCores * htFactor;
  const cpuOvercommit = totalLogicalCores > 0 ? usedVcpu / totalLogicalCores : NaN;
  const ramOvercommit = totalRamGB > 0 ? usedRamGB / totalRamGB : NaN;

  // CPU%/RAM% hiển thị (thanh bar, status) đều tinh theo mức độ đã dùng "chỗ overcommit cho phép":
  // % = (overcommit thực tế / ngưỡng overcommit cấu hình) x 100. Vượt 100% tức overcommit thực tế đã vượt ngưỡng cho phép.
  const cpuOcMax = appState.policy.cpuOvercommitMax;
  const ramOcMax = appState.policy.ramOvercommitMax;
  const cpuPct = (isFinite(cpuOvercommit) && cpuOcMax > 0) ? (cpuOvercommit / cpuOcMax) * 100 : NaN;
  const ramPct = (isFinite(ramOvercommit) && ramOcMax > 0) ? (ramOvercommit / ramOcMax) * 100 : NaN;

  return {
    hostCount: hosts.length,
    vmCount: vms.length,
    poweredOnCount: poweredOn.length,
    poweredOffCount: vms.length - poweredOn.length,
    totalCores, totalLogicalCores, htFactor, totalRamGB, provStorageGB, usedStorageGB,
    usedVcpu, usedRamGB,
    freeCores: Math.max(0, totalCores - usedVcpu),
    freeLogicalCores: Math.max(0, totalLogicalCores - usedVcpu),
    freeRamGB: Math.max(0, totalRamGB - usedRamGB),
    freeStorageGB: Math.max(0, provStorageGB - usedStorageGB),
    cpuPct, ramPct, storagePct,
    cpuOvercommit, ramOvercommit
  };
}

function getFilteredHosts() {
  return appState.hosts.filter(h => {
    if (appState.filters.cluster !== 'all' && h.cluster !== appState.filters.cluster) return false;
    if (appState.filters.host !== 'all' && h.host !== appState.filters.host) return false;
    return true;
  });
}

function getFilteredVms() {
  return appState.vms.filter(v => {
    if (appState.filters.cluster !== 'all' && v.cluster !== appState.filters.cluster) return false;
    if (appState.filters.host !== 'all' && v.host !== appState.filters.host) return false;
    return true;
  });
}

function getClusterList() {
  const set = new Set();
  appState.hosts.forEach(h => set.add(h.cluster));
  appState.vms.forEach(v => set.add(v.cluster));
  return Array.from(set).sort((a, b) => a.localeCompare(b));
}

function getHostList(clusterFilter) {
  const set = new Set();
  appState.hosts.forEach(h => { if (clusterFilter === 'all' || !clusterFilter || h.cluster === clusterFilter) set.add(h.host); });
  return Array.from(set).sort((a, b) => a.localeCompare(b));
}

// clusters === null means "no cluster filter applied" (every host, from any cluster).
// clusters === [] means "filtered down to zero clusters" (no host qualifies).
function getHostsForClusters(clusters) {
  if (clusters === null) return getHostList('all');
  const allowed = new Set(clusters);
  const set = new Set();
  appState.hosts.forEach(h => { if (allowed.has(h.cluster)) set.add(h.host); });
  return Array.from(set).sort((a, b) => a.localeCompare(b));
}

function buildClusterRows() {
  const clusters = getClusterList();
  return clusters.map(name => {
    const hosts = appState.hosts.filter(h => h.cluster === name);
    const vms = appState.vms.filter(v => v.cluster === name);
    const model = buildCapacityModel(hosts, vms);
    // CPU%/RAM% đã được tính theo overcommit ratio / ngưỡng overcommit (xem buildCapacityModel) nên dùng ngưỡng cố định 80%/100% để đánh giá: <80% AN TOÀN, 80-100% THEO DÕI, >100% nghĩa là đã vượt ngưỡng overcommit cấu hình.
    const cpuStatus = getStatusForPct(model.cpuPct, 80, 100);
    const ramStatus = getStatusForPct(model.ramPct, 80, 100);
    const dsStatus = getStatusForPct(model.storagePct, appState.policy.dsWarn, appState.policy.dsHigh);
    // Status tổng chỉ dựa trên CPU/RAM (đã gắn overcommit) — không tính storage (dùng shared SAN, không thuộc phạm vi compliance).
    const overall = combineStatus(cpuStatus, ramStatus);
    return { name, hosts: hosts.length, vms: vms.length, model, cpuStatus, ramStatus, dsStatus, overall };
  });
}

function buildHostRows() {
  return appState.hosts.map(h => {
    const vmsOnHost = appState.vms.filter(v => v.host === h.host && v.cluster === h.cluster && v.isPoweredOn);
    const vcpuUsed = vmsOnHost.reduce((a, v) => a + v.vcpu, 0);
    const ramUsedGB = vmsOnHost.reduce((a, v) => a + v.ramGB, 0);
    // Overcommit ratio cấp host (vCPU:pCPU logic, vRAM:pRAM) - giống cách tính cấp cluster, pCPU logic = cores × hệ số Hyperthreading.
    const htFactor = Math.max(1, appState.policy.htFactor || 2);
    const logicalCores = h.cores * htFactor;
    const cpuOvercommit = logicalCores > 0 ? vcpuUsed / logicalCores : NaN;
    const ramOvercommit = h.ramGB > 0 ? ramUsedGB / h.ramGB : NaN;
    const cpuAllocPct = logicalCores > 0 ? (vcpuUsed / logicalCores) * 100 : NaN;
    const ramAllocPct = h.ramGB > 0 ? (ramUsedGB / h.ramGB) * 100 : NaN;
    // CPU%/RAM% hiển thị = (overcommit thực tế cấp host / ngưỡng overcommit cấu hình) x 100 — nhất quán với cấp cluster.
    const cpuOcMax = appState.policy.cpuOvercommitMax;
    const ramOcMax = appState.policy.ramOvercommitMax;
    const cpuPct = (isFinite(cpuOvercommit) && cpuOcMax > 0) ? (cpuOvercommit / cpuOcMax) * 100 : NaN;
    const ramPct = (isFinite(ramOvercommit) && ramOcMax > 0) ? (ramOvercommit / ramOcMax) * 100 : NaN;
    const cpuStatus = getStatusForPct(cpuPct, 80, 100);
    const ramStatus = getStatusForPct(ramPct, 80, 100);
    const overall = combineStatus(cpuStatus, ramStatus);
    return { ...h, vcpuUsed, ramUsedGB, cpuAllocPct, ramAllocPct, cpuOvercommit, ramOvercommit, cpuPct, ramPct, vmCount: vmsOnHost.length, cpuStatus, ramStatus, overall };
  });
}

function buildDatastoreRows() {
  const map = new Map();
  appState.vms.forEach(v => {
    const key = v.datastore || 'UNKNOWN';
    if (!map.has(key)) map.set(key, { name: key, provGB: 0, usedGB: 0, vmCount: 0 });
    const d = map.get(key);
    d.provGB += v.diskProvGB;
    d.usedGB += v.diskUsedGB;
    d.vmCount += 1;
  });
  return Array.from(map.values()).map(d => {
    const pct = d.provGB > 0 ? (d.usedGB / d.provGB) * 100 : NaN;
    return { ...d, pct, status: getStatusForPct(pct, appState.policy.dsWarn, appState.policy.dsHigh) };
  }).sort((a, b) => (b.pct || 0) - (a.pct || 0));
}
/* =========================================================
   Compliance checks
   ========================================================= */

const FINDING_PREFIX = {
  host_cpu: 'HCPU', host_ram: 'HRAM', overcommit_cpu: 'CCPU', overcommit_ram: 'CRAM',
  vm_idle: 'IDLE', vm_off: 'POFF', vm_overprovision: 'OVP'
};

// Mã đối soát — must match backend/app/capacity.py finding_code() (FNV-1a 32-bit
// over "type|vcenterKey|cluster|entity", 5 uppercase hex chars) so PDF and web agree.
// vcenterKey disambiguates identical host/VM names across vCenters; cluster-level
// findings are aggregated by name, so they carry no vcenterKey.
function findingCode(type, cluster, entity, vcenterKey) {
  const bytes = new TextEncoder().encode(`${type}|${vcenterKey || ''}|${cluster || ''}|${entity || ''}`);
  let h = 0x811c9dc5;
  for (const b of bytes) { h ^= b; h = Math.imul(h, 0x01000193) >>> 0; }
  return `${FINDING_PREFIX[type] || 'FND'}-${h.toString(16).toUpperCase().padStart(8, '0').slice(0, 5)}`;
}

function buildComplianceFindings() {
  const findings = [];
  const P = appState.policy;

  // 1. Host CPU/RAM vượt ngưỡng overcommit (CPU%/RAM% = overcommit thực tế / ngưỡng overcommit cấu hình x 100)
  buildHostRows().forEach(h => {
    if (isFinite(h.cpuPct) && h.cpuPct >= 80) {
      findings.push({
        severity: h.cpuPct > 100 ? 'critical' : 'warning',
        type: 'host_cpu',
        typeLabel: 'Host CPU vượt ngưỡng',
        entity: h.host, cluster: h.cluster, vcenterKey: h.vcenterKey || '',
        metric: `CPU overcommit ${fmtNum(h.cpuOvercommit,2)}x (ngưỡng ${fmtNum(P.cpuOvercommitMax,1)}x) — ${fmtInt(h.vcpuUsed)}/${fmtInt(h.cores * (P.htFactor||2))} pCPU logic (${fmtInt(h.cores)} core vật lý × HT${fmtNum(P.htFactor||2,0)}), ${fmtPct(h.cpuPct)} ngưỡng`,
        recommendation: h.cpuPct > 100 ? 'Overcommit CPU đã vượt ngưỡng cấu hình — cần di dời VM hoặc bổ sung host ngay.' : 'Overcommit CPU đang tiến gần ngưỡng — cân nhắc vMotion VM sang host khác hoặc lên kế hoạch bổ sung năng lực CPU.'
      });
    }
    if (isFinite(h.ramPct) && h.ramPct >= 80) {
      findings.push({
        severity: h.ramPct > 100 ? 'critical' : 'warning',
        type: 'host_ram',
        typeLabel: 'Host RAM vượt ngưỡng',
        entity: h.host, cluster: h.cluster, vcenterKey: h.vcenterKey || '',
        metric: `RAM overcommit ${fmtNum(h.ramOvercommit,2)}x (ngưỡng ${fmtNum(P.ramOvercommitMax,1)}x) — ${fmtNum(h.ramUsedGB)}/${fmtNum(h.ramGB)} GB, ${fmtPct(h.ramPct)} ngưỡng`,
        recommendation: h.ramPct > 100 ? 'Overcommit RAM đã vượt ngưỡng cấu hình — rủi ro cao, cần hành động ngay.' : 'Overcommit RAM đang tiến gần ngưỡng — theo dõi sát, cân nhắc bổ sung RAM hoặc cân bằng lại workload.'
      });
    }
  });

  // 2. Overcommit ratio vượt ngưỡng ở cấp cluster (vCPU:pCPU, vRAM:pRAM)
  buildClusterRows().forEach(c => {
    const cpuOc = c.model.cpuOvercommit;
    if (isFinite(cpuOc) && cpuOc > P.cpuOvercommitMax) {
      findings.push({
        severity: cpuOc > P.cpuOvercommitMax * 1.25 ? 'critical' : 'warning',
        type: 'overcommit_cpu',
        typeLabel: 'Overcommit ratio vượt ngưỡng (CPU)',
        entity: c.name, cluster: c.name,
        metric: `CPU overcommit ${fmtX(cpuOc)} (ngưỡng ${fmtNum(P.cpuOvercommitMax,1)}x)`,
        recommendation: 'Tỷ lệ vCPU:pCPU vượt ngưỡng khuyến nghị — cân nhắc bổ sung host hoặc giảm vCPU cấp phát cho VM trong cluster.'
      });
    }
    const ramOc = c.model.ramOvercommit;
    if (isFinite(ramOc) && ramOc > P.ramOvercommitMax) {
      findings.push({
        severity: ramOc > P.ramOvercommitMax * 1.25 ? 'critical' : 'warning',
        type: 'overcommit_ram',
        typeLabel: 'Overcommit ratio vượt ngưỡng (RAM)',
        entity: c.name, cluster: c.name,
        metric: `RAM overcommit ${fmtX(ramOc)} (ngưỡng ${fmtNum(P.ramOvercommitMax,1)}x)`,
        recommendation: 'Tỷ lệ vRAM:pRAM vượt ngưỡng khuyến nghị — cân nhắc bổ sung RAM vật lý hoặc giảm RAM cấp phát cho VM trong cluster.'
      });
    }
  });

  // 3. VM idle lâu / tắt lâu / over-provision CPU — chỉ tập trung compliance resource CPU/RAM,
  // bỏ kiểm tra storage/datastore vì hạ tầng dùng shared SAN storage.
  appState.vms.forEach(v => {
    if (v.isPoweredOn && isFinite(v.lastActivityDays) && v.lastActivityDays >= P.idleDays) {
      findings.push({
        severity: v.lastActivityDays >= P.idleDays * 2 ? 'critical' : 'warning',
        type: 'vm_idle',
        typeLabel: 'VM idle lâu',
        entity: v.vm, cluster: v.cluster, vcenterKey: v.vcenterKey || '',
        metric: `Không hoạt động ${fmtInt(v.lastActivityDays)} ngày`,
        recommendation: 'Xác nhận với owner; cân nhắc decommission hoặc thu hồi tài nguyên nếu không còn dùng.'
      });
    }
    if (!v.isPoweredOn && isFinite(v.powerOffDays) && v.powerOffDays >= P.idleDays) {
      findings.push({
        severity: v.powerOffDays >= P.idleDays * 3 ? 'critical' : 'warning',
        type: 'vm_off',
        typeLabel: 'VM tắt lâu',
        entity: v.vm, cluster: v.cluster, vcenterKey: v.vcenterKey || '',
        metric: `Đã tắt ${fmtInt(v.powerOffDays)} ngày`,
        recommendation: 'VM tắt lâu ngày vẫn giữ reservation vCPU/RAM — cân nhắc decommission để thu hồi tài nguyên.'
      });
    }
    // Ngưỡng usage% suy ra từ ngưỡng overcommit CPU (ví dụ 1:6 → ~16.7%): usage thực tế thấp hơn mức này
    // cho thấy vCPU cấp phát đang dư so với tỷ lệ overcommit khuyến nghị.
    const vmOverprovisionUsageThreshold = P.cpuOvercommitMax > 0 ? 100 / P.cpuOvercommitMax : 15;
    if (isFinite(v.cpuUsagePctRaw) && v.cpuUsagePctRaw > 0 && v.cpuUsagePctRaw < vmOverprovisionUsageThreshold && v.vcpu >= 4) {
      findings.push({
        severity: 'info',
        type: 'vm_overprovision',
        typeLabel: 'VM over-provision (vCPU)',
        entity: v.vm, cluster: v.cluster, vcenterKey: v.vcenterKey || '',
        metric: `${fmtInt(v.vcpu)} vCPU cấp phát, CPU usage chỉ ${fmtPct(v.cpuUsagePctRaw)} (ngưỡng ${fmtPct(vmOverprovisionUsageThreshold)} theo overcommit ${fmtNum(P.cpuOvercommitMax,1)}x)`,
        recommendation: 'vCPU cấp phát dư nhiều so với usage thực tế và ngưỡng overcommit CPU cấu hình; cân nhắc resize giảm vCPU.'
      });
    }
  });

  findings.forEach(f => { f.code = findingCode(f.type, f.cluster, f.entity, f.vcenterKey); });
  return findings;
}

const SEVERITY_ORDER = { critical: 0, warning: 1, info: 2 };

function getFilteredCompliance() {
  const P = appState.compliance;
  let findings = buildComplianceFindings();

  // apply cluster/host global filters
  findings = findings.filter(f => {
    if (appState.filters.cluster !== 'all' && f.cluster !== '—' && f.cluster !== appState.filters.cluster) return false;
    return true;
  });

  if (P.severity !== 'all') findings = findings.filter(f => f.severity === P.severity);
  if (P.type !== 'all') findings = findings.filter(f => f.type === P.type);
  if (P.search.trim()) {
    const q = P.search.trim().toLowerCase();
    findings = findings.filter(f => (f.code + ' ' + f.entity + ' ' + f.cluster + ' ' + f.typeLabel).toLowerCase().includes(q));
  }

  findings.sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
  return findings;
}
/* =========================================================
   Sample data, validation banner, export CSV/HTML
   ========================================================= */

function daysAgoISO(days) { return new Date(Date.now() - days * 86400000).toISOString(); }

function loadSampleData() {
  appState.hostsRaw = [
    { Cluster:'Cluster-Prod-A', Host:'esx-a-01', 'CPUs':'64', 'Memory GB':'512', 'CPU Usage %':'62', 'Memory Usage %':'70' },
    { Cluster:'Cluster-Prod-A', Host:'esx-a-02', 'CPUs':'64', 'Memory GB':'512', 'CPU Usage %':'58', 'Memory Usage %':'65' },
    { Cluster:'Cluster-Prod-A', Host:'esx-a-03', 'CPUs':'64', 'Memory GB':'512', 'CPU Usage %':'91', 'Memory Usage %':'88' },
    { Cluster:'Cluster-Prod-B', Host:'esx-b-01', 'CPUs':'48', 'Memory GB':'384', 'CPU Usage %':'35', 'Memory Usage %':'40' },
    { Cluster:'Cluster-Prod-B', Host:'esx-b-02', 'CPUs':'48', 'Memory GB':'384', 'CPU Usage %':'28', 'Memory Usage %':'33' },
    { Cluster:'Cluster-DR', Host:'esx-dr-01', 'CPUs':'32', 'Memory GB':'256', 'CPU Usage %':'12', 'Memory Usage %':'15' },
    { Cluster:'Cluster-DR', Host:'esx-dr-02', 'CPUs':'32', 'Memory GB':'256', 'CPU Usage %':'10', 'Memory Usage %':'14' }
  ];

  appState.vmsRaw = [
    { Cluster:'Cluster-Prod-A', Host:'esx-a-01', Name:'app-web-01', 'Power State':'Powered On', 'CPUs':'8', 'Memory GB':'16', 'Provisioned GB':'300', 'Used GB':'210', 'IP Address':'10.0.0.11', Datastore:'DS-A1', 'CPU Usage %':'45', 'Last Activity': daysAgoISO(2) },
    { Cluster:'Cluster-Prod-A', Host:'esx-a-02', Name:'db-core-01', 'Power State':'Powered On', 'CPUs':'16', 'Memory GB':'64', 'Provisioned GB':'800', 'Used GB':'620', 'IP Address':'10.0.0.12', Datastore:'DS-A1', 'CPU Usage %':'70', 'Last Activity': daysAgoISO(1) },
    { Cluster:'Cluster-Prod-A', Host:'esx-a-03', Name:'app-web-02', 'Power State':'Powered On', 'CPUs':'8', 'Memory GB':'16', 'Provisioned GB':'250', 'Used GB':'90', 'IP Address':'10.0.0.13', Datastore:'DS-A2', 'CPU Usage %':'8', 'Last Activity': daysAgoISO(45) },
    { Cluster:'Cluster-Prod-A', Host:'esx-a-03', Name:'legacy-svc-01', 'Power State':'Powered Off', 'CPUs':'4', 'Memory GB':'8', 'Provisioned GB':'150', 'Used GB':'60', 'IP Address':'10.0.0.14', Datastore:'DS-A2', 'Power Off Date': daysAgoISO(120) },
    { Cluster:'Cluster-Prod-A', Host:'esx-a-01', Name:'batch-job-01', 'Power State':'Powered On', 'CPUs':'12', 'Memory GB':'32', 'Provisioned GB':'400', 'Used GB':'110', 'IP Address':'10.0.0.15', Datastore:'DS-A1', 'CPU Usage %':'6', 'Last Activity': daysAgoISO(60) },
    { Cluster:'Cluster-Prod-B', Host:'esx-b-01', Name:'app-b-01', 'Power State':'Powered On', 'CPUs':'12', 'Memory GB':'32', 'Provisioned GB':'250', 'Used GB':'160', 'IP Address':'10.0.1.11', Datastore:'DS-B1', 'CPU Usage %':'38', 'Last Activity': daysAgoISO(0) },
    { Cluster:'Cluster-Prod-B', Host:'esx-b-02', Name:'db-b-01', 'Power State':'Powered On', 'CPUs':'12', 'Memory GB':'48', 'Provisioned GB':'400', 'Used GB':'380', 'IP Address':'10.0.1.12', Datastore:'DS-B1', 'CPU Usage %':'55', 'Last Activity': daysAgoISO(1) },
    { Cluster:'Cluster-Prod-B', Host:'esx-b-01', Name:'cache-b-01', 'Power State':'Powered Off', 'CPUs':'4', 'Memory GB':'16', 'Provisioned GB':'80', 'Used GB':'40', 'IP Address':'10.0.1.13', Datastore:'DS-B2', 'Power Off Date': daysAgoISO(15) },
    { Cluster:'Cluster-DR', Host:'esx-dr-01', Name:'dr-standby-01', 'Power State':'Powered Off', 'CPUs':'8', 'Memory GB':'32', 'Provisioned GB':'300', 'Used GB':'150', 'IP Address':'10.0.2.11', Datastore:'DS-DR1', 'Power Off Date': daysAgoISO(200) },
    { Cluster:'Cluster-DR', Host:'esx-dr-02', Name:'dr-standby-02', 'Power State':'Powered Off', 'CPUs':'8', 'Memory GB':'32', 'Provisioned GB':'300', 'Used GB':'140', 'IP Address':'10.0.2.12', Datastore:'DS-DR1', 'Power Off Date': daysAgoISO(200) }
  ];

  appState.reportTimestamp = new Date().toISOString();
  rebuildFromRaw();
  refreshAll();
}

function showValidationBanner(errors, warnings, info = []) {
  const banner = $('#validationBanner');
  banner.classList.remove('ok');
  if (!errors.length && !warnings.length && !info.length) { banner.classList.remove('show', 'error', 'warn', 'info'); banner.innerHTML = ''; return; }
  banner.classList.add('show');
  banner.classList.toggle('error', errors.length > 0);
  banner.classList.toggle('warn', errors.length === 0 && warnings.length > 0);
  banner.classList.toggle('info', !errors.length && !warnings.length);
  if (!errors.length && !warnings.length) { banner.innerHTML = info.map(i => `<span>${esc(i)}</span>`).join(' '); return; }
  let html = '';
  if (errors.length) html += `<strong>${errors.length} lỗi dữ liệu:</strong><ul>${errors.slice(0,8).map(e => `<li>${esc(e)}</li>`).join('')}</ul>`;
  if (warnings.length) html += `<strong>${warnings.length} cảnh báo:</strong><ul>${warnings.slice(0,8).map(w => `<li>${esc(w)}</li>`).join('')}</ul>`;
  banner.innerHTML = html;
}

/* Success state of the same banner (e.g. "Đồng bộ ngay" finished): glyph + sentence, no "cảnh báo" heading. */
function showSuccessBanner(message) {
  const banner = $('#validationBanner');
  if (!banner) return;
  banner.classList.remove('error', 'warn', 'info');
  banner.classList.add('show', 'ok');
  banner.innerHTML = `<span class="vb-ok" role="status">${esc(message)}</span>`;
}

function rebuildFromRaw() {
  appState.policy.idleDays = Math.max(1, cleanNum($('#idleThresholdInput').value) || 30);
  appState.policy.cpuOvercommitMax = Math.max(0.1, cleanNum($('#cpuOvercommitInput').value) || 6);
  appState.policy.ramOvercommitMax = Math.max(0.1, cleanNum($('#ramOvercommitInput').value) || 1.0);
  appState.policy.htFactor = Math.max(1, cleanNum($('#htFactorInput').value) || 2);
  // No raw rows staged (data came from a live vCenter sync or was already
  // loaded from the server via /api/data/latest) — nothing to re-parse.
  // Keep the already-normalized appState.hosts/vms as-is; only the policy
  // thresholds above changed, and every render function reads those live.
  if (!appState.hostsRaw.length && !appState.vmsRaw.length) return;
  const { hosts, vms, errors, warnings } = normalizeAndValidate(appState.hostsRaw, appState.vmsRaw);
  appState.hosts = hosts;
  appState.vms = vms;
  appState.validation = { errors, warnings };
  showValidationBanner(errors, warnings);
}

async function loadFileInput(input, target) {
  const file = input.files && input.files[0];
  if (!file) return;
  try {
    const text = await readFile(file);
    let rows = parseInputText(text, file.name);
    // SA5 / A4: unfamiliar layout → saved mapping, or ask the user (AI can pre-fill) before parsing.
    if (typeof importMapRows === 'function') rows = await importMapRows(rows, target === 'hostsRaw' ? 'hosts' : 'vms', file.name);
    appState[target] = rows;
    appState.reportTimestamp = new Date().toISOString();
    rebuildFromRaw();
    refreshAll();
    if (typeof persistImportIfReady === 'function') persistImportIfReady();
  } catch (err) {
    showValidationBanner([`Không thể đọc file "${file.name}": ${err.message}`], []);
  }
}

/* =========================================================
   Filters, view switching, bindings, refreshAll, init
   ========================================================= */

function populateFilterSelects() {
  const clusterSel = $('#filterCluster');
  const hostSel = $('#filterHost');
  const clusters = getClusterList();

  const prevCluster = appState.filters.cluster;
  clusterSel.innerHTML = `<option value="all">Tất cả cluster</option>` + clusters.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
  appState.filters.cluster = clusters.includes(prevCluster) ? prevCluster : 'all';
  clusterSel.value = appState.filters.cluster;

  const hosts = getHostList(appState.filters.cluster);
  const prevHost = appState.filters.host;
  hostSel.innerHTML = `<option value="all">Tất cả host</option>` + hosts.map(h => `<option value="${esc(h)}">${esc(h)}</option>`).join('');
  appState.filters.host = hosts.includes(prevHost) ? prevHost : 'all';
  hostSel.value = appState.filters.host;

  $('#dataFootprint').textContent = `Hosts: ${appState.hosts.length} | VMs: ${appState.vms.length}`;

  if (typeof populatePdfScopeFilter === 'function') populatePdfScopeFilter();
}

function syncViewSections() {
  $$('.view-section').forEach(sec => sec.classList.add('hidden'));
  const view = VIEWS[appState.view] || VIEWS.dashboard;
  const target = $(view.section);
  if (target) target.classList.remove('hidden');
  // Settings: one tab of panels at a time (Cài đặt → Tiêu chí & ngưỡng / Nguồn dữ liệu / …).
  const sub = appState.settingsSub || 'criteria';
  $$('#viewSettings [data-set-tab]').forEach(el => el.classList.toggle('set-off', el.dataset.setTab !== sub));
  if (appState.view === 'settings' && VIEWS.settings.tabRender[sub]) VIEWS.settings.tabRender[sub]();
  document.body.dataset.view = appState.view;
  if (typeof syncNavState === 'function') syncNavState();
  // AI buttons outside page renderers (Hỏi ICO in the top bar, Sizing): show when their task is on.
  if (typeof aiGate === 'function' && currentUser) { aiGate(document.querySelector('.topbar')); aiGate($('#viewSizing')); }
}

function renderActiveView() {
  renderView(appState.view);               // routes.js: how each view renders (+ lazy module)
}

function refreshAll() {
  populateFilterSelects();
  syncViewSections();
  renderActiveView();
}

function bindGlobalInteractions() {
  $('#hostFile').addEventListener('change', e => loadFileInput(e.target, 'hostsRaw'));
  $('#vmFile').addEventListener('change', e => loadFileInput(e.target, 'vmsRaw'));
  $('#btnLoadSample').addEventListener('click', loadSampleData);
  const csvBtn = $('#btnExportCsv'); if (csvBtn) csvBtn.addEventListener('click', exportCsv);

  $('#btnResetAll').addEventListener('click', () => {
    appState.hostsRaw = []; appState.vmsRaw = []; appState.hosts = []; appState.vms = [];
    appState.reportTimestamp = null; appState.filters = { cluster:'all', host:'all' };
    appState.simHistory = []; showValidationBanner([], []);
    refreshAll();
  });

  $('#idleThresholdInput').addEventListener('change', () => { rebuildFromRaw(); refreshAll(); });
  $('#cpuOvercommitInput').addEventListener('change', () => { rebuildFromRaw(); refreshAll(); });
  $('#ramOvercommitInput').addEventListener('change', () => { rebuildFromRaw(); refreshAll(); });
  $('#htFactorInput').addEventListener('change', () => { rebuildFromRaw(); refreshAll(); });

  $('#filterCluster').addEventListener('change', e => {
    appState.filters.cluster = e.target.value;
    appState.filters.host = 'all';
    refreshAll();
  });
  $('#filterHost').addEventListener('change', e => { appState.filters.host = e.target.value; refreshAll(); });

  $('#themeToggle').addEventListener('click', () => {
    const html = document.documentElement;
    const next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    uiSetPref('theme', next);
    renderActiveView(); // charts read their palette from CSS variables
  });

  $('#complianceSearch').addEventListener('input', e => { appState.compliance.search = e.target.value; renderComplianceTable(); renderComplianceKpis(); });
  $('#complianceSeverityFilter').addEventListener('change', e => { appState.compliance.severity = e.target.value; renderComplianceTable(); });
  $('#complianceTypeFilter').addEventListener('change', e => { appState.compliance.type = e.target.value; renderComplianceTable(); });

  $('#hostSearch').addEventListener('input', e => { appState.hostTableSearch = e.target.value; renderHostTable(); });
  $('#vmSearch').addEventListener('input', e => { appState.vmTableSearch = e.target.value; renderVmTable(); });
  $('#vmStatusFilter').addEventListener('change', e => { appState.vmStatusFilter = e.target.value; renderVmTable(); });
  $('#vmClusterFilter').addEventListener('change', e => { appState.vmClusterFilter = e.target.value; renderVmTable(); });
  ['#btnExportHostsXlsx', '#btnExportHostsXlsx2'].forEach(sel => { const b = $(sel); if (b) b.addEventListener('click', e => exportInventoryXlsx('hosts', e.currentTarget)); });
  ['#btnExportVmsXlsx', '#btnExportVmsXlsx2'].forEach(sel => { const b = $(sel); if (b) b.addEventListener('click', e => exportInventoryXlsx('vms', e.currentTarget)); });

  $('#whatifClusterSelect').addEventListener('change', e => { appState.whatif.cluster = e.target.value; renderSimForm(); });
  $$('#simTabs .sim-tab').forEach(btn => btn.addEventListener('click', () => {
    appState.whatif.tab = btn.dataset.sim;
    $$('#simTabs .sim-tab').forEach(b => b.classList.toggle('active', b === btn));
    $('#simResultArea').classList.add('hidden');
    renderSimForm();
  }));
  $('#btnClearSimHistory').addEventListener('click', () => { appState.simHistory = []; renderSimHistory(); });
}
