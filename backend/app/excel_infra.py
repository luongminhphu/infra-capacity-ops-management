"""Excel "Sức khoẻ hạ tầng" — every row of infra.report_data (same numbers as report_infra.py and the pages)."""
import io
from typing import Any, Dict

from openpyxl import Workbook

from .excel_style import _sheet_title, _widths, _write_rows

WORD = {"crit": "VƯỢT NGƯỠNG", "bad": "CẢNH BÁO", "warn": "THEO DÕI", "ok": "AN TOÀN"}


def build_xlsx(d: Dict[str, Any], scope_label: str = "") -> bytes:
    ds, st, sn, al, hh = d["datastores"], d["storage"], d["snapshots"], d["alarms"], d["health"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Tổng hợp"
    row = _sheet_title(ws, "Sức khoẻ hạ tầng", scope_label or "Phạm vi: toàn bộ", 3)
    lv = lambda rows, k: sum(1 for r in rows if r["level"] == k)          # noqa: E731
    rows = [["Datastore", len(ds), f"{lv(ds, 'crit')} vượt ngưỡng · {lv(ds, 'bad')} cảnh báo · {lv(ds, 'warn')} theo dõi"],
            ["Mảng lưu trữ", len(st["arrays"]), f"{sum(1 for a in st['arrays'] if a['level'] != 'ok')} có vấn đề"],
            ["Volume / LUN", len(st["volumes"]), f"{sum(1 for v in st['volumes'] if v['datastores'])} đã ghép datastore"],
            ["VM có snapshot", len(sn), f"{lv(sn, 'crit') + lv(sn, 'bad')} cũ / lớn"],
            ["Alarm vCenter", len(al), f"{sum(1 for a in al if a['status'] == 'red')} đỏ · {sum(1 for a in al if not a['acknowledged'])} chưa xác nhận"],
            ["Host", len(hh), f"{sum(1 for h in hh if h['level'] != 'ok')} có vấn đề"]]
    _write_rows(ws, row, ["Hạng mục", "Số lượng", "Ghi chú"], rows)
    _widths(ws, {"A": 22, "B": 12, "C": 52})

    w = wb.create_sheet("Datastore")
    r = _sheet_title(w, "Datastore", "Sắp theo mức rồi theo % đã dùng; “Đầy sau” theo xu hướng 30 ngày", 15)
    _write_rows(w, r, ["Mức", "Datastore", "vCenter", "Loại", "Cluster", "Mảng / volume", "Dung lượng (GB)", "Đã dùng (GB)", "Trống (GB)",
                       "Đã dùng (%)", "Cấp phát (%)", "Tăng (GB/ngày)", "Đầy sau (ngày)", "Ngày đầy dự kiến", "Lý do"],
                [[WORD[x["level"]], x["name"], x["vcenterKey"], x["type"], ", ".join(x["clusters"]),
                  " / ".join(v for v in ((x["storage"] or {}).get("array"), (x["storage"] or {}).get("volume")) if v) or None,
                  x["capacityGB"], x["usedGB"], x["freeGB"], x["usedPct"], x["provisionedPct"], x["growthGBPerDay"], x["daysToFull"],
                  x["fullDate"], "; ".join(x["reasons"])] for x in ds], {7: "#,##0.0", 8: "#,##0.0", 9: "#,##0.0", 10: "0.0", 11: "0.0"})
    _widths(w, {"A": 14, "B": 30, "C": 10, "D": 8, "E": 26, "F": 28, "G": 12, "H": 12, "I": 12, "J": 10, "K": 10, "L": 12, "M": 12,
                "N": 14, "O": 40})
    w.freeze_panes = w.cell(row=r + 1, column=3)

    w = wb.create_sheet("Mảng lưu trữ")
    r = _sheet_title(w, "Mảng lưu trữ", "Pure FlashArray / NetApp ONTAP — dung lượng, hiệu năng, alert", 12)
    _write_rows(w, r, ["Mức", "Mảng", "Hãng", "Model", "Phiên bản", "Dung lượng (GB)", "Đã dùng (%)", "Giảm dữ liệu", "Độ trễ đọc (ms)",
                       "Độ trễ ghi (ms)", "Alert", "Lý do"],
                [[WORD[a["level"]], a["name"], a["vendor"], a["model"], a["version"], a["capacityGB"], a["usedPct"], a["dataReduction"],
                  a["latencyReadMs"], a["latencyWriteMs"], len(a["alerts"]), "; ".join(a["reasons"])] for a in st["arrays"]],
                {6: "#,##0", 7: "0.0"})
    _widths(w, {"A": 14, "B": 26, "C": 9, "D": 18, "E": 12, "F": 14, "G": 10, "H": 10, "I": 12, "J": 12, "K": 8, "L": 40})

    w = wb.create_sheet("Volume")
    r = _sheet_title(w, "Volume / LUN", "Ghép với datastore VMware theo NAA (block) hoặc đường dẫn NFS", 9)
    _write_rows(w, r, ["Mảng", "Volume", "Loại", "Dung lượng (GB)", "Đã dùng (%)", "NAA", "NFS", "Datastore", "Số VM"],
                [[v["array"], v["name"], v["kind"], v["sizeGB"], v["usedPct"], v["naa"], v["nfsPath"],
                  ", ".join(f"{x['datastore']} ({x['vcenterKey']})" for x in v["datastores"]) or None, v["vmCount"]] for v in st["volumes"]],
                {4: "#,##0", 5: "0.0"})
    _widths(w, {"A": 22, "B": 32, "C": 8, "D": 14, "E": 10, "F": 36, "G": 30, "H": 36, "I": 8})

    w = wb.create_sheet("Snapshot VM")
    r = _sheet_title(w, "Snapshot VM", "Một dòng một VM; snapshot của phần mềm backup đánh dấu ở cột “Của backup”", 10)
    _write_rows(w, r, ["Mức", "VM", "vCenter", "Cluster", "Host", "Số snapshot", "Cũ nhất (ngày)", "Dung lượng (GB)", "Của backup", "Lý do"],
                [[WORD[x["level"]], x["vm"], x["vcenterKey"], x["cluster"], x["host"], x["count"], x["ageDays"], x["sizeGB"],
                  "Có" if x["backupLike"] else None, "; ".join(x["reasons"])] for x in sn], {7: "0.0", 8: "#,##0.0"})
    _widths(w, {"A": 14, "B": 30, "C": 10, "D": 18, "E": 22, "F": 10, "G": 12, "H": 12, "I": 10, "J": 36})

    w = wb.create_sheet("Alarm")
    r = _sheet_title(w, "Alarm vCenter", "Alarm đỏ / vàng đang kích hoạt", 8)
    _write_rows(w, r, ["Mức", "Alarm", "Loại đối tượng", "Đối tượng", "vCenter", "Cluster", "Từ lúc", "Đã xác nhận"],
                [[WORD[a["level"]], a["alarm"], a["entityType"], a["entity"], a["vcenterKey"], a["cluster"], a["at"],
                  "Có" if a["acknowledged"] else "Chưa"] for a in al])
    _widths(w, {"A": 14, "B": 40, "C": 14, "D": 30, "E": 10, "F": 18, "G": 22, "H": 12})

    w = wb.create_sheet("Sức khoẻ host")
    r = _sheet_title(w, "Sức khoẻ host", "Mọi host: kết nối, bảo trì, cảm biến bất thường", 7)
    _write_rows(w, r, ["Mức", "Host", "vCenter", "Cluster", "Kết nối", "Bảo trì", "Cảm biến / lý do"],
                [[WORD[h["level"]], h["host"], h["vcenterKey"], h["cluster"], h["connection"], "Có" if h["maintenance"] else None,
                  "; ".join(h["reasons"])] for h in hh])
    _widths(w, {"A": 14, "B": 30, "C": 10, "D": 18, "E": 14, "F": 8, "G": 50})
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
