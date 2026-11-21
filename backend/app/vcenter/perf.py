"""Contention counters read with PerformanceManager.QueryPerf (ICO v2 G9c) — read-only, in the inventory session.

Realtime interval (20 s), the last 15 minutes (45 samples) averaged, aggregate instance (""), in batches of BATCH entities per call.
Counter ids are looked up once per session from perfManager.perfCounter. Powered-on VMs, largest vCPU first, up to `max_vms`
(and a time budget): what is cut is reported, never silently dropped.
  VM:   readyPct  = cpu.ready.summation (ms per 20 s sample, summed over vCPUs) / (20 000 × vCPU) × 100
        costopPct = cpu.costop.summation, same formula
        balloonMB = mem.vmmemctl.average (KB) / 1024 · swappedMB = mem.swapped.average (KB) / 1024
        latencyMs = disk.maxTotalLatency.latest
  host: latencyMs = disk.maxTotalLatency.latest · balloonMB = mem.vmmemctl.average / 1024
"""
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from pyVmomi import vim

from .extras import _moid, _sa

logger = logging.getLogger(__name__)

INTERVAL_S = 20
SAMPLES = 45                      # 15 minutes of 20-second samples
BATCH = 50
COUNTERS = {"cpu.ready.summation": "ready", "cpu.costop.summation": "costop", "mem.vmmemctl.average": "balloon",
            "mem.swapped.average": "swapped", "disk.maxTotalLatency.latest": "latency"}
VM_COUNTERS = tuple(COUNTERS)
HOST_COUNTERS = ("mem.vmmemctl.average", "disk.maxTotalLatency.latest")


def ready_pct(ms: float, vcpu: float, interval_s: int = INTERVAL_S) -> Optional[float]:
    """% of the interval the vCPUs were ready but not scheduled: 1 600 ms over 20 s on 4 vCPU = 2 %."""
    if not vcpu or vcpu <= 0:
        return None
    return round(ms / (interval_s * 1000.0 * vcpu) * 100.0, 2)


def to_row(avg: Dict[str, float], vcpu: Optional[float]) -> Dict[str, Optional[float]]:
    """Averaged raw counter values → the stored figures (units of the module docstring)."""
    out: Dict[str, Optional[float]] = {}
    if "ready" in avg:
        out["readyPct"] = ready_pct(avg["ready"], vcpu or 0)
    if "costop" in avg:
        out["costopPct"] = ready_pct(avg["costop"], vcpu or 0)
    if "balloon" in avg:
        out["balloonMB"] = round(avg["balloon"] / 1024.0, 1)
    if "swapped" in avg:
        out["swappedMB"] = round(avg["swapped"] / 1024.0, 1)
    if "latency" in avg:
        out["latencyMs"] = round(avg["latency"], 1)
    return out


def counter_ids(perf_manager) -> Dict[str, int]:
    out = {}
    for c in perf_manager.perfCounter or []:
        name = f"{_sa(c, 'groupInfo', 'key')}.{_sa(c, 'nameInfo', 'key')}.{_sa(c, 'rollupType')}"
        if name in COUNTERS:
            out[name] = c.key
    return out


def query(perf_manager, objs: List[Any], ids: Dict[str, int], names: Tuple[str, ...]) -> Dict[str, Dict[str, float]]:
    """{moId: {short counter name: average}} for one batch (one QueryPerf call)."""
    metric_ids = [vim.PerformanceManager.MetricId(counterId=ids[n], instance="") for n in names if n in ids]
    if not metric_ids or not objs:
        return {}
    specs = [vim.PerformanceManager.QuerySpec(entity=o, metricId=metric_ids, intervalId=INTERVAL_S, maxSample=SAMPLES, format="normal")
             for o in objs]
    by_id = {v: COUNTERS[k] for k, v in ids.items()}
    out: Dict[str, Dict[str, float]] = {}
    for em in perf_manager.QueryPerf(querySpec=specs) or []:
        vals: Dict[str, float] = {}
        for s in em.value or []:
            samples = [x for x in (s.value or []) if x is not None and x >= 0]
            key = by_id.get(_sa(s, "id", "counterId"))
            if key and samples:
                vals[key] = sum(samples) / len(samples)
        out[_moid(em.entity)] = vals
    return out


def read(content, hosts, vms, host_info: Dict[str, Tuple[str, str]], max_vms: int = 2000, budget_s: float = 90.0) -> Dict[str, Any]:
    pm = content.perfManager
    ids = counter_ids(pm)
    started = time.monotonic()
    on = [(moid, obj, props) for moid, (obj, props) in vms.items()
          if str(_sa(props.get("summary"), "runtime", "powerState") or "") == "poweredOn"]
    on.sort(key=lambda t: -(_sa(t[2].get("summary"), "config", "numCpu") or 0))
    take, cut = on[:max_vms], max(0, len(on) - max_vms)
    vm_rows, host_rows = [], []
    for i in range(0, len(take), BATCH):
        if time.monotonic() - started > budget_s:
            cut += len(take) - i
            break
        part = take[i:i + BATCH]
        got = query(pm, [o for _, o, _ in part], ids, VM_COUNTERS)
        for moid, _o, props in part:
            s = props.get("summary")
            vcpu = _sa(s, "config", "numCpu") or 0
            host, cluster = host_info.get(_moid(_sa(s, "runtime", "host")), (None, "UNKNOWN"))
            vm_rows.append({"vm": _sa(s, "config", "name") or props.get("name") or moid, "cluster": cluster, "host": host, "vcpu": vcpu,
                            **to_row(got.get(moid, {}), vcpu)})
    hl = list(hosts.items())
    for i in range(0, len(hl), BATCH):
        if time.monotonic() - started > budget_s:
            break
        part = hl[i:i + BATCH]
        got = query(pm, [o for _, (o, _p) in part], ids, HOST_COUNTERS)
        for moid, (_o, props) in part:
            name, cluster = host_info.get(moid, (props.get("name") or moid, "UNKNOWN"))
            host_rows.append({"host": name, "cluster": cluster, **to_row(got.get(moid, {}), None)})
    return {"vms": vm_rows, "hosts": host_rows, "cut": cut}
