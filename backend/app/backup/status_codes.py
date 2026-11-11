"""NetBackup status codes → short Vietnamese description and failure-cause group.

Grouping failures by cause tells the operations team *who* has to act
(network / client owner / storage / VMware / backup admin) instead of
handing them a list of numbers. Unknown codes fall back to "Khác".
"""
from typing import Dict, Optional, Tuple

GROUPS = {
    "client": "Kết nối client / mạng",
    "data": "Dữ liệu trên client",
    "storage": "Storage / media",
    "snapshot": "Snapshot / ảo hoá",
    "window": "Cửa sổ backup / huỷ job",
    "config": "Cấu hình policy",
    "other": "Khác",
}

GROUP_SHORT = {
    "client": "Client / mạng", "data": "Dữ liệu client", "storage": "Storage / media", "snapshot": "Snapshot / ảo hoá",
    "window": "Cửa sổ / huỷ job", "config": "Cấu hình policy", "other": "Khác",
}

SCHEDULE_LABEL = {
    "FULL": "Full", "DIFFERENTIAL_INCREMENTAL": "Incremental", "CUMULATIVE_INCREMENTAL": "Cumulative",
    "USER_BACKUP": "User backup", "USER_ARCHIVE": "User archive", "TLOG": "Transaction log",
}


def schedule_label(v):
    return SCHEDULE_LABEL.get((v or "").upper(), v or "")


GROUP_ACTION = {
    "client": "Kiểm tra dịch vụ NetBackup client (bpcd/vnetd), firewall cổng 1556/13724, phân giải DNS hai chiều và chứng chỉ host.",
    "data": "Rà soát danh sách backup selection, file bị khoá / quyền truy cập trên client; dùng VSS/Open File cho Windows.",
    "storage": "Kiểm tra dung lượng và trạng thái disk pool / storage unit, media server và số job đồng thời (max concurrent jobs).",
    "snapshot": "Kiểm tra kết nối vCenter/ESXi, snapshot tồn đọng, VMware Tools và quyền tài khoản backup trên vCenter.",
    "window": "Xem lại cửa sổ backup, độ dài job full và mức tải media server; tách lịch full theo nhóm client.",
    "config": "Đối chiếu client và backup selection trong policy với hiện trạng; gỡ client đã ngừng hoạt động.",
    "other": "Tra cứu mã lỗi trong NetBackup Status Codes Reference và log job chi tiết.",
}

# code: (description, group)
CODES: Dict[int, Tuple[str, str]] = {
    0: ("Thành công", "other"),
    1: ("Thành công một phần — có file không backup được", "data"),
    2: ("Không backup được file nào trong danh sách", "data"),
    4: ("Lỗi khi lưu trữ (archive)", "data"),
    5: ("Restore thất bại", "data"),
    6: ("Backup của người dùng thất bại", "data"),
    11: ("Lỗi hệ thống trên client", "client"),
    12: ("Không mở được file", "data"),
    13: ("Không đọc được file", "data"),
    14: ("Không ghi được file", "data"),
    23: ("Lỗi đọc socket", "client"),
    24: ("Lỗi ghi socket", "client"),
    25: ("Không kết nối được socket", "client"),
    26: ("Lỗi giao tiếp client/server", "client"),
    40: ("Mất kết nối mạng", "client"),
    41: ("Hết thời gian chờ kết nối mạng", "client"),
    42: ("Lỗi đọc mạng", "client"),
    44: ("Lỗi ghi mạng", "client"),
    46: ("Server không được phép truy cập client", "client"),
    48: ("Không phân giải được tên client", "client"),
    49: ("Client không kết thúc đúng", "client"),
    50: ("Tiến trình trên client bị huỷ", "client"),
    54: ("Hết thời gian chờ khi kết nối client", "client"),
    57: ("Client từ chối kết nối", "client"),
    58: ("Không kết nối được tới client", "client"),
    59: ("Client không cho phép truy cập", "client"),
    69: ("Danh sách file không hợp lệ", "config"),
    71: ("Không file nào trong danh sách tồn tại", "config"),
    84: ("Lỗi ghi media", "storage"),
    85: ("Lỗi đọc media", "storage"),
    86: ("Lỗi định vị media", "storage"),
    96: ("Không cấp được media mới — storage unit hết media", "storage"),
    129: ("Disk storage unit đã đầy", "storage"),
    130: ("Lỗi hệ thống", "other"),
    150: ("Quản trị viên yêu cầu dừng job", "window"),
    156: ("Lỗi snapshot", "snapshot"),
    174: ("Media manager — lỗi hệ thống", "storage"),
    191: ("Không có image nào được xử lý thành công", "other"),
    196: ("Không chạy vì đã hết cửa sổ backup", "window"),
    200: ("Scheduler thấy không có gì cần làm", "other"),
    213: ("Không có storage unit khả dụng", "storage"),
    219: ("Storage unit yêu cầu không khả dụng", "storage"),
    227: ("Không tìm thấy image cần thiết", "other"),
    239: ("Client không có trong policy", "config"),
    252: ("Lỗi mở rộng (extended error) — xem chi tiết job", "other"),
    636: ("Đọc từ socket bị huỷ", "client"),
    800: ("Yêu cầu tài nguyên thất bại", "storage"),
    806: ("Media server không khả dụng", "storage"),
    811: ("Không giao tiếp được với Resource Broker", "storage"),
    2074: ("Disk volume đang DOWN", "storage"),
    2106: ("Disk storage server đang DOWN", "storage"),
    4200: ("Không tạo được snapshot", "snapshot"),
    4201: ("Không gửi được yêu cầu snapshot tới vCenter/ESXi", "snapshot"),
    4207: ("Không lấy được metadata snapshot", "snapshot"),
    4239: ("Không tìm thấy máy ảo", "snapshot"),
    4263: ("Không có ổ đĩa ảo nào để backup", "snapshot"),
    5449: ("Lỗi truy cập vCenter / ESXi", "snapshot"),
    5464: ("Không tạo được kết nối tới vCenter", "snapshot"),
}


def describe(code: Optional[int]) -> str:
    if code is None:
        return "Đang chạy"
    hit = CODES.get(code)
    return hit[0] if hit else f"Mã lỗi {code}"


def group_of(code: Optional[int]) -> str:
    if code is None or code in (0,):
        return "other"
    hit = CODES.get(code)
    return hit[1] if hit else "other"


def outcome(status: Optional[int]) -> str:
    """success | partial | failed | running"""
    if status is None:
        return "running"
    if status == 0:
        return "success"
    if status == 1:
        return "partial"
    return "failed"
