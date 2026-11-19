"""Cổng Owner (ICO v2 G2c) — the only API an owner-scope user can call besides /api/auth/me and sign-out.

GET /api/owner/systems            — systems the caller owns / deputises (full users: every active system)
GET /api/owner/vms?system=<id>    — VMs of those systems; a system outside the caller's scope answers 404, the same
                                    as an unknown id (no way to tell a foreign system from a missing one).
GET /api/owner/reviews · /api/owner/reviews/{id} · PUT /api/owner/reviews/{id}/items  — the owner's review (G3b):
                                    only the caller's items; foreign campaign / item ids answer 404."""
import json
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
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


# ---------------------------------------------------------------- reviews (G3b)
def _item_out(i) -> dict:
    return {"id": i.id, "vm": i.vm_name, "vcenterKey": i.vcenter_key, "systemCode": i.system_code, "systemName": i.system_name,
            "ownerEmail": i.owner_email, "signals": json.loads(i.signals or "{}"), "flagged": bool(i.flagged),
            "reasons": json.loads(i.reasons or "[]"), "decision": i.decision,
            "actionDate": i.action_date.isoformat() if i.action_date else None, "newVcpu": i.new_vcpu, "newRamGB": i.new_ram_gb,
            "transferTo": i.transfer_to, "note": i.note, "decidedBy": i.decided_by,
            "decidedAt": i.decided_at.isoformat() + "Z" if i.decided_at else None}


def _campaign_or_404(db: Session, p: OwnerPrincipal, cid: int):
    from ..owners import review
    from ..owners.models import ReviewCampaign
    c = db.get(ReviewCampaign, cid)
    # a campaign with nothing for the caller looks the same as a missing one
    if c is None or not review.visible_items(db, p, c.id).first():
        raise HTTPException(404, "Không có kỳ rà soát này.")
    return c


@router.get("/reviews")
def my_reviews(p: OwnerPrincipal = Depends(get_owner_user), db: Session = Depends(get_db)):
    from ..owners import review
    from ..owners.models import ReviewCampaign
    out = []
    for c in db.query(ReviewCampaign).order_by(ReviewCampaign.opened_at.desc()).limit(20):
        q = review.visible_items(db, p, c.id)
        n = q.count()
        if n:
            out.append({"id": c.id, "periodLabel": c.period_label, "status": c.status, "dueAt": c.due_at.isoformat(),
                        "items": n, "decided": q.filter(review.ReviewItem.decision.isnot(None)).count()})
    return {"items": out}


@router.get("/reviews/{cid}")
def my_review(cid: int, p: OwnerPrincipal = Depends(get_owner_user), db: Session = Depends(get_db)):
    from ..owners import review
    c = _campaign_or_404(db, p, cid)
    rows = review.visible_items(db, p, c.id).order_by(review.ReviewItem.flagged.desc(), review.ReviewItem.vm_name).limit(MAX_VMS).all()
    return {"id": c.id, "periodLabel": c.period_label, "status": c.status, "dueAt": c.due_at.isoformat(),
            "editable": c.status == "open", "decisions": [{"value": k, "label": v} for k, v in review.DECISION_LABELS.items()],
            "items": [_item_out(i) for i in rows]}


@router.put("/reviews/{cid}/items")
def decide_mine(cid: int, data: dict = Body(...), p: OwnerPrincipal = Depends(get_owner_user), db: Session = Depends(get_db)):
    """One decision for several of the caller's items. Any id outside the caller's scope → 404 for the whole request."""
    from ..owners import review
    c = _campaign_or_404(db, p, cid)
    ids = data.get("itemIds")
    if not isinstance(ids, list) or not ids or len(ids) > 2000 or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise HTTPException(422, "itemIds phải là danh sách số nguyên (1–2000).")
    items = review.visible_items(db, p, c.id).filter(review.ReviewItem.id.in_(set(ids))).all()
    if len(items) != len(set(ids)):
        raise HTTPException(404, "Không có VM này trong kỳ rà soát của bạn.")
    try:
        n = review.decide(db, c, items, data, p.user.username)
    except review.Invalid as exc:
        raise HTTPException(409 if c.status != "open" else 400, str(exc))
    return {"saved": n}
