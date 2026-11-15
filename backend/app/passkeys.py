"""Passkeys (WebAuthn) for LOCAL accounts: sign in with Face ID / Touch ID on an iPhone (iCloud
Keychain), a Mac, Android, Windows Hello or a security key — no password.

* Relying party: RP ID = host name of PUBLIC_URL (or of the request when unset, for direct / dev
  access). Behind Microsoft Entra Application Proxy the Host header is the internal name, so
  PUBLIC_URL must be the external URL users open; a passkey is bound to that name for good.
* Sign-in is usernameless (discoverable credential, user verification required). From a desktop
  browser, the browser offers "use a phone": scan the QR code with the iPhone.
* Ceremony state (challenge, user, invite) lives in a short-lived signed cookie, never in a table.
* Microsoft Entra users do not get passkeys here: they use Microsoft's own sign-in (which supports
  passkeys in Microsoft Authenticator / FIDO2 keys)."""
import datetime as dt
import hashlib
import json
import secrets
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from jose import JWTError, jwt
from sqlalchemy.orm import Session

import logging

from .config import Settings, get_settings
from .models import PasskeyInvite, User, UserPasskey

logger = logging.getLogger("vco.passkeys")
STATE_COOKIE = "ico_webauthn"
STATE_PATH = "/api/auth/passkey"
STATE_SECONDS = 300
INVITE_HOURS = 72
MAX_PER_USER = 10
TIMEOUT_MS = 120000


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def require_public_url(request: Request) -> None:
    """Passkeys are bound to a domain for good: create them / invite links only for the real
    external name (PUBLIC_URL), except on localhost for trials."""
    if get_settings().public_base():
        return
    host = (urlsplit(str(request.base_url)).hostname or "").lower()
    if host not in LOCAL_HOSTS:
        raise HTTPException(503, "Chưa đặt PUBLIC_URL (URL ngoài, vd App Proxy) — đặt trước khi tạo passkey / link mời, "
                                 "vì passkey gắn cố định với tên miền.")


def relying_party(request: Request, s: Optional[Settings] = None) -> Tuple[str, str]:
    """(rp_id, origin). PUBLIC_URL wins; otherwise the URL the request came in on."""
    s = s or get_settings()
    base = s.public_base()
    if base:
        u = urlsplit(base)
    else:
        u = urlsplit(str(request.base_url))
    host = (u.hostname or "").lower()
    if not host:
        raise HTTPException(500, "Không xác định được tên miền cho passkey — đặt PUBLIC_URL.")
    origin = f"{u.scheme}://{u.netloc}".lower()
    return host, origin


# ---------------------------------------------------------------- ceremony state (signed cookie)
def state_token(kind: str, challenge: bytes, **extra: Any) -> str:
    s = get_settings()
    from webauthn.helpers import bytes_to_base64url
    payload = {"k": kind, "c": bytes_to_base64url(challenge), "exp": dt.datetime.utcnow() + dt.timedelta(seconds=STATE_SECONDS), **extra}
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def read_state(request: Request, kind: str) -> Dict[str, Any]:
    s = get_settings()
    raw = request.cookies.get(STATE_COOKIE)
    if not raw:
        raise HTTPException(400, "Phiên tạo / dùng passkey đã hết hạn — thử lại.")
    try:
        data = jwt.decode(raw, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except JWTError:
        raise HTTPException(400, "Phiên tạo / dùng passkey đã hết hạn — thử lại.") from None
    if data.get("k") != kind:
        raise HTTPException(400, "Phiên passkey không hợp lệ — thử lại.")
    return data


def set_state(response, token: str) -> None:
    response.set_cookie(STATE_COOKIE, token, max_age=STATE_SECONDS, httponly=True, secure=get_settings().secure_cookies(),
                        samesite="strict", path=STATE_PATH)


def clear_state(response) -> None:
    response.delete_cookie(STATE_COOKIE, path=STATE_PATH)


# ---------------------------------------------------------------- invites
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_invite(db: Session, user: User, by: str, now: Optional[dt.datetime] = None) -> Tuple[str, dt.datetime]:
    now = now or dt.datetime.utcnow()
    db.query(PasskeyInvite).filter(PasskeyInvite.user_id == user.id, PasskeyInvite.used_at.is_(None)).delete(synchronize_session=False)
    token = secrets.token_urlsafe(32)
    exp = now + dt.timedelta(hours=INVITE_HOURS)
    db.add(PasskeyInvite(user_id=user.id, token_hash=_hash(token), expires_at=exp, created_by=by, created_at=now))
    return token, exp


def find_invite(db: Session, token: Optional[str], now: Optional[dt.datetime] = None) -> PasskeyInvite:
    now = now or dt.datetime.utcnow()
    if not token or len(token) > 200:
        raise HTTPException(400, "Link mời không hợp lệ.")
    inv = db.query(PasskeyInvite).filter(PasskeyInvite.token_hash == _hash(token)).first()
    if not inv or inv.used_at is not None or inv.expires_at < now:
        raise HTTPException(400, "Link mời đã dùng hoặc đã hết hạn — nhờ quản trị tạo link mới.")
    return inv


# ---------------------------------------------------------------- registration
def registration_options(db: Session, user: User, request: Request) -> Tuple[str, bytes]:
    """JSON options for navigator.credentials.create() and the challenge."""
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers import base64url_to_bytes
    from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor, ResidentKeyRequirement,
                                          UserVerificationRequirement)
    s = get_settings()
    require_public_url(request)
    rp_id, _ = relying_party(request, s)
    existing = db.query(UserPasskey).filter(UserPasskey.user_id == user.id).all()
    if len(existing) >= MAX_PER_USER:
        raise HTTPException(409, f"Mỗi tài khoản tối đa {MAX_PER_USER} passkey — xoá bớt passkey cũ trước.")
    opts = generate_registration_options(
        rp_id=rp_id, rp_name=s.passkey_rp_name[:64], user_id=f"ico-user-{user.id}".encode(), user_name=user.username,
        user_display_name=user.display_name or user.username, timeout=TIMEOUT_MS,
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,
                                                               user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id)) for p in existing])
    return options_to_json(opts), opts.challenge


def finish_registration(db: Session, user: User, request: Request, challenge: bytes, credential: Dict[str, Any], name: str) -> UserPasskey:
    from webauthn import verify_registration_response
    from webauthn.helpers import bytes_to_base64url
    rp_id, origin = relying_party(request)
    try:
        v = verify_registration_response(credential=credential, expected_challenge=challenge, expected_rp_id=rp_id,
                                         expected_origin=origin, require_user_verification=True)
    except Exception:  # noqa: BLE001 — library raises several types; message is generic on purpose
        logger.warning("passkey registration rejected", exc_info=True)
        raise HTTPException(400, "Không xác minh được passkey — mở ứng dụng đúng địa chỉ chính thức rồi thử lại.") from None
    cid = bytes_to_base64url(v.credential_id)
    if db.query(UserPasskey.id).filter(UserPasskey.credential_id == cid).first():
        raise HTTPException(409, "Passkey này đã được đăng ký.")
    transports = (credential.get("response") or {}).get("transports") if isinstance(credential.get("response"), dict) else None
    pk = UserPasskey(user_id=user.id, credential_id=cid, public_key=bytes_to_base64url(v.credential_public_key),
                     sign_count=v.sign_count, aaguid=str(v.aaguid)[:40] if v.aaguid else None,
                     backed_up=bool(getattr(v, "credential_backed_up", False)),
                     transports=",".join(t for t in (transports or []) if isinstance(t, str))[:120] or None,
                     name=(name or "").strip()[:80] or default_name(request))
    db.add(pk)
    return pk


def default_name(request: Request) -> str:
    ua = (request.headers.get("user-agent") or "").lower()
    for key, label in (("iphone", "iPhone"), ("ipad", "iPad"), ("android", "Android"), ("mac os", "Mac"), ("windows", "Windows")):
        if key in ua:
            return f"Passkey trên {label}"
    return "Passkey"


# ---------------------------------------------------------------- authentication
def authentication_options(request: Request) -> Tuple[str, bytes]:
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import UserVerificationRequirement
    rp_id, _ = relying_party(request)
    opts = generate_authentication_options(rp_id=rp_id, timeout=TIMEOUT_MS, user_verification=UserVerificationRequirement.REQUIRED)
    return options_to_json(opts), opts.challenge


def finish_authentication(db: Session, request: Request, challenge: bytes, credential: Dict[str, Any]) -> User:
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes
    cid = credential.get("id") if isinstance(credential, dict) else None
    if not isinstance(cid, str) or len(cid) > 512:
        raise HTTPException(400, "Passkey không hợp lệ.")
    pk = db.query(UserPasskey).filter(UserPasskey.credential_id == cid).first()
    if not pk:
        raise HTTPException(401, "Passkey này không còn được đăng ký — đăng nhập cách khác hoặc nhờ quản trị cấp link mới.")
    rp_id, origin = relying_party(request)
    try:
        v = verify_authentication_response(credential=credential, expected_challenge=challenge, expected_rp_id=rp_id, expected_origin=origin,
                                           credential_public_key=base64url_to_bytes(pk.public_key),
                                           credential_current_sign_count=pk.sign_count or 0, require_user_verification=True)
    except Exception:  # noqa: BLE001
        logger.warning("passkey assertion rejected", exc_info=True)
        raise HTTPException(401, "Không xác minh được passkey.") from None
    user = db.get(User, pk.user_id)
    if not user:
        raise HTTPException(401, "Tài khoản không còn tồn tại.")
    pk.sign_count = v.new_sign_count
    pk.last_used_at = dt.datetime.utcnow()
    return user


def passkey_out(p: UserPasskey) -> Dict[str, Any]:
    from .timeutil import to_iso_utc
    return {"id": p.id, "name": p.name, "createdAt": to_iso_utc(p.created_at), "lastUsedAt": to_iso_utc(p.last_used_at) if p.last_used_at else None,
            "synced": bool(p.backed_up), "transports": (p.transports or "").split(",") if p.transports else []}


def parse_credential(raw: Any) -> Dict[str, Any]:
    """The browser's PublicKeyCredential as JSON; bounded before the library parses it."""
    if isinstance(raw, str):
        if len(raw) > 64 * 1024:
            raise HTTPException(413, "Dữ liệu passkey quá lớn.")
        try:
            raw = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "Dữ liệu passkey không hợp lệ.") from None
    if not isinstance(raw, dict) or len(json.dumps(raw)) > 64 * 1024:
        raise HTTPException(400, "Dữ liệu passkey không hợp lệ.")
    return raw


__all__: List[str] = ["relying_party", "registration_options", "finish_registration", "authentication_options",
                      "finish_authentication", "create_invite", "find_invite"]
