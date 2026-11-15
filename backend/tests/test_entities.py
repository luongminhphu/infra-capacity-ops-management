"""Super app SA3: global search and entity pages (host / VM), filtered by role and feature."""
import pytest

from test_spend_opex import _login, _opex, admin, app_, editor  # noqa: F401  (fixtures)

HOSTS = [{"host": "esx01.corp.local", "cluster": "PRD", "cores": 32, "ramGB": 512, "vcenterKey": "vc1", "vendor": "Dell Inc.",
          "model": "PowerEdge R750", "esxiVersion": "8.0.2", "cpuSockets": 2},
         {"host": "esx02.corp.local", "cluster": "PRD", "cores": 32, "ramGB": 512, "vcenterKey": "vc1"}]
VMS = [{"vm": "sql-prod-01", "cluster": "PRD", "host": "esx01.corp.local", "vcpu": 16, "ramGB": 128, "isPoweredOn": True,
        "ipAddress": "10.1.2.3", "vcenterKey": "vc1", "guestFullName": "Microsoft Windows Server 2022 (64-bit)", "guestId": "windows2019srvNext_64Guest"},
       {"vm": "old-app", "cluster": "PRD", "host": "esx01.corp.local", "vcpu": 2, "ramGB": 4, "isPoweredOn": False, "powerOffDays": 120,
        "vcenterKey": "vc1"}]


@pytest.fixture(autouse=True)
def _data(app_, monkeypatch):
    from app import data_access
    from app.services import entities
    entities._cache.clear()
    monkeypatch.setattr(data_access, "get_latest_combined", lambda db, keys=None, include_import=True: (HOSTS, VMS, []))


def test_search_hosts_vms_and_spend_codes_by_role(app_, editor):
    o = editor.post("/api/spend/opex", json=_opex(title="Bảo hành esx01 Dell R750",
                                                  assets=[{"serial": "SN-1", "vcenterKey": "vc1", "hostName": "esx01.corp.local"}])).json()
    viewer, nobody = _login(app_, "viewer1"), _login(app_, "nobody1")
    r = viewer.get("/api/search?q=esx01").json()["items"]
    types = [i["type"] for i in r]
    assert "host" in types and "opex" in types
    h = next(i for i in r if i["type"] == "host")
    assert h["entity"] == {"type": "host", "name": "esx01.corp.local", "vcenterKey": "vc1"}
    op = next(i for i in r if i["type"] == "opex")
    assert op["page"] == "spend-opex" and op["query"] == f"code={o['code']}"
    assert [i["type"] for i in viewer.get("/api/search?q=10.1.2").json()["items"]] == ["vm"]
    # No spend role: no business codes.
    assert all(i["type"] in ("host", "vm", "client") for i in nobody.get("/api/search?q=esx01").json()["items"])
    assert nobody.get(f"/api/search?q={o['code']}").json()["items"] == []
    assert viewer.get("/api/search?q=a").status_code == 422
    # Wildcards are literal.
    assert viewer.get("/api/search?q=%25%25").json()["items"] == []


def test_host_card_gathers_modules(app_, editor):
    editor.post("/api/spend/opex", json=_opex(assets=[{"serial": "SN-R750-01", "vcenterKey": "vc1", "hostName": "ESX01.corp.local"}]))
    viewer, nobody = _login(app_, "viewer1"), _login(app_, "nobody1")
    d = viewer.get("/api/entities/host?name=esx01.corp.local&vcenter=vc1").json()
    assert d["hardware"]["model"] == "PowerEdge R750" and d["capacity"]["vcpuUsed"] == 16 and d["capacity"]["vmCount"] == 1
    assert [v["name"] for v in d["vms"]] == ["sql-prod-01", "old-app"] and d["clusterStatus"]["hosts"] == 2
    assert d["devices"][0]["serial"] == "SN-R750-01"
    assert nobody.get("/api/entities/host?name=esx01.corp.local").json()["devices"] is None     # no spend role
    assert viewer.get("/api/entities/host?name=nope").status_code == 404


def test_vm_card_os_and_findings(app_):
    viewer = _login(app_, "viewer1")
    d = viewer.get("/api/entities/vm?name=sql-prod-01").json()
    assert d["host"] == "esx01.corp.local" and d["config"]["ipAddress"] == "10.1.2.3"
    assert d["os"]["category"] and d["os"]["label"]
    off = viewer.get("/api/entities/vm?name=old-app").json()
    assert any(f["typeLabel"] for f in off["findings"])                     # powered off for 120 days → finding
    assert viewer.get("/api/entities/vm?name=missing").status_code == 404


def test_license_off_hides_os_and_esxi(app_, admin):
    viewer = _login(app_, "viewer1")
    admin.put("/api/settings/features", json={"features": {"license": False}})
    try:
        assert viewer.get("/api/entities/vm?name=sql-prod-01").json()["os"] is None
        assert "esxiVersion" not in viewer.get("/api/entities/host?name=esx01.corp.local").json()["hardware"]
    finally:
        admin.put("/api/settings/features", json={"features": {"license": True}})


def test_devices_like_is_escaped(app_, editor):
    editor.post("/api/spend/opex", json=_opex(assets=[{"serial": "SN-X", "hostName": "esxA1.corp.local"}]))
    from app.services.entities import _devices_for_host
    from app.database import SessionLocal
    db = SessionLocal()
    assert _devices_for_host(db, "esx_1") == [] and _devices_for_host(db, "esx%") == []
    assert [d["serial"] for d in _devices_for_host(db, "esxa1")] == ["SN-X"]
    db.close()


def test_search_backup_clients_recent_window_only(app_):
    """QRY-02: clients come from the last CLIENT_WINDOW_DAYS of jobs (cached), not the whole table."""
    import datetime as dt
    from app.database import SessionLocal
    from app.models import BackupJob
    from app.services import entities
    now = dt.datetime.utcnow()
    with SessionLocal() as db:
        db.add_all([BackupJob(nbu_key="nbu-qry02", job_id=9100001, client_name="ZqRecent-Client01", start_time=now - dt.timedelta(days=3)),
                    BackupJob(nbu_key="nbu-qry02", job_id=9100002, client_name="ZqStale-Client02", start_time=now - dt.timedelta(days=200)),
                    BackupJob(nbu_key="nbu-qry02", job_id=9100003, client_name="sql-prod-01", start_time=now)])
        db.commit()
    try:
        entities._cache.clear()
        viewer = _login(app_, "viewer1")
        items = viewer.get("/api/search?q=zq").json()["items"]
        assert [(i["type"], i["label"]) for i in items] == [("client", "ZqRecent-Client01")]
        # A client that is also a VM shows once, as the VM.
        assert [i["type"] for i in viewer.get("/api/search?q=sql-prod").json()["items"]] == ["vm"]
    finally:
        with SessionLocal() as db:
            db.query(BackupJob).filter(BackupJob.nbu_key == "nbu-qry02").delete()
            db.commit()
