/* =========================================================
   Sức khoẻ hạ tầng → Cấu hình (ICO v2 G9a): baseline ESXi / cluster / VM. Data: /api/baseline (baseline/service.report —
   the same numbers as the Excel / PDF and Hộp việc). Rules and exceptions: admin (server-checked).
   ========================================================= */
const cfgState = { rule: '', q: '', data: null };
const CFG_SCOPE = { host: 'Host', cluster: 'Cluster', vm: 'VM' };

async function renderConfigAuditView() {
  const mount = $('#configMount');
  if (!mount) return;
  cfgBind(mount);
  const p = new URLSearchParams((location.hash.split('?')[1]) || '');
  if (p.has('rule')) cfgState.rule = p.get('rule');
  if (p.has('q')) cfgState.q = p.get('q');
  mount.innerHTML = uiLoading();
  try {
    cfgState.data = await apiRequest('/baseline');
    cfgRender(mount);
  } catch (err) { mount.innerHTML = uiError(err.message); }
}

function cfgMatch(f) {
  const q = cfgState.q.toLowerCase();
  return (!cfgState.rule || f.rule === cfgState.rule)
    && (!q || [f.entity, f.cluster, f.ip, f.code, f.vcenterKey].some(v => String(v || '').toLowerCase().includes(q)));
}

function cfgEntity(f) {
  const name = f.scope === 'cluster' ? `<b>${esc(f.entity)}</b>` : uiEntityLink(f.scope, f.entity, f.vcenterKey);
  return `${name}${f.scope === 'vm' ? ` <span class="muted-sub mono sr-inline">${esc(f.ip || '—')}</span>` : ''}<span class="muted-sub">${esc(CFG_SCOPE[f.scope])} · ${esc(f.cluster || '—')} · ${esc(f.vcenterKey)}</span>`;
}

function cfgRender(mount) {
  const d = cfgState.data, s = d.summary, admin = uiIsAdmin();
  const opt = (v, t) => `<option value="${esc(v)}"${v === cfgState.rule ? ' selected' : ''}>${esc(t)}</option>`;
  const rows = d.findings.filter(cfgMatch);
  const exc = d.excepted.filter(cfgMatch);
  const findRows = rows.slice(0, 500).map(f => `<tr><td>${uiLevelPill(f.level)}<span class="muted-sub mono">${esc(f.code)}</span></td>
      <td>${esc(f.label)}</td><td>${cfgEntity(f)}</td><td>${esc(f.detail)}</td>
      <td>${admin ? `<button class="btn btn-ghost btn-sm" type="button" data-cfg-exc="${esc(f.code)}">Chấp nhận có hạn</button>` : ''}</td></tr>`).join('');
  const excRows = exc.map(f => `<tr><td>${uiTag('ngoại lệ')}<span class="muted-sub mono">${esc(f.code)}</span></td><td>${esc(f.label)}</td><td>${cfgEntity(f)}</td>
      <td>${esc(f.exception.reason)}<span class="muted-sub">đến ${esc(f.exception.until.split('-').reverse().join('/'))}</span></td>
      <td>${admin ? `<button class="btn btn-ghost btn-sm" type="button" data-cfg-unexc="${esc(f.exception.id)}">Bỏ ngoại lệ</button>` : ''}</td></tr>`).join('');
  const ruleRows = d.rules.map(r => `<tr><td>${esc(r.label)}<span class="muted-sub">${esc(r.scopeLabel)}</span></td>
      <td>${admin ? `<label class="finding-filter-item"><input type="checkbox" data-cfg-rule="${esc(r.rule)}" data-k="enabled"${r.enabled ? ' checked' : ''}><span>Bật</span></label>` : (r.enabled ? 'Bật' : 'Tắt')}</td>
      <td>${admin ? `<select data-cfg-rule="${esc(r.rule)}" data-k="level" aria-label="Mức của ${esc(r.label)}">${['warn', 'bad', 'crit'].map(l => `<option value="${l}"${l === r.level ? ' selected' : ''}>${esc(UI_LEVELS[l][1])}</option>`).join('')}</select>` : uiLevelPill(r.level)}</td>
      <td>${r.paramLabel ? (admin ? `<input data-cfg-rule="${esc(r.rule)}" data-k="param" value="${esc(r.param ?? '')}" inputmode="numeric" aria-label="${esc(r.paramLabel)}">` : esc(r.param)) : '—'}</td>
      <td class="num">${uiNumber(r.checked)}</td><td class="num">${uiNumber(r.findings)}</td><td class="num">${uiNumber(r.excepted)}</td></tr>`).join('');
  mount.innerHTML = `<section class="panel"><div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Lệch cấu hình</div>
      <p class="panel-sub">So cấu hình đọc từ vCenter (chỉ đọc) với baseline: SSH / ESXi Shell, lockdown, NTP, syslog, HA / DRS, build trong cluster, VMware Tools, phiên bản phần cứng VM, ISO đang gắn. Mục không đọc được không tính là lệch.</p></div>
      <div class="panel-toolbar"><button class="btn btn-secondary btn-sm" type="button" data-cfg-dl="pdf"><i data-lucide="file-text"></i>PDF</button>
        <button class="btn btn-secondary btn-sm" type="button" data-cfg-dl="xlsx"><i data-lucide="sheet"></i>Excel</button></div></div>
      ${uiKpiStrip([{ label: 'Lệch', value: uiNumber(s.findings), status: s.byLevel.crit ? { cls: 'crit', text: 'VƯỢT NGƯỠNG' } : s.byLevel.bad ? { cls: 'bad', text: 'CẢNH BÁO' } : s.byLevel.warn ? { cls: 'warn', text: 'THEO DÕI' } : { cls: 'ok', text: 'AN TOÀN' } },
        { label: 'Cảnh báo', value: uiNumber(s.byLevel.bad + s.byLevel.crit) }, { label: 'Theo dõi', value: uiNumber(s.byLevel.warn) },
        { label: 'Ngoại lệ còn hạn', value: uiNumber(s.excepted) }, { label: 'Host · VM đã đọc', value: `${uiNumber(s.hosts)} · ${uiNumber(s.vms)}` }])}
      <div class="form-grid-aligned"><label class="field"><span>Quy tắc</span><select data-cfg-filter="rule">${opt('', 'Mọi quy tắc')}${d.rules.map(r => opt(r.rule, r.label)).join('')}</select></label>
        <label class="field"><span>Tìm</span><input type="search" data-cfg-filter="q" value="${esc(cfgState.q)}" maxlength="120" placeholder="host, VM, IP, cluster, mã"></label></div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Mức · mã</th><th>Quy tắc</th><th>Đối tượng</th><th>Chi tiết</th><th></th></tr></thead>
        <tbody>${findRows || uiEmptyRow(5, 'Không có lệch cấu hình khớp bộ lọc.')}</tbody></table></div>
      ${rows.length > 500 ? `<p class="muted-sub">Hiện 500 / ${uiNumber(rows.length)} dòng — lọc thêm hoặc xuất Excel.</p>` : ''}</section>
    ${excRows ? `<section class="panel"><div class="panel-title" role="heading" aria-level="2">Lệch đã chấp nhận</div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Mã</th><th>Quy tắc</th><th>Đối tượng</th><th>Lý do</th><th></th></tr></thead><tbody>${excRows}</tbody></table></div></section>` : ''}
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Quy tắc</div>
      ${admin ? '<p class="panel-sub">Đổi bật / tắt, mức hoặc tham số thì lưu ngay; Hộp việc cập nhật ở lần làm mới tiếp theo.</p>' : ''}
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Quy tắc</th><th>Bật</th><th>Mức</th><th>Tham số</th><th class="num">Đã kiểm</th><th class="num">Lệch</th><th class="num">Ngoại lệ</th></tr></thead>
        <tbody>${ruleRows}</tbody></table></div></section>`;
  refreshIcons();
}

async function cfgSaveRule(rule) {
  const r = cfgState.data.rules.find(x => x.rule === rule);
  const val = (k) => { const el = document.querySelector(`[data-cfg-rule="${CSS.escape(rule)}"][data-k="${k}"]`); return el ? (el.type === 'checkbox' ? el.checked : el.value) : r[k]; };
  cfgState.data = await apiRequest(`/baseline/rules/${encodeURIComponent(rule)}`, { method: 'PUT', body: JSON.stringify({ enabled: val('enabled'), level: val('level'), param: val('param') }) });
  cfgRender($('#configMount'));
}

function cfgException(code) {
  const f = cfgState.data.findings.find(x => x.code === code);
  const d = new Date(); d.setMonth(d.getMonth() + 3);
  const body = `<p>${esc(f.label)} — <b>${esc(f.entity)}</b> <span class="muted-sub mono">${esc(f.code)}</span></p>
    <div class="form-grid-aligned"><label class="field"><span>Áp dụng cho</span><select id="cfgExcScope"><option value="${esc(f.scope)}">${esc(CFG_SCOPE[f.scope])} ${esc(f.entity)}</option>
      ${f.scope !== 'cluster' && f.cluster ? `<option value="cluster">Cả cluster ${esc(f.cluster)}</option>` : ''}</select></label>
      <label class="field"><span>Đến ngày</span><input type="date" id="cfgExcUntil" value="${d.toISOString().slice(0, 10)}"></label></div>
    <label class="field"><span>Lý do</span><input id="cfgExcReason" maxlength="500" placeholder="VD: vendor cần SSH để hỗ trợ, ticket #…"></label>`;
  uiPanelDialog('Chấp nhận lệch có hạn', body, '<button class="btn btn-primary btn-sm" type="button" data-cfg-save-exc>Lưu ngoại lệ</button>');
  $('#spDialogBody').onclick = async (e) => {
    if (!e.target.closest('[data-cfg-save-exc]')) return;
    const scope = $('#cfgExcScope').value;
    try {
      cfgState.data = await apiRequest('/baseline/exceptions', { method: 'POST', body: JSON.stringify({
        rule: f.rule, scope, vcenterKey: f.vcenterKey, entity: scope === 'cluster' ? f.cluster : f.entity,
        reason: $('#cfgExcReason').value, until: $('#cfgExcUntil').value }) });
      $('#spDialog').close();
      cfgRender($('#configMount'));
    } catch (err) { $('#spDialogMsg').textContent = err.message; }
  };
}

function cfgBind(mount) {
  if (mount.dataset.bound) return;
  mount.dataset.bound = '1';
  mount.addEventListener('input', (e) => {
    const f = e.target.closest('[data-cfg-filter="q"]');
    if (f) { cfgState.q = f.value.trim(); clearTimeout(cfgBind.t); cfgBind.t = setTimeout(() => cfgRender(mount), 250); }
  });
  mount.addEventListener('change', async (e) => {
    const f = e.target.closest('[data-cfg-filter="rule"]');
    if (f) { cfgState.rule = f.value; cfgRender(mount); return; }
    const r = e.target.closest('[data-cfg-rule]');
    if (r) { try { await cfgSaveRule(r.dataset.cfgRule); } catch (err) { uiAlert(err.message); } }
  });
  mount.addEventListener('click', async (e) => {
    const dl = e.target.closest('[data-cfg-dl]');
    if (dl) { uiDownload(`${API_BASE}/baseline/report.${dl.dataset.cfgDl}`, { method: 'GET' }, `ICO_Baseline_cau_hinh.${dl.dataset.cfgDl}`); return; }
    const x = e.target.closest('[data-cfg-exc]');
    const u = e.target.closest('[data-cfg-unexc]');
    try {
      if (x) cfgException(x.dataset.cfgExc);
      if (u && await uiConfirm('Bỏ ngoại lệ này? Lệch sẽ hiện lại trong danh sách và Hộp việc.')) {
        cfgState.data = await apiRequest(`/baseline/exceptions/${encodeURIComponent(u.dataset.cfgUnexc)}`, { method: 'DELETE' });
        cfgRender(mount);
      }
    } catch (err) { uiAlert(err.message); }
  });
}

/* Entity cards (host / VM): the object's deviations and accepted ones. */
function enConfigHtml(list) {
  if (!list || !list.length) return '';
  return `<section class="panel"><div class="panel-head"><div class="panel-title" role="heading" aria-level="2">Cấu hình</div>
      <div class="panel-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-nav-link="infra-config">Lệch cấu hình</button></div></div>
    <ul class="en-links">${list.map(f => `<li>${f.exception ? uiTag('ngoại lệ') : uiLevelPill(f.level)} <b>${esc(f.label)}</b>${f.scope === 'cluster' ? ` (cluster ${esc(f.entity)})` : ''}
      <span class="muted-sub">${esc(f.detail)} · ${esc(f.code)}${f.exception ? ` · chấp nhận đến ${esc(f.exception.until.split('-').reverse().join('/'))}` : ''}</span></li>`).join('')}</ul></section>`;
}
