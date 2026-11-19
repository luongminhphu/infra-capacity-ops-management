"""ICO v2 G2b: automatic system assignment — vCenter folder / pool / custom attributes, owner rules, priority, proposals."""
import datetime as dt
import json
from types import SimpleNamespace as NS

import pytest

from app.cmdb.models import Ci
from app.models import User
from app.owners import rules, service
from app.owners.models import OwnerProposal, OwnerRule, System, SystemMember
from test_cmdb import _settings, db  # noqa: F401  (fixture)

T0 = dt.datetime(2026, 11, 1)
ADMIN = NS(username="tester", owner_admin=True, auth_source="local", is_admin=True)


def _vm(db, name, folder=None, pool=None, cluster="PRD", attrs=None):  # noqa: F811
    c = Ci(type="vm", name=name, source_kind="vcenter", source_key=f"u-{name}", status="active", first_seen=T0, last_seen=T0,
           attrs=json.dumps({"vcenterKey": "vc1", "cluster": cluster, "folderPath": folder, "resourcePool": pool,
                             "customAttrs": attrs or {}}))
    db.add(c)
    db.flush()
    return c.id


def _system(db, name, owner=None):  # noqa: F811
    return service.create(db, ADMIN, {"name": name, "ownerEmail": owner or ""}).id


def _rule(db, kind, pattern, sid, priority=100):  # noqa: F811
    db.add(OwnerRule(match_kind=kind, pattern=pattern, system_id=sid, priority=priority, enabled=True))


def _member(db, ci):  # noqa: F811
    m = db.query(SystemMember).filter_by(ci_id=ci).first()
    return (db.get(System, m.system_id).name, m.source) if m else None


# ---------------------------------------------------------------- vCenter helpers (no SOAP)
def test_folder_path_pool_and_custom_attrs_from_vcenter_objects():
    from app.vcenter import client as vc
    ref = lambda m: NS(_moId=m)  # noqa: E731
    folders = {"f-root": (None, {"name": "vm", "parent": ref("datacenter-1")}),
               "f-a": (None, {"name": "Projects", "parent": ref("f-root")}),
               "f-b": (None, {"name": "Bank", "parent": ref("f-a")})}
    assert vc.folder_path(folders, ref("f-b")) == "/Projects/Bank"
    assert vc.folder_path(folders, ref("f-root")) == "/"
    assert vc.folder_path(folders, None) is None and vc.folder_path({}, ref("f-b")) is None
    loop = {"x": (None, {"name": "x", "parent": ref("y")}), "y": (None, {"name": "y", "parent": ref("x")})}
    assert vc.folder_path(loop, ref("x")) in ("/y/x", "/x/y")                      # a cycle stops, never hangs
    pools = {"rp-1": (None, {"name": "Resources"}), "rp-2": (None, {"name": "RP-Web"})}
    assert (vc._pool_name(pools, ref("rp-1")), vc._pool_name(pools, ref("rp-2")), vc._pool_name(pools, None)) == (None, "RP-Web", None)
    values = [NS(key=101, value="Core Banking"), NS(key=102, value=" lead@x.vn "), NS(key=999, value="orphan"), NS(key=103, value="")]
    assert vc.custom_attrs(values, {101: "System", 102: "Owner", 103: "Empty"}) == {"System": "Core Banking", "Owner": "lead@x.vn"}


def test_demo_vms_carry_folders_and_attributes():
    from app.config import VCenterConfig
    from app.vcenter import demo_generator as dg
    _, vms = dg.collect_inventory(VCenterConfig("vcd", "VC", "demo", 443, "u", "p", False, demo=True))
    assert all(v["folderPath"] for v in vms)
    assert {v["folderPath"].split("/")[1] for v in vms} <= {"Projects", "Shared", "Discovered virtual machine", "Infrastructure"}
    with_attr = [v for v in vms if v["customAttrs"]]
    assert 0.15 * len(vms) < len(with_attr) < 0.5 * len(vms)
    _, again = dg.collect_inventory(VCenterConfig("vcd", "VC", "demo", 443, "u", "p", False, demo=True))
    assert [(v["vm"], v["folderPath"], v["customAttrs"]) for v in again] == [(v["vm"], v["folderPath"], v["customAttrs"]) for v in vms]
    _, pve = dg.collect_inventory(VCenterConfig("pv", "PVE", "demo", 443, "u", "p", False, demo=True, platform="proxmox"))
    assert all(v["folderPath"] is None and v["customAttrs"] == {} for v in pve)


# ---------------------------------------------------------------- priority
def test_four_overlapping_sources_follow_priority(db):  # noqa: F811
    s_man, s_attr, s_rule, s_itsm = (_system(db, n) for n in ("Manual", "Attr", "Rule", "Itsm"))
    _rule(db, "folder", "/Projects/Bank", s_rule)
    every = dict(folder="/Projects/Bank/Core", attrs={"System": "Attr"})
    v1 = _vm(db, "v1", **every)           # manual + attribute + rule + itsm
    v2 = _vm(db, "v2", **every)           # attribute + rule + itsm
    v3 = _vm(db, "v3", folder="/Projects/Bank/Core")   # rule + itsm
    v4 = _vm(db, "v4")                    # itsm only
    now = dt.datetime.utcnow()
    db.add_all([SystemMember(ci_id=v1, system_id=s_man, source="manual", assigned_at=now),
                SystemMember(ci_id=v2, system_id=s_itsm, source="itsm", assigned_at=now),
                SystemMember(ci_id=v3, system_id=s_itsm, source="itsm", assigned_at=now),
                SystemMember(ci_id=v4, system_id=s_itsm, source="itsm", assigned_at=now)])
    db.commit()
    res = rules.run(db)
    assert (_member(db, v1), _member(db, v2), _member(db, v3), _member(db, v4)) == \
        (("Manual", "manual"), ("Attr", "attribute"), ("Rule", "rule"), ("Itsm", "itsm"))
    assert (res["add"], res["change"], res["remove"], res["keptManual"]) == (0, 2, 0, 1)
    # second run: nothing to do
    assert {k: rules.run(db)[k] for k in ("add", "change", "remove")} == {"add": 0, "change": 0, "remove": 0}
    # an attribute that disappears falls back to the rule; a folder rule removed → assignment removed
    a = json.loads(db.get(Ci, v2).attrs)
    a["customAttrs"] = {}
    db.get(Ci, v2).attrs = json.dumps(a)
    db.commit()
    rules.run(db)
    assert _member(db, v2) == ("Rule", "rule")
    db.query(OwnerRule).delete()
    db.commit()
    assert rules.run(db)["remove"] == 2 and _member(db, v2) is None and _member(db, v1) == ("Manual", "manual")


def test_nested_folder_rules_priority_then_longest(db):  # noqa: F811
    bank, card, card2 = _system(db, "Bank"), _system(db, "Card"), _system(db, "Card bis")
    _rule(db, "folder", "/Projects/Bank", bank, priority=100)
    _rule(db, "folder", "/projects/bank/card/", card, priority=50)          # normalised: case, trailing slash
    vms = {n: _vm(db, n, folder=f) for n, f in (("core", "/Projects/Bank/Core"), ("card", "/Projects/Bank/Card"),
                                                  ("card-api", "/Projects/Bank/Card/API"), ("bankx", "/Projects/BankX"),
                                                  ("root", "/Projects"))}
    db.commit()
    rules.run(db)
    assert {n: (_member(db, c) or (None,))[0] for n, c in vms.items()} == \
        {"core": "Bank", "card": "Card", "card-api": "Card", "bankx": None, "root": None}
    # same priority: the longer (more specific) folder wins
    db.query(OwnerRule).delete()
    _rule(db, "folder", "/Projects/Bank", bank, priority=10)
    _rule(db, "folder", "/Projects/Bank/Card", card2, priority=10)
    db.commit()
    rules.run(db)
    assert _member(db, vms["card-api"]) == ("Card bis", "rule") and _member(db, vms["core"]) == ("Bank", "rule")


def test_other_rule_kinds(db):  # noqa: F811
    web, dwh, fin, ops = (_system(db, n) for n in ("Web", "DWH", "Finance", "Ops"))
    _rule(db, "resource_pool", "rp-web", web)
    _rule(db, "cluster", "DR-CLUSTER", ops)
    _rule(db, "attribute", "Phòng ban=Tài chính", fin)
    _rule(db, "name_regex", r"^dwh-\d+$", dwh)
    v = {"pool": _vm(db, "a1", pool="RP-Web"), "cl": _vm(db, "a2", cluster="dr-cluster"),
         "attr": _vm(db, "a3", attrs={"phong ban": "tài chính"}), "rx": _vm(db, "DWH-01"), "none": _vm(db, "dwh-x")}
    db.commit()
    rules.run(db)
    assert {k: (_member(db, c) or (None,))[0] for k, c in v.items()} == {"pool": "Web", "cl": "Ops", "attr": "Finance", "rx": "DWH", "none": None}


@pytest.mark.parametrize("pattern, ok", [
    (r"^web-\d+$", True), ("a" * 200, True), ("a" * 201, False), (r"(a+)+$", False), (r"(\w*)*x", False),
    (r"(a|b)\1", False), ("([a-z]+", False), (r"^(db|app)-[0-9]{2}$", True)])
def test_regex_guard(pattern, ok):
    if ok:
        assert rules.check_regex(pattern)
    else:
        with pytest.raises(rules.Invalid):
            rules.check_regex(pattern)


# ---------------------------------------------------------------- attributes → proposals
def test_owner_attribute_and_proposals(db):  # noqa: F811
    pay = _system(db, "Thanh toán", owner="lead.pay@corp.example")
    a = _vm(db, "pay-01", attrs={"Owner": "Lead.Pay@corp.example"})
    b = _vm(db, "crm-01", attrs={"System": "CRM", "Owner": "lead.crm@corp.example"})
    c = _vm(db, "crm-02", attrs={"System": "crm"})
    d = _vm(db, "x-01", attrs={"Owner": "nobody@corp.example"})
    db.commit()
    rules.run(db)
    assert _member(db, a) == ("Thanh toán", "attribute") and _member(db, b) is None
    props = {p.key: p for p in db.query(OwnerProposal)}
    assert set(props) == {"system:crm", "owner:nobody@corp.example"}
    crm = props["system:crm"]
    assert (crm.system_name, crm.owner_email, crm.vm_count, json.loads(crm.sample)) == ("CRM", "lead.crm@corp.example", 2, ["crm-01", "crm-02"])
    assert db.query(System).count() == 1                                      # never auto-created
    # accepting creates the system; the next run assigns both VMs
    s = service.create(db, ADMIN, {"name": crm.system_name, "ownerEmail": crm.owner_email})
    crm.status, crm.system_id = "accepted", s.id
    db.commit()
    rules.run(db)
    assert _member(db, b) == ("CRM", "attribute") and _member(db, c) == ("CRM", "attribute")
    assert _member(db, d) is None
    # configurable attribute names
    rules.save_attr_names(db, {"system": "Hệ thống", "owner": ""})
    db.commit()
    rules.run(db)
    assert _member(db, b) is None and _member(db, a) is None                  # "System" / "Owner" no longer read


# ---------------------------------------------------------------- preview = real run, API
def test_preview_matches_real_run_through_api(monkeypatch):
    from fastapi.testclient import TestClient
    from app.cmdb.models import CiKey, CiLink
    from app.database import SessionLocal, ensure_schema
    from app.main import app
    ensure_schema()
    s = SessionLocal()
    for m in (SystemMember, OwnerRule, OwnerProposal, System, CiLink, CiKey, Ci):
        s.query(m).delete()
    s.commit()
    ids = {n: _vm(s, n, folder=f) for n, f in (("w1", "/Projects/Web"), ("w2", "/Projects/Web/Api"), ("d1", "/Projects/DWH"),
                                                ("x1", "/Other"))}
    web, dwh = _system(s, "Web", "lead.web@corp.example"), _system(s, "DWH", "lead.dwh@corp.example")
    s.commit()
    s.close()
    try:
        with TestClient(app) as c:
            assert c.post("/api/auth/login", json={"username": "admin", "password": "Admin12345!"}).status_code == 200
            draft = {"rules": [{"matchKind": "folder", "pattern": "/Projects/Web", "systemId": web, "priority": 10},
                               {"matchKind": "folder", "pattern": "/Projects/DWH", "systemId": dwh, "priority": 20}],
                     "attributes": {"system": "System", "owner": "Owner"}}
            pv = c.post("/api/systems/rules/preview", json=draft).json()
            assert (pv["counts"]["add"], pv["counts"]["unassigned"]) == (3, 1)
            assert sorted(x["vm"] for x in pv["adds"]) == ["d1", "w1", "w2"]
            assert c.get("/api/systems/coverage").json()["owned"] == 0             # preview wrote nothing
            saved = c.put("/api/systems/rules", json=draft).json()["applied"]
            assert (saved["add"], saved["change"], saved["remove"]) == (pv["counts"]["add"], pv["counts"]["change"], pv["counts"]["remove"])
            assert c.get("/api/systems/coverage").json()["owned"] == 3
            got = c.get("/api/systems/rules").json()
            assert [r["pattern"] for r in got["rules"]] == ["/Projects/Web", "/Projects/DWH"] and got["pending"]["add"] == 0
            # change a rule: the preview announces exactly what the save does
            draft["rules"][1]["pattern"] = "/Other"
            pv2 = c.post("/api/systems/rules/preview", json=draft).json()["counts"]
            res2 = c.put("/api/systems/rules", json=draft).json()["applied"]
            assert (pv2["add"], pv2["remove"]) == (res2["add"], res2["remove"]) == (1, 1)
            bad = dict(draft, rules=[{"matchKind": "name_regex", "pattern": "(a+)+", "systemId": web}])
            assert c.post("/api/systems/rules/preview", json=bad).status_code == 400
            assert c.put("/api/systems/rules", json=dict(draft, rules=[{"matchKind": "folder", "pattern": "/x", "systemId": 999999}])).status_code == 400
            assert c.get("/api/systems/proposals").json() == {"items": []}
    finally:
        s = SessionLocal()
        for m in (SystemMember, OwnerRule, OwnerProposal, System, CiLink, CiKey, Ci):
            s.query(m).delete()
        s.commit()
        s.close()


def test_rule_endpoints_need_owner_admin():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        assert c.get("/api/systems/rules").status_code == 401


# ---------------------------------------------------------------- DEMO: folders + attributes cover ≥ 80 %
def test_demo_auto_assigns_at_least_80_percent(db, monkeypatch):  # noqa: F811
    from app.cmdb import builder
    from app.collectors import scheduler
    from app.config import VCenterConfig, get_settings
    from app.vcenter.demo_generator import DEMO_PROJECTS
    keys = ["vcd1", "vcd2"]
    for k in keys:
        assert scheduler.sync_vcenter(db, VCenterConfig(k, k, "demo", 443, "u", "p", False, demo=True), get_settings()).status == "ok"
    _settings(monkeypatch, vcs=keys, nbus=(), arrays=())
    builder.build_all(db)
    sids = {}
    for _, folder, system, owner in DEMO_PROJECTS:
        if system and system not in sids:
            sids[system] = _system(db, system, owner)
    for folder in ("/Projects/CoreBanking", "/Projects/Payments", "/Projects/Web", "/Projects/DataWarehouse", "/Shared/Internal"):
        name = next(s for _, f, s, _ in DEMO_PROJECTS if f.startswith(folder))
        _rule(db, "folder", folder, sids[name])
    db.commit()
    res = rules.run(db)
    total = db.query(Ci).filter(Ci.type == "vm", Ci.status == "active").count()
    from sqlalchemy import func
    by_source = dict(db.query(SystemMember.source, func.count(SystemMember.id)).group_by(SystemMember.source).all())
    assigned = sum(by_source.values())
    assert assigned / total >= 0.8, (assigned, total)
    assert by_source.get("attribute", 0) > 0 and by_source.get("rule", 0) > 0 and res["add"] == assigned
    assert rules.run(db)["add"] == 0
