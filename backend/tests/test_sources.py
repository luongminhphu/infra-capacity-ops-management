"""Settings → Nguồn dữ liệu: vCenter / NetBackup servers managed from the UI."""
import json

import pytest

from app import secrets_box, sources
from app.config import get_settings
from app.database import SessionLocal
from app.models import AuditLog, DataSource

ADMIN = {"username": "admin", "password": "Admin12345!"}
SECRET = "Sup3r-Secret-Pw!"


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


def _vc(**kw):
    return {"kind": "vcenter", "key": "vc-ui-01", "name": "vCenter UI", "host": "vcsa01.corp.example", "port": 443,
            "username": "infra.mon@vsphere.local", "password": SECRET, "verifySsl": False, **kw}


def test_secret_roundtrip_and_wrong_key(monkeypatch):
    token = secrets_box.encrypt(SECRET)
    assert token.startswith("f1:") and SECRET not in token
    assert secrets_box.decrypt(token) == SECRET
    monkeypatch.setattr(get_settings(), "secrets_key", "another-key")
    with pytest.raises(secrets_box.SecretUnreadable):
        secrets_box.decrypt(token)


def test_create_list_update_never_returns_secrets(client):
    r = client.post("/api/admin/sources", json=_vc())
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["hasPassword"] and SECRET not in r.text
    listed = client.get("/api/admin/sources")
    assert SECRET not in listed.text and listed.json()["secretsKeyConfigured"] is False
    # Stored encrypted.
    db = SessionLocal()
    row = db.get(DataSource, created["id"])
    assert row.password_enc and SECRET not in row.password_enc
    db.close()
    # Empty password on edit keeps the saved one; the collector sees the real password.
    r = client.put(f"/api/admin/sources/{created['id']}", json={**_vc(), "password": "", "name": "VC renamed"})
    assert r.status_code == 200 and r.json()["name"] == "VC renamed"
    cfg = next(c for c in get_settings().vcenters() if c.key == "vc-ui-01")
    assert cfg.password == SECRET and cfg.origin == "ui"
    # Key is permanent (history is stored under it).
    client.put(f"/api/admin/sources/{created['id']}", json={**_vc(), "key": "other"})
    assert any(c.key == "vc-ui-01" for c in get_settings().vcenters())
    # Audit trail without the secret.
    db = SessionLocal()
    audit = [a.detail for a in db.query(AuditLog).filter(AuditLog.action.like("source.%")).all()]
    db.close()
    assert audit and not any(SECRET in a for a in audit)


def test_disabled_and_deleted_sources_are_not_polled(client):
    created = client.post("/api/admin/sources", json=_vc(demo=True, password="")).json()
    assert any(v["key"] == "vc-ui-01" for v in client.get("/api/vcenters").json())
    client.put(f"/api/admin/sources/{created['id']}", json={**_vc(demo=True, password=""), "enabled": False})
    assert not any(c.key == "vc-ui-01" for c in get_settings().vcenters())
    client.put(f"/api/admin/sources/{created['id']}", json={**_vc(demo=True, password=""), "enabled": True})
    assert client.post("/api/vcenters/vc-ui-01/sync").json()["vmCount"] > 0      # picked up without restart
    client.delete(f"/api/admin/sources/{created['id']}")
    assert not any(c.key == "vc-ui-01" for c in get_settings().vcenters())


@pytest.mark.parametrize("payload,msg", [
    ({"host": "https://vcsa01/sdk"}, "https://"),
    ({"host": "bad host"}, "https://"),
    ({"port": 70000}, "1–65535"),
    ({"key": "Bad Key"}, "Mã nguồn"),
    ({"password": ""}, "mật khẩu"),
    ({"caPem": "-----BEGIN PRIVATE KEY-----"}, "private key"),
    ({"kind": "netbackup", "password": "", "username": ""}, "API key"),
])
def test_validation(client, payload, msg):
    r = client.post("/api/admin/sources", json=_vc(**payload))
    assert r.status_code == 400 and msg in r.json()["detail"]


def test_netbackup_source_with_api_key(client):
    body = {"kind": "netbackup", "key": "nbu-ui", "name": "Backup UI", "host": "nbu01.corp.example",
            "apiKey": "A1B2C3D4E5F6", "apiVersion": "12.0", "domainType": "unixpwd"}
    r = client.post("/api/admin/sources", json=body)
    assert r.status_code == 200 and r.json()["hasApiKey"] and "A1B2C3D4E5F6" not in r.text
    cfg = next(c for c in get_settings().netbackups() if c.key == "nbu-ui")
    assert cfg.api_key == "A1B2C3D4E5F6" and cfg.api_version == "12.0" and cfg.port == 1556
    assert client.post("/api/admin/sources", json=body).status_code == 409


def test_connection_test_reports_failures_without_saving(client):
    r = client.post("/api/admin/sources/test", json=_vc(host="127.0.0.1", port=9))
    assert r.status_code == 200 and r.json()["ok"] is False and "Không kết nối được" in r.json()["message"]
    assert client.post("/api/admin/sources/test", json=_vc(demo=True)).json()["ok"] is True
    assert client.get("/api/admin/sources").json()["items"] == [i for i in client.get("/api/admin/sources").json()["items"]
                                                               if i["origin"] == "env"]


def test_ui_source_replaces_env_source_and_import(client):
    env_key = get_settings().env_netbackups()[0].key            # conftest: NETBACKUP_1 demo
    assert client.post("/api/admin/sources/import-env").json()["added"] == [f"netbackup:{env_key}"]
    items = client.get("/api/admin/sources").json()["items"]
    assert any(i["origin"] == "env" and i["key"] == env_key and i["replacedByUi"] for i in items)
    ui = next(i for i in items if i["origin"] == "ui" and i["key"] == env_key)
    nbus = [c for c in get_settings().netbackups() if c.key == env_key]
    assert len(nbus) == 1 and nbus[0].origin == "ui" and nbus[0].host == get_settings().env_netbackups()[0].host
    # Second import skips existing keys.
    assert client.post("/api/admin/sources/import-env").json()["added"] == []
    client.put(f"/api/admin/sources/{ui['id']}", json={**ui, "enabled": False})
    assert not [c for c in get_settings().netbackups() if c.key == env_key]      # UI decides, even when disabled


def test_unreadable_secret_is_flagged(client, monkeypatch):
    created = client.post("/api/admin/sources", json=_vc()).json()
    monkeypatch.setattr(get_settings(), "secrets_key", "rotated")
    sources.invalidate_cache()
    item = next(i for i in client.get("/api/admin/sources").json()["items"] if i["id"] == created["id"])
    assert item["secretsReadable"] is False
    assert next(c for c in get_settings().vcenters() if c.key == "vc-ui-01").password == ""


def test_only_admins(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_users", "someone-else")
    assert client.get("/api/admin/sources").status_code == 403
    assert client.post("/api/admin/sources", json=_vc()).status_code == 403
    assert client.post("/api/admin/sources/test", json=_vc()).status_code == 403
    assert json.dumps(client.get("/api/vcenters").json()).count(SECRET) == 0
