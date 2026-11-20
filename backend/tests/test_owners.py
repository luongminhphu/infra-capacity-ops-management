"""ICO v2 G2a: Hệ thống & Owner — CRUD, permissions, assignment rules, coverage, Excel import / export."""
import io
import json

import pytest
from openpyxl import Workbook, load_workbook

from app.cmdb.models import Ci, CiKey, CiLink
from app.database import SessionLocal
from app.models import AuditLog, User
from app.owners import service
from app.owners.models import OwnerRule, System, SystemMember
from test_spend_opex import _login, admin, app_  # noqa: F401  (fixtures)

VMS = [("own-app-01", "vc-own", "u-own-1"), ("own-app-02", "vc-own", "u-own-2"), ("own-db-01", "vc-own", "u-own-3"),
       ("own-web-01", "vc-own", "u-own-4")]


def _wipe(s):
    for m in (SystemMember, OwnerRule, System, CiLink, CiKey, Ci):
        s.query(m).delete()
    s.query(User).filter(User.username.in_(["viewer1"])).update({"owner_admin": False}, synchronize_session=False)
    s.commit()


@pytest.fixture()
def vms(app_):  # noqa: F811
    s = SessionLocal()
    _wipe(s)
    ids = {}
    for name, vc, uuid in VMS:
        c = Ci(type="vm", name=name, source_kind="vcenter", source_key=uuid, status="active",
               attrs=json.dumps({"vcenterKey": vc, "cluster": "PRD", "host": "esx01", "powerOn": True}))
        s.add(c)
        s.flush()
        ids[name] = c.id
    s.commit()
    s.close()
    yield ids
    s = SessionLocal()
    _wipe(s)
    s.close()


def _sys(client, **kw):
    body = {"name": "Core Banking", "project": "CB", "department": "Khối Vận hành", "ownerEmail": "Lead.CB@Corp.Example",
            "ownerName": "Trần B", "criticality": 1, "env": "prod", **kw}
    return client.post("/api/systems", json=body)


# ---------------------------------------------------------------- CRUD + permissions
def test_crud_validation_and_audit(app_, admin, vms):  # noqa: F811
    r = _sys(admin)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["code"].startswith("SYS-") and len(a["code"]) == 8 and a["ownerEmail"] == "lead.cb@corp.example"
    b = _sys(admin, name="Website", ownerEmail="web@corp.example", criticality=3, department="Khối Kinh doanh").json()
    assert int(b["code"][4:]) == int(a["code"][4:]) + 1                               # sequence, never reused
    assert _sys(admin, name="core  banking").status_code == 400                       # same name (normalised)
    assert _sys(admin, name="X1", ownerEmail="not-an-email").json()["detail"].startswith("Email owner không đúng định dạng")
    assert _sys(admin, name="X2", criticality=5).status_code == 400
    assert _sys(admin, name="X3", env="qa").status_code == 400
    assert _sys(admin, name="X4", env="Production", criticality=4).json()["env"] == "prod"
    # list + filters + paging headers
    r = admin.get("/api/systems?criticality=1")
    assert [s["name"] for s in r.json()["items"]] == ["Core Banking"] and r.headers["X-Total-Count"] == "1"
    assert [s["name"] for s in admin.get("/api/systems?owner=web@").json()["items"]] == ["Website"]
    assert [s["name"] for s in admin.get("/api/systems?department=khối kinh doanh").json()["items"]] == ["Website"]
    assert admin.get("/api/systems?limit=1").headers["X-Has-More"] == "true"
    # owner change is audited as such
    assert admin.put(f"/api/systems/{a['id']}", json={"ownerEmail": "new.lead@corp.example"}).json()["ownerEmail"] == "new.lead@corp.example"
    s = SessionLocal()
    log = s.query(AuditLog).filter(AuditLog.action == "owners.system.owner_change").order_by(AuditLog.id.desc()).first()
    assert json.loads(log.detail)["changes"]["owner_email"] == ["lead.cb@corp.example", "new.lead@corp.example"]
    s.close()
    assert admin.delete(f"/api/systems/{b['id']}").json() == {"ok": True, "unassigned": 0}
    assert admin.get(f"/api/systems/{b['id']}").status_code == 404


def test_write_needs_owner_admin_role(app_, admin, vms):  # noqa: F811
    viewer = _login(app_, "viewer1")
    assert viewer.get("/api/systems").status_code == 200
    assert _sys(viewer).status_code == 403
    assert viewer.get("/api/auth/me").json()["ownerAdmin"] is False
    uid = next(u["id"] for u in admin.get("/api/admin/users").json()["items"] if u["username"] == "viewer1")
    assert viewer.put(f"/api/admin/users/{uid}/owner-admin", json={"ownerAdmin": True}).status_code == 403
    assert admin.put(f"/api/admin/users/{uid}/owner-admin", json={"ownerAdmin": True}).json()["ownerAdmin"] is True
    assert _sys(viewer, name="Viewer system").status_code == 200
    assert viewer.get("/api/auth/me").json()["ownerAdmin"] is True
    assert admin.get("/api/auth/me").json()["ownerAdmin"] is True                   # admins always may


# ---------------------------------------------------------------- assignment
def test_duplicate_assignment_blocked_move_and_manual_wins(app_, admin, vms):  # noqa: F811
    a = _sys(admin).json()
    b = _sys(admin, name="Website", ownerEmail="web@corp.example").json()
    vm = vms["own-app-01"]
    assert admin.post(f"/api/systems/{a['id']}/members", json={"ciIds": [vm, vms["own-app-02"]]}).json() == \
        {"added": 2, "moved": 0, "unchanged": 0}
    assert admin.post(f"/api/systems/{a['id']}/members", json={"ciIds": [vm]}).json()["unchanged"] == 1
    r = admin.post(f"/api/systems/{b['id']}/members", json={"ciIds": [vm]})
    assert r.status_code == 409 and "own-app-01" in r.json()["detail"]
    assert admin.post(f"/api/systems/{b['id']}/members", json={"ciIds": [vm], "move": True}).json()["moved"] == 1
    assert [m["vm"] for m in admin.get(f"/api/systems/{b['id']}/members").json()["items"]] == ["own-app-01"]
    assert admin.post(f"/api/systems/{a['id']}/members", json={"ciIds": [999999]}).status_code == 400
    # manual wins over automatic sources
    s = SessionLocal()
    try:
        assert service.assign_auto(s, vm, a["id"], "rule") == "kept_manual"
        assert service.assign_auto(s, vms["own-db-01"], a["id"], "attribute") == "added"
        s.commit()
        assert service.assign_auto(s, vms["own-db-01"], b["id"], "rule") == "updated"
        s.commit()
    finally:
        s.close()
    # a manual assignment replaces an automatic one without the move flag
    assert admin.post(f"/api/systems/{a['id']}/members", json={"ciIds": [vms["own-db-01"]]}).json()["moved"] == 1
    v = admin.get(f"/api/systems/vm/{vms['own-db-01']}").json()["system"]
    assert (v["code"], v["source"]) == (a["code"], "manual")
    assert admin.request("DELETE", f"/api/systems/{a['id']}/members", json={"ciIds": [vms["own-db-01"]]}).json() == {"removed": 1}


def test_coverage_counts_vms_with_owner(app_, admin, vms):  # noqa: F811
    a = _sys(admin).json()
    noowner = _sys(admin, name="Chưa có owner", ownerEmail="").json()
    assert noowner["ownerEmail"] is None
    admin.post(f"/api/systems/{a['id']}/members", json={"ciIds": [vms["own-app-01"], vms["own-app-02"]]})
    admin.post(f"/api/systems/{noowner['id']}/members", json={"ciIds": [vms["own-db-01"]]})
    c = admin.get("/api/systems/coverage").json()
    assert (c["total"], c["owned"], c["inSystemNoOwner"], c["unassigned"], c["ownedPct"]) == (4, 2, 1, 1, 50.0)
    assert [v["vm"] for v in c["items"]] == ["own-web-01"]


# ---------------------------------------------------------------- Excel
def _xlsx(systems, vm_rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Hệ thống"
    ws.append(["Mã", "Tên hệ thống", "Dự án", "Phòng ban", "Owner (email)", "Tên owner", "Người thay thế (email)",
               "Quản lý trực tiếp (email)", "Mức độ quan trọng", "Môi trường"])
    for r in systems:
        ws.append(r)
    wv = wb.create_sheet("VM")
    wv.append(["VM", "vCenter", "Hệ thống (mã hoặc tên)"])
    for r in vm_rows:
        wv.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _up(client, path, data):
    return client.post(path, files={"file": ("he-thong.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def test_excel_import_five_rows_with_two_errors_then_round_trip(app_, admin, vms):  # noqa: F811
    data = _xlsx([["", "Thanh toán", "PAY", "Khối Vận hành", "Pay.Lead@corp.example", "Lê C", "", "boss@corp.example", 2, "prod"],
                  ["", "Báo cáo", "RPT", "Khối Tài chính", "sai-email", "", "", "", 3, "uat"],
                  ["", "Kho dữ liệu", "DWH", "Khối Tài chính", "dwh@corp.example", "", "", "", "1", "dr"]],
                 [["own-app-01", "vc-own", "Thanh toán"], ["khong-ton-tai", "vc-own", "Thanh toán"]])
    p = _up(admin, "/api/systems/import/preview", data).json()
    assert p["counts"] == {"systemsNew": 2, "systemsUpdate": 0, "systemsSame": 0, "vmsNew": 1, "vmsMove": 0, "vmsSame": 0,
                           "errors": 2, "rows": 3}
    errs = sorted((e["sheet"], e["row"], e["error"][:20]) for e in p["errors"])
    assert errs == [("Hệ thống", 3, "Email owner không đú"), ("VM", 3, "Không có VM “khong-t")]
    s = SessionLocal()
    assert s.query(System).count() == 0                                             # preview writes nothing
    s.close()
    assert _up(admin, "/api/systems/import/commit", data).json() == \
        {"systemsNew": 2, "systemsUpdated": 0, "vmsAdded": 1, "vmsMoved": 0, "errors": 2}
    items = {x["name"]: x for x in admin.get("/api/systems").json()["items"]}
    assert set(items) == {"Thanh toán", "Kho dữ liệu"}
    assert (items["Thanh toán"]["ownerEmail"], items["Thanh toán"]["managerEmail"], items["Thanh toán"]["vmCount"]) == \
        ("pay.lead@corp.example", "boss@corp.example", 1)
    assert (items["Kho dữ liệu"]["criticality"], items["Kho dữ liệu"]["env"]) == (1, "dr")
    # export → same template → re-import finds nothing to change
    out = admin.get("/api/systems/export.xlsx")
    assert out.status_code == 200 and out.headers["content-type"].startswith("application/vnd.openxmlformats")
    wb = load_workbook(io.BytesIO(out.content))
    assert wb.sheetnames == ["Hệ thống", "VM"]
    again = _up(admin, "/api/systems/import/preview", out.content).json()["counts"]
    assert (again["systemsSame"], again["vmsSame"], again["rows"], again["errors"]) == (2, 1, 0, 0)
    # an unknown code is an error, a file without the sheets is refused
    bad = _xlsx([["SYS-9999", "Không có", "", "", "", "", "", "", 3, "prod"]], [])
    assert _up(admin, "/api/systems/import/preview", bad).json()["errors"][0]["error"].startswith("Không có hệ thống mã SYS-9999")
    wb2 = Workbook()
    wb2.active.title = "Khác"
    buf = io.BytesIO()
    wb2.save(buf)
    assert _up(admin, "/api/systems/import/preview", buf.getvalue()).status_code == 422
    assert _up(_login(app_, "viewer1"), "/api/systems/import/preview", data).status_code == 403


def test_vm_card_shows_system_and_owner(app_, admin, vms, monkeypatch):  # noqa: F811
    from app import data_access
    from app.services import entities
    entities._cache.clear()
    rows = [{"vm": "own-app-01", "cluster": "PRD", "host": "esx01", "vcpu": 2, "ramGB": 8, "isPoweredOn": True,
             "vcenterKey": "vc-own", "instanceUuid": "u-own-1"}]
    monkeypatch.setattr(data_access, "get_latest_combined", lambda db, keys=None, include_import=True: ([], rows, []))
    a = _sys(admin).json()
    admin.post(f"/api/systems/{a['id']}/members", json={"ciIds": [vms["own-app-01"]]})
    v = admin.get("/api/entities/vm?name=own-app-01&vcenter=vc-own").json()
    assert (v["system"]["code"], v["system"]["ownerEmail"], v["canAssignSystem"]) == (a["code"], "lead.cb@corp.example", True)
    entities._cache.clear()
    assert _login(app_, "viewer1").get("/api/entities/vm?name=own-app-01&vcenter=vc-own").json()["canAssignSystem"] is False
    entities._cache.clear()


# ---------------------------------------------------------------- support (project team) + backup name
def test_support_and_backup_fields(app_, admin, vms):  # noqa: F811
    r = _sys(admin, deputyEmail="Backup.CB@corp.example", deputyName="Lê C",
             supportEmails="Dev.A@corp.example; dev.b@corp.example dev.a@corp.example", supportName="Nhóm dự án CB",
             supportContact="Teams: #cb-support · 0901 234 567")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["supportEmails"] == ["dev.a@corp.example", "dev.b@corp.example"]           # lower case, de-duplicated, order kept
    assert (s["deputyEmail"], s["deputyName"], s["supportName"]) == ("backup.cb@corp.example", "Lê C", "Nhóm dự án CB")
    assert s["supportContact"] == "Teams: #cb-support · 0901 234 567"
    too_many = ",".join(f"d{i}@corp.example" for i in range(6))
    assert admin.put(f"/api/systems/{s['id']}", json={"supportEmails": too_many}).json()["detail"] == "Email support: tối đa 5 email."
    assert "không đúng định dạng" in admin.put(f"/api/systems/{s['id']}", json={"supportEmails": "x@"}).json()["detail"]
    r = admin.put(f"/api/systems/{s['id']}", json={"supportEmails": ["ops@corp.example"]})
    assert r.json()["supportEmails"] == ["ops@corp.example"] and r.json()["deputyName"] == "Lê C"   # partial update keeps the rest
    found = admin.get("/api/systems?owner=ops@corp").json()["items"]
    assert [x["id"] for x in found] == [s["id"]]                                          # owner filter also finds support
    assert admin.put(f"/api/systems/{s['id']}", json={"supportEmails": ""}).json()["supportEmails"] == []
    # clone = a new system with the same metadata (the UI posts the copied fields under a new name)
    c = _sys(admin, name="Core Banking DR", deputyEmail="backup.cb@corp.example", supportEmails="ops@corp.example").json()
    assert c["code"] != s["code"] and c["supportEmails"] == ["ops@corp.example"] and c["vmCount"] == 0


def test_excel_round_trip_keeps_support(app_, admin, vms):  # noqa: F811
    _sys(admin, deputyEmail="backup.cb@corp.example", deputyName="Lê C", supportEmails="dev.a@corp.example,dev.b@corp.example",
         supportName="Nhóm CB", supportContact="Teams #cb")
    x = admin.get("/api/systems/export.xlsx")
    ws = load_workbook(io.BytesIO(x.content))["Hệ thống"]
    rows = [[c.value for c in r] for r in ws.iter_rows() if r[0].value]
    hdr = next(r for r in rows if r[0] == "Mã")
    data = next(r for r in rows if str(r[0]).startswith("SYS-"))
    rec = dict(zip(hdr, data))
    assert rec["Backup (email)"] == "backup.cb@corp.example" and rec["Tên backup"] == "Lê C"
    assert rec["Support (email)"] == "dev.a@corp.example, dev.b@corp.example" and rec["Tên support"] == "Nhóm CB"
    assert rec["Liên hệ support"] == "Teams #cb"


# ---------------------------------------------------------------- "VM chưa có owner": filters with several values, select all
def test_unowned_filters_multi_values_and_select_all(app_, admin, vms):  # noqa: F811
    s = SessionLocal()
    extra = [("lot-core-01", "vc-own", "PRD", True, "/DC/LOT"), ("lot-core-02", "vc-b", "UAT", False, "/DC/LOT/uat"),
             ("lot-gw-01", "vc-b", "PRD", True, "/DC/GW")]
    for name, vc, cl, on, folder in extra:
        s.add(Ci(type="vm", name=name, source_kind="vcenter", source_key=f"u-{name}", status="active",
                 attrs=json.dumps({"vcenterKey": vc, "cluster": cl, "powerOn": on, "folderPath": folder})))
    s.commit()
    s.close()
    get = lambda **q: admin.get("/api/systems/coverage", params=q).json()  # noqa: E731
    names = lambda r: sorted(v["vm"] for v in r["items"])  # noqa: E731
    assert names(get(q="lot-core*")) == ["lot-core-01", "lot-core-02"]
    assert names(get(q="lot-gw, own-db")) == ["lot-gw-01", "own-db-01"]                 # several terms = any of them
    assert names(get(q="lot", vcenter="vc-b")) == ["lot-core-02", "lot-gw-01"]
    assert names(get(q="lot", vcenter="vc-b,vc-own", cluster="PRD")) == ["lot-core-01", "lot-gw-01"]
    assert names(get(q="lot", power="off")) == ["lot-core-02"]
    assert names(get(folder="dc/lot")) == ["lot-core-01", "lot-core-02"]
    r = get(q="lot", cluster="PRD")
    assert {x["value"]: x["count"] for x in r["facets"]["vcenters"]} == {"vc-b": 1, "vc-own": 1}     # counts under the cluster filter
    assert {x["value"]: x["count"] for x in r["facets"]["clusters"]} == {"PRD": 2, "UAT": 1}        # all clusters to choose from
    all_ids = get(q="lot", limit=1, ids="true")
    assert len(all_ids["items"]) == 1 and len(all_ids["ids"]) == 3 and all_ids["itemsTotal"] == 3
    assert admin.get("/api/systems/coverage", params={"power": "maybe"}).status_code == 422
    sid = _sys(admin).json()["id"]
    assert admin.post(f"/api/systems/{sid}/members", json={"ciIds": all_ids["ids"]}).json()["added"] == 3
    assert get(q="lot")["itemsTotal"] == 0
    assert admin.post(f"/api/systems/{sid}/members", json={"ciIds": list(range(service.MAX_SELECT + 1))}).status_code == 422
