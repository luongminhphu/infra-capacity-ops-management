"""ICO v2 G7b: body tables → request lines, AI fallback (always review), review queue, label map, demo acceptance."""
import pytest

from app.database import SessionLocal
from app.itsm import demo_generator, process
from app.itsm.models import ItsmLabelMap, ItsmRequest, ItsmRequestLine
from app.itsm.parse import parse_html
from test_spend_opex import _login, admin, app_  # noqa: F401  (fixtures)

VERTICAL = """<p>Dự án Core</p><table><tr><td>Tên máy</td><td>core-app-01</td></tr><tr><td>vCPU</td><td>8</td></tr>
<tr><td>RAM (GB)</td><td>32</td></tr><tr><td>Ổ cứng</td><td>200 GB</td></tr><tr><td>Môi trường</td><td>PROD</td></tr></table>"""
HORIZONTAL = """<table><tr><th>Hostname</th><th>CPU</th><th>Memory (GB)</th><th>Disk</th><th>Số lượng</th></tr>
<tr><td>web-01</td><td>4</td><td>8</td><td>100</td><td>2</td></tr><tr><td>web-02</td><td>2</td><td>4</td><td>80</td><td>1</td></tr></table>"""
MERGED = """<table><tr><th>Tên VM</th><th>Môi trường</th><th>vCPU</th><th>RAM</th></tr>
<tr><td>db-01</td><td rowspan="2">UAT</td><td>8</td><td>64</td></tr><tr><td>db-02</td><td>4</td><td>32</td></tr></table>"""
ODD_LABEL = """<table><tr><th>Máy chủ ảo</th><th>Số nhân</th><th>RAM</th></tr><tr><td>x-01</td><td>4</td><td>16</td></tr></table>"""
TB_UNIT = """<table><tr><th>Tên VM</th><th>vCPU</th><th>RAM</th><th>Dung lượng</th></tr>
<tr><td>dw-01</td><td>16</td><td>0,5 TB</td><td>1,5 TB</td></tr></table>"""
EMPTY_CELL = """<table><tr><th>Tên VM</th><th>vCPU</th><th>RAM</th><th>Disk</th></tr>
<tr><td>a-01</td><td>4</td><td>8</td><td>100</td></tr><tr><td>a-02</td><td></td><td>—</td><td>100</td></tr></table>"""
SCRIPT = """<script>document.write('<table><tr><th>vCPU</th><th>RAM</th></tr><tr><td>99</td><td>99</td></tr></table>')</script>
<table onclick="x()"><tr><th>Tên VM</th><th>vCPU</th><th>RAM</th></tr><tr><td>s-01</td><td>2</td><td>4</td></tr></table>"""
FREE_TEXT = "<p>Nhờ team tạo 2 VM Ubuntu 4 vCPU 8GB RAM cho dự án CRM.</p>"
RECONFIG = """<p><b>Cấu hình hiện tại (As-is)</b></p><table><tr><th>Tên VM</th><th>vCPU</th><th>RAM</th><th>Disk</th></tr>
<tr><td>app-01</td><td>4</td><td>8</td><td>100</td></tr><tr><td>app-02</td><td>2</td><td>4</td><td>80</td></tr></table>
<p><b>Cấu hình đề xuất (To-be)</b></p><table><tr><th>Tên VM</th><th>vCPU</th><th>RAM</th><th>Disk</th></tr>
<tr><td>app-02</td><td>4</td><td>8</td><td>80</td></tr><tr><td>app-01</td><td>8</td><td>16</td><td>200</td></tr></table>"""
RECONFIG_ONE = """<table><tr><th>Thông số</th><th>As-is</th><th>To-be</th></tr><tr><td>Hostname</td><td>db-01</td><td>db-01</td></tr>
<tr><td>vCPU</td><td>8</td><td>16</td></tr><tr><td>RAM</td><td>32 GB</td><td>64 GB</td></tr><tr><td>Disk</td><td>500 GB</td><td>1 TB</td></tr></table>"""


def test_vertical_table():
    r = parse_html(VERTICAL)
    [ln] = r["lines"]
    assert (ln["vm_name"], ln["vcpu"], ln["ram_gb"], ln["disk_gb"], ln["env"], ln["qty"]) == ("core-app-01", 8, 32, 200, "PROD", 1)
    assert r["confidence"] == 1.0


def test_horizontal_table_with_quantity():
    r = parse_html(HORIZONTAL)
    assert [(x["vm_name"], x["vcpu"], x["ram_gb"], x["disk_gb"], x["qty"]) for x in r["lines"]] == [("web-01", 4, 8, 100, 2), ("web-02", 2, 4, 80, 1)]


def test_merged_cells_repeat_the_value():
    assert [(x["vm_name"], x["env"], x["vcpu"]) for x in parse_html(MERGED)["lines"]] == [("db-01", "UAT", 8), ("db-02", "UAT", 4)]


def test_unknown_label_is_reported_then_learned():
    r = parse_html(ODD_LABEL)
    assert r["confidence"] < process.CONFIDENCE_OK and set(r["unknownLabels"]) == {"Máy chủ ảo", "Số nhân"}
    r2 = parse_html(ODD_LABEL, {"so nhan": "vcpu", "may chu ao": "vm_name"})
    assert [(x["vm_name"], x["vcpu"], x["ram_gb"]) for x in r2["lines"]] == [("x-01", 4, 16)] and r2["confidence"] >= 0.7


def test_terabytes_become_gigabytes():
    [ln] = parse_html(TB_UNIT)["lines"]
    assert (ln["ram_gb"], ln["disk_gb"]) == (512, 1536)


def test_empty_cells_lower_the_confidence():
    r = parse_html(EMPTY_CELL)
    assert r["lines"][1]["vcpu"] is None and r["lines"][1]["ram_gb"] is None and r["confidence"] == 0.6


def test_script_is_ignored():
    r = parse_html(SCRIPT)
    assert [(x["vm_name"], x["vcpu"]) for x in r["lines"]] == [("s-01", 2)] and "99" not in r["text"].split()


def test_free_text_has_no_lines():
    r = parse_html(FREE_TEXT)
    assert r["lines"] == [] and r["confidence"] == 0 and "Ubuntu" in r["text"]


@pytest.fixture()
def db(app_):
    s = SessionLocal()
    for m in (ItsmRequestLine, ItsmRequest, ItsmLabelMap):
        s.query(m).delete()
    s.commit()
    yield s
    for m in (ItsmRequestLine, ItsmRequest, ItsmLabelMap):
        s.query(m).delete()
    s.commit()
    s.close()


def _req(db, n, body, template="Yêu cầu cấp máy ảo"):
    r = ItsmRequest(source_key="sdp-t", req_id=str(n), template=template, subject=f"Yêu cầu {n}", description=body)
    db.add(r)
    db.commit()
    return r


def test_process_statuses_ai_fallback_and_counting(db, monkeypatch):
    calls = []

    def fake_ai(db_, req, text):
        calls.append(req.req_id)
        return {"lines": [{"vm_name": None, "env": "UAT", "os": "Ubuntu", "project": "CRM", "vcpu": 4, "ram_gb": 8, "disk_gb": None,
                           "qty": 2, "raw": {"source": "ai"}}], "kind": "new_vm", "notes": ""}
    monkeypatch.setattr(process, "_ai", fake_ai)
    a, b, c = _req(db, 1, HORIZONTAL), _req(db, 2, FREE_TEXT), _req(db, 3, EMPTY_CELL)
    assert process.process(db) == {"parsed": 1, "ai": 2, "review": 0}
    db.expire_all()
    assert (a.parse_status, b.parse_status, c.parse_status) == ("parsed", "review", "review")      # AI is never trusted
    assert calls == ["2", "3"] and db.query(ItsmRequestLine).filter_by(request_id=b.id).one().qty == 2
    assert process.counted(db.query(ItsmRequest)).count() == 1
    admin_user = type("U", (), {"display_name": "Admin", "username": "admin"})()
    process.review(db, b, admin_user, "approve", [{"vm_name": "crm-uat-01", "vcpu": "4", "ram_gb": 8, "qty": 2}])
    process.review(db, c, admin_user, "skip")
    db.commit()
    assert process.counted(db.query(ItsmRequest)).count() == 2 and c.parse_status == "failed"
    assert db.query(ItsmRequestLine).filter_by(request_id=b.id).one().vm_name == "crm-uat-01"


def test_ai_off_goes_to_review(db, monkeypatch):
    r = _req(db, 9, FREE_TEXT)
    process.process(db, allow_ai=False)
    db.expire_all()
    assert r.parse_status == "review" and r.confidence == 0


def test_api_review_labels_and_permissions(db, admin):  # noqa: F811
    r = _req(db, 5, ODD_LABEL)
    process.process(db, allow_ai=False)
    d = admin.get(f"/api/itsm/requests/{r.id}").json()
    assert d["parseStatus"] == "review" and "Số nhân" in d["unknownLabels"] and "<script" not in d["bodyHtml"]
    assert admin.get("/api/itsm/requests?status=review").json()["summary"]["pending"] == 1
    out = admin.post("/api/itsm/labels", json={"label": "Số nhân", "field": "vcpu"}).json()
    admin.post("/api/itsm/labels", json={"label": "Máy chủ ảo", "field": "vm_name"})
    assert out["label"] == "so nhan"
    db.expire_all()
    assert db.get(ItsmRequest, r.id).parse_status == "parsed"                 # read again with the new labels
    assert admin.post(f"/api/itsm/requests/{r.id}/review", json={"action": "skip"}).json()["parseStatus"] == "failed"
    assert admin.post(f"/api/itsm/requests/{r.id}/review", json={"action": "delete"}).status_code == 400


def test_review_needs_admin(app_, db):  # noqa: F811
    r = _req(db, 6, FREE_TEXT)
    viewer = _login(app_, "viewer1")
    assert viewer.get("/api/itsm/requests").status_code == 200
    assert viewer.post(f"/api/itsm/requests/{r.id}/review", json={"action": "skip"}).status_code == 403
    assert viewer.post("/api/itsm/labels", json={"label": "x", "field": "vcpu"}).status_code == 403


def test_demo_acceptance_90_percent_of_table_requests_parse_automatically():
    rs = demo_generator.requests()
    tables = [r for r in rs if "<table" in r["description"]]
    auto = sum(1 for r in tables if parse_html(r["description"])["confidence"] >= process.CONFIDENCE_OK)
    assert auto / len(tables) >= 0.9


@pytest.mark.parametrize("template,kind", [("VM provisioning", "new_vm"), ("VM reconfiguration", "resize"),
                                            ("VM Decommision", "decommission"), ("VM Decommission", "decommission")])
def test_hsc_templates_are_recognised(template, kind):
    from app.itsm.sdp import kind_of
    assert kind_of(template, "Yêu cầu bất kỳ") == kind


def test_one_year_statistics_per_kind_and_month(db):
    import datetime as dt
    now = dt.datetime(2026, 11, 20, 3, 0)

    def req(n, template, created, body, status="parsed", reviewed=False):
        r = ItsmRequest(source_key="sdp-t", req_id=str(n), template=template, subject="x", description=body,
                        created_at=created)
        db.add(r)
        db.commit()
        return r
    req(1, "VM provisioning", dt.datetime(2026, 1, 10), HORIZONTAL)          # web-01 ×2 (4 vCPU, 8 GB, 100 GB) + web-02
    req(2, "VM reconfiguration", dt.datetime(2026, 11, 2), RECONFIG)         # change: +4/+2 vCPU, +8/+4 GB, +100/0 GB
    req(3, "VM Decommision", dt.datetime(2026, 11, 5), MERGED)               # db-01 (8 / 64), db-02 (4 / 32)
    req(4, "VM provisioning", dt.datetime(2025, 10, 1), HORIZONTAL)          # older than 12 months: not counted
    req(5, "VM provisioning", dt.datetime(2026, 11, 6), FREE_TEXT)           # review: reported apart
    process.process(db, allow_ai=False)
    s = process.stats(db, 12, now=now)
    t = s["total"]
    assert (t["new_vm"]["requests"], t["new_vm"]["vms"], t["new_vm"]["vcpu"], t["new_vm"]["ramGB"], t["new_vm"]["diskGB"]) == \
        (1, 3, 10.0, 20.0, 280.0)
    assert (t["resize"]["vms"], t["resize"]["vcpu"], t["resize"]["ramGB"], t["resize"]["diskGB"]) == (2, 6.0, 12.0, 100.0)
    assert (t["decommission"]["requests"], t["decommission"]["vms"], t["decommission"]["vcpu"], t["decommission"]["ramGB"]) == \
        (1, 2, 12.0, 96.0)
    assert s["since"] == "2025-12" and s["pendingReview"]["new_vm"] == 1
    assert [m["month"] for m in s["byMonth"]] == ["2026-01", "2026-11"]



def test_reconfiguration_as_is_and_to_be_tables_are_paired_by_vm_name():
    lines = parse_html(RECONFIG)["lines"]
    got = {x["vm_name"]: (x["vcpu_before"], x["vcpu"], x["ram_gb_before"], x["ram_gb"], x["disk_gb_before"], x["disk_gb"]) for x in lines}
    assert got == {"app-01": (4, 8, 8, 16, 100, 200), "app-02": (2, 4, 4, 8, 80, 80)}      # To-be rows in another order


def test_reconfiguration_in_one_table_as_is_to_be_columns():
    r = parse_html(RECONFIG_ONE)
    [ln] = r["lines"]
    assert (ln["vm_name"], ln["vcpu_before"], ln["vcpu"], ln["ram_gb_before"], ln["ram_gb"], ln["disk_gb_before"], ln["disk_gb"]) == \
        ("db-01", 8, 16, 32, 64, 500, 1024) and r["confidence"] == 1.0


def test_reconfiguration_without_as_is_goes_to_review(db):
    r = _req(db, 7, VERTICAL, template="VM reconfiguration")
    process.process(db, allow_ai=False)
    db.expire_all()
    assert r.kind == "resize" and r.parse_status == "review" and r.confidence == 0.5


RECONFIG_TITLE_ROW = """<table><tr><th colspan="4">AS-IS</th></tr><tr><th>Tên VM</th><th>vCPU</th><th>RAM</th><th>Disk</th></tr>
<tr><td>app-01</td><td>4</td><td>8</td><td>100</td></tr></table>
<table><tr><th colspan="4">TO-BE</th></tr><tr><th>Tên VM</th><th>vCPU</th><th>RAM</th><th>Disk</th></tr>
<tr><td>app-01</td><td>8</td><td>16</td><td>200</td></tr></table>"""
RECONFIG_COLUMNS = """<table><tr><th>Tên VM</th><th>vCPU (As-is)</th><th>vCPU (To-be)</th><th>RAM (As-is)</th><th>RAM (To-be)</th>
<th>Disk (As-is)</th><th>Disk (To-be)</th></tr><tr><td>app-01</td><td>4</td><td>8</td><td>8</td><td>16</td><td>100</td><td>200</td></tr></table>"""


@pytest.mark.parametrize("body", [RECONFIG_TITLE_ROW, RECONFIG_COLUMNS], ids=["title-row-in-table", "as-is-to-be-columns"])
def test_reconfiguration_other_horizontal_layouts(body):
    """'As-is' / 'To-be' as a merged title row inside each table, or as column suffixes of one table: one VM, not two."""
    r = parse_html(body)
    assert [(x["vm_name"], x["vcpu_before"], x["vcpu"], x["ram_gb_before"], x["ram_gb"], x["disk_gb_before"], x["disk_gb"])
            for x in r["lines"]] == [("app-01", 4, 8, 8, 16, 100, 200)] and r["unknownLabels"] == []
