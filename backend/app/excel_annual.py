"""Excel "Tổng kết nhu cầu năm & kế hoạch năm sau" — same numbers as itsm.annual.build / report_annual.py (ICO v2 G8)."""
from typing import Any, Dict

from openpyxl import Workbook

from .excel_style import _sheet_title, _widths, _write_rows, to_bytes

NUM = "#,##0.0"


def build_xlsx(d: Dict[str, Any]) -> bytes:
    s, f, fc = d["summary"], d["fulfilment"], d["forecast"]
    t, n, st = fc["totals"], fc["node"], s["totals"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Kết luận"
    row = _sheet_title(ws, f"Kế hoạch hạ tầng ảo hoá {d['planYear']}", f"Host chuẩn {n['name']} · mục tiêu {fc['targetPct']:g}% ngưỡng · N+1", 2)
    rows = [["Host cần thêm", t["nodes"]], ["Đơn giá host (đ, chưa VAT)", n["unitPrice"]], ["Ngân sách (đ, chưa VAT)", t["amount"]],
            ["vCPU hiện tại", t["usedVcpu"]], ["RAM hiện tại (GB)", t["usedRamGB"]], ["vCPU tăng tự nhiên", t["organicVcpu"]],
            ["RAM tăng tự nhiên (GB)", t["organicRamGB"]], ["vCPU nhu cầu đã biết", t["knownVcpu"]], ["RAM nhu cầu đã biết (GB)", t["knownRamGB"]],
            ["vCPU thu hồi", t["reclaimVcpu"]], ["RAM thu hồi (GB)", t["reclaimRamGB"]], ["vCPU ròng", t["netVcpu"]], ["RAM ròng (GB)", t["netRamGB"]],
            ["Nhân hệ số dùng thật / xin", "Có" if fc["useFactor"] else "Không"],
            [f"Yêu cầu năm {d['year']}", st["requests"]], [f"VM mới năm {d['year']}", st["vms"]], ["vCPU xin", st["vcpu"]],
            ["RAM xin (GB)", st["ramGB"]], ["Disk xin (GB)", st["diskGB"]], ["Thời gian xử lý trung vị (ngày)", st["medianDays"]]]
    _write_rows(ws, row, ["Chỉ tiêu", "Giá trị"], rows, {2: "#,##0.0"})
    _widths(ws, {"A": 38, "B": 22})

    wc = wb.create_sheet("Theo cluster")
    r = _sheet_title(wc, "Nhu cầu ròng và host cần thêm theo cluster", "ròng = hiện tại + tăng tự nhiên + đã biết − thu hồi", 18)
    _write_rows(wc, r, ["Cluster", "Host", "vCPU hiện tại", "RAM hiện tại", "vCPU tự nhiên", "RAM tự nhiên", "vCPU đã biết", "RAM đã biết",
                        "vCPU thu hồi", "RAM thu hồi", "vCPU ròng", "RAM ròng", "% CPU ròng", "% RAM ròng", "Host thêm", "Tài nguyên quyết định",
                        "% CPU sau", "% RAM sau"],
                [[c["name"], c["hosts"], c["usedVcpu"], c["usedRamGB"], c["organicVcpu"], c["organicRamGB"], c["knownVcpu"], c["knownRamGB"],
                  c["reclaimVcpu"], c["reclaimRamGB"], c["netVcpu"], c["netRamGB"], c["cpuPct"], c["ramPct"], c["nodes"], c["binding"],
                  c["cpuPctAfter"], c["ramPctAfter"]] for c in fc["clusters"]], {k: NUM for k in range(3, 15)} | {17: NUM, 18: NUM})
    _widths(wc, {"A": 24})

    wy = wb.create_sheet("Tổng kết năm")
    r = _sheet_title(wy, f"Nhu cầu năm {d['year']}", "Yêu cầu đọc tự động hoặc đã duyệt", 6)
    for label, key in (("Quý", "byQuarter"), ("Phòng ban", "byDepartment"), ("Dự án", "byProject"), ("Môi trường", "byEnv")):
        r = _write_rows(wy, r, [label, "Yêu cầu", "VM", "vCPU", "RAM (GB)", "Disk (GB)"],
                        [[x["key"], x["requests"], x["vms"], x["vcpu"], x["ramGB"], x["diskGB"]] for x in s[key]], {4: NUM, 5: NUM, 6: NUM}) + 2
    _widths(wy, {"A": 30, "B": 10, "C": 10, "D": 12, "E": 12, "F": 12})

    wf = wb.create_sheet("Xin - cấp - dùng")
    r = _sheet_title(wf, "Xin → cấp → dùng thật theo phòng ban", "Dùng = cấu hình × % sử dụng trung bình 7 ngày từ ngày 90 / 180", 14)
    r = _write_rows(wf, r, ["Phòng ban", "VM", "vCPU xin", "RAM xin", "vCPU cấp", "RAM cấp", "VM đo 90 ngày", "vCPU dùng 90", "RAM dùng 90",
                            "VM đo 180 ngày", "vCPU dùng 180", "RAM dùng 180", "Dùng / xin vCPU", "Dùng / xin RAM"],
                    [[x["department"], x["vms"], x["reqVcpu"], x["reqRamGB"], x["provVcpu"], x["provRamGB"], x["vms90"], x["used90Vcpu"],
                      x["used90RamGB"], x["vms180"], x["used180Vcpu"], x["used180RamGB"], x["ratioVcpu"], x["ratioRamGB"]]
                     for x in f["departments"] + [{**f["overall"], "department": "Toàn bộ"}]], {k: NUM for k in range(3, 15)}) + 2
    _write_rows(wf, r, ["VM", "IP", "vCenter", "Cluster", "Yêu cầu", "Phòng ban", "Xuất hiện", "vCPU xin", "RAM xin", "vCPU cấp", "RAM cấp",
                        "vCPU dùng 90", "RAM dùng 90", "vCPU dùng 180", "RAM dùng 180"],
                [[v["vm"], v.get("ip"), v["vcenterKey"], v["cluster"], v["reqId"], v["department"], v["appearedAt"], v["reqVcpu"], v["reqRamGB"],
                  v["provVcpu"], v["provRamGB"], (v["used90"] or {}).get("vcpu"), (v["used90"] or {}).get("ramGB"),
                  (v["used180"] or {}).get("vcpu"), (v["used180"] or {}).get("ramGB")] for v in f["vms"]], {k: NUM for k in range(8, 16)})
    _widths(wf, {"A": 30, "B": 15, "F": 24, "G": 22})

    wk = wb.create_sheet("Nhu cầu đã biết")
    r = _sheet_title(wk, f"Nhu cầu đã biết cho năm {d['planYear']}", "Yêu cầu đã duyệt chưa cấp + dự án năm sau nhập tay", 10)
    src = {"approved": "Yêu cầu đã duyệt, chưa cấp", "manual": "Dự án năm sau"}
    r = _write_rows(wk, r, ["Nhu cầu", "Nguồn", "Phòng ban", "Cluster", "vCPU", "Hệ số vCPU", "vCPU tính", "RAM (GB)", "Hệ số RAM", "RAM tính"],
                    [[k["label"], src.get(k["source"], k["source"]), k.get("department"), k["cluster"], k.get("vcpu"), k["factorVcpu"],
                      k["vcpuNet"], k.get("ramGB"), k["factorRamGB"], k["ramGBNet"]] for k in fc["known"]], {5: NUM, 7: NUM, 8: NUM, 10: NUM}) + 2
    _write_rows(wk, r, ["Thu hồi đã cam kết (VM)", "Quyết định", "Hệ thống", "Cluster", "vCPU", "RAM (GB)"],
                [[x["label"], "Thu hồi" if x["decision"] == "decommission" else "Giảm cấu hình", x.get("systemCode"), x["cluster"], x["vcpu"],
                  x["ramGB"]] for x in fc["reclaims"]], {5: NUM, 6: NUM})
    _widths(wk, {"A": 36, "B": 26, "C": 22, "D": 20})
    return to_bytes(wb)
