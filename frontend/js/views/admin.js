/* =========================================================
   Settings → Tính năng (module switches), logo màn hình đăng nhập,
   AI / MCP server (admin only). Also the login-screen branding.
   ========================================================= */

/* ---------- Login screen branding (no sign-in needed) ---------- */
async function loadLoginBranding() {
  try {
    const b = await apiRequest('/public/branding');
    setLoginBrandLogo(b && b.loginLogo);
  } catch (err) { /* keep default mark */ }
}

function setLoginBrandLogo(dataUrl) {
  const img = $('#loginBrandLogo');
  const mark = $('#loginBrandMark');
  if (!img || !mark) return;
  if (dataUrl) { img.src = dataUrl; img.classList.remove('hidden'); mark.classList.add('hidden'); }
  else { img.removeAttribute('src'); img.classList.add('hidden'); mark.classList.remove('hidden'); }
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(new Error('Không đọc được tệp ảnh.'));
    r.readAsDataURL(file);
  });
}

const fmtUtc = (iso) => iso ? iso.replace('T', ' ').replace(/:\d\dZ$/, '') : '—';

/* ---------- Feature switches ---------- */
function renderFeatureSwitches(catalog, isAdmin) {
  const list = $('#featureList');
  if (!list) return;
  list.innerHTML = catalog.map(f => `
    <div class="feature-row">
      <div><div class="feature-name" id="feat-lbl-${esc(f.key)}">${esc(f.label)}</div><div class="feature-desc">${esc(f.desc)}</div></div>
      <label class="switch" title="${isAdmin ? '' : 'Chỉ quản trị viên được thay đổi'}">
        <input type="checkbox" role="switch" data-feature="${esc(f.key)}" aria-labelledby="feat-lbl-${esc(f.key)}"
          ${f.enabled ? 'checked' : ''} ${isAdmin ? '' : 'disabled'}>
        <span class="slider"></span>
      </label>
    </div>`).join('');
}

async function loadFeatureSettings(isAdmin) {
  try {
    const r = await apiRequest('/settings/features');
    renderFeatureSwitches(r.catalog, isAdmin);
    if (!isAdmin) $('#featureStatus').textContent = 'Chỉ quản trị viên được bật / tắt tính năng.';
  } catch (err) { $('#featureStatus').textContent = err.message; }
}

function bindFeatureSwitches() {
  const list = $('#featureList');
  if (!list || list.dataset.bound) return;
  list.dataset.bound = '1';
  list.addEventListener('change', async e => {
    const input = e.target.closest('[data-feature]');
    if (!input) return;
    const status = $('#featureStatus');
    input.disabled = true;
    try {
      const r = await apiRequest('/settings/features', { method: 'PUT', body: JSON.stringify({ features: { [input.dataset.feature]: input.checked } }) });
      applyFeatures(r.features);
      const f = r.catalog.find(x => x.key === input.dataset.feature);
      status.textContent = `${f.label}: ${f.enabled ? 'đã bật' : 'đã ẩn'} cho mọi người dùng.`;
    } catch (err) {
      input.checked = !input.checked;
      status.textContent = err.message;
    } finally { input.disabled = false; }
  });
}

/* ---------- Login-screen logo ---------- */
function showLoginLogoPreview(dataUrl) {
  const img = $('#loginLogoImg');
  if (dataUrl) { img.src = dataUrl; img.classList.remove('hidden'); $('#loginLogoEmpty').classList.add('hidden'); }
  else { img.classList.add('hidden'); img.removeAttribute('src'); $('#loginLogoEmpty').classList.remove('hidden'); }
  setLoginBrandLogo(dataUrl);
}

async function loadLoginLogoSettings(isAdmin) {
  $$('#loginLogoPanel .admin-only').forEach(el => el.classList.toggle('hidden', !isAdmin));
  try { showLoginLogoPreview((await apiRequest('/settings/login-logo')).dataUrl); } catch (err) { /* ignore */ }
  const input = $('#loginLogoInput');
  if (!input || input.dataset.bound) return;
  input.dataset.bound = '1';
  const status = $('#loginLogoStatus');
  input.addEventListener('change', async () => {
    const file = input.files && input.files[0];
    input.value = '';
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) { status.textContent = 'Ảnh vượt quá 2MB.'; return; }
    status.textContent = 'Đang tải lên…';
    try {
      const dataUrl = await readFileAsDataUrl(file);
      const r = await apiRequest('/settings/login-logo', { method: 'PUT', body: JSON.stringify({ data_url: dataUrl, filename: file.name }) });
      showLoginLogoPreview(r.dataUrl);
      status.textContent = `Đã cập nhật logo màn hình đăng nhập (${file.name}).`;
    } catch (err) { status.textContent = err.message; }
  });
  $('#btnRemoveLoginLogo').addEventListener('click', async () => {
    try {
      await apiRequest('/settings/login-logo', { method: 'DELETE' });
      showLoginLogoPreview(null);
      status.textContent = 'Đã xoá logo — màn hình đăng nhập dùng biểu tượng mặc định.';
    } catch (err) { status.textContent = err.message; }
  });
}

/* ---------- AI / MCP server ---------- */
const mcpDiff = (b = {}, a = {}) => Object.keys(a).filter(k => JSON.stringify(a[k]) !== JSON.stringify(b[k]))
  .map(k => `${MCP_FIELD_LABEL[k] || k}: ${JSON.stringify(b[k])} → ${JSON.stringify(a[k])}`).join('; ') || 'không đổi';

async function loadMcpPanel() {
  let info;
  try { info = await apiRequest('/admin/mcp'); } catch (err) { $('#mcpState').textContent = err.message; return; }
  mcpFillConfig(info);
  $('#mcpState').innerHTML = info.enabled
    ? `MCP đang <b>bật</b>. Token tối đa ${esc(info.maxDays)} ngày; ${info.maskHosts ? 'host / IP trong log được che.' : '<b>host / IP KHÔNG được che</b>.'}`
    : 'MCP đang <b>tắt</b> — bật bằng công tắc phía trên rồi bấm Lưu cấu hình (không cần redeploy).';
  $('#mcpCreate').classList.toggle('hidden', !info.enabled);
  const tbody = $('#mcpTokenTable tbody');
  tbody.innerHTML = info.tokens.length ? info.tokens.map(t => `
    <tr>
      <td>${esc(t.name)}<div class="feature-desc"><code>${esc(t.prefix)}…</code></div></td>
      <td>${esc(t.owner)}</td><td>${fmtUtc(t.expiresAt)}</td><td>${fmtUtc(t.lastUsedAt)}</td>
      <td>${t.status === 'active' ? 'Đang dùng' : t.status === 'expired' ? 'Hết hạn' : 'Đã thu hồi'}</td>
      <td>${t.status === 'active' ? `<button type="button" class="btn btn-secondary btn-sm btn-danger" data-revoke="${t.id}">Thu hồi</button>` : ''}</td>
    </tr>`).join('') : '<tr><td colspan="6">Chưa có token.</td></tr>';
  try {
    const audit = await apiRequest('/admin/mcp/audit?limit=50');
    $('#mcpAuditTable tbody').innerHTML = audit.length ? audit.map(a => `
      <tr><td>${fmtUtc(a.at)}</td><td>${esc(a.user || '')}</td><td>${esc(a.action)}</td>
      <td><code>${esc(a.detail.tool || a.detail.name || a.detail.prefix || (a.detail.after ? mcpDiff(a.detail.before, a.detail.after) : ''))}</code></td></tr>`).join('')
      : '<tr><td colspan="4">Chưa có truy cập.</td></tr>';
  } catch (err) { /* ignore */ }
}

/* Settings → AI / MCP server: values saved here win over ConfigMap ico-config; every pod reads them within 15 s. */
const MCP_FIELD_LABEL = { enabled: 'Bật MCP', maskHosts: 'Che host / IP', tokenMaxDays: 'Hạn token', ratePerMinute: 'Giới hạn / phút', allowedOrigins: 'Origin' };
let mcpLoaded = null;
function mcpFillConfig(info) {
  mcpLoaded = info;
  const c = info.config || {}, d = info.defaults || {}, saved = new Set(info.saved || []);
  $('#mcpUrl').textContent = `${location.origin}/mcp`;
  $('#mcpCfgEnabled').checked = !!c.enabled;
  $('#mcpCfgMask').checked = !!c.maskHosts;
  $('#mcpCfgDays').value = c.tokenMaxDays ?? '';
  $('#mcpCfgRate').value = c.ratePerMinute ?? '';
  $('#mcpCfgOrigins').value = (c.allowedOrigins || []).join(', ');
  const show = (k, v) => Array.isArray(v) ? (v.join(', ') || 'trống') : typeof v === 'boolean' ? (v ? 'bật' : 'tắt') : String(v);
  $$('[data-mcp-def]').forEach(el => {
    const k = el.dataset.mcpDef;
    el.textContent = `ConfigMap: ${show(k, d[k])}${saved.has(k) ? ' · đang dùng giá trị đã lưu' : ''}`;
  });
  $('#mcpCfgSource').textContent = saved.size
    ? `Đã lưu trên giao diện: ${[...saved].map(k => MCP_FIELD_LABEL[k] || k).join(', ')}. Còn lại theo ConfigMap ico-config.`
    : 'Đang theo ConfigMap ico-config (chưa lưu giá trị nào trên giao diện).';
  $('#btnMcpCfgReset').disabled = !saved.size;
}

async function mcpSaveConfig() {
  const form = { enabled: $('#mcpCfgEnabled').checked, maskHosts: $('#mcpCfgMask').checked,
    tokenMaxDays: $('#mcpCfgDays').value.trim(), ratePerMinute: $('#mcpCfgRate').value.trim(), allowedOrigins: $('#mcpCfgOrigins').value };
  // Only what the admin changed: an untouched field keeps following the ConfigMap.
  const was = (mcpLoaded && mcpLoaded.config) || {};
  const norm = (k, v) => k === 'allowedOrigins' ? String(Array.isArray(v) ? v.join(',') : v).split(/[\s,]+/).filter(Boolean).map(o => o.replace(/\/$/, '')).join(',') : String(v);
  const body = Object.fromEntries(Object.entries(form).filter(([k, v]) => norm(k, v) !== norm(k, was[k] ?? '')));
  const msg = $('#mcpCfgMsg');
  if (!Object.keys(body).length) { uiSetStatus(msg, 'Không có thay đổi để lưu.', 'ok'); return; }
  const send = () => apiRequest('/admin/mcp/config', { method: 'PUT', body: JSON.stringify(body) });
  try {
    try { await send(); }
    catch (err) {
      if (!/xác nhận/.test(err.message)) throw err;
      if (!await uiConfirm('Tắt che host / IP: tên máy chủ và IP nội bộ trong log sẽ được gửi cho AI client. Tiếp tục?')) return;
      body.confirmMaskOff = true;
      await send();
    }
    uiSetStatus(msg, 'Đã lưu — các pod áp dụng trong khoảng 15 giây.', 'ok');
    loadMcpPanel();
  } catch (err) { uiSetStatus(msg, err.message, 'error'); }
}

function bindMcpPanel() {
  const panel = $('#mcpPanel');
  if (panel.dataset.bound) return;
  panel.dataset.bound = '1';
  $('#btnMcpCfgSave').addEventListener('click', mcpSaveConfig);
  $('#btnMcpCfgReset').addEventListener('click', async () => {
    if (!await uiConfirm('Bỏ các giá trị đã lưu và dùng lại cấu hình trong ConfigMap ico-config?')) return;
    try { await apiRequest('/admin/mcp/config', { method: 'DELETE' }); uiSetStatus($('#mcpCfgMsg'), 'Đã về mặc định ConfigMap.', 'ok'); loadMcpPanel(); }
    catch (err) { uiSetStatus($('#mcpCfgMsg'), err.message, 'error'); }
  });
  $('#btnCreateMcpToken').addEventListener('click', async () => {
    const name = $('#mcpTokenName').value.trim() || 'claude-code';
    try {
      const r = await apiRequest('/admin/mcp/tokens', { method: 'POST', body: JSON.stringify({ name, days: Number($('#mcpTokenDays').value) }) });
      $('#mcpNewTokenValue').textContent = r.token;
      $('#mcpCommand').textContent = `claude mcp add --transport http ico ${location.origin}/mcp --header "Authorization: Bearer ${r.token}"`;
      $('#mcpNewToken').classList.remove('hidden');
      $('#mcpTokenName').value = '';
      loadMcpPanel();
    } catch (err) { $('#mcpState').textContent = err.message; }
  });
  $('#btnCopyMcpCmd').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('#mcpCommand').textContent); $('#btnCopyMcpCmd').textContent = 'Đã sao chép'; }
    catch (err) { /* clipboard blocked: user copies manually */ }
  });
  panel.addEventListener('click', async e => {
    const b = e.target.closest('[data-revoke]');
    if (!b || !await uiConfirm('Thu hồi token này? AI client đang dùng token sẽ mất quyền truy cập ngay.')) return;
    try { await apiRequest(`/admin/mcp/tokens/${b.dataset.revoke}`, { method: 'DELETE' }); loadMcpPanel(); }
    catch (err) { $('#mcpState').textContent = err.message; }
  });
}

function initAdminSettings(user) {
  const isAdmin = !!(user && user.isAdmin);
  bindFeatureSwitches();
  loadFeatureSettings(isAdmin);
  loadLoginLogoSettings(isAdmin);
  $('#mcpPanel').classList.toggle('hidden', !isAdmin);
  $('#mcpNewToken').classList.add('hidden');
  if (isAdmin) { bindMcpPanel(); loadMcpPanel(); }
  if (typeof initSourcesSettings === 'function') initSourcesSettings(isAdmin);
  // Cài đặt → Chi phí & nhắc hạn: built when its tab opens (routes.js VIEWS.settings.tabRender.spend).
}
