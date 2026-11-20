"""Giai đoạn 1: datastore capacity + days to full, long-lived snapshots, vCenter alarms, host health,
inbox items and the operations alert rules (Cài đặt → Cảnh báo)."""
import datetime as dt
import json
from types import SimpleNamespace as NS

import pytest

from app import alert_rules, inbox, infra, metrics, ops_alerts, secrets_box
from app.database import SessionLocal
from app.models import AppSetting, DatastoreState, HostHealthState, MetricPoint, MetricRollup, VcAlarmState, VmSnapshotState
from app.spend.models import SpendNotifyChannel
from app.vcenter import extras
from test_spend_opex import _login, admin, app_, editor  # noqa: F401  (fixtures)

NOW = dt.datetime(2026, 11, 17, 3, 0)          # 10:00 Vietnam time
ISO = lambda t: t.isoformat() + "Z"           # noqa: E731


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    """The inbox calls infra.snapshots / datastores without `now` (wall clock): pin them to NOW so ages measured
    against the fixed test data do not change with the day the suite runs."""
    for name in ("snapshots", "datastores"):
        real = getattr(infra, name)
        monkeypatch.setattr(infra, name, lambda db, scope=None, now=None, _real=real: _real(db, scope, now or NOW))


@pytest.fixture()
def db(app_):
    s = SessionLocal()
    for m in (DatastoreState, VmSnapshotState, VcAlarmState, HostHealthState, MetricPoint, MetricRollup):
        s.query(m).delete()
    s.query(AppSetting).filter(AppSetting.key.like("alerts.%")).delete(synchronize_session=False)
    s.commit()
    inbox.invalidate()
    yield s
    for m in (DatastoreState, VmSnapshotState, VcAlarmState, HostHealthState, MetricPoint, MetricRollup):
        s.query(m).delete()
    s.query(AppSetting).filter(AppSetting.key.like("alerts.%")).delete(synchronize_session=False)
    s.query(SpendNotifyChannel).delete()
    s.commit()
    inbox.invalidate()
    s.close()


def _extras(used_gb=900.0, cap=1000.0, snap_age_days=8, alarms=None, health=None):
    return {
        "datastores": [
            {"key": "datastore-1", "name": "DS-PROD-01", "type": "VMFS", "capacityGB": cap, "freeGB": cap - used_gb, "uncommittedGB": 700,
             "accessible": True, "maintenance": "normal", "clusters": ["PROD"], "hostCount": 4, "vmCount": 30},
            {"key": "datastore-2", "name": "DS-DEV-01", "type": "NFS", "capacityGB": 2000, "freeGB": 1500, "uncommittedGB": 0,
             "accessible": True, "maintenance": "normal", "clusters": ["DEV"], "hostCount": 2, "vmCount": 5}],
        "snapshots": [
            {"vm": "app-01", "instanceUuid": "u1", "cluster": "PROD", "host": "esx1", "count": 1, "sizeGB": 40,
             "oldestAt": ISO(NOW - dt.timedelta(days=snap_age_days)), "newestAt": ISO(NOW - dt.timedelta(days=snap_age_days)),
             "items": [{"name": "truoc-va", "createdAt": ISO(NOW - dt.timedelta(days=snap_age_days)), "description": "", "quiesced": False, "depth": 0}]},
            {"vm": "db-01", "instanceUuid": "u2", "cluster": "PROD", "host": "esx2", "count": 1, "sizeGB": 2,
             "oldestAt": ISO(NOW - dt.timedelta(minutes=30)), "newestAt": ISO(NOW - dt.timedelta(minutes=30)),
             "items": [{"name": "NBU_SNAPSHOT db-01", "createdAt": ISO(NOW - dt.timedelta(minutes=30)), "description": "NetBackup", "quiesced": True, "depth": 0}]}],
        "alarms": alarms if alarms is not None else [
            {"key": "a1", "entityType": "datastore", "entity": "DS-PROD-01", "cluster": "PROD", "alarm": "Datastore usage on disk",
             "status": "red", "at": ISO(NOW - dt.timedelta(hours=3)), "acknowledged": False},
            {"key": "a2", "entityType": "vm", "entity": "app-01", "cluster": "PROD", "alarm": "VM CPU usage",
             "status": "yellow", "at": ISO(NOW), "acknowledged": True}],
        "health": health if health is not None else [
            {"host": "esx1", "cluster": "PROD", "overallStatus": "red", "connection": "connected", "maintenance": False,
             "issues": [{"kind": "power", "name": "Power Supply 2: Failure", "state": "red", "reading": None}]},
            {"host": "esx2", "cluster": "PROD", "overallStatus": "green", "connection": "connected", "maintenance": False, "issues": []},
            {"host": "esx3", "cluster": "DEV", "overallStatus": "yellow", "connection": "connected", "maintenance": True,
             "issues": [{"kind": "fan", "name": "Fan 4", "state": "yellow", "reading": 1680}]}],
    }


# ---------------------------------------------------------------- rules with concrete numbers
def test_datastore_levels():
    assert infra.datastore_level(96.2, True, None, 100) == ("crit", ["Đã dùng 96,2% (≥ 95%)"])
    assert infra.datastore_level(91.0, True, 45, 100)[0] == "bad"
    assert infra.datastore_level(85.0, True, 20, 100) == ("bad", ["Đã dùng 85,0% (≥ 80%)", "Dự báo đầy sau 20 ngày (≤ 30)"])
    assert infra.datastore_level(60.0, True, 5, 100)[0] == "crit"                  # 60 % but full within a week
    assert infra.datastore_level(50.0, True, None, 160) == ("warn", ["Cấp phát thin 160% dung lượng (≥ 150%)"])
    assert infra.datastore_level(10.0, False, None, None)[0] == "crit"             # inaccessible
    assert infra.datastore_level(40.0, True, None, 90) == ("ok", [])


def test_snapshot_levels():
    assert infra.snapshot_level(8, 10, 1)[0] == "crit"
    assert infra.snapshot_level(4, 10, 1) == ("bad", ["Snapshot cũ nhất 4 ngày (≥ 3)"])
    assert infra.snapshot_level(1.5, 10, 1) == ("warn", ["Snapshot tồn 36 giờ"])
    assert infra.snapshot_level(0.02, 2, 1) == ("ok", [])                          # backup snapshot, 30 minutes
    assert infra.snapshot_level(0.5, 250, 1)[0] == "crit"                          # 250 GB of delta
    assert infra.snapshot_level(0.5, 10, 3)[0] == "bad"                            # chain of 3


# ---------------------------------------------------------------- store + read
def test_store_forecast_and_levels(db):
    # 10 days of history: +5 GB/day, today 900 / 1000 GB → full in ~20 days
    for d in range(10):
        metrics.record(db, "datastore", [("vc1:datastore-1", "usedGB", 855 + 5 * d)], NOW - dt.timedelta(days=9 - d))
    infra.store(db, "vc1", _extras(), NOW)
    db.commit()
    ds = {d["name"]: d for d in infra.datastores(db, now=NOW)}
    p = ds["DS-PROD-01"]
    assert (p["usedPct"], p["provisionedPct"], p["growthGBPerDay"]) == (90.0, 160.0, 5.0)
    assert round(p["daysToFull"]) == 20 and p["fullDate"] == "2026-12-07"
    assert p["level"] == "bad" and p["reasons"][:2] == ["Đã dùng 90,0% (≥ 90%)", "Dự báo đầy sau 20 ngày (≤ 30)"]
    assert ds["DS-DEV-01"]["level"] == "ok" and ds["DS-DEV-01"]["daysToFull"] is None
    sn = {s["vm"]: s for s in infra.snapshots(db, now=NOW)}
    assert sn["app-01"]["level"] == "crit" and sn["app-01"]["ageDays"] == 8.0
    assert sn["db-01"]["level"] == "ok" and sn["db-01"]["backupLike"] is True
    al = infra.alarms(db)
    assert [(a["alarm"], a["level"]) for a in al] == [("Datastore usage on disk", "crit"), ("VM CPU usage", "bad")]
    hh = {h["host"]: h for h in infra.host_health(db)}
    assert hh["esx1"]["level"] == "crit" and hh["esx1"]["reasons"] == ["Power Supply 2: Failure"]
    assert hh["esx2"]["level"] == "ok"
    # history recorded for the chart / next forecast
    assert db.query(MetricPoint).filter_by(entity="vc1:datastore-1", metric="usedGB").count() == 11
    # scope filter
    from app.scope import Scope
    assert [d["name"] for d in infra.datastores(db, Scope.from_query("all", "DEV"), now=NOW)] == ["DS-DEV-01"]
    s = infra.summary(db)
    assert s["datastores"]["bad"] == 1 and s["alarms"]["red"] == 1 and s["health"]["issues"] == 2


def test_failed_extras_keep_previous_state(db):
    infra.store(db, "vc1", _extras(), NOW)
    db.commit()
    assert infra.store(db, "vc1", {"datastores": [], "snapshots": [], "alarms": [], "health": [], "error": True}, NOW) == {}
    db.commit()
    assert db.query(VcAlarmState).count() == 2 and db.query(DatastoreState).count() == 2
    infra.store(db, "vc1", _extras(alarms=[]), NOW)            # a real "no alarms" clears them
    db.commit()
    assert db.query(VcAlarmState).count() == 0


# ---------------------------------------------------------------- inbox + alert rules
def test_inbox_items_and_default_rules(db):
    infra.store(db, "vc1", _extras(used_gb=960), NOW)           # 96 % → VƯỢT NGƯỠNG
    db.commit()
    items = [i for i in inbox._compute(db, NOW.date())[0] if i["source"] in ("datastore", "snapshot", "alarm", "health")]
    by = {(i["source"], i["level"]) for i in items}
    assert ("datastore", "crit") in by and ("snapshot", "crit") in by and ("alarm", "crit") in by and ("health", "crit") in by
    assert not any(i["source"] == "alarm" and "CPU" in i["title"] for i in items)          # acknowledged: not in the inbox
    assert not any(i["source"] == "health" and "esx3" in i["title"] for i in items)        # maintenance mode: not in the inbox
    assert not any(i["source"] == "snapshot" and "db-01" in i["title"] for i in items)     # backup snapshot, 30 min
    rules = alert_rules.rules(db)
    assert rules["datastore"].min_level == "bad" and rules["snapshot"].min_level == "crit"
    snap_bad = {"source": "snapshot", "level": "bad", "push": False}
    assert rules["datastore"].wants({"source": "datastore", "level": "bad"}) and not rules["snapshot"].wants(snap_bad)
    assert rules["capacity"].wants({"source": "capacity", "level": "warn", "push": True})  # 90 % decision kept


def test_inbox_drill_down_alarm_health_snapshot(db):
    """Each infra item carries a deep link to its object, key facts and the concrete events behind it."""
    alarms = _extras()["alarms"] + [
        {"key": "a3", "entityType": "host", "entity": "esx1", "cluster": "PROD", "alarm": "Host hardware power status",
         "status": "red", "at": ISO(NOW - dt.timedelta(hours=1)), "acknowledged": False},
        {"key": "a4", "entityType": "host", "entity": "esx1", "cluster": "PROD", "alarm": "Host connection failure",
         "status": "yellow", "at": ISO(NOW - dt.timedelta(minutes=50)), "acknowledged": False}]
    infra.store(db, "vc1", _extras(alarms=alarms), NOW)
    db.commit()
    items = {(i["source"], i["title"]): i for i in inbox._compute(db, NOW.date())[0]}
    ds_alarm = items[("alarm", "Alarm Datastore usage on disk — DS-PROD-01")]
    assert ds_alarm["page"] == "infra-alarm" and ds_alarm["query"] == "alarm=vc1%7Ca1" and ds_alarm["pageLabel"] == "Xem alarm này"
    assert {f["label"]: f["value"] for f in ds_alarm["facts"]} == {
        "Alarm": "Datastore usage on disk", "Trạng thái trên vCenter": "Đỏ", "Đối tượng": "Datastore DS-PROD-01",
        "Cluster": "PROD", "vCenter": "vc1", "Kích hoạt lúc": ISO(NOW - dt.timedelta(hours=3))}
    assert [l["label"] for l in ds_alarm["links"]] == ["Datastore DS-PROD-01"]
    # host alarm: both alarms of esx1 + its failed power sensor, card + health page links
    host_alarm = items[("alarm", "Alarm Host hardware power status — esx1")]
    assert [e["title"] for e in host_alarm["events"]] == [
        "Alarm Host hardware power status", "Alarm Host connection failure", "Cảm biến Power Supply 2: Failure"]
    assert [e["level"] for e in host_alarm["events"]] == ["crit", "bad", "crit"]
    assert host_alarm["events"][1]["query"] == "alarm=vc1%7Ca4"
    assert host_alarm["links"][0] == {"label": "Thẻ host esx1", "entity": {"type": "host", "name": "esx1", "vc": "vc1"}}
    assert host_alarm["links"][1]["page"] == "infra-health" and host_alarm["links"][1]["query"] == "host=esx1"
    health = items[("health", "Host esx1: phần cứng vượt ngưỡng")]
    assert health["query"] == "host=esx1" and [e["title"] for e in health["events"]] == ["Power Supply 2: Failure"]
    assert {f["label"]: f["value"] for f in health["facts"]}["Trạng thái tổng"] == "red"
    snap = next(i for (src, _), i in items.items() if src == "snapshot")
    assert snap["query"] == "vm=app-01" and [e["title"] for e in snap["events"]] == ["Snapshot truoc-va"]
    assert snap["events"][0]["at"] == ISO(NOW - dt.timedelta(days=8))
    ds = next(i for (src, _), i in items.items() if src == "datastore")
    assert ds["query"] == "ds=vc1%7Cdatastore-1" and [e["title"] for e in ds["events"]][0] == "Đã dùng 90,0% (≥ 90%)"


def _channel(db, name):
    ch = SpendNotifyChannel(kind="discord", name=name, enabled=True, ops_alerts=True, created_by="t",
                            webhook_enc=secrets_box.encrypt("https://discord.com/api/webhooks/123456789012345678/abcDEF_-1"))
    db.add(ch)
    db.commit()
    return ch


def test_rules_channels_and_quiet_hours(db, monkeypatch):
    monkeypatch.setattr(inbox, "PROVIDERS", [("datastore", inbox.datastore_items, False), ("snapshot", inbox.snapshot_items, False)])
    infra.store(db, "vc1", _extras(used_gb=910, snap_age_days=4), NOW)      # datastore CẢNH BÁO, snapshot CẢNH BÁO
    db.commit()
    a, b = _channel(db, "ops-a"), _channel(db, "ops-b")
    # datastore only to channel B; snapshot at CẢNH BÁO and above
    alert_rules.save(db, {"rules": [{"source": "datastore", "enabled": True, "minLevel": "bad", "channels": [str(b.id)]},
                                    {"source": "snapshot", "enabled": True, "minLevel": "bad", "channels": []}]}, [str(a.id), str(b.id)])
    db.commit()
    posts = []
    r = ops_alerts.check_and_notify(db, post=lambda url, p: posts.append(p["content"]))
    assert r["new"] == 2 and r["sent"] == 2
    assert sum("Datastore" in p for p in posts) == 1 and sum("Snapshot VM" in p for p in posts) == 2    # A: snapshot; B: both
    # quiet hours now (10:00 VN in 08:00–12:00): a new CẢNH BÁO waits, nothing is sent
    db.query(DatastoreState).delete()
    db.commit()
    from app import models
    from app.ops_alerts import OpsAlertState
    db.query(OpsAlertState).delete()
    db.commit()
    alert_rules.save(db, {"quiet": {"enabled": True, "start": "08:00", "end": "12:00"}}, [])
    db.commit()
    monkeypatch.setattr(alert_rules.Quiet, "active", lambda self, now_utc=None: self.enabled)
    posts.clear()
    r = ops_alerts.check_and_notify(db, post=lambda url, p: posts.append(p["content"]))
    assert posts == [] and r["new"] == 1                         # owed, sent after the quiet hours
    monkeypatch.setattr(alert_rules.Quiet, "active", lambda self, now_utc=None: False)
    r = ops_alerts.check_and_notify(db, post=lambda url, p: posts.append(p["content"]))
    assert len(posts) == 2 and all("Snapshot VM" in p for p in posts)
    assert models  # noqa


def test_quiet_window_crossing_midnight():
    q = alert_rules.Quiet(True, "22:00", "07:00")
    assert q.active(dt.datetime(2026, 11, 17, 16, 30))           # 23:30 VN
    assert q.active(dt.datetime(2026, 11, 17, 23, 0))            # 06:00 VN
    assert not q.active(dt.datetime(2026, 11, 17, 3, 0))         # 10:00 VN
    assert not alert_rules.Quiet(False, "22:00", "07:00").active(dt.datetime(2026, 11, 17, 16, 30))


# ---------------------------------------------------------------- API
def test_api_pages_and_alert_settings(db, admin, editor):
    infra.store(db, "vc1", _extras(), NOW)
    db.commit()
    assert {d["name"] for d in editor.get("/api/infra/datastores").json()} == {"DS-PROD-01", "DS-DEV-01"}
    assert [d["name"] for d in editor.get("/api/infra/datastores", params={"clusters": "DEV"}).json()] == ["DS-DEV-01"]
    h = editor.get("/api/infra/datastores/vc1/datastore-1/history", params={"days": 7}).json()
    assert h["used"] and h["used"][-1][1] == 900.0
    assert len(editor.get("/api/infra/snapshots").json()) == 2 and len(editor.get("/api/infra/alarms").json()) == 2
    assert editor.get("/api/infra/summary").json()["datastores"]["total"] == 2
    # alert rules: admin only, validated, audited
    assert editor.get("/api/settings/alerts").status_code == 403
    cfg = admin.get("/api/settings/alerts").json()
    assert {r["source"] for r in cfg["rules"]} >= {"capacity", "datastore", "snapshot", "alarm", "health"}
    bad = admin.put("/api/settings/alerts", json={"rules": [{"source": "datastore", "enabled": True, "minLevel": "huh"}]})
    assert bad.status_code == 400
    bad = admin.put("/api/settings/alerts", json={"rules": [{"source": "datastore", "enabled": True, "minLevel": "bad", "channels": ["999"]}]})
    assert bad.status_code == 400 and "kênh" in bad.json()["detail"]
    assert admin.put("/api/settings/alerts", json={"quiet": {"enabled": True, "start": "25:00", "end": "07:00"}}).status_code == 400
    ok = admin.put("/api/settings/alerts", json={"rules": [{"source": "snapshot", "enabled": False, "minLevel": "crit", "channels": []}],
                                                 "quiet": {"enabled": True, "start": "22:00", "end": "07:00"}})
    assert ok.status_code == 200
    out = ok.json()
    assert next(r for r in out["rules"] if r["source"] == "snapshot")["enabled"] is False and out["quiet"]["start"] == "22:00"


# ---------------------------------------------------------------- vCenter parsing (fake pyVmomi objects)
def test_build_extras_from_vcenter_objects(monkeypatch):
    t0 = dt.datetime(2026, 11, 10, 8, 0, tzinfo=dt.timezone.utc)
    snap = NS(rootSnapshotList=[NS(name="before-patch", createTime=t0, description="x", quiesced=False,
                                   childSnapshotList=[NS(name="child", createTime=t0 + dt.timedelta(days=1), description="", quiesced=False,
                                                         childSnapshotList=[])])])
    host_ref, vm_ref, ds_ref = NS(_moId="host-1"), NS(_moId="vm-1"), NS(_moId="datastore-1")
    hosts = {"host-1": (host_ref, {"name": "esx1", "summary": NS(overallStatus="red", runtime=NS(connectionState="connected", inMaintenanceMode=False)),
                                   "runtime.healthSystemRuntime": NS(systemHealthInfo=NS(numericSensorInfo=[
                                       NS(name="PSU 2", sensorType="power", currentReading=0, healthState=NS(key="red")),
                                       NS(name="CPU temp", sensorType="temperature", currentReading=40, healthState=NS(key="green"))]),
                                       hardwareStatusInfo=NS(memoryStatusInfo=[NS(name="DIMM A1", status=NS(key="yellow"))], cpuStatusInfo=[], storageStatusInfo=[]))})}
    vms = {"vm-1": (vm_ref, {"name": "app-01", "datastore": [ds_ref],
                             "summary": NS(config=NS(name="app-01", instanceUuid="u1"), runtime=NS(host=host_ref)), "snapshot": snap})}
    dss = {"datastore-1": (ds_ref, {"name": "DS1", "summary": NS(capacity=1000 * extras.GB, freeSpace=250 * extras.GB, uncommitted=100 * extras.GB,
                                                                type="VMFS", accessible=True, maintenanceMode="normal"),
                                    "host": [NS(key=host_ref)]})}
    layout = NS(file=[NS(name="[DS1] app-01/app-01-000001-sesparse.vmdk", type="diskExtent", size=30 * extras.GB),
                      NS(name="[DS1] app-01/app-01.vmdk", type="diskExtent", size=200 * extras.GB),
                      NS(name="[DS1] app-01/app-01-Snapshot1.vmsn", type="snapshotData", size=1 * extras.GB)])
    alarm_ref = NS(_moId="alarm-7")

    def fake_retrieve(content, refs, vim_type, paths):          # PropertyCollector read of explicit objects
        if refs[0] is vm_ref:
            return {"vm-1": {"layoutEx": layout}}
        return {"alarm-7": {"info.name": "Host hardware power status"}}
    monkeypatch.setattr(extras, "retrieve_refs", fake_retrieve)
    content = NS(rootFolder=NS(triggeredAlarmState=[
        NS(key="alarm-7.host-1", alarm=alarm_ref, entity=host_ref, overallStatus="red", time=t0, acknowledged=False),
        NS(key="alarm-8.vm-1", alarm=alarm_ref, entity=vm_ref, overallStatus="green", time=t0, acknowledged=False)]))
    out = extras.build_extras(content, hosts, {}, vms, dss, {"host-1": ("esx1", "PROD")})
    d = out["datastores"][0]
    assert (d["capacityGB"], d["freeGB"], d["uncommittedGB"], d["clusters"], d["vmCount"]) == (1000.0, 250.0, 100.0, ["PROD"], 1)
    s = out["snapshots"][0]
    assert (s["vm"], s["count"], s["cluster"], s["sizeGB"], s["oldestAt"]) == ("app-01", 2, "PROD", 31.0, "2026-11-10T08:00:00Z")
    assert [i["depth"] for i in s["items"]] == [0, 1]
    h = out["health"][0]
    assert h["overallStatus"] == "red" and [i["name"] for i in h["issues"]] == ["PSU 2", "DIMM A1"]
    assert out["alarms"] == [{"key": "alarm-7.host-1", "entityType": "host", "entity": "esx1", "cluster": "PROD",
                              "alarm": "Host hardware power status", "status": "red", "at": "2026-11-10T08:00:00Z", "acknowledged": False}]


def test_demo_sync_fills_states(db):
    from app.collectors import scheduler
    from app.config import VCenterConfig
    from app.database import SessionLocal as SL
    cfg = VCenterConfig("vcdemo", "VC demo", "demo", 443, "u", "p", False, demo=True)
    with SL() as s:
        scheduler.sync_vcenter(s, cfg)
    with SL() as s:
        rows = infra.datastores(s)
        assert rows and any(r["level"] in ("crit", "bad") for r in rows)          # the demo's nearly full datastore
        assert s.query(VcAlarmState).filter_by(vcenter_key="vcdemo").count() >= 3
        assert any(h["level"] == "crit" for h in infra.host_health(s))
        assert s.query(MetricPoint).filter_by(kind="datastore").count() == 3 * len(rows)
