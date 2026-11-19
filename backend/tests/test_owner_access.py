"""ICO v2 G2c: owner access scope (deny by default, owner portal, IDOR) and the owner filter across pages / reports."""
import json
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import auth_entra
from app.cmdb.models import Ci, CiKey, CiLink
from app.database import SessionLocal
from app.models import User
from app.owners.models import System, SystemMember
from app.security import hash_password
from test_auth_entra import _finish, _start, entra  # noqa: F401  (fixture)
from test_spend_opex import PW, _login, admin, app_  # noqa: F401  (fixtures)


# ---------------------------------------------------------------- scope decision (pure)
def _s(**kw):
    base = dict(entra_owner_role="ICO.Owner", entra_admin_roles="ICO.Admin", entra_allowed_roles="", entra_allowed_groups="",
                entra_spend_viewer_roles="ICO.Spend.Viewer", entra_spend_editor_roles="ICO.Spend.Editor",
                entra_spend_approver_roles="ICO.Spend.Approver")
    base.update(kw)
    s = NS(**base)
    s.entra_owner_role_list = lambda: [r for r in s.entra_owner_role.split(",") if r]
    s.entra_admin_role_list = lambda: [r for r in s.entra_admin_roles.split(",") if r]
    return s


@pytest.mark.parametrize("roles, groups, extra, want", [
    ([], [], {}, "full"),                                        # no owner role: unchanged behaviour
    (["ICO.Owner"], [], {}, "owner"),
    (["ICO.Owner", "ICO.Admin"], [], {}, "full"),
    (["ICO.Owner", "ICO.Spend.Viewer"], [], {}, "full"),
    (["ICO.Owner", "ICO.Ops"], [], {"entra_allowed_roles": "ICO.Ops"}, "full"),
    (["ICO.Owner"], ["g-ops"], {"entra_allowed_groups": "g-ops"}, "full"),
    (["ICO.Owner"], ["g-other"], {"entra_allowed_groups": "g-ops"}, "owner"),
    (["ICO.Viewer"], [], {}, "full"),
])
def test_access_scope_for(roles, groups, extra, want):
    assert auth_entra.access_scope_for(_s(**extra), {"roles": roles, "groups": groups}) == want


# ---------------------------------------------------------------- fixtures: two owners, their systems and VMs
def _wipe(s):
    for m in (SystemMember, System, CiLink, CiKey, Ci):
        s.query(m).delete()
    s.query(User).filter(User.username.in_(["owner.a@corp.example", "owner.b@corp.example"])).delete(synchronize_session=False)
    s.commit()


def _vm(s, name, uuid, vc="vc-acc", cluster="PRD"):
    c = Ci(type="vm", name=name, source_kind="vcenter", source_key=uuid, status="active",
           attrs=json.dumps({"vcenterKey": vc, "cluster": cluster, "powerOn": True, "vcpu": 2, "ramGB": 8}))
    s.add(c)
    s.flush()
    s.add(CiKey(ci_id=c.id, kind="instance_uuid", value_norm=uuid))
    return c.id


@pytest.fixture()
def owners(app_):  # noqa: F811
    from app.owners import service
    s = SessionLocal()
    _wipe(s)
    for name in ("owner.a@corp.example", "owner.b@corp.example"):
        s.add(User(username=name, password_hash=hash_password(PW), email=name, access_scope="owner"))
    sa = System(code="SYS-9001", name="Hệ thống A", name_norm="he thong a", owner_email="owner.a@corp.example",
                department="Khối A", project="PA", criticality=1, env="prod")
    sb = System(code="SYS-9002", name="Hệ thống B", name_norm="he thong b", owner_email="owner.b@corp.example",
                deputy_email="owner.a@corp.example", department="Khối B", project="PB", criticality=3, env="prod")
    sc = System(code="SYS-9003", name="Hệ thống C", name_norm="he thong c", owner_email="someone@corp.example",
                department="Khối B", project="PC", criticality=2, env="uat")
    s.add_all([sa, sb, sc])
    s.flush()
    vms = {n: _vm(s, n, u, cluster=cl) for n, u, cl in (("acc-a1", "u-a1", "PRD"), ("acc-a2", "u-a2", "PRD"),
                                                         ("acc-b1", "u-b1", "PRD"), ("acc-c1", "u-c1", "UAT"),
                                                         ("acc-free", "u-free", "DEV"))}
    for vm, sys_ in (("acc-a1", sa), ("acc-a2", sa), ("acc-b1", sb), ("acc-c1", sc)):
        s.add(SystemMember(ci_id=vms[vm], system_id=sys_.id, source="manual"))
    s.commit()
    ids = {"A": sa.id, "B": sb.id, "C": sc.id, **vms}
    s.close()
    service.invalidate_scope_cache()
    yield ids
    s = SessionLocal()
    _wipe(s)
    s.close()
    service.invalidate_scope_cache()


# ---------------------------------------------------------------- deny by default
OLD_ENDPOINTS = ["/api/data/latest", "/api/compliance/findings", "/api/dashboard/trend", "/api/systems", "/api/systems/coverage",
                 "/api/spend/meta", "/api/ci", "/api/search?q=acc", "/api/admin/users", "/api/backup/summary", "/api/infra/snapshots",
                 "/api/inbox", "/api/settings", "/api/vcenters", "/api/reports/pdf", "/api/entities/vm?name=acc-a1"]


def test_owner_scope_is_refused_everywhere_but_me_and_portal(app_, owners):  # noqa: F811
    a = _login(app_, "owner.a@corp.example")
    me = a.get("/api/auth/me").json()
    assert (me["accessScope"], me["isAdmin"], me["ownerAdmin"]) == ("owner", False, False)
    for path in OLD_ENDPOINTS:
        r = a.get(path)
        assert r.status_code == 403, (path, r.status_code)
        assert r.json()["detail"] == "Tài khoản owner chỉ dùng được cổng Owner."
    assert a.post("/api/systems", json={"name": "x"}).status_code == 403
    assert a.get("/api/owner/systems").status_code == 200
    assert a.post("/api/auth/logout").status_code == 200
    assert a.get("/api/owner/systems").status_code == 401                    # signed out on the server too


def test_owner_sees_only_own_and_deputy_systems(app_, owners):  # noqa: F811
    a, b = _login(app_, "owner.a@corp.example"), _login(app_, "owner.b@corp.example")
    assert [x["code"] for x in a.get("/api/owner/systems").json()["items"]] == ["SYS-9001", "SYS-9002"]   # owner + deputy
    assert [x["code"] for x in b.get("/api/owner/systems").json()["items"]] == ["SYS-9002"]
    assert sorted(v["vm"] for v in a.get("/api/owner/vms").json()["items"]) == ["acc-a1", "acc-a2", "acc-b1"]
    assert [v["vm"] for v in b.get("/api/owner/vms").json()["items"]] == ["acc-b1"]


def test_idor_foreign_or_guessed_system_answers_404(app_, owners):  # noqa: F811
    b = _login(app_, "owner.b@corp.example")
    for sid in (owners["A"], owners["C"], 999999):
        r = b.get(f"/api/owner/vms?system={sid}")
        assert r.status_code == 404 and r.json()["detail"] == "Không có hệ thống này."      # foreign = missing: no enumeration
    assert [v["vm"] for v in b.get(f"/api/owner/vms?system={owners['B']}").json()["items"]] == ["acc-b1"]
    # no parameter can widen the scope
    assert b.get(f"/api/owner/vms?system={owners['A']}&systems={owners['A']}").status_code == 404
    assert [v["vm"] for v in b.get(f"/api/owner/vms?owners=owner.a@corp.example").json()["items"]] == ["acc-b1"]


def test_owner_without_systems_sees_nothing(app_, owners):  # noqa: F811
    s = SessionLocal()
    s.query(System).filter(System.id == owners["B"]).update({"deputy_email": None})
    s.commit()
    s.close()
    lonely = _login(app_, "owner.b@corp.example")
    s = SessionLocal()
    s.query(System).filter(System.id == owners["B"]).update({"owner_email": "nobody@corp.example"})
    s.commit()
    s.close()
    assert lonely.get("/api/owner/systems").json()["items"] == [] and lonely.get("/api/owner/vms").json() == {"items": [], "total": 0}


def test_full_user_unaffected_and_sees_all_in_portal(app_, admin, owners):  # noqa: F811
    assert admin.get("/api/auth/me").json()["accessScope"] == "full"
    assert admin.get("/api/systems").status_code == 200
    assert [x["code"] for x in admin.get("/api/owner/systems").json()["items"]] == ["SYS-9001", "SYS-9003", "SYS-9002"]
    assert admin.get(f"/api/owner/vms?system={owners['C']}").json()["total"] == 1


# ---------------------------------------------------------------- Entra: role change → scope change + old session ended
def test_entra_owner_role_changes_scope_and_ends_old_sessions(entra):  # noqa: F811
    from app.main import app
    s = SessionLocal()
    s.query(User).filter(User.username == "alice@corp.example").delete()
    s.commit()
    s.close()
    with TestClient(app) as c:
        entra.claims = {"roles": ["ICO.Owner"]}
        _finish(c, _start(c, entra))
        assert c.get("/api/auth/me").json()["accessScope"] == "owner"
        assert c.get("/api/data/latest").status_code == 403
        old = c.cookies.get("vco_session")
        entra.claims = {"roles": ["ICO.Owner", "ICO.Admin"]}
        c.cookies.clear()
        _finish(c, _start(c, entra))
        assert c.get("/api/auth/me").json()["accessScope"] == "full" and c.get("/api/data/latest").status_code == 200
    with TestClient(app) as stale:
        stale.cookies.set("vco_session", old)
        assert stale.get("/api/auth/me").status_code == 401                   # the owner-scope session is gone
    s = SessionLocal()
    s.query(User).filter(User.username == "alice@corp.example").delete()
    s.commit()
    s.close()


def test_owner_role_may_sign_in_when_roles_are_restricted(entra, monkeypatch):  # noqa: F811
    from app.config import get_settings
    from app.main import app
    monkeypatch.setattr(get_settings(), "entra_allowed_roles", "ICO.Ops")
    with TestClient(app) as c:
        entra.claims = {"roles": ["ICO.Owner"], "oid": "oid-own", "preferred_username": "own@corp.example"}
        r = _finish(c, _start(c, entra))
        assert r.status_code == 302 and "login_error" not in r.headers["location"]
        assert c.get("/api/auth/me").json()["accessScope"] == "owner"
        entra.claims = {"roles": ["ICO.Viewer"], "oid": "oid-x", "preferred_username": "x@corp.example"}
        c.cookies.clear()
        assert "login_error" in _finish(c, _start(c, entra)).headers["location"]
    s = SessionLocal()
    s.query(User).filter(User.username.in_(["own@corp.example", "x@corp.example"])).delete(synchronize_session=False)
    s.commit()
    s.close()


# ---------------------------------------------------------------- owner filter on pages and reports (full users)
HOSTS = [{"host": "esx-p1", "cluster": "PRD", "cores": 32, "ramGB": 512, "vcenterKey": "vc-acc"},
         {"host": "esx-u1", "cluster": "UAT", "cores": 32, "ramGB": 512, "vcenterKey": "vc-acc"},
         {"host": "esx-d1", "cluster": "DEV", "cores": 32, "ramGB": 512, "vcenterKey": "vc-acc"}]


def _row(vm, uuid, host, cluster, cpu=3):
    return {"vm": vm, "instanceUuid": uuid, "host": host, "cluster": cluster, "vcenterKey": "vc-acc", "vcpu": 4, "ramGB": 16,
            "isPoweredOn": True, "powerState": "Powered On", "cpuUsagePctRaw": cpu, "ramUsagePctRaw": 20, "diskProvGB": 100,
            "diskUsedGB": 50, "datastore": "DS1"}


VMS = [_row("acc-a1", "u-a1", "esx-p1", "PRD"), _row("acc-a2", "u-a2", "esx-p1", "PRD"), _row("acc-b1", "u-b1", "esx-p1", "PRD"),
       _row("acc-c1", "u-c1", "esx-u1", "UAT"), _row("acc-free", "u-free", "esx-d1", "DEV"),
       _row("ACC-A1-RENAMED", "u-a1-clone", "esx-d1", "DEV")]


@pytest.fixture()
def latest(monkeypatch):
    from app import data_access
    monkeypatch.setattr(data_access, "get_latest_combined", lambda db, keys=None, include_import=True: (HOSTS, VMS, []))


def test_owner_filter_on_capacity_compliance_inventory_csv(app_, admin, owners, latest):  # noqa: F811
    d = admin.get(f"/api/data/latest?systems={owners['A']}").json()
    assert sorted(v["vm"] for v in d["vms"]) == ["acc-a1", "acc-a2"] and [h["host"] for h in d["hosts"]] == ["esx-p1"]
    assert d["ownerScope"] == "Đang lọc theo owner — hệ thống: SYS-9001"            # codes, not ids, in banners / PDFs
    assert admin.get("/api/data/latest").json()["ownerScope"] is None and len(admin.get("/api/data/latest").json()["vms"]) == 6
    # by code, owner e-mail (owner or deputy), department, project, criticality — AND across dimensions
    assert sorted(v["vm"] for v in admin.get("/api/data/latest?systems=sys-9002,SYS-9003").json()["vms"]) == ["acc-b1", "acc-c1"]
    assert sorted(v["vm"] for v in admin.get("/api/data/latest?owners=Owner.A@corp.example").json()["vms"]) == ["acc-a1", "acc-a2", "acc-b1"]
    assert sorted(v["vm"] for v in admin.get("/api/data/latest?departments=khối b").json()["vms"]) == ["acc-b1", "acc-c1"]
    assert [v["vm"] for v in admin.get("/api/data/latest?departments=Khối B&criticality=2").json()["vms"]] == ["acc-c1"]
    assert [v["vm"] for v in admin.get("/api/data/latest?projects=PA&criticality=3").json()["vms"]] == []
    assert admin.get("/api/data/latest?systems=").json()["vms"] == []              # explicit empty selection = nothing
    f = admin.get(f"/api/compliance/findings?systems={owners['C']}").json()["findings"]
    assert {x.get("cluster") for x in f} <= {"UAT", "—"}
    csv = admin.get(f"/api/export/csv?type=vms&systems={owners['A']}").text
    assert "acc-a1" in csv and "acc-b1" not in csv and "acc-free" not in csv


def test_owner_filter_on_pdf(app_, admin, owners, latest):  # noqa: F811
    r = admin.get(f"/api/reports/pdf?view=technical&systems={owners['A']}")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert admin.get("/api/reports/pdf?view=technical&projects=none").status_code == 404


def test_owner_filter_on_snapshots_and_vm_alarms(app_, admin, owners):  # noqa: F811
    import datetime as dt
    from app.models import VcAlarmState, VmSnapshotState
    s = SessionLocal()
    now = dt.datetime.utcnow()
    s.add_all([VmSnapshotState(vcenter_key="vc-acc", vm="acc-a1", instance_uuid="u-a1", cluster="PRD", snapshot_count=1, oldest_at=now),
               VmSnapshotState(vcenter_key="vc-acc", vm="acc-free", instance_uuid="u-free", cluster="DEV", snapshot_count=1, oldest_at=now),
               VcAlarmState(vcenter_key="vc-acc", alarm_key="k1", entity_type="vm", entity="acc-b1", cluster="PRD", alarm="CPU", status="red"),
               VcAlarmState(vcenter_key="vc-acc", alarm_key="k2", entity_type="host", entity="esx-p1", cluster="PRD", alarm="HW", status="red")])
    s.commit()
    try:
        assert [x["vm"] for x in admin.get(f"/api/infra/snapshots?systems={owners['A']}").json() if x["vcenterKey"] == "vc-acc"] == ["acc-a1"]
        al = [x for x in admin.get(f"/api/infra/alarms?systems={owners['B']}").json() if x["vcenterKey"] == "vc-acc"]
        assert [(x["entityType"], x["entity"]) for x in al] == [("vm", "acc-b1")]
        assert len([x for x in admin.get("/api/infra/alarms").json() if x["vcenterKey"] == "vc-acc"]) == 2
    finally:
        s.query(VmSnapshotState).filter(VmSnapshotState.vcenter_key == "vc-acc").delete()
        s.query(VcAlarmState).filter(VcAlarmState.vcenter_key == "vc-acc").delete()
        s.commit()
        s.close()


def test_owner_filter_on_backup_coverage(app_, admin, owners, latest):  # noqa: F811
    full = admin.get("/api/backup/summary").json()
    part = admin.get(f"/api/backup/summary?systems={owners['A']}").json()
    assert part["ownerScope"].startswith("Đang lọc theo owner")
    if full.get("coverage", {}).get("available"):
        assert part["coverage"]["total"] == 2 and part["coverage"]["total"] < full["coverage"]["total"]
    keys = {c.get("key") for c in part.get("clients") or []}
    assert keys <= {"acc-a1", "acc-a2"}


# ---------------------------------------------------------------- guard: every /api route is closed to owner scope
# Endpoints allowed to skip get_current_user. Adding one here is a security decision: say why in the comment.
NOT_FULL_ONLY = {
    "/api/health", "/api/health/ready", "/api/public/branding",                      # public
    "/api/auth/config", "/api/auth/login", "/api/auth/logout", "/api/auth/oidc/login", "/api/auth/oidc/callback",
    "/api/auth/passkey/login/options", "/api/auth/passkey/login/verify",              # sign-in itself
    "/api/auth/passkey/invite", "/api/auth/passkey/register/options", "/api/auth/passkey/register/verify",  # invite link / own passkey
    "/api/auth/me",                                                                   # any scope: picks the shell
    "/api/owner/systems", "/api/owner/vms",                                           # owner portal (get_owner_user)
    "/api/owner/reviews", "/api/owner/reviews/{cid}", "/api/owner/reviews/{cid}/items",  # owner's review decisions (G3b)
}


def test_every_api_route_requires_full_scope_unless_allowlisted():
    from fastapi.routing import APIRoute
    from app.main import app
    from app.owners.access import get_owner_user
    from app.security import get_current_user

    def calls(d, acc):
        for x in d.dependencies:
            acc.add(x.call)
            calls(x, acc)
        return acc
    open_routes = set()
    for r in app.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/api"):
            c = calls(r.dependant, set())
            if get_current_user not in c:
                open_routes.add(r.path)
            if r.path.startswith("/api/owner/"):
                assert get_owner_user in c, r.path                                     # portal = owner-scoped principal
    assert open_routes == NOT_FULL_ONLY, sorted(open_routes ^ NOT_FULL_ONLY)


def test_owner_filter_on_executive_aggregate(app_, admin, owners, latest):  # noqa: F811
    plain = admin.get("/api/dashboard/executive").json()
    assert "ownerScope" not in plain                                              # contract of the unfiltered response
    a = admin.get(f"/api/dashboard/executive?systems={owners['B']}").json()
    assert a["ownerScope"] == "Đang lọc theo owner — hệ thống: SYS-9002"
