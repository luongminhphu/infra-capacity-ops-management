"""Sync one NetBackup primary server into the database.

* Jobs are upserted by (server key, jobId). Each poll re-reads the window from
  the previous poll minus an overlap (and from the oldest job still running),
  so a job first seen ACTIVE gets its final status on a later poll.
* Policies and disk pools are stored as a snapshot of the run — enough to show
  current configuration and the storage-usage trend / days-to-full forecast.
* A failed poll is still recorded (status='error') so the UI shows the reason.
"""
import datetime as dt
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import insert
from sqlalchemy.orm import Session

from .. import data_access
from ..config import NetBackupConfig, Settings, get_settings
from ..models import BackupJob, BackupPolicySnapshot, BackupStorageSnapshot, BackupSyncRun
from . import client as nbu_client
from . import demo_generator

logger = logging.getLogger("vco.netbackup")

OVERLAP = dt.timedelta(hours=48)
MAX_RUNNING_LOOKBACK = dt.timedelta(days=7)
POLICY_SNAPSHOT_KEEP = dt.timedelta(days=14)


def _since(db: Session, cfg: NetBackupConfig, settings: Settings, now: dt.datetime) -> dt.datetime:
    last = (db.query(BackupSyncRun).filter(BackupSyncRun.nbu_key == cfg.key, BackupSyncRun.status == "ok",
                                           BackupSyncRun.jobs_fetched.isnot(None), BackupSyncRun.finished_at.isnot(None))
            .order_by(BackupSyncRun.started_at.desc()).first())
    if not last or not db.query(BackupJob.id).filter(BackupJob.nbu_key == cfg.key).first():
        return now - dt.timedelta(days=max(1, settings.backup_initial_days))
    since = last.started_at - OVERLAP
    oldest_running = (db.query(BackupJob.start_time).filter(BackupJob.nbu_key == cfg.key, BackupJob.status.is_(None),
                                                            BackupJob.start_time >= now - MAX_RUNNING_LOOKBACK)
                      .order_by(BackupJob.start_time.asc()).first())
    if oldest_running and oldest_running[0] and oldest_running[0] < since:
        since = oldest_running[0] - dt.timedelta(minutes=5)
    return since


def _known_vm_names(db: Session, settings: Settings) -> List[str]:
    _, vms, _ = data_access.get_latest_combined(db, include_import=True)
    return [v["vm"] for v in vms if v.get("vm")]


def _job_columns(j: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "parent_job_id": j.get("parentJobId"), "job_type": j.get("jobType"), "category": j.get("category") or "other",
        "policy_name": j.get("policyName"), "policy_type": j.get("policyType"), "schedule_name": j.get("scheduleName"),
        "schedule_type": j.get("scheduleType"), "client_name": j.get("clientName"),
        "client_key": nbu_client.client_key(j.get("clientName")), "state": j.get("state"), "status": j.get("status"),
        "start_time": j.get("startTime"), "end_time": j.get("endTime"), "elapsed_sec": j.get("elapsedSec"),
        "kbytes": j.get("kbytes"), "files": j.get("files"), "storage_unit": j.get("storageUnit"),
        "media_server": j.get("mediaServer"),
    }


def _upsert_jobs(db: Session, key: str, jobs: List[Dict[str, Any]]) -> int:
    """Insert new jobs in bulk; update existing ones only when something changed (the
    overlap window re-reads thousands of finished, unchanged jobs on every poll)."""
    if not jobs:
        return 0
    by_id = {j["jobId"]: _job_columns(j) for j in jobs}
    ids = list(by_id)
    existing: Dict[int, BackupJob] = {}
    for i in range(0, len(ids), 500):
        for row in db.query(BackupJob).filter(BackupJob.nbu_key == key, BackupJob.job_id.in_(ids[i:i + 500])).all():
            existing[row.job_id] = row
    now = dt.datetime.utcnow()
    new_rows = []
    for jid, cols in by_id.items():
        row = existing.get(jid)
        if row is None:
            new_rows.append({"nbu_key": key, "job_id": jid, "updated_at": now, **cols})
            continue
        changed = {k: v for k, v in cols.items() if getattr(row, k) != v}
        if changed:
            for k, v in changed.items():
                setattr(row, k, v)
            row.updated_at = now
    if new_rows:
        db.execute(insert(BackupJob), new_rows)
    return len(by_id)


def _store_snapshot(db: Session, run: BackupSyncRun, key: str, policies: List[Dict[str, Any]], pools: List[Dict[str, Any]],
                    at: dt.datetime) -> None:
    if policies:
        db.execute(insert(BackupPolicySnapshot), [{
            "run_id": run.id, "nbu_key": key, "name": p["name"], "policy_type": p.get("policyType"), "active": p.get("active"),
            "clients_json": json.dumps(p.get("clients") or []), "schedules_json": json.dumps(p.get("schedules") or []),
            "storage": p.get("storage"), "selection": p.get("selection"), "captured_at": at} for p in policies])
    if pools:
        db.execute(insert(BackupStorageSnapshot), [{
            "run_id": run.id, "nbu_key": key, "name": s["name"], "storage_type": s.get("storageType"),
            "total_bytes": s.get("totalBytes"), "used_bytes": s.get("usedBytes"), "free_bytes": s.get("freeBytes"),
            "high_water_pct": s.get("highWaterPct"), "state": s.get("state"), "captured_at": at} for s in pools])


def _demo_backfill_storage(db: Session, cfg: NetBackupConfig, now: dt.datetime, days: int = 45) -> None:
    """First demo sync: one storage-only run per past day so the trend / forecast
    has history immediately (a real server accumulates it through polling)."""
    if db.query(BackupSyncRun.id).filter(BackupSyncRun.nbu_key == cfg.key).count() > 1:
        return
    for d in range(days, 0, -1):
        at = now - dt.timedelta(days=d)
        r = BackupSyncRun(nbu_key=cfg.key, source="demo", status="ok", started_at=at, finished_at=at, jobs_fetched=None)
        db.add(r)
        db.flush()
        pools = [p for p in (nbu_client.parse_disk_pool(x) for x in demo_generator.pools_payload(cfg, at)) if p]
        _store_snapshot(db, r, cfg.key, [], pools, at)
        r.pool_count = len(pools)


def _purge(db: Session, cfg: NetBackupConfig, settings: Settings, now: dt.datetime) -> None:
    cutoff = now - dt.timedelta(days=max(30, settings.backup_retention_days))
    db.query(BackupJob).filter(BackupJob.nbu_key == cfg.key, BackupJob.start_time < cutoff).delete(synchronize_session=False)
    old_runs = [r[0] for r in db.query(BackupSyncRun.id).filter(BackupSyncRun.nbu_key == cfg.key,
                                                                 BackupSyncRun.started_at < now - POLICY_SNAPSHOT_KEEP).all()]
    if old_runs:
        db.query(BackupPolicySnapshot).filter(BackupPolicySnapshot.run_id.in_(old_runs)).delete(synchronize_session=False)


def sync_netbackup(db: Session, cfg: NetBackupConfig, settings: Optional[Settings] = None,
                   now: Optional[dt.datetime] = None) -> BackupSyncRun:
    """Poll one primary server; raises client.NetBackupError on failure (the run
    is still persisted with status='error')."""
    settings = settings or get_settings()
    now = now or dt.datetime.utcnow()
    run = BackupSyncRun(nbu_key=cfg.key, source="demo" if cfg.demo else "live", status="ok", started_at=now)
    db.add(run)
    db.flush()
    try:
        since = _since(db, cfg, settings, now)
        if cfg.demo:
            vm_names = _known_vm_names(db, settings)
            jobs = [j for j in (nbu_client.parse_job(x) for x in demo_generator.jobs_payload(cfg, since, vm_names, now)) if j]
            policies = [p for p in (nbu_client.parse_policy(x) for x in demo_generator.policies_payload(cfg, vm_names)) if p]
            pools = [p for p in (nbu_client.parse_disk_pool(x) for x in demo_generator.pools_payload(cfg, now)) if p]
            warnings: List[str] = []
            _demo_backfill_storage(db, cfg, now)
        else:
            jobs, policies, pools, warnings = nbu_client.collect(cfg, since)
        run.jobs_fetched = _upsert_jobs(db, cfg.key, jobs)
        _store_snapshot(db, run, cfg.key, policies, pools, now)
        run.policy_count, run.pool_count = len(policies), len(pools)
        run.error_message = "; ".join(warnings) or None
        run.status = "ok"
        run.finished_at = dt.datetime.utcnow()
        _purge(db, cfg, settings, now)
        db.commit()
        logger.info("Synced NetBackup %s: %d jobs, %d policies, %d pools", cfg.key, run.jobs_fetched, len(policies), len(pools))
        return run
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        run = BackupSyncRun(nbu_key=cfg.key, source="demo" if cfg.demo else "live", status="error",
                            error_message=str(exc), started_at=now, finished_at=dt.datetime.utcnow())
        db.add(run)
        db.commit()
        logger.exception("NetBackup sync failed for %s", cfg.key)
        raise
