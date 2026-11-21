/* =========================================================
   Vận hành → Sự cố (ICO v2 G5): incidents from the event store, list → detail (timeline + nhận / giao / đóng),
   monthly MTTA / MTTR, top CIs. Owner filter added by apiRequest. Data: /api/incidents*, /api/ci/<id>/timeline.
   ========================================================= */
const incState = { status: 'active', level: '', source: '', days: '' };
const INC_STATUS = { open: ['bad', 'Đang mở'], acked: ['warn', 'Đã nhận'], resolved: ['ok', 'Đã đóng'] };
const INC_TRANSITION = { open: 'Mở', worse: 'Nặng hơn', better: 'Nhẹ hơn', clear: 'Hết' };

/* Minutes → "1 giờ 30 phút" / "45 phút" / "—". */
function incDuration(min) {
  if (min === null || min === undefined) return '—';
  const h = Math.floor(min / 60), m = Math.round(min % 60);
  return h ? `${uiNumber(h)} giờ${m ? ` ${m} phút` : ''}` : `${m} phút`;
}

function incCi(ci) {
  if (!ci) return '<span class="muted-sub">Không gắn CI</span>';
  return `<a href="#/entity?type=ci&id=${encodeURIComponent(ci.id)}">${esc(ci.name)}</a>`;
}

async function renderIncidentsView() {
  const mount = $('#incidentsMount');
  if (!mount) return;
  incBind(mount);
  const id = new URLSearchParams((location.hash.split('?')[1]) || '').get('id');
  mount.innerHTML = uiLoading();
  try {
    if (id) { mount.innerHTML = incDetailHtml(await apiRequest(`/incidents/${encodeURIComponent(id)}`)); refreshIcons(); return; }
    const p = new URLSearchParams(Object.entries(incState).filter(([, v]) => v));
    const [list, stats] = await Promise.all([apiRequest(`/incidents?${p}`), apiRequest('/incidents/stats?months=6')]);
    mount.innerHTML = incListHtml(list, stats);
    refreshIcons();
  } catch (err) { mount.innerHTML = uiError(err.message); }
}

function incListHtml(list, stats) {
  const opt = (v, t, cur) => `<option value="${esc(v)}"${String(v) === String(cur) ? ' selected' : ''}>${esc(t)}</option>`;
  const sel = (key, label, opts) => `<label class="field"><span>${esc(label)}</span><select data-inc-filter="${key}">${opts.map(([v, t]) => opt(v, t, incState[key])).join('')}</select></label>`;
  const filters = `<div class="form-grid-aligned">
    ${sel('status', 'Trạng thái', [['active', 'Chưa đóng'], ['open', 'Đang mở'], ['acked', 'Đã nhận'], ['resolved', 'Đã đóng'], ['', 'Tất cả']])}
    ${sel('level', 'Mức', [['', 'Mọi mức'], ['crit', 'VƯỢT NGƯỠNG'], ['bad', 'CẢNH BÁO'], ['warn', 'THEO DÕI']])}
    ${sel('source', 'Nguồn', [['', 'Mọi nguồn'], ...list.sources.map(s => [s.key, s.label])])}
    ${sel('days', 'Mở trong', [['', 'Mọi thời gian'], ['1', '24 giờ qua'], ['7', '7 ngày qua'], ['30', '30 ngày qua'], ['90', '90 ngày qua']])}</div>`;
  const rows = list.items.map(i => `<tr class="infra-row" tabindex="0" data-inc-open="${i.id}">
      <td>${uiLevelPill(i.level)}</td><td><b>${esc(i.title)}</b><span class="muted-sub">#${i.id} · ${esc(i.sourceLabel)}${i.flapping ? ' · chập chờn' : ''}</span></td>
      <td>${incCi(i.ci)}</td><td>${uiPill(...(INC_STATUS[i.status] || ['na', i.status]))}${i.assignee ? `<span class="muted-sub">${esc(i.assignee)}</span>` : ''}</td>
      <td>${esc(uiDateTime(i.openedAt, 'short'))}</td><td class="num">${uiNumber(i.eventCount)}</td></tr>`).join('');
  const month = stats.byMonth.map(m => `<tr><td>${esc(m.key.split('-').reverse().join('/'))}</td><td class="num">${uiNumber(m.incidents)}</td>
      <td class="num">${uiNumber(m.crit)}</td><td class="num">${uiNumber(m.open)}</td><td class="num">${esc(incDuration(m.mttaMin))}</td>
      <td class="num">${esc(incDuration(m.mttrMin))}</td></tr>`).join('');
  const bySource = stats.bySource.map(m => `<tr><td>${esc(m.label)}</td><td class="num">${uiNumber(m.incidents)}</td>
      <td class="num">${esc(incDuration(m.mttaMin))}</td><td class="num">${esc(incDuration(m.mttrMin))}</td></tr>`).join('');
  const top = stats.topCis.map(t => `<li>${incCi(t.ci)} <span class="muted-sub sr-inline">${uiNumber(t.incidents)} sự cố</span></li>`).join('');
  return `<section class="panel"><div class="panel-title" role="heading" aria-level="2">Sự cố</div>
      <p class="panel-sub">Sự cố mở khi một đối tượng chuyển sang THEO DÕI trở lên và tự đóng khi mọi sự kiện của nó hết.</p>
      ${filters}
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Mức</th><th>Sự cố</th><th>Đối tượng</th><th>Trạng thái</th><th>Mở lúc</th><th class="num">Sự kiện</th></tr></thead>
      <tbody>${rows || uiEmptyRow(6, 'Không có sự cố khớp bộ lọc.')}</tbody></table></div>
      ${list.truncated ? '<p class="muted-sub">Chỉ hiện 500 sự cố mới nhất.</p>' : ''}</section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Thống kê 6 tháng</div>
      <p class="panel-sub">MTTA: từ lúc mở đến lúc có người nhận. MTTR: từ lúc mở đến lúc đóng.</p>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Tháng</th><th class="num">Sự cố</th><th class="num">Vượt ngưỡng</th>
      <th class="num">Chưa đóng</th><th class="num">MTTA</th><th class="num">MTTR</th></tr></thead>
      <tbody>${month || uiEmptyRow(6, 'Chưa có sự cố trong 6 tháng.')}</tbody></table></div></section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">Theo nguồn</div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Nguồn</th><th class="num">Sự cố</th><th class="num">MTTA</th><th class="num">MTTR</th></tr></thead>
      <tbody>${bySource || uiEmptyRow(4, 'Chưa có dữ liệu.')}</tbody></table></div></section>
    <section class="panel"><div class="panel-title" role="heading" aria-level="2">10 đối tượng nhiều sự cố nhất</div>
      ${top ? `<ol class="en-links">${top}</ol>` : uiEmpty('Chưa có dữ liệu.')}</section>`;
}

function incEventsHtml(events) {
  if (!events.length) return uiEmpty('Chưa có sự kiện.');
  return `<ul class="home-list">${events.map(e => `<li class="home-item"><div class="home-item-main">
      ${e.level ? uiLevelPill(e.level) : uiPill('ok', 'Hết')} <b>${esc(INC_TRANSITION[e.transition] || e.transition)}</b> · ${esc(e.title)}
      <p class="home-item-detail">${esc(uiDateTime(e.at, 'short'))} · ${esc(e.sourceLabel)}${e.detail ? ` — ${esc(e.detail)}` : ''}</p></div></li>`).join('')}</ul>`;
}

function incDetailHtml(i) {
  const act = i.status === 'resolved' ? '' : `<div class="sp-pane-actions">
      ${i.status === 'open' ? '<button class="btn btn-primary btn-sm" type="button" data-inc-act="ack">Nhận xử lý</button>' : ''}
      <button class="btn btn-secondary btn-sm" type="button" data-inc-act="assign">Giao cho…</button>
      <button class="btn btn-secondary btn-sm" type="button" data-inc-act="resolve">Đóng sự cố</button></div>`;
  return `<section class="panel"><div class="panel-head"><div><div class="eyebrow">Sự cố #${i.id}</div>
      <div class="panel-title" role="heading" aria-level="2">${esc(i.title)}</div></div>
      <button class="btn btn-ghost btn-sm" type="button" data-inc-back>Danh sách sự cố</button></div>
      ${act}
      <dl class="sp-dl">${[['Mức cao nhất', uiLevelPill(i.level)], ['Trạng thái', uiPill(...(INC_STATUS[i.status] || ['na', i.status]))],
        ['Đối tượng', incCi(i.ci)], ['Nguồn', esc(i.sourceLabel)], ['Mở lúc', esc(uiDateTime(i.openedAt, 'short'))],
        ['Nhận', i.ackedAt ? `${esc(uiDateTime(i.ackedAt, 'short'))} · ${esc(i.ackedBy || '')}` : '—'], ['Người xử lý', esc(i.assignee || '—')],
        ['Đóng', i.resolvedAt ? `${esc(uiDateTime(i.resolvedAt, 'short'))} · ${esc(i.resolvedBy === 'system' ? 'tự động (mọi sự kiện đã hết)' : i.resolvedBy || '')}` : '—'],
        ['Chập chờn', i.flapping ? uiPill('warn', 'Có') : 'Không'], ['Ghi chú', esc(i.note || '—')]]
        .map(([k, v]) => `<div class="sp-dl-item"><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join('')}</dl></section>
    <section class="panel" data-inc-id="${i.id}"><div class="panel-title" role="heading" aria-level="2">Diễn biến</div>${incEventsHtml(i.events)}</section>`;
}

function incBind(mount) {
  if (mount.dataset.bound) return;
  mount.dataset.bound = '1';
  mount.addEventListener('change', (e) => {
    const f = e.target.closest('[data-inc-filter]');
    if (f) { incState[f.dataset.incFilter] = f.value; renderIncidentsView(); }
  });
  mount.addEventListener('click', async (e) => {
    if (e.target.closest('[data-inc-back]')) { location.hash = '#/incidents'; return; }
    const a = e.target.closest('[data-inc-act]');
    if (a) {
      const id = new URLSearchParams((location.hash.split('?')[1]) || '').get('id');
      const action = a.dataset.incAct;
      let body = {};
      if (action === 'assign') {
        const who = await uiPrompt('Giao sự cố cho (tên hoặc email)');
        if (who === null) return;
        body = { assignee: who };
      } else if (action === 'resolve') {
        const note = await uiPrompt('Ghi chú khi đóng (nguyên nhân / cách xử lý)');
        if (note === null) return;
        body = { note };
      }
      try { await apiRequest(`/incidents/${encodeURIComponent(id)}/${action}`, { method: 'POST', body: JSON.stringify(body) }); renderIncidentsView(); }
      catch (err) { uiAlert(err.message); }
      return;
    }
    const row = e.target.closest('[data-inc-open]');
    if (row && !e.target.closest('a')) location.hash = `#/incidents?id=${encodeURIComponent(row.dataset.incOpen)}`;
  });
  mount.addEventListener('keydown', (e) => {
    const row = e.target.closest('[data-inc-open]');
    if (row && e.key === 'Enter') row.click();
  });
}
