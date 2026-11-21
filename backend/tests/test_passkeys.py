"""Passkeys (WebAuthn) for local accounts + sign-in settings behind Microsoft Entra Application Proxy.

A software authenticator (P-256 key, "none" attestation, CBOR as the spec) plays the iPhone, so the
whole flow runs without a browser: admin invite → passkey creation → usernameless sign-in → revoke."""
import base64
import hashlib
import json
import os

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import SessionLocal
from app.models import PasskeyInvite, User, UserPasskey

from test_spend_opex import ADMIN, _login, admin, app_  # noqa: F401  (fixtures)

ORIGIN, RP_ID = "http://testserver", "testserver"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class SoftAuthenticator:
    """Minimal platform authenticator: one discoverable P-256 credential, user verification done."""

    def __init__(self, rp_id=RP_ID, origin=ORIGIN):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(16)
        self.rp_id, self.origin, self.counter = rp_id, origin, 0

    def _cose(self) -> bytes:
        n = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})

    def create(self, options: dict, origin=None) -> dict:
        client = json.dumps({"type": "webauthn.create", "challenge": options["challenge"], "origin": origin or self.origin}).encode()
        flags = 0x01 | 0x04 | 0x40 | 0x08 | 0x10                     # UP, UV, AT, BE, BS (synced passkey)
        auth = (hashlib.sha256(options["rp"]["id"].encode()).digest() + bytes([flags]) + (0).to_bytes(4, "big")
                + bytes(16) + len(self.cred_id).to_bytes(2, "big") + self.cred_id + self._cose())
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth})
        return {"id": b64u(self.cred_id), "rawId": b64u(self.cred_id), "type": "public-key",
                "response": {"clientDataJSON": b64u(client), "attestationObject": b64u(att), "transports": ["internal", "hybrid"]}}

    def get(self, options: dict, origin=None) -> dict:
        self.counter += 0                                             # iCloud Keychain keeps the counter at 0
        client = json.dumps({"type": "webauthn.get", "challenge": options["challenge"], "origin": origin or self.origin}).encode()
        auth = hashlib.sha256(options["rpId"].encode()).digest() + bytes([0x01 | 0x04 | 0x08 | 0x10]) + self.counter.to_bytes(4, "big")
        sig = self.key.sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64u(self.cred_id), "rawId": b64u(self.cred_id), "type": "public-key",
                "response": {"clientDataJSON": b64u(client), "authenticatorData": b64u(auth), "signature": b64u(sig), "userHandle": None}}


@pytest.fixture(autouse=True)
def _settings(app_):
    s = get_settings()
    old = (s.auth_local_method, s.public_url, s.auth_breakglass_users, s.auth_mode)
    db = SessionLocal()
    db.query(UserPasskey).delete()
    db.query(PasskeyInvite).delete()
    db.query(User).filter(User.username.in_(["phu.iphone", "entra.user@corp.example"])).delete(synchronize_session=False)
    db.commit()
    db.close()
    s.public_url = ORIGIN                      # passkeys / invites need the external URL (here: the test client's)
    yield s
    s.auth_local_method, s.public_url, s.auth_breakglass_users, s.auth_mode = old


def _invite(admin, username="phu.iphone"):
    r = admin.post("/api/admin/users", json={"username": username, "displayName": "Phú (iPhone)"})
    assert r.status_code == 200, r.text
    assert "/#passkey_invite=" in r.json()["inviteUrl"]          # fragment: never sent to the server / logs
    return r.json()["inviteUrl"].split("passkey_invite=")[1]


def _register(client, phone, invite=None, origin=None):
    opts = client.post("/api/auth/passkey/register/options", json={"invite": invite} if invite else {})
    assert opts.status_code == 200, opts.text
    o = opts.json()
    assert o["authenticatorSelection"]["residentKey"] == "required" and o["authenticatorSelection"]["userVerification"] == "required"
    return client.post("/api/auth/passkey/register/verify", json={"credential": phone.create(o, origin), "invite": invite, "name": "iPhone 16"})


def _signin(client, phone, origin=None):
    o = client.post("/api/auth/passkey/login/options").json()
    assert o["rpId"] == RP_ID and o["userVerification"] == "required" and o.get("allowCredentials", []) == []   # usernameless
    return client.post("/api/auth/passkey/login/verify", json={"credential": phone.get(o, origin)})


def test_invite_register_and_usernameless_signin(app_, admin):
    token = _invite(admin)
    anon = TestClient(app_)
    info = anon.post("/api/auth/passkey/invite", json={"token": token}).json()
    assert info["username"] == "phu.iphone"
    phone = SoftAuthenticator()
    r = _register(anon, phone, invite=token)
    assert r.status_code == 200, r.text
    assert r.json()["passkey"]["synced"] is True and r.json()["passkey"]["name"] == "iPhone 16"
    assert anon.get("/api/auth/me").json()["username"] == "phu.iphone"                 # signed in by the invite
    assert anon.post("/api/auth/passkey/invite", json={"token": token}).status_code == 400   # single use
    # no password: the password form cannot open this account
    assert TestClient(app_).post("/api/auth/login", json={"username": "phu.iphone", "password": "!passkey"}).status_code == 401
    other = TestClient(app_)
    r = _signin(other, phone)
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "phu.iphone" and other.get("/api/auth/me").status_code == 200
    mine = other.get("/api/auth/passkeys").json()
    assert len(mine["items"]) == 1 and mine["hasPassword"] is False
    # the last passkey of a password-less account cannot be deleted by its owner
    assert other.delete(f"/api/auth/passkeys/{mine['items'][0]['id']}").status_code == 409


def test_wrong_origin_replay_and_revoke(app_, admin):
    token = _invite(admin)
    phone = SoftAuthenticator()
    c = TestClient(app_)
    assert _register(c, phone, invite=token, origin="https://evil.example").status_code == 400          # phishing origin
    token = admin.post(f"/api/admin/users/{_uid('phu.iphone')}/passkey-invite").json()["inviteUrl"].split("=")[1]
    assert _register(c, phone, invite=token).status_code == 200
    # replay: a signed assertion for an old challenge is refused
    o_old = c.post("/api/auth/passkey/login/options").json()
    stale = phone.get(o_old)
    c.post("/api/auth/passkey/login/options")                                          # new challenge in the cookie
    assert c.post("/api/auth/passkey/login/verify", json={"credential": stale}).status_code == 401
    # admin revokes the lost phone: sign-in stops at once
    keys = admin.get(f"/api/admin/users/{_uid('phu.iphone')}/passkeys").json()["items"]
    assert admin.delete(f"/api/admin/users/{_uid('phu.iphone')}/passkeys/{keys[0]['id']}").status_code == 200
    assert _signin(TestClient(app_), phone).status_code == 401


def _uid(name):
    db = SessionLocal()
    try:
        return db.query(User.id).filter(User.username == name).scalar()
    finally:
        db.close()


def test_passkey_only_mode_keeps_breakglass_password(app_, admin, _settings):
    _settings.auth_local_method = "passkey"
    _settings.auth_breakglass_users = ADMIN["username"]
    assert _login(app_, ADMIN["username"], ADMIN["password"]).get("/api/auth/me").status_code == 200        # break-glass admin: password still works
    cfg = TestClient(app_).get("/api/auth/config").json()
    assert cfg["passkey"] is True and cfg["password"] is False
    _settings.auth_breakglass_users = ""
    r = TestClient(app_).post("/api/auth/login", json={"username": ADMIN["username"], "password": ADMIN["password"]})
    assert r.status_code == 401 and "Passkey" in r.json()["detail"]
    # same answer for an unknown name (no account enumeration)
    assert TestClient(app_).post("/api/auth/login", json={"username": "ghost", "password": "x"}).json()["detail"] == r.json()["detail"]


def test_signed_in_user_adds_passkey_and_entra_user_cannot(app_, admin):
    phone = SoftAuthenticator()
    assert _register(admin, phone).status_code == 200                                 # admin (password account) adds an iPhone
    assert _signin(TestClient(app_), phone).json()["username"] == ADMIN["username"]
    db = SessionLocal()
    db.add(User(username="entra.user@corp.example", password_hash="!entra", auth_source="entra", external_id="oid-1"))
    db.commit()
    db.close()
    from app.security import create_access_token
    c = TestClient(app_)
    c.cookies.set("vco_session", create_access_token("entra.user@corp.example", "entra"))
    assert c.post("/api/auth/passkey/register/options", json={}).status_code == 403
    assert TestClient(app_).post("/api/auth/passkey/register/options", json={}).status_code == 401


def test_admin_user_rules_and_app_proxy_status(app_, admin, _settings):
    assert admin.post("/api/admin/users", json={"username": "A B"}).status_code == 400
    _invite(admin)
    assert admin.post("/api/admin/users", json={"username": "phu.iphone"}).status_code == 409
    nobody = _login(app_, "viewer1")
    assert nobody.get("/api/admin/users").status_code == 403 and nobody.post("/api/admin/users", json={"username": "x.y.z"}).status_code == 403
    row = next(u for u in admin.get("/api/admin/users").json()["items"] if u["username"] == "phu.iphone")
    assert row["hasPassword"] is False and row["passkeys"] == 0 and row["inviteExpiresAt"]
    # Người dùng & quyền: the same table carries the module role; changing it goes through the audited spend API
    users = admin.get("/api/admin/users").json()
    assert row["spendRole"] == "none" and row["spendRoleSource"] is None
    assert users["spend"]["on"] is True and [r["value"] for r in users["spend"]["roles"]] == ["none", "viewer", "editor", "approver"]
    assert admin.put(f"/api/spend/admin/users/{row['id']}/role", json={"role": "editor"}).status_code == 200
    row = next(u for u in admin.get("/api/admin/users").json()["items"] if u["username"] == "phu.iphone")
    assert row["spendRole"] == "editor" and row["spendRoleSource"] == "ui"
    # behind App Proxy: PUBLIC_URL decides the redirect URI, passkey domain, secure cookies and invite links
    _settings.public_url = "https://ico-contoso.msappproxy.net/"
    st = admin.get("/api/admin/auth-status").json()
    assert st["rpId"] == "ico-contoso.msappproxy.net" and st["origin"] == "https://ico-contoso.msappproxy.net"
    assert st["secureCookies"] is True and st["publicUrl"] == "https://ico-contoso.msappproxy.net"
    # Audit: FORWARDED_ALLOW_IPS left at 127.0.0.1 behind App Proxy → every user shares the proxy IP for the login guard
    fwd = next(c for c in st["checks"] if c["text"].startswith("FORWARDED_ALLOW_IPS"))
    assert fwd["ok"] is False and "30 lần sai" in fwd["fix"]
    assert _settings.oidc_redirect_uri() == "https://ico-contoso.msappproxy.net/api/auth/oidc/callback"
    inv = admin.post(f"/api/admin/users/{row['id']}/passkey-invite").json()["inviteUrl"]
    assert inv.startswith("https://ico-contoso.msappproxy.net/#passkey_invite=")
    # a passkey created for testserver does not work for the external name (bound to the RP ID)
    o = TestClient(app_).post("/api/auth/passkey/login/options").json()
    assert o["rpId"] == "ico-contoso.msappproxy.net"


def _old_session(client, username, minutes=30):
    """Session cookie signed in `minutes` ago (step-up window is 10 minutes)."""
    import datetime as dt
    from app.security import create_access_token
    db = SessionLocal()
    u = db.query(User).filter(User.username == username).one()
    tok = create_access_token(u.username, "local", u.session_version or 0, int(dt.datetime.utcnow().timestamp()) - minutes * 60)
    db.close()
    client.cookies.set("vco_session", tok)
    return client


def test_step_up_before_changing_passkeys(app_, admin):
    token = _invite(admin)
    phone = SoftAuthenticator()
    assert _register(TestClient(app_), phone, invite=token).status_code == 200
    c = _old_session(TestClient(app_), "phu.iphone")
    assert c.get("/api/auth/me").status_code == 200                                   # old session still valid for reading
    assert c.post("/api/auth/passkey/register/options", json={}).status_code == 428     # stolen cookie alone cannot add a key
    # step-up with the account's own passkey, then it works
    o = c.post("/api/auth/passkey/login/options").json()
    assert c.post("/api/auth/passkey/reauth/verify", json={"credential": phone.get(o)}).status_code == 200
    assert c.post("/api/auth/passkey/register/options", json={}).status_code == 200
    # another account's passkey cannot be used for the step-up
    other = SoftAuthenticator()
    assert _register(admin, other).status_code == 200
    c2 = _old_session(TestClient(app_), "phu.iphone")
    o = c2.post("/api/auth/passkey/login/options").json()
    assert c2.post("/api/auth/passkey/reauth/verify", json={"credential": other.get(o)}).status_code == 403


def test_revoke_ends_sessions_and_public_url_required(app_, admin, _settings):
    token = _invite(admin)
    phone = SoftAuthenticator()
    thief = TestClient(app_)
    assert _register(thief, phone, invite=token).status_code == 200 and thief.get("/api/auth/me").status_code == 200
    keys = admin.get(f"/api/admin/users/{_uid('phu.iphone')}/passkeys").json()["items"]
    admin.delete(f"/api/admin/users/{_uid('phu.iphone')}/passkeys/{keys[0]['id']}")
    assert thief.get("/api/auth/me").status_code == 401                                 # lost phone: its session ends too
    _settings.public_url = ""
    assert admin.post(f"/api/admin/users/{_uid('phu.iphone')}/passkey-invite").status_code == 503
    assert admin.post("/api/auth/passkey/register/options", json={}).status_code == 503
