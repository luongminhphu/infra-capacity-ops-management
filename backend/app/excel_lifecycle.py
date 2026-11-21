"""Excel "Vòng đời & bảo mật" — same numbers as lifecycle.service.report (page, PDF, Hộp việc). ICO v2 G9b."""
from typing import Any, Dict

from openpyxl import Workbook

from .excel_style import _sheet_title, _widths, _write_rows, to_bytes
from .shared.levels import LEVEL_TEXT

LV = {**LEVEL_TEXT, "na": "Không xác định"}


def build_xlsx(d: Dict[str, Any]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Vòng đời"
    r = _sheet_title(ws, "Vòng đời ESXi / vCenter", "Theo bảng End of General Support do admin nhập", 10)
    _write_rows(ws, r, ["Mức", "Sản phẩm", "Đối tượng", "vCenter", "Cluster", "Phiên bản", "Build", "Dòng EoGS", "Hết hỗ trợ (EoGS)", "Còn (ngày)"],
                [[LV[x["level"]], x["productLabel"], x["name"], x["vcenterKey"], x.get("cluster"), x.get("version"), x.get("build"), x["line"],
                  x["eogs"], x["daysLeft"]] for x in d["lifecycle"]])
    _widths(ws, {"A": 14, "B": 10, "C": 32, "D": 12, "E": 22, "F": 10, "G": 12, "H": 10, "I": 16, "J": 10})
    wv = wb.create_sheet("VMSA chưa vá")
    r = _sheet_title(wv, "VMSA chưa vá", "Build hiện tại thấp hơn build đã vá", 9)
    _write_rows(wv, r, ["Mức", "VMSA", "CVSS", "Tiêu đề", "Sản phẩm", "Đối tượng", "vCenter", "Build hiện tại", "Build đã vá"],
                [[LV[x["level"]], x["vmsa"], x["cvss"], x.get("title"), x["productLabel"], x["name"], x["vcenterKey"], x.get("build"), x["fixedBuild"]]
                 for x in d["vmsa"]])
    _widths(wv, {"A": 14, "B": 16, "C": 8, "D": 34, "E": 10, "F": 32, "G": 12, "H": 14, "I": 14})
    wc = wb.create_sheet("Chứng chỉ TLS")
    r = _sheet_title(wc, "Chứng chỉ TLS", "Đọc bằng TLS handshake", 11)
    _write_rows(wc, r, ["Mức", "Loại", "Tên", "Máy chủ", "Cổng", "Hết hạn", "Còn (ngày)", "Tự ký", "Subject", "Issuer", "Lỗi"],
                [[LV[c["level"]], c["kindLabel"], c["name"], c["host"], c["port"], c["notAfter"], c["daysLeft"],
                  "Có" if c.get("selfSigned") else "", c.get("subject"), c.get("issuer"), c.get("error")] for c in d["certs"]])
    _widths(wc, {"A": 14, "B": 14, "C": 30, "D": 30, "E": 7, "F": 22, "G": 10, "H": 7, "I": 34, "J": 34, "K": 40})
    return to_bytes(wb)
