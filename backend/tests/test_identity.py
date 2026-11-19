"""ICO v2 G1a: canonical identity keys + host hardware serial (vCenter / Nutanix / Proxmox / demo)."""
from types import SimpleNamespace as NS

import pytest

from app.shared import identity as idn


# ---------------------------------------------------------------- pure key functions
@pytest.mark.parametrize("raw, want", [
    ("71fhkz3", "71FHKZ3"),
    ("  71FHKZ3 ", "71FHKZ3"),
    ("SN-R750-01", "SNR75001"),
    ("cz j 1234 abc", "CZJ1234ABC"),
    (952248001227, "952248001227"),
    (None, ""),
    ("", ""),
    ("---", ""),
    ("x" * 200, "X" * 128),
])
def test_norm_serial(raw, want):
    assert idn.norm_serial(raw) == want


def test_norm_serial_matches_dc_assets_serial_key():
    from app import dc_assets
    assert dc_assets.serial_key is idn.norm_serial
    for raw in ("71FHKZ3 ", "sn:ab-12", None, 0, "Đ12", "ß9"):
        assert dc_assets.serial_key(raw) == idn.norm_serial(raw)


@pytest.mark.parametrize("raw, want", [
    ("esx01.corp.local", "esx01.corp.local"),
    ("ESX01.Corp.Local", "esx01.corp.local"),
    ("esx01.corp.local.", "esx01.corp.local"),
    ("  esx01.corp.local  ", "esx01.corp.local"),
    ("ESX01", "esx01"),
    (None, ""),
    ("", ""),
])
def test_norm_fqdn(raw, want):
    assert idn.norm_fqdn(raw) == want


@pytest.mark.parametrize("raw, want", [
    ("esx01.corp.local", "esx01"),
    ("ESX01.Corp.Local.", "esx01"),
    (" esx01 ", "esx01"),
    ("10.1.2.3", "10.1.2.3"),
    ("db-01.hsc.vn", "db-01"),
    (None, ""),
    ("", ""),
])
def test_short_name(raw, want):
    assert idn.short_name(raw) == want


@pytest.mark.parametrize("raw, want", [
    ("AA-BB-CC-DD-EE-FF", "aa:bb:cc:dd:ee:ff"),
    ("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff"),
    ("AABB.CCDD.EEFF", "aa:bb:cc:dd:ee:ff"),
    ("00505689ab0c", "00:50:56:89:ab:0c"),
    (" 00:50:56:89:AB:0C ", "00:50:56:89:ab:0c"),
    ("aa:bb:cc:dd:ee", None),            # 40 bits
    ("aa:bb:cc:dd:ee:gg", None),         # not hex
    ("aa:bb:cc:dd:ee:ff:00", None),      # 56 bits
    (None, None),
    ("", None),
])
def test_norm_mac(raw, want):
    assert idn.norm_mac(raw) == want


@pytest.mark.parametrize("raw, want", [
    ("naa.624A9370ABCDEF0123456789", "624a9370abcdef0123456789"),
    ("NAA.600a0980383046", "600a0980383046"),
    ("624a9370abcdef", "624a9370abcdef"),
    ("  naa.624a9370abcdef ", "624a9370abcdef"),
    ("naa.6000-0980-3830", "600009803830"),
    (None, ""),
    ("", ""),
])
def test_norm_naa(raw, want):
    assert idn.norm_naa(raw) == want


@pytest.mark.parametrize("raw, want", [
    ("10.1.2.3", "10.1.2.3"),
    (" 10.1.2.3 ", "10.1.2.3"),
    ("FE80:0:0:0:0:0:0:1", "fe80::1"),
    ("10.1.2.256", None),
    ("10.1.2", None),
    ("esx01", None),
    (None, None),
    ("", None),
])
def test_norm_ip(raw, want):
    assert idn.norm_ip(raw) == want


@pytest.mark.parametrize("raw, want", [
    ("SQL-PROD-01.corp.local", "sql-prod-01"),
    ("sql-prod-01", "sql-prod-01"),
    (" Sql-Prod-01 ", "sql-prod-01"),
    ("10.20.30.40", "10.20.30.40"),
    ("10.20.30", "10"),
    ("", None),
    ("   ", None),
    (None, None),
])
def test_nbu_client_key_and_backup_alias(raw, want):
    from app.backup.client import client_key
    assert idn.nbu_client_key(raw) == want
    assert client_key(raw) == want


# ---------------------------------------------------------------- vCenter: summary.hardware
def _id(key, value):
    return NS(identifierType=NS(key=key), identifierValue=value)


@pytest.mark.parametrize("infos, want", [
    ([_id("AssetTag", "No Asset Tag"), _id("ServiceTag", "7XK2Q93"), _id("SerialNumberTag", "CN123")], "7XK2Q93"),
    ([_id("SerialNumberTag", "CZJ1234ABC"), _id("EnclosureSerialNumberTag", "ENC1")], "CZJ1234ABC"),
    ([_id("EnclosureSerialNumberTag", "ENC1")], "ENC1"),
    ([_id("ServiceTag", "  "), _id("SerialNumberTag", "SN2")], "SN2"),          # blank service tag skipped
    ([_id("AssetTag", "A1"), _id("OemSpecificString", "x")], None),
    ([], None),
    (None, None),
])
def test_host_serial_preference(infos, want):
    from app.vcenter.client import host_serial
    assert host_serial(NS(otherIdentifyingInfo=infos)) == want


def test_host_serial_without_hardware():
    from app.vcenter.client import host_serial
    assert host_serial(None) is None


def test_build_rows_reads_serial_and_uuid_from_summary():
    from app.vcenter import client as vc
    hw = NS(numCpuCores=32, cpuMhz=2400, memorySize=512 * 1024 ** 3, vendor="Dell Inc.", model="PowerEdge R750",
            cpuModel="Xeon", numCpuPkgs=2, numCpuThreads=64, uuid="4c4c4544-0058-4b10-8032-b7c04f513933",
            otherIdentifyingInfo=[_id("ServiceTag", "7XK2Q93")])
    summary = NS(hardware=hw, quickStats=NS(overallCpuUsage=0, overallMemoryUsage=0),
                 config=NS(product=NS(version="8.0.2", build="1")), runtime=NS(connectionState="connected"))
    hosts = {"host-1": (None, {"name": "esx01", "summary": summary, "parent": None})}
    host_rows, _ = vc.build_rows(hosts, {}, {}, {})
    assert (host_rows[0]["serial"], host_rows[0]["hwUuid"]) == ("7XK2Q93", "4c4c4544-0058-4b10-8032-b7c04f513933")
    assert "otherIdentifyingInfo" not in " ".join(vc.HOST_PROPS)       # read from "summary", no new property path


# ---------------------------------------------------------------- Nutanix / Proxmox
def test_nutanix_maps_node_serial_not_block_serial():
    from app.hypervisors import nutanix
    hosts = [{"name": "ntx-01", "uuid": "h1", "serial": " 7XK2Q93 ", "block_serial": "BLK1", "state": "NORMAL"},
             {"name": "ntx-02", "uuid": "h2", "block_serial": "BLK1", "state": "NORMAL"}]
    rows, _, _ = nutanix.parse({"name": "NTX"}, hosts, [], [])
    assert [(r["serial"], r["hwUuid"]) for r in rows] == [("7XK2Q93", None), (None, None)]


# ---------------------------------------------------------------- demo
def test_demo_service_tag_is_stable_dell_style():
    from app.vcenter.demo_generator import _TAG_ALPHABET, demo_service_tag
    a = demo_service_tag("vc1-PROD-CLUSTER-A-esx01")
    assert a == demo_service_tag("vc1-PROD-CLUSTER-A-esx01") and len(a) == 7 and set(a) <= set(_TAG_ALPHABET)
    assert a != demo_service_tag("vc1-PROD-CLUSTER-A-esx02")
    assert idn.norm_serial(a) == a


def test_demo_identity_per_platform():
    from app.config import VCenterConfig
    from app.vcenter import demo_generator as dg
    vmw, _ = dg.collect_inventory(VCenterConfig("vcid", "VC", "demo", 443, "u", "p", False, demo=True))
    assert len(vmw) == 13 and all(h["serial"] and h["hwUuid"] for h in vmw)
    dell = [h for h in vmw if h["vendor"].startswith("Dell")]
    hpe = [h for h in vmw if h["vendor"] == "HPE"]
    assert len(dell) == 10 and all(len(h["serial"]) == 7 for h in dell)
    assert len(hpe) == 3 and all(h["serial"].startswith("CZJ") and len(h["serial"]) == 10 for h in hpe)
    assert len({h["serial"] for h in vmw}) == 13
    # Adding the identity did not shift the seeded inventory (usage comes from the same random stream).
    again, _ = dg.collect_inventory(VCenterConfig("vcid", "VC", "demo", 443, "u", "p", False, demo=True))
    assert [(h["serial"], h["cpuUsagePctRaw"]) for h in again] == [(h["serial"], h["cpuUsagePctRaw"]) for h in vmw]
    pve, _ = dg.collect_inventory(VCenterConfig("pveid", "PVE", "demo", 443, "u", "p", False, demo=True, platform="proxmox"))
    assert all(h["serial"] is None and h["hwUuid"] is None for h in pve)


@pytest.fixture()
def _schema():
    from app.database import ensure_schema
    ensure_schema()


def test_demo_sync_stores_serial_and_old_rows_stay_readable(_schema):
    from app import data_access
    from app.collectors import scheduler
    from app.collectors.retention import CHILD_MODELS
    from app.config import VCenterConfig, get_settings
    from app.database import SessionLocal
    from app.models import HostSnapshot, SnapshotRun
    db = SessionLocal()
    cfg = VCenterConfig("vc-serial", "VC serial", "demo", 443, "u", "p", False, demo=True)
    run = scheduler.sync_vcenter(db, cfg, get_settings())
    try:
        assert run.status == "ok"
        stored = db.query(HostSnapshot).filter(HostSnapshot.run_id == run.id).all()
        assert len(stored) == 13 and all(h.serial and h.hw_uuid for h in stored)
        # A row written before G1a (serial / hw_uuid NULL) still loads.
        stored[0].serial = stored[0].hw_uuid = None
        db.commit()
        hosts, _, _ = data_access.get_latest_combined(db, ["vc-serial"], include_import=False)
        assert len(hosts) == 13 and sum(1 for h in hosts if h["serial"] is None and h["hwUuid"] is None) == 1
        assert all(h["serial"] for h in hosts if h["host"] != stored[0].host)
    finally:
        for model in CHILD_MODELS:
            db.query(model).filter(model.run_id == run.id).delete(synchronize_session=False)
        db.query(SnapshotRun).filter(SnapshotRun.id == run.id).delete(synchronize_session=False)
        db.commit()
        db.close()
