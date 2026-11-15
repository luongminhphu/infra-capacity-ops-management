"""Chi phí & Hợp đồng — writes that must stay correct under double clicks and concurrent users:
apply a budget plan once (DAT-02), optimistic lock in the UPDATE itself (DAT-05), one Excel
import at a time (DAT-11)."""
import threading

import pytest
from fastapi import HTTPException

from app.database import SessionLocal
from app.models import User
from app.spend import budget_plan as bp, importer, service, settings as spend_settings
from app.spend.models import BudgetPlan, BudgetPlanLine, CapexProject, OpexItem

from test_spend_budget_plan import _year
from test_spend_import import OPEX_FILE, _xlsx
from test_spend_opex import _cat_id, _login, _opex, admin, app_, editor  # noqa: F401  (fixtures)


def _approved_plan(app_, admin):
    """Approved plan for next year with one new Opex line (has a vendor) and one new Capex line."""
    y = _year()
    admin.post("/api/spend/opex", json=_opex(assets=[]))            # creates a vendor
    vendor = admin.get("/api/spend/meta").json()["vendors"][0]["id"]
    ed, ap = _login(app_, "editor1"), _login(app_, "approver1")
    pid = ed.post("/api/spend/plans", json={"fiscalYear": y}).json()["id"]
    ed.post(f"/api/spend/plans/{pid}/lines", json={"kind": "opex", "title": "Dịch vụ SOC 24/7", "categoryId": _cat_id("OUTSOURCED"),
                                                   "baseNet": 200_000_000, "vendorId": vendor, "startDate": f"{y}-04-01"})
    ed.post(f"/api/spend/plans/{pid}/lines", json={"kind": "capex", "title": "Thay core switch", "categoryId": _cat_id("HW_NETWORK"),
                                                   "baseNet": 900_000_000})
    assert ed.post(f"/api/spend/plans/{pid}/submit").status_code == 200
    plan = ap.post(f"/api/spend/plans/{pid}/approve").json()
    assert plan["status"] == "approved"
    return ap, plan


def _counts():
    db = SessionLocal()
    try:
        return db.query(OpexItem).count(), db.query(CapexProject).count()
    finally:
        db.close()


# ---------------------------------------------------------------- DAT-02
def test_apply_twice_with_same_row_version_creates_once(app_, admin):
    ap, plan = _approved_plan(app_, admin)
    opex0, capex0 = _counts()
    body = {"rowVersion": plan["rowVersion"]}
    first = ap.post(f"/api/spend/plans/{plan['id']}/apply", json=body)
    assert first.status_code == 200 and len(first.json()["applied"]["created"]) == 2
    second = ap.post(f"/api/spend/plans/{plan['id']}/apply", json=body)         # double click: same rowVersion
    assert second.status_code == 409 and "tải lại" in second.json()["detail"]
    assert _counts() == (opex0 + 1, capex0 + 1)


def test_apply_loses_line_claim_to_concurrent_apply_and_creates_nothing(app_, admin, monkeypatch):
    """Another request marks the line applied between our SELECT and our claim: the conditional
    UPDATE ... WHERE applied_code IS NULL hits 0 rows, we raise 409 and our Opex is rolled back."""
    _ap, plan = _approved_plan(app_, admin)
    opex0, capex0 = _counts()
    original = bp.service.create

    def create_after_rival(db, data, *a, **kw):
        rival = SessionLocal()
        rival.query(BudgetPlanLine).filter(BudgetPlanLine.plan_id == plan["id"], BudgetPlanLine.kind == "opex") \
            .update({BudgetPlanLine.applied_code: "OPX-RIVAL"})
        rival.commit()
        rival.close()
        return original(db, data, *a, **kw)

    monkeypatch.setattr(bp.service, "create", create_after_rival)
    db = SessionLocal()
    try:
        p = db.get(BudgetPlan, plan["id"])
        user = db.query(User).filter_by(username="approver1").one()
        with pytest.raises(HTTPException) as exc:
            bp.apply(db, p, spend_settings.load(db), user, None)
        assert exc.value.status_code == 409
        db.rollback()
    finally:
        db.close()
    assert _counts() == (opex0, capex0)


# ---------------------------------------------------------------- DAT-05
def test_stale_row_version_is_rejected_by_the_update_itself(editor):
    """Session A read version 1; B saves (version 2). A's own copy still says 1, so a Python-only
    comparison would pass and overwrite B — the conditional UPDATE refuses instead."""
    item = editor.post("/api/spend/opex", json=_opex(assets=[])).json()
    a = SessionLocal()
    try:
        mine = a.query(OpexItem).filter_by(code=item["code"]).one()
        assert mine.row_version == 1
        saved = editor.put(f"/api/spend/opex/{item['code']}", json={**_opex(assets=[]), "title": "Bản của B", "rowVersion": 1})
        assert saved.status_code == 200 and saved.json()["rowVersion"] == 2
        assert mine.row_version == 1                                   # A's stale copy
        with pytest.raises(HTTPException) as exc:
            service.bump_version(a, mine, mine.row_version)
        assert exc.value.status_code == 409 and exc.value.detail == service.STALE_MESSAGE
        a.rollback()
        service.bump_version(a, mine, 2)                               # the current version goes through, +1
        assert mine.row_version == 3
        a.commit()
    finally:
        a.close()
    assert editor.get(f"/api/spend/opex/{item['code']}").json()["rowVersion"] == 3


def test_api_stale_row_version_gets_409_and_versions_step_by_one(editor):
    item = editor.post("/api/spend/opex", json=_opex(assets=[])).json()
    body = {**_opex(assets=[]), "title": "Sửa lần 1", "rowVersion": item["rowVersion"]}
    assert editor.put(f"/api/spend/opex/{item['code']}", json=body).json()["rowVersion"] == 2
    stale = editor.put(f"/api/spend/opex/{item['code']}", json={**body, "title": "Sửa đè"})   # still rowVersion 1
    assert stale.status_code == 409
    got = editor.get(f"/api/spend/opex/{item['code']}").json()
    assert (got["title"], got["rowVersion"]) == ("Sửa lần 1", 2)


# ---------------------------------------------------------------- DAT-11
def test_concurrent_import_commits_create_each_opex_once(app_, admin):
    """Two "Ghi" of the same file at once. The first holds its transaction open after inserting;
    the second must wait for it and then find every row a duplicate."""
    data = _xlsx(OPEX_FILE)
    inserted, release = threading.Event(), threading.Event()
    original = importer._opex_commit
    results, errors = {}, []

    def slow_commit(db, rows, *a, **kw):
        out = original(db, rows, *a, **kw)
        if threading.current_thread().name == "first":
            inserted.set()
            release.wait(10)
        return out

    def run(name):
        db = SessionLocal()
        try:
            user = db.query(User).filter_by(username="editor1").one()
            results[name] = importer.commit(db, "opex", data, spend_settings.load(db), user, None, True)["created"]
            db.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    importer._opex_commit = slow_commit
    try:
        t1 = threading.Thread(target=run, args=("first",), name="first")
        t1.start()
        assert inserted.wait(10)
        t2 = threading.Thread(target=run, args=("second",), name="second")
        t2.start()
        t2.join(0.5)                       # give the second commit time to reach the import lock
        release.set()
        t1.join(10)
        t2.join(10)
    finally:
        importer._opex_commit = original
        release.set()
    assert not errors, errors
    assert len(results["first"]) == 4 and results["second"] == []
    db = SessionLocal()
    try:
        assert db.query(OpexItem).count() == 4
        assert db.query(OpexItem).filter_by(title="Bảo hành Dell R750").count() == 2      # two terms, not four
    finally:
        db.close()
