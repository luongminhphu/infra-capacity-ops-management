# CLAUDE.md — Infra Capacity Ops Management (ICO)

Ứng dụng theo dõi capacity / compliance / what-if cho hạ tầng VMware và giám sát backup
Veritas NetBackup. Đọc dữ liệu **chỉ đọc** từ các vCenter và NetBackup primary server (khai báo ở
Cài đặt → Kết nối nguồn dữ liệu, hoặc bằng biến `VCENTER_i_*` / `NETBACKUP_i_*`),
lưu lịch sử vào PostgreSQL, hiển thị dashboard Điều hành / Kỹ thuật và xuất báo cáo PDF / Excel.
Tổng quan + triển khai trong `README.md`; chi tiết nghiệp vụ từng tính năng và vận hành trong
`docs/huong-dan-chi-tiet.md` (viết bằng tiếng Việt). README giữ ngắn — mục mới viết vào hướng dẫn chi tiết.

## Stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2.0, PostgreSQL 16 (test dùng SQLite),
  pyVmomi (vCenter), urllib (NetBackup REST), WeasyPrint (PDF), openpyxl (Excel),
  python-jose + bcrypt (JWT cookie), Microsoft Entra ID OIDC (`auth_entra.py`).
- **Frontend**: HTML + vanilla JS + CSS, **không có build step** (`frontend/`). FastAPI phục vụ
  luôn file tĩnh trên cùng port với API.
- **Triển khai**: Docker image build từ `backend/Dockerfile` (context = repo root), push lên GHCR
  qua `.github/workflows/build-push.yml`; chạy bằng Docker Compose hoặc Kubernetes (`k8s/`, Rancher).

## Lệnh thường dùng

```bash
# Chạy toàn bộ (cần .env, copy từ .env.example)
docker compose up -d --build
docker compose logs -f app

# Test backend (SQLite tạm, NetBackup demo — xem backend/tests/conftest.py)
cd backend && pip install -r requirements.txt pytest httpx && python -m pytest tests -q

# Chạy thử không cần vCenter/NetBackup thật
VCENTER_1_DEMO=true NETBACKUP_1_DEMO=true ...
```

## Bản đồ code

| Khu vực | File chính |
|---|---|
| Thu thập vCenter | `backend/app/vcenter/client.py`, `vcenter/demo_generator.py` |
| Thu thập NetBackup | `backend/app/backup/client.py`, `collector.py`, `status_codes.py`, `analysis.py`, `demo_generator.py` |
| Lịch poll | `backend/app/collectors/scheduler.py` (thread pool, mỗi nguồn 1 DB session) |
| Danh sách nguồn | `backend/app/sources.py` (biến môi trường + bảng `data_sources`, `Settings.vcenters()` / `netbackups()`), `secrets_box.py` (mã hoá), `routers/sources.py` |
| Mô hình capacity + compliance | `backend/app/capacity.py` ⇄ `frontend/js/core.js` (**phải giữ khớp số liệu**) |
| Quy hoạch / sizing / license | `planning.py`, `sizing.py`, `sizing_advisory.py`, `licensing/` |
| Báo cáo PDF | `report_pdf.py` (helper dùng chung) + `report_*.py` |
| Excel | `excel_sizing.py`, `excel_backup.py`, `routers/export.py` |
| Phạm vi / export / service dùng chung | `backend/app/scope.py` (`Scope.from_query`, `split_csv`), `exports.py` (registry mọi file xuất + `/api/exports`; Kho báo cáo đọc từ đây), `services/entities.py`, `backup/service.py`, `licensing/inventory.py` (module khác gọi service, không import router), `excel_style.py` / `pdf_kit.py` (giao diện Excel / PDF chung) |
| Dịch vụ nền tảng dùng chung (mọi module dùng, không import chéo giữa module) | `backend/app/shared/` — `xlsx_io.py` (mọi chỗ đọc .xlsx upload: chống zip-bomb, chỉ đọc, giới hạn dòng / cột), `ai_transport.py` (gọi Anthropic; `spend/ai.py` là alias), `webhook.py` (Discord), `filetype.py` (nhận dạng PDF / docx / xlsx theo nội dung), `text.py` (`parse_number`, `norm_text`). Không đặt tên gói trùng thư viện chuẩn (vd `platform`) |
| Schema DB | `models.py` + migration bổ sung trong `database.py::ensure_schema()` |
| Auth | `security.py` (`get_current_user`, `require_admin`), `auth_entra.py` (SSO Entra ID OIDC), `passkeys.py` (WebAuthn cho tài khoản cục bộ, link mời), `routers/auth.py`, `routers/users_admin.py` (tài khoản, passkey, trạng thái App Proxy); `PUBLIC_URL` = URL ngoài (App Proxy); UI `frontend/js/views/passkey.js` |
| MCP server (chỉ đọc) | `mcp_server.py`, `observability.py` |
| Chi phí & Hợp đồng IT (`spend`) | `backend/app/spend/` (`models.py`, `service.py`, `fiscal.py`, `roles.py`, `files.py`, `notify.py`, `reminders.py`, `capex.py`, `report.py`, `contracts.py`, `proposal_doc.py`, `ai.py`, `quotes.py`, `renovation.py`, `budget_plan.py` — kế hoạch năm sau, `importer.py` — nhập Opex / Capex từ Excel), `routers/spend.py`, `spend_admin.py`, `spend_capex.py`, `spend_reports.py`, `spend_contracts.py`, `spend_payments.py`, `spend_proposals.py`, `spend_quotes.py`, `spend_renovations.py`, `spend_plans.py`, `spend_import.py`, `report_spend.py`, `report_renovation.py` (PDF), `excel_spend.py`, `excel_quotes.py`, `excel_renovation.py`, `excel_budget_plan.py`, `frontend/js/views/spend.js`, `spend_capex.js`, `spend_report.js`, `spend_contract.js`, `spend_payment.js`, `spend_proposal.js`, `spend_quote.js`, `spend_renovation.js`, `spend_plan.js`, `spend_import.js`; thiết kế `docs/design/spend-module.md`, `spend-vendor-quotes.md`, `spend-budget-plan.md`, `spend-import.md` |
| Chuẩn hoá BOM | `backend/app/bom.py` (đọc Dell configurator / Cisco CCW / HPE / thường, phân loại, tóm tắt), `excel_bom.py`, `routers/bom.py`, `frontend/js/views/bom.js` |
| Trang chủ / hộp việc / lịch việc / cảnh báo Discord 90% | `backend/app/inbox.py` (nguồn việc từng module, cache 60 s), `ops_alerts.py`, `routers/home.py` (`/api/inbox`, `/api/agenda`, `/api/me/prefs`), `frontend/js/views/home.js` |
| Tìm kiếm Ctrl + K / thẻ Host-VM | `backend/app/routers/entities.py` (`/api/search`, `/api/entities/host|vm`), `frontend/js/views/entity.js`, `ui.js` (`uiEntityLink`) |
| AI dùng chung (chính sách, che tên, hạn mức, `ai_runs`, cache) | `backend/app/ai_platform.py`, `ai_tasks.py` (bản tin, tổng kết PDF), `routers/ai_admin.py`, transport `shared/ai_transport.py`; UI `frontend/js/views/ai.js`. AI cho dữ liệu biến động (lỗi backup, ánh xạ cột nhập + `import_mappings`, phân loại HĐH, biến động VM): `ai_data.py`, `routers/ai_data.py`, `frontend/js/views/ai_data.js`. AI nâng cao (nhận định sizing, tóm tắt version hợp đồng, đọc hoá đơn, Hỏi ICO với công cụ chỉ đọc): `ai_advanced.py`, `ask_ico.py`, transport `shared/ai_transport.py` (`complete_json_pdf`, `run_tools`), UI `frontend/js/views/ai_assist.js`. Kho báo cáo PDF: `report_archive.py` (middleware theo header `X-ICO-Archive`), `routers/report_archive.py`. Mọi tác vụ AI mới phải đi qua `ai_platform.run()` |
| Giao diện | `frontend/index.html`, `css/styles.css` (HSC design system), `js/core.js` (chỉ mô hình capacity + trạng thái + nạp dữ liệu), `js/routes.js` (**bảng route duy nhất**: `PAGES`, `MENU` 2 miền + tab, `VIEWS` = section / render / tab con — trang mới thêm ở đây), `js/app.js` (điều hướng `#/<mục>/<tab>`), `js/ui.js` (pill, trạng thái, hộp thoại, `uiEmpty` / `uiNote`, `chartPalette`, bảng sắp xếp, `uiPref` lưu tuỳ chọn trên server), `js/views/*.js` (`capacity.js`, `whatif.js`…; Chi phí và BOM nạp khi mở trang qua `uiRequire` — gọi hàm của chúng từ module khác phải `uiRequire('spend').then(...)`), `vendor/` (Chart.js, lucide ghim phiên bản, không dùng CDN); thiết kế `docs/design/super-app.md` |

## Quy tắc bắt buộc

1. **vCenter và NetBackup chỉ được ĐỌC.** Không bao giờ gọi API làm thay đổi hạ tầng
   (power, reconfigure, snapshot, xoá, POST/PUT/DELETE tới NetBackup ngoài `/login` `/logout`).
   Tài khoản dịch vụ (`infra.mon@vsphere.local`, user NBU chỉ đọc) được cấp quyền tối thiểu.
   Xem skill `vmware-collector` và `netbackup-api`.
2. **Mọi nguồn dữ liệu có chế độ DEMO.** Thêm trường mới vào client thật thì phải thêm vào
   `demo_generator.py` tương ứng, để môi trường demo và test không vỡ.
3. **`capacity.py` và `frontend/js/core.js` phải cho cùng kết quả** (overcommit, ngưỡng trạng thái,
   mã đối soát FNV-1a). Sửa một bên thì sửa bên kia trong cùng thay đổi. Xem skill `capacity-math`.
4. **Thay đổi schema**: dự án không dùng Alembic. `create_all()` không sửa bảng đã có, nên cột mới
   phải được thêm vào dict `additive` trong `database.py::ensure_schema()` (kiểu nullable hoặc có
   `DEFAULT`, chạy được cả SQLite và PostgreSQL, idempotent). Không đổi tên / xoá cột trong cùng
   một bản phát hành — hai pod cũ và mới chạy song song trong lúc rollout.
5. **Giao diện và báo cáo viết tiếng Việt**, có dấu đầy đủ, văn phong ngắn, nêu thẳng vấn đề →
   quyết định. Code, tên biến, comment code viết tiếng Anh. Field dữ liệu dùng camelCase giống
   frontend (`ramGB`, `isPoweredOn`, `vcpu`...).
6. **Design**: dùng token trong `frontend/css/styles.css` và `COLORS` trong `report_pdf.py`,
   không hard-code màu mới. Trạng thái luôn là 4 mức AN TOÀN / THEO DÕI / CẢNH BÁO / VƯỢT NGƯỠNG.
   Xem skill `hsc-design-system` và `pdf-report`.
7. **Bảo mật**:
   - Endpoint mới phải chọn rõ `Depends(get_current_user)` hay `Depends(require_admin)`;
     thao tác thay đổi cấu hình hệ thống, user, tính năng nên dùng `require_admin`.
   - Mọi chuỗi đến từ vCenter / NetBackup / người dùng đưa vào HTML (web hoặc PDF) phải escape
     (`esc()` trong Python, hàm escape sẵn có trong JS). Không để dữ liệu ngoài tạo ra URL mà
     WeasyPrint sẽ tải (`<img src>`, `url()`): logo chỉ dùng `data:` URL đã kiểm tra.
   - Không log hoặc trả về mật khẩu, API key, token. `MCP_MASK_HOSTS` mặc định che tên host.
     Mật khẩu / API key lưu trong DB phải qua `secrets_box.encrypt()`; API chỉ trả `hasPassword` /
     `hasApiKey`. Lấy danh sách nguồn bằng `get_settings().vcenters()` / `.netbackups()` (đã gộp và
     giải mã), không đọc thẳng biến `VCENTER_i_*`.
   - Không commit `.env`, `k8s/02-secret.yaml`, chứng chỉ thật.
8. **Hiệu năng**: đọc vCenter theo lô bằng PropertyCollector; ghi snapshot bằng bulk INSERT;
   tránh truy vấn lịch sử theo từng VM trong vòng lặp (N+1).
9. **Test**: thêm hoặc sửa logic tính toán (`capacity`, `planning`, `sizing`, `licensing`,
   `backup/analysis`, `spend/budget_plan`) thì phải có test trong `backend/tests/` với số liệu cụ thể. Chạy
   `python -m pytest tests -q` trước khi kết thúc.

## Skill của dự án (`.claude/skills/`)

- `vmware-collector` — đọc vCenter bằng pyVmomi, demo generator, scheduler.
- `netbackup-api` — NetBackup REST chỉ đọc, parser, mã lỗi, RPO.
- `capacity-math` — công thức overcommit, HT, ngưỡng, N+1, dự phóng, mã đối soát.
- `hsc-design-system` — token màu / chữ / khoảng cách, light/dark, trạng thái.
- `pdf-report` — quy ước báo cáo PDF A4 bằng WeasyPrint.

Quét bảo mật: chạy `/security-review` (thay đổi đang chờ) hoặc `/claude-security`
(quét sâu cả repo) trong Claude Code trước khi tạo PR.
