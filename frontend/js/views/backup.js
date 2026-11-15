/* =========================================================
   Backup — Veritas NetBackup monitoring module (read-only REST API).
   Sub-pages: Tổng quan · Client & RPO · Job backup · Độ phủ VM ·
   Storage & policy · Nguồn & ngưỡng. All analysis runs on the backend
   (backend/app/backup/analysis.py); this file renders and edits thresholds.
   Scope: NetBackup server + period selected in the page header.
   ========================================================= */

const BK_TTL_MS = 60 * 1000;
const BK_STATUS = { ok: 'Đạt', warn: 'Cần theo dõi', crit: 'Cần xử lý', na: 'Không hoạt động' };
const BK_SEV = { critical: { cls: 'crit', label: 'Nghiêm trọng' }, warning: { cls: 'warn', label: 'Cảnh báo' }, info: { cls: 'na', label: 'Lưu ý' } };
const BK_OUTCOME = { success: { cls: 'ok', label: 'Thành công' }, partial: { cls: 'warn', label: 'Một phần' }, failed: { cls: 'crit', label: 'Lỗi' }, running: { cls: 'na', label: 'Đang chạy' } };
const BK_READY = { ok: { cls: 'ok', label: 'Đạt' }, warn: { cls: 'warn', label: 'Cần theo dõi' }, crit: { cls: 'crit', label: 'Cần xử lý' } };

function bkState() {
  if (!appState.backup) {
    appState.backup = {
      sub: 'overview', server: 'all', days: 7, servers: null, summary: null, jobs: null, thresholds: null,
      loading: false, error: null, bound: false, charts: {},
      clientFilter: { status: 'all', q: '' }, jobFilter: { outcome: 'failed', category: 'backup', q: '' }, covFilter: { state: 'unprotected', q: '', vc: 'all', cl: 'all' },
      scope: null, scopeEdit: null, sched: null, schedMsg: '',
      lastKey: null, loadedAt: 0,
    };
  }
  return appState.backup;
}

const bkNum = (v, d = 0) => (v === null || v === undefined || !isFinite(v)) ? '—' : Number(v).toLocaleString('vi-VN', { maximumFractionDigits: d, minimumFractionDigits: d });
const bkPct = (v, d = 1) => (v === null || v === undefined || !isFinite(v)) ? '—' : `${bkNum(v, d)}%`;
const bkDate = (iso) => { if (!iso) return '—'; const d = new Date(iso); return isNaN(d) ? esc(iso) : d.toLocaleString('vi-VN', { hour12: false, hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' }); };
const bkPill = (cls, label) => uiPill(cls, label);
const bkHours = (h) => (h === null || h === undefined) ? '—' : (h >= 48 ? `${bkNum(h / 24, 1)} ngày` : `${bkNum(h, 0)} giờ`);

function bkSetStatus(msg, isErr) { uiSetStatus('#bkStatus', msg, isErr ? 'error' : '', { hideWhenEmpty: true }); }

async function bkLoadServers() {
  const st = bkState();
  try { st.servers = await api.bkServers(); } catch (err) { st.servers = []; bkSetStatus(`Không tải được danh sách máy chủ backup: ${err.message}`, true); }
}

async function bkLoad(force) {
  const st = bkState();
  const key = `${st.server}|${st.days}`;
  if (!force && st.summary && st.lastKey === key) return;
  st.loading = true;
  renderBackupBody();
  try {
    if (!st.servers || force) await bkLoadServers();
    st.summary = await api.bkSummary(st.server, st.days);
    st.lastKey = key;
    st.loadedAt = Date.now();
    st.jobs = null;
    st.error = null;
  } catch (err) {
    st.error = err.message;
  } finally {
    st.loading = false;
  }
}

async function bkLoadJobs() {
  const st = bkState();
  const f = st.jobFilter;
  st.jobs = { loading: true };
  renderBackupBody();
  try { st.jobs = await api.bkJobs({ server: st.server, days: st.days, outcome: f.outcome === 'all' ? '' : f.outcome, category: f.category, q: f.q, limit: 1000 }); }
  catch (err) { st.jobs = { error: err.message }; }
  renderBackupBody();
}

function bkDestroyCharts() {
  const st = bkState();
  Object.keys(st.charts).forEach(k => { if (st.charts[k]) st.charts[k].destroy(); st.charts[k] = null; });
}

/* ---------- Header: server / period / actions ---------- */
function renderBackupHead() {
  const st = bkState();
  const head = $('#bkHead');
  if (!head) return;
  const servers = st.servers || [];
  const opts = [`<option value="all">Tất cả máy chủ backup (${servers.length})</option>`]
    .concat(servers.map(s => `<option value="${esc(s.key)}">${esc(s.name)}${s.demo ? ' · demo' : ''}</option>`)).join('');
  const chips = servers.map(s => {
    const cls = s.lastStatus === 'error' ? 'crit' : (s.lastStatus === 'ok' ? 'ok' : 'na');
    const t = s.lastStatus === 'error' ? `Lỗi: ${s.lastError}` : (s.lastSyncAt ? `Đồng bộ ${bkDate(s.lastSyncAt)}` : 'Chưa đồng bộ');
    return `<span class="pill ${cls}" title="${esc(t)}${s.warning ? ' · ' + esc(s.warning) : ''}"><span class="pill-dot"></span>${esc(s.name)} · ${esc(t)}</span>`;
  }).join(' ');
  head.innerHTML = `
    <div class="lic-head-row">
      <div class="panel-toolbar">
        <select id="bkServer" title="Máy chủ backup">${opts}</select>
        <select id="bkDays" title="Kỳ thống kê">
          ${[1, 7, 14, 30, 60, 90].map(d => `<option value="${d}" ${d === st.days ? 'selected' : ''}>${d === 1 ? '24 giờ qua' : d + ' ngày qua'}</option>`).join('')}
        </select>
        <span class="bk-chips">${chips || '<span class="pill na">Chưa cấu hình máy chủ backup</span>'}</span>
      </div>
      <div class="panel-toolbar">
        <button class="btn btn-secondary btn-sm" id="bkSync" type="button"><i data-lucide="refresh-cw"></i>Đồng bộ backup</button>
        <button class="btn btn-secondary btn-sm" id="bkXlsx" type="button"><i data-lucide="sheet"></i>Xuất Excel</button>
        <button class="btn btn-primary btn-sm" id="bkPdf" type="button"><i data-lucide="file-text"></i>Báo cáo PDF</button>
      </div>
    </div>
    <p class="logo-upload-hint hidden" id="bkStatus" role="status" aria-live="polite"></p>`;
  $('#bkServer').value = st.server;
  refreshIcons();
}

function bindBackupHead() {
  const st = bkState();
  const head = $('#bkHead');
  if (!head || st.bound) return;
  st.bound = true;
  head.addEventListener('change', async e => {
    if (e.target.id === 'bkServer') st.server = e.target.value;
    else if (e.target.id === 'bkDays') st.days = Number(e.target.value) || 7;
    else return;
    await bkLoad(true);
    renderBackupBody();
  });
  head.addEventListener('click', async e => {
    const btn = e.target.closest('button');
    if (!btn) return;
    if (btn.id === 'bkSync') {
      const orig = btn.innerHTML; btn.disabled = true; btn.textContent = 'Đang đồng bộ...';
      try {
        const res = st.server === 'all' ? await api.bkSyncAll() : { results: [await api.bkSync(st.server)].map(r => ({ ...r, ok: true })) };
        const bad = (res.results || []).filter(r => !r.ok);
        bkSetStatus(bad.length ? `Đồng bộ lỗi: ${bad.map(r => `${r.key}: ${r.error}`).join('; ')}` : `Đã đồng bộ backup lúc ${new Date().toLocaleTimeString('vi-VN')}.`, bad.length > 0);
        await bkLoad(true);
        renderBackupView();
      } catch (err) { bkSetStatus(`Đồng bộ thất bại: ${err.message}`, true); }
      finally { btn.disabled = false; btn.innerHTML = orig; refreshIcons(); }
    } else if (btn.id === 'bkPdf' || btn.id === 'bkXlsx') {
      const orig = btn.innerHTML; btn.disabled = true; btn.textContent = btn.id === 'bkPdf' ? 'Đang tạo PDF...' : 'Đang tạo Excel...';
      try { await (btn.id === 'bkPdf' ? api.bkReportPdf(st.server, st.days) : api.bkReportXlsx(st.server, st.days)); }
      catch (err) { bkSetStatus(`Không xuất được báo cáo: ${err.message}`, true); }
      finally { btn.disabled = false; btn.innerHTML = orig; refreshIcons(); }
    }
  });
}

/* ---------- Body ---------- */
function bkKpis(s) {
  const k = s.kpis, th = s.thresholds, cov = s.coverage;
  const tone = (v, warn, crit, higherBetter = true) => (v === null || v === undefined) ? '' : (higherBetter ? (v < crit ? 'tone-crit' : v < warn ? 'tone-warn' : 'tone-ok') : (v >= crit ? 'tone-crit' : v >= warn ? 'tone-warn' : 'tone-ok'));
  const cards = [
    ['Tỷ lệ thành công', `<span class="${tone(k.successRate, th.success_target_pct, th.success_crit_pct)}">${bkPct(k.successRate)}</span>`, `${bkNum(k.jobs)} job · ${bkNum(k.failed)} lỗi · ${bkNum(k.partial)} một phần · mục tiêu ${bkNum(th.success_target_pct)}%`],
    ['24 giờ qua', `<span class="${tone(k.successRate24h, th.success_target_pct, th.success_crit_pct)}">${bkPct(k.successRate24h)}</span>`, `${bkNum(k.jobs24h)} job · ${bkNum(k.dataTB24h, 2)} TB · ${bkNum(k.running)} đang chạy`],
    ['Client đạt RPO', `<span class="${tone(k.rpoCompliancePct, 95, 85)}">${bkPct(k.rpoCompliancePct)}</span>`, `${bkNum(k.clientsCrit)} cần xử lý · ${bkNum(k.clientsWarn)} theo dõi / ${bkNum(k.clients)} client · RPO ${bkNum(th.rpo_hours)} giờ`],
    ['Độ phủ backup VM', cov.available ? `<span class="${tone(cov.pct, 95, 90)}">${bkPct(cov.pct)}</span>` : '—', cov.available ? `${bkNum(cov.unprotected)} VM chưa backup · ${bkNum(cov.failing)} lỗi / ${bkNum(cov.total)} VM đang chạy` : 'Chưa có inventory vCenter để đối chiếu'],
    ['Dữ liệu đã backup', `${bkNum(k.dataTB, 2)} TB`, `Trong ${s.period.days} ngày · bản sao DR ${bkPct(k.copyRate)} thành công`],
    ['Disk pool cao nhất', `<span class="${tone(k.storageMaxPct, th.storage_warn_pct, th.storage_crit_pct, false)}">${bkPct(k.storageMaxPct, 0)}</span>`, esc(k.storageMaxName || 'Chưa có dữ liệu storage')],
  ];
  return `<div class="kpi-grid lic-kpi-6">${cards.map(([l, v, sub]) => `<div class="kpi-card"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${sub}</div></div>`).join('')}</div>`;
}

function bkFindingsTable(findings, limit) {
  const rows = (findings || []).slice(0, limit || 999).map(f => `<tr>
    <td>${bkPill(BK_SEV[f.severity].cls, BK_SEV[f.severity].label)}</td><td>${esc(f.area)}</td>
    <td class="wrap"><b>${esc(f.title)}</b>${f.detail ? `<span class="muted-sub bk-wrap">${esc(f.detail)}</span>` : ''}</td>
    <td class="wrap">${esc(f.action)}</td></tr>`).join('');
  return `<div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Mức độ</th><th>Hạng mục</th><th>Phát hiện</th><th>Khuyến nghị</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="4" class="empty-note">Không có phát hiện nào.</td></tr>'}</tbody></table></div>`;
}

function bkOverview(s) {
  const r = BK_READY[s.readiness];
  const groups = (s.failureGroups || []).map(g => `<tr><td><b>${esc(g.label)}</b><span class="muted-sub bk-wrap">${esc(g.action)}</span></td><td class="num">${bkNum(g.count)}</td><td class="num">${bkNum(g.clientCount)}</td></tr>`).join('');
  const crit = (s.clients || []).filter(c => c.status !== 'ok').slice(0, 8).map(c => `<tr><td><b>${esc(c.client)}</b><span class="muted-sub">${esc(c.policies.join(', '))}</span></td>
    <td>${bkDate(c.lastSuccessAt)}<span class="muted-sub">${bkHours(c.hoursSinceSuccess)} trước</span></td><td class="wrap">${esc(c.lastStatusText)}</td><td>${bkPill(c.status, c.statusLabel)}</td></tr>`).join('');
  return `
    <section class="insight-box bk-brief bk-${s.readiness}"><div class="bk-brief-head">${bkPill(r.cls, r.label)}<strong>${esc(s.headline)}</strong></div></section>
    ${bkKpis(s)}
    <section class="chart-grid exec-chart-grid">
      <article class="card chart-card">
        <div class="panel-head panel-head-tight"><div><div class="panel-title">Kết quả job backup theo ngày</div><p class="panel-sub">Job ở cấp thực thi (không đếm trùng job cha của VMware / multistream).</p></div></div>
        <div class="chart-wrap"><canvas id="bkChartDaily"></canvas></div>
      </article>
      <article class="card chart-card">
        <div class="panel-head panel-head-tight"><div><div class="panel-title">Nguyên nhân lỗi</div><p class="panel-sub">Gom theo đầu mối xử lý.</p></div></div>
        <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Nhóm</th><th>Job</th><th>Client</th></tr></thead>
        <tbody>${groups || '<tr><td colspan="3" class="empty-note">Không có job lỗi trong kỳ.</td></tr>'}</tbody></table></div>
      </article>
    </section>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title">Client cần xử lý</div><p class="panel-sub">Lỗi liên tiếp, quá RPO hoặc lần gần nhất không thành công.</p></div>
        <div class="panel-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-nav-link="bk-clients">Xem tất cả client</button></div></div>
      <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Client</th><th>Thành công gần nhất</th><th>Kết quả lần cuối</th><th>Đánh giá</th></tr></thead>
      <tbody>${crit || '<tr><td colspan="4" class="empty-note">Mọi client đều đạt.</td></tr>'}</tbody></table></div>
    </section>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title">Phát hiện &amp; khuyến nghị</div><p class="panel-sub">Theo các ngưỡng tại trang Nguồn &amp; ngưỡng.</p></div></div>
      ${bkFindingsTable(s.findings)}
    </section>`;
}

function bkClients(s) {
  const f = bkState().clientFilter;
  const q = f.q.trim().toLowerCase();
  const list = (s.clients || []).filter(c => (f.status === 'all' || c.status === f.status) && (!q || `${c.client} ${c.policies.join(' ')}`.toLowerCase().includes(q)));
  const cnt = (k) => (s.clients || []).filter(c => k === 'all' || c.status === k).length;
  const rows = list.map(c => `<tr>
    <td><b>${esc(c.client)}</b><span class="muted-sub">${esc(c.policyTypes.join(', '))}</span></td><td class="wrap">${esc(c.policies.join(', '))}</td>
    <td>${bkDate(c.lastJobAt)}</td><td class="wrap">${c.lastStatus !== null && c.lastStatus !== undefined ? `<b>${c.lastStatus}</b> · ` : ''}${esc(c.lastStatusText)}</td>
    <td>${bkDate(c.lastSuccessAt)}</td><td class="num">${bkHours(c.hoursSinceSuccess)}</td><td class="num">${c.consecutiveFail || '—'}</td>
    <td class="num">${bkNum(c.jobs)}</td><td class="num">${c.failed ? `<b class="tone-crit">${c.failed}</b>` : '0'}</td><td class="num">${bkNum(c.tb, 2)}</td>
    <td>${bkPill(c.status, c.statusLabel)}${c.note ? `<span class="muted-sub bk-wrap">${esc(c.note)}</span>` : ''}</td></tr>`).join('');
  return `
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title">Client &amp; RPO</div><p class="panel-sub">RPO = thời gian từ bản backup thành công gần nhất (status 0 hoặc 1). Cảnh báo khi quá ${bkNum(s.thresholds.rpo_hours)} giờ; nghiêm trọng khi quá ${bkNum(s.thresholds.rpo_crit_hours)} giờ hoặc lỗi liên tiếp từ ${s.thresholds.consecutive_fail} lần.</p></div>
        <div class="panel-toolbar">
          <select data-bk-filter="clientStatus">${['all', 'crit', 'warn', 'ok'].map(k => `<option value="${k}" ${f.status === k ? 'selected' : ''}>${k === 'all' ? 'Tất cả' : BK_STATUS[k]} (${cnt(k)})</option>`).join('')}</select>
          <span class="search-box"><i data-lucide="search"></i><input type="text" data-bk-filter="clientQ" placeholder="Tìm client / policy" value="${esc(f.q)}"/></span>
        </div></div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Client</th><th>Policy</th><th>Job gần nhất</th><th>Kết quả</th><th>Thành công gần nhất</th><th>Đã qua</th><th>Lỗi liên tiếp</th><th>Job</th><th>Lỗi</th><th>TB</th><th>Đánh giá</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="11" class="empty-note">Không có client phù hợp.</td></tr>'}</tbody></table></div>
      <div class="row-count">${list.length} / ${(s.clients || []).length} client</div>
    </section>`;
}

function bkJobs() {
  const st = bkState();
  const f = st.jobFilter, j = st.jobs;
  let body;
  if (!j || j.loading) body = '<div class="empty-note">Đang tải job…</div>';
  else if (j.error) body = `<div class="empty-note tone-crit">${esc(j.error)}</div>`;
  else {
    const rows = j.rows.map(r => `<tr>
      <td>${esc(String(r.jobId))}${r.parentJobId ? `<span class="muted-sub">cha ${esc(String(r.parentJobId))}</span>` : ''}</td><td>${esc(r.jobType || '')}</td>
      <td>${esc(r.clientName || '—')}</td><td>${esc(r.policyName || '—')}<span class="muted-sub">${esc(r.scheduleLabel || '')}</span></td>
      <td>${bkDate(r.startTime)}</td><td class="num">${r.elapsedSec ? bkNum(r.elapsedSec / 60, 0) + ' ph' : '—'}</td><td class="num">${bkNum((r.kbytes || 0) / 1048576, 2)}</td>
      <td>${bkPill(BK_OUTCOME[r.outcome].cls, BK_OUTCOME[r.outcome].label)}</td>
      <td class="wrap">${r.status !== null && r.status !== undefined ? `<b>${r.status}</b> · ` : ''}${esc(r.statusText)}${r.group ? `<span class="muted-sub">${esc(r.group)}</span>` : ''}</td></tr>`).join('');
    body = `<div class="table-scroll"><table class="data-table"><thead><tr><th>Job ID</th><th>Loại</th><th>Client</th><th>Policy / lịch</th><th>Bắt đầu</th><th>Thời lượng</th><th>GB</th><th>Kết quả</th><th>Mã · mô tả</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="9" class="empty-note">Không có job phù hợp.</td></tr>'}</tbody></table></div>
      <div class="row-count">${j.rows.length < j.total ? `Hiển thị ${j.rows.length} / ` : ''}${bkNum(j.total)} job — danh sách đầy đủ trong file Excel.</div>`;
  }
  return `
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title">Job backup</div><p class="panel-sub">Job của máy chủ backup trong kỳ đã chọn (lưu lịch sử theo mỗi lần đồng bộ).</p></div>
        <div class="panel-toolbar">
          <select data-bk-filter="jobOutcome">${[['all', 'Mọi kết quả'], ['failed', 'Lỗi'], ['partial', 'Một phần'], ['success', 'Thành công'], ['running', 'Đang chạy']].map(([k, l]) => `<option value="${k}" ${f.outcome === k ? 'selected' : ''}>${l}</option>`).join('')}</select>
          <select data-bk-filter="jobCategory">${[['backup', 'Backup'], ['copy', 'Nhân bản (DR)'], ['restore', 'Restore'], ['all', 'Mọi loại job']].map(([k, l]) => `<option value="${k}" ${f.category === k ? 'selected' : ''}>${l}</option>`).join('')}</select>
          <span class="search-box"><i data-lucide="search"></i><input type="text" data-bk-filter="jobQ" placeholder="Client, policy, job ID, mã lỗi" value="${esc(f.q)}"/></span>
          <button class="btn btn-secondary btn-sm" type="button" data-bk-action="jobs-search">Lọc</button>
        </div></div>
      ${body}
    </section>`;
}

function bkCoverage(s) {
  const cov = s.coverage;
  if (!cov.available) return `${bkScopePanel(cov)}<section class="table-card"><div class="empty-note">Chưa có inventory vCenter để đối chiếu. Bấm “Đồng bộ ngay” (vCenter) — trang này tự cập nhật sau khi đồng bộ xong.</div></section>`;
  const f = bkState().covFilter;
  const q = f.q.trim().toLowerCase();
  const pass = (v) => (f.vc === 'all' || (v.vcenterKey || 'import') === f.vc) && (f.cl === 'all' || v.cluster === f.cl);
  const vms = (f.state === 'unprotected' ? cov.unprotectedVms : f.state === 'failing' ? cov.failingVms : cov.unprotectedVms.concat(cov.failingVms))
    .filter(v => pass(v) && (!q || `${v.vm} ${v.cluster} ${v.host || ''} ${v.ip || ''}`.toLowerCase().includes(q)));
  const vcOpts = [...new Set(cov.byCluster.map(c => c.vcenterKey))].sort();
  const clOpts = [...new Set(cov.byCluster.filter(c => f.vc === 'all' || c.vcenterKey === f.vc).map(c => c.cluster))].sort();
  const byCl = cov.byCluster.filter(c => pass({ vcenterKey: c.vcenterKey, cluster: c.cluster })).map(c => `<tr><td>${esc(c.vcenterKey)}</td><td><b>${esc(c.cluster)}</b></td><td class="num">${c.total}</td><td class="num">${c.protected}</td>
    <td class="num">${c.failing || 0}</td><td class="num">${c.unprotected ? `<b class="tone-crit">${c.unprotected}</b>` : 0}</td><td class="num">${bkPct(c.pct)}</td></tr>`).join('');
  const rows = vms.map(v => `<tr><td>${uiEntityLink('vm', v.vm, v.vcenterKey)}</td><td>${esc(v.vcenterKey || '')}</td><td>${esc(v.cluster || '')}</td><td>${esc(v.host || '—')}</td><td>${esc(v.ip || '—')}</td>
    <td class="num">${bkNum(v.vcpu)}</td><td class="num">${bkNum(v.ramGB)}</td><td class="num">${bkNum(v.diskGB)}</td>
    <td>${v.state === 'unprotected' ? bkPill('crit', 'Chưa backup') : bkPill('warn', 'Có job nhưng lỗi')}</td></tr>`).join('');
  const k = [['VM đang chạy' + (cov.scopeActive ? ' (trong phạm vi)' : ''), bkNum(cov.total), `Không tính ${cov.poweredOff} VM tắt · ${cov.excluded} VM loại trừ theo tên${cov.scopeActive ? ` · ${cov.outOfScope} VM ngoài phạm vi` : ''}`],
             ['Có backup', bkNum(cov.protected), `Thành công trong ${s.thresholds.coverage_days} ngày · ${bkPct(cov.pct)}`],
             ['Có job nhưng lỗi', bkNum(cov.failing), 'Có trong policy / có job, chưa thành công'],
             ['Chưa có backup', `<span class="${cov.unprotected ? 'tone-crit' : 'tone-ok'}">${bkNum(cov.unprotected)}</span>`, 'Không khớp client backup nào']];
  const filt = `<select data-bk-filter="covVc"><option value="all">Mọi vCenter</option>${vcOpts.map(x => `<option value="${esc(x)}" ${f.vc === x ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select>
          <select data-bk-filter="covCl"><option value="all">Mọi cluster</option>${clOpts.map(x => `<option value="${esc(x)}" ${f.cl === x ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select>`;
  return `
    ${bkScopePanel(cov)}
    <div class="kpi-grid">${k.map(([l, v, sub]) => `<div class="kpi-card"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${sub}</div></div>`).join('')}</div>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title">Độ phủ theo cluster</div><p class="panel-sub">Đối chiếu VM ${cov.scopeActive ? 'trong phạm vi cần backup' : 'trong inventory vCenter'} với client NetBackup theo tên VM, hostname guest hoặc IP.</p></div>
        <div class="panel-toolbar">${filt}</div></div>
      <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>vCenter</th><th>Cluster</th><th>VM</th><th>Có backup</th><th>Lỗi</th><th>Chưa backup</th><th>Độ phủ</th></tr></thead><tbody>${byCl}</tbody></table></div>
    </section>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title">VM cần xác nhận backup</div><p class="panel-sub">Xác nhận với System Owner: bổ sung vào policy / query VMware hoặc ghi nhận ngoại lệ (thêm từ khoá loại trừ ở trang Nguồn &amp; ngưỡng).</p></div>
        <div class="panel-toolbar">
          ${filt}
          <select data-bk-filter="covState">${[['unprotected', 'Chưa backup'], ['failing', 'Có job nhưng lỗi'], ['all', 'Cả hai']].map(([kk, l]) => `<option value="${kk}" ${f.state === kk ? 'selected' : ''}>${l}</option>`).join('')}</select>
          <span class="search-box"><i data-lucide="search"></i><input type="text" data-bk-filter="covQ" placeholder="Tìm VM / host / IP" value="${esc(f.q)}"/></span>
        </div></div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>VM</th><th>vCenter</th><th>Cluster</th><th>Host</th><th>IP</th><th>vCPU</th><th>RAM (GB)</th><th>Disk (GB)</th><th>Tình trạng</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="9" class="empty-note">Không có VM nào.</td></tr>'}</tbody></table></div>
      <div class="row-count">${vms.length} VM</div>
    </section>`;
}

/* ---------- Backup scope (systems that must be backed up) ----------
   Tree vCenter → cluster → host; several clusters across several vCenters can be ticked.
   Saved as rules on the server, compressed to the highest fully-ticked level so a
   whole cluster / vCenter keeps covering hosts added later. */
const BK_SEP = '\u0001';
const bkHostKey = (vc, cl, h) => [vc, cl, h].join(BK_SEP);

function bkRuleMatch(rules, vc, cl, h) {
  return (rules || []).some(r => (r.vcenterKey || 'import') === vc && (!r.cluster || r.cluster === cl) && (!r.host || r.host === h));
}

function bkScopeStartEdit() {
  const st = bkState();
  const sc = st.scope;
  const checked = new Set();
  (sc.tree || []).forEach(v => v.clusters.forEach(c => c.hosts.forEach(h => {
    if (bkRuleMatch(sc.rules, v.vcenterKey, c.name, h.name)) checked.add(bkHostKey(v.vcenterKey, c.name, h.name));
  })));
  st.scopeEdit = { mode: sc.rules.length ? 'selected' : 'all', checked, open: new Set(), q: '' };
}

function bkScopeRules() {
  const st = bkState(), ed = st.scopeEdit;
  if (ed.mode === 'all') return [];
  const rules = [];
  (st.scope.tree || []).forEach(v => {
    const all = v.clusters.every(c => c.hosts.every(h => ed.checked.has(bkHostKey(v.vcenterKey, c.name, h.name))));
    if (all && v.clusters.length) { rules.push({ vcenterKey: v.vcenterKey }); return; }
    v.clusters.forEach(c => {
      const hs = c.hosts.filter(h => ed.checked.has(bkHostKey(v.vcenterKey, c.name, h.name)));
      if (hs.length && hs.length === c.hosts.length) rules.push({ vcenterKey: v.vcenterKey, cluster: c.name });
      else hs.forEach(h => rules.push({ vcenterKey: v.vcenterKey, cluster: c.name, host: h.name }));
    });
  });
  return rules;
}

function bkTriState(keys, checked) {
  const n = keys.filter(k => checked.has(k)).length;
  return n === 0 ? 'none' : (n === keys.length ? 'all' : 'some');
}

function bkScopeEditorHtml() {
  const st = bkState(), sc = st.scope, ed = st.scopeEdit;
  const q = ed.q.trim().toLowerCase();
  const dis = ed.mode === 'all' ? 'disabled' : '';
  const box = (attr, state, label, sub) => `<label class="bk-sc-item"><input type="checkbox" ${attr} ${state === 'all' ? 'checked' : ''} data-tri="${state}" ${dis}/><span>${label}</span>${sub ? `<span class="muted-sub">${sub}</span>` : ''}</label>`;
  const tree = (sc.tree || []).map(v => {
    const clusters = v.clusters.filter(c => !q || `${v.vcenterKey} ${v.name} ${c.name} ${c.hosts.map(h => h.name).join(' ')}`.toLowerCase().includes(q));
    if (!clusters.length) return '';
    const vKeys = v.clusters.flatMap(c => c.hosts.map(h => bkHostKey(v.vcenterKey, c.name, h.name)));
    const cl = clusters.map(c => {
      const cKeys = c.hosts.map(h => bkHostKey(v.vcenterKey, c.name, h.name));
      const ck = `${v.vcenterKey}${BK_SEP}${c.name}`;
      const open = ed.open.has(ck) || (q && c.hosts.some(h => h.name.toLowerCase().includes(q)) && !c.name.toLowerCase().includes(q));
      const hosts = open ? `<div class="bk-sc-hosts">${c.hosts.map(h => box(`data-sc-host="${esc(bkHostKey(v.vcenterKey, c.name, h.name))}"`,
        ed.checked.has(bkHostKey(v.vcenterKey, c.name, h.name)) ? 'all' : 'none', esc(h.name), `${h.vmsOn} VM đang chạy`)).join('')}</div>` : '';
      return `<div class="bk-sc-cluster"><button type="button" class="bk-sc-caret" data-sc-toggle="${esc(ck)}" title="Chọn từng host">${open ? '▾' : '▸'}</button>
        ${box(`data-sc-cl="${esc(ck)}"`, bkTriState(cKeys, ed.checked), `<b>${esc(c.name)}</b>`, `${c.hosts.length} host · ${c.vmsOn} VM đang chạy`)}${hosts}</div>`;
    }).join('');
    return `<div class="bk-sc-vc">${box(`data-sc-vc="${esc(v.vcenterKey)}"`, bkTriState(vKeys, ed.checked), `<b>${esc(v.name)}</b> <span class="muted">(${esc(v.vcenterKey)})</span>`, `${v.clusters.length} cluster · ${v.vmsOn} VM đang chạy`)}
      <div class="bk-sc-clusters">${cl}</div></div>`;
  }).join('');
  const rules = bkScopeRules();
  const nVm = (sc.tree || []).reduce((s, v) => s + v.clusters.reduce((s2, c) => s2 + c.hosts.reduce((s3, h) => s3 + (ed.mode === 'all' || ed.checked.has(bkHostKey(v.vcenterKey, c.name, h.name)) ? h.vmsOn : 0), 0), 0), 0);
  return `
    <div class="bk-sc-mode">
      <label><input type="radio" name="bkScMode" value="all" data-sc-mode ${ed.mode === 'all' ? 'checked' : ''}/> Tất cả hệ thống trong inventory vCenter</label>
      <label><input type="radio" name="bkScMode" value="selected" data-sc-mode ${ed.mode === 'selected' ? 'checked' : ''}/> Chỉ các vCenter / cluster / host được chọn (ví dụ production)</label>
    </div>
    <div class="panel-toolbar bk-sc-tools">
      <span class="search-box"><i data-lucide="search"></i><input type="text" data-sc-q placeholder="Lọc cluster / host theo tên, vd: prod" value="${esc(ed.q)}" ${dis}/></span>
      <button class="btn btn-secondary btn-sm" type="button" data-bk-action="scope-select-visible" ${dis}>Chọn các cluster đang hiện</button>
      <button class="btn btn-ghost btn-sm" type="button" data-bk-action="scope-clear" ${dis}>Bỏ chọn tất cả</button>
    </div>
    <div class="bk-sc-tree ${ed.mode === 'all' ? 'is-disabled' : ''}">${tree || '<div class="empty-note">Không có cluster khớp bộ lọc.</div>'}</div>
    <div class="bk-sc-foot">
      <span class="muted">${ed.mode === 'all' ? 'Mọi VM đang chạy đều được yêu cầu có backup.' : `${rules.length} mục phạm vi · khoảng ${bkNum(nVm)} VM đang chạy cần backup.`}
        Nên chọn theo cluster: chọn theo host sẽ bỏ sót VM khi DRS / vMotion chuyển VM sang host khác.</span>
      <span class="panel-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-bk-action="scope-cancel">Huỷ</button>
      <button class="btn btn-primary btn-sm" type="button" data-bk-action="scope-save">Lưu phạm vi</button></span>
    </div>`;
}

function bkScopePanel(cov) {
  const st = bkState();
  const editing = !!st.scopeEdit;
  const label = cov.scopeActive ? esc(cov.scopeLabel) : 'Toàn bộ hệ thống trong inventory vCenter';
  return `
    <section class="table-card bk-scope-card">
      <div class="panel-head panel-head-tight"><div><div class="panel-title">Phạm vi cần backup</div>
        <p class="panel-sub">Chỉ VM thuộc phạm vi này được tính độ phủ, đưa vào phát hiện và báo cáo PDF / Excel. Có thể chọn nhiều cluster trên nhiều vCenter.</p></div>
        ${editing ? '' : `<div class="panel-toolbar"><button class="btn btn-secondary btn-sm" type="button" data-bk-action="scope-edit"><i data-lucide="filter"></i>Chỉnh phạm vi</button></div>`}</div>
      ${editing ? `<div id="bkScopeEditor">${bkScopeEditorHtml()}</div>`
        : `<div class="bk-scope-summary">${cov.scopeActive ? bkPill('ok', 'Đang áp dụng') : bkPill('na', 'Chưa giới hạn')} <span>${label}</span>${cov.scopeActive ? `<span class="muted"> · ${bkNum(cov.outOfScope)} VM ngoài phạm vi không tính</span>` : ''}</div>`}
    </section>`;
}

function bkScopeRerender() {
  const el = $('#bkScopeEditor');
  if (!el) return;
  const qEl = el.querySelector('[data-sc-q]');
  const hadFocus = qEl && document.activeElement === qEl;
  const pos = hadFocus ? qEl.selectionStart : 0;
  el.innerHTML = bkScopeEditorHtml();
  el.querySelectorAll('input[data-tri="some"]').forEach(i => { i.indeterminate = true; });
  refreshIcons();
  if (hadFocus) { const n = el.querySelector('[data-sc-q]'); if (n) { n.focus(); try { n.setSelectionRange(pos, pos); } catch { /* ignore */ } } }
}

function bkScopeSetMany(keys, on) {
  const ed = bkState().scopeEdit;
  keys.forEach(k => (on ? ed.checked.add(k) : ed.checked.delete(k)));
}

function bkScopeKeysOf(vc, cl) {
  const out = [];
  (bkState().scope.tree || []).forEach(v => {
    if (v.vcenterKey !== vc) return;
    v.clusters.forEach(c => { if (cl === undefined || c.name === cl) c.hosts.forEach(h => out.push(bkHostKey(v.vcenterKey, c.name, h.name))); });
  });
  return out;
}

/* ---------- Backup schedule & nightly compliance ---------- */
const BK_DAYS = ['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN'];
const BK_NIGHT = {
  ok: { cls: 'ok', sym: '✓', label: 'Đạt' }, partial: { cls: 'warn', sym: '½', label: 'Một phần' },
  late: { cls: 'warn', sym: 'T', label: 'Trễ' }, wrongtype: { cls: 'warn', sym: 'S', label: 'Sai loại' },
  failed: { cls: 'crit', sym: '✕', label: 'Lỗi' }, missing: { cls: 'crit', sym: '!', label: 'Thiếu' },
  running: { cls: 'na', sym: '…', label: 'Đang chạy' }, pending: { cls: 'na', sym: '·', label: 'Chưa đến lịch' },
};
const bkDaysTxt = (arr) => (arr || []).length === 7 ? 'Hằng ngày' : (arr || []).map(d => BK_DAYS[d]).join(', ') || '—';

function bkSchedule(s) {
  const st = bkState();
  const sc = s.schedule || { rules: [], nights: [] };
  const nights = sc.nights || [];
  const counts = {};
  (sc.rules || []).forEach(r => Object.entries(r.counts || {}).forEach(([k, v]) => { counts[k] = (counts[k] || 0) + v; }));
  const nbN = (sc.rules || []).filter(r => r.source === 'netbackup').length;
  const fxN = (sc.rules || []).filter(r => r.source === 'fixed').length;
  const manN = (sc.rules || []).filter(r => r.source === 'manual').length;
  const head = nights.map(d => { const dt = new Date(d + 'T00:00:00'); const wd = (dt.getDay() + 6) % 7;
    return `<th class="bk-night-h" title="Đêm ${d}">${BK_DAYS[wd]}<span class="muted-sub">${d.slice(8, 10)}/${d.slice(5, 7)}</span></th>`; }).join('');
  const rows = (sc.rules || []).map(r => {
    const cells = r.nights.map(c => {
      if (!c.status) return `<td class="bk-night ${c.expected ? '' : 'off'}" title="${esc(c.detail || 'Không có lịch')}">${c.expected ? '<span class="muted">?</span>' : ''}</td>`;
      const m = BK_NIGHT[c.status];
      const tip = `${c.date} · ${c.expected === 'full' ? 'Full' : c.expected === 'any' ? 'Khung cố định' : 'Incremental'} · ${m.label}${c.detail ? ' — ' + c.detail : ''}`;
      return `<td class="bk-night" title="${esc(tip)}"><span class="bk-cell ${m.cls} ${c.expected === 'full' ? 'is-full' : ''}">${m.sym}</span></td>`;
    }).join('');
    const src = r.source === 'netbackup' ? bkPill('na', 'Từ policy') : r.source === 'fixed' ? bkPill('warn', 'Khung cố định') : bkPill('ok', 'Thủ công');
    const pols = (r.source === 'netbackup' || r.source === 'fixed' ? '' : `<span class="muted-sub bk-wrap">${r.policies.length ? esc(r.policies.join(', ')) : '<b class="tone-warn">Chưa gán policy</b>'}</span>`)
      + (r.scheduleNotes || []).map(n => `<span class="muted-sub bk-wrap tone-warn">${esc(n)}</span>`).join('')
      + (r.calendar && Object.keys(r.calendar).length ? `<span class="muted-sub">Lịch theo ngày trong tháng: ${Object.entries(r.calendar).map(([k, c]) => `${k === 'full' ? 'Full' : 'Incr'} ngày ${[...(c.monthDays || []), ...(c.lastDayOfMonth ? ['cuối tháng'] : [])].join(', ') || (c.dates || []).join(', ')}`).join(' · ')}</span>` : '');
    const full = r.source === 'fixed' ? 'Mỗi đêm (không xét Full / Incr)' : r.fullEveryDays ? `Full mỗi ${r.fullEveryDays} ngày` : `Full ${bkDaysTxt(r.fullDays)}`;
    const extra = [r.retentionIssues && r.retentionIssues.length ? `<span class="pill warn" title="${esc(r.retentionIssues.join('; '))}">Retention lệch</span>` : ''].join('');
    return `<tr><td><b>${esc(r.system)}</b> ${src}${pols}</td>
      <td class="wrap">${full}<span class="muted-sub">${r.source === 'fixed' ? '' : 'Incr ' + bkDaysTxt(r.incrDays) + ' · '}${esc(r.startTime)}→${esc(r.endTime)}${r.retentionDays ? ' · giữ Full ' + r.retentionDays + ' ngày' : ''}</span></td>
      ${cells}<td class="num"><b class="${r.compliancePct === null ? '' : r.compliancePct >= 95 ? 'tone-ok' : r.compliancePct >= 80 ? 'tone-warn' : 'tone-crit'}">${bkPct(r.compliancePct)}</b>
      <span class="muted-sub">${r.counts.ok || 0}/${r.evaluated} đêm</span></td><td class="num">${r.rpoHours ? bkNum(r.rpoHours) + ' giờ' : '—'}</td><td>${extra}</td></tr>`;
  }).join('');
  const legend = Object.entries(BK_NIGHT).map(([k, m]) => `<span class="bk-legend-item"><span class="bk-cell ${m.cls}">${m.sym}</span>${m.label}${counts[k] ? ` (${counts[k]})` : ''}</span>`).join('')
    + '<span class="bk-legend-item"><span class="bk-cell ok is-full">✓</span>Đêm Full (viền đậm)</span>';
  const unm = (sc.unmappedPolicies || []);
  return `
    <div class="kpi-grid">
      <div class="kpi-card"><div class="label">Tuân thủ lịch</div><div class="value ${sc.compliancePct === null ? '' : sc.compliancePct >= 95 ? 'tone-ok' : sc.compliancePct >= 80 ? 'tone-warn' : 'tone-crit'}">${bkPct(sc.compliancePct)}</div><div class="sub">${counts.ok || 0} / ${sc.evaluated || 0} đêm đạt trong ${nights.length} đêm gần nhất</div></div>
      <div class="kpi-card"><div class="label">Lỗi / thiếu</div><div class="value ${(counts.failed || 0) + (counts.missing || 0) ? 'tone-crit' : 'tone-ok'}">${bkNum((counts.failed || 0) + (counts.missing || 0))}</div><div class="sub">${counts.failed || 0} đêm có client lỗi · ${counts.missing || 0} đêm không chạy</div></div>
      <div class="kpi-card"><div class="label">Trễ / sai loại</div><div class="value ${(counts.late || 0) + (counts.wrongtype || 0) ? 'tone-warn' : 'tone-ok'}">${bkNum((counts.late || 0) + (counts.wrongtype || 0))}</div><div class="sub">Kết thúc sau giờ kết thúc + ${sc.graceMinutes} phút · đêm Full không chạy Full</div></div>
      <div class="kpi-card"><div class="label">Nguồn lịch</div><div class="value">${bkNum((sc.rules || []).length)}</div><div class="sub">${sc.mode === 'fixed' ? 'Chế độ khung cố định' : 'Chế độ đọc từ NetBackup'} · ${nbN} theo NetBackup · ${fxN} theo khung ${esc(sc.fixedWindow || '')} · ${manN} thủ công / Excel</div></div>
    </div>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title">Tuân thủ lịch theo từng đêm</div>
        <p class="panel-sub">Mỗi đêm backup tính từ ${sc.cutoffHour}:00 hôm đó đến trước ${sc.cutoffHour}:00 hôm sau — job chạy qua nửa đêm (20:00 → 04:00) thuộc đêm bắt đầu. Đạt = mọi client có bản backup thành công, đúng loại (Full / Incremental), xong trước giờ kết thúc + ${sc.graceMinutes} phút. Di chuột lên ô để xem chi tiết.</p></div></div>
      <div class="bk-legend">${legend}</div>
      <div class="table-scroll"><table class="data-table bk-sched-table"><thead><tr><th>Hệ thống / policy</th><th>Lịch</th>${head}<th>Tuân thủ</th><th>RPO</th><th></th></tr></thead>
      <tbody>${rows || `<tr><td colspan="${nights.length + 5}" class="empty-note">Chưa có lịch nào — đồng bộ NetBackup (lịch đọc tự động từ policy) hoặc khai báo lịch bên dưới.</td></tr>`}</tbody></table></div>
      ${unm.length ? `<div class="row-count">Policy chưa có lịch (không có schedule Full / Incremental có cửa sổ, hoặc đã tắt tự đọc): ${esc(unm.slice(0, 20).join(', '))}${unm.length > 20 ? '…' : ''}</div>` : ''}
    </section>
    ${bkScheduleEditor()}`;
}

function bkScheduleEditor() {
  const st = bkState();
  const ed = st.sched;
  if (!ed) {
    return `<section class="table-card"><div class="panel-head"><div><div class="panel-title">Lịch thủ công / import Excel</div>
      <p class="panel-sub">Mặc định lịch được đọc tự động từ schedule của từng policy NetBackup. Khai báo thủ công khi muốn gom nhiều policy thành một hệ thống, hoặc áp lịch đã duyệt khác với cấu hình NetBackup — lịch thủ công được ưu tiên cho các policy nó bao gồm.</p></div>
      <div class="panel-toolbar"><button class="btn btn-secondary btn-sm" type="button" data-bk-action="sched-open"><i data-lucide="calendar-cog"></i>Quản lý lịch thủ công</button></div></div></section>`;
  }
  const dayBoxes = (i, field, days) => BK_DAYS.map((d, k) => `<label class="bk-day"><input type="checkbox" data-sched="${i}" data-field="${field}" data-day="${k}" ${days.includes(k) ? 'checked' : ''}/>${d}</label>`).join('');
  const rows = ed.rules.map((r, i) => `<tr>
    <td><input type="text" data-sched="${i}" data-field="system" value="${esc(r.system)}" placeholder="Tên hệ thống"/></td>
    <td><input type="text" data-sched="${i}" data-field="policyPatterns" value="${esc(r.policyPatterns || '')}" placeholder="vd P-CORE-EQ-*, VM-EQUITY"/>
      <span class="muted-sub bk-wrap">${(r.policies || []).length ? esc(r.policies.join(', ')) : '<b class="tone-warn">Chưa khớp policy nào</b>'}</span></td>
    <td class="bk-days">${dayBoxes(i, 'fullDays', r.fullDays)}</td><td class="bk-days">${dayBoxes(i, 'incrDays', r.incrDays)}</td>
    <td><input type="text" class="bk-hhmm" data-sched="${i}" data-field="startTime" value="${esc(r.startTime)}" placeholder="20:00" maxlength="5"/></td>
    <td><input type="text" class="bk-hhmm" data-sched="${i}" data-field="endTime" value="${esc(r.endTime)}" placeholder="04:00" maxlength="5"/></td>
    <td><input type="number" min="0" data-sched="${i}" data-field="retentionDays" value="${r.retentionDays ?? ''}" style="width:70px"/></td>
    <td><input type="checkbox" data-sched="${i}" data-field="enabled" ${r.enabled ? 'checked' : ''}/></td>
    <td><button class="btn btn-ghost btn-sm" type="button" data-bk-action="sched-del" data-i="${i}">Xoá</button></td></tr>`).join('');
  return `<section class="table-card">
    <div class="panel-head"><div><div class="panel-title">Lịch thủ công / import Excel</div>
      <p class="panel-sub">Cột “Policy”: tên policy NetBackup thuộc hệ thống, phân cách bằng dấu phẩy, dùng * cho mẫu tên. Import file theo mẫu HSC-Backup-Management (tên hệ thống, Monday…Sunday, Full Backup, Start time / End Time, Retention): hệ thống đã có giữ nguyên phần gán policy.</p></div>
      <div class="panel-toolbar">
        <label class="btn btn-secondary btn-sm"><i data-lucide="upload"></i>Import Excel<input type="file" accept=".xlsx" data-bk-action="sched-import" hidden/></label>
        <button class="btn btn-secondary btn-sm" type="button" data-bk-action="sched-add">Thêm hệ thống</button>
        <button class="btn btn-ghost btn-sm" type="button" data-bk-action="sched-close">Đóng</button>
        <button class="btn btn-primary btn-sm" type="button" data-bk-action="sched-save">Lưu lịch</button>
      </div></div>
    ${st.schedMsg ? `<p class="logo-upload-hint">${esc(st.schedMsg)}</p>` : ''}
    <div class="table-scroll"><table class="data-table bk-sched-edit"><thead><tr><th>Hệ thống</th><th>Policy</th><th>Ngày Full</th><th>Ngày Incremental</th><th>Bắt đầu</th><th>Kết thúc</th><th>Giữ Full (ngày)</th><th>Bật</th><th></th></tr></thead>
    <tbody>${rows || '<tr><td colspan="9" class="empty-note">Chưa có lịch thủ công — đang dùng lịch đọc từ NetBackup.</td></tr>'}</tbody></table></div>
    ${ed.unmappedPolicies && ed.unmappedPolicies.length ? `<div class="row-count">Policy chưa thuộc lịch thủ công nào: ${esc(ed.unmappedPolicies.slice(0, 30).join(', '))}</div>` : ''}
  </section>`;
}

async function bkScheduleOpen() {
  const st = bkState();
  try { st.sched = await api.bkSchedules(); st.schedMsg = ''; }
  catch (err) { bkSetStatus(`Không tải được lịch: ${err.message}`, true); return; }
  renderBackupBody();
}

function bkScheduleCollect() {
  const st = bkState();
  return st.sched.rules.map(r => ({ system: r.system, policyPatterns: r.policyPatterns || '', fullDays: r.fullDays, incrDays: r.incrDays,
    startTime: r.startTime, endTime: r.endTime, retentionDays: r.retentionDays === '' ? null : r.retentionDays, enabled: r.enabled, note: r.note || null }));
}

function bkStorage(s) {
  const pools = (s.storage || []).map(p => `<tr><td><b>${esc(p.name)}</b><span class="muted-sub">${esc(p.nbuKey)}</span></td><td>${esc(p.type || '—')}</td><td>${esc(p.state || '—')}</td>
    <td class="num">${bkNum(p.totalTB, 1)}</td><td class="num">${bkNum(p.usedTB, 1)}</td><td class="num">${bkNum(p.freeTB, 1)}</td>
    <td style="min-width:120px;"><div class="bar"><span style="width:${Math.min(100, p.usedPct || 0)}%;background:var(--${p.status === 'crit' ? 'crit' : p.status === 'warn' ? 'warn' : 'ok'})"></span></div><div class="bar-label"><span>${bkPct(p.usedPct)}</span><span>HWM ${bkNum(p.highWaterPct)}%</span></div></td>
    <td class="num">${p.growthTBPerDay !== null && p.growthTBPerDay !== undefined ? (p.growthTBPerDay >= 0 ? '+' : '') + bkNum(p.growthTBPerDay, 2) : '—'}</td>
    <td class="num">${p.daysToFull === null || p.daysToFull === undefined ? '—' : (p.daysToFull > 365 ? '> 1 năm' : bkNum(p.daysToFull))}</td><td>${bkPill(p.status, p.statusLabel)}</td></tr>`).join('');
  const pols = (s.policies || []).map(p => `<tr><td><b>${esc(p.name)}</b><span class="muted-sub">${esc(p.nbuKey)}</span></td><td>${esc(p.policyType || '—')}</td>
    <td>${p.active === true ? 'Bật' : p.active === false ? 'Tắt' : '—'}</td><td class="num">${bkNum(p.clients)}</td><td>${esc(p.storage || '—')}</td>
    <td class="wrap">${esc(p.schedules || '—')}</td><td>${bkDate(p.lastRunAt)}</td><td class="num">${bkNum(p.jobs)}</td><td class="num">${p.failed ? `<b class="tone-crit">${p.failed}</b>` : 0}</td>
    <td class="num">${bkPct(p.successRate)}</td><td>${bkPill(p.status, p.statusLabel)}</td></tr>`).join('');
  return `
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title">Disk pool</div><p class="panel-sub">Cảnh báo từ ${bkNum(s.thresholds.storage_warn_pct)}%, nghiêm trọng từ ${bkNum(s.thresholds.storage_crit_pct)}% hoặc khi dự kiến chạm ngưỡng high-water (HWM) trong ${s.thresholds.storage_days_warn} ngày theo xu hướng 30 ngày.</p></div></div>
      <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Disk pool</th><th>Loại</th><th>Trạng thái</th><th>Tổng (TB)</th><th>Đã dùng</th><th>Còn trống</th><th>% dùng</th><th>TB/ngày</th><th>Ngày tới HWM</th><th>Đánh giá</th></tr></thead>
      <tbody>${pools || '<tr><td colspan="10" class="empty-note">Chưa có dữ liệu disk pool (cần quyền xem Storage).</td></tr>'}</tbody></table></div>
      <div class="chart-wrap" style="margin-top:var(--space-4);min-height:240px;"><canvas id="bkChartStorage"></canvas></div>
    </section>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title">Policy backup</div><p class="panel-sub">Policy đang bật mà không có job trong 48 giờ được đánh giá “Không chạy”. Số client của policy VMware lấy theo VM thực tế được backup.</p></div></div>
      <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Policy</th><th>Loại</th><th>Hoạt động</th><th>Client</th><th>Storage</th><th>Lịch (retention)</th><th>Chạy gần nhất</th><th>Job</th><th>Lỗi</th><th>Tỷ lệ</th><th>Đánh giá</th></tr></thead>
      <tbody>${pols || '<tr><td colspan="11" class="empty-note">Chưa có dữ liệu policy (cần quyền xem Policy).</td></tr>'}</tbody></table></div>
    </section>`;
}

function bkConfig() {
  const st = bkState();
  const servers = (st.servers || []).map(s => `<tr><td><b>${esc(s.name)}</b><span class="muted-sub">${esc(s.key)}</span></td><td>${esc(s.host)}:${s.port}</td>
    <td>${s.demo ? bkPill('na', 'Demo') : (s.auth === 'apikey' ? 'API key' : s.auth === 'login' ? 'Tài khoản (login)' : bkPill('crit', 'Thiếu thông tin'))}</td><td>${esc(s.apiVersion)}</td>
    <td>${s.lastSyncAt ? bkDate(s.lastSyncAt) : '—'}</td>
    <td class="wrap">${s.lastStatus === 'error' ? bkPill('crit', 'Lỗi') + `<span class="muted-sub bk-wrap">${esc(s.lastError)}</span>` : s.lastStatus === 'ok' ? bkPill('ok', 'OK') + (s.warning ? `<span class="muted-sub bk-wrap">${esc(s.warning)}</span>` : '') : bkPill('na', 'Chưa đồng bộ')}</td>
    <td><button class="btn btn-secondary btn-sm" type="button" data-bk-sync="${esc(s.key)}">Đồng bộ</button></td></tr>`).join('');
  const t = st.thresholds;
  const form = t ? t.meta.map(m => {
    const v = t.values[m.key], d = t.defaults[m.key];
    const isText = typeof d === 'string';
    const input = m.key === 'schedule_mode'
      ? `<select id="bkTh_${m.key}" data-bk-th="${m.key}"><option value="netbackup" ${v === 'netbackup' ? 'selected' : ''}>Đọc lịch từ policy NetBackup (khung cố định khi không có)</option><option value="fixed" ${v === 'fixed' ? 'selected' : ''}>Khung cố định cho mọi policy</option></select>`
      : `<input id="bkTh_${m.key}" data-bk-th="${m.key}" type="${isText ? 'text' : 'number'}" ${isText ? '' : 'min="0" step="any"'} value="${esc(String(v))}"/>`;
    return `<div class="field"><label for="bkTh_${m.key}">${esc(m.label)}${m.unit ? ` (${esc(m.unit)})` : ''}</label>
      ${input}
      <span class="muted-sub">Mặc định: ${esc(String(d))}</span></div>`;
  }).join('') : '<div class="empty-note">Đang tải…</div>';
  return `
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title">Máy chủ backup</div><p class="panel-sub">Thêm / sửa máy chủ, thông tin đăng nhập và API key ở <a href="#/settings/sources" data-nav-link="set-sources">Cài đặt → Kết nối nguồn dữ liệu</a> (cấu hình bằng biến <code>NETBACKUP_i_*</code> vẫn được hỗ trợ). Tự động đồng bộ cùng chu kỳ vCenter.</p></div></div>
      <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Server</th><th>Địa chỉ</th><th>Xác thực</th><th>API</th><th>Đồng bộ gần nhất</th><th>Trạng thái</th><th></th></tr></thead>
      <tbody>${servers || '<tr><td colspan="7" class="empty-note">Chưa có máy chủ backup. Thêm ở Cài đặt → Kết nối nguồn dữ liệu (bật DEMO để chạy thử).</td></tr>'}</tbody></table></div>
      <div class="insight-box"><strong>Tài khoản chỉ đọc:</strong> tạo user dịch vụ trên NetBackup (ví dụ <code>svc_nbu_ro</code>), gán RBAC role tuỳ chỉnh chỉ có quyền <b>View</b> cho Jobs, Policies (NetBackup policies), Storage (disk pools / storage units) — không cấp quyền thao tác. Ưu tiên cấp <b>API key</b> cho user này (NetBackup 9.x trở lên) và khai báo ở <code>NETBACKUP_i_API_KEY</code>; app chỉ gọi các API GET. Chi tiết trong README.</div>
    </section>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title">Ngưỡng giám sát backup</div><p class="panel-sub">Áp dụng cho trang Backup, báo cáo PDF và Excel. Lưu trên server, dùng chung cho mọi người dùng.</p></div>
        <div class="panel-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-bk-action="th-reset">Về mặc định</button><button class="btn btn-primary btn-sm" type="button" data-bk-action="th-save">Lưu ngưỡng</button></div></div>
      <div class="bk-th-grid">${form}</div>
    </section>`;
}

function renderBackupBody() {
  const st = bkState();
  const body = $('#bkBody');
  if (!body) return;
  bkDestroyCharts();
  if (st.sub === 'config') { body.innerHTML = bkConfig(); refreshIcons(); return; }
  if (st.loading && !st.summary) { body.innerHTML = '<div class="empty-note">Đang tải dữ liệu backup…</div>'; return; }
  if (st.error) { body.innerHTML = `<section class="table-card"><div class="empty-note tone-crit">Không tải được dữ liệu backup: ${esc(st.error)}</div></section>`; return; }
  const s = st.summary;
  if (!s) { body.innerHTML = '<div class="empty-note">Đang tải dữ liệu backup…</div>'; return; }
  if (!(st.servers || []).length) {
    body.innerHTML = `<section class="table-card"><div class="empty-state">Chưa có máy chủ backup. Thêm máy chủ ở <a href="#/settings/sources" data-nav-link="set-sources">Cài đặt → Kết nối nguồn dữ liệu</a> (bật DEMO để chạy thử).</div></section>`;
    return;
  }
  const html = { overview: bkOverview, clients: bkClients, coverage: bkCoverage, storage: bkStorage, schedule: bkSchedule }[st.sub];
  body.innerHTML = st.sub === 'jobs' ? bkJobs() : html(s);
  body.querySelectorAll('input[data-tri="some"]').forEach(i => { i.indeterminate = true; });
  refreshIcons();
  if (st.sub === 'overview') bkChartDaily(s);
  if (st.sub === 'storage') bkChartStorage(s);
  if (st.sub === 'jobs' && !st.jobs) bkLoadJobs();
}

function bkChartDaily(s) {
  if (typeof Chart === 'undefined') return;
  const el = $('#bkChartDaily'); if (!el) return;
  const c = chartPalette();
  const labels = s.daily.map(d => `${d.date.slice(8, 10)}/${d.date.slice(5, 7)}`);
  bkState().charts.daily = new Chart(el, {
    type: 'bar',
    data: { labels, datasets: [
      { label: 'Thành công', data: s.daily.map(d => d.success), backgroundColor: c.ok, stack: 'j', order: 1 },
      { label: 'Một phần', data: s.daily.map(d => d.partial), backgroundColor: c.warn, stack: 'j', order: 1 },
      { label: 'Lỗi', data: s.daily.map(d => d.failed), backgroundColor: c.crit, stack: 'j', order: 1 },
      { label: 'Dữ liệu (TB)', data: s.daily.map(d => d.tb), type: 'line', yAxisID: 'tb', order: 0, borderColor: c.primary, backgroundColor: c.primary, pointRadius: 2, tension: .3 },
    ] },
    options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: 'index', intersect: false },
      plugins: { legend: { labels: { color: c.muted, boxWidth: 10 } } },
      scales: { x: { stacked: true, ticks: { color: c.muted }, grid: { display: false } },
        y: { stacked: true, beginAtZero: true, ticks: { color: c.muted }, grid: { color: c.divider }, title: { display: true, text: 'Job', color: c.muted } },
        tb: { position: 'right', beginAtZero: true, ticks: { color: c.muted }, grid: { display: false }, title: { display: true, text: 'TB', color: c.muted } } } },
  });
}

function bkChartStorage(s) {
  if (typeof Chart === 'undefined') return;
  const el = $('#bkChartStorage'); const tr = s.storageTrend;
  if (!el || !tr || tr.dates.length < 2) return;
  const c = chartPalette();
  const colors = [c.primary, c.accent, '#7c3aed', '#ca8a04', '#db2777', c.muted];
  bkState().charts.storage = new Chart(el, {
    type: 'line',
    data: { labels: tr.dates.map(d => `${d.slice(8, 10)}/${d.slice(5, 7)}`), datasets: tr.series.map((x, i) => ({ label: x.name, data: x.values, borderColor: colors[i % colors.length], backgroundColor: colors[i % colors.length], pointRadius: 0, tension: .2, spanGaps: true }))
      .concat([{ label: `Nghiêm trọng ${s.thresholds.storage_crit_pct}%`, data: tr.dates.map(() => s.thresholds.storage_crit_pct), borderColor: c.crit, borderDash: [5, 4], pointRadius: 0, borderWidth: 1 }]) },
    options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { labels: { color: c.muted, boxWidth: 10 } }, title: { display: true, text: 'Mức dùng disk pool 30 ngày (%)', color: c.muted } },
      scales: { x: { ticks: { color: c.muted, maxTicksLimit: 10 }, grid: { display: false } }, y: { min: 0, max: 100, ticks: { color: c.muted }, grid: { color: c.divider } } } },
  });
}

function bindBackupBody() {
  const body = $('#bkBody');
  if (!body || body.dataset.bound) return;
  body.dataset.bound = '1';
  const st = bkState();
  let qTimer = null;
  body.addEventListener('input', e => {
    if (e.target.matches('[data-sc-q]') && st.scopeEdit) {
      st.scopeEdit.q = e.target.value;
      clearTimeout(qTimer);
      qTimer = setTimeout(bkScopeRerender, 200);
      return;
    }
    const k = e.target.dataset.bkFilter;
    if (!k || !['clientQ', 'covQ'].includes(k)) { if (k === 'jobQ') st.jobFilter.q = e.target.value; return; }
    if (k === 'clientQ') st.clientFilter.q = e.target.value; else st.covFilter.q = e.target.value;
    clearTimeout(qTimer);
    qTimer = setTimeout(() => {
      const pos = e.target.selectionStart;
      renderBackupBody();
      const inp = body.querySelector(`[data-bk-filter="${k}"]`);
      if (inp) { inp.focus(); try { inp.setSelectionRange(pos, pos); } catch { /* ignore */ } }
    }, 250);
  });
  body.addEventListener('change', async e => {
    const t = e.target;
    if (t.dataset.sched !== undefined && st.sched) {
      const r = st.sched.rules[Number(t.dataset.sched)];
      const f = t.dataset.field;
      if (f === 'fullDays' || f === 'incrDays') {
        const k = Number(t.dataset.day);
        r[f] = t.checked ? [...new Set([...r[f], k])].sort() : r[f].filter(x => x !== k);
        if (t.checked) { const other = f === 'fullDays' ? 'incrDays' : 'fullDays'; r[other] = r[other].filter(x => x !== k); renderBackupBody(); }
      } else if (f === 'enabled') r.enabled = t.checked;
      else if (f === 'retentionDays') r.retentionDays = t.value === '' ? '' : Number(t.value);
      else r[f] = t.value;
      return;
    }
    if (t.dataset.bkAction === 'sched-import' && t.files && t.files[0]) {
      try {
        st.sched = await api.bkSchedulesImport(t.files[0]);
        st.schedMsg = `Đã import ${st.sched.imported.length} hệ thống: ${st.sched.imported.join(', ')}. Gán policy cho từng hệ thống rồi bấm Lưu lịch.`;
        await bkLoad(true);
      } catch (err) { st.schedMsg = `Import thất bại: ${err.message}`; }
      renderBackupBody();
      return;
    }
    if (st.scopeEdit && (t.matches('[data-sc-mode]') || t.matches('[data-sc-vc]') || t.matches('[data-sc-cl]') || t.matches('[data-sc-host]'))) {
      if (t.matches('[data-sc-mode]')) st.scopeEdit.mode = t.value;
      else if (t.dataset.scVc) bkScopeSetMany(bkScopeKeysOf(t.dataset.scVc), t.checked);
      else if (t.dataset.scCl) { const [vc, cl] = t.dataset.scCl.split(BK_SEP); bkScopeSetMany(bkScopeKeysOf(vc, cl), t.checked); }
      else bkScopeSetMany([t.dataset.scHost], t.checked);
      bkScopeRerender();
      return;
    }
    const k = t.dataset.bkFilter;
    if (k === 'covVc') { st.covFilter.vc = t.value; st.covFilter.cl = 'all'; renderBackupBody(); return; }
    if (k === 'covCl') { st.covFilter.cl = t.value; renderBackupBody(); return; }
    if (k === 'clientStatus') { st.clientFilter.status = e.target.value; renderBackupBody(); }
    else if (k === 'covState') { st.covFilter.state = e.target.value; renderBackupBody(); }
    else if (k === 'jobOutcome') { st.jobFilter.outcome = e.target.value; bkLoadJobs(); }
    else if (k === 'jobCategory') { st.jobFilter.category = e.target.value; bkLoadJobs(); }
  });
  body.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.dataset.bkFilter === 'jobQ') { st.jobFilter.q = e.target.value; bkLoadJobs(); } });
  body.addEventListener('click', async e => {
    const tg = e.target.closest('[data-sc-toggle]');
    if (tg && st.scopeEdit) {
      const o = st.scopeEdit.open;
      o.has(tg.dataset.scToggle) ? o.delete(tg.dataset.scToggle) : o.add(tg.dataset.scToggle);
      bkScopeRerender();
      return;
    }
    const a = e.target.closest('[data-bk-action]');
    const syncBtn = e.target.closest('[data-bk-sync]');
    if (syncBtn) {
      syncBtn.disabled = true; syncBtn.textContent = 'Đang đồng bộ...';
      try { await api.bkSync(syncBtn.dataset.bkSync); bkSetStatus(`Đã đồng bộ ${syncBtn.dataset.bkSync}.`); }
      catch (err) { bkSetStatus(`Đồng bộ ${syncBtn.dataset.bkSync} thất bại: ${err.message}`, true); }
      await bkLoad(true); renderBackupView();
      return;
    }
    if (!a) return;
    const act = a.dataset.bkAction;
    if (act === 'sched-open') { await bkScheduleOpen(); return; }
    if (act === 'sched-close') { st.sched = null; renderBackupBody(); return; }
    if (act === 'sched-add') { st.sched.rules.push({ system: '', policyPatterns: '', fullDays: [4], incrDays: [0, 1, 2, 3, 5, 6], startTime: '20:00', endTime: '04:00', retentionDays: 14, enabled: true, policies: [] }); renderBackupBody(); return; }
    if (act === 'sched-del') { st.sched.rules.splice(Number(a.dataset.i), 1); renderBackupBody(); return; }
    if (act === 'sched-save') {
      a.disabled = true;
      try {
        st.sched = await api.bkSchedulesSave(bkScheduleCollect());
        st.schedMsg = 'Đã lưu lịch.';
        await bkLoad(true);
      } catch (err) { st.schedMsg = `Không lưu được: ${err.message}`; }
      renderBackupBody();
      return;
    }
    if (act === 'scope-edit') {
      try { st.scope = await api.bkScope(); } catch (err) { bkSetStatus(`Không tải được phạm vi: ${err.message}`, true); return; }
      bkScopeStartEdit(); renderBackupBody(); bkScopeRerender(); return;
    }
    if (act === 'scope-cancel') { st.scopeEdit = null; renderBackupBody(); return; }
    if (act === 'scope-clear') { st.scopeEdit.checked.clear(); bkScopeRerender(); return; }
    if (act === 'scope-select-visible') {
      const q = st.scopeEdit.q.trim().toLowerCase();
      (st.scope.tree || []).forEach(v => v.clusters.forEach(c => {
        if (!q || `${v.vcenterKey} ${v.name} ${c.name}`.toLowerCase().includes(q)) bkScopeSetMany(bkScopeKeysOf(v.vcenterKey, c.name), true);
      }));
      bkScopeRerender(); return;
    }
    if (act === 'scope-save') {
      const rules = bkScopeRules();
      if (st.scopeEdit.mode === 'selected' && !rules.length) { bkSetStatus('Chưa chọn vCenter / cluster / host nào cho phạm vi backup.', true); return; }
      a.disabled = true;
      try {
        st.scope = await api.bkScopeSave(rules);
        st.scopeEdit = null;
        bkSetStatus(rules.length ? `Đã lưu phạm vi cần backup: ${st.scope.label}.` : 'Đã lưu: tính độ phủ trên toàn bộ hệ thống.');
        await bkLoad(true);
        renderBackupBody();
      } catch (err) { bkSetStatus(`Không lưu được phạm vi: ${err.message}`, true); a.disabled = false; }
      return;
    }
    if (a.dataset.bkAction === 'jobs-search') { const inp = body.querySelector('[data-bk-filter="jobQ"]'); st.jobFilter.q = inp ? inp.value : ''; bkLoadJobs(); }
    else if (a.dataset.bkAction === 'th-save' || a.dataset.bkAction === 'th-reset') {
      const payload = {};
      body.querySelectorAll('[data-bk-th]').forEach(inp => {
        const key = inp.dataset.bkTh;
        const def = st.thresholds.defaults[key];
        if (a.dataset.bkAction === 'th-reset') payload[key] = def;
        else payload[key] = typeof def === 'string' ? inp.value : Number(inp.value);
      });
      try {
        st.thresholds = await api.bkThresholdsSave(payload);
        bkSetStatus('Đã lưu ngưỡng giám sát backup.');
        await bkLoad(true);
        renderBackupBody();
      } catch (err) { bkSetStatus(`Không lưu được ngưỡng: ${err.message}`, true); }
    }
  });
}

async function renderBackupView() {
  const st = bkState();
  if (!st.servers) await bkLoadServers();
  renderBackupHead();
  bindBackupHead();
  bindBackupBody();
  if (st.sub === 'config') {
    if (!st.thresholds) { try { st.thresholds = await api.bkThresholds(); } catch (err) { bkSetStatus(err.message, true); } }
    renderBackupBody();
    return;
  }
  // Reload when the data is older than BK_TTL_MS or was invalidated (vCenter / NetBackup sync,
  // scheduler poll): coverage depends on the vCenter inventory, which changes outside this page.
  const stale = !st.summary || (Date.now() - (st.loadedAt || 0)) > BK_TTL_MS;
  if (stale) { renderBackupBody(); await bkLoad(true); }
  renderBackupBody();
}

/* Called after a vCenter sync elsewhere in the app: the next visit (or the current page) reloads. */
function bkInvalidate() {
  if (!appState.backup) return;
  const st = bkState();
  st.loadedAt = 0;
  st.scope = null;
  if (appState.view === 'backup') renderBackupView();
}
