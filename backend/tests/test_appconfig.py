"""Settings: module switches and the login-screen logo."""
import base64

import pytest

from app.config import get_settings

ADMIN = {"username": "admin", "password": "Admin12345!"}
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json=ADMIN).status_code == 200
        yield c
        c.put("/api/settings/features", json={"features": {"backup": True, "sizing": True}})
        c.delete("/api/settings/login-logo")


def test_backup_switch_hides_module_api(client):
    feats = client.get("/api/settings/features").json()
    assert feats["features"]["backup"] is True and {f["key"] for f in feats["catalog"]} >= {"backup", "license", "sizing"}
    assert client.get("/api/backup/servers").status_code == 200
    r = client.put("/api/settings/features", json={"features": {"backup": False}})
    assert r.status_code == 200 and r.json()["features"]["backup"] is False
    assert client.get("/api/backup/servers").status_code == 404
    client.put("/api/settings/features", json={"features": {"backup": True}})
    assert client.get("/api/backup/servers").status_code == 200
    assert client.put("/api/settings/features", json={"features": {"nope": False}}).status_code == 400


def test_only_admins_change_settings(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_users", "someone-else")
    assert client.get("/api/settings/features").status_code == 200
    assert client.put("/api/settings/features", json={"features": {"backup": False}}).status_code == 403
    assert client.put("/api/settings/login-logo", json={"data_url": PNG}).status_code == 403


def test_login_logo_is_public_and_separate_from_report_logo(client):
    assert client.get("/api/public/branding").json()["loginLogo"] is None
    assert client.put("/api/settings/login-logo", json={"data_url": PNG, "filename": "logo.png"}).status_code == 200
    client.cookies.clear()
    assert client.get("/api/public/branding").json()["loginLogo"] == PNG      # visible before sign-in
    assert client.post("/api/auth/login", json=ADMIN).status_code == 200
    assert client.get("/api/settings/logo").json()["dataUrl"] != PNG        # PDF cover logo untouched
    assert client.delete("/api/settings/login-logo").status_code == 200
    assert client.get("/api/public/branding").json()["loginLogo"] is None


def test_login_logo_rejects_scripted_svg(client):
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    url = "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()
    assert client.put("/api/settings/login-logo", json={"data_url": url}).status_code == 400
