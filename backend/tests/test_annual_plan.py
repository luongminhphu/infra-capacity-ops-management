"""ICO v2 G8: annual demand review + next year's plan — fixed data set (12 requests / 3 departments), every number checked;
then the database path (build → PDF / Excel numbers, push to the budget plan once, updated on re-run)."""
import datetime as dt
import json

import pytest

from app import planning
from app.capacity import Policy
from app.sizing import HostProfile
from test_cmdb import db  # noqa: F401  (fixture: private SQLite)

D = dt.datetime
A, B, C = "Khối CNTT", "Khối Ngân hàng số", "Khối Vận hành"


def _ln(rid, dep, created, days, project, env, vms, vcpu, ram, disk, kind="new_vm"):
    return {"requestId": rid, "kind": kind, "createdAt": created, "resolvedAt": created + dt.timedelta(days=days) if days is not None else None,
            "department": dep, "project": project, "env": env, "vms": vms, "vcpu": vcpu, "ramGB": ram, "diskGB": disk}


LINES = [
    _ln(1, A, D(2026, 1, 10, 9), 2, "Core", "PROD", 2, 8, 32, 200),
    _ln(2, A, D(2026, 2, 5, 9), 4, "Core", "UAT", 1, 4, 16, 100),
    _ln(3, A, D(2026, 4, 1, 9), 6, "CRM", "PROD", 1, 8, 32, 200),
    _ln(4, A, D(2026, 7, 15, 9), None, "CRM", "PROD", 1, 4, 8, 100),
    _ln(5, B, D(2026, 3, 20, 9), 1, "Mobile", "PROD", 3, 12, 48, 300),
    _ln(6, B, D(2026, 5, 2, 9), 3, "Mobile", "UAT", 1, 2, 4, 50),
    _ln(7, B, D(2026, 8, 8, 9), 8, "Mobile", "PROD", 2, 16, 64, 400),
    _ln(8, B, D(2026, 10, 10, 9), 2, "Data", "PROD", 1, 8, 64, 1000),
    _ln(9, C, D(2026, 6, 6, 9), 5, "Ops", "PROD", 1, 2, 4, 60),
    _ln(10, C, D(2026, 11, 11, 9), 10, "Ops", None, 1, 4, 8, 80),
    _ln(11, C, D(2026, 9, 9, 9), 1, "Ops", "PROD", 0, 2, 8, 0, kind="resize"),      # change To-be − As-is
    _ln(12, C, D(2025, 12, 31, 9), 1, "Ops", "PROD", 1, 64, 256, 1000),            # previous year: not counted
]
ITEMS = [   # VMs created by requests: requested → provisioned → used 90 / 180 days later
    {"department": A, "reqVcpu": 4, "reqRamGB": 16, "provVcpu": 4, "provRamGB": 16, "used90": {"vcpu": 1.0, "ramGB": 8}, "used180": {"vcpu": 1.2, "ramGB": 9.6}},
    {"department": A, "reqVcpu": 8, "reqRamGB": 32, "provVcpu": 8, "provRamGB": 32, "used90": {"vcpu": 2.0, "ramGB": 16}, "used180": None},
    {"department": B, "reqVcpu": 4, "reqRamGB": 16, "provVcpu": 4, "provRamGB": 16, "used90": {"vcpu": 3.0, "ramGB": 12}, "used180": {"vcpu": 3.2, "ramGB": 12.8}},
    {"department": B, "reqVcpu": 4, "reqRamGB": 16, "provVcpu": 4, "provRamGB": 16, "used90": {"vcpu": 4.0, "ramGB": 16}, "used180": {"vcpu": 6.0, "ramGB": 24}},
    {"department": C, "reqVcpu": 2, "reqRamGB": 4, "provVcpu": 2, "provRamGB": 4, "used90": None, "used180": None},
]


def _row(rows, key):
    return next({k: r[k] for k in ("requests", "vms", "vcpu", "ramGB", "diskGB")} for r in rows if r["key"] == key)


def test_demand_summary_totals_quarters_groups_and_median():
    s = planning.demand_summary(LINES, 2026)
    t = s["totals"]
    assert (t["requests"], t["vms"], t["vcpu"], t["ramGB"], t["diskGB"]) == (11, 14, 70, 288, 2490)
    assert (t["medianDays"], t["resolved"]) == (3.5, 10)        # 1,1,2,2,3 | 4,5,6,8,10 → (3 + 4) / 2
    assert [(q["key"], q["requests"], q["vms"], q["vcpu"], q["ramGB"], q["diskGB"]) for q in s["byQuarter"]] == [
        ("Q1", 3, 6, 24, 96, 600), ("Q2", 3, 3, 12, 40, 310), ("Q3", 3, 3, 22, 80, 500), ("Q4", 2, 2, 12, 72, 1080)]
    assert [r["key"] for r in s["byDepartment"]] == [B, A, C]
    assert _row(s["byDepartment"], A) == {"requests": 4, "vms": 5, "vcpu": 24, "ramGB": 88, "diskGB": 600}
    assert _row(s["byDepartment"], B) == {"requests": 4, "vms": 7, "vcpu": 38, "ramGB": 180, "diskGB": 1750}
    assert _row(s["byDepartment"], C) == {"requests": 3, "vms": 2, "vcpu": 8, "ramGB": 20, "diskGB": 140}
    assert _row(s["byProject"], "Mobile") == {"requests": 3, "vms": 6, "vcpu": 30, "ramGB": 116, "diskGB": 750}
    assert _row(s["byEnv"], "Chưa rõ") == {"requests": 1, "vms": 1, "vcpu": 4, "ramGB": 8, "diskGB": 80}
    assert _row(s["byEnv"], "PROD")["vcpu"] == 60
    assert planning.demand_summary([], 2026)["totals"]["medianDays"] is None


def test_fulfilment_ratio_per_department_and_factor_capped_at_one():
    f = planning.fulfilment(ITEMS)
    dep = {d["department"]: d for d in f["departments"]}
    a, b, c = dep[A], dep[B], dep[C]
    assert (a["vms"], a["reqVcpu"], a["vms90"], a["used90Vcpu"], a["used90RamGB"], a["vms180"]) == (2, 12, 2, 3.0, 24, 1)
    assert (a["horizon"], a["ratioVcpu"], a["ratioRamGB"], a["factorVcpu"], a["factorRamGB"]) == (180, 0.3, 0.6, 0.3, 0.6)   # 1,2/4 · 9,6/16
    assert (b["ratioVcpu"], b["ratioRamGB"], b["factorVcpu"], b["factorRamGB"]) == (1.15, 1.15, 1.0, 1.0)                  # 9,2/8 · 36,8/32
    assert (c["horizon"], c["ratioVcpu"], c["factorVcpu"]) == (None, None, None)
    o = f["overall"]
    assert (o["vms"], o["reqVcpu"], o["reqRamGB"], o["ratioVcpu"], o["ratioRamGB"]) == (5, 22, 84, 0.87, 0.97)   # 10,4/12 · 46,4/48


def test_organic_growth_subtracts_request_vms_and_scales_to_a_year():
    series = [{"date": "2026-01-01", "usedVcpu": 500, "usedRamGB": 1000}, {"date": "2026-07-01", "usedVcpu": 560, "usedRamGB": 1100}]
    g = planning.organic_growth(series, {"vcpu": 20, "ramGB": 40})                  # 181 days
    assert (g["spanDays"], g["observedVcpu"], g["observedRamGB"]) == (181, 60, 100)
    assert (g["vcpu"], g["ramGB"]) == (round(40 * 365 / 181, 1), round(60 * 365 / 181, 1)) == (80.7, 121.0)
    assert planning.organic_growth(series, {"vcpu": 100, "ramGB": 0})["vcpu"] == 0          # never negative
    assert planning.organic_growth(series[:1], {})["vcpu"] == 0


CLUSTERS = [
    {"name": "PRD", "hosts": [{"cores": 32, "ramGB": 512}] * 3, "usedVcpu": 600, "usedRamGB": 1100, "organicVcpu": 60, "organicRamGB": 100},
    {"name": "DEV", "hosts": [{"cores": 16, "ramGB": 256}] * 2, "usedVcpu": 100, "usedRamGB": 300, "organicVcpu": 0, "organicRamGB": 0},
]
KNOWN = [
    {"label": "#501 crm-app", "source": "approved", "department": A, "cluster": None, "vcpu": 40, "ramGB": 64, "diskGB": 100},
    {"label": "Mobile v3", "source": "manual", "department": B, "cluster": "PRD", "vcpu": 100, "ramGB": 200, "diskGB": 500},
    {"label": "Ops tool", "source": "manual", "department": C, "cluster": "DEV", "vcpu": 20, "ramGB": 40, "diskGB": 0},
]
RECLAIMS = [{"label": "old-01", "decision": "decommission", "cluster": "PRD", "vcpu": 12, "ramGB": 48.4},
            {"label": "x", "decision": "resize", "cluster": "ELSEWHERE", "vcpu": 99, "ramGB": 99}]
NODE = HostProfile(name="2×16 core · 512 GB", sockets=2, cores_per_socket=16, ram_gb=512, unit_price=450_000_000)


def test_forecast_net_demand_and_hosts_with_factor():
    fc = planning.forecast(CLUSTERS, KNOWN, RECLAIMS, NODE, Policy(), 80, factors=planning.fulfilment(ITEMS))
    prd, dev = fc["clusters"]
    assert fc["defaultCluster"] == "PRD"                          # largest: the approved request without a cluster lands there
    assert [(k["cluster"], k["vcpuNet"], k["ramGBNet"]) for k in fc["known"]] == [("PRD", 12, 38.4), ("PRD", 100, 200), ("DEV", 17.4, 38.8)]
    assert (prd["netVcpu"], prd["netRamGB"]) == (760, 1390)       # 600 + 60 + 112 − 12 · 1100 + 100 + 238,4 − 48,4
    assert (round(prd["cpuPct"], 2), round(prd["ramPct"], 2)) == (65.97, 90.49)
    assert (prd["nodes"], prd["nodesCpu"], prd["nodesRam"], prd["binding"]) == (1, 0, 1, "RAM")        # 1390 / 1536 > 80 %
    assert round(prd["ramPctAfter"], 2) == 67.87
    assert (dev["netVcpu"], dev["netRamGB"], dev["nodes"], dev["binding"]) == (117.4, 338.8, 1, "RAM")   # N+1: 338,8 / 256 > 100 %
    assert [x["label"] for x in fc["reclaims"]] == ["old-01"]     # a reclaim outside the clusters frees nothing here
    assert (fc["totals"]["nodes"], fc["totals"]["amount"]) == (2, 900_000_000)
    assert (fc["totals"]["knownVcpu"], fc["totals"]["reclaimVcpu"], fc["totals"]["netVcpu"]) == (129.4, 12, 877.4)


def test_forecast_without_factor_and_without_price():
    node = HostProfile(name="std", sockets=2, cores_per_socket=16, ram_gb=512)
    fc = planning.forecast(CLUSTERS, KNOWN, RECLAIMS, node, Policy(), 80, default_cluster="DEV")
    assert [(k["cluster"], k["vcpuNet"]) for k in fc["known"]] == [("DEV", 40), ("PRD", 100), ("DEV", 20)]
    assert fc["clusters"][1]["netVcpu"] == 160 and fc["totals"]["amount"] is None and fc["useFactor"] is False


# ---------------------------------------------------------------- database path
def _seed(db, now):  # noqa: F811
    """1 cluster (3 hosts), 1 VM created by a counted request 200 days ago, 1 request approved but not provisioned."""
    from app.cmdb.models import Ci
    from app.events.models import ChangeLog
    from app.itsm.models import ItsmRequest, ItsmRequestCi, ItsmRequestLine
    from app.models import HostSnapshot, SnapshotRun, VmSnapshot
    from app.spend.models import SpendCategory
    created = now - dt.timedelta(days=200)

    def run(at, usage):
        r = SnapshotRun(vcenter_key="vc1", source="live", status="ok", started_at=at, finished_at=at)
        db.add(r)
        db.flush()
        db.add_all([HostSnapshot(run_id=r.id, vcenter_key="vc1", cluster="PRD", host=f"esx{i}", cores=32, ram_gb=512, captured_at=at)
                    for i in range(3)])
        db.add(VmSnapshot(run_id=r.id, vcenter_key="vc1", cluster="PRD", vm="app-01", vcpu=8, ram_gb=32, is_powered_on=True,
                          cpu_usage_pct_raw=usage, ram_usage_pct_raw=usage, captured_at=at))
    for days, usage in ((0, 10), (90, 25), (180, 50), (200, 50)):
        run(created + dt.timedelta(days=days, hours=1), usage)
    ci = Ci(type="vm", name="app-01", source_kind="vcenter", source_key="u1", status="active", first_seen=created, last_seen=now,
            attrs=json.dumps({"vcenterKey": "vc1", "cluster": "PRD"}))
    db.add(ci)
    db.flush()
    db.add(ChangeLog(at=created, vcenter_key="vc1", vc_event_id=1, ci_id=ci.id, kind="vm_created", object_type="vm", object_name="app-01"))
    r1 = ItsmRequest(source_key="sdp", req_id="101", kind="new_vm", parse_status="parsed", department=A, project="Core",
                     created_at=created - dt.timedelta(days=3), resolved_at=created)
    r2 = ItsmRequest(source_key="sdp", req_id="102", kind="new_vm", parse_status="parsed", department=A, project="Core",
                     created_at=now - dt.timedelta(days=10), status="Open")
    db.add_all([r1, r2])
    db.flush()
    db.add_all([ItsmRequestLine(request_id=r1.id, line_no=0, vm_name="app-01", vcpu=8, ram_gb=32, disk_gb=100, qty=1),
                ItsmRequestLine(request_id=r2.id, line_no=0, vm_name="crm-01", vcpu=16, ram_gb=64, disk_gb=200, qty=1),
                ItsmRequestCi(request_id=r1.id, line_no=0, ci_id=ci.id, match="strong", matched_by="name+window"),
                SpendCategory(code="HW_SERVER", name="Máy chủ", grp="hw_warranty", active=True)])
    db.commit()


def test_build_push_once_and_update_on_rerun(db, monkeypatch):  # noqa: F811
    from types import SimpleNamespace as NS
    from app.excel_annual import build_xlsx
    from app.itsm import annual
    from app.report_annual import render_html
    from app.spend import budget_plan as bp
    from app.spend.models import BudgetPlan, BudgetPlanLine
    now = dt.datetime.utcnow().replace(microsecond=0)
    _seed(db, now)
    year = (now + dt.timedelta(hours=7) - dt.timedelta(days=200)).year
    annual.save_inputs(db, year + 1, {**annual.DEFAULT_PARAMS, "unitPrice": 400_000_000, "useFactor": True},
                       [{"name": "Mobile v3", "department": "Khối Ngân hàng số", "cluster": "PRD", "vms": 4, "vcpu": 2000, "ramGB": 900, "diskGB": 0}], "t")
    db.commit()
    d = annual.build(db, year, now)
    [vm] = d["fulfilment"]["vms"]
    assert (vm["provVcpu"], vm["used90"], vm["used180"]) == (8, {"vcpu": 2.0, "ramGB": 8.0}, {"vcpu": 4.0, "ramGB": 16.0})
    assert d["fulfilment"]["overall"]["factorVcpu"] == 0.5       # 4 used / 8 requested after 180 days
    [approved, manual] = d["forecast"]["known"]
    assert (approved["label"], approved["vcpuNet"], approved["ramGBNet"]) == ("#102 crm-01", 8, 32)   # × factor of the department
    assert manual["vcpuNet"] == 1000                              # Khối Ngân hàng số: no VM measured → overall factor 0,5
    t = d["forecast"]["totals"]
    assert t["nodes"] >= 1 and t["amount"] == t["nodes"] * 400_000_000
    html = render_html(d, "tester")                               # the PDF says the same numbers as the page
    assert f"cần thêm {t['nodes']} host" in html
    assert build_xlsx(d)[:2] == b"PK"
    # push: kept without a draft plan, then one line in the draft plan, updated (not duplicated) on re-run
    user = NS(username="tester", display_name="Tester")
    annual.push(db, d, "tester")
    db.commit()
    plan = BudgetPlan(fiscal_year=year + 1, version=1, status="draft", name="KH", created_by="t", row_version=1, uplift_pct=5)
    db.add(plan)
    db.flush()
    cfg = NS(fiscal_start_month=1)
    monkeypatch.setattr("app.dc_assets.plan_suggestions", lambda *_a: [])
    monkeypatch.setattr("app.hardware.warranty.plan_suggestions", lambda *_a: [])
    assert bp.refresh(db, plan, cfg, user, only="capacity")["added"] == 1
    line = db.query(BudgetPlanLine).filter_by(plan_id=plan.id, source="capacity").one()
    assert (line.kind, line.source_id, line.title, line.amount_net) == ("capex", year + 1, f"Mở rộng hạ tầng ảo hoá {year + 1}", t["amount"])
    assert "Nhu cầu ròng" in line.detail
    annual.save_inputs(db, year + 1, {**annual.DEFAULT_PARAMS, "unitPrice": 500_000_000}, json.loads(json.dumps(d["inputs"]["projects"])), "t")
    d2 = annual.build(db, year, now)
    annual.push(db, d2, "tester")
    db.flush()
    assert bp.refresh(db, plan, cfg, user, only="capacity") == {"added": 0, "updated": 1, "removed": 0, "stale": 0}
    assert db.query(BudgetPlanLine).filter_by(plan_id=plan.id, source="capacity").count() == 1
    assert line.amount_net == d2["forecast"]["totals"]["nodes"] * 500_000_000


def test_clean_inputs_validates():
    from app.itsm import annual
    params, projects = annual.clean_inputs({"params": {"unitPrice": "450.000.000", "targetPct": "85"},
                                            "projects": [{"name": "A", "vcpu": "16", "ramGB": "64"}, {"name": "", "vcpu": ""}]})
    assert (params["unitPrice"], params["targetPct"], len(projects), projects[0]["vcpu"]) == (450_000_000, 85.0, 1, 16)
    for bad in ({"params": {"targetPct": 120}}, {"projects": [{"vcpu": 4}]}, {"projects": [{"name": "x", "vcpu": -1}]}):
        with pytest.raises(ValueError):
            annual.clean_inputs(bad)
