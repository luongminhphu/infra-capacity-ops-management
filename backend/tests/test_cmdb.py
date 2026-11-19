"""ICO v2 G1b: CI registry — builder on a small fixed inventory, idempotence, retirement, API, build time."""
import datetime as dt
import json
import time
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.cmdb import builder
from app.cmdb.models import Ci, CiKey, CiLink
from app.database import Base
from test_spend_opex import _login, app_  # noqa: F401  (fixture)

T0 = dt.datetime(2026, 11, 1, 8, 0, 0)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A private SQLite database: exact counts, no rows from other tests."""
    eng = create_engine(f"sqlite:///{tmp_path / 'cmdb.db'}")
    event.listen(eng, "connect", lambda c, _r: c.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()
    eng.dispose()


def _settings(monkeypatch, vcs=("vct",), nbus=("nbu-t",), arrays=("pure-t",), retire_days=7):
    from app import infra
    from app.config import get_settings
    real = get_settings()
    fake = NS(vcenters=lambda: [NS(key=k, platform="vmware") for k in vcs], netbackups=lambda: [NS(key=k) for k in nbus],
              storages=lambda: [NS(key=k) for k in arrays], cmdb_retire_days=retire_days,
              metrics_raw_days=real.metrics_raw_days)
    monkeypatch.setattr(builder, "get_settings", lambda: fake)
    monkeypatch.setattr(infra, "get_settings", lambda: fake)


def _run(db, at, vms):
    from app.models import HostSnapshot, SnapshotRun, VmSnapshot
    run = SnapshotRun(vcenter_key="vct", source="live", status="ok", started_at=at, finished_at=at)
    db.add(run)
    db.flush()
    db.add_all([
        HostSnapshot(run_id=run.id, vcenter_key="vct", cluster="PRD", host="esx01.corp.local", cores=32, vendor="Dell Inc.",
                     model="PowerEdge R750", serial="7XK2Q93", hw_uuid="4C4C4544-0058-4B10"),
        HostSnapshot(run_id=run.id, vcenter_key="vct", cluster="PRD", host="esx02.corp.local", cores=32),
    ])
    for name, uuid, host, ip, guest in vms:
        db.add(VmSnapshot(run_id=run.id, vcenter_key="vct", cluster="PRD", host=host, vm=name, instance_uuid=uuid,
                          is_powered_on=True, power_state="Powered On", ip_address=ip, vcpu=2, ram_gb=8, datastore="DS1",
                          guest_hostname=guest))
    db.commit()


VMS = [("vm-a", "u-1", "esx01.corp.local", "10.0.0.1", "vm-a.corp.local"),
       ("vm-b", "u-2", "esx01.corp.local", "10.0.0.2", None),
       ("vm-c", "u-3", "esx02.corp.local", "10.0.0.3", None)]


def _seed(db):
    from app.models import BackupJob, DatastoreState, DcAsset, StorageArrayState, StorageVolumeState
    from app.spend.models import OpexAsset, OpexItem, SpendCategory, SpendVendor
    _run(db, T0, VMS)
    db.add(DatastoreState(vcenter_key="vct", ds_key="datastore-1", name="DS1", ds_type="VMFS",
                          backing=json.dumps({"naa": ["naa.624a9370abc0001"], "nfsHost": None, "nfsPath": None})))
    db.add(StorageArrayState(source_key="pure-t", vendor="pure", name="FA-01", captured_at=T0, last_ok_at=T0))
    db.add(StorageVolumeState(source_key="pure-t", name="vol-ds1", kind="volume", naa="naa.624a9370abc0001"))
    db.add(DcAsset(asset_key="tag:HSC-001", category="server", device_id="esx01", serial="7xk2q93", serial_key="7XK2Q93",
                   asset_tag="hsc-001"))
    db.add(BackupJob(nbu_key="nbu-t", job_id=1, client_name="VM-A.corp.local", client_key="vm-a", status=0,
                     start_time=T0 - dt.timedelta(days=1)))
    cat = db.query(SpendCategory).filter_by(code="CMDB-HW").first() or \
        SpendCategory(code="CMDB-HW", name="Bảo hành (test CMDB)", grp="hw_warranty")   # shared DB: kept between tests
    ven = SpendVendor(name="Dell CMDB", name_norm="dell cmdb")
    db.add_all([cat, ven])
    db.flush()
    op = OpexItem(code="OPX-26-001", fiscal_year=2026, category_id=cat.id, title="Bảo hành R750", vendor_id=ven.id,
                  start_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 12, 31), billing_cycle="yearly", amount_net=1,
                  amount_gross=1, owner_name="A", status="active", created_by="test")
    db.add(op)
    db.flush()
    db.add(OpexAsset(opex_id=op.id, serial="7XK2-Q93"))
    db.commit()


def _dump(db):
    cis = sorted((c.id, c.type, c.source_kind, c.source_key, c.name, c.status, c.attrs, c.first_seen, c.last_seen)
                 for c in db.query(Ci))
    keys = sorted((k.id, k.ci_id, k.kind, k.value_norm) for k in db.query(CiKey))
    links = sorted((x.id, x.from_ci, x.to_ci, x.relation, x.source, x.confidence, x.attrs, x.first_seen, x.last_seen)
                   for x in db.query(CiLink))
    return cis, keys, links


def _ci(db, type_, name):
    return db.query(Ci).filter(Ci.type == type_, Ci.name == name).one()


def _links(db):
    names = {c.id: (c.type, c.name) for c in db.query(Ci)}
    return {(names[x.from_ci], x.relation, names[x.to_ci], x.confidence) for x in db.query(CiLink)}


def test_build_fixed_inventory(db, monkeypatch):
    _settings(monkeypatch)
    _seed(db)
    out = builder.build_all(db, now=T0)
    counts = {}
    for c in db.query(Ci):
        counts[c.type] = counts.get(c.type, 0) + 1
    assert counts == {"vm": 3, "hypervisor_host": 2, "cluster": 1, "physical_server": 1, "datastore": 1,
                      "storage_array": 1, "storage_volume": 1, "backup_client": 1, "dc_asset": 1, "opex_item": 1}
    assert out["cis"] == 13 and out["links"] == 14 and out["retired"] == 0
    H1, H2 = ("hypervisor_host", "esx01.corp.local"), ("hypervisor_host", "esx02.corp.local")
    PS, DS = ("physical_server", "7XK2Q93"), ("datastore", "DS1")
    assert _links(db) == {
        (("vm", "vm-a"), "hosted_on", H1, "strong"), (("vm", "vm-b"), "hosted_on", H1, "strong"),
        (("vm", "vm-c"), "hosted_on", H2, "strong"),
        (H1, "member_of", ("cluster", "PRD"), "strong"), (H2, "member_of", ("cluster", "PRD"), "strong"),
        (H1, "runs_on", PS, "strong"),
        (("vm", "vm-a"), "stored_on", DS, "strong"), (("vm", "vm-b"), "stored_on", DS, "strong"),
        (("vm", "vm-c"), "stored_on", DS, "strong"),
        (DS, "backed_by", ("storage_volume", "vol-ds1"), "strong"),
        (("storage_volume", "vol-ds1"), "on", ("storage_array", "FA-01"), "strong"),
        (("vm", "vm-a"), "protected_by", ("backup_client", "VM-A.corp.local"), "weak"),
        (("dc_asset", "esx01"), "records", PS, "strong"),
        (("opex_item", "Bảo hành R750"), "covers", PS, "strong"),
    }
    ps = _ci(db, "physical_server", "7XK2Q93")
    assert json.loads(ps.attrs) == {"origins": ["dc_asset", "host", "opex"], "vendor": "Dell Inc.", "model": "PowerEdge R750"}
    keys = lambda c: {(k.kind, k.value_norm) for k in db.query(CiKey).filter(CiKey.ci_id == c.id)}  # noqa: E731
    assert keys(_ci(db, "hypervisor_host", "esx01.corp.local")) == {
        ("fqdn", "esx01.corp.local"), ("short_name", "esx01"), ("serial", "7XK2Q93"), ("hw_uuid", "4c4c4544-0058-4b10")}
    assert keys(_ci(db, "vm", "vm-a")) == {("instance_uuid", "u-1"), ("short_name", "vm-a"), ("fqdn", "vm-a.corp.local"),
                                           ("ip", "10.0.0.1")}
    assert keys(_ci(db, "datastore", "DS1")) == {("naa", "624a9370abc0001")} == keys(_ci(db, "storage_volume", "vol-ds1"))
    assert keys(_ci(db, "dc_asset", "esx01")) == {("serial", "7XK2Q93"), ("asset_tag", "HSC001")}
    assert keys(_ci(db, "opex_item", "Bảo hành R750")) == {("opex_code", "OPX-26-001")}
    pb = db.query(CiLink).filter(CiLink.relation == "protected_by").one()
    assert json.loads(pb.attrs) == {"matchedBy": "vm_name"}


def test_build_twice_changes_nothing(db, monkeypatch):
    _settings(monkeypatch)
    _seed(db)
    builder.build_all(db, now=T0)
    before = _dump(db)
    again = builder.build_all(db, now=T0 + dt.timedelta(minutes=15))
    assert _dump(db) == before
    assert (again["ciInserted"], again["ciUpdated"], again["keysInserted"], again["keysDeleted"],
            again["linksInserted"], again["linksDeleted"]) == (0, 0, 0, 0, 0, 0)


def test_vm_gone_for_8_days_is_retired_not_deleted(db, monkeypatch):
    _settings(monkeypatch)
    _seed(db)
    builder.build_all(db, now=T0)
    later = T0 + dt.timedelta(days=8)
    _run(db, later, VMS[:2])                                    # vm-c deleted in vCenter
    out = builder.build_all(db, now=later)
    vmc = _ci(db, "vm", "vm-c")
    assert out["retired"] == 1 and vmc.status == "retired" and vmc.last_seen == T0
    assert _ci(db, "vm", "vm-a").status == "active" and _ci(db, "vm", "vm-a").last_seen == later
    assert {(k.kind, k.value_norm) for k in db.query(CiKey).filter(CiKey.ci_id == vmc.id)} >= {("instance_uuid", "u-3")}
    assert not db.query(CiLink).filter((CiLink.from_ci == vmc.id) | (CiLink.to_ci == vmc.id)).count()
    # Back in the inventory → active again, same id.
    back = later + dt.timedelta(hours=2)
    _run(db, back, VMS)
    builder.build_all(db, now=back)
    assert _ci(db, "vm", "vm-c").id == vmc.id and _ci(db, "vm", "vm-c").status == "active"


def test_source_down_longer_than_retire_window_retires_its_cis(db, monkeypatch):
    _settings(monkeypatch)
    _seed(db)
    builder.build_all(db, now=T0)
    out = builder.build_all(db, now=T0 + dt.timedelta(days=8))        # no newer run: inventory is stale
    assert out["retired"] >= 7 and _ci(db, "hypervisor_host", "esx01.corp.local").status == "retired"
    assert _ci(db, "dc_asset", "esx01").status == "active"            # DC asset list is not a polled source


def test_nfs_datastore_link_is_weak_and_opex_by_host_name(db, monkeypatch):
    from app.models import DatastoreState, StorageArrayState, StorageVolumeState
    from app.spend.models import OpexAsset, OpexItem
    _settings(monkeypatch)
    _seed(db)
    db.add(DatastoreState(vcenter_key="vct", ds_key="datastore-2", name="NFS1", ds_type="NFS",
                          backing=json.dumps({"naa": [], "nfsHost": "10.9.9.9", "nfsPath": "/vol/nfs1"})))
    db.query(StorageArrayState).filter_by(source_key="pure-t").update(
        {"nfs_endpoints": json.dumps([{"svm": "svm1", "ip": "10.9.9.9"}])})
    db.add(StorageVolumeState(source_key="pure-t", name="nfs1", container="svm1", kind="volume", nfs_path="/vol/nfs1"))
    op = db.query(OpexItem).one()
    db.add(OpexAsset(opex_id=op.id, serial="", vcenter_key="vct", host_name="ESX02.corp.local"))
    db.commit()
    builder.build_all(db, now=T0)
    links = _links(db)
    assert (("datastore", "NFS1"), "backed_by", ("storage_volume", "nfs1"), "weak") in links
    assert (("opex_item", "Bảo hành R750"), "covers", ("hypervisor_host", "esx02.corp.local"), "weak") in links


# ---------------------------------------------------------------- API (shared test database)
@pytest.fixture()
def api(monkeypatch):
    from fastapi.testclient import TestClient
    from app.database import SessionLocal, ensure_schema
    from app.main import app
    ensure_schema()
    s = SessionLocal()
    for m in (CiLink, CiKey, Ci):
        s.query(m).delete()
    a = Ci(type="vm", name="api-vm", source_kind="vcenter", source_key="uuid-api", attrs="{}", first_seen=T0, last_seen=T0)
    b = Ci(type="backup_client", name="api-vm", source_kind="netbackup", source_key="nbu:api-vm", attrs="{}",
           first_seen=T0, last_seen=T0)
    h = Ci(type="hypervisor_host", name="api-esx", source_kind="vcenter", source_key="vc:api-esx", attrs="{}",
           first_seen=T0, last_seen=T0)
    o = Ci(type="opex_item", name="api-opex", source_kind="opex", source_key="OPX-API", attrs="{}", first_seen=T0, last_seen=T0)
    s.add_all([a, b, h, o])
    s.flush()
    s.add_all([CiKey(ci_id=a.id, kind="ip", value_norm="10.7.7.7"), CiKey(ci_id=h.id, kind="serial", value_norm="ABC1234"),
               CiLink(from_ci=a.id, to_ci=b.id, relation="protected_by", source="netbackup", confidence="weak",
                      attrs='{"matchedBy": "vm_name"}', first_seen=T0, last_seen=T0),
               CiLink(from_ci=a.id, to_ci=h.id, relation="hosted_on", source="vcenter", confidence="strong", attrs="{}",
                      first_seen=T0, last_seen=T0),
               CiLink(from_ci=o.id, to_ci=h.id, relation="covers", source="opex", confidence="weak", attrs="{}",
                      first_seen=T0, last_seen=T0)])
    s.commit()
    ids = {"vm": a.id, "client": b.id, "host": h.id, "opex": o.id}
    s.close()
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"username": "admin", "password": "Admin12345!"}).status_code == 200
        yield c, ids
    s = SessionLocal()
    for m in (CiLink, CiKey, Ci):
        s.query(m).delete()
    s.commit()
    s.close()


def test_api_list_detail_stats_and_feature_filter(api):
    c, ids = api
    assert c.get("/api/ci?q=10.7.7").json()["items"][0]["id"] == ids["vm"]           # search by key
    assert [i["type"] for i in c.get("/api/ci?q=api-vm").json()["items"]] == ["backup_client", "vm"]
    assert c.get("/api/ci?type=bogus").status_code == 422
    d = c.get(f"/api/ci/{ids['vm']}").json()
    assert d["keys"] == [{"kind": "ip", "value": "10.7.7.7"}]
    assert {(x["relation"], x["direction"], x["peer"]["name"], x["confidence"]) for x in d["links"]} == {
        ("protected_by", "out", "api-vm", "weak"), ("hosted_on", "out", "api-esx", "strong")}
    host = c.get(f"/api/ci/{ids['host']}").json()
    assert {(x["relation"], x["direction"]) for x in host["links"]} == {("hosted_on", "in"), ("covers", "in")}
    st = c.get("/api/ci/stats").json()
    assert (st["hosts"], st["hostsWithSerial"], st["hostSerialPct"], st["weakLinks"]) == (1, 1, 100.0, 2)
    assert c.get("/api/ci/999999").status_code == 404
    # Backup feature off: clients disappear everywhere, their id answers 404.
    assert c.put("/api/settings/features", json={"features": {"backup": False}}).status_code == 200
    try:
        assert [i["type"] for i in c.get("/api/ci?q=api-vm").json()["items"]] == ["vm"]
        assert c.get(f"/api/ci/{ids['client']}").status_code == 404
        assert [x["relation"] for x in c.get(f"/api/ci/{ids['vm']}").json()["links"]] == ["hosted_on"]
        assert "backup_client" not in c.get("/api/ci/stats").json()["byType"]
        assert c.get("/api/ci/stats").json()["weakLinks"] == 1
    finally:
        c.put("/api/settings/features", json={"features": {"backup": True}})


def test_api_needs_login_and_opex_needs_spend_role(app_, api):
    from fastapi.testclient import TestClient
    c, ids = api
    assert TestClient(app_).get("/api/ci").status_code == 401
    nobody = _login(app_, "nobody1")                     # signed in, no spend role
    assert nobody.get(f"/api/ci/{ids['opex']}").status_code == 404
    assert "opex_item" not in nobody.get("/api/ci/stats").json()["byType"]
    assert c.get(f"/api/ci/{ids['opex']}").status_code == 200             # admin


# ---------------------------------------------------------------- build time on a large demo
def test_demo_build_under_5_seconds_for_3000_vms(db, monkeypatch):
    from app.collectors import scheduler
    from app.config import VCenterConfig, get_settings
    keys = [f"dv{i:02d}" for i in range(30)]
    settings = get_settings()
    for k in keys:
        run = scheduler.sync_vcenter(db, VCenterConfig(k, k, "demo", 443, "u", "p", False, demo=True), settings)
        assert run.status == "ok"
    _settings(monkeypatch, vcs=keys, nbus=(), arrays=())
    started = time.monotonic()
    out = builder.build_all(db)
    took = time.monotonic() - started
    vms = db.query(Ci).filter(Ci.type == "vm").count()
    print(f"CMDB build: {vms} VMs, {out['cis']} CIs, {out['links']} links in {took:.2f}s")
    assert vms >= 2500 and out["cis"] > vms
    assert took < 5.0, f"build took {took:.2f}s for {vms} VMs"
    started = time.monotonic()
    again = builder.build_all(db)
    assert time.monotonic() - started < 5.0 and again["ciInserted"] == 0 and again["linksInserted"] == 0


def test_scheduler_build_failure_does_not_stop_the_round(monkeypatch):
    from app.collectors import scheduler

    def boom(db):
        raise RuntimeError("x")
    monkeypatch.setattr(builder, "build_all", boom)
    assert scheduler._build_cmdb() is None
