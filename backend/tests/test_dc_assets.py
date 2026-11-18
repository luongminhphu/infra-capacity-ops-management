"""Tài sản → Thiết bị Data Center: Excel import, age against the 5-year policy, serial ↔ Opex coverage,
replacement suggestions in Kế hoạch năm sau (Capex)."""
import datetime as dt
import io

import pytest
from openpyxl import Workbook

from app import dc_assets
from app.database import SessionLocal
from app.models import DcAsset
from app.spend import fiscal

from test_spend_opex import _cat_id, _login, admin, app_, editor  # noqa: F401  (fixtures)

D = dt.date
HEAD = ["No.", "BUSINESS FUNCTION", "QTY", "Type", "Env", "ID", "BRAND", "MODEL", "SN", "ASSET TAG", "ACQUISITION DATE",
        "UPTIME (MONTH)", "UPTIME (YEAR)", "LOCATION", None, "Years", "PROD/BETA", "UAT", "DEV", "Removed"]


def _sheet(ws, title, rows):
    ws.title = title
    ws.append(["DATA CENTER STATUS", None, None, None, None, None, None, None, None, None, None, dt.datetime(2026, 11, 17),
               None, None, None, "Green Policy"])
    ws.append(HEAD)
    for i, r in enumerate(rows):
        summary = [["<= 3 years", 16, 1, 0, 0], [">3 years & < 5 years", 10, 2, 0, 0]][i] if i < 2 else []
        ws.append(list(r) + ([None] if summary else []) + summary)


def _book(acq_old=dt.datetime(2019, 11, 22)):
    wb = Workbook()
    _sheet(wb.active, "DC-Server", [
        (1, "ONE CLUSTER", 1, "TRADING", "PROD", "svr124", "DELL", "PowerEdge R660", "4806WY3", "HSC-SVR-124-2023", dt.datetime(2023, 10, 22), 37, "3Y1M", "DC"),
        (2, None, 1, None, None, "svr126", "DELL", "PowerEdge R660", "71FHKZ3 ", "HSC-SVR-126-2023", dt.datetime(2023, 12, 22), 35, "2Y11M", "DC"),
        (3, "IBS CLUSTER", 1, "TRADING", "UAT", "svr85", "HPE", "DL360 Gen10", "SGH943XT7P", "HSC-SVR-85-2019", acq_old, 84, "7Y0M", "DC"),
        (4, None, 1, None, None, "svr119", "DELL", "PowerEdge R650", "DSMZ6V3", "HSC-SVR-119-2023", dt.datetime(2023, 3, 22), 44, "3Y8M", "DC"),
        (5, None, 1, None, None, "svr99", "HPE", "DL360 Gen10 Plus", "SGH143T3WR", "HSC-SVR-119-2023", dt.datetime(2021, 7, 22), 64, "5Y4M", "DC"),
        (None, None, None, None, None, None, None, None, None, None, None, None, None, None),
        (None, "REMOVED", 1, "TRADING", "PROD", "svr56", "HPE", "DL380 Gen9", "CZ2550XYZ", "HSC-SVR-56-2016", dt.datetime(2016, 1, 1), None, None, "DC"),
        (None, None, 1, None, None, "svr60", "HPE", "DL380 Gen9", "CZ2550ABC", "HSC-SVR-60-2016", "15/03/2016", None, None, "DC"),
    ])
    _sheet(wb.create_sheet(), "DC-Storage", [
        (1, "DC ENTERPRISE STORAGE CLUSTER", 1, "ENT", "PROD", "SAN3-A300-01", "NETAPP", "AFF A300", 952248001227, "HSC-STO-A301-2018", dt.datetime(2018, 10, 1), 98, "8Y2M", "DC"),
        (2, None, 1, "ENT", "PROD", "SAN3-A300-02", "NETAPP", "AFF A300", 952324000108, "HSC-STO-A302-2018", dt.datetime(2018, 10, 1), 98, "8Y2M", "DC"),
        (3, None, 1, "ENT", "PROD", "BAD-DATE", "NETAPP", "DS224", "SHF1", "HSC-STO-X", "không rõ", None, None, "DC"),
        (4, None, 1, "ENT", "PROD", "NO-SN", "NETAPP", "DS224", None, None, None, None, None, "DC"),
    ])
    _sheet(wb.create_sheet(), "DC-Network", [
        (1, "Load Balance", 1, "ENT", "PROD", "LB_OUT_01", "Peplink", "Balance 1350", "182C-AA06-F506", "HSC-NET-LLB01-2022", dt.datetime(2022, 12, 19), 47, "3Y11M", "AB TOWER"),
        (2, "Load Balance", 1, "TRADING", "PROD", "LB_IN_01", "Peplink", "Balance 1350", "182C-AA06-F506", "HSC-NET-LLB01-2021", dt.datetime(2021, 6, 1), 66, "5Y6M", "DC"),
    ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- pure
def test_parse_layout_carry_down_removed_and_identities():
    p = dc_assets.parse(_book())
    assert [(s["name"], s["category"], s["rows"]) for s in p["sheets"]] == [
        ("DC-Server", "server", 7), ("DC-Storage", "storage", 4), ("DC-Network", "network", 2)]
    rows = {r["deviceId"]: r for r in p["rows"]}
    # merged business function / type / env carry down; summary block on the right is not read
    assert (rows["svr126"]["business"], rows["svr126"]["bizType"], rows["svr126"]["env"]) == ("ONE CLUSTER", "TRADING", "PROD")
    assert rows["svr119"]["business"] == "IBS CLUSTER" and rows["svr119"]["env"] == "UAT"
    # serial trimmed for display, key without spaces / case; numeric NetApp serial kept as digits
    assert rows["svr126"]["serial"] == "71FHKZ3" and rows["svr126"]["serialKey"] == "71FHKZ3"
    assert rows["SAN3-A300-01"]["serial"] == "952248001227"
    assert rows["svr124"]["acquiredOn"] == D(2023, 10, 22) and rows["svr60"]["acquiredOn"] == D(2016, 3, 15)
    # "REMOVED" group: that row and the rows under it
    assert rows["svr56"]["removed"] and rows["svr60"]["removed"] and not rows["svr85"]["removed"]
    assert rows["svr124"]["key"] == "tag:HSC-SVR-124-2023"
    # the same asset tag typed on two devices: both kept, identified by SN + ID, reported
    assert rows["svr119"]["key"].startswith("sn:server:DSMZ6V3:") and rows["svr99"]["key"].startswith("sn:server:SGH143T3WR:")
    assert p["duplicateTags"][0]["assetTag"] == "HSC-SVR-119-2023"
    # unreadable date / no SN nor tag: refused with the reason
    errs = {e["deviceId"]: e["errors"] for e in p["errors"]}
    assert errs == {"BAD-DATE": ["Ngày “không rõ” không đọc được (dd/mm/yyyy)"], "NO-SN": ["Thiếu SN và asset tag"]}
    # one serial on two devices with different tags: warning only
    assert p["duplicateSerials"][0]["serial"] == "182C-AA06-F506" and len(p["duplicateSerials"][0]["rows"]) == 2
    assert len(p["rows"]) == 11


def test_parse_rejects_a_workbook_without_asset_table():
    wb = Workbook()
    wb.active.append(["Tên", "Giá"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(dc_assets.ImportError_):
        dc_assets.parse(buf.getvalue())


def test_age_arithmetic():
    assert dc_assets.months_between(D(2021, 7, 22), D(2026, 11, 17)) == 63          # day 17 < 22: month not complete
    assert dc_assets.months_between(D(2021, 7, 17), D(2026, 11, 17)) == 64
    assert dc_assets.age_text(63) == "5 năm 3 tháng" and dc_assets.age_text(60) == "5 năm" and dc_assets.age_text(7) == "7 tháng"
    assert [dc_assets.age_bucket(m, 60) for m in (36, 37, 59, 60, None)] == ["le3", "3to5", "3to5", "ge5", "unknown"]
    assert dc_assets.serial_key(" 182c-aa06 f506 ") == "182CAA06F506"


def _asset(acq, removed=False):
    return DcAsset(category="server", device_id="x", serial="S1", serial_key="S1", acquired_on=acq, removed=removed, qty=1)


def _cov(grp, start, end, status="active"):
    return {"id": 1, "code": "OPX-1", "title": "t", "grp": grp, "category": "c", "vendor": "v", "start": start, "end": end,
            "status": status, "contractRef": None, "fiscalYear": start.year}


def test_levels_age_and_coverage():
    today = D(2026, 11, 17)
    ev = lambda acq, links=(): dc_assets.evaluate(_asset(acq), list(links), today, 60, 48)  # noqa: E731
    # ≥ 5 years, never covered → VƯỢT NGƯỠNG
    e = ev(D(2019, 11, 22))
    assert e["level"] == "crit" and e["reasons"] == ["Đã dùng 6 năm 11 tháng (≥ 5 năm)", "Chưa ghép hợp đồng bảo hành phần cứng"]
    # ≥ 5 years with a HW warranty covering today → CẢNH BÁO (plan the replacement)
    e = ev(D(2019, 11, 22), [_cov("hw_warranty", D(2026, 1, 1), D(2026, 12, 31))])
    assert e["level"] == "bad" and e["hw"]["state"] == "active"
    # warranty expired / stopped → VƯỢT NGƯỠNG
    assert ev(D(2019, 11, 22), [_cov("hw_warranty", D(2025, 1, 1), D(2025, 12, 31))])["reasons"][1] == "Không còn bảo hành phần cứng"
    assert ev(D(2019, 11, 22), [_cov("hw_warranty", D(2026, 1, 1), D(2026, 12, 31), "stopped")])["hw"]["state"] == "expired"
    # 4 years 3 months → THEO DÕI with the date it reaches 5 years
    e = ev(D(2022, 7, 22))
    assert e["level"] == "warn" and e["reasons"] == ["Đủ 5 năm vào 07/2027"]
    # young device, HW warranty and license expired → THEO DÕI with both reasons
    e = ev(D(2024, 1, 1), [_cov("hw_warranty", D(2024, 1, 1), D(2025, 12, 31)), _cov("license", D(2025, 1, 1), D(2026, 6, 30))])
    assert e["level"] == "warn" and e["reasons"] == ["Hết bảo hành phần cứng từ 31/12/2025", "Hết hạn license / firmware từ 30/06/2026"]
    assert ev(D(2024, 1, 1))["level"] == "ok"
    # removed devices never alert
    assert dc_assets.evaluate(_asset(D(2010, 1, 1), removed=True), [], today, 60, 48)["level"] == "ok"


# ---------------------------------------------------------------- API
@pytest.fixture()
def clean():
    def wipe():
        db = SessionLocal()
        db.query(DcAsset).delete()
        db.commit()
        db.close()
    wipe()
    yield
    wipe()


def _upload(c, path, data, **params):
    return c.post(path, params=params, files={"file": ("Green_IT.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def test_import_preview_commit_reimport_and_permissions(app_, admin, clean):
    data = _book()
    viewer = _login(app_, "viewer1")
    assert _upload(viewer, "/api/assets/dc/import/preview", data).status_code == 403
    pv = _upload(admin, "/api/assets/dc/import/preview", data).json()
    assert pv["counts"] == {"rows": 11, "new": 11, "update": 0, "same": 0, "errors": 2, "missing": 0}
    assert SessionLocal().query(DcAsset).count() == 0                     # preview writes nothing
    assert _upload(admin, "/api/assets/dc/import/commit", data).json() == {"new": 11, "update": 0, "same": 0, "removed": 0, "errors": 2}
    # same file again: nothing changes; one device date corrected: one update
    assert _upload(admin, "/api/assets/dc/import/commit", data).json()["same"] == 11
    r = _upload(admin, "/api/assets/dc/import/commit", _book(acq_old=dt.datetime(2019, 12, 1))).json()
    assert (r["new"], r["update"], r["same"]) == (0, 1, 10)
    # a file without one device + "mark missing": that device becomes removed
    wb_small = _book()
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(wb_small))
    wb["DC-Network"].delete_rows(4)                                       # LB_IN_01
    buf = io.BytesIO()
    wb.save(buf)
    r = _upload(admin, "/api/assets/dc/import/commit", buf.getvalue(), markMissing="true").json()
    assert r["removed"] == 1
    db = SessionLocal()
    assert db.query(DcAsset).filter_by(device_id="LB_IN_01").one().removed is True
    db.close()
    assert _upload(admin, "/api/assets/dc/import/preview", b"not an excel file").status_code in (400, 415, 422)


def test_listing_correlates_serials_with_opex(app_, admin, editor, clean):
    _upload(admin, "/api/assets/dc/import/commit", _book())
    today = fiscal.today_vn()
    # HW warranty covering today for svr85 (SN typed lower case with a space), license expired for svr124
    assert editor.post("/api/spend/opex", json={
        "categoryId": _cat_id("HW_SERVER"), "title": "Bảo hành HPE DL360", "vendorName": "HPE", "billingCycle": "yearly",
        "startDate": (today - dt.timedelta(days=30)).isoformat(), "endDate": (today + dt.timedelta(days=300)).isoformat(),
        "amountNet": 50_000_000, "vatPct": 10, "ownerName": "A", "contractRef": "HĐ-01/2026",
        "assets": [{"serial": "sgh943 xt7p"}]}).status_code == 200
    assert editor.post("/api/spend/opex", json={
        "categoryId": _cat_id("LIC_VMWARE"), "title": "Firmware / license Dell", "vendorName": "Dell", "billingCycle": "yearly",
        "startDate": (today - dt.timedelta(days=400)).isoformat(), "endDate": (today - dt.timedelta(days=35)).isoformat(),
        "amountNet": 10_000_000, "vatPct": 10, "ownerName": "A", "assets": [{"serial": "4806WY3"}]}).status_code == 200
    d = admin.get("/api/assets/dc").json()
    items = {a["deviceId"]: a for a in d["items"]}
    hw = items["svr85"]["hw"]
    assert hw["state"] == "active" and hw["code"].startswith("OPX-") and hw["contractRef"] == "HĐ-01/2026"
    assert items["svr85"]["opex"][0]["vendor"] == "HPE"
    assert items["svr124"]["license"]["state"] == "expired"
    assert any(r.startswith("Hết hạn license / firmware từ") for r in items["svr124"]["reasons"])
    assert items["svr56"]["removed"] and items["svr56"]["levelText"] == "Đã thanh lý"
    # NetApp A300 (2018) has no warranty Opex → VƯỢT NGƯỠNG
    assert items["SAN3-A300-01"]["level"] == "crit"
    s = d["summary"]
    assert s["removed"] == 2 and s["total"] == 9 and s["linked"] == 2
    # without a spend role the coverage state is shown but not the Opex details
    nobody = _login(app_, "nobody1")
    it = {a["deviceId"]: a for a in nobody.get("/api/assets/dc").json()["items"]}["svr85"]
    assert it["hw"]["state"] == "active" and "code" not in it["hw"] and it["opex"] == [] and it["opexCount"] == 1


def test_inbox_groups_devices_by_level(app_, admin, clean):
    _upload(admin, "/api/assets/dc/import/commit", _book())
    from app import inbox
    db = SessionLocal()
    try:
        items = [i for i in inbox.asset_items(db, fiscal.today_vn())]
    finally:
        db.close()
    crit = next(i for i in items if i["level"] == "crit")
    assert crit["page"] == "dc-assets" and crit["query"] == "level=crit" and "không còn bảo hành phần cứng" in crit["title"]
    assert "SAN3-A300-01" in crit["detail"] and "svr56" not in crit["detail"]   # removed devices excluded


def test_replacement_lines_in_next_year_plan(app_, admin, editor, clean):
    _upload(admin, "/api/assets/dc/import/commit", _book())
    y = fiscal.fiscal_year(fiscal.today_vn(), 1) + 1
    plan = editor.post("/api/spend/plans", json={"fiscalYear": y}).json()
    rep = [l for l in plan["lines"] if l["source"] == "replace"]
    titles = {l["title"]: l for l in rep}
    # One line per business function: NetApp A300 ×2 (2018); IBS CLUSTER = svr85 (2019) + svr99 (2021), not svr119 (2023);
    # Load Balance = both Peplink. Removed devices are not suggested.
    assert "Thay thế 2 × NETAPP AFF A300 — DC ENTERPRISE STORAGE CLUSTER" in titles
    ibs = titles["Thay thế 2 thiết bị máy chủ — IBS CLUSTER"]
    assert ibs["detail"].startswith("1 × HPE DL360 Gen10, 1 × HPE DL360 Gen10 Plus. Dùng từ 5 năm: svr85 (SN SGH943XT7P")
    assert "svr119" not in ibs["detail"]
    assert "Thay thế 2 × Peplink Balance 1350 — Load Balance" in titles
    assert not any("REMOVED" in t for t in titles)
    assert ("Thay thế 2 × DELL PowerEdge R660 — ONE CLUSTER" in titles) == (D(2028, 12, 22) <= D(y, 12, 31))
    a300 = titles["Thay thế 2 × NETAPP AFF A300 — DC ENTERPRISE STORAGE CLUSTER"]
    assert a300["kind"] == "capex" and a300["priority"] == "must" and a300["amountNet"] == 0 and a300["sourceCode"] == "2 thiết bị"
    assert a300["categoryName"] == "Bảo hành storage"                        # HW_STORAGE
    assert "SAN3-A300-01 (SN 952248001227" in a300["detail"] and a300["startDate"] == f"{y}-01-01"
    # price typed by the planner survives a refresh; approval then creates a draft Capex with the device list
    pid = plan["id"]
    assert editor.put(f"/api/spend/plans/{pid}/lines/{a300['id']}", json={"amountNet": 2_000_000_000}).status_code == 200
    plan = editor.post(f"/api/spend/plans/{pid}/refresh").json()
    a300 = next(l for l in plan["lines"] if l["id"] == a300["id"])
    assert a300["amountNet"] == 2_000_000_000 and a300["amountGross"] == 2_200_000_000
    approver = _login(app_, "approver1")
    assert editor.post(f"/api/spend/plans/{pid}/submit").status_code == 200
    assert approver.post(f"/api/spend/plans/{pid}/approve").status_code == 200
    out = approver.post(f"/api/spend/plans/{pid}/apply").json()["applied"]
    assert len(out["created"]) == len([l for l in rep if l["included"]])
    from app.spend.models import CapexProject
    db = SessionLocal()
    p = db.query(CapexProject).filter_by(name="Thay thế 2 × NETAPP AFF A300 — DC ENTERPRISE STORAGE CLUSTER").one()
    assert p.status == "draft" and p.fiscal_year == y and "SAN3-A300-02" in p.description
    db.close()


def test_manual_add_edit_and_import_keeps_manual_devices(app_, admin, clean):
    viewer = _login(app_, "viewer1")
    body = {"category": "storage", "deviceId": "DR-SAN-SW1", "brand": "BROCADE", "model": "G620", "serial": " frc2210x01 ",
            "assetTag": "", "acquiredOn": "2020-03-01", "location": "DR", "qty": 1, "business": "DR SAN"}
    assert viewer.post("/api/assets/dc", json=body).status_code == 403
    assert admin.post("/api/assets/dc", json={**body, "serial": "", "assetTag": ""}).json()["detail"] == "Nhập SN hoặc asset tag."
    assert admin.post("/api/assets/dc", json={**body, "deviceId": ""}).json()["detail"] == "Thiếu tên thiết bị (ID)."
    assert admin.post("/api/assets/dc", json={**body, "acquiredOn": "31/02/2020"}).status_code == 400
    assert admin.post("/api/assets/dc", json={**body, "category": "printer"}).status_code == 400
    aid = admin.post("/api/assets/dc", json=body).json()["id"]
    assert admin.post("/api/assets/dc", json=body).json()["detail"] == "Đã có thiết bị cùng SN và tên."
    it = next(a for a in admin.get("/api/assets/dc").json()["items"] if a["id"] == aid)
    assert (it["serial"], it["manual"], it["acquiredOn"], it["category"]) == ("frc2210x01", True, "2020-03-01", "storage")
    assert it["level"] == "crit"                                                  # ≥ 5 years, no warranty
    assert admin.put(f"/api/assets/dc/{aid}", json={**body, "assetTag": "HSC-STO-DR01", "removed": True}).status_code == 200
    it = next(a for a in admin.get("/api/assets/dc").json()["items"] if a["id"] == aid)
    assert it["assetTag"] == "HSC-STO-DR01" and it["removed"] and it["level"] == "ok"
    assert admin.put("/api/assets/dc/999999", json=body).status_code == 404
    # an import of the storage sheet with "mark missing" does not touch a device typed by hand
    admin.put(f"/api/assets/dc/{aid}", json={**body, "removed": False})
    r = _upload(admin, "/api/assets/dc/import/commit", _book(), markMissing="true").json()
    assert r["removed"] == 0
    db = SessionLocal()
    assert db.query(DcAsset).get(aid).removed is False
    db.close()


def test_keep_in_use_moves_devices_from_capex_replacement_to_opex_renewal(app_, admin, editor, clean):
    """Over budget / still good: the devices are kept, their warranty is renewed in Opex instead of a Capex."""
    _upload(admin, "/api/assets/dc/import/commit", _book())
    y = fiscal.fiscal_year(fiscal.today_vn(), 1) + 1
    today = fiscal.today_vn()
    # svr85 (IBS CLUSTER) has a HW warranty Opex already: its renewal is the normal Gia hạn line
    editor.post("/api/spend/opex", json={
        "categoryId": _cat_id("HW_SERVER"), "title": "Bảo hành HPE DL360 Gen10", "vendorName": "HPE", "billingCycle": "yearly",
        "startDate": (today - dt.timedelta(days=30)).isoformat(), "endDate": f"{y}-03-31", "amountNet": 50_000_000,
        "vatPct": 10, "ownerName": "A", "assets": [{"serial": "SGH943XT7P"}]})
    plan = editor.post("/api/spend/plans", json={"fiscalYear": y}).json()
    pid = plan["id"]
    lines = {l["title"]: l for l in plan["lines"]}
    a300 = lines["Thay thế 2 × NETAPP AFF A300 — DC ENTERPRISE STORAGE CLUSTER"]
    ibs = lines["Thay thế 2 thiết bị máy chủ — IBS CLUSTER"]
    nobody = _login(app_, "nobody1")
    dec = {"groupOf": a300["sourceId"], "fiscalYear": y, "decision": "extend", "reason": "budget", "note": "Dời sang FY sau"}
    assert nobody.post("/api/assets/dc/decision", json=dec).status_code == 403
    assert editor.post("/api/assets/dc/decision", json={**dec, "reason": ""}).json()["detail"].startswith("Chọn lý do")
    assert editor.post("/api/assets/dc/decision", json=dec).json() == {"updated": 2}
    assert editor.post("/api/assets/dc/decision", json={**dec, "groupOf": ibs["sourceId"], "reason": "good"}).json() == {"updated": 2}
    plan = editor.post(f"/api/spend/plans/{pid}/refresh").json()
    by_title = {l["title"]: l for l in plan["lines"]}
    # A300 ×2: no Capex any more; no warranty Opex to renew → one "Dùng tiếp" Opex line listing both serials
    assert "Thay thế 2 × NETAPP AFF A300 — DC ENTERPRISE STORAGE CLUSTER" not in by_title
    ext = by_title["Gia hạn bảo hành 2 thiết bị lưu trữ dùng tiếp — DC ENTERPRISE STORAGE CLUSTER"]
    assert (ext["source"], ext["kind"], ext["priority"]) == ("extend", "opex", "must")
    assert "Không thay (vượt ngân sách)" in ext["detail"] and "952324000108" in ext["detail"]
    # IBS: svr85 has a warranty Opex (renewed by its Gia hạn line); svr99 has none → extend line with svr99 only
    assert not any(t.startswith("Thay thế") and "IBS CLUSTER" in t for t in by_title)
    ibs_ext = by_title["Gia hạn bảo hành 1 thiết bị máy chủ dùng tiếp — IBS CLUSTER"]
    assert "svr99" in ibs_ext["detail"] and "svr85" not in ibs_ext["detail"]
    assert any(l["source"] == "renewal" and l["title"] == "Bảo hành HPE DL360 Gen10" for l in plan["lines"])
    # listing: kept in use + active warranty → THEO DÕI; kept without warranty → still VƯỢT NGƯỠNG
    items = {a["deviceId"]: a for a in admin.get("/api/assets/dc").json()["items"]}
    assert items["svr85"]["level"] == "warn" and "Dùng tiếp — thiết bị còn tốt" in items["svr85"]["reasons"]
    assert items["svr85"]["decision"]["label"] == "Dùng tiếp, gia hạn Opex"
    assert items["SAN3-A300-01"]["level"] == "crit"
    # kept only until mid-year: back to a Capex line starting the day after the review date
    editor.post("/api/assets/dc/decision", json={**dec, "until": f"{y}-06-30"})
    plan = editor.post(f"/api/spend/plans/{pid}/refresh").json()
    rep = next(l for l in plan["lines"] if l["title"] == "Thay thế 2 × NETAPP AFF A300 — DC ENTERPRISE STORAGE CLUSTER")
    assert rep["startDate"] == f"{y}-07-01" and "đến hạn 07/" in rep["detail"]
    editor.post("/api/assets/dc/decision", json=dec)                               # kept all year again
    plan = editor.post(f"/api/spend/plans/{pid}/refresh").json()
    ext = next(l for l in plan["lines"] if l["source"] == "extend" and "STORAGE" in l["title"])
    editor.put(f"/api/spend/plans/{pid}/lines/{ext['id']}", json={"amountNet": 300_000_000, "vendorId": None})
    # approve + apply: the extend line becomes a planned Opex holding the serials → the devices show the coverage
    from app.spend.models import SpendVendor
    db = SessionLocal()
    vid = db.query(SpendVendor.id).first()[0]
    db.close()
    editor.put(f"/api/spend/plans/{pid}/lines/{ext['id']}", json={"vendorId": vid})
    approver = _login(app_, "approver1")
    assert editor.post(f"/api/spend/plans/{pid}/submit").status_code == 200
    assert approver.post(f"/api/spend/plans/{pid}/approve").status_code == 200
    out = approver.post(f"/api/spend/plans/{pid}/apply").json()["applied"]
    assert any(c.startswith("OPX-") for c in out["created"])
    items = {a["deviceId"]: a for a in admin.get("/api/assets/dc").json()["items"]}
    assert items["SAN3-A300-01"]["hw"]["state"] == "planned" and items["SAN3-A300-02"]["opexCount"] == 1


def test_no_warranty_decision_silences_the_device_and_stays_out_of_the_plan(app_, admin, editor, clean):
    """Kept on purpose without buying a warranty: no alert, no Capex / Opex line, until the review date."""
    _upload(admin, "/api/assets/dc/import/commit", _book())
    items = {a["deviceId"]: a for a in admin.get("/api/assets/dc").json()["items"]}
    a1, a2 = items["SAN3-A300-01"]["id"], items["SAN3-A300-02"]["id"]
    assert items["SAN3-A300-01"]["level"] == "crit"
    y = fiscal.fiscal_year(fiscal.today_vn(), 1) + 1
    body = {"ids": [a1, a2], "decision": "nocover", "reason": "good", "note": "Chỉ chạy dữ liệu lưu trữ lạnh"}
    assert editor.post("/api/assets/dc/decision", json={**body, "reason": None}).status_code == 400
    assert editor.post("/api/assets/dc/decision", json=body).json() == {"updated": 2}
    d = admin.get("/api/assets/dc").json()
    it = {a["deviceId"]: a for a in d["items"]}["SAN3-A300-01"]
    assert it["level"] == "ok" and it["reasons"] == ["Không mua bảo hành — thiết bị còn tốt"]
    assert it["decision"]["label"] == "Dùng tiếp, không mua bảo hành" and d["summary"]["noCover"] == 2
    from app import inbox
    db = SessionLocal()
    try:
        assert not any("SAN3-A300" in i["detail"] for i in inbox.asset_items(db, fiscal.today_vn()))
    finally:
        db.close()
    plan = editor.post("/api/spend/plans", json={"fiscalYear": y}).json()
    assert not any("DC ENTERPRISE STORAGE CLUSTER" in l["title"] for l in plan["lines"])
    # review date inside the plan year: back in the replacement plan after it, and alerts come back once it passes
    editor.post("/api/assets/dc/decision", json={**body, "until": f"{y}-03-31"})
    plan = editor.post(f"/api/spend/plans/{plan['id']}/refresh").json()
    rep = next(l for l in plan["lines"] if l["title"] == "Thay thế 2 × NETAPP AFF A300 — DC ENTERPRISE STORAGE CLUSTER")
    assert rep["startDate"] == f"{y}-04-01"
    editor.post("/api/assets/dc/decision", json={**body, "until": (fiscal.today_vn() - dt.timedelta(days=1)).isoformat()})
    assert {a["deviceId"]: a for a in admin.get("/api/assets/dc").json()["items"]}["SAN3-A300-01"]["level"] == "crit"


def test_bulk_decision_and_disposal(app_, admin, editor, clean):
    """Several devices at once; "Thanh lý" removes them from alerts and the plan, survives a re-import of an
    older list, and another decision puts them back into use."""
    _upload(admin, "/api/assets/dc/import/commit", _book())
    items = {a["deviceId"]: a for a in admin.get("/api/assets/dc").json()["items"]}
    ids = [items[n]["id"] for n in ("SAN3-A300-01", "SAN3-A300-02", "svr85")]
    # bulk: keep in use, one call for three devices
    assert editor.post("/api/assets/dc/decision", json={"ids": ids, "decision": "nocover", "reason": "budget"}).json() == {"updated": 3}
    items = {a["deviceId"]: a for a in admin.get("/api/assets/dc").json()["items"]}
    assert {items[n]["decision"]["decision"] for n in ("SAN3-A300-01", "SAN3-A300-02", "svr85")} == {"nocover"}
    # bulk disposal on a given date (no reason needed)
    r = editor.post("/api/assets/dc/decision", json={"ids": ids, "decision": "dispose", "until": "2026-10-31", "note": "Bán thanh lý đợt 10/2026"})
    assert r.json() == {"updated": 3}
    d = admin.get("/api/assets/dc").json()
    items = {a["deviceId"]: a for a in d["items"]}
    a300 = items["SAN3-A300-01"]
    assert a300["removed"] and a300["disposedOn"] == "2026-10-31" and a300["levelText"] == "Đã thanh lý"
    assert a300["decision"] == {"decision": "dispose", "label": "Thanh lý", "reason": None, "reasonLabel": "",
                                "note": "Bán thanh lý đợt 10/2026", "until": "2026-10-31", "by": "editor1",
                                "at": a300["decision"]["at"]}
    assert d["summary"]["removed"] == 5                                         # 2 REMOVED in the file + 3 disposed
    from app import inbox
    db = SessionLocal()
    try:
        assert not any("SAN3-A300" in i["detail"] or "svr85" in i["detail"] for i in inbox.asset_items(db, fiscal.today_vn()))
    finally:
        db.close()
    y = fiscal.fiscal_year(fiscal.today_vn(), 1) + 1
    plan = editor.post("/api/spend/plans", json={"fiscalYear": y}).json()
    assert not any("DC ENTERPRISE STORAGE CLUSTER" in l["title"] for l in plan["lines"])
    # the old list (devices not marked REMOVED) is imported again: still disposed, counted as unchanged
    r = _upload(admin, "/api/assets/dc/import/commit", _book()).json()
    assert r["update"] == 0
    assert {a["deviceId"]: a for a in admin.get("/api/assets/dc").json()["items"]}["svr85"]["removed"] is True
    # undo: another decision puts the device back into use
    editor.post("/api/assets/dc/decision", json={"ids": [ids[2]], "decision": None})
    svr85 = {a["deviceId"]: a for a in admin.get("/api/assets/dc").json()["items"]}["svr85"]
    assert svr85["removed"] is False and svr85["disposedOn"] is None and svr85["decision"] is None
    assert editor.post("/api/assets/dc/decision", json={"ids": ids, "decision": "dispose", "until": "31/02/2026"}).status_code == 400
    nobody = _login(app_, "nobody1")
    assert nobody.post("/api/assets/dc/decision", json={"ids": ids, "decision": "dispose"}).status_code == 403
