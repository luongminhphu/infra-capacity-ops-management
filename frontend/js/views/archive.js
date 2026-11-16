/* Kho báo cáo (PDF đã lưu): moved out of ai_assist.js (audit FE-35). */

/* ---------------------------------------------------------------- Kho báo cáo */

(function bindArchiveToggle() {
  const cb = $('#rptArchive');
  if (cb) {
    cb.checked = reportArchiveOn();
    cb.addEventListener('change', () => uiSetPref('reportArchive', cb.checked));
  }
  // The header itself is added by uiDownload (ui.js); the server keeps only exports its registry marks archivable.
}());

const archState = { kind: '', mine: false };
async function renderArchiveView() {
  const mount = $('#archiveMount');
  if (!mount) return;
  let d;
  try { d = await apiRequest(`/report-archive?${new URLSearchParams({ ...(archState.kind ? { kind: archState.kind } : {}), mine: archState.mine ? 'true' : 'false' })}`); }
  catch (err) { mount.innerHTML = `${uiError(err.message)}`; return; }
  const size = (b) => b >= 1048576 ? `${fmtNum(b / 1048576, 1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`;
  mount.innerHTML = `<section class="table-card">
    <div class="panel-head"><div><div class="panel-title" role="heading" aria-level="2">Kho báo cáo</div>
      <p class="panel-sub">Bản PDF đúng như lúc xuất. Bật “Lưu vào kho” ở <a href="#/docs/reports">Trung tâm báo cáo</a> để mỗi lần xuất được lưu một bản. Mỗi người giữ tối đa ${esc(d.maxPerUser)} bản, ${esc(d.retentionDays)} ngày.</p></div>
      <div class="panel-toolbar"><select id="archKind" aria-label="Loại báo cáo"><option value="">Mọi loại</option>${Object.entries(d.kinds).map(([k, l]) => `<option value="${esc(k)}"${archState.kind === k ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>
        <label class="finding-filter-item"><input type="checkbox" id="archMine" ${archState.mine ? 'checked' : ''}><span>Chỉ của tôi</span></label></div></div>
    <div class="table-scroll"><table class="data-table"><thead><tr><th>Lúc</th><th>Loại</th><th>Báo cáo</th><th>Tham số</th><th>Người xuất</th><th class="num">Dung lượng</th><th></th></tr></thead><tbody>
    ${d.items.map(x => `<tr><td>${esc(spTimeSafe(x.createdAt))}</td><td>${esc(x.kindLabel)}</td>
      <td class="wrap"><a href="/api/report-archive/${encodeURIComponent(x.id)}/file" download>${esc(x.filename)}</a></td>
      <td class="wrap muted-sub">${esc((x.params || '').replace(/&/g, ' · '))}</td><td>${esc(x.createdBy)}</td><td class="num">${esc(size(x.sizeBytes))}</td>
      <td>${x.createdBy === (currentUser && currentUser.username) || (currentUser && currentUser.isAdmin) ? `<button class="btn btn-ghost btn-sm btn-danger" type="button" data-arch-del="${esc(x.id)}" aria-label="Xoá ${esc(x.filename)}"><i data-lucide="trash-2"></i></button>` : ''}</td></tr>`).join('')
      || `<tr><td colspan="7">${uiEmpty('Chưa có báo cáo nào trong kho.')}</td></tr>`}
    </tbody></table></div></section>`;
  refreshIcons();
  $('#archKind').onchange = (e) => { archState.kind = e.target.value; renderArchiveView(); };
  $('#archMine').onchange = (e) => { archState.mine = e.target.checked; renderArchiveView(); };
  mount.querySelectorAll('[data-arch-del]').forEach(b => { b.onclick = async () => {
    if (!(await uiConfirm('Xoá bản báo cáo này khỏi kho?'))) return;
    try { await apiRequest(`/report-archive/${encodeURIComponent(b.dataset.archDel)}`, { method: 'DELETE' }); renderArchiveView(); }
    catch (err) { uiSetStatus('#archiveMount .panel-sub', err.message, 'error'); }
  }; });
}
