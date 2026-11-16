/* =========================================================
   routes.js — the ONE route table of the super app (docs/design/super-app.md §2.1).
   PAGES — one row per page (id, view, sub-tab, feature / spend role, scope, title, description);
   MENU  — the 2-domain menu, each entry grouping pages as in-page tabs (#/<entry>/<tab>);
   VIEWS — per view: its section, how to render it and, for views with internal tabs, how to read /
           set that tab. app.js (navigation) and core.js (sections, rendering) read these tables
           instead of their own if/else chains. A new page = one PAGES row + one MENU tab (+ VIEWS).
   ========================================================= */

/* `scope` lists the global filters that make sense on the page. */
const PAGES = [
    { id: 'home', view: 'home', icon: 'house', label: 'Hôm nay', title: 'Trang chủ',
      desc: 'Hôm nay cần làm: bản tin, trạng thái từng miền và hộp việc gom từ mọi module theo quyền của bạn.', scope: [] },
    { id: 'agenda', view: 'agenda', icon: 'calendar-clock', label: 'Lịch việc & nhắc hạn', title: 'Lịch việc & nhắc hạn',
      desc: 'Mọi mốc có ngày: Opex hết hạn, đợt thanh toán, license hết hạn, báo giá hết hiệu lực.', scope: [] },
    { id: 'entity', view: 'entity', icon: 'box', label: 'Đối tượng', title: 'Đối tượng',
      desc: 'Mọi thông tin về một host / VM từ các module: capacity, phần cứng, hệ điều hành, backup, compliance, bảo hành.', scope: [] },
    { id: 'spend-payments', feature: 'spend', spendRole: 'viewer', view: 'spend', sub: 'payments', icon: 'banknote', label: 'Đợt thanh toán', title: 'Đợt thanh toán',
      desc: 'Mọi đợt thanh toán của mọi hợp đồng, sớm nhất trước, kèm tình trạng hồ sơ.', scope: [] },
    { id: 'executive', view: 'executive', icon: 'briefcase', label: 'Điều hành', title: 'Tổng quan điều hành',
      desc: 'Tình trạng capacity, rủi ro, đề xuất và nội dung cần phê duyệt theo System Owner của từng cluster.', scope: ['vcenter'] },
    { id: 'dashboard', view: 'dashboard', icon: 'layout-dashboard', label: 'Capacity', title: 'Capacity',
      desc: 'Tổng / đã dùng / còn dư CPU, RAM, Storage và tỷ lệ overcommit theo cluster, host, datastore.', scope: ['vcenter', 'cluster', 'host', 'footprint'] },
    { id: 'compliance', view: 'compliance', icon: 'shield-alert', label: 'Compliance', title: 'Compliance',
      desc: 'Phát hiện theo ngưỡng đã cấu hình: host CPU/RAM, overcommit cluster, VM idle, tắt lâu, over-provision.', scope: ['vcenter', 'cluster', 'host', 'footprint'] },
    { id: 'history', view: 'history', icon: 'trending-up', label: 'Xu hướng & biến động', title: 'Xu hướng & biến động',
      desc: 'Biến động CPU/RAM so với ngưỡng overcommit giữa các snapshot và danh sách VM thêm / bớt / đổi cấu hình.', scope: ['vcenter', 'cluster', 'host'] },
    { id: 'inventory', view: 'inventory', icon: 'server', label: 'Host & VM', title: 'Host & VM',
      desc: 'Danh sách ESXi host và VM đã đồng bộ hoặc import, kèm mức sử dụng; xuất Excel theo bộ lọc.', scope: ['vcenter', 'cluster', 'host', 'footprint'] },
    { id: 'lic-hardware', view: 'licensing', sub: 'hardware', icon: 'cpu', label: 'ESXi & phần cứng', title: 'ESXi & phần cứng',
      desc: 'Model, phiên bản ESXi, socket, physical core, logical thread, RAM và số VM của từng host.', scope: ['vcenter', 'cluster'] },
    { id: 'lic-os', view: 'licensing', sub: 'os', icon: 'app-window', label: 'Hệ điều hành', title: 'Hệ điều hành guest',
      desc: 'Phân loại guest OS (Windows Server, Linux, khác) làm cơ sở tính license Windows Server.', scope: ['vcenter', 'cluster'] },
    { id: 'whatif', feature: 'whatif', view: 'whatif', icon: 'git-branch', label: 'What-if capacity', title: 'What-if capacity',
      desc: 'Mô phỏng thêm / nâng cấp host, thêm / resize VM, decommission VM idle và kịch bản mẫu.', scope: ['vcenter'] },
    { id: 'sizing', feature: 'sizing', view: 'sizing', icon: 'calculator', label: 'Sizing máy chủ', title: 'Sizing máy chủ',
      desc: 'Từ danh sách VM của dự án mới tính số host vật lý cần mua theo từng cấu hình ứng viên.', scope: ['vcenter'] },
    { id: 'planning', feature: 'planning', view: 'planning', icon: 'calendar-range', label: 'Quy hoạch tài nguyên', title: 'Quy hoạch tài nguyên',
      desc: 'Dự phóng tăng trưởng CPU/RAM và thời điểm cần bổ sung node máy chủ cho từng cluster.', scope: ['vcenter'] },
    { id: 'lic-assess', feature: 'license', view: 'licensing', sub: 'assess', icon: 'badge-check', label: 'Đánh giá license', title: 'Đánh giá license',
      desc: 'Nhu cầu license VMware per-core và Windows Server theo cluster, so với entitlement và rule đang áp dụng.', scope: ['vcenter', 'cluster'] },
    { id: 'lic-whatif', feature: 'license', view: 'licensing', sub: 'whatif', icon: 'sliders-horizontal', label: 'What-if license', title: 'What-if license',
      desc: 'Ảnh hưởng license khi thêm / giảm / nâng cấp host, thêm VM hoặc chuyển workload.', scope: ['vcenter', 'cluster'] },
    { id: 'lic-snapshots', feature: 'license', view: 'licensing', sub: 'snapshots', icon: 'camera', label: 'Snapshot license', title: 'Snapshot license',
      desc: 'Lưu kết quả đánh giá tại một thời điểm để đối chiếu, tái lập hoặc xuất PDF.', scope: ['vcenter', 'cluster'] },
    { id: 'bk-overview', feature: 'backup', view: 'backup', sub: 'overview', icon: 'database-backup', label: 'Tổng quan backup', title: 'Tổng quan backup',
      desc: 'Tỷ lệ backup thành công, RPO, nguyên nhân lỗi và phát hiện từ hệ thống backup (tài khoản chỉ đọc).', scope: [] },
    { id: 'bk-clients', feature: 'backup', view: 'backup', sub: 'clients', icon: 'monitor-check', label: 'Client & RPO', title: 'Client & RPO',
      desc: 'Lần backup thành công gần nhất, lỗi liên tiếp và đánh giá RPO của từng client.', scope: [] },
    { id: 'bk-jobs', feature: 'backup', view: 'backup', sub: 'jobs', icon: 'list-checks', label: 'Job backup', title: 'Job backup',
      desc: 'Danh sách job theo kết quả, loại job, client, policy và mã lỗi backup.', scope: [] },
    { id: 'bk-schedule', feature: 'backup', view: 'backup', sub: 'schedule', icon: 'calendar-check', label: 'Lịch & tuân thủ', title: 'Lịch backup & tuân thủ',
      desc: 'Đối chiếu từng đêm backup với lịch (đọc từ schedule của policy backup hoặc khai báo / import Excel): đạt, trễ, sai loại, lỗi, thiếu.', scope: [] },
    { id: 'bk-coverage', feature: 'backup', view: 'backup', sub: 'coverage', icon: 'shield-check', label: 'Độ phủ VM', title: 'Độ phủ backup VM',
      desc: 'Đối chiếu VM trong vCenter với client backup: VM chưa có backup hoặc backup lỗi.', scope: [] },
    { id: 'bk-storage', feature: 'backup', view: 'backup', sub: 'storage', icon: 'hard-drive', label: 'Storage & policy', title: 'Storage & policy',
      desc: 'Dung lượng disk pool, xu hướng và số ngày tới ngưỡng high-water; tình trạng từng policy.', scope: [] },
    { id: 'bk-config', feature: 'backup', view: 'backup', sub: 'config', icon: 'sliders-horizontal', label: 'Nguồn & ngưỡng', title: 'Nguồn backup & ngưỡng',
      desc: 'Trạng thái kết nối máy chủ backup, hướng dẫn tài khoản chỉ đọc và ngưỡng giám sát backup.', scope: [] },
    { id: 'spend-overview', feature: 'spend', spendRole: 'viewer', view: 'spend', sub: 'overview', icon: 'landmark', label: 'Tổng quan chi phí', title: 'Kế hoạch ngân sách IT',
      desc: 'Tổng hợp Opex và Capex theo năm tài chính, loại, nhà cung cấp; hết hạn theo quý; xuất PDF / Excel theo bộ lọc.', scope: [] },
    { id: 'spend-opex', feature: 'spend', spendRole: 'viewer', view: 'spend', sub: 'opex', icon: 'wallet', label: 'Opex', title: 'Chi phí vận hành (Opex)',
      desc: 'Bảo hành, license, bảo trì, dịch vụ thuê ngoài theo năm tài chính; hạn hết hiệu lực và nhắc gia hạn.', scope: [] },
    { id: 'spend-capex', feature: 'spend', spendRole: 'viewer', view: 'spend', sub: 'capex', icon: 'package-plus', label: 'Capex', title: 'Dự án đầu tư (Capex)',
      desc: 'Dự án mua mới gồm nhiều hạng mục; tự sinh Opex gia hạn bảo hành / license dự kiến cho các năm sau.', scope: [] },
    { id: 'spend-plan', feature: 'spend', spendRole: 'viewer', view: 'spend', sub: 'plan', icon: 'calendar-range', label: 'Kế hoạch năm sau', title: 'Kế hoạch ngân sách năm sau',
      desc: 'Gợi ý từ Opex sắp gia hạn, Opex đã ghi nhận và Capex của năm sau; thêm đề xuất, ưu tiên, trần ngân sách, duyệt và theo dõi thực hiện.', scope: [] },
    { id: 'spend-contracts', feature: 'spend', spendRole: 'viewer', view: 'spend', sub: 'contracts', icon: 'scroll-text', label: 'Hợp đồng', title: 'Hợp đồng',
      desc: 'Hợp đồng gắn Opex / Capex, version theo từng bên chỉnh sửa, so sánh, chốt Final, bản ký và lịch xoá version cũ.', scope: [] },
    { id: 'spend-vendors', feature: 'spend', spendRole: 'viewer', view: 'spend', sub: 'vendors', icon: 'building-2', label: 'Nhà cung cấp', title: 'Nhà cung cấp',
      desc: 'Hồ sơ nhà cung cấp: tên viết tắt dùng thống nhất trong tài liệu, tên gọi khác, liên hệ, lịch sử báo giá / hợp đồng / chi phí.', scope: [] },
    { id: 'bom', feature: 'bom', view: 'bom', icon: 'list-tree', label: 'Chuẩn hoá BOM', title: 'Chuẩn hoá BOM',
      desc: 'Tải BOM thô (Dell Solutions Configurator, Cisco CCW, HPE, Excel thường) → xuất Excel chuyên nghiệp theo đầu mục, có tóm tắt cấu hình và giá.', scope: [] },
    { id: 'spend-quotes', feature: 'spend', spendRole: 'viewer', view: 'spend', sub: 'quotes', icon: 'scale', label: 'So sánh báo giá', title: 'So sánh báo giá',
      desc: 'Gói nhiều báo giá: đọc file Excel / Word / PDF scan, kiểm tra công thức tiền, so sánh từng hạng mục, phiếu đánh giá NCC, chọn NCC.', scope: [] },
    { id: 'spend-renovations', feature: 'spend', spendRole: 'viewer', view: 'spend', sub: 'renovations', icon: 'building', label: 'Cải tạo văn phòng', title: 'Cải tạo văn phòng — chi phí IT',
      desc: 'Dự án cải tạo theo tầng: quy mô, cấu hình, 3 khối Cabling / Network / VC; so sánh với các tầng đã làm theo chi phí / node và / phòng họp.', scope: [] },
    { id: 'spend-proposals', feature: 'spend', spendRole: 'viewer', view: 'spend', sub: 'proposals', icon: 'file-text', label: 'Tờ trình', title: 'Tờ trình',
      desc: 'Soạn tờ trình Mẫu A (trình TGĐ) / Mẫu B (trình Ban Mua sắm) từ mẫu .docx của công ty, AI viết nội dung theo dữ liệu Opex / Capex.', scope: [] },
    { id: 'report-archive', view: 'archive', icon: 'archive', label: 'Kho báo cáo', title: 'Kho báo cáo',
      desc: 'Bản PDF đã xuất và lưu lại (tick “Lưu vào kho” ở Trung tâm báo cáo): ai xuất, lúc nào, với tham số nào.', scope: [] },
    { id: 'reports', view: 'reports', icon: 'file-text', label: 'Trung tâm báo cáo', title: 'Trung tâm báo cáo',
      desc: 'Mọi báo cáo PDF và dữ liệu Excel / CSV, nhóm theo chức năng: Capacity & vận hành, Quy hoạch & Sizing, License & Inventory, Backup.', scope: ['vcenter'] },
    { id: 'settings', view: 'settings', sub: 'criteria', icon: 'settings', label: 'Cài đặt', title: 'Cài đặt — tiêu chí & ngưỡng',
      desc: 'Tiêu chí và ngưỡng đánh giá capacity / compliance, dùng chung cho màn hình, báo cáo PDF và quy hoạch.', scope: [] },
    { id: 'set-sources', view: 'settings', sub: 'sources', icon: 'plug', label: 'Nguồn dữ liệu', title: 'Cài đặt — nguồn dữ liệu',
      desc: 'Trạng thái vCenter, import thủ công và kết nối vCenter / NetBackup (admin).', scope: [] },
    { id: 'set-features', view: 'settings', sub: 'features', icon: 'toggle-right', label: 'Tính năng', title: 'Cài đặt — tính năng',
      desc: 'Bật / tắt module cho mọi người dùng.', scope: [] },
    { id: 'set-spend', view: 'settings', sub: 'spend', icon: 'bell-ring', label: 'Chi phí & nhắc hạn', title: 'Cài đặt — chi phí & nhắc hạn',
      desc: 'Năm tài chính, loại chi phí, mốc nhắc, kênh email / Discord, mẫu tờ trình và AI soạn tờ trình.', scope: [], spendRole: 'viewer', feature: 'spend' },
    { id: 'set-ai', view: 'settings', sub: 'ai', icon: 'bot', label: 'AI & MCP', title: 'Cài đặt — AI & MCP',
      desc: 'MCP server chỉ đọc cho AI phân tích log / hiệu suất của app (admin).', scope: [] },
    { id: 'set-users', view: 'settings', sub: 'users', icon: 'users', label: 'Người dùng & quyền', title: 'Cài đặt — người dùng & quyền',
      desc: 'Tài khoản, cách đăng nhập, passkey và vai trò từng module (admin).', scope: [] },
    { id: 'set-auth', view: 'settings', sub: 'auth', icon: 'shield-check', label: 'Đăng nhập & bảo mật', title: 'Cài đặt — đăng nhập & bảo mật',
      desc: 'SSO Entra ID, passkey cho tài khoản cục bộ, App Proxy (admin).', scope: [] },
    { id: 'set-brand', view: 'settings', sub: 'brand', icon: 'image', label: 'Thương hiệu', title: 'Cài đặt — thương hiệu',
      desc: 'Logo màn hình đăng nhập.', scope: [] },
];

/* ---------- Super-app menu (docs/design/super-app.md §2.1) ----------
   Two domains (Vận hành / Quản lý thông tin) + Hệ thống. Each menu entry groups
   existing pages (NAV_ITEMS ids) as in-page tabs; `path` is the hash route of a tab
   (#/backup/jobs). Old ids (#/bk-jobs) still work and are rewritten to the new path. */
const MENU = [
  { label: '', entries: [
    { key: 'home', label: 'Trang chủ', icon: 'house', tabs: [['home', '', 'Hôm nay'], ['executive', 'leader', 'Điều hành']] },
  ] },
  { label: 'Vận hành', entries: [
    { key: 'capacity', label: 'Capacity & Compliance', icon: 'gauge', tabs: [
      ['dashboard', '', 'Capacity'], ['compliance', 'compliance', 'Compliance'], ['history', 'trends', 'Xu hướng & biến động']] },
    { key: 'backup', label: 'Backup', icon: 'database-backup', tabs: [
      ['bk-overview', '', 'Tổng quan'], ['bk-clients', 'clients', 'Client & RPO'], ['bk-jobs', 'jobs', 'Job'],
      ['bk-schedule', 'schedule', 'Lịch & tuân thủ'], ['bk-coverage', 'coverage', 'Độ phủ VM'], ['bk-storage', 'storage', 'Storage & policy']] },
    { key: 'agenda', label: 'Lịch việc & nhắc hạn', icon: 'calendar-clock', tabs: [['agenda', '', 'Lịch việc']] },
    { key: 'simulate', label: 'Mô phỏng & quy hoạch', icon: 'git-branch', tabs: [
      ['whatif', '', 'What-if capacity'], ['planning', 'planning', 'Quy hoạch tài nguyên'], ['lic-whatif', 'license', 'What-if license']] },
    { key: 'sizing', label: 'Sizing máy chủ', icon: 'calculator', tabs: [['sizing', '', 'Sizing']] },
  ] },
  { label: 'Quản lý thông tin', entries: [
    { key: 'assets', label: 'Tài sản', icon: 'server', tabs: [
      ['inventory', '', 'Host & VM'], ['lic-hardware', 'hardware', 'ESXi & phần cứng'], ['lic-os', 'os', 'Hệ điều hành']] },
    { key: 'license', label: 'License', icon: 'badge-check', tabs: [
      ['lic-assess', '', 'Đánh giá & quyền sử dụng'], ['lic-snapshots', 'snapshots', 'Snapshot']] },
    { key: 'budget', label: 'Ngân sách & chi phí', icon: 'wallet', tabs: [
      ['spend-overview', '', 'Tổng quan ngân sách'], ['spend-opex', 'opex', 'Opex'], ['spend-capex', 'capex', 'Capex'], ['spend-plan', 'plan', 'Kế hoạch năm sau']] },
    { key: 'procure', label: 'Mua sắm', icon: 'shopping-cart', tabs: [
      ['spend-vendors', '', 'Nhà cung cấp'], ['spend-quotes', 'quotes', 'So sánh báo giá'], ['bom', 'bom', 'Chuẩn hoá BOM'],
      ['spend-renovations', 'renovations', 'Cải tạo văn phòng']] },
    { key: 'contracts', label: 'Hợp đồng & thanh toán', icon: 'scroll-text', tabs: [['spend-contracts', '', 'Hợp đồng'], ['spend-payments', 'payments', 'Đợt thanh toán']] },
    { key: 'docs', label: 'Tài liệu', icon: 'file-text', tabs: [['spend-proposals', '', 'Tờ trình'], ['reports', 'reports', 'Báo cáo'], ['report-archive', 'archive', 'Kho báo cáo']] },
  ] },
  { label: 'Hệ thống', entries: [
    { key: 'settings', label: 'Cài đặt', icon: 'settings', tabs: [
      ['settings', '', 'Tiêu chí & ngưỡng'], ['set-sources', 'sources', 'Nguồn dữ liệu'], ['bk-config', 'backup', 'Backup'],
      ['set-features', 'features', 'Tính năng'], ['set-spend', 'spend', 'Chi phí & nhắc hạn'], ['set-ai', 'ai', 'AI & MCP'],
      ['set-users', 'users', 'Người dùng & quyền'], ['set-auth', 'auth', 'Đăng nhập & bảo mật'], ['set-brand', 'brand', 'Thương hiệu']] },
  ] },
];

const callIf = (name, ...args) => { if (typeof window[name] === 'function') window[name](...args); };

/* Lazy modules: scripts fetched the first time a page of the module is opened (or a function of it is
   needed — uiRequire('spend').then(...)). Same ?v= fingerprint as index.html, same origin only. */
const ASSET_V = (() => { try { return new URL(document.currentScript.src).searchParams.get('v') || ''; } catch (e) { return ''; } })();
const MODULES = {
  spend: ['spend', 'spend_capex', 'spend_report', 'spend_contract', 'spend_proposal', 'spend_payment', 'spend_quote', 'spend_renovation', 'spend_plan', 'spend_import'],
  bom: ['bom'],
};
const moduleLoads = {};
const modulesReady = new Set();
function uiRequire(name) {
  if (moduleLoads[name]) return moduleLoads[name];
  const files = MODULES[name];
  if (!files) return Promise.reject(new Error(`Không có module ${name}`));
  // Sequential: later files of a module use top-level consts of the first one.
  moduleLoads[name] = files.reduce((p, f) => p.then(() => new Promise((resolve, reject) => {
    const el = document.createElement('script');
    el.src = `js/views/${f}.js?v=${encodeURIComponent(ASSET_V)}`;
    el.onload = resolve;
    el.onerror = () => reject(new Error('Không tải được giao diện, hãy tải lại trang.'));
    document.body.appendChild(el);
  })), Promise.resolve());
  moduleLoads[name].then(() => { modulesReady.add(name); }, () => { delete moduleLoads[name]; });   // failed: allow a retry
  return moduleLoads[name];
}

const VIEWS = {
  home: { section: '#viewHome', render: () => callIf('renderHomeView') },
  agenda: { section: '#viewAgenda', render: () => callIf('renderAgendaView') },
  entity: { section: '#viewEntity', render: () => callIf('renderEntityView') },
  executive: { section: '#viewExecutive', render: () => callIf('renderExecutiveView') },
  dashboard: { section: '#viewDashboard', render: () => {
    renderCapacityKpis(); renderDashboardCharts(); renderClusterTable();
    bindSortableHeaders('clusterTable', 'clusterTable', renderClusterTable); highlightSortHeaders('clusterTable', 'clusterTable');
  } },
  compliance: { section: '#viewCompliance', render: () => renderComplianceView() },
  history: { section: '#viewHistory', render: () => {
    // Snapshot-history page (needs the backend; skipped silently in static mode).
    if (typeof renderHistoryPanel === 'function' && typeof api !== 'undefined') renderHistoryPanel('techHistoryMount', { showVmChanges: true, followClusterFilter: true });
  } },
  inventory: { section: '#viewInventory', render: () => renderInventoryView() },
  whatif: { section: '#viewWhatif', render: () => renderWhatifView() },
  planning: { section: '#viewPlanning' },                       // static form bound once in app.js
  sizing: { section: '#viewSizing', render: () => callIf('renderSizingView') },
  licensing: { section: '#viewLicensing', render: () => callIf('renderLicensingView'),
    sub: { get: () => (typeof licState === 'function' && licState().sub) || 'hardware',
           set: (s) => { if (typeof licState === 'function') licState().sub = s || 'hardware'; }, fallback: 'lic-hardware' } },
  backup: { section: '#viewBackup', render: () => callIf('renderBackupView'),
    sub: { get: () => (typeof bkState === 'function' && bkState().sub) || 'overview',
           set: (s) => { if (typeof bkState === 'function') bkState().sub = s || 'overview'; }, fallback: 'bk-overview' } },
  spend: { section: '#viewSpend', module: 'spend', render: () => callIf('renderSpendView'),
    // Before spend.js is loaded the wanted tab waits in `pending` (applied once the module is in).
    sub: { pending: null,
           get: () => (typeof spState !== 'undefined' ? spState.sub : VIEWS.spend.sub.pending) || 'opex',
           set: (s) => { if (typeof spState !== 'undefined') spState.sub = s || 'opex'; else VIEWS.spend.sub.pending = s || 'opex'; },
           fallback: 'spend-opex' } },
  reports: { section: '#viewReports', render: () => callIf('renderReportsView') },
  archive: { section: '#viewArchive', render: () => callIf('renderArchiveView') },
  bom: { section: '#viewBom', module: 'bom', render: () => callIf('renderBomView') },
  settings: { section: '#viewSettings',
    sub: { get: () => appState.settingsSub || 'criteria', set: (s) => { appState.settingsSub = s || 'criteria'; }, fallback: 'settings' },
    // Settings tabs whose panel is built by a view module when shown.
    tabRender: { ai: () => callIf('renderAiSettings'), auth: () => callIf('renderAuthSettings'), users: () => callIf('renderUsersSettings'),
      spend: () => { if (currentUser && currentUser.spendRole && currentUser.spendRole !== 'none') uiRequire('spend').then(() => callIf('initSpendSettings', currentUser), (e) => uiAlert(e.message)); } } },
};

/* Render a view, loading its lazy module first. A page left before the load finished is not drawn. */
function renderView(name) {
  const v = VIEWS[name];
  if (!v || !v.render) return;
  if (!v.module || modulesReady.has(v.module)) { v.render(); return; }
  const mount = document.querySelector(v.section);
  if (mount && !mount.querySelector('[data-lazy-note]')) mount.insertAdjacentHTML('afterbegin', '<div class="empty-note" data-lazy-note>Đang tải…</div>');
  uiRequire(v.module).then(() => {
    if (mount) mount.querySelectorAll('[data-lazy-note]').forEach(el => el.remove());
    if (v.sub && v.sub.pending) { v.sub.set(v.sub.pending); v.sub.pending = null; }
    if (appState.view === name) v.render();
  }, (e) => { if (mount) mount.querySelectorAll('[data-lazy-note]').forEach(el => { el.textContent = e.message; el.classList.add('tone-crit'); }); });
}

/* Spend exports on the Reports page work before the module is loaded. */
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-spend-export]');
  if (!b) return;
  e.preventDefault();
  uiRequire('spend').then(() => spendExport(b.dataset.spendExport, b), (err) => uiAlert(err.message));
});
