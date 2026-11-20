"""GestióIP IPAM source: parsers (JSON fixtures), read-only guard on request_type (GestióIP also writes on GET), paging,
Basic auth, Settings sources API, demo sync + status family, orphans."""
import base64
import json
import pathlib

import pytest

from app.database import SessionLocal
from app.ipam import IpamConfig, IpamError, collector, gestioip
from app.ipam.models import IpamHost, IpamNetwork, IpamSourceState, IpamVlan
from app.models import DataSource
from app.storage import http
from test_spend_opex import _login, admin, app_, editor  # noqa: F401  (fixtures)

FIX = pathlib.Path(__file__).parent / "fixtures"
CFG = IpamConfig("gip", "GestióIP", "ipam.corp.local", username="ico_ro", password="Ipam-S3cret!", serial_column="SN", mac_column="MAC")


def _fix(name):
    return json.loads((FIX / name).read_text())


# ---------------------------------------------------------------- parsers
def test_parse_hosts_fixture():
    rows = gestioip._rows(_fix("gestioip_list_hosts.json"), "Host")
    hosts = [h for h in (gestioip.parse_host(r, "SN", "MAC") for r in rows) if h]
    assert [h["ip"] for h in hosts] == ["10.20.3.4", "10.30.10.11", "fe80::1"]          # "not-an-ip" dropped
    app, idrac, v6 = hosts
    assert (app["hostname"], app["site"], app["category"], app["alive"]) == ("app-01", "DC1", "server", "up")
    assert app["serial"] is None and app["mac"] == "00:50:56:aa:bb:01" and app["custom"]["OS"] == "RHEL 9"
    assert idrac["serial"] == "7FHKZ53" and idrac["mac"] == "b0:7b:25:aa:bb:01" and idrac["comment"] == "R750"
    assert v6["ipVersion"] == "v6" and v6["custom"] == {}
    assert gestioip.parse_host({"IP": "10.0.0.1"}, "sn", "")["serial"] is None              # column absent: no serial


def test_single_element_list_and_networks():
    one = {"listHostsResult": {"error": "", "HostList": {"Host": {"id": "1", "IP": "10.0.0.5", "hostname": "solo"}}}}
    assert [h["hostname"] for h in (gestioip.parse_host(r) for r in gestioip._rows(one, "Host"))] == ["solo"]
    nets = [n for n in (gestioip.parse_network(r) for r in gestioip._rows(_fix("gestioip_list_networks.json"), "Network")) if n]
    assert [(n["cidr"], n["vlan"]) for n in nets] == [("10.20.0.0/16", "100"), ("10.30.0.0/16", "30"), ("10.30.10.0/24", None)]
    assert gestioip.network_of("10.30.10.11", nets) == "10.30.10.0/24"                      # longest prefix
    assert gestioip.network_of("10.30.99.1", nets) == "10.30.0.0/16"
    assert gestioip.network_of("192.168.1.1", nets) is None


def test_parse_vlans_csv():
    assert gestioip.parse_vlan("3,100,server1,Máy chủ,,white,black") == {"number": 100, "name": "server1", "descr": "Máy chủ"}
    assert gestioip.parse_vlan("bad") is None and gestioip.parse_vlan("1,x,name") is None


def test_api_error_field_raises(monkeypatch):
    monkeypatch.setattr(http.JsonClient, "get", lambda self, path, params=None: {"listHostsResult": {"error": "client not found"}})
    with pytest.raises(IpamError, match="client not found"):
        gestioip.GestioIpClient(CFG).call("listHosts")


# ---------------------------------------------------------------- read-only
@pytest.mark.parametrize("rt", ["createHost", "updateHost", "deleteHost", "createNetwork", "deleteNetwork",
                                "reserveFirstFreeNetworkAddress", "firstFreeNetwork", "createVlan", "deleteVlan",
                                "createClient", "deleteClient", "discoverNetwork", "help", "readHostHelp"])
def test_every_non_read_request_type_is_refused_before_sending(monkeypatch, rt):
    sent = []
    monkeypatch.setattr(http.JsonClient, "request", lambda self, *a, **k: sent.append(a) or ({}, {}))
    with pytest.raises(IpamError, match="chỉ đọc"):
        gestioip.GestioIpClient(CFG).call(rt)
    assert sent == []


def test_collect_reads_with_get_basic_auth_and_pages(monkeypatch):
    calls = []
    hosts = gestioip._rows(_fix("gestioip_list_hosts.json"), "Host")

    def fake(self, method, path, params=None, headers=None, body=None):
        calls.append((method, path, dict(params or {}), dict(self.headers)))
        rt = params["request_type"]
        if rt == "listNetworks":
            return _fix("gestioip_list_networks.json"), {}
        if rt == "listHosts":
            page = params["page"]
            return {"listHostsResult": {"error": "", "HostList": {"Host": hosts[page * 2:page * 2 + 2]}}}, {}
        if rt == "listVlans":
            return {"listVlansResult": {"error": "", "VlanList": {"Vlan": ["2,1,Default,default VLAN,,white,black", "3,100,server,,,white,black"]}}}, {}
        raise AssertionError(rt)
    monkeypatch.setattr(http.JsonClient, "request", fake)
    monkeypatch.setattr(gestioip, "PAGE", 2)
    out = gestioip.collect(CFG)
    assert [h["ip"] for h in out["hosts"]] == ["10.20.3.4", "10.30.10.11", "fe80::1"]
    assert {h["ip"]: h["network"] for h in out["hosts"]} == {"10.20.3.4": "10.20.0.0/16", "10.30.10.11": "10.30.10.0/24", "fe80::1": None}
    assert [v["number"] for v in out["vlans"]] == [1, 100]
    assert {m for m, *_ in calls} == {"GET"} and all(p == "/gestioip/api/api.cgi" for _m, p, *_ in calls)
    host_pages = [c[2]["page"] for c in calls if c[2]["request_type"] == "listHosts"]
    assert host_pages == [0, 1, 2]                                 # two full pages of 2, then an empty one ends the read
    first = calls[0][2]
    assert first["output_type"] == "json" and first["client_name"] == "DEFAULT" and first["no_csv"] == "yes"
    token = base64.b64encode(b"ico_ro:Ipam-S3cret!").decode()
    assert all(c[3]["Authorization"] == f"Basic {token}" for c in calls)
    assert all("Ipam-S3cret!" not in json.dumps(c[2]) for c in calls)      # never in the URL


def test_client_needs_credentials():
    with pytest.raises(IpamError, match="tài khoản GestióIP"):
        gestioip.GestioIpClient(IpamConfig("g", "G", "ipam.local"))


# ---------------------------------------------------------------- Settings + demo
@pytest.fixture()
def db(app_):
    s = SessionLocal()

    def wipe():
        for m in (IpamHost, IpamNetwork, IpamVlan, IpamSourceState):
            s.query(m).delete()
        s.query(DataSource).filter(DataSource.kind.in_(("gestioip", "vcenter", "ome"))).delete(synchronize_session=False)
        s.commit()
        from app import sources
        sources.invalidate_cache()
    wipe()
    yield s
    s.rollback()
    wipe()
    s.close()


def test_sources_api_gestioip(db, admin, editor):
    base = {"kind": "gestioip", "key": "gip-hcm", "name": "GestióIP HCM", "host": "ipam.corp.local", "username": "ico_ro"}
    r = admin.post("/api/admin/sources", json=base)
    assert r.status_code == 400 and "GestióIP" in r.json()["detail"]
    r = admin.post("/api/admin/sources", json={**base, "password": "Ipam-S3cret!", "apiPath": "https://evil/x"})
    assert r.status_code == 400 and "Đường dẫn API" in r.json()["detail"]
    r = admin.post("/api/admin/sources", json={**base, "password": "Ipam-S3cret!", "clientName": "HSC", "serialColumn": "SN"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["clientName"] == "HSC" and body["apiPath"] == "/gestioip/api/api.cgi" and body["serialColumn"] == "SN"
    assert "Ipam-S3cret!" not in r.text and "Ipam-S3cret!" not in admin.get("/api/admin/sources").text
    assert editor.post("/api/admin/sources", json={**base, "key": "x", "demo": True}).status_code == 403
    from app import sources
    from app.config import get_settings
    sources.invalidate_cache()
    cfg = {c.key: c for c in get_settings().ipam()}["gip-hcm"]
    assert isinstance(cfg, IpamConfig) and cfg.password == "Ipam-S3cret!" and cfg.client_name == "HSC" and cfg.serial_column == "SN"


def test_demo_sync_status_and_orphans(db, admin):
    from app import orphans, sources
    from app.collectors import scheduler
    from app.config import get_settings
    assert admin.post("/api/admin/sources", json={"kind": "vcenter", "key": "vcd", "name": "VC demo", "host": "", "demo": True}).status_code == 200
    assert admin.post("/api/admin/sources", json={"kind": "ome", "key": "ome-demo", "name": "OME demo", "host": "", "demo": True}).status_code == 200
    gip = admin.post("/api/admin/sources", json={"kind": "gestioip", "key": "gip-demo", "name": "GestióIP demo", "host": "", "demo": True}).json()
    sources.invalidate_cache()
    s = get_settings()
    scheduler.sync_vcenter(db, s.vcenters()[0], s)
    r = admin.post(f"/api/admin/sources/{gip['id']}/sync").json()
    assert r["ok"] is True and r["status"]["family"] == "ipam" and r["status"]["state"] == "ok"
    hosts = db.query(IpamHost).filter_by(source_key="gip-demo").all()
    idrac = [h for h in hosts if h.network == "10.30.0.0/16"]
    assert len(idrac) == 13                                       # 12 OME servers + 1 decommissioned iDRAC entry
    assert sum(1 for h in idrac if h.serial) == 12 and any(h.serial == "WRONG01" for h in idrac)
    assert any(h.hostname == "decom-app-01" and h.alive == "down" for h in hosts)
    assert all(h.network for h in hosts)                          # every demo IP falls in a declared network
    assert db.query(IpamVlan).filter_by(source_key="gip-demo").count() == 3
    st = admin.get("/api/sources/status").json()
    assert any(f["family"] == "ipam" and f["label"] == "IPAM" for f in st["summary"]["families"])
    db.query(DataSource).filter_by(key="gip-demo").delete()
    db.commit()
    sources.invalidate_cache()
    hit = [o for o in orphans.scan(db) if o["family"] == "ipam" and o["key"] == "gip-demo"]
    assert hit and hit[0]["familyLabel"] == "IPAM (GestióIP)"
