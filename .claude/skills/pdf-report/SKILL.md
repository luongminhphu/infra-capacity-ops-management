---
name: pdf-report
description: Dùng khi thêm hoặc sửa báo cáo PDF (WeasyPrint, khổ A4) — backend/app/report_pdf.py, report_planning.py, report_sizing*.py, report_license.py, report_backup.py, report_view.py, routers/reports.py — hoặc báo cáo Excel đi kèm. Covers layout, SVG charts, colors, Vietnamese writing style for executive/technical reports.
---

# Báo cáo PDF của ICO

Báo cáo là sản phẩm chính đưa lên lãnh đạo để **duyệt quyết định đầu tư**. Ưu tiên: đúng số,
ngắn, nêu thẳng vấn đề → quyết định đề nghị → thời điểm.

## Kiến trúc

- Mỗi báo cáo = một hàm `render_*_html(...) -> str` + `build_*_pdf(...)` gọi
  `html_to_pdf_bytes()` trong `report_pdf.py`. Không gọi `weasyprint.HTML` ở chỗ khác.
- Dùng lại helper của `report_pdf.py`: `_base_css()` (A4, lề 16/14/18/14 mm, header/footer có
  số trang), `_exec_visual_css()`, `COLORS`, `STATUS_COLOR`, `status_pill()`, `bar_cell()`,
  `svg_gauge()`, `svg_donut()`, `svg_bar_chart()`, `svg_ranked_bar_chart()`, `svg_line_chart()`,
  `_stat_tiles_html()`, `fmt_num()` / `fmt_int()` / `fmt_pct()`.
- Biểu đồ vẽ bằng **SVG inline** sinh trong Python — không dùng thư viện JS, không ảnh raster.
- Số liệu lấy từ cùng hàm nghiệp vụ mà web dùng (`capacity`, `planning`, `backup/analysis`...),
  không tính lại trong file report. Xem skill `capacity-math`.

## Bảo mật khi dựng HTML

- **Mọi** chuỗi từ vCenter / NetBackup / người dùng (tên VM, host, cluster, policy, scope label,
  tên file) phải qua `esc()` trước khi vào HTML hoặc SVG.
- WeasyPrint tự tải mọi URL trong HTML/CSS (`<img src>`, `url()`, `@import`, và cả tham chiếu
  bên trong ảnh SVG). Không bao giờ đưa URL do người dùng / hệ thống ngoài cung cấp vào báo cáo.
  Logo chỉ là `data:` URL đã kiểm tra MIME + base64 (`routers/settings.py`). Logo SVG có thể chứa
  tham chiếu ra ngoài — nếu đụng tới phần này, cân nhắc chặn bằng `url_fetcher` chỉ cho `data:`.

## Màu & chữ trên PDF

- Nguồn sự thật: `design-system/ico/MASTER.md` (mục 3, 8, 9). PDF nền trắng dùng `COLORS` trong `report_pdf.py` =
  vai trò light-theme của HSC quy ra hex (ink `#0B2D5C`, primary `#165AB8`, logo `#074B9B`, ok `#187A2C`,
  bad `#B82E37`, crit `#8F282D`, `crit_solid` `#B82E37`, series `series1…3`). Không thêm mã màu rời.
- 4 mức trạng thái AN TOÀN / THEO DÕI / CẢNH BÁO / VƯỢT NGƯỠNG và nhãn mức độ `SEVERITY_LABEL_VI`
  (Nghiêm trọng / Cảnh báo / Thông tin) phải khớp web. `status_pill()` vẽ glyph + chữ mực; VƯỢT NGƯỠNG là khối đặc.
- Font trong image Docker: **Inter** (`fonts-inter`, cùng font với web) + Noto Sans dự phòng; mã đối soát dùng
  DejaVu Sans Mono. Thêm font mới → thêm gói vào `backend/Dockerfile`.
- Biểu đồ: tối đa 3 series màu + xám, không dùng màu trạng thái cho series (trừ khi dữ liệu là trạng thái), một trục Y.
- Gauge: CPU/RAM vùng 0–80 ok, 80–100 warn; Storage 0–80 / 80–90 / 90–100.
- Trạng thái trong PDF **chỉ** qua `status_pill()` (hoặc `pdf_kit.status_pill`) — không tự dựng `<span class="pill" style=…>`.
  Thông tin không phải trạng thái (edition, power state, HA/DRS) dùng nhãn trung tính viền mảnh.
- Khối “Tổng kết” (`.exec-brief`) là khối kết luận như web: nền trắng, vạch trái 4 px theo trạng thái, câu kết luận 15 px.
- So sánh % ngưỡng theo cluster dùng `cluster_bullets_html()` / `svg_bullet()` (vạch 80 % / 100 %), không dùng cột nhóm.
- Không viết hoa toàn bộ nhãn (`text-transform:uppercase`); Excel cũng vậy (`excel_style`).

## Quy ước nội dung

- **Báo cáo Vận hành** (`view=executive`): 2 trang, tối đa 3. Trang 1 mở bằng hộp
  **Tổng kết — vấn đề & quyết định đề nghị** (trạng thái + 1 câu kết luận + danh sách đánh số
  "pain point → quyết định · thời điểm" + 5 ô số liệu), bảng **Vấn đề cần quyết định** tối đa 6
  dòng. Ít chữ, không mô tả kỹ thuật.
- **Báo cáo Kỹ thuật**: cùng phần mở đầu, rồi chi tiết overcommit theo cluster, host, datastore,
  toàn bộ findings sắp theo mức độ (nghiêm trọng trước), có cột **Mã** đối soát.
- Mọi khuyến nghị dẫn chiếu **mã đối soát** để tra ngược sang tab Compliance trên web.
- Khi `include_storage` tắt: bỏ toàn bộ KPI/biểu đồ/bảng storage và ghi rõ Storage ngoài phạm vi
  (`_storage_excluded_html()`).
- Thiếu dữ liệu (chưa có lịch sử, không có cluster) → hiện trạng thái rỗng có giải thích, không
  để trang trắng hay lỗi.
- Câu chữ tiếng Việt có dấu, câu ngắn, động từ hành động ("Duyệt", "Bổ sung", "Thu hồi").
- Nhãn ngắn như web (MASTER §6 “Form, nhãn và độ dài chữ”, skill `web-layout`): tiêu đề cột / ô số / tiêu đề khối chỉ gồm
  tên + đơn vị, một dòng; giải thích đưa xuống chú thích dưới bảng hoặc câu dẫn. Cùng một trường dùng **cùng tên** trên
  web, PDF và Excel (VD “Tăng trưởng %/quý”, “Mua sắm + triển khai”, “Mục tiêu % ngưỡng”).
- Không cắt tên đối tượng (VM, host, NCC) bằng “…”: cho xuống dòng (`overflow-wrap:anywhere`) hoặc thu hẹp cột khác.

## Kiểm tra trước khi xong

- [ ] Xuất thử PDF ở chế độ DEMO (4 vCenter demo) với cả phạm vi `all` và một cluster; mở file,
      kiểm tra số trang, ngắt trang, không tràn bảng, dấu tiếng Việt hiển thị đúng.
- [ ] Số liệu trên PDF khớp số trên web cho cùng phạm vi.
- [ ] Tiêu đề cột không xuống quá 2 dòng; không tên đối tượng nào bị cắt; nhãn trùng tên với web.
- [ ] Mọi chuỗi ngoài đã qua `esc()`; không có URL ngoài trong HTML.
- [ ] Cập nhật mục báo cáo tương ứng trong `docs/huong-dan-chi-tiet.md` nếu đổi bố cục / tham số.
