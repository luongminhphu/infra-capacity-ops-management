/* License tabs 4–5: what-if, snapshots — moved out of licensing.js (audit FE-29). */

/* =========================================================
   4. What-if license
   ========================================================= */
function renderLicWhatif() {
  const st = licState();
  const w = st.whatif;
  const clusters = [...new Set(st.inv.hosts.map(h => `${h.vcenterKey || ''}||${h.cluster}`))].sort().map(k => { const [vc, cl] = k.split('||'); return { vc, cl }; });
  const clOpt = (id, cur) => `<select id="${id}">${clusters.map(c => `<option value="${esc(c.vc)}||${esc(c.cl)}"${cur === `${c.vc}||${c.cl}` ? ' selected' : ''}>${esc(c.cl)} (${esc(c.vc || 'import')})</option>`).join('')}</select>`;
  const f = w.form;
  let fields = '';
  if (w.type === 'add_host') fields = `<div class="field"><label>Cluster</label>${clOpt('lwCluster', f.cluster)}</div><div class="field"><label>Số host thêm</label><input id="lwCount" type="number" min="1" value="${f.count ?? 1}"></div><div class="field"><label>Physical core / host (trống = như host hiện có)</label><input id="lwCores" type="number" min="1" value="${f.cores ?? ''}"></div><div class="field"><label>Socket / host</label><input id="lwSockets" type="number" min="1" value="${f.sockets ?? ''}"></div><div class="field"><label>RAM GB / host</label><input id="lwRam" type="number" min="1" value="${f.ramGB ?? ''}"></div>`;
  else if (w.type === 'remove_host') fields = `<div class="field"><label>Cluster</label>${clOpt('lwCluster', f.cluster)}</div><div class="field"><label>Số host giảm (host nhỏ nhất trước)</label><input id="lwCount" type="number" min="1" value="${f.count ?? 1}"></div>`;
  else if (w.type === 'resize_host') fields = `<div class="field"><label>Cluster</label>${clOpt('lwCluster', f.cluster)}</div><div class="field"><label>Physical core / host mới</label><input id="lwCores" type="number" min="1" value="${f.cores ?? ''}"></div><div class="field"><label>Socket / host mới</label><input id="lwSockets" type="number" min="1" value="${f.sockets ?? ''}"></div><div class="field"><label>RAM GB / host mới</label><input id="lwRam" type="number" min="1" value="${f.ramGB ?? ''}"></div>`;
  else if (w.type === 'add_vms') fields = `<div class="field"><label>Cluster</label>${clOpt('lwCluster', f.cluster)}</div><div class="field"><label>Số VM</label><input id="lwCount" type="number" min="1" value="${f.count ?? 5}"></div><div class="field"><label>Loại OS</label><select id="lwCat">${Object.entries(LIC_CATEGORY_LABEL).map(([k, l]) => `<option value="${k}"${(f.category || 'windows_server') === k ? ' selected' : ''}>${l}</option>`).join('')}</select></div><div class="field"><label>vCPU / VM</label><input id="lwVcpu" type="number" min="1" value="${f.vcpu ?? 4}"></div><div class="field"><label>RAM GB / VM</label><input id="lwRam" type="number" min="1" value="${f.ramGB ?? 16}"></div>`;
  else if (w.type === 'move_vms') fields = `<div class="field"><label>Từ cluster</label>${clOpt('lwCluster', f.cluster)}</div><div class="field"><label>Sang cluster</label>${clOpt('lwTarget', f.target)}</div><div class="field"><label>Loại OS cần chuyển</label><select id="lwCat">${Object.entries(LIC_CATEGORY_LABEL).map(([k, l]) => `<option value="${k}"${(f.category || 'windows_server') === k ? ' selected' : ''}>${l}</option>`).join('')}</select></div><div class="field"><label>Số VM (trống = tất cả)</label><input id="lwCount" type="number" min="1" value="${f.count ?? ''}"></div>`;

  const hint = {
    add_host: 'Thêm host làm tăng core VMware và (với Standard) nhân thêm số bộ license cho cluster. Dùng để thấy chi phí license đi kèm khi mở rộng.',
    remove_host: 'Giảm host tiết kiệm core VMware/Windows Server nhưng bị chặn nếu cluster vượt ngưỡng overcommit hoặc mất khả năng N+1.',
    resize_host: 'Nâng core/socket mỗi host (thay CPU) — license per-core tăng tương ứng; thường nhiều core ít host rẻ hơn Datacenter.',
    add_vms: 'Thêm VM Windows Server trong cluster Standard làm tăng số bộ license; với Datacenter thì không đổi. Capacity được kiểm tra song song.',
    move_vms: 'Gom VM Windows Server về cluster đã license Datacenter để giảm nhu cầu Standard ở cluster nguồn — chỉ đề xuất khi cluster đích còn đủ capacity/HA.',
  }[w.type];

  return `<section class="table-card">
    <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">What-if license gắn với capacity</div><p class="panel-sub">Mỗi kịch bản được tính lại cả license (VMware per-core, Windows Server Standard/Datacenter) lẫn capacity (overcommit CPU/RAM theo chính sách, khả năng chịu lỗi N+1 của cluster). Kịch bản vi phạm capacity/HA sẽ bị đánh dấu ${uiPill('crit', 'Không đề xuất')} dù có tiết kiệm license.</p></div></div>
    <div class="sim-tabbar">${LIC_SCENARIOS.map(s => `<button class="sim-tab${w.type === s.type ? ' active' : ''}" data-lic-wtype="${s.type}" type="button">${s.label}</button>`).join('')}</div>
    <p class="muted-sub">${hint}</p>
    <div class="sim-form-grid lic-form-5">${fields}</div>
    <div class="sim-actions"><button class="btn btn-primary" data-lic-action="whatif-run" type="button"${w.busy ? ' disabled' : ''}>${w.busy ? 'Đang tính…' : 'Chạy kịch bản'}</button>${w.result ? '<button class="btn btn-secondary" data-lic-action="export-whatif" type="button">Xuất Excel</button>' : ''}</div>
    <p class="logo-upload-hint hidden" id="licWhatifErr"></p>
  </section>
  ${w.result ? renderLicWhatifResult(w.result) : ''}`;
}

function renderLicWhatifResult(r) {
  const verdict = { ok: ['ok', 'Đạt capacity/HA'], warn: ['warn', 'Vùng cảnh báo capacity'], violation: ['crit', 'Không đề xuất — vi phạm capacity/HA'] }[r.capacityVerdict] || ['na', r.capacityVerdict];
  const d = r.delta;
  const dRow = (label, x, unit = 'core', money = false) => {
    if (!x || x.before === null || x.before === undefined) return '';
    const fmt = money ? (v) => licMoney(v) : (v) => licNum(v);
    const dl = x.delta;
    const cls = dl > 0 ? 'lic-bad' : dl < 0 ? 'lic-good' : '';
    return `<tr><td>${label}</td><td class="num">${fmt(x.before)}</td><td class="num">${fmt(x.after)}</td><td class="num ${cls}"><b>${dl > 0 ? '+' : ''}${fmt(dl)}</b> ${money ? '' : unit}</td></tr>`;
  };
  const capRows = r.capacityAfter.map((a, i) => {
    const b = r.capacityBefore[i];
    const pct = (v) => isFinite(v) ? `${Math.round(v)}%` : '—';
    const ha = (c) => !c.haEligible ? uiPill('na', '1 host') : c.haSurvives ? uiPill('ok', 'N+1 đạt') : uiPill('crit', 'N+1 không đạt');
    return `<tr><td><b>${esc(a.cluster)}</b></td><td class="num">${b.hosts} → ${a.hosts}</td><td class="num">${b.vms} → ${a.vms}</td><td class="num">${pct(b.cpuPct)} → <b>${pct(a.cpuPct)}</b></td><td class="num">${pct(b.ramPct)} → <b>${pct(a.ramPct)}</b></td><td>${ha(b)} → ${ha(a)}</td></tr>`;
  }).join('');
  const reasons = r.capacityVerdicts.flatMap(v => v.reasons.map(x => `${v.cluster}: ${x}`));
  return `<section class="sim-card sim-result">
    <div class="lic-opt-head"><b>${esc(r.description)}</b> ${uiPill(verdict[0], verdict[1])}</div>
    <div class="insight-box">${r.insights.map(x => `<div>• ${esc(x)}</div>`).join('')}${reasons.length ? `<div class="lic-reasons">${reasons.map(x => `<div>⚠ ${esc(x)}</div>`).join('')}</div>` : ''}</div>
    <div class="lic-2col">
      <div><h3 class="lic-h4">License trước → sau</h3><table class="data-table lic-table"><thead><tr><th>Chỉ số</th><th class="num">Trước</th><th class="num">Sau</th><th class="num">Δ</th></tr></thead><tbody>
        ${dRow('Host', d.hosts, 'host')}${dRow('Physical core', d.physicalCores)}${dRow('VM Windows Server', d.windowsServerVms, 'VM')}
        ${dRow('VMware core nhu cầu', d.vmwareCores)}${dRow('VMware core thiếu', d.vmwareShortfall)}
        ${dRow('WS Standard core', d.wsStandardCores)}${dRow('WS Standard core (tối đa)', d.wsStandardCoresMax)}${dRow('WS Standard thiếu', d.wsStandardShortfall)}
        ${dRow('WS Datacenter core', d.wsDatacenterCores)}${dRow('WS Datacenter thiếu', d.wsDatacenterShortfall)}
        ${dRow('Chi phí VMware (toàn bộ nhu cầu)', d.vmwareCost, '', true)}${dRow('Chi phí WS Standard', d.wsStandardCost, '', true)}${dRow('Chi phí WS Datacenter', d.wsDatacenterCost, '', true)}
      </tbody></table></div>
      <div><h3 class="lic-h4">Capacity cluster bị ảnh hưởng</h3><table class="data-table lic-table"><thead><tr><th>Cluster</th><th class="num">Host</th><th class="num">VM</th><th class="num">CPU % ngưỡng</th><th class="num">RAM % ngưỡng</th><th>HA N+1</th></tr></thead><tbody>${capRows}</tbody></table>
      <p class="muted-sub">% ngưỡng = overcommit sau kịch bản / overcommit tối đa theo chính sách. &gt;80% cảnh báo, &gt;100% vi phạm.</p></div>
    </div>
  </section>`;
}

/* =========================================================
   5. Snapshot
   ========================================================= */
function renderLicSnapshots() {
  const st = licState();
  const snaps = st.snaps || [];
  const rows = snaps.map(s => {
    const sm = s.summary || {};
    const sc = s.scope || {};
    return `<tr>
      <td class="num">#${s.id}</td><td>${licDate(s.createdAt)}<div class="muted-sub">${esc(s.createdBy || '')}</div></td>
      <td><b>${esc(s.title || '')}</b>${s.note ? `<div class="muted-sub">${esc(s.note)}</div>` : ''}</td>
      <td>${esc((sc.vcenterKeys || ['Tất cả vCenter']).join(', '))}${sc.partial ? ` ${uiPill('warn', 'một phần', `Chưa tính: ${(sc.excludedClusterKeys || []).map(licKeyLabel).join(', ')}`)}` : ''}<div class="muted-sub lic-scope-cell">${sc.clusters ? esc(sc.clusters.join(', ')) : 'tất cả cluster'}</div></td>
      <td>${licPill(sm.overallStatus)}</td>
      <td class="num">${sm.vmware ? `${licNum(sm.vmware.demandCores)} / thiếu ${licNum(sm.vmware.shortfallCores)}` : '—'}</td>
      <td class="num">${sm.windowsServer ? `Std ${licRange(sm.windowsServer.standard.demandCores, sm.windowsServer.standard.demandCoresMax)} · DC ${licNum(sm.windowsServer.datacenter.demandCores)}` : '—'}</td>
      <td><code class="lic-hash" title="${esc(s.resultHash || '')}">${esc(String(s.resultHash || '').slice(0, 10))}</code><span class="muted-sub" id="licRecomp${s.id}"></span></td>
      <td class="lic-actions"><div class="lic-btns"><button class="btn btn-secondary btn-sm" data-lic-action="snap-view" data-id="${s.id}" type="button">Xem</button>${uiIsAdmin() ? ` <button class="btn btn-secondary btn-sm" data-lic-action="snap-recompute" data-id="${s.id}" type="button">Tái lập</button>` : ''} <button class="btn btn-secondary btn-sm" data-lic-action="snap-pdf" data-id="${s.id}" type="button">PDF</button>${uiIsAdmin() ? ` <button class="btn btn-secondary btn-sm btn-danger" data-lic-action="snap-delete" data-id="${s.id}" type="button">Xoá</button>` : ''}</div></td>
    </tr>`;
  }).join('');
  return `<section class="table-card">
    <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Snapshot đánh giá</div><p class="panel-sub">Mỗi snapshot lưu toàn bộ đầu vào (inventory, override, version rule và tham số, entitlement) cùng kết quả và hash. "Tái lập" chạy lại engine trên đầu vào đã lưu và so hash để chứng minh kết quả tái lập được, độc lập với inventory hiện tại.</p></div>
      ${uiIsAdmin() ? '<div class="panel-toolbar"><input id="licSnapTitle" placeholder="Tiêu đề snapshot (tuỳ chọn)"><button class="btn btn-primary btn-sm" data-lic-action="snap-save" type="button">Lưu snapshot phạm vi hiện tại</button></div>' : `<span class="muted-sub">${ADMIN_ONLY_NOTE}</span>`}</div>
    <div class="table-scroll"><table class="data-table lic-table"><thead><tr><th class="num">#</th><th>Thời điểm</th><th>Tiêu đề</th><th>Phạm vi</th><th>Kết luận</th><th class="num">VMware core</th><th class="num">Windows Server core</th><th>Hash</th><th></th></tr></thead>
    <tbody>${rows || uiEmptyRow(9, 'Chưa có snapshot.')}</tbody></table></div>
  </section>`;
}
