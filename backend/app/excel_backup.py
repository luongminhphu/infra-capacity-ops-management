"""Excel workbook "Thống kê backup" — same styling as the sizing workbook.

Sheets: Tổng quan (KPI tiles, findings) · Theo ngày (native stacked chart) ·
Nguyên nhân lỗi · Client & RPO · Policy · Storage · Độ phủ VM · Job (raw list).
Every data range is an Excel table so the customer can filter / pivot it.
"""
import datetime as dt
import io
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font

from .excel_sizing import (BLUE, CRIT, CRIT_BG, NAVY, NUM, NUM1, OK, OK_BG, TEAL, WARN, WARN_BG, _fill, _font, _print_setup,
                           _section, _sheet_title, _table, _tile, _widths, _write_rows)
from .timeutil import VN_TZ

STATUS_TXT = {"ok": "Đạt", "warn": "Cần theo dõi", "crit": "Cần xử lý", "na": "Không hoạt động"}
SEV_TXT = {"critical": "Nghiêm trọng", "warning": "Cảnh báo", "info": "Lưu ý"}
OUTCOME_TXT = {"success": "Thành công", "partial": "Một phần", "failed": "Lỗi", "running": "Đang chạy"}


def _local(v: Any) -> Optional[dt.datetime]:
    """Naive VN-local datetime for Excel cells (Excel has no time zones)."""
    if v is None:
        return None
    if isinstance(v, str):
        try:
            v = dt.datetime.fromisoformat(v)
        except ValueError:
            return None
    if v.tzinfo is None:
        v = v.replace(tzinfo=dt.timezone.utc)
    return v.astimezone(VN_TZ).replace(tzinfo=None)


DATE_FMT = "dd/mm/yyyy hh:mm"


def _status_rules(ws, rng: str) -> None:
    for txt, fg, bg in (("Cần xử lý", CRIT, CRIT_BG), ("Nghiêm trọng", CRIT, CRIT_BG), ("Lỗi", CRIT, CRIT_BG),
                        ("Cần theo dõi", WARN, WARN_BG), ("Cảnh báo", WARN, WARN_BG), ("Một phần", WARN, WARN_BG),
                        ("Đạt", OK, OK_BG), ("Thành công", OK, OK_BG)):
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=[f'"{txt}"'], font=Font(color=fg, bold=True), fill=_fill(bg)))


def _sheet(wb, title: str, heading: str, subtitle: str, ncols: int, landscape: bool = True):
    ws = wb.create_sheet(title)
    start = _sheet_title(ws, heading, subtitle, ncols)
    _print_setup(ws, landscape)
    return ws, start


def _overview(wb, r: Dict[str, Any], servers: List[Dict[str, Any]], subtitle: str) -> None:
    ws = wb.active
    ws.title = "Tổng quan"
    _sheet_title(ws, "Thống kê hiện trạng Backup — Veritas NetBackup", subtitle, 12)
    _widths(ws, {c: 11 for c in "ABCDEFGHIJKL"})
    _print_setup(ws, landscape=False)
    k, th, cov = r["kpis"], r["thresholds"], r["coverage"]
    ws.merge_cells("A4:L4")
    ws["A4"] = r["headline"]
    ws["A4"].font = _font(11, True, NAVY)
    ws["A4"].alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[4].height = 36
    tiles = [
        ("Tỷ lệ thành công", f"{k['successRate'] or 0:.1f}%", f"Mục tiêu {th['success_target_pct']:.0f}% · {r['period']['days']} ngày", BLUE),
        ("Client đạt RPO", f"{k['rpoCompliancePct'] or 0:.1f}%", f"{k['clientsCrit']} cần xử lý · {k['clientsWarn']} theo dõi", TEAL),
        ("Độ phủ VM", f"{cov['pct']:.1f}%" if cov.get("available") and cov.get("pct") is not None else "—",
         f"{cov.get('unprotected', 0)} VM chưa backup" if cov.get("available") else "Chưa có dữ liệu vCenter", NAVY),
        ("Disk pool cao nhất", f"{k['storageMaxPct']:.0f}%" if k["storageMaxPct"] is not None else "—", k["storageMaxName"] or "", WARN),
    ]
    for i, (a, b, c, acc) in enumerate(tiles):
        _tile(ws, 6, 1 + i * 3, 3, a, b, c, acc)
    row = _section(ws, 10, "Chỉ số chính", 12)
    kv = [
        ("Job backup hoàn tất", k["jobs"]), ("Thành công", k["success"]), ("Thành công một phần", k["partial"]), ("Lỗi", k["failed"]),
        ("Đang chạy", k["running"]), ("Tỷ lệ thành công 24 giờ (%)", k["successRate24h"]), ("Dữ liệu backup trong kỳ (TB)", k["dataTB"]),
        ("Dữ liệu backup 24 giờ (TB)", k["dataTB24h"]), ("Số client", k["clients"]), ("Số policy (đang bật)", k["policiesActive"]),
        ("Job nhân bản DR", k["copyJobs"]), ("Tỷ lệ nhân bản thành công (%)", k["copyRate"]),
        ("Job chạy / chờ quá lâu", k["longJobs"]), ("Job tràn cửa sổ backup", k["windowOverrun"]),
    ]
    top = row
    last = _write_rows(ws, row, ["Chỉ số", "Giá trị"], [[a, b] for a, b in kv], fmts={2: "#,##0.##"})
    for rr in range(top, last + 1):
        ws.merge_cells(start_row=rr, start_column=2, end_row=rr, end_column=3)
    ws.column_dimensions["A"].width = 30
    row = _section(ws, last + 2, "Phát hiện & khuyến nghị", 12)
    for f in r["findings"]:
        ws.cell(row=row, column=1, value=SEV_TXT[f["severity"]]).font = _font(10, True)
        ws.cell(row=row, column=2, value=f["area"]).font = _font(10)
        ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=7)
        ws.cell(row=row, column=3, value=f["title"] + (f" — {f['detail']}" if f["detail"] else "")).alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=row, start_column=8, end_row=row, end_column=12)
        ws.cell(row=row, column=8, value=f["action"]).alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[row].height = 44
        row += 1
    _status_rules(ws, f"A{last + 3}:A{row}")
    row = _section(ws, row + 1, "Nguồn dữ liệu", 12)
    for s in servers:
        ws.cell(row=row, column=1, value=s["name"])
        ws.cell(row=row, column=3, value=f"{s['host']}:{s['port']}" + (" (demo)" if s.get("demo") else ""))
        ws.cell(row=row, column=6, value=_local(s.get("lastSyncAt"))).number_format = DATE_FMT
        row += 1


def _daily(wb, r, subtitle) -> None:
    ws, row = _sheet(wb, "Theo ngày", "Kết quả job backup theo ngày", subtitle, 6, landscape=False)
    rows = [[dt.datetime.strptime(d["date"], "%Y-%m-%d"), d["success"], d["partial"], d["failed"],
             None, d["tb"]] for d in r["daily"]]
    last = _write_rows(ws, row, ["Ngày", "Thành công", "Một phần", "Lỗi", "Tỷ lệ thành công", "Dữ liệu (TB)"], rows,
                       fmts={1: "dd/mm/yyyy", 2: NUM, 3: NUM, 4: NUM, 5: "0.0%", 6: "#,##0.00"})
    for rr in range(row + 1, last + 1):
        ws.cell(row=rr, column=5, value=f"=IFERROR((B{rr}+C{rr})/(B{rr}+C{rr}+D{rr}),\"\")").number_format = "0.0%"
    if rows:
        _table(ws, f"A{row}:F{last}", "TheoNgay")
        ch = BarChart()
        ch.type, ch.grouping, ch.overlap = "col", "stacked", 100
        ch.title = "Job backup theo ngày"
        ch.add_data(Reference(ws, min_col=2, max_col=4, min_row=row, max_row=last), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=row + 1, max_row=last))
        for s, c in zip(ch.series, ("15803D", "F59E0B", "B91C1C")):
            s.graphicalProperties.solidFill = c
            s.graphicalProperties.line.solidFill = c
        ch.height, ch.width = 8, 18
        ws.add_chart(ch, f"H{row}")
    _widths(ws, {"A": 14, "B": 12, "C": 12, "D": 10, "E": 16, "F": 14})


SCHED_TXT = {"ok": "Đạt", "partial": "Một phần", "late": "Trễ", "wrongtype": "Sai loại", "failed": "Lỗi", "missing": "Thiếu",
             "running": "Đang chạy", "pending": "Chưa đến lịch"}
WD = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]


def _schedule(wb, r, subtitle) -> None:
    sc = r.get("schedule") or {}
    nights = sc.get("nights") or []
    ws, row = _sheet(wb, "Lịch & tuân thủ", "Tuân thủ lịch backup theo từng đêm", subtitle, 6 + len(nights))
    rules = sc.get("rules") or []
    if not rules:
        ws.cell(row=row, column=1, value="Chưa có lịch backup.")
        return
    ws.cell(row=row, column=1, value=(f"Tuân thủ: {sc.get('compliancePct')}% đêm đạt ({sc.get('evaluated')} đêm có lịch). Đêm tính từ "
                                      f"{sc.get('cutoffHour')}:00 đến trước {sc.get('cutoffHour')}:00 hôm sau; trễ khi xong sau giờ kết thúc + "
                                      f"{sc.get('graceMinutes')} phút.")).font = _font(10, True, NAVY)
    row += 2
    import datetime as _dt
    head = ["Hệ thống / policy", "Nguồn lịch", "Full", "Incremental", "Cửa sổ"] + [
        f"{WD[_dt.date.fromisoformat(d).weekday()]} {d[8:10]}/{d[5:7]}" for d in nights] + ["Tuân thủ"]
    rows = []
    for x in rules:
        full = ("Không xét" if x.get("source") == "fixed" else f"Mỗi {x['fullEveryDays']} ngày" if x.get("fullEveryDays")
                else ", ".join(WD[d] for d in x["fullDays"]))
        rows.append([x["system"], {"netbackup": "NetBackup", "fixed": "Khung cố định"}.get(x.get("source"), "Thủ công / Excel"), full,
                     ", ".join(WD[d] for d in x["incrDays"]), f"{x['startTime']} → {x['endTime']}"]
                    + [SCHED_TXT.get(c.get("status"), "" if not c.get("expected") else "?") for c in x["nights"]]
                    + [(x["compliancePct"] / 100.0) if x.get("compliancePct") is not None else None])
    last = _write_rows(ws, row, head, rows, fmts={len(head): "0.0%"})
    from openpyxl.utils import get_column_letter
    rng = f"F{row + 1}:{get_column_letter(5 + len(nights))}{last}"
    _status_rules(ws, rng)
    for txt, fg, bg in (("Trễ", WARN, WARN_BG), ("Sai loại", WARN, WARN_BG), ("Thiếu", CRIT, CRIT_BG)):
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=[f'"{txt}"'], font=Font(color=fg, bold=True), fill=_fill(bg)))
    _widths(ws, {"A": 24, "B": 16, "C": 12, "D": 22, "E": 14})
    for i in range(len(nights)):
        ws.column_dimensions[get_column_letter(6 + i)].width = 10
    ws.freeze_panes = ws[f"F{row + 1}"]
    # Detail of nights that are not OK
    row = _section(ws, last + 2, "Các đêm chưa đạt", 6)
    detail = [[x["system"], c["date"], "Full" if c.get("expected") == "full" else "Incremental", SCHED_TXT.get(c["status"], ""), c.get("detail", "")]
              for x in rules for c in x["nights"] if c.get("status") in ("late", "wrongtype", "failed", "missing", "partial")]
    top = row
    l2 = _write_rows(ws, row, ["Hệ thống / policy", "Đêm", "Loại", "Kết quả", "Chi tiết"], detail, wrap_cols=(5,))
    if detail:
        _table(ws, f"A{top}:E{l2}", "DemChuaDat", "TableStyleLight9")


def _failures(wb, r, subtitle) -> None:
    ws, row = _sheet(wb, "Nguyên nhân lỗi", "Nguyên nhân lỗi theo nhóm và mã NetBackup", subtitle, 5)
    row = _section(ws, row, "Theo nhóm nguyên nhân", 5)
    top = row
    last = _write_rows(ws, row, ["Nhóm", "Số job", "Số client", "Hướng xử lý"],
                       [[g["label"], g["count"], g["clientCount"], g["action"]] for g in r["failureGroups"]], wrap_cols=(4,))
    if r["failureGroups"]:
        _table(ws, f"A{top}:D{last}", "NhomLoi")
    row = _section(ws, last + 2, "Theo mã lỗi", 5)
    top = row
    last = _write_rows(ws, row, ["Mã", "Mô tả", "Nhóm", "Số job", "Client"],
                       [[c["code"], c["desc"], c["group"], c["count"], ", ".join(c["clients"])] for c in r["topCodes"]], wrap_cols=(5,))
    if r["topCodes"]:
        _table(ws, f"A{top}:E{last}", "MaLoi")
    _widths(ws, {"A": 28, "B": 40, "C": 24, "D": 70, "E": 50})


def _clients(wb, r, subtitle) -> None:
    ws, row = _sheet(wb, "Client & RPO", "Client — RPO và lỗi lặp lại", subtitle, 12)
    rows = [[c["client"], ", ".join(c["policies"]), ", ".join(c["policyTypes"]), _local(c["lastJobAt"]), c["lastStatus"], c["lastStatusText"],
             _local(c["lastSuccessAt"]), c["hoursSinceSuccess"], c["consecutiveFail"], c["jobs"], c["failed"], STATUS_TXT[c["status"]],
             c["note"]] for c in r["clients"]]
    last = _write_rows(ws, row, ["Client", "Policy", "Loại", "Job gần nhất", "Mã", "Kết quả gần nhất", "Thành công gần nhất",
                                 "Giờ từ lần thành công", "Lỗi liên tiếp", "Job trong kỳ", "Lỗi trong kỳ", "Đánh giá", "Ghi chú"], rows,
                       fmts={4: DATE_FMT, 7: DATE_FMT, 8: NUM1}, wrap_cols=(2, 13))
    if rows:
        _table(ws, f"A{row}:M{last}", "ClientRPO")
        _status_rules(ws, f"L{row + 1}:L{last}")
    ws.freeze_panes = ws[f"B{row + 1}"]
    _widths(ws, {"A": 28, "B": 28, "C": 16, "D": 16, "E": 7, "F": 30, "G": 16, "H": 12, "I": 10, "J": 10, "K": 10, "L": 14, "M": 50})


def _policies(wb, r, subtitle) -> None:
    ws, row = _sheet(wb, "Policy", "Policy backup", subtitle, 11)
    rows = [[p["nbuKey"], p["name"], p["policyType"], "Bật" if p["active"] else ("Tắt" if p["active"] is False else ""), p["clients"],
             p["storage"], p["schedules"], _local(p["lastRunAt"]), p["jobs"], p["failed"],
             (p["successRate"] / 100.0) if p["successRate"] is not None else None, STATUS_TXT.get(p["status"], p["statusLabel"])] for p in r["policies"]]
    last = _write_rows(ws, row, ["Server", "Policy", "Loại", "Hoạt động", "Client", "Storage", "Lịch (retention)", "Chạy gần nhất",
                                 "Job", "Lỗi", "Tỷ lệ", "Đánh giá"], rows, fmts={8: DATE_FMT, 11: "0.0%"}, wrap_cols=(7,))
    if rows:
        _table(ws, f"A{row}:L{last}", "Policy")
        _status_rules(ws, f"L{row + 1}:L{last}")
    _widths(ws, {"A": 10, "B": 26, "C": 16, "D": 10, "E": 8, "F": 18, "G": 40, "H": 16, "I": 8, "J": 8, "K": 9, "L": 14})


def _storage(wb, r, subtitle) -> None:
    ws, row = _sheet(wb, "Storage", "Dung lượng disk pool", subtitle, 11)
    rows = [[s["nbuKey"], s["name"], s["type"], s["state"], s["totalTB"], s["usedTB"], s["freeTB"],
             (s["usedPct"] / 100.0) if s["usedPct"] is not None else None, s["growthTBPerDay"], s["daysToFull"],
             STATUS_TXT.get(s["status"], s["statusLabel"])] for s in r["storage"]]
    last = _write_rows(ws, row, ["Server", "Disk pool", "Loại", "Trạng thái", "Tổng (TB)", "Đã dùng (TB)", "Còn trống (TB)", "% dùng",
                                 "Tăng (TB/ngày)", "Ngày tới HWM", "Đánh giá"], rows,
                       fmts={5: NUM1, 6: NUM1, 7: NUM1, 8: "0.0%", 9: "#,##0.000", 10: NUM})
    if rows:
        _table(ws, f"A{row}:K{last}", "DiskPool")
        _status_rules(ws, f"K{row + 1}:K{last}")
    tr = r["storageTrend"]
    if len(tr["dates"]) >= 2:
        row = _section(ws, last + 2, "Mức dùng 30 ngày (%)", 11)
        hdr = ["Ngày"] + [s["name"] for s in tr["series"]]
        rows = [[dt.datetime.strptime(d, "%Y-%m-%d")] + [(s["values"][i] / 100.0) if s["values"][i] is not None else None for s in tr["series"]]
                for i, d in enumerate(tr["dates"])]
        top = row
        last2 = _write_rows(ws, row, hdr, rows, fmts={1: "dd/mm/yyyy", **{j: "0.0%" for j in range(2, len(hdr) + 1)}})
        _table(ws, f"A{top}:{chr(64 + len(hdr))}{last2}", "XuHuongPool", "TableStyleLight9")
    _widths(ws, {"A": 12, "B": 22, "C": 14, "D": 11, "E": 11, "F": 12, "G": 13, "H": 10, "I": 13, "J": 12, "K": 14})


def _coverage(wb, r, subtitle) -> None:
    cov = r["coverage"]
    ws, row = _sheet(wb, "Độ phủ VM", "Độ phủ backup máy ảo (đối chiếu inventory vCenter)", subtitle, 7)
    if not cov.get("available"):
        ws.cell(row=row, column=1, value="Chưa có dữ liệu inventory vCenter để đối chiếu.")
        return
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
    c = ws.cell(row=row, column=1, value=("Phạm vi cần backup: " + cov["scopeLabel"] + f" — {cov['outOfScope']} VM ngoài phạm vi không tính.")
                if cov.get("scopeActive") else "Phạm vi cần backup: toàn bộ VM trong inventory vCenter.")
    c.font = _font(10, True, NAVY)
    c.alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[row].height = 32
    row = _section(ws, row + 2, "Theo cluster", 7)
    top = row
    last = _write_rows(ws, row, ["vCenter", "Cluster", "VM đang chạy", "Có backup", "Có job nhưng lỗi", "Chưa backup", "Độ phủ"],
                       [[c["vcenterKey"], c["cluster"], c["total"], c["protected"], c["failing"], c["unprotected"],
                         (c["pct"] / 100.0) if c["pct"] is not None else None] for c in cov["byCluster"]], fmts={7: "0.0%"})
    if cov["byCluster"]:
        _table(ws, f"A{top}:G{last}", "DoPhuCluster")
    row = _section(ws, last + 2, "VM chưa có backup / backup lỗi", 7)
    top = row
    vms = cov["unprotectedVms"] + cov["failingVms"]
    last = _write_rows(ws, row, ["VM", "vCenter", "Cluster", "Host", "IP", "vCPU", "RAM (GB)", "Tình trạng"],
                       [[v["vm"], v["vcenterKey"], v["cluster"], v.get("host"), v.get("ip"), v.get("vcpu"), v.get("ramGB"),
                         "Chưa backup" if v["state"] == "unprotected" else "Có job nhưng lỗi"] for v in vms])
    if vms:
        _table(ws, f"A{top}:H{last}", "VMChuaBackup")
    _widths(ws, {"A": 32, "B": 14, "C": 22, "D": 26, "E": 16, "F": 8, "G": 10, "H": 18})


def _jobs(wb, jobs: List[Dict[str, Any]], subtitle) -> None:
    ws, row = _sheet(wb, "Job", "Danh sách job trong kỳ", subtitle, 16)
    rows = [[j["nbuKey"], j["jobId"], j["parentJobId"], j["jobType"], j["policyName"], j["scheduleName"], j["scheduleLabel"], j["clientName"],
             _local(j["startTime"]), _local(j["endTime"]), round((j["elapsedSec"] or 0) / 60, 1), round((j["kbytes"] or 0) / 1024 ** 2, 3),
             j["state"], j["status"], OUTCOME_TXT.get(j["outcome"], ""), j["statusText"], j["group"], j["storageUnit"]] for j in jobs]
    last = _write_rows(ws, row, ["Server", "Job ID", "Job cha", "Loại job", "Policy", "Schedule", "Kiểu lịch", "Client", "Bắt đầu",
                                 "Kết thúc", "Thời lượng (phút)", "Dữ liệu (GB)", "Trạng thái", "Mã", "Kết quả", "Mô tả", "Nhóm lỗi",
                                 "Storage unit"], rows, fmts={9: DATE_FMT, 10: DATE_FMT, 11: NUM1, 12: "#,##0.00"})
    if rows:
        _table(ws, f"A{row}:R{last}", "Job", "TableStyleLight9")
        _status_rules(ws, f"O{row + 1}:O{last}")
    ws.freeze_panes = ws[f"C{row + 1}"]
    _widths(ws, {"A": 9, "B": 10, "C": 10, "D": 13, "E": 22, "F": 14, "G": 18, "H": 26, "I": 16, "J": 16, "K": 10, "L": 11,
                 "M": 10, "N": 6, "O": 12, "P": 30, "Q": 20, "R": 16})


def build_backup_xlsx(r: Dict[str, Any], jobs: List[Dict[str, Any]], servers: List[Dict[str, Any]], scope_label: str) -> bytes:
    p = r["period"]
    subtitle = (f"{scope_label}   ·   Kỳ {_local(p['from']):%d/%m/%Y} – {_local(p['to']):%d/%m/%Y} ({p['days']} ngày)"
                f"   ·   Lập {dt.datetime.now(VN_TZ):%d/%m/%Y %H:%M}")
    wb = Workbook()
    _overview(wb, r, servers, subtitle)
    _daily(wb, r, subtitle)
    _schedule(wb, r, subtitle)
    _failures(wb, r, subtitle)
    _clients(wb, r, subtitle)
    _policies(wb, r, subtitle)
    _storage(wb, r, subtitle)
    _coverage(wb, r, subtitle)
    _jobs(wb, jobs, subtitle)
    wb.properties.creator = "Infra Capacity Ops"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
