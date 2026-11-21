"""Read-only MCP server: admin tokens, JSON-RPC protocol, tools, redaction, performance capture."""
import datetime as dt
import json
import logging

import pytest

from app import observability as obs
from app.config import get_settings

ADMIN = {"username": "admin", "password": "Admin12345!"}


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr(get_settings(), "mcp_enabled", True)
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json=ADMIN).status_code == 200
        yield c


def _token(client, name="claude-code"):
    r = client.post("/api/admin/mcp/tokens", json={"name": name, "days": 30})
    assert r.status_code == 200, r.text
    return r.json()


def _rpc(client, token, method, params=None, id_=1, **kw):
    body = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers={"Authorization": f"Bearer {token}", **kw.get("headers", {})})


def _call(client, token, tool, args=None):
    r = _rpc(client, token, "tools/call", {"name": tool, "arguments": args or {}})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


def test_me_reports_admin(client):
    assert client.get("/api/auth/me").json()["isAdmin"] is True


def test_protocol_handshake_and_tools(client):
    tok = _token(client)["token"]
    r = _rpc(client, tok, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "test", "version": "1"}})
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-06-18" and "tools" in res["capabilities"]
    # Notifications get 202 with no body.
    n = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert n.status_code == 202
    tools = _rpc(client, tok, "tools/list").json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"ico_app_overview", "ico_search_logs", "ico_error_summary", "ico_endpoint_performance", "ico_slow_queries",
            "ico_sync_history", "ico_performance_trend", "ico_slow_requests"} <= names          # old names: aliases
    assert all(t["annotations"]["readOnlyHint"] for t in tools)
    assert _rpc(client, tok, "prompts/get", {"name": "health_review"}).json()["result"]["messages"]
    assert _rpc(client, tok, "nope").json()["error"]["code"] == -32601
    assert client.get("/mcp").status_code == 405


def test_auth_rejections(client, monkeypatch):
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    assert _rpc(client, "ico_mcp_wrong", "ping").status_code == 401
    created = _token(client)
    tok = created["token"]
    assert _rpc(client, tok, "ping").status_code == 200
    # A browser page on another origin cannot use the token.
    assert _rpc(client, tok, "ping", headers={"Origin": "https://evil.example"}).status_code == 403
    # Owner no longer admin → token stops working.
    monkeypatch.setattr(get_settings(), "admin_users", "someone-else")
    assert _rpc(client, tok, "ping").status_code == 401
    monkeypatch.setattr(get_settings(), "admin_users", "")
    # Revoked.
    assert client.delete(f"/api/admin/mcp/tokens/{created['id']}").status_code == 200
    assert _rpc(client, tok, "ping").status_code == 401
    # Disabled server.
    monkeypatch.setattr(get_settings(), "mcp_enabled", False)
    assert _rpc(client, "ico_mcp_x", "ping").status_code == 404


def test_expired_token(client):
    from app.database import SessionLocal
    from app.models import ApiToken
    created = _token(client)
    db = SessionLocal()
    t = db.get(ApiToken, created["id"])
    t.expires_at = dt.datetime.utcnow() - dt.timedelta(minutes=1)
    db.commit()
    db.close()
    assert _rpc(client, created["token"], "ping").status_code == 401


def test_non_admin_cannot_manage_tokens(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_users", "someone-else")
    assert client.post("/api/admin/mcp/tokens", json={"name": "x"}).status_code == 403
    assert client.get("/api/admin/mcp").status_code == 403


def test_logs_are_redacted_grouped_and_masked(client, monkeypatch):
    tok = _token(client)["token"]
    log = logging.getLogger("vco.mcptest")
    for i in range(3):
        log.error("Login to vcenter01.corp.local (10.1.2.%d) failed: password=Sup3rS3cret! token=abcdefgh123", i)
    try:
        raise ValueError("boom 42")
    except ValueError:
        log.exception("Collector crashed for job 1234")
    groups = _call(client, tok, "error_summary", {"since_minutes": 10})["groups"]
    top = next(g for g in groups if g["logger"] == "vco.mcptest" and g["exception"] is None)
    assert top["count"] == 3
    text = json.dumps(groups)
    assert "Sup3rS3cret" not in text and "abcdefgh123" not in text and "<redacted>" in text
    assert "vcenter01.corp.local" not in text and "10.1.2." not in text and "<host:" in text
    assert any(g["exception"] == "ValueError" and "boom" in (g["sampleTraceback"] or "") for g in groups)
    found = _call(client, tok, "search_logs", {"level": "ERROR", "contains": "crashed"})
    assert found["count"] == 1 and "Traceback" in found["logs"][0]["traceback"]
    # Nothing secret was stored in the database either.
    from app.database import SessionLocal
    from app.models import AppLog
    db = SessionLocal()
    assert not any("Sup3rS3cret" in (r.message or "") for r in db.query(AppLog).all())
    db.close()
    # Every tool call is audited.
    audit = client.get("/api/admin/mcp/audit").json()
    assert any(a["action"] == "mcp.tool" and a["detail"]["tool"] == "ico_error_summary" for a in audit)   # old name resolved


def test_performance_is_captured_per_route_template(client):
    tok = _token(client)["token"]
    for _ in range(3):
        client.get("/api/auth/me")
    perf = _call(client, tok, "endpoint_performance", {"since_minutes": 10, "sort": "count", "limit": 200})
    me = next(e for e in perf["endpoints"] if e["endpoint"] == "GET /api/auth/me")
    assert me["requests"] >= 3 and me["p95Ms"] is not None and me["avgDbQueries"] > 0
    client.delete("/api/admin/mcp/tokens/999999")    # 404 → counted as 4xx under the template
    perf = _call(client, tok, "endpoint_performance", {"since_minutes": 10, "limit": 200})
    assert any(e["endpoint"] == "DELETE /api/admin/mcp/tokens/{token_id}" and e["errors4xx"] >= 1 for e in perf["endpoints"])
    # Buckets survive a flush to the database.
    obs.flush(force_all_buckets=True)
    trend = _call(client, tok, "performance_trend", {"hours": 1, "endpoint": "GET /api/auth/me"})
    assert sum(p["requests"] for p in trend["points"]) >= 3
    overview = _call(client, tok, "app_overview")
    assert overview["database"]["pingMs"] is not None and overview["requestsLast1h"]["requests"] > 0
    assert "runs" in _call(client, tok, "sync_history")
    assert "queries" in _call(client, tok, "slow_queries")
    assert "requests" in _call(client, tok, "slow_requests")


def test_redact_patterns():
    s = obs.redact("postgresql+psycopg2://ico:pa55word@db:5432/x Authorization: Bearer abc.def.ghijklmnop "
                   "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlMTIz {\"api_key\": \"k123456\"}")
    assert "pa55word" not in s and "abc.def.ghijklmnop" not in s and "eyJhbGci" not in s and "k123456" not in s
    assert obs.normalize_sql("SELECT * FROM t WHERE a = 'x' AND b IN (?, ?, ?)") == "SELECT * FROM t WHERE a = '?' AND b IN (…)"


# ---------------------------------------------------------------- Settings → AI / MCP server (stored in app_settings)
@pytest.fixture()
def clean_config(client):
    client.delete("/api/admin/mcp/config")
    yield client
    client.delete("/api/admin/mcp/config")


def test_settings_ui_overrides_configmap_and_reset_returns_to_it(clean_config, monkeypatch):
    client = clean_config
    monkeypatch.setattr(get_settings(), "mcp_enabled", False)          # ConfigMap says off
    info = client.get("/api/admin/mcp").json()
    assert info["enabled"] is False and info["saved"] == [] and info["defaults"]["enabled"] is False
    tok = None
    # Turn it on from the UI: endpoint and token creation follow at once in this pod.
    r = client.put("/api/admin/mcp/config", json={"enabled": True, "tokenMaxDays": 30, "ratePerMinute": 60,
                                                   "allowedOrigins": "https://ico.example.vn, http://localhost:8765/"})
    assert r.status_code == 200, r.text
    assert r.json()["allowedOrigins"] == ["https://ico.example.vn", "http://localhost:8765"]
    assert set(r.json()["saved"]) == {"enabled", "tokenMaxDays", "ratePerMinute", "allowedOrigins"}
    t = client.post("/api/admin/mcp/tokens", json={"name": "cfg", "days": 90}).json()
    tok = t["token"]
    assert dt.datetime.fromisoformat(t["expiresAt"].rstrip("Z")) < dt.datetime.utcnow() + dt.timedelta(days=31)   # capped at 30
    assert _rpc(client, tok, "tools/list").status_code == 200
    assert _rpc(client, tok, "tools/list", headers={"Origin": "https://ico.example.vn"}).status_code == 200
    assert _rpc(client, tok, "tools/list", headers={"Origin": "https://evil.example"}).status_code == 403
    # Every change is in the MCP audit trail.
    assert any(a["action"] == "mcp.config" and a["detail"]["after"]["enabled"] for a in client.get("/api/admin/mcp/audit").json())
    # Off from the UI → endpoint 404 even though the token is valid.
    assert client.put("/api/admin/mcp/config", json={"enabled": False}).status_code == 200
    assert _rpc(client, tok, "tools/list").status_code == 404
    # Reset → back to the ConfigMap value (off), no saved key left.
    r = client.delete("/api/admin/mcp/config")
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert client.get("/api/admin/mcp").json()["saved"] == []


def test_settings_validation_and_mask_off_confirmation(clean_config):
    client = clean_config
    for body, needle in (({"tokenMaxDays": 0}, "Hạn token"), ({"tokenMaxDays": 400}, "Hạn token"),
                         ({"ratePerMinute": 5}, "Giới hạn"), ({"enabled": "yes"}, "bật / tắt"),
                         ({"allowedOrigins": "*"}, "Origin không hợp lệ"),
                         ({"allowedOrigins": "https://a.vn/path"}, "Origin không hợp lệ"),
                         ({"allowedOrigins": "javascript:alert(1)"}, "Origin không hợp lệ")):
        r = client.put("/api/admin/mcp/config", json=body)
        assert r.status_code == 400 and needle in r.json()["detail"], (body, r.text)
    # Host masking off sends internal names to the AI: refused without an explicit confirmation.
    r = client.put("/api/admin/mcp/config", json={"maskHosts": False})
    assert r.status_code == 400 and "xác nhận" in r.json()["detail"]
    assert obs.mask_infra("db01.corp.local 10.1.2.3").count("<") == 2
    assert client.put("/api/admin/mcp/config", json={"maskHosts": False, "confirmMaskOff": True}).status_code == 200
    assert obs.mask_infra("db01.corp.local 10.1.2.3") == "db01.corp.local 10.1.2.3"


def test_settings_admin_only(clean_config, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_users", "someone-else")
    assert clean_config.put("/api/admin/mcp/config", json={"enabled": True}).status_code == 403
    assert clean_config.delete("/api/admin/mcp/config").status_code == 403


def test_mask_infra_is_keyed_and_knows_short_names(monkeypatch):
    """Audit: IPs were tagged with plain SHA-1 (reversible by hashing 10.0.0.0/8) and short host names stayed visible."""
    import hashlib
    monkeypatch.setattr(obs, "_inventory_names", lambda: {"esx-a-01"})
    out = obs.mask_infra("connect to esx-a-01 (10.1.2.3) failed")
    assert "esx-a-01" not in out and "10.1.2.3" not in out
    assert hashlib.sha1(b"10.1.2.3").hexdigest()[:6] not in out


def test_entra_token_of_someone_not_signed_in_for_weeks_is_refused(client):
    """Audit: an Entra admin who left kept MCP access until the token expired (admin flag only refreshed at sign-in)."""
    from app.database import SessionLocal
    from app.models import ApiToken, User
    created = _token(client)
    db = SessionLocal()
    u = db.get(User, db.get(ApiToken, created["id"]).user_id)
    src, last, adm = u.auth_source, u.last_login_at, u.is_admin
    u.auth_source, u.is_admin, u.last_login_at = "entra", True, dt.datetime.utcnow() - dt.timedelta(days=1)
    db.commit()
    try:
        assert _rpc(client, created["token"], "ping").status_code == 200          # signed in recently: works
        u.last_login_at = dt.datetime.utcnow() - dt.timedelta(days=30)
        db.commit()
        assert _rpc(client, created["token"], "ping").status_code == 401
    finally:
        u.auth_source, u.last_login_at, u.is_admin = src, last, adm
        db.commit()
        db.close()
