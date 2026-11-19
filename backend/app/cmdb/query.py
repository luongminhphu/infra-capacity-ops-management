"""Read side of the CI registry for the API and (later) Hỏi ICO — always with the caller's features and roles:
backup clients need the Backup feature, Opex items need the Chi phí feature and a spend role (like services/entities.py)."""
import json
from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException
from sqlalchemy import func, or_, select, true
from sqlalchemy.orm import Session, aliased

from ..features import get_features, is_on
from ..models import User
from ..spend.roles import has_role
from .models import CI_STATUSES, CI_TYPES, Ci, CiKey, CiLink

MAX_LIMIT = 200
MAX_LINKS = 1000             # per direction on the detail page (a datastore can carry hundreds of VMs)


def hidden_types(db: Session, user: User) -> Set[str]:
    features = get_features(db)
    hidden = set()
    if not is_on(features, "backup"):
        hidden.add("backup_client")
    if not (is_on(features, "spend") and has_role(user, "viewer")):
        hidden.add("opex_item")
    return hidden


def _like(q: str) -> str:
    return "%" + q.replace("\\", " ").replace("%", " ").replace("_", " ").lower() + "%"


def _brief(c: Ci) -> Dict[str, Any]:
    return {"id": c.id, "type": c.type, "name": c.name, "status": c.status}


def _out(c: Ci) -> Dict[str, Any]:
    return {**_brief(c), "sourceKind": c.source_kind, "sourceKey": c.source_key,
            "firstSeen": c.first_seen.isoformat() + "Z" if c.first_seen else None,
            "lastSeen": c.last_seen.isoformat() + "Z" if c.last_seen else None,
            "attrs": json.loads(c.attrs or "{}")}


def list_cis(db: Session, user: User, type_: Optional[str], q: Optional[str], status: str, limit: int) -> Dict[str, Any]:
    if type_ and type_ not in CI_TYPES:
        raise HTTPException(422, "Loại CI không hợp lệ.")
    if status not in CI_STATUSES + ("all",):
        raise HTTPException(422, "Trạng thái không hợp lệ (active, retired, all).")
    hidden = hidden_types(db, user)
    if type_ in hidden:
        return {"items": [], "total": 0}
    qry = db.query(Ci).filter(Ci.type.notin_(hidden) if hidden else true())
    if type_:
        qry = qry.filter(Ci.type == type_)
    if status != "all":
        qry = qry.filter(Ci.status == status)
    term = " ".join((q or "").split())
    if term:
        pat = _like(term)
        qry = qry.filter(or_(func.lower(Ci.name).like(pat),
                             Ci.id.in_(select(CiKey.ci_id).where(func.lower(CiKey.value_norm).like(pat)))))
    total = qry.count()
    rows = qry.order_by(Ci.type, Ci.name, Ci.id).limit(max(1, min(int(limit), MAX_LIMIT))).all()
    return {"items": [_out(c) for c in rows], "total": total}


def get_ci(db: Session, user: User, ci_id: int) -> Dict[str, Any]:
    hidden = hidden_types(db, user)
    c = db.get(Ci, ci_id)
    if c is None or c.type in hidden:
        raise HTTPException(404, "Không có CI này.")
    keys = [{"kind": k, "value": v} for k, v in
            db.query(CiKey.kind, CiKey.value_norm).filter(CiKey.ci_id == ci_id).order_by(CiKey.kind, CiKey.value_norm)]
    peer = aliased(Ci)
    links: List[Dict[str, Any]] = []
    truncated = False
    for direction, mine, other in (("out", CiLink.from_ci, CiLink.to_ci), ("in", CiLink.to_ci, CiLink.from_ci)):
        qry = (db.query(CiLink, peer).join(peer, peer.id == other).filter(mine == ci_id)
               .filter(peer.type.notin_(hidden) if hidden else true())
               .order_by(CiLink.relation, peer.type, peer.name).limit(MAX_LINKS + 1))
        rows = qry.all()
        truncated = truncated or len(rows) > MAX_LINKS
        links += [{"direction": direction, "relation": lk.relation, "confidence": lk.confidence, "source": lk.source,
                   "attrs": json.loads(lk.attrs or "{}"), "peer": _brief(p)} for lk, p in rows[:MAX_LINKS]]
    return {**_out(c), "keys": keys, "links": links, "linksTruncated": truncated}


def stats(db: Session, user: User) -> Dict[str, Any]:
    hidden = hidden_types(db, user)
    by_type = {t: 0 for t in CI_TYPES if t not in hidden}
    retired = 0
    for t, st, n in db.query(Ci.type, Ci.status, func.count(Ci.id)).group_by(Ci.type, Ci.status):
        if t in hidden:
            continue
        if st == "active":
            by_type[t] = n
        else:
            retired += n
    hosts = by_type.get("hypervisor_host", 0)
    with_serial = (db.query(func.count(func.distinct(CiKey.ci_id))).join(Ci, Ci.id == CiKey.ci_id)
                   .filter(Ci.type == "hypervisor_host", Ci.status == "active", CiKey.kind == "serial").scalar() or 0)
    a, b = aliased(Ci), aliased(Ci)
    weak_q = (db.query(func.count(CiLink.id)).join(a, a.id == CiLink.from_ci).join(b, b.id == CiLink.to_ci)
              .filter(CiLink.confidence == "weak"))
    if hidden:
        weak_q = weak_q.filter(a.type.notin_(hidden), b.type.notin_(hidden))
    return {"byType": by_type, "active": sum(by_type.values()), "retired": retired,
            "hosts": hosts, "hostsWithSerial": with_serial,
            "hostSerialPct": round(with_serial * 100.0 / hosts, 1) if hosts else None,
            "weakLinks": weak_q.scalar() or 0}
