/* =========================================================
   Quản lý thông tin → Nhu cầu hạ tầng → Yêu cầu ServiceDesk (ICO v2 G7b): requests read from ServiceDesk Plus,
   the review queue (body table → resource lines), the original body side by side with the result, approve / skip,
   new table labels. Data: /api/itsm/requests*, /api/itsm/labels. Decisions and labels: admin only (server-checked).
   The original body is shown in an iframe with sandbox="" (no script, no form, no navigation) from server-cleaned HTML.
   ========================================================= */
const itsmState = { status: 'review', kind: '', q: '' };
const ITSM_STATUS_TONE = { parsed: 'ok', review: 'warn', failed: 'na' };

function itsmNum(v) { return v === null || v === undefined ? '' : String(v); }

async function renderItsmView() {
  const mount = $('#itsmMount');
  if (!mount) return;
  itsmBind(mount);
  const id = new URLSearchParams((location.hash.split('?')[1]) || '').get('id');
  mount.innerHTML = uiLoading();
  try {
    if (id) { mount.innerHTML = itsmDetailHtml(await apiRequest(`/itsm/requests/${encodeURIComponent(id)}`)); refreshIcons(); return; }
    const p = new URLSearchParams(Object.entries(itsmState).filter(([, v]) => v));
    const [list, stats] = await Promise.all([apiRequest(`/itsm/requests?${p}`), apiRequest('/itsm/stats?months=12')]);
    mount.innerHTML = itsmStatsHtml(stats) + itsmListHtml(list);
    refreshIcons();
  } catch (err) { mount.innerHTML = uiError(err.message); }
}

function itsmListHtml(d) {
  const s = d.summary;
  const opt = (v, t, cur) => `<option value="${esc(v)}"${String(v) === String(cur) ? ' selected' : ''}>${esc(t)}</option>`;
  const rows = d.items.map(r => `<tr class="infra-row" tabindex="0" data-itsm-open="${r.id}">
      <td>${uiPill(ITSM_STATUS_TONE[r.parseStatus] || 'na', r.reviewedAt ? 'Đã duyệt' : r.parseLabel)}</td>
      <td><b>${esc(r.subject || '—')}</b><span class="muted-sub">#${esc(r.reqId)} · ${esc(r.template || '—')}</span></td>
      <td>${uiTag(r.kindLabel)}</td><td>${esc(r.requester || '—')}<span class="muted-sub">${esc(r.department || '')}</span></td>
      <td>${r.createdAt ? esc(uiDateTime(r.createdAt, 'short')) : '—'}</td></tr>`).join('');
  return `<section class="panel"><div class="panel-title" role="heading" aria-level="2">Yêu cầu từ ServiceDesk</div>
      <p class="panel-sub">Bảng tài nguyên trong yêu cầu được đọc tự động. Yêu cầu chưa đọc chắc chắn (chữ tự do, nhãn lạ, AI gợi ý) chờ người duyệt; chỉ yêu cầu đọc tự động hoặc đã duyệt mới vào thống kê.</p>
      <div class="kpi-strip"><div class="kpi-cell"><div class="kpi-l">Tổng yêu cầu</div><div class="kpi-v">${uiNumber(s.total)}</div></div>
        <div class="kpi-cell"><div class="kpi-l">Vào thống kê</div><div class="kpi-v">${uiNumber(s.counted)}</div></div>
        <div class="kpi-cell"><div class="kpi-l">Chờ duyệt</div><div class="kpi-v">${uiNumber(s.pending)}</div></div>
        <div class="kpi-cell"><div class="kpi-l">Bảng đọc tự động</div><div class="kpi-v">${s.autoTablePct === null ? '—' : `${uiNumber(s.autoTablePct, 1)}%`}</div></div></div>
      <div class="form-grid-aligned">
        <label class="field"><span>Trạng thái</span><select data-itsm-filter="status">${[['review', 'Chờ duyệt'], ['parsed', 'Đọc tự động'],
          ['reviewed', 'Đã duyệt'], ['failed', 'Bỏ qua'], ['all', 'Tất cả']].map(([v, t]) => opt(v, t, itsmState.status)).join('')}</select></label>
        <label class="field"><span>Loại yêu cầu</span><select data-itsm-filter="kind">${opt('', 'Mọi loại', itsmState.kind)}${d.kinds.map(k => opt(k.key, k.label, itsmState.kind)).join('')}</select></label>
        <label class="field"><span>Tìm</span><input type="search" data-itsm-filter="q" value="${esc(itsmState.q)}" maxlength="120"></label>
      </div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Trạng thái</th><th>Yêu cầu</th><th>Loại</th><th>Người yêu cầu</th><th>Tạo lúc</th></tr></thead>
      <tbody>${rows || uiEmptyRow(5, 'Không có yêu cầu khớp bộ lọc.')}</tbody></table></div>
      ${d.truncated ? '<p class="muted-sub">Chỉ hiện 1.000 yêu cầu mới nhất.</p>' : ''}</section>`;
}

function itsmDetailHtml(r) {
  const admin = uiIsAdmin() && !r.reviewedAt;
  const cell = (ln, f, i) => admin
    ? `<input data-itsm-cell="${i}|${f.key}" value="${esc(itsmNum(ln[f.key]))}" aria-label="${esc(f.label)} dòng ${i + 1}"${['vcpu', 'ram_gb', 'disk_gb', 'qty', 'vcpu_before', 'ram_gb_before', 'disk_gb_before'].includes(f.key) ? ' inputmode="decimal"' : ''}>`
    : esc(itsmNum(ln[f.key]) || '—');
  const lines = r.lines.length ? r.lines : (admin ? [{}] : []);
  const table = lines.length ? `<div class="table-scroll"><table class="data-table"><thead><tr>${r.fields.map(f => `<th>${esc(f.label)}</th>`).join('')}</tr></thead>
      <tbody>${lines.map((ln, i) => `<tr>${r.fields.map(f => `<td>${cell(ln, f, i)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : uiEmpty('Chưa đọc được dòng nào.');
  const kinds = [['new_vm', 'Cấp phát mới (VM provisioning)'], ['resize', 'Nâng cấp / tinh chỉnh (VM reconfiguration)'],
    ['decommission', 'Xoá VM (VM Decommision)'], ['other', 'Khác']];
  const labels = r.unknownLabels.length ? `<div class="panel-sub">Nhãn chưa biết trong bảng — gán vào trường chuẩn để lần sau đọc tự động:</div>
      <ul class="en-links">${r.unknownLabels.map(l => `<li>${uiTag(l)}${uiIsAdmin() ? ` <select data-itsm-label="${esc(l)}" aria-label="Trường chuẩn cho nhãn ${esc(l)}">
        <option value="">— chọn trường —</option>${r.labelFields.map(f => `<option value="${esc(f.key)}">${esc(f.label)}</option>`).join('')}</select>` : ''}</li>`).join('')}</ul>` : '';
  return `<section class="panel"><div class="panel-head"><div><div class="eyebrow">Yêu cầu #${esc(r.reqId)} · ${esc(r.template || '')}</div>
      <div class="panel-title" role="heading" aria-level="2">${esc(r.subject || '—')}</div>
      <p class="panel-sub">${esc(r.requester || '—')} · ${esc(r.requesterEmail || '')} · ${esc(r.department || '')} · ${esc(r.status || '')}</p></div>
      <button class="btn btn-ghost btn-sm" type="button" data-itsm-back>Danh sách yêu cầu</button></div>
      <p>${uiPill(ITSM_STATUS_TONE[r.parseStatus] || 'na', r.reviewedAt ? `Đã duyệt bởi ${r.reviewedBy}` : r.parseLabel)}
        ${r.confidence !== null && r.confidence !== undefined ? `<span class="muted-sub sr-inline">độ tin cậy ${uiNumber(r.confidence * 100)}%</span>` : ''}</p></section>
    <div class="sr-two">
      <section class="panel"><div class="panel-title" role="heading" aria-level="2">Nội dung gốc</div>
        <iframe class="itsm-body" sandbox="" title="Nội dung gốc của yêu cầu" srcdoc="${esc(r.bodyHtml || '<p>(trống)</p>')}"></iframe></section>
      <section class="panel"><div class="panel-title" role="heading" aria-level="2">Tài nguyên đọc được</div>
        ${admin ? `<label class="field"><span>Loại yêu cầu</span><select id="itsmKind">${kinds.map(([v, t]) => `<option value="${v}"${v === r.kind ? ' selected' : ''}>${t}</option>`).join('')}</select></label>` : `<p>${uiTag(r.kindLabel)}</p>`}
        ${table}
        ${admin ? '<button class="btn btn-ghost btn-sm" type="button" data-itsm-addline>Thêm dòng</button>' : ''}
        ${labels}
        ${admin ? `<div class="sp-pane-actions"><button class="btn btn-primary btn-sm" type="button" data-itsm-act="approve">Duyệt</button>
          <button class="btn btn-secondary btn-sm" type="button" data-itsm-act="skip">Bỏ qua</button></div>` : ''}</section>
    </div>`;
}

function itsmLines(mount) {
  const out = [];
  mount.querySelectorAll('[data-itsm-cell]').forEach(el => {
    const [i, k] = el.dataset.itsmCell.split('|');
    out[i] = out[i] || {};
    out[i][k] = el.value.trim().replace(',', '.');
  });
  return out.filter(ln => ln && Object.values(ln).some(v => v));
}

function itsmBind(mount) {
  if (mount.dataset.bound) return;
  mount.dataset.bound = '1';
  const id = () => new URLSearchParams((location.hash.split('?')[1]) || '').get('id');
  mount.addEventListener('change', async (e) => {
    const f = e.target.closest('[data-itsm-filter]');
    if (f) { itsmState[f.dataset.itsmFilter] = f.value.trim(); renderItsmView(); return; }
    const l = e.target.closest('[data-itsm-label]');
    if (l && l.value) {
      try { await apiRequest('/itsm/labels', { method: 'POST', body: JSON.stringify({ label: l.dataset.itsmLabel, field: l.value }) }); renderItsmView(); }
      catch (err) { uiAlert(err.message); }
    }
  });
  mount.addEventListener('click', async (e) => {
    if (e.target.closest('[data-itsm-back]')) { location.hash = '#/demand'; return; }
    if (e.target.closest('[data-itsm-addline]')) {
      const tb = mount.querySelector('.sr-two tbody');
      const i = tb ? tb.rows.length : 0;
      if (tb) tb.insertAdjacentHTML('beforeend', `<tr>${[...tb.rows[0].cells].map((c, j) => `<td><input data-itsm-cell="${i}|${esc(c.querySelector('input').dataset.itsmCell.split('|')[1])}" aria-label="Dòng ${i + 1}"></td>`).join('')}</tr>`);
      return;
    }
    const a = e.target.closest('[data-itsm-act]');
    if (a) {
      const body = a.dataset.itsmAct === 'approve' ? { action: 'approve', lines: itsmLines(mount), kind: $('#itsmKind').value } : { action: 'skip' };
      try { await apiRequest(`/itsm/requests/${encodeURIComponent(id())}/review`, { method: 'POST', body: JSON.stringify(body) }); renderItsmView(); }
      catch (err) { uiAlert(err.message); }
      return;
    }
    const row = e.target.closest('[data-itsm-open]');
    if (row) location.hash = `#/demand?id=${encodeURIComponent(row.dataset.itsmOpen)}`;
  });
  mount.addEventListener('keydown', (e) => {
    const row = e.target.closest('[data-itsm-open]');
    if (row && e.key === 'Enter') row.click();
  });
}

/* One year of VM provisioning / reconfiguration / decommission (counted requests only: read automatically or reviewed). */
const ITSM_KINDS3 = [['new_vm', 'Cấp phát mới', 'VM provisioning'], ['resize', 'Nâng cấp / tinh chỉnh', 'VM reconfiguration'],
  ['decommission', 'Xoá VM', 'VM Decommision']];

/* A reconfiguration counts the change To-be − As-is: shown with its sign (+ added, − reduced). */
const itsmSigned = (v) => `${v > 0 ? '+' : ''}${uiNumber(v)}`;

function itsmRes(b, signed) {
  const n = signed ? itsmSigned : uiNumber;
  return `${n(b.vcpu)} vCPU · ${n(b.ramGB)} GB RAM · ${n(b.diskGB)} GB disk`;
}

function itsmKindCell(b, kind) {
  if (!b || !b.requests) return '<td class="num">—</td>';
  return `<td class="num"><b>${uiNumber(b.vms)} VM</b><span class="muted-sub">${uiNumber(b.requests)} yêu cầu · ${itsmRes(b, kind === 'resize')}</span></td>`;
}

function itsmStatsHtml(s) {
  const t = s.total;
  const kpis = ITSM_KINDS3.map(([k, label, tpl]) => `<div class="kpi-cell"><div class="kpi-l">${esc(label)}</div>
      <div class="kpi-v">${uiNumber(t[k].vms)} VM</div><div class="kpi-s">${uiNumber(t[k].requests)} yêu cầu ${esc(tpl)} · ${k === 'resize' ? 'thay đổi ' : k === 'decommission' ? 'thu hồi ' : ''}${itsmRes(t[k], k === 'resize')}</div></div>`).join('');
  const rows = s.byMonth.slice().reverse().map(m => `<tr><td>${esc(m.month.split('-').reverse().join('/'))}</td>${ITSM_KINDS3.map(([k]) => itsmKindCell(m[k], k)).join('')}</tr>`).join('');
  const pending = ITSM_KINDS3.reduce((a, [k]) => a + (s.pendingReview[k] || 0), 0);
  return `<section class="panel"><div class="panel-title" role="heading" aria-level="2">Thống kê 12 tháng — tạo mới, nâng cấp, xoá VM</div>
      <p class="panel-sub">Từ ${esc(s.since.split('-').reverse().join('/'))}. VM và tài nguyên = tổng các dòng × số lượng: cấp phát mới là tài nguyên tạo ra,
        nâng cấp là phần thay đổi To-be − As-is (dấu − là giảm), xoá VM là tài nguyên thu hồi.${t.resize.noAsIs ? ` ${uiNumber(t.resize.noAsIs)} dòng nâng cấp thiếu bảng As-is chưa tính được phần thay đổi.` : ''}
        Chỉ tính yêu cầu đọc tự động hoặc đã duyệt${pending ? ` — còn ${uiNumber(pending)} yêu cầu chờ duyệt chưa tính` : ''}.</p>
      <div class="kpi-strip">${kpis}</div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Tháng</th>${ITSM_KINDS3.map(([, l]) => `<th class="num">${esc(l)}</th>`).join('')}</tr></thead>
      <tbody>${rows || uiEmptyRow(4, 'Chưa có yêu cầu trong 12 tháng.')}</tbody></table></div></section>`;
}
