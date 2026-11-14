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

- PDF nền trắng dùng `COLORS` trong `report_pdf.py` (brand `primary` #2681FF, `primary_dark`
  #074B9B; trạng thái `ok/warn/bad/crit/na` + `*_bg`). Không thêm mã màu rời rạc.
- 4 mức trạng thái AN TOÀN / THEO DÕI / CẢNH BÁO / VƯỢT NGƯỠNG và nhãn mức độ
  `SEVERITY_LABEL_VI` (Nghiêm trọng / Cảnh báo / Thông tin) phải khớp web.
- Font trong image Docker: Roboto + Noto Sans (đủ dấu tiếng Việt), mã đối soát dùng
  DejaVu Sans Mono. Thêm font mới → thêm gói vào `backend/Dockerfile`.
- Gauge: CPU/RAM vùng 0–80 ok, 80–100 warn; Storage 0–80 / 80–90 / 90–100.

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

## Kiểm tra trước khi xong

- [ ] Xuất thử PDF ở chế độ DEMO (4 vCenter demo) với cả phạm vi `all` và một cluster; mở file,
      kiểm tra số trang, ngắt trang, không tràn bảng, dấu tiếng Việt hiển thị đúng.
- [ ] Số liệu trên PDF khớp số trên web cho cùng phạm vi.
- [ ] Mọi chuỗi ngoài đã qua `esc()`; không có URL ngoài trong HTML.
- [ ] Cập nhật mục báo cáo tương ứng trong `README.md` nếu đổi bố cục / tham số.
