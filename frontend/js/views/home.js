/* =========================================================
   Trang chủ "Hôm nay cần làm" (bản tin + thẻ miền + hộp việc),
   Lịch việc & nhắc hạn, Đợt thanh toán, kết quả Quy hoạch
   trên màn hình — super app SA2 (docs/design/super-app.md §2.2).
   Items come from /api/inbox (filtered by role server-side).
   ========================================================= */

const homeState = { show: 'open', level: '', source: '', data: null, brief: null, bound: false, prefsLoaded: false };
const agState = { days: 120, kind: '', data: null, bound: false };
const HOME_LEVEL_TONE = { crit: 'crit', bad: 'bad', warn: 'warn', task: 'na', info: 'na' };
const HOME_SOURCE_PAGE = { capacity: 'dashboard', backup: 'bk-overview', license: 'lic-assess', spend: 'spend-overview' };
const AG_KIND = { opex: 'Opex hết hạn', payment: 'Đợt thanh toán', license: 'License hết hạn', quote: 'Báo giá hết hiệu lực' };

/* Link to a page, keeping a ?query (e.g. code=OPX-…) that the page reads from the hash. */
function homeLink(page, query) {
  const route = typeof PAGE_ROUTE !== 'undefined' ? PAGE_ROUTE[page] : null;
  return `#/${route ? route.path : page}${query ? `?${query}` : ''}`;
}

async function homeLoadPrefs() {
  if (homeState.prefsLoaded) return;
  homeState.prefsLoaded = true;
  try {
    const p = await apiRequest('/me/prefs');
    if (p.inboxFilter && typeof p.inboxFilter === 'object') {
      homeState.level = p.inboxFilter.level || '';
      homeState.source = p.inboxFilter.source || '';
    }
    if (p.agendaDays) agState.days = p.agendaDays;
  } catch (err) { /* defaults */ }
}

function homeSavePrefs() {
  apiRequest('/me/prefs', { method: 'PUT', body: JSON.stringify({ inboxFilter: { level: homeState.level, source: homeState.source }, agendaDays: agState.days }) })
    .catch(() => { /* preference only */ });
}

async function renderHomeView(fresh) {
  const mount = $('#homeMount');
  if (!mount) return;
  homeBind();
  await homeLoadPrefs();
  if (!homeState.data) mount.innerHTML = '<div class="empty-note">Đang tải…</div>';
  const [inbox, brief] = await Promise.allSettled([apiRequest(`/inbox?show=${homeState.show}${fresh ? '&fresh=true' : ''}`), apiRequest('/reports/brief')]);
  homeState.data = inbox.status === 'fulfilled' ? inbox.value : { items: [], counts: {}, sources: {}, unavailable: [], error: inbox.reason.message };
  homeState.brief = brief.status === 'fulfilled' ? brief.value : null;
  homeRender();
}

function homeRender() {
  const d = homeState.data, b = homeState.brief;
  const items = d.items.filter(i => (!homeState.level || i.level === homeState.level) && (!homeState.source || i.source === homeState.source));
  const cards = Object.entries(d.sources).map(([key, s]) => `<button class="home-card${homeState.source === key ? ' active' : ''}" type="button" data-home-source="${esc(key)}">
      <span class="home-card-label">${esc(s.label)}</span>
      <span class="home-card-state">${s.worst ? uiPill(HOME_LEVEL_TONE[s.worst], ({ crit: 'VƯỢT NGƯỠNG', bad: 'CẢNH BÁO', warn: 'THEO DÕI', task: 'Có việc chờ', info: 'Thông tin' })[s.worst]) : uiPill('ok', 'AN TOÀN')}</span>
      <span class="home-card-sub">${s.count ? `${esc(s.count)} mục cần xem` : 'Không có việc'}</span></button>`).join('');
  const brief = b && b.available ? `<div class="home-brief">
      <div class="home-brief-head">${b.overall ? uiPill(b.overall.cls, b.overall.text) : ''}<span class="eyebrow">Bản tin capacity</span></div>
      <p class="home-brief-text">${esc(b.headline || '')}</p>
      <button class="btn btn-ghost btn-sm" type="button" data-nav-link="executive">Xem tổng quan điều hành</button></div>` : '';
  const levels = [['', 'Tất cả'], ['crit', 'Vượt ngưỡng'], ['bad', 'Cảnh báo'], ['warn', 'Theo dõi'], ['task', 'Chờ xử lý']];
  const rows = items.map(i => `<li class="home-item lvl-${esc(i.level)}">
      <div class="home-item-main">${uiPill(HOME_LEVEL_TONE[i.level], i.levelLabel)} <span class="muted-sub sr-inline">${esc(i.sourceLabel)}</span>
        <a class="home-item-title" href="${esc(homeLink(i.page, i.query))}">${esc(i.title)}</a>
        ${i.detail ? `<p class="home-item-detail">${esc(i.detail)}</p>` : ''}</div>
      <div class="home-item-actions">${i.due ? `<span class="muted-sub sr-inline">${esc(spDateSafe(i.due))}</span>` : ''}
        ${i.ack ? `<span class="muted-sub sr-inline">${i.ack.state === 'done' ? 'Đã xong' : `Ẩn tới ${esc(spDateSafe(i.ack.until))}`}</span>
          <button class="btn btn-ghost btn-sm" type="button" data-home-ack="undo" data-key="${esc(i.key)}">Hiện lại</button>`
        : `<button class="btn btn-ghost btn-sm" type="button" data-home-ack="done" data-key="${esc(i.key)}" title="Đánh dấu đã xử lý">Xong</button>
          <button class="btn btn-ghost btn-sm" type="button" data-home-ack="snooze" data-key="${esc(i.key)}" title="Ẩn 7 ngày">Ẩn 7 ngày</button>`}</div></li>`).join('');
  $('#homeMount').innerHTML = `
    ${brief}
    <div class="home-cards">${cards}</div>
    <section class="panel">
      <div class="panel-head"><div><div class="panel-title">Hôm nay cần làm</div>
        <p class="panel-sub">Gom từ mọi module theo quyền của bạn: cảnh báo capacity / backup, license và Opex sắp hết hạn, đợt thanh toán đến hạn, báo giá chờ kiểm tra / chọn, tờ trình chờ duyệt.</p></div>
        <div class="panel-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-home-refresh title="Đọc lại từ mọi module"><i data-lucide="refresh-cw"></i>Làm mới</button><div class="view-toggle" role="tablist">${[['open', 'Cần xử lý'], ['done', 'Đã xong / đã ẩn']].map(([k, t]) => `<button type="button" class="${homeState.show === k ? 'active' : ''}" data-home-show="${k}">${t}</button>`).join('')}</div></div></div>
      <div class="sp-inline home-filters">${levels.map(([k, t]) => `<button class="btn btn-ghost btn-sm${homeState.level === k ? ' active' : ''}" type="button" data-home-level="${k}">${esc(t)}${k && d.counts[k] ? ` (${esc(d.counts[k])})` : ''}</button>`).join('')}
        ${homeState.source ? `<button class="btn btn-ghost btn-sm" type="button" data-home-source="">Bỏ lọc ${esc((d.sources[homeState.source] || {}).label || '')} ✕</button>` : ''}</div>
      ${d.error ? `<p class="criteria-status error">${esc(d.error)}</p>` : ''}
      ${d.unavailable.length ? `<p class="criteria-status">Tạm thời không đọc được: ${esc(d.unavailable.join(', '))}.</p>` : ''}
      ${rows ? `<ul class="home-list">${rows}</ul>` : uiEmpty(homeState.show === 'open' ? 'Không có việc cần xử lý theo bộ lọc này.' : 'Chưa có mục nào được đánh dấu.')}
    </section>`;
  refreshIcons();
}

const spDateSafe = (iso) => iso ? String(iso).slice(0, 10).split('-').reverse().join('/') : '—';

function homeBind() {
  if (homeState.bound) return;
  homeState.bound = true;
  $('#homeMount').addEventListener('click', async (e) => {
    const t = (s) => e.target.closest(s);
    let b;
    if (t('[data-home-refresh]')) return renderHomeView(true);
    if ((b = t('[data-home-show]'))) { homeState.show = b.dataset.homeShow; return renderHomeView(); }
    if ((b = t('[data-home-level]'))) { homeState.level = b.dataset.homeLevel; homeSavePrefs(); return homeRender(); }
    if ((b = t('[data-home-source]'))) {
      const s = b.dataset.homeSource;
      homeState.source = homeState.source === s ? '' : s; homeSavePrefs(); return homeRender();
    }
    if ((b = t('[data-home-ack]'))) {
      b.disabled = true;
      try {
        await apiRequest('/inbox/ack', { method: 'POST', body: JSON.stringify({ key: b.dataset.key, action: b.dataset.homeAck, days: 7 }) });
        await renderHomeView();
      } catch (err) { alert(err.message); b.disabled = false; }
    }
  });
}

/* ---------------------------------------------------------------- Lịch việc & nhắc hạn */
async function renderAgendaView() {
  const mount = $('#agendaMount');
  if (!mount) return;
  await homeLoadPrefs();
  if (!agState.bound) {
    agState.bound = true;
    mount.addEventListener('change', (e) => {
      const el = e.target.closest('[data-ag]');
      if (!el) return;
      if (el.dataset.ag === 'days') { agState.days = Number(el.value); homeSavePrefs(); renderAgendaView(); }
      else { agState.kind = el.value; agRender(); }
    });
  }
  try { agState.data = await apiRequest(`/agenda?days=${agState.days}`); }
  catch (err) { mount.innerHTML = `<p class="criteria-status error">${esc(err.message)}</p>`; return; }
  agRender();
}

function agRender() {
  const d = agState.data;
  const items = d.items.filter(i => !agState.kind || i.kind === agState.kind);
  const groups = [];
  items.forEach(i => {
    const label = i.daysLeft < 0 ? 'Đã quá hạn' : i.daysLeft <= 7 ? '7 ngày tới' : i.daysLeft <= 30 ? 'Trong 30 ngày'
      : `Tháng ${i.date.slice(5, 7)}/${i.date.slice(0, 4)}`;
    let g = groups.find(x => x.label === label);
    if (!g) { g = { label, items: [] }; groups.push(g); }
    g.items.push(i);
  });
  $('#agendaMount').innerHTML = `<section class="panel">
    <div class="panel-head"><div><div class="panel-title">Lịch việc & nhắc hạn</div>
      <p class="panel-sub">Mọi mốc có ngày: Opex hết hạn, đợt thanh toán, license hết hạn, báo giá hết hiệu lực — kể cả mốc đã quá hạn trong 90 ngày.</p></div>
      <div class="panel-toolbar">
        <select data-ag="kind">${spOptSafe('', 'Tất cả loại', agState.kind)}${Object.entries(AG_KIND).map(([k, t]) => spOptSafe(k, t, agState.kind)).join('')}</select>
        <select data-ag="days">${[30, 60, 120, 180, 365].map(n => spOptSafe(n, `${n} ngày tới`, agState.days)).join('')}</select></div></div>
    ${groups.length ? groups.map(g => `<div class="sp-h">${esc(g.label)}</div><ul class="home-list">${g.items.map(i => `<li class="home-item">
        <div class="home-item-main">${uiPill(i.tone, i.daysLeft < 0 ? `Quá ${-i.daysLeft} ngày` : i.daysLeft === 0 ? 'Hôm nay' : `Còn ${i.daysLeft} ngày`)}
          <span class="muted-sub sr-inline">${esc(spDateSafe(i.date))} · ${esc(AG_KIND[i.kind] || i.kind)}</span>
          <a class="home-item-title" href="${esc(homeLink(i.page, i.query))}">${esc(i.title)}</a>
          ${i.detail ? `<p class="home-item-detail">${esc(i.detail)}</p>` : ''}</div></li>`).join('')}</ul>`).join('')
      : uiEmpty('Không có mốc nào trong khoảng này.')}
  </section>`;
}

const spOptSafe = (value, label, sel) => `<option value="${esc(value)}"${String(sel) === String(value) ? ' selected' : ''}>${esc(label)}</option>`;

/* ---------------------------------------------------------------- Hợp đồng & thanh toán → Đợt thanh toán */
const payListState = { status: 'open' };
async function renderPaymentsPage() {
  const mount = $('#spendMount');
  let d;
  try { d = await apiRequest(`/spend/payments?status=${payListState.status}`); }
  catch (err) { mount.innerHTML = `<p class="criteria-status error">${esc(err.message)}</p>`; return; }
  mount.innerHTML = `<section class="table-card">
    <div class="panel-head"><div><div class="panel-title">Đợt thanh toán</div>
      <p class="panel-sub">Mọi đợt thanh toán của mọi hợp đồng, sớm nhất trước. Mở hợp đồng để tick hồ sơ, nộp hồ sơ, ghi nhận thanh toán.</p></div>
      <div class="panel-toolbar"><div class="view-toggle">${[['open', 'Chưa thanh toán'], ['paid', 'Đã thanh toán'], ['all', 'Tất cả']].map(([k, t]) => `<button type="button" class="${payListState.status === k ? 'active' : ''}" data-paylist="${k}">${t}</button>`).join('')}</div></div></div>
    ${payListState.status !== 'paid' ? `<p class="panel-sub">Tổng chưa thanh toán: <b>${spMoney(d.openTotal)}</b></p>` : ''}
    <div class="table-scroll"><table class="data-table sp-table"><thead><tr><th>Hợp đồng</th><th>Đợt</th><th>Nhà cung cấp</th><th class="num">Số tiền</th><th>Hạn</th><th>Hồ sơ</th><th>Trạng thái</th></tr></thead><tbody>
    ${d.items.map(p => `<tr class="sp-row" data-ct-goto="${esc(p.contractCode)}" tabindex="0"><td><b>${esc(p.contractCode)}</b><span class="muted-sub">${esc(p.contractTitle)}</span></td>
      <td class="wrap">${esc(p.seq)}. ${esc(p.title)}</td><td>${esc(p.vendor || '—')}</td><td class="num">${spMoney(p.amount)}</td>
      <td>${uiPill(p.tone, p.dueLabel)}${p.dueDate ? `<span class="muted-sub">${esc(spDateSafe(p.dueDate))}</span>` : ''}</td>
      <td>${esc(p.docsDone)}/${esc(p.docsTotal)}${p.requiredMissing.length ? `<span class="muted-sub">thiếu: ${esc(p.requiredMissing.join(', '))}</span>` : ''}</td>
      <td>${esc(p.statusLabel)}</td></tr>`).join('') || `<tr><td colspan="7">${uiEmpty('Không có đợt thanh toán nào.')}</td></tr>`}
    </tbody></table></div></section>`;
  mount.onclick = (e) => {
    const f = e.target.closest('[data-paylist]');
    if (f) { payListState.status = f.dataset.paylist; return renderPaymentsPage(); }
    const row = e.target.closest('[data-ct-goto]');
    if (row && typeof ctOpenDetail === 'function') ctOpenDetail(row.dataset.ctGoto);
  };
}

/* ---------------------------------------------------------------- Quy hoạch tài nguyên: kết quả trên màn hình */
async function runPlanningPreview(btn) {
  const out = $('#planningResult');
  if (!out) return;
  let params;
  try { params = getPlanningParams(); } catch (err) { out.innerHTML = `<p class="criteria-status error">${esc(err.message)}</p>`; return; }
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== null && v !== '').map(([k, v]) => [k, String(v)]));
  if (typeof currentVcenterFilter !== 'undefined' && currentVcenterFilter) qs.set('vcenter', currentVcenterFilter);
  if (btn) btn.disabled = true;
  out.innerHTML = '<div class="empty-note">Đang tính…</div>';
  try {
    const d = await apiRequest(`/reports/planning?${qs}`);
    const recs = (d.recommendations && d.recommendations.recommendations) || [];
    const sc = d.scenarios;
    out.innerHTML = `
      ${d.recommendations && d.recommendations.headline ? `<p class="home-brief-text">${esc(d.recommendations.headline)}</p>` : ''}
      ${recs.length ? `<ul class="home-list">${recs.map(r => `<li class="home-item"><div class="home-item-main">${uiPill(({ cao: 'bad', tb: 'warn', thap: 'ok' })[r.priority] || 'na', r.timeframe || '—')}
        <b>${esc(r.title)}</b><p class="home-item-detail">${esc(r.decision)}</p></div></li>`).join('')}</ul>` : ''}
      <div class="sp-h">Theo kịch bản tăng trưởng</div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Kịch bản</th><th class="num">Node cần thêm</th><th class="num">+ cores</th><th class="num">+ RAM (GB)</th><th>Cần từ</th><th>Đặt hàng trước</th></tr></thead><tbody>
      ${d.totals.map((t, i) => `<tr><td>${esc(sc[i].label || `${sc[i].growthPct}%/quý`)}${i === d.baseIndex ? ' <span class="pill na">cơ sở</span>' : ''}</td>
        <td class="num">${esc(t.finalNodes)}</td><td class="num">${esc(t.addCores)}</td><td class="num">${esc(t.addRamGB)}</td>
        <td>${esc(t.firstNeedLabel || '—')}</td><td>${esc(t.orderByLabel || '—')}</td></tr>`).join('')}</tbody></table></div>
      <div class="sp-h">Theo cluster (kịch bản cơ sở)</div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Cluster</th><th class="num">Host</th><th class="num">CPU</th><th class="num">RAM</th><th class="num">Node cần thêm</th><th>Cấu hình node</th><th>Cần từ</th><th>Đặt hàng trước</th></tr></thead><tbody>
      ${d.clusters.map(c => `<tr><td><b>${esc(c.name)}</b></td><td class="num">${esc(c.hosts)}</td>
        <td class="num">${c.cpuPct === null ? '—' : esc(Math.round(c.cpuPct))}%</td><td class="num">${c.ramPct === null ? '—' : esc(Math.round(c.ramPct))}%</td>
        <td class="num">${esc(c.nodes)}</td><td>${esc(c.nodeSpec.cores)} cores / ${esc(c.nodeSpec.ramGB)} GB</td>
        <td>${esc(c.firstNeedLabel || '—')}</td><td>${esc(c.orderByLabel || '—')}</td></tr>`).join('')}</tbody></table></div>`;
  } catch (err) { out.innerHTML = `<p class="criteria-status error">${esc(err.message)}</p>`; }
  finally { if (btn) btn.disabled = false; }
}
