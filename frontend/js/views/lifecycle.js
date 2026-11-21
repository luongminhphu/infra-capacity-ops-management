/* =========================================================
   Sức khoẻ hạ tầng → Vòng đời & bảo mật (ICO v2 G9b): ESXi / vCenter vs End of General Support, VMSA not patched, TLS
   certificates. Data: /api/lifecycle (lifecycle/service.report — same numbers as the Excel / PDF and Hộp việc).
   EoGS / VMSA tables, certificate scan: admin (server-checked). Nothing is fetched from the Internet.
   ========================================================= */
const lcState = { q: '', data: null };
const LC_LV = (lv) => (lv === 'na' ? uiTag('không xác định') : uiLevelPill(lv));
const lcDate = (iso) => (iso ? String(iso).slice(0, 10).split('-').reverse().join('/') : '—');

async function renderLifecycleView() {
  const mount = $('#lifecycleMount');
  if (!mount) return;
  lcBind(mount);
  const p = new URLSearchParams((location.hash.split('?')[1]) || '');
  if (p.has('q')) lcState.q = p.get('q');
  mount.innerHTML = uiLoading();
  try {
    lcState.data = await apiRequest('/lifecycle');
    lcRender(mount);
  } catch (err) { mount.innerHTML = uiError(err.message); }
}

function lcMatch(r) {
  const q = lcState.q.toLowerCase();
  return !q || [r.name, r.host, r.vmsa, r.vcenterKey, r.cluster, r.version, r.build, r.kindLabel].some(v => String(v || '').toLowerCase().includes(q));
}

function lcTable(head, rows, empty) {
  return `<div class="table-scroll"><table class="data-table"><thead><tr>${head.map(h => `<th${h.startsWith('#') ? ' class="num"' : ''}>${esc(h.replace(/^#/, ''))}</th>`).join('')}</tr></thead>
    <tbody>${rows.join('') || uiEmptyRow(head.length, empty)}</tbody></table></div>`;
}

function lcObj(r) {
  const link = r.product === 'esxi' ? uiEntityLink('host', r.name, r.vcenterKey) : `<b>${esc(r.name)}</b>`;
  return `${link}<span class="muted-sub">${esc(r.productLabel)} · ${esc(r.vcenterKey)}${r.cluster ? ' · ' + esc(r.cluster) : ''}</span>`;
}

function lcRender(mount) {
  const d = lcState.data, s = d.summary, admin = uiIsAdmin();
  const bad = (k) => s[k].crit + s[k].bad;
  const life = d.lifecycle.filter(lcMatch).slice(0, 500).map(r => `<tr><td>${LC_LV(r.level)}</td><td>${lcObj(r)}</td>
      <td>${esc(r.version || '—')}<span class="muted-sub">build ${esc(r.build || '—')}</span></td>
      <td>${r.eogs ? lcDate(r.eogs) : '<span class="muted-sub">chưa có trong bảng EoGS</span>'}</td>
      <td class="num">${r.daysLeft === null ? '—' : uiNumber(r.daysLeft)}</td></tr>`);
  const vmsa = d.vmsa.filter(lcMatch).slice(0, 500).map(r => `<tr><td>${LC_LV(r.level)}</td><td><b>${esc(r.vmsa)}</b><span class="muted-sub">${esc(r.title || '')}</span></td>
      <td class="num">${uiNumber(r.cvss, 1)}</td><td>${lcObj(r)}</td><td class="num">${esc(r.build)} → ${esc(r.fixedBuild)}</td></tr>`);
  const certs = d.certs.filter(lcMatch).map(c => `<tr><td>${LC_LV(c.level)}</td><td><b>${esc(c.name)}</b><span class="muted-sub">${esc(c.kindLabel)}</span></td>
      <td class="mono">${esc(c.host)}:${esc(c.port)}</td>
      <td>${c.error ? `<span class="muted-sub">${esc(c.error)}</span>` : `${lcDate(c.notAfter)}<span class="muted-sub">còn ${uiNumber(c.daysLeft)} ngày</span>`}</td>
      <td>${esc(c.issuer || '—')}${c.selfSigned ? ' ' + uiTag('tự ký') : ''}</td></tr>`);
  const del = (kind, id) => (admin ? `<button class="btn btn-ghost btn-sm" type="button" data-lc-del="${kind}|${esc(id)}" aria-label="Xoá dòng">Xoá</button>` : '');
  const eogsRows = d.eogsTable.map(e => `<tr><td>${esc(e.product === 'esxi' ? 'ESXi' : 'vCenter')} ${esc(e.version)}</td><td>${lcDate(e.eogs)}</td>
      <td>${lcDate(e.eotg)}</td><td>${esc(e.note || '')}</td><td>${del('eogs', e.id)}</td></tr>`);
  const vmsaRows = d.vmsaTable.map(a => `<tr><td><b>${esc(a.vmsa)}</b><span class="muted-sub">${esc(a.title || '')}</span></td><td class="num">${uiNumber(a.cvss, 1)}</td>
      <td>${esc(a.product === 'esxi' ? 'ESXi' : 'vCenter')} ${esc(a.version)}</td><td class="num">${esc(a.fixedBuild)}</td><td>${del('vmsa', a.id)}</td></tr>`);
  const tools = (kind, label) => admin ? `<div class="panel-toolbar">
      <button class="btn btn-ghost btn-sm" type="button" data-lc-tpl="${kind}"><i data-lucide="download"></i>File mẫu</button>
      <label class="btn btn-secondary btn-sm"><i data-lucide="upload"></i>Nhập ${label} (.xlsx)<input type="file" accept=".xlsx" data-lc-import="${kind}" hidden></label></div>` : '';
  mount.innerHTML = `<section class="panel"><div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Vòng đời &amp; bảo mật</div>
      <p class="panel-sub">Phiên bản ESXi / vCenter so với bảng hết hỗ trợ (EoGS), bản tin bảo mật VMSA chưa vá theo build, và chứng chỉ TLS của mọi nguồn (đọc bằng TLS handshake, mỗi ngày một lần). Hai bảng EoGS / VMSA do admin nhập — ICO không tải gì từ Internet.</p></div>
      <div class="panel-toolbar">${admin ? '<button class="btn btn-ghost btn-sm" type="button" data-lc-scan><i data-lucide="refresh-cw"></i>Đọc lại chứng chỉ</button>' : ''}
        <button class="btn btn-secondary btn-sm" type="button" data-lc-dl="pdf"><i data-lucide="file-text"></i>PDF</button>
        <button class="btn btn-secondary btn-sm" type="button" data-lc-dl="xlsx"><i data-lucide="sheet"></i>Excel</button></div></div>
      ${uiKpiStrip([{ label: 'Hết / sắp hết hỗ trợ', value: uiNumber(bad('lifecycle')), sub: `${uiNumber(s.lifecycle.warn)} trong 1 năm · ${uiNumber(s.noLine)} chưa có trong bảng` },
        { label: 'VMSA chưa vá', value: uiNumber(d.vmsa.length), sub: `${uiNumber(s.vmsaHosts)} đối tượng` },
        { label: 'Chứng chỉ ≤ 60 ngày', value: uiNumber(bad('certs')), sub: `${uiNumber(s.certs.warn)} ≤ 90 ngày` },
        { label: 'Chứng chỉ chưa đọc được', value: uiNumber(s.certs.na), sub: s.certsCheckedAt ? `đọc lúc ${uiDateTime(s.certsCheckedAt, 'short')}` : 'chưa đọc lần nào' }])}
      <div class="form-grid-aligned"><label class="field"><span>Tìm</span><input type="search" data-lc-q value="${esc(lcState.q)}" maxlength="120" placeholder="host, vCenter, VMSA, build"></label></div></section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Vòng đời ESXi / vCenter</div>
      ${lcTable(['Mức', 'Đối tượng', 'Phiên bản', 'Hết hỗ trợ (EoGS)', '#Còn (ngày)'], life, 'Không có host / vCenter khớp.')}</section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">VMSA chưa vá</div>
      ${lcTable(['Mức', 'VMSA', '#CVSS', 'Đối tượng', '#Build hiện tại → đã vá'], vmsa, d.vmsaTable.length ? 'Không có VMSA chưa vá.' : 'Chưa nhập bảng VMSA.')}</section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Chứng chỉ TLS</div>
      ${lcTable(['Mức', 'Nguồn', 'Máy chủ', 'Hết hạn', 'Issuer'], certs, 'Chưa có chứng chỉ nào được đọc.')}</section>
    <div class="sr-two">
      <section class="panel"><div class="panel-head"><div class="panel-title" role="heading" aria-level="2">Bảng EoGS</div>${tools('eogs', 'EoGS')}</div>
        ${lcTable(['Dòng phiên bản', 'EoGS', 'EoTG', 'Ghi chú', ''], eogsRows, 'Chưa có dòng nào.')}
        ${admin ? `<div class="form-grid-aligned"><label class="field"><span>Sản phẩm</span><select id="lcProd"><option value="esxi">ESXi</option><option value="vcenter">vCenter</option></select></label>
          <label class="field"><span>Phiên bản</span><input id="lcVer" maxlength="16" placeholder="8.0"></label>
          <label class="field"><span>EoGS</span><input id="lcEogs" type="date"></label>
          <label class="field"><span>EoTG</span><input id="lcEotg" type="date"></label></div>
          <button class="btn btn-primary btn-sm" type="button" data-lc-add-eogs>Lưu dòng EoGS</button>` : ''}</section>
      <section class="panel"><div class="panel-head"><div class="panel-title" role="heading" aria-level="2">Bảng VMSA</div>${tools('vmsa', 'VMSA')}</div>
        ${lcTable(['VMSA', '#CVSS', 'Dòng phiên bản', '#Build đã vá', ''], vmsaRows, 'Chưa có dòng nào.')}</section>
    </div>`;
  refreshIcons();
}

function lcBind(mount) {
  if (mount.dataset.bound) return;
  mount.dataset.bound = '1';
  const redraw = (d) => { lcState.data = d; lcRender(mount); };
  mount.addEventListener('input', (e) => {
    if (!e.target.closest('[data-lc-q]')) return;
    lcState.q = e.target.value.trim();
    clearTimeout(lcBind.t);
    lcBind.t = setTimeout(() => { const pos = e.target.selectionStart; lcRender(mount); const el = mount.querySelector('[data-lc-q]'); el.focus(); el.setSelectionRange(pos, pos); }, 300);
  });
  mount.addEventListener('change', async (e) => {
    const f = e.target.closest('[data-lc-import]');
    if (!f || !f.files.length) return;
    const fd = new FormData();
    fd.append('file', f.files[0], f.files[0].name);
    try {
      const kind = f.dataset.lcImport;
      if (!await uiConfirm(`Thay toàn bộ bảng ${kind === 'eogs' ? 'EoGS' : 'VMSA'} bằng nội dung file ${f.files[0].name}?`, { danger: false })) return;
      redraw(await apiRequest(`/lifecycle/import/${encodeURIComponent(kind)}`, { method: 'POST', body: fd }));
    } catch (err) { uiAlert(err.message); } finally { f.value = ''; }
  });
  mount.addEventListener('click', async (e) => {
    const dl = e.target.closest('[data-lc-dl]');
    const tpl = e.target.closest('[data-lc-tpl]');
    try {
      if (dl) return uiDownload(`${API_BASE}/lifecycle/report.${dl.dataset.lcDl}`, { method: 'GET' }, `ICO_Vong_doi_bao_mat.${dl.dataset.lcDl}`);
      if (tpl) return uiDownload(`${API_BASE}/lifecycle/template/${tpl.dataset.lcTpl}.xlsx`, { method: 'GET' }, `ICO_mau_${tpl.dataset.lcTpl}.xlsx`);
      if (e.target.closest('[data-lc-scan]')) {
        e.target.closest('[data-lc-scan]').disabled = true;
        return redraw(await apiRequest('/lifecycle/scan', { method: 'POST' }));
      }
      if (e.target.closest('[data-lc-add-eogs]')) {
        return redraw(await apiRequest('/lifecycle/eogs', { method: 'POST', body: JSON.stringify({
          product: $('#lcProd').value, version: $('#lcVer').value.trim(), eogs: $('#lcEogs').value, eotg: $('#lcEotg').value }) }));
      }
      const del = e.target.closest('[data-lc-del]');
      if (del && await uiConfirm('Xoá dòng này khỏi bảng?')) {
        const [kind, id] = del.dataset.lcDel.split('|');
        return redraw(await apiRequest(`/lifecycle/${kind}/${encodeURIComponent(id)}`, { method: 'DELETE' }));
      }
    } catch (err) { uiAlert(err.message); }
    return null;
  });
}
