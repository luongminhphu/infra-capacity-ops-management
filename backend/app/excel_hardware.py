"""Excel "Phần cứng" — servers, Dell warranty vs Opex, firmware; same numbers as hardware.service.listing (web page and PDF)."""
from typing import Any, Dict

from openpyxl import Workbook

from .excel_style import _sheet_title, _widths, _write_rows, to_bytes

from .shared.levels import LEVEL_TEXT


def build_xlsx(d: Dict[str, Any]) -> bytes:
    s, items = d["summary"], d["items"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Máy chủ"
    row = _sheet_title(ws, "Sức khoẻ phần cứng — máy chủ Dell (OME)",
                       f"{s['servers']} máy chủ · {s['disksPredictive']} ổ dự báo hỏng · {s['warrantyExpired']} hết bảo hành Dell", 14)
    _write_rows(ws, row, ["Máy chủ", "Service tag", "Model", "iDRAC", "Nguồn điện", "Trạng thái", "Lý do", "Ổ dự báo hỏng",
                          "Firmware lệch", "Host", "Hệ thống", "VM", "VM đang chạy", "Kiểm kê cuối"],
                [[i["name"], i["serviceTag"], i["model"], i["idracIp"], i["powerText"], LEVEL_TEXT[i["level"]], "; ".join(i["reasons"]),
                  i["disksPredictive"], i["firmware"]["behind"], ", ".join(h["name"] for h in i["hosts"]),
                  ", ".join(x["code"] for x in i["systems"]), i["vmCount"], i["vmOn"], i["lastInventoryAt"]] for i in items])
    _widths(ws, {"A": 26, "B": 12, "C": 18, "D": 15, "E": 12, "F": 14, "G": 44, "H": 10, "I": 10, "J": 30, "K": 22, "L": 7, "M": 9, "N": 22})
    ws.freeze_panes = ws.cell(row=row + 1, column=2)
    ww = wb.create_sheet("Bảo hành")
    r2 = _sheet_title(ww, "Bảo hành Dell ↔ Opex", "Mã đối soát WNOP / WOPE / WDLE / WCRT — xem Tài sản → Thiết bị Data Center", 10)
    _write_rows(ww, r2, ["Máy chủ", "Service tag", "Gói bảo hành Dell", "Dell đến", "Còn (ngày)", "Opex", "Opex đến", "Tình trạng Opex",
                         "Mã đối soát", "Chênh lệch"],
                [[i["name"], i["serviceTag"], i["warranty"].get("dellService"), i["warranty"].get("dellEnd"), i["warranty"].get("daysLeft"),
                  i["warranty"].get("opexCode"), i["warranty"].get("opexEnd"),
                  {"active": "Còn hiệu lực", "planned": "Chưa bắt đầu", "expired": "Đã hết hạn", "none": "Chưa có Opex"}.get(i["warranty"].get("opexState"), ""),
                  i["warranty"].get("code"), i["warranty"].get("caseLabel")] for i in items])
    _widths(ww, {"A": 26, "B": 12, "C": 34, "D": 12, "E": 10, "F": 16, "G": 12, "H": 14, "I": 13, "J": 40})
    wf = wb.create_sheet("Firmware")
    r3 = _sheet_title(wf, "Firmware so baseline OME", "Không có baseline: OME chưa cấu hình baseline cho linh kiện này", 6)
    _write_rows(wf, r3, ["Máy chủ", "Service tag", "Linh kiện", "Phiên bản", "Baseline", "So baseline"],
                [[f["server"], f["serviceTag"], f["component"], f["version"], f["baselineVersion"], f["complianceText"]]
                 for f in d.get("firmwareRows") or []])
    _widths(wf, {"A": 26, "B": 12, "C": 40, "D": 16, "E": 16, "F": 18})
    return to_bytes(wb)
