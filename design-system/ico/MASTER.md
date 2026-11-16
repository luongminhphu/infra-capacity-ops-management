# ICO Design System — MASTER (nguồn sự thật duy nhất)

> Áp dụng cho web (`frontend/`), báo cáo PDF (`report_*.py`, `pdf_kit.py`) và Excel (`excel_style.py`).
> Trang có quy tắc riêng nằm ở `pages/*.md` và **ghi đè** file này cho trang đó.
> Hướng đã chọn (16/11/2026): **C — Bàn xử lý làm khung**, **khối kết luận của B** cho trang tổng quan và PDF,
> **bullet bar + small multiples của A** cho Capacity / Backup. Bối cảnh và bản mẫu: `docs/design/redesign/`.

## 1. Nguyên tắc

1. **Hai bộ nhận diện, tách bạch** (quyết định 16/11/2026):
   - **App web:** bảng màu và font riêng của ICO — *“Phòng máy”* (graphite ánh xanh lạnh, tín hiệu cyan; Be Vietnam Pro
     + JetBrains Mono cho mã máy). Kiểm chứng ngày 16/11/2026 bằng dữ liệu tra cứu của `ui-ux-pro-max`
     (`docs/design/redesign/validation-2026-11-16.md`): phong cách Swiss + dày dữ liệu, nền tối trung tính, màu trạng thái
     lục / hổ phách / đỏ và font tiếng Việt đều khớp. Nguồn từng quyết định ghi ở cột **Nguồn** bên dưới.
   - **Báo cáo PDF / Excel:** **HSC Design System** (màu HSC, Inter) — xem §9. Không chép màu web sang báo cáo và ngược lại.
   Cả hai đều phải đạt WCAG 2.2 AA.
2. **Kết luận trước, bằng chứng sau.** Trang tổng quan và báo cáo mở bằng một câu kết luận + trạng thái.
3. **Danh sách → chi tiết, không rời trang.** Mọi đối tượng có thể xử lý (việc, Opex, hợp đồng, báo giá, host, VM)
   mở ở khung chi tiết bên phải; < 1024 px thì chi tiết chiếm toàn màn.
4. **Trạng thái đọc được không cần màu**: hình biểu tượng + độ nặng + nhãn chữ.
5. **Số liệu do code tính** (`capacity.py` ⇄ `core.js`). Giao diện không làm tròn khác nhau giữa web, PDF, Excel.

## 2. Màu web (“Phòng máy”)

Token: `frontend/css/styles.css` (`:root` = theme dark, `[data-theme="light"]`). Alias HSC cũ đã gỡ hết ở vòng 2
(FE-02 / FE-03 / FE-04). Nền nhạt theo vai trò: `--selected-bg` (mục đang chọn: menu, dòng danh sách, chip), `--brand-tint`
+ `--brand-tint-border` (hộp ghi chú), `--selection-bg` (bôi chữ), `--row-hover`, `--row-best` (dòng đề xuất), `--track-bg`
(rãnh thanh), `--status-warn-border` / `--status-crit-border` (viền hộp cảnh báo), `--danger-fg` (chữ nút xoá).
Nguồn: `--domain ux` “Active State”; audit FE-04, duyệt 16/11/2026.

| Vai trò | Token | Dark | Light | Nguồn |
|---|---|---|---|---|
| Nền / bề mặt / bề mặt 2 / nổi | `--bg-app` `--bg-surface` `--bg-surface-2` `--bg-elevated` | `#0E1319` `#151B23` `#1B2330` `#232D3B` | `#F5F7F9` `#FFFFFF` `#EEF2F5` `#E3E9EF` | `--domain color` “dark operations dashboard”, `--domain style` “data-dense dashboard” (Q1, Q7, Q6) |
| Chữ chính | `--text-primary` | `#E8EEF5` (14,8) | `#121A24` (17,5) | `--domain color` (foreground); đo 14,8 / 17,5 |
| Chữ phụ | `--text-secondary` | `#A9B6C6` (8,4) | `#3E4C5E` (8,75) | đo ≥ 6,75 mọi bề mặt |
| Chữ mờ (thấp nhất cho chữ) | `--text-tertiary` | `#8C9AAC` (≥ 4,86 mọi bề mặt) | `#566579` (≥ 4,86) | `--domain color` muted `#94A3B8`; đo ≥ 4,86 |
| Link / thương hiệu (chữ) | `--text-brand` | `#5CD0E4` (9,56) | `#0E6A82` (6,17) | giữ cyan, không dùng nhấn xanh lá của Q1 (trùng màu AN TOÀN) |
| Nhấn (không phải chữ) | `--brand` | cyan `#1FA3BA` | `#0E7490` | như trên |
| Nút chính | `--btn-primary-bg` | `#0E7490`, chữ trắng 5,36 | `#0E6A82`, chữ trắng 6,17 | đo chữ trắng 5,36 / 6,17 |

Tỉ lệ trong ngoặc đo trên `--bg-surface`.

| Thêm từ vòng 2 (duyệt Q8, 16/11/2026) | Token | Dark | Light | Nguồn |
|---|---|---|---|---|
| Viền ô nhập (control) | `--field-border` | `#66768B` (≥ 3,00 mọi bề mặt) | `#768396` (≥ 3,15) | WCAG 1.4.11; `references/pro-rules.md` “Icon Contrast”; đo 66 / 72 màn hình < 3 : 1 trước khi sửa |
| Chữ màu VƯỢT NGƯỠNG | `--status-crit` (`--rd5`) | `#F26B75` (≥ 4,73) | `#B42330` | `--domain ux` “dark mode contrast”; trước đó `#EF4E5A` = 3,91 trên bề mặt nổi |

## 3. Trạng thái (4 mức + NA)

Nhãn và class lấy từ `getStatusForPct` / `combineStatus` (core.js) và `derived_status` (Chi phí). Không tự đặt nhãn.
Token: `--status-ok` / `--status-warn` / `--status-bad` / `--status-crit` (+ `-bg`, `--status-crit-solid`, `--status-crit-ink`)
— đổi tên ngày 16/11 (Q9); tên cũ `--ok` … `--status-success` / `--tone-bad` đã bỏ.
Nguồn: `--domain product` “Status Page” (status green + incident red + maintenance amber), `--domain ux` “Color Only”.

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
- **Nhãn trung tính** (loại, nguồn, phân loại HĐH, “Lịch sử”, “Demo”, “ghi đè”) dùng `uiTag(label, title)` — viền mảnh,
  không glyph. Không mượn `pill ok/crit` để tô một nhãn không phải trạng thái. Không viết `<span class="pill …">` bằng tay:
  `uiPill` / `uiTag` lọc tone và escape nhãn. Nguồn: `--domain ux` “Color Only”; audit FE-23 (vòng 2).

## 4. Chữ

- **Web:** **Be Vietnam Pro** (400 / 500 / 600 / 700, subset latin + vietnamese) cho mọi chữ giao diện — thiết kế riêng cho
  dấu tiếng Việt. **JetBrains Mono** chỉ cho mã máy (`code`, `.ref-code`, mã đối soát, OPX- …). Tự host ở
  `frontend/vendor/be-vietnam-pro-5.2.5/`, `frontend/vendor/jetbrains-mono-5.2.5/` (SIL OFL); không CDN.
  `font-size-adjust: from-font` để không giật chữ khi đổi font.
- **PDF:** Inter từ gói Debian `fonts-inter` (HSC), dự phòng Noto Sans; mã đối soát DejaVu Sans Mono. **Excel:** Calibri.
- Thang web: 12 · 13 · **14 (thân)** · 16 · 18 · 20 · 22 · 28. Không dùng < 12 px. Số dùng `tabular-nums`.
  Nguồn: `--domain style` “data-dense dashboard” (chữ 12–14 px), `--domain ux` “Line Height” 1,5–1,75 (thân 14 / 22 = 1,57).
- **Ô nhập trên điện thoại (< 640 px): 16 px** để iOS không tự phóng to (quick-reference `readable-font-size`; Q8).
- Font: `--domain typography` “vietnamese readable” → *Vietnamese Friendly* (Be Vietnam Pro); `--domain google-fonts`: Be Vietnam Pro,
  JetBrains Mono có subset `vietnamese`. Bộ *Dashboard Data* (Fira Code + Fira Sans) bị loại vì Fira Code không có subset tiếng Việt.
- Không viết hoa toàn bộ cho nhãn nhóm / tiêu đề bảng (ALL CAPS chỉ cho nhãn trạng thái).
- **Định dạng số vi-VN ở mọi nơi người đọc**: `1.234.567` · `1.234,5` · `9,6%` · `0,64x` (web, PDF, chữ trong Excel).
  Web: `fmtInt` / `fmtNum` / `fmtPct` / `fmtX` (core.js), `uiNumber` / `uiMoney` (ui.js), Chart.js `locale = 'vi-VN'`.
  Python: `vn_num` / `vn_g` (`shared/text.py`), qua `fmt_num` / `fmt_int` / `fmt_pct` của `report_pdf.py`.
  Giữ số thuần (dấu chấm, không nhóm) cho dữ liệu máy đọc: CSV, dữ liệu biểu đồ, toạ độ SVG, độ rộng CSS, công thức Excel;
  ô số Excel vẫn là số. Nguồn: `--domain ux` “Number Formatting”; quyết định Q7 (16/11/2026).

## 5. Khoảng cách, bo góc, đổ bóng, chuyển động

- Khoảng cách: 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 (`--space-1…8`); hai vùng bấm cạnh nhau cách ≥ 8 px.
  Nguồn: thang HSC; `--domain ux` “Touch Spacing”.
- Bo góc web: 6 control · 10 khối · 14 lớp nổi (dialog, popover) · pill cho chip lọc / chế độ xem.
  Nguồn: quyết định hướng C (16/11/2026); `search.py` **không có kết quả phù hợp trong dữ liệu** (`rounded corners`
  `--domain style` chỉ trả Organic Biophilic / Neumorphism — loại vì không phải app dữ liệu).
- Control: 36 px mặc định; **< 1024 px: nút, ô nhập, tab, chế độ xem, dòng danh sách, mục sidebar ≥ 44 px** (`--control-h-lg`);
  không dưới 24 px ở mọi nơi (WCAG 2.5.8). Nguồn: `--domain ux` “Touch Target Size”; Q8.
- Icon (lucide): 3 cỡ token `--icon-sm` 14 · `--icon-md` 16 · `--icon-lg` 20 (pro-rules “Consistent Icon Sizing”; Q8).
- Chuyển động: `--domain ux` “Duration Timing” — token chung, không coi 150–300 ms là chuẩn cứng; GSAP / scroll reveal
  (gợi ý của `--design-system`) bị loại: app nội bộ, chỉ chuyển động khi thao tác.
- Chuyển động: 120 / 160 / 200 ms, vào `cubic-bezier(.2,0,0,1)`, ra nhanh hơn. Chỉ chuyển động khi người dùng
  thao tác (mở chi tiết, đổi theme), tối đa 1–2 phần tử mỗi màn. Luôn tôn trọng `prefers-reduced-motion`.
  Nguồn: `--domain ux` “Duration Timing”, “Reduced Motion”, “Excessive Motion”.

## 6. Bố cục & điều hướng

- **Sidebar 240 px** (2 miền + Hệ thống, nhãn nhóm chữ thường) có **số việc** từng mục; < 1024 px thành drawer.
  Nguồn: quyết định hướng C (16/11/2026); `--domain ux` “Active State”; `modern-web-guidance` “navigation-drawer”.
- **Topbar**: tiêu đề trang + tab trong trang (`#/<mục>/<tab>`), tìm kiếm Ctrl + K, đồng bộ. Phạm vi (vCenter /
  cluster / host) nằm **trong** trang, ngay trên nội dung, không đẩy topbar xuống nhiều dòng.
- **Khung danh sách → chi tiết**: danh sách trái (`minmax(380px, 44%)`), chi tiết phải; danh sách là
  `role="listbox"` + `aria-activedescendant`, phím J / K / Enter; chi tiết `aria-live="polite"`.
  Nguồn: quyết định hướng C (16/11/2026); `--domain ux` “Keyboard Navigation”. `search.py` **không có kết quả phù hợp
  trong dữ liệu** cho mẫu list → detail (`master detail list`, `list detail split view`).
- Phạm vi (vCenter / cluster / host) trên điện thoại (< 640 px) gom sau nút “Phạm vi: …” (`aria-expanded`). Nguồn: §7 “không
  quá 3 select hiện cùng lúc”; vòng 2 PR8.
- **Khối kết luận**: vạch trạng thái 4 px + trạng thái + một câu (Be Vietnam Pro 28–36 px trên web, Inter trên PDF,
  `text-wrap:balance`) + câu dẫn. Nguồn: quyết định 16/11/2026 (khối kết luận của hướng B).
- Co giãn theo **container query** cho khối; breakpoint trang chỉ còn 640 và 1024. Nguồn: `modern-web-guidance` “size-aware-styling”.

## 7. Thành phần

| Thành phần | Quy tắc | Nguồn |
|---|---|---|
| Hộp thoại | `<dialog>` + `showModal()` cho xác nhận / form chặn trang; bảng điều khiển không chặn dùng `popover`; focus thấy rõ | `modern-web-guidance` “dialog”; `--domain ux` “Focus States”, “Focus Appearance” |
| Bảng | `<table>` + `<caption>`; khi cuộn ngang bọc `role="region" tabindex="0" aria-labelledby`; tiêu đề dính; sắp xếp có `aria-sort` | `modern-web-guidance` “responsive-table”, “sortable-table”; `--domain ux` “Table Handling”, “Horizontal Scroll” |
| Bộ lọc | Chip / chế độ xem đã lưu cho lọc chính; lọc phụ trong popover; không quá 3 `select` hiện cùng lúc | quyết định hướng C (16/11/2026) |
| KPI | Dải KPI liền khối có bullet bar (vạch 80 % / 100 %) thay cho thẻ số to rời | `--domain chart` “KPI target progress” |
| Đang tải / lỗi | `uiLoading(text)`; lỗi `uiError(text, retry)` có `role="alert"` + nút Thử lại | `--domain ux` “Error Recovery”, “Error Messages”; vòng 2 PR8 |
| Trạng thái rỗng | Nói điều gì đang thiếu + một hành động (`uiEmpty`) | `--domain ux` “Empty States” |
| Pill / tag | Trạng thái: `uiPill(tone, label, title)`; nhãn trung tính: `uiTag(label, title)` (§3) | `--domain ux` “Color Only” |
| Logo công ty | Khe cao cố định, `width:auto; object-fit:contain` — không bao giờ kéo giãn | quyết định vòng 1 (logo bị kéo giãn trên báo cáo) |

## 8. Biểu đồ (web + PDF cùng quy tắc)

- Series web: `--chart-s1` cyan · `--chart-s2` tím · `--chart-s3` hồng (dark `#1FA3BA #8070F0 #D45EA3`, light
  `#0089A6 #6A5AE0 #C2468E` — qua cả 6 bài kiểm của validator `dataviz`) + `--chart-s4` xám cho “Khác / tổng”.
  PDF dùng series HSC (`COLORS["series1…3"]`). Nhiều hơn 3 series → small multiples, hoặc chỉ tô 3 series nhấn
  và để các series còn lại xám (`chartSeriesColor(i)`), nét mảnh hơn. `s5–s8` đã bỏ (FE-06). Phần của một tổng
  (cột chồng 100 %): tối đa 3 sắc + các bậc xám (`chartAlpha(other, a)`). Tô màu trong suốt bằng `chartAlpha`, không nối chuỗi hex.
- **Không dùng màu trạng thái cho series.** Màu trạng thái chỉ khi dữ liệu *là* trạng thái (thành công / lỗi).
- Một trục Y. Ngưỡng vẽ bằng vạch + dải nền (`--chart-band-warn` 80–100 %, `--chart-band-bad` > 100 %).
- Nhãn trực tiếp trên đường khi ≤ 4 series; luôn có chú giải khi ≥ 2 series; luôn có “Xem bảng”.
- **Biểu đồ đường ≥ 2 series: khác màu và khác kiểu nét + hình điểm** (liền / gạch / chấm). Plugin `icoSeriesStyle` (ui.js)
  tự gán; đường ngưỡng tự đặt `borderDash` riêng. Nguồn: `--domain chart` “Trend Over Time” (Q8).
- **“Xem bảng”**: plugin `icoDataTable` (ui.js) dựng bảng thu gọn dưới mọi biểu đồ từ chính dữ liệu biểu đồ. Nguồn:
  `--domain chart` — mọi loại có *A11y Fallback* = bảng dữ liệu (Q8).
- KPI so với ngưỡng: bullet bar (`--domain chart` “KPI target progress” → bullet chart khi ≥ 3 KPI).
- `chartPalette()` (ui.js) là cửa duy nhất lấy màu biểu đồ trong JS: `series` (3), `other`, `seq` (một sắc, cho nhóm có thứ tự),
  `deep`, `grid`, `axis`, `bandWarn`, `bandBad`; PDF dùng `COLORS["series1…3"]`. Nguồn: `--domain chart` “Compare Categories”
  (không mã hoá hạng mục chỉ bằng màu), `--domain ux` “Color Only”; FE-06 duyệt 16/11/2026.

## 9. PDF / Excel — giữ HSC Design System

Nguồn: quyết định 16/11/2026 (hai bộ nhận diện tách bạch, §1). Skill `hsc-design-system` chỉ còn phạm vi này.

- `COLORS` trong `report_pdf.py` = vai trò HSC trên nền trắng (ink `#0B2D5C`, muted
  `#465F83`, faint `#597090`, ok `#187A2C`, bad `#B82E37`, crit `#8F282D`, crit_solid `#B82E37`…).
- Không thêm hex rời trong `report_*.py` / `excel_*.py`; cần màu mới thì thêm vào `COLORS` / `excel_style.py`.
- Báo cáo mở bằng **khối kết luận** giống web (trang 1 PDF Vận hành = trang Điều hành).

## 10. Chữ trên giao diện

Tiếng Việt có dấu, câu ngắn, động từ hành động (“Duyệt”, “Tạo gia hạn”, “Đánh dấu xong”). Một hành động giữ một tên
suốt luồng. Lỗi nói chuyện gì xảy ra và cách sửa, không xin lỗi. Không trộn Anh – Việt trên nhãn (“Add host” → “Thêm host”);
**giữ thuật ngữ kỹ thuật quốc tế** (vCPU, overcommit, VM, host, cluster, datastore, snapshot, Opex / Capex, RPO, N+1).
Nguồn: skill `design-skills:ux-writing`; `--domain ux` “Form Labels”; quyết định Q10 (16/11/2026).

## 11. Kiểm tra trước khi giao

- [ ] Không mã màu / px mới ngoài token (`:root`, `COLORS`, `excel_style.py`); không chép màu web vào báo cáo.
- [ ] Số hiển thị qua helper vi-VN (§4); không `toFixed` / `toLocaleString('en-US')` cho chữ người đọc.
- [ ] Không `<span class="pill …">` viết tay — `uiPill` / `uiTag` (§3).
- [ ] Dark + light, ≥ 1440 px và < 1024 px; không cuộn ngang trang.
- [ ] Chữ ≥ 4,5 : 1, glyph / viền điều khiển ≥ 3 : 1; focus nhìn thấy; dùng được hoàn toàn bằng bàn phím.
- [ ] Quyết định mới về màu / font / mật độ: tra `ui-ux-pro-max` (`python3 .claude/skills/ui-ux-pro-max/scripts/search.py`),
      kiểm độ phù hợp (loại mẫu landing / marketing), font phải có subset `vietnamese`, ghi nguồn vào bảng tương ứng.
- [ ] `prefers-reduced-motion` tôn trọng; không chữ < 12 px.
- [ ] Mọi ô nhập / chọn có nhãn (`<label>` hoặc `aria-label`; `uiAutoLabel` chỉ là lưới an toàn); vùng bấm ≥ 24 × 24 px.
- [ ] Không cắt chữ bằng “…” ở dòng danh sách — cho xuống dòng; bảng rộng nằm trong vùng cuộn riêng; tiêu đề không nhảy bậc.
- [ ] Biểu đồ: ≤ 3 series màu, không màu trạng thái cho series, một trục Y, có “Xem bảng”.
- [ ] Số liệu khớp giữa web / PDF / Excel ở chế độ DEMO.
- [ ] `python -m pytest tests -q` (gồm `test_frontend_markup.py`).
