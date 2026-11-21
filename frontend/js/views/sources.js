/* =========================================================
   Settings → Kết nối nguồn dữ liệu (admin): vCenter and backup
   servers added from the UI. Secrets are write-only: the API only
   says whether one is saved.
   ========================================================= */

const srcState = { items: [], domainTypes: [], editing: null, kind: 'vcenter' };
const SRC_DEFAULTS = {
  vcenter: { port: 443, user: 'infra.mon@vsphere.local', title: 'vCenter', label: 'vCenter' },
  proxmox: { port: 8006, user: '', title: 'cụm Proxmox VE', label: 'Proxmox VE', userLabel: 'Token ID (user@realm!token)',
    passLabel: 'Secret của API token', userHint: 'ico@pve!monitor', hostHint: 'pve01.corp.local' },
  nutanix: { port: 9440, user: '', title: 'cụm Nutanix (Prism Element)', label: 'Nutanix AHV', userLabel: 'Tài khoản Viewer (chỉ đọc)',
    userHint: 'ico_ro', hostHint: 'IP ảo của cụm Prism Element' },
  netbackup: { port: 1556, user: '', title: 'máy chủ backup (NetBackup)', label: 'Backup', keyLabel: 'API key (thay cho user / mật khẩu)' },
  pure: { port: 443, user: '', title: 'mảng Pure FlashArray', label: 'Pure FlashArray', keyLabel: 'API token (user role readonly)' },
  netapp: { port: 443, user: 'ico_ro', title: 'cụm NetApp ONTAP', label: 'NetApp ONTAP' },
  gestioip: { port: 443, user: '', title: 'GestióIP IPAM', label: 'GestióIP', userLabel: 'Tài khoản đọc (API)',
    userHint: 'ico_ro', hostHint: 'ipam.corp.local' },
  sdp: { port: 8443, user: '', title: 'ServiceDesk Plus (On-Premises)', label: 'ServiceDesk Plus',
    keyLabel: 'Technician key (tài khoản chỉ xem yêu cầu)', hostHint: 'sdp.corp.local' },
  ome: { port: 443, user: '', title: 'Dell OpenManage Enterprise', label: 'Dell OME', userLabel: 'Tài khoản Viewer (chỉ đọc)',
    userHint: 'ico_ro', hostHint: 'ome01.corp.local' },
};
/* Which credential fields each kind uses: user + password, API key / token, NetBackup-only fields. */
const SRC_FIELDS = { vcenter: ['up'], proxmox: ['up'], nutanix: ['up'], netbackup: ['up', 'key', 'nbu'], pure: ['key'], netapp: ['up'], ome: ['up'], gestioip: ['up', 'ipam'], sdp: ['key', 'sdp'] };

function srcSetStatus(msg, isErr) { uiSetStatus('#srcStatus', msg, isErr ? 'error' : ''); }

function srcAuthLabel(it) {
  if (it.demo) return uiTag('Demo');
  const parts = [];
  if (it.hasApiKey) parts.push('API key');
  if (it.username) parts.push(esc(it.username) + (it.hasPassword ? '' : ' <span class="tone-crit">(thiếu mật khẩu)</span>'));
  if (!parts.length) return '<span class="tone-crit">Thiếu thông tin đăng nhập</span>';
  if (it.secretsReadable === false) parts.push('<span class="tone-crit">không giải mã được — nhập lại</span>');
  return parts.join('<br>');
}

function srcRow(it) {
  const kind = (SRC_DEFAULTS[it.kind] || {}).label || it.kind;
  const test = srcConnHtml(it);
  const origin = it.origin === 'env'
    ? `Biến môi trường${it.replacedByUi ? '<span class="muted-sub">đã thay bằng cấu hình giao diện — có thể xoá biến</span>' : ''}`
    : `Giao diện<span class="muted-sub">${esc(it.updatedBy || '')}</span>`;
  const toggle = it.origin === 'ui'
    ? `<label class="switch" title="Bật / tắt đồng bộ"><input type="checkbox" role="switch" data-src-toggle="${it.id}" ${it.enabled ? 'checked' : ''} aria-label="Bật ${esc(it.name)}"><span class="slider"></span></label>`
    : `<span class="muted-sub">${it.replacedByUi ? 'Không dùng' : 'Luôn bật'}</span>`;
  const actions = it.origin === 'ui'
    ? `${it.enabled ? `<button class="btn btn-ghost btn-sm src-sync-btn" type="button" data-src-sync="${it.id}" title="Đồng bộ ${esc(it.name)} ngay (chỉ đọc)" aria-label="Đồng bộ ${esc(it.name)} ngay"><i data-lucide="refresh-cw"></i></button>` : ''}
       <button class="btn btn-secondary btn-sm" type="button" data-src-edit="${it.id}">Sửa</button>
       <button class="btn btn-secondary btn-sm btn-danger" type="button" data-src-del="${it.id}">Xoá</button>`
    : '';
  return `<tr class="${it.origin === 'env' && it.replacedByUi ? 'muted-row' : ''}">
    <td><b>${esc(it.name)}</b><span class="muted-sub">${kind} · ${esc(it.key)}</span></td>
    <td>${esc(it.host)}:${esc(String(it.port))}${it.verifySsl ? '<span class="muted-sub">Kiểm tra TLS</span>' : ''}</td>
    <td>${srcAuthLabel(it)}</td><td>${origin}</td><td>${test}</td><td>${toggle}</td><td class="src-actions">${actions}</td></tr>`;
}

async function loadSources() {
  try {
    const r = await apiRequest('/admin/sources');
    srcState.items = r.items;
    srcState.domainTypes = r.domainTypes;
    srcKeyBox(r);
    $('#btnSrcImportEnv').classList.toggle('hidden', !r.items.some(i => i.origin === 'env' && !i.replacedByUi));
    $('#srcTable tbody').innerHTML = r.items.length ? r.items.map(srcRow).join('')
      : uiEmptyRow(7, 'Chưa có nguồn dữ liệu — bấm “+ vCenter”, “+ Proxmox VE”, “+ Nutanix”, “+ Máy chủ backup”, “+ Pure FlashArray”, “+ NetApp ONTAP”, “+ Dell OME” hoặc “+ GestióIP”.');
  } catch (err) { srcSetStatus(err.message, true); }
  loadOrphans();
}

/* Encryption key of the stored secrets: missing SECRETS_KEY, secrets still on a previous key, unreadable ones. */
function srcKeyBox(r) {
  const box = $('#srcKeyWarning'), s = r.secrets || { current: 0, previous: 0, unreadable: 0 };
  let html = '';
  if (!r.secretsKeyConfigured) {
    html = `<strong>Chưa đặt SECRETS_KEY.</strong> Mật khẩu / API key đang mã hoá bằng khoá suy ra từ JWT_SECRET.
      Thêm <code>SECRETS_KEY</code> (vd <code>openssl rand -hex 32</code>) vào Secret <code>ico-secrets</code> rồi khởi động lại app:
      mật khẩu đã lưu tự được mã hoá lại bằng khoá mới, không cần nhập lại.`;
  } else if (s.previous) {
    html = `<strong>${fmtInt(s.previous)} mật khẩu / API key còn mã hoá bằng khoá cũ.</strong> App tự mã hoá lại khi khởi động;
      bấm để làm ngay. <button class="btn btn-secondary btn-sm" type="button" id="btnSrcRotate">Mã hoá lại ngay</button>`;
  }
  if (s.unreadable) {
    html += `${html ? '<br>' : ''}<strong>${fmtInt(s.unreadable)} mật khẩu / API key không giải mã được</strong> (lưu bằng khoá không còn trong cấu hình) —
      nhập lại ở nút Sửa của nguồn, kênh thông báo hoặc cấu hình AI tương ứng.`;
  }
  box.innerHTML = html;
  box.classList.toggle('hidden', !html);
  const b = $('#btnSrcRotate');
  if (b) b.onclick = async () => {
    b.disabled = true;
    try { const x = await apiRequest('/admin/sources/rotate-secrets', { method: 'POST' }); srcSetStatus(`Đã mã hoá lại ${fmtInt(x.rotated)} mật khẩu / API key.`); await loadSources(); }
    catch (err) { srcSetStatus(err.message, true); b.disabled = false; }
  };
}

/* ---------- Data of sources no longer configured ---------- */
async function loadOrphans() {
  const mount = $('#srcOrphans');
  if (!mount) return;
  let r;
  try { r = await apiRequest('/admin/sources/orphans'); } catch (err) { mount.innerHTML = uiError(err.message); return; }
  const rows = r.items.map(o => `<tr>
      <td><b>${esc(o.key)}</b><span class="muted-sub">${esc(o.familyLabel)}</span></td>
      <td>${o.lastSeen ? uiDateTime(o.lastSeen, 'short') : '—'}</td>
      <td class="num">${fmtInt(o.rows)}</td>
      <td class="wrap">${esc(o.tables.map(t => `${t.label} ${fmtInt(t.rows)}`).join(' · '))}</td>
      <td><button class="btn btn-ghost btn-sm btn-danger" type="button" data-orphan="${esc(o.family)}|${esc(o.key)}">Xoá dữ liệu</button></td></tr>`).join('');
  mount.innerHTML = `<div class="panel-head src-orphans-head"><div><div class="panel-title" role="heading" aria-level="3">Dữ liệu mồ côi</div>
      <p class="panel-sub">Lịch sử của mã nguồn không còn được cấu hình (đã xoá, đổi mã, dữ liệu DEMO cũ). Xoá nguồn chỉ ngắt kết nối,
        dữ liệu vẫn giữ để nguồn thêm lại cùng mã dùng tiếp. Dữ liệu nhập tay (import) không nằm ở đây.</p></div></div>
    ${r.items.length ? `<div class="table-scroll table-scroll-flat"><table class="data-table">
      <caption class="sr-only">Dữ liệu của nguồn không còn cấu hình</caption>
      <thead><tr><th>Mã nguồn</th><th>Đồng bộ cuối</th><th class="num">Số dòng</th><th>Gồm</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>` : uiNote('Không có dữ liệu mồ côi — mọi lịch sử đều thuộc một nguồn đang cấu hình.')}`;
}

function purgeOrphanDialog(family, key) {
  uiPanelDialog(`Xoá dữ liệu của “${esc(key)}”`, `
    <p class="src-orphan-note">Xoá toàn bộ lịch sử của mã nguồn <b>${esc(key)}</b> (đồng bộ, VM / host, job backup, chuỗi số liệu…). Không hoàn tác được.
      Báo cáo, xu hướng và snapshot license cũ dùng dữ liệu này sẽ không còn số liệu của nguồn đó.</p>
    <label class="field"><span>Gõ lại mã nguồn để xác nhận</span><input id="orphanConfirm" autocomplete="off" placeholder="${esc(key)}"></label>`,
  `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
   <button class="btn btn-secondary btn-sm btn-danger" type="button" id="orphanGo">Xoá dữ liệu</button>`);
  $('#orphanGo').onclick = async () => {
    const btn = $('#orphanGo');
    btn.disabled = true;
    uiSetStatus($('#spDialogMsg'), 'Đang xoá theo từng lô…');
    try {
      const x = await apiRequest('/admin/sources/orphans/purge', { method: 'POST', body: JSON.stringify({ family, key, confirm: $('#orphanConfirm').value.trim() }) });
      $('#spDialog').close();
      srcSetStatus(`Đã xoá ${fmtInt(x.rows)} dòng dữ liệu của ${key}.`);
      loadOrphans();
      if (typeof refreshVcenterList === 'function') refreshVcenterList();
    } catch (err) { uiSetStatus($('#spDialogMsg'), err.message, 'error'); btn.disabled = false; }
  };
}

/* ---------- Dialog ---------- */
function srcFormPayload() {
  const kind = srcState.kind;
  const p = {
    kind, key: $('#srcKey').value.trim(), name: $('#srcName').value.trim(), host: $('#srcHost').value.trim(),
    port: Number($('#srcPort').value) || undefined, username: $('#srcUser').value.trim(),
    password: $('#srcPassword').value, verifySsl: $('#srcVerify').checked, caPem: $('#srcCaPem').value,
    demo: $('#srcDemo').checked,
  };
  if (kind === 'pure') p.apiKey = $('#srcApiKey').value;
  if (kind === 'sdp') {
    Object.assign(p, { apiKey: $('#srcApiKey').value, templates: $('#srcSdpTemplates').value, backfillFrom: $('#srcSdpFrom').value,
      ratePerMin: Number($('#srcSdpRate').value) || undefined, intervalMinutes: Number($('#srcSdpEvery').value) || undefined });
  }
  if (kind === 'gestioip') {
    Object.assign(p, { clientName: $('#srcIpamClient').value.trim() || 'DEFAULT', apiPath: $('#srcIpamPath').value.trim(),
      serialColumn: $('#srcIpamSerial').value.trim(), macColumn: $('#srcIpamMac').value.trim() });
  }
  if (kind === 'netbackup') {
    Object.assign(p, { apiKey: $('#srcApiKey').value, apiVersion: $('#srcApiVersion').value.trim() || '8.0',
      domainType: $('#srcDomainType').value, domainName: $('#srcDomainName').value.trim() });
  }
  if (srcState.editing) { p.id = srcState.editing.id; p.enabled = srcState.editing.enabled; }
  return p;
}

function srcSetResult(msg, ok) {
  const el = $('#srcResult');
  el.textContent = msg || '';
  el.className = 'src-result' + (msg ? (ok ? ' ok' : ' err') : '');
}

function openSourceDialog(kind, item) {
  srcState.kind = kind;
  srcState.editing = item || null;
  const d = SRC_DEFAULTS[kind];
  $('#srcDialogTitle').textContent = (item ? 'Sửa ' : 'Thêm ') + d.title;
  const fields = SRC_FIELDS[kind] || ['up'];
  $$('#srcDialog .src-nbu').forEach(el => el.classList.toggle('hidden', !fields.includes('nbu')));
  $$('#srcDialog .src-ipam').forEach(el => el.classList.toggle('hidden', !fields.includes('ipam')));
  $$('#srcDialog .src-sdp').forEach(el => el.classList.toggle('hidden', !fields.includes('sdp')));
  $$('#srcDialog .src-up').forEach(el => el.classList.toggle('hidden', !fields.includes('up')));
  $$('#srcDialog .src-key').forEach(el => el.classList.toggle('hidden', !fields.includes('key')));
  if (d.keyLabel) $('#srcApiKeyLabel').textContent = d.keyLabel;
  $('#srcUser').placeholder = d.userHint || (kind === 'netapp' ? 'ico_ro (role readonly, ứng dụng http)' : 'infra.mon@vsphere.local');
  $('label[for="srcUser"]').textContent = d.userLabel || 'Tài khoản chỉ đọc';
  $('label[for="srcPassword"]').textContent = d.passLabel || 'Mật khẩu';
  $('#srcHost').placeholder = d.hostHint || 'vcenter01.corp.local';
  $('#srcDomainType').innerHTML = srcState.domainTypes.map(t => `<option value="${esc(t)}">${esc(t)}</option>`).join('');
  const v = item || {};
  $('#srcName').value = v.name || '';
  $('#srcKey').value = v.key || '';
  $('#srcKey').disabled = !!item;
  $('#srcHost').value = v.host || '';
  $('#srcPort').value = v.port || d.port;
  $('#srcUser').value = item ? (v.username || '') : d.user;
  $('#srcPassword').value = '';
  $('#srcPassword').placeholder = v.hasPassword ? 'Đã lưu — để trống để giữ nguyên' : '';
  $('#srcApiKey').value = '';
  $('#srcApiKey').placeholder = v.hasApiKey ? 'Đã lưu — để trống để giữ nguyên' : '';
  $('#srcApiVersion').value = v.apiVersion || '';
  $('#srcDomainType').value = v.domainType || 'unixpwd';
  $('#srcDomainName').value = v.domainName || '';
  $('#srcIpamClient').value = v.clientName || '';
  $('#srcIpamPath').value = v.apiPath || '';
  $('#srcIpamSerial').value = v.serialColumn || '';
  $('#srcIpamMac').value = v.macColumn || '';
  $('#srcSdpTemplates').value = (v.templates || []).join(', ');
  $('#srcSdpFrom').value = v.backfillFrom || '2025-10-01';
  $('#srcSdpRate').value = v.ratePerMin || '';
  $('#srcSdpEvery').value = v.intervalMinutes || '';
  $('#srcVerify').checked = !!v.verifySsl;
  $('#srcCaPem').value = v.caPem || '';
  $('.src-ca').classList.toggle('hidden', !v.verifySsl);
  $('#srcDemo').checked = !!v.demo;
  srcSetResult('');
  $('#srcDialog').showModal();
  refreshIcons();
  $('#srcName').focus();
}

function closeSourceDialog() {
  $('#srcPassword').value = '';
  $('#srcApiKey').value = '';
  $('#srcDialog').close();
}

async function afterSourcesChanged() {
  await loadSources();
  if (typeof refreshVcenterList === 'function') refreshVcenterList();
  if (typeof bkInvalidate === 'function') bkInvalidate();
  if (typeof refreshSourceStatus === 'function') refreshSourceStatus();
}

function bindSourcesPanel() {
  const panel = $('#sourcesPanel');
  if (!panel || panel.dataset.bound) return;
  panel.dataset.bound = '1';
  panel.addEventListener('click', async e => {
    const orphan = e.target.closest('[data-orphan]');
    if (orphan) { const [family, ...k] = orphan.dataset.orphan.split('|'); purgeOrphanDialog(family, k.join('|')); return; }
    const sync = e.target.closest('[data-src-sync]');
    if (sync) { srcSyncOne(sync); return; }
    const add = e.target.closest('[data-src-add]');
    if (add) { openSourceDialog(add.dataset.srcAdd); return; }
    const edit = e.target.closest('[data-src-edit]');
    if (edit) {
      const it = srcState.items.find(i => i.id === Number(edit.dataset.srcEdit));
      if (it) openSourceDialog(it.kind, it);
      return;
    }
    const del = e.target.closest('[data-src-del]');
    if (del) {
      const it = srcState.items.find(i => i.id === Number(del.dataset.srcDel));
      if (!it || !await uiConfirm(`Xoá kết nối "${it.name}"? Dữ liệu lịch sử đã thu thập vẫn được giữ.`)) return;
      try { await apiRequest(`/admin/sources/${it.id}`, { method: 'DELETE' }); srcSetStatus(`Đã xoá ${it.name}.`); afterSourcesChanged(); }
      catch (err) { srcSetStatus(err.message, true); }
    }
  });
  panel.addEventListener('change', async e => {
    const t = e.target.closest('[data-src-toggle]');
    if (!t) return;
    const it = srcState.items.find(i => i.id === Number(t.dataset.srcToggle));
    t.disabled = true;
    try {
      await apiRequest(`/admin/sources/${it.id}`, { method: 'PUT', body: JSON.stringify({ ...it, enabled: t.checked }) });
      srcSetStatus(`${it.name}: ${t.checked ? 'đã bật' : 'đã tắt'} đồng bộ.`);
      afterSourcesChanged();
    } catch (err) { t.checked = !t.checked; srcSetStatus(err.message, true); }
    finally { t.disabled = false; }
  });
  $('#btnSrcImportEnv').addEventListener('click', async () => {
    if (!await uiConfirm('Sao chép mọi vCenter / NetBackup đang khai báo bằng biến môi trường (kèm mật khẩu, API key) vào giao diện? Sau đó có thể xoá các biến VCENTER_i_* / NETBACKUP_i_* khỏi ConfigMap / Secret.')) return;
    try {
      const r = await apiRequest('/admin/sources/import-env', { method: 'POST' });
      srcSetStatus(r.added.length ? `Đã chuyển ${r.added.length} nguồn: ${r.added.join(', ')}.` : 'Không có nguồn mới để chuyển.');
      afterSourcesChanged();
    } catch (err) { srcSetStatus(err.message, true); }
  });

  const dlg = $('#srcDialog');
  dlg.addEventListener('click', e => { if (e.target.closest('[data-src-close]')) closeSourceDialog(); });
  dlg.addEventListener('close', () => { $('#srcPassword').value = ''; $('#srcApiKey').value = ''; });
  $('#srcVerify').addEventListener('change', e => $('.src-ca').classList.toggle('hidden', !e.target.checked));
  $('#btnSrcTest').addEventListener('click', async () => {
    const btn = $('#btnSrcTest');
    btn.disabled = true;
    srcSetResult('Đang kiểm tra kết nối…', true);
    $('#srcResult').className = 'src-result';
    try {
      const r = await apiRequest('/admin/sources/test', { method: 'POST', body: JSON.stringify(srcFormPayload()) });
      srcSetResult(r.message, r.ok);
      if (srcState.editing) loadSources();
    } catch (err) { srcSetResult(err.message, false); }
    finally { btn.disabled = false; }
  });
  $('#srcForm').addEventListener('submit', async e => {
    e.preventDefault();
    const btn = $('#btnSrcSave');
    btn.disabled = true;
    try {
      const p = srcFormPayload();
      const r = srcState.editing
        ? await apiRequest(`/admin/sources/${srcState.editing.id}`, { method: 'PUT', body: JSON.stringify(p) })
        : await apiRequest('/admin/sources', { method: 'POST', body: JSON.stringify(p) });
      closeSourceDialog();
      await afterSourcesChanged();
      if (r.enabled && r.id) {                // read it at once: no waiting for the next scheduler round
        const b = document.querySelector(`[data-src-sync="${r.id}"]`);
        if (b) { srcSetStatus(`Đã lưu ${r.name}. Đang đọc nguồn lần đầu…`); await srcSyncOne(b); }
      } else {
        srcSetStatus(`Đã lưu ${r.name}.`);
      }
    } catch (err) { srcSetResult(err.message, false); }
    finally { btn.disabled = false; }
  });
}

function initSourcesSettings(isAdmin) {
  const panel = $('#sourcesPanel');
  if (!panel) return;
  panel.classList.toggle('hidden', !isAdmin);
  if (!isAdmin) return;
  bindSourcesPanel();
  loadSources();
}

/* Connection state of a source: its last real sync (scheduler round or Đồng bộ) + the last manual connection test. */
function srcConnHtml(it) {
  const parts = [];
  const s = it.sync;
  if (s) {
    parts.push(uiPill(s.level, s.stateText));
    parts.push(`<span class="muted-sub">${s.lastSyncAt ? `Đồng bộ ${esc(uiDateTime(s.lastSyncAt, 'short'))}` : 'Chưa đồng bộ lần nào — bấm Đồng bộ'}</span>`);
    if (s.error) parts.push(`<span class="src-status-error">${esc(s.error)}</span>`);
  } else {
    parts.push(uiTag(it.enabled === false || it.replacedByUi ? 'Không đồng bộ' : 'Chưa nạp'));
  }
  if (it.lastTest) {
    parts.push(`<span class="muted-sub">Kiểm tra tay: <span class="${it.lastTest.ok ? 'tone-ok' : 'tone-crit'}">${it.lastTest.ok ? 'OK' : 'lỗi'}</span> ${esc(fmtUtc(it.lastTest.at))}</span>`);
  }
  return `<div class="src-conn">${parts.join('')}</div>`;
}

async function srcSyncOne(btn) {
  const it = srcState.items.find(i => i.id === Number(btn.dataset.srcSync));
  if (!it) return;
  btn.disabled = true;
  srcSetStatus(`Đang đọc ${it.name}…`);
  try {
    const r = await apiRequest(`/admin/sources/${it.id}/sync`, { method: 'POST' });
    srcSetStatus(r.ok ? `${it.name}: đồng bộ thành công.` : `${it.name}: ${r.error || 'đồng bộ thất bại'}.`, !r.ok);
    await loadSources();
    if (typeof refreshSourceStatus === 'function') refreshSourceStatus();
  } catch (err) { srcSetStatus(err.message, true); btn.disabled = false; }
}
