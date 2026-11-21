"""Excel "Baseline cấu hình" — same numbers as baseline.service.report (page, PDF, Hộp việc). ICO v2 G9a."""
from typing import Any, Dict

from openpyxl import Workbook

from .excel_style import _sheet_title, _widths, _write_rows, to_bytes
from .shared.levels import LEVEL_TEXT


def build_xlsx(d: Dict[str, Any]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Quy tắc"
    r = _sheet_title(ws, "Baseline cấu hình ESXi / cluster / VM", f"{d['summary']['findings']} lệch · {d['summary']['excepted']} ngoại lệ", 8)
    _write_rows(ws, r, ["Quy tắc", "Phạm vi", "Bật", "Mức", "Tham số", "Đã kiểm", "Lệch", "Ngoại lệ"],
                [[x["label"], x["scopeLabel"], "Có" if x["enabled"] else "Không", LEVEL_TEXT[x["level"]], x["param"], x["checked"],
                  x["findings"], x["excepted"]] for x in d["rules"]])
    _widths(ws, {"A": 40, "B": 12, "C": 8, "D": 16, "E": 10, "F": 10, "G": 10, "H": 10})
    head = ["Mã", "Mức", "Quy tắc", "Phạm vi", "Đối tượng", "IP", "Cluster", "vCenter", "Chi tiết"]
    row = lambda f: [f["code"], LEVEL_TEXT[f["level"]], f["label"], f["scope"], f["entity"], f.get("ip"), f.get("cluster"),  # noqa: E731
                     f["vcenterKey"], f["detail"]]
    wf = wb.create_sheet("Lệch cấu hình")
    r = _sheet_title(wf, "Lệch cấu hình", "Chưa có ngoại lệ", len(head))
    _write_rows(wf, r, head, [row(f) for f in d["findings"]])
    _widths(wf, {"A": 11, "B": 14, "C": 34, "D": 9, "E": 34, "F": 15, "G": 22, "H": 12, "I": 50})
    wf.freeze_panes = wf.cell(row=r + 1, column=2)
    we = wb.create_sheet("Ngoại lệ")
    r = _sheet_title(we, "Lệch đã chấp nhận (ngoại lệ còn hạn)", "", len(head) + 2)
    _write_rows(we, r, head + ["Lý do", "Đến ngày"], [row(f) + [f["exception"]["reason"], f["exception"]["until"]] for f in d["excepted"]])
    _widths(we, {"A": 11, "B": 14, "C": 34, "D": 9, "E": 34, "F": 15, "G": 22, "H": 12, "I": 40, "J": 40, "K": 12})
    return to_bytes(wb)
