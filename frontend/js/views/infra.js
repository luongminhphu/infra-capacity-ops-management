/* =========================================================
   Sức khoẻ hạ tầng (Giai đoạn 1): Datastore · Snapshot VM · Alarm vCenter · Sức khoẻ host.
   Data: /api/infra/* (state stored at each vCenter sync — no call to vCenter from here).
   Cài đặt → Cảnh báo: /api/settings/alerts (admin).
   ========================================================= */
const infraState = { sub: 'datastores', q: '', onlyIssues: false, data: {}, summary: null, error: null, loading: false };
const INFRA_TABS = { datastores: 'infra-ds', snapshots: 'infra-snap', alarms: 'infra-alarm', hosts: 'infra-health', storage: 'infra-storage' };
const INFRA_LEVEL = { crit: 'VƯỢT NGƯỠNG', bad: 'CẢNH BÁO', warn: 'THEO DÕI', ok: 'AN TOÀN' };
const INFRA_ENTITY = { host: 'Host', vm: 'VM', datastore: 'Datastore', cluster: 'Cluster', other: 'Khác' };
let infraChart = null;

function infraVc() {
  return (typeof currentVcenterFilter === 'undefined' || !currentVcenterFilter || currentVcenterFilter === 'import') ? 'all' : currentVcenterFilter;
}

async function infraLoad(force) {
  const sub = infraState.sub;
  if (infraState.data[sub] && !force) return;
  infraState.loading = true;
  const q = `?vcenter=${encodeURIComponent(infraVc())}`;
  try {
    const [summary, rows] = await Promise.all([
      apiRequest(`/infra/summary${q}`),
      apiRequest(sub === 'storage' ? '/infra/storage' : `/infra/${{ datastores: 'datastores', snapshots: 'snapshots', alarms: 'alarms', hosts: 'health' }[sub]}${q}`)]);
    infraState.summary = summary;
    infraState.data[sub] = rows;
    infraState.error = null;
  } catch (err) { infraState.error = err.message; }
  infraState.loading = false;
}

async function renderInfraView() {
  const mount = $('#infraMount');
  if (!mount) return;
  infraBind();
  if (!infraState.data[infraState.sub]) mount.innerHTML = uiLoading();
  await infraLoad();
  infraRender();
}

function infraKpis() {
  const s = infraState.summary;
  if (!s) return '';
  const ds = s.datastores, sn = s.snapshots, al = s.alarms, hh = s.health;
  const dsStatus = ds.crit ? { cls: 'crit', text: `${ds.crit} VƯỢT NGƯỠNG` } : ds.bad ? { cls: 'bad', text: `${ds.bad} CẢNH BÁO` } : { cls: 'ok', text: 'AN TOÀN' };
  return uiKpiStrip([
    { label: 'Datastore', valueHtml: `${fmtInt(ds.total)}<small>datastore</small>`,
      subHtml: ds.soonestFullDays !== null ? `Đầy sớm nhất sau <b>${fmtInt(ds.soonestFullDays)}</b> ngày` : 'Chưa đủ lịch sử để dự báo', status: dsStatus },
    { label: 'Snapshot VM', valueHtml: `${fmtInt(sn.vms)}<small>VM</small>`, subHtml: `${fmtNum(sn.sizeGB, 0)} GB dữ liệu snapshot`,
      status: sn.crit ? { cls: 'crit', text: `${sn.crit} VƯỢT NGƯỠNG` } : sn.bad ? { cls: 'bad', text: `${sn.bad} CẢNH BÁO` } : { cls: 'ok', text: 'AN TOÀN' } },
    { label: 'Alarm vCenter', valueHtml: `${fmtInt(al.total)}<small>alarm</small>`, subHtml: `${fmtInt(al.red)} đỏ · ${fmtInt(al.unacknowledged)} chưa xác nhận`,
      status: al.red ? { cls: 'crit', text: 'Có alarm đỏ' } : al.total ? { cls: 'bad', text: 'Có alarm vàng' } : { cls: 'ok', text: 'AN TOÀN' } },
    { label: 'Sức khoẻ host', valueHtml: `${fmtInt(hh.hosts)}<small>host</small>`, subHtml: `${fmtInt(hh.issues)} host có vấn đề phần cứng / kết nối`,
      status: hh.issues ? { cls: 'bad', text: `${hh.issues} cần xử lý` } : { cls: 'ok', text: 'AN TOÀN' } },
    ...(s.storage && s.storage.arrays ? [{ label: 'Storage', valueHtml: `${fmtInt(s.storage.arrays)}<small>mảng</small>`,
      subHtml: `${fmtInt(s.storage.alerts)} alert đang mở`,
      status: s.storage.crit ? { cls: 'crit', text: `${s.storage.crit} VƯỢT NGƯỠNG` } : s.storage.issues ? { cls: 'bad', text: `${s.storage.issues} cần xử lý` } : { cls: 'ok', text: 'AN TOÀN' } }] : []),
  ]);
}

function infraFilter(rows, text) {
  const q = infraState.q.trim().toLowerCase();
  return rows.filter(r => (!infraState.onlyIssues || r.level !== 'ok') && (!q || text(r).toLowerCase().includes(q)));
}

const infraPill = (r) => uiPill(r.level, r.levelText || INFRA_LEVEL[r.level]);
const infraDays = (v) => (v === null || v === undefined) ? '—' : fmtInt(v);

function infraDatastores(rows) {
  const list = infraFilter(rows, r => `${r.name} ${r.clusters.join(' ')} ${r.vcenterKey}`);
  if (!rows.length) return uiEmpty('Chưa có dữ liệu datastore — dữ liệu có sau lần đồng bộ vCenter kế tiếp.');
  return `<div class="table-scroll" role="region" tabindex="0" aria-label="Datastore"><table class="data-table infra-table">
    <caption class="sr-only">Dung lượng datastore và dự báo ngày đầy</caption>
    <thead><tr><th>Datastore</th><th>Loại</th><th class="num">Dung lượng (GB)</th><th>Đã dùng</th><th class="num">Trống (GB)</th>
      <th class="num">Cấp phát thin</th><th class="num">Tăng / ngày (GB)</th><th class="num">Đầy sau (ngày)</th><th>Storage</th><th>Trạng thái</th></tr></thead>
    <tbody>${list.map(d => `<tr class="infra-row" tabindex="0" data-infra-ds="${esc(d.vcenterKey)}|${esc(d.key)}" title="Xem xu hướng 30 ngày">
      <td><b>${esc(d.name)}</b><span class="muted-sub">${esc(d.clusters.join(', ') || '—')} · ${esc(d.vcenterKey)}</span></td>
      <td>${uiTag(d.type || '—')}</td>
      <td class="num">${fmtNum(d.capacityGB, 0)}</td>
      <td><span class="infra-use">${uiBullet(d.usedPct, d.name)}<b class="num">${d.usedPct === null ? '—' : fmtNum(d.usedPct, 1) + '%'}</b></span></td>
      <td class="num">${fmtNum(d.freeGB, 0)}</td>
      <td class="num">${d.provisionedPct === null ? '—' : fmtNum(d.provisionedPct, 0) + '%'}</td>
      <td class="num">${d.growthGBPerDay === null ? '—' : fmtNum(d.growthGBPerDay, 1)}</td>
      <td class="num">${infraDays(d.daysToFull)}${d.fullDate ? `<span class="muted-sub">${esc(d.fullDate.split('-').reverse().join('/'))}</span>` : ''}</td>
      <td>${d.storage ? `${esc(d.storage.array)}<span class="muted-sub">${esc(d.storage.kind === 'lun' ? 'LUN' : 'Volume')} ${esc(d.storage.volume)}</span>` : '<span class="muted-sub">—</span>'}</td>
      <td>${infraPill(d)}${d.reasons.length ? `<span class="muted-sub">${esc(d.reasons.join(' · '))}</span>` : ''}</td></tr>`).join('')
      || uiEmptyRow(10, 'Không có datastore khớp bộ lọc.')}</tbody></table></div>
    <p class="muted-sub">Ngưỡng: ≥ 80% THEO DÕI · ≥ 90% CẢNH BÁO · ≥ 95% VƯỢT NGƯỠNG; dự báo đầy ≤ 30 ngày CẢNH BÁO, ≤ 7 ngày VƯỢT NGƯỠNG
      (xu hướng tuyến tính 30 ngày, cần ≥ 3 ngày lịch sử). Bấm một dòng để xem biểu đồ.</p>`;
}

function infraSnapshots(rows) {
  const list = infraFilter(rows, r => `${r.vm} ${r.cluster || ''} ${r.host || ''} ${r.items.map(i => i.name).join(' ')}`);
  if (!rows.length) return uiEmpty('Không VM nào đang có snapshot.');
  return `<div class="table-scroll" role="region" tabindex="0" aria-label="Snapshot VM"><table class="data-table infra-table">
    <caption class="sr-only">VM đang có snapshot</caption>
    <thead><tr><th>VM</th><th>Cluster · host</th><th class="num">Số snapshot</th><th class="num">Cũ nhất (ngày)</th><th class="num">Dữ liệu (GB)</th><th>Snapshot</th><th>Trạng thái</th></tr></thead>
    <tbody>${list.map(s => `<tr>
      <td>${typeof uiEntityLink === 'function' ? uiEntityLink('vm', s.vm) : esc(s.vm)}<span class="muted-sub">${esc(s.vcenterKey)}</span></td>
      <td>${esc(s.cluster || '—')}<span class="muted-sub">${esc(s.host || '—')}</span></td>
      <td class="num">${fmtInt(s.count)}</td>
      <td class="num">${s.ageDays === null ? '—' : fmtNum(s.ageDays, 1)}<span class="muted-sub">${uiDateTime(s.oldestAt, 'short')}</span></td>
      <td class="num">${s.sizeGB === null ? '—' : fmtNum(s.sizeGB, 1)}</td>
      <td class="wrap">${s.items.slice(0, 3).map(i => esc(i.name)).join('<br>')}${s.items.length > 3 ? `<span class="muted-sub">+${s.items.length - 3} snapshot</span>` : ''}
        ${s.backupLike ? uiTag('Snapshot của phần mềm backup', 'Tạo tạm khi backup — thường tự xoá sau job') : ''}</td>
      <td>${infraPill(s)}${s.reasons.length ? `<span class="muted-sub">${esc(s.reasons.join(' · '))}</span>` : ''}</td></tr>`).join('')
      || uiEmptyRow(7, 'Không có VM khớp bộ lọc.')}</tbody></table></div>
    <p class="muted-sub">Ngưỡng: tồn ≥ 24 giờ THEO DÕI · ≥ 3 ngày CẢNH BÁO · ≥ 7 ngày hoặc ≥ 200 GB VƯỢT NGƯỠNG · chuỗi ≥ 3 snapshot CẢNH BÁO.
      Dung lượng ước tính từ file delta / bộ nhớ của snapshot. ICO chỉ đọc — xoá / hợp nhất snapshot trên vCenter sau khi xác nhận với chủ VM.</p>`;
}

function infraAlarms(rows) {
  const list = infraFilter(rows, r => `${r.alarm} ${r.entity} ${r.cluster || ''}`);
  if (!rows.length) return uiEmpty('Không có alarm đỏ / vàng nào đang kích hoạt.');
  return `<div class="table-scroll" role="region" tabindex="0" aria-label="Alarm vCenter"><table class="data-table infra-table">
    <caption class="sr-only">Alarm vCenter đang kích hoạt</caption>
    <thead><tr><th>Trạng thái</th><th>Alarm</th><th>Đối tượng</th><th>Cluster</th><th>Kích hoạt lúc</th><th>Xác nhận</th></tr></thead>
    <tbody>${list.map(a => `<tr>
      <td>${infraPill(a)}</td><td><b>${esc(a.alarm)}</b><span class="muted-sub">${esc(a.vcenterKey)}</span></td>
      <td>${uiTag(INFRA_ENTITY[a.entityType] || a.entityType)} ${a.entityType === 'host' || a.entityType === 'vm' ? (typeof uiEntityLink === 'function' ? uiEntityLink(a.entityType, a.entity) : esc(a.entity)) : esc(a.entity)}</td>
      <td>${esc(a.cluster || '—')}</td><td>${uiDateTime(a.at, 'short')}</td>
      <td>${a.acknowledged ? uiTag('Đã xác nhận') : '<span class="muted-sub">Chưa</span>'}</td></tr>`).join('')
      || uiEmptyRow(6, 'Không có alarm khớp bộ lọc.')}</tbody></table></div>
    <p class="muted-sub">Đỏ = VƯỢT NGƯỠNG, vàng = CẢNH BÁO. Alarm đã xác nhận trên vCenter không vào hộp việc Trang chủ.</p>`;
}

function infraHosts(rows) {
  const list = infraFilter(rows, r => `${r.host} ${r.cluster || ''} ${r.reasons.join(' ')}`);
  if (!rows.length) return uiEmpty('Chưa có dữ liệu sức khoẻ host — có sau lần đồng bộ vCenter kế tiếp.');
  return `<div class="table-scroll" role="region" tabindex="0" aria-label="Sức khoẻ host"><table class="data-table infra-table">
    <caption class="sr-only">Sức khoẻ phần cứng host</caption>
    <thead><tr><th>Host</th><th>Cluster</th><th>Kết nối</th><th>Cảm biến bất thường</th><th>Trạng thái</th></tr></thead>
    <tbody>${list.map(h => `<tr>
      <td>${typeof uiEntityLink === 'function' ? uiEntityLink('host', h.host) : esc(h.host)}${h.maintenance ? ` ${uiTag('Bảo trì')}` : ''}<span class="muted-sub">${esc(h.vcenterKey)}</span></td>
      <td>${esc(h.cluster || '—')}</td><td>${esc(h.connection)}</td>
      <td class="wrap">${h.issues.length ? h.issues.slice(0, 6).map(i => `${uiPill(i.state === 'red' ? 'crit' : 'bad', i.state === 'red' ? 'Đỏ' : 'Vàng')} ${esc(i.name)}`).join('<br>') : '<span class="muted-sub">Không</span>'}</td>
      <td>${infraPill(h)}</td></tr>`).join('') || uiEmptyRow(5, 'Không có host khớp bộ lọc.')}</tbody></table></div>
    <p class="muted-sub">Đọc từ cảm biến phần cứng qua vCenter (nguồn, quạt, nhiệt độ, bộ nhớ, bộ điều khiển đĩa). Host đang bảo trì không vào hộp việc.</p>`;
}

/* ---------- Storage arrays (Pure FlashArray / NetApp ONTAP) ---------- */
const infraMs = (v) => (v === null || v === undefined) ? '—' : `${fmtNum(v, 2)} ms`;
function infraStorage(data) {
  const arrays = (data && data.arrays) || [], vols = (data && data.volumes) || [];
  if (!arrays.length) return uiEmpty('Chưa có mảng lưu trữ — thêm Pure FlashArray / NetApp ONTAP ở Cài đặt → Nguồn dữ liệu.');
  const cards = arrays.filter(a => !infraState.onlyIssues || a.level !== 'ok').map(a => `<article class="infra-array">
      <header><div><b>${esc(a.name)}</b><span class="muted-sub">${esc(a.model || (a.vendor === 'pure' ? 'Pure FlashArray' : 'NetApp ONTAP'))} · ${esc(a.version || '—')}</span></div>${infraPill(a)}</header>
      <div class="infra-use">${uiBullet(a.usedPct, a.name)}<b class="num">${a.usedPct === null ? '—' : fmtNum(a.usedPct, 1) + '%'}</b></div>
      <dl class="sp-dl">
        <div class="sp-dl-item"><dt>Vật lý đã dùng</dt><dd>${a.usedGB === null ? '—' : fmtNum(a.usedGB / 1024, 1)} / ${a.capacityGB === null ? '—' : fmtNum(a.capacityGB / 1024, 1)} TB</dd></div>
        <div class="sp-dl-item"><dt>Giảm dữ liệu</dt><dd>${a.dataReduction === null ? '—' : fmtNum(a.dataReduction, 1) + ' : 1'}</dd></div>
        <div class="sp-dl-item"><dt>Độ trễ đọc · ghi</dt><dd>${infraMs(a.latencyReadMs)} · ${infraMs(a.latencyWriteMs)}</dd></div>
        <div class="sp-dl-item"><dt>IOPS · băng thông</dt><dd>${a.iops === null ? '—' : fmtInt(a.iops)} · ${a.throughputMBps === null ? '—' : fmtNum(a.throughputMBps, 0) + ' MB/s'}</dd></div>
        <div class="sp-dl-item"><dt>Controller</dt><dd>${a.controllers.map(c => `${esc(c.name)} ${uiTag(c.status || '—')}`).join(' ') || '—'}</dd></div>
        <div class="sp-dl-item"><dt>Đọc lần cuối</dt><dd>${uiDateTime(a.lastOkAt, 'short')}</dd></div>
      </dl>
      ${a.reasons.length ? `<ul class="infra-reasons">${a.reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}
      ${a.alerts.length ? `<details class="infra-alerts"><summary>${fmtInt(a.alerts.length)} alert đang mở</summary><ul>${a.alerts.slice(0, 20).map(x =>
        `<li>${uiPill(x.severity === 'critical' ? 'crit' : x.severity === 'warning' ? 'bad' : 'warn', x.severity === 'critical' ? 'Nghiêm trọng' : x.severity === 'warning' ? 'Cảnh báo' : 'Thông tin')} ${esc(x.summary)}<span class="muted-sub">${esc(x.component || '')} · ${uiDateTime(x.at, 'short')}</span></li>`).join('')}</ul></details>` : ''}
    </article>`).join('');
  const list = infraFilter(vols.map(v => ({ ...v, level: v.datastores.length ? 'warn' : 'ok' })), v => `${v.name} ${v.array} ${v.container || ''} ${v.datastores.map(d => d.datastore).join(' ')}`)
    .filter(v => !infraState.onlyIssues || v.datastores.length);
  const table = `<div class="table-scroll" role="region" tabindex="0" aria-label="Volume và LUN"><table class="data-table infra-table">
    <caption class="sr-only">Volume / LUN của các mảng và datastore VMware tương ứng</caption>
    <thead><tr><th>Volume / LUN</th><th>Mảng</th><th class="num">Dung lượng (GB)</th><th>Đã dùng</th><th>Datastore VMware</th><th class="num">VM</th></tr></thead>
    <tbody>${list.slice(0, 500).map(v => `<tr>
      <td><b>${esc(v.name)}</b><span class="muted-sub">${esc(v.kind === 'lun' ? 'LUN' : 'Volume')}${v.container ? ' · ' + esc(v.container) : ''}${v.nfsPath ? ' · NFS ' + esc(v.nfsPath) : ''}</span></td>
      <td>${esc(v.array)}</td><td class="num">${v.sizeGB === null ? '—' : fmtNum(v.sizeGB, 0)}</td>
      <td><span class="infra-use">${uiBullet(v.usedPct, v.name)}<b class="num">${v.usedPct === null ? '—' : fmtNum(v.usedPct, 1) + '%'}</b></span></td>
      <td>${v.datastores.length ? v.datastores.map(d => `${esc(d.datastore)}<span class="muted-sub">${esc(d.vcenterKey)} · ${esc(d.clusters.join(', ') || '—')}</span>`).join('') : '<span class="muted-sub">Không gắn VMware</span>'}</td>
      <td class="num">${v.datastores.length ? fmtInt(v.vmCount) : '—'}</td></tr>`).join('') || uiEmptyRow(6, 'Không có volume khớp bộ lọc.')}</tbody></table></div>
    <p class="muted-sub">Ghép volume / LUN với datastore theo mã NAA của extent VMFS (Pure: naa.624a9370 + serial; ONTAP: naa.600a0980 + serial)
      và theo IP LIF + đường dẫn export của datastore NFS. "Chỉ mục cần xử lý" ở bảng này = chỉ volume đang chứa datastore VMware.</p>`;
  return `<div class="infra-arrays">${cards || uiNote('Không có mảng cần xử lý.')}</div><h3 class="sp-h">Volume / LUN ↔ datastore ↔ VM</h3>${table}`;
}

function infraRender() {
  const mount = $('#infraMount');
  if (!mount) return;
  const sub = infraState.sub, rows = infraState.data[sub] || [];
  const body = infraState.error ? uiError(infraState.error, 'data-infra-retry')
    : { datastores: infraDatastores, snapshots: infraSnapshots, alarms: infraAlarms, hosts: infraHosts, storage: infraStorage }[sub](rows);
  mount.innerHTML = `${infraKpis()}
    <section class="panel">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">${esc({ datastores: 'Dung lượng datastore & dự báo đầy', snapshots: 'Snapshot VM tồn lâu', alarms: 'Alarm vCenter đang kích hoạt', hosts: 'Sức khoẻ phần cứng host', storage: 'Mảng lưu trữ (Pure FlashArray · NetApp ONTAP)' }[sub])}</div>
        <p class="panel-sub">Cập nhật ở mỗi lần đồng bộ vCenter (chỉ đọc). Mục cần xử lý cũng vào hộp việc Trang chủ và cảnh báo Discord theo Cài đặt → Cảnh báo.</p></div>
        <div class="panel-toolbar infra-toolbar">
          <input type="search" data-infra-q placeholder="Tìm tên, cluster…" aria-label="Tìm" value="${esc(infraState.q)}">
          <label class="infra-check"><input type="checkbox" data-infra-issues${infraState.onlyIssues ? ' checked' : ''}> Chỉ mục cần xử lý</label>
          ${sub === 'storage' && uiIsAdmin() ? '<button class="btn btn-secondary btn-sm" type="button" data-infra-storage-sync><i data-lucide="database"></i>Đồng bộ storage</button>' : ''}
          <button class="btn btn-ghost btn-sm" type="button" data-infra-reload><i data-lucide="refresh-cw"></i>Tải lại</button></div></div>
      <div id="infraBody">${body}</div>
    </section>`;
  refreshIcons();
}

function infraBind() {
  const mount = $('#infraMount');
  if (!mount || mount.dataset.bound) return;
  mount.dataset.bound = '1';
  mount.addEventListener('input', (e) => {
    if (!e.target.matches('[data-infra-q]')) return;
    infraState.q = e.target.value;
    const pos = e.target.selectionStart;
    infraRender();
    const box = $('[data-infra-q]'); if (box) { box.focus(); box.setSelectionRange(pos, pos); }
  });
  mount.addEventListener('change', (e) => {
    if (e.target.matches('[data-infra-issues]')) { infraState.onlyIssues = e.target.checked; infraRender(); }
  });
  const open = (row) => { const [vc, key] = row.dataset.infraDs.split('|'); infraOpenDatastore(vc, key); };
  mount.addEventListener('click', async (e) => {
    if (e.target.closest('[data-infra-reload], [data-infra-retry]')) { await infraLoad(true); infraRender(); return; }
    const sync = e.target.closest('[data-infra-storage-sync]');
    if (sync) {
      sync.disabled = true;
      try {
        const r = await apiRequest('/infra/storage/sync', { method: 'POST' });
        const bad = r.results.filter(x => !x.ok);
        if (bad.length) uiAlert(bad.map(x => `${x.key}: ${x.error || 'đang đồng bộ'}`).join('\n'));
        await infraLoad(true); infraRender();
      } catch (err) { uiAlert(err.message); } finally { sync.disabled = false; }
      return;
    }
    const row = e.target.closest('[data-infra-ds]');
    if (row && !e.target.closest('a, button')) open(row);
  });
  mount.addEventListener('keydown', (e) => {
    const row = e.target.closest('[data-infra-ds]');
    if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); open(row); }
  });
}

async function infraOpenDatastore(vc, key) {
  const d = (infraState.data.datastores || []).find(x => x.vcenterKey === vc && x.key === key);
  if (!d) return;
  uiPanelDialog(`Datastore ${esc(d.name)}`, `${uiLoading()}`, '<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Đóng</button>');
  let h;
  try { h = await apiRequest(`/infra/datastores/${encodeURIComponent(vc)}/${encodeURIComponent(key)}/history?days=30`); }
  catch (err) { $('#spDialogBody .sp-dialog-body').innerHTML = uiError(err.message); return; }
  $('#spDialogBody .sp-dialog-body').innerHTML = `
    <dl class="sp-dl">
      <div class="sp-dl-item"><dt>Đã dùng</dt><dd>${fmtNum(d.usedGB, 0)} / ${fmtNum(d.capacityGB, 0)} GB (${d.usedPct === null ? '—' : fmtNum(d.usedPct, 1) + '%'})</dd></div>
      <div class="sp-dl-item"><dt>Tăng trung bình</dt><dd>${d.growthGBPerDay === null ? '—' : fmtNum(d.growthGBPerDay, 1) + ' GB / ngày'}</dd></div>
      <div class="sp-dl-item"><dt>Dự báo đầy</dt><dd>${d.daysToFull === null ? 'Chưa dự báo được' : `sau ${fmtInt(d.daysToFull)} ngày (${esc(d.fullDate.split('-').reverse().join('/'))})`}</dd></div>
      <div class="sp-dl-item"><dt>Cluster · host · VM</dt><dd>${esc(d.clusters.join(', ') || '—')} · ${fmtInt(d.hostCount)} host · ${fmtInt(d.vmCount)} VM</dd></div>
    </dl>
    ${h.used.length < 2 ? uiNote('Chưa đủ lịch sử để vẽ xu hướng (cần ít nhất 2 lần đồng bộ).') : '<div class="infra-chart"><canvas id="infraDsChart" aria-label="Xu hướng dung lượng 30 ngày"></canvas></div>'}`;
  if (h.used.length < 2 || typeof Chart === 'undefined') return;
  if (infraChart) { infraChart.destroy(); infraChart = null; }
  const pal = chartPalette();
  const labels = h.used.map(p => uiDateTime(p[0], 'short'));
  infraChart = new Chart($('#infraDsChart'), {
    type: 'line',
    data: { labels, datasets: [
      { label: 'Đã dùng (GB)', data: h.used.map(p => p[1]), borderColor: pal.series[0], backgroundColor: pal.series[0], pointRadius: 0, tension: 0.2 },
      { label: 'Dung lượng (GB)', data: h.capacity.map(p => p[1]), borderColor: pal.other, backgroundColor: pal.other, pointRadius: 0, borderDash: [6, 4] }] },
    options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      scales: { y: { beginAtZero: true, ticks: { color: pal.axis, callback: (v) => fmtInt(v) }, grid: { color: pal.grid } },
                x: { ticks: { color: pal.axis, maxTicksLimit: 8 }, grid: { display: false } } },
      plugins: { legend: { labels: { color: pal.text } },
                 tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmtNum(c.parsed.y, 0)}` } } } },
  });
}

/* ---------- Cài đặt → Cảnh báo (admin) ---------- */
async function renderAlertSettings() {
  const mount = $('#alertRulesMount');
  if (!mount) return;
  if (!uiIsAdmin()) { mount.innerHTML = uiNote('Chỉ quản trị viên xem và sửa quy tắc cảnh báo.'); return; }
  mount.innerHTML = uiLoading();
  let cfg;
  try { cfg = await apiRequest('/settings/alerts'); } catch (err) { mount.innerHTML = uiError(err.message); return; }
  const chans = cfg.channels;
  const chanBox = (r) => chans.length
    ? `<div class="alert-chans">${chans.map(c => `<label class="infra-check"><input type="checkbox" data-al-chan="${esc(r.source)}" value="${esc(c.id)}"${r.channels.includes(c.id) ? ' checked' : ''}> ${esc(c.name)}</label>`).join('')}</div>
       <span class="field-help">Không chọn kênh nào = gửi tới mọi kênh nhận cảnh báo vận hành.</span>`
    : '<span class="muted-sub">Chưa có kênh</span>';
  mount.innerHTML = `
    ${chans.length ? '' : uiNote('Chưa có kênh Discord nào bật “Nhận cảnh báo vận hành” — thêm ở Cài đặt → Chi phí & nhắc hạn → Kênh thông báo.', 'warn')}
    <div class="table-scroll" role="region" tabindex="0" aria-label="Quy tắc cảnh báo"><table class="data-table alert-rules">
      <caption class="sr-only">Quy tắc gửi cảnh báo vận hành theo nguồn</caption>
      <thead><tr><th>Nguồn</th><th>Gửi Discord</th><th>Gửi từ mức</th><th>Kênh</th></tr></thead>
      <tbody>${cfg.rules.map(r => `<tr data-al-row="${esc(r.source)}">
        <td><b>${esc(r.label)}</b></td>
        <td><label class="switch"><input type="checkbox" role="switch" data-al-on aria-label="Gửi cảnh báo ${esc(r.label)}"${r.enabled ? ' checked' : ''}><span class="slider"></span></label></td>
        <td><select data-al-level aria-label="Mức tối thiểu ${esc(r.label)}">${cfg.levels.map(l => `<option value="${esc(l.key)}"${l.key === r.minLevel ? ' selected' : ''}>${esc(l.label)}</option>`).join('')}</select></td>
        <td>${chanBox(r)}</td></tr>`).join('')}</tbody></table></div>
    <p class="muted-sub">Capacity / Backup luôn gửi khi chạm mốc 90% (quyết định chung), cộng thêm theo mức chọn ở đây. Mỗi cảnh báo gửi một lần cho đến khi hết, kèm tin “Đã hết”.</p>
    <div class="feature-row">
      <div><div class="feature-name" id="alQuietLbl">Giờ im lặng</div><div class="feature-desc">Trong khung giờ này (giờ Việt Nam) chỉ gửi VƯỢT NGƯỠNG; các tin khác gửi khi hết giờ im lặng.</div></div>
      <label class="switch"><input type="checkbox" role="switch" id="alQuietOn" aria-labelledby="alQuietLbl"${cfg.quiet.enabled ? ' checked' : ''}><span class="slider"></span></label>
    </div>
    <div class="form-grid-aligned alert-quiet">
      <label class="field"><span>Bắt đầu</span><input type="time" id="alQuietStart" value="${esc(cfg.quiet.start)}"></label>
      <label class="field"><span>Kết thúc</span><input type="time" id="alQuietEnd" value="${esc(cfg.quiet.end)}"></label>
    </div>
    <div class="sp-inline"><span class="muted-sub">${cfg.quiet.activeNow ? 'Đang trong giờ im lặng.' : ''}</span><span class="src-spacer"></span>
      <button class="btn btn-primary btn-sm" type="button" id="btnAlertSave">Lưu quy tắc</button></div>
    <p class="src-result" id="alertMsg" role="status"></p>`;
  $('#btnAlertSave').onclick = async () => {
    const rules = $$('[data-al-row]').map(tr => ({ source: tr.dataset.alRow, enabled: tr.querySelector('[data-al-on]').checked,
      minLevel: tr.querySelector('[data-al-level]').value, channels: $$(`[data-al-chan="${tr.dataset.alRow}"]:checked`).map(c => c.value) }));
    const body = { rules, quiet: { enabled: $('#alQuietOn').checked, start: $('#alQuietStart').value, end: $('#alQuietEnd').value } };
    try { await apiRequest('/settings/alerts', { method: 'PUT', body: JSON.stringify(body) }); uiSetStatus($('#alertMsg'), 'Đã lưu — áp dụng từ lượt kiểm tra cảnh báo kế tiếp.', 'ok'); }
    catch (err) { uiSetStatus($('#alertMsg'), err.message, 'error'); }
  };
}
