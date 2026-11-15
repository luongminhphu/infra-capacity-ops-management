/* =========================================================
   Trang chủ "Hôm nay cần làm" (bản tin + thẻ miền + hộp việc),
   Lịch việc & nhắc hạn, Đợt thanh toán, kết quả Quy hoạch
   trên màn hình — super app SA2 (docs/design/super-app.md §2.2).
   Items come from /api/inbox (filtered by role server-side).
   ========================================================= */

const homeState = { show: 'open', view: 'open', level: '', source: '', data: null, brief: null, bound: false, prefsLoaded: false,
  selected: '', assignees: null };
const agState = { days: 120, kind: '', data: null, bound: false };
const HOME_LEVEL_TONE = { crit: 'crit', bad: 'bad', warn: 'warn', task: 'na', info: 'na' };
const HOME_LEVEL_TEXT = { crit: 'VƯỢT NGƯỠNG', bad: 'CẢNH BÁO', warn: 'THEO DÕI', task: 'Chờ xử lý', info: 'Thông tin' };
const HOME_GROUPS = [['crit', 'Vượt ngưỡng'], ['bad', 'Cảnh báo'], ['warn', 'Theo dõi'], ['task', 'Chờ xử lý'], ['info', 'Thông tin']];
const HOME_VIEWS = [['open', 'Cần xử lý'], ['mine', 'Giao cho tôi'], ['unassigned', 'Chưa giao'], ['done', 'Đã xong']];
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
    const p = await uiLoadPrefs();
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
  if (!homeState.data) mount.innerHTML = uiNote('Đang tải…');
  homeState.show = homeState.view === 'done' ? 'done' : 'open';
  const [inbox, brief, ai] = await Promise.allSettled([apiRequest(`/inbox?show=${homeState.show}${fresh ? '&fresh=true' : ''}`), apiRequest('/reports/brief'),
    typeof aiStatus === 'function' ? aiStatus() : Promise.resolve({ tasks: {} })]);
  homeState.aiOn = ai.status === 'fulfilled' && !!(ai.value.tasks || {}).home_brief;
  homeState.data = inbox.status === 'fulfilled' ? inbox.value : { items: [], counts: {}, sources: {}, unavailable: [], error: inbox.reason.message };
  homeState.brief = brief.status === 'fulfilled' ? brief.value : null;
  if (homeState.show === 'open') homeNavBadges(homeState.data);
  homeRender();
}

/* Titles from the modules end with " — <object>" even when the object is already named ("47 VM … — 47 VM"):
   show the object once. Display only; the item key and the source text are unchanged. */
function homeCleanTitle(t) {
  const i = String(t || '').lastIndexOf(' — ');
  if (i < 0) return t;
  const head = t.slice(0, i), tail = t.slice(i + 3).trim();
  return tail && head.toLowerCase().includes(tail.toLowerCase()) ? head : t;
}

/* "context → action" (rule text of the modules) → { context, action }. */
function homeSplitDetail(d) {
  const i = String(d || '').indexOf('→');
  return i < 0 ? { context: d || '', action: '' } : { context: d.slice(0, i).trim(), action: d.slice(i + 1).trim() };
}

function homeVisibleItems() {
  const d = homeState.data, me = currentUser ? currentUser.username : '';
  return d.items.filter(i => (!homeState.level || i.level === homeState.level) && (!homeState.source || i.source === homeState.source)
    && (homeState.view !== 'mine' || (i.assignee && i.assignee.username === me))
    && (homeState.view !== 'unassigned' || !i.assignee));
}

/* One sentence for the verdict block, from the counts (rules, not AI). */
function homeVerdict(d) {
  const open = d.items.filter(i => !i.ack);
  const worst = HOME_GROUPS.map(g => g[0]).find(l => open.some(i => i.level === l));
  if (!worst) return { tone: 'ok', label: 'AN TOÀN', text: 'Không có việc cần xử lý.' };
  const by = {};
  open.filter(i => i.level === worst).forEach(i => { by[i.sourceLabel] = (by[i.sourceLabel] || 0) + 1; });
  const top = Object.entries(by).sort((a, b) => b[1] - a[1])[0];
  const n = open.filter(i => i.level === worst).length;
  const rest = open.length - n;
  const what = { crit: 'việc vượt ngưỡng', bad: 'việc cảnh báo', warn: 'việc cần theo dõi', task: 'việc chờ bạn xử lý', info: 'thông tin mới' }[worst];
  return { tone: HOME_LEVEL_TONE[worst], label: HOME_LEVEL_TEXT[worst],
    text: `${n} ${what}${top && Object.keys(by).length > 1 ? `, nhiều nhất ở ${top[0]}` : top ? ` ở ${top[0]}` : ''}${rest ? `; thêm ${rest} việc mức thấp hơn.` : '.'}` };
}

function homeRender() {
  const d = homeState.data, b = homeState.brief;
  const items = homeVisibleItems();
  if (!items.some(i => i.key === homeState.selected)) homeState.selected = items.length ? items[0].key : '';
  const v = homeVerdict(d);
  const mine = currentUser ? d.items.filter(i => !i.ack && i.assignee && i.assignee.username === currentUser.username).length : 0;
  const unassigned = d.items.filter(i => !i.ack && !i.assignee).length;
  const domains = Object.entries(d.sources).map(([key, s]) => `<button class="hm-domain${homeState.source === key ? ' active' : ''}" type="button" data-home-source="${esc(key)}" aria-pressed="${homeState.source === key}">
      <span class="hm-domain-name">${esc(s.label)}</span>${s.worst ? uiPill(HOME_LEVEL_TONE[s.worst], HOME_LEVEL_TEXT[s.worst]) : uiPill('ok', 'AN TOÀN')}
      <span class="hm-domain-n">${s.count ? `${esc(s.count)} việc` : 'Không có việc'}</span></button>`).join('');
  const groups = HOME_GROUPS.map(([lvl, label]) => {
    const g = items.filter(i => i.level === lvl);
    if (!g.length) return '';
    return `<li class="hm-group" role="presentation"><span>${esc(label)}</span><span>${g.length}</span></li>` + g.map(i => {
      const sd = homeSplitDetail(i.detail);
      return `<li class="hm-row${i.ack ? ' is-done' : ''}" role="option" id="hm-${esc(btoa(unescape(encodeURIComponent(i.key))).replace(/[^a-zA-Z0-9]/g, ''))}" data-key="${esc(i.key)}" aria-selected="${i.key === homeState.selected}">
        <span class="hm-lv lv-${esc(HOME_LEVEL_TONE[i.level])}" aria-hidden="true"></span>
        <span class="hm-t">${esc(homeCleanTitle(i.title))}</span>
        <span class="hm-r">${i.assignee ? `<b>${esc(i.assignee.displayName)}</b>` : '<b class="hm-none">Chưa giao</b>'}${esc(i.sourceLabel)}</span>
        <span class="hm-m">${esc(i.due ? `Hạn ${spDateSafe(i.due)} · ` : '')}${esc(sd.context)}</span>
        <span class="sr-only">Mức ${esc(i.levelLabel)}</span></li>`;
    }).join('');
  }).join('');
  const views = HOME_VIEWS.map(([k, t]) => `<button class="hm-view" type="button" data-home-view="${k}" aria-pressed="${homeState.view === k}">${esc(t)}${
    k === 'open' ? ` <span class="n">${esc(d.items.filter(i => !i.ack).length)}</span>` : k === 'mine' ? ` <span class="n">${esc(mine)}</span>` : k === 'unassigned' ? ` <span class="n">${esc(unassigned)}</span>` : ''}</button>`).join('');
  $('#homeMount').innerHTML = `
    <section class="hm-verdict tone-rule-${esc(v.tone)}" aria-labelledby="hmVerdict">
      <div class="hm-verdict-meta">${uiPill(v.tone, v.label)}<span>Mức cao nhất trong việc của bạn</span>
        ${homeState.aiOn ? '<button class="btn btn-ghost btn-sm" type="button" data-home-ai><i data-lucide="sparkles"></i>AI tóm tắt hôm nay</button>' : ''}</div>
      <h2 id="hmVerdict">${esc(v.text)}</h2>
      ${b && b.available ? `<p class="hm-lead">${b.overall ? `Capacity ${esc(String(b.overall.text || '').toLowerCase())}: ` : ''}${esc(b.headline || '')} <button class="link-btn" type="button" data-nav-link="executive">Xem điều hành</button></p>` : ''}
    </section>
    <div id="homeAiBrief"></div>
    <div class="hm-domains" role="group" aria-label="Lọc theo miền">${domains}</div>
    ${d.error ? `<p class="criteria-status error">${esc(d.error)}</p>` : ''}
    ${d.unavailable.length ? `<p class="criteria-status">Tạm thời không đọc được: ${esc(d.unavailable.join(', '))}.</p>` : ''}
    <section class="hm-split panel" aria-label="Hộp việc">
      <div class="hm-list">
        <div class="hm-toolbar"><div class="hm-views" role="group" aria-label="Chế độ xem">${views}</div>
          <button class="btn btn-ghost btn-sm" type="button" data-home-refresh title="Đọc lại từ mọi module"><i data-lucide="refresh-cw"></i><span class="sr-only">Làm mới</span></button></div>
        ${homeState.level || homeState.source ? `<div class="hm-filters">${homeState.level ? `<button class="chip-x" type="button" data-home-level="">Mức: ${esc(HOME_LEVEL_TEXT[homeState.level])} ✕</button>` : ''}${homeState.source ? `<button class="chip-x" type="button" data-home-source="">${esc((d.sources[homeState.source] || {}).label || '')} ✕</button>` : ''}</div>` : ''}
        ${groups ? `<ul class="hm-rows" role="listbox" tabindex="0" aria-label="Việc, sắp theo mức" aria-activedescendant="">${groups}</ul>`
          : uiEmpty(homeState.view === 'done' ? 'Chưa có việc nào được đánh dấu xong hay tạm ẩn.' : homeState.view === 'mine' ? 'Chưa có việc nào giao cho bạn.' : 'Không có việc cần xử lý theo bộ lọc này.')}
        <p class="hm-hint"><span><kbd>J</kbd> <kbd>K</kbd> chuyển việc</span><span><kbd>E</kbd> xong</span><span><kbd>H</kbd> ẩn 7 ngày</span><span><kbd>Ctrl K</kbd> tìm</span></p>
      </div>
      <div class="hm-detail" id="homeDetail" aria-live="polite"></div>
    </section>`;
  homeRenderDetail();
  const lb = $('#homeMount .hm-rows'), sel = lb && lb.querySelector('[aria-selected="true"]');
  if (lb && sel) lb.setAttribute('aria-activedescendant', sel.id);
  refreshIcons();
}

function homeRenderDetail() {
  const box = $('#homeDetail');
  if (!box) return;
  const i = homeState.data.items.find(x => x.key === homeState.selected);
  if (!i) { box.innerHTML = uiEmpty('Chọn một việc ở danh sách bên trái.'); return; }
  const sd = homeSplitDetail(i.detail);
  const people = homeState.assignees;
  const assignSel = people ? `<label class="field hm-assign"><span>Ai xử lý</span><select data-home-assign>
      <option value="">— Chưa giao —</option>${people.map(p => `<option value="${esc(p.username)}"${i.assignee && i.assignee.username === p.username ? ' selected' : ''}>${esc(p.displayName)}</option>`).join('')}</select></label>`
    : `<button class="btn btn-secondary btn-sm" type="button" data-home-load-people><i data-lucide="user-plus"></i>${i.assignee ? 'Đổi người xử lý' : 'Giao việc'}</button>`;
  box.innerHTML = `
    <div class="hm-d-top">${uiPill(HOME_LEVEL_TONE[i.level], i.levelLabel)}<h3>${esc(homeCleanTitle(i.title))}</h3></div>
    <div class="hm-d-meta"><span>${esc(i.sourceLabel)}</span>${i.due ? `<span>Hạn ${esc(spDateSafe(i.due))}</span>` : ''}
      ${i.assignee ? `<span>Ai xử lý: <b>${esc(i.assignee.displayName)}</b> (giao bởi ${esc(i.assignee.assignedBy)})</span>` : '<span>Chưa giao cho ai</span>'}
      ${i.ack ? `<span>${i.ack.state === 'done' ? 'Đã đánh dấu xong' : `Đang ẩn tới ${esc(spDateSafe(i.ack.until))}`}</span>` : ''}</div>
    <div class="hm-d-actions">
      ${i.ack ? `<button class="btn btn-secondary btn-sm" type="button" data-home-ack="undo" data-key="${esc(i.key)}">Hiện lại</button>`
        : `<button class="btn btn-primary btn-sm" type="button" data-home-ack="done" data-key="${esc(i.key)}"><i data-lucide="check"></i>Đánh dấu xong <kbd>E</kbd></button>
           <button class="btn btn-secondary btn-sm" type="button" data-home-ack="snooze" data-key="${esc(i.key)}"><i data-lucide="clock"></i>Ẩn 7 ngày <kbd>H</kbd></button>`}
      ${i.page ? `<a class="btn btn-ghost btn-sm" href="${esc(homeLink(i.page, i.query))}"><i data-lucide="external-link"></i>Mở trang ${esc(i.sourceLabel)}</a>` : ''}
      ${assignSel}
    </div>
    ${sd.action ? `<div class="hm-d-sec"><h4>Nên làm gì</h4><p>${esc(sd.action)}</p></div>` : ''}
    ${sd.context ? `<div class="hm-d-sec"><h4>${sd.action ? 'Chi tiết' : 'Nội dung'}</h4><p>${esc(sd.context)}</p></div>` : ''}
    <p class="muted-sub" id="homeAssignMsg" role="status"></p>`;
  refreshIcons();
}

function homeSelect(key, focusList) {
  homeState.selected = key;
  const lb = $('#homeMount .hm-rows');
  if (!lb) return;
  let selEl = null;
  lb.querySelectorAll('[role="option"]').forEach(o => { const on = o.dataset.key === key; o.setAttribute('aria-selected', String(on)); if (on) selEl = o; });
  if (selEl) { lb.setAttribute('aria-activedescendant', selEl.id); selEl.scrollIntoView({ block: 'nearest' }); }
  const swap = () => homeRenderDetail();
  if (document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) document.startViewTransition(swap); else swap();
  if (focusList) lb.focus();
  else if (matchMedia('(max-width:1023px)').matches) { const d = $('#homeDetail'); if (d) d.scrollIntoView({ block: 'start' }); }
}

async function homeAck(key, action) {
  await apiRequest('/inbox/ack', { method: 'POST', body: JSON.stringify({ key, action, days: 7 }) });
  const items = homeVisibleItems(), idx = items.findIndex(i => i.key === key);
  const next = items[idx + 1] || items[idx - 1];
  homeState.selected = next ? next.key : '';
  await renderHomeView();
}

/* Sidebar counts: open items per menu entry (worst level drives the badge tone). */
function homeNavBadges(d) {
  if (!d || !Array.isArray(d.items) || typeof PAGE_ROUTE === 'undefined') return;
  const per = {};
  const bump = (entry, lvl) => {
    const p = per[entry] || (per[entry] = { n: 0, hot: false });
    p.n += 1; if (lvl === 'crit' || lvl === 'bad') p.hot = true;
  };
  d.items.filter(i => !i.ack).forEach(i => {
    const route = PAGE_ROUTE[i.page] || PAGE_ROUTE[HOME_SOURCE_PAGE[i.source]];
    if (route && route.entry) bump(route.entry.key, i.level);
    if (i.level === 'crit' || i.level === 'bad' || i.level === 'task') bump('home', i.level);
  });
  $$('#mainNav .nav-item').forEach(b => {
    let c = b.querySelector('.nav-count');
    const p = per[b.dataset.entry];
    if (!p) { if (c) c.remove(); return; }
    if (!c) { c = document.createElement('span'); c.className = 'nav-count'; b.appendChild(c); }
    c.textContent = p.n > 99 ? '99+' : String(p.n);
    c.classList.toggle('hot', p.hot);
    c.setAttribute('aria-label', `${p.n} việc${p.hot ? ', có cảnh báo' : ''}`);
  });
}

/* Badges without opening the home page: once after sign-in, then every 5 minutes (server caches 60 s). */
let homeBadgeTimer = null;
function homeRefreshBadges() {
  const run = () => apiRequest('/inbox?show=open').then(homeNavBadges).catch(() => { /* badges are optional */ });
  run();
  if (!homeBadgeTimer) homeBadgeTimer = setInterval(run, 5 * 60 * 1000);
}

const spDateSafe = (iso) => iso ? String(iso).slice(0, 10).split('-').reverse().join('/') : '—';

function homeBind() {
  if (homeState.bound) return;
  homeState.bound = true;
  const mount = $('#homeMount');
  mount.addEventListener('click', async (e) => {
    const t = (s) => e.target.closest(s);
    let b;
    if (t('[data-home-refresh]')) return renderHomeView(true);
    if ((b = t('[data-home-ai]'))) return homeAiBrief(b);
    if ((b = t('[data-home-view]'))) {
      const was = homeState.view; homeState.view = b.dataset.homeView;
      return (was === 'done') !== (homeState.view === 'done') ? renderHomeView() : homeRender();
    }
    if ((b = t('[data-home-level]'))) { homeState.level = b.dataset.homeLevel; homeSavePrefs(); return homeRender(); }
    if ((b = t('[data-home-source]'))) {
      const s = b.dataset.homeSource;
      homeState.source = homeState.source === s ? '' : s; homeSavePrefs(); return homeRender();
    }
    if ((b = t('.hm-row'))) return homeSelect(b.dataset.key);
    if (t('[data-home-load-people]')) {
      try { homeState.assignees = await apiRequest('/inbox/assignees'); homeRenderDetail(); const s = $('[data-home-assign]'); if (s) s.focus(); }
      catch (err) { uiAlert(err.message); }
      return;
    }
    if ((b = t('[data-home-ack]'))) {
      b.disabled = true;
      try { await homeAck(b.dataset.key, b.dataset.homeAck); }
      catch (err) { uiAlert(err.message); b.disabled = false; }
    }
  });
  mount.addEventListener('change', async (e) => {
    const s = e.target.closest('[data-home-assign]');
    if (!s) return;
    const key = homeState.selected;
    s.disabled = true;
    try {
      const r = await apiRequest('/inbox/assign', { method: 'POST', body: JSON.stringify({ key, assignee: s.value || null }) });
      const it = homeState.data.items.find(x => x.key === key);
      if (it) it.assignee = r.assignee;
      homeRender();
      uiSetStatus('#homeAssignMsg', r.assignee ? `Đã giao cho ${r.assignee.displayName}.` : 'Đã bỏ giao.', 'ok');
    } catch (err) { uiSetStatus('#homeAssignMsg', err.message, 'error'); s.disabled = false; }
  });
  mount.addEventListener('keydown', async (e) => {
    const lb = e.target.closest('.hm-rows');
    if (!lb || e.ctrlKey || e.metaKey || e.altKey) return;
    const keys = [...lb.querySelectorAll('[role="option"]')].map(o => o.dataset.key);
    const i = keys.indexOf(homeState.selected), k = e.key.toLowerCase();
    if (k === 'arrowdown' || k === 'j') { e.preventDefault(); if (keys.length) homeSelect(keys[Math.min(i + 1, keys.length - 1)], true); }
    else if (k === 'arrowup' || k === 'k') { e.preventDefault(); if (keys.length) homeSelect(keys[Math.max(i - 1, 0)], true); }
    else if (k === 'enter') { const it = homeState.data.items.find(x => x.key === homeState.selected); if (it && it.page) location.hash = homeLink(it.page, it.query); }
    else if ((k === 'e' || k === 'h') && homeState.selected && homeState.view !== 'done') {
      e.preventDefault();
      try { await homeAck(homeState.selected, k === 'e' ? 'done' : 'snooze'); const l = $('#homeMount .hm-rows'); if (l) l.focus(); }
      catch (err) { uiAlert(err.message); }
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
    ${payListState.status !== 'paid' ? `<p class="panel-sub">Tổng chưa thanh toán: <b>${uiMoney(d.openTotal, 'VND', 3)}</b></p>` : ''}
    <div class="table-scroll"><table class="data-table sp-table"><thead><tr><th>Hợp đồng</th><th>Đợt</th><th>Nhà cung cấp</th><th class="num">Số tiền</th><th>Hạn</th><th>Hồ sơ</th><th>Trạng thái</th></tr></thead><tbody>
    ${d.items.map(p => `<tr class="sp-row" data-ct-goto="${esc(p.contractCode)}" tabindex="0"><td><b>${esc(p.contractCode)}</b><span class="muted-sub">${esc(p.contractTitle)}</span></td>
      <td class="wrap">${esc(p.seq)}. ${esc(p.title)}</td><td>${esc(p.vendor || '—')}</td><td class="num">${uiMoney(p.amount, 'VND', 3)}</td>
      <td>${uiPill(p.tone, p.dueLabel)}${p.dueDate ? `<span class="muted-sub">${esc(spDateSafe(p.dueDate))}</span>` : ''}</td>
      <td>${esc(p.docsDone)}/${esc(p.docsTotal)}${p.requiredMissing.length ? `<span class="muted-sub">thiếu: ${esc(p.requiredMissing.join(', '))}</span>` : ''}</td>
      <td>${esc(p.statusLabel)}</td></tr>`).join('') || `<tr><td colspan="7">${uiEmpty('Không có đợt thanh toán nào.')}</td></tr>`}
    </tbody></table></div></section>`;
  mount.onclick = (e) => {
    const f = e.target.closest('[data-paylist]');
    if (f) { payListState.status = f.dataset.paylist; return renderPaymentsPage(); }
    const row = e.target.closest('[data-ct-goto]');
    if (row) uiRequire('spend').then(() => ctOpenDetail(row.dataset.ctGoto), (err) => uiAlert(err.message));
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
  out.innerHTML = uiNote('Đang tính…');
  try {
    const d = await apiRequest(`/reports/planning?${qs}`);
    const recs = (d.recommendations && d.recommendations.recommendations) || [];
    const canCapex = featureOn('spend') && spendRoleOk('editor');
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
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Cluster</th><th class="num">Host</th><th class="num">CPU</th><th class="num">RAM</th><th class="num">Node cần thêm</th><th>Cấu hình node</th><th>Cần từ</th><th>Đặt hàng trước</th><th></th></tr></thead><tbody>
      ${d.clusters.map(c => `<tr><td><b>${esc(c.name)}</b></td><td class="num">${esc(c.hosts)}</td>
        <td class="num">${c.cpuPct === null ? '—' : esc(Math.round(c.cpuPct))}%</td><td class="num">${c.ramPct === null ? '—' : esc(Math.round(c.ramPct))}%</td>
        <td class="num">${esc(c.nodes)}</td><td>${esc(c.nodeSpec.cores)} cores / ${esc(c.nodeSpec.ramGB)} GB</td>
        <td>${esc(c.firstNeedLabel || '—')}</td><td>${esc(c.orderByLabel || '—')}</td>
        <td>${c.nodes > 0 && canCapex ? `<button class="btn btn-ghost btn-sm" type="button" data-plan-capex="${esc(c.name)}">Tạo Capex nháp</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`;
    planState.result = d;
    out.onclick = (e) => { const b = e.target.closest('[data-plan-capex]'); if (b) planOpenCapex(d.clusters.find(x => x.name === b.dataset.planCapex)); };
  } catch (err) { out.innerHTML = `<p class="criteria-status error">${esc(err.message)}</p>`; }
  finally { if (btn) btn.disabled = false; }
}

const planState = { result: null };

/* Quy hoạch → Capex nháp: n node of the base scenario for one cluster (price per node optional). */
async function planOpenCapex(c) {
  if (!c) return;
  let meta;
  try { await uiRequire('spend'); meta = await spLoadMeta(); meta = spState.meta; } catch (err) { uiAlert(err.message); return; }
  const year = Number((/(\d{4})/.exec(c.orderByLabel || '') || [])[1]) || meta.currentFiscalYear;
  const cats = meta.categories.filter(x => x.active);
  const body = `<form id="pcForm" class="sp-form" novalidate><div class="src-grid">
      <label class="field sp-span2"><span>Tên dự án</span><input name="name" maxlength="255" value="${esc(`Bổ sung ${c.nodes} node cluster ${c.name}`)}"></label>
      <label class="field"><span>Năm tài chính</span><input name="fiscalYear" type="number" min="2000" max="2100" value="${esc(year)}"></label>
      <label class="field"><span>Loại chi phí *</span><select name="categoryId">${spOpt('', '— Chọn —', '')}${cats.map(x => spOpt(x.id, x.name, '')).join('')}</select></label>
      <label class="field"><span>Đơn giá 1 node trước VAT (đ, để trống nếu chưa có)</span><input name="price" inputmode="numeric"></label>
    </div></form>
    <p class="muted-sub">Theo kịch bản cơ sở: ${esc(c.nodes)} node ${esc(c.nodeSpec.cores)} cores / ${esc(c.nodeSpec.ramGB)} GB, cần vận hành từ ${esc(c.firstNeedLabel || '—')}, đặt hàng trước ${esc(c.orderByLabel || '—')}. Dự án tạo ở trạng thái nháp; bổ sung NCC, bảo hành và báo giá trong Capex (hoặc qua So sánh báo giá).</p>`;
  spDialog('Tạo Capex nháp từ quy hoạch', body, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
    <button class="btn btn-primary btn-sm" type="button" id="pcSave">Tạo Capex</button>`);
  spOnce('#pcSave', async () => {
    const fd = Object.fromEntries(new FormData($('#pcForm')).entries());
    if (!fd.categoryId) { spMsg('Chọn loại chi phí.'); return; }
    const price = Number(String(fd.price || '0').replace(/[^0-9]/g, '')) || 0;
    const payload = { name: fd.name, fiscalYear: Number(fd.fiscalYear),
      description: `Từ Quy hoạch tài nguyên: cluster ${c.name}, ${c.nodes} node (${c.nodeSpec.cores} cores / ${c.nodeSpec.ramGB} GB), cần từ ${c.firstNeedLabel || '—'}, đặt hàng trước ${c.orderByLabel || '—'}.`,
      items: [{ categoryId: Number(fd.categoryId), description: `${c.nodes} node máy chủ ${c.nodeSpec.cores} cores / ${c.nodeSpec.ramGB} GB — cluster ${c.name}`.slice(0, 255),
        quantity: c.nodes, amountNet: price * c.nodes, vatPct: 10, coverageYears: 0 }] };
    try {
      const p = await apiRequest('/spend/capex', { method: 'POST', body: JSON.stringify(payload) });
      $('#spDialog').close();
      location.hash = homeLink('spend-capex', `code=${p.code}`);
    } catch (err) { spMsg(err.message); }
  });
}
