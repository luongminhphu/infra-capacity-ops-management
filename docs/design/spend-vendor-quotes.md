# Thiết kế: Nhà cung cấp & So sánh báo giá · Cải tạo văn phòng (hạ tầng IT)

Trạng thái: **ĐÃ CHỐT** 14/11/2026 theo trả lời của anh/chị:
báo giá NCC chủ yếu là **PDF scan** (V1 cần AI đọc PDF); **cho phép** gửi báo giá tới Anthropic (vẫn qua công tắc
admin); **3 khối** Cabling (gồm Hub) / Network / Video Conference; lịch sử **5 tầng FL3, FL11, FL12, FL6, FL7**;
so sánh lịch sử theo **thực chi**; phiếu đánh giá NCC theo **mẫu EVALUATION FORM** đính kèm (mục 1.6). Mở rộng module *Chi phí & Hợp đồng IT*
(`docs/design/spend-module.md`), dùng lại hạ tầng sẵn có: lưu file, kiểm tra file, audit, vai trò, AI provider,
báo cáo PDF / Excel, tờ trình.

---

## 0. Rút ra từ file mẫu `IT_FL6_Cost_17092026.xlsx`

Cách làm hiện tại (sheet *Summarize*):

- 3 khối chi phí: **I. IT Facility** (cáp đồng, cáp quang, ống PVC, rack, access control, camera, sàn nâng + UPS +
  điều hoà phòng hub, nhân công, điện UPS phòng họp, di dời máy tính, quản lý dự án, phát sinh), **II. IT Network**
  (AP, switch L3 / L2, SFP, license, triển khai), **III. Video Conference** (theo từng phòng họp).
- Mỗi khối: chi tiết theo dòng của **phương án chọn** (cột *Price*) + **tổng của 3 nhà cung cấp** (VNT / TTC Infotech /
  Thakral One; CMC / HPT / SVT).
- So sánh với **các tầng đã làm**: FL6 – FL12 – FL3, theo cùng danh sách nhóm chi phí, kèm quy mô (*186 nodes – 75 NS*,
  *62 nodes – 24 NS*, *220 nodes*) và cấu hình (*10G & Wifi7* so với *1G & Wifi6*).
- Chỉ số chuẩn hoá: **chi phí / node mạng** (Facility, Network) và **chi phí / phòng họp** (VC).

Những điểm hệ thống sẽ tự phát hiện (thử trên chính file này):

| # | Phát hiện | Số liệu |
|---|---|---|
| 1 | Dự toán IT Facility (F10, đưa lên bảng tổng C3) **thấp hơn** tổng báo giá VNT (G10) và cột lịch sử FL6 (M10) | 814.752.128 so với 913.109.394, lệch **98.356.266** = RACK CABINET 39.760.848 (F14 trống) + PHÁT SINH 58.596.418 (F24 trống) |
| 2 | Dự toán VC (F41 = 1.600.711.200) chỉ bằng ~41% báo giá SVT được chọn (I41 = 3.909.015.600) | Cần xác nhận phạm vi (2 phòng VC + 1 phòng trình chiếu) khác phạm vi báo giá |
| 3 | Chi phí / node (G25 = 4.909.191) tính trên 913.109.394 trong khi dự toán dùng 814.752.128 | Hai con số cùng tên "chi phí FL6" không khớp |
| 4 | Tiêu đề khối VC ghi "SO SÁNH CHI PHÍ NETWORK" (K39) | Lỗi sao chép nhãn |
| 5 | Chi phí / phòng họp FL12 = 0 nhưng vẫn chia cho 2 phòng (N45) | Thiếu dữ liệu lịch sử nên ghi "không có số liệu", không ghi 0 |

---

## 1. Module A — Nhà cung cấp & so sánh báo giá

### 1.1 Hồ sơ nhà cung cấp

Mở rộng bảng `spend_vendors` sẵn có (cột mới qua `ensure_schema()`): tên viết tắt, mã số thuế, địa chỉ, người liên hệ,
email, điện thoại, website, lĩnh vực (nhãn: Cáp & hạ tầng, Mạng, VC, Server/Storage, License…), ghi chú, trạng thái
(Đang dùng / Tạm ngừng / Không hợp tác — kèm lý do).

Trang hồ sơ NCC tổng hợp **từ dữ liệu đã có**, không nhập lại:
- Opex, hạng mục Capex, hợp đồng, đợt thanh toán của NCC; tổng giá trị theo năm.
- Các gói báo giá đã tham gia: số lần tham gia / trúng, điểm đánh giá trung bình, chênh lệch giá trung bình so với giá thấp nhất.
- Lịch sử đơn giá các nhóm chi phí NCC này đã báo (xem 2.4).

### 1.2 Gói so sánh báo giá

- Mã `BG-<năm tài chính>-<số>`. Gồm: tên gói, phạm vi (chung / thuộc một dự án Cải tạo văn phòng và một khối chi phí),
  liên kết tuỳ chọn tới dự án Capex, bảng khối lượng mời chào (tuỳ chọn) và số báo giá tối thiểu (mặc định **3**).
- Trạng thái: Nháp → Đang so sánh → Đã chọn NCC (quyền **Duyệt**) → Đóng.
- Chọn NCC khi có **ít hơn 3 báo giá** thì bắt buộc nhập lý do. Ví dụ "Độc quyền — chỉ 1 NCC", như mục License Core
  trong Mẫu A.

### 1.3 Nhận diện báo giá

Mỗi báo giá = 1 NCC + file gốc (PDF / .xlsx / .docx; kiểm tra nội dung, chặn macro như hiện tại) + số báo giá, ngày,
hiệu lực, tiền tệ (VND hoặc ngoại tệ kèm tỷ giá), điều khoản (giao hàng, bảo hành, thanh toán).

| Định dạng | Cách đọc | Cần AI? |
|---|---|---|
| Excel (.xlsx) | Tự tìm dòng tiêu đề bảng theo từ khoá (STT, Hạng mục / Mô tả / Components, Model, ĐVT / Unit, SL / Qty, Đơn giá / Price, Thành tiền / Amount); đọc từng dòng; nhận dòng Cộng / VAT / Tổng / Chiết khấu; giữ cả công thức để đối chiếu | Không |
| Word (.docx) | Đọc các bảng, cùng quy tắc tiêu đề như Excel | Không |
| PDF có chữ / PDF scan / ảnh | Gửi file cho AI (Claude đọc PDF, kể cả bản scan), trả về các dòng theo JSON schema cố định | Có — chỉ khi admin bật và cho phép gửi ra ngoài; nếu không thì nhập tay hoặc dán từ Excel |

Kết quả nhận diện **luôn phải qua bước người kiểm tra**: bảng dòng sửa được, bấm "Đã kiểm tra". Báo giá chưa kiểm tra
vẫn hiện trong so sánh nhưng có nhãn "Chưa kiểm tra" và không được dùng để chốt chọn NCC.

### 1.4 Kiểm tra công thức (code tính, không dùng AI)

| Mã | Kiểm tra | Ngưỡng |
|---|---|---|
| `LINE` | SL × Đơn giá = Thành tiền từng dòng | lệch > 1 đồng × SL (làm tròn) |
| `SUBTOTAL` | Tổng các dòng = Cộng trước thuế ghi trên báo giá | lệch > 1.000 đ |
| `VAT` | VAT = Cộng × thuế suất (nhận 0 / 5 / 8 / 10%) | lệch > 1.000 đ |
| `TOTAL` | Tổng = Cộng – Chiết khấu + VAT | lệch > 1.000 đ |
| `MISSING` | Dòng thiếu SL, ĐVT hoặc đơn giá; dòng giá 0 | — |
| `DUP` | Dòng trùng mô tả + model | — |
| `SCOPE` | Thiếu hạng mục có trong bảng khối lượng mời chào, hoặc khối lượng khác | — |
| `ESTIMATE` | Dự toán chọn khác tổng báo giá của NCC được chọn | > 1% (phát hiện #1, #2 ở mục 0) |
| `FX` | Báo giá ngoại tệ thiếu tỷ giá | — |

Mỗi lỗi lưu thành một dòng *phát hiện* (mã, mức độ, giá trị mong đợi / thực tế, dòng liên quan) để xem, lọc và xuất báo cáo.

### 1.5 So sánh

- **Ghép dòng giữa 3 báo giá**: ưu tiên theo bảng khối lượng mời chào; nếu không có thì theo model / part number, rồi
  độ giống của mô tả (chuẩn hoá chữ hoa / thường, dấu, đơn vị). Người dùng sửa ghép tay; hệ thống **nhớ cách ghép** cho
  lần sau (theo NCC + mô tả).
- **Ma trận**: hạng mục × NCC (đơn giá, thành tiền). Tô giá thấp nhất; ghi chênh lệch % so với thấp nhất; ghi rõ
  "không báo giá" và "báo thêm". So tổng **trước VAT** (tránh lệch do 8% / 10%), có thêm cột so tổng trên phần chung.
- **Nội dung / cấu hình**: so model, thông số chính, bảo hành, thời gian giao hàng, điều kiện thanh toán, hiệu lực báo
  giá. Phần so model / mã do code làm. Phần tóm tắt khác biệt kỹ thuật bằng lời do AI làm (tuỳ chọn) và luôn ghi rõ là
  "AI tóm tắt".
- **Chấm điểm NCC**: xem **1.6 Phiếu đánh giá NCC** (thay cho đoạn dưới đây, giữ lại để đối chiếu). Trước khi có mẫu:
  - **A. Khung tiêu chuẩn (70%)**: Chất lượng sản phẩm / dịch vụ 15%, Giá thành 15%, Uy tín NCC 10%,
    Kinh nghiệm NCC 10%, Dịch vụ trước / sau bán hàng 10%, Tuân thủ an toàn thông tin 10%.
  - **B. Mở rộng (30%)**: Kinh nghiệm hỗ trợ hệ thống quan trọng 10%, Đội ngũ kỹ thuật & QLDA 5%,
    Chủ động đưa giải pháp tối ưu 15%.
  - Điểm 1–10, tổng có trọng số. Xếp **Loại A / B / C** theo ngưỡng cấu hình.
  - Điểm "Giá thành" có thể tự gợi ý theo tỷ lệ giá thấp nhất / giá NCC; người chấm vẫn được sửa.
- **Chọn NCC** (Duyệt): lưu lý do. Từ báo giá được chọn có thể tạo nhanh **hạng mục Capex** hoặc **khoản Opex**,
  **hợp đồng**, và đưa bảng so sánh + bảng điểm vào **tờ trình Mẫu B** (mục "Đánh giá nhà cung cấp",
  "Tổng hợp chi phí").

### 1.6 Phiếu đánh giá NCC (theo EVALUATION FORM của công ty)

Ba file mẫu (IT Facility / Network / Video Conference) cùng một cấu trúc; hệ thống dựng đúng cấu trúc này:

- Đầu phiếu: *Bộ phận đánh giá* (mặc định ITD, cấu hình), *Ngày*, *Loại hình sản phẩm, dịch vụ* (= tên gói báo giá,
  vd "Thi công hạ tầng mạng cho dự án Cải tạo văn phòng FL6"), *Kết luận lựa chọn* ("Đề xuất chọn VNT").
- **A. Khung đánh giá tiêu chuẩn — 70%** (cố định): Chất lượng sản phẩm/dịch vụ 15%, Giá thành 15%, Mức độ uy tín NCC
  10%, Kinh nghiệm NCC 10%, Dịch vụ trước/sau bán hàng 10%, Tuân thủ các quy định về bảo mật và an toàn thông tin 10%.
- **B. Khung đánh giá mở rộng — 30%**: **tối thiểu 02, tối đa 04 tiêu chí** do bộ phận ghi theo loại dịch vụ; tổng
  trọng số B = 30%. Gợi ý sẵn theo khối (lấy từ 3 mẫu, sửa được):
  - Cabling: *Mức độ hiểu biết về hạ tầng cáp mạng của HSC*, *Linh hoạt xử lý các vấn đề phát sinh do thay đổi thiết
    kế*, *Đáp ứng yêu cầu tiến độ dự án* (10% mỗi tiêu chí).
  - Network: *Hiểu biết về mô hình kết nối mạng ở các dự án cải tạo trước đó*, *Hỗ trợ cho mượn thiết bị tương đương để
    vận hành trước*, *Đáp ứng yêu cầu tiến độ dự án*.
  - Video Conference: *Có kinh nghiệm tư vấn và triển khai các giải pháp VC*, *Hỗ trợ cho mượn thiết bị*, *Đáp ứng yêu
    cầu tiến độ dự án*.
- Điểm 1–10 cho từng NCC; **Total = Σ tỷ trọng × điểm**; **Loại đánh giá**: ≥ 8 → A, ≥ 7 → B, ≥ 6 → C, < 6 → không đạt
  (đúng công thức `IFS` trong mẫu).
- Ràng buộc kiểm tra khi lưu: tổng tỷ trọng = 100% (A = 70%, B = 30%), B có 2–4 tiêu chí, điểm nguyên / thập phân 1–10,
  đủ điểm cho mọi NCC trước khi chốt.
- **Gợi ý điểm Giá thành** từ báo giá: NCC rẻ nhất 10, các NCC khác giảm theo % cao hơn (người chấm được sửa).
- **Xuất Excel đúng bố cục EVALUATION FORM** (giữ công thức Total và Loại đánh giá) để ký: *Người thực hiện đánh giá* /
  *Trưởng Bộ phận phê duyệt*; và đưa bảng điểm vào tờ trình Mẫu B.
- Tên NCC có **tên gọi khác** (alias): "Thakral One" trong file chi phí = "THRAKONE" trong phiếu đánh giá.

---

## 2. Module B — Cải tạo văn phòng (hạ tầng IT)

### 2.1 Dự án cải tạo

Mã `CTVP-<năm>-<số>`. Gồm:
- Tên, địa điểm / tầng (FL6, FL12…), năm, trạng thái.
- **Quy mô**: số node mạng, số nhân sự (NS), diện tích m² (tuỳ chọn), số phòng hub, phòng họp theo loại
  (nhỏ / vừa / lớn / chỉ trình chiếu).
- **Nhãn cấu hình** để so sánh cùng loại: 10G / 1G, Wifi7 / Wifi6, có / không VC…
- Liên kết dự án Capex.
- Cờ **"Dự án lịch sử"** cho số liệu các năm trước nhập vào.

### 2.2 Bốn khối chi phí

**Đã chốt 3 khối** (đúng file hiện tại):

| Khối | Nhóm chi phí chuẩn (khởi tạo từ file FL6, sửa được) | Chỉ số chuẩn hoá |
|---|---|---|
| **Cabling (gồm Hub)** | Cabling materials, cáp quang, PVC conduit & accessories, rack cabinet, access control, camera, raise floor for hub room, UPS, air condition for hub room, install fee, điện nguồn UPS cho VC & tivi phòng họp, dịch vụ di dời máy tính, project management, phát sinh | đ / node, đ / NS |
| **Network** | Access point, switch (L3 + L2 + license), SFP / module, chi phí triển khai thiết bị & vật tư dây nhảy | đ / node, đ / AP |
| **Video Conference** | Theo từng phòng họp (VC / chỉ trình chiếu có dây – không dây) | đ / phòng họp (theo loại) |

Mỗi khối có một **gói báo giá** (Module A) với 3 NCC. Dòng báo giá được **ghép vào nhóm chi phí chuẩn** theo từ khoá /
danh sách tên gọi khác (ví dụ "CABLING MATERIALS", "Cáp mạng Cat6", "CommScope" → *Vật tư cáp đồng*), người dùng xác
nhận một lần. Nhóm có thể gom nhiều dòng: *Switch* = switch L3 + switch L2 + license, giống ô M31 trong file.

### 2.3 So sánh với lịch sử

Với dự án đang làm, mỗi khối hiển thị:
- **Theo nhóm chi phí**: số hiện tại (báo giá chọn / thấp nhất) so với từng dự án trước (FL12, FL3…) và so với
  trung vị / thấp nhất / cao nhất của các dự án trước.
- **Theo chỉ số chuẩn hoá**: đ / node, đ / NS, đ / phòng họp, đ / phòng hub. Đây là cách so công bằng khi quy mô khác
  nhau (186 node so với 62 node).
- **Điều chỉnh trượt giá** (tuỳ chọn): quy đổi số cũ về năm hiện tại theo % / năm cấu hình (mặc định tắt).
- **Cảnh báo**: lệch quá ngưỡng (mặc định **±15%**) so với trung vị lịch sử cùng nhãn cấu hình. Ví dụ "Network FL6
  15.225.897 đ / node, cao hơn FL3 50% — khác cấu hình 10G & Wifi7 so với 1G & Wifi6". Khi nhãn cấu hình khác nhau,
  cảnh báo ghi rõ "khác cấu hình" thay vì kết luận đắt hơn.
- **Đơn giá từng mặt hàng** (cùng model, ví dụ C9300-48T-E, AP Meraki) qua các năm và các NCC: biểu đồ xu hướng.
- Không có số liệu thì ghi **"không có số liệu"**, không ghi 0 và không chia cho số phòng (phát hiện #5).

### 2.4 Nguồn số liệu lịch sử

1. **Nhập từ file so sánh đang dùng** (bố cục *Summarize* như FL6): đọc các khối I / II / III, các cột dự án (FL6,
   FL12, FL3), quy mô từ tiêu đề cột (`(62 nodes) - 24 NS`, `10G & Wifi7`), tạo dự án lịch sử + số liệu theo nhóm chi
   phí. Hiện bảng xem trước để xác nhận trước khi lưu.
2. **Mẫu Excel chuẩn** để nhập các năm trước chưa có file theo bố cục này.
3. Từ đây trở đi: số liệu tự sinh từ báo giá được chọn, và từ **chi phí thực tế** (hợp đồng, đợt thanh toán đã trả)
   khi dự án đóng.

Mỗi số liệu lịch sử ghi rõ **loại**: dự toán, báo giá trúng hoặc thực chi. So sánh mặc định dùng *thực chi*; nếu không
có thì dùng *báo giá trúng*.

### 2.5 Chốt dự án

Khi dự án đóng: chốt số thực chi theo nhóm (từ hợp đồng / thanh toán). Từ đó dự án trở thành dữ liệu lịch sử cho năm sau.

### 2.6 Báo cáo

- **Excel theo đúng bố cục anh/chị đang dùng** (*Summarize*):
  - bảng tổng; mỗi khối có chi tiết phương án chọn + tổng 3 NCC; khối so sánh lịch sử; dòng chi phí / node, chi phí / phòng họp;
  - **giữ công thức Excel** để Finance tự kiểm.
  - Thêm sheet chi tiết từng báo giá và sheet các phát hiện sai lệch.
- **PDF tóm tắt**, theo khung báo cáo hiện có:
  - tổng 3 khối, NCC đề xuất, bảng điểm;
  - so sánh lịch sử theo chỉ số;
  - cảnh báo và quyết định cần duyệt.
- **Dữ liệu cho tờ trình**: bảng so sánh, bảng điểm và so sánh lịch sử được đưa vào dữ liệu AI / bảng chi phí khi soạn
  tờ trình Mẫu B.

---

## 3. Mô hình dữ liệu

| Bảng | Nội dung chính | Ràng buộc |
|---|---|---|
| `spend_vendors` (+ cột) | liên hệ, mã số thuế, lĩnh vực, trạng thái + lý do | thêm qua `ensure_schema()` |
| `quote_requests` | mã `BG-…`, tên, phạm vi, `renovation_project_id`?, `workstream`?, `capex_project_id`?, trạng thái, `min_quotes`, NCC chọn, lý do | mã UNIQUE; CHECK trạng thái; CHECK (đã chọn ⇔ có NCC chọn); CHECK (< min_quotes ⇒ có lý do) |
| `quote_request_items` | bảng khối lượng mời chào: mô tả, ĐVT, SL, nhóm chi phí chuẩn | |
| `quotations` | gói, NCC, file, số / ngày / hiệu lực, tiền tệ + tỷ giá, các tổng *ghi trên báo giá* và *tính lại*, cách nhận diện (excel / docx / ai / tay), trạng thái nhận diện (chờ / đã đọc / đã kiểm tra / lỗi), điều khoản | UNIQUE (gói, NCC); tiền BIGINT ≥ 0 |
| `quotation_lines` | thứ tự, loại dòng (hàng / chiết khấu / cộng / VAT / tổng / ghi chú), mô tả, model, ĐVT, SL `numeric(14,3)`, đơn giá, thành tiền ghi trên báo giá, dòng mời chào ghép, nhóm chi phí | |
| `quotation_findings` | báo giá, dòng?, mã kiểm tra, mức độ, mong đợi, thực tế, thông điệp | sinh lại mỗi lần kiểm tra |
| `line_match_memory` | NCC + mô tả chuẩn hoá → nhóm chi phí / dòng mời chào | UNIQUE (NCC, mô tả) |
| `vendor_criteria`, `vendor_scores` | tiêu chí (nhóm, tên, trọng số); điểm (gói, báo giá, tiêu chí, điểm 1–10, người chấm) | tổng trọng số = 100% (kiểm tra khi lưu); UNIQUE (báo giá, tiêu chí) |
| `renovation_projects` | mã `CTVP-…`, năm, địa điểm / tầng, node, NS, m², phòng hub, phòng họp theo loại, nhãn cấu hình, lịch sử?, Capex? | số lượng ≥ 0 |
| `cost_groups` | nhóm chi phí chuẩn: khối, tên, đơn vị, tên gọi khác (aliases), thứ tự | UNIQUE (khối, tên) |
| `renovation_costs` | dự án, nhóm, loại số liệu (dự toán / báo giá trúng / thực chi), số tiền, nguồn (báo giá, hợp đồng, import) | UNIQUE (dự án, nhóm, loại) |

Đơn giá từng mặt hàng qua các năm lấy trực tiếp từ `quotation_lines` đã kiểm tra (có model / nhóm), không lưu bảng riêng.

---

## 4. API (tiền tố `/api/spend`)

`vendors/{id}` (hồ sơ + tổng hợp) · `quotes` (CRUD gói) · `quotes/{code}/quotations` (upload → nhận diện) ·
`…/quotations/{id}/lines` (sửa dòng) · `…/verify` · `…/findings` · `quotes/{code}/comparison` (ma trận) ·
`quotes/{code}/scores` · `quotes/{code}/select` (Duyệt) · `quotes/{code}/export.xlsx|pdf` ·
`renovations` (CRUD) · `renovations/{code}/history` · `renovations/import` (xem trước → xác nhận) ·
`renovations/{code}/export.xlsx|pdf` · `admin/cost-groups`, `admin/vendor-criteria`.
Quyền: Xem — đọc; Biên tập — tạo / upload / sửa dòng / chấm điểm; Duyệt — chọn NCC, chốt dự án; Admin — danh mục,
tiêu chí, nhập lịch sử, xoá.

---

## 5. Màn hình

- **Nhà cung cấp**: danh sách + hồ sơ: liên hệ, lịch sử chi tiêu, các gói báo giá, điểm, đơn giá.
- **So sánh báo giá**:
  - danh sách gói;
  - chi tiết gói gồm các tab: *Báo giá* (upload 3 file, trạng thái nhận diện, số lỗi), *Kiểm tra* (bảng phát hiện,
    bấm vào để tới dòng), *So sánh* (ma trận tô màu, tổng, điều khoản), *Chấm điểm*, *Kết luận*.
- **Cải tạo văn phòng**:
  - danh sách dự án (có cả dự án lịch sử);
  - chi tiết dự án gồm: quy mô và cấu hình, 4 khối (mỗi khối: gói báo giá, tổng, so sánh lịch sử, cảnh báo),
    biểu đồ chỉ số theo năm, nút xuất Excel / PDF;
  - **Nhập lịch sử** (upload file Summarize → xem trước → lưu).

---

## 6. Rủi ro & cách xử lý

| Rủi ro | Xử lý |
|---|---|
| Nhận diện sai (bố cục lạ, ô gộp, nhiều sheet) | Luôn có bước "Đã kiểm tra"; hiện dòng gốc cạnh dòng đọc được; sửa tay; nhớ cách ghép |
| PDF scan khi AI tắt | Nhập tay / dán từ Excel; ghi rõ "chưa nhận diện" |
| Gửi báo giá của NCC ra ngoài | Chỉ khi admin bật AI **và** cho phép gửi; ghi audit file nào đã gửi |
| Đơn vị khác nhau (m / cuộn 305 m, bộ / cái) | Hệ số quy đổi trong nhóm chi phí; cảnh báo khi ĐVT khác |
| VAT 8% / 10% khác nhau giữa NCC | So sánh trước VAT; hiển thị cả sau VAT |
| Quy mô / cấu hình dự án khác nhau | So theo chỉ số chuẩn hoá + nhãn cấu hình; không kết luận khi khác cấu hình |
| File Excel của NCC chứa công thức / macro | Chặn .xlsm / macro (đã có); đọc giá trị *và* công thức; khi xuất, ô chữ không bao giờ thành công thức |
| Số lịch sử thiếu | Ghi "không có số liệu", loại khỏi trung vị |

---

## 7. Phân giai đoạn (mỗi giai đoạn một branch + PR, có test với số liệu cụ thể)

| GĐ | Phạm vi |
|---|---|
| **V1** | Hồ sơ NCC (+ tên gọi khác); gói báo giá; upload 3 báo giá; đọc **Excel / Word** và **PDF scan bằng AI** (Claude đọc PDF, theo công tắc admin); sửa dòng + "Đã kiểm tra"; **kiểm tra công thức**; ghép dòng + ma trận + tổng; **phiếu đánh giá NCC theo EVALUATION FORM** + xuất Excel đúng mẫu; chọn NCC; xuất Excel so sánh |
| **V2** | Cải tạo văn phòng: dự án, **3 khối**, nhóm chi phí chuẩn, quy mô / cấu hình; **nhập lịch sử 5 tầng (FL3, FL11, FL12, FL6, FL7) từ file Summarize**; so sánh lịch sử theo **thực chi**, theo nhóm + chỉ số + cảnh báo; xuất Excel đúng bố cục Summarize (có công thức) + PDF |
| **V3** | AI tóm tắt khác biệt nội dung / cấu hình giữa 3 báo giá; đơn giá theo mặt hàng qua các năm; đưa so sánh + phiếu đánh giá vào tờ trình Mẫu B; tạo nhanh Capex / hợp đồng từ báo giá chọn |

Test bắt buộc (ví dụ): đọc lại đúng file FL6 → phát hiện #1 (lệch 98.356.266 = 39.760.848 + 58.596.418);
chi phí / node FL6 Network = 15.225.897, FL12 = 11.801.788, FL3 = 10.138.000; dòng SL × đơn giá sai 1 chữ số bị bắt;
VAT 8% so với 10%; ghép dòng 3 báo giá có mô tả khác nhau.

---

## 8. Câu hỏi cần anh/chị quyết

1. Báo giá NCC gửi thường là **Excel, PDF có chữ hay PDF scan**? (Quyết định V1 có cần AI hay không.)
2. Có cho phép **gửi file báo giá của NCC tới Anthropic** để nhận diện PDF / scan không?
3. Giữ **3 khối** như file (Facility / Network / VC) hay tách **4 khối** (Cáp / Phòng Hub / Mạng / VC) như mô tả?
   *Đề xuất: lưu 4 khối, báo cáo xuất được theo 3 khối như file hiện tại.*
4. Số liệu các dự án cải tạo trước đây có bao nhiêu năm / tầng, và đã có file theo bố cục *Summarize* chưa?
5. So lịch sử theo **thực chi** hay **báo giá trúng**? Ngưỡng cảnh báo **±15%** và có điều chỉnh trượt giá không?
6. Bộ tiêu chí chấm điểm lấy đúng như Mẫu B (A 70% / B 30%) và ngưỡng xếp loại A / B / C là bao nhiêu?
