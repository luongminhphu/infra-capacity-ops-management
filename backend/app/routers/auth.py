import datetime as dt
import logging
from typing import Optional

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import auth_entra, login_guard, passkeys
from ..config import get_settings
from ..database import get_db
from ..models import ApiToken, User, UserPasskey
from ..schemas import LoginRequest, UserOut
from ..spend.roles import apply_entra_roles, effective_role
from ..security import (COOKIE_NAME, create_access_token, decode_session, get_current_user, is_admin, local_login_allowed,
                        hash_password, recently_authenticated, session_user, verify_password)

logger = logging.getLogger("vco.routers.auth")
_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")   # bcrypt work when the account does not exist

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session(response: Response, user: User, auth_at: Optional[int] = None) -> None:
    """auth_at: keep the original sign-in time when re-issuing a session (default = now)."""
    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=create_access_token(user.username, user.auth_source or "local", user.session_version or 0, auth_at),
        httponly=True,
        secure=settings.secure_cookies(),
        samesite="lax",
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )



def _owner_admin(user: User) -> bool:
    from ..owners.service import can_manage
    return can_manage(user)

@router.get("/config")
def auth_config():
    """What the login screen should offer (no secrets)."""
    s = get_settings()
    local = s.local_enabled()
    return {"local": local, "entra": s.entra_enabled(), "entraLabel": s.entra_login_label,
            "breakglass": bool(s.breakglass()) and not local,
            # local accounts: passkey button, password form (break-glass keeps its password)
            "passkey": s.passkey_enabled() and (local or bool(s.breakglass())),
            "password": local and s.auth_local_method in ("password", "both"),
            "entraAuto": s.entra_auto_redirect and s.entra_enabled() and not local}


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = login_guard.client_ip(request)
    login_guard.check(ip, payload.username or "")
    if not get_settings().password_enabled_for(payload.username or ""):
        # Same answer for every name: does not reveal which accounts exist.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Tài khoản cục bộ đăng nhập bằng Passkey (Face ID / Touch ID), không dùng mật khẩu")
    user = db.query(User).filter(User.username == payload.username).first()
    # bcrypt runs whether or not the account exists: response time does not reveal valid names (SEC2-01)
    ok = verify_password(payload.password, user.password_hash if user else _DUMMY_HASH)
    if not user or (user.auth_source or "local") != "local" or not ok:
        login_guard.failed(ip, payload.username or "")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sai tên đăng nhập hoặc mật khẩu")
    login_guard.succeeded(ip, payload.username or "")
    if not local_login_allowed(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Đăng nhập bằng mật khẩu đã bị tắt — dùng tài khoản Microsoft")
    user.last_login_at = dt.datetime.utcnow()
    db.commit()
    _set_session(response, user)
    return UserOut(username=user.username, display_name=user.display_name, is_admin=is_admin(user),
                   spend_role=effective_role(user), owner_admin=_owner_admin(user))


# ---- Microsoft Entra ID (OIDC) ----
def _fail(message: str) -> RedirectResponse:
    from urllib.parse import quote
    resp = RedirectResponse(f"/?login_error={quote(message)}", status_code=302)
    resp.delete_cookie(auth_entra.FLOW_COOKIE, path="/api/auth/oidc")
    return resp


@router.get("/oidc/login")
def oidc_login(next: Optional[str] = Query(default="/")):
    s = get_settings()
    if not s.entra_enabled():
        raise HTTPException(404, "Chưa bật đăng nhập Entra ID")
    try:
        url, flow = auth_entra.build_login(s, next)
    except auth_entra.EntraError as exc:
        return _fail(str(exc))
    resp = RedirectResponse(url, status_code=302)
    # SameSite=Lax: sent back on the top-level redirect from login.microsoftonline.com.
    resp.set_cookie(auth_entra.FLOW_COOKIE, flow, max_age=600, httponly=True, secure=s.secure_cookies(),
                    samesite="lax", path="/api/auth/oidc")
    return resp


@router.get("/oidc/callback")
def oidc_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None,
                  error: Optional[str] = None, error_description: Optional[str] = None,
                  db: Session = Depends(get_db)):
    s = get_settings()
    if not s.entra_enabled():
        raise HTTPException(404, "Chưa bật đăng nhập Entra ID")
    if error:
        return _fail(f"Entra ID: {(error_description or error).splitlines()[0][:300]}")
    try:
        claims, next_path = auth_entra.complete_login(s, request.cookies.get(auth_entra.FLOW_COOKIE), state, code)
    except auth_entra.EntraError as exc:
        return _fail(str(exc))
    oid = claims.get("oid") or claims.get("sub")
    upn = (claims.get("preferred_username") or claims.get("upn") or claims.get("email") or oid or "").strip().lower()
    if not oid or not upn:
        return _fail("Token thiếu thông tin định danh người dùng.")
    user = db.query(User).filter(User.external_id == oid).first() or db.query(User).filter(User.username == upn).first()
    if user and (user.auth_source or "local") == "local" and user.external_id is None:
        # Never take over a local (password) account that happens to share the UPN.
        return _fail(f"Tên đăng nhập {upn} đang là tài khoản cục bộ — liên hệ quản trị để chuyển sang Entra ID.")
    if user and user.external_id and user.external_id != oid:
        # Same UPN, different directory object (e.g. a recycled name): do not hand over the old account (SEC-13).
        logger.warning("Entra sign-in refused: UPN matches an account bound to another object id")
        return _fail(f"Tên đăng nhập {upn} đã gắn với một tài khoản Entra khác — liên hệ quản trị.")
    if not user:
        user = User(username=upn, password_hash="!entra", auth_source="entra")
        db.add(user)
    user.username, user.external_id, user.auth_source = upn, oid, "entra"
    user.display_name = claims.get("name") or user.display_name or upn
    user.email = claims.get("email") or (upn if "@" in upn else user.email)
    user.last_login_at = dt.datetime.utcnow()
    was_admin = bool(user.is_admin)
    user.is_admin = bool(set(s.entra_admin_role_list()) & set(claims.get("roles") or []))
    apply_entra_roles(user, s, claims.get("roles") or [])
    if was_admin and not user.is_admin and user.id:
        # Admin role removed in Entra: the user's MCP tokens stop working at once.
        db.query(ApiToken).filter(ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None)).update(
            {ApiToken.revoked_at: dt.datetime.utcnow()}, synchronize_session=False)
    db.commit()
    resp = RedirectResponse(next_path, status_code=302)
    resp.delete_cookie(auth_entra.FLOW_COOKIE, path="/api/auth/oidc")
    _set_session(resp, user)
    return resp


# ---- Passkeys (WebAuthn) for local accounts ----
def _local_user_or_403(user: User) -> None:
    if (user.auth_source or "local") != "local":
        raise HTTPException(403, "Tài khoản Microsoft dùng passkey của Microsoft (Authenticator), không đăng ký ở đây.")
    s = get_settings()
    if not local_login_allowed(user):
        raise HTTPException(403, "Tài khoản cục bộ đã bị tắt — đăng nhập bằng Microsoft.")
    if not (s.passkey_enabled() or user.username.lower() in s.breakglass()):
        raise HTTPException(403, "Passkey cho tài khoản cục bộ đang tắt.")


@router.post("/passkey/login/options")
def passkey_login_options(request: Request):
    s = get_settings()
    if not (s.passkey_enabled() and (s.local_enabled() or s.breakglass())):
        raise HTTPException(404, "Đăng nhập bằng passkey đang tắt.")
    options, challenge = passkeys.authentication_options(request)
    resp = Response(options, media_type="application/json")
    passkeys.set_state(resp, passkeys.state_token("auth", challenge))
    return resp


@router.post("/passkey/login/verify", response_model=UserOut)
def passkey_login_verify(request: Request, response: Response, data: dict = Body(...), db: Session = Depends(get_db)):
    from webauthn.helpers import base64url_to_bytes
    st = passkeys.read_state(request, "auth")
    user = passkeys.finish_authentication(db, request, base64url_to_bytes(st["c"]), passkeys.parse_credential(data.get("credential")))
    if (user.auth_source or "local") != "local" or not local_login_allowed(user):
        raise HTTPException(403, "Tài khoản cục bộ đã bị tắt — đăng nhập bằng Microsoft.")
    user.last_login_at = dt.datetime.utcnow()
    db.commit()
    passkeys.clear_state(response)
    _set_session(response, user)
    return UserOut(username=user.username, display_name=user.display_name, is_admin=is_admin(user), spend_role=effective_role(user),
                   owner_admin=_owner_admin(user))


@router.post("/passkey/invite")
def passkey_invite_info(data: dict = Body(...), db: Session = Depends(get_db)):
    """What the invite landing page shows (account name only). POST: the token never sits in a URL / access log."""
    inv = passkeys.find_invite(db, data.get("token") if isinstance(data.get("token"), str) else None)
    user = db.get(User, inv.user_id)
    return {"username": user.username, "displayName": user.display_name, "expiresAt": inv.expires_at.isoformat() + "Z"}


def _register_target(request: Request, db: Session, invite: str = None) -> tuple:
    """The account a passkey is being added to: the signed-in local user, or the invite's account."""
    if invite:
        inv = passkeys.find_invite(db, invite)
        user = db.get(User, inv.user_id)
        return user, inv
    token = request.cookies.get(COOKIE_NAME)
    user = session_user(db, token)
    if not user:
        raise HTTPException(401, "Chưa đăng nhập")
    _require_recent(request)
    return user, None


REAUTH_STATUS = 428          # the UI asks for Face ID / password again, then retries


def _require_recent(request: Request) -> None:
    """Adding / deleting a passkey with a stolen session cookie must not be enough."""
    if not recently_authenticated(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(REAUTH_STATUS, "Xác nhận lại danh tính (Face ID / mật khẩu) trước khi thay đổi passkey.")


@router.post("/passkey/register/options")
def passkey_register_options(request: Request, data: dict = Body(default={}), db: Session = Depends(get_db)):
    invite = data.get("invite") if isinstance(data.get("invite"), str) else None
    user, inv = _register_target(request, db, invite)
    _local_user_or_403(user)
    options, challenge = passkeys.registration_options(db, user, request)
    resp = Response(options, media_type="application/json")
    passkeys.set_state(resp, passkeys.state_token("reg", challenge, u=user.id, i=inv.id if inv else None))
    return resp


@router.post("/passkey/register/verify")
def passkey_register_verify(request: Request, response: Response, data: dict = Body(...), db: Session = Depends(get_db)):
    from webauthn.helpers import base64url_to_bytes
    from ..models import PasskeyInvite
    from ..spend import service
    st = passkeys.read_state(request, "reg")
    invite = data.get("invite") if isinstance(data.get("invite"), str) else None
    user, inv = _register_target(request, db, invite)
    if user.id != st.get("u") or (inv.id if inv else None) != st.get("i"):
        raise HTTPException(400, "Phiên tạo passkey không khớp tài khoản — thử lại.")
    _local_user_or_403(user)
    pk = passkeys.finish_registration(db, user, request, base64url_to_bytes(st["c"]), passkeys.parse_credential(data.get("credential")),
                                      str(data.get("name") or ""))
    if inv is not None:
        if db.query(PasskeyInvite).filter(PasskeyInvite.id == inv.id, PasskeyInvite.used_at.is_(None)).update(
                {PasskeyInvite.used_at: dt.datetime.utcnow()}, synchronize_session=False) != 1:
            db.rollback()
            raise HTTPException(409, "Link mời vừa được dùng — nhờ quản trị tạo link mới.")
    service.audit(db, user, request, "auth.passkey.register", name=pk.name, viaInvite=inv is not None, synced=pk.backed_up)
    user.last_login_at = dt.datetime.utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Passkey này đã được đăng ký.") from None
    passkeys.clear_state(response)
    if inv is not None:                       # first passkey through an invite: signed in straight away
        _set_session(response, user)
    return {"ok": True, "passkey": passkeys.passkey_out(pk)}


@router.get("/passkeys")
def my_passkeys(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(UserPasskey).filter(UserPasskey.user_id == user.id).order_by(UserPasskey.created_at).all()
    s = get_settings()
    return {"items": [passkeys.passkey_out(p) for p in rows], "local": (user.auth_source or "local") == "local",
            "enabled": s.passkey_enabled() or user.username.lower() in s.breakglass(),
            "hasPassword": (user.password_hash or "").startswith("$2") and s.password_enabled_for(user.username)}


@router.delete("/passkeys/{passkey_id}")
def delete_my_passkey(passkey_id: int, request: Request, response: Response, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    from ..spend import service
    _require_recent(request)
    pk = db.query(UserPasskey).filter(UserPasskey.id == passkey_id, UserPasskey.user_id == user.id).first()
    if not pk:
        raise HTTPException(404, "Không tìm thấy passkey.")
    left = db.query(UserPasskey).filter(UserPasskey.user_id == user.id).count() - 1
    can_password = (user.password_hash or "").startswith("$2") and get_settings().password_enabled_for(user.username)
    if left == 0 and not can_password:
        raise HTTPException(409, "Đây là passkey cuối cùng và tài khoản không đăng nhập bằng mật khẩu — thêm passkey khác trước khi xoá.")
    service.audit(db, user, request, "auth.passkey.delete", name=pk.name)
    db.delete(pk)
    user.session_version = (user.session_version or 0) + 1          # sessions opened with that device end
    db.commit()
    at = (decode_session(request.cookies.get(COOKIE_NAME)) or {}).get("at")
    _set_session(response, user, at)                                 # this browser stays signed in
    return {"ok": True}


@router.post("/reauth")
def reauth_password(request: Request, response: Response, data: dict = Body(...), user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Step-up with the password (accounts without a passkey yet). Throttled like /login: a stolen
    session cookie must not allow unlimited password guesses."""
    ip = login_guard.client_ip(request)
    login_guard.check(ip, user.username)
    if (user.auth_source or "local") != "local" or not get_settings().password_enabled_for(user.username) \
            or not verify_password(str(data.get("password") or ""), user.password_hash):
        login_guard.failed(ip, user.username)
        raise HTTPException(401, "Mật khẩu không đúng.")
    login_guard.succeeded(ip, user.username)
    _set_session(response, user)
    return {"ok": True}


@router.post("/passkey/reauth/verify")
def passkey_reauth_verify(request: Request, response: Response, data: dict = Body(...), user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    """Step-up with one of the signed-in user's own passkeys (same options endpoint as sign-in)."""
    from webauthn.helpers import base64url_to_bytes
    st = passkeys.read_state(request, "auth")
    who = passkeys.finish_authentication(db, request, base64url_to_bytes(st["c"]), passkeys.parse_credential(data.get("credential")))
    if who.id != user.id:
        db.rollback()
        raise HTTPException(403, "Passkey này của tài khoản khác.")
    db.commit()
    passkeys.clear_state(response)
    _set_session(response, user)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response, vco_session: Optional[str] = Cookie(default=None), db: Session = Depends(get_db)):
    response.delete_cookie(COOKIE_NAME, path="/")
    # Revoke on the server too (SEC-11 / SEC2-04): a copied cookie stops working now, not at expiry.
    # session_version is per account, so this signs the user out on every device.
    user = session_user(db, vco_session) if vco_session else None
    if user is not None:
        user.session_version = (user.session_version or 0) + 1
        db.commit()
    s = get_settings()
    out = {"ok": True}
    if s.entra_enabled() and vco_session:
        from jose import jwt
        try:
            src = jwt.decode(vco_session, s.jwt_secret, algorithms=[s.jwt_algorithm], options={"verify_exp": False}).get("src")
        except Exception:  # noqa: BLE001
            src = None
        if src == "entra":
            out["logoutUrl"] = auth_entra.logout_url(s)
    return out


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut(username=user.username, display_name=user.display_name, is_admin=is_admin(user),
                   spend_role=effective_role(user), owner_admin=_owner_admin(user))
