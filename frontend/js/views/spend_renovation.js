/* =========================================================
   Chi phí & Hợp đồng IT — Cải tạo văn phòng (phần IT): dự án theo tầng, quy mô, 3 khối
   (Cabling gồm Hub / Network / Video Conference), số liệu theo nhóm chi phí chuẩn,
   so sánh lịch sử theo chỉ số (đ / node, đ / phòng họp VC), nhập file Summarize.
   Every number is computed by the backend (spend/renovation.py).
   ========================================================= */

const rnState = { list: null, current: null, history: null, mapping: null, bound: false, error: null };
const RN_WS = { cabling: 'IT Facility — Cabling (gồm Hub)', network: 'IT Network', vc: 'Video Conference' };
const RN_KIND = { estimate: 'Dự toán', awarded: 'Báo giá trúng', actual: 'Thực chi' };
const RN_STATUS = { planning: ['na', 'Lập kế hoạch'], executing: ['warn', 'Đang thi công'], closed: ['ok', 'Đã chốt'] };
const RN_KPI = { perNode: 'đ / node', perHead: 'đ / nhân sự', perAp: 'đ / AP', perRoom: 'đ / phòng VC' };
const rnUrl = (code, ...parts) => ['/spend/renovations', encodeURIComponent(code), ...parts].join('/');
const rnMoney = (v) => (v === null || v === undefined) ? '<span class="muted-sub sr-inline">không có số liệu</span>' : esc(uiNumber(v, 3));
const rnPct = (v) => (v === null || v === undefined) ? '—' : `${v > 0 ? '+' : ''}${Number(v).toLocaleString('vi-VN', { maximumFractionDigits: 1 })}%`;

async function renderRenovationsPage() {
  rnBind();
  if (rnState.current) { await rnOpen(rnState.current.code); return; }
  try { rnState.list = await apiRequest('/spend/renovations'); rnState.error = null; }
  catch (err) { rnState.list = []; rnState.error = err.message; }
  rnRenderList();
}

function rnRenderList() {
  const rows = (rnState.list || []).map(p => `<tr data-rn-code="${esc(p.code)}" class="sp-row" tabindex="0">
      <td><b>${esc(p.floor)}</b><span class="muted-sub">${esc(p.code)}</span></td><td>${esc(p.year)}</td>
      <td class="wrap">${esc(p.name)}${p.isHistorical ? ' <span class="pill na">Lịch sử</span>' : ''}<span class="muted-sub">${esc([p.nodes ? `${p.nodes} node` : '', p.headcount ? `${p.headcount} NS` : '', p.configTags || ''].filter(Boolean).join(' · '))}</span></td>
      <td>${spPill(...(RN_STATUS[p.status] || ['na', p.statusLabel]))}</td>
      ${['cabling', 'network', 'vc'].map(ws => `<td class="num">${rnMoney(p.workstreams[ws].total)}<span class="muted-sub">${p.workstreams[ws].kpi !== null ? esc(Number(p.workstreams[ws].kpi).toLocaleString('vi-VN')) + (ws === 'vc' ? ' / phòng' : ' / node') : ''}</span></td>`).join('')}
      <td class="num"><b>${rnMoney(p.total)}</b></td></tr>`).join('');
  $('#spendMount').innerHTML = `
    <section class="table-card">
      <div class="panel-head">
        <div><div class="panel-title" role="heading" aria-level="2">Cải tạo văn phòng — chi phí IT</div>
          <p class="panel-sub">Mỗi tầng một dự án: quy mô (node, nhân sự, phòng họp VC), cấu hình (10G / Wifi7…), 3 khối chi phí. So sánh với các tầng đã làm theo chi phí / node và / phòng họp; lịch sử dùng thực chi.</p></div>
        <div class="panel-toolbar">
          ${spCan('admin') ? '<button class="btn btn-ghost btn-sm" type="button" data-rn-act="groups"><i data-lucide="list"></i>Nhóm chi phí</button><button class="btn btn-ghost btn-sm" type="button" data-rn-act="import"><i data-lucide="upload"></i>Nhập lịch sử</button>' : ''}
          ${spCan('editor') ? '<button class="btn btn-primary btn-sm" type="button" data-rn-act="new"><i data-lucide="plus"></i>Tạo dự án</button>' : ''}</div>
      </div>
      ${rnState.error ? `<p class="criteria-status error">${esc(rnState.error)}</p>` : ''}
      <div class="table-scroll"><table class="data-table sp-table">
        <thead><tr><th>Tầng</th><th>Năm</th><th>Dự án</th><th>Trạng thái</th><th class="num">Cabling</th><th class="num">Network</th><th class="num">VC</th><th class="num">Tổng</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="8" class="empty-note">Chưa có dự án.${spCan('admin') ? ' Bấm “Nhập lịch sử” để nạp các tầng đã làm từ file so sánh (sheet Summarize).' : ''}</td></tr>`}</tbody>
      </table></div>
    </section>`;
  refreshIcons();
}

function rnBind() {
  if (rnState.bound) return;
  rnState.bound = true;
  const mount = $('#spendMount');
  mount.addEventListener('click', (e) => {
    if (spState.sub !== 'renovations') return;
    const row = e.target.closest('[data-rn-code]');
    if (row) return rnOpen(row.dataset.rnCode);
    const quote = e.target.closest('[data-rn-quote]');
    if (quote) { qtState.current = { code: quote.dataset.rnQuote }; qtState.tab = 'quotes'; location.hash = '#/spend-quotes'; return; }
    const act = e.target.closest('[data-rn-act]');
    if (act) return rnAction(act.dataset.rnAct, act);
  });
  mount.addEventListener('change', (e) => {
    if (spState.sub !== 'renovations' || !e.target.closest('[data-rn-hist]')) return;
    rnState.history = $$('[data-rn-hist]').filter(x => x.checked).map(x => x.value);
    rnOpen(rnState.current.code);
  });
  mount.addEventListener('keydown', (e) => {
    const row = spState.sub === 'renovations' && e.target.closest('[data-rn-code]');
    if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); rnOpen(row.dataset.rnCode); }
  });
}

async function rnOpen(code) {
  if (rnState.current && rnState.current.code !== code) rnState.history = null;
  const qs = rnState.history ? `?history=${encodeURIComponent(rnState.history.join(',') || '0')}` : '';
  try {
    rnState.current = await apiRequest(rnUrl(code) + qs);
    rnState.mapping = rnState.current.quoteRequests.length ? await apiRequest(rnUrl(code, 'quote-mapping')) : null;
  } catch (err) { uiAlert(err.message); rnState.current = null; return; }
  rnRenderDetail();
}

function rnEditable() {
  const d = rnState.current;
  return spCan('editor') && d && (d.status !== 'closed' || spCan('approver'));
}

function rnRenderDetail() {
  const d = rnState.current, ed = rnEditable();
  const chosen = new Set(d.comparison.cabling.history.map(h => String(h.projectId)));
  const scale = [['Node mạng', d.nodes], ['Nhân sự', d.headcount], ['Phòng hub', d.hubRooms], ['Phòng họp', d.meetingRooms],
    ['Phòng họp có VC', d.vcRooms], ['Access point', d.accessPoints], ['Diện tích (m²)', d.areaM2]];
  $('#spendMount').innerHTML = `
    <section class="panel">
      <div class="panel-head">
        <div><button class="btn btn-ghost btn-sm" type="button" data-rn-act="back"><i data-lucide="arrow-left"></i>Danh sách dự án</button>
          <div class="panel-title" role="heading" aria-level="2">${esc(d.floor)} (${esc(d.year)}) — ${esc(d.name)}</div>
          <p class="panel-sub">${esc(d.code)} · ${spPill(...(RN_STATUS[d.status] || ['na', d.statusLabel]))}${d.isHistorical ? ' <span class="pill na">Lịch sử</span>' : ''}${d.configTags ? ` · cấu hình <b>${esc(d.configTags)}</b>` : ''}${d.capex ? ` · Capex ${esc(d.capex.code)}` : ''}</p></div>
        <div class="panel-toolbar">
          <button class="btn btn-ghost btn-sm" type="button" data-rn-act="xlsx"><i data-lucide="file-spreadsheet"></i>Excel (Summarize)</button>
          <button class="btn btn-ghost btn-sm" type="button" data-rn-act="pdf"><i data-lucide="file-text"></i>PDF</button>
          ${ed ? '<button class="btn btn-ghost btn-sm" type="button" data-rn-act="edit"><i data-lucide="pencil"></i>Sửa</button>' : ''}
          ${spCan('approver') && d.status !== 'closed' ? '<button class="btn btn-primary btn-sm" type="button" data-rn-act="close"><i data-lucide="lock"></i>Chốt thực chi</button>' : ''}
          ${spCan('approver') && d.status === 'closed' && !d.isHistorical ? '<button class="btn btn-ghost btn-sm" type="button" data-rn-act="reopen"><i data-lucide="rotate-ccw"></i>Mở lại</button>' : ''}
          ${spCan('admin') ? '<button class="btn btn-ghost btn-sm" type="button" data-rn-act="delete"><i data-lucide="trash-2"></i>Xoá</button>' : ''}
        </div>
      </div>
      <dl class="sp-dl rn-scale">${scale.map(([l, v]) => `<div class="sp-dl-item"><dt>${l}</dt><dd>${v === null || v === undefined ? '—' : esc(v)}</dd></div>`).join('')}</dl>
      ${d.warnings.map(w => `<p class="criteria-status">${esc(w)}</p>`).join('')}
      <div class="rn-hist"><span class="muted-sub sr-inline">So sánh với:</span>
        ${d.historyAvailable.map(h => `<label class="rn-chip"><input type="checkbox" data-rn-hist value="${esc(h.id)}"${chosen.has(String(h.id)) ? ' checked' : ''}>${esc(h.label)}${h.config ? ` <span class="muted-sub sr-inline">${esc(h.config)}</span>` : ''}</label>`).join('') || '<span class="muted-sub sr-inline">chưa có dự án trước (nhập lịch sử hoặc chốt dự án cũ)</span>'}</div>
      ${rnQuotesBlock(d)}
      ${['cabling', 'network', 'vc'].map(ws => rnBlock(d, ws, ed)).join('')}
      ${ed ? '<div class="sp-inline"><span class="src-spacer"></span><button class="btn btn-primary btn-sm" type="button" data-rn-act="save-costs">Lưu số liệu</button></div>' : ''}
      <p class="src-result" id="rnMsg" role="status"></p>
    </section>`;
  refreshIcons();
}

function rnQuotesBlock(d) {
  const m = rnState.mapping;
  const reqs = d.quoteRequests.map(r => `<li><button class="btn btn-ghost btn-sm sp-link" type="button" data-rn-quote="${esc(r.code)}">${esc(r.code)}</button>
      ${esc(RN_WS[r.workstream] || r.workstream)} — ${esc(r.title)} · ${r.selectedVendor ? `chọn <b>${esc(r.selectedVendor)}</b> ${spMoney(r.selectedSubtotal)}` : 'chưa chọn NCC'}</li>`).join('');
  const groupsOf = (ws) => d.groups[ws].filter(g => g.active);
  const unmatched = m && m.unmatched.length ? `<div class="sp-h">Dòng báo giá chưa gắn nhóm chi phí</div>
    <div class="table-scroll"><table class="data-table"><thead><tr><th>Gói</th><th>Dòng</th><th class="num">Thành tiền</th><th>Nhóm</th></tr></thead><tbody>
    ${m.unmatched.map(u => `<tr><td>${esc(u.request)}</td><td class="wrap">${esc(u.description)}${u.model ? `<span class="muted-sub">${esc(u.model)}</span>` : ''}</td><td class="num">${rnMoney(u.amount)}</td>
      <td><select data-rn-line="${esc(u.lineId)}"${rnEditable() ? '' : ' disabled'}><option value="">— chọn —</option>${groupsOf(u.workstream).map(g => spOpt(g.id, g.name, '')).join('')}</select></td></tr>`).join('')}
    </tbody></table></div>` : '';
  return `<div class="sp-h">Gói báo giá của dự án</div>
    ${reqs ? `<ul class="sp-cc">${reqs}</ul>` : '<p class="muted-sub">Chưa có gói nào — tạo ở trang So sánh báo giá và chọn dự án này.</p>'}
    ${unmatched}
    ${rnEditable() && d.status !== 'closed' && d.quoteRequests.some(r => r.selectedVendor) ? '<button class="btn btn-ghost btn-sm" type="button" data-rn-act="pull"><i data-lucide="download"></i>Lấy báo giá trúng từ NCC được chọn</button>' : ''}`;
}

function rnBlock(d, ws, ed) {
  const c = d.comparison[ws], hist = c.history, main = c.mainKpi;
  const groups = d.groups[ws].filter(g => g.active || d.costs[String(g.id)]);
  const kinds = ['estimate', 'awarded', 'actual'];
  const canActual = spCan('approver') || d.isHistorical;
  const rows = groups.map(g => {
    const v = d.costs[String(g.id)] || {}, r = c.rows.find(x => x.groupId === g.id) || { history: [] };
    const cells = kinds.map(k => ed && (k !== 'actual' || canActual)
      ? `<td><input class="num rn-amt" inputmode="numeric" data-g="${esc(g.id)}" data-k="${k}" value="${v[k] ? esc(v[k].amount) : ''}"${v[k] && v[k].source !== 'manual' ? ` title="${esc(v[k].note || v[k].source)}"` : ''}></td>`
      : `<td class="num">${v[k] ? rnMoney(v[k].amount) : '—'}</td>`).join('');
    const dev = r.deviationPct;
    return `<tr><td class="wrap">${esc(g.name)}</td>${cells}${hist.map((h, i) => `<td class="num">${r.history[i] !== null && r.history[i] !== undefined ? rnMoney(r.history[i]) : '—'}</td>`).join('')}
      <td class="num">${dev !== null && dev !== undefined ? spPill(Math.abs(dev) > 15 ? 'warn' : 'ok', rnPct(dev)) : '—'}</td></tr>`;
  }).join('');
  const kpiRow = `<tr class="qt-foot"><td><b>Tổng</b><span class="muted-sub">${esc(RN_KPI[main])}</span></td><td colspan="3" class="num"><b>${rnMoney(c.total)}</b>${c.kinds.length ? `<span class="muted-sub">${esc(c.kinds.map(k => RN_KIND[k]).join(' + '))}</span>` : ''}<span class="muted-sub"><b>${c.kpi[main] !== null ? esc(Number(c.kpi[main]).toLocaleString('vi-VN')) : 'không có số liệu'}</b> ${esc(RN_KPI[main])}</span></td>
    ${hist.map(h => `<td class="num"><b>${rnMoney(h.total)}</b><span class="muted-sub">${h.kpi[main] !== null ? esc(Number(h.kpi[main]).toLocaleString('vi-VN')) : 'không có số liệu'}</span></td>`).join('')}
    <td class="num">${c.deviationPct !== null ? `<b>${esc(rnPct(c.deviationPct))}</b><span class="muted-sub">so trung vị ${esc(Number(c.median).toLocaleString('vi-VN'))}</span>` : '—'}</td></tr>`;
  return `<div class="rn-block">
    <div class="sp-h">${esc(RN_WS[ws])}</div>
    ${c.alerts.map(a => `<p class="criteria-status${a.level === 'crit' ? ' error' : ''}">${esc(a.message)}</p>`).join('')}
    <div class="table-scroll"><table class="data-table rn-table">
      <thead><tr><th>Nhóm chi phí</th>${kinds.map(k => `<th class="num">${RN_KIND[k]}</th>`).join('')}
        ${hist.map(h => `<th class="num">${esc(h.label)}${h.config ? `<span class="muted-sub">${esc(h.config)}${h.sameConfig ? '' : ' · khác cấu hình'}</span>` : ''}</th>`).join('')}<th class="num">So trung vị</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="${5 + hist.length}" class="empty-note">Chưa có nhóm chi phí.</td></tr>`}</tbody>
      <tfoot>${kpiRow}</tfoot>
    </table></div></div>`;
}

function rnMsg(text, ok) {
  const el = $('#rnMsg');
  if (!el) { if (text && !ok) uiAlert(text); return; }
  el.classList.add('src-result');
  uiSetStatus(el, text, text ? (ok ? 'ok' : 'error') : '');
}

async function rnAction(act, btn) {
  const d = rnState.current;
  const run = async (fn, ok) => {
    btn.disabled = true;
    try { await fn(); await rnOpen(d.code); if (ok) rnMsg(ok, true); } catch (err) { rnMsg(err.message); } finally { btn.disabled = false; }
  };
  if (act === 'back') { rnState.current = null; rnState.history = null; return renderRenovationsPage(); }
  if (act === 'new') return rnOpenForm();
  if (act === 'edit') return rnOpenForm(d);
  if (act === 'import') return rnOpenImport();
  if (act === 'groups') return rnOpenGroups();
  if (act === 'xlsx') return qtDownload(rnUrl(d.code, 'export.xlsx'), `cai-tao-${d.floor}.xlsx`, btn);
  if (act === 'pdf') return qtDownload(rnUrl(d.code, 'export.pdf'), `cai-tao-${d.floor}.pdf`, btn);
  if (act === 'pull') return run(() => apiRequest(rnUrl(d.code, 'pull-quotes'), { method: 'POST' }), 'Đã lấy báo giá trúng theo nhóm chi phí.');
  if (act === 'close') {
    if (!await uiConfirm('Chốt thực chi? Nhóm chưa có thực chi sẽ lấy theo báo giá trúng; dự án trở thành dữ liệu lịch sử cho các tầng sau.')) return;
    return run(() => apiRequest(rnUrl(d.code, 'close'), { method: 'POST' }), 'Đã chốt.');
  }
  if (act === 'reopen') return run(() => apiRequest(rnUrl(d.code, 'reopen'), { method: 'POST' }));
  if (act === 'delete') {
    if (!await uiConfirm(`Xoá dự án ${d.code} và toàn bộ số liệu?`)) return;
    try { await apiRequest(rnUrl(d.code), { method: 'DELETE' }); rnState.current = null; renderRenovationsPage(); } catch (err) { rnMsg(err.message); }
    return;
  }
  if (act === 'save-costs') {
    const costs = $$('.rn-amt').map(i => ({ groupId: Number(i.dataset.g), kind: i.dataset.k, amount: i.value.trim() || null }));
    const lines = $$('[data-rn-line]').filter(s => s.value);
    return run(async () => {
      for (const s of lines) await apiRequest(rnUrl(d.code, 'line-group'), { method: 'PUT', body: JSON.stringify({ lineId: Number(s.dataset.rnLine), groupId: Number(s.value) }) });
      await apiRequest(rnUrl(d.code, 'costs'), { method: 'PUT', body: JSON.stringify({ costs }) });
    }, 'Đã lưu.');
  }
}

async function rnOpenForm(p) {
  const v = p || { year: new Date().getFullYear() };
  const n = (name, label, val) => `<label class="field"><span>${label}</span><input name="${name}" type="number" min="0" value="${esc(val ?? '')}"></label>`;
  const body = `<form id="rnForm" class="sp-form" novalidate><div class="src-grid">
      <label class="field sp-span2"><span>Tên dự án *</span><input name="name" maxlength="255" value="${esc(v.name || '')}" placeholder="Vd: Cải tạo văn phòng tầng 9"></label>
      <label class="field"><span>Tầng *</span><input name="floor" maxlength="32" value="${esc(v.floor || '')}" placeholder="FL9"></label>
      <label class="field"><span>Năm *</span><input name="year" type="number" min="2000" max="2100" value="${esc(v.year || '')}"></label>
      ${n('nodes', 'Số node mạng', v.nodes)}${n('headcount', 'Số nhân sự (NS)', v.headcount)}
      ${n('meetingRooms', 'Số phòng họp', v.meetingRooms)}${n('vcRooms', 'Phòng họp có VC', v.vcRooms)}
      ${n('hubRooms', 'Số phòng hub', v.hubRooms)}${n('accessPoints', 'Số access point', v.accessPoints)}
      ${n('areaM2', 'Diện tích (m²)', v.areaM2)}
      <label class="field"><span>Cấu hình (cách nhau dấu phẩy)</span><input name="configTags" maxlength="255" value="${esc(v.configTags || '')}" placeholder="10G, Wifi7"></label>
      <label class="field"><span>Dự án Capex</span><input name="capexCode" maxlength="20" value="${esc((v.capex && v.capex.code) || '')}"></label>
      ${p ? `<label class="field"><span>Trạng thái</span><select name="status"${p.status === 'closed' ? ' disabled' : ''}>${spOpt('planning', 'Lập kế hoạch', p.status)}${spOpt('executing', 'Đang thi công', p.status)}${p.status === 'closed' ? spOpt('closed', 'Đã chốt', 'closed') : ''}</select></label>` : ''}
      <label class="field sp-span2"><span>Ghi chú</span><textarea name="notes" rows="2" maxlength="5000">${esc(v.notes || '')}</textarea></label>
    </div></form><p class="muted-sub">Quy mô dùng để tính chi phí / node và / phòng họp VC; cấu hình dùng để chỉ so với các tầng cùng loại.</p>`;
  spDialog(p ? `Sửa ${esc(p.code)}` : 'Tạo dự án cải tạo', body, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
    <button class="btn btn-primary btn-sm" type="button" id="rnSave">${p ? 'Lưu' : 'Tạo'}</button>`);
  spOnce('#rnSave', async () => {
    const fd = Object.fromEntries(new FormData($('#rnForm')).entries());
    if (p) { fd.rowVersion = p.rowVersion; fd.isHistorical = p.isHistorical; if (!fd.status) fd.status = p.status; }
    try {
      const d = await apiRequest(p ? rnUrl(p.code) : '/spend/renovations', { method: p ? 'PUT' : 'POST', body: JSON.stringify(fd) });
      $('#spDialog').close(); await rnOpen(d.code);
    } catch (err) { spMsg(err.message); }
  });
}

async function rnOpenImport() {
  spDialog('Nhập lịch sử từ file so sánh (Summarize)', `
    <p class="panel-sub">Chọn file Excel theo bố cục đang dùng (khối I / II / III, bảng “SO SÁNH CHI PHÍ … FL6 - FL12 - FL3”). Hệ thống đọc các cột tầng, quy mô trong tiêu đề cột (“(62 nodes) - 24 NS”, “(10G &amp; Wifi7)”) và số theo nhóm chi phí; xem trước rồi mới lưu.</p>
    <div class="sp-inline"><input type="file" id="rnFile" accept=".xlsx"><button class="btn btn-ghost btn-sm" type="button" id="rnPreview">Xem trước</button></div>
    <div id="rnPrev"></div>`, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Đóng</button>
    <button class="btn btn-primary btn-sm" type="button" id="rnApply" disabled>Lưu các dự án đã chọn</button>`);
  $('#spDialog').classList.add('qt-wide');
  $('#spDialog').addEventListener('close', () => $('#spDialog').classList.remove('qt-wide'), { once: true });
  const send = async (apply) => {
    const f = $('#rnFile').files[0];
    if (!f) throw new Error('Chọn file .xlsx.');
    const fd = new FormData(); fd.append('file', f); if (apply) fd.append('apply', JSON.stringify(apply));
    return apiRequest('/spend/renovations/import', { method: 'POST', body: fd });
  };
  spOnce('#rnPreview', async () => {
    try {
      const r = await send(null);
      const year = new Date().getFullYear();
      $('#rnPrev').innerHTML = `${r.message ? `<p class="criteria-status error">${esc(r.message)}</p>` : ''}
        ${r.findings.length ? `<div class="sp-h">Phát hiện trong file</div><ul class="qt-findings">${r.findings.map(f => `<li class="warn"><b>${esc(f.code)}</b> ${esc(f.message)}</li>`).join('')}</ul>` : ''}
        <div class="table-scroll"><table class="data-table"><thead><tr><th>Nhập</th><th>Tầng</th><th>Năm *</th><th>Loại số liệu</th><th>Quy mô / cấu hình</th><th class="num">Cabling</th><th class="num">Network</th><th class="num">VC</th></tr></thead><tbody>
        ${r.projects.map(p => `<tr data-rn-imp="${esc(p.floor)}"><td><input type="checkbox" class="rn-inc" checked></td><td><b>${esc(p.floor)}</b>${p.existing.length ? `<span class="muted-sub">đã có: ${esc(p.existing.map(e => e.year).join(', '))}</span>` : ''}</td>
          <td><input type="number" class="rn-year" min="2000" max="2100" value="${esc((p.existing[0] && p.existing[0].year) || year - 1)}"></td>
          <td><select class="rn-kind">${spOpt('actual', 'Thực chi', 'actual')}${spOpt('awarded', 'Báo giá trúng', '')}${spOpt('estimate', 'Dự toán', '')}</select></td>
          <td class="wrap">${esc([p.nodes ? `${p.nodes} node` : '', p.headcount ? `${p.headcount} NS` : '', p.vcRooms ? `${p.vcRooms} phòng VC` : '', p.configTags || ''].filter(Boolean).join(' · ') || '—')}</td>
          ${['cabling', 'network', 'vc'].map(ws => `<td class="num">${p.blocks[ws] ? rnMoney(p.blocks[ws].total) : '—'}</td>`).join('')}</tr>`).join('')}
        </tbody></table></div>
        <p class="muted-sub">Mỗi dòng của file được ghép vào nhóm chi phí chuẩn; nhãn chưa có sẽ tạo nhóm mới. Nhập lại cùng tầng / năm sẽ cập nhật số liệu.</p>`;
      $('#rnApply').disabled = !r.projects.length;
    } catch (err) { spMsg(err.message); }
  });
  spOnce('#rnApply', async () => {
    const projects = $$('[data-rn-imp]').map(tr => ({ floor: tr.dataset.rnImp, include: tr.querySelector('.rn-inc').checked,
      year: Number(tr.querySelector('.rn-year').value), kind: tr.querySelector('.rn-kind').value }));
    try {
      const r = await send({ projects });
      spMsg(`Đã tạo ${r.created.length}, cập nhật ${r.updated.length} dự án.`, true);
      if (!rnState.current) { rnState.list = await apiRequest('/spend/renovations'); rnRenderList(); }
    } catch (err) { spMsg(err.message); }
  });
}

async function rnOpenGroups() {
  let g;
  try { g = await apiRequest('/spend/renovation-groups'); } catch (err) { uiAlert(err.message); return; }
  const row = (x = {}) => `<tr data-grp="${esc(x.id || '')}"><td><input class="rn-gname" maxlength="255" value="${esc(x.name || '')}"></td>
    <td><input class="rn-galias" value="${esc((x.aliases || []).join('; '))}" placeholder="từ khoá trên báo giá, cách nhau ;"></td>
    <td><input type="checkbox" class="rn-gactive"${x.active === false ? '' : ' checked'}></td></tr>`;
  const body = ['cabling', 'network', 'vc'].map(ws => `<div class="sp-h">${esc(RN_WS[ws])}</div>
    <div class="table-scroll"><table class="data-table qt-lines"><thead><tr><th>Nhóm chi phí</th><th>Tên gọi khác (để ghép dòng báo giá)</th><th>Dùng</th></tr></thead>
    <tbody data-grp-ws="${ws}">${g[ws].map(row).join('')}</tbody></table></div>
    <button class="btn btn-ghost btn-sm" type="button" data-grp-add="${ws}">+ Thêm nhóm</button>`).join('');
  spDialog('Nhóm chi phí chuẩn', body, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Đóng</button>
    <button class="btn btn-primary btn-sm" type="button" id="grpSave">Lưu</button>`);
  $('#spDialogBody').onclick = (e) => {
    const add = e.target.closest('[data-grp-add]');
    if (add) $(`[data-grp-ws="${add.dataset.grpAdd}"]`).insertAdjacentHTML('beforeend', row());
  };
  spOnce('#grpSave', async () => {
    const payload = {};
    ['cabling', 'network', 'vc'].forEach(ws => {
      payload[ws] = $$(`[data-grp-ws="${ws}"] tr`).filter(tr => tr.querySelector('.rn-gname').value.trim()).map(tr => ({
        id: tr.dataset.grp ? Number(tr.dataset.grp) : null, name: tr.querySelector('.rn-gname').value.trim(),
        aliases: tr.querySelector('.rn-galias').value.split(';').map(s => s.trim()).filter(Boolean), active: tr.querySelector('.rn-gactive').checked }));
    });
    try { await apiRequest('/spend/renovation-groups', { method: 'PUT', body: JSON.stringify(payload) }); spMsg('Đã lưu.', true); }
    catch (err) { spMsg(err.message); }
  });
}
