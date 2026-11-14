---
name: hsc-design-system
description: Dùng khi thêm hoặc sửa giao diện web (frontend/index.html, css/styles.css, js/views/*.js, app.js) hoặc màu sắc/kiểu chữ của biểu đồ — HSC design system tokens, dark/light theme, status colors, typography, spacing, sidebar layout. Use for any UI / styling / chart color change.
---

# HSC Design System trong ICO

Nguồn token duy nhất cho web: khối `:root` và `[data-theme="light"]` trong
`frontend/css/styles.css`. **Không hard-code mã màu, cỡ chữ, khoảng cách mới** trong JS/HTML —
dùng `var(--token)`. Cần token mới thì thêm vào `:root` (và bản light) theo đúng thang có sẵn.

## Theme

- **Dark mặc định**: nền app `--n10` (#000), bề mặt `--bg-surface` = `--n20` (#14171A),
  bề mặt 2 `--n30`, viền `--n50`. Chữ chính `--cm10` (#FFF), phụ `--neutral-a52`, mờ `--neutral-a32`.
- **Light**: `[data-theme="light"]` ghi đè cùng tên token (chữ chính #0B2D5C, bề mặt #F4F6F8).
  Mọi màu phải đi qua token để tự đổi theo theme — kiểm tra cả hai theme sau khi sửa.
- Brand: `--brand` #2681FF, `--logo-blue` #074B9B, `--brand-deep` #00468C.
  Nút chính `--btn-primary-bg` #3377FF.

## Trạng thái (dùng đúng 4 mức + NA)

| Nhãn | class | màu chữ | nền |
|---|---|---|---|
| AN TOÀN | `ok` | `--ok` (`--status-success`) | `--ok-bg` |
| THEO DÕI | `warn` | `--warn` (`--status-warning`) | `--warn-bg` |
| CẢNH BÁO | `bad` | `--bad` (`--tone-bad`) | `--bad-bg` |
| VƯỢT NGƯỠNG | `crit` | `--crit` (`--status-error`) | `--crit-bg` |
| NA | `na` | `--text-secondary` | `--na-bg` |

Nhãn và class lấy từ `getStatusForPct` / `combineStatus` (core.js) — không tự đặt nhãn mới.
Không chỉ dùng màu để truyền tải trạng thái: luôn kèm chữ (pill) để đọc được khi in đen trắng
và với người mù màu.

## Chữ & số

- Font `--font-body` / `--font-display` = Inter (Google Fonts trong `index.html`); số dùng
  `font-variant-numeric: var(--numeric-features)` (tabular-nums) để cột thẳng hàng.
- Thang chữ: `--fs-caption` 12, `--fs-body-sm` 14 (mặc định body), `--fs-body` 16,
  `--text-lg` 18, `--text-xl` 20, `--fs-body-lg` 24, `--fs-h6` 28.
- Trọng lượng: `--fw-regular/medium/semibold/bold`.

## Khoảng cách, bo góc, đổ bóng, chuyển động

- `--space-1..10` = 4, 8, 12, 16, 24, 32, 48, 64, 72, 100 px.
- `--radius-xs/sm/md/lg/xl/pill` = 2, 4, 8, 12, 20, 100 px.
- Chiều cao control `--control-h-sm/md/lg` = 28 / 36 / 44 px.
- Bóng `--z100/200/400/600`; chuyển động `--motion-fast/base/slow` + `--ease-out`.

## Bố cục & điều hướng

- Sidebar trái `--sidebar-w` 240 px, topbar `--topbar-h` 56 px, nội dung tối đa `--content` 1720 px;
  dưới 1024 px sidebar thành drawer.
- Mỗi trang có route `#/<trang>` (giữ khi F5 / chia sẻ link). Thanh **Phạm vi** chỉ hiện bộ lọc
  trang đó dùng (vCenter / cluster / host).
- Trang mới: thêm vào nhóm điều hướng phù hợp (Tổng quan, Giám sát, Inventory, Quy hoạch,
  License, Backup, Báo cáo & cài đặt) và cập nhật bảng "Cấu trúc giao diện" trong README.

## Biểu đồ

- Màu series: `--chart-portfolio`, `--chart-optimized`, `--chart-index`, rồi các bậc `--sb*`,
  `--sc*`. Trạng thái trên biểu đồ dùng đúng màu trạng thái ở trên.
- Web và PDF phải cùng ý nghĩa màu (xem skill `pdf-report`; PDF dùng `COLORS` riêng cho nền trắng).

## Code giao diện

- Vanilla JS, không framework, không build step. Chèn dữ liệu vào template string **luôn qua
  `esc()`** (định nghĩa ở `core.js`) — tên VM/host/policy đến từ hệ thống ngoài.
- Chữ trên giao diện viết tiếng Việt có dấu, ngắn gọn; dùng `design-skills:ux-writing` khi viết
  thông báo lỗi / trạng thái rỗng.
- `backend/tests/test_frontend_markup.py` kiểm tra thẻ HTML đóng mở đúng — chạy lại sau khi sửa
  `index.html`.

## Kiểm tra trước khi xong

- [ ] Không có mã màu / px mới hard-code ngoài `:root`.
- [ ] Đúng ở cả dark và light theme, và ở màn hình < 1024 px.
- [ ] Độ tương phản chữ đạt WCAG AA (dùng `design-skills:accessibility-audit` nếu đổi màu).
- [ ] `python -m pytest tests/test_frontend_markup.py -q` pass.
