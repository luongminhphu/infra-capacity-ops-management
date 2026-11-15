/* =========================================================
   Executive view — management/investment-decision perspective.
   Reuses the exact same capacity engine (buildCapacityModel,
   buildClusterRows, getStatusForPct, combineStatus, chartPalette,
   destroyChart) from core.js so numbers always match the
   Technical view; adds org-wide KPIs, historical trend +
   investment forecast, per-vCenter split, and top-risk clusters.
   ========================================================= */

/** Toggle a canvas + its sibling empty-state note without ever removing the
 *  canvas node, so repeated renders (toggling perspective back and forth)
 *  keep working. `hasData` controls which one is visible. */
function toggleChartEmptyState(canvasId, hasData, emptyMessage) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const wrap = canvas.closest('.chart-wrap');
  let note = wrap.querySelector('.chart-empty-note');
  if (!note) {
    note = document.createElement('div');
    note.className = 'empty-state chart-empty-note hidden';
    wrap.appendChild(note);
  }
  note.textContent = emptyMessage || '';
  canvas.classList.toggle('hidden', !hasData);
  note.classList.toggle('hidden', hasData);
}

function execKpiCard(label, value, sub, tone) {
  return `
    <article class="card exec-kpi">
      <div class="exec-label">${esc(label)}</div>
      <div class="exec-value ${tone || ''}">${value}</div>
      <div class="exec-sub">${esc(sub || '')}</div>
    </article>`;
}

function renderExecutiveKpis() {
  const clusters = buildClusterRows();
  const model = buildCapacityModel(appState.hosts, appState.vms);
  const findings = buildComplianceFindings();
  const critCount = findings.filter(f => f.severity === 'critical').length;
  const overCount = clusters.filter(c => c.overall.cls === 'crit' || c.overall.cls === 'bad').length;

  const box = $('#execKpis');
  box.className = 'kpi-strip-wrap';
  box.innerHTML = uiKpiStrip([
    { label: 'CPU so với ngưỡng overcommit', value: isFinite(model.cpuPct) ? `${fmtNum(model.cpuPct, 0)}%` : 'NA',
      sub: `${fmtInt(model.usedVcpu)} / ${fmtInt(model.totalLogicalCores)} pCPU logic · ${fmtInt(model.totalCores)} core × HT ${model.htFactor}x`,
      pct: model.cpuPct, status: getStatusForPct(model.cpuPct, 80, 100) },
    { label: 'RAM so với ngưỡng overcommit', value: isFinite(model.ramPct) ? `${fmtNum(model.ramPct, 0)}%` : 'NA',
      sub: `${fmtNum(model.usedRamGB, 0)} / ${fmtNum(model.totalRamGB, 0)} GB`, pct: model.ramPct, status: getStatusForPct(model.ramPct, 80, 100) },
    { label: 'Cluster cần đầu tư / mở rộng', value: String(overCount), unit: `/ ${clusters.length} cluster`,
      sub: overCount ? 'CẢNH BÁO hoặc VƯỢT NGƯỠNG' : 'Không cluster nào cần mở rộng' },
    { label: 'Phát hiện nghiêm trọng (compliance)', value: String(critCount), unit: `/ ${findings.length} phát hiện`,
      sub: critCount ? 'Xem tab Compliance' : 'Không có mục nghiêm trọng' },
  ]);
}

function renderExecutiveTopRisk() {
  const clusters = buildClusterRows()
    .slice()
    .sort((a, b) => Math.max(b.model.cpuPct || 0, b.model.ramPct || 0) - Math.max(a.model.cpuPct || 0, a.model.ramPct || 0))
    .slice(0, 6);

  $('#execTopRisk').innerHTML = uiSmallMultiples(clusters.map(c => ({
    name: c.name, status: c.overall, sub: `${c.hosts} host · ${c.vms} VM`,
    metrics: [{ label: 'CPU', pct: c.model.cpuPct }, { label: 'RAM', pct: c.model.ramPct }] })), { empty: 'Chưa có dữ liệu cluster.' });
}

function renderExecutiveForecastCard(elId, label, forecast, unavailableReason) {
  const el = $(elId);
  if (!forecast) {
    el.classList.remove('risk');
    el.innerHTML = `<h3 class="exec-fc-h">${esc(label)}</h3><p>${esc(unavailableReason || 'Chưa có dữ liệu dự báo.')}</p>`;
    return;
  }
  const isRisk = forecast.daysTo100Pct && forecast.daysTo100Pct <= 180;
  el.classList.toggle('risk', !!isRisk);
  el.innerHTML = `
    <h3 class="exec-fc-h">${esc(label)} — hiện tại ${fmtNum(forecast.currentPct, 0)}% công suất vật lý</h3>
    <p>${esc(forecast.insight)}</p>`;
}

async function renderExecutiveTrendChart() {
  if (typeof Chart === 'undefined') return;
  let trendData;
  try {
    trendData = await api.getTrend({ days: 90 });
  } catch (err) {
    trendData = { trend: [], forecast: { available: false, reason: 'Không tải được lịch sử.' } };
  }
  const colors = chartPalette();
  destroyChart('execTrend');

  const trend = trendData.trend || [];
  const labels = trend.map(t => t.date);
  const cpuUsedPct = trend.map(t => t.totalLogicalCores ? (t.usedVcpu / t.totalLogicalCores) * 100 : null);
  const ramUsedPct = trend.map(t => t.totalRamGB ? (t.usedRamGB / t.totalRamGB) * 100 : null);

  toggleChartEmptyState('chartExecTrend', labels.length > 0, 'Chưa có đủ lịch sử polling để vẽ xu hướng. Dữ liệu sẽ tích luỹ sau mỗi lần đồng bộ vCenter hoặc import.');
  if (labels.length) {
    appState.charts.execTrend = new Chart($('#chartExecTrend'), {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'CPU % công suất vật lý', data: cpuUsedPct, borderColor: colors.series[0], backgroundColor: colors.series[0], borderWidth: 2, pointRadius: 2, tension: .25, fill: false },
          { label: 'RAM % công suất vật lý', data: ramUsedPct, borderColor: colors.series[1], backgroundColor: colors.series[1], borderWidth: 2, pointRadius: 2, tension: .25, fill: false },
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          x: { ticks: { color: colors.axis, maxRotation: 0 }, grid: { display: false } },
          y: { beginAtZero: true, max: 100, ticks: { color: colors.axis }, grid: { color: colors.grid } },
        },
        plugins: { legend: { labels: { color: colors.text } } },
      }
    });
  }

  const forecast = trendData.forecast || {};
  renderExecutiveForecastCard('#execForecastCpu', 'Dự báo CPU', forecast.cpu, forecast.reason);
  renderExecutiveForecastCard('#execForecastRam', 'Dự báo RAM', forecast.ram, forecast.reason);
}

async function renderExecutiveByVcenterChart() {
  if (typeof Chart === 'undefined') return;
  destroyChart('execByVcenter');
  let grouped;
  try {
    grouped = await api.getLatestData({ group_by_vcenter: true });
  } catch (err) {
    grouped = { byVcenter: {} };
  }
  const byVcenter = grouped.byVcenter || {};
  const keys = Object.keys(byVcenter);
  if (!keys.length) { $('#execByVcenter').innerHTML = uiEmpty('Chưa có dữ liệu theo vCenter.'); return; }
  $('#execByVcenter').innerHTML = uiSmallMultiples(keys.map(k => {
    const m = buildCapacityModel(byVcenter[k].hosts || [], byVcenter[k].vms || []);
    return { name: k === 'import' ? 'Import thủ công' : k, sub: `${fmtInt(m.hostCount)} host · ${fmtInt(m.vmCount)} VM`,
      status: combineStatus(getStatusForPct(m.cpuPct, 80, 100), getStatusForPct(m.ramPct, 80, 100)),
      metrics: [{ label: 'CPU', pct: m.cpuPct }, { label: 'RAM', pct: m.ramPct }] };
  }), { legend: false });
}

/* ---- Management brief (headline, key points, recommendations, decision
   register). Fetched from the backend so the web view shows exactly the same
   wording as the Executive PDF; degrades gracefully when unavailable. ---- */
const PRIORITY_PILL_CLS = { cao: 'prio-cao', tb: 'prio-tb', thap: 'prio-thap', info: 'prio-info' };

function briefPriorityPill(rec) {
  const cls = PRIORITY_PILL_CLS[rec.priority] || 'prio-info';
  return `<span class="pill ${cls}">${esc(rec.priority_label || rec.priority || '')}</span>`;
}

function renderExecutiveBriefCard(brief) {
  const card = $('#execBrief');
  if (!card) return;
  if (!brief || !brief.available) {
    card.className = 'card exec-brief-card';
    card.innerHTML = `<div class="empty-state">${esc((brief && brief.message) || 'Chưa có dữ liệu để tổng hợp nhận định. Hãy đồng bộ vCenter hoặc import dữ liệu trước.')}</div>`;
    return;
  }
  card.className = `verdict tone-rule-${brief.health_cls || 'na'}`;
  const storageNote = brief.include_storage ? '' : '<span>Storage không đánh giá (SAN dùng chung)</span>';
  card.innerHTML = `
    <div class="verdict-meta">${uiPill(brief.health_cls || 'na', brief.health_word || '')}<span>${esc(brief.scope || '')}</span>${storageNote}</div>
    <h2>${esc(brief.headline || '')}</h2>
    <ul class="verdict-points">${(brief.points || []).map(p => `<li>${esc(p)}</li>`).join('')}</ul>
    <p class="verdict-decision"><strong>Quyết định cần phê duyệt:</strong> ${esc(brief.decision_line || '')}</p>`;
}

function renderExecutiveRecommendations(brief) {
  const box = $('#execRecommendations');
  const panel = $('#execRecommendationsPanel');
  if (!box) return;
  const recs = (brief && brief.available && brief.recommendations) || [];
  panel.classList.toggle('hidden', !recs.length);
  box.innerHTML = `<div class="rec-grid-web">${recs.map(r => `
    <article class="rec-card-web prio-${esc(r.priority)} tone-${esc(r.tone || 'neutral')}">
      <div class="rec-head"><span class="rec-num">${r.index}</span><div class="rec-title">${esc(r.title)}</div></div>
      <div class="rec-meta">${briefPriorityPill(r)}<span class="pill">${esc(r.timeframe || '')}</span></div>
      <div class="rec-block"><span class="rec-label">Điều này có nghĩa</span>${esc(r.meaning)}</div>
      <div class="rec-block"><span class="rec-label">Chúng tôi đề xuất</span>${esc(r.action)}</div>
      <div class="rec-block"><span class="rec-label">Nếu chưa hành động</span>${esc(r.impact)}</div>
      <div class="rec-decision"><strong>Đề nghị phê duyệt:</strong> ${esc(r.decision)}</div>
      ${recRefsHtml(r)}
    </article>`).join('')}</div>`;
}

/* "Đối soát" — ticket-style finding codes so each recommendation can be traced
   to concrete rows in the Compliance tab (same codes as both PDF reports). */
function recRefsHtml(r) {
  const refs = r.refs || {};
  const items = refs.items || [];
  const note = r.refNote || '';
  if (!items.length && !note) return '';
  const more = (refs.total || 0) - items.length;
  const chips = items.map(it => `<span class="ref-item"><button type="button" class="ref-code" data-code="${esc(it.code)}" title="Mở trang Compliance và lọc theo mã này">${esc(it.code)}</button> ${esc(it.entity)}${it.vcenterKey ? ` (${esc(it.vcenterKey)})` : ''}${refs.compact ? '' : `<span class="ref-metric"> — ${esc(it.metric || it.typeLabel || '')}</span>`}</span>`).join('');
  const moreHtml = more > 0 ? `<span class="ref-item muted">+${more} mã khác — xem trang Compliance</span>` : '';
  return `<div class="rec-refs"><strong>Đối soát:</strong> ${chips}${moreHtml}${note ? `<div class="ref-note">${esc(note)}</div>` : ''}</div>`;
}

document.addEventListener('click', (e) => {
  const btn = e.target.closest('.ref-code[data-code]');
  if (!btn) return;
  // Open the Compliance page and pre-fill the search box with the reconciliation code.
  if (typeof navigateTo === 'function') navigateTo('compliance');
  const search = $('#complianceSearch');
  if (search) { search.value = btn.dataset.code; search.dispatchEvent(new Event('input', { bubbles: true })); search.scrollIntoView({ block: 'center' }); }
});

function renderExecutiveDecisionRegister(brief) {
  const panel = $('#execDecisionPanel');
  const tbody = document.querySelector('#execDecisionTable tbody');
  if (!tbody) return;
  const recs = ((brief && brief.available && brief.recommendations) || []).filter(r => r.priority !== 'info');
  panel.classList.toggle('hidden', !recs.length);
  tbody.innerHTML = recs.map((r, i) => `
    <tr>
      <td>${i + 1}</td>
      <td class="decision-cell"><strong>${esc(r.decision)}</strong><span class="sub">${esc(r.title)}</span>${((r.refs || {}).items || []).length ? `<span class="sub mono">Mã đối soát: ${esc(((r.refs || {}).items || []).slice(0, 5).map(it => it.code).join(', '))}${((r.refs || {}).total || 0) > 5 ? ` (+${(r.refs.total) - 5})` : ''}</span>` : ''}</td>
      <td>${briefPriorityPill(r)}</td>
      <td>${esc(r.timeframe || '')}</td>
      <td class="decision-cell">${esc(r.impact)}</td>
    </tr>`).join('');
}

async function renderExecutiveBrief() {
  let brief = null;
  try {
    const vcenter = (typeof currentVcenterFilter !== 'undefined' && currentVcenterFilter !== 'import') ? currentVcenterFilter : 'all';
    brief = await api.getManagementBrief(vcenter, !!appState.policy.includeStorage);
  } catch (err) {
    brief = { available: false, message: `Không tải được nhận định: ${err.message || err}` };
  }
  renderExecutiveBriefCard(brief);
  renderExecutiveRecommendations(brief);
  renderExecutiveDecisionRegister(brief);
}

async function renderExecutiveView() {
  renderExecutiveKpis();
  renderExecutiveTopRisk();
  await Promise.all([renderExecutiveTrendChart(), renderExecutiveByVcenterChart(), renderExecutiveBrief(),
    renderHistoryPanel('execHistoryMount', { showVmChanges: false })]);
}
