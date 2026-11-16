/* Report scope filter (cluster / host) for PDF export — moved out of app.js (audit FE-29). */

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
  summary.textContent = checkedCount === all.length ? 'Tất cả loại phát hiện' : `Đang chọn ${checkedCount}/${all.length} loại`;
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
