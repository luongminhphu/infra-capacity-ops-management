"""Polling: vCenter inventory snapshots and backup-server syncs.

Performance notes
* vCenter properties are fetched in bulk by vcenter/client.py (PropertyCollector).
* Idle / powered-off durations come from each VM's previous snapshot in ONE query
  (not one history query per VM).
* Snapshot rows are written with bulk INSERTs.
* Sources are polled concurrently (SYNC_CONCURRENCY threads, one DB session each).
"""
import asyncio
import datetime as dt
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import insert
from sqlalchemy.orm import Session

from .. import capacity, data_access
from ..config import Settings, VCenterConfig, get_settings
from ..database import SessionLocal
from ..models import ClusterAggregate, ComplianceFindingSnapshot, HostSnapshot, SnapshotRun, VmSnapshot
from ..vcenter import client as vcenter_client
from ..vcenter import demo_generator

logger = logging.getLogger("vco.scheduler")

IDLE_CPU_PCT = 5.0
PREVIOUS_RUNS = 5        # look for a VM's previous snapshot in this many recent successful runs


def _enrich_activity(db: Session, vcenter_key: str, vm_rows: List[Dict[str, Any]],
                     now: Optional[dt.datetime] = None) -> None:
    """Fill lastActivityDays / powerOffDays from our own polling history (a single
    vCenter snapshot has no notion of "idle since").

    Each stored snapshot already holds the duration up to its own capture time, so the
    start of the current idle / off streak is `previous.captured_at - previous.days`
    when the VM was in the same state in its previous snapshot — one query for all VMs
    instead of walking every VM's history."""
    now = now or dt.datetime.utcnow()
    run_ids = [r for (r,) in db.query(SnapshotRun.id)
               .filter(SnapshotRun.vcenter_key == vcenter_key, SnapshotRun.source == "live",
                       SnapshotRun.status == "ok", SnapshotRun.finished_at.isnot(None))
               .order_by(SnapshotRun.started_at.desc()).limit(PREVIOUS_RUNS)]
    previous: Dict[str, Any] = {}
    if run_ids:
        rows = (db.query(VmSnapshot.vm, VmSnapshot.captured_at, VmSnapshot.is_powered_on, VmSnapshot.cpu_usage_pct_raw,
                         VmSnapshot.last_activity_days, VmSnapshot.power_off_days)
                .filter(VmSnapshot.run_id.in_(run_ids)).all())
        for r in rows:
            p = previous.get(r.vm)
            if p is None or r.captured_at > p.captured_at:
                previous[r.vm] = r

    def days_since(start: dt.datetime) -> float:
        return max(0.0, (now - start).total_seconds() / 86400.0)

    for v in vm_rows:
        p = previous.get(v.get("vm"))
        if v.get("isPoweredOn"):
            cpu = v.get("cpuUsagePctRaw")
            if cpu is not None and cpu >= IDLE_CPU_PCT:
                v["lastActivityDays"] = 0
            elif p is not None and p.is_powered_on and (p.cpu_usage_pct_raw or 0) < IDLE_CPU_PCT:
                v["lastActivityDays"] = days_since(p.captured_at - dt.timedelta(days=p.last_activity_days or 0))
            else:
                v["lastActivityDays"] = 0
        elif p is not None and not p.is_powered_on:
            v["powerOffDays"] = days_since(p.captured_at - dt.timedelta(days=p.power_off_days or 0))
        else:
            v["powerOffDays"] = 0


def _safe(v):
    return v if (v is not None and math.isfinite(v)) else None


def _int_or_none(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _host_inventory_cols(h: Dict[str, Any]) -> Dict[str, Any]:
    """Map camelCase collector/import row → HostSnapshot inventory columns."""
    return {
        "vendor": h.get("vendor"), "model": h.get("model"), "esxi_version": h.get("esxiVersion"),
        "esxi_build": h.get("esxiBuild"), "cpu_model": h.get("cpuModel"),
        "cpu_sockets": _int_or_none(h.get("cpuSockets")), "cpu_threads": _int_or_none(h.get("cpuThreads")),
        "cluster_ha": h.get("clusterHa"), "cluster_drs": h.get("clusterDrs"),
    }


def _vm_inventory_cols(v: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "guest_id": v.get("guestId"), "guest_full_name": v.get("guestFullName"),
        "tools_guest_full_name": v.get("toolsGuestFullName"), "tools_guest_family": v.get("toolsGuestFamily"),
        "tools_status": v.get("toolsStatus"), "guest_hostname": v.get("guestHostname"),
    }


def _bulk(db: Session, model, rows: List[Dict[str, Any]]) -> None:
    if rows:
        db.execute(insert(model), rows)


def sync_vcenter(db: Session, cfg: VCenterConfig, settings: Optional[Settings] = None) -> SnapshotRun:
    """Query one vCenter (or generate demo data), persist a full SnapshotRun and return it,
    with `host_count`, `vm_count`, `finding_count` set on the instance. Raises
    vcenter_client.VCenterConnectionError on connection failure (the run is still
    persisted with status='error' for visibility)."""
    settings = settings or get_settings()
    started_at = dt.datetime.utcnow()
    run = SnapshotRun(vcenter_key=cfg.key, source="live", status="ok", started_at=started_at)
    db.add(run)
    db.flush()

    try:
        if cfg.demo:
            host_rows, vm_rows = demo_generator.collect_inventory(cfg)
        else:
            host_rows, vm_rows = vcenter_client.collect_inventory(cfg)
        now = dt.datetime.utcnow()
        _enrich_activity(db, cfg.key, vm_rows, now)
        # Same policy as the live dashboard (env defaults + admin overrides in app_settings),
        # so stored aggregates / findings match what users saw when the snapshot was taken.
        policy = data_access.get_effective_policy(db, settings)
        common = {"run_id": run.id, "vcenter_key": cfg.key, "captured_at": now}

        _bulk(db, HostSnapshot, [{
            **common, "cluster": h["cluster"], "host": h["host"], "cores": h["cores"], "cpu_mhz": h.get("cpuMhz", 0),
            "ram_gb": h["ramGB"], "cpu_usage_pct_raw": h.get("cpuUsagePctRaw"), "ram_usage_pct_raw": h.get("ramUsagePctRaw"),
            "status": h.get("status", "connected"), **_host_inventory_cols(h),
        } for h in host_rows])
        _bulk(db, VmSnapshot, [{
            **common, "cluster": v["cluster"], "host": v.get("host"), "vm": v["vm"], "instance_uuid": v.get("instanceUuid"),
            "power_state": v["powerState"], "is_powered_on": v["isPoweredOn"], "ip_address": v.get("ipAddress"),
            "vcpu": v["vcpu"], "ram_gb": v["ramGB"], "disk_prov_gb": v["diskProvGB"], "disk_used_gb": v["diskUsedGB"],
            "datastore": v["datastore"], "cpu_usage_pct_raw": v.get("cpuUsagePctRaw"),
            "ram_usage_pct_raw": v.get("ramUsagePctRaw"), "last_activity_days": v.get("lastActivityDays"),
            "power_off_days": v.get("powerOffDays"), **_vm_inventory_cols(v),
        } for v in vm_rows])

        cluster_rows = capacity.build_cluster_rows(host_rows, vm_rows, policy)
        _bulk(db, ClusterAggregate, [{
            **common, "cluster": c["name"], "host_count": c["hosts"], "vm_count": c["vms"],
            "total_cores": c["model"]["totalCores"], "total_logical_cores": c["model"]["totalLogicalCores"],
            "total_ram_gb": c["model"]["totalRamGB"], "used_vcpu": c["model"]["usedVcpu"],
            "used_ram_gb": c["model"]["usedRamGB"], "prov_storage_gb": c["model"]["provStorageGB"],
            "used_storage_gb": c["model"]["usedStorageGB"], "cpu_overcommit": _safe(c["model"]["cpuOvercommit"]),
            "ram_overcommit": _safe(c["model"]["ramOvercommit"]), "cpu_pct": _safe(c["model"]["cpuPct"]),
            "ram_pct": _safe(c["model"]["ramPct"]),
        } for c in cluster_rows])

        findings = capacity.build_compliance_findings(host_rows, vm_rows, policy)
        _bulk(db, ComplianceFindingSnapshot, [{
            **common, "severity": f["severity"], "type": f["type"],
            "type_label": f.get("typeLabel"), "entity": f.get("entity"), "cluster": f.get("cluster"),
            "metric": f.get("metric"), "recommendation": f.get("recommendation"),
        } for f in findings])

        run.status = "ok"
        run.finished_at = dt.datetime.utcnow()
        db.commit()
        run.host_count, run.vm_count, run.finding_count = len(host_rows), len(vm_rows), len(findings)
        logger.info("Synced vCenter %s: %d hosts, %d vms, %d findings in %.1fs", cfg.key, len(host_rows), len(vm_rows),
                    len(findings), (run.finished_at - run.started_at).total_seconds())
        return run
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        # Record the failure as a NEW row (same pattern as backup/collector.py). Re-merging the
        # rolled-back `run` would reuse its flushed primary key, which SQLite hands out again
        # to a concurrent sync once this INSERT is rolled back — the error row then collides
        # with that run's id and is never saved.
        failed = SnapshotRun(vcenter_key=cfg.key, source="live", status="error", error_message=str(exc),
                             started_at=started_at, finished_at=dt.datetime.utcnow())
        db.add(failed)
        db.commit()
        logger.exception("Sync failed for vCenter %s", cfg.key)
        raise


# ---------------------------------------------------------------------------
# Running several sources at once
# ---------------------------------------------------------------------------
def vcenter_result(cfg: VCenterConfig, run: SnapshotRun) -> Dict[str, Any]:
    return {"vcenterKey": cfg.key, "ok": True, "runId": run.id, "hostCount": run.host_count, "vmCount": run.vm_count,
            "findingCount": run.finding_count, "finishedAt": run.finished_at}


def backup_result(cfg, run) -> Dict[str, Any]:
    return {"key": cfg.key, "ok": True, "jobs": run.jobs_fetched, "warning": run.error_message,
            "finishedAt": run.finished_at}


def _sync_vcenter_task(cfg: VCenterConfig, settings: Settings) -> Dict[str, Any]:
    with SessionLocal() as db:
        return vcenter_result(cfg, sync_vcenter(db, cfg, settings))


def _sync_backup_task(cfg, settings: Settings) -> Dict[str, Any]:
    from ..backup.collector import sync_netbackup
    with SessionLocal() as db:
        return backup_result(cfg, sync_netbackup(db, cfg, settings))


def run_syncs(tasks: List[Callable[[], Dict[str, Any]]], keys: List[Dict[str, Any]], workers: int) -> List[Dict[str, Any]]:
    """Run sync tasks in parallel threads; a failure becomes {"ok": False, "error": ...}."""
    if not tasks:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(tasks))), thread_name_prefix="sync") as pool:
        futures = [pool.submit(t) for t in tasks]
        out = []
        for key, fut in zip(keys, futures):
            try:
                out.append(fut.result())
            except Exception as exc:  # noqa: BLE001 — already logged and recorded as an error run
                out.append({**key, "ok": False, "error": str(exc)})
        return out


def sync_vcenters(settings: Settings, configs: Optional[List[VCenterConfig]] = None) -> List[Dict[str, Any]]:
    configs = settings.vcenters() if configs is None else configs
    return run_syncs([lambda c=c: _sync_vcenter_task(c, settings) for c in configs],
                     [{"vcenterKey": c.key} for c in configs], settings.sync_concurrency)


def sync_backups(settings: Settings, configs=None) -> List[Dict[str, Any]]:
    configs = settings.netbackups() if configs is None else configs
    return run_syncs([lambda c=c: _sync_backup_task(c, settings) for c in configs],
                     [{"key": c.key} for c in configs], settings.sync_concurrency)


def sync_everything(settings: Settings) -> List[Dict[str, Any]]:
    """All vCenters and backup servers concurrently (vCenters first in the queue: backup
    coverage reads the VM inventory)."""
    vcs, nbus = settings.vcenters(), settings.netbackups()
    tasks = [lambda c=c: _sync_vcenter_task(c, settings) for c in vcs] + \
            [lambda c=c: _sync_backup_task(c, settings) for c in nbus]
    keys = [{"vcenterKey": c.key} for c in vcs] + [{"key": c.key} for c in nbus]
    return run_syncs(tasks, keys, settings.sync_concurrency)


def _ops_alerts() -> None:
    """Discord alerts for capacity / backup at the 90 % level (docs/design/super-app.md decision 5)."""
    from .. import ops_alerts
    try:
        with SessionLocal() as db:
            ops_alerts.check_and_notify(db)
    except Exception:  # noqa: BLE001 — an alert failure must not stop the polling loop
        logger.exception("Operations alerts check failed")


async def scheduler_loop(app_settings: Settings):
    """Poll every source each interval, off the API event loop."""
    interval = max(5, app_settings.poll_interval_minutes) * 60
    while True:
        started = dt.datetime.utcnow()
        try:
            results = await asyncio.to_thread(sync_everything, app_settings)
            failed = [r.get("vcenterKey") or r.get("key") for r in results if not r["ok"]]
            await asyncio.to_thread(_ops_alerts)
            logger.info("Scheduled sync of %d sources finished in %.1fs%s", len(results),
                        (dt.datetime.utcnow() - started).total_seconds(),
                        f"; failed: {', '.join(failed)} (retry next cycle)" if failed else "")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Scheduled sync cycle failed; will retry next cycle")
        await asyncio.sleep(interval)
