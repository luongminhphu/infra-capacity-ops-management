"""Change log (ICO v2 G6): vCenter events stored per vCenter with a read cursor, the CI they are about, and the reads of
the "Nhật ký thay đổi" page, the entity card and the incident page ("thay đổi trong 24 giờ trước sự cố").

Events carry the person who did it; the snapshot comparison (history.get_vm_changes) only sees the result. The page shows
the events, then the snapshot changes no event explains (added / removed / resized VMs without a matching event)."""
import datetime as dt
import json
from typing import Any, Dict, Iterable, List, Optional, Set

from sqlalchemy.orm import Session

from ..cmdb.models import vc_key
from ..timeutil import to_iso_utc as _iso
from .models import CHANGE_KINDS, ChangeCursor, ChangeLog

KIND_LABELS = {"vm_created": "Tạo VM", "vm_removed": "Xoá VM", "vm_reconfigured": "Đổi cấu hình VM", "vm_migrated": "Di chuyển VM",
               "vm_powered_on": "Bật VM", "vm_powered_off": "Tắt VM", "host_connection_lost": "Host mất kết nối",
               "host_maintenance_enter": "Host vào bảo trì", "host_maintenance_exit": "Host ra khỏi bảo trì",
               "ha_vm_restarted": "HA khởi động lại VM", "ha_host_failed": "HA: host lỗi", "alarm_status": "Alarm đổi trạng thái"}
# Changes that are incidents by themselves (no "cleared" state): turned into ops_event rows by events.service.run.
POINT_LEVEL = {"ha_host_failed": "crit", "host_connection_lost": "crit", "ha_vm_restarted": "bad"}
BEFORE_INCIDENT = dt.timedelta(hours=24)
MAX_LIST = 1000


def cursor(db: Session, vcenter: str) -> Optional[dt.datetime]:
    row = db.get(ChangeCursor, vcenter)
    return row.until if row else None


def store(db: Session, vcenter: str, events: Optional[List[Dict[str, Any]]], until: Optional[dt.datetime]) -> int:
    """Insert the events not stored yet (UNIQUE vCenter + event id) with their CI, move the cursor to `until`.
    events None = the read failed: nothing changes, the next round reads the same window. No commit (the sync commits)."""
    if events is None or until is None:
        return 0
    have = set()
    ids = [e["id"] for e in events]
    for i in range(0, len(ids), 500):
        have |= {x for (x,) in db.query(ChangeLog.vc_event_id).filter(ChangeLog.vcenter_key == vcenter,
                                                                      ChangeLog.vc_event_id.in_(ids[i:i + 500]))}
    new = [e for e in events if e["id"] not in have and e["kind"] in CHANGE_KINDS]
    if new:
        from .service import CiResolver
        resolve = CiResolver(db)
        hint = {"vm": lambda n: {"type": "vm", "name": n, "vc": vcenter},
                "host": lambda n: {"type": "hypervisor_host", "key": vc_key(vcenter, n)}}
        db.bulk_insert_mappings(ChangeLog, [{
            "at": e["at"], "vcenter_key": vcenter, "vc_event_id": e["id"], "kind": e["kind"], "user_name": (e.get("userName") or None),
            "object_type": e.get("objectType"), "object_name": (e.get("objectName") or "")[:255] or None,
            "message": (e.get("message") or "")[:1000] or None, "detail": json.dumps(e.get("detail") or {}, ensure_ascii=False),
            "ci_id": resolve(hint[e["objectType"]](e["objectName"])) if e.get("objectType") in hint and e.get("objectName") else None,
            "handled": e["kind"] not in POINT_LEVEL} for e in new])
    row = db.get(ChangeCursor, vcenter)
    if row is None:
        db.add(ChangeCursor(vcenter_key=vcenter, until=until))
        db.flush()                                    # autoflush is off: a second store before commit must see it
    elif until > row.until:
        row.until = until
    return len(new)


def out(r: ChangeLog, cis: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    return {"id": r.id, "at": _iso(r.at), "vcenterKey": r.vcenter_key, "kind": r.kind, "kindLabel": KIND_LABELS.get(r.kind, r.kind),
            "user": r.user_name, "objectType": r.object_type, "object": r.object_name, "message": r.message,
            "detail": json.loads(r.detail or "{}"), "ci": cis.get(r.ci_id), "source": "event"}


def _rows(db: Session, rows: List[ChangeLog]) -> List[Dict[str, Any]]:
    """Output rows with their CI and, for VMs, the IP next to the name (rule 9)."""
    from .. import vm_ip
    from .service import _ci_names
    cis = _ci_names(db, (r.ci_id for r in rows))
    items = [out(r, cis) for r in rows]
    vm_ip.attach(db, items, vm_key="object", only=lambda i: i["objectType"] == "vm")
    return items


def listing(db: Session, kind: Optional[str] = None, user: Optional[str] = None, days: int = 7, q: Optional[str] = None,
            osc=None, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Nhật ký thay đổi: events (newest first) + snapshot changes no event explains."""
    from .service import scope_ci_ids
    now = now or dt.datetime.utcnow()
    since = now - dt.timedelta(days=days)
    qry = db.query(ChangeLog).filter(ChangeLog.at >= since)
    if kind:
        qry = qry.filter(ChangeLog.kind == kind)
    if user:
        qry = qry.filter(ChangeLog.user_name.ilike(f"%{user.strip()[:80]}%"))
    if q:
        qry = qry.filter(ChangeLog.object_name.ilike(f"%{q.strip()[:80]}%"))
    ids = scope_ci_ids(db, osc)
    if ids is not None:
        qry = qry.filter(ChangeLog.ci_id.in_(list(ids) or [-1]))
    rows = qry.order_by(ChangeLog.at.desc(), ChangeLog.id.desc()).limit(MAX_LIST + 1).all()
    users = sorted({u for (u,) in db.query(ChangeLog.user_name).filter(ChangeLog.at >= since, ChangeLog.user_name.isnot(None)).distinct()})
    snapshot = _unexplained(db, days, rows) if not (kind or user or ids is not None) else []
    return {"items": _rows(db, rows[:MAX_LIST]), "truncated": len(rows) > MAX_LIST, "users": users,
            "kinds": [{"key": k, "label": KIND_LABELS[k]} for k in CHANGE_KINDS], "snapshot": snapshot, "days": days}


def _unexplained(db: Session, days: int, events: List[ChangeLog]) -> List[Dict[str, Any]]:
    """VMs added / removed / resized between snapshots in the window that no create / remove / reconfigure event covers
    (changes made while the event read was off, or older than the first event window)."""
    from .. import history
    try:
        diff = history.get_vm_changes(db, None, max(1, round(days / 30)) if days >= 30 else 1)
    except Exception:  # noqa: BLE001 — the comparison is a complement; the events still show
        return []
    seen = {(r.kind, (r.object_name or "").lower()) for r in events}
    want = (("added", "vm_created", "Thêm VM (so sánh snapshot)"), ("removed", "vm_removed", "Bớt VM (so sánh snapshot)"),
            ("resized", "vm_reconfigured", "Đổi cấu hình (so sánh snapshot)"))
    out_rows = []
    for key, kind, label in want:
        for v in diff.get(key) or []:
            name = v.get("vm") or v.get("name")
            if name and (kind, name.lower()) not in seen:
                out_rows.append({"kind": kind, "kindLabel": label, "object": name, "objectType": "vm",
                                 "vcenterKey": v.get("vcenterKey"), "ip": v.get("ipAddress") or v.get("ip"), "source": "snapshot"})
    return out_rows[:MAX_LIST]


def for_ci(db: Session, ci_id: int, limit: int = 200) -> List[Dict[str, Any]]:
    rows = db.query(ChangeLog).filter(ChangeLog.ci_id == ci_id).order_by(ChangeLog.at.desc(), ChangeLog.id.desc()).limit(limit).all()
    return _rows(db, rows)


def before(db: Session, ci_ids: Iterable[int], at: dt.datetime, limit: int = 100) -> List[Dict[str, Any]]:
    """Changes on these CIs in the 24 hours before `at` (the incident page)."""
    ids: Set[int] = {i for i in ci_ids if i}
    if not ids:
        return []
    rows = (db.query(ChangeLog).filter(ChangeLog.ci_id.in_(list(ids)), ChangeLog.at >= at - BEFORE_INCIDENT, ChangeLog.at <= at)
            .order_by(ChangeLog.at.desc()).limit(limit).all())
    return _rows(db, rows)


def purge(db: Session, cutoff: dt.datetime) -> int:
    n = db.query(ChangeLog).filter(ChangeLog.at < cutoff).delete(synchronize_session=False)
    db.commit()
    return n
