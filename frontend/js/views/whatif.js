/* =========================================================
   views/whatif.js — What-if: kịch bản mẫu, thêm / nâng cấp host, thêm VM, lịch sử mô phỏng.
   Reads the capacity model in core.js (buildCapacityModel, buildClusterRows, buildHostRows,
   buildComplianceFindings, …); the maths stays in core.js so it keeps matching capacity.py.
   ========================================================= */

/* =========================================================
   What-if Simulation
   ========================================================= */

const SCENARIO_TEMPLATES = [
  { id:'peakSeason', title:'Mùa cao điểm', desc:'Tăng tải VM 30% (vCPU & RAM) để mô phỏng cao điểm kinh doanh.' },
  { id:'drDrill', title:'Diễn tập DR', desc:'Bật toàn bộ VM đang tắt để mô phỏng kích hoạt DR site.' },
  { id:'lostHost', title:'Mất 1 host', desc:'Loại bỏ host lớn nhất khỏi cluster để kiểm tra khả năng chịu lỗi N+1.' },
  { id:'migration', title:'Di chuyển VM', desc:'Di chuyển 30% VM từ cluster tải cao nhất sang cluster tải thấp nhất.' },
  { id:'costOpt', title:'Tối ưu chi phí', desc:'Decommission toàn bộ VM idle/tắt lâu để giải phóng tài nguyên.' },
  { id:'expansion', title:'Kế hoạch mở rộng', desc:'Thêm 1 host mới (trung bình cấu hình hiện có) vào cluster mục tiêu.' },
  { id:'hostUpgrade', title:'Nâng cấp host', desc:'Nâng cấp tại chỗ toàn bộ host hiện hữu: RAM ×2, CPU cores giữ nguyên (so sánh với phương án mua host mới).' },
  { id:'upgradeAndExpand', title:'Nâng cấp + mở rộng', desc:'Kết hợp: RAM ×2 cho host hiện hữu và thêm 1 host mới (cấu hình bằng host lớn nhất sau nâng cấp).' }
];

function currentWhatifCluster() {
  return appState.whatif.cluster || getClusterList()[0] || null;
}

function baseModelForCluster(clusterName) {
  const hosts = appState.hosts.filter(h => h.cluster === clusterName);
  const vms = appState.vms.filter(v => v.cluster === clusterName);
  return buildCapacityModel(hosts, vms);
}

function modelWithOverrides(clusterName, overrides) {
  const eff = getEffectiveData(overrides);
  const hosts = eff.hosts.filter(h => h.cluster === clusterName);
  const vms = eff.vms.filter(v => v.cluster === clusterName);
  return buildCapacityModel(hosts, vms);
}

function deltaBadge(before, after, higherIsWorse = true) {
  const delta = after - before;
  if (!isFinite(delta) || Math.abs(delta) < 0.05) return `<span class="delta-badge delta-flat">±0.0</span>`;
  const worse = higherIsWorse ? delta > 0 : delta < 0;
  return `<span class="delta-badge ${worse ? 'delta-up' : 'delta-down'}">${delta >= 0 ? '+' : ''}${fmtNum(delta, 1)}</span>`;
}

function renderCompareBlock(label, before, after, unit, higherIsWorse = true) {
  return `
    <div class="metric-row">
      <span>${esc(label)}</span>
      <span class="mono">${fmtNum(before,1)}${unit} → ${fmtNum(after,1)}${unit} ${deltaBadge(before, after, higherIsWorse)}</span>
    </div>`;
}

function renderSingleBlock(label, value, unit) {
  return `
    <div class="metric-row">
      <span>${esc(label)}</span>
      <span class="mono">${fmtNum(value,1)}${unit}</span>
    </div>`;
}

function buildInsight(clusterName, before, after) {
  // CPU%/RAM% đã tính theo overcommit ratio / ngưỡng overcommit nên dùng ngưỡng cố định 80%/100%.
  const bStatus = getStatusForPct(before.cpuPct, 80, 100);
  const aStatusCpu = getStatusForPct(after.cpuPct, 80, 100);
  const aStatusRam = getStatusForPct(after.ramPct, 80, 100);
  const worstAfter = combineStatus(aStatusCpu, aStatusRam);
  let msg = `Cluster <strong>${esc(clusterName)}</strong>: `;
  if (worstAfter.cls === 'crit') msg += 'sau thay đổi sẽ VƯỢT NGƯỠNG năng lực — cần bổ sung tài nguyên trước khi triển khai.';
  else if (worstAfter.cls === 'bad') msg += 'sau thay đổi bước vào vùng CẢNH BÁO — nên có kế hoạch mở rộng trong ngắn hạn.';
  else if (worstAfter.cls === 'warn') msg += 'sau thay đổi ở mức THEO DÕI — vẫn an toàn nhưng cần giám sát sát sao.';
  else msg += 'sau thay đổi vẫn AN TOÀN với biên năng lực còn dư.';
  if (after.cpuPct - before.cpuPct > 15) msg += ' CPU tăng đáng kể, là yếu tố cần ưu tiên theo dõi.';
  if (after.ramPct - before.ramPct > 15) msg += ' RAM tăng đáng kể, là yếu tố cần ưu tiên theo dõi.';
  return msg;
}

function renderSimResult(clusterName, before, after, extraRows, title) {
  const html = `
    <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">${esc(title)}</div><p class="panel-sub">So sánh before/after cho cluster ${esc(clusterName)}.</p></div></div>
    <div class="compare-grid">
      <div class="compare-col">
        <h5>Before</h5>
        ${renderSingleBlock('CPU %', before.cpuPct, '%')}
        ${renderSingleBlock('RAM %', before.ramPct, '%')}
        ${appState.policy.includeStorage ? renderSingleBlock('Storage %', before.storagePct, '%') : ''}
        ${renderSingleBlock('CPU overcommit', before.cpuOvercommit, 'x')}
        ${renderSingleBlock('RAM overcommit', before.ramOvercommit, 'x')}
      </div>
      <div class="compare-arrow">→</div>
      <div class="compare-col">
        <h5>After</h5>
        ${renderCompareBlock('CPU %', before.cpuPct, after.cpuPct, '%')}
        ${renderCompareBlock('RAM %', before.ramPct, after.ramPct, '%')}
        ${appState.policy.includeStorage ? renderCompareBlock('Storage %', before.storagePct, after.storagePct, '%') : ''}
        ${renderCompareBlock('CPU overcommit', before.cpuOvercommit, after.cpuOvercommit, 'x')}
        ${renderCompareBlock('RAM overcommit', before.ramOvercommit, after.ramOvercommit, 'x')}
      </div>
    </div>
    ${extraRows || ''}
    <div class="insight-box">💡 ${buildInsight(clusterName, before, after)}</div>
  `;
  const area = $('#simResultArea');
  area.innerHTML = html;
  area.classList.remove('hidden');

  appState.simHistory.unshift({ title, cluster: clusterName, time: new Date().toISOString(), before, after });
  appState.simHistory = appState.simHistory.slice(0, 12);
  renderSimHistory();
}

function renderSimHistory() {
  const list = $('#simHistoryList');
  if (!appState.simHistory.length) { list.innerHTML = `<div class="empty-note">Chưa có lần mô phỏng nào.</div>`; return; }
  list.innerHTML = appState.simHistory.map(h => `
    <div class="sim-log-item">
      <strong>${esc(h.title)}</strong> · ${esc(h.cluster)}<br>
      CPU ${fmtPct(h.before.cpuPct)} → ${fmtPct(h.after.cpuPct)} · RAM ${fmtPct(h.before.ramPct)} → ${fmtPct(h.after.ramPct)}
      <div style="color:var(--text-tertiary);margin-top:.2rem;">${formatReportTime(h.time)}</div>
    </div>
  `).join('');
}

/* ---------- Multi-row Add Host ---------- */
function ensureHostRows(cluster) {
  const d = appState.whatif.drafts;
  if (!d.addHostRows || !d.addHostRows.length) {
    d.addHostRows = [{ id: 1, name: 'NEW-HOST-01', cores: 32, ramGB: 256 }];
    d.addHostSeq = 1;
  }
}

function renderAddHostForm(cluster, area, opts) {
  opts = opts || {};
  const rows = appState.whatif.drafts.addHostRows;
  area.innerHTML = `
    <div class="multi-row-list" id="addHostRowsList">
      ${rows.map(r => `
        <div class="multi-row-item" data-row-id="${r.id}">
          <div class="sim-form-grid">
            <div class="field"><label>Tên host mới</label><input class="rowName" value="${esc(r.name)}"></div>
            <div class="field"><label>CPU cores</label><input class="rowCores" value="${r.cores}"></div>
            <div class="field"><label>RAM GB</label><input class="rowRam" value="${r.ramGB}"></div>
          </div>
          <button class="btn btn-secondary btn-sm icon-btn btn-remove-row" type="button" title="Xoá host này" ${rows.length <= 1 ? 'disabled' : ''}>✕</button>
        </div>
      `).join('')}
    </div>
    <div class="multi-row-add"><button class="btn btn-secondary btn-sm" id="btnAddHostRow" type="button">+ Thêm host</button></div>
    ${opts.embedded ? '' : `<div class="sim-actions"><button class="btn btn-primary" id="btnRunAddHost" type="button">Chạy mô phỏng (${rows.length} host)</button></div>`}
  `;

  const syncRowsFromDom = () => {
    const items = $$('#addHostRowsList .multi-row-item');
    rows.forEach((r, i) => {
      const el = items[i];
      if (!el) return;
      r.name = el.querySelector('.rowName').value.trim() || `NEW-HOST-${i+1}`;
      r.cores = cleanNum(el.querySelector('.rowCores').value);
      r.ramGB = cleanNum(el.querySelector('.rowRam').value);
    });
  };

  appState.whatif.drafts.syncAddHostRows = syncRowsFromDom;

  $$('#addHostRowsList .btn-remove-row').forEach((btn, i) => {
    btn.onclick = () => {
      syncRowsFromDom();
      rows.splice(i, 1);
      renderAddHostForm(cluster, area, opts);
    };
  });

  $('#btnAddHostRow').onclick = () => {
    syncRowsFromDom();
    appState.whatif.drafts.addHostSeq += 1;
    const n = appState.whatif.drafts.addHostSeq;
    rows.push({ id: n, name: `NEW-HOST-0${n}`, cores: 32, ramGB: 256 });
    renderAddHostForm(cluster, area, opts);
  };

  if (opts.embedded) return;
  $('#btnRunAddHost').onclick = () => {
    syncRowsFromDom();
    const addHosts = rows.map(r => ({ cluster, host: r.name || 'NEW-HOST', cores: cleanNum(r.cores), ramGB: cleanNum(r.ramGB) }));
    const before = baseModelForCluster(cluster);
    const after = modelWithOverrides(cluster, { addHosts });
    const totalCores = addHosts.reduce((a,h)=>a+h.cores,0), totalRam = addHosts.reduce((a,h)=>a+h.ramGB,0);
    const extra = addHosts.length > 1
      ? `<p class="panel-sub" style="margin-top:.5rem;">Thêm ${addHosts.length} host mới: tổng +${fmtInt(totalCores)} cores, +${fmtNum(totalRam,0)} GB RAM.</p>`
      : '';
    renderSimResult(cluster, before, after, extra, addHosts.length > 1 ? `Thêm host (${addHosts.length} host)` : 'Thêm host');
  };
}

/* ---------- Multi-row Add VM ---------- */
function ensureVmRows(cluster) {
  const d = appState.whatif.drafts;
  if (!d.addVmRows || !d.addVmRows.length) {
    d.addVmRows = [{ id: 1, count: 5, vcpu: 4, ramGB: 16, diskGB: 100 }];
    d.addVmSeq = 1;
  }
}

function renderAddVmForm(cluster, area) {
  const rows = appState.whatif.drafts.addVmRows;
  area.innerHTML = `
    <div class="multi-row-list" id="addVmRowsList">
      ${rows.map(r => `
        <div class="multi-row-item" data-row-id="${r.id}">
          <div class="sim-form-grid">
            <div class="field"><label>Số lượng VM</label><input class="rowCount" value="${r.count}"></div>
            <div class="field"><label>vCPU / VM</label><input class="rowVcpu" value="${r.vcpu}"></div>
            <div class="field"><label>RAM GB / VM</label><input class="rowRam" value="${r.ramGB}"></div>
            <div class="field"><label>Disk GB / VM</label><input class="rowDisk" value="${r.diskGB}"></div>
          </div>
          <button class="btn btn-secondary btn-sm icon-btn btn-remove-row" type="button" title="Xoá nhóm VM này" ${rows.length <= 1 ? 'disabled' : ''}>✕</button>
        </div>
      `).join('')}
    </div>
    <div class="multi-row-add"><button class="btn btn-secondary btn-sm" id="btnAddVmRow" type="button">+ Thêm nhóm VM</button></div>
    <div class="sim-actions"><button class="btn btn-primary" id="btnRunAddVm" type="button">Chạy mô phỏng</button></div>
  `;

  const syncRowsFromDom = () => {
    const items = $$('#addVmRowsList .multi-row-item');
    rows.forEach((r, i) => {
      const el = items[i];
      if (!el) return;
      r.count = Math.max(1, Math.round(cleanNum(el.querySelector('.rowCount').value)) || 1);
      r.vcpu = cleanNum(el.querySelector('.rowVcpu').value);
      r.ramGB = cleanNum(el.querySelector('.rowRam').value);
      r.diskGB = cleanNum(el.querySelector('.rowDisk').value);
    });
  };

  $$('#addVmRowsList .btn-remove-row').forEach((btn, i) => {
    btn.onclick = () => {
      syncRowsFromDom();
      rows.splice(i, 1);
      renderAddVmForm(cluster, area);
    };
  });

  $('#btnAddVmRow').onclick = () => {
    syncRowsFromDom();
    appState.whatif.drafts.addVmSeq += 1;
    const n = appState.whatif.drafts.addVmSeq;
    rows.push({ id: n, count: 3, vcpu: 2, ramGB: 8, diskGB: 50 });
    renderAddVmForm(cluster, area);
  };

  $('#btnRunAddVm').onclick = () => {
    syncRowsFromDom();
    let seq = 0;
    const addVms = [];
    rows.forEach((r, gi) => {
      const count = Math.max(1, Math.round(cleanNum(r.count)) || 1);
      const vcpu = cleanNum(r.vcpu), ram = cleanNum(r.ramGB), disk = cleanNum(r.diskGB);
      for (let i = 0; i < count; i++) {
        seq += 1;
        addVms.push({ cluster, host:'', vm:`SIM-VM-${gi+1}-${seq}`, powerState:'Powered On', isPoweredOn:true, ipAddress:'', vcpu, ramGB:ram, diskProvGB:disk, diskUsedGB:disk*0.5, datastore:'UNKNOWN', lastActivityDays:0, powerOffDays:null });
      }
    });
    const before = baseModelForCluster(cluster);
    const after = modelWithOverrides(cluster, { addVms });
    const totalVcpu = addVms.reduce((a,v)=>a+v.vcpu,0), totalRam = addVms.reduce((a,v)=>a+v.ramGB,0);
    const groupSummary = rows.map(r => `${Math.round(r.count)}× (${fmtNum(r.vcpu,0)} vCPU/${fmtNum(r.ramGB,0)}GB)`).join(', ');
    const extra = `<p class="panel-sub" style="margin-top:.5rem;">Thêm ${addVms.length} VM mới từ ${rows.length} nhóm [${esc(groupSummary)}], tổng +${fmtInt(totalVcpu)} vCPU, +${fmtNum(totalRam,0)} GB RAM.</p>`;
    renderSimResult(cluster, before, after, extra, rows.length > 1 ? `Thêm VM (${rows.length} nhóm, ${addVms.length} VM)` : 'Thêm VM');
  };
}

/* ---------- Upgrade existing hosts (CPU / RAM in place) ---------- */
function ensureUpgradeDraft(cluster) {
  const d = appState.whatif.drafts;
  if (!d.upgrade || d.upgrade.cluster !== cluster) {
    d.upgrade = { cluster, selected: null, cpuMode: 'keep', cpuVal: 48, ramMode: 'set', ramVal: 512 };
  }
}

/** Apply a CPU/RAM change rule to a host; returns the upgraded {cores, ramGB}. */
function applyUpgradeRule(h, u) {
  const calc = (cur, mode, val) => {
    const v = cleanNum(val);
    if (mode === 'set') return isFinite(v) && v > 0 ? v : cur;
    if (mode === 'add') return cur + (isFinite(v) ? v : 0);
    if (mode === 'pct') return cur * (1 + (isFinite(v) ? v : 0) / 100);
    return cur;
  };
  return { cores: Math.max(1, Math.round(calc(h.cores, u.cpuMode, u.cpuVal))), ramGB: Math.max(1, Math.round(calc(h.ramGB, u.ramMode, u.ramVal))) };
}

/** Per-host before→after table, totals, equivalent-new-host comparison and N+1 check after the upgrade. */
function buildUpgradeExtra(cluster, hosts, upgradeHosts, addHosts) {
  addHosts = addHosts || [];
  const byName = new Map(upgradeHosts.map(u => [u.host, u]));
  const changed = hosts.filter(h => byName.has(h.host));
  const dCores = changed.reduce((a, h) => a + (byName.get(h.host).cores - h.cores), 0);
  const dRam = changed.reduce((a, h) => a + (byName.get(h.host).ramGB - h.ramGB), 0);
  const avgCores = hosts.length ? hosts.reduce((a, h) => a + h.cores, 0) / hosts.length : 0;
  const avgRam = hosts.length ? hosts.reduce((a, h) => a + h.ramGB, 0) / hosts.length : 0;
  const eqCpu = avgCores > 0 ? dCores / avgCores : 0, eqRam = avgRam > 0 ? dRam / avgRam : 0;

  // N+1 after the change (new hosts included): CPU against losing the host with most cores, RAM against losing the one
  // with most RAM — the same rule as capacity.evaluate_ha_headroom / planning.
  const upgraded = hosts.map(h => byName.has(h.host) ? { ...h, ...byName.get(h.host) } : h).concat(addHosts);
  const bigCpu = [...upgraded].sort((a, b) => b.cores - a.cores || b.ramGB - a.ramGB)[0];
  const bigRam = [...upgraded].sort((a, b) => b.ramGB - a.ramGB || b.cores - a.cores)[0];
  const lose = (h) => modelWithOverrides(cluster, { upgradeHosts, addHosts, removeHostNames: h ? [h.host] : [] });
  const n1 = { cpuPct: lose(bigCpu).cpuPct, ramPct: (bigRam === bigCpu ? lose(bigCpu) : lose(bigRam)).ramPct };
  const biggest = bigCpu === bigRam || !(n1.ramPct > n1.cpuPct) ? bigCpu : bigRam;
  const n1Ok = upgraded.length >= 2 && isFinite(n1.cpuPct) && isFinite(n1.ramPct) && n1.cpuPct <= 100 && n1.ramPct <= 100;
  const addCores = addHosts.reduce((a, h) => a + h.cores, 0), addRam = addHosts.reduce((a, h) => a + h.ramGB, 0);
  const addRows = addHosts.map(h => `<tr><td><strong>${esc(h.host)}</strong> ${uiTag('mới')}</td>
      <td class="mono">— → <strong>${fmtInt(h.cores)}</strong></td><td class="mono">— → <strong>${fmtNum(h.ramGB, 0)}</strong> GB</td>
      <td class="mono">+${fmtInt(h.cores)}</td><td class="mono">+${fmtNum(h.ramGB, 0)} GB</td></tr>`).join('');
  const combinedLine = addHosts.length
    ? `Kết hợp: nâng cấp ${changed.length} host (${dCores >= 0 ? '+' : ''}${fmtInt(dCores)} cores, ${dRam >= 0 ? '+' : ''}${fmtNum(dRam, 0)} GB) + thêm ${addHosts.length} host mới (+${fmtInt(addCores)} cores, +${fmtNum(addRam, 0)} GB)
       = <strong>tổng ${dCores + addCores >= 0 ? '+' : ''}${fmtInt(dCores + addCores)} cores, ${dRam + addRam >= 0 ? '+' : ''}${fmtNum(dRam + addRam, 0)} GB RAM</strong>; số host ${hosts.length} → ${hosts.length + addHosts.length}. `
    : '';

  const rows = changed.map(h => {
    const u = byName.get(h.host);
    const cc = u.cores !== h.cores, rc = u.ramGB !== h.ramGB;
    return `<tr><td>${uiEntityLink('host', h.host, h.vcenterKey)}</td>
      <td class="mono">${fmtInt(h.cores)} → ${cc ? `<strong>${fmtInt(u.cores)}</strong>` : fmtInt(u.cores)}</td>
      <td class="mono">${fmtNum(h.ramGB, 0)} → ${rc ? `<strong>${fmtNum(u.ramGB, 0)}</strong>` : fmtNum(u.ramGB, 0)} GB</td>
      <td class="mono">${cc ? (u.cores > h.cores ? '+' : '') + fmtInt(u.cores - h.cores) : '—'}</td>
      <td class="mono">${rc ? (u.ramGB > h.ramGB ? '+' : '') + fmtNum(u.ramGB - h.ramGB, 0) + ' GB' : '—'}</td></tr>`;
  }).join('');
  return `
    <div class="table-wrap" style="margin-top:.75rem;max-height:260px;overflow:auto;">
      <table class="data-table">
        <thead><tr><th>Host</th><th>CPU cores</th><th>RAM</th><th>Δ cores</th><th>Δ RAM</th></tr></thead>
        <tbody>${rows}${addRows}</tbody>
      </table>
    </div>
    <p class="panel-sub" style="margin-top:.5rem;">${combinedLine}Nâng cấp <strong>${changed.length}/${hosts.length}</strong> host hiện hữu: <strong>${dCores >= 0 ? '+' : ''}${fmtInt(dCores)} cores</strong>, <strong>${dRam >= 0 ? '+' : ''}${fmtNum(dRam, 0)} GB RAM</strong>
      — tương đương ${fmtNum(eqCpu, 1)} host (CPU) / ${fmtNum(eqRam, 1)} host (RAM) so với host trung bình hiện có (${fmtInt(avgCores)} cores / ${fmtInt(avgRam)} GB).
      N+1 sau thay đổi (mất ${biggest ? esc(biggest.host) : 'host lớn nhất'}): <strong style="color:${n1Ok ? 'var(--status-ok)' : 'var(--status-crit)'}">${n1Ok ? 'Đạt' : 'Không đạt'}</strong>
      (CPU ${fmtPct(n1.cpuPct)} · RAM ${fmtPct(n1.ramPct)}). ${addHosts.length ? 'Host mới làm tăng số host nên cải thiện dự phòng N+1; phần nâng cấp tại chỗ chỉ tăng năng lực.' : 'Lưu ý: nâng cấp tại chỗ không tăng số host nên không cải thiện dự phòng N+1.'} Cần xác nhận slot DIMM / socket CPU còn trống và downtime bảo trì từng host.</p>`;
}

function renderUpgradeHostForm(cluster, area, opts) {
  opts = opts || {};
  ensureUpgradeDraft(cluster);
  const u = appState.whatif.drafts.upgrade;
  const hosts = appState.hosts.filter(h => h.cluster === cluster).sort((a, b) => a.host.localeCompare(b.host));
  if (!hosts.length) { area.innerHTML = `<div class="empty-note">Cluster không có host nào.</div>`; return; }
  if (!u.selected) u.selected = new Set(hosts.map(h => h.host));
  const modeOpts = (cur, unitSet, unitAdd) => `
    <option value="keep" ${cur === 'keep' ? 'selected' : ''}>Giữ nguyên</option>
    <option value="set" ${cur === 'set' ? 'selected' : ''}>Đặt thành (${unitSet})</option>
    <option value="add" ${cur === 'add' ? 'selected' : ''}>Cộng thêm (${unitAdd})</option>
    <option value="pct" ${cur === 'pct' ? 'selected' : ''}>Tăng theo %</option>`;
  const preview = hosts.map(h => {
    const sel = u.selected.has(h.host);
    const after = sel ? applyUpgradeRule(h, u) : { cores: h.cores, ramGB: h.ramGB };
    return `<tr>
      <td><input type="checkbox" class="upgHostChk" data-host="${esc(h.host)}" ${sel ? 'checked' : ''}></td>
      <td>${uiEntityLink('host', h.host, h.vcenterKey)}</td>
      <td class="mono">${fmtInt(h.cores)}${sel && after.cores !== h.cores ? ` → <strong>${fmtInt(after.cores)}</strong>` : ''}</td>
      <td class="mono">${fmtNum(h.ramGB, 0)}${sel && after.ramGB !== h.ramGB ? ` → <strong>${fmtNum(after.ramGB, 0)}</strong>` : ''} GB</td>
      <td class="mono">${fmtInt(appState.vms.filter(v => v.cluster === cluster && v.host === h.host && v.isPoweredOn).length)}</td>
    </tr>`;
  }).join('');
  area.innerHTML = `
    ${opts.embedded ? '' : `<p class="panel-sub">Mô phỏng nâng cấp tại chỗ (thêm DIMM / đổi CPU) cho các host đang chọn trong cluster ${esc(cluster)}. Quy tắc áp dụng cho từng host được tick.</p>`}
    <div class="sim-form-grid">
      <div class="field"><label>CPU cores</label><select id="upgCpuMode">${modeOpts(u.cpuMode, 'cores/host', 'cores/host')}</select></div>
      <div class="field"><label>Giá trị CPU</label><input id="upgCpuVal" value="${esc(u.cpuVal)}" ${u.cpuMode === 'keep' ? 'disabled' : ''}></div>
      <div class="field"><label>RAM</label><select id="upgRamMode">${modeOpts(u.ramMode, 'GB/host', 'GB/host')}</select></div>
      <div class="field"><label>Giá trị RAM</label><input id="upgRamVal" value="${esc(u.ramVal)}" ${u.ramMode === 'keep' ? 'disabled' : ''}></div>
    </div>
    <div class="table-wrap" style="margin-top:.75rem;max-height:280px;overflow:auto;">
      <table class="data-table">
        <thead><tr><th><input type="checkbox" id="upgSelectAll" ${u.selected.size === hosts.length ? 'checked' : ''} title="Chọn tất cả"></th><th>Host</th><th>CPU cores (→ sau)</th><th>RAM (→ sau)</th><th>VM bật</th></tr></thead>
        <tbody>${preview}</tbody>
      </table>
    </div>
    ${opts.embedded ? '' : `<div class="sim-actions"><button class="btn btn-primary" id="btnRunUpgrade" type="button" ${u.selected.size ? '' : 'disabled'}>Chạy mô phỏng (${u.selected.size} host)</button></div>`}
  `;
  const sync = () => {
    u.cpuMode = $('#upgCpuMode').value; u.cpuVal = $('#upgCpuVal').value;
    u.ramMode = $('#upgRamMode').value; u.ramVal = $('#upgRamVal').value;
  };
  appState.whatif.drafts.syncUpgrade = sync;
  const rerender = () => { sync(); renderUpgradeHostForm(cluster, area, opts); };
  $('#upgCpuMode').onchange = rerender; $('#upgRamMode').onchange = rerender;
  $('#upgCpuVal').onchange = rerender; $('#upgRamVal').onchange = rerender;
  $$('.upgHostChk').forEach(chk => chk.onchange = () => { sync(); if (chk.checked) u.selected.add(chk.dataset.host); else u.selected.delete(chk.dataset.host); renderUpgradeHostForm(cluster, area, opts); });
  $('#upgSelectAll').onchange = (e) => { sync(); u.selected = new Set(e.target.checked ? hosts.map(h => h.host) : []); renderUpgradeHostForm(cluster, area, opts); };
  if (opts.embedded) return;
  $('#btnRunUpgrade').onclick = () => {
    sync();
    const { upgradeHosts, changed } = collectUpgradeHosts(cluster, hosts, u);
    if (!changed.length) { showValidationBanner([], ['Chưa có thay đổi cấu hình nào: chọn "Đặt thành", "Cộng thêm" hoặc "Tăng theo %" cho CPU hoặc RAM.']); return; }
    const before = baseModelForCluster(cluster);
    const after = modelWithOverrides(cluster, { upgradeHosts });
    renderSimResult(cluster, before, after, buildUpgradeExtra(cluster, hosts, upgradeHosts), `Nâng cấp host (${changed.length} host)`);
  };
}

/** Upgrade rows for the selected hosts of a cluster + the subset that actually changes. */
function collectUpgradeHosts(cluster, hosts, u) {
  const targets = hosts.filter(h => u.selected && u.selected.has(h.host));
  const upgradeHosts = targets.map(h => ({ cluster, host: h.host, ...applyUpgradeRule(h, u) }));
  const changed = upgradeHosts.filter((x, i) => x.cores !== targets[i].cores || x.ramGB !== targets[i].ramGB);
  return { upgradeHosts, changed };
}

/* ---------- Combined: upgrade existing hosts + add new hosts ---------- */
function renderUpgradeAndAddForm(cluster, area) {
  ensureUpgradeDraft(cluster);
  ensureHostRows(cluster);
  const hosts = appState.hosts.filter(h => h.cluster === cluster).sort((a, b) => a.host.localeCompare(b.host));
  if (!hosts.length) { area.innerHTML = `<div class="empty-note">Cluster không có host nào.</div>`; return; }
  area.innerHTML = `
    <p class="panel-sub">Kịch bản kết hợp cho cluster ${esc(cluster)}: nâng cấp tại chỗ các host đang tick <em>và</em> bổ sung host mới. Kết quả tính chung một lần, kể cả kiểm tra N+1.</p>
    <h5 class="sim-step-title">1 · Nâng cấp host hiện hữu (CPU, RAM)</h5>
    <div id="combUpgradeBlock"></div>
    <h5 class="sim-step-title">2 · Bổ sung host mới</h5>
    <div id="combAddBlock"></div>
    <div class="sim-actions"><button class="btn btn-primary" id="btnRunCombined" type="button">Chạy mô phỏng kết hợp</button></div>
  `;
  renderUpgradeHostForm(cluster, $('#combUpgradeBlock'), { embedded: true });
  renderAddHostForm(cluster, $('#combAddBlock'), { embedded: true });
  $('#btnRunCombined').onclick = () => {
    const d = appState.whatif.drafts;
    if (d.syncUpgrade) d.syncUpgrade();
    if (d.syncAddHostRows) d.syncAddHostRows();
    const { upgradeHosts, changed } = collectUpgradeHosts(cluster, hosts, d.upgrade);
    const addHosts = d.addHostRows
      .map(r => ({ cluster, host: r.name || 'NEW-HOST', cores: cleanNum(r.cores), ramGB: cleanNum(r.ramGB) }))
      .filter(h => isFinite(h.cores) && h.cores > 0 && isFinite(h.ramGB) && h.ramGB > 0);
    if (!changed.length && !addHosts.length) { showValidationBanner([], ['Chưa có thay đổi nào: đặt quy tắc nâng cấp cho host đang tick hoặc nhập ít nhất một host mới.']); return; }
    const before = baseModelForCluster(cluster);
    const after = modelWithOverrides(cluster, { upgradeHosts, addHosts });
    const title = `Nâng cấp + thêm host (nâng cấp ${changed.length} · thêm ${addHosts.length})`;
    renderSimResult(cluster, before, after, buildUpgradeExtra(cluster, hosts, upgradeHosts, addHosts), title);
  };
}

/* ---------- Individual sim forms ---------- */
function renderSimForm() {
  const tab = appState.whatif.tab;
  const area = $('#simFormArea');
  const cluster = currentWhatifCluster();
  if (!cluster) { area.innerHTML = `<div class="empty-note">Cần import dữ liệu trước khi mô phỏng.</div>`; return; }

  if (tab === 'addHost') {
    ensureHostRows(cluster);
    renderAddHostForm(cluster, area);
  } else if (tab === 'upgradeHost') {
    renderUpgradeHostForm(cluster, area);
  } else if (tab === 'upgradeAndAdd') {
    renderUpgradeAndAddForm(cluster, area);
  } else if (tab === 'addVm') {
    ensureVmRows(cluster);
    renderAddVmForm(cluster, area);
  } else if (tab === 'resizeVm') {
    const vmOptions = appState.vms.filter(v => v.cluster === cluster).map(v => `<option value="${esc(v.vm)}">${esc(v.vm)}</option>`).join('');
    area.innerHTML = `
      <div class="sim-form-grid">
        <div class="field"><label>Chọn VM</label><select id="simResizeVmName">${vmOptions || '<option value="">Không có VM</option>'}</select></div>
        <div class="field"><label>vCPU mới</label><input id="simResizeVcpu" value="8"></div>
        <div class="field"><label>RAM GB mới</label><input id="simResizeRam" value="32"></div>
      </div>
      <div class="sim-actions"><button class="btn btn-primary" id="btnRunResize" type="button">Chạy mô phỏng</button></div>
    `;
    $('#btnRunResize').onclick = () => {
      const name = $('#simResizeVmName').value;
      if (!name) return;
      const before = baseModelForCluster(cluster);
      const after = modelWithOverrides(cluster, { resizeVm: { cluster, name, vcpu: cleanNum($('#simResizeVcpu').value), ramGB: cleanNum($('#simResizeRam').value) } });
      renderSimResult(cluster, before, after, '', `Đổi cấu hình VM — ${esc(name)}`);
    };
  } else if (tab === 'decommission') {
    const idleVms = appState.vms.filter(v => v.cluster === cluster && ((v.isPoweredOn && isFinite(v.lastActivityDays) && v.lastActivityDays >= appState.policy.idleDays) || (!v.isPoweredOn && isFinite(v.powerOffDays) && v.powerOffDays >= appState.policy.idleDays)));
    area.innerHTML = `
      <p class="panel-sub">Tìm thấy <strong>${idleVms.length}</strong> VM idle/tắt lâu (≥ ${appState.policy.idleDays} ngày) trong cluster ${esc(cluster)}.</p>
      <div class="sim-actions"><button class="btn btn-primary" id="btnRunDecom" type="button" ${idleVms.length ? '' : 'disabled'}>Decommission toàn bộ VM idle</button></div>
    `;
    $('#btnRunDecom').onclick = () => {
      const before = baseModelForCluster(cluster);
      const after = modelWithOverrides(cluster, { removeVmNames: idleVms.map(v => v.vm) });
      renderSimResult(cluster, before, after, `<p class="panel-sub" style="margin-top:.5rem;">Giải phóng ${fmtNum(idleVms.reduce((a,v)=>a+v.diskProvGB,0),0)} GB storage provisioned.</p>`, 'Decommission Idle VM');
    };
  }
}

function runScenarioTemplate(id) {
  const cluster = currentWhatifCluster();
  if (!cluster) return;
  const before = baseModelForCluster(cluster);
  let after, title, extra = '';

  if (id === 'peakSeason') {
    const vms = appState.vms.filter(v => v.cluster === cluster && v.isPoweredOn);
    const addVms = vms.map(v => ({ ...v, vm: 'PEAK-' + v.vm, vcpu: Math.round(v.vcpu * 0.3), ramGB: v.ramGB * 0.3, diskProvGB: 0, diskUsedGB: 0 }));
    after = modelWithOverrides(cluster, { addVms });
    title = 'Kịch bản: Mùa cao điểm (+30% tải)';
  } else if (id === 'drDrill') {
    const offVms = appState.vms.filter(v => v.cluster === cluster && !v.isPoweredOn);
    const eff = getEffectiveData({});
    eff.vms = eff.vms.map(v => (v.cluster === cluster && !v.isPoweredOn) ? { ...v, isPoweredOn: true, powerState: 'Powered On' } : v);
    after = buildCapacityModel(eff.hosts.filter(h => h.cluster === cluster), eff.vms.filter(v => v.cluster === cluster));
    title = 'Kịch bản: Diễn tập DR (bật toàn bộ VM tắt)';
    extra = `<p class="panel-sub" style="margin-top:.5rem;">Kích hoạt ${offVms.length} VM đang tắt.</p>`;
  } else if (id === 'lostHost') {
    const hosts = appState.hosts.filter(h => h.cluster === cluster);
    if (!hosts.length) return;
    const biggest = [...hosts].sort((a,b) => b.cores - a.cores)[0];
    after = modelWithOverrides(cluster, { removeHostNames: [biggest.host] });
    title = `Kịch bản: Mất 1 host (${esc(biggest.host)})`;
    extra = `<p class="panel-sub" style="margin-top:.5rem;">Kiểm tra N+1: cluster có chịu được mất host lớn nhất (${fmtInt(biggest.cores)} cores, ${fmtNum(biggest.ramGB,0)} GB) không.</p>`;
  } else if (id === 'migration') {
    const rows = buildClusterRows();
    const sortedByCpu = [...rows].sort((a,b) => (b.model.cpuPct||0) - (a.model.cpuPct||0));
    const highest = sortedByCpu[0], lowest = sortedByCpu[sortedByCpu.length - 1];
    if (!highest || !lowest || highest.name === lowest.name) return;
    const vmsToMove = appState.vms.filter(v => v.cluster === highest.name);
    const moveCount = Math.max(1, Math.round(vmsToMove.length * 0.3));
    const moving = vmsToMove.slice(0, moveCount);
    const eff = getEffectiveData({});
    eff.vms = eff.vms.map(v => moving.some(m => m.vm === v.vm && m.cluster === v.cluster) ? { ...v, cluster: lowest.name } : v);
    const targetCluster = cluster === highest.name ? highest.name : (cluster === lowest.name ? lowest.name : highest.name);
    after = buildCapacityModel(eff.hosts.filter(h => h.cluster === targetCluster), eff.vms.filter(v => v.cluster === targetCluster));
    const beforeTarget = baseModelForCluster(targetCluster);
    title = `Kịch bản: di chuyển VM ${esc(highest.name)} → ${esc(lowest.name)}`;
    extra = `<p class="panel-sub" style="margin-top:.5rem;">Di chuyển ${moveCount} VM từ cluster tải cao nhất (${esc(highest.name)}) sang cluster tải thấp nhất (${esc(lowest.name)}). Kết quả hiển thị cho cluster ${esc(targetCluster)}.</p>`;
    renderSimResult(targetCluster, beforeTarget, after, extra, title);
    return;
  } else if (id === 'costOpt') {
    const P = appState.policy;
    const idleVms = appState.vms.filter(v => v.cluster === cluster && ((v.isPoweredOn && isFinite(v.lastActivityDays) && v.lastActivityDays >= P.idleDays) || (!v.isPoweredOn && isFinite(v.powerOffDays) && v.powerOffDays >= P.idleDays)));
    after = modelWithOverrides(cluster, { removeVmNames: idleVms.map(v => v.vm) });
    title = 'Kịch bản: Tối ưu chi phí (decommission idle/off)';
    extra = `<p class="panel-sub" style="margin-top:.5rem;">Loại bỏ ${idleVms.length} VM idle/tắt lâu, giải phóng ${fmtNum(idleVms.reduce((a,v)=>a+v.diskProvGB,0),0)} GB storage.</p>`;
  } else if (id === 'expansion') {
    const hosts = appState.hosts.filter(h => h.cluster === cluster);
    const avgCores = hosts.length ? hosts.reduce((a,h)=>a+h.cores,0)/hosts.length : 32;
    const avgRam = hosts.length ? hosts.reduce((a,h)=>a+h.ramGB,0)/hosts.length : 256;
    after = modelWithOverrides(cluster, { addHosts: [{ cluster, host: 'EXPANSION-HOST', cores: Math.round(avgCores), ramGB: Math.round(avgRam) }] });
    title = 'Kịch bản: Kế hoạch mở rộng (+1 host trung bình)';
    extra = `<p class="panel-sub" style="margin-top:.5rem;">Host mới: ${fmtInt(avgCores)} cores, ${fmtInt(avgRam)} GB RAM (trung bình hosts hiện có).</p>`;
  } else if (id === 'hostUpgrade') {
    const hosts = appState.hosts.filter(h => h.cluster === cluster);
    if (!hosts.length) return;
    const upgradeHosts = hosts.map(h => ({ cluster, host: h.host, cores: h.cores, ramGB: h.ramGB * 2 }));
    after = modelWithOverrides(cluster, { upgradeHosts });
    title = 'Kịch bản: Nâng cấp host (RAM ×2 toàn bộ host)';
    extra = buildUpgradeExtra(cluster, hosts, upgradeHosts);
  } else if (id === 'upgradeAndExpand') {
    const hosts = appState.hosts.filter(h => h.cluster === cluster);
    if (!hosts.length) return;
    const upgradeHosts = hosts.map(h => ({ cluster, host: h.host, cores: h.cores, ramGB: h.ramGB * 2 }));
    const maxCores = Math.max(...hosts.map(h => h.cores)), maxRam = Math.max(...upgradeHosts.map(h => h.ramGB));
    const addHosts = [{ cluster, host: 'EXPANSION-HOST', cores: maxCores, ramGB: maxRam }];
    after = modelWithOverrides(cluster, { upgradeHosts, addHosts });
    title = 'Kịch bản: Nâng cấp + mở rộng (RAM ×2 + 1 host mới)';
    extra = buildUpgradeExtra(cluster, hosts, upgradeHosts, addHosts);
  }

  renderSimResult(cluster, before, after, extra, title);
}

function renderScenarioTemplates() {
  $('#scenarioTemplates').innerHTML = SCENARIO_TEMPLATES.map(t => `
    <button class="scenario-btn" type="button" data-scenario="${t.id}">
      <div class="t">${esc(t.title)}</div>
      <div class="d">${esc(t.desc)}</div>
    </button>
  `).join('');
  $$('#scenarioTemplates [data-scenario]').forEach(btn => btn.onclick = () => runScenarioTemplate(btn.dataset.scenario));
}

function renderWhatifClusterSelect() {
  const sel = $('#whatifClusterSelect');
  const clusters = getClusterList();
  if (!clusters.length) { sel.innerHTML = `<option value="">Không có cluster</option>`; return; }
  sel.innerHTML = clusters.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
  if (!appState.whatif.cluster || !clusters.includes(appState.whatif.cluster)) appState.whatif.cluster = clusters[0];
  sel.value = appState.whatif.cluster;
}

function renderWhatifView() {
  renderScenarioTemplates();
  renderWhatifClusterSelect();
  $$('#simTabs .sim-tab').forEach(btn => btn.classList.toggle('active', btn.dataset.sim === appState.whatif.tab));
  renderSimForm();
  renderSimHistory();
}
