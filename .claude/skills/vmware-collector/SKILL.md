---
name: vmware-collector
description: Dùng khi đọc thêm dữ liệu từ vCenter, sửa backend/app/vcenter/client.py, demo_generator.py hoặc collectors/scheduler.py (pyVmomi, PropertyCollector, host/VM/cluster/datastore, polling, snapshot history). Use for any vCenter / VMware inventory collection change.
---

# Thu thập dữ liệu vCenter (chỉ đọc)

## Nguyên tắc không được vi phạm

- App dùng tài khoản **chỉ đọc** `infra.mon@vsphere.local`. Chỉ được đọc thuộc tính qua
  `PropertyCollector` / `ContainerView`. **Cấm** gọi bất kỳ method nào thay đổi hạ tầng:
  `PowerOn*`, `PowerOff*`, `Reconfig*`, `CreateSnapshot*`, `RemoveSnapshot*`, `Destroy_Task`,
  `Relocate*`, `MigrateVM*`, `EnterMaintenanceMode*`... Ngoại lệ duy nhất: `view.Destroy()` để
  giải phóng ContainerView do chính app tạo (không đụng tới đối tượng hạ tầng).
- Nếu tính năng mới thật sự cần quyền ghi → dừng lại và hỏi người dùng, không tự thêm.
- Lỗi kết nối phải ném `VCenterConnectionError` với thông báo tiếng Việt mà vận hành hiểu được
  (`"Không kết nối được vCenter '<name>' (<host>): ..."`). Không đưa mật khẩu vào thông báo / log.

## Cách thêm một thuộc tính mới

1. Thêm property path vào hằng tương ứng trong `client.py`:
   `HOST_PROPS`, `COMPUTE_PROPS`, `VM_PROPS`, `DATASTORE_PROPS`. Chỉ lấy đúng path cần thiết
   (ví dụ `"guest.guestFamily"`), không lấy `"config"` cả cây — tốn băng thông với vài nghìn VM.
2. Đọc giá trị trong `build_rows()` bằng `props.get("<path>")` hoặc `_safe_attr(obj, ...)`.
   Giá trị có thể là `None` trên vCenter cũ / VM chưa có VMware Tools → luôn có giá trị mặc định.
3. Đặt tên field **camelCase giống frontend** (`ramGB`, `isPoweredOn`, `guestHostname`...): dữ liệu
   đi thẳng ra UI không qua lớp chuyển đổi.
4. Kiểm tra nhánh fallback `_retrieve_lazy()` vẫn chạy (nó đọc cùng danh sách path, từng object).
5. Thêm field tương ứng vào `vcenter/demo_generator.py` để chế độ `VCENTER_n_DEMO=true` có dữ liệu.
6. Nếu cần lưu lịch sử: thêm cột vào `models.py` **và** vào dict `additive` trong
   `database.py::ensure_schema()` (nullable), rồi map trong `scheduler.py`
   (`_host_inventory_cols` / `_vm_inventory_cols`).
7. Nếu field ảnh hưởng tính capacity → xem skill `capacity-math` và sửa cả `frontend/js/core.js`.

## Hiệu năng (vài nghìn VM × 4 vCenter)

- Luôn lấy theo lô: `_retrieve()` dùng `RetrievePropertiesEx` + `ContinueRetrievePropertiesEx`,
  `PAGE_SIZE = 1000`. Tham chiếu chéo (VM → host → cluster, VM → datastore) giải quyết **cục bộ**
  bằng `_moId`, không gọi thêm SOAP.
- Không truy cập thuộc tính managed object trong vòng lặp (`vm.runtime.host.name`) — mỗi lần là
  một round-trip.
- Scheduler poll các nguồn song song (thread pool, mỗi task một DB session riêng), ghi snapshot bằng
  bulk INSERT (`_bulk`). Số ngày idle / tắt máy tính từ snapshot trước trong **một** truy vấn
  (`_enrich_activity`), không truy vấn theo từng VM.

## Kiểm tra trước khi xong

- [ ] Không có method ghi nào được gọi (grep `_Task(` và các tên ở trên trong `vcenter/`).
- [ ] Demo generator có field mới; app chạy được với `VCENTER_1_DEMO=true`.
- [ ] Cột DB mới nằm trong `ensure_schema()`; chạy được trên SQLite (test) và PostgreSQL.
- [ ] `python -m pytest tests -q` trong `backend/` pass (đặc biệt `test_sync.py`).
