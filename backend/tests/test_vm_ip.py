"""Rule of the product owner: every VM listing carries its IP (vm_ip.py) — owners lists, findings, backup clients."""
from app import capacity, vm_ip
from app.backup.service import _client_ips
from app.cmdb.models import CiKey
from app.database import SessionLocal
from test_owners import _sys, vms  # noqa: F401  (fixtures)
from test_spend_opex import admin, app_  # noqa: F401  (fixtures)

ROWS = [{"vm": "1CEK-APP-TX01", "vcenterKey": "prod", "ipAddress": "10.70.1.21", "guestHostname": "ektx01.hsc.local"},
        {"vm": "app-02", "vcenterKey": "uat", "ipAddress": "10.80.0.5"},
        {"vm": "no-ip", "vcenterKey": "prod", "ipAddress": None}]


def test_index_lookup_by_vcenter_name_and_fqdn(monkeypatch):
    from app import data_access
    monkeypatch.setattr(data_access, "get_latest_combined", lambda db, keys=None, include_import=True: ([], ROWS, []))
    vm_ip.invalidate()
    idx = vm_ip.index(None)
    assert vm_ip.ip_of(idx, "1cek-app-tx01", "prod") == "10.70.1.21"
    assert vm_ip.ip_of(idx, "1CEK-APP-TX01", "uat") is None                 # another vCenter: no guess
    assert vm_ip.ip_of(idx, "app-02.corp.local") == "10.80.0.5"              # backup client FQDN → VM short name
    rows = [{"vm": "app-02", "vcenterKey": "uat"}, {"vm": "no-ip", "vcenterKey": "prod"}, {"vm": "x", "vcenterKey": "prod", "ip": "1.1.1.1"}]
    vm_ip.attach(None, rows)
    assert [r.get("ip") for r in rows] == ["10.80.0.5", None, "1.1.1.1"]
    alarms = [{"entityType": "vm", "entity": "app-02", "vcenterKey": "uat"}, {"entityType": "host", "entity": "app-02", "vcenterKey": "uat"}]
    vm_ip.attach(None, alarms, vm_key="entity", only=lambda a: a["entityType"] == "vm")
    assert alarms[0]["ip"] == "10.80.0.5" and "ip" not in alarms[1]
    vm_ip.invalidate()


def test_vm_findings_carry_the_ip_without_changing_the_code():
    vm = {"vm": "idle-01", "cluster": "C1", "vcenterKey": "vc1", "isPoweredOn": True, "lastActivityDays": 90, "ipAddress": "10.1.1.9",
          "vcpu": 2}
    found = [f for f in capacity.build_compliance_findings([], [vm], capacity.Policy(idle_days=30)) if f["type"] == "vm_idle"]
    assert found and found[0]["ip"] == "10.1.1.9"
    assert found[0]["code"] == capacity.finding_code("vm_idle", "C1", "idle-01", "vc1")      # code unchanged by the new field


def test_backup_clients_and_failed_jobs_get_the_vm_ip():
    rep = {"clients": [{"client": "ektx01.hsc.local", "key": "ektx01"}, {"client": "10.80.0.5", "key": "10.80.0.5"},
                       {"client": "ghost", "key": "ghost"}],
           "failedJobs": [{"client": "APP-02.corp.local"}]}
    _client_ips(rep, ROWS)
    assert [c.get("ip") for c in rep["clients"]] == ["10.70.1.21", "10.80.0.5", None]   # by guest hostname, by IP, unknown
    assert rep["failedJobs"][0]["ip"] == "10.80.0.5"


def test_owner_lists_show_ip_and_unowned_search_matches_ip(app_, admin, vms):  # noqa: F811
    s = SessionLocal()
    s.add(CiKey(ci_id=vms["own-db-01"], kind="ip", value_norm="10.9.8.7"))
    s.commit()
    s.close()
    import app.vm_ip as m
    m.invalidate()
    real = m.index
    m.index = lambda db: {("vc-own", "own-db-01"): "10.9.8.7", ("", "own-db-01"): "10.9.8.7",
                          ("vc-own", "own-app-01"): "10.9.8.1", ("", "own-app-01"): "10.9.8.1"}
    try:
        cov = admin.get("/api/systems/coverage?q=10.9.8").json()
        assert [(i["vm"], i["ip"]) for i in cov["items"]] == [("own-db-01", "10.9.8.7")]    # IP search via the CI key
        a = _sys(admin).json()
        admin.post(f"/api/systems/{a['id']}/members", json={"ciIds": [vms["own-app-01"]]})
        mem = admin.get(f"/api/systems/{a['id']}/members").json()["items"]
        assert [(x["vm"], x["ip"]) for x in mem] == [("own-app-01", "10.9.8.1")]
    finally:
        m.index = real
        m.invalidate()
        s = SessionLocal()
        s.query(CiKey).filter(CiKey.value_norm == "10.9.8.7").delete()
        s.commit()
        s.close()
