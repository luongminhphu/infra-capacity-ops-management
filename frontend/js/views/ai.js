/* =========================================================
   Cài đặt → AI & MCP: nền tảng AI dùng chung (super app SA4) —
   provider, API key, công tắc từng tác vụ, che tên host / IP / VM,
   hạn mức tháng + ngưỡng cảnh báo, thống kê chi phí.
   Plus the AI pieces of other pages: bản tin trang chủ, tổng kết PDF.
   ========================================================= */

let aiStatusCache = null;
async function aiStatus(force) {
  if (!aiStatusCache || force) {
    try { aiStatusCache = await apiRequest('/ai/status'); } catch (err) { return { enabled: false, tasks: {} }; }   // not cached: retried after login
  }
  return aiStatusCache;
}

const aiUsd = (v) => uiMoney(Number(v || 0), 'USD', 2);
const AI_RUN = { ok: ['ok', 'Đã gọi'], cached: ['na', 'Dùng lại'], blocked: ['warn', 'Bị chặn'], error: ['bad', 'Lỗi'] };

let aiSettingsAt = 0;
async function renderAiSettings(force) {
  const mount = $('#aiSettingsMount');
  const panel = $('#aiPanel');
  if (!mount || !panel) return;
  // The settings view is re-synced on navigation and again when data arrives: never wipe a form being edited.
  if (!force && Date.now() - aiSettingsAt < 15000 && mount.childElementCount) return;
  aiSettingsAt = Date.now();
  if (!(currentUser && currentUser.isAdmin)) { panel.classList.add('hidden'); return; }
  panel.classList.remove('hidden');
  let d;
  try { d = await apiRequest('/admin/ai'); } catch (err) { mount.innerHTML = `<p class="criteria-status error">${esc(err.message)}</p>`; return; }
  const u = d.usage;
  const pct = Math.min(100, (u.costUsd / Math.max(1, u.budgetUsd)) * 100);
  const tone = u.costUsd >= u.budgetUsd ? 'crit' : u.costUsd >= u.alertUsd ? 'bad' : 'ok';
  const opt = (v, l, s) => `<option value="${esc(v)}"${String(v) === String(s) ? ' selected' : ''}>${esc(l)}</option>`;
  mount.innerHTML = `
    <div class="ai-usage">
      <div><div class="eyebrow">Chi phí tháng ${esc(u.month.slice(5))}/${esc(u.month.slice(0, 4))}</div>
        <div class="ai-usage-value">${esc(aiUsd(u.costUsd))} <span class="muted-sub sr-inline">/ hạn mức ${esc(u.budgetUsd)} USD · cảnh báo ở ${esc(u.alertUsd)} USD</span></div>
        <div class="ai-bar"><span class="ai-bar-fill tone-${tone}" style="width:${pct.toFixed(1)}%"></span><span class="ai-bar-mark" style="left:${Math.min(100, u.alertUsd * 100 / Math.max(1, u.budgetUsd)).toFixed(1)}%"></span></div></div>
      ${uiPill(d.enabled ? 'ok' : 'na', d.enabled ? 'AI đang bật' : 'AI đang tắt')}
    </div>
    <div class="settings-form ai-grid">
      <label class="field"><span>Nhà cung cấp</span><select id="aiProv">${opt('none', 'Không dùng AI', d.provider)}${opt('anthropic', 'Anthropic (Claude)', d.provider)}</select></label>
      <label class="field"><span>Model</span><input id="aiModel2" maxlength="64" value="${esc(d.model)}"></label>
      <label class="field"><span>Mức suy luận (effort)</span><select id="aiEffort2">${['low', 'medium', 'high'].map(x => opt(x, x, d.effort)).join('')}</select></label>
      <label class="field"><span>API key</span><input type="password" id="aiKey2" autocomplete="off" placeholder="${d.hasApiKey ? 'Đã lưu — để trống để giữ' : 'sk-ant-…'}"></label>
      <label class="field"><span>Hạn mức / tháng (USD) — chạm là AI tạm dừng</span><input id="aiBudget" type="number" min="1" max="10000" value="${esc(d.budgetUsd)}"></label>
      <label class="field"><span>Ngưỡng cảnh báo (USD) — Discord + hộp việc admin</span><input id="aiAlert" type="number" min="1" max="10000" value="${esc(d.alertUsd)}"></label>
      <label class="field"><span>Lượt gọi tối đa / người / giờ</span><input id="aiRate" type="number" min="1" max="1000" value="${esc(d.ratePerHour)}"></label>
    </div>
    <div class="ai-checks">
      <label class="finding-filter-item"><input type="checkbox" id="aiAllow2" ${d.allowExternal ? 'checked' : ''}><span>Cho phép gửi dữ liệu tới Anthropic (chỉ dữ kiện tối thiểu của từng tác vụ; không gửi mật khẩu, API key, email, file nội bộ)</span></label>
      <label class="finding-filter-item"><input type="checkbox" id="aiMask" ${d.maskNames ? 'checked' : ''}><span>Che tên host, IP, tên VM / client backup trước khi gửi (thay bằng mã &lt;host:…&gt;, khôi phục trong kết quả)</span></label>
      ${d.secretsKeyConfigured ? '' : '<p class="criteria-status">Chưa đặt SECRETS_KEY: API key đang mã hoá bằng khoá suy ra từ JWT_SECRET.</p>'}
      <div class="sp-h">Tác vụ AI</div>
      ${d.tasks.map(t => `<label class="finding-filter-item"><input type="checkbox" data-ai-task="${esc(t.key)}" ${t.on ? 'checked' : ''}><span><b>${esc(t.label)}</b> — ${esc(t.desc)}</span></label>`).join('')}
    </div>
    <div class="sim-actions"><button class="btn btn-primary btn-sm" type="button" id="aiSave2">Lưu cấu hình AI</button>
      <button class="btn btn-secondary btn-sm" type="button" id="aiTest2">Kiểm tra kết nối</button></div>
    <p class="criteria-status" id="aiMsg" role="status"></p>
    <div class="sp-h">Theo tác vụ (tháng này)</div>
    <div class="table-scroll"><table class="data-table"><thead><tr><th>Tác vụ</th><th class="num">Lượt gọi</th><th class="num">Dùng lại (cache)</th><th class="num">Bị chặn</th><th class="num">Lỗi</th><th class="num">Chi phí</th></tr></thead><tbody>
      ${Object.values(u.byTask).map(t => `<tr><td>${esc(t.label)}</td><td class="num">${esc(t.calls)}</td><td class="num">${esc(t.cached)}</td><td class="num">${esc(t.blocked)}</td><td class="num">${esc(t.errors)}</td><td class="num">${esc(aiUsd(t.costUsd))}</td></tr>`).join('')}
    </tbody></table></div>
    <details class="sp-history"><summary>30 lượt gần nhất</summary>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Lúc</th><th>Tác vụ</th><th>Người</th><th>Kết quả</th><th>Model</th><th class="num">Token vào / ra</th><th class="num">Chi phí</th><th>Ghi chú</th></tr></thead><tbody>
      ${u.recent.map(r => `<tr><td>${esc(spTimeSafe(r.at))}</td><td>${esc((d.tasks.find(t => t.key === r.task) || { label: r.task }).label)}</td><td>${esc(r.user || '—')}</td>
        <td>${uiPill(...(AI_RUN[r.status] || ['na', r.status]))}</td><td>${esc(r.model || '—')}</td>
        <td class="num">${esc(r.inputTokens ?? '—')} / ${esc(r.outputTokens ?? '—')}</td><td class="num">${esc(aiUsd(r.costUsd))}</td><td class="wrap">${esc(r.message || '')}</td></tr>`).join('') || `<tr><td colspan="8">${uiEmpty('Chưa có lượt gọi AI.')}</td></tr>`}
      </tbody></table></div></details>`;
  const msg = (t, ok) => uiSetStatus('#aiMsg', t, ok ? 'ok' : 'error');
  $('#aiSave2').onclick = async (e) => {
    const b = e.currentTarget; b.disabled = true;
    const tasks = {};
    $$('[data-ai-task]').forEach(c => { tasks[c.dataset.aiTask] = c.checked; });
    try {
      await apiRequest('/admin/ai', { method: 'PUT', body: JSON.stringify({ provider: $('#aiProv').value, model: $('#aiModel2').value, effort: $('#aiEffort2').value,
        apiKey: $('#aiKey2').value, allowExternal: $('#aiAllow2').checked, maskNames: $('#aiMask').checked,
        budgetUsd: Number($('#aiBudget').value), alertUsd: Number($('#aiAlert').value), ratePerHour: Number($('#aiRate').value), tasks }) });
      aiStatusCache = null;
      await renderAiSettings(true); uiSetStatus('#aiMsg', 'Đã lưu cấu hình AI.', 'ok');
    } catch (err) { msg(err.message); b.disabled = false; }
  };
  $('#aiTest2').onclick = async (e) => {
    const b = e.currentTarget; b.disabled = true;
    try { const r = await apiRequest('/admin/ai/test', { method: 'POST' }); msg(r.message, r.ok); } catch (err) { msg(err.message); }
    finally { b.disabled = false; }
  };
}

/* ---------------------------------------------------------------- Trang chủ: bản tin AI */
async function homeAiBrief(btn) {
  const out = $('#homeAiBrief');
  if (!out) return;
  btn.disabled = true;
  out.innerHTML = uiNote('AI đang tóm tắt…');
  try {
    const s = await apiRequest('/ai/home-brief', { method: 'POST' });
    out.innerHTML = `<div class="home-ai">
      <div class="home-brief-head"><span class="eyebrow">Bản tin AI${s.cached ? ' · dùng lại' : ''}</span><span class="muted-sub sr-inline">AI soạn từ hộp việc của bạn — đối chiếu trước khi quyết định</span></div>
      <p class="home-brief-text">${esc(s.headline)}</p>
      ${s.points.length ? `<ul class="home-list">${s.points.map(p => `<li class="home-item"><div class="home-item-main">${uiLevelPill(p.level)} ${esc(p.text)}</div></li>`).join('')}</ul>` : ''}
      ${s.decisions.length ? `<div class="sp-h">Việc nên làm</div><ol class="home-ai-dec">${s.decisions.map(x => `<li>${esc(x)}</li>`).join('')}</ol>` : ''}</div>`;
  } catch (err) { out.innerHTML = `<p class="criteria-status error">${esc(err.message)}</p>`; }
  finally { btn.disabled = false; }
}

/* ---------------------------------------------------------------- Tổng kết PDF bằng AI (Trung tâm báo cáo) */
function reportAiOn() {
  return uiPref('reportAi', false) === true;
}
function reportAiParam(prefix = '&') { return reportAiOn() ? `${prefix}ai=1` : ''; }

(function bindReportAiToggle() {
  const cb = $('#rptAi');
  if (!cb) return;
  cb.checked = reportAiOn();
  cb.addEventListener('change', () => uiSetPref('reportAi', cb.checked));
}());
