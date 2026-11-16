/* Manual import (CSV / JSON inventory): parsing, field mapping and row normalisation.
   Moved out of core.js (audit FE-29); loaded right after core.js. */

function normKey(s) { return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ''); }

function buildKeyMap(row) {
  const map = new Map();
  Object.keys(row || {}).forEach(k => map.set(normKey(k), k));
  return map;
}

function pickField(row, keys) {
  const map = buildKeyMap(row);
  for (const key of keys) {
    if (row && row[key] !== undefined && String(row[key]).trim() !== '') return row[key];
    const real = map.get(normKey(key));
    if (real !== undefined && String(row[real]).trim() !== '') return row[real];
  }
  return '';
}

function parseSizeToGB(v) {
  const raw = String(v ?? '').trim();
  if (!raw) return 0;
  const m = raw.replace(/,/g, '').match(/(-?\d+(?:\.\d+)?)\s*(TB|GB|MB|KB)?/i);
  if (!m) return 0;
  const num = parseFloat(m[1]) || 0;
  const unit = (m[2] || 'GB').toUpperCase();
  if (unit === 'TB') return num * 1024;
  if (unit === 'MB') return num / 1024;
  if (unit === 'KB') return num / 1024 / 1024;
  return num;
}

/* ---------- CSV / JSON parsing ---------- */
function splitCsvLine(line) {
  const out = [];
  let current = '', inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (inQuotes && line[i + 1] === '"') { current += '"'; i++; }
      else inQuotes = !inQuotes;
    } else if (ch === ',' && !inQuotes) { out.push(current); current = ''; }
    else current += ch;
  }
  out.push(current);
  return out;
}

function parseCSV(text) {
  const lines = String(text || '').split(/\r?\n/).filter(l => l.trim() !== '');
  if (lines.length < 2) return [];
  const headers = splitCsvLine(lines[0]).map(h => h.trim());
  return lines.slice(1).map(line => {
    const cols = splitCsvLine(line);
    const row = {};
    headers.forEach((h, i) => row[h] = (cols[i] || '').trim());
    return row;
  });
}

function parseInputText(text, fileName) {
  const trimmed = String(text || '').trim();
  if (!trimmed) return [];
  const isJson = /\.json$/i.test(fileName || '') || trimmed.startsWith('[') || trimmed.startsWith('{');
  if (isJson) {
    try {
      const data = JSON.parse(trimmed);
      if (Array.isArray(data)) return data;
      if (data && Array.isArray(data.rows)) return data.rows;
      if (data && Array.isArray(data.data)) return data.data;
      if (data && typeof data === 'object') return [data];
      return [];
    } catch (e) {
      throw new Error('File JSON không hợp lệ: ' + e.message);
    }
  }
  return parseCSV(trimmed);
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = reject;
    reader.readAsText(file);
  });
}
/* =========================================================
   Normalize & validate host / VM rows
   ========================================================= */

function normalizePowerState(row) {
  const raw = String(pickField(row, ['PowerState','Power State','VM Power State','State','Status','Runtime Power State'])).trim().toLowerCase();
  if (!raw) return { text:'Powered On', isPoweredOn:true };
  if (raw.includes('on') || raw === 'running') return { text:'Powered On', isPoweredOn:true };
  if (raw.includes('off') || raw === 'stopped' || raw.includes('suspend')) return { text:'Powered Off', isPoweredOn:false };
  return { text: raw, isPoweredOn:true };
}

function normalizeIpAddress(row) {
  const raw = String(pickField(row, ['IP Address','IP Addresses','IPAddress','IP','Primary IP','Guest IP Address','NIC 1 IP']) || '').trim();
  if (!raw) return '';
  const ipv4 = raw.match(/\b(?:\d{1,3}\.){3}\d{1,3}\b/);
  if (ipv4) return ipv4[0];
  return raw.split(/[;,|]/).map(x => x.trim()).filter(Boolean)[0] || raw;
}

function parseMemoryGB(row, mbKeys, gbKeys, genericKeys) {
  const mbVal = pickField(row, mbKeys);
  if (String(mbVal).trim()) return cleanNum(mbVal) / 1024;
  const gbVal = pickField(row, gbKeys);
  if (String(gbVal).trim()) return cleanNum(gbVal);
  return parseSizeToGB(pickField(row, genericKeys));
}

function parseDateSafe(v) {
  if (!v) return null;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

function daysSince(date) {
  if (!date) return null;
  const diffMs = Date.now() - date.getTime();
  return Math.max(0, Math.floor(diffMs / 86400000));
}

/**
 * Normalize a raw host row into a canonical shape.
 * Returns { row, errors, warnings }
 */
function normalizeHostRow(row, index) {
  const errors = [], warnings = [];
  const name = String(pickField(row, ['Name','Host','Host Name','ESXi Host','name']) || '').trim();
  const cluster = String(pickField(row, ['Cluster','cluster','Cluster Name','Compute Cluster','ClusterName']) || '').trim();
  const cores = cleanNum(pickField(row, ['CPUs','NumCpu','Num CPU','CPU Cores','Cores','cores','Processor Cores','Cpu Total Cores']));
  const cpuMhz = cleanNum(pickField(row, ['CPU MHz','CpuMhz','CPU Mhz per Core','CPU Speed']));
  const ramGB = parseMemoryGB(row,
    ['Memory Size MB','Memory Size (MB)','MemoryMB','Memory_MB','RAM_MB','Host Memory MB','Total Memory MB','Memory Capacity MB'],
    ['Memory GB','RAM GB','MemoryGB','RAM_GB','Total Memory GB','Memory Capacity GB'],
    ['Memory Size','Memory','RAM','Host Memory','Total Memory','Memory Capacity']);
  const cpuUsagePct = cleanNum(pickField(row, ['CPU Usage %','CPU Usage(%)','CpuUsagePct','CPU Utilization']));
  const ramUsagePct = cleanNum(pickField(row, ['Memory Usage %','RAM Usage %','MemoryUsagePct','Memory Utilization']));
  const status = String(pickField(row, ['Connection State','ConnectionState','Status']) || 'Connected').trim();

  if (!name) errors.push(`Dòng host #${index + 1}: thiếu tên host.`);
  if (!cluster) warnings.push(`Host "${name || '#' + (index + 1)}": thiếu Cluster, sẽ gán vào UNKNOWN.`);
  if (cores <= 0) warnings.push(`Host "${name || '#' + (index + 1)}": CPU cores không hợp lệ hoặc bằng 0.`);
  if (ramGB <= 0) warnings.push(`Host "${name || '#' + (index + 1)}": RAM không hợp lệ hoặc bằng 0.`);

  return {
    row: {
      cluster: cluster || 'UNKNOWN',
      host: name || `HOST-${index + 1}`,
      name: name || `HOST-${index + 1}`,
      cores, cpuMhz, ramGB,
      cpuUsagePctRaw: cpuUsagePct,
      ramUsagePctRaw: ramUsagePct,
      status,
      raw: row
    },
    errors, warnings
  };
}

/**
 * Normalize a raw VM row.
 */
function normalizeVmRow(row, index) {
  const errors = [], warnings = [];
  const power = normalizePowerState(row);
  const vmName = String(pickField(row, ['Name','VM','VM Name','Virtual Machine','VMName','Display Name']) || '').trim();
  const cluster = String(pickField(row, ['Cluster','cluster','Cluster Name','Compute Cluster','ClusterName']) || '').trim();
  const host = String(pickField(row, ['Host','ESXi Host','VM Host','Host Name','HostName','Runtime Host']) || '').trim();
  const vcpu = cleanNum(pickField(row, ['CPUs','NumCpu','Num CPU','vCPU','vCPUs','Number of CPUs','Num vCPU','CPU Count']));
  const ramGB = parseMemoryGB(row,
    ['Memory Size MB','Memory Size (MB)','MemoryMB','Memory_MB','RAM_MB','Configured Memory MB','Guest Memory MB'],
    ['Memory GB','RAM GB','MemoryGB','RAM_GB'],
    ['Memory Size','Memory','RAM','Configured Memory','Guest Memory']);
  const diskProvGB = parseSizeToGB(pickField(row, ['Provisioned Space','Provisioned','Disk Prov GB','Provisioned GB','Provisioned Size','Provisioned Storage','Capacity Provisioned']));
  const diskUsedGB = parseSizeToGB(pickField(row, ['Used Space','Used','Disk Used GB','Used GB','Consumed Space','Consumed Storage','Used Storage']));
  const datastore = String(pickField(row, ['Datastore','Datastore Name','DS','Storage']) || 'UNKNOWN').trim();
  const cpuUsagePct = cleanNum(pickField(row, ['CPU Usage %','CPU Usage(%)','CpuUsagePct','CPU Utilization']));
  const ramUsagePct = cleanNum(pickField(row, ['Memory Usage %','RAM Usage %','MemoryUsagePct','Memory Utilization']));
  const lastActivity = parseDateSafe(pickField(row, ['Last Activity','LastActivity','Last Seen Active','Last IO','Last Used']));
  const powerOffSince = parseDateSafe(pickField(row, ['Power Off Date','PowerOffDate','Last Power Off','State Change Date']));
  const provisionedDate = parseDateSafe(pickField(row, ['Provisioned Date','Created Date','CreationDate']));
  // Stable identity (RVTools "VM UUID"/"InstanceUUID", PowerCLI "InstanceUuid") — lets the
  // history compare snapshots by ID so rename / migrate are not counted as add/remove.
  const instanceUuid = String(pickField(row, ['InstanceUuid','Instance UUID','InstanceUUID','VM UUID','VM_UUID','instanceUuid','Uuid','UUID']) || '').trim() || null;

  if (!vmName) errors.push(`Dòng VM #${index + 1}: thiếu tên VM.`);
  if (!cluster) warnings.push(`VM "${vmName || '#' + (index + 1)}": thiếu Cluster, sẽ gán vào UNKNOWN.`);
  if (vcpu <= 0) warnings.push(`VM "${vmName || '#' + (index + 1)}": vCPU không hợp lệ hoặc bằng 0.`);
  if (diskProvGB > 0 && diskUsedGB > diskProvGB * 1.01) warnings.push(`VM "${vmName || '#' + (index + 1)}": Disk Used vượt quá Disk Provisioned.`);

  return {
    row: {
      cluster: cluster || 'UNKNOWN',
      host, vm: vmName || `VM-${index + 1}`,
      powerState: power.text, isPoweredOn: power.isPoweredOn,
      ipAddress: normalizeIpAddress(row),
      instanceUuid,
      vcpu, ramGB, diskProvGB, diskUsedGB, datastore,
      cpuUsagePctRaw: cpuUsagePct, ramUsagePctRaw: ramUsagePct,
      lastActivity, powerOffSince, provisionedDate,
      lastActivityDays: daysSince(lastActivity),
      powerOffDays: daysSince(powerOffSince),
      raw: row
    },
    errors, warnings
  };
}

function normalizeAndValidate(hostRawRows, vmRawRows) {
  const errors = [], warnings = [];
  const hosts = [], vms = [];
  const seenHosts = new Set();

  (hostRawRows || []).forEach((row, i) => {
    const { row: h, errors: e, warnings: w } = normalizeHostRow(row, i);
    errors.push(...e); warnings.push(...w);
    const key = (h.cluster + '::' + h.host).toLowerCase();
    if (seenHosts.has(key)) {
      warnings.push(`Host trùng lặp bị bỏ qua: "${h.host}" trong cluster "${h.cluster}".`);
      return;
    }
    seenHosts.add(key);
    hosts.push(h);
  });

  (vmRawRows || []).forEach((row, i) => {
    const { row: v, errors: e, warnings: w } = normalizeVmRow(row, i);
    errors.push(...e); warnings.push(...w);
    vms.push(v);
  });

  return { hosts, vms, errors, warnings };
}
