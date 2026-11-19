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
    ['Người dùng & bảo mật', ['set-users', 'set-owners', 'set-reviews', 'set-auth']], ['Chi phí & giao diện', ['set-spend', 'set-brand']]],
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
  const os = $('#ownerShell'); if (os) os.classList.add('hidden');
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
  if (user.accessScope === 'owner') { showOwnerShell(user); refreshIcons(); return; }   // owner portal only (G2c)
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
          return `<tr><td>${esc(v.name)}<span class="sub muted-sub">${esc(v.key || '')}</span></td><td>${uiPill(pill, i.statusText)}${v.lastError ? `<div class="muted-sub">${esc(v.lastError)}</div>` : ''}</td><td>${esc(i.when)}</td></tr>`;
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
    // Where the numbers come from is information, not a warning: one short line, the per-source times on
    // demand (MASTER.md, trang-chu.md; was a 2-line sentence on phones).
    showValidationBanner([], []);
    renderSourceMeta(data.runMeta || []);
    if (typeof ownerScopeSetServerLabel === 'function') ownerScopeSetServerLabel(data.ownerScope);
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
  if (typeof ownerScopeLoad === 'function') ownerScopeLoad();     // owner filter remembered per user (before data loads)
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
    if (user.accessScope === 'owner') { showOwnerShell(user); refreshIcons(); return; }  // owner portal only (G2c)
    $('#currentUserLabel').textContent = user.displayName || user.username;
    showAppShell();
    await initAppData();
  } catch (err) {
    showLoginScreen();
  }
}

setupLoginOptions();
bootstrap();


/* ---------- Data source line ("Cập nhật …") ---------- */
function renderSourceMeta(runMeta) {
  const banner = $('#validationBanner');
  if (!banner || !runMeta.length) return;
  const name = r => (r.vcenterKey === 'import' ? 'import thủ công' : r.vcenterKey);
  const when = r => (r.finishedAt ? new Date(r.finishedAt) : null);
  const times = runMeta.map(when).filter(Boolean);
  const oldest = times.length ? new Date(Math.min(...times)) : null;      // the stalest source is the one that matters
  const short = d => d.toLocaleString('vi-VN', { hour12: false, hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' });
  const summary = oldest
    ? `Dữ liệu cập nhật ${short(oldest)}${runMeta.length > 1 ? ` · ${runMeta.length} nguồn` : ` · ${name(runMeta[0])}`}`
    : 'Dữ liệu: chưa rõ giờ cập nhật';
  const detail = runMeta.map(r => `${name(r)} (${when(r) ? when(r).toLocaleString('vi-VN') : 'chưa rõ giờ'})`).join(', ');
  banner.classList.add('show', 'info');
  banner.classList.remove('error', 'warn');
  banner.innerHTML = `<details class="src-meta"><summary>${esc(summary)}</summary><span>${esc(detail)}</span></details>`;
}

/* ---------- Scope toggle on phones ---------- */
function updateScopeToggle() {
  const box = $('#topbarScope'), txt = $('#scopeToggleText');
  if (!box || !txt) return;
  // selects the current page uses (the others carry .hidden); "Tất cả …" adds nothing to the summary
  const used = [...box.querySelectorAll('select')].filter(sel => !sel.classList.contains('hidden'));
  const btn = $('#scopeToggle');
  if (btn && btn.classList.contains('hidden') !== !used.length) btn.classList.toggle('hidden', !used.length);   // page without scope: no button
  const parts = used
    .map(sel => (sel.options[sel.selectedIndex] || {}).text || '').filter(t => t && !/^Tất cả/.test(t));
  const next = parts.length ? `Phạm vi: ${parts.join(' · ')}` : 'Phạm vi: tất cả';
  if (txt.textContent !== next) txt.textContent = next;      // unchanged text: no DOM write, so the observer below settles
}
document.addEventListener('DOMContentLoaded', () => {
  const box = $('#topbarScope'), btn = $('#scopeToggle');
  if (btn && box) {
    btn.addEventListener('click', () => {
      const open = !box.classList.contains('is-open');
      box.classList.toggle('is-open', open);
      btn.setAttribute('aria-expanded', String(open));
      if (open) { const first = box.querySelector('select:not(.hidden)'); if (first) first.focus(); }
    });
    box.addEventListener('change', updateScopeToggle);
    new MutationObserver(updateScopeToggle).observe(box, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
    updateScopeToggle();
  }
  const tabs = $('#pageTabs');
  if (tabs) {
    const mark = () => tabs.classList.toggle('at-end', tabs.scrollLeft + tabs.clientWidth >= tabs.scrollWidth - 4);
    tabs.addEventListener('scroll', mark, { passive: true });
    new MutationObserver(mark).observe(tabs, { childList: true });
    mark();
  }
});
