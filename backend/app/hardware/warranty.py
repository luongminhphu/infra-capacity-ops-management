"""Dell warranty (OME, hw_warranty) ↔ Opex hardware-warranty items (opex_assets.serial + opex_items dates).

Four cases per server still seen by OME (hw_device_state of a configured source):
  WNOP  Có bảo hành Dell, không có Opex          — Dell covers today, no Opex lists the service tag;
  WOPE  Opex hết hạn trước Dell                   — Dell covers today, the Opex ends before Dell does;
  WDLE  Dell hết hạn nhưng Opex còn               — Dell ended, an Opex still covers today (third-party / wrong list?);
  WCRT  Hết bảo hành, đang chạy VM quan trọng     — Dell ended, no Opex covers today, VMs of systems with criticality 1–2
                                                    run on it (CI registry: physical_server ← host ← VM → system, G2).
Each finding has a stable reconciliation code (prefix + 5 hex of FNV-1a(case|service tag)), like the capacity codes.
Reads only; the inbox (source "asset") and Kế hoạch năm sau (source "warranty") use it.
"""
import datetime as dt
import json
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from .models import HwDeviceState, HwWarranty

CASES = {
    "no_opex": ("WNOP", "Có bảo hành Dell, không có Opex", "warn"),
    "opex_before_dell": ("WOPE", "Opex hết hạn trước Dell", "warn"),
    "dell_expired_opex_active": ("WDLE", "Dell hết hạn nhưng Opex còn", "warn"),
    "expired_critical": ("WCRT", "Hết bảo hành, đang chạy VM quan trọng (mức 1–2)", "crit"),
}
CRITICAL_MAX = 2                      # systems with criticality 1–2


def code(case: str, tag: str) -> str:
    h = 0x811C9DC5
    for b in f"{case}|{tag}".encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{CASES[case][0]}-{h:08X}"[:len(CASES[case][0]) + 6]


def _sources() -> List[str]:
    from ..config import get_settings
    return [c.key for c in get_settings().hardware()]


def servers(db: Session) -> Dict[str, HwDeviceState]:
    """Service tag → OME device (configured sources only; the first source wins when two report it)."""
    out: Dict[str, HwDeviceState] = {}
    keys = _sources()
    if not keys:
        return out
    for d in db.query(HwDeviceState).filter(HwDeviceState.source_key.in_(keys), HwDeviceState.service_tag.isnot(None)) \
            .order_by(HwDeviceState.source_key, HwDeviceState.device_id):
        out.setdefault(d.service_tag, d)
    return out


def dell_coverage(db: Session, tags) -> Dict[str, Dict[str, Any]]:
    """Service tag → the entitlement ending last {end, start, level}."""
    out: Dict[str, Dict[str, Any]] = {}
    tags = list(tags)
    if not tags:
        return out
    for w in db.query(HwWarranty).filter(HwWarranty.service_tag.in_(tags), HwWarranty.end_date.isnot(None)):
        cur = out.get(w.service_tag)
        if cur is None or w.end_date > cur["end"]:
            out[w.service_tag] = {"end": w.end_date, "start": w.start_date, "level": w.service_level}
    return out


def critical_systems(db: Session, tags) -> Dict[str, Dict[str, Any]]:
    """Service tag → {"systems": [{code, name, criticality}], "vms": powered-on VMs of those systems} through the
    CI registry. Three set-based queries, no per-server loop."""
    from ..cmdb.models import Ci, CiLink
    from ..owners.models import System, SystemMember
    tags = list(tags)
    phys = {cid: tag for cid, tag in db.query(Ci.id, Ci.source_key).filter(
        Ci.type == "physical_server", Ci.status == "active", Ci.source_key.in_(tags or [""]))}
    if not phys:
        return {}
    host_of = {h: phys[p] for h, p in db.query(CiLink.from_ci, CiLink.to_ci).filter(
        CiLink.relation == "runs_on", CiLink.to_ci.in_(list(phys)))}
    vm_host = {v: h for v, h in db.query(CiLink.from_ci, CiLink.to_ci).filter(
        CiLink.relation == "hosted_on", CiLink.to_ci.in_(list(host_of) or [-1]))}
    if not vm_host:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    rows = (db.query(SystemMember.ci_id, System.code, System.name, System.criticality, Ci.attrs)
            .join(System, System.id == SystemMember.system_id).join(Ci, Ci.id == SystemMember.ci_id)
            .filter(SystemMember.ci_id.in_(list(vm_host)), System.criticality <= CRITICAL_MAX, Ci.status == "active"))
    for vm, scode, sname, crit, attrs in rows:
        try:
            on = bool(json.loads(attrs or "{}").get("powerOn"))
        except ValueError:
            on = False
        if not on:
            continue
        tag = host_of[vm_host[vm]]
        d = out.setdefault(tag, {"systems": {}, "vms": 0})
        d["systems"][scode] = {"code": scode, "name": sname, "criticality": crit}
        d["vms"] += 1
    return {t: {"systems": sorted(d["systems"].values(), key=lambda x: (x["criticality"], x["code"])), "vms": d["vms"]}
            for t, d in out.items()}


def _opex(db: Session, tags) -> Dict[str, List[Dict[str, Any]]]:
    from .. import dc_assets
    links = dc_assets.opex_links(db, tags)
    return {k: [o for o in v if o["grp"] == "hw_warranty"] for k, v in links.items()}


def classify(dell: Optional[Dict[str, Any]], cov: Dict[str, Any], critical: Optional[Dict[str, Any]],
             today: dt.date) -> Optional[str]:
    """Case of one server (pure): dell = latest Dell entitlement or None (no warranty data → no finding);
    cov = dc_assets.coverage of its warranty Opex; critical = critical_systems entry or None."""
    if dell is None:
        return None
    dell_ok = dell["end"] >= today
    if dell_ok:
        if cov["state"] == "none":
            return "no_opex"
        if cov["end"] is not None and cov["end"] < dell["end"]:
            return "opex_before_dell"
        return None
    if cov["state"] == "active":
        return "dell_expired_opex_active"
    if critical and critical.get("vms"):
        return "expired_critical"
    return None


def reconcile(db: Session, today: dt.date) -> List[Dict[str, Any]]:
    from ..cmdb.models import Ci
    from .. import dc_assets
    devs = servers(db)
    if not devs:
        return []
    dell = dell_coverage(db, devs)
    opex = _opex(db, devs)
    crit = critical_systems(db, devs)
    ci_ids = {tag: cid for cid, tag in db.query(Ci.id, Ci.source_key).filter(
        Ci.type == "physical_server", Ci.source_key.in_(list(devs)))}
    out = []
    for tag, d in devs.items():
        cov = dc_assets.coverage(opex.get(tag) or [], today)
        case = classify(dell.get(tag), cov, crit.get(tag), today)
        if case is None:
            continue
        prefix, label, level = CASES[case]
        o = cov.get("item")
        w = dell[tag]
        out.append({"code": code(case, tag), "case": case, "caseLabel": label, "level": level, "serviceTag": tag,
                    "name": d.name, "model": d.model, "sourceKey": d.source_key, "ciId": ci_ids.get(tag),
                    "dellEnd": w["end"].isoformat(), "dellService": w.get("level"),
                    "opex": {"code": o["code"], "title": o["title"], "end": o["end"].isoformat(), "state": cov["state"]} if o else None,
                    "systems": (crit.get(tag) or {}).get("systems") or [], "vms": (crit.get(tag) or {}).get("vms") or 0})
    rank = {"crit": 0, "bad": 1, "warn": 2}
    out.sort(key=lambda x: (rank[x["level"]], x["case"], x["dellEnd"], x["serviceTag"]))
    return out


# ---------------------------------------------------------------- Kế hoạch năm sau (spend/budget_plan.py)
def renewal_candidates(db: Session, year_start: dt.date, year_end: dt.date) -> List[Tuple[HwDeviceState, dt.date, bool]]:
    """Servers still running whose Dell warranty ends before the end of the fiscal year and that no warranty Opex
    covers to the end of it: (device, Dell end, runs critical VMs)."""
    from .. import dc_assets
    devs = servers(db)
    dell = dell_coverage(db, devs)
    opex = _opex(db, devs)
    crit = critical_systems(db, devs)
    out = []
    for tag, d in devs.items():
        w = dell.get(tag)
        if w is None or w["end"] >= year_end:
            continue
        covered = any(o["status"] != "stopped" and o["end"] >= year_end and o["start"] <= max(w["end"], year_start)
                      for o in opex.get(tag) or [])
        if covered:
            continue
        out.append((d, w["end"], bool((crit.get(tag) or {}).get("vms"))))
    out.sort(key=lambda x: (x[1], x[0].service_tag))
    return out


def plan_suggestions(db: Session, year_start: dt.date, year_end: dt.date) -> List[Dict[str, Any]]:
    """One Opex line "Gia hạn bảo hành Dell" for the fiscal year (sourceId = the year it starts in, stable across
    refreshes). No price is known: the amount is typed in the plan; once applied it becomes a planned Opex listing
    the service tags (plan_serials)."""
    from ..spend.models import SpendCategory, SpendVendor
    rows = renewal_candidates(db, year_start, year_end)
    if not rows:
        return []
    cats = db.query(SpendCategory).filter(SpendCategory.active.is_(True)).order_by(SpendCategory.sort_order, SpendCategory.id).all()
    if not cats:
        return []
    cat = next((c for c in cats if c.grp == "hw_warranty"), None) or next((c for c in cats if c.code == "HW_SERVER"), cats[0])
    vendor = db.query(SpendVendor).filter(SpendVendor.name.ilike("%dell%")).order_by(SpendVendor.id).first()
    by_model: Dict[str, int] = defaultdict(int)
    for d, _end, _c in rows:
        by_model[d.model or "—"] += 1
    must = any(c or end < year_start for _d, end, c in rows)
    parts = [f"{d.service_tag} ({d.name}, hết {end.strftime('%d/%m/%Y')}{', chạy VM mức 1–2' if c else ''})" for d, end, c in rows]
    detail = ", ".join(f"{n} × {m}" for m, n in sorted(by_model.items(), key=lambda x: (-x[1], x[0]))) + \
        ". Bảo hành Dell hết trước cuối năm, chưa có Opex gia hạn: " + "; ".join(parts)
    start = min(max(min(end for _d, end, _c in rows) + dt.timedelta(days=1), year_start), year_end)
    return [{"kind": "opex", "source": "warranty", "sourceId": year_start.year, "sourceCode": f"{len(rows)} máy chủ",
             "categoryId": cat.id, "vendorId": vendor.id if vendor else None, "costCenterId": None,
             "title": f"Gia hạn bảo hành Dell {len(rows)} máy chủ", "startDate": start, "terms": 1, "baseNet": 0,
             "vatPct": Decimal(10), "detail": detail if len(detail) <= 1000 else detail[:999] + "…",
             "priority": "must" if must else "should"}]


def plan_serials(db: Session, year_start: dt.date, year_end: dt.date) -> List[str]:
    """Service tags of the "warranty" plan line (the Opex created when the plan is applied lists them)."""
    return [d.service_tag for d, _end, _c in renewal_candidates(db, year_start, year_end)][:200]
