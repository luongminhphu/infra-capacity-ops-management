"""ICO v2 G7c: request ↔ system (project) ↔ VM (strong / weak / none), owner source "itsm", VM card, SDP link."""
import datetime as dt
import json
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from app.cmdb.models import Ci, CiKey
from app.events.models import ChangeLog
from app.itsm import link
from app.itsm.models import ItsmProjectMap, ItsmRequest, ItsmRequestCi, ItsmRequestLine
from app.models import SnapshotRun, VmSnapshot
from app.owners import rules
from app.owners.models import SystemMember
from app.routers import itsm as itsm_router
from test_cmdb import db  # noqa: F401  (fixture: private SQLite)
from test_owner_rules import ADMIN, _member, _system

T = dt.datetime


def _vm(db, name, keys=(), created=None, first_seen=None):  # noqa: F811
    c = Ci(type="vm", name=name, source_kind="vcenter", source_key=f"u-{name}", status="active",
           first_seen=T(2024, 1, 1), last_seen=T(2026, 11, 1), attrs=json.dumps({"vcenterKey": "vc1"}))
    db.add(c)
    db.flush()
    db.add_all([CiKey(ci_id=c.id, kind=k, value_norm=v) for k, v in keys])
    if created:
        db.add(ChangeLog(at=created, vcenter_key="vc1", vc_event_id=c.id, ci_id=c.id, kind="vm_created", object_type="vm", object_name=name))
    if first_seen:
        run = SnapshotRun(vcenter_key="vc1", source="live", status="ok", started_at=first_seen, finished_at=first_seen)
        db.add(run)
        db.flush()
        db.add(VmSnapshot(run_id=run.id, vcenter_key="vc1", cluster="PRD", vm=name, vcpu=4, ram_gb=8, disk_prov_gb=100, captured_at=first_seen))
    db.flush()
    return c.id


def _request(db, n, kind, names, project="Core Banking", created=T(2026, 10, 1), resolved=T(2026, 10, 5), sizes=(8, 16, 200)):  # noqa: F811
    r = ItsmRequest(source_key="sdp-t", req_id=str(n), subject=f"Yêu cầu {n}", kind=kind, parse_status="parsed",
                    description=f"<p>Dự án: <b>{project}</b> — Khối CNTT</p>", created_at=created, resolved_at=resolved)
    db.add(r)
    db.flush()
    db.add_all([ItsmRequestLine(request_id=r.id, line_no=i, vm_name=v, vcpu=sizes[0], ram_gb=sizes[1], disk_gb=sizes[2], qty=1)
                for i, v in enumerate(names)])
    db.flush()
    return r


def _links(db):  # noqa: F811
    names = {c.id: c.name for c in db.query(Ci)}
    return {(names[x.ci_id], x.match, x.matched_by) for x in db.query(ItsmRequestCi)}


def test_strong_weak_and_no_match(db):  # noqa: F811
    _vm(db, "app-01", created=T(2026, 10, 3))                                              # created by the request
    _vm(db, "WEB-01", keys=[("fqdn", "web-01.corp.local"), ("short_name", "web-01")], first_seen=T(2026, 10, 15))  # ≤ resolved + 14 d
    _vm(db, "db-01.corp.local", keys=[("short_name", "db-01")], created=T(2026, 10, 2))   # short name only
    _vm(db, "old-01", first_seen=T(2025, 1, 1))                                              # name reused: existed before
    _vm(db, "late-01", created=T(2026, 10, 25))                                              # appeared after resolved + 14 days
    _request(db, 101, "new_vm", ["app-01", "web-01.corp.local", "db-01", "old-01", "late-01", "nothing-01"])
    assert link.link_vms(db) == {"strong": 2, "weak": 3}
    assert _links(db) == {("app-01", "strong", "name+window"), ("WEB-01", "strong", "fqdn+window"),
                          ("db-01.corp.local", "weak", "short_name"), ("old-01", "weak", "name+outside_window"),
                          ("late-01", "weak", "name+outside_window")}
    assert link.link_vms(db) == {"strong": 2, "weak": 3}          # rebuilt, not duplicated


def test_resize_and_decommission_match_existing_vm_without_window(db):  # noqa: F811
    legacy = _vm(db, "legacy-01", first_seen=T(2024, 3, 1))
    _request(db, 201, "resize", ["legacy-01"])
    _request(db, 202, "decommission", ["LEGACY-01"])
    _request(db, 203, "other", ["legacy-01"])                       # other requests are not linked
    review = _request(db, 204, "resize", ["legacy-01"])
    review.parse_status = "review"                                   # not counted until reviewed
    link.link_vms(db)
    assert sorted((r.req_id, x.match) for x, r in db.query(ItsmRequestCi, ItsmRequest).join(ItsmRequest)) == [("201", "strong"), ("202", "strong")]
    assert {x.ci_id for x in db.query(ItsmRequestCi)} == {legacy}


def test_project_exact_match_suggestion_and_saved_mapping(db):  # noqa: F811
    core = _system(db, "Core Banking")
    _system(db, "Card Gateway")
    a = _request(db, 301, "new_vm", [], project="core banking")       # exact (normalised name)
    b = _request(db, 302, "new_vm", [], project="Core Bankng")        # typo: suggestion only
    link.run(db)
    assert (a.project, a.system_id, b.project, b.system_id) == ("core banking", core, "Core Bankng", None)
    out = itsm_router.projects(ADMIN, db)
    row = next(p for p in out["projects"] if p["project"] == "Core Bankng")
    assert row["system"] is None and row["suggestions"][0]["systemId"] == core
    assert next(p for p in out["projects"] if p["project"] == "core banking")["how"] == "exact"
    itsm_router.map_project({"project": "Core Bankng", "systemId": core}, ADMIN, db)       # confirm once …
    c = _request(db, 303, "new_vm", [], project="CORE BANKNG")       # … reused by later requests
    link.run(db)
    assert (b.system_id, c.system_id) == (core, core)
    assert db.query(ItsmProjectMap).count() == 1
    with pytest.raises(HTTPException):
        itsm_router.map_project({"project": "X", "systemId": 99999}, ADMIN, db)
    itsm_router.map_project({"project": "Core Bankng", "systemId": None}, ADMIN, db)
    assert db.query(ItsmProjectMap).count() == 0 and b.system_id is None


def test_owner_source_itsm_is_the_lowest_and_needs_a_strong_link(db):  # noqa: F811
    _system(db, "Core Banking")
    manual = _system(db, "Manual")
    app1 = _vm(db, "app-01", created=T(2026, 10, 3))
    app2 = _vm(db, "app-02", created=T(2026, 10, 3))
    short = _vm(db, "app-03.corp.local", keys=[("short_name", "app-03")], created=T(2026, 10, 3))
    _request(db, 401, "new_vm", ["app-01", "app-02", "app-03"])
    db.add(SystemMember(ci_id=app2, system_id=manual, source="manual", assigned_at=T(2026, 10, 4)))
    link.run(db)
    rules.run(db)
    assert (_member(db, app1), _member(db, app2), _member(db, short)) == (("Core Banking", "itsm"), ("Manual", "manual"), None)
    assert db.query(SystemMember).filter_by(ci_id=app1).one().confidence == "strong"
    db.query(ItsmRequestCi).delete()                                 # link gone → the assignment it gave is removed
    db.commit()
    assert rules.run(db)["remove"] == 1 and _member(db, app1) is None


def test_vm_card_requested_vs_current_and_sdp_link(db, monkeypatch):  # noqa: F811
    from app import config
    cid = _vm(db, "app-01", created=T(2026, 10, 3))
    _request(db, 501, "new_vm", ["app-01"])
    r = _request(db, 502, "resize", ["app-01"], created=T(2026, 10, 20), resolved=None, sizes=(16, 32, 300))
    db.query(ItsmRequestLine).filter_by(request_id=r.id).update({"vcpu_before": 8, "ram_gb_before": 16, "disk_gb_before": 200})
    link.link_vms(db)
    base = NS(itsm_base_url="https://sdp.corp.local:8080/", itsm=lambda: [])
    monkeypatch.setattr(config, "get_settings", lambda: base)
    out = link.vm_requests(db, cid, {"vcpu": 16, "ramGB": 32, "diskProvGB": 300})
    assert [(x["reqId"], x["kind"], x["match"]) for x in out] == [("502", "resize", "strong"), ("501", "new_vm", "strong")]
    assert out[1]["url"] == "https://sdp.corp.local:8080/WorkOrder.do?woMode=viewWO&woID=501"
    assert out[1]["requested"]["vcpu"] == 8 and out[1]["current"] == {"vcpu": 16, "ramGB": 32, "diskGB": 300}
    assert (out[0]["requested"]["vcpuBefore"], out[0]["requested"]["vcpu"]) == (8, 16)
    # no base URL: the source host; a demo source or a non-http base gives no link
    base.itsm_base_url = ""
    base.itsm = lambda: [NS(key="sdp-t", host="sdp01", port=8443, demo=False)]
    assert link.sdp_url("sdp-t", "7") == "https://sdp01:8443/WorkOrder.do?woMode=viewWO&woID=7"
    base.itsm_base_url = "javascript:alert(1)"
    assert link.sdp_url("sdp-t", "7") is None
