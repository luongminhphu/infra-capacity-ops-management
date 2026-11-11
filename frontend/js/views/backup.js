/* =========================================================
   Backup — Veritas NetBackup monitoring module (read-only REST API).
   Sub-pages: Tổng quan · Client & RPO · Job backup · Độ phủ VM ·
   Storage & policy · Nguồn & ngưỡng. All analysis runs on the backend
   (backend/app/backup/analysis.py); this file renders and edits thresholds.
   Scope: NetBackup server + period selected in the page header.
   ========================================================= */

const BK_STATUS = { ok: 'Đạt', warn: 'Cần theo dõi', crit: 'Cần xử lý', na: 'Không hoạt động' };
const BK_SEV = { critical: { cls: 'crit', label: 'Nghiêm trọng' }, warning: { cls: 'warn', label: 'Cảnh báo' }, info: { cls: 'na', label: 'Lưu ý' } };
const BK_OUTCOME = { success: { cls: 'ok', label: 'Thành công' }, partial: { cls: 'warn', label: 'Một phần' }, failed: { cls: 'crit', label: 'Lỗi' }, running: { cls: 'na', label: 'Đang chạy' } };
const BK_READY = { ok: { cls: 'ok', label: 'Đạt' }, warn: { cls: 'warn', label: 'Cần theo dõi' }, crit: { cls: 'crit', label: 'Cần xử lý' } };

function bkState() {
  if (!appState.backup) {
    appState.backup = {
      sub: 'overview', server: 'all', days: 7, servers: null, summary: null, jobs: null, thresholds: null,
      loading: false, error: null, bound: false, charts: {},
      clientFilter: { status: 'all', q: '' }, jobFilter: { outcome: 'failed', category: 'backup', q: '' }, covFilter: { state: 'unprotected', q: '' },
      lastKey: null,
    };
  }
  return appState.backup;
}

const bkNum = (v, d = 0) => (v === null || v === undefined || !isFinite(v)) ? '—' : Number(v).toLocaleString('vi-VN', { maximumFractionDigits: d, minimumFractionDigits: d });
const bkPct = (v, d = 1) => (v === null || v === undefined || !isFinite(v)) ? '—' : `${bkNum(v, d)}%`;
const bkDate = (iso) => { if (!iso) return '—'; const d = new Date(iso); return isNaN(d) ? esc(iso) : d.toLocaleString('vi-VN', { hour12: false, hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' }); };
const bkPill = (cls, label) => `<span class="pill ${cls}">${esc(label)}</span>`;
const bkHours = (h) => (h === null || h === undefined) ? '—' : (h >= 48 ? `${bkNum(h / 24, 1)} ngày` : `${bkNum(h, 0)} giờ`);

function bkSetStatus(msg, isErr) {
  const el = $('#bkStatus');
  if (!el) return;
  el.textContent = msg || '';
  el.classList.toggle('hidden', !msg);
  el.classList.toggle('tone-crit', !!isErr);
}

async function bkLoadServers() {
  const st = bkState();
  try { st.servers = await api.bkServers(); } catch (err) { st.servers = []; bkSetStatus(`Không tải được danh sách NetBackup: ${err.message}`, true); }
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
  const opts = [`<option value="all">Tất cả NetBackup (${servers.length})</option>`]
    .concat(servers.map(s => `<option value="${esc(s.key)}">${esc(s.name)}${s.demo ? ' · demo' : ''}</option>`)).join('');
  const chips = servers.map(s => {
    const cls = s.lastStatus === 'error' ? 'crit' : (s.lastStatus === 'ok' ? 'ok' : 'na');
    const t = s.lastStatus === 'error' ? `Lỗi: ${s.lastError}` : (s.lastSyncAt ? `Đồng bộ ${bkDate(s.lastSyncAt)}` : 'Chưa đồng bộ');
    return `<span class="pill ${cls}" title="${esc(t)}${s.warning ? ' · ' + esc(s.warning) : ''}"><span class="pill-dot"></span>${esc(s.name)} · ${esc(t)}</span>`;
  }).join(' ');
  head.innerHTML = `
    <div class="lic-head-row">
      <div class="panel-toolbar">
        <select id="bkServer" title="NetBackup primary server">${opts}</select>
        <select id="bkDays" title="Kỳ thống kê">
          ${[1, 7, 14, 30, 60, 90].map(d => `<option value="${d}" ${d === st.days ? 'selected' : ''}>${d === 1 ? '24 giờ qua' : d + ' ngày qua'}</option>`).join('')}
        </select>
        <span class="bk-chips">${chips || '<span class="pill na">Chưa cấu hình NetBackup</span>'}</span>
      </div>
      <div class="panel-toolbar">
        <button class="btn btn-secondary btn-sm" id="bkSync" type="button"><i data-lucide="refresh-cw"></i>Đồng bộ NetBackup</button>
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
        bkSetStatus(bad.length ? `Đồng bộ lỗi: ${bad.map(r => `${r.key}: ${r.error}`).join('; ')}` : `Đã đồng bộ NetBackup lúc ${new Date().toLocaleTimeString('vi-VN')}.`, bad.length > 0);
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
      <div class="panel-head"><div><div class="panel-title">Job backup</div><p class="panel-sub">Activity monitor của NetBackup trong kỳ đã chọn (lưu lịch sử theo mỗi lần đồng bộ).</p></div>
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
  if (!cov.available) return '<section class="table-card"><div class="empty-note">Chưa có inventory vCenter để đối chiếu. Đồng bộ vCenter (nút “Đồng bộ ngay”) rồi tải lại trang này.</div></section>';
  const f = bkState().covFilter;
  const q = f.q.trim().toLowerCase();
  const vms = (f.state === 'unprotected' ? cov.unprotectedVms : f.state === 'failing' ? cov.failingVms : cov.unprotectedVms.concat(cov.failingVms))
    .filter(v => !q || `${v.vm} ${v.cluster} ${v.ip || ''}`.toLowerCase().includes(q));
  const byCl = cov.byCluster.map(c => `<tr><td>${esc(c.vcenterKey)}</td><td><b>${esc(c.cluster)}</b></td><td class="num">${c.total}</td><td class="num">${c.protected}</td>
    <td class="num">${c.failing || 0}</td><td class="num">${c.unprotected ? `<b class="tone-crit">${c.unprotected}</b>` : 0}</td><td class="num">${bkPct(c.pct)}</td></tr>`).join('');
  const rows = vms.map(v => `<tr><td><b>${esc(v.vm)}</b></td><td>${esc(v.vcenterKey || '')}</td><td>${esc(v.cluster || '')}</td><td>${esc(v.ip || '—')}</td>
    <td class="num">${bkNum(v.vcpu)}</td><td class="num">${bkNum(v.ramGB)}</td><td class="num">${bkNum(v.diskGB)}</td>
    <td>${v.state === 'unprotected' ? bkPill('crit', 'Chưa backup') : bkPill('warn', 'Có job nhưng lỗi')}</td></tr>`).join('');
  const k = [['VM đang chạy', bkNum(cov.total), `Không tính ${cov.poweredOff} VM tắt · ${cov.excluded} VM loại trừ theo tên`],
             ['Có backup', bkNum(cov.protected), `Thành công trong ${s.thresholds.coverage_days} ngày · ${bkPct(cov.pct)}`],
             ['Có job nhưng lỗi', bkNum(cov.failing), 'Có trong policy / có job, chưa thành công'],
             ['Chưa có backup', `<span class="${cov.unprotected ? 'tone-crit' : 'tone-ok'}">${bkNum(cov.unprotected)}</span>`, 'Không khớp client NetBackup nào']];
  return `
    <div class="kpi-grid">${k.map(([l, v, sub]) => `<div class="kpi-card"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${sub}</div></div>`).join('')}</div>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title">Độ phủ theo cluster</div><p class="panel-sub">Đối chiếu VM trong inventory vCenter với client NetBackup theo tên VM, hostname guest hoặc IP.</p></div></div>
      <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>vCenter</th><th>Cluster</th><th>VM</th><th>Có backup</th><th>Lỗi</th><th>Chưa backup</th><th>Độ phủ</th></tr></thead><tbody>${byCl}</tbody></table></div>
    </section>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title">VM cần xác nhận backup</div><p class="panel-sub">Xác nhận với System Owner: bổ sung vào policy / query VMware hoặc ghi nhận ngoại lệ (thêm từ khoá loại trừ ở trang Nguồn &amp; ngưỡng).</p></div>
        <div class="panel-toolbar">
          <select data-bk-filter="covState">${[['unprotected', 'Chưa backup'], ['failing', 'Có job nhưng lỗi'], ['all', 'Cả hai']].map(([kk, l]) => `<option value="${kk}" ${f.state === kk ? 'selected' : ''}>${l}</option>`).join('')}</select>
          <span class="search-box"><i data-lucide="search"></i><input type="text" data-bk-filter="covQ" placeholder="Tìm VM / cluster / IP" value="${esc(f.q)}"/></span>
        </div></div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>VM</th><th>vCenter</th><th>Cluster</th><th>IP</th><th>vCPU</th><th>RAM (GB)</th><th>Disk (GB)</th><th>Tình trạng</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="8" class="empty-note">Không có VM nào.</td></tr>'}</tbody></table></div>
      <div class="row-count">${vms.length} VM</div>
    </section>`;
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
    return `<div class="field"><label for="bkTh_${m.key}">${esc(m.label)}${m.unit ? ` (${esc(m.unit)})` : ''}</label>
      <input id="bkTh_${m.key}" data-bk-th="${m.key}" type="${isText ? 'text' : 'number'}" ${isText ? '' : 'min="0" step="any"'} value="${esc(String(v))}"/>
      <span class="muted-sub">Mặc định: ${esc(String(d))}</span></div>`;
  }).join('') : '<div class="empty-note">Đang tải…</div>';
  return `
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title">Nguồn NetBackup</div><p class="panel-sub">Cấu hình qua biến môi trường <code>NETBACKUP_i_*</code> (tối đa 4 primary server). Tự động đồng bộ cùng chu kỳ vCenter (POLL_INTERVAL_MINUTES).</p></div></div>
      <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Server</th><th>Địa chỉ</th><th>Xác thực</th><th>API</th><th>Đồng bộ gần nhất</th><th>Trạng thái</th><th></th></tr></thead>
      <tbody>${servers || '<tr><td colspan="7" class="empty-note">Chưa cấu hình NetBackup. Thêm khối NETBACKUP_1_* vào .env / ConfigMap (đặt NETBACKUP_1_DEMO=true để chạy thử).</td></tr>'}</tbody></table></div>
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
    body.innerHTML = `<section class="table-card"><div class="empty-state">Chưa cấu hình NetBackup. Khai báo <code>NETBACKUP_1_HOST</code> + <code>NETBACKUP_1_API_KEY</code> (hoặc <code>NETBACKUP_1_DEMO=true</code>) rồi khởi động lại ứng dụng — xem trang <a href="#/bk-config" data-nav-link="bk-config">Nguồn &amp; ngưỡng</a>.</div></section>`;
    return;
  }
  const html = { overview: bkOverview, clients: bkClients, coverage: bkCoverage, storage: bkStorage }[st.sub];
  body.innerHTML = st.sub === 'jobs' ? bkJobs() : html(s);
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
  body.addEventListener('change', e => {
    const k = e.target.dataset.bkFilter;
    if (k === 'clientStatus') { st.clientFilter.status = e.target.value; renderBackupBody(); }
    else if (k === 'covState') { st.covFilter.state = e.target.value; renderBackupBody(); }
    else if (k === 'jobOutcome') { st.jobFilter.outcome = e.target.value; bkLoadJobs(); }
    else if (k === 'jobCategory') { st.jobFilter.category = e.target.value; bkLoadJobs(); }
  });
  body.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.dataset.bkFilter === 'jobQ') { st.jobFilter.q = e.target.value; bkLoadJobs(); } });
  body.addEventListener('click', async e => {
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
  if (!st.summary) { renderBackupBody(); await bkLoad(false); }
  renderBackupBody();
}
