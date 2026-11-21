"""Lifecycle & security — storage, admin tables (EoGS, VMSA) and the one report every consumer reads (page, Excel, PDF, Hộp việc,
budget plan suggestion)."""
import datetime as dt
import json
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from . import rules as R
from .models import PRODUCTS, LifecycleEogs, TlsCertState, VcProductState, VmsaAdvisory

PRODUCT_LABELS = {"esxi": "ESXi", "vcenter": "vCenter"}
KIND_LABELS = {"vcenter": "vCenter", "esxi": "ESXi", "netbackup": "NetBackup", "ome": "Dell OME", "storage": "Storage",
               "ipam": "GestióIP", "itsm": "ServiceDesk Plus", "app": "ICO"}
EOGS_HEAD = ("Sản phẩm", "Phiên bản", "Hết hỗ trợ chung (EoGS)", "Hết hướng dẫn kỹ thuật (EoTG)", "Ghi chú")
VMSA_HEAD = ("Mã VMSA", "Tiêu đề", "CVSS", "Sản phẩm", "Phiên bản", "Build đã vá", "Ngày công bố")


def store_product(db: Session, vcenter_key: str, extras: Dict[str, Any], now: Optional[dt.datetime] = None) -> None:
    """Version / build of one vCenter (extras["product"]); a source without it (Proxmox / Nutanix, read error) is left as is."""
    p = (extras or {}).get("product")
    if not p:
        return
    row = db.query(VcProductState).filter(VcProductState.vcenter_key == vcenter_key).first() or VcProductState(vcenter_key=vcenter_key)
    row.version, row.build = (p.get("version") or None) and str(p["version"])[:32], (p.get("build") or None) and str(p["build"])[:32]
    row.full_name, row.captured_at = (p.get("fullName") or None) and str(p["fullName"])[:255], now or dt.datetime.utcnow()
    db.add(row)


def products(db: Session) -> List[Dict[str, Any]]:
    """ESXi hosts of VMware vCenters (latest sync) + every vCenter's own version."""
    from .. import data_access
    from ..config import get_settings
    vmware = {c.key for c in get_settings().vcenters() if getattr(c, "platform", "vmware") == "vmware"}
    hosts, _vms, _ = data_access.get_latest_combined(db, sorted(vmware))
    out = [{"product": "esxi", "name": h["host"], "vcenterKey": h.get("vcenterKey") or "", "cluster": h.get("cluster"),
            "version": h.get("esxiVersion"), "build": h.get("esxiBuild")} for h in hosts if h.get("esxiVersion")]
    out += [{"product": "vcenter", "name": v.vcenter_key, "vcenterKey": v.vcenter_key, "cluster": None, "version": v.version,
             "build": v.build} for v in db.query(VcProductState).filter(VcProductState.vcenter_key.in_(sorted(vmware) or [""]))]
    return out


def eogs_rows(db: Session) -> List[Dict[str, Any]]:
    return [{"id": e.id, "product": e.product, "version": e.version, "eogs": e.eogs, "eotg": e.eotg, "note": e.note}
            for e in db.query(LifecycleEogs).order_by(LifecycleEogs.product, LifecycleEogs.version)]


def vmsa_rows(db: Session) -> List[Dict[str, Any]]:
    return [{"id": a.id, "vmsa": a.vmsa, "title": a.title, "cvss": a.cvss, "product": a.product, "version": a.version,
             "fixedBuild": a.fixed_build, "published": a.published} for a in db.query(VmsaAdvisory).order_by(VmsaAdvisory.vmsa.desc())]


def cert_rows(db: Session) -> List[Dict[str, Any]]:
    return [{"target": c.target, "kind": c.kind, "kindLabel": KIND_LABELS.get(c.kind, c.kind), "name": c.name, "host": c.host, "port": c.port,
             "vcenterKey": c.vcenter_key, "notAfter": c.not_after, "subject": c.subject, "issuer": c.issuer, "san": json.loads(c.san or "[]"),
             "selfSigned": c.self_signed, "error": c.error, "checkedAt": c.checked_at.isoformat() + "Z"} for c in db.query(TlsCertState)]


def _iso(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in r.items()} for r in rows]


def report(db: Session, today: Optional[dt.date] = None, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    from ..spend.fiscal import today_vn
    today, now = today or today_vn(), now or dt.datetime.utcnow()
    eogs, vmsa = eogs_rows(db), vmsa_rows(db)
    res = R.evaluate(products(db), eogs, vmsa, cert_rows(db), today, now)
    count = lambda rows: {lv: sum(1 for r in rows if r["level"] == lv) for lv in ("crit", "bad", "warn", "ok", "na")}  # noqa: E731
    for r in res["lifecycle"] + res["vmsa"]:
        r["productLabel"] = PRODUCT_LABELS[r["product"]]
    return {**res, "eogsTable": _iso(eogs), "vmsaTable": _iso(vmsa),
            "summary": {"lifecycle": count(res["lifecycle"]), "vmsa": count(res["vmsa"]), "certs": count(res["certs"]),
                        "vmsaHosts": len({(r["vcenterKey"], r["name"]) for r in res["vmsa"]}),
                        "noLine": sum(1 for r in res["lifecycle"] if r["line"] is None),
                        "certsCheckedAt": max((c["checkedAt"] for c in res["certs"]), default=None)}}


# ---------------------------------------------------------------- admin tables
def _date(v: Any, label: str, required: bool = True) -> Optional[dt.date]:
    if v in (None, ""):
        if required:
            raise ValueError(f"Thiếu {label}.")
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"{label} không hợp lệ: {s[:20]} (yyyy-mm-dd hoặc dd/mm/yyyy).")


def _product(v: Any) -> str:
    s = str(v or "").strip().lower().replace(" ", "")
    s = {"vcsa": "vcenter", "vcenterserver": "vcenter", "esx": "esxi"}.get(s, s)
    if s not in PRODUCTS:
        raise ValueError(f"Sản phẩm phải là ESXi hoặc vCenter (nhận được “{str(v)[:20]}”).")
    return s


def _version(v: Any) -> str:
    s = str(v or "").strip()
    if not s or len(s) > 16 or not all(p.isdigit() for p in s.split(".")):
        raise ValueError(f"Phiên bản phải dạng 8.0 hoặc 8.0.2 (nhận được “{s[:20]}”).")
    return s


def clean_eogs(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"product": _product(r.get("product")), "version": _version(r.get("version")), "eogs": _date(r.get("eogs"), "ngày EoGS"),
            "eotg": _date(r.get("eotg"), "ngày EoTG", False), "note": (str(r.get("note") or "").strip()[:255] or None)}


def clean_vmsa(r: Dict[str, Any]) -> Dict[str, Any]:
    from ..shared.text import parse_number
    vmsa = str(r.get("vmsa") or "").strip().upper()
    if not vmsa or len(vmsa) > 32:
        raise ValueError("Thiếu mã VMSA.")
    cvss = parse_number(r.get("cvss"))
    if cvss is None or not 0 <= cvss <= 10:
        raise ValueError(f"{vmsa}: CVSS phải từ 0 đến 10.")
    build = R.build_no(r.get("fixedBuild"))
    if not build or build <= 0:
        raise ValueError(f"{vmsa}: build đã vá phải là số.")
    return {"vmsa": vmsa, "title": (str(r.get("title") or "").strip()[:255] or None), "cvss": float(cvss), "product": _product(r.get("product")),
            "version": _version(r.get("version")), "fixedBuild": build, "published": _date(r.get("published"), "ngày công bố", False)}


def parse_sheet(data: bytes, kind: str) -> List[Dict[str, Any]]:
    """Rows of an imported .xlsx (first sheet, header on row 1 in the order of the template). ValueError with the row number."""
    from ..shared.xlsx_io import read_rows
    keys = ("product", "version", "eogs", "eotg", "note") if kind == "eogs" else ("vmsa", "title", "cvss", "product", "version", "fixedBuild", "published")
    clean = clean_eogs if kind == "eogs" else clean_vmsa
    rows = read_rows(data, max_sheets=1, max_rows=2000, max_cols=len(keys))[0][1]
    out, seen = [], set()
    for i, r in enumerate(rows[1:], 2):
        if not any(c not in (None, "") for c in r):
            continue
        try:
            row = clean(dict(zip(keys, list(r) + [None] * len(keys))))
        except ValueError as exc:
            raise ValueError(f"Dòng {i}: {exc}") from None
        k = (row["product"], row["version"]) if kind == "eogs" else (row["vmsa"], row["product"], row["version"])
        if k in seen:
            raise ValueError(f"Dòng {i}: trùng {' · '.join(k)}.")
        seen.add(k)
        out.append(row)
    if not out:
        raise ValueError("File không có dòng dữ liệu nào (dòng 1 là tiêu đề).")
    return out


def replace_table(db: Session, kind: str, rows: List[Dict[str, Any]], user: str) -> int:
    now = dt.datetime.utcnow()
    if kind == "eogs":
        db.query(LifecycleEogs).delete()
        db.add_all([LifecycleEogs(**r, updated_by=user, updated_at=now) for r in rows])
    else:
        db.query(VmsaAdvisory).delete()
        db.add_all([VmsaAdvisory(vmsa=r["vmsa"], title=r["title"], cvss=r["cvss"], product=r["product"], version=r["version"],
                                 fixed_build=r["fixedBuild"], published=r["published"], updated_by=user, updated_at=now) for r in rows])
    return len(rows)


def template(kind: str) -> bytes:
    from openpyxl import Workbook
    from .. import excel_style as xs
    wb = Workbook()
    ws = wb.active
    ws.title = "EoGS" if kind == "eogs" else "VMSA"
    head = EOGS_HEAD if kind == "eogs" else VMSA_HEAD
    sample = ([["ESXi", "7.0", "2025-10-02", "2027-04-02", "Theo VMware Lifecycle Matrix"], ["vCenter", "7.0", "2025-10-02", "2027-04-02", ""],
               ["ESXi", "8.0", "2027-10-11", "2029-10-11", ""]] if kind == "eogs"
              else [["VMSA-2025-0004", "VMCI heap-overflow (ví dụ)", 9.3, "ESXi", "8.0", 24585383, "2025-03-04"],
                    ["VMSA-2025-0004", "VMCI heap-overflow (ví dụ)", 9.3, "ESXi", "7.0", 24585291, "2025-03-04"]])
    xs._write_rows(ws, 1, list(head), sample)
    xs._widths(ws, {c: 22 for c in "ABCDEFG"})
    return xs.to_bytes(wb)


# ---------------------------------------------------------------- budget plan (spend/budget_plan.py source "lifecycle")
def plan_suggestions(db: Session, year: int, year_start: dt.date, year_end: dt.date) -> List[Dict[str, Any]]:
    """One Capex line "Nâng cấp ESXi / vCenter hết hỗ trợ <năm>" when hosts / vCenters reach End of General Support before the end of
    the fiscal year (sourceId = year). No price is known: the amount is typed in the plan."""
    from decimal import Decimal
    from ..spend.models import SpendCategory
    eogs = eogs_rows(db)
    hits: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for p in products(db):
        e = R.match_eogs(p, eogs)
        if e and e["eogs"] <= year_end:
            hits.append((p, e))
    if not hits:
        return []
    cats = db.query(SpendCategory).filter(SpendCategory.active.is_(True)).order_by(SpendCategory.sort_order, SpendCategory.id).all()
    if not cats:
        return []
    cat = next((c for c in cats if c.code == "LIC_VMWARE"), None) or next((c for c in cats if c.code == "OTHER"), cats[0])
    n_host = sum(1 for p, _ in hits if p["product"] == "esxi")
    n_vc = len(hits) - n_host
    lines = sorted({f"{PRODUCT_LABELS[p['product']]} {e['version']} hết hỗ trợ {e['eogs'].strftime('%d/%m/%Y')}" for p, e in hits})
    names = ", ".join(sorted(p["name"] for p, _ in hits)[:40])
    detail = f"{'; '.join(lines)}. {n_host} host ESXi, {n_vc} vCenter: {names}"
    return [{"kind": "capex", "source": "lifecycle", "sourceId": year, "sourceCode": f"{len(hits)} đối tượng", "categoryId": cat.id,
             "vendorId": None, "costCenterId": None, "title": f"Nâng cấp ESXi / vCenter hết hỗ trợ {year}",
             "startDate": min(max(min(e["eogs"] for _, e in hits), year_start), year_end), "terms": 1, "baseNet": 0, "vatPct": Decimal(10),
             "detail": detail if len(detail) <= 1000 else detail[:999] + "…", "priority": "must"}]


def seed_demo(db: Session) -> None:
    """DEMO only: sample EoGS / VMSA rows (marked "mẫu demo") when both tables are empty, so the page has something to match."""
    if db.query(LifecycleEogs.id).first() or db.query(VmsaAdvisory.id).first():
        return
    d = dt.date
    replace_table(db, "eogs", [{"product": p, "version": v, "eogs": e, "eotg": t, "note": "Mẫu demo — thay bằng bảng thật"}
                               for p, v, e, t in (("esxi", "7.0", d(2025, 10, 2), d(2027, 4, 2)), ("vcenter", "7.0", d(2025, 10, 2), d(2027, 4, 2)),
                                                  ("esxi", "8.0", d(2027, 10, 11), d(2029, 10, 11)), ("vcenter", "8.0", d(2027, 10, 11), d(2029, 10, 11)))], "demo")
    replace_table(db, "vmsa", [{"vmsa": a, "title": f"{t} (mẫu demo)", "cvss": c, "product": p, "version": v, "fixedBuild": b, "published": pub}
                               for a, t, c, p, v, b, pub in (
                                   ("VMSA-2025-0004", "VMCI heap-overflow", 9.3, "esxi", "8.0", 24585383, d(2025, 3, 4)),
                                   ("VMSA-2025-0004", "VMCI heap-overflow", 9.3, "esxi", "7.0", 24585291, d(2025, 3, 4)),
                                   ("VMSA-2024-0019", "DCERPC heap-overflow", 9.8, "vcenter", "7.0", 24322018, d(2024, 9, 17)),
                                   ("VMSA-2024-0011", "Out-of-bounds write", 7.1, "esxi", "8.0", 23825572, d(2024, 5, 21)))], "demo")
