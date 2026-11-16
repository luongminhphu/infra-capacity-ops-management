"""Chuẩn hoá BOM — professional Excel output.

Sheet "BOM": title band, project block, then one section per category (I. Máy chủ, II. Thiết bị mạng…).
Each configuration gets a heading row, a summary box (Model, CPU, RAM… per unit) and its item table;
line totals are formulas (=SL×Đơn giá) and every subtotal / total is a SUM formula, so edits in Excel
stay consistent. Sheet "Tổng hợp": one row per category and per configuration with formulas pointing
to the BOM sheet. Sheet "Dữ liệu gốc": the lines as read, for traceability.
All text from the vendor file / user is written as text (never a formula)."""
import datetime as dt
import io
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, PatternFill, Side
from openpyxl.utils import get_column_letter

from .bom import CATEGORIES, CATEGORY_LABELS
from .excel_style import BLUE, FAINT, INK, LINE, MUTED, NAVY, SURFACE, _fill, _font, _print_setup, _sheet_title
from .timeutil import VN_TZ
from .shared.text import vn_g

ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
THIN = Side(style="thin", color=LINE)
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
LIGHT = "DFE9F6"          # category / config bands (brand blue tint)
SUMMARY_FILL = SURFACE
COLS = [("STT", 7), ("Mã hàng (P/N)", 20), ("Mô tả", 62), ("ĐVT", 8), ("SL", 8), ("Đơn giá", 16), ("Thành tiền", 18), ("Ghi chú", 22)]
NCOL = len(COLS)


def _money_fmt(currency: str) -> str:
    return '#,##0.00 "USD"' if currency == "USD" else '#,##0 "₫"'


def _t(ws, row: int, col: int, value: Any, **style):
    """Text (or number) cell; strings starting with = + - @ stay text."""
    c = ws.cell(row=row, column=col, value=value)
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        c.data_type = "s"
    for k, v in style.items():
        setattr(c, k, v)
    return c


def _band(ws, row: int, text: str, fill: str, color: str, size: float = 11, height: float = 22, right: Optional[str] = None) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=NCOL - 2)
    _t(ws, row, 1, text, font=_font(size, True, color), fill=_fill(fill), alignment=Alignment(vertical="center", indent=1))
    for col in range(1, NCOL + 1):
        ws.cell(row=row, column=col).fill = _fill(fill)
        ws.cell(row=row, column=col).border = BOX
    ws.row_dimensions[row].height = height


def build_bom_xlsx(data: Dict[str, Any]) -> bytes:
    p = data.get("project") or {}
    currency = "USD" if p.get("currency") == "USD" else "VND"
    money = _money_fmt(currency)
    vat = p.get("vatPct")
    vat = float(vat) if isinstance(vat, (int, float)) and 0 < float(vat) <= 20 else None
    hide_empty = p.get("hideEmpty") is not False
    configs = []
    for c in data.get("configs") or []:
        items = [it for it in c.get("items") or [] if not (hide_empty and it.get("empty"))]
        if items:
            configs.append({**c, "items": items})
    wb = Workbook()
    ws = wb.active
    ws.title = "BOM"
    title = f"BẢNG KÊ THIẾT BỊ (BILL OF MATERIALS){' — ' + str(p['name'])[:120] if p.get('name') else ''}"
    sub = " · ".join(x for x in (f"Khách hàng: {str(p['customer'])[:120]}" if p.get("customer") else "",
                                  f"Người lập: {str(p['preparedBy'])[:80]}" if p.get("preparedBy") else "",
                                  f"Ngày lập: {dt.datetime.now(VN_TZ):%d/%m/%Y}", f"Tiền tệ: {currency}") if x)
    row = _sheet_title(ws, title, sub, NCOL)
    for i, (_, w) in enumerate(COLS, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    if p.get("note"):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=NCOL)
        _t(ws, row, 1, str(p["note"])[:1000], font=_font(9.5, False, MUTED, italic=True), alignment=Alignment(wrap_text=True, vertical="top"))
        ws.row_dimensions[row].height = 30
        row += 1
    row += 1
    head_row = row
    for i, (h, _) in enumerate(COLS, 1):
        _t(ws, row, i, h, font=_font(10, True, "FFFFFF"), fill=_fill(NAVY), border=BOX,
           alignment=Alignment(horizontal="center", vertical="center", wrap_text=True))
    ws.row_dimensions[row].height = 26
    ws.freeze_panes = ws.cell(row=row + 1, column=1)
    ws.print_title_rows = f"{row}:{row}"
    row += 1

    cat_total_cells: List[tuple] = []          # (category label, cell ref) for the summary sheet
    cfg_total_cells: List[tuple] = []          # (section no, title, category, qty, cell ref)
    by_cat = {k: [c for c in configs if c.get("category") == k] for k, _ in CATEGORIES}
    sec = 0
    for key, label in CATEGORIES:
        group = by_cat[key]
        if not group:
            continue
        roman = ROMAN[sec] if sec < len(ROMAN) else str(sec + 1)
        sec += 1
        cat_row = row
        _band(ws, row, f"{roman}. {label.upper()}", NAVY, "FFFFFF", 11.5, 24)
        row += 1
        sub_cells = []
        for j, c in enumerate(group, 1):
            qty = c.get("qty") or 1
            _band(ws, row, f"{roman}.{j}  {str(c.get('title') or '')[:150]}   ·   Số lượng: {qty}", LIGHT, NAVY, 10.5, 21)
            row += 1
            # summary box (per unit)
            summary = [s for s in (c.get("summary") or []) if isinstance(s, (list, tuple)) and len(s) == 2 and str(s[1]).strip()][:14]
            if summary:
                _t(ws, row, 2, "Thông số chính (mỗi thiết bị)", font=_font(9, True, MUTED))
                row += 1
                for lbl, val in summary:
                    if str(lbl).startswith(("Giá", "Thành tiền")) and val:
                        val = f"{val} {currency}"
                    _t(ws, row, 2, str(lbl)[:60], font=_font(9.5, True, INK), fill=_fill(SUMMARY_FILL), border=BOX,
                       alignment=Alignment(vertical="top", wrap_text=True))
                    ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=NCOL)
                    _t(ws, row, 3, str(val)[:800], font=_font(9.5, False, INK), border=BOX, alignment=Alignment(vertical="top", wrap_text=True))
                    for col in range(4, NCOL + 1):
                        ws.cell(row=row, column=col).border = BOX
                    ws.row_dimensions[row].height = max(16, 14 * (1 + len(str(val)) // 110))
                    row += 1
                row += 1
            first = row
            for n, it in enumerate(c["items"][:1000], 1):
                bold = bool(it.get("isModel"))
                _t(ws, row, 1, n, font=_font(9.5, bold), alignment=Alignment(horizontal="center", vertical="top"), border=BOX)
                _t(ws, row, 2, str(it.get("pn") or "")[:80], font=_font(9.5, bold), alignment=Alignment(vertical="top"), border=BOX)
                _t(ws, row, 3, str(it.get("desc") or "")[:1000], font=_font(9.5, bold), alignment=Alignment(vertical="top", wrap_text=True), border=BOX)
                _t(ws, row, 4, str(it.get("unit") or "")[:20], font=_font(9.5), alignment=Alignment(horizontal="center", vertical="top"), border=BOX)
                q, pr, tot = _n(it.get("qty")), _n(it.get("price")), _n(it.get("total"))
                _t(ws, row, 5, q, font=_font(9.5), number_format=_qty_fmt(q), alignment=Alignment(horizontal="center", vertical="top"), border=BOX)
                _t(ws, row, 6, pr, font=_font(9.5), number_format=money, alignment=Alignment(vertical="top"), border=BOX)
                cell = ws.cell(row=row, column=7)
                cell.value = f"=IF(AND(ISNUMBER(E{row}),ISNUMBER(F{row})),E{row}*F{row},{tot if tot is not None else 0})" if pr is not None else tot
                cell.number_format, cell.font, cell.border = money, _font(9.5, bold), BOX
                cell.alignment = Alignment(vertical="top")
                _t(ws, row, 8, str(it.get("module") or "")[:60], font=_font(8.5, False, MUTED), border=BOX,
                   alignment=Alignment(vertical="top", wrap_text=True))
                ws.row_dimensions[row].height = max(15, 13 * (1 + len(str(it.get("desc") or "")) // 85))
                row += 1
            last = row - 1
            per_unit = c.get("priceBasis") == "per_unit" and int(qty) > 1
            totals = [(f"Cộng {roman}.{j}" + (" — 1 thiết bị" if per_unit else ""), f"=SUM(G{first}:G{last})")]
            if per_unit:
                totals.append((f"Thành tiền {roman}.{j} — {qty} thiết bị", f"=G{row}*{qty}"))
            for lbl, formula in totals:
                ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
                _t(ws, row, 1, lbl, font=_font(10, True, NAVY), alignment=Alignment(horizontal="right"), border=BOX)
                cell = ws.cell(row=row, column=7, value=formula)
                cell.number_format, cell.font, cell.border, cell.fill = money, _font(10, True, NAVY), BOX, _fill(LIGHT)
                ws.cell(row=row, column=8).border = BOX
                row += 1
            sub_cells.append(f"G{row - 1}")
            cfg_total_cells.append((f"{roman}.{j}", str(c.get("title") or "")[:150], label, qty, f"G{row - 1}", c.get("summary") or []))
            row += 1
        # category subtotal on its band row
        ct = ws.cell(row=cat_row, column=7, value="=" + "+".join(sub_cells))
        ct.number_format, ct.font, ct.fill = money, _font(11, True, "FFFFFF"), _fill(NAVY)
        ws.merge_cells(start_row=cat_row, start_column=NCOL - 1, end_row=cat_row, end_column=NCOL - 1)
        cat_total_cells.append((f"{roman}. {label}", f"G{cat_row}"))

    # grand totals
    row += 1
    tot_rows = []
    lines = [("TỔNG CỘNG" + (" (chưa VAT)" if vat else ""), "=" + "+".join(c for _, c in cat_total_cells) if cat_total_cells else 0)]
    for lbl, formula in lines:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        _t(ws, row, 1, lbl, font=_font(11, True, "FFFFFF"), fill=_fill(NAVY), alignment=Alignment(horizontal="right", indent=1))
        c = ws.cell(row=row, column=7, value=formula)
        c.number_format, c.font, c.fill = money, _font(11, True, "FFFFFF"), _fill(NAVY)
        ws.cell(row=row, column=8).fill = _fill(NAVY)
        tot_rows.append(row)
        row += 1
    if vat:
        vat_row = row
        for lbl, formula in ((f"VAT {vn_g(vat)}%", f"=ROUND(G{tot_rows[0]}*{vat:g}/100,{2 if currency == 'USD' else 0})"),
                             ("TỔNG CỘNG (gồm VAT)", f"=G{tot_rows[0]}+G{vat_row}")):
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
            _t(ws, row, 1, lbl, font=_font(11, True, NAVY), fill=_fill(LIGHT), alignment=Alignment(horizontal="right", indent=1))
            c = ws.cell(row=row, column=7, value=formula)
            c.number_format, c.font, c.fill = money, _font(11, True, NAVY), _fill(LIGHT)
            ws.cell(row=row, column=8).fill = _fill(LIGHT)
            row += 1
    _print_setup(ws, landscape=False)
    ws.sheet_view.zoomScale = 100

    # ------------------------------------------------ Tổng hợp
    s = wb.create_sheet("Tổng hợp")
    r = _sheet_title(s, "TỔNG HỢP THEO ĐẦU MỤC", sub, 5) + 1
    for i, (h, w) in enumerate([("Đầu mục", 46), ("Số cấu hình", 13), ("Số thiết bị", 13), ("Thành tiền", 20), ("Tỷ trọng", 11)], 1):
        s.column_dimensions[get_column_letter(i)].width = w
        _t(s, r, i, h, font=_font(10, True, "FFFFFF"), fill=_fill(NAVY), border=BOX, alignment=Alignment(horizontal="center", vertical="center"))
    r += 1
    first = r
    for lbl, ref in cat_total_cells:
        cfgs = [x for x in cfg_total_cells if lbl.endswith(x[2])]
        _t(s, r, 1, lbl, font=_font(10, True), border=BOX)
        _t(s, r, 2, len(cfgs), font=_font(10), border=BOX, alignment=Alignment(horizontal="center"))
        _t(s, r, 3, sum(int(x[3] or 1) for x in cfgs), font=_font(10), border=BOX, alignment=Alignment(horizontal="center"))
        c = s.cell(row=r, column=4, value=f"='BOM'!{ref}")
        c.number_format, c.border, c.font = money, BOX, _font(10)
        r += 1
    last = r - 1
    _t(s, r, 1, "TỔNG CỘNG" + (" (chưa VAT)" if vat else ""), font=_font(10.5, True, "FFFFFF"), fill=_fill(NAVY), border=BOX)
    for col in (2, 3, 5):
        s.cell(row=r, column=col).fill = _fill(NAVY)
    if cat_total_cells:
        c = s.cell(row=r, column=4, value=f"=SUM(D{first}:D{last})")
        c.number_format, c.font, c.fill = money, _font(10.5, True, "FFFFFF"), _fill(NAVY)
        for k in range(first, last + 1):
            pc = s.cell(row=k, column=5, value=f"=IF(D{r}=0,0,D{k}/D{r})")
            pc.number_format, pc.border = "0.0%", BOX
    r += 2
    _t(s, r, 1, "Chi tiết cấu hình", font=_font(10.5, True, NAVY))
    r += 1
    for i, h in enumerate(["Mục", "Cấu hình", "SL", "Thành tiền", "Thông số chính"], 1):
        _t(s, r, i, h, font=_font(10, True, "FFFFFF"), fill=_fill(NAVY), border=BOX, alignment=Alignment(horizontal="center"))
    s.column_dimensions["E"].width = 70
    r += 1
    for no, ttl, _cat, qty, ref, summ in cfg_total_cells:
        brief = " · ".join(f"{a}: {b}" for a, b in summ if a not in ("Model", "Số lượng"))[:600]
        _t(s, r, 1, no, font=_font(9.5), border=BOX)
        _t(s, r, 2, ttl, font=_font(9.5, True), border=BOX, alignment=Alignment(wrap_text=True, vertical="top"))
        _t(s, r, 3, qty, font=_font(9.5), border=BOX, alignment=Alignment(horizontal="center", vertical="top"))
        c = s.cell(row=r, column=4, value=f"='BOM'!{ref}")
        c.number_format, c.border, c.alignment = money, BOX, Alignment(vertical="top")
        _t(s, r, 5, brief, font=_font(9), border=BOX, alignment=Alignment(wrap_text=True, vertical="top"))
        s.row_dimensions[r].height = max(16, 13 * (1 + len(brief) // 95))
        r += 1
    _print_setup(s, landscape=True)

    # ------------------------------------------------ Dữ liệu gốc
    g = wb.create_sheet("Dữ liệu gốc")
    heads = ["Đầu mục", "Cấu hình", "P/N", "Mô tả", "ĐVT", "SL", "Đơn giá", "Thành tiền"]
    for i, (h, w) in enumerate(zip(heads, [22, 30, 18, 70, 8, 8, 14, 16]), 1):
        g.column_dimensions[get_column_letter(i)].width = w
        _t(g, 1, i, h, font=_font(10, True, "FFFFFF"), fill=_fill(NAVY))
    rr = 2
    for c in configs:
        for it in c["items"][:1000]:
            vals = [CATEGORY_LABELS.get(c.get("category"), "Khác"), str(c.get("title") or "")[:150], str(it.get("pn") or "")[:80],
                    str(it.get("desc") or "")[:1000], str(it.get("unit") or "")[:20], _n(it.get("qty")), _n(it.get("price")), _n(it.get("total"))]
            for i, v in enumerate(vals, 1):
                _t(g, rr, i, v, font=_font(9))
            rr += 1
    g.freeze_panes = "A2"
    g.auto_filter.ref = f"A1:H{max(1, rr - 1)}"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _qty_fmt(q: Optional[float]) -> str:
    """Whole quantities show without a decimal point. Excel renders "#,##0.##" on 2 as "2." (trailing
    separator), so that format is only used when the quantity really has a fraction."""
    if q is not None and float(q).is_integer():
        return "#,##0"
    return "#,##0.##"


def _n(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v if abs(v) < 1e15 else None
    from .shared.text import parse_number
    d = parse_number(v)
    return float(d) if d is not None else None


__all__ = ["build_bom_xlsx", "BLUE", "FAINT"]
