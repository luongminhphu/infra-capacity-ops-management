/* =========================================================
   Settings → Kết nối nguồn dữ liệu (admin): vCenter and backup
   servers added from the UI. Secrets are write-only: the API only
   says whether one is saved.
   ========================================================= */

const srcState = { items: [], domainTypes: [], editing: null, kind: 'vcenter' };
const SRC_DEFAULTS = {
  vcenter: { port: 443, user: 'infra.mon@vsphere.local', title: 'vCenter', label: 'vCenter' },
  netbackup: { port: 1556, user: '', title: 'máy chủ backup (NetBackup)', label: 'Backup', keyLabel: 'API key (thay cho user / mật khẩu)' },
  pure: { port: 443, user: '', title: 'mảng Pure FlashArray', label: 'Pure FlashArray', keyLabel: 'API token (user role readonly)' },
  netapp: { port: 443, user: 'ico_ro', title: 'cụm NetApp ONTAP', label: 'NetApp ONTAP' },
};
/* Which credential fields each kind uses: user + password, API key / token, NetBackup-only fields. */
const SRC_FIELDS = { vcenter: ['up'], netbackup: ['up', 'key', 'nbu'], pure: ['key'], netapp: ['up'] };

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
  const test = it.lastTest
    ? `<span class="${it.lastTest.ok ? 'tone-ok' : 'tone-crit'}">${it.lastTest.ok ? 'OK' : 'Lỗi'}</span>
       <span class="muted-sub">${esc(fmtUtc(it.lastTest.at))}</span>`
    : '—';
  const origin = it.origin === 'env'
    ? `Biến môi trường${it.replacedByUi ? '<span class="muted-sub">đã thay bằng cấu hình giao diện — có thể xoá biến</span>' : ''}`
    : `Giao diện<span class="muted-sub">${esc(it.updatedBy || '')}</span>`;
  const toggle = it.origin === 'ui'
    ? `<label class="switch" title="Bật / tắt đồng bộ"><input type="checkbox" role="switch" data-src-toggle="${it.id}" ${it.enabled ? 'checked' : ''} aria-label="Bật ${esc(it.name)}"><span class="slider"></span></label>`
    : `<span class="muted-sub">${it.replacedByUi ? 'Không dùng' : 'Luôn bật'}</span>`;
  const actions = it.origin === 'ui'
    ? `<button class="btn btn-secondary btn-sm" type="button" data-src-edit="${it.id}">Sửa</button>
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
    $('#srcKeyWarning').classList.toggle('hidden', !!r.secretsKeyConfigured);
    $('#btnSrcImportEnv').classList.toggle('hidden', !r.items.some(i => i.origin === 'env' && !i.replacedByUi));
    $('#srcTable tbody').innerHTML = r.items.length ? r.items.map(srcRow).join('')
      : uiEmptyRow(7, 'Chưa có nguồn dữ liệu — bấm “+ vCenter”, “+ Máy chủ backup”, “+ Pure FlashArray” hoặc “+ NetApp ONTAP”.');
  } catch (err) { srcSetStatus(err.message, true); }
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
  $$('#srcDialog .src-up').forEach(el => el.classList.toggle('hidden', !fields.includes('up')));
  $$('#srcDialog .src-key').forEach(el => el.classList.toggle('hidden', !fields.includes('key')));
  if (d.keyLabel) $('#srcApiKeyLabel').textContent = d.keyLabel;
  $('#srcUser').placeholder = kind === 'netapp' ? 'ico_ro (role readonly, ứng dụng http)' : 'infra.mon@vsphere.local';
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
}

function bindSourcesPanel() {
  const panel = $('#sourcesPanel');
  if (!panel || panel.dataset.bound) return;
  panel.dataset.bound = '1';
  panel.addEventListener('click', async e => {
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
      srcSetStatus(`Đã lưu ${r.name}. Bấm “Đồng bộ ngay” để lấy dữ liệu ngay, hoặc chờ chu kỳ kế tiếp.`);
      afterSourcesChanged();
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
