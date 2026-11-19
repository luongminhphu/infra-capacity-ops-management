"""Search (Ctrl + K) and entity cards (thẻ Host / VM) as a service: used by routers/entities.py
and by Hỏi ICO's read-only tools, always with the calling user's features and roles.

One object, one page: a host or a VM gathers what every module knows about it
(capacity, hardware / ESXi, guest OS, backup, compliance, Opex warranty devices).
Every part is filtered by the caller's features and roles; reads only."""
import datetime as dt
import math
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import capacity, data_access
from ..config import get_settings
from ..features import get_features, is_on
from ..cmdb import query as cq
from ..models import User
from ..shared.identity import norm_serial
from ..spend.roles import has_role

PER_TYPE = 8
CACHE_SECONDS = 30
_cache: Dict[str, Any] = {}
_lock = threading.Lock()


def _cached(name: str, ttl: float, fn):
    """Small process cache: the latest snapshot and the backup report are read once per `ttl`
    seconds whatever the number of searches / entity pages (one per keystroke)."""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(name)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[name] = (time.monotonic(), val)
    return val


def _latest(db: Session):
    return _cached("latest", CACHE_SECONDS, lambda: data_access.get_latest_combined(db, None)[:2])


CLIENT_WINDOW_DAYS = 35      # covers monthly full backups
CLIENT_CACHE_SECONDS = 60
MAX_CLIENTS = 20000


def _backup_clients(db: Session) -> List[str]:
    """Distinct backup client names seen in the last CLIENT_WINDOW_DAYS, cached CLIENT_CACHE_SECONDS.

    Ctrl + K searches on every keystroke; matching `lower(client_name) LIKE '%q%'` + DISTINCT on
    backup_jobs scanned the whole table (400 days of jobs) each time (audit QRY-02). Instead one
    query per minute reads the recent window through the start_time index, and keystrokes filter
    this small in-memory list. A client with no job in the window is not an active client and is
    still reachable from the Backup → Clients page. No pg_trgm index: it would be PostgreSQL-only."""
    from ..models import BackupJob

    def load() -> List[str]:
        since = dt.datetime.utcnow() - dt.timedelta(days=CLIENT_WINDOW_DAYS)
        rows = (db.query(BackupJob.client_name)
                .filter(BackupJob.start_time >= since, BackupJob.client_name.isnot(None))
                .distinct().limit(MAX_CLIENTS).all())
        return sorted({c for (c,) in rows if c}, key=str.lower)

    return _cached("backup_clients", CLIENT_CACHE_SECONDS, load)


def _num(v):
    return v if isinstance(v, (int, float)) and math.isfinite(v) else None


def _like(q: str) -> str:
    return "%" + q.replace("\\", " ").replace("%", " ").replace("_", " ") + "%"


def _spend_ok(user: User, features: Dict[str, bool]) -> bool:
    return is_on(features, "spend") and has_role(user, "viewer")


# ================================================================ search
def search(db: Session, user: User, q: str) -> Dict[str, Any]:
    """Hosts, VMs (name / IP / guest hostname), backup clients and — with a spend role — every
    business code (OPX / CPX / HD / BG / CTVP / TT) and vendors. At most 8 hits per type."""
    term = " ".join(q.split())
    t = term.lower()
    if len(t) < 2:
        raise HTTPException(400, "Nhập ít nhất 2 ký tự.")
    features = get_features(db)
    out: List[Dict[str, Any]] = []
    hosts, vms = _latest(db)
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
    if is_on(features, "backup"):
        known_vms = {(v.get("vm") or "").lower() for v in vms}
        n = 0
        for c in _backup_clients(db):
            if t in c.lower() and c.lower() not in known_vms:
                out.append({"type": "client", "label": c, "sub": "Client backup", "page": "bk-clients"})
                n += 1
                if n >= PER_TYPE:
                    break
    out.extend(_ci_hits(db, term))
    if _spend_ok(user, features):
        out.extend(_spend_hits(db, t))
    return {"q": term, "items": out[:60]}


def _ci_hits(db: Session, term: str) -> List[Dict[str, Any]]:
    """Physical servers by serial / asset tag (prefix, through the CI registry) and datastores by name."""
    from ..cmdb.models import Ci, CiKey, CiLink
    hits: List[Dict[str, Any]] = []
    sk = norm_serial(term)
    if len(sk) >= 3:
        rows = (db.query(Ci).join(CiKey, CiKey.ci_id == Ci.id)
                .filter(CiKey.kind.in_(("serial", "asset_tag")), CiKey.value_norm.like(sk + "%"),
                        Ci.type.in_(("physical_server", "dc_asset")), Ci.status == "active")
                .distinct().limit(PER_TYPE * 2).all())
        servers: Dict[int, Any] = {c.id: c for c in rows if c.type == "physical_server"}
        assets = [c.id for c in rows if c.type == "dc_asset"]
        if assets:                                   # an asset tag → the server it records
            for c in (db.query(Ci).join(CiLink, CiLink.to_ci == Ci.id)
                      .filter(CiLink.from_ci.in_(assets), CiLink.relation == "records", Ci.status == "active")):
                servers.setdefault(c.id, c)
        for c in sorted(servers.values(), key=lambda c: c.name)[:PER_TYPE]:
            a = cq.attrs_of(c)
            model = " ".join(x for x in (a.get("vendor"), a.get("model")) if x) or "—"
            hits.append({"type": "server", "label": c.name, "sub": f"Máy chủ vật lý · {model}",
                         "entity": {"type": "server", "name": c.name, "vcenterKey": ""}})
    for c in (db.query(Ci).filter(Ci.type == "datastore", Ci.status == "active", func.lower(Ci.name).like(_like(term.lower())))
              .order_by(Ci.name).limit(PER_TYPE)):
        a = cq.attrs_of(c)
        hits.append({"type": "datastore", "label": c.name, "sub": f"Datastore · {a.get('dsType') or '—'} · {a.get('vcenterKey') or '—'}",
                     "entity": {"type": "datastore", "name": c.name, "vcenterKey": a.get("vcenterKey") or "", "id": c.id}})
    return hits


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
        "capex", "spend-capex", lambda r: f"{r.code} — {r.name}", lambda r: f"Capex · FY {r.fiscal_year}", lambda r: f"code={r.code}")
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


def _opex_rows(rows) -> List[Dict[str, Any]]:
    from ..spend import service
    return [{"code": o.code, "title": o.title, "serial": a.serial, "endDate": o.end_date.isoformat(), "status": o.status,
             "statusLabel": service.STATUS_LABELS.get(o.status, o.status)} for a, o in rows]


def _opex_for_server(db: Session, server) -> List[Dict[str, Any]]:
    """Opex items that cover a physical server: incoming strong "covers" links of the CI registry."""
    from ..cmdb.models import Ci, CiLink
    from ..spend.models import OpexAsset, OpexItem
    codes = [code for (code,) in db.query(Ci.source_key).join(CiLink, CiLink.from_ci == Ci.id)
             .filter(CiLink.to_ci == server.id, CiLink.relation == "covers", CiLink.confidence == "strong", Ci.type == "opex_item")]
    if not codes:
        return []
    rows = (db.query(OpexAsset, OpexItem).join(OpexItem, OpexItem.id == OpexAsset.opex_id)
            .filter(OpexItem.code.in_(codes)).order_by(OpexItem.end_date.desc()).limit(200).all())
    return [dict(r, matchedBy="serial") for r in _opex_rows([(a, o) for a, o in rows if norm_serial(a.serial) == server.source_key])][:50]


def _devices_for_host(db: Session, host: str, serial: Optional[str] = None) -> List[Dict[str, Any]]:
    """Warranty / contract lines of a host: through its physical server (same serial, strong) when the host reports
    a serial known to the CI registry; otherwise by host name (labelled "ghép theo tên")."""
    from ..spend.models import OpexAsset, OpexItem
    sk = norm_serial(serial)
    server = cq.find(db, "physical_server", sk) if sk else None
    if server is not None:
        return _opex_for_server(db, server)
    short = host.split(".")[0].lower()
    rows = (db.query(OpexAsset, OpexItem).join(OpexItem, OpexItem.id == OpexAsset.opex_id)
            .filter(or_(func.lower(OpexAsset.host_name) == host.lower(),
                        func.lower(OpexAsset.host_name).startswith(short + ".", autoescape=True),
                        func.lower(OpexAsset.host_name) == short))
            .order_by(OpexItem.end_date.desc()).limit(50).all())
    return [dict(r, matchedBy="name") for r in _opex_rows(rows)]


def host_card(db: Session, user: User, name: str, vcenter: str = "") -> Dict[str, Any]:
    hosts, vms = _latest(db)
    h = _find(hosts, "host", name, vcenter)
    if not h:
        raise HTTPException(404, f"Không có host “{name}” trong dữ liệu mới nhất.")
    policy = data_access.get_effective_policy(db, get_settings())
    row = next((r for r in capacity.build_host_rows([h], vms, policy)), {})
    cluster = next((c for c in capacity.build_cluster_rows([x for x in hosts if x.get("cluster") == h.get("cluster")],
                                                           [v for v in vms if v.get("cluster") == h.get("cluster")], policy)), None)
    features = get_features(db)
    on_host = sorted((v for v in vms if v.get("host") == h["host"] and (v.get("vcenterKey") or "") == (h.get("vcenterKey") or "")),
                     key=lambda v: (not v.get("isPoweredOn"), v.get("vm") or ""))
    out = {
        "type": "host", "name": h["host"], "vcenterKey": h.get("vcenterKey") or "", "cluster": h.get("cluster"),
        "hardware": {k: h.get(k) for k in ("vendor", "model", "cpuModel", "cpuSockets", "cores", "cpuThreads", "ramGB", "status",
                                           "serial", "hwUuid", *(("esxiVersion", "esxiBuild") if is_on(features, "license") else ()))},
        "capacity": {"vcpuUsed": row.get("vcpuUsed"), "ramUsedGB": row.get("ramUsedGB"), "cpuPct": _num(row.get("cpuPct")),
                     "ramPct": _num(row.get("ramPct")), "vmCount": row.get("vmCount"),
                     "overall": _status(row["overall"]) if row.get("overall") else None},
        "clusterStatus": {"name": cluster["name"], "hosts": cluster["hosts"], "vms": cluster["vms"],
                          "cpuPct": _num(cluster["model"]["cpuPct"]), "ramPct": _num(cluster["model"]["ramPct"]),
                          "overall": _status(cluster["overall"]), "ha": h.get("clusterHa"), "drs": h.get("clusterDrs")} if cluster else None,
        "vms": [{"name": v["vm"], "powerOn": bool(v.get("isPoweredOn")), "vcpu": v.get("vcpu"), "ramGB": v.get("ramGB"),
                 "ip": v.get("ipAddress")} for v in on_host[:300]],
        "findings": _findings_for(hosts, vms, policy, h["host"]),
        "devices": _devices_for_host(db, h["host"], h.get("serial")) if _spend_ok(user, features) else None,
        "server": _server_ref(db, h.get("serial")),
        "ci": cq.card_extras(db, user, cq.find(db, "hypervisor_host", f"{h.get('vcenterKey') or ''}:{h['host']}")),
    }
    return out


def _server_ref(db: Session, serial: Optional[str]) -> Optional[Dict[str, Any]]:
    sk = norm_serial(serial)
    c = cq.find(db, "physical_server", sk) if sk else None
    return {"serial": c.name, "id": c.id} if c is not None else None


def _vm_ci(db: Session, v: Dict[str, Any]):
    uuid = str(v.get("instanceUuid") or "").strip().lower()
    vc = v.get("vcenterKey") or ""
    for key in ([uuid, f"{vc}:{uuid}"] if uuid else []) + [f"{vc}:{v.get('vm')}"]:
        c = cq.find(db, "vm", key)
        if c is not None and (cq.attrs_of(c).get("vcenterKey") or "") == vc:
            return c
    return None


def vm_card(db: Session, user: User, name: str, vcenter: str = "") -> Dict[str, Any]:
    from ..licensing import os_classify
    from ..licensing.inventory import _overrides_map
    hosts, vms = _latest(db)
    v = _find(vms, "vm", name, vcenter)
    if not v:
        raise HTTPException(404, f"Không có VM “{name}” trong dữ liệu mới nhất.")
    policy = data_access.get_effective_policy(db, get_settings())
    features = get_features(db)
    os_ = None
    if is_on(features, "license"):
        ov = _overrides_map(db).get(os_classify.override_key(v.get("vcenterKey"), v.get("instanceUuid"), v.get("vm")))
        os_ = os_classify.classify_vm(v, ov)
    out = {
        "type": "vm", "name": v["vm"], "vcenterKey": v.get("vcenterKey") or "", "cluster": v.get("cluster"), "host": v.get("host"),
        "config": {k: v.get(k) for k in ("powerState", "isPoweredOn", "vcpu", "ramGB", "diskProvGB", "diskUsedGB", "datastore",
                                         "ipAddress", "guestHostname", "toolsStatus", "lastActivityDays", "powerOffDays")},
        "os": {"category": os_.get("category"), "label": os_.get("categoryLabel"), "version": os_.get("osVersion"),
               "edition": os_.get("osEdition"), "source": os_.get("source"), "overridden": bool(os_.get("overridden"))} if os_ else None,
        "findings": _findings_for(hosts, vms, policy, v["vm"]),
        "backup": None,
        "ci": None,
    }
    ci = _vm_ci(db, v)
    if is_on(features, "backup"):
        out["backup"] = _backup_for_vm(db, v, ci)
    out["ci"] = cq.card_extras(db, user, ci)
    from ..owners import service as owners
    out["system"] = owners.system_of_vm(db, ci.id) if ci is not None else None
    out["canAssignSystem"] = ci is not None and owners.can_manage(user)
    return out


def _protected_by(db: Session, ci) -> Dict[str, str]:
    """nbu_client key → matchedBy of the VM's "protected_by" links (CI registry)."""
    import json
    from ..cmdb.models import Ci, CiKey, CiLink
    rows = (db.query(CiKey.value_norm, CiLink.attrs).join(CiLink, CiLink.to_ci == CiKey.ci_id).join(Ci, Ci.id == CiKey.ci_id)
            .filter(CiLink.from_ci == ci.id, CiLink.relation == "protected_by", CiKey.kind == "nbu_client", Ci.status == "active"))
    return {k: json.loads(a or "{}").get("matchedBy") or "" for k, a in rows}


def _backup_for_vm(db: Session, v: Dict[str, Any], ci=None) -> Optional[Dict[str, Any]]:
    if not get_settings().netbackups():
        return None
    from ..backup.client import client_key
    from ..backup.service import build_summary
    rep = _cached("backup", 60, lambda: build_summary(db, "all", 7))
    linked = _protected_by(db, ci) if ci is not None else {}
    # The registry link when there is one; before the first build, the same name / IP rule directly.
    keys = set(linked) or {k for k in (client_key(v.get("vm")), client_key(v.get("guestHostname")), v.get("ipAddress")) if k}
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
                                                                    "lastStatusText", "note")} if client else None,
            "matchedBy": linked.get(client.get("key")) if client and linked else None}


# ================================================================ physical server / datastore (CI registry, G1c)
def server_card(db: Session, user: User, serial: str) -> Dict[str, Any]:
    """Physical server by serial: model, DC asset rows, Opex warranty (spend role), ESXi hosts running on it."""
    from ..cmdb.models import Ci, CiLink
    from ..models import DcAsset
    sk = norm_serial(serial)
    c = cq.find(db, "physical_server", sk) if sk else None
    if c is None:
        raise HTTPException(404, f"Không có máy chủ vật lý serial “{serial[:64]}” trong sổ CI.")
    a = cq.attrs_of(c)
    peers = (db.query(Ci, CiLink.relation).join(CiLink, CiLink.from_ci == Ci.id)
             .filter(CiLink.to_ci == c.id, CiLink.relation.in_(("runs_on", "records")), Ci.status == "active").all())
    hosts = [{"name": p.name, "vcenterKey": cq.attrs_of(p).get("vcenterKey") or "", "cluster": cq.attrs_of(p).get("cluster"),
              "platform": cq.attrs_of(p).get("platform")} for p, rel in peers if rel == "runs_on"]
    keys = [p.source_key for p, rel in peers if rel == "records"]
    assets = []
    if keys:
        for d in db.query(DcAsset).filter(DcAsset.asset_key.in_(keys)).order_by(DcAsset.device_id):
            assets.append({"id": d.id, "deviceId": d.device_id, "category": d.category, "brand": d.brand, "model": d.model,
                           "serial": d.serial, "assetTag": d.asset_tag, "location": d.location, "env": d.env,
                           "acquiredOn": d.acquired_on.isoformat() if d.acquired_on else None})
    return {"type": "server", "name": c.name, "serial": c.name, "status": c.status, "vendor": a.get("vendor"), "model": a.get("model"),
            "origins": a.get("origins") or [], "hosts": sorted(hosts, key=lambda h: (h["vcenterKey"], h["name"])), "dcAssets": assets,
            "opex": _opex_for_server(db, c) if _spend_ok(user, get_features(db)) else None,
            "ci": cq.card_extras(db, user, c)}


def datastore_card(db: Session, user: User, ci_id: int) -> Dict[str, Any]:
    """Datastore by CI id: capacity (latest state), the array volume behind it, VMs on it, impact."""
    from ..cmdb.models import Ci, CiLink
    from ..models import DatastoreState
    c = cq.visible_ci(db, user, ci_id)
    if c.type != "datastore":
        raise HTTPException(404, "CI này không phải datastore.")
    a = cq.attrs_of(c)
    vc = a.get("vcenterKey") or ""
    st = db.query(DatastoreState).filter(DatastoreState.vcenter_key == vc,
                                         DatastoreState.ds_key == c.source_key[len(vc) + 1:]).first()
    vol = (db.query(Ci, CiLink.confidence, CiLink.attrs).join(CiLink, CiLink.to_ci == Ci.id)
           .filter(CiLink.from_ci == c.id, CiLink.relation == "backed_by").first())
    array = None
    if vol is not None:
        array = (db.query(Ci).join(CiLink, CiLink.to_ci == Ci.id)
                 .filter(CiLink.from_ci == vol[0].id, CiLink.relation == "on").first())
    vms = (db.query(Ci).join(CiLink, CiLink.from_ci == Ci.id)
           .filter(CiLink.to_ci == c.id, CiLink.relation == "stored_on", Ci.status == "active").order_by(Ci.name).limit(500).all())
    cap = st.capacity_gb if st else None
    used_pct = round((cap - st.free_gb) / cap * 100, 1) if st and cap else None
    import json
    return {"type": "datastore", "id": c.id, "name": c.name, "vcenterKey": vc, "status": c.status, "dsType": a.get("dsType"),
            "capacity": {"capacityGB": cap, "freeGB": st.free_gb if st else None, "usedPct": used_pct,
                         "accessible": st.accessible if st else None, "clusters": json.loads(st.clusters or "[]") if st else []},
            "storage": {"volume": vol[0].name, "volumeId": vol[0].id, "confidence": vol[1],
                        "matchedBy": json.loads(vol[2] or "{}").get("matchedBy"),
                        "array": array.name if array else None, "arrayId": array.id if array else None,
                        "vendor": cq.attrs_of(array).get("vendor") if array else None} if vol is not None else None,
            "vms": [{"name": v.name, "vcenterKey": cq.attrs_of(v).get("vcenterKey") or "",
                     "powerOn": cq.attrs_of(v).get("powerOn")} for v in vms],
            "ci": cq.card_extras(db, user, c)}
