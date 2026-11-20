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
      clientFilter: { status: 'all', q: '' }, clientSel: null, jobFilter: { outcome: 'failed', category: 'backup', q: '' }, covFilter: { state: 'unprotected', q: '', vc: 'all', cl: 'all' },
      scope: null, scopeEdit: null, sched: null, schedMsg: '',
      lastKey: null, loadedAt: 0,
    };
  }
  return appState.backup;
}

const bkNum = (v, d = 0) => uiNumber(v, d, d);
const bkPct = (v, d = 1) => (v === null || v === undefined || !isFinite(v)) ? '—' : `${bkNum(v, d)}%`;
const bkDate = (iso) => esc(uiDateTime(iso, 'short'));
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
    return `${uiPill(cls, `${s.name} · ${t}`, `${t}${s.warning ? ' · ' + s.warning : ''}`)}`;
  }).join(' ');
  head.innerHTML = `
    <div class="lic-head-row">
      <div class="panel-toolbar">
        <select id="bkServer" title="Máy chủ backup">${opts}</select>
        <select id="bkDays" title="Kỳ thống kê">
          ${[1, 7, 14, 30, 60, 90].map(d => `<option value="${d}" ${d === st.days ? 'selected' : ''}>${d === 1 ? '24 giờ qua' : d + ' ngày qua'}</option>`).join('')}
        </select>
        <span class="bk-chips">${chips || uiPill('na', 'Chưa cấu hình máy chủ backup')}</span>
      </div>
      <div class="panel-toolbar">
        ${uiIsAdmin() ? '<button class="btn btn-secondary btn-sm" id="bkSync" type="button"><i data-lucide="refresh-cw"></i>Đồng bộ backup</button>' : ''}
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
  // valueHtml / subHtml: the values above already went through bkPct / bkNum / esc.
  return `<div class="kpi-strip-wrap">${uiKpiStrip(cards.map(([l, v, sub]) => ({ label: l, valueHtml: v, subHtml: sub })))}</div>`;
}

function bkFindingsTable(findings, limit) {
  const rows = (findings || []).slice(0, limit || 999).map(f => `<tr>
    <td>${bkPill(BK_SEV[f.severity].cls, BK_SEV[f.severity].label)}</td><td>${esc(f.area)}</td>
    <td class="wrap"><b>${esc(f.title)}</b>${f.detail ? `<span class="muted-sub bk-wrap">${esc(f.detail)}</span>` : ''}</td>
    <td class="wrap">${esc(f.action)}</td></tr>`).join('');
  return `<div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Mức độ</th><th>Hạng mục</th><th>Phát hiện</th><th>Khuyến nghị</th></tr></thead>
    <tbody>${rows || uiEmptyRow(4, 'Không có phát hiện nào.')}</tbody></table></div>`;
}

/* The rule headline is one run-on line joined with " · " (shared with the PDF): show the first clause as
   the verdict sentence and the rest as points. Display only — the text is unchanged. */
function bkVerdict(s, r) {
  const parts = String(s.headline || '').split(' · ').map(x => x.trim()).filter(Boolean);
  const head = parts.shift() || '';
  return `<section class="verdict tone-rule-${esc(r.cls)}" aria-label="Kết luận backup">
      <div class="verdict-meta">${bkPill(r.cls, r.label)}<span>${esc(s.period ? `${s.period.days} ngày qua` : '')}</span></div>
      <h2>${esc(head.replace(/\.$/, ''))}.</h2>
      ${parts.length ? `<ul class="verdict-points">${parts.map(x => `<li>${esc(x.replace(/\.$/, ''))}</li>`).join('')}</ul>` : ''}
    </section>`;
}

function bkOverview(s) {
  const r = BK_READY[s.readiness];
  const groups = (s.failureGroups || []).map(g => `<tr><td><b>${esc(g.label)}</b><span class="muted-sub bk-wrap">${esc(g.action)}</span></td><td class="num">${bkNum(g.count)}</td><td class="num">${bkNum(g.clientCount)}</td></tr>`).join('');
  const crit = (s.clients || []).filter(c => c.status !== 'ok').slice(0, 8).map(c => `<tr><td><b>${esc(c.client)}</b><span class="muted-sub">${esc(c.policies.join(', '))}</span></td><td class="mono">${esc(c.ip || '—')}</td>
    <td>${bkDate(c.lastSuccessAt)}<span class="muted-sub">${bkHours(c.hoursSinceSuccess)} trước</span></td><td class="wrap">${esc(c.lastStatusText)}</td><td>${bkPill(c.status, c.statusLabel)}</td></tr>`).join('');
  return `
    ${bkVerdict(s, r)}
    ${bkKpis(s)}
    <section class="chart-grid exec-chart-grid">
      <article class="card chart-card">
        <div class="panel-head panel-head-tight"><div><div class="panel-title" role="heading" aria-level="2">Kết quả job backup theo ngày</div><p class="panel-sub">Job ở cấp thực thi (không đếm trùng job cha của VMware / multistream).</p></div></div>
        <div class="chart-wrap chart-wrap-split"><canvas id="bkChartDaily" role="img" aria-label="Số job theo kết quả mỗi ngày"></canvas></div>
        <div class="chart-wrap chart-wrap-mini"><canvas id="bkChartDailyTb" role="img" aria-label="Dung lượng đã backup mỗi ngày (TB)"></canvas></div>
      </article>
      <article class="card chart-card">
        <div class="panel-head panel-head-tight"><div><div class="panel-title" role="heading" aria-level="2">Nguyên nhân lỗi</div><p class="panel-sub">Gom theo đầu mối xử lý.</p></div>
          <div class="panel-toolbar"><button class="btn btn-secondary btn-sm hidden" type="button" data-ai-act="backup" data-ai-gate="backup_analysis">AI phân tích lỗi</button></div></div>
        <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Nhóm</th><th>Job</th><th>Client</th></tr></thead>
        <tbody>${groups || uiEmptyRow(3, 'Không có job lỗi trong kỳ.')}</tbody></table></div>
      </article>
    </section>
    ${typeof bkSizeOverview === 'function' ? bkSizeOverview(s) : ''}
    <section class="table-card ai-out" id="bkAiOut" aria-live="polite"></section>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Client cần xử lý</div><p class="panel-sub">Lỗi liên tiếp, quá RPO hoặc lần gần nhất không thành công.</p></div>
        <div class="panel-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-nav-link="bk-clients">Xem tất cả client</button></div></div>
      <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Client</th><th>IP</th><th>Thành công gần nhất</th><th>Kết quả lần cuối</th><th>Đánh giá</th></tr></thead>
      <tbody>${crit || uiEmptyRow(5, 'Mọi client đều đạt.')}</tbody></table></div>
    </section>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Phát hiện &amp; khuyến nghị</div><p class="panel-sub">Theo các ngưỡng tại trang Nguồn &amp; ngưỡng.</p></div></div>
      ${bkFindingsTable(s.findings)}
    </section>`;
}

/* Client & RPO as list → detail (pages/danh-sach.md): grouped by assessment, worst first; the detail pane
   holds what the 11-column table spread across a row. Same rows / filters / thresholds as before. */
const BK_CL_ORDER = { crit: 0, warn: 1, ok: 2, na: 3 };
function bkClients(s) {
  const st = bkState(), f = st.clientFilter;
  const q = f.q.trim().toLowerCase();
  // The selection is the client NAME (audit FE-20): an array index pointed at another client after a filter / reload.
  const all = (s.clients || []).map((c, i) => ({ ...c, _i: i }));
  const list = all.filter(c => (f.status === 'all' || c.status === f.status) && (!q || `${c.client} ${c.ip || ''} ${c.policies.join(' ')}`.toLowerCase().includes(q)))
    .sort((a, b) => (BK_CL_ORDER[a.status] ?? 9) - (BK_CL_ORDER[b.status] ?? 9));   // stable: keeps server order inside a group
  const cnt = (k) => all.filter(c => k === 'all' || c.status === k).length;
  if (!list.some(c => c.client === st.clientSel)) st.clientSel = list.length ? list[0].client : null;
  const groups = ['crit', 'warn', 'ok', 'na'].map(k => {
    const g = list.filter(c => c.status === k);
    if (!g.length) return '';
    return `<li class="hm-group" role="presentation"><span>${esc(BK_STATUS[k])}</span><span>${g.length}</span></li>` + g.map(c => `
      <li class="hm-row" role="option" id="bkc-${c._i}" data-bk-client="${esc(c.client)}" aria-selected="${c.client === st.clientSel}">
        <span class="hm-lv lv-${esc(c.status)}" aria-hidden="true"></span>
        <span class="hm-t">${esc(c.client)}</span>
        <span class="hm-r"><b>${bkHours(c.hoursSinceSuccess)}</b>${c.consecutiveFail ? `${c.consecutiveFail} lần lỗi liền` : 'từ lần thành công'}</span>
        <span class="hm-m">IP ${esc(c.ip || '—')} · ${esc(c.statusLabel)} · ${esc(c.policies.join(', ') || '—')}</span>
        <span class="sr-only">${esc(BK_STATUS[c.status] || '')}</span></li>`).join('');
  }).join('');
  const sel = list.find(c => c.client === st.clientSel);
  return `
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Client &amp; RPO</div><p class="panel-sub">RPO = thời gian từ bản backup thành công gần nhất (status 0 hoặc 1). Cảnh báo khi quá ${bkNum(s.thresholds.rpo_hours)} giờ; nghiêm trọng khi quá ${bkNum(s.thresholds.rpo_crit_hours)} giờ hoặc lỗi liên tiếp từ ${s.thresholds.consecutive_fail} lần.</p></div>
        <div class="panel-toolbar">
          <select data-bk-filter="clientStatus" aria-label="Lọc theo đánh giá">${['all', 'crit', 'warn', 'ok'].map(k => `<option value="${k}" ${f.status === k ? 'selected' : ''}>${k === 'all' ? 'Tất cả' : BK_STATUS[k]} (${cnt(k)})</option>`).join('')}</select>
          <span class="search-box"><i data-lucide="search"></i><input type="text" data-bk-filter="clientQ" placeholder="Tìm client / IP / policy" aria-label="Tìm client, IP hoặc policy" value="${esc(f.q)}"/></span>
        </div></div>
      <div class="hm-split cp-split">
        <div class="hm-list"><ul class="hm-rows" id="bkClientList" role="listbox" tabindex="0" aria-label="Client backup, nhóm theo đánh giá"${sel ? ` aria-activedescendant="bkc-${sel._i}"` : ''}>${groups || `<li role="presentation">${uiEmpty('Không có client phù hợp.')}</li>`}</ul>
          <p class="hm-hint"><span><kbd>J</kbd> <kbd>K</kbd> chuyển</span><span>${list.length} / ${all.length} client</span></p></div>
        <div class="hm-detail" id="bkClientDetail" aria-live="polite">${bkClientDetail(sel)}</div>
      </div>
    </section>`;
}

function bkClientDetail(c) {
  if (!c) return uiEmpty('Chọn một client ở danh sách bên trái.');
  const code = c.lastStatus !== null && c.lastStatus !== undefined ? `<code>${esc(String(c.lastStatus))}</code> · ` : '';
  const fact = (l, v) => `<div><dt>${l}</dt><dd>${v}</dd></div>`;
  return `
    <div class="hm-d-top">${bkPill(c.status, c.statusLabel)}<h3>${esc(c.client)}</h3></div>
    <div class="hm-d-meta"><span class="mono">IP ${esc(c.ip || '—')}</span><span>${esc(c.policyTypes.join(', ') || '—')}</span><span>${esc(c.policies.join(', ') || '—')}</span></div>
    ${c.note ? `<div class="hm-d-sec"><h4>Tình trạng</h4><p>${esc(c.note)}</p></div>` : ''}
    <div class="hm-d-sec"><h4>Kết quả job gần nhất</h4><p>${code}${esc(c.lastStatusText || '—')}</p></div>
    <dl class="bk-facts">
      ${fact('Job gần nhất', bkDate(c.lastJobAt))}
      ${fact('Thành công gần nhất', bkDate(c.lastSuccessAt))}
      ${fact('Đã qua (RPO)', bkHours(c.hoursSinceSuccess))}
      ${fact('Lỗi liên tiếp', c.consecutiveFail ? String(c.consecutiveFail) : '—')}
      ${fact('Job trong kỳ', `${bkNum(c.jobs)} · lỗi ${bkNum(c.failed)}`)}
      ${fact('Dung lượng', `${bkNum(c.tb, 2)} TB`)}
    </dl>
    <div class="hm-d-actions"><button class="btn btn-secondary btn-sm" type="button" data-bk-action="client-jobs" data-client="${esc(c.client)}"><i data-lucide="list-checks"></i>Xem job của client</button></div>`;
}

function bkClientSelect(name, focus) {
  const st = bkState();
  st.clientSel = name;
  const list = $('#bkClientList');
  if (!list) return;
  uiListSelect('#bkClientList', 'bkClient', name);
  const all = st.summary.clients || [];
  const i = all.findIndex(c => c.client === name);
  $('#bkClientDetail').innerHTML = bkClientDetail(i >= 0 ? { ...all[i], _i: i } : null);
  refreshIcons();
  if (focus) list.focus();
  else if (matchMedia('(max-width:1023px)').matches) $('#bkClientDetail').scrollIntoView({ block: 'start' });
}

function bkJobs() {
  const st = bkState();
  const f = st.jobFilter, j = st.jobs;
  let body;
  if (!j || j.loading) body = uiNote('Đang tải job…');
  else if (j.error) body = `<div class="empty-note tone-crit">${esc(j.error)}</div>`;
  else {
    const rows = j.rows.map(r => `<tr>
      <td>${esc(String(r.jobId))}${r.parentJobId ? `<span class="muted-sub">cha ${esc(String(r.parentJobId))}</span>` : ''}</td><td>${esc(r.jobType || '')}</td>
      <td>${esc(r.clientName || '—')}</td><td class="mono">${esc(r.ip || '—')}</td><td>${esc(r.policyName || '—')}<span class="muted-sub">${esc(r.scheduleLabel || '')}</span></td>
      <td>${bkDate(r.startTime)}</td><td class="num">${r.elapsedSec ? bkNum(r.elapsedSec / 60, 0) + ' ph' : '—'}</td><td class="num">${bkNum((r.kbytes || 0) / 1048576, 2)}</td>
      <td>${bkPill(BK_OUTCOME[r.outcome].cls, BK_OUTCOME[r.outcome].label)}</td>
      <td class="wrap">${r.status !== null && r.status !== undefined ? `<b>${r.status}</b> · ` : ''}${esc(r.statusText)}${r.group ? `<span class="muted-sub">${esc(r.group)}</span>` : ''}</td></tr>`).join('');
    body = `<div class="table-scroll"><table class="data-table"><thead><tr><th>Job ID</th><th>Loại</th><th>Client</th><th>IP</th><th>Policy / lịch</th><th>Bắt đầu</th><th>Thời lượng</th><th>GB</th><th>Kết quả</th><th>Mã · mô tả</th></tr></thead>
      <tbody>${rows || uiEmptyRow(10, 'Không có job phù hợp.')}</tbody></table></div>
      <div class="row-count">${j.rows.length < j.total ? `Hiển thị ${j.rows.length} / ` : ''}${bkNum(j.total)} job — danh sách đầy đủ trong file Excel.</div>`;
  }
  return `
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Job backup</div><p class="panel-sub">Job của máy chủ backup trong kỳ đã chọn (lưu lịch sử theo mỗi lần đồng bộ).</p></div>
        <div class="panel-toolbar">
          <select data-bk-filter="jobOutcome" aria-label="Lọc theo kết quả job">${[['all', 'Mọi kết quả'], ['failed', 'Lỗi'], ['partial', 'Một phần'], ['success', 'Thành công'], ['running', 'Đang chạy']].map(([k, l]) => `<option value="${k}" ${f.outcome === k ? 'selected' : ''}>${l}</option>`).join('')}</select>
          <select data-bk-filter="jobCategory" aria-label="Lọc theo loại job">${[['backup', 'Backup'], ['copy', 'Nhân bản (DR)'], ['restore', 'Restore'], ['all', 'Mọi loại job']].map(([k, l]) => `<option value="${k}" ${f.category === k ? 'selected' : ''}>${l}</option>`).join('')}</select>
          <span class="search-box"><i data-lucide="search"></i><input type="text" data-bk-filter="jobQ" aria-label="Tìm job" placeholder="Client, policy, job ID, mã lỗi" value="${esc(f.q)}"/></span>
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
    <div class="kpi-strip-wrap">${uiKpiStrip(k.map(([label, valueHtml, sub]) => ({ label, valueHtml, sub })))}</div>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Độ phủ theo cluster</div><p class="panel-sub">Đối chiếu VM ${cov.scopeActive ? 'trong phạm vi cần backup' : 'trong inventory vCenter'} với client NetBackup theo tên VM, hostname guest hoặc IP.</p></div>
        <div class="panel-toolbar">${filt}</div></div>
      <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>vCenter</th><th>Cluster</th><th>VM</th><th>Có backup</th><th>Lỗi</th><th>Chưa backup</th><th>Độ phủ</th></tr></thead><tbody>${byCl}</tbody></table></div>
    </section>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">VM cần xác nhận backup</div><p class="panel-sub">Xác nhận với System Owner: bổ sung vào policy / query VMware hoặc ghi nhận ngoại lệ (thêm từ khoá loại trừ ở trang Nguồn &amp; ngưỡng).</p></div>
        <div class="panel-toolbar">
          ${filt}
          <select data-bk-filter="covState">${[['unprotected', 'Chưa backup'], ['failing', 'Có job nhưng lỗi'], ['all', 'Cả hai']].map(([kk, l]) => `<option value="${kk}" ${f.state === kk ? 'selected' : ''}>${l}</option>`).join('')}</select>
          <span class="search-box"><i data-lucide="search"></i><input type="text" data-bk-filter="covQ" placeholder="Tìm VM / host / IP" value="${esc(f.q)}"/></span>
        </div></div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>VM</th><th>vCenter</th><th>Cluster</th><th>Host</th><th>IP</th><th>vCPU</th><th>RAM (GB)</th><th>Disk (GB)</th><th>Tình trạng</th></tr></thead>
      <tbody>${rows || uiEmptyRow(9, 'Không có VM nào.')}</tbody></table></div>
      <div class="row-count">${vms.length} VM</div>
    </section>`;
}

function bkStorage(s) {
  const pools = (s.storage || []).map(p => `<tr><td><b>${esc(p.name)}</b><span class="muted-sub">${esc(p.nbuKey)}</span></td><td>${esc(p.type || '—')}</td><td>${esc(p.state || '—')}</td>
    <td class="num">${bkNum(p.totalTB, 1)}</td><td class="num">${bkNum(p.usedTB, 1)}</td><td class="num">${bkNum(p.freeTB, 1)}</td>
    <td style="min-width:120px;"><div class="bar"><span style="width:${Math.min(100, p.usedPct || 0)}%;background:var(--chart-s1)"></span></div><div class="bar-label"><span>${bkPct(p.usedPct)}</span><span>HWM ${bkNum(p.highWaterPct)}%</span></div></td>
    <td class="num">${p.growthTBPerDay !== null && p.growthTBPerDay !== undefined ? (p.growthTBPerDay >= 0 ? '+' : '') + bkNum(p.growthTBPerDay, 2) : '—'}</td>
    <td class="num">${p.daysToFull === null || p.daysToFull === undefined ? '—' : (p.daysToFull > 365 ? '> 1 năm' : bkNum(p.daysToFull))}</td><td>${bkPill(p.status, p.statusLabel)}</td></tr>`).join('');
  const pols = (s.policies || []).map(p => `<tr><td><b>${esc(p.name)}</b><span class="muted-sub">${esc(p.nbuKey)}</span></td><td>${esc(p.policyType || '—')}</td>
    <td>${p.active === true ? 'Bật' : p.active === false ? 'Tắt' : '—'}</td><td class="num">${bkNum(p.clients)}</td><td>${esc(p.storage || '—')}</td>
    <td class="wrap">${esc(p.schedules || '—')}</td><td>${bkDate(p.lastRunAt)}</td><td class="num">${bkNum(p.jobs)}</td><td class="num">${p.failed ? `<b class="tone-crit">${p.failed}</b>` : 0}</td>
    <td class="num">${bkPct(p.successRate)}</td><td>${bkPill(p.status, p.statusLabel)}</td></tr>`).join('');
  return `
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Disk pool</div><p class="panel-sub">Cảnh báo từ ${bkNum(s.thresholds.storage_warn_pct)}%, nghiêm trọng từ ${bkNum(s.thresholds.storage_crit_pct)}% hoặc khi dự kiến chạm ngưỡng high-water (HWM) trong ${s.thresholds.storage_days_warn} ngày theo xu hướng 30 ngày.</p></div></div>
      <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Disk pool</th><th>Loại</th><th>Trạng thái</th><th>Tổng (TB)</th><th>Đã dùng</th><th>Còn trống</th><th>% dùng</th><th>TB/ngày</th><th>Ngày tới HWM</th><th>Đánh giá</th></tr></thead>
      <tbody>${pools || uiEmptyRow(10, 'Chưa có dữ liệu disk pool (cần quyền xem Storage).')}</tbody></table></div>
      <div class="chart-wrap" style="margin-top:var(--space-4);min-height:240px;"><canvas id="bkChartStorage"></canvas></div>
    </section>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Policy backup</div><p class="panel-sub">Policy đang bật mà không có job trong 48 giờ được đánh giá “Không chạy”. Số client của policy VMware lấy theo VM thực tế được backup.</p></div></div>
      <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Policy</th><th>Loại</th><th>Hoạt động</th><th>Client</th><th>Storage</th><th>Lịch (retention)</th><th>Chạy gần nhất</th><th>Job</th><th>Lỗi</th><th>Tỷ lệ</th><th>Đánh giá</th></tr></thead>
      <tbody>${pols || uiEmptyRow(11, 'Chưa có dữ liệu policy (cần quyền xem Policy).')}</tbody></table></div>
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
  const admin = uiIsAdmin();
  const lock = admin ? '' : 'disabled';
  const form = t ? t.meta.map(m => {
    const v = t.values[m.key], d = t.defaults[m.key];
    const isText = typeof d === 'string';
    const input = m.key === 'schedule_mode'
      ? `<select id="bkTh_${m.key}" data-bk-th="${m.key}" ${lock}><option value="netbackup" ${v === 'netbackup' ? 'selected' : ''}>Lịch của policy NetBackup</option><option value="fixed" ${v === 'fixed' ? 'selected' : ''}>Khung cố định</option></select>`
      : `<input id="bkTh_${m.key}" data-bk-th="${m.key}" ${lock} type="${isText ? 'text' : 'number'}" ${isText ? '' : 'min="0" step="any"'} value="${esc(String(v))}" aria-describedby="bkThH_${m.key}"/>`;
    // short label · control · helper (hint + default) — one row of fields lines up (ui-ux-pro-max input-helper-text)
    return `<div class="field"><label for="bkTh_${m.key}">${esc(m.label)}${m.unit ? ` (${esc(m.unit)})` : ''}</label>
      ${input.replace('<select ', `<select aria-describedby="bkThH_${m.key}" `)}
      <span class="muted-sub field-help" id="bkThH_${m.key}">${m.hint ? `${esc(m.hint)} ` : ''}Mặc định: ${esc(String(d))}</span></div>`;
  }).join('') : uiNote('Đang tải…');
  return `
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Máy chủ backup</div><p class="panel-sub">Thêm / sửa máy chủ, thông tin đăng nhập và API key ở <a href="#/settings/sources" data-nav-link="set-sources">Cài đặt → Kết nối nguồn dữ liệu</a> (cấu hình bằng biến <code>NETBACKUP_i_*</code> vẫn được hỗ trợ). Tự động đồng bộ cùng chu kỳ vCenter.</p></div></div>
      <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Server</th><th>Địa chỉ</th><th>Xác thực</th><th>API</th><th>Đồng bộ gần nhất</th><th>Trạng thái</th><th></th></tr></thead>
      <tbody>${servers || uiEmptyRow(7, 'Chưa có máy chủ backup. Thêm ở Cài đặt → Kết nối nguồn dữ liệu (bật DEMO để chạy thử).')}</tbody></table></div>
      <div class="insight-box"><strong>Tài khoản chỉ đọc:</strong> tạo user dịch vụ trên NetBackup (ví dụ <code>svc_nbu_ro</code>), gán RBAC role tuỳ chỉnh chỉ có quyền <b>View</b> cho Jobs, Policies (NetBackup policies), Storage (disk pools / storage units) — không cấp quyền thao tác. Ưu tiên cấp <b>API key</b> cho user này (NetBackup 9.x trở lên) và khai báo ở <code>NETBACKUP_i_API_KEY</code>; app chỉ gọi các API GET. Chi tiết trong README.</div>
    </section>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Ngưỡng giám sát backup</div><p class="panel-sub">Áp dụng cho trang Backup, báo cáo PDF và Excel. Lưu trên server, dùng chung cho mọi người dùng.</p></div>
        ${admin ? `<div class="panel-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-bk-action="th-reset">Về mặc định</button><button class="btn btn-primary btn-sm" type="button" data-bk-action="th-save">Lưu ngưỡng</button></div>` : `<span class="muted-sub">${ADMIN_ONLY_NOTE}</span>`}</div>
      <div class="bk-th-grid form-grid-aligned">${form}</div>
    </section>`;
}

function renderBackupBody() {
  const st = bkState();
  const body = $('#bkBody');
  if (!body) return;
  bkDestroyCharts();
  if (st.sub === 'config') { body.innerHTML = bkConfig(); refreshIcons(); return; }
  if (st.loading && !st.summary) { body.innerHTML = uiNote('Đang tải dữ liệu backup…'); return; }
  if (st.error) { body.innerHTML = `<section class="table-card"><div class="empty-note tone-crit">Không tải được dữ liệu backup: ${esc(st.error)}</div></section>`; return; }
  const s = st.summary;
  if (!s) { body.innerHTML = uiNote('Đang tải dữ liệu backup…'); return; }
  if (!(st.servers || []).length) {
    body.innerHTML = `<section class="table-card"><div class="empty-state">Chưa có máy chủ backup. Thêm máy chủ ở <a href="#/settings/sources" data-nav-link="set-sources">Cài đặt → Kết nối nguồn dữ liệu</a> (bật DEMO để chạy thử).</div></section>`;
    return;
  }
  const html = { overview: bkOverview, clients: bkClients, coverage: bkCoverage, storage: bkStorage, schedule: bkSchedule, size: bkSizePage }[st.sub];
  body.innerHTML = st.sub === 'jobs' ? bkJobs() : html(s);
  body.querySelectorAll('input[data-tri="some"]').forEach(i => { i.indeterminate = true; });
  refreshIcons();
  if (st.sub === 'overview') { bkChartDaily(s); if (typeof aiGate === 'function') aiGate(body); }
  if (st.sub === 'storage') bkChartStorage(s);
  if (st.sub === 'size') bkSizeBind(s);
  if (st.sub === 'jobs' && !st.jobs) bkLoadJobs();
}

function bkChartDaily(s) {
  if (typeof Chart === 'undefined') return;
  const el = $('#bkChartDaily'); if (!el) return;
  const c = chartPalette();
  const labels = s.daily.map(d => `${d.date.slice(8, 10)}/${d.date.slice(5, 7)}`);
  // Outcomes ARE statuses, so status colors are right here; TB moves to its own chart below the same x axis
  // (MASTER.md §8: one y axis per chart — the old version had job count and TB on two axes).
  bkState().charts.daily = new Chart(el, {
    type: 'bar',
    data: { labels, datasets: [
      { label: 'Thành công', data: s.daily.map(d => d.success), backgroundColor: c.ok, stack: 'j' },
      { label: 'Một phần', data: s.daily.map(d => d.partial), backgroundColor: c.warn, stack: 'j' },
      { label: 'Lỗi', data: s.daily.map(d => d.failed), backgroundColor: c.crit, stack: 'j' },
    ] },
    options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: 'index', intersect: false },
      plugins: { legend: { labels: { color: c.text, boxWidth: 10 } } },
      scales: { x: { stacked: true, ticks: { color: c.axis }, grid: { display: false } },
        y: { stacked: true, beginAtZero: true, ticks: { color: c.axis }, grid: { color: c.grid }, title: { display: true, text: 'Số job', color: c.axis } } } },
  });
  const tb = $('#bkChartDailyTb'); if (!tb) return;
  bkState().charts.dailyTb = new Chart(tb, {
    type: 'line',
    data: { labels, datasets: [{ label: 'Dữ liệu đã backup (TB)', data: s.daily.map(d => d.tb), borderColor: c.series[0], backgroundColor: c.series[0], pointRadius: 2, borderWidth: 2, tension: .25 }] },
    options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: false }, title: { display: true, text: 'Dữ liệu đã backup (TB)', color: c.axis, align: 'start', font: { weight: 500 } } },
      scales: { x: { ticks: { color: c.axis }, grid: { display: false } }, y: { beginAtZero: true, ticks: { color: c.axis, maxTicksLimit: 4 }, grid: { color: c.grid } } } },
  });
}

function bkChartStorage(s) {
  if (typeof Chart === 'undefined') return;
  const el = $('#bkChartStorage'); const tr = s.storageTrend;
  if (!el || !tr || tr.dates.length < 2) return;
  const c = chartPalette();
  const colors = c.series;   /* validated HSC series; never status colors for pools; the limit line is ink */
  bkState().charts.storage = new Chart(el, {
    type: 'line',
    data: { labels: tr.dates.map(d => `${d.slice(8, 10)}/${d.slice(5, 7)}`), datasets: tr.series.map((x, i) => ({ label: x.name, data: x.values, borderColor: colors[i % colors.length], backgroundColor: colors[i % colors.length], pointRadius: 0, tension: .2, spanGaps: true }))
      .concat([{ label: `Nghiêm trọng ${s.thresholds.storage_crit_pct}%`, data: tr.dates.map(() => s.thresholds.storage_crit_pct), borderColor: c.text, borderDash: [5, 4], pointRadius: 0, borderWidth: 1.5 }]) },
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
  body.addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.target.dataset.bkFilter === 'jobQ') { st.jobFilter.q = e.target.value; bkLoadJobs(); return; }
    if (e.target.id !== 'bkClientList') return;
    uiListKeys(e, 'bkClient', st.clientSel, bkClientSelect, () => uiFocusDetail('#bkClientDetail'));   // J / K / ↑↓ / Home / End / Enter, same as every list
  });
  body.addEventListener('click', async e => {
    const tg = e.target.closest('[data-sc-toggle]');
    if (tg && st.scopeEdit) {
      const o = st.scopeEdit.open;
      o.has(tg.dataset.scToggle) ? o.delete(tg.dataset.scToggle) : o.add(tg.dataset.scToggle);
      bkScopeRerender();
      return;
    }
    const cl = e.target.closest('[data-bk-client]');
    if (cl) { bkClientSelect(cl.dataset.bkClient); return; }
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
    if (act === 'client-jobs') {
      st.jobFilter = { outcome: 'all', category: 'all', q: a.dataset.client || '' };
      st.jobs = null;
      location.hash = '#/backup/jobs';
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
  bkTakeQuery(st);
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

/* Deep links from the inbox (once per hash): ?client=<name> selects the client, ?q= searches every job (all outcomes). */
function bkTakeQuery(st) {
  const raw = location.hash.split('?')[1] || '';
  if (!raw || st.lastQuery === location.hash) return;
  st.lastQuery = location.hash;
  const q = new URLSearchParams(raw);
  if (q.get('client')) { st.clientFilter = { ...st.clientFilter, status: 'all', q: q.get('client') }; st.clientSel = q.get('client'); }
  if (q.get('q')) { st.jobFilter = { ...st.jobFilter, q: q.get('q') }; st.jobs = null; }
}

/* Called after a vCenter sync elsewhere in the app: the next visit (or the current page) reloads. */
function bkInvalidate() {
  if (!appState.backup) return;
  const st = bkState();
  st.loadedAt = 0;
  st.scope = null;
  if (appState.view === 'backup') renderBackupView();
}
