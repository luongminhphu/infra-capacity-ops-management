/* =========================================================
   Trung tâm báo cáo — every PDF / Excel / CSV export of the app,
   grouped by function (Capacity & vận hành · Hạ tầng & phần cứng ·
   Hệ thống & Owner · Quy hoạch & Sizing · License & Inventory · Backup ·
   Chi phí & ngân sách).
   - Each report has its own options ("Tuỳ chọn"), remembered per
     browser; scope (cluster / host) and finding types come from the
     side panel, the logo applies to every PDF.
   - "Chọn nhiều": pick several outputs and export them one after
     another.
   Exports reuse the functions of each page so a report is identical
   wherever it is exported from.
   ========================================================= */

const RPT_GROUP_PREF = 'ico.reports.group';
const RPT_OPTS_PREF = 'ico.reports.opts';
const rptState = { batch: false, picked: new Set(), opts: {} };

function rptSetStatus(msg, isErr) { uiSetStatus('#rptStatus', msg, isErr ? 'error' : '', { hideWhenEmpty: true, scroll: true }); }

/* ---------- options (data-opt="report.field"), remembered in localStorage ---------- */
function rptLoadOpts() {
  try { rptState.opts = JSON.parse(localStorage.getItem(RPT_OPTS_PREF) || '{}') || {}; } catch { rptState.opts = {}; }
}
function rptSaveOpts() { try { localStorage.setItem(RPT_OPTS_PREF, JSON.stringify(rptState.opts)); } catch { /* ignore */ } }

function rptOpt(key, fallback) {
  const el = document.querySelector(`#viewReports [data-opt="${key}"]`);
  if (el) return el.type === 'checkbox' ? el.checked : el.value;
  return key in rptState.opts ? rptState.opts[key] : fallback;
}

function rptApplyOpts() {
  $$('#viewReports [data-opt]').forEach(el => {
    const v = rptState.opts[el.dataset.opt];
    if (v === undefined) return;
    if (el.type === 'checkbox') el.checked = !!v;
    else if (el.tagName === 'SELECT' && ![...el.options].some(o => o.value === String(v))) return;
    else el.value = v;
  });
}

/* Planning defaults come from the Quy hoạch page form until the user changes them here. */
function rptPlanSync(force) {
  const map = { 'plan.horizon': '#planHorizon', 'plan.growth': '#planGrowth', 'plan.lead': '#planLead', 'plan.target': '#planTarget',
                'plan.nodeCores': '#planNodeCores', 'plan.nodeRam': '#planNodeRam' };
  Object.entries(map).forEach(([k, sel]) => {
    const src = $(sel), dst = document.querySelector(`#viewReports [data-opt="${k}"]`);
    if (src && dst && (force || !(k in rptState.opts))) dst.value = src.value;
  });
}

function rptApplyGroup(group) {
  $$('#rptFilter [data-rpt-group]').forEach(b => b.classList.toggle('active', b.dataset.rptGroup === group));
  $$('#viewReports .rpt-group').forEach(sec => sec.classList.toggle('hidden', group !== 'all' && sec.dataset.rptGroup !== group));
  try { localStorage.setItem(RPT_GROUP_PREF, group); } catch { /* ignore */ }
}

/* ---------- state lines (Sizing data, NetBackup servers, license snapshots) ---------- */
function rptEnsureSizing() {
  if (typeof sizingState !== 'function') return null;
  const st = sizingState();
  // Sizing exports read the Sizing page inputs: render it once (hidden) so they hold the saved values.
  if (!st.bound && typeof renderSizingView === 'function') renderSizingView();
  return st;
}

function rptSizingVmCount() {
  if (typeof sizingState !== 'function') return 0;
  return (sizingState().vms || []).filter(v => Number(v.qty) > 0 && (Number(v.vcpu) > 0 || Number(v.ramGB) > 0)).length;
}

function rptRenderState() {
  const el = $('#rptSizingState');
  if (el) {
    const n = rptSizingVmCount();
    const st = typeof sizingState === 'function' ? sizingState() : null;
    const env = st && st.params && st.params.environment ? ` · ${st.params.environment}` : '';
    el.innerHTML = n ? `${uiPill('ok', 'Sẵn sàng')} ${n} dòng VM${esc(env)}${st && st.result ? ' · đã tính' : ' · báo cáo tự tính khi xuất'}`
      : uiPill('na', 'Chưa có dữ liệu') + ' Mở Sizing để nhập danh sách VM.';
    $$('#viewReports [data-report^="sizing-"]').forEach(b => { if (b.dataset.report !== 'sizing-template') b.disabled = !n; });
  }
  const sel = $('#rptBkServer');
  if (sel && typeof bkState === 'function') {
    const servers = bkState().servers || [];
    const cur = rptOpt('bk.server', 'all');
    sel.innerHTML = `<option value="all">Tất cả máy chủ backup (${servers.length})</option>`
      + servers.map(s => `<option value="${esc(s.key)}">${esc(s.name)}${s.demo ? ' · demo' : ''}</option>`).join('');
    sel.value = servers.some(s => s.key === cur) ? cur : 'all';
    $$('#viewReports [data-report^="bk-"]').forEach(b => { b.disabled = !servers.length; b.title = servers.length ? '' : 'Chưa cấu hình máy chủ backup'; });
  }
  rptRenderBatch();
}

async function rptLoadLicSnapshots() {
  const sel = $('#rptLicSnapshot');
  if (!sel || sel.dataset.loaded || typeof api.licSnapshots !== 'function') return;
  sel.dataset.loaded = '1';
  try {
    const snaps = await api.licSnapshots();
    const list = Array.isArray(snaps) ? snaps : (snaps.items || snaps.rows || []);
    sel.innerHTML = '<option value="">Hiện tại (theo phạm vi đang chọn)</option>' + list.map(x => {
      const d = x.createdAt ? new Date(x.createdAt).toLocaleString('vi-VN', { hour12: false }) : '';
      return `<option value="${x.id}">Snapshot #${x.id}${x.title ? ' — ' + esc(x.title) : ''}${d ? ' · ' + d : ''}</option>`;
    }).join('');
    const saved = rptState.opts['lic.snapshot'];
    if (saved && [...sel.options].some(o => o.value === String(saved))) sel.value = saved;
  } catch { sel.dataset.loaded = ''; }
}

/* Review campaigns (owner admin only: /api/reviews answers 403 to others — the card is hidden then). */
async function rptLoadReviews() {
  const sel = $('#rptReviewCampaign'), card = $('#rptReviewCard');
  if (!sel || sel.dataset.loaded) return;
  const can = !!(currentUser && (currentUser.isAdmin || currentUser.ownerAdmin));
  card.classList.toggle('hidden', !can);
  if (!can) return;
  sel.dataset.loaded = '1';
  try {
    const items = (await apiRequest('/reviews')).items || [];
    sel.innerHTML = items.length ? items.map(c => `<option value="${esc(c.id)}">Kỳ ${esc(c.periodLabel)} · ${c.status === 'closed' ? 'đã đóng' : 'đang mở'}${c.dryRun ? ' · chạy thử' : ''}</option>`).join('')
      : '<option value="">Chưa có kỳ rà soát</option>';
    const saved = rptState.opts['rv.campaign'];
    if (saved && items.some(c => String(c.id) === String(saved))) sel.value = saved;
    $$('#viewReports [data-report^="rv-"]').forEach(b => { b.disabled = !items.length; });
  } catch { sel.dataset.loaded = ''; card.classList.add('hidden'); }
}

async function renderReportsView() {
  let group = 'all';
  try { group = localStorage.getItem(RPT_GROUP_PREF) || 'all'; } catch { /* ignore */ }
  rptApplyGroup(group);
  rptApplyOpts();
  rptPlanSync(false);
  if (typeof bkState === 'function' && !bkState().servers && typeof bkLoadServers === 'function') await bkLoadServers();
  rptLoadReviews();
  rptRenderState();
}

/* ---------- export actions ---------- */
function rptVcenter() { return currentVcenterFilter === 'import' ? '__none__' : currentVcenterFilter; }

function rptStorage(key) {
  const v = rptOpt(key, 'setting');
  return v === 'setting' ? !!appState.policy.includeStorage : v === '1';
}

async function rptCapacityPdf(view, prefix, extra) {
  await api.downloadPdfReport(view, rptVcenter(), getSelectedPdfFindingTypes(), getSelectedPdfClusters(), getSelectedPdfHosts(),
    rptStorage(`${prefix}.storage`), Number(rptOpt(`${prefix}.months`, 12)) || 12, extra || null);
}

function rptPlanningParams() {
  const growth = String(rptOpt('plan.growth', '5,10,20')).split(',').map(x => parseFloat(x.trim())).filter(n => isFinite(n) && n >= 0);
  if (!growth.length) throw new Error('Tăng trưởng %/quý không hợp lệ (ví dụ: 5,10,20).');
  const num = (k) => { const v = parseFloat(rptOpt(k, '')); return isFinite(v) ? v : null; };
  return { horizon: rptOpt('plan.horizon', '36'), growth: growth.join(','), node_cores: num('plan.nodeCores'), node_ram: num('plan.nodeRam'),
           lead: num('plan.lead') ?? 3, target: num('plan.target') ?? 80 };
}

async function rptSizingPdf(btn, detail) {
  if (!rptSizingVmCount()) throw new Error('Chưa có danh sách VM cho Sizing — mở trang Sizing máy chủ để nhập.');
  rptEnsureSizing();
  const r = document.querySelector(`input[name="szPdfDetail"][value="${detail}"]`); if (r) r.checked = true;
  const m = document.querySelector(`input[name="szPdfMode"][value="${rptOpt('sizing.mode', 'new')}"]`); if (m) m.checked = true;
  await exportSizingPdf(btn);
}

/* Capacity scope of the side panel (vCenter bar + cluster / host picks) as query parameters; the owner filter is added by uiDownload. */
function rptScopeQuery() {
  const p = new URLSearchParams();
  const vc = rptVcenter();
  if (vc && vc !== 'all') p.set('vcenter', vc);
  const cl = getSelectedPdfClusters(), hs = getSelectedPdfHosts();
  if (Array.isArray(cl) && cl.length) p.set('clusters', cl.join(','));
  if (Array.isArray(hs) && hs.length) p.set('hosts', hs.join(','));
  return p;
}

function rptGet(path, params, name) {
  const q = params && params.toString();
  return uiDownload(`${API_BASE}${path}${q ? `?${q}` : ''}`, { method: 'GET' }, name);
}

function rptHwQuery() {
  const p = new URLSearchParams();
  if (rptOpt('hw.status', '')) p.set('status', rptOpt('hw.status', ''));
  if (rptOpt('hw.warranty', '')) p.set('warrantyDays', rptOpt('hw.warranty', ''));
  return p;
}

function rptReview(fmt) {
  const id = rptOpt('rv.campaign', '');
  if (!id) throw new Error('Chưa có kỳ rà soát owner nào.');
  return rptGet(`/reviews/${encodeURIComponent(id)}/report.${fmt}`, null, `ICO_Ra_soat_owner.${fmt}`);
}

/* Nhu cầu hạ tầng (G8): same endpoint as the Tổng kết năm / Kế hoạch năm sau tabs; the year from the card option. */
function rptAnnual(fmt) {
  const y = rptOpt('annual.year', '');
  return rptGet(`/itsm/annual/report.${fmt}`, y ? new URLSearchParams({ year: y }) : null, `ICO_Nhu_cau_ha_tang.${fmt}`);
}

async function rptLicense(run, ready) {
  await licLoad(false);
  if (!ready(licState())) throw new Error('Chưa tải được dữ liệu license / inventory (cần đồng bộ vCenter trước).');
  await run();
}

const RPT_ACTIONS = {
  'cap-exec': { label: 'Báo cáo Vận hành (PDF)', run: () => rptCapacityPdf('executive', 'exec') },
  'cap-tech': { label: 'Báo cáo Kỹ thuật (PDF)', run: () => rptCapacityPdf('technical', 'tech') },
  'inv-hosts': { label: 'Danh sách host (Excel)', run: (btn) => exportInventoryXlsx('hosts', btn) },
  'inv-vms': { label: 'Danh sách VM (Excel)', run: (btn) => exportInventoryXlsx('vms', btn) },
  'cap-csv': { label: 'Dữ liệu tổng hợp (CSV)', run: () => exportCsv() },
  'plan-pdf': { label: 'Quy hoạch tài nguyên (PDF)', run: () => rptCapacityPdf('planning', 'plan', rptPlanningParams()) },
  'sizing-full': { label: 'Sizing — PDF đầy đủ', run: (btn) => rptSizingPdf(btn, 'full') },
  'sizing-brief': { label: 'Sizing — PDF rút gọn', run: (btn) => rptSizingPdf(btn, 'brief') },
  'sizing-xlsx': { label: 'Sizing — Excel', run: async (btn) => { rptEnsureSizing(); await exportSizingXlsx(btn); } },
  'sizing-template': { label: 'Mẫu nhập liệu Sizing', run: () => api.downloadSizingTemplate() },
  'lic-pdf': { label: 'Tổng quan license (PDF)', run: async () => {
    const snap = rptOpt('lic.snapshot', '');
    if (snap) return api.licReportPdf('all', null, [], Number(snap));
    await licLoad(false);
    const sc = licScope();
    return api.licReportPdf(sc.vcenter, sc.cluster, sc.clusters, null);
  } },
  'lic-assess': { label: 'Đánh giá license (Excel)', run: () => rptLicense(() => licExportAssess(), st => st.assess || st.viewingSnapshot) },
  'lic-hw': { label: 'ESXi & phần cứng (Excel)', run: () => rptLicense(() => licExportHardware(), st => st.inv) },
  'lic-os': { label: 'Hệ điều hành guest (Excel)', run: () => rptLicense(() => licExportOs(), st => st.inv) },
  'bk-pdf': { label: 'Hiện trạng Backup (PDF)', run: () => api.bkReportPdf(rptOpt('bk.server', 'all'), Number(rptOpt('bk.days', 7)) || 7, !!rptOpt('bk.appendix', true)) },
  'bk-xlsx': { label: 'Thống kê backup (Excel)', run: () => api.bkReportXlsx(rptOpt('bk.server', 'all'), Number(rptOpt('bk.days', 7)) || 7) },
  'infra-pdf': { label: 'Sức khoẻ hạ tầng (PDF)', run: () => rptGet('/infra/report.pdf', rptScopeQuery(), 'suc-khoe-ha-tang.pdf') },
  'infra-xlsx': { label: 'Sức khoẻ hạ tầng (Excel)', run: () => rptGet('/infra/report.xlsx', rptScopeQuery(), 'suc-khoe-ha-tang.xlsx') },
  'hw-pdf': { label: 'Phần cứng máy chủ (PDF)', run: () => rptGet('/hardware/report.pdf', rptHwQuery(), 'ICO_Suc_khoe_phan_cung.pdf') },
  'hw-xlsx': { label: 'Phần cứng máy chủ (Excel)', run: () => rptGet('/hardware/report.xlsx', rptHwQuery(), 'ICO_Suc_khoe_phan_cung.xlsx') },
  'ci-xlsx': { label: 'Sổ CI (Excel)', run: () => {
    const p = new URLSearchParams({ status: rptOpt('ci.status', 'active') });
    if (rptOpt('ci.type', '')) p.set('type', rptOpt('ci.type', ''));
    return rptGet('/ci/export.xlsx', p, 'so-ci.xlsx');
  } },
  'sys-pdf': { label: 'VM theo hệ thống (PDF)', run: () => rptGet('/systems/report.pdf', null, 'vm-theo-he-thong.pdf') },
  'sys-xlsx': { label: 'VM theo hệ thống (Excel)', run: () => rptGet('/systems/report.xlsx', null, 'vm-theo-he-thong.xlsx') },
  'sys-export': { label: 'Hệ thống & Owner (Excel nhập lại được)', run: () => rptGet('/systems/export.xlsx', null, 'he-thong-owner.xlsx') },
  'annual-pdf': { label: 'Tổng kết nhu cầu năm & kế hoạch năm sau (PDF)', run: () => rptAnnual('pdf') },
  'annual-xlsx': { label: 'Tổng kết nhu cầu năm & kế hoạch năm sau (Excel)', run: () => rptAnnual('xlsx') },
  'rv-pdf': { label: 'Kết quả rà soát owner (PDF)', run: () => rptReview('pdf') },
  'rv-xlsx': { label: 'Kết quả rà soát owner (Excel)', run: () => rptReview('xlsx') },
};

async function rptRun(btn) {
  const action = RPT_ACTIONS[btn.dataset.report];
  if (!action) return;
  const label = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = 'Đang tạo…';
  try { await action.run(btn); }
  finally { btn.disabled = false; btn.innerHTML = label; refreshIcons(); }
}

/* ---------- multi-select ---------- */
function rptRenderBatch() {
  const bar = $('#rptBatch');
  if (!bar) return;
  bar.classList.toggle('is-on', rptState.batch);
  $('#viewReports').classList.toggle('rpt-batch-mode', rptState.batch);
  const t = bar.querySelector('[data-rpt-batch="toggle"]');
  if (t) t.classList.toggle('btn-primary', rptState.batch);
  $$('#viewReports [data-report]').forEach(b => b.classList.toggle('rpt-picked', rptState.picked.has(b.dataset.report)));
  const c = $('#rptBatchCount');
  if (c) c.textContent = `Đã chọn ${rptState.picked.size}`;
}

async function rptRunBatch() {
  const keys = $$('#viewReports [data-report]').map(b => b.dataset.report).filter((k, i, arr) => rptState.picked.has(k) && arr.indexOf(k) === i);
  if (!keys.length) { rptSetStatus('Chưa chọn báo cáo nào — bấm vào các nút PDF / Excel / CSV để chọn.', true); return; }
  const done = [], failed = [];
  for (const k of keys) {
    const btn = document.querySelector(`#viewReports [data-report="${k}"]`);
    rptSetStatus(`Đang xuất ${done.length + failed.length + 1}/${keys.length}: ${RPT_ACTIONS[k].label}…`);
    try { await rptRun(btn); done.push(RPT_ACTIONS[k].label); }
    catch (err) { failed.push(`${RPT_ACTIONS[k].label}: ${err.message || err}`); }
    await new Promise(r => setTimeout(r, 400));   // let the browser start each download
  }
  rptSetStatus(`Đã xuất ${done.length}/${keys.length} báo cáo.${failed.length ? ' Lỗi — ' + failed.join(' · ') : ''}`
    + (keys.length > 1 ? ' Nếu trình duyệt hỏi “cho phép tải nhiều tệp”, hãy chọn Cho phép.' : ''), failed.length > 0);
}

function bindReportsView() {
  const view = $('#viewReports');
  if (!view || view.dataset.rptBound) return;
  view.dataset.rptBound = '1';
  rptLoadOpts();
  view.addEventListener('click', async e => {
    const chip = e.target.closest('#rptFilter [data-rpt-group]');
    if (chip) { rptApplyGroup(chip.dataset.rptGroup); return; }
    const sync = e.target.closest('[data-rpt-plan-sync]');
    if (sync) {
      rptPlanSync(true);
      $$('#viewReports [data-opt^="plan."]').forEach(el => { rptState.opts[el.dataset.opt] = el.value; });
      rptSaveOpts();
      return;
    }
    const bb = e.target.closest('[data-rpt-batch]');
    if (bb) {
      const a = bb.dataset.rptBatch;
      if (a === 'toggle') { rptState.batch = !rptState.batch; if (!rptState.batch) rptState.picked.clear(); }
      else if (a === 'clear') rptState.picked.clear();
      else if (a === 'run') { bb.disabled = true; try { await rptRunBatch(); } finally { bb.disabled = false; } }
      rptRenderBatch();
      return;
    }
    const btn = e.target.closest('[data-report]');
    if (!btn || btn.disabled) return;
    if (rptState.batch) {
      const k = btn.dataset.report;
      rptState.picked.has(k) ? rptState.picked.delete(k) : rptState.picked.add(k);
      rptRenderBatch();
      return;
    }
    rptSetStatus('');
    try { await rptRun(btn); }
    catch (err) { rptSetStatus(`Không xuất được ${RPT_ACTIONS[btn.dataset.report]?.label || 'báo cáo'}: ${err.message || err}`, true); }
    rptRenderState();
  });
  const remember = e => {
    const el = e.target.closest('[data-opt]');
    if (!el) return;
    rptState.opts[el.dataset.opt] = el.type === 'checkbox' ? el.checked : el.value;
    rptSaveOpts();
  };
  view.addEventListener('change', remember);
  view.addEventListener('input', remember);
  view.addEventListener('toggle', e => { if (e.target.matches('[data-rpt-lic-opts]') && e.target.open) rptLoadLicSnapshots(); }, true);
}

document.addEventListener('DOMContentLoaded', bindReportsView);
