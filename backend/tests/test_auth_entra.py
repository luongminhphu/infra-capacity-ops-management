"""Sign-in with Microsoft Entra ID: the full OIDC round trip against a fake tenant
(discovery, JWKS and token endpoint served from memory, ID tokens signed with a test RSA key)."""
import time
import urllib.parse
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from jose.utils import long_to_base64

from app import auth_entra
from app.config import get_settings

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ISSUER = f"https://login.example.test/{TENANT}/v2.0"
KID = "test-kid"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_pem = _key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
_pub = _key.public_key().public_numbers()
JWKS = {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": KID,
                  "n": long_to_base64(_pub.n).decode(), "e": long_to_base64(_pub.e).decode()}]}
META = {
    "issuer": "https://login.example.test/{tenantid}/v2.0",
    "authorization_endpoint": f"https://login.example.test/{TENANT}/oauth2/v2.0/authorize",
    "token_endpoint": f"https://login.example.test/{TENANT}/oauth2/v2.0/token",
    "end_session_endpoint": f"https://login.example.test/{TENANT}/oauth2/v2.0/logout",
    "jwks_uri": "https://login.example.test/keys",
}


class FakeTenant:
    """Stands in for login.microsoftonline.com."""

    def __init__(self):
        self.claims = {}
        self.nonce = None
        self.token_requests = []

    def __call__(self, url, data=None, timeout=15):
        if url.endswith("/.well-known/openid-configuration"):
            return META
        if url == META["jwks_uri"]:
            return JWKS
        if url == META["token_endpoint"]:
            self.token_requests.append(data)
            now = int(time.time())
            claims = {"iss": ISSUER, "aud": CLIENT, "tid": TENANT, "iat": now, "nbf": now, "exp": now + 3600,
                      "nonce": self.nonce, "oid": "oid-alice", "sub": "sub-alice",
                      "preferred_username": "Alice@Corp.example", "name": "Alice Nguyen", "amr": ["pwd", "mfa"]}
            claims.update(self.claims)
            claims = {k: v for k, v in claims.items() if v is not None}
            return {"id_token": jwt.encode(claims, _pem, algorithm="RS256", headers={"kid": KID}),
                    "token_type": "Bearer"}
        raise AssertionError(f"unexpected call {url}")


@pytest.fixture()
def entra(monkeypatch):
    s = get_settings()
    for k, v in {"auth_mode": "both", "entra_tenant_id": TENANT, "entra_client_id": CLIENT,
                 "entra_client_secret": "shh", "entra_redirect_uri": "https://ico.example.test/api/auth/oidc/callback",
                 "entra_authority_host": "https://login.example.test", "entra_require_mfa": "if-present",
                 "entra_allowed_groups": "", "entra_allowed_roles": "", "auth_breakglass_users": "",
                 "cookie_secure": False}.items():
        monkeypatch.setattr(s, k, v)
    tenant = FakeTenant()
    monkeypatch.setattr(auth_entra, "_http_json", tenant)
    auth_entra._cache.clear()
    yield tenant
    auth_entra._cache.clear()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def _start(client, tenant, next_path="/backup"):
    r = client.get("/api/auth/oidc/login", params={"next": next_path}, follow_redirects=False)
    assert r.status_code == 302
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r.headers["location"]).query))
    assert q["client_id"] == CLIENT and q["code_challenge_method"] == "S256" and q["response_type"] == "code"
    tenant.nonce = q["nonce"]
    return q


def _finish(client, q, state=None):
    return client.get("/api/auth/oidc/callback", params={"code": "auth-code", "state": state or q["state"]},
                      follow_redirects=False)


def _error(r):
    assert r.status_code == 302
    loc = urllib.parse.urlparse(r.headers["location"])
    return dict(urllib.parse.parse_qsl(loc.query)).get("login_error", "")


def test_auth_config_reports_modes(client, entra):
    assert client.get("/api/auth/config").json()["entra"] is True


def test_login_round_trip_creates_entra_user(client, entra):
    q = _start(client, entra)
    r = _finish(client, q)
    assert r.status_code == 302 and r.headers["location"] == "/backup"
    # PKCE verifier and the client secret go to the token endpoint.
    sent = entra.token_requests[-1]
    assert sent["code_verifier"] and sent["client_secret"] == "shh" and sent["code"] == "auth-code"
    me = client.get("/api/auth/me").json()
    assert me["username"] == "alice@corp.example"
    # Logging out of an Entra session also signs out of Microsoft.
    out = client.post("/api/auth/logout").json()
    assert out["logoutUrl"].startswith(META["end_session_endpoint"])


def test_second_login_matches_by_object_id(client, entra):
    _finish(client, _start(client, entra))
    entra.claims = {"preferred_username": "alice.renamed@corp.example"}
    client.cookies.clear()
    _finish(client, _start(client, entra))
    assert client.get("/api/auth/me").json()["username"] == "alice.renamed@corp.example"


@pytest.mark.parametrize("claims,msg", [
    ({"aud": "someone-else"}, "không hợp lệ"),
    ({"tid": "99999999-2222-3333-4444-555555555555"}, "tenant"),
    ({"nonce": "replayed"}, "Nonce"),
    ({"amr": ["pwd"]}, "MFA"),
])
def test_bad_tokens_are_rejected(client, entra, claims, msg):
    q = _start(client, entra)
    entra.claims = claims
    r = _finish(client, q)
    assert msg in _error(r)
    assert client.get("/api/auth/me").status_code == 401


def test_state_mismatch_is_rejected(client, entra):
    q = _start(client, entra)
    assert "state" in _error(_finish(client, q, state="forged"))


def test_strict_mfa_requires_amr(client, entra, monkeypatch):
    monkeypatch.setattr(get_settings(), "entra_require_mfa", "strict")
    q = _start(client, entra)
    entra.claims = {"amr": None}
    assert "MFA" in _error(_finish(client, q))


def test_allowed_roles(client, entra, monkeypatch):
    monkeypatch.setattr(get_settings(), "entra_allowed_roles", "ICO.Admin")
    q = _start(client, entra)
    entra.claims = {"roles": ["ICO.Reader"], "oid": str(uuid.uuid4()), "preferred_username": "bob@corp.example"}
    assert "quyền" in _error(_finish(client, q))
    client.cookies.clear()
    q = _start(client, entra)
    entra.claims = {"roles": ["ICO.Admin"], "oid": str(uuid.uuid4()), "preferred_username": "carol@corp.example"}
    assert _finish(client, q).headers["location"] == "/backup"


def test_local_account_is_never_taken_over(client, entra):
    q = _start(client, entra)
    entra.claims = {"preferred_username": "admin", "oid": str(uuid.uuid4())}
    assert "cục bộ" in _error(_finish(client, q))


def test_open_redirect_is_blocked(client, entra):
    for bad in ("https://evil.example", "//evil.example", "/\\evil.example"):
        client.cookies.clear()
        q = _start(client, entra, next_path=bad)
        assert _finish(client, q).headers["location"] == "/"


def test_entra_only_mode_disables_local_login_except_breakglass(client, entra, monkeypatch):
    creds = {"username": "admin", "password": "Admin12345!"}
    assert client.post("/api/auth/login", json=creds).status_code == 200
    s = get_settings()
    monkeypatch.setattr(s, "auth_mode", "entra")
    # Existing local session stops working, and new password logins are refused.
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json=creds).status_code == 403
    cfg = client.get("/api/auth/config").json()
    assert cfg["local"] is False and cfg["breakglass"] is False
    monkeypatch.setattr(s, "auth_breakglass_users", "admin")
    assert client.get("/api/auth/config").json()["breakglass"] is True
    assert client.post("/api/auth/login", json=creds).status_code == 200
    assert client.get("/api/auth/me").status_code == 200
