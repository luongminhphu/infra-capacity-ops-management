# Thiết kế: Kế hoạch ngân sách năm sau (Opex / Capex)

**Vị trí:** Ngân sách & chi phí → tab **Kế hoạch năm sau** (`#/budget/plan`).

**Code:**
- Backend: `backend/app/spend/budget_plan.py` (logic), `routers/spend_plans.py` (API), `excel_budget_plan.py` (Excel).
- Frontend: `frontend/js/views/spend_plan.js`.
- Test: `backend/tests/test_spend_budget_plan.py`.

## 1. Vấn đề

Cuối năm, phòng IT phải lập ngân sách năm sau. Hiện tại số liệu nằm rải rác:
- Opex sắp hết hạn cần gia hạn nằm trong danh sách Opex.
- Opex gia hạn do Capex sinh ra nằm ở năm sau.
- Dự án Capex mới vẫn còn ở dạng nháp.

Người lập phải tự gom ra Excel, tự cộng % tăng giá, và không biết sau khi duyệt năm đó đã chi bao nhiêu so với kế hoạch.

## 2. Ý tưởng

Một **kế hoạch** cho mỗi năm tài chính. Hệ thống **tự gợi ý** các dòng từ dữ liệu đang có; con người **điều chỉnh**, **duyệt**, rồi dùng kế hoạch làm **thước đo** trong năm.

| Nguồn dòng | Lấy từ đâu | Số tiền |
|---|---|---|
| **Gia hạn** | Opex *Dự kiến / Đang hiệu lực* của năm trước, chưa gia hạn, có kỳ kế tiếp bắt đầu trong năm kế hoạch. Kỳ tháng / quý / năm giữ đúng lịch (quý: 01/01, 01/04…; 29/02 → 01/03). Kỳ ngắn hơn một năm được cộng đủ số kỳ trong năm. | hiện tại × số kỳ × (1 + % tăng giá) |
| **Đã ghi nhận** | Opex đã thuộc năm kế hoạch: gia hạn sớm, Opex sinh từ Capex, khoản nhập trước cho năm sau. | như đã ghi |
| **Dự án Capex** | Dự án Capex của năm kế hoạch, kể cả bản nháp (đánh dấu "Nháp — chưa duyệt"). | tổng hạng mục (sau VAT chính xác) |
| **Mới đề xuất** | Người dùng nhập: dịch vụ mới, dự án mới. | nhập tay |

Không gợi ý: Opex *một lần*, Opex đã ngừng, kỳ kế tiếp nằm ngoài năm kế hoạch (vd hợp đồng 3 năm còn hạn).

**Điều chỉnh cho từng dòng:**
- % tăng giá riêng (mặc định lấy % của kế hoạch);
- số tiền nhập tay (sau đó dòng không tự tính lại nữa);
- mức ưu tiên *Bắt buộc / Nên có / Tuỳ chọn*;
- đưa vào hoặc bỏ khỏi kế hoạch. Bỏ một dòng gợi ý **phải ghi lý do**, vd ngừng dịch vụ, gộp hợp đồng;
- nhà cung cấp, cost center, ngày bắt đầu / thanh toán.

**Trần ngân sách (tuỳ chọn, sau VAT).** Các dòng được xếp theo ưu tiên rồi theo thứ tự. Dòng nào làm tổng vượt trần được đánh dấu **Vượt trần**. Kết quả là đường cắt để lãnh đạo chọn bỏ dòng nào.

**Phân tích hiển thị:**
- Tổng kế hoạch so với **thực tế năm hiện tại**, tính theo cùng trạng thái với báo cáo ngân sách: Opex *dự kiến / hiệu lực / đã gia hạn*, Capex *đã duyệt* trở đi.
- So sánh theo loại chi phí.
- Phân bổ theo **quý tài chính** (theo ngày bắt đầu, để lên dòng tiền).
- Tiền tăng do tăng giá gia hạn.

## 3. Quy trình

```
Nháp ──Gửi duyệt (Biên tập)──▶ Chờ duyệt ──Duyệt (Duyệt)──▶ Đã duyệt ──Sửa đổi (Biên tập)──▶ v2 Nháp …
  ▲                               │                             │
  └──────Trả lại (Duyệt, lý do)───┘                             └─ duyệt v2 ⇒ v1 "Đã thay thế"
```

- **Làm mới gợi ý** (chỉ khi Nháp): gộp gợi ý mới vào kế hoạch mà không mất phần người dùng đã sửa.
  - Dòng chưa ai sửa đi theo dữ liệu nguồn.
  - Dòng đã sửa vẫn giữ số tiền và quyết định của người sửa, chỉ cập nhật số "hiện tại".
  - Nguồn biến mất (Opex đã gia hạn sớm, dự án bị xoá): dòng chưa sửa thì bị bỏ; dòng đã sửa được đánh dấu **Nguồn đã thay đổi** để người dùng xem lại.
  - Khoản gia hạn sớm xuất hiện thành một dòng **Đã ghi nhận** mới.
- Dòng gợi ý không xoá được, chỉ bỏ khỏi kế hoạch (kèm lý do). Dòng mới đề xuất và dòng có nguồn đã thay đổi thì xoá được.
- Mỗi năm chỉ có tối đa một bản đang soạn hoặc chờ duyệt, và một bản đã duyệt. Năm đã có bản duyệt thì dùng **Sửa đổi**: bản mới copy toàn bộ dòng sang.
- **Tạo Opex / Capex từ đề xuất mới** (Duyệt, chỉ khi kế hoạch đã duyệt): đề xuất Opex sinh khoản Opex *Dự kiến* (cần nhà cung cấp); đề xuất Capex sinh dự án Capex *Nháp* có một hạng mục. Chạy lại không tạo trùng.
  - Dòng gia hạn **không** tự gia hạn Opex. Việc gia hạn vẫn đi qua nút *Gia hạn* khi đến hạn.

## 4. Theo dõi thực hiện (kế hoạch đã duyệt)

Mỗi dòng có số **Thực hiện** và **Chênh lệch**:

| Nguồn dòng | Cách tính số thực hiện |
|---|---|
| Gia hạn | Đi theo chuỗi gia hạn từ Opex gốc, cộng các kỳ rơi vào năm kế hoạch. |
| Đã ghi nhận | Số tiền hiện tại của Opex đó. |
| Dự án Capex / đề xuất đã tạo | Giá trị hiện tại khi dự án đã duyệt. |

**Phát sinh ngoài kế hoạch**: Opex / Capex của năm không thuộc dòng nào, liệt kê riêng.

## 5. Dữ liệu

Hai bảng mới, tạo bằng `create_all`, không đổi bảng cũ:
- `budget_plans`: năm, version, trạng thái, % tăng mặc định, trần, ghi chú, người gửi / duyệt, `row_version`.
- `budget_plan_lines`: nguồn + id nguồn (UNIQUE theo kế hoạch), loại Opex / Capex, loại chi phí, nhà cung cấp, cost center, ngày bắt đầu, số kỳ, số hiện tại, % tăng, `amount_mode` auto / manual, số kế hoạch trước / sau VAT, ưu tiên, đưa vào, ghi chú, `stale`, mã đã tạo.

Mọi thay đổi ghi vào `audit_log` (`spend.plan.*`). Hộp việc của người có quyền Duyệt hiện mục "Kế hoạch ngân sách … chờ duyệt".

Xuất Excel gồm 4 sheet: Tổng hợp, Chi tiết, Theo loại, Thực hiện. Số liệu cùng nguồn với màn hình.

## 6. Hướng mở rộng (chưa làm)

- Phân bổ dòng tiền theo **tháng** (theo chu kỳ thanh toán), thay vì chỉ theo quý.
- % tăng theo **loại chi phí** và theo **tỷ giá** (license USD).
- Kịch bản: so sánh 2–3 phương án trần.
- Tờ trình ngân sách: điền kế hoạch đã duyệt vào mẫu .docx, có AI viết phần thuyết minh.
- Nhắc trong năm khi thực hiện vượt 90% kế hoạch của một loại chi phí.
