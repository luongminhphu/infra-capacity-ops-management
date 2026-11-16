---
name: hsc-design-system
description: Dùng khi thêm hoặc sửa giao diện web (frontend/index.html, css/styles.css, js/views/*.js, app.js, ui.js), màu / chữ của biểu đồ, hoặc màu / font của báo cáo PDF / Excel — ICO design system (web: bảng màu "Phòng máy" + Be Vietnam Pro; PDF/Excel: HSC + Inter), trạng thái 4 mức, bố cục danh sách → chi tiết, khối kết luận. Use for any UI / styling / chart / report color change.
---

# ICO design system (web riêng; báo cáo theo HSC)

**Nguồn sự thật duy nhất: [`design-system/ico/MASTER.md`](../../../design-system/ico/MASTER.md).**
Trước khi sửa một trang, đọc MASTER rồi xem `design-system/ico/pages/<trang>.md` — nếu có, quy tắc trang đó ghi đè MASTER.
Bối cảnh, chấm điểm và bản mẫu: `docs/design/redesign/`.

## Tóm tắt phải nhớ

- **Web** dùng bảng màu “Phòng máy” + Be Vietnam Pro qua token ngữ nghĩa trong `frontend/css/styles.css`
  (`--bg-*`, `--text-*`, `--brand`, `--status-ok/-warn/-bad/-crit`, `--chart-s*`). Tên HSC cũ (`--sb60`, `--blue-a12`…) chỉ là alias.
  **PDF / Excel** giữ HSC (`COLORS` trong `report_pdf.py`, `excel_style.py`, Inter). Không hard-code mã màu trong JS / HTML.
- **AA bắt buộc**: chữ phụ / chữ mờ dùng `--text-secondary` / `--text-tertiary`. Nút chính dùng `--btn-primary-bg`.
- **Trạng thái**: chỉ nhãn từ `getStatusForPct` / `combineStatus` / `derived_status`; hiển thị bằng `uiPill(tone, label)`
  (glyph + nền + độ nặng; chữ mực; VƯỢT NGƯỠNG là khối đặc). Không tô màu chữ nhãn nhỏ theo trạng thái.
- **Font**: web Be Vietnam Pro + JetBrains Mono (mã máy) tự host trong `frontend/vendor/`, không CDN. PDF dùng Inter từ `fonts-inter` (Dockerfile).
- **Biểu đồ**: màu qua `chartPalette()` (`series[0..2]` + `series[3]` xám), không màu trạng thái cho series, một trục Y,
  ngưỡng bằng `bandWarn` / `bandBad`, luôn có chú giải khi ≥ 2 series.
- **Bố cục**: sidebar có số việc; danh sách → chi tiết (`role="listbox"`); khối kết luận ở trang tổng quan; phạm vi nằm
  trong trang, không trong topbar.
- **Bảo mật**: chèn dữ liệu ngoài vào template luôn qua `esc()`; link do người dùng nhập chỉ render khi `http(s)://`.
- **Chữ**: tiếng Việt có dấu, câu ngắn, động từ hành động; dùng `design-skills:ux-writing` cho lỗi / trạng thái rỗng.

## Kiểm tra trước khi xong

Chạy checklist mục 11 của MASTER.md, và:

- [ ] `python -m pytest tests/test_frontend_markup.py -q` pass.
- [ ] Chụp dark + light, 1440 px và 820 px ở chế độ DEMO; số liệu không đổi so với trước.
- [ ] Đổi màu → kiểm tương phản (≥ 4,5 chữ, ≥ 3 glyph / viền); đổi màu series → chạy validator của skill `dataviz`.
