"""Chi phí & Hợp đồng — admin configuration: SMTP, reminder channels, reminder log, user roles.

Secrets (SMTP password, Discord webhook URL) are write-only: stored with secrets_box, the API
only answers hasPassword / hasWebhook, audit rows say "changed" without the value.
"""
import datetime as dt
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import secrets_box
from ..database import get_db
from ..features import require_feature
from ..models import User
from ..security import is_admin
from ..spend import fiscal, files, reminders, service, settings as spend_settings
from ..spend.models import OpexItem, SpendNotifyChannel, SpendReminderLog, SpendSmtpConfig
from ..spend.notify import NotifyError, ReminderLine, parse_recipients, send, validate_discord_url
from ..spend.roles import LABELS, ROLES, effective_role, require_spend
from ..timeutil import to_iso_utc

router = APIRouter(prefix="/api/spend/admin", tags=["spend-admin"], dependencies=[Depends(require_feature("spend"))])
admin = require_spend("admin")


def _readable(token: Optional[str]) -> Optional[bool]:
    if not token:
        return None
    try:
        secrets_box.decrypt(token)
        return True
    except secrets_box.SecretUnreadable:
        return False


# ---------------------------------------------------------------- SMTP
def _smtp_out(row: Optional[SpendSmtpConfig]) -> Dict[str, Any]:
    if not row:
        return {"configured": False, "host": "", "port": 587, "security": "starttls", "username": "", "fromAddr": "",
                "hasPassword": False, "secretsKeyConfigured": secrets_box.dedicated_key()}
    return {"configured": True, "host": row.host, "port": row.port, "security": row.security, "username": row.username or "",
            "fromAddr": row.from_addr, "hasPassword": bool(row.password_enc), "passwordReadable": _readable(row.password_enc),
            "updatedAt": to_iso_utc(row.updated_at), "updatedBy": row.updated_by,
            "secretsKeyConfigured": secrets_box.dedicated_key()}


@router.get("/smtp")
def get_smtp(user: User = Depends(admin), db: Session = Depends(get_db)):
    return _smtp_out(db.get(SpendSmtpConfig, 1))


@router.put("/smtp")
def put_smtp(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(admin), db: Session = Depends(get_db)):
    host = str(data.get("host") or "").strip()
    from_addr = str(data.get("fromAddr") or "").strip()
    security = data.get("security") or "starttls"
    try:
        port = int(data.get("port") or 587)
    except (TypeError, ValueError):
        raise HTTPException(400, "Cổng SMTP phải là số.")
    if not host or len(host) > 255 or any(c in host for c in " /\\@"):
        raise HTTPException(400, "Máy chủ SMTP không hợp lệ.")
    if not 1 <= port <= 65535 or security not in ("starttls", "ssl", "none"):
        raise HTTPException(400, "Cổng hoặc kiểu bảo mật SMTP không hợp lệ.")
    if not service.EMAIL_RE.match(from_addr):
        raise HTTPException(400, "Địa chỉ người gửi không hợp lệ.")
    row = db.get(SpendSmtpConfig, 1) or SpendSmtpConfig(id=1)
    row.host, row.port, row.security, row.from_addr = host, port, security, from_addr
    row.username = str(data.get("username") or "").strip()[:255] or None
    changed = False
    if data.get("password"):
        row.password_enc, changed = secrets_box.encrypt(str(data["password"])), True
    elif data.get("clearPassword"):
        row.password_enc, changed = None, True
    row.updated_at, row.updated_by = dt.datetime.utcnow(), user.username
    db.merge(row)
    service.audit(db, user, request, "spend.smtp.update", host=host, port=port, security=security, passwordChanged=changed)
    db.commit()
    return _smtp_out(db.get(SpendSmtpConfig, 1))


# ---------------------------------------------------------------- channels
def _channel_out(c: SpendNotifyChannel) -> Dict[str, Any]:
    return {"id": c.id, "kind": c.kind, "name": c.name, "enabled": c.enabled, "hasWebhook": bool(c.webhook_enc),
            "webhookReadable": _readable(c.webhook_enc), "recipients": c.recipients or "", "includeOwner": c.include_owner,
            "lastTestAt": to_iso_utc(c.last_test_at), "lastTestOk": c.last_test_ok,
            "updatedAt": to_iso_utc(c.updated_at or c.created_at), "updatedBy": c.updated_by or c.created_by}


@router.get("/channels")
def list_channels(user: User = Depends(admin), db: Session = Depends(get_db)):
    return [_channel_out(c) for c in db.query(SpendNotifyChannel).order_by(SpendNotifyChannel.id)]


def _apply_channel(row: SpendNotifyChannel, data: Dict[str, Any]) -> bool:
    """Returns True when the webhook (secret) changed."""
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 128:
        raise HTTPException(400, "Tên kênh bắt buộc, tối đa 128 ký tự.")
    row.name, row.enabled = name, bool(data.get("enabled", True))
    changed = False
    try:
        if row.kind == "discord":
            if data.get("webhookUrl"):
                row.webhook_enc, changed = secrets_box.encrypt(validate_discord_url(str(data["webhookUrl"]))), True
            if not row.webhook_enc:
                raise HTTPException(400, "Nhập webhook Discord.")
        else:
            recipients = parse_recipients(data.get("recipients"))
            row.recipients = ", ".join(recipients) or None
            row.include_owner = bool(data.get("includeOwner"))
            if not recipients and not row.include_owner:
                raise HTTPException(400, "Nhập ít nhất một email nhận hoặc chọn gửi cho người phụ trách.")
    except NotifyError as exc:
        raise HTTPException(400, str(exc)) from exc
    return changed


@router.post("/channels")
def create_channel(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(admin), db: Session = Depends(get_db)):
    kind = data.get("kind")
    if kind not in ("email", "discord"):
        raise HTTPException(400, "Loại kênh phải là email hoặc discord.")
    row = SpendNotifyChannel(kind=kind, created_by=user.username)
    changed = _apply_channel(row, data)
    db.add(row)
    db.flush()
    service.audit(db, user, request, "spend.channel.create", id=row.id, kind=kind, name=row.name, webhookChanged=changed)
    db.commit()
    return _channel_out(row)


def _channel(db: Session, channel_id: int) -> SpendNotifyChannel:
    row = db.get(SpendNotifyChannel, channel_id)
    if not row:
        raise HTTPException(404, "Không tìm thấy kênh nhắc.")
    return row


@router.put("/channels/{channel_id}")
def update_channel(channel_id: int, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(admin),
                   db: Session = Depends(get_db)):
    row = _channel(db, channel_id)
    changed = _apply_channel(row, data)
    row.updated_at, row.updated_by = dt.datetime.utcnow(), user.username
    service.audit(db, user, request, "spend.channel.update", id=row.id, kind=row.kind, name=row.name,
                  enabled=row.enabled, webhookChanged=changed)
    db.commit()
    return _channel_out(row)


@router.delete("/channels/{channel_id}")
def delete_channel(channel_id: int, request: Request, user: User = Depends(admin), db: Session = Depends(get_db)):
    row = _channel(db, channel_id)
    service.audit(db, user, request, "spend.channel.delete", id=row.id, kind=row.kind, name=row.name)
    db.query(SpendReminderLog).filter(SpendReminderLog.channel_id == row.id).delete(synchronize_session=False)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/channels/{channel_id}/test")
def test_channel(channel_id: int, request: Request, user: User = Depends(admin), db: Session = Depends(get_db)):
    row = _channel(db, channel_id)
    sample = ReminderLine(code="OPX-TEST-0001", title="Tin thử — gia hạn bảo hành máy chủ", vendor="Nhà cung cấp mẫu",
                          end_date=service.date_vn(fiscal.today_vn() + dt.timedelta(days=30)), days_left=30,
                          amount=service.money_vn(123456789), owner=user.display_name or user.username,
                          owner_email=None, link=None)
    try:
        send(row, db.get(SpendSmtpConfig, 1), [sample], test=True)
        ok, message = True, "Đã gửi tin thử."
    except NotifyError as exc:
        ok, message = False, str(exc)
    row.last_test_at, row.last_test_ok = dt.datetime.utcnow(), ok
    service.audit(db, user, request, "spend.channel.test", id=row.id, ok=ok)
    db.commit()
    return {"ok": ok, "message": message}


# ---------------------------------------------------------------- reminder log & run
@router.get("/reminders")
def reminder_log(status: Optional[str] = None, limit: int = Query(default=100, ge=1, le=500),
                 user: User = Depends(admin), db: Session = Depends(get_db)):
    qry = db.query(SpendReminderLog, OpexItem.code, OpexItem.title, SpendNotifyChannel.name) \
        .join(OpexItem, OpexItem.id == SpendReminderLog.opex_id) \
        .join(SpendNotifyChannel, SpendNotifyChannel.id == SpendReminderLog.channel_id)
    if status:
        qry = qry.filter(SpendReminderLog.status == status)
    rows = qry.order_by(SpendReminderLog.id.desc()).limit(limit).all()
    counts = dict(db.query(SpendReminderLog.status, func.count()).group_by(SpendReminderLog.status).all())
    return {"state": spend_settings.get_state(db, "reminder"), "counts": counts, "storage": files.disk_usage(),
            "items": [{"id": r.id, "code": code, "title": title, "channel": ch, "threshold": r.threshold_days,
                       "endDate": r.end_date.isoformat(), "status": r.status, "attempts": r.attempts, "error": r.last_error,
                       "createdAt": to_iso_utc(r.created_at), "sentAt": to_iso_utc(r.sent_at)}
                      for r, code, title, ch in rows]}


@router.post("/reminders/run")
def run_reminders(request: Request, user: User = Depends(admin), db: Session = Depends(get_db)):
    """Run now, whatever the hour — safe to repeat: the log decides what is still unsent."""
    service.audit(db, user, request, "spend.reminder.run")
    db.commit()
    return reminders.run(db)


@router.post("/reminders/{log_id}/retry")
def retry_reminder(log_id: int, request: Request, user: User = Depends(admin), db: Session = Depends(get_db)):
    """Allow another round of attempts for a failed / unknown delivery (an explicit admin decision)."""
    row = db.get(SpendReminderLog, log_id)
    if not row or row.status not in ("failed", "unknown"):
        raise HTTPException(409, "Chỉ gửi lại được dòng lỗi hoặc không rõ kết quả.")
    row.status, row.attempts = "failed", 0
    service.audit(db, user, request, "spend.reminder.retry", id=row.id)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- user roles
@router.get("/users")
def list_users(user: User = Depends(admin), db: Session = Depends(get_db)):
    return {"roles": [{"value": r, "label": LABELS[r]} for r in ROLES],
            "items": [{"id": u.id, "username": u.username, "displayName": u.display_name, "authSource": u.auth_source or "local",
                       "spendRole": u.spend_role or "none", "roleSource": u.spend_role_source, "isAdmin": is_admin(u),
                       "effectiveRole": effective_role(u)} for u in db.query(User).order_by(User.username)]}


@router.put("/users/{user_id}/role")
def set_user_role(user_id: int, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(admin),
                  db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "Không tìm thấy người dùng.")
    role = data.get("role")
    if role not in ROLES:
        raise HTTPException(400, "Vai trò không hợp lệ.")
    if target.spend_role_source == "entra":
        raise HTTPException(409, "Vai trò của tài khoản này lấy từ app role Entra ID — đổi trong Entra.")
    before = target.spend_role
    target.spend_role, target.spend_role_source = role, ("ui" if role != "none" else None)
    service.audit(db, user, request, "spend.user.role", target=target.username, role=[before, role])
    db.commit()
    return {"ok": True, "spendRole": role}
