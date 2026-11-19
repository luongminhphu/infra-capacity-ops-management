# Thiết kế: ICO v2 — sổ CI, chủ sở hữu hệ thống, Dell OME, sự kiện và kế hoạch theo nhu cầu ITSM

Trạng thái: **đã chốt hướng (19/11/2026)**, triển khai theo lộ trình mục 6. Prompt triển khai từng PR: [`ico-v2-prompts.md`](ico-v2-prompts.md).

## 1. Mục tiêu

Mỗi đối tượng hạ tầng có **một định danh**; mọi nguồn ghi vào cùng một sổ, mọi sự kiện gắn vào đối tượng đó.
ICO khép được vòng nghiệp vụ **Cầu → Cung → Sử dụng → Thu hồi → Kế hoạch**:

```
 ServiceDesk Plus (yêu cầu VM / tăng tài nguyên)   vCenter · Nutanix · Proxmox · Dell OME · Pure · NetApp · NBU   Spend
                       └───────────────────────────────────┬──────────────────────────────────────────────────┘
                                                           ▼
             ① SỔ CI + KHOÁ CHUẨN  ──  ② HỆ THỐNG & CHỦ SỞ HỮU  ──  ③ KHO SỰ KIỆN / SỰ CỐ
                                                           ▼
   CẦU (yêu cầu theo dự án) → CUNG (đã cấp) → SỬ DỤNG (dùng thật) → THU HỒI (owner xác nhận) → KẾ HOẠCH NĂM SAU → Capex
```

## 2. Nguyên tắc (giữ nguyên CLAUDE.md)

- Nguồn hạ tầng **chỉ đọc**. Ngoại lệ đăng nhập / đăng xuất session được liệt kê đích danh (mục 5).
- Nguồn mới nào cũng có `demo_generator`; cột mới qua `ensure_schema`; job nền chỉ ở pod leader.
- Liên kết có độ tin cậy: `strong` (serial, instanceUuid, NAA, mã Opex) và `weak` (tên, IP). Chỉ `strong` được dùng để
  gom sự cố; `weak` hiển thị rõ là "ghép theo tên".
- Mỗi giai đoạn: branch + PR, test có số liệu cụ thể, `/security-review`, CI xanh.

## 3. Quyết định đã chốt (19/11/2026)

| # | Nội dung | Quyết định |
|---|---|---|
| D1 | ITSM | ServiceDesk Plus **On-Premises**, API v3, xác thực **technician key** (header `authtoken`), chỉ GET |
| D2 | Dữ liệu yêu cầu VM | Template riêng; **bảng cấu trúc nằm trong body (description HTML)**. Lịch sử từ **cuối 2025** → nạp lùi từ 01/10/2025 (cấu hình được) |
| D3 | System owner | Chưa có hệ thống chuẩn; mọi owner có tài khoản **Entra**. Owner = **Team Lead đại diện nhóm dự án / phòng ban** |
| D4 | Rà soát | Chu kỳ **6 tháng**; leo thang tới **quản lý trực tiếp của owner** |
| D5 | Dell OME | **4.x, 1 instance**, ra Internet qua proxy, **lấy được bảo hành Dell**; chỉ có máy chủ Dell |
| D6 | Thứ tự | Theo lộ trình chuẩn (mục 6) |

## 4. Mô hình dữ liệu mới

```
-- ① Sổ CI (backend/app/cmdb/)
ci              (id, type, name, source_kind, source_key, status[active|retired], first_seen, last_seen, attrs JSON)
                UNIQUE(type, source_kind, source_key)
ci_key          (ci_id, kind[serial|hw_uuid|instance_uuid|fqdn|short_name|ip|mac|naa|nbu_client|asset_tag|opex_code|idrac_ip],
                 value_norm)  UNIQUE(kind, value_norm, ci_id)
ci_link         (from_ci, to_ci, relation, source, confidence[strong|weak], first_seen, last_seen)
                UNIQUE(from_ci, to_ci, relation)

-- ② Hệ thống & owner (backend/app/owners/)
system          (id, code, name, project, department, owner_email, owner_name, deputy_email, manager_email,
                 cost_center, criticality[1..4], env, status, note)
system_member   (ci_id UNIQUE, system_id, source[manual|attribute|tag|rule|itsm], confidence, assigned_by, assigned_at)
owner_rule      (id, priority, match_kind[folder|resource_pool|attribute|tag|name_regex|cluster], pattern, system_id, enabled)
review_campaign (id, period_label, opened_at, due_at, closed_at, status[draft|open|closed], dry_run)
review_item     (id, campaign_id, ci_id, system_id, owner_email, signals JSON, decision[keep|decommission|resize|transfer|not_mine],
                 decide_by_date, decided_by, decided_at, note, escalated_at, verified_at)
review_notice   (campaign_id, recipient, kind[open|reminder1|reminder2|escalation|closing], sent_at, error)
                UNIQUE(campaign_id, recipient, kind)

-- Dell OME (backend/app/hardware/)
hw_device_state        (source_key, device_id, service_tag, name, model, idrac_ip, power_state, status, connection, last_inventory_at, captured_at)
hw_component_health    (source_key, device_id, subsystem, status, detail, captured_at)
hw_disk_state          (source_key, device_id, slot, model, media, size_gb, status, predictive_failure, captured_at)
hw_firmware_state      (source_key, device_id, component, version, baseline_version, compliance, captured_at)
hw_warranty            (source_key, service_tag, service_level, start_date, end_date, captured_at)
hw_alert               (source_key, alert_id UNIQUE per source, device_id, service_tag, severity, status, category, message_id, message, at)

-- ③ Sự kiện (backend/app/events/)
ops_event       (id, at, source, ci_id, event_key, level, transition[open|worse|better|clear], title, detail, ref)
ops_event_open  (source, event_key PK, ci_id, level, opened_at, last_seen)            -- trạng thái đang mở để so chuyển trạng thái
ops_incident    (id, ci_id, root_ci_id, status[open|acked|in_progress|resolved], peak_level, opened_at, acked_at, acked_by,
                 resolved_at, resolved_by, assignee, event_count, flapping, ticket_ref)

-- ITSM (backend/app/itsm/)
itsm_request       (source_key, req_id, template, subject, kind[new_vm|resize|decommission|other], requester_email, department,
                    project, system_id, status, created_at, approved_at, resolved_at, updated_at, parse_status[parsed|ai|review|failed],
                    confidence, reviewed_by, reviewed_at)    UNIQUE(source_key, req_id)
itsm_request_line  (request_id, line_no, vm_name, env, os, vcpu, ram_gb, disk_gb, qty, raw JSON)
itsm_request_ci    (request_id, ci_id, match[strong|weak], matched_by)
itsm_label_map     (label_norm, field)                                                  -- nhãn trong bảng body → trường chuẩn
```

## 5. Nguồn dữ liệu mới và ngoại lệ chỉ đọc

| Nguồn | Gọi | Ngoại lệ không phải GET |
|---|---|---|
| vCenter (bổ sung) | `summary.hardware.otherIdentifyingInfo` (ServiceTag), `summary.hardware.uuid`, VM `parent` / `resourcePool` / `customValue`, Folder `name` / `parent`, `eventManager.QueryEvents` (GĐ6) | vSphere Tags (tuỳ chọn, GĐ2d): `POST /api/session`, `DELETE /api/session` |
| Dell OME 4.x | `DeviceService/Devices`, `Devices(id)/SubSystemHealth`, `Devices(id)/InventoryDetails('…')`, `AlertService/Alerts`, `WarrantyService/Warranties`, `UpdateService/Baselines…/DeviceComplianceReports`, `GroupService/Groups` | `POST /api/SessionService/Sessions`, `DELETE /api/SessionService/Sessions('<id>')`. **Cấm** mọi action (`AlertService.Acknowledge`, job, firmware update, power) |
| ServiceDesk Plus On-Prem v3 | `GET /api/v3/requests` (`input_data` list_info), `GET /api/v3/requests/{id}` | Không có. Ghi ticket (GĐ10) cần quyết định riêng |
| Entra / Graph (tuỳ chọn) | Quản lý trực tiếp của owner (`/users/{id}/manager`) | — (mặc định nhập tay `manager_email`) |

## 6. Lộ trình

| Đợt | GĐ | Nội dung | Phụ thuộc | Nghiệm thu |
|---|---|---|---|---|
| A | 1 | Khoá chuẩn + serial host + sổ CI + thẻ đối tượng / tác động (G1a: #108; G1b: đang làm) | — | Host nối DC asset / Opex qua serial; datastore → array → VM → backup client |
| A | 2 | Hệ thống & owner: gán 4 cách, lọc khắp app, phạm vi truy cập Owner (mặc định chặn) | 1 | Lọc mọi trang theo owner; test IDOR đạt |
| A | 3 | Rà soát owner 6 tháng: email, xác nhận, nhắc, leo thang, đóng kỳ, thu hồi, đối chiếu kỳ sau | 2 | Một kỳ demo chạy trọn, không gửi trùng |
| B | 4 | Dell OME: sức khoẻ linh kiện, ổ dự báo hỏng, firmware, bảo hành ↔ Opex, trang Phần cứng | 1 | Máy bảo hành OME ≠ Opex được liệt kê |
| B | 5 | Kho sự kiện + vòng đời sự cố, MTTA / MTTR, flapping, timeline CI | 1, 4 | Thống kê sự cố theo tháng |
| B | 6 | Event vCenter + nhật ký thay đổi + gom sự cố theo đồ thị CI | 5 | PSU lỗi + alarm + HA restart = 1 sự cố |
| C | 7 | ITSM SDP: nạp lùi, đọc bảng trong body, AI dự phòng + hàng chờ duyệt, nối yêu cầu ↔ VM / hệ thống | 1, 2 | ≥ 90 % yêu cầu VM có tài nguyên đã duyệt |
| C | 8 | Tổng kết năm (xin → cấp → dùng) + mô hình kế hoạch năm sau → Kế hoạch ngân sách / Capex | 3, 7 | Ra số host cần mua kèm nguồn số |
| + | 9 | Sức khoẻ sâu: lệch cấu hình, EoGS / VMSA / chứng chỉ TLS, contention `QueryPerf`, VCSA; DR theo criticality; showback | 2, 5 | — |
| + | 10 | Điểm sức khoẻ, báo cáo tháng, Teams, ticket SDP (ghi), bằng chứng ISO 27001 | 3, 5 | — |

## 7. Phạm vi truy cập Owner (bảo mật)

- Thêm `users.access_scope` = `full` | `owner`. Đăng nhập Entra với app role `ICO.Owner` (và không có role đầy đủ) → `owner`.
- **Mặc định chặn:** `get_current_user` từ chối user `owner` (403) — mọi endpoint hiện có tự động an toàn.
  Cổng owner dùng dependency riêng `get_owner_user`, chỉ trả dữ liệu của hệ thống mà email user là owner / deputy.
- Test IDOR: owner A không đọc / quyết định được VM của owner B, kể cả đoán id.

## 8. Rủi ro

| Rủi ro | Xử lý |
|---|---|
| Ghép sai CI (tên trùng, IP tái dùng) | Liên kết `weak` không gom sự cố; trang "liên kết cần xem" |
| Body SDP không đồng nhất | Parser HTML + bảng ánh xạ nhãn; AI chỉ là dự phòng; số liệu chỉ vào báo cáo sau khi duyệt |
| Email gửi nhầm / gửi trùng | `dry_run` mặc định cho kỳ đầu, `review_notice` UNIQUE, xem trước nội dung |
| Sổ CI chậm khi dữ liệu lớn | Diff theo tập (set-based), bulk upsert, chạy sau vòng sync |
| Owner thấy dữ liệu ngoài phạm vi | Mặc định chặn (mục 7) + test |
