"""
Shared query helpers: turn persisted SnapshotRun rows back into the camelCase
row dicts the frontend/capacity engine expects, and figure out "latest data
per source" (each live vCenter's most recent successful run, plus the most
recent manual import, combined).
"""
import json
import math
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from . import capacity
from .config import Settings
from .models import AppSetting, HostSnapshot, SnapshotRun, VmSnapshot
from .timeutil import to_iso_utc


def get_effective_policy(db: Session, settings: Settings) -> "capacity.Policy":
    """Merge env-var defaults with any admin-adjusted thresholds stored in the
    app_settings table (see routers/settings.py)."""
    overrides = {row.key: row.value for row in db.query(AppSetting).all()}

    def num(key: str, fallback: float, floor: float) -> float:
        """Like core.js::rebuildFromRaw: empty / 0 / not a number → the default, then never below the floor
        (values stored before the input check, or from the environment)."""
        try:
            v = float(overrides.get(key, getattr(settings, key)))
        except (TypeError, ValueError):
            v = 0.0
        return max(floor, v if math.isfinite(v) and v else fallback)
    return capacity.Policy(
        cpu_overcommit_max=num("cpu_overcommit_max", 6.0, 0.1),
        ram_overcommit_max=num("ram_overcommit_max", 1.0, 0.1),
        ht_factor=num("ht_factor", 2.0, 1.0),
        idle_days=int(num("idle_days", 30, 1)),
        include_storage=capacity.parse_bool(overrides.get("include_storage"), default=bool(settings.include_storage)),
    )


def _platform(key):
    from .sources import platform_of
    try:
        return platform_of(key)
    except Exception:  # noqa: BLE001 — sources unavailable: the legacy assumption
        return "vmware" if key and key != "import" else None


def _host_to_row(h: HostSnapshot) -> Dict[str, Any]:
    return {
        "cluster": h.cluster, "host": h.host, "cores": h.cores, "cpuMhz": h.cpu_mhz,
        "ramGB": h.ram_gb, "cpuUsagePctRaw": h.cpu_usage_pct_raw, "ramUsagePctRaw": h.ram_usage_pct_raw,
        "status": h.status, "vcenterKey": h.vcenter_key, "platform": _platform(h.vcenter_key),
        "vendor": h.vendor, "model": h.model, "esxiVersion": h.esxi_version, "esxiBuild": h.esxi_build,
        "cpuModel": h.cpu_model, "cpuSockets": h.cpu_sockets, "cpuThreads": h.cpu_threads,
        "clusterHa": h.cluster_ha, "clusterDrs": h.cluster_drs, "serial": h.serial, "hwUuid": h.hw_uuid,
    }


def _attrs(raw: Optional[str]) -> Dict[str, str]:
    try:
        d = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def _vm_to_row(v: VmSnapshot) -> Dict[str, Any]:
    return {
        "cluster": v.cluster, "host": v.host, "vm": v.vm, "instanceUuid": v.instance_uuid, "powerState": v.power_state,
        "isPoweredOn": v.is_powered_on, "ipAddress": v.ip_address, "vcpu": v.vcpu, "ramGB": v.ram_gb,
        "diskProvGB": v.disk_prov_gb, "diskUsedGB": v.disk_used_gb, "datastore": v.datastore,
        "cpuUsagePctRaw": v.cpu_usage_pct_raw, "ramUsagePctRaw": v.ram_usage_pct_raw,
        "lastActivityDays": v.last_activity_days, "powerOffDays": v.power_off_days,
        "vcenterKey": v.vcenter_key,
        "guestId": v.guest_id, "guestFullName": v.guest_full_name, "toolsGuestFullName": v.tools_guest_full_name,
        "toolsGuestFamily": v.tools_guest_family, "toolsStatus": v.tools_status, "guestHostname": v.guest_hostname,
        "folderPath": v.folder_path, "resourcePool": v.resource_pool, "customAttrs": _attrs(v.custom_attrs),
    }


def latest_run_for_key(db: Session, vcenter_key: Optional[str], source: Optional[str] = None) -> Optional[SnapshotRun]:
    q = db.query(SnapshotRun).filter(SnapshotRun.status == "ok")
    if vcenter_key is None:
        q = q.filter(SnapshotRun.vcenter_key.is_(None))
    else:
        q = q.filter(SnapshotRun.vcenter_key == vcenter_key)
    if source:
        q = q.filter(SnapshotRun.source == source)
    return q.order_by(SnapshotRun.finished_at.desc().nullslast(), SnapshotRun.id.desc()).first()


def known_vcenter_keys(db: Session) -> List[str]:
    rows = db.query(SnapshotRun.vcenter_key).filter(SnapshotRun.vcenter_key.isnot(None)).distinct().all()
    return [r[0] for r in rows]


def get_latest_combined(db: Session, vcenter_keys: Optional[List[str]] = None, include_import: bool = True) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Returns (host_rows, vm_rows, run_meta) combining the latest successful run
    for each requested vCenter key (default: all known keys) plus, optionally,
    the latest manual import run. run_meta lists each contributing run's
    {vcenterKey, source, finishedAt} for UI freshness display."""
    keys = vcenter_keys if vcenter_keys is not None else known_vcenter_keys(db)
    host_rows: List[Dict[str, Any]] = []
    vm_rows: List[Dict[str, Any]] = []
    run_meta: List[Dict[str, Any]] = []

    for key in keys:
        run = latest_run_for_key(db, key, source="live")
        if not run:
            continue
        host_rows.extend(_host_to_row(h) for h in run.hosts)
        vm_rows.extend(_vm_to_row(v) for v in run.vms)
        run_meta.append({"vcenterKey": key, "source": "live", "finishedAt": to_iso_utc(run.finished_at)})

    if include_import:
        run = latest_run_for_key(db, None, source="import")
        if run:
            host_rows.extend(_host_to_row(h) for h in run.hosts)
            vm_rows.extend(_vm_to_row(v) for v in run.vms)
            run_meta.append({"vcenterKey": "import", "source": "import", "finishedAt": to_iso_utc(run.finished_at)})

    return host_rows, vm_rows, run_meta


def get_latest_grouped(db: Session, include_import: bool = True) -> Dict[str, Any]:
    """Like get_latest_combined, but also returns a byVcenter breakdown so the
    frontend can compute per-vCenter KPIs (Executive view) without N round
    trips."""
    keys = known_vcenter_keys(db)
    host_rows: List[Dict[str, Any]] = []
    vm_rows: List[Dict[str, Any]] = []
    run_meta: List[Dict[str, Any]] = []
    by_vcenter: Dict[str, Any] = {}

    for key in keys:
        run = latest_run_for_key(db, key, source="live")
        if not run:
            continue
        h = [_host_to_row(x) for x in run.hosts]
        v = [_vm_to_row(x) for x in run.vms]
        host_rows.extend(h)
        vm_rows.extend(v)
        by_vcenter[key] = {"hosts": h, "vms": v}
        run_meta.append({"vcenterKey": key, "source": "live", "finishedAt": to_iso_utc(run.finished_at)})

    if include_import:
        run = latest_run_for_key(db, None, source="import")
        if run:
            h = [_host_to_row(x) for x in run.hosts]
            v = [_vm_to_row(x) for x in run.vms]
            host_rows.extend(h)
            vm_rows.extend(v)
            by_vcenter["import"] = {"hosts": h, "vms": v}
            run_meta.append({"vcenterKey": "import", "source": "import", "finishedAt": to_iso_utc(run.finished_at)})

    return {"hosts": host_rows, "vms": vm_rows, "byVcenter": by_vcenter, "runMeta": run_meta}


