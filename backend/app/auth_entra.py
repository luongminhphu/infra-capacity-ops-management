"""Sign-in with Microsoft Entra ID (OpenID Connect, authorization code flow + PKCE).

Deployment model: the app is published through Microsoft Entra application proxy with
Entra ID pre-authentication, and signs users in itself with the same app registration:

    browser ──▶ App Proxy (pre-auth, Conditional Access: MFA) ──▶ connector ──▶ ico-app
              └─ /api/auth/oidc/login ─▶ login.microsoftonline.com ─▶ /api/auth/oidc/callback

* MFA is enforced by a Conditional Access policy on the enterprise application; the app
  double-checks the `amr` claim of the ID token (ENTRA_REQUIRE_MFA).
* Access can be limited to assigned users (enterprise app "Assignment required"), and
  optionally to group object IDs or app roles carried in the token.
* Tokens are validated locally: RS256 signature against the tenant JWKS, issuer, audience,
  expiry, nonce and tenant id. Only the ID token is used; no Graph call is made.
"""
import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from jose import jwt
from jose.exceptions import JWTError

from .config import Settings

FLOW_COOKIE = "ico_oidc"
_cache: Dict[str, Tuple[float, Any]] = {}
CACHE_SECONDS = 3600


class EntraError(Exception):
    """Sign-in failure with a message safe to show to the user."""


# ---------------------------------------------------------------------------
# Metadata (discovery + signing keys), cached
# ---------------------------------------------------------------------------
def _http_json(url: str, data: Optional[Dict[str, str]] = None, timeout: int = 15) -> Dict[str, Any]:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if body else "GET",
                                 headers={"Accept": "application/json",
                                          **({"Content-Type": "application/x-www-form-urlencoded"} if body else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:   # honours HTTPS_PROXY from the environment
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read() or b"{}")
            desc = (err.get("error_description") or err.get("error") or "").split("\r\n")[0]
        except ValueError:
            desc = exc.reason
        raise EntraError(f"Entra ID từ chối yêu cầu ({exc.code}): {desc}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EntraError(f"Không kết nối được tới Entra ID ({urllib.parse.urlparse(url).netloc}): {exc}") from exc


def _cached(key: str, loader, force: bool = False):
    hit = _cache.get(key)
    if hit and not force and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = loader()
    _cache[key] = (time.time(), value)
    return value


def metadata(s: Settings) -> Dict[str, Any]:
    url = f"{s.entra_authority_host.rstrip('/')}/{s.entra_tenant_id}/v2.0/.well-known/openid-configuration"
    return _cached("meta:" + url, lambda: _http_json(url))


def _jwks(s: Settings, force: bool = False) -> Dict[str, Any]:
    uri = metadata(s)["jwks_uri"]
    return _cached("jwks:" + uri, lambda: _http_json(uri), force)


def _signing_key(s: Settings, kid: Optional[str]) -> Dict[str, Any]:
    for force in (False, True):                      # keys rotate: refresh once on an unknown kid
        for k in _jwks(s, force).get("keys", []):
            if k.get("kid") == kid:
                return k
    raise EntraError("Không tìm thấy khoá ký của token (kid) trong JWKS của tenant.")


# ---------------------------------------------------------------------------
# Login request / callback
# ---------------------------------------------------------------------------
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def safe_next(target: Optional[str]) -> str:
    """Only same-site relative paths — no open redirect."""
    t = (target or "/").strip()
    if not t.startswith("/") or t.startswith("//") or "\\" in t:
        return "/"
    return t


def build_login(s: Settings, next_path: Optional[str]) -> Tuple[str, str]:
    """(authorize URL, signed flow cookie value)."""
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    params = {
        "client_id": s.entra_client_id, "response_type": "code", "redirect_uri": s.oidc_redirect_uri(),
        "response_mode": "query", "scope": "openid profile email", "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    url = metadata(s)["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)
    flow = jwt.encode({"st": state, "n": nonce, "v": verifier, "nx": safe_next(next_path), "exp": int(time.time()) + 600},
                      s.jwt_secret, algorithm=s.jwt_algorithm)
    return url, flow


def _csv(v: str) -> List[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def validate_id_token(s: Settings, id_token: str, nonce: str, access_token: Optional[str] = None) -> Dict[str, Any]:
    try:
        header = jwt.get_unverified_header(id_token)
    except JWTError as exc:
        raise EntraError("ID token không hợp lệ.") from exc
    if header.get("alg") != "RS256":
        raise EntraError("Thuật toán ký của token không được chấp nhận.")
    key = _signing_key(s, header.get("kid"))
    issuer = metadata(s)["issuer"].replace("{tenantid}", s.entra_tenant_id)
    try:
        claims = jwt.decode(id_token, key, algorithms=["RS256"], audience=s.entra_client_id, issuer=issuer,
                            access_token=access_token, options={"leeway": 120})
    except JWTError as exc:
        raise EntraError(f"ID token không hợp lệ: {exc}") from exc
    if claims.get("nonce") != nonce:
        raise EntraError("Nonce không khớp — yêu cầu đăng nhập có thể đã bị phát lại.")
    if s.entra_tenant_id.count("-") == 4 and claims.get("tid") != s.entra_tenant_id:
        raise EntraError("Tài khoản không thuộc tenant được phép.")
    amr = claims.get("amr")
    mode = (s.entra_require_mfa or "if-present").lower()
    if mode == "strict" and not (isinstance(amr, list) and "mfa" in amr):
        raise EntraError("Đăng nhập chưa qua xác thực đa yếu tố (MFA).")
    if mode == "if-present" and isinstance(amr, list) and amr and "mfa" not in amr:
        raise EntraError("Đăng nhập chưa qua xác thực đa yếu tố (MFA).")
    groups, roles = _csv(s.entra_allowed_groups), _csv(s.entra_allowed_roles)
    if roles:
        roles = roles + s.entra_admin_role_list() + s.entra_owner_role_list()   # admins and system owners are always allowed in
    if groups or roles:
        if groups and "_claim_names" in claims and "groups" in (claims.get("_claim_names") or {}):
            raise EntraError("Tài khoản thuộc quá nhiều nhóm để kiểm tra trong token — dùng app role thay cho nhóm.")
        ok = bool(set(groups) & set(claims.get("groups") or [])) or bool(set(roles) & set(claims.get("roles") or []))
        if not ok:
            raise EntraError("Tài khoản chưa được cấp quyền truy cập ứng dụng này.")
    return claims


def access_scope_for(s: Settings, claims: Dict[str, Any]) -> str:
    """"owner" when the token carries the owner app role and nothing that grants the full app (admin, an allowed role,
    a spend role, an allowed group); otherwise "full". Without the owner role nothing changes: "full"."""
    roles = set(claims.get("roles") or [])
    if not roles & set(s.entra_owner_role_list()):
        return "full"
    full = set(s.entra_admin_role_list()) | set(_csv(s.entra_allowed_roles)) | {
        r.strip() for v in (s.entra_spend_viewer_roles, s.entra_spend_editor_roles, s.entra_spend_approver_roles)
        for r in (v or "").split(",") if r.strip()}
    in_group = bool(set(_csv(s.entra_allowed_groups)) & set(claims.get("groups") or []))
    return "full" if (roles & full or in_group) else "owner"


def complete_login(s: Settings, flow_cookie: Optional[str], state: Optional[str], code: Optional[str]) -> Tuple[Dict[str, Any], str]:
    """(validated ID token claims, next path)."""
    if not flow_cookie:
        raise EntraError("Phiên đăng nhập đã hết hạn — vui lòng đăng nhập lại.")
    try:
        flow = jwt.decode(flow_cookie, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except JWTError as exc:
        raise EntraError("Phiên đăng nhập đã hết hạn — vui lòng đăng nhập lại.") from exc
    if not state or not secrets.compare_digest(str(state), str(flow.get("st"))):
        raise EntraError("Tham số state không khớp.")
    if not code:
        raise EntraError("Thiếu mã xác thực từ Entra ID.")
    tok = _http_json(metadata(s)["token_endpoint"], {
        "client_id": s.entra_client_id, "client_secret": s.entra_client_secret, "grant_type": "authorization_code",
        "code": code, "redirect_uri": s.oidc_redirect_uri(), "code_verifier": flow["v"], "scope": "openid profile email",
    })
    if not tok.get("id_token"):
        raise EntraError("Entra ID không trả về ID token.")
    return validate_id_token(s, tok["id_token"], flow["n"], tok.get("access_token")), flow.get("nx") or "/"


def logout_url(s: Settings) -> Optional[str]:
    try:
        end = metadata(s).get("end_session_endpoint")
    except EntraError:
        return None
    if not end:
        return None
    post = s.entra_post_logout_redirect_uri or (s.public_base() + "/" if s.public_base() else "")
    q = {"post_logout_redirect_uri": post} if post else {}
    return end + ("?" + urllib.parse.urlencode(q) if q else "")
