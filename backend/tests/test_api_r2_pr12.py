"""Audit 16/11 v2: API-05 (paged lists + X-Total-Count), API-02 (Executive aggregate), BEQ-01 (readiness)."""
import datetime as dt
import time

import pytest
from fastapi.testclient import TestClient

from app.collectors import scheduler
from app.config import VCenterConfig, get_settings
from app.database import SessionLocal
from app.models import BackupJob, SnapshotRun
from app.spend.models import Contract, ContractPayment, OpexItem, QuoteRequest, SpendVendor

from test_spend_opex import _login, _opex, admin, app_  # noqa: F401  (fixtures)


# ---------------------------------------------------------------------------
# API-05: paging
# ---------------------------------------------------------------------------
def _contracts(app, n):
    editor = _login(app, "editor1")
    opex_code = editor.post("/api/spend/opex", json=_opex(assets=[])).json()["code"]
    db = SessionLocal()
    opex_id = db.query(OpexItem.id).filter_by(code=opex_code).scalar()
    rows = [Contract(code=f"HD-PG-{i:03d}", title=f"Hợp đồng {i}", status="draft", current_version_no=0, created_by="t",
                     opex_id=opex_id) for i in range(n)]
    db.add_all(rows)
    db.flush()
    ids = [c.id for c in rows]
    db.commit()
    db.close()
    return ids


def test_contracts_paged_with_total_header(app_):
    ids = _contracts(app_, 7)
    viewer = _login(app_, "viewer1")
    r = viewer.get("/api/spend/contracts")
    assert r.status_code == 200 and isinstance(r.json(), list)           # same shape as before
    assert r.headers["X-Total-Count"] == "7" and r.headers["X-Has-More"] == "false"
    assert [c["code"] for c in r.json()] == [f"HD-PG-{i:03d}" for i in range(6, -1, -1)]   # newest (highest id) first
    r = viewer.get("/api/spend/contracts?offset=2&limit=3")
    assert [c["code"] for c in r.json()] == ["HD-PG-004", "HD-PG-003", "HD-PG-002"]
    assert r.headers["X-Total-Count"] == "7" and r.headers["X-Has-More"] == "true"
    r = viewer.get("/api/spend/contracts?offset=5&limit=3")
    assert [c["code"] for c in r.json()] == ["HD-PG-001", "HD-PG-000"] and r.headers["X-Has-More"] == "false"
    # Filters narrow the total too.
    r = viewer.get("/api/spend/contracts?q=HD-PG-00")
    assert r.headers["X-Total-Count"] == "7"
    r = viewer.get("/api/spend/contracts?q=HD-PG-006")
    assert r.headers["X-Total-Count"] == "1" and len(r.json()) == 1
    assert viewer.get("/api/spend/contracts?limit=2001").status_code == 422
    assert viewer.get("/api/spend/contracts?offset=-1").status_code == 422
    assert len(ids) == 7


def test_payments_paged_and_open_total_over_whole_set(app_):
    cid = _contracts(app_, 1)[0]
    db = SessionLocal()
    base = dt.date(2027, 1, 1)
    # 5 open instalments (1, 2, 3, 4, 5 triệu), due on consecutive days, and 1 paid (9 triệu).
    for i in range(5):
        db.add(ContractPayment(contract_id=cid, seq=i + 1, title=f"Đợt {i + 1}", amount=(i + 1) * 1_000_000,
                               due_date=base + dt.timedelta(days=i), status="pending", created_by="t"))
    db.add(ContractPayment(contract_id=cid, seq=6, title="Đợt 6", amount=9_000_000, due_date=base, status="paid", paid_at=base, paid_amount=9_000_000, created_by="t"))
    db.commit()
    db.close()
    viewer = _login(app_, "viewer1")
    r = viewer.get("/api/spend/payments?status=open&limit=2")
    d = r.json()
    assert [p["seq"] for p in d["items"]] == [1, 2]
    assert r.headers["X-Total-Count"] == "5" and r.headers["X-Has-More"] == "true"
    assert d["openTotal"] == 15_000_000                       # 1+2+3+4+5 triệu, not only the 2 returned
    r = viewer.get("/api/spend/payments?status=open&offset=4&limit=2")
    assert [p["seq"] for p in r.json()["items"]] == [5] and r.headers["X-Has-More"] == "false"
    r = viewer.get("/api/spend/payments?status=all")
    assert r.headers["X-Total-Count"] == "6" and r.json()["openTotal"] == 15_000_000
    # Same due date (seq 1 open, seq 6 paid): ordered by seq within the contract.
    assert [p["seq"] for p in r.json()["items"]] == [1, 6, 2, 3, 4, 5]
    r = viewer.get("/api/spend/payments?status=paid")
    assert r.headers["X-Total-Count"] == "1" and r.json()["openTotal"] == 0


def test_quotes_and_vendor_profiles_paged(app_):
    db = SessionLocal()
    for i in range(4):
        db.add(QuoteRequest(code=f"BG-PG-{i}", title=f"Gói {i}", workstream="other", min_quotes=3, status="draft", created_by="t"))
    for name in ("Zeta", "Alpha", "Mu"):
        db.add(SpendVendor(name=f"{name} JSC", name_norm=f"{name.lower()} jsc", active=True))
    db.commit()
    db.close()
    viewer = _login(app_, "viewer1")
    r = viewer.get("/api/spend/quotes?limit=3")
    assert [q["code"] for q in r.json()] == ["BG-PG-3", "BG-PG-2", "BG-PG-1"]
    assert r.headers["X-Total-Count"] == "4" and r.headers["X-Has-More"] == "true"
    r = viewer.get("/api/spend/vendor-profiles")
    assert [v["name"] for v in r.json()] == ["Alpha JSC", "Mu JSC", "Zeta JSC"] and r.headers["X-Total-Count"] == "3"
    r = viewer.get("/api/spend/vendor-profiles?offset=1&limit=1")
    assert [v["name"] for v in r.json()] == ["Mu JSC"] and r.headers["X-Has-More"] == "true"
    assert viewer.get("/api/spend/vendor-profiles?limit=5001").status_code == 422


def test_backup_jobs_offset_and_total_header(app_, admin):  # noqa: F811
    db = SessionLocal()
    db.query(BackupJob).filter(BackupJob.nbu_key == "page-t1").delete()
    now = dt.datetime.utcnow().replace(microsecond=0)
    # 6 jobs; jobs 4 and 5 share a start time -> the id tiebreak (newest id first) decides.
    for i in range(6):
        db.add(BackupJob(nbu_key="page-t1", job_id=1000 + i, category="backup", status=0, state="DONE",
                         client_name=f"client-{i}", start_time=now - dt.timedelta(minutes=10 - min(i, 4))))
    db.commit()
    db.close()
    try:
        r = admin.get("/api/backup/jobs?server=page-t1&days=1&limit=4")
        d = r.json()
        assert d["total"] == 6 and r.headers["X-Total-Count"] == "6" and r.headers["X-Has-More"] == "true"
        assert [j["jobId"] for j in d["rows"]] == [1005, 1004, 1003, 1002]
        r = admin.get("/api/backup/jobs?server=page-t1&days=1&offset=4&limit=4")
        assert [j["jobId"] for j in r.json()["rows"]] == [1001, 1000] and r.headers["X-Has-More"] == "false"
        assert r.json()["total"] == 6
    finally:
        db = SessionLocal()
        db.query(BackupJob).filter(BackupJob.nbu_key == "page-t1").delete()
        db.commit()
        db.close()


# ---------------------------------------------------------------------------
# API-02: one request for the Executive page, same numbers as the single endpoints
# ---------------------------------------------------------------------------
@pytest.fixture()
def demo_vcenter(app_):  # noqa: F811
    db = SessionLocal()
    cfg = VCenterConfig(key="exec-demo", name="Exec demo", host="demo.local", port=443, username="u", password="p",
                        verify_ssl=False, demo=True)
    run = scheduler.sync_vcenter(db, cfg, get_settings())
    assert run.status == "ok"
    run_id = run.id
    db.close()
    yield "exec-demo"
    # Leave no extra vCenter behind for other tests.
    from app.collectors.retention import CHILD_MODELS
    db = SessionLocal()
    for model in CHILD_MODELS:
        db.query(model).filter(model.run_id == run_id).delete(synchronize_session=False)
    db.query(SnapshotRun).filter(SnapshotRun.id == run_id).delete(synchronize_session=False)
    db.commit()
    db.close()


@pytest.mark.parametrize("vcenter", ["all", "exec-demo", "import"])
def test_executive_aggregate_equals_single_endpoints(admin, demo_vcenter, vcenter):  # noqa: F811
    agg = admin.get(f"/api/dashboard/executive?vcenter={vcenter}&storage=0&months=6&group=cluster&days=90")
    assert agg.status_code == 200
    a = agg.json()
    assert set(a) == {"trend", "byVcenter", "brief", "history", "vmChanges"}
    brief_vc = "all" if vcenter == "import" else vcenter
    assert a["trend"] == admin.get("/api/dashboard/trend?days=90").json()
    assert a["byVcenter"] == admin.get("/api/data/latest?group_by_vcenter=true").json()["byVcenter"]
    assert a["brief"] == admin.get(f"/api/reports/brief?vcenter={brief_vc}&storage=0").json()
    # history / vm-changes stamp their window with "now" (since / until): compare everything else.
    def body(d):
        return {k: v for k, v in d.items() if k not in ("since", "until")}
    single = admin.get(f"/api/dashboard/history?vcenter={vcenter}&months=6&group=cluster").json()
    assert body(a["history"]) == body(single) and a["history"]["since"][:10] == single["since"][:10]
    single = admin.get(f"/api/dashboard/vm-changes?vcenter={vcenter}&months=6").json()
    assert body(a["vmChanges"]) == body(single)
    if vcenter != "import":
        assert a["brief"]["available"] is True and demo_vcenter in a["byVcenter"]
        assert len(a["byVcenter"][demo_vcenter]["hosts"]) > 0


def test_executive_aggregate_requires_login(app_):  # noqa: F811
    assert TestClient(app_).get("/api/dashboard/executive").status_code == 401


# ---------------------------------------------------------------------------
# BEQ-01: scheduler in readiness is configurable
# ---------------------------------------------------------------------------
def _stale(s):
    return {"at": time.monotonic() - (3 * 300 + 2 * scheduler.deadline_seconds(s) + 121), "interval": 300, "rounds": 1}


def test_ready_gates_on_stale_scheduler_by_default(app_, monkeypatch):  # noqa: F811
    s = get_settings()
    monkeypatch.setattr(s, "enable_scheduler", True)
    monkeypatch.setattr(s, "readiness_require_scheduler", True)
    monkeypatch.setattr(scheduler, "_heartbeat", _stale(s))
    r = TestClient(app_).get("/api/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["scheduler"]["reason"] == "scheduler_stale" and r.json()["checks"]["scheduler"]["required"] is True


def test_ready_ignores_scheduler_when_not_required(app_, monkeypatch):  # noqa: F811
    s = get_settings()
    monkeypatch.setattr(s, "enable_scheduler", True)
    monkeypatch.setattr(s, "readiness_require_scheduler", False)
    monkeypatch.setattr(scheduler, "_heartbeat", _stale(s))
    r = TestClient(app_).get("/api/health/ready")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ok"
    # Still reported, so monitoring sees it.
    assert body["checks"]["scheduler"]["ok"] is False and body["checks"]["scheduler"]["reason"] == "scheduler_stale"
    assert body["checks"]["scheduler"]["required"] is False
    # Database still gates.
    from sqlalchemy.exc import OperationalError
    from app import main

    def down():
        raise OperationalError("SELECT 1", {}, Exception("down"))
    monkeypatch.setattr(main.engine, "connect", down)
    assert TestClient(app_).get("/api/health/ready").status_code == 503
