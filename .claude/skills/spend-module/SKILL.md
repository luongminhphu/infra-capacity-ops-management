---
name: spend-module
description: Dùng khi sửa module Chi phí & Hợp đồng IT (`spend`) — backend/app/spend/ (models, service, fiscal, roles, files, notify, reminders, capex, contracts, quotes, budget_plan, importer, proposal_doc, renovation), routers/spend*.py, report_spend.py / report_renovation.py, excel_spend.py / excel_quotes.py / excel_budget_plan.py / excel_renovation.py, frontend/js/views/spend*.js. Covers Opex / Capex / hợp đồng / đợt thanh toán / báo giá / tờ trình / kế hoạch năm sau / nhập Excel: tiền VND, năm tài chính, phân quyền 4 mức, trạng thái suy ra, nhắc hạn idempotent, upload an toàn.
---

# Module Chi phí & Hợp đồng IT (`spend`)

Thiết kế gốc (đã duyệt): `docs/design/spend-module.md`; phần mở rộng: `spend-vendor-quotes.md`,
`spend-budget-plan.md`, `spend-import.md`. Đọc mục liên quan **trước** khi đổi schema hoặc luồng nghiệp vụ.

## Bản đồ

| Phần | Backend | Router | Web | Báo cáo |
|---|---|---|---|---|
| Opex (khoản định kỳ) | `spend/service.py`, `models.py` | `routers/spend.py` | `views/spend.js` | `report_spend.py`, `excel_spend.py` |
| Capex → Opex dự kiến | `spend/capex.py` | `spend_capex.py` | `spend_capex.js` | |
| Hợp đồng (version, Final, bản ký, xoá version cũ) | `spend/contracts.py` | `spend_contracts.py` | `spend_contract.js` | |
| Đợt thanh toán + checklist hồ sơ | — | `spend_payments.py` | `spend_payment.js` (trong hợp đồng), `home.js` (`renderPaymentsPage`) | |
| Báo giá / NCC / phiếu đánh giá | `spend/quotes.py` | `spend_quotes.py` | `spend_quote.js` | `excel_quotes.py` |
| Tờ trình (.docx từ mẫu) | `spend/proposal_doc.py`, `ai.py` | `spend_proposals.py` | `spend_proposal.js` | |
| Kế hoạch năm sau | `spend/budget_plan.py` | `spend_plans.py` | `spend_plan.js` | `excel_budget_plan.py` |
| Nhập Opex / Capex từ Excel | `spend/importer.py` | `spend_import.py` | `spend_import.js` | |
| Cải tạo văn phòng | `spend/renovation.py` | `spend_renovations.py` | `spend_renovation.js` | `report_renovation.py`, `excel_renovation.py` |
| Nhắc hạn, kênh, SMTP, xoá file | `reminders.py`, `notify.py`, `files.py`, `settings.py` | `spend_admin.py` | `views/spend.js` (Cài đặt) | |

Mọi router có `dependencies=[Depends(require_feature("spend"))]`. Web nạp lười: module khác gọi hàm spend
phải qua `uiRequire('spend').then(...)`.

## Quy tắc bắt buộc

1. **Tiền là số nguyên đồng** (`BigInteger`), không float. Tỷ lệ `Decimal`, làm tròn `ROUND_HALF_UP`
   (`service.gross_of`, `budget_plan.uplifted`). Đọc số từ người dùng / Excel qua `service._money` hoặc
   `shared/text.parse_number`, không `float()`.
2. **Năm tài chính** đặt tên theo năm bắt đầu: `fiscal.fiscal_year(d, start_month)`; cộng năm bằng
   `fiscal.add_years` / `term_end` (29/02 → 28/02). `fiscal_year` **được lưu** trên dòng — đổi
   `start_month` không được dịch dữ liệu cũ. Ngày nghiệp vụ dùng `Date`, "hôm nay" dùng `fiscal.today_vn()`.
3. **Trạng thái suy ra không lưu**: "Sắp hết hạn" / "Quá hạn" và tone 4 mức tính trong
   `service.derived_status` (crit = quá hạn, bad ≤ mốc nhỏ nhất, warn ≤ mốc lớn nhất). Web / PDF / Excel /
   hộp việc dùng chung kết quả này, không tự tính lại.
4. **Đổi trạng thái chỉ bằng lệnh** (`confirm`, `renew`, `stop`, `reactivate`, `negotiate`, `finalize`,
   `sign`, `submit`, `paid`…), không qua PUT chung. Schema tạo / sửa không nhận `code`, `status`,
   `origin`, `confirmed_*` (chống mass-assignment).
5. **Phân quyền ở endpoint**: `Depends(require_spend("viewer"|"editor"|"approver"|"admin"))` từ
   `spend/roles.py` (none < viewer < editor < approver; admin vượt tất cả). Ẩn nút trên web (`spCan`) chỉ là
   phụ. Cấu hình kênh / SMTP / AI / danh mục, xoá, huỷ xoá file → `admin`.
6. **Đồng thời**: sửa bản ghi gửi kèm `rowVersion` (lệch → 409); upload version hợp đồng dùng UPDATE có điều
   kiện + UQ `(contract_id, version_no)`; mã `OPX-/CPX-/HD-/TT-/BG-<năm>-<số>` sinh bằng `codes.next_code`
   trong cùng transaction; gia hạn có UQ `renewed_from_id`.
7. **Nhắc hạn idempotent**: UQ `(opex_id, end_date, threshold_days, channel_id)` trong `spend_reminder_log`;
   INSERT `pending` → commit → gửi → cập nhật. Chỉ gửi mốc nhỏ nhất đến hạn (catch-up), at-most-once.
8. **Upload**: chỉ qua `files.read_upload` + `files.store` (giới hạn `SPEND_MAX_UPLOAD_MB`, kiểm nội dung bằng
   `shared/filetype.py`, .docx chặn macro / zip bomb / `..`; .xlsx qua `shared/xlsx_io.py`). Tên file người
   dùng chỉ là metadata; trên đĩa là uuid. File đã xoá giữ dòng metadata (`purged_at`), tải về trả 410.
9. **Ra ngoài**: Discord chỉ URL `discord.com/api/webhooks/…`, không theo redirect, `allowed_mentions`
   rỗng, escape markdown (`notify.discord_escape`); email bỏ CR/LF ở header, HTML qua `esc()`. AI chỉ qua
   `ai_platform.run()` / `shared/ai_transport.py`, tôn trọng `allow_external`, che tên.
10. **Bí mật** (webhook, mật khẩu SMTP, API key): `secrets_box.encrypt`; API chỉ trả `hasWebhook` /
    `hasPassword`; audit ghi "đã đổi", không ghi giá trị.
11. **Audit** mọi thay đổi bằng `service.audit(...)` (`action="spend.*"`, `before/after` cho tiền, ngày,
    trạng thái) trong cùng transaction.
12. **Schema**: không Alembic — bảng mới qua `create_all()`, cột thêm vào bảng cũ qua `additive` trong
    `database.py::ensure_schema()` (nullable hoặc có DEFAULT, chạy cả SQLite và PostgreSQL).

## Giao diện (xem `design-system/ico/pages/danh-sach.md`)

- Trang danh sách dùng khung **danh sách → chi tiết**: `.hm-split.panel.sp-split`, dòng `.sp-li`
  (`role="option"`), glyph `.hm-lv.lv-<tone>` + nhãn ẩn, nhóm `.hm-group` có tổng tiền, chế độ xem `.hm-view`.
- Chi tiết mở bằng `spPaneOrDialog(paneSel, titleHtml, code, body, actions)` — khung bên phải từ 1024 px,
  hộp thoại khi nhỏ hơn hoặc khi mở từ trang khác. Phím: `spListKeys` (J / K / Enter), chọn dòng:
  `spListSelect`.
- Tiền hiển thị `spMoney` / `uiMoney`; ngày `spDate`; nhãn trạng thái từ `derived_status`
  (`displayLabel`, `tone`) qua `spPill` / `uiPill`. Không tự đặt nhãn, không hard-code màu.
- Mọi chuỗi từ người dùng / NCC / file đưa vào HTML qua `esc()`; link do người dùng nhập chỉ khi `http(s)://`.
- Form, bộ lọc, nhãn, nút theo skill `web-layout` (MASTER §6 “Form, nhãn và độ dài chữ”): ô cùng hàng thẳng hàng
  (`.form-grid-aligned`), nhãn một dòng không “(tuỳ chọn)”, chữ tuỳ chọn select ngắn (VD bộ lọc Opex “Cần theo dõi”,
  giải thích ở `title`), không cuộn ngang ở 390 px.
- Chọn file (hợp đồng, bản ký, báo giá, đính kèm Opex, cải tạo): `uiDropzone(id, …)` hoặc nút `.btn` bọc
  `<input type="file" hidden>` — không để lộ ô chọn file gốc (`test_frontend_markup.py` kiểm).

## Kiểm tra trước khi xong

- [ ] Endpoint mới có `require_spend(...)` đúng mức; thao tác cấu hình → `admin`.
- [ ] Tiền: không float; có test số cụ thể (vd 1.234.567.891 đ × 15 % = 185.185.184 đ) trong
      `tests/test_spend_*.py` cho mọi logic tính mới (`budget_plan`, `capex`, `quotes.check`, `importer`).
- [ ] Đổi trạng thái qua lệnh, có audit, có `rowVersion`.
- [ ] Upload mới đi qua `files.store`; file Excel qua `shared/xlsx_io.py`.
- [ ] Web / PDF / Excel cùng số (cùng hàm trong `spend/`); PDF / Excel theo HSC (`report_pdf.COLORS`,
      `excel_style.py`).
- [ ] Giao diện đổi: `ui_audit.js` (skill `web-layout`) ở 1440 + 390 px cho trang Chi phí / Hợp đồng / Mua sắm đã sửa.
- [ ] `python -m pytest tests -q` (gồm `test_spend_*`) xanh.
