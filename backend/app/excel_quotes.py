"""Excel exports of a quotation request (gói báo giá).

* build_comparison_xlsx — "So sánh" (aligned items, one column group per vendor, SUM / MIN formulas),
  one sheet per quotation with a recompute column (=SL*ĐG) and the difference, "Kiểm tra" (findings).
* build_evaluation_xlsx — replica of the company EVALUATION FORM: section A 70 %, section B 30 %,
  Total = SUMPRODUCT(weights, scores), class = nested IF (>= 8 A, >= 7 B, >= 6 C).

Vendor columns use the vendor short name (service.vendor_label). Text typed by people or read
from vendor files is always written as text, never as a formula."""
import datetime as dt
import io
import re
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .excel_sizing import MONEY, NAVY, _fill, _font, _print_setup, _section, _sheet_title, _widths
from .timeutil import VN_TZ

THIN = Side(style="thin", color="98A2B3")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
SCORE = "0.00"
QTY = "#,##0.###"
HEAD_FILL = "DCE7F7"


def _txt(ws, row: int, col: int, value: Any, **style):
    """Write a value; strings stay text even when they start with = + - @."""
    c = ws.cell(row=row, column=col, value=value)
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        c.data_type = "s"
    for k, v in style.items():
        setattr(c, k, v)
    return c


def _formula(ws, row: int, col: int, formula: str, fmt: Optional[str] = None, bold: bool = False):
    c = ws.cell(row=row, column=col, value=formula)
    c.font = _font(10, bold)
    if fmt:
        c.number_format = fmt
    return c


def _sheet_name(label: str, used: set) -> str:
    base = re.sub(r"[\[\]:*?/\\']", " ", label or "NCC").strip()[:28] or "NCC"
    name, i = base, 2
    while name.lower() in used:
        name = f"{base[:25]} ({i})"
        i += 1
    used.add(name.lower())
    return name


def _subtitle(d: Dict[str, Any]) -> str:
    return f"{d['code']} · {d['workstreamLabel']} · VND · Lập {dt.datetime.now(VN_TZ):%d/%m/%Y %H:%M}"


def _head(ws, row: int, values: List[str], col: int = 1) -> None:
    for j, h in enumerate(values, col):
        c = _txt(ws, row, j, h)
        c.font = _font(10, True, "FFFFFF")
        c.fill = _fill(NAVY)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BOX
    ws.row_dimensions[row].height = 30


def _save(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ================================================================ comparison
def build_comparison_xlsx(d: Dict[str, Any], comp: Dict[str, Any], lines: Dict[int, List[Dict[str, Any]]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "So sánh"
    cols = comp["columns"]
    n = len(cols)
    ncols = 3 + 3 * n + 1
    start = _sheet_title(ws, f"So sánh báo giá — {d['title']}", _subtitle(d), ncols)
    _widths(ws, {"A": 6, "B": 48, "C": 9})
    for i in range(n):
        for k, w in enumerate((9, 15, 17)):
            ws.column_dimensions[get_column_letter(4 + 3 * i + k)].width = w
    ws.column_dimensions[get_column_letter(ncols)].width = 17

    r = start
    _txt(ws, r, 1, "Dự toán (trước VAT):", font=_font(10, True))
    if d.get("estimateAmount") is not None:
        c = ws.cell(row=r, column=3, value=d["estimateAmount"])
        c.number_format = MONEY
    r += 2
    # two header rows: vendor label merged over SL / Đơn giá / Thành tiền
    _head(ws, r, ["STT", "Hạng mục", "ĐVT"])
    _head(ws, r + 1, ["", "", ""])
    for col in (1, 2, 3):
        ws.merge_cells(start_row=r, start_column=col, end_row=r + 1, end_column=col)
    for i, c in enumerate(cols):
        c0 = 4 + 3 * i
        _head(ws, r, [c["label"], "", ""], c0)
        ws.merge_cells(start_row=r, start_column=c0, end_row=r, end_column=c0 + 2)
        _head(ws, r + 1, ["SL", "Đơn giá", "Thành tiền"], c0)
    _head(ws, r, ["Thấp nhất"], ncols)
    _head(ws, r + 1, [""], ncols)
    ws.merge_cells(start_row=r, start_column=ncols, end_row=r + 1, end_column=ncols)
    first = r + 2
    r = first
    for i, row in enumerate(comp["rows"], 1):
        _txt(ws, r, 1, i)
        _txt(ws, r, 2, row["description"], alignment=Alignment(wrap_text=True, vertical="top"))
        _txt(ws, r, 3, row.get("unit") or "")
        amount_refs = []
        for j, c in enumerate(cols):
            c0 = 4 + 3 * j
            cell = row["cells"].get(str(c["quotationId"]))
            if cell:
                ws.cell(row=r, column=c0, value=cell.get("qty")).number_format = QTY
                ws.cell(row=r, column=c0 + 1, value=cell.get("unitPriceVnd")).number_format = MONEY
                ws.cell(row=r, column=c0 + 2, value=cell.get("amountVnd")).number_format = MONEY
                if str(c["quotationId"]) == row.get("minQuotation") and n > 1 and row.get("spreadPct"):
                    ws.cell(row=r, column=c0 + 2).fill = _fill("DCFCE7")
            amount_refs.append(f"{get_column_letter(c0 + 2)}{r}")
        if amount_refs:
            _formula(ws, r, ncols, f"=IF(COUNT({','.join(amount_refs)})=0,\"\",MIN({','.join(amount_refs)}))", MONEY)
        for col in range(1, ncols + 1):
            ws.cell(row=r, column=col).border = BOX
        r += 1
    last = r - 1

    def total_row(label: str, values: Dict[int, Any], formula: bool = False, fmt: str = MONEY, bold: bool = True) -> None:
        nonlocal r
        _txt(ws, r, 2, label, font=_font(10, bold))
        for j, c in enumerate(cols):
            col = 4 + 3 * j + 2
            v = values.get(j)
            if formula:
                _formula(ws, r, col, v, fmt, bold)
            elif v is not None:
                ws.cell(row=r, column=col, value=v).number_format = fmt
            ws.cell(row=r, column=col).font = _font(10, bold)
        for col in range(1, ncols + 1):
            ws.cell(row=r, column=col).border = BOX
            ws.cell(row=r, column=col).fill = _fill(HEAD_FILL)
        r += 1

    sums = {j: f"=SUM({get_column_letter(6 + 3 * j)}{first}:{get_column_letter(6 + 3 * j)}{max(first, last)})" for j in range(n)}
    sum_row = r
    total_row("Cộng các dòng (tính lại)", sums, formula=True)
    total_row("Trước VAT — NCC ghi", {j: q["subtotalVnd"] for j, q in enumerate(cols)})
    total_row("Tổng sau VAT — NCC ghi", {j: q["totalVnd"] for j, q in enumerate(cols)})
    lo = ",".join(f"{get_column_letter(6 + 3 * j)}{sum_row}" for j in range(n))
    total_row("Chênh so với thấp nhất", {j: f"={get_column_letter(6 + 3 * j)}{sum_row}-MIN({lo})" for j in range(n)}, formula=True)
    total_row("Chênh (%)", {j: f"=IF(MIN({lo})=0,\"\",{get_column_letter(6 + 3 * j)}{sum_row}/MIN({lo})-1)" for j in range(n)},
              formula=True, fmt="0.0%")
    if d.get("estimateAmount"):
        total_row("Chênh so với dự toán", {j: f"={get_column_letter(6 + 3 * j)}{sum_row}-$C${start}" for j in range(n)}, formula=True)
    r += 1
    r = _section(ws, r, "Điều kiện thương mại", ncols)
    for label, key in (("Hiệu lực báo giá", "validUntil"), ("Giao hàng", "deliveryTerms"), ("Bảo hành", "warrantyTerms"),
                       ("Thanh toán", "paymentTerms"), ("Lỗi tính toán (nghiêm trọng)", "critFindings")):
        _txt(ws, r, 2, label, font=_font(10, True))
        for j, c in enumerate(cols):
            col = 4 + 3 * j
            ws.merge_cells(start_row=r, start_column=col, end_row=r, end_column=col + 2)
            v = c.get(key)
            if key == "validUntil" and v:
                v = dt.date.fromisoformat(v).strftime("%d/%m/%Y")
            _txt(ws, r, col, v if v not in (None, "") else "—", alignment=Alignment(wrap_text=True, vertical="top"))
        ws.row_dimensions[r].height = 30
        r += 1
    ws.freeze_panes = ws.cell(row=first, column=4)
    _print_setup(ws, landscape=True)

    # ---- one sheet per quotation
    used = {"so sánh", "kiểm tra"}
    by_id = {q["id"]: q for q in d["quotations"]}
    for c in cols:
        q = by_id.get(c["quotationId"])
        if not q:
            continue
        ws2 = wb.create_sheet(_sheet_name(c["label"], used))
        s = _sheet_title(ws2, f"Báo giá {c['label']}", f"{q.get('vendorName') or ''} · số {q.get('quoteNo') or '—'} · {q['currency']}", 9)
        _widths(ws2, dict(zip("ABCDEFGHI", [6, 46, 20, 9, 10, 15, 17, 17, 15])))
        _head(ws2, s, ["STT", "Mô tả", "Model", "ĐVT", "SL", "Đơn giá", "Thành tiền (NCC ghi)", "Tính lại = SL × ĐG", "Lệch"])
        rr = s + 1
        items = [l for l in lines.get(q["id"], []) if l["lineType"] == "item"]
        for i, l in enumerate(items, 1):
            _txt(ws2, rr, 1, i)
            _txt(ws2, rr, 2, l["description"], alignment=Alignment(wrap_text=True, vertical="top"))
            _txt(ws2, rr, 3, l.get("model") or "")
            _txt(ws2, rr, 4, l.get("unit") or "")
            ws2.cell(row=rr, column=5, value=l.get("qty")).number_format = QTY
            ws2.cell(row=rr, column=6, value=l.get("unitPrice")).number_format = MONEY
            ws2.cell(row=rr, column=7, value=l.get("amount")).number_format = MONEY
            _formula(ws2, rr, 8, f"=IF(OR(E{rr}=\"\",F{rr}=\"\"),\"\",E{rr}*F{rr})", MONEY)
            _formula(ws2, rr, 9, f"=IF(OR(G{rr}=\"\",H{rr}=\"\"),\"\",G{rr}-H{rr})", MONEY)
            for col in range(1, 10):
                ws2.cell(row=rr, column=col).border = BOX
            rr += 1
        lst = max(s + 1, rr - 1)
        sub_row = rr
        _txt(ws2, rr, 2, "Cộng các dòng", font=_font(10, True))
        _formula(ws2, rr, 7, f"=SUM(G{s + 1}:G{lst})", MONEY, True)
        _formula(ws2, rr, 8, f"=SUM(H{s + 1}:H{lst})", MONEY, True)
        rr += 1
        for label, key in (("Trước VAT — NCC ghi", "statedSubtotal"), ("Chiết khấu — NCC ghi", "statedDiscount"),
                           ("VAT — NCC ghi", "statedVat"), ("Tổng — NCC ghi", "statedTotal")):
            _txt(ws2, rr, 2, label, font=_font(10, True))
            if q.get(key) is not None:
                ws2.cell(row=rr, column=7, value=q[key]).number_format = MONEY
            rr += 1
        if q.get("vatPct") is not None:
            _txt(ws2, rr, 2, f"Tổng tính lại (VAT {q['vatPct']:g}%)", font=_font(10, True))
            disc = f"-G{sub_row + 2}" if q.get("statedDiscount") else ""
            _formula(ws2, rr, 8, f"=ROUND((H{sub_row}{disc})*(1+{q['vatPct']}/100),0)", MONEY, True)
        ws2.freeze_panes = ws2.cell(row=s + 1, column=3)
        _print_setup(ws2, landscape=True)

    # ---- findings
    ws3 = wb.create_sheet("Kiểm tra")
    s = _sheet_title(ws3, "Kết quả kiểm tra công thức", _subtitle(d), 4)
    _widths(ws3, {"A": 18, "B": 12, "C": 12, "D": 100})
    _head(ws3, s, ["NCC", "Mã", "Mức", "Nội dung"])
    rr = s + 1
    for q in d["quotations"]:
        for f in q["findings"]:
            _txt(ws3, rr, 1, q["vendorLabel"])
            _txt(ws3, rr, 2, f["code"])
            _txt(ws3, rr, 3, "Nghiêm trọng" if f["severity"] == "crit" else "Lưu ý")
            _txt(ws3, rr, 4, f["message"], alignment=Alignment(wrap_text=True, vertical="top"))
            rr += 1
    if rr == s + 1:
        _txt(ws3, rr, 4, "Không phát hiện sai lệch công thức.")
    _print_setup(ws3, landscape=True)
    return _save(wb)


# ================================================================ evaluation form
def build_evaluation_xlsx(d: Dict[str, Any]) -> bytes:
    ev = d["evaluation"]
    cols = ev["columns"]
    n = max(1, len(cols))
    last_col = 7 + n                                  # A STT, B:F tiêu chí, G tỷ trọng, H.. NCC
    wb = Workbook()
    ws = wb.active
    ws.title = "EVALUATION FORM"
    ws.sheet_view.showGridLines = False
    _widths(ws, {"A": 6, "B": 14, "C": 14, "D": 14, "E": 14, "F": 14, "G": 11})
    for j in range(n):
        ws.column_dimensions[get_column_letter(8 + j)].width = 14
    L = get_column_letter(last_col)

    ws.merge_cells(f"A1:{L}2")
    _txt(ws, 1, 1, "PHIẾU ĐÁNH GIÁ NHÀ CUNG CẤP", font=Font(name="Calibri", size=16, bold=True, color=NAVY),
         alignment=Alignment(horizontal="center", vertical="center"))
    ws.merge_cells(f"A3:{L}3")
    _txt(ws, 3, 1, f"(EVALUATION FORM) · {d['code']}", font=_font(10, False, "475467"), alignment=Alignment(horizontal="center"))

    def field(row: int, col: int, label: str, value: Any, span_to: int) -> None:
        _txt(ws, row, col, label, font=_font(10, True))
        ws.merge_cells(start_row=row, start_column=col + 2, end_row=row, end_column=span_to)
        _txt(ws, row, col + 2, value if value not in (None, "") else "", font=_font(10),
             border=Border(bottom=Side(style="dotted", color="98A2B3")))

    date = ev.get("date")
    field(6, 1, "Bộ phận đánh giá:", ev.get("department"), 6)
    _txt(ws, 6, 7, "Ngày đánh giá:", font=_font(10, True))
    if date:
        c = ws.cell(row=6, column=8, value=dt.date.fromisoformat(date))
        c.number_format = "dd/mm/yyyy"
    field(8, 1, "Loại hình sản phẩm, dịch vụ:", ev.get("service"), last_col)
    field(10, 1, "Kết luận:", ev.get("conclusion"), last_col)

    hr = 12
    _head(ws, hr, ["STT", "Tiêu chí đánh giá", "", "", "", "", "Tỷ trọng"] + [c["label"] for c in cols])
    ws.merge_cells(start_row=hr, start_column=2, end_row=hr, end_column=6)

    def section_row(row: int, label: str, weight: float) -> None:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        _txt(ws, row, 1, label, font=_font(10, True, NAVY), fill=_fill(HEAD_FILL))
        c = ws.cell(row=row, column=7, value=weight)
        c.number_format = "0%"
        c.font = _font(10, True, NAVY)
        c.fill = _fill(HEAD_FILL)
        for col in range(1, last_col + 1):
            ws.cell(row=row, column=col).border = BOX
            ws.cell(row=row, column=col).fill = _fill(HEAD_FILL)

    r = hr + 1
    ranges = []
    for sec, label, weight in (("A", "A. KHUNG ĐÁNH GIÁ TIÊU CHUẨN", 0.7), ("B", "B. KHUNG ĐÁNH GIÁ MỞ RỘNG", 0.3)):
        section_row(r, label, weight)
        r += 1
        first = r
        for c in [x for x in ev["criteria"] if x["section"] == sec]:
            _txt(ws, r, 1, c["seq"], alignment=Alignment(horizontal="center", vertical="center"))
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=6)
            _txt(ws, r, 2, c["name"], alignment=Alignment(wrap_text=True, vertical="center"))
            w = ws.cell(row=r, column=7, value=c["weight"])
            w.number_format = "0%"
            w.alignment = Alignment(horizontal="center")
            for j, col in enumerate(cols):
                v = c["scores"].get(str(col["quotationId"]))
                cell = ws.cell(row=r, column=8 + j, value=v)
                cell.number_format = "0.0#"
                cell.alignment = Alignment(horizontal="center")
            for col in range(1, last_col + 1):
                ws.cell(row=r, column=col).border = BOX
            ws.row_dimensions[r].height = 30 if len(c["name"]) > 60 else 18
            r += 1
        ranges.append((first, r - 1))

    total_row = r
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=7)
    _txt(ws, r, 1, "Total", font=_font(11, True), alignment=Alignment(horizontal="right"))
    for j in range(len(cols)):
        col = get_column_letter(8 + j)
        parts = [f"SUMPRODUCT($G${a}:$G${b},{col}{a}:{col}{b})" for a, b in ranges if b >= a]
        _formula(ws, r, 8 + j, "=ROUND(" + "+".join(parts or ["0"]) + ",2)", SCORE, True).alignment = Alignment(horizontal="center")
    for col in range(1, last_col + 1):
        ws.cell(row=r, column=col).border = BOX
        ws.cell(row=r, column=col).fill = _fill(HEAD_FILL)
    r += 1
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=7)
    _txt(ws, r, 1, "Loại đánh giá", font=_font(11, True), alignment=Alignment(horizontal="right"))
    for j in range(len(cols)):
        t = f"{get_column_letter(8 + j)}{total_row}"
        # Same thresholds as spend.quotes.classify (nested IF works in every Excel / LibreOffice version).
        _formula(ws, r, 8 + j, f"=IF({t}=\"\",\"\",IF({t}>=8,\"A\",IF({t}>=7,\"B\",IF({t}>=6,\"C\",\"Không đạt\"))))", None, True
                 ).alignment = Alignment(horizontal="center")
    for col in range(1, last_col + 1):
        ws.cell(row=r, column=col).border = BOX
        ws.cell(row=r, column=col).fill = _fill(HEAD_FILL)
    r += 2
    _txt(ws, r, 1, "Thang điểm 1–10. Xếp loại: ≥ 8 A · ≥ 7 B · ≥ 6 C · dưới 6 Không đạt.", font=_font(9, False, "475467", True))
    r += 2
    mid = max(8, last_col - 2)
    _txt(ws, r, 2, "Người thực hiện đánh giá", font=_font(10, True), alignment=Alignment(horizontal="center"))
    ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5)
    _txt(ws, r, mid, "Trưởng Bộ phận phê duyệt", font=_font(10, True), alignment=Alignment(horizontal="center"))
    ws.merge_cells(start_row=r, start_column=mid, end_row=r, end_column=last_col)
    r += 5
    _txt(ws, r, 2, ev.get("evaluator") or "", alignment=Alignment(horizontal="center"))
    ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5)
    _txt(ws, r, mid, ev.get("approver") or "", alignment=Alignment(horizontal="center"))
    ws.merge_cells(start_row=r, start_column=mid, end_row=r, end_column=last_col)
    _print_setup(ws, landscape=False)
    ws.page_setup.fitToWidth = 1
    return _save(wb)
