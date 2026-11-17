"""Storage monitoring: Pure FlashArray (Purity REST 2.x) and NetApp ONTAP REST — parsing, read-only client
behaviour, datastore correlation (NAA / NFS), array levels, Settings sources API, demo end-to-end."""
import datetime as dt
import json

import pytest

from app import infra
from app.database import SessionLocal
from app.models import DataSource, DatastoreState, StorageAlertState, StorageArrayState, StorageVolumeState
from app.storage import StorageConfig, StorageError, collector, http, netapp, pure
from test_spend_opex import _login, admin, app_, editor  # noqa: F401  (fixtures)

GB = 1024 ** 3
NOW = dt.datetime(2026, 11, 17, 3, 0)

# ---------------------------------------------------------------- Purity REST 2.x payloads (FlashArray//X20R3)
PURE_ARRAYS = [{"name": "pure-x20-hcm", "os": "Purity//FA", "version": "6.5.8", "capacity": 40 * 1024 * GB,
                "space": {"data_reduction": 3.42, "total_physical": 30 * 1024 * GB, "total_provisioned": 120 * 1024 * GB}}]
PURE_PERF = [{"usec_per_read_op": 450, "usec_per_write_op": 6200, "reads_per_sec": 12000, "writes_per_sec": 8000,
              "read_bytes_per_sec": 400 * 1024 ** 2, "write_bytes_per_sec": 200 * 1024 ** 2}]
PURE_CTL = [{"name": "CT0", "mode": "primary", "model": "FA-X20R3", "status": "ready", "version": "6.5.8"},
            {"name": "CT1", "mode": "secondary", "model": "FA-X20R3", "status": "ready", "version": "6.5.8"}]
PURE_HW = [{"name": "CH0.PWR1", "type": "power_supply", "status": "ok"}, {"name": "CH0.PWR0", "type": "power_supply", "status": "failed",
                                                                          "details": "Power supply failure"},
           {"name": "CH0.BAY20", "type": "drive_bay", "status": "not_installed"}]
PURE_ALERTS = [{"id": "41", "code": 21, "severity": "critical", "summary": "Power supply failure", "component_name": "CH0.PWR0",
                "created": 1763337600000, "state": "open"},
               {"id": "42", "severity": "hidden", "summary": "x", "state": "open"}]
PURE_VOLS = [{"name": "vmfs-prod-01", "serial": "F4252922ADE248CF000113E8", "provisioned": 10 * 1024 * GB,
              "space": {"virtual": 7 * 1024 * GB, "total_physical": 2 * 1024 * GB}, "destroyed": False},
             {"name": "old", "serial": "AAA", "provisioned": GB, "space": {}, "destroyed": True}]


def test_pure_parse():
    out = pure.parse(PURE_ARRAYS, PURE_PERF, PURE_CTL, PURE_HW, PURE_ALERTS, PURE_VOLS)
    a = out["array"]
    assert (a["model"], a["capacityGB"], a["usedGB"], a["provisionedGB"], a["dataReduction"]) == ("FA-X20R3", 40960.0, 30720.0, 122880.0, 3.42)
    assert (a["latencyReadMs"], a["latencyWriteMs"], a["iops"], a["throughputMBps"]) == (0.45, 6.2, 20000, 600.0)
    assert a["hardwareIssues"] == [{"name": "CH0.PWR0", "type": "power_supply", "status": "failed", "details": "Power supply failure"}]
    assert [x["severity"] for x in out["alerts"]] == ["critical"] and out["alerts"][0]["at"] == "2025-11-17T00:00:00Z"
    v = out["volumes"]
    assert len(v) == 1 and v[0]["naa"] == "naa.624a9370f4252922ade248cf000113e8" and (v[0]["sizeGB"], v[0]["usedGB"]) == (10240.0, 7168.0)
    assert pure.pick_version(["1.17", "2.0", "2.26", "2.4"]) == "2.26"
    with pytest.raises(StorageError):
        pure.pick_version(["1.17", "1.19"])


def test_pure_client_session_and_pagination(monkeypatch):
    calls = []

    def fake(self, method, path, params=None, headers=None):
        calls.append((method, path, dict(params or {}), dict(headers or {}), dict(self.headers)))
        if path == "/api/api_version":
            return {"version": ["1.19", "2.21"]}, {}
        if path.endswith("/login"):
            assert headers == {"api-token": "tok-123"}
            return {}, {"x-auth-token": "sess-9"}
        if path.endswith("/logout"):
            return {}, {}
        if path.endswith("/volumes"):
            if "continuation_token" not in (params or {}):
                return {"items": [PURE_VOLS[0]], "continuation_token": "c1"}, {}
            return {"items": [{**PURE_VOLS[0], "name": "vmfs-prod-02", "serial": "B"}]}, {}
        key = path.rsplit("/", 2)[-2] + "/" + path.rsplit("/", 1)[-1] if path.endswith("/performance") else path.rsplit("/", 1)[-1]
        return {"items": {"arrays": PURE_ARRAYS, "arrays/performance": PURE_PERF, "controllers": PURE_CTL, "hardware": PURE_HW,
                          "alerts": PURE_ALERTS}[key]}, {}
    monkeypatch.setattr(http.JsonClient, "request", fake)
    out = pure.collect(StorageConfig("p1", "Pure 1", "pure", "fa.local", api_token="tok-123"))
    assert [v["name"] for v in out["volumes"]] == ["vmfs-prod-01", "vmfs-prod-02"]
    methods = [(m, p) for m, p, *_ in calls if m != "GET"]
    assert methods == [("POST", "/api/2.21/login"), ("POST", "/api/2.21/logout")]               # the only POSTs
    gets = [c for c in calls if c[0] == "GET" and c[1] != "/api/api_version"]
    assert all(c[4].get("x-auth-token") == "sess-9" for c in gets)
    assert next(c for c in gets if c[1].endswith("/alerts"))[2]["filter"] == "state='open'"
    assert all("tok-123" not in json.dumps(c[4]) for c in gets)                                  # token only on /login


def test_json_client_refuses_writes():
    c = http.JsonClient(StorageConfig("n1", "N", "netapp", "ontap.local"), "https://ontap.local:443")
    for method, path in (("POST", "/api/storage/volumes"), ("DELETE", "/api/storage/volumes/1"), ("PATCH", "/api/cluster")):
        with pytest.raises(StorageError, match="chỉ đọc"):
            c.request(method, path)


# ---------------------------------------------------------------- ONTAP REST payloads (AFF A300)
ONTAP = dict(
    cluster={"name": "ntap-a300", "version": {"full": "NetApp Release 9.11.1P13"},
             "metric": {"latency": {"read": 850, "write": 420}, "iops": {"total": 15400}, "throughput": {"total": 300 * 1024 ** 2}}},
    nodes=[{"name": "ntap-01", "model": "AFF-A300", "state": "up", "controller": {"failed_fan": {"count": 0}, "failed_power_supply": {"count": 1, "message": {"message": "PSU2 failed"}}}},
           {"name": "ntap-02", "model": "AFF-A300", "state": "up", "controller": {"failed_fan": {"count": 0}, "over_temperature": "normal"}}],
    aggregates=[{"name": "aggr1", "state": "online", "space": {"block_storage": {"size": 30 * 1024 * GB, "used": 18 * 1024 * GB}, "efficiency": {"ratio": 1.5}}},
                {"name": "aggr2", "state": "online", "space": {"block_storage": {"size": 30 * 1024 * GB, "used": 6 * 1024 * GB}, "efficiency": {"ratio": 3.0}}}],
    volumes=[{"name": "nfs_ds01", "svm": {"name": "svm_vmware"}, "state": "online", "nas": {"path": "/nfs_ds01"},
              "space": {"size": 4 * 1024 * GB, "used": 3 * 1024 * GB}}],
    luns=[{"name": "/vol/vmfs_vol/lun0", "svm": {"name": "svm_vmware"}, "serial_number": "wOj4o$JYDyrL", "space": {"size": 2 * 1024 * GB, "used": 1024 * GB}}],
    lifs=[{"ip": {"address": "10.20.0.11"}, "svm": {"name": "svm_vmware"}}],
    disks=[{"name": "1.0.7", "state": "broken", "node": {"name": "ntap-02"}}],
    events=[{"index": 901, "time": "2026-11-17T01:00:00+07:00", "message": {"name": "monitor.chassisPowerSupply.degraded", "severity": "alert"},
             "log_message": "Power supply 2 degraded", "node": {"name": "ntap-01"}},
            {"index": 902, "time": "2026-11-17T02:00:00+07:00", "message": {"name": "x", "severity": "notice"}}],
)


def test_ontap_parse():
    o = ONTAP
    out = netapp.parse(o["cluster"], o["nodes"], o["aggregates"], o["volumes"], o["luns"], o["lifs"], o["disks"], o["events"])
    a = out["array"]
    assert (a["model"], a["capacityGB"], a["usedGB"]) == ("AFF-A300", 61440.0, 24576.0)
    assert a["dataReduction"] == 1.88                       # (18 × 1,5 + 6 × 3,0) / 24, weighted by used
    assert (a["latencyReadMs"], a["latencyWriteMs"], a["iops"], a["throughputMBps"]) == (0.85, 0.42, 15400, 300.0)
    assert [(i["type"], i["status"]) for i in a["hardwareIssues"]] == [("failed_power_supply", "failed"), ("disk", "broken")]
    assert [x["severity"] for x in out["alerts"]] == ["critical"]                 # "alert" → critical, notice dropped
    lun = next(v for v in out["volumes"] if v["kind"] == "lun")
    assert lun["naa"] == "naa.600a0980" + "wOj4o$JYDyrL".encode().hex()
    assert next(v for v in out["volumes"] if v["kind"] == "volume")["nfsPath"] == "/nfs_ds01"
    assert out["nfsEndpoints"] == [{"svm": "svm_vmware", "ip": "10.20.0.11"}]


def test_ontap_client_get_only_with_pagination(monkeypatch):
    calls = []

    def fake(self, method, path, params=None, headers=None):
        calls.append((method, path, self.headers.get("Authorization")))
        if path.startswith("/api/storage/volumes") and "page=2" not in path:
            return {"records": ONTAP["volumes"], "_links": {"next": {"href": "/api/storage/volumes?page=2"}}}, {}
        if path.startswith("/api/storage/volumes"):
            return {"records": [{**ONTAP["volumes"][0], "name": "nfs_ds02", "nas": {"path": "/nfs_ds02"}}]}, {}
        if path == "/api/cluster":
            return ONTAP["cluster"], {}
        key = {"/api/cluster/nodes": "nodes", "/api/storage/aggregates": "aggregates", "/api/storage/luns": "luns",
               "/api/network/ip/interfaces": "lifs", "/api/storage/disks": "disks", "/api/support/ems/events": "events"}[path]
        return {"records": ONTAP[key]}, {}
    monkeypatch.setattr(http.JsonClient, "request", fake)
    out = netapp.collect(StorageConfig("n1", "A300", "netapp", "ontap.local", username="icoro", password="s3cret"), now=NOW)
    assert {m for m, _, _ in calls} == {"GET"}
    assert all(auth and auth.startswith("Basic ") for _, _, auth in calls)
    assert [v["name"] for v in out["volumes"] if v["kind"] == "volume"] == ["nfs_ds01", "nfs_ds02"]


# ---------------------------------------------------------------- correlation + levels
@pytest.fixture()
def db(app_):
    s = SessionLocal()
    for m in (StorageArrayState, StorageVolumeState, StorageAlertState, DatastoreState):
        s.query(m).delete()
    s.query(DataSource).filter(DataSource.kind.in_(("pure", "netapp"))).delete(synchronize_session=False)
    s.commit()
    yield s
    for m in (StorageArrayState, StorageVolumeState, StorageAlertState, DatastoreState):
        s.query(m).delete()
    s.query(DataSource).filter(DataSource.kind.in_(("pure", "netapp"))).delete(synchronize_session=False)
    s.commit()
    s.close()


def _ds(db, name, backing, vms):
    db.add(DatastoreState(vcenter_key="vc1", ds_key=name, name=name, ds_type="VMFS", capacity_gb=1000, free_gb=300, uncommitted_gb=0,
                          clusters=json.dumps(["PROD"]), backing=json.dumps(backing), vm_count=vms))


def test_correlation_and_levels(db, monkeypatch):
    p_cfg = StorageConfig("pure-x20", "Pure X20", "pure", "fa")
    n_cfg = StorageConfig("ntap", "AFF A300", "netapp", "ontap")
    monkeypatch.setattr(infra, "_configured", lambda: {"pure-x20", "ntap"})
    collector.store(db, p_cfg, pure.parse(PURE_ARRAYS, PURE_PERF, PURE_CTL, PURE_HW, PURE_ALERTS, PURE_VOLS), NOW)
    o = ONTAP
    collector.store(db, n_cfg, netapp.parse(o["cluster"], o["nodes"], o["aggregates"], o["volumes"], o["luns"], o["lifs"], [], []), NOW)
    _ds(db, "DS-VMFS-PURE", {"naa": ["naa.624a9370f4252922ade248cf000113e8"]}, 30)
    _ds(db, "DS-VMFS-NTAP", {"naa": ["naa.600a0980" + "wOj4o$JYDyrL".encode().hex()]}, 12)
    _ds(db, "DS-NFS", {"naa": [], "nfsHost": "10.20.0.11", "nfsPath": "/nfs_ds01/"}, 7)
    _ds(db, "DS-LOCAL", {"naa": ["naa.5000c500deadbeef"]}, 2)
    db.commit()
    st = infra.storage(db, NOW)
    arrays = {a["sourceKey"]: a for a in st["arrays"]}
    # Pure: failed PSU + critical alert → VƯỢT NGƯỠNG; write latency 6,2 ms ≥ 5 → reason; 75 % used → nothing (< 80)
    assert arrays["pure-x20"]["level"] == "crit" and "Độ trễ 6,2 ms (≥ 5 ms)" in arrays["pure-x20"]["reasons"]
    assert arrays["pure-x20"]["usedPct"] == 75.0
    # ONTAP: one failed PSU → VƯỢT NGƯỠNG
    assert arrays["ntap"]["level"] == "crit" and any("PSU2" in r for r in arrays["ntap"]["reasons"])
    vols = {v["name"]: v for v in st["volumes"]}
    assert [d["datastore"] for d in vols["vmfs-prod-01"]["datastores"]] == ["DS-VMFS-PURE"] and vols["vmfs-prod-01"]["vmCount"] == 30
    assert vols["/vol/vmfs_vol/lun0"]["datastores"][0]["datastore"] == "DS-VMFS-NTAP"
    assert vols["nfs_ds01"]["datastores"][0]["datastore"] == "DS-NFS" and vols["nfs_ds01"]["vmCount"] == 7
    ds = {d["name"]: d for d in infra.datastores(db, now=NOW)}
    assert ds["DS-VMFS-PURE"]["storage"] == {"sourceKey": "pure-x20", "array": "pure-x20-hcm", "volume": "vmfs-prod-01", "kind": "volume"}
    assert ds["DS-LOCAL"]["storage"] is None
    # a failed read keeps the figures and says so
    collector.record_error(db, p_cfg, "Không kết nối được fa:443", NOW + dt.timedelta(minutes=5))
    db.commit()
    a = {x["sourceKey"]: x for x in infra.storage(db, NOW + dt.timedelta(minutes=5))["arrays"]}["pure-x20"]
    assert a["capacityGB"] == 40960.0 and any(r.startswith("Không đọc được mảng") for r in a["reasons"])
    # a source removed from Settings disappears from the page
    monkeypatch.setattr(infra, "_configured", lambda: {"ntap"})
    assert [x["sourceKey"] for x in infra.storage(db, NOW)["arrays"]] == ["ntap"]


# ---------------------------------------------------------------- Settings → Nguồn dữ liệu
def test_sources_api_storage(db, admin, editor):
    r = admin.post("/api/admin/sources", json={"kind": "pure", "key": "pure-x20", "name": "Pure X20R3", "host": "fa-x20.corp.local"})
    assert r.status_code == 400 and "API token" in r.json()["detail"]
    r = admin.post("/api/admin/sources", json={"kind": "pure", "key": "pure-x20", "name": "Pure X20R3", "host": "fa-x20.corp.local",
                                         "apiKey": "T-OKEN-SECRET-1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hasApiKey"] is True and "T-OKEN-SECRET-1" not in r.text and body["port"] == 443
    r = admin.post("/api/admin/sources", json={"kind": "netapp", "key": "ntap-a300", "name": "AFF A300", "host": "10.1.1.50", "username": "icoro"})
    assert r.status_code == 400 and "readonly" in r.json()["detail"]
    r = admin.post("/api/admin/sources", json={"kind": "netapp", "key": "ntap-a300", "name": "AFF A300", "host": "10.1.1.50",
                                         "username": "icoro", "password": "P@ss-secret"})
    assert r.status_code == 200 and r.json()["hasPassword"] is True and "P@ss-secret" not in r.text
    listing = admin.get("/api/admin/sources").text
    assert "T-OKEN-SECRET-1" not in listing and "P@ss-secret" not in listing
    assert editor.post("/api/admin/sources", json={"kind": "pure", "key": "x", "name": "x", "host": "x", "demo": True}).status_code == 403
    t = admin.post("/api/admin/sources/test", json={"kind": "pure", "name": "d", "host": "", "demo": True})
    assert t.json()["ok"] is True and "DEMO" in t.json()["message"]
    from app.config import get_settings
    from app import sources
    sources.invalidate_cache()
    cfgs = {c.key: c for c in get_settings().storages()}
    assert cfgs["pure-x20"].api_token == "T-OKEN-SECRET-1" and cfgs["ntap-a300"].password == "P@ss-secret"
    assert cfgs["ntap-a300"].vendor == "netapp"


def test_demo_end_to_end(db, admin):
    """Demo vCenter + demo arrays (X20R3, RC20, AFF A300): every VMFS / NFS demo datastore lands on an array."""
    from app.collectors import scheduler
    from app.config import VCenterConfig, get_settings
    from app import sources
    with SessionLocal() as s:
        scheduler.sync_vcenter(s, VCenterConfig("vcd", "VC demo", "demo", 443, "u", "p", False, demo=True))
    for key, kind, name in (("pure-x20", "pure", "Pure FlashArray//X20R3"), ("pure-rc20", "pure", "Pure FlashArray//RC20"),
                            ("ntap-a300", "netapp", "NetApp AFF A300")):
        assert admin.post("/api/admin/sources", json={"kind": kind, "key": key, "name": name, "host": "", "demo": True}).status_code == 200
    sources.invalidate_cache()
    res = scheduler.sync_storages(get_settings())
    assert [r["ok"] for r in res] == [True, True, True]
    with SessionLocal() as s:
        st = infra.storage(s)
        models = sorted(a["model"] for a in st["arrays"])
        assert models == ["AFF-A300", "FA-RC20", "FA-X20R3"]
        ds = [d for d in infra.datastores(s) if d["vcenterKey"] == "vcd"]
        assert ds and all(d["storage"] for d in ds)                       # every demo datastore correlated
        assert {d["storage"]["sourceKey"] for d in ds} == {"pure-x20", "pure-rc20", "ntap-a300"}
        assert any(a["level"] == "crit" for a in st["arrays"])            # the demo A300 has a failed PSU
        s.query(DatastoreState).filter_by(vcenter_key="vcd").delete()
        s.commit()
