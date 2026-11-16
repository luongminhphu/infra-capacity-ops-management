"""Unit tests of the license engine (audit TST-01) — pure functions, exact numbers.

Rules under test (app/licensing/rules.py, skill capacity-math):
- Windows Server: licensable cores per host = max(physical cores, 8 × sockets, 16).
- VMware per-core subscription: cores per host = max(physical cores, 16 × sockets).
- VMware legacy per-CPU: sockets × ceil(cores per socket / 32).
- Standard edition in a mobile cluster: ceil(WS VMs / 2) license sets on every host.
Money (DAT-10 release 2): integer minor units, HALF_UP once per amount.
"""
import copy

import pytest

from app import capacity
from app.licensing import assess as A
from app.licensing import whatif as W
from app.licensing.os_classify import classify_vm
from app.licensing.rules import VMWARE_CORE_PARAMS, VMWARE_CPU_LEGACY_PARAMS, WS_DEFAULT_PARAMS

WS_GUEST = "Microsoft Windows Server 2022 (64-bit)"
LINUX_GUEST = "Red Hat Enterprise Linux 9 (64-bit)"


def _rules(ws=None, vmw=None, legacy=False):
    out = {
        "windows_server": {"key": "ws", "version": 1, "name": "WS", "status": "verified",
                           "params": {**WS_DEFAULT_PARAMS, **(ws or {})}},
        "vmware": {"key": "vmw", "version": 1, "name": "VMW", "status": "verified",
                   "params": {**VMWARE_CORE_PARAMS, **(vmw or {})}},
    }
    if legacy:
        out["vmware_cpu_legacy"] = {"key": "vmware_cpu_legacy", "version": 1, "name": "legacy", "status": "verified",
                                    "isActive": True, "params": dict(VMWARE_CPU_LEGACY_PARAMS)}
    return out


def _host(name, cores, sockets, cluster="CL1", ram=512, **kw):
    return {"vcenterKey": "vc1", "cluster": cluster, "host": name, "cores": cores, "cpuSockets": sockets,
            "cpuThreads": cores * 2 if cores else None, "ramGB": ram, "clusterHa": True, "clusterDrs": True, **kw}


def _vm(name, host, cluster="CL1", guest=WS_GUEST, on=True, vcpu=4, ram=16):
    v = {"vcenterKey": "vc1", "cluster": cluster, "host": host, "vm": name, "isPoweredOn": on,
         "vcpu": vcpu, "ramGB": ram, "toolsGuestFullName": guest, "toolsStatus": "guestToolsRunning",
         "toolsGuestFamily": "windowsGuest" if "Windows" in guest else "linuxGuest"}
    v["os"] = classify_vm(v)
    return v


def _unknown_vm(name, host, cluster="CL1"):
    v = {"vcenterKey": "vc1", "cluster": cluster, "host": host, "vm": name, "isPoweredOn": True, "vcpu": 2, "ramGB": 8}
    v["os"] = classify_vm(v)
    assert v["os"]["category"] == "other_unknown"
    return v


def _ent(product, qty, metric="core", price=None, currency="USD", **kw):
    e = {"product": product, "metric": metric, "quantity": qty, "kind": "owned", "unitPrice": price, "currency": currency}
    e.update(kw)
    return e


WS, VMW = WS_DEFAULT_PARAMS, VMWARE_CORE_PARAMS


# ---------------------------------------------------------------------------
# Per-host floors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cores,sockets,ws_cores,vmw_cores", [
    (12, 2, 16, 32),     # 2 × 6 core: WS host floor 16, VMware 16/CPU → 32
    (4, 1, 16, 16),      # tiny 1-socket host: both floors at 16
    (8, 4, 32, 64),      # 4 × 2 core: WS 8/CPU floor (32) beats the 16/host floor; VMware 4 × 16
    (48, 2, 48, 48),     # 2 × 24 core: no floor applies
    (32, 2, 32, 32),     # exactly on the VMware floor
    (40, 1, 40, 40),
])
def test_host_floors(cores, sockets, ws_cores, vmw_cores):
    r = A.host_license_row(_host("h", cores, sockets), WS, VMW, None)
    assert (r["wsLicensableCores"], r["vmwareCores"]) == (ws_cores, vmw_cores)
    assert r["wsFloorApplied"] is (ws_cores > cores)
    assert r["vmwareFloorApplied"] is (vmw_cores > cores)
    assert r["flags"] == []


def test_host_missing_sockets_is_assumed_and_flagged():
    big = A.host_license_row(_host("h", 20, None), WS, VMW, None)       # ≥ 16 core → assume 2 sockets
    assert (big["sockets"], big["wsLicensableCores"], big["vmwareCores"]) == (2, 20, 32)
    small = A.host_license_row(_host("h", 12, 0), WS, VMW, None)       # < 16 core → assume 1 socket
    assert (small["sockets"], small["wsLicensableCores"], small["vmwareCores"]) == (1, 16, 16)
    assert big["flags"] == small["flags"] == ["sockets_assumed"]


def test_host_without_cores_licenses_nothing():
    r = A.host_license_row(_host("h", 0, 2), WS, VMW, VMWARE_CPU_LEGACY_PARAMS)
    assert (r["wsLicensableCores"], r["vmwareCores"], r["vmwareLegacyCpuLicenses"]) == (0, 0, 0)
    assert "cores_missing" in r["flags"]


@pytest.mark.parametrize("cores,sockets,licenses", [(64, 2, 2), (66, 2, 4), (80, 2, 4), (32, 1, 1), (33, 1, 2)])
def test_legacy_per_cpu_32_core_cap(cores, sockets, licenses):
    r = A.host_license_row(_host("h", cores, sockets), WS, VMW, VMWARE_CPU_LEGACY_PARAMS)
    assert r["vmwareLegacyCpuLicenses"] == licenses


def test_custom_rule_parameters_are_used():
    r = A.host_license_row(_host("h", 12, 2), {"min_cores_per_proc": 10, "min_cores_per_host": 24}, {"min_cores_per_cpu": 8}, None)
    assert (r["wsLicensableCores"], r["vmwareCores"]) == (24, 16)


def test_packs_greedy():
    assert A._packs(70, [2, 16]) == {"pack16": 4, "pack2": 3, "pack16Only": 5}
    assert A._packs(32, [2, 16]) == {"pack16": 2, "pack2": 0, "pack16Only": 2}
    assert A._packs(17, [16]) == {"pack16": 2, "pack16Only": 2}


# ---------------------------------------------------------------------------
# Cluster maths and totals
# ---------------------------------------------------------------------------

def _two_host_cluster():
    hosts = [_host("esx1", 12, 2), _host("esx2", 12, 2)]                 # WS 16 + 16, VMware 32 + 32
    vms = [_vm(f"ws{i}", "esx1" if i % 2 else "esx2") for i in range(4)]  # 4 Windows Server VMs
    vms += [_unknown_vm("odd", "esx1"), _vm("lnx", "esx2", guest=LINUX_GUEST)]
    return hosts, vms


def test_windows_standard_vs_datacenter_cluster_total():
    hosts, vms = _two_host_cluster()
    res = A.assess(hosts, vms, _rules(), [], {})
    [c] = res["clusters"]
    assert (c["wsLicensableCores"], c["wsVms"], c["unknownVms"]) == (32, 4, 1)
    # Standard: ceil(4 / 2) = 2 sets on each host → 32 × 2 = 64; with the unknown VM ceil(5/2) = 3 → 96
    assert (c["standard"]["sets"], c["standard"]["cores"], c["standard"]["coresMax"]) == (2, 64, 96)
    assert c["standard"]["packs"] == {"pack16": 4, "pack2": 0, "pack16Only": 4}
    assert c["datacenter"]["cores"] == 32
    assert c["recommendedEdition"] == "datacenter"
    ws = res["windowsServer"]
    assert (ws["standard"]["demandCores"], ws["standard"]["demandCoresMax"], ws["datacenter"]["demandCores"]) == (64, 96, 32)
    assert ws["recommendedEdition"]["byCores"] == "datacenter"
    assert ws["standard"]["status"] == ws["datacenter"]["status"] == "no_entitlement"
    assert res["vmware"]["demandCores"] == 64
    assert res["totals"] == {"hosts": 2, "sockets": 4, "physicalCores": 24, "logicalThreads": 48, "vcpuAllocated": 22.0,
                             "vms": 6, "windowsServerVms": 4, "unknownVms": 1}
    assert res["overallStatus"] == "no_entitlement"


def test_windows_standard_current_placement_mode():
    hosts, vms = _two_host_cluster()                     # esx1: 2 WS VMs, esx2: 2 WS VMs
    vms.append(_vm("ws-extra", "esx1"))                  # esx1: 3 → ceil(3/2) = 2 sets; esx2: 1 set
    res = A.assess(hosts, vms, _rules(ws={"standard_peak_mode": "current_placement"}), [], {})
    std = res["clusters"][0]["standard"]
    assert std["sets"] is None
    assert std["cores"] == 16 * 2 + 16 * 1 == 48


def test_powered_off_windows_vms_can_be_excluded():
    hosts, vms = _two_host_cluster()
    vms[0]["isPoweredOn"] = False
    on = A.assess(hosts, vms, _rules(), [], {})
    off = A.assess(hosts, vms, _rules(ws={"include_powered_off": False}), [], {})
    assert on["totals"]["windowsServerVms"] == 4
    assert off["totals"]["windowsServerVms"] == 3
    assert off["clusters"][0]["standard"]["cores"] == 32 * 2   # ceil(3/2) = 2 sets


def test_status_ok_shortfall_and_insufficient_data():
    hosts, vms = _two_host_cluster()
    # Datacenter: 32 cores needed, 2 × pack16 owned → ok
    ok = A.assess(hosts, vms, _rules(), [_ent("ws_dc", 2, "pack16")], {})["windowsServer"]["datacenter"]
    assert (ok["entitledCores"], ok["shortfallCores"], ok["status"]) == (32.0, 0.0, "ok")
    # Standard: 64 confirmed .. 96 with the unknown VM; 80 owned → enough for confirmed VMs only
    mid = A.assess(hosts, vms, _rules(), [_ent("ws_std", 5, "pack16")], {})["windowsServer"]["standard"]
    assert (mid["entitledCores"], mid["shortfallCores"], mid["shortfallCoresMax"], mid["status"]) == (80.0, 0.0, 16.0, "insufficient_data")
    # 30 × pack2 = 60 cores → shortfall 4 .. 36
    short = A.assess(hosts, vms, _rules(), [_ent("ws_std", 30, "pack2")], {})["windowsServer"]["standard"]
    assert (short["shortfallCores"], short["shortfallCoresMax"], short["status"]) == (4.0, 36.0, "shortfall")
    assert short["shortfallPacks"] == {"pack16": 0, "pack2": 2, "pack16Only": 1}


def test_vmware_shortfall_and_minimum_order():
    hosts = [_host("esx1", 12, 2), _host("esx2", 4, 1)]                 # 32 + 16 = 48 cores
    ents = [_ent("vvf", 32, price=150, currency="USD")]
    res = A.assess(hosts, [], _rules(), ents, {})["vmware"]
    assert (res["demandCores"], res["entitledCores"], res["shortfallCores"], res["orderCores"], res["status"]) == (48, 32.0, 16.0, 16.0, "shortfall")
    assert res["cost"] == {"basis": "VMware vSphere Foundation (VVF)", "currency": "USD", "pricePerCore": 150.0,
                           "demandCost": 7200.0, "shortfallCost": 2400.0}
    res72 = A.assess(hosts, [], _rules(vmw={"min_cores_per_order": 72}), ents, {})["vmware"]
    assert (res72["shortfallCores"], res72["orderCores"], res72["cost"]["shortfallCost"]) == (16.0, 72, 10800.0)


def test_vmware_entitlements_of_all_products_add_up_and_cheapest_price_is_used():
    hosts = [_host("esx1", 32, 2), _host("esx2", 32, 2)]                # 64 cores
    ents = [_ent("vcf", 16, price=350), _ent("vvf", 48, price=135)]
    res = A.assess(hosts, [], _rules(), ents, {})["vmware"]
    assert (res["entitledCores"], res["shortfallCores"], res["status"]) == (64.0, 0.0, "ok")
    assert (res["cost"]["basis"], res["cost"]["pricePerCore"], res["cost"]["demandCost"]) == ("VMware vSphere Foundation (VVF)", 135.0, 8640.0)


def test_declared_none_makes_whole_demand_a_shortfall():
    hosts = [_host("esx1", 12, 2)]                                      # 32 VMware cores
    res = A.assess(hosts, [], _rules(), [_ent("vcf", 0, price=350, kind="none")], {})["vmware"]
    assert (res["entitledCores"], res["shortfallCores"], res["status"], res["declaredNone"]) == (0.0, 32.0, "shortfall", True)
    assert res["cost"]["shortfallCost"] == 11200.0
    assert res["entitlements"]["vcf"]["totalCost"] == 0.0              # nothing owned, the price is a quote


def test_legacy_rule_only_when_active():
    hosts = [_host("esx1", 66, 2)]                                      # 2 × ceil(33/32) = 4 CPU licenses
    assert A.assess(hosts, [], _rules(), [], {})["vmware"]["legacy"] is None
    leg = A.assess(hosts, [], _rules(legacy=True), [_ent("vmware_cpu_legacy", 2, "cpu")], {})["vmware"]["legacy"]
    assert (leg["demandCpuLicenses"], leg["entitled"], leg["shortfall"], leg["status"]) == (4, 2.0, 2.0, "shortfall")


def test_scope_filters_hosts_and_entitlements():
    hosts = [_host("a1", 12, 2, cluster="A"), _host("b1", 48, 2, cluster="B")]
    ents = [_ent("vvf", 32, scopeCluster="A"), _ent("vvf", 100, scopeCluster="B")]
    res = A.assess(hosts, [], _rules(), ents, {"clusterKeys": ["vc1::A"]})
    assert res["totals"]["hosts"] == 1
    assert (res["vmware"]["demandCores"], res["vmware"]["entitledCores"]) == (32, 32.0)
    assert [e["scopeCluster"] for e in res["entitlementsUsed"]] == ["A"]


def test_result_hash_is_stable_and_sensitive():
    hosts, vms = _two_host_cluster()
    r1 = A.assess(hosts, vms, _rules(), [], {})
    r2 = A.assess(copy.deepcopy(hosts), copy.deepcopy(vms), _rules(), [], {})
    assert A.result_hash(r1) == A.result_hash(r2)
    hosts[0]["cores"] = 20
    assert A.result_hash(A.assess(hosts, vms, _rules(), [], {})) != A.result_hash(r1)


# ---------------------------------------------------------------------------
# Money: integer minor units (DAT-10 release 2)
# ---------------------------------------------------------------------------

def test_owned_cost_is_exact_cents():
    # 1.234,55 USD × 3 packs: the float product is 3703.6499999999996, the integer one is 370365 cents.
    s = A._ent_summary([_ent("ws_dc", 3, "pack16", price=1234.55)], "ws_dc", "core")
    assert s["totalCost"] == 3703.65
    assert s["units"] == 48.0 and s["currency"] == "USD"
    assert s["pricePerUnit"] == 77.159375                # 123455 cents / 16 cores, display only


def test_shortfall_cost_rounds_once_half_up():
    hosts, vms = _two_host_cluster()                     # Datacenter needs 32 cores
    ents = [_ent("ws_dc", 1, "pack16", price=1234.55)]   # 16 owned → 16 short
    dc = A.assess(hosts, vms, _rules(), ents, {})["windowsServer"]["datacenter"]
    # 16 × 123455 / 16 = 123455 cents; 32 cores → 246910 cents
    assert (dc["cost"]["shortfallCost"], dc["cost"]["demandCost"], dc["cost"]["ownedCost"]) == (1234.55, 2469.1, 1234.55)
    # 5 short cores: 5 × 123455 / 16 = 38579.6875 cents → 38580 (the float product was 385.796875)
    assert A._cost(5, {"minor": 123455, "per": 16, "currency": "USD"}) == 385.80


def test_vnd_prices_have_no_minor_unit():
    s = A._ent_summary([_ent("ws_std", 3, "pack2", price=1_234_567.6, currency="VND")], "ws_std", "core")
    assert s["totalCost"] == 3 * 1_234_568 == 3_703_704
    assert A._cost(3, s["_price"]) == 1_851_852                 # 3 × 1 234 568 / 2 = 1 851 852


def test_old_entitlement_without_minor_reads_like_new():
    """Snapshots stored before release 2 have only `unitPrice` — the result (and its hash) is identical."""
    hosts, vms = _two_host_cluster()
    old = [_ent("ws_dc", 1, "pack16", price=1234.5)]
    new = [_ent("ws_dc", 1, "pack16", price=1234.5, unitPriceMinor=123450)]
    r_old, r_new = A.assess(hosts, vms, _rules(), old, {}), A.assess(hosts, vms, _rules(), new, {})
    assert r_old["windowsServer"]["datacenter"]["cost"] == r_new["windowsServer"]["datacenter"]["cost"]
    assert r_new["windowsServer"]["datacenter"]["cost"]["shortfallCost"] == 1234.5
    assert A.result_hash(r_old) == A.result_hash(r_new)
    assert "_price" not in r_new["windowsServer"]["datacenter"]["entitlement"]   # internal basis never returned


def test_edition_saving_is_exact():
    hosts, vms = _two_host_cluster()                     # Standard 64 cores, Datacenter 32 cores
    ents = [_ent("ws_std", 0, "pack2", price=100.1, kind="none"), _ent("ws_dc", 0, "pack16", price=1000.01, kind="none")]
    rec = A.assess(hosts, vms, _rules(), ents, {})["windowsServer"]["recommendedEdition"]
    # Standard 64 × 10010 / 2 = 320320 cents; Datacenter 32 × 100001 / 16 = 200002 cents → saving 120318 cents
    assert (rec["byCost"], rec["saving"], rec["currency"]) == ("datacenter", 1203.18, "USD")


# ---------------------------------------------------------------------------
# What-if + capacity / N+1 guard
# ---------------------------------------------------------------------------

def _whatif_cluster():
    hosts = [_host(f"esx{i}", 16, 2, ram=256) for i in range(1, 4)]     # 3 × (2 × 8 core, 256 GB)
    vms = [_vm(f"ws{i}", f"esx{1 + i % 3}", vcpu=8, ram=32) for i in range(12)]   # 96 vCPU, 384 GB
    return hosts, vms


def _run(hosts, vms, scenario, ents=None):
    return W.run_whatif(hosts, vms, _rules(), ents or [], {}, capacity.Policy(), scenario, classify_vm)


def test_whatif_remove_host_breaks_n_plus_one():
    hosts, vms = _whatif_cluster()
    out = _run(hosts, vms, {"type": "remove_host", "cluster": "CL1", "count": 1}, [_ent("vvf", 0, price=150, kind="none")])
    # VMware: 3 hosts × 32 cores = 96 → 64; Datacenter 3 × 16 = 48 → 32
    assert out["delta"]["vmwareCores"] == {"before": 96, "after": 64, "delta": -32}
    assert out["delta"]["wsDatacenterCores"] == {"before": 48, "after": 32, "delta": -16}
    assert out["delta"]["vmwareCost"] == {"before": 14400.0, "after": 9600.0, "delta": -4800.0}
    # RAM: 384 GB on 768 GB = 50 % before; 2 hosts (512 GB) → 75 %; losing one more → 150 % → N+1 lost
    [b], [a] = out["capacityBefore"], out["capacityAfter"]
    assert (b["ramPct"], a["ramPct"], b["haSurvives"], a["haSurvives"]) == (50.0, 75.0, True, False)
    assert out["capacityVerdict"] == "violation" and out["recommendable"] is False
    assert any("N+1" in r for r in out["capacityVerdicts"][0]["reasons"])


def test_whatif_add_host_uses_template_and_floors():
    hosts, vms = _whatif_cluster()
    out = _run(hosts, vms, {"type": "add_host", "cluster": "CL1", "count": 2, "cores": 12, "sockets": 2})
    # each new host: VMware max(12, 32) = 32, WS max(12, 16, 16) = 16
    assert out["delta"]["vmwareCores"]["delta"] == 64
    assert out["delta"]["wsDatacenterCores"]["delta"] == 32
    # Standard: ceil(12 / 2) = 6 sets on every host → +2 hosts × 16 × 6 = +192
    assert out["delta"]["wsStandardCores"] == {"before": 288, "after": 480, "delta": 192}
    assert out["capacityVerdict"] == "ok"


def test_whatif_add_windows_vms_raises_standard_sets():
    hosts, vms = _whatif_cluster()
    out = _run(hosts, vms, {"type": "add_vms", "cluster": "CL1", "count": 2, "vcpu": 4, "ramGB": 8, "category": "windows_server"})
    # 12 → 14 WS VMs: ceil(14/2) = 7 sets × 48 licensable cores = 336
    assert out["delta"]["wsStandardCores"] == {"before": 288, "after": 336, "delta": 48}
    assert out["delta"]["windowsServerVms"]["delta"] == 2


def test_whatif_cost_delta_is_exact_decimal():
    hosts, vms = _whatif_cluster()
    ents = [_ent("ws_dc", 0, "pack2", price=0.3, kind="none")]          # 0,15 USD / core
    out = _run(hosts, vms, {"type": "add_host", "cluster": "CL1", "count": 1, "cores": 16, "sockets": 2}, ents)
    # 48 → 64 cores × 0,15 = 7,20 → 9,60: delta 2,40 exactly (float subtraction gives 2.3999999999999995)
    assert out["delta"]["wsDatacenterCost"] == {"before": 7.2, "after": 9.6, "delta": 2.4}


def test_whatif_unknown_scenario_is_rejected():
    hosts, vms = _whatif_cluster()
    with pytest.raises(ValueError):
        W.apply_scenario(hosts, vms, {"type": "explode"})
