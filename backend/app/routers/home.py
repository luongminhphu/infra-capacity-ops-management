"""Trang chủ (hộp việc), Lịch việc & nhắc hạn, tuỳ chọn người dùng — super app SA2."""
import datetime as dt
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import inbox, ops_alerts
from ..database import get_db
from ..features import get_features, is_on
from ..models import AuditLog, InboxAck, InboxAssignment, User, UserPref
from ..security import get_current_user, is_admin, require_admin
from ..spend.fiscal import today_vn
from ..spend.roles import has_role
from ..shared.text import vn_g

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
    # Per-user view options that used to live in one browser's localStorage (ui.js uiPref).
    if data.get("historyMonths") in (3, 6, 12):
        out["historyMonths"] = data["historyMonths"]
    if data.get("historyGroup") in ("cluster", "vcenter"):
        out["historyGroup"] = data["historyGroup"]
    for key in ("reportAi", "reportArchive"):
        if isinstance(data.get(key), bool):
            out[key] = data[key]
    if isinstance(data.get("ownerScope"), str):                 # owner filter of the pages (G2c): JSON text, checked shape
        out["ownerScope"] = _clean_owner_scope(data["ownerScope"])
    return out


OWNER_SCOPE_KEYS = ("systems", "owners", "departments", "projects", "criticality")


def _clean_owner_scope(raw: str) -> str:
    try:
        d = json.loads(raw[:3000]) if raw else {}
    except ValueError:
        return ""
    if not isinstance(d, dict):
        return ""
    clean = {k: [str(x)[:255] for x in d[k][:50]] for k in OWNER_SCOPE_KEYS if isinstance(d.get(k), list)}
    return json.dumps(clean, ensure_ascii=False) if clean else ""


def _stored_prefs(row: Optional[UserPref]) -> Dict[str, Any]:
    try:
        data = json.loads(row.data) if row and row.data else {}
    except ValueError:
        return {}
    return _clean_prefs(data) if isinstance(data, dict) else {}


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
    assigned = _assignments(db, [it["key"] for it in items])
    from ..events.models import OpsEventOpen
    incident_of = {(src, key): iid for src, key, iid in db.query(OpsEventOpen.source, OpsEventOpen.event_key,
                                                                 OpsEventOpen.incident_id).filter(OpsEventOpen.incident_id.isnot(None))}
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
        iid = incident_of.get((it["source"], it["alertKey"]))
        links = it["links"] + ([{"label": f"Sự cố #{iid}", "page": "incidents", "query": f"id={iid}"}] if iid else [])
        out.append({**{k: v for k, v in it.items() if k not in ("role", "feature", "push", "ci")}, "links": links,
                    "due": _iso(it["due"]),
                    "levelLabel": inbox.LEVEL_LABELS[it["level"]], "sourceLabel": inbox.SOURCE_LABELS[it["source"]],
                    "ack": {"state": a.state, "until": _iso(a.until)} if a else None,
                    "assignee": assigned.get(it["key"])})
    sources = {s: {"label": inbox.SOURCE_LABELS[s], "worst": None, "count": 0} for s in inbox.SOURCE_LABELS}
    for it in out:
        if it["ack"] and show != "done":
            continue
        s = sources[it["source"]]
        s["count"] += 1
        if s["worst"] is None or inbox.LEVEL_RANK[it["level"]] < inbox.LEVEL_RANK[s["worst"]]:
            s["worst"] = it["level"]
    if not is_on(features, "backup"):
        sources.pop("backup")
    if not is_on(features, "license"):
        sources.pop("license")
    if not is_on(features, "spend") or not has_role(user, "viewer"):
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
    try:
        db.commit()
    except IntegrityError:          # double click / two tabs: the other request inserted it first (BEQ-09)
        db.rollback()
        row = db.query(InboxAck).filter_by(username=user.username, item_key=key).first()
        row.state, row.until, row.created_at = action, until, dt.datetime.utcnow()
        db.commit()
    return {"ok": True, "until": _iso(until)}


def _display(u: Optional[User], username: str) -> str:
    return (u.display_name or u.username) if u else username


def _assignments(db: Session, keys: List[str]) -> Dict[str, Dict[str, Any]]:
    """item key -> {username, displayName, assignedBy, assignedAt}; two queries whatever the item count."""
    if not keys:
        return {}
    rows = db.query(InboxAssignment).filter(InboxAssignment.item_key.in_(keys)).all()
    names = {r.assignee for r in rows} | {r.assigned_by for r in rows}
    users = {u.username: u for u in db.query(User).filter(User.username.in_(names))} if names else {}
    return {r.item_key: {"username": r.assignee, "displayName": _display(users.get(r.assignee), r.assignee),
                         "assignedBy": _display(users.get(r.assigned_by), r.assigned_by),
                         "assignedAt": r.assigned_at.isoformat() if r.assigned_at else None} for r in rows}


@router.get("/inbox/assignees")
def inbox_assignees(key: str = "", user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """People one item can be given to: only when the caller sees the item, and only people who see it too
    (name only — no email, role or login details). Not a user directory."""
    items, _ = inbox.collect(db)
    it = next((x for x in items if x["key"] == key[:255]), None) if key else None
    features = get_features(db)
    if it is None or not inbox.visible(it, user, features):
        raise HTTPException(404, "Không tìm thấy việc này, có thể nó đã được xử lý. Tải lại hộp việc.")
    users = db.query(User).order_by(User.display_name, User.username).limit(500).all()
    return [{"username": u.username, "displayName": u.display_name or u.username} for u in users
            if inbox.visible(it, u, features)]


@router.post("/inbox/assign")
def assign_item(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Set or clear "Ai xử lý" of an inbox item. The caller must see the item, and the assignee too
    (otherwise the item would vanish from their inbox)."""
    key = str(data.get("key") or "")[:255]
    target = data.get("assignee")
    if not key or (target is not None and not isinstance(target, str)):
        raise HTTPException(400, "Thao tác không hợp lệ.")
    items, _ = inbox.collect(db)
    it = next((x for x in items if x["key"] == key), None)
    features = get_features(db)
    if it is None or not inbox.visible(it, user, features):
        raise HTTPException(404, "Không tìm thấy việc này, có thể nó đã được xử lý. Tải lại hộp việc.")
    row = db.query(InboxAssignment).filter_by(item_key=key).first()
    if not target:
        if row:
            db.delete(row)
            db.add(AuditLog(username=user.username, action="inbox.unassign",
                            client_ip=request.client.host if request.client else None,
                            detail=json.dumps({"key": key, "was": row.assignee}, ensure_ascii=False)))
        db.commit()
        return {"ok": True, "assignee": None}
    who = db.query(User).filter(User.username == target[:255]).first()
    if who is None:
        raise HTTPException(400, "Không có người dùng này.")
    if not inbox.visible(it, who, features):
        raise HTTPException(400, f"{_display(who, who.username)} không có quyền xem việc này nên không giao được.")
    # keep the table small: items resolve and keys change with the level
    db.query(InboxAssignment).filter(InboxAssignment.assigned_at < dt.datetime.utcnow() - dt.timedelta(days=180)).delete()
    row = row or InboxAssignment(item_key=key)
    row.assignee, row.assigned_by, row.assigned_at = who.username, user.username, dt.datetime.utcnow()
    db.add(row)
    audit = dict(username=user.username, action="inbox.assign", client_ip=request.client.host if request.client else None,
                 detail=json.dumps({"key": key, "assignee": who.username}, ensure_ascii=False))
    db.add(AuditLog(**audit))
    try:
        db.commit()
    except IntegrityError:          # concurrent first assignment of the same item: last one wins (BEQ-09)
        db.rollback()
        row = db.query(InboxAssignment).filter_by(item_key=key).first()
        row.assignee, row.assigned_by, row.assigned_at = who.username, user.username, dt.datetime.utcnow()
        db.add(AuditLog(**audit))
        db.commit()
    return {"ok": True, "assignee": _assignments(db, [key]).get(key)}


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

    if is_on(features, "license"):
        from ..models import LicenseEntitlement
        for e in db.query(LicenseEntitlement).filter(LicenseEntitlement.valid_to.isnot(None)):
            d = e.valid_to.date()
            if d <= end and (d - today).days >= -90:
                add(d, "license", f"License {e.product} hết hạn", f"{vn_g(e.quantity)} {e.metric} · {e.scope_cluster or e.scope_vcenter or 'toàn bộ'}",
                    "lic-assess", tone_of(d))
    if is_on(features, "spend") and has_role(user, "viewer"):
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
    return _stored_prefs(db.get(UserPref, user.username))


@router.put("/me/prefs")
def put_prefs(data: Dict[str, Any] = Body(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if len(json.dumps(data, ensure_ascii=False, default=str).encode()) > MAX_PREFS_BYTES:
        raise HTTPException(413, "Tuỳ chọn quá lớn.")
    # Merge: each screen sends only its own keys (home: inbox / agenda, ui.js: theme, history, reports).
    row = db.get(UserPref, user.username) or UserPref(username=user.username)
    clean = {**_stored_prefs(row), **_clean_prefs(data)}
    row.data, row.updated_at = json.dumps(clean, ensure_ascii=False), dt.datetime.utcnow()
    db.add(row)
    db.commit()
    return clean


@router.post("/admin/ops-alerts/run")
def run_ops_alerts(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Check operations alerts now (normally after each scheduled sync)."""
    return ops_alerts.check_and_notify(db)
