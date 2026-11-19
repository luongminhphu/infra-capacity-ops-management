"""Cài đặt → Rà soát owner (ICO v2 G3a): review campaigns, their items and the e-mails (preview / outbox).

GET  /api/reviews                                   — campaigns (owner admin)
POST /api/reviews {periodLabel?, dueDays?, dryRun?} — open a campaign now (admin; one per period)
GET  /api/reviews/{id}                              — counts, VMs without owner, e-mails (owner admin)
GET  /api/reviews/{id}/items?owner=&flagged=        — items (owner admin)
GET  /api/reviews/{id}/notices/{nid}                — one e-mail: subject, text, HTML (preview; owner admin)
POST /api/reviews/{id}/send                         — leave dry run and send the e-mails (admin)"""
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..owners import review
from ..owners.models import ReviewCampaign, ReviewItem, ReviewNotice
from ..security import require_admin
from .owners import require_owner_admin

router = APIRouter(prefix="/api/reviews", tags=["owner-reviews"])


def _c(db: Session, cid: int) -> ReviewCampaign:
    c = db.get(ReviewCampaign, cid)
    if c is None:
        raise HTTPException(404, "Không có kỳ rà soát này.")
    return c


def _out(c: ReviewCampaign) -> Dict[str, Any]:
    return {"id": c.id, "periodLabel": c.period_label, "openedAt": c.opened_at.isoformat() + "Z", "openedBy": c.opened_by,
            "dueAt": c.due_at.isoformat(), "closedAt": c.closed_at.isoformat() + "Z" if c.closed_at else None, "status": c.status,
            "dryRun": bool(c.dry_run), "itemCount": c.item_count, "portalLink": review.portal_link(c.id)}


def _item(i: ReviewItem) -> Dict[str, Any]:
    return {"id": i.id, "ciId": i.ci_id, "vm": i.vm_name, "vcenterKey": i.vcenter_key, "systemCode": i.system_code,
            "systemName": i.system_name, "ownerEmail": i.owner_email, "signals": json.loads(i.signals or "{}"),
            "flagged": bool(i.flagged), "reasons": json.loads(i.reasons or "[]"), "decision": i.decision}


def _notice(n: ReviewNotice) -> Dict[str, Any]:
    return {"id": n.id, "recipient": n.recipient, "kind": n.kind, "status": n.status, "subject": n.subject, "vmCount": n.vm_count,
            "attempts": n.attempts, "sentAt": n.sent_at.isoformat() + "Z" if n.sent_at else None, "error": n.error}


@router.get("")
def list_campaigns(user: User = Depends(require_owner_admin), db: Session = Depends(get_db)):
    rows = db.query(ReviewCampaign).order_by(ReviewCampaign.opened_at.desc(), ReviewCampaign.id.desc()).limit(50).all()
    return {"items": [_out(c) for c in rows], "nextLabel": review.period_label(review.today_vn()),
            "startMonths": review.start_months()}


@router.post("")
def open_now(data: Dict[str, Any] = Body(default={}), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    label = data.get("periodLabel")
    if label is not None and (not isinstance(label, str) or not 1 <= len(label.strip()) <= 16):
        raise HTTPException(422, "Tên kỳ 1–16 ký tự (vd 2027-H1).")
    due = data.get("dueDays")
    if due is not None and (not isinstance(due, int) or isinstance(due, bool) or not 1 <= due <= 120):
        raise HTTPException(422, "Hạn xác nhận 1–120 ngày.")
    dry = data.get("dryRun")
    if dry is not None and not isinstance(dry, bool):
        raise HTTPException(422, "dryRun phải là true / false.")
    try:
        c = review.open_campaign(db, label.strip() if label else None, due, dry, by=user.username)
    except review.Exists as exc:
        raise HTTPException(409, str(exc))
    return _out(c)


@router.get("/{cid}")
def campaign(cid: int, user: User = Depends(require_owner_admin), db: Session = Depends(get_db)):
    c = _c(db, cid)
    base = db.query(ReviewItem).filter(ReviewItem.campaign_id == c.id)
    owned = base.filter(ReviewItem.owner_email.isnot(None)).count()
    flagged = base.filter(ReviewItem.flagged.is_(True)).count()
    unowned = base.filter(ReviewItem.owner_email.is_(None)).order_by(ReviewItem.vm_name).limit(1000).all()
    by_status = dict(db.query(ReviewNotice.status, func.count(ReviewNotice.id)).filter(ReviewNotice.campaign_id == c.id)
                     .group_by(ReviewNotice.status))
    notices = db.query(ReviewNotice).filter(ReviewNotice.campaign_id == c.id).order_by(ReviewNotice.recipient, ReviewNotice.kind).all()
    return {**_out(c), "counts": {"items": c.item_count, "owned": owned, "unowned": c.item_count - owned, "flagged": flagged,
                                  "owners": len({n.recipient for n in notices}), "notices": by_status},
            "unowned": [_item(i) for i in unowned], "notices": [_notice(n) for n in notices]}


@router.get("/{cid}/items")
def items(cid: int, owner: Optional[str] = Query(default=None, max_length=255), flagged: Optional[bool] = None,
          user: User = Depends(require_owner_admin), db: Session = Depends(get_db)):
    c = _c(db, cid)
    q = db.query(ReviewItem).filter(ReviewItem.campaign_id == c.id)
    if owner:
        q = q.filter(ReviewItem.owner_email == owner.strip().lower())
    if flagged is not None:
        q = q.filter(ReviewItem.flagged.is_(flagged))
    return {"items": [_item(i) for i in q.order_by(ReviewItem.owner_email, ReviewItem.flagged.desc(), ReviewItem.vm_name).limit(5000)]}


@router.get("/{cid}/notices/{nid}")
def notice(cid: int, nid: int, user: User = Depends(require_owner_admin), db: Session = Depends(get_db)):
    n = db.get(ReviewNotice, nid)
    if n is None or n.campaign_id != cid:
        raise HTTPException(404, "Không có email này.")
    return {**_notice(n), "bodyText": n.body_text, "bodyHtml": n.body_html}


@router.post("/{cid}/send")
def send(cid: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    c = _c(db, cid)
    try:
        return review.send_campaign(db, c, user.username)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
