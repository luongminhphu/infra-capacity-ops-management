import datetime as dt
from typing import Optional

import bcrypt
from fastapi import Cookie, Depends, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import User

COOKIE_NAME = "vco_session"

# Using the `bcrypt` library directly (not passlib) — passlib 1.7.x's bcrypt
# backend detection is broken against bcrypt>=4.1 (missing `__about__`), which
# crashes bootstrap on a clean install. bcrypt truncates at 72 bytes natively
# so long passwords are hashed safely without raising.


def hash_password(password: str) -> str:
    pw_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("utf-8"))
    except Exception:
        return False


REAUTH_SECONDS = 600     # adding / deleting a passkey needs a sign-in this recent


def create_access_token(username: str, source: str = "local", session_version: int = 0, auth_at: Optional[int] = None) -> str:
    """Session token. "at" = when the user last proved a credential (password, passkey, Microsoft);
    "sv" = the account's session version (bumped to sign it out everywhere)."""
    settings = get_settings()
    now = dt.datetime.utcnow()
    payload = {"sub": username, "exp": now + dt.timedelta(minutes=settings.jwt_expire_minutes), "src": source,
               "sv": int(session_version or 0), "at": int(auth_at if auth_at is not None else now.timestamp())}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_session(token: str) -> Optional[dict]:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


def decode_access_token(token: str) -> Optional[str]:
    payload = decode_session(token)
    return payload.get("sub") if payload else None


def session_user(db: Session, token: Optional[str]) -> Optional[User]:
    """The user of a session cookie, or None (expired, signed out everywhere, unknown)."""
    payload = decode_session(token) if token else None
    if not payload:
        return None
    user = db.query(User).filter(User.username == payload.get("sub")).first()
    if not user or int(payload.get("sv") or 0) != int(user.session_version or 0):
        return None
    return user


def recently_authenticated(token: Optional[str]) -> bool:
    payload = decode_session(token) if token else None
    return bool(payload) and dt.datetime.utcnow().timestamp() - float(payload.get("at") or 0) <= REAUTH_SECONDS


def get_session_user(
    vco_session: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Authentication only: a valid session of any access scope. Use only for endpoints an owner-scope user must
    reach (who am I, sign out, the owner portal via owners/access.py); everything else uses get_current_user."""
    if not vco_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Chưa đăng nhập")
    if not decode_access_token(vco_session):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Phiên đăng nhập không hợp lệ hoặc đã hết hạn")
    user = session_user(db, vco_session)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Phiên đăng nhập đã kết thúc — đăng nhập lại")
    if not local_login_allowed(user) and (user.auth_source or "local") == "local":
        # AUTH_MODE=entra: local sessions issued earlier stop working, except break-glass accounts.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Tài khoản cục bộ đã bị tắt — đăng nhập bằng Microsoft")
    return user


OWNER_ONLY = "Tài khoản owner chỉ dùng được cổng Owner."


def get_current_user(user: User = Depends(get_session_user)) -> User:
    """Authentication + the full app: a user with access_scope "owner" is refused (403) — deny by default, so every
    endpoint written before the owner portal (and every new one using this dependency) stays closed to owners."""
    if (user.access_scope or "full") != "full":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=OWNER_ONLY)
    return user


def local_login_allowed(user: User) -> bool:
    s = get_settings()
    return s.local_enabled() or user.username.lower() in s.breakglass()


def is_admin(user: User) -> bool:
    """Entra users: app role in ENTRA_ADMIN_ROLES (refreshed at each sign-in). Local: ADMIN_USERS."""
    if (user.auth_source or "local") == "entra":
        return bool(user.is_admin)
    return local_login_allowed(user) and user.username.lower() in get_settings().admin_user_list()


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not is_admin(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Chỉ quản trị viên mới được dùng chức năng này")
    return user


def ensure_bootstrap_admin(db: Session):
    """Create the first admin user from env vars if the users table is empty."""
    settings = get_settings()
    if db.query(User).count() > 0:
        return
    if not settings.admin_password:
        # No admin bootstrap password configured — skip silently, operator must
        # create a user manually via `python -m app.seed`.
        return
    user = User(
        username=settings.admin_username,
        password_hash=hash_password(settings.admin_password),
        display_name="Administrator",
    )
    db.add(user)
    db.commit()
