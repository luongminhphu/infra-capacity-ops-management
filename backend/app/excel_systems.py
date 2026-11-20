"""Excel "Danh sách VM theo hệ thống" — same numbers as owners.inventory.build / report_systems.py.
Also attached to the owner review e-mail drafts (filtered to the systems of that owner)."""
import io
from typing import Any, Dict, List

from openpyxl import Workbook

from .excel_style import _sheet_title, _widths, _write_rows

VM_HEAD = ["VM", "Hostname (trong máy ảo)", "vCenter", "Cluster", "IP", "Host", "Nguồn", "vCPU", "RAM (GB)", "Disk (GB)",
           "Hệ điều hành", "Datastore", "Backup"]


def _vm(v: Dict[str, Any]) -> List[Any]:
    return [v["vm"], v.get("hostname"), v["vcenterKey"], v["cluster"], v["ip"], v["host"], "Bật" if v["powerOn"] else "Tắt", v["vcpu"],
            v["ramGB"], v["diskGB"], v["os"], v["datastore"], v["backupLabel"]]


def build_xlsx(d: Dict[str, Any], title: str = "Danh sách VM theo hệ thống") -> bytes:
    t, systems = d["totals"], d["systems"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Tổng hợp"
    row = _sheet_title(ws, title, d["scope"] or "Phạm vi: toàn bộ hệ thống", 17)
    head = ["Mã", "Hệ thống", "Dự án", "Phòng ban", "Môi trường", "Mức quan trọng", "Owner (email)", "Tên owner", "Backup (email)",
            "Tên backup", "Support", "Quản lý", "VM", "VM bật", "vCPU", "RAM (GB)", "Disk (GB)"]
    rows = [[s["code"], s["name"], s["project"], s["department"], s["envLabel"], s["criticalityLabel"], s["ownerEmail"], s["ownerName"],
             s["backupEmail"], s["backupName"],
             " · ".join(x for x in (s["supportName"], ", ".join(s["supportEmails"]), s["supportContact"]) if x) or None,
             s["managerEmail"], s["totals"]["vms"], s["totals"]["on"], s["totals"]["vcpu"], s["totals"]["ramGB"], s["totals"]["diskGB"]]
            for s in systems]
    rows.append(["Tổng", f"{t['systems']} hệ thống", None, None, None, None, None, None, None, None, None, None,
                 t["vms"], t["on"], t["vcpu"], t["ramGB"], t["diskGB"]])
    _write_rows(ws, row, head, rows, {15: "#,##0", 16: "#,##0.0", 17: "#,##0.0"})
    _widths(ws, {"A": 11, "B": 30, "C": 18, "D": 18, "E": 12, "F": 18, "G": 30, "H": 20, "I": 30, "J": 20, "K": 36, "L": 28,
                 "M": 8, "N": 8, "O": 9, "P": 10, "Q": 11})
    ws.freeze_panes = ws.cell(row=row + 1, column=3)
    wv = wb.create_sheet("VM theo hệ thống")
    r2 = _sheet_title(wv, "VM theo hệ thống", "Một dòng một VM, kèm hệ thống và owner", 16)
    _write_rows(wv, r2, ["Mã hệ thống", "Hệ thống", "Owner (email)"] + VM_HEAD,
                [[s["code"], s["name"], s["ownerEmail"]] + _vm(v) for s in systems for v in s["vms"]],
                {11: "#,##0.0", 12: "#,##0.0", 13: "#,##0.0"})
    _widths(wv, {"A": 11, "B": 26, "C": 28, "D": 32, "E": 26, "F": 10, "G": 16, "H": 15, "I": 18, "J": 7, "K": 6, "L": 9, "M": 10,
                 "N": 30, "O": 20, "P": 13})
    wv.freeze_panes = wv.cell(row=r2 + 1, column=4)
    if d["unowned"] is not None:
        wu = wb.create_sheet("VM chưa thuộc hệ thống")
        r3 = _sheet_title(wu, "VM chưa thuộc hệ thống", f"{len(d['unowned'])} VM chưa có owner chịu trách nhiệm", len(VM_HEAD))
        _write_rows(wu, r3, VM_HEAD, [_vm(v) for v in d["unowned"]], {9: "#,##0.0", 10: "#,##0.0"})
        _widths(wu, {"A": 32, "B": 26, "C": 10, "D": 16, "E": 15, "F": 18, "G": 7, "H": 6, "I": 9, "J": 10, "K": 30, "L": 20, "M": 13})
        wu.freeze_panes = wu.cell(row=r3 + 1, column=2)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
