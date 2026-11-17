/* Cài đặt → Chi phí & nhắc hạn (settings, SMTP, channels, reminder log, roles) — moved out of spend.js (audit FE-29). */

/* =========================================================
   Cài đặt → Chi phí & nhắc hạn
   ========================================================= */
async function initSpendSettings(user) {
  const panel = $('#spendSettingsPanel');
  if (!panel) return;
  const role = (user && user.spendRole) || 'none';
  panel.classList.toggle('hidden', role === 'none');
  if (role === 'none') return;
  try { await spLoadMeta(true); } catch (err) { panel.classList.add('hidden'); return; }
  spRenderSettings();
}

async function spRenderSettings() {
  const mount = $('#spendSettingsMount');
  const m = spState.meta, s = m.settings, admin = spCan('admin'), dis = admin ? '' : 'disabled';
  const months = Array.from({ length: 12 }, (_, i) => spOpt(i + 1, `Tháng ${i + 1}`, s.fiscalStartMonth)).join('');
  const hours = Array.from({ length: 24 }, (_, i) => spOpt(i, `${String(i).padStart(2, '0')}:00`, s.reminderHour)).join('');
  const cats = m.categories.map(c => `<tr data-cat="${esc(c.id)}">
      <td><code>${esc(c.code)}</code></td>
      <td><input type="text" data-k="name" value="${esc(c.name)}" maxlength="128" ${dis} aria-label="Tên loại"></td>
      <td><select data-k="grp" ${dis} aria-label="Nhóm">${Object.entries(SP_GROUPS).map(([k, t]) => spOpt(k, t, c.grp)).join('')}</select></td>
      <td><input type="number" data-k="renewalPct" min="0" max="100" step="0.01" value="${esc(c.renewalPct ?? '')}" ${dis} placeholder="—" aria-label="% gia hạn"></td>
      <td><input type="checkbox" data-k="active" ${c.active ? 'checked' : ''} ${dis} aria-label="Đang dùng"></td>
      <td>${admin ? '<button class="btn btn-ghost btn-sm" type="button" data-cat-save>Lưu</button>' : ''}</td></tr>`).join('');
  const ccs = m.costCenters.map(c => `<li><b>${esc(c.code)}</b> — ${esc(c.name)}${c.active ? '' : ' ' + uiPill('na', 'Không dùng')}</li>`).join('') || '<li class="muted-sub">Chưa có cost center.</li>';
  mount.innerHTML = `
    <div class="sp-settings-grid">
      <div>
        <h4 class="sp-h">Năm tài chính &amp; mốc nhắc</h4>
        <div class="settings-form form-grid-aligned sp-form-2">
          <label class="field"><span>Tháng bắt đầu năm tài chính</span><select id="spSetMonth" ${dis}>${months}</select><span class="field-help"></span></label>
          <label class="field"><span>Mốc nhắc (ngày trước hạn)</span><input type="text" id="spSetDays" value="${esc(s.reminderDays.join(', '))}" ${dis} placeholder="90, 60, 30" aria-describedby="spSetDaysH"><span class="field-help" id="spSetDaysH">Phân cách bằng dấu phẩy.</span></label>
          <label class="field"><span>Giờ gửi nhắc (giờ VN)</span><select id="spSetHour" ${dis}>${hours}</select><span class="field-help">Gửi một lần mỗi ngày.</span></label>
          <label class="field"><span>Địa chỉ ứng dụng</span><input type="text" id="spSetUrl" value="${esc(s.appBaseUrl)}" ${dis} placeholder="https://ico.congty.vn" aria-describedby="spSetUrlH"><span class="field-help" id="spSetUrlH">Dùng làm link trong tin nhắc.</span></label>
          <label class="field"><span>Đơn vị đề xuất (tờ trình)</span><input type="text" id="spSetDept" maxlength="128" value="${esc(s.proposalDepartment || '')}" ${dis} placeholder="Phòng Hạ tầng IT"><span class="field-help">Mặc định khi soạn tờ trình.</span></label>
          <label class="field"><span>Giữ version hợp đồng cũ (ngày)</span><input type="number" id="spSetPurge" min="1" max="365" value="${esc(s.purgeDays)}" ${dis} aria-describedby="spSetPurgeH"><span class="field-help" id="spSetPurgeH">Sau đó chỉ giữ bản Final và bản ký.</span></label>
        </div>
        <p class="panel-sub">Năm tài chính đặt tên theo năm bắt đầu (tháng 4: 04/2026–03/2027 = FY 2026). Đổi tháng bắt đầu không làm thay đổi năm tài chính của các khoản đã lưu.</p>
        ${admin ? '<div class="sim-actions"><button class="btn btn-primary btn-sm" type="button" id="spSetSave">Lưu cài đặt</button><span class="criteria-status" id="spSetStatus"></span></div>' : ''}
      </div>
      <div>
        <h4 class="sp-h">Cost center</h4>
        <p class="panel-sub">Bộ phận chịu chi phí, dùng để lọc và tổng hợp Opex / Capex.</p>
        <ul class="sp-cc">${ccs}</ul>
        ${spCan('editor') ? `<div class="sp-add-row"><input type="text" id="spCcCode" maxlength="32" placeholder="Mã" aria-label="Mã cost center"><input type="text" id="spCcName" maxlength="128" placeholder="Tên bộ phận" aria-label="Tên cost center"><button class="btn btn-secondary btn-sm" type="button" id="spCcAdd">Thêm</button></div>` : ''}
      </div>
      <div class="sp-span-all">
        <h4 class="sp-h">Loại chi phí</h4>
        <p class="panel-sub">% gia hạn dùng để tự tính Opex dự kiến từ Capex (giai đoạn sau). Loại đã dùng không xoá được, chỉ bỏ chọn “Dùng”.</p>
        <div class="table-scroll table-scroll-flat"><table class="data-table sp-cat-table"><thead><tr><th>Mã</th><th>Tên</th><th>Nhóm</th><th>% gia hạn</th><th>Dùng</th><th></th></tr></thead><tbody>${cats}</tbody></table></div>
        ${admin ? `<div class="sp-add-row"><input type="text" id="spCatCode" maxlength="32" placeholder="MÃ_MỚI" aria-label="Mã loại"><input type="text" id="spCatName" maxlength="128" placeholder="Tên loại" aria-label="Tên loại mới"><button class="btn btn-secondary btn-sm" type="button" id="spCatAdd">Thêm loại</button></div>` : ''}
      </div>
    </div>
    ${admin ? `<div id="spAdminMount">${uiLoading('Đang tải cấu hình kênh nhắc…')}</div>` : ''}
    <p class="criteria-status" id="spSettingsMsg"></p>`;
  const msg = (t, ok) => { const el = $('#spSettingsMsg'); el.textContent = t || ''; el.className = 'criteria-status' + (t ? (ok ? ' ok' : ' error') : ''); };
  spOnce('#spSetSave', async () => {
    const days = $('#spSetDays').value.split(/[,;\s]+/).filter(Boolean).map(Number);
    try {
      await apiRequest('/spend/settings', { method: 'PUT', body: JSON.stringify({ fiscalStartMonth: Number($('#spSetMonth').value),
        reminderDays: days, reminderHour: Number($('#spSetHour').value), appBaseUrl: $('#spSetUrl').value.trim(),
        proposalDepartment: $('#spSetDept').value.trim(), purgeDays: Number($('#spSetPurge').value) }) });
      await spLoadMeta(true); spRenderSettings(); msg('Đã lưu cài đặt.', true);
    } catch (err) { msg(err.message); }
  });
  spOnce('#spCcAdd', async () => {
    try { await apiRequest('/spend/cost-centers', { method: 'POST', body: JSON.stringify({ code: $('#spCcCode').value, name: $('#spCcName').value }) }); await spLoadMeta(true); spRenderSettings(); }
    catch (err) { msg(err.message); }
  });
  spOnce('#spCatAdd', async () => {
    try { await apiRequest('/spend/categories', { method: 'POST', body: JSON.stringify({ code: $('#spCatCode').value, name: $('#spCatName').value, grp: 'other' }) }); await spLoadMeta(true); spRenderSettings(); }
    catch (err) { msg(err.message); }
  });
  mount.querySelectorAll('[data-cat-save]').forEach(b => b.onclick = async () => {
    const tr = b.closest('tr'), get = (k) => tr.querySelector(`[data-k="${k}"]`);
    try {
      await apiRequest(`/spend/categories/${encodeURIComponent(tr.dataset.cat)}`, { method: 'PUT', body: JSON.stringify({
        name: get('name').value, grp: get('grp').value, renewalPct: get('renewalPct').value === '' ? null : Number(get('renewalPct').value), active: get('active').checked }) });
      await spLoadMeta(true); msg('Đã lưu loại chi phí.', true);
    } catch (err) { msg(err.message); }
  });
  if (admin) spRenderAdmin();
}

/* ---------- Admin: SMTP, channels, reminder log, roles ---------- */
async function spRenderAdmin() {
  const mount = $('#spAdminMount');
  let smtp, channels, log, users;
  try {
    [smtp, channels, log, users] = await Promise.all([apiRequest('/spend/admin/smtp'), apiRequest('/spend/admin/channels'),
      apiRequest('/spend/admin/reminders?limit=50'), apiRequest('/spend/admin/users')]);
  } catch (err) { mount.innerHTML = `${uiError(err.message)}`; return; }
  const chRows = channels.map(c => `<tr>
      <td><b>${esc(c.name)}</b><span class="muted-sub">${c.kind === 'discord' ? 'Discord' : 'Email'}</span></td>
      <td class="wrap">${c.kind === 'discord' ? (c.webhookReadable === false ? '<span class="tone-crit">Webhook không giải mã được — nhập lại</span>' : 'Webhook đã lưu (ẩn)')
        : `${esc(c.recipients || '—')}${c.includeOwner ? '<span class="muted-sub">+ email người phụ trách</span>' : ''}`}</td>
      <td>${c.lastTestAt ? `<span class="${c.lastTestOk ? 'tone-ok' : 'tone-crit'}">${c.lastTestOk ? 'OK' : 'Lỗi'}</span><span class="muted-sub">${esc(spTime(c.lastTestAt))}</span>` : '—'}</td>
      <td><label class="switch" title="Bật / tắt kênh"><input type="checkbox" role="switch" data-ch-toggle="${esc(c.id)}" ${c.enabled ? 'checked' : ''} aria-label="Bật kênh ${esc(c.name)}"><span class="slider"></span></label></td>
      <td class="src-actions"><button class="btn btn-secondary btn-sm" type="button" data-ch-test="${esc(c.id)}">Gửi thử</button>
        <button class="btn btn-secondary btn-sm" type="button" data-ch-edit="${esc(c.id)}">Sửa</button>
        <button class="btn btn-secondary btn-sm btn-danger" type="button" data-ch-del="${esc(c.id)}">Xoá</button></td></tr>`).join('');
  const st = log.state || {};
  const STATUS = { sent: ['ok', 'Đã gửi'], failed: ['crit', 'Lỗi'], skipped: ['na', 'Bỏ qua (đã có mốc nhỏ hơn)'], unknown: ['warn', 'Không rõ'], pending: ['na', 'Đang gửi'] };
  const logRows = log.items.map(r => `<tr><td>${esc(spTime(r.createdAt))}</td><td><b>${esc(r.code)}</b><span class="muted-sub">${esc(r.title)}</span></td>
      <td>${esc(r.threshold)} ngày<span class="muted-sub">hết hạn ${spDate(r.endDate)}</span></td><td>${esc(r.channel)}</td>
      <td>${spPill(...(STATUS[r.status] || ['na', r.status]))}${r.error ? `<span class="muted-sub bk-wrap">${esc(r.error)}</span>` : ''}</td>
      <td>${['failed', 'unknown'].includes(r.status) ? `<button class="btn btn-ghost btn-sm" type="button" data-log-retry="${esc(r.id)}">Gửi lại</button>` : ''}</td></tr>`).join('');
  const free = log.storage && log.storage.freeBytes !== null ? `${fmtNum(log.storage.freeBytes / 1073741824, 1)} GB trống` : 'không đọc được';
  const userRows = users.items.map(u => {
    const locked = u.isAdmin || u.roleSource === 'entra';
    return `<tr><td><b>${esc(u.displayName || u.username)}</b><span class="muted-sub">${esc(u.username)}</span></td><td>${u.authSource === 'entra' ? 'Entra ID' : 'Cục bộ'}</td>
      <td>${u.isAdmin ? spPill('ok', 'Quản trị — toàn quyền') : `<select data-user-role="${esc(u.id)}" ${locked ? 'disabled' : ''} aria-label="Vai trò ${esc(u.username)}">${users.roles.map(r => spOpt(r.value, r.label, u.spendRole)).join('')}</select>`}
      ${u.roleSource === 'entra' ? '<span class="muted-sub">Từ app role Entra</span>' : ''}</td></tr>`;
  }).join('');
  mount.innerHTML = `
    <div class="sp-settings-grid">
      <div>
        <h4 class="sp-h">Máy chủ SMTP <span class="badge-admin">Admin</span></h4>
        ${smtp.secretsKeyConfigured ? '' : '<div class="insight-box src-warning"><strong>Chưa đặt SECRETS_KEY.</strong> Mật khẩu SMTP / webhook đang mã hoá bằng khoá suy ra từ JWT_SECRET.</div>'}
        <div class="settings-form form-grid-aligned sp-form-2">
          <label class="field"><span>Máy chủ</span><input type="text" id="spSmtpHost" value="${esc(smtp.host)}" placeholder="smtp.congty.vn"></label>
          <label class="field"><span>Cổng</span><input type="number" id="spSmtpPort" min="1" max="65535" value="${esc(smtp.port)}"></label>
          <label class="field"><span>Bảo mật</span><select id="spSmtpSec">${spOpt('starttls', 'STARTTLS', smtp.security)}${spOpt('ssl', 'SSL/TLS', smtp.security)}${spOpt('none', 'Không (relay nội bộ)', smtp.security)}</select></label>
          <label class="field"><span>Người gửi (From)</span><input type="email" id="spSmtpFrom" value="${esc(smtp.fromAddr)}" placeholder="ico@congty.vn"></label>
          <label class="field"><span>Tài khoản</span><input type="text" id="spSmtpUser" value="${esc(smtp.username)}" autocomplete="off"></label>
          <label class="field"><span>Mật khẩu</span><input type="password" id="spSmtpPw" autocomplete="new-password" placeholder="${smtp.hasPassword ? (smtp.passwordReadable === false ? 'Không giải mã được — nhập lại' : 'Đã lưu — để trống để giữ') : ''}"></label>
        </div>
        <div class="sim-actions"><button class="btn btn-primary btn-sm" type="button" id="spSmtpSave">Lưu SMTP</button></div>
        <h4 class="sp-h">Vai trò người dùng</h4>
        <p class="panel-sub">Xem: đọc dữ liệu. Biên tập: tạo / sửa / gia hạn / ngừng. Duyệt: thêm xác nhận khoản dự kiến, kích hoạt lại. Tài khoản Entra lấy vai trò từ app role ICO.Spend.Viewer / Editor / Approver.</p>
        ${currentUser && currentUser.isAdmin ? '<p class="panel-sub">Quản lý cùng tài khoản và passkey ở <a href="#/settings/users" data-nav-link="set-users">Cài đặt → Người dùng &amp; quyền</a>.</p>'
          : `<div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Người dùng</th><th>Nguồn</th><th>Vai trò Chi phí</th></tr></thead><tbody>${userRows}</tbody></table></div>`}
      </div>
      <div>
        <h4 class="sp-h">Kênh nhắc hạn <span class="badge-admin">Admin</span></h4>
        <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Kênh</th><th>Gửi tới</th><th>Thử gần nhất</th><th>Bật</th><th></th></tr></thead>
          <tbody>${chRows || uiEmptyRow(5, 'Chưa có kênh — thêm email hoặc Discord để bắt đầu nhắc hạn.')}</tbody></table></div>
        <div class="sim-actions"><button class="btn btn-secondary btn-sm" type="button" data-ch-add="email"><i data-lucide="plus"></i>Email</button>
          <button class="btn btn-secondary btn-sm" type="button" data-ch-add="discord"><i data-lucide="plus"></i>Discord</button></div>
        <h4 class="sp-h">Nhật ký nhắc hạn</h4>
        <p class="panel-sub">Lần chạy gần nhất: ${st.at ? esc(spTime(st.at)) : 'chưa chạy'}
          ${st.at ? ` · gửi ${esc(st.sent)} · lỗi ${esc(st.failed)} · bỏ qua ${esc(st.skipped)}` : ''} · volume file: ${esc(free)}</p>
        <div class="sim-actions"><button class="btn btn-secondary btn-sm" type="button" id="spRunNow">Chạy nhắc hạn ngay</button></div>
        <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Thời gian</th><th>Khoản</th><th>Mốc</th><th>Kênh</th><th>Kết quả</th><th></th></tr></thead>
          <tbody>${logRows || uiEmptyRow(6, 'Chưa có lượt nhắc nào.')}</tbody></table></div>
      </div>
    </div>
    <div id="ppAdminMount"></div>
    <div id="payDefaultsMount"></div>`;
  if (typeof payRenderDefaults === 'function') payRenderDefaults($('#payDefaultsMount'));
  if (typeof ppRenderAdmin === 'function') ppRenderAdmin($('#ppAdminMount'));
  refreshIcons();
  const msg = (t, ok) => { const el = $('#spSettingsMsg'); el.textContent = t || ''; el.className = 'criteria-status' + (t ? (ok ? ' ok' : ' error') : ''); };
  spOnce('#spSmtpSave', async () => {
    try {
      await apiRequest('/spend/admin/smtp', { method: 'PUT', body: JSON.stringify({ host: $('#spSmtpHost').value, port: Number($('#spSmtpPort').value),
        security: $('#spSmtpSec').value, fromAddr: $('#spSmtpFrom').value, username: $('#spSmtpUser').value, password: $('#spSmtpPw').value }) });
      msg('Đã lưu cấu hình SMTP.', true); spRenderAdmin();
    } catch (err) { msg(err.message); }
  });
  spOnce('#spRunNow', async () => {
    try { const r = await apiRequest('/spend/admin/reminders/run', { method: 'POST' }); msg(`Đã chạy: gửi ${r.sent}, lỗi ${r.failed}, bỏ qua ${r.skipped}.`, !r.failed); spRenderAdmin(); }
    catch (err) { msg(err.message); }
  });
  mount.onclick = async (e) => {
    const t = (sel) => e.target.closest(sel);
    let b;
    try {
      if ((b = t('[data-ch-add]'))) return spOpenChannel(b.dataset.chAdd, null);
      if ((b = t('[data-ch-edit]'))) return spOpenChannel(null, channels.find(c => String(c.id) === b.dataset.chEdit));
      if ((b = t('[data-ch-test]'))) {
        b.disabled = true;
        const r = await apiRequest(`/spend/admin/channels/${encodeURIComponent(b.dataset.chTest)}/test`, { method: 'POST' });
        msg(r.message, r.ok); return spRenderAdmin();
      }
      if ((b = t('[data-ch-del]')) && await uiConfirm('Xoá kênh nhắc này? Nhật ký gửi của kênh cũng bị xoá.')) {
        await apiRequest(`/spend/admin/channels/${encodeURIComponent(b.dataset.chDel)}`, { method: 'DELETE' }); return spRenderAdmin();
      }
      if ((b = t('[data-log-retry]'))) {
        await apiRequest(`/spend/admin/reminders/${encodeURIComponent(b.dataset.logRetry)}/retry`, { method: 'POST' });
        msg('Đã đánh dấu gửi lại — sẽ gửi ở lần chạy kế tiếp (hoặc bấm “Chạy nhắc hạn ngay”).', true); return spRenderAdmin();
      }
    } catch (err) { msg(err.message); }
  };
  mount.onchange = async (e) => {
    const tg = e.target.closest('[data-ch-toggle]'), ur = e.target.closest('[data-user-role]');
    try {
      if (tg) {
        const c = channels.find(x => String(x.id) === tg.dataset.chToggle);
        await apiRequest(`/spend/admin/channels/${encodeURIComponent(c.id)}`, { method: 'PUT', body: JSON.stringify({ name: c.name, enabled: tg.checked, recipients: c.recipients, includeOwner: c.includeOwner }) });
        msg(tg.checked ? 'Đã bật kênh.' : 'Đã tắt kênh.', true);
      }
      if (ur) {
        await apiRequest(`/spend/admin/users/${encodeURIComponent(ur.dataset.userRole)}/role`, { method: 'PUT', body: JSON.stringify({ role: ur.value }) });
        msg('Đã đổi vai trò — có hiệu lực ở lần tải trang kế tiếp của người dùng đó.', true);
      }
    } catch (err) { msg(err.message); spRenderAdmin(); }
  };
}

function spOpenChannel(kind, c) {
  kind = c ? c.kind : kind;
  const v = c || { name: '', enabled: true, recipients: '', includeOwner: false };
  const body = kind === 'discord'
    ? `<div class="src-grid"><label class="field"><span>Tên kênh</span><input id="spChName" maxlength="128" value="${esc(v.name)}" placeholder="Discord #ha-tang"></label>
        <label class="field"><span>Webhook URL</span><input type="password" id="spChHook" autocomplete="off" placeholder="${c ? 'Đã lưu — để trống để giữ' : 'https://discord.com/api/webhooks/…'}"></label></div>
       <p class="panel-sub">Discord → Cài đặt kênh → Tích hợp → Webhook → Sao chép URL. URL là bí mật: lưu mã hoá, không hiển thị lại.</p>
       <label class="finding-filter-item"><input type="checkbox" id="spChOps" ${v.opsAlerts ? 'checked' : ''}><span>Nhận cả <b>cảnh báo vận hành</b>: capacity cluster ≥ 90%, backup thành công / độ phủ / RPO dưới 90% (gửi một lần, báo lại khi hết)</span></label>`
    : `<div class="src-grid"><label class="field"><span>Tên kênh</span><input id="spChName" maxlength="128" value="${esc(v.name)}" placeholder="Email nhóm hạ tầng"></label>
        <label class="field"><span>Người nhận (cách nhau dấu phẩy)</span><input id="spChTo" value="${esc(v.recipients)}" placeholder="infra@congty.vn, finance@congty.vn"></label></div>
       <label class="finding-filter-item"><input type="checkbox" id="spChOwner" ${v.includeOwner ? 'checked' : ''}><span>Gửi thêm cho email người phụ trách của từng khoản (chỉ các khoản của họ)</span></label>`;
  uiPanelDialog(`${c ? 'Sửa' : 'Thêm'} kênh ${kind === 'discord' ? 'Discord' : 'email'}`, body,
    `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button><button class="btn btn-primary btn-sm" type="button" id="spChSave">Lưu</button>`);
  spOnce('#spChSave', async () => {
    const p = { kind, name: $('#spChName').value, enabled: v.enabled };
    if (kind === 'discord') { const h = $('#spChHook').value.trim(); if (h) p.webhookUrl = h; p.opsAlerts = $('#spChOps').checked; }
    else { p.recipients = $('#spChTo').value; p.includeOwner = $('#spChOwner').checked; }
    try {
      await apiRequest(c ? `/spend/admin/channels/${encodeURIComponent(c.id)}` : '/spend/admin/channels', { method: c ? 'PUT' : 'POST', body: JSON.stringify(p) });
      $('#spDialog').close(); spRenderAdmin();
    } catch (err) { spMsg(err.message); }
  });
}
