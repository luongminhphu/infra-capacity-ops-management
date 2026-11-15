# Thiết kế: ICO thành super app — sắp xếp lại cụm tính năng và nền tảng AI dùng chung

> Trạng thái: **ĐÃ CHỐT** (quyết định ở mục 9). Làm theo thứ tự SA1 → SA6.

---

## 0. Hiện trạng (rà soát toàn bộ backend + frontend)

| Chỉ số | Hiện tại |
|---|---|
| Mục menu | **30 mục / 8 nhóm** (Backup 7, Chi phí & Hợp đồng 8, License 3…) |
| Màn hình (`view-section`) | 13, nạp hết khi mở app, định tuyến bằng chuỗi `if / else` trong `renderActiveView()` |
| Trang con | 3 kiểu khác nhau: mục menu làm tab (License, Backup, Chi phí), `sim-tabbar` (2 trang What-if), trang dài một mạch (Cài đặt, Sizing) |
| Bộ lọc phạm vi | 4 hệ riêng: thanh "Phạm vi" trên cùng, checkbox cluster của License, quy tắc phạm vi Backup, phạm vi PDF ở Trung tâm báo cáo |
| Trang chi tiết đối tượng | **Không có.** Cùng một host xuất hiện ở 6 nơi (Capacity, Host & VM, ESXi & phần cứng, đánh giá license, thiết bị của Opex, cluster hiện có trong Sizing); cùng một VM ở 5 nơi — không có liên kết qua lại |
| Tìm kiếm chung / hộp thông báo / trang chủ tuỳ chọn | **Không có.** Cảnh báo chỉ hiện trên từng màn hình; kênh gửi duy nhất là nhắc hạn Opex |
| Cấu hình | Rải rác: ngưỡng backup nằm trong Backup, AI nằm trong Chi phí, còn lại một trang Cài đặt dài |
| Xuất file | Nút riêng từng module **và** Trung tâm báo cáo; 2 đường Excel (chung `/export/xlsx` và builder riêng); không lưu lịch sử báo cáo |
| Quy hoạch tài nguyên | Chỉ có form giả định + nút PDF — **kết quả không hiện trên màn hình** |
| AI | Chỉ trong Chi phí (`spend/ai.py`): tờ trình, đọc báo giá PDF, tóm tắt khác biệt. MCP chỉ đọc log/hiệu suất app |
| Văn bản "Tổng kết — vấn đề & quyết định" | Viết bằng luật cứng ở ~10 nơi: `report_pdf._management_brief`, `report_planning._summary_box`, `report_sizing._summary_box`, `sizing_advisory.findings` (~200 dòng luật), `report_license._summary_box`, `backup/analysis.analyze` (~330 dòng), `report_backup._summary`, `report_spend._summary`, `report_renovation._summary`… |

**Kết luận:** app đang được tổ chức **theo nguồn dữ liệu** (vCenter, NetBackup, Chi phí) chứ không theo **công việc** của người dùng.
- Người dùng phải tự nối các mảnh: VM này có backup chưa, host này hết bảo hành chưa, license đó gắn hợp đồng nào.
- Nội dung có nhiều biến số (tổng kết, khuyến nghị, phân tích lỗi, ánh xạ file lạ) đang được viết bằng luật cứng: khó bảo trì, đọc máy móc.

---

## 1. Nguyên tắc

1. **Tổ chức theo công việc, không theo nguồn.** Hai miền chính:
   - **Vận hành:** theo dõi sống, cảnh báo, việc lặp lại, quyết định ngắn hạn.
   - **Quản lý thông tin:** danh mục, hồ sơ, hợp đồng, tài liệu, quyết định đầu tư.
2. **Một đối tượng — một trang.** Host, VM, Nhà cung cấp, Dự án là "thẻ" gom dữ liệu từ mọi module. Mọi nơi nhắc tới đối tượng đều bấm được vào thẻ.
3. **Một khung trang cho mọi màn hình.** Tiêu đề, phạm vi, tab, nút *Xuất* và *AI* cùng vị trí. Bảng, bộ lọc, hộp thoại, trạng thái rỗng dùng chung một bộ thành phần.
4. **Việc cần làm đến với người dùng.** Hộp việc chung trên trang chủ, gom cảnh báo và việc chờ từ mọi module theo vai trò.
5. **AI là trợ lý có rào chắn.**
   - Số liệu luôn do code tính. AI chỉ viết, ánh xạ, phân loại, tóm tắt.
   - Kết quả theo JSON schema và được kiểm tra. Người dùng xác nhận trước khi dùng. Ghi nhật ký.
   - Có công tắc riêng từng tính năng. AI tắt thì quay về văn bản theo luật hiện có (không mất chức năng).
6. **Không phá link cũ.** `#/bk-jobs`, `#/spend-opex`… chuyển hướng sang địa chỉ mới.
7. **Giữ kỹ thuật hiện tại** (FastAPI, vanilla JS không build, HSC design system). Không thêm framework.

---

## 2. Kiến trúc thông tin mới

### 2.1 Menu: 30 mục → 14 mục, 2 miền + hệ thống

```
⌂  Trang chủ                      ← hộp việc + bản tin (mới)
──────────── VẬN HÀNH ────────────
◉  Capacity & Compliance          tab: Tổng quan · Cluster · Compliance · Xu hướng
◉  Backup                         tab: Tổng quan · Client & RPO · Job · Lịch · Độ phủ VM · Storage
◉  Lịch việc & nhắc hạn           (mới) Opex / bảo hành / license hết hạn, đợt thanh toán, việc chờ duyệt
◉  Mô phỏng & quy hoạch           tab: What-if capacity · Quy hoạch tài nguyên · What-if license
◉  Sizing máy chủ                 (bàn làm việc tài liệu tư vấn — giữ riêng)
──────── QUẢN LÝ THÔNG TIN ────────
▤  Tài sản                        tab: Host · VM · ESXi & phần cứng · Hệ điều hành   (+ thẻ đối tượng)
▤  License                        tab: Đánh giá · Quyền sử dụng · Quy tắc · Snapshot
▤  Ngân sách & chi phí            tab: Tổng quan ngân sách · Opex · Capex
▤  Mua sắm                        tab: Nhà cung cấp · So sánh báo giá · Cải tạo văn phòng
▤  Hợp đồng & thanh toán          tab: Hợp đồng · Đợt thanh toán (mới: danh sách mọi đợt)
▤  Tài liệu                       tab: Tờ trình · Báo cáo (Trung tâm báo cáo + lưu trữ)
──────────── HỆ THỐNG ────────────
⚙  Cài đặt                        tab: Chung · Ngưỡng · Nguồn dữ liệu · Tính năng · Nhắc hạn & kênh · AI · MCP · Thương hiệu
👤 Người dùng & quyền             (admin)
```

- Trang **Điều hành** hiện tại trở thành chế độ "Lãnh đạo" của **Trang chủ**, không còn là mục riêng.
- Các trang con của License, Backup, Chi phí chuyển thành **tab trong trang**. Địa chỉ dạng `#/backup/jobs`, `#/procure/quotes/BG-2026-0001`.
- Sizing vẫn ở Vận hành vì dùng số liệu cluster sống, nhưng mỗi phương án Sizing gửi sang *Ngân sách → Capex nháp* được (mục 2.3).

### 2.2 Trang chủ — "Hôm nay cần làm"

```
┌ Bản tin (AI hoặc luật) ──────────────────────────────────────────────┐
│ "Cluster PRD-01 RAM 87% — cần 1 node trước Q2. 3 job backup lỗi liên │
│  tiếp ở client SQL-02. 2 hợp đồng bảo hành hết hạn trong 30 ngày."   │
└──────────────────────────────────────────────────────────────────────┘
[Capacity ●CẢNH BÁO] [Backup ●THEO DÕI] [License ●AN TOÀN] [Chi phí: 4 việc]   ← thẻ miền
┌ Hộp việc (lọc: của tôi · tất cả · đã xử lý) ──────────────────────────┐
│ ● VƯỢT NGƯỠNG  Cluster PRD-01 RAM vượt ngưỡng N+1        → Capacity  │
│ ● CẢNH BÁO     SQL-02: 3 lần backup lỗi (mã 13)           → Backup    │
│ ● THEO DÕI     OPX-2026-0012 hết hạn sau 28 ngày          → Opex      │
│ ○ Chờ tôi      BG-2026-0003: 3/3 báo giá đã kiểm tra, chờ chọn NCC   │
│ ○ Chờ tôi      TT-2026-0004 chờ duyệt                                 │
└──────────────────────────────────────────────────────────────────────┘
```

- Mỗi module khai báo một **nguồn việc** (`inbox provider`) trả về các mục.
  - Mỗi mục gồm mức (4 mức chuẩn), tiêu đề, đối tượng, liên kết và người / vai trò cần xử lý.
  - Hộp việc gộp các nguồn, lọc theo quyền. Người dùng đánh dấu "đã xem / tạm ẩn đến ngày …" (bảng `inbox_acks`).
- Trang chủ mặc định chọn theo vai trò, người dùng đổi được (bảng `user_prefs`).

### 2.3 Thẻ đối tượng (entity page) và liên kết chéo

| Thẻ | Gom từ |
|---|---|
| **Host** | capacity (CPU / RAM / HT, ngưỡng), phần cứng & ESXi, license (socket / core, đánh giá), Opex bảo hành và thiết bị (đã có liên kết mềm `vcenter_key + host_name`), hợp đồng, xu hướng |
| **VM** | cấu hình, cluster, HĐH (phân loại + ghi đè), backup (client, RPO, lần thành công cuối, độ phủ), biến động (thêm / đổi / xoá), phát hiện compliance |
| **Nhà cung cấp** | hồ sơ (đã có) + Opex, Capex, hợp đồng, đợt thanh toán, báo giá, điểm đánh giá, đơn giá theo năm |
| **Dự án** (Capex / Cải tạo) | hạng mục, gói báo giá, hợp đồng, thanh toán, tờ trình, so sánh lịch sử — một **không gian dự án** thay cho việc nhảy qua 5 trang |

Các luồng nối mới:
- *Quy hoạch: cần thêm node trước Qx* → **Tạo Capex nháp**.
- *Sizing phương án* → **Capex nháp**.
- *Disk pool backup hết chỗ sau N ngày* → **Capex nháp**.
- *Quyền sử dụng license* ↔ **Opex license**: ngày hết hạn chỉ lưu một chỗ.
- *Phát hiện compliance* → thẻ Host / VM.

### 2.4 Tìm kiếm chung và bảng lệnh (Ctrl + K)

- Tìm mọi mã nghiệp vụ (`OPX-` `CPX-` `HD-` `BG-` `CTVP-` `TT-`), host, VM, client backup, policy, nhà cung cấp (tên, tên viết tắt, tên gọi khác).
- Bảng lệnh gồm cả lệnh điều hướng ("Backup → Job lỗi") và hành động ("Tạo khoản Opex", "Tạo gói báo giá", "Xuất báo cáo điều hành").
- API `GET /api/search?q=` gộp kết quả từ các module, **lọc theo quyền** (người không có vai trò Chi phí không thấy mã Chi phí), tối đa 30 kết quả.

---

## 3. Khung giao diện dùng chung (frontend, không build step)

| Thành phần | Thay cho |
|---|---|
| `routes.js`: bảng định tuyến `{path, view, tabs, feature, role}` + chuyển hướng link cũ | `if/else` trong `renderActiveView`, `currentNavId` có ngoại lệ cho từng module |
| `ui.js`: `pageHeader`, `tabs`, `dataTable` (sắp xếp, tìm, phân trang, xuất), `statusPill` (4 mức), `toast` / `setStatus`, `dialog`, `confirmDialog`, `emptyState` | 6 hàm báo trạng thái riêng, 3 hàm pill riêng, `confirm()` của trình duyệt, 4 kiểu trạng thái rỗng |
| Một **mô hình phạm vi** (vCenter → cluster → host), dùng chung cho Capacity, License, Báo cáo | 4 hệ lọc riêng (quy tắc phạm vi Backup vẫn là cấu hình riêng) |
| Menu **Xuất** chuẩn trên khung trang (PDF / Excel / CSV), gọi cùng endpoint với Trung tâm báo cáo | Nút và form tuỳ chọn riêng từng module |
| `prefs`: trang chủ, giao diện, phạm vi gần nhất, bộ lọc đã lưu — **lưu phía máy chủ** | Rải rác các khoá `localStorage` |
| Nạp module JS khi mở trang lần đầu (thêm thẻ `<script>` động) | Nạp 20 file khi mở app |

Giữ nguyên: token màu, 4 trạng thái, giao diện sáng / tối, toàn bộ API hiện có. Mỗi module được chuyển sang khung mới mà **không viết lại logic**.

---

## 4. Nền tảng AI dùng chung (`backend/app/ai/`)

### 4.1 Kiến trúc

```
module (backup, sizing, …) ── facts (code tính) ──▶ ai.run(task, facts, user)
                                                   │ 1. kiểm tra công tắc tính năng + quyền + hạn mức
                                                   │ 2. che dữ liệu theo chính sách (hostname/IP → mã, như MCP)
                                                   │ 3. cache theo hash(facts) — cùng dữ liệu không gọi lại
                                                   │ 4. gọi Claude (structured output, schema của task)
                                                   │ 5. kiểm tra schema + giới hạn độ dài + bỏ trường lạ
                                                   │ 6. bỏ che, ghi ai_runs (task, user, token, thời gian, trạng thái)
                                                   ▼
                              kết quả + nhãn "AI soạn — kiểm tra trước khi dùng"
                              (AI tắt / lỗi → fallback: văn bản theo luật hiện có)
```

- **Task registry.** Mỗi tác vụ AI khai báo: tên, công tắc, system prompt, JSON schema, hàm dựng `facts` (chỉ dữ liệu cần thiết), hàm kiểm tra, hàm fallback.
  - Ba tác vụ hiện có trong Chi phí (tờ trình, đọc báo giá PDF, tóm tắt khác biệt) chuyển vào registry, hành vi không đổi.
- **Cấu hình chung** (Cài đặt → AI, chỉ admin):
  - Nhà cung cấp, model (mặc định `claude-opus-5-5`), API key (mã hoá, không trả về), cho phép gửi ra ngoài.
  - Công tắc từng tác vụ.
  - **Chính sách che dữ liệu:** che hostname / IP / tên VM — mặc định *bật*.
  - **Hạn mức** token / tháng và lượt / người / giờ.
  - Bảng `spend_ai_config` hiện có được chuyển thành `ai_config` (cột bổ sung qua `ensure_schema()`; giữ bảng cũ một bản phát hành để rollout an toàn).
- **Bảng `ai_runs`.** Theo dõi chi phí và kiểm toán; trang Cài đặt → AI hiện thống kê theo tháng / tác vụ. Không lưu nội dung gửi đi, chỉ lưu hash và kích thước.
- **Prompt injection.** Dữ liệu từ file NCC, tên VM, log được đặt trong khối "DỮ LIỆU". AI không có công cụ ghi; mọi thao tác ghi vẫn do người bấm.

### 4.2 Những chỗ đưa AI vào (xếp theo giá trị × độ biến thiên)

| # | Chỗ | Hiện tại (luật cứng) | AI làm gì | Ưu tiên |
|---|---|---|---|---|
| A1 | **Bản tin trang chủ / Điều hành** | `_management_brief`, `build_management_brief` | Viết bản tin ngắn từ dữ kiện của mọi miền (capacity, backup, license, chi phí), xếp ưu tiên quyết định | P1 |
| A2 | **"Tổng kết — vấn đề & quyết định" của mọi báo cáo PDF** (điều hành, kỹ thuật, quy hoạch, sizing, license, backup, ngân sách, cải tạo) | ~10 hàm `_summary` / `_summary_box` | Một tác vụ chung `report_summary(facts, audience)`: lãnh đạo (ngắn, quyết định) hoặc kỹ thuật (chi tiết). Số liệu do code điền | P1 |
| A3 | **Phân tích lỗi backup** | `backup/analysis.analyze` (~330 dòng), `status_codes.group_of` (mã lạ → "Khác") | Gom lỗi theo nguyên nhân gốc từ mã + thông báo job, giải thích mã lạ, đề xuất hành động; mức độ vẫn theo luật | P1 |
| A4 | **Ánh xạ cột file lạ khi nhập** (RVTools / host-VM, VM cho Sizing, lịch backup, file Summarize, báo giá Excel bố cục lạ) | `pickField` + danh sách tên cột cứng, mỗi chỗ một parser | AI chỉ nhận tiêu đề cột + 5 dòng mẫu, đề xuất ánh xạ; người xác nhận; **nhớ ánh xạ** cho lần sau | P1 |
| A5 | **Phân loại HĐH chưa nhận ra** | `os_classify._classify_text` (regex) | Đề xuất họ / phiên bản / edition cho VM "Không xác định"; người xác nhận → ghi đè | P2 |
| A6 | **Xu hướng: chuyện gì đã thay đổi** | Chưa có | Tóm tắt biến động VM / cluster trong kỳ (thêm / xoá / đổi cấu hình, đổi tên) và tác động tới ngưỡng | P2 |
| A7 | **Sizing: nhận định và bản cho khách hàng** | `sizing_advisory.findings` (~200 dòng), `report_view.customer_view` | Diễn giải phương án, rủi ro, câu hỏi cần làm rõ với khách; số liệu giữ nguyên từ engine | P2 |
| A8 | **Hợp đồng: tóm tắt thay đổi giữa 2 version** | Có diff tô màu, chưa có diễn giải | Tóm tắt điều khoản đổi (thanh toán, phạt, bảo hành, SLA), đánh dấu điều khoản rủi ro | P2 |
| A9 | **Hồ sơ thanh toán: đọc hoá đơn scan** | Tick tay | Đọc hoá đơn PDF, đối chiếu số tiền / MST / số hợp đồng với đợt thanh toán, báo lệch (đối chiếu do code) | P3 |
| A10 | **Hỏi đáp "Hỏi ICO"** | Chưa có | Hỏi bằng tiếng Việt ("VM nào của cluster PRD chưa có backup 3 ngày?"). AI gọi **công cụ chỉ đọc** (API nội bộ, kiểm quyền người hỏi), trả lời kèm link thẻ đối tượng | P3 |
| A11 | Nội dung nhắc hạn email / Discord, mô tả thay đổi quy tắc license | Mẫu cố định | Cá nhân hoá ngắn — giá trị thấp | P3 |

**Không dùng AI** cho: tính capacity / overcommit / N+1 / mã đối soát (`capacity.py` ⇄ `core.js`), kiểm tra công thức báo giá, điểm đánh giá NCC, chỉ số / node, phân quyền, mọi thao tác ghi.

---

## 5. Thay đổi backend

| Hạng mục | Nội dung |
|---|---|
| `app/ai/` | `client.py` (một đường gọi Anthropic, map lỗi, fallback model), `registry.py`, `policy.py` (công tắc, hạn mức, che dữ liệu), `tasks/*.py`; `spend/ai.py` chuyển thành các task |
| Bảng mới | `ai_runs`, `inbox_acks` (người, khoá mục, trạng thái, ẩn đến), `user_prefs` (JSON nhỏ / người), `import_mappings` (nguồn, chữ ký tiêu đề → ánh xạ), `report_archive` (file báo cáo đã xuất, theo PVC `ico-files`) |
| API mới | `GET /api/search`, `GET /api/inbox` + `POST /api/inbox/ack`, `GET /api/entities/{host\|vm\|vendor}/{key}`, `GET/PUT /api/me/prefs`, `POST /api/ai/{task}`, `GET /api/admin/ai/usage` |
| Nguồn việc | `capacity` (cluster vượt ngưỡng), `backup` (lỗi, RPO trễ, pool sắp đầy), `license` (thiếu quyền, sắp hết hạn), `spend` (Opex hết hạn, đợt thanh toán đến hạn, báo giá chờ kiểm tra / chọn, tờ trình chờ duyệt, dự án cải tạo vượt ±15%) |
| Thông báo đẩy | Tổng quát hoá kênh email / Discord của Chi phí thành kênh dùng chung; mỗi loại việc chọn kênh và mức tối thiểu |
| Quyền | Giữ `require_admin`, `require_spend(...)`. Tìm kiếm, hộp việc, thẻ đối tượng **lọc theo quyền từng phần** (thẻ Host ẩn phần Chi phí nếu không có vai trò Chi phí) |

Không đổi: luật chỉ đọc vCenter / NetBackup, chế độ DEMO, `capacity.py` ⇄ `core.js`, schema hiện có (chỉ thêm).

---

## 6. Rủi ro và cách xử lý

| Rủi ro | Xử lý |
|---|---|
| Đổi menu làm người dùng quen tay bị lạc | Chuyển hướng mọi link cũ; giữ tên trang; Ctrl + K tìm được mọi trang cũ theo tên cũ; ghi chú "Đã chuyển tới …" trong 1 bản phát hành |
| Làm lại khung UI một lần, dễ vỡ | Chuyển **từng module** sang khung mới, mỗi giai đoạn một PR; test giao diện bằng Playwright (sáng / tối / mobile) cho từng trang |
| Hộp việc chậm vì gộp nhiều module | Nguồn việc đọc từ snapshot / bảng đã có (không gọi vCenter / NetBackup); cache 60 giây; giới hạn 200 mục |
| Chi phí AI tăng | Chạy theo nút bấm hoặc theo lịch (bản tin 1 lần / ngày); cache theo hash dữ kiện; hạn mức tháng; thống kê ở Cài đặt |
| Lộ thông tin hạ tầng ra ngoài | Che hostname / IP / tên VM mặc định; công tắc từng tác vụ; chỉ gửi dữ kiện tối thiểu; nhật ký `ai_runs` |
| AI viết sai | Số liệu do code; schema + kiểm tra; nhãn "AI soạn"; luôn có bản theo luật để so; người xác nhận trước khi đưa vào tài liệu |
| Hai pod cũ / mới chạy song song khi rollout | Chỉ thêm bảng / cột; giữ `spend_ai_config` một bản phát hành rồi mới bỏ |

---

## 7. Phân giai đoạn (mỗi giai đoạn: branch + PR, test, rà soát bảo mật, CI xanh)

| GĐ | Phạm vi | Kết quả nhìn thấy |
|---|---|---|
| **SA1 — Khung và menu** | Menu 2 miền (`MENU` trong `app.js`), tab trong trang cho mọi mục, địa chỉ `#/<mục>/<tab>` + chuyển hướng link cũ; Cài đặt chia tab (gom Backup nguồn & ngưỡng); `ui.js` (pill, trạng thái, hộp xác nhận thay `confirm()`, trạng thái rỗng) | App gọn, mọi trang cùng một khung |
| **SA2 — Trang chủ và hộp việc** (+ phần khung còn lại: tuỳ chọn người dùng phía máy chủ) | Nguồn việc từ 4 module; `inbox_acks`, `user_prefs`; trang chủ theo vai trò; trang *Lịch việc & nhắc hạn*; danh sách *Đợt thanh toán*; Quy hoạch hiện kết quả trên màn hình | Mở app thấy ngay việc cần làm |
| **SA3 — Tìm kiếm và thẻ đối tượng** (mô hình phạm vi chung, menu Xuất chuẩn, nạp module khi mở trang chuyển sang SA6) | `/api/search` + Ctrl + K; thẻ Host / VM / Nhà cung cấp / không gian Dự án; liên kết chéo (compliance → host, VM → backup, quyền license ↔ Opex); "Tạo Capex nháp" từ Quy hoạch / Sizing / Backup storage | Một đối tượng — một trang |
| **SA4 — Nền tảng AI + A1, A2** | `app/ai/` (registry, chính sách, che dữ liệu, cache, hạn mức, `ai_runs`), chuyển 3 tác vụ Chi phí sang; bản tin trang chủ; tổng kết báo cáo PDF; Cài đặt → AI có thống kê | AI dùng chung, có kiểm soát |
| **SA5 — AI cho dữ liệu nhiều biến số** | A3 phân tích lỗi backup, A4 ánh xạ cột khi nhập (+ `import_mappings`), A5 phân loại HĐH, A6 tóm tắt biến động | Ít nhập tay, ít luật cứng |
| **SA6 — AI nâng cao** | A7 Sizing, A8 tóm tắt version hợp đồng, A9 đọc hoá đơn, A10 Hỏi ICO (công cụ chỉ đọc, kiểm quyền), lưu trữ báo cáo | Trợ lý xuyên suốt |

SA1–SA3 không cần AI và làm được ngay. SA4 là nền cho SA5–SA6.

---

## 8. Đo kết quả

- Số lần bấm để trả lời "VM X có backup và bảo hành không": hiện 4–6 trang → mục tiêu **1 thẻ**.
- Số mục menu: 30 → 14. Số hàm trạng thái / pill / confirm riêng: 6 / 3 / nhiều → 1 / 1 / 1.
- Tỷ lệ tổng kết AI được dùng nguyên văn hoặc sửa nhẹ (đo qua nút "Dùng" / "Sửa"); chi phí AI / tháng trong hạn mức.

---

## 9. Quyết định (đã chốt)

1. **Nhóm menu:** đồng ý 2 miền *Vận hành* / *Quản lý thông tin* (+ Hệ thống) như mục 2.1.
2. **Trang chủ:** **một trang chung cho tất cả** (bản tin + thẻ miền + hộp việc lọc theo quyền), không chia theo vai trò.
3. **Che dữ liệu khi gửi AI:** đồng ý che hostname / IP / tên VM mặc định (giống MCP).
4. **Hạn mức AI:** tối đa **100 USD / tháng** (chạm hạn mức thì dừng gọi AI, quay về văn bản theo luật);
   **cảnh báo ở 50 USD** gửi cho admin (Discord + hộp việc). Bản tin trang chủ: chạy khi bấm, cache theo dữ kiện.
5. **Thông báo đẩy:** gửi **Discord** cho lỗi backup và capacity khi chạm **mức 90%** (ngưỡng VƯỢT NGƯỠNG của
   capacity, tỷ lệ backup thành công / độ phủ dưới 90%); mỗi cảnh báo gửi một lần cho tới khi hết rồi tái phát.
6. **Thứ tự:** SA1 → SA2 → SA3 → SA4 → SA5 → SA6.
