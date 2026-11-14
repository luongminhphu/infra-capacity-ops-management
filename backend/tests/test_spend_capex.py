"""Capex -> planned Opex: mapping arithmetic (pure) and reconcile through the API."""
import datetime as dt
from decimal import Decimal

import pytest

from app.database import SessionLocal
from app.spend.capex import plan_item
from app.spend.models import CapexItem, CapexProject, OpexItem, SpendCategory
from app.spend.settings import SpendSettings

from test_spend_opex import _cat_id, _login, admin, app_, editor  # noqa: F401  (fixtures)

D = dt.date


def _plan(start, years, net=1_000_000_000, start_month=1, pct=None, cat_pct=Decimal("15"), amount=None, go_live=None):
    item = CapexItem(amount_net=net, vat_pct=Decimal("10"), coverage_years=years, start_date=start,
                     renewal_pct=pct, renewal_amount=amount)
    project = CapexProject(go_live_date=go_live)
    cat = SpendCategory(renewal_pct=cat_pct)
    return plan_item(item, project, cat, SpendSettings(fiscal_start_month=start_month))


@pytest.mark.parametrize("start,years,start_month,renewal,end,fy", [
    (D(2026, 6, 15), 1, 1, D(2027, 6, 15), D(2028, 6, 14), 2027),      # 1 year -> next year
    (D(2026, 6, 15), 3, 1, D(2029, 6, 15), D(2030, 6, 14), 2029),      # 3 years -> 4th year
    (D(2026, 1, 1), 5, 1, D(2031, 1, 1), D(2031, 12, 31), 2031),
    (D(2026, 12, 31), 1, 1, D(2027, 12, 31), D(2028, 12, 30), 2027),   # last day of the fiscal year
    (D(2028, 2, 29), 1, 1, D(2029, 3, 1), D(2030, 2, 28), 2029),       # cover ends 28/02/2029 -> renew 01/03
    (D(2028, 2, 29), 4, 1, D(2032, 2, 29), D(2033, 2, 28), 2032),      # leap year again: 29/02
    (D(2028, 2, 29), 1, 3, D(2029, 3, 1), D(2030, 2, 28), 2029),       # FY from March: renewal in FY 2029
    (D(2031, 3, 1), 1, 1, D(2032, 3, 1), D(2033, 2, 28), 2032),        # term over a leap day
    (D(2026, 3, 31), 1, 4, D(2027, 3, 31), D(2028, 3, 30), 2026),      # FY starts in April: still FY 2026
    (D(2026, 4, 1), 1, 4, D(2027, 4, 1), D(2028, 3, 31), 2027),
])
def test_renewal_dates_and_fiscal_year(start, years, start_month, renewal, end, fy):
    p, reason = _plan(start, years, start_month=start_month)
    assert reason is None
    assert (p.start_date, p.end_date, p.fiscal_year) == (renewal, end, fy)


def test_amounts():
    p, _ = _plan(D(2026, 1, 1), 3, net=1_234_567_891)                  # category 15 %
    assert (p.amount_net, p.amount_gross, p.pct) == (185_185_184, 203_703_702, Decimal("15"))
    p, _ = _plan(D(2026, 1, 1), 1, net=480_000_000, pct=Decimal("20"))  # item % overrides category
    assert p.amount_net == 96_000_000 and p.amount_gross == 105_600_000
    p, _ = _plan(D(2026, 1, 1), 1, amount=77_000_000)                  # typed amount wins
    assert p.amount_net == 77_000_000 and p.pct is None
    p, _ = _plan(D(2026, 1, 1), 1, net=3, cat_pct=Decimal("50"))         # 1.5 -> 2 (half up)
    assert p.amount_net == 2


def test_no_plan_reasons():
    assert _plan(D(2026, 1, 1), 0)[0] is None
    assert "Thiếu ngày" in _plan(None, 3)[1]
    assert _plan(None, 3, go_live=D(2026, 7, 1))[0].start_date == D(2029, 7, 1)   # project go-live used
    assert "% gia hạn" in _plan(D(2026, 1, 1), 1, cat_pct=None)[1]


# ---------------------------------------------------------------- API
def _project(**kw):
    return {"name": "Mở rộng cụm PROD 2026", "goLiveDate": "2026-06-15", "ownerName": "Trần B",
            "items": [
                {"categoryId": _cat_id("HW_SERVER"), "description": "4 máy chủ Dell R760", "vendorName": "Dell Việt Nam",
                 "amountNet": 2_000_000_000, "vatPct": 10, "coverageYears": 3},
                {"categoryId": _cat_id("LIC_VMWARE"), "description": "VMware VCF 128 core", "vendorName": "Broadcom",
                 "amountNet": 1_000_000_000, "vatPct": 10, "coverageYears": 1},
                {"categoryId": _cat_id("OTHER"), "description": "Dịch vụ triển khai", "vendorName": "FPT",
                 "amountNet": 150_000_000, "vatPct": 8, "coverageYears": 0},
            ], **kw}


def _opex_of(code):
    db = SessionLocal()
    p = db.query(CapexProject).filter_by(code=code).one()
    rows = {o.title: (o.code, o.fiscal_year, o.start_date, o.amount_net, o.status, o.amount_mode)
            for o in db.query(OpexItem).filter_by(capex_project_id=p.id)}
    db.close()
    return rows


def test_draft_previews_but_does_not_generate(app_, editor):
    r = editor.post("/api/spend/capex", json=_project())
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["code"] == "CPX-2026-0001" and p["status"] == "draft" and p["amountNet"] == 3_150_000_000
    assert p["items"][0]["plan"] == {"startDate": "2029-06-15", "endDate": "2030-06-14", "fiscalYear": 2029,
                                     "amountNet": 300_000_000, "amountGross": 330_000_000}
    assert p["items"][1]["plan"]["fiscalYear"] == 2027 and p["items"][1]["plan"]["amountNet"] == 200_000_000
    assert p["items"][2]["plan"] is None
    assert _opex_of(p["code"]) == {}
    pv = editor.post("/api/spend/capex/preview", json=_project()).json()
    assert [i["plan"]["fiscalYear"] if i["plan"] else None for i in pv["items"]] == [2029, 2027, None]


def test_approve_generates_then_edits_follow_only_managed(app_, editor):
    approver = _login(app_, "approver1")
    p = editor.post("/api/spend/capex", json=_project()).json()
    # Editors cannot approve.
    assert editor.post(f"/api/spend/capex/{p['code']}/status", json={"status": "approved"}).status_code == 403
    r = approver.post(f"/api/spend/capex/{p['code']}/status", json={"status": "approved"})
    assert r.status_code == 200 and len(r.json()["reconcile"]["created"]) == 2
    rows = _opex_of(p["code"])
    assert rows["Gia hạn: 4 máy chủ Dell R760"][1:] == (2029, D(2029, 6, 15), 300_000_000, "planned", "auto")
    assert rows["Gia hạn: VMware VCF 128 core"][1:] == (2027, D(2027, 6, 15), 200_000_000, "planned", "auto")
    lic_code = rows["Gia hạn: VMware VCF 128 core"][0]
    hw_code = rows["Gia hạn: 4 máy chủ Dell R760"][0]
    assert lic_code.startswith("OPX-2027-") and hw_code.startswith("OPX-2029-")
    # Reconcile is idempotent.
    d = editor.get(f"/api/spend/capex/{p['code']}").json()
    body = {**_project(), "rowVersion": d["rowVersion"], "items": [
        {**_project()["items"][i], "id": d["items"][i]["id"]} for i in range(3)]}
    r = editor.put(f"/api/spend/capex/{p['code']}", json=body).json()
    assert r["reconcile"] == {"created": [], "updated": [], "removed": [], "kept": []}
    # Confirm the licence renewal; then change both items: only the managed (HW) one follows.
    assert approver.post(f"/api/spend/capex/{p['code']}/opex/confirm", json={"codes": [lic_code]}).status_code == 200
    items = [{**body["items"][0], "amountNet": 2_400_000_000, "coverageYears": 5},
             {**body["items"][1], "amountNet": 1_500_000_000}, body["items"][2]]
    r = editor.put(f"/api/spend/capex/{p['code']}", json={**body, "rowVersion": r["rowVersion"], "items": items}).json()
    assert r["reconcile"]["updated"] == [hw_code] and r["reconcile"]["kept"] == [lic_code]
    rows = _opex_of(p["code"])
    assert rows["Gia hạn: 4 máy chủ Dell R760"][1:4] == (2031, D(2031, 6, 15), 360_000_000)   # code kept: OPX-2029-…
    assert rows["Gia hạn: 4 máy chủ Dell R760"][0] == hw_code
    assert rows["Gia hạn: VMware VCF 128 core"][3:5] == (200_000_000, "active")              # not overwritten
    lic_view = next(i for i in r["items"] if i["opex"] and i["opex"]["code"] == lic_code)
    assert lic_view["opex"]["state"] == "confirmed" and lic_view["opex"]["differsFromPlan"] is True
    assert lic_view["plan"]["amountNet"] == 300_000_000


def test_hand_edited_opex_is_not_overwritten_and_item_removal(app_, editor):
    approver = _login(app_, "approver1")
    p = editor.post("/api/spend/capex", json=_project()).json()
    approver.post(f"/api/spend/capex/{p['code']}/status", json={"status": "approved"})
    rows = _opex_of(p["code"])
    hw_code, lic_code = rows["Gia hạn: 4 máy chủ Dell R760"][0], rows["Gia hạn: VMware VCF 128 core"][0]
    o = editor.get(f"/api/spend/opex/{hw_code}").json()
    assert o["managedByCapex"] is True and o["capexProjectCode"] == p["code"]
    from test_spend_opex import _opex
    edited = editor.put(f"/api/spend/opex/{hw_code}", json={**_opex(categoryId=o["categoryId"], vendorName="Dell Việt Nam",
                        startDate=o["startDate"], endDate=o["endDate"], amountNet=280_000_000, status="planned"),
                        "rowVersion": o["rowVersion"], "assets": []}).json()
    assert edited["managedByCapex"] is False and edited["amountNet"] == 280_000_000
    d = editor.get(f"/api/spend/capex/{p['code']}").json()
    # Remove both generating items: the edited one is kept (unlinked), the managed one is deleted.
    body = {**_project(), "rowVersion": d["rowVersion"], "items": [{**_project()["items"][2], "id": d["items"][2]["id"]}]}
    r = editor.put(f"/api/spend/capex/{p['code']}", json=body).json()
    assert r["reconcile"]["removed"] == [lic_code] and r["reconcile"]["kept"] == [hw_code]
    assert [x["code"] for x in r["orphanOpex"]] == [hw_code]
    assert editor.get(f"/api/spend/opex/{lic_code}").status_code == 404
    assert editor.get(f"/api/spend/opex/{hw_code}").json()["amountNet"] == 280_000_000


def test_back_to_draft_removes_managed_and_delete_rules(app_, admin, editor):
    approver = _login(app_, "approver1")
    p = editor.post("/api/spend/capex", json=_project()).json()
    approver.post(f"/api/spend/capex/{p['code']}/status", json={"status": "approved"})
    r = approver.post(f"/api/spend/capex/{p['code']}/status", json={"status": "draft"}).json()
    assert len(r["reconcile"]["removed"]) == 2 and _opex_of(p["code"]) == {}
    assert editor.post(f"/api/spend/capex/{p['code']}/status", json={"status": "closed"}).status_code == 409
    approver.post(f"/api/spend/capex/{p['code']}/status", json={"status": "approved"})
    code = list(_opex_of(p["code"]).values())[0][0]
    approver.post(f"/api/spend/capex/{p['code']}/opex/confirm", json={"codes": [code]})
    assert editor.delete(f"/api/spend/capex/{p['code']}").status_code == 403
    assert admin.delete(f"/api/spend/capex/{p['code']}").status_code == 409     # has a confirmed Opex
    q = editor.post("/api/spend/capex", json=_project()).json()
    assert admin.delete(f"/api/spend/capex/{q['code']}").status_code == 200


def test_validation_and_locking(app_, editor):
    items = _project()["items"]
    r = editor.post("/api/spend/capex", json=_project(items=[{**items[0], "vendorName": ""}]))
    assert r.status_code == 400 and "nhà cung cấp" in r.json()["detail"]
    r = editor.post("/api/spend/capex", json=_project(items=[{**items[0], "coverageYears": 11}]))
    assert r.status_code == 400
    r = editor.post("/api/spend/capex", json=_project(goLiveDate=None, fiscalYear=None))
    assert r.status_code == 400
    p = editor.post("/api/spend/capex", json=_project()).json()
    assert editor.put(f"/api/spend/capex/{p['code']}", json={**_project(), "rowVersion": 99}).status_code == 409
    # Another project's item id cannot be hijacked.
    q = editor.post("/api/spend/capex", json=_project()).json()
    r = editor.put(f"/api/spend/capex/{q['code']}", json={**_project(), "rowVersion": q["rowVersion"],
                   "items": [{**items[0], "id": p["items"][0]["id"]}]})
    assert r.status_code == 400
    viewer = _login(app_, "viewer1")
    assert viewer.get("/api/spend/capex").status_code == 200
    assert viewer.post("/api/spend/capex", json=_project()).status_code == 403
    assert editor.get("/api/spend/capex").json()[0]["itemCount"] == 3
