"""Infrastructure health (Giai đoạn 1): datastore capacity + days-to-full, long-lived VM snapshots,
triggered vCenter alarms, host hardware health.

Levels use the inbox vocabulary of the 4 standard statuses: crit (VƯỢT NGƯỠNG), bad (CẢNH BÁO),
warn (THEO DÕI), ok (AN TOÀN). Thresholds come from settings (DS_* / SNAPSHOT_*).
The collector stores the latest state per vCenter (store); readers never call vCenter.
"""
import datetime as dt
import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from . import metrics
from .config import get_settings
from .models import (DatastoreState, HostHealthState, StorageAlertState, StorageArrayState, StorageVolumeState, VcAlarmState,
                     VmSnapshotState)
from .scope import Scope

LEVEL_RANK = {"crit": 0, "bad": 1, "warn": 2, "ok": 3}
LEVEL_TEXT = {"crit": "VƯỢT NGƯỠNG", "bad": "CẢNH BÁO", "warn": "THEO DÕI", "ok": "AN TOÀN"}
FORECAST_DAYS = 30


def worst(*levels: str) -> str:
    return min(levels, key=lambda lv: LEVEL_RANK.get(lv, 9))


def _parse(ts: Optional[str]) -> Optional[dt.datetime]:
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(str(ts).rstrip("Z"))
    except ValueError:
        return None


def _iso(t: Optional[dt.datetime]) -> Optional[str]:
    return t.replace(microsecond=0).isoformat() + "Z" if t else None


def ds_entity(vcenter_key: str, ds_key: str) -> str:
    return f"{vcenter_key}:{ds_key}"


# ---------------------------------------------------------------- store (collector)
def store(db: Session, vcenter_key: str, extras: Dict[str, Any], now: Optional[dt.datetime] = None) -> Dict[str, int]:
    """Replace the latest state of one vCenter and record datastore usage history. Extras that failed to
    read (extras["error"]) leave the previous state untouched: a read error is not "no alarms". Caller commits."""
    if not extras or extras.get("error"):
        return {}
    now = now or dt.datetime.utcnow()
    for model in (DatastoreState, VmSnapshotState, VcAlarmState, HostHealthState):
        db.query(model).filter(model.vcenter_key == vcenter_key).delete(synchronize_session=False)
    ds = extras.get("datastores") or []
    db.bulk_insert_mappings(DatastoreState, [{
        "vcenter_key": vcenter_key, "ds_key": d["key"][:64], "name": d["name"][:255], "ds_type": (d.get("type") or "")[:16],
        "capacity_gb": d["capacityGB"], "free_gb": d["freeGB"], "uncommitted_gb": d.get("uncommittedGB") or 0,
        "accessible": bool(d.get("accessible", True)), "maintenance": (d.get("maintenance") or "normal")[:32],
        "clusters": json.dumps(d.get("clusters") or []), "host_count": d.get("hostCount") or 0, "vm_count": d.get("vmCount") or 0,
        "backing": json.dumps({"naa": d.get("naa") or [], "nfsHost": d.get("nfsHost"), "nfsPath": d.get("nfsPath")}),
        "captured_at": now} for d in ds])
    db.bulk_insert_mappings(VmSnapshotState, [{
        "vcenter_key": vcenter_key, "vm": s["vm"][:255], "instance_uuid": s.get("instanceUuid"), "cluster": s.get("cluster"),
        "host": s.get("host"), "snapshot_count": s.get("count") or 0, "oldest_at": _parse(s.get("oldestAt")),
        "newest_at": _parse(s.get("newestAt")), "size_gb": s.get("sizeGB"), "items": json.dumps(s.get("items") or [], ensure_ascii=False),
        "captured_at": now} for s in extras.get("snapshots") or []])
    db.bulk_insert_mappings(VcAlarmState, [{
        "vcenter_key": vcenter_key, "alarm_key": str(a["key"])[:255], "entity_type": a.get("entityType") or "other",
        "entity": str(a.get("entity") or "")[:255], "cluster": a.get("cluster"), "alarm": str(a.get("alarm") or "")[:255],
        "status": a.get("status") or "yellow", "triggered_at": _parse(a.get("at")), "acknowledged": bool(a.get("acknowledged")),
        "captured_at": now} for a in extras.get("alarms") or []])
    db.bulk_insert_mappings(HostHealthState, [{
        "vcenter_key": vcenter_key, "host": h["host"][:255], "cluster": h.get("cluster"), "overall_status": (h.get("overallStatus") or "gray")[:8],
        "connection": (h.get("connection") or "connected")[:32], "maintenance": bool(h.get("maintenance")),
        "issues": json.dumps(h.get("issues") or [], ensure_ascii=False), "captured_at": now} for h in extras.get("health") or []])
    points = []
    for d in ds:
        e = ds_entity(vcenter_key, d["key"])
        used = max(0.0, d["capacityGB"] - d["freeGB"])
        points += [(e, "usedGB", used), (e, "capacityGB", d["capacityGB"]), (e, "provGB", used + (d.get("uncommittedGB") or 0))]
    metrics.record(db, "datastore", points, now)
    return {"datastores": len(ds), "snapshots": len(extras.get("snapshots") or []), "alarms": len(extras.get("alarms") or []),
            "hosts": len(extras.get("health") or [])}


def _in_scope(scope: Optional[Scope], vcenter_key: str, clusters: List[str], host: Optional[str] = None) -> bool:
    if scope is None:
        return True
    if scope.vcenter and vcenter_key != scope.vcenter:
        return False
    if scope.clusters is not None and not (set(clusters) & set(scope.clusters)):
        return False
    if scope.hosts is not None and host is not None and host not in scope.hosts:
        return False
    return True


# ---------------------------------------------------------------- datastores
def _n(v: float, d: int = 1) -> str:
    """vi-VN number for sentences: 92,3 / 1.234."""
    return f"{v:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def datastore_level(used_pct: Optional[float], accessible: bool, days_to_full: Optional[float], overprov_pct: Optional[float]):
    """(level, [reasons]) of one datastore."""
    s = get_settings()
    if not accessible:
        return "crit", ["Datastore không truy cập được từ vCenter"]
    level, reasons = "ok", []
    if used_pct is not None:
        for lv, limit in (("crit", s.ds_crit_pct), ("bad", s.ds_warn_pct), ("warn", s.ds_watch_pct)):
            if used_pct >= limit:
                level = lv
                reasons.append(f"Đã dùng {_n(used_pct)}% (≥ {_n(limit, 0)}%)")
                break
    if days_to_full is not None:
        if days_to_full <= s.ds_full_crit_days:
            level = worst(level, "crit")
            reasons.append(f"Dự báo đầy sau {_n(days_to_full, 0)} ngày (≤ {s.ds_full_crit_days})")
        elif days_to_full <= s.ds_full_warn_days:
            level = worst(level, "bad")
            reasons.append(f"Dự báo đầy sau {_n(days_to_full, 0)} ngày (≤ {s.ds_full_warn_days})")
    if overprov_pct is not None and overprov_pct >= s.ds_overprov_pct:
        level = worst(level, "warn")
        reasons.append(f"Cấp phát thin {_n(overprov_pct, 0)}% dung lượng (≥ {_n(s.ds_overprov_pct, 0)}%)")
    return level, reasons


def datastores(db: Session, scope: Optional[Scope] = None, now: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    now = now or dt.datetime.utcnow()
    rows = [r for r in db.query(DatastoreState).order_by(DatastoreState.vcenter_key, DatastoreState.name)
            if _in_scope(scope, r.vcenter_key, json.loads(r.clusters or "[]"))]
    hist = metrics.series(db, "datastore", [ds_entity(r.vcenter_key, r.ds_key) for r in rows], "usedGB",
                          now - dt.timedelta(days=FORECAST_DAYS), now)
    backing = datastore_backing(db)
    out = []
    for r in rows:
        used = max(0.0, r.capacity_gb - r.free_gb)
        pct = used * 100 / r.capacity_gb if r.capacity_gb else None
        overprov = (used + r.uncommitted_gb) * 100 / r.capacity_gb if r.capacity_gb else None
        fc = metrics.linear_forecast(hist.get(ds_entity(r.vcenter_key, r.ds_key), []), r.capacity_gb) if r.capacity_gb else None
        days = fc["daysToLimit"] if fc else None
        level, reasons = datastore_level(pct, r.accessible, days, overprov)
        out.append({
            "vcenterKey": r.vcenter_key, "key": r.ds_key, "name": r.name, "type": r.ds_type, "clusters": json.loads(r.clusters or "[]"),
            "storage": backing.get((r.vcenter_key, r.ds_key)),
            "capacityGB": round(r.capacity_gb, 1), "usedGB": round(used, 1), "freeGB": round(r.free_gb, 1),
            "usedPct": round(pct, 1) if pct is not None else None, "provisionedPct": round(overprov, 1) if overprov is not None else None,
            "growthGBPerDay": round(fc["perDay"], 2) if fc else None, "daysToFull": round(days, 1) if days is not None else None,
            "fullDate": (now + dt.timedelta(days=days)).date().isoformat() if days is not None else None,
            "accessible": r.accessible, "maintenance": r.maintenance, "hostCount": r.host_count, "vmCount": r.vm_count,
            "level": level, "levelText": LEVEL_TEXT[level], "reasons": reasons, "capturedAt": _iso(r.captured_at),
        })
    out.sort(key=lambda d: (LEVEL_RANK[d["level"]], -(d["usedPct"] or 0)))
    return out


def datastore_history(db: Session, vcenter_key: str, ds_key: str, days: int, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    now = now or dt.datetime.utcnow()
    e = ds_entity(vcenter_key, ds_key)
    since = now - dt.timedelta(days=max(1, min(days, 1095)))
    used = metrics.series(db, "datastore", [e], "usedGB", since, now)[e]
    cap = metrics.series(db, "datastore", [e], "capacityGB", since, now)[e]
    return {"used": [[_iso(t), round(v, 1)] for t, v in used], "capacity": [[_iso(t), round(v, 1)] for t, v in cap]}


# ---------------------------------------------------------------- snapshots
def snapshot_level(age_days: Optional[float], size_gb: Optional[float], count: int, name_hint: str = ""):
    s = get_settings()
    level, reasons = "ok", []
    if age_days is not None:
        if age_days >= s.snapshot_crit_days:
            level = "crit"
            reasons.append(f"Snapshot cũ nhất {_n(age_days, 0)} ngày (≥ {s.snapshot_crit_days})")
        elif age_days >= s.snapshot_warn_days:
            level = "bad"
            reasons.append(f"Snapshot cũ nhất {_n(age_days, 0)} ngày (≥ {s.snapshot_warn_days})")
        elif age_days * 24 >= s.snapshot_watch_hours:
            level = "warn"
            reasons.append(f"Snapshot tồn {_n(age_days * 24, 0)} giờ")
    if size_gb is not None and size_gb >= s.snapshot_crit_gb:
        level = worst(level, "crit")
        reasons.append(f"Dữ liệu snapshot {_n(size_gb, 0)} GB (≥ {_n(s.snapshot_crit_gb, 0)})")
    if count >= s.snapshot_chain_warn:
        level = worst(level, "bad")
        reasons.append(f"Chuỗi {count} snapshot (≥ {s.snapshot_chain_warn})")
    return level, reasons


def snapshots(db: Session, scope: Optional[Scope] = None, now: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    now = now or dt.datetime.utcnow()
    out = []
    for r in db.query(VmSnapshotState):
        if not _in_scope(scope, r.vcenter_key, [r.cluster] if r.cluster else [], r.host):
            continue
        age = (now - r.oldest_at).total_seconds() / 86400 if r.oldest_at else None
        items = json.loads(r.items or "[]")
        level, reasons = snapshot_level(age, r.size_gb, r.snapshot_count)
        out.append({"vcenterKey": r.vcenter_key, "vm": r.vm, "instanceUuid": r.instance_uuid, "cluster": r.cluster, "host": r.host,
                    "count": r.snapshot_count, "oldestAt": _iso(r.oldest_at), "ageDays": round(age, 1) if age is not None else None,
                    "sizeGB": r.size_gb, "items": items, "level": level, "levelText": LEVEL_TEXT[level], "reasons": reasons,
                    "backupLike": all("nbu" in (i.get("name") or "").lower() or "backup" in (i.get("description") or "").lower()
                                      for i in items) if items else False})
    out.sort(key=lambda d: (LEVEL_RANK[d["level"]], -(d["ageDays"] or 0)))
    return out


# ---------------------------------------------------------------- alarms & host health
ALARM_LEVEL = {"red": "crit", "yellow": "bad"}
HEALTH_LEVEL = {"red": "crit", "yellow": "bad"}


def alarms(db: Session, scope: Optional[Scope] = None) -> List[Dict[str, Any]]:
    out = []
    for r in db.query(VcAlarmState):
        if not _in_scope(scope, r.vcenter_key, [r.cluster] if r.cluster else [], r.entity if r.entity_type == "host" else None):
            continue
        level = ALARM_LEVEL.get(r.status, "warn")
        out.append({"vcenterKey": r.vcenter_key, "key": r.alarm_key, "entityType": r.entity_type, "entity": r.entity,
                    "cluster": r.cluster, "alarm": r.alarm, "status": r.status, "at": _iso(r.triggered_at),
                    "acknowledged": r.acknowledged, "level": level, "levelText": LEVEL_TEXT[level]})
    out.sort(key=lambda d: (LEVEL_RANK[d["level"]], d["acknowledged"], d["at"] or ""), reverse=False)
    return out


def host_health(db: Session, scope: Optional[Scope] = None) -> List[Dict[str, Any]]:
    out = []
    for r in db.query(HostHealthState):
        if not _in_scope(scope, r.vcenter_key, [r.cluster] if r.cluster else [], r.host):
            continue
        issues = json.loads(r.issues or "[]")
        level = "ok"
        reasons = []
        if r.connection not in ("connected",):
            level, reasons = "crit", [f"Host {r.connection} với vCenter"]
        level = worst(level, HEALTH_LEVEL.get(r.overall_status, "ok"))
        for i in issues:
            level = worst(level, HEALTH_LEVEL.get(i.get("state"), "ok"))
            reasons.append(f"{i.get('name')}")
        out.append({"vcenterKey": r.vcenter_key, "host": r.host, "cluster": r.cluster, "overallStatus": r.overall_status,
                    "connection": r.connection, "maintenance": r.maintenance, "issues": issues, "level": level,
                    "levelText": LEVEL_TEXT[level], "reasons": reasons})
    out.sort(key=lambda d: (LEVEL_RANK[d["level"]], d["host"]))
    return out


def summary(db: Session, scope: Optional[Scope] = None) -> Dict[str, Any]:
    ds, sn, al, hh = datastores(db, scope), snapshots(db, scope), alarms(db, scope), host_health(db, scope)
    st = storage(db)

    def count(rows, lv):
        return sum(1 for r in rows if r["level"] == lv)
    soonest = min((d["daysToFull"] for d in ds if d["daysToFull"] is not None), default=None)
    return {
        "datastores": {"total": len(ds), "crit": count(ds, "crit"), "bad": count(ds, "bad"), "warn": count(ds, "warn"),
                       "soonestFullDays": soonest,
                       "capacityGB": round(sum(d["capacityGB"] for d in ds), 1), "usedGB": round(sum(d["usedGB"] for d in ds), 1)},
        "snapshots": {"vms": len(sn), "crit": count(sn, "crit"), "bad": count(sn, "bad"),
                      "sizeGB": round(sum((s["sizeGB"] or 0) for s in sn), 1)},
        "alarms": {"total": len(al), "red": sum(1 for a in al if a["status"] == "red"), "unacknowledged": sum(1 for a in al if not a["acknowledged"])},
        "health": {"hosts": len(hh), "issues": sum(1 for h in hh if h["level"] != "ok")},
        "storage": {"arrays": len(st["arrays"]), "issues": sum(1 for a in st["arrays"] if a["level"] != "ok"),
                    "crit": sum(1 for a in st["arrays"] if a["level"] == "crit"),
                    "alerts": sum(len(a["alerts"]) for a in st["arrays"])},
        "available": bool(ds or hh or st["arrays"]),
    }


# ---------------------------------------------------------------- storage arrays (Pure FlashArray / NetApp ONTAP)
def _configured() -> Optional[set]:
    try:
        return {c.key for c in get_settings().storages()}
    except Exception:  # noqa: BLE001
        return None


def _correlation(db: Session):
    """(by_naa, by_nfs, ips, arrays): volume rows keyed by NAA and by (array, NFS path); NFS LIF IP → array.
    Only arrays still configured (and enabled) in Cài đặt → Nguồn dữ liệu."""
    keys = _configured()
    arrays = {a.source_key: a for a in db.query(StorageArrayState) if keys is None or a.source_key in keys}
    by_naa, by_nfs = {}, {}
    for v in db.query(StorageVolumeState):
        if v.source_key not in arrays:
            continue
        if v.naa:
            by_naa[v.naa] = v
        if v.nfs_path:
            by_nfs[(v.source_key, v.container or "", v.nfs_path.rstrip("/") or "/")] = v
    ips = {}                                            # data LIF IP → (array, SVM): two SVMs may export the same path
    for key, a in arrays.items():
        for ep in json.loads(a.nfs_endpoints or "[]"):
            if ep.get("ip"):
                ips[ep["ip"]] = (key, ep.get("svm") or "")
    return by_naa, by_nfs, ips, arrays


def _match(backing_json: Optional[str], by_naa, by_nfs, ips):
    """The array volume behind one datastore: VMFS by an extent's NAA, NFS by (array of the LIF IP, junction
    path); an NFS datastore mounted by host name matches on the path when only one array exports it."""
    b = json.loads(backing_json or "{}") if backing_json else {}
    vol = next((by_naa[n] for n in b.get("naa") or [] if n in by_naa), None)
    if vol is None and b.get("nfsPath"):
        path = b["nfsPath"].rstrip("/") or "/"
        src = ips.get(b.get("nfsHost") or "")
        vol = by_nfs.get((src[0], src[1], path)) if src else None
        if vol is None and not src:                    # mounted by host name: the path alone, when unambiguous
            hits = [v for (_, _, p), v in by_nfs.items() if p == path]
            vol = hits[0] if len(hits) == 1 else None
    return vol


def datastore_backing(db: Session) -> Dict[tuple, Dict[str, Any]]:
    """(vcenter key, datastore key) → {sourceKey, array, volume, kind} for datastores found on a monitored array."""
    by_naa, by_nfs, ips, arrays = _correlation(db)
    if not arrays:
        return {}
    out = {}
    for r in db.query(DatastoreState.vcenter_key, DatastoreState.ds_key, DatastoreState.backing):
        vol = _match(r.backing, by_naa, by_nfs, ips)
        if vol is not None:
            a = arrays.get(vol.source_key)
            out[(r.vcenter_key, r.ds_key)] = {"sourceKey": vol.source_key, "array": a.name if a else vol.source_key,
                                              "volume": vol.name, "kind": vol.kind}
    return out


def _array_level(a: StorageArrayState, alerts: List[StorageAlertState], now: dt.datetime):
    s = get_settings()
    level, reasons = "ok", []
    if a.last_error and (a.last_ok_at is None or a.last_error_at and a.last_error_at > a.last_ok_at):
        level = "bad"
        reasons.append(f"Không đọc được mảng: {a.last_error[:160]}")
    elif a.last_ok_at and (now - a.last_ok_at).total_seconds() > s.storage_stale_minutes * 60:
        level = "bad"
        reasons.append(f"Dữ liệu cũ hơn {s.storage_stale_minutes} phút")
    pct = a.used_gb * 100 / a.capacity_gb if a.capacity_gb and a.used_gb is not None else None
    if pct is not None:
        for lv, limit in (("crit", s.ds_crit_pct), ("bad", s.ds_warn_pct), ("warn", s.ds_watch_pct)):
            if pct >= limit:
                level = worst(level, lv)
                reasons.append(f"Dung lượng vật lý đã dùng {_n(pct)}% (≥ {_n(limit, 0)}%)")
                break
    lat = max([x for x in (a.latency_read_ms, a.latency_write_ms) if x is not None] or [0])
    if lat >= s.storage_latency_crit_ms:
        level = worst(level, "crit")
        reasons.append(f"Độ trễ {_n(lat, 1)} ms (≥ {_n(s.storage_latency_crit_ms, 0)} ms)")
    elif lat >= s.storage_latency_warn_ms:
        level = worst(level, "bad")
        reasons.append(f"Độ trễ {_n(lat, 1)} ms (≥ {_n(s.storage_latency_warn_ms, 0)} ms)")
    for c in json.loads(a.controllers or "[]"):
        if str(c.get("status") or "").lower() not in ("ready", "up", "", "none"):
            level = worst(level, "crit")
            reasons.append(f"Controller {c.get('name')}: {c.get('status')}")
    for h in json.loads(a.hardware_issues or "[]"):
        st = str(h.get("status") or "").lower()
        level = worst(level, "crit" if st in ("failed", "critical", "broken", "down", "over") else "bad")
        reasons.append(f"{h.get('name')}: {h.get('details') or st}")
    for al in alerts:
        level = worst(level, "crit" if al.severity == "critical" else "bad" if al.severity == "warning" else level)
    n_crit = sum(1 for x in alerts if x.severity == "critical")
    n_warn = sum(1 for x in alerts if x.severity == "warning")
    if n_crit or n_warn:
        reasons.append(f"{n_crit} alert nghiêm trọng, {n_warn} cảnh báo đang mở")
    return level, reasons, pct


def storage(db: Session, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Arrays (level, capacity, performance, hardware, alerts) and their volumes with the VMware datastore
    each one backs (NAA / NFS correlation) and that datastore's VM count."""
    now = now or dt.datetime.utcnow()
    by_naa, by_nfs, ips, arrays = _correlation(db)
    alerts: Dict[str, List[StorageAlertState]] = {}
    for x in db.query(StorageAlertState).order_by(StorageAlertState.raised_at.desc()):
        alerts.setdefault(x.source_key, []).append(x)
    ds_by_vol: Dict[int, List[Dict[str, Any]]] = {}
    for r in db.query(DatastoreState):
        vol = _match(r.backing, by_naa, by_nfs, ips)
        if vol is not None:
            ds_by_vol.setdefault(vol.id, []).append({"vcenterKey": r.vcenter_key, "datastore": r.name, "vmCount": r.vm_count,
                                                     "clusters": json.loads(r.clusters or "[]")})
    out_arrays = []
    for key, a in sorted(arrays.items(), key=lambda kv: kv[1].name or kv[0]):
        level, reasons, pct = _array_level(a, alerts.get(key, []), now)
        out_arrays.append({
            "sourceKey": key, "vendor": a.vendor, "name": a.name, "model": a.model, "version": a.version,
            "capacityGB": a.capacity_gb, "usedGB": a.used_gb, "usedPct": round(pct, 1) if pct is not None else None,
            "provisionedGB": a.provisioned_gb, "dataReduction": a.data_reduction,
            "latencyReadMs": a.latency_read_ms, "latencyWriteMs": a.latency_write_ms, "iops": a.iops, "throughputMBps": a.throughput_mbps,
            "controllers": json.loads(a.controllers or "[]"), "hardwareIssues": json.loads(a.hardware_issues or "[]"),
            "alerts": [{"id": x.alert_id, "severity": x.severity, "summary": x.summary, "component": x.component, "at": _iso(x.raised_at)}
                       for x in alerts.get(key, [])][:50],
            "lastOkAt": _iso(a.last_ok_at), "lastError": a.last_error, "lastErrorAt": _iso(a.last_error_at),
            "level": level, "levelText": LEVEL_TEXT[level], "reasons": reasons,
        })
    vols = []
    for v in db.query(StorageVolumeState).order_by(StorageVolumeState.source_key, StorageVolumeState.name):
        if v.source_key not in arrays:
            continue
        mapped = ds_by_vol.get(v.id, [])
        pct = v.used_gb * 100 / v.size_gb if v.size_gb and v.used_gb is not None else None
        vols.append({"sourceKey": v.source_key, "array": arrays[v.source_key].name if v.source_key in arrays else v.source_key,
                     "name": v.name, "container": v.container, "kind": v.kind, "sizeGB": v.size_gb, "usedGB": v.used_gb,
                     "usedPct": round(pct, 1) if pct is not None else None, "physicalGB": v.physical_gb, "naa": v.naa, "nfsPath": v.nfs_path,
                     "datastores": mapped, "vmCount": sum(d["vmCount"] for d in mapped)})
    return {"arrays": out_arrays, "volumes": vols}

