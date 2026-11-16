/* Quy hoạch tài nguyên: on-screen result and "Tạo Capex từ quy hoạch"; moved out of home.js (audit FE-35). */

/* ---------------------------------------------------------------- Quy hoạch tài nguyên: kết quả trên màn hình */
async function runPlanningPreview(btn) {
  const out = $('#planningResult');
  if (!out) return;
  let params;
  try { params = getPlanningParams(); } catch (err) { out.innerHTML = `${uiError(err.message)}`; return; }
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
      ${d.totals.map((t, i) => `<tr><td>${esc(sc[i].label || `${sc[i].growthPct}%/quý`)}${i === d.baseIndex ? ' ' + uiTag('cơ sở') : ''}</td>
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
  } catch (err) { out.innerHTML = `${uiError(err.message)}`; }
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
  uiPanelDialog('Tạo Capex nháp từ quy hoạch', body, `<span class="src-spacer"></span><button class="btn btn-ghost btn-sm" type="button" data-sp-close>Huỷ</button>
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
