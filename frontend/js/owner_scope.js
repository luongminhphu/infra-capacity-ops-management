/* =========================================================
   Owner filter (ICO v2 G2c): Hệ thống / Owner / Phòng ban / Dự án / Mức quan trọng, for the whole app.
   The filter is applied on the SERVER (scope.OwnerScope): the data the pages compute from (/api/data/latest)
   and every scoped report / list below get the same parameters, so core.js and capacity.py keep computing the
   same numbers on the same subset of VMs. Remembered per user (uiPref "ownerScope").
   ========================================================= */
const OWNER_SCOPED_PATHS = [/^\/data\/latest(\?|$)/, /^\/compliance\/findings(\?|$)/, /^\/export\/csv(\?|$)/,
  /^\/reports\/(pdf|brief|planning)(\?|$)/, /^\/dashboard\/executive(\?|$)/, /^\/backup\/(summary|report\/pdf|report\/xlsx)(\?|$)/, /^\/infra\/(snapshots|alarms)(\?|$)/,
  /^\/incidents(\/stats)?(\?|$)/];
const OWNER_SCOPE_KEYS = ['systems', 'owners', 'departments', 'projects', 'criticality'];
const ownerScope = { value: null, systems: null, serverLabel: '' };

function ownerScopeLoad() {
  let d = null;
  try { const raw = uiPref('ownerScope', ''); d = raw ? JSON.parse(raw) : null; } catch (err) { d = null; }
  ownerScope.value = d && OWNER_SCOPE_KEYS.some(k => Array.isArray(d[k])) ? d : null;
  ownerScopeRenderControls();
}

const ownerScopeActive = () => !!ownerScope.value;

/* Append the filter to a scoped API path ("/data/latest?…" or "/api/reports/pdf?…"); other paths are untouched. */
function ownerScopeApply(url) {
  if (!ownerScopeActive() || typeof url !== 'string') return url;
  const base = typeof API_BASE !== 'undefined' ? API_BASE : '/api';
  const rel = url.startsWith(base + '/') ? url.slice(base.length) : url;
  if (!OWNER_SCOPED_PATHS.some(rx => rx.test(rel))) return url;
  const [path, query] = url.split('?');
  const q = new URLSearchParams(query || '');
  OWNER_SCOPE_KEYS.forEach(k => { if (Array.isArray(ownerScope.value[k])) q.set(k, ownerScope.value[k].join(',')); });
  return `${path}?${q}`;
}

/* The server describes the filter it applied (SYS codes, not ids) with the page data: prefer that text. */
function ownerScopeSetServerLabel(label) {
  ownerScope.serverLabel = String(label || '').replace(/^Đang lọc theo owner — /, '');
  ownerScopeRenderControls();
}

function ownerScopeSummary() {
  const v = ownerScope.value;
  if (!v) return '';
  if (ownerScope.serverLabel) return ownerScope.serverLabel;
  const names = { systems: 'hệ thống', owners: 'owner', departments: 'phòng ban', projects: 'dự án', criticality: 'mức quan trọng' };
  return OWNER_SCOPE_KEYS.filter(k => Array.isArray(v[k])).map(k => {
    const vals = k === 'systems' && ownerScope.systems
      ? v[k].map(id => (ownerScope.systems.find(s => String(s.id) === String(id)) || {}).code || id) : v[k];
    return `${names[k]}: ${vals.slice(0, 3).join(', ')}${vals.length > 3 ? ` +${vals.length - 3}` : ''}`;
  }).join(' · ');
}

/* Topbar button + the "đang lọc theo owner" line under the page title. */
function ownerScopeRenderControls() {
  const btn = $('#ownerScopeBtn');
  if (btn) {
    btn.classList.toggle('is-narrowed', ownerScopeActive());
    $('#ownerScopeBtnText').textContent = ownerScopeActive() ? 'Owner: đang lọc' : 'Owner';
  }
  const bar = $('#ownerScopeBar');
  if (!bar) return;
  bar.classList.toggle('hidden', !ownerScopeActive());
  bar.innerHTML = ownerScopeActive()
    ? `<span>${uiTag('Đang lọc theo owner')} ${esc(ownerScopeSummary())} — Capacity, Compliance, Host &amp; VM, Backup, Sức khoẻ và báo cáo chỉ tính VM thuộc phạm vi này.</span>
       <button class="btn btn-ghost btn-sm" type="button" data-owner-scope-clear>Bỏ lọc</button>` : '';
}

async function ownerScopeApplyValue(value) {
  ownerScope.value = value && OWNER_SCOPE_KEYS.some(k => Array.isArray(value[k])) ? value : null;
  ownerScope.serverLabel = '';
  uiSetPref('ownerScope', ownerScope.value ? JSON.stringify(ownerScope.value) : '');
  ownerScopeRenderControls();
  if (appState.backup) appState.backup.lastKey = null;           // backup summary is cached per server / days
  if (typeof loadLiveData === 'function') await loadLiveData();  // refreshAll() re-renders the active page
}

async function ownerScopeDialog() {
  let systems;
  try { systems = (await apiRequest('/systems?limit=500')).items; } catch (err) { uiAlert(err.message); return; }
  ownerScope.systems = systems;
  if (!systems.length) { uiAlert('Chưa có hệ thống nào — tạo ở Quản lý thông tin → Hệ thống & Owner.'); return; }
  const cur = ownerScope.value || {};
  const uniq = (arr) => [...new Set(arr.filter(Boolean))].sort((a, b) => a.localeCompare(b, 'vi'));
  const lists = {
    systems: systems.map(s => [String(s.id), `${s.code} — ${s.name}`]),
    owners: uniq(systems.flatMap(s => [s.ownerEmail, s.deputyEmail])).map(e => [e, e]),
    departments: uniq(systems.map(s => s.department)).map(d => [d, d]),
    projects: uniq(systems.map(s => s.project)).map(p => [p, p]),
    criticality: [['1', '1 — Rất quan trọng'], ['2', '2 — Quan trọng'], ['3', '3 — Trung bình'], ['4', '4 — Thấp']],
  };
  const titles = { systems: 'Hệ thống', owners: 'Owner / người thay thế', departments: 'Phòng ban', projects: 'Dự án', criticality: 'Mức quan trọng' };
  const group = (k) => {
    const on = new Set((cur[k] || []).map(String));
    return `<fieldset class="osc-group"><legend>${esc(titles[k])}</legend>
      ${lists[k].length > 8 ? `<input type="search" class="osc-search" data-osc-search="${k}" placeholder="Tìm…" aria-label="Tìm ${esc(titles[k])}">` : ''}
      <div class="osc-list" data-osc-list="${k}">${lists[k].map(([v, t]) => `<label class="infra-check"><input type="checkbox" data-osc="${k}" value="${esc(v)}"${on.has(String(v)) ? ' checked' : ''}> ${esc(t)}</label>`).join('')
        || '<span class="muted-sub">Chưa có giá trị.</span>'}</div></fieldset>`;
  };
  uiPanelDialog('Lọc theo hệ thống / owner', `
    <p class="muted-sub">Chọn trong nhiều nhóm thì VM phải khớp mọi nhóm; nhiều lựa chọn trong một nhóm khớp bất kỳ. Không chọn gì trong một nhóm = không lọc theo nhóm đó.</p>
    <div class="osc-grid">${OWNER_SCOPE_KEYS.map(group).join('')}</div>`,
  `<button class="btn btn-ghost btn-sm" type="button" id="oscClear">Bỏ lọc</button><span class="src-spacer"></span>
   <button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
   <button class="btn btn-primary btn-sm" type="button" id="oscApply">Áp dụng</button>`);
  $('#spDialogBody').oninput = (e) => {
    const k = e.target.dataset.oscSearch;
    if (!k) return;
    const q = e.target.value.trim().toLowerCase();
    $$(`[data-osc-list="${k}"] label`).forEach(l => { l.classList.toggle('hidden', !!q && !l.textContent.toLowerCase().includes(q)); });
  };
  $('#oscClear').onclick = async () => { $('#spDialog').close(); await ownerScopeApplyValue(null); };
  $('#oscApply').onclick = async () => {
    const v = {};
    OWNER_SCOPE_KEYS.forEach(k => { const picked = $$(`[data-osc="${k}"]`).filter(c => c.checked).map(c => c.value); if (picked.length) v[k] = picked; });
    $('#spDialog').close();
    await ownerScopeApplyValue(Object.keys(v).length ? v : null);
  };
}

document.addEventListener('click', (e) => {
  if (e.target.closest('#ownerScopeBtn')) { ownerScopeDialog(); return; }
  if (e.target.closest('[data-owner-scope-clear]')) ownerScopeApplyValue(null);
});
