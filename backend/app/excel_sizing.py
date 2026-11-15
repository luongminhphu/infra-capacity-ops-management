"""Excel workbook for the host-sizing consulting report, laid out as a dashboard.

Sheet 1 "Dashboard": title band (with logo), KPI tiles, key conclusions, native
Excel charts (hosts per option, VM size mix, top consumers, capacity
allocation, multi-year projection, sensitivity, TCO) and the top findings.
The following sheets hold the detail as formatted Excel tables (filters,
banding, data bars / colour scales) so the customer can keep working with
the numbers: options comparison, risks & recommendations, roadmap, workload,
sensitivity & projection, assumptions & thresholds, VM list. Chart source
data lives on a dedicated "Dữ liệu biểu đồ" sheet.

Pure openpyxl (+ Pillow for the logo, already a WeasyPrint dependency).
"""
import base64
import datetime as dt
import io
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.chart import BarChart, DoughnutChart, LineChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule, DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from . import sizing_advisory
from .report_view import customer_view

from .excel_style import (  # noqa: E402  (shared Excel look; names kept for this module)
    AMBER, BLUE, CRIT, CRIT_BG, FAINT, INK, LINE, MONEY, MUTED, NAVY, NUM, NUM1, OK, OK_BG, READY, SERIES, SEV, SURFACE2, TEAL, WARN, _fill, _fit_heights, _font, _gb, _money_short, _print_setup, _r, _section, _sheet_title, _table, _tile, _widths, _write_rows, thin,
)


def _logo_image(data_url: Optional[str], max_h: int = 46):
    if not data_url or ";base64," not in data_url:
        return None
    mime = data_url[5:data_url.index(";")].lower()
    if "svg" in mime:
        return None  # Excel/openpyxl cannot embed SVG
    try:
        from openpyxl.drawing.image import Image as XLImage
        from PIL import Image as PILImage
        raw = base64.b64decode(data_url.split(",", 1)[1])
        im = PILImage.open(io.BytesIO(raw))
        im.load()
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        buf.seek(0)
        x = XLImage(buf)
        ratio = max_h / float(x.height or max_h)
        x.height = max_h
        x.width = int((x.width or max_h) * ratio)
        return x
    except Exception:  # noqa: BLE001 — a bad logo must never break the export
        return None


# ---------------------------------------------------------------------------
# Dashboard sheet
# ---------------------------------------------------------------------------
DASH_COLS = 16  # A..P, each 10 wide → tiles are 2–3 columns


def _dashboard(wb, result, payload_meta, data_ws, refs) -> None:
    ws = wb.active
    ws.title = "Dashboard"
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = NAVY
    for i in range(1, DASH_COLS + 1):
        ws.column_dimensions[get_column_letter(i)].width = 10.5
    d, p, a = result["demand"], result["params"], result.get("analysis") or {}
    best = result["results"][result["recommendedIndex"]] if result["recommendedIndex"] is not None else None
    env = p.get("environment") or "Dự án mới"

    # Title band rows 1–3
    for r in (1, 2, 3):
        for c in range(1, DASH_COLS + 1):
            ws.cell(row=r, column=c).fill = _fill(NAVY)
    ws.merge_cells("A1:L1")
    ws.merge_cells("A2:L2")
    ws.merge_cells("A3:L3")
    ws["A1"] = (payload_meta.get("preparedOrg") or "Báo cáo tư vấn").upper()
    ws["A1"].font = _font(9, True, "9DC2F5")
    ws["A2"] = f"Đề xuất cấu hình máy chủ — {env}"
    ws["A2"].font = _font(18, True, "FFFFFF")
    from . import platforms
    pf = platforms.get(p)
    sub = ["Sizing hạ tầng ảo hoá" + ("" if p.get("platform") == "generic" else f" {pf['label']}")]
    if payload_meta.get("customerName"):
        sub.append(f"Khách hàng: {payload_meta['customerName']}")
    if payload_meta.get("preparedBy"):
        sub.append(f"Người lập: {payload_meta['preparedBy']}")
    if payload_meta.get("documentCode"):
        sub.append(f"Mã: {payload_meta['documentCode']}")
    sub.append(f"Ngày: {dt.date.today().strftime('%d/%m/%Y')}")
    ws["A3"] = "   ·   ".join(sub)
    ws["A3"].font = _font(9.5, False, "DFE9F6")
    for cell in ("A1", "A2", "A3"):
        ws[cell].alignment = Alignment(vertical="center", indent=1)
    ws.row_dimensions[1].height = 20
    ws.row_dimensions[2].height = 32
    ws.row_dimensions[3].height = 20
    logo = _logo_image(payload_meta.get("logoDataUrl"))
    if logo is not None:
        # white plate behind the logo so any logo colour stays legible
        ws.merge_cells("N1:P3")
        ws["N1"].fill = _fill("FFFFFF")
        for r in (1, 2, 3):
            for c in (14, 15, 16):
                ws.cell(row=r, column=c).fill = _fill("FFFFFF")
        ws.add_image(logo, "N1")

    # Readiness + headline (row 5–6)
    label, fg, bg = READY.get(a.get("readiness") or "ok", READY["ok"])
    ws.merge_cells("A5:C5")
    ws["A5"] = label
    ws["A5"].font = _font(9, True, fg)
    ws["A5"].fill = _fill(bg)
    ws["A5"].alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells("D5:P5")
    fc = a.get("findingCounts") or {}
    ws["D5"] = f"  {fc.get('critical', 0)} nghiêm trọng · {fc.get('warning', 0)} cảnh báo · {fc.get('info', 0)} lưu ý · {fc.get('ok', 0)} đạt"
    ws["D5"].font = _font(9, False, MUTED)
    ws.merge_cells("A6:P6")
    ws["A6"] = result["recommendation"]["headline"]
    ws["A6"].font = _font(12, True, NAVY)
    ws["A6"].alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[6].height = 36

    # KPI tiles (rows 8–10)
    costs = best["advisory"]["costs"] if best else {}
    tiles = [
        ("Máy ảo yêu cầu", f"{d['vmCount']:,}", f"TB {d['avgVcpu']:.1f} vCPU · {_gb(d['avgRamGB'])} / VM", BLUE),
        ("vCPU (sau tăng trưởng)", f"{d['vcpuPlanned']:,.0f}", f"yêu cầu {d['vcpu']:,.0f} · +{p['growth_pct']:g}%", BLUE),
        ("RAM (sau tăng trưởng)", _gb(d["ramPlannedGB"]), f"yêu cầu {_gb(d['ramGB'])}", BLUE),
        ("Host đề xuất", f"{best['hostsTotal']}" if best else "—", f"{best['profile']['name']} · N+{best['redundancy']}" if best else "", OK),
        ("Lưu trữ cần cấp", _gb(d["storageNeededGB"]), "SAN dùng chung" if p["storage_mode"] == "san" else f"HCI ({pf['hci']})", TEAL),
        ((f"TCO {costs.get('years')} năm" if costs.get("tco") else f"License ({best['advisory']['license'].get('unit', 'core') if best else 'core'})"),
         (_money_short(costs.get("tco")) if costs.get("tco") else (f"{best['advisory']['license']['coresTotal']:,}" if best else "—")),
         ("phần cứng + license" if costs.get("tco") else platforms.edition_of(p)), NAVY),
    ]
    col = 1
    spans = [3, 3, 3, 2, 3, 2]
    for (lbl, val, s, acc), span in zip(tiles, spans):
        _tile(ws, 8, col, span, lbl, val, s, acc)
        col += span

    # Key conclusions (rows 12–17)
    row = _section(ws, 12, "Kết luận chính", DASH_COLS)
    lines: List[str] = [result["recommendation"]["detail"]]
    if result["recommendation"].get("why"):
        lines.append(result["recommendation"]["why"])
    if a.get("expandText"):
        lines.append(a["expandText"])
    if best and best["advisory"]["costs"].get("perVmMonth"):
        c = best["advisory"]["costs"]
        lines.append(f"Chi phí bình quân {_money_short(c['perVmMonth'])} / máy ảo / tháng (TCO {c['years']} năm {_money_short(c['tco'])}).")
    for ln in lines:
        ws.merge_cells(f"A{row}:P{row}")
        ws[f"A{row}"] = "•  " + ln
        ws[f"A{row}"].font = _font(10, False, INK)
        ws[f"A{row}"].alignment = Alignment(wrap_text=True, vertical="top", indent=1)
        ws.row_dimensions[row].height = 30 if len(ln) > 150 else 18
        row += 1

    # Charts grid
    row += 1
    row = _section(ws, row, "Biểu đồ phân tích", DASH_COLS)
    chart_top = row
    H, W = 7.2, 13.6  # cm
    charts = []
    if refs.get("hosts"):
        charts.append(refs["hosts"])
    if refs.get("sizes"):
        charts.append(refs["sizes"])
    if refs.get("alloc"):
        charts.append(refs["alloc"])
    if refs.get("projection"):
        charts.append(refs["projection"])
    if refs.get("top"):
        charts.append(refs["top"])
    if refs.get("sens"):
        charts.append(refs["sens"])
    if refs.get("tco"):
        charts.append(refs["tco"])
    rows_per_chart = 15
    for i, ch in enumerate(charts):
        ch.height, ch.width = H, W
        r = chart_top + (i // 2) * rows_per_chart
        c = "A" if i % 2 == 0 else "I"
        ws.add_chart(ch, f"{c}{r}")
    row = chart_top + ((len(charts) + 1) // 2) * rows_per_chart + 1

    # Top findings
    fnd = [f for f in a.get("findings", []) if f["severity"] in ("critical", "warning")][:5] or a.get("findings", [])[:4]
    if fnd:
        row = _section(ws, row, "Rủi ro & khuyến nghị chính", DASH_COLS)
        for f in fnd:
            lbl, fg, bg = SEV.get(f["severity"], SEV["info"])
            ws.merge_cells(f"A{row}:B{row}")
            ws[f"A{row}"] = lbl
            ws[f"A{row}"].font = _font(9, True, fg)
            ws[f"A{row}"].fill = _fill(bg)
            ws[f"A{row}"].alignment = Alignment(horizontal="center", vertical="center")
            ws.merge_cells(f"C{row}:H{row}")
            ws[f"C{row}"] = f["title"]
            ws[f"C{row}"].font = _font(10, True)
            ws[f"C{row}"].alignment = Alignment(wrap_text=True, vertical="center")
            ws.merge_cells(f"I{row}:P{row}")
            ws[f"I{row}"] = f["action"]
            ws[f"I{row}"].font = _font(9.5, False, MUTED)
            ws[f"I{row}"].alignment = Alignment(wrap_text=True, vertical="center")
            ws.row_dimensions[row].height = 32
            for cc in range(1, DASH_COLS + 1):
                ws.cell(row=row, column=cc).border = Border(bottom=Side(style="thin", color=LINE))
            row += 1
        row += 1
    ws.merge_cells(f"A{row}:P{row}")
    ws[f"A{row}"] = ("Chi tiết từng hạng mục xem ở các sheet tiếp theo. Báo cáo được lập dựa trên danh sách máy ảo và giả định tại sheet "
                     "\"Giả định & ngưỡng\"; cấu hình và chi phí chính xác được xác nhận trong báo giá chính thức.")
    ws[f"A{row}"].font = _font(8.5, False, FAINT, italic=True)
    ws[f"A{row}"].alignment = Alignment(wrap_text=True)
    ws.row_dimensions[row].height = 26
    _print_setup(ws, landscape=False)
    ws.print_area = f"A1:P{row}"


# ---------------------------------------------------------------------------
# Chart data sheet + chart objects
# ---------------------------------------------------------------------------
def _labels(show_percent: bool = False) -> DataLabelList:
    dl = DataLabelList()
    dl.showVal = not show_percent
    dl.showPercent = show_percent
    dl.showSerName = False
    dl.showCatName = False
    dl.showLegendKey = False
    dl.showLeaderLines = False
    return dl


def _style_chart(ch, title: str, y_title: Optional[str] = None) -> None:
    ch.title = title
    ch.style = 10
    ch.legend.position = "b"
    if y_title and hasattr(ch, "y_axis"):
        ch.y_axis.title = y_title
    if hasattr(ch, "y_axis"):
        ch.y_axis.majorGridlines = ch.y_axis.majorGridlines  # keep default light grid
        ch.y_axis.delete = False
        ch.x_axis.delete = False


def _color_series(ch, colors: List[str]) -> None:
    for s, c in zip(ch.series, colors):
        s.graphicalProperties.solidFill = c
        s.graphicalProperties.line.solidFill = c


def _chart_data(wb, result) -> Dict[str, Any]:
    ws = wb.create_sheet("Dữ liệu biểu đồ")
    ws.sheet_properties.tabColor = FAINT
    a = result.get("analysis") or {}
    rows_sorted = sorted(result["results"], key=lambda r: (r.get("suggested", False), r["rank"] or 99))
    best = result["results"][result["recommendedIndex"]] if result["recommendedIndex"] is not None else None
    refs: Dict[str, Any] = {}
    row = 1

    def block(title: str, header: List[str], data: List[List[Any]]) -> int:
        nonlocal row
        ws.cell(row=row, column=1, value=title).font = _font(10, True, NAVY)
        row += 1
        top = row
        for j, h in enumerate(header, 1):
            c = ws.cell(row=row, column=j, value=h)
            c.font = _font(9, True, "FFFFFF")
            c.fill = _fill(NAVY)
        for i, r in enumerate(data, 1):
            for j, v in enumerate(r, 1):
                ws.cell(row=row + i, column=j, value=v).font = _font(9)
        row += len(data) + 2
        return top

    # 1. Hosts per option
    if rows_sorted:
        top = block("Số host theo phương án", ["Cấu hình", "Theo CPU", "Theo RAM", "Tổng (gồm dự phòng)"],
                    [[r["profile"]["name"] + (" ★" if r["recommended"] else ""), r["hostsForCpu"], r["hostsForRam"], r["hostsTotal"]] for r in rows_sorted])
        n = len(rows_sorted)
        ch = BarChart()
        ch.type = "col"
        ch.grouping = "clustered"
        ch.add_data(Reference(ws, min_col=2, max_col=4, min_row=top, max_row=top + n), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=top + 1, max_row=top + n))
        _style_chart(ch, "Số host theo từng phương án", "host")
        _color_series(ch, [BLUE, TEAL, NAVY])
        ch.dataLabels = _labels()
        refs["hosts"] = ch

    # 2. VM size mix
    wl = a.get("workload") or {}
    buckets = [b for b in wl.get("sizeBuckets", []) if b["count"]]
    if buckets:
        top = block("Phân bố kích thước máy ảo", ["Nhóm", "Số VM"], [[b["label"], b["count"]] for b in buckets])
        ch = DoughnutChart()
        ch.add_data(Reference(ws, min_col=2, min_row=top, max_row=top + len(buckets)), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=top + 1, max_row=top + len(buckets)))
        ch.title = "Phân bố kích thước máy ảo"
        ch.holeSize = 55
        ch.legend.position = "r"
        ch.dataLabels = _labels(show_percent=True)
        from openpyxl.chart.series import DataPoint
        pal = ["BFDBFE", "60A5FA", "2681FF", "1D4ED8", "074B9B"]
        s = ch.series[0]
        for i in range(len(buckets)):
            pt = DataPoint(idx=i)
            pt.graphicalProperties.solidFill = pal[i % len(pal)]
            s.dPt.append(pt)
        refs["sizes"] = ch

    # 3. Top consumers
    topg = wl.get("topGroups", [])[:8]
    if topg:
        top = block("Nhóm máy ảo chiếm tài nguyên lớn nhất (%)", ["Nhóm", "% vCPU", "% RAM"],
                    [[g["name"], round(g["vcpuShare"], 1), round(g["ramShare"], 1)] for g in reversed(topg)])
        ch = BarChart()
        ch.type = "bar"
        ch.add_data(Reference(ws, min_col=2, max_col=3, min_row=top, max_row=top + len(topg)), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=top + 1, max_row=top + len(topg)))
        _style_chart(ch, "Nhóm máy ảo chiếm tài nguyên lớn nhất (% tổng)")
        _color_series(ch, [BLUE, TEAL])
        refs["top"] = ch

    # 4. Capacity allocation (recommended)
    if best:
        p = result["params"]
        n, red, n_fail = best["hostsTotal"], best["redundancy"], best["failure"]["hostsLeft"]
        util = max(0.05, min(1.0, p["max_util_pct"] / 100.0))
        ph, prof, dmd = best["perHost"], best["profile"], result["demand"]

        def segs(total, usable_host, req, planned):
            vals = [req, planned - req, max(0.0, usable_host * n_fail * util - planned), usable_host * n_fail * (1 - util),
                    usable_host * red, max(0.0, total - usable_host * n)]
            t = sum(max(0.0, v) for v in vals) or 1.0
            return [round(max(0.0, v) / t * 100, 1) for v in vals]

        cpu = segs(prof["cores"] * max(1.0, p["ht_factor"]) * p["cpu_ratio"] * n, ph["logicalCpu"] * p["cpu_ratio"], dmd["vcpu"], dmd["vcpuPlanned"])
        ram = segs(prof["ramGB"] * p["ram_ratio"] * n, ph["usableRamGB"] * p["ram_ratio"], dmd["ramGB"], dmd["ramPlannedGB"])
        labels = ["Yêu cầu", "Dự phòng tăng trưởng", "Còn trống", f"Vùng an toàn {100 - p['max_util_pct']:g}%", f"Dự phòng N+{red}", "Hypervisor"]
        top = block("Phân bổ năng lực cụm đề xuất (%)", ["Tài nguyên"] + labels, [["CPU"] + cpu, ["RAM"] + ram])
        ch = BarChart()
        ch.type = "bar"
        ch.grouping = "percentStacked"
        ch.overlap = 100
        ch.add_data(Reference(ws, min_col=2, max_col=7, min_row=top, max_row=top + 2), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=top + 1, max_row=top + 2))
        _style_chart(ch, f"Phân bổ năng lực — {prof['name']} × {n}")
        _color_series(ch, ["2681FF", "93C5FD", "86EFAC", "E1CB75", "FFADB1", "CBD5E1"])
        refs["alloc"] = ch

    # 5. Projection
    proj = a.get("projection")
    if proj:
        pts = proj["points"]
        top = block("Dự báo số host theo năm", ["Năm", "Host cần", "Đã đề xuất"],
                    [["Hiện tại" if pt["year"] == 0 else f"Năm {pt['year']}", pt["hosts"], proj["installed"]] for pt in pts])
        bar = BarChart()
        bar.add_data(Reference(ws, min_col=2, min_row=top, max_row=top + len(pts)), titles_from_data=True)
        bar.set_categories(Reference(ws, min_col=1, min_row=top + 1, max_row=top + len(pts)))
        _style_chart(bar, f"Dự báo số host (tăng trưởng {proj['annualGrowthPct']:g}%/năm)", "host")
        _color_series(bar, [BLUE])
        bar.dataLabels = _labels()
        line = LineChart()
        line.add_data(Reference(ws, min_col=3, min_row=top, max_row=top + len(pts)), titles_from_data=True)
        line.series[0].graphicalProperties.line.solidFill = OK
        line.series[0].graphicalProperties.line.dashStyle = "dash"
        line.series[0].graphicalProperties.line.width = 28000
        bar += line
        refs["projection"] = bar

    # 6. Sensitivity (growth)
    sens = (a.get("sensitivity") or {}).get("growth")
    if sens:
        xs = sens["x"]
        hdr = ["Dự phòng tăng trưởng"] + [s["name"] for s in sens["series"]]
        top = block("Độ nhạy — số host theo mức dự phòng tăng trưởng", hdr,
                    [[f"{x:g}%"] + [s["hosts"][i] for s in sens["series"]] for i, x in enumerate(xs)])
        ch = LineChart()
        ch.add_data(Reference(ws, min_col=2, max_col=1 + len(sens["series"]), min_row=top, max_row=top + len(xs)), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=top + 1, max_row=top + len(xs)))
        _style_chart(ch, "Số host theo mức dự phòng tăng trưởng", "host")
        for s, c in zip(ch.series, SERIES):
            s.graphicalProperties.line.solidFill = c
            s.graphicalProperties.line.width = 22000
            s.marker.symbol = "circle"
            s.marker.size = 5
            s.marker.graphicalProperties.solidFill = c
        refs["sens"] = ch

    # 7. TCO
    priced = [r for r in rows_sorted if _tco_comparable(r, result)]
    if priced:
        top = block("TCO theo phương án", ["Cấu hình", "Phần cứng", "Gia hạn bảo hành", f"License {priced[0]['advisory']['costs']['years']} năm"],
                    [[r["profile"]["name"] + (" ★" if r["recommended"] else ""), r["advisory"]["costs"]["hardware"] or 0,
                      r["advisory"]["costs"]["warranty"] or 0, r["advisory"]["costs"]["license"] or 0] for r in priced])
        ch = BarChart()
        ch.type = "bar"
        ch.grouping = "stacked"
        ch.overlap = 100
        ch.add_data(Reference(ws, min_col=2, max_col=4, min_row=top, max_row=top + len(priced)), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=top + 1, max_row=top + len(priced)))
        _style_chart(ch, f"TCO {priced[0]['advisory']['costs']['years']} năm theo phương án")
        _color_series(ch, [BLUE, AMBER, TEAL])
        ch.y_axis.number_format = '#,##0,," tr"' 
        refs["tco"] = ch

    _widths(ws, {"A": 34, "B": 16, "C": 16, "D": 18, "E": 16, "F": 18, "G": 14, "H": 14, "I": 14})
    return refs


# ---------------------------------------------------------------------------
# Detail sheets
# ---------------------------------------------------------------------------
def _tco_comparable(r, result) -> bool:
    c = r["advisory"]["costs"]
    if not c.get("tco"):
        return False
    any_hw = any(x["advisory"]["costs"].get("hardware") is not None for x in result["results"] if not x.get("suggested"))
    return not (any_hw and c.get("hardware") is None)


def _tco_sheet(wb, result, subtitle: str) -> None:
    best = result["results"][result["recommendedIndex"]] if result["recommendedIndex"] is not None else None
    if not best or not best["advisory"]["costs"].get("tco"):
        return
    c = best["advisory"]["costs"]
    ws = wb.create_sheet("Chi phí TCO")
    ws.sheet_properties.tabColor = AMBER
    span = max(7, len(c["yearly"]) + 2)
    start = _sheet_title(ws, f"Chi phí & TCO {c['years']} năm", subtitle, span)
    inc = c["warrantyIncludedYears"]
    mode = "% giá host / năm" if c["warrantyMode"] == "pct" else "chi phí / host / năm"
    ws.merge_cells(f"A{start}:{get_column_letter(span)}{start}")
    ws[f"A{start}"] = (f"Phần cứng mua năm 1, đã gồm bảo hành {inc} năm; gia hạn bảo hành các năm sau tính theo {mode}; license tính theo từng năm."
                       + (f" Chưa nhập chi phí gia hạn năm {', '.join(str(y) for y in c['warrantyMissingYears'])}." if c.get("warrantyMissingYears") else ""))
    ws[f"A{start}"].font = _font(9.5, False, MUTED, italic=True)
    ws[f"A{start}"].alignment = Alignment(wrap_text=True)
    ws.row_dimensions[start].height = 30
    ys = c["yearly"]
    ncol = len(ys) + 2                      # label + years + total
    row = _section(ws, start + 2, f"TCO theo năm — {best['profile']['name']} × {best['hostsTotal']} host", ncol)
    top = row
    header = ["Thành phần"] + [f"Năm {y['year']}" for y in ys] + [f"Tổng {c['years']} năm"]
    for j, h in enumerate(header, 1):
        cell = ws.cell(row=top, column=j, value=h)
        cell.font = _font(10, True, "FFFFFF")
        cell.fill = _fill(NAVY)
        cell.alignment = Alignment(horizontal="left" if j == 1 else "right", vertical="center")
    ws.row_dimensions[top].height = 22
    comps = [("Phần cứng", "hardware"), ("License", "license"), ("Dịch vụ bảo hành HW", "warranty")]
    for i, (label, key) in enumerate(comps, 1):
        r = top + i
        ws.cell(row=r, column=1, value=label).font = _font(10, True)
        for j, y in enumerate(ys, 2):
            cell = ws.cell(row=r, column=j)
            if key == "warranty" and y["inWarranty"]:
                cell.value = 0
                cell.number_format = '[=0]"Đã gồm";#,##0'
                cell.font = _font(10, False, MUTED)
            elif y[key] is None:
                cell.value = "Chưa nhập" if key == "warranty" else None
                cell.font = _font(10, False, WARN)
            else:
                cell.value = y[key]
                cell.number_format = MONEY
                cell.font = _font(10)
            cell.alignment = Alignment(horizontal="right")
        tc = ws.cell(row=r, column=ncol, value=f"=SUM(B{r}:{get_column_letter(ncol - 1)}{r})")
        tc.number_format = MONEY
        tc.font = _font(10, True)
    r_hw, r_war, r_tot, r_cum = top + 1, top + 3, top + 4, top + 5
    ws.cell(row=r_tot, column=1, value="Tổng theo năm").font = _font(10, True)
    ws.cell(row=r_cum, column=1, value="Lũy kế").font = _font(10, False, MUTED)
    for j in range(2, ncol + 1):
        L = get_column_letter(j)
        t = ws.cell(row=r_tot, column=j, value=f"=SUM({L}{r_hw}:{L}{r_war})")
        t.number_format = MONEY
        t.font = _font(10, True)
        if j < ncol:
            prev = get_column_letter(j - 1)
            cv = ws.cell(row=r_cum, column=j, value=f"={L}{r_tot}" if j == 2 else f"={prev}{r_cum}+{L}{r_tot}")
            cv.number_format = MONEY
            cv.font = _font(10, False, MUTED)
    for j in range(1, ncol + 1):
        ws.cell(row=r_tot, column=j).fill = _fill(SURFACE2)
        ws.cell(row=r_tot, column=j).border = Border(top=Side(style="medium", color=NAVY))
        for rr in range(top + 1, r_cum + 1):
            if j == ncol:
                ws.cell(row=rr, column=j).fill = _fill(SURFACE2)
            if rr != r_tot:
                ws.cell(row=rr, column=j).border = Border(bottom=thin)
    last = r_cum
    tot = last
    # yearly chart: components stacked per year (series = rows) + cumulative line
    bar = BarChart()
    bar.type = "col"
    bar.grouping = "stacked"
    bar.overlap = 100
    bar.add_data(Reference(ws, min_col=1, max_col=ncol - 1, min_row=r_hw, max_row=r_war), titles_from_data=True, from_rows=True)
    bar.set_categories(Reference(ws, min_col=2, max_col=ncol - 1, min_row=top, max_row=top))
    _style_chart(bar, "Chi phí theo năm và lũy kế")
    _color_series(bar, [BLUE, TEAL, AMBER])
    bar.y_axis.number_format = '#,##0,," tr"'
    line = LineChart()
    line.add_data(Reference(ws, min_col=1, max_col=ncol - 1, min_row=r_cum, max_row=r_cum), titles_from_data=True, from_rows=True)
    line.y_axis.axId = 200
    line.y_axis.title = "Lũy kế"
    line.y_axis.number_format = '#,##0,," tr"'
    line.y_axis.crosses = "max"
    line.series[0].graphicalProperties.line.solidFill = NAVY
    line.series[0].graphicalProperties.line.dashStyle = "dash"
    bar += line
    bar.height, bar.width = 8, 18
    ws.add_chart(bar, f"A{tot + 2}")
    # comparison of options
    row = _section(ws, tot + 20, "So sánh TCO giữa các phương án", 7)
    top2 = row
    opts = [r for r in sorted(result["results"], key=lambda x: (x.get("suggested", False), x["rank"] or 99)) if _tco_comparable(r, result)]
    rows2 = [[r["profile"]["name"] + (" ★" if r["recommended"] else ""), r["hostsTotal"], r["advisory"]["costs"]["hardware"],
              r["advisory"]["costs"]["warranty"], r["advisory"]["costs"]["license"], r["advisory"]["costs"]["tco"],
              _r(r["advisory"]["costs"]["perVmMonth"], 0)] for r in opts]
    last2 = _write_rows(ws, row, ["Phương án", "Host", "Phần cứng", "Gia hạn bảo hành", f"License {c['years']} năm", f"TCO {c['years']} năm",
                                  "Chi phí / VM / tháng"], rows2, fmts={3: MONEY, 4: MONEY, 5: MONEY, 6: MONEY, 7: MONEY})
    if rows2:
        _table(ws, f"A{top2}:G{last2}", "TCOPhuongAn", "TableStyleLight9")
        ws.conditional_formatting.add(f"F{top2 + 1}:F{last2}", DataBarRule(start_type="num", start_value=0, end_type="max", color=AMBER))
    ws.cell(row=last2 + 2, column=1, value="TCO chỉ gồm phần cứng, gia hạn bảo hành và license theo đơn giá đã nhập; chưa gồm dịch vụ triển khai, điện, không gian và vận hành.").font = _font(8.5, False, FAINT, italic=True)
    ws.column_dimensions["A"].width = 30
    for j in range(2, max(ncol, 7) + 1):
        ws.column_dimensions[get_column_letter(j)].width = 17
    _print_setup(ws)


def _options_sheet(wb, result, subtitle: str) -> None:
    ws = wb.create_sheet("So sánh phương án")
    ws.sheet_properties.tabColor = BLUE
    p = result["params"]
    years = p.get("tco_years", 5)
    header = ["Hạng", "Cấu hình host", "CPU × core", "RAM / host (GB)", "Host theo CPU", "Host theo RAM", "Dự phòng",
              "Tổng host", "Yếu tố quyết định", "Tổng core", "Tổng RAM (GB)", "CPU khi mất 1 host (%)", "RAM khi mất 1 host (%)",
              "Đạt dự phòng", "Chứa thêm (VM)", "Đơn giá / host", "Chi phí phần cứng", "Core license", "License / năm",
              f"TCO {years} năm", "Chi phí / VM / tháng", "Dư địa tăng trưởng (%)", "Điểm tổng hợp", "Đề xuất"]
    rows = []
    for r in sorted(result["results"], key=lambda x: (x.get("suggested", False), x["rank"] or 99)):
        pr, adv = r["profile"], r["advisory"]
        rows.append([r["rank"] if r["rank"] is not None else "Tham khảo", pr["name"], f"{pr['sockets']}×{pr['coresPerSocket']}", pr["ramGB"], r["hostsForCpu"], r["hostsForRam"],
                     f"N+{r['redundancy']}", r["hostsTotal"], r["binding"], r["totals"]["cores"], r["totals"]["ramGB"],
                     _r(r["failure"]["cpuPct"]), _r(r["failure"]["ramPct"]), ("Sát ngưỡng" if r["failure"].get("atLimit") else "Đạt") if r["failure"]["ok"] else "Chưa đạt",
                     r["spare"]["extraAvgVms"], pr["unitPrice"], r["budget"], adv["license"]["coresTotal"], adv["license"]["yearlyCost"],
                     adv["costs"]["tco"] if _tco_comparable(r, result) else None,
                     _r(adv["costs"]["perVmMonth"], 0) if _tco_comparable(r, result) else None, _r(adv["runway"]["extraGrowthPct"]),
                     _r(adv.get("scoreTotal"), 0), "★ Đề xuất" if r["recommended"] else ("Tham khảo" if r.get("suggested") else "")])
    start = _sheet_title(ws, "So sánh phương án cấu hình", subtitle, len(header))
    last = _write_rows(ws, start, header, rows, fmts={4: NUM, 10: NUM, 11: NUM, 12: NUM1, 13: NUM1, 16: MONEY, 17: MONEY, 18: NUM,
                                                       19: MONEY, 20: MONEY, 21: MONEY, 22: NUM1, 23: NUM})
    _table(ws, f"A{start}:{get_column_letter(len(header))}{last}", "SoSanhPhuongAn")
    for i, r in enumerate(sorted(result["results"], key=lambda x: (x.get("suggested", False), x["rank"] or 99))):
        if r["recommended"]:
            for c in range(1, len(header) + 1):
                cell = ws.cell(row=start + 1 + i, column=c)
                cell.fill = _fill(OK_BG)
                cell.font = _font(10, True)
    rng = lambda col: f"{col}{start + 1}:{col}{last}"  # noqa: E731
    ws.conditional_formatting.add(rng("H"), DataBarRule(start_type="num", start_value=0, end_type="max", color=BLUE))
    ws.conditional_formatting.add(rng("W"), ColorScaleRule(start_type="num", start_value=40, start_color="F2E5E6",
                                                           mid_type="num", mid_value=70, mid_color="FFF3B8", end_type="num", end_value=100, end_color="DBF0DF"))
    for col in ("L", "M"):
        ws.conditional_formatting.add(rng(col), CellIsRule(operator="greaterThan", formula=["100"], font=Font(color=CRIT, bold=True)))
    ws.conditional_formatting.add(rng("N"), CellIsRule(operator="equal", formula=['"Chưa đạt"'], font=Font(color=CRIT, bold=True), fill=_fill(CRIT_BG)))
    widths = [7, 22, 11, 11, 11, 11, 10, 11, 12, 10, 12, 12, 12, 11, 11, 15, 16, 11, 15, 16, 14, 13, 11, 12]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(row=start + 1, column=3)

    # Scorecard block
    axes = (result.get("analysis") or {}).get("scoreAxes") or []
    if axes:
        row = last + 3
        row = _section(ws, row, "Bảng điểm đa tiêu chí (0–100, cao = tốt)", 2 + len(axes))
        hdr = ["Cấu hình", ""] + [ax["label"] for ax in axes]
        srows = []
        for r in sorted(result["results"], key=lambda x: (x.get("suggested", False), x["rank"] or 99)):
            sc = r["advisory"].get("scores") or {}
            srows.append([r["profile"]["name"], "★" if r["recommended"] else ""] + [_r(sc.get(ax["key"]), 0) for ax in axes])
        top = row
        lst = _write_rows(ws, row, hdr, srows, fmts={i: NUM for i in range(3, 3 + len(axes))})
        _table(ws, f"A{top}:{get_column_letter(len(hdr))}{lst}", "BangDiem", "TableStyleLight9")
        ws.conditional_formatting.add(f"C{top + 1}:{get_column_letter(len(hdr))}{lst}",
                                      ColorScaleRule(start_type="num", start_value=0, start_color="F2E5E6", mid_type="num", mid_value=60,
                                                     mid_color="FFF3B8", end_type="num", end_value=100, end_color="DBF0DF"))
    _print_setup(ws)


def _findings_sheet(wb, result, subtitle: str) -> None:
    ws = wb.create_sheet("Rủi ro & khuyến nghị")
    ws.sheet_properties.tabColor = WARN
    a = result.get("analysis") or {}
    header = ["Mức độ", "Lĩnh vực", "Nhận định", "Chi tiết", "Khuyến nghị"]
    start = _sheet_title(ws, "Rủi ro & khuyến nghị", subtitle, len(header))
    fnd = a.get("findings", [])
    rows = [[SEV.get(f["severity"], SEV["info"])[0], f["area"], f["title"], f["detail"], f["action"]] for f in fnd]
    last = _write_rows(ws, start, header, rows, wrap_cols=(3, 4, 5)) if rows else start
    if rows:
        _table(ws, f"A{start}:E{last}", "RuiRo")
        for i, f in enumerate(fnd, 1):
            lbl, fg, bg = SEV.get(f["severity"], SEV["info"])
            c = ws.cell(row=start + i, column=1)
            c.font = _font(10, True, fg)
            c.fill = _fill(bg)
            c.alignment = Alignment(horizontal="center", vertical="top")
            ws.cell(row=start + i, column=3).font = _font(10, True)
    _widths(ws, {"A": 24, "B": 20, "C": 42, "D": 60, "E": 60})
    if rows:
        _fit_heights(ws, start + 1, last, {3: 42, 4: 60, 5: 60})

    steps = a.get("nextSteps", [])
    if steps:
        row = _section(ws, last + 3, "Lộ trình triển khai", 5)
        top = row
        lst = _write_rows(ws, row, ["Giai đoạn", "Thời điểm", "Nội dung", "", ""],
                          [[s["phase"], s["when"], s["what"], "", ""] for s in steps], wrap_cols=(3,))
        for rr in range(top + 1, lst + 1):
            ws.merge_cells(f"C{rr}:E{rr}")
            ws.row_dimensions[rr].height = max(20, -(-len(str(ws.cell(row=rr, column=3).value or "")) // 150) * 14 + 6)
            ws.cell(row=rr, column=1).font = _font(10, True)
        for c in range(1, 6):
            ws.cell(row=top, column=c).fill = _fill(NAVY)
    ws.freeze_panes = ws.cell(row=start + 1, column=1)
    _print_setup(ws)


def _workload_sheet(wb, result, subtitle: str) -> None:
    ws = wb.create_sheet("Phân tích workload")
    ws.sheet_properties.tabColor = TEAL
    a = result.get("analysis") or {}
    wl = a.get("workload") or {}
    d = result["demand"]
    start = _sheet_title(ws, "Phân tích nhu cầu workload", subtitle, 8)
    facts = [("Đặc tính", wl.get("characterLabel")), ("GB RAM / vCPU", _r(wl.get("ramPerVcpu"))),
             ("Số máy ảo", d["vmCount"]), ("Nhóm cấu hình", wl.get("groupCount")),
             ("Số nhóm chiếm 80% RAM", wl.get("paretoLines")),
             ("Máy ảo lớn nhất", f"{(wl.get('largestVm') or {}).get('name', '—')} · {(wl.get('largestVm') or {}).get('vcpu', 0):g} vCPU / {_gb((wl.get('largestVm') or {}).get('ramGB'))}"),
             ("Cơ sở dữ liệu / middleware", ", ".join(wl.get("dbLike", [])) or "—")]
    row = start
    for k, v in facts:
        ws.cell(row=row, column=1, value=k).font = _font(10, True, MUTED)
        ws.merge_cells(f"B{row}:H{row}")
        ws.cell(row=row, column=2, value=v).font = _font(10, True)
        ws.cell(row=row, column=2).alignment = Alignment(horizontal="left")
        for c in range(1, 9):
            ws.cell(row=row, column=c).border = Border(bottom=thin)
        row += 1
    row = _section(ws, row + 1, "Phân bố kích thước máy ảo", 4)
    top = row
    lst = _write_rows(ws, row, ["Nhóm kích thước", "Số VM", "vCPU", "RAM (GB)"],
                      [[b["label"], b["count"], b["vcpu"], b["ramGB"]] for b in wl.get("sizeBuckets", [])], fmts={2: NUM, 3: NUM, 4: NUM})
    _table(ws, f"A{top}:D{lst}", "PhanBoKichThuoc", "TableStyleMedium9")
    ws.conditional_formatting.add(f"B{top + 1}:B{lst}", DataBarRule(start_type="num", start_value=0, end_type="max", color=BLUE))
    row = _section(ws, lst + 2, "Nhóm máy ảo theo mức sử dụng tài nguyên", 8)
    top = row
    lst = _write_rows(ws, row, ["Nhóm máy ảo", "Số lượng", "Tổng vCPU", "Tổng RAM (GB)", "Tổng Disk (GB)", "% vCPU", "% RAM", "% Disk"],
                      [[g["name"], g["qty"], g["vcpu"], g["ramGB"], g["diskGB"], _r(g["vcpuShare"]), _r(g["ramShare"]), _r(g["diskShare"])]
                       for g in wl.get("topGroups", [])], fmts={2: NUM, 3: NUM, 4: NUM, 5: NUM, 6: NUM1, 7: NUM1, 8: NUM1})
    if lst > top:
        _table(ws, f"A{top}:H{lst}", "NhomMayAo", "TableStyleMedium9")
        for col, color in (("F", BLUE), ("G", TEAL), ("H", "94A3B8")):
            ws.conditional_formatting.add(f"{col}{top + 1}:{col}{lst}", DataBarRule(start_type="num", start_value=0, end_type="num", end_value=100, color=color))
    _widths(ws, {"A": 30, "B": 12, "C": 12, "D": 14, "E": 14, "F": 12, "G": 12, "H": 12})
    _print_setup(ws)


def _sensitivity_sheet(wb, result, subtitle: str) -> None:
    ws = wb.create_sheet("Độ nhạy & dự báo")
    ws.sheet_properties.tabColor = "7C3AED"
    a = result.get("analysis") or {}
    sens = a.get("sensitivity") or {}
    names = [s["name"] for s in (sens.get("growth") or {}).get("series", [])]
    start = _sheet_title(ws, "Độ nhạy & dự báo tăng trưởng", subtitle, max(4, 1 + len(names)))
    row = start
    titles = {"growth": "Dự phòng tăng trưởng (%)", "cpuRatio": "Tỷ lệ chia sẻ CPU (vCPU/thread)", "maxUtil": "Mức dùng tối đa / host (%)"}
    tno = 0
    for key, title in titles.items():
        sw = sens.get(key)
        if not sw:
            continue
        row = _section(ws, row, f"Số host theo {title.lower()}", 1 + len(sw["series"]))
        top = row
        rows = [[(f"{x:g}x" if key == "cpuRatio" else f"{x:g}%") + (" (hiện tại)" if x == sw["current"] else "")]
                + [s["hosts"][i] for s in sw["series"]] for i, x in enumerate(sw["x"])]
        lst = _write_rows(ws, row, [title] + [s["name"] for s in sw["series"]], rows, fmts={i: NUM for i in range(2, 2 + len(sw["series"]))})
        tno += 1
        _table(ws, f"A{top}:{get_column_letter(1 + len(sw['series']))}{lst}", f"DoNhay{tno}", "TableStyleLight9")
        ws.conditional_formatting.add(f"B{top + 1}:{get_column_letter(1 + len(sw['series']))}{lst}",
                                      ColorScaleRule(start_type="min", start_color="DFE9F6", end_type="max", end_color="7FB2FF"))
        for i, x in enumerate(sw["x"]):
            if x == sw["current"]:
                for c in range(1, 2 + len(sw["series"])):
                    ws.cell(row=top + 1 + i, column=c).font = _font(10, True, NAVY)
        row = lst + 2
    proj = a.get("projection")
    if proj:
        row = _section(ws, row, f"Dự báo theo năm — tăng trưởng {proj['annualGrowthPct']:g}%/năm, đề xuất {proj['installed']} host", 4)
        top = row
        lst = _write_rows(ws, row, ["Năm", "Tăng trưởng lũy kế (%)", "Host cần", "Đánh giá"],
                          [["Hiện tại" if pt["year"] == 0 else f"Năm {pt['year']}", _r(pt["growthPct"]), pt["hosts"],
                            "Đáp ứng" if pt["hosts"] <= proj["installed"] else "Cần mở rộng"] for pt in proj["points"]],
                          fmts={2: NUM1, 3: NUM})
        _table(ws, f"A{top}:D{lst}", "DuBao", "TableStyleLight9")
        ws.conditional_formatting.add(f"D{top + 1}:D{lst}", CellIsRule(operator="equal", formula=['"Cần mở rộng"'], font=Font(color=CRIT, bold=True), fill=_fill(CRIT_BG)))
        ws.conditional_formatting.add(f"D{top + 1}:D{lst}", CellIsRule(operator="equal", formula=['"Đáp ứng"'], font=Font(color=OK, bold=True), fill=_fill(OK_BG)))
    ws.column_dimensions["A"].width = 34
    for i in range(2, 2 + max(4, len(names))):
        ws.column_dimensions[get_column_letter(i)].width = 20
    _print_setup(ws)


def _assumptions_sheet(wb, result, subtitle: str) -> None:
    ws = wb.create_sheet("Giả định & ngưỡng")
    ws.sheet_properties.tabColor = MUTED
    start = _sheet_title(ws, "Giả định & ngưỡng tư vấn", subtitle, 2)
    row = _section(ws, start, "Giả định sizing", 2)
    for ln in result["assumptions"]:
        ws.merge_cells(f"A{row}:B{row}")
        ws.cell(row=row, column=1, value="•  " + ln).font = _font(10)
        ws.cell(row=row, column=1).alignment = Alignment(wrap_text=True)
        row += 1
    row = _section(ws, row + 1, "Ngưỡng tư vấn áp dụng", 2)
    th = (result["params"] or {}).get("advisory") or {}
    defaults = sizing_advisory.AdvisoryThresholds().__dict__
    top = row
    rows = []
    for key, label, unit in sizing_advisory.THRESHOLD_META:
        v = th.get(key, defaults.get(key))
        rows.append([label, f"{v} {unit}".strip()])
    lst = _write_rows(ws, row, ["Ngưỡng", "Giá trị"], rows, wrap_cols=(2,))
    _table(ws, f"A{top}:B{lst}", "NguongTuVan", "TableStyleLight9")
    _widths(ws, {"A": 52, "B": 60})
    _print_setup(ws, landscape=False)


def _vm_sheet(wb, vms: List[Dict[str, Any]], result, subtitle: str) -> None:
    ws = wb.create_sheet("Danh sách VM")
    ws.sheet_properties.tabColor = FAINT
    header = ["Tên VM / nhóm", "Số lượng", "vCPU / VM", "RAM / VM (GB)", "Disk / VM (GB)", "Tổng vCPU", "Tổng RAM (GB)", "Tổng Disk (GB)", "Ghi chú"]
    start = _sheet_title(ws, "Danh sách máy ảo", subtitle, len(header))
    rows = []
    for i, v in enumerate(vms):
        r = start + 1 + i
        rows.append([v["name"], v["qty"], v["vcpu"], v["ramGB"], v["diskGB"], f"=B{r}*C{r}", f"=B{r}*D{r}", f"=B{r}*E{r}", v.get("note") or ""])
    last = _write_rows(ws, start, header, rows, fmts={2: NUM, 3: NUM, 4: NUM, 5: NUM, 6: NUM, 7: NUM, 8: NUM}, wrap_cols=(9,))
    if rows:
        _table(ws, f"A{start}:I{last}", "DanhSachVM")
        tot = last + 1
        ws.cell(row=tot, column=1, value="TỔNG")
        for col in "BFGH":
            ws[f"{col}{tot}"] = f"=SUM({col}{start + 1}:{col}{last})"
            ws[f"{col}{tot}"].number_format = NUM
        for c in range(1, 10):
            cell = ws.cell(row=tot, column=c)
            cell.font = _font(10, True)
            cell.fill = _fill(SURFACE2)
            cell.border = Border(top=Side(style="medium", color=NAVY))
    _widths(ws, {"A": 28, "B": 10, "C": 10, "D": 13, "E": 13, "F": 12, "G": 14, "H": 14, "I": 40})
    ws.freeze_panes = ws.cell(row=start + 1, column=2)
    _print_setup(ws)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def build_workbook(result: Dict[str, Any], vms: List[Dict[str, Any]], meta: Dict[str, Any]) -> Workbook:
    """`result` = sizing.build_sizing() output; `vms` = submitted VM rows (name/qty/vcpu/ramGB/diskGB/note);
    `meta` = {customerName, preparedOrg, preparedBy, documentCode, logoDataUrl}."""
    result = customer_view(result)
    wb = Workbook()
    env = result["params"].get("environment") or "Dự án mới"
    parts = [env]
    if meta.get("customerName"):
        parts.append(meta["customerName"])
    if meta.get("preparedOrg"):
        parts.append(meta["preparedOrg"])
    parts.append(dt.date.today().strftime("%d/%m/%Y"))
    subtitle = "   ·   ".join(parts)
    refs = _chart_data(wb, result)
    _dashboard(wb, result, meta, wb["Dữ liệu biểu đồ"], refs)
    _options_sheet(wb, result, subtitle)
    _tco_sheet(wb, result, subtitle)
    _findings_sheet(wb, result, subtitle)
    _workload_sheet(wb, result, subtitle)
    _sensitivity_sheet(wb, result, subtitle)
    _assumptions_sheet(wb, result, subtitle)
    _vm_sheet(wb, vms, result, subtitle)
    # Chart data last
    data = wb["Dữ liệu biểu đồ"]
    wb.move_sheet(data, offset=len(wb.sheetnames) - wb.sheetnames.index(data.title) - 1)
    wb.active = 0
    wb.properties.title = f"Đề xuất cấu hình máy chủ — {env}"
    wb.properties.creator = meta.get("preparedOrg") or "Capacity Ops"
    return wb
