"""Read side of the hardware data (ICO v2 G4c): the Phần cứng page (list → detail), the OME block of the physical
server card, and the numbers of the PDF / Excel "Sức khoẻ phần cứng" — one function each, so the page, the PDF and
the Excel can never disagree.

Overall level of a server = worst of: OME rollup status, a disk predicting failure (VƯỢT NGƯỠNG), a failed / degraded
part (its level), firmware behind the OME baseline (THEO DÕI). Hosts / systems / VMs come from the CI registry
(physical_server ← runs_on host ← hosted_on VM → system). Set-based queries only (no per-server loop over the DB).
"""
import datetime as dt
import ipaddress
import json
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..infra import LEVEL_TEXT, worst
from .models import (
    HwAlert, HwComponentHealth, HwDeviceInventory, HwDeviceState, HwDiskState, HwFirmwareState, HwPartState, HwSourceState,
    HwWarranty,
)

POWER_TEXT = {"on": "Đang bật", "off": "Đang tắt", "powering_on": "Đang bật lên", "powering_off": "Đang tắt", "unknown": "Không rõ"}
PART_KIND = {"raid": "RAID controller", "psu": "Bộ nguồn", "memory": "RAM"}
COMPLIANCE_TEXT = {"ok": "Đúng baseline", "upgrade": "Cũ hơn baseline", "downgrade": "Mới hơn baseline", "unknown": "Không rõ"}
LEVEL_RANK = {"crit": 0, "bad": 1, "warn": 2, "ok": 3}
MAX_VMS = 300


def idrac_url(ip: Optional[str]) -> Optional[str]:
    """https://<ip> only for a valid IP address (never a name or anything else coming from OME)."""
    try:
        a = ipaddress.ip_address(str(ip or "").strip())
    except ValueError:
        return None
    return f"https://[{a}]" if a.version == 6 else f"https://{a}"


def _keys() -> List[str]:
    from ..config import get_settings
    return [c.key for c in get_settings().hardware()]


def _iso(v) -> Optional[str]:
    if v is None:
        return None
    return v.isoformat() + ("Z" if isinstance(v, dt.datetime) else "")


def topology(db: Session, tags: List[str]) -> Dict[str, Dict[str, Any]]:
    """Service tag → {ciId, hosts, vms, systems} through the CI registry."""
    from ..cmdb.models import Ci, CiLink
    from ..owners.models import System, SystemMember
    out: Dict[str, Dict[str, Any]] = {}
    if not tags:
        return out
    phys = {}
    for cid, tag in db.query(Ci.id, Ci.source_key).filter(Ci.type == "physical_server", Ci.source_key.in_(tags)):
        phys[cid] = tag
        out[tag] = {"ciId": cid, "hosts": [], "vms": [], "systems": []}
    if not phys:
        return out
    host_tag: Dict[int, str] = {}
    rows =(db.query(Ci, CiLink.to_ci, CiLink.confidence).join(CiLink, CiLink.from_ci == Ci.id)
            .filter(CiLink.relation == "runs_on", CiLink.to_ci.in_(list(phys)), Ci.status == "active").all())
    for h, to, conf in rows:
        a = json.loads(h.attrs or "{}")
        host_tag[h.id] = phys[to]
        out[phys[to]]["hosts"].append({"name": h.name, "vcenterKey": a.get("vcenterKey") or "", "cluster": a.get("cluster"),
                                       "matchedBy": "serial" if conf == "strong" else "hostname"})
    vm_tag: Dict[int, str] = {}
    if host_tag:
        for v, to in (db.query(Ci, CiLink.to_ci).join(CiLink, CiLink.from_ci == Ci.id)
                      .filter(CiLink.relation == "hosted_on", CiLink.to_ci.in_(list(host_tag)), Ci.status == "active")):
            a = json.loads(v.attrs or "{}")
            tag = host_tag[to]
            vm_tag[v.id] = tag
            out[tag]["vms"].append({"ciId": v.id, "name": v.name, "vcenterKey": a.get("vcenterKey") or "", "powerOn": bool(a.get("powerOn")),
                                    "instanceUuid": v.source_key if ":" not in v.source_key else "", "system": None})
    if vm_tag:
        sysmap: Dict[int, Dict[str, Any]] = {}
        for vm_id, s in (db.query(SystemMember.ci_id, System).join(System, System.id == SystemMember.system_id)
                         .filter(SystemMember.ci_id.in_(list(vm_tag)))):
            sysmap[vm_id] = {"id": s.id, "code": s.code, "name": s.name, "criticality": s.criticality, "ownerEmail": s.owner_email}
        for tag, d in out.items():
            seen = {}
            for v in d["vms"]:
                s = sysmap.get(v["ciId"])
                if s:
                    v["system"] = s["code"]
                    seen[s["code"]] = s
            d["systems"] = sorted(seen.values(), key=lambda s: (s["criticality"], s["code"]))
    for d in out.values():
        d["hosts"].sort(key=lambda h: (h["vcenterKey"], h["name"]))
        d["vms"].sort(key=lambda v: (not v["powerOn"], v["name"]))
    return out


def _warranty(db: Session, devs: List[HwDeviceState], today: dt.date, opex_detail: bool) -> Dict[str, Dict[str, Any]]:
    from .. import dc_assets
    from . import warranty as w
    tags = [d.service_tag for d in devs if d.service_tag]
    dell = w.dell_coverage(db, tags)
    opex = w._opex(db, tags)
    crit = w.critical_systems(db, tags)
    out = {}
    for t in tags:
        cov = dc_assets.coverage(opex.get(t) or [], today)
        dl = dell.get(t)
        case = w.classify(dl, cov, crit.get(t), today)
        o = cov.get("item")
        out[t] = {"dellEnd": dl["end"].isoformat() if dl else None, "dellService": dl.get("level") if dl else None,
                  "daysLeft": (dl["end"] - today).days if dl else None,
                  "opexState": cov["state"], "opexEnd": o["end"].isoformat() if o else None,
                  "opexCode": o["code"] if (o and opex_detail) else None, "opexTitle": o["title"] if (o and opex_detail) else None,
                  "case": case, "caseLabel": w.CASES[case][1] if case else None, "caseLevel": w.CASES[case][2] if case else None,
                  "code": w.code(case, t) if case else None}
    return out


def listing(db: Session, today: dt.date, osc=None, status: Optional[str] = None, model: Optional[str] = None,
            warranty_days: Optional[int] = None, opex_detail: bool = False) -> Dict[str, Any]:
    keys = _keys()
    devs = db.query(HwDeviceState).filter(HwDeviceState.source_key.in_(keys or [""])) \
        .order_by(HwDeviceState.name, HwDeviceState.device_id).all()
    ids = [(d.source_key, d.device_id) for d in devs]
    issues: Dict[tuple, List[tuple]] = defaultdict(list)            # (source, device) → [(level, text)]
    for h in db.query(HwComponentHealth).filter(HwComponentHealth.source_key.in_(keys or [""]), HwComponentHealth.status != "ok"):
        issues[(h.source_key, h.device_id)].append((h.status, f"{h.subsystem}: {h.detail or LEVEL_TEXT[h.status].lower()}"))
    for p in db.query(HwPartState).filter(HwPartState.source_key.in_(keys or [""]), HwPartState.status.in_(("bad", "crit"))):
        issues[(p.source_key, p.device_id)].append((p.status, f"{PART_KIND.get(p.kind, p.kind)} {p.name} lỗi"))
    predictive: Dict[tuple, int] = defaultdict(int)
    for x in db.query(HwDiskState).filter(HwDiskState.source_key.in_(keys or [""]), HwDiskState.predictive_failure.is_(True)):
        predictive[(x.source_key, x.device_id)] += 1
    fw_total: Dict[tuple, int] = defaultdict(int)
    fw_behind: Dict[tuple, int] = defaultdict(int)
    for f in db.query(HwFirmwareState.source_key, HwFirmwareState.device_id, HwFirmwareState.compliance).filter(
            HwFirmwareState.source_key.in_(keys or [""]), HwFirmwareState.compliance.isnot(None)):
        fw_total[(f[0], f[1])] += 1
        if f[2] in ("upgrade", "downgrade"):
            fw_behind[(f[0], f[1])] += 1
    baseline = {k: v for k, v in db.query(HwSourceState.source_key, HwSourceState.baseline_configured)
                .filter(HwSourceState.source_key.in_(keys or [""]))}
    tags = sorted({d.service_tag for d in devs if d.service_tag})
    topo = topology(db, tags)
    war = _warranty(db, devs, today, opex_detail)
    resolved = osc.resolve(db) if osc is not None and osc.active else None
    from ..scope import in_vm_scope
    items, models = [], defaultdict(int)
    for d in devs:
        k = (d.source_key, d.device_id)
        t = topo.get(d.service_tag or "") or {"ciId": None, "hosts": [], "vms": [], "systems": []}
        if resolved is not None and not any(in_vm_scope(resolved, {"vcenterKey": v["vcenterKey"], "vm": v["name"],
                                                                    "instanceUuid": v["instanceUuid"]}) for v in t["vms"]):
            continue
        reasons = []
        level = d.status
        if d.status != "ok":
            reasons.append(d.status_reason and f"OME: {d.status_reason}" or f"Trạng thái OME {LEVEL_TEXT[d.status]}")
        for lv, txt in issues.get(k, []):
            level = worst(level, lv) if lv in LEVEL_RANK else level
            reasons.append(txt)
        if predictive.get(k):
            level = "crit"
            reasons.append(f"{predictive[k]} ổ đĩa dự báo hỏng")
        if fw_behind.get(k):
            level = worst(level, "warn")
            reasons.append(f"{fw_behind[k]} firmware lệch baseline")
        w = war.get(d.service_tag or "") or {}
        models[d.model or "—"] += 1
        items.append({
            "sourceKey": d.source_key, "deviceId": d.device_id, "serviceTag": d.service_tag, "name": d.name, "model": d.model,
            "idracIp": d.idrac_ip, "idracUrl": idrac_url(d.idrac_ip), "powerState": d.power_state,
            "powerText": POWER_TEXT.get(d.power_state, d.power_state), "omeStatus": d.status, "connection": d.connection,
            "lastInventoryAt": _iso(d.last_inventory_at), "level": level, "levelText": LEVEL_TEXT[level],
            "reasons": list(dict.fromkeys(reasons)), "disksPredictive": predictive.get(k, 0),
            "firmware": {"behind": fw_behind.get(k, 0), "checked": fw_total.get(k, 0), "baselineConfigured": baseline.get(d.source_key)},
            "warranty": w, "ciId": t["ciId"], "hosts": t["hosts"], "systems": t["systems"],
            "vmCount": len(t["vms"]), "vmOn": sum(1 for v in t["vms"] if v["powerOn"])})
    total = len(items)
    if status:
        items = [i for i in items if i["level"] == status]
    if model:
        items = [i for i in items if (i["model"] or "—") == model]
    if warranty_days is not None:
        items = [i for i in items if i["warranty"].get("daysLeft") is not None and i["warranty"]["daysLeft"] <= warranty_days]
    items.sort(key=lambda i: (LEVEL_RANK[i["level"]], i["name"]))
    by_level = {lv: sum(1 for i in items if i["level"] == lv) for lv in ("crit", "bad", "warn", "ok")}
    summary = {"servers": len(items), "inScope": total, "byLevel": by_level,
               "disksPredictive": sum(i["disksPredictive"] for i in items),
               "firmwareBehind": sum(1 for i in items if i["firmware"]["behind"]),
               "warrantyExpired": sum(1 for i in items if (i["warranty"].get("daysLeft") is not None and i["warranty"]["daysLeft"] < 0)),
               "warrantyExpiring90": sum(1 for i in items if i["warranty"].get("daysLeft") is not None and 0 <= i["warranty"]["daysLeft"] <= 90),
               "noOpex": sum(1 for i in items if i["warranty"].get("case") == "no_opex"),
               "noWarrantyData": sum(1 for i in items if i["warranty"].get("dellEnd") is None),
               "vmsOn": sum(i["vmOn"] for i in items)}
    st = db.query(HwSourceState).filter(HwSourceState.source_key.in_(keys or [""])).all()
    sources = [{"key": s.source_key, "name": s.name, "devices": s.device_count, "capturedAt": _iso(s.captured_at),
                "inventoryAt": _iso(s.inventory_at), "warrantyAt": _iso(s.warranty_at), "baselineConfigured": s.baseline_configured,
                "lastError": s.last_error, "lastWarning": s.last_warning} for s in st]
    return {"configured": bool(keys), "sources": sources, "summary": summary, "models": sorted(models), "opexDetail": opex_detail,
            "filters": {"status": status, "model": model, "warrantyDays": warranty_days}, "items": items}


def detail(db: Session, source_key: str, device_id: int, today: dt.date, opex_detail: bool) -> Dict[str, Any]:
    if source_key not in _keys():
        raise HTTPException(404, "Không có máy chủ này.")
    d = db.query(HwDeviceState).filter_by(source_key=source_key, device_id=device_id).first()
    if d is None:
        raise HTTPException(404, "Không có máy chủ này.")
    base = next((i for i in listing(db, today, opex_detail=opex_detail)["items"]
                 if i["sourceKey"] == source_key and i["deviceId"] == device_id), None)
    k = {"source_key": source_key, "device_id": device_id}
    inv = db.query(HwDeviceInventory).filter_by(**k).first()
    t = topology(db, [d.service_tag] if d.service_tag else []).get(d.service_tag or "") or {"vms": []}
    subs = [{"subsystem": h.subsystem, "status": h.status, "levelText": LEVEL_TEXT[h.status], "detail": h.detail}
            for h in db.query(HwComponentHealth).filter_by(**k).order_by(HwComponentHealth.subsystem)]
    disks = [{"slot": x.slot, "model": x.model, "serial": x.serial, "media": x.media, "sizeGB": x.size_gb, "status": x.status,
              "levelText": LEVEL_TEXT[x.status], "predictiveFailure": x.predictive_failure}
             for x in db.query(HwDiskState).filter_by(**k).order_by(HwDiskState.slot)]
    parts = [{"kind": p.kind, "kindText": PART_KIND.get(p.kind, p.kind), "name": p.name, "model": p.model, "serial": p.serial,
              "detail": p.detail, "status": p.status, "levelText": LEVEL_TEXT[p.status]}
             for p in db.query(HwPartState).filter_by(**k).order_by(HwPartState.kind, HwPartState.name)]
    fw = [{"component": f.component, "version": f.version, "baselineVersion": f.baseline_version, "compliance": f.compliance,
           "complianceText": COMPLIANCE_TEXT.get(f.compliance) if f.compliance else "Không có baseline"}
          for f in db.query(HwFirmwareState).filter_by(**k).order_by(HwFirmwareState.component)]
    warr = [{"serviceLevel": w.service_level, "start": _iso(w.start_date), "end": _iso(w.end_date)}
            for w in db.query(HwWarranty).filter_by(source_key=source_key, service_tag=d.service_tag or "")
            .order_by(HwWarranty.end_date.desc())]
    alerts = [{"at": _iso(a.at), "severity": a.severity, "category": a.category, "messageId": a.message_id, "message": a.message,
               "status": a.status}
              for a in db.query(HwAlert).filter(HwAlert.source_key == source_key,
                                               (HwAlert.device_id == device_id) | (HwAlert.service_tag == d.service_tag))
              .order_by(HwAlert.at.desc()).limit(20)]
    st = db.query(HwSourceState).filter_by(source_key=source_key).first()
    return {**(base or {}), "os": inv.os_name if inv else None, "osHostname": inv.os_hostname if inv else None,
            "macs": json.loads(inv.macs) if inv and inv.macs else [], "subsystems": subs, "disks": disks, "parts": parts,
            "firmwareRows": fw, "warranties": warr, "alerts": alerts,
            "vms": [{k2: v[k2] for k2 in ("name", "vcenterKey", "powerOn", "system")} for v in t["vms"][:MAX_VMS]],
            "vmsTruncated": len(t["vms"]) > MAX_VMS,
            "source": {"key": source_key, "name": st.name if st else source_key, "capturedAt": _iso(st.captured_at) if st else None,
                       "inventoryAt": _iso(st.inventory_at) if st else None, "baselineConfigured": st.baseline_configured if st else None}}


def card_block(db: Session, service_tag: str, today: dt.date, opex_detail: bool) -> Optional[Dict[str, Any]]:
    """OME part of the physical server card (G1c): None when no configured OME source reports this tag."""
    d = (db.query(HwDeviceState).filter(HwDeviceState.source_key.in_(_keys() or [""]), HwDeviceState.service_tag == service_tag)
         .order_by(HwDeviceState.source_key).first())
    if d is None:
        return None
    full = detail(db, d.source_key, d.device_id, today, opex_detail)
    keep = ("sourceKey", "deviceId", "name", "model", "idracIp", "idracUrl", "powerText", "level", "levelText", "reasons",
            "disksPredictive", "firmware", "warranty", "lastInventoryAt", "os", "osHostname", "subsystems")
    return {k: full.get(k) for k in keep}


def firmware_rows(db: Session, keys: List[tuple]) -> List[Dict[str, Any]]:
    """Every firmware row of the given servers (Excel sheet "Firmware"), one query."""
    want = set(keys)
    if not want:
        return []
    names = {(d.source_key, d.device_id): (d.name, d.service_tag) for d in db.query(HwDeviceState)
             .filter(HwDeviceState.source_key.in_({k[0] for k in want}))}
    out = []
    for f in (db.query(HwFirmwareState).filter(HwFirmwareState.source_key.in_({k[0] for k in want}))
              .order_by(HwFirmwareState.source_key, HwFirmwareState.device_id, HwFirmwareState.component)):
        k = (f.source_key, f.device_id)
        if k not in want:
            continue
        name, tag = names.get(k, (str(f.device_id), None))
        out.append({"server": name, "serviceTag": tag, "component": f.component, "version": f.version,
                    "baselineVersion": f.baseline_version,
                    "complianceText": COMPLIANCE_TEXT.get(f.compliance) if f.compliance else "Không có baseline"})
    return out
