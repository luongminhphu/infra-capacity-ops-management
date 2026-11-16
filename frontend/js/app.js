/* =========================================================
   app.js — bootstrap: auth gate, sidebar navigation grouped by
   workflow, top bar (page title, global scope, sync), report
   centre, settings page and live-data loading glue.
   Everything else (capacity model, tables, charts, what-if)
   lives in core.js / views/*.js.
   ========================================================= */

let currentVcenterFilter = 'all';
let vcentersCache = [];


const PAGE_ROUTE = {};      // page id -> { entry, group, path, tab }
const PATH_PAGE = {};       // 'backup/jobs' -> 'bk-jobs'
MENU.forEach(g => g.entries.forEach(en => en.tabs.forEach(([id, slug, tab]) => {
  const path = slug ? `${en.key}/${slug}` : en.key;
  PAGE_ROUTE[id] = { entry: en, group: g, path, tab };
  PATH_PAGE[path] = id;
})));
const DEFAULT_PAGE = 'home';
const NAV_ITEMS = {};
PAGES.forEach(it => { NAV_ITEMS[it.id] = it; });
let activeNavId = DEFAULT_PAGE;

/* Modules switched off in Settings → Tính năng disappear from the menu, routes and report centre. */
let appFeatures = {};
let currentUser = null;
const featureOn = (f) => !f || appFeatures[f] !== false;
/* Pages of the Chi phí & Hợp đồng module also need a spend role (the API checks it again). */
const SPEND_RANK = { none: 0, viewer: 1, editor: 2, approver: 3, admin: 4 };
const spendRoleOk = (min) => !min || !currentUser || (SPEND_RANK[(currentUser && currentUser.spendRole) || 'none'] || 0) >= SPEND_RANK[min];
const navItemVisible = (id) => !!NAV_ITEMS[id] && featureOn(NAV_ITEMS[id].feature) && spendRoleOk(NAV_ITEMS[id].spendRole);

function applyFeatures(features) {
  appFeatures = features || {};
  renderSidebarNav();
  // Report centre: hide groups whose modules are all off.
  const groupOn = { backup: featureOn('backup'), planning: featureOn('sizing') || featureOn('planning'),
    spend: featureOn('spend') && spendRoleOk('viewer') && !!currentUser };
  Object.entries(groupOn).forEach(([g, on]) => {
    $$(`[data-rpt-group="${g}"]`).forEach(el => el.classList.toggle('feature-off', !on));
  });
  if (!navItemVisible(currentNavId())) {
    history.replaceState(null, '', '#/home');
    applyInitialRoute();
  }
  syncNavState();
}

async function loadFeatures() {
  try { const r = await apiRequest('/settings/features'); applyFeatures(r.features); }
  catch (err) { applyFeatures({}); }
}

function refreshIcons() {
  if (window.lucide && typeof lucide.createIcons === 'function') {
    try { lucide.createIcons(); } catch (err) { /* ignore */ }
  }
}

const entryTabs = (en) => en.tabs.map(t => t[0]).filter(id => navItemVisible(id));

function renderSidebarNav() {
  const nav = $('#mainNav');
  if (!nav) return;
  nav.innerHTML = MENU.map(g => ({ ...g, entries: g.entries.filter(en => entryTabs(en).length) }))
    .filter(g => g.entries.length).map(g => `
    <div class="nav-group">
      ${g.label ? `<div class="nav-group-label">${esc(g.label)}</div>` : ''}
      ${g.entries.map(en => `<button type="button" class="nav-item" data-nav="${entryTabs(en)[0]}" data-entry="${en.key}" title="${esc(en.tabs.map(t => t[2]).join(' · '))}"><i data-lucide="${en.icon}"></i><span>${esc(en.label)}</span></button>`).join('')}
    </div>`).join('');
  if (!nav.dataset.bound) {
    nav.dataset.bound = '1';
    nav.addEventListener('click', e => {
      const btn = e.target.closest('[data-nav]');
      if (btn) navigateTo(btn.dataset.nav);
    });
  }
  refreshIcons();
}

/* Which nav entry is current — derived from state so views that change
   state themselves (e.g. "Xem snapshot" switching to the assessment) stay in sync. */
function currentNavId() {
  const v = VIEWS[appState.view];
  if (v && v.sub) {
    const sub = v.sub.get();
    const hit = PAGES.find(it => it.view === appState.view && it.sub === sub);
    return hit ? hit.id : v.sub.fallback;
  }
  const hit = PAGES.find(it => it.view === appState.view);
  return hit ? hit.id : DEFAULT_PAGE;
}

function syncNavState() {
  activeNavId = currentNavId();
  const item = NAV_ITEMS[activeNavId];
  const route = PAGE_ROUTE[activeNavId];
  $$('#mainNav .nav-item').forEach(b => {
    const on = !!route && b.dataset.entry === route.entry.key;
    b.classList.toggle('active', on);
    if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  const groupEl = $('#pageGroup'); if (groupEl) groupEl.textContent = route ? (route.group.label || 'ICO') : '';
  const title = route ? route.entry.label : item.title;
  const titleEl = $('#pageTitle'); if (titleEl) titleEl.textContent = title;
  const descEl = $('#pageDesc'); if (descEl) descEl.textContent = item.desc;
  document.title = `${route && route.entry.tabs.length > 1 ? `${route.tab} — ${title}` : title} — Infra Capacity Ops Management`;
  renderPageTabs(route);
  updateScopeControls(item);
  const want = `#/${route ? route.path : activeNavId}`;
  const keep = (location.hash.match(/\?.*$/) || [''])[0];     // ?code=… links of reminders stay usable
  if (location.hash.replace(/\?.*$/, '') !== want) history.replaceState(null, '', want + keep);
}

const PAGE_TAB_GROUPS = {
  settings: [['Đánh giá', ['settings', 'bk-config']], ['Dữ liệu & tính năng', ['set-sources', 'set-features', 'set-ai']],
    ['Người dùng & bảo mật', ['set-users', 'set-auth']], ['Chi phí & giao diện', ['set-spend', 'set-brand']]],
};

/* In-page tabs of the current menu entry (hidden when the entry has a single page). */
function renderPageTabs(route) {
  const bar = $('#pageTabs');
  if (!bar) return;
  const tabs = route ? route.entry.tabs.filter(([id]) => navItemVisible(id)) : [];
  bar.classList.toggle('hidden', tabs.length < 2);
  const tab = ([id, , label]) => `<button class="sim-tab${id === activeNavId ? ' active' : ''}" type="button" role="tab"
      aria-selected="${id === activeNavId}" data-nav-link="${id}">${esc(label)}</button>`;
  const groups = route && PAGE_TAB_GROUPS[route.entry.key];
  bar.classList.toggle('page-tabs-grouped', !!groups && tabs.length >= 2);
  // Many tabs (Cài đặt: 9) read as labelled groups instead of one overflowing row.
  bar.innerHTML = groups ? groups.map(([label, ids]) => {
    const inGroup = tabs.filter(([id]) => ids.includes(id));
    return inGroup.length ? `<div class="tab-group" role="presentation"><span class="tab-group-label">${esc(label)}</span>${inGroup.map(tab).join('')}</div>` : '';
  }).join('') : tabs.map(tab).join('');
}

/* Show only the global filters that apply to the current page. */
function updateScopeControls(item) {
  const scope = new Set(item.scope || []);
  $$('#topbarScope [data-scope]').forEach(el => el.classList.toggle('hidden', !scope.has(el.dataset.scope)));
  const bar = $('#topbarScope');
  if (bar) bar.classList.toggle('hidden', scope.size === 0);
}

function closeSidebar() { document.body.classList.remove('nav-open'); }

function applyPageState(item) {
  appState.view = item.view;
  appState.perspective = item.view === 'executive' ? 'executive' : 'technical';
  const v = VIEWS[item.view];
  if (v && v.sub) v.sub.set(item.sub);
}

function navigateTo(id) {
  const item = navItemVisible(id) ? NAV_ITEMS[id] : NAV_ITEMS[DEFAULT_PAGE];
  applyPageState(item);
  closeSidebar();
  syncViewSections();
  renderActiveView();
  const main = $('#mainContent');
  if (main) window.scrollTo({ top: 0, behavior: 'auto' });
}

/* #/backup/jobs (new path) or #/bk-jobs (old page id, kept working). */
function navIdFromHash() {
  const m = /^#\/([a-z0-9/-]+)/.exec(location.hash || '');
  if (!m) return null;
  const raw = m[1].replace(/\/+$/, '');
  const id = PATH_PAGE[raw] || (NAV_ITEMS[raw] ? raw : null);
  return id && navItemVisible(id) ? id : null;
}

/* Apply the hash route to state without rendering (used before the first data load). */
function applyInitialRoute() {
  const id = navIdFromHash() || DEFAULT_PAGE;
  applyPageState(NAV_ITEMS[id]);
  syncViewSections();
}

function bindNavigation() {
  renderSidebarNav();
  document.addEventListener('click', e => {
    const link = e.target.closest('[data-nav-link]');
    if (link) { e.preventDefault(); navigateTo(link.dataset.navLink); }
  });
  window.addEventListener('hashchange', () => {
    const id = navIdFromHash();
    if (id && (id !== activeNavId || id === 'entity')) navigateTo(id);   // entity: same page, other object
    else if (!id) syncNavState();   // unknown or switched-off page: restore the current one
  });
  const open = $('#btnSidebarOpen'), close = $('#btnSidebarClose'), scrim = $('#sidebarScrim');
  if (open) open.addEventListener('click', () => document.body.classList.add('nav-open'));
  if (close) close.addEventListener('click', closeSidebar);
  if (scrim) scrim.addEventListener('click', closeSidebar);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeSidebar(); });
}

/* ---------- Theme (dark is the product default) ---------- */
function initTheme() {
  let theme = 'dark';
  try { const v = localStorage.getItem('vco.theme'); if (v === 'light' || v === 'dark') theme = v; } catch (err) { /* ignore */ }
  document.documentElement.setAttribute('data-theme', theme);
}

/* Server copy of the user's preferences (ui.js uiLoadPrefs): theme, history range, report toggles. */
async function applyUserPrefs() {
  await uiLoadPrefs();
  const theme = uiPref('theme');
  if (theme === 'light' || theme === 'dark') document.documentElement.setAttribute('data-theme', theme);
  if (typeof historyApplyPrefs === 'function') historyApplyPrefs();
  ['rptAi', 'rptArchive'].forEach((id) => { const cb = document.getElementById(id); if (cb) cb.checked = uiPref(id === 'rptAi' ? 'reportAi' : 'reportArchive', false) === true; });
}

/* ---------- Screen switching ---------- */
function showLoginScreen() {
  $('#loginScreen').classList.remove('hidden');
  $('#appShell').classList.add('hidden');
}
function showAppShell() {
  $('#loginScreen').classList.add('hidden');
  $('#appShell').classList.remove('hidden');
}
window.onApiUnauthorized = () => showLoginScreen();

/* ---------- Login ---------- */
async function finishLogin(user) {
  try { sessionStorage.removeItem('ico.autoTried'); } catch (err) { /* ignore */ }
  currentUser = user;
  $('#currentUserLabel').textContent = user.displayName || user.username;
  showAppShell();
  await initAppData();
}

async function handleLoginSubmit(e) {
  e.preventDefault();
  const btn = $('#btnLogin');
  const username = $('#loginUsername').value.trim();
  const password = $('#loginPassword').value;
  $('#loginError').textContent = '';
  btn.disabled = true;
  btn.textContent = 'Đang đăng nhập...';
  try {
    await finishLogin(await api.login(username, password));
  } catch (err) {
    $('#loginError').textContent = err.message || 'Đăng nhập thất bại.';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Đăng nhập';
  }
}

async function handleLogout() {
  uiFlushPrefs();
  uiResetPrefs();
  try { sessionStorage.setItem('ico.noAuto', '1'); } catch (err) { /* stay on the login page after sign-out */ }
  let res = null;
  try { res = await api.logout(); } catch (err) { /* ignore */ }
  if (res && res.logoutUrl) { window.location.href = res.logoutUrl; return; }  // also end the Microsoft session
  showLoginScreen();
}

/* ---------- Login options (local password / Microsoft Entra ID) ---------- */
async function setupLoginOptions() {
  // Invite link: /#passkey_invite=<token> — the fragment never reaches the server; drop it from the address bar now.
  const inv = /^#passkey_invite=([A-Za-z0-9_-]{20,200})$/.exec(window.location.hash || '');
  if (inv) { window.pkInviteToken = inv[1]; history.replaceState(null, '', window.location.pathname + window.location.search); }
  loadLoginBranding();
  const params = new URLSearchParams(window.location.search);
  const loginError = params.get('login_error');
  if (loginError) {
    $('#loginError').textContent = loginError;
    params.delete('login_error');
    const q = params.toString();
    history.replaceState(null, '', window.location.pathname + (q ? '?' + q : '') + window.location.hash);
  }
  let cfg = { local: true, entra: false };
  try { cfg = await api.authConfig(); } catch (err) { /* keep local login */ }
  const entraBtn = $('#btnLoginEntra');
  const local = $('#localLoginFields');
  if (cfg.entra) {
    const next = window.location.pathname + window.location.search + window.location.hash;
    entraBtn.href = '/api/auth/oidc/login?next=' + encodeURIComponent(next);
    if (cfg.entraLabel) $('#btnLoginEntraLabel').textContent = cfg.entraLabel;
    entraBtn.classList.remove('hidden');
    const hint = $('#loginEntraHint'); if (hint && (cfg.passkey || cfg.password)) hint.classList.remove('hidden');
  }
  const showLocal = () => {
    local.classList.remove('hidden');
    $('#btnShowLocalLogin').classList.add('hidden');
    if (cfg.entra) $('#loginDivider').classList.remove('hidden');
    $('#loginUsername').required = true; $('#loginPassword').required = true;
  };
  if (typeof setupPasskeyLogin === 'function') setupPasskeyLogin(cfg, params);
  let autoTried = false;
  try { autoTried = !!sessionStorage.getItem('ico.noAuto') || !!sessionStorage.getItem('ico.autoTried'); } catch (err) { autoTried = true; }
  if (cfg.entraAuto && cfg.entra && !loginError && !window.pkInviteToken && !autoTried) {
    // One automatic hop per browser tab: if the session does not stick (cookie refused), the page stays here.
    try { sessionStorage.setItem('ico.autoTried', '1'); } catch (err) { /* no storage: no auto hop */ }
    window.location.href = entraBtn.href; return;                // seamless SSO behind App Proxy
  }
  if ((cfg.local && cfg.password !== false) || (!cfg.entra && !cfg.passkey)) {
    showLocal();
  } else if (cfg.local && cfg.password === false) {
    local.classList.add('hidden');
    $('#loginUsername').required = false; $('#loginPassword').required = false;
    if (cfg.passkey && !cfg.entra) $('#loginDivider').classList.add('hidden');
  } else {
    local.classList.add('hidden');
    $('#loginUsername').required = false; $('#loginPassword').required = false;
    if (cfg.breakglass) {
      const alt = $('#btnShowLocalLogin');
      alt.classList.remove('hidden');
      alt.addEventListener('click', () => { showLocal(); $('#loginUsername').focus(); }, { once: true });
    }
  }
}

/* ---------- vCenter top bar ---------- */
function vcenterStatusInfo(v) {
  const cls = v.demo ? 'demo' : (v.lastStatus === 'ok' ? 'ok' : (v.lastError ? 'error' : ''));
  const label = v.demo ? `${v.name} (DEMO)` : v.name;
  const when = v.lastSyncAt ? new Date(v.lastSyncAt).toLocaleString('vi-VN', { hour12: false }) : 'chưa đồng bộ';
  const statusText = v.demo ? 'Demo' : (v.lastStatus === 'ok' ? 'Kết nối tốt' : (v.lastError ? 'Lỗi' : 'Chưa đồng bộ'));
  return { cls, label, when, statusText };
}

function renderVcenterStatusChips() {
  const row = $('#vcenterStatusRow');
  if (row) {
    row.innerHTML = vcentersCache.length
      ? vcentersCache.map(v => {
          const i = vcenterStatusInfo(v);
          return `<span class="vcenter-chip ${i.cls}" title="${esc(i.label)} · ${i.statusText} · đồng bộ ${esc(i.when)}${v.lastError ? ' · ' + esc(v.lastError) : ''}"><span class="dot"></span><span class="vc-name">${esc(v.name)}</span></span>`;
        }).join('')
      : `<span class="vcenter-chip"><span class="dot"></span>Chưa cấu hình vCenter</span>`;
  }
  const tbody = document.querySelector('#vcenterSourceTable tbody');
  if (tbody) {
    tbody.innerHTML = vcentersCache.length
      ? vcentersCache.map(v => {
          const i = vcenterStatusInfo(v);
          const pill = i.cls === 'ok' ? 'ok' : (i.cls === 'error' ? 'crit' : (i.cls === 'demo' ? 'warn' : 'na'));
          return `<tr><td>${esc(v.name)}<span class="sub muted-sub">${esc(v.key || '')}</span></td><td><span class="pill ${pill}">${i.statusText}</span>${v.lastError ? `<div class="muted-sub">${esc(v.lastError)}</div>` : ''}</td><td>${esc(i.when)}</td></tr>`;
        }).join('')
      : '<tr><td colspan="3" class="empty-note">Chưa cấu hình vCenter — dùng import thủ công bên dưới.</td></tr>';
  }
}

async function refreshVcenterList() {
  try {
    vcentersCache = await api.listVcenters();
    const sel = $('#vcenterSelect');
    const prev = sel.value || 'all';
    sel.innerHTML = `<option value="all">Tất cả vCenter</option>` +
      vcentersCache.map(v => `<option value="${esc(v.key)}">${esc(v.name)}${v.demo ? ' (DEMO)' : ''}</option>`).join('') +
      `<option value="import">Chỉ dữ liệu import</option>`;
    sel.value = [...sel.options].some(o => o.value === prev) ? prev : 'all';
    currentVcenterFilter = sel.value;
    renderVcenterStatusChips();
  } catch (err) {
    console.error('Không tải được danh sách vCenter', err);
  }
}

/* ---------- Report scope filter (cluster/host) for PDF export ---------- */
const SCOPE_PRESET_PATTERNS = {
  production: /prod/i,
  uat: /uat/i,
  dr: /(^|[^a-z])dr([^a-z]|$)|disaster/i,
};
const SCOPE_PRESET_LABELS = { production: 'Production', uat: 'UAT', dr: 'DR' };

function getCheckedValues(selector) {
  return Array.from(document.querySelectorAll(selector)).filter(cb => cb.checked).map(cb => cb.value);
}

function getSelectedPdfClusters() {
  const all = Array.from(document.querySelectorAll('.pdfClusterCb'));
  if (!all.length) return null;
  const checked = all.filter(cb => cb.checked).map(cb => cb.value);
  if (checked.length === all.length) return null; // no filter = every cluster
  return checked;
}

function getSelectedPdfHosts() {
  const toggle = $('#pdfHostNarrowToggle');
  if (!toggle || !toggle.checked) return null; // host narrowing disabled = every host in scope
  const all = Array.from(document.querySelectorAll('.pdfHostCb'));
  if (!all.length) return null;
  return all.filter(cb => cb.checked).map(cb => cb.value);
}

function renderPdfHostList() {
  const container = $('#pdfHostList');
  if (!container) return;
  const clusters = getSelectedPdfClusters();
  const hosts = typeof getHostsForClusters === 'function' ? getHostsForClusters(clusters) : [];
  const prevChecked = new Set(getCheckedValues('.pdfHostCb'));
  const keepPrev = prevChecked.size > 0;
  container.innerHTML = hosts.length
    ? hosts.map(h => `<label class="finding-filter-item"><input type="checkbox" class="pdfHostCb" value="${esc(h)}" ${(!keepPrev || prevChecked.has(h)) ? 'checked' : ''}><span>${esc(h)}</span></label>`).join('')
    : '<div class="scope-empty-note">Không có host trong phạm vi đã chọn</div>';
}

function updateScopeFilterSummary() {
  const summary = $('#scopeFilterSummary');
  if (!summary) return;
  const clusters = getSelectedPdfClusters();
  const hosts = getSelectedPdfHosts();
  const parts = [];
  if (clusters) parts.push(`${clusters.length} cluster`);
  if (hosts) parts.push(`${hosts.length} host`);
  summary.textContent = parts.length ? `Đang giới hạn: ${parts.join(', ')}` : 'Toàn bộ cluster trong vCenter đã chọn';
  summary.classList.toggle('is-narrowed', parts.length > 0);

  const clusterAllCb = $('#pdfClusterAll');
  const clusterBoxes = Array.from(document.querySelectorAll('.pdfClusterCb'));
  if (clusterAllCb) clusterAllCb.checked = clusterBoxes.length > 0 && clusterBoxes.every(cb => cb.checked);

  $$('.scope-quick-btn').forEach(btn => {
    const preset = btn.dataset.scopeQuick;
    if (preset === 'all') {
      btn.classList.toggle('active', clusters === null);
      return;
    }
    const re = SCOPE_PRESET_PATTERNS[preset];
    const matching = clusterBoxes.filter(cb => re.test(cb.value)).map(cb => cb.value).sort();
    const checked = clusterBoxes.filter(cb => cb.checked).map(cb => cb.value).sort();
    btn.classList.toggle('active', matching.length > 0 && JSON.stringify(matching) === JSON.stringify(checked));
  });
}

function populateScopeFilterPresets() {
  const group = $('#scopeQuickGroup');
  if (!group) return;
  const clusters = typeof getClusterList === 'function' ? getClusterList() : [];
  const existing = new Set(['all']);
  Object.keys(SCOPE_PRESET_PATTERNS).forEach(preset => {
    if (clusters.some(c => SCOPE_PRESET_PATTERNS[preset].test(c)) && !group.querySelector(`[data-scope-quick="${preset}"]`)) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'scope-quick-btn';
      btn.dataset.scopeQuick = preset;
      btn.textContent = SCOPE_PRESET_LABELS[preset];
      group.appendChild(btn);
      btn.addEventListener('click', () => applyScopePreset(preset));
    }
    existing.add(preset);
  });
}

function applyScopePreset(preset) {
  const clusterBoxes = Array.from(document.querySelectorAll('.pdfClusterCb'));
  if (preset === 'all') {
    clusterBoxes.forEach(cb => { cb.checked = true; });
  } else {
    const re = SCOPE_PRESET_PATTERNS[preset];
    clusterBoxes.forEach(cb => { cb.checked = re.test(cb.value); });
  }
  renderPdfHostList();
  updateScopeFilterSummary();
}

function populatePdfScopeFilter() {
  const container = $('#pdfClusterList');
  if (!container) return;
  const clusters = typeof getClusterList === 'function' ? getClusterList() : [];
  const prevChecked = new Set(getCheckedValues('.pdfClusterCb'));
  const keepPrev = prevChecked.size > 0;
  container.innerHTML = clusters.length
    ? clusters.map(c => `<label class="finding-filter-item"><input type="checkbox" class="pdfClusterCb" value="${esc(c)}" ${(!keepPrev || prevChecked.has(c)) ? 'checked' : ''}><span>${esc(c)}</span></label>`).join('')
    : '<div class="scope-empty-note">Chưa có cluster nào</div>';
  populateScopeFilterPresets();
  renderPdfHostList();
  updateScopeFilterSummary();
}

function bindPdfScopeFilterDropdown() {
  const clusterAllCb = $('#pdfClusterAll');
  if (clusterAllCb) {
    clusterAllCb.addEventListener('change', () => {
      document.querySelectorAll('.pdfClusterCb').forEach(cb => { cb.checked = clusterAllCb.checked; });
      renderPdfHostList();
      updateScopeFilterSummary();
    });
  }
  const hostToggle = $('#pdfHostNarrowToggle');
  const hostList = $('#pdfHostList');
  if (hostToggle && hostList) {
    hostToggle.addEventListener('change', () => {
      hostList.classList.toggle('hidden', !hostToggle.checked);
      updateScopeFilterSummary();
    });
  }
  $$('.scope-quick-btn').forEach(btn => btn.addEventListener('click', () => applyScopePreset(btn.dataset.scopeQuick)));
  ['#pdfClusterList', '#pdfHostList'].forEach(sel => {
    const list = $(sel);
    if (!list) return;
    list.addEventListener('change', e => {
      if (e.target.classList.contains('pdfClusterCb')) { renderPdfHostList(); updateScopeFilterSummary(); }
      else if (e.target.classList.contains('pdfHostCb')) { updateScopeFilterSummary(); }
    });
  });
}

function getSelectedPdfFindingTypes() {
  const all = Array.from(document.querySelectorAll('.pdfFindingTypeCb'));
  const checked = all.filter(cb => cb.checked).map(cb => cb.value);
  if (checked.length === all.length) return null; // no filter = every type
  return checked;
}

function updatePdfFindingFilterSummary() {
  const all = Array.from(document.querySelectorAll('.pdfFindingTypeCb'));
  const checkedCount = all.filter(cb => cb.checked).length;
  const summary = $('#pdfFindingFilterSummary');
  if (!summary) return;
  summary.textContent = checkedCount === all.length ? 'Tất cả loại finding' : `Đang chọn ${checkedCount}/${all.length} loại`;
  summary.classList.toggle('is-narrowed', checkedCount !== all.length);
  const allCb = $('#pdfFindingTypeAll');
  if (allCb) allCb.checked = checkedCount === all.length;
}

function bindPdfFindingFilterDropdown() {
  const allCb = $('#pdfFindingTypeAll');
  const typeCbs = Array.from(document.querySelectorAll('.pdfFindingTypeCb'));
  if (allCb) {
    allCb.addEventListener('change', () => {
      typeCbs.forEach(cb => { cb.checked = allCb.checked; });
      updatePdfFindingFilterSummary();
    });
  }
  typeCbs.forEach(cb => cb.addEventListener('change', updatePdfFindingFilterSummary));
  updatePdfFindingFilterSummary();
}

/* ---------- Assessment criteria (Storage on/off) ---------- */
function setCriteriaStatus(message, kind) { uiSetStatus('#criteriaStatus', message, kind || ''); }

function updateCriteriaSummary() {
  const cb = $('#criteriaStorageToggle');
  if (cb) cb.checked = !!appState.policy.includeStorage;
}

async function handleStorageCriteriaToggle(e) {
  const enabled = !!e.target.checked;
  appState.policy.includeStorage = enabled;
  try { localStorage.setItem('vco.includeStorage', enabled ? '1' : '0'); } catch (err) { /* ignore */ }
  updateCriteriaSummary();
  refreshAll();
  if (!uiIsAdmin()) {
    setCriteriaStatus(`Chỉ áp dụng trên màn hình của bạn. ${ADMIN_ONLY_NOTE}`, '');
    return;
  }
  try {
    await api.updateSettings({ include_storage: enabled });
    setCriteriaStatus(enabled ? 'Đã bật đánh giá Storage (đã lưu cho toàn hệ thống).' : 'Đã tắt đánh giá Storage (đã lưu cho toàn hệ thống).', 'ok');
  } catch (err) {
    setCriteriaStatus(`Áp dụng cục bộ; không lưu được lên server: ${err.message}`, 'error');
  }
}

function bindCriteriaDropdown() {
  const cb = $('#criteriaStorageToggle');
  if (cb) cb.addEventListener('change', handleStorageCriteriaToggle);
  updateCriteriaSummary();
}

/* ---------- Thresholds: applied live by core.js; "Lưu ngưỡng" persists them ---------- */
function setThresholdStatus(message, kind) {
  const el = $('#thresholdStatus');
  if (!el) return;
  el.textContent = message || '';
  el.classList.remove('ok', 'error');
  if (kind) el.classList.add(kind);
}

async function handleSaveThresholds(e) {
  const btn = e.currentTarget;
  rebuildFromRaw(); // normalises the inputs into appState.policy
  refreshAll();
  btn.disabled = true;
  setThresholdStatus('Đang lưu...', null);
  try {
    await api.updateSettings({
      idle_days: appState.policy.idleDays,
      cpu_overcommit_max: appState.policy.cpuOvercommitMax,
      ram_overcommit_max: appState.policy.ramOvercommitMax,
      ht_factor: appState.policy.htFactor,
    });
    setThresholdStatus(`Đã lưu ngưỡng cho toàn hệ thống lúc ${new Date().toLocaleTimeString('vi-VN', { hour12: false })}.`, 'ok');
  } catch (err) {
    setThresholdStatus(`Đang áp dụng cục bộ; không lưu được lên server: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
  }
}

/* ---------- Company logo upload (embedded on PDF report covers) ---------- */
function setLogoStatus(message, kind) {
  const el = $('#logoUploadStatus');
  if (!el) return;
  el.textContent = message || '';
  el.classList.remove('ok', 'error');
  if (kind) el.classList.add(kind);
}

function renderLogoPreview(dataUrl) {
  const img = $('#logoUploadImg');
  const emptyLabel = $('#logoUploadEmptyLabel');
  if (!img || !emptyLabel) return;
  if (dataUrl) {
    img.src = dataUrl;
    img.classList.remove('hidden');
    emptyLabel.classList.add('hidden');
  } else {
    img.removeAttribute('src');
    img.classList.add('hidden');
    emptyLabel.classList.remove('hidden');
  }
}

async function loadCompanyLogo() {
  try {
    const res = await api.getLogo();
    renderLogoPreview(res && res.dataUrl ? res.dataUrl : null);
  } catch (err) {
    renderLogoPreview(null);
  }
}

const MAX_LOGO_UPLOAD_BYTES = 2 * 1024 * 1024;

async function handleLogoFileSelected(e) {
  const file = e.target.files && e.target.files[0];
  e.target.value = '';
  if (!file) return;
  if (file.size > MAX_LOGO_UPLOAD_BYTES) {
    setLogoStatus('Kích thước logo vượt quá 2MB cho phép.', 'error');
    return;
  }
  setLogoStatus('Đang tải logo lên...', null);
  try {
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('Không đọc được tệp ảnh.'));
      reader.readAsDataURL(file);
    });
    const res = await api.saveLogo(dataUrl, file.name);
    renderLogoPreview(res && res.dataUrl ? res.dataUrl : dataUrl);
    setLogoStatus('Đã lưu logo. Logo sẽ xuất hiện trên báo cáo PDF tiếp theo.', 'ok');
  } catch (err) {
    setLogoStatus(`Tải logo thất bại: ${err.message}`, 'error');
  }
}

async function handleRemoveLogo() {
  setLogoStatus('Đang xóa logo...', null);
  try {
    await api.deleteLogo();
    renderLogoPreview(null);
    setLogoStatus('Đã xóa logo công ty.', 'ok');
  } catch (err) {
    setLogoStatus(`Xóa logo thất bại: ${err.message}`, 'error');
  }
}

/* Non-admins keep the live (on-screen) threshold / storage what-if, but cannot save or change the logo. */
function applySharedConfigGates() {
  const admin = uiIsAdmin();
  const syncNow = $('#btnSyncNow');      // manual vCenter / NetBackup sync: admins only (decision 16/11)
  if (syncNow) syncNow.classList.toggle('hidden', !admin);
  const saveThr = $('#btnSaveThresholds');
  if (saveThr) {
    saveThr.disabled = !admin;
    saveThr.title = admin ? '' : ADMIN_ONLY_NOTE;
    if (!admin) setThresholdStatus(`${ADMIN_ONLY_NOTE} Ngưỡng nhập ở đây chỉ áp dụng trên màn hình của bạn.`, null);
    else if (($('#thresholdStatus') || {}).textContent?.startsWith(ADMIN_ONLY_NOTE)) setThresholdStatus('', null);
  }
  const pick = document.querySelector('label[for="logoUploadInput"]');
  if (pick) pick.classList.toggle('hidden', !admin);
  const removeBtn = $('#btnRemoveLogo');
  if (removeBtn) removeBtn.classList.toggle('hidden', !admin);
  if (!admin) setLogoStatus(ADMIN_ONLY_NOTE, null);
  else if (($('#logoUploadStatus') || {}).textContent === ADMIN_ONLY_NOTE) setLogoStatus('', null);
}

function bindLogoUploadDropdown() {
  const input = $('#logoUploadInput');
  const removeBtn = $('#btnRemoveLogo');
  if (input) input.addEventListener('change', handleLogoFileSelected);
  if (removeBtn) removeBtn.addEventListener('click', handleRemoveLogo);
}

function getPlanningParams() {
  const growthRaw = ($('#planGrowth').value || '').split(',').map(s => parseFloat(s.trim())).filter(n => isFinite(n) && n >= 0);
  if (!growthRaw.length) throw new Error('Tăng trưởng %/quý không hợp lệ (ví dụ: 5,10,20).');
  const num = (sel) => { const v = parseFloat($(sel).value); return isFinite(v) ? v : null; };
  return {
    horizon: $('#planHorizon').value,
    growth: growthRaw.join(','),
    node_cores: num('#planNodeCores'),
    node_ram: num('#planNodeRam'),
    lead: num('#planLead') ?? 3,
    target: num('#planTarget') ?? 80,
  };
}

async function handleExportPdf(view, btn) {
  const originalLabel = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = 'Đang tạo PDF...';
  try {
    const vcenterParam = currentVcenterFilter === 'import' ? '__none__' : currentVcenterFilter;
    const findingTypes = getSelectedPdfFindingTypes();
    const clusters = getSelectedPdfClusters();
    const hosts = getSelectedPdfHosts();
    const months = (typeof historyState !== 'undefined' && historyState.months) ? historyState.months : 12;
    const extra = view === 'planning' ? getPlanningParams() : null;
    await api.downloadPdfReport(view, vcenterParam, findingTypes, clusters, hosts, !!appState.policy.includeStorage, months, extra);
  } catch (err) {
    showValidationBanner([`Không thể xuất báo cáo PDF: ${err.message}`], []);
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalLabel;
  }
}

/* License executive summary from the report centre — same scope rules as the License pages. */
async function handleExportLicSummaryPdf(btn) {
  const originalLabel = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = 'Đang tạo PDF...';
  try {
    if (typeof licLoad === 'function') await licLoad(false);
    const sc = licScope();
    await api.licReportPdf(sc.vcenter, sc.cluster, sc.clusters, null);
  } catch (err) {
    showValidationBanner([`Không thể xuất báo cáo license: ${err.message}`], []);
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalLabel;
  }
}

async function handleSyncNow() {
  const btn = $('#btnSyncNow');
  const originalLabel = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = 'Đang đồng bộ...';
  try {
    if (currentVcenterFilter === 'all' || currentVcenterFilter === 'import') {
      await api.syncAll();
    } else {
      await api.syncVcenter(currentVcenterFilter);
    }
    await refreshVcenterList();
    await loadLiveData();
    showValidationBanner([], [`Đã đồng bộ dữ liệu từ vCenter lúc ${new Date().toLocaleString('vi-VN')}.`]);
    if (typeof bkInvalidate === 'function') bkInvalidate();
  } catch (err) {
    showValidationBanner([`Đồng bộ vCenter thất bại: ${err.message}`], []);
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalLabel;
  }
}

/* ---------- Live data loading (replaces/augments file-import flow) ---------- */
async function loadLiveData() {
  try {
    const includeImport = currentVcenterFilter !== 'import' ? true : true;
    const vcenterParam = currentVcenterFilter === 'import' ? 'none' : currentVcenterFilter;
    const params = { vcenter: vcenterParam === 'none' ? 'all' : vcenterParam, include_import: includeImport };
    if (currentVcenterFilter === 'import') {
      // "Chỉ dữ liệu import" — ask for a scope that matches no live vCenter key
      // so only the manual-import snapshot comes back.
      params.vcenter = '__none__';
    }
    const data = await api.getLatestData(params);
    appState.hostsRaw = [];
    appState.vmsRaw = [];
    appState.hosts = data.hosts || [];
    appState.vms = data.vms || [];
    appState.reportTimestamp = new Date().toISOString();
    appState.validation = { errors: [], warnings: [] };
    const footprintNote = data.runMeta && data.runMeta.length
      ? [`Dữ liệu từ ${data.runMeta.map(r => `${r.vcenterKey === 'import' ? 'import thủ công' : r.vcenterKey} (${r.finishedAt ? new Date(r.finishedAt).toLocaleString('vi-VN') : 'chưa rõ giờ'})`).join(', ')}.`]
      : [];
    // Where the numbers come from is information, not a warning: one quiet line (MASTER.md, trang-chu.md).
    showValidationBanner([], [], footprintNote);
    refreshAll(); // renders whichever page is active, including Điều hành
  } catch (err) {
    console.error('Không tải được dữ liệu vCenter', err);
    showValidationBanner([`Không tải được dữ liệu từ server: ${err.message}`], []);
  }
}

/* Called after a manual CSV/JSON import so it is also persisted server-side
   and therefore feeds the Executive trend history alongside live polls. */
async function persistImportIfReady() {
  if (!appState.hosts.length && !appState.vms.length) return;
  if (!uiIsAdmin()) return;            // import feeds the shared dashboard: admins only; others see it locally
  try {
    await api.importData({ hosts: appState.hosts, vms: appState.vms, label: 'manual-import' });
    await refreshVcenterList();
  } catch (err) {
    console.warn('Không lưu được dữ liệu import lên server (dữ liệu vẫn hiển thị cục bộ):', err.message);
  }
}

/* ---------- Bootstrap ---------- */
async function initAppData() {
  applyInitialRoute();
  await Promise.all([loadFeatures(), applyUserPrefs()]);
  if (typeof initAdminSettings === 'function') initAdminSettings(currentUser);
  applySharedConfigGates();
  if (typeof homeRefreshBadges === 'function') homeRefreshBadges();   // sidebar counts (MASTER.md §6)
  try {
    const policy = await api.getSettings();
    if (policy) {
      appState.policy.idleDays = policy.idle_days ?? policy.idleDays ?? appState.policy.idleDays;
      appState.policy.cpuOvercommitMax = policy.cpu_overcommit_max ?? policy.cpuOvercommitMax ?? appState.policy.cpuOvercommitMax;
      appState.policy.ramOvercommitMax = policy.ram_overcommit_max ?? policy.ramOvercommitMax ?? appState.policy.ramOvercommitMax;
      appState.policy.htFactor = policy.ht_factor ?? policy.htFactor ?? appState.policy.htFactor;
      if (policy.include_storage !== undefined || policy.includeStorage !== undefined) {
        appState.policy.includeStorage = !!(policy.include_storage ?? policy.includeStorage);
      }
      $('#idleThresholdInput').value = appState.policy.idleDays;
      $('#cpuOvercommitInput').value = appState.policy.cpuOvercommitMax;
      $('#ramOvercommitInput').value = appState.policy.ramOvercommitMax;
      $('#htFactorInput').value = appState.policy.htFactor;
    }
  } catch (err) {
    // Server unreachable — fall back to the last locally chosen criteria.
    try { const v = localStorage.getItem('vco.includeStorage'); if (v !== null) appState.policy.includeStorage = v === '1'; } catch (e) { /* ignore */ }
  }
  updateCriteriaSummary();

  await refreshVcenterList();
  await loadLiveData();
  await loadCompanyLogo();
}

function bindTopBarInteractions() {
  $('#loginForm').addEventListener('submit', handleLoginSubmit);
  $('#btnLogout').addEventListener('click', handleLogout);
  $('#btnSyncNow').addEventListener('click', handleSyncNow);
  const searchBtn = $('#btnSearch'); if (searchBtn) searchBtn.addEventListener('click', () => openPalette());
  [['#btnExportPdfExec', 'executive'], ['#btnExportPdfTech', 'technical'], ['#btnExportPdfPlan', 'planning']].forEach(([sel, view]) => {
    const b = $(sel); if (b) b.addEventListener('click', e => handleExportPdf(view, e.currentTarget));
  });
  const planPreview = $('#btnPlanPreview');
  if (planPreview) planPreview.addEventListener('click', e => runPlanningPreview(e.currentTarget));
  const planQuick = $('#btnExportPdfPlanQuick');
  if (planQuick) planQuick.addEventListener('click', e => handleExportPdf('planning', e.currentTarget));
  const licSummary = $('#btnExportPdfLicSummary');
  if (licSummary) licSummary.addEventListener('click', e => handleExportLicSummaryPdf(e.currentTarget));
  const saveThr = $('#btnSaveThresholds');
  if (saveThr) saveThr.addEventListener('click', handleSaveThresholds);
  bindPdfFindingFilterDropdown();
  bindPdfScopeFilterDropdown();
  bindLogoUploadDropdown();
  bindCriteriaDropdown();
  $('#vcenterSelect').addEventListener('change', e => {
    currentVcenterFilter = e.target.value;
    loadLiveData();
  });
}

async function bootstrap() {
  initTheme();
  bindNavigation();
  bindGlobalInteractions();
  bindTopBarInteractions();
  applyInitialRoute();   // keep the page from the URL (#/settings…) across reloads
  syncNavState();
  refreshIcons();
  try {
    const user = await api.me();
    currentUser = user;
    $('#currentUserLabel').textContent = user.displayName || user.username;
    showAppShell();
    await initAppData();
  } catch (err) {
    showLoginScreen();
  }
}

setupLoginOptions();
bootstrap();
