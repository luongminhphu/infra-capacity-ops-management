"""Physical-host sizing from a list of requested VMs.

Input: the VM list a Dev team hands over for a new application/system
(name, quantity, vCPU, RAM GB, Disk GB), a set of sizing assumptions
(target overcommit, headroom, growth buffer, N+x redundancy, hypervisor
reserve) and one or more candidate host configurations.

Output, per candidate host configuration: how many hosts are needed, which
resource is binding (CPU or RAM), the resulting overcommit / utilisation,
behaviour when `redundancy` hosts are lost, aggregate hardware totals and —
when unit prices are given — an estimated budget. Storage is sized as a SAN
capacity requirement by default (shared Pure/NetApp arrays), optionally as
local/vSAN disk per host.

All math is pure Python so it can be reused by the JSON API, the Excel export
and a future PDF report.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .sizing_advisory import AdvisoryThresholds

# Column aliases accepted when importing an Excel/CSV VM list (lower-case,
# matched as "contains" after stripping spaces / diacritics-insensitive words).
VM_COLUMN_ALIASES: Dict[str, List[str]] = {
    "name": ["ten vm", "tên vm", "ten may chu", "tên máy chủ", "vm name", "vmname", "name", "server", "may chu", "máy chủ", "vm", "hostname", "ten", "tên"],
    "qty": ["so luong", "số lượng", "sl", "qty", "quantity", "count", "so may", "số máy"],
    "vcpu": ["vcpu", "cpu", "core", "cores", "so cpu", "số cpu"],
    "ram_gb": ["ram", "memory", "mem", "bo nho", "bộ nhớ"],
    "disk_gb": ["disk", "storage", "o cung", "ổ cứng", "dung luong", "dung lượng", "hdd", "ssd", "luu tru", "lưu trữ"],
    "note": ["ghi chu", "ghi chú", "note", "notes", "mo ta", "mô tả", "description", "role", "vai tro", "vai trò", "os", "he dieu hanh"],
}


@dataclass
class VmSpec:
    name: str
    qty: int = 1
    vcpu: float = 0
    ram_gb: float = 0
    disk_gb: float = 0
    note: str = ""


@dataclass
class HostProfile:
    name: str
    sockets: int = 2
    cores_per_socket: int = 16
    ram_gb: float = 512
    disk_gb: float = 0          # local disk per host (only used when storage_mode == 'local')
    unit_price: Optional[float] = None  # optional, any currency; used for budget estimate

    @property
    def cores(self) -> int:
        return int(self.sockets * self.cores_per_socket)


@dataclass
class SizingParams:
    cpu_ratio: float = 3.0           # target vCPU : pCPU-logic (consistent with the app's overcommit metric)
    ht_factor: float = 2.0           # logical threads per physical core
    ram_ratio: float = 1.0           # target vRAM : pRAM (1.0 = no RAM overcommit)
    max_util_pct: float = 80.0       # headroom: plan to use at most this % of host capacity
    growth_pct: float = 20.0         # growth buffer applied to the Dev team's request
    redundancy: int = 1              # N + redundancy
    min_hosts: int = 2
    reserve_cores: float = 2.0       # physical cores reserved for the hypervisor (+ CVM / Ceph if any) per host
    reserve_ram_gb: float = 16.0     # RAM reserved for the hypervisor (+ CVM / Ceph if any) per host
    storage_mode: str = "san"        # 'san' | 'local'
    storage_overhead_pct: float = 25.0  # snapshots, swap, vmdk growth, free-space floor
    storage_protection_factor: float = 1.0  # local / HCI only: 2-way mirror → 2.0, RAID-5/EC 3+1 → 1.33 ...
    environment: str = ""            # free text shown on the report, e.g. "Core Banking phase 2"
    # Advisory / costing (see sizing_advisory.py)
    annual_growth_pct: float = 15.0  # yearly organic growth used for the multi-year projection
    horizon_years: int = 3           # projection horizon and TCO period
    license_price_per_core: Optional[float] = None  # per-core subscription price per year (VCF/VVF-style), optional
    license_min_cores_per_socket: int = 16          # per-core licensing minimum per CPU socket
    optimize_for: str = "hosts"      # 'hosts' = fewest hosts first · 'cost' = lowest TCO first (when priced)
    platform: str = "vmware"         # vmware | nutanix | hyperv | proxmox | generic — vocabulary & licence rules (platforms.py)
    license_edition: str = "VMware vSphere Foundation (VVF)"  # shown on the report; final per vendor policy at quote time
    suggest_variants: bool = True    # add engine-suggested reference options (fewer cores / more RAM)
    # Manual override by the person preparing the report (applied after the automatic ranking):
    pick_profile: str = ""           # profile key "<sockets>x<coresPerSocket>x<ramGB>" (user or suggested option); "" = automatic
    host_override: int = 0           # hosts to propose for the chosen option (only raises the computed count); 0 = computed
    advisory: AdvisoryThresholds = field(default_factory=AdvisoryThresholds)  # tunable advisory thresholds

    def __post_init__(self):
        self.advisory = AdvisoryThresholds.from_any(self.advisory)


def _ceil_pos(x: float) -> int:
    if not math.isfinite(x) or x <= 0:
        return 0
    return int(math.ceil(x - 1e-9))


def _pct(num: float, den: float) -> Optional[float]:
    if den <= 0 or not math.isfinite(num) or not math.isfinite(den):
        return None
    return num / den * 100.0


def summarize_demand(vms: List[VmSpec], params: SizingParams) -> Dict[str, Any]:
    g = 1.0 + max(0.0, params.growth_pct) / 100.0
    count = sum(max(0, int(v.qty)) for v in vms)
    vcpu = sum(max(0, int(v.qty)) * max(0.0, float(v.vcpu)) for v in vms)
    ram = sum(max(0, int(v.qty)) * max(0.0, float(v.ram_gb)) for v in vms)
    disk = sum(max(0, int(v.qty)) * max(0.0, float(v.disk_gb)) for v in vms)
    so = 1.0 + max(0.0, params.storage_overhead_pct) / 100.0
    prot = params.storage_protection_factor if params.storage_mode == "local" else 1.0
    prot = max(1.0, prot or 1.0)
    return {
        "vmCount": count,
        "vcpu": vcpu, "ramGB": ram, "diskGB": disk,
        "vcpuPlanned": vcpu * g, "ramPlannedGB": ram * g, "diskPlannedGB": disk * g,
        # what the hypervisor must actually provide once overcommit targets apply
        "logicalCpuNeeded": (vcpu * g) / max(0.1, params.cpu_ratio),
        "physRamNeededGB": (ram * g) / max(0.1, params.ram_ratio),
        "storageNeededGB": disk * g * so * prot,
        "storageOverheadFactor": so, "storageProtectionFactor": prot,
        "growthFactor": g,
        "avgVcpu": (vcpu / count) if count else 0.0,
        "avgRamGB": (ram / count) if count else 0.0,
        "avgDiskGB": (disk / count) if count else 0.0,
    }


def profile_key(p) -> str:
    """Stable key of a host configuration (HostProfile or result['profile'] dict)."""
    if isinstance(p, dict):
        return f"{int(p['sockets'])}x{int(p['coresPerSocket'])}x{float(p['ramGB']):g}"
    return f"{int(p.sockets)}x{int(p.cores_per_socket)}x{float(p.ram_gb):g}"


def size_profile(profile: HostProfile, demand: Dict[str, Any], params: SizingParams, force_hosts: int = 0) -> Dict[str, Any]:
    util = max(0.05, min(1.0, params.max_util_pct / 100.0))
    usable_cores = max(0.0, profile.cores - params.reserve_cores)
    usable_ram = max(0.0, profile.ram_gb - params.reserve_ram_gb)
    logical_per_host = usable_cores * max(1.0, params.ht_factor)

    # Capacity one host offers at target overcommit and headroom.
    vcpu_cap_host = logical_per_host * params.cpu_ratio * util
    vram_cap_host = usable_ram * params.ram_ratio * util

    n_cpu = _ceil_pos(demand["vcpuPlanned"] / vcpu_cap_host) if vcpu_cap_host > 0 else 0
    n_ram = _ceil_pos(demand["ramPlannedGB"] / vram_cap_host) if vram_cap_host > 0 else 0
    n_base = max(n_cpu, n_ram, 1 if demand["vmCount"] else 0)
    redundancy = max(0, int(params.redundancy))
    n_total = max(n_base + redundancy, int(params.min_hosts)) if n_base else 0
    n_computed = n_total
    if n_total and force_hosts and force_hosts > n_total:
        n_total = int(force_hosts)
    if n_cpu == n_ram:
        binding = "CPU & RAM"
    else:
        binding = "CPU" if n_cpu > n_ram else "RAM"

    total_cores = profile.cores * n_total
    total_ram = profile.ram_gb * n_total
    total_logical_usable = logical_per_host * n_total
    total_ram_usable = usable_ram * n_total

    # Resulting overcommit (vs. physical) and utilisation of the headroom-adjusted capacity.
    cpu_oc_after = (demand["vcpuPlanned"] / total_logical_usable) if total_logical_usable > 0 else None
    ram_oc_after = (demand["ramPlannedGB"] / total_ram_usable) if total_ram_usable > 0 else None
    cpu_util_pct = _pct(demand["vcpuPlanned"], vcpu_cap_host * n_total / util) if n_total else None
    ram_util_pct = _pct(demand["ramPlannedGB"], vram_cap_host * n_total / util) if n_total else None

    # Failure case: lose `redundancy` hosts — can the remainder still carry the load within 100% of target capacity?
    n_fail = max(0, n_total - redundancy)
    cpu_fail_pct = _pct(demand["vcpuPlanned"], vcpu_cap_host * n_fail / util) if n_fail else None
    ram_fail_pct = _pct(demand["ramPlannedGB"], vram_cap_host * n_fail / util) if n_fail else None
    fail_ok = bool(n_fail) and (cpu_fail_pct or 0) <= 100.0 + 1e-6 and (ram_fail_pct or 0) <= 100.0 + 1e-6
    # "At the limit": after losing the redundant hosts, the load sits at (or within a small margin of)
    # the planned max utilisation — passes N+x on paper, but any growth forces another host.
    margin = getattr(getattr(params, "advisory", None), "failover_margin_pct", 2.0)
    worst_fail = max(cpu_fail_pct or 0, ram_fail_pct or 0)
    at_limit = bool(fail_ok and redundancy and worst_fail >= params.max_util_pct - margin)

    # Spare capacity after N+x (in average-VM units) – useful "how many more VMs fit" number.
    spare_vcpu = max(0.0, vcpu_cap_host * n_fail - demand["vcpuPlanned"])
    spare_ram = max(0.0, vram_cap_host * n_fail - demand["ramPlannedGB"])
    avg_vcpu = demand["avgVcpu"] or 0
    avg_ram = demand["avgRamGB"] or 0
    fit_cpu = int(spare_vcpu // avg_vcpu) if avg_vcpu > 0 else None
    fit_ram = int(spare_ram // avg_ram) if avg_ram > 0 else None
    fits = [x for x in (fit_cpu, fit_ram) if x is not None]
    extra_vms = min(fits) if fits else None

    # Storage
    storage: Dict[str, Any] = {"mode": params.storage_mode, "neededGB": demand["storageNeededGB"]}
    if params.storage_mode == "local":
        per_host = (demand["storageNeededGB"] / n_total) if n_total else 0.0
        storage.update({
            "perHostNeededGB": per_host,
            "perHostConfiguredGB": profile.disk_gb,
            "ok": (profile.disk_gb or 0) >= per_host - 1e-6 if n_total else None,
            "totalConfiguredGB": (profile.disk_gb or 0) * n_total,
        })

    budget = (profile.unit_price * n_total) if (profile.unit_price is not None and n_total) else None

    # Efficiency: how much of the purchased (headroom-adjusted, post-failure) capacity the request actually uses.
    eff_cpu = _pct(demand["vcpuPlanned"], vcpu_cap_host * n_fail) if n_fail else None
    eff_ram = _pct(demand["ramPlannedGB"], vram_cap_host * n_fail) if n_fail else None

    return {
        "profile": {
            "name": profile.name, "sockets": profile.sockets, "coresPerSocket": profile.cores_per_socket,
            "cores": profile.cores, "ramGB": profile.ram_gb, "diskGB": profile.disk_gb, "unitPrice": profile.unit_price,
        },
        "perHost": {
            "usableCores": usable_cores, "logicalCpu": logical_per_host, "usableRamGB": usable_ram,
            "vcpuCapacity": vcpu_cap_host, "vramCapacityGB": vram_cap_host,
        },
        "hostsForCpu": n_cpu, "hostsForRam": n_ram, "hostsBase": n_base, "redundancy": redundancy,
        "hostsTotal": n_total, "hostsComputed": n_computed, "hostsForced": n_total != n_computed, "binding": binding,
        "key": profile_key(profile),
        "totals": {"cores": total_cores, "ramGB": total_ram, "logicalCpu": profile.cores * max(1.0, params.ht_factor) * n_total},
        "after": {
            "cpuOvercommit": cpu_oc_after, "ramOvercommit": ram_oc_after,
            "cpuUtilPct": cpu_util_pct, "ramUtilPct": ram_util_pct,
        },
        "failure": {"hostsLeft": n_fail, "cpuPct": cpu_fail_pct, "ramPct": ram_fail_pct, "ok": fail_ok, "atLimit": at_limit},
        # vCPU per *physical core* — the ratio customers usually reason in (vs. per thread in cpu_ratio).
        "vcpuPerCore": (demand["vcpuPlanned"] / total_cores) if total_cores else None,
        "calc": {"cpuHostsRaw": (demand["vcpuPlanned"] / vcpu_cap_host) if vcpu_cap_host > 0 else None,
                 "ramHostsRaw": (demand["ramPlannedGB"] / vram_cap_host) if vram_cap_host > 0 else None},
        "spare": {"vcpu": spare_vcpu, "ramGB": spare_ram, "extraAvgVms": extra_vms},
        "efficiency": {"cpuPct": eff_cpu, "ramPct": eff_ram},
        "storage": storage,
        "budget": budget,
    }


def _cost_basis(results: List[Dict[str, Any]]) -> str:
    """Compare like with like: TCO when every option has one, else hardware budget, else licence cores."""
    live = [r for r in results if r["hostsTotal"]]
    if live and all((r.get("advisory") or {}).get("costs", {}).get("tco") is not None for r in live):
        return "tco"
    if live and all(r["budget"] is not None for r in live):
        return "budget"
    return "cores"


def _cost_of(r: Dict[str, Any], basis: str) -> float:
    if basis == "tco":
        return r["advisory"]["costs"]["tco"]
    if basis == "budget":
        return r["budget"]
    return float((r.get("advisory") or {}).get("license", {}).get("coresTotal") or 0)


def _is_tight(r: Dict[str, Any], params: "SizingParams") -> bool:
    run = ((r.get("advisory") or {}).get("runway") or {}).get("extraGrowthPct")
    low = getattr(params.advisory, "low_runway_pct", 10.0)
    return bool(r["failure"].get("atLimit")) or (run is not None and run < low) or not r["failure"]["ok"]


def _rank_key(r: Dict[str, Any], params: "SizingParams", basis: str):
    """Fewest hosts → lowest cost (same basis for all options) → not sitting at the limit →
    highest overall score. (Previously ties went to the *most utilised* option, which picked
    configurations with no growth headroom.)"""
    hosts = r["hostsTotal"] if r["hostsTotal"] else 10**9
    cost = _cost_of(r, basis) if r["hostsTotal"] else float("inf")
    tight = 1 if _is_tight(r, params) else 0
    score = -((r.get("advisory") or {}).get("scoreTotal") or 0)
    if params.optimize_for == "cost":
        return (cost, hosts, tight, score)
    return (hosts, cost, tight, score)


STD_CORES = [8, 12, 16, 24, 32, 48, 64, 96, 128]
STD_RAM_GB = [128, 192, 256, 384, 512, 768, 1024, 1536, 2048, 3072, 4096]


def _ram_label(gb: float) -> str:
    return f"{gb / 1024:g} TB" if gb >= 1024 else f"{gb:g} GB"


def _suggest_variants(best_profile: HostProfile, best: Dict[str, Any], profiles: List[HostProfile],
                      demand: Dict[str, Any], params: "SizingParams") -> List[Dict[str, Any]]:
    """Reference options the customer usually asks about: fewer cores per CPU when CPU is
    largely idle (licence is per core), and more RAM per host when the pick has no headroom."""
    out: List[Dict[str, Any]] = []
    existing = {(p.sockets, p.cores_per_socket, float(p.ram_gb)) for p in profiles}

    def try_add(prof: HostProfile, why: str, must_keep_hosts: bool = True):
        key = (prof.sockets, prof.cores_per_socket, float(prof.ram_gb))
        if key in existing:
            return
        r = size_profile(prof, demand, params)
        if not r["hostsTotal"] or not r["failure"]["ok"] or r["failure"].get("atLimit"):
            return
        if must_keep_hosts and r["hostsTotal"] > best["hostsTotal"]:
            return
        r["suggested"] = True
        r["suggestedWhy"] = why
        existing.add(key)
        out.append(r)

    bp = best_profile
    tight = bool(best["failure"].get("atLimit"))
    ram_bound = best["binding"] in ("RAM", "CPU & RAM")
    # More RAM when the pick is at the limit on RAM.
    bigger_ram = next((g for g in STD_RAM_GB if g > bp.ram_gb), None)
    if tight and ram_bound and bigger_ram:
        try_add(HostProfile(name=f"{bp.sockets}×{bp.cores_per_socket}C / {_ram_label(bigger_ram)} (gợi ý)", sockets=bp.sockets,
                            cores_per_socket=bp.cores_per_socket, ram_gb=bigger_ram, disk_gb=bp.disk_gb), "more_ram")
    base_ram = bigger_ram if (tight and ram_bound and bigger_ram) else bp.ram_gb
    # Fewer cores when CPU is mostly idle (after losing the redundant hosts).
    cpu_fail = best["failure"]["cpuPct"] or 0
    lic_min = max(1, int(params.license_min_cores_per_socket or 0))
    from . import platforms
    per_core_licence = platforms.get(params)["license_unit"] == "core"
    if cpu_fail < 50 and per_core_licence:
        for c in STD_CORES:
            if c >= lic_min and c < bp.cores_per_socket:
                before = len(out)
                try_add(HostProfile(name=f"{bp.sockets}×{c}C / {_ram_label(base_ram)} (gợi ý)", sockets=bp.sockets,
                                    cores_per_socket=c, ram_gb=base_ram, disk_gb=bp.disk_gb), "fewer_cores")
                if len(out) > before:
                    break
    return out


def build_sizing(vms: List[VmSpec], params: SizingParams, profiles: List[HostProfile]) -> Dict[str, Any]:
    from . import sizing_advisory

    demand = summarize_demand(vms, params)
    results = [size_profile(p, demand, params) for p in profiles]
    sizing_advisory.annotate_results(results, vms, params, demand)
    basis = _cost_basis(results)
    ranked = sorted(range(len(results)), key=lambda i: _rank_key(results[i], params, basis))
    best_idx = ranked[0] if ranked and demand["vmCount"] else None
    for i, r in enumerate(results):
        r["recommended"] = (i == best_idx)
        r["rank"] = ranked.index(i) + 1 if ranked else None
        r["tight"] = _is_tight(r, params)

    best = results[best_idx] if best_idx is not None else None
    if best is not None and params.suggest_variants:
        variants = _suggest_variants(profiles[best_idx], best, profiles, demand, params)
        if variants:
            sizing_advisory.annotate_results(variants, vms, params, demand, ref_rows=results)
            for v in variants:
                v["recommended"] = False
                v["rank"] = None
                v["tight"] = _is_tight(v, params)
            results = results + variants

    # Manual selection (option and/or host count) by the person preparing the report.
    selection = {"manual": False, "autoKey": best["key"] if best else None, "autoName": best["profile"]["name"] if best else None,
                 "autoHosts": best["hostsTotal"] if best else None}
    profiles = list(profiles)
    pick = (params.pick_profile or "").strip()
    if best is not None and (pick or params.host_override):
        idx = next((i for i, r in enumerate(results) if r["key"] == pick), best_idx) if pick else best_idx
        row = results[idx]
        if row.get("suggested"):  # promote a suggested option to a real candidate
            src = row["profile"]
            hp = HostProfile(name=src["name"].replace(" (gợi ý)", ""), sockets=src["sockets"], cores_per_socket=src["coresPerSocket"],
                             ram_gb=src["ramGB"], disk_gb=src["diskGB"], unit_price=src.get("unitPrice"))
            profiles.append(hp)
            prof_idx = len(profiles) - 1
        else:
            prof_idx = idx if idx < len(profiles) else best_idx
        forced = max(0, int(params.host_override or 0))
        new_row = size_profile(profiles[prof_idx], demand, params, force_hosts=forced)
        sizing_advisory.annotate_results([new_row], vms, params, demand, ref_rows=[r for r in results if not r.get("suggested")])
        new_row["tight"] = _is_tight(new_row, params)
        if row.get("suggested"):
            results = [r for r in results if r is not row]
            results.insert(len(profiles) - 1, new_row)
            idx = len(profiles) - 1
        else:
            results[idx] = new_row
        for i, r in enumerate(results):
            r["recommended"] = (i == idx)
        new_row["rank"] = new_row.get("rank") or row.get("rank")
        new_row["manual"] = True
        best_idx, best = idx, new_row
        selection.update({"manual": True, "key": new_row["key"], "name": new_row["profile"]["name"],
                          "hosts": new_row["hostsTotal"], "hostsComputed": new_row["hostsComputed"],
                          "changedOption": new_row["key"] != selection["autoKey"], "changedHosts": new_row["hostsForced"]})

    recommendation = _recommendation_text(demand, params, best, results, basis)
    if selection["manual"]:
        parts = []
        if selection["changedOption"]:
            parts.append(f"chọn {selection['name']} thay cho phương án tự động {selection['autoName']}")
        if selection["changedHosts"]:
            parts.append(f"đề xuất {selection['hosts']} host thay vì {selection['hostsComputed']} host theo tính toán (thêm dư địa)")
        if parts:
            recommendation["why"] = "Người lập điều chỉnh: " + "; ".join(parts) + "."
    return {
        "demand": demand,
        "params": {**params.__dict__, "advisory": params.advisory.__dict__.copy()},
        "results": results,
        "recommendedIndex": best_idx,
        "selection": selection,
        "recommendation": recommendation,
        "costBasis": basis,
        "assumptions": assumption_lines(params),
        "analysis": sizing_advisory.build_advisory(vms, params, profiles, demand, results, best_idx),
    }


def assumption_rows(p: SizingParams) -> List[List[str]]:
    """[hạng mục, giá trị, ý nghĩa] — the assumptions table of the report."""
    from . import platforms
    pf = platforms.get(p)
    ht = max(1.0, p.ht_factor)
    rows = [
        ["Tỷ lệ chia sẻ CPU", f"{p.cpu_ratio:g} vCPU / thread (≈ {p.cpu_ratio * ht:g} vCPU / core)", "Số vCPU tối đa xếp lên 1 luồng CPU vật lý (HT ×" + f"{ht:g})"],
        ["Tỷ lệ chia sẻ RAM", f"{p.ram_ratio:g} : 1", "Không cấp RAM vượt dung lượng thật" if p.ram_ratio <= 1 else "Cấp RAM vượt dung lượng thật"],
        ["Vùng an toàn", f"{100 - p.max_util_pct:g}%", f"Mỗi host chỉ dùng tối đa {p.max_util_pct:g}%"],
        ["Dự phòng tăng trưởng", f"+{p.growth_pct:g}%", "Cộng vào nhu cầu ban đầu (tương đương năm thứ 1 của dự báo nếu bằng tăng trưởng hằng năm)"],
        ["Dự phòng sự cố", f"N+{p.redundancy}, tối thiểu {p.min_hosts} host", f"Mất {p.redundancy} host hệ thống vẫn chạy" if p.redundancy else "Không có host dự phòng"],
        ["Tài nguyên dành cho hypervisor", f"{p.reserve_cores:g} core + {p.reserve_ram_gb:g} GB / host",
         f"{pf['hypervisor']}" + (f" và dịch vụ lưu trữ ({pf['hci']})" if p.storage_mode == "local" and pf.get("overhead_note") else "") + "; trừ khỏi năng lực khả dụng"],
    ]
    if p.storage_mode == "local":
        rows.append([f"Lưu trữ ({pf['hci']})", f"Hệ số bảo vệ ×{p.storage_protection_factor:g}, dự trữ {p.storage_overhead_pct:g}%",
                     "×2 tương đương 2 bản sao (mirror), chịu lỗi 1 host; dự trữ cho snapshot, swap, dựng lại dữ liệu"])
    else:
        rows.append(["Lưu trữ (SAN)", f"Dự trữ {p.storage_overhead_pct:g}%", "Snapshot, swap, tăng trưởng ổ đĩa ảo, vùng trống tối thiểu"])
    rows.append(["Tăng trưởng hằng năm", f"{p.annual_growth_pct:g}% / năm, dự báo {p.horizon_years} năm",
                 "Dự báo tính lại từ nhu cầu ban đầu, không cộng dồn với dự phòng tăng trưởng"])
    unit = "socket" if pf["license_unit"] == "socket" else "core"
    lic_rule = ("Theo CPU socket" if unit == "socket" else
                f"Theo core, tối thiểu {p.license_min_cores_per_socket} core / CPU"
                + (f", {pf['license_min_per_host']} core / host" if pf.get("license_min_per_host") else ""))
    rows.append(["License", lic_rule,
                 (f"{p.license_price_per_core:,.0f} / {unit} / năm" if p.license_price_per_core is not None else "Chưa có đơn giá")
                 + f"; {platforms.edition_of(p)}"])
    return rows


def assumption_lines(p: SizingParams) -> List[str]:
    return [f"{k}: {v} — {m}." for k, v, m in assumption_rows(p)]


def _fmt_int(v: float) -> str:
    return f"{int(round(v)):,}"


def _tib(gb: float) -> str:
    return f"{gb / 1024:,.1f} TiB" if gb >= 1024 else f"{gb:,.0f} GB"


def _recommendation_text(demand: Dict[str, Any], params: SizingParams, best: Optional[Dict[str, Any]],
                         results: Optional[List[Dict[str, Any]]] = None, basis: str = "cores") -> Dict[str, str]:
    if not best or not demand["vmCount"]:
        return {"headline": "Chưa có VM nào trong danh sách yêu cầu.", "detail": "", "why": ""}
    p = best["profile"]
    headline = (
        f"Đề xuất {best['hostsTotal']} host {p['name']} ({p['cores']} core · {_fmt_int(p['ramGB'])} GB RAM mỗi host) "
        f"cho {demand['vmCount']} VM ({_fmt_int(demand['vcpu'])} vCPU · {_fmt_int(demand['ramGB'])} GB RAM · {_tib(demand['diskGB'])} dữ liệu)."
    )
    fail = best["failure"]
    if fail["ok"] and fail["cpuPct"] is not None:
        fail_txt = (f"Khi mất {best['redundancy']} host, cụm dùng {fail['cpuPct']:.0f}% CPU · {fail['ramPct']:.0f}% RAM"
                    + (" — sát mức tối đa." if fail.get("atLimit") else "."))
    else:
        fail_txt = f"Chưa đạt dự phòng N+{best['redundancy']} — cần thêm host hoặc giảm tỷ lệ chia sẻ."
    from . import platforms
    hci = platforms.get(params)["hci"]
    store_txt = (f"Lưu trữ cần cấp: {_tib(demand['storageNeededGB'])} trên SAN." if params.storage_mode == "san"
                 else f"Lưu trữ {hci}: {_tib(demand['storageNeededGB'])} dung lượng thô (~{_tib(best['storage'].get('perHostNeededGB', 0))} mỗi host).")
    bind = {"CPU": "CPU", "RAM": "RAM", "CPU & RAM": "cả CPU và RAM"}.get(best["binding"], best["binding"])
    detail = (f"Yếu tố quyết định số host là {bind}: cần {best['hostsForCpu']} host theo CPU, {best['hostsForRam']} host theo RAM, "
              f"cộng {best['redundancy']} host dự phòng. {fail_txt} {store_txt}")
    extra = best["spare"].get("extraAvgVms")
    if extra is not None:
        detail += (" Không còn dư địa cho VM mới khi vẫn giữ dự phòng." if extra <= 0
                   else f" Sau khi trừ dự phòng, cụm còn chứa thêm khoảng {extra} VM cỡ trung bình.")
    # Why this option over the others (same order as the ranking rule).
    why = ""
    peers = [r for r in (results or []) if r is not best and r["hostsTotal"] and not r.get("suggested")]
    if peers:
        basis_txt = {"tco": "TCO", "budget": "chi phí phần cứng", "cores": "số core license"}[basis]
        same_hosts = [r for r in peers if r["hostsTotal"] == best["hostsTotal"]]
        parts = []
        fewer = [r for r in peers if r["hostsTotal"] > best["hostsTotal"]]
        if fewer and params.optimize_for != "cost":
            parts.append(f"ít host hơn {', '.join(r['profile']['name'] for r in fewer[:2])}")
        if same_hosts:
            tight_peers = [r for r in same_hosts if r.get("tight")]
            if tight_peers and not best.get("tight"):
                parts.append(f"cùng {best['hostsTotal']} host nhưng {', '.join(r['profile']['name'] for r in tight_peers[:2])} sát ngưỡng, không còn dư địa")
            else:
                parts.append(f"cùng số host, {basis_txt} thấp hơn hoặc bằng và điểm tổng hợp cao hơn")
        if parts:
            why = "Lý do chọn: " + "; ".join(parts) + "."
    return {"headline": headline, "detail": detail, "why": why}


# ---------------------------------------------------------------------------
# Excel / CSV import of the Dev team's VM list
# ---------------------------------------------------------------------------
def _norm_header(s: Any) -> str:
    import unicodedata
    text = unicodedata.normalize("NFD", str(s or "").strip().lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    return " ".join(text.replace("_", " ").replace("-", " ").split())


def map_columns(headers: List[Any]) -> Dict[str, int]:
    """Return {field: column index} using the alias table. Exact match wins,
    then 'contains'. Each column is assigned once."""
    norm = [_norm_header(h) for h in headers]
    mapping: Dict[str, int] = {}
    used: set = set()
    for field_name, aliases in VM_COLUMN_ALIASES.items():
        for alias in aliases:
            a = _norm_header(alias)
            for idx, h in enumerate(norm):
                if idx in used or not h:
                    continue
                if h == a or h.startswith(a + " ") or h.startswith(a + "(") or h == a.replace(" ", ""):
                    mapping[field_name] = idx
                    used.add(idx)
                    break
            if field_name in mapping:
                break
    # second pass: "contains" for anything still missing
    for field_name, aliases in VM_COLUMN_ALIASES.items():
        if field_name in mapping:
            continue
        for alias in aliases:
            a = _norm_header(alias)
            if len(a) < 3:
                continue
            for idx, h in enumerate(norm):
                if idx in used or not h:
                    continue
                if a in h:
                    mapping[field_name] = idx
                    used.add(idx)
                    break
            if field_name in mapping:
                break
    return mapping


def _to_num(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower().replace(",", "")
    mult = 1.0
    if s.endswith("tb"):
        mult, s = 1024.0, s[:-2]
    elif s.endswith("gb"):
        s = s[:-2]
    elif s.endswith("mb"):
        mult, s = 1 / 1024.0, s[:-2]
    s = "".join(ch for ch in s if ch.isdigit() or ch == ".")
    try:
        return float(s) * mult if s else 0.0
    except ValueError:
        return 0.0


def rows_to_vmspecs(table: List[List[Any]]) -> Dict[str, Any]:
    """`table` = list of rows (first non-empty row = header). Returns {rows, warnings, mapping}."""
    warnings: List[str] = []
    rows_out: List[Dict[str, Any]] = []
    # find header row: first row with ≥2 non-empty cells
    header_idx = None
    for i, row in enumerate(table[:20]):
        if sum(1 for c in row if str(c or "").strip()) >= 2:
            header_idx = i
            break
    if header_idx is None:
        return {"rows": [], "warnings": ["Không tìm thấy dòng tiêu đề trong file."], "mapping": {}}
    headers = table[header_idx]
    mapping = map_columns(headers)
    missing = [f for f in ("vcpu", "ram_gb") if f not in mapping]
    if missing:
        warnings.append(
            "Không nhận diện được cột " + ", ".join({"vcpu": "vCPU", "ram_gb": "RAM"}[m] for m in missing)
            + f". Tiêu đề đọc được: {', '.join(str(h) for h in headers if str(h or '').strip())}."
        )
        return {"rows": [], "warnings": warnings, "mapping": mapping}

    def cell(row, key, default=None):
        idx = mapping.get(key)
        if idx is None or idx >= len(row):
            return default
        return row[idx]

    for n, row in enumerate(table[header_idx + 1:], start=header_idx + 2):
        if not any(str(c or "").strip() for c in row):
            continue
        name = str(cell(row, "name", "") or "").strip()
        vcpu = _to_num(cell(row, "vcpu"))
        ram = _to_num(cell(row, "ram_gb"))
        disk = _to_num(cell(row, "disk_gb"))
        qty_raw = cell(row, "qty")
        qty = int(_to_num(qty_raw)) if qty_raw not in (None, "") else 1
        if qty <= 0:
            qty = 1
        if vcpu <= 0 and ram <= 0:
            if name:
                warnings.append(f"Dòng {n} ({name}): thiếu vCPU/RAM — bỏ qua.")
            continue
        if ram and ram < 1 and "mb" not in str(cell(row, "ram_gb", "")).lower():
            warnings.append(f"Dòng {n} ({name or 'VM'}): RAM {ram:g} GB có vẻ bất thường.")
        rows_out.append({
            "name": name or f"VM-{len(rows_out) + 1:02d}",
            "qty": qty, "vcpu": vcpu, "ramGB": ram, "diskGB": disk,
            "note": str(cell(row, "note", "") or "").strip(),
        })
    if not rows_out:
        warnings.append("File không có dòng VM hợp lệ.")
    return {"rows": rows_out, "warnings": warnings, "mapping": mapping}


def parse_vm_file(filename: str, content: bytes) -> Dict[str, Any]:
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        import io
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        best: Optional[Dict[str, Any]] = None
        for ws in wb.worksheets:
            table = [list(r) for r in ws.iter_rows(values_only=True)]
            parsed = rows_to_vmspecs(table)
            parsed["sheet"] = ws.title
            if parsed["rows"] and (best is None or len(parsed["rows"]) > len(best["rows"])):
                best = parsed
            if best is None:
                best = parsed
        return best or {"rows": [], "warnings": ["File Excel không có sheet nào."], "mapping": {}}
    if name.endswith(".xls"):
        return {"rows": [], "warnings": ["Định dạng .xls cũ chưa hỗ trợ — hãy lưu lại thành .xlsx."], "mapping": {}}
    # CSV / TSV / TXT
    import csv
    import io
    text = content.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    table = [row for row in csv.reader(io.StringIO(text), dialect)]
    return rows_to_vmspecs(table)


# ---------------------------------------------------------------------------
# Default candidate host profiles (typical 2-socket rack servers)
# ---------------------------------------------------------------------------
DEFAULT_PROFILES: List[Dict[str, Any]] = [
    {"name": "2×16C / 512 GB", "sockets": 2, "coresPerSocket": 16, "ramGB": 512, "diskGB": 0, "unitPrice": None},
    {"name": "2×24C / 768 GB", "sockets": 2, "coresPerSocket": 24, "ramGB": 768, "diskGB": 0, "unitPrice": None},
    {"name": "2×32C / 1 TB", "sockets": 2, "coresPerSocket": 32, "ramGB": 1024, "diskGB": 0, "unitPrice": None},
]

TEMPLATE_ROWS: List[Dict[str, Any]] = [
    {"name": "APP-WEB", "qty": 4, "vcpu": 4, "ramGB": 16, "diskGB": 100, "note": "Web/API tier, Ubuntu 22.04"},
    {"name": "APP-SVC", "qty": 6, "vcpu": 8, "ramGB": 32, "diskGB": 200, "note": "Service tier"},
    {"name": "DB-PRIMARY", "qty": 2, "vcpu": 16, "ramGB": 128, "diskGB": 2048, "note": "PostgreSQL primary/standby"},
    {"name": "CACHE", "qty": 3, "vcpu": 4, "ramGB": 64, "diskGB": 100, "note": "Redis cluster"},
    {"name": "MQ", "qty": 3, "vcpu": 4, "ramGB": 16, "diskGB": 500, "note": "Kafka"},
    {"name": "MGMT", "qty": 2, "vcpu": 2, "ramGB": 8, "diskGB": 100, "note": "Bastion, monitoring agent"},
]
