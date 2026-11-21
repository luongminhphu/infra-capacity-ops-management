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
| Proxmox VE / Nutanix AHV | `backend/app/hypervisors/` (`proxmox.py` API token, `nutanix.py` Prism Element v2.0, chỉ GET qua `storage/http.py::JsonClient`); cùng dạng dòng host / VM / extras với vCenter, lưu như vCenter (`VCenterConfig.platform`, kind `proxmox` / `nutanix` trong `sources.py`, rẽ nhánh ở `scheduler._sync_vcenter_locked`); `platform` trên dòng host (`data_access`), license VMware bỏ qua host không phải VMware (`licensing/assess.py`) |
| Thu thập NetBackup | `backend/app/backup/client.py`, `collector.py`, `status_codes.py`, `analysis.py`, `demo_generator.py` |
| Lịch poll | `backend/app/collectors/scheduler.py` (thread pool, mỗi nguồn 1 DB session); job nền chỉ chạy ở pod leader (`leader.py`, advisory lock PostgreSQL; khoá theo nguồn giữa các pod) |
| Sức khoẻ hạ tầng | `backend/app/vcenter/extras.py` (datastore, snapshot, alarm, sức khoẻ host — cùng phiên vCenter), `infra.py` (trạng thái + mức + dự báo đầy), `metrics.py` (chuỗi thời gian thô → giờ → ngày), `alert_rules.py` (Cài đặt → Cảnh báo) + `ops_alerts.py`, `routers/infra.py`, `frontend/js/views/infra.js` |
| Storage Pure / NetApp | `backend/app/storage/` (`http.py` client JSON chỉ GET, `pure.py` Purity REST 2.x, `netapp.py` ONTAP REST, `collector.py`, `demo_generator.py`); ghép volume / LUN ↔ datastore (NAA, NFS) và mức mảng ở `infra.py` (`storage()`, `datastore_backing()`); nguồn khai báo trên giao diện (`sources.py` kind `pure` / `netapp`) |
| Phần cứng Dell OME (ICO v2 G4) | `backend/app/hardware/` (`ome.py` client OME 4.x chỉ đọc + parser Devices / SubSystemHealth, quy đổi Status 1000 ok · 3000 bad · 4000 crit · khác warn; `collector.py` thay trạng thái theo nguồn, `models.py` bảng `hw_source_state` / `hw_device_state` / `hw_component_health`, `demo_generator.py` PowerEdge khớp service tag host demo; G4b: `collector.plan_for` — InventoryDetails + baseline firmware mỗi `OME_INVENTORY_HOURS`, bảo hành mỗi `OME_WARRANTY_HOURS`, alert tăng dần theo `hw_source_state.alert_cursor`; bảng `hw_device_inventory` / `hw_disk_state` / `hw_part_state` / `hw_firmware_state` / `hw_warranty` / `hw_alert` (giữ `HW_ALERT_RETENTION_MONTHS`, `collectors/retention.py`); `warranty.py` đối chiếu bảo hành Dell ↔ Opex 4 ca + mã đối soát + dòng Kế hoạch năm sau nguồn `warranty`; `GET /api/assets/dc/warranty-check`, khối trên trang Thiết bị Data Center; hộp việc nguồn `hardware` + nhóm bảo hành trong nguồn `asset`, `alert_rules` nguồn `hardware`); nguồn kind `ome` trong `sources.py` (`Settings.hardware()`), `scheduler._sync_ome_task`; ghép vào sổ CI ở `cmdb/builder.py::_hardware` (physical_server theo service tag, khoá `idrac_ip` / `mac` / tên HĐH, liên kết yếu theo hostname, bare-metal); G4c: `hardware/service.py` (danh sách / chi tiết / khối thẻ — một nguồn số cho trang, PDF, Excel; mức tổng; `idrac_url` chỉ cho IP hợp lệ; lọc owner qua VM trên máy), `routers/hardware.py` (`/api/hardware`, `/servers/{nguồn}/{id}`, `/report.pdf|xlsx`), `report_hardware.py`, `excel_hardware.py`, `frontend/js/views/hardware.js` (Vận hành → Phần cứng, `#/hardware?s=&d=`), khối Dell OME trên thẻ Máy chủ vật lý (`services/entities.server_card`, `entity.js::enOmeHtml`); CI `physical_server` luôn mang tên = service tag, tên OME ở `attrs.omeName`; fixture `backend/tests/fixtures/ome_*.json` |
| Sự kiện + sự cố (ICO v2 G5) | `backend/app/events/` (`models.py` bảng `ops_event` chỉ thêm / `ops_event_open` / `ops_incident`, `service.py` `record_transitions` so với sự kiện đang mở — nguồn lỗi giữ nguyên, gom theo CI, tự đóng, chập chờn ≥ 4 lần / 6 giờ báo Discord một lần mỗi kênh (`flap_sent_channels`), MTTA / MTTR theo tháng giờ VN, retention 13 tháng); nguồn = mục Hộp việc của 8 nguồn hạ tầng, khoá `alertKey`, CI qua gợi ý `ci` trên `inbox.item()` (khoá host / cluster / datastore `cmdb/models.vc_key`, dùng chung với `builder.py`); mục Hộp việc có sự kiện đang mở thêm liên kết “Sự cố #N” (`routers/home.py`); chạy ở leader trong bước `alerts` (`scheduler._ops_alerts`: `inbox._compute` một lần cho sự kiện + Discord); `routers/incidents.py`, `/api/ci/{id}/timeline`, `frontend/js/views/incidents.js` (Vận hành → Sự cố), mục Timeline trên thẻ đối tượng |
| Nhật ký thay đổi + gom sự cố theo CI (ICO v2 G6) | `backend/app/vcenter/events.py` (`QueryEvents` theo cửa sổ con trỏ → now, 12 loại event, đủ 1.000 thì chia đôi cửa sổ; cùng phiên với kiểm kê qua `client.collect_inventory_full(cfg, events_since)`), `vcenter/demo_generator.collect_events`; `events/models.py` bảng `change_log` (UNIQUE vCenter + id event, `handled`) / `change_cursor`; `events/changes.py` (`store` gắn CI + chống trùng, trang, thẻ đối tượng, “thay đổi 24 giờ trước sự cố”, phần chỉ thấy qua so sánh snapshot `history.get_vm_changes`); gom sự cố `events/service._Correlator` (liên kết strong của `cmdb/impact`, `EVENT_CORRELATE_MINUTES` quanh giờ của event, gộp sự cố tách rời, `root_ci_id` = CI cao nhất có sự kiện); HA restart / host lỗi / mất kết nối = sự kiện một lần (`changes.POINT_LEVEL`, nguồn `vcenter`); `GET /api/changes`, `/api/ci/{id}/changes`; tab Vận hành → Sự cố & thay đổi → Nhật ký thay đổi (`incidents.js::renderChangesView`) |
| ServiceDesk Plus (ICO v2 G7a) | `backend/app/itsm/` (`sdp.py` client API v3 chỉ GET, header `authtoken` = technician key (`secrets_box`) + `Accept` v3, danh sách theo template + `last_updated_time` > cursor (sắp theo `last_updated_time` để không sót yêu cầu sửa sau), chi tiết `/requests/{id}`, giới hạn `rate_per_min`, retry có backoff (không retry 401 / 403 / 404), `parse_request`, `clean_html` (bỏ script / handler, ≤ 64 KB), `kind_of` theo template / tiêu đề; `collector.py` nạp lùi từ `backfill_from` theo lô `MAX_PER_RUN`, commit sau mỗi trang (dừng giữa chừng thì chạy tiếp), xong nạp lùi thì đọc mỗi `interval_minutes`; `models.py` bảng `itsm_request` / `itsm_source_state`; `demo_generator.py` 60 yêu cầu có bảng (chuẩn / lệch nhãn / thiếu cột / gộp ô) + 5 chữ tự do); kind `sdp` trong `sources.py` (`Settings.itsm()`, template / ngày nạp lùi / tốc độ / chu kỳ), `scheduler._sync_itsm_task`, nhóm *ServiceDesk* ở `source_status.py`, `orphans.py`; biến mặc định `ITSM_BACKFILL_FROM` / `ITSM_RATE_PER_MIN` / `ITSM_INTERVAL_MINUTES` / `ITSM_TEMPLATES` |
| IPAM GestióIP | `backend/app/ipam/` (`gestioip.py` client API 3.5 chỉ đọc theo danh sách `request_type`, parser JSON; `collector.py`, `models.py` bảng `ipam_source_state` / `ipam_network` / `ipam_host` / `ipam_vlan`, `demo_generator.py` IP khớp VM / iDRAC demo + chênh lệch cố ý); kind `gestioip` trong `sources.py` (`Settings.ipam()`, cột serial / MAC tuỳ chỉnh), `scheduler._sync_ipam_task`; nhóm *IPAM* ở `source_status.py`; dùng cho đối soát IP (`docs/design/ico-correlation.md`) |
| Danh sách nguồn | `backend/app/sources.py` (biến môi trường + bảng `data_sources`, `Settings.vcenters()` / `netbackups()`), `secrets_box.py` (mã hoá), `routers/sources.py`; trạng thái kết nối mọi nguồn theo lần đồng bộ thật `source_status.py` (ok / error / stale / never, nhóm Ảo hoá / Backup / Storage / Dell OME), `GET /api/sources/status`, `POST /api/sources/sync-all` (`?background=1` → `202` + `runId`, tiến độ `GET /api/sources/sync-runs/{runId}` từ `job_runs`; trang dùng chế độ này) + `POST /api/admin/sources/{id}/sync` (admin); chip nhóm trên thanh trên `app.js::renderSourceStatusChips` |
| Mô hình capacity + compliance | `backend/app/capacity.py` ⇄ `frontend/js/core.js` (**phải giữ khớp số liệu**) |
| Quy hoạch / sizing / license | `planning.py`, `sizing.py`, `sizing_advisory.py`, `licensing/` |
| Báo cáo PDF | `report_pdf.py` (helper dùng chung) + `report_*.py` (vd `report_infra.py` Sức khoẻ hạ tầng từ `infra.report_data`) |
| Excel | `excel_sizing.py`, `excel_backup.py`, `excel_infra.py`, `excel_cmdb.py` (Sổ CI, `GET /api/ci/export.xlsx`), `routers/export.py` |
| Trung tâm báo cáo (quy tắc 10) | `frontend/index.html` `#viewReports` (nhóm Capacity · Hạ tầng & phần cứng · Hệ thống & Owner · Quy hoạch & Sizing · License · Backup · Chi phí) + `js/views/reports.js` (`RPT_ACTIONS`; báo cáo mới: thêm thẻ + action + dòng `exports.py` có `center`); ẩn nhóm theo module ở `app.js::applyFeatures`; kiểm bằng `tests/test_report_center.py` |
| Sổ CI (ICO v2 G1) | `backend/app/cmdb/` (`models.py` bảng `ci` / `ci_key` / `ci_link`, `registry.py` ghi theo tập — upsert / thay khoá / thay liên kết / `retire_unseen`, `builder.py` `build_all()` dựng lại sau mỗi vòng sync ở pod leader, `query.py` đọc có lọc theo tính năng / quyền + `card_extras` cho thẻ, `impact.py` tác động down / up theo tầng), `routers/cmdb.py` (`/api/ci`, `/api/ci/{id}`, `/api/ci/{id}/impact`, `/api/ci/stats`); khoá chuẩn `shared/identity.py`; CI không thấy quá `CMDB_RETIRE_DAYS` → `retired`, không xoá |
| Hệ thống & Owner (ICO v2 G2) | `backend/app/owners/` (`models.py` bảng `systems` / `system_members` / `owner_rules` / `owner_sequences`, `service.py` CRUD + gán VM — manual thắng tự động, một VM một hệ thống — + độ phủ owner + audit, `excel.py` nhập / xuất cùng mẫu, `rules.py` gán tự động manual > attribute > tag > rule > itsm, chạy ở leader sau bộ dựng CI, `plan()` = Chạy thử, đề xuất `owner_proposals`, `review.py` kỳ rà soát 6 tháng — `review_campaigns` / `review_items` / `review_notices` (outbox email: giành dòng trước khi gửi SMTP, UNIQUE kỳ + người nhận + loại, `dry_run`; G3b: `decide` / `advance` nhắc − 7 / − 2, leo thang, đóng + 7 / `verify` / `summary`, `REVIEW_INFRA_EMAIL`, `REVIEW_UNIT_COST_*`), `routers/reviews.py` Cài đặt → Rà soát owner (kết quả, hàng gán lại, `report_review.py` PDF, `excel_review.py`; nguồn hộp việc `review`), xác nhận của owner ở `routers/owner_portal.py` `/api/owner/reviews*` + `owner_portal.js` (`#/owner/review/<id>`), `routers/owner_rules.py` (Cài đặt → Owner: `/api/systems/rules*`, `/api/systems/proposals*`, đăng ký trước `owners`), `routers/owners.py` (`/api/systems*`; ghi cần `require_owner_admin` = admin hoặc `users.owner_admin`, bật ở Cài đặt → Người dùng & quyền), `frontend/js/views/owners.js` (Quản lý thông tin → Hệ thống & Owner; `osAssignDialog` dùng chung với thẻ VM; form 3 nhóm Owner & Backup / Support (`support_emails` CSV ≤ 5, chỉ liên hệ, không quyền cổng Owner) / thông tin; *Nhân bản* = `osForm(s, {clone})`; báo cáo *VM theo hệ thống* `owners/inventory.py` (một nguồn số) → `report_systems.py` / `excel_systems.py`, `GET /api/systems/report.pdf|xlsx`; bản nháp email rà soát ISO 27001 `owners/mail_drafts.py` (bảng `owner_mail_templates`, trường `{…}` cố định, .eml `X-Unsent: 1` / .zip, không gửi), `/api/systems/mail-template`, `/api/systems/mail-drafts*` (owner admin), `osMailDialog`; tab VM chưa có owner lọc nhiều giá trị qua `ui.js::uiMultiSelect`, `GET /api/systems/coverage?vcenter=&cluster=&power=&folder=&ids=`) |
| Phạm vi / export / service dùng chung | `backend/app/vm_ip.py` (IP cạnh tên VM ở mọi danh sách, quy tắc 9), `backend/app/scope.py` (`Scope.from_query`, `split_csv`), `exports.py` (registry mọi file xuất + `/api/exports`; Kho báo cáo đọc từ đây), `services/entities.py`, `backup/service.py`, `licensing/inventory.py` (module khác gọi service, không import router), `excel_style.py` / `pdf_kit.py` (giao diện Excel / PDF chung) |
| Dịch vụ nền tảng dùng chung (mọi module dùng, không import chéo giữa module) | `backend/app/shared/` — `xlsx_io.py` (mọi chỗ đọc .xlsx upload: chống zip-bomb, chỉ đọc, giới hạn dòng / cột), `ai_transport.py` (gọi Anthropic; `spend/ai.py` là alias), `webhook.py` (Discord), `mailer.py` (SMTP chung: `send`, `SmtpSettings`, chặn CR/LF header; cấu hình SMTP lấy qua `spend.notify.smtp_settings(db)`), `filetype.py` (nhận dạng PDF / docx / xlsx theo nội dung), `text.py` (`parse_number`, `norm_text`), `identity.py` (khoá chuẩn: `norm_serial`, `norm_fqdn`, `short_name`, `norm_mac`, `norm_naa`, `norm_ip`, `nbu_client_key` — mọi chỗ so khớp serial / tên / IP / NAA dùng hàm ở đây), `money.py` (tiền số nguyên: `to_minor` / `from_minor`, micro-USD), `schema.py` (`in_check`: CHECK `col IN (…)` cho model mọi gói), `stored_text.py` (kiểm JSON / CSV trong cột Text khi ghi — cột mới kiểu này phải đăng ký ở cuối `models.py`, hoặc cuối `models.py` của gói như `spend/`, `cmdb/`). Không đặt tên gói trùng thư viện chuẩn (vd `platform`) |
| Schema DB | `models.py` + migration bổ sung trong `database.py::ensure_schema()` |
| Auth | `security.py` (`get_session_user` = đăng nhập mọi phạm vi — chỉ cho `/api/auth/me` + cổng Owner; `get_current_user` = app đầy đủ, **403 cho `access_scope="owner"`** — mọi endpoint mới dùng cái này; `require_admin`; test `test_owner_access.py::test_every_api_route_requires_full_scope_unless_allowlisted` chặn route mới lọt), `owners/access.py` (`get_owner_user`: hệ thống mà email user là owner / deputy), `routers/owner_portal.py` (`/api/owner/*`), `frontend/js/views/owner_portal.js`; bộ lọc owner toàn app `scope.py::OwnerScope` + `frontend/js/owner_scope.js` (tham số gắn ở `apiRequest` / `uiDownload`), `auth_entra.py` (SSO Entra ID OIDC), `passkeys.py` (WebAuthn cho tài khoản cục bộ, link mời), `routers/auth.py`, `routers/users_admin.py` (tài khoản, passkey, trạng thái App Proxy); `PUBLIC_URL` = URL ngoài (App Proxy); UI `frontend/js/views/passkey.js` |
| MCP server (chỉ đọc) | `mcp_server.py` (14 tool `ico_*` + bí danh tên cũ, `title` / annotation đủ / `outputSchema` + `structuredContent`, `_check_args` kiểm tham số theo schema → lỗi tool có hướng dẫn, phân trang `offset` / `hasMore` / `nextOffset`, 5 prompt), `mcp_extra.py` (`ico_source_status`, `ico_job_runs`, `ico_data_freshness`, `ico_ai_usage`, `ico_config_check`, `ico_trace_request`; bộ đánh giá `docs/design/ico-mcp-eval.xml`), `mcp_config.py`, `observability.py` (log vòng nhớ + `app_logs` có `request_id` / `pod`, `quiet_libraries()`, histogram độ trễ nội suy), `jobruns.py` (bảng `job_runs`: mọi lần chạy nền — đồng bộ mọi nhóm nguồn qua `scheduler.run_syncs`, dựng CI / owner / rà soát / cảnh báo / retention / metrics qua `jobruns.timed`, vòng poll — đọc bằng tool `sync_history`); rà soát + lộ trình cải tiến `docs/design/ico-ai-mcp-review.md` (đợt A–D), skill `mcp-builder` |
| Chi phí & Hợp đồng IT (`spend`) | `backend/app/spend/` (`models.py`, `service.py`, `fiscal.py`, `roles.py`, `files.py`, `notify.py`, `reminders.py`, `capex.py`, `report.py`, `contracts.py`, `proposal_doc.py`, `ai.py`, `quotes.py`, `renovation.py`, `budget_plan.py` — kế hoạch năm sau, `importer.py` — nhập Opex / Capex từ Excel), `routers/spend.py`, `spend_admin.py`, `spend_capex.py`, `spend_reports.py`, `spend_contracts.py`, `spend_payments.py`, `spend_proposals.py`, `spend_quotes.py`, `spend_renovations.py`, `spend_plans.py`, `spend_import.py`, `report_spend.py`, `report_renovation.py` (PDF), `excel_spend.py`, `excel_quotes.py`, `excel_renovation.py`, `excel_budget_plan.py`, `frontend/js/views/spend.js`, `spend_capex.js`, `spend_report.js`, `spend_contract.js`, `spend_payment.js`, `spend_proposal.js`, `spend_quote.js`, `spend_renovation.js`, `spend_plan.js`, `spend_import.js`; thiết kế `docs/design/spend-module.md`, `spend-vendor-quotes.md`, `spend-budget-plan.md`, `spend-import.md`; skill `spend-module` |
| Thiết bị Data Center | `backend/app/dc_assets.py` (đọc Excel danh sách thiết bị, nhập tay, tuổi theo ngưỡng 5 năm, ghép SN ↔ `opex_assets`, gợi ý *Thay thế thiết bị* cho Kế hoạch năm sau), `routers/dc_assets.py`, `frontend/js/views/dc_assets.js`; inbox nguồn `asset` |
| Chuẩn hoá BOM | `backend/app/bom.py` (đọc Dell configurator / Cisco CCW / HPE / thường, phân loại, tóm tắt), `excel_bom.py`, `routers/bom.py`, `frontend/js/views/bom.js` |
| Trang chủ / hộp việc / lịch việc / cảnh báo Discord 90% | `backend/app/inbox.py` (nguồn việc từng module, cache 60 s), `ops_alerts.py`, `routers/home.py` (`/api/inbox`, `/api/agenda`, `/api/me/prefs`), `frontend/js/views/home.js` |
| Tìm kiếm Ctrl + K / thẻ đối tượng | `backend/app/routers/entities.py` (`/api/search` — cả serial / asset tag / datastore, `/api/entities/host|vm`, `/api/entities/server/{serial}`, `/api/entities/datastore/{id}`; thẻ CI chung `#/entity?type=ci&id=`), `frontend/js/views/entity.js`, `ui.js` (`uiEntityLink`) |
| AI dùng chung (chính sách, che tên, hạn mức, `ai_runs`, cache) | `backend/app/ai_platform.py`, `ai_tasks.py` (bản tin, tổng kết PDF), `routers/ai_admin.py`, transport `shared/ai_transport.py`; UI `frontend/js/views/ai.js`. AI cho dữ liệu biến động (lỗi backup, ánh xạ cột nhập + `import_mappings`, phân loại HĐH, biến động VM): `ai_data.py`, `routers/ai_data.py`, `frontend/js/views/ai_data.js`. AI nâng cao (nhận định sizing, tóm tắt version hợp đồng, đọc hoá đơn, Hỏi ICO với công cụ chỉ đọc): `ai_advanced.py`, `ask_ico.py`, transport `shared/ai_transport.py` (`complete_json_pdf`, `run_tools`), UI `frontend/js/views/ai_assist.js`. Kho báo cáo PDF: `report_archive.py` (middleware theo header `X-ICO-Archive`), `routers/report_archive.py`. Mọi tác vụ AI mới phải đi qua `ai_platform.run()`.
  - Che tên `Masker`: thêm `<email:…>`, `<person:…>` (trường tên người `PERSON_KEYS`), IPv6, tên có dấu cách / ngoặc (khớp nguyên chuỗi); `_source_names` gồm datastore / storage / OME / IPAM. MCP `observability.mask_infra`: HMAC theo khoá máy chủ + tên trong kho.
  - Giữ chỗ ngân sách trong DB: `spend_ai_config.reserved_micros` qua `_reserve` / `_release` (session riêng), hết hạn sau
    `RESERVE_STALE`.
  - `ai_runs` có `request_id` / `prompt_version` (`PROMPT_VERSIONS` theo tác vụ) / `feedback`.
  - Hỏi ICO có thêm công cụ `phan_cung` / `he_thong` / `trang_thai_nguon` / `tra_ip`; `SOURCES` = `inbox.SOURCE_LABELS`.
  - 👍 / 👎 qua `POST /api/ai/ask/{runId}/feedback`, chỉ người hỏi. |
| Giao diện | `frontend/index.html`, `css/*.css` (7 file nạp theo thứ tự trong `index.html`: `tokens.css` token web “Phòng máy”, `base`, `components`, `pages`, `modules`, `shell`, `marks`; CSS trang / module mới thêm vào cuối `modules.css`, luật phải đè lên `shell` / `marks` thì thêm vào cuối `marks.css` — không tạo file vá riêng), `js/core.js` (chỉ mô hình capacity + trạng thái + nạp dữ liệu; nhập CSV / JSON ở `js/import.js`), `js/scope_filter.js` + `js/settings_page.js` (phạm vi báo cáo, tiêu chí / ngưỡng / logo), `js/routes.js` (**bảng route duy nhất**: `PAGES`, `MENU` 2 miền + tab, `VIEWS` = section / render / tab con — trang mới thêm ở đây), `js/app.js` (điều hướng `#/<mục>/<tab>`), `js/ui.js` (pill, trạng thái, hộp thoại, `uiEmpty` / `uiNote`, `chartPalette`, bảng sắp xếp, `uiPref` lưu tuỳ chọn trên server), `js/views/*.js` (`capacity.js`, `whatif.js`…; trang lớn tách theo phần: `sizing*.js`, `licensing*.js`, `backup*.js`, `spend_admin.js`; `payments.js` = Đợt thanh toán, `planning.js` = Quy hoạch tài nguyên, `archive.js` = Kho báo cáo; Chi phí và BOM nạp khi mở trang qua `uiRequire` — gọi hàm của chúng từ module khác phải `uiRequire('spend').then(...)`), `vendor/` (Chart.js, lucide ghim phiên bản, không dùng CDN); thiết kế `docs/design/super-app.md` |

## Quy tắc bắt buộc

1. **vCenter, Proxmox VE, Nutanix, NetBackup, storage (Pure / NetApp), Dell OME, GestióIP và ServiceDesk Plus chỉ được ĐỌC.** Không bao giờ gọi API làm thay đổi hạ tầng
   (power, reconfigure, snapshot, xoá, POST/PUT/DELETE tới NetBackup / Pure ngoài `/login` `/logout`; ONTAP chỉ GET —
   `storage/http.py::JsonClient` chặn sẵn; OME chỉ GET, ngoại lệ duy nhất là phiên `POST /api/SessionService/Sessions` và
   `DELETE /api/SessionService/Sessions('<id>')` — `hardware/ome.py::allow`, cấm acknowledge alert / job / firmware / power;
   GestióIP nhận cả lệnh ghi qua GET nên chặn theo `request_type` — chỉ các loại đọc trong `ipam/gestioip.py::READ_REQUEST_TYPES`;
   event vCenter (G6) chỉ đọc bằng `eventManager.QueryEvents` trong `vcenter/events.py`, không tạo EventHistoryCollector, không post event;
   ServiceDesk Plus (G7a) chỉ GET — `itsm/sdp.py` truyền `allow` từ chối mọi phương thức khác cho `JsonClient`, không tạo / sửa ticket).
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
   Index (`Index(...)`, `Column(index=True)`) và CHECK mới trên bảng đã có: chỉ cần khai báo trên model —
   `ensure_schema()` tự tạo index còn thiếu (`CONCURRENTLY` trên PostgreSQL) và thêm CHECK `NOT VALID` rồi `VALIDATE`.
   Các bước migration chạy dưới advisory lock nên hai pod khởi động cùng lúc không đụng nhau.
5. **Giao diện và báo cáo viết tiếng Việt**, có dấu đầy đủ, văn phong ngắn, nêu thẳng vấn đề →
   quyết định. Code, tên biến, comment code viết tiếng Anh. Field dữ liệu dùng camelCase giống
   frontend (`ramGB`, `isPoweredOn`, `vcpu`...).
6. **Design**: nguồn sự thật là `design-system/ico/MASTER.md` (+ `pages/*.md` ghi đè theo trang).
   **App web** dùng bảng màu và font riêng (“Phòng máy”, Be Vietnam Pro + JetBrains Mono tự host ở `frontend/vendor/`)
   qua token trong `frontend/css/tokens.css`. **Báo cáo PDF / Excel** giữ HSC Design System (`COLORS` trong
   `report_pdf.py`, `excel_style.py`, font Inter qua `fonts-inter`). Không hard-code màu mới, chữ phải đạt WCAG AA.
   Trạng thái luôn là 4 mức AN TOÀN / THEO DÕI / CẢNH BÁO / VƯỢT NGƯỠNG, hiển thị bằng glyph + nhãn (`uiPill`,
   `status_pill`); nhãn trung tính dùng `uiTag`. Số hiển thị kiểu vi-VN (`1.234,5`) qua helper (`fmtNum`, `uiNumber`,
   `vn_num`) — MASTER §4. Web: đọc MASTER; PDF / Excel: skill `hsc-design-system` và `pdf-report`.
   Quyết định design mới (màu, font, mật độ, thành phần) phải tra `ui-ux-pro-max` trước — xem mục Skill bên dưới.
   Form / nhãn / nút / bố cục web theo MASTER §6 “Form, nhãn và độ dài chữ” (skill `web-layout`): ô cùng hàng thẳng hàng,
   nhãn một dòng (tên + đơn vị, không “(tuỳ chọn)”), không cắt chữ thiết yếu, không cuộn ngang ở 390 px, chọn file qua
   `uiDropzone`. Sửa giao diện thì chạy `ui_audit.js` của skill đó ở 1440 + 390 px.
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
9. **VM luôn kèm IP** (quyết định của chủ sản phẩm). Mọi chỗ liệt kê VM — trang web, hộp thoại, thẻ, email, PDF, Excel, CSV —
   phải có cột (hoặc dòng) IP cạnh tên VM, vì tên VM trên VMware thường khác hostname mà system owner quản lý.
   - Backend: `vm_ip.py` (`attach(db, rows, vm_key=, vc_key=)` gắn `ip` theo vCenter + tên VM từ lần đồng bộ mới nhất, cache 60 s;
     FQDN client backup → tên ngắn).
   - Dòng lịch sử giữ `ipAddress` của snapshot.
   - Finding VM có `ip` ở cả `capacity.py` và `core.js` (không đổi mã đối soát).
   - Lọc theo tên VM nên khớp cả IP.
10. **Báo cáo gom về Tài liệu → Báo cáo** (quyết định của chủ sản phẩm). Tính năng mới có xuất báo cáo (PDF / Excel / CSV) thì
   ngoài nút ở trang của tính năng, báo cáo phải có mặt ở **Trung tâm báo cáo**, trong cùng thay đổi:
   - Khai file xuất trong `backend/app/exports.py` gồm `group`, `rx`, `feature` / `role` và `archive` (PDF thường `True`, để vào
     Kho báo cáo). Trường `center` ghi chỗ đặt trên trang:
     - `"<key>"`: nút `data-report`;
     - `"spend:<fmt>"`: nút `data-spend-export`;
     - `"record:<page>"`: báo cáo của một hồ sơ, đặt lối tắt `data-nav-link` trên thẻ “Báo cáo theo hồ sơ”.
   - Thêm thẻ (tiêu đề, mô tả một câu, tuỳ chọn trong `<details class="rpt-opts">` nếu có, nút PDF / Excel, nút *Mở trang*) vào
     đúng nhóm của `frontend/index.html` `#viewReports`. Nhóm mới thì thêm chip `data-rpt-group` và ẩn theo module ở
     `app.js::applyFeatures`.
   - Thêm action vào `RPT_ACTIONS` (`frontend/js/views/reports.js`), dùng chung hàm / endpoint với trang của tính năng để số liệu
     giống nhau ở mọi nơi xuất. Phạm vi lấy từ thanh lọc và khung bên phải; bộ lọc Owner do `uiDownload` gắn.
   - Cập nhật bảng nhóm ở `docs/huong-dan-chi-tiet.md` (mục *Trung tâm báo cáo*).
   - `backend/tests/test_report_center.py` chặn file xuất chưa có chỗ trên trang và nút không có action / không khai.
11. **Test**: thêm hoặc sửa logic tính toán (`capacity`, `planning`, `sizing`, `licensing`,
   `backup/analysis`, `spend/budget_plan`) thì phải có test trong `backend/tests/` với số liệu cụ thể. Chạy
   `python -m pytest tests -q` trước khi kết thúc.
12. **Code gọn, không chép lại** (quyết định của chủ sản phẩm). Viết xong tính năng, trước khi tạo PR thì chạy `/simplify`
   cho thay đổi đang chờ và sửa luôn các điểm nó tìm ra (tái sử dụng · đơn giản hoá · hiệu năng · sửa đúng tầng), hành vi giữ nguyên.
   - **Dùng helper có sẵn, không viết bản riêng:**
     - tải file về: `downloads.attachment()` / `stamp()`;
     - mức trạng thái: `shared/levels.py` (`LEVEL_RANK`, `LEVEL_TEXT`, `worst`);
     - PDF: `pdf_kit` (`vn`, `data_table`, `level_pill`, `short_date`);
     - Excel: `excel_style` (`_sheet_title`, `_write_rows`, `_widths`, `to_bytes`);
     - IP của VM: `vm_ip.attach()` (trả lại danh sách dòng; `vc_key=None` cho client backup);
     - khoá chuẩn: `shared/identity.py`;
     - giờ: `timeutil` (`to_iso_utc`, `to_vn_str`);
     - các helper khác trong `shared/`.
     Thấy cùng một đoạn ở hai nơi thì đưa về helper dùng chung trong cùng thay đổi.
   - **Không tính thừa:**
     - không truy vấn trong vòng lặp (N+1);
     - không gọi lại cùng một hàm tổng hợp hai lần trong một request: tính một lần rồi truyền xuống (ví dụ `infra.summary_from`);
     - trang chi tiết đọc đúng một đối tượng, không dựng cả danh sách rồi lọc;
     - lọc trong SQL thay vì tải hết rồi lọc bằng Python;
     - cache có khoá dựng một lần cho nhiều luồng.
   - **Không để code chết:** xoá hàm / endpoint / import / CSS không còn dùng, comment cũ, alias “tên cũ” không còn ai gọi.
     Kiểm import thừa bằng `python -m pyflakes <file đã sửa>`.
   - **Không tạo file vá riêng:**
     - CSS trang / module thêm vào cuối `modules.css`; luật phải đè lên `shell.css` / `marks.css` thì thêm vào cuối `marks.css`;
     - logic mới đặt vào đúng module, không thêm file “patch” / “fix” rời rạc.
   - Sửa đúng gốc ở tầng dùng chung thay vì thêm ngoại lệ ở từng nơi gọi.
   - Gỡ / chuyển CSS thì chụp ảnh trước / sau ở 1440 + 390 px để chắc giao diện không đổi.
   - Điểm `/simplify` nêu ra mà chưa sửa (thay đổi lớn, đổi hành vi) thì ghi lại trong mô tả PR.

## Lộ trình ICO v2

Thiết kế đã chốt: `docs/design/ico-v2-plan.md` (sổ CI + khoá chuẩn, hệ thống & owner, rà soát 6 tháng, Dell OME, kho sự kiện,
ServiceDesk Plus, kế hoạch năm sau). Mỗi giai đoạn là **một PR**, làm tuần tự theo `docs/design/ico-v2-prompts.md`
(dán *Phần chung* ở đầu mỗi prompt). Ngoại lệ chỉ đọc mới (OME session, vSphere Automation session) chỉ hợp lệ khi đúng mục 5
của kế hoạch và được thêm vào quy tắc 1 trong cùng PR. Đánh dấu tiến độ bằng cách ghi số PR vào bảng lộ trình (mục 6).
Hợp nhất dữ liệu theo serial / service tag và IP (Hồ sơ thiết bị, Đối soát, Tra IP — bước C1–C6): `docs/design/ico-correlation.md`.

## Skill của dự án (`.claude/skills/`)

- `vmware-collector` — đọc vCenter bằng pyVmomi, demo generator, scheduler.
- `netbackup-api` — NetBackup REST chỉ đọc, parser, mã lỗi, RPO.
- `capacity-math` — công thức overcommit, HT, ngưỡng, N+1, dự phóng, mã đối soát.
- `web-layout` — bố cục web: căn hàng ô nhập, độ dài nhãn / tiêu đề / tuỳ chọn select, khổ 390 px, `uiDropzone`;
  script `scripts/run_demo.sh` (demo) + `scripts/ui_audit.js` (Playwright, đo lệch hàng / cắt chữ / cuộn ngang).
- `hsc-design-system` — chỉ cho báo cáo PDF / Excel (HSC, Inter, số vi-VN). Giao diện web: `design-system/ico/MASTER.md`.
- `pdf-report` — quy ước báo cáo PDF A4 bằng WeasyPrint.
- `spend-module` — module Chi phí & Hợp đồng: tiền VND số nguyên, năm tài chính, phân quyền 4 mức, trạng thái suy ra,
  nhắc hạn idempotent, upload an toàn, khung danh sách → chi tiết (`spPaneOrDialog`).
- `hunt-*` (35 skill) — tập con **phòng thủ** của [elementalsouls/Claude-BugHunter](https://github.com/elementalsouls/Claude-BugHunter) (MIT + CC-BY-4.0, pin trong `skills-lock.json`; nguồn / commit ở `.claude/skills/BUGHUNTER-LICENSE.md`). Dùng để tự rà lỗ hổng trên code ICO khi chạy `/security-review` (IDOR/BOLA, XSS/DOM, SQLi, CSRF, SSRF/LFI/XXE, RCE/SSTI/deserialization, upload, auth/session/JWT/OAuth/MFA, host-header, CORS, race-condition, business-logic, LLM/MCP…). **Không** cài phần OSINT / red-team / tấn công bên thứ ba của bộ gốc. Chỉ dùng cho tài sản mình sở hữu hoặc được uỷ quyền.
- `mcp-builder` — hướng dẫn làm MCP server chất lượng (đặt tên tool, annotation, `outputSchema`, phân trang, lỗi có
  hướng dẫn, bộ đánh giá 10 câu hỏi) của `anthropics/skills` (Apache-2.0, pin trong `skills-lock.json`). Dùng khi sửa
  `mcp_server.py` / thêm tool MCP; script `scripts/evaluation.py` chạy bộ đánh giá (cần API key, chạy tay).
- `find-skills` — tìm skill trong hệ sinh thái `npx skills` (`vercel-labs/skills`, MIT, pin trong `skills-lock.json`). **Quy tắc cài skill mới của dự án** (thay Step 6 của skill): không dùng `-g` / `-y`; tải về thư mục tạm, đọc toàn bộ nội dung (script, hook, `allowed-tools`) trước, chỉ cài vào `.claude/skills/` sau khi chủ sản phẩm đồng ý, ghi nguồn + hash vào `skills-lock.json`, kèm giấy phép.
- `ui-ux-pro-max` — tra cứu style / bảng màu / font / UX guideline (bên thứ ba, `nextlevelbuilder/ui-ux-pro-max-skill`,
  cài bằng `npx skills add`; mã nằm ở `.agents/skills/`, `.claude/skills/ui-ux-pro-max` là symlink, phiên bản ghim trong
  `skills-lock.json`). Cách tra:
  ```bash
  python3 .claude/skills/ui-ux-pro-max/scripts/search.py "<2–5 từ khoá, một ý>" --domain ux|color|style|typography|google-fonts|chart|product
  ```
  - Một truy vấn một ý; không đưa dữ liệu thật (tên host, số tiền) vào truy vấn; không dùng `--stack`.
  - **Kiểm độ phù hợp**: loại kết quả landing / marketing / thương mại điện tử. Không khớp → thử lại một lần với từ khoá
    khác; vẫn không có → ghi “không có kết quả phù hợp trong dữ liệu” và dùng nguồn khác (quyết định đã duyệt, WCAG,
    `modern-web-guidance`).
  - **Font tiếng Việt**: font mới phải có subset `vietnamese` (`--domain google-fonts`) và tự host ở `frontend/vendor/`.
  - **Không** chạy `--persist --force` lên `design-system/ico/MASTER.md`; kết quả chỉ là đề xuất, MASTER đổi sau khi chủ
    sản phẩm duyệt và mỗi quy tắc ghi **Nguồn**. Cách làm đầy đủ: `docs/design/redesign/README.md` mục 1.

Quét bảo mật: chạy `/security-review` (thay đổi đang chờ) hoặc `/claude-security`
(quét sâu cả repo) trong Claude Code trước khi tạo PR.
