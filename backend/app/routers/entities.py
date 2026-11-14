"""Super app SA3 — tìm kiếm chung (Ctrl + K) and entity pages (thẻ Host / VM).

One object, one page: a host or a VM gathers what every module knows about it
(capacity, hardware / ESXi, guest OS, backup, compliance, Opex warranty devices).
Every part is filtered by the caller's features and roles; reads only."""
import math
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import capacity, data_access
from ..config import get_settings
from ..database import get_db
from ..features import get_features
from ..models import User
from ..security import get_current_user
from ..spend.roles import has_role

router = APIRouter(prefix="/api", tags=["entities"])
PER_TYPE = 8


def _num(v):
    return v if isinstance(v, (int, float)) and math.isfinite(v) else None


def _like(q: str) -> str:
    return "%" + q.replace("\\", " ").replace("%", " ").replace("_", " ") + "%"


def _spend_ok(user: User, features: Dict[str, bool]) -> bool:
    return features.get("spend") is not False and has_role(user, "viewer")


# ================================================================ search
@router.get("/search")
def search(q: str = Query(min_length=2, max_length=80), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Hosts, VMs (name / IP / guest hostname), backup clients and — with a spend role — every
    business code (OPX / CPX / HD / BG / CTVP / TT) and vendors. At most 8 hits per type."""
    term = " ".join(q.split())
    t = term.lower()
    if len(t) < 2:
        raise HTTPException(400, "Nhập ít nhất 2 ký tự.")
    features = get_features(db)
    out: List[Dict[str, Any]] = []
    hosts, vms, _ = data_access.get_latest_combined(db, None)
    for h in hosts:
        if t in (h.get("host") or "").lower():
            out.append({"type": "host", "label": h["host"], "sub": f"Host · cluster {h.get('cluster') or '—'} · {h.get('vcenterKey') or 'import'}",
                        "entity": {"type": "host", "name": h["host"], "vcenterKey": h.get("vcenterKey") or ""}})
            if sum(1 for x in out if x["type"] == "host") >= PER_TYPE:
                break
    n = 0
    for v in vms:
        hay = " ".join(str(v.get(k) or "") for k in ("vm", "ipAddress", "guestHostname")).lower()
        if t in hay:
            out.append({"type": "vm", "label": v["vm"], "sub": f"VM · {v.get('cluster') or '—'} · {v.get('ipAddress') or 'không có IP'}",
                        "entity": {"type": "vm", "name": v["vm"], "vcenterKey": v.get("vcenterKey") or ""}})
            n += 1
            if n >= PER_TYPE:
                break
    if features.get("backup") is not False:
        from ..models import BackupJob
        names = (db.query(BackupJob.client_name).filter(func.lower(BackupJob.client_name).like(_like(t)))
                 .distinct().limit(PER_TYPE).all())
        known_vms = {(v.get("vm") or "").lower() for v in vms}
        for (c,) in names:
            if c and c.lower() not in known_vms:
                out.append({"type": "client", "label": c, "sub": "Client backup", "page": "bk-clients"})
    if _spend_ok(user, features):
        out.extend(_spend_hits(db, t))
    return {"q": term, "items": out[:60]}


def _spend_hits(db: Session, t: str) -> List[Dict[str, Any]]:
    from ..spend import service
    from ..spend.models import CapexProject, Contract, OpexItem, Proposal, QuoteRequest, RenovationProject, SpendVendor
    like = _like(t)
    hits: List[Dict[str, Any]] = []

    def add(rows, typ, page, label, sub, query=None):
        for r in rows:
            hits.append({"type": typ, "label": label(r), "sub": sub(r), "page": page, "query": query(r) if query else ""})

    add(db.query(OpexItem).filter(or_(func.lower(OpexItem.code).like(like), func.lower(OpexItem.title).like(like))).limit(PER_TYPE),
        "opex", "spend-opex", lambda r: f"{r.code} — {r.title}", lambda r: f"Opex · hết hạn {service.date_vn(r.end_date)}",
        lambda r: f"code={r.code}")
    add(db.query(CapexProject).filter(or_(func.lower(CapexProject.code).like(like), func.lower(CapexProject.name).like(like))).limit(PER_TYPE),
        "capex", "spend-capex", lambda r: f"{r.code} — {r.name}", lambda r: f"Capex · NTC {r.fiscal_year}", lambda r: f"code={r.code}")
    add(db.query(Contract).filter(or_(func.lower(Contract.code).like(like), func.lower(Contract.title).like(like))).limit(PER_TYPE),
        "contract", "spend-contracts", lambda r: f"{r.code} — {r.title}", lambda r: "Hợp đồng", lambda r: f"code={r.code}")
    add(db.query(QuoteRequest).filter(or_(func.lower(QuoteRequest.code).like(like), func.lower(QuoteRequest.title).like(like))).limit(PER_TYPE),
        "quote", "spend-quotes", lambda r: f"{r.code} — {r.title}", lambda r: "Gói báo giá", lambda r: f"code={r.code}")
    add(db.query(RenovationProject).filter(or_(func.lower(RenovationProject.code).like(like), func.lower(RenovationProject.name).like(like),
                                               func.lower(RenovationProject.floor).like(like))).limit(PER_TYPE),
        "renovation", "spend-renovations", lambda r: f"{r.floor} ({r.year}) — {r.name}", lambda r: f"Cải tạo văn phòng · {r.code}",
        lambda r: f"code={r.code}")
    add(db.query(Proposal).filter(or_(func.lower(Proposal.code).like(like), func.lower(Proposal.subject).like(like))).limit(PER_TYPE),
        "proposal", "spend-proposals", lambda r: f"{r.code} — {r.subject}", lambda r: "Tờ trình", lambda r: f"code={r.code}")
    vendors = (db.query(SpendVendor).filter(or_(func.lower(SpendVendor.name).like(like), func.lower(SpendVendor.short_name).like(like),
                                                func.lower(SpendVendor.aliases).like(like))).limit(PER_TYPE).all())
    for v in vendors:
        hits.append({"type": "vendor", "label": service.vendor_label(v), "sub": f"Nhà cung cấp · {v.name}", "page": "spend-vendors",
                     "query": f"vendor={v.id}"})
    return hits


# ================================================================ entity pages
def _find(rows: List[Dict[str, Any]], field: str, name: str, vcenter_key: str) -> Optional[Dict[str, Any]]:
    cands = [r for r in rows if (r.get(field) or "") == name]
    if vcenter_key:
        cands = [r for r in cands if (r.get("vcenterKey") or "") == vcenter_key] or cands
    return cands[0] if cands else None


def _status(s: Dict[str, str]) -> Dict[str, str]:
    return {"text": s.get("text"), "cls": s.get("cls")}


def _findings_for(hosts, vms, policy, entity: str) -> List[Dict[str, Any]]:
    fs = capacity.assign_finding_codes(capacity.build_compliance_findings(hosts, vms, policy))
    return [{"code": f.get("code"), "severity": f["severity"], "typeLabel": f["typeLabel"], "metric": f.get("metric"),
             "recommendation": f.get("recommendation")} for f in fs if f.get("entity") == entity]


def _devices_for_host(db: Session, host: str) -> List[Dict[str, Any]]:
    from ..spend import service
    from ..spend.models import OpexAsset, OpexItem
    short = host.split(".")[0].lower()
    rows = (db.query(OpexAsset, OpexItem).join(OpexItem, OpexItem.id == OpexAsset.opex_id)
            .filter(or_(func.lower(OpexAsset.host_name) == host.lower(), func.lower(OpexAsset.host_name).like(short + ".%"),
                        func.lower(OpexAsset.host_name) == short))
            .order_by(OpexItem.end_date.desc()).limit(50).all())
    return [{"code": o.code, "title": o.title, "serial": a.serial, "endDate": o.end_date.isoformat(), "status": o.status,
             "statusLabel": service.STATUS_LABELS.get(o.status, o.status)} for a, o in rows]


@router.get("/entities/host")
def host_card(name: str = Query(min_length=1, max_length=255), vcenter: str = Query(default="", max_length=64),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    hosts, vms, _ = data_access.get_latest_combined(db, None)
    h = _find(hosts, "host", name, vcenter)
    if not h:
        raise HTTPException(404, f"Không có host “{name}” trong dữ liệu mới nhất.")
    policy = data_access.get_effective_policy(db, get_settings())
    row = next((r for r in capacity.build_host_rows([h], vms, policy)), {})
    cluster = next((c for c in capacity.build_cluster_rows([x for x in hosts if x.get("cluster") == h.get("cluster")],
                                                           [v for v in vms if v.get("cluster") == h.get("cluster")], policy)), None)
    on_host = sorted((v for v in vms if v.get("host") == h["host"] and (v.get("vcenterKey") or "") == (h.get("vcenterKey") or "")),
                     key=lambda v: (not v.get("isPoweredOn"), v.get("vm") or ""))
    features = get_features(db)
    out = {
        "type": "host", "name": h["host"], "vcenterKey": h.get("vcenterKey") or "", "cluster": h.get("cluster"),
        "hardware": {k: h.get(k) for k in ("vendor", "model", "cpuModel", "cpuSockets", "cores", "cpuThreads", "ramGB",
                                           "esxiVersion", "esxiBuild", "status")},
        "capacity": {"vcpuUsed": row.get("vcpuUsed"), "ramUsedGB": row.get("ramUsedGB"), "cpuPct": _num(row.get("cpuPct")),
                     "ramPct": _num(row.get("ramPct")), "vmCount": row.get("vmCount"),
                     "overall": _status(row["overall"]) if row.get("overall") else None},
        "clusterStatus": {"name": cluster["name"], "hosts": cluster["hosts"], "vms": cluster["vms"],
                          "cpuPct": _num(cluster["model"]["cpuPct"]), "ramPct": _num(cluster["model"]["ramPct"]),
                          "overall": _status(cluster["overall"]), "ha": h.get("clusterHa"), "drs": h.get("clusterDrs")} if cluster else None,
        "vms": [{"name": v["vm"], "powerOn": bool(v.get("isPoweredOn")), "vcpu": v.get("vcpu"), "ramGB": v.get("ramGB"),
                 "ip": v.get("ipAddress")} for v in on_host[:300]],
        "findings": _findings_for(hosts, vms, policy, h["host"]),
        "devices": _devices_for_host(db, h["host"]) if _spend_ok(user, features) else None,
    }
    return out


@router.get("/entities/vm")
def vm_card(name: str = Query(min_length=1, max_length=255), vcenter: str = Query(default="", max_length=64),
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from ..licensing import os_classify
    from .licensing import _overrides_map
    hosts, vms, _ = data_access.get_latest_combined(db, None)
    v = _find(vms, "vm", name, vcenter)
    if not v:
        raise HTTPException(404, f"Không có VM “{name}” trong dữ liệu mới nhất.")
    policy = data_access.get_effective_policy(db, get_settings())
    features = get_features(db)
    ov = _overrides_map(db).get(os_classify.override_key(v.get("vcenterKey"), v.get("instanceUuid"), v.get("vm")))
    os_ = os_classify.classify_vm(v, ov)
    out = {
        "type": "vm", "name": v["vm"], "vcenterKey": v.get("vcenterKey") or "", "cluster": v.get("cluster"), "host": v.get("host"),
        "config": {k: v.get(k) for k in ("powerState", "isPoweredOn", "vcpu", "ramGB", "diskProvGB", "diskUsedGB", "datastore",
                                         "ipAddress", "guestHostname", "toolsStatus", "lastActivityDays", "powerOffDays")},
        "os": {"category": os_.get("category"), "label": os_.get("categoryLabel"), "version": os_.get("osVersion"),
               "edition": os_.get("osEdition"), "source": os_.get("source"), "overridden": bool(os_.get("overridden"))},
        "findings": _findings_for(hosts, vms, policy, v["vm"]),
        "backup": _backup_for_vm(db, v) if features.get("backup") is not False else None,
    }
    return out


def _backup_for_vm(db: Session, v: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not get_settings().netbackups():
        return None
    from ..backup.client import client_key
    from .backup import build_summary
    rep = build_summary(db, "all", 7)
    keys = {k for k in (client_key(v.get("vm")), client_key(v.get("guestHostname")), v.get("ipAddress")) if k}
    client = next((c for c in rep.get("clients") or [] if c.get("key") in keys), None)
    cov = rep.get("coverage") or {}
    state = None
    for st, lst in (("unprotected", cov.get("unprotectedVms") or []), ("failing", cov.get("failingVms") or [])):
        if any(r.get("vm") == v.get("vm") and (r.get("vcenterKey") or "") == (v.get("vcenterKey") or "") for r in lst):
            state = st
    if state is None and cov.get("available") and v.get("isPoweredOn"):
        state = "protected" if client else None
    return {"coverage": state, "client": {k: client.get(k) for k in ("client", "policies", "lastSuccessAt", "hoursSinceSuccess",
                                                                    "rpoHours", "consecutiveFail", "status", "statusLabel",
                                                                    "lastStatusText", "note")} if client else None}
