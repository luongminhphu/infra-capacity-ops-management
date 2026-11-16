/* Backup → Dung lượng: top servers by data size (last successful Full) and by data backed up in the period.
   Data: summary.sizeStats from backup/analysis.size_stats (successful leaf backup jobs only). */

const BK_SIZE_TOPS = [10, 20, 50];
const BK_SIZE_COLS = {
  size: [['rank', '#', 'num'], ['client', 'Máy chủ (client)', ''], ['sizeGB', 'Dung lượng', 'num'], ['basis', 'Căn cứ', ''],
         ['vmDiskUsedGB', 'Đĩa VM đã dùng / cấp phát', 'num'], ['policies', 'Policy', '']],
  total: [['rank', '#', 'num'], ['client', 'Máy chủ (client)', ''], ['totalGB', 'Đã backup trong kỳ', 'num'], ['jobs', 'Số job', 'num'],
          ['largestGB', 'Job lớn nhất', 'num']],
};

/* GB → "512,4 GB" / "1,2 TB" (vi-VN, MASTER §4). */
const bkSize = (gb) => (gb === null || gb === undefined || !isFinite(gb)) ? '—' : (gb >= 1024 ? `${bkNum(gb / 1024, 1)} TB` : `${bkNum(gb, 1)} GB`);

function bkSizeState() {
  const st = bkState();
  if (!st.size) st.size = { top: 10, sort: { size: { key: 'sizeGB', dir: 'desc' }, total: { key: 'totalGB', dir: 'desc' } } };
  return st.size;
}

function bkSizeValue(r, key) {
  if (key === 'client') return (r.client || '').toLowerCase();
  if (key === 'largestGB') return r.largestJob ? r.largestJob.gb : null;
  if (key === 'basis') return r.sizeBasis === 'full' ? 0 : 1;
  if (key === 'policies') return (r.policies || []).join(', ').toLowerCase();
  return r[key];
}

function bkSizeRows(list, table) {
  const ss = bkSizeState(), s = ss.sort[table];
  // the server sends each ranking already ordered; "#" keeps that rank whatever column is sorted
  const ranked = (list || []).slice(0, ss.top).map((r, i) => ({ ...r, rank: i + 1 }));
  return [...ranked].sort((a, b) => {
    const av = bkSizeValue(a, s.key), bv = bkSizeValue(b, s.key);
    if (typeof av === 'string' || typeof bv === 'string') return String(av ?? '').localeCompare(String(bv ?? ''), 'vi') * (s.dir === 'asc' ? 1 : -1);
    const an = isFinite(av) && av !== null ? av : -Infinity, bn = isFinite(bv) && bv !== null ? bv : -Infinity;
    return (an - bn) * (s.dir === 'asc' ? 1 : -1);
  });
}

/* Column headers are buttons (keyboard) with aria-sort on the th (MASTER §7). */
function bkSizeHead(table) {
  const s = bkSizeState().sort[table];
  return `<tr>${BK_SIZE_COLS[table].map(([key, label, cls]) => {
    const on = s.key === key;
    const aria = on ? (s.dir === 'asc' ? 'ascending' : 'descending') : 'none';
    return `<th class="${cls}" aria-sort="${aria}"><button type="button" class="th-sort${on ? ' on' : ''}" data-bk-sizesort="${table}:${key}">${esc(label)}${on ? `<span aria-hidden="true">${s.dir === 'asc' ? ' ▲' : ' ▼'}</span>` : ''}</button></th>`;
  }).join('')}</tr>`;
}

/* Relative bar: share of the largest value in the table (not a status, so the series colour). */
const bkSizeBar = (v, max) => `<span class="bk-size-bar" aria-hidden="true"><i style="width:${max ? Math.max(2, Math.min(100, v * 100 / max)).toFixed(1) : 0}%"></i></span>`;

function bkSizeClient(r) {
  const vmLink = r.vm ? uiEntityLink('vm', r.vm, r.vcenterKey) : `<strong>${esc(r.client)}</strong>`;
  return `${vmLink}${r.vm && r.vm.toLowerCase() !== String(r.client).toLowerCase() ? `<span class="muted-sub">client ${esc(r.client)}</span>` : ''}`;
}

function bkSizeTables(s) {
  const z = s.sizeStats || { bySize: [], byTotal: [], clients: 0 };
  const sizeRows = bkSizeRows(z.bySize, 'size'), totalRows = bkSizeRows(z.byTotal, 'total');
  const maxSize = Math.max(0, ...sizeRows.map(r => r.sizeGB || 0)), maxTotal = Math.max(0, ...totalRows.map(r => r.totalGB || 0));
  const sizeBody = sizeRows.map(r => `<tr>
      <td class="num">${r.rank}</td><td>${bkSizeClient(r)}</td>
      <td class="num bk-size-cell"><b>${bkSize(r.sizeGB)}</b>${bkSizeBar(r.sizeGB, maxSize)}</td>
      <td>${r.sizeBasis === 'full' ? `Full ${bkDate(r.sizeAt)}` : uiTag('Job lớn nhất', 'Không có bản Full thành công trong kỳ: lấy job thành công lớn nhất')}</td>
      <td class="num">${r.vmDiskUsedGB !== null && r.vmDiskUsedGB !== undefined ? `${bkSize(r.vmDiskUsedGB)} / ${bkSize(r.vmDiskProvGB)}` : '—'}</td>
      <td class="wrap">${esc((r.policies || []).join(', ') || '—')}</td></tr>`).join('');
  const totalBody = totalRows.map(r => `<tr>
      <td class="num">${r.rank}</td><td>${bkSizeClient(r)}</td>
      <td class="num bk-size-cell"><b>${bkSize(r.totalGB)}</b>${bkSizeBar(r.totalGB, maxTotal)}</td>
      <td class="num">${bkNum(r.jobs)}</td>
      <td class="num">${r.largestJob ? `${bkSize(r.largestJob.gb)}<span class="muted-sub">${esc(r.largestJob.schedule || '—')} · ${bkDate(r.largestJob.startTime)}</span>` : '—'}</td></tr>`).join('');
  return `
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2" id="bkSizeT1">Máy chủ có dung lượng lớn nhất</div>
        <p class="panel-sub">Dung lượng = bản Full thành công gần nhất trong kỳ (ước tính dữ liệu cần backup của máy chủ). Chưa có Full trong kỳ thì lấy job thành công lớn nhất. Cột đĩa VM lấy từ vCenter khi khớp tên / IP.</p></div></div>
      <div class="table-scroll table-scroll-flat" role="region" tabindex="0" aria-labelledby="bkSizeT1"><table class="data-table" id="bkSizeTable">
        <caption class="sr-only">Top ${sizeRows.length} máy chủ theo dung lượng</caption>
        <thead>${bkSizeHead('size')}</thead><tbody>${sizeBody || uiEmptyRow(6, 'Chưa có job backup thành công trong kỳ.')}</tbody></table></div>
    </section>
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2" id="bkSizeT2">Máy chủ backup nhiều dữ liệu nhất</div>
        <p class="panel-sub">Tổng dữ liệu các job thành công (Full + Incremental …) trong ${bkNum(s.period ? s.period.days : bkState().days)} ngày.</p></div></div>
      <div class="table-scroll table-scroll-flat" role="region" tabindex="0" aria-labelledby="bkSizeT2"><table class="data-table" id="bkTotalTable">
        <caption class="sr-only">Top ${totalRows.length} máy chủ theo dữ liệu đã backup</caption>
        <thead>${bkSizeHead('total')}</thead><tbody>${totalBody || uiEmptyRow(5, 'Chưa có job backup thành công trong kỳ.')}</tbody></table></div>
    </section>`;
}

function bkSizePage(s) {
  const z = s.sizeStats || { bySize: [], byTotal: [], clients: 0, totalGB: 0 };
  const ss = bkSizeState();
  const biggest = (z.bySize || [])[0], most = (z.byTotal || [])[0];
  return `
    ${uiKpiStrip([
      { label: 'Máy chủ có backup thành công', value: bkNum(z.clients), sub: `trong ${bkNum(s.period ? s.period.days : bkState().days)} ngày` },
      { label: 'Tổng dữ liệu đã backup', value: bkSize(z.totalGB), sub: `Top 10 chiếm ${bkPct(z.top10SharePct)}` },
      { label: 'Máy chủ lớn nhất', value: biggest ? bkSize(biggest.sizeGB) : '—', sub: biggest ? biggest.client : 'Chưa có dữ liệu' },
      { label: 'Backup nhiều nhất', value: most ? bkSize(most.totalGB) : '—', sub: most ? `${most.client} · ${bkNum(most.jobs)} job` : 'Chưa có dữ liệu' },
    ])}
    <div class="panel-toolbar bk-size-toolbar">
      <label class="field field-inline"><span>Hiển thị</span>
        <select data-bk-sizetop aria-label="Số máy chủ hiển thị">${BK_SIZE_TOPS.map(n => `<option value="${n}"${ss.top === n ? ' selected' : ''}>Top ${n}</option>`).join('')}</select></label>
      <span class="muted-sub">Bấm tiêu đề cột để sắp xếp; cột # giữ thứ hạng gốc.</span>
    </div>
    <div id="bkSizeMount">${bkSizeTables(s)}</div>`;
}

function bkSizeBind(s) {
  const body = $('#bkBody') || document;
  const mount = $('#bkSizeMount');
  if (!mount) return;
  const rerender = () => { mount.innerHTML = bkSizeTables(s); refreshIcons(); };
  mount.onclick = (e) => {
    const b = e.target.closest('[data-bk-sizesort]');
    if (!b) return;
    const [table, key] = b.dataset.bkSizesort.split(':');
    const cur = bkSizeState().sort[table];
    bkSizeState().sort[table] = { key, dir: cur.key === key ? (cur.dir === 'desc' ? 'asc' : 'desc') : (key === 'client' || key === 'policies' || key === 'basis' ? 'asc' : 'desc') };
    rerender();
    const again = mount.querySelector(`[data-bk-sizesort="${table}:${key}"]`);
    if (again) again.focus();
  };
  const sel = body.querySelector('[data-bk-sizetop]');
  if (sel) sel.onchange = () => { bkSizeState().top = Number(sel.value) || 10; rerender(); };
}

/* Tổng quan backup: compact top 5 of both rankings with a link to the full tab. */
function bkSizeOverview(s) {
  const z = s.sizeStats;
  if (!z || !z.clients) return '';
  const list = (rows, val, sub) => {
    const top = (rows || []).slice(0, 5), mx = Math.max(0, ...top.map(val));
    return `<table class="data-table bk-size-mini"><thead><tr><th class="num">#</th><th>Máy chủ</th><th class="num">${sub}</th></tr></thead><tbody>${top.map((r, i) => `<tr>
        <td class="num">${i + 1}</td><td>${bkSizeClient(r)}</td>
        <td class="num bk-size-cell"><b>${bkSize(val(r))}</b>${bkSizeBar(val(r), mx)}</td></tr>`).join('')}</tbody></table>`;
  };
  const days = bkNum(s.period ? s.period.days : bkState().days);
  return `
    <section class="table-card">
      <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Dung lượng backup theo máy chủ</div>
        <p class="panel-sub">${bkNum(z.clients)} máy chủ backup thành công trong ${days} ngày, tổng <b>${bkSize(z.totalGB)}</b>; 10 máy chủ lớn nhất chiếm <b>${bkPct(z.top10SharePct)}</b>.</p></div>
        <div class="panel-toolbar"><button class="btn btn-ghost btn-sm" type="button" data-nav-link="bk-size">Xem đủ top 50</button></div></div>
      <div class="chart-grid exec-chart-grid bk-size-pair">
        <div><h3 class="sp-h">Dung lượng lớn nhất <span class="muted-sub sr-inline">bản Full gần nhất</span></h3>${list(z.bySize, r => r.sizeGB || 0, 'Dung lượng')}</div>
        <div><h3 class="sp-h">Backup nhiều nhất <span class="muted-sub sr-inline">trong ${days} ngày</span></h3>${list(z.byTotal, r => r.totalGB || 0, 'Đã backup')}</div>
      </div>
    </section>
`;
}
