"""ICO v2 G6: vCenter events → change log, grouping of incidents along strong CI links, changes before an incident."""
import datetime as dt
import json

import pytest

from app.cmdb.models import Ci, CiLink
from app.database import SessionLocal
from app.events import changes, service as ev
from app.events.models import ChangeCursor, ChangeLog, OpsEvent, OpsEventOpen, OpsIncident
from app.vcenter import events as vce
from test_spend_opex import admin, app_  # noqa: F401  (fixtures)

T0 = dt.datetime(2026, 11, 20, 2, 0)
VC = "g6vc"


def _wipe(db):
    for m in (OpsEvent, OpsEventOpen, OpsIncident, ChangeLog, ChangeCursor):
        db.query(m).delete()
    ids = [i for (i,) in db.query(Ci.id).filter(Ci.source_key.like("g6%"))]
    db.query(CiLink).filter(CiLink.from_ci.in_(ids or [-1])).delete(synchronize_session=False)
    db.query(Ci).filter(Ci.id.in_(ids or [-1])).delete(synchronize_session=False)
    db.commit()


@pytest.fixture()
def db(app_):
    s = SessionLocal()
    _wipe(s)
    yield s
    _wipe(s)
    s.close()


@pytest.fixture()
def graph(db):
    """physical server P ← host H (runs_on, strong) ← VM1, VM2 (hosted_on, strong); VM3 only weakly linked to H."""
    def ci(typ, key, name, attrs=None):
        c = Ci(type=typ, source_kind="hardware" if typ == "physical_server" else "vcenter", source_key=key, name=name,
               status="active", attrs=json.dumps(attrs or {"vcenterKey": VC}))
        db.add(c)
        db.flush()
        return c.id
    g = {"P": ci("physical_server", "g6SN001", "g6SN001", {}), "H": ci("hypervisor_host", f"{VC}:g6-esx01", "g6-esx01"),
         "VM1": ci("vm", "g6-u1", "g6-app01"), "VM2": ci("vm", "g6-u2", "g6-db01"), "VM3": ci("vm", "g6-u3", "g6-web01")}
    for f, t, rel, conf in (("H", "P", "runs_on", "strong"), ("VM1", "H", "hosted_on", "strong"),
                            ("VM2", "H", "hosted_on", "strong"), ("VM3", "H", "hosted_on", "weak")):
        db.add(CiLink(from_ci=g[f], to_ci=g[t], relation=rel, source="test", confidence=conf, first_seen=T0, last_seen=T0))
    db.commit()
    return g


def _ha(vm, n, at):
    return {"id": n, "at": at, "kind": "ha_vm_restarted", "eventType": "VmRestartedOnAlternateHostEvent", "userName": None,
            "objectType": "vm", "objectName": vm, "message": f"HA restarted {vm}", "detail": {"eventType": "VmRestartedOnAlternateHostEvent"}}


def test_psu_host_alarm_and_two_ha_restarts_make_one_incident_rooted_at_the_server(db, graph):
    # round 1 (vCenter sync): two VMs restarted by HA, before any hardware signal → stored in the change log
    changes.store(db, VC, [_ha("g6-app01", 1, T0 + dt.timedelta(minutes=2)), _ha("g6-db01", 2, T0 + dt.timedelta(minutes=3)),
                           _ha("g6-web01", 3, T0 + dt.timedelta(minutes=4))], T0 + dt.timedelta(minutes=5))
    db.commit()
    ev.record_transitions(db, {s: {} for s in ev.SOURCES}, now=T0 + dt.timedelta(minutes=5), points=ev._points(db))
    assert db.query(OpsIncident).count() == 3                       # nothing links them yet
    # round 2, 6 minutes later: OME reports the PSU, vCenter the host hardware alarm
    cur = {s: {} for s in ev.SOURCES}
    cur["hardware"] = {"hw|ome|7": ("crit", graph["P"], "Máy chủ g6SN001: PSU 2 lỗi", "Power Supply 2 failure")}
    cur["alarm"] = {"alarm|g6vc|a1": ("bad", graph["H"], "Alarm Host hardware power status — g6-esx01", "")}
    ev.record_transitions(db, cur, now=T0 + dt.timedelta(minutes=11))
    incs = db.query(OpsIncident).order_by(OpsIncident.id).all()
    main = next(i for i in incs if i.root_ci_id == graph["P"])
    members = {e.ci_id for e in db.query(OpsEvent).filter(OpsEvent.incident_id == main.id)}
    assert members == {graph["P"], graph["H"], graph["VM1"], graph["VM2"]} and main.event_count == 4
    assert main.peak_level == "crit"
    # VM3 is linked to the host by a weak (name / IP) link only: its restart stays its own incident
    assert len(incs) == 2 and next(i for i in incs if i is not main).ci_id == graph["VM3"]
    # incident page: the changes of the 24 hours before (here: the HA restarts at the incident's members)
    detail = ev.incident_detail(db, main.id)
    assert detail["root"]["id"] == graph["P"] and {c["object"] for c in detail["changesBefore"]} >= {"g6-app01"}


def test_events_older_than_the_window_do_not_group(db, graph):
    cur = {s: {} for s in ev.SOURCES}
    cur["hardware"] = {"hw|ome|7": ("crit", graph["P"], "PSU", "")}
    ev.record_transitions(db, cur, now=T0)
    cur["alarm"] = {"alarm|g6vc|a1": ("bad", graph["H"], "Alarm host", "")}
    ev.record_transitions(db, cur, now=T0 + dt.timedelta(minutes=16))     # EVENT_CORRELATE_MINUTES = 15
    assert db.query(OpsIncident).count() == 2


def test_store_is_deduplicated_and_moves_the_cursor(db, graph):
    e = [{"id": 10, "at": T0, "kind": "vm_reconfigured", "eventType": "VmReconfiguredEvent", "userName": "CORP\\an",
          "objectType": "vm", "objectName": "g6-app01", "message": "numCPU 4 → 8", "detail": {}}]
    assert changes.store(db, VC, e, T0 + dt.timedelta(minutes=5)) == 1
    assert changes.store(db, VC, e, T0 + dt.timedelta(minutes=10)) == 0
    db.commit()
    assert changes.cursor(db, VC) == T0 + dt.timedelta(minutes=10)
    assert changes.store(db, VC, None, T0 + dt.timedelta(hours=1)) == 0 and changes.cursor(db, VC) == T0 + dt.timedelta(minutes=10)
    row = db.query(ChangeLog).one()
    assert row.ci_id == graph["VM1"] and row.handled is True                 # not an incident kind
    out = changes.listing(db, user="an", days=400, now=T0 + dt.timedelta(hours=1))
    assert [(i["kind"], i["user"], i["ci"]["id"]) for i in out["items"]] == [("vm_reconfigured", "CORP\\an", graph["VM1"])]
    assert "ip" in out["items"][0]


def test_query_events_splits_a_full_window():
    """vCenter answers at most 1.000 events: a full page means the window is split and read again."""
    class VmPoweredOnEvent:
        def __init__(self, k, at):
            self.key, self.createdTime, self.userName, self.fullFormattedMessage = k, at, "u", "on"
            self.vm = type("A", (), {"name": f"vm{k}"})()
            self.host = None

    calls = []

    class EM:
        def QueryEvents(self, spec):
            b, e = spec.time.beginTime.replace(tzinfo=None), spec.time.endTime.replace(tzinfo=None)
            calls.append((b, e))
            evs = [VmPoweredOnEvent(i, b + dt.timedelta(seconds=i)) for i in range(int((e - b).total_seconds()))]
            return evs[:vce.MAX_PER_QUERY]
    content = type("C", (), {"eventManager": EM()})()
    got = vce.read(content, T0, T0 + dt.timedelta(seconds=1500))
    assert len(calls) == 3 and len(got) == 1500 and got[0]["kind"] == "vm_powered_on" and got[0]["objectName"] == "vm0"


def test_change_log_api(db, graph, admin):  # noqa: F811
    changes.store(db, VC, [_ha("g6-app01", 5, dt.datetime.utcnow() - dt.timedelta(hours=1))], dt.datetime.utcnow())
    db.commit()
    r = admin.get("/api/changes?days=1&kind=ha_vm_restarted").json()
    assert [i["object"] for i in r["items"]] == ["g6-app01"] and r["items"][0]["kindLabel"] == "HA khởi động lại VM"
    assert [i["object"] for i in admin.get(f"/api/ci/{graph['VM1']}/changes").json()["items"]] == ["g6-app01"]


def test_late_read_events_group_by_their_own_time(db, graph):
    """First sync reads 24 h back: an HA failover 8 h ago (host failed + VM restarted) must still form one incident."""
    old = T0 - dt.timedelta(hours=8)
    failed = {**_ha("g6-esx01", 20, old), "kind": "ha_host_failed", "objectType": "host", "objectName": "g6-esx01"}
    changes.store(db, VC, [failed, _ha("g6-app01", 21, old + dt.timedelta(seconds=40))], T0)
    db.commit()
    ev.record_transitions(db, {s: {} for s in ev.SOURCES}, now=T0, points=ev._points(db))
    inc = db.query(OpsIncident).one()
    assert inc.root_ci_id == graph["H"] and inc.event_count == 2 and inc.opened_at == old
