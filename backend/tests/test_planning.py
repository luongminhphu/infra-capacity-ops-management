"""Unit tests for backend/app/planning.py: quarterly compounding, months to
target, default scenarios, node sizing with N+1 and the RAM-upgrade option."""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import planning  # noqa: E402
from app.capacity import Policy, _months_to_target  # noqa: E402


# --- growth compounding ----------------------------------------------------

@pytest.mark.parametrize("months,expected", [
    (0, 100.0),
    (3, 110.0),                  # one quarter
    (6, 121.0),                  # two quarters, compounded
    (12, 146.41),
    (1.5, 100 * 1.1 ** 0.5),     # fractional quarter
])
def test_grow_compounds_quarterly(months, expected):
    assert planning._grow(100.0, 10.0, months) == pytest.approx(expected, rel=1e-12)


def test_grow_with_zero_growth_is_flat():
    assert planning._grow(384.0, 0.0, 36) == 384.0


# --- months to target ------------------------------------------------------

def test_months_to_target_inverts_grow():
    months = _months_to_target(60, 80, 10)
    assert planning._grow(60, 10, months) == pytest.approx(80, rel=1e-12)


@pytest.mark.parametrize("growth", [0, -5])
def test_months_to_target_never_reached_without_positive_growth(growth):
    assert _months_to_target(60, 80, growth) is None


@pytest.mark.parametrize("current", [80, 95.5])
def test_months_to_target_zero_when_already_at_or_over(current):
    assert _months_to_target(current, 80, 10) == 0.0
    # Even with no growth, already being there is 0, not None.
    assert _months_to_target(current, 80, 0) == 0.0


@pytest.mark.parametrize("current", [None, 0, -1, float("nan"), float("inf")])
def test_months_to_target_invalid_current_is_none(current):
    assert _months_to_target(current, 80, 10) is None


# --- scenarios -------------------------------------------------------------

def test_default_scenarios_low_base_high():
    sc = planning.build_scenarios(planning.PlanningParams(), None)
    assert [(s["label"], s["growthPct"], s["source"], s["base"]) for s in sc] == [
        ("Thấp", 5.0, "assumed", False),
        ("Cơ sở", 10.0, "assumed", True),
        ("Cao", 20.0, "assumed", False),
    ]


def test_trend_scenario_added_only_for_positive_growth():
    params = planning.PlanningParams()
    sc = planning.build_scenarios(params, {"months": 6, "combined": 7.5, "cpu": 7.5, "ram": 3.0})
    assert len(sc) == 4
    assert sc[-1]["label"] == "Xu hướng 6 tháng"
    assert sc[-1]["growthPct"] == 7.5 and sc[-1]["source"] == "trend"
    assert len(planning.build_scenarios(params, {"months": 6, "combined": 0.0})) == 3
    assert len(planning.build_scenarios(params, {"months": 6, "combined": -2.0})) == 3


def test_non_default_scenario_count_uses_generic_labels():
    sc = planning.build_scenarios(planning.PlanningParams(growth=(20, 5, 10, 15)), None)
    assert [s["growthPct"] for s in sc] == [5.0, 10.0, 15.0, 20.0]  # sorted
    assert [s["label"] for s in sc] == ["Kịch bản 1", "Kịch bản 2", "Kịch bản 3", "Kịch bản 4"]


def test_quarterly_growth_from_series():
    # 100 -> 121 over 182.5 days (two 91.25-day quarters) = 10 %/quarter.
    series = [{"date": "2026-01-01", "usedVcpu": 100}, {"date": "2026-07-02T12:00:00", "usedVcpu": 121}]
    assert planning._quarterly_growth_from_series(series, "usedVcpu") == pytest.approx(10.0, abs=0.1)
    # Under 30 days of history: no signal.
    short = [{"date": "2026-01-01", "usedVcpu": 100}, {"date": "2026-01-20", "usedVcpu": 150}]
    assert planning._quarterly_growth_from_series(short, "usedVcpu") is None


# --- _nodes_needed ---------------------------------------------------------

# Defaults: 3 hosts x 32 cores (HT 2 -> 64 logical each), 3 x 512 GB, ocMax CPU 6 / RAM 1, target 80 %.
BASE = dict(cap_logical=192.0, cap_ram=1536.0, n_hosts=3, largest_logical=64.0, largest_ram=512.0,
            node_logical=64.0, node_ram=512.0)


def _need(demand_vcpu, demand_ram, target=80.0, **over):
    args = {**BASE, **over}
    return planning._nodes_needed(demand_vcpu, demand_ram, args["cap_logical"], args["cap_ram"], args["n_hosts"],
                                  args["largest_logical"], args["largest_ram"], args["node_logical"],
                                  args["node_ram"], Policy(), target)


def test_nodes_needed_skill_example_n_plus_one_binds_cpu():
    # 900/1152 = 78.1 % passes the target, but losing a host gives 900/768 = 117 %.
    # One 32-core node: 900/1536 = 58.6 %, N+1 900/1152 = 78.1 % -> k = 1.
    need = _need(900, 100)
    assert need == {"nodes": 1, "nodesCpu": 1, "nodesRam": 0, "binding": "CPU"}


def test_nodes_needed_ram_constraint_binds():
    # RAM 1200/1536 = 78.1 % but N+1 1200/1024 = 117 % -> one 512 GB node.
    need = _need(10, 1200)
    assert need == {"nodes": 1, "nodesCpu": 0, "nodesRam": 1, "binding": "RAM"}


def test_nodes_needed_both_constraints_equal():
    assert _need(900, 1200) == {"nodes": 1, "nodesCpu": 1, "nodesRam": 1, "binding": "CPU+RAM"}


def test_nodes_needed_nothing_to_add():
    assert _need(100, 100) == {"nodes": 0, "nodesCpu": 0, "nodesRam": 0, "binding": "—"}


def test_nodes_needed_target_constraint_binds():
    # target 50 %: 600/1152 = 52 % fails; +1 node 600/1536 = 39 %, N+1 600/1152 = 52 % <= 100 %.
    assert _need(600, 10, target=50.0)["nodesCpu"] == 1


def test_nodes_needed_larger_new_node_is_the_one_lost():
    # New node = 128 logical (bigger than any existing host). Demand 1200 vCPU:
    # k=1: 1200/1920 = 62.5 % ok, but losing the new node leaves 192 -> 1200/1152 = 104 % fail.
    # k=2: 1200/2688 = 44.6 %, losing one new node leaves 320 -> 1200/1920 = 62.5 % -> k = 2.
    assert _need(1200, 10, node_logical=128.0)["nodesCpu"] == 2


def test_nodes_needed_single_host_cluster_never_passes_n_plus_one_alone():
    # Zero demand still needs a second host for N+1.
    need = _need(0, 0, cap_logical=64.0, cap_ram=512.0, n_hosts=1)
    assert need["nodes"] == 1
    # Empty cluster needs two nodes.
    need = _need(1, 1, cap_logical=0.0, cap_ram=0.0, n_hosts=0, largest_logical=0.0, largest_ram=0.0)
    assert need["nodes"] == 2


# --- plan_cluster ----------------------------------------------------------

def _cluster(ram_per_vm=None, vcpu=30):
    hosts = [{"host": f"esx0{i}", "cluster": "CL1", "cores": 32, "ramGB": 512} for i in (1, 2, 3)]
    vms = [{"vm": "app", "host": "esx01", "cluster": "CL1", "vcpu": vcpu, "ramGB": ram_per_vm,
            "isPoweredOn": True, "diskProvGB": 0, "diskUsedGB": 0}]
    return hosts, vms


def _plan(hosts, vms, growth):
    params = planning.PlanningParams(horizon_months=6, growth=growth)
    scenarios = planning.build_scenarios(params, None)
    return planning.plan_cluster("CL1", hosts, vms, Policy(), params, scenarios, None)


def test_ram_upgrade_rounds_up_to_64_gb_steps():
    # RAM 1400 GB, target 80 %: need 1400/0.8 = 1750 GB vs 1536 -> gap 214 GB / 3 hosts = 71.3 GB
    # -> rounded up to 2 x 64 = 128 GB per host.
    assert planning.RAM_STEP_GB == 64
    hosts, vms = _cluster(ram_per_vm=1400)
    row = _plan(hosts, vms, (0.0,))["scenarios"][0]
    assert row["final"]["nodesCpu"] == 0 and row["final"]["nodesRam"] == 1
    assert row["ramUpgrade"] == {"perHostGB": 128, "totalGB": 384, "hosts": 3}


def test_ram_upgrade_single_step():
    # 1300 GB: gap 1625 - 1536 = 89 GB / 3 = 29.7 GB -> 64 GB per host.
    hosts, vms = _cluster(ram_per_vm=1300)
    row = _plan(hosts, vms, (0.0,))["scenarios"][0]
    assert row["ramUpgrade"] == {"perHostGB": 64, "totalGB": 192, "hosts": 3}


def test_plan_cluster_projects_demand_with_quarterly_compounding():
    hosts, vms = _cluster(ram_per_vm=100, vcpu=600)
    row = _plan(hosts, vms, (10.0,))["scenarios"][0]
    final = row["final"]
    assert final["month"] == 6
    assert final["demandVcpu"] == pytest.approx(600 * 1.21)
    assert final["demandRamGB"] == pytest.approx(121.0)
    assert final["cpuPct"] == pytest.approx(726 / 1152 * 100)
    assert row["firstNeedMonth"] is None  # 726/768 = 94.5 % under N+1, 63 % normal
    assert math.isclose(row["series"]["cpuPct"][0], 600 / 1152 * 100)
