/* =========================================================
   AI cho dữ liệu nhiều biến số (super app SA5):
   A3 phân tích lỗi backup · A4 ánh xạ cột file nhập · A5 gợi ý phân loại HĐH · A6 tóm tắt biến động VM.
   Mọi kết quả là đề xuất: mức độ vẫn theo luật, ánh xạ / ghi đè chỉ áp dụng khi người dùng xác nhận.
   ========================================================= */

const AI_LEVEL = UI_LEVELS;   // the 4 standard levels (+ info), same words as every page
const AI_CONF = { high: ['ok', 'Cao'], medium: ['warn', 'Trung bình'], low: ['bad', 'Thấp'] };

/* Show the AI buttons of `root` whose task is on (hidden by default, so nothing flashes when AI is off). */
async function aiGate(root) {
  const els = (root || document).querySelectorAll('[data-ai-gate]');
  if (!els.length) return;
  const s = await aiStatus();
  els.forEach(el => el.classList.toggle('hidden', !(s.enabled && s.tasks[el.dataset.aiGate])));
}

function aiNote(cached) {
  return `<span class="muted-sub sr-inline">AI soạn${cached ? ' · dùng lại' : ''} — đối chiếu với số liệu trước khi quyết định</span>`;
}

function aiSummaryHtml(s, title) {
  return `<div class="home-ai">
    <div class="home-brief-head"><span class="eyebrow">${esc(title)}</span>${aiNote(s.cached)}</div>
    <p class="home-brief-text">${esc(s.headline)}</p>
    ${(s.points || []).length ? `<ul class="home-list">${s.points.map(p => `<li class="home-item"><div class="home-item-main">${uiPill(...(AI_LEVEL[p.level] || AI_LEVEL.info))} ${esc(p.text)}</div></li>`).join('')}</ul>` : ''}
    ${(s.decisions || []).length ? `<div class="sp-h">Việc nên làm</div><ol class="home-ai-dec">${s.decisions.map(x => `<li>${esc(x)}</li>`).join('')}</ol>` : ''}</div>`;
}

async function aiRunInto(btn, out, fn) {
  if (!out) return;
  btn.disabled = true;
  out.innerHTML = '<div class="empty-note">AI đang phân tích…</div>';
  try { out.innerHTML = await fn(); } catch (err) { out.innerHTML = `<p class="criteria-status error">${esc(err.message)}</p>`; }
  finally { btn.disabled = false; }
}

/* ---------------------------------------------------------------- A3 Backup → Tổng quan */
async function aiBackupAnalysis(btn) {
  const st = bkState();
  const q = new URLSearchParams({ server: st.server || 'all', days: String(st.days || 7) });
  await aiRunInto(btn, $('#bkAiOut'), async () => {
    const r = await apiRequest(`/ai/backup-analysis?${q}`, { method: 'POST' });
    if (r.empty) return uiEmpty(r.headline);
    return `<div class="home-ai">
      <div class="home-brief-head"><span class="eyebrow">Phân tích lỗi bằng AI</span>${aiNote(r.cached)}</div>
      <p class="home-brief-text">${esc(r.headline)}</p>
      ${r.causes.length ? `<div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th>Mức</th><th>Nguyên nhân gốc</th><th>Mã</th><th>Việc cần làm</th></tr></thead><tbody>
        ${r.causes.map(c => `<tr><td>${uiPill(...(AI_LEVEL[c.level] || AI_LEVEL.info))}</td><td class="wrap"><b>${esc(c.title)}</b><span class="muted-sub bk-wrap">${esc(c.explanation)}</span></td>
          <td>${c.codes.map(x => `<span class="pill na">${esc(x)}</span>`).join(' ') || '—'}</td><td class="wrap">${esc(c.action)}</td></tr>`).join('')}</tbody></table></div>` : ''}
      ${r.unknownCodes.length ? `<div class="sp-h">Mã lỗi chưa có trong bảng mã</div><ul class="home-list">${r.unknownCodes.map(u => `<li class="home-item"><div class="home-item-main"><b>Mã ${esc(u.code)}</b> — ${esc(u.meaning)}<span class="muted-sub">${esc(u.action)}</span></div></li>`).join('')}</ul>` : ''}
      ${r.priorities.length ? `<div class="sp-h">Thứ tự xử lý</div><ol class="home-ai-dec">${r.priorities.map(x => `<li>${esc(x)}</li>`).join('')}</ol>` : ''}</div>`;
  });
}

/* ---------------------------------------------------------------- A5 License → Phân loại OS */
let aiOsSuggest = [];
async function aiOsClassify(btn) {
  await aiRunInto(btn, $('#licAiOs'), async () => {
    const r = await apiRequest(`/ai/os-classify?${new URLSearchParams({ vcenter: licScope().vcenter || 'all' })}`, { method: 'POST' });
    aiOsSuggest = r.items;
    if (!r.total) return uiEmpty('Không có VM nào ở nhóm Other / Unknown chưa ghi đè.');
    const rows = r.items.map((s, i) => `<tr>
      <td><input type="checkbox" data-ai-os-pick="${i}" ${s.category !== 'other_unknown' && s.confidence === 'high' ? 'checked' : ''} ${s.category === 'other_unknown' ? 'disabled' : ''} aria-label="Chọn ${esc(s.vm)}"></td>
      <td><b>${esc(s.vm)}</b><span class="muted-sub">${esc(s.cluster || '')}</span></td>
      <td class="wrap">${esc(s.evidence.toolsGuestFullName || s.evidence.guestFullName || s.evidence.guestId || '—')}</td>
      <td>${esc(LIC_CATEGORY_LABEL[s.category] || s.category)}</td><td>${esc(s.osVersion || '—')}</td><td>${esc(s.osEdition || '—')}</td>
      <td>${uiPill(...(AI_CONF[s.confidence] || AI_CONF.low))}</td><td class="wrap">${esc(s.reason)}</td></tr>`).join('');
    return `<div class="home-ai">
      <div class="home-brief-head"><span class="eyebrow">Đề xuất phân loại HĐH (${esc(r.items.length)} / ${esc(r.total)} VM chưa rõ${r.sent < r.total ? ` — gửi ${esc(r.sent)} VM đầu, chạy lại sau khi ghi đè để xem tiếp` : ''})</span>${aiNote(r.cached)}</div>
      <div class="table-scroll table-scroll-flat"><table class="data-table"><thead><tr><th></th><th>VM</th><th>Bằng chứng</th><th>Phân loại</th><th>Phiên bản</th><th>Edition</th><th>Tin cậy</th><th>Lý do</th></tr></thead><tbody>${rows || `<tr><td colspan="8">${uiEmpty('AI không đưa ra đề xuất nào.')}</td></tr>`}</tbody></table></div>
      <div class="sim-actions"><button class="btn btn-primary btn-sm" type="button" data-ai-act="os-apply">Ghi đè các VM đã chọn</button>
        <span class="muted-sub sr-inline">Ghi đè ghi tên bạn và lý do của AI; sửa hoặc xoá được trong bảng bên dưới.</span></div>
      <p class="criteria-status" id="licAiOsMsg" role="status"></p></div>`;
  });
}

async function aiOsApply(btn) {
  const picks = [...document.querySelectorAll('[data-ai-os-pick]:checked')].map(c => aiOsSuggest[Number(c.dataset.aiOsPick)]).filter(Boolean);
  if (!picks.length) { uiSetStatus('#licAiOsMsg', 'Chưa chọn VM nào.', 'error'); return; }
  if (!(await uiConfirm(`Ghi đè phân loại HĐH cho ${picks.length} VM theo đề xuất của AI?`, { okLabel: 'Ghi đè', danger: false }))) return;
  btn.disabled = true;
  let ok = 0;
  const errs = [];
  for (const s of picks) {
    try {
      await apiRequest('/licensing/overrides', { method: 'PUT', body: JSON.stringify({ vcenterKey: s.vcenterKey, instanceUuid: s.instanceUuid, vm: s.vm,
        category: s.category, osVersion: s.osVersion, osEdition: s.osEdition, note: `AI đề xuất (tin cậy ${(AI_CONF[s.confidence] || AI_CONF.low)[1].toLowerCase()}): ${s.reason}`.slice(0, 480) }) });
      ok += 1;
    } catch (err) { errs.push(`${s.vm}: ${err.message}`); }
  }
  btn.disabled = false;
  await licLoad(true);
  renderLicBody();
  const msg = `Đã ghi đè ${ok} VM.${errs.length ? ` Lỗi: ${errs.slice(0, 3).join('; ')}` : ''}`;
  const out = $('#licAiOs');
  if (out) out.innerHTML = `<p class="criteria-status ${errs.length ? 'error' : 'ok'}">${esc(msg)}</p>`;
}

/* ---------------------------------------------------------------- A6 Lịch sử → biến động VM */
async function aiVmChanges(btn) {
  const mountId = btn.dataset.aiMount;
  const opts = historyMounts[mountId] || {};
  const p = { vcenter: historyVcenterParam(), months: String(historyState.months || 12) };
  const clusters = historyClustersParam(opts), hosts = historyHostsParam(opts);
  if (Array.isArray(clusters)) p.clusters = clusters.join(',');
  if (Array.isArray(hosts)) p.hosts = hosts.join(',');
  await aiRunInto(btn, document.getElementById(`${mountId}AiChanges`), async () => {
    const s = await apiRequest(`/ai/vm-changes?${new URLSearchParams(p)}`, { method: 'POST' });
    return s.empty ? uiEmpty(s.headline) : aiSummaryHtml(s, 'Tóm tắt biến động VM (AI)');
  });
}

/* ---------------------------------------------------------------- A4 ánh xạ cột khi nhập file */
function importLooksMapped(rows, target) {
  const sample = rows.slice(0, 5);
  return sample.every((r, i) => {
    const n = target === 'hosts' ? normalizeHostRow(r, i) : normalizeVmRow(r, i);
    return !n.errors.length && (target === 'hosts' ? n.row.cores > 0 : n.row.vcpu > 0);
  });
}

function importApplyMapping(rows, mapping, fields) {
  const col = Object.fromEntries(fields.map(f => [f.key, f.column]));
  return rows.map(r => {
    const o = { ...r };
    Object.entries(mapping).forEach(([f, h]) => { if (col[f] && h in r) o[col[f]] = r[h]; });
    return o;
  });
}

/* Called by loadFileInput: returns rows (mapped when needed) or null when the user cancels. */
async function importMapRows(rows, target, fileName) {
  if (!rows.length || importLooksMapped(rows, target)) return rows;
  const headers = [...new Set(rows.slice(0, 50).flatMap(r => Object.keys(r || {})))].slice(0, 200);
  let lk;
  try { lk = await apiRequest('/import-mappings/lookup', { method: 'POST', body: JSON.stringify({ target, headers }) }); }
  catch (err) { return rows; }
  if (lk.saved) {
    return importApplyMapping(rows, lk.saved.mapping, lk.fields);
  }
  const mapping = await importMapDialog(rows, target, headers, lk.fields, fileName);
  return mapping ? importApplyMapping(rows, mapping, lk.fields) : rows;
}

function importMapDialog(rows, target, headers, fields, fileName) {
  const ui = uiDialog('importMapDlg', { title: `Chọn cột cho file ${fileName || ''}` });
  const dlg = ui.el;
  const opts = (sel) => `<option value="">— không có —</option>${headers.map(h => `<option value="${esc(h)}"${h === sel ? ' selected' : ''}>${esc(h)}</option>`).join('')}`;
  ui.body.innerHTML = `<form method="dialog">
    <p class="muted-sub">Không nhận ra cột ${target === 'hosts' ? 'tên host / số core' : 'tên VM / vCPU'} trong file. Chọn cột tương ứng cho từng trường (trường có * là bắt buộc).</p>
    <div class="settings-form ai-grid">${fields.map(f => `<label class="field"><span>${esc(f.label)}${f.required ? ' *' : ''}</span><select data-map-field="${esc(f.key)}">${opts('')}</select></label>`).join('')}</div>
    <label class="finding-filter-item"><input type="checkbox" id="importMapSave" checked><span>Nhớ cách ánh xạ này cho các file cùng bộ cột</span></label>
    <p class="criteria-status" id="importMapMsg" role="status"></p>
    <div class="src-dialog-actions"><button class="btn btn-secondary btn-sm hidden" type="button" id="importMapAi" data-ai-gate="column_mapping">AI đề xuất</button>
      <span class="src-spacer"></span>
      <button class="btn btn-ghost btn-sm" value="no" type="submit">Bỏ qua</button>
      <button class="btn btn-primary btn-sm" value="yes" type="submit" id="importMapOk">Áp dụng</button></div></form>`;
  aiGate(dlg);
  let source = 'manual';
  const read = () => Object.fromEntries([...dlg.querySelectorAll('[data-map-field]')].filter(s => s.value).map(s => [s.dataset.mapField, s.value]));
  $('#importMapAi', dlg).onclick = async (e) => {
    const b = e.currentTarget; b.disabled = true;
    uiSetStatus('#importMapMsg', 'AI đang đọc tiêu đề cột…', 'ok');
    try {
      const r = await apiRequest('/ai/column-mapping', { method: 'POST', body: JSON.stringify({ target, headers, samples: rows.slice(0, 5) }) });
      dlg.querySelectorAll('[data-map-field]').forEach(s => { s.value = r.mapping[s.dataset.mapField] || ''; });
      source = 'ai';
      const low = Object.entries(r.confidence || {}).filter(([, c]) => c === 'low').map(([k]) => (fields.find(f => f.key === k) || {}).label);
      uiSetStatus('#importMapMsg', `AI đã điền ${Object.keys(r.mapping).length} trường — kiểm tra trước khi áp dụng.${low.length ? ` Chưa chắc: ${low.join(', ')}.` : ''} ${r.notes || ''}`, 'ok');
    } catch (err) { uiSetStatus('#importMapMsg', err.message, 'error'); }
    finally { b.disabled = false; }
  };
  return new Promise(resolve => {
    $('#importMapOk', dlg).onclick = (e) => {
      const m = read();
      const miss = fields.filter(f => f.required && !m[f.key]).map(f => f.label);
      if (miss.length) { e.preventDefault(); uiSetStatus('#importMapMsg', `Chưa chọn: ${miss.join(', ')}.`, 'error'); }
    };
    dlg.addEventListener('close', async () => {
      if (dlg.returnValue !== 'yes') { resolve(null); return; }
      const m = read();
      if ($('#importMapSave', dlg).checked) {
        // Applied anyway; a refusal (layout saved by someone else, cap reached) only means it is not remembered.
        try { await apiRequest('/import-mappings', { method: 'PUT', body: JSON.stringify({ target, headers, mapping: m, source }) }); } catch (err) { /* not remembered */ }
      }
      resolve(m);
    }, { once: true });
    dlg.returnValue = '';
    dlg.showModal();
  });
}

/* ---------------------------------------------------------------- one delegated handler for the AI buttons */
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-ai-act]');
  if (!b) return;
  const act = b.dataset.aiAct;
  if (act === 'backup') aiBackupAnalysis(b);
  else if (act === 'os') aiOsClassify(b);
  else if (act === 'os-apply') aiOsApply(b);
  else if (act === 'vm-changes') aiVmChanges(b);
});
