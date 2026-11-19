"""Cổng Owner (ICO v2 G2c) — the only API an owner-scope user can call besides /api/auth/me and sign-out.

GET /api/owner/systems            — systems the caller owns / deputises (full users: every active system)
GET /api/owner/vms?system=<id>    — VMs of those systems; a system outside the caller's scope answers 404, the same
                                    as an unknown id (no way to tell a foreign system from a missing one)."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..cmdb.models import Ci
from ..database import get_db
from ..owners import service
from ..owners.access import OwnerPrincipal, get_owner_user
from ..owners.models import System, SystemMember

router = APIRouter(prefix="/api/owner", tags=["owner-portal"])
MAX_VMS = 5000


def _scope(db: Session, p: OwnerPrincipal):
    q = db.query(System).filter(System.status == "active")
    if p.system_ids is not None:
        if not p.system_ids:
            return []
        q = q.filter(System.id.in_(sorted(p.system_ids)))
    return q.order_by(System.criticality, System.name, System.id).all()


@router.get("/systems")
def my_systems(p: OwnerPrincipal = Depends(get_owner_user), db: Session = Depends(get_db)):
    systems = _scope(db, p)
    counts = service.member_counts(db, (s.id for s in systems))
    return {"items": [service.out(s, counts.get(s.id, 0)) for s in systems], "accessScope": p.user.access_scope or "full"}


@router.get("/vms")
def my_vms(system: Optional[int] = Query(default=None, ge=1), p: OwnerPrincipal = Depends(get_owner_user),
           db: Session = Depends(get_db)):
    if system is not None:
        s = db.get(System, system)
        if s is None or s.status != "active" or not p.allows(s.id):
            raise HTTPException(404, "Không có hệ thống này.")
        ids = [s.id]
    else:
        ids = [s.id for s in _scope(db, p)]
    if not ids:
        return {"items": [], "total": 0}
    rows = (db.query(Ci, System.id, System.code, System.name).join(SystemMember, SystemMember.ci_id == Ci.id)
            .join(System, System.id == SystemMember.system_id)
            .filter(SystemMember.system_id.in_(ids), Ci.type == "vm", Ci.status == "active")
            .order_by(System.code, Ci.name, Ci.id).limit(MAX_VMS + 1).all())
    items = []
    for c, sid, code, name in rows[:MAX_VMS]:
        a = json.loads(c.attrs or "{}")
        items.append({"ciId": c.id, "vm": c.name, "vcenterKey": a.get("vcenterKey") or "", "cluster": a.get("cluster"),
                      "powerOn": a.get("powerOn"), "vcpu": a.get("vcpu"), "ramGB": a.get("ramGB"),
                      "systemId": sid, "systemCode": code, "systemName": name})
    return {"items": items, "total": len(items), "truncated": len(rows) > MAX_VMS}
