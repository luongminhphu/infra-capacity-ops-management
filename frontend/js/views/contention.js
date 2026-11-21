/* =========================================================
   Capacity → Nghẽn tài nguyên (ICO v2 G9c): CPU ready / co-stop, balloon, swap, disk latency read with QueryPerf
   (realtime, 15 minutes averaged at each sync). Data: /api/contention (contention/service.report — same as Hộp việc).
   Separate from the overcommit % of the Capacity tab (allocation vs threshold).
   ========================================================= */
const ctState = { q: '', data: null, chart: null };
const CT_LV = (lv) => uiLevelPill(lv === 'ok' ? 'ok' : lv);
const ctN = (v, d = 1) => (v === null || v === undefined ? '—' : uiNumber(v, d));

async function renderContentionView() {
  const mount = $('#contentionMount');
  if (!mount) return;
  ctBind(mount);
  const p = new URLSearchParams((location.hash.split('?')[1]) || '');
  if (p.has('q')) ctState.q = p.get('q');
  mount.innerHTML = uiLoading();
  try {
    ctState.data = await apiRequest('/contention');
    ctRender(mount);
  } catch (err) { mount.innerHTML = uiError(err.message); }
}

function ctMatch(r) {
  const q = ctState.q.toLowerCase();
  return !q || [r.name, r.cluster, r.host, r.ip, r.vcenterKey].some(v => String(v || '').toLowerCase().includes(q));
}

function ctRender(mount) {
  const d = ctState.data, s = d.summary, t = d.thresholds;
  const vms = d.vms.filter(ctMatch);
  const vmRows = vms.slice(0, 200).map(r => `<tr class="infra-row" tabindex="0" data-ct-open="vm|${esc(r.vcenterKey)}|${esc(r.name)}">
      <td>${CT_LV(r.level)}</td><td>${uiEntityLink('vm', r.name, r.vcenterKey)} <span class="muted-sub mono sr-inline">${esc(r.ip || '—')}</span>
        <span class="muted-sub">${esc(r.cluster || '—')} · ${uiNumber(r.vcpu)} vCPU</span></td>
      <td class="num">${ctN(r.readyPct, 2)}</td><td class="num">${ctN(r.costopPct, 2)}</td><td class="num">${ctN(r.balloonMB, 0)}</td>
      <td class="num">${ctN(r.swappedMB, 0)}</td><td class="num">${ctN(r.latencyMs)}</td><td>${esc(r.reasons.join('; '))}</td></tr>`).join('');
  const hostRows = d.hosts.filter(ctMatch).map(r => `<tr class="infra-row" tabindex="0" data-ct-open="host|${esc(r.vcenterKey)}|${esc(r.name)}">
      <td>${CT_LV(r.level)}</td><td>${uiEntityLink('host', r.name, r.vcenterKey)}<span class="muted-sub">${esc(r.cluster || '—')}</span></td>
      <td class="num">${ctN(r.latencyMs)}</td><td class="num">${ctN(r.balloonMB, 0)}</td><td>${esc(r.reasons.join('; '))}</td></tr>`).join('');
  const clRows = d.clusters.filter(c => !ctState.q || c.cluster.toLowerCase().includes(ctState.q.toLowerCase())).map(c => `<tr>
      <td>${CT_LV(c.level)}</td><td><b>${esc(c.cluster || '—')}</b><span class="muted-sub">${esc(c.vcenterKey)}</span></td>
      <td class="num">${uiNumber(c.hot)} / ${uiNumber(c.vms)}</td><td class="num">${uiNumber(c.sharePct, 1)}%</td><td class="num">${uiNumber(c.readyAvg, 2)}%</td></tr>`).join('');
  mount.innerHTML = `<section class="panel"><div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Nghẽn tài nguyên</div>
      <p class="panel-sub">Đọc từ vCenter mỗi lần đồng bộ (trung bình 15 phút gần nhất, chỉ đọc). Ngưỡng: CPU ready ≥ ${uiNumber(t.ready[0])}% theo dõi, ≥ ${uiNumber(t.ready[1])}% cảnh báo;
        co-stop ≥ ${uiNumber(t.costop[0])}%; độ trễ disk ≥ ${uiNumber(t.latency[0])} / ${uiNumber(t.latency[1])} ms; balloon / swap kéo dài ≥ ${uiNumber(t.sustained)} lần đồng bộ;
        cluster có ≥ ${uiNumber(t.clusterSharePct)}% VM nghẽn là cảnh báo. Khác % ngưỡng overcommit ở tab Capacity (cấp phát).</p></div></div>
      ${uiKpiStrip([{ label: 'VM nghẽn', value: uiNumber(s.hotVms), sub: `${uiNumber(s.badVms)} mức cảnh báo · ${uiNumber(s.vms)} VM đã đo` },
        { label: 'Host nghẽn', value: uiNumber(s.hotHosts), sub: `${uiNumber(s.hosts)} host đã đo` },
        { label: 'Cluster cảnh báo', value: uiNumber(d.clusters.filter(c => c.level !== 'ok').length), sub: `${uiNumber(d.clusters.length)} cluster` },
        { label: 'Đo lúc', value: s.capturedAt ? uiDateTime(s.capturedAt, 'short') : '—' }])}
      <div class="form-grid-aligned"><label class="field"><span>Tìm</span><input type="search" data-ct-q value="${esc(ctState.q)}" maxlength="120" placeholder="VM, IP, host, cluster"></label></div></section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Theo cluster</div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Mức</th><th>Cluster</th><th class="num">VM nghẽn</th><th class="num">Tỷ lệ</th><th class="num">CPU ready TB</th></tr></thead>
        <tbody>${clRows || uiEmptyRow(5, 'Chưa có số đo.')}</tbody></table></div></section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">VM (nghẽn nhiều nhất trước)</div>
      <p class="panel-sub">Bấm một dòng để xem xu hướng 7 / 30 ngày.</p>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Mức</th><th>VM</th><th class="num">Ready %</th><th class="num">Co-stop %</th>
        <th class="num">Balloon MB</th><th class="num">Swap MB</th><th class="num">Độ trễ ms</th><th>Lý do</th></tr></thead>
        <tbody>${vmRows || uiEmptyRow(8, 'Không có VM khớp.')}</tbody></table></div>
      ${vms.length > 200 ? `<p class="muted-sub">Hiện 200 / ${uiNumber(vms.length)} VM — lọc thêm để xem phần còn lại.</p>` : ''}</section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Host</div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Mức</th><th>Host</th><th class="num">Độ trễ ms</th><th class="num">Balloon MB</th><th>Lý do</th></tr></thead>
        <tbody>${hostRows || uiEmptyRow(5, 'Không có host khớp.')}</tbody></table></div></section>`;
  refreshIcons();
}

async function ctOpen(kind, vc, name, days = 7) {
  const body = (h) => `<div class="form-grid-aligned"><label class="field"><span>Khoảng</span><select data-ct-days="${esc(kind)}|${esc(vc)}|${esc(name)}">
      ${[7, 30].map(n => `<option value="${n}"${n === days ? ' selected' : ''}>${n} ngày</option>`).join('')}</select></label></div>${h}`;
  uiPanelDialog(`${kind === 'vm' ? 'VM' : 'Host'} ${esc(name)}`, body(uiLoading()), '<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Đóng</button>');
  let h;
  try { h = await apiRequest(`/contention/history?${new URLSearchParams({ kind, name, vc, days })}`); }
  catch (err) { $('#spDialogBody .sp-dialog-body').innerHTML = uiError(err.message); return; }
  const keys = Object.keys(h);
  const n = keys.length ? h[keys[0]].length : 0;
  $('#spDialogBody .sp-dialog-body').innerHTML = body(n < 2 ? uiNote('Chưa đủ số đo để vẽ xu hướng (cần ít nhất 2 lần đồng bộ).')
    : '<div class="infra-chart"><canvas id="ctChart" aria-label="Xu hướng nghẽn tài nguyên"></canvas></div>');
  $('#spDialogBody').onchange = (e) => {
    const sel = e.target.closest('[data-ct-days]');
    if (sel) { const [k, v, nm] = sel.dataset.ctDays.split('|'); ctOpen(k, v, nm, Number(sel.value)); }
  };
  if (n < 2 || typeof Chart === 'undefined') return;
  if (ctState.chart) { ctState.chart.destroy(); ctState.chart = null; }
  const pal = chartPalette();
  const label = { readyPct: 'CPU ready (%)', costopPct: 'Co-stop (%)', latencyMs: 'Độ trễ disk (ms)', balloonMB: 'Balloon (MB)' };
  ctState.chart = new Chart($('#ctChart'), {
    type: 'line',
    data: { labels: h[keys[0]].map(p => uiDateTime(p[0], 'short')),
      datasets: keys.map((k, i) => ({ label: label[k] || k, data: h[k].map(p => p[1]), borderColor: chartSeriesColor(i), backgroundColor: chartSeriesColor(i),
        pointRadius: 0, tension: 0.2, yAxisID: k === 'latencyMs' || k === 'balloonMB' ? 'y1' : 'y' })) },
    options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      scales: { y: { beginAtZero: true, position: 'left', ticks: { color: pal.axis }, grid: { color: pal.grid } },
                y1: { beginAtZero: true, position: 'right', ticks: { color: pal.axis }, grid: { display: false } },
                x: { ticks: { color: pal.axis, maxTicksLimit: 8 }, grid: { display: false } } },
      plugins: { legend: { labels: { color: pal.text } }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${uiNumber(c.parsed.y, 2)}` } } } },
  });
}

function ctBind(mount) {
  if (mount.dataset.bound) return;
  mount.dataset.bound = '1';
  mount.addEventListener('input', (e) => {
    if (!e.target.closest('[data-ct-q]')) return;
    ctState.q = e.target.value.trim();
    clearTimeout(ctBind.t);
    ctBind.t = setTimeout(() => { const pos = e.target.selectionStart; ctRender(mount); const el = mount.querySelector('[data-ct-q]'); el.focus(); el.setSelectionRange(pos, pos); }, 300);
  });
  const open = (e) => {
    if (e.target.closest('a')) return;
    const row = e.target.closest('[data-ct-open]');
    if (row) { const [k, vc, ...name] = row.dataset.ctOpen.split('|'); ctOpen(k, vc, name.join('|')); }
  };
  mount.addEventListener('click', open);
  mount.addEventListener('keydown', (e) => { if (e.key === 'Enter') open(e); });
}

/* Entity cards (VM / host): the latest figures. */
function enContentionHtml(r) {
  if (!r) return '';
  const vm = r.kind === 'vm';
  return `<section class="panel"><div class="panel-head"><div class="panel-title" role="heading" aria-level="2">Nghẽn tài nguyên ${CT_LV(r.level)}</div>
      <div class="panel-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-nav-link="cap-contention">Nghẽn tài nguyên</button></div></div>
    ${enDl([...(vm ? [['CPU ready', `${ctN(r.readyPct, 2)}%`], ['Co-stop', `${ctN(r.costopPct, 2)}%`], ['Swap', `${ctN(r.swappedMB, 0)} MB`]] : []),
      ['Balloon', `${ctN(r.balloonMB, 0)} MB`], ['Độ trễ disk', `${ctN(r.latencyMs)} ms`]])}
    ${r.reasons.length ? `<p class="muted-sub">${esc(r.reasons.join('; '))}</p>` : ''}</section>`;
}
