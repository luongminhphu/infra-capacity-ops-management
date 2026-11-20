# Thiết kế: Hợp nhất dữ liệu theo Serial / Service Tag và IP — "Hồ sơ thiết bị" và "Đối soát"

Trạng thái: **đề xuất, chờ chủ sản phẩm chốt mục 14**. Xây trên sổ CI (G1), Hệ thống & Owner (G2), Dell OME (G4);
dùng chung kho sự kiện của G5 khi có. Mọi nguồn vẫn **chỉ đọc** (CLAUDE.md quy tắc 1).

## 0. Tóm tắt quyết định

| # | Quyết định |
|---|---|
| D1 | **Serial / Service Tag là khoá định danh vật lý mạnh** — không đổi trong đời thiết bị, dùng để *hợp nhất* bản ghi của nhiều nguồn thành một thiết bị. |
| D2 | **IP là khoá mạng có thời hạn và phạm vi** — một IP chỉ thuộc một đối tượng *trong một khoảng thời gian, trong một vùng mạng*. IP chỉ dùng để *liên kết* (liên kết yếu, ghi rõ "ghép theo IP"), **không bao giờ hợp nhất** hai bản ghi. |
| D3 | Thêm **chỉ mục đảo** `serial_index` (serial → mọi nơi xuất hiện) và **lịch sử gán IP** `ip_binding` (IP + vùng → đối tượng, từ – đến). Cả hai dựng lại sau mỗi vòng poll ở pod leader, ghi theo tập như sổ CI. |
| D4 | Thêm **bộ máy đối soát** (`xcheck`): bộ quy tắc chạy sau khi dựng chỉ mục, sinh **phát hiện có mã đối soát ổn định** (FNV-1a, như capacity / bảo hành G4b). Mỗi phát hiện có vòng đời mở → đã hết → bỏ qua có lý do. |
| D5 | **Một màn hình chung "Hồ sơ thiết bị"** (mở từ Ctrl + K bằng serial, asset tag, IP hoặc tên), cộng trang **"Đối soát dữ liệu"** (ma trận thiết bị × nguồn) và **"Tra IP"**. |
| D6 | Nguồn đang lỗi / dữ liệu cũ **không được đóng** phát hiện hay kết thúc gán IP của chính nguồn đó (giống `ops_alerts` khi provider lỗi). |

## 1. Mục tiêu và phạm vi

**Mục tiêu**
1. Nhìn xuyên suốt một thiết bị: máy chủ vật lý ↔ host ảo hoá ↔ VM ↔ hệ thống / owner ↔ backup ↔ storage ↔ tài sản DC ↔
   Opex / Capex / hợp đồng ↔ bảo hành Dell ↔ alert phần cứng ↔ sự kiện.
2. Tự phát hiện **chỗ lệch giữa các nguồn** (có trong nguồn này, thiếu ở nguồn kia; lệch model; IP trùng; trả bảo hành cho máy
   không tồn tại…) — kèm mã đối soát và gợi ý xử lý.
3. Trả lời nhanh "IP này đang / đã thuộc máy nào" để điều tra sự cố.

**Ngoài phạm vi (đợt này)**: thiết bị mạng (switch, firewall), IPAM đầy đủ, quét mạng chủ động (ping / port scan / DNS
lookup ra ngoài) — ICO không chủ động dò mạng, chỉ đọc nguồn đã khai báo.

## 2. Hiện trạng

### 2.1 Khoá đang thu và đã ghép

| Nguồn | Serial / Service Tag | IP | MAC / tên | Đã ghép vào sổ CI |
|---|---|---|---|---|
| vCenter host | `summary.hardware.otherIdentifyingInfo` (ServiceTag / SerialNumberTag) | — *(chưa đọc vmk)* | tên host (FQDN / short name) | `hypervisor_host` + khoá `serial`, `hw_uuid` → `physical_server` |
| vCenter VM | — | **1 IP** (`guest.ipAddress`) | guest hostname | `vm` + khoá `ip`, `fqdn`, `short_name` |
| Nutanix | node serial | 1 IP / VM | — | như vCenter |
| Proxmox | — | — | — | chỉ theo tên |
| Dell OME | service tag máy; serial ổ đĩa / PSU / RAM | iDRAC IP | MAC NIC, hostname HĐH | `physical_server` + `idrac_ip`, `mac`, tên |
| Pure FlashArray | — *(chưa đọc serial mảng / controller)* | IP quản lý = địa chỉ nguồn | — | `storage_array` theo mã nguồn |
| NetApp ONTAP | **có đọc `serial_number` node nhưng không lưu** | data LIF (NFS) | — | `storage_array` theo mã nguồn |
| NetBackup | — | tên client (có khi là IP) | — | `backup_client` → VM theo tên / IP |
| Thiết bị DC (Excel) | serial, asset tag | — | device id (tên) | `dc_asset` → `physical_server` (serial) |
| Opex | `opex_assets.serial` | — | host_name, vCenter | `opex_item` → `physical_server` (serial), host (tên, yếu) |
| Capex / hợp đồng | — | — | — | không ghép thiết bị |

### 2.2 Khoảng trống

1. **Không có chỉ mục "serial này xuất hiện ở đâu"**: phải đi qua từng bảng; serial của linh kiện (ổ, PSU) không tra được.
2. **Không có lịch sử IP**: `ci_key` chỉ giữ IP hiện tại — không trả lời được "hôm qua 10.1.2.3 là máy nào".
3. **Thiếu khoá ở nguồn**: VM chỉ 1 IP (thiếu NIC phụ, IPv6); host thiếu IP quản lý; mảng storage thiếu serial;
   OME chưa lưu ngày giao hàng (`SystemShipDate`).
4. **Đối soát chỉ có 4 ca bảo hành** (G4b). Các lệch khác (máy chạy mà không có trong sổ tài sản, thanh lý mà còn chạy,
   IP trùng…) chưa được phát hiện.
5. **Không có màn hình chung**: thông tin rải ở thẻ máy chủ, Phần cứng, Thiết bị DC, Opex, Backup.

## 3. Nguyên tắc ghép

### 3.1 Độ tin cậy của khoá

| Khoá | Loại | Dùng để |
|---|---|---|
| `serial` (máy) | **mạnh** | hợp nhất bản ghi thành một `physical_server` / `storage_controller` |
| `hw_uuid`, `instance_uuid`, `naa` | mạnh | như hiện có (G1) |
| `asset_tag` | mạnh trong phạm vi sổ tài sản | tìm kiếm, nối DC asset |
| `mac` | mạnh khi không phải MAC ảo / dùng chung | nối NIC ↔ máy |
| `ip` + vùng mạng + thời gian | **yếu** | liên kết, tra cứu, phát hiện xung đột |
| `fqdn` / `short_name` | yếu | liên kết khi không có khoá mạnh |

- Liên kết yếu luôn mang `matchedBy` (`ip`, `hostname`…) và **không** dùng để gom sự cố (giữ quy tắc của G1).
- Một khoá yếu khớp **nhiều hơn một** đối tượng → không liên kết, sinh phát hiện "mơ hồ" để người xử lý.

### 3.2 Chuẩn hoá (một chỗ duy nhất: `shared/identity.py`)

- `norm_serial`: giữ như hiện tại (chữ hoa, chỉ chữ số).
  - Thêm **danh sách giá trị rác** bị coi là "không có serial": `TOBEFILLEDBYOEM`, `0`, `NONE`, `DEFAULTSTRING`, `0123456789`, chuỗi toàn `0`…
  - Serial rác → phát hiện S08, không bao giờ dùng để ghép.
- **Bí danh serial** (`serial_alias`) cho trường hợp một thiết bị có hai số khác nhau ở hai nguồn:
  - Nutanix chassis (block serial) ↔ node serial;
  - HPE serial ↔ product id;
  - lỗi gõ trong Excel đã được xác nhận.

  Bí danh do người tạo (có audit), không tự đoán.
- `norm_ip`: giữ dạng chuẩn IPv4 / IPv6.
  - Bỏ IP vô nghĩa cho định danh: loopback, link-local (`169.254/16`, `fe80::/10`), `0.0.0.0`, APIPA, IP của docker / k8s
    overlay khai trong cấu hình.
  - **Vùng mạng** (`ip_scope`) gán theo CIDR khai báo; IP không thuộc vùng nào → vùng `default`.
- `norm_mac`: như hiện có. Bỏ MAC rỗng / `00:…:00` và MAC đa hướng.
  - Danh sách tiền tố MAC ảo (VMware `00:50:56`, Hyper-V `00:15:5D`…) chỉ dùng cho NIC ảo, không ghép vật lý.

### 3.3 Thứ tự ưu tiên thuộc tính (một nguồn sự thật cho mỗi thuộc tính)

| Thuộc tính | Ưu tiên (cao → thấp) |
|---|---|
| Model, hãng | OME → hypervisor → DC asset → Opex |
| Tên hiển thị máy vật lý | service tag (CI) — tên OME / device id DC asset là thuộc tính |
| Trạng thái phần cứng | OME → sức khoẻ host vCenter |
| Ngày đưa vào dùng | DC asset → ngày giao hàng Dell (OME) |
| Bảo hành | Dell (OME) và Opex — **so sánh**, không ghi đè (S10–S13) |
| Vị trí, chức năng nghiệp vụ | DC asset |
| Hệ thống / owner | G2 (qua VM) |

Khi hai nguồn mạnh cho giá trị khác nhau → giữ giá trị theo thứ tự trên **và** sinh phát hiện lệch (S04).

## 4. Kiến trúc

```
 Nguồn (chỉ đọc)                    Lưu trạng thái (hiện có)           Lớp hợp nhất (mới)                     Dùng
 ─────────────────                  ───────────────────────            ─────────────────────                  ─────
 vCenter / Nutanix / Proxmox ─┐     host_snapshots, vm_snapshots ─┐
 NetBackup ──────────────────┤     backup_jobs, policies ───────┤    ┌─ sổ CI (G1, có sẵn) ─────────┐
 Pure / NetApp ──────────────┤ →   storage_* ───────────────────┤ →  ├─ serial_index  (mới) ────────┤ →  xcheck engine → xcheck_finding
 Dell OME ───────────────────┤     hw_* ────────────────────────┤    ├─ ip_binding    (mới) ────────┤        │
 Excel thiết bị DC / Opex ───┘     dc_assets, opex_assets ──────┘    └─ serial_alias / ip_scope ────┘        ├→ Hộp việc (nguồn "xcheck")
                                                                                                             ├→ Cảnh báo Discord (alert_rules)
                     vòng poll ở pod leader:  sync → build CI → index serial + IP → xcheck → owners → alerts  ├→ Kho sự kiện G5 (open / clear)
                                                                                                             └→ Hồ sơ thiết bị · Đối soát · Tra IP · PDF / Excel
```

- **Một gói mới** `backend/app/correlation/`:
  - `models.py` (bảng mục 5);
  - `index.py` (dựng `serial_index`, `ip_binding`);
  - `rules.py` (bộ quy tắc);
  - `engine.py` (chạy quy tắc, ghi phát hiện theo tập);
  - `service.py` (đọc cho màn hình / PDF / Excel — một nguồn số);
  - `routers/correlation.py`.
- **Chỉ đọc cơ sở dữ liệu**, không gọi nguồn ngoài.
  - Dựng lại toàn bộ trong bộ nhớ, ghi phần chênh bằng vài câu lệnh theo tập (giống `cmdb/registry.py`).
  - Hai lần dựng liên tiếp không đổi gì.
- Chạy **sau** `cmdb.builder.build_all()` trong `scheduler`, trước `owners.rules`. Lỗi lớp hợp nhất không dừng vòng poll.
- **Mỗi nguồn có cờ độ tươi**, lấy từ `source_status.py` (đã có): `ok` / `error` / `stale` / `never`.
  - Quy tắc và việc đóng gán IP chỉ dựa trên nguồn `ok`.
  - Nguồn khác giữ nguyên trạng thái cũ của mình (D6).

## 5. Cấu trúc dữ liệu

Tất cả là bảng mới (không đụng cột cũ). Cột JSON đăng ký `stored_text` ở cuối `correlation/models.py`. Index và CHECK khai
trên model (quy tắc 4).

### 5.1 `serial_alias` — bí danh do người xác nhận

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `alias_key` | String(128) PK | `norm_serial` của số phụ |
| `canonical_key` | String(128) NOT NULL, index | số chính (service tag) |
| `reason` | String(255) NOT NULL | "Nutanix block serial", "lỗi gõ Excel"… |
| `created_by`, `created_at` | | audit `correlation.alias.add/remove` |

CHECK `alias_key <> canonical_key`. Khi dựng chỉ mục: thay mọi `alias_key` bằng `canonical_key` trước khi ghép.

### 5.2 `serial_index` — mọi nơi một serial xuất hiện (chỉ mục đảo, dẫn xuất)

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `id` | PK | |
| `serial_key` | String(128) NOT NULL | sau khi áp bí danh |
| `scope` | String(12) | `device` (máy / mảng / controller) \| `component` (ổ, PSU, RAM, NIC) |
| `family` | String(16) | `virtualization` \| `hardware` \| `storage` \| `asset` \| `finance` |
| `source_key` | String(64) | mã nguồn (vc1, ome-hcm, sheet DC asset, `opex`) |
| `ref_type` | String(24) | `host`, `ome_device`, `ome_disk`, `ome_psu`, `ome_memory`, `dc_asset`, `opex_asset`, `storage_node`… |
| `ref_id` | String(255) | khoá bản ghi gốc (`vc1:esx01`, `ome-hcm:10074`, `OPX-2026-0001`…) |
| `ci_id` | FK ci NULL | CI của thiết bị chứa (linh kiện → máy chủ) |
| `label` | String(255) | tên hiển thị (host name, device id, mã Opex…) |
| `model` | String(128) | model theo nguồn đó (cho S04) |
| `attrs` | Text JSON | ít trường so sánh: `acquiredOn`, `removed`, `hostName`, `vcenterKey`, `status`… |
| `first_seen`, `last_seen` | DateTime | |

UNIQUE (`serial_key`, `ref_type`, `ref_id`); index (`serial_key`), (`ci_id`). Dòng không còn thấy quá `CMDB_RETIRE_DAYS`
bị xoá khỏi chỉ mục (đây là dữ liệu dẫn xuất; lịch sử nằm ở sự kiện G5 / phát hiện).

### 5.3 `ip_scope` — vùng mạng (khai báo tay, admin)

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `id` | PK | |
| `name` | String(64) UNIQUE | `prod-dc1`, `mgmt-oob`, `dr-site`… |
| `cidrs` | Text CSV | `10.10.0.0/16,10.20.0.0/16` (kiểm tra `ipaddress`) |
| `priority` | Integer | CIDR chồng nhau: ưu tiên cao thắng |
| `shared_ips` | Text CSV | VIP / IP dùng chung được phép trùng (cluster IP, VRRP, NAT) |
| `note` | String(255) | |

Không khai vùng nào → mọi IP thuộc vùng `default` (vẫn chạy đúng cho mạng không trùng dải).

### 5.4 `ip_binding` — IP thuộc đối tượng nào, từ khi nào đến khi nào

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `id` | PK | |
| `ip` | String(64) NOT NULL | `norm_ip` |
| `scope` | String(64) NOT NULL | tên vùng |
| `ci_id` | FK ci NOT NULL | VM, host, physical_server, storage_array, backup_client |
| `role` | String(12) | `guest` (IP của VM), `mgmt` (vmk / quản lý host), `idrac`, `data` (LIF storage), `backup` (tên client là IP) |
| `source` | String(16) | `vcenter`, `ome`, `storage`, `netbackup`, `import` |
| `source_key` | String(64) | |
| `first_seen` | DateTime NOT NULL | |
| `last_seen` | DateTime NOT NULL | |
| `ended_at` | DateTime NULL | NULL = đang gán |

- **Index**:
  - (`ip`, `scope`, `ended_at`) để tra IP hiện tại;
  - (`ci_id`) để lấy IP của một máy;
  - (`last_seen`) cho retention.
- **CHECK**: `last_seen >= first_seen`; `ended_at IS NULL OR ended_at >= first_seen`.
- **Partial unique** (PostgreSQL): (`ip`, `scope`, `ci_id`, `role`) WHERE `ended_at IS NULL` — một gán đang mở cho mỗi bộ.
  - **Cố ý không** UNIQUE (`ip`, `scope`) WHERE mở: IP trùng là dữ liệu cần thấy (I01), không phải lỗi ghi.
- **Giữ** `IP_BINDING_RETENTION_MONTHS` (đề xuất 13 tháng, cùng `hw_alert`) trong `collectors/retention.py`.

**Quy tắc mở / đóng** (mỗi vòng, chỉ cho nguồn có trạng thái `ok`):
- Thấy (ip, scope, ci, role) chưa có gán mở → mở mới (`first_seen = last_seen = now`).
- Còn thấy → cập nhật `last_seen`.
- Không còn thấy quá `IP_BINDING_GRACE_MINUTES` (đề xuất 2 × chu kỳ poll) → `ended_at = last_seen`. VM tắt / Tools tạm mất
  IP một vòng không làm đứt lịch sử.
- Nguồn lỗi / cũ → không đóng gán nào của nguồn đó.

### 5.5 `xcheck_finding` — phát hiện đối soát

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `id` | PK | |
| `code` | String(16) UNIQUE | `PREFIX-XXXXX` = tiền tố quy tắc + 5 hex FNV-1a(quy tắc \| chủ thể) — ổn định giữa các vòng |
| `rule` | String(8) NOT NULL | `S01`… `I07` (CHECK trong danh sách) |
| `subject_kind` | String(8) | `serial` \| `ip` \| `ci` |
| `subject` | String(255) | serial_key / `ip@scope` / ci id |
| `ci_id` | FK ci NULL | để nối Hồ sơ thiết bị và lọc owner |
| `level` | String(8) | `crit` \| `bad` \| `warn` \| `info` |
| `title`, `detail` | String(300), Text JSON | bằng chứng: nguồn nào nói gì, lúc nào |
| `state` | String(8) | `open` \| `resolved` \| `ignored` |
| `first_seen`, `last_seen`, `resolved_at` | DateTime | |
| `ignored_by`, `ignored_reason`, `ignored_until` | | bỏ qua có lý do, tự mở lại khi hết hạn (mặc định 90 ngày) |

- **Ghi theo tập**: phát hiện hiện tại so với bảng → mở mới / cập nhật / đánh dấu đã hết.
- Đã hết rồi xuất hiện lại → mở lại cùng mã. G5 sẽ ghi các lần chuyển trạng thái vào kho sự kiện.

### 5.6 Mở rộng sổ CI

- **CI mới `storage_controller`**: serial node NetApp / controller Pure, liên kết `on` → `storage_array`. Cho phép ghép mảng
  storage với DC asset / Opex bảo hành storage theo serial.
- **`physical_server` thêm thuộc tính** từ OME: `shipDate`, `omeName` (đã có).
- **Khoá `ip` trong `ci_key` giữ như hiện tại** (IP hiện tại, dùng cho tìm kiếm). Lịch sử và vai trò của IP nằm ở `ip_binding`.

## 6. Đồng bộ và vòng đời

1. **Thu thêm khoá ở nguồn** (chỉ đọc, cùng phiên đang có — không thêm ngoại lệ ghi nào):

   | Nguồn | Thêm gì | Cách đọc |
   |---|---|---|
   | vCenter VM | mọi IP + MAC từng NIC | `guest.net` (`ipConfig.ipAddress`, `macAddress`) trong cùng PropertyCollector |
   | vCenter host | IP vmkernel + vai trò (management / vMotion / vSAN) | `config.network.vnic` + `config.virtualNicManagerInfo` |
   | Nutanix | mọi IP NIC VM; IP CVM / hypervisor | `vm_nics`, `/hosts` (đã gọi) |
   | NetApp | serial node | đã gọi `/api/cluster/nodes` — chỉ cần lưu `serial_number` |
   | Pure | serial mảng / controller | `GET /api/2.x/hardware` (đã gọi) — trường `serial` |
   | OME | ngày giao hàng, IP quản lý khác iDRAC | `SystemShipDate` (Warranties), `DeviceManagement[]` (đã đọc) |
   | NetBackup | — | client là IP → `ip_binding` role `backup` |
   | Nhập tay (tuỳ chọn) | danh sách IP / VIP / IPAM | Excel qua `shared/xlsx_io.py` → `ip_scope.shared_ips`, `ip_binding` source `import` |

   Mỗi trường mới có trong `demo_generator.py` tương ứng (quy tắc 2).
2. **Thứ tự trong vòng poll** (pod leader): đồng bộ nguồn → `cmdb.build_all` → `correlation.index` (serial + IP) →
   `correlation.engine` (quy tắc) → `owners.rules` → hộp việc / cảnh báo → (G5) sự kiện.
3. **Thao tác tay** (admin hoặc owner admin, có audit):
   - thêm / xoá bí danh serial;
   - khai vùng IP và IP dùng chung;
   - bỏ qua phát hiện kèm lý do và hạn;
   - "đã xử lý" (đóng tay, tự mở lại nếu điều kiện còn).
4. **Hiệu lực ngay**: thao tác tay ghi bảng và đánh dấu "cần dựng lại". Vòng kế tiếp áp dụng; màn hình hiện "đang chờ vòng
   đồng bộ" hoặc admin bấm *Đồng bộ ngay* (đã có ở `POST /api/sources/sync-all`).

## 7. Bộ quy tắc đối soát

Mức theo 4 mức chuẩn (+ `info` cho tra cứu, không vào hộp việc). Cột "Cần nguồn" = quy tắc chỉ chạy khi các nguồn đó `ok`.

### 7.1 Theo serial

| Mã | Phát hiện | Điều kiện | Mức | Gợi ý xử lý | Cần nguồn |
|---|---|---|---|---|---|
| S01 | Thiết bị đang chạy chưa có trong sổ tài sản | serial có ở host / OME / storage node, không có dòng DC asset (kể cả bí danh) | THEO DÕI | Bổ sung vào danh sách thiết bị DC | nguồn thiết bị + có sổ DC |
| S02 | Tài sản không còn thấy chạy | DC asset `server` / `storage` chưa thanh lý, không nguồn nào thấy serial > 30 ngày, trong khi nguồn quản lý loại đó đang `ok` | THEO DÕI | Xác nhận đã tắt / thanh lý / sai SN | OME hoặc hypervisor / storage |
| S03 | Đã thanh lý / luân chuyển nhưng vẫn chạy | DC asset `removed` hoặc `disposed_on` < hôm nay, serial vẫn thấy ở nguồn | **VƯỢT NGƯỠNG** | Kiểm tra máy đang chạy là máy nào; sửa sổ tài sản | như S01 |
| S04 | Lệch model giữa nguồn | model chuẩn hoá (bỏ hãng, khoảng trắng, "PowerEdge") khác nhau giữa OME / hypervisor / DC asset | THEO DÕI | Sửa sổ tài sản hoặc SN nhập nhầm sang máy khác | ≥ 2 nguồn |
| S05 | Một serial cho nhiều thiết bị | cùng serial trên 2 host khác vCenter-host, 2 dòng DC asset khác nhau, hoặc 2 máy OME | CẢNH BÁO | Gõ nhầm SN / clone BIOS / cần bí danh | bất kỳ |
| S06 | Opex trả bảo hành cho máy không tồn tại | `opex_assets.serial` của Opex nhóm bảo hành còn hiệu lực, không có ở nguồn nào và không có trong sổ tài sản | CẢNH BÁO | Kiểm tra danh sách SN hợp đồng — có thể đang trả tiền thừa | mọi nguồn thiết bị `ok` |
| S07 | Opex ghi sai host | `opex_assets.host_name` / `vcenter_key` khác host thực tế mang serial đó | THEO DÕI | Cập nhật Opex | hypervisor |
| S08 | Nguồn không báo serial / serial rác | host ESXi / OME device thiếu serial hoặc giá trị trong danh sách rác | THEO DÕI | Cập nhật BIOS / khai bí danh | nguồn đó |
| S09 | Ngày đưa vào dùng lệch ngày giao hàng | \|`dc_assets.acquired_on` − OME `SystemShipDate`\| > 90 ngày | thông tin | Kiểm tra ngày nhập sổ (ảnh hưởng tuổi 5 năm) | OME + sổ DC |
| S10–S13 | 4 ca bảo hành Dell ↔ Opex | chuyển nguyên từ G4b (`WNOP`, `WOPE`, `WDLE`, `WCRT`) | như G4b | như G4b | OME |
| S14 | Linh kiện dự báo hỏng còn bảo hành | serial ổ đĩa `predictive_failure`, máy còn bảo hành Dell | VƯỢT NGƯỠNG | Mở case RMA với Dell: service tag + SN ổ có sẵn | OME |
| S15 | Linh kiện chuyển máy | cùng serial ổ / RAM / PSU xuất hiện ở máy khác so với lần trước | thông tin | Ghi nhận thay linh kiện | OME |

### 7.2 Theo IP

| Mã | Phát hiện | Điều kiện | Mức | Gợi ý | Cần nguồn |
|---|---|---|---|---|---|
| I01 | Xung đột IP | ≥ 2 CI khác nhau đang giữ cùng IP trong cùng vùng, IP không thuộc `shared_ips` | CẢNH BÁO | Một trong hai máy cấu hình sai / clone VM chưa đổi IP | các nguồn có IP đó `ok` |
| I02 | IP quản lý trùng IP khách | IP role `idrac` / `mgmt` trùng IP role `guest` | CẢNH BÁO | Tách dải quản lý | OME + hypervisor |
| I03 | IP vừa đổi chủ | IP kết thúc gán với CI A và mở với CI B trong 7 ngày | thông tin | Dùng khi điều tra (firewall rule / DNS cũ còn trỏ) | — |
| I04 | Backup client trỏ sai máy | tên client NetBackup là IP, IP đó hiện thuộc VM khác VM đang được backup trước đây | THEO DÕI | Kiểm tra policy backup theo IP | NetBackup + hypervisor |
| I05 | VM không có IP | VM bật, Tools chạy, không báo IP nào | thông tin | Không ghép được backup / ITSM theo IP | hypervisor |
| I06 | IP ngoài vùng khai báo | IP thuộc vùng `default` khi đã khai ≥ 1 vùng | thông tin | Bổ sung vùng / IP lạ | — |
| I07 | Không ghép được vì mơ hồ | một khoá yếu (IP / hostname) khớp > 1 đối tượng | THEO DÕI | Khai `shared_ips` hoặc sửa tên | — |

### 7.3 Theo chuỗi liên kết (dùng lại sổ CI)

| Mã | Phát hiện | Điều kiện | Mức |
|---|---|---|---|
| X01 | Máy hỏng chạy hệ thống quan trọng | `physical_server` mức VƯỢT NGƯỠNG (G4c) và có VM hệ thống mức 1–2 đang bật | VƯỢT NGƯỠNG |
| X02 | Hệ thống quan trọng dồn một máy | ≥ 50 % VM bật của một hệ thống mức 1–2 nằm trên một máy vật lý | THEO DÕI |
| X03 | Thiết bị không thuộc owner nào | máy vật lý chạy VM nhưng không VM nào thuộc hệ thống có owner | THEO DÕI |

Mỗi quy tắc là một hàm thuần `rule(ctx) -> [Finding]` trên dữ liệu đã nạp sẵn. Test với số liệu cụ thể (quy tắc 9), bật / tắt
và chỉnh ngưỡng ở **Cài đặt → Đối soát** (giống Cài đặt → Cảnh báo).

## 8. Màn hình chung

### 8.1 Hồ sơ thiết bị (mở rộng thẻ Máy chủ vật lý của G1c / G4c)

Mở từ Ctrl + K bằng **serial / service tag, asset tag, IP, tên host / VM / OME**. IP → máy đang giữ IP; nhiều kết quả →
danh sách chọn.

```
┌ Máy chủ vật lý · 7FHKZ53 ─────────────────────────────── [⊗ VƯỢT NGƯỠNG] ── [iDRAC] [PDF] ┐
│ PowerEdge R750 · esx-prod-a01 · DC1 tủ A12 · đưa vào dùng 03/2023 · 3 năm 8 tháng          │
│ Nguồn thấy: [● vCenter vc1 5 phút] [● OME 5 phút] [● DC asset] [● Opex OPX-2026-0012] [— Storage]│
├────────────────────────────────────────────────────────────────────────────────────────────┤
│ Đối soát (3)   S04 Lệch model: DC asset ghi R740, OME báo R750           [Bỏ qua…] [Đã xử lý]│
│                S14 Ổ Disk 1 (SN WBN0ABCD) dự báo hỏng — còn bảo hành Dell đến 01/03/2028     │
│                I01 IP 10.10.5.21 cũng đang ở VM app-test-07                                  │
├ Định danh & khoá ──────────┬ Phần cứng (OME) ───────────┬ Ảo hoá ──────────────────────────────┤
│ Serial   7FHKZ53  vCenter·OME·DC·Opex│ Trạng thái, phân hệ,    │ Host esx-prod-a01 · cluster A        │
│ Asset tag HSC-SRV-0123   DC asset    │ ổ đĩa, PSU, RAM,        │ 12 VM (10 bật) · hệ thống:           │
│ iDRAC    10.30.10.11  OME   từ 01/25 │ firmware vs baseline    │ SYS-0001 Core Banking (mức 1)        │
│ MGMT     10.10.0.21   vCenter        │                         │ SYS-0007 Web (mức 3)                 │
│ MAC      b0:7b:25:…   OME            │                         │                                      │
├ Mạng / IP ─────────────────┴───────────────────────────┴──────────────────────────────────────┤
│ IP hiện tại theo vai trò; lịch sử 13 tháng (ai giữ trước / sau)                                │
├ Tài sản & tài chính (theo quyền Chi phí) ┬ Bảo hành ───────────────┬ Sự kiện (G5) ────────────┤
│ DC asset, Opex, Capex, hợp đồng, đợt TT  │ Dell vs Opex, mã đối soát │ alert, sự cố, thay đổi   │
├ Tác động ──────────────────────────────────────────────────────────────────────────────────────┤
│ Nếu máy lỗi: 1 host, 12 VM, 2 hệ thống (1 mức 1), 3 backup client                               │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Định danh & khoá**: mỗi khoá ghi rõ **nguồn nào báo, lần thấy cuối, mạnh / yếu** (`serial_index` + `ci_key` +
  `ip_binding`). Đây là phần "deep dive" để người xem tự kiểm tra vì sao các dữ liệu được ghép với nhau.
- **Chip "Nguồn thấy"**: dùng trạng thái của `source_status` (nguồn lỗi hiện chấm đỏ — dữ liệu có thể cũ).
- **Khổ 390 px**: các khối xếp dọc theo thứ tự Đối soát → Định danh → Phần cứng → Ảo hoá → Mạng → Tài sản → Bảo hành → Sự kiện
  → Tác động. Bảng dài cuộn trong vùng riêng.

### 8.2 Đối soát dữ liệu (Quản lý thông tin → Đối soát)

```
 KPI: Thiết bị 312 · Đủ 3 mảnh (đang chạy + sổ tài sản + bảo hành) 71 % · Phát hiện mở 48 (6 VƯỢT NGƯỠNG) · IP trùng 3
 Lọc: [Quy tắc ▾] [Mức ▾] [Nguồn thiếu ▾] [Owner (bộ lọc chung)] [Tìm serial / IP / tên]

 Serial      Tên / model            vCenter  OME  Storage  Sổ DC  Opex   Bảo hành Dell   Phát hiện
 7FHKZ53     esx-prod-a01 R750        ●      ●      —       ≠       ●     01/03/2028      S04 · S14
 9QWXY63     esx-prod-b01 R740        ●      ●      —       —       ●     đã hết          S01 · S11
 CZJ8xxxx    dr-esx02 DL380           ●      —      —       ●       —     —               S06?
 4BMDB21     db-oracle-01 R760        —      ●      —       ●       —     2028            —
```

`●` có, `—` không có, `≠` có nhưng lệch. Mỗi dòng mở Hồ sơ thiết bị. Tab thứ hai liệt kê phát hiện (mã, quy tắc, mức, chủ
thể, từ ngày, trạng thái) và có thao tác bỏ qua / đã xử lý.

### 8.3 Tra IP (Vận hành → Phần cứng → Tra IP, hoặc Ctrl + K gõ IP)

Nhập IP (hoặc dải `/24`) → ai đang giữ (vai trò, nguồn, từ khi nào), lịch sử gán, xung đột, vùng mạng. Thêm mốc thời gian
"tại thời điểm" để trả lời câu hỏi điều tra sự cố.

### 8.4 Báo cáo

- **PDF "Đối soát dữ liệu hạ tầng"** (HSC, `pdf-report`): tổng kết độ phủ, phát hiện theo quy tắc, chi tiết mức VƯỢT
  NGƯỠNG / CẢNH BÁO.
- **Excel**: ma trận, phát hiện, lịch sử IP theo bộ lọc.

Cả hai đăng ký `exports.py`, số lấy từ `correlation.service` như màn hình.

## 9. API (mọi endpoint `get_current_user` trừ khi ghi chú)

| Phương thức | Đường dẫn | Ghi chú |
|---|---|---|
| GET | `/api/correlation/devices` | ma trận + KPI; lọc `rule`, `level`, `missing=<family>`, owner (G2c) |
| GET | `/api/correlation/devices/{serial}` | Hồ sơ thiết bị (thay / mở rộng `/api/entities/server/{serial}`) |
| GET | `/api/correlation/findings` | danh sách phát hiện có lọc |
| POST | `/api/correlation/findings/{code}/ignore` | `require_owner_admin`, lý do + hạn |
| POST | `/api/correlation/findings/{code}/resolve` | `require_owner_admin` |
| GET | `/api/correlation/ip/{ip}?at=` | ai giữ IP (hiện tại / tại thời điểm), lịch sử |
| GET / POST / DELETE | `/api/correlation/aliases` | ghi: `require_admin` |
| GET / PUT | `/api/correlation/scopes` | ghi: `require_admin` |
| GET / PUT | `/api/correlation/rules` | bật / tắt, ngưỡng — `require_admin` |
| GET | `/api/correlation/report.pdf`, `.xlsx` | theo bộ lọc |

Ctrl + K (`/api/search`) thêm nhóm "IP" và tìm serial linh kiện (từ `serial_index`, `scope=component` → mở máy chứa).

## 10. Hiệu năng và quy mô

- **Ước lượng**: 1 000 máy vật lý, 15 000 VM, 30 000 IP, 20 000 serial linh kiện.
  - Dựng `serial_index` + `ip_binding` trong bộ nhớ: < 2 giây;
  - ghi chênh lệch: vài câu `INSERT … / UPDATE … WHERE id IN (…)` theo lô 1 000.
- **Không truy vấn theo từng thiết bị trong vòng lặp** (quy tắc 8): mỗi bảng nguồn đọc một lần, quy tắc chạy trên dict.
- **Màn hình**:
  - ma trận phân trang phía server (500 dòng / trang);
  - Hồ sơ thiết bị ≤ 10 truy vấn có index;
  - Tra IP dùng index (`ip`, `scope`, `ended_at`).
- **Chỉ số theo dõi**: `metrics.record("correlation", …)` cho thời gian dựng, số phát hiện mở, số IP trùng — có trong dòng
  log `job=poll`.

## 11. Bảo mật và phân quyền

- **Chi phí:** mã / tên Opex, Capex, hợp đồng chỉ trả cho người có quyền Chi phí (`_spend_ok`), như G4b / G4c. Người
  khác thấy trạng thái và ngày.
- **Owner:**
  - bộ lọc owner (G2c) áp cho ma trận và phát hiện qua VM trên máy;
  - tài khoản phạm vi Owner chỉ thấy máy chạy VM của hệ thống mình, không có thao tác ghi.
- **Escape:** mọi chuỗi từ nguồn (tên, model, thông điệp) được escape trên web và PDF (quy tắc 7).
- **Che dữ liệu khi gửi ra ngoài:** MCP / AI che IP và tên host theo `MCP_MASK_HOSTS` / chính sách che tên của `ai_platform`.
- **Không dò mạng:** ICO không phân giải DNS ra ngoài, không ping hay quét. IP chỉ lấy từ nguồn đã khai.
- **Audit:** bí danh, vùng IP, bỏ qua phát hiện ghi `audit_log`.

## 12. Thu thêm từ nguồn — xác nhận vẫn chỉ đọc

Các bổ sung ở mục 6.1 dùng **GET / PropertyCollector** trong phiên sẵn có. **Không** cần ngoại lệ ghi mới, không đổi quy
tắc 1. Riêng vSphere Tags (G2d) vẫn là ngoại lệ phiên riêng, không thuộc thiết kế này.

## 13. Lộ trình (mỗi bước một PR, sau G5 hoặc song song nếu chủ sản phẩm muốn)

| Bước | Nội dung | Nghiệm thu |
|---|---|---|
| C1 | Chuẩn hoá (giá trị rác, IP bỏ qua, MAC ảo); thu thêm khoá: VM mọi IP / MAC, host vmk, serial NetApp / Pure, OME ship date; demo + test | DEMO: mọi host / mảng có serial; VM có ≥ 1 IP mỗi NIC |
| C2 | `serial_alias`, `serial_index`, `ip_scope`, `ip_binding` + dựng ở leader; CI `storage_controller`; retention | Hai lần dựng không đổi; IP đổi chủ có lịch sử; nguồn lỗi không đóng gán |
| C3 | Bộ máy đối soát + quy tắc S01–S09, S14–S15, I01–I07, X01–X03; chuyển 4 ca bảo hành G4b vào; hộp việc nguồn `xcheck`, `alert_rules`; Cài đặt → Đối soát | Test số liệu cụ thể từng quy tắc; DEMO có ≥ 1 phát hiện mỗi nhóm |
| C4 | Hồ sơ thiết bị (mở rộng thẻ máy chủ), Ctrl + K theo IP / serial linh kiện | `ui_audit` 1440 + 390 px |
| C5 | Trang Đối soát (ma trận + phát hiện), Tra IP, PDF / Excel | PDF đúng số với trang |
| C6 | Nối kho sự kiện G5 (mở / đóng phát hiện, IP đổi chủ thành sự kiện; dòng thời gian trên Hồ sơ thiết bị) | Dòng thời gian một máy đủ alert + đổi IP + phát hiện |

## 14. Cần chủ sản phẩm chốt

1. **Vùng mạng**: có dải IP trùng giữa các site / VRF không? Nếu có, cần danh sách CIDR theo vùng để khai `ip_scope` trước C2.
2. **IP dùng chung**: danh sách VIP / cluster IP / NAT được phép trùng (tránh báo I01 sai).
3. **Ngưỡng**: S02 (không thấy chạy) 30 ngày? S09 (lệch ngày) 90 ngày? Thời gian giữ lịch sử IP 13 tháng?
4. **Ai được bỏ qua phát hiện**: admin + owner admin (đề xuất) hay thêm vai trò Chi phí cho các quy tắc liên quan Opex (S06, S07)?
5. **Thứ tự**: làm C1–C3 trước G5 (để G5 ghi sự kiện đối soát ngay) hay sau G5 (đề xuất: **sau G5**, C6 gộp vào khi đã có kho sự kiện)?
6. **Nguồn IPAM**: có file / hệ thống IPAM để nhập (Excel) không — dùng làm "IP khai báo" so với IP thực tế?

## 15. Kiểm thử

- **Hàm thuần**: chuẩn hoá serial / IP / MAC; gán vùng CIDR (chồng nhau, ưu tiên); từng quy tắc với dữ liệu nhỏ có số cụ thể;
  mã đối soát ổn định.
- **Dựng chỉ mục**:
  - hai lần liên tiếp không ghi gì;
  - IP chuyển từ VM A sang VM B: gán A đóng sau thời gian chờ, B mở, I03 xuất hiện;
  - nguồn lỗi: gán của nguồn đó giữ nguyên.
- **Quyền**: người không có quyền Chi phí không thấy mã Opex; phạm vi Owner chỉ thấy máy của mình; route mới qua
  `test_every_api_route_requires_full_scope_unless_allowlisted`.
- **DEMO**: dữ liệu demo cố ý có S01, S03, S05, I01, S14 để nghiệm thu màn hình.
