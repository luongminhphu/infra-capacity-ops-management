# Nhập Opex / Capex từ Excel

**Mục tiêu:** đổ dữ liệu chi phí đang quản lý bằng file Excel vào app một lần (hoặc nhiều lần), không phải gõ lại,
không tạo trùng, và giữ được lịch sử gia hạn.

**Code:**
- Backend: `backend/app/spend/importer.py` (logic), `routers/spend_import.py` (API + file mẫu).
- Frontend: `frontend/js/views/spend_import.js`, gắn vào nút **Nhập Excel** ở trang Opex và Capex.
- Test: `backend/tests/test_spend_import.py`.

## Luồng

1. **Tải file mẫu** (không bắt buộc). File mẫu có:
   - danh sách chọn cho Loại chi phí, Chu kỳ, Trạng thái, Cost center (lấy từ danh mục hiện tại);
   - 2 dòng ví dụ;
   - sheet *Hướng dẫn*.
2. **Chọn file → Kiểm tra.** Server đọc file, chưa ghi gì, và trả về từng dòng với một trong ba kết quả: *Tạo mới*, *Bỏ qua* (kèm lý do trùng), hoặc *Lỗi* (kèm lý do), cùng các cảnh báo.
3. **Nhập.**
   - File được gửi lại và kiểm tra lại, nên những gì được ghi đúng là những gì đã xem.
   - Toàn bộ ghi trong một giao dịch.
   - Còn dòng lỗi thì phải chọn *Chỉ nhập dòng hợp lệ*.
   - Ghi qua đúng hàm của màn hình: `service.create` cho Opex; helper hạng mục và `capex.reconcile` cho Capex. Mã OPX / CPX, kiểm tra hợp lệ và audit log vì vậy giống hệt khi nhập tay.

## Đọc file của người dùng (không bắt buộc dùng mẫu)

- Dùng `shared.xlsx_io`: chống zip-bomb, chỉ đọc, tối đa 2000 dòng, 40 cột, 10 MB. Chỉ nhận `.xlsx` thật (kiểm tra nội dung, chặn macro).
- **Tìm dòng tiêu đề** trong 20 dòng đầu của mọi sheet, theo **tên cột tiếng Việt / tiếng Anh**. So khớp bỏ dấu và bỏ hoa thường; khớp đúng trước, sau đó mới khớp "chứa". Phía trên dòng tiêu đề có thể có dòng tên bảng.
- **Số tiền:**
  - Chấp nhận `480.000.000`, `480,000,000`, hoặc ô số.
  - VAT nhận `10`, `10%`, hoặc ô phần trăm `0.1`.
  - Chỉ có số sau VAT thì suy ra số trước VAT.
- **Ngày:** ô ngày, hoặc chuỗi `dd/mm/yyyy`, `yyyy-mm-dd`, `dd-mm-yyyy`, `dd.mm.yyyy`. Thiếu ngày kết thúc thì dùng *Thời hạn (tháng)*; cả hai đều thiếu thì tính 1 năm (có cảnh báo).
- **Danh mục:**
  - Loại chi phí và cost center khớp theo tên hoặc mã.
  - Nhà cung cấp khớp theo tên, tên ngắn hoặc alias (`service.find_vendor`). Nhà cung cấp chưa có sẽ được tạo mới (có cảnh báo).

## Quy tắc Opex

- **Bắt buộc:** Loại chi phí, Nội dung, Nhà cung cấp, Ngày bắt đầu, Số tiền.
- **Trùng → bỏ qua:**
  - Mã OPX đã có trong hệ thống.
  - Cùng nội dung + nhà cung cấp + ngày bắt đầu với một khoản đã có.
  - Trùng một dòng khác trong cùng file.

  Nhờ vậy nhập lại cùng một file không tạo trùng.
- **Chuỗi gia hạn:**
  - Hai dòng cùng nội dung + nhà cung cấp, kỳ sau bắt đầu trong vòng 31 ngày sau khi kỳ trước kết thúc, thì kỳ trước là *Đã gia hạn* và được nối `renewed_from` sang kỳ sau.
  - Nhờ vậy kế hoạch ngân sách và phần thực hiện so với kế hoạch dùng được ngay trên dữ liệu cũ.
- **Trạng thái khi cột trống:**

  | Trường hợp | Trạng thái |
  |---|---|
  | Có kỳ tiếp theo trong file | Đã gia hạn |
  | Bắt đầu sau hôm nay | Dự kiến |
  | Còn lại | Đang hiệu lực |

  Kỳ đã hết hạn mà không có kỳ tiếp theo được giữ *Đang hiệu lực* và có cảnh báo. Trên màn hình nó hiện *Quá hạn* để người dùng quyết định gia hạn hoặc ngừng. Không gửi nhắc, vì nhắc chỉ chạy cho khoản chưa hết hạn.
- Cột *Trạng thái* nếu có sẽ được dùng: Dự kiến / Đang hiệu lực / Đã gia hạn / Ngừng (kèm *Lý do ngừng*).
- Cột *Serial thiết bị* (phân tách bằng dấu phẩy hoặc chấm phẩy) thành danh sách thiết bị của khoản.

## Quy tắc Capex

- Mỗi dòng là một hạng mục. Các dòng cùng *Tên dự án* + *Năm tài chính* gộp thành một dự án. Ô tên dự án để trống (ô gộp) thì thuộc dự án và năm của dòng trên.
- Thông tin dự án (go-live, trạng thái, người phụ trách, cost center) lấy từ dòng đầu tiên có giá trị.
- Dự án lỗi khi có một hạng mục lỗi.
- **Trùng → bỏ qua:** dự án cùng tên, cùng năm tài chính đã có.
- **Trạng thái:** trống thì là *Nháp*.
  - Dự án *Đã duyệt / Đang triển khai / Đã đóng* tự sinh Opex gia hạn cho hạng mục có số năm bảo hành, như khi duyệt trên màn hình.
  - Màn hình kiểm tra cảnh báo khả năng trùng với Opex đã nhập. Trong trường hợp đó, đặt *Số năm bảo hành = 0*.

## Bảo mật

- Quyền **Biên tập** của module Chi phí; module tắt thì API trả 404.
- Văn bản trong file chỉ được hiển thị đã escape. Thông báo lỗi không chứa nội dung file ngoài đoạn ngắn đã cắt.
- Audit `spend.import.opex|capex` ghi số dòng tạo / bỏ qua / lỗi và danh sách mã (tối đa 200).
