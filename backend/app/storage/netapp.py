"""NetApp ONTAP (AFF A300 / FAS, ONTAP ≥ 9.6) — REST API, read-only (GET only, HTTP Basic with a user of
the built-in `readonly` role, application `http`).

Reads: cluster (version, metric latency / IOPS / throughput), nodes (model, state, failed fans / PSUs,
temperature), aggregates (capacity, efficiency), volumes (NFS junction path), LUNs (serial → NAA), data
LIFs serving NFS (IP → SVM), broken disks, EMS events of severity error and above in the last 24 hours.
VMware sees an ONTAP LUN as naa.600a0980 + hex(serial_number ASCII).
"""
import base64
import datetime as dt
from typing import Any, Dict, List, Optional

from . import StorageConfig, StorageError
from .http import JsonClient

GB = 1024 ** 3
NAA_PREFIX = "naa.600a0980"
MAX_PAGES = 50
EMS_HOURS = 24


def naa_of(serial: Optional[str]) -> Optional[str]:
    return f"{NAA_PREFIX}{serial.encode().hex()}" if serial else None


def _gb(v: Any) -> Optional[float]:
    try:
        return round(float(v) / GB, 2) if v is not None else None
    except (TypeError, ValueError):
        return None


def _get(d: Dict[str, Any], *path, default=None):
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
    return default if cur is None else cur


class OntapClient:
    def __init__(self, cfg: StorageConfig):
        if not (cfg.username and cfg.password):
            raise StorageError("Chưa nhập user + mật khẩu của tài khoản ONTAP role readonly.")
        self.http = JsonClient(cfg, f"https://{cfg.host}:{cfg.port}")
        token = base64.b64encode(f"{cfg.username}:{cfg.password}".encode()).decode()
        self.http.headers["Authorization"] = f"Basic {token}"

    def records(self, path: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        res = self.http.get(path, {"max_records": 1000, **(params or {})})
        for _ in range(MAX_PAGES):
            out.extend(res.get("records") or [])
            nxt = _get(res, "_links", "next", "href")
            if not nxt or not str(nxt).startswith("/api/"):
                break
            res = self.http.get(nxt)
        return out


def parse(cluster, nodes, aggregates, volumes, luns, lifs, disks, events) -> Dict[str, Any]:
    cap = sum(_get(a, "space", "block_storage", "size", default=0) for a in aggregates or [])
    used = sum(_get(a, "space", "block_storage", "used", default=0) for a in aggregates or [])
    ratios = [(_get(a, "space", "block_storage", "used", default=0), _get(a, "space", "efficiency", "ratio")) for a in aggregates or []]
    ratios = [(u, r) for u, r in ratios if isinstance(r, (int, float)) and u]
    dr = round(sum(u * r for u, r in ratios) / sum(u for u, _ in ratios), 2) if ratios else None
    prov = sum(_get(v, "space", "size", default=0) for v in volumes or [])
    ctl, issues = [], []
    for n in nodes or []:
        ctl.append({"name": n.get("name"), "model": n.get("model"), "status": n.get("state"), "mode": "ha", "version": None})
        if n.get("state") not in ("up", None):
            issues.append({"name": n.get("name"), "type": "node", "status": n.get("state"), "details": "Node không ở trạng thái up"})
        for key, label in (("failed_fan", "quạt"), ("failed_power_supply", "nguồn")):
            cnt = _get(n, "controller", key, "count", default=0)
            if cnt:
                issues.append({"name": n.get("name"), "type": key, "status": "failed",
                               "details": f"{cnt} {label} hỏng — {_get(n, 'controller', key, 'message', 'message', default='')}".strip(" —")})
        if _get(n, "controller", "over_temperature") == "over":
            issues.append({"name": n.get("name"), "type": "temperature", "status": "over", "details": "Nhiệt độ vượt ngưỡng"})
    for a in aggregates or []:
        if a.get("state") not in ("online", None):
            issues.append({"name": a.get("name"), "type": "aggregate", "status": a.get("state"), "details": "Aggregate không online"})
    for d in disks or []:
        issues.append({"name": d.get("name"), "type": "disk", "status": d.get("state") or "broken",
                       "details": f"Node {_get(d, 'node', 'name', default='—')}"})
    sev = {"emergency": "critical", "alert": "critical", "error": "warning"}
    alerts = []
    for e in events or []:
        s = sev.get(_get(e, "message", "severity"))
        if s:
            alerts.append({"id": str(e.get("index") or f"{_get(e, 'node', 'name')}-{e.get('time')}"), "severity": s,
                           "summary": (e.get("log_message") or _get(e, "message", "name") or "")[:300],
                           "component": f"{_get(e, 'node', 'name', default='')} {_get(e, 'message', 'name', default='')}".strip(),
                           "at": e.get("time")})
    vols = [{"name": v.get("name"), "container": _get(v, "svm", "name", default=""), "kind": "volume",
             "sizeGB": _gb(_get(v, "space", "size")), "usedGB": _gb(_get(v, "space", "used")), "physicalGB": None,
             "naa": None, "nfsPath": _get(v, "nas", "path")} for v in volumes or [] if v.get("state") in ("online", None)]
    vols += [{"name": l.get("name"), "container": _get(l, "svm", "name", default=""), "kind": "lun",
              "sizeGB": _gb(_get(l, "space", "size")), "usedGB": _gb(_get(l, "space", "used")), "physicalGB": None,
              "naa": naa_of(l.get("serial_number")), "nfsPath": None} for l in luns or []]
    lat = lambda us: round(us / 1000, 2) if isinstance(us, (int, float)) else None   # noqa: E731
    m = (cluster or {}).get("metric") or {}
    return {
        "array": {"vendor": "netapp", "name": (cluster or {}).get("name"), "model": next((c["model"] for c in ctl if c.get("model")), None),
                  "version": _get(cluster or {}, "version", "full"), "capacityGB": _gb(cap), "usedGB": _gb(used),
                  "provisionedGB": _gb(prov), "dataReduction": dr,
                  "latencyReadMs": lat(_get(m, "latency", "read")), "latencyWriteMs": lat(_get(m, "latency", "write")),
                  "iops": _get(m, "iops", "total"),
                  "throughputMBps": round(_get(m, "throughput", "total", default=0) / 1024 ** 2, 1) if _get(m, "throughput", "total") is not None else None,
                  "controllers": ctl, "hardwareIssues": issues},
        "volumes": vols, "alerts": alerts,
        "nfsEndpoints": [{"svm": _get(i, "svm", "name", default=""), "ip": _get(i, "ip", "address")} for i in lifs or [] if _get(i, "ip", "address")],
    }


def collect(cfg: StorageConfig, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    c = OntapClient(cfg)
    since = ((now or dt.datetime.utcnow()) - dt.timedelta(hours=EMS_HOURS)).replace(microsecond=0).isoformat() + "Z"
    return parse(
        c.http.get("/api/cluster", {"fields": "name,version,metric"}),
        c.records("/api/cluster/nodes", {"fields": "name,model,state,serial_number,controller.failed_fan,controller.failed_power_supply,controller.over_temperature"}),
        c.records("/api/storage/aggregates", {"fields": "name,state,space,node.name"}),
        c.records("/api/storage/volumes", {"fields": "name,svm.name,space.size,space.used,nas.path,state", "type": "rw"}),
        c.records("/api/storage/luns", {"fields": "name,svm.name,serial_number,space.size,space.used"}),
        c.records("/api/network/ip/interfaces", {"fields": "ip.address,svm.name", "services": "data_nfs"}),
        c.records("/api/storage/disks", {"fields": "name,state,node.name", "state": "broken"}),
        c.records("/api/support/ems/events", {"fields": "index,time,message.name,message.severity,log_message,node.name",
                                              "message.severity": "emergency|alert|error", "time": f">{since}", "max_records": 200}),
    )


def test(cfg: StorageConfig) -> str:
    cl = OntapClient(cfg).http.get("/api/cluster", {"fields": "name,version"})
    return f"Kết nối thành công: cụm ONTAP {cl.get('name') or cfg.host} ({_get(cl, 'version', 'full', default='—')})."
