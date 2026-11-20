/* =========================================================
   Vận hành → Phần cứng (ICO v2 G4c): Dell OME servers, list → detail.
   List: overall status (OME rollup, failing parts, disks predicting failure, firmware behind the baseline),
   Dell warranty vs Opex, host / systems / VMs from the CI registry. Filters: status, model, warranty ending within
   N days, owner (global owner filter, G2c — added by apiRequest / uiDownload).
   Data: /api/hardware, /api/hardware/servers/<source>/<device>, /api/hardware/report.pdf|xlsx.
   ========================================================= */
const hwState = { data: null, error: null, status: '', model: '', days: '', q: '', detail: null };
const HW_DAYS = [['', 'Mọi hạn bảo hành'], ['30', 'Hết trong 30 ngày'], ['90', 'Hết trong 90 ngày'], ['180', 'Hết trong 180 ngày'], ['365', 'Hết trong 1 năm']];
const HW_OPEX = { active: ['ok', 'Còn hiệu lực'], planned: ['warn', 'Chưa bắt đầu'], expired: ['bad', 'Đã hết hạn'], none: ['na', 'Chưa có Opex'] };
const hwDate = (iso) => iso ? String(iso).slice(0, 10).split('-').reverse().join('/') : '—';

function hwQuery() {
  const p = new URLSearchParams();
  if (hwState.status) p.set('status', hwState.status);
  if (hwState.model) p.set('model', hwState.model);
  if (hwState.days) p.set('warrantyDays', hwState.days);
  return p.toString();
}

async function hwLoad() {
  try { hwState.data = await apiRequest(`/hardware${hwQuery() ? `?${hwQuery()}` : ''}`); hwState.error = null; }
  catch (err) { hwState.error = err.message; }
}

async function renderHardwareView() {
  const mount = $('#hardwareMount');
  if (!mount) return;
  hwBind(mount);
  const q = new URLSearchParams((location.hash.split('?')[1]) || '');
  if (q.get('s') && q.get('d')) { await hwOpenDetail(mount, q.get('s'), Number(q.get('d'))); return; }
  hwState.detail = null;
  mount.innerHTML = uiLoading();
  await hwLoad();
  hwRender(mount);
}

function hwLevel(level) { const [tone, text] = UI_LEVELS[level] || UI_LEVELS.info; return uiPill(tone, text); }

function hwWarrantyCell(w) {
  if (!w || !w.dellEnd) return '<span class="muted-sub">Chưa có dữ liệu</span>';
  const d = w.daysLeft;
  const note = d < 0 ? `hết ${fmtInt(-d)} ngày trước` : `còn ${fmtInt(d)} ngày`;
  return `${esc(hwDate(w.dellEnd))}<span class="muted-sub">${esc(note)}</span>`;
}

function hwOpexCell(w) {
  if (!w) return '—';
  const [tone, text] = HW_OPEX[w.opexState] || HW_OPEX.none;
  return `${w.opexCode ? `<span class="mono">${esc(w.opexCode)}</span>` : uiPill(tone, text)}${w.opexEnd ? `<span class="muted-sub">hết ${esc(hwDate(w.opexEnd))}</span>` : ''}`;
}

function hwRender(mount) {
  if (hwState.error) { mount.innerHTML = uiError(hwState.error); return; }
  const d = hwState.data, s = d.summary;
  if (!d.configured) {
    mount.innerHTML = `<section class="panel">${uiEmpty(uiIsAdmin() ? 'Chưa có nguồn Dell OpenManage Enterprise — thêm ở Cài đặt → Nguồn dữ liệu → “+ Dell OME”.'
      : 'Chưa có nguồn Dell OpenManage Enterprise — quản trị viên thêm ở Cài đặt → Nguồn dữ liệu.')}</section>`;
    return;
  }
  const opt = (v, t, cur) => `<option value="${esc(v)}"${String(v) === String(cur) ? ' selected' : ''}>${esc(t)}</option>`;
  const q = hwState.q.trim().toLowerCase();
  const list = q ? d.items.filter(i => [i.name, i.serviceTag, i.model, i.idracIp, ...i.hosts.map(h => h.name), ...i.systems.map(x => x.code)]
    .some(x => String(x || '').toLowerCase().includes(q))) : d.items;
  const rows = list.slice(0, 1000).map(i => `<tr class="infra-row" tabindex="0" data-hw-open="${esc(i.sourceKey)}|${esc(i.deviceId)}">
      <td><b>${esc(i.name)}</b><span class="muted-sub">${esc([i.serviceTag, i.model].filter(Boolean).join(' · '))}</span></td>
      <td class="dc-status">${hwLevel(i.level)}${i.reasons.length ? `<span class="muted-sub">${esc(i.reasons.slice(0, 2).join('; '))}</span>` : ''}</td>
      <td>${hwWarrantyCell(i.warranty)}</td>
      <td>${hwOpexCell(i.warranty)}</td>
      <td>${esc(i.hosts.map(h => h.name).join(', ') || 'bare-metal')}${i.systems.length ? `<span class="muted-sub">${esc(i.systems.map(x => x.code).join(', '))}</span>` : ''}</td>
      <td class="num">${fmtInt(i.vmCount)}</td></tr>`).join('');
  const src = d.sources.filter(x => !x.never).map(x => `${x.name}: ${x.capturedAt ? `đồng bộ ${uiDateTime(x.capturedAt, 'short')}` : 'chưa có dữ liệu'}${x.baselineConfigured === false ? ' · chưa cấu hình baseline firmware' : ''}`).join(' · ');
  /* Sources not synced yet / failing / partly read: one box, each with its state pill, sync button for the admin. */
  const issues = d.sources.filter(x => x.never || x.lastError || x.lastWarning).map(x => x.never
    ? `<li>${uiPill('na', 'Chưa đồng bộ')} <b>${esc(x.name)}</b> — chưa đọc lần nào; dữ liệu có sau vòng đồng bộ kế tiếp${uiIsAdmin() ? ' hoặc khi bấm Đồng bộ ngay' : ''}.</li>`
    : x.lastError ? `<li>${uiPill('crit', 'Lỗi kết nối')} <b>${esc(x.name)}</b> — ${esc(uiDateTime(x.lastErrorAt, 'short'))}: ${esc(x.lastError)}${x.capturedAt ? ' Đang hiện dữ liệu lần đọc được gần nhất.' : ''}</li>`
    : `<li>${uiPill('warn', 'Đọc thiếu')} <b>${esc(x.name)}</b> — ${esc(x.lastWarning)}</li>`).join('');
  const notes = issues ? `<div class="insight-box hw-src-box"><ul class="hw-src-issues">${issues}</ul>
      ${uiIsAdmin() && d.sources.some(x => x.never || x.lastError) ? '<button class="btn btn-secondary btn-sm" type="button" data-hw-sync><i data-lucide="refresh-cw"></i>Đồng bộ ngay</button>' : ''}</div>` : '';
  mount.innerHTML = `${d.ownerScope ? uiNote(d.ownerScope) : ''}${notes}
    ${uiKpiStrip([
      { label: 'Máy chủ', value: uiNumber(s.servers), sub: `${uiNumber(s.vmsOn)} VM đang chạy` },
      { label: 'Cần xử lý', value: uiNumber(s.byLevel.crit + s.byLevel.bad + s.byLevel.warn), sub: `${uiNumber(s.byLevel.crit)} vượt ngưỡng · ${uiNumber(s.byLevel.bad)} cảnh báo`,
        status: s.byLevel.crit ? { cls: 'crit', text: 'Vượt ngưỡng' } : s.byLevel.bad ? { cls: 'bad', text: 'Cảnh báo' } : undefined },
      { label: 'Ổ dự báo hỏng', value: uiNumber(s.disksPredictive), sub: 'SMART', status: s.disksPredictive ? { cls: 'crit', text: 'Vượt ngưỡng' } : undefined },
      { label: 'Firmware lệch', value: uiNumber(s.firmwareBehind), sub: 'so baseline OME' },
      { label: 'Hết bảo hành Dell', value: uiNumber(s.warrantyExpired), sub: `${uiNumber(s.warrantyExpiring90)} hết trong 90 ngày` },
    ])}
    <section class="panel">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Máy chủ vật lý (Dell OME)</div>
        <p class="panel-sub">${esc(src)}</p></div>
        <div class="panel-toolbar infra-toolbar">
          <button class="btn btn-secondary btn-sm" type="button" data-hw-dl="pdf"><i data-lucide="file-text"></i>Báo cáo PDF</button>
          <button class="btn btn-ghost btn-sm" type="button" data-hw-dl="xlsx"><i data-lucide="sheet"></i>Excel</button></div></div>
      <div class="panel-toolbar infra-toolbar dc-filters">
        <input type="search" id="hwQ" placeholder="Tìm tên, service tag, host, hệ thống…" value="${esc(hwState.q)}" aria-label="Tìm máy chủ">
        <select id="hwStatus" class="hw-filter" aria-label="Trạng thái">${opt('', 'Mọi trạng thái', hwState.status)}${['crit', 'bad', 'warn', 'ok'].map(l => opt(l, UI_LEVELS[l][1], hwState.status)).join('')}</select>
        <select id="hwModel" class="hw-filter" aria-label="Model">${opt('', 'Mọi model', hwState.model)}${d.models.map(m => opt(m, m, hwState.model)).join('')}</select>
        <select id="hwDays" class="hw-filter" aria-label="Hạn bảo hành Dell">${HW_DAYS.map(([v, t]) => opt(v, t, hwState.days)).join('')}</select>
      </div>
      <div class="table-scroll" role="region" tabindex="0" aria-label="Máy chủ vật lý"><table class="data-table infra-table">
        <caption class="sr-only">Máy chủ vật lý, trạng thái và bảo hành</caption>
        <thead><tr><th>Máy chủ</th><th>Trạng thái</th><th>Bảo hành Dell</th><th>Opex bảo hành</th><th>Host · hệ thống</th><th class="num">VM</th></tr></thead>
        <tbody>${rows || uiEmptyRow(6, 'Không có máy chủ khớp bộ lọc.')}</tbody></table></div>
      <p class="muted-sub">${fmtInt(list.length)} / ${fmtInt(s.inScope)} máy chủ${list.length > 1000 ? ' (hiện 1.000 dòng đầu)' : ''}. Trạng thái tổng = mức xấu nhất của
        trạng thái OME, linh kiện lỗi, ổ dự báo hỏng (VƯỢT NGƯỠNG) và firmware lệch baseline (THEO DÕI).${d.opexDetail ? '' : ' Mã Opex cần quyền xem Chi phí.'}</p>
    </section>`;
  refreshIcons();
}

async function hwOpenDetail(mount, source, device) {
  mount.innerHTML = uiLoading();
  try { hwState.detail = await apiRequest(`/hardware/servers/${encodeURIComponent(source)}/${encodeURIComponent(device)}`); }
  catch (err) { mount.innerHTML = uiError(err.message); return; }
  mount.innerHTML = hwDetailHtml(hwState.detail);
  refreshIcons();
}

function hwDetailHtml(d) {
  const dl = (pairs) => `<dl class="sp-dl">${pairs.map(([k, v]) => `<div class="sp-dl-item"><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join('')}</dl>`;
  const table = (label, head, rows, empty) => rows.length
    ? `<div class="table-scroll" role="region" tabindex="0" aria-label="${esc(label)}"><table class="data-table"><thead><tr>${head.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead>
       <tbody>${rows.join('')}</tbody></table></div>` : uiNote(empty);
  const w = d.warranty || {};
  const idrac = d.idracUrl ? `<a href="${esc(d.idracUrl)}" target="_blank" rel="noopener noreferrer">${esc(d.idracIp)}</a>` : esc(d.idracIp || '—');
  const subs = d.subsystems.map(x => `<tr><td>${esc(x.subsystem)}</td><td>${hwLevel(x.status)}</td><td class="wrap">${esc(x.detail || '—')}</td></tr>`);
  const disks = d.disks.map(x => `<tr><td class="wrap">${esc(x.slot)}</td><td>${esc(x.model || '—')}<span class="muted-sub">${esc(x.serial || '')}</span></td>
      <td>${esc(x.media || '—')}</td><td class="num">${x.sizeGB != null ? `${uiNumber(x.sizeGB, 0)} GB` : '—'}</td>
      <td>${x.predictiveFailure ? uiPill('crit', 'Dự báo hỏng') : hwLevel(x.status)}</td></tr>`);
  const parts = d.parts.map(x => `<tr><td>${esc(x.kindText)}</td><td>${esc(x.name)}</td><td>${esc(x.model || '—')}<span class="muted-sub">${esc(x.serial || '')}</span></td>
      <td>${esc(x.detail || '—')}</td><td>${hwLevel(x.status)}</td></tr>`);
  const fwTone = { ok: 'ok', upgrade: 'warn', downgrade: 'warn', unknown: 'na' };
  const fw = d.firmwareRows.map(x => `<tr><td class="wrap">${esc(x.component)}</td><td class="mono">${esc(x.version || '—')}</td>
      <td class="mono">${esc(x.baselineVersion || '—')}</td><td>${x.compliance ? uiPill(fwTone[x.compliance] || 'na', x.complianceText) : uiTag(x.complianceText)}</td></tr>`);
  const warr = d.warranties.map(x => `<tr><td>${esc(x.serviceLevel || '—')}</td><td>${esc(hwDate(x.start))}</td><td>${esc(hwDate(x.end))}</td></tr>`);
  const vms = d.vms.map(v => `<tr><td>${uiEntityLink('vm', v.name, v.vcenterKey, false)}</td><td>${esc(v.system || '—')}</td>
      <td>${v.powerOn ? uiPill('ok', 'Bật') : uiPill('na', 'Tắt')}</td></tr>`);
  const sevTone = { crit: 'crit', bad: 'bad', warn: 'warn', info: 'na' };
  const alerts = d.alerts.map(a => `<tr><td>${esc(uiDateTime(a.at, 'short'))}</td><td>${uiPill(sevTone[a.severity] || 'na', (UI_LEVELS[a.severity] || UI_LEVELS.info)[1])}</td>
      <td>${esc(a.category || '—')}</td><td class="wrap">${esc(a.message || '—')}<span class="muted-sub">${esc(a.messageId || '')}</span></td></tr>`);
  const baseline = d.source && d.source.baselineConfigured === false ? uiNote('OME chưa cấu hình baseline firmware — chưa so được phiên bản.') : '';
  return `<p><button class="btn btn-ghost btn-sm" type="button" data-hw-back><i data-lucide="arrow-left"></i>Danh sách máy chủ</button></p>
    <section class="panel"><div class="panel-head"><div><div class="eyebrow">Máy chủ vật lý · Dell OME</div>
      <div class="panel-title" role="heading" aria-level="2">${esc(d.name)} ${hwLevel(d.level)}</div>
      <p class="panel-sub">${esc([d.serviceTag, d.model, d.powerText].filter(Boolean).join(' · '))}</p></div>
      <div class="panel-toolbar">${d.serviceTag ? `<a class="btn btn-ghost btn-sm" href="${esc(uiEntityHref('server', d.serviceTag))}"><i data-lucide="box"></i>Thẻ đối tượng</a>` : ''}</div></div>
      ${d.reasons && d.reasons.length ? `<ul class="en-links">${d.reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}
      ${dl([['Service tag', esc(d.serviceTag || '—')], ['iDRAC', idrac], ['Hệ điều hành', esc(d.os || '—')], ['Hostname', esc(d.osHostname || '—')],
            ['Kiểm kê cuối', esc(uiDateTime(d.lastInventoryAt))], ['Nguồn OME', esc(d.source ? d.source.name : '—')]])}</section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Sức khoẻ phân hệ</div>
      ${table('Sức khoẻ phân hệ', ['Phân hệ', 'Trạng thái', 'Chi tiết'], subs, 'Mọi phân hệ bình thường (OME chỉ báo chi tiết khi có bất thường).')}</section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Ổ đĩa (${fmtInt(d.disks.length)})</div>
      ${table('Ổ đĩa', ['Vị trí', 'Model · SN', 'Loại', 'Dung lượng', 'Trạng thái'], disks, 'Chưa có inventory ổ đĩa.')}</section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Bộ nguồn, RAID, RAM</div>
      ${table('Linh kiện', ['Loại', 'Tên', 'Model · SN', 'Thông số', 'Trạng thái'], parts, 'Chưa có inventory linh kiện.')}</section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Firmware so baseline</div>${baseline}
      ${table('Firmware', ['Linh kiện', 'Phiên bản', 'Baseline', 'So baseline'], fw, 'Chưa có inventory firmware.')}</section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Bảo hành Dell ↔ Opex</div>
      ${dl([['Bảo hành Dell đến', hwWarrantyCell(w)], ['Opex bảo hành', hwOpexCell(w)],
            ['Chênh lệch', w.case ? `${uiPill(w.caseLevel === 'crit' ? 'crit' : 'warn', w.caseLabel)}<span class="muted-sub mono">${esc(w.code)}</span>` : 'Không có']])}
      ${table('Gói bảo hành Dell', ['Gói', 'Từ', 'Đến'], warr, 'OME chưa có dữ liệu bảo hành Dell của máy này.')}</section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Host, hệ thống và VM chạy trên máy</div>
      ${dl([['Host ảo hoá', d.hosts && d.hosts.length ? d.hosts.map(h => `${uiEntityLink('host', h.name, h.vcenterKey, false)}${h.matchedBy === 'hostname' ? ' ' + uiTag('ghép theo tên') : ''}`).join(', ') : 'Không có (bare-metal)'],
            ['Hệ thống', d.systems && d.systems.length ? d.systems.map(x => `${esc(x.code)} — ${esc(x.name)} (mức ${esc(x.criticality)})`).join('<br>') : '—']])}
      ${table('VM', ['VM', 'Hệ thống', 'Nguồn'], vms, 'Không có VM chạy trên máy này.')}
      ${d.vmsTruncated ? '<p class="muted-sub">Danh sách VM đã rút gọn.</p>' : ''}</section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Alert phần cứng gần đây</div>
      ${table('Alert phần cứng', ['Thời điểm', 'Mức', 'Nhóm', 'Nội dung'], alerts, 'Không có alert phần cứng.')}</section>`;
}

function hwBind(mount) {
  if (mount.dataset.bound) return;
  mount.dataset.bound = '1';
  mount.addEventListener('change', async (e) => {
    const id = e.target.id;
    if (!['hwStatus', 'hwModel', 'hwDays'].includes(id)) return;
    hwState[{ hwStatus: 'status', hwModel: 'model', hwDays: 'days' }[id]] = e.target.value;
    mount.innerHTML = uiLoading();
    await hwLoad();
    hwRender(mount);
  });
  mount.addEventListener('input', (e) => {
    if (e.target.id !== 'hwQ') return;
    hwState.q = e.target.value;
    const pos = e.target.selectionStart;
    hwRender(mount);
    const el = $('#hwQ'); if (el) { el.focus(); el.setSelectionRange(pos, pos); }
  });
  mount.addEventListener('click', (e) => {
    const dl = e.target.closest('[data-hw-dl]');
    if (dl) {
      const f = dl.dataset.hwDl;
      uiDownload(`${API_BASE}/hardware/report.${f}${hwQuery() ? `?${hwQuery()}` : ''}`, { method: 'GET' }, `ICO_Suc_khoe_phan_cung.${f}`).catch(err => uiAlert(err.message));
      return;
    }
    if (e.target.closest('[data-hw-back]')) { location.hash = '#/hardware'; return; }
    const sb = e.target.closest('[data-hw-sync]');
    if (sb && typeof syncAllSources === 'function') {
      sb.disabled = true;
      sb.textContent = 'Đang đồng bộ…';
      syncAllSources().then(r => { if (r.failed) uiAlert(r.message); renderHardwareView(); })
        .catch(err => { uiAlert(err.message); sb.disabled = false; });
      return;
    }
    const row = e.target.closest('[data-hw-open]');
    if (row && !e.target.closest('a')) {
      const [s, dId] = row.dataset.hwOpen.split('|');
      location.hash = `#/hardware?s=${encodeURIComponent(s)}&d=${encodeURIComponent(dId)}`;
    }
  });
  mount.addEventListener('keydown', (e) => {
    const row = e.target.closest('[data-hw-open]');
    if (row && e.key === 'Enter') row.click();
  });
}
