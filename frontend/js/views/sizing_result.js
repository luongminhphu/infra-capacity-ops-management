/* Sizing máy chủ — kết quả: bảng phương án, nhận định, biểu đồ, TCO (audit FE-28).
   Loaded right after views/sizing.js (state, params, VM table, profiles, run, view bindings). */

/* ---------- Result rendering ---------- */
function renderSizingResult(result, payload, scroll = true) {
  const area = $('#sizingResult');
  const d = result.demand;
  const rows = result.results;
  const best = result.recommendedIndex != null ? rows[result.recommendedIndex] : null;
  const p = result.params;
  const anyPrice = rows.some(r => r.budget != null);
  const local = p.storage_mode === 'local';

  const tiles = `
    <div class="kpi-strip sz-kpis">
      <div class="kpi-cell"><div class="kpi-l">Số VM yêu cầu</div><div class="kpi-v">${szNum(d.vmCount)}</div><div class="kpi-s">${payload.vms.length} dòng · TB ${szNum(d.avgVcpu, 1)} vCPU / ${szGB(d.avgRamGB)} / VM</div></div>
      <div class="kpi-cell"><div class="kpi-l">vCPU</div><div class="kpi-v">${szNum(d.vcpu)} <span class="sz-arrow">→</span> ${szNum(d.vcpuPlanned)}</div><div class="kpi-s">sau +${szNum(p.growth_pct)}% tăng trưởng · cần ${szNum(d.logicalCpuNeeded)} pCPU logic @ ${p.cpu_ratio}x</div></div>
      <div class="kpi-cell"><div class="kpi-l">RAM</div><div class="kpi-v">${szGB(d.ramGB)} <span class="sz-arrow">→</span> ${szGB(d.ramPlannedGB)}</div><div class="kpi-s">sau tăng trưởng · RAM vật lý cần ${szGB(d.physRamNeededGB)} @ ${p.ram_ratio}x</div></div>
      <div class="kpi-cell"><div class="kpi-l">${local ? 'Lưu trữ cục bộ cần' : 'Dung lượng SAN cần cấp'}</div><div class="kpi-v">${szGB(d.storageNeededGB)}</div><div class="kpi-s">từ ${szGB(d.diskGB)} yêu cầu · +${szNum(p.growth_pct)}% tăng trưởng · +${szNum(p.storage_overhead_pct)}% overhead${local ? ` · ×${p.storage_protection_factor} bảo vệ` : ''}</div></div>
    </div>`;

  const cmpRows = rows.map(r => {
    const pr = r.profile;
    const failTxt = r.failure.hostsLeft ? `${szNum(r.failure.cpuPct)}% / ${szNum(r.failure.ramPct)}%` : '—';
    const failPill = r.redundancy === 0 ? uiPill('na', 'N')
      : (!r.failure.ok ? uiPill('crit', 'Chưa đạt') : (r.failure.atLimit ? uiPill('warn', 'Sát ngưỡng') : `${uiPill('ok', `Đạt N+${r.redundancy}`)}`));
    const status = r.suggested ? uiPill('na', 'Tham khảo', 'Phương án tham khảo do hệ thống gợi ý')
      : (r.recommended ? uiPill('ok', 'Đề xuất') : (r.tight ? uiPill('warn', 'Sát ngưỡng') : `<span class="muted-sub">#${r.rank}</span>`));
    const storageCell = local
      ? `${szGB(r.storage.perHostNeededGB)}/host ${r.storage.ok === false ? uiPill('crit', 'thiếu đĩa') : ''}`
      : '';
    return `
      <tr class="${r.recommended ? 'sz-best' : ''}${r.suggested ? ' sz-suggested' : ''}">
        <td><b>${esc(pr.name)}</b><div class="muted-sub">${esc(pr.sockets)}×${esc(pr.coresPerSocket)}C = ${esc(pr.cores)} cores · ${szGB(pr.ramGB)} RAM${local ? ` · ${szGB(pr.diskGB)} đĩa` : ''}</div></td>
        <td class="num">${r.hostsForCpu}</td>
        <td class="num">${r.hostsForRam}</td>
        <td class="num">+${r.redundancy}</td>
        <td class="num sz-hosts"><b>${r.hostsTotal}</b></td>
        <td>${esc(r.binding)}</td>
        <td class="num">${szNum(r.totals.cores)} / ${szGB(r.totals.ramGB)}</td>
        <td class="num">${r.vcpuPerCore == null ? '—' : szNum(r.vcpuPerCore, 2) + ' : 1'}</td>
        <td class="num">${failTxt} ${failPill}</td>
        <td class="num">${r.spare.extraAvgVms == null ? '—' : (r.spare.extraAvgVms <= 0 ? '0' : '~' + szNum(r.spare.extraAvgVms))}</td>
        ${local ? `<td>${storageCell}</td>` : ''}
        ${anyPrice ? `<td class="num">${r.budget == null ? '—' : szMoney(r.budget)}</td>` : ''}
        <td>${status}</td>
      </tr>`;
  }).join('');

  const absorption = sizingAbsorptionRows(d);
  let absorbHtml = '';
  if (absorption.length) {
    const canAbsorb = absorption.filter(a => a.cls === 'ok' && (!a.n1 || a.n1.ok));
    const absorbRows = absorption.map(a => `
      <tr>
        <td><b>${esc(a.name)}</b><div class="muted-sub">${a.hostCount} host · ${a.before.poweredOnCount} VM bật</div></td>
        <td class="num">${fmtPct(a.before.cpuPct)} → <b>${fmtPct(a.after.cpuPct)}</b></td>
        <td class="num">${fmtPct(a.before.ramPct)} → <b>${fmtPct(a.after.ramPct)}</b></td>
        <td class="num">${a.n1 ? `${fmtPct(a.n1.cpuPct)} / ${fmtPct(a.n1.ramPct)} ${a.n1.ok ? uiPill('ok', 'Đạt') : uiPill('crit', 'Không')}` : '—'}</td>
        <td>${uiPill(a.cls, a.cls === 'ok' ? 'Hấp thụ được' : a.cls === 'warn' ? 'Sát ngưỡng' : 'Vượt ngưỡng')}</td>
      </tr>`).join('');
    absorbHtml = `
      <div class="sz-section-title">Hạ tầng hiện hữu có hấp thụ được không?</div>
      <p class="panel-sub">Giả định đưa toàn bộ nhu cầu (sau tăng trưởng) vào từng cluster đang có; % tính theo ngưỡng overcommit của chính sách hiện hành (${appState.policy.cpuOvercommitMax}x CPU · ${appState.policy.ramOvercommitMax}x RAM).</p>
      <div class="table-scroll"><table class="data-table sz-compare">
        <thead><tr><th>Cluster</th><th>CPU % ngưỡng (trước → sau)</th><th>RAM % ngưỡng (trước → sau)</th><th>Khi mất 1 host (CPU / RAM)</th><th>Kết luận</th></tr></thead>
        <tbody>${absorbRows}</tbody>
      </table></div>
      <div class="insight-box">${canAbsorb.length
        ? `<strong>Có thể chưa cần mua host mới:</strong> cluster ${canAbsorb.map(a => esc(a.name)).join(', ')} hấp thụ được toàn bộ nhu cầu mà vẫn dưới 80% ngưỡng và giữ N+1. Phương án mua host bên trên là để tách riêng dự án hoặc để dành dư địa.`
        : `<strong>Không cluster nào hấp thụ được toàn bộ nhu cầu</strong> mà vẫn an toàn — cần bổ sung host theo đề xuất bên trên (hoặc chia nhỏ dự án vào nhiều cluster).`}</div>`;
  }

  area.innerHTML = `
    <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Kết quả sizing${p.environment ? ` — ${esc(p.environment)}` : ''}</div><p class="panel-sub">Số host vật lý cần cho ${szNum(d.vmCount)} VM theo từng cấu hình ứng viên; phương án đề xuất: ít host nhất → không sát ngưỡng → chi phí thấp nhất → điểm tổng hợp cao nhất. Dòng in nghiêng là phương án tham khảo do hệ thống gợi ý.</p></div></div>
    ${best ? `<div class="insight-box sz-headline"><strong>${esc(result.recommendation.headline)}</strong><div class="sz-detail">${esc(result.recommendation.detail)}</div>${result.recommendation.why ? `<div class="sz-detail"><b>${esc(result.recommendation.why)}</b></div>` : ''}</div>` : ''}
    ${tiles}
    <div class="sz-section-title">So sánh cấu hình host</div>
    <div class="table-scroll"><table class="data-table sz-compare">
      <thead><tr>
        <th>Cấu hình</th><th title="Số host cần để đáp ứng vCPU ở overcommit mục tiêu và mức dùng tối đa">Host theo CPU</th><th title="Số host cần để đáp ứng RAM">Host theo RAM</th><th>Dự phòng</th><th>Tổng host</th><th>Yếu tố quyết định</th>
        <th>Tổng cores / RAM</th><th title="Số vCPU (sau tăng trưởng) trên mỗi core vật lý của cụm">vCPU : core</th><th title="% năng lực mục tiêu đang dùng khi mất đúng số host dự phòng">Khi mất host (CPU / RAM)</th><th title="Số VM cỡ trung bình của dự án còn chứa thêm được mà vẫn giữ dự phòng">Chứa thêm (VM)</th>
        ${local ? '<th>Đĩa cục bộ</th>' : ''}${anyPrice ? '<th>Ngân sách ước tính</th>' : ''}<th></th>
      </tr></thead>
      <tbody>${cmpRows}</tbody>
    </table></div>
    ${sizingAdvisoryHtml(result)}
    <div class="sz-assumptions"><b>Giả định:</b> ${result.assumptions.map(esc).join(' ')}</div>
    ${absorbHtml}
  `;
  area.classList.remove('hidden');
  renderSizingCharts(result);
  if (scroll) area.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ---------- Advisory: findings, charts, roadmap ---------- */
const SZ_SEV = { critical: ['crit', 'Nghiêm trọng'], warning: ['warn', 'Cảnh báo'], info: ['na', 'Lưu ý'], ok: ['ok', 'Đạt'] };
const SZ_READY = { ok: ['ok', 'Sẵn sàng triển khai'], warn: ['warn', 'Triển khai được · cần lưu ý'], crit: ['crit', 'Cần xử lý rủi ro trước'] };
const szMoneyShort = (v) => {
  if (v === null || v === undefined || !isFinite(v)) return '—';
  if (Math.abs(v) >= 1e9) return `${(v / 1e9).toLocaleString('vi-VN', { maximumFractionDigits: 2 })} tỷ`;
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toLocaleString('vi-VN', { maximumFractionDigits: 0 })} tr`;
  return Number(v).toLocaleString('vi-VN');
};

function sizingCapacitySegments(best, d, p) {
  const n = best.hostsTotal, red = best.redundancy, nFail = best.failure.hostsLeft;
  const util = Math.max(0.05, Math.min(1, p.max_util_pct / 100));
  const ph = best.perHost, prof = best.profile;
  const mk = (total, usableHost, req, planned) => [
    ['Yêu cầu dự án', req], ['Dự phòng tăng trưởng', planned - req], ['Còn trống', Math.max(0, usableHost * nFail * util - planned)],
    [`Vùng an toàn ${100 - p.max_util_pct}%`, usableHost * nFail * (1 - util)], [`Dự phòng N+${red}`, usableHost * red], ['Hypervisor', Math.max(0, total - usableHost * n)],
  ];
  return {
    cpu: mk(prof.cores * Math.max(1, p.ht_factor) * p.cpu_ratio * n, ph.logicalCpu * p.cpu_ratio, d.vcpu, d.vcpuPlanned),
    ram: mk(prof.ramGB * p.ram_ratio * n, ph.usableRamGB * p.ram_ratio, d.ramGB, d.ramPlannedGB),
  };
}

function sizingAdvisoryHtml(result) {
  const a = result.analysis;
  if (!a) return '';
  const best = result.recommendedIndex != null ? result.results[result.recommendedIndex] : null;
  const wl = a.workload;
  const anyCost = result.results.some(r => r.advisory && r.advisory.costs && r.advisory.costs.tco);
  const [rcls, rtxt] = SZ_READY[a.readiness] || SZ_READY.ok;
  const fc = a.findingCounts || {};
  const costTiles = best && best.advisory.costs.tco ? `
      <div class="kpi-cell"><div class="kpi-l">TCO ${best.advisory.costs.years} năm (đề xuất)</div><div class="kpi-v">${szMoneyShort(best.advisory.costs.tco)}</div><div class="kpi-s">phần cứng ${szMoneyShort(best.advisory.costs.hardware)} · bảo hành ${szMoneyShort(best.advisory.costs.warranty)} · license ${szMoneyShort(best.advisory.costs.license)}</div></div>
      <div class="kpi-cell"><div class="kpi-l">Chi phí / VM / tháng</div><div class="kpi-v">${szMoneyShort(best.advisory.costs.perVmMonth)}</div><div class="kpi-s">/ VM: ${szMoneyShort(best.advisory.costs.perVm)} · / vCPU: ${szMoneyShort(best.advisory.costs.perVcpu)}</div></div>` : '';
  const insightTiles = best ? `
    <div class="kpi-strip sz-kpis">
      <div class="kpi-cell"><div class="kpi-l">Đặc tính workload</div><div class="kpi-v">${szNum(wl.ramPerVcpu, 1)} GB/vCPU</div><div class="kpi-s">${esc(wl.characterLabel)}</div></div>
      <div class="kpi-cell"><div class="kpi-l">Dư địa tăng trưởng thêm</div><div class="kpi-v">${best.advisory.runway.extraGrowthPct == null ? '—' : '+' + szNum(Math.max(0, best.advisory.runway.extraGrowthPct)) + '%'}</div><div class="kpi-s">ngoài +${szNum(result.params.growth_pct)}% đã dự phòng, vẫn giữ N+${best.redundancy}</div></div>
      <div class="kpi-cell"><div class="kpi-l">License theo core</div><div class="kpi-v">${szNum(best.advisory.license.coresTotal)} core</div><div class="kpi-s">${szNum(best.advisory.license.coresPerHost)}/host${best.advisory.license.paddedCores ? ` · ${szNum(best.advisory.license.paddedCores)} core trả thừa` : ''}</div></div>
      <div class="kpi-cell"><div class="kpi-l">Cần mở rộng</div><div class="kpi-v">${a.projection && a.projection.expandYear != null ? 'Năm ' + a.projection.expandYear : 'Không'}</div><div class="kpi-s">trong ${a.projection ? a.projection.years : '—'} năm · tăng ${szNum(result.params.annual_growth_pct)}%/năm</div></div>
      ${costTiles}
    </div>` : '';

  const findings = (a.findings || []).map(f => {
    const [cls, lbl] = SZ_SEV[f.severity] || SZ_SEV.info;
    return `<div class="sz-finding ${f.severity}"><div>${uiPill(cls, lbl)}<div class="sz-f-area">${esc(f.area)}</div></div>
      <div><div class="sz-f-title">${esc(f.title)}</div><div class="sz-f-detail">${esc(f.detail)}</div></div>
      <div><b>Khuyến nghị:</b> ${esc(f.action)}</div></div>`;
  }).join('');
  const steps = (a.nextSteps || []).map(st => `<div class="sz-step"><span class="muted-sub">${esc(st.when)}</span><b>${esc(st.phase)}</b>${esc(st.what)}</div>`).join('');
  const sensOpts = [['growth', 'Dự phòng tăng trưởng'], ['cpuRatio', 'Overcommit CPU mục tiêu'], ['maxUtil', 'Mức dùng tối đa / host']]
    .map(([k, l]) => `<option value="${k}" ${sizingState().sensKey === k ? 'selected' : ''}>${l}</option>`).join('');

  return `
    <div class="sz-section-title">Phân tích tư vấn</div>
    <div class="sz-readiness">${uiPill(rcls, rtxt)}<span class="muted-sub">${fc.critical || 0} nghiêm trọng · ${fc.warning || 0} cảnh báo · ${fc.info || 0} lưu ý · ${fc.ok || 0} đạt</span></div>
    ${insightTiles}
    <div class="sz-chart-grid three">
      <div class="sz-chart"><div class="sz-chart-title">Phân bố kích thước VM</div><div class="sz-chart-sub">Số VM theo nhóm T-shirt size.</div><div class="chart-wrap"><canvas id="szChartSizes"></canvas></div></div>
      <div class="sz-chart"><div class="sz-chart-title">Nhóm VM chiếm tài nguyên lớn nhất</div><div class="sz-chart-sub">% tổng vCPU / RAM của dự án; ${wl.paretoLines} nhóm chiếm 80% RAM.</div><div class="chart-wrap"><canvas id="szChartTop"></canvas></div></div>
      <div class="sz-chart"><div class="sz-chart-title">Từ yêu cầu đến năng lực lắp đặt</div><div class="sz-chart-sub">RAM (GB): yêu cầu → tăng trưởng → headroom → lắp đặt.</div><div class="chart-wrap"><canvas id="szChartWaterfall"></canvas></div></div>
    </div>
    <div class="sz-chart-grid">
      <div class="sz-chart"><div class="sz-chart-title">Số host theo từng ràng buộc</div><div class="sz-chart-sub">Host cần theo CPU, theo RAM và tổng gồm N+x cho mỗi cấu hình.</div><div class="chart-wrap"><canvas id="szChartHosts"></canvas></div></div>
      <div class="sz-chart"><div class="sz-chart-title">Bảng điểm đa tiêu chí</div><div class="sz-chart-sub">0–100, cao = tốt: số host, chi phí, hiệu suất, cân bằng, chịu lỗi, mở rộng, NUMA.</div><div class="chart-wrap tall"><canvas id="szChartRadar"></canvas></div></div>
    </div>
    ${best ? `<div class="sz-chart-grid">
      <div class="sz-chart"><div class="sz-chart-title">Phân bổ năng lực cluster đề xuất</div><div class="sz-chart-sub">${esc(best.profile.name)} × ${best.hostsTotal}: năng lực ở overcommit mục tiêu được dùng vào đâu.</div><div class="chart-wrap"><canvas id="szChartAlloc"></canvas></div></div>
      <div class="sz-chart"><div class="sz-chart-title">Dự báo nhu cầu host theo năm</div><div class="sz-chart-sub">Tăng trưởng ${szNum(result.params.annual_growth_pct)}%/năm; đường xanh = số host đề xuất lắp đặt.</div><div class="chart-wrap"><canvas id="szChartProjection"></canvas></div></div>
    </div>
    <div class="sz-chart-grid">
      <div class="sz-chart"><div class="panel-head panel-head-tight"><div><div class="sz-chart-title">Phân tích độ nhạy</div><div class="sz-chart-sub">Số host của từng cấu hình khi một giả định thay đổi.</div></div>
        <select id="szSensKey" class="btn-sm">${sensOpts}</select></div><div class="chart-wrap"><canvas id="szChartSens"></canvas></div></div>
      ${anyCost ? `<div class="sz-chart"><div class="sz-chart-title">TCO ${best.advisory.costs.years} năm theo phương án</div><div class="sz-chart-sub">Phần cứng + gia hạn bảo hành + license.</div><div class="chart-wrap"><canvas id="szChartCost"></canvas></div></div>`
        : `<div class="sz-chart"><div class="sz-chart-title">Chi phí</div><div class="sz-chart-sub">Nhập đơn giá host và/hoặc đơn giá license / core để xem TCO, chi phí / VM / tháng và so sánh chi phí giữa các phương án.</div></div>`}
    </div>` : ''}
    ${sizingTcoHtml(best)}
    <div class="sz-section-title">Rủi ro &amp; khuyến nghị</div>
    <div class="sz-findings">${findings || uiNote('Không có nhận định.')}</div>
    ${steps ? `<div class="sz-section-title">Lộ trình triển khai đề xuất</div><div class="sz-steps">${steps}</div>` : ''}`;
}

function sizingTcoHtml(best) {
  const c = best && best.advisory && best.advisory.costs;
  if (!c || !c.tco || !c.yearly || !c.yearly.length) return '';
  const m = v => (v == null ? '—' : szMoneyShort(v));
  const z = v => (v == null ? uiPill('warn', 'chưa nhập') : (v ? szMoneyShort(v) : '<span class="sz-muted">–</span>'));
  const ys = c.yearly;
  const head = `<tr><th>Thành phần</th>${ys.map(y => `<th>Năm ${y.year}</th>`).join('')}<th class="sum">Tổng ${c.years} năm</th></tr>`;
  const row = (label, cells, total, cls = '') => `<tr class="${cls}"><td>${label}</td>${cells.join('')}<td class="sum">${total}</td></tr>`;
  const rows = [
    row('Phần cứng', ys.map(y => `<td>${y.hardware == null ? '—' : z(y.hardware)}</td>`), m(c.hardware)),
    row('License', ys.map(y => `<td>${y.license == null ? '—' : z(y.license)}</td>`), m(c.license)),
    row(`Dịch vụ bảo hành HW<div class="muted-sub">${c.warrantyIncludedYears} năm đầu đã gồm trong giá HW</div>`,
      ys.map(y => `<td>${y.inWarranty ? '<span class="sz-muted">Đã gồm</span>' : z(y.warranty)}</td>`), m(c.warranty || 0)),
    row('Tổng theo năm', ys.map(y => `<td>${m(y.total)}</td>`), m(c.tco), 'total'),
    row('<span class="muted-sub">Lũy kế</span>', ys.map(y => `<td><span class="sz-muted">${m(y.cumulative)}</span></td>`), '', 'cum'),
  ].join('');
  const miss = (c.warrantyMissingYears || []).length
    ? `<p class="logo-upload-hint">Chưa nhập chi phí gia hạn bảo hành năm ${c.warrantyMissingYears.join(', ')} — nhập ở nhóm “Tư vấn &amp; chi phí” để TCO đầy đủ.</p>` : '';
  return `<div class="sz-section-title">Chi phí &amp; TCO ${c.years} năm — phương án đề xuất</div>
    <div class="sz-chart-grid sz-tco-grid">
      <div class="sz-chart"><div class="sz-chart-title">Chi phí theo năm và lũy kế</div>
        <div class="sz-chart-sub">Năm 1 mua phần cứng (gồm bảo hành ${c.warrantyIncludedYears} năm); các năm sau: gia hạn bảo hành + license.</div>
        <div class="chart-wrap"><canvas id="szChartTcoYear"></canvas></div></div>
      <div class="sz-chart"><div class="sz-chart-title">Bảng TCO theo năm</div>
        <div class="table-scroll"><table class="sz-tco-table"><thead>${head}</thead><tbody>${rows}</tbody></table></div>${miss}</div>
    </div>`;
}

function renderSizingCharts(result) {
  ['szSizes', 'szTop', 'szWaterfall', 'szHosts', 'szRadar', 'szAlloc', 'szProjection', 'szSens', 'szCost', 'szTcoYear'].forEach(destroyChart);
  if (typeof Chart === 'undefined' || !result.analysis) return;
  const c = chartPalette();
  const a = result.analysis;
  const d = result.demand;
  const p = result.params;
  const best = result.recommendedIndex != null ? result.results[result.recommendedIndex] : null;
  const rows = result.results.slice().sort((x, y) => (x.rank || 99) - (y.rank || 99));
  const scales = (extra = {}) => ({ x: { ticks: { color: c.muted }, grid: { color: c.divider }, ...(extra.x || {}) }, y: { beginAtZero: true, ticks: { color: c.muted }, grid: { color: c.divider }, ...(extra.y || {}) } });
  const legend = { labels: { color: c.text, boxWidth: 12 } };
  const mk = (key, id, cfg) => { const el = document.getElementById(id); if (el) appState.charts[key] = new Chart(el, cfg); };

  const buckets = a.workload.sizeBuckets.filter(b => b.count);
  mk('szSizes', 'szChartSizes', { type: 'doughnut',
    data: { labels: buckets.map(b => b.label), datasets: [{ data: buckets.map(b => b.count), backgroundColor: c.seq.slice(0, 5).filter((_, i) => a.workload.sizeBuckets[i].count), borderWidth: 0 }] },
    options: { maintainAspectRatio: false, cutout: '62%', plugins: { legend: { ...legend, position: 'bottom' } } } });

  const top = a.workload.topGroups.slice(0, 8);
  mk('szTop', 'szChartTop', { type: 'bar',
    data: { labels: top.map(g => g.name), datasets: [
      { label: '% vCPU', data: top.map(g => +g.vcpuShare.toFixed(1)), backgroundColor: c.series[0], borderRadius: 3 },
      { label: '% RAM', data: top.map(g => +g.ramShare.toFixed(1)), backgroundColor: c.series[1], borderRadius: 3 }] },
    options: { indexAxis: 'y', maintainAspectRatio: false, plugins: { legend }, scales: scales({ x: { ticks: { color: c.muted, callback: v => v + '%' } } }) } });

  // Floating-bar waterfall (RAM)
  const steps = a.waterfall.ram;
  let prev = 0;
  const wfData = steps.map((st, i) => { const last = i === steps.length - 1; const v = (i === 0 || last) ? [0, st.value] : [Math.min(prev, st.value), Math.max(prev, st.value)]; prev = st.value; return v; });
  mk('szWaterfall', 'szChartWaterfall', { type: 'bar',
    data: { labels: steps.map(st => st.label), datasets: [{ label: 'GB RAM', data: wfData, borderRadius: 3,
      backgroundColor: steps.map((_, i) => i === 0 ? c.series[0] : i === steps.length - 1 ? c.deep : c.series[1]) }] },   // start · added steps · total — no status colour (MASTER §8)
    options: { maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => { const [lo, hi] = ctx.raw; const i = ctx.dataIndex; return (i === 0 || i === steps.length - 1) ? `${szGB(hi)}` : `+${szGB(hi - lo)} → ${szGB(steps[i].value)}`; } } } }, scales: scales() } });

  mk('szHosts', 'szChartHosts', { type: 'bar',
    data: { labels: rows.map(r => r.profile.name), datasets: [
      { label: 'Theo CPU', data: rows.map(r => r.hostsForCpu), backgroundColor: c.series[0], borderRadius: 4 },
      { label: 'Theo RAM', data: rows.map(r => r.hostsForRam), backgroundColor: c.series[1], borderRadius: 4 },
      { label: 'Tổng (gồm N+x)', data: rows.map(r => r.hostsTotal), backgroundColor: c.other, borderRadius: 4 }] },
    options: { maintainAspectRatio: false, plugins: { legend }, scales: scales({ y: { ticks: { color: c.muted, precision: 0 } } }) } });

  const axes = a.scoreAxes || [];
  mk('szRadar', 'szChartRadar', { type: 'radar',
    // Recommended profile first so it gets the first colour; profiles after the 3rd are grey (MASTER §8).
    data: { labels: axes.map(x => x.label), datasets: rows.filter(r => r.advisory && r.advisory.scores).slice(0, 5)
      .sort((x, y) => (y.recommended ? 1 : 0) - (x.recommended ? 1 : 0)).map((r, i) => ({
      label: `${r.profile.name}${r.recommended ? ' ★' : ''} (${szNum(r.advisory.scoreTotal)})`, data: axes.map(x => +r.advisory.scores[x.key].toFixed(0)),
      borderColor: chartSeriesColor(i, c), backgroundColor: chartAlpha(chartSeriesColor(i, c), 0.13), borderWidth: r.recommended ? 2.5 : 1.5, pointRadius: 2 })) },
    options: { maintainAspectRatio: false, plugins: { legend: { ...legend, position: 'bottom' } },
      scales: { r: { min: 0, max: 100, ticks: { display: false, stepSize: 25 }, grid: { color: c.divider }, angleLines: { color: c.divider }, pointLabels: { color: c.text, font: { size: 11 } } } } } });

  if (best) {
    const seg = sizingCapacitySegments(best, d, p);
    // Parts of one whole: demand (2 tones of s1), free room (s2), reserves in grey tones — 3 hues max (MASTER §8).
    const segColors = [c.series[0], c.seq[1], c.series[1], c.other, chartAlpha(c.other, 0.6), chartAlpha(c.other, 0.3)];
    const pct = (arr) => { const t = arr.reduce((s, x) => s + Math.max(0, x[1]), 0) || 1; return arr.map(x => +(Math.max(0, x[1]) / t * 100).toFixed(1)); };
    const cpuPct = pct(seg.cpu), ramPct = pct(seg.ram);
    mk('szAlloc', 'szChartAlloc', { type: 'bar',
      data: { labels: ['CPU', 'RAM'], datasets: seg.cpu.map((s, i) => ({ label: s[0], data: [cpuPct[i], ramPct[i]], backgroundColor: segColors[i] })) },
      options: { indexAxis: 'y', maintainAspectRatio: false, plugins: { legend: { ...legend, position: 'bottom' }, tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${ctx.raw}%` } } },
        scales: { x: { stacked: true, max: 100, ticks: { color: c.muted, callback: v => v + '%' }, grid: { color: c.divider } }, y: { stacked: true, ticks: { color: c.text }, grid: { display: false } } } } });

    const pr = a.projection;
    if (pr) {
      mk('szProjection', 'szChartProjection', { type: 'bar',
        data: { labels: pr.points.map(pt => pt.year === 0 ? 'Hiện tại' : `Năm ${pt.year}`), datasets: [
          { type: 'line', label: `Đã lắp (${pr.installed})`, data: pr.points.map(() => pr.installed), borderColor: c.other, borderDash: [6, 4], pointRadius: 0, borderWidth: 2 },
          // red only where the data IS a status: the year needs more hosts than installed
          { label: 'Host cần', data: pr.points.map(pt => pt.hosts), backgroundColor: pr.points.map(pt => pt.hosts > pr.installed ? c.crit : c.series[0]), borderRadius: 4 }] },
        options: { maintainAspectRatio: false, plugins: { legend, tooltip: { callbacks: { afterLabel: ctx => ctx.datasetIndex === 1 ? `Tải +${szNum(pr.points[ctx.dataIndex].growthPct)}% so với hiện tại` : '' } } },
          scales: scales({ y: { ticks: { color: c.muted, precision: 0 } } }) } });
    }

    const drawSens = () => {
      destroyChart('szSens');
      const key = sizingState().sensKey || 'growth';
      const sw = a.sensitivity && a.sensitivity[key];
      if (!sw) return;
      const fmt = key === 'cpuRatio' ? (x => `${x}x`) : (x => `${x}%`);
      mk('szSens', 'szChartSens', { type: 'line',
        data: { labels: sw.x.map(fmt), datasets: sw.series.map((s, i) => ({ label: s.name, data: s.hosts, borderColor: chartSeriesColor(i, c), backgroundColor: chartSeriesColor(i, c), borderWidth: i < c.series.length ? 2 : 1.25, stepped: false, tension: 0, pointRadius: sw.x.map(x => x === sw.current ? 5 : 2.5) })) },
        options: { maintainAspectRatio: false, plugins: { legend, tooltip: { callbacks: { title: items => `${items[0].label}${sw.x[items[0].dataIndex] === sw.current ? ' (hiện tại)' : ''}` } } },
          scales: scales({ y: { ticks: { color: c.muted, precision: 0 }, title: { display: true, text: 'host', color: c.muted } } }) } });
    };
    drawSens();
    const sel = $('#szSensKey');
    if (sel) sel.addEventListener('change', () => { sizingState().sensKey = sel.value; drawSens(); });

    const anyHw = rows.some(r => !r.suggested && r.advisory.costs.hardware != null);
    const costRows = rows.filter(r => r.advisory.costs.tco && !(anyHw && r.advisory.costs.hardware == null));
    if (costRows.length) {
      mk('szCost', 'szChartCost', { type: 'bar',
        data: { labels: costRows.map(r => r.profile.name + (r.recommended ? ' ★' : '')), datasets: [
          { label: 'Phần cứng', data: costRows.map(r => r.advisory.costs.hardware || 0), backgroundColor: c.series[0], borderRadius: 3 },
          { label: 'Gia hạn bảo hành', data: costRows.map(r => r.advisory.costs.warranty || 0), backgroundColor: c.series[2], borderRadius: 3 },
          { label: `License ${costRows[0].advisory.costs.years} năm`, data: costRows.map(r => r.advisory.costs.license || 0), backgroundColor: c.series[1], borderRadius: 3 }] },
        options: { indexAxis: 'y', maintainAspectRatio: false, plugins: { legend, tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${szMoney(ctx.raw)}`, footer: items => `TCO: ${szMoney(costRows[items[0].dataIndex].advisory.costs.tco)}` } } },
          scales: { x: { stacked: true, ticks: { color: c.muted, callback: v => szMoneyShort(v) }, grid: { color: c.divider } }, y: { stacked: true, ticks: { color: c.text }, grid: { display: false } } } } });
    }
    const bc = best.advisory.costs;
    if (bc && bc.tco && bc.yearly && bc.yearly.length) {
      const ys = bc.yearly;
      mk('szTcoYear', 'szChartTcoYear', { type: 'bar',
        data: { labels: ys.map(y => `Năm ${y.year}`), datasets: [
          { label: 'Phần cứng', data: ys.map(y => y.hardware || 0), backgroundColor: c.series[0], stack: 's' },
          { label: 'License', data: ys.map(y => y.license || 0), backgroundColor: c.series[1], stack: 's' },
          { label: 'Dịch vụ bảo hành HW', data: ys.map(y => y.warranty || 0), backgroundColor: c.series[2], stack: 's' }] },
        // one Y axis (MASTER §8): the running total moves from a 2nd axis to the tooltip footer
        options: { maintainAspectRatio: false, plugins: { legend, tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${szMoney(ctx.raw)}`,
          footer: items => `Lũy kế đến năm ${ys[items[0].dataIndex].year}: ${szMoney(ys[items[0].dataIndex].cumulative)}` } } },
          scales: { x: { stacked: true, ticks: { color: c.muted }, grid: { display: false } },
            y: { stacked: true, beginAtZero: true, ticks: { color: c.muted, callback: v => szMoneyShort(v) }, grid: { color: c.divider } } } } });
    }
  }
}

