"""Advisory layer on top of the host-sizing math in `sizing.py`.

`sizing.build_sizing()` answers "how many hosts of each candidate config?".
This module answers the consulting questions a customer / partner asks next,
and produces the data behind every chart of the sizing report:

* workload profile — VM size distribution (T-shirt sizes), GB RAM per vCPU
  (compute- / balanced / memory-intensive), top consumers (Pareto);
* demand waterfall — requested → growth → overcommit → headroom → N+x;
* per candidate — NUMA fit, CPU/RAM balance (stranded capacity and the RAM
  per host that would balance it), licensing cores (per-core subscription
  with a 16-core/socket minimum), hardware + license TCO and unit costs,
  growth runway, a 0–100 scorecard for the radar chart;
* sensitivity — hosts needed vs. CPU overcommit target / growth buffer /
  max utilisation, for every candidate;
* multi-year projection — hosts needed per year at an annual growth rate;
* findings (risks & advisories with severity and action) and next steps.

Pure Python, no I/O, no app imports beyond `sizing` — so it can be served by
this app, a standalone sizing micro-service, or a CLI unchanged.
"""
from __future__ import annotations

import copy
import math
import re
from typing import Any, Dict, List, Optional

# T-shirt sizes by (vCPU, RAM GB) upper bounds — a VM lands in the first bucket
# whose both bounds it fits.
SIZE_BUCKETS = [
    ("S", "S (≤2 vCPU · ≤8 GB)", 2, 8),
    ("M", "M (≤4 vCPU · ≤16 GB)", 4, 16),
    ("L", "L (≤8 vCPU · ≤64 GB)", 8, 64),
    ("XL", "XL (≤16 vCPU · ≤128 GB)", 16, 128),
    ("XXL", "XXL (lớn hơn)", math.inf, math.inf),
]

# Names / notes hinting at latency-sensitive workloads that deserve a lower
# CPU overcommit than general app servers.
_DB_HINT = re.compile(r"\b(db|database|sql|mssql|mysql|postgres|postgresql|oracle|mongo|mongodb|redis|kafka|elastic|elasticsearch|sap|hana|cassandra)\b", re.I)

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}


def _fin(v: Any) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _gb_txt(v: float) -> str:
    if v >= 1024:
        return f"{v / 1024:,.1f} TB"
    return f"{v:,.0f} GB"


# ---------------------------------------------------------------------------
# Workload profile
# ---------------------------------------------------------------------------
def workload_profile(vms, demand: Dict[str, Any]) -> Dict[str, Any]:
    buckets = {k: {"key": k, "label": lbl, "count": 0, "vcpu": 0.0, "ramGB": 0.0} for k, lbl, _, _ in SIZE_BUCKETS}
    groups = []
    largest = None
    db_like = []
    for v in vms:
        q = max(0, int(v.qty))
        if q <= 0:
            continue
        for k, _, max_cpu, max_ram in SIZE_BUCKETS:
            if v.vcpu <= max_cpu and v.ram_gb <= max_ram:
                b = buckets[k]
                b["count"] += q
                b["vcpu"] += q * v.vcpu
                b["ramGB"] += q * v.ram_gb
                break
        groups.append({"name": v.name, "qty": q, "vcpu": q * v.vcpu, "ramGB": q * v.ram_gb, "diskGB": q * v.disk_gb,
                       "vcpuEach": v.vcpu, "ramEach": v.ram_gb})
        if largest is None or (v.vcpu, v.ram_gb) > (largest["vcpu"], largest["ramGB"]):
            largest = {"name": v.name, "vcpu": v.vcpu, "ramGB": v.ram_gb}
        if _DB_HINT.search(f"{v.name} {v.note}"):
            db_like.append(v.name)

    tot_cpu = demand["vcpu"] or 1.0
    tot_ram = demand["ramGB"] or 1.0
    tot_disk = demand["diskGB"] or 1.0
    for g in groups:
        g["vcpuShare"] = g["vcpu"] / tot_cpu * 100
        g["ramShare"] = g["ramGB"] / tot_ram * 100
        g["diskShare"] = g["diskGB"] / tot_disk * 100 if demand["diskGB"] else 0.0
    top = sorted(groups, key=lambda g: (g["vcpuShare"] + g["ramShare"]), reverse=True)

    # Pareto: how many lines make up 80% of RAM (RAM is usually the binding resource).
    acc, pareto_n = 0.0, 0
    for g in sorted(groups, key=lambda g: g["ramGB"], reverse=True):
        acc += g["ramShare"]
        pareto_n += 1
        if acc >= 80:
            break

    ratio = (demand["ramGB"] / demand["vcpu"]) if demand["vcpu"] else 0.0
    if ratio <= 3:
        character, char_label = "compute", "Thiên về tính toán (compute-intensive)"
    elif ratio <= 6:
        character, char_label = "balanced", "Cân bằng CPU / RAM (general purpose)"
    else:
        character, char_label = "memory", "Thiên về bộ nhớ (memory-intensive)"

    return {
        "sizeBuckets": [buckets[k] for k, _, _, _ in SIZE_BUCKETS],
        "ramPerVcpu": ratio,
        "character": character,
        "characterLabel": char_label,
        "topGroups": top[:10],
        "groupCount": len(groups),
        "paretoLines": pareto_n,
        "largestVm": largest,
        "dbLike": db_like,
    }


# ---------------------------------------------------------------------------
# Demand waterfall (what each assumption adds) for the recommended profile
# ---------------------------------------------------------------------------
def demand_waterfall(demand: Dict[str, Any], params, best: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    util = max(0.05, min(1.0, params.max_util_pct / 100.0))
    out: Dict[str, Any] = {}
    # CPU in vCPU-equivalents, RAM in GB — each step is a cumulative bar.
    cpu_steps = [
        {"label": "Yêu cầu", "value": demand["vcpu"]},
        {"label": f"+{params.growth_pct:g}% tăng trưởng", "value": demand["vcpuPlanned"]},
        {"label": f"Headroom {params.max_util_pct:g}%", "value": demand["vcpuPlanned"] / util},
    ]
    ram_steps = [
        {"label": "Yêu cầu", "value": demand["ramGB"]},
        {"label": f"+{params.growth_pct:g}% tăng trưởng", "value": demand["ramPlannedGB"]},
        {"label": f"Headroom {params.max_util_pct:g}%", "value": demand["ramPlannedGB"] / util},
    ]
    if best and best["hostsTotal"]:
        ph = best["perHost"]
        cpu_steps.append({"label": f"Lắp đặt ({best['hostsTotal']} host)",
                          "value": ph["logicalCpu"] * params.cpu_ratio * best["hostsTotal"]})
        ram_steps.append({"label": f"Lắp đặt ({best['hostsTotal']} host)",
                          "value": ph["usableRamGB"] * params.ram_ratio * best["hostsTotal"]})
    out["cpu"] = cpu_steps
    out["ram"] = ram_steps
    return out


# ---------------------------------------------------------------------------
# Per-candidate analysis
# ---------------------------------------------------------------------------
def _numa(profile: Dict[str, Any], vms, params) -> Dict[str, Any]:
    cps = profile["coresPerSocket"]
    sockets = max(1, profile["sockets"])
    ram_socket = profile["ramGB"] / sockets
    usable_logical = max(0.0, profile["cores"] - params.reserve_cores) * max(1.0, params.ht_factor)
    usable_ram = max(0.0, profile["ramGB"] - params.reserve_ram_gb)
    wide_cpu, wide_ram, no_fit = [], [], []
    for v in vms:
        if v.qty <= 0:
            continue
        if v.vcpu > usable_logical or v.ram_gb > usable_ram:
            no_fit.append(v.name)
        elif v.vcpu > cps:
            wide_cpu.append(v.name)
        elif v.ram_gb > ram_socket:
            wide_ram.append(v.name)
    wide_vm_count = sum(v.qty for v in vms if v.name in set(wide_cpu + wide_ram + no_fit))
    total = sum(max(0, v.qty) for v in vms) or 1
    return {"coresPerSocket": cps, "ramPerSocketGB": ram_socket, "wideCpu": wide_cpu, "wideRam": wide_ram,
            "noFit": no_fit, "wideVmPct": wide_vm_count / total * 100, "ok": not (wide_cpu or wide_ram or no_fit)}


def _balance(r: Dict[str, Any], demand: Dict[str, Any], params) -> Dict[str, Any]:
    cu = r["after"]["cpuUtilPct"] or 0.0
    ru = r["after"]["ramUtilPct"] or 0.0
    # Stranded = capacity of the non-binding resource left idle when the binding one reaches its target.
    hi = max(cu, ru)
    stranded_cpu = (1 - cu / hi) * 100 if hi > 0 and ru >= cu else 0.0
    stranded_ram = (1 - ru / hi) * 100 if hi > 0 and cu > ru else 0.0
    # RAM per host that would make CPU and RAM run out together for this workload.
    util = max(0.05, min(1.0, params.max_util_pct / 100.0))
    ph = r["perHost"]
    ideal_ram = None
    if demand["vcpuPlanned"] > 0 and ph["vcpuCapacity"] > 0:
        ram_per_vcpu = demand["ramPlannedGB"] / demand["vcpuPlanned"]
        ideal_ram = ph["vcpuCapacity"] * ram_per_vcpu / (params.ram_ratio * util) + params.reserve_ram_gb
    # Cores per host that would balance the configured RAM.
    ideal_cores = None
    if demand["ramPlannedGB"] > 0 and ph["vramCapacityGB"] > 0:
        vcpu_per_gb = demand["vcpuPlanned"] / demand["ramPlannedGB"]
        ideal_cores = ph["vramCapacityGB"] * vcpu_per_gb / (params.cpu_ratio * max(1.0, params.ht_factor) * util) + params.reserve_cores
    return {"cpuUtilPct": cu, "ramUtilPct": ru, "strandedCpuPct": stranded_cpu, "strandedRamPct": stranded_ram,
            "idealRamPerHostGB": ideal_ram, "idealCoresPerHost": ideal_cores,
            "gapPct": abs(cu - ru)}


def _license(r: Dict[str, Any], params) -> Dict[str, Any]:
    p = r["profile"]
    min_cps = max(0, int(params.license_min_cores_per_socket or 0))
    per_host = p["sockets"] * max(min_cps, p["coresPerSocket"])
    total = per_host * r["hostsTotal"]
    padded = (per_host - p["cores"]) * r["hostsTotal"]
    yearly = (params.license_price_per_core * total) if params.license_price_per_core is not None else None
    return {"coresPerHost": per_host, "coresTotal": total, "paddedCores": max(0, padded),
            "pricePerCoreYear": params.license_price_per_core, "yearlyCost": yearly}


def _costs(r: Dict[str, Any], lic: Dict[str, Any], demand: Dict[str, Any], params) -> Dict[str, Any]:
    hw = r["budget"]
    years = max(1, int(params.horizon_years or 1))
    lic_total = lic["yearlyCost"] * years if lic["yearlyCost"] is not None else None
    if hw is None and lic_total is None:
        return {"hardware": None, "license": None, "tco": None, "years": years, "perVm": None, "perVcpu": None, "perGbRam": None, "perVmMonth": None}
    tco = (hw or 0.0) + (lic_total or 0.0)
    vms = demand["vmCount"] or 0
    return {
        "hardware": hw, "license": lic_total, "tco": tco, "years": years,
        "perVm": tco / vms if vms else None,
        "perVcpu": tco / demand["vcpu"] if demand["vcpu"] else None,
        "perGbRam": tco / demand["ramGB"] if demand["ramGB"] else None,
        "perVmMonth": tco / vms / (12 * years) if vms else None,
    }


def _runway(r: Dict[str, Any], demand: Dict[str, Any]) -> Dict[str, Any]:
    """Extra growth (beyond the planned buffer) the installed cluster absorbs while still keeping N+x."""
    n_fail = r["failure"]["hostsLeft"]
    ph = r["perHost"]
    if not n_fail or not demand["vcpuPlanned"] or not demand["ramPlannedGB"]:
        return {"extraGrowthPct": None, "totalGrowthPct": None}
    cap_cpu = ph["vcpuCapacity"] * n_fail
    cap_ram = ph["vramCapacityGB"] * n_fail
    mult = min(cap_cpu / demand["vcpuPlanned"], cap_ram / demand["ramPlannedGB"])
    total_mult = min(cap_cpu / demand["vcpu"], cap_ram / demand["ramGB"]) if demand["vcpu"] and demand["ramGB"] else None
    return {"extraGrowthPct": (mult - 1) * 100, "totalGrowthPct": (total_mult - 1) * 100 if total_mult is not None else None}


def _scores(rows: List[Dict[str, Any]]) -> None:
    """0–100 per axis, higher = better; relative axes compare against the best candidate."""
    valid = [r for r in rows if r["hostsTotal"]]
    if not valid:
        return
    min_hosts = min(r["hostsTotal"] for r in valid)
    tcos = [r["advisory"]["costs"]["tco"] for r in valid if r["advisory"]["costs"]["tco"]]
    min_tco = min(tcos) if tcos else None
    min_lic = min(r["advisory"]["license"]["coresTotal"] for r in valid) or 1
    for r in rows:
        a = r["advisory"]
        if not r["hostsTotal"]:
            a["scores"] = None
            continue
        eff = r["efficiency"]
        eff_bind = (eff["cpuPct"] if r["binding"].startswith("CPU") else eff["ramPct"]) or 0.0
        f = r["failure"]
        worst_fail = max(f["cpuPct"] or 0, f["ramPct"] or 0)
        if r["redundancy"] == 0:
            resil = 25.0
        elif not f["ok"]:
            resil = 10.0
        else:
            resil = _clamp(100 - max(0.0, worst_fail - 60) * 2.0, 30, 100)
        if min_tco and a["costs"]["tco"]:
            cost = min_tco / a["costs"]["tco"] * 100
        else:  # no prices → licensing cores as the cost proxy
            cost = min_lic / max(1, a["license"]["coresTotal"]) * 100
        runway = a["runway"]["extraGrowthPct"]
        a["scores"] = {
            "compact": _clamp(min_hosts / r["hostsTotal"] * 100),
            "cost": _clamp(cost),
            "efficiency": _clamp(eff_bind),
            "balance": _clamp(100 - a["balance"]["gapPct"] * 1.5),
            "resilience": resil,
            "scalability": _clamp((runway or 0) * 2) if runway is not None else 0.0,
            "numa": _clamp(100 - a["numa"]["wideVmPct"] * 2) if not a["numa"]["noFit"] else 0.0,
        }
        a["scoreTotal"] = sum(a["scores"].values()) / len(a["scores"])


SCORE_AXES = [
    ("compact", "Số host"), ("cost", "Chi phí"), ("efficiency", "Hiệu suất"), ("balance", "Cân bằng"),
    ("resilience", "Chịu lỗi"), ("scalability", "Mở rộng"), ("numa", "NUMA"),
]


# ---------------------------------------------------------------------------
# Sensitivity & projection
# ---------------------------------------------------------------------------
def _hosts_for(profile_obj, vms, params) -> int:
    from . import sizing
    demand = sizing.summarize_demand(vms, params)
    return sizing.size_profile(profile_obj, demand, params)["hostsTotal"]


def sensitivity(vms, params, profiles) -> Dict[str, Any]:
    def sweep(attr: str, values: List[float]) -> Dict[str, Any]:
        series = []
        for prof in profiles:
            pts = []
            for x in values:
                p2 = copy.copy(params)
                setattr(p2, attr, x)
                pts.append(_hosts_for(prof, vms, p2))
            series.append({"name": prof.name, "hosts": pts})
        return {"x": values, "series": series, "current": getattr(params, attr)}

    cpu_vals = sorted(set([1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0] + [float(params.cpu_ratio)]))
    growth_vals = sorted(set([0.0, 10.0, 20.0, 30.0, 50.0, 75.0, 100.0] + [float(params.growth_pct)]))
    util_vals = sorted(set([60.0, 70.0, 75.0, 80.0, 85.0, 90.0] + [float(params.max_util_pct)]))
    return {"cpuRatio": sweep("cpu_ratio", cpu_vals), "growth": sweep("growth_pct", growth_vals),
            "maxUtil": sweep("max_util_pct", util_vals)}


def projection(vms, params, profile_obj, installed: int) -> Dict[str, Any]:
    """Hosts needed year by year when the requested load grows `annual_growth_pct`/year
    (the planning growth buffer is replaced by the compounded growth)."""
    years = max(1, min(10, int(params.horizon_years or 3)))
    rate = max(0.0, params.annual_growth_pct or 0.0)
    pts = []
    first_short = None
    for y in range(0, years + 1):
        p2 = copy.copy(params)
        p2.growth_pct = ((1 + rate / 100.0) ** y - 1) * 100.0
        n = _hosts_for(profile_obj, vms, p2)
        pts.append({"year": y, "hosts": n, "growthPct": p2.growth_pct})
        if first_short is None and n > installed:
            first_short = y
    return {"years": years, "annualGrowthPct": rate, "installed": installed, "points": pts, "expandYear": first_short}


# ---------------------------------------------------------------------------
# Findings (risks & advisories) and next steps
# ---------------------------------------------------------------------------
def _finding(sev: str, area: str, title: str, detail: str, action: str) -> Dict[str, str]:
    return {"severity": sev, "area": area, "title": title, "detail": detail, "action": action}


def findings(demand, params, best, workload, proj, results=None) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not best or not demand["vmCount"]:
        return out
    a = best["advisory"]
    prof = best["profile"]
    f = best["failure"]

    # Alternative that is cheaper (when optimising for hosts) or more compact (when optimising for cost).
    best_tco = a["costs"]["tco"]
    for r in results or []:
        if r is best or not r["hostsTotal"]:
            continue
        tco = r["advisory"]["costs"]["tco"]
        if best_tco and tco and tco < best_tco * 0.97 and r["failure"]["ok"]:
            out.append(_finding("info", "Chi phí", f"Phương án {r['profile']['name']} rẻ hơn {(1 - tco / best_tco) * 100:.0f}% TCO",
                                f"{r['hostsTotal']} host thay vì {best['hostsTotal']}; vẫn đạt N+{r['redundancy']}.",
                                "Chuyển tiêu chí tối ưu sang 'Chi phí thấp nhất' nếu ưu tiên ngân sách hơn số host / không gian rack."))
            break

    # Resilience
    if best["redundancy"] == 0:
        out.append(_finding("critical", "Chịu lỗi", "Không có dự phòng host (N)",
                            "Một host hỏng hoặc bảo trì sẽ làm thiếu năng lực cho toàn bộ dự án.",
                            "Chọn tối thiểu N+1 cho môi trường Production."))
    elif not f["ok"]:
        out.append(_finding("critical", "Chịu lỗi", f"Chưa đạt dự phòng N+{best['redundancy']}",
                            f"Mất {best['redundancy']} host thì phần còn lại phải gánh CPU {f['cpuPct'] or 0:.0f}% · RAM {f['ramPct'] or 0:.0f}% năng lực mục tiêu.",
                            "Thêm host hoặc hạ overcommit mục tiêu."))
    else:
        out.append(_finding("ok", "Chịu lỗi", f"Đạt dự phòng N+{best['redundancy']}",
                            f"Khi mất {best['redundancy']} host: CPU {f['cpuPct'] or 0:.0f}% · RAM {f['ramPct'] or 0:.0f}% năng lực mục tiêu.",
                            f"Bật vSphere HA (admission control dành năng lực tương ứng N+{best['redundancy']}) và DRS."))
    if best["hostsTotal"] == 2 and best["redundancy"] >= 1:
        out.append(_finding("info", "Chịu lỗi", "Cluster 2 host: 50% năng lực dành cho dự phòng",
                            "Khi một host bảo trì, cluster chạy không còn dự phòng.",
                            "Cân nhắc 3 host cấu hình nhỏ hơn để giảm phần năng lực 'nằm chờ' và bảo trì an toàn."))

    # Fit / NUMA
    numa = a["numa"]
    if numa["noFit"]:
        out.append(_finding("critical", "Cấu hình VM", "VM không vừa một host",
                            f"{', '.join(numa['noFit'][:4])} cần nhiều vCPU/RAM hơn năng lực khả dụng của một host {prof['name']}.",
                            "Chọn host lớn hơn hoặc chia nhỏ VM (scale-out)."))
    if numa["wideCpu"]:
        out.append(_finding("warning", "NUMA", f"{len(numa['wideCpu'])} nhóm VM vượt số core của một socket",
                            f"{', '.join(numa['wideCpu'][:4])} có vCPU > {prof['coresPerSocket']} cores/socket → VM trải trên nhiều NUMA node, tăng độ trễ bộ nhớ.",
                            "Cấu hình vNUMA/cores-per-socket đúng topology, hoặc chọn CPU nhiều core hơn mỗi socket."))
    if numa["wideRam"]:
        out.append(_finding("warning", "NUMA", f"{len(numa['wideRam'])} nhóm VM vượt RAM của một NUMA node",
                            f"{', '.join(numa['wideRam'][:4])} có RAM > {numa['ramPerSocketGB']:.0f} GB/socket.",
                            "Tăng RAM mỗi host hoặc giảm RAM cấp phát của VM nếu thực tế dùng thấp."))

    # Balance
    bal = a["balance"]
    # Only meaningful when the cluster is sized by load, not by the minimum host count.
    loaded = max(bal["cpuUtilPct"], bal["ramUtilPct"]) >= 40
    if not loaded:
        pass
    elif bal["strandedCpuPct"] >= 35 and bal["idealRamPerHostGB"]:
        out.append(_finding("warning", "Cân bằng tài nguyên", f"Dư CPU ~{bal['strandedCpuPct']:.0f}% do RAM là ràng buộc",
                            f"Workload {workload['ramPerVcpu']:.1f} GB RAM/vCPU; với {prof['cores']} cores/host, RAM cân bằng ≈ {_gb_txt(bal['idealRamPerHostGB'])}/host (hiện {_gb_txt(prof['ramGB'])}).",
                            "Đề nghị nhà cung cấp báo giá cấu hình nhiều RAM hơn — thường giảm được số host và license."))
    elif bal["strandedRamPct"] >= 35 and bal["idealCoresPerHost"]:
        out.append(_finding("warning", "Cân bằng tài nguyên", f"Dư RAM ~{bal['strandedRamPct']:.0f}% do CPU là ràng buộc",
                            f"Với {_gb_txt(prof['ramGB'])}/host, số core cân bằng ≈ {bal['idealCoresPerHost']:.0f} cores/host (hiện {prof['cores']}).",
                            "Chọn CPU nhiều core hơn hoặc giảm RAM/host để tối ưu chi phí."))
    elif bal["gapPct"] < 15:
        out.append(_finding("ok", "Cân bằng tài nguyên", "CPU và RAM được dùng cân bằng",
                            f"Mức dùng CPU {bal['cpuUtilPct']:.0f}% · RAM {bal['ramUtilPct']:.0f}% năng lực — ít tài nguyên bị lãng phí.", "Giữ tỷ lệ cấu hình này."))

    # Overcommit policy
    if params.ram_ratio > 1.0:
        out.append(_finding("warning", "Overcommit", f"Overcommit RAM {params.ram_ratio:g}x",
                            "Overcommit bộ nhớ dễ gây ballooning / swap khi tải cao, ảnh hưởng hiệu năng toàn cluster.",
                            "Giữ RAM 1.0x cho Production; chỉ overcommit RAM ở Dev/Test."))
    if workload["dbLike"] and params.cpu_ratio > 2.0:
        out.append(_finding("warning", "Overcommit", "Có workload CSDL / middleware nhạy độ trễ",
                            f"{', '.join(workload['dbLike'][:4])} chạy chung overcommit CPU {params.cpu_ratio:g}x.",
                            "Đặt CPU reservation hoặc tách cluster/host group riêng với overcommit ≤ 2x cho các VM này."))
    elif params.cpu_ratio >= 5:
        out.append(_finding("warning", "Overcommit", f"Overcommit CPU mục tiêu cao ({params.cpu_ratio:g}x)",
                            "Phù hợp Dev/Test, rủi ro CPU ready cao với Production.", "Prod nên 2–4x; theo dõi CPU Ready < 5%."))

    # Growth
    run = a["runway"]["extraGrowthPct"]
    if run is not None and run < 10:
        out.append(_finding("warning", "Tăng trưởng", "Dư địa tăng trưởng thấp",
                            f"Ngoài +{params.growth_pct:g}% đã dự phòng, cluster chỉ chịu thêm ~{max(0, run):.0f}% tải trước khi mất N+{best['redundancy']}.",
                            "Cân nhắc thêm 1 host hoặc cấu hình lớn hơn nếu dự án có kế hoạch mở rộng."))
    if proj and proj.get("expandYear") is not None:
        out.append(_finding("info", "Tăng trưởng", f"Cần mở rộng vào năm thứ {proj['expandYear']}",
                            f"Với tăng trưởng {proj['annualGrowthPct']:g}%/năm, nhu cầu vượt {proj['installed']} host đã lắp sau {proj['expandYear']} năm.",
                            "Đưa hạng mục mở rộng vào kế hoạch ngân sách năm tương ứng."))
    if params.growth_pct <= 0:
        out.append(_finding("info", "Tăng trưởng", "Chưa dự phòng tăng trưởng",
                            "Yêu cầu của team Dev thường tăng 15–30% trong năm đầu vận hành.", "Đặt dự phòng tăng trưởng 15–25%."))

    # Licensing
    lic = a["license"]
    if lic["paddedCores"] > 0:
        out.append(_finding("info", "License", f"Trả license cho {lic['paddedCores']} core không dùng tới",
                            f"License VMware tính tối thiểu {params.license_min_cores_per_socket} core/socket; cấu hình {prof['coresPerSocket']} cores/socket bị làm tròn lên.",
                            f"Chọn CPU ≥ {params.license_min_cores_per_socket} cores/socket để tận dụng license đã trả."))

    # Scale / cluster size
    if best["hostsTotal"] > 64:
        out.append(_finding("warning", "Kiến trúc", f"Cluster lớn ({best['hostsTotal']} host)",
                            "Cluster quá lớn khó vận hành, nâng cấp và khoanh vùng sự cố.", "Chia thành nhiều cluster ≤ 32–64 host hoặc chọn host lớn hơn."))
    elif best["hostsTotal"] > 32:
        out.append(_finding("info", "Kiến trúc", f"{best['hostsTotal']} host — cân nhắc chia cluster",
                            "Vẫn trong giới hạn vSphere nhưng nên chia theo tầng ứng dụng / miền sự cố.", "Thiết kế 2+ cluster, mỗi cluster có N+1 riêng."))

    # Storage
    st = best["storage"]
    if params.storage_mode == "local":
        if st.get("ok") is False:
            out.append(_finding("critical", "Lưu trữ", "Thiếu đĩa cục bộ",
                                f"Cần ~{_gb_txt(st.get('perHostNeededGB') or 0)}/host nhưng cấu hình chỉ có {_gb_txt(prof['diskGB'] or 0)}.",
                                "Tăng số/kích thước ổ đĩa mỗi host hoặc chuyển sang SAN."))
        min_vsan = 4 if (params.storage_protection_factor or 1) < 1.9 and (params.storage_protection_factor or 1) > 1.0 else 3
        if best["hostsTotal"] < min_vsan:
            out.append(_finding("warning", "Lưu trữ", f"vSAN cần tối thiểu {min_vsan} host",
                                f"Hệ số bảo vệ ×{params.storage_protection_factor:g} yêu cầu ≥ {min_vsan} host.", f"Nâng cluster lên {min_vsan} host."))
    else:
        out.append(_finding("info", "Lưu trữ", f"Cấp {_gb_txt(demand['storageNeededGB'])} trên SAN",
                            f"Đã gồm +{params.growth_pct:g}% tăng trưởng và +{params.storage_overhead_pct:g}% overhead.",
                            "Xác nhận dung lượng trống, IOPS và chính sách snapshot/replication với nhóm Storage."))

    out.sort(key=lambda x: SEVERITY_ORDER.get(x["severity"], 9))
    return out


def next_steps(demand, params, best, n_findings_crit: int) -> List[Dict[str, str]]:
    if not best:
        return []
    p = best["profile"]
    steps = [
        {"phase": "1. Xác nhận yêu cầu", "when": "Tuần 1",
         "what": f"Rà soát danh sách {demand['vmCount']} VM và giả định (overcommit {params.cpu_ratio:g}x, tăng trưởng {params.growth_pct:g}%, N+{params.redundancy}) với chủ dự án."},
        {"phase": "2. Báo giá & lựa chọn", "when": "Tuần 2–3",
         "what": f"Lấy báo giá 2–3 nhà cung cấp cho {best['hostsTotal']} host {p['name']}; so sánh TCO {params.horizon_years} năm gồm license và bảo hành."},
        {"phase": "3. Phê duyệt", "when": "Tuần 3–4", "what": "Trình duyệt ngân sách và chủ trương đầu tư kèm báo cáo này."},
        {"phase": "4. Triển khai", "when": "Sau khi có thiết bị",
         "what": "Lắp đặt, cấu hình cluster: vSphere HA (admission control), DRS, EVC, VM/host group cho CSDL, chuẩn hoá template VM."},
        {"phase": "5. Kiểm thử", "when": "Trước go-live", "what": f"Kiểm thử chịu lỗi: tắt {max(1, best['redundancy'])} host, xác nhận VM khởi động lại và hiệu năng đạt yêu cầu."},
        {"phase": "6. Vận hành & tối ưu", "when": "30 / 60 / 90 ngày",
         "what": "Theo dõi CPU Ready, RAM active, right-size VM cấp thừa; đánh giá lại sizing sau 6 tháng."},
    ]
    if n_findings_crit:
        steps.insert(1, {"phase": "0. Xử lý rủi ro", "when": "Trước báo giá", "what": f"Giải quyết {n_findings_crit} rủi ro nghiêm trọng nêu trong mục Rủi ro & khuyến nghị."})
    return steps


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def annotate_results(results: List[Dict[str, Any]], vms, params, demand: Dict[str, Any]) -> None:
    """Attach the per-candidate `advisory` block (needed before ranking, which uses TCO)."""
    for r in results:
        lic = _license(r, params)
        r["advisory"] = {
            "numa": _numa(r["profile"], vms, params),
            "balance": _balance(r, demand, params),
            "license": lic,
            "costs": _costs(r, lic, demand, params),
            "runway": _runway(r, demand),
        }
    _scores(results)


def build_advisory(vms, params, profiles, demand: Dict[str, Any], results: List[Dict[str, Any]],
                   best_idx: Optional[int]) -> Dict[str, Any]:
    if results and "advisory" not in results[0]:
        annotate_results(results, vms, params, demand)

    best = results[best_idx] if best_idx is not None else None
    wl = workload_profile(vms, demand)
    proj = projection(vms, params, profiles[best_idx], best["hostsTotal"]) if best else None
    fnd = findings(demand, params, best, wl, proj, results)
    n_crit = sum(1 for x in fnd if x["severity"] == "critical")
    readiness = "crit" if n_crit else ("warn" if any(x["severity"] == "warning" for x in fnd) else "ok")
    return {
        "workload": wl,
        "waterfall": demand_waterfall(demand, params, best),
        "sensitivity": sensitivity(vms, params, profiles) if demand["vmCount"] else None,
        "projection": proj,
        "findings": fnd,
        "findingCounts": {k: sum(1 for x in fnd if x["severity"] == k) for k in SEVERITY_ORDER},
        "readiness": readiness,
        "nextSteps": next_steps(demand, params, best, n_crit),
        "scoreAxes": [{"key": k, "label": lbl} for k, lbl in SCORE_AXES],
    }
