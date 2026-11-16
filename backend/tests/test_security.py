"""Security hardening (audit 2026-11-15, SEC-01/02/03/05/06/07/09/10)."""
import base64
import io
import logging

import pytest
from openpyxl import Workbook, load_workbook
from weasyprint.urls import URLFetcher

from app import report_pdf, sources
from app.config import Settings, get_settings, jwt_secret_problem
from app.database import SessionLocal
from app.excel_style import Formula, _write_rows, csv_safe
from app.models import DataSource

ADMIN = {"username": "admin", "password": "Admin12345!"}
SECRET = "Stored-Secret-Pw!"
# 1x1 transparent PNG
PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json=ADMIN).status_code == 200
        yield c
    db = SessionLocal()
    db.query(DataSource).delete()
    db.commit()
    db.close()
    sources.invalidate_cache()


@pytest.fixture()
def non_admin(monkeypatch):
    """The logged-in local account stops being admin (ADMIN_USERS no longer lists it)."""
    monkeypatch.setattr(get_settings(), "admin_users", "someone-else")


# ---------------------------------------------------------------- SEC-01: PDF render never fetches out

def test_pdf_fetcher_only_serves_data_urls():
    f = report_pdf.safe_url_fetcher()
    for url in ("http://169.254.169.254/latest/meta-data/", "https://example.com/x.png", "file:///etc/passwd",
                "ftp://host/x", "logo.png", "/etc/passwd"):
        with pytest.raises(ValueError):
            f.fetch(url)
    resp = f.fetch(f"data:image/png;base64,{PNG}")
    assert resp.read() == base64.b64decode(PNG)


def test_pdf_render_blocks_http_and_file_in_svg_logo(monkeypatch):
    seen, served = [], []
    real_parent = URLFetcher.fetch
    real_child = report_pdf.DataOnlyURLFetcher.fetch

    def spy_child(self, url, headers=None):
        seen.append(url)
        return real_child(self, url, headers)

    def spy_parent(self, url, headers=None):
        served.append(url)
        return real_parent(self, url, headers)

    monkeypatch.setattr(URLFetcher, "fetch", spy_parent)
    monkeypatch.setattr(report_pdf.DataOnlyURLFetcher, "fetch", spy_child)
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="10" height="10">'
           '<image href="file:///etc/passwd" width="10" height="10"/>'
           '<image xlink:href="http://127.0.0.1:9/meta" width="10" height="10"/></svg>')
    svg_url = "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()
    html = (f'<html><body><img src="http://127.0.0.1:9/a.png"><img src="file:///etc/hostname">'
            f'<img src="{svg_url}"><img src="data:image/png;base64,{PNG}">'
            '<div style="background:url(https://example.com/bg.png)">x</div></body></html>')
    pdf = report_pdf.html_to_pdf_bytes(html)
    assert pdf.startswith(b"%PDF")
    assert "file:///etc/passwd" in seen                              # SVG <image> goes through the same fetcher
    assert "http://127.0.0.1:9/a.png" in seen                       # the attempt reached our fetcher…
    assert served and all(u.startswith("data:") for u in served)   # …but only data: URLs were ever opened


def test_logo_write_needs_admin(client, non_admin):
    assert client.get("/api/settings/logo").status_code == 200
    assert client.put("/api/settings/logo", json={"data_url": f"data:image/png;base64,{PNG}", "filename": "x.png"}).status_code == 403
    assert client.delete("/api/settings/logo").status_code == 403


# ---------------------------------------------------------------- SEC-02: weak JWT secret

@pytest.mark.parametrize("value", ["", "change-me-in-.env-please", "change-me-to-a-long-random-string",
                                   "change-me-to-another-long-random-string", "short-secret-15"])
def test_weak_jwt_secrets_are_refused(value):
    assert jwt_secret_problem(value)


def test_startup_check(caplog):
    from app.main import check_startup_secrets
    assert jwt_secret_problem("test-secret-0123456789") is None                  # 22 chars, used by the suite
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        check_startup_secrets(Settings(jwt_secret="change-me-in-.env-please", secrets_key="x" * 32))
    with caplog.at_level(logging.WARNING, logger="vco.main"):
        check_startup_secrets(Settings(jwt_secret="a" * 40, secrets_key=""))
    assert any("SECRETS_KEY" in r.getMessage() for r in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="vco.main"):
        check_startup_secrets(Settings(jwt_secret="a" * 40, secrets_key="b" * 40, cookie_secure=True))
    assert not caplog.records
    with caplog.at_level(logging.WARNING, logger="vco.main"):                        # SEC2-04: plain-HTTP cookie warned
        check_startup_secrets(Settings(jwt_secret="a" * 40, secrets_key="b" * 40, cookie_secure=False, public_url=""))
    assert any("Secure" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------- SEC-03: CORS

def test_cors_default_closed_and_wildcard_without_credentials(client):
    from app.main import cors_options
    assert Settings(cors_origins="").cors_origin_list() == []
    assert Settings(cors_origins="*").cors_origin_list() == ["*"]
    assert cors_options(["*"])["allow_credentials"] is False
    assert cors_options(["https://ico.corp.example"])["allow_credentials"] is True
    r = client.get("/api/health", headers={"Origin": "https://evil.corp.example"})
    assert "access-control-allow-origin" not in r.headers


# ---------------------------------------------------------------- SEC-05: shared configuration is admin-only

def test_shared_config_writes_need_admin(client, non_admin):
    # Reads stay open to every user (manual sync became admin-only on 16/11: see below).
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/backup/thresholds").status_code == 200
    assert client.get("/api/licensing/rules").status_code == 200
    rule_id = client.get("/api/licensing/rules").json()[0]["id"]
    for method, path, body in (
        ("put", "/api/settings", {"idle_days": 30}),
        ("put", "/api/backup/thresholds", {}),
        ("put", "/api/backup/scope", {"rules": []}),
        ("put", "/api/backup/schedules", {"rules": []}),
        ("post", f"/api/licensing/rules/{rule_id}/deactivate", None),
        ("post", f"/api/licensing/rules/{rule_id}/activate", None),
        ("post", f"/api/licensing/rules/{rule_id}/versions", {"params": {}}),
        ("post", "/api/licensing/entitlements", {"product": "ws_dc"}),
        ("delete", "/api/licensing/entitlements/1", None),
    ):
        r = client.request(method.upper(), path, json=body)
        assert r.status_code == 403, (path, r.status_code)


def test_shared_config_writes_work_for_admin(client):
    assert client.put("/api/settings", json={}).status_code == 200
    assert client.put("/api/backup/thresholds", json={}).status_code == 200
    created = client.post("/api/licensing/entitlements", json={"product": "ws_dc", "quantity": 16})
    assert created.status_code == 200, created.text
    ent_id = next(e["id"] for e in client.get("/api/licensing/entitlements").json()["items"] if e["quantity"] == 16)
    assert client.delete(f"/api/licensing/entitlements/{ent_id}").status_code == 200


# ---------------------------------------------------------------- SEC-06: rule source link

def test_rule_source_url_must_be_http(client):
    rule_id = client.get("/api/licensing/rules").json()[0]["id"]
    for bad in ("javascript:alert(1)", "data:text/html,<script>", "JaVaScRiPt:alert(1)", "https://x.example/\"onmouseover=1",
                "https://x.example/" + "a" * 500):
        r = client.post(f"/api/licensing/rules/{rule_id}/versions", json={"params": {}, "sourceUrl": bad})
        assert r.status_code == 422, bad
    from app.routers.licensing import RuleVersionIn
    assert RuleVersionIn(params={}, sourceUrl=" https://learn.microsoft.com/x ").sourceUrl == "https://learn.microsoft.com/x"
    assert RuleVersionIn(params={}).sourceUrl is None


# ---------------------------------------------------------------- SEC-09: saved secret never sent to a new host

def test_source_test_does_not_reuse_secret_for_other_host(client, monkeypatch):
    body = {"kind": "vcenter", "key": "vc-sec-01", "name": "VC", "host": "vcsa01.corp.example", "port": 443,
            "username": "infra.mon@vsphere.local", "password": SECRET}
    src = client.post("/api/admin/sources", json=body).json()
    used = []
    monkeypatch.setattr(sources, "test_connection", lambda cfg, kind: used.append((cfg.host, cfg.password)) or "OK")

    # Same host / port, empty password: the saved one is used.
    r = client.post("/api/admin/sources/test", json={**body, "id": src["id"], "password": ""})
    assert r.json()["ok"] is True and used == [("vcsa01.corp.example", SECRET)]
    # Another host (or port) with no password typed: refused, nothing sent.
    for change in ({"host": "attacker.example"}, {"port": 8443}):
        r = client.post("/api/admin/sources/test", json={**body, **change, "id": src["id"], "password": ""})
        assert r.json()["ok"] is False and "nhập lại mật khẩu" in r.json()["message"]
    assert len(used) == 1
    # Another host with a password typed in the form: that one is used, never the saved one.
    r = client.post("/api/admin/sources/test", json={**body, "host": "vcsa02.corp.example", "id": src["id"], "password": "typed-pw"})
    assert r.json()["ok"] is True and used[-1] == ("vcsa02.corp.example", "typed-pw")


# ---------------------------------------------------------------- SEC-07: Excel / CSV formula injection

def _roundtrip(wb):
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return load_workbook(buf)


def test_write_rows_keeps_external_text_as_text():
    wb = Workbook()
    ws = wb.active
    evil = ['=HYPERLINK("http://x","y")', "+cmd|' /C calc'!A0", "-2+3", "@SUM(1)", "\tx", "vm-01"]
    _write_rows(ws, 1, ["A", "B", "C", "D", "E", "F", "G"], [evil + [Formula("=SUM(1,2)")]])
    out = _roundtrip(wb).active
    for j, v in enumerate(evil, 1):
        c = out.cell(row=2, column=j)
        assert c.data_type == "s" and c.value == v, v
    assert out.cell(row=2, column=7).data_type == "f" and out.cell(row=2, column=7).value == "=SUM(1,2)"


def test_export_xlsx_endpoint_neutralises_formulas(client):
    payload = {"filename": "x", "sheets": [{"name": "VM", "columns": [{"key": "vm", "label": "=1+1"}, {"key": "n", "label": "n", "fmt": "int"}],
                                            "rows": [{"vm": "=HYPERLINK(\"http://x\")", "n": -5}]}]}
    r = client.post("/api/export/xlsx", json=payload)
    assert r.status_code == 200
    ws = load_workbook(io.BytesIO(r.content)).active
    assert ws["A1"].data_type == "s" and ws["A2"].data_type == "s" and ws["A2"].value == "=HYPERLINK(\"http://x\")"
    assert ws["B2"].value == -5                                       # numbers untouched


def test_csv_safe():
    assert csv_safe("=1+1") == "'=1+1" and csv_safe("-x") == "'-x" and csv_safe("vm-01") == "vm-01"
    assert csv_safe(-5) == -5 and csv_safe(None) is None


def test_sizing_vm_sheet_keeps_code_formulas_and_blocks_vm_names():
    from app import excel_sizing
    wb = Workbook()
    excel_sizing._vm_sheet(wb, [{"name": "=cmd()", "qty": 2, "vcpu": 4, "ramGB": 8, "diskGB": 100, "note": ""}], None, "x")
    ws = _roundtrip(wb)["Danh sách VM"]
    row = next(r for r in ws.iter_rows() if r[0].value == "=cmd()")
    assert row[0].data_type == "s"
    assert row[5].data_type == "f" and row[5].value.startswith("=B")


# ---------------------------------------------------------------- SEC-10: no internal detail in 5xx

def test_backup_sync_error_is_generic(client, monkeypatch, caplog):
    from app.routers import backup as backup_router

    def boom(db, cfg):
        raise RuntimeError("postgresql://ico:pw@db/internal")

    monkeypatch.setattr(backup_router, "sync_netbackup", boom)
    key = get_settings().netbackups()[0].key
    with caplog.at_level(logging.ERROR, logger="vco.routers.backup"):
        r = client.post(f"/api/backup/servers/{key}/sync")
    assert r.status_code == 502 and "postgresql://" not in r.text and "Đồng bộ backup thất bại" in r.json()["detail"]
    assert any(rec.exc_info for rec in caplog.records)                # traceback kept in the server log


# ---------------------------------------------------------------- Round 2 (16/11): admin-only writes on shared data

def test_round2_shared_data_writes_need_admin(client, non_admin):
    """Q1 manual sync, Q2 license data (OS overrides, assessment snapshots, rule verification),
    Q3 inventory import: admins only. Reads stay open."""
    assert client.get("/api/licensing/overrides").status_code == 200
    assert client.get("/api/vcenters").status_code == 200
    rule_id = client.get("/api/licensing/rules").json()[0]["id"]
    vc_key, nbu_key = "any-vc", "any-nbu"            # the admin check runs before the key lookup
    for method, path, body in (
        ("post", "/api/import", {"hosts": [], "vms": []}),
        ("post", f"/api/vcenters/{vc_key}/sync", None),
        ("post", "/api/vcenters/sync-all", None),
        ("post", f"/api/backup/servers/{nbu_key}/sync", None),
        ("post", "/api/backup/sync-all", None),
        ("put", "/api/licensing/overrides", {"vm": "x", "category": "windows_server"}),
        ("delete", "/api/licensing/overrides/1", None),
        ("post", f"/api/licensing/rules/{rule_id}/verify", {"note": "ok"}),
        ("post", "/api/licensing/assessments", {"vcenter": "all"}),
        ("post", "/api/licensing/assessments/1/recompute", None),
        ("delete", "/api/licensing/assessments/1", None),
    ):
        r = client.request(method.upper(), path, json=body)
        assert r.status_code == 403, (path, r.status_code)


def test_import_is_bounded_and_audited(client):
    from app.models import AuditLog
    too_many = {"hosts": [{"host": f"h{i}"} for i in range(5001)], "vms": []}
    assert client.post("/api/import", json=too_many).status_code == 422
    assert client.post("/api/import", json={"hosts": ["not-an-object"], "vms": []}).status_code == 422
    r = client.post("/api/import", json={"hosts": [{"cluster": "C1", "host": "h1", "cores": 8, "ramGB": 64}], "vms": []})
    assert r.status_code == 200 and r.json()["hostCount"] == 1
    db = SessionLocal()
    try:
        assert db.query(AuditLog).filter_by(action="data.import").count() >= 1
    finally:
        db.close()


def test_vcenter_sync_error_is_generic_and_last_error_admin_only(client, monkeypatch, caplog):
    from app.routers import vcenters as vc_router

    def boom(db, cfg, settings):
        raise RuntimeError("postgresql://ico:pw@db/internal")

    from types import SimpleNamespace
    cfg = SimpleNamespace(key="vc-test", name="vc-test", host="vc.example.local", demo=True)
    real = get_settings()
    monkeypatch.setattr(vc_router, "get_settings", lambda: SimpleNamespace(vcenters=lambda: [cfg], **{k: getattr(real, k) for k in ("admin_users",)}))
    monkeypatch.setattr(vc_router, "sync_vcenter", boom)
    key = "vc-test"
    with caplog.at_level(logging.ERROR, logger="vco.routers.vcenters"):
        r = client.post(f"/api/vcenters/{key}/sync")
    assert r.status_code == 502 and "postgresql://" not in r.text and "Đồng bộ thất bại" in r.json()["detail"]
    assert any(rec.exc_info for rec in caplog.records)


def test_public_error_hides_unknown_exceptions():
    from app.collectors.scheduler import public_error
    from app.vcenter.client import VCenterConnectionError
    assert public_error(RuntimeError("password=hunter2")) == "Đồng bộ thất bại. Xem log máy chủ để biết chi tiết."
    assert public_error(VCenterConnectionError("Không kết nối được vCenter")) == "Không kết nối được vCenter"


def test_sizing_pdf_header_cannot_close_style():
    from app.report_sizing_full import _css
    css = _css('ACME</style><h1>x</h1>', 'v"1\nnext', "16/11/2026")
    assert "</style>" not in css and "<h1>" not in css and "\\3C " in css



# ---------------------------------------------------------------- Round 2 PR2: session & HTTP surface

def _fresh_client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_login_is_throttled_after_repeated_failures():
    with _fresh_client() as c:
        for _ in range(5):
            assert c.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
        r = c.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
        # the right password is also refused while locked (no oracle), other names are not affected
        assert c.post("/api/auth/login", json=ADMIN).status_code == 429
        assert c.post("/api/auth/login", json={"username": "nobody", "password": "x"}).status_code == 401


def test_login_guard_backoff_grows_and_success_clears():
    from app import login_guard as g
    g.reset()
    t0 = 1_000_000.0
    for i in range(5):
        g.failed("1.2.3.4", "bob", now=t0 + i)
    assert 55 <= g.retry_after("1.2.3.4", "bob", now=t0 + 5) <= 61          # 60 s after the 5th failure
    g.failed("1.2.3.4", "bob", now=t0 + 70)
    assert 115 <= g.retry_after("1.2.3.4", "bob", now=t0 + 71) <= 121       # then 120 s
    g.succeeded("1.2.3.4", "bob")
    assert g.retry_after("1.2.3.4", "bob", now=t0 + 72) == 0
    g.reset()


def test_logout_revokes_the_session_on_the_server():
    with _fresh_client() as c:
        assert c.post("/api/auth/login", json=ADMIN).status_code == 200
        stolen = c.cookies.get("vco_session")
        assert c.post("/api/auth/logout").status_code == 200
        c.cookies.set("vco_session", stolen)
        assert c.get("/api/auth/me").status_code == 401


def test_security_headers_on_api_and_static(client):
    for path in ("/api/health", "/"):
        h = client.get(path).headers
        assert "frame-ancestors 'none'" in h["content-security-policy"] and "script-src 'self'" in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "same-origin"


def test_oversized_body_is_refused(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_request_mb", 1)
    big = b"x" * (1024 * 1024 + 10)
    r = client.post("/api/bom/parse", content=big, headers={"content-type": "application/octet-stream"})
    assert r.status_code == 413
    def chunks():                                  # no Content-Length: counted while the route reads the JSON
        yield b'{"hosts": [], "vms": [], "label": "'
        for _ in range(3):
            yield b"y" * (512 * 1024)
        yield b'"}'
    r = client.post("/api/import", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_mcp_ip_ignores_forwarded_for_unless_trusted(monkeypatch):
    from types import SimpleNamespace
    from app import mcp_server
    req = SimpleNamespace(headers={"x-forwarded-for": "6.6.6.6"}, client=SimpleNamespace(host="10.0.0.5"))
    assert mcp_server._client_ip(req) == "10.0.0.5"
    monkeypatch.setattr(get_settings(), "trust_forwarded_for", True)
    assert mcp_server._client_ip(req) == "6.6.6.6"



def test_entitlement_price_written_as_integer_too(client):
    """DAT-10 step 1: the Float price gets an integer twin in minor units."""
    from app.models import LicenseEntitlement
    r = client.post("/api/licensing/entitlements", json={"product": "ws_dc", "metric": "core", "quantity": 16,
                                                         "unitPrice": 1234567.6, "currency": "VND"})
    assert r.status_code == 200, r.text
    ent_id = r.json()["id"]
    db = SessionLocal()
    try:
        row = db.get(LicenseEntitlement, ent_id)
        assert row.unit_price_minor == 1234568
        db.delete(row)
        db.commit()
    finally:
        db.close()
