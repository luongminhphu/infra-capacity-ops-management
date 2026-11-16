---
name: hsc-design-system
description: Dùng khi sửa màu / font / bảng / thẻ số / biểu đồ SVG của báo cáo PDF (report_pdf.py, pdf_kit.py, report_*.py) hoặc giao diện file Excel (excel_style.py, excel_*.py) — HSC Design System (màu HSC trên nền trắng, Inter, Calibri), trạng thái 4 mức trên giấy, định dạng số vi-VN. Giao diện web KHÔNG thuộc skill này: đọc design-system/ico/MASTER.md.
---

# HSC Design System — chỉ cho báo cáo PDF / Excel

**Phạm vi:** báo cáo PDF (WeasyPrint) và file Excel (openpyxl). Từ vòng 2 (16/11/2026) web có bộ nhận diện riêng
(“Phòng máy”, Be Vietnam Pro) — **quy tắc web nằm ở [`design-system/ico/MASTER.md`](../../../design-system/ico/MASTER.md)**,
không ở skill này. Không chép màu web sang báo cáo và ngược lại (MASTER §1, §9).

Bố cục / văn phong báo cáo PDF: xem thêm skill `pdf-report`.

## Màu

- PDF: chỉ dùng `COLORS` trong `backend/app/report_pdf.py` (vai trò HSC trên nền trắng: `ink #0B2D5C`, `muted #465F83`,
  `faint #597090`, `ok #187A2C`, `bad #B82E37`, `crit #8F282D`, `crit_solid #B82E37`, `series1…3`…).
- Excel: chỉ dùng hằng trong `backend/app/excel_style.py` (`NAVY`, `INK`, `MUTED`, `OK`, `WARN`, `CRIT`, `SERIES`…).
- Không thêm hex rời trong `report_*.py` / `excel_*.py`; cần màu mới thì thêm vào `COLORS` / `excel_style.py`, đo tương phản
  trên nền trắng (chữ ≥ 4,5 : 1, glyph / viền ≥ 3 : 1).
- Biểu đồ SVG: tối đa 3 series màu (`series1…3`) + xám cho “Khác / tổng”; không dùng màu trạng thái cho series; một trục Y.

## Chữ

- PDF: **Inter** (gói Debian `fonts-inter` trong `backend/Dockerfile`), dự phòng Noto Sans; mã đối soát DejaVu Sans Mono.
  Excel: Calibri. Font mới phải có đủ dấu tiếng Việt.
- **Số trong chữ viết kiểu vi-VN** (`1.234.567`, `1.234,5`, `9,6%`, `0,64x`): dùng `fmt_num` / `fmt_int` / `fmt_pct` của
  `report_pdf.py` hoặc `vn_num` / `vn_g` (`backend/app/shared/text.py`). Không dùng `f"{x:,.1f}"` cho chữ người đọc.
  Giữ số thuần cho toạ độ SVG, độ rộng CSS, công thức Excel; ô số Excel vẫn là số (`number_format`).
  Câu do engine Sizing sinh ra (`sizing.py`, `sizing_advisory.py`) đã viết số vi-VN; báo cáo in nguyên văn, không đổi dấu
  bằng regex. Mã / khoá (vd `2x32x1024`) giữ số thuần.

## Trạng thái

- 4 mức AN TOÀN / THEO DÕI / CẢNH BÁO / VƯỢT NGƯỠNG + NA, nhãn lấy từ `capacity.py` / `derived_status`, không tự đặt.
- PDF vẽ bằng `report_pdf.status_pill()` / `pdf_kit.status_pill` — glyph SVG inline + nhãn, đọc được khi in trắng đen.
- Mức ưu tiên (cao / trung bình / thấp) không phải trạng thái.

## Bảo mật

- Mọi chuỗi từ vCenter / NetBackup / người dùng đưa vào HTML của PDF qua `esc()`.
- WeasyPrint chỉ được tải `data:` URL đã kiểm (logo); không tạo `<img src>` / `url()` từ dữ liệu ngoài.

## Kiểm tra trước khi xong

- [ ] Xuất PDF / Excel ở chế độ DEMO; số liệu khớp web (cùng giá trị, cùng cách viết vi-VN).
- [ ] `pdftotext` không còn số thập phân kiểu Anh trong chữ (`grep -E "\b[0-9]+\.[0-9]{1,2}(%|x| GB)"`).
- [ ] Không hex mới ngoài `COLORS` / `excel_style.py`; tương phản đạt AA trên nền trắng.
- [ ] `python -m pytest tests -q`.
