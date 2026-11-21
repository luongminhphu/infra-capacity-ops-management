/* =========================================================
   Quản lý thông tin → Nhu cầu hạ tầng → Yêu cầu ServiceDesk (ICO v2 G7b): requests read from ServiceDesk Plus,
   the review queue (body table → resource lines), the original body side by side with the result, approve / skip,
   new table labels. Data: /api/itsm/requests*, /api/itsm/labels. Decisions and labels: admin only (server-checked).
   The original body is shown in an iframe with sandbox="" (no script, no form, no navigation) from server-cleaned HTML.
   ========================================================= */
const itsmState = { status: 'review', kind: '', q: '', sub: 'requests', year: 0 };
const ITSM_FILTERS = ['status', 'kind', 'q'];
const ITSM_STATUS_TONE = { parsed: 'ok', review: 'warn', failed: 'na' };

function itsmNum(v) { return v === null || v === undefined ? '' : String(v); }

async function renderItsmView() {
  const mount = $('#itsmMount');
  if (!mount) return;
  itsmBind(mount);
  const id = new URLSearchParams((location.hash.split('?')[1]) || '').get('id');
  mount.innerHTML = uiLoading();
  try {
    if (itsmState.sub !== 'requests') { await itsmAnnualRender(mount); return; }
    if (id) { mount.innerHTML = itsmDetailHtml(await apiRequest(`/itsm/requests/${encodeURIComponent(id)}`)); refreshIcons(); return; }
    const p = new URLSearchParams(ITSM_FILTERS.filter(k => itsmState[k]).map(k => [k, itsmState[k]]));
    const [list, stats] = await Promise.all([apiRequest(`/itsm/requests?${p}`), apiRequest('/itsm/stats?months=12')]);
    mount.innerHTML = itsmStatsHtml(stats) + itsmListHtml(list) + itsmProjectsHtml(await apiRequest('/itsm/projects'));
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
      <div class="panel-toolbar">${r.sdpUrl ? `<a class="btn btn-ghost btn-sm" href="${esc(r.sdpUrl)}" target="_blank" rel="noopener noreferrer">Mở trên ServiceDesk</a>` : ''}
        <button class="btn btn-ghost btn-sm" type="button" data-itsm-back>Danh sách yêu cầu</button></div></div>
      ${r.project === null ? '' : enDl([['Dự án', esc(r.project)], ['Hệ thống', r.system ? `${esc(r.system.name)} <span class="muted-sub mono">${esc(r.system.code)}</span>` : uiPill('warn', 'Chưa khớp hệ thống')],
              ['VM', r.vms.length ? r.vms.map(v => `${uiEntityLink('vm', v.vm, v.vcenterKey, false)} <span class="muted-sub mono sr-inline">${esc(v.ip || '—')}</span>${v.match === 'weak' ? ' ' + uiTag('tên ngắn') : ''}${v.retired ? ' ' + uiTag('đã gỡ') : ''}`).join(', ') : '—']])}
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
    const yr = e.target.closest('[data-itsm-year]');
    if (yr) { itsmState.year = Number(yr.value); renderItsmView(); return; }
    const f = e.target.closest('[data-itsm-filter]');
    if (f) { itsmState[f.dataset.itsmFilter] = f.value.trim(); renderItsmView(); return; }
    const pm = e.target.closest('[data-itsm-project]');
    if (pm) {
      const body = { project: pm.dataset.itsmProject, systemId: pm.value ? Number(pm.value) : null };
      try { await apiRequest('/itsm/projects', { method: 'POST', body: JSON.stringify(body) }); renderItsmView(); }
      catch (err) { uiAlert(err.message); }
      return;
    }
    const l = e.target.closest('[data-itsm-label]');
    if (l && l.value) {
      try { await apiRequest('/itsm/labels', { method: 'POST', body: JSON.stringify({ label: l.dataset.itsmLabel, field: l.value }) }); renderItsmView(); }
      catch (err) { uiAlert(err.message); }
    }
  });
  mount.addEventListener('click', async (e) => {
    if (itsmState.sub !== 'requests') {
      try { await itsmAnnualClick(e, mount); } catch (err) { uiAlert(err.message); }
      return;
    }
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

/* Project named in the requests → business system (G7c). Exact code / name matches are automatic; the rest get close
   suggestions and an admin confirms (saved and reused). Matched new-VM requests feed the owner source "itsm". */
function itsmProjectsHtml(d) {
  if (!d.projects.length) return '';
  const how = { mapped: 'đã xác nhận', exact: 'khớp chính xác' };
  const pick = (p) => {
    const sugg = new Set(p.suggestions.map(s => s.systemId));
    const opts = [...p.suggestions.map(s => ({ id: s.systemId, code: s.code, name: s.name, hint: ` (gần đúng ${uiNumber(s.score * 100)}%)` })),
      ...d.systems.filter(s => !sugg.has(s.id)).map(s => ({ ...s, hint: '' }))];
    return `<select data-itsm-project="${esc(p.project)}" aria-label="Hệ thống cho dự án ${esc(p.project)}"><option value="">— chưa gán —</option>${opts.map(s =>
      `<option value="${s.id}"${p.system && p.system.id === s.id ? ' selected' : ''}>${esc(`${s.code} — ${s.name}${s.hint}`)}</option>`).join('')}</select>`;
  };
  const rows = d.projects.map(p => `<tr><td><b>${esc(p.project)}</b><span class="muted-sub">${uiNumber(p.requests)} yêu cầu</span></td>
      <td>${uiIsAdmin() ? pick(p) : (p.system ? esc(`${p.system.code} — ${p.system.name}`) : '—')}</td>
      <td>${p.system ? uiTag(how[p.how]) : p.suggestions.length ? uiPill('warn', 'Có gợi ý') : uiPill('na', 'Chưa khớp')}</td></tr>`).join('');
  return `<section class="panel"><div class="panel-title" role="heading" aria-level="2">Dự án → hệ thống</div>
      <p class="panel-sub">Dự án trùng mã / tên hệ thống được ghép tự động. Dự án khác chọn hệ thống một lần, các yêu cầu sau dùng lại. VM tạo theo yêu cầu đã ghép hệ thống được gán owner tự động (ưu tiên thấp nhất, không đè gán tay).</p>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Dự án</th><th>Hệ thống</th><th>Ghép</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
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

/* =========================================================
   Tổng kết năm + Kế hoạch năm sau (ICO v2 G8). One call /api/itsm/annual?year= gives both tabs (itsm/annual.build — the
   same numbers as the PDF / Excel). Inputs (projects of next year, factor, standard host): admin; “Đưa vào Kế hoạch ngân
   sách”: spend editor (server-checked) — one Capex line per year, updated on every push.
   ========================================================= */
const itsmGB = (v) => `${uiNumber(v)} GB`;
const itsmDelta = (v, sign) => `${v > 0 ? sign : ''}${uiNumber(v)}`;
const itsmPct = (v) => (v === null || v === undefined ? '—' : `${uiNumber(v)}%`);
const itsmRatio = (v) => (v === null || v === undefined ? '—' : `${uiNumber(v * 100)}%`);
const ITSM_KNOWN_SRC = { approved: 'Yêu cầu đã duyệt, chưa cấp', manual: 'Dự án năm sau' };

async function itsmAnnualRender(mount) {
  const q = itsmState.year ? `?year=${encodeURIComponent(itsmState.year)}` : '';
  const d = await apiRequest(`/itsm/annual${q}`);
  itsmState.year = d.year;
  itsmState.annual = d;
  mount.innerHTML = itsmYearBar(d) + (itsmState.sub === 'annual' ? itsmAnnualHtml(d) : itsmPlanHtml(d));
  refreshIcons();
}

function itsmYearBar(d) {
  const now = new Date().getFullYear();
  const years = Array.from({ length: 6 }, (_, i) => now - i);
  return `<section class="panel"><div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">${itsmState.sub === 'annual'
      ? `Tổng kết nhu cầu năm ${esc(d.year)}` : `Kế hoạch hạ tầng ảo hoá năm ${esc(d.planYear)}`}</div>
      <p class="panel-sub">${itsmState.sub === 'annual' ? 'Yêu cầu đọc tự động hoặc đã duyệt: VM cấp mới và phần thay đổi của nâng cấp.'
        : `Từ dữ liệu đến hôm nay: tăng tự nhiên 12 tháng + nhu cầu đã biết − thu hồi đã cam kết, theo host chuẩn với N+1.`}</p></div>
      <div class="panel-toolbar"><label class="field"><span>Năm tổng kết</span><select data-itsm-year>${years.map(y => `<option value="${y}"${y === d.year ? ' selected' : ''}>${y}</option>`).join('')}</select></label>
        <button class="btn btn-secondary btn-sm" type="button" data-itsm-dl="pdf"><i data-lucide="file-text"></i>PDF</button>
        <button class="btn btn-secondary btn-sm" type="button" data-itsm-dl="xlsx"><i data-lucide="sheet"></i>Excel</button></div></div></section>`;
}

function itsmGroupTable(title, rows) {
  const body = rows.map(r => `<tr><td>${esc(r.key)}</td><td class="num">${uiNumber(r.requests)}</td><td class="num">${uiNumber(r.vms)}</td>
      <td class="num">${uiNumber(r.vcpu)}</td><td class="num">${uiNumber(r.ramGB)}</td><td class="num">${uiNumber(r.diskGB)}</td></tr>`).join('');
  return `<div class="table-scroll"><table class="data-table"><thead><tr><th>${esc(title)}</th><th class="num">Yêu cầu</th><th class="num">VM</th>
      <th class="num">vCPU</th><th class="num">RAM (GB)</th><th class="num">Disk (GB)</th></tr></thead>
      <tbody>${body || uiEmptyRow(6, 'Chưa có yêu cầu.')}</tbody></table></div>`;
}

function itsmAnnualHtml(d) {
  const t = d.summary.totals, f = d.fulfilment;
  const frow = (x, name) => `<tr><td>${esc(name)}</td><td class="num">${uiNumber(x.vms)}</td>
      <td class="num">${uiNumber(x.reqVcpu)} / ${uiNumber(x.reqRamGB)}</td><td class="num">${uiNumber(x.provVcpu)} / ${uiNumber(x.provRamGB)}</td>
      <td class="num">${uiNumber(x.used90Vcpu, 1)} / ${uiNumber(x.used90RamGB, 1)}<span class="muted-sub">${uiNumber(x.vms90)} VM</span></td>
      <td class="num">${uiNumber(x.used180Vcpu, 1)} / ${uiNumber(x.used180RamGB, 1)}<span class="muted-sub">${uiNumber(x.vms180)} VM</span></td>
      <td class="num">${itsmRatio(x.ratioVcpu)} · ${itsmRatio(x.ratioRamGB)}</td></tr>`;
  return `<section class="panel"><div class="kpi-strip">
      <div class="kpi-cell"><div class="kpi-l">Yêu cầu</div><div class="kpi-v">${uiNumber(t.requests)}</div></div>
      <div class="kpi-cell"><div class="kpi-l">VM mới</div><div class="kpi-v">${uiNumber(t.vms)}</div></div>
      <div class="kpi-cell"><div class="kpi-l">vCPU xin</div><div class="kpi-v">${uiNumber(t.vcpu)}</div></div>
      <div class="kpi-cell"><div class="kpi-l">RAM xin</div><div class="kpi-v">${itsmGB(t.ramGB)}</div></div>
      <div class="kpi-cell"><div class="kpi-l">Disk xin</div><div class="kpi-v">${itsmGB(t.diskGB)}</div></div>
      <div class="kpi-cell"><div class="kpi-l">Xử lý trung vị</div><div class="kpi-v">${t.medianDays === null ? '—' : `${uiNumber(t.medianDays, 1)} ngày`}</div></div></div>
      <div class="sr-two"><div>${itsmGroupTable('Quý', d.summary.byQuarter)}</div><div>${itsmGroupTable('Phòng ban', d.summary.byDepartment)}</div></div>
      <div class="sr-two"><div>${itsmGroupTable('Dự án', d.summary.byProject)}</div><div>${itsmGroupTable('Môi trường', d.summary.byEnv)}</div></div></section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Xin → cấp → dùng thật</div>
      <p class="panel-sub">VM tạo theo yêu cầu (khớp chắc). Dùng = cấu hình × % sử dụng trung bình 7 ngày từ ngày 90 / 180. Tỷ lệ dùng / xin tính ở mốc dài nhất đã đo; hệ số áp vào nhu cầu năm sau tối đa 100%.</p>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Phòng ban</th><th class="num">VM</th><th class="num">Xin vCPU / RAM</th>
        <th class="num">Cấp vCPU / RAM</th><th class="num">Dùng 90 ngày</th><th class="num">Dùng 180 ngày</th><th class="num">Dùng / xin vCPU · RAM</th></tr></thead>
      <tbody>${f.departments.map(x => frow(x, x.department)).join('') || uiEmptyRow(7, 'Chưa có VM tạo theo yêu cầu trong năm.')}
        ${f.departments.length ? frow(f.overall, 'Toàn bộ') : ''}</tbody></table></div></section>`;
}

function itsmPlanHtml(d) {
  const fc = d.forecast, t = fc.totals, n = fc.node, p = d.inputs.params, admin = uiIsAdmin();
  const names = fc.clusters.map(c => c.name);
  const rows = fc.clusters.map(c => `<tr><td><b>${esc(c.name)}</b><span class="muted-sub">${uiNumber(c.hosts)} host</span></td>
      <td class="num">${uiNumber(c.usedVcpu)} / ${uiNumber(c.usedRamGB)}</td>
      <td class="num">${itsmDelta(c.organicVcpu, '+')} / ${itsmDelta(c.organicRamGB, '+')}<span class="muted-sub">${uiNumber(c.organic.spanDays)} ngày lịch sử</span></td>
      <td class="num">${itsmDelta(c.knownVcpu, '+')} / ${itsmDelta(c.knownRamGB, '+')}</td><td class="num">${itsmDelta(c.reclaimVcpu, '−')} / ${itsmDelta(c.reclaimRamGB, '−')}</td>
      <td class="num"><b>${uiNumber(c.netVcpu)} / ${uiNumber(c.netRamGB)}</b></td><td class="num">${itsmPct(c.cpuPct)} / ${itsmPct(c.ramPct)}</td>
      <td class="num"><b>${uiNumber(c.nodes)}</b><span class="muted-sub">${esc(c.binding)}</span></td><td class="num">${itsmPct(c.cpuPctAfter)} / ${itsmPct(c.ramPctAfter)}</td></tr>`).join('');
  const num = (k, v, label, extra = '') => `<label class="field"><span>${label}</span><input data-itsm-param="${k}" value="${esc(v === null || v === undefined ? '' : v)}" inputmode="decimal"${admin ? '' : ' disabled'}${extra}></label>`;
  const params = `<div class="form-grid-aligned">
      ${num('sockets', p.sockets, 'Socket / host')}${num('coresPerSocket', p.coresPerSocket, 'Core / socket')}${num('ramGB', p.ramGB, 'RAM / host (GB)')}
      ${num('unitPrice', p.unitPrice, 'Đơn giá host (đ)')}${num('targetPct', p.targetPct, 'Mục tiêu % ngưỡng')}
      <label class="field"><span>Cluster mặc định</span><select data-itsm-param="defaultCluster"${admin ? '' : ' disabled'}><option value="">Cluster lớn nhất</option>${names.map(c => `<option${c === p.defaultCluster ? ' selected' : ''}>${esc(c)}</option>`).join('')}</select></label>
      <label class="finding-filter-item"><input type="checkbox" data-itsm-param="useFactor"${p.useFactor ? ' checked' : ''}${admin ? '' : ' disabled'}><span>Nhân hệ số dùng thật / xin</span></label></div>`;
  const cell = (r, i, k, label, numeric) => admin
    ? `<input data-itsm-proj="${i}|${k}" value="${esc(r[k] === null || r[k] === undefined ? '' : r[k])}" aria-label="${label} dòng ${i + 1}"${numeric ? ' inputmode="decimal"' : ''}>`
    : esc(r[k] === null || r[k] === undefined ? '—' : String(r[k]));
  const clusterCell = (r, i) => admin ? `<select data-itsm-proj="${i}|cluster" aria-label="Cluster dòng ${i + 1}"><option value="">Mặc định</option>${names.map(c => `<option${c === r.cluster ? ' selected' : ''}>${esc(c)}</option>`).join('')}</select>` : esc(r.cluster || 'Mặc định');
  const projects = [...d.inputs.projects, ...(admin ? [{}] : [])];
  const projRows = projects.map((r, i) => `<tr><td>${cell(r, i, 'name', 'Dự án')}</td><td>${cell(r, i, 'department', 'Phòng ban')}</td><td>${clusterCell(r, i)}</td>
      <td>${cell(r, i, 'vms', 'Số VM', true)}</td><td>${cell(r, i, 'vcpu', 'vCPU', true)}</td><td>${cell(r, i, 'ramGB', 'RAM', true)}</td><td>${cell(r, i, 'diskGB', 'Disk', true)}</td></tr>`).join('');
  const known = fc.known.map(k => `<tr><td>${esc(k.label)}</td><td>${uiTag(ITSM_KNOWN_SRC[k.source] || k.source)}</td><td>${esc(k.department || '—')}</td><td>${esc(k.cluster || '—')}</td>
      <td class="num">${uiNumber(k.vcpu)} × ${uiNumber(k.factorVcpu, 2)} = ${uiNumber(k.vcpuNet, 1)}</td><td class="num">${uiNumber(k.ramGB)} × ${uiNumber(k.factorRamGB, 2)} = ${uiNumber(k.ramGBNet, 1)}</td></tr>`).join('');
  const recl = fc.reclaims.map(x => `<tr><td>${esc(x.label)}</td><td>${uiTag(x.decision === 'decommission' ? 'Thu hồi' : 'Giảm cấu hình')}</td><td>${esc(x.systemCode || '—')}</td>
      <td>${esc(x.cluster)}</td><td class="num">${uiNumber(x.vcpu)}</td><td class="num">${uiNumber(x.ramGB)}</td></tr>`).join('');
  const pushed = d.inputs.pushedAt ? `Đã đưa vào Kế hoạch ngân sách lúc ${esc(uiDateTime(d.inputs.pushedAt, 'short'))} bởi ${esc(d.inputs.pushedBy || '—')}: ${uiNumber(d.inputs.pushed.nodes)} host · ${uiMoney(d.inputs.pushed.amount)}.` : 'Chưa đưa vào Kế hoạch ngân sách.';
  return `<section class="panel"><div class="kpi-strip">
      <div class="kpi-cell"><div class="kpi-l">Host cần thêm</div><div class="kpi-v">${uiNumber(t.nodes)}</div><div class="kpi-s">${esc(n.name)}</div></div>
      <div class="kpi-cell"><div class="kpi-l">Ngân sách (chưa VAT)</div><div class="kpi-v">${t.amount === null ? '—' : uiMoney(t.amount)}</div><div class="kpi-s">${n.unitPrice === null ? 'chưa nhập đơn giá' : `${uiMoney(n.unitPrice)} / host`}</div></div>
      <div class="kpi-cell"><div class="kpi-l">vCPU ròng</div><div class="kpi-v">${uiNumber(t.netVcpu)}</div><div class="kpi-s">hiện tại ${uiNumber(t.usedVcpu)}</div></div>
      <div class="kpi-cell"><div class="kpi-l">RAM ròng</div><div class="kpi-v">${itsmGB(t.netRamGB)}</div><div class="kpi-s">hiện tại ${itsmGB(t.usedRamGB)}</div></div></div>
      <p class="panel-sub">${pushed}</p>
      ${featureOn('spend') ? '<div class="sp-pane-actions"><button class="btn btn-primary btn-sm" type="button" data-itsm-push>Đưa vào Kế hoạch ngân sách</button></div>' : ''}</section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Nhu cầu ròng theo cluster</div>
      <p class="panel-sub">Ròng = hiện tại + tăng tự nhiên + nhu cầu đã biết − thu hồi (vCPU / RAM GB). % là phần của ngưỡng overcommit; host thêm đạt mục tiêu ${uiNumber(fc.targetPct)}% và N+1.</p>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Cluster</th><th class="num">Hiện tại</th><th class="num">Tăng tự nhiên</th><th class="num">Đã biết</th>
        <th class="num">Thu hồi</th><th class="num">Ròng</th><th class="num">% ngưỡng CPU / RAM</th><th class="num">Host thêm</th><th class="num">Sau bổ sung</th></tr></thead>
      <tbody>${rows || uiEmptyRow(9, 'Chưa có dữ liệu host.')}</tbody></table></div></section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Giả định</div>${params}
      <div class="panel-title" role="heading" aria-level="3">Dự án năm ${esc(d.planYear)}</div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Dự án</th><th>Phòng ban</th><th>Cluster</th><th>Số VM</th><th>vCPU</th><th>RAM (GB)</th><th>Disk (GB)</th></tr></thead>
        <tbody>${projRows || uiEmptyRow(7, 'Chưa có dự án.')}</tbody></table></div>
      ${admin ? `<div class="sp-pane-actions"><button class="btn btn-ghost btn-sm" type="button" data-itsm-addproj>Thêm dòng</button>
        <button class="btn btn-primary btn-sm" type="button" data-itsm-save>Lưu và tính lại</button></div>` : ''}</section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Nhu cầu đã biết</div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Nhu cầu</th><th>Nguồn</th><th>Phòng ban</th><th>Cluster</th><th class="num">vCPU × hệ số</th><th class="num">RAM × hệ số</th></tr></thead>
        <tbody>${known || uiEmptyRow(6, 'Không có.')}</tbody></table></div>
      <div class="panel-title" role="heading" aria-level="3">Thu hồi đã cam kết (rà soát owner)</div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>VM</th><th>Quyết định</th><th>Hệ thống</th><th>Cluster</th><th class="num">vCPU</th><th class="num">RAM (GB)</th></tr></thead>
        <tbody>${recl || uiEmptyRow(6, 'Không có.')}</tbody></table></div></section>`;
}

function itsmPlanBody(mount) {
  const params = {};
  mount.querySelectorAll('[data-itsm-param]').forEach(el => { params[el.dataset.itsmParam] = el.type === 'checkbox' ? el.checked : el.value.trim(); });
  const rows = [];
  mount.querySelectorAll('[data-itsm-proj]').forEach(el => {
    const [i, k] = el.dataset.itsmProj.split('|');
    rows[i] = rows[i] || {};
    rows[i][k] = el.value.trim();
  });
  return { params, projects: rows.filter(r => r && Object.values(r).some(v => v)) };
}

async function itsmAnnualClick(e, mount) {
  const dl = e.target.closest('[data-itsm-dl]');
  if (dl) {
    const fmt = dl.dataset.itsmDl;
    return uiDownload(`${API_BASE}/itsm/annual/report.${fmt}?year=${encodeURIComponent(itsmState.year)}`, { method: 'GET' }, `ICO_Nhu_cau_ha_tang.${fmt}`);
  }
  if (e.target.closest('[data-itsm-addproj]')) {
    const tb = e.target.closest('section').querySelector('tbody');
    const i = tb.querySelectorAll('tr').length;
    const tpl = tb.querySelector('tr:last-child');
    tb.insertAdjacentHTML('beforeend', tpl.outerHTML.replaceAll(/data-itsm-proj="\d+\|/g, `data-itsm-proj="${i}|`).replaceAll(/ value="[^"]*"/g, ''));
    return null;
  }
  if (e.target.closest('[data-itsm-save]')) {
    const d = await apiRequest(`/itsm/annual/${encodeURIComponent(itsmState.annual.planYear)}/inputs`, { method: 'PUT', body: JSON.stringify(itsmPlanBody(mount)) });
    mount.innerHTML = itsmYearBar(d) + itsmPlanHtml(d);
    itsmState.annual = d;
    refreshIcons();
    return null;
  }
  if (e.target.closest('[data-itsm-push]')) {
    const y = itsmState.annual.planYear;
    if (!await uiConfirm(`Tạo / cập nhật dòng Capex “Mở rộng hạ tầng ảo hoá ${y}” trong Kế hoạch ngân sách năm ${y} theo số trên trang?`)) return null;
    const r = await apiRequest(`/itsm/annual/${encodeURIComponent(y)}/budget`, { method: 'POST' });
    await uiAlert(r.plan ? `Đã cập nhật ${r.plan.label}: ${uiNumber(r.snapshot.nodes)} host · ${uiMoney(r.snapshot.amount)}.`
      : `Đã lưu ${uiNumber(r.snapshot.nodes)} host · ${uiMoney(r.snapshot.amount)}. Năm ${y} chưa có kế hoạch ngân sách nháp — dòng sẽ có khi tạo kế hoạch.`);
    renderItsmView();
  }
  return null;
}
