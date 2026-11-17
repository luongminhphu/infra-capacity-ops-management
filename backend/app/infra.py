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
from .models import DatastoreState, HostHealthState, VcAlarmState, VmSnapshotState
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
        "available": bool(ds or hh),
    }

