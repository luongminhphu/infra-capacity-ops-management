# Thiết kế module "Chi phí & Hợp đồng IT" (`spend`)

Trạng thái: **ĐÃ DUYỆT** 14/11/2026 (câu 1–4, 6 theo đề xuất; câu 5 đổi: mẫu tờ trình tải lên tự do, xem §1.8).

Tài liệu này theo quy trình 6 bước (ngữ cảnh → dữ liệu → lỗi → phân quyền → idempotence/đồng thời →
quan sát) và kỷ luật mô hình dữ liệu (bất biến nằm trong schema). Code, tên bảng/cột viết tiếng Anh;
giao diện, báo cáo, tin nhắn viết tiếng Việt (CLAUDE.md quy tắc 5).

---

## 0. Ngữ cảnh và các quyết định nền

| Thành phần | Loại | Ghi chú |
|---|---|---|
| Opex, Capex, Hợp đồng, Tờ trình, Cài đặt | HTTP endpoint (FastAPI, cookie JWT) | Người dùng thao tác qua UI, không có client tự retry. |
| Nhắc hạn + xoá file version cũ | Cron trong tiến trình app | Task asyncio riêng cạnh `scheduler_loop`, chạy 1 lần/ngày. |
| SMTP, Discord webhook, AI provider | Outbound integration | Gọi ra ngoài: timeout, không theo redirect, không log secret. |
| Báo cáo PDF / Excel | Endpoint đồng bộ | Cùng một hàm tính số liệu cho web, PDF, Excel. |

Ràng buộc từ hạ tầng hiện có:

- **1 pod, strategy `Recreate`** (`k8s/04-app.yaml`) → không có hai scheduler chạy song song. Vẫn
  thiết kế idempotent bằng ràng buộc UNIQUE để an toàn nếu sau này tăng replicas.
- **Không Alembic**: bảng mới tạo bằng `create_all()`; cột thêm vào bảng cũ (`users`) qua `additive`
  trong `ensure_schema()`.
- **Quy ước codebase**: khoá chính `Integer`, thời điểm `DateTime` naive UTC (`utcnow()`), hiển thị qua
  `timeutil.to_iso_utc` / `to_vn_str`. Module mới **giữ quy ước này** thay vì UUIDv7/timestamptz:
  ứng dụng nội bộ, một tenant, mọi API đều qua đăng nhập + phân quyền; định danh nghiệp vụ hiển thị
  ra ngoài là **mã** (`OPX-2026-0001`), không phải id. Ngày nghiệp vụ (bắt đầu, hết hạn) dùng `Date`.
- **Tiền**: VND không có phần lẻ → `BigInteger` đơn vị đồng. Không dùng float ở bất kỳ đâu trong
  phép tính tiền; tỷ lệ % dùng `Numeric(5,2)`, làm tròn `ROUND_HALF_UP` về đồng.
- **Không thêm hạ tầng mới**: không queue, không object storage. File lưu trên volume (PVC).
  Phụ thuộc mới duy nhất: `python-docx` (GĐ4–5, đọc/ghi .docx). SMTP dùng `smtplib`, Discord và AI
  dùng `urllib` (giống NetBackup client).

---

## 1. Mô hình dữ liệu

Ký hiệu: **NN** = NOT NULL, **UQ** = UNIQUE, **CK** = CHECK, **FK→x (hành vi)**.
Mọi bảng nghiệp vụ có `created_at, created_by, updated_at, updated_by` (NN trừ `updated_*`).

### 1.1 Danh mục (GĐ1)

**`spend_categories`** — loại chi phí, cấu hình được.
| Cột | Kiểu | Ràng buộc |
|---|---|---|
| id | int PK | |
| code | varchar(32) | NN, UQ (`HW_WARRANTY`, `LIC_FIREWALL`…) — không đổi sau khi tạo |
| name | varchar(128) | NN |
| grp | varchar(16) | NN, CK ∈ `hw_warranty, license, dc_maintenance, outsourced, other` |
| renewal_pct | numeric(5,2) | NULL = không tự sinh gia hạn; CK 0 ≤ x ≤ 100 |
| active | bool | NN default true (danh mục đã dùng không xoá, chỉ tắt) |
| sort_order | int | NN default 0 |

Seed mặc định: Bảo hành máy chủ (15%), Bảo hành storage (15%), Bảo hành thiết bị mạng (15%),
License firewall (20%), License VMware (20%), License backup (20%), Bảo trì DC, Dịch vụ thuê ngoài, Khác.

**`spend_vendors`**: id, name NN, name_norm NN UQ (chữ thường, bỏ dấu, gộp khoảng trắng — chống trùng
"Dell VN" / "dell  vn"), tax_code NULL, active. Lý do tách bảng: báo cáo "top nhà cung cấp" cần
khoá ổn định, không group theo chuỗi gõ tay.

**`spend_cost_centers`**: id, code NN UQ, name NN, active.

**`spend_sequences`**: (kind varchar(8), year int) PK, last_no int NN. Sinh mã bằng
`UPDATE … SET last_no = last_no + 1 WHERE kind=:k AND year=:y` trong cùng transaction với INSERT
(PostgreSQL khoá dòng; SQLite khoá DB). Mã thực thể còn có UQ riêng nên trùng là lỗi cứng, không âm thầm.

### 1.2 Opex (GĐ1)

**`opex_items`**
| Cột | Kiểu | Ràng buộc / ý nghĩa |
|---|---|---|
| id | int PK | |
| code | varchar(20) | NN, UQ, `OPX-<năm tài chính>-<4 số>`. **Bất biến** — sửa năm tài chính sau này không đổi mã |
| fiscal_year | int | NN, CK 2000–2100 |
| category_id | FK→spend_categories (RESTRICT) | NN, index |
| title | varchar(255) | NN — nội dung dịch vụ |
| description | text | NULL |
| vendor_id | FK→spend_vendors (RESTRICT) | NN, index |
| contract_ref, po_ref | varchar(64) | NULL — mã HĐ / PO bên ngoài (chuỗi tự do, không phải FK) |
| start_date, end_date | date | NN, CK end_date ≥ start_date |
| billing_cycle | varchar(12) | NN, CK ∈ `one_time, monthly, quarterly, yearly` |
| amount_net | bigint | NN, CK ≥ 0 — trước VAT |
| vat_pct | numeric(5,2) | NN default 10, CK 0–100 |
| amount_gross | bigint | NN, CK ≥ amount_net — sau VAT; mặc định = round(net × (1+vat)), sửa tay được (hoá đơn làm tròn khác) |
| cost_center_id | FK→spend_cost_centers (RESTRICT) | NULL, index |
| owner_name | varchar(128) | NN — người phụ trách (có thể không phải user app) |
| owner_email | varchar(255) | NULL — tuỳ chọn nhận email nhắc |
| status | varchar(12) | NN, CK ∈ `planned, active, renewed, stopped` |
| origin | varchar(8) | NN, CK ∈ `manual, capex, renewal` |
| capex_project_id | FK→capex_projects (SET NULL) | NULL, index (GĐ2) |
| capex_item_id | FK→capex_items (SET NULL) | NULL; **UQ một phần** `WHERE origin='capex'` → mỗi hạng mục sinh tối đa 1 Opex |
| amount_mode | varchar(8) | NN default `manual`, CK ∈ `auto, manual` (auto = tính theo % hạng mục Capex) |
| confirmed_at, confirmed_by | datetime / varchar | NULL; CK `origin <> 'capex' OR status <> 'planned' OR confirmed_at IS NULL` |
| renewed_from_id | FK→opex_items (SET NULL) | NULL, **UQ** → một khoản chỉ có tối đa một khoản kế nhiệm |
| row_version | int | NN default 1 — optimistic lock khi sửa (PUT phải gửi kèm, lệch → 409) |

Index: `(fiscal_year, status)`, `(status, end_date)` cho job nhắc hạn, FK đã nêu.

**Trạng thái** (lưu): Dự kiến `planned` → Đang hiệu lực `active` → Đã gia hạn `renewed` | Ngừng `stopped`.
**"Sắp hết hạn" KHÔNG lưu** — là trạng thái suy ra: `status='active' AND end_date - hôm_nay ≤ mốc nhắc lớn nhất`.
Lưu nó sẽ cần job cập nhật và sẽ sai ngay khi job lỡ một ngày. UI và báo cáo hiển thị 5 nhãn, DB lưu 4.

Chuyển trạng thái chỉ qua lệnh, không qua PUT chung (chống mass-assignment):

| Lệnh | Từ → đến | Ai |
|---|---|---|
| `confirm` | planned → active (ghi confirmed_*) | Duyệt |
| `renew` | active → renewed, **đồng thời tạo khoản kế nhiệm** (origin=renewal, copy trường, ngày mới, năm tài chính mới, status planned hoặc active) | Biên tập |
| `stop` | planned/active → stopped (kèm lý do) | Biên tập |
| `reactivate` | stopped → active | Duyệt |

`renewed` và nhắc hạn: job chỉ nhắc `status='active'` → gia hạn xong tự dừng nhắc.

**`opex_assets`** — thiết bị liên quan: id, opex_id FK CASCADE NN, serial varchar(128) NN, description,
vcenter_key varchar(64) NULL, host_name varchar(255) NULL; index (opex_id), (serial).
Liên kết host vCenter là **tham chiếu mềm** (vcenter_key + host_name): host chỉ tồn tại trong
`host_snapshots` (lịch sử), không có bảng host ổn định để làm FK. UI gợi ý host từ snapshot mới nhất
và đánh dấu "không còn thấy trong vCenter" khi host biến mất. vCenter vẫn chỉ đọc.

### 1.3 File (GĐ1, dùng lại ở GĐ4–5)

**`spend_files`**: id, storage_key varchar(64) NN UQ (tên file trên đĩa = uuid4 ngẫu nhiên, không bao giờ
dùng tên người dùng), sha256 char(64) NN, size_bytes bigint NN CK > 0, mime varchar(80) NN CK ∈ {docx, pdf},
original_name varchar(255) NN, uploaded_by, uploaded_at, purged_at NULL, purged_by NULL, purge_reason NULL.

Đây là ngoại lệ có chủ đích của "không soft delete": khi xoá file vật lý, **dòng metadata được giữ**
(kiểm toán yêu cầu), trạng thái thể hiện bằng `purged_at`. Không có truy vấn nghiệp vụ nào "lọc bỏ"
dòng này — chỉ nút tải xuống trả 410.

Bảng liên kết riêng thay vì polymorphic `entity_type/entity_id`:
`opex_attachments(opex_id FK CASCADE, file_id FK RESTRICT, PK cả hai)`,
`capex_attachments(capex_project_id, file_id)` (GĐ2).

### 1.4 Nhắc hạn (GĐ1)

**`spend_notify_channels`**: id, kind CK ∈ `email, discord`, name NN, enabled NN,
webhook_enc text NULL (Discord URL mã hoá `secrets_box`), recipients text NULL (email, phân cách dấu phẩy),
include_owner bool NN default false (email: gửi thêm cho `owner_email` của khoản),
CK `(kind='discord' AND webhook_enc IS NOT NULL) OR (kind='email' AND (recipients IS NOT NULL OR include_owner))`.
API chỉ trả `hasWebhook`, không trả URL.

**`spend_smtp_config`** (đúng 1 dòng, CK id = 1): host, port, security CK ∈ `starttls, ssl, none`,
username, password_enc, from_addr, updated_by/at. Không để trong `app_settings` vì cột value 255 ký tự
không đủ chắc cho chuỗi mã hoá.

**`spend_reminder_log`**: id, opex_id FK CASCADE NN, end_date date NN, threshold_days int NN,
channel_id FK CASCADE NN, status CK ∈ `pending, sent, failed, skipped, unknown`, attempts int NN,
last_error varchar(500) NULL (đã lọc, không chứa URL/mật khẩu), created_at, sent_at.
**UQ (opex_id, end_date, threshold_days, channel_id)** — đây là chỗ đảm bảo "mỗi mốc chỉ gửi một lần".
Có `end_date` trong khoá: nếu ngày hết hạn bị sửa (gia hạn tại chỗ, nhập sai), chu kỳ nhắc mới bắt đầu đúng.

Cấu hình đơn giản trong `app_settings`: `spend.fiscal_start_month` (1–12, mặc định 1),
`spend.reminder_days` (`90,60,30`), `spend.reminder_hour` (giờ VN, mặc định 8),
`spend.app_base_url` (link trong tin nhắn), `spend.purge_days` (30).

### 1.5 Capex (GĐ2)

**`capex_projects`**: id, code `CPX-<năm>-<số>` NN UQ, fiscal_year NN, name NN, description, cost_center_id,
owner_name, status CK ∈ `draft, approved, in_progress, closed`, go_live_date date NULL (ngày bắt đầu
bảo hành mặc định cho các hạng mục), row_version, audit.

**`capex_items`**: id, project_id FK CASCADE NN, category_id FK RESTRICT NN, description NN, vendor_id NULL,
quantity int NN default 1 CK > 0, amount_net bigint NN CK ≥ 0, vat_pct, amount_gross CK ≥ net,
start_date date NULL (NULL = lấy go_live_date của dự án), coverage_years int NN default 0 CK 0–10
(0 = không có bảo hành/support → không sinh Opex), renewal_pct numeric(5,2) NULL (ghi đè % của loại),
renewal_amount bigint NULL CK ≥ 0 (nhập tay, ưu tiên cao nhất), sort_order, updated_at.

### 1.6 Báo cáo (GĐ3)

**`spend_saved_filters`**: id, owner_username NN, name NN, filters_json text NN, shared bool NN,
UQ (owner_username, name). JSON hợp lý ở đây (bộ lọc là blob tự do, validate bằng Pydantic khi đọc/ghi).

### 1.7 Hợp đồng (GĐ4)

**`contracts`**
| Cột | Ràng buộc |
|---|---|
| code `HD-<năm>-<số>` | NN UQ |
| title, vendor_id | NN |
| opex_id FK→opex_items (RESTRICT), capex_project_id FK→capex_projects (RESTRICT) | **CK đúng một trong hai khác NULL** |
| proposal_id FK→proposals (SET NULL) | NULL |
| status | CK ∈ `draft, negotiating, final, signed` |
| current_version_no | int NN default 0 — khoá lạc quan cho upload |
| final_version_id FK→contract_versions, finalized_at/by | NULL; CK `status IN ('final','signed') ⇔ final_version_id IS NOT NULL` |
| signed_file_id FK→spend_files, signed_at/by | NULL; CK `status='signed' ⇔ signed_file_id IS NOT NULL` |
| purge_due_at, purge_cancelled_at/by, purge_done_at | NULL |

**`contract_versions`** (append-only, không UPDATE trừ khi purge): id, contract_id FK RESTRICT NN,
version_no int NN, **UQ (contract_id, version_no)**, editor_party CK ∈ `legal, finance, partner`,
file_id FK RESTRICT NN, text_content text NULL (văn bản trích từ .docx để so sánh), note, uploaded_by, uploaded_at.

Một nhánh duy nhất được bảo đảm hai lớp:
1. `UPDATE contracts SET current_version_no = :n+1 WHERE id=:id AND current_version_no = :n AND status IN ('draft','negotiating')`
   → rowcount 0 ⇒ **409** "Đã có version v{x} mới hơn — tải bản mới nhất rồi upload lại".
2. UQ (contract_id, version_no) chặn mọi đường vòng.

### 1.8 Tờ trình và AI (GĐ5)

- **`proposal_templates`**: id, kind CK ∈ `A, B`, name, description, file_id FK RESTRICT, outline_json text
  (cấu trúc trích từ .docx khi upload: danh sách tiêu đề / đoạn / bảng theo thứ tự, kèm style — dữ liệu suy ra,
  lưu cache để không phải đọc lại file mỗi lần), active, uploaded_by/at.
  **Mẫu là file .docx bất kỳ do người dùng tải lên** (Cài đặt → Mẫu tờ trình → Thêm mẫu), không bắt buộc
  placeholder. Khi soạn: AI nhận *outline* của mẫu + dữ liệu Opex/Capex, trả JSON nội dung cho từng phần
  (theo chỉ số phần trong outline); hệ thống ghi nội dung vào **bản sao của chính file mẫu**, giữ nguyên
  style, header/footer, logo. Mẫu A: phần thân ngắn (executive summary), chi tiết đưa vào phụ lục cuối file;
  Mẫu B: chi tiết, so sánh phương án, NCC, chi phí. Nếu mẫu có placeholder `{{...}}` thì vẫn được thay đúng
  chỗ (tuỳ chọn, không bắt buộc). Chế độ "Không dùng AI": điền các placeholder nếu có và chèn bảng dữ liệu
  (mã, dịch vụ, NCC, thời hạn, số tiền) vào cuối văn bản để người soạn tự viết phần lời.
- **`proposal_recipients`**: id, kind A/B, title (chức danh) NN, person_name NULL, sort_order, active.
- **`proposals`**: id, code `TT-<năm>-<số>` UQ, kind, template_id FK RESTRICT, title, status CK ∈
  `draft, submitted, approved, rejected`, capex_project_id FK RESTRICT NULL, file_id FK NULL (bản .docx sinh ra),
  ai_provider_used varchar(16) NN (`none` nếu không dùng AI), created_by/at.
- **`proposal_opex`** (proposal_id, opex_id) PK. Bất biến "một dự án Capex HOẶC ≥1 Opex" là liên bảng →
  kiểm tra ở service trong cùng transaction (DB không biểu diễn được gọn), có test.
- **`spend_ai_config`** (1 dòng): provider CK ∈ `none, anthropic, azure_openai`, model, endpoint,
  api_key_enc, deployment (Azure), **allow_external bool NN default false**, updated_by/at.

### 1.9 Phân quyền

Thêm vào `users` (qua `additive`): `spend_role VARCHAR(16) NOT NULL DEFAULT 'none'`
(giá trị ∈ `none, viewer, editor, approver`, kiểm tra ở Pydantic + CK ở bảng mới không áp được cho ALTER SQLite một cách chắc chắn).

### 1.10 Audit

Dùng lại bảng `audit_log`, action `spend.*`, `detail` JSON có `before/after` cho các trường tiền,
ngày, trạng thái. Ghi trong **cùng transaction** với thay đổi (không mất audit khi commit thành công).

---

## 2. Logic tính toán chính

### 2.1 Năm tài chính

```
fiscal_year(d, start_month) = d.year      nếu d.month >= start_month
                            = d.year - 1  nếu ngược lại
```
Năm tài chính được **đặt tên theo năm bắt đầu** (start_month=4: 01/04/2026–31/03/2027 = "NTC 2026").
Với start_month=1 trùng năm dương lịch. `fiscal_year` được lưu trên dòng (gợi ý tự động từ ngày bắt đầu,
sửa được) → đổi `start_month` sau này không làm dịch chuyển dữ liệu cũ một cách âm thầm.

### 2.2 Capex → Opex dự kiến

Với mỗi hạng mục có `coverage_years = N > 0`:
```
start        = item.start_date or project.go_live_date      (thiếu cả hai → chưa sinh, báo "thiếu ngày")
renewal_date = add_years(start, N)        # 29/02 + N năm, năm đích không nhuận → 28/02
opex.start   = renewal_date
opex.end     = add_years(renewal_date, 1) - 1 ngày
opex.fiscal_year = fiscal_year(renewal_date, start_month)
amount_net   = item.renewal_amount                         nếu nhập tay
             = round_half_up(item.amount_net × pct / 100)  pct = item.renewal_pct ?? category.renewal_pct
             (cả hai NULL → không sinh)
```
Ví dụ kiểm thử (start_month=1, trừ khi ghi khác):
| start | N | renewal_date | NTC |
|---|---|---|---|
| 15/06/2026 | 1 | 15/06/2027 | 2027 |
| 15/06/2026 | 3 | 15/06/2029 | 2029 (năm thứ 4) |
| 01/01/2026 | 5 | 01/01/2031 | 2031 |
| 29/02/2028 | 1 | 28/02/2029 | 2029 |
| 29/02/2028 | 4 | 29/02/2032 | 2032 |
| 31/03/2026, start_month=4 | 1 | 31/03/2027 | 2026 |
| 01/04/2026, start_month=4 | 1 | 01/04/2027 | 2027 |
| 1.234.567.891 đ × 15% | | | 185.185.184 đ (làm tròn nửa lên) |

**Đồng bộ khi sửa Capex** (cùng transaction với lưu Capex, hàm thuần `plan_capex_opex(project, items, cfg) → desired[]`
rồi `reconcile(existing, desired)`):
- Opex `origin='capex' AND status='planned' AND confirmed_at IS NULL` → cập nhật theo desired.
- Đã xác nhận (hoặc đã chuyển trạng thái) → **không đè**; UI gắn cờ "Capex đã đổi sau khi xác nhận"
  (suy ra: `capex_item.updated_at > opex.confirmed_at`), có nút xem chênh lệch.
- Hạng mục bị xoá / N về 0 → xoá Opex dự kiến chưa xác nhận nếu không có hợp đồng/tờ trình gắn vào;
  nếu có thì giữ lại, bỏ liên kết hạng mục, gắn cờ cần xử lý.

### 2.3 Chọn mốc nhắc (catch-up an toàn)

`days_left = end_date - hôm_nay_VN`. Mốc đến hạn = các mốc `t` có `days_left ≤ t`.
Chỉ gửi **mốc nhỏ nhất** trong đó; các mốc lớn hơn chưa gửi ghi `skipped`. Ví dụ app tắt 5 ngày,
bật lại còn 58 ngày → gửi mốc 60, ghi 90 là skipped (không dội 2 tin cùng lúc). Đã quá hạn (`days_left < 0`)
và vẫn `active` → không nhắc theo mốc, nhưng hiển thị đỏ "Quá hạn" trên dashboard.

---

## 3. Luồng nghiệp vụ

1. **Opex thủ công**: Biên tập tạo → `active` (hoặc `planned` nếu là kế hoạch năm sau) → job nhắc theo mốc →
   người phụ trách bấm **Gia hạn** → khoản cũ `renewed`, khoản mới sinh ra (mã mới, liên kết `renewed_from_id`).
2. **Capex**: Biên tập lập dự án + hạng mục → hệ thống sinh Opex `planned` cho năm tương lai →
   Duyệt bấm **Xác nhận** từng khoản (hoặc hàng loạt) → `active`.
3. **Báo cáo**: chọn bộ lọc (lưu được) → dashboard → xuất PDF (tóm tắt + quyết định cần duyệt) / Excel (đầy đủ dòng).
4. **Tờ trình**: chọn Opex/Capex + mẫu A/B → (AI soạn hoặc chỉ điền trường) → tải .docx chỉnh tiếp →
   lưu tờ trình gắn mã.
5. **Hợp đồng**: tạo hợp đồng gắn Opex/Capex (+ tờ trình) → upload v1 (Legal) → v2 (Đối tác) … →
   Duyệt bấm **Chốt Final** (chọn version) → `purge_due_at = now + 30 ngày` → upload bản scan → **Đã ký** →
   job xoá file các version không phải Final khi tới hạn (admin huỷ được trước hạn).

---

## 4. API (tiền tố `/api/spend`, router có `Depends(require_feature("spend"))`)

Quyền ghi ở cột cuối; mọi endpoint tối thiểu `viewer`.

| Method & path | Mô tả | Quyền |
|---|---|---|
| GET `/meta` | danh mục, NCC, cost center, năm tài chính hiện tại, quyền của tôi | viewer |
| POST/PUT `/categories`, `/vendors`, `/cost-centers` | quản lý danh mục | editor (categories: admin) |
| GET `/opex?fy=&status=&category=&vendor=&q=&expiringWithin=&page=&size=` | danh sách phân trang (size ≤ 200) | viewer |
| POST `/opex` · GET/PUT `/opex/{code}` (PUT gửi `rowVersion`) | tạo / xem / sửa | editor |
| POST `/opex/{code}/confirm` · `/renew` · `/stop` · `/reactivate` | lệnh trạng thái | xem §1.2 |
| DELETE `/opex/{code}` | chỉ khi không có liên kết hợp đồng/tờ trình/khoản kế nhiệm | admin |
| POST/DELETE `/opex/{code}/attachments` · GET `/files/{id}` | file đính kèm | editor / viewer |
| GET `/opex/{code}/history` | audit của khoản | viewer |
| GET/PUT `/settings` | năm TC, mốc nhắc, giờ chạy, base URL, thời gian chờ xoá | GET viewer, PUT admin |
| GET/PUT `/notify/smtp` · CRUD `/notify/channels` · POST `/notify/channels/{id}/test` | kênh nhắc | admin |
| GET `/notify/log?status=` · POST `/notify/run` (chạy ngay, idempotent) | theo dõi job | admin |
| CRUD `/capex`, `/capex/{code}/items` · GET `/capex/{code}/opex-preview` | Capex + xem trước Opex sinh ra | editor |
| POST `/capex/{code}/opex/confirm` (danh sách mã) | xác nhận hàng loạt | approver |
| POST `/reports/budget` (body = bộ lọc) → JSON · `/reports/budget.pdf` · `/reports/budget.xlsx` | báo cáo | viewer |
| CRUD `/saved-filters` | bộ lọc của tôi (+ dùng chung) | viewer (chỉ sửa của mình) |
| CRUD `/contracts` · POST `/contracts/{code}/versions` (multipart: file, editorParty, note, **baseVersion**) | hợp đồng | editor |
| GET `/contracts/{code}/diff?from=&to=` · `/timeline` | so sánh / lịch sử | viewer |
| POST `/contracts/{code}/finalize` · `/sign` (multipart PDF) | chốt / ký | approver |
| POST `/contracts/{code}/purge/cancel` | huỷ xoá tự động | admin |
| CRUD `/proposal-templates`, `/proposal-recipients` · GET/PUT `/ai` | mẫu, người nhận, AI | admin |
| POST `/proposals` (opexCodes[] hoặc capexCode, templateId) · GET `/proposals/{code}/docx` | soạn tờ trình | editor |

Lỗi trả theo mẫu hiện có (`HTTPException(detail="…tiếng Việt…")`): 400 dữ liệu sai, 403 thiếu quyền,
404 không có / module tắt, 409 xung đột version hoặc trạng thái không cho phép, 410 file đã bị xoá theo
chính sách, 413 file quá lớn, 415 sai định dạng, 502 lỗi gọi SMTP/Discord/AI (thông điệp đã lọc).

---

## 5. Màn hình

Nhóm sidebar mới **"Chi phí & Hợp đồng"** (feature `spend`; ẩn với user `spend_role='none'` không phải admin):

| Trang | GĐ | Nội dung |
|---|---|---|
| **Tổng quan chi phí** | 3 | KPI năm nay / năm sau, theo loại, sắp hết hạn theo quý, top NCC, Opex sinh từ Capex. |
| **Opex** | 1 | Bảng lọc nhanh (năm TC, trạng thái 5 nhãn, loại, NCC, cost center, hết hạn trong N ngày) + drawer chi tiết: thông tin, thiết bị, file, lịch sử nhắc, audit; nút Gia hạn / Ngừng / Xác nhận theo quyền. |
| **Capex** | 2 | Danh sách dự án; chi tiết có bảng hạng mục và khối "Opex sẽ phát sinh" (xem trước trước khi lưu). |
| **Hợp đồng** | 4 | Danh sách + chi tiết: timeline version (màu theo bên: Legal / Finance / Đối tác), so sánh 2 version, nút Chốt / Ký, đếm ngược xoá file. |
| **Tờ trình** | 5 | Chọn khoản → chọn mẫu A/B → xem trước nội dung → tải .docx. |

Cài đặt → tab mới **"Chi phí & nhắc hạn"**: năm tài chính, mốc nhắc, danh mục + % gia hạn, SMTP,
kênh Discord/email (bật/tắt, Gửi thử), nhật ký gửi; GĐ5 thêm AI, người nhận, mẫu tờ trình.
Admin → Người dùng: gán `spend_role` cho user local. Trung tâm báo cáo: nhóm chip mới "Chi phí & ngân sách".
Màu trạng thái hạn dùng đúng 4 mức HSC: còn > mốc lớn nhất = AN TOÀN, ≤ 90 = THEO DÕI, ≤ 30 = CẢNH BÁO,
quá hạn = VƯỢT NGƯỠNG (map theo cấu hình mốc).

---

## 6. Failure modes

| # | Tình huống | Phát hiện | Xử lý |
|---|---|---|---|
| F1 | App tắt đúng ngày mốc | `spend.reminder.last_date` < hôm nay | Vòng lặp kiểm 15 phút/lần, chạy bù; chọn mốc nhỏ nhất (§2.3) |
| F2 | SMTP/Discord lỗi hoặc timeout | log `failed`, `attempts` | Thử lại ở các vòng 15 phút kế tiếp, tối đa 5 lần/ngày; hiển thị trên Cài đặt |
| F3 | Pod chết giữa lúc gửi (đã gửi, chưa ghi `sent`) | dòng `pending` > 1 giờ | Chuyển `unknown`, **không gửi lại** (chọn at-most-once: nhắc trùng gây mất tin cậy hơn nhắc thiếu một mốc khi còn mốc sau) |
| F4 | Discord 429 rate limit | HTTP 429 + `retry_after` | Gom tin (digest), chờ theo `retry_after` tối đa 10 s, còn lại để vòng sau |
| F5 | Webhook/mật khẩu không giải mã được (đổi SECRETS_KEY) | `SecretUnreadable` | Kênh báo "cần nhập lại", không crash job |
| F6 | Hai người sửa cùng Opex | `row_version` lệch | 409, UI tải lại |
| F7 | Hai người upload version cùng lúc | UPDATE có điều kiện rowcount 0 | 409, file vừa ghi bị xoá khỏi đĩa |
| F8 | Ghi file thành công, transaction DB lỗi | file mồ côi | Ghi file vào `tmp/` → commit DB → rename; job dọn file mồ côi > 1 ngày |
| F9 | Đĩa đầy | `OSError ENOSPC` | 507 + thông báo; Cài đặt hiển thị dung lượng dùng/còn |
| F10 | File độc (docx chứa macro, zip bomb, PDF giả) | kiểm tra nội dung | 415/413 (xem §7) |
| F11 | Job xoá file chạy khi admin vừa huỷ | `purge_cancelled_at` | Job đọc lại dòng với điều kiện trong cùng transaction trước khi xoá |
| F12 | AI lỗi / trả nội dung sai cấu trúc | parse JSON thất bại | Fallback "Không dùng AI": vẫn điền trường dữ liệu, mục AI để "[Cần soạn]", báo người dùng |
| F13 | Đổi tháng bắt đầu năm tài chính | — | Không đổi dữ liệu đã lưu; chỉ ảnh hưởng gợi ý cho bản ghi mới + cảnh báo trên form |
| F14 | Sửa Capex khi Opex đã xác nhận | so `updated_at` | Không đè, gắn cờ (§2.2) |

---

## 7. Bảo mật và phân quyền

**Ma trận** (admin hiện có = toàn quyền):

| Hành động | Xem | Biên tập | Duyệt | Admin |
|---|---|---|---|---|
| Xem Opex/Capex/hợp đồng/báo cáo, tải file | ✓ | ✓ | ✓ | ✓ |
| Tạo/sửa Opex, Capex, upload version, soạn tờ trình | | ✓ | ✓ | ✓ |
| Xác nhận Opex dự kiến, Chốt Final, Đã ký, kích hoạt lại | | | ✓ | ✓ |
| Cấu hình kênh nhắc, SMTP, AI, mẫu, danh mục; xoá; huỷ xoá file | | | | ✓ |

- Kiểm tra quyền bằng dependency `require_spend_role("editor")` ở **endpoint**, không chỉ ẩn nút UI.
  Duyệt bao hàm Biên tập (thứ bậc none < viewer < editor < approver; admin vượt tất cả).
- Entra: biến mới `ENTRA_SPEND_VIEWER_ROLES` (mặc định `ICO.Spend.Viewer`), `…_EDITOR_ROLES`
  (`ICO.Spend.Editor`), `…_APPROVER_ROLES` (`ICO.Spend.Approver`); làm mới `spend_role` mỗi lần đăng nhập
  giống `is_admin`. User local: admin gán trên giao diện. Thêm các role này vào `ENTRA_ALLOWED_ROLES` nếu đang giới hạn.
- Không mass-assignment: schema Pydantic riêng cho tạo / sửa, không nhận `code, status, origin, confirmed_*`;
  trạng thái chỉ đổi qua lệnh.
- **Upload**: tối đa `SPEND_MAX_UPLOAD_MB` (mặc định 20), đọc theo stream và cắt khi vượt. `.pdf` phải bắt đầu
  `%PDF-`; `.docx` phải là ZIP hợp lệ có `[Content_Types].xml` + `word/document.xml`, **từ chối**
  `vbaProject.bin`/macro, giới hạn số entry (≤ 2.000) và tổng dung lượng giải nén (≤ 200 MB, chống zip bomb),
  từ chối đường dẫn `..`. Tên file người dùng chỉ lưu làm metadata; tải xuống trả
  `Content-Disposition: attachment; filename*=UTF-8''…`, `X-Content-Type-Options: nosniff`.
- **Discord**: chỉ nhận URL `https://discord.com|discordapp.com|ptb.discord.com|canary.discord.com/api/webhooks/…`
  (chống SSRF), không theo redirect, `allowed_mentions: {"parse": []}` và escape markdown của dữ liệu người dùng
  (chống `@everyone`, link giả).
- **Email**: header từ dữ liệu người dùng bỏ CR/LF; phần HTML escape bằng `esc()`; có phần text thuần.
- **AI**: không gọi ra ngoài khi `provider='none'` hoặc `allow_external=false` (kiểm tra ở lớp provider,
  không chỉ UI). Chỉ gửi trường cần cho tờ trình (không gửi serial, email, ghi chú nội bộ trừ khi bật).
  Nội dung người dùng nhập được đặt trong khối dữ liệu tách biệt của prompt; đầu ra AI chỉ đi vào .docx để
  người soạn duyệt lại, không bao giờ được thực thi hay render HTML không escape.
- PDF: mọi chuỗi qua `esc()`; không URL ngoài.
- Secret (webhook, SMTP password, AI key): `secrets_box.encrypt`, API trả `has*`, audit ghi "đã đổi"
  không ghi giá trị, log lỗi lọc URL webhook.

---

## 8. Idempotence và đồng thời

| Thao tác | Cơ chế |
|---|---|
| Nhắc hạn | UQ log (opex, end_date, mốc, kênh); INSERT `pending` → commit → gửi → cập nhật. Insert trùng = đã xử lý, bỏ qua |
| Chạy job thủ công (`/notify/run`) đồng thời với job nền | Cùng UQ trên; thêm `threading.Lock` trong tiến trình |
| Sinh Opex từ Capex | Hàm thuần + reconcile theo `capex_item_id` (UQ một phần) — chạy lại nhiều lần cho cùng kết quả |
| Sinh mã | `spend_sequences` UPDATE trong transaction + UQ mã |
| Sửa bản ghi | `row_version` |
| Upload version | UPDATE có điều kiện + UQ (contract_id, version_no) |
| Xoá file theo chính sách | Mỗi file: kiểm điều kiện → xoá file đĩa → `purged_at` + audit; xoá file đã mất trên đĩa coi như thành công |
| Gia hạn Opex | `renewed_from_id` UQ → bấm 2 lần không tạo 2 khoản kế nhiệm (lần 2 trả 409) |

---

## 9. Quan sát

- Log có cấu trúc: `vco.spend.reminder` một dòng tổng kết mỗi lần chạy (số khoản xét, sent/failed/skipped
  theo kênh, thời gian); `vco.spend.purge` từng file bị xoá (mã HĐ, version, sha256 — không nội dung).
  Đi vào `app_logs` qua `observability.py` sẵn có → MCP đọc được.
- Request API tự có latency/lỗi qua `TimingMiddleware`.
- Cài đặt → "Chi phí & nhắc hạn" hiển thị: lần chạy gần nhất, kết quả, số `failed/unknown`, dung lượng volume file.
- Health: `/api/health` không đổi (job hỏng không được làm pod bị restart); thêm cảnh báo trên trang Điều hành
  khi job nhắc không chạy > 2 ngày.

---

## 10. Hạ tầng

- `docker-compose.yml`: volume `ico_files:/data/files`; env `SPEND_FILES_DIR=/data/files`, `SPEND_MAX_UPLOAD_MB=20`.
- `k8s/`: file mới `05-files-pvc.yaml` (PVC `ico-files`, RWO, 10Gi, storageClass mặc định), mount vào
  `ico-app`; thêm vào `kustomization.yaml`. RWO hợp với `replicas: 1 + Recreate`. Cần đưa PVC vào kế hoạch backup
  (README bổ sung). Image phải ghi được `/data/files` (kiểm tra UID trong Dockerfile).
- Mạng: pod cần ra được SMTP relay nội bộ và `discord.com:443` (và endpoint AI nếu bật) — cần mở firewall/proxy.
- `requirements.txt`: thêm `python-docx` ở GĐ4.

---

## 11. Phân giai đoạn (mỗi GĐ một branch + PR, test, CI xanh, `/security-review` trước PR)

| GĐ | Phạm vi | Test bắt buộc (số liệu cụ thể) |
|---|---|---|
| **1. Opex + nhắc hạn** | feature `spend`; `spend_role` + Entra mapping; danh mục/NCC/cost center; sinh mã; Opex CRUD + lệnh trạng thái + thiết bị + file đính kèm (hạ tầng file, PVC); audit; Cài đặt năm TC; SMTP + kênh Discord/email + Gửi thử; job nhắc hằng ngày; trang Opex; README | `fiscal_year` các tháng biên; mã OPX liên tiếp & không trùng; chọn mốc (95/90/58/30/0/-1 ngày, catch-up, đổi end_date); nhắc đúng một lần khi chạy job 2 lần; gia hạn dừng nhắc; escape Discord/HTML; quyền 403 theo vai trò; secret không xuất hiện trong response; upload file sai định dạng/macro/quá lớn |
| **2. Capex + mapping** | Capex/hạng mục, xem trước + sinh Opex dự kiến, reconcile, xác nhận hàng loạt, trang Capex | Bảng §2.2 (1/3/5 năm, 29/02, biên NTC start_month=4), làm tròn tiền, sửa Capex không đè khoản đã xác nhận, xoá hạng mục |
| **3. Báo cáo** | hàm `build_budget_report(filters)` dùng chung; dashboard; PDF (helper `report_pdf.py`); Excel (openpyxl); bộ lọc lưu được; Trung tâm báo cáo | Cùng bộ dữ liệu → tổng web = tổng Excel = tổng PDF; so sánh năm nay/năm sau; nhóm theo quý hết hạn; top NCC |
| **4. Hợp đồng & version** | contracts/versions, upload với baseVersion, trích text, diff + tô màu theo bên, timeline, Final/Ký, job xoá file + huỷ, nhật ký xoá | 409 khi upload trên bản cũ; CK gắn đúng một Opex/Capex; chuyển trạng thái sai → 409; purge chỉ xoá version không phải Final, giữ metadata, tôn trọng huỷ; diff đánh dấu đúng version/bên |
| **5. AI tờ trình** | upload mẫu A/B (.docx tự do, trích outline), người nhận, provider (none/anthropic/azure_openai), sinh .docx, lưu tờ trình, gắn hợp đồng | Chế độ none điền đủ placeholder; provider giả lập trả JSON hỏng → fallback; `allow_external=false` → không có request ra ngoài; key không bị trả về |

Mỗi GĐ cũng cập nhật `README.md` (mục mới) và `CLAUDE.md` (bản đồ code).

---

## 12. Câu hỏi cần anh/chị quyết (đã có đề xuất mặc định)

1. **Tên năm tài chính**: đặt theo năm bắt đầu (04/2026–03/2027 = NTC 2026)? *Đề xuất: có.*
2. **Opex nhiều năm trả một lần** (vd. license 3 năm): ghi toàn bộ số tiền vào năm tài chính của ngày bắt đầu,
   không phân bổ? *Đề xuất: có (ngân sách theo dòng tiền chi); phân bổ để sau nếu Finance cần.*
3. **Quyền mặc định**: user chưa được gán vai trò Chi phí **không thấy** module (dữ liệu tài chính)?
   *Đề xuất: có.*
4. **Xoá version cũ**: xoá cả văn bản trích xuất (`text_content`), chỉ giữ hash + metadata → sau khi xoá
   không so sánh được với version cũ nữa? *Đề xuất: có — nếu giữ text thì thực chất chưa xoá nội dung.*
5. **Mẫu tờ trình**: ~~placeholder bắt buộc~~ → **đã quyết**: chức năng upload mẫu .docx tự do; AI đọc cấu
   trúc mẫu và điền nội dung theo đó (§1.8).
6. **Hình thức nhắc**: mỗi kênh nhận **một tin tổng hợp/ngày** liệt kê các khoản tới mốc (log vẫn theo
   từng khoản–mốc), thay vì mỗi khoản một tin? *Đề xuất: tổng hợp.*
