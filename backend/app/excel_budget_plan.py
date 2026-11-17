"""Excel workbook of one budget plan (Kế hoạch ngân sách năm sau). Same numbers as the screen:
everything comes from the plan detail built by routers/spend_plans._detail (spend.budget_plan).

Sheets: Tổng hợp · Chi tiết · Theo loại · Thực hiện (approved plans only).
User-entered text is written as text, never as a formula."""
import datetime as dt
import io
from typing import Any, Dict, List

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from .excel_style import MONEY, _print_setup, _section, _sheet_title, _table, _tile, _widths, _write_rows
from .timeutil import VN_TZ

PCT = '0.0"%"'


def _m(v: int) -> str:
    return f"{v:,}".replace(",", ".")


def _no_formulas(ws) -> None:
    for row in ws.iter_rows():
        for c in row:
            if c.data_type == "f":
                c.data_type = "s"


def _rows_table(ws, start: int, name: str, header: List[str], rows: List[List[Any]], fmts: Dict[int, str]) -> int:
    last = _write_rows(ws, start, header, rows, fmts)
    if rows:
        _table(ws, f"A{start}:{get_column_letter(len(header))}{last}", name)
    return last


def build_plan_xlsx(d: Dict[str, Any]) -> bytes:
    s = d["summary"]
    subtitle = f"FY {d['fiscalYear']} · {d['label']} · {d['statusLabel']}   ·   VND   ·   Lập {dt.datetime.now(VN_TZ):%d/%m/%Y %H:%M}"
    wb = Workbook()

    ws = wb.active
    ws.title = "Tổng hợp"
    _sheet_title(ws, d["name"], subtitle, 8)
    _widths(ws, {c: 17 for c in "ABCDEFGH"})
    base = s["baseline"]
    _tile(ws, 4, 1, 2, f"Kế hoạch FY {d['fiscalYear']}", _m(s["total"]), f"{s['count']} dòng · sau VAT")
    _tile(ws, 4, 3, 2, f"Thực tế FY {base['fiscalYear']}", _m(base["total"]),
          "thay đổi: " + (f"{s['deltaPct']:+.1f}%" if s["deltaPct"] is not None else "—"))
    _tile(ws, 4, 5, 2, "Opex / Capex", f"{_m(s['byKind']['opex'])} / {_m(s['byKind']['capex'])}", "sau VAT")
    cap = s["cap"]
    _tile(ws, 4, 7, 2, "Trần ngân sách", _m(cap) if cap is not None else "Không đặt",
          f"vượt {_m(s['overCap'])}" if s["overCap"] else "trong trần")
    row = _section(ws, 8, "Theo nguồn và mức ưu tiên", 8)
    src = [["Gia hạn (đã tính tăng giá)", s["bySource"]["renewal"]], ["Đã ghi nhận trong năm", s["bySource"]["committed"]],
           ["Dự án Capex", s["bySource"]["capex"]], ["Mới đề xuất", s["bySource"]["new"]],
           ["Bắt buộc", s["byPriority"]["must"]], ["Nên có", s["byPriority"]["should"]], ["Tuỳ chọn", s["byPriority"]["could"]]]
    row = _write_rows(ws, row, ["Nhóm", "Sau VAT"], src, {2: MONEY}) + 2
    row = _section(ws, row, "Theo quý tài chính (ngày bắt đầu / thanh toán)", 8)
    _write_rows(ws, row, ["Q1", "Q2", "Q3", "Q4"], [s["byQuarter"]], {1: MONEY, 2: MONEY, 3: MONEY, 4: MONEY})
    _print_setup(ws)

    ws = wb.create_sheet("Chi tiết")
    _sheet_title(ws, "Chi tiết kế hoạch", subtitle, 16)
    _widths(ws, {"A": 12, "B": 13, "C": 7, "D": 40, "E": 22, "F": 24, "G": 12, "H": 6, "I": 15, "J": 8, "K": 15, "L": 16,
                 "M": 11, "N": 9, "O": 40, "P": 13})
    cut = set(s.get("cutLineIds") or [])
    rows = [[l["sourceLabel"], l["sourceCode"] or "", l["kind"].upper(), l["title"], l["categoryName"] or "", l["vendorName"] or "",
             dt.date.fromisoformat(l["startDate"]) if l["startDate"] else None, l["terms"], l["baseNet"], l["effectiveUpliftPct"],
             l["amountNet"], l["amountGross"], l["priorityLabel"], "Có" if l["included"] else "Không",
             " · ".join(x for x in (l["note"], l.get("detail") if l["source"] == "replace" else None) if x)
             + (" · Vượt trần" if l["id"] in cut else "") + (" · Nguồn đã thay đổi" if l["stale"] else ""),
             l["appliedCode"] or ""] for l in d["lines"]]
    _rows_table(ws, 4, "KeHoach", ["Nguồn", "Mã gốc", "Loại", "Nội dung", "Loại chi phí", "Nhà cung cấp", "Bắt đầu", "Kỳ",
                                   "Hiện tại (trước VAT)", "% tăng", "Kế hoạch (trước VAT)", "Kế hoạch (sau VAT)",
                                   "Ưu tiên", "Đưa vào", "Ghi chú", "Đã tạo"], rows,
                {7: "DD/MM/YYYY", 9: MONEY, 10: PCT, 11: MONEY, 12: MONEY})
    _print_setup(ws)

    ws = wb.create_sheet("Theo loại")
    _sheet_title(ws, "So sánh theo loại chi phí", subtitle, 5)
    _widths(ws, {"A": 32, "B": 18, "C": 18, "D": 18, "E": 12})
    rows = [[c["category"], c["baseline"], c["plan"], c["delta"], c["deltaPct"]] for c in s["byCategory"]]
    _rows_table(ws, 4, "TheoLoai", ["Loại chi phí", f"Thực tế FY {base['fiscalYear']}", f"Kế hoạch FY {d['fiscalYear']}",
                                    "Chênh lệch", "%"], rows, {2: MONEY, 3: MONEY, 4: MONEY, 5: PCT})
    _print_setup(ws, landscape=False)

    a = d.get("actuals")
    if a:
        ws = wb.create_sheet("Thực hiện")
        _sheet_title(ws, "Kế hoạch so với thực hiện", subtitle, 6)
        _widths(ws, {"A": 44, "B": 13, "C": 18, "D": 18, "E": 18, "F": 30})
        rows = []
        for l in d["lines"]:
            got = a["lines"].get(l["id"]) or {"actual": 0, "refs": [], "variance": 0}
            rows.append([l["title"], l["sourceCode"] or l["appliedCode"] or "", l["amountGross"] if l["included"] else 0,
                         got["actual"], got["variance"], ", ".join(got["refs"])])
        last = _rows_table(ws, 4, "ThucHien", ["Dòng kế hoạch", "Mã", "Kế hoạch", "Thực hiện", "Chênh lệch", "Chứng từ"], rows,
                           {3: MONEY, 4: MONEY, 5: MONEY})
        row = _section(ws, last + 2, "Phát sinh ngoài kế hoạch", 6)
        _write_rows(ws, row, ["Nội dung", "Mã", "Sau VAT"],
                    [[u["title"], u["code"], u["amountGross"]] for u in a["unplanned"]], {3: MONEY})
        _print_setup(ws)

    for w in wb.worksheets:
        _no_formulas(w)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
