"""ICO v2 G1c: impact analysis, entity cards (host / VM / server / datastore), search and Hỏi ICO tools from the CI registry."""
import datetime as dt

import pytest

from app.cmdb import builder, impact
from app.cmdb.models import Ci, CiKey, CiLink
from test_cmdb import _ci, _seed, _settings, db  # noqa: F401  (fixture)
from test_spend_opex import _login, admin, app_  # noqa: F401  (fixtures)

T0 = dt.datetime(2026, 11, 1, 8, 0, 0)


def _names(res):
    return sorted((i["type"], i["name"], i["level"]) for i in res["items"])


# ---------------------------------------------------------------- impact on the G1b sample (private DB)
def test_downstream_from_array_reaches_volume_datastore_vms_and_backup(db, monkeypatch):  # noqa: F811
    _settings(monkeypatch)
    _seed(db)
    builder.build_all(db, now=T0)
    r = impact.walk(db, _ci(db, "storage_array", "FA-01").id, "down")
    assert r["counts"] == {"storage_volume": 1, "datastore": 1, "vm": 3, "backup_client": 1}
    assert _names(r) == [("backup_client", "VM-A.corp.local", 4), ("datastore", "DS1", 2), ("storage_volume", "vol-ds1", 1),
                         ("vm", "vm-a", 3), ("vm", "vm-b", 3), ("vm", "vm-c", 3)]
    assert (r["total"], r["weak"], r["truncated"]) == (6, 1, False)
    # depth 3 stops at the VMs
    assert impact.walk(db, _ci(db, "storage_array", "FA-01").id, "down", depth=3)["counts"] == \
        {"storage_volume": 1, "datastore": 1, "vm": 3}
    # A physical server: its host, the 2 VMs on it, the backup client of vm-a
    ps = impact.walk(db, _ci(db, "physical_server", "7XK2Q93").id, "down")
    assert ps["counts"] == {"hypervisor_host": 1, "vm": 2, "backup_client": 1}
    # The cluster carries both hosts and every VM
    assert impact.walk(db, _ci(db, "cluster", "PRD").id, "down")["counts"] == {"hypervisor_host": 2, "vm": 3, "backup_client": 1}
    # Hidden types are left out of the result
    assert "backup_client" not in impact.walk(db, _ci(db, "storage_array", "FA-01").id, "down", hidden={"backup_client"})["counts"]


def test_upstream_from_vm(db, monkeypatch):  # noqa: F811
    _settings(monkeypatch)
    _seed(db)
    builder.build_all(db, now=T0)
    r = impact.walk(db, _ci(db, "vm", "vm-a").id, "up")
    assert _names(r) == [("cluster", "PRD", 2), ("datastore", "DS1", 1), ("hypervisor_host", "esx01.corp.local", 1),
                         ("physical_server", "7XK2Q93", 2), ("storage_array", "FA-01", 3), ("storage_volume", "vol-ds1", 2)]
    # Opex / DC asset are documentation, never in the impact
    assert "opex_item" not in impact.walk(db, _ci(db, "physical_server", "7XK2Q93").id, "up")["counts"]


def test_walk_is_cycle_safe_and_bounded(db, monkeypatch):  # noqa: F811
    a = Ci(type="hypervisor_host", name="a", source_kind="vcenter", source_key="x:a", attrs="{}", first_seen=T0, last_seen=T0)
    b = Ci(type="hypervisor_host", name="b", source_kind="vcenter", source_key="x:b", attrs="{}", first_seen=T0, last_seen=T0)
    db.add_all([a, b])
    db.flush()
    db.add_all([CiLink(from_ci=a.id, to_ci=b.id, relation="hosted_on", source="t", confidence="strong", attrs="{}",
                       first_seen=T0, last_seen=T0),
                CiLink(from_ci=b.id, to_ci=a.id, relation="hosted_on", source="t", confidence="strong", attrs="{}",
                       first_seen=T0, last_seen=T0)])
    db.commit()
    r = impact.walk(db, a.id, "down")
    assert [(i["name"], i["level"]) for i in r["items"]] == [("b", 1)]          # a is not visited twice
    hosts = [Ci(type="vm", name=f"v{i}", source_kind="vcenter", source_key=f"v{i}", attrs="{}", first_seen=T0, last_seen=T0)
             for i in range(30)]
    db.add_all(hosts)
    db.flush()
    db.add_all([CiLink(from_ci=h.id, to_ci=a.id, relation="hosted_on", source="t", confidence="strong", attrs="{}",
                       first_seen=T0, last_seen=T0) for h in hosts])
    db.commit()
    monkeypatch.setattr(impact, "MAX_NODES", 10)
    r = impact.walk(db, a.id, "down")
    assert r["truncated"] and r["total"] <= 10


# ---------------------------------------------------------------- API on the shared test database
@pytest.fixture()
def shared(app_, monkeypatch):  # noqa: F811
    """G1b sample in the shared database (spend users from app_), built with a fixed source list, wiped after."""
    from app.collectors.retention import CHILD_MODELS
    from app.database import SessionLocal, ensure_schema
    from app.models import BackupJob, DatastoreState, DcAsset, SnapshotRun, StorageArrayState, StorageVolumeState
    from app.services import entities
    ensure_schema()
    _settings(monkeypatch, nbus=("nbu-test",))
    s = SessionLocal()
    for m in (CiLink, CiKey, Ci):
        s.query(m).delete()
    s.commit()
    _seed(s)
    s.query(BackupJob).filter(BackupJob.client_key == "vm-a").update(
        {"nbu_key": "nbu-test", "start_time": dt.datetime.utcnow() - dt.timedelta(hours=6),
         "end_time": dt.datetime.utcnow() - dt.timedelta(hours=5), "category": "backup", "state": "DONE"})
    s.commit()
    builder.build_all(s, now=T0)
    entities._cache.clear()
    ids = {t: _ci(s, *t).id for t in (("datastore", "DS1"), ("vm", "vm-a"), ("storage_array", "FA-01"),
                                       ("backup_client", "VM-A.corp.local"), ("hypervisor_host", "esx02.corp.local"))}
    s.close()
    yield ids
    s = SessionLocal()
    for m in (CiLink, CiKey, Ci):
        s.query(m).delete()
    runs = [r.id for r in s.query(SnapshotRun.id).filter(SnapshotRun.vcenter_key == "vct")]
    for model in CHILD_MODELS:
        s.query(model).filter(model.run_id.in_(runs)).delete(synchronize_session=False)
    s.query(SnapshotRun).filter(SnapshotRun.id.in_(runs)).delete(synchronize_session=False)
    for m, f in ((DatastoreState, DatastoreState.vcenter_key == "vct"), (StorageVolumeState, StorageVolumeState.source_key == "pure-t"),
                 (StorageArrayState, StorageArrayState.source_key == "pure-t"), (DcAsset, DcAsset.asset_key == "tag:HSC-001"),
                 (BackupJob, BackupJob.client_key == "vm-a")):
        s.query(m).filter(f).delete(synchronize_session=False)
    s.commit()
    s.close()
    entities._cache.clear()


def test_impact_endpoint_directions_and_feature_gate(app_, admin, shared):  # noqa: F811
    r = admin.get(f"/api/ci/{shared[('storage_array', 'FA-01')]}/impact?direction=down").json()
    assert r["root"]["name"] == "FA-01" and r["counts"] == {"storage_volume": 1, "datastore": 1, "vm": 3, "backup_client": 1}
    up = admin.get(f"/api/ci/{shared[('vm', 'vm-a')]}/impact?direction=up").json()
    assert up["counts"]["storage_array"] == 1 and up["direction"] == "up"
    assert admin.get(f"/api/ci/{shared[('vm', 'vm-a')]}/impact?direction=sideways").status_code == 422
    assert admin.get("/api/ci/999999/impact").status_code == 404
    assert admin.put("/api/settings/features", json={"features": {"backup": False}}).status_code == 200
    try:
        assert admin.get(f"/api/ci/{shared[('backup_client', 'VM-A.corp.local')]}/impact").status_code == 404
        assert "backup_client" not in admin.get(f"/api/ci/{shared[('storage_array', 'FA-01')]}/impact").json()["counts"]
    finally:
        admin.put("/api/settings/features", json={"features": {"backup": True}})


def test_datastore_card_opens_array_vms_and_backup(app_, admin, shared):  # noqa: F811
    d = admin.get(f"/api/entities/datastore/{shared[('datastore', 'DS1')]}").json()
    assert (d["name"], d["vcenterKey"], d["dsType"]) == ("DS1", "vct", "VMFS")
    assert (d["storage"]["array"], d["storage"]["volume"], d["storage"]["confidence"], d["storage"]["matchedBy"]) == \
        ("FA-01", "vol-ds1", "strong", "naa")
    assert [v["name"] for v in d["vms"]] == ["vm-a", "vm-b", "vm-c"]
    assert d["ci"]["impact"]["counts"] == {"vm": 3, "backup_client": 1}
    rels = {(l["relation"], l["direction"], l["peer"]["name"]) for l in d["ci"]["links"]}
    assert ("backed_by", "out", "vol-ds1") in rels and ("stored_on", "in", "vm-a") in rels
    assert admin.get(f"/api/entities/datastore/{shared[('vm', 'vm-a')]}").status_code == 404           # not a datastore
    assert admin.get("/api/entities/datastore/999999").status_code == 404


def test_server_card_dc_asset_opex_by_role(app_, admin, shared):  # noqa: F811
    d = admin.get("/api/entities/server/7xk2-q93").json()                     # any spelling of the serial
    assert (d["serial"], d["vendor"], d["model"]) == ("7XK2Q93", "Dell Inc.", "PowerEdge R750")
    assert d["origins"] == ["dc_asset", "host", "opex"]
    assert [h["name"] for h in d["hosts"]] == ["esx01.corp.local"]
    assert [(a["deviceId"], a["assetTag"]) for a in d["dcAssets"]] == [("esx01", "hsc-001")]
    assert [(o["code"], o["matchedBy"]) for o in d["opex"]] == [("OPX-26-001", "serial")]
    assert d["ci"]["impact"]["counts"] == {"hypervisor_host": 1, "vm": 2, "backup_client": 1}
    nobody = _login(app_, "nobody1")
    n = nobody.get("/api/entities/server/7XK2Q93").json()
    assert n["opex"] is None and all(l["peer"]["type"] != "opex_item" for l in n["ci"]["links"])
    assert admin.get("/api/entities/server/NOPE999").status_code == 404


def test_host_card_warranty_through_serial_and_name_fallback(app_, admin, shared):  # noqa: F811
    from app.database import SessionLocal
    from app.spend.models import OpexAsset, OpexItem
    h = admin.get("/api/entities/host?name=esx01.corp.local&vcenter=vct").json()
    assert h["hardware"]["serial"] == "7XK2Q93" and h["server"]["serial"] == "7XK2Q93"
    assert [(x["code"], x["matchedBy"]) for x in h["devices"]] == [("OPX-26-001", "serial")]
    assert h["ci"]["impact"]["counts"] == {"vm": 2, "backup_client": 1}
    # esx02 reports no serial: the Opex line declared by host name is shown, labelled as a name match
    s = SessionLocal()
    op = s.query(OpexItem).filter(OpexItem.code == "OPX-26-001").one()
    s.add(OpexAsset(opex_id=op.id, serial="", vcenter_key="vct", host_name="esx02.corp.local"))
    s.commit()
    s.close()
    h2 = admin.get("/api/entities/host?name=esx02.corp.local&vcenter=vct").json()
    assert h2["server"] is None and [(x["code"], x["matchedBy"]) for x in h2["devices"]] == [("OPX-26-001", "name")]


def test_vm_card_backup_through_protected_by(app_, admin, shared):  # noqa: F811
    v = admin.get("/api/entities/vm?name=vm-a&vcenter=vct").json()
    assert v["ci"]["id"] == shared[("vm", "vm-a")]
    assert v["backup"]["client"]["client"].lower() == "vm-a.corp.local" and v["backup"]["matchedBy"] == "vm_name"
    assert {(l["relation"], l["peer"]["name"]) for l in v["ci"]["links"]} >= {("hosted_on", "esx01.corp.local"),
                                                                             ("stored_on", "DS1")}


def test_search_by_serial_asset_tag_and_datastore(app_, admin, shared):  # noqa: F811
    by_serial = [i for i in admin.get("/api/search?q=7xk2").json()["items"] if i["type"] == "server"]
    assert [i["entity"] for i in by_serial] == [{"type": "server", "name": "7XK2Q93", "vcenterKey": ""}]
    by_tag = [i for i in admin.get("/api/search?q=HSC-001").json()["items"] if i["type"] == "server"]
    assert [i["label"] for i in by_tag] == ["7XK2Q93"]
    ds = [i for i in admin.get("/api/search?q=DS1").json()["items"] if i["type"] == "datastore"]
    assert ds and ds[0]["entity"]["id"] == shared[("datastore", "DS1")]


def test_ask_ico_ci_tools(app_, admin, shared):  # noqa: F811
    from app.ask_ico import Toolbox
    from app.database import SessionLocal
    from app.models import User
    s = SessionLocal()
    try:
        box = Toolbox(s, s.query(User).filter(User.username == "admin").one())
        found = box.run("ci_lookup", {"q": "FA-01"})
        arr = next(c for c in found["ci"] if c["loai"] == "storage_array")
        imp = box.run("ci_impact", {"id": arr["id"], "direction": "down"})
        assert imp["theo_loai"] == {"storage_volume": 1, "datastore": 1, "vm": 3, "backup_client": 1} and imp["ghep_theo_ten"] == 1
        assert ("vm", "vm-a") in box.seen                                           # answer links for the VMs
        assert box.run("ci_impact", {"id": 999999}) == {"loi": "Không có CI này."}
        with pytest.raises(ValueError):
            box.run("ci_impact", {"id": "1"})
    finally:
        s.close()
