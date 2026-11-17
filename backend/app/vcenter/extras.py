"""Operational state read in the same vCenter session as the inventory (Giai đoạn 1): datastore
capacity, VM snapshots, triggered alarms, host hardware health. Read-only: PropertyCollector reads only.

Row shapes (camelCase, like the inventory rows) — demo_generator.collect_extras returns the same:
  datastores: {key, name, type, capacityGB, freeGB, uncommittedGB, accessible, maintenance, clusters[], hostCount, vmCount}
  snapshots:  {vm, instanceUuid, cluster, host, count, oldestAt, newestAt, sizeGB, items[{name, createdAt, description, quiesced}]}
  alarms:     {key, entityType, entity, cluster, alarm, status ('red'|'yellow'), at, acknowledged}
  health:     {host, cluster, overallStatus, connection, maintenance, issues[{kind, name, state, reading}]}
"""
import datetime as dt
import logging
import re
from typing import Any, Dict, Iterable, List, Optional

from pyVmomi import vim, vmodl

logger = logging.getLogger(__name__)

GB = 1024 ** 3
SNAPSHOT_FILE = re.compile(r"-\d{6}(-delta|-sesparse)?\.vmdk$|\.vmsn$|\.vmem$", re.I)
ENTITY_TYPES = {vim.HostSystem: "host", vim.VirtualMachine: "vm", vim.Datastore: "datastore",
                vim.ClusterComputeResource: "cluster", vim.ComputeResource: "cluster"}


def _sa(obj, *path, default=None):
    cur = obj
    for name in path:
        try:
            cur = getattr(cur, name)
        except Exception:  # noqa: BLE001
            return default
        if cur is None:
            return default
    return cur


def _moid(ref) -> Optional[str]:
    return getattr(ref, "_moId", None) if ref is not None else None


def _utc(t) -> Optional[str]:
    if t is None:
        return None
    if getattr(t, "tzinfo", None) is not None:
        t = t.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return t.replace(microsecond=0).isoformat() + "Z"


def retrieve_refs(content, refs: List[Any], vim_type, paths: List[str]) -> Dict[str, Dict[str, Any]]:
    """PropertyCollector read of explicit objects (alarm names, layoutEx of VMs with snapshots)."""
    if not refs:
        return {}
    pc = vmodl.query.PropertyCollector
    spec = pc.FilterSpec(objectSet=[pc.ObjectSpec(obj=r, skip=False) for r in refs],
                         propSet=[pc.PropertySpec(type=vim_type, pathSet=paths, all=False)])
    out: Dict[str, Dict[str, Any]] = {}
    result = content.propertyCollector.RetrievePropertiesEx([spec], pc.RetrieveOptions(maxObjects=1000))
    while result:
        for oc in result.objects:
            out[oc.obj._moId] = {p.name: p.val for p in (oc.propSet or [])}
        if not result.token:
            break
        result = content.propertyCollector.ContinueRetrievePropertiesEx(result.token)
    return out


def _walk(nodes, depth=0) -> Iterable[Dict[str, Any]]:
    for n in nodes or []:
        yield {"name": _sa(n, "name") or "", "createdAt": _utc(_sa(n, "createTime")),
               "description": (_sa(n, "description") or "")[:500], "quiesced": bool(_sa(n, "quiesced")), "depth": depth}
        yield from _walk(_sa(n, "childSnapshotList"), depth + 1)


def snapshot_size_gb(layout) -> Optional[float]:
    """Approximate disk used by snapshots: delta / sesparse disks, memory and state files (layoutEx)."""
    files = _sa(layout, "file") or []
    if not files:
        return None
    total = sum((_sa(f, "size") or 0) for f in files
                if _sa(f, "type") in ("snapshotData", "snapshotMemory") or SNAPSHOT_FILE.search(_sa(f, "name") or ""))
    return round(total / GB, 2)


def build_extras(content, hosts, compute, vms, datastores, host_info: Dict[str, tuple]) -> Dict[str, List[Dict[str, Any]]]:
    """host_info: host moId -> (host name, cluster name), from build_rows."""
    # ---- datastores
    ds_vms: Dict[str, int] = {}
    for _, (_, props) in vms.items():
        for ref in props.get("datastore") or []:
            ds_vms[_moid(ref)] = ds_vms.get(_moid(ref), 0) + 1
    ds_rows = []
    for moid, (_, props) in datastores.items():
        s = props.get("summary")
        mounts = props.get("host") or []
        clusters = sorted({host_info.get(_moid(_sa(m, "key")), (None, None))[1] for m in mounts} - {None, "UNKNOWN"})
        cap = (_sa(s, "capacity") or 0) / GB
        ds_rows.append({
            "key": moid, "name": props.get("name") or _sa(s, "name") or moid, "type": _sa(s, "type") or "",
            "capacityGB": round(cap, 2), "freeGB": round((_sa(s, "freeSpace") or 0) / GB, 2),
            "uncommittedGB": round((_sa(s, "uncommitted") or 0) / GB, 2),
            "accessible": bool(_sa(s, "accessible", default=True)), "maintenance": _sa(s, "maintenanceMode") or "normal",
            "clusters": clusters, "hostCount": len(mounts), "vmCount": ds_vms.get(moid, 0),
        })
    # ---- VM snapshots (+ size from layoutEx, read only for the VMs that have snapshots)
    with_snap = [(moid, obj, props) for moid, (obj, props) in vms.items() if _sa(props.get("snapshot"), "rootSnapshotList")]
    layouts: Dict[str, Dict[str, Any]] = {}
    if with_snap:
        try:
            layouts = retrieve_refs(content, [o for _, o, _ in with_snap], vim.VirtualMachine, ["layoutEx"])
        except Exception:  # noqa: BLE001 — sizes are optional; ages are what matters
            logger.warning("Could not read layoutEx for snapshot sizes", exc_info=True)
    snap_rows = []
    for moid, _, props in with_snap:
        summary = props.get("summary")
        items = list(_walk(_sa(props.get("snapshot"), "rootSnapshotList")))
        times = sorted(i["createdAt"] for i in items if i["createdAt"])
        host_name, cluster = host_info.get(_moid(_sa(summary, "runtime", "host")), (None, "UNKNOWN"))
        snap_rows.append({
            "vm": _sa(summary, "config", "name") or props.get("name") or moid,
            "instanceUuid": _sa(summary, "config", "instanceUuid"), "cluster": cluster, "host": host_name,
            "count": len(items), "oldestAt": times[0] if times else None, "newestAt": times[-1] if times else None,
            "sizeGB": snapshot_size_gb(layouts.get(moid, {}).get("layoutEx")), "items": items[:50],
        })
    # ---- host health
    health_rows = []
    for moid, (_, props) in hosts.items():
        s = props.get("summary")
        hs = props.get("runtime.healthSystemRuntime")
        issues = []
        for sensor in _sa(hs, "systemHealthInfo", "numericSensorInfo") or []:
            state = (_sa(sensor, "healthState", "key") or "").lower()
            if state in ("yellow", "red"):
                issues.append({"kind": _sa(sensor, "sensorType") or "sensor", "name": (_sa(sensor, "name") or "")[:200],
                               "state": state, "reading": _sa(sensor, "currentReading")})
        hw = _sa(hs, "hardwareStatusInfo")
        for group, kind in (("memoryStatusInfo", "memory"), ("cpuStatusInfo", "cpu"), ("storageStatusInfo", "storage")):
            for st in _sa(hw, group) or []:
                state = (_sa(st, "status", "key") or "").lower()
                if state in ("yellow", "red"):
                    issues.append({"kind": kind, "name": (_sa(st, "name") or "")[:200], "state": state, "reading": None})
        name, cluster = host_info.get(moid, (props.get("name") or moid, "UNKNOWN"))
        health_rows.append({
            "host": name, "cluster": cluster, "overallStatus": str(_sa(s, "overallStatus") or "gray"),
            "connection": str(_sa(s, "runtime", "connectionState") or "connected"),
            "maintenance": bool(_sa(s, "runtime", "inMaintenanceMode")), "issues": issues[:100],
        })
    # ---- triggered alarms (whole inventory, from the root folder)
    alarm_rows = []
    try:
        states = list(content.rootFolder.triggeredAlarmState or [])
    except Exception:  # noqa: BLE001
        logger.warning("Could not read triggered alarms", exc_info=True)
        states = []
    names: Dict[str, Dict[str, Any]] = {}
    alarm_refs = {_moid(_sa(a, "alarm")): _sa(a, "alarm") for a in states if _sa(a, "alarm") is not None}
    if alarm_refs:
        try:
            names = retrieve_refs(content, list(alarm_refs.values()), vim.alarm.Alarm, ["info.name"])
        except Exception:  # noqa: BLE001
            logger.warning("Could not read alarm names", exc_info=True)
    lookup = {**{k: ("host", v[1].get("name")) for k, v in hosts.items()},
              **{k: ("vm", _sa(v[1].get("summary"), "config", "name") or v[1].get("name")) for k, v in vms.items()},
              **{k: ("datastore", v[1].get("name")) for k, v in datastores.items()},
              **{k: ("cluster", v[1].get("name")) for k, v in compute.items()}}
    vm_cluster = {k: host_info.get(_moid(_sa(v[1].get("summary"), "runtime", "host")), (None, None))[1] for k, v in vms.items()}
    for a in states:
        status = str(_sa(a, "overallStatus") or "").lower()
        if status not in ("red", "yellow"):
            continue
        ent = _sa(a, "entity")
        eid = _moid(ent)
        etype, ename = lookup.get(eid, (next((v for k, v in ENTITY_TYPES.items() if isinstance(ent, k)), "other"), eid))
        cluster = host_info.get(eid, (None, None))[1] if etype == "host" else vm_cluster.get(eid) if etype == "vm" else (ename if etype == "cluster" else None)
        aid = _moid(_sa(a, "alarm"))
        alarm_rows.append({
            "key": _sa(a, "key") or f"{aid}.{eid}", "entityType": etype, "entity": ename or eid, "cluster": cluster,
            "alarm": (names.get(aid, {}).get("info.name") or aid or "Alarm")[:255], "status": status,
            "at": _utc(_sa(a, "time")), "acknowledged": bool(_sa(a, "acknowledged")),
        })
    return {"datastores": ds_rows, "snapshots": snap_rows, "alarms": alarm_rows, "health": health_rows}
