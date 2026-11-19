/* =========================================================
   Cổng Owner (ICO v2 G2c): the whole UI of an owner-scope account (Entra app role ICO.Owner only).
   Separate shell — no menu, no app data: only /api/auth/me, /api/owner/* and sign-out (the server refuses the rest).
   ========================================================= */
const opState = { systems: [], vms: [], system: '' };

function showOwnerShell(user) {
  $('#loginScreen').classList.add('hidden');
  $('#appShell').classList.add('hidden');
  $('#ownerShell').classList.remove('hidden');
  $('#ownerUserLabel').textContent = user.displayName || user.username;
  document.title = 'Cổng Owner — Infra Capacity Ops Management';
  renderOwnerPortal();
}

async function renderOwnerPortal() {
  const mount = $('#ownerMount');
  mount.innerHTML = uiLoading();
  try {
    const q = opState.system ? `?system=${encodeURIComponent(opState.system)}` : '';
    const [sys, vms] = await Promise.all([apiRequest('/owner/systems'), apiRequest(`/owner/vms${q}`)]);
    opState.systems = sys.items; opState.vms = vms.items;
  } catch (err) { mount.innerHTML = uiError(err.message); return; }
  if (!opState.systems.length) {
    mount.innerHTML = `<section class="panel">${uiEmpty('Tài khoản của bạn chưa là owner hay người thay thế của hệ thống nào. Liên hệ nhóm hạ tầng nếu đây là nhầm lẫn.')}</section>`;
    return;
  }
  const opt = (v, t) => `<option value="${esc(v)}"${String(v) === String(opState.system) ? ' selected' : ''}>${esc(t)}</option>`;
  const cards = opState.systems.map(s => `<tr><td><b>${esc(s.name)}</b><span class="muted-sub mono">${esc(s.code)}</span></td>
      <td>${esc([s.project, s.department].filter(Boolean).join(' · ') || '—')}</td><td>${uiTag(s.criticalityLabel)}</td>
      <td>${esc(s.envLabel)}</td><td class="num">${fmtInt(s.vmCount)}</td></tr>`).join('');
  const rows = opState.vms.map(v => `<tr><td><b>${esc(v.vm)}</b><span class="muted-sub">${esc(v.vcenterKey)} · ${esc(v.cluster || '—')}</span></td>
      <td>${esc(v.systemCode)}</td><td>${v.powerOn ? uiPill('ok', 'Bật') : uiPill('na', 'Tắt')}</td>
      <td class="num">${esc(uiNumber(v.vcpu))}</td><td class="num">${esc(uiNumber(v.ramGB))}</td></tr>`).join('');
  mount.innerHTML = `
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Hệ thống của bạn (${fmtInt(opState.systems.length)})</div>
      <p class="panel-sub">Hệ thống bạn là owner hoặc người thay thế. Nhóm hạ tầng dùng danh sách này để hỏi bạn xác nhận VM trong đợt rà soát 6 tháng.</p>
      <div class="table-scroll" role="region" tabindex="0" aria-label="Hệ thống"><table class="data-table">
        <thead><tr><th>Hệ thống</th><th>Dự án · phòng ban</th><th>Mức quan trọng</th><th>Môi trường</th><th class="num">VM</th></tr></thead>
        <tbody>${cards}</tbody></table></div></section>
    <section class="table-card"><div class="panel-head"><div class="panel-title" role="heading" aria-level="2">VM (${fmtInt(opState.vms.length)})</div>
      <div class="panel-toolbar"><select id="opSystem" aria-label="Lọc theo hệ thống">${opt('', 'Mọi hệ thống của bạn')}${opState.systems.map(s => opt(s.id, `${s.code} — ${s.name}`)).join('')}</select></div></div>
      <div class="table-scroll" role="region" tabindex="0" aria-label="VM"><table class="data-table">
        <thead><tr><th>VM</th><th>Hệ thống</th><th>Nguồn</th><th class="num">vCPU</th><th class="num">RAM (GB)</th></tr></thead>
        <tbody>${rows || uiEmptyRow(5, 'Hệ thống chưa có VM nào được gán.')}</tbody></table></div></section>`;
  $('#opSystem').onchange = (e) => { opState.system = e.target.value; renderOwnerPortal(); };
}

document.addEventListener('click', async (e) => {
  if (!e.target.closest('#ownerLogout')) return;
  try { const r = await api.logout(); if (r && r.logoutUrl) { location.href = r.logoutUrl; return; } } catch (err) { /* already signed out */ }
  location.reload();
});
