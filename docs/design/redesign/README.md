# Làm mới giao diện ICO — Giai đoạn 1: rà soát và 3 hướng thiết kế

> Trạng thái: **CHỜ CHỌN HƯỚNG.** Chưa sửa code app. Rà soát backend nằm riêng ở [`docs/audit-2026-11-15.md`](../../audit-2026-11-15.md).
>
> Ràng buộc đã chốt (15/11): **giữ bảng màu và font của HSC Design System** (primitive màu HSC, Inter). Phần làm mới là
> bố cục, mật độ, điều hướng, thành phần, trạng thái, biểu đồ, chuyển động, chữ trên giao diện — và **bậc màu HSC nào được
> dùng cho vai trò nào**, để đạt WCAG 2.2 AA.

Mở bản mẫu trực tiếp từ repo (cần thư mục `frontend/vendor/` để có icon): `docs/design/redesign/<hướng>/trang-chu.html`,
`dieu-hanh.html`, `opex.html`. Nút mặt trời / mặt trăng góc trên đổi dark / light. Ảnh chụp sẵn ở `previews/`.

| Hướng | Ý tưởng một câu | Bản mẫu |
|---|---|---|
| **A — Đài kiểm soát** | Một màn hình = một ca trực: thanh *nhịp hạ tầng* luôn hiện, bảng dày, rail icon có nhãn | [`a-dai-kiem-soat/`](a-dai-kiem-soat/) |
| **B — Tờ trình sống** | Mỗi trang đọc như bản PDF nó xuất ra: một câu kết luận → quyết định → bằng chứng | [`b-to-trinh-song/`](b-to-trinh-song/) |
| **C — Bàn xử lý** | Danh sách bên trái, đối tượng đang chọn bên phải; mở việc, xử lý, sang việc kế — không rời trang | [`c-ban-xu-ly/`](c-ban-xu-ly/) |

---

## 1. Cách làm và nguồn

- `ui-ux-pro-max`: skill trong môi trường này **chỉ có `SKILL.md`, không có `scripts/search.py` và `references/`**. Vì vậy
  không chạy được `--design-system`, `--domain`, các dial `--variance / --motion / --density`. Mọi chấm điểm và quyết định
  dưới đây **theo quy tắc mặc định của skill** (bảng 10 nhóm ưu tiên trong `SKILL.md`); giá trị dial ghi ở mỗi hướng là
  *ý định thiết kế*, không phải kết quả tra cứu.
- `dataviz`: bảng màu biểu đồ được kiểm bằng `scripts/validate_palette.js` của skill (kết quả thật, mục 4).
- `modern-web-guidance`: đã tra `dialog`, `navigation-drawer`, `size-aware-styling`, `responsive-table`,
  `same-document-transitions`, `visually-stable-font-fallbacks`, `sortable-table` (áp dụng ở mục 5).
- `frontend-design`: dùng để soát "dấu hiệu template" (nhãn ALL CAPS trên mọi khối, thẻ giống hệt nhau, số to + nhãn nhỏ
  mặc định) — app hiện tại mắc cả ba.
- Ảnh chụp app hiện tại ở chế độ DEMO (2 vCenter, 1 NetBackup), 1440 px và 820 px, dark và light. Số liệu trong bản mẫu
  lấy từ API DEMO; **khoản Opex là dữ liệu mẫu** vì DEMO chưa có Opex (ghi rõ trên từng trang).

## 2. Chấm điểm giao diện hiện tại (1 = kém, 5 = tốt)

Theo thứ tự ưu tiên của `ui-ux-pro-max`: 1 Truy cập · 2 Chạm & tương tác · 3 Hiệu năng · 4 Chọn phong cách · 5 Bố cục &
responsive · 6 Chữ & màu · 7 Chuyển động · 8 Form & phản hồi · 9 Điều hướng · 10 Biểu đồ & dữ liệu.

| Nhóm trang | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | Điểm nghẽn lớn nhất |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Trang chủ | 2 | 2 | 3 | 3 | 3 | 3 | 3 | 3 | 4 | — | 18 việc cùng một độ nặng; tiêu đề lặp (“… — Kết nối client / mạng”); thẻ miền chỉ có nhãn trạng thái, không có số |
| Điều hành | 2 | 2 | 3 | 2 | 3 | 2 | 3 | — | 3 | 2 | KPI “0” tô xanh neon cỡ lớn; khối xu hướng rỗng chiếm nửa màn hình khi mới có 1 snapshot |
| Capacity | 2 | 2 | 3 | 2 | 2 | 2 | 3 | 3 | 3 | 2 | Thanh phạm vi đẩy topbar thành 2–3 dòng; donut “VM power state” neon chiếm 1/3 hàng; nhãn host bị cắt |
| What-if | 2 | 2 | 3 | 3 | 3 | 3 | 3 | 2 | 3 | 3 | 8 thẻ kịch bản ngang hàng che mất form; nhãn trộn Anh–Việt (“Add host”, “Chạy simulation”) |
| Backup | 2 | 2 | 3 | 2 | 3 | 2 | 3 | 3 | 4 | 1 | Biểu đồ **hai trục Y** (số job + TB); dải đỏ “Cần xử lý” là một câu dài nối bằng “·”; 6 KPI 4 màu |
| License | 2 | 2 | 3 | 3 | 2 | 3 | 3 | 3 | 3 | — | “Tường” chip cluster 2 dòng; bảng 11 cột cuộn ngang; khối vàng cảnh báo rule đứng trước kết luận |
| Chi phí & Hợp đồng | 2 | 2 | 3 | 3 | 3 | 3 | 3 | 2 | 3 | — | 6 `select` lọc bị cắt chữ (“Cần theo dõi (dự kiế…”); không chọn nhiều / thao tác hàng loạt |
| BOM | 2 | 2 | 3 | 3 | 3 | 3 | 3 | 2 | 3 | — | Ô chọn file gốc của trình duyệt (“Choose File”, tiếng Anh); nút chính bị tắt gần như không đọc được |
| Cài đặt | 2 | 2 | 3 | 3 | 2 | 3 | 3 | 3 | 2 | — | 9 tab ngang tràn dòng; nút Lưu nằm xa trường vừa sửa |

Bằng chứng chung (đo trên `frontend/`):

1. **Truy cập (nghiêm trọng nhất).** Chữ mờ `--text-tertiary` = 2,57 : 1 (dark) và 2,29 : 1 (light) — dùng cho nhãn
   nhóm menu, placeholder, chú thích. Nút chính chữ trắng trên `#3377FF` = 4,02 : 1. Light: xanh lá trạng thái `#21A33B`
   3,30 : 1, đỏ VƯỢT NGƯỠNG `#FF0011` 3,99 : 1, vàng THEO DÕI `#8F7611` 4,40 : 1 — **cả ba dưới 4,5** khi là chữ.
   Dark: CẢNH BÁO `#FF525A` và VƯỢT NGƯỠNG `#FF0011` đều đỏ, khó phân biệt. Không có `prefers-reduced-motion`.
   6 chỗ chữ 9–11 px. Có skip link và focus ring (tốt).
2. **Chạm.** Control 28 / 32 / 36 px, dưới mức 44 px; tab 32 px.
3. **Hiệu năng.** Font Inter tải từ Google Fonts — trái quy tắc “không CDN” và **không tải được sau App Proxy / mạng
   không ra Internet** (khi đó rơi về font hệ thống, chữ đổi kích thước). 20 file JS nạp lúc mở app (Chi phí, BOM đã lazy).
4. **Phong cách.** Xanh lá `#00FF11` và vàng `#FFD400` dùng cho số to và cả series biểu đồ; nhãn ALL CAPS ở nhóm menu,
   eyebrow, tiêu đề bảng (14 chỗ); mọi khối là thẻ bo 8 px giống nhau.
5. **Bố cục.** 7 breakpoint khác nhau (640 / 760 / 900 / 1024 / 1240 / 1400 / 1680), không có container query; banner
   “1 cảnh báo: Dữ liệu tổng hợp từ vc1, vc2” (thông tin, không phải cảnh báo) lặp trên mọi trang.
6. **Chữ & màu.** Nhãn trạng thái tô màu chữ (màu là kênh duy nhất phân biệt CẢNH BÁO / VƯỢT NGƯỠNG ở dark).
9. **Điều hướng.** Menu 2 miền + tab + `#/<mục>/<tab>` đã tốt. Còn: Điều hành nằm dưới Trang chủ nhưng nói về Capacity;
   Cài đặt 9 tab.
10. **Biểu đồ.** Bảng màu series hiện tại **không đạt** kiểm định (mục 4); trộn màu trạng thái vào series (“Powered On”
    xanh lá = màu AN TOÀN); biểu đồ hai trục ở Backup; không có chế độ xem bảng.

## 3. Màu HSC giữ nguyên — đổi bậc để đạt AA (dùng chung cho cả 3 hướng)

File: [`_shared/hsc-tokens.css`](_shared/hsc-tokens.css). Không thêm mã màu ngoài HSC; chỉ đổi vai trò → bậc.

| Vai trò | Dark: hiện tại → đề xuất | Light: hiện tại → đề xuất |
|---|---|---|
| Chữ phụ `ink-2` | a52 (4,68) → **a64 (6,46)** | a72 (5,50) → **a76 (6,21)** |
| Chữ mờ `ink-3` (thấp nhất cho mọi chữ) | a32 (**2,57**) → **a56 (≥ 4,62 trên mọi bề mặt)** | a40 (**2,29**) → **a68 (≥ 4,62)** |
| Nút chính | trắng / `#3377FF` (**4,02**) → trắng / `#1F5FD6` (5,73) | → trắng / `sb40 #165AB8` (6,59) |
| Link | `sb50` → `sb60 #588EDA` (5,39) | `sb50` (**3,71**) → `sb40` (6,08) |

**Trạng thái: chữ luôn là màu mực (ink), màu nằm ở biểu tượng + nền + độ đậm.** Bốn mức khác nhau bằng **hình** và
**độ nặng**, không chỉ bằng sắc — đọc được khi in đen trắng và với người mù màu.

| Mức | Biểu tượng | Hình thức | Dark (chữ / nền) | Light (chữ / nền) | Glyph ≥ 3 : 1 |
|---|---|---|---|---|---|
| AN TOÀN | `circle-check` | viền mảnh, không nền | 17,99 | 13,6 | `sg60` 8,40 / `sg40` 3,30 |
| THEO DÕI | `eye` | nền vàng nhạt | 12,35 | 12,15 | `sy50` 9,6 / mực navy 12,15 |
| CẢNH BÁO | `triangle-alert` | nền đỏ nhạt + vạch trái | 14,37 | 11,26 | `sr70` 4,52 / `sr40` 4,99 |
| VƯỢT NGƯỠNG | `octagon-x` | **khối đặc** `sr40`, chữ trắng | 6,03 | 6,03 | — |
| NA | `circle-dashed` | nền xám nhạt | 14,84 | 12,13 | — |

Nhãn, class (`ok / warn / bad / crit / na`) và ngưỡng giữ nguyên từ `getStatusForPct` / `derived_status`.

## 4. Bảng màu biểu đồ (kiểm bằng validator của `dataviz`)

| Bảng | Dark (nền `#14171A`) | Light (nền `#FFFFFF`) |
|---|---|---|
| Hiện tại `chart-s1…s6` (`#2681FF, #23A4B8, #C552FF, #FFB020, #FF7077, #34CB52`) | **FAIL**: dải sáng (3 màu), CVD ΔE 3,2 (`#34CB52`↔`#FF7077`, deutan) | — |
| **Đề xuất** 3 sắc HSC + xám | `#2681FF` · `#23A4B8` · `#C552FF` — **ALL CHECKS PASS** | `#165AB8` · `#23A4B8` · `#B729FE` — **ALL CHECKS PASS** |

Quy tắc kèm theo: tối đa 3 series màu + `n100` cho “Khác / tổng”; nhiều hơn → small multiples. **Không dùng màu trạng thái
cho series.** Một trục Y (biểu đồ Backup tách thành 2 biểu đồ). Ngưỡng 80 % / 100 % vẽ bằng vạch và dải nền, nhãn trực tiếp
trên đường. Mọi biểu đồ có “Xem bảng”. Cùng bảng màu cho PDF (`COLORS` trong `report_pdf.py`) ở Giai đoạn 2.

## 5. Nền tảng chung cho mọi hướng

- **Font:** Inter (HSC), **tự host** trong `frontend/vendor/` (bỏ Google Fonts), `font-size-adjust: from-font` để
  không giật chữ khi đổi font (guide `visually-stable-font-fallbacks`). PDF giữ Roboto / Noto Sans trong Docker, hoặc
  thêm Inter vào image — chọn ở Giai đoạn 2.
- **Thang chữ:** 12 · 13 · 14 (thân) · 16 · 18 · 20 · 24 · 28 · 36. Bỏ 9–11 px. Không ALL CAPS cho nhãn.
- **Khoảng cách:** HSC 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64. **Bo góc:** 4 (control) · 8 (khối) · 12 (lớp nổi).
- **Chuyển động:** 120 / 160 / 200 ms, vào `cubic-bezier(.2,0,0,1)`, ra nhanh hơn; tôn trọng `prefers-reduced-motion`;
  đổi theme bằng View Transition (có fallback).
- **Thành phần nền tảng:** hộp thoại = `<dialog>` (modal cho xác nhận, `popover` cho bảng điều khiển không chặn trang —
  guide `dialog`); bảng có `role="region"` + `tabindex="0"` khi cuộn ngang, tiêu đề dính, sắp xếp có `aria-sort`
  (guide `responsive-table`, `sortable-table`); khối co giãn theo **container query** thay cho 7 breakpoint
  (guide `size-aware-styling`).
- **Logo công ty:** khe `.logo` cao cố định, `width:auto; object-fit:contain` — không bao giờ kéo giãn.

## 6. Ba hướng

### A — Đài kiểm soát

**Ý tưởng.** Người trực vận hành mở app và thấy cả hệ thống trong một dải: *thanh nhịp hạ tầng* (Capacity · Backup ·
License · Chi phí · Nguồn) với trạng thái, một con số và spark 7 ngày, dính dưới topbar trên mọi trang. Bên dưới là bảng
dày, có chọn nhiều và thao tác hàng loạt. Dial ý định: variance 5 · motion 2 · density 9.

| Mục | Quyết định |
|---|---|
| Nền | Dark mặc định `#000` / bề mặt `n20`; light `#FFF` / `#F4F6F8` |
| Mật độ | Hàng bảng 34 px, padding khối 16 px, gap 12 px |
| Điều hướng | **Rail 76 px icon + nhãn ngắn** (không icon trơn) + tab trên topbar; < 1024 px rail thành thanh dưới, thanh nhịp cuộn ngang |
| KPI | Dải KPI liền khối có thanh bullet (vạch 80 % / 100 %) thay cho thẻ số to |
| Biểu đồ | Small multiples theo cluster, bullet bar, dự phóng có dải THEO DÕI / VƯỢT NGƯỠNG |
| Hợp với | Nhóm hạ tầng trực hằng ngày, màn hình lớn, nhiều dữ liệu |
| Rủi ro | Dày với lãnh đạo; rail 76 px cần học icon; nhiều bảng phải làm lại cột |

### B — Tờ trình sống

**Ý tưởng.** App hiện là một bộ *báo cáo để quyết định*. Mỗi trang mở bằng **một câu kết luận** cỡ lớn (Inter Display
28–36 px) có vạch trạng thái, rồi danh sách việc / quyết định **xếp hạng** (số thứ tự = mức ưu tiên), rồi bằng chứng
theo từng mục (nhãn trái 220 px, nội dung phải). Web và PDF dùng **cùng bố cục**. Dial ý định: variance 6 · motion 3 ·
density 4.

| Mục | Quyết định |
|---|---|
| Nền | Như A; light trông như giấy báo cáo |
| Mật độ | Thoáng: cột tối đa 1 180 px, khoảng giữa mục 32–48 px, chữ dẫn 18 px |
| Điều hướng | **Menu ngang 2 miền** dạng popover (anchor positioning, có fallback) + breadcrumb + tab gạch chân; không sidebar |
| Lọc | Bộ lọc dạng câu: “Hiện [khoản cần theo dõi] của [mọi NCC], loại [bất kỳ], năm tài chính [2026].” |
| Opex | **Dòng hạn theo tháng** (ngày to bên trái, số tiền phải), bảng là chế độ phụ |
| Biểu đồ | Một đường, ghi chú trực tiếp, không chú giải; bảng chỉ đường kẻ ngang |
| Hợp với | Lãnh đạo, người duyệt ngân sách; đồng bộ với báo cáo PDF |
| Rủi ro | Ít dữ liệu trên một màn; danh sách dài (job backup, VM) vẫn cần dạng bảng của A / C |

### C — Bàn xử lý

**Ý tưởng.** App là nơi *xử lý việc*: bên trái là danh sách (hộp việc, cluster, Opex) nhóm theo mức / thời hạn, có
chế độ xem đã lưu; bên phải là **đối tượng đang chọn** với “Nên làm gì”, số liệu, việc liên quan (VM → backup, Opex →
thiết bị, cluster → Capex nháp) và nút hành động. Bàn phím: J / K chuyển, E xong, H ẩn 7 ngày. Hiện thực trọn
“một đối tượng — một trang” của `super-app.md`. Dial ý định: variance 4 · motion 4 · density 6.

| Mục | Quyết định |
|---|---|
| Nền | Như A |
| Mật độ | Hàng danh sách 2 dòng (~64 px), chi tiết thoáng |
| Điều hướng | **Sidebar 232 px** nhóm chữ thường + **số việc** từng mục; chế độ xem đã lưu dạng pill trên đầu; < 1024 px: sidebar thành drawer, chi tiết mở toàn màn hình |
| Danh sách | `role="listbox"` + `aria-activedescendant`; chi tiết cập nhật bằng View Transition, `aria-live="polite"` |
| Biểu đồ | Nằm trong chi tiết đối tượng: bullet CPU / RAM, dự phóng riêng từng cluster |
| Hợp với | Mọi vai trò dùng hằng ngày; hộp việc, Opex, hợp đồng, báo giá đều là danh sách → chi tiết |
| Rủi ro | Trang dashboard thuần (Capacity tổng, Backup tổng quan) cần thêm chế độ “toàn màn”; làm lại nhiều nhất ở JS |

### So sánh nhanh

| Tiêu chí | A | B | C |
|---|---|---|---|
| Tốc độ xử lý việc hằng ngày | Tốt | Trung bình | **Tốt nhất** |
| Đọc nhanh cho lãnh đạo | Trung bình | **Tốt nhất** | Tốt |
| Gần bản PDF (một ngôn ngữ thiết kế) | Thấp | **Cao** | Trung bình |
| Mật độ dữ liệu | **Cao** | Thấp | Trung bình |
| Thay đổi so với hiện tại / công sức | Trung bình (M) | Trung bình (M) | Lớn (L) |
| Màn hình < 1024 px | Thanh dưới | **Tự nhiên** (một cột) | Drawer + chi tiết toàn màn |

**Đề xuất:** **C làm khung** (sidebar có số việc, danh sách → chi tiết cho Trang chủ, Chi phí, Hợp đồng, Báo giá, Tài sản),
**mượn khối “câu kết luận” của B** cho Điều hành và trang tổng quan từng module (cũng là phần mở đầu PDF), và **bullet bar
+ small multiples của A** cho Capacity / Backup. Nếu cần một hướng thuần, chọn C.

## 7. Cần anh/chị quyết

1. Chọn hướng: **A**, **B**, **C**, hay **C + khối kết luận của B + biểu đồ của A** (đề xuất).
2. Font PDF: giữ Roboto / Noto Sans trong Docker, hay thêm Inter để web và PDF cùng font.
3. Duyệt danh sách sửa backend và 4 câu hỏi ở mục 3 của [`docs/audit-2026-11-15.md`](../../audit-2026-11-15.md).
4. Trường “Ai xử lý” / “tuổi” của mục hộp việc (có trong bản mẫu A, C) là **trường mới** — có muốn thêm không
   (cần thêm cột, không đổi số liệu).
