/* =========================================================
   Tài sản → Thiết bị Data Center: danh sách nhập từ Excel, tuổi theo chính sách Green (≥ 5 năm),
   bảo hành phần cứng / license – firmware ghép theo số serial với Opex.
   Data: /api/assets/dc (list), /api/assets/dc/import/preview|commit (admin).
   ========================================================= */
const dcState = { data: null, error: null, q: '', cat: '', age: '', level: '', onlyIssues: false, showRemoved: false, file: null };
const DC_AGE = { le3: '≤ 3 năm', '3to5': '3 – 5 năm', ge5: '≥ 5 năm', unknown: 'Chưa có ngày' };
const DC_REASONS = { budget: 'Vượt ngân sách', good: 'Thiết bị còn tốt', support: 'Hãng còn hỗ trợ', project: 'Chờ dự án / chuyển đổi khác', other: 'Lý do khác' };
const DC_SPEND_RANK = { none: 0, viewer: 1, editor: 2, approver: 3, admin: 4 };
/* Lifecycle decisions: admin or a spend editor (budget planners). */
const dcCanDecide = () => uiIsAdmin() || (typeof currentUser !== 'undefined' && currentUser && (DC_SPEND_RANK[currentUser.spendRole] || 0) >= 2);
const DC_COV = { active: ['ok', 'Còn hiệu lực'], planned: ['warn', 'Chưa bắt đầu'], expired: ['bad', 'Đã hết hạn'], none: ['', 'Chưa ghép'] };

async function dcLoad() {
  try { dcState.data = await apiRequest('/assets/dc'); dcState.error = null; }
  catch (err) { dcState.error = err.message; }
}

async function renderDcAssetsView() {
  const mount = $('#dcAssetsMount');
  if (!mount) return;
  const q = new URLSearchParams((location.hash.split('?')[1]) || '');
  if (q.get('level')) { dcState.level = q.get('level'); dcState.onlyIssues = false; }
  dcBind();
  if (!dcState.data) { mount.innerHTML = uiLoading(); await dcLoad(); }
  dcRender();
}

function dcKpis(s, d) {
  const limit = Math.round(d.limitMonths / 12);
  const active = s.total || 0;
  return uiKpiStrip([
    { label: 'Thiết bị đang dùng', valueHtml: `${fmtInt(active)}<small>thiết bị</small>`, subHtml: `${fmtInt(s.removed)} đã thanh lý · ${fmtInt(s.linked)} có hợp đồng ghép SN` },
    { label: `Dùng từ ${limit} năm`, valueHtml: `${fmtInt(s.ge5)}<small>thiết bị</small>`, subHtml: `${fmtInt(s.crit)} không còn bảo hành phần cứng`,
      status: s.crit ? { cls: 'crit', text: `${fmtInt(s.crit)} VƯỢT NGƯỠNG` } : s.ge5 ? { cls: 'bad', text: 'Lập kế hoạch thay thế' } : { cls: 'ok', text: 'AN TOÀN' } },
    { label: '3 – 5 năm', valueHtml: `${fmtInt(s['3to5'])}<small>thiết bị</small>`, subHtml: `${fmtInt(s.warn)} sắp đủ ${limit} năm hoặc hết bảo hành / license`,
      status: s.warn ? { cls: 'warn', text: `${fmtInt(s.warn)} THEO DÕI` } : null },
    { label: '≤ 3 năm', valueHtml: `${fmtInt(s.le3)}<small>thiết bị</small>`, subHtml: s.unknown ? `${fmtInt(s.unknown)} thiết bị chưa có ngày đưa vào dùng` : 'Trong vòng đời' },
    { label: 'Bảo hành phần cứng', valueHtml: `${fmtInt(s.hwActive)}<small>/ ${fmtInt(s.hwActive + s.hwExpired + s.hwNone)}</small>`,
      subHtml: `${fmtInt(s.hwExpired)} hết hạn · ${fmtInt(s.hwNone)} chưa ghép Opex` },
  ].filter(Boolean));
}

function dcCov(c, d) {
  const [tone, text] = DC_COV[c.state] || DC_COV.none;
  const date = c.endDate ? `đến ${dcDate(c.endDate)}` : '';
  const ref = d.opexDetail && c.code ? `<a href="${esc(dcLink('spend-opex', 'code=' + encodeURIComponent(c.code)))}">${esc(c.code)}</a>` : '';
  return `${tone ? uiPill(tone, text) : `<span class="muted-sub">${esc(text)}</span>`}${date || ref ? `<span class="muted-sub">${[ref, esc(date)].filter(Boolean).join(' · ')}</span>` : ''}`;
}

const dcDate = (iso) => iso ? iso.split('-').reverse().join('/') : '—';
const dcLink = (page, query) => (typeof homeLink === 'function' ? homeLink(page, query) : `#/${page}?${query}`);

/* In the table the age has its own column: keep the other reasons. */
function dcShortReasons(a) {
  const r = a.reasons.filter(x => !x.startsWith('Đã dùng '));
  return r.length ? `<span class="muted-sub">${esc(r.join(' · '))}</span>` : '';
}

function dcFiltered(items) {
  const q = dcState.q.trim().toLowerCase();
  return items.filter(a => (dcState.showRemoved || !a.removed)
    && (!dcState.cat || a.category === dcState.cat)
    && (!dcState.age || a.ageBucket === dcState.age)
    && (!dcState.level || a.level === dcState.level)
    && (!dcState.onlyIssues || a.level !== 'ok')
    && (!q || [a.deviceId, a.serial, a.assetTag, a.brand, a.model, a.business, a.location, a.env].join(' ').toLowerCase().includes(q)));
}

function dcRender() {
  const mount = $('#dcAssetsMount');
  if (!mount) return;
  if (dcState.error) { mount.innerHTML = uiError(dcState.error); return; }
  const d = dcState.data;
  const admin = uiIsAdmin();
  const head = `<div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Thiết bị Data Center</div>
      <p class="panel-sub">Danh sách nhập từ Excel. Tuổi tính từ ngày đưa vào dùng; bảo hành / license ghép theo số serial với Opex.</p></div>
      <div class="panel-toolbar infra-toolbar">${admin ? `<button class="btn btn-secondary btn-sm" type="button" data-dc-add><i data-lucide="plus"></i>Thêm thiết bị</button>
        <button class="btn btn-primary btn-sm" type="button" data-dc-import><i data-lucide="upload"></i>Nhập Excel</button>` : ''}
      <button class="btn btn-ghost btn-sm" type="button" data-dc-reload><i data-lucide="refresh-cw"></i>Tải lại</button></div></div>`;
  if (!d.items.length) {
    mount.innerHTML = `<section class="panel">${head}${uiEmpty(admin ? 'Chưa có thiết bị — bấm “Nhập Excel” để nạp danh sách tài sản Data Center hoặc “Thêm thiết bị” để nhập tay.' : 'Chưa có thiết bị — quản trị viên nhập danh sách từ Excel hoặc nhập tay.')}</section>`;
    refreshIcons();
    return;
  }
  const list = dcFiltered(d.items);
  const opt = (v, t, cur) => `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc(t)}</option>`;
  const rows = list.slice(0, 1000).map(a => `<tr class="infra-row" tabindex="0" data-dc-id="${esc(a.id)}">
      <td>${a.host && typeof uiEntityLink === 'function' ? uiEntityLink('host', a.host) : `<b>${esc(a.deviceId || '—')}</b>`}
        <span class="muted-sub">${esc([a.business || a.categoryLabel, a.env, a.location].filter(Boolean).join(' · '))}</span></td>
      <td>${esc(a.brand || '—')}<span class="muted-sub">${esc(a.model || '')}</span></td>
      <td class="mono">${esc(a.serial || '—')}<span class="muted-sub">${esc(a.assetTag || '')}</span></td>
      <td>${dcDate(a.acquiredOn)}<span class="muted-sub">${esc(a.ageText)}</span></td>
      <td>${dcCov(a.hw, d)}</td>
      <td>${dcCov(a.license, d)}</td>
      <td class="dc-status">${a.removed ? uiTag('Đã thanh lý') : uiPill(a.level, a.levelText)}${dcShortReasons(a)}</td></tr>`).join('');
  mount.innerHTML = `${dcKpis(d.summary, d)}
    <section class="panel">${head}
      <div class="panel-toolbar infra-toolbar dc-filters">
        <input type="search" id="dcQ" placeholder="Tìm tên, SN, asset tag, model…" value="${esc(dcState.q)}" aria-label="Tìm thiết bị">
        <select id="dcCat" aria-label="Nhóm thiết bị">${opt('', 'Mọi nhóm', dcState.cat)}${opt('server', 'Máy chủ', dcState.cat)}${opt('storage', 'Lưu trữ', dcState.cat)}${opt('network', 'Mạng', dcState.cat)}${opt('other', 'Khác', dcState.cat)}</select>
        <select id="dcAge" aria-label="Tuổi thiết bị">${opt('', 'Mọi độ tuổi', dcState.age)}${Object.entries(DC_AGE).map(([k, t]) => opt(k, t, dcState.age)).join('')}</select>
        <select id="dcLevel" aria-label="Trạng thái">${opt('', 'Mọi trạng thái', dcState.level)}${opt('crit', 'VƯỢT NGƯỠNG', dcState.level)}${opt('bad', 'CẢNH BÁO', dcState.level)}${opt('warn', 'THEO DÕI', dcState.level)}${opt('ok', 'AN TOÀN', dcState.level)}</select>
        <label class="infra-check"><input type="checkbox" id="dcIssues"${dcState.onlyIssues ? ' checked' : ''}> Chỉ mục cần xử lý</label>
        <label class="infra-check"><input type="checkbox" id="dcRemoved"${dcState.showRemoved ? ' checked' : ''}> Hiện thiết bị đã thanh lý</label>
      </div>
      <div class="table-scroll" role="region" tabindex="0" aria-label="Thiết bị Data Center"><table class="data-table infra-table dc-table">
        <caption class="sr-only">Thiết bị Data Center, tuổi và bảo hành</caption>
        <thead><tr><th>Thiết bị</th><th>Hãng · model</th><th>SN · asset tag</th><th>Đưa vào dùng</th><th>Bảo hành HW</th><th>License / FW</th><th>Trạng thái</th></tr></thead>
        <tbody>${rows || uiEmptyRow(7, 'Không có thiết bị khớp bộ lọc.')}</tbody></table></div>
      <p class="muted-sub">${fmtInt(list.length)} / ${fmtInt(d.items.length)} dòng${list.length > 1000 ? ' (hiện 1.000 dòng đầu)' : ''}.
        Ngưỡng: dùng từ ${esc(Math.round(d.limitMonths / 12))} năm CẢNH BÁO (không còn bảo hành phần cứng: VƯỢT NGƯỠNG), từ ${esc(Math.round(d.warnMonths / 12))} năm THEO DÕI.
        Thiết bị đủ ${esc(Math.round(d.limitMonths / 12))} năm trong năm kế hoạch được gợi ý ở Ngân sách → Kế hoạch năm sau → “Thay thế thiết bị” (Capex).
        Bảo hành / license: Opex có số serial trùng${d.opexDetail ? '' : ' (chi tiết hợp đồng cần quyền xem Chi phí)'}.</p>
    </section>`;
  refreshIcons();
}

function dcOpen(id) {
  const d = dcState.data, a = d.items.find(x => x.id === id);
  if (!a) return;
  const opex = d.opexDetail ? (a.opex.length ? `<div class="table-scroll"><table class="data-table"><caption class="sr-only">Opex ghép theo SN</caption>
      <thead><tr><th>Opex</th><th>Nội dung</th><th>Nhà cung cấp</th><th>Hết hạn</th></tr></thead>
      <tbody>${a.opex.map(o => `<tr><td><a href="${esc(dcLink('spend-opex', 'code=' + encodeURIComponent(o.code)))}">${esc(o.code)}</a><span class="muted-sub">${esc(o.category)}</span></td>
        <td class="wrap">${esc(o.title)}${o.contractRef ? `<span class="muted-sub">HĐ ${esc(o.contractRef)}</span>` : ''}</td><td>${esc(o.vendor)}</td><td>${dcDate(o.endDate)}</td></tr>`).join('')}</tbody></table></div>`
    : uiNote('Chưa có Opex nào khai báo SN này — thêm SN vào mục “Thiết bị” của Opex bảo hành / license.'))
    : uiNote(`Có ${fmtInt(a.opexCount)} Opex ghép theo SN — cần quyền xem Chi phí để xem chi tiết.`);
  uiPanelDialog(esc(a.deviceId || a.serial || 'Thiết bị'), `
    <dl class="sp-dl">
      <div class="sp-dl-item"><dt>Nhóm · chức năng</dt><dd>${esc(a.categoryLabel)}${a.business ? ` · ${esc(a.business)}` : ''}${a.env ? ` · ${esc(a.env)}` : ''}${a.bizType ? ` · ${esc(a.bizType)}` : ''}</dd></div>
      <div class="sp-dl-item"><dt>Hãng · model</dt><dd>${esc([a.brand, a.model].filter(Boolean).join(' ') || '—')}</dd></div>
      <div class="sp-dl-item"><dt>SN · asset tag</dt><dd>${esc(a.serial || '—')} · ${esc(a.assetTag || '—')}</dd></div>
      <div class="sp-dl-item"><dt>Vị trí</dt><dd>${esc(a.location || '—')}</dd></div>
      <div class="sp-dl-item"><dt>Đưa vào dùng</dt><dd>${dcDate(a.acquiredOn)} · ${esc(a.ageText)}${a.limitDate ? ` · đủ ${esc(Math.round(d.limitMonths / 12))} năm ${dcDate(a.limitDate)}` : ''}</dd></div>
      <div class="sp-dl-item"><dt>Trạng thái</dt><dd>${a.removed ? uiTag('Đã thanh lý') : uiPill(a.level, a.levelText)} ${esc(a.reasons.join(' · '))}</dd></div>
      <div class="sp-dl-item"><dt>Quyết định vòng đời</dt><dd>${dcDecisionText(a.decision)}</dd></div>
      <div class="sp-dl-item"><dt>Bảo hành phần cứng</dt><dd>${dcCov(a.hw, d)}</dd></div>
      <div class="sp-dl-item"><dt>License / firmware</dt><dd>${dcCov(a.license, d)}</dd></div>
      <div class="sp-dl-item"><dt>Nguồn</dt><dd>${a.manual ? 'Nhập tay' : `Excel · sheet ${esc(a.sheet)}`}${a.updatedBy ? ` · ${esc(a.updatedBy)}` : ''}</dd></div>
      ${a.notes ? `<div class="sp-dl-item"><dt>Ghi chú</dt><dd>${esc(a.notes)}</dd></div>` : ''}
    </dl>
    <h3 class="panel-subtitle">Opex ghép theo SN</h3>${opex}`,
  `${uiIsAdmin() ? '<button class="btn btn-ghost btn-sm btn-danger" type="button" id="dcDel">Xoá thiết bị</button>' : ''}
   <span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Đóng</button>
   ${dcCanDecide() && !a.removed ? '<button class="btn btn-secondary btn-sm" type="button" id="dcDecide">Thay thế / dùng tiếp</button>' : ''}
   ${uiIsAdmin() ? '<button class="btn btn-primary btn-sm" type="button" id="dcEdit"><i data-lucide="pencil"></i>Sửa</button>' : ''}`);
  const dec = $('#dcDecide');
  if (dec) dec.onclick = () => dcDecisionForm({ title: `Quyết định — ${a.deviceId || a.serial}`, base: { ids: [a.id] }, current: a.decision,
    onDone: async () => { await dcLoad(); dcRender(); } });
  const edit = $('#dcEdit');
  if (edit) edit.onclick = () => dcForm(a);
  const del = $('#dcDel');
  if (del) del.onclick = async () => {
    if (!await uiConfirm(`Xoá thiết bị ${a.deviceId || a.serial}? Lần nhập Excel sau sẽ thêm lại nếu còn trong file.`, { danger: true })) return;
    try { await apiRequest(`/assets/dc/${a.id}`, { method: 'DELETE' }); $('#spDialog').close(); await dcLoad(); dcRender(); }
    catch (err) { uiSetStatus($('#spDialogMsg'), err.message, 'error'); }
  };
}

function dcDecisionText(x) {
  if (!x) return '<span class="muted-sub">Chưa quyết định</span>';
  const bits = [x.reasonLabel, x.until ? `xem lại ${dcDate(x.until)}` : '', x.note].filter(Boolean);
  return `${uiTag(x.label)}${bits.length ? ` ${esc(bits.join(' · '))}` : ''}<span class="muted-sub">${esc(x.by || '')}</span>`;
}

/* Thay thế (Capex) or Dùng tiếp (renew in Opex) for one device or a whole plan line. Shared with Kế hoạch năm sau:
   base = { ids } or { groupOf, fiscalYear }; onDone runs after the decision is saved. */
function dcDecisionForm({ title, base, current, defaultUntil, onDone }) {
  const cur = current || {};
  const opt = (v, t, c) => `<option value="${esc(v)}"${v === (c || '') ? ' selected' : ''}>${esc(t)}</option>`;
  uiPanelDialog(esc(title), `
    <p class="muted-sub dc-form-help">Thiết bị đủ 5 năm: <b>Thay thế</b> thì nằm trong dòng Capex “Thay thế thiết bị” của Kế hoạch năm sau;
      <b>Dùng tiếp</b> (vượt ngân sách, còn tốt…) thì rời dòng Capex, bảo hành được gia hạn ở Opex — dòng “Gia hạn” của Opex đang có,
      hoặc dòng “Dùng tiếp thiết bị” khi chưa có Opex bảo hành.</p>
    <div class="form-grid-aligned dc-form">
      <label class="field"><span>Quyết định</span><select id="dcdDecision">${[['extend', 'Dùng tiếp, gia hạn Opex'], ['replace', 'Thay thế (Capex)'], ['none', 'Chưa quyết định']].map(([k, t]) => opt(k, t, cur.decision || 'extend')).join('')}</select></label>
      <label class="field"><span>Lý do</span><select id="dcdReason">${opt('', '— Chọn —', cur.reason)}${Object.entries(DC_REASONS).map(([k, t]) => opt(k, t, cur.reason)).join('')}</select></label>
      <label class="field"><span>Dùng tiếp đến</span><input id="dcdUntil" type="date" value="${esc(cur.until || defaultUntil || '')}"></label>
      <label class="field sp-span2"><span>Ghi chú</span><input id="dcdNote" maxlength="500" value="${esc(cur.note || '')}" placeholder="vd: dời sang FY sau, hãng còn hỗ trợ đến 2028"></label>
    </div>
    <p class="muted-sub dc-form-help">Để trống “Dùng tiếp đến” = dùng tiếp cho tới khi đổi quyết định. Nếu ngày này nằm trong năm kế hoạch,
      thiết bị quay lại dòng Thay thế từ ngày hôm sau.</p>`,
  `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
   <button class="btn btn-primary btn-sm" type="button" id="dcdSave">Lưu quyết định</button>`);
  const sync = () => { const ext = $('#dcdDecision').value === 'extend'; $('#dcdReason').disabled = !ext; $('#dcdUntil').disabled = !ext; };
  $('#dcdDecision').onchange = sync; sync();
  $('#dcdSave').onclick = async () => {
    const pick = $('#dcdDecision').value;
    const body = { ...base, decision: pick === 'none' ? null : pick, reason: $('#dcdReason').value || null,
      until: $('#dcdUntil').value || null, note: $('#dcdNote').value.trim() };
    $('#dcdSave').disabled = true;
    try {
      await apiRequest('/assets/dc/decision', { method: 'POST', body: JSON.stringify(body) });
      $('#spDialog').close();
      if (onDone) await onDone();
    } catch (err) { uiSetStatus($('#spDialogMsg'), err.message, 'error'); $('#dcdSave').disabled = false; }
  };
}

/* Add / edit one device by hand (admin). Same fields as a row of the Excel list. */
function dcForm(a) {
  const v = a || { category: 'server', qty: 1, removed: false };
  const opt = (val, t, cur) => `<option value="${esc(val)}"${String(val) === String(cur ?? '') ? ' selected' : ''}>${esc(t)}</option>`;
  const inp = (id, label, val, extra = '') => `<label class="field"><span>${esc(label)}</span><input id="${id}" value="${esc(val ?? '')}" ${extra}></label>`;
  uiPanelDialog(a ? `Sửa thiết bị — ${esc(a.deviceId || a.serial || '')}` : 'Thêm thiết bị Data Center', `
    <div class="form-grid-aligned dc-form">
      <label class="field"><span>Nhóm</span><select id="dcfCat">${['server', 'storage', 'network', 'other'].map(k => opt(k, { server: 'Máy chủ', storage: 'Lưu trữ', network: 'Mạng', other: 'Khác' }[k], v.category)).join('')}</select></label>
      ${inp('dcfId', 'Tên thiết bị (ID)', v.deviceId, 'maxlength="255" required')}
      ${inp('dcfBiz', 'Chức năng', v.business, 'maxlength="128" placeholder="vd: ONE CLUSTER"')}
      ${inp('dcfEnv', 'Môi trường', v.env, 'maxlength="16" placeholder="PROD / UAT / DEV" list="dcEnvList"')}
      ${inp('dcfType', 'Khối', v.bizType, 'maxlength="32" placeholder="TRADING / ENT"')}
      ${inp('dcfBrand', 'Hãng', v.brand, 'maxlength="64"')}
      ${inp('dcfModel', 'Model', v.model, 'maxlength="128"')}
      ${inp('dcfSn', 'Số serial (SN)', v.serial, 'maxlength="128" autocomplete="off"')}
      ${inp('dcfTag', 'Asset tag', v.assetTag, 'maxlength="64" autocomplete="off"')}
      ${inp('dcfDate', 'Ngày đưa vào dùng', v.acquiredOn, 'type="date"')}
      ${inp('dcfLoc', 'Vị trí', v.location, 'maxlength="128" placeholder="DC / DR / AB TOWER"')}
      ${inp('dcfQty', 'Số lượng', v.qty, 'type="number" min="1" step="1"')}
      <label class="field"><span>Tình trạng</span><select id="dcfRemoved">${opt('0', 'Đang dùng', v.removed ? '1' : '0')}${opt('1', 'Đã thanh lý', v.removed ? '1' : '0')}</select></label>
      <label class="field sp-span2"><span>Ghi chú</span><input id="dcfNotes" maxlength="500" value="${esc(v.notes || '')}"></label>
    </div>
    <datalist id="dcEnvList"><option value="PROD"><option value="BETA"><option value="UAT"><option value="DEV"></datalist>
    <p class="muted-sub dc-form-help">Cần SN hoặc asset tag. Bảo hành / license tự ghép khi SN này có trong mục “Thiết bị” của một Opex.</p>`,
  `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
   <button class="btn btn-primary btn-sm" type="button" id="dcfSave">${a ? 'Lưu' : 'Thêm'}</button>`);
  $('#dcfSave').onclick = async () => {
    const g = (id) => $(id).value.trim();
    const body = { category: g('#dcfCat'), deviceId: g('#dcfId'), business: g('#dcfBiz'), env: g('#dcfEnv'), bizType: g('#dcfType'),
      brand: g('#dcfBrand'), model: g('#dcfModel'), serial: g('#dcfSn'), assetTag: g('#dcfTag'), acquiredOn: g('#dcfDate') || null,
      location: g('#dcfLoc'), qty: Number(g('#dcfQty') || 1), removed: g('#dcfRemoved') === '1', notes: g('#dcfNotes') };
    $('#dcfSave').disabled = true;
    try {
      await apiRequest(a ? `/assets/dc/${a.id}` : '/assets/dc', { method: a ? 'PUT' : 'POST', body: JSON.stringify(body) });
      $('#spDialog').close();
      await dcLoad(); dcRender();
    } catch (err) { uiSetStatus($('#spDialogMsg'), err.message, 'error'); $('#dcfSave').disabled = false; }
  };
}

function dcImportDialog() {
  dcState.file = null;
  uiPanelDialog('Nhập thiết bị Data Center từ Excel', `
    <p class="muted-sub">Mỗi sheet một nhóm (vd DC-Server, DC-Storage, DC-Network). Dòng tiêu đề có cột SN và các cột ID, BRAND, MODEL,
      ASSET TAG, ACQUISITION DATE, LOCATION, BUSINESS FUNCTION, Env. Ô chức năng gộp được điền xuống; nhóm “REMOVED” = đã thanh lý.
      Cột tuổi trong file được tính lại theo ngày đưa vào dùng.</p>
    ${uiDropzone('dcFile', 'Chọn file .xlsx', 'Tối đa 10 MB', '.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}
    <label class="infra-check"><input type="checkbox" id="dcMarkMissing"> Đánh dấu đã thanh lý các thiết bị có trong ICO nhưng không có trong file</label>
    <div id="dcPreview"></div>`,
  `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
   <button class="btn btn-secondary btn-sm" type="button" id="dcPrev" disabled>Kiểm tra</button>
   <button class="btn btn-primary btn-sm" type="button" id="dcCommit" disabled>Nhập</button>`);
  const msg = $('#spDialogMsg');
  $('#dcFile').addEventListener('change', (e) => { dcState.file = e.target.files[0] || null; $('#dcPrev').disabled = !dcState.file; $('#dcCommit').disabled = true; $('#dcPreview').innerHTML = ''; });
  $('#dcPrev').onclick = async () => {
    const fd = new FormData(); fd.append('file', dcState.file);
    uiSetStatus(msg, 'Đang đọc file…');
    try {
      const p = await apiRequest('/assets/dc/import/preview', { method: 'POST', body: fd });
      $('#dcPreview').innerHTML = dcPreviewHtml(p);
      uiSetStatus(msg, p.counts.rows ? `Sẵn sàng nhập ${fmtInt(p.counts.rows)} dòng.` : 'Không có dòng hợp lệ.', p.counts.rows ? 'ok' : 'error');
      $('#dcCommit').disabled = !p.counts.rows;
    } catch (err) { uiSetStatus(msg, err.message, 'error'); }
  };
  $('#dcCommit').onclick = async () => {
    const fd = new FormData(); fd.append('file', dcState.file);
    $('#dcCommit').disabled = true;
    try {
      const r = await apiRequest(`/assets/dc/import/commit?markMissing=${$('#dcMarkMissing').checked}`, { method: 'POST', body: fd });
      $('#spDialog').close();
      await dcLoad(); dcRender();
      uiAlert(`Đã nhập: ${fmtInt(r.new)} mới, ${fmtInt(r.update)} cập nhật, ${fmtInt(r.same)} không đổi${r.removed ? `, ${fmtInt(r.removed)} đánh dấu đã thanh lý` : ''}${r.errors ? `; ${fmtInt(r.errors)} dòng lỗi bị bỏ qua` : ''}.`);
    } catch (err) { uiSetStatus(msg, err.message, 'error'); $('#dcCommit').disabled = false; }
  };
}

function dcPreviewHtml(p) {
  const c = p.counts;
  const act = { new: uiPill('ok', 'Mới'), update: uiPill('warn', 'Cập nhật'), same: '<span class="muted-sub">Không đổi</span>' };
  const list = (title, arr, fn) => arr.length ? `<details class="dc-issues"><summary>${esc(title)} (${fmtInt(arr.length)})</summary><ul>${arr.map(x => `<li>${fn(x)}</li>`).join('')}</ul></details>` : '';
  return `<dl class="sp-dl">
      <div class="sp-dl-item"><dt>Sheet</dt><dd>${p.sheets.map(s => `${esc(s.name)} (${fmtInt(s.rows)})`).join(' · ')}</dd></div>
      <div class="sp-dl-item"><dt>Kết quả</dt><dd>${fmtInt(c.new)} mới · ${fmtInt(c.update)} cập nhật · ${fmtInt(c.same)} không đổi · ${fmtInt(c.errors)} lỗi
        ${c.missing ? ` · ${fmtInt(c.missing)} thiết bị trong ICO không có trong file` : ''}</dd></div></dl>
    ${list('Dòng lỗi (bỏ qua)', p.errors, e => `${esc(e.sheet)} dòng ${esc(e.row)} ${esc(e.deviceId || e.serial || '')}: ${esc(e.errors.join('; '))}`)}
    ${list('Asset tag trùng giữa các thiết bị (ghép theo SN + ID)', p.duplicateTags, d => `${esc(d.assetTag)}: ${esc(d.rows.join(', '))}`)}
    ${list('SN trùng giữa các dòng (kiểm tra lại file)', p.duplicateSerials, d => `${esc(d.serial)}: ${esc(d.rows.join(', '))}`)}
    <div class="table-scroll dc-preview" role="region" tabindex="0" aria-label="Xem trước"><table class="data-table">
      <caption class="sr-only">Xem trước dữ liệu nhập</caption>
      <thead><tr><th>Dòng</th><th>Thiết bị</th><th>SN · asset tag</th><th>Đưa vào dùng</th><th>Thay đổi</th></tr></thead>
      <tbody>${p.rows.filter(r => r.action !== 'same').slice(0, 100).map(r => `<tr><td>${esc(r.sheet)} · ${esc(r.row)}</td>
        <td>${esc(r.deviceId || '—')}<span class="muted-sub">${esc([r.brand, r.model].filter(Boolean).join(' '))}${r.removed ? ' · đã thanh lý' : ''}</span></td>
        <td class="mono">${esc(r.serial || '—')}<span class="muted-sub">${esc(r.assetTag || '')}</span></td>
        <td>${dcDate(r.acquiredOn)}</td><td>${act[r.action]}</td></tr>`).join('') || uiEmptyRow(5, 'Không có thay đổi so với dữ liệu hiện có.')}</tbody></table></div>`;
}

function dcBind() {
  const mount = $('#dcAssetsMount');
  if (!mount || mount.dataset.bound) return;
  mount.dataset.bound = '1';
  mount.addEventListener('input', (e) => {
    if (e.target.id === 'dcQ') { dcState.q = e.target.value; const pos = e.target.selectionStart; dcRender(); const i = $('#dcQ'); i.focus(); i.setSelectionRange(pos, pos); }
  });
  mount.addEventListener('change', (e) => {
    const id = e.target.id;
    if (id === 'dcCat') dcState.cat = e.target.value;
    else if (id === 'dcAge') dcState.age = e.target.value;
    else if (id === 'dcLevel') dcState.level = e.target.value;
    else if (id === 'dcIssues') dcState.onlyIssues = e.target.checked;
    else if (id === 'dcRemoved') dcState.showRemoved = e.target.checked;
    else return;
    dcRender();
  });
  mount.addEventListener('click', async (e) => {
    if (e.target.closest('[data-dc-reload]')) { mount.innerHTML = uiLoading(); await dcLoad(); dcRender(); return; }
    if (e.target.closest('[data-dc-import]')) { dcImportDialog(); return; }
    if (e.target.closest('[data-dc-add]')) { dcForm(null); return; }
    const row = e.target.closest('[data-dc-id]');
    if (row && !e.target.closest('a, button')) dcOpen(Number(row.dataset.dcId));
  });
  mount.addEventListener('keydown', (e) => {
    const row = e.target.closest('[data-dc-id]');
    if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); dcOpen(Number(row.dataset.dcId)); }
  });
}
