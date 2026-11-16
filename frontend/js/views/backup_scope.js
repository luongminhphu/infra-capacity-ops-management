/* Backup scope (systems that must be backed up) — moved out of backup.js (audit FE-29). */

/* ---------- Backup scope (systems that must be backed up) ----------
   Tree vCenter → cluster → host; several clusters across several vCenters can be ticked.
   Saved as rules on the server, compressed to the highest fully-ticked level so a
   whole cluster / vCenter keeps covering hosts added later. */
const BK_SEP = '\u0001';
const bkHostKey = (vc, cl, h) => [vc, cl, h].join(BK_SEP);

function bkRuleMatch(rules, vc, cl, h) {
  return (rules || []).some(r => (r.vcenterKey || 'import') === vc && (!r.cluster || r.cluster === cl) && (!r.host || r.host === h));
}

function bkScopeStartEdit() {
  const st = bkState();
  const sc = st.scope;
  const checked = new Set();
  (sc.tree || []).forEach(v => v.clusters.forEach(c => c.hosts.forEach(h => {
    if (bkRuleMatch(sc.rules, v.vcenterKey, c.name, h.name)) checked.add(bkHostKey(v.vcenterKey, c.name, h.name));
  })));
  st.scopeEdit = { mode: sc.rules.length ? 'selected' : 'all', checked, open: new Set(), q: '' };
}

function bkScopeRules() {
  const st = bkState(), ed = st.scopeEdit;
  if (ed.mode === 'all') return [];
  const rules = [];
  (st.scope.tree || []).forEach(v => {
    const all = v.clusters.every(c => c.hosts.every(h => ed.checked.has(bkHostKey(v.vcenterKey, c.name, h.name))));
    if (all && v.clusters.length) { rules.push({ vcenterKey: v.vcenterKey }); return; }
    v.clusters.forEach(c => {
      const hs = c.hosts.filter(h => ed.checked.has(bkHostKey(v.vcenterKey, c.name, h.name)));
      if (hs.length && hs.length === c.hosts.length) rules.push({ vcenterKey: v.vcenterKey, cluster: c.name });
      else hs.forEach(h => rules.push({ vcenterKey: v.vcenterKey, cluster: c.name, host: h.name }));
    });
  });
  return rules;
}

function bkTriState(keys, checked) {
  const n = keys.filter(k => checked.has(k)).length;
  return n === 0 ? 'none' : (n === keys.length ? 'all' : 'some');
}

function bkScopeEditorHtml() {
  const st = bkState(), sc = st.scope, ed = st.scopeEdit;
  const q = ed.q.trim().toLowerCase();
  const dis = ed.mode === 'all' ? 'disabled' : '';
  const box = (attr, state, label, sub) => `<label class="bk-sc-item"><input type="checkbox" ${attr} ${state === 'all' ? 'checked' : ''} data-tri="${state}" ${dis}/><span>${label}</span>${sub ? `<span class="muted-sub">${sub}</span>` : ''}</label>`;
  const tree = (sc.tree || []).map(v => {
    const clusters = v.clusters.filter(c => !q || `${v.vcenterKey} ${v.name} ${c.name} ${c.hosts.map(h => h.name).join(' ')}`.toLowerCase().includes(q));
    if (!clusters.length) return '';
    const vKeys = v.clusters.flatMap(c => c.hosts.map(h => bkHostKey(v.vcenterKey, c.name, h.name)));
    const cl = clusters.map(c => {
      const cKeys = c.hosts.map(h => bkHostKey(v.vcenterKey, c.name, h.name));
      const ck = `${v.vcenterKey}${BK_SEP}${c.name}`;
      const open = ed.open.has(ck) || (q && c.hosts.some(h => h.name.toLowerCase().includes(q)) && !c.name.toLowerCase().includes(q));
      const hosts = open ? `<div class="bk-sc-hosts">${c.hosts.map(h => box(`data-sc-host="${esc(bkHostKey(v.vcenterKey, c.name, h.name))}"`,
        ed.checked.has(bkHostKey(v.vcenterKey, c.name, h.name)) ? 'all' : 'none', esc(h.name), `${h.vmsOn} VM đang chạy`)).join('')}</div>` : '';
      return `<div class="bk-sc-cluster"><button type="button" class="bk-sc-caret" data-sc-toggle="${esc(ck)}" title="Chọn từng host">${open ? '▾' : '▸'}</button>
        ${box(`data-sc-cl="${esc(ck)}"`, bkTriState(cKeys, ed.checked), `<b>${esc(c.name)}</b>`, `${c.hosts.length} host · ${c.vmsOn} VM đang chạy`)}${hosts}</div>`;
    }).join('');
    return `<div class="bk-sc-vc">${box(`data-sc-vc="${esc(v.vcenterKey)}"`, bkTriState(vKeys, ed.checked), `<b>${esc(v.name)}</b> <span class="muted">(${esc(v.vcenterKey)})</span>`, `${v.clusters.length} cluster · ${v.vmsOn} VM đang chạy`)}
      <div class="bk-sc-clusters">${cl}</div></div>`;
  }).join('');
  const rules = bkScopeRules();
  const nVm = (sc.tree || []).reduce((s, v) => s + v.clusters.reduce((s2, c) => s2 + c.hosts.reduce((s3, h) => s3 + (ed.mode === 'all' || ed.checked.has(bkHostKey(v.vcenterKey, c.name, h.name)) ? h.vmsOn : 0), 0), 0), 0);
  return `
    <div class="bk-sc-mode">
      <label><input type="radio" name="bkScMode" value="all" data-sc-mode ${ed.mode === 'all' ? 'checked' : ''}/> Tất cả hệ thống trong inventory vCenter</label>
      <label><input type="radio" name="bkScMode" value="selected" data-sc-mode ${ed.mode === 'selected' ? 'checked' : ''}/> Chỉ các vCenter / cluster / host được chọn (ví dụ production)</label>
    </div>
    <div class="panel-toolbar bk-sc-tools">
      <span class="search-box"><i data-lucide="search"></i><input type="text" data-sc-q placeholder="Lọc cluster / host theo tên, vd: prod" value="${esc(ed.q)}" ${dis}/></span>
      <button class="btn btn-secondary btn-sm" type="button" data-bk-action="scope-select-visible" ${dis}>Chọn các cluster đang hiện</button>
      <button class="btn btn-ghost btn-sm" type="button" data-bk-action="scope-clear" ${dis}>Bỏ chọn tất cả</button>
    </div>
    <div class="bk-sc-tree ${ed.mode === 'all' ? 'is-disabled' : ''}">${tree || uiNote('Không có cluster khớp bộ lọc.')}</div>
    <div class="bk-sc-foot">
      <span class="muted">${ed.mode === 'all' ? 'Mọi VM đang chạy đều được yêu cầu có backup.' : `${rules.length} mục phạm vi · khoảng ${bkNum(nVm)} VM đang chạy cần backup.`}
        Nên chọn theo cluster: chọn theo host sẽ bỏ sót VM khi DRS / vMotion chuyển VM sang host khác.</span>
      <span class="panel-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-bk-action="scope-cancel">Huỷ</button>
      <button class="btn btn-primary btn-sm" type="button" data-bk-action="scope-save">Lưu phạm vi</button></span>
    </div>`;
}

function bkScopePanel(cov) {
  const st = bkState();
  const editing = !!st.scopeEdit;
  const label = cov.scopeActive ? esc(cov.scopeLabel) : 'Toàn bộ hệ thống trong inventory vCenter';
  return `
    <section class="table-card bk-scope-card">
      <div class="panel-head panel-head-tight"><div><div class="panel-title" role="heading" aria-level="2">Phạm vi cần backup</div>
        <p class="panel-sub">Chỉ VM thuộc phạm vi này được tính độ phủ, đưa vào phát hiện và báo cáo PDF / Excel. Có thể chọn nhiều cluster trên nhiều vCenter.</p></div>
        ${editing ? '' : uiIsAdmin() ? `<div class="panel-toolbar"><button class="btn btn-secondary btn-sm" type="button" data-bk-action="scope-edit"><i data-lucide="filter"></i>Chỉnh phạm vi</button></div>` : `<span class="muted-sub">${ADMIN_ONLY_NOTE}</span>`}</div>
      ${editing ? `<div id="bkScopeEditor">${bkScopeEditorHtml()}</div>`
        : `<div class="bk-scope-summary">${cov.scopeActive ? bkPill('ok', 'Đang áp dụng') : bkPill('na', 'Chưa giới hạn')} <span>${label}</span>${cov.scopeActive ? `<span class="muted"> · ${bkNum(cov.outOfScope)} VM ngoài phạm vi không tính</span>` : ''}</div>`}
    </section>`;
}

function bkScopeRerender() {
  const el = $('#bkScopeEditor');
  if (!el) return;
  const qEl = el.querySelector('[data-sc-q]');
  const hadFocus = qEl && document.activeElement === qEl;
  const pos = hadFocus ? qEl.selectionStart : 0;
  el.innerHTML = bkScopeEditorHtml();
  el.querySelectorAll('input[data-tri="some"]').forEach(i => { i.indeterminate = true; });
  refreshIcons();
  if (hadFocus) { const n = el.querySelector('[data-sc-q]'); if (n) { n.focus(); try { n.setSelectionRange(pos, pos); } catch { /* ignore */ } } }
}

function bkScopeSetMany(keys, on) {
  const ed = bkState().scopeEdit;
  keys.forEach(k => (on ? ed.checked.add(k) : ed.checked.delete(k)));
}

function bkScopeKeysOf(vc, cl) {
  const out = [];
  (bkState().scope.tree || []).forEach(v => {
    if (v.vcenterKey !== vc) return;
    v.clusters.forEach(c => { if (cl === undefined || c.name === cl) c.hosts.forEach(h => out.push(bkHostKey(v.vcenterKey, c.name, h.name))); });
  });
  return out;
}
