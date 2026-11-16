/* Sizing máy chủ — nhập file VM, dữ liệu mẫu, payload cụm hiện có / tài liệu (audit FE-28).
   Loaded right after views/sizing.js (state, params, VM table, profiles, run, view bindings). */

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

