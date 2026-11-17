---
name: netbackup-api
description: Dùng khi sửa module Backup / Veritas NetBackup — backend/app/backup/ (client.py, collector.py, analysis.py, status_codes.py, schedule.py, demo_generator.py), report_backup.py, excel_backup.py, frontend/js/views/backup.js. Covers NetBackup REST API (read-only), job parsing, RPO, failure groups.
---

# Module Backup — Veritas NetBackup REST API (chỉ đọc)

## Nguyên tắc

- Chỉ dùng **GET** (ngoài `/login`, `/logout` khi cấu hình username/password thay vì API key).
  Các endpoint đang dùng (cổng 1556):
  - `GET /netbackup/admin/jobs` — activity monitor
  - `GET /netbackup/config/policies` và `/netbackup/config/policies/{name}`
  - `GET /netbackup/storage/disk-pools`
  Thêm endpoint mới → chỉ GET, ghi vào docstring đầu `backup/client.py` và mục
  "Tài khoản read-only cho NetBackup" trong docs/huong-dan-chi-tiet.md (quyền RBAC cần cấp).
- Header media type theo `NETBACKUP_n_API_VERSION` (8.0 cho NBU 10.0, 12.0 cho 10.4). Lỗi **406**
  nghĩa là sai version → thông báo rõ cho vận hành.
- Lỗi kết nối / xác thực / quyền ném `NetBackupError` với thông báo tiếng Việt có hướng xử lý.
  Không log API key, mật khẩu, token.
- TLS: dùng `nbu_ssl_context(cfg)`; NetBackup dùng CA nội bộ (`NETBACKUP_n_CA_CERT`). Không tắt
  verify cho kết nối vCenter khi sửa phần này.

## Parser phải "chịu lỗi"

- Tên thuộc tính thay đổi giữa NBU 8.3 → 10.x: đọc bằng `_first(d, "a", "b", ...)`,
  số bằng `_num()` / `_int()`, thời gian bằng `parse_time()`; `elapsedTime` có thể là ISO
  duration (`PT...`). Thiếu dữ liệu → `None`, **không** làm hỏng cả lần đồng bộ.
- Phân loại job bằng `job_category()` với các tập `BACKUP_TYPES`, `COPY_TYPES`, `RESTORE_TYPES`.
- Giới hạn an toàn: `PAGE_LIMIT=100`, `MAX_JOB_PAGES=300`, `MAX_POLICIES=400`,
  `POLICY_WORKERS=8`. Không bỏ giới hạn.

## Đồng bộ (collector.py)

- Đồng bộ phải **idempotent**: chạy lại không nhân bản job (`_upsert_jobs` theo khoá job id +
  server key). Có test trong `tests/test_backup.py` — giữ nó xanh.
- Lần đầu lấy `BACKUP_INITIAL_DAYS` ngày; dọn dữ liệu cũ hơn `BACKUP_RETENTION_DAYS` (`_purge`).
- Độ phủ VM đối chiếu với inventory vCenter mới nhất (`_known_vm_names`).

## Nghiệp vụ

- **RPO** = thời gian từ bản backup thành công gần nhất (status 0 hoặc 1). Ngưỡng cảnh báo /
  nghiêm trọng và số lần lỗi liên tiếp là cấu hình (`rpo_hours`, `rpo_crit_hours`,
  `consecutive_fail`), không hard-code.
- Mã lỗi → nhóm nguyên nhân trong `status_codes.py` (`GROUPS`, `GROUP_ACTION`): client/mạng,
  dữ liệu client, storage/media, snapshot/ảo hoá, cửa sổ/huỷ job, cấu hình policy, khác.
  Mã mới → thêm mô tả tiếng Việt ngắn và nhóm, để báo cáo nói được **ai** phải xử lý.

## Kiểm tra trước khi xong

- [ ] Chỉ có request GET (trừ login/logout).
- [ ] `backup/demo_generator.py` sinh payload đúng cấu trúc REST cho trường mới; chạy được với
      `NETBACKUP_1_DEMO=true`.
- [ ] `python -m pytest tests/test_backup.py -q` pass; thêm test cho parser / analysis mới.
- [ ] Web (`views/backup.js`), PDF (`report_backup.py`) và Excel (`excel_backup.py`) dùng cùng
      số liệu từ `analysis.py`, không tự tính lại ở mỗi nơi.
- [ ] Giao diện Backup đổi: theo skill `web-layout` (nhãn ngắn, ô thẳng hàng, không cuộn ngang 390 px), chạy `ui_audit.js --pages backup,backup/clients,…`.
