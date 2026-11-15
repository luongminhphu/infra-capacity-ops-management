# ICO Design System — MASTER (nguồn sự thật duy nhất)

> Áp dụng cho web (`frontend/`), báo cáo PDF (`report_*.py`, `pdf_kit.py`) và Excel (`excel_style.py`).
> Trang có quy tắc riêng nằm ở `pages/*.md` và **ghi đè** file này cho trang đó.
> Hướng đã chọn (16/11/2026): **C — Bàn xử lý làm khung**, **khối kết luận của B** cho trang tổng quan và PDF,
> **bullet bar + small multiples của A** cho Capacity / Backup. Bối cảnh và bản mẫu: `docs/design/redesign/`.

## 1. Nguyên tắc

1. **Hai bộ nhận diện, tách bạch** (quyết định 16/11/2026):
   - **App web:** bảng màu và font riêng của ICO — *“Phòng máy”* (graphite ánh xanh lạnh, tín hiệu cyan; Be Vietnam Pro
     + JetBrains Mono cho mã máy), chọn theo quy tắc mặc định của `ui-ux-pro-max`.
   - **Báo cáo PDF / Excel:** **HSC Design System** (màu HSC, Inter) — xem §9. Không chép màu web sang báo cáo và ngược lại.
   Cả hai đều phải đạt WCAG 2.2 AA.
2. **Kết luận trước, bằng chứng sau.** Trang tổng quan và báo cáo mở bằng một câu kết luận + trạng thái.
3. **Danh sách → chi tiết, không rời trang.** Mọi đối tượng có thể xử lý (việc, Opex, hợp đồng, báo giá, host, VM)
   mở ở khung chi tiết bên phải; < 1024 px thì chi tiết chiếm toàn màn.
4. **Trạng thái đọc được không cần màu**: hình biểu tượng + độ nặng + nhãn chữ.
5. **Số liệu do code tính** (`capacity.py` ⇄ `core.js`). Giao diện không làm tròn khác nhau giữa web, PDF, Excel.

## 2. Màu web (“Phòng máy”)

Token: `frontend/css/styles.css` (`:root` = dark mặc định, `[data-theme="light"]`). Tên token cũ của HSC (`--blue-a12`,
`--sb60`…) chỉ còn là alias trỏ sang màu mới — không dùng cho code mới.

| Vai trò | Token | Dark | Light |
|---|---|---|---|
| Nền / bề mặt / bề mặt 2 / nổi | `--bg-app` `--bg-surface` `--bg-surface-2` `--bg-elevated` | `#0E1319` `#151B23` `#1B2330` `#232D3B` | `#F5F7F9` `#FFFFFF` `#EEF2F5` `#E3E9EF` |
| Chữ chính | `--text-primary` | `#E8EEF5` (14,8) | `#121A24` (17,5) |
| Chữ phụ | `--text-secondary` | `#A9B6C6` (8,4) | `#3E4C5E` (8,75) |
| Chữ mờ (thấp nhất cho chữ) | `--text-tertiary` | `#8C9AAC` (≥ 4,86 mọi bề mặt) | `#566579` (≥ 4,86) |
| Link / thương hiệu (chữ) | `--text-brand` | `#5CD0E4` (9,56) | `#0E6A82` (6,17) |
| Nhấn (không phải chữ) | `--brand` | cyan `#1FA3BA` | `#0E7490` |
| Nút chính | `--btn-primary-bg` | `#0E7490`, chữ trắng 5,36 | `#0E6A82`, chữ trắng 6,17 |

Tỉ lệ trong ngoặc đo trên `--bg-surface`.

## 3. Trạng thái (4 mức + NA)

Nhãn và class lấy từ `getStatusForPct` / `combineStatus` (core.js) và `derived_status` (Chi phí). Không tự đặt nhãn.

| Nhãn | class | Biểu tượng (lucide) | Hình thức | Màu glyph web dark / light |
|---|---|---|---|---|
| AN TOÀN | `ok` | `circle-check` | viền mảnh, không nền | lục `#4CC38A` / `#1A7346` |
| THEO DÕI | `warn` | `eye` | nền hổ phách nhạt | hổ phách `#F2C14E` / `#8A6100` |
| CẢNH BÁO | `bad` | `triangle-alert` | nền cam nhạt + vạch trái 3 px | cam `#F28C38` / `#A84A0A` |
| VƯỢT NGƯỠNG | `crit` | `octagon-x` | **khối đặc** đỏ `#C7303D` / `#B42330`, chữ trắng (5,36 / 6,51) | trắng |
| NA | `na` | `circle-dashed` | nền xám nhạt | chữ phụ |

- **Chữ nhãn luôn là mực** (`--text-primary`), trừ VƯỢT NGƯỠNG (trắng trên khối đặc). Web dùng 4 sắc khác nhau (lục / hổ phách / cam / đỏ); báo cáo PDF giữ sắc HSC (§9).
- Web: `.pill.<cls>` vẽ glyph bằng `::before` + `mask` (SVG `data:`), nên mọi chỗ gọi `uiPill()` tự đúng.
- PDF: `report_pdf.status_pill()` vẽ glyph SVG inline, cùng quy tắc.
- Số to có màu (`.tone-*`) chỉ dùng cho số ≥ 18 px; nhãn nhỏ không tô màu trạng thái.
- Mức ưu tiên (`prio-cao/tb/thap`) **không phải trạng thái**: không có glyph, chữ mực.

## 4. Chữ

- **Web:** **Be Vietnam Pro** (400 / 500 / 600 / 700, subset latin + vietnamese) cho mọi chữ giao diện — thiết kế riêng cho
  dấu tiếng Việt. **JetBrains Mono** chỉ cho mã máy (`code`, `.ref-code`, mã đối soát, OPX- …). Tự host ở
  `frontend/vendor/be-vietnam-pro-5.2.5/`, `frontend/vendor/jetbrains-mono-5.2.5/` (SIL OFL); không CDN.
  `font-size-adjust: from-font` để không giật chữ khi đổi font.
- **PDF:** Inter từ gói Debian `fonts-inter` (HSC), dự phòng Noto Sans; mã đối soát DejaVu Sans Mono. **Excel:** Calibri.
- Thang web: 12 · 13 · **14 (thân)** · 16 · 18 · 20 · 22 · 28. Không dùng < 12 px. Số dùng `tabular-nums`.
- Không viết hoa toàn bộ cho nhãn nhóm / tiêu đề bảng (ALL CAPS chỉ cho nhãn trạng thái).

## 5. Khoảng cách, bo góc, đổ bóng, chuyển động

- Khoảng cách HSC: 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 (`--space-1…8`).
- Bo góc web: 6 control · 10 khối · 14 lớp nổi (dialog, popover) · pill cho chip lọc / chế độ xem.
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

- Series web: `--chart-s1` cyan · `--chart-s2` tím · `--chart-s3` hồng (dark `#1FA3BA #8070F0 #D45EA3`, light
  `#0089A6 #6A5AE0 #C2468E` — qua cả 6 bài kiểm của validator `dataviz`) + `--chart-s4` xám cho “Khác / tổng”.
  PDF dùng series HSC (`COLORS["series1…3"]`). Nhiều hơn 3 series → small multiples. `s5–s8` chỉ còn cho
  biểu đồ cũ chưa vẽ lại.
- **Không dùng màu trạng thái cho series.** Màu trạng thái chỉ khi dữ liệu *là* trạng thái (thành công / lỗi).
- Một trục Y. Ngưỡng vẽ bằng vạch + dải nền (`--chart-band-warn` 80–100 %, `--chart-band-bad` > 100 %).
- Nhãn trực tiếp trên đường khi ≤ 4 series; luôn có chú giải khi ≥ 2 series; luôn có “Xem bảng”.
- `chartPalette()` (ui.js) là cửa duy nhất lấy màu biểu đồ trong JS; PDF dùng `COLORS["series1…3"]`.

## 9. PDF / Excel — giữ HSC Design System

- `COLORS` trong `report_pdf.py` = vai trò HSC trên nền trắng (ink `#0B2D5C`, muted
  `#465F83`, faint `#597090`, ok `#187A2C`, bad `#B82E37`, crit `#8F282D`, crit_solid `#B82E37`…).
- Không thêm hex rời trong `report_*.py` / `excel_*.py`; cần màu mới thì thêm vào `COLORS` / `excel_style.py`.
- Báo cáo mở bằng **khối kết luận** giống web (trang 1 PDF Vận hành = trang Điều hành).

## 10. Chữ trên giao diện

Tiếng Việt có dấu, câu ngắn, động từ hành động (“Duyệt”, “Tạo gia hạn”, “Đánh dấu xong”). Một hành động giữ một tên
suốt luồng. Lỗi nói chuyện gì xảy ra và cách sửa, không xin lỗi. Không trộn Anh – Việt trên nhãn (“Add host” → “Thêm host”).

## 11. Kiểm tra trước khi giao

- [ ] Không mã màu / px mới ngoài token (`:root`, `COLORS`, `excel_style.py`); không chép màu web vào báo cáo.
- [ ] Dark + light, ≥ 1440 px và < 1024 px; không cuộn ngang trang.
- [ ] Chữ ≥ 4,5 : 1, glyph / viền điều khiển ≥ 3 : 1; focus nhìn thấy; dùng được hoàn toàn bằng bàn phím.
- [ ] `prefers-reduced-motion` tôn trọng; không chữ < 12 px.
- [ ] Mọi ô nhập / chọn có nhãn (`<label>` hoặc `aria-label`; `uiAutoLabel` chỉ là lưới an toàn); vùng bấm ≥ 24 × 24 px.
- [ ] Không cắt chữ bằng “…” ở dòng danh sách — cho xuống dòng; bảng rộng nằm trong vùng cuộn riêng; tiêu đề không nhảy bậc.
- [ ] Biểu đồ: ≤ 3 series màu, không màu trạng thái cho series, một trục Y, có “Xem bảng”.
- [ ] Số liệu khớp giữa web / PDF / Excel ở chế độ DEMO.
- [ ] `python -m pytest tests -q` (gồm `test_frontend_markup.py`).
