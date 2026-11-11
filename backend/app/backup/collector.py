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


def _upsert_jobs(db: Session, key: str, jobs: List[Dict[str, Any]]) -> int:
    if not jobs:
        return 0
    by_id = {j["jobId"]: j for j in jobs}
    ids = list(by_id)
    existing: Dict[int, BackupJob] = {}
    for i in range(0, len(ids), 500):
        for row in db.query(BackupJob).filter(BackupJob.nbu_key == key, BackupJob.job_id.in_(ids[i:i + 500])).all():
            existing[row.job_id] = row
    for jid, j in by_id.items():
        row = existing.get(jid) or BackupJob(nbu_key=key, job_id=jid)
        row.parent_job_id = j.get("parentJobId")
        row.job_type = j.get("jobType")
        row.category = j.get("category") or "other"
        row.policy_name = j.get("policyName")
        row.policy_type = j.get("policyType")
        row.schedule_name = j.get("scheduleName")
        row.schedule_type = j.get("scheduleType")
        row.client_name = j.get("clientName")
        row.client_key = nbu_client.client_key(j.get("clientName"))
        row.state = j.get("state")
        row.status = j.get("status")
        row.start_time = j.get("startTime")
        row.end_time = j.get("endTime")
        row.elapsed_sec = j.get("elapsedSec")
        row.kbytes = j.get("kbytes")
        row.files = j.get("files")
        row.storage_unit = j.get("storageUnit")
        row.media_server = j.get("mediaServer")
        row.updated_at = dt.datetime.utcnow()
        if row.id is None:
            db.add(row)
    return len(by_id)


def _store_snapshot(db: Session, run: BackupSyncRun, key: str, policies: List[Dict[str, Any]], pools: List[Dict[str, Any]],
                    at: dt.datetime) -> None:
    for p in policies:
        db.add(BackupPolicySnapshot(
            run_id=run.id, nbu_key=key, name=p["name"], policy_type=p.get("policyType"), active=p.get("active"),
            clients_json=json.dumps(p.get("clients") or []), schedules_json=json.dumps(p.get("schedules") or []),
            storage=p.get("storage"), selection=p.get("selection"), captured_at=at))
    for s in pools:
        db.add(BackupStorageSnapshot(
            run_id=run.id, nbu_key=key, name=s["name"], storage_type=s.get("storageType"), total_bytes=s.get("totalBytes"),
            used_bytes=s.get("usedBytes"), free_bytes=s.get("freeBytes"), high_water_pct=s.get("highWaterPct"),
            state=s.get("state"), captured_at=at))


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
