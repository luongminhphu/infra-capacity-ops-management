# ICO Design System — MASTER (nguồn sự thật duy nhất)

> Áp dụng cho web (`frontend/`), báo cáo PDF (`report_*.py`, `pdf_kit.py`) và Excel (`excel_style.py`).
> Trang có quy tắc riêng nằm ở `pages/*.md` và **ghi đè** file này cho trang đó.
> Hướng đã chọn (16/11/2026): **C — Bàn xử lý làm khung**, **khối kết luận của B** cho trang tổng quan và PDF,
> **bullet bar + small multiples của A** cho Capacity / Backup. Bối cảnh và bản mẫu: `docs/design/redesign/`.

## 1. Nguyên tắc

1. **Màu và font là HSC** (primitive `--sr/--sg/--sb/--sc/--sy/--n…`, Inter). Không thêm mã màu ngoài HSC; chỉ chọn
   *bậc* HSC cho từng vai trò sao cho đạt WCAG 2.2 AA.
2. **Kết luận trước, bằng chứng sau.** Trang tổng quan và báo cáo mở bằng một câu kết luận + trạng thái.
3. **Danh sách → chi tiết, không rời trang.** Mọi đối tượng có thể xử lý (việc, Opex, hợp đồng, báo giá, host, VM)
   mở ở khung chi tiết bên phải; < 1024 px thì chi tiết chiếm toàn màn.
4. **Trạng thái đọc được không cần màu**: hình biểu tượng + độ nặng + nhãn chữ.
5. **Số liệu do code tính** (`capacity.py` ⇄ `core.js`). Giao diện không làm tròn khác nhau giữa web, PDF, Excel.

## 2. Màu (vai trò → bậc HSC)

Token web: `frontend/css/styles.css` (`:root` = dark mặc định, `[data-theme="light"]`). Tỉ lệ tương phản đo trên bề mặt
`--bg-surface`.

| Vai trò | Token | Dark | Light |
|---|---|---|---|
| Chữ chính | `--text-primary` | `#FFFFFF` | `sb10 #0B2D5C` |
| Chữ phụ | `--text-secondary` | `rgba(214,231,255,.64)` 6,46 | `rgba(11,45,92,.76)` 6,21 |
| Chữ mờ (thấp nhất cho mọi chữ) | `--text-tertiary` | `.56` ≥ 4,62 | `.68` ≥ 4,62 |
| Chữ tắt (không cần AA) | `--text-disable` | `.32` | `.32` |
| Link / chữ thương hiệu | `--text-brand` | `sb60 #588EDA` 5,39 | `sb40 #165AB8` 6,08 |
| Nút chính | `--btn-primary-bg` | `#1F5FD6` (chữ trắng 5,73) | `sb40` (6,59) |
| Viền focus | `--focus-ring` | `sb60` | `sb40` |
| Nhấn thương hiệu (không phải chữ) | `--brand` | `sb50 #2681FF` | `sb50` |

**Cấm:** `--text-tertiary` cũ (`.32` / `.40`) cho chữ; chữ trắng trên `#3377FF`; `sb50` làm chữ trên nền trắng.

## 3. Trạng thái (4 mức + NA)

Nhãn và class lấy từ `getStatusForPct` / `combineStatus` (core.js) và `derived_status` (Chi phí). Không tự đặt nhãn.

| Nhãn | class | Biểu tượng (lucide) | Hình thức | Màu glyph dark / light |
|---|---|---|---|---|
| AN TOÀN | `ok` | `circle-check` | viền mảnh, không nền | `sg60` / `sg40` |
| THEO DÕI | `warn` | `eye` | nền vàng nhạt | `sy50` / mực `sb10` |
| CẢNH BÁO | `bad` | `triangle-alert` | nền đỏ nhạt + vạch trái 3 px | `sr70` / `sr40` |
| VƯỢT NGƯỠNG | `crit` | `octagon-x` | **khối đặc** `sr40`, chữ trắng (6,03) | trắng |
| NA | `na` | `circle-dashed` | nền xám nhạt | chữ phụ |

- **Chữ nhãn luôn là mực** (`--text-primary`), trừ VƯỢT NGƯỠNG (trắng trên khối đặc).
- Web: `.pill.<cls>` vẽ glyph bằng `::before` + `mask` (SVG `data:`), nên mọi chỗ gọi `uiPill()` tự đúng.
- PDF: `report_pdf.status_pill()` vẽ glyph SVG inline, cùng quy tắc.
- Số to có màu (`.tone-*`) chỉ dùng cho số ≥ 18 px; nhãn nhỏ không tô màu trạng thái.
- Mức ưu tiên (`prio-cao/tb/thap`) **không phải trạng thái**: không có glyph, chữ mực.

## 4. Chữ

- **Inter** variable (trục `wght` 100–900 và `opsz`), tự host ở `frontend/vendor/inter-5.2.5/` (latin, latin-ext,
  vietnamese; SIL OFL). Không dùng Google Fonts / CDN. `font-optical-sizing:auto` thay cho “Inter Display”.
  `font-size-adjust: from-font` để không giật chữ khi đổi font.
- PDF: Inter từ gói Debian `fonts-inter` (Dockerfile), dự phòng Noto Sans; mã đối soát dùng DejaVu Sans Mono.
- Excel: Calibri (Inter thường không có trên máy mở file).
- Thang: 12 · 13 · **14 (thân)** · 16 · 18 · 20 · 24 · 28 · 36. Không dùng < 12 px. Số dùng `tabular-nums`.
- Không viết hoa toàn bộ cho nhãn nhóm / tiêu đề bảng (giữ ALL CAPS duy nhất cho nhãn trạng thái).

## 5. Khoảng cách, bo góc, đổ bóng, chuyển động

- Khoảng cách HSC: 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 (`--space-1…8`).
- Bo góc: 4 control · 8 khối · 12 lớp nổi (dialog, popover) · pill 100 cho chip lọc / chế độ xem.
- Control: 36 px mặc định, 44 px cho mục chạm chính trên < 1024 px; không dưới 28 px.
- Chuyển động: 120 / 160 / 200 ms, vào `cubic-bezier(.2,0,0,1)`, ra nhanh hơn. Chỉ chuyển động khi người dùng
  thao tác (mở chi tiết, đổi theme). Luôn tôn trọng `prefers-reduced-motion`.

## 6. Bố cục & điều hướng

- **Sidebar 240 px** (2 miền + Hệ thống, nhãn nhóm chữ thường) có **số việc** từng mục; < 1024 px thành drawer.
- **Topbar**: tiêu đề trang + tab trong trang (`#/<mục>/<tab>`), tìm kiếm Ctrl + K, đồng bộ. Phạm vi (vCenter /
  cluster / host) nằm **trong** trang, ngay trên nội dung, không đẩy topbar xuống nhiều dòng.
- **Khung danh sách → chi tiết**: danh sách trái (`minmax(380px, 44%)`), chi tiết phải; danh sách là
  `role="listbox"` + `aria-activedescendant`, phím J / K / Enter; chi tiết `aria-live="polite"`.
- **Khối kết luận**: vạch trạng thái 4 px + trạng thái + một câu (Inter 28–36 px, `text-wrap:balance`) + câu dẫn.
- Co giãn theo **container query** cho khối; breakpoint trang chỉ còn 640 và 1024.

## 7. Thành phần

| Thành phần | Quy tắc |
|---|---|
| Hộp thoại | `<dialog>` + `showModal()` cho xác nhận / form chặn trang; bảng điều khiển không chặn dùng `popover` |
| Bảng | `<table>` + `<caption>`; khi cuộn ngang bọc `role="region" tabindex="0" aria-labelledby`; tiêu đề dính; sắp xếp có `aria-sort` |
| Bộ lọc | Chip / chế độ xem đã lưu cho lọc chính; lọc phụ trong popover; không quá 3 `select` hiện cùng lúc |
| KPI | Dải KPI liền khối có bullet bar (vạch 80 % / 100 %) thay cho thẻ số to rời |
| Trạng thái rỗng | Nói điều gì đang thiếu + một hành động (`uiEmpty`) |
| Logo công ty | Khe cao cố định, `width:auto; object-fit:contain` — không bao giờ kéo giãn |

## 8. Biểu đồ (web + PDF cùng quy tắc)

- Series: `--chart-s1` xanh dương · `--chart-s2` lục lam · `--chart-s3` tím (đã qua validator của `dataviz`, cả
  dark và light) + `--chart-s4` xám cho “Khác / tổng”. Nhiều hơn 3 series → small multiples. `s5–s8` chỉ còn cho
  biểu đồ cũ chưa vẽ lại.
- **Không dùng màu trạng thái cho series.** Màu trạng thái chỉ khi dữ liệu *là* trạng thái (thành công / lỗi).
- Một trục Y. Ngưỡng vẽ bằng vạch + dải nền (`--chart-band-warn` 80–100 %, `--chart-band-bad` > 100 %).
- Nhãn trực tiếp trên đường khi ≤ 4 series; luôn có chú giải khi ≥ 2 series; luôn có “Xem bảng”.
- `chartPalette()` (ui.js) là cửa duy nhất lấy màu biểu đồ trong JS; PDF dùng `COLORS["series1…3"]`.

## 9. PDF / Excel

- `COLORS` trong `report_pdf.py` = vai trò light-theme ở trên, quy ra hex trên nền trắng (ink `#0B2D5C`, muted
  `#465F83`, faint `#597090`, ok `#187A2C`, bad `#B82E37`, crit `#8F282D`, crit_solid `#B82E37`…).
- Không thêm hex rời trong `report_*.py` / `excel_*.py`; cần màu mới thì thêm vào `COLORS` / `excel_style.py`.
- Báo cáo mở bằng **khối kết luận** giống web (trang 1 PDF Vận hành = trang Điều hành).

## 10. Chữ trên giao diện

Tiếng Việt có dấu, câu ngắn, động từ hành động (“Duyệt”, “Tạo gia hạn”, “Đánh dấu xong”). Một hành động giữ một tên
suốt luồng. Lỗi nói chuyện gì xảy ra và cách sửa, không xin lỗi. Không trộn Anh – Việt trên nhãn (“Add host” → “Thêm host”).

## 11. Kiểm tra trước khi giao

- [ ] Không mã màu / px mới ngoài token (`:root`, `COLORS`, `excel_style.py`).
- [ ] Dark + light, ≥ 1440 px và < 1024 px; không cuộn ngang trang.
- [ ] Chữ ≥ 4,5 : 1, glyph / viền điều khiển ≥ 3 : 1; focus nhìn thấy; dùng được hoàn toàn bằng bàn phím.
- [ ] `prefers-reduced-motion` tôn trọng; không chữ < 12 px.
- [ ] Biểu đồ: ≤ 3 series màu, không màu trạng thái cho series, một trục Y, có “Xem bảng”.
- [ ] Số liệu khớp giữa web / PDF / Excel ở chế độ DEMO.
- [ ] `python -m pytest tests -q` (gồm `test_frontend_markup.py`).
