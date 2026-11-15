"""Kế hoạch ngân sách năm sau: projection arithmetic (pure) and the plan workflow through the API."""
import datetime as dt
import io
from decimal import Decimal

from openpyxl import load_workbook

from app.database import SessionLocal
from app.spend import fiscal
from app.spend.budget_plan import renewal_starts, uplifted
from app.spend.models import OpexItem
from app.models import AuditLog

from test_spend_opex import _cat_id, _login, admin, app_, editor  # noqa: F401  (fixtures)

D = dt.date


# ---------------------------------------------------------------- pure
def test_renewal_starts_inside_the_year():
    y0, y1 = D(2027, 1, 1), D(2027, 12, 31)
    # yearly term ending mid-year -> one renewal on the anniversary
    assert renewal_starts(D(2026, 7, 1), D(2027, 6, 30), y0, y1) == [D(2027, 7, 1)]
    # yearly term ending in the current year: its renewal is this year, the next one falls in 2027
    assert renewal_starts(D(2025, 7, 1), D(2026, 6, 30), y0, y1) == [D(2027, 7, 1)]
    # quarter-long term -> four renewals in 2027
    assert renewal_starts(D(2026, 10, 1), D(2026, 12, 31), y0, y1) == [D(2027, 1, 1), D(2027, 4, 1), D(2027, 7, 1), D(2027, 10, 1)]
    # month term starting on the 31st stays on month ends (31/01 -> 28/02 -> 31/03 is not drift)
    assert renewal_starts(D(2026, 12, 1), D(2026, 12, 31), y0, D(2027, 3, 31)) == [D(2027, 1, 1), D(2027, 2, 1), D(2027, 3, 1)]
    # 30-day term (not whole months) keeps its length in days
    assert renewal_starts(D(2026, 12, 2), D(2026, 12, 31), y0, D(2027, 3, 1)) == [D(2027, 1, 1), D(2027, 1, 31), D(2027, 3, 2)][:2]
    # 3-year term ending after the year: nothing to plan
    assert renewal_starts(D(2026, 1, 1), D(2028, 12, 31), y0, y1) == []
    # 29/02 term: renewed on 01/03 when the year is not a leap year
    assert renewal_starts(D(2024, 2, 29), D(2025, 2, 28), D(2025, 1, 1), D(2025, 12, 31)) == [D(2025, 3, 1)]
    # fiscal year from April
    assert renewal_starts(D(2026, 4, 1), D(2027, 3, 31), D(2027, 4, 1), D(2028, 3, 31)) == [D(2027, 4, 1)]


def test_uplift_rounding():
    assert uplifted(120_000_000, Decimal("5")) == 126_000_000
    assert uplifted(33_333_333, Decimal("7.5")) == 35_833_333        # 35 833 332.975 -> half up
    assert uplifted(3, Decimal("50")) == 5                            # 4.5 -> 5
    assert uplifted(0, Decimal("10")) == 0


# ---------------------------------------------------------------- API
def _year():
    return fiscal.fiscal_year(fiscal.today_vn(), 1) + 1


def _seed(c, y):
    """Opex / Capex of the current year and of year y (start month 1)."""
    b = y - 1
    o = lambda **kw: c.post("/api/spend/opex", json={  # noqa: E731
        "categoryId": _cat_id(), "vendorName": "Dell Việt Nam", "billingCycle": "yearly", "vatPct": 10,
        "ownerName": "Nguyễn Văn A", **kw}).json()
    a = o(title="Bảo hành Dell R750", startDate=f"{b}-07-01", endDate=f"{y}-06-30", amountNet=120_000_000)
    q = o(title="Giám sát NOC theo quý", startDate=f"{b}-10-01", endDate=f"{b}-12-31", amountNet=30_000_000, billingCycle="quarterly")
    o(title="Khảo sát một lần", startDate=f"{b}-03-01", endDate=f"{b}-03-31", amountNet=10_000_000, billingCycle="one_time")
    d = o(title="License firewall năm sau", startDate=f"{y}-02-01", endDate=f"{y + 1}-01-31", amountNet=50_000_000,
          status="planned", categoryId=_cat_id("LIC_FIREWALL"))
    e = o(title="Dịch vụ đã ngừng", startDate=f"{b}-05-01", endDate=f"{y}-04-30", amountNet=80_000_000)
    assert c.post(f"/api/spend/opex/{e['code']}/stop", json={"reason": "Chuyển nhà cung cấp"}).status_code == 200
    p = c.post("/api/spend/capex", json={"name": "Mở rộng storage", "fiscalYear": y, "ownerName": "Trần B", "items": [
        {"categoryId": _cat_id("HW_STORAGE"), "description": "Thêm 2 shelf", "vendorName": "NetApp", "amountNet": 1_000_000_000,
         "vatPct": 10, "coverageYears": 0}]}).json()
    return a, q, d, p


def test_plan_lines_totals_cap_and_edits(app_, admin):
    y = _year()
    a, q, d, cpx = _seed(admin, y)
    viewer, ed = _login(app_, "viewer1"), _login(app_, "editor1")
    assert viewer.post("/api/spend/plans", json={"fiscalYear": y}).status_code == 403
    plan = ed.post("/api/spend/plans", json={"fiscalYear": y, "upliftPct": 5, "budgetCap": 1_500_000_000}).json()
    assert ed.post("/api/spend/plans", json={"fiscalYear": y}).status_code == 409        # one draft per year
    lines = {l["title"]: l for l in plan["lines"]}
    assert set(lines) == {"Bảo hành Dell R750", "Giám sát NOC theo quý", "License firewall năm sau", "Mở rộng storage"}
    r = lines["Bảo hành Dell R750"]
    assert (r["source"], r["startDate"], r["terms"], r["baseNet"], r["amountNet"], r["amountGross"]) == \
        ("renewal", f"{y}-07-01", 1, 120_000_000, 126_000_000, 138_600_000)
    n = lines["Giám sát NOC theo quý"]
    assert (n["terms"], n["baseNet"], n["amountNet"], n["startDate"]) == (4, 120_000_000, 126_000_000, f"{y}-01-01")
    f = lines["License firewall năm sau"]
    assert (f["source"], f["amountNet"], f["amountGross"]) == ("committed", 50_000_000, 55_000_000)
    s = lines["Mở rộng storage"]
    assert (s["source"], s["kind"], s["amountGross"], s["sourceCode"]) == ("capex", "capex", 1_100_000_000, cpx["code"])

    # A new initiative, priority "Nên có"
    plan = ed.post(f"/api/spend/plans/{plan['id']}/lines", json={"kind": "opex", "title": "Dịch vụ SOC 24/7",
                   "categoryId": _cat_id("OUTSOURCED"), "baseNet": 200_000_000, "startDate": f"{y}-04-01"}).json()
    sm = plan["summary"]
    assert sm["total"] == 138_600_000 * 2 + 55_000_000 + 1_100_000_000 + 220_000_000 == 1_652_200_000
    assert sm["baseline"] == {"fiscalYear": y - 1, "opex": 132_000_000 + 33_000_000 + 11_000_000, "capex": 0, "total": 176_000_000}
    # quarter of the first start: NOC 01/01 + firewall 01/02 + storage (no date -> Q1) | SOC 01/04 | Dell 01/07
    assert sm["byQuarter"] == [138_600_000 + 55_000_000 + 1_100_000_000, 220_000_000, 138_600_000, 0]
    new_id = next(l["id"] for l in plan["lines"] if l["source"] == "new")
    assert sm["overCap"] == 152_200_000 and sm["cutLineIds"] == [new_id]          # "Nên có" is above the cut line
    assert sm["renewalUplift"] == 12_000_000

    # Edits: line uplift, a typed amount, leaving a line out needs a reason
    rid, nid, fid = r["id"], n["id"], f["id"]
    plan = ed.put(f"/api/spend/plans/{plan['id']}/lines/{rid}", json={"upliftPct": 10}).json()
    assert next(l for l in plan["lines"] if l["id"] == rid)["amountNet"] == 132_000_000
    plan = ed.put(f"/api/spend/plans/{plan['id']}/lines/{nid}", json={"amountNet": 100_000_000}).json()
    assert next(l for l in plan["lines"] if l["id"] == nid)["amountMode"] == "manual"
    assert ed.put(f"/api/spend/plans/{plan['id']}/lines/{fid}", json={"included": False}).status_code == 400
    plan = ed.put(f"/api/spend/plans/{plan['id']}/lines/{fid}", json={"included": False, "note": "Gộp vào hợp đồng khung"}).json()
    assert plan["summary"]["excludedTotal"] == 55_000_000
    # Plan uplift change follows on auto lines only
    plan = ed.put(f"/api/spend/plans/{plan['id']}", json={"upliftPct": 8, "rowVersion": plan["rowVersion"]}).json()
    by = {l["id"]: l for l in plan["lines"]}
    assert by[rid]["amountNet"] == 132_000_000 and by[nid]["amountNet"] == 100_000_000
    assert ed.put(f"/api/spend/plans/{plan['id']}", json={"upliftPct": 9, "rowVersion": 1}).status_code == 409
    assert ed.delete(f"/api/spend/plans/{plan['id']}/lines/{rid}").status_code == 409    # suggested lines are left out, not deleted

    # Refresh keeps edits; the renewal done early becomes a committed line, the edited one is flagged
    renewed = admin.post(f"/api/spend/opex/{a['code']}/renew", json={"amountNet": 129_000_000}).json()
    plan = ed.post(f"/api/spend/plans/{plan['id']}/refresh").json()
    by = {l["id"]: l for l in plan["lines"]}
    assert by[rid]["stale"] is True and by[nid]["amountNet"] == 100_000_000 and by[fid]["included"] is False
    com = next(l for l in plan["lines"] if l["sourceCode"] == renewed["code"])
    assert (com["source"], com["amountNet"]) == ("committed", 129_000_000)
    assert ed.delete(f"/api/spend/plans/{plan['id']}/lines/{rid}").status_code == 200     # stale lines can go


def test_workflow_apply_actuals_and_revision(app_, admin):
    y = _year()
    a, q, d, cpx = _seed(admin, y)
    ed, ap, viewer = _login(app_, "editor1"), _login(app_, "approver1"), _login(app_, "viewer1")
    plan = ed.post("/api/spend/plans", json={"fiscalYear": y}).json()
    pid = plan["id"]
    vendor = admin.get("/api/spend/meta").json()["vendors"][0]["id"]
    plan = ed.post(f"/api/spend/plans/{pid}/lines", json={"kind": "opex", "title": "Dịch vụ SOC 24/7", "categoryId": _cat_id("OUTSOURCED"),
                                                          "baseNet": 200_000_000, "vendorId": vendor, "startDate": f"{y}-04-01"}).json()
    plan = ed.post(f"/api/spend/plans/{pid}/lines", json={"kind": "capex", "title": "Thay core switch", "categoryId": _cat_id("HW_NETWORK"),
                                                          "baseNet": 900_000_000}).json()
    assert ed.post(f"/api/spend/plans/{pid}/submit").json()["status"] == "submitted"
    assert ed.put(f"/api/spend/plans/{pid}", json={"upliftPct": 7}).status_code == 409      # locked while submitted
    assert ed.post(f"/api/spend/plans/{pid}/approve").status_code == 403
    inbox = ap.get("/api/inbox?fresh=true").json()["items"]
    assert any("chờ duyệt" in i["title"] and i["page"] == "spend-plan" for i in inbox)
    plan = ap.post(f"/api/spend/plans/{pid}/approve").json()
    assert plan["status"] == "approved" and plan["approvedBy"] == "approver1"
    assert ed.post(f"/api/spend/plans/{pid}/apply").status_code == 403
    out = ap.post(f"/api/spend/plans/{pid}/apply").json()
    assert len(out["applied"]["created"]) == 2 and ap.post(f"/api/spend/plans/{pid}/apply").json()["applied"]["created"] == []
    opx = next(c for c in out["applied"]["created"] if c.startswith("OPX"))
    db = SessionLocal()
    o = db.query(OpexItem).filter_by(code=opx).one()
    assert (o.status, o.fiscal_year, o.amount_net, o.start_date) == ("planned", y, 200_000_000, D(y, 4, 1))
    assert db.query(AuditLog).filter(AuditLog.action == "spend.plan.apply", AuditLog.detail.contains(opx)).count() == 1
    db.close()

    # Actuals: the renewal done later counts against its line; an Opex outside the plan is listed
    admin.post(f"/api/spend/opex/{a['code']}/renew", json={"amountNet": 130_000_000})
    admin.post("/api/spend/opex", json={"categoryId": _cat_id(), "vendorName": "HPE", "title": "Hỗ trợ khẩn cấp", "billingCycle": "yearly",
                                        "startDate": f"{y}-05-01", "endDate": f"{y + 1}-04-30", "amountNet": 40_000_000, "vatPct": 10,
                                        "ownerName": "A"})
    plan = viewer.get(f"/api/spend/plans/{pid}").json()
    act = plan["actuals"]
    rline = next(l for l in plan["lines"] if l["title"] == "Bảo hành Dell R750")
    got = act["lines"][str(rline["id"])]
    assert (got["actual"], got["variance"]) == (143_000_000, 143_000_000 - 138_600_000)
    assert [u["title"] for u in act["unplanned"]] == ["Hỗ trợ khẩn cấp"] and act["unplannedTotal"] == 44_000_000

    # Excel
    r = viewer.get(f"/api/spend/plans/{pid}/export.xlsx")
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Tổng hợp", "Chi tiết", "Theo loại", "Thực hiện"]

    assert ed.post("/api/spend/plans", json={"fiscalYear": y}).status_code == 409     # approved year: revise, not a new plan
    # Revision: v2 copies the lines; approving it supersedes v1
    v2 = ed.post(f"/api/spend/plans/{pid}/revise").json()
    assert (v2["version"], v2["status"], len(v2["lines"])) == (2, "draft", len(plan["lines"]))
    ed.post(f"/api/spend/plans/{v2['id']}/submit")
    ap.post(f"/api/spend/plans/{v2['id']}/approve")
    assert viewer.get(f"/api/spend/plans/{pid}").json()["status"] == "superseded"
    assert [p["version"] for p in viewer.get("/api/spend/plans").json()["items"]] == [2, 1]
