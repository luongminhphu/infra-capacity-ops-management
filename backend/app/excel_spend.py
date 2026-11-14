"""Excel workbook "Kế hoạch ngân sách IT" — every detail row, same styling as the other workbooks.

Sheets: Tổng quan · Chi tiết · Hết hạn theo quý · Từ Capex · Nhà cung cấp · Bộ lọc.
Numbers come from spend.report.build_budget_report, like the web dashboard and the PDF.
User-entered text is written as text, never as a formula (cells starting with = + - @).
"""
import datetime as dt
import io
from typing import Any, Dict, List

from openpyxl import Workbook

from .excel_sizing import MONEY, _print_setup, _section, _sheet_title, _table, _tile, _widths, _write_rows
from .timeutil import VN_TZ

PCT = '0.0"%"'


def _no_formulas(ws) -> None:
    """openpyxl stores a string starting with "=" as a formula: force such cells back to text."""
    for row in ws.iter_rows():
        for c in row:
            if c.data_type == "f":
                c.data_type = "s"


def _d(v: Any):
    if isinstance(v, str) and v:
        return dt.date.fromisoformat(v)
    return v


def _rows_table(ws, start: int, name: str, header: List[str], rows: List[List[Any]], fmts: Dict[int, str]) -> int:
    last = _write_rows(ws, start, header, rows, fmts)
    if rows:
        from openpyxl.utils import get_column_letter
        _table(ws, f"A{start}:{get_column_letter(len(header))}{last}", name)
    return last


def build_budget_xlsx(r: Dict[str, Any], scope_label: str) -> bytes:
    subtitle = f"{scope_label}   ·   VND, sau VAT   ·   Lập {dt.datetime.now(VN_TZ):%d/%m/%Y %H:%M}"
    wb = Workbook()

    # ---- Tổng quan
    ws = wb.active
    ws.title = "Tổng quan"
    _sheet_title(ws, "Kế hoạch ngân sách IT", subtitle, 8)
    _widths(ws, {c: 16 for c in "ABCDEFGH"})
    t, cmp_ = r["totals"], r["compare"]
    _tile(ws, 4, 1, 2, f"NTC {cmp_['current']['fiscalYear']}", f"{cmp_['current']['total']:,}".replace(",", "."), "đồng")
    _tile(ws, 4, 3, 2, f"NTC {cmp_['next']['fiscalYear']}", f"{cmp_['next']['total']:,}".replace(",", "."),
          "so với năm nay: " + (f"{cmp_['deltaPct']:+.1f}%" if cmp_["deltaPct"] is not None else "—"))
    _tile(ws, 4, 5, 2, "Tổng theo bộ lọc", f"{t['total']:,}".replace(",", "."), f"{t['count']} dòng")
    _tile(ws, 4, 7, 2, "Opex từ Capex", f"{r['fromCapex']['total']:,}".replace(",", "."), f"{r['fromCapex']['count']} khoản")
    row = _section(ws, 8, "Tổng kết", 8)
    ws.cell(row=row, column=1, value=r["headline"])
    row += 1
    for d in r["decisions"]:
        ws.cell(row=row, column=1, value=f"• {d['title']} ({d['amount']:,} đ) → {d['decision']} · {d['timeframe']}".replace(",", "."))
        row += 1
    row = _section(ws, row + 1, "Theo năm tài chính", 8)
    last = _rows_table(ws, row, "TheoNam", ["Năm tài chính", "Opex", "Capex", "Tổng"],
                       [[y["fiscalYear"], y["opex"], y["capex"], y["total"]] for y in r["byYear"]], {2: MONEY, 3: MONEY, 4: MONEY})
    row = _section(ws, last + 2, "Theo loại chi phí", 8)
    _rows_table(ws, row, "TheoLoai", ["Loại chi phí", "Số dòng", "Opex", "Capex", "Tổng", "Tỷ trọng"],
                [[c["category"], c["count"], c["opex"], c["capex"], c["total"], c["sharePct"]] for c in r["byCategory"]],
                {3: MONEY, 4: MONEY, 5: MONEY, 6: PCT})
    _print_setup(ws, landscape=False)

    # ---- Chi tiết (every row)
    ws = wb.create_sheet("Chi tiết")
    head = ["Loại", "Mã", "Nội dung", "NTC", "Loại chi phí", "Nhà cung cấp", "Cost center", "Bắt đầu", "Kết thúc",
            "Trạng thái", "Nguồn gốc", "Dự án Capex", "Phụ trách", "Trước VAT", "Sau VAT"]
    start = _sheet_title(ws, "Chi tiết ngân sách", subtitle, len(head))
    _widths(ws, dict(zip("ABCDEFGHIJKLMNO", [8, 15, 44, 7, 22, 24, 18, 11, 11, 15, 14, 15, 18, 16, 16])))
    _rows_table(ws, start, "ChiTiet", head, [
        ["Opex" if x["kind"] == "opex" else "Capex", x["code"], x["title"], x["fiscalYear"], x["category"], x["vendor"],
         x["costCenter"], _d(x["startDate"]), _d(x["endDate"]), x["statusLabel"], x["originLabel"], x["capexCode"] or "",
         x["owner"], x["amountNet"], x["amountGross"]] for x in r["rows"]],
        {8: "dd/mm/yyyy", 9: "dd/mm/yyyy", 14: MONEY, 15: MONEY})
    ws.freeze_panes = ws.cell(row=start + 1, column=3)
    _print_setup(ws)

    # ---- Hết hạn theo quý
    ws = wb.create_sheet("Hết hạn theo quý")
    head = ["Quý (NTC)", "Mã", "Dịch vụ", "Nhà cung cấp", "Hết hạn", "Còn (ngày)", "Phụ trách", "Sau VAT"]
    start = _sheet_title(ws, "Opex hết hạn theo quý", subtitle, len(head))
    _widths(ws, dict(zip("ABCDEFGH", [14, 15, 44, 24, 11, 11, 18, 16])))
    _rows_table(ws, start, "HetHan", head, [
        [g["label"], i["code"], i["title"], i["vendor"], _d(i["endDate"]), i["daysLeft"], i["owner"], i["amountGross"]]
        for g in r["expiringByQuarter"] for i in g["items"]], {5: "dd/mm/yyyy", 8: MONEY})
    _print_setup(ws)

    # ---- Từ Capex
    ws = wb.create_sheet("Từ Capex")
    head = ["Mã Opex", "Gia hạn", "NTC", "Nhà cung cấp", "Dự án Capex", "Trạng thái", "Sau VAT"]
    start = _sheet_title(ws, "Opex phát sinh từ Capex", subtitle, len(head))
    _widths(ws, dict(zip("ABCDEFG", [15, 44, 7, 24, 15, 15, 16])))
    _rows_table(ws, start, "TuCapex", head, [
        [i["code"], i["title"], i["fiscalYear"], i["vendor"], i["capexCode"] or "", i["statusLabel"], i["amountGross"]]
        for i in r["fromCapex"]["items"]], {7: MONEY})
    _print_setup(ws)

    # ---- Nhà cung cấp
    ws = wb.create_sheet("Nhà cung cấp")
    head = ["#", "Nhà cung cấp", "Số dòng", "Opex", "Capex", "Tổng", "Tỷ trọng"]
    start = _sheet_title(ws, "Nhà cung cấp lớn nhất", subtitle, len(head))
    _widths(ws, dict(zip("ABCDEFG", [5, 32, 9, 16, 16, 16, 10])))
    _rows_table(ws, start, "NhaCungCap", head, [
        [i, v["vendor"], v["count"], v["opex"], v["capex"], v["total"], v["sharePct"]] for i, v in enumerate(r["topVendors"], 1)],
        {4: MONEY, 5: MONEY, 6: MONEY, 7: PCT})
    _print_setup(ws, landscape=False)

    # ---- Bộ lọc
    ws = wb.create_sheet("Bộ lọc")
    start = _sheet_title(ws, "Bộ lọc của báo cáo", subtitle, 2)
    _widths(ws, {"A": 26, "B": 80})
    _write_rows(ws, start, ["Tham số", "Giá trị"], [["Phạm vi", scope_label]] + [
        [k, ", ".join(map(str, v)) if isinstance(v, list) else ("" if v is None else str(v))] for k, v in r["filters"].items()])

    for sheet in wb.worksheets:
        _no_formulas(sheet)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
