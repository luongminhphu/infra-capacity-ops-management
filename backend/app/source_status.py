"""Connection status of every configured data source (top bar + Cài đặt → Nguồn dữ liệu), from the last real sync:

  ok     — the last attempt worked and its data is fresh (within STALE_FACTOR × the poll interval, at least 3 h);
  error  — the last attempt failed (its message, written for operators, never a secret);
  stale  — the last good sync is older than that (scheduler stopped, source skipped / busy for a long time);
  never  — no sync yet (a source added since the last round: Đồng bộ ngay reads it at once).

Families: virtualisation (vCenter / Proxmox VE / Nutanix — snapshot_runs), backup (NetBackup — backup_sync_runs),
storage (Pure / NetApp — storage_array_states), hardware (Dell OME — hw_source_state). Reads the database only.
"""
import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

FAMILIES = {"virtualization": "Ảo hoá", "backup": "Backup", "storage": "Storage", "hardware": "Dell OME", "ipam": "IPAM", "itsm": "ServiceDesk"}
KIND_LABELS = {"vcenter": "vCenter", "proxmox": "Proxmox VE", "nutanix": "Nutanix", "netbackup": "NetBackup",
               "pure": "Pure FlashArray", "netapp": "NetApp ONTAP", "ome": "Dell OME", "gestioip": "GestióIP", "sdp": "ServiceDesk Plus"}
STATE_TEXT = {"ok": "Kết nối tốt", "error": "Lỗi kết nối", "stale": "Dữ liệu cũ", "never": "Chưa đồng bộ"}
STATE_LEVEL = {"ok": "ok", "error": "crit", "stale": "warn", "never": "na"}
STALE_FACTOR = 3
MIN_STALE = dt.timedelta(hours=3)


def _iso(v: Optional[dt.datetime]) -> Optional[str]:
    return v.isoformat() + "Z" if v else None


def state_of(last_ok: Optional[dt.datetime], last_error: Optional[dt.datetime], now: dt.datetime,
             stale_after: dt.timedelta) -> str:
    """Pure: state from the last good and the last failed sync times."""
    if last_error is not None and (last_ok is None or last_error >= last_ok):
        return "error"
    if last_ok is None:
        return "never"
    return "stale" if now - last_ok > stale_after else "ok"


def stale_after() -> dt.timedelta:
    from .config import get_settings
    return max(MIN_STALE, dt.timedelta(minutes=STALE_FACTOR * max(1, int(get_settings().poll_interval_minutes))))


def _runs(db: Session, model, key_col, keys: List[str]) -> Dict[str, Dict[str, Any]]:
    """key → {lastOk, lastError, error, lastSync} from a runs table (status ok | error), three grouped queries."""
    out: Dict[str, Dict[str, Any]] = {k: {"lastOk": None, "lastError": None, "error": None, "lastSync": None} for k in keys}
    if not keys:
        return out
    col = getattr(model, key_col)
    when = func.coalesce(model.finished_at, model.started_at)
    for k, at in db.query(col, func.max(when)).filter(col.in_(keys), model.status == "ok").group_by(col):
        out[k]["lastOk"] = at
    last_err = dict(db.query(col, func.max(model.id)).filter(col.in_(keys), model.status != "ok").group_by(col).all())
    if last_err:
        for r in db.query(model).filter(model.id.in_(list(last_err.values()))):
            k = getattr(r, key_col)
            out[k]["lastError"] = r.finished_at or r.started_at
            out[k]["error"] = (r.error_message or "Đồng bộ thất bại.")[:500]
    for d in out.values():
        d["lastSync"] = max([x for x in (d["lastOk"], d["lastError"]) if x is not None], default=None)
    return out


def _states(db: Session, model, keys: List[str]) -> Dict[str, Dict[str, Any]]:
    """key → status from a per-source state row (last_ok_at / last_error / last_error_at)."""
    out: Dict[str, Dict[str, Any]] = {k: {"lastOk": None, "lastError": None, "error": None, "lastSync": None} for k in keys}
    for r in db.query(model).filter(model.source_key.in_(keys or [""])):
        d = out[r.source_key]
        d["lastOk"], d["lastError"] = r.last_ok_at, r.last_error_at if r.last_error else None
        d["error"] = (r.last_error or "")[:500] or None
        d["lastSync"] = max([x for x in (d["lastOk"], d["lastError"]) if x is not None], default=None)
    return out


def configured() -> List[Tuple[str, str, Any]]:
    """(family, kind, config) of every source that is polled (environment + enabled UI rows)."""
    from .config import get_settings
    s = get_settings()
    out: List[Tuple[str, str, Any]] = []
    for c in s.vcenters():
        out.append(("virtualization", {"vmware": "vcenter"}.get(getattr(c, "platform", "vmware"), getattr(c, "platform", "vcenter")), c))
    out += [("backup", "netbackup", c) for c in s.netbackups()]
    out += [("storage", c.vendor, c) for c in s.storages()]
    out += [("hardware", getattr(c, "kind", "ome"), c) for c in s.hardware()]
    out += [("ipam", getattr(c, "kind", "gestioip"), c) for c in s.ipam()]
    out += [("itsm", getattr(c, "kind", "sdp"), c) for c in s.itsm()]
    return out


def status_all(db: Session, now: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    from .hardware.models import HwSourceState
    from .ipam.models import IpamSourceState
    from .itsm.models import ItsmSourceState
    from .models import BackupSyncRun, SnapshotRun, StorageArrayState
    now = now or dt.datetime.utcnow()
    srcs = configured()
    keys = lambda fam: [c.key for f, _k, c in srcs if f == fam]  # noqa: E731
    data = {"virtualization": _runs(db, SnapshotRun, "vcenter_key", keys("virtualization")),
            "backup": _runs(db, BackupSyncRun, "nbu_key", keys("backup")),
            "storage": _states(db, StorageArrayState, keys("storage")),
            "hardware": _states(db, HwSourceState, keys("hardware")),
            "ipam": _states(db, IpamSourceState, keys("ipam")),
            "itsm": _states(db, ItsmSourceState, keys("itsm"))}
    limit = stale_after()
    out = []
    for fam, kind, c in srcs:
        d = data[fam].get(c.key) or {}
        st = state_of(d.get("lastOk"), d.get("lastError"), now, limit)
        out.append({"family": fam, "familyLabel": FAMILIES[fam], "kind": kind, "kindLabel": KIND_LABELS.get(kind, kind),
                    "key": c.key, "name": c.name or c.key, "demo": bool(getattr(c, "demo", False)), "state": st,
                    "stateText": STATE_TEXT[st], "level": STATE_LEVEL[st], "lastSyncAt": _iso(d.get("lastSync")),
                    "lastOkAt": _iso(d.get("lastOk")), "lastErrorAt": _iso(d.get("lastError")),
                    "error": d.get("error") if st == "error" else None})
    return out


def summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per family: total, ok, worst state (error > stale > never > ok) — the top bar chips."""
    rank = {"error": 0, "stale": 1, "never": 2, "ok": 3}
    fams = []
    for fam, label in FAMILIES.items():
        rows = [i for i in items if i["family"] == fam]
        if not rows:
            continue
        worst = min((i["state"] for i in rows), key=lambda s: rank[s])
        fams.append({"family": fam, "label": label, "total": len(rows), "ok": sum(1 for i in rows if i["state"] == "ok"),
                     "state": worst, "level": STATE_LEVEL[worst]})
    worst = min((i["state"] for i in items), key=lambda s: rank[s]) if items else "never"
    return {"total": len(items), "ok": sum(1 for i in items if i["state"] == "ok"), "state": worst,
            "level": STATE_LEVEL[worst], "families": fams}
