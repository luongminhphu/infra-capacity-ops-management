/* Đợt thanh toán (Hợp đồng & thanh toán): list → detail of payments; moved out of home.js (audit FE-35).
   Loaded after home.js; opens contract details from the lazy spend module via uiRequire('spend'). */

/* ---------------------------------------------------------------- Hợp đồng & thanh toán → Đợt thanh toán */
/* List → detail (pages/danh-sach.md): payments grouped by due date with a total per group; selecting one opens its
   contract (where documents are ticked and payment recorded) beside the list, with that payment expanded. */
const payListState = { status: 'open', selected: null, data: null, bound: false };
const PAY_VIEWS = [['open', 'Chưa thanh toán'], ['paid', 'Đã thanh toán'], ['all', 'Tất cả']];

function payBuckets(items) {
  const today = new Date(new Date().toDateString());
  const days = (d) => Math.round((new Date(d) - today) / 86400000);
  const key = (p) => p.status === 'paid' ? 'paid' : !p.dueDate ? 'nodue' : days(p.dueDate) < 0 ? 'overdue' : days(p.dueDate) <= 30 ? 'd30' : days(p.dueDate) <= 90 ? 'd90' : 'later';
  return [['overdue', 'Quá hạn'], ['d30', 'Trong 30 ngày'], ['d90', '31–90 ngày'], ['later', 'Sau 90 ngày'], ['nodue', 'Chưa có hạn'], ['paid', 'Đã thanh toán']]
    .map(([k, label]) => { const g = items.filter(p => key(p) === k); return { label, items: g, sum: g.reduce((t, p) => t + (Number(p.amount) || 0), 0) }; })
    .filter(g => g.items.length);
}

async function renderPaymentsPage(keepPane) {
  const mount = $('#spendMount');
  let d;
  try { d = await apiRequest(`/spend/payments?status=${payListState.status}`); }
  catch (err) { mount.innerHTML = `${uiError(err.message)}`; return; }
  payListState.data = d;
  const pid = (p) => `${p.contractCode}:${p.seq}`;
  if (!d.items.some(p => pid(p) === payListState.selected)) payListState.selected = d.items.length ? pid(d.items[0]) : null;
  const row = (p) => `<li class="sp-li" role="option" id="pay-opt-${esc(pid(p).replace(/[^\w-]/g, '_'))}" data-pay-pick="${esc(pid(p))}" aria-selected="${pid(p) === payListState.selected}">
      <span class="hm-lv lv-${esc(p.status === 'paid' ? 'ok' : p.tone)}" aria-hidden="true"></span>
      <span class="sp-li-t">Đợt ${esc(p.seq)} · ${esc(p.title)}</span>
      <span class="sp-li-r"><b class="num">${uiMoney(p.amount, 'VND', 3)}</b>${p.status === 'paid' ? esc(p.statusLabel) : esc(p.dueLabel)}</span>
      <span class="sp-li-m">${esc(p.contractCode)} · ${esc(p.vendor || 'chưa có NCC')}${p.dueDate ? ` · hạn ${esc(spDateSafe(p.dueDate))}` : ''} · hồ sơ ${esc(p.docsDone)}/${esc(p.docsTotal)}</span>
      <span class="sr-only">${esc(p.statusLabel)}; ${esc(p.dueLabel || '')}</span></li>`;
  const groups = payBuckets(d.items).map(g => `<li class="hm-group" role="presentation"><span>${esc(g.label)}</span><span class="num">${uiMoney(g.sum, 'VND', 3)}</span></li>${g.items.map(row).join('')}`).join('');
  const overdue = d.items.filter(p => p.status !== 'paid' && p.tone === 'crit').length;
  const missing = d.items.filter(p => p.status === 'pending' && p.requiredMissing.length).length;
  const keepHtml = keepPane && $('#ctDetail') ? $('#ctDetail').innerHTML : null;
  mount.innerHTML = `
    <div class="kpi-strip-wrap">${uiKpiStrip([
      { label: 'Chưa thanh toán', valueHtml: payListState.status === 'paid' ? '—' : uiMoney(d.openTotal, 'VND', 3), sub: payListState.status === 'paid' ? 'Chọn “Chưa thanh toán” để xem' : `${d.items.filter(p => p.status !== 'paid').length} đợt theo bộ lọc` },
      { label: 'Quá hạn', valueHtml: `<span class="${overdue ? 'tone-crit' : ''}">${overdue}</span>`, sub: 'Đã qua hạn thanh toán, chưa ghi nhận' },
      { label: 'Thiếu hồ sơ bắt buộc', valueHtml: `<span class="${missing ? 'tone-warn' : ''}">${missing}</span>`, sub: 'Chưa nộp được hồ sơ' },
    ])}</div>
    <h2 class="sr-only">Danh sách đợt thanh toán</h2>
    <section class="hm-split panel sp-split" aria-label="Danh sách đợt thanh toán">
      <div class="hm-list">
        <div class="hm-toolbar"><div class="hm-views" role="group" aria-label="Chế độ xem">${PAY_VIEWS.map(([k, t]) => `<button class="hm-view" type="button" data-paylist="${k}" aria-pressed="${payListState.status === k}">${esc(t)}</button>`).join('')}</div></div>
        ${groups ? `<ul class="hm-rows" id="payList" role="listbox" tabindex="0" aria-label="Đợt thanh toán, nhóm theo hạn">${groups}</ul>` : uiEmpty('Không có đợt thanh toán nào. Thêm đợt trong chi tiết hợp đồng.')}
        <div class="hm-hint"><span>${d.items.length} đợt · sớm nhất trước</span><span><kbd>J</kbd> <kbd>K</kbd> chuyển</span></div>
      </div>
      <div class="hm-detail sp-detail" id="ctDetail" aria-live="polite" aria-labelledby="spPaneTitle">${keepHtml !== null ? keepHtml : d.items.length ? uiNote('Đang mở…') : uiEmpty('Chọn một đợt ở danh sách bên trái.')}</div>
    </section>`;
  refreshIcons();
  payBindList();
  if (!keepPane && payListState.selected && matchMedia('(min-width: 1024px)').matches) payOpenPick(payListState.selected);
}

/* Open the payment's contract in the pane (or dialog), with this payment expanded. */
function payOpenPick(key, focus) {
  const p = (payListState.data && payListState.data.items || []).find(x => `${x.contractCode}:${x.seq}` === key);
  if (!p) return;
  payListState.selected = key;
  uiListSelect('#payList', 'payPick', key, focus);
  uiRequire('spend').then(() => {
    if (typeof payState !== 'undefined' && p.id !== undefined) { payState.code = p.contractCode; payState.open = new Set([p.id]); payState.editing = null; }
    return ctOpenDetail(p.contractCode);
  }).then(() => uiListSelect('#payList', 'payPick', key, focus), (err) => uiAlert(err.message));
}

function payBindList() {
  const mount = $('#spendMount');
  mount.onclick = (e) => {
    if (spState.sub !== 'payments') return;
    const f = e.target.closest('[data-paylist]');
    if (f) { payListState.status = f.dataset.paylist; return renderPaymentsPage(); }
    const row = e.target.closest('[data-pay-pick]');
    if (row) payOpenPick(row.dataset.payPick);
  };
  mount.onkeydown = (e) => {
    if (spState.sub !== 'payments' || !e.target.closest('#payList')) return;
    uiListKeys(e, 'payPick', payListState.selected, payOpenPick);
  };
}
