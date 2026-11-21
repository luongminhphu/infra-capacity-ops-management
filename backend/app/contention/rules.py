"""Contention levels (pure, tested). One row = latest figures of one VM / host + consecutive polls with balloon / swap.

  CPU ready      ≥ 5 % THEO DÕI · ≥ 10 % CẢNH BÁO
  co-stop        ≥ 3 % THEO DÕI · ≥ 6 % CẢNH BÁO
  balloon / swap > 0 for ≥ 3 consecutive syncs → THEO DÕI (swap: CẢNH BÁO)
  disk latency   ≥ 20 ms THEO DÕI · ≥ 50 ms CẢNH BÁO
A cluster where ≥ 20 % of its measured VMs are at THEO DÕI or worse is a cluster finding at CẢNH BÁO."""
from typing import Any, Dict, List, Optional, Tuple

READY = (5.0, 10.0)
COSTOP = (3.0, 6.0)
LATENCY = (20.0, 50.0)
SUSTAINED = 3
CLUSTER_SHARE = 0.20
RANK = {"crit": 0, "bad": 1, "warn": 2, "ok": 3}


def _two(v: Optional[float], t: Tuple[float, float]) -> Optional[str]:
    if v is None:
        return None
    return "bad" if v >= t[1] else "warn" if v >= t[0] else None


def _vn(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def assess(r: Dict[str, Any]) -> Tuple[str, List[str]]:
    """(level, reasons) of one row."""
    found = []
    for key, t, label, unit in (("readyPct", READY, "CPU ready", "%"), ("costopPct", COSTOP, "co-stop", "%"),
                                ("latencyMs", LATENCY, "độ trễ disk", " ms")):
        lv = _two(r.get(key), t)
        if lv:
            found.append((lv, f"{label} {_vn(r[key])}{unit}"))
    if (r.get("balloonMB") or 0) > 0 and r.get("balloonPolls", 0) >= SUSTAINED:
        found.append(("warn", f"balloon {_vn(r['balloonMB'])} MB ({r['balloonPolls']} lần liên tiếp)"))
    if (r.get("swappedMB") or 0) > 0 and r.get("swapPolls", 0) >= SUSTAINED:
        found.append(("bad", f"swap {_vn(r['swappedMB'])} MB ({r['swapPolls']} lần liên tiếp)"))
    if not found:
        return "ok", []
    found.sort(key=lambda x: RANK[x[0]])
    return found[0][0], [t for _, t in found]


def clusters(vms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Cluster findings from the assessed VM rows (each with "level")."""
    per: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for v in vms:
        per.setdefault((v["vcenterKey"], v.get("cluster") or ""), []).append(v)
    out = []
    for (vc, cl), rows in per.items():
        hot = [r for r in rows if r["level"] != "ok"]
        share = len(hot) / len(rows)
        out.append({"vcenterKey": vc, "cluster": cl, "vms": len(rows), "hot": len(hot), "sharePct": round(share * 100, 1),
                    "level": "bad" if rows and share >= CLUSTER_SHARE else "ok",
                    "readyAvg": round(sum(r.get("readyPct") or 0 for r in rows) / len(rows), 2)})
    out.sort(key=lambda c: (RANK[c["level"]], -c["sharePct"], c["cluster"]))
    return out
