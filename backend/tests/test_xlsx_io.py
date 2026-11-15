"""Shared xlsx reader (platform/xlsx_io): zip-bomb guard applied to every upload that reads Excel —
sizing VM import and backup schedule import included (they opened workbooks directly before)."""
import io
import zipfile

import pytest
from openpyxl import Workbook

from app.platform import xlsx_io

from test_spend_opex import admin, app_  # noqa: F401  (fixtures)


def bomb() -> bytes:
    """Valid-looking .xlsx whose shared strings part unzips to 16 MB (compresses to a few KB)."""
    wb = Workbook()
    wb.active.append(["VM", "vCPU", "RAM"])
    buf = io.BytesIO()
    wb.save(buf)
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            z.writestr(info, src.read(info))
        z.writestr("xl/padding.xml", b"A" * (16 * 1024 * 1024))
    return out.getvalue()


def small() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "VMs"
    ws.append(["VM", "vCPU", "RAM GB"])
    ws.append(["app-01", 4, 16])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_reader_bounds_and_guard():
    assert len(bomb()) < 200_000
    with pytest.raises(xlsx_io.XlsxError, match="quá lớn"):
        xlsx_io.read_rows(bomb())
    with pytest.raises(xlsx_io.XlsxError):
        xlsx_io.read_rows(b"PK\x03\x04 not a zip")
    (title, rows), = xlsx_io.read_rows(small(), max_rows=1)
    assert title == "VMs" and rows == [["VM", "vCPU", "RAM GB"]]                 # max_rows honoured


def test_sizing_and_schedule_imports_refuse_bombs(app_, admin):
    r = admin.post("/api/sizing/import", files={"file": ("vms.xlsx", bomb())})
    assert r.status_code == 200 and r.json()["rows"] == [] and "quá lớn" in r.json()["warnings"][0]
    ok = admin.post("/api/sizing/import", files={"file": ("vms.xlsx", small())}).json()
    assert ok["rows"] and ok["rows"][0]["vcpu"] == 4
    r = admin.post("/api/backup/schedules/import", files={"file": ("lich.xlsx", bomb())})
    assert r.status_code == 400 and "quá lớn" in r.json()["detail"]
