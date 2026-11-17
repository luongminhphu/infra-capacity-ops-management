---
name: web-layout
description: Dùng khi thêm hoặc sửa form, bộ lọc, lưới ô nhập, nhãn, tiêu đề panel, nút, thẻ KPI, ô chọn file hoặc bố cục trang của giao diện web (frontend/index.html, frontend/js/views/*.js, frontend/css/*.css). Covers căn hàng cột ô nhập, độ dài nhãn / tiêu đề / tuỳ chọn select, chống cắt chữ và cuộn ngang ở 390 px, uiDropzone thay ô chọn file gốc, và script audit tự động (Playwright) chạy trước khi giao.
---

# Bố cục web — căn hàng, độ dài chữ, khổ điện thoại

Quy tắc gốc (có **Nguồn**) nằm ở [`design-system/ico/MASTER.md`](../../../design-system/ico/MASTER.md) §6 “Form, nhãn và
độ dài chữ” và checklist §11. Skill này là cách làm + công cụ kiểm. Báo cáo PDF / Excel: skill `pdf-report`,
`hsc-design-system` (cùng nguyên tắc nhãn ngắn, không cắt tên).

## 1. Quy tắc (tóm tắt MASTER §6)

| # | Quy tắc | Làm thế nào |
|---|---|---|
| L1 | Ô nhập cùng một hàng phải **thẳng hàng** dù nhãn dài ngắn khác nhau | Lưới form dùng `.form-grid-aligned` (subgrid nhãn · ô · trợ giúp); lưới cũ (`flex`, `.sim-form-grid`, `.rpt-opt-grid`) thì `align-items:end` |
| L2 | **Nhãn ≤ ~24 ký tự, một dòng** ở khổ máy tính; chỉ chứa *tên trường + đơn vị* (`(%)`, `(GB)`, `(năm)`) | Không “(tuỳ chọn)”, không ví dụ, không giải thích trong nhãn |
| L3 | “Tuỳ chọn”, ví dụ, đơn vị tiền → `placeholder`; giải thích ngắn → `title` của nhãn; giải thích **cần thiết** → dòng trợ giúp `.field-help` / `panel-sub` (title không hiện trên điện thoại) | VD `Đơn giá / host` + placeholder `VND`; `Tăng trưởng %/quý` + placeholder `thấp,cơ sở,cao` |
| L4 | Chữ tuỳ chọn của `<select>` vừa ô (≤ ~28 ký tự trong cột 240 px) | Rút gọn chữ hiển thị, **giữ nguyên `value`** gửi backend; tên đầy đủ đặt `title` của option. VD bỏ tiền tố hãng “VMware …” vì đã chọn nền tảng |
| L5 | Tiêu đề panel / thẻ ≤ 1 dòng: tên ngắn, câu giải thích xuống `panel-sub` | “VM đổi tên” + mô tả “Không tính là thêm mới / loại bỏ…” |
| L6 | Không cắt chữ thiết yếu (tiêu đề, nút, tên đối tượng, lỗi) bằng “…” — cho xuống dòng | `.muted-sub` mặc định 1 dòng; **dạng đoạn (`<p>` / `<div>`) tự xuống dòng** (patches.css) |
| L7 | **Không cuộn ngang trang ở 390 px** | Track lưới luôn `minmax(0,1fr)` (không `1fr` trần), con của grid / flex `min-width:0`, bảng rộng nằm trong `.table-scroll`, giá trị kỹ thuật dài `overflow-wrap:anywhere` |
| L8 | Trên điện thoại (< 640 px): ô tên / ghi chú chiếm cả dòng, ô số 2 cột | VD `.sz-profile-grid`, `.pay-def-row` |
| L9 | Dải KPI dùng `uiKpiStrip`; ô tối thiểu 200 px, giá trị có thể xuống dòng ở dấu cách khi ô < 230 px | Không tự đặt `minmax(<200px…)` cho `.kpi-strip` |
| L10 | **Không dùng ô chọn file gốc** (nút “Choose File” tiếng Anh) | `uiDropzone(id, title, sub, accept)` (ui.js) — giữ `id`, handler `change` cũ chạy nguyên; hoặc nút `.btn` bọc `<input type="file" hidden>` |
| L11 | Nút: một dòng, động từ + tân ngữ ngắn (“Upload v3”, “Xem trước”) | Nhãn dài → `title` hoặc rút gọn |
| L12 | Một hành động / trường giữ **một tên** ở mọi nơi (trang Quy hoạch và Tuỳ chọn báo cáo dùng cùng nhãn) | Sửa nhãn ở một chỗ thì `grep` chỗ còn lại (`index.html` + `js/views`) |

## 2. Mẫu code

```html
<!-- lưới form mới: nhãn, ô, trợ giúp thẳng hàng theo hàng -->
<div class="form-grid-aligned sp-form-2">
  <label class="field"><span>Đơn giá / host</span><input placeholder="VND · tuỳ chọn"><span class="field-help">Để trống nếu chưa có báo giá.</span></label>
  <label class="field"><span title="Thời gian từ đề xuất tới khi chạy">Mua sắm + triển khai</span><input type="number" placeholder="tháng"></label>
</div>
```

```js
// ô chọn file: id giữ nguyên, handler cũ ($('#ctFile').files[0] / onchange) không đổi
`${uiDropzone('ctFile', 'Chọn file .docx', 'Bắt buộc · bản Word của version mới', '.docx')}`
// select: chữ ngắn, value đầy đủ
`<option value="${esc(full)}" title="${esc(full)}">${esc(short(full))}</option>`
```

CSS mới cho bố cục thêm vào **cuối `frontend/css/patches.css`**, có comment nói lỗi gì ở trang nào / khổ nào; chỉ dùng token
(`--space-*`, `--control-h-sm|md|lg`, `--fs-*`), không px màu / cỡ chữ rời.

## 3. Kiểm trước khi giao

1. `node --check` các file JS đã sửa; `python -m pytest tests -q` (gồm `test_frontend_markup.py`: không ô chọn file gốc
   hiện ra, không “(tuỳ chọn)” trong nhãn).
2. Chạy demo + audit (cần Playwright, có sẵn trong môi trường cloud):
   ```bash
   ICO_PASS='<mật khẩu admin demo>' bash .claude/skills/web-layout/scripts/run_demo.sh 8765 &
   ICO_PASS='<mật khẩu admin demo>' node .claude/skills/web-layout/scripts/ui_audit.js --widths 1440,390 --out /tmp/ui-audit
   ```
   `--pages home,sizing` để chạy vài trang; `--theme light`; `--json` lấy số liệu thô. Script đo 44 trang:
   **LỆCH** (ô nhập cùng hàng lệch > 3 px), **CẮT** (chữ bị `overflow:hidden` / ellipsis, tuỳ chọn select rộng hơn ô),
   **DÀI** (nút / pill / tab > 1 dòng; nhãn / tiêu đề / `th` > 2 dòng), **cuộn ngang**. Exit 1 khi có cuộn ngang hoặc lỗi JS.
3. Mục còn lại phải giải thích được (VD giá trị dữ liệu dài trong ô nhập là bình thường) và **xem ảnh chụp** ở `--out`,
   dark + light, 1440 + 390 — số đo không thay được mắt nhìn.
4. Thay đổi bố cục đáng kể: lưu ảnh trước / sau vào `docs/design/redesign/previews/<chủ đề>/`.
