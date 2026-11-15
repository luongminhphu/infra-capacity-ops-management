/* =========================================================
   Passkey (WebAuthn) cho tài khoản cục bộ: đăng nhập bằng Face ID / Touch ID trên iPhone,
   link mời tạo passkey, "Passkey của tôi", và Cài đặt → Đăng nhập & bảo mật (admin).
   ========================================================= */

/* ---------- base64url ⇄ ArrayBuffer (browsers before parseCreationOptionsFromJSON) ---------- */
function pkB64ToBuf(s) {
  const b = atob(String(s).replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (String(s).length % 4)) % 4));
  return Uint8Array.from(b, c => c.charCodeAt(0)).buffer;
}
function pkBufToB64(buf) {
  let s = '';
  new Uint8Array(buf).forEach(b => { s += String.fromCharCode(b); });
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function pkSupported() { return !!(window.PublicKeyCredential && navigator.credentials); }

async function pkCreate(options) {
  const o = { ...options, challenge: pkB64ToBuf(options.challenge), user: { ...options.user, id: pkB64ToBuf(options.user.id) },
    excludeCredentials: (options.excludeCredentials || []).map(c => ({ ...c, id: pkB64ToBuf(c.id) })) };
  const cred = await navigator.credentials.create({ publicKey: o });
  return { id: cred.id, rawId: pkBufToB64(cred.rawId), type: cred.type,
    response: { clientDataJSON: pkBufToB64(cred.response.clientDataJSON), attestationObject: pkBufToB64(cred.response.attestationObject),
      transports: typeof cred.response.getTransports === 'function' ? cred.response.getTransports() : [] },
    clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {} };
}

async function pkGet(options) {
  const o = { ...options, challenge: pkB64ToBuf(options.challenge), allowCredentials: (options.allowCredentials || []).map(c => ({ ...c, id: pkB64ToBuf(c.id) })) };
  const cred = await navigator.credentials.get({ publicKey: o });
  return { id: cred.id, rawId: pkBufToB64(cred.rawId), type: cred.type,
    response: { clientDataJSON: pkBufToB64(cred.response.clientDataJSON), authenticatorData: pkBufToB64(cred.response.authenticatorData),
      signature: pkBufToB64(cred.response.signature), userHandle: cred.response.userHandle ? pkBufToB64(cred.response.userHandle) : null },
    clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {} };
}

function pkError(err) {
  if (err && err.name === 'NotAllowedError') return 'Đã huỷ hoặc hết thời gian xác nhận Face ID / Touch ID.';
  if (err && err.name === 'InvalidStateError') return 'Thiết bị này đã có passkey cho tài khoản.';
  if (err && err.name === 'SecurityError') return 'Trình duyệt chặn passkey trên địa chỉ này — mở đúng URL HTTPS của ứng dụng.';
  return (err && err.message) || 'Không dùng được passkey.';
}

/* ---------- Login screen ---------- */
function setupPasskeyLogin(cfg, params) {
  const btn = $('#btnLoginPasskey');
  if (!btn) return;
  if (cfg.passkey && pkSupported()) {
    btn.classList.remove('hidden');
    $('#loginPasskeyHint').classList.remove('hidden');
    btn.onclick = loginWithPasskey;
  }
  if (cfg.local && cfg.password === false) {               // passkey-only: password form only for break-glass accounts
    const alt = $('#btnShowLocalLogin');
    alt.classList.remove('hidden');
    alt.onclick = () => { $('#localLoginFields').classList.remove('hidden'); alt.classList.add('hidden'); $('#loginDivider').classList.remove('hidden');
      $('#loginUsername').required = true; $('#loginPassword').required = true; $('#loginUsername').focus(); };
  }
  if (window.pkInviteToken) pkShowInvite(window.pkInviteToken);
}

async function loginWithPasskey() {
  const btn = $('#btnLoginPasskey');
  $('#loginError').textContent = '';
  btn.disabled = true;
  try {
    const options = await apiRequest('/auth/passkey/login/options', { method: 'POST' });
    const credential = await pkGet(options);
    await finishLogin(await apiRequest('/auth/passkey/login/verify', { method: 'POST', body: JSON.stringify({ credential }) }));
  } catch (err) { $('#loginError').textContent = pkError(err); }
  finally { btn.disabled = false; }
}

async function pkShowInvite(token) {
  const box = $('#passkeyInvite');
  try {
    const info = await apiRequest('/auth/passkey/invite', { method: 'POST', body: JSON.stringify({ token }) });
    $('#passkeyInviteText').textContent = `Tạo passkey cho tài khoản ${info.displayName || info.username} (${info.username}).`;
    box.classList.remove('hidden');
    // Only the invite button while registering; the normal choices come back afterwards (e.g. after sign-out).
    const hidden = ['#btnLoginEntra', '#loginEntraHint', '#btnLoginPasskey', '#loginPasskeyHint', '#localLoginFields', '#loginDivider', '#btnShowLocalLogin']
      .map(sel => $(sel)).filter(el => el && !el.classList.contains('hidden'));
    hidden.forEach(el => el.classList.add('hidden'));
    box.restore = () => hidden.forEach(el => el.classList.remove('hidden'));
    if (!pkSupported()) { $('#loginError').textContent = 'Trình duyệt này không hỗ trợ passkey — mở link trên Safari (iPhone) hoặc Chrome / Edge mới.'; return; }
    $('#btnInviteRegister').onclick = async (e) => {
      const b = e.currentTarget; b.disabled = true; $('#loginError').textContent = '';
      try {
        const options = await apiRequest('/auth/passkey/register/options', { method: 'POST', body: JSON.stringify({ invite: token }) });
        const credential = await pkCreate(options);
        await apiRequest('/auth/passkey/register/verify', { method: 'POST', body: JSON.stringify({ invite: token, credential }) });
        window.pkInviteToken = null;
        box.classList.add('hidden');
        box.restore();
        await finishLogin(await apiRequest('/auth/me'));
      } catch (err) { $('#loginError').textContent = pkError(err); b.disabled = false; }
    };
  } catch (err) { $('#loginError').textContent = err.message; }
}

/* ---------- Step-up: Face ID (or password) again before changing passkeys ---------- */
async function pkReauth(hasPasskeys) {
  if (hasPasskeys) {
    const options = await apiRequest('/auth/passkey/login/options', { method: 'POST' });
    const credential = await pkGet(options);
    await apiRequest('/auth/passkey/reauth/verify', { method: 'POST', body: JSON.stringify({ credential }) });
    return;
  }
  const pw = $('#pkPw');
  if (!pw || !pw.value) throw new Error('Nhập mật khẩu hiện tại để xác nhận, rồi bấm lại.');
  await apiRequest('/auth/reauth', { method: 'POST', body: JSON.stringify({ password: pw.value }) });
}

async function pkWithReauth(fn, hasPasskeys) {
  try { return await fn(); }
  catch (err) {
    if (err.status !== 428) throw err;
    await pkReauth(hasPasskeys);
    return fn();
  }
}

/* ---------- Passkey của tôi (every local user) ---------- */
async function pkMyDialog() {
  let d;
  try { d = await apiRequest('/auth/passkeys'); } catch (err) { uiConfirm(err.message, { okLabel: 'Đóng', danger: false }); return; }
  const rows = d.items.map(p => `<tr><td><b>${esc(p.name)}</b>${p.synced ? ' <span class="pill-mini">Đồng bộ</span>' : ''}</td>
      <td>${esc(spTimeSafe(p.createdAt))}</td><td>${esc(p.lastUsedAt ? spTimeSafe(p.lastUsedAt) : '—')}</td>
      <td><button class="btn btn-ghost btn-sm btn-danger" type="button" data-pk-del="${esc(p.id)}" aria-label="Xoá ${esc(p.name)}"><i data-lucide="trash-2"></i></button></td></tr>`).join('');
  const ui = uiDialog('pkMyDlg', { title: 'Passkey của tôi' });
  const dlg = ui.el;
  ui.body.innerHTML = `
    ${!d.local ? '<p class="muted-sub">Tài khoản Microsoft: quản lý passkey / phương thức đăng nhập ở trang bảo mật của Microsoft (mysignins.microsoft.com).</p>'
      : !d.enabled ? '<p class="muted-sub">Passkey cho tài khoản cục bộ đang tắt.</p>' : `
    <p class="muted-sub">Đăng nhập bằng Face ID / Touch ID thay cho mật khẩu. Nên có ít nhất 2 passkey (vd iPhone và Mac) phòng khi mất máy.
      Thêm / xoá passkey cần xác nhận lại danh tính nếu bạn đăng nhập đã quá 10 phút. Xoá một passkey sẽ đăng xuất các phiên khác của tài khoản.</p>
    <div class="table-scroll"><table class="data-table"><thead><tr><th>Passkey</th><th>Tạo lúc</th><th>Dùng gần nhất</th><th></th></tr></thead>
      <tbody>${rows || `<tr><td colspan="4">${uiEmpty('Chưa có passkey.')}</td></tr>`}</tbody></table></div>
    ${!d.items.length && d.hasPassword ? '<label class="field"><span>Mật khẩu hiện tại (xác nhận trước khi thêm passkey)</span><input type="password" id="pkPw" autocomplete="current-password"></label>' : ''}
    <div class="sim-actions"><input id="pkName" maxlength="80" placeholder="Tên (vd iPhone 16 của Phú)" aria-label="Tên passkey">
      <button class="btn btn-primary btn-sm" type="button" id="pkAdd"><i data-lucide="fingerprint"></i>Thêm passkey trên thiết bị này</button></div>`}
    <p class="criteria-status" id="pkMsg" role="status"></p>`;
  refreshIcons();
  const add = $('#pkAdd', dlg);
  if (add) add.onclick = async () => {
    add.disabled = true;
    try {
      const options = await pkWithReauth(() => apiRequest('/auth/passkey/register/options', { method: 'POST', body: '{}' }), d.items.length > 0);
      const credential = await pkCreate(options);
      await apiRequest('/auth/passkey/register/verify', { method: 'POST', body: JSON.stringify({ credential, name: $('#pkName', dlg).value }) });
      pkMyDialog();
    } catch (err) { uiSetStatus('#pkMsg', pkError(err), 'error'); add.disabled = false; }
  };
  dlg.querySelectorAll('[data-pk-del]').forEach(b => { b.onclick = async () => {
    if (!(await uiConfirm('Xoá passkey này? Thiết bị đó sẽ không đăng nhập được nữa.'))) return;
    try { await pkWithReauth(() => apiRequest(`/auth/passkeys/${encodeURIComponent(b.dataset.pkDel)}`, { method: 'DELETE' }), true); pkMyDialog(); }
    catch (err) { uiSetStatus('#pkMsg', err.message, 'error'); }
  }; });
  ui.open();
}

/* ---------- Cài đặt → Đăng nhập & bảo mật (admin) ---------- */
let authSettingsAt = 0;
async function renderAuthSettings(force) {
  const mount = $('#authSettingsMount'), panel = $('#authPanel');
  if (!mount || !panel) return;
  if (!(currentUser && currentUser.isAdmin)) { panel.classList.add('hidden'); return; }
  panel.classList.remove('hidden');
  if (!force && Date.now() - authSettingsAt < 15000 && mount.childElementCount) return;
  authSettingsAt = Date.now();
  let st, users;
  try { [st, users] = await Promise.all([apiRequest('/admin/auth-status'), apiRequest('/admin/users')]); }
  catch (err) { mount.innerHTML = `<p class="criteria-status error">${esc(err.message)}</p>`; return; }
  const kv = (k, v) => `<div class="sp-dl-item"><dt>${esc(k)}</dt><dd>${v}</dd></div>`;
  const src = { local: 'Cục bộ', entra: 'Microsoft Entra ID' };
  mount.innerHTML = `
    <dl class="sp-dl">
      ${kv('Chế độ đăng nhập (AUTH_MODE)', esc(st.authMode))}${kv('Tài khoản cục bộ (AUTH_LOCAL_METHOD)', esc(st.localMethod))}
      ${kv('Entra ID', uiPill(st.entraEnabled ? 'ok' : 'na', st.entraEnabled ? 'Đang bật' : 'Tắt'))}
      ${kv('URL ngoài (PUBLIC_URL)', esc(st.publicUrl || '—'))}${kv('Redirect URI', esc(st.redirectUri || '—'))}
      ${kv('Tên miền passkey (RP ID)', esc(st.rpId || '—'))}${kv('Tài khoản break-glass', esc((st.breakglass || []).join(', ') || '—'))}
      ${kv('Request vừa rồi', esc(`${st.request.scheme}://${st.request.host || ''} · IP ${st.request.clientIp || '—'}${st.request.forwardedHost ? ' · X-Forwarded-Host ' + st.request.forwardedHost : ''}`))}
    </dl>
    <div class="sp-h">Kiểm tra cấu hình</div>
    <ul class="home-list">${st.checks.map(c => `<li class="home-item"><div class="home-item-main">${uiPill(c.ok ? 'ok' : 'warn', c.ok ? 'Đạt' : 'Cần xem')} ${esc(c.text)}${c.fix ? `<span class="muted-sub">${esc(c.fix)}</span>` : ''}</div></li>`).join('')}</ul>
    <div class="sp-h">Tài khoản</div>
    <div class="sim-actions"><input id="auNewUser" maxlength="64" placeholder="tên đăng nhập (vd phu.luong)" aria-label="Tên đăng nhập mới">
      <input id="auNewName" maxlength="128" placeholder="Họ tên" aria-label="Họ tên">
      <button class="btn btn-primary btn-sm" type="button" id="auCreate"><i data-lucide="user-plus"></i>Tạo tài khoản cục bộ (passkey)</button></div>
    <p class="criteria-status" id="auMsg" role="status"></p>
    <div id="auInvite"></div>
    <div class="table-scroll"><table class="data-table"><thead><tr><th>Tài khoản</th><th>Nguồn</th><th>Mật khẩu</th><th class="num">Passkey</th><th>Đăng nhập gần nhất</th><th></th></tr></thead><tbody>
      ${users.items.map(u => `<tr><td><b>${esc(u.username)}</b>${u.displayName ? `<span class="muted-sub">${esc(u.displayName)}</span>` : ''}${u.isAdmin ? ' <span class="badge-admin">Admin</span>' : ''}</td>
        <td>${esc(src[u.source] || u.source)}</td><td>${u.source === 'local' ? (u.hasPassword ? 'Có' : 'Không') : '—'}</td>
        <td class="num">${u.source === 'local' ? esc(u.passkeys) : '—'}${u.inviteExpiresAt ? `<span class="muted-sub">link mời hạn ${esc(spTimeSafe(u.inviteExpiresAt))}</span>` : ''}</td>
        <td>${esc(u.lastLoginAt ? spTimeSafe(u.lastLoginAt) : '—')}</td>
        <td>${u.source === 'local' ? `<button class="btn btn-ghost btn-sm" type="button" data-au-invite="${esc(u.id)}">Link mời passkey</button>
          ${u.passkeys ? `<button class="btn btn-ghost btn-sm" type="button" data-au-keys="${esc(u.id)}" data-au-name="${esc(u.username)}">Passkey…</button>` : ''}` : ''}</td></tr>`).join('')}
    </tbody></table></div>
    <details class="sp-history"><summary>Cấu hình Microsoft Entra Application Proxy</summary><div class="ap-guide">
      <ol>
        <li>Entra admin center → <b>Enterprise applications → New application → On-premises application</b>. Internal URL: địa chỉ nội bộ của ICO (vd <code>http://ico.internal:8000/</code>); External URL: <code>https://ico-&lt;tenant&gt;.msappproxy.net/</code> hoặc tên miền riêng + chứng chỉ.</li>
        <li><b>Pre-authentication</b>: <i>Microsoft Entra ID</i> nếu mọi người dùng là tài khoản công ty (an toàn nhất; bật <code>ENTRA_AUTO_REDIRECT=true</code> để không phải bấm nút lần hai). Chọn <i>Passthrough</i> nếu còn tài khoản cục bộ đăng nhập bằng passkey.</li>
        <li><b>Translate URLs in headers</b>: để mặc định (bật). <b>Translate URLs in application body</b>: tắt. <b>Backend application timeout</b>: Long (báo cáo PDF / AI có thể quá 85 giây).</li>
        <li>Đặt <code>PUBLIC_URL</code> = External URL. Thêm <code>PUBLIC_URL/api/auth/oidc/callback</code> vào Redirect URI (Web) của app registration. Đặt <code>FORWARDED_ALLOW_IPS</code> = IP máy chủ connector (hoặc ingress).</li>
        <li>Connector chỉ cần đi ra Internet (443) — không mở cổng vào. Người dùng tạo passkey <b>sau khi</b> đã đặt PUBLIC_URL: passkey gắn với tên miền ngoài.</li>
      </ol></div></details>`;
  refreshIcons();
  const msg = (t, tone) => uiSetStatus('#auMsg', t, tone || 'ok');
  const showInvite = (r, who) => {
    $('#auInvite').innerHTML = `<div class="insight-box"><div><b>Link mời cho ${esc(who)}</b> (dùng 1 lần, hạn ${esc(spTimeSafe(r.expiresAt))}). Gửi riêng cho người dùng; mở trên iPhone để tạo passkey.</div>
      <div class="sim-actions"><input readonly value="${esc(r.inviteUrl)}" id="auInviteUrl" aria-label="Link mời"><button class="btn btn-secondary btn-sm" type="button" id="auCopy">Chép link</button></div></div>`;
    $('#auCopy').onclick = () => { navigator.clipboard?.writeText(r.inviteUrl).then(() => msg('Đã chép link mời.')).catch(() => $('#auInviteUrl').select()); };
  };
  $('#auCreate').onclick = async (e) => {
    const b = e.currentTarget; b.disabled = true;
    try {
      const r = await apiRequest('/admin/users', { method: 'POST', body: JSON.stringify({ username: $('#auNewUser').value, displayName: $('#auNewName').value }) });
      await renderAuthSettings(true); showInvite(r, r.user.username); msg('Đã tạo tài khoản (không mật khẩu).');
    } catch (err) { msg(err.message, 'error'); b.disabled = false; }
  };
  mount.querySelectorAll('[data-au-invite]').forEach(b => { b.onclick = async () => {
    try { const r = await apiRequest(`/admin/users/${encodeURIComponent(b.dataset.auInvite)}/passkey-invite`, { method: 'POST' });
      showInvite(r, b.closest('tr').querySelector('b').textContent); } catch (err) { msg(err.message, 'error'); }
  }; });
  mount.querySelectorAll('[data-au-keys]').forEach(b => { b.onclick = async () => {
    const id = b.dataset.auKeys;
    try {
      const r = await apiRequest(`/admin/users/${encodeURIComponent(id)}/passkeys`);
      $('#auInvite').innerHTML = `<div class="insight-box"><div><b>Passkey của ${esc(b.dataset.auName)}</b> — thu hồi khi người dùng mất / đổi máy.</div>
        <ul class="home-list">${r.items.map(p => `<li class="home-item"><div class="home-item-main">${esc(p.name)} · tạo ${esc(spTimeSafe(p.createdAt))} · dùng ${esc(p.lastUsedAt ? spTimeSafe(p.lastUsedAt) : '—')}
          <button class="btn btn-ghost btn-sm btn-danger" type="button" data-au-revoke="${esc(p.id)}">Thu hồi</button></div></li>`).join('')}</ul></div>`;
      $('#auInvite').querySelectorAll('[data-au-revoke]').forEach(x => { x.onclick = async () => {
        if (!(await uiConfirm('Thu hồi passkey này? Thiết bị đó không đăng nhập được nữa.'))) return;
        try { await apiRequest(`/admin/users/${encodeURIComponent(id)}/passkeys/${encodeURIComponent(x.dataset.auRevoke)}`, { method: 'DELETE' });
          await renderAuthSettings(true); msg('Đã thu hồi passkey.'); } catch (err) { msg(err.message, 'error'); }
      }; });
    } catch (err) { msg(err.message, 'error'); }
  }; });
}

(function bindPasskeyUi() {
  const b = $('#btnMyPasskeys');
  if (b) b.addEventListener('click', pkMyDialog);
}());
