"""Excel "Kết quả rà soát owner" — same numbers as owners.review.summary / report_review.py (evidence, ISO 27001)."""
from typing import Any, Dict

from openpyxl import Workbook

from .excel_style import _sheet_title, _widths, _write_rows, to_bytes


def build_xlsx(d: Dict[str, Any]) -> bytes:
    c, t, r = d["campaign"], d["totals"], d["reclaim"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Tổng kết"
    row = _sheet_title(ws, f"Kết quả rà soát owner — kỳ {c['periodLabel']}",
                       f"Trạng thái: {'Đã đóng' if c['status'] == 'closed' else 'Đang mở'} · hạn {c['dueAt']}", 2)
    rows = [["VM trong kỳ", t["items"]], ["VM có owner", t["owned"]], ["Đã có quyết định", t["decided"]],
            ["Chưa quyết định", t["undecided"]], ["Tỷ lệ phản hồi (%)", t["responsePct"]], ["VM nên rà", t["flagged"]],
            ["VM chưa có owner", t["unowned"]], ["Chờ gán lại owner", t["queue"]], ["Đã leo thang", t["escalated"]],
            ["Thu hồi đã xác minh", t["verified"]], ["vCPU thu hồi", r["vcpu"]], ["RAM thu hồi (GB)", r["ramGB"]],
            ["Disk thu hồi (GB)", r["diskGB"]], ["Giá trị thu hồi (VND/tháng)", r["vndMonthly"]], ["Giá trị thu hồi (VND/năm)", r["vndYearly"]]]
    rows += [[f"Quyết định: {x['label']}", x["count"]] for x in d["byDecision"]]
    _write_rows(ws, row, ["Chỉ tiêu", "Giá trị"], rows, {2: "#,##0.0"})
    _widths(ws, {"A": 34, "B": 18})
    wo = wb.create_sheet("Theo owner")
    r2 = _sheet_title(wo, "Phản hồi theo owner", c["periodLabel"], 4)
    _write_rows(wo, r2, ["Owner", "VM", "Đã quyết định", "Tỷ lệ (%)"], [[o["ownerEmail"], o["items"], o["decided"], o["pct"]] for o in d["owners"]])
    _widths(wo, {"A": 36, "B": 10, "C": 14, "D": 12})
    wv = wb.create_sheet("Chi tiết VM")
    r3 = _sheet_title(wv, "Chi tiết VM", "Quyết định, người quyết định và tín hiệu lúc mở kỳ", 17)
    head = ["VM", "IP", "vCenter", "Hệ thống", "Tên hệ thống", "Owner", "Nên rà", "Tín hiệu", "Quyết định", "Ngày thu hồi", "vCPU đề xuất",
            "RAM đề xuất (GB)", "Chuyển cho", "Ghi chú", "Người quyết định", "Thời điểm", "vCPU / RAM / Disk thu hồi"]
    _write_rows(wv, r3, head, [[i["vm"], i.get("ip"), i["vcenterKey"], i["systemCode"], i["systemName"], i["ownerEmail"], "Có" if i["flagged"] else "",
                                "; ".join(i["reasons"]), i["decisionLabel"], i["actionDate"], i["newVcpu"], i["newRamGB"], i["transferTo"],
                                i["note"], i["decidedBy"], i["decidedAt"],
                                f"{i['reclaim']['vcpu']:g} / {i['reclaim']['ramGB']:g} / {i['reclaim']['diskGB']:g}"] for i in d["items"]])
    _widths(wv, {"A": 34, "B": 15, "C": 10, "D": 11, "E": 24, "F": 30, "G": 8, "H": 34, "I": 22, "J": 12, "K": 10, "L": 12,
                 "M": 28, "N": 30, "O": 26, "P": 22, "Q": 22})
    wv.freeze_panes = wv.cell(row=r3 + 1, column=2)
    return to_bytes(wb)
