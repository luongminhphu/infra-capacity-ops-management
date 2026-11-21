"""Snapshot history retention (36 months) and the composite history indexes."""
import datetime as dt

import pytest
from sqlalchemy import inspect

from app.collectors import retention
from app.database import SessionLocal, engine, ensure_schema
from app.models import ClusterAggregate, ComplianceFindingSnapshot, HostSnapshot, SnapshotRun, VmSnapshot

NOW = dt.datetime(2026, 11, 15, 3, 0)


@pytest.fixture(autouse=True, scope="module")
def _schema():
    ensure_schema()


def _run(db, key, started, status="ok", source="live", vms=2):
    run = SnapshotRun(vcenter_key=key, source=source, status=status, started_at=started,
                      finished_at=started + dt.timedelta(minutes=5))
    db.add(run)
    db.flush()
    common = dict(run_id=run.id, vcenter_key=key, captured_at=started)
    db.add(HostSnapshot(**common, cluster="C1", host=f"esx-{run.id}"))
    for i in range(vms):
        db.add(VmSnapshot(**common, cluster="C1", vm=f"vm{i}"))
    db.add(ClusterAggregate(**common, cluster="C1"))
    db.add(ComplianceFindingSnapshot(**common, severity="warning", type="idle"))
    db.commit()
    return run.id


def _exists(db, rid):
    return db.get(SnapshotRun, rid) is not None


def test_months_ago_calendar():
    assert retention.months_ago(NOW, 36) == dt.datetime(2023, 11, 15, 3, 0)
    assert retention.months_ago(dt.datetime(2026, 3, 31), 1) == dt.datetime(2026, 2, 28)
    assert retention.months_ago(dt.datetime(2026, 1, 10), 13) == dt.datetime(2024, 12, 10)


def test_purge_deletes_older_than_36_months_keeps_newer_and_latest():
    db = SessionLocal()
    try:
        a, b = "vc-ret-a", "vc-ret-b"
        old_a = [_run(db, a, dt.datetime(2022, 1, 1) + dt.timedelta(days=d)) for d in range(0, 50, 2)]  # 25 runs
        old_err = _run(db, a, dt.datetime(2023, 11, 14), status="error", vms=0)    # 1 day before cutoff
        new_a = _run(db, a, dt.datetime(2023, 11, 16))                             # 1 day after cutoff
        latest_a = _run(db, a, dt.datetime(2026, 11, 14))
        # vCenter b stopped polling long ago: its last successful run must survive.
        old_b = _run(db, b, dt.datetime(2021, 6, 1))
        last_b = _run(db, b, dt.datetime(2022, 6, 1))
        last_b_err = _run(db, b, dt.datetime(2022, 7, 1), status="error", vms=0)

        counts = retention.purge_snapshots(db, 36, now=NOW, chunk=7)   # several chunks
        db.expire_all()

        assert not any(_exists(db, r) for r in old_a)
        assert not _exists(db, old_err) and not _exists(db, old_b) and not _exists(db, last_b_err)
        assert _exists(db, new_a) and _exists(db, latest_a) and _exists(db, last_b)
        assert counts["snapshot_runs"] == 25 + 1 + 1 + 1
        assert counts["vm_snapshots"] == 25 * 2 + 2          # old_a + old_b; error runs have no VM rows
        assert counts["host_snapshots"] == 28
        assert counts["cluster_aggregates"] == 28 and counts["compliance_findings"] == 28
        deleted = old_a + [old_err, old_b, last_b_err]
        for model in retention.CHILD_MODELS:
            assert db.query(model).filter(model.run_id.in_(deleted)).count() == 0
        assert db.query(VmSnapshot).filter(VmSnapshot.run_id == last_b).count() == 2

        # Idempotent; 0 months disables.
        assert retention.purge_snapshots(db, 36, now=NOW)["snapshot_runs"] == 0
        assert retention.purge_snapshots(db, 0, now=dt.datetime(2040, 1, 1))["snapshot_runs"] == 0
        assert _exists(db, new_a)
    finally:
        db.close()


def test_maybe_purge_runs_once_a_day(monkeypatch):
    calls = []
    monkeypatch.setattr(retention, "_last_purge", None)
    monkeypatch.setattr(retention, "purge_snapshots", lambda db, months, now: calls.append(now) or {"snapshot_runs": 0})
    assert retention.maybe_purge(36, NOW) is not None
    assert retention.maybe_purge(36, NOW + dt.timedelta(hours=23)) is None
    assert retention.maybe_purge(36, NOW + dt.timedelta(hours=25)) is not None
    off = retention.maybe_purge(0, NOW + dt.timedelta(days=5))       # snapshots kept; alerts / events keep their retention
    assert off is not None and "snapshot_runs" not in off and {"hw_alert", "ops_event"} <= set(off)
    assert len(calls) == 2


def test_composite_history_indexes():
    insp = inspect(engine)
    vm = {i["name"]: i["column_names"] for i in insp.get_indexes("vm_snapshots")}
    runs = {i["name"]: i["column_names"] for i in insp.get_indexes("snapshot_runs")}
    assert vm["ix_vm_snapshots_vc_captured"] == ["vcenter_key", "captured_at"]
    assert runs["ix_snapshot_runs_latest"] == ["vcenter_key", "status", "source", "finished_at"]
    ensure_schema()   # idempotent



def test_purge_time_budget_leaves_the_rest_for_next_pass():
    """SCH-06: a pass stops after its time budget and says there is more."""
    db = SessionLocal()
    try:
        counts = retention.purge_snapshots(db, 36, now=NOW, budget_s=-1, pause_s=0)
        assert counts["more"] == 1 and counts["snapshot_runs"] == 0
    finally:
        db.close()


def test_ensure_schema_restores_model_indexes_on_old_tables():
    """SCH-04 / SCH-09: an index declared on the model but missing in an existing database is created."""
    from sqlalchemy import inspect, text
    from app.database import engine, ensure_schema
    ensure_schema()
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_contracts_status"))
        conn.execute(text("DROP INDEX IF EXISTS ix_contract_payment_status_due"))
    assert "ix_contracts_status" not in {i["name"] for i in inspect(engine).get_indexes("contracts")}
    ensure_schema()
    insp = inspect(engine)
    assert "ix_contracts_status" in {i["name"] for i in insp.get_indexes("contracts")}
    assert "ix_contract_payment_status_due" in {i["name"] for i in insp.get_indexes("contract_payments")}
    assert "ix_snapshot_runs_started_at" in {i["name"] for i in insp.get_indexes("snapshot_runs")}
