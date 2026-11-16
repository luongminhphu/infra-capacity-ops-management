/* Sizing máy chủ — tuỳ chỉnh báo cáo sau khi tính, logo riêng, xuất PDF / Excel (audit FE-28).
   Loaded right after views/sizing.js (state, params, VM table, profiles, run, view bindings). */

/* ---------- Report customisation after the calculation ---------- */
function sizingDefaultReport() {
  return { pick: '', hosts: 0, selectionNote: '', sections: {}, headline: '', note: '', hiddenFindings: [], extraFindings: [] };
}

const SZ_QUICK = [
  ['cpu_ratio', 'Tỷ lệ chia sẻ CPU (vCPU / thread)', 1, 8, 0.5, v => `${v}`],
  ['ram_ratio', 'Tỷ lệ chia sẻ RAM', 1, 2, 0.1, v => `${v} : 1`],
  ['max_util_pct', 'Mức dùng tối đa mỗi host', 50, 95, 5, v => `${v}%`],
  ['growth_pct', 'Dự phòng tăng trưởng', 0, 100, 5, v => `+${v}%`],
  ['annual_growth_pct', 'Tăng trưởng hằng năm (dự báo)', 0, 50, 5, v => `${v}%/năm`],
];
const SZ_SECTIONS = [
  ['scope', 'Phạm vi & dữ liệu đầu vào'], ['demand', 'Nhu cầu tài nguyên'], ['options', 'So sánh phương án'],
  ['scores', 'Bảng điểm', 'options'], ['design', 'Cấu hình đề xuất'],
  ['bom', 'BOM sơ bộ', 'design'], ['arch', 'Kiến trúc logic', 'design'], ['alloc', 'Phân bổ năng lực', 'design'],
  ['tco', 'Chi phí & TCO'], ['scale', 'Khả năng mở rộng'], ['risks', 'Rủi ro & khuyến nghị'], ['plan', 'Kế hoạch triển khai'],
  ['method', 'Giả định & cách tính'], ['existing', 'Hạ tầng hiện hữu (chế độ tận dụng)'], ['vms', 'Phụ lục: Danh sách máy ảo'],
  ['glossary', 'Phụ lục: Thuật ngữ'],
];
let szRecalcTimer = null;
function szRecalcSoon() {
  clearTimeout(szRecalcTimer);
  szRecalcTimer = setTimeout(() => runSizing(null, false), 250);
}

function renderSizingAdjust(result) {
  const box = $('#sizingAdjust');
  if (!box) return;
  const st = sizingState();
  const rep = st.report || (st.report = sizingDefaultReport());
  const p = st.params;
  const sel = result.selection || {};
  const rows = result.results || [];
  const best = result.recommendedIndex != null ? rows[result.recommendedIndex] : null;
  const curKey = rep.pick || '';
  const hostsMin = best ? (best.hostsComputed || best.hostsTotal) : 1;
  const tag = r => (r.suggested ? ' · tham khảo' : '') + (r.tight ? ' · sát ngưỡng' : '') + (!r.failure.ok ? ' · chưa đạt N+1' : '');
  const opts = [`<option value="" ${!curKey ? 'selected' : ''}>Tự động — ${esc(sel.autoName || '—')} (${sel.autoHosts || '—'} host)</option>`]
    .concat(rows.filter(r => r.hostsTotal).map(r => `<option value="${esc(r.key)}" ${r.key === curKey ? 'selected' : ''}>${esc(r.profile.name.replace(' (gợi ý)', ''))} — ${r.hostsComputed || r.hostsTotal} host${tag(r)}</option>`)).join('');
  const quick = SZ_QUICK.map(([k, label, min, max, step, fmt]) => `
    <div class="sz-adj-row"><span class="sz-adj-label">${label}</span>
      <input type="range" data-quick="${k}" min="${min}" max="${max}" step="${step}" value="${esc(p[k])}"><output data-out="${k}">${esc(fmt(p[k]))}</output></div>`).join('')
    + `<div class="sz-adj-row"><span class="sz-adj-label">Dự phòng sự cố</span>
      <select data-quick="redundancy">${[0, 1, 2].map(x => `<option value="${x}" ${Number(p.redundancy) === x ? 'selected' : ''}>N+${x}</option>`).join('')}</select><span></span></div>`;
  const secs = rep.sections || {};
  const cb = (k, label, sub) => `<label class="${sub ? 'sub' : ''}"><input type="checkbox" data-sec="${k}" ${secs[k] === false ? '' : 'checked'}> ${label}</label>`;
  const checks = SZ_SECTIONS.filter(x => !x[2]).map(([k, label]) =>
    `<div class="sz-adj-group">${cb(k, label)}${SZ_SECTIONS.filter(x => x[2] === k).map(([ck, cl]) => cb(ck, cl, true)).join('')}</div>`).join('');
  const hidden = new Set(rep.hiddenFindings || []);
  const SEV = { critical: ['crit', 'Nghiêm trọng'], warning: ['warn', 'Cảnh báo'], info: ['na', 'Lưu ý'], ok: ['ok', 'Đạt'] };
  const fl = ((result.analysis || {}).findings || []).map(f => `<label class="${hidden.has(f.id) ? 'off' : ''}"><input type="checkbox" data-finding="${esc(f.id)}" ${hidden.has(f.id) ? '' : 'checked'}>
      ${uiPill(SEV[f.severity][0], SEV[f.severity][1])}<span class="t">${esc(f.title)}</span></label>`).join('');
  const extras = (rep.extraFindings || []).map((x, i) => `<label>${uiPill((SEV[x.severity] || SEV.info)[0], (SEV[x.severity] || SEV.info)[1])}
      <span class="t">${esc(x.title)}</span><button class="btn btn-ghost btn-sm" type="button" data-del-extra="${i}" title="Xoá">✕</button></label>`).join('');
  const changed = rep.pick || rep.hosts || rep.headline || rep.note || (rep.hiddenFindings || []).length || (rep.extraFindings || []).length
    || Object.values(rep.sections || {}).some(v => v === false);
  box.innerHTML = `
    <div class="sz-adj-head"><div><div class="panel-title panel-title-sm">Tuỳ chỉnh báo cáo</div>
      <p class="panel-sub">Điều chỉnh sau khi tính: giả định và phương án áp dụng cho kết quả trên màn hình, PDF và Excel; mục in và nội dung áp dụng cho PDF báo cáo đầy đủ.</p></div>
      <button class="btn btn-secondary btn-sm" type="button" id="btnSzAdjReset" ${changed ? '' : 'disabled'}>Khôi phục mặc định</button></div>
    <div class="sz-adj-grid">
      <details class="sz-adj-box" open><summary>Chỉnh nhanh giả định</summary>${quick}
        <p class="logo-upload-hint">Thả tay để tính lại; giá trị đồng bộ với cột Giả định bên trái.</p></details>
      <details class="sz-adj-box" open><summary>Phương án & số host trong báo cáo</summary>
        <div class="field"><label>Phương án đề xuất</label><select id="szAdjPick">${opts}</select></div>
        <div class="field"><label>Số host đề xuất (tối thiểu ${hostsMin} theo tính toán)</label>
          <input id="szAdjHosts" type="number" min="0" step="1" value="${rep.hosts || ''}" placeholder="${hostsMin} (theo tính toán)"></div>
        <div class="field"><label>Lý do điều chỉnh (ghi vào báo cáo)</label><input id="szAdjSelNote" value="${esc(rep.selectionNote || '')}" placeholder="VD: theo hợp đồng khung phần cứng hiện có"></div>
        ${sel.manual ? `<p class="logo-upload-hint"><b>Đang dùng lựa chọn của người lập</b> — tự động là ${esc(sel.autoName || '')} × ${sel.autoHosts} host.</p>` : ''}</details>
      <details class="sz-adj-box" open><summary>Mục in trong báo cáo</summary><div class="sz-adj-checks">${checks}</div>
        <p class="logo-upload-hint">Mục Tóm tắt luôn được in; số mục, mục lục và chữ cái phụ lục tự đánh lại.</p></details>
      <details class="sz-adj-box" open><summary>Nội dung tư vấn</summary>
        <div class="field"><label>Câu kết luận (để trống = tự động)</label><textarea id="szAdjHeadline" placeholder="${esc((result.recommendation || {}).headline || '')}">${esc(rep.headline || '')}</textarea></div>
        <div class="field"><label>Ghi chú của người lập (in ở mục Tóm tắt)</label><textarea id="szAdjNote" placeholder="VD: điều kiện, cam kết, lưu ý riêng cho khách hàng">${esc(rep.note || '')}</textarea></div>
        <div class="sz-adj-label" style="margin-bottom:4px;">Khuyến nghị in trong báo cáo (bỏ chọn để ẩn)</div>
        <div class="sz-adj-findings">${fl}${extras}</div>
        <details><summary class="muted-sub">+ Thêm khuyến nghị riêng</summary>
          <div class="sz-adj-extra" style="margin-top:6px;">
            <select id="szExSev"><option value="warning">Cảnh báo</option><option value="info" selected>Lưu ý</option><option value="critical">Nghiêm trọng</option><option value="ok">Đạt</option></select>
            <input id="szExArea" placeholder="Lĩnh vực (VD: Mạng)">
            <input id="szExTitle" class="full" placeholder="Nhận định (bắt buộc)">
            <input id="szExDetail" class="full" placeholder="Chi tiết">
            <input id="szExAction" class="full" placeholder="Khuyến nghị">
            <button class="btn btn-secondary btn-sm" type="button" id="btnSzExAdd">Thêm</button></div></details></details>
    </div>`;
  box.classList.remove('hidden');
}

function bindSizingAdjust() {
  const box = $('#sizingAdjust');
  if (!box || box.dataset.bound) return;
  box.dataset.bound = '1';
  const st = () => sizingState();
  const rep = () => (st().report || (st().report = sizingDefaultReport()));
  box.addEventListener('input', e => {
    const k = e.target.dataset.quick;
    if (k && e.target.type === 'range') {
      const def = SZ_QUICK.find(q => q[0] === k);
      const out = box.querySelector(`[data-out="${k}"]`);
      if (out && def) out.textContent = def[5](e.target.value);
    }
  });
  box.addEventListener('change', e => {
    const t = e.target;
    if (t.dataset.quick) {
      const k = t.dataset.quick;
      const v = k === 'redundancy' ? Math.round(cleanNum(t.value)) : cleanNum(t.value);
      st().params[k] = v;
      const el = document.getElementById(SIZING_PARAM_FIELDS[k]);
      if (el) el.value = v;
      persistSizing(); szRecalcSoon(); return;
    }
    if (t.id === 'szAdjPick') { rep().pick = t.value; rep().hosts = 0; persistSizing(); szRecalcSoon(); return; }
    if (t.id === 'szAdjHosts') { rep().hosts = Math.max(0, Math.round(cleanNum(t.value)) || 0); persistSizing(); szRecalcSoon(); return; }
    if (t.id === 'szAdjSelNote') { rep().selectionNote = t.value.trim(); persistSizing(); return; }
    if (t.id === 'szAdjHeadline') { rep().headline = t.value.trim(); persistSizing(); return; }
    if (t.id === 'szAdjNote') { rep().note = t.value.trim(); persistSizing(); return; }
    if (t.dataset.sec) { rep().sections = { ...(rep().sections || {}), [t.dataset.sec]: t.checked }; persistSizing(); return; }
    if (t.dataset.finding) {
      const set = new Set(rep().hiddenFindings || []);
      if (t.checked) set.delete(t.dataset.finding); else set.add(t.dataset.finding);
      rep().hiddenFindings = [...set];
      t.closest('label').classList.toggle('off', !t.checked);
      persistSizing();
    }
  });
  box.addEventListener('click', e => {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.id === 'btnSzExAdd') {
      const title = $('#szExTitle').value.trim();
      if (!title) { $('#szExTitle').focus(); return; }
      rep().extraFindings = (rep().extraFindings || []).concat([{ severity: $('#szExSev').value, area: $('#szExArea').value.trim() || 'Khác', title,
        detail: $('#szExDetail').value.trim(), action: $('#szExAction').value.trim() }]);
      persistSizing(); renderSizingAdjust(st().result); return;
    }
    if (t.dataset.delExtra != null) {
      rep().extraFindings = (rep().extraFindings || []).filter((_, i) => i !== Number(t.dataset.delExtra));
      persistSizing(); renderSizingAdjust(st().result); return;
    }
    if (t.id === 'btnSzAdjReset') {
      const hadSelection = rep().pick || rep().hosts;
      st().report = sizingDefaultReport();
      persistSizing();
      if (hadSelection) szRecalcSoon(); else renderSizingAdjust(st().result);
    }
  });
}

/* ---------- Per-report logo override (falls back to the default report logo) ---------- */
const SIZING_LOGO_KEY = 'vco.sizing.logo.v1';
const SZ_MAX_LOGO_BYTES = 2 * 1024 * 1024;
function sizingLogoOverride() {
  const st = sizingState();
  if (st.logoOverride === undefined) {
    try { st.logoOverride = JSON.parse(localStorage.getItem(SIZING_LOGO_KEY) || 'null'); } catch (e) { st.logoOverride = null; }
  }
  return st.logoOverride;
}
function setSizingLogoOverride(val) {
  sizingState().logoOverride = val;
  try { val ? localStorage.setItem(SIZING_LOGO_KEY, JSON.stringify(val)) : localStorage.removeItem(SIZING_LOGO_KEY); } catch (e) { /* too large for storage: keep for this session */ }
}
async function refreshSizingLogoPreview() {
  const img = $('#szLogoImg'), empty = $('#szLogoEmpty'), hint = $('#szLogoHint'), reset = $('#btnSzLogoReset');
  if (!img) return;
  const ov = sizingLogoOverride();
  let src = ov ? ov.dataUrl : null;
  let note = ov ? `Logo riêng cho báo cáo này: ${ov.name || 'ảnh đã chọn'}.` : '';
  if (!src) {
    try { const res = await api.getLogo(); src = res && res.dataUrl ? res.dataUrl : null; } catch (e) { src = null; }
    note = src ? 'Đang dùng logo mặc định trong Cài đặt báo cáo.' : 'Chưa có logo mặc định — PDF sẽ không có logo.';
  }
  img.classList.toggle('hidden', !src);
  empty.classList.toggle('hidden', !!src);
  if (src) img.src = src; else img.removeAttribute('src');
  reset.classList.toggle('hidden', !ov);
  hint.textContent = `${note} Không chọn → dùng logo mặc định. PNG / JPEG / SVG / WEBP, tối đa 2MB; áp dụng cho PDF và Excel Sizing (logo SVG chỉ hiển thị trên PDF).`;
}
async function onSizingLogoFile(file) {
  if (!file) return;
  if (!/^image\/(png|jpeg|svg\+xml|webp)$/i.test(file.type)) { szPdfStatus('Logo phải là PNG, JPEG, SVG hoặc WEBP.', true); return; }
  if (file.size > SZ_MAX_LOGO_BYTES) { szPdfStatus('Logo vượt quá 2MB cho phép.', true); return; }
  const dataUrl = await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = () => reject(new Error('Không đọc được tệp ảnh.')); r.readAsDataURL(file); });
  setSizingLogoOverride({ dataUrl, name: file.name });
  szPdfStatus('');
  refreshSizingLogoPreview();
}

function szPdfStatus(msg, isError = false) {
  // Inline status inside the options panel — the global banner sits at the top of the page and is easy to miss here.
  uiSetStatus('#szPdfStatus', msg, isError ? 'error' : '', { hideWhenEmpty: true });
}

async function exportSizingPdf(btn) {
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = 'Đang tạo PDF…';
  szPdfStatus('Đang tổng hợp dữ liệu và tạo PDF…');
  try {
    const payload = sizingExistingPayload();
    if (!payload.vms.length) {
      szPdfStatus('Danh sách VM trống hoặc chưa có dòng hợp lệ (cần số lượng > 0 và vCPU/RAM > 0) — chưa có gì để lập đề xuất.', true);
      return;
    }
    await api.downloadSizingPdf(payload);
    szPdfStatus('');
    const dd = $('#sizingPdfDropdown'); if (dd) dd.removeAttribute('open');
  } catch (err) {
    console.error('Sizing PDF export failed', err);
    const msg = `Không tạo được PDF: ${err && err.message ? err.message : err}`;
    szPdfStatus(msg, true);
    showValidationBanner([msg], []);
  } finally { btn.disabled = false; btn.textContent = label; }
}

async function exportSizingXlsx(btn) {
  const payload = { ...sizingPayload(), ...sizingDocPayload() };
  if (!payload.vms.length) { showValidationBanner(['Danh sách VM trống — chưa có gì để xuất.'], []); return; }
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = 'Đang tạo Excel…';
  try { await api.downloadSizingXlsx(payload); }
  catch (err) { showValidationBanner([`Không xuất được Excel: ${err.message}`], []); }
  finally { btn.disabled = false; btn.textContent = label; }
}

