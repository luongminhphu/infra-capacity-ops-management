"""Shared Excel look of every ICO export (HSC colours, title band, section rule, tables, print setup).
Before this file, excel_sizing.py was imported by every excel_*.py for these helpers.

Public names: fill, font, widths, sheet_title, section, table, write_rows, fit_heights, print_setup
(the underscored originals stay importable)."""
from typing import Any, Dict, List, Optional

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo


# ---------------------------------------------------------------------------
# Palette & styles (aligned with the PDF report)
# ---------------------------------------------------------------------------
# HSC Design System on white (design-system/ico/MASTER.md) — same roles as COLORS in report_pdf.py.
NAVY = "074B9B"      # HSC logo blue: title band
BLUE = "165AB8"      # HSC sb40 (6.59 on white)
TEAL = "147E8F"      # HSC sc30
AMBER = "A38613"     # HSC sy20
INK = "0B2D5C"       # HSC sb10, light-theme text
MUTED = "465F83"
FAINT = "597090"
LINE = "CED5DE"
SURFACE = "F4F6F8"
SURFACE2 = "EDF1F5"
OK, OK_BG = "187A2C", "E4F4E7"
WARN, WARN_BG = "8F7611", "FFF3B8"
CRIT, CRIT_BG = "8F282D", "F2E5E6"
# Excel keeps Calibri: Inter is not installed on most machines that open the file (web + PDF use Inter).
FONT = "Calibri"

# Validated HSC series first (blue, cyan, magenta), then neutral; never status colors.
SERIES = ["165AB8", "23A4B8", "B729FE", "898C8F", "074B9B", "147E8F", "7BA5E0", "A38613"]
SEV = {"critical": ("Nghiêm trọng", CRIT, CRIT_BG), "warning": ("Cảnh báo", WARN, WARN_BG),
       "info": ("Lưu ý", NAVY, SURFACE2), "ok": ("Đạt", OK, OK_BG)}
READY = {"ok": ("SẴN SÀNG TRIỂN KHAI", OK, OK_BG), "warn": ("TRIỂN KHAI ĐƯỢC · CẦN LƯU Ý", WARN, WARN_BG),
         "crit": ("CẦN XỬ LÝ RỦI RO", CRIT, CRIT_BG)}

thin = Side(style="thin", color=LINE)
NUM = "#,##0"
NUM1 = "#,##0.0"
MONEY = "#,##0"


def _fill(c: str) -> PatternFill:
    return PatternFill("solid", fgColor=c)


def _font(size=10, bold=False, color=INK, italic=False) -> Font:
    return Font(name=FONT, size=size, bold=bold, color=color, italic=italic)


def _gb(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v / 1024:,.1f} TiB" if v >= 1024 else f"{v:,.0f} GB"


def _money_short(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1e9:
        return f"{v / 1e9:,.2f} tỷ".replace(",", "X").replace(".", ",").replace("X", ".")
    if abs(v) >= 1e6:
        return f"{v / 1e6:,.0f} tr".replace(",", ".")
    return f"{v:,.0f}".replace(",", ".")


def _r(v: Optional[float], d: int = 1):
    return None if v is None else round(v, d)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
def _widths(ws, widths: Dict[str, float]) -> None:
    for col, w in widths.items():
        ws.column_dimensions[col].width = w


def _sheet_title(ws, title: str, subtitle: str, ncols: int) -> int:
    """Navy title band on rows 1–2; returns the first free row."""
    ws.sheet_view.showGridLines = False
    last = get_column_letter(ncols)
    ws.merge_cells(f"A1:{last}1")
    ws.merge_cells(f"A2:{last}2")
    ws["A1"] = title
    ws["A1"].font = _font(15, True, "FFFFFF")
    ws["A1"].fill = _fill(NAVY)
    ws["A1"].alignment = Alignment(vertical="center", indent=1)
    ws["A2"] = subtitle
    ws["A2"].font = _font(9.5, False, "DFE9F6")
    ws["A2"].fill = _fill(NAVY)
    ws["A2"].alignment = Alignment(vertical="center", indent=1)
    for ref in ("A1", "A2"):                      # titles may carry user / vendor text: never a formula
        if isinstance(ws[ref].value, str) and ws[ref].value[:1] in ("=", "+", "-", "@"):
            ws[ref].data_type = "s"
    for c in range(1, ncols + 1):
        ws.cell(row=1, column=c).fill = _fill(NAVY)
        ws.cell(row=2, column=c).fill = _fill(NAVY)
    ws.row_dimensions[1].height = 30
    ws.row_dimensions[2].height = 18
    return 4


def _section(ws, row: int, text: str, ncols: int, col: int = 1) -> int:
    end = get_column_letter(col + ncols - 1)
    start = get_column_letter(col)
    ws.merge_cells(f"{start}{row}:{end}{row}")
    c = ws[f"{start}{row}"]
    c.value = text   # sentence case (MASTER.md §4); the band color marks the section
    c.font = _font(10, True, NAVY)
    c.alignment = Alignment(vertical="center")
    for i in range(col, col + ncols):
        ws.cell(row=row, column=i).border = Border(bottom=Side(style="medium", color=NAVY))
    ws.row_dimensions[row].height = 20
    return row + 1


def _table(ws, ref: str, name: str, style: str = "TableStyleMedium2") -> None:
    t = Table(displayName=name, ref=ref)
    t.tableStyleInfo = TableStyleInfo(name=style, showRowStripes=True, showColumnStripes=False)
    ws.add_table(t)


def _write_rows(ws, row: int, header: List[str], rows: List[List[Any]], fmts: Optional[Dict[int, str]] = None,
                wrap_cols: tuple = ()) -> int:
    """Header + rows starting at column A; returns last data row."""
    for j, h in enumerate(header, 1):
        c = ws.cell(row=row, column=j, value=h)
        c.font = _font(10, True, "FFFFFF")
        c.fill = _fill(NAVY)
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 30
    for i, r in enumerate(rows, 1):
        for j, v in enumerate(r, 1):
            c = ws.cell(row=row + i, column=j, value=v)
            c.font = _font(10)
            c.alignment = Alignment(vertical="top", wrap_text=j in wrap_cols)
            if fmts and j in fmts:
                c.number_format = fmts[j]
    return row + len(rows)


def _fit_heights(ws, first: int, last: int, widths: Dict[int, float], line_h: float = 13.5) -> None:
    """Estimate row heights for wrapped text (Excel does not auto-fit rows written by openpyxl)."""
    import math
    for r in range(first, last + 1):
        lines = 1
        for col, w in widths.items():
            v = ws.cell(row=r, column=col).value
            if isinstance(v, str) and v:
                lines = max(lines, math.ceil(len(v) / max(1.0, w * 1.05)))
        ws.row_dimensions[r].height = max(18, lines * line_h + 4)


def _print_setup(ws, landscape: bool = True) -> None:
    ws.page_setup.orientation = "landscape" if landscape else "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top = ws.page_margins.bottom = 0.5



def _tile(ws, row: int, col: int, span: int, label: str, value: str, sub: str, accent: str = BLUE) -> None:
    a, b = get_column_letter(col), get_column_letter(col + span - 1)
    for r, (text, font, h) in enumerate([(label, _font(9, True, MUTED), 16),
                                         (value, _font(18, True, INK), 30),
                                         (sub, _font(8.5, False, MUTED), 28)]):
        rr = row + r
        ws.merge_cells(f"{a}{rr}:{b}{rr}")
        c = ws[f"{a}{rr}"]
        c.value = text
        c.font = font
        c.alignment = Alignment(vertical="center" if r else "bottom", horizontal="left", indent=1, wrap_text=(r == 2))
        for cc in range(col, col + span):
            cell = ws.cell(row=rr, column=cc)
            cell.fill = _fill(SURFACE)
            top = Side(style="thick", color=accent) if r == 0 else None
            cell.border = Border(left=thin if cc == col else None, right=thin if cc == col + span - 1 else None,
                                 top=top, bottom=thin if r == 2 else None)


fill = _fill
fit_heights = _fit_heights
font = _font
print_setup = _print_setup
section = _section
sheet_title = _sheet_title
table = _table
widths = _widths
write_rows = _write_rows
tile = _tile
