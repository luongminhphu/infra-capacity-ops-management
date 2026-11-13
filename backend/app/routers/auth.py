import datetime as dt
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import auth_entra
from ..config import get_settings
from ..database import get_db
from ..models import User
from ..schemas import LoginRequest, UserOut
from ..security import COOKIE_NAME, create_access_token, get_current_user, local_login_allowed, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session(response: Response, user: User) -> None:
    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=create_access_token(user.username, user.auth_source or "local"),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )


@router.get("/config")
def auth_config():
    """What the login screen should offer (no secrets)."""
    s = get_settings()
    return {"local": s.local_enabled(), "entra": s.entra_enabled(), "entraLabel": s.entra_login_label,
            "breakglass": bool(s.breakglass()) and not s.local_enabled()}


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or (user.auth_source or "local") != "local" or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sai tên đăng nhập hoặc mật khẩu")
    if not local_login_allowed(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Đăng nhập bằng mật khẩu đã bị tắt — dùng tài khoản Microsoft")
    user.last_login_at = dt.datetime.utcnow()
    db.commit()
    _set_session(response, user)
    return UserOut(username=user.username, display_name=user.display_name)


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
    resp.set_cookie(auth_entra.FLOW_COOKIE, flow, max_age=600, httponly=True, secure=s.cookie_secure,
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
    if not user:
        user = User(username=upn, password_hash="!entra", auth_source="entra")
        db.add(user)
    user.username, user.external_id, user.auth_source = upn, oid, "entra"
    user.display_name = claims.get("name") or user.display_name or upn
    user.email = claims.get("email") or (upn if "@" in upn else user.email)
    user.last_login_at = dt.datetime.utcnow()
    db.commit()
    resp = RedirectResponse(next_path, status_code=302)
    resp.delete_cookie(auth_entra.FLOW_COOKIE, path="/api/auth/oidc")
    _set_session(resp, user)
    return resp


@router.post("/logout")
def logout(response: Response, vco_session: Optional[str] = Cookie(default=None)):
    response.delete_cookie(COOKIE_NAME, path="/")
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
    return UserOut(username=user.username, display_name=user.display_name)
