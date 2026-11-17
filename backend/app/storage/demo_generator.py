"""Demo storage arrays (DEMO mode, rule 2): Pure FlashArray//X20R3 or //RC20 (refurbished line) and NetApp AFF A300, with volumes /
LUNs / NFS exports that back the demo vCenter datastores (same correlation keys as real arrays), so the
datastore ↔ volume ↔ VM view works without hardware. Same shape as pure.parse / netapp.parse."""
import datetime as dt
import hashlib
import json
import random
import time
from typing import Any, Dict, List

from . import StorageConfig

DEMO_NFS_HOST = "10.20.0.11"


def _model(cfg: StorageConfig) -> str:
    if cfg.vendor == "netapp":
        return "AFF-A300"
    return "FA-RC20" if "rc20" in f"{cfg.key} {cfg.name}".lower() else "FA-X20R3"


def _datastores() -> List[Dict[str, Any]]:
    try:
        from ..database import SessionLocal
        from ..models import DatastoreState
        with SessionLocal() as db:
            return [{"name": r.name, "vc": r.vcenter_key, "cap": r.capacity_gb, "used": r.capacity_gb - r.free_gb,
                     "backing": json.loads(r.backing or "{}")} for r in db.query(DatastoreState)]
    except Exception:  # noqa: BLE001 — tables not ready: empty demo array
        return []


def collect(cfg: StorageConfig, peers: List[str]) -> Dict[str, Any]:
    """peers: keys of every demo source of the same vendor (Pure datastores are spread over them)."""
    rnd = random.Random(hashlib.sha1(cfg.key.encode()).hexdigest())
    drift = (time.time() / 86400.0) % 30
    now = dt.datetime.utcnow().replace(microsecond=0)
    peers = sorted(peers) or [cfg.key]
    vols, used, prov = [], 0.0, 0.0
    dss = _datastores()
    pure_naa = sorted(n for d in dss for n in (d["backing"].get("naa") or [])[:1] if n.startswith("naa.624a9370"))
    owner = {n: peers[i % len(peers)] for i, n in enumerate(pure_naa)}          # round robin over the demo FlashArrays
    seen = set()                                    # a LUN / export shared by several vCenters exists once on the array
    for d in dss:
        b = d["backing"]
        ident = (b.get("naa") or [None])[0] or b.get("nfsPath")
        if ident in seen:
            continue
        seen.add(ident)
        naa = (b.get("naa") or [None])[0]
        mine = False
        if cfg.vendor == "pure" and naa and naa.startswith("naa.624a9370"):
            mine = owner.get(naa) == cfg.key
            if mine:
                vols.append({"name": d["name"].lower(), "container": "", "kind": "volume", "sizeGB": round(d["cap"], 1),
                             "usedGB": round(d["used"], 1), "physicalGB": round(d["used"] / 3.2, 1), "naa": naa, "nfsPath": None})
        elif cfg.vendor == "netapp" and naa and naa.startswith("naa.600a0980"):
            mine = True
            vols.append({"name": f"/vol/{d['name'].lower()}_vol/{d['name'].lower()}.lun", "container": "svm_vmware", "kind": "lun",
                         "sizeGB": round(d["cap"], 1), "usedGB": round(d["used"], 1), "physicalGB": None, "naa": naa, "nfsPath": None})
        elif cfg.vendor == "netapp" and b.get("nfsPath"):
            mine = True
            vols.append({"name": b["nfsPath"].strip("/"), "container": "svm_vmware", "kind": "volume", "sizeGB": round(d["cap"], 1),
                         "usedGB": round(d["used"], 1), "physicalGB": None, "naa": None, "nfsPath": b["nfsPath"]})
        if mine:
            used += d["used"]
            prov += d["cap"]
    # a few volumes not presented to VMware (file shares, SQL physical servers…)
    for i in range(3):
        size = rnd.choice([2048, 4096, 8192])
        vols.append({"name": f"{'cifs' if cfg.vendor == 'netapp' else 'sql-phys'}-{i + 1:02d}", "container": "svm_files" if cfg.vendor == "netapp" else "",
                     "kind": "volume", "sizeGB": float(size), "usedGB": round(size * rnd.uniform(0.3, 0.7), 1), "physicalGB": None,
                     "naa": None, "nfsPath": None})
        used += vols[-1]["usedGB"] / (3.0 if cfg.vendor == "pure" else 1.4)
        prov += size
    model = _model(cfg)
    physical_used = used / (3.0 if cfg.vendor == "pure" else 1.4)
    capacity = max(20480.0, round(physical_used / (0.62 + drift * 0.003) / 1024) * 1024)
    ctl = ([{"name": "CT0", "model": model, "status": "ready", "mode": "primary", "version": "6.5.8"},
            {"name": "CT1", "model": model, "status": "ready", "mode": "secondary", "version": "6.5.8"}] if cfg.vendor == "pure"
           else [{"name": f"{cfg.key}-0{i}", "model": model, "status": "up", "mode": "ha", "version": None} for i in (1, 2)])
    issues, alerts = [], []
    if cfg.vendor == "netapp":
        issues.append({"name": f"{cfg.key}-02", "type": "failed_power_supply", "status": "failed", "details": "1 nguồn hỏng — PSU2 failed"})
        alerts.append({"id": "ems-1", "severity": "warning", "summary": "Power supply 2 on node is in a degraded state.",
                       "component": f"{cfg.key}-02 monitor.chassisPowerSupply.degraded", "at": (now - dt.timedelta(hours=3)).isoformat() + "Z"})
    elif "rc20" in model.lower():
        alerts.append({"id": "pure-77", "severity": "warning", "summary": "Array space utilisation above 75%",
                       "component": "array.capacity", "at": (now - dt.timedelta(hours=10)).isoformat() + "Z"})
    return {
        "array": {"vendor": cfg.vendor, "name": cfg.name, "model": model,
                  "version": "Purity//FA 6.5.8" if cfg.vendor == "pure" else "NetApp Release 9.11.1P13",
                  "capacityGB": capacity, "usedGB": round(physical_used, 1), "provisionedGB": round(prov, 1),
                  "dataReduction": 3.0 if cfg.vendor == "pure" else 1.4,
                  "latencyReadMs": round(rnd.uniform(0.3, 0.8) if cfg.vendor == "pure" else rnd.uniform(0.8, 2.5), 2),
                  "latencyWriteMs": round(rnd.uniform(0.2, 0.6) if cfg.vendor == "pure" else rnd.uniform(0.5, 1.8), 2),
                  "iops": rnd.randint(8000, 45000), "throughputMBps": round(rnd.uniform(300, 1800), 1),
                  "controllers": ctl, "hardwareIssues": issues},
        "volumes": vols, "alerts": alerts,
        "nfsEndpoints": [{"svm": "svm_vmware", "ip": DEMO_NFS_HOST}] if cfg.vendor == "netapp" else [],
    }
