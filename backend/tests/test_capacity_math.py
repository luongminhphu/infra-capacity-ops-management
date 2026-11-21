"""Unit tests for backend/app/capacity.py with concrete numbers.

The worked examples come from .claude/skills/capacity-math/SKILL.md. Every
number here feeds a hardware purchase decision, so the assertions pin exact
values, threshold boundaries and the reconciliation-code format.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import capacity  # noqa: E402
from app.capacity import Policy  # noqa: E402

OK = {"text": "AN TOÀN", "cls": "ok"}
WARN = {"text": "THEO DÕI", "cls": "warn"}
BAD = {"text": "CẢNH BÁO", "cls": "bad"}
CRIT = {"text": "VƯỢT NGƯỠNG", "cls": "crit"}
NA = {"text": "NA", "cls": "na"}


def _host(name, cores=32, ram=512, cluster="CL1", vc="vc1"):
    return {"host": name, "cluster": cluster, "cores": cores, "ramGB": ram, "vcenterKey": vc}


def _vm(name, vcpu, ram, on=True, host="esx01", cluster="CL1", prov=0.0, used=0.0, ds="DS1", vc="vc1"):
    return {"vm": name, "host": host, "cluster": cluster, "vcpu": vcpu, "ramGB": ram, "isPoweredOn": on,
            "diskProvGB": prov, "diskUsedGB": used, "datastore": ds, "vcenterKey": vc}


def _worked_example():
    """Skill example 1: 2 hosts x 32 cores (HT 2) x 512 GB; powered-on VMs 384 vCPU / 900 GB."""
    hosts = [_host("esx01"), _host("esx02")]
    vms = [_vm("app01", 192, 450, host="esx01"), _vm("app02", 192, 450, host="esx02")]
    return hosts, vms


# --- worked examples -------------------------------------------------------

def test_worked_example_cpu_overcommit_and_pct():
    hosts, vms = _worked_example()
    m = capacity.build_capacity_model(hosts, vms, Policy())
    assert m["totalCores"] == 64
    assert m["totalLogicalCores"] == 128
    assert m["usedVcpu"] == 384
    assert m["cpuOvercommit"] == pytest.approx(3.0)
    assert m["cpuPct"] == pytest.approx(50.0)
    assert capacity.get_status_for_pct(m["cpuPct"], 80, 100) == OK


def test_worked_example_ram_pct_is_watch():
    hosts, vms = _worked_example()
    m = capacity.build_capacity_model(hosts, vms, Policy())
    assert m["totalRamGB"] == 1024
    assert m["ramOvercommit"] == pytest.approx(900 / 1024)
    assert m["ramPct"] == pytest.approx(87.890625)
    assert capacity.get_status_for_pct(m["ramPct"], 80, 100) == WARN


def test_worked_example_cluster_row_overall_is_worst_of_cpu_ram():
    hosts, vms = _worked_example()
    [row] = capacity.build_cluster_rows(hosts, vms, Policy())
    assert row["cpuStatus"] == OK
    assert row["ramStatus"] == WARN
    assert row["overall"] == WARN


def test_months_to_target_worked_example():
    months = capacity._months_to_target(60, 80, 10)
    assert months == pytest.approx(math.log(80 / 60) / math.log(1.1) * 3, rel=1e-12)
    assert months == pytest.approx(9.06, abs=0.005)


def test_ha_headroom_matches_n_plus_one_example():
    # Skill example 3: 3 hosts x 32 cores, 900 vCPU powered on. Normal 78.1 %, N+1 117 %.
    hosts = [_host(f"esx0{i}") for i in (1, 2, 3)]
    vms = [_vm("big", 900, 100, host="esx01")]
    m = capacity.build_capacity_model(hosts, vms, Policy())
    assert m["cpuPct"] == pytest.approx(900 / 1152 * 100)
    [ha] = capacity.evaluate_ha_headroom(hosts, vms, Policy())
    assert ha["eligible"] is True
    assert ha["cpuPctAfter"] == pytest.approx(900 / 768 * 100)
    assert ha["survives"] is False


def test_ha_headroom_single_host_cluster_is_not_eligible():
    hosts = [_host("esx01")]
    vms = [_vm("a", 1, 1)]
    assert capacity.evaluate_ha_headroom(hosts, vms, Policy()) == [{"cluster": "CL1", "eligible": False, "hosts": 1}]


# --- status thresholds -----------------------------------------------------

@pytest.mark.parametrize("pct,expected", [
    (0, OK),
    (79.999, OK),
    (80, WARN),          # exactly warn -> THEO DÕI
    (99.999, WARN),
    (100, BAD),          # exactly high (=100) -> CẢNH BÁO, not VƯỢT NGƯỠNG
    (100.01, CRIT),
    (250, CRIT),
    (-5, OK),
])
def test_status_thresholds_cpu_ram(pct, expected):
    assert capacity.get_status_for_pct(pct, 80, 100) == expected


@pytest.mark.parametrize("pct", [float("nan"), float("inf"), float("-inf"), None])
def test_status_non_finite_is_na(pct):
    assert capacity.get_status_for_pct(pct, 80, 100) == NA


def test_status_over_100_wins_even_when_high_threshold_is_higher():
    # pct > 100 is always VƯỢT NGƯỠNG regardless of the configured bands.
    assert capacity.get_status_for_pct(100.5, 120, 150) == CRIT


@pytest.mark.parametrize("pct,expected", [(79.9, OK), (80, WARN), (89.9, WARN), (90, BAD), (100, BAD), (100.1, CRIT)])
def test_datastore_thresholds_80_90(pct, expected):
    assert capacity.get_status_for_pct(pct, 80, 90) == expected


def test_datastore_rows_use_80_90_and_sort_desc_with_nan_last():
    vms = [
        _vm("a", 1, 1, prov=100, used=85, ds="DS-A"),      # 85 % -> THEO DÕI
        _vm("b", 1, 1, prov=100, used=90, ds="DS-B"),      # 90 % -> CẢNH BÁO
        _vm("c", 1, 1, prov=0, used=0, ds="DS-EMPTY"),     # 0 provisioned -> NA
        _vm("d", 1, 1, prov=200, used=100, ds="DS-A"),     # DS-A total 185/300 = 61.67 %
    ]
    rows = capacity.build_datastore_rows(vms)
    assert [r["name"] for r in rows] == ["DS-B", "DS-A", "DS-EMPTY"]
    by = {r["name"]: r for r in rows}
    assert by["DS-B"]["status"] == BAD
    assert by["DS-A"]["pct"] == pytest.approx(185 / 300 * 100)
    assert by["DS-A"]["status"] == OK
    assert by["DS-A"]["vmCount"] == 2
    assert math.isnan(by["DS-EMPTY"]["pct"])
    assert by["DS-EMPTY"]["status"] == NA


# --- combine_status --------------------------------------------------------

@pytest.mark.parametrize("inputs,expected", [
    ((OK, WARN), WARN),
    ((WARN, BAD), BAD),
    ((BAD, CRIT, OK), CRIT),
    ((OK, OK), OK),
    ((NA, OK), OK),       # NA is ignored when any other status exists
    ((NA, BAD), BAD),
    ((NA, NA), NA),
    ((), NA),
])
def test_combine_status_takes_worst(inputs, expected):
    assert capacity.combine_status(*inputs) == expected


# --- zero denominators -----------------------------------------------------

def test_zero_capacity_gives_nan_never_infinity():
    m = capacity.build_capacity_model([], [_vm("a", 4, 8, prov=0)], Policy())
    for key in ("cpuOvercommit", "ramOvercommit", "cpuPct", "ramPct", "storagePct"):
        assert math.isnan(m[key]), key
    assert capacity.get_status_for_pct(m["cpuPct"], 80, 100) == NA


def test_zero_overcommit_max_gives_nan_pct():
    hosts, vms = _worked_example()
    m = capacity.build_capacity_model(hosts, vms, Policy(cpu_overcommit_max=0, ram_overcommit_max=0))
    assert m["cpuOvercommit"] == pytest.approx(3.0)
    assert math.isnan(m["cpuPct"]) and math.isnan(m["ramPct"])


def test_host_row_with_zero_cores_and_ram_is_na():
    rows = capacity.build_host_rows([_host("esx01", cores=0, ram=0)], [_vm("a", 4, 8)], Policy())
    assert math.isnan(rows[0]["cpuPct"]) and math.isnan(rows[0]["ramPct"])
    assert rows[0]["overall"] == NA


# --- powered-off VMs, HT factor, storage ----------------------------------

def test_powered_off_vms_excluded_from_cpu_ram_but_counted_in_storage():
    hosts = [_host("esx01")]
    vms = [
        _vm("on", 8, 32, on=True, prov=100, used=40),
        _vm("off", 64, 256, on=False, prov=300, used=200),
    ]
    m = capacity.build_capacity_model(hosts, vms, Policy())
    assert m["usedVcpu"] == 8
    assert m["usedRamGB"] == 32
    assert m["poweredOnCount"] == 1 and m["poweredOffCount"] == 1
    assert m["provStorageGB"] == 400
    assert m["usedStorageGB"] == 240
    assert m["storagePct"] == pytest.approx(60.0)
    host_row = capacity.build_host_rows(hosts, vms, Policy())[0]
    assert host_row["vcpuUsed"] == 8 and host_row["vmCount"] == 1


@pytest.mark.parametrize("ht,expected_logical", [(2.0, 64), (1.0, 32), (0.5, 32), (0, 64), (None, 64)])
def test_ht_factor_floor_one_and_default_two(ht, expected_logical):
    m = capacity.build_capacity_model([_host("esx01")], [], Policy(ht_factor=ht))
    assert m["totalLogicalCores"] == expected_logical


def test_include_storage_defaults_off_and_never_drives_overall_status():
    assert Policy().include_storage is False
    assert Policy(include_storage="yes").include_storage is True
    hosts = [_host("esx01")]
    # Storage 100 % used, CPU/RAM tiny: overall must stay AN TOÀN.
    vms = [_vm("a", 2, 8, prov=100, used=100)]
    [row] = capacity.build_cluster_rows(hosts, vms, Policy(include_storage=True))
    assert row["model"]["storagePct"] == pytest.approx(100.0)
    assert row["overall"] == OK


@pytest.mark.parametrize("raw,default,expected", [
    (None, True, True), ("1", False, True), ("On", False, True), ("no", True, False), ("", True, False), (False, True, False),
])
def test_parse_bool(raw, default, expected):
    assert capacity.parse_bool(raw, default) is expected


# --- growth scenarios ------------------------------------------------------

def test_growth_scenarios_use_100_for_cpu_ram_and_90_for_storage():
    out = capacity.estimate_growth_scenarios({"cpuPct": 50.0, "ramPct": 100.0, "storagePct": 45.0})
    cpu10 = next(s for s in out["cpu"]["scenarios"] if s["growthPct"] == 10.0)
    assert cpu10["months"] == pytest.approx(math.log(2) / math.log(1.1) * 3)  # ~21.82
    assert all(s["months"] == 0.0 for s in out["ram"]["scenarios"])         # already at target
    assert out["storage"]["target"] == 90.0
    st5 = out["storage"]["scenarios"][0]
    assert st5["growthPct"] == 5.0
    assert st5["months"] == pytest.approx(math.log(2) / math.log(1.05) * 3)


# --- finding codes ---------------------------------------------------------

def _fnv1a_32(text: str) -> int:
    """Reference FNV-1a 32-bit, written independently of the app."""
    h = 2166136261
    for byte in text.encode("utf-8"):
        h = ((h ^ byte) * 16777619) % (1 << 32)
    return h


def test_reference_fnv1a_matches_published_vectors():
    assert _fnv1a_32("") == 0x811C9DC5
    assert _fnv1a_32("a") == 0xE40C292C


def test_finding_code_hard_coded_values():
    assert capacity.finding_code("host_cpu", "CL-PROD", "esx01.lab.local", "vc1") == "HCPU-3A001"
    # Vietnamese diacritics hash as UTF-8 bytes.
    assert capacity.finding_code("vm_idle", "Cụm Hà Nội", "Máy chủ kế toán", "vc2") == "IDLE-210B6"


@pytest.mark.parametrize("ftype,cluster,entity,vc", [
    ("host_cpu", "CL-PROD", "esx01", "vc1"),
    ("host_ram", "CL-PROD", "esx01", ""),
    ("overcommit_cpu", "CL-DR", "CL-DR", None),
    ("overcommit_ram", "CL-DR", "CL-DR", ""),
    ("vm_idle", "Cụm Đà Nẵng", "VM-ứng-dụng", "vc-hn"),
    ("vm_off", None, None, None),
    ("vm_overprovision", "c", "e", "v"),
    ("something_new", "c", "e", "v"),
])
def test_finding_code_format_and_independent_hash(ftype, cluster, entity, vc):
    prefix = {"host_cpu": "HCPU", "host_ram": "HRAM", "overcommit_cpu": "CCPU", "overcommit_ram": "CRAM",
              "vm_idle": "IDLE", "vm_off": "POFF", "vm_overprovision": "OVP"}.get(ftype, "FND")
    expected_hex = f"{_fnv1a_32(f'{ftype}|{vc or ''}|{cluster or ''}|{entity or ''}'):08X}"[:5]
    code = capacity.finding_code(ftype, cluster, entity, vc)
    assert code == f"{prefix}-{expected_hex}"
    assert len(code.split("-")[1]) == 5


def test_finding_code_is_deterministic_and_field_order_matters():
    a = capacity.finding_code("host_cpu", "CL1", "esx01", "vc1")
    assert a == capacity.finding_code("host_cpu", "CL1", "esx01", "vc1")
    assert a != capacity.finding_code("host_cpu", "esx01", "CL1", "vc1")
    assert a != capacity.finding_code("host_cpu", "CL1", "esx01", "vc2")


def test_compliance_findings_carry_codes_for_worked_example():
    hosts, vms = _worked_example()
    findings = capacity.build_compliance_findings(hosts, vms, Policy())
    # Each host: RAM 450/512 = 87.9 % of threshold -> host_ram warning; CPU 50 % -> nothing.
    assert sorted((f["type"], f["entity"], f["severity"]) for f in findings) == [
        ("host_ram", "esx01", "warning"), ("host_ram", "esx02", "warning"),
    ]
    for f in findings:
        assert f["code"] == capacity.finding_code("host_ram", "CL1", f["entity"], "vc1")


def test_ha_headroom_ram_checked_against_the_host_with_most_ram():
    """Audit: the 'largest' host was picked by cores only. A 64C/256 GB, B and D 32C/1024 GB, 1800 GB of RAM:
    losing A leaves 2048 GB (88 %) but losing B leaves 1280 GB (141 %) → does not survive."""
    hosts = [_host("A", 64, 256), _host("B", 32, 1024), _host("D", 32, 1024)]
    vms = [_vm("big", 16, 1800)]
    [ha] = capacity.evaluate_ha_headroom(hosts, vms, Policy(ram_overcommit_max=1.0))
    assert ha["ramPctAfter"] == pytest.approx(1800 / 1280 * 100)
    assert ha["cpuPctAfter"] == pytest.approx(16 / (64 * 2) / 6 * 100)              # CPU still against losing A (64 cores)
    assert ha["survives"] is False and ha["largestHost"] in ("B", "D")


def test_effective_policy_has_the_same_floors_as_core_js():
    from types import SimpleNamespace as NS
    from app import data_access
    from app.models import AppSetting

    class Q:
        def __init__(self, rows): self.rows = rows
        def query(self, _m): return self
        def all(self): return [AppSetting(key=k, value=v) for k, v in self.rows.items()]
    env = NS(cpu_overcommit_max=6.0, ram_overcommit_max=1.0, ht_factor=2.0, idle_days=30, include_storage=False)
    p = data_access.get_effective_policy(Q({"ram_overcommit_max": "0", "cpu_overcommit_max": "0.05", "ht_factor": "nan",
                                            "idle_days": "-5"}), env)
    # core.js: Math.max(0.1, x || 1.0) / Math.max(0.1, x || 6) / Math.max(1, x || 2) / Math.max(1, x || 30)
    assert (p.ram_overcommit_max, p.cpu_overcommit_max, p.ht_factor, p.idle_days) == (1.0, 0.1, 2.0, 1)
