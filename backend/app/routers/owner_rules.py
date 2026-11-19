"""Cài đặt → Owner (ICO v2 G2b): vCenter attributes used as owner source, assignment rules, proposals.
Every endpoint needs admin or "Quản trị Hệ thống & Owner".

GET  /api/systems/rules                    — rules + attribute names + current plan counts
POST /api/systems/rules/preview            — Chạy thử: {rules, attributes} (draft) → diff, nothing written
PUT  /api/systems/rules                    — save {rules, attributes}, then apply → result
POST /api/systems/rules/run                — apply the saved rules now
GET  /api/systems/proposals?status=open    — attribute values naming no known system / owner
POST /api/systems/proposals/{id}/accept    — create the system (body = system fields), re-run the rules
POST /api/systems/proposals/{id}/dismiss
Registered before routers/owners.py: these paths must not be read as /api/systems/{id}."""
import datetime as dt
import json
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..owners import rules as engine, service
from ..owners.models import OwnerProposal, OwnerRule, System
from .owners import require_owner_admin

router = APIRouter(prefix="/api/systems", tags=["owners"])


def _draft(db: Session, data: Dict[str, Any]):
    raw = data.get("rules")
    if not isinstance(raw, list) or len(raw) > engine.MAX_RULES:
        raise HTTPException(422, f"rules phải là danh sách (tối đa {engine.MAX_RULES}).")
    ids = {i for (i,) in db.query(System.id)}
    try:
        rules = []
        for i, d in enumerate(raw):
            if not isinstance(d, dict):
                raise engine.Invalid("Quy tắc không hợp lệ.")
            try:
                rules.append(engine.clean_rule(d, ids))
            except engine.Invalid as exc:
                raise engine.Invalid(f"Quy tắc {i + 1}: {exc}") from None
    except engine.Invalid as exc:
        raise HTTPException(400, str(exc))
    att = data.get("attributes") if isinstance(data.get("attributes"), dict) else engine.attr_names(db)
    names = {k: " ".join(str(att.get(k) or "").split())[:64] for k in engine.DEFAULT_ATTRS}
    return rules, names


@router.get("/rules")
def get_rules(user: User = Depends(require_owner_admin), db: Session = Depends(get_db)):
    p = engine.plan(db)
    return {"rules": [engine.rule_out(r) for r in db.query(OwnerRule).order_by(OwnerRule.priority, OwnerRule.id)],
            "attributes": engine.attr_names(db), "kinds": [{"value": k, "label": engine.KIND_LABELS[k]} for k in engine.UI_KINDS],
            "pending": p["counts"]}


@router.post("/rules/preview")
def preview_rules(data: Dict[str, Any] = Body(...), user: User = Depends(require_owner_admin), db: Session = Depends(get_db)):
    rules, names = _draft(db, data)
    return engine.public_plan(db, engine.plan(db, [dict(r, id=i) for i, r in enumerate(rules)], names))


@router.put("/rules")
def save_rules(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_owner_admin),
               db: Session = Depends(get_db)):
    rules, names = _draft(db, data)
    db.query(OwnerRule).delete(synchronize_session=False)
    for r in rules:
        db.add(OwnerRule(**r))
    engine.save_attr_names(db, names)
    service.audit(db, user, "owners.rules.save", request.client.host if request.client else None, rules=len(rules), attributes=names)
    db.commit()
    return {"ok": True, "applied": engine.run(db, user.username)}


@router.post("/rules/run")
def run_rules(user: User = Depends(require_owner_admin), db: Session = Depends(get_db)):
    return engine.run(db, user.username)


def _proposal_out(p: OwnerProposal, systems: Dict[int, str]) -> Dict[str, Any]:
    return {"id": p.id, "kind": p.key.split(":", 1)[0], "systemName": p.system_name, "ownerEmail": p.owner_email,
            "vmCount": p.vm_count, "sample": json.loads(p.sample or "[]"), "status": p.status,
            "lastSeen": p.last_seen.isoformat() + "Z" if p.last_seen else None, "decidedBy": p.decided_by,
            "system": systems.get(p.system_id)}


@router.get("/proposals")
def list_proposals(status: str = Query("open", max_length=10), user: User = Depends(require_owner_admin),
                   db: Session = Depends(get_db)):
    q = db.query(OwnerProposal)
    if status != "all":
        q = q.filter(OwnerProposal.status == status)
    systems = {s.id: f"{s.code} — {s.name}" for s in db.query(System)}
    return {"items": [_proposal_out(p, systems) for p in q.order_by(OwnerProposal.vm_count.desc(), OwnerProposal.id).limit(500)]}


def _open(db: Session, pid: int) -> OwnerProposal:
    p = db.get(OwnerProposal, pid)
    if p is None:
        raise HTTPException(404, "Không có đề xuất này.")
    if p.status != "open":
        raise HTTPException(409, "Đề xuất đã được xử lý.")
    return p


@router.post("/proposals/{pid}/accept")
def accept_proposal(pid: int, request: Request, data: Dict[str, Any] = Body(default={}), user: User = Depends(require_owner_admin),
                    db: Session = Depends(get_db)):
    p = _open(db, pid)
    body = {"name": p.system_name or "", "ownerEmail": p.owner_email or "", **{k: v for k, v in data.items() if k in service.FIELDS}}
    try:
        s = service.create(db, user, body, request.client.host if request.client else None)
    except service.Invalid as exc:
        raise HTTPException(400, str(exc))
    p.status, p.system_id, p.decided_by, p.decided_at = "accepted", s.id, user.username, dt.datetime.utcnow()
    db.commit()
    return {"system": service.out(s, 0), "applied": engine.run(db, user.username)}


@router.post("/proposals/{pid}/dismiss")
def dismiss_proposal(pid: int, request: Request, user: User = Depends(require_owner_admin), db: Session = Depends(get_db)):
    p = _open(db, pid)
    p.status, p.decided_by, p.decided_at = "dismissed", user.username, dt.datetime.utcnow()
    service.audit(db, user, "owners.proposal.dismiss", request.client.host if request.client else None, key=p.key)
    db.commit()
    return {"ok": True}
