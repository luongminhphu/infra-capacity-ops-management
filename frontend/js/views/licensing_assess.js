/* License tab 3: assessment, entitlements, rules — moved out of licensing.js (audit FE-29). */

/* =========================================================
   3. License assessment
   ========================================================= */
function licVerdictText(a, ov) {
  const vm = a.vmware, ws = a.windowsServer;
  const gaps = [];
  if (vm.shortfallCores > 0) gaps.push(`VMware thiếu ${licNum(vm.shortfallCores)} core`);
  if (ws.standard.shortfallCores > 0) gaps.push(`Windows Server Standard thiếu ${licRange(ws.standard.shortfallCores, ws.standard.shortfallCoresMax)} core`);
  if (a.overallStatus === 'no_entitlement') return 'Chưa nhập quyền sử dụng (entitlement), nên chưa kết luận được đủ hay thiếu license.';
  if (a.overallStatus === 'insufficient_data') return 'Chưa đủ dữ liệu để kết luận; bổ sung hệ điều hành của VM chưa rõ hoặc entitlement.';
  if (gaps.length) return `${ov.label}: ${gaps.join('; ')}.`;
  return 'Đủ license cho phạm vi đang xem.';
}

function renderLicAssess() {
  const st = licState();
  const a = st.viewingSnapshot ? st.viewingSnapshot.result : st.assess;
  const ws = a.windowsServer, vm = a.vmware;
  const snapBanner = st.viewingSnapshot ? `<div class="insight-box lic-snap-banner"><strong>Đang xem snapshot #${st.viewingSnapshot.id}</strong> — ${esc(st.viewingSnapshot.title || '')} · lưu lúc ${licDate(st.viewingSnapshot.createdAt)} bởi ${esc(st.viewingSnapshot.createdBy || '—')} · hash ${esc(st.viewingSnapshot.resultHash || '')}. <button class="btn btn-secondary btn-sm" data-lic-action="snap-close" type="button">Quay về dữ liệu hiện tại</button></div>` : '';
  const ruleWarn = [a.rules.windows_server, a.rules.vmware, a.rules.vmware_cpu_legacy].filter(Boolean).filter(r => r.status !== 'verified');
  const scp = a.scope || {};
  const scopeBanner = scp.partial ? `<div class="insight-box"><strong>Phạm vi mua đợt này:</strong> ${a.clusters.length} cluster (${esc(a.clusters.map(c => `${c.cluster} (${c.vcenterKey})`).join(', '))}). Chưa tính ${scp.excludedClusterKeys.length} cluster: ${esc(scp.excludedClusterKeys.map(licKeyLabel).join(', '))}.</div>` : '';

  const wsCard = (opt, title) => {
    const c = opt.cost;
    return `<div class="sim-card lic-opt-card">
      <div class="lic-opt-head"><b>${title}</b> ${licPill(opt.status)}</div>
      <table class="lic-kv">
        <tr><td>Nhu cầu core</td><td class="num"><b>${licRange(opt.demandCores, opt.demandCoresMax)}</b></td></tr>
        <tr><td>Pack 16 + pack 2</td><td class="num">${opt.packs.pack16 || 0} × 16 + ${opt.packs.pack2 || 0} × 2${opt.demandCoresMax !== opt.demandCores ? ` <span class="muted-sub">(tối đa ${opt.packsMax.pack16} × 16 + ${opt.packsMax.pack2} × 2)</span>` : ''}</td></tr>
        <tr><td>Chỉ pack 16 core</td><td class="num">${licRange(opt.packs.pack16Only, opt.packsMax.pack16Only)} pack</td></tr>
        <tr><td>Entitlement hiện có</td><td class="num">${opt.entitlement.declaredNone && !(opt.entitledCores > 0) ? uiPill('crit', 'Chưa có (đã xác nhận)') : `${licNum(opt.entitledCores)} core${opt.entitlement.hasSa ? ' <span class="tag">SA</span>' : ''}`}</td></tr>
        <tr><td>Thiếu hụt</td><td class="num"><b class="${opt.shortfallCores > 0 ? 'lic-bad' : ''}">${licRange(opt.shortfallCores, opt.shortfallCoresMax)} core</b> → ${opt.shortfallPacks.pack16} × 16 + ${opt.shortfallPacks.pack2} × 2</td></tr>
        ${c ? `<tr><td>Đơn giá / core</td><td class="num">${licMoney(c.pricePerCore, c.currency)}</td></tr>
        <tr><td>Chi phí toàn bộ nhu cầu</td><td class="num">${licMoney(c.demandCost, c.currency)}${c.demandCostMax !== c.demandCost ? ` <span class="muted-sub">– ${licMoney(c.demandCostMax, c.currency)}</span>` : ''}</td></tr>
        <tr><td>Chi phí bù thiếu hụt</td><td class="num"><b>${licMoney(c.shortfallCost, c.currency)}</b>${c.shortfallCostMax !== c.shortfallCost ? ` <span class="muted-sub">– ${licMoney(c.shortfallCostMax, c.currency)}</span>` : ''}</td></tr>` : '<tr><td>Chi phí</td><td class="num muted-sub">Chưa có đơn giá</td></tr>'}
      </table></div>`;
  };
  const rec = ws.recommendedEdition || {};
  const recText = rec.byCores ? `Theo số core: <b>${rec.byCores === 'datacenter' ? 'Datacenter' : 'Standard'}</b> cần ít core hơn.` + (rec.byCost ? ` Theo chi phí: <b>${rec.byCost === 'datacenter' ? 'Datacenter' : 'Standard'}</b> rẻ hơn ${licMoney(rec.saving, rec.currency)}.` : ` ${esc(rec.note || '')}`) : 'Không có VM Windows Server trong phạm vi.';
  const vmwCost = vm.cost;
  const legacy = vm.legacy;

  const clusterRows = a.clusters.map(c => `<tr>
    <td><b>${esc(c.cluster)}</b><div class="muted-sub">${esc(c.vcenterKey || 'import')}</div></td>
    <td class="num">${c.hostCount}</td><td class="num">${c.sockets}</td><td class="num">${licNum(c.physicalCores)}</td><td class="num">${licNum(c.wsLicensableCores)}</td>
    <td>${c.ha ? '<span class="tag">HA</span>' : ''}${c.drs ? ' <span class="tag">DRS</span>' : ''}${!c.ha && !c.drs ? '—' : ''}</td>
    <td class="num">${c.wsVms}${c.wsVmsLowConfidence ? ` ${uiPill('warn', `${c.wsVmsLowConfidence} ?`, 'VM chỉ có bằng chứng cấu hình/tên')}` : ''}</td>
    <td class="num">${c.unknownVms || 0}</td>
    <td class="num">${c.standard.sets !== null && c.standard.sets !== undefined ? `${c.standard.sets} bộ → ` : ''}<b>${licRange(c.standard.cores, c.standard.coresMax)}</b></td>
    <td class="num"><b>${licNum(c.datacenter.cores)}</b></td>
    <td class="num"><b>${licNum(c.vmwareCores)}</b>${legacy ? `<div class="muted-sub">${c.vmwareLegacyCpuLicenses} CPU lic.</div>` : ''}</td>
    <td>${c.recommendedEdition ? `<span class="tag">${c.recommendedEdition === 'datacenter' ? 'Datacenter' : 'Standard'}</span>` : '—'}</td>
  </tr>`).join('');

  const list = (arr) => arr && arr.length ? `<ul class="lic-list">${arr.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '<div class="muted-sub">—</div>';

  const ov = LIC_STATUS[a.overallStatus] || { cls: 'na', label: a.overallStatus };
  const pill = (stt) => { const m = LIC_STATUS[stt] || { cls: 'na', label: stt }; return { cls: m.cls, text: m.label }; };
  return `${snapBanner}
    <section class="verdict tone-rule-${esc(ov.cls)}" aria-label="Kết luận license">
      <div class="verdict-meta">${licPill(a.overallStatus)}<span>${esc(a.totals.hosts)} host · ${licNum(a.totals.physicalCores)} core vật lý · ${esc(a.totals.vms)} VM · ${esc(a.totals.windowsServerVms)} Windows Server${a.totals.unknownVms ? ` · ${esc(a.totals.unknownVms)} chưa rõ HĐH` : ''}</span></div>
      <h2>${esc(licVerdictText(a, ov))}</h2>
    </section>
    ${scopeBanner}
    <div class="kpi-strip-wrap">${uiKpiStrip([
      { label: 'VMware per-core', valueHtml: `${licNum(vm.demandCores)}<small>core</small>`, subHtml: `Có ${licNum(vm.entitledCores)} · thiếu <b>${licNum(vm.shortfallCores)}</b>`, status: pill(vm.status), pct: null },
      { label: 'Windows Server Standard', valueHtml: `${licRange(ws.standard.demandCores, ws.standard.demandCoresMax)}<small>core</small>`, subHtml: `Có ${licNum(ws.standard.entitledCores)} · thiếu <b>${licRange(ws.standard.shortfallCores, ws.standard.shortfallCoresMax)}</b>`, status: pill(ws.standard.status), pct: null },
      { label: 'Windows Server Datacenter', valueHtml: `${licNum(ws.datacenter.demandCores)}<small>core</small>`, subHtml: `Có ${licNum(ws.datacenter.entitledCores)} · thiếu <b>${licNum(ws.datacenter.shortfallCores)}</b>`, status: pill(ws.datacenter.status), pct: null },
    ])}</div>
    ${ruleWarn.length ? `<p class="note-line">${uiPill('warn', 'Rule chưa xác minh')} ${ruleWarn.map(r => `${esc(r.name)} v${esc(r.version)}`).join('; ')} — kết quả để tham khảo cho tới khi rule được đối chiếu tài liệu nguồn và ghi người / ngày xác minh (mục Rule licensing bên dưới).</p>` : ''}

    <section class="table-card">
      <div class="panel-head">
        <div><div class="panel-title" role="heading" aria-level="2">Nhu cầu license theo cluster</div><p class="panel-sub">Windows Server được tính theo cluster vì HA/DRS/vMotion cho phép mọi VM chạy trên mọi host; không đếm theo host hiện tại và không chỉ đếm số VM Windows. Chế độ đếm OSE Standard: <b>${ws.peakMode === 'current_placement' ? 'theo host hiện tại (tham chiếu)' : 'toàn cluster'}</b>.</p></div>
        <div class="panel-toolbar">
          ${st.viewingSnapshot || !uiIsAdmin() ? '' : '<button class="btn btn-primary btn-sm" data-lic-action="snap-save" type="button">Lưu snapshot</button>'}
          <button class="btn btn-secondary btn-sm" data-lic-action="export-pdf" type="button" title="Executive summary: kết luận, quyết định đề nghị, tổng quan license theo cluster">Xuất PDF · Executive summary</button>
          <button class="btn btn-secondary btn-sm" data-lic-action="export-assess" type="button">Xuất Excel</button>
        </div>
      </div>
      <div class="table-scroll"><table class="data-table lic-table">
        <thead><tr><th>Cluster</th><th class="num">Host</th><th class="num">Socket</th><th class="num">Physical core</th><th class="num">Core licensable (WS)</th><th>HA / DRS</th><th class="num">VM Windows Server</th><th class="num">VM chưa rõ</th><th class="num">WS Standard core</th><th class="num">WS Datacenter core</th><th class="num">VMware core</th><th>Edition ít core hơn</th></tr></thead>
        <tbody>${clusterRows || uiEmptyRow(12, 'Không có cluster.')}</tbody>
      </table></div>
    </section>

    <div class="lic-3col">
      ${wsCard(ws.standard, 'Windows Server Standard')}
      ${wsCard(ws.datacenter, 'Windows Server Datacenter')}
      <div class="sim-card lic-opt-card">
        <div class="lic-opt-head"><b>VMware (VCF / VVF / vSphere, per-core)</b> ${licPill(vm.status)}</div>
        <table class="lic-kv">
          <tr><td>Nhu cầu core</td><td class="num"><b>${licNum(vm.demandCores)}</b></td></tr>
          <tr><td>Entitlement hiện có</td><td class="num">${vm.declaredNone && !(vm.entitledCores > 0) ? uiPill('crit', 'Chưa có (đã xác nhận)') : `${licNum(vm.entitledCores)} core`}${Object.values(vm.entitlements).filter(e => e.units > 0).map(e => `<div class="muted-sub">${esc(e.label)}: ${licNum(e.units)}</div>`).join('')}</td></tr>
          <tr><td>Thiếu hụt</td><td class="num"><b class="${vm.shortfallCores > 0 ? 'lic-bad' : ''}">${licNum(vm.shortfallCores)} core</b>${vm.orderCores !== vm.shortfallCores ? ` <span class="muted-sub">(đặt hàng tối thiểu ${licNum(vm.orderCores)})</span>` : ''}</td></tr>
          ${vmwCost ? `<tr><td>Đơn giá / core (${esc(vmwCost.basis)})</td><td class="num">${licMoney(vmwCost.pricePerCore, vmwCost.currency)}</td></tr>
          <tr><td>Chi phí toàn bộ nhu cầu</td><td class="num">${licMoney(vmwCost.demandCost, vmwCost.currency)}</td></tr>
          <tr><td>Chi phí bù thiếu hụt</td><td class="num"><b>${licMoney(vmwCost.shortfallCost, vmwCost.currency)}</b></td></tr>` : '<tr><td>Chi phí</td><td class="num muted-sub">Chưa có đơn giá</td></tr>'}
          ${legacy ? `<tr><td colspan="2" class="lic-sep">Per-CPU perpetual (legacy)</td></tr><tr><td>Số license CPU cần</td><td class="num">${licNum(legacy.demandCpuLicenses)}</td></tr><tr><td>Có / thiếu</td><td class="num">${licNum(legacy.entitled)} / <b>${licNum(legacy.shortfall)}</b> ${licPill(legacy.status)}</td></tr>` : ''}
        </table></div>
    </div>

    <div class="insight-box"><strong>So sánh edition Windows Server:</strong> ${recText}</div>

    <div class="lic-3col">
      <div class="sim-card"><h3 class="lic-h4">Công thức — Windows Server</h3>${list(ws.formula)}<h3 class="lic-h4">Giả định</h3>${list(ws.assumptions)}</div>
      <div class="sim-card"><h3 class="lic-h4">Công thức — VMware</h3>${list(vm.formula)}<h3 class="lic-h4">Giả định</h3>${list(vm.assumptions)}</div>
      <div class="sim-card lic-gap-card"><h3 class="lic-h4">Thiếu dữ liệu / cần xác nhận</h3>${list([...(ws.dataGaps || []), ...(vm.dataGaps || [])])}</div>
    </div>

    ${st.viewingSnapshot ? '' : renderLicEntitlements() + renderLicRules()}`;
}

/* ---------- Entitlements ---------- */
function renderLicEntitlements() {
  const st = licState();
  const items = st.ents || [];
  const products = st.entMeta?.products || {};
  const metrics = st.entMeta?.metrics || {};
  const clusters = [...new Set(st.inv.hosts.map(h => h.cluster).filter(Boolean))].sort();
  const vcs = [...new Set(st.inv.hosts.map(h => h.vcenterKey).filter(Boolean))].sort();
  const e = st.entEditing || {};
  const kinds = st.entMeta?.kinds || LIC_KIND_LABEL;
  const isNone = (e.id ? (e.kind || 'owned') : (st.entKindDraft || 'owned')) === 'none';
  const admin = uiIsAdmin();
  const form = !admin ? `<p class="muted-sub">${ADMIN_ONLY_NOTE}</p>` : `<div class="planning-panel lic-editor">
    <div class="planning-panel-title">${e.id ? `Sửa entitlement #${e.id}` : 'Thêm entitlement / báo giá'}</div>
    <div class="sim-form-grid lic-form-6">
      <div class="field lic-span-2"><label>Sản phẩm</label><select id="licEntProduct">${Object.entries(products).map(([k, l]) => `<option value="${k}"${(e.product || 'ws_dc') === k ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></div>
      <div class="field"><label>Tình trạng</label><select id="licEntKind" data-lic-entkind="1">${Object.entries(kinds).map(([k, l]) => `<option value="${k}"${(isNone ? 'none' : 'owned') === k ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></div>
      <div class="field"><label>Đơn vị</label><select id="licEntMetric">${Object.entries(metrics).map(([k, l]) => `<option value="${k}"${(e.metric || 'pack16') === k ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></div>
      <div class="field"><label${isNone ? ' title="Chưa có quyền sử dụng: số lượng = 0"' : ''}>Số lượng</label><input id="licEntQty" type="number" min="0" step="1" value="${isNone ? 0 : (e.quantity ?? '')}"${isNone ? ' disabled' : ''}></div>
      <div class="field"><label>${isNone ? 'Báo giá / đơn vị' : 'Đơn giá / đơn vị'}</label><input id="licEntPrice" type="number" min="0" step="1" value="${e.unitPrice ?? ''}" placeholder="Tuỳ chọn"></div>
      <div class="field"><label>Tiền tệ</label><input id="licEntCur" value="${esc(e.currency || 'VND')}"></div>
      <div class="field"><label>SA / Subscription</label><select id="licEntSa"><option value="0"${!e.hasSa ? ' selected' : ''}>Không</option><option value="1"${e.hasSa ? ' selected' : ''}>Có</option></select></div>
      <div class="field"><label>Phạm vi vCenter</label><select id="licEntVc"><option value="">Tất cả</option>${vcs.map(v => `<option value="${esc(v)}"${e.scopeVcenter === v ? ' selected' : ''}>${esc(v)}</option>`).join('')}</select></div>
      <div class="field"><label>Phạm vi cluster</label><select id="licEntCl"><option value="">Tất cả</option>${clusters.map(c => `<option value="${esc(c)}"${e.scopeCluster === c ? ' selected' : ''}>${esc(c)}</option>`).join('')}</select></div>
      <div class="field"><label title="Tuỳ chọn">Hết hạn</label><input id="licEntValid" type="date" value="${e.validTo ? String(e.validTo).slice(0, 10) : ''}"></div>
      <div class="field"><label>Ghi chú (hợp đồng, PO…)</label><input id="licEntNote" value="${esc(e.note || '')}"></div>
    </div>
    <div class="sim-actions">
      <button class="btn btn-primary btn-sm" data-lic-action="ent-save" type="button">${e.id ? 'Cập nhật' : 'Thêm'}</button>
      ${e.id ? '<button class="btn btn-secondary btn-sm" data-lic-action="ent-cancel" type="button">Huỷ</button>' : ''}
    </div></div>`;
  const rows = items.map(x => `<tr>
    <td><b>${esc(x.label)}</b>${x.kind === 'none' ? ' ' + uiPill('crit', 'Chưa có') : ''}${x.hasSa && x.kind !== 'none' ? ' <span class="tag">SA</span>' : ''}</td><td>${esc(metrics[x.metric] || x.metric)}</td><td class="num">${x.kind === 'none' ? '0' : licNum(x.quantity)}</td>
    <td class="num">${x.unitPrice != null ? licMoney(x.unitPrice, x.currency) : '—'}</td>
    <td>${esc(x.scopeVcenter || 'Tất cả')} / ${esc(x.scopeCluster || 'Tất cả')}</td><td>${x.validTo ? licDate(x.validTo).split(' ')[0] : '—'}</td><td class="lic-note">${esc(x.note || '')}</td>
    <td>${admin ? `<button class="btn btn-secondary btn-sm" data-lic-action="ent-edit" data-id="${x.id}" type="button">Sửa</button> <button class="btn btn-secondary btn-sm btn-danger" data-lic-action="ent-delete" data-id="${x.id}" type="button">Xoá</button>` : ''}</td>
  </tr>`).join('');
  return `<section class="table-card">
    <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Entitlement hiện có &amp; đơn giá</div><p class="panel-sub">Nhập license đang sở hữu, hoặc chọn tình trạng <b>Chưa có</b> để xác nhận chưa mua license sản phẩm đó (toàn bộ nhu cầu được tính là thiếu hụt; có thể kèm báo giá để ước chi phí). Số lượng pack được quy đổi sang core; đơn giá là giá mỗi đơn vị đã chọn (pack/core/CPU). Không có giá mặc định — thiếu giá thì không hiển thị chi phí.</p></div></div>
    ${form}
    <div class="table-scroll"><table class="data-table lic-table"><thead><tr><th>Sản phẩm</th><th>Đơn vị</th><th class="num">Số lượng</th><th class="num">Đơn giá</th><th>Phạm vi</th><th>Hết hạn</th><th>Ghi chú</th><th></th></tr></thead>
    <tbody>${rows || uiEmptyRow(8, 'Chưa có entitlement nào.')}</tbody></table></div>
  </section>`;
}

/* ---------- Rules ---------- */
/* A rule's source link is shown only for http(s) URLs (never javascript: / data:); the API validates too. */
const licSafeUrl = (u) => typeof u === 'string' && /^https?:\/\//i.test(u.trim());

function renderLicRules() {
  const st = licState();
  const rules = st.rules || [];
  const ed = st.ruleEditing;
  const admin = uiIsAdmin();
  const rows = rules.map(r => `<tr class="${r.isActive ? 'lic-rule-active' : ''}">
    <td><b>${esc(r.name)}</b><div class="muted-sub">${esc(r.product)} · ${esc(r.key)}</div></td>
    <td class="num">v${r.version}</td>
    <td>${licRulePill(r.status)}${r.isActive ? ' ' + uiPill('ok', 'đang dùng') : ''}</td>
    <td>${licSafeUrl(r.sourceUrl) ? `<a href="${esc(r.sourceUrl)}" target="_blank" rel="noopener noreferrer">Nguồn</a>` : '—'}<div class="muted-sub lic-note" title="${esc(r.sourceNote || '')}">${esc((r.sourceNote || '').split('\n')[0]).slice(0, 140)}</div></td>
    <td>${r.verifiedAt ? `${licDate(r.verifiedAt)}<div class="muted-sub">${esc(r.verifiedBy || '')}</div>` : '<span class="muted-sub">chưa</span>'}</td>
    <td class="lic-actions">
      <button class="btn btn-secondary btn-sm" data-lic-action="rule-edit" data-id="${r.id}" type="button">${admin ? 'Xem / tạo version' : 'Xem'}</button>
      ${r.status !== 'verified' && uiIsAdmin() ? `<button class="btn btn-secondary btn-sm" data-lic-action="rule-verify" data-id="${r.id}" type="button">Xác minh</button>` : ''}
      ${!admin ? '' : r.isActive ? `<button class="btn btn-secondary btn-sm" data-lic-action="rule-deactivate" data-id="${r.id}" type="button">Tắt</button>` : `<button class="btn btn-secondary btn-sm" data-lic-action="rule-activate" data-id="${r.id}" type="button">Kích hoạt</button>`}
    </td></tr>`).join('');
  const editor = ed ? `<div class="planning-panel lic-editor">
    <div class="planning-panel-title">Rule ${esc(ed.name)} v${ed.version} — tạo version mới (rule không sửa đè để assessment cũ tái lập được)</div>
    <div class="sim-form-grid lic-form-2">
      <div class="field"><label>Tên</label><input id="licRuleName" value="${esc(ed.name)}"></div>
      <div class="field"><label>URL nguồn</label><input id="licRuleUrl" value="${esc(ed.sourceUrl || '')}"></div>
    </div>
    <div class="field"><label>Tham số (JSON)</label><textarea id="licRuleParams" rows="10" class="lic-json">${esc(JSON.stringify(ed.params, null, 2))}</textarea></div>
    <div class="field"><label>Ghi chú nguồn / lý do thay đổi</label><input id="licRuleNote" value="${esc(ed.sourceNote || '')}"></div>
    <div class="sim-actions">
      ${admin ? `<button class="btn btn-primary btn-sm" data-lic-action="rule-save" data-id="${ed.id}" data-activate="1" type="button">Lưu version mới &amp; kích hoạt</button>
      <button class="btn btn-secondary btn-sm" data-lic-action="rule-save" data-id="${ed.id}" type="button">Chỉ lưu version mới</button>` : `<span class="muted-sub">${ADMIN_ONLY_NOTE}</span>`}
      <button class="btn btn-secondary btn-sm" data-lic-action="rule-cancel" type="button">Đóng</button>
    </div>
    <p class="logo-upload-hint hidden" id="licRuleErr"></p></div>` : '';
  return `<section class="table-card">
    <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Rule licensing (có version, nguồn, ngày xác minh)</div><p class="panel-sub">Engine chỉ đọc tham số từ rule đang kích hoạt. Rule mặc định lấy từ tài liệu công khai của Microsoft/Broadcom và ở trạng thái "Chưa xác minh" cho tới khi người có trách nhiệm đối chiếu và bấm Xác minh. Tham số ví dụ: min_cores_per_host, standard_peak_mode (cluster_total | current_placement), include_powered_off, min_cores_per_cpu, min_cores_per_order.</p></div></div>
    ${editor}
    <div class="table-scroll"><table class="data-table lic-table"><thead><tr><th>Rule</th><th class="num">Version</th><th>Trạng thái</th><th>Nguồn</th><th>Xác minh</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
  </section>`;
}
