/* Cài đặt: assessment criteria, thresholds, company logo — moved out of app.js (audit FE-29). */

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
    let res = null;
    if (currentVcenterFilter === 'all' || currentVcenterFilter === 'import') {
      res = await syncAllSources();             // every source: vCenter, backup, storage, Dell OME
    } else {
      await api.syncVcenter(currentVcenterFilter);
    }
    await refreshVcenterList();
    await loadLiveData();
    if (res && res.failed) showValidationBanner([res.message], []);
    else showSuccessBanner(res ? res.message : `Đã đồng bộ vCenter ${currentVcenterFilter} lúc ${new Date().toLocaleString('vi-VN')}.`);
    if (typeof bkInvalidate === 'function') bkInvalidate();
    if (typeof renderHardwareView === 'function' && /^#\/hardware/.test(location.hash)) renderHardwareView();
  } catch (err) {
    showValidationBanner([`Đồng bộ thất bại: ${err.message}`], []);
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalLabel;
  }
}
