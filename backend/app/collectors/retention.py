"""Retention for the vCenter snapshot history (snapshot_runs and its four child tables).

Every poll appends a full inventory (hosts, VMs, cluster aggregates, findings), so without a
purge the history tables grow forever (audit PERF-01 / QRY-01). Policy:

* Runs that started more than SNAPSHOT_RETENTION_MONTHS calendar months ago are deleted
  (0 disables the purge).
* The most recent successful run of each (vcenter_key, source) is never deleted, even when it
  is older than the cutoff: it is what the dashboards show for a vCenter that stopped polling.
* Deletion goes by run-id chunks, children first, one commit per chunk, so the first purge of a
  large database never holds long locks or one huge transaction.
* Runs at most once a day, called from the scheduler loop (single replica deployment).
"""
import datetime as dt
import logging
import threading
from typing import Dict, List, Optional

from sqlalchemy import delete, func
from sqlalchemy.orm import Session

from ..models import ClusterAggregate, ComplianceFindingSnapshot, HostSnapshot, SnapshotRun, VmSnapshot

logger = logging.getLogger("vco.retention")

# ~3 000 VMs per run → about 60 000 child rows per chunk: short transactions on PostgreSQL.
RUN_CHUNK = 20
PURGE_EVERY = dt.timedelta(days=1)
CHILD_MODELS = (VmSnapshot, HostSnapshot, ClusterAggregate, ComplianceFindingSnapshot)

_last_purge: Optional[dt.datetime] = None
_purge_lock = threading.Lock()


def months_ago(now: dt.datetime, months: int) -> dt.datetime:
    """Same day-of-month `months` calendar months earlier (clamped to the month's last day)."""
    y, m = divmod(now.year * 12 + (now.month - 1) - months, 12)
    m += 1
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    last_day = (nxt - dt.timedelta(days=1)).day
    return now.replace(year=y, month=m, day=min(now.day, last_day))


def _protected_run_ids(db: Session) -> List[int]:
    """Latest successful run per (vcenter_key, source) — one grouped query."""
    return [rid for (rid,) in db.query(func.max(SnapshotRun.id)).filter(SnapshotRun.status == "ok")
            .group_by(SnapshotRun.vcenter_key, SnapshotRun.source).all() if rid is not None]


def purge_snapshots(db: Session, months: int, now: Optional[dt.datetime] = None,
                    chunk: int = RUN_CHUNK) -> Dict[str, int]:
    """Delete snapshot runs (and their rows) older than `months`; return rows deleted per table."""
    counts = {m.__tablename__: 0 for m in CHILD_MODELS}
    counts[SnapshotRun.__tablename__] = 0
    if months <= 0:
        return counts
    cutoff = months_ago(now or dt.datetime.utcnow(), months)
    keep = _protected_run_ids(db)
    while True:
        q = db.query(SnapshotRun.id).filter(SnapshotRun.started_at < cutoff)
        if keep:
            q = q.filter(SnapshotRun.id.notin_(keep))
        ids = [rid for (rid,) in q.order_by(SnapshotRun.id).limit(chunk).all()]
        if not ids:
            break
        for model in CHILD_MODELS:
            counts[model.__tablename__] += db.execute(
                delete(model).where(model.run_id.in_(ids)).execution_options(synchronize_session=False)).rowcount or 0
        counts[SnapshotRun.__tablename__] += db.execute(
            delete(SnapshotRun).where(SnapshotRun.id.in_(ids)).execution_options(synchronize_session=False)).rowcount or 0
        db.commit()
    if counts[SnapshotRun.__tablename__]:
        logger.info("Snapshot retention (%d months, cutoff %s): deleted %s", months, cutoff.date().isoformat(),
                    ", ".join(f"{t}={n}" for t, n in counts.items()))
    return counts


def maybe_purge(months: int, now: Optional[dt.datetime] = None) -> Optional[Dict[str, int]]:
    """Run purge_snapshots at most once per PURGE_EVERY in this process; never raises."""
    global _last_purge
    from ..database import SessionLocal
    now = now or dt.datetime.utcnow()
    if months <= 0 or not _purge_lock.acquire(blocking=False):
        return None
    try:
        if _last_purge is not None and now - _last_purge < PURGE_EVERY:
            return None
        with SessionLocal() as db:
            counts = purge_snapshots(db, months, now)
        _last_purge = now
        return counts
    except Exception:  # noqa: BLE001 — a failed purge must not stop polling; retried next cycle
        logger.exception("Snapshot retention purge failed; will retry next cycle")
        return None
    finally:
        _purge_lock.release()
