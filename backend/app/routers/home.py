"""Trang chủ (hộp việc), Lịch việc & nhắc hạn, tuỳ chọn người dùng — super app SA2."""
import datetime as dt
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import inbox, ops_alerts
from ..database import get_db
from ..features import get_features
from ..models import InboxAck, User, UserPref
from ..security import get_current_user, is_admin, require_admin
from ..spend.fiscal import today_vn
from ..spend.roles import has_role

router = APIRouter(prefix="/api", tags=["home"])
MAX_PREFS_BYTES = 4096
MAX_ACKS = 500


def _clean_prefs(data: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if data.get("theme") in ("dark", "light"):
        out["theme"] = data["theme"]
    f = data.get("inboxFilter")
    if isinstance(f, dict):
        out["inboxFilter"] = {"level": f.get("level") if f.get("level") in ("", *inbox.LEVELS) else "",
                              "source": f.get("source") if f.get("source") in ("", *inbox.SOURCE_LABELS) else ""}
    if data.get("agendaDays") in (30, 60, 120, 180, 365):
        out["agendaDays"] = data["agendaDays"]
    return out


def _iso(d: Optional[dt.date]) -> Optional[str]:
    return d.isoformat() if d else None


@router.get("/inbox")
def get_inbox(show: str = Query(default="open", pattern="^(open|all|done)$"), fresh: bool = False,
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Items of every module the user may see; `open` hides items marked done or snoozed."""
    today = today_vn()
    items, errors = inbox.collect(db, use_cache=not fresh)
    features = get_features(db)
    acks = {a.item_key: a for a in db.query(InboxAck).filter(InboxAck.username == user.username)}
    out, counts = [], {k: 0 for k in inbox.LEVELS}
    for it in items:
        if not inbox.visible(it, user, features):
            continue
        a = acks.get(it["key"])
        hidden = bool(a and (a.state == "done" or (a.state == "snooze" and a.until and a.until >= today)))
        if (show == "open" and hidden) or (show == "done" and not hidden):
            continue
        if not hidden:
            counts[it["level"]] += 1
        out.append({**{k: v for k, v in it.items() if k not in ("role", "feature", "push")}, "due": _iso(it["due"]),
                    "levelLabel": inbox.LEVEL_LABELS[it["level"]], "sourceLabel": inbox.SOURCE_LABELS[it["source"]],
                    "ack": {"state": a.state, "until": _iso(a.until)} if a else None})
    sources = {s: {"label": inbox.SOURCE_LABELS[s], "worst": None, "count": 0} for s in inbox.SOURCE_LABELS}
    for it in out:
        if it["ack"] and show != "done":
            continue
        s = sources[it["source"]]
        s["count"] += 1
        if s["worst"] is None or inbox.LEVEL_RANK[it["level"]] < inbox.LEVEL_RANK[s["worst"]]:
            s["worst"] = it["level"]
    if features.get("backup") is False:
        sources.pop("backup")
    if features.get("license") is False:
        sources.pop("license")
    if features.get("spend") is False or not has_role(user, "viewer"):
        sources.pop("spend")
    if not is_admin(user):
        sources.pop("system")
    return {"items": out[:300], "counts": counts, "sources": sources, "unavailable": errors}


@router.post("/inbox/ack")
def ack_item(data: Dict[str, Any] = Body(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    key = str(data.get("key") or "")[:255]
    action = data.get("action")
    if not key or action not in ("done", "snooze", "undo"):
        raise HTTPException(400, "Thao tác không hợp lệ.")
    row = db.query(InboxAck).filter_by(username=user.username, item_key=key).first()
    if action == "undo":
        if row:
            db.delete(row)
        db.commit()
        return {"ok": True}
    until = None
    if action == "snooze":
        try:
            days = int(data.get("days") or 7)
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(400, "Số ngày tạm ẩn không hợp lệ.") from None
        if not 1 <= days <= 90:
            raise HTTPException(400, "Tạm ẩn từ 1 đến 90 ngày.")
        until = today_vn() + dt.timedelta(days=days)
    if row is None and db.query(InboxAck).filter_by(username=user.username).count() >= MAX_ACKS:
        # keep the table small: forget the oldest marks of this user first
        oldest = db.query(InboxAck).filter_by(username=user.username).order_by(InboxAck.created_at).first()
        db.delete(oldest)
    row = row or InboxAck(username=user.username, item_key=key)
    row.state, row.until, row.created_at = action, until, dt.datetime.utcnow()
    db.add(row)
    db.commit()
    return {"ok": True, "until": _iso(until)}


@router.get("/agenda")
def agenda(days: int = Query(default=120, ge=7, le=400), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Dated events of the next `days` days (and overdue ones): Opex expiry, payment instalments,
    license expiry, quotation validity."""
    today = today_vn()
    end = today + dt.timedelta(days=days)
    features = get_features(db)
    out: List[Dict[str, Any]] = []

    def add(date, kind, title, detail, page, tone, query=""):
        out.append({"date": date.isoformat(), "daysLeft": (date - today).days, "kind": kind, "title": title, "detail": detail,
                    "page": page, "query": query, "tone": tone})

    def tone_of(d: dt.date, warn: int = 60, bad: int = 30) -> str:
        n = (d - today).days
        return "crit" if n < 0 else "bad" if n <= bad else "warn" if n <= warn else "ok"

    if features.get("license") is not False:
        from ..models import LicenseEntitlement
        for e in db.query(LicenseEntitlement).filter(LicenseEntitlement.valid_to.isnot(None)):
            d = e.valid_to.date()
            if d <= end and (d - today).days >= -90:
                add(d, "license", f"License {e.product} hết hạn", f"{e.quantity:g} {e.metric} · {e.scope_cluster or e.scope_vcenter or 'toàn bộ'}",
                    "lic-assess", tone_of(d))
    if features.get("spend") is not False and has_role(user, "viewer"):
        from ..spend import service
        from ..spend.models import Contract, ContractPayment, OpexItem, Quotation, QuoteRequest, SpendVendor
        vendors = {v.id: service.vendor_label(v) for v in db.query(SpendVendor)}
        for o in (db.query(OpexItem).filter(OpexItem.status.in_(("planned", "active")), OpexItem.end_date <= end,
                                            OpexItem.end_date >= today - dt.timedelta(days=90)).order_by(OpexItem.end_date).limit(400)):
            add(o.end_date, "opex", f"{o.code} hết hạn — {o.title}",
                f"{vendors.get(o.vendor_id, '—')} · {service.money_vn(o.amount_gross)}{' · dự kiến' if o.status == 'planned' else ''}",
                "spend-opex", tone_of(o.end_date), f"code={o.code}")
        for p, c in (db.query(ContractPayment, Contract).join(Contract, Contract.id == ContractPayment.contract_id)
                     .filter(ContractPayment.status.in_(("pending", "submitted")), ContractPayment.due_date.isnot(None),
                             ContractPayment.due_date <= end).order_by(ContractPayment.due_date).limit(400)):
            add(p.due_date, "payment", f"{c.code} — đợt {p.seq}: {p.title}",
                f"{vendors.get(c.vendor_id, '—')} · {service.money_vn(p.amount)} · {'đã nộp hồ sơ' if p.status == 'submitted' else 'chờ hồ sơ'}",
                "spend-contracts", tone_of(p.due_date, 30, 7))
        for q, r in (db.query(Quotation, QuoteRequest).join(QuoteRequest, QuoteRequest.id == Quotation.request_id)
                     .filter(QuoteRequest.status.in_(("draft", "comparing")), Quotation.valid_until.isnot(None),
                             Quotation.valid_until <= end, Quotation.valid_until >= today).limit(200)):
            add(q.valid_until, "quote", f"{r.code} — báo giá {vendors.get(q.vendor_id, '—')} hết hiệu lực", r.title,
                "spend-quotes", tone_of(q.valid_until, 14, 7))
    out.sort(key=lambda x: (x["date"], x["title"]))
    return {"today": today.isoformat(), "days": days, "items": out}


@router.get("/me/prefs")
def get_prefs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.get(UserPref, user.username)
    try:
        return json.loads(row.data) if row else {}
    except ValueError:
        return {}


@router.put("/me/prefs")
def put_prefs(data: Dict[str, Any] = Body(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if len(json.dumps(data, ensure_ascii=False, default=str).encode()) > MAX_PREFS_BYTES:
        raise HTTPException(413, "Tuỳ chọn quá lớn.")
    clean = _clean_prefs(data)
    raw = json.dumps(clean, ensure_ascii=False)
    row = db.get(UserPref, user.username) or UserPref(username=user.username)
    row.data, row.updated_at = raw, dt.datetime.utcnow()
    db.add(row)
    db.commit()
    return clean


@router.post("/admin/ops-alerts/run")
def run_ops_alerts(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Check operations alerts now (normally after each scheduled sync)."""
    return ops_alerts.check_and_notify(db)
