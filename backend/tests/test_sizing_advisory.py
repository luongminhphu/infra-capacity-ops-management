"""Unit tests for the sizing engine + advisory layer (pure Python, no DB)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import sizing  # noqa: E402


def _vms():
    vms = [sizing.VmSpec(name=r["name"], qty=r["qty"], vcpu=r["vcpu"], ram_gb=r["ramGB"], disk_gb=r["diskGB"], note=r["note"])
           for r in sizing.TEMPLATE_ROWS]
    vms.append(sizing.VmSpec(name="DB-BIG", qty=1, vcpu=32, ram_gb=384, disk_gb=4000, note="Oracle"))
    return vms


def _profiles(prices=(300e6, 450e6, 620e6)):
    return [sizing.HostProfile(name=p["name"], sockets=p["sockets"], cores_per_socket=p["coresPerSocket"], ram_gb=p["ramGB"], unit_price=u)
            for p, u in zip(sizing.DEFAULT_PROFILES, prices)]


def test_analysis_shape_and_workload():
    r = sizing.build_sizing(_vms(), sizing.SizingParams(), _profiles())
    a = r["analysis"]
    assert sum(b["count"] for b in a["workload"]["sizeBuckets"]) == r["demand"]["vmCount"]
    assert a["workload"]["character"] == "memory"
    assert a["workload"]["largestVm"]["name"] == "DB-BIG"
    assert "DB-BIG" in a["workload"]["dbLike"]
    assert a["readiness"] in ("ok", "warn", "crit")
    assert a["nextSteps"]
    for res in r["results"]:
        sc = res["advisory"]["scores"]
        assert set(sc) == {ax["key"] for ax in a["scoreAxes"]}
        assert all(0 <= v <= 100 for v in sc.values())


def test_license_minimum_cores_per_socket():
    prof = [sizing.HostProfile(name="2x12C", sockets=2, cores_per_socket=12, ram_gb=512)]
    r = sizing.build_sizing(_vms(), sizing.SizingParams(license_price_per_core=1000.0), prof)
    lic = r["results"][0]["advisory"]["license"]
    hosts = r["results"][0]["hostsTotal"]
    assert lic["coresPerHost"] == 32  # 2 sockets x max(16, 12)
    assert lic["paddedCores"] == 8 * hosts
    assert lic["yearlyCost"] == 1000.0 * 32 * hosts
    assert any(f["area"] == "License" for f in r["analysis"]["findings"])


def test_tco_and_optimize_for_cost():
    params_h = sizing.SizingParams(license_price_per_core=3.5e6)
    params_c = sizing.SizingParams(license_price_per_core=3.5e6, optimize_for="cost")
    rh = sizing.build_sizing(_vms(), params_h, _profiles())
    rc = sizing.build_sizing(_vms(), params_c, _profiles())
    best_h = rh["results"][rh["recommendedIndex"]]
    best_c = rc["results"][rc["recommendedIndex"]]
    assert best_h["hostsTotal"] == min(x["hostsTotal"] for x in rh["results"])
    assert best_c["advisory"]["costs"]["tco"] == min(x["advisory"]["costs"]["tco"] for x in rc["results"])
    c = best_c["advisory"]["costs"]
    assert c["tco"] == c["hardware"] + c["license"]


def test_sensitivity_is_monotonic_in_growth():
    r = sizing.build_sizing(_vms(), sizing.SizingParams(), _profiles())
    for s in r["analysis"]["sensitivity"]["growth"]["series"]:
        assert s["hosts"] == sorted(s["hosts"])


def test_projection_flags_expansion_year():
    r = sizing.build_sizing(_vms(), sizing.SizingParams(annual_growth_pct=40, horizon_years=5), _profiles())
    pr = r["analysis"]["projection"]
    assert len(pr["points"]) == 6
    assert pr["expandYear"] is not None
    assert pr["points"][pr["expandYear"]]["hosts"] > pr["installed"]


def test_no_redundancy_is_critical_and_numa_wide_vm_warned():
    vms = [sizing.VmSpec(name="BIG", qty=2, vcpu=24, ram_gb=256)]
    prof = [sizing.HostProfile(name="2x16C", sockets=2, cores_per_socket=16, ram_gb=512)]
    r = sizing.build_sizing(vms, sizing.SizingParams(redundancy=0), prof)
    sev = {(f["severity"], f["area"]) for f in r["analysis"]["findings"]}
    assert ("critical", "Chịu lỗi") in sev
    assert ("warning", "NUMA") in sev
    assert r["analysis"]["readiness"] == "crit"


def test_empty_request():
    r = sizing.build_sizing([], sizing.SizingParams(), _profiles())
    assert r["recommendedIndex"] is None
    assert r["analysis"]["findings"] == []
    assert r["analysis"]["sensitivity"] is None


def test_pdf_html_renders_all_sections():
    from app import report_sizing
    html = report_sizing.render_sizing_html(_vms(), sizing.SizingParams(license_price_per_core=3.5e6), _profiles(),
                                            customer_name="ACME", prepared_org="Partner", document_code="SZ-1")
    for sec in ("sec-summary", "sec-workload", "sec-options", "sec-design", "sec-sensitivity", "sec-risks", "sec-roadmap", "sec-appendix"):
        assert f'id="{sec}"' in html
    assert "ACME" in html and "Partner" in html and "<svg" in html
    brief = report_sizing.render_sizing_html(_vms(), sizing.SizingParams(), _profiles(), detail="brief")
    assert "sec-workload" not in brief


def test_custom_thresholds_change_findings_and_classification():
    from app.sizing_advisory import AdvisoryThresholds
    base = sizing.build_sizing(_vms(), sizing.SizingParams(), _profiles())
    assert base["analysis"]["workload"]["character"] == "memory"
    assert any(f["area"] == "Cân bằng tài nguyên" and f["severity"] == "warning" for f in base["analysis"]["findings"])

    th = {"memory_min_gb_per_vcpu": 10, "stranded_warn_pct": 90, "db_keywords": "oracle"}
    r = sizing.build_sizing(_vms(), sizing.SizingParams(advisory=th), _profiles())
    a = r["analysis"]
    assert a["workload"]["character"] == "balanced"
    assert a["workload"]["dbLike"] == ["DB-BIG"]
    assert not any(f["area"] == "Cân bằng tài nguyên" and f["severity"] == "warning" for f in a["findings"])
    assert a["thresholds"]["stranded_warn_pct"] == 90
    assert r["params"]["advisory"]["memory_min_gb_per_vcpu"] == 10
    # unknown keys ignored, dataclass instance accepted
    assert AdvisoryThresholds.from_any({"nope": 1}).stranded_warn_pct == 35
    assert sizing.SizingParams(advisory=AdvisoryThresholds(cluster_info_hosts=2)).advisory.cluster_info_hosts == 2


def test_cluster_size_threshold():
    vms = [sizing.VmSpec(name="APP", qty=10, vcpu=4, ram_gb=16)]
    prof = [sizing.HostProfile(name="small", sockets=1, cores_per_socket=16, ram_gb=64)]
    r = sizing.build_sizing(vms, sizing.SizingParams(advisory={"cluster_info_hosts": 2, "cluster_warn_hosts": 3}), prof)
    assert any(f["area"] == "Kiến trúc" and f["severity"] == "warning" for f in r["analysis"]["findings"])
