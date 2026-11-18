"""Polling: vCenter inventory snapshots and backup-server syncs.

Performance notes
* vCenter properties are fetched in bulk by vcenter/client.py (PropertyCollector).
* Idle / powered-off durations come from each VM's previous snapshot in ONE query
  (not one history query per VM).
* Snapshot rows are written with bulk INSERTs.
* Sources are polled concurrently (SYNC_CONCURRENCY threads, one DB session each).

Reliability
* Every vCenter SOAP call has a socket timeout (vcenter/client.py) and each source has an
  overall deadline in a round (SYNC_SOURCE_DEADLINE_SECONDS): one hung source cannot stall the
  other sources, the next round or the 90 % Discord alerts.
* A source is synced by one thread at a time (collectors/locks.py), shared with manual syncs.
* The loop records a heartbeat read by /api/health/ready.
"""
import asyncio
import contextvars
import datetime as dt
import logging
import math
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import insert
from sqlalchemy.orm import Session

from .. import capacity, data_access, observability
from ..config import Settings, VCenterConfig, get_settings
from ..database import SessionLocal
from ..models import ClusterAggregate, ComplianceFindingSnapshot, HostSnapshot, SnapshotRun, VmSnapshot
from ..vcenter import client as vcenter_client
from ..vcenter import demo_generator
from .locks import SourceBusy, source_lock
from . import retention

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
    # Previous state of each VM, matched by instance UUID when vCenter gives one (a renamed VM keeps its
    # history; two VMs with the same name in different clusters no longer share it — DAT-06), by name otherwise.
    by_uuid: Dict[str, Any] = {}
    by_name: Dict[str, Any] = {}
    if run_ids:
        rows = (db.query(VmSnapshot.vm, VmSnapshot.instance_uuid, VmSnapshot.captured_at, VmSnapshot.is_powered_on,
                         VmSnapshot.cpu_usage_pct_raw, VmSnapshot.last_activity_days, VmSnapshot.power_off_days)
                .filter(VmSnapshot.run_id.in_(run_ids)).all())
        for r in rows:
            for index, key in ((by_uuid, r.instance_uuid), (by_name, r.vm)):
                if not key:
                    continue
                p = index.get(key)
                if p is None or r.captured_at > p.captured_at:
                    index[key] = r

    def previous_of(v: Dict[str, Any]) -> Any:
        uuid = v.get("instanceUuid")
        if uuid:
            p = by_uuid.get(uuid)
            if p is not None:
                return p
            q = by_name.get(v.get("vm"))         # legacy snapshot stored before uuids: same name, no uuid
            return q if q is not None and not q.instance_uuid else None
        return by_name.get(v.get("vm"))

    def days_since(start: dt.datetime) -> float:
        return max(0.0, (now - start).total_seconds() / 86400.0)

    for v in vm_rows:
        p = previous_of(v)
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
    with source_lock("vcenter", cfg.key):           # raises SourceBusy while another sync runs
        return _sync_vcenter_locked(db, cfg, settings or get_settings())


def _sync_vcenter_locked(db: Session, cfg: VCenterConfig, settings: Settings) -> SnapshotRun:
    started_at = dt.datetime.utcnow()
    try:
        platform = getattr(cfg, "platform", "vmware")
        if cfg.demo:
            host_rows, vm_rows = demo_generator.collect_inventory(cfg)
            extras = demo_generator.collect_extras(cfg, host_rows, vm_rows)
        elif platform in ("proxmox", "nutanix"):
            from ..hypervisors import PLATFORM_LABELS, nutanix, proxmox
            from ..storage import StorageError
            try:
                host_rows, vm_rows, extras = (proxmox if platform == "proxmox" else nutanix).collect(cfg)
            except StorageError as exc:     # same failure path as a vCenter: the run is recorded with the error
                raise vcenter_client.VCenterConnectionError(
                    f"Không đọc được {PLATFORM_LABELS[platform]} '{cfg.name}' ({cfg.host}): {exc}") from exc
        else:
            # same session: inventory + datastores / snapshots / alarms / host health (read-only)
            host_rows, vm_rows, extras = vcenter_client.collect_inventory_full(cfg)
        # The run row is written only once vCenter has answered: no DB transaction (and, on
        # SQLite, no database write lock) stays open while waiting on the network.
        run = SnapshotRun(vcenter_key=cfg.key, source="live", status="ok", started_at=started_at)
        db.add(run)
        db.flush()
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

        from .. import infra
        infra.store(db, cfg.key, extras, now)      # latest state + datastore usage history (metric_points)

        run.status = "ok"
        run.finished_at = dt.datetime.utcnow()
        db.commit()
        run.host_count, run.vm_count, run.finding_count = len(host_rows), len(vm_rows), len(findings)
        logger.info("Synced vCenter %s: %d hosts, %d vms, %d findings in %.1fs", cfg.key, len(host_rows), len(vm_rows),
                    len(findings), (run.finished_at - run.started_at).total_seconds())
        return run
    except Exception as exc:  # noqa: BLE001 — logged, recorded as an error run, re-raised
        # Log the original error first: if the database is the thing that is down, recording
        # the run below fails too and must not hide this stack.
        logger.exception("Sync failed for vCenter %s", cfg.key)
        try:
            db.rollback()
            # Record the failure as a NEW row (same pattern as backup/collector.py). Re-merging the
            # rolled-back `run` would reuse its flushed primary key, which SQLite hands out again
            # to a concurrent sync once this INSERT is rolled back — the error row then collides
            # with that run's id and is never saved.
            failed = SnapshotRun(vcenter_key=cfg.key, source="live", status="error", error_message=str(exc),
                                 started_at=started_at, finished_at=dt.datetime.utcnow())
            db.add(failed)
            db.commit()
        except Exception:  # noqa: BLE001 — keep the original error as the one raised
            logger.exception("Could not record the failed sync run of vCenter %s", cfg.key)
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
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


def deadline_seconds(settings: Optional[Settings] = None) -> float:
    return max(10, (settings or get_settings()).sync_source_deadline_seconds)


def run_syncs(tasks: List[Callable[[], Dict[str, Any]]], keys: List[Dict[str, Any]], workers: int,
              deadline: Optional[float] = None) -> List[Dict[str, Any]]:
    """Run sync tasks in parallel threads (at most `workers` at once) and return one result per
    task, in order. A failure becomes {"ok": False, "error": ...}; a source already being synced
    {"ok": False, "skipped": True, ...}. A task still running `deadline` seconds after it started
    is abandoned: reported as failed, its daemon thread left to end on its socket timeout (it
    keeps the source lock until then, so the next round skips that source instead of piling up).

    Plain threads rather than ThreadPoolExecutor: leaving a `with ThreadPoolExecutor` block
    waits for every thread, so one hung source used to block the whole round."""
    n = len(tasks)
    if not n:
        return []
    deadline = deadline if deadline is not None else deadline_seconds()
    workers = max(1, min(workers, n))
    done: "queue.Queue" = queue.Queue()
    results: List[Optional[Dict[str, Any]]] = [None] * n
    running: Dict[int, float] = {}          # task index -> monotonic start
    next_idx = 0

    def runner(i: int) -> None:
        try:
            done.put((i, tasks[i](), None))
        except BaseException as exc:  # noqa: BLE001 — handed over to the waiting thread
            done.put((i, None, exc))

    while next_idx < n or running:
        while next_idx < n and len(running) < workers:
            running[next_idx] = time.monotonic()
            # own copy of the caller's context per thread: log lines keep the round's job id
            threading.Thread(target=contextvars.copy_context().run, args=(runner, next_idx),
                             name=f"sync-{next_idx}", daemon=True).start()
            next_idx += 1
        wait = max(0.01, min(running.values()) + deadline - time.monotonic())
        try:
            i, res, exc = done.get(timeout=wait)
        except queue.Empty:
            now = time.monotonic()
            for i, start in list(running.items()):
                if now - start >= deadline:
                    del running[i]
                    name = keys[i].get("vcenterKey") or keys[i].get("key")
                    logger.error("Sync of %s still running after %.0fs (SYNC_SOURCE_DEADLINE_SECONDS); "
                                 "abandoned for this round", name, deadline)
                    results[i] = {**keys[i], "ok": False, "timedOut": True,
                                  "error": f"Quá hạn {int(deadline)} giây, bỏ qua lượt này (SYNC_SOURCE_DEADLINE_SECONDS)."}
            continue
        if i not in running:                 # finished after being abandoned: already reported
            continue
        del running[i]
        if exc is None:
            results[i] = res
        elif isinstance(exc, SourceBusy):
            results[i] = {**keys[i], "ok": False, "skipped": True, "error": str(exc)}
        else:                                # already logged and recorded as an error run
            results[i] = {**keys[i], "ok": False, "error": public_error(exc)}
    return [r for r in results if r is not None]


def public_error(exc: BaseException) -> str:
    """Message safe to show in the UI: our own connection errors are written for operators
    (no secrets); anything else (SQL, library internals) stays in the log only."""
    from ..backup.client import NetBackupError
    from ..storage import StorageError
    if isinstance(exc, (vcenter_client.VCenterConnectionError, NetBackupError, StorageError, SourceBusy)):
        return str(exc)
    return "Đồng bộ thất bại. Xem log máy chủ để biết chi tiết."


def sync_vcenters(settings: Settings, configs: Optional[List[VCenterConfig]] = None) -> List[Dict[str, Any]]:
    configs = settings.vcenters() if configs is None else configs
    return run_syncs([lambda c=c: _sync_vcenter_task(c, settings) for c in configs],
                     [{"vcenterKey": c.key} for c in configs], settings.sync_concurrency, deadline_seconds(settings))


def sync_backups(settings: Settings, configs=None) -> List[Dict[str, Any]]:
    configs = settings.netbackups() if configs is None else configs
    return run_syncs([lambda c=c: _sync_backup_task(c, settings) for c in configs],
                     [{"key": c.key} for c in configs], settings.sync_concurrency, deadline_seconds(settings))


def _sync_storage_task(cfg, peers: List[str]) -> Dict[str, Any]:
    from ..storage.collector import sync_storage
    with SessionLocal() as db:
        return sync_storage(db, cfg, peers)


def sync_storages(settings: Settings, configs=None) -> List[Dict[str, Any]]:
    configs = settings.storages() if configs is None else configs
    peers = {v: [c.key for c in configs if c.vendor == v and c.demo] for v in ("pure", "netapp")}
    return run_syncs([lambda c=c: _sync_storage_task(c, peers[c.vendor]) for c in configs],
                     [{"key": c.key} for c in configs], settings.sync_concurrency, deadline_seconds(settings))


def sync_everything(settings: Settings) -> List[Dict[str, Any]]:
    """All vCenters, backup servers and storage arrays concurrently (vCenters first in the queue: backup
    coverage reads the VM inventory, storage correlation reads the datastores)."""
    vcs, nbus, arrays = settings.vcenters(), settings.netbackups(), settings.storages()
    peers = {v: [c.key for c in arrays if c.vendor == v and c.demo] for v in ("pure", "netapp")}
    tasks = [lambda c=c: _sync_vcenter_task(c, settings) for c in vcs] + \
            [lambda c=c: _sync_backup_task(c, settings) for c in nbus] + \
            [lambda c=c: _sync_storage_task(c, peers[c.vendor]) for c in arrays]
    keys = [{"vcenterKey": c.key} for c in vcs] + [{"key": c.key} for c in nbus] + [{"key": c.key} for c in arrays]
    return run_syncs(tasks, keys, settings.sync_concurrency, deadline_seconds(settings))


def _ops_alerts() -> Optional[Dict[str, int]]:
    """Discord alerts for capacity / backup at the 90 % level (docs/design/super-app.md decision 5).
    Returns the counters ({"new", "resolved", "sent", "failed"}) or None when the check failed."""
    from .. import ops_alerts
    try:
        with SessionLocal() as db:
            return ops_alerts.check_and_notify(db)
    except Exception:  # noqa: BLE001 — an alert failure must not stop the polling loop
        logger.exception("Operations alerts check failed")
        return None


def round_summary(results: List[Dict[str, Any]], seconds: float, alerts: Optional[Dict[str, int]],
                  purge: Optional[Dict[str, int]]) -> str:
    """One key=value line per scheduler round (audit BEQ-11), easy to grep / parse:
    job=poll sources=3 ok=2 failed=1 skipped=0 duration_ms=4210 failed_keys=vc2 alerts_new=1 ..."""
    ok = sum(1 for r in results if r.get("ok"))
    skipped = [r.get("vcenterKey") or r.get("key") for r in results if r.get("skipped")]
    failed = [r.get("vcenterKey") or r.get("key") for r in results if not r.get("ok") and not r.get("skipped")]
    parts = [f"job=poll sources={len(results)} ok={ok} failed={len(failed)} skipped={len(skipped)}",
             f"duration_ms={int(seconds * 1000)}"]
    if failed:
        parts.append("failed_keys=" + ",".join(str(k) for k in failed))
    if skipped:
        parts.append("skipped_keys=" + ",".join(str(k) for k in skipped))
    if alerts is None:
        parts.append("alerts=error")
    else:
        parts.append(" ".join(f"alerts_{k}={alerts.get(k, 0)}" for k in ("new", "resolved", "sent", "failed")))
    if purge:
        parts.append(" ".join(f"purged_{k}={v}" for k, v in sorted(purge.items())))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Heartbeat (read by /api/health/ready)
# ---------------------------------------------------------------------------
_heartbeat: Dict[str, Any] = {"at": None, "interval": None, "rounds": 0}


def _beat(interval: float) -> None:
    _heartbeat.update(at=time.monotonic(), interval=interval)


def heartbeat_status(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """{"ok", "ageSeconds", "maxAgeSeconds", "rounds", "reason"?}. The loop beats before and
    after every round and between syncs and alerts, so a gap longer than 3 poll intervals + two
    source deadlines (more sources than SYNC_CONCURRENCY run in waves) + 2 min margin means the
    loop is stuck or dead."""
    settings = settings or get_settings()
    at, interval = _heartbeat["at"], _heartbeat["interval"]
    if at is None:
        return {"ok": False, "reason": "scheduler_not_started"}
    age = time.monotonic() - at
    max_age = 3 * interval + 2 * deadline_seconds(settings) + 120
    from .. import leader
    out = {"ok": age <= max_age, "ageSeconds": round(age), "maxAgeSeconds": round(max_age),
           "rounds": _heartbeat["rounds"], "leader": leader.status()}
    if not out["ok"]:
        out["reason"] = "scheduler_stale"
    return out


async def scheduler_loop(app_settings: Settings):
    """Poll every source each interval, off the API event loop."""
    from .. import leader
    interval = max(5, app_settings.poll_interval_minutes) * 60
    while True:
        # Several replicas: only the leader pod polls; standby pods wait here (and keep beating).
        await leader.wait_until_leader(lambda: _beat(interval))
        started = time.monotonic()
        _beat(interval)
        job = observability.job_context("poll")
        job.__enter__()   # every log line of this round (threads included) carries the job id
        try:
            results = await asyncio.to_thread(sync_everything, app_settings)
            _beat(interval)
            alerts = await asyncio.to_thread(_ops_alerts)
            purge = await asyncio.to_thread(retention.maybe_purge, app_settings.snapshot_retention_months)
            from .. import metrics
            rolled = await asyncio.to_thread(metrics.maybe_rollup)     # raw → hourly / daily, at most hourly
            line = round_summary(results, time.monotonic() - started, alerts, purge)
            if rolled:
                line += " " + " ".join(f"metrics_{k}={v}" for k, v in sorted(rolled.items()))
            logger.info("%s", line)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Scheduled sync cycle failed; will retry next cycle")
        finally:
            job.__exit__(None, None, None)
        _heartbeat["rounds"] += 1
        _beat(interval)
        await asyncio.sleep(interval)
