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
from dataclasses import dataclass, fields
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

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}

DEFAULT_DB_KEYWORDS = "db, database, sql, mssql, mysql, postgres, postgresql, oracle, mongo, mongodb, redis, kafka, elastic, elasticsearch, sap, hana, cassandra"


@dataclass
class AdvisoryThresholds:
    """Tunable thresholds behind the workload classification, findings and
    scorecard. Defaults are common VMware practice; a partner / customer can
    override any of them per request (``params.advisory``) without code changes."""
    compute_max_gb_per_vcpu: float = 3.0     # ≤ → compute-intensive
    memory_min_gb_per_vcpu: float = 6.0      # > → memory-intensive
    stranded_warn_pct: float = 35.0          # idle share of the non-binding resource that triggers a warning
    balance_ok_gap_pct: float = 15.0         # |CPU% − RAM%| below this = balanced
    balance_min_load_pct: float = 40.0       # balance findings only when the binding resource is at least this loaded
    db_cpu_ratio_max: float = 2.0            # max CPU overcommit for DB / latency-sensitive VMs
    high_cpu_ratio_warn: float = 5.0         # CPU overcommit target considered high for Production
    ram_ratio_warn: float = 1.0              # RAM overcommit above this triggers a warning
    low_runway_pct: float = 10.0             # extra growth headroom below this triggers a warning
    failover_comfort_pct: float = 60.0       # load after host loss up to this scores 100 on resilience
    cheaper_alt_pct: float = 3.0             # flag an alternative at least this % cheaper (TCO)
    cluster_info_hosts: int = 32             # suggest splitting the cluster above this
    cluster_warn_hosts: int = 64             # warn above this
    db_keywords: str = DEFAULT_DB_KEYWORDS   # comma-separated name/note keywords marking DB-like VMs

    @classmethod
    def from_any(cls, v: Any) -> "AdvisoryThresholds":
        if isinstance(v, cls):
            return v
        if isinstance(v, dict):
            known = {f.name for f in fields(cls)}
            return cls(**{k: val for k, val in v.items() if k in known and val is not None})
        return cls()


# Labels / units for UI forms and the report's methodology section.
THRESHOLD_META = [
    ("compute_max_gb_per_vcpu", "Workload thiên CPU khi RAM/vCPU ≤", "GB"),
    ("memory_min_gb_per_vcpu", "Workload thiên RAM khi RAM/vCPU >", "GB"),
    ("stranded_warn_pct", "Cảnh báo tài nguyên thừa khi ≥", "%"),
    ("balance_ok_gap_pct", "Coi là cân bằng khi chênh CPU/RAM <", "%"),
    ("balance_min_load_pct", "Chỉ xét cân bằng khi tải ràng buộc ≥", "%"),
    ("db_cpu_ratio_max", "Overcommit CPU tối đa cho CSDL", "x"),
    ("high_cpu_ratio_warn", "Cảnh báo overcommit CPU mục tiêu ≥", "x"),
    ("ram_ratio_warn", "Cảnh báo overcommit RAM >", "x"),
    ("low_runway_pct", "Cảnh báo dư địa tăng trưởng <", "%"),
    ("failover_comfort_pct", "Tải khi mất host đạt điểm tối đa ≤", "%"),
    ("cheaper_alt_pct", "Gợi ý phương án rẻ hơn khi TCO thấp hơn ≥", "%"),
    ("cluster_info_hosts", "Gợi ý chia cluster khi số host >", "host"),
    ("cluster_warn_hosts", "Cảnh báo cluster quá lớn khi số host >", "host"),
    ("db_keywords", "Từ khoá nhận diện VM CSDL / middleware", ""),
]


def thresholds_of(params) -> AdvisoryThresholds:
    return AdvisoryThresholds.from_any(getattr(params, "advisory", None))


def _db_regex(keywords: str):
    words = [re.escape(w.strip()) for w in (keywords or "").split(",") if w.strip()]
    return re.compile(r"\b(" + "|".join(words) + r")\b", re.I) if words else None


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
def workload_profile(vms, demand: Dict[str, Any], th: Optional[AdvisoryThresholds] = None) -> Dict[str, Any]:
    th = th or AdvisoryThresholds()
    db_re = _db_regex(th.db_keywords)
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
        if db_re and db_re.search(f"{v.name} {v.note}"):
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
    if ratio <= th.compute_max_gb_per_vcpu:
        character, char_label = "compute", "Thiên về tính toán (compute-intensive)"
    elif ratio <= th.memory_min_gb_per_vcpu:
        character, char_label = "balanced", "Cân đối giữa CPU và RAM (general purpose)"
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


def _scores(rows: List[Dict[str, Any]], th: Optional[AdvisoryThresholds] = None) -> None:
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
            comfort = min(99.0, max(0.0, (th or AdvisoryThresholds()).failover_comfort_pct))
            resil = _clamp(100 - max(0.0, worst_fail - comfort) * (70.0 / max(1.0, 100.0 - comfort)), 30, 100)
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
    """Findings written in the voice of the SI consultant addressing the customer
    ("chúng tôi" khuyến nghị, "Quý khách" quyết định) — plain language, terms explained."""
    out: List[Dict[str, str]] = []
    if not best or not demand["vmCount"]:
        return out
    th = thresholds_of(params)
    a = best["advisory"]
    prof = best["profile"]
    f = best["failure"]
    red = best["redundancy"]

    # Alternative that is noticeably cheaper.
    best_tco = a["costs"]["tco"]
    for r in results or []:
        if r is best or not r["hostsTotal"]:
            continue
        tco = r["advisory"]["costs"]["tco"]
        if best_tco and tco and tco < best_tco * (1 - th.cheaper_alt_pct / 100.0) and r["failure"]["ok"]:
            out.append(_finding("info", "Chi phí", f"Có phương án tiết kiệm hơn {(1 - tco / best_tco) * 100:.0f}% tổng chi phí",
                                f"Cấu hình {r['profile']['name']} cần {r['hostsTotal']} máy chủ (thay vì {best['hostsTotal']}) nhưng tổng chi phí sở hữu thấp hơn và vẫn đảm bảo dự phòng N+{r['redundancy']}.",
                                "Nếu Quý khách ưu tiên ngân sách hơn số lượng máy chủ / không gian tủ rack, chúng tôi sẽ điều chỉnh đề xuất theo phương án này."))
            break

    # Resilience
    if red == 0:
        out.append(_finding("critical", "Chịu lỗi", "Hệ thống chưa có máy chủ dự phòng",
                            "Khi một máy chủ gặp sự cố hoặc cần bảo trì, hệ thống sẽ không đủ tài nguyên để chạy toàn bộ ứng dụng.",
                            "Chúng tôi khuyến nghị bổ sung tối thiểu 1 máy chủ dự phòng (N+1) cho môi trường Production."))
    elif not f["ok"]:
        out.append(_finding("critical", "Chịu lỗi", f"Chưa đảm bảo dự phòng N+{red}",
                            f"Nếu {red} máy chủ ngừng hoạt động, các máy còn lại phải gánh {f['cpuPct'] or 0:.0f}% CPU và {f['ramPct'] or 0:.0f}% RAM — vượt mức an toàn.",
                            "Chúng tôi sẽ bổ sung máy chủ hoặc điều chỉnh mức chia sẻ tài nguyên (overcommit) để đảm bảo dự phòng."))
    else:
        out.append(_finding("ok", "Chịu lỗi", f"Đảm bảo dự phòng N+{red}",
                            f"Khi {red} máy chủ ngừng hoạt động, hệ thống vẫn chạy ổn định ở mức {f['cpuPct'] or 0:.0f}% CPU và {f['ramPct'] or 0:.0f}% RAM.",
                            f"Chúng tôi sẽ cấu hình vSphere HA (tự khởi động lại VM khi máy chủ lỗi) dành sẵn năng lực cho N+{red}, cùng DRS để tự cân bằng tải."))
    if best["hostsTotal"] == 2 and red >= 1:
        out.append(_finding("info", "Chịu lỗi", "Cụm 2 máy chủ: một nửa năng lực dành cho dự phòng",
                            "Trong lúc một máy chủ bảo trì, hệ thống tạm thời không còn dự phòng.",
                            "Quý khách có thể cân nhắc 3 máy chủ cấu hình nhỏ hơn để bảo trì an toàn và giảm phần năng lực 'nằm chờ'."))

    # Fit / NUMA
    numa = a["numa"]
    if numa["noFit"]:
        out.append(_finding("critical", "Cấu hình VM", "Có máy ảo lớn hơn khả năng của một máy chủ",
                            f"{', '.join(numa['noFit'][:4])} cần nhiều CPU/RAM hơn một máy chủ {prof['name']} có thể cung cấp.",
                            "Chúng tôi sẽ đề xuất máy chủ cấu hình lớn hơn, hoặc cùng đội ứng dụng tách máy ảo này thành nhiều máy nhỏ."))
    if numa["wideCpu"]:
        out.append(_finding("warning", "NUMA", f"{len(numa['wideCpu'])} nhóm máy ảo lớn hơn một bộ xử lý (CPU socket)",
                            f"{', '.join(numa['wideCpu'][:4])} có số vCPU lớn hơn {prof['coresPerSocket']} core của một CPU, nên phải dùng bộ nhớ của cả hai CPU — có thể giảm hiệu năng.",
                            "Chúng tôi sẽ cấu hình vNUMA đúng kiến trúc phần cứng; nếu máy ảo nhạy hiệu năng, nên chọn CPU nhiều core hơn."))
    if numa["wideRam"]:
        out.append(_finding("warning", "NUMA", f"{len(numa['wideRam'])} nhóm máy ảo có RAM lớn hơn bộ nhớ gắn với một CPU",
                            f"{', '.join(numa['wideRam'][:4])} cần hơn {numa['ramPerSocketGB']:.0f} GB RAM — phần vượt sẽ truy cập chậm hơn.",
                            "Chúng tôi khuyến nghị tăng RAM mỗi máy chủ, hoặc rà soát lại RAM cấp cho máy ảo nếu thực tế dùng ít hơn."))

    # Balance
    bal = a["balance"]
    loaded = max(bal["cpuUtilPct"], bal["ramUtilPct"]) >= th.balance_min_load_pct
    if not loaded:
        pass
    elif bal["strandedCpuPct"] >= th.stranded_warn_pct and bal["idealRamPerHostGB"]:
        out.append(_finding("warning", "Cân bằng tài nguyên", f"RAM hết trước, còn dư khoảng {bal['strandedCpuPct']:.0f}% CPU",
                            f"Ứng dụng của Quý khách dùng nhiều RAM ({workload['ramPerVcpu']:.1f} GB cho mỗi vCPU). Với {prof['cores']} core mỗi máy chủ, mức RAM cân đối là khoảng {_gb_txt(bal['idealRamPerHostGB'])} (cấu hình hiện tại {_gb_txt(prof['ramGB'])}).",
                            "Chúng tôi khuyến nghị nâng RAM mỗi máy chủ — thường giúp giảm số máy chủ và chi phí license."))
    elif bal["strandedRamPct"] >= th.stranded_warn_pct and bal["idealCoresPerHost"]:
        out.append(_finding("warning", "Cân bằng tài nguyên", f"CPU hết trước, còn dư khoảng {bal['strandedRamPct']:.0f}% RAM",
                            f"Với {_gb_txt(prof['ramGB'])} RAM mỗi máy chủ, số core cân đối là khoảng {bal['idealCoresPerHost']:.0f} core (cấu hình hiện tại {prof['cores']}).",
                            "Chúng tôi khuyến nghị chọn CPU nhiều core hơn hoặc giảm RAM mỗi máy chủ để tối ưu chi phí."))
    elif bal["gapPct"] < th.balance_ok_gap_pct:
        out.append(_finding("ok", "Cân bằng tài nguyên", "CPU và RAM được sử dụng cân đối",
                            f"Mức dùng CPU {bal['cpuUtilPct']:.0f}% và RAM {bal['ramUtilPct']:.0f}% — tài nguyên được khai thác hiệu quả, ít lãng phí.",
                            "Giữ tỷ lệ cấu hình này."))

    # Overcommit policy
    if params.ram_ratio > th.ram_ratio_warn:
        out.append(_finding("warning", "Overcommit", f"Cấp phát RAM vượt dung lượng thật ({params.ram_ratio:g}x)",
                            "Khi tải cao, hệ thống phải thu hồi hoặc đẩy bộ nhớ xuống đĩa, làm chậm toàn bộ máy ảo.",
                            f"Chúng tôi khuyến nghị giữ tỷ lệ RAM ≤ {th.ram_ratio_warn:g}x cho Production; chỉ áp dụng mức cao hơn cho Dev/Test."))
    if workload["dbLike"] and params.cpu_ratio > th.db_cpu_ratio_max:
        out.append(_finding("warning", "Overcommit", "Cơ sở dữ liệu / middleware dùng chung tài nguyên CPU",
                            f"{', '.join(workload['dbLike'][:4])} là các hệ thống nhạy về độ trễ nhưng đang dùng chung CPU ở tỷ lệ {params.cpu_ratio:g} vCPU / luồng xử lý.",
                            f"Chúng tôi sẽ dành riêng CPU (reservation) hoặc nhóm các máy ảo này trên máy chủ riêng với tỷ lệ ≤ {th.db_cpu_ratio_max:g}x."))
    elif params.cpu_ratio >= th.high_cpu_ratio_warn:
        out.append(_finding("warning", "Overcommit", f"Tỷ lệ chia sẻ CPU cao ({params.cpu_ratio:g}x)",
                            "Phù hợp môi trường Dev/Test; với Production, máy ảo có thể phải chờ CPU khi tải cao.",
                            "Chúng tôi khuyến nghị 2–4x cho Production và sẽ theo dõi chỉ số CPU Ready (thời gian chờ CPU) dưới 5%."))

    # Growth
    run = a["runway"]["extraGrowthPct"]
    if run is not None and run < th.low_runway_pct:
        out.append(_finding("warning", "Tăng trưởng", "Dư địa mở rộng còn ít",
                            f"Ngoài phần +{params.growth_pct:g}% đã dự phòng, hệ thống chỉ tiếp nhận thêm khoảng {max(0, run):.0f}% tải trước khi mất khả năng dự phòng.",
                            "Nếu ứng dụng có kế hoạch mở rộng, chúng tôi khuyến nghị bổ sung 1 máy chủ hoặc chọn cấu hình lớn hơn ngay từ đầu."))
    if proj and proj.get("expandYear") is not None:
        out.append(_finding("info", "Tăng trưởng", f"Cần mở rộng vào năm thứ {proj['expandYear']}",
                            f"Với mức tăng trưởng {proj['annualGrowthPct']:g}%/năm, nhu cầu sẽ vượt năng lực {proj['installed']} máy chủ sau {proj['expandYear']} năm.",
                            "Chúng tôi sẽ đồng hành cùng Quý khách lập kế hoạch mở rộng và đưa vào ngân sách năm tương ứng."))
    if params.growth_pct <= 0:
        out.append(_finding("info", "Tăng trưởng", "Chưa tính dự phòng tăng trưởng",
                            "Thực tế, nhu cầu tài nguyên thường tăng 15–30% trong năm đầu vận hành.",
                            "Chúng tôi khuyến nghị dự phòng tăng trưởng 15–25%."))

    # Licensing
    lic = a["license"]
    if lic["paddedCores"] > 0:
        out.append(_finding("info", "License", f"{lic['paddedCores']} core license không được khai thác",
                            f"License VMware tính tối thiểu {params.license_min_cores_per_socket} core cho mỗi CPU, trong khi cấu hình chỉ có {prof['coresPerSocket']} core/CPU.",
                            f"Chúng tôi khuyến nghị chọn CPU từ {params.license_min_cores_per_socket} core trở lên để tận dụng trọn license."))

    # Scale / cluster size
    if best["hostsTotal"] > th.cluster_warn_hosts:
        out.append(_finding("warning", "Kiến trúc", f"Cụm máy chủ quá lớn ({best['hostsTotal']} máy chủ)",
                            "Cụm quá lớn khó vận hành, nâng cấp và khoanh vùng khi có sự cố.",
                            f"Chúng tôi sẽ thiết kế thành nhiều cụm, mỗi cụm {th.cluster_info_hosts}–{th.cluster_warn_hosts} máy chủ, hoặc dùng máy chủ cấu hình lớn hơn."))
    elif best["hostsTotal"] > th.cluster_info_hosts:
        out.append(_finding("info", "Kiến trúc", f"{best['hostsTotal']} máy chủ — nên chia thành nhiều cụm",
                            "Vẫn trong giới hạn của vSphere nhưng nên tách theo tầng ứng dụng để dễ vận hành và hạn chế ảnh hưởng khi có sự cố.",
                            "Chúng tôi sẽ thiết kế từ 2 cụm trở lên, mỗi cụm có dự phòng N+1 riêng."))

    # Storage
    st = best["storage"]
    if params.storage_mode == "local":
        if st.get("ok") is False:
            out.append(_finding("critical", "Lưu trữ", "Dung lượng ổ đĩa trên máy chủ chưa đủ",
                                f"Mỗi máy chủ cần khoảng {_gb_txt(st.get('perHostNeededGB') or 0)} nhưng cấu hình hiện có {_gb_txt(prof['diskGB'] or 0)}.",
                                "Chúng tôi sẽ bổ sung số lượng / dung lượng ổ đĩa mỗi máy chủ, hoặc đề xuất phương án lưu trữ tập trung (SAN)."))
        min_vsan = 4 if (params.storage_protection_factor or 1) < 1.9 and (params.storage_protection_factor or 1) > 1.0 else 3
        if best["hostsTotal"] < min_vsan:
            out.append(_finding("warning", "Lưu trữ", f"vSAN cần tối thiểu {min_vsan} máy chủ",
                                f"Mức bảo vệ dữ liệu ×{params.storage_protection_factor:g} yêu cầu từ {min_vsan} máy chủ trở lên.",
                                f"Chúng tôi sẽ điều chỉnh cụm lên {min_vsan} máy chủ."))
    else:
        out.append(_finding("info", "Lưu trữ", f"Cần {_gb_txt(demand['storageNeededGB'])} dung lượng trên hệ thống lưu trữ (SAN)",
                            f"Đã bao gồm +{params.growth_pct:g}% tăng trưởng và +{params.storage_overhead_pct:g}% cho snapshot, swap và vùng trống an toàn.",
                            "Chúng tôi sẽ cùng Quý khách xác nhận dung lượng trống, hiệu năng (IOPS) và chính sách sao lưu / nhân bản dữ liệu."))

    out.sort(key=lambda x: SEVERITY_ORDER.get(x["severity"], 9))
    return out


def next_steps(demand, params, best, n_findings_crit: int) -> List[Dict[str, str]]:
    """Engagement roadmap from the SI's side: what we do, what the customer decides."""
    if not best:
        return []
    p = best["profile"]
    steps = [
        {"phase": "1. Thống nhất yêu cầu", "when": "Tuần 1",
         "what": f"Chúng tôi cùng Quý khách rà soát danh sách {demand['vmCount']} máy ảo và các giả định (tỷ lệ chia sẻ CPU {params.cpu_ratio:g}x, tăng trưởng {params.growth_pct:g}%, dự phòng N+{params.redundancy})."},
        {"phase": "2. Chốt cấu hình & báo giá", "when": "Tuần 2",
         "what": f"Chúng tôi hoàn thiện cấu hình {best['hostsTotal']} máy chủ {p['name']} và gửi báo giá trọn gói gồm phần cứng, license, bảo hành và dịch vụ triển khai, kèm TCO {params.horizon_years} năm."},
        {"phase": "3. Phê duyệt", "when": "Tuần 3–4",
         "what": "Quý khách phê duyệt phương án và ngân sách; chúng tôi hỗ trợ hồ sơ thuyết minh kỹ thuật (báo cáo này) cho việc trình duyệt."},
        {"phase": "4. Triển khai", "when": "Sau khi bàn giao thiết bị",
         "what": "Chúng tôi lắp đặt và cấu hình cụm máy chủ: vSphere HA, DRS, EVC, nhóm máy chủ riêng cho cơ sở dữ liệu, chuẩn hoá mẫu máy ảo."},
        {"phase": "5. Kiểm thử & nghiệm thu", "when": "Trước khi vận hành chính thức",
         "what": f"Kiểm thử chịu lỗi: tắt {max(1, best['redundancy'])} máy chủ, xác nhận máy ảo tự khởi động lại và hiệu năng đạt yêu cầu; nghiệm thu cùng Quý khách."},
        {"phase": "6. Đồng hành vận hành", "when": "30 / 60 / 90 ngày",
         "what": "Chúng tôi theo dõi hiệu năng, tối ưu máy ảo cấp thừa và đánh giá lại năng lực sau 6 tháng để chủ động kế hoạch mở rộng."},
    ]
    if n_findings_crit:
        steps.insert(1, {"phase": "0. Xử lý rủi ro", "when": "Trước khi chốt cấu hình",
                         "what": f"Chúng tôi điều chỉnh thiết kế để xử lý {n_findings_crit} rủi ro nghiêm trọng nêu tại mục Rủi ro & khuyến nghị."})
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
    _scores(results, thresholds_of(params))


def build_advisory(vms, params, profiles, demand: Dict[str, Any], results: List[Dict[str, Any]],
                   best_idx: Optional[int]) -> Dict[str, Any]:
    if results and "advisory" not in results[0]:
        annotate_results(results, vms, params, demand)

    best = results[best_idx] if best_idx is not None else None
    th = thresholds_of(params)
    wl = workload_profile(vms, demand, th)
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
        "thresholds": th.__dict__.copy(),
    }
