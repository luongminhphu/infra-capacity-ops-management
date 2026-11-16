/* Backup schedule & nightly compliance — moved out of backup.js (audit FE-29). */

/* ---------- Backup schedule & nightly compliance ---------- */
const BK_DAYS = ['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN'];
const BK_NIGHT = {
  ok: { cls: 'ok', sym: '✓', label: 'Đạt' }, partial: { cls: 'warn', sym: '½', label: 'Một phần' },
  late: { cls: 'warn', sym: 'T', label: 'Trễ' }, wrongtype: { cls: 'warn', sym: 'S', label: 'Sai loại' },
  failed: { cls: 'crit', sym: '✕', label: 'Lỗi' }, missing: { cls: 'crit', sym: '!', label: 'Thiếu' },
  running: { cls: 'na', sym: '…', label: 'Đang chạy' }, pending: { cls: 'na', sym: '·', label: 'Chưa đến lịch' },
};
const bkDaysTxt = (arr) => (arr || []).length === 7 ? 'Hằng ngày' : (arr || []).map(d => BK_DAYS[d]).join(', ') || '—';

function bkSchedule(s) {
  const st = bkState();
  const sc = s.schedule || { rules: [], nights: [] };
  const nights = sc.nights || [];
  const counts = {};
  (sc.rules || []).forEach(r => Object.entries(r.counts || {}).forEach(([k, v]) => { counts[k] = (counts[k] || 0) + v; }));
  const nbN = (sc.rules || []).filter(r => r.source === 'netbackup').length;
  const fxN = (sc.rules || []).filter(r => r.source === 'fixed').length;
  const manN = (sc.rules || []).filter(r => r.source === 'manual').length;
  const head = nights.map(d => { const dt = new Date(d + 'T00:00:00'); const wd = (dt.getDay() + 6) % 7;
    return `<th class="bk-night-h" title="Đêm ${d}">${BK_DAYS[wd]}<span class="muted-sub">${d.slice(8, 10)}/${d.slice(5, 7)}</span></th>`; }).join('');
  const rows = (sc.rules || []).map(r => {
    const cells = r.nights.map(c => {
      if (!c.status) return `<td class="bk-night ${c.expected ? '' : 'off'}" title="${esc(c.detail || 'Không có lịch')}">${c.expected ? '<span class="muted">?</span>' : ''}</td>`;
      const m = BK_NIGHT[c.status];
      const tip = `${c.date} · ${c.expected === 'full' ? 'Full' : c.expected === 'any' ? 'Khung cố định' : 'Incremental'} · ${m.label}${c.detail ? ' — ' + c.detail : ''}`;
      return `<td class="bk-night" title="${esc(tip)}"><span class="bk-cell ${m.cls} ${c.expected === 'full' ? 'is-full' : ''}">${m.sym}</span></td>`;
    }).join('');
    const src = `<span class="tag">${r.source === 'netbackup' ? 'Từ policy' : r.source === 'fixed' ? 'Khung cố định' : 'Thủ công'}</span>`;   // a category, not a status
    const pols = (r.source === 'netbackup' || r.source === 'fixed' ? '' : `<span class="muted-sub bk-wrap">${r.policies.length ? esc(r.policies.join(', ')) : '<b class="tone-warn">Chưa gán policy</b>'}</span>`)
      + (r.scheduleNotes || []).map(n => `<span class="muted-sub bk-wrap tone-warn">${esc(n)}</span>`).join('')
      + (r.calendar && Object.keys(r.calendar).length ? `<span class="muted-sub">Lịch theo ngày trong tháng: ${Object.entries(r.calendar).map(([k, c]) => `${k === 'full' ? 'Full' : 'Incr'} ngày ${[...(c.monthDays || []), ...(c.lastDayOfMonth ? ['cuối tháng'] : [])].join(', ') || (c.dates || []).join(', ')}`).join(' · ')}</span>` : '');
    const full = r.source === 'fixed' ? 'Mỗi đêm (không xét Full / Incr)' : r.fullEveryDays ? `Full mỗi ${r.fullEveryDays} ngày` : `Full ${bkDaysTxt(r.fullDays)}`;
    const extra = [r.retentionIssues && r.retentionIssues.length ? `${uiPill('warn', 'Retention lệch', r.retentionIssues.join('; '))}` : ''].join('');
    return `<tr><td><b>${esc(r.system)}</b> ${src}${pols}</td>
      <td class="wrap">${full}<span class="muted-sub">${r.source === 'fixed' ? '' : 'Incr ' + bkDaysTxt(r.incrDays) + ' · '}${esc(r.startTime)}→${esc(r.endTime)}${r.retentionDays ? ' · giữ Full ' + r.retentionDays + ' ngày' : ''}</span></td>
      ${cells}<td class="num"><b class="${r.compliancePct === null ? '' : r.compliancePct >= 95 ? 'tone-ok' : r.compliancePct >= 80 ? 'tone-warn' : 'tone-crit'}">${bkPct(r.compliancePct)}</b>
      <span class="muted-sub">${r.counts.ok || 0}/${r.evaluated} đêm</span></td><td class="num">${r.rpoHours ? bkNum(r.rpoHours) + ' giờ' : '—'}</td><td>${extra}</td></tr>`;
  }).join('');
  const legend = Object.entries(BK_NIGHT).map(([k, m]) => `<span class="bk-legend-item"><span class="bk-cell ${m.cls}">${m.sym}</span>${m.label}${counts[k] ? ` (${counts[k]})` : ''}</span>`).join('')
    + '<span class="bk-legend-item"><span class="bk-cell ok is-full">✓</span>Đêm Full (viền đậm)</span>';
  const unm = (sc.unmappedPolicies || []);
  return `
    <div class="kpi-strip-wrap">${uiKpiStrip([
      { label: 'Tuân thủ lịch', valueHtml: `<span class="${sc.compliancePct === null ? '' : sc.compliancePct >= 95 ? '' : sc.compliancePct >= 80 ? 'tone-warn' : 'tone-crit'}">${bkPct(sc.compliancePct)}</span>`, sub: `${counts.ok || 0} / ${sc.evaluated || 0} đêm đạt trong ${nights.length} đêm gần nhất` },
      { label: 'Lỗi / thiếu', valueHtml: `<span class="${(counts.failed || 0) + (counts.missing || 0) ? 'tone-crit' : ''}">${bkNum((counts.failed || 0) + (counts.missing || 0))}</span>`, sub: `${counts.failed || 0} đêm có client lỗi · ${counts.missing || 0} đêm không chạy` },
      { label: 'Trễ / sai loại', valueHtml: `<span class="${(counts.late || 0) + (counts.wrongtype || 0) ? 'tone-warn' : ''}">${bkNum((counts.late || 0) + (counts.wrongtype || 0))}</span>`, sub: `Kết thúc sau giờ kết thúc + ${sc.graceMinutes} phút · đêm Full không chạy Full` },
      { label: 'Nguồn lịch', value: bkNum((sc.rules || []).length), sub: `${sc.mode === 'fixed' ? 'Chế độ khung cố định' : 'Chế độ đọc từ NetBackup'} · ${nbN} theo NetBackup · ${fxN} theo khung ${sc.fixedWindow || ''} · ${manN} thủ công / Excel` },
    ])}</div>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Tuân thủ lịch theo từng đêm</div>
        <p class="panel-sub">Mỗi đêm backup tính từ ${sc.cutoffHour}:00 hôm đó đến trước ${sc.cutoffHour}:00 hôm sau — job chạy qua nửa đêm (20:00 → 04:00) thuộc đêm bắt đầu. Đạt = mọi client có bản backup thành công, đúng loại (Full / Incremental), xong trước giờ kết thúc + ${sc.graceMinutes} phút. Di chuột lên ô để xem chi tiết.</p></div></div>
      <div class="bk-legend">${legend}</div>
      <div class="table-scroll"><table class="data-table bk-sched-table"><thead><tr><th>Hệ thống / policy</th><th>Lịch</th>${head}<th>Tuân thủ</th><th>RPO</th><th></th></tr></thead>
      <tbody>${rows || `<tr><td colspan="${nights.length + 5}" class="empty-note">Chưa có lịch nào — đồng bộ NetBackup (lịch đọc tự động từ policy) hoặc khai báo lịch bên dưới.</td></tr>`}</tbody></table></div>
      ${unm.length ? `<div class="row-count">Policy chưa có lịch (không có schedule Full / Incremental có cửa sổ, hoặc đã tắt tự đọc): ${esc(unm.slice(0, 20).join(', '))}${unm.length > 20 ? '…' : ''}</div>` : ''}
    </section>
    ${bkScheduleEditor()}`;
}

function bkScheduleEditor() {
  const st = bkState();
  const ed = st.sched;
  if (!ed) {
    return `<section class="table-card"><div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Lịch thủ công / import Excel</div>
      <p class="panel-sub">Mặc định lịch được đọc tự động từ schedule của từng policy NetBackup. Khai báo thủ công khi muốn gom nhiều policy thành một hệ thống, hoặc áp lịch đã duyệt khác với cấu hình NetBackup — lịch thủ công được ưu tiên cho các policy nó bao gồm.</p></div>
      ${uiIsAdmin() ? `<div class="panel-toolbar"><button class="btn btn-secondary btn-sm" type="button" data-bk-action="sched-open"><i data-lucide="calendar-cog"></i>Quản lý lịch thủ công</button></div>` : `<span class="muted-sub">${ADMIN_ONLY_NOTE}</span>`}</div></section>`;
  }
  const dayBoxes = (i, field, days) => BK_DAYS.map((d, k) => `<label class="bk-day"><input type="checkbox" data-sched="${i}" data-field="${field}" data-day="${k}" ${days.includes(k) ? 'checked' : ''}/>${d}</label>`).join('');
  const rows = ed.rules.map((r, i) => `<tr>
    <td><input type="text" data-sched="${i}" data-field="system" value="${esc(r.system)}" placeholder="Tên hệ thống"/></td>
    <td><input type="text" data-sched="${i}" data-field="policyPatterns" value="${esc(r.policyPatterns || '')}" placeholder="vd P-CORE-EQ-*, VM-EQUITY"/>
      <span class="muted-sub bk-wrap">${(r.policies || []).length ? esc(r.policies.join(', ')) : '<b class="tone-warn">Chưa khớp policy nào</b>'}</span></td>
    <td class="bk-days">${dayBoxes(i, 'fullDays', r.fullDays)}</td><td class="bk-days">${dayBoxes(i, 'incrDays', r.incrDays)}</td>
    <td><input type="text" class="bk-hhmm" data-sched="${i}" data-field="startTime" value="${esc(r.startTime)}" placeholder="20:00" maxlength="5"/></td>
    <td><input type="text" class="bk-hhmm" data-sched="${i}" data-field="endTime" value="${esc(r.endTime)}" placeholder="04:00" maxlength="5"/></td>
    <td><input type="number" min="0" data-sched="${i}" data-field="retentionDays" value="${r.retentionDays ?? ''}" style="width:70px"/></td>
    <td><input type="checkbox" data-sched="${i}" data-field="enabled" ${r.enabled ? 'checked' : ''}/></td>
    <td><button class="btn btn-ghost btn-sm" type="button" data-bk-action="sched-del" data-i="${i}">Xoá</button></td></tr>`).join('');
  return `<section class="table-card">
    <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Lịch thủ công / import Excel</div>
      <p class="panel-sub">Cột “Policy”: tên policy NetBackup thuộc hệ thống, phân cách bằng dấu phẩy, dùng * cho mẫu tên. Import file theo mẫu HSC-Backup-Management (tên hệ thống, Monday…Sunday, Full Backup, Start time / End Time, Retention): hệ thống đã có giữ nguyên phần gán policy.</p></div>
      <div class="panel-toolbar">
        <label class="btn btn-secondary btn-sm"><i data-lucide="upload"></i>Import Excel<input type="file" accept=".xlsx" data-bk-action="sched-import" hidden/></label>
        <button class="btn btn-secondary btn-sm" type="button" data-bk-action="sched-add">Thêm hệ thống</button>
        <button class="btn btn-ghost btn-sm" type="button" data-bk-action="sched-close">Đóng</button>
        <button class="btn btn-primary btn-sm" type="button" data-bk-action="sched-save">Lưu lịch</button>
      </div></div>
    ${st.schedMsg ? `<p class="logo-upload-hint">${esc(st.schedMsg)}</p>` : ''}
    <div class="table-scroll"><table class="data-table bk-sched-edit"><thead><tr><th>Hệ thống</th><th>Policy</th><th>Ngày Full</th><th>Ngày Incremental</th><th>Bắt đầu</th><th>Kết thúc</th><th>Giữ Full (ngày)</th><th>Bật</th><th></th></tr></thead>
    <tbody>${rows || uiEmptyRow(9, 'Chưa có lịch thủ công — đang dùng lịch đọc từ NetBackup.')}</tbody></table></div>
    ${ed.unmappedPolicies && ed.unmappedPolicies.length ? `<div class="row-count">Policy chưa thuộc lịch thủ công nào: ${esc(ed.unmappedPolicies.slice(0, 30).join(', '))}</div>` : ''}
  </section>`;
}

async function bkScheduleOpen() {
  const st = bkState();
  try { st.sched = await api.bkSchedules(); st.schedMsg = ''; }
  catch (err) { bkSetStatus(`Không tải được lịch: ${err.message}`, true); return; }
  renderBackupBody();
}

function bkScheduleCollect() {
  const st = bkState();
  return st.sched.rules.map(r => ({ system: r.system, policyPatterns: r.policyPatterns || '', fullDays: r.fullDays, incrDays: r.incrDays,
    startTime: r.startTime, endTime: r.endTime, retentionDays: r.retentionDays === '' ? null : r.retentionDays, enabled: r.enabled, note: r.note || null }));
}
