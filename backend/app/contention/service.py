"""Contention — storage (latest state + metrics history) and the one report every consumer reads (page, cards, Hộp việc)."""
import datetime as dt
from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import metrics
from ..cmdb.models import vc_key
from . import rules as R
from .models import ContentionState

METRICS = (("readyPct", "ready_pct"), ("costopPct", "costop_pct"), ("balloonMB", "balloon_mb"), ("swappedMB", "swapped_mb"),
           ("latencyMs", "latency_ms"))
RECORDED = {"vm": ("readyPct", "costopPct", "latencyMs"), "host": ("latencyMs", "balloonMB")}   # history (charts); balloon / swap
# of VMs are judged on the consecutive-sync counters, which keeps the volume at 3 points per VM and sync


def store(db: Session, vcenter_key: str, extras: Dict[str, Any], now: Optional[dt.datetime] = None) -> int:
    """Replace the latest figures of one vCenter and record them in metrics.py. extras["perf"] None = not readable this round
    (previous state kept); missing = a source without counters (cleared). Caller commits."""
    if "perf" in (extras or {}) and extras["perf"] is None:
        return 0
    perf = (extras or {}).get("perf") or {}
    now = now or dt.datetime.utcnow()
    prev = {(k, n): (b, s) for k, n, b, s in db.query(ContentionState.kind, ContentionState.name, ContentionState.balloon_polls,
                                                        ContentionState.swap_polls).filter(ContentionState.vcenter_key == vcenter_key)}
    db.query(ContentionState).filter(ContentionState.vcenter_key == vcenter_key).delete(synchronize_session=False)
    rows, points = [], {"vm": [], "host": []}
    for kind, items, name_key in (("vm", perf.get("vms") or [], "vm"), ("host", perf.get("hosts") or [], "host")):
        for it in items:
            name = str(it[name_key])[:255]
            b0, s0 = prev.get((kind, name), (0, 0))
            row = {"vcenter_key": vcenter_key, "kind": kind, "name": name, "cluster": it.get("cluster"), "host": it.get("host"),
                   "vcpu": it.get("vcpu"), "captured_at": now,
                   "balloon_polls": b0 + 1 if (it.get("balloonMB") or 0) > 0 else 0, "swap_polls": s0 + 1 if (it.get("swappedMB") or 0) > 0 else 0}
            for key, col in METRICS:
                row[col] = it.get(key)
                if key in RECORDED[kind] and it.get(key) is not None:
                    points[kind].append((vc_key(vcenter_key, name), key, it[key]))
            rows.append(row)
    db.bulk_insert_mappings(ContentionState, rows)
    for kind, pts in points.items():
        metrics.record(db, kind, pts, now)
    return len(rows)


def _row(s: ContentionState) -> Dict[str, Any]:
    r = {"kind": s.kind, "name": s.name, "vcenterKey": s.vcenter_key, "cluster": s.cluster, "host": s.host, "vcpu": s.vcpu,
         "balloonPolls": s.balloon_polls, "swapPolls": s.swap_polls, **{k: getattr(s, c) for k, c in METRICS}}
    r["level"], r["reasons"] = R.assess(r)
    return r


def report(db: Session) -> Dict[str, Any]:
    from .. import vm_ip
    rows = [_row(s) for s in db.query(ContentionState)]
    vms = [r for r in rows if r["kind"] == "vm"]
    hosts = [r for r in rows if r["kind"] == "host"]
    vm_ip.attach(db, vms, vm_key="name")
    rank = lambda r: (R.RANK[r["level"]], -(r.get("readyPct") or 0), r["name"])  # noqa: E731
    vms.sort(key=rank)
    hosts.sort(key=lambda r: (R.RANK[r["level"]], -(r.get("latencyMs") or 0), r["name"]))
    captured = db.query(func.max(ContentionState.captured_at)).scalar()
    return {"vms": vms, "hosts": hosts, "clusters": R.clusters(vms),
            "summary": {"vms": len(vms), "hosts": len(hosts), "hotVms": sum(1 for r in vms if r["level"] != "ok"),
                        "badVms": sum(1 for r in vms if r["level"] == "bad"), "hotHosts": sum(1 for r in hosts if r["level"] != "ok"),
                        "capturedAt": captured.isoformat() + "Z" if captured else None},
            "thresholds": {"ready": R.READY, "costop": R.COSTOP, "latency": R.LATENCY, "sustained": R.SUSTAINED,
                           "clusterSharePct": R.CLUSTER_SHARE * 100}}


def for_entity(db: Session, kind: str, name: str, vcenter_key: str) -> Optional[Dict[str, Any]]:
    s = (db.query(ContentionState).filter(ContentionState.vcenter_key == (vcenter_key or ""), ContentionState.kind == kind,
                                          ContentionState.name == name).first())
    return _row(s) if s else None


def history(db: Session, kind: str, name: str, vcenter_key: str, days: int, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Series of one VM / host for the chart (raw, hourly or daily from metrics.py by window)."""
    now = now or dt.datetime.utcnow()
    e = vc_key(vcenter_key, name)
    since = now - dt.timedelta(days=days)
    return {k: [[t.replace(microsecond=0).isoformat() + "Z", round(v, 2)] for t, v in metrics.series(db, kind, [e], k, since, now)[e]]
            for k in RECORDED[kind]}
