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
    for sec in ("sec-summary", "sec-scope", "sec-demand", "sec-options", "sec-design", "sec-scale", "sec-risks", "sec-plan", "app-vms"):
        assert f'id="{sec}"' in html
    assert "ACME" in html and "Partner" in html and "<svg" in html
    brief = report_sizing.render_sizing_html(_vms(), sizing.SizingParams(), _profiles(), detail="brief")
    assert "sec-demand" not in brief


def test_custom_thresholds_change_findings_and_classification():
    from app.sizing_advisory import AdvisoryThresholds
    base = sizing.build_sizing(_vms(), sizing.SizingParams(), _profiles())
    assert base["analysis"]["workload"]["character"] == "memory"
    assert any(f["area"] == "Cân đối tài nguyên" and f["severity"] == "warning" for f in base["analysis"]["findings"])

    th = {"memory_min_gb_per_vcpu": 10, "stranded_warn_pct": 90, "db_keywords": "oracle"}
    r = sizing.build_sizing(_vms(), sizing.SizingParams(advisory=th), _profiles())
    a = r["analysis"]
    assert a["workload"]["character"] == "balanced"
    assert a["workload"]["dbLike"] == ["DB-BIG"]
    assert not any(f["area"] == "Cân đối tài nguyên" and f["severity"] == "warning" for f in a["findings"])
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


def test_excel_dashboard_workbook():
    from app import excel_sizing
    rows = [dict(r) for r in sizing.TEMPLATE_ROWS]
    res = sizing.build_sizing(_vms(), sizing.SizingParams(license_price_per_core=3.5e6), _profiles())
    wb = excel_sizing.build_workbook(res, rows, {"customerName": "ACME", "preparedOrg": "Partner"})
    assert wb.sheetnames[0] == "Dashboard" and wb.sheetnames[-1] == "Dữ liệu biểu đồ"
    assert len(wb["Dashboard"]._charts) == 7
    assert "Giả định & ngưỡng" in wb.sheetnames and "Danh sách VM" in wb.sheetnames
    # minimal input (no prices, no analysis extras) must still build
    small = sizing.build_sizing([sizing.VmSpec(name="x", qty=1, vcpu=2, ram_gb=4)], sizing.SizingParams(), _profiles((None, None, None)))
    excel_sizing.build_workbook(small, [{"name": "x", "qty": 1, "vcpu": 2, "ramGB": 4, "diskGB": 0}], {})


# --- Scenario from a real review (AVEVA PI on vSAN): 16 VM · 158 vCPU · 512 GB · 12 100 GB ----------------
def _pi_case(**kw):
    rows = [("PI Data Archive", 2, 16, 64, 2000, "primary/secondary"), ("PI AF Server", 2, 8, 32, 500, ""),
            ("PI Vision", 2, 8, 32, 300, ""), ("PI Interface", 2, 8, 16, 200, ""), ("SETPOINT Connector Node", 1, 8, 32, 300, ""),
            ("Domain Controller", 2, 4, 16, 200, "AD DS"), ("Backup Server", 1, 8, 32, 4000, "Veeam"),
            ("EWS VM", 2, 16, 48, 500, "EWS VM: engineering"), ("Jump Host", 2, 11, 16, 200, "")]
    vms = [sizing.VmSpec(name=a, qty=b, vcpu=c, ram_gb=d, disk_gb=e, note=f) for a, b, c, d, e, f in rows]
    profs = [sizing.HostProfile(name="2×32C / 384 GB", sockets=2, cores_per_socket=32, ram_gb=384, disk_gb=12000),
             sizing.HostProfile(name="2×32C / 512 GB", sockets=2, cores_per_socket=32, ram_gb=512, disk_gb=12000)]
    base = dict(cpu_ratio=4, growth_pct=15, storage_mode="local", storage_overhead_pct=15, storage_protection_factor=2.0)
    base.update(kw)
    return sizing.build_sizing(vms, sizing.SizingParams(**base), profs)


def test_tight_option_is_not_recommended_and_flagged():
    r = _pi_case()
    by = {x["profile"]["name"]: x for x in r["results"]}
    tight, roomy = by["2×32C / 384 GB"], by["2×32C / 512 GB"]
    assert tight["failure"]["atLimit"] and round(tight["failure"]["ramPct"]) == 80 and tight["spare"]["extraAvgVms"] == 0
    assert roomy["recommended"] and round(roomy["failure"]["ramPct"]) == 59 and roomy["spare"]["extraAvgVms"] == 6
    assert abs(roomy["vcpuPerCore"] - 158 * 1.15 / 192) < 1e-6
    assert "sát ngưỡng" in r["recommendation"]["why"]


def test_fewer_core_variant_suggested_but_not_recommended():
    r = _pi_case()
    sug = [x for x in r["results"] if x.get("suggested")]
    assert any(x["profile"]["coresPerSocket"] == 16 and x["advisory"]["license"]["coresTotal"] == 96 for x in sug)
    assert all(not x["recommended"] and x["rank"] is None for x in sug)


def test_workload_specific_rules():
    r = _pi_case()
    wl = r["analysis"]["workload"]
    assert "PI Data Archive" in wl["dbLike"]
    assert wl["dcCount"] == 2 and wl["backupVms"] == ["Backup Server"]
    titles = " | ".join(f["title"] for f in r["analysis"]["findings"])
    assert "Domain Controller" in titles and "backup" in titles.lower()
    assert "vSAN 3 host" in titles and "Dự trữ dung lượng vSAN" in titles and "HA cần lưu trữ dùng chung" in titles


def test_expand_text_consistent():
    r = _pi_case(annual_growth_pct=40, horizon_years=4)
    pr = r["analysis"]["projection"]
    assert pr["expandYear"] is not None
    assert f"cần mở rộng trước năm thứ {pr['expandYear']}" in r["analysis"]["expandText"] or "ngay trong năm thứ 1" in r["analysis"]["expandText"]
    assert any(r["analysis"]["expandText"] == f["detail"] for f in r["analysis"]["findings"])


def test_full_report_structure_and_vn_numbers():
    from app import report_sizing_full
    r = _pi_case()
    vms = [sizing.VmSpec(name="PI Data Archive", qty=2, vcpu=16, ram_gb=64, disk_gb=2000)]
    html = report_sizing_full.render_full_html(vms, sizing.SizingParams(storage_mode="local", storage_protection_factor=2.0),
                                               [sizing.HostProfile(name="2×32C / 512 GB", sockets=2, cores_per_socket=32, ram_gb=512, disk_gb=12000)],
                                               customer_name="CMC", customer_contact="Ms. Hung", prepared_org="ioBox Solutions",
                                               version="1.1", approved_by="B", document_code="X-1")
    for sec in ("sec-summary", "sec-scope", "sec-demand", "sec-options", "sec-design", "sec-scale", "sec-risks", "sec-plan",
                "sec-method", "app-vms", "app-glossary"):
        assert f'id="{sec}"' in html
    assert "Ngưỡng đánh giá" not in html and "app-thresholds" not in html
    assert "ioBox Solutions · Đề xuất cấu hình máy chủ – CMC" in html and "Ms. Hung" in html and "Phiên bản 1.1" in html
    assert "radar" not in html.lower() or "svg_radar" not in html
    assert report_sizing_full.vn("12,100.5 GB và 0.95 : 1, 2×32C") == "12.100,5 GB và 0,95 : 1, 2×32C"
    assert report_sizing_full._clean_note("EWS VM", "EWS VM: Engineering") == "Engineering"
    assert r["results"]


def test_platform_vocabulary_and_licensing():
    from app import platforms, report_sizing_full
    vms = [sizing.VmSpec(name="APP", qty=6, vcpu=8, ram_gb=32, disk_gb=200), sizing.VmSpec(name="Domain Controller", qty=2, vcpu=2, ram_gb=8)]
    prof = [sizing.HostProfile(name="2×16C / 512 GB", sockets=2, cores_per_socket=16, ram_gb=512, disk_gb=8000)]
    for key in platforms.PLATFORM_KEYS:
        pf = platforms.PLATFORMS[key]
        p = sizing.SizingParams(platform=key, storage_mode="local", storage_protection_factor=2.0,
                                license_min_cores_per_socket=pf["license_min_per_socket"], reserve_ram_gb=pf["reserve"]["ram_gb"])
        r = sizing.build_sizing(vms, p, prof)
        best = r["results"][r["recommendedIndex"]]
        lic = best["advisory"]["license"]
        assert lic["unit"] == pf["license_unit"]
        html = report_sizing_full.render_full_html(vms, p, prof, customer_name="X")
        text = " ".join(f["action"] + f["detail"] + f["title"] for f in r["analysis"]["findings"])
        if key != "vmware":
            # no VMware product names leak into other platforms' advice or design section
            assert "vSphere" not in text and "vSAN" not in text and "DRS" not in text.replace("DRS tách", "")
            design = html[html.index('id="sec-design"'):html.index('id="sec-scale"')]
            assert "ESXi" not in design and "vCenter" not in design and "vSAN" not in design
        if key != "generic":
            assert pf["hypervisor"] in html and pf["hci_short"] in html
    # licence units
    hp = sizing.build_sizing(vms, sizing.SizingParams(platform="proxmox"), prof)
    assert hp["results"][0]["advisory"]["license"]["coresTotal"] == 2 * hp["results"][0]["hostsTotal"]  # sockets
    hv = sizing.build_sizing(vms, sizing.SizingParams(platform="hyperv", license_min_cores_per_socket=8),
                             [sizing.HostProfile(name="1×8C", sockets=1, cores_per_socket=8, ram_gb=256)])
    assert hv["results"][0]["advisory"]["license"]["coresPerHost"] == 16  # Windows Server: min 16 core / host
    # Nutanix CVM reserve too low → warning
    nx = sizing.build_sizing(vms, sizing.SizingParams(platform="nutanix", reserve_ram_gb=16), prof)
    assert any(f["area"] == "Nền tảng" for f in nx["analysis"]["findings"])
