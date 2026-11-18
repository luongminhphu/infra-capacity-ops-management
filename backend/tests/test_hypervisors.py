"""Proxmox VE and Nutanix AHV sources: read-only collectors, normalised rows, Settings, demo sync, licensing."""
import pytest

from app import sources
from app.config import VCenterConfig, get_settings
from app.database import SessionLocal
from app.hypervisors import nutanix, proxmox
from app.licensing.assess import host_license_row
from app.models import DataSource, HostSnapshot, SnapshotRun, VmSnapshot
from app.storage import StorageError
from app.storage.http import JsonClient

from test_sources import client  # noqa: F401  (fixture)

GIB = 1024 ** 3


def _cfg(platform="proxmox", **kw):
    base = dict(key="pve-dc1", name="PVE DC1", host="pve01.corp.example", port=8006, username="ico@pve!monitor",
                password="11111111-2222-3333-4444-555555555555", verify_ssl=False, platform=platform)
    base.update(kw)
    return VCenterConfig(**base)


# ---------------------------------------------------------------- Proxmox VE
PVE_NODES = [{"node": "pve01", "status": "online", "cpu": 0.25, "maxcpu": 64, "mem": 200 * GIB, "maxmem": 512 * GIB},
             {"node": "pve02", "status": "offline", "maxcpu": 64, "maxmem": 512 * GIB}]
PVE_STATUS = {"pve01": {"cpuinfo": {"cores": 16, "sockets": 2, "cpus": 64, "mhz": "2800.000", "model": "Intel(R) Xeon(R) Gold 6342"},
                        "memory": {"total": 512 * GIB, "used": 256 * GIB}, "pveversion": "pve-manager/8.2.4/faa83925c9641325"}}
PVE_RES = [
    {"type": "qemu", "vmid": 101, "name": "app01", "node": "pve01", "status": "running", "maxcpu": 4, "cpu": 0.5,
     "maxmem": 8 * GIB, "mem": 4 * GIB, "maxdisk": 100 * GIB, "disk": 0, "template": 0},
    {"type": "lxc", "vmid": 200, "name": "dns01", "node": "pve01", "status": "stopped", "maxcpu": 2,
     "maxmem": 2 * GIB, "maxdisk": 20 * GIB, "disk": 5 * GIB},
    {"type": "qemu", "vmid": 9000, "name": "tpl-ubuntu", "node": "pve01", "status": "stopped", "template": 1},
]
PVE_STORAGE = [
    {"storage": "ceph-vm", "node": "pve01", "shared": 1, "status": "available", "maxdisk": 10240 * GIB, "disk": 4096 * GIB, "plugintype": "rbd"},
    {"storage": "ceph-vm", "node": "pve02", "shared": 1, "status": "available", "maxdisk": 10240 * GIB, "disk": 4096 * GIB, "plugintype": "rbd"},
    {"storage": "local-lvm", "node": "pve01", "shared": 0, "status": "available", "maxdisk": 900 * GIB, "disk": 300 * GIB, "plugintype": "lvmthin"},
]


def test_proxmox_parse_hosts_vms_storage():
    hosts, vms, ex = proxmox.parse("PVE-PROD", PVE_NODES, PVE_STATUS, PVE_RES, PVE_STORAGE)
    h1, h2 = hosts
    assert (h1["cluster"], h1["host"], h1["cores"], h1["cpuSockets"], h1["cpuThreads"], h1["cpuMhz"], h1["ramGB"]) == \
        ("PVE-PROD", "pve01", 32.0, 2, 64, 2800.0, 512.0)
    assert (h1["cpuUsagePctRaw"], h1["ramUsagePctRaw"], h1["status"]) == (25.0, 50.0, "connected")
    assert (h1["esxiVersion"], h1["esxiBuild"]) == ("Proxmox VE 8.2.4", "faa83925c9641325")
    assert (h2["status"], h2["cores"], h2["cpuUsagePctRaw"]) == ("disconnected", 64.0, None)       # offline: /nodes figures
    assert [v["vm"] for v in vms] == ["app01", "dns01"]                                           # template skipped
    app, dns = vms
    assert (app["instanceUuid"], app["vcpu"], app["ramGB"], app["diskProvGB"], app["diskUsedGB"], app["cpuUsagePctRaw"],
            app["ramUsagePctRaw"], app["isPoweredOn"]) == ("qemu/101", 4.0, 8.0, 100.0, 100.0, 50.0, 50.0, True)
    assert (dns["diskUsedGB"], dns["isPoweredOn"], dns["guestId"], dns["cpuUsagePctRaw"]) == (5.0, False, "lxc", None)
    ds = {d["key"]: d for d in ex["datastores"]}
    assert ds["shared/ceph-vm"]["hostCount"] == 2 and ds["shared/ceph-vm"]["capacityGB"] == 10240.0
    assert ds["shared/ceph-vm"]["freeGB"] == 6144.0 and ds["pve01/local-lvm"]["name"] == "local-lvm (pve01)"
    assert [x["overallStatus"] for x in ex["health"]] == ["green", "red"]


def test_proxmox_client_is_get_only_with_api_token(monkeypatch):
    calls = []

    def fake(self, method, path, params=None, headers=None):
        calls.append((method, path, dict(params or {}), self.headers.get("Authorization")))
        data = {"/version": {"version": "8.2.4"}, "/cluster/status": [{"type": "cluster", "name": "PVE-PROD"}],
                "/nodes": PVE_NODES, "/nodes/pve01/status": PVE_STATUS["pve01"]}
        if path == "/cluster/resources":
            return {"data": PVE_RES if params["type"] == "vm" else PVE_STORAGE}, {}
        return {"data": data[path]}, {}
    monkeypatch.setattr(JsonClient, "request", fake)
    hosts, vms, _ = proxmox.collect(_cfg())
    assert len(hosts) == 2 and len(vms) == 2 and hosts[0]["cluster"] == "PVE-PROD"
    assert {m for m, *_ in calls} == {"GET"}
    assert calls[0][3] == "PVEAPIToken=ico@pve!monitor=11111111-2222-3333-4444-555555555555"
    assert [p for _, p, *_ in calls].count("/cluster/resources") == 2                 # no per-VM request
    assert "pve02" not in " ".join(p for _, p, *_ in calls)                            # offline node not queried
    assert proxmox.test(_cfg()).startswith("Kết nối thành công: Proxmox VE 8.2.4 · cụm PVE-PROD · 1/2 node online")
    with pytest.raises(StorageError, match="Token ID dạng"):
        proxmox.ProxmoxClient(_cfg(username="root@pam"))


def test_json_client_still_refuses_writes_for_hypervisors():
    from app.hypervisors import http_view
    c = JsonClient(http_view(_cfg()), "https://pve01.corp.example:8006/api2/json", product="Proxmox VE")
    with pytest.raises(StorageError, match="ICO chỉ đọc dữ liệu Proxmox VE"):
        c.request("POST", "/nodes/pve01/qemu/101/status/stop")


# ---------------------------------------------------------------- Nutanix
NTX_CLUSTER = {"name": "NTX-PROD", "version": "6.10"}
NTX_HOSTS = [{"name": "ntx-01", "uuid": "h1", "num_cpu_cores": 48, "num_cpu_sockets": 2, "num_cpu_threads": 96,
              "cpu_frequency_in_hz": 2600000000, "memory_capacity_in_bytes": 768 * GIB, "cpu_model": "Intel Xeon Gold 6342",
              "hypervisor_full_name": "AHV 10.0", "block_model_name": "NX-3170-G8", "state": "NORMAL",
              "stats": {"hypervisor_cpu_usage_ppm": "312500", "hypervisor_memory_usage_ppm": "650000"}},
             {"name": "ntx-02", "uuid": "h2", "num_cpu_cores": 48, "num_cpu_sockets": 2, "num_cpu_threads": 96,
              "memory_capacity_in_bytes": 768 * GIB, "state": "MAINTENANCE", "stats": {}}]
NTX_VMS = [{"name": "db01", "uuid": "v1", "num_vcpus": 2, "num_cores_per_vcpu": 4, "memory_mb": 32768, "power_state": "on",
            "host_uuid": "h1", "vm_nics": [{"ip_address": "10.0.0.11"}],
            "vm_disk_info": [{"is_cdrom": True, "size": 5 * GIB}, {"is_cdrom": False, "size": 200 * GIB, "storage_container_uuid": "c1"},
                             {"is_cdrom": False, "size": 300 * GIB, "storage_container_uuid": "c1"}]},
           {"name": "off01", "uuid": "v2", "num_vcpus": 1, "num_cores_per_vcpu": 2, "memory_mb": 4096, "power_state": "off", "host_uuid": None}]
NTX_CT = [{"name": "default-container", "storage_container_uuid": "c1",
           "usage_stats": {"storage.user_capacity_bytes": str(20480 * GIB), "storage.user_usage_bytes": str(8192 * GIB)}}]


def test_nutanix_parse():
    hosts, vms, ex = nutanix.parse(NTX_CLUSTER, NTX_HOSTS, NTX_VMS, NTX_CT)
    h1, h2 = hosts
    assert (h1["cluster"], h1["cores"], h1["cpuSockets"], h1["cpuThreads"], h1["cpuMhz"], h1["ramGB"]) == ("NTX-PROD", 48.0, 2, 96, 2600.0, 768.0)
    assert (h1["cpuUsagePctRaw"], h1["ramUsagePctRaw"], h1["vendor"], h1["model"], h1["esxiVersion"]) == \
        (31.2, 65.0, "Nutanix", "NX-3170-G8", "AHV 10.0")
    assert h2["status"] == "maintenance" and h2["cpuUsagePctRaw"] is None and h2["esxiVersion"] == "AHV · AOS 6.10"
    db, off = vms
    assert (db["vcpu"], db["ramGB"], db["diskProvGB"], db["datastore"], db["host"], db["ipAddress"], db["isPoweredOn"]) == \
        (8.0, 32.0, 500.0, "default-container", "ntx-01", "10.0.0.11", True)
    assert (off["vcpu"], off["host"], off["datastore"], off["isPoweredOn"]) == (2.0, None, "UNKNOWN", False)
    d = ex["datastores"][0]
    assert (d["capacityGB"], d["freeGB"], d["vmCount"], d["hostCount"]) == (20480.0, 12288.0, 1, 2)
    assert [x["overallStatus"] for x in ex["health"]] == ["green", "red"]


def test_nutanix_pages_vms_with_get(monkeypatch):
    calls = []
    many = [{"name": f"vm{i}", "uuid": f"u{i}", "num_vcpus": 1, "num_cores_per_vcpu": 1, "memory_mb": 1024, "power_state": "on"}
            for i in range(nutanix.PAGE + 20)]

    def fake(self, method, path, params=None, headers=None):
        calls.append((method, path, dict(params or {})))
        if path == "/vms/":
            off = params["offset"]
            return {"entities": many[off:off + params["length"]], "metadata": {"grand_total_entities": len(many)}}, {}
        return ({"entities": NTX_HOSTS} if path == "/hosts/" else {"entities": NTX_CT} if path == "/storage_containers/"
                else NTX_CLUSTER), {}
    monkeypatch.setattr(JsonClient, "request", fake)
    hosts, vms, _ = nutanix.collect(_cfg("nutanix", port=9440, username="ico_ro", password="pw"))
    assert len(vms) == nutanix.PAGE + 20 and len(hosts) == 2
    assert {m for m, *_ in calls} == {"GET"}
    assert [c[2].get("offset") for c in calls if c[1] == "/vms/"] == [0, nutanix.PAGE]
    with pytest.raises(StorageError, match="role Viewer"):
        nutanix.PrismClient(_cfg("nutanix", username="", password=""))


# ---------------------------------------------------------------- Settings, demo sync, licensing
def _pve(**kw):
    return {"kind": "proxmox", "key": "pve-dc1", "name": "Proxmox DC1", "host": "pve01.corp.example",
            "username": "ico@pve!monitor", "password": "token-secret-123", "verifySsl": False, **kw}


@pytest.fixture()
def clean_runs():
    yield
    db = SessionLocal()
    runs = [r.id for r in db.query(SnapshotRun.id).filter(SnapshotRun.vcenter_key.in_(("pve-demo", "ntx-demo")))]
    for m in (HostSnapshot, VmSnapshot):
        db.query(m).filter(m.run_id.in_(runs or [-1])).delete(synchronize_session=False)
    from app.models import ClusterAggregate, ComplianceFindingSnapshot
    for m in (ClusterAggregate, ComplianceFindingSnapshot):
        db.query(m).filter(m.run_id.in_(runs or [-1])).delete(synchronize_session=False)
    db.query(SnapshotRun).filter(SnapshotRun.id.in_(runs or [-1])).delete(synchronize_session=False)
    db.commit()
    db.close()


def test_sources_api_for_proxmox_and_nutanix(client):
    r = client.post("/api/admin/sources", json=_pve(username="root@pam"))
    assert r.status_code == 400 and "user@realm!tên-token" in r.json()["detail"]
    assert client.post("/api/admin/sources", json=_pve(password="")).json()["detail"].startswith("Cần Token ID và Secret")
    created = client.post("/api/admin/sources", json=_pve()).json()
    assert created["port"] == 8006 and created["hasPassword"] is True and "token-secret-123" not in str(created)
    n = client.post("/api/admin/sources", json={"kind": "nutanix", "key": "ntx-dc1", "name": "Nutanix DC1", "host": "10.0.0.50",
                                               "username": "ico_ro", "password": "pw-123"}).json()
    assert n["port"] == 9440
    # one history family: the same key cannot be used by a vCenter and a Proxmox source
    r = client.post("/api/admin/sources", json={"kind": "vcenter", "key": "pve-dc1", "name": "x", "host": "vc.corp.example",
                                               "username": "a", "password": "b"})
    assert r.status_code == 409 and "nền tảng ảo hoá khác" in r.json()["detail"]
    platforms = {c.key: c.platform for c in get_settings().vcenters()}
    assert platforms["pve-dc1"] == "proxmox" and platforms["ntx-dc1"] == "nutanix"
    assert sources.platform_of("pve-dc1") == "proxmox" and sources.platform_of("import") is None
    vc = {v["key"]: v for v in client.get("/api/vcenters").json()}
    assert vc["pve-dc1"]["platform"] == "proxmox"


def test_demo_proxmox_sync_feeds_capacity_and_skips_vmware_license(client, clean_runs):
    from app.collectors import scheduler
    from app import data_access
    client.post("/api/admin/sources", json=_pve(key="pve-demo", demo=True, username="", password=""))
    cfg = next(c for c in get_settings().vcenters() if c.key == "pve-demo")
    db = SessionLocal()
    try:
        run = scheduler.sync_vcenter(db, cfg)
        assert run.status == "ok" and run.host_count > 0
        hosts, vms, _ = data_access.get_latest_combined(db, ["pve-demo"], include_import=False)
    finally:
        db.close()
    assert hosts and all(h["platform"] == "proxmox" and "-pve" in h["host"] for h in hosts)
    assert all(h["esxiVersion"] == "Proxmox VE 8.2.4" for h in hosts) and vms
    row = host_license_row({**hosts[0], "cores": 32, "cpuSockets": 2}, {"min_cores_per_proc": 8, "min_cores_per_host": 16},
                           {"min_cores_per_cpu": 16}, {"cores_per_cpu_license": 32})
    assert (row["vmwareCores"], row["vmwareLegacyCpuLicenses"], row["wsLicensableCores"]) == (0, None, 32)
    assert "not_vmware" in row["flags"]
    vmw = host_license_row({**hosts[0], "platform": "vmware", "cores": 32, "cpuSockets": 2},
                           {"min_cores_per_proc": 8, "min_cores_per_host": 16}, {"min_cores_per_cpu": 16}, None)
    assert vmw["vmwareCores"] == 32


def test_collector_errors_are_recorded_like_vcenter_failures(client, monkeypatch, clean_runs):
    from app.collectors import scheduler
    from app.vcenter.client import VCenterConnectionError
    monkeypatch.setattr(proxmox, "collect", lambda cfg: (_ for _ in ()).throw(StorageError("pve01:8006 từ chối xác thực (401)")))
    cfg = _cfg(key="pve-demo", name="PVE lỗi")
    db = SessionLocal()
    try:
        with pytest.raises(VCenterConnectionError, match="Không đọc được Proxmox VE 'PVE lỗi'.*401"):
            scheduler.sync_vcenter(db, cfg)
        assert db.query(SnapshotRun).filter_by(vcenter_key="pve-demo", status="error").count() == 1
    finally:
        db.close()
