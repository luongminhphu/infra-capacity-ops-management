/* =========================================================
   AI nâng cao (super app SA6): A7 nhận định sizing · A8 tóm tắt thay đổi hợp đồng ·
   A9 đọc hoá đơn (đối chiếu do hệ thống làm) · A10 Hỏi ICO · Kho báo cáo PDF.
   ========================================================= */

const AI_RISK = { high: ['crit', 'Rủi ro cao'], medium: ['warn', 'Trung bình'], low: ['ok', 'Thấp'] };
const AI_CHECK = { ok: ['ok', 'Khớp'], bad: ['crit', 'Lệch'], warn: ['warn', 'Cần xem'], na: ['na', 'Không đối chiếu'] };

/* ---------------------------------------------------------------- A7 Sizing */
async function aiSizingNarrative(btn) {
  await aiRunInto(btn, $('#sizingAiOut'), async () => {
    const payload = sizingExistingPayload();
    if (!payload.vms.length) return uiEmpty('Danh sách VM trống — nhập VM rồi bấm Tính sizing trước.');
    const s = await apiRequest('/sizing/ai-narrative', { method: 'POST', body: JSON.stringify(payload) });
    if (s.empty) return uiEmpty(s.headline);
    return `<div class="home-ai">
      <div class="home-brief-head"><span class="eyebrow">Nhận định sizing (AI)</span>${aiNote(s.cached)}</div>
      <p class="home-brief-text">${esc(s.headline)}</p>
      ${s.narrative.map(p => `<p>${esc(p)}</p>`).join('')}
      ${s.risks.length ? `<div class="sp-h">Rủi ro</div><ul class="home-list">${s.risks.map(r => `<li class="home-item"><div class="home-item-main">${uiPill(...(AI_LEVEL[r.level] || AI_LEVEL.info))} ${esc(r.text)}</div></li>`).join('')}</ul>` : ''}
      ${s.questions.length ? `<div class="sp-h">Câu hỏi cần làm rõ với khách hàng</div><ol class="home-ai-dec">${s.questions.map(q => `<li>${esc(q)}</li>`).join('')}</ol>` : ''}
      ${s.customerSummary ? `<div class="sp-h">Đoạn gửi khách hàng</div><blockquote class="ai-quote">${esc(s.customerSummary)}</blockquote>
        <button class="btn btn-ghost btn-sm" type="button" data-ai-copy="${esc(s.customerSummary)}">Chép đoạn này</button>` : ''}</div>`;
  });
}

/* ---------------------------------------------------------------- A8 Hợp đồng */
async function aiContractDiff(btn) {
  const code = btn.dataset.aiCode;
  const q = new URLSearchParams({ from: $('#ctFrom').value, to: $('#ctTo').value });
  await aiRunInto(btn, $('#ctAiDiff'), async () => {
    const s = await apiRequest(`/spend/contracts/${encodeURIComponent(code)}/diff/ai?${q}`, { method: 'POST' });
    if (s.empty) return uiEmpty(s.headline);
    const party = (typeof CT_PARTY !== 'undefined' && CT_PARTY) || {};
    return `<div class="home-ai">
      <div class="home-brief-head"><span class="eyebrow">Tóm tắt thay đổi v${esc(q.get('from'))} → v${esc(q.get('to'))} (AI)</span>${aiNote(s.cached)}</div>
      <p class="home-brief-text">${esc(s.headline)}</p>
      ${s.changes.length ? `<div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Rủi ro với bên mua</th><th>Điều khoản</th><th>Thay đổi</th><th>Bên sửa</th></tr></thead><tbody>
        ${s.changes.map(c => `<tr><td>${uiPill(...(AI_RISK[c.risk] || AI_RISK.low))}</td><td>${esc(c.topicLabel)}</td>
          <td class="wrap"><b>${esc(c.summary)}</b><span class="muted-sub bk-wrap">${esc(c.why)}</span></td><td>${esc(party[c.party] || c.party || '—')} · v${esc(c.version)}</td></tr>`).join('')}</tbody></table></div>` : ''}
      ${s.watch.length ? `<div class="sp-h">Cần đàm phán lại</div><ol class="home-ai-dec">${s.watch.map(x => `<li>${esc(x)}</li>`).join('')}</ol>` : ''}
      ${s.truncated ? '<p class="muted-sub">Chỉ 60 thay đổi đầu được gửi cho AI — xem đủ ở bảng so sánh bên dưới.</p>' : ''}</div>`;
  });
}

/* ---------------------------------------------------------------- A9 Hoá đơn */
async function aiInvoiceRead(btn) {
  await aiRunInto(btn, document.getElementById(btn.dataset.aiOut), async () => {
    const r = await apiRequest(btn.dataset.aiUrl, { method: 'POST' });
    const inv = r.invoice;
    const val = (v) => (typeof v === 'number' ? uiMoney(v, 'VND', 3) : esc(v ?? '—'));
    return `<div class="home-ai">
      <div class="home-brief-head"><span class="eyebrow">Đối chiếu hoá đơn ${esc(inv.invoiceSerial || '')} ${esc(inv.invoiceNo || '')}</span>
        <span class="muted-sub sr-inline">AI đọc hoá đơn${inv.cached ? ' · dùng lại' : ''}; hệ thống đối chiếu — tick hồ sơ sau khi kiểm tra</span></div>
      <p>${uiPill(r.ok ? 'ok' : 'warn', r.ok ? 'Không thấy lệch' : 'Có điểm cần xem')} ${esc(inv.sellerName || '')} · MST ${esc(inv.sellerTaxCode || '—')} · ngày ${esc(inv.invoiceDate || '—')}</p>
      <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Kiểm tra</th><th>Trên hoá đơn</th><th>Hệ thống</th><th>Kết quả</th></tr></thead><tbody>
        ${r.checks.map(c => `<tr><td>${esc(c.label)}</td><td class="num">${val(c.invoice)}</td><td class="num">${val(c.expected)}</td>
          <td>${uiPill(...(AI_CHECK[c.status] || AI_CHECK.na))}<span class="muted-sub">${esc(c.note)}</span></td></tr>`).join('')}</tbody></table></div></div>`;
  });
}

/* ---------------------------------------------------------------- A10 Hỏi ICO */
const askLog = [];
function askDialog() {
  let dlg = $('#askDlg');
  if (!dlg) {
    const ui = uiDialog('askDlg', { title: 'Hỏi ICO', className: 'ask-dlg' });
    dlg = ui.el;
    ui.body.innerHTML = `
      <p class="muted-sub">Hỏi bằng tiếng Việt, vd “VM nào của cluster PRD chưa có backup 3 ngày?”, “Cluster nào sắp vượt ngưỡng RAM?”.
        ICO chỉ đọc dữ liệu bạn được xem; tên host / IP / VM được che trước khi gửi AI.</p>
      <div class="ask-log" id="askLog" aria-live="polite"></div>
      <form class="ask-form" id="askForm"><input type="text" id="askInput" maxlength="500" autocomplete="off" placeholder="Nhập câu hỏi…" aria-label="Câu hỏi">
        <button class="btn btn-primary btn-sm" type="submit" id="askSend">Hỏi</button></form>`;
    $('#askForm', dlg).onsubmit = async (e) => {
      e.preventDefault();
      const input = $('#askInput', dlg), btn = $('#askSend', dlg);
      const q = input.value.trim();
      if (q.length < 3) return;
      btn.disabled = true;
      askLog.push({ q, pending: true });
      askRender();
      try {
        const r = await apiRequest('/ai/ask', { method: 'POST', body: JSON.stringify({ question: q }) });
        Object.assign(askLog[askLog.length - 1], { pending: false, answer: r.answer, links: r.links });
        input.value = '';
      } catch (err) { Object.assign(askLog[askLog.length - 1], { pending: false, error: err.message }); }
      finally { btn.disabled = false; askRender(); input.focus(); }
    };
    dlg.addEventListener('click', (e) => { if (e.target.closest('.entity-link')) dlg.close(); });
    refreshIcons();
  }
  askRender();
  dlg.showModal();
  $('#askInput', dlg).focus();
}

function askRender() {
  const log = $('#askLog');
  if (!log) return;
  log.innerHTML = askLog.slice(-10).map(x => `<div class="ask-q">${esc(x.q)}</div>
    <div class="ask-a">${x.pending ? '<span class="muted-sub">ICO đang tra cứu…</span>'
      : x.error ? `<span class="criteria-status error">${esc(x.error)}</span>`
      : `${esc(x.answer).replace(/\n/g, '<br>')}${(x.links || []).length ? `<div class="ask-links">${x.links.map(l => uiEntityLink(l.type, l.name, l.vcenterKey, false)).join(' · ')}</div>` : ''}`}</div>`).join('')
    || uiEmpty('Chưa có câu hỏi.');
  log.scrollTop = log.scrollHeight;
}

/* ---------------------------------------------------------------- Kho báo cáo */

(function bindArchiveToggle() {
  const cb = $('#rptArchive');
  if (cb) {
    cb.checked = reportArchiveOn();
    cb.addEventListener('change', () => uiSetPref('reportArchive', cb.checked));
  }
  // The header itself is added by uiDownload (ui.js); the server keeps only exports its registry marks archivable.
}());

const archState = { kind: '', mine: false };
async function renderArchiveView() {
  const mount = $('#archiveMount');
  if (!mount) return;
  let d;
  try { d = await apiRequest(`/report-archive?${new URLSearchParams({ ...(archState.kind ? { kind: archState.kind } : {}), mine: archState.mine ? 'true' : 'false' })}`); }
  catch (err) { mount.innerHTML = `<p class="criteria-status error">${esc(err.message)}</p>`; return; }
  const size = (b) => b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`;
  mount.innerHTML = `<section class="table-card">
    <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Kho báo cáo</div>
      <p class="panel-sub">Bản PDF đúng như lúc xuất. Bật “Lưu vào kho” ở <a href="#/docs/reports">Trung tâm báo cáo</a> để mỗi lần xuất được lưu một bản. Mỗi người giữ tối đa ${esc(d.maxPerUser)} bản, ${esc(d.retentionDays)} ngày.</p></div>
      <div class="panel-toolbar"><select id="archKind" aria-label="Loại báo cáo"><option value="">Mọi loại</option>${Object.entries(d.kinds).map(([k, l]) => `<option value="${esc(k)}"${archState.kind === k ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>
        <label class="finding-filter-item"><input type="checkbox" id="archMine" ${archState.mine ? 'checked' : ''}><span>Chỉ của tôi</span></label></div></div>
    <div class="table-scroll"><table class="data-table"><thead><tr><th>Lúc</th><th>Loại</th><th>Báo cáo</th><th>Tham số</th><th>Người xuất</th><th class="num">Dung lượng</th><th></th></tr></thead><tbody>
    ${d.items.map(x => `<tr><td>${esc(spTimeSafe(x.createdAt))}</td><td>${esc(x.kindLabel)}</td>
      <td class="wrap"><a href="/api/report-archive/${encodeURIComponent(x.id)}/file" download>${esc(x.filename)}</a></td>
      <td class="wrap muted-sub">${esc((x.params || '').replace(/&/g, ' · '))}</td><td>${esc(x.createdBy)}</td><td class="num">${esc(size(x.sizeBytes))}</td>
      <td>${x.createdBy === (currentUser && currentUser.username) || (currentUser && currentUser.isAdmin) ? `<button class="btn btn-ghost btn-sm btn-danger" type="button" data-arch-del="${esc(x.id)}" aria-label="Xoá ${esc(x.filename)}"><i data-lucide="trash-2"></i></button>` : ''}</td></tr>`).join('')
      || `<tr><td colspan="7">${uiEmpty('Chưa có báo cáo nào trong kho.')}</td></tr>`}
    </tbody></table></div></section>`;
  refreshIcons();
  $('#archKind').onchange = (e) => { archState.kind = e.target.value; renderArchiveView(); };
  $('#archMine').onchange = (e) => { archState.mine = e.target.checked; renderArchiveView(); };
  mount.querySelectorAll('[data-arch-del]').forEach(b => { b.onclick = async () => {
    if (!(await uiConfirm('Xoá bản báo cáo này khỏi kho?'))) return;
    try { await apiRequest(`/report-archive/${encodeURIComponent(b.dataset.archDel)}`, { method: 'DELETE' }); renderArchiveView(); }
    catch (err) { uiSetStatus('#archiveMount .panel-sub', err.message, 'error'); }
  }; });
}

/* ---------------------------------------------------------------- handlers */
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-ai-act]');
  if (b) {
    const act = b.dataset.aiAct;
    if (act === 'sizing') aiSizingNarrative(b);
    else if (act === 'contract-diff') aiContractDiff(b);
    else if (act === 'invoice') aiInvoiceRead(b);
    return;
  }
  const c = e.target.closest('[data-ai-copy]');
  if (c) {
    navigator.clipboard?.writeText(c.dataset.aiCopy).then(() => { c.textContent = 'Đã chép'; }).catch(() => {});
    return;
  }
  if (e.target.closest('#btnAsk')) askDialog();
});
