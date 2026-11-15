"""Cài đặt → Đăng nhập & bảo mật (admin): local accounts without a password (passkey invite link),
passkeys of every account, and the sign-in / App Proxy configuration as the server sees it."""
import datetime as dt
import os
import re
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import passkeys
from ..config import get_settings
from ..database import get_db
from ..models import PasskeyInvite, User, UserPasskey
from ..security import is_admin, require_admin
from ..spend import service
from ..timeutil import to_iso_utc

router = APIRouter(prefix="/api/admin", tags=["admin-users"])
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._@-]{2,63}$")


def _invite_url(request: Request, token: str) -> str:
    base = get_settings().public_base() or str(request.base_url).rstrip("/")
    return f"{base}/?passkey_invite={token}"


def _user_out(u: User, n_keys: int, invite) -> Dict[str, Any]:
    return {"id": u.id, "username": u.username, "displayName": u.display_name, "source": u.auth_source or "local",
            "hasPassword": (u.password_hash or "").startswith("$2"), "passkeys": n_keys, "isAdmin": is_admin(u),
            "lastLoginAt": to_iso_utc(u.last_login_at) if u.last_login_at else None,
            "inviteExpiresAt": to_iso_utc(invite.expires_at) if invite else None}


@router.get("/users")
def list_users(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    counts = dict(db.query(UserPasskey.user_id, func.count(UserPasskey.id)).group_by(UserPasskey.user_id).all())
    now = dt.datetime.utcnow()
    invites = {i.user_id: i for i in db.query(PasskeyInvite).filter(PasskeyInvite.used_at.is_(None), PasskeyInvite.expires_at > now)}
    rows = db.query(User).order_by(User.auth_source, User.username).limit(2000).all()
    return {"items": [_user_out(u, counts.get(u.id, 0), invites.get(u.id)) for u in rows]}


@router.post("/users")
def create_local_user(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Local account WITHOUT a password: it signs in with the passkey created through the invite link."""
    name = str(data.get("username") or "").strip().lower()
    if not USERNAME_RE.match(name):
        raise HTTPException(400, "Tên đăng nhập 3–64 ký tự: chữ thường, số, . _ @ -")
    if db.query(User.id).filter(func.lower(User.username) == name).first():
        raise HTTPException(409, "Tên đăng nhập đã tồn tại.")
    u = User(username=name, display_name=str(data.get("displayName") or "").strip()[:128] or None,
             password_hash="!passkey", auth_source="local")
    db.add(u)
    db.flush()
    token, exp = passkeys.create_invite(db, u, user.username)
    service.audit(db, user, request, "auth.user.create", target=name, method="passkey")
    db.commit()
    return {"user": _user_out(u, 0, None), "inviteUrl": _invite_url(request, token), "expiresAt": to_iso_utc(exp)}


def _local(db: Session, user_id: int) -> User:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "Không tìm thấy tài khoản.")
    if (u.auth_source or "local") != "local":
        raise HTTPException(400, "Tài khoản Microsoft không dùng passkey của ứng dụng.")
    return u


@router.post("/users/{user_id}/passkey-invite")
def new_invite(user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = _local(db, user_id)
    token, exp = passkeys.create_invite(db, u, admin.username)
    service.audit(db, admin, request, "auth.passkey.invite", target=u.username)
    db.commit()
    return {"inviteUrl": _invite_url(request, token), "expiresAt": to_iso_utc(exp)}


@router.get("/users/{user_id}/passkeys")
def user_passkeys(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = _local(db, user_id)
    return {"items": [passkeys.passkey_out(p) for p in db.query(UserPasskey).filter(UserPasskey.user_id == u.id).order_by(UserPasskey.created_at)]}


@router.delete("/users/{user_id}/passkeys/{passkey_id}")
def revoke_passkey(user_id: int, passkey_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Lost / replaced phone: the passkey stops working at once (the user's session cookie is not revoked)."""
    u = _local(db, user_id)
    pk = db.query(UserPasskey).filter(UserPasskey.id == passkey_id, UserPasskey.user_id == u.id).first()
    if not pk:
        raise HTTPException(404, "Không tìm thấy passkey.")
    service.audit(db, admin, request, "auth.passkey.revoke", target=u.username, name=pk.name)
    db.delete(pk)
    db.commit()
    return {"ok": True}


@router.get("/auth-status")
def auth_status(request: Request, admin: User = Depends(require_admin)):
    """Sign-in / App Proxy settings as the server sees them, with what is missing (no secrets)."""
    s = get_settings()
    try:
        rp_id, origin = passkeys.relying_party(request, s)
    except HTTPException:
        rp_id, origin = "", ""
    seen = {"host": request.headers.get("host"), "scheme": request.url.scheme, "clientIp": request.client.host if request.client else None,
            "forwardedHost": request.headers.get("x-forwarded-host"), "forwardedProto": request.headers.get("x-forwarded-proto")}
    checks = []

    def check(ok: bool, text: str, fix: str) -> None:
        checks.append({"ok": ok, "text": text, "fix": "" if ok else fix})
    check(bool(s.public_base()), f"PUBLIC_URL = {s.public_base() or '(trống)'}",
          "Đặt PUBLIC_URL = URL ngoài (vd https://ico-contoso.msappproxy.net hoặc tên miền riêng). App Proxy đổi Host header thành tên nội bộ.")
    check(s.public_base().startswith("https://") or not s.public_base(), "URL ngoài dùng HTTPS", "App Proxy luôn phục vụ HTTPS: PUBLIC_URL phải bắt đầu bằng https://")
    check(s.secure_cookies() or not s.public_base(), "Cookie phiên có cờ Secure", "Đặt COOKIE_SECURE=true hoặc PUBLIC_URL https://…")
    if s.auth_mode in ("entra", "both"):
        check(bool(s.entra_tenant_id and s.entra_client_id), "Đã khai báo ENTRA_TENANT_ID / ENTRA_CLIENT_ID", "Khai báo app registration trong Entra ID.")
        check(bool(s.oidc_redirect_uri()), f"Redirect URI = {s.oidc_redirect_uri() or '(trống)'}",
              "Đặt PUBLIC_URL (Redirect URI tự suy ra) hoặc ENTRA_REDIRECT_URI, và thêm đúng URI đó vào app registration (Web).")
    if s.passkey_enabled():
        check(bool(rp_id) and rp_id not in ("localhost",) or not s.public_base(), f"Passkey gắn với tên miền {rp_id or '(chưa có)'}",
              "Passkey chỉ dùng được trên đúng tên miền này — đặt PUBLIC_URL trước khi người dùng tạo passkey.")
    check(bool(os.environ.get("FORWARDED_ALLOW_IPS")), f"FORWARDED_ALLOW_IPS = {os.environ.get('FORWARDED_ALLOW_IPS') or '(mặc định 127.0.0.1)'}",
          "Đặt FORWARDED_ALLOW_IPS = IP máy chủ connector / ingress để log ghi đúng IP người dùng (X-Forwarded-For).")
    return {"authMode": s.auth_mode, "localMethod": s.auth_local_method, "entraEnabled": s.entra_enabled(), "entraAutoRedirect": s.entra_auto_redirect,
            "publicUrl": s.public_base(), "redirectUri": s.oidc_redirect_uri(), "rpId": rp_id, "origin": origin,
            "secureCookies": s.secure_cookies(), "breakglass": s.breakglass(), "request": seen, "checks": checks}
