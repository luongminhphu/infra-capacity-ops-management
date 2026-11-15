"""Excel of an office renovation project in the company "Summarize" layout, with live formulas.

Top table: Components / Estimate / Vendor per workstream, TOTAL COST.
Per workstream (I IT FACILITY, II IT NETWORK, III VIDEO CONFERENCE):
  left  — # / Components / Price (dự toán, else báo giá trúng) / vendor totals (tên viết tắt);
  right — comparison with previous projects, total = SUM, KPI = ROUNDUP(total / count, 0).
A missing history value is left empty and the KPI says "không có số liệu" (never 0).
Second sheet: KPI history; third: alerts. Text cells are never formulas."""
import datetime as dt
import io
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from .excel_quotes import BOX, HEAD_FILL, _head, _txt
from .excel_style import MONEY, NAVY, _fill, _font, _print_setup, _sheet_title, _widths
from .spend import renovation as rv
from .timeutil import VN_TZ

ROMAN = {"cabling": "I", "network": "II", "vc": "III"}
TITLES = {"cabling": "IT FACILITY", "network": "IT NETWORK", "vc": "VIDEO CONFERENCE SYSTEM"}
KPI_TEXT = {"cabling": "Chi phí trung bình của 01 node mạng", "network": "Chi phí trung bình của 01 node mạng",
            "vc": "Chi phí trung bình của 01 phòng họp VC"}


def _count(ws: str, p: Dict[str, Any]) -> Optional[int]:
    return p.get("nodes") if ws in ("cabling", "network") else p.get("vcRooms")


def build_xlsx(d: Dict[str, Any]) -> bytes:
    wb = Workbook()
    ws_ = wb.active
    ws_.title = "Summarize"
    ws_.sheet_view.showGridLines = False
    _widths(ws_, {"A": 6, "B": 44, "C": 16, "D": 8, "E": 8, "F": 16, "G": 16, "H": 16, "I": 16, "J": 3, "K": 6, "L": 44})
    comp, costs = d["comparison"], d["costs"]
    floor = d["floor"]
    ws_.merge_cells("A1:I1")
    _txt(ws_, 1, 1, f"{floor} — chi phí IT cải tạo văn phòng · {d['code']} · lập {dt.datetime.now(VN_TZ):%d/%m/%Y}",
         font=Font(name="Calibri", size=13, bold=True, color=NAVY))
    _head(ws_, 2, ["#", "Components", "Estimate", "Vendor"])
    top_rows = {}
    for i, w in enumerate(rv.WORKSTREAMS, 1):
        r = 2 + i
        _txt(ws_, r, 1, i)
        _txt(ws_, r, 2, f"{floor} - {TITLES[w]}")
        v = d.get("vendors", {}).get(w)
        sel = next((c["label"] for c in (v or {}).get("columns", []) if c["id"] == (v or {}).get("selected")), "")
        _txt(ws_, r, 4, sel)
        top_rows[w] = r
        for c in range(1, 5):
            ws_.cell(row=r, column=c).border = BOX
    _txt(ws_, 6, 1, "TOTAL COST", font=_font(11, True))
    ws_.merge_cells("A6:B6")
    c = ws_.cell(row=6, column=3, value="=SUM(C3:C5)")
    c.number_format, c.font = MONEY, _font(11, True)

    row = 8
    for w in rv.WORKSTREAMS:
        cw = comp[w]
        groups = [g for g in d["groups"][w] if g["active"] or str(g["id"]) in costs]
        v = d.get("vendors", {}).get(w) or {"columns": []}
        vcols = v["columns"][:6]
        hist = cw["history"]
        # Titles
        _txt(ws_, row, 1, ROMAN[w], font=_font(11, True, NAVY))
        _txt(ws_, row, 2, f"{floor} - {TITLES[w]}", font=_font(11, True, NAVY))
        _txt(ws_, row, 11, f"SO SÁNH CHI PHÍ {TITLES[w]} " + " - ".join([floor] + [h["label"].split(" ")[0] for h in hist]),
             font=_font(11, True, NAVY))
        hr = row + 1
        _head(ws_, hr, ["#", "Components", "Model", "Qty", "Unit", "Price"] + [c["label"] for c in vcols])
        proj_heads = [_proj_head(w, {"floor": floor, "nodes": d["nodes"], "headcount": d["headcount"], "config": d["configTags"]})] + \
                     [_proj_head(w, {"floor": h["label"].split(" ")[0], "nodes": h.get("nodes"), "headcount": h.get("headcount"),
                                     "config": h["config"]}) for h in hist]
        _head(ws_, hr, ["#", "Components"] + proj_heads, col=11)
        for j in range(len(proj_heads)):
            ws_.column_dimensions[get_column_letter(13 + j)].width = 18
        tr = hr + 1
        first, last = tr + 1, tr + max(1, len(groups))
        rows_by_gid = {r_["groupId"]: r_ for r_ in cw["rows"]}
        for i, g in enumerate(groups, 1):
            r = tr + i
            vals = costs.get(str(g["id"]), {})
            price = (vals.get("estimate") or vals.get("awarded") or {}).get("amount")
            _txt(ws_, r, 1, i)
            _txt(ws_, r, 2, g["name"])
            if price is not None:
                ws_.cell(row=r, column=6, value=price).number_format = MONEY
            _txt(ws_, r, 11, i)
            _txt(ws_, r, 12, g["name"])
            rr = rows_by_gid.get(g["id"], {})
            for j, val in enumerate([rr.get("current")] + list(rr.get("history") or [None] * len(hist))):
                if val is not None:
                    ws_.cell(row=r, column=13 + j, value=val).number_format = MONEY
            for col in list(range(1, 7 + len(vcols))) + list(range(11, 13 + len(proj_heads))):
                ws_.cell(row=r, column=col).border = BOX
        # Totals row (right under the header, like the company file)
        f = ws_.cell(row=tr, column=6, value=f"=SUM(F{first}:F{last})")
        f.number_format, f.font = MONEY, _font(10, True)
        for j, c in enumerate(vcols):
            cell = ws_.cell(row=tr, column=7 + j, value=c["subtotal"])
            cell.number_format, cell.font = MONEY, _font(10, True)
            if c["id"] == v.get("selected"):
                cell.fill = _fill("DCFCE7")
        for j in range(len(proj_heads)):
            col = get_column_letter(13 + j)
            has = (cw["total"] if j == 0 else hist[j - 1]["total"]) is not None
            cell = ws_.cell(row=tr, column=13 + j, value=f"=SUM({col}{first}:{col}{last})" if has else None)
            cell.number_format, cell.font = MONEY, _font(10, True)
            if not has:
                _txt(ws_, tr, 13 + j, "không có số liệu", font=_font(9, False, "475467", True))
        for col in list(range(1, 7 + len(vcols))) + list(range(11, 13 + len(proj_heads))):
            ws_.cell(row=tr, column=col).fill = _fill(HEAD_FILL)
            ws_.cell(row=tr, column=col).border = BOX
        ws_.cell(row=top_rows[w], column=3, value=f"=F{tr}").number_format = MONEY
        # KPI row
        kr = last + 1
        _txt(ws_, kr, 11, KPI_TEXT[w], font=_font(10, True))
        ws_.merge_cells(start_row=kr, start_column=11, end_row=kr, end_column=12)
        counts = [_count(w, d)] + [_hist_count(w, h, cw["mainKpi"]) for h in hist]
        for j, n in enumerate(counts):
            col = get_column_letter(13 + j)
            total_known = (cw["total"] if j == 0 else hist[j - 1]["total"]) is not None
            if n and total_known:
                _formula = ws_.cell(row=kr, column=13 + j, value=f"=ROUNDUP({col}{tr}/{int(n)},0)")
                _formula.number_format, _formula.font = MONEY, _font(10, True)
            else:
                _txt(ws_, kr, 13 + j, "không có số liệu", font=_font(9, False, "475467", True))
            ws_.cell(row=kr, column=13 + j).border = BOX
        row = kr + 3
    ws_.freeze_panes = "C3"
    _print_setup(ws_, landscape=True)

    # ---- KPI sheet
    k = wb.create_sheet("Chỉ số")
    s = _sheet_title(k, f"Chỉ số chi phí — {floor} so với lịch sử", "Lịch sử: thực chi (không có thì báo giá trúng)", 6)
    _widths(k, {"A": 22, "B": 26, "C": 18, "D": 18, "E": 18, "F": 18})
    _head(k, s, ["Khối", "Dự án", "Cấu hình", "Tổng", "Chỉ số", "So trung vị"])
    r = s + 1
    for w in rv.WORKSTREAMS:
        cw = comp[w]
        main = cw["mainKpi"]
        entries = [(f"{floor} ({d['year']}) — dự án này", d["configTags"], cw["total"], cw["kpi"][main], cw["deviationPct"])] + \
                  [(h["label"], h["config"], h["total"], h["kpi"][main], None) for h in cw["history"]]
        for label, cfg, total, kpi, dev in entries:
            _txt(k, r, 1, rv.WS_LABELS[w])
            _txt(k, r, 2, label)
            _txt(k, r, 3, cfg or "—")
            if total is not None:
                k.cell(row=r, column=4, value=total).number_format = MONEY
            else:
                _txt(k, r, 4, "không có số liệu")
            if kpi is not None:
                k.cell(row=r, column=5, value=kpi).number_format = MONEY
            else:
                _txt(k, r, 5, "không có số liệu")
            if dev is not None:
                k.cell(row=r, column=6, value=dev / 100).number_format = "+0.0%;-0.0%"
            r += 1
    # ---- alerts
    a = wb.create_sheet("Cảnh báo")
    s = _sheet_title(a, "Cảnh báo & lưu ý", f"{d['code']} · ngưỡng ±15% so với trung vị lịch sử", 2)
    _widths(a, {"A": 22, "B": 120})
    r = s
    for w in rv.WORKSTREAMS:
        for al in comp[w]["alerts"]:
            _txt(a, r, 1, rv.WS_LABELS[w])
            _txt(a, r, 2, al["message"], alignment=Alignment(wrap_text=True))
            r += 1
    for wtext in d["warnings"]:
        _txt(a, r, 1, "Dự án")
        _txt(a, r, 2, wtext, alignment=Alignment(wrap_text=True))
        r += 1
    if r == s:
        _txt(a, r, 2, "Không có chênh lệch vượt ngưỡng.")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _proj_head(w: str, p: Dict[str, Any]) -> str:
    if w == "cabling":
        extra = " - ".join(x for x in [f"({p['nodes']} nodes)" if p.get("nodes") else "", f"{p['headcount']} NS" if p.get("headcount") else ""] if x)
    elif w == "network":
        extra = f"({p['config']})" if p.get("config") else ""
    else:
        extra = ""
    return f"{p['floor']} \n{extra}".strip()


def _hist_count(w: str, h: Dict[str, Any], main: str) -> Optional[int]:
    """Number of nodes (Facility, Network) or VC rooms of a previous project."""
    return h.get("count") or None
