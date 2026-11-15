"""Budget report: concrete numbers, and the web JSON, Excel and PDF showing the same totals."""
import datetime as dt
import io
import json

import pytest
from openpyxl import load_workbook

from app import report_spend
from app.database import SessionLocal
from app.spend import report
from app.spend.settings import SpendSettings

from test_spend_opex import _cat_id, _login, _opex, admin, app_, editor  # noqa: F401  (fixtures)

TODAY = dt.date(2026, 11, 14)
CFG = SpendSettings(fiscal_start_month=1)


@pytest.fixture()
def data(app_, editor):
    """2 active + 1 stopped Opex in FY2026, 1 planned in FY2027, an approved Capex (FY2027) and a draft one."""
    approver = _login(app_, "approver1")
    mk = lambda **kw: editor.post("/api/spend/opex", json=_opex(assets=[], **kw)).json()  # noqa: E731
    a = mk(title="Bảo hành Dell R750", vendorName="Dell", startDate="2026-01-01", endDate="2026-12-31", amountNet=100_000_000)
    b = mk(categoryId=_cat_id("LIC_FIREWALL"), title="License FortiGate", vendorName="FPT", startDate="2026-03-01",
           endDate="2027-02-28", amountNet=200_000_000)
    c = mk(title="Thiết bị đã thanh lý", vendorName="HPE", startDate="2026-01-01", endDate="2026-12-31", amountNet=50_000_000)
    editor.post(f"/api/spend/opex/{c['code']}/stop", json={"reason": "Thanh lý"})
    d = mk(categoryId=_cat_id("LIC_VMWARE"), title="VMware VCF", vendorName="Broadcom", startDate="2027-06-01",
           endDate="2028-05-31", amountNet=300_000_000, status="planned")
    p = editor.post("/api/spend/capex", json={"name": "Cụm PROD", "fiscalYear": 2027, "goLiveDate": "2027-01-15", "items": [
        {"categoryId": _cat_id("HW_SERVER"), "description": "4 máy chủ", "vendorName": "Dell", "amountNet": 1_000_000_000,
         "vatPct": 10, "coverageYears": 1},
        {"categoryId": _cat_id("OTHER"), "description": "Triển khai", "vendorName": "FPT", "amountNet": 100_000_000,
         "vatPct": 8, "coverageYears": 0}]}).json()
    approver.post(f"/api/spend/capex/{p['code']}/status", json={"status": "approved"})
    editor.post("/api/spend/capex", json={"name": "Đề xuất DR", "fiscalYear": 2027, "items": [
        {"categoryId": _cat_id("OTHER"), "description": "Site DR", "vendorName": "FPT", "amountNet": 500_000_000}]})
    return {"a": a, "b": b, "c": c, "d": d, "capex": p}


def _report(filters=None):
    db = SessionLocal()
    try:
        return report.build_budget_report(db, report.parse_filters(filters, CFG, TODAY), CFG, TODAY)
    finally:
        db.close()


def test_totals_by_year_category_vendor(data):
    r = _report()
    assert r["filters"]["fiscalYears"] == [2026, 2027]
    assert r["totals"] == {"total": 1_868_000_000, "opex": 660_000_000, "capex": 1_208_000_000, "net": 1_700_000_000,
                           "count": 5, "vendorCount": 3}
    assert r["byYear"] == [{"fiscalYear": 2026, "opex": 330_000_000, "capex": 0, "total": 330_000_000},
                           {"fiscalYear": 2027, "opex": 330_000_000, "capex": 1_208_000_000, "total": 1_538_000_000}]
    assert r["compare"]["deltaPct"] == 366.1 and r["compare"]["inFilter"]
    assert [(c["category"], c["total"]) for c in r["byCategory"]] == [
        ("Bảo hành máy chủ", 1_210_000_000), ("License VMware", 330_000_000), ("License firewall", 220_000_000), ("Khác", 108_000_000)]
    assert r["byCategory"][0]["sharePct"] == 64.8
    assert [(v["vendor"], v["total"]) for v in r["topVendors"]] == [("Dell", 1_210_000_000), ("Broadcom", 330_000_000), ("FPT", 328_000_000)]
    assert "tăng 366,1%" in r["headline"]


def test_expiring_by_fiscal_quarter_and_decisions(data):
    r = _report()
    assert [(g["label"], g["count"], g["total"]) for g in r["expiringByQuarter"]] == [
        ("Q4/FY 2026", 1, 110_000_000), ("Q1/FY 2027", 1, 220_000_000)]
    assert r["expiringByQuarter"][0]["items"][0]["daysLeft"] == 47
    assert [(d["cls"], d["amount"]) for d in r["decisions"]] == [("bad", 110_000_000), ("warn", 330_000_000)]
    # Fiscal year starting in April: 31/12/2026 is Q3 of FY 2026, 28/02/2027 Q4 of FY 2026.
    q = report.fiscal_quarter
    assert q(dt.date(2026, 12, 31), 4)["label"] == "Q3/FY 2026" and q(dt.date(2027, 2, 28), 4)["label"] == "Q4/FY 2026"
    assert q(dt.date(2027, 4, 1), 4)["label"] == "Q1/FY 2027"


def test_filters(data):
    assert _report({"kind": "opex"})["totals"]["total"] == 660_000_000
    assert _report({"kind": "capex", "capexStatuses": ["draft", "approved"]})["totals"]["capex"] == 1_758_000_000
    assert _report({"opexStatuses": ["stopped"], "kind": "opex"})["totals"]["total"] == 55_000_000
    assert _report({"categoryIds": [_cat_id("LIC_FIREWALL")]})["totals"]["total"] == 220_000_000
    assert _report({"amountMin": 200_000_000, "amountMax": 400_000_000})["totals"]["count"] == 2   # 220M + 330M
    r = _report({"expiryFrom": "2026-12-01", "expiryTo": "2027-03-31"})                              # capex left out
    assert r["totals"]["total"] == 330_000_000 and r["totals"]["capex"] == 0
    fc = _report({"fiscalYears": [2026, 2027, 2028]})["fromCapex"]
    assert fc["count"] == 1 and fc["total"] == 165_000_000 and fc["byYear"] == [{"fiscalYear": 2028, "total": 165_000_000}]
    with pytest.raises(report.FilterError):
        report.parse_filters({"kind": "x"}, CFG, TODAY)
    with pytest.raises(report.FilterError):
        report.parse_filters({"amountMin": 5, "amountMax": 1}, CFG, TODAY)


def test_web_excel_pdf_show_the_same_numbers(data, editor):
    body = {"fiscalYears": [2026, 2027, 2028]}
    j = editor.post("/api/spend/reports/budget", json=body)
    assert j.status_code == 200, j.text
    j = j.json()
    x = editor.post("/api/spend/reports/budget.xlsx", json=body)
    assert x.status_code == 200 and x.headers["content-type"].startswith("application/vnd.openxmlformats")
    wb = load_workbook(io.BytesIO(x.content))
    ws = wb["Chi tiết"]
    head = [c.value for c in ws[4]]          # rows 1-2: title band, row 4: header
    col = head.index("Sau VAT") + 1
    detail = [ws.cell(row=r, column=col).value for r in range(5, ws.max_row + 1)]
    assert len(detail) == j["totals"]["count"] == j["rowsTotal"] and sum(detail) == j["totals"]["total"]
    years = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=4).value for r in range(1, wb["Tổng quan"].max_row + 1)
             for ws in [wb["Tổng quan"]] if isinstance(ws.cell(row=r, column=1).value, int)}
    assert years == {y["fiscalYear"]: y["total"] for y in j["byYear"]}
    html = report_spend.render_budget_html(_report(body), "FY 2026, 2027, 2028")
    assert report_spend.money_vn(j["totals"]["total"]) in html
    for y in j["byYear"]:
        assert report_spend.money_vn(y["total"]) in html
    p = editor.get("/api/spend/reports/budget.pdf?filters=" + json.dumps(body))
    assert p.status_code == 200 and p.content[:5] == b"%PDF-"


def test_formula_injection_and_escaping(data, editor):
    editor.post("/api/spend/opex", json=_opex(assets=[], title='=HYPERLINK("http://evil","x")', vendorName="<b>NCC</b>",
                                              startDate="2026-01-01", endDate="2026-12-31"))
    x = editor.post("/api/spend/reports/budget.xlsx", json={})
    ws = load_workbook(io.BytesIO(x.content))["Chi tiết"]
    cells = [c for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("=HYPERLINK")]
    assert cells and all(c.data_type == "s" for c in cells)
    html = report_spend.render_budget_html(_report(), "x")
    assert "<b>NCC</b>" not in html and "&lt;b&gt;NCC&lt;/b&gt;" in html


def test_saved_filters_private_unless_shared(app_, editor):
    viewer = _login(app_, "viewer1")
    r = editor.post("/api/spend/reports/filters", json={"name": "Ngân sách HW", "filters": {"kind": "opex", "fiscalYears": [2027]}})
    assert r.status_code == 200 and r.json()["filters"]["kind"] == "opex"
    assert viewer.get("/api/spend/reports/filters").json() == []
    assert viewer.delete(f"/api/spend/reports/filters/{r.json()['id']}").status_code == 404
    editor.post("/api/spend/reports/filters", json={"name": "Ngân sách HW", "filters": {"kind": "opex"}, "shared": True})
    assert [f["name"] for f in viewer.get("/api/spend/reports/filters").json()] == ["Ngân sách HW"]
    assert editor.post("/api/spend/reports/filters", json={"name": "x", "filters": {"kind": "bad"}}).status_code == 400
    assert editor.delete(f"/api/spend/reports/filters/{r.json()['id']}").status_code == 200
    assert _login(app_, "nobody1").post("/api/spend/reports/budget", json={}).status_code == 403
