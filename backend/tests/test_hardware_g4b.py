"""ICO v2 G4b: OME deep inventory / firmware compliance / warranty / alerts — parsers (fixtures), read plan, alert
cursor without duplicates, retention, Dell warranty ↔ Opex reconciliation (4 cases), Kế hoạch năm sau line, inbox."""
import datetime as dt
import json
import pathlib

import pytest

from app.database import SessionLocal
from app.hardware import HardwareConfig, HardwareError, collector, ome, warranty
from app.hardware.models import (
    HwAlert, HwComponentHealth, HwDeviceInventory, HwDeviceState, HwDiskState, HwFirmwareState, HwPartState, HwSourceState,
    HwWarranty,
)
from app.models import DataSource
from app.storage import http
from test_spend_opex import _login, admin, app_  # noqa: F401  (fixtures)

FIX = pathlib.Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 11, 20)
HW_MODELS = (HwAlert, HwComponentHealth, HwDeviceInventory, HwDiskState, HwFirmwareState, HwPartState, HwWarranty,
             HwDeviceState, HwSourceState)


def _fix(name):
    return json.loads((FIX / name).read_text())


# ---------------------------------------------------------------- parsers
def test_parse_inventory_details():
    inv = ome.parse_inventory(_fix("ome_inventory_details.json"))
    d0, d1 = inv["disks"]
    assert (d0["sizeGB"], d0["media"], d0["status"], d0["predictiveFailure"]) == (893.75, "SSD", "ok", False)
    assert (d1["sizeGB"], d1["status"], d1["predictiveFailure"], d1["serial"]) == (2232.32, "bad", True, "WBN0ABCD")
    parts = {(p["kind"], p["name"]): p for p in inv["parts"]}
    assert parts[("psu", "PS1 Status")]["status"] == "crit" and parts[("psu", "PS1 Status")]["detail"] == "1400 W"
    assert parts[("psu", "PS2 Status")]["status"] == "ok"                       # StatusString only
    assert parts[("raid", "PERC H755 Front")]["detail"] == "FW 52.16.1-4405"
    assert parts[("memory", "DIMM.Socket.A1")]["detail"] == "32 GB"
    assert inv["macs"] == ["b0:7b:25:aa:bb:01", "b0:7b:25:aa:bb:02"]          # 3 spellings, duplicates + 00:… dropped
    assert inv["os"] == "VMware ESXi 8.0.2" and inv["hostname"] == "esx-prod-a01.corp.local"
    assert [s["component"] for s in inv["software"]] == ["BIOS", "Integrated Dell Remote Access Controller", "PERC H755 Front"]


def test_firmware_compliance_merge():
    fx = _fix("ome_baseline_compliance.json")
    comp = ome.parse_compliance(fx["reports"]["value"])
    inv = ome.merge_compliance({10074: ome.parse_inventory(_fix("ome_inventory_details.json"))}, comp)
    fw = {f["component"]: (f["version"], f["baselineVersion"], f["compliance"]) for f in inv[10074]["firmware"]}
    assert fw == {"BIOS": ("1.9.2", "1.12.1", "upgrade"),
                  "Integrated Dell Remote Access Controller": ("7.00.00.171", "7.00.00.171", "ok"),
                  "PERC H755 Front": ("52.16.1-4405", "52.14.0-3901", "downgrade"),
                  "Broadcom Adv. Dual 25Gb Ethernet": ("22.31.6", "22.31.6", "ok")}      # only in the report: added
    no_baseline = ome.merge_compliance({1: ome.parse_inventory(_fix("ome_inventory_details.json"))}, {})
    assert all(f["compliance"] is None and f["baselineVersion"] is None for f in no_baseline[1]["firmware"])


def test_parse_warranty_and_alerts():
    ws = [w for w in (ome.parse_warranty(r) for r in _fix("ome_warranties.json")["value"]) if w]
    assert ws == [{"serviceTag": "7FHKZ53", "serviceLevel": "Next Business Day Onsite", "start": "2023-03-01", "end": "2024-03-01"},
                  {"serviceTag": "7FHKZ53", "serviceLevel": "ProSupport Plus Mission Critical 4 Hour", "start": "2024-03-02",
                   "end": "2028-03-01"}]
    al = [a for a in (ome.parse_alert(r) for r in _fix("ome_alerts.json")["value"]) if a]
    assert [(a["alertId"], a["severity"], a["category"], a["serviceTag"]) for a in al] == [
        (9001, "crit", "Power Supply", "9QWXY63"), (9002, "bad", "Storage", "4BMDB21")]   # audit + chassis alerts dropped
    assert al[0]["at"] == "2026-11-17T01:58:00"


# ---------------------------------------------------------------- client: slower reads on the same session, read-only
CFG = HardwareConfig("ome1", "OME 1", "ome.corp.local", username="ico_ro", password="S3cret-OME!")


def test_collect_with_full_plan_and_failing_warranty(monkeypatch):
    calls = []
    fx = _fix("ome_baseline_compliance.json")

    def fake(self, method, path, params=None, headers=None, body=None):
        calls.append((method, path))
        if method == "POST":
            return {"Id": "s-1"}, {"x-auth-token": "t"}
        if method == "DELETE":
            return {}, {}
        if path.startswith("/api/DeviceService/Devices?"):
            return {"value": [{"Id": 10074, "Type": 1000, "DeviceServiceTag": "7FHKZ53", "Status": 1000}]}, {}
        if path.endswith("/InventoryDetails"):
            return _fix("ome_inventory_details.json"), {}
        if path.startswith("/api/UpdateService/Baselines?"):
            return fx["baselines"], {}
        if path.startswith("/api/UpdateService/Baselines(7)/DeviceComplianceReports"):
            return fx["reports"], {}
        if path.startswith("/api/WarrantyService/Warranties"):
            raise HardwareError("Không có API /api/WarrantyService/Warranties (404)")
        if path.startswith("/api/AlertService/Alerts?"):
            return _fix("ome_alerts.json"), {}
        raise AssertionError(path)
    monkeypatch.setattr(http.JsonClient, "request", fake)
    out = ome.collect(CFG, {"inventory": True, "warranty": True, "alertsSince": dt.datetime(2026, 11, 17, 1, 58)})
    assert out["baselineConfigured"] is True and out["inventory"][10074]["firmware"][0]["compliance"] == "upgrade"
    assert "warranties" not in out and out["warnings"] == ["Bảo hành: Không có API /api/WarrantyService/Warranties (404)"]
    assert [a["alertId"] for a in out["alerts"]] == [9001, 9002]
    alert_url = next(p for m, p in calls if p.startswith("/api/AlertService/Alerts"))
    assert "$filter=TimeStamp%20ge%20%272026-11-17%2001%3A58%3A00.000%27" in alert_url and "$orderby=TimeStamp%20asc" in alert_url
    assert [(m, p) for m, p in calls if m != "GET"] == [("POST", "/api/SessionService/Sessions"),
                                                        ("DELETE", "/api/SessionService/Sessions('s-1')")]


def test_no_baseline_is_not_an_error(monkeypatch):
    def fake(self, method, path, params=None, headers=None, body=None):
        if method == "POST":
            return {"Id": "s"}, {"x-auth-token": "t"}
        if method == "DELETE":
            return {}, {}
        if path.startswith("/api/DeviceService/Devices?"):
            return {"value": [{"Id": 1, "Type": 1000, "DeviceServiceTag": "AAA1111", "Status": 1000}]}, {}
        if path.endswith("/InventoryDetails"):
            return _fix("ome_inventory_details.json"), {}
        return {"value": []}, {}
    monkeypatch.setattr(http.JsonClient, "request", fake)
    out = ome.collect(CFG, {"inventory": True})
    assert out["baselineConfigured"] is False and out["warnings"] == []
    assert all(f["compliance"] is None for f in out["inventory"][1]["firmware"])


def test_acknowledge_and_remove_alerts_are_refused():
    c = ome.OmeClient(CFG)
    for path in ("/api/AlertService/Actions/AlertService.Acknowledge", "/api/AlertService/Actions/AlertService.RemoveAlerts"):
        with pytest.raises(HardwareError, match="chỉ đọc"):
            c.http.request("POST", path)


# ---------------------------------------------------------------- read plan
def test_plan_for_inventory_every_12h_warranty_daily_alerts_from_cursor():
    now = dt.datetime(2026, 11, 20, 12, 0)
    p = collector.plan_for(None, now)
    assert p["inventory"] and p["warranty"] and p["alertsSince"] == now - dt.timedelta(days=7)
    st = HwSourceState(inventory_at=now - dt.timedelta(hours=11), warranty_at=now - dt.timedelta(hours=23),
                       alert_cursor=dt.datetime(2026, 11, 20, 9, 30))
    p = collector.plan_for(st, now)
    assert (p["inventory"], p["warranty"], p["alertsSince"]) == (False, False, dt.datetime(2026, 11, 20, 9, 30))
    st.inventory_at, st.warranty_at = now - dt.timedelta(hours=12), now - dt.timedelta(hours=24)
    p = collector.plan_for(st, now)
    assert p["inventory"] and p["warranty"]


# ---------------------------------------------------------------- store / cursor / retention
@pytest.fixture()
def db(app_):
    s = SessionLocal()

    def wipe():
        for m in HW_MODELS:
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


def _alert(i, at):
    return {"alertId": i, "deviceId": 1, "serviceTag": "AAA1111", "severity": "bad", "status": "Not Acknowledged",
            "category": "Fan", "messageId": "FAN0001", "message": f"alert {i}", "at": at}


def test_alert_cursor_never_stores_twice(db, monkeypatch):
    cfg = HardwareConfig("ome-c", "OME C", "ome.local", username="u", password="p")
    seen_plans = []
    batches = [[_alert(1, "2026-11-19T08:00:00"), _alert(2, "2026-11-19T09:00:00")],
               [_alert(2, "2026-11-19T09:00:00"), _alert(3, "2026-11-19T10:30:00")],     # "ge" re-reads the boundary
               []]

    def fake_read(c, plan=None):
        seen_plans.append(plan)
        return {"devices": [], "health": [], "warnings": [], "alerts": batches[len(seen_plans) - 1]}
    monkeypatch.setattr(collector, "read", fake_read)
    for _ in range(3):
        collector.sync_hardware(db, cfg)
    assert sorted(a for (a,) in db.query(HwAlert.alert_id).filter_by(source_key="ome-c")) == [1, 2, 3]
    assert seen_plans[1]["alertsSince"] == dt.datetime(2026, 11, 19, 9, 0)
    assert seen_plans[2]["alertsSince"] == dt.datetime(2026, 11, 19, 10, 30)
    st = db.query(HwSourceState).filter_by(source_key="ome-c").one()
    assert st.alert_cursor == dt.datetime(2026, 11, 19, 10, 30)               # an empty batch keeps the cursor


def test_inventory_kept_until_the_next_deep_read(db, monkeypatch):
    cfg = HardwareConfig("ome-i", "OME I", "ome.local", username="u", password="p")
    inv = {1: {**ome.parse_inventory(_fix("ome_inventory_details.json")), "firmware": [{"component": "BIOS", "version": "1"}]}}
    data = [{"devices": [{"deviceId": 1, "serviceTag": "AAA1111", "name": "a", "status": "ok", "powerState": "on"}],
             "health": [], "warnings": [], "inventory": inv, "baselineConfigured": False},
            {"devices": [{"deviceId": 1, "serviceTag": "AAA1111", "name": "a", "status": "ok", "powerState": "on"}],
             "health": [], "warnings": ["Bảo hành: lỗi"]}]
    monkeypatch.setattr(collector, "read", lambda c, plan=None: data.pop(0))
    collector.sync_hardware(db, cfg)
    first = db.query(HwSourceState).filter_by(source_key="ome-i").one().inventory_at
    collector.sync_hardware(db, cfg)
    st = db.query(HwSourceState).filter_by(source_key="ome-i").one()
    assert st.inventory_at == first and st.baseline_configured is False and st.last_warning == "Bảo hành: lỗi"
    assert db.query(HwDiskState).filter_by(source_key="ome-i").count() == 2
    assert db.query(HwPartState).filter_by(source_key="ome-i").count() == 4
    assert json.loads(db.query(HwDeviceInventory).filter_by(source_key="ome-i").one().macs) == ["b0:7b:25:aa:bb:01", "b0:7b:25:aa:bb:02"]


def test_hw_alert_retention_13_months(db):
    from app.collectors import retention
    now = dt.datetime(2026, 11, 20, 0, 0)
    for i, at in enumerate((dt.datetime(2025, 10, 19), dt.datetime(2025, 10, 21), dt.datetime(2026, 11, 1))):
        db.add(HwAlert(source_key="ome-r", alert_id=i, severity="bad", at=at))
    db.commit()
    assert retention.purge_hw_alerts(db, 13, now) == 1                  # cutoff 2025-10-20
    assert sorted(a for (a,) in db.query(HwAlert.alert_id).filter_by(source_key="ome-r")) == [1, 2]
    assert retention.purge_hw_alerts(db, 0, now) == 0                   # 0 disables


# ---------------------------------------------------------------- warranty ↔ Opex
COV_NONE = {"state": "none", "end": None, "item": None}


@pytest.mark.parametrize("dell_end,cov,critical,case", [
    (dt.date(2028, 1, 1), COV_NONE, None, "no_opex"),
    (dt.date(2028, 1, 1), {"state": "active", "end": dt.date(2026, 12, 31)}, None, "opex_before_dell"),
    (dt.date(2028, 1, 1), {"state": "expired", "end": dt.date(2026, 6, 30)}, None, "opex_before_dell"),
    (dt.date(2028, 1, 1), {"state": "active", "end": dt.date(2028, 6, 30)}, None, None),
    (dt.date(2025, 6, 1), {"state": "active", "end": dt.date(2026, 12, 31)}, None, "dell_expired_opex_active"),
    (dt.date(2025, 6, 1), COV_NONE, {"vms": 3}, "expired_critical"),
    (dt.date(2025, 6, 1), {"state": "expired", "end": dt.date(2025, 6, 1)}, {"vms": 1}, "expired_critical"),
    (dt.date(2025, 6, 1), COV_NONE, None, None),
    (dt.date(2026, 11, 20), COV_NONE, None, "no_opex"),                   # covers today
])
def test_classify_cases(dell_end, cov, critical, case):
    assert warranty.classify({"end": dell_end}, cov, critical, TODAY) == case
    assert warranty.classify(None, cov, critical, TODAY) is None          # no warranty data: no finding


def test_codes_are_stable_and_prefixed():
    a = warranty.code("expired_critical", "AAA0004")
    assert a == warranty.code("expired_critical", "AAA0004") and a.startswith("WCRT-") and len(a) == 10
    assert a != warranty.code("expired_critical", "AAA0005") and warranty.code("no_opex", "X").startswith("WNOP-")


def _setup_reconcile(db, admin):
    from app.cmdb.models import Ci, CiLink
    from app.owners.models import System, SystemMember
    from test_spend_opex import _opex
    assert admin.post("/api/admin/sources", json={"kind": "ome", "key": "ome-t", "name": "OME T", "host": "", "demo": True}).status_code == 200
    from app import sources
    sources.invalidate_cache()
    dell = {"AAA0001": "2028-01-01", "AAA0002": "2028-01-01", "AAA0003": "2025-06-01", "AAA0004": "2025-06-01",
            "AAA0005": "2025-06-01", "AAA0006": "2028-01-01"}
    for n, (tag, end) in enumerate(dell.items()):
        db.add(HwDeviceState(source_key="ome-t", device_id=n + 1, service_tag=tag, name=f"srv-{tag[-1]}", model="PowerEdge R750",
                             status="ok", power_state="on"))
        db.add(HwWarranty(source_key="ome-t", service_tag=tag, service_level="ProSupport", start_date=dt.date(2022, 1, 1),
                          end_date=dt.date.fromisoformat(end)))
    db.commit()
    for tags, start, end in ((["AAA0002"], "2026-01-01", "2026-12-31"), (["AAA0003"], "2026-01-01", "2026-12-31"),
                             (["AAA0006"], "2026-01-01", "2028-06-30")):
        r = admin.post("/api/spend/opex", json=_opex(title=f"BH {tags[0]}", startDate=start, endDate=end,
                                                    assets=[{"serial": t.lower()} for t in tags]))
        assert r.status_code == 200, r.text
    phys = Ci(type="physical_server", name="AAA0004", source_kind="hardware", source_key="AAA0004")
    host = Ci(type="hypervisor_host", name="esx-t4", source_kind="vcenter", source_key="vct:esx-t4")
    vms = [Ci(type="vm", name=f"core-{i}", source_kind="vcenter", source_key=f"g4b-vm-{i}", attrs=json.dumps({"powerOn": i < 2}))
           for i in range(3)]
    db.add_all([phys, host, *vms])
    db.flush()
    db.add_all([CiLink(from_ci=host.id, to_ci=phys.id, relation="runs_on", source="vcenter", confidence="strong"),
                *[CiLink(from_ci=v.id, to_ci=host.id, relation="hosted_on", source="vcenter", confidence="strong") for v in vms]])
    crit = System(code="SYS-G4B1", name="Core Banking G4b", name_norm="core banking g4b", criticality=1)
    low = System(code="SYS-G4B2", name="Intranet G4b", name_norm="intranet g4b", criticality=3)
    db.add_all([crit, low])
    db.flush()
    db.add_all([SystemMember(ci_id=vms[0].id, system_id=crit.id), SystemMember(ci_id=vms[1].id, system_id=low.id),
                SystemMember(ci_id=vms[2].id, system_id=crit.id)])     # vm 2 is off: not counted
    db.commit()
    return [phys, host, *vms], [crit, low]


def _cleanup(db, cis, systems):
    from app.spend.models import OpexAsset, OpexItem
    for c in cis:
        db.delete(db.merge(c))
    for s in systems:
        db.delete(db.merge(s))
    ids = [i for (i,) in db.query(OpexItem.id).filter(OpexItem.title.like("BH AAA%"))]
    db.query(OpexAsset).filter(OpexAsset.opex_id.in_(ids or [-1])).delete(synchronize_session=False)
    db.query(OpexItem).filter(OpexItem.id.in_(ids or [-1])).delete(synchronize_session=False)
    db.commit()


def test_reconcile_four_cases_and_plan_line(db, admin):
    cis, systems = _setup_reconcile(db, admin)
    try:
        found = {f["serviceTag"]: f for f in warranty.reconcile(db, TODAY)}
        assert {t: f["case"] for t, f in found.items()} == {
            "AAA0001": "no_opex", "AAA0002": "opex_before_dell", "AAA0003": "dell_expired_opex_active", "AAA0004": "expired_critical"}
        w4 = found["AAA0004"]
        assert w4["level"] == "crit" and w4["code"] == warranty.code("expired_critical", "AAA0004")
        assert w4["systems"] == [{"code": "SYS-G4B1", "name": "Core Banking G4b", "criticality": 1}] and w4["vms"] == 1
        assert found["AAA0002"]["opex"]["end"] == "2026-12-31" and found["AAA0002"]["opex"]["state"] == "active"
        assert list(found)[0] == "AAA0004"                                  # VƯỢT NGƯỠNG first

        # Kế hoạch năm sau 2027: servers whose Dell warranty ends before 31/12/2027 and no Opex covers the year
        from app.spend import budget_plan, settings as spend_settings
        lines = [s for s in budget_plan.suggest(db, 2027, spend_settings.load(db)) if s["source"] == "warranty"]
        assert len(lines) == 1
        line = lines[0]
        assert line["title"] == "Gia hạn bảo hành Dell 3 máy chủ" and line["sourceId"] == 2027 and line["priority"] == "must"
        assert line["kind"] == "opex" and line["baseNet"] == 0
        assert warranty.plan_serials(db, dt.date(2027, 1, 1), dt.date(2027, 12, 31)) == ["AAA0003", "AAA0004", "AAA0005"]

        r = admin.get("/api/assets/dc/warranty-check")
        assert r.status_code == 200
        body = r.json()
        assert body["configured"] is True and len(body["items"]) == 4 and body["opexDetail"] is True
        assert {c["code"] for c in body["cases"]} == {"WNOP", "WOPE", "WDLE", "WCRT"}
    finally:
        _cleanup(db, cis, systems)


def test_warranty_check_hides_opex_without_spend_role(db, admin, app_):
    cis, systems = _setup_reconcile(db, admin)
    try:
        viewer = _login(app_, "nobody1")
        items = viewer.get("/api/assets/dc/warranty-check").json()["items"]
        withopex = [i for i in items if i["opex"]]
        assert withopex and all(set(i["opex"]) == {"state", "end"} for i in withopex)
    finally:
        _cleanup(db, cis, systems)


# ---------------------------------------------------------------- demo end to end: inbox
def test_demo_inbox_has_predictive_disk_and_warranty_groups(db, admin):
    from app import inbox
    from app.cmdb import builder
    from app.collectors import scheduler
    from app.config import get_settings
    from app import sources
    assert admin.post("/api/admin/sources", json={"kind": "vcenter", "key": "vcd", "name": "VC demo", "host": "", "demo": True}).status_code == 200
    assert admin.post("/api/admin/sources", json={"kind": "ome", "key": "ome-demo", "name": "OME demo", "host": "", "demo": True}).status_code == 200
    sources.invalidate_cache()
    s = get_settings()
    scheduler.sync_vcenter(db, s.vcenters()[0], s)
    res = scheduler.sync_hardware_sources(s)[0]
    assert (res["inventory"], res["warranties"], res["alerts"]) == (12, 12, 2)
    again = scheduler.sync_hardware_sources(s)[0]
    assert "inventory" not in again and "warranties" not in again       # not due again within 12 h / 24 h
    assert db.query(HwAlert).filter_by(source_key="ome-demo").count() == 2
    builder.build_all(db)
    hw = inbox.hardware_items(db)
    disks = [i for i in hw if i["title"].startswith("Ổ đĩa dự báo hỏng")]
    assert len(disks) == 1 and disks[0]["level"] == "crit" and disks[0]["page"] == "entity"
    assert disks[0]["query"].startswith("type=server&name=")
    assert any(i["level"] == "crit" and "linh kiện hỏng" in i["title"] for i in hw)          # failed PSU
    assert any(i["level"] == "bad" and "backup-media-01" in i["title"] for i in hw)          # fan warning
    fw = [i for i in hw if "firmware lệch" in i["title"]]
    assert len(fw) == 1 and fw[0]["level"] == "warn" and fw[0]["title"].startswith("1 máy chủ")
    aw = {i["key"].split("|")[1]: i for i in inbox.warranty_items(db, dt.date.today())}
    # demo: cluster B hosts 1–2 + backup-media-01 expired (no critical VMs, no Opex); the others covered, no Opex
    assert set(aw) == {"no_opex"} and aw["no_opex"]["title"].startswith("9 máy chủ") and "WNOP-" in aw["no_opex"]["detail"]
    assert all(i["source"] == "hardware" for i in hw)
    from app import alert_rules
    assert "hardware" in alert_rules.SOURCES
