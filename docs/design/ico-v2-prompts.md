# ICO v2 — bộ prompt triển khai cho Claude Code

Dùng cùng `docs/design/ico-v2-plan.md`. Mỗi prompt = **một PR**. Chạy tuần tự; mở phiên Claude Code mới cho mỗi PR.
Dán **Phần chung** ở đầu mọi prompt (hoặc nói "áp dụng Phần chung trong docs/design/ico-v2-prompts.md").

Mức chi tiết: Đợt A (GĐ1–3) viết đầy đủ. Đợt B, C viết đủ để làm, sẽ tinh chỉnh khi tới lượt (phụ thuộc code thực tế của
các PR trước). GĐ9–10 ở dạng khung.

---

## Phần chung (dán ở đầu mọi prompt)

```text
Bối cảnh: repo ICO. Đọc CLAUDE.md và docs/design/ico-v2-plan.md trước khi làm. Đây là MỘT PR trong lộ trình ICO v2.

Quy tắc bắt buộc cho PR này:
1. Nguồn hạ tầng chỉ ĐỌC. Ngoại lệ không phải GET chỉ đúng những gì ico-v2-plan.md mục 5 liệt kê.
2. Trường mới ở client thật → thêm vào demo_generator tương ứng; demo + test không được vỡ.
3. Schema: bảng mới khai báo trong models.py; cột mới trên bảng cũ → dict `additive` trong database.py::ensure_schema()
   (nullable / có DEFAULT, chạy cả SQLite và PostgreSQL). Không đổi tên / xoá cột. Cột JSON/CSV kiểu Text đăng ký stored_text.
4. Job nền chỉ chạy ở pod leader (leader.py). Ghi hàng loạt bằng bulk; không N+1.
5. Endpoint mới chọn rõ get_current_user / require_admin / require_feature; escape mọi chuỗi ngoài khi vào HTML / PDF / email.
6. UI tiếng Việt có dấu, theo design-system/ico/MASTER.md, 4 mức trạng thái qua uiPill, số vi-VN qua helper.
   Sửa UI thì chạy skill web-layout (ui_audit.js ở 1440 + 390 px).
7. Test trong backend/tests/ với số liệu cụ thể cho mọi logic mới. Chạy `cd backend && python -m pytest tests -q` → xanh.
8. Cập nhật docs/huong-dan-chi-tiet.md (mục mới) và bảng "Bản đồ code" trong CLAUDE.md nếu thêm module.
9. Trước khi tạo PR: /security-review. Branch tên `claude/ico-v2-<mã PR>`. Mô tả PR: mục tiêu, thay đổi schema,
   ngoại lệ chỉ đọc (nếu có), cách kiểm tra bằng DEMO, ảnh chụp UI (nếu có).

Cách làm: trước khi code, đọc các file liên quan và trình bày kế hoạch ngắn (file sửa, schema, điểm móc vào scheduler / router),
nêu chỗ chưa rõ. Sau đó làm, chạy test, tự rà soát diff theo tiêu chí nghiệm thu ở cuối prompt.
```

---

# ĐỢT A — Nền + Owner

## G1a — Khoá chuẩn + serial host

```text
Mục tiêu: có bộ chuẩn hoá khoá dùng chung và thu serial phần cứng của host, làm nền cho sổ CI.

1. Tạo backend/app/shared/identity.py (thuần hàm, không import module nghiệp vụ):
   - norm_serial(s): upper-case, chỉ chữ + số. Phải cho cùng kết quả với dc_assets.serial_key (chuyển dc_assets dùng hàm này,
     giữ tên cũ làm alias để không vỡ chỗ gọi).
   - norm_fqdn (lower, bỏ dấu chấm cuối), short_name, norm_mac (aa:bb:cc:dd:ee:ff), norm_naa (lower, bỏ tiền tố "naa."),
     norm_ip (ipaddress, trả None nếu không hợp lệ), nbu_client_key (dùng lại logic backup.client.client_key — chuyển lõi sang
     identity, client_key gọi lại).
   - Test bảng: ít nhất 6 ca mỗi hàm (khoảng trắng, chữ thường, FQDN có dấu chấm cuối, MAC dạng 'AA-BB-…', NAA có tiền tố, IP sai).
2. vCenter (skill vmware-collector): HOST_PROPS đã có "summary" → đọc summary.hardware.otherIdentifyingInfo, lấy giá trị có
   identifierType.key = "ServiceTag" (ưu tiên), rồi "SerialNumberTag", "EnclosureSerialNumberTag"; và summary.hardware.uuid.
   Thêm cột host_snapshots.serial VARCHAR(64), host_snapshots.hw_uuid VARCHAR(64) (additive), ghi trong _host_inventory_cols.
   Không thêm round-trip SOAP mới.
3. Nutanix: nếu API v2.0 trả serial của host thì map vào cùng trường; Proxmox để None. Ghi rõ trong code.
4. demo_generator: host demo có service tag 7 ký tự kiểu Dell, ổn định theo seed; một phần trùng serial với dữ liệu DC assets demo
   / Opex demo (nếu có seed) để GĐ1b có ca ghép.
5. data_access: dòng host trả thêm `serial`, `hwUuid` (camelCase). Thẻ Host hiện "Service Tag".

Nghiệm thu: test identity xanh; sync DEMO ghi serial cho mọi host VMware demo; host_snapshots cũ (serial NULL) không lỗi.
```

## G1b — Sổ CI và bộ dựng

```text
Mục tiêu: sổ định danh CI (bảng ci, ci_key, ci_link theo ico-v2-plan.md mục 4) được dựng lại sau mỗi vòng sync.
Đọc skill backend-design:data-modeling-discipline trước khi viết model.

1. Gói backend/app/cmdb/: models trong models.py (hoặc cmdb/models.py import vào Base giống spend), registry.py
   (upsert_ci, set_keys, upsert_link, retire_unseen), builder.py (build_all(db)), impact.py (GĐ1c).
2. Loại CI và nguồn:
   - vm: source_key = instanceUuid (fallback "<vcenterKey>:<name>"); keys instance_uuid, short_name, fqdn (guestHostname), ip.
   - hypervisor_host: "<vcenterKey>:<host>"; keys fqdn, short_name, serial, hw_uuid.
   - cluster: "<vcenterKey>:<cluster>".
   - physical_server: source_key = norm_serial(serial); tạo khi có serial từ host / dc_assets (server) / opex_assets.
   - datastore: "<vcenterKey>:<ds_key>"; keys naa (từ backing).
   - storage_array, storage_volume: theo storage_array_states / storage_volume_states; volume keys naa.
   - backup_client: theo client NBU; key nbu_client.
   - dc_asset: theo dc_assets.key; keys serial, asset_tag. opex_item: theo mã Opex; key opex_code.
3. Quan hệ (relation, confidence):
   vm hosted_on hypervisor_host (strong); hypervisor_host member_of cluster (strong); hypervisor_host runs_on physical_server
   (strong, cùng serial); dc_asset records physical_server (strong, serial); opex_item covers physical_server (strong, serial
   từ opex_assets) — nếu opex_assets chỉ có host_name thì covers hypervisor_host (weak); vm stored_on datastore (strong);
   datastore backed_by storage_volume (strong nếu NAA, weak nếu NFS path); storage_volume on storage_array (strong);
   vm protected_by backup_client (weak — ghép tên / IP; ghi matched_by trong attrs).
4. Dựng: chạy ở leader sau sync_everything trong vòng scheduler (một transaction ngắn mỗi loại; set-based: đọc tập hiện có,
   tính diff, bulk insert / update). Idempotent: chạy 2 lần liên tiếp không đổi dữ liệu. CI không còn thấy > 7 ngày (cấu hình
   CMDB_RETIRE_DAYS) → status retired, không xoá. Ghi thời gian chạy vào metrics.
5. Endpoint (get_current_user, lọc theo features như services/entities.py):
   GET /api/ci?type=&q=&limit=, GET /api/ci/{id} (keys + links hai chiều), GET /api/ci/stats (số CI theo loại, tỷ lệ host có
   serial, số liên kết weak).
6. Test: dữ liệu mẫu nhỏ cố định (2 host cùng cluster, 1 có serial trùng dc_asset + opex_asset, 3 VM, 1 datastore NAA ghép 1
   volume Pure) → kiểm số CI, số link, confidence; chạy builder 2 lần → không đổi; xoá 1 VM khỏi snapshot + tua thời gian
   8 ngày → retired.

Nghiệm thu: DEMO đầy đủ dựng sổ < 5 giây cho ~3.000 VM demo (đo trong test hiệu năng có sẵn kiểu test_perf_r2); test xanh.
```

## G1c — Thẻ đối tượng + tác động đọc từ sổ CI

```text
Mục tiêu: thẻ Host / VM, tìm kiếm Ctrl + K và Hỏi ICO dùng sổ CI; thêm thẻ Máy chủ vật lý, Datastore và phân tích tác động.

1. cmdb/impact.py: downstream(ci_id, depth=4) và upstream(ci_id, depth=4) theo các relation ở G1b (hướng: physical_server →
   hypervisor_host → vm → backup_client; storage_array → storage_volume → datastore → vm). Một truy vấn mỗi tầng (không N+1),
   chống vòng lặp, giới hạn 5.000 nút. Trả số lượng theo loại + danh sách rút gọn.
2. services/entities.py: _devices_for_host dùng link covers / records qua physical_server (strong); chỉ fallback so tên khi
   không có serial và gắn nhãn "ghép theo tên". Backup của VM lấy qua link protected_by.
3. Endpoint GET /api/ci/{id}/impact?direction=down|up, GET /api/entities/server/{serial}, GET /api/entities/datastore/{id}.
4. Frontend (views/entity.js): thẻ thêm mục "Liên kết" (nhóm theo quan hệ, liên kết weak có uiTag "ghép theo tên") và
   "Tác động" ("Nếu đối tượng này lỗi: N host, M VM, K client backup"). Thẻ Máy chủ vật lý: serial, model, DC asset, Opex bảo hành,
   host ESXi chạy trên nó. Ctrl + K tìm được theo serial và asset tag.
5. ask_ico.py: thêm công cụ chỉ đọc ci_lookup / ci_impact (đi qua ai_platform, che tên như hiện tại).
6. Test: impact trên dữ liệu G1b (array → 1 volume → 1 datastore → 3 VM); test endpoint 404 / phân quyền feature.

Nghiệm thu: từ một datastore demo mở được array, VM và client backup; từ một host thấy bảo hành qua serial; ui_audit đạt.
```

## G2a — Mô hình Hệ thống & Owner + gán thủ công / Excel

```text
Mục tiêu: đối tượng Hệ thống (system) và gán VM vào hệ thống. Owner = Team Lead đại diện nhóm dự án / phòng ban, có tài khoản
Entra (email UPN). Bảng: system, system_member, owner_rule (ico-v2-plan.md mục 4).

1. Gói backend/app/owners/: models, service.py (CRUD hệ thống, gán / bỏ gán VM, chuyển owner), audit_log cho mọi thay đổi.
   system.code dạng SYS-0001 (dùng cơ chế sequence giống spend). owner_email, deputy_email, manager_email lưu lower-case, kiểm
   định dạng email. criticality 1–4 (1 = quan trọng nhất), env: prod|uat|dev|dr|other.
2. system_member: một VM (ci_id) thuộc tối đa một hệ thống; source manual luôn thắng các nguồn tự động.
3. Nhập / xuất Excel (shared/xlsx_io, skill spend-module mục upload an toàn): sheet "Hệ thống" (mã, tên, dự án, phòng ban,
   owner, deputy, quản lý trực tiếp, mức độ quan trọng, môi trường) và sheet "VM" (VM, vCenter, mã hoặc tên hệ thống).
   Xem trước → xác nhận → ghi; báo dòng lỗi. Xuất Excel cùng mẫu (excel_style).
4. Endpoint: /api/systems (list có lọc owner / phòng ban / dự án / criticality, phân trang), /api/systems/{id},
   /api/systems/{id}/members, POST/PUT/DELETE — require_admin hoặc vai trò mới "owner_admin" (cấu hình user, mặc định chỉ admin);
   đọc: get_current_user.
5. Frontend: mục mới trong menu Quản lý thông tin → "Hệ thống & Owner" (routes.js), khung danh sách → chi tiết; tab "VM chưa có
   owner" (đếm + tỷ lệ phủ); trên thẻ VM hiện Hệ thống / Owner và nút gán (cho người có quyền).
6. Test: CRUD, gán trùng bị chặn, manual thắng auto, nhập Excel 5 dòng (1 dòng email sai, 1 dòng VM không tồn tại).

Nghiệm thu: tạo hệ thống, gán VM, xuất / nhập Excel trên DEMO; tỷ lệ phủ owner hiện đúng.
```

## G2b — Gán tự động: thuộc tính vCenter + quy tắc

```text
Mục tiêu: gợi ý / gán owner tự động từ vCenter.

1. vCenter (skill vmware-collector): VM_PROPS thêm "parent", "resourcePool", "customValue"; đọc thêm Folder ("name", "parent")
   và ResourcePool ("name") bằng PropertyCollector theo lô, CustomFieldsManager.field để đổi key → tên thuộc tính.
   Lưu vm_snapshots.folder_path VARCHAR(512), resource_pool VARCHAR(255), custom_attrs TEXT (JSON {tên: giá trị}, stored_text).
   demo_generator sinh folder theo dự án, vài VM có custom attribute "Owner" / "System".
2. owners/rules.py: áp dụng theo thứ tự ưu tiên: manual > attribute (tên thuộc tính cấu hình được, vd "Owner", "System") >
   owner_rule (folder, resource_pool, name_regex, cluster; theo priority) > itsm (GĐ7). Regex có giới hạn độ dài + timeout an toàn
   (dùng re với pattern đã kiểm, cấm pattern > 200 ký tự). Chạy ở leader sau bộ dựng CI; chỉ đổi bản ghi không phải manual.
   Thuộc tính trỏ tới email chưa có hệ thống → tạo "đề xuất", không tự tạo hệ thống.
3. UI Cài đặt → Owner: thuộc tính vCenter dùng làm nguồn, danh sách quy tắc (thêm / sửa / thứ tự / bật tắt), nút "Chạy thử" hiện
   số VM sẽ đổi trước khi lưu; danh sách đề xuất cần duyệt.
4. Test: 4 nguồn chồng nhau trên cùng VM → đúng thứ tự ưu tiên; quy tắc folder lồng nhau; regex quá dài bị từ chối; chạy 2 lần
   không đổi.

Nghiệm thu: DEMO tự gán ≥ 80 % VM qua folder + attribute; đổi quy tắc → chạy thử khớp kết quả thật.
```

## G2c — Lọc theo owner khắp app + phạm vi truy cập Owner

```text
Mục tiêu: lọc theo hệ thống / owner / phòng ban / dự án ở mọi trang; owner đăng nhập chỉ thấy hệ thống của mình.
Đọc skill backend-design:auth-and-authorization và security-discipline trước khi làm.

1. scope.py: Scope.from_query nhận system, owner, department, project, criticality → giải ra tập VM (instanceUuid / vcenterKey+name),
   cache theo vòng sync. Áp dụng cho Capacity (cluster / host chỉ còn VM thuộc phạm vi — ghi rõ trên UI "đang lọc theo owner"),
   Inventory, Backup (coverage, client), Sức khoẻ (snapshot, alarm VM), xuất PDF / Excel, Kho báo cáo. capacity.py ⇄ core.js:
   nếu lọc ảnh hưởng mô hình thì giữ khớp (skill capacity-math).
2. scope_filter.js: thêm bộ chọn Hệ thống / Owner / Phòng ban / Dự án (tìm kiếm, nhiều lựa chọn), nhớ qua uiPref.
3. Phạm vi truy cập (ico-v2-plan.md mục 7):
   - users.access_scope VARCHAR(8) DEFAULT 'full' (additive). Entra: app role ICO.Owner (ENTRA_OWNER_ROLE, mặc định "ICO.Owner")
     được phép đăng nhập; nếu user chỉ có role này → access_scope='owner'. Áp mỗi lần đăng nhập (giống apply_entra_roles).
   - get_current_user trả 403 cho access_scope='owner' (mặc định chặn mọi endpoint cũ). get_owner_user cho phép cả full và owner,
     kèm tập system_id được phép (owner_email hoặc deputy_email = email user; full → tất cả).
   - Cổng owner: /api/owner/systems, /api/owner/vms (chỉ phạm vi được phép), giao diện tối giản #/owner (menu riêng, không lộ
     menu khác). Frontend đọc /api/auth/me.accessScope để chọn shell.
4. Test bảo mật: owner A gọi /api/owner/vms?system=<của B> → 403 / rỗng; owner gọi /api/dashboard, /api/systems, /api/spend… → 403;
   user full không bị ảnh hưởng; đổi role Entra → scope đổi ở lần đăng nhập sau và session cũ bị vô hiệu (session_version).

Nghiệm thu: lọc theo owner trên 5 trang chính và PDF; test IDOR xanh; /security-review không còn phát hiện mức cao.
```

## G2d (tuỳ chọn) — vSphere Tags

```text
Chỉ làm nếu HSC đã / sẽ dùng vSphere Tags. Ngoại lệ chỉ đọc: POST /api/session và DELETE /api/session của vSphere Automation API
(cập nhật CLAUDE.md quy tắc 1 trong cùng PR). Đọc /api/cis/tagging/category, /tag, /tag-association?action=list-attached-objects-on-tags
(POST theo spec là thao tác ĐỌC — liệt kê rõ trong CLAUDE.md, client chỉ cho đúng đường dẫn này). Lưu tag theo VM, dùng làm nguồn
"tag" trong owners/rules.py. Demo + test như G2b.
```

## G3a — Chiến dịch rà soát: dựng kỳ, tín hiệu, email

```text
Mục tiêu: mỗi 6 tháng mở một kỳ rà soát; mỗi owner nhận MỘT email tổng hợp VM của mình kèm tín hiệu dọn dẹp.
Bảng: review_campaign, review_item, review_notice (ico-v2-plan.md mục 4). Đọc skill backend-design:idempotency-and-side-effects.

1. Chuyển smtp_send / dựng email từ spend/notify.py sang backend/app/shared/mailer.py (spend gọi lại, không đổi hành vi, test spend
   vẫn xanh). Dùng cấu hình SMTP hiện có.
2. owners/review.py:
   - Lịch: REVIEW_PERIOD_MONTHS=6, REVIEW_START_MONTHS="1,7" (tháng mở kỳ), REVIEW_DUE_DAYS=21; leader kiểm mỗi vòng, mở kỳ đúng
     một lần (UNIQUE period_label, vd "2027-H1"). Admin mở kỳ thủ công được.
   - Dựng review_item cho mọi VM có owner: signals = {powerOffDays, idleDays, cpuPct, ramPct, vcpu, ramGB, diskProvGB,
     backupCoverage, oldestSnapshotDays, osEolFlag (nếu có), lastReviewDecision}. Gợi ý "nên rà" khi: tắt ≥ 30 ngày, idle ≥ 30 ngày,
     không backup, snapshot ≥ 14 ngày (ngưỡng lấy từ settings hiện có, không hard-code).
   - VM chưa có owner → danh sách riêng cho team hạ tầng, không gửi ai.
3. Email (tiếng Việt, HTML escape mọi chuỗi từ vCenter): tiêu đề "[ICO] Rà soát hệ thống <kỳ> — <n> VM cần xác nhận trước <ngày>";
   bảng VM (gợi ý rà lên đầu), tổng tài nguyên, nút "Xác nhận trên ICO" → PUBLIC_URL/#/owner/review/<campaign>. Không chứa token
   đăng nhập; người nhận đăng nhập SSO.
   - dry_run (mặc định BẬT cho kỳ đầu): không gửi, lưu nội dung để xem trước trong Cài đặt → Rà soát owner.
   - Gửi idempotent: review_notice UNIQUE(campaign, recipient, kind); lỗi SMTP ghi error, vòng sau gửi lại, không gửi trùng.
4. Test: dựng kỳ từ dữ liệu cố định (3 owner, 10 VM, 2 VM không owner) → đúng số item và số email; chạy 2 lần → không gửi lại;
   SMTP lỗi lần 1 → lần 2 gửi; escape chuỗi "<script>" trong tên VM.

Nghiệm thu: DEMO mở kỳ dry_run, xem trước 3 email đúng nội dung.
```

## G3b — Xác nhận của owner, nhắc, leo thang, đóng kỳ, thu hồi

```text
Mục tiêu: owner quyết định trên ICO; hệ thống nhắc, leo thang tới quản lý trực tiếp, đóng kỳ và tính tài nguyên thu hồi.

1. Cổng owner (#/owner/review/<id>, dùng get_owner_user): danh sách VM của owner trong kỳ, chọn cho từng VM / hàng loạt:
   Còn dùng | Thu hồi (ngày dự kiến) | Giảm cấu hình (vCPU / RAM đề xuất) | Chuyển owner (email) | Không phải của tôi; ghi chú.
   Lưu decided_by / decided_at, audit_log. Chỉ sửa được khi kỳ còn mở.
2. Nhắc / leo thang (leader, idempotent qua review_notice): reminder1 = due − 7 ngày, reminder2 = due − 2 ngày, escalation tại due
   gửi manager_email của hệ thống (thiếu manager_email → gửi Team hạ tầng, ghi cảnh báo "thiếu quản lý"), closing = due + 7 ngày:
   đóng kỳ; item chưa quyết → trạng thái "chờ team hạ tầng xác nhận" (nguồn mới "review" trong inbox.py).
3. "Chuyển owner" / "Không phải của tôi" → item vào hàng chờ admin để gán lại (G2a), không tự đổi.
4. Kết quả kỳ: trang tổng hợp (tỷ lệ phản hồi, số VM theo quyết định, tài nguyên thu hồi vCPU / RAM / disk; quy ra VND nếu có đơn giá
   trong settings REVIEW_UNIT_COST_*), xuất PDF (skill pdf-report, HSC) + Excel làm bằng chứng rà soát (ISO 27001).
5. Đối chiếu kỳ sau: VM "Thu hồi" quá ngày dự kiến mà CI vẫn active → item "chưa dọn" trong inbox; CI retired → verified_at.
6. Test: luồng đủ với đồng hồ giả (open → reminder1 → reminder2 → escalation → close), owner B không sửa được item của A,
   tài nguyên thu hồi cộng đúng (vd 3 VM: 4+8+2 vCPU = 14), đối chiếu kỳ sau.

Nghiệm thu: một kỳ DEMO chạy trọn với đồng hồ giả; PDF tổng kết đúng số; ui_audit đạt cho cổng owner ở 390 px (owner dùng điện thoại).
```

---

# ĐỢT B — Phần cứng + Sự kiện

## G4a — Nguồn Dell OME: client, thu thập, demo

```text
Mục tiêu: nguồn kind "ome" (1 instance OME 4.x) đọc thiết bị và sức khoẻ, ghép vào sổ CI qua Service Tag.
Tài liệu API OME 4.x ở Dell Developer Portal — đối chiếu tên trường thực tế; nếu khác tài liệu, ưu tiên phản hồi thật và ghi chú.

1. backend/app/hardware/: http client dựa trên storage/http.py::JsonClient. Cho phép đúng: POST /api/SessionService/Sessions
   (body {"UserName","Password","SessionType":"API"}, lấy header X-Auth-Token) và DELETE /api/SessionService/Sessions('<id>').
   Mọi POST / PUT / PATCH / DELETE khác bị chặn ở client (test chứng minh). Tài khoản OME role Viewer. TLS verify + CA tuỳ chọn
   như NetBackup. Phân trang OData ($top / $skip, @odata.nextLink).
2. ome.py: Devices (Id, Identifier, DeviceServiceTag, Model, DeviceName, PowerState, Status, ConnectionState, DeviceManagement →
   iDRAC IP, LastInventoryTime); Devices(id)/SubSystemHealth. Quy đổi Status: 1000 → ok, 3000 → bad, 4000 → crit,
   2000/5000/khác → warn (kèm lý do "không có dữ liệu"). Chu kỳ: theo vòng sync chung.
3. Bảng hw_device_state, hw_component_health (mục 4 kế hoạch), lưu trạng thái mới nhất theo nguồn (như infra.py store).
4. sources.py: kind "ome", form Cài đặt → Nguồn dữ liệu (URL, user, mật khẩu qua secrets_box, verify SSL), nút Kiểm tra kết nối
   (đăng nhập → GET 1 trang Devices → đăng xuất). scheduler: _sync_ome_task, khoá theo nguồn, chỉ leader.
5. demo_generator: thiết bị Dell PowerEdge khớp service tag của host demo (G1a) + 2 máy bare-metal + 1 máy có PSU lỗi, 1 quạt cảnh báo.
6. cmdb/builder: physical_server lấy thêm thuộc tính OME (model, iDRAC IP → key idrac_ip); thiết bị OME không khớp host nào →
   vẫn là physical_server (bare-metal).
7. orphans.py: thêm họ "hardware".
Test: parser với JSON mẫu (lưu fixture), quy đổi status, client chặn ghi, demo sync, builder ghép đúng serial.
Nghiệm thu: DEMO thấy 100 % host VMware ghép được máy chủ OME; máy bare-metal hiện trong sổ CI.
```

## G4b — Inventory sâu, bảo hành, firmware, alert, đối chiếu Opex

```text
Mục tiêu: làm giàu dữ liệu phần cứng và nối với Spend.

1. InventoryDetails: serverArrayDisks (predictive failure, status), serverRaidControllers, serverPowerSupplies, serverMemoryDevices,
   serverNetworkInterfaces (MAC → ci_key mac), serverOperatingSystems (hostname → ci_key fqdn, weak), deviceSoftware (firmware).
   Chạy mỗi OME_INVENTORY_HOURS=12 (không mỗi vòng). Bảng hw_disk_state, hw_firmware_state.
2. WarrantyService/Warranties hằng ngày → hw_warranty. Đối chiếu với Opex (opex_assets.serial + opex_items.end_date):
   - "Có bảo hành Dell, không có Opex" / "Opex hết hạn trước Dell" / "Dell hết hạn nhưng Opex còn" / "hết bảo hành và đang chạy VM
     criticality 1–2" (dùng system từ G2).
   - Đưa vào inbox nguồn "asset" (đã có) với mã đối soát; gợi ý dòng gia hạn cho Kế hoạch năm sau (budget_plan, skill spend-module).
3. UpdateService baselines → hw_firmware_state.compliance (nếu OME chưa có baseline: hiện "chưa cấu hình baseline", không lỗi).
4. AlertService/Alerts: đọc tăng dần theo TimeStamp (lưu cursor), chỉ alert phần cứng; hw_alert UNIQUE(source, alert_id),
   giữ 13 tháng (collectors/retention). Không gọi Acknowledge / RemoveAlerts.
5. inbox.py nguồn mới "hardware" (linh kiện lỗi, ổ dự báo hỏng = crit, firmware lệch = warn); alert_rules.py thêm nguồn "hardware".
Test: fixture JSON cho từng loại inventory; đối chiếu bảo hành 4 ca; cursor alert không đọc trùng; retention.
Nghiệm thu: DEMO có danh sách đối chiếu bảo hành ↔ Opex và 1 ổ dự báo hỏng lên Hộp việc.
```

## G4c — Trang Phần cứng + thẻ Máy chủ vật lý + báo cáo

```text
Mục tiêu: giao diện cho dữ liệu OME.
1. Menu Vận hành → "Phần cứng" (routes.js): danh sách → chi tiết máy chủ (trạng thái tổng + từng linh kiện bằng uiPill, ổ đĩa,
   PSU, RAM, firmware so baseline, bảo hành Dell vs Opex, host ESXi / hệ thống / VM chạy trên máy — qua sổ CI, link mở iDRAC
   (https://<idrac_ip>, rel=noopener, chỉ khi IP hợp lệ).
   Lọc theo trạng thái, model, hết bảo hành trong N ngày, hệ thống / owner (G2c).
2. Thẻ Máy chủ vật lý (G1c) thêm phần OME. Ctrl + K tìm theo service tag.
3. PDF "Sức khoẻ phần cứng" + Excel (danh sách máy, bảo hành, firmware) — skill pdf-report / hsc-design-system; đăng ký exports.py.
Nghiệm thu: ui_audit đạt 1440 + 390 px; PDF đúng số với trang.
```

## G5 — Kho sự kiện + vòng đời sự cố

```text
Mục tiêu: lưu lịch sử sự kiện và quản lý sự cố (ico-v2-plan.md mục 4: ops_event, ops_event_open, ops_incident).
Đọc skill backend-design:incident-thinker và idempotency-and-side-effects.

1. backend/app/events/: record_transitions(db, source, current: {event_key: (level, ci_id, title, detail)}) so với ops_event_open →
   ghi open / worse / better / clear vào ops_event (append-only). Gọi ở leader sau mỗi lần cập nhật: infra (datastore, snapshot,
   alarm, health), storage alert, hardware (G4), capacity finding, backup client status.
   Nguồn bị lỗi khi thu thập → KHÔNG đóng sự kiện của nguồn đó (giống ops_alerts giữ alert khi provider lỗi).
2. Sự cố: event open trên CI chưa có incident mở → tạo incident; thêm event cùng CI → gắn vào; mọi event clear → resolved tự động
   (resolved_by = "system"); có thể đóng tay. Flapping: ≥ 4 lần open/clear trong 6 giờ → flapping=true, gửi cảnh báo 1 lần.
3. API: /api/incidents (lọc trạng thái, mức, nguồn, hệ thống / owner, thời gian), /api/incidents/{id} (timeline event),
   ack / assign / resolve (get_current_user, audit_log). /api/ci/{id}/timeline.
4. inbox.py: các nguồn trên đọc từ incident đang mở (giữ key / alertKey cũ để không bắn lại Discord khi chuyển). ops_alerts.py đẩy theo
   chuyển trạng thái incident.
5. Chỉ số: MTTA, MTTR theo tháng / nguồn / hệ thống; top 10 CI nhiều sự cố. Thẻ đối tượng có tab Timeline.
6. Retention ops_event 13 tháng.
Test: chuỗi chuyển trạng thái (ok → bad → crit → bad → ok) ra đúng 4 event + 1 incident resolved; nguồn lỗi giữ nguyên; flapping;
MTTA / MTTR với số cụ thể.
Nghiệm thu: Hộp việc không đổi hành vi với người dùng; trang Sự cố có thống kê tháng trên DEMO.
```

## G6 — Event vCenter, nhật ký thay đổi, gom sự cố theo đồ thị CI

```text
Mục tiêu: biết ai đổi gì, khi nào; gom sự kiện liên quan thành một sự cố.

1. vCenter: eventManager.QueryEvents(filter) theo cửa sổ thời gian từ cursor (lưu theo vcenter_key), lọc loại: VmCreatedEvent,
   VmRemovedEvent, VmReconfiguredEvent, VmRelocatedEvent / DrsVmMigratedEvent / VmMigratedEvent, VmPoweredOff/OnEvent,
   HostConnectionLostEvent, HostEnteringMaintenanceModeEvent, HostExitMaintenanceModeEvent, VmRestartedOnAlternateHostEvent
   (HA), DasHostFailedEvent, AlarmStatusChangedEvent. Tối đa 1.000 / lần, chia cửa sổ nếu đầy. Đây là thao tác đọc; ghi chú trong
   CLAUDE.md quy tắc 1. Demo generator sinh event tương ứng.
2. Bảng change_log (at, vcenter_key, ci_id, kind, user_name, detail JSON). Gộp với diff snapshot (history.get_vm_changes) — event có
   người thực hiện thì ưu tiên. Trang "Nhật ký thay đổi" (lọc hệ thống / owner / loại / người) + tab trên thẻ đối tượng.
3. Gom sự cố: event mới trên CI X trong 15 phút (EVENT_CORRELATE_MINUTES) mà có incident mở trên CI tổ tiên qua liên kết strong
   (upstream từ cmdb/impact) → gắn vào incident đó, root_ci_id = CI tổ tiên cao nhất có sự kiện. Hiện "Thay đổi trong 24 giờ trước
   sự cố" trên trang sự cố.
Test: kịch bản PSU lỗi (OME) + alarm phần cứng host + HA restart 2 VM trong 10 phút → 1 incident, root = physical_server;
liên kết weak không gom.
```

---

# ĐỢT C — Nhu cầu ITSM + Kế hoạch

## G7a — Kết nối ServiceDesk Plus On-Prem (chỉ đọc) + nạp lùi

```text
Mục tiêu: nguồn kind "sdp" đọc các yêu cầu tạo VM / tăng tài nguyên từ ServiceDesk Plus On-Premises, API v3, technician key.
Đối chiếu tài liệu API v3 đúng build SDP của HSC; nếu tên trường khác thì theo phản hồi thật.

1. backend/app/itsm/sdp.py: chỉ GET. Header authtoken: <technician key> (secrets_box), Accept: application/vnd.manageengine.sdp.v3+json.
   - Danh sách: GET /api/v3/requests?input_data=<JSON list_info: row_count 100, start_index, sort_field "created_time", sort_order "asc",
     search_criteria: template.name trong ITSM_TEMPLATES (cấu hình, nhiều tên) và last_updated_time > cursor>.
   - Chi tiết: GET /api/v3/requests/{id} lấy description (HTML), requester (email), status, created_time / resolved_time (ms epoch),
     approval_status, udf_fields nếu có.
   - Giới hạn tốc độ (ITSM_RATE_PER_MIN), retry có backoff, timeout.
2. Nạp lùi: ITSM_BACKFILL_FROM=2025-10-01, chạy theo lô nhỏ ở leader cho tới khi xong, sau đó tăng dần theo cursor mỗi 30 phút.
3. itsm_request (mục 4 kế hoạch), lưu description gốc (đã làm sạch, giới hạn 64 KB) để xử lý lại khi đổi parser.
4. sources.py kind "sdp" + form Cài đặt (URL, technician key, template, ngày nạp lùi, nút Kiểm tra kết nối). demo_generator: 60 yêu cầu
   mẫu từ 10/2025, body HTML có bảng (một số bảng lệch nhãn, thiếu cột, gộp ô) + 5 yêu cầu chỉ có chữ tự do.
Test: client chặn mọi phương thức ngoài GET; phân trang; cursor; nạp lùi dừng / tiếp tục.
```

## G7b — Đọc bảng trong body + AI dự phòng + hàng chờ duyệt

```text
Mục tiêu: chuyển body HTML thành dòng tài nguyên chuẩn (itsm_request_line).

1. itsm/parse.py: html.parser (thư viện chuẩn, không tải tài nguyên ngoài) → bảng → hàng. Nhận dạng nhãn qua itsm_label_map
   (chuẩn hoá bằng shared/text.norm_text; seed sẵn: "tên máy|hostname|vm name" → vm_name, "vcpu|cpu|số core" → vcpu,
   "ram|memory (gb)" → ram_gb, "ổ cứng|disk|dung lượng (gb)" → disk_gb, "số lượng" → qty, "môi trường" → env, "hđh|os" → os,
   "dự án|project" → project). Hỗ trợ bảng dọc (nhãn | giá trị) và bảng ngang (nhiều VM). parse_number cho "8 GB", "1,5 TB" → GB.
   Phân loại kind theo template + tiêu đề (new_vm | resize | decommission | other). Kết quả kèm confidence.
2. AI dự phòng (chỉ khi parse thất bại hoặc confidence < 0.7): tác vụ mới qua ai_platform.run() (che tên, hạn mức, ai_runs),
   trả JSON theo schema cố định; KHÔNG tự tin cậy: parse_status="review".
3. Hàng chờ duyệt (UI Mua sắm/… → đặt ở mục mới "Nhu cầu hạ tầng"): xem body gốc (đã sanitize, hiển thị an toàn) cạnh kết quả,
   sửa / duyệt / bỏ qua; thêm nhãn mới vào itsm_label_map từ màn hình. Chỉ yêu cầu parsed hoặc đã duyệt mới vào thống kê.
4. Test: 8 body mẫu (dọc, ngang, gộp ô, nhãn lạ, đơn vị TB, ô trống, HTML có script → bị bỏ, chữ tự do → review).
Nghiệm thu: ≥ 90 % yêu cầu demo có bảng được parse tự động.
```

## G7c — Nối yêu cầu ↔ hệ thống ↔ VM

```text
1. Dự án / phòng ban trong yêu cầu → system (G2): khớp chính xác theo mã / tên; gợi ý gần đúng để admin xác nhận; lưu ánh xạ
   project → system để dùng lại. Yêu cầu tạo VM đã khớp hệ thống → nguồn gán owner "itsm" (G2b, ưu tiên thấp nhất).
2. Dòng yêu cầu → VM: vm_name / hostname trùng (strong nếu trùng FQDN hoặc tên đầy đủ + VM tạo trong [created_at, resolved_at + 14 ngày]
   theo change_log G6 hoặc snapshot đầu tiên; weak nếu chỉ trùng tên ngắn). itsm_request_ci.
3. Thẻ VM: "Tạo theo yêu cầu #<id>" (link SDP: ITSM_BASE_URL/WorkOrder.do?woMode=viewWO&woID=<id>), tài nguyên xin vs hiện tại.
Test: ghép strong / weak / không ghép; yêu cầu resize ghép VM có sẵn.
```

## G8 — Tổng kết năm + kế hoạch năm sau

```text
Mục tiêu: báo cáo nhu cầu năm và mô hình kế hoạch đưa vào Kế hoạch ngân sách (spend/budget_plan.py). Skill capacity-math, spend-module.

1. planning: hàm thuần (test số liệu cụ thể):
   - demand_summary(năm): số VM mới, tổng vCPU / RAM / disk xin theo quý / dự án / phòng ban / môi trường; thời gian xử lý trung vị.
   - fulfilment: xin → cấp (VM ghép strong, cấu hình lúc tạo) → dùng sau 90 và 180 ngày (cpu / ram usage từ vm_snapshots) →
     hệ số "dùng thật / xin" theo phòng ban.
   - forecast(năm sau) = tăng trưởng tự nhiên đo từ lịch sử snapshot (không tính VM theo yêu cầu, tránh đếm đôi)
     + nhu cầu dự án đã biết (yêu cầu đã duyệt chưa cấp + danh sách dự án năm sau nhập tay, nhân hệ số dùng thật nếu bật)
     − thu hồi đã cam kết (G3: quyết định Thu hồi / Giảm cấu hình chưa thực hiện) → nhu cầu ròng theo cluster
     → số host cần thêm theo cấu hình host chuẩn (sizing.py) với N+1 và ngưỡng mục tiêu.
2. Trang "Nhu cầu hạ tầng" → tab Tổng kết năm + tab Kế hoạch năm sau (nhập danh sách dự án năm sau, chọn hệ số, xem từng thành phần).
3. Nút "Đưa vào Kế hoạch ngân sách": tạo / cập nhật dòng Capex "Mở rộng hạ tầng ảo hoá <năm>" trong budget_plan với diễn giải nguồn số
   (idempotent theo năm).
4. PDF điều hành (1 trang kết luận + phụ lục) + Excel chi tiết; đăng ký exports.py.
Test: bộ số cố định 12 yêu cầu / 3 phòng ban → kiểm từng con số tổng hợp, hệ số, forecast và số host.
Nghiệm thu: số trong PDF khớp trang; dòng Capex tạo một lần, cập nhật khi chạy lại.
```

---

# Phần tiếp theo (khung, viết chi tiết khi tới lượt)

- **G9 — Sức khoẻ sâu:** baseline cấu hình ESXi (SSH / ESXi Shell, lockdown, NTP, syslog, admission control, DRS, build lệch trong
  cluster), VM (Tools, HW version, ISO gắn); vòng đời ESXi / vCenter (bảng EoGS nhập tay) + bảng VMSA → build đã vá (admin import);
  chứng chỉ TLS (vCenter, ESXi, NBU, OME, App Proxy); contention qua PerformanceManager.QueryPerf (cpu.ready, co-stop, mem.vmmemctl,
  mem.swapped, disk latency) vào metrics.py; health và lịch backup VCSA; RPO theo criticality, catalog NBU, sổ diễn tập khôi phục,
  immutable; đơn giá nội bộ + showback theo hệ thống / phòng ban.
- **G10 — Đầu ra:** điểm sức khoẻ theo cluster / hệ thống; báo cáo vận hành tháng tự gửi; kênh Teams + email cho cảnh báo;
  ticket ServiceDesk Plus từ sự cố và việc dọn dẹp (cần quyết định cho phép GHI vào SDP); bộ bằng chứng ISO 27001 (A.5.30, A.8.6,
  A.8.8, A.8.9, A.8.13, A.8.32) xuất từ dữ liệu G3, G5, G6, G9.
