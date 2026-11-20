"""ICO v2 G4a: Dell OpenManage Enterprise source — parser (fixtures), status mapping, read-only client (session
only), demo sync, CI builder join by service tag, Settings sources API, orphans."""
import datetime as dt
import json
import pathlib

import pytest

from app.database import SessionLocal
from app.hardware import HardwareConfig, HardwareError, collector, ome
from app.hardware.models import HwComponentHealth, HwDeviceState, HwSourceState
from app.models import DataSource
from app.storage import http
from test_spend_opex import _login, admin, app_, editor  # noqa: F401  (fixtures)

FIX = pathlib.Path(__file__).parent / "fixtures"


def _fix(name):
    return json.loads((FIX / name).read_text())


# ---------------------------------------------------------------- parsing
@pytest.mark.parametrize("code,level,reason", [
    (1000, "ok", None), ("1000", "ok", None), (3000, "bad", None), (4000, "crit", None),
    (2000, "warn", "không có dữ liệu"), (5000, "warn", "không có dữ liệu"), (None, "warn", "không có dữ liệu"),
    ("abc", "warn", "không có dữ liệu"), (1234, "warn", "không có dữ liệu")])
def test_status_mapping(code, level, reason):
    assert ome.status_of(code) == (level, reason)


def test_parse_devices_fixture():
    raw = _fix("ome_devices_page1.json")["value"] + _fix("ome_devices_page2.json")["value"]
    devs = [d for d in (ome.parse_device(r) for r in raw) if d]
    assert [d["deviceId"] for d in devs] == [10074, 10075, 10091]           # the MX7000 chassis (Type 2000) is left out
    a, b, bare = devs
    assert a["serviceTag"] == "7FHKZ53" and a["model"] == "PowerEdge R750" and a["idracIp"] == "10.30.10.11"   # IPv4 first
    assert a["powerState"] == "on" and a["status"] == "ok" and a["connection"] is True
    assert a["lastInventoryAt"] == "2026-11-17T01:00:12"
    assert b["serviceTag"] == "9QWXY63" and b["status"] == "crit" and b["powerState"] == "off"   # "9qwxy63 " normalised
    assert b["lastInventoryAt"] == "2026-11-16T23:30:00"
    assert bare["status"] == "warn" and bare["statusReason"] == "không có dữ liệu" and bare["statusCode"] == 5000
    assert bare["powerState"] == "unknown" and bare["connection"] is False and bare["lastInventoryAt"] is None


def test_parse_subsystem_health_fixture():
    subs = ome.parse_subsystems(_fix("ome_subsystem_health.json"))
    assert [(s["subsystem"], s["status"]) for s in subs] == [("PowerSupply", "crit"), ("Fan", "ok"), ("Battery", "warn")]
    assert subs[0]["detail"] == "PSU.Slot.1: Power supply 1 is not receiving input power."
    assert subs[2]["detail"] == "không có dữ liệu" and subs[1]["detail"] is None


# ---------------------------------------------------------------- read-only client
CFG = HardwareConfig("ome1", "OME 1", "ome.corp.local", username="ico_ro", password="S3cret-OME!")


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/AlertService/Actions/AlertService.Acknowledge"), ("POST", "/api/JobService/Jobs"),
    ("POST", "/api/UpdateService/Actions/UpdateService.UpdateFirmware"),
    ("POST", "/api/DeviceService/Actions/DeviceService.PowerAction"), ("POST", "/api/SessionService/Sessions/extra"),
    ("PUT", "/api/DeviceService/Devices(10074)"), ("PATCH", "/api/SessionService/Sessions('ab12')"),
    ("DELETE", "/api/DeviceService/Devices(10074)"), ("DELETE", "/api/SessionService/Sessions('ab12');x"),
    ("DELETE", "/api/SessionService/Sessions"), ("GETX", "/api/DeviceService/Devices")])
def test_client_refuses_every_write_but_the_session(method, path):
    c = ome.OmeClient(CFG)
    with pytest.raises(HardwareError, match="chỉ đọc"):
        c.http.request(method, path)


def test_session_calls_are_the_only_writes_allowed():
    assert ome.allow("POST", "/api/SessionService/Sessions")
    assert ome.allow("DELETE", "/api/SessionService/Sessions('5b6c1f0e-7a2d-4c11-9b22-0f1e2d3c4b5a')")
    assert not ome.allow("PUT", "/api/SessionService/Sessions")


def test_collect_session_pagination_and_health(monkeypatch):
    calls = []
    p1, p2, health = _fix("ome_devices_page1.json"), _fix("ome_devices_page2.json"), _fix("ome_subsystem_health.json")

    def fake(self, method, path, params=None, headers=None, body=None):
        calls.append((method, path, body, dict(self.headers)))
        if method == "POST":
            return {"Id": "5b6c1f0e-7a2d-4c11-9b22-0f1e2d3c4b5a"}, {"x-auth-token": "tok-77"}
        if method == "DELETE":
            return {}, {}
        if path == "/api/DeviceService/Devices?$top=200&$skip=0":
            return p1, {}
        if path == "/api/DeviceService/Devices?$skip=2&$top=2":
            return p2, {}
        if path.endswith("/SubSystemHealth"):
            return health, {}
        raise AssertionError(path)
    monkeypatch.setattr(http.JsonClient, "request", fake)
    out = ome.collect(CFG)
    assert [d["deviceId"] for d in out["devices"]] == [10074, 10075, 10091]
    writes = [(m, p) for m, p, *_ in calls if m != "GET"]
    assert writes == [("POST", "/api/SessionService/Sessions"),
                      ("DELETE", "/api/SessionService/Sessions('5b6c1f0e-7a2d-4c11-9b22-0f1e2d3c4b5a')")]
    assert calls[0][2] == {"UserName": "ico_ro", "Password": "S3cret-OME!", "SessionType": "API"}
    gets = [c for c in calls if c[0] == "GET"]
    assert all(c[3].get("X-Auth-Token") == "tok-77" for c in gets)
    assert all("S3cret-OME!" not in json.dumps(c[3]) for c in gets)         # the password only in the login body
    # health only for devices that are not normal (10075 critical, 10091 no status)
    assert sorted(p for m, p, *_ in gets if p.endswith("SubSystemHealth")) == [
        "/api/DeviceService/Devices(10075)/SubSystemHealth", "/api/DeviceService/Devices(10091)/SubSystemHealth"]
    assert {h["deviceId"] for h in out["health"]} == {10075, 10091}


def test_next_link_to_another_host_is_refused(monkeypatch):
    def fake(self, method, path, params=None, headers=None, body=None):
        if method == "POST":
            return {"Id": "abc"}, {"x-auth-token": "t"}
        if method == "DELETE":
            return {}, {}
        return {"value": [], "@odata.nextLink": "https://evil.example/api/DeviceService/Devices?$skip=2"}, {}
    monkeypatch.setattr(http.JsonClient, "request", fake)
    with pytest.raises(HardwareError, match="máy chủ khác"):
        ome.collect(CFG)


def test_client_needs_credentials():
    with pytest.raises(HardwareError, match="Viewer"):
        ome.OmeClient(HardwareConfig("o", "O", "ome.local"))


# ---------------------------------------------------------------- store / errors
@pytest.fixture()
def db(app_):
    s = SessionLocal()

    def wipe():
        for m in (HwComponentHealth, HwDeviceState, HwSourceState):
            s.query(m).delete()
        s.query(DataSource).filter(DataSource.kind.in_(("ome", "vcenter"))).delete(synchronize_session=False)
        s.commit()
        from app import sources
        sources.invalidate_cache()
    wipe()
    yield s
    s.rollback()
    wipe()
    s.close()


def test_store_replaces_rows_and_error_keeps_them(db, monkeypatch):
    cfg = HardwareConfig("ome-x", "OME X", "ome.local", username="u", password="p")
    data = {"devices": [ome.parse_device(r) for r in _fix("ome_devices_page1.json")["value"]],
            "health": [{**s, "deviceId": 10075} for s in ome.parse_subsystems(_fix("ome_subsystem_health.json"))]}
    monkeypatch.setattr(collector, "read", lambda c, plan=None: data)
    assert collector.sync_hardware(db, cfg) == {"key": "ome-x", "ok": True, "devices": 2, "health": 3}
    assert collector.sync_hardware(db, cfg)["ok"]                           # replaced, not duplicated
    assert db.query(HwDeviceState).filter_by(source_key="ome-x").count() == 2
    assert db.query(HwComponentHealth).filter_by(source_key="ome-x").count() == 3
    psu = db.query(HwComponentHealth).filter_by(source_key="ome-x", subsystem="PowerSupply").one()
    assert psu.status == "crit" and "PSU.Slot.1" in psu.detail

    def boom(c, plan=None):
        raise HardwareError("OME từ chối xác thực (401)")
    monkeypatch.setattr(collector, "read", boom)
    with pytest.raises(HardwareError):
        collector.sync_hardware(db, cfg)
    st = db.query(HwSourceState).filter_by(source_key="ome-x").one()
    assert st.last_error == "OME từ chối xác thực (401)" and st.last_ok_at is not None
    assert db.query(HwDeviceState).filter_by(source_key="ome-x").count() == 2   # previous state kept


# ---------------------------------------------------------------- Settings → Nguồn dữ liệu
def test_sources_api_ome(db, admin, editor):
    r = admin.post("/api/admin/sources", json={"kind": "ome", "key": "ome-hcm", "name": "OME HCM", "host": "ome01.corp.local",
                                         "username": "ico_ro"})
    assert r.status_code == 400 and "Viewer" in r.json()["detail"]
    r = admin.post("/api/admin/sources", json={"kind": "ome", "key": "ome-hcm", "name": "OME HCM", "host": "ome01.corp.local",
                                         "username": "ico_ro", "password": "OME-pass-secret"})
    assert r.status_code == 200, r.text
    assert r.json()["hasPassword"] is True and r.json()["port"] == 443 and "OME-pass-secret" not in r.text
    assert "OME-pass-secret" not in admin.get("/api/admin/sources").text
    assert editor.post("/api/admin/sources", json={"kind": "ome", "key": "x", "name": "x", "host": "x", "demo": True}).status_code == 403
    t = admin.post("/api/admin/sources/test", json={"kind": "ome", "name": "d", "host": "", "demo": True})
    assert t.json()["ok"] is True and "DEMO" in t.json()["message"]
    from app import sources
    from app.config import get_settings
    sources.invalidate_cache()
    cfg = {c.key: c for c in get_settings().hardware()}["ome-hcm"]
    assert isinstance(cfg, HardwareConfig) and cfg.password == "OME-pass-secret" and cfg.timeout == 60


def test_connection_test_logs_in_reads_one_page_logs_out(monkeypatch):
    calls = []

    def fake(self, method, path, params=None, headers=None, body=None):
        calls.append((method, path))
        if method == "POST":
            return {"Id": "s1"}, {"x-auth-token": "t"}
        if method == "DELETE":
            return {}, {}
        return {"@odata.count": 42, "value": [{}]}, {}
    monkeypatch.setattr(http.JsonClient, "request", fake)
    msg = ome.test(HardwareConfig("o", "O", "ome.local", username="u", password="p"))
    assert "42 thiết bị" in msg
    assert calls == [("POST", "/api/SessionService/Sessions"), ("GET", "/api/DeviceService/Devices?$top=1&$skip=0"),
                     ("DELETE", "/api/SessionService/Sessions('s1')")]


# ---------------------------------------------------------------- demo end to end + CI registry
def test_demo_sync_joins_every_dell_host_and_lists_bare_metal(db, admin):
    from app import sources
    from app.cmdb import builder
    from app.cmdb.models import Ci, CiKey, CiLink
    from app.collectors import scheduler
    from app.config import get_settings
    assert admin.post("/api/admin/sources", json={"kind": "vcenter", "key": "vcd", "name": "VC demo", "host": "", "demo": True}).status_code == 200
    assert admin.post("/api/admin/sources", json={"kind": "ome", "key": "ome-demo", "name": "OME demo", "host": "", "demo": True}).status_code == 200
    sources.invalidate_cache()
    s = get_settings()
    scheduler.sync_vcenter(db, s.vcenters()[0], s)
    res = scheduler.sync_hardware_sources(s)
    assert res == [{"key": "ome-demo", "ok": True, "devices": 12, "health": 5,   # 10 Dell hosts + 2 bare-metal
                    "inventory": 12, "warranties": 12, "alerts": 2}]
    devs = db.query(HwDeviceState).filter_by(source_key="ome-demo").all()
    assert sorted(d.status for d in devs).count("ok") == 10
    assert {d.status for d in devs} == {"ok", "bad", "crit"}
    subs = {(h.subsystem, h.status) for h in db.query(HwComponentHealth).filter_by(source_key="ome-demo")}
    assert ("PowerSupply", "crit") in subs and ("Fan", "bad") in subs

    builder.build_all(db)
    hosts = db.query(Ci).filter_by(type="hypervisor_host", status="active").all()
    dell = [h for h in hosts if json.loads(h.attrs).get("vendor", "").startswith("Dell") and json.loads(h.attrs).get("vcenterKey") == "vcd"]
    assert len(dell) == 10
    for h in dell:                                    # 100 % of the Dell VMware hosts land on an OME server
        link = db.query(CiLink).filter_by(from_ci=h.id, relation="runs_on").one()
        phys = db.get(Ci, link.to_ci)
        a = json.loads(phys.attrs)
        assert phys.type == "physical_server" and "ome" in a["origins"] and "host" in a["origins"]
        assert a["omeSource"] == "ome-demo" and a["idracIp"].startswith("10.30.")
    bare = [c for c in db.query(Ci).filter_by(type="physical_server") if json.loads(c.attrs).get("origins") == ["ome"]]
    assert sorted(json.loads(c.attrs)["omeName"] for c in bare) == ["backup-media-01", "db-oracle-01"]
    assert all(c.name == c.source_key for c in bare)                 # the CI name stays the service tag
    assert all(db.query(CiKey).filter_by(ci_id=c.id, kind="idrac_ip").count() == 1 for c in bare)
    assert db.query(CiLink).filter(CiLink.to_ci.in_([c.id for c in bare])).count() == 0   # no host on top: bare-metal


def test_orphans_list_hardware_rows_of_a_removed_source(db, admin):
    from app import orphans
    db.add(HwSourceState(source_key="ome-old", kind="ome", name="Old", captured_at=dt.datetime(2026, 1, 1)))
    db.add(HwDeviceState(source_key="ome-old", device_id=1, name="x", status="ok", power_state="on"))
    db.commit()
    hit = [o for o in orphans.scan(db) if o["family"] == "hardware" and o["key"] == "ome-old"]
    assert hit and hit[0]["rows"] == 2 and hit[0]["familyLabel"] == "Phần cứng (Dell OME)"
    assert orphans.purge(db, "hardware", "ome-old") == {"Máy chủ": 1, "Trạng thái nguồn": 1}
