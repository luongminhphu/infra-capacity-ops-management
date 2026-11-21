/* =========================================================
   Super app SA3 — thẻ đối tượng (Host / VM) and global search
   (Ctrl + K command palette). docs/design/super-app.md §2.3–2.4.
   ========================================================= */

/* ---------------------------------------------------------------- entity page (#/entity?type=host|vm|server|datastore&name=…&vc=…&id=…) */
const EN_TYPES = ['host', 'vm', 'server', 'datastore', 'ci'];
function entityParams() {
  const q = new URLSearchParams((location.hash.split('?')[1]) || '');
  const type = EN_TYPES.includes(q.get('type')) ? q.get('type') : 'host';
  return { type, name: q.get('name') || '', vc: q.get('vc') || '', id: q.get('id') || '' };
}

function entityUrl(p) {
  if (p.type === 'server') return `/entities/server/${encodeURIComponent(p.name)}`;
  if (p.type === 'datastore') return `/entities/datastore/${encodeURIComponent(p.id)}`;
  if (p.type === 'ci') return `/ci/${encodeURIComponent(p.id)}`;
  return `/entities/${p.type}?${new URLSearchParams({ name: p.name, vcenter: p.vc })}`;
}

const enPct = (v) => (v === null || v === undefined) ? '—' : `${Math.round(v)}%`;
const enVal = (v, suffix = '') => (v === null || v === undefined || v === '') ? '—' : `${esc(v)}${suffix}`;
const enDl = (pairs) => `<dl class="sp-dl en-dl">${pairs.map(([l, v]) => `<div class="sp-dl-item"><dt>${esc(l)}</dt><dd>${v}</dd></div>`).join('')}</dl>`;
const EN_SEV = { critical: ['crit', 'Nghiêm trọng'], warning: ['warn', 'Cảnh báo'], info: ['na', 'Thông tin'] };

async function renderEntityView() {
  const mount = $('#entityMount');
  if (!mount) return;
  const p = entityParams();
  if (['datastore', 'ci'].includes(p.type) ? !/^\d+$/.test(p.id) : !p.name) { mount.innerHTML = uiEmpty('Chưa chọn đối tượng — tìm bằng Ctrl + K.'); return; }
  mount.innerHTML = uiNote('Đang tải…');
  let d;
  try {
    d = await apiRequest(entityUrl(p));
    if (p.type === 'ci') {             // generic CI card: registry entry + its downstream impact
      const imp = await apiRequest(`/ci/${encodeURIComponent(p.id)}/impact?direction=down`);
      d.ci = { id: d.id, keys: d.keys, links: d.links, linksTruncated: d.linksTruncated, impact: imp };
    }
  }
  catch (err) { mount.innerHTML = `<section class="panel">${uiEmpty(err.message)}</section>`; return; }
  $('#pageTitle').textContent = d.name;
  document.title = `${d.name} — Infra Capacity Ops Management`;
  const body = { host: entityHostHtml, vm: entityVmHtml, server: entityServerHtml, datastore: entityDatastoreHtml, ci: entityCiHtml }[p.type](d);
  mount.innerHTML = body + enCiHtml(d.ci);
  refreshIcons();
  const ciId = d.ci && d.ci.id;
  if (ciId && typeof incEventsHtml === 'function') {         // event timeline (G5) + change log (G6) of this CI
    Promise.all([apiRequest(`/ci/${encodeURIComponent(ciId)}/timeline`), apiRequest(`/ci/${encodeURIComponent(ciId)}/changes`)]).then(([t, c]) => {
      mount.insertAdjacentHTML('beforeend', `<section class="panel"><div class="panel-title" role="heading" aria-level="2">Timeline sự kiện</div>
        ${incEventsHtml(t.items)}</section>
        <section class="panel"><div class="panel-title" role="heading" aria-level="2">Thay đổi gần đây</div>${chgListHtml(c.items)}</section>`);
      refreshIcons();
    }).catch(() => {});
  }
}

function enFindings(fs) {
  if (!fs.length) return '<p class="qt-ok">Không có phát hiện compliance.</p>';
  return `<ul class="home-list">${fs.map(f => `<li class="home-item"><div class="home-item-main">${uiPill(...(EN_SEV[f.severity] || ['na', f.severity]))}
    <b>${esc(f.typeLabel)}</b> <span class="muted-sub sr-inline mono">${esc(f.code || '')}</span>
    <p class="home-item-detail">${esc(f.metric || '')} — ${esc(f.recommendation || '')}</p></div></li>`).join('')}</ul>`;
}

function entityHostHtml(d) {
  const hw = d.hardware, c = d.capacity, cl = d.clusterStatus;
  const vms = d.vms.map(v => `<tr><td>${uiEntityLink('vm', v.name, d.vcenterKey, false)}</td><td>${v.powerOn ? uiPill('ok', 'Bật') : uiPill('na', 'Tắt')}</td>
      <td class="num">${enVal(v.vcpu)}</td><td class="num">${enVal(v.ramGB)}</td><td>${enVal(v.ip)}</td></tr>`).join('');
  const dev = d.devices === null ? '' : `<section class="panel"><div class="panel-title" role="heading" aria-level="2">Bảo hành / hợp đồng (Opex)</div>
      ${d.devices.length ? `<ul class="home-list">${d.devices.map(x => `<li class="home-item"><div class="home-item-main">
        <a class="home-item-title" href="${esc(homeLink('spend-opex', `code=${encodeURIComponent(x.code)}`))}">${esc(x.code)} — ${esc(x.title)}</a>
        <p class="home-item-detail">Serial ${esc(x.serial)} · hết hạn ${esc(spDateSafe(x.endDate))} · ${esc(x.statusLabel)}${x.matchedBy === 'name' ? ' ' + uiTag('ghép theo tên', 'Host không báo serial: ghép theo tên host khai trong Opex') : ''}</p></div></li>`).join('')}</ul>`
        : '<p class="muted-sub">Chưa có khoản Opex nào khai báo thiết bị này (Opex → Thiết bị liên quan → tên host).</p>'}</section>`;
  return `
    <section class="panel"><div class="panel-head"><div><div class="eyebrow">Host · ${esc(d.vcenterKey || 'import')}</div>
      <div class="panel-title" role="heading" aria-level="2">${esc(d.name)} ${c.overall ? uiPill(c.overall.cls, c.overall.text) : ''}</div>
      <p class="panel-sub">Cluster <b>${esc(d.cluster || '—')}</b> · ${esc(c.vmCount ?? 0)} VM đang chạy</p></div>
      <div class="panel-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-nav-link="dashboard">Capacity</button>
        <button class="btn btn-ghost btn-sm" type="button" data-nav-link="lic-hardware">ESXi & phần cứng</button></div></div>
      ${enDl([['CPU dùng (theo ngưỡng)', enPct(c.cpuPct)], ['RAM dùng (theo ngưỡng)', enPct(c.ramPct)], ['vCPU cấp', enVal(c.vcpuUsed)],
              ['RAM cấp (GB)', enVal(c.ramUsedGB)], ['Hãng / model', `${enVal(hw.vendor)} ${hw.model ? esc(hw.model) : ''}`],
              ['CPU', `${enVal(hw.cpuModel)}`], ['Socket × core', `${enVal(hw.cpuSockets)} × ${enVal(hw.cores)}`], ['RAM (GB)', enVal(hw.ramGB)],
              ['ESXi', `${enVal(hw.esxiVersion)} ${hw.esxiBuild ? `(build ${esc(hw.esxiBuild)})` : ''}`], ['Service Tag', d.server ? enServerLink(d.server.serial) : enVal(hw.serial)], ['Trạng thái host', enVal(hw.status)]])}
    </section>
    ${cl ? `<section class="panel"><div class="panel-title" role="heading" aria-level="2">Cluster ${esc(cl.name)} ${uiPill(cl.overall.cls, cl.overall.text)}</div>
      ${enDl([['Host', enVal(cl.hosts)], ['VM', enVal(cl.vms)], ['CPU', enPct(cl.cpuPct)], ['RAM', enPct(cl.ramPct)],
              ['HA', cl.ha === true ? 'Bật' : cl.ha === false ? 'Tắt' : '—'], ['DRS', cl.drs === true ? 'Bật' : cl.drs === false ? 'Tắt' : '—']])}</section>` : ''}
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Phát hiện compliance</div>${enFindings(d.findings)}</section>
    ${dev}
    <section class="table-card"><div class="panel-title" role="heading" aria-level="2">VM trên host (${esc(d.vms.length)})</div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>VM</th><th>Nguồn</th><th class="num">vCPU</th><th class="num">RAM (GB)</th><th>IP</th></tr></thead>
      <tbody>${vms || `<tr><td colspan="5">${uiEmpty('Không có VM.')}</td></tr>`}</tbody></table></div></section>`;
}

function entityVmHtml(d) {
  const c = d.config, os = d.os, b = d.backup;
  const cov = b ? { protected: ['ok', 'Có backup'], failing: ['warn', 'Có job nhưng lỗi'], unprotected: ['crit', 'Chưa backup'] }[b.coverage] : null;
  const backup = b === null ? '' : `<section class="panel"><div class="panel-title" role="heading" aria-level="2">Backup ${cov ? uiPill(...cov) : uiPill('na', 'Không xác định')}</div>
      ${b.client ? enDl([['Client', `${enVal(b.client.client)}${b.matchedBy ? ' ' + uiTag('ghép theo tên', EN_MATCH[b.matchedBy] || '') : ''}`], ['Đánh giá', uiPill(({ ok: 'ok', warn: 'warn', crit: 'crit' })[b.client.status] || 'na', b.client.statusLabel)],
                         ['Policy', enVal((b.client.policies || []).join(', '))], ['Thành công gần nhất', b.client.lastSuccessAt ? esc(spTimeSafe(b.client.lastSuccessAt)) : '—'],
                         ['Đã qua (giờ)', enVal(b.client.hoursSinceSuccess)], ['RPO (giờ)', enVal(b.client.rpoHours)],
                         ['Lỗi liên tiếp', enVal(b.client.consecutiveFail)], ['Kết quả lần cuối', enVal(b.client.lastStatusText)]])
        : '<p class="muted-sub">Không khớp client backup nào (theo tên VM, hostname guest hoặc IP).</p>'}
      <button class="btn btn-ghost btn-sm" type="button" data-nav-link="bk-coverage">Độ phủ backup VM</button></section>`;
  return `
    <section class="panel"><div class="panel-head"><div><div class="eyebrow">VM · ${esc(d.vcenterKey || 'import')}</div>
      <div class="panel-title" role="heading" aria-level="2">${esc(d.name)} ${c.isPoweredOn ? uiPill('ok', 'Đang chạy') : uiPill('na', 'Tắt')}</div>
      <p class="panel-sub">Cluster <b>${esc(d.cluster || '—')}</b> · host ${d.host ? uiEntityLink('host', d.host, d.vcenterKey, false) : '—'}</p></div>
      <div class="panel-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-nav-link="history">Xu hướng & biến động</button>
        <button class="btn btn-ghost btn-sm" type="button" data-nav-link="lic-os">Hệ điều hành</button></div></div>
      ${enDl([['vCPU', enVal(c.vcpu)], ['RAM (GB)', enVal(c.ramGB)], ['Disk cấp / dùng (GB)', `${enVal(c.diskProvGB)} / ${enVal(c.diskUsedGB)}`],
              ['Datastore', enVal(c.datastore)], ['IP', enVal(c.ipAddress)], ['Hostname guest', enVal(c.guestHostname)],
              ['VMware Tools', enVal(c.toolsStatus)], ['Không hoạt động (ngày)', enVal(c.lastActivityDays)], ['Tắt (ngày)', enVal(c.powerOffDays)],
              ...(os ? [['Hệ điều hành', `${enVal(os.label)} ${os.version ? esc(os.version) : ''} ${os.edition ? esc(os.edition) : ''}${os.overridden ? ' ' + uiTag('ghi đè') : ''}`]] : [])])}
    </section>
    ${backup}
    ${enSystemHtml(d)}
    ${enItsmHtml(d.itsm)}
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Phát hiện compliance</div>${enFindings(d.findings)}</section>`;
}

/* ServiceDesk requests that created / changed / removed this VM (G7c): requested vs current resources. */
function enItsmHtml(list) {
  if (!list || !list.length) return '';
  const res = (o, k) => (o[k] === null || o[k] === undefined ? '—' : uiNumber(o[k]));
  const rows = list.map(r => {
    const q = r.requested, now = r.current;
    const want = ['vcpu', 'ramGB', 'diskGB'].map(k => `${q[k + 'Before'] !== null && q[k + 'Before'] !== undefined ? `${res(q, k + 'Before')} → ` : ''}${res(q, k)}`);
    const title = r.kind === 'new_vm' ? `Tạo theo yêu cầu #${esc(r.reqId)}` : `${esc(r.kindLabel)} #${esc(r.reqId)}`;
    return `<tr><td><b>${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${title}</a>` : title}</b>
        <span class="muted-sub">${esc(r.subject || '')}${r.createdAt ? ' · ' + esc(uiDateTime(r.createdAt, 'short')) : ''}</span></td>
      <td>${r.match === 'strong' ? uiTag('khớp chắc') : uiTag('khớp theo tên ngắn')}</td>
      <td class="num">${want[0]}<span class="muted-sub">hiện ${res(now, 'vcpu')}</span></td>
      <td class="num">${want[1]}<span class="muted-sub">hiện ${res(now, 'ramGB')}</span></td>
      <td class="num">${want[2]}<span class="muted-sub">hiện ${res(now, 'diskGB')}</span></td></tr>`;
  }).join('');
  return `<section class="panel"><div class="panel-head"><div class="panel-title" role="heading" aria-level="2">Yêu cầu ServiceDesk</div>
      <div class="panel-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-nav-link="itsm-requests">Nhu cầu hạ tầng</button></div></div>
    <div class="table-scroll"><table class="data-table"><thead><tr><th>Yêu cầu</th><th>Ghép</th><th>vCPU xin</th><th>RAM xin (GB)</th><th>Disk xin (GB)</th></tr></thead>
    <tbody>${rows}</tbody></table></div></section>`;
}

/* Hệ thống & Owner of a VM (G2a) + assign button for owner admins. */
function enSystemHtml(d) {
  if (!d.ci) return '';
  const s = d.system;
  const btn = d.canAssignSystem ? `<button class="btn btn-secondary btn-sm" type="button" data-en-assign="${esc(d.ci.id)}" data-en-vm="${esc(d.name)}">${s ? 'Đổi hệ thống' : 'Gán hệ thống'}</button>` : '';
  const body = s ? enDl([['Hệ thống', `${esc(s.name)} <span class="muted-sub mono">${esc(s.code)}</span>`],
      ['Owner', s.ownerEmail ? `${esc(s.ownerName || '')} <span class="mono">${esc(s.ownerEmail)}</span>` : uiPill('warn', 'Chưa có owner')],
      ...(s.deputyEmail ? [['Backup', `${esc(s.deputyName || '')} <span class="mono">${esc(s.deputyEmail)}</span>`]] : []),
      ...((s.supportEmails || []).length || s.supportName ? [['Support (đội dự án)', `${esc(s.supportName || '')}${(s.supportEmails || []).map(e => ` <span class="mono">${esc(e)}</span>`).join(',')}${s.supportContact ? `<span class="muted-sub">${esc(s.supportContact)}</span>` : ''}`]] : []),
      ['Dự án · phòng ban', esc([s.project, s.department].filter(Boolean).join(' · ') || '—')],
      ['Mức quan trọng', uiTag(s.criticalityLabel)], ['Gán bởi', esc(`${({ manual: 'Gán tay', itsm: 'Yêu cầu ServiceDesk' })[s.source] || s.source}${s.assignedBy ? ' · ' + s.assignedBy : ''}`)]])
    : '<p class="muted-sub">VM chưa thuộc hệ thống nào — chưa có owner.</p>';
  return `<section class="panel"><div class="panel-head"><div class="panel-title" role="heading" aria-level="2">Hệ thống & Owner</div>
      <div class="panel-toolbar">${btn}<button class="btn btn-ghost btn-sm" type="button" data-nav-link="systems">Hệ thống & Owner</button></div></div>${body}</section>`;
}

document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-en-assign]');
  if (!b || typeof osAssignDialog !== 'function') return;
  osAssignDialog([Number(b.dataset.enAssign)], b.dataset.enVm, () => renderEntityView());
});

/* ---------------------------------------------------------------- CI registry: links, impact, server, datastore (ICO v2 G1c) */
const EN_REL = {       // relation → [label seen from the source (out), label seen from the target (in)]
  hosted_on: ['Chạy trên host', 'VM trên host'], member_of: ['Thuộc cluster', 'Host trong cluster'],
  runs_on: ['Máy chủ vật lý', 'Host chạy trên máy này'], records: ['Ghi nhận máy chủ', 'Thiết bị Data Center'],
  covers: ['Bảo hành / hợp đồng cho', 'Opex bảo hành'], stored_on: ['Lưu trên datastore', 'VM trên datastore'],
  backed_by: ['Volume lưu trữ', 'Datastore dùng volume'], on: ['Thuộc mảng lưu trữ', 'Volume trên mảng'],
  protected_by: ['Client backup', 'VM được backup'],
};
const EN_CI_TYPE = { vm: 'VM', hypervisor_host: 'host', cluster: 'cluster', physical_server: 'máy chủ vật lý', datastore: 'datastore',
  storage_volume: 'volume', storage_array: 'mảng lưu trữ', backup_client: 'client backup', dc_asset: 'thiết bị DC', opex_item: 'khoản Opex' };
const EN_MATCH = { vm_name: 'Ghép theo tên VM', guest_hostname: 'Ghép theo hostname trong guest', ip: 'Ghép theo IP',
  host_name: 'Ghép theo tên host khai trong Opex', nfs_path: 'Ghép theo đường dẫn NFS' };
const EN_LINKS_SHOWN = 12;

function enServerLink(serial) {
  return `<a class="entity-link" href="${esc(uiEntityHref('server', serial))}" title="Mở thẻ máy chủ vật lý">${esc(serial)}</a>`;
}

function enPeer(p) {
  if (p.type === 'vm' || p.type === 'hypervisor_host') return uiEntityLink(p.type === 'vm' ? 'vm' : 'host', p.name, p.vcenterKey || '', false);
  if (p.type === 'physical_server') return enServerLink(p.name);
  if (p.type === 'datastore') return `<a class="entity-link" href="${esc(uiEntityHref('datastore', p.name, p.vcenterKey || '', p.id))}">${esc(p.name)}</a>`;
  return enCiLink(p.id, p.name);
}

function enCiLink(id, name) {
  return `<a class="entity-link" href="${esc(uiEntityHref('ci', name, '', id))}">${esc(name)}</a>`;
}

function enImpactText(imp) {
  if (!imp.total) return 'Không có đối tượng nào phụ thuộc vào đối tượng này.';
  const order = ['physical_server', 'hypervisor_host', 'cluster', 'datastore', 'storage_volume', 'vm', 'backup_client'];
  const parts = Object.keys(imp.counts).sort((a, b) => order.indexOf(a) - order.indexOf(b))
    .map(t => `${uiNumber(imp.counts[t])} ${EN_CI_TYPE[t] || t}`);
  return `Nếu đối tượng này lỗi: ${parts.join(', ')}${imp.truncated ? ' (đã rút gọn)' : ''}.`;
}

function enCiHtml(ci) {
  if (!ci) return '';
  const groups = new Map();
  ci.links.forEach(l => {
    const key = `${l.relation}:${l.direction}`;
    if (!groups.has(key)) groups.set(key, { label: (EN_REL[l.relation] || [l.relation, l.relation])[l.direction === 'out' ? 0 : 1], items: [] });
    groups.get(key).items.push(l);
  });
  const rows = [...groups.values()].map(g => {
    const shown = g.items.slice(0, EN_LINKS_SHOWN).map(l => `<li>${enPeer(l.peer)}${l.confidence === 'weak'
      ? ' ' + uiTag('ghép theo tên', EN_MATCH[(l.attrs || {}).matchedBy] || 'Ghép theo tên / IP, không dùng để gom sự cố') : ''}</li>`).join('');
    const more = g.items.length > EN_LINKS_SHOWN ? `<li class="muted-sub">và ${uiNumber(g.items.length - EN_LINKS_SHOWN)} đối tượng khác</li>` : '';
    return `<div class="sp-dl-item"><dt>${esc(g.label)} (${uiNumber(g.items.length)})</dt><dd><ul class="en-links">${shown}${more}</ul></dd></div>`;
  }).join('');
  const imp = ci.impact;
  return `<section class="panel"><div class="panel-title" role="heading" aria-level="2">Tác động</div>
      <p>${esc(enImpactText(imp))}</p>
      ${imp.weak ? `<p class="muted-sub">${esc(uiNumber(imp.weak))} đối tượng nối qua liên kết ghép theo tên.</p>` : ''}</section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Liên kết</div>
      ${rows ? `<dl class="sp-dl en-dl">${rows}</dl>` : uiEmpty('Chưa có liên kết trong sổ CI.')}
      ${ci.linksTruncated ? '<p class="muted-sub">Danh sách liên kết đã rút gọn.</p>' : ''}</section>`;
}

function entityServerHtml(d) {
  const hosts = d.hosts.length ? `<ul class="en-links">${d.hosts.map(h => `<li>${uiEntityLink('host', h.name, h.vcenterKey, false)}
      <span class="muted-sub">${esc(h.vcenterKey)} · cluster ${esc(h.cluster || '—')}</span></li>`).join('')}</ul>`
    : '<p class="muted-sub">Không có host ảo hoá nào báo serial này.</p>';
  const assets = d.dcAssets.length ? `<div class="table-scroll"><table class="data-table"><thead><tr><th>Thiết bị</th><th>Model</th><th>Asset tag</th>
      <th>Vị trí</th><th>Đưa vào dùng</th></tr></thead><tbody>${d.dcAssets.map(a => `<tr><td>${esc(a.deviceId || '—')}</td>
      <td>${esc([a.brand, a.model].filter(Boolean).join(' ') || '—')}</td><td>${esc(a.assetTag || '—')}</td><td>${esc(a.location || '—')}</td>
      <td>${esc(spDateSafe(a.acquiredOn))}</td></tr>`).join('')}</tbody></table></div>
      <button class="btn btn-ghost btn-sm" type="button" data-nav-link="dc-assets">Thiết bị Data Center</button>`
    : '<p class="muted-sub">Không có trong danh sách Thiết bị Data Center.</p>';
  const opex = d.opex === null ? '' : `<section class="panel"><div class="panel-title" role="heading" aria-level="2">Bảo hành / hợp đồng (Opex)</div>
      ${d.opex.length ? `<ul class="home-list">${d.opex.map(x => `<li class="home-item"><div class="home-item-main">
        <a class="home-item-title" href="${esc(homeLink('spend-opex', `code=${encodeURIComponent(x.code)}`))}">${esc(x.code)} — ${esc(x.title)}</a>
        <p class="home-item-detail">Serial ${esc(x.serial)} · hết hạn ${esc(spDateSafe(x.endDate))} · ${esc(x.statusLabel)}</p></div></li>`).join('')}</ul>`
        : '<p class="muted-sub">Chưa có khoản Opex nào khai serial này.</p>'}</section>`;
  const src = { host: 'vCenter / hypervisor', dc_asset: 'Thiết bị DC', opex: 'Opex', ome: 'Dell OME' };
  return `
    <section class="panel"><div class="panel-head"><div><div class="eyebrow">Máy chủ vật lý</div>
      <div class="panel-title" role="heading" aria-level="2">${esc(d.serial)} ${d.status === 'retired' ? uiTag('không còn thấy') : ''}</div>
      <p class="panel-sub">${esc([d.vendor, d.model].filter(Boolean).join(' ') || 'Chưa rõ model')}</p></div></div>
      ${enDl([['Serial / Service Tag', esc(d.serial)], ['Hãng', enVal(d.vendor)], ['Model', enVal(d.model)],
              ['Nguồn ghi nhận', esc(d.origins.map(o => src[o] || o).join(', ') || '—')]])}
    </section>
    ${enOmeHtml(d.ome)}
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Host ảo hoá chạy trên máy (${esc(d.hosts.length)})</div>${hosts}</section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Thiết bị Data Center</div>${assets}</section>
    ${opex}`;
}

function entityDatastoreHtml(d) {
  const c = d.capacity, st = d.storage;
  const vms = d.vms.map(v => `<tr><td>${uiEntityLink('vm', v.name, v.vcenterKey, false)}</td><td class="mono">${esc(v.ip || '—')}</td>
      <td>${v.powerOn ? uiPill('ok', 'Bật') : uiPill('na', 'Tắt')}</td></tr>`).join('');
  return `
    <section class="panel"><div class="panel-head"><div><div class="eyebrow">Datastore · ${esc(d.vcenterKey || '—')}</div>
      <div class="panel-title" role="heading" aria-level="2">${esc(d.name)} ${d.status === 'retired' ? uiTag('không còn thấy') : ''}</div>
      <p class="panel-sub">${esc(d.dsType || '—')} · ${esc(uiNumber(d.vms.length))} VM</p></div>
      <div class="panel-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-nav-link="infra-ds">Datastore</button></div></div>
      ${enDl([['Dung lượng (GB)', esc(uiNumber(c.capacityGB, 1))], ['Còn trống (GB)', esc(uiNumber(c.freeGB, 1))],
              ['Đã dùng', c.usedPct === null ? '—' : `${esc(uiNumber(c.usedPct, 1))}%`],
              ['Truy cập được', c.accessible === null ? '—' : c.accessible ? 'Có' : 'Không'], ['Cluster', esc((c.clusters || []).join(', ') || '—')],
              ['Mảng lưu trữ', st && st.array ? `${enCiLink(st.arrayId, st.array)}${st.vendor ? ` <span class="muted-sub">${esc(st.vendor)}</span>` : ''}` : '—'],
              ['Volume / LUN', st ? `${enCiLink(st.volumeId, st.volume)}${st.confidence === 'weak' ? ' ' + uiTag('ghép theo tên', EN_MATCH[st.matchedBy] || '') : ''}` : '—']])}
    </section>
    <section class="table-card"><div class="panel-title" role="heading" aria-level="2">VM trên datastore (${esc(d.vms.length)})</div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>VM</th><th>IP</th><th>Nguồn</th></tr></thead>
      <tbody>${vms || `<tr><td colspan="3">${uiEmpty('Không có VM.')}</td></tr>`}</tbody></table></div></section>`;
}

const EN_ATTR = { vcenterKey: 'vCenter', cluster: 'Cluster', host: 'Host', platform: 'Nền tảng', vendor: 'Hãng', model: 'Model',
  dsType: 'Loại', kind: 'Kiểu', container: 'SVM / pod', array: 'Mảng', nbuKey: 'NetBackup', category: 'Nhóm', brand: 'Hãng',
  location: 'Vị trí', env: 'Môi trường', code: 'Mã', status: 'Trạng thái', endDate: 'Hết hạn', group: 'Nhóm chi phí' };
const EN_KEY = { serial: 'Serial', hw_uuid: 'UUID phần cứng', instance_uuid: 'instanceUuid', fqdn: 'FQDN', short_name: 'Tên ngắn', ip: 'IP',
  mac: 'MAC', naa: 'NAA', nbu_client: 'Client NetBackup', asset_tag: 'Asset tag', opex_code: 'Mã Opex', idrac_ip: 'iDRAC IP' };

function entityCiHtml(d) {
  const attrs = Object.entries(d.attrs || {}).filter(([k, v]) => EN_ATTR[k] && v !== null && typeof v !== 'object')
    .map(([k, v]) => [EN_ATTR[k], k === 'endDate' ? esc(spDateSafe(v)) : enVal(v)]);
  const keys = (d.keys || []).map(k => [EN_KEY[k.kind] || k.kind, `<span class="mono">${esc(k.value)}</span>`]);
  return `
    <section class="panel"><div class="panel-head"><div><div class="eyebrow">${esc((EN_CI_TYPE[d.type] || d.type).replace(/^./, c => c.toUpperCase()))} · sổ CI</div>
      <div class="panel-title" role="heading" aria-level="2">${esc(d.name)} ${d.status === 'retired' ? uiTag('không còn thấy') : ''}</div>
      <p class="panel-sub">Thấy lần cuối ${esc(spTimeSafe(d.lastSeen))}</p></div></div>
      ${enDl([...attrs, ...keys]) }
    </section>`;
}


/* ---------------------------------------------------------------- command palette (Ctrl + K) */
const palState = { timer: null, seq: 0, items: [], active: 0 };
const PAL_TYPE = { page: 'Trang', action: 'Thao tác', host: 'Host', vm: 'VM', server: 'Máy chủ', datastore: 'Datastore', client: 'Client backup', opex: 'Opex', capex: 'Capex',
  contract: 'Hợp đồng', quote: 'Báo giá', renovation: 'Cải tạo', proposal: 'Tờ trình', vendor: 'NCC' };
const PAL_ACTIONS = [
  { label: 'Tạo khoản Opex', page: 'spend-opex', run: () => uiRequire('spend').then(() => spOpenForm()), role: 'editor', feature: 'spend' },
  { label: 'Tạo gói báo giá', page: 'spend-quotes', run: () => uiRequire('spend').then(() => qtOpenForm()), role: 'editor', feature: 'spend' },
  { label: 'Tạo dự án cải tạo văn phòng', page: 'spend-renovations', run: () => uiRequire('spend').then(() => rnOpenForm()), role: 'editor', feature: 'spend' },
  { label: 'Xem kết quả quy hoạch tài nguyên', page: 'planning', run: () => typeof runPlanningPreview === 'function' && runPlanningPreview() },
  { label: 'Đồng bộ dữ liệu ngay', run: () => { const b = $('#btnSyncNow'); if (b) b.click(); }, admin: true },
];

function palPages(q) {
  const out = [];
  MENU.forEach(g => g.entries.forEach(en => en.tabs.forEach(([id, , tab]) => {
    if (!navItemVisible(id)) return;
    const label = en.tabs.length > 1 ? `${en.label} › ${tab}` : en.label;
    const it = NAV_ITEMS[id];
    if (!q || `${label} ${it.label} ${it.title} ${it.desc || ''}`.toLowerCase().includes(q)) out.push({ type: 'page', label, sub: g.label || 'ICO', page: id });
  })));
  return out;
}

function palActions(q) {
  return PAL_ACTIONS.filter(a => (!a.feature || featureOn(a.feature)) && (!a.role || spendRoleOk(a.role)) && (!a.admin || uiIsAdmin()) && (!a.page || navItemVisible(a.page))
    && (!q || a.label.toLowerCase().includes(q))).map(a => ({ type: 'action', label: a.label, sub: 'Thao tác', action: a }));
}

function openPalette() {
  let dlg = $('#palDialog');
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'palDialog';
    dlg.className = 'src-dialog pal-dialog';
    dlg.setAttribute('aria-label', 'Tìm kiếm');
    dlg.innerHTML = `<input type="text" id="palInput" placeholder="Tìm host, VM, serial, asset tag, datastore, mã OPX / CPX / HD / BG / CTVP / TT, nhà cung cấp, trang…" autocomplete="off" aria-controls="palList">
      <ul class="pal-list" id="palList" role="listbox"></ul><p class="pal-hint">↑ ↓ chọn · Enter mở · Esc đóng</p>`;
    document.body.appendChild(dlg);
    $('#palInput').addEventListener('input', palQuery);
    $('#palInput').addEventListener('keydown', palKeys);
    $('#palList').addEventListener('click', (e) => { const li = e.target.closest('[data-pal]'); if (li) palGo(Number(li.dataset.pal)); });
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  }
  $('#palInput').value = '';
  palShow(palActions('').concat(palPages('')).slice(0, 12));
  dlg.showModal();
  $('#palInput').focus();
}

function palShow(items) {
  palState.items = items; palState.active = 0;
  $('#palList').innerHTML = items.map((it, i) => `<li class="pal-item${i === 0 ? ' active' : ''}" role="option" data-pal="${i}" aria-selected="${i === 0}">
      <span class="pal-type">${esc(PAL_TYPE[it.type] || it.type)}</span><span class="pal-label">${esc(it.label)}</span><span class="pal-sub">${esc(it.sub || '')}</span></li>`).join('')
    || '<li class="pal-empty">Không tìm thấy.</li>';
}

function palQuery() {
  const raw = $('#palInput').value.trim();
  const q = raw.toLowerCase();
  const local = palActions(q).concat(palPages(q));
  palShow(local.slice(0, 20));
  clearTimeout(palState.timer);
  if (q.length < 2) return;
  const seq = ++palState.seq;
  palState.timer = setTimeout(async () => {
    try {
      const r = await apiRequest(`/search?q=${encodeURIComponent(raw)}`);
      if (seq !== palState.seq) return;
      palShow(r.items.concat(local).slice(0, 40));
    } catch (err) { /* keep local results */ }
  }, 250);
}

function palKeys(e) {
  const n = palState.items.length;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    if (!n) return;
    palState.active = (palState.active + (e.key === 'ArrowDown' ? 1 : n - 1)) % n;
    $$('#palList .pal-item').forEach((li, i) => { li.classList.toggle('active', i === palState.active); li.setAttribute('aria-selected', i === palState.active); });
    const cur = $$('#palList .pal-item')[palState.active]; if (cur) cur.scrollIntoView({ block: 'nearest' });
  } else if (e.key === 'Enter') { e.preventDefault(); palGo(palState.active); }
}

function palGo(i) {
  const it = palState.items[i];
  if (!it) return;
  $('#palDialog').close();
  if (it.entity) { location.hash = uiEntityHref(it.entity.type, it.entity.name, it.entity.vcenterKey, it.entity.id); return; }
  if (it.action) {
    if (it.action.page) navigateTo(it.action.page);
    setTimeout(() => it.action.run(), 300);
    return;
  }
  if (it.page && it.query) location.hash = homeLink(it.page, it.query);
  else if (it.page) navigateTo(it.page);
}

document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
    if (!currentUser) return;
    e.preventDefault();
    const dlg = $('#palDialog');
    if (dlg && dlg.open) dlg.close(); else openPalette();
  }
});

/* Dell OME block of the physical server card (G4c): status, iDRAC, warranty, link to Vận hành → Phần cứng. */
function enOmeHtml(o) {
  if (!o) return '';
  const lv = (UI_LEVELS[o.level] || UI_LEVELS.info);
  const w = o.warranty || {};
  const idrac = o.idracUrl ? `<a href="${esc(o.idracUrl)}" target="_blank" rel="noopener noreferrer">${esc(o.idracIp)}</a>` : esc(o.idracIp || '—');
  const dell = w.dellEnd ? `${esc(spDateSafe(w.dellEnd))}${w.daysLeft != null ? ` <span class="muted-sub">${w.daysLeft < 0 ? `hết ${uiNumber(-w.daysLeft)} ngày trước` : `còn ${uiNumber(w.daysLeft)} ngày`}</span>` : ''}` : 'Chưa có dữ liệu';
  const href = `#/hardware?s=${encodeURIComponent(o.sourceKey)}&d=${encodeURIComponent(o.deviceId)}`;
  return `<section class="panel"><div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Dell OME ${uiPill(lv[0], lv[1])}</div>
      <p class="panel-sub">${esc([o.name, o.model, o.powerText].filter(Boolean).join(' · '))}</p></div>
      <div class="panel-toolbar"><a class="btn btn-ghost btn-sm" href="${esc(href)}"><i data-lucide="server-cog"></i>Chi tiết phần cứng</a></div></div>
    ${o.reasons && o.reasons.length ? `<ul class="en-links">${o.reasons.slice(0, 6).map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}
    ${enDl([['iDRAC', idrac], ['Ổ dự báo hỏng', esc(uiNumber(o.disksPredictive || 0))],
            ['Firmware lệch baseline', o.firmware && o.firmware.baselineConfigured === false ? 'Chưa cấu hình baseline' : esc(uiNumber((o.firmware || {}).behind || 0))],
            ['Bảo hành Dell đến', dell], ['Chênh lệch với Opex', w.case ? `${esc(w.caseLabel)} <span class="muted-sub mono">${esc(w.code)}</span>` : 'Không có'],
            ['Hệ điều hành', esc(o.os || '—')]])}</section>`;
}
