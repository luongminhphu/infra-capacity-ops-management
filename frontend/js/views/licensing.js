/* =========================================================
   License & Inventory — Inventory & License Assessment module.
   Sub-views: ESXi & phần cứng · Phân loại OS · License assessment ·
   What-if license · Snapshot. All math runs on the backend
   (backend/app/licensing/*); this file only renders and edits inputs
   (OS overrides, rule versions, entitlements, what-if scenarios).
   Scope follows the global vCenter / cluster filters.
   ========================================================= */

const LIC_CATEGORY_LABEL = { windows_server: 'Windows Server', windows_client: 'Windows Client', linux: 'Linux', appliance: 'Appliance', other_unknown: 'Other / Unknown' };
const LIC_CONF_LABEL = { confirmed: 'Đã xác nhận', high: 'Cao', medium: 'Trung bình', low: 'Thấp', none: 'Không có' };
const LIC_SOURCE_LABEL = { manual: 'Override thủ công', tools: 'VMware Tools', config: 'Cấu hình VM', name: 'Tên VM', none: '—' };
const LIC_STATUS = {
  ok: { cls: 'ok', label: 'Đủ license' }, shortfall: { cls: 'crit', label: 'Thiếu hụt' },
  insufficient_data: { cls: 'warn', label: 'Chưa đủ dữ liệu' }, no_entitlement: { cls: 'na', label: 'Chưa nhập entitlement' },
};
const LIC_KIND_LABEL = { owned: 'Đang sở hữu', none: 'Chưa có (chưa mua)' };
const LIC_RULE_STATUS = { needs_verification: { cls: 'warn', label: 'Chưa xác minh' }, verified: { cls: 'ok', label: 'Đã xác minh' }, retired: { cls: 'na', label: 'Ngừng dùng' } };
const LIC_SCENARIOS = [
  { type: 'add_host', label: 'Thêm host' }, { type: 'remove_host', label: 'Giảm host' }, { type: 'resize_host', label: 'Nâng cấp host' },
  { type: 'add_vms', label: 'Thêm VM' }, { type: 'move_vms', label: 'Chuyển workload' },
];

function licState() {
  if (!appState.licensing) {
    appState.licensing = {
      sub: 'hardware', inv: null, assess: null, rules: null, ents: null, snaps: null, bound: false, loading: false,
      clusters: null, scopeClusters: licLoadScopePref(),
      filters: { category: 'all', confidence: 'all', source: 'all', cluster: 'all', q: '' },
      hwOpen: {}, editing: null, entEditing: null, ruleEditing: null,
      whatif: { type: 'remove_host', form: {}, result: null, busy: false },
      viewingSnapshot: null, lastScopeKey: null,
    };
  }
  return appState.licensing;
}

const licNum = (v, d = 0) => (v === null || v === undefined || !isFinite(v)) ? '—' : Number(v).toLocaleString('vi-VN', { maximumFractionDigits: d, minimumFractionDigits: d });
const licMoney = (v, cur) => uiMoney(v, cur && cur !== 'mixed' ? cur : '', 0);
const licPill = (status) => { const s = LIC_STATUS[status] || { cls: 'na', label: status || '—' }; return uiPill(s.cls, s.label); };
const licRulePill = (status) => { const s = LIC_RULE_STATUS[status] || { cls: 'na', label: status }; return `${uiPill(s.cls, s.label)}`; };
const licDate = (iso) => { if (!iso) return '—'; const d = new Date(iso); return isNaN(d) ? esc(iso) : d.toLocaleString('vi-VN', { hour12: false }); };
const licBool = (v) => v === true ? 'Bật' : v === false ? 'Tắt' : '—';
const licRange = (a, b) => (a === b || b === null || b === undefined) ? licNum(a) : `${licNum(a)} – ${licNum(b)}`;

/* Purchase scope: a set of "vcenterKey::cluster" keys chosen in the License tab
   (persisted per browser) so a first buying round can cover only some
   production clusters. The global cluster filter, when set, narrows further. */
const LIC_SCOPE_PREF = 'vco.lic.scopeClusters';
function licLoadScopePref() { try { const v = JSON.parse(localStorage.getItem(LIC_SCOPE_PREF) || '[]'); return Array.isArray(v) ? v : []; } catch { return []; } }
function licSaveScopePref(arr) { try { localStorage.setItem(LIC_SCOPE_PREF, JSON.stringify(arr || [])); } catch { /* ignore */ } }

function licKeyLabel(k) { const [vc, cl] = String(k).split('::'); return cl ? `${cl} (${vc})` : k; }

function licScope() {
  const st = licState();
  const vc = (typeof currentVcenterFilter !== 'undefined' && currentVcenterFilter && currentVcenterFilter !== 'import') ? currentVcenterFilter : 'all';
  const cl = appState.filters?.cluster || 'all';
  // only keep selected keys that exist in the current vCenter scope
  const avail = new Set((st.clusters || []).map(c => c.key));
  // global cluster filter wins: when it is set, the chip selection is paused (not combined)
  const clusters = cl !== 'all' ? [] : (st.scopeClusters || []).filter(k => !st.clusters || avail.has(k));
  return { vcenter: vc, cluster: cl, clusters };
}

function renderLicScopeBar() {
  const st = licState();
  const bar = $('#licScopeBar');
  if (!bar) return;
  const list = st.clusters || [];
  if (!list.length) { bar.innerHTML = ''; return; }
  const sc = licScope();
  const globalCl = sc.cluster !== 'all' ? sc.cluster : null;
  const sel = new Set(sc.clusters);
  const all = sel.size === 0;
  const chips = list.map(c => {
    const on = all || sel.has(c.key);
    const dis = globalCl && c.cluster !== globalCl;
    return `<button type="button" class="lic-chip${on && !dis ? ' on' : ''}${dis ? ' disabled' : ''}" data-lic-scope="${esc(c.key)}" title="${esc(c.vcenterKey || 'import')} · ${c.hostCount} host · ${licNum(c.physicalCores)} core · ${c.vmCount} VM">${esc(c.cluster)} <span class="lic-chip-sub">${esc(c.vcenterKey || 'import')} · ${c.hostCount}H · ${licNum(c.physicalCores)}C</span></button>`;
  }).join('');
  const inScope = globalCl ? list.filter(c => c.cluster === globalCl).length : (all ? list.length : list.filter(c => sel.has(c.key)).length);
  const note = globalCl
    ? `Bộ lọc cluster toàn cục đang chọn <b>${esc(globalCl)}</b> — phạm vi license theo cluster đó. Đặt bộ lọc về "Tất cả cluster" để chọn nhiều cluster ở đây.`
    : (all ? `Đang tính cho toàn bộ ${list.length} cluster. Bấm vào cluster để giới hạn phạm vi mua license (ví dụ chỉ production trước).`
           : `Phạm vi mua đợt này: <b>${inScope}/${list.length} cluster</b> — ${list.length - inScope} cluster còn lại chưa tính license; báo cáo và snapshot ghi rõ phạm vi này.`);
  // One line by default (the 6+ chips used to fill two rows above the result); <details> opens the picker.
  const summary = globalCl ? esc(globalCl) : all ? `Tất cả ${list.length} cluster` : `${inScope} / ${list.length} cluster`;
  const wasOpen = !!(bar.querySelector('details') || {}).open;
  bar.innerHTML = `<details class="lic-scope"${wasOpen || (!all && !globalCl) ? ' open' : ''}>
    <summary><span class="lic-scope-label">Phạm vi cluster</span><b>${summary}</b><span class="lic-scope-hint">Đổi</span></summary>
    <div class="lic-scope-body"><button type="button" class="lic-chip${all && !globalCl ? ' on' : ''}${globalCl ? ' disabled' : ''}" data-lic-scope="__all__">Tất cả</button>${chips}
    <p class="lic-scope-note${!all && !globalCl ? ' partial' : ''}">${note}</p></div></details>`;
}

function licSetStatus(msg, isError = false) { uiSetStatus('#licStatus', msg, isError ? 'error' : '', { hideWhenEmpty: true }); }

/* ---------- Data loading ---------- */
async function licLoad(force = false) {
  const st = licState();
  const vc0 = licScope().vcenter;
  if (force || !st.clusters || st.clustersVc !== vc0) {
    try { st.clusters = await api.licClusters(vc0); st.clustersVc = vc0; } catch (e) { st.clusters = st.clusters || []; }
  }
  const sc = licScope();
  const key = `${sc.vcenter}|${sc.cluster}|${sc.clusters.join(',')}`;
  renderLicScopeBar();
  if (!force && st.inv && st.lastScopeKey === key) return;
  st.loading = true;
  licSetStatus('Đang tải inventory và tính toán license…');
  try {
    // ESXi / Hệ điều hành belong to Tài sản: with the License module off only the inventory is loaded.
    const licOn = featureOn('license');
    const [inv, assess, rules, ents, snaps] = await Promise.all([
      api.licInventory(sc.vcenter, sc.cluster, sc.clusters), licOn ? api.licAssess(sc.vcenter, sc.cluster, sc.clusters) : null,
      licOn ? api.licRules() : [], licOn ? api.licEntitlements() : { items: [] }, licOn ? api.licSnapshots() : [],
    ]);
    st.inv = inv; st.assess = assess; st.rules = rules; st.ents = ents.items; st.entMeta = ents; st.snaps = snaps;
    st.lastScopeKey = key; st.viewingSnapshot = null;
    licSetStatus('');
  } catch (e) {
    licSetStatus(`Không tải được dữ liệu license: ${e.message}`, true);
  } finally {
    st.loading = false;
  }
  const pill = $('#licScopePill');
  if (pill) {
    const n = sc.clusters.length;
    pill.textContent = `${sc.vcenter === 'all' ? 'Tất cả vCenter' : sc.vcenter}${sc.cluster !== 'all' ? ' · ' + sc.cluster : (n ? ` · ${n}/${(st.clusters || []).length} cluster` : ' · tất cả cluster')}`;
    pill.className = `pill ${sc.cluster !== 'all' || n ? 'warn' : 'na'}`;
  }
}

/* ---------- Binding ---------- */
function bindLicensingView() {
  const st = licState();
  if (st.bound) return;
  st.bound = true;
  // Sub-pages (hardware / os / assess / whatif / snapshots) are chosen from the
  // sidebar; navigateTo() sets st.sub before calling renderLicensingView().
  $('#btnLicRefresh').addEventListener('click', async () => { await licLoad(true); renderLicBody(); });
  $('#licScopeBar').addEventListener('click', async (e) => {
    const chip = e.target.closest('[data-lic-scope]');
    if (!chip || chip.classList.contains('disabled')) return;
    const k = chip.dataset.licScope;
    const all = (st.clusters || []).map(c => c.key);
    if (k === '__all__') st.scopeClusters = [];
    else {
      let cur = st.scopeClusters.length ? st.scopeClusters.filter(x => all.includes(x)) : all.slice();
      cur = cur.includes(k) ? cur.filter(x => x !== k) : [...cur, k];
      if (!cur.length) { licSetStatus('Cần ít nhất một cluster trong phạm vi.', true); return; }
      st.scopeClusters = cur.length === all.length ? [] : cur;
    }
    licSaveScopePref(st.scopeClusters);
    st.whatif.result = null;
    await licLoad(false); renderLicBody();
  });
  // Delegated actions inside the body
  $('#licBody').addEventListener('click', onLicBodyClick);
  // category cells act as toggle buttons: Enter / Space like a click
  $('#licBody').addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[data-lic-cat]')) { e.preventDefault(); onLicBodyClick(e); } });
  $('#licBody').addEventListener('change', onLicBodyChange);
  $('#licBody').addEventListener('input', (e) => {
    if (e.target.id === 'licOsSearch') { st.filters.q = e.target.value; renderLicOsTable(); }
  });
}

async function renderLicensingView() {
  bindLicensingView();
  await licLoad(false);
  renderLicBody();
}
window.renderLicensingView = renderLicensingView;

function renderLicBody() {
  const st = licState();
  const body = $('#licBody');
  const needsAssess = !['hardware', 'os'].includes(st.sub);
  if (!st.inv || (needsAssess && !st.assess)) { body.innerHTML = `<div class="empty-note">${st.loading ? 'Đang tải dữ liệu…' : 'Chưa có dữ liệu inventory. Hãy đồng bộ vCenter hoặc import dữ liệu trước.'}</div>`; return; }
  if (st.sub === 'hardware') body.innerHTML = renderLicHardware();
  else if (st.sub === 'os') { body.innerHTML = renderLicOs(); renderLicOsTable(); if (typeof aiGate === 'function') aiGate(body); }
  else if (st.sub === 'assess') body.innerHTML = renderLicAssess();
  else if (st.sub === 'whatif') body.innerHTML = renderLicWhatif();
  else if (st.sub === 'snapshots') body.innerHTML = renderLicSnapshots();
}

/* =========================================================
   Actions
   ========================================================= */
function onLicBodyChange(e) {
  const st = licState();
  const f = e.target.dataset.licFilter;
  if (f) { st.filters[f] = e.target.value; renderLicOsTable(); return; }
  if (e.target.dataset.licEntkind) {
    const none = e.target.value === 'none';
    st.entKindDraft = e.target.value;
    if (st.entEditing) st.entEditing = { ...st.entEditing, kind: e.target.value };
    const qty = $('#licEntQty'); if (qty) { qty.disabled = none; if (none) qty.value = 0; }
    const lbl = qty?.closest('.field')?.querySelector('label'); if (lbl) lbl.textContent = none ? 'Số lượng (chưa có = 0)' : 'Số lượng';
  }
}

async function onLicBodyClick(e) {
  const st = licState();
  const wtype = e.target.closest('[data-lic-wtype]');
  if (wtype) { st.whatif.type = wtype.dataset.licWtype; st.whatif.result = null; renderLicBody(); return; }
  const catCard = e.target.closest('[data-lic-cat]');
  if (catCard) { st.filters.category = st.filters.category === catCard.dataset.licCat ? 'all' : catCard.dataset.licCat; renderLicBody(); return; }
  const tog = e.target.closest('[data-hw-toggle]');
  if (tog) { const k = tog.dataset.hwToggle; st.hwOpen[k] = !st.hwOpen[k]; renderLicBody(); return; }
  const btn = e.target.closest('[data-lic-action]');
  if (!btn) return;
  const act = btn.dataset.licAction;
  const id = btn.dataset.id ? Number(btn.dataset.id) : null;
  try {
    if (act === 'hw-expand') { st.inv.hosts.forEach(h => { st.hwOpen[`${h.vcenterKey || 'import'}||${h.cluster || '—'}`] = true; }); renderLicBody(); }
    else if (act === 'hw-collapse') { st.hwOpen = {}; renderLicBody(); }
    else if (act === 'export-hw') await licExportHardware();
    else if (act === 'export-os') await licExportOs();
    else if (act === 'export-assess') await licExportAssess();
    else if (act === 'export-pdf') {
      const sc = licScope();
      licSetStatus('Đang tạo PDF executive summary…');
      await api.licReportPdf(sc.vcenter, sc.cluster, sc.clusters, st.viewingSnapshot ? st.viewingSnapshot.id : null);
      licSetStatus('Đã xuất PDF executive summary.');
    }
    else if (act === 'snap-pdf') { licSetStatus(`Đang tạo PDF từ snapshot #${id}…`); await api.licReportPdf('all', null, [], id); licSetStatus(`Đã xuất PDF snapshot #${id}.`); }
    else if (act === 'export-whatif') await licExportWhatif();
    else if (act === 'override') { const vm = st.inv.vms[Number(btn.dataset.idx)]; st.overrides = await api.licOverrides(); st.editing = vm; renderLicOverrideEditor(vm); }
    else if (act === 'override-cancel') { st.editing = null; renderLicOverrideEditor(null); }
    else if (act === 'override-save') {
      const vm = st.editing;
      await api.licOverrideSave({ vcenterKey: vm.vcenterKey || null, instanceUuid: vm.instanceUuid || null, vm: vm.vm, category: $('#licOvCat').value, osVersion: $('#licOvVer').value.trim() || null, osEdition: $('#licOvEd').value.trim() || null, note: $('#licOvNote').value.trim() || null });
      st.editing = null; await licLoad(true); renderLicBody(); licSetStatus(`Đã lưu override cho ${vm.vm}.`);
    }
    else if (act === 'override-delete') { await api.licOverrideDelete(id); st.editing = null; await licLoad(true); renderLicBody(); licSetStatus('Đã xoá override.'); }
    else if (act === 'ent-edit') { st.entEditing = st.ents.find(x => x.id === id); renderLicBody(); $('#licEntProduct')?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    else if (act === 'ent-cancel') { st.entEditing = null; st.entKindDraft = null; renderLicBody(); }
    else if (act === 'ent-save') {
      const kind = $('#licEntKind')?.value || 'owned';
      const payload = { product: $('#licEntProduct').value, kind, metric: $('#licEntMetric').value, quantity: kind === 'none' ? 0 : Number($('#licEntQty').value || 0), unitPrice: $('#licEntPrice').value === '' ? null : Number($('#licEntPrice').value), currency: $('#licEntCur').value.trim() || 'VND', hasSa: $('#licEntSa').value === '1', scopeVcenter: $('#licEntVc').value || null, scopeCluster: $('#licEntCl').value || null, validTo: $('#licEntValid').value || null, note: $('#licEntNote').value.trim() || null };
      if (kind === 'owned' && !(payload.quantity > 0)) { licSetStatus('Số lượng entitlement phải lớn hơn 0 — hoặc chọn tình trạng "Chưa có" nếu chưa mua license.', true); return; }
      if (st.entEditing?.id) await api.licEntitlementUpdate(st.entEditing.id, payload); else await api.licEntitlementCreate(payload);
      st.entEditing = null; st.entKindDraft = null; await licLoad(true); renderLicBody(); licSetStatus(kind === 'none' ? 'Đã ghi nhận "Chưa có license" và tính lại assessment.' : 'Đã lưu entitlement và tính lại assessment.');
    }
    else if (act === 'ent-delete') { if (!await uiConfirm('Xoá entitlement này?')) return; await api.licEntitlementDelete(id); await licLoad(true); renderLicBody(); }
    else if (act === 'rule-edit') { st.ruleEditing = st.rules.find(r => r.id === id); renderLicBody(); $('#licRuleParams')?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    else if (act === 'rule-cancel') { st.ruleEditing = null; renderLicBody(); }
    else if (act === 'rule-save') {
      let params;
      try { params = JSON.parse($('#licRuleParams').value); } catch (err) { const el = $('#licRuleErr'); el.classList.remove('hidden'); el.style.color = 'var(--status-bad)'; el.textContent = `JSON không hợp lệ: ${err.message}`; return; }
      await api.licRuleVersion(id, { params, name: $('#licRuleName').value.trim() || null, sourceUrl: $('#licRuleUrl').value.trim() || null, sourceNote: $('#licRuleNote').value.trim() || null, activate: btn.dataset.activate === '1' });
      st.ruleEditing = null; await licLoad(true); renderLicBody(); licSetStatus('Đã tạo version rule mới.');
    }
    else if (act === 'rule-activate') { await api.licRuleActivate(id); await licLoad(true); renderLicBody(); }
    else if (act === 'rule-deactivate') { if (!await uiConfirm('Tắt rule này? Assessment sẽ báo thiếu rule cho tới khi kích hoạt version khác.')) return; await api.licRuleDeactivate(id); await licLoad(true); renderLicBody(); }
    else if (act === 'rule-verify') {
      const note = await uiPrompt('Ghi chú xác minh (tài liệu đã đối chiếu, ngày, người):', `Đối chiếu tài liệu nguồn ngày ${new Date().toLocaleDateString('vi-VN')}`, { okLabel: 'Xác minh' });
      if (note === null) return;
      await api.licRuleVerify(id, note); await licLoad(true); renderLicBody(); licSetStatus('Đã ghi nhận xác minh rule.');
    }
    else if (act === 'whatif-run') await licRunWhatif();
    else if (act === 'snap-save') {
      const sc = licScope();
      const title = $('#licSnapTitle')?.value.trim() || null;
      const s = await api.licSnapshotCreate({ vcenter: sc.vcenter, cluster: sc.cluster !== 'all' ? sc.cluster : null, clusters: sc.clusters.length ? sc.clusters : null, title });
      st.snaps = await api.licSnapshots(); licSetStatus(`Đã lưu snapshot #${s.id} (hash ${s.resultHash}).`);
      if (st.sub === 'snapshots') renderLicBody();
    }
    else if (act === 'snap-view') { const full = await api.licSnapshotGet(id); st.viewingSnapshot = full; st.sub = 'assess'; if (typeof syncNavState === 'function') syncNavState(); renderLicBody(); }
    else if (act === 'snap-close') { st.viewingSnapshot = null; renderLicBody(); }
    else if (act === 'snap-recompute') { const r = await api.licSnapshotRecompute(id); const el = $(`#licRecomp${id}`); if (el) el.innerHTML = r.reproducible ? ' ' + uiPill('ok', 'tái lập đúng') : ` ${uiPill('crit', `khác: ${r.recomputedHash}`)}`; }
    else if (act === 'snap-delete') { if (!await uiConfirm(`Xoá snapshot #${id}?`)) return; await api.licSnapshotDelete(id); st.snaps = await api.licSnapshots(); renderLicBody(); }
  } catch (err) {
    licSetStatus(`Thao tác thất bại: ${err.message}`, true);
  }
}

async function licRunWhatif() {
  const st = licState();
  const w = st.whatif;
  const err = $('#licWhatifErr');
  const split = (v) => { const [vc, cl] = String(v || '').split('||'); return { vc: vc || null, cl }; };
  const num = (id) => { const el = $(`#${id}`); if (!el || el.value === '') return null; const n = Number(el.value); return isFinite(n) ? n : null; };
  const src = split($('#lwCluster')?.value);
  const scenario = { type: w.type, vcenterKey: src.vc, cluster: src.cl };
  w.form = { cluster: $('#lwCluster')?.value, target: $('#lwTarget')?.value, count: num('lwCount'), cores: num('lwCores'), sockets: num('lwSockets'), ramGB: num('lwRam'), vcpu: num('lwVcpu'), category: $('#lwCat')?.value };
  if (w.form.count) scenario.count = w.form.count;
  if (w.form.cores) scenario.cores = w.form.cores;
  if (w.form.sockets) scenario.sockets = w.form.sockets;
  if (w.form.ramGB) scenario.ramGB = w.form.ramGB;
  if (w.form.vcpu) scenario.vcpu = w.form.vcpu;
  if (w.form.category) scenario.category = w.form.category;
  if (w.type === 'move_vms') {
    const dst = split($('#lwTarget')?.value);
    scenario.sourceCluster = src.cl; scenario.targetCluster = dst.cl; scenario.targetVcenterKey = dst.vc;
    if (dst.cl === src.cl && dst.vc === src.vc) { err.classList.remove('hidden'); err.style.color = 'var(--status-bad)'; err.textContent = 'Cluster đích phải khác cluster nguồn.'; return; }
  }
  if (w.type === 'resize_host' && !scenario.cores && !scenario.sockets && !scenario.ramGB) { err.classList.remove('hidden'); err.style.color = 'var(--status-bad)'; err.textContent = 'Nhập ít nhất một thông số mới (core, socket hoặc RAM).'; return; }
  w.busy = true; renderLicBody();
  try {
    const sc = licScope();
    w.result = await api.licWhatif({ vcenter: sc.vcenter, cluster: sc.cluster !== 'all' ? sc.cluster : null, clusters: sc.clusters.length ? sc.clusters : null, scenario });
  } catch (e) {
    w.result = null;
    renderLicBody();
    const el = $('#licWhatifErr'); if (el) { el.classList.remove('hidden'); el.style.color = 'var(--status-bad)'; el.textContent = `Không chạy được kịch bản: ${e.message}`; }
    w.busy = false; return;
  }
  w.busy = false; renderLicBody();
}

/* ---------- Exports (generic server-side xlsx) ---------- */
function licScopeSlug() { const sc = licScope(); return `${sc.vcenter}${sc.cluster !== 'all' ? '_' + sc.cluster : (sc.clusters.length ? '_' + sc.clusters.map(k => k.split('::')[1]).slice(0, 3).join('_') : '')}`.replace(/[^A-Za-z0-9_-]+/g, '_'); }

async function licExportHardware() {
  const st = licState();
  const cols = [['vcenterKey', 'vCenter'], ['cluster', 'Cluster'], ['host', 'Host'], ['vendor', 'Vendor'], ['model', 'Model'], ['esxiVersion', 'ESXi version'], ['esxiBuild', 'ESXi build'], ['cpuModel', 'CPU model'], ['sockets', 'CPU socket'], ['cores', 'Physical core'], ['threads', 'Logical thread'], ['ramGB', 'RAM GB'], ['vmCount', 'Số VM'], ['clusterHa', 'HA'], ['clusterDrs', 'DRS'], ['wsLicensableCores', 'WS licensable core'], ['vmwareCores', 'VMware core'], ['flags', 'Giả định']].map(([key, label]) => ({ key, label }));
  const rows = st.inv.hosts.map(h => ({ ...h, clusterHa: licBool(h.clusterHa), clusterDrs: licBool(h.clusterDrs), flags: (h.flags || []).join(', ') }));
  await api.downloadXlsx({ filename: `ICO_ESXi_inventory_${licScopeSlug()}`, sheets: [{ name: 'ESXi hosts', columns: cols, rows }] });
}

async function licExportOs() {
  const rows = licFilteredVms().map(v => ({ vcenterKey: v.vcenterKey, cluster: v.cluster, host: v.host, vm: v.vm, ipAddress: v.ipAddress || '', hostname: v.os.evidence?.guestHostname || '', power: v.isPoweredOn ? 'Bật' : 'Tắt', vcpu: v.vcpu, ramGB: v.ramGB, category: v.os.categoryLabel, osVersion: v.os.osVersion || '', osEdition: v.os.osEdition || '', source: LIC_SOURCE_LABEL[v.os.source] || v.os.source, confidence: LIC_CONF_LABEL[v.os.confidence] || v.os.confidence, guestId: v.os.evidence?.guestId || '', guestFullName: v.os.evidence?.guestFullName || '', toolsGuestFullName: v.os.evidence?.toolsGuestFullName || '', toolsStatus: v.os.evidence?.toolsStatus || '', note: v.os.note || '' }));
  const cols = [['vcenterKey', 'vCenter'], ['cluster', 'Cluster'], ['host', 'Host'], ['vm', 'VM'], ['ipAddress', 'IP address'], ['hostname', 'Hostname (guest)'], ['power', 'Trạng thái'], ['vcpu', 'vCPU'], ['ramGB', 'RAM GB'], ['category', 'Phân loại'], ['osVersion', 'OS version'], ['osEdition', 'Edition'], ['source', 'Nguồn'], ['confidence', 'Độ tin cậy'], ['guestId', 'guestId'], ['guestFullName', 'Cấu hình VM'], ['toolsGuestFullName', 'VMware Tools'], ['toolsStatus', 'Tools status'], ['note', 'Ghi chú']].map(([key, label]) => ({ key, label }));
  await api.downloadXlsx({ filename: `ICO_VM_OS_inventory_${licScopeSlug()}`, sheets: [{ name: 'VM OS', columns: cols, rows }] });
}

async function licExportAssess() {
  const st = licState();
  const a = st.viewingSnapshot ? st.viewingSnapshot.result : st.assess;
  const ws = a.windowsServer, vm = a.vmware;
  const summary = [
    { k: 'Kết luận chung', v: LIC_STATUS[a.overallStatus]?.label || a.overallStatus },
    { k: 'Phạm vi', v: `${(a.scope.vcenterKeys || ['Tất cả vCenter']).join(', ')}${a.scope.clusters ? ' · ' + a.scope.clusters.join(', ') : ' · tất cả cluster'}` },
    ...(a.scope.partial ? [{ k: 'Cluster ngoài phạm vi (chưa tính)', v: (a.scope.excludedClusterKeys || []).map(k => k.split('::')[1]).join(', ') }] : []),
    { k: 'Thời điểm tính', v: a.generatedAt || '' }, { k: 'Hash kết quả', v: a.resultHash || '' },
    { k: 'Rule Windows Server', v: a.rules.windows_server ? `${a.rules.windows_server.name} v${a.rules.windows_server.version} (${a.rules.windows_server.status})` : '—' },
    { k: 'Rule VMware', v: a.rules.vmware ? `${a.rules.vmware.name} v${a.rules.vmware.version} (${a.rules.vmware.status})` : '—' },
    { k: 'VMware core nhu cầu', v: vm.demandCores }, { k: 'VMware core entitlement', v: vm.entitledCores }, { k: 'VMware core thiếu', v: vm.shortfallCores },
    { k: 'WS Standard core nhu cầu', v: `${ws.standard.demandCores}${ws.standard.demandCoresMax !== ws.standard.demandCores ? ' – ' + ws.standard.demandCoresMax : ''}` }, { k: 'WS Standard entitlement', v: ws.standard.entitledCores }, { k: 'WS Standard thiếu', v: ws.standard.shortfallCores },
    { k: 'WS Datacenter core nhu cầu', v: ws.datacenter.demandCores }, { k: 'WS Datacenter entitlement', v: ws.datacenter.entitledCores }, { k: 'WS Datacenter thiếu', v: ws.datacenter.shortfallCores },
    ...(ws.standard.cost ? [{ k: 'Chi phí bù thiếu Standard', v: ws.standard.cost.shortfallCost }] : []), ...(ws.datacenter.cost ? [{ k: 'Chi phí bù thiếu Datacenter', v: ws.datacenter.cost.shortfallCost }] : []), ...(vm.cost ? [{ k: 'Chi phí bù thiếu VMware', v: vm.cost.shortfallCost }] : []),
    ...ws.formula.map((x, i) => ({ k: `Công thức WS ${i + 1}`, v: x })), ...vm.formula.map((x, i) => ({ k: `Công thức VMware ${i + 1}`, v: x })),
    ...ws.assumptions.map((x, i) => ({ k: `Giả định WS ${i + 1}`, v: x })), ...vm.assumptions.map((x, i) => ({ k: `Giả định VMware ${i + 1}`, v: x })),
    ...[...ws.dataGaps, ...vm.dataGaps].map((x, i) => ({ k: `Thiếu dữ liệu ${i + 1}`, v: x })),
  ];
  const clusters = a.clusters.map(c => ({ vcenterKey: c.vcenterKey, cluster: c.cluster, hostCount: c.hostCount, sockets: c.sockets, physicalCores: c.physicalCores, wsLicensableCores: c.wsLicensableCores, ha: licBool(c.ha), drs: licBool(c.drs), wsVms: c.wsVms, wsVmsLowConfidence: c.wsVmsLowConfidence, unknownVms: c.unknownVms, stdSets: c.standard.sets, stdCores: c.standard.cores, stdCoresMax: c.standard.coresMax, dcCores: c.datacenter.cores, vmwareCores: c.vmwareCores, recommended: c.recommendedEdition || '' }));
  const ccols = [['vcenterKey', 'vCenter'], ['cluster', 'Cluster'], ['hostCount', 'Host'], ['sockets', 'Socket'], ['physicalCores', 'Physical core'], ['wsLicensableCores', 'WS licensable core'], ['ha', 'HA'], ['drs', 'DRS'], ['wsVms', 'VM Windows Server'], ['wsVmsLowConfidence', 'WS confidence thấp'], ['unknownVms', 'VM chưa rõ'], ['stdSets', 'Bộ Standard/host'], ['stdCores', 'WS Standard core'], ['stdCoresMax', 'WS Standard core tối đa'], ['dcCores', 'WS Datacenter core'], ['vmwareCores', 'VMware core'], ['recommended', 'Edition ít core hơn']].map(([key, label]) => ({ key, label }));
  const hcols = [['vcenterKey', 'vCenter'], ['cluster', 'Cluster'], ['host', 'Host'], ['model', 'Model'], ['esxiVersion', 'ESXi'], ['sockets', 'Socket'], ['cores', 'Physical core'], ['threads', 'Logical thread'], ['wsLicensableCores', 'WS licensable core'], ['vmwareCores', 'VMware core'], ['wsVmsOnHost', 'WS VM trên host'], ['vmCount', 'VM']].map(([key, label]) => ({ key, label }));
  const ents = (a.entitlementsUsed || []).map(e => ({ product: e.label || e.product, kind: LIC_KIND_LABEL[e.kind || 'owned'], metric: e.metric, quantity: e.quantity, unitPrice: e.unitPrice, currency: e.currency, hasSa: licBool(e.hasSa), scope: `${e.scopeVcenter || 'Tất cả'} / ${e.scopeCluster || 'Tất cả'}`, note: e.note || '' }));
  const ecols = [['product', 'Sản phẩm'], ['kind', 'Tình trạng'], ['metric', 'Đơn vị'], ['quantity', 'Số lượng'], ['unitPrice', 'Đơn giá'], ['currency', 'Tiền tệ'], ['hasSa', 'SA'], ['scope', 'Phạm vi'], ['note', 'Ghi chú']].map(([key, label]) => ({ key, label }));
  await api.downloadXlsx({ filename: `ICO_License_assessment_${licScopeSlug()}${st.viewingSnapshot ? '_snapshot' + st.viewingSnapshot.id : ''}`, sheets: [
    { name: 'Tóm tắt', columns: [{ key: 'k', label: 'Chỉ tiêu' }, { key: 'v', label: 'Giá trị' }], rows: summary },
    { name: 'Theo cluster', columns: ccols, rows: clusters }, { name: 'Theo host', columns: hcols, rows: a.hosts }, { name: 'Entitlement', columns: ecols, rows: ents },
  ] });
}

async function licExportWhatif() {
  const r = licState().whatif.result; if (!r) return;
  const rows = Object.entries(r.delta).filter(([, x]) => x && x.before !== null && x.before !== undefined).map(([k, x]) => ({ k, before: x.before, after: x.after, delta: x.delta }));
  const cap = r.capacityAfter.map((a, i) => ({ cluster: a.cluster, hostsBefore: r.capacityBefore[i].hosts, hostsAfter: a.hosts, cpuBefore: r.capacityBefore[i].cpuPct, cpuAfter: a.cpuPct, ramBefore: r.capacityBefore[i].ramPct, ramAfter: a.ramPct, haBefore: r.capacityBefore[i].haSurvives, haAfter: a.haSurvives }));
  await api.downloadXlsx({ filename: `ICO_License_whatif_${licScopeSlug()}`, sheets: [
    { name: 'Kịch bản', columns: [{ key: 'k', label: 'Mục' }, { key: 'v', label: 'Giá trị' }], rows: [{ k: 'Mô tả', v: r.description }, { k: 'Kết luận capacity', v: r.capacityVerdict }, { k: 'Đề xuất được', v: r.recommendable ? 'Có' : 'Không' }, ...r.insights.map((x, i) => ({ k: `Insight ${i + 1}`, v: x }))] },
    { name: 'License delta', columns: [{ key: 'k', label: 'Chỉ số' }, { key: 'before', label: 'Trước' }, { key: 'after', label: 'Sau' }, { key: 'delta', label: 'Δ' }], rows },
    { name: 'Capacity', columns: [{ key: 'cluster', label: 'Cluster' }, { key: 'hostsBefore', label: 'Host trước' }, { key: 'hostsAfter', label: 'Host sau' }, { key: 'cpuBefore', label: 'CPU % trước' }, { key: 'cpuAfter', label: 'CPU % sau' }, { key: 'ramBefore', label: 'RAM % trước' }, { key: 'ramAfter', label: 'RAM % sau' }, { key: 'haBefore', label: 'N+1 trước' }, { key: 'haAfter', label: 'N+1 sau' }], rows: cap },
  ] });
}
