"""DAT-03: /api/dashboard/trend sums one snapshot per (day, vCenter, cluster), from successful runs only."""
import datetime as dt

from app.database import SessionLocal
from app.models import ClusterAggregate, SnapshotRun
from app.timeutil import to_vn_date

from test_spend_opex import admin, app_  # noqa: F401  (fixtures)


def _run(db, at, status, aggs):
    run = SnapshotRun(vcenter_key="trend-t1", source="live", status=status, started_at=at, finished_at=at)
    db.add(run)
    db.flush()
    for cluster, used_vcpu, logical in aggs:
        db.add(ClusterAggregate(run_id=run.id, vcenter_key="trend-t1", cluster=cluster, captured_at=at,
                                used_vcpu=used_vcpu, total_logical_cores=logical, used_ram_gb=10, total_ram_gb=100))


def test_trend_keeps_last_successful_poll_per_day(app_, admin):
    # A day 200 days ago (VN noon) holds only this test's rows: other tests write "now".
    day = (dt.datetime.utcnow() - dt.timedelta(days=200)).replace(hour=3, minute=0, second=0, microsecond=0)
    db = SessionLocal()
    _run(db, day, "ok", [("C1", 100, 400), ("C2", 50, 200)])                         # 10:00 VN
    _run(db, day + dt.timedelta(hours=2), "ok", [("C1", 120, 400)])                  # 12:00 VN, C1 only
    _run(db, day + dt.timedelta(hours=4), "error", [("C1", 999, 999)])               # failed run: ignored
    db.commit()
    db.close()
    trend = admin.get("/api/dashboard/trend?days=365").json()["trend"]
    point = next(p for p in trend if p["date"] == to_vn_date(day).isoformat())
    # C1 = last ok poll (120), C2 = its only poll (50); old code summed every row: 100+50+120+999.
    assert point["usedVcpu"] == 170
    assert point["totalLogicalCores"] == 600
    assert point["usedRamGB"] == 20 and point["totalRamGB"] == 200
