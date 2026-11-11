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
    ("S", "S ≤2 vCPU/8 GB", 2, 8),
    ("M", "M ≤4 vCPU/16 GB", 4, 16),
    ("L", "L ≤8 vCPU/64 GB", 8, 64),
    ("XL", "XL ≤16 vCPU/128 GB", 16, 128),
    ("XXL", "XXL lớn hơn", math.inf, math.inf),
]

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}

DEFAULT_DB_KEYWORDS = ("db, database, sql, mssql, mysql, postgres, postgresql, oracle, mongo, mongodb, redis, kafka, elastic, "
                       "elasticsearch, sap, hana, cassandra, pi data archive, pi archive, data archive, piarchive, historian, osisoft, aveva")
DEFAULT_DC_KEYWORDS = "dc, domain controller, active directory, ad ds, addc"
DEFAULT_BACKUP_KEYWORDS = "backup, veeam, commvault, netbackup, veritas, avamar, networker, rubrik, cohesity"


@dataclass
class AdvisoryThresholds:
    """Tunable thresholds behind the workload classification, findings and
    scorecard. Defaults are common virtualisation practice; a partner / customer can
    override any of them per request (``params.advisory``) without code changes."""
    compute_max_gb_per_vcpu: float = 3.0     # ≤ → compute-intensive
    memory_min_gb_per_vcpu: float = 6.0      # > → memory-intensive
    stranded_warn_pct: float = 35.0          # idle share of the non-binding resource that triggers a warning
    balance_ok_gap_pct: float = 15.0         # |CPU% − RAM%| below this = balanced
    balance_min_load_pct: float = 40.0       # balance findings only when the binding resource is at least this loaded
    db_cpu_per_core_max: float = 4.0         # max vCPU per physical core for DB / latency-sensitive VMs
    high_cpu_per_core_warn: float = 6.0      # vCPU per physical core considered high for Production
    ram_ratio_warn: float = 1.0              # RAM overcommit above this triggers a warning
    low_runway_pct: float = 10.0             # extra growth headroom below this triggers a warning
    failover_comfort_pct: float = 60.0       # load after host loss up to this scores 100 on resilience
    cheaper_alt_pct: float = 3.0             # flag an alternative at least this % cheaper (TCO)
    cluster_info_hosts: int = 32             # suggest splitting the cluster above this
    cluster_warn_hosts: int = 64             # warn above this
    failover_margin_pct: float = 2.0         # load after host loss within this many points of the max-util limit = "at the limit"
    vsan_free_min_pct: float = 25.0          # recommended free space (storage overhead) for HCI storage (vSAN / AOS / S2D / Ceph)
    db_keywords: str = DEFAULT_DB_KEYWORDS   # comma-separated name/note keywords marking DB-like VMs
    dc_keywords: str = DEFAULT_DC_KEYWORDS   # keywords marking domain controllers (anti-affinity)
    backup_keywords: str = DEFAULT_BACKUP_KEYWORDS  # keywords marking backup servers

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
    ("stranded_warn_pct", "Cảnh báo tài nguyên dư khi ≥", "%"),
    ("balance_ok_gap_pct", "Coi là cân đối khi CPU và RAM chênh <", "%"),
    ("balance_min_load_pct", "Chỉ xét cân đối khi tải yếu tố quyết định ≥", "%"),
    ("db_cpu_per_core_max", "Tỷ lệ chia sẻ CPU tối đa cho CSDL (vCPU/core)", "vCPU"),
    ("high_cpu_per_core_warn", "Cảnh báo tỷ lệ chia sẻ CPU > (vCPU/core)", "vCPU"),
    ("ram_ratio_warn", "Cảnh báo tỷ lệ chia sẻ RAM >", "x"),
    ("low_runway_pct", "Cảnh báo dư địa tăng trưởng <", "%"),
    ("failover_comfort_pct", "Tải khi mất host được điểm tối đa ≤", "%"),
    ("failover_margin_pct", "Coi là sát ngưỡng khi tải lúc mất host cách mức tối đa ≤", "điểm %"),
    ("vsan_free_min_pct", "Dung lượng trống khuyến nghị cho lưu trữ HCI ≥", "%"),
    ("cheaper_alt_pct", "Nêu phương án rẻ hơn khi TCO thấp hơn ≥", "%"),
    ("cluster_info_hosts", "Gợi ý chia cụm khi số host >", "host"),
    ("cluster_warn_hosts", "Cảnh báo cụm quá lớn khi số host >", "host"),
    ("db_keywords", "Từ khoá nhận diện VM CSDL / middleware", ""),
    ("dc_keywords", "Từ khoá nhận diện Domain Controller", ""),
    ("backup_keywords", "Từ khoá nhận diện máy chủ backup", ""),
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
    """GB (as entered, = GiB in the hypervisor) → TiB above 1 024 GB; one unit convention for the whole report."""
    if v >= 1024:
        return f"{v / 1024:,.1f} TiB"
    return f"{v:,.0f} GB"


def expand_text(proj: Optional[Dict[str, Any]]) -> Optional[str]:
    """Single wording for "when is expansion needed", used by findings, report and Excel."""
    if not proj:
        return None
    y = proj.get("expandYear")
    if y is None:
        return f"Đủ năng lực trong cả {proj['years']} năm dự báo (tăng trưởng {proj['annualGrowthPct']:g}%/năm)."
    if y <= 1:
        return f"Với tăng trưởng {proj['annualGrowthPct']:g}%/năm, cần mở rộng ngay trong năm thứ 1."
    return (f"Với tăng trưởng {proj['annualGrowthPct']:g}%/năm, đủ năng lực đến hết năm thứ {y - 1}; "
            f"cần mở rộng trước năm thứ {y}.")


# ---------------------------------------------------------------------------
# Workload profile
# ---------------------------------------------------------------------------
def workload_profile(vms, demand: Dict[str, Any], th: Optional[AdvisoryThresholds] = None) -> Dict[str, Any]:
    th = th or AdvisoryThresholds()
    db_re = _db_regex(th.db_keywords)
    dc_re = _db_regex(th.dc_keywords)
    bk_re = _db_regex(th.backup_keywords)
    dcs, backups, pairs = [], [], []
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
        text = f"{v.name} {v.note}"
        if bk_re and bk_re.search(text):
            backups.append(v.name)
        elif dc_re and dc_re.search(text):
            dcs.append((v.name, q))
        elif db_re and db_re.search(text):
            db_like.append(v.name)
        if q >= 2 and not (bk_re and bk_re.search(text)):
            pairs.append(v.name)

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
        "domainControllers": [n for n, _ in dcs],
        "dcCount": sum(q for _, q in dcs),
        "backupVms": backups,
        "multiInstance": pairs,
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
        {"label": f"Vùng an toàn {100 - params.max_util_pct:g}%", "value": demand["vcpuPlanned"] / util},
    ]
    ram_steps = [
        {"label": "Yêu cầu", "value": demand["ramGB"]},
        {"label": f"+{params.growth_pct:g}% tăng trưởng", "value": demand["ramPlannedGB"]},
        {"label": f"Vùng an toàn {100 - params.max_util_pct:g}%", "value": demand["ramPlannedGB"] / util},
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
    """Licence units per platform: cores (with per-CPU / per-host minimums) or CPU sockets.
    `coresTotal` keeps its name for compatibility and holds the licensed *units*."""
    from . import platforms
    pf = platforms.get(params)
    p = r["profile"]
    if pf["license_unit"] == "socket":
        per_host = p["sockets"]
        padded = 0
        unit = "socket"
    else:
        min_cps = max(0, int(params.license_min_cores_per_socket or 0))
        per_host = max(p["sockets"] * max(min_cps, p["coresPerSocket"]), int(pf.get("license_min_per_host") or 0))
        padded = per_host - p["cores"]
        unit = "core"
    total = per_host * r["hostsTotal"]
    yearly = (params.license_price_per_core * total) if params.license_price_per_core is not None else None
    return {"coresPerHost": per_host, "coresTotal": total, "paddedCores": max(0, padded * r["hostsTotal"]), "unit": unit,
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


def _scores(rows: List[Dict[str, Any]], th: Optional[AdvisoryThresholds] = None,
            ref_rows: Optional[List[Dict[str, Any]]] = None) -> None:
    """0–100 per axis, higher = better; relative axes compare against the best of `ref_rows` (default: rows)."""
    valid = [r for r in (ref_rows or rows) if r["hostsTotal"]]
    if not valid:
        return
    min_hosts = min(r["hostsTotal"] for r in valid)
    valid_all = valid + [r for r in rows if r["hostsTotal"] and r not in valid]
    tcos = [r["advisory"]["costs"]["tco"] for r in valid if r["advisory"]["costs"]["tco"]]
    min_tco = min(tcos) if tcos else None
    min_lic = min(r["advisory"]["license"]["coresTotal"] for r in valid) or 1
    min_hosts = min(min_hosts, min(r["hostsTotal"] for r in valid_all))
    min_lic = min(min_lic, min(r["advisory"]["license"]["coresTotal"] for r in valid_all) or 1)
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
    ("compact", "Số host"), ("cost", "Chi phí"), ("efficiency", "Hiệu suất"), ("balance", "Cân đối"),
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
    """Risks & recommendations, plain language, one vocabulary: "host", "tỷ lệ chia sẻ",
    "vùng an toàn", "yếu tố quyết định". Few pronouns — state the fact and the action."""
    out: List[Dict[str, str]] = []
    if not best or not demand["vmCount"]:
        return out
    th = thresholds_of(params)
    from . import platforms
    pf = platforms.get(params)
    a = best["advisory"]
    prof = best["profile"]
    f = best["failure"]
    red = best["redundancy"]
    ht = max(1.0, params.ht_factor)
    per_core = params.cpu_ratio * ht
    results = results or []

    # Cheaper alternative among the candidates.
    best_tco = a["costs"]["tco"]
    for r in results:
        if r is best or not r["hostsTotal"] or r.get("suggested"):
            continue
        tco = r["advisory"]["costs"]["tco"]
        if best_tco and tco and tco < best_tco * (1 - th.cheaper_alt_pct / 100.0) and r["failure"]["ok"]:
            out.append(_finding("info", "Chi phí", f"Phương án {r['profile']['name']} có TCO thấp hơn {(1 - tco / best_tco) * 100:.0f}%",
                                f"Cần {r['hostsTotal']} host (thay vì {best['hostsTotal']}), vẫn đạt dự phòng N+{r['redundancy']}.",
                                "Chọn phương án này nếu ưu tiên ngân sách hơn số host và không gian tủ rack."))
            break

    # Suggested variants (fewer cores / more RAM) generated by the engine.
    for r in results:
        if not r.get("suggested") or not r["hostsTotal"]:
            continue
        why = r.get("suggestedWhy", "")
        lic_b, lic_r = a["license"]["coresTotal"], r["advisory"]["license"]["coresTotal"]
        vpc = demand["vcpuPlanned"] / max(1, r["totals"]["cores"])
        if why == "fewer_cores":
            out.append(_finding("info", "Chi phí", f"Phương án tham khảo {r['profile']['name']}: giảm license từ {lic_b} xuống {lic_r} {a['license'].get('unit', 'core')}",
                                f"CPU đang dư nhiều; với {r['profile']['coresPerSocket']} core/CPU vẫn đủ ({vpc:.1f} vCPU : 1 core), "
                                f"cần {r['hostsTotal']} host, khi mất {r['redundancy']} host dùng {r['failure']['cpuPct'] or 0:.0f}% CPU · {r['failure']['ramPct'] or 0:.0f}% RAM.",
                                "Cân nhắc khi chi phí license theo core là ưu tiên; kiểm tra lại với yêu cầu hiệu năng của ứng dụng."))
        elif why == "more_ram":
            out.append(_finding("info", "Dư địa", f"Phương án tham khảo {r['profile']['name']}: thêm RAM để có dư địa",
                                f"Cùng {r['hostsTotal']} host; khi mất {r['redundancy']} host RAM còn {r['failure']['ramPct'] or 0:.0f}%, "
                                f"chứa thêm khoảng {r['spare']['extraAvgVms'] or 0} VM.",
                                "Chi phí tăng chủ yếu ở phần RAM; nên chọn nếu dự kiến mở rộng ứng dụng."))

    # Resilience
    util_pct = params.max_util_pct
    worst = max(f["cpuPct"] or 0, f["ramPct"] or 0)
    if red == 0:
        out.append(_finding("critical", "Chịu lỗi", "Không có host dự phòng (N)",
                            "Khi một host hỏng hoặc bảo trì, cụm không đủ tài nguyên để chạy toàn bộ VM.",
                            "Bổ sung tối thiểu 1 host dự phòng (N+1) cho môi trường Production."))
    elif not f["ok"]:
        out.append(_finding("critical", "Chịu lỗi", f"Chưa đạt dự phòng N+{red}",
                            f"Khi mất {red} host, các host còn lại phải gánh {f['cpuPct'] or 0:.0f}% CPU và {f['ramPct'] or 0:.0f}% RAM.",
                            "Thêm host hoặc giảm tỷ lệ chia sẻ tài nguyên."))
    elif f.get("atLimit"):
        out.append(_finding("warning", "Chịu lỗi", f"Đạt N+{red} nhưng sát ngưỡng",
                            f"Khi mất {red} host, {'RAM' if (f['ramPct'] or 0) >= (f['cpuPct'] or 0) else 'CPU'} dùng {worst:.0f}% — "
                            f"đúng mức tối đa cho phép ({util_pct:g}%). Chỉ cần nhu cầu tăng nhẹ là phải thêm host.",
                            "Chọn cấu hình có thêm RAM/CPU mỗi host, hoặc thêm 1 host."))
    else:
        out.append(_finding("ok", "Chịu lỗi", f"Đạt dự phòng N+{red}",
                            f"Khi mất {red} host, cụm vẫn chạy ở mức {f['cpuPct'] or 0:.0f}% CPU và {f['ramPct'] or 0:.0f}% RAM.",
                            f"Cấu hình {pf['ha']} (tự khởi động lại VM khi host lỗi) dành sẵn năng lực N+{red}; bật {pf['balancer']} để cân bằng tải."))
    if best["hostsTotal"] == 2 and red >= 1:
        out.append(_finding("info", "Chịu lỗi", "Cụm 2 host: một nửa năng lực dành cho dự phòng",
                            "Trong lúc một host bảo trì, cụm không còn dự phòng.",
                            "Cân nhắc 3 host cấu hình nhỏ hơn để bảo trì an toàn."))

    # Platform services that run on every host (Nutanix CVM, Ceph OSDs on Proxmox).
    needs_overhead = pf.get("overhead_min_ram_gb") and (params.platform == "nutanix" or params.storage_mode == "local")
    if needs_overhead and params.reserve_ram_gb < pf["overhead_min_ram_gb"]:
        out.append(_finding("warning", "Nền tảng", f"Phần tài nguyên dành cho {pf['hypervisor']} có thể thấp",
                            f"{pf['overhead_note']} Giả định hiện tại chỉ trừ {params.reserve_ram_gb:g} GB RAM / host.",
                            f"Tăng mục \"Tài nguyên dành cho hypervisor\" lên khoảng {pf['reserve']['ram_gb']} GB RAM và {pf['reserve']['cores']} core / host rồi tính lại."))

    # Placement rules (anti-affinity) for DCs and multi-instance groups
    if workload.get("dcCount", 0) >= 2:
        out.append(_finding("warning", "Bố trí VM", f"{workload['dcCount']} Domain Controller cần nằm trên host khác nhau",
                            f"{', '.join(workload['domainControllers'][:3])}: nếu cùng nằm trên một host, sự cố host làm mất toàn bộ dịch vụ xác thực.",
                            f"Tạo {pf['anti_affinity']} cho các Domain Controller."))
    multi = [n for n in workload.get("multiInstance", []) if n not in workload.get("domainControllers", [])]
    if multi:
        out.append(_finding("info", "Bố trí VM", "Nhóm VM chạy nhiều bản nên được tách host",
                            f"{', '.join(multi[:5])}{'…' if len(multi) > 5 else ''} có từ 2 VM trở lên cùng vai trò.",
                            "Đặt quy tắc tách host (anti-affinity) cho các cặp/cụm ứng dụng cần sẵn sàng cao."))
    if workload.get("backupVms"):
        out.append(_finding("warning", "Sao lưu", "Máy chủ backup chạy chung cụm với dữ liệu cần backup",
                            f"{', '.join(workload['backupVms'][:3])}: khi cụm hoặc hệ thống lưu trữ gặp sự cố, mất cả dữ liệu lẫn bản sao lưu.",
                            "Lưu bản sao ra thiết bị hoặc vị trí khác cụm (nguyên tắc 3-2-1); cân nhắc đặt máy chủ backup ngoài cụm."))

    # Fit / NUMA
    numa = a["numa"]
    if numa["noFit"]:
        out.append(_finding("critical", "Cấu hình VM", "Có VM lớn hơn năng lực một host",
                            f"{', '.join(numa['noFit'][:4])} cần nhiều CPU/RAM hơn một host {prof['name']} cung cấp được.",
                            "Chọn host lớn hơn hoặc tách VM thành nhiều VM nhỏ."))
    if numa["wideCpu"]:
        out.append(_finding("warning", "NUMA", f"{len(numa['wideCpu'])} nhóm VM có vCPU lớn hơn số core của một CPU",
                            f"{', '.join(numa['wideCpu'][:4])} vượt {prof['coresPerSocket']} core/CPU nên phải dùng bộ nhớ của cả hai CPU — có thể giảm hiệu năng.",
                            "Cấu hình vNUMA đúng kiến trúc phần cứng; nếu VM nhạy hiệu năng, chọn CPU nhiều core hơn."))
    if numa["wideRam"]:
        out.append(_finding("warning", "NUMA", f"{len(numa['wideRam'])} nhóm VM có RAM lớn hơn bộ nhớ của một CPU",
                            f"{', '.join(numa['wideRam'][:4])} cần hơn {numa['ramPerSocketGB']:.0f} GB — phần vượt truy cập chậm hơn.",
                            "Tăng RAM mỗi host, hoặc rà soát lại RAM cấp cho VM."))

    # Balance — explain the real cause instead of an "ideal RAM" number.
    bal = a["balance"]
    loaded = max(bal["cpuUtilPct"], bal["ramUtilPct"]) >= th.balance_min_load_pct
    if loaded and bal["strandedCpuPct"] >= th.stranded_warn_pct:
        cause = ("workload thiên RAM" if workload["character"] == "memory"
                 else f"tỷ lệ chia sẻ CPU đặt cao ({params.cpu_ratio:g} vCPU/thread ≈ {per_core:g} vCPU/core) nên CPU luôn dư")
        out.append(_finding("warning", "Cân đối tài nguyên", f"RAM là yếu tố quyết định, CPU dư khoảng {bal['strandedCpuPct']:.0f}%",
                            f"Nguyên nhân: {cause}. Thực tế chỉ cần {demand['vcpuPlanned'] / max(1, best['totals']['cores']):.2f} vCPU trên mỗi core vật lý.",
                            "Có thể chọn CPU ít core hơn để giảm chi phí license (license tính theo core)."))
    elif loaded and bal["strandedRamPct"] >= th.stranded_warn_pct:
        out.append(_finding("warning", "Cân đối tài nguyên", f"CPU là yếu tố quyết định, RAM dư khoảng {bal['strandedRamPct']:.0f}%",
                            f"Mỗi host có {_gb_txt(prof['ramGB'])} RAM nhưng CPU hết trước.",
                            "Chọn CPU nhiều core hơn, hoặc giảm RAM mỗi host để tối ưu chi phí."))
    elif loaded and bal["gapPct"] < th.balance_ok_gap_pct:
        out.append(_finding("ok", "Cân đối tài nguyên", "CPU và RAM được dùng cân đối",
                            f"Mức dùng CPU {bal['cpuUtilPct']:.0f}% và RAM {bal['ramUtilPct']:.0f}% — ít tài nguyên bỏ trống.",
                            "Giữ tỷ lệ cấu hình này."))

    # Sharing ratios
    if params.ram_ratio > th.ram_ratio_warn:
        out.append(_finding("warning", "Tỷ lệ chia sẻ", f"Cấp RAM vượt dung lượng thật ({params.ram_ratio:g} : 1)",
                            "Khi tải cao, hệ thống phải thu hồi bộ nhớ hoặc đẩy xuống đĩa, làm chậm toàn bộ VM.",
                            f"Giữ tỷ lệ RAM ≤ {th.ram_ratio_warn:g} : 1 cho Production."))
    if workload["dbLike"] and per_core > th.db_cpu_per_core_max:
        out.append(_finding("warning", "Tỷ lệ chia sẻ", "Cơ sở dữ liệu dùng chung CPU ở tỷ lệ cao",
                            f"{', '.join(workload['dbLike'][:4])} nhạy độ trễ nhưng áp chung tỷ lệ {per_core:g} vCPU/core.",
                            f"Đặt CPU reservation hoặc nhóm host riêng với tỷ lệ ≤ {th.db_cpu_per_core_max:g} vCPU/core cho các VM này."))
    if per_core > th.high_cpu_per_core_warn:
        out.append(_finding("warning", "Tỷ lệ chia sẻ", f"Tỷ lệ chia sẻ CPU giả định cao ({per_core:g} vCPU/core)",
                            f"Giả định {params.cpu_ratio:g} vCPU trên mỗi thread, tức {per_core:g} vCPU trên mỗi core vật lý — cao hơn thông lệ 2–4 vCPU/core cho Production.",
                            "Dùng giả định thấp hơn khi sizing; theo dõi CPU Ready (thời gian chờ CPU) dưới 5% khi vận hành."))

    # Growth
    run = a["runway"]["extraGrowthPct"]
    if run is not None and run < th.low_runway_pct:
        out.append(_finding("warning", "Tăng trưởng", "Không còn dư địa tăng trưởng" if run < 1 else "Dư địa tăng trưởng thấp",
                            (f"Ngoài +{params.growth_pct:g}% đã dự phòng, cụm không nhận thêm được tải nào mà vẫn giữ N+{red}."
                             if run < 1 else f"Ngoài +{params.growth_pct:g}% đã dự phòng, cụm chỉ nhận thêm khoảng {run:.0f}% tải mà vẫn giữ N+{red}."),
                            "Thêm RAM/CPU mỗi host hoặc thêm 1 host nếu ứng dụng có kế hoạch mở rộng."))
    et = expand_text(proj)
    if proj and proj.get("expandYear") is not None:
        out.append(_finding("info", "Tăng trưởng", f"Cần mở rộng trước năm thứ {proj['expandYear']}", et,
                            "Đưa hạng mục mở rộng vào kế hoạch ngân sách tương ứng."))
    if params.growth_pct <= 0:
        out.append(_finding("info", "Tăng trưởng", "Chưa tính dự phòng tăng trưởng",
                            "Nhu cầu tài nguyên thường tăng 15–30% trong năm đầu vận hành.", "Đặt dự phòng tăng trưởng 15–25%."))

    # Licensing
    lic = a["license"]
    if lic["paddedCores"] > 0 and lic.get("unit", "core") == "core":
        out.append(_finding("info", "License", f"{lic['paddedCores']} core license không dùng tới",
                            (f"License tính tối thiểu {params.license_min_cores_per_socket} core mỗi CPU"
                             + (f" và {pf['license_min_per_host']} core mỗi host" if pf.get("license_min_per_host") else "")
                             + f", cấu hình chỉ có {prof['coresPerSocket']} core/CPU."),
                            f"Chọn CPU từ {params.license_min_cores_per_socket} core trở lên để dùng hết license."))

    # Cluster size
    if best["hostsTotal"] > th.cluster_warn_hosts:
        out.append(_finding("warning", "Kiến trúc", f"Cụm quá lớn ({best['hostsTotal']} host)",
                            "Khó vận hành, nâng cấp và khoanh vùng sự cố.",
                            f"Chia thành nhiều cụm {th.cluster_info_hosts}–{th.cluster_warn_hosts} host hoặc dùng host lớn hơn."))
    elif best["hostsTotal"] > th.cluster_info_hosts:
        out.append(_finding("info", "Kiến trúc", f"{best['hostsTotal']} host — nên chia nhiều cụm",
                            "Vẫn trong giới hạn của nền tảng nhưng nên tách theo tầng ứng dụng.", "Thiết kế từ 2 cụm, mỗi cụm có N+1 riêng."))

    # Storage
    st = best["storage"]
    if params.storage_mode == "local":
        prot = params.storage_protection_factor or 1
        hci = pf["hci"]
        if st.get("ok") is False:
            out.append(_finding("critical", "Lưu trữ", "Dung lượng đĩa trên host chưa đủ",
                                f"Mỗi host cần {_gb_txt(st.get('perHostNeededGB') or 0)} dung lượng thô, cấu hình có {_gb_txt(prof['diskGB'] or 0)}.",
                                "Tăng số lượng / dung lượng ổ đĩa mỗi host, hoặc chuyển sang SAN."))
        min_nodes = max(int(pf.get("hci_min_nodes", 3)), 4 if 1.0 < prot < 1.9 else 0)
        if best["hostsTotal"] < min_nodes:
            out.append(_finding("warning", "Lưu trữ", f"{hci} cần tối thiểu {min_nodes} host",
                                f"Mức bảo vệ ×{prot:g} trên {hci} yêu cầu từ {min_nodes} host.", f"Nâng cụm lên {min_nodes} host."))
        elif best["hostsTotal"] == min_nodes and prot >= 1.9:
            out.append(_finding("warning", "Lưu trữ", f"{hci} {min_nodes} host: không còn chỗ dựng lại dữ liệu khi 1 host bảo trì",
                                "Với mức bảo vệ chịu lỗi 1 host (2 bản sao), khi 1 host bảo trì hoặc hỏng, dữ liệu chỉ còn một bản cho đến khi host trở lại.",
                                f"Khuyến nghị {min_nodes + 1} host để {hci} tự dựng lại dữ liệu; nếu giữ {min_nodes} host, lên lịch bảo trì ngắn và theo dõi chặt."))
        if params.storage_overhead_pct < th.vsan_free_min_pct:
            out.append(_finding("warning", "Lưu trữ", f"Dự trữ dung lượng {hci} {params.storage_overhead_pct:g}% thấp hơn khuyến nghị",
                                f"Lưu trữ HCI cần khoảng {th.vsan_free_min_pct:g}–30% dung lượng trống cho snapshot, dựng lại dữ liệu và bảo trì.",
                                f"Tăng mức dự trữ lên ≥ {th.vsan_free_min_pct:g}% khi chốt số lượng ổ đĩa."))
        out.append(_finding("info", "Lưu trữ", f"{pf['ha']} cần lưu trữ dùng chung",
                            f"Thiết kế giả định ổ đĩa trên host được gộp thành {pf['hci_long']}. Nếu chỉ dùng đĩa cục bộ độc lập, HA không khởi động lại được VM trên host khác.",
                            f"Chốt {hci} hoặc SAN dùng chung trước khi báo giá."))
    else:
        out.append(_finding("info", "Lưu trữ", f"Cần {_gb_txt(demand['storageNeededGB'])} trên SAN",
                            f"Đã gồm +{params.growth_pct:g}% tăng trưởng và +{params.storage_overhead_pct:g}% dự trữ (snapshot, swap, vùng trống).",
                            "Xác nhận dung lượng trống, hiệu năng (IOPS) và chính sách sao lưu / nhân bản trên SAN."))

    out.sort(key=lambda x: SEVERITY_ORDER.get(x["severity"], 9))
    return out


def next_steps(demand, params, best, n_findings_crit: int) -> List[Dict[str, str]]:
    """Implementation plan (SI engagement)."""
    if not best:
        return []
    from . import platforms
    pf = platforms.get(params)
    p = best["profile"]
    steps = [
        {"phase": "1. Thống nhất yêu cầu", "when": "Tuần 1",
         "what": f"Rà soát danh sách {demand['vmCount']} VM và các giả định (tỷ lệ chia sẻ, tăng trưởng {params.growth_pct:g}%, dự phòng N+{params.redundancy}) với khách hàng."},
        {"phase": "2. Chốt cấu hình & báo giá", "when": "Tuần 2",
         "what": f"Hoàn thiện cấu hình {best['hostsTotal']} host {p['name']}, gửi báo giá gồm phần cứng, license, bảo hành, dịch vụ triển khai và TCO {params.horizon_years} năm."},
        {"phase": "3. Phê duyệt", "when": "Tuần 3–4", "what": "Khách hàng phê duyệt phương án và ngân sách; báo cáo này dùng làm thuyết minh kỹ thuật."},
        {"phase": "4. Triển khai", "when": "Sau khi giao thiết bị",
         "what": f"Lắp đặt, cấu hình cụm {pf['label']}: {pf['ha']}, {pf['balancer']} (kèm {pf['anti_affinity']}), {pf['cpu_compat']}, "
                 f"mạng quản lý / {pf['migration']}" + (f" / {pf['hci']}" if params.storage_mode == "local" else " / lưu trữ") + ", mẫu VM chuẩn."},
        {"phase": "5. Kiểm thử & nghiệm thu", "when": "Trước vận hành chính thức",
         "what": f"Kiểm thử chịu lỗi: tắt {max(1, best['redundancy'])} host, xác nhận VM tự khởi động lại và hiệu năng đạt yêu cầu; nghiệm thu."},
        {"phase": "6. Hỗ trợ vận hành", "when": "30 / 60 / 90 ngày",
         "what": "Theo dõi hiệu năng, điều chỉnh VM cấp thừa, đánh giá lại năng lực sau 6 tháng."},
    ]
    if n_findings_crit:
        steps.insert(1, {"phase": "0. Xử lý rủi ro", "when": "Trước khi chốt cấu hình",
                         "what": f"Điều chỉnh thiết kế để xử lý {n_findings_crit} rủi ro nghiêm trọng tại mục Rủi ro & khuyến nghị."})
    return steps


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def annotate_results(results: List[Dict[str, Any]], vms, params, demand: Dict[str, Any],
                     ref_rows: Optional[List[Dict[str, Any]]] = None) -> None:
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
    _scores(results, thresholds_of(params), ref_rows)


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
        "expandText": expand_text(proj),
    }
