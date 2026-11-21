"""Nhập Opex / Capex từ Excel: header detection, parsing, duplicates, renewal chains, commit."""
import datetime as dt
import io

from openpyxl import Workbook, load_workbook

from app.database import SessionLocal
from app.spend.models import CapexItem, CapexProject, OpexItem

from test_spend_opex import _login, admin, app_, editor  # noqa: F401  (fixtures)

Y = dt.date.today().year


def _xlsx(rows, title_rows=2):
    wb = Workbook()
    ws = wb.active
    for i in range(title_rows):                       # a company file: title lines above the header
        ws.append([f"BẢNG THEO DÕI CHI PHÍ IT {Y}" if i == 0 else None])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _post(c, path, data):
    return c.post(path, files={"file": ("chi-phi.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


OPEX_FILE = [
    ["STT", "Loại", "Dịch vụ", "NCC", "Từ ngày", "Đến ngày", "Giá trị trước VAT", "Thuế suất", "Chu kỳ", "Trạng thái", "Lý do ngừng", "Serial"],
    [1, "Bảo hành máy chủ", "Bảo hành Dell R750", "Dell Việt Nam", f"01/07/{Y - 2}", f"30/06/{Y - 1}", 450_000_000, 10, "Hằng năm", None, None, "SN1; SN2"],
    [2, "bao hanh may chu", "Bảo hành Dell R750", "DELL VIỆT NAM", f"01/07/{Y - 1}", f"30/06/{Y}", "480.000.000", "10%", None, None, None, None],
    [3, "Dịch vụ thuê ngoài", "Giám sát NOC", "FPT IS", dt.datetime(Y, 1, 1), dt.datetime(Y, 3, 31), 45_000_000, 0.08, "Hằng quý", None, None, None],
    [4, "Bảo hành máy chủ", "Bảo hành Dell R750", "Dell Việt Nam", f"01/07/{Y - 1}", f"30/06/{Y}", 480_000_000, 10, None, None, None, None],
    [5, "Loại không có", "Phần mềm X", "ABC", f"01/01/{Y}", f"31/12/{Y}", 10_000_000, 10, None, None, None, None],
    [6, "License firewall", "License Fortinet", "Fortinet", f"01/03/{Y - 1}", f"28/02/{Y}", 120_000_000, 10, None, "Ngừng", "Chuyển sang Palo Alto", None],
    [None, None, None, None, None, None, None, None, None, None, None, None],
]


def test_opex_preview_commit_chain_and_reimport(app_, admin):
    ed, viewer = _login(app_, "editor1"), _login(app_, "viewer1")
    data = _xlsx(OPEX_FILE)
    assert _post(viewer, "/api/spend/import/preview?kind=opex", data).status_code == 403
    pv = _post(ed, "/api/spend/import/preview?kind=opex", data).json()
    assert pv["headerRow"] == 3 and pv["columns"]["title"] == "Nội dung dịch vụ"
    s = pv["summary"]
    assert (s["total"], s["create"], s["skip"], s["error"], s["renewedLinks"]) == (6, 4, 1, 1, 1)
    by = {r["row"]: r for r in pv["rows"]}
    assert by[4]["status"] == "renewed" and by[4]["successorRow"] == 5
    assert by[5]["amountNet"] == 480_000_000 and by[5]["vatPct"] == 10 and by[5]["status"] == "active"
    assert by[6]["billingCycle"] == "quarterly" and by[6]["vatPct"] == 8 and by[6]["startDate"] == f"{Y}-01-01"
    assert by[7]["action"] == "skip" and "dòng 5" in by[7]["skipReason"]
    assert by[8]["action"] == "error" and "Loại chi phí" in by[8]["errors"][0]
    assert by[9]["status"] == "stopped"
    # 450M + 480M + 45M (8 %) + 120M, after VAT
    assert s["amountGross"] == 495_000_000 + 528_000_000 + 48_600_000 + 132_000_000

    assert _post(ed, "/api/spend/import/commit?kind=opex", data).status_code == 400          # errors left
    out = _post(ed, "/api/spend/import/commit?kind=opex&skipErrors=true", data).json()
    assert len(out["created"]) == 4
    db = SessionLocal()
    rows = {(o.title, o.start_date): o for o in db.query(OpexItem).all()}
    old, new = rows[("Bảo hành Dell R750", dt.date(Y - 2, 7, 1))], rows[("Bảo hành Dell R750", dt.date(Y - 1, 7, 1))]
    assert (old.status, new.renewed_from_id, new.origin, new.vendor_id) == ("renewed", old.id, "renewal", old.vendor_id)   # vendor names merged
    fw = rows[("License Fortinet", dt.date(Y - 1, 3, 1))]
    assert (fw.status, fw.stop_reason) == ("stopped", "Chuyển sang Palo Alto")
    assert old.fiscal_year == Y - 2
    db.close()
    # Same file again: everything is a duplicate, nothing created
    again = _post(ed, "/api/spend/import/commit?kind=opex&skipErrors=true", data).json()
    assert again["created"] == [] and again["summary"]["skip"] == 5


def test_capex_grouping_status_and_generated_opex(app_, admin):
    ed = _login(app_, "editor1")
    data = _xlsx([
        ["Tên dự án", "Năm tài chính", "Trạng thái", "Loại chi phí", "Hạng mục", "Nhà cung cấp", "SL", "Thành tiền", "VAT",
         "Ngày bắt đầu bảo hành", "Số năm bảo hành"],
        ["Mở rộng cụm PROD", Y, "Đã duyệt", "Bảo hành máy chủ", "4 máy chủ R760", "Dell Việt Nam", 4, "2.000.000.000", 10, f"15/06/{Y}", 3],
        [None, None, None, "Khác", "Triển khai", "FPT", 1, 150_000_000, 8, None, 0],
        ["Thay core switch", Y + 1, None, "Bảo hành thiết bị mạng", "2 x Nexus 9300", "Cisco", 2, 900_000_000, 10, None, 0],
        ["Lỗi", Y, None, "Khác", None, None, 1, None, 10, None, 0],
    ], title_rows=0)
    pv = _post(ed, "/api/spend/import/preview?kind=capex", data).json()
    ps = {p["name"]: p for p in pv["projects"]}
    assert (pv["summary"]["create"], pv["summary"]["error"], pv["summary"]["items"]) == (2, 1, 3), [(p["name"], p["fiscalYear"], p["errors"], [(r["row"], r["errors"], r.get("project")) for r in p["rows"]]) for p in pv["projects"]]
    assert ps["Mở rộng cụm PROD"]["status"] == "approved" and len(ps["Mở rộng cụm PROD"]["rows"]) == 2
    assert ps["Mở rộng cụm PROD"]["amountNet"] == 2_150_000_000
    assert any("sinh Opex gia hạn" in w for w in ps["Mở rộng cụm PROD"]["warnings"])
    assert ps["Thay core switch"]["status"] == "draft"
    out = _post(ed, "/api/spend/import/commit?kind=capex&skipErrors=true", data).json()
    assert len(out["created"]) == 2
    db = SessionLocal()
    p = db.query(CapexProject).filter_by(name="Mở rộng cụm PROD").one()
    assert (p.status, p.fiscal_year, db.query(CapexItem).filter_by(project_id=p.id).count()) == ("approved", Y, 2)
    gen = db.query(OpexItem).filter_by(capex_project_id=p.id).one()                       # 3-year warranty -> renewal
    assert (gen.status, gen.start_date, gen.fiscal_year) == ("planned", dt.date(Y + 3, 6, 15), Y + 3)
    db.close()
    assert _post(ed, "/api/spend/import/commit?kind=capex&skipErrors=true", data).json()["summary"]["skip"] == 2


def test_template_roundtrip_and_bad_files(app_, admin):
    ed = _login(app_, "editor1")
    for kind, sheet in (("opex", "Opex"), ("capex", "Capex")):
        r = ed.get(f"/api/spend/import/template.xlsx?kind={kind}")
        assert r.status_code == 200
        wb = load_workbook(io.BytesIO(r.content))
        assert wb.sheetnames == [sheet, "Danh mục", "Hướng dẫn"]
        pv = _post(ed, f"/api/spend/import/preview?kind={kind}", r.content).json()      # the template reads back
        assert pv["summary"]["error"] == 0 and pv["summary"]["create"] >= 1
    assert _post(ed, "/api/spend/import/preview?kind=opex", b"%PDF-1.4 not excel").status_code == 415
    no_header = _xlsx([["A", "B"], [1, 2]], title_rows=0)
    r = _post(ed, "/api/spend/import/preview?kind=opex", no_header)
    assert r.status_code == 422 and "tiêu đề" in r.json()["detail"]
    assert ed.get("/api/spend/import/template.xlsx?kind=other").status_code == 422


def test_preview_flags_what_the_commit_would_refuse(app_, admin):  # noqa: F811
    """Audit: Capex gross < net, quantity < 1 and fiscal year 26 passed the preview, then the commit rejected the file."""
    ed = _login(app_, "editor1")
    data = _xlsx([
        ["Tên dự án", "Năm tài chính", "Loại chi phí", "Hạng mục", "SL", "Thành tiền", "VAT", "Thành tiền sau VAT"],
        ["Gross nhỏ", Y, "Khác", "A", 1, 100, 10, 90],
        ["Số lượng âm", Y, "Khác", "B", -2, 100, 10, None],
        ["Năm sai", 26, "Khác", "C", 1, 100, 10, None],
    ], title_rows=0)
    pv = _post(ed, "/api/spend/import/preview?kind=capex", data).json()
    errs = {p["name"]: " ".join(e for r in p["rows"] for e in r["errors"]) + " ".join(p.get("errors") or []) for p in pv["projects"]}
    assert "nhỏ hơn trước VAT" in errs["Gross nhỏ"] and "Số lượng" in errs["Số lượng âm"] and "2000" in errs["Năm sai"]
    assert pv["summary"]["create"] == 0
